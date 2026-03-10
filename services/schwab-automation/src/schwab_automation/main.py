from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, replace
from datetime import UTC, datetime
import json
import logging
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from swing_agent_database import AutomationStore, SymbolAvailabilityStore

from .analysis import analyze_portfolio, format_shadow_report
from .candidate_discovery import parse_entry_candidate_ideas
from .config import Settings, load_settings
from .copilot_runner import run_structured_analysis
from .execution import execute_action
from .llm_actions import ValidatedAction, parse_actions, validate_actions
from .notifier import DiscordNotifier
from .option_candidates import fetch_broker_option_candidates
from .schwab_client import create_async_client
from .sync import fetch_account_snapshot


@dataclass(frozen=True)
class TradingWindowStatus:
    is_open: bool
    local_timestamp: datetime
    reason: str


def _format_trade_analysis_report(
    *,
    analysis_enabled: bool,
    llm_actions: list[ValidatedAction],
    llm_error: str | None,
) -> str:
    lines = ["Trade analysis:"]
    if not analysis_enabled:
        lines.append("- not run")
        return "\n".join(lines)
    if llm_error:
        lines.append(f"- failed: {llm_error}")
        return "\n".join(lines)
    if not llm_actions:
        lines.append("- completed: no actions returned")
        return "\n".join(lines)

    for validated in llm_actions:
        proposed = validated.proposed
        symbol = proposed.symbol or "portfolio"
        rationale = "; ".join(proposed.rationale) if proposed.rationale else "no rationale"
        lines.append(
            (
                f"- {symbol}: {proposed.action_type} [{validated.validation_status}] "
                f"confidence={proposed.confidence} execution_supported={'yes' if validated.execution_supported else 'no'}"
            )
        )
        if proposed.quantity is not None:
            lines.append(f"  quantity: {proposed.quantity}")
        if proposed.option_type is not None:
            lines.append(f"  option: {proposed.option_type}")
        if proposed.expiration is not None or proposed.strike is not None:
            lines.append(f"  contract: exp={proposed.expiration} strike={proposed.strike}")
        if proposed.current_expiration is not None or proposed.current_strike is not None:
            lines.append(f"  current: exp={proposed.current_expiration} strike={proposed.current_strike}")
        if proposed.target_expiration is not None or proposed.target_strike is not None:
            lines.append(f"  target: exp={proposed.target_expiration} strike={proposed.target_strike}")
        if proposed.limit_price is not None:
            lines.append(f"  limit_price: {proposed.limit_price}")
        if validated.validation_errors:
            lines.append(f"  validation_errors: {'; '.join(validated.validation_errors)}")
        lines.append(f"  rationale: {rationale}")
    return "\n".join(lines)


def _format_candidate_discovery_report(*, candidate_ideas: list[dict[str, object]]) -> str:
    lines = ["Candidate discovery:"]
    if not candidate_ideas:
        lines.append("- no entry candidates found")
        return "\n".join(lines)
    for candidate in candidate_ideas:
        lines.append(
            f"- {candidate['symbol']}: {candidate['strategy_type']} option_type={candidate['option_type']} confidence={candidate['confidence']}"
        )
        rationale = candidate.get("rationale") or []
        if rationale:
            lines.append(f"  rationale: {'; '.join(str(item) for item in rationale)}")
    return "\n".join(lines)


def _build_fallback_entry_candidates(analysis) -> list[dict[str, object]]:
    fallback: list[dict[str, object]] = []
    seen: set[str] = set()
    for decision in analysis.decisions:
        if decision.action_type != "csp_entry_candidate":
            continue
        if decision.symbol is None or decision.symbol in seen:
            continue
        seen.add(decision.symbol)
        fallback.append(
            {
                "symbol": decision.symbol,
                "strategy_type": "cash_secured_put",
                "option_type": "PUT",
                "confidence": "medium",
                "rationale": [decision.rationale, "Fallback candidate derived from deterministic portfolio gate."],
            }
        )
    return fallback


def _find_project_root() -> Path:
    candidates = [Path.cwd(), Path(__file__).resolve().parent]
    for candidate in candidates:
        for current in (candidate, *candidate.parents):
            if (current / ".env").exists() or (current / ".git").exists():
                return current
    return Path.cwd()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the hourly Schwab automation worker")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Force dry-run mode for this invocation",
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Keep running in-process and execute on the configured interval",
    )
    parser.add_argument(
        "--interval-seconds",
        type=int,
        default=3600,
        help="Sleep interval when --loop is enabled",
    )
    return parser.parse_args()


