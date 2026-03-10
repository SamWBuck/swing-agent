from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace
import logging
from pathlib import Path

from dotenv import load_dotenv

from swing_agent_database import AutomationStore

from .config import load_settings
from .notifier import DiscordNotifier
from .schwab_client import create_async_client
from .sync import fetch_account_snapshot


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
    return parser.parse_args()


async def _main_async(args: argparse.Namespace) -> int:
    workspace_root = _find_project_root()
    load_dotenv(workspace_root / ".env")

    settings = load_settings()
    if args.dry_run:
        settings = replace(settings, dry_run=True)

    store = AutomationStore(settings.automation_store)
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
        account = store.upsert_account(
            account_hash=snapshot.account_hash,
            account_number=snapshot.account_number,
            account_type=snapshot.account_type,
            display_name=snapshot.display_name,
            cash_available=snapshot.cash_available,
            cash_reserved=snapshot.cash_reserved,
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
        store.record_decision(
            run_id=run.id,
            action_type="reconcile",
            status="executed",
            rationale="Reconciled live Schwab account state into broker tables",
            details={"positions": len(positions), "account_hash": snapshot.account_hash},
        )
        store.finish_run(
            run_id=run.id,
            status="completed",
            account_hash=snapshot.account_hash,
            details={
                "phase": "reconcile",
                "positions": len(positions),
                "account_number": snapshot.account_number,
            },
        )
        await notifier.send_run_summary(
            account_label=snapshot.account_number or snapshot.account_hash,
            positions_count=len(positions),
            cash_available=None if account.cash_available is None else str(account.cash_available),
            liquidation_value=None if account.liquidation_value is None else str(account.liquidation_value),
            run_details={
                "service_name": settings.service_name,
                "run_type": settings.run_type,
                "dry_run": settings.dry_run,
            },
        )
        logging.getLogger(__name__).info(
            "Automation reconcile complete: account=%s positions=%d dry_run=%s",
            snapshot.account_hash,
            len(positions),
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


def run() -> None:
    args = _parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    raise SystemExit(asyncio.run(_main_async(args)))


if __name__ == "__main__":
    run()