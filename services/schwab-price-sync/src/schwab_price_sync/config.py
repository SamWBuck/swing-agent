from __future__ import annotations

import os
from dataclasses import dataclass

from swing_agent_database import build_database_url, env_bool, env_int, find_project_root, required_env, resolve_path


@dataclass(frozen=True)
class Settings:
    database_url: str
    candles_schema: str
    candles_table: str
    availability_schema: str
    availability_table: str
    schwab_api_key: str
    schwab_app_secret: str
    schwab_callback_url: str
    schwab_token_path: Path
    batch_size: int
    need_extended_hours_data: bool
    interactive_login: bool
    request_timeout_seconds: int


def load_settings() -> Settings:
    workspace_root = find_project_root()
    token_path = resolve_path(
        os.getenv("SCHWAB_TOKEN_PATH", "token.json"),
        base_dir=workspace_root,
    )
    return Settings(
        database_url=build_database_url(consumer_name="schwab-price-sync"),
        candles_schema=os.getenv("PRICE_CANDLES_SCHEMA", "public"),
        candles_table=os.getenv("PRICE_CANDLES_TABLE", "price_candles"),
        availability_schema=os.getenv("SYMBOL_AVAILABILITY_SCHEMA", "public"),
        availability_table=os.getenv("SYMBOL_AVAILABILITY_TABLE", "symbol_availability"),
        schwab_api_key=required_env("SCHWAB_API_KEY"),
        schwab_app_secret=required_env("SCHWAB_APP_SECRET"),
        schwab_callback_url=required_env("SCHWAB_CALLBACK_URL"),
        schwab_token_path=token_path,
        batch_size=env_int("SCHWAB_SYNC_BATCH_SIZE", 1000),
        need_extended_hours_data=env_bool("SCHWAB_NEED_EXTENDED_HOURS_DATA", False),
        interactive_login=env_bool("SCHWAB_INTERACTIVE_LOGIN", True),
        request_timeout_seconds=env_int("SCHWAB_REQUEST_TIMEOUT_SECONDS", 60),
    )