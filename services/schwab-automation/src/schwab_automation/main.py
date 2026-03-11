from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
import json
import logging
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from swing_agent_database import AutomationStore, SymbolAvailabilityStore, find_project_root

from .analysis import analyze_portfolio
from .candidate_discovery import EntryCandidateIdea, parse_entry_candidate_ideas
from .config import Settings, load_settings
from .copilot_runner import run_structured_analysis
from .execution import execute_action
from .llm_actions import ValidatedAction, parse_actions, validate_actions
from .notifier import DiscordNotifier
from .option_candidates import fetch_broker_option_candidates
from .schwab_client import create_async_client
from .sync import AccountSnapshot, fetch_account_snapshot


log = logging.getLogger(__name__)
_ACTION_TYPES_REQUIRING_MCP_CONFIRMATION = {
    "close_option",
    "roll_option",
    "sell_covered_call",
    "sell_cash_secured_put",
}


@dataclass(frozen=True)
class TradingWindowStatus:
    is_open: bool
    local_timestamp: datetime
    reason: str


@dataclass(frozen=True)
class ExecutionPolicy:
    analysis_supported_symbols: list[str]
    analysis_enabled: bool
    enable_new_entries: bool
    enable_management: bool
    execution_enabled: bool
    recommendation_mode: bool
    execution_block_reason: str | None


@dataclass(frozen=True)
class ReconciledState:
    snapshot: AccountSnapshot
    analysis: object
    positions: list[object]
    trading_window: TradingWindowStatus
    policy: ExecutionPolicy
    supported_symbols: list[str]


@dataclass(frozen=True)
class DiscoveryState:
    candidate_ideas: list[EntryCandidateIdea] = field(default_factory=list)
    broker_option_candidates: dict[str, list[dict[str, object]]] = field(default_factory=dict)
    source: str = "none"
    error: str | None = None


@dataclass(frozen=True)
class AnalysisState:
    llm_actions: list[ValidatedAction] = field(default_factory=list)
    raw_llm_response: str | None = None
    llm_error: str | None = None


def _format_structured_context(payload: dict[str, object]) -> str:
    return json.dumps(payload, indent=2, default=str)


def _build_candidate_discovery_prompt(settings: Settings, state: ReconciledState) -> str:
    context = {
        "portfolio": {
            "account_number": state.snapshot.account_number,
            "cash_balance": str(state.analysis.cash_balance),
            "reserved_cash": str(state.analysis.reserved_cash),
            "deployable_cash": str(state.analysis.deployable_cash),
        },
        "positions": state.snapshot.positions,
        "supported_symbols": state.policy.analysis_supported_symbols,
        "policy": {
            "enable_new_entries": state.policy.enable_new_entries,
            "recommendation_mode": state.policy.recommendation_mode,
            "execution_enabled": state.policy.execution_enabled,
            "execution_block_reason": state.policy.execution_block_reason,
            "min_entry_cash": settings.min_entry_cash,
            "min_csp_reserve": settings.min_csp_reserve,
            "trading_window_open": state.trading_window.is_open,
            "trading_window_reason": state.trading_window.reason,
        },
    }
    return (
        "Research the best options-income entry candidates for this portfolio using the available MCP tools. "
        "Start with Yahoo Finance MCP for trade discovery and current options context, then use Price Data MCP "
        "to confirm trend, volatility, and support/resistance before elevating any symbol. "
        "If SEC filings materially change risk, use SEC EDGAR MCP before finalizing the candidate list. "
        "Only consider symbols from the supported universe, respect the cash and policy guards, and avoid exact contract selection in this phase. "
        "When recommendation mode is enabled, you may still surface next-session ideas even if execution is currently blocked.\n\n"
        "Structured portfolio context:\n"
        f"{_format_structured_context(context)}"
    )


