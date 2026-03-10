from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Iterator

from sqlalchemy import MetaData, Table, create_engine, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from .config import SymbolAvailabilitySettings


@dataclass(frozen=True)
class SymbolAvailabilityRecord:
    symbol: str
    latest_1m_ts: datetime | None
    latest_5m_ts: datetime | None
    latest_10m_ts: datetime | None
    latest_15m_ts: datetime | None
    latest_30m_ts: datetime | None
    latest_day_ts: datetime | None
    latest_week_ts: datetime | None
    updated_at: datetime | None


class SymbolAvailabilityStore:
    def __init__(self, settings: SymbolAvailabilitySettings) -> None:
        self._settings = settings
        self._engine: Engine = create_engine(settings.database_url, pool_pre_ping=True)
        self._metadata = MetaData(schema=settings.schema_name)
        self._table = Table(
            settings.table_name,
            self._metadata,
            autoload_with=self._engine,
            schema=settings.schema_name,
        )

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = Session(self._engine)
        try:
            yield session
        finally:
            session.close()

    def list_symbol_availability(self) -> list[SymbolAvailabilityRecord]:
        statement = select(
            self._table.c.symbol,
            self._table.c.latest_1m_ts,
            self._table.c.latest_5m_ts,
            self._table.c.latest_10m_ts,
            self._table.c.latest_15m_ts,
            self._table.c.latest_30m_ts,
            self._table.c.latest_day_ts,
            self._table.c.latest_week_ts,
            self._table.c.updated_at,
        ).order_by(self._table.c.symbol)

        with self.session() as session:
            rows = session.execute(statement).mappings().all()

        return [SymbolAvailabilityRecord(**dict(row)) for row in rows]

    def get_symbol(self, symbol: str) -> SymbolAvailabilityRecord | None:
        statement = select(
            self._table.c.symbol,
            self._table.c.latest_1m_ts,
            self._table.c.latest_5m_ts,
            self._table.c.latest_10m_ts,
            self._table.c.latest_15m_ts,
            self._table.c.latest_30m_ts,
            self._table.c.latest_day_ts,
            self._table.c.latest_week_ts,
            self._table.c.updated_at,
        ).where(self._table.c.symbol == symbol)

        with self.session() as session:
            row = session.execute(statement).mappings().first()

        if row is None:
            return None
        return SymbolAvailabilityRecord(**dict(row))

    def add_symbol(self, symbol: str) -> bool:
        statement = pg_insert(self._table).values(symbol=symbol)
        statement = statement.on_conflict_do_nothing(index_elements=[self._table.c.symbol])
        statement = statement.returning(self._table.c.symbol)

        with self.session() as session, session.begin():
            created_symbol = session.execute(statement).scalar_one_or_none()

        return created_symbol is not None