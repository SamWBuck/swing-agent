from __future__ import annotations

import asyncio
import argparse
import logging
from pathlib import Path

from dotenv import load_dotenv

from .config import load_settings
from .schwab_client import create_async_client


def _find_project_root() -> Path:
    candidates = [Path.cwd(), Path(__file__).resolve().parent]
    for candidate in candidates:
        for current in (candidate, *candidate.parents):
            if (current / ".env").exists() or (current / ".git").exists():
                return current
    return Path.cwd()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create or refresh the Schwab token file")
    parser.add_argument("--symbol", default="SPY", help="Symbol used for the validation request")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level",
    )
    return parser.parse_args()


async def _main_async(args: argparse.Namespace) -> None:
    project_root = _find_project_root()
    load_dotenv(project_root / ".env")

    settings = load_settings()
    client = create_async_client(settings)
    response = await client.get_price_history_every_day(args.symbol)
    response.raise_for_status()

    logging.getLogger(__name__).info(
        "Schwab token is ready at %s; validation request for %s returned %d candles",
        settings.schwab_token_path,
        args.symbol,
        len(response.json().get("candles", [])),
    )


def main() -> None:
    args = _parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    asyncio.run(_main_async(args))


if __name__ == "__main__":
    main()