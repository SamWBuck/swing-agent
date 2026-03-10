from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Iterator

from sqlalchemy import MetaData, Table, create_engine, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from .config import AutomationStoreSettings


def _now_utc() -> datetime:
    return datetime.now(tz=UTC)


def _to_decimal(value: Decimal | int | float | str | None) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


@dataclass(frozen=True)
class AutomationRunRecord:
    id: int
    service_name: str
    run_type: str
    status: str
    dry_run: bool
    started_at: datetime
    completed_at: datetime | None
    account_hash: str | None
    prompt_version: str | None
    details: dict[str, Any]
    error_text: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class BrokerAccountRecord:
    id: int
    account_hash: str
    account_number: str | None
    account_type: str | None
    display_name: str | None
    is_active: bool
    cash_available: Decimal | None
    cash_reserved: Decimal | None
    liquidation_value: Decimal | None
    balances: dict[str, Any]
    raw_payload: dict[str, Any]
    last_synced_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class BrokerPositionRecord:
    id: int
    account_hash: str
    position_key: str
    underlying_symbol: str
    asset_type: str
    instrument_type: str | None
    option_type: str | None
    expiration_date: datetime | None
    strike_price: Decimal | None
    quantity: Decimal
    long_quantity: Decimal
    short_quantity: Decimal
    average_price: Decimal | None
    market_value: Decimal | None
    cost_basis: Decimal | None
    is_active: bool
    synced_at: datetime
    raw_payload: dict[str, Any]
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class AutomationDecisionRecord:
    id: int
    run_id: int
    action_type: str
    symbol: str | None
    status: str
    rationale: str | None
    details: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class AutomationStore:
    def __init__(self, settings: AutomationStoreSettings) -> None:
        self._settings = settings
        self._engine: Engine = create_engine(settings.database_url, pool_pre_ping=True)
        self._metadata = MetaData(schema=settings.schema_name)
        self._runs = Table(
            settings.runs_table_name,
            self._metadata,
            autoload_with=self._engine,
            schema=settings.schema_name,
        )
        self._accounts = Table(
            settings.accounts_table_name,
            self._metadata,
            autoload_with=self._engine,
            schema=settings.schema_name,
        )
        self._positions = Table(
            settings.positions_table_name,
            self._metadata,
            autoload_with=self._engine,
            schema=settings.schema_name,
        )
        self._decisions = Table(
            settings.decisions_table_name,
            self._metadata,
            autoload_with=self._engine,
            schema=settings.schema_name,
        )

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = Session(self._engine)
        try:
            yield session
        finally:
            session.close()

    def start_run(
        self,
        *,
        service_name: str,
        run_type: str,
        dry_run: bool,
        prompt_version: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> AutomationRunRecord:
        timestamp = _now_utc()
        payload = details or {}
        with self.session() as session, session.begin():
            row = session.execute(
                insert(self._runs)
                .values(
                    service_name=service_name,
                    run_type=run_type,
                    status="running",
                    dry_run=dry_run,
                    started_at=timestamp,
                    prompt_version=prompt_version,
                    details=payload,
                    updated_at=timestamp,
                )
                .returning(*self._runs.c)
            ).mappings().one()
        return AutomationRunRecord(**dict(row))

    def finish_run(
        self,
        *,
        run_id: int,
        status: str,
        account_hash: str | None = None,
        details: dict[str, Any] | None = None,
        error_text: str | None = None,
    ) -> AutomationRunRecord:
        timestamp = _now_utc()
        update_values: dict[str, Any] = {
            "status": status,
            "completed_at": timestamp,
            "account_hash": account_hash,
            "error_text": error_text,
            "updated_at": timestamp,
        }
        if details is not None:
            update_values["details"] = details
        with self.session() as session, session.begin():
            row = session.execute(
                update(self._runs)
                .where(self._runs.c.id == run_id)
                .values(**update_values)
                .returning(*self._runs.c)
            ).mappings().one()
        return AutomationRunRecord(**dict(row))

    def upsert_account(
        self,
        *,
        account_hash: str,
        account_number: str | None,
        account_type: str | None,
        display_name: str | None,
        cash_available: Decimal | int | float | str | None,
        cash_reserved: Decimal | int | float | str | None,
        liquidation_value: Decimal | int | float | str | None,
        balances: dict[str, Any],
        raw_payload: dict[str, Any],
        synced_at: datetime | None = None,
    ) -> BrokerAccountRecord:
        timestamp = synced_at or _now_utc()
        statement = pg_insert(self._accounts).values(
            account_hash=account_hash,
            account_number=account_number,
            account_type=account_type,
            display_name=display_name,
            is_active=True,
            cash_available=_to_decimal(cash_available),
            cash_reserved=_to_decimal(cash_reserved),
            liquidation_value=_to_decimal(liquidation_value),
            balances=balances,
            raw_payload=raw_payload,
            last_synced_at=timestamp,
            updated_at=timestamp,
        )
        excluded = statement.excluded
        statement = statement.on_conflict_do_update(
            index_elements=[self._accounts.c.account_hash],
            set_={
                "account_number": excluded.account_number,
                "account_type": excluded.account_type,
                "display_name": excluded.display_name,
                "is_active": True,
                "cash_available": excluded.cash_available,
                "cash_reserved": excluded.cash_reserved,
                "liquidation_value": excluded.liquidation_value,
                "balances": excluded.balances,
                "raw_payload": excluded.raw_payload,
                "last_synced_at": excluded.last_synced_at,
                "updated_at": excluded.updated_at,
            },
        ).returning(*self._accounts.c)

        with self.session() as session, session.begin():
            row = session.execute(statement).mappings().one()
        return BrokerAccountRecord(**dict(row))

    def replace_positions(
        self,
        *,
        account_hash: str,
        positions: list[dict[str, Any]],
        synced_at: datetime | None = None,
    ) -> list[BrokerPositionRecord]:
        timestamp = synced_at or _now_utc()
        records: list[BrokerPositionRecord] = []

        with self.session() as session, session.begin():
            session.execute(
                update(self._positions)
                .where(self._positions.c.account_hash == account_hash)
                .values(is_active=False, updated_at=timestamp)
            )

            for position in positions:
                statement = pg_insert(self._positions).values(
                    account_hash=account_hash,
                    position_key=position["position_key"],
                    underlying_symbol=position["underlying_symbol"],
                    asset_type=position["asset_type"],
                    instrument_type=position.get("instrument_type"),
                    option_type=position.get("option_type"),
                    expiration_date=position.get("expiration_date"),
                    strike_price=_to_decimal(position.get("strike_price")),
                    quantity=_to_decimal(position.get("quantity")) or Decimal("0"),
                    long_quantity=_to_decimal(position.get("long_quantity")) or Decimal("0"),
                    short_quantity=_to_decimal(position.get("short_quantity")) or Decimal("0"),
                    average_price=_to_decimal(position.get("average_price")),
                    market_value=_to_decimal(position.get("market_value")),
                    cost_basis=_to_decimal(position.get("cost_basis")),
                    is_active=True,
                    synced_at=timestamp,
                    raw_payload=position.get("raw_payload") or {},
                    updated_at=timestamp,
                )
                excluded = statement.excluded
                statement = statement.on_conflict_do_update(
                    index_elements=[self._positions.c.account_hash, self._positions.c.position_key],
                    set_={
                        "underlying_symbol": excluded.underlying_symbol,
                        "asset_type": excluded.asset_type,
                        "instrument_type": excluded.instrument_type,
                        "option_type": excluded.option_type,
                        "expiration_date": excluded.expiration_date,
                        "strike_price": excluded.strike_price,
                        "quantity": excluded.quantity,
                        "long_quantity": excluded.long_quantity,
                        "short_quantity": excluded.short_quantity,
                        "average_price": excluded.average_price,
                        "market_value": excluded.market_value,
                        "cost_basis": excluded.cost_basis,
                        "is_active": True,
                        "synced_at": excluded.synced_at,
                        "raw_payload": excluded.raw_payload,
                        "updated_at": excluded.updated_at,
                    },
                ).returning(*self._positions.c)
                row = session.execute(statement).mappings().one()
                records.append(BrokerPositionRecord(**dict(row)))

        return records

    def record_decision(
        self,
        *,
        run_id: int,
        action_type: str,
        status: str,
        symbol: str | None = None,
        rationale: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> AutomationDecisionRecord:
        timestamp = _now_utc()
        with self.session() as session, session.begin():
            row = session.execute(
                insert(self._decisions)
                .values(
                    run_id=run_id,
                    action_type=action_type,
                    symbol=symbol,
                    status=status,
                    rationale=rationale,
                    details=details or {},
                    updated_at=timestamp,
                )
                .returning(*self._decisions.c)
            ).mappings().one()
        return AutomationDecisionRecord(**dict(row))

    def list_active_positions(self, *, account_hash: str) -> list[BrokerPositionRecord]:
        statement = (
            select(*self._positions.c)
            .where(self._positions.c.account_hash == account_hash)
            .where(self._positions.c.is_active.is_(True))
            .order_by(self._positions.c.underlying_symbol, self._positions.c.position_key)
        )
        with self.session() as session:
            rows = session.execute(statement).mappings().all()
        return [BrokerPositionRecord(**dict(row)) for row in rows]