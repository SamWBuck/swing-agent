from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, replace
from datetime import UTC, datetime
import json
import logging
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from swing_agent_database import find_project_root

from .config import Settings, load_settings
from .copilot_runner import run_structured_analysis
from .execution import execute_action
from .notifier import DiscordNotifier
from .schwab_client import create_async_client
from .sync import AccountSnapshot, fetch_account_snapshot


log = logging.getLogger(__name__)


@dataclass(frozen=True)
class TradingWindowStatus:
    is_open: bool
    local_timestamp: datetime
    reason: str


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
            reason=f"Within trading window {start.strftime('%H:%M')} to {end.strftime('%H:%M')} {settings.trading_timezone}",
        )
    return TradingWindowStatus(
        is_open=False,
        local_timestamp=local_timestamp,
        reason=f"Outside trading window {start.strftime('%H:%M')} to {end.strftime('%H:%M')} {settings.trading_timezone}",
    )


def _build_context(snapshot: AccountSnapshot, trading_window: TradingWindowStatus, *, dry_run: bool) -> dict:
    return {
        "account_number": snapshot.account_number,
        "cash_available": str(snapshot.cash_available),
        "cash_reserved": str(snapshot.cash_reserved),
        "liquidation_value": str(snapshot.liquidation_value),
        "positions": snapshot.positions,
        "trading_window": {
            "is_open": trading_window.is_open,
            "reason": trading_window.reason,
            "timestamp": trading_window.local_timestamp.isoformat(),
        },
        "dry_run": dry_run,
    }


def _build_report(
    *,
    settings: Settings,
    snapshot: AccountSnapshot,
    trading_window: TradingWindowStatus,
    actions: list[dict],
    executed: list[tuple[dict, str | None]],
    errors: list[str],
) -> str:
    lines = [
        f"[{settings.service_name}] {settings.run_type}",
        f"Account: {snapshot.account_number or snapshot.account_hash}",
        f"Schedule: {trading_window.local_timestamp.strftime('%Y-%m-%d %H:%M %Z')} | {trading_window.reason}",
        f"Dry run: {'yes' if settings.dry_run else 'no'}",
        f"Positions: {len(snapshot.positions)}",
    ]
    if actions:
        lines.append("\nActions:")
        for action in actions:
            symbol = action.get("symbol") or "portfolio"
            action_type = action.get("action_type", "?")
            confidence = action.get("confidence", "?")
            rationale = action.get("rationale") or []
            rationale_str = "; ".join(rationale) if isinstance(rationale, list) else str(rationale)
            lines.append(f"- {symbol}: {action_type} ({confidence}) — {rationale_str}")
    else:
        lines.append("\nNo actions proposed.")
    if executed:
        lines.append("\nExecuted:")
        for action, order_id in executed:
            lines.append(f"- {action.get('symbol') or '?'}: {action.get('action_type', '?')} order_id={order_id}")
    if errors:
        lines.append("\nExecution errors:")
        for err in errors:
            lines.append(f"- {err}")
    return "\n".join(lines)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Schwab automation worker")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Force dry-run mode: analysis always runs, trading window is bypassed, no orders are placed",
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


async def _run_once(settings: Settings) -> None:
    notifier = DiscordNotifier(
        settings.discord_webhook_url,
        timeout_seconds=settings.webhook_timeout_seconds,
        bot_token=settings.discord_bot_token,
        channel_id=settings.discord_channel_id,
    )
    try:
        client = create_async_client(settings)

        # Step 1: fetch portfolio from Schwab
        snapshot = await fetch_account_snapshot(client, settings)

        # Step 2: build context — raw positions + cash + trading window
        trading_window = _get_trading_window_status(datetime.now(tz=UTC), settings)
        if settings.dry_run:
            execution_enabled = False
        else:
            execution_enabled = settings.execution_enabled and trading_window.is_open

        context = _build_context(snapshot, trading_window, dry_run=settings.dry_run)

        # Step 3+4: send context + available MCP tools to the agent
        user_prompt = (
            "Evaluate this portfolio and propose options income actions using the available MCP tools.\n\n"
            "Portfolio context:\n"
            f"{json.dumps(context, indent=2, default=str)}"
        )
        result, _raw_response = await run_structured_analysis(settings, user_prompt=user_prompt)

        # Step 5: parse the response
        actions: list[dict] = result.get("actions") or []
        log.info("LLM analysis returned %d actions for account=%s", len(actions), snapshot.account_hash)

        # Step 6: execute trades via Schwab-py
        executed: list[tuple[dict, str | None]] = []
        errors: list[str] = []
        if execution_enabled:
            for action in actions:
                action_type = action.get("action_type", "")
                if action_type in {"hold", "skip"}:
                    continue
                try:
                    order_id = await execute_action(client, account_hash=snapshot.account_hash, action=action)
                    if order_id is not None:
                        executed.append((action, order_id))
                except Exception as exc:
                    log.error("Failed to execute %s for %s: %s", action_type, action.get("symbol"), exc)
                    errors.append(f"{action_type} {action.get('symbol')}: {exc}")

        report = _build_report(
            settings=settings,
            snapshot=snapshot,
            trading_window=trading_window,
            actions=actions,
            executed=executed,
            errors=errors,
        )
        await notifier.send(report)
        log.info(
            "Automation run complete: account=%s positions=%d actions=%d executed=%d dry_run=%s",
            snapshot.account_hash,
            len(snapshot.positions),
            len(actions),
            len(executed),
            settings.dry_run,
        )
    except Exception as exc:
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
        await _run_once(settings)
        return 0

    consecutive_failures = 0
    while True:
        try:
            await _run_once(settings)
            consecutive_failures = 0
        except Exception:
            consecutive_failures += 1
            log.exception("Automation loop iteration failed")
            if consecutive_failures >= settings.max_consecutive_failures:
                log.error(
                    "Automation loop aborting after %d consecutive failures",
                    consecutive_failures,
                )
                return 1
        await asyncio.sleep(args.interval_seconds)


def run() -> None:
    args = _parse_args()
    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    raise SystemExit(asyncio.run(_main_async(args)))


if __name__ == "__main__":
    raise SystemExit(run())
