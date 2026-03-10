from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from .analysis import PortfolioAnalysis, SymbolAnalysis

ALLOWED_ACTION_TYPES = {
    "hold",
    "skip",
    "close_option",
    "roll_option",
    "sell_covered_call",
    "sell_cash_secured_put",
}
ALLOWED_CONFIDENCE = {"low", "medium", "high"}


def _to_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"Invalid decimal value: {value}") from exc


def _to_date(value: Any) -> date | None:
    if value in {None, ""}:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


@dataclass(frozen=True)
class ProposedAction:
    action_type: str
    symbol: str | None
    strategy_type: str | None
    confidence: str
    rationale: list[str]
    quantity: int | None
    option_type: str | None
    expiration: date | None
    strike: Decimal | None
    limit_price: Decimal | None
    related_position_key: str | None
    current_expiration: date | None
    current_strike: Decimal | None
    target_expiration: date | None
    target_strike: Decimal | None
    raw_payload: dict[str, Any]


@dataclass(frozen=True)
class ValidatedAction:
    proposed: ProposedAction
    validation_status: str
    execution_supported: bool
    validation_errors: list[str]
    matched_position: dict[str, Any] | None


def parse_actions(payload: dict[str, Any]) -> list[ProposedAction]:
    raw_actions = payload.get("actions") or []
    if not isinstance(raw_actions, list):
        raise ValueError("actions must be a list")
    actions: list[ProposedAction] = []
    for raw_action in raw_actions:
        if not isinstance(raw_action, dict):
            raise ValueError("each action must be an object")
        action_type = str(raw_action.get("action_type") or "").strip()
        confidence = str(raw_action.get("confidence") or "medium").strip().lower()
        rationale = raw_action.get("rationale") or []
        if not isinstance(rationale, list):
            rationale = [str(rationale)]
        actions.append(
            ProposedAction(
                action_type=action_type,
                symbol=None if raw_action.get("symbol") in {None, ""} else str(raw_action.get("symbol")).upper(),
                strategy_type=None if raw_action.get("strategy_type") in {None, ""} else str(raw_action.get("strategy_type")),
                confidence=confidence,
                rationale=[str(item) for item in rationale],
                quantity=None if raw_action.get("quantity") in {None, ""} else int(raw_action.get("quantity")),
                option_type=None if raw_action.get("option_type") in {None, ""} else str(raw_action.get("option_type")).upper(),
                expiration=_to_date(raw_action.get("expiration")),
                strike=_to_decimal(raw_action.get("strike")),
                limit_price=_to_decimal(raw_action.get("limit_price")),
                related_position_key=None if raw_action.get("related_position_key") in {None, ""} else str(raw_action.get("related_position_key")),
                current_expiration=_to_date(raw_action.get("current_expiration")),
                current_strike=_to_decimal(raw_action.get("current_strike")),
                target_expiration=_to_date(raw_action.get("target_expiration")),
                target_strike=_to_decimal(raw_action.get("target_strike")),
                raw_payload=raw_action,
            )
        )
    return actions


def _find_symbol_analysis(analysis: PortfolioAnalysis, symbol: str | None) -> SymbolAnalysis | None:
    if symbol is None:
        return None
    for item in analysis.symbols:
        if item.symbol == symbol:
            return item
    return None


def _find_matching_option_position(action: ProposedAction, positions: list[dict[str, Any]]) -> dict[str, Any] | None:
    expiration_to_match = action.expiration
    strike_to_match = action.strike
    if action.action_type == "roll_option":
        expiration_to_match = action.current_expiration
        strike_to_match = action.current_strike

    if action.related_position_key:
        for position in positions:
            if position.get("position_key") == action.related_position_key:
                return position
    for position in positions:
        if position.get("underlying_symbol") != action.symbol:
            continue
        if (position.get("option_type") or "").upper() != (action.option_type or ""):
            continue
        expiration = position.get("expiration_date")
        if isinstance(expiration, datetime):
            expiration = expiration.date()
        if expiration != expiration_to_match:
            continue
        strike = position.get("strike_price")
        if strike != strike_to_match:
            continue
        return position
    return None


def _short_contracts(position: dict[str, Any] | None) -> int:
    if position is None:
        return 0
    return int(Decimal(str(position.get("short_quantity") or 0)))


def _position_strike(position: dict[str, Any] | None) -> Decimal | None:
    if position is None:
        return None
    strike = position.get("strike_price")
    if strike is None:
        return None
    return Decimal(str(strike))


