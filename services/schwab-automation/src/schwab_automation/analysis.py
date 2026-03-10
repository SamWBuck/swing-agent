from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any


def _to_decimal(value: Decimal | int | float | str | None) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _contracts(quantity: Decimal | int | float | str | None) -> int:
    value = _to_decimal(quantity)
    if value <= 0:
        return 0
    return int(value)


def _money(value: Decimal) -> str:
    return f"${value.quantize(Decimal('0.01')):,}"


@dataclass(frozen=True)
class SymbolAnalysis:
    symbol: str
    share_quantity: Decimal
    share_lots: int
    short_call_contracts: int
    long_call_contracts: int
    long_dated_long_call_contracts: int
    short_put_contracts: int
    short_put_reserve: Decimal
    covered_call_contracts_available: int
    pmcc_contracts_available: int


@dataclass(frozen=True)
class ShadowDecision:
    action_type: str
    status: str
    symbol: str | None
    rationale: str
    details: dict[str, Any]


@dataclass(frozen=True)
class PortfolioAnalysis:
    cash_balance: Decimal
    reserved_cash: Decimal
    deployable_cash: Decimal
    symbols: list[SymbolAnalysis]
    decisions: list[ShadowDecision]
    supported_symbols: list[str]


def analyze_portfolio(
    *,
    cash_balance: Decimal | int | float | str | None,
    positions: list[dict[str, Any]],
    as_of: datetime,
    supported_symbols: list[str],
    min_csp_reserve: Decimal | int | float | str,
    min_entry_cash: Decimal | int | float | str,
    roll_dte_threshold_days: int,
    close_dte_threshold_days: int,
) -> PortfolioAnalysis:
    normalized_cash = _to_decimal(cash_balance)
    min_csp_reserve_value = _to_decimal(min_csp_reserve)
    min_entry_cash_value = _to_decimal(min_entry_cash)
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=UTC)

    decisions: list[ShadowDecision] = [
        ShadowDecision(
            action_type="capital_summary",
            status="proposed",
            symbol=None,
            rationale="Computed cash balance, reserved cash for short puts, and deployable cash for shadow mode.",
            details={},
        )
    ]

    per_symbol: dict[str, dict[str, Any]] = {}
    reserved_cash = Decimal("0")
    long_dated_threshold = as_of + timedelta(days=90)
    roll_threshold = as_of + timedelta(days=roll_dte_threshold_days)
    close_threshold = as_of + timedelta(days=close_dte_threshold_days)

    for position in positions:
        symbol = str(position.get("underlying_symbol") or "UNKNOWN")
        bucket = per_symbol.setdefault(
            symbol,
            {
                "share_quantity": Decimal("0"),
                "short_call_contracts": 0,
                "long_call_contracts": 0,
                "long_dated_long_call_contracts": 0,
                "short_put_contracts": 0,
                "short_put_reserve": Decimal("0"),
            },
        )
        option_type = position.get("option_type")
        expiration = position.get("expiration_date")
        strike_price = position.get("strike_price")
        long_quantity = _to_decimal(position.get("long_quantity"))
        short_quantity = _to_decimal(position.get("short_quantity"))
        quantity = _to_decimal(position.get("quantity"))

        if option_type is None:
            if long_quantity > 0:
                bucket["share_quantity"] += long_quantity
            elif quantity > 0:
                bucket["share_quantity"] += quantity
            continue

        option_kind = str(option_type).upper()
        if option_kind == "CALL":
            short_calls = _contracts(short_quantity)
            long_calls = _contracts(long_quantity)
            bucket["short_call_contracts"] += short_calls
            bucket["long_call_contracts"] += long_calls
            if long_calls > 0 and isinstance(expiration, datetime) and expiration >= long_dated_threshold:
                bucket["long_dated_long_call_contracts"] += long_calls
        elif option_kind == "PUT":
            short_puts = _contracts(short_quantity)
            bucket["short_put_contracts"] += short_puts
            if short_puts > 0 and strike_price is not None:
                reserve = _to_decimal(strike_price) * Decimal("100") * Decimal(short_puts)
                bucket["short_put_reserve"] += reserve
                reserved_cash += reserve

        if option_kind in {"CALL", "PUT"} and short_quantity > 0:
            if isinstance(expiration, datetime):
                days_to_expiration = (expiration.date() - as_of.date()).days
                if expiration <= close_threshold:
                    decisions.append(
                        ShadowDecision(
                            action_type="close_candidate",
                            status="proposed",
                            symbol=symbol,
                            rationale=f"Short {option_kind.lower()} exposure in {symbol} is within {close_dte_threshold_days} day(s) of expiration; shadow mode flags it for close review.",
                            details={
                                "days_to_expiration": days_to_expiration,
                                "option_type": option_kind,
                                "short_contracts": _contracts(short_quantity),
                            },
                        )
                    )
                elif expiration <= roll_threshold:
                    decisions.append(
                        ShadowDecision(
                            action_type="roll_candidate",
                            status="proposed",
                            symbol=symbol,
                            rationale=f"Short {option_kind.lower()} exposure in {symbol} is within {roll_dte_threshold_days} day(s) of expiration; shadow mode flags it for roll review.",
                            details={
                                "days_to_expiration": days_to_expiration,
                                "option_type": option_kind,
                                "short_contracts": _contracts(short_quantity),
                            },
                        )
                    )

    symbol_analyses: list[SymbolAnalysis] = []

    for symbol in sorted(per_symbol):
        bucket = per_symbol[symbol]
        share_quantity = bucket["share_quantity"]
        share_lots = int(share_quantity // Decimal("100"))
        short_call_contracts = int(bucket["short_call_contracts"])
        long_call_contracts = int(bucket["long_call_contracts"])
        long_dated_long_call_contracts = int(bucket["long_dated_long_call_contracts"])
        short_put_contracts = int(bucket["short_put_contracts"])
        short_put_reserve = _to_decimal(bucket["short_put_reserve"])
        covered_call_contracts_available = max(share_lots - short_call_contracts, 0)
        pmcc_contracts_available = max(
            long_dated_long_call_contracts - max(short_call_contracts - share_lots, 0),
            0,
        )

        symbol_analysis = SymbolAnalysis(
            symbol=symbol,
            share_quantity=share_quantity,
            share_lots=share_lots,
            short_call_contracts=short_call_contracts,
            long_call_contracts=long_call_contracts,
            long_dated_long_call_contracts=long_dated_long_call_contracts,
            short_put_contracts=short_put_contracts,
            short_put_reserve=short_put_reserve,
            covered_call_contracts_available=covered_call_contracts_available,
            pmcc_contracts_available=pmcc_contracts_available,
        )
        symbol_analyses.append(symbol_analysis)

        if share_quantity > 0:
            if covered_call_contracts_available > 0:
                decisions.append(
                    ShadowDecision(
                        action_type="covered_call_candidate",
                        status="proposed",
                        symbol=symbol,
                        rationale=f"{symbol} has {share_lots} uncovered 100-share lot(s) available for covered calls.",
                        details={"share_quantity": str(share_quantity), "contracts_available": covered_call_contracts_available},
                    )
                )
            elif share_quantity < Decimal("100"):
                decisions.append(
                    ShadowDecision(
                        action_type="covered_call_candidate",
                        status="skipped",
                        symbol=symbol,
                        rationale=f"{symbol} has only {share_quantity.normalize()} share(s), which is below one covered-call lot.",
                        details={"share_quantity": str(share_quantity)},
                    )
                )

            decisions.append(
                ShadowDecision(
                    action_type="hold_candidate",
                    status="proposed",
                    symbol=symbol,
                    rationale=f"Hold {symbol} shares while shadow mode tracks option-management eligibility.",
                    details={"share_quantity": str(share_quantity)},
                )
            )

        if short_put_contracts > 0:
            decisions.append(
                ShadowDecision(
                    action_type="short_put_obligation",
                    status="proposed",
                    symbol=symbol,
                    rationale=f"{symbol} has {short_put_contracts} short put contract(s) reserving {_money(short_put_reserve)}.",
                    details={"contracts": short_put_contracts, "reserve": str(short_put_reserve)},
                )
            )

        if long_dated_long_call_contracts > 0:
            rationale = (
                f"{symbol} has {pmcc_contracts_available} long-dated call contract(s) available for PMCC coverage."
                if pmcc_contracts_available > 0
                else f"{symbol} long-dated call cover is already consumed by existing short calls."
            )
            decisions.append(
                ShadowDecision(
                    action_type="pmcc_candidate",
                    status="proposed" if pmcc_contracts_available > 0 else "skipped",
                    symbol=symbol,
                    rationale=rationale,
                    details={
                        "long_call_contracts": long_call_contracts,
                        "long_dated_long_call_contracts": long_dated_long_call_contracts,
                        "contracts_available": pmcc_contracts_available,
                    },
                )
            )
        elif long_call_contracts > 0:
            decisions.append(
                ShadowDecision(
                    action_type="pmcc_candidate",
                    status="skipped",
                    symbol=symbol,
                    rationale=f"{symbol} has long calls, but none are far enough out to count as PMCC cover yet.",
                    details={"long_call_contracts": long_call_contracts},
                )
            )

    deployable_cash = normalized_cash - reserved_cash
    if deployable_cash < 0:
        deployable_cash = Decimal("0")

    capital_decision = decisions[0]
    decisions[0] = ShadowDecision(
        action_type=capital_decision.action_type,
        status=capital_decision.status,
        symbol=capital_decision.symbol,
        rationale=capital_decision.rationale,
        details={
            "cash_balance": str(normalized_cash),
            "reserved_cash": str(reserved_cash),
            "deployable_cash": str(deployable_cash),
        },
    )

    entry_status = "skipped"
    entry_details: dict[str, Any] = {"deployable_cash": str(deployable_cash)}
    if deployable_cash < min_entry_cash_value:
        entry_rationale = (
            f"Deployable cash {_money(deployable_cash)} is below the minimum entry threshold of {_money(min_entry_cash_value)}."
        )
    elif deployable_cash < min_csp_reserve_value:
        entry_rationale = (
            f"Deployable cash {_money(deployable_cash)} is below the minimum CSP reserve of {_money(min_csp_reserve_value)}."
        )
    elif not supported_symbols:
        entry_rationale = "No tracked symbols are available in symbol_availability for shadow entry scans."
    else:
        entry_status = "proposed"
        entry_rationale = (
            f"Deployable cash {_money(deployable_cash)} clears policy thresholds; shadow mode can scan supported symbols for new entries."
        )
        entry_details["candidate_symbols"] = supported_symbols
    decisions.append(
        ShadowDecision(
            action_type="new_position_scan",
            status=entry_status,
            symbol=None,
            rationale=entry_rationale,
            details=entry_details,
        )
    )

    if entry_status == "proposed":
        for symbol in supported_symbols:
            decisions.append(
                ShadowDecision(
                    action_type="csp_entry_candidate",
                    status="proposed",
                    symbol=symbol,
                    rationale=f"{symbol} is in the tracked symbol universe and passes the portfolio cash gate for a shadow CSP scan.",
                    details={
                        "deployable_cash": str(deployable_cash),
                        "min_csp_reserve": str(min_csp_reserve_value),
                    },
                )
            )

    return PortfolioAnalysis(
        cash_balance=normalized_cash,
        reserved_cash=reserved_cash,
        deployable_cash=deployable_cash,
        symbols=symbol_analyses,
        decisions=decisions,
        supported_symbols=supported_symbols,
    )


def format_shadow_report(
    *,
    service_name: str,
    run_type: str,
    dry_run: bool,
    account_label: str,
    analysis: PortfolioAnalysis,
    schedule_note: str | None = None,
) -> str:
    lines = [
        f"[{service_name}] {run_type} shadow report",
        f"Account: {account_label}",
        f"Cash balance: {_money(analysis.cash_balance)}",
        f"Cash reserved: {_money(analysis.reserved_cash)}",
        f"Deployable cash: {_money(analysis.deployable_cash)}",
    ]
    if schedule_note:
        lines.append(f"Schedule: {schedule_note}")
    if analysis.supported_symbols:
        lines.append(f"Tracked universe: {', '.join(analysis.supported_symbols)}")

    lines.append("Positions:")
    if not analysis.symbols:
        lines.append("- none")
    else:
        for symbol in analysis.symbols:
            parts = []
            if symbol.share_quantity > 0:
                parts.append(f"shares {symbol.share_quantity.normalize()}")
            if symbol.short_call_contracts > 0:
                parts.append(f"short calls {symbol.short_call_contracts}")
            if symbol.long_call_contracts > 0:
                parts.append(f"long calls {symbol.long_call_contracts}")
            if symbol.short_put_contracts > 0:
                parts.append(f"short puts {symbol.short_put_contracts}")
            if not parts:
                parts.append("unclassified")
            lines.append(f"- {symbol.symbol}: {', '.join(parts)}")

    lines.append("Eligibility:")
    if not analysis.symbols:
        lines.append("- no current positions")
    else:
        for symbol in analysis.symbols:
            lines.append(
                f"- {symbol.symbol}: covered calls {symbol.covered_call_contracts_available}, PMCC slots {symbol.pmcc_contracts_available}, short put reserve {_money(symbol.short_put_reserve)}"
            )

    lines.append("Shadow actions:")
    for decision in analysis.decisions:
        prefix = decision.symbol or "portfolio"
        lines.append(f"- {prefix}: {decision.action_type} [{decision.status}] - {decision.rationale}")

    lines.append(f"Dry run: {'yes' if dry_run else 'no'}")
    return "\n".join(lines)