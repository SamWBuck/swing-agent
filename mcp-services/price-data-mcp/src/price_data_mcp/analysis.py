from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

import pandas as pd
from ta import add_all_ta_features
from ta.momentum import (
    RSIIndicator,
    StochasticOscillator,
    StochRSIIndicator,
    TSIIndicator,
    UltimateOscillator,
    WilliamsRIndicator,
)
from ta.trend import ADXIndicator, CCIIndicator, EMAIndicator, MACD, SMAIndicator
from ta.volatility import AverageTrueRange, BollingerBands
from ta.volume import ChaikinMoneyFlowIndicator, MFIIndicator, OnBalanceVolumeIndicator


REQUIRED_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]


@dataclass(frozen=True)
class IndicatorDefinition:
    name: str
    category: str
    outputs: tuple[str, ...]
    minimum_rows: int


INDICATOR_DEFINITIONS: dict[str, IndicatorDefinition] = {
    "rsi": IndicatorDefinition("rsi", "momentum", ("rsi",), 14),
    "stoch": IndicatorDefinition("stoch", "momentum", ("stoch_k", "stoch_d"), 14),
    "stochrsi": IndicatorDefinition("stochrsi", "momentum", ("stochrsi", "stochrsi_k", "stochrsi_d"), 14),
    "tsi": IndicatorDefinition("tsi", "momentum", ("tsi",), 25),
    "ultimate_oscillator": IndicatorDefinition("ultimate_oscillator", "momentum", ("ultimate_oscillator",), 28),
    "williams_r": IndicatorDefinition("williams_r", "momentum", ("williams_r",), 14),
    "adx": IndicatorDefinition("adx", "trend", ("adx", "adx_pos", "adx_neg"), 14),
    "cci": IndicatorDefinition("cci", "trend", ("cci",), 20),
    "ema_20": IndicatorDefinition("ema_20", "trend", ("ema_20",), 20),
    "sma_20": IndicatorDefinition("sma_20", "trend", ("sma_20",), 20),
    "macd": IndicatorDefinition("macd", "trend", ("macd", "macd_signal", "macd_diff"), 26),
    "atr": IndicatorDefinition("atr", "volatility", ("atr",), 14),
    "bollinger": IndicatorDefinition(
        "bollinger",
        "volatility",
        (
            "bollinger_mavg",
            "bollinger_hband",
            "bollinger_lband",
            "bollinger_wband",
            "bollinger_pband",
        ),
        20,
    ),
    "cmf": IndicatorDefinition("cmf", "volume", ("cmf",), 20),
    "mfi": IndicatorDefinition("mfi", "volume", ("mfi",), 14),
    "obv": IndicatorDefinition("obv", "volume", ("obv",), 2),
}


