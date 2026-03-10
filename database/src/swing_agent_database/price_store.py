from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterator

from sqlalchemy import MetaData, Table, asc, create_engine, desc, distinct, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from .config import PriceStoreSettings


@dataclass(frozen=True)
class CandleColumns:
    symbol: str
    interval: str
    timestamp: str
    open: str
    high: str
    low: str
    close: str
    volume: str


class PriceStore:
    def __init__(self, settings: PriceStoreSettings) -> None:
        self._settings = settings
        self._engine: Engine = create_engine(settings.database_url, pool_pre_ping=True)
        self._metadata = MetaData(schema=settings.schema_name)
        self._table = Table(
            settings.table_name,
            self._metadata,
            autoload_with=self._engine,
            schema=settings.schema_name,
        )
        self._columns = CandleColumns(
            symbol=settings.symbol_column,
            interval=settings.interval_column,
            timestamp=settings.timestamp_column,
            open=settings.open_column,
            high=settings.high_column,
            low=settings.low_column,
            close=settings.close_column,
            volume=settings.volume_column,
        )
        self._validate_columns()

    @property
    def table(self) -> Table:
        return self._table

    @property
    def columns(self) -> CandleColumns:
        return self._columns

    @property
    def settings(self) -> PriceStoreSettings:
        return self._settings

    def _validate_columns(self) -> None:
        missing = [name for name in self._columns.__dict__.values() if name not in self._table.c]
        if missing:
            raise RuntimeError(
                f"Configured candle columns were not found in "
                f"{self._settings.schema_name}.{self._settings.table_name}: {missing}"
            )

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = Session(self._engine)
        try:
            yield session
        finally:
            session.close()

    def describe_source(self) -> dict[str, Any]:
        return {
            "schema": self._settings.schema_name,
            "table": self._settings.table_name,
            "mapped_columns": self._columns.__dict__,
            "available_columns": [column.name for column in self._table.columns],
        }

    def list_symbols(self, interval: str | None = None, limit: int = 500) -> list[str]:
        symbol_column = self._table.c[self._columns.symbol]
        statement = select(distinct(symbol_column)).order_by(symbol_column).limit(limit)
        if interval:
            statement = statement.where(self._table.c[self._columns.interval] == interval)

        with self.session() as session:
            return [value for value in session.execute(statement).scalars() if value is not None]

    def list_intervals(self, symbol: str | None = None, limit: int = 100) -> list[str]:
        interval_column = self._table.c[self._columns.interval]
        statement = select(distinct(interval_column)).order_by(interval_column).limit(limit)
        if symbol:
            statement = statement.where(self._table.c[self._columns.symbol] == symbol)

        with self.session() as session:
            return [value for value in session.execute(statement).scalars() if value is not None]

    def fetch_candles(
        self,
        *,
        symbol: str,
        interval: str,
        start: datetime | None,
        end: datetime | None,
        limit: int,
        ascending: bool,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(limit, self._settings.max_limit))
        timestamp_column = self._table.c[self._columns.timestamp]

        statement = (
            select(
                self._table.c[self._columns.symbol].label("symbol"),
                self._table.c[self._columns.interval].label("interval"),
                timestamp_column.label("timestamp"),
                self._table.c[self._columns.open].label("open"),
                self._table.c[self._columns.high].label("high"),
                self._table.c[self._columns.low].label("low"),
                self._table.c[self._columns.close].label("close"),
                self._table.c[self._columns.volume].label("volume"),
            )
            .where(self._table.c[self._columns.symbol] == symbol)
            .where(self._table.c[self._columns.interval] == interval)
        )

        if start is not None:
            statement = statement.where(timestamp_column >= start)
        if end is not None:
            statement = statement.where(timestamp_column <= end)

        statement = statement.order_by(asc(timestamp_column) if ascending else desc(timestamp_column))
        statement = statement.limit(limit)

        with self.session() as session:
            rows = session.execute(statement).mappings().all()
            records = [dict(row) for row in rows]

        if not ascending:
            records.reverse()
        return records