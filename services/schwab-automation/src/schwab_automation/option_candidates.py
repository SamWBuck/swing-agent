from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from .candidate_discovery import EntryCandidateIdea
from .config import Settings


@dataclass(frozen=True)
class BrokerOptionCandidate:
    symbol: str
    option_type: str
    expiration: str
    strike: str
    bid: str | None
    ask: str | None
    mark: str | None
    delta: str | None
    days_to_expiration: int | None
    contract_symbol: str | None


def _as_decimal(value: Any) -> Decimal | None:
    if value in {None, ""}:
        return None
    return Decimal(str(value))


def _normalize_expiration_key(expiration_key: str) -> tuple[date | None, int | None]:
    if not expiration_key:
        return None, None
    prefix, _, suffix = str(expiration_key).partition(":")
    try:
        expiration = date.fromisoformat(prefix)
    except ValueError:
        return None, None
    dte = None
    if suffix:
        try:
            dte = int(suffix)
        except ValueError:
            dte = None
    return expiration, dte


def _candidate_from_contract(symbol: str, expiration_key: str, strike_key: str, contract: dict[str, Any]) -> BrokerOptionCandidate | None:
    expiration, dte = _normalize_expiration_key(expiration_key)
    if expiration is None:
        return None
    strike = _as_decimal(strike_key)
    if strike is None:
        strike = _as_decimal(contract.get("strikePrice"))
    if strike is None:
        return None
    return BrokerOptionCandidate(
        symbol=symbol,
        option_type=str(contract.get("putCall") or "").upper(),
        expiration=expiration.isoformat(),
        strike=str(strike),
        bid=None if contract.get("bid") is None else str(contract.get("bid")),
        ask=None if contract.get("ask") is None else str(contract.get("ask")),
        mark=None if contract.get("mark") is None else str(contract.get("mark")),
        delta=None if contract.get("delta") is None else str(contract.get("delta")),
        days_to_expiration=dte,
        contract_symbol=None if contract.get("symbol") is None else str(contract.get("symbol")),
    )


def _extract_chain_candidates(symbol: str, option_type: str, payload: dict[str, Any], *, limit: int) -> list[BrokerOptionCandidate]:
    map_name = "callExpDateMap" if option_type == "CALL" else "putExpDateMap"
    exp_map = payload.get(map_name) or {}
    results: list[BrokerOptionCandidate] = []
    for expiration_key, strike_map in exp_map.items():
        if not isinstance(strike_map, dict):
            continue
        for strike_key, contracts in strike_map.items():
            if not isinstance(contracts, list):
                continue
            for contract in contracts:
                if not isinstance(contract, dict):
                    continue
                candidate = _candidate_from_contract(symbol, expiration_key, strike_key, contract)
                if candidate is None:
                    continue
                if candidate.option_type != option_type:
                    continue
                results.append(candidate)
                if len(results) >= limit:
                    return results
    return results


async def fetch_broker_option_candidates(
    client: Any,
    *,
    settings: Settings,
    ideas: list[EntryCandidateIdea],
    as_of: datetime,
) -> dict[str, list[dict[str, Any]]]:
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=UTC)

    start_date = as_of.date() + timedelta(days=settings.entry_chain_min_dte)
    end_date = as_of.date() + timedelta(days=settings.entry_chain_max_dte)
    by_key: dict[str, list[dict[str, Any]]] = {}
    seen_requests: set[tuple[str, str]] = set()

    for idea in ideas:
        request_key = (idea.symbol, idea.option_type)
        if request_key in seen_requests:
            continue
        seen_requests.add(request_key)
        contract_type = client.Options.ContractType.CALL if idea.option_type == "CALL" else client.Options.ContractType.PUT
        response = await client.get_option_chain(
            idea.symbol,
            contract_type=contract_type,
            strike_count=settings.entry_chain_strike_count,
            include_underlying_quote=True,
            from_date=start_date,
            to_date=end_date,
        )
        response.raise_for_status()
        payload = response.json()
        candidates = _extract_chain_candidates(
            idea.symbol,
            idea.option_type,
            payload,
            limit=settings.entry_chain_contract_limit,
        )
        by_key[f"{idea.symbol}:{idea.option_type}"] = [asdict(candidate) for candidate in candidates]

    return by_key