def validate_actions(
    *,
    actions: list[ProposedAction],
    analysis: PortfolioAnalysis,
    positions: list[dict[str, Any]],
    supported_symbols: list[str],
    enable_new_entries: bool,
    enable_management: bool,
) -> list[ValidatedAction]:
    validated: list[ValidatedAction] = []
    supported = set(supported_symbols)
    for action in actions:
        errors: list[str] = []
        execution_supported = False
        matched_position = None

        if action.action_type not in ALLOWED_ACTION_TYPES:
            errors.append(f"Unsupported action_type: {action.action_type}")
        if action.confidence not in ALLOWED_CONFIDENCE:
            errors.append(f"Unsupported confidence value: {action.confidence}")
        if not action.rationale:
            errors.append("Action must include at least one rationale item")

        if action.action_type in {"sell_covered_call", "sell_cash_secured_put"}:
            if not enable_new_entries:
                errors.append("New entries are disabled by policy")
            if action.symbol is None or action.symbol not in supported:
                errors.append("Symbol is not in the supported trading universe")
            if action.quantity is None or action.quantity <= 0:
                errors.append("Entry actions require a positive quantity")
            if action.option_type is None or action.expiration is None or action.strike is None or action.limit_price is None:
                errors.append("Entry actions require option_type, expiration, strike, and limit_price")

        if action.action_type in {"close_option", "roll_option"}:
            if not enable_management:
                errors.append("Management actions are disabled by policy")
            matched_position = _find_matching_option_position(action, positions)
            if matched_position is None:
                errors.append("No matching live option position was found for the management action")

        symbol_analysis = _find_symbol_analysis(analysis, action.symbol)
        if action.action_type == "sell_covered_call":
            if action.option_type != "CALL":
                errors.append("Covered calls must use CALL contracts")
            if symbol_analysis is None or action.quantity is None or symbol_analysis.covered_call_contracts_available < action.quantity:
                errors.append("Insufficient covered-call share coverage for requested quantity")
            else:
                execution_supported = True

        if action.action_type == "sell_cash_secured_put":
            if action.option_type != "PUT":
                errors.append("Cash-secured puts must use PUT contracts")
            if action.quantity is not None and action.strike is not None:
                reserve = action.strike * Decimal("100") * Decimal(action.quantity)
                if analysis.deployable_cash < reserve:
                    errors.append("Deployable cash does not cover requested CSP reserve")
                else:
                    execution_supported = True

        if action.action_type == "close_option" and matched_position is not None:
            if action.limit_price is None or action.quantity is None or action.quantity <= 0:
                errors.append("Close actions require quantity and limit_price")
            else:
                execution_supported = True

        if action.action_type == "roll_option":
            if action.option_type not in {"CALL", "PUT"}:
                errors.append("Roll actions require option_type to be CALL or PUT")
            if action.quantity is None or action.quantity <= 0:
                errors.append("Roll actions require a positive quantity")
            if action.current_expiration is None or action.current_strike is None:
                errors.append("Roll actions require current_expiration and current_strike")
            if action.target_expiration is None or action.target_strike is None:
                errors.append("Roll actions require target_expiration and target_strike")
            if action.limit_price is None:
                errors.append("Roll actions require limit_price")

            matched_short_contracts = _short_contracts(matched_position)
            if matched_position is not None and matched_short_contracts <= 0:
                errors.append("Roll execution currently supports only live short option positions")
            if matched_position is not None and action.quantity is not None and matched_short_contracts > 0 and action.quantity > matched_short_contracts:
                errors.append("Roll quantity cannot exceed the live short option quantity")
            if (
                action.current_expiration is not None
                and action.current_strike is not None
                and action.target_expiration is not None
                and action.target_strike is not None
                and action.current_expiration == action.target_expiration
                and action.current_strike == action.target_strike
            ):
                errors.append("Roll target must differ from the current contract")

            if action.option_type == "PUT" and action.quantity is not None and action.target_strike is not None:
                current_strike = _position_strike(matched_position)
                released_reserve = Decimal("0")
                if current_strike is not None:
                    released_reserve = current_strike * Decimal("100") * Decimal(action.quantity)
                target_reserve = action.target_strike * Decimal("100") * Decimal(action.quantity)
                if analysis.deployable_cash + released_reserve < target_reserve:
                    errors.append("Post-roll cash would not cover the replacement CSP reserve")

            if not errors:
                execution_supported = True

        if action.action_type in {"hold", "skip"}:
            execution_supported = False

        validation_status = "valid" if not errors else "invalid"
        validated.append(
            ValidatedAction(
                proposed=action,
                validation_status=validation_status,
                execution_supported=execution_supported,
                validation_errors=errors,
                matched_position=matched_position,
            )
        )
    return validated