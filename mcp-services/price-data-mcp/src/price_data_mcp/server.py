from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP

try:
    from .analysis import (
        calculate_support_resistance,
        compute_all_indicators,
        compute_selected_indicators,
        frame_from_records,
        indicator_catalog,
        serialize_frame,
    )
    from .config import load_settings
    from .db import PriceStore
except ImportError:
    from price_data_mcp.analysis import (
        calculate_support_resistance,
        compute_all_indicators,
        compute_selected_indicators,
        frame_from_records,
        indicator_catalog,
        serialize_frame,
    )
    from price_data_mcp.config import load_settings
    from price_data_mcp.db import PriceStore


settings = load_settings()
store = PriceStore(settings)
mcp = FastMCP("price-data-mcp")


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized)


def _effective_limit(limit: int | None) -> int:
    if limit is None:
        return settings.default_limit
    return max(1, min(limit, settings.max_limit))


@mcp.tool()
def describe_price_source() -> dict[str, Any]:
    """Describe the configured candle table and mapped OHLCV columns."""
    return store.describe_source()


@mcp.tool()
def list_symbols(interval: str | None = None, limit: int = 500) -> dict[str, Any]:
    """List symbols available in the candle store, optionally filtered by interval."""
    return {
        "interval": interval,
        "symbols": store.list_symbols(interval=interval, limit=limit),
    }


@mcp.tool()
def list_intervals(symbol: str | None = None, limit: int = 100) -> dict[str, Any]:
    """List intervals available in the candle store, optionally filtered by symbol."""
    return {
        "symbol": symbol,
        "intervals": store.list_intervals(symbol=symbol, limit=limit),
    }


@mcp.tool()
def get_raw_candles(
    symbol: str,
    interval: str,
    start: str | None = None,
    end: str | None = None,
    limit: int | None = None,
    ascending: bool = True,
) -> dict[str, Any]:
    """Fetch raw OHLCV candles from Postgres with symbol, interval, and time filtering."""
    candles = store.fetch_candles(
        symbol=symbol,
        interval=interval,
        start=_parse_datetime(start),
        end=_parse_datetime(end),
        limit=_effective_limit(limit),
        ascending=ascending,
    )
    return {
        "symbol": symbol,
        "interval": interval,
        "count": len(candles),
        "candles": candles,
    }


@mcp.tool()
def get_indicator_catalog() -> dict[str, Any]:
    """Return the supported indicator catalog and explain all-feature mode."""
    return indicator_catalog()


@mcp.tool()
def calculate_indicators(
    symbol: str,
    interval: str,
    indicators: list[str] | None = None,
    mode: Literal["selected", "all"] = "selected",
    start: str | None = None,
    end: str | None = None,
    limit: int | None = None,
    tail: int = 50,
) -> dict[str, Any]:
    """Calculate technical indicators over stored candles using the ta library."""
    candles = store.fetch_candles(
        symbol=symbol,
        interval=interval,
        start=_parse_datetime(start),
        end=_parse_datetime(end),
        limit=_effective_limit(limit),
        ascending=True,
    )
    frame = frame_from_records(candles)

    if mode == "all":
        enriched = compute_all_indicators(frame)
        selected = [column for column in enriched.columns if column not in frame.columns]
    else:
        requested = indicators or ["rsi", "macd", "atr", "bollinger", "obv"]
        enriched = compute_selected_indicators(frame, requested)
        selected = [column for column in enriched.columns if column not in frame.columns]

    return {
        "symbol": symbol,
        "interval": interval,
        "mode": mode,
        "rows": len(enriched),
        "indicator_columns": selected,
        "data": serialize_frame(enriched, tail=tail),
    }


@mcp.tool()
def get_support_resistance(
    symbol: str,
    interval: str,
    start: str | None = None,
    end: str | None = None,
    limit: int | None = None,
    pivot_lookback: int = 3,
    tolerance_pct: float = 0.005,
    max_levels: int = 5,
) -> dict[str, Any]:
    """Estimate support and resistance levels from swing highs and lows in candle data."""
    candles = store.fetch_candles(
        symbol=symbol,
        interval=interval,
        start=_parse_datetime(start),
        end=_parse_datetime(end),
        limit=_effective_limit(limit),
        ascending=True,
    )
    frame = frame_from_records(candles)
    analysis = calculate_support_resistance(
        frame,
        lookback=pivot_lookback,
        tolerance_pct=tolerance_pct,
        max_levels=max_levels,
    )
    return {
        "symbol": symbol,
        "interval": interval,
        **analysis,
    }


@mcp.tool()
def summarize_market_data(
    symbol: str,
    interval: str,
    start: str | None = None,
    end: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Return a compact technical snapshot combining candles, indicators, and support/resistance."""
    candles = store.fetch_candles(
        symbol=symbol,
        interval=interval,
        start=_parse_datetime(start),
        end=_parse_datetime(end),
        limit=_effective_limit(limit),
        ascending=True,
    )
    frame = frame_from_records(candles)
    enriched = compute_selected_indicators(frame, ["rsi", "macd", "atr", "adx", "bollinger", "mfi", "obv"])
    latest = serialize_frame(enriched, tail=1)[0]
    levels = calculate_support_resistance(frame, lookback=3, tolerance_pct=0.005, max_levels=3)

    return {
        "symbol": symbol,
        "interval": interval,
        "rows": len(enriched),
        "latest": latest,
        "support_resistance": levels,
    }


def main() -> None:
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