def _build_analysis_prompt(settings: Settings, state: ReconciledState, discovery: DiscoveryState) -> str:
    context = {
        "portfolio": {
            "account_number": state.snapshot.account_number,
            "cash_balance": str(state.analysis.cash_balance),
            "reserved_cash": str(state.analysis.reserved_cash),
            "deployable_cash": str(state.analysis.deployable_cash),
            "liquidation_value": None if state.snapshot.liquidation_value is None else str(state.snapshot.liquidation_value),
        },
        "positions": state.snapshot.positions,
        "symbol_analysis": [item.__dict__ for item in state.analysis.symbols],
        "supported_symbols": state.policy.analysis_supported_symbols,
        "entry_candidate_ideas": [idea.__dict__ for idea in discovery.candidate_ideas],
        "broker_option_candidates": discovery.broker_option_candidates,
        "policy": {
            "enable_new_entries": state.policy.enable_new_entries,
            "enable_management": state.policy.enable_management,
            "execution_enabled": state.policy.execution_enabled,
            "recommendation_mode": state.policy.recommendation_mode,
            "execution_block_reason": state.policy.execution_block_reason,
            "min_entry_cash": settings.min_entry_cash,
            "min_csp_reserve": settings.min_csp_reserve,
            "roll_dte_threshold_days": settings.roll_dte_threshold_days,
            "close_dte_threshold_days": settings.close_dte_threshold_days,
            "trading_window_open": state.trading_window.is_open,
            "trading_window_reason": state.trading_window.reason,
            "trading_window_timestamp": state.trading_window.local_timestamp.isoformat(),
        },
        "deterministic_shadow_decisions": [
            {
                "action_type": decision.action_type,
                "status": decision.status,
                "symbol": decision.symbol,
                "rationale": decision.rationale,
                "details": decision.details,
            }
            for decision in state.analysis.decisions
        ],
    }
    return (
        "Evaluate this portfolio's existing positions and entry opportunities using the available MCP tools before making any actionable decision. "
        "Use Price Data MCP to confirm current technical context, and use Yahoo Finance MCP to confirm expirations, strikes, premiums, and realistic limit prices for any option action. "
        "Use SEC EDGAR MCP when filings materially affect risk. "
        "Only propose actions that fit the provided policy guards and current live portfolio state. "
        "If recommendation mode is enabled, you may still recommend next-session actions when execution is blocked, but only after live MCP confirmation.\n\n"
        "Structured portfolio context:\n"
        f"{_format_structured_context(context)}"
    )


def _filter_actions_without_mcp_confirmation(actions):
    filtered_actions = [
        action for action in actions if action.action_type not in _ACTION_TYPES_REQUIRING_MCP_CONFIRMATION
    ]
    return filtered_actions, len(actions) - len(filtered_actions)


def _format_reasoning_report(
    *,
    analysis_enabled: bool,
    llm_actions: list[ValidatedAction],
    llm_error: str | None,
) -> str:
    lines = ["Reasoning:"]
    if not analysis_enabled:
        lines.append("- analysis not run")
        return "\n".join(lines)
    if llm_error:
        lines.append(f"- analysis failed: {llm_error}")
        return "\n".join(lines)
    if not llm_actions:
        lines.append("- no action reasoning returned")
        return "\n".join(lines)

    for validated in llm_actions:
        proposed = validated.proposed
        symbol = proposed.symbol or "portfolio"
        rationale = "; ".join(proposed.rationale) if proposed.rationale else "no rationale"
        summary = f"- {symbol}: {proposed.action_type}"
        contract_bits: list[str] = []
        if proposed.quantity is not None:
            contract_bits.append(f"qty {proposed.quantity}")
        if proposed.option_type is not None:
            contract_bits.append(proposed.option_type)
        if proposed.expiration is not None:
            contract_bits.append(f"exp {proposed.expiration}")
        if proposed.strike is not None:
            contract_bits.append(f"strike {proposed.strike}")
        if proposed.target_expiration is not None:
            contract_bits.append(f"target exp {proposed.target_expiration}")
        if proposed.target_strike is not None:
            contract_bits.append(f"target strike {proposed.target_strike}")
        if contract_bits:
            summary = f"{summary} ({', '.join(contract_bits)})"
        if validated.validation_status != "valid":
            summary = f"{summary} [{validated.validation_status}]"
        lines.append(summary)
        lines.append(f"  {rationale}")
    return "\n".join(lines)


