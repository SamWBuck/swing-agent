from __future__ import annotations

import os
from dataclasses import dataclass

from sqlalchemy import URL


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return int(value)


def _optional_env(name: str) -> str | None:
    value = os.getenv(name)
    if value is None or value == "":
        return None
    return value


def _build_database_url() -> str:
    direct_url = os.getenv("DATABASE_URL")
    if direct_url:
        return direct_url

    required_names = ["DB_USER", "DB_PASSWORD", "DB_HOST", "DB_PORT", "DB_NAME"]
    values = {name: os.getenv(name) for name in required_names}
    missing = [name for name, value in values.items() if value is None or value == ""]
    if missing:
        raise RuntimeError(
            "Missing database environment variables for price-data-mcp: "
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
    schema_name: str
    table_name: str
    symbol_column: str
    interval_column: str
    timestamp_column: str
    open_column: str
    high_column: str
    low_column: str
    close_column: str
    volume_column: str
    default_limit: int
    max_limit: int


def load_settings() -> Settings:
    return Settings(
        database_url=_build_database_url(),
        schema_name=os.getenv("PRICE_CANDLES_SCHEMA", "public"),
        table_name=os.getenv("PRICE_CANDLES_TABLE", "price_candles"),
        symbol_column=os.getenv("PRICE_CANDLES_SYMBOL_COLUMN", "symbol"),
        interval_column=os.getenv("PRICE_CANDLES_INTERVAL_COLUMN", "interval"),
        timestamp_column=os.getenv("PRICE_CANDLES_TIMESTAMP_COLUMN", "ts"),
        open_column=os.getenv("PRICE_CANDLES_OPEN_COLUMN", "open"),
        high_column=os.getenv("PRICE_CANDLES_HIGH_COLUMN", "high"),
        low_column=os.getenv("PRICE_CANDLES_LOW_COLUMN", "low"),
        close_column=os.getenv("PRICE_CANDLES_CLOSE_COLUMN", "close"),
        volume_column=os.getenv("PRICE_CANDLES_VOLUME_COLUMN", "volume"),
        default_limit=_env_int("PRICE_DATA_DEFAULT_LIMIT", 200),
        max_limit=_env_int("PRICE_DATA_MAX_LIMIT", 5000),
    )