def _filter_supported_symbols(symbols: list[str]) -> list[str]:
    filtered: list[str] = []
    seen: set[str] = set()
    for raw_symbol in symbols:
        symbol = raw_symbol.strip().upper()
        if not symbol or symbol.startswith("/"):
            continue
        if symbol in seen:
            continue
        seen.add(symbol)
        filtered.append(symbol)
    return filtered


def _get_trading_window_status(now_utc: datetime, settings: Settings) -> TradingWindowStatus:
    local_timestamp = now_utc.astimezone(ZoneInfo(settings.trading_timezone))
    if not settings.enforce_trading_window:
        return TradingWindowStatus(
            is_open=True,
            local_timestamp=local_timestamp,
            reason="Trading window enforcement disabled",
        )

    if local_timestamp.weekday() >= 5:
        return TradingWindowStatus(
            is_open=False,
            local_timestamp=local_timestamp,
            reason="Weekend trading is not allowed",
        )

    start = local_timestamp.replace(
        hour=settings.trading_start_hour,
        minute=settings.trading_start_minute,
        second=0,
        microsecond=0,
    )
    end = local_timestamp.replace(
        hour=settings.trading_end_hour,
        minute=settings.trading_end_minute,
        second=0,
        microsecond=0,
    )
    if end <= start:
        raise RuntimeError("Trading window end must be after the start time")
    if start <= local_timestamp < end:
        return TradingWindowStatus(
            is_open=True,
            local_timestamp=local_timestamp,
            reason=(
                f"Within trading window {start.strftime('%H:%M')} to {end.strftime('%H:%M')} {settings.trading_timezone}"
            ),
        )
    return TradingWindowStatus(
        is_open=False,
        local_timestamp=local_timestamp,
        reason=(
            f"Outside trading window {start.strftime('%H:%M')} to {end.strftime('%H:%M')} {settings.trading_timezone}"
        ),
    )


