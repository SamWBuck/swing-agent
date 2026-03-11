from __future__ import annotations

from datetime import datetime
import json
import logging
import os
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP
from swing_agent_database import PriceStore, load_price_store_settings

try:
    from .analysis import (
        calculate_support_resistance,
        compute_all_indicators,
        compute_selected_indicators,
        frame_from_records,
        indicator_catalog,
        serialize_frame,
    )
except ImportError:
    from price_data_mcp.analysis import (
        calculate_support_resistance,
        compute_all_indicators,
        compute_selected_indicators,
        frame_from_records,
        indicator_catalog,
        serialize_frame,
    )


settings = load_price_store_settings()
store = PriceStore(settings)
mcp = FastMCP("price-data-mcp")
log = logging.getLogger(__name__)


def _trace_tool_call(tool_name: str, **kwargs: Any) -> None:
    payload = {"tool": tool_name, "args": kwargs}
    message = f"MCP_TOOL_CALL {json.dumps(payload, default=str, sort_keys=True)}"
    print(message, flush=True)
    log.info(message)


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
    _trace_tool_call("describe_price_source")
    return store.describe_source()


@mcp.tool()
def list_symbols(interval: str | None = None, limit: int = 500) -> dict[str, Any]:
    """List symbols available in the candle store, optionally filtered by interval."""
    _trace_tool_call("list_symbols", interval=interval, limit=limit)
    return {
        "interval": interval,
        "symbols": store.list_symbols(interval=interval, limit=limit),
    }


@mcp.tool()
def list_intervals(symbol: str | None = None, limit: int = 100) -> dict[str, Any]:
    """List intervals available in the candle store, optionally filtered by symbol."""
    _trace_tool_call("list_intervals", symbol=symbol, limit=limit)
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
    _trace_tool_call(
        "get_raw_candles",
        symbol=symbol,
        interval=interval,
        start=start,
        end=end,
        limit=limit,
        ascending=ascending,
    )
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
    _trace_tool_call("get_indicator_catalog")
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
    _trace_tool_call(
        "calculate_indicators",
        symbol=symbol,
        interval=interval,
        mode=mode,
        indicators=indicators,
        start=start,
        end=end,
        limit=limit,
        tail=tail,
    )
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
        requested = indicators or ["rsi", "macd", "atr", "atr_pct", "bollinger", "obv", "hv_20", "return_20d"]
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
    _trace_tool_call(
        "get_support_resistance",
        symbol=symbol,
        interval=interval,
        start=start,
        end=end,
        limit=limit,
        pivot_lookback=pivot_lookback,
        tolerance_pct=tolerance_pct,
        max_levels=max_levels,
    )
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
    _trace_tool_call(
        "summarize_market_data",
        symbol=symbol,
        interval=interval,
        start=start,
        end=end,
        limit=limit,
    )
    candles = store.fetch_candles(
        symbol=symbol,
        interval=interval,
        start=_parse_datetime(start),
        end=_parse_datetime(end),
        limit=_effective_limit(limit),
        ascending=True,
    )
    frame = frame_from_records(candles)
    enriched = compute_selected_indicators(
        frame,
        ["rsi", "macd", "atr", "atr_pct", "adx", "bollinger", "mfi", "obv", "hv_20", "return_20d", "cmf"],
    )
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
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    transport = os.getenv("MCP_TRANSPORT", "streamable-http")
    log.info("Starting price-data MCP server transport=%s", transport)
    mcp.run(transport=transport)


if __name__ == "__main__":
    main()