def _format_positions_report(state: ReconciledState) -> str:
    lines = ["Positions:"]
    if not state.analysis.symbols:
        lines.append("- none")
        return "\n".join(lines)

    for symbol in state.analysis.symbols:
        parts: list[str] = []
        if symbol.share_quantity > 0:
            parts.append(f"shares {symbol.share_quantity.normalize()}")
        if symbol.short_call_contracts > 0:
            parts.append(f"short calls {symbol.short_call_contracts}")
        if symbol.long_call_contracts > 0:
            parts.append(f"long calls {symbol.long_call_contracts}")
        if symbol.short_put_contracts > 0:
            parts.append(f"short puts {symbol.short_put_contracts}")
        if not parts:
            parts.append("unclassified")
        lines.append(f"- {symbol.symbol}: {', '.join(parts)}")
    return "\n".join(lines)


def _format_header_report(*, settings: Settings, state: ReconciledState) -> str:
    lines = [
        f"[{settings.service_name}] {settings.run_type} shadow report",
        f"Account: {state.snapshot.account_number or state.snapshot.account_hash}",
        (
            "Schedule: "
            f"{state.trading_window.local_timestamp.strftime('%Y-%m-%d %H:%M %Z')} | {state.trading_window.reason}"
        ),
        f"Dry run: {'yes' if settings.dry_run else 'no'}",
    ]
    return "\n".join(lines)


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


def _reconcile_snapshot(store: AutomationStore, snapshot, analysis):
    """Persist the latest broker account snapshot and replace active broker positions."""
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
    return store.replace_positions(
        account_hash=snapshot.account_hash,
        positions=snapshot.positions,
        synced_at=snapshot.synced_at,
    )


async def _refresh_live_validation(
    *,
    client,
    settings: Settings,
    store: AutomationStore,
    supported_symbols: list[str],
    account_hash: str,
    validated: ValidatedAction,
):
    """Refresh broker state and re-run hard validation immediately before live execution."""
    latest_snapshot = await fetch_account_snapshot(client, settings)
    if latest_snapshot.account_hash != account_hash:
        raise RuntimeError("Configured Schwab account changed during live execution revalidation")

    trading_window = _get_trading_window_status(datetime.now(tz=UTC), settings)
    latest_analysis = analyze_portfolio(
        cash_balance=latest_snapshot.cash_available,
        positions=latest_snapshot.positions,
        as_of=latest_snapshot.synced_at,
        supported_symbols=supported_symbols if trading_window.is_open else [],
        min_csp_reserve=settings.min_csp_reserve,
        min_entry_cash=settings.min_entry_cash,
        roll_dte_threshold_days=settings.roll_dte_threshold_days,
        close_dte_threshold_days=settings.close_dte_threshold_days,
    )
    latest_positions = _reconcile_snapshot(store, latest_snapshot, latest_analysis)
    refreshed = validate_actions(
        actions=[validated.proposed],
        analysis=latest_analysis,
        positions=latest_snapshot.positions,
        supported_symbols=supported_symbols if trading_window.is_open else [],
        enable_new_entries=settings.enable_new_entries and trading_window.is_open,
        enable_management=settings.enable_management and trading_window.is_open,
        max_contracts_per_symbol=settings.max_contracts_per_symbol,
        min_account_cash_floor=settings.min_account_cash_floor,
        liquidation_value=latest_snapshot.liquidation_value,
        max_position_pct_of_portfolio=settings.max_position_pct_of_portfolio,
    )[0]
    return latest_snapshot, latest_analysis, latest_positions, trading_window, refreshed


