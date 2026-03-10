from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from dotenv import load_dotenv

from .config import load_settings
from .repository import Repository
from .sync import INTERVAL_SPECS, sync_symbols


def _find_project_root() -> Path:
    candidates = [Path.cwd(), Path(__file__).resolve().parent]
    for candidate in candidates:
        for current in (candidate, *candidate.parents):
            if (current / ".env").exists() or (current / ".git").exists():
                return current
    return Path.cwd()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync price candles from Schwab into Postgres")
    parser.add_argument("--symbol", action="append", dest="symbols", help="Limit sync to one or more symbols")
    parser.add_argument(
        "--interval",
        action="append",
        dest="intervals",
        choices=sorted(INTERVAL_SPECS),
        help="Limit sync to one or more intervals",
    )
    parser.add_argument("--limit", type=int, default=None, help="Limit the number of symbols processed")
    parser.add_argument(
        "--missing-only",
        action="store_true",
        help="Only process symbols whose selected interval timestamps are still null in symbol_availability",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level",
    )
    return parser.parse_args()


async def _main_async(args: argparse.Namespace) -> int:
    workspace_root = _find_project_root()
    load_dotenv(workspace_root / ".env")

    settings = load_settings()
    repository = Repository(settings)
    result = await sync_symbols(
        settings,
        repository,
        symbols=args.symbols,
        intervals=args.intervals,
        limit=args.limit,
        missing_only=args.missing_only,
    )
    logging.getLogger(__name__).info(
        "Sync complete: symbols=%d intervals=%d fetched=%d written=%d",
        result.symbols_processed,
        result.intervals_processed,
        result.candles_fetched,
        result.candles_written,
    )
    return 0


def run() -> None:
    args = _parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    raise SystemExit(asyncio.run(_main_async(args)))


if __name__ == "__main__":
    run()