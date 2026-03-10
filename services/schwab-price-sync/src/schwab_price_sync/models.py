from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class SymbolAvailability:
    symbol: str
    latest_1m_ts: datetime | None
    latest_5m_ts: datetime | None
    latest_10m_ts: datetime | None
    latest_15m_ts: datetime | None
    latest_30m_ts: datetime | None
    latest_day_ts: datetime | None
    latest_week_ts: datetime | None


@dataclass(frozen=True)
class CandleRecord:
    symbol: str
    interval: str
    ts: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


@dataclass
class UpsertResult:
    fetched: int = 0
    written: int = 0


@dataclass
class SyncResult:
    symbols_processed: int = 0
    intervals_processed: int = 0
    candles_fetched: int = 0
    candles_written: int = 0