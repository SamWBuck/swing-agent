from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from .config import Settings

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AccountSnapshot:
    account_hash: str
    account_number: str | None
    account_type: str | None
    display_name: str | None
    cash_available: Decimal | None
    cash_reserved: Decimal | None
    liquidation_value: Decimal | None
    balances: dict[str, Any]
    raw_payload: dict[str, Any]
    positions: list[dict[str, Any]]
    synced_at: datetime


def _as_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _parse_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=UTC)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value) / 1000.0, tz=UTC)

    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _pick_first(mapping: dict[str, Any], keys: list[str]) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value is not None:
            return value
    return None


def _unwrap_account_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if "securitiesAccount" in payload and isinstance(payload["securitiesAccount"], dict):
        return payload["securitiesAccount"]
    for value in payload.values():
        if isinstance(value, dict) and (
            "positions" in value or "currentBalances" in value or "initialBalances" in value
        ):
            return value
    return payload


def _normalize_position(position: dict[str, Any]) -> dict[str, Any]:
    instrument = position.get("instrument") or {}
    underlying_symbol = (
        instrument.get("underlyingSymbol")
        or instrument.get("symbol")
        or position.get("symbol")
        or "UNKNOWN"
    )
    option_symbol = instrument.get("symbol")
    option_type = instrument.get("putCall")
    expiration = _parse_datetime(_pick_first(instrument, ["expirationDate", "expirationDateTime"]))
    strike_price = _as_decimal(instrument.get("strikePrice"))
    asset_type = instrument.get("assetType") or position.get("assetType") or "UNKNOWN"
    instrument_type = instrument.get("type") or instrument.get("instrumentType")
    long_quantity = _as_decimal(position.get("longQuantity")) or Decimal("0")
    short_quantity = _as_decimal(position.get("shortQuantity")) or Decimal("0")
    quantity = _as_decimal(position.get("quantity"))
    if quantity is None:
        quantity = long_quantity - short_quantity

    position_key_parts = [underlying_symbol, str(asset_type)]
    if option_symbol:
        position_key_parts.append(str(option_symbol))
    elif option_type and strike_price is not None and expiration is not None:
        position_key_parts.extend([str(option_type), str(expiration.date()), str(strike_price)])
    else:
        position_key_parts.append(str(quantity))

    return {
        "position_key": "|".join(position_key_parts),
        "underlying_symbol": underlying_symbol,
        "asset_type": str(asset_type),
        "instrument_type": None if instrument_type is None else str(instrument_type),
        "option_type": None if option_type is None else str(option_type).upper(),
        "expiration_date": expiration,
        "strike_price": strike_price,
        "quantity": quantity,
        "long_quantity": long_quantity,
        "short_quantity": short_quantity,
        "average_price": _as_decimal(_pick_first(position, ["averagePrice", "averageLongPrice"])),
        "market_value": _as_decimal(position.get("marketValue")),
        "cost_basis": _as_decimal(_pick_first(position, ["averagePrice", "averageLongPrice", "averageShortPrice"])),
        "raw_payload": position,
    }


def _extract_balances(account_data: dict[str, Any]) -> tuple[dict[str, Any], Decimal | None, Decimal | None, Decimal | None]:
    balances = {
        "currentBalances": account_data.get("currentBalances") or {},
        "initialBalances": account_data.get("initialBalances") or {},
        "projectedBalances": account_data.get("projectedBalances") or {},
    }
    current = balances["currentBalances"]
    initial = balances["initialBalances"]
    projected = balances["projectedBalances"]
    cash_available = _as_decimal(
        _pick_first(
            current,
            [
                "cashBalance",
                "moneyMarketFund",
                "cashReceipts",
            ],
        )
        or _pick_first(
            initial,
            [
                "totalCash",
                "cashBalance",
                "moneyMarketFund",
            ],
        )
    )
    cash_reserved = None
    liquidation_value = _as_decimal(
        _pick_first(current, ["liquidationValue", "netLiquidationValue", "equity"]) 
        or _pick_first(initial, ["liquidationValue", "netLiquidationValue", "equity"])
    )
    return balances, cash_available, cash_reserved, liquidation_value


def _select_account(accounts: list[dict[str, Any]], settings: Settings) -> dict[str, Any]:
    if not accounts:
        raise RuntimeError("Schwab returned no linked accounts for this token")

    if settings.preferred_account_hash:
        for account in accounts:
            if account.get("hashValue") == settings.preferred_account_hash:
                return account
        raise RuntimeError(f"Preferred account hash {settings.preferred_account_hash} was not returned by Schwab")

    if settings.preferred_account_number:
        for account in accounts:
            if account.get("accountNumber") == settings.preferred_account_number:
                return account
        raise RuntimeError(f"Preferred account number {settings.preferred_account_number} was not returned by Schwab")

    if settings.require_explicit_account and not settings.dry_run:
        raise RuntimeError(
            "Explicit Schwab account selection is required outside dry-run mode; set SCHWAB_ACCOUNT_HASH or SCHWAB_ACCOUNT_NUMBER"
        )
    return accounts[0]


async def fetch_account_snapshot(client: Any, settings: Settings) -> AccountSnapshot:
    accounts_response = await client.get_account_numbers()
    accounts_response.raise_for_status()
    accounts_payload = accounts_response.json()
    if not isinstance(accounts_payload, list):
        raise RuntimeError("Unexpected Schwab account number payload")

    account_stub = _select_account(accounts_payload, settings)
    account_hash = account_stub.get("hashValue")
    if not account_hash:
        raise RuntimeError("Selected Schwab account is missing hashValue")

    account_fields = None
    account_namespace = getattr(client, "Account", None)
    if account_namespace is not None and hasattr(account_namespace, "Fields"):
        account_fields = [account_namespace.Fields.POSITIONS]

    if account_fields is None:
        account_response = await client.get_account(account_hash)
    else:
        account_response = await client.get_account(account_hash, fields=account_fields)
    account_response.raise_for_status()
    raw_payload = account_response.json()
    if not isinstance(raw_payload, dict):
        raise RuntimeError("Unexpected Schwab account payload")

    account_data = _unwrap_account_payload(raw_payload)
    balances, cash_available, cash_reserved, liquidation_value = _extract_balances(account_data)
    raw_positions = account_data.get("positions") or []
    positions = [_normalize_position(position) for position in raw_positions if isinstance(position, dict)]
    synced_at = datetime.now(tz=UTC)

    log.info(
        "Fetched Schwab account snapshot account_hash=%s positions=%d",
        account_hash,
        len(positions),
    )

    return AccountSnapshot(
        account_hash=account_hash,
        account_number=account_stub.get("accountNumber") or account_data.get("accountNumber"),
        account_type=account_data.get("type") or account_data.get("accountType"),
        display_name=account_data.get("nickName") or account_data.get("displayName"),
        cash_available=cash_available,
        cash_reserved=cash_reserved,
        liquidation_value=liquidation_value,
        balances=balances,
        raw_payload=raw_payload,
        positions=positions,
        synced_at=synced_at,
    )