def _build_execution_policy(
    *,
    settings: Settings,
    trading_window: TradingWindowStatus,
    supported_symbols: list[str],
) -> ExecutionPolicy:
    """Resolve the current run policy gates from configuration and market timing."""
    analysis_supported_symbols = supported_symbols if settings.dry_run or trading_window.is_open else []
    analysis_enabled = settings.analysis_enabled and (settings.dry_run or trading_window.is_open)
    enable_new_entries = settings.enable_new_entries and (settings.dry_run or trading_window.is_open)
    enable_management = settings.enable_management and (settings.dry_run or trading_window.is_open)

    execution_enabled = (
        settings.execution_enabled
        and not settings.dry_run
        and trading_window.is_open
        and not settings.kill_switch_enabled
    )
    execution_block_reason = None
    if settings.dry_run:
        execution_block_reason = "Dry run mode disables order execution"
    elif not trading_window.is_open:
        execution_block_reason = trading_window.reason
    elif settings.kill_switch_enabled:
        execution_block_reason = "Manual kill switch is enabled"

    return ExecutionPolicy(
        analysis_supported_symbols=analysis_supported_symbols,
        analysis_enabled=analysis_enabled,
        enable_new_entries=enable_new_entries,
        enable_management=enable_management,
        execution_enabled=execution_enabled,
        recommendation_mode=settings.dry_run,
        execution_block_reason=execution_block_reason,
    )


async def _reconcile_phase(settings: Settings, store: AutomationStore, symbol_store: SymbolAvailabilityStore, client) -> ReconciledState:
    """Fetch broker state, compute portfolio analysis, and persist the reconciled snapshot."""
    snapshot = await fetch_account_snapshot(client, settings)
    trading_window = _get_trading_window_status(datetime.now(tz=UTC), settings)
    supported_symbols = _filter_supported_symbols([record.symbol for record in symbol_store.list_symbol_availability()])
    policy = _build_execution_policy(settings=settings, trading_window=trading_window, supported_symbols=supported_symbols)
    analysis = analyze_portfolio(
        cash_balance=snapshot.cash_available,
        positions=snapshot.positions,
        as_of=snapshot.synced_at,
        supported_symbols=policy.analysis_supported_symbols,
        min_csp_reserve=settings.min_csp_reserve,
        min_entry_cash=settings.min_entry_cash,
        roll_dte_threshold_days=settings.roll_dte_threshold_days,
        close_dte_threshold_days=settings.close_dte_threshold_days,
    )
    positions = _reconcile_snapshot(store, snapshot, analysis)
    return ReconciledState(
        snapshot=snapshot,
        analysis=analysis,
        positions=positions,
        trading_window=trading_window,
        policy=policy,
        supported_symbols=supported_symbols,
    )


def _record_policy_gates(store: AutomationStore, *, run_id: int, settings: Settings, state: ReconciledState) -> None:
    """Persist schedule and kill-switch decisions for the current run."""
    if settings.dry_run:
        store.record_decision(
            run_id=run_id,
            action_type="schedule_gate",
            status="executed",
            rationale=(
                f"Dry run bypassed trading window at {state.trading_window.local_timestamp.isoformat()} ({state.trading_window.reason})."
            ),
            details={
                "trading_window_open": state.trading_window.is_open,
                "timezone": settings.trading_timezone,
            },
        )
    elif not state.trading_window.is_open:
        store.record_decision(
            run_id=run_id,
            action_type="schedule_gate",
            status="skipped",
            rationale=(
                f"Live automation blocked at {state.trading_window.local_timestamp.isoformat()} because {state.trading_window.reason.lower()}."
            ),
            details={
                "trading_window_open": False,
                "timezone": settings.trading_timezone,
            },
        )
    if settings.kill_switch_enabled:
        store.record_decision(
            run_id=run_id,
            action_type="manual_kill_switch",
            status="skipped",
            rationale="Live automation execution is blocked because AUTOMATION_KILL_SWITCH is enabled.",
            details={},
        )


