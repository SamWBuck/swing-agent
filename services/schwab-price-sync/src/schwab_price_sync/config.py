from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import URL


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return int(value)


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if value is None or value == "":
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _optional_env(name: str) -> str | None:
    value = os.getenv(name)
    if value is None or value == "":
        return None
    return value


def _find_project_root() -> Path:
    candidates = [Path.cwd(), Path(__file__).resolve().parent]
    for candidate in candidates:
        for current in (candidate, *candidate.parents):
            if (current / ".env").exists() or (current / ".git").exists():
                return current
    return Path.cwd()


def _resolve_path(value: str, *, base_dir: Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()


def _build_database_url() -> str:
    direct_url = os.getenv("DATABASE_URL")
    if direct_url:
        return direct_url

    required_names = ["DB_USER", "DB_PASSWORD", "DB_HOST", "DB_PORT", "DB_NAME"]
    values = {name: os.getenv(name) for name in required_names}
    missing = [name for name, value in values.items() if value is None or value == ""]
    if missing:
        raise RuntimeError(
            "Missing database environment variables for schwab-price-sync: "
            f"{missing}. Set DB_USER, DB_PASSWORD, DB_HOST, DB_PORT, and DB_NAME or provide DATABASE_URL."
        )

    query: dict[str, str] = {}
    sslmode = _optional_env("DB_SSLMODE")
    sslrootcert = _optional_env("DB_SSLROOTCERT")
    sslcert = _optional_env("DB_SSLCERT")
    sslkey = _optional_env("DB_SSLKEY")

    if sslmode:
        query["sslmode"] = sslmode
    if sslrootcert:
        query["sslrootcert"] = sslrootcert
    if sslcert:
        query["sslcert"] = sslcert
    if sslkey:
        query["sslkey"] = sslkey

    url = URL.create(
        drivername="postgresql+psycopg",
        username=values["DB_USER"],
        password=values["DB_PASSWORD"],
        host=values["DB_HOST"],
        port=int(values["DB_PORT"]),
        database=values["DB_NAME"],
        query=query,
    )
    return url.render_as_string(hide_password=False)


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
    workspace_root = _find_project_root()
    token_path = _resolve_path(
        os.getenv("SCHWAB_TOKEN_PATH", "token.json"),
        base_dir=workspace_root,
    )
    return Settings(
        database_url=_build_database_url(),
        candles_schema=os.getenv("PRICE_CANDLES_SCHEMA", "public"),
        candles_table=os.getenv("PRICE_CANDLES_TABLE", "price_candles"),
        availability_schema=os.getenv("SYMBOL_AVAILABILITY_SCHEMA", "public"),
        availability_table=os.getenv("SYMBOL_AVAILABILITY_TABLE", "symbol_availability"),
        schwab_api_key=_required_env("SCHWAB_API_KEY"),
        schwab_app_secret=_required_env("SCHWAB_APP_SECRET"),
        schwab_callback_url=_required_env("SCHWAB_CALLBACK_URL"),
        schwab_token_path=token_path,
        batch_size=_env_int("SCHWAB_SYNC_BATCH_SIZE", 1000),
        need_extended_hours_data=_env_bool("SCHWAB_NEED_EXTENDED_HOURS_DATA", False),
        interactive_login=_env_bool("SCHWAB_INTERACTIVE_LOGIN", True),
        request_timeout_seconds=_env_int("SCHWAB_REQUEST_TIMEOUT_SECONDS", 60),
    )