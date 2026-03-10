from __future__ import annotations

import asyncio
import calendar
import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx

from .config import Settings
from .models import CandleRecord, SyncResult, SymbolAvailability
from .repository import Repository
from .schwab_client import create_async_client


log = logging.getLogger(__name__)


@dataclass(frozen=True)
class IntervalSpec:
    interval: str
    latest_attr: str
    method_name: str
    bootstrap_days: int | None = None
    bootstrap_months: int | None = None
    chunk_days: int | None = None


INTERVAL_SPECS: dict[str, IntervalSpec] = {
    "1m": IntervalSpec("1m", "latest_1m_ts", "get_price_history_every_minute", bootstrap_days=30, chunk_days=1),
    "5m": IntervalSpec("5m", "latest_5m_ts", "get_price_history_every_five_minutes", bootstrap_months=6),
    "10m": IntervalSpec("10m", "latest_10m_ts", "get_price_history_every_ten_minutes", bootstrap_months=6),
    "15m": IntervalSpec("15m", "latest_15m_ts", "get_price_history_every_fifteen_minutes", bootstrap_months=6),
    "30m": IntervalSpec("30m", "latest_30m_ts", "get_price_history_every_thirty_minutes", bootstrap_months=6),
    "1d": IntervalSpec("1d", "latest_day_ts", "get_price_history_every_day", bootstrap_months=6),
    "1w": IntervalSpec("1w", "latest_week_ts", "get_price_history_every_week", bootstrap_months=6),
}


INTERVAL_FRESHNESS: dict[str, timedelta] = {
    "1m": timedelta(minutes=1),
    "5m": timedelta(minutes=5),
    "10m": timedelta(minutes=10),
    "15m": timedelta(minutes=15),
    "30m": timedelta(minutes=30),
    "1d": timedelta(days=1),
    "1w": timedelta(weeks=1),
}


def _subtract_months(value: datetime, months: int) -> datetime:
    month = value.month - months
    year = value.year
    while month <= 0:
        month += 12
        year -= 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _determine_windows(spec: IntervalSpec, latest_ts: datetime | None, now: datetime) -> list[tuple[datetime, datetime]]:
    if latest_ts is not None:
        start = _ensure_utc(latest_ts) - timedelta(days=1)
        if start >= now:
            return []
        return [(start, now)]

    if spec.bootstrap_days is not None and spec.chunk_days is not None:
        windows: list[tuple[datetime, datetime]] = []
        start = now - timedelta(days=spec.bootstrap_days)
        current = start
        while current < now:
            window_end = min(current + timedelta(days=spec.chunk_days), now)
            windows.append((current, window_end))
            current = window_end
        return windows

    if spec.bootstrap_months is not None:
        start = _subtract_months(now, spec.bootstrap_months)
        return [(start, now)]

    raise RuntimeError(f"Unsupported interval spec: {spec}")


def _normalize_candle(symbol: str, interval: str, candle: dict[str, Any]) -> CandleRecord:
    raw_timestamp = candle.get("datetime")
    if isinstance(raw_timestamp, (int, float)):
        ts = datetime.fromtimestamp(raw_timestamp / 1000, tz=UTC)
    elif isinstance(raw_timestamp, datetime):
        ts = _ensure_utc(raw_timestamp)
    else:
        raise RuntimeError(f"Unexpected Schwab candle timestamp for {symbol} {interval}: {raw_timestamp!r}")

    return CandleRecord(
        symbol=symbol,
        interval=interval,
        ts=ts,
        open=Decimal(str(candle["open"])),
        high=Decimal(str(candle["high"])),
        low=Decimal(str(candle["low"])),
        close=Decimal(str(candle["close"])),
        volume=Decimal(str(candle["volume"])),
    )


def _dedupe_candles(candles: Iterable[CandleRecord]) -> list[CandleRecord]:
    deduped: dict[tuple[str, str, datetime], CandleRecord] = {}
    for candle in candles:
        deduped[(candle.symbol, candle.interval, candle.ts)] = candle
    return sorted(deduped.values(), key=lambda candle: (candle.symbol, candle.interval, candle.ts))


def _batched(records: Sequence[CandleRecord], batch_size: int) -> Iterable[list[CandleRecord]]:
    for index in range(0, len(records), batch_size):
        yield list(records[index:index + batch_size])


def _should_skip_bootstrap_window_error(
    spec: IntervalSpec,
    latest_ts: datetime | None,
    response: httpx.Response,
) -> bool:
    return latest_ts is None and spec.interval == "1m" and response.status_code == 400


async def _fetch_interval_candles(
    client: Any,
    settings: Settings,
    symbol: str,
    spec: IntervalSpec,
    latest_ts: datetime | None,
    now: datetime,
) -> list[CandleRecord]:
    method = getattr(client, spec.method_name)
    all_candles: list[CandleRecord] = []

    for start, end in _determine_windows(spec, latest_ts, now):
        response = await method(
            symbol,
            start_datetime=start,
            end_datetime=end,
            need_extended_hours_data=settings.need_extended_hours_data,
            need_previous_close=False,
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError:
            if _should_skip_bootstrap_window_error(spec, latest_ts, response):
                log.warning(
                    "Skipping %s %s bootstrap window %s -> %s after Schwab returned %s",
                    symbol,
                    spec.interval,
                    start,
                    end,
                    response.status_code,
                )
                continue
            raise
        payload = response.json()
        raw_candles = payload.get("candles", [])
        all_candles.extend(_normalize_candle(symbol, spec.interval, candle) for candle in raw_candles)

    return _dedupe_candles(all_candles)


async def sync_symbols(
    settings: Settings,
    repository: Repository,
    *,
    symbols: list[str] | None = None,
    intervals: list[str] | None = None,
    limit: int | None = None,
    missing_only: bool = False,
    stale_only: bool = False,
) -> SyncResult:
    selected_specs = [INTERVAL_SPECS[name] for name in (intervals or list(INTERVAL_SPECS))]
    targets = repository.list_symbol_availability(
        symbols=symbols,
        limit=limit,
        missing_intervals=[spec.interval for spec in selected_specs] if missing_only else None,
    )
    result = SyncResult()
    now = datetime.now(tz=UTC)
    client = create_async_client(settings)

    for target in targets:
        result.symbols_processed += 1
        for spec in selected_specs:
            latest_ts = getattr(target, spec.latest_attr)
            if stale_only and latest_ts is not None:
                latest_utc = _ensure_utc(latest_ts)
                if now - latest_utc <= INTERVAL_FRESHNESS[spec.interval]:
                    log.info(
                        "Skipping %s %s: latest=%s is still within freshness window %s",
                        target.symbol,
                        spec.interval,
                        latest_ts,
                        INTERVAL_FRESHNESS[spec.interval],
                    )
                    continue
            try:
                candles = await _fetch_interval_candles(client, settings, target.symbol, spec, latest_ts, now)
            except Exception:
                log.exception("Failed to sync %s %s", target.symbol, spec.interval)
                continue

            if not candles:
                log.info("No candles returned for %s %s", target.symbol, spec.interval)
                result.intervals_processed += 1
                continue

            written = 0
            for batch in _batched(candles, settings.batch_size):
                written += repository.upsert_candles(batch)

            result.intervals_processed += 1
            result.candles_fetched += len(candles)
            result.candles_written += written
            log.info(
                "Synced %s %s: fetched=%d written=%d latest=%s",
                target.symbol,
                spec.interval,
                len(candles),
                written,
                latest_ts,
            )

        await asyncio.sleep(0)

    return result