async def _candidate_discovery_phase(
    settings: Settings,
    *,
    store: AutomationStore,
    run_id: int,
    client,
    state: ReconciledState,
) -> DiscoveryState:
    """Discover entry candidates through MCP-backed structured analysis."""
    if not state.policy.analysis_enabled or not state.policy.enable_new_entries or not state.policy.analysis_supported_symbols:
        return DiscoveryState()

    candidate_prompt = _build_candidate_discovery_prompt(settings, state)

    candidate_ideas: list[EntryCandidateIdea] = []
    discovery_error = None
    discovery_source = "none"
    skip_rationale: str | None = None
    try:
        candidate_payload, _, had_tool_events = await run_structured_analysis(
            settings,
            user_prompt=candidate_prompt,
            prompt_path=settings.entry_candidate_prompt_path,
        )
        candidate_ideas = parse_entry_candidate_ideas(candidate_payload)
        if not had_tool_events:
            if candidate_ideas:
                skip_rationale = (
                    "Candidate discovery returned ideas without MCP tool confirmation; discarded all candidates."
                )
                log.warning(skip_rationale)
            else:
                skip_rationale = "Candidate discovery completed without MCP tool confirmation."
                log.warning(skip_rationale)
            candidate_ideas = []
        elif candidate_ideas:
            discovery_source = "yahoo_mcp"
    except Exception as exc:
        discovery_error = str(exc)
        store.record_decision(
            run_id=run_id,
            action_type="candidate_discovery",
            status="failed",
            rationale=f"Candidate discovery failed: {exc}",
            details={"error": str(exc)},
        )

    if not candidate_ideas and discovery_error is None:
        store.record_decision(
            run_id=run_id,
            action_type="candidate_discovery",
            status="skipped",
            rationale=skip_rationale or "Candidate discovery completed without any structured entry ideas.",
            details={"candidate_count": 0, "mcp_confirmed": skip_rationale is None},
        )

    broker_option_candidates: dict[str, list[dict[str, object]]] = {}
    if candidate_ideas:
        broker_option_candidates = await fetch_broker_option_candidates(
            client,
            settings=settings,
            ideas=candidate_ideas,
            as_of=state.snapshot.synced_at,
        )

    return DiscoveryState(
        candidate_ideas=candidate_ideas,
        broker_option_candidates=broker_option_candidates,
        source=discovery_source,
        error=discovery_error,
    )


async def _analysis_phase(
    settings: Settings,
    *,
    store: AutomationStore,
    run_id: int,
    state: ReconciledState,
    discovery: DiscoveryState,
) -> AnalysisState:
    """Run the structured LLM analysis and convert it into validated actions."""
    if not state.policy.analysis_enabled:
        return AnalysisState()

    analysis_prompt = _build_analysis_prompt(settings, state, discovery)
    try:
        llm_payload, raw_llm_response, had_tool_events = await run_structured_analysis(
            settings,
            user_prompt=analysis_prompt,
        )
        parsed_actions = parse_actions(llm_payload)
        if not had_tool_events:
            parsed_actions, filtered_count = _filter_actions_without_mcp_confirmation(parsed_actions)
            if filtered_count:
                rationale = f"Filtered {filtered_count} actionable decisions due to missing MCP tool confirmation."
                log.warning(rationale)
                store.record_decision(
                    run_id=run_id,
                    action_type="llm_analysis",
                    status="skipped",
                    rationale=rationale,
                    details={
                        "filtered_action_count": filtered_count,
                        "allowed_without_mcp": ["hold", "skip"],
                    },
                )
        llm_actions = validate_actions(
            actions=parsed_actions,
            analysis=state.analysis,
            positions=state.snapshot.positions,
            supported_symbols=state.policy.analysis_supported_symbols,
            enable_new_entries=state.policy.enable_new_entries,
            enable_management=state.policy.enable_management,
            max_new_entries_per_run=settings.max_new_entries_per_run,
            max_contracts_per_symbol=settings.max_contracts_per_symbol,
            min_account_cash_floor=settings.min_account_cash_floor,
            liquidation_value=state.snapshot.liquidation_value,
            max_position_pct_of_portfolio=settings.max_position_pct_of_portfolio,
        )
        return AnalysisState(llm_actions=llm_actions, raw_llm_response=raw_llm_response)
    except Exception as exc:
        store.record_decision(
            run_id=run_id,
            action_type="llm_analysis",
            status="failed",
            rationale=f"LLM structured analysis failed: {exc}",
            details={"error": str(exc)},
        )
        return AnalysisState(llm_error=str(exc))


