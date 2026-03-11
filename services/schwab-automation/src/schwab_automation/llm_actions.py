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
class MatchedPosition:
    position_key: str
    underlying_symbol: str
    option_type: str | None
    expiration_date: datetime | None
    strike_price: Decimal | None
    quantity: Decimal
    long_quantity: Decimal
    short_quantity: Decimal
    raw_payload: dict[str, Any]

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "MatchedPosition":
        expiration = payload.get("expiration_date")
        if isinstance(expiration, date) and not isinstance(expiration, datetime):
            expiration = datetime(expiration.year, expiration.month, expiration.day, tzinfo=UTC)
        return cls(
            position_key=str(payload.get("position_key") or ""),
            underlying_symbol=str(payload.get("underlying_symbol") or ""),
            option_type=None if payload.get("option_type") in {None, ""} else str(payload.get("option_type")).upper(),
            expiration_date=expiration if isinstance(expiration, datetime) else None,
            strike_price=_to_decimal(payload.get("strike")) or _to_decimal(payload.get("strike_price")),
            quantity=_to_decimal(payload.get("quantity")) or Decimal("0"),
            long_quantity=_to_decimal(payload.get("long_quantity")) or Decimal("0"),
            short_quantity=_to_decimal(payload.get("short_quantity")) or Decimal("0"),
            raw_payload=payload.get("raw_payload") or {},
        )


@dataclass(frozen=True)
class ValidatedAction:
    proposed: ProposedAction
    validation_status: str
    execution_supported: bool
    validation_errors: list[str]
    matched_position: MatchedPosition | None


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


def _find_matching_option_position(action: ProposedAction, positions: list[dict[str, Any]]) -> MatchedPosition | None:
    expiration_to_match = action.expiration
    strike_to_match = action.strike
    if action.action_type == "roll_option":
        expiration_to_match = action.current_expiration
        strike_to_match = action.current_strike

    if action.related_position_key:
        for position in positions:
            if position.get("position_key") == action.related_position_key:
                return MatchedPosition.from_payload(position)
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
        return MatchedPosition.from_payload(position)
    return None


def _short_contracts(position: MatchedPosition | None) -> int:
    if position is None:
        return 0
    return int(position.short_quantity)


def _position_strike(position: MatchedPosition | None) -> Decimal | None:
    if position is None:
        return None
    return position.strike_price


def validate_actions(
    *,
    actions: list[ProposedAction],
    analysis: PortfolioAnalysis,
    positions: list[dict[str, Any]],
    supported_symbols: list[str],
    enable_new_entries: bool,
    enable_management: bool,
    max_new_entries_per_run: int | None = None,
    max_contracts_per_symbol: int | None = None,
    min_account_cash_floor: Decimal | int | float | str | None = None,
    liquidation_value: Decimal | int | float | str | None = None,
    max_position_pct_of_portfolio: int | None = None,
) -> list[ValidatedAction]:
    """Validate proposed actions against deterministic portfolio and policy rules.

    This function is the hard-risk firewall between LLM output and broker execution.
    It applies portfolio cash gates, symbol eligibility, position matching, and
    sequential entry limits before any order may be submitted.
    """
    validated: list[ValidatedAction] = []
    supported = set(supported_symbols)
    remaining_deployable_cash = analysis.deployable_cash
    entry_actions_accepted = 0
    projected_short_contracts = {
        item.symbol: item.short_call_contracts + item.short_put_contracts
        for item in analysis.symbols
    }
    projected_covered_call_capacity = {
        item.symbol: item.covered_call_contracts_available
        for item in analysis.symbols
    }
    min_cash_floor = _to_decimal(min_account_cash_floor) or Decimal("0")
    normalized_liquidation_value = _to_decimal(liquidation_value)
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
            if max_new_entries_per_run is not None and max_new_entries_per_run >= 0 and entry_actions_accepted >= max_new_entries_per_run:
                errors.append("New entry limit per run has been reached")
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
            contracts_available = projected_covered_call_capacity.get(action.symbol or "", 0)
            if symbol_analysis is None or action.quantity is None or contracts_available < action.quantity:
                errors.append("Insufficient covered-call share coverage for requested quantity")
            if (
                max_contracts_per_symbol is not None
                and action.symbol is not None
                and action.quantity is not None
                and projected_short_contracts.get(action.symbol, 0) + action.quantity > max_contracts_per_symbol
            ):
                errors.append("Requested entry would exceed the max contracts per symbol limit")
            else:
                execution_supported = True

        if action.action_type == "sell_cash_secured_put":
            if action.option_type != "PUT":
                errors.append("Cash-secured puts must use PUT contracts")
            if action.quantity is not None and action.strike is not None:
                reserve = action.strike * Decimal("100") * Decimal(action.quantity)
                if remaining_deployable_cash < reserve:
                    errors.append("Deployable cash does not cover requested CSP reserve")
                elif remaining_deployable_cash - reserve < min_cash_floor:
                    errors.append("Requested CSP would breach the minimum account cash floor")
                elif (
                    max_position_pct_of_portfolio is not None
                    and max_position_pct_of_portfolio >= 0
                    and normalized_liquidation_value is not None
                    and normalized_liquidation_value > 0
                    and reserve / normalized_liquidation_value > Decimal(max_position_pct_of_portfolio) / Decimal("100")
                ):
                    errors.append("Requested CSP would exceed the max position percentage of portfolio")
                elif max_position_pct_of_portfolio is not None and normalized_liquidation_value in {None, Decimal('0')}:
                    errors.append("Portfolio liquidation value is required to enforce position sizing")
                elif (
                    max_contracts_per_symbol is not None
                    and action.symbol is not None
                    and projected_short_contracts.get(action.symbol, 0) + action.quantity > max_contracts_per_symbol
                ):
                    errors.append("Requested entry would exceed the max contracts per symbol limit")
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
                elif analysis.deployable_cash + released_reserve - target_reserve < min_cash_floor:
                    errors.append("Post-roll cash would breach the minimum account cash floor")
                elif (
                    max_position_pct_of_portfolio is not None
                    and max_position_pct_of_portfolio >= 0
                    and normalized_liquidation_value is not None
                    and normalized_liquidation_value > 0
                    and target_reserve / normalized_liquidation_value > Decimal(max_position_pct_of_portfolio) / Decimal("100")
                ):
                    errors.append("Replacement CSP would exceed the max position percentage of portfolio")
                elif max_position_pct_of_portfolio is not None and normalized_liquidation_value in {None, Decimal('0')}:
                    errors.append("Portfolio liquidation value is required to enforce position sizing")

            if not errors:
                execution_supported = True

        if action.action_type in {"hold", "skip"}:
            execution_supported = False

        validation_status = "valid" if not errors else "invalid"
        if validation_status == "valid" and action.action_type == "sell_covered_call" and action.symbol is not None and action.quantity is not None:
            projected_covered_call_capacity[action.symbol] = projected_covered_call_capacity.get(action.symbol, 0) - action.quantity
            projected_short_contracts[action.symbol] = projected_short_contracts.get(action.symbol, 0) + action.quantity
            entry_actions_accepted += 1
        elif validation_status == "valid" and action.action_type == "sell_cash_secured_put" and action.symbol is not None and action.quantity is not None and action.strike is not None:
            reserve = action.strike * Decimal("100") * Decimal(action.quantity)
            remaining_deployable_cash -= reserve
            projected_short_contracts[action.symbol] = projected_short_contracts.get(action.symbol, 0) + action.quantity
            entry_actions_accepted += 1
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