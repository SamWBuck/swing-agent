from __future__ import annotations

import os
from dataclasses import dataclass

from sqlalchemy import URL

from .env_helpers import env_int, optional_env


def build_database_url(*, consumer_name: str = "service") -> str:
    direct_url = os.getenv("DATABASE_URL")
    if direct_url:
        return direct_url

    required_names = ["DB_USER", "DB_PASSWORD", "DB_HOST", "DB_PORT", "DB_NAME"]
    values = {name: os.getenv(name) for name in required_names}
    missing = [name for name, value in values.items() if value is None or value == ""]
    if missing:
        raise RuntimeError(
            f"Missing database environment variables for {consumer_name}: {missing}. "
            "Set DB_USER, DB_PASSWORD, DB_HOST, DB_PORT, and DB_NAME or provide DATABASE_URL."
        )

    query: dict[str, str] = {}
    sslmode = optional_env("DB_SSLMODE")
    sslrootcert = optional_env("DB_SSLROOTCERT")
    sslcert = optional_env("DB_SSLCERT")
    sslkey = optional_env("DB_SSLKEY")

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
class PriceStoreSettings:
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


@dataclass(frozen=True)
class SymbolAvailabilitySettings:
    database_url: str
    schema_name: str
    table_name: str


@dataclass(frozen=True)
class PortfolioStoreSettings:
    database_url: str
    schema_name: str
    users_table_name: str
    portfolios_table_name: str
    positions_table_name: str
    position_legs_table_name: str
    trade_events_table_name: str


@dataclass(frozen=True)
class AutomationStoreSettings:
    database_url: str
    schema_name: str
    runs_table_name: str
    accounts_table_name: str
    positions_table_name: str
    decisions_table_name: str
    action_intents_table_name: str


def load_price_store_settings(*, consumer_name: str = "price-data-mcp") -> PriceStoreSettings:
    return PriceStoreSettings(
        database_url=build_database_url(consumer_name=consumer_name),
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
        default_limit=env_int("PRICE_DATA_DEFAULT_LIMIT", 200),
        max_limit=env_int("PRICE_DATA_MAX_LIMIT", 5000),
    )


def load_symbol_availability_settings(*, consumer_name: str = "service") -> SymbolAvailabilitySettings:
    return SymbolAvailabilitySettings(
        database_url=build_database_url(consumer_name=consumer_name),
        schema_name=os.getenv("SYMBOL_AVAILABILITY_SCHEMA", "public"),
        table_name=os.getenv("SYMBOL_AVAILABILITY_TABLE", "symbol_availability"),
    )


def load_portfolio_store_settings(*, consumer_name: str = "service") -> PortfolioStoreSettings:
    return PortfolioStoreSettings(
        database_url=build_database_url(consumer_name=consumer_name),
        schema_name=os.getenv("PORTFOLIO_SCHEMA", "public"),
        users_table_name=os.getenv("PORTFOLIO_USERS_TABLE", "users"),
        portfolios_table_name=os.getenv("PORTFOLIO_PORTFOLIOS_TABLE", "portfolios"),
        positions_table_name=os.getenv("PORTFOLIO_POSITIONS_TABLE", "positions"),
        position_legs_table_name=os.getenv("PORTFOLIO_POSITION_LEGS_TABLE", "position_legs"),
        trade_events_table_name=os.getenv("PORTFOLIO_TRADE_EVENTS_TABLE", "trade_events"),
    )


def load_automation_store_settings(*, consumer_name: str = "service") -> AutomationStoreSettings:
    return AutomationStoreSettings(
        database_url=build_database_url(consumer_name=consumer_name),
        schema_name=os.getenv("AUTOMATION_SCHEMA", "public"),
        runs_table_name=os.getenv("AUTOMATION_RUNS_TABLE", "automation_runs"),
        accounts_table_name=os.getenv("AUTOMATION_ACCOUNTS_TABLE", "broker_accounts"),
        positions_table_name=os.getenv("AUTOMATION_POSITIONS_TABLE", "broker_positions"),
        decisions_table_name=os.getenv("AUTOMATION_DECISIONS_TABLE", "automation_decisions"),
        action_intents_table_name=os.getenv("AUTOMATION_ACTION_INTENTS_TABLE", "automation_action_intents"),
    )