def frame_from_records(records: list[dict[str, Any]]) -> pd.DataFrame:
    frame = pd.DataFrame.from_records(records)
    if frame.empty:
        raise ValueError("No candle data found for the requested filters")

    missing = [column for column in REQUIRED_COLUMNS if column not in frame.columns]
    if missing:
        raise ValueError(f"Candle dataset is missing required columns: {missing}")

    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
    frame = frame.sort_values("timestamp").reset_index(drop=True)
    for column in ["open", "high", "low", "close", "volume"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame


def indicator_catalog() -> dict[str, Any]:
    return {
        "selected_indicators": {
            name: {
                "category": definition.category,
                "outputs": list(definition.outputs),
                "minimum_rows": definition.minimum_rows,
            }
            for name, definition in INDICATOR_DEFINITIONS.items()
        },
        "all_mode": {
            "description": "Adds the full ta.add_all_ta_features output set across momentum, volume, volatility, trend, and others categories.",
            "requires": ["open", "high", "low", "close", "volume"],
        },
    }


def compute_selected_indicators(frame: pd.DataFrame, indicators: Iterable[str]) -> pd.DataFrame:
    output = frame.copy()
    requested = list(dict.fromkeys(indicators))
    unknown = [name for name in requested if name not in INDICATOR_DEFINITIONS]
    if unknown:
        raise ValueError(f"Unsupported indicators requested: {unknown}")

    for name in requested:
        if name == "rsi":
            output["rsi"] = RSIIndicator(close=output["close"]).rsi()
        elif name == "stoch":
            indicator = StochasticOscillator(high=output["high"], low=output["low"], close=output["close"])
            output["stoch_k"] = indicator.stoch()
            output["stoch_d"] = indicator.stoch_signal()
        elif name == "stochrsi":
            indicator = StochRSIIndicator(close=output["close"])
            output["stochrsi"] = indicator.stochrsi()
            output["stochrsi_k"] = indicator.stochrsi_k()
            output["stochrsi_d"] = indicator.stochrsi_d()
        elif name == "tsi":
            output["tsi"] = TSIIndicator(close=output["close"]).tsi()
        elif name == "ultimate_oscillator":
            output["ultimate_oscillator"] = UltimateOscillator(
                high=output["high"],
                low=output["low"],
                close=output["close"],
            ).ultimate_oscillator()
        elif name == "williams_r":
            output["williams_r"] = WilliamsRIndicator(
                high=output["high"],
                low=output["low"],
                close=output["close"],
            ).williams_r()
        elif name == "adx":
            indicator = ADXIndicator(high=output["high"], low=output["low"], close=output["close"])
            output["adx"] = indicator.adx()
            output["adx_pos"] = indicator.adx_pos()
            output["adx_neg"] = indicator.adx_neg()
        elif name == "cci":
            output["cci"] = CCIIndicator(high=output["high"], low=output["low"], close=output["close"]).cci()
        elif name == "ema_20":
            output["ema_20"] = EMAIndicator(close=output["close"], window=20).ema_indicator()
        elif name == "sma_20":
            output["sma_20"] = SMAIndicator(close=output["close"], window=20).sma_indicator()
        elif name == "macd":
            indicator = MACD(close=output["close"])
            output["macd"] = indicator.macd()
            output["macd_signal"] = indicator.macd_signal()
            output["macd_diff"] = indicator.macd_diff()
        elif name == "atr":
            output["atr"] = AverageTrueRange(
                high=output["high"],
                low=output["low"],
                close=output["close"],
            ).average_true_range()
        elif name == "bollinger":
            indicator = BollingerBands(close=output["close"])
            output["bollinger_mavg"] = indicator.bollinger_mavg()
            output["bollinger_hband"] = indicator.bollinger_hband()
            output["bollinger_lband"] = indicator.bollinger_lband()
            output["bollinger_wband"] = indicator.bollinger_wband()
            output["bollinger_pband"] = indicator.bollinger_pband()
        elif name == "cmf":
            output["cmf"] = ChaikinMoneyFlowIndicator(
                high=output["high"],
                low=output["low"],
                close=output["close"],
                volume=output["volume"],
            ).chaikin_money_flow()
        elif name == "mfi":
            output["mfi"] = MFIIndicator(
                high=output["high"],
                low=output["low"],
                close=output["close"],
                volume=output["volume"],
            ).money_flow_index()
        elif name == "obv":
            output["obv"] = OnBalanceVolumeIndicator(
                close=output["close"],
                volume=output["volume"],
            ).on_balance_volume()

    return output


def compute_all_indicators(frame: pd.DataFrame) -> pd.DataFrame:
    output = frame.copy()
    return add_all_ta_features(
        output,
        open="open",
        high="high",
        low="low",
        close="close",
        volume="volume",
        fillna=False,
    )


def serialize_frame(frame: pd.DataFrame, tail: int | None = None) -> list[dict[str, Any]]:
    data = frame.tail(tail) if tail else frame
    serializable = data.copy()
    serializable["timestamp"] = serializable["timestamp"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    serializable = serializable.where(pd.notnull(serializable), None)
    return serializable.to_dict(orient="records")


def calculate_support_resistance(
    frame: pd.DataFrame,
    *,
    lookback: int = 3,
    tolerance_pct: float = 0.005,
    max_levels: int = 5,
) -> dict[str, Any]:
    if lookback < 1:
        raise ValueError("lookback must be at least 1")

    highs = frame["high"]
    lows = frame["low"]
    closes = frame["close"]
    latest_close = float(closes.iloc[-1])

    pivot_highs = frame[
        highs.eq(highs.rolling(window=lookback * 2 + 1, center=True, min_periods=lookback).max())
    ][["timestamp", "high"]].rename(columns={"high": "price"})
    pivot_highs["kind"] = "resistance"

    pivot_lows = frame[
        lows.eq(lows.rolling(window=lookback * 2 + 1, center=True, min_periods=lookback).min())
    ][["timestamp", "low"]].rename(columns={"low": "price"})
    pivot_lows["kind"] = "support"

    pivots = pd.concat([pivot_highs, pivot_lows], ignore_index=True)
    if pivots.empty:
        return {
            "latest_close": latest_close,
            "supports": [],
            "resistances": [],
            "message": "Not enough pivot structure found for the requested window",
        }

    levels: list[dict[str, Any]] = []
    for record in pivots.sort_values("timestamp").to_dict(orient="records"):
        price = float(record["price"])
        matched = None
        for level in levels:
            if level["kind"] != record["kind"]:
                continue
            if abs(level["price"] - price) / max(price, 1e-9) <= tolerance_pct:
                matched = level
                break
        if matched is None:
            levels.append(
                {
                    "kind": record["kind"],
                    "price": price,
                    "touches": 1,
                    "first_seen": record["timestamp"],
                    "last_seen": record["timestamp"],
                }
            )
        else:
            touches = matched["touches"] + 1
            matched["price"] = (matched["price"] * matched["touches"] + price) / touches
            matched["touches"] = touches
            matched["last_seen"] = record["timestamp"]

    supports = sorted(
        [level for level in levels if level["kind"] == "support" and level["price"] <= latest_close],
        key=lambda item: (item["touches"], item["price"]),
        reverse=True,
    )[:max_levels]
    resistances = sorted(
        [level for level in levels if level["kind"] == "resistance" and level["price"] >= latest_close],
        key=lambda item: (item["touches"], -item["price"]),
        reverse=True,
    )[:max_levels]

    for group in (supports, resistances):
        for level in group:
            level["distance_pct_from_close"] = (level["price"] - latest_close) / latest_close
            level["first_seen"] = pd.Timestamp(level["first_seen"]).strftime("%Y-%m-%dT%H:%M:%SZ")
            level["last_seen"] = pd.Timestamp(level["last_seen"]).strftime("%Y-%m-%dT%H:%M:%SZ")

    return {
        "latest_close": latest_close,
        "supports": supports,
        "resistances": resistances,
        "parameters": {
            "lookback": lookback,
            "tolerance_pct": tolerance_pct,
            "max_levels": max_levels,
        },
    }
