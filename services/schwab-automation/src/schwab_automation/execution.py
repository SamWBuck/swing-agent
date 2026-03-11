from __future__ import annotations

import logging
from datetime import date
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
    option_sell_to_open_limit,
)
from schwab.utils import Utils

log = logging.getLogger(__name__)


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


def _roll_strategy_type(
    current_expiration: date | None,
    current_strike: Decimal | None,
    target_expiration: date | None,
    target_strike: Decimal | None,
) -> ComplexOrderStrategyType:
    if current_expiration == target_expiration and current_strike != target_strike:
        return ComplexOrderStrategyType.VERTICAL_ROLL
    if current_strike == target_strike and current_expiration != target_expiration:
        return ComplexOrderStrategyType.CALENDAR
    if current_expiration != target_expiration and current_strike != target_strike:
        return ComplexOrderStrategyType.DIAGONAL
    return ComplexOrderStrategyType.CUSTOM


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _parse_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


async def resolve_option_contract_symbol(
    client: Any,
    *,
    symbol: str,
    option_type: str,
    expiration: date,
    strike: Decimal,
) -> str:
    contract_type = client.Options.ContractType.CALL if option_type == "CALL" else client.Options.ContractType.PUT
    response = await client.get_option_chain(
        symbol,
        contract_type=contract_type,
        strike=strike,
        from_date=expiration,
        to_date=expiration,
    )
    response.raise_for_status()
    payload = response.json()
    exp_map_name = "callExpDateMap" if option_type == "CALL" else "putExpDateMap"
    exp_map = payload.get(exp_map_name) or {}
    expiration_prefix = expiration.isoformat()
    strike_key = str(float(strike))
    for exp_key, strike_map in exp_map.items():
        if not str(exp_key).startswith(expiration_prefix):
            continue
        if strike_key not in strike_map:
            continue
        contracts = strike_map[strike_key]
        if contracts:
            return contracts[0]["symbol"]
    return OptionSymbol(symbol, expiration, option_type, _format_strike(strike)).build()


async def execute_action(client: Any, *, account_hash: str, action: dict) -> str | None:
    """Submit a raw LLM action dict to Schwab. Returns the Schwab order ID or None."""
    action_type = action.get("action_type", "")
    symbol = action.get("symbol") or ""
    option_type = (action.get("option_type") or "CALL").upper()
    quantity = int(action.get("quantity") or 1)
    limit_price = _parse_decimal(action.get("limit_price"))
    expiration = _parse_date(action.get("expiration"))
    strike = _parse_decimal(action.get("strike"))

    if action_type in {"sell_covered_call", "sell_cash_secured_put"}:
        if expiration is None or strike is None or limit_price is None:
            raise ValueError(
                f"Missing required fields for {action_type}: "
                f"expiration={expiration} strike={strike} limit_price={limit_price}"
            )
        contract_symbol = await resolve_option_contract_symbol(
            client, symbol=symbol, option_type=option_type, expiration=expiration, strike=strike,
        )
        order_spec = option_sell_to_open_limit(contract_symbol, quantity, float(limit_price)).build()

    elif action_type == "close_option":
        if expiration is None or strike is None or limit_price is None:
            raise ValueError(
                f"Missing required fields for close_option: "
                f"expiration={expiration} strike={strike} limit_price={limit_price}"
            )
        contract_symbol = await resolve_option_contract_symbol(
            client, symbol=symbol, option_type=option_type, expiration=expiration, strike=strike,
        )
        # Options income positions are always short; always buy to close
        order_spec = option_buy_to_close_limit(contract_symbol, quantity, float(limit_price)).build()

    elif action_type == "roll_option":
        current_expiration = _parse_date(action.get("current_expiration"))
        current_strike = _parse_decimal(action.get("current_strike"))
        target_expiration = _parse_date(action.get("target_expiration"))
        target_strike = _parse_decimal(action.get("target_strike"))
        if any(v is None for v in [current_expiration, current_strike, target_expiration, target_strike, limit_price]):
            raise ValueError(
                "Missing required fields for roll_option: "
                f"current_expiration={current_expiration} current_strike={current_strike} "
                f"target_expiration={target_expiration} target_strike={target_strike} limit_price={limit_price}"
            )
        current_symbol = await resolve_option_contract_symbol(
            client, symbol=symbol, option_type=option_type, expiration=current_expiration, strike=current_strike,
        )
        target_symbol = await resolve_option_contract_symbol(
            client, symbol=symbol, option_type=option_type, expiration=target_expiration, strike=target_strike,
        )
        builder = (
            OrderBuilder()
            .set_session(Session.NORMAL)
            .set_duration(Duration.DAY)
            .set_order_type(_roll_order_type(limit_price))
            .set_complex_order_strategy_type(
                _roll_strategy_type(current_expiration, current_strike, target_expiration, target_strike)
            )
            .set_price(_abs_price(limit_price))
            .set_quantity(quantity)
            .set_order_strategy_type(OrderStrategyType.SINGLE)
            .add_option_leg(OptionInstruction.BUY_TO_CLOSE, current_symbol, quantity)
            .add_option_leg(OptionInstruction.SELL_TO_OPEN, target_symbol, quantity)
        )
        order_spec = builder.build()

    else:
        # hold, skip, and unrecognised action types are not executable
        return None

    response = await client.place_order(account_hash, order_spec)
    response.raise_for_status()
    order_id = Utils(client, account_hash).extract_order_id(response)
    log.info("Submitted %s for %s: order_id=%s", action_type, symbol, order_id)
    return None if order_id is None else str(order_id)