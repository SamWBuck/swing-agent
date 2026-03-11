from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from schwab.orders.common import (
    ComplexOrderStrategyType,
    Duration,
    OptionInstruction,
    OrderStrategyType,
    OrderType,
    Session,
)
from schwab.orders.generic import OrderBuilder
from schwab.orders.options import (
    OptionSymbol,
    option_buy_to_close_limit,
    option_sell_to_close_limit,
    option_sell_to_open_limit,
)
from schwab.utils import Utils

from .llm_actions import MatchedPosition, ValidatedAction


@dataclass(frozen=True)
class ExecutionResult:
    execution_status: str
    schwab_order_id: str | None
    message: str


def _format_strike(value: Decimal) -> str:
    normalized = value.quantize(Decimal("0.001"))
    return format(normalized.normalize(), "f")


def _abs_price(value: Decimal) -> str:
    normalized = abs(value).quantize(Decimal("0.0001")).normalize()
    return format(normalized, "f")


def _roll_order_type(price: Decimal) -> OrderType:
    if price > 0:
        return OrderType.NET_CREDIT
    if price < 0:
        return OrderType.NET_DEBIT
    return OrderType.NET_ZERO


def _roll_strategy_type(validated: ValidatedAction) -> ComplexOrderStrategyType:
    action = validated.proposed
    if action.current_expiration == action.target_expiration and action.current_strike != action.target_strike:
        return ComplexOrderStrategyType.VERTICAL_ROLL
    if action.current_strike == action.target_strike and action.current_expiration != action.target_expiration:
        return ComplexOrderStrategyType.CALENDAR
    if action.current_expiration != action.target_expiration and action.current_strike != action.target_strike:
        return ComplexOrderStrategyType.DIAGONAL
    return ComplexOrderStrategyType.CUSTOM


def _matched_option_symbol(matched_position: MatchedPosition) -> str | None:
    raw_payload = matched_position.raw_payload or {}
    instrument = raw_payload.get("instrument") or {}
    symbol = instrument.get("symbol")
    if symbol:
        return str(symbol)
    return None


async def resolve_option_contract_symbol(client: Any, *, symbol: str, option_type: str, expiration: datetime | None, strike: Decimal) -> str:
    if expiration is None:
        raise ValueError("expiration is required for option contract resolution")
    contract_type = client.Options.ContractType.CALL if option_type == "CALL" else client.Options.ContractType.PUT
    response = await client.get_option_chain(
        symbol,
        contract_type=contract_type,
        strike=strike,
        from_date=expiration.date(),
        to_date=expiration.date(),
    )
    response.raise_for_status()
    payload = response.json()
    exp_map_name = "callExpDateMap" if option_type == "CALL" else "putExpDateMap"
    exp_map = payload.get(exp_map_name) or {}
    expiration_prefix = expiration.date().isoformat()
    strike_key = str(float(strike))
    for exp_key, strike_map in exp_map.items():
        if not str(exp_key).startswith(expiration_prefix):
            continue
        if strike_key not in strike_map:
            continue
        contracts = strike_map[strike_key]
        if contracts:
            return contracts[0]["symbol"]
    return OptionSymbol(symbol, expiration.date(), option_type, _format_strike(strike)).build()


async def build_order_spec(client: Any, validated: ValidatedAction) -> dict:
    """Translate a validated automation action into a Schwab order specification."""
    action = validated.proposed
    option_expiration = None if action.expiration is None else datetime(action.expiration.year, action.expiration.month, action.expiration.day, tzinfo=UTC)
    contract_symbol = None
    if action.action_type in {"sell_covered_call", "sell_cash_secured_put", "close_option"}:
        contract_symbol = await resolve_option_contract_symbol(
            client,
            symbol=action.symbol or "",
            option_type=action.option_type or "CALL",
            expiration=option_expiration,
            strike=action.strike or Decimal("0"),
        )

    if action.action_type in {"sell_covered_call", "sell_cash_secured_put"}:
        builder = option_sell_to_open_limit(contract_symbol, action.quantity or 0, float(action.limit_price or 0))
        return builder.build()

    if action.action_type == "close_option":
        matched = validated.matched_position
        long_quantity = Decimal("0") if matched is None else matched.long_quantity
        if long_quantity > 0:
            builder = option_sell_to_close_limit(contract_symbol, action.quantity or 0, float(action.limit_price or 0))
        else:
            builder = option_buy_to_close_limit(contract_symbol, action.quantity or 0, float(action.limit_price or 0))
        return builder.build()

    if action.action_type == "roll_option":
        matched = validated.matched_position
        current_symbol = None if matched is None else _matched_option_symbol(matched)
        if current_symbol is None:
            current_expiration = action.current_expiration
            current_strike = action.current_strike
            if current_expiration is None or current_strike is None:
                raise ValueError("Current contract details are required to build a roll order")
            current_symbol = await resolve_option_contract_symbol(
                client,
                symbol=action.symbol or "",
                option_type=action.option_type or "CALL",
                expiration=datetime(current_expiration.year, current_expiration.month, current_expiration.day, tzinfo=UTC),
                strike=current_strike,
            )

        target_expiration = action.target_expiration
        target_strike = action.target_strike
        if target_expiration is None or target_strike is None or action.limit_price is None:
            raise ValueError("Target contract details and limit_price are required to build a roll order")
        target_symbol = await resolve_option_contract_symbol(
            client,
            symbol=action.symbol or "",
            option_type=action.option_type or "CALL",
            expiration=datetime(target_expiration.year, target_expiration.month, target_expiration.day, tzinfo=UTC),
            strike=target_strike,
        )

        builder = (
            OrderBuilder()
            .set_session(Session.NORMAL)
            .set_duration(Duration.DAY)
            .set_order_type(_roll_order_type(action.limit_price))
            .set_complex_order_strategy_type(_roll_strategy_type(validated))
            .set_price(_abs_price(action.limit_price))
            .set_quantity(action.quantity or 0)
            .set_order_strategy_type(OrderStrategyType.SINGLE)
            .add_option_leg(OptionInstruction.BUY_TO_CLOSE, current_symbol, action.quantity or 0)
            .add_option_leg(OptionInstruction.SELL_TO_OPEN, target_symbol, action.quantity or 0)
        )
        return builder.build()

    raise ValueError(f"Action type {action.action_type} is not executable")


async def execute_action(client: Any, *, account_hash: str, validated: ValidatedAction) -> ExecutionResult:
    """Submit a validated order to Schwab and normalize the execution result."""
    if not validated.execution_supported or validated.validation_status != "valid":
        return ExecutionResult(execution_status="skipped", schwab_order_id=None, message="Action is not executable")

    order_spec = await build_order_spec(client, validated)
    response = await client.place_order(account_hash, order_spec)
    response.raise_for_status()
    order_id = Utils(client, account_hash).extract_order_id(response)
    return ExecutionResult(
        execution_status="submitted",
        schwab_order_id=None if order_id is None else str(order_id),
        message="Order submitted",
    )