from __future__ import annotations

from collections.abc import Iterable, Iterator
from contextlib import contextmanager

from sqlalchemy import MetaData, Table, create_engine, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from .config import Settings
from .models import CandleRecord, SymbolAvailability


class Repository:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._engine: Engine = create_engine(settings.database_url, pool_pre_ping=True)
        self._metadata = MetaData()
        self._price_candles = Table(
            settings.candles_table,
            self._metadata,
            autoload_with=self._engine,
            schema=settings.candles_schema,
        )
        self._symbol_availability = Table(
            settings.availability_table,
            self._metadata,
            autoload_with=self._engine,
            schema=settings.availability_schema,
        )

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = Session(self._engine)
        try:
            yield session
        finally:
            session.close()

    def list_symbol_availability(
        self,
        *,
        symbols: list[str] | None = None,
        limit: int | None = None,
        missing_intervals: list[str] | None = None,
    ) -> list[SymbolAvailability]:
        statement = select(
            self._symbol_availability.c.symbol,
            self._symbol_availability.c.latest_1m_ts,
            self._symbol_availability.c.latest_5m_ts,
            self._symbol_availability.c.latest_10m_ts,
            self._symbol_availability.c.latest_15m_ts,
            self._symbol_availability.c.latest_30m_ts,
            self._symbol_availability.c.latest_day_ts,
            self._symbol_availability.c.latest_week_ts,
        ).order_by(self._symbol_availability.c.symbol)

        if symbols:
            statement = statement.where(self._symbol_availability.c.symbol.in_(symbols))
        if missing_intervals:
            interval_columns = {
                "1m": self._symbol_availability.c.latest_1m_ts,
                "5m": self._symbol_availability.c.latest_5m_ts,
                "10m": self._symbol_availability.c.latest_10m_ts,
                "15m": self._symbol_availability.c.latest_15m_ts,
                "30m": self._symbol_availability.c.latest_30m_ts,
                "1d": self._symbol_availability.c.latest_day_ts,
                "1w": self._symbol_availability.c.latest_week_ts,
            }
            statement = statement.where(
                or_(
                    *[
                        interval_columns[interval].is_(None)
                        for interval in missing_intervals
                    ]
                )
            )
        if limit is not None:
            statement = statement.limit(limit)

        with self.session() as session:
            rows = session.execute(statement).mappings().all()

        return [SymbolAvailability(**dict(row)) for row in rows]

    def upsert_candles(self, candles: Iterable[CandleRecord]) -> int:
        payload = [
            {
                "symbol": candle.symbol,
                "interval": candle.interval,
                "ts": candle.ts,
                "open": candle.open,
                "high": candle.high,
                "low": candle.low,
                "close": candle.close,
                "volume": candle.volume,
            }
            for candle in candles
        ]
        if not payload:
            return 0

        statement = pg_insert(self._price_candles).values(payload)
        excluded = statement.excluded
        update_map = {
            "open": excluded.open,
            "high": excluded.high,
            "low": excluded.low,
            "close": excluded.close,
            "volume": excluded.volume,
        }
        changed = (
            self._price_candles.c.open != excluded.open
            ) | (
            self._price_candles.c.high != excluded.high
            ) | (
            self._price_candles.c.low != excluded.low
            ) | (
            self._price_candles.c.close != excluded.close
            ) | (
            self._price_candles.c.volume != excluded.volume
        )

        statement = statement.on_conflict_do_update(
            index_elements=[
                self._price_candles.c.symbol,
                self._price_candles.c.interval,
                self._price_candles.c.ts,
            ],
            set_=update_map,
            where=changed,
        )
        statement = statement.returning(
            self._price_candles.c.symbol,
            self._price_candles.c.interval,
            self._price_candles.c.ts,
        )

        with self.session() as session, session.begin():
            result = session.execute(statement)
            return len(result.fetchall())