def _record_analysis_decisions(store: AutomationStore, *, run_id: int, state: ReconciledState) -> None:
    """Persist deterministic reconciliation and shadow-analysis decisions."""
    store.record_decision(
        run_id=run_id,
        action_type="reconcile",
        status="executed",
        rationale="Reconciled live Schwab account state into broker tables",
        details={"positions": len(state.positions), "account_hash": state.snapshot.account_hash},
    )
    for decision in state.analysis.decisions:
        store.record_decision(
            run_id=run_id,
            action_type=decision.action_type,
            status=decision.status,
            symbol=decision.symbol,
            rationale=decision.rationale,
            details=decision.details,
        )


async def _execution_phase(
    settings: Settings,
    *,
    store: AutomationStore,
    run_id: int,
    client,
    state: ReconciledState,
    analysis_state: AnalysisState,
) -> ReconciledState:
    """Persist intents, optionally execute valid actions, and keep broker state current."""
    current_state = state
    for index, validated in enumerate(analysis_state.llm_actions, start=1):
        intent = store.record_action_intent(
            run_id=run_id,
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
        if validated.validation_status != "valid":
            store.record_decision(
                run_id=run_id,
                action_type=f"llm_{validated.proposed.action_type}",
                status="rejected",
                symbol=validated.proposed.symbol,
                rationale=" | ".join(validated.validation_errors),
                details={"confidence": validated.proposed.confidence},
            )
            continue

        store.record_decision(
            run_id=run_id,
            action_type=f"llm_{validated.proposed.action_type}",
            status="proposed",
            symbol=validated.proposed.symbol,
            rationale=" | ".join(validated.proposed.rationale),
            details={"confidence": validated.proposed.confidence, "execution_supported": validated.execution_supported},
        )
        if not current_state.policy.execution_enabled or not validated.execution_supported:
            continue

        try:
            live_snapshot, live_analysis, live_positions, live_trading_window, live_validated = await _refresh_live_validation(
                client=client,
                settings=settings,
                store=store,
                supported_symbols=current_state.supported_symbols,
                account_hash=current_state.snapshot.account_hash,
                validated=validated,
            )
            live_policy = _build_execution_policy(
                settings=settings,
                trading_window=live_trading_window,
                supported_symbols=current_state.supported_symbols,
            )
            current_state = ReconciledState(
                snapshot=live_snapshot,
                analysis=live_analysis,
                positions=live_positions,
                trading_window=live_trading_window,
                policy=live_policy,
                supported_symbols=current_state.supported_symbols,
            )
            if live_validated.validation_status != "valid" or not live_validated.execution_supported:
                live_errors = list(live_validated.validation_errors)
                if not live_trading_window.is_open:
                    live_errors.append(live_trading_window.reason)
                store.update_action_intent(
                    intent_id=intent.id,
                    validation_status=live_validated.validation_status,
                    execution_status="not_submitted",
                    validation_errors=live_errors,
                )
                store.record_decision(
                    run_id=run_id,
                    action_type=f"execute_{validated.proposed.action_type}",
                    status="rejected",
                    symbol=validated.proposed.symbol,
                    rationale=" | ".join(live_errors) or "Live revalidation blocked execution",
                    details={},
                )
                continue

            result = await execute_action(client, account_hash=current_state.snapshot.account_hash, validated=live_validated)
            store.update_action_intent(
                intent_id=intent.id,
                validation_status=live_validated.validation_status,
                execution_status=result.execution_status,
                schwab_order_id=result.schwab_order_id,
            )
            store.record_decision(
                run_id=run_id,
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
                run_id=run_id,
                action_type=f"execute_{validated.proposed.action_type}",
                status="failed",
                symbol=validated.proposed.symbol,
                rationale=str(exc),
                details={},
            )
    return current_state


def _build_report(
    *,
    settings: Settings,
    state: ReconciledState,
    discovery: DiscoveryState,
    analysis_state: AnalysisState,
) -> str:
    """Build the final Discord report text for a completed automation cycle."""
    report_sections = [
        _format_header_report(settings=settings, state=state),
        _format_positions_report(state),
        _format_reasoning_report(
            analysis_enabled=state.policy.analysis_enabled,
            llm_actions=analysis_state.llm_actions,
            llm_error=analysis_state.llm_error,
        ),
    ]
    if discovery.error:
        report_sections.append(f"Discovery note:\n- {discovery.error}")
    report_text = "\n\n".join(report_sections)
    return report_text


async def _run_once(settings: Settings) -> int:
    """Execute one automation cycle: reconcile, analyze, validate, execute, and report."""
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
        state = await _reconcile_phase(settings, store, symbol_store, client)
        _record_policy_gates(store, run_id=run.id, settings=settings, state=state)
        discovery = await _candidate_discovery_phase(settings, store=store, run_id=run.id, client=client, state=state)
        analysis_state = await _analysis_phase(settings, store=store, run_id=run.id, state=state, discovery=discovery)
        _record_analysis_decisions(store, run_id=run.id, state=state)
        final_state = await _execution_phase(
            settings,
            store=store,
            run_id=run.id,
            client=client,
            state=state,
            analysis_state=analysis_state,
        )

        report_text = _build_report(
            settings=settings,
            state=final_state,
            discovery=discovery,
            analysis_state=analysis_state,
        )
        store.finish_run(
            run_id=run.id,
            status="completed",
            account_hash=final_state.snapshot.account_hash,
            details={
                "phase": "reconcile",
                "positions": len(final_state.positions),
                "account_number": final_state.snapshot.account_number,
                "cash_balance": str(final_state.analysis.cash_balance),
                "cash_reserved": str(final_state.analysis.reserved_cash),
                "deployable_cash": str(final_state.analysis.deployable_cash),
                "shadow_decisions": len(final_state.analysis.decisions),
                "tracked_symbols": final_state.policy.analysis_supported_symbols,
                "entry_candidate_ideas": [idea.__dict__ for idea in discovery.candidate_ideas],
                "entry_candidate_source": discovery.source,
                "entry_candidate_error": discovery.error,
                "llm_actions": len(analysis_state.llm_actions),
                "raw_llm_response": analysis_state.raw_llm_response,
                "trading_window_open": final_state.trading_window.is_open,
                "trading_window_reason": final_state.trading_window.reason,
                "trading_window_timestamp": final_state.trading_window.local_timestamp.isoformat(),
            },
        )
        await notifier.send(report_text)
        logging.getLogger(__name__).info(
            "Automation shadow run complete: account=%s positions=%d decisions=%d dry_run=%s",
            final_state.snapshot.account_hash,
            len(final_state.positions),
            len(final_state.analysis.decisions),
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
    workspace_root = find_project_root()
    load_dotenv(workspace_root / ".env")

    settings = load_settings()
    if args.dry_run:
        settings = replace(settings, dry_run=True)

    if not args.loop:
        return await _run_once(settings)

    consecutive_failures = 0
    while True:
        try:
            await _run_once(settings)
            consecutive_failures = 0
        except Exception:
            consecutive_failures += 1
            logging.getLogger(__name__).exception("Automation loop iteration failed")
            if settings.max_consecutive_failures > 0 and consecutive_failures >= settings.max_consecutive_failures:
                logging.getLogger(__name__).critical(
                    "Automation circuit breaker opened after %d consecutive failures",
                    consecutive_failures,
                )
                return 1
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