async def _run_once(settings: Settings) -> int:
    store = AutomationStore(settings.automation_store)
    symbol_store = SymbolAvailabilityStore(settings.symbol_availability_store)
    notifier = DiscordNotifier(
        settings.discord_webhook_url,
        timeout_seconds=settings.webhook_timeout_seconds,
        bot_token=settings.discord_bot_token,
        channel_id=settings.discord_channel_id,
    )
    run = store.start_run(
        service_name=settings.service_name,
        run_type=settings.run_type,
        dry_run=settings.dry_run,
        prompt_version=settings.prompt_version,
        details={"phase": "reconcile", "mode": "bootstrap"},
    )

    try:
        client = create_async_client(settings)
        snapshot = await fetch_account_snapshot(client, settings)
        trading_window = _get_trading_window_status(datetime.now(tz=UTC), settings)
        supported_symbols = _filter_supported_symbols([record.symbol for record in symbol_store.list_symbol_availability()])
        analysis_supported_symbols = supported_symbols if settings.dry_run or trading_window.is_open else []
        analysis_enabled = settings.analysis_enabled and (settings.dry_run or trading_window.is_open)
        enable_new_entries = settings.enable_new_entries and (settings.dry_run or trading_window.is_open)
        enable_management = settings.enable_management and (settings.dry_run or trading_window.is_open)
        execution_enabled = settings.execution_enabled and not settings.dry_run and trading_window.is_open
        recommendation_mode = settings.dry_run
        execution_block_reason = None
        if settings.dry_run:
            execution_block_reason = "Dry run mode disables order execution"
        elif not trading_window.is_open:
            execution_block_reason = trading_window.reason
        analysis = analyze_portfolio(
            cash_balance=snapshot.cash_available,
            positions=snapshot.positions,
            as_of=snapshot.synced_at,
            supported_symbols=analysis_supported_symbols,
            min_csp_reserve=settings.min_csp_reserve,
            min_entry_cash=settings.min_entry_cash,
            roll_dte_threshold_days=settings.roll_dte_threshold_days,
            close_dte_threshold_days=settings.close_dte_threshold_days,
        )
        store.upsert_account(
            account_hash=snapshot.account_hash,
            account_number=snapshot.account_number,
            account_type=snapshot.account_type,
            display_name=snapshot.display_name,
            cash_available=snapshot.cash_available,
            cash_reserved=analysis.reserved_cash,
            liquidation_value=snapshot.liquidation_value,
            balances=snapshot.balances,
            raw_payload=snapshot.raw_payload,
            synced_at=snapshot.synced_at,
        )
        positions = store.replace_positions(
            account_hash=snapshot.account_hash,
            positions=snapshot.positions,
            synced_at=snapshot.synced_at,
        )
        if settings.dry_run:
            store.record_decision(
                run_id=run.id,
                action_type="schedule_gate",
                status="executed",
                rationale=(
                    f"Dry run bypassed trading window at {trading_window.local_timestamp.isoformat()} ({trading_window.reason})."
                ),
                details={
                    "trading_window_open": trading_window.is_open,
                    "timezone": settings.trading_timezone,
                },
            )
        elif not trading_window.is_open:
            store.record_decision(
                run_id=run.id,
                action_type="schedule_gate",
                status="skipped",
                rationale=(
                    f"Live automation blocked at {trading_window.local_timestamp.isoformat()} because {trading_window.reason.lower()}."
                ),
                details={
                    "trading_window_open": False,
                    "timezone": settings.trading_timezone,
                },
            )
        llm_actions = []
        raw_llm_response = None
        llm_error = None
        candidate_ideas: list[dict[str, object]] = []
        broker_option_candidates: dict[str, list[dict[str, object]]] = {}
        candidate_discovery_error = None
        candidate_discovery_source = "none"
        if analysis_enabled:
            if enable_new_entries and analysis_supported_symbols:
                candidate_prompt = json.dumps(
                    {
                        "portfolio": {
                            "account_number": snapshot.account_number,
                            "cash_balance": str(analysis.cash_balance),
                            "reserved_cash": str(analysis.reserved_cash),
                            "deployable_cash": str(analysis.deployable_cash),
                        },
                        "positions": snapshot.positions,
                        "supported_symbols": analysis_supported_symbols,
                        "policy": {
                            "enable_new_entries": enable_new_entries,
                            "recommendation_mode": recommendation_mode,
                            "execution_enabled": execution_enabled,
                            "execution_block_reason": execution_block_reason,
                            "min_entry_cash": settings.min_entry_cash,
                            "min_csp_reserve": settings.min_csp_reserve,
                            "trading_window_open": trading_window.is_open,
                            "trading_window_reason": trading_window.reason,
                        },
                    },
                    default=str,
                )
                try:
                    candidate_payload, _ = await run_structured_analysis(
                        settings,
                        user_prompt=candidate_prompt,
                        prompt_path=settings.entry_candidate_prompt_path,
                    )
                    candidate_ideas = [
                        {
                            "symbol": idea.symbol,
                            "strategy_type": idea.strategy_type,
                            "option_type": idea.option_type,
                            "confidence": idea.confidence,
                            "rationale": idea.rationale,
                        }
                        for idea in parse_entry_candidate_ideas(candidate_payload)
                    ]
                    if candidate_ideas:
                        candidate_discovery_source = "yahoo_mcp"
                except Exception as exc:
                    candidate_discovery_error = str(exc)
                    store.record_decision(
                        run_id=run.id,
                        action_type="candidate_discovery",
                        status="failed",
                        rationale=f"Candidate discovery failed: {exc}",
                        details={"error": str(exc)},
                    )

                if not candidate_ideas:
                    candidate_ideas = _build_fallback_entry_candidates(analysis)
                    if candidate_ideas:
                        candidate_discovery_source = "deterministic_fallback"
                        store.record_decision(
                            run_id=run.id,
                            action_type="candidate_discovery",
                            status="executed",
                            rationale="Used deterministic fallback candidates because Yahoo discovery returned no structured entry ideas.",
                            details={"candidate_count": len(candidate_ideas)},
                        )

                if candidate_ideas:
                    broker_option_candidates = await fetch_broker_option_candidates(
                        client,
                        settings=settings,
                        ideas=parse_entry_candidate_ideas({"candidates": candidate_ideas}),
                        as_of=snapshot.synced_at,
                    )

            analysis_prompt = json.dumps(
                {
                    "portfolio": {
                        "account_number": snapshot.account_number,
                        "cash_balance": str(analysis.cash_balance),
                        "reserved_cash": str(analysis.reserved_cash),
                        "deployable_cash": str(analysis.deployable_cash),
                        "liquidation_value": None if snapshot.liquidation_value is None else str(snapshot.liquidation_value),
                    },
                    "positions": snapshot.positions,
                    "symbol_analysis": [item.__dict__ for item in analysis.symbols],
                    "supported_symbols": analysis_supported_symbols,
                    "entry_candidate_ideas": candidate_ideas,
                    "broker_option_candidates": broker_option_candidates,
                    "policy": {
                        "enable_new_entries": enable_new_entries,
                        "enable_management": enable_management,
                        "execution_enabled": execution_enabled,
                        "recommendation_mode": recommendation_mode,
                        "execution_block_reason": execution_block_reason,
                        "min_entry_cash": settings.min_entry_cash,
                        "min_csp_reserve": settings.min_csp_reserve,
                        "roll_dte_threshold_days": settings.roll_dte_threshold_days,
                        "close_dte_threshold_days": settings.close_dte_threshold_days,
                        "trading_window_open": trading_window.is_open,
                        "trading_window_reason": trading_window.reason,
                        "trading_window_timestamp": trading_window.local_timestamp.isoformat(),
                    },
                    "deterministic_shadow_decisions": [
                        {
                            "action_type": decision.action_type,
                            "status": decision.status,
                            "symbol": decision.symbol,
                            "rationale": decision.rationale,
                            "details": decision.details,
                        }
                        for decision in analysis.decisions
                    ],
                },
                default=str,
            )
            try:
                llm_payload, raw_llm_response = await run_structured_analysis(settings, user_prompt=analysis_prompt)
                llm_actions = validate_actions(
                    actions=parse_actions(llm_payload),
                    analysis=analysis,
                    positions=snapshot.positions,
                    supported_symbols=analysis_supported_symbols,
                    enable_new_entries=enable_new_entries,
                    enable_management=enable_management,
                )
            except Exception as exc:
                llm_error = str(exc)
                store.record_decision(
                    run_id=run.id,
                    action_type="llm_analysis",
                    status="failed",
                    rationale=f"LLM structured analysis failed: {exc}",
                    details={"error": str(exc)},
                )

        store.record_decision(
            run_id=run.id,
            action_type="reconcile",
            status="executed",
            rationale="Reconciled live Schwab account state into broker tables",
            details={"positions": len(positions), "account_hash": snapshot.account_hash},
        )
        for decision in analysis.decisions:
            store.record_decision(
                run_id=run.id,
                action_type=decision.action_type,
                status=decision.status,
                symbol=decision.symbol,
                rationale=decision.rationale,
                details=decision.details,
            )

        for index, validated in enumerate(llm_actions, start=1):
            intent = store.record_action_intent(
                run_id=run.id,
                action_index=index,
                action_type=validated.proposed.action_type,
                symbol=validated.proposed.symbol,
                strategy_type=validated.proposed.strategy_type,
                status="proposed",
                confidence=validated.proposed.confidence,
                quantity=validated.proposed.quantity,
                option_type=validated.proposed.option_type,
                expiration_date=None
                if validated.proposed.expiration is None
                else datetime(
                    validated.proposed.expiration.year,
                    validated.proposed.expiration.month,
                    validated.proposed.expiration.day,
                    tzinfo=UTC,
                ),
                strike_price=validated.proposed.strike,
                limit_price=validated.proposed.limit_price,
                related_position_key=validated.proposed.related_position_key,
                validation_status=validated.validation_status,
                execution_status="not_submitted",
                rationale=validated.proposed.rationale,
                raw_payload=validated.proposed.raw_payload,
                validation_errors=validated.validation_errors,
            )
            if validated.validation_status == "valid":
                store.record_decision(
                    run_id=run.id,
                    action_type=f"llm_{validated.proposed.action_type}",
                    status="proposed",
                    symbol=validated.proposed.symbol,
                    rationale=" | ".join(validated.proposed.rationale),
                    details={"confidence": validated.proposed.confidence, "execution_supported": validated.execution_supported},
                )
                if execution_enabled and validated.execution_supported:
                    try:
                        result = await execute_action(client, account_hash=snapshot.account_hash, validated=validated)
                        store.update_action_intent(
                            intent_id=intent.id,
                            execution_status=result.execution_status,
                            schwab_order_id=result.schwab_order_id,
                        )
                        store.record_decision(
                            run_id=run.id,
                            action_type=f"execute_{validated.proposed.action_type}",
                            status="executed" if result.execution_status == "submitted" else result.execution_status,
                            symbol=validated.proposed.symbol,
                            rationale=result.message,
                            details={"schwab_order_id": result.schwab_order_id},
                        )
                    except Exception as exc:
                        store.update_action_intent(
                            intent_id=intent.id,
                            execution_status="failed",
                            validation_errors=validated.validation_errors + [str(exc)],
                        )
                        store.record_decision(
                            run_id=run.id,
                            action_type=f"execute_{validated.proposed.action_type}",
                            status="failed",
                            symbol=validated.proposed.symbol,
                            rationale=str(exc),
                            details={},
                        )
            else:
                store.record_decision(
                    run_id=run.id,
                    action_type=f"llm_{validated.proposed.action_type}",
                    status="rejected",
                    symbol=validated.proposed.symbol,
                    rationale=" | ".join(validated.validation_errors),
                    details={"confidence": validated.proposed.confidence},
                )

        report_text = format_shadow_report(
            service_name=settings.service_name,
            run_type=settings.run_type,
            dry_run=settings.dry_run,
            account_label=snapshot.account_number or snapshot.account_hash,
            analysis=analysis,
            schedule_note=(
                f"{trading_window.local_timestamp.strftime('%Y-%m-%d %H:%M %Z')} | {trading_window.reason}"
            ),
        )
        report_text = "\n\n".join(
            [
                report_text,
                _format_trade_analysis_report(
                    analysis_enabled=analysis_enabled,
                    llm_actions=llm_actions,
                    llm_error=llm_error,
                ),
            ]
        )
        candidate_section = _format_candidate_discovery_report(candidate_ideas=candidate_ideas)
        if candidate_discovery_source != "none":
            candidate_section = "\n".join([candidate_section, f"Source: {candidate_discovery_source}"])
        if candidate_discovery_error:
            candidate_section = "\n".join([candidate_section, f"Discovery error: {candidate_discovery_error}"])
        report_text = "\n\n".join(
            [
                report_text,
                candidate_section,
            ]
        )
        store.finish_run(
            run_id=run.id,
            status="completed",
            account_hash=snapshot.account_hash,
            details={
                "phase": "reconcile",
                "positions": len(positions),
                "account_number": snapshot.account_number,
                "cash_balance": str(analysis.cash_balance),
                "cash_reserved": str(analysis.reserved_cash),
                "deployable_cash": str(analysis.deployable_cash),
                "shadow_decisions": len(analysis.decisions),
                "tracked_symbols": analysis_supported_symbols,
                "entry_candidate_ideas": candidate_ideas,
                "entry_candidate_source": candidate_discovery_source,
                "entry_candidate_error": candidate_discovery_error,
                "llm_actions": len(llm_actions),
                "raw_llm_response": raw_llm_response,
                "trading_window_open": trading_window.is_open,
                "trading_window_reason": trading_window.reason,
                "trading_window_timestamp": trading_window.local_timestamp.isoformat(),
            },
        )
        await notifier.send(report_text)
        logging.getLogger(__name__).info(
            "Automation shadow run complete: account=%s positions=%d decisions=%d dry_run=%s",
            snapshot.account_hash,
            len(positions),
            len(analysis.decisions),
            settings.dry_run,
        )
        return 0
    except Exception as exc:
        store.finish_run(
            run_id=run.id,
            status="failed",
            error_text=str(exc),
            details={"phase": "reconcile", "error": str(exc)},
        )
        await notifier.send_failure(
            service_name=settings.service_name,
            run_type=settings.run_type,
            error_text=str(exc),
        )
        raise


async def _main_async(args: argparse.Namespace) -> int:
    workspace_root = _find_project_root()
    load_dotenv(workspace_root / ".env")

    settings = load_settings()
    if args.dry_run:
        settings = replace(settings, dry_run=True)

    if not args.loop:
        return await _run_once(settings)

    while True:
        try:
            await _run_once(settings)
        except Exception:
            logging.getLogger(__name__).exception("Automation loop iteration failed")
        await asyncio.sleep(max(args.interval_seconds, 1))


def run() -> int:
    args = _parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    raise SystemExit(run())