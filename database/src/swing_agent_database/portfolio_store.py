from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Iterator

from sqlalchemy import MetaData, Table, create_engine, insert, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from .config import PortfolioStoreSettings


def _ensure_utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(tz=UTC)
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _to_decimal(value: Decimal | int | float | str) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


@dataclass(frozen=True)
class PortfolioUserRecord:
    id: int
    discord_user_id: int
    username: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class PortfolioRecord:
    id: int
    user_id: int
    name: str
    is_default: bool
    cash_available: Decimal
    cash_reserved: Decimal
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class PositionRecord:
    id: int
    portfolio_id: int
    symbol: str
    asset_type: str
    strategy_type: str
    status: str
    quantity: int
    opened_at: datetime
    closed_at: datetime | None
    notes: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class PositionLegRecord:
    id: int
    position_id: int
    leg_type: str
    status: str
    side: str
    quantity: int
    symbol: str
    option_type: str | None
    strike: Decimal | None
    expiration: date | None
    entry_price: Decimal
    opened_at: datetime
    closed_at: datetime | None
    exit_price: Decimal | None
    notes: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class TradeEventRecord:
    id: int
    position_id: int
    position_leg_id: int | None
    event_type: str
    occurred_at: datetime
    notes: str | None
    details: dict[str, Any]
    created_at: datetime


@dataclass(frozen=True)
class PositionSnapshot:
    position: PositionRecord
    legs: list[PositionLegRecord]
    recent_events: list[TradeEventRecord]


@dataclass(frozen=True)
class PortfolioSnapshot:
    user: PortfolioUserRecord
    portfolio: PortfolioRecord
    open_positions: list[PositionSnapshot]


@dataclass(frozen=True)
class OptionLegInput:
    side: str
    quantity: int
    option_type: str
    strike: Decimal
    expiration: date
    entry_price: Decimal
    notes: str | None = None


class PortfolioStore:
    def __init__(self, settings: PortfolioStoreSettings) -> None:
        self._settings = settings
        self._engine: Engine = create_engine(settings.database_url, pool_pre_ping=True)
        self._metadata = MetaData(schema=settings.schema_name)
        self._users = Table(
            settings.users_table_name,
            self._metadata,
            autoload_with=self._engine,
            schema=settings.schema_name,
        )
        self._portfolios = Table(
            settings.portfolios_table_name,
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
        self._position_legs = Table(
            settings.position_legs_table_name,
            self._metadata,
            autoload_with=self._engine,
            schema=settings.schema_name,
        )
        self._trade_events = Table(
            settings.trade_events_table_name,
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

    def get_or_create_default_portfolio(
        self,
        *,
        discord_user_id: int,
        username: str | None,
    ) -> tuple[PortfolioUserRecord, PortfolioRecord]:
        with self.session() as session, session.begin():
            user = self._get_or_create_user(session, discord_user_id=discord_user_id, username=username)
            portfolio = self._get_or_create_default_portfolio_for_user(session, user.id)
        return user, portfolio

    def set_cash_balances(
        self,
        *,
        discord_user_id: int,
        username: str | None,
        cash_available: Decimal | int | float | str,
        cash_reserved: Decimal | int | float | str = Decimal("0"),
    ) -> PortfolioRecord:
        available = _to_decimal(cash_available)
        reserved = _to_decimal(cash_reserved)
        with self.session() as session, session.begin():
            user = self._get_or_create_user(session, discord_user_id=discord_user_id, username=username)
            portfolio = self._get_or_create_default_portfolio_for_user(session, user.id)
            row = session.execute(
                update(self._portfolios)
                .where(self._portfolios.c.id == portfolio.id)
                .values(
                    cash_available=available,
                    cash_reserved=reserved,
                    updated_at=datetime.now(tz=UTC),
                )
                .returning(*self._portfolios.c)
            ).mappings().one()
        return PortfolioRecord(**dict(row))

    def add_stock_position(
        self,
        *,
        discord_user_id: int,
        username: str | None,
        symbol: str,
        shares: int,
        entry_price: Decimal | int | float | str,
        opened_at: datetime | None = None,
        notes: str | None = None,
    ) -> PositionRecord:
        timestamp = _ensure_utc(opened_at)
        with self.session() as session, session.begin():
            user = self._get_or_create_user(session, discord_user_id=discord_user_id, username=username)
            portfolio = self._get_or_create_default_portfolio_for_user(session, user.id)
            position_row = session.execute(
                insert(self._positions)
                .values(
                    portfolio_id=portfolio.id,
                    symbol=symbol,
                    asset_type="stock",
                    strategy_type="stock",
                    status="open",
                    quantity=shares,
                    opened_at=timestamp,
                    notes=notes,
                    updated_at=timestamp,
                )
                .returning(*self._positions.c)
            ).mappings().one()
            position = PositionRecord(**dict(position_row))
            leg_row = session.execute(
                insert(self._position_legs)
                .values(
                    position_id=position.id,
                    leg_type="stock",
                    status="open",
                    side="buy",
                    quantity=shares,
                    symbol=symbol,
                    entry_price=_to_decimal(entry_price),
                    opened_at=timestamp,
                    notes=notes,
                    updated_at=timestamp,
                )
                .returning(self._position_legs.c.id)
            ).scalar_one()
            self._insert_trade_event(
                session,
                position_id=position.id,
                position_leg_id=leg_row,
                event_type="open",
                occurred_at=timestamp,
                notes=notes or "Opened stock position",
            )
        return position

    def add_option_position(
        self,
        *,
        discord_user_id: int,
        username: str | None,
        symbol: str,
        strategy_type: str,
        quantity: int,
        legs: list[OptionLegInput],
        opened_at: datetime | None = None,
        notes: str | None = None,
    ) -> PositionRecord:
        timestamp = _ensure_utc(opened_at)
        with self.session() as session, session.begin():
            user = self._get_or_create_user(session, discord_user_id=discord_user_id, username=username)
            portfolio = self._get_or_create_default_portfolio_for_user(session, user.id)
            position_row = session.execute(
                insert(self._positions)
                .values(
                    portfolio_id=portfolio.id,
                    symbol=symbol,
                    asset_type="option_strategy",
                    strategy_type=strategy_type,
                    status="open",
                    quantity=quantity,
                    opened_at=timestamp,
                    notes=notes,
                    updated_at=timestamp,
                )
                .returning(*self._positions.c)
            ).mappings().one()
            position = PositionRecord(**dict(position_row))
            for leg in legs:
                leg_id = session.execute(
                    insert(self._position_legs)
                    .values(
                        position_id=position.id,
                        leg_type="option",
                        status="open",
                        side=leg.side,
                        quantity=leg.quantity,
                        symbol=symbol,
                        option_type=leg.option_type,
                        strike=leg.strike,
                        expiration=leg.expiration,
                        entry_price=leg.entry_price,
                        opened_at=timestamp,
                        notes=leg.notes,
                        updated_at=timestamp,
                    )
                    .returning(self._position_legs.c.id)
                ).scalar_one()
                self._insert_trade_event(
                    session,
                    position_id=position.id,
                    position_leg_id=leg_id,
                    event_type="open",
                    occurred_at=timestamp,
                    notes=leg.notes or f"Opened {leg.side} {leg.option_type} leg",
                )
        return position

    def close_position(
        self,
        *,
        discord_user_id: int,
        username: str | None,
        position_id: int,
        exit_price: Decimal | int | float | str | None = None,
        notes: str | None = None,
    ) -> PositionRecord | None:
        timestamp = datetime.now(tz=UTC)
        normalized_exit_price = None if exit_price is None else _to_decimal(exit_price)
        with self.session() as session, session.begin():
            self._get_or_create_user(session, discord_user_id=discord_user_id, username=username)
            position = self._get_open_position_for_user(session, discord_user_id=discord_user_id, position_id=position_id)
            if position is None:
                return None

            position_row = session.execute(
                update(self._positions)
                .where(self._positions.c.id == position_id)
                .values(status="closed", closed_at=timestamp, updated_at=timestamp)
                .returning(*self._positions.c)
            ).mappings().one()
            session.execute(
                update(self._position_legs)
                .where(self._position_legs.c.position_id == position_id)
                .where(self._position_legs.c.status == "open")
                .values(
                    status="closed",
                    closed_at=timestamp,
                    exit_price=normalized_exit_price,
                    updated_at=timestamp,
                )
            )
            self._insert_trade_event(
                session,
                position_id=position_id,
                position_leg_id=None,
                event_type="close",
                occurred_at=timestamp,
                notes=notes or "Closed via Discord bot",
                details={} if normalized_exit_price is None else {"exit_price": str(normalized_exit_price)},
            )
        return PositionRecord(**dict(position_row))

    def add_position_note(
        self,
        *,
        discord_user_id: int,
        username: str | None,
        position_id: int,
        notes: str,
    ) -> bool:
        with self.session() as session, session.begin():
            self._get_or_create_user(session, discord_user_id=discord_user_id, username=username)
            position = self._get_position_for_user(session, discord_user_id=discord_user_id, position_id=position_id)
            if position is None:
                return False
            timestamp = datetime.now(tz=UTC)
            session.execute(
                update(self._positions)
                .where(self._positions.c.id == position_id)
                .values(notes=notes, updated_at=timestamp)
            )
            self._insert_trade_event(
                session,
                position_id=position_id,
                position_leg_id=None,
                event_type="note",
                occurred_at=timestamp,
                notes=notes,
            )
        return True

    def build_portfolio_snapshot(
        self,
        *,
        discord_user_id: int,
        username: str | None,
    ) -> PortfolioSnapshot:
        with self.session() as session, session.begin():
            user = self._get_or_create_user(session, discord_user_id=discord_user_id, username=username)
            portfolio = self._get_or_create_default_portfolio_for_user(session, user.id)

            position_rows = session.execute(
                select(*self._positions.c)
                .where(self._positions.c.portfolio_id == portfolio.id)
                .where(self._positions.c.status == "open")
                .order_by(self._positions.c.opened_at.desc(), self._positions.c.id.desc())
            ).mappings().all()

            position_ids = [row["id"] for row in position_rows]
            leg_map: dict[int, list[PositionLegRecord]] = {position_id: [] for position_id in position_ids}
            event_map: dict[int, list[TradeEventRecord]] = {position_id: [] for position_id in position_ids}

            if position_ids:
                leg_rows = session.execute(
                    select(*self._position_legs.c)
                    .where(self._position_legs.c.position_id.in_(position_ids))
                    .order_by(self._position_legs.c.position_id, self._position_legs.c.id)
                ).mappings().all()
                for row in leg_rows:
                    leg = PositionLegRecord(**dict(row))
                    leg_map[leg.position_id].append(leg)

                event_rows = session.execute(
                    select(*self._trade_events.c)
                    .where(self._trade_events.c.position_id.in_(position_ids))
                    .order_by(self._trade_events.c.position_id, self._trade_events.c.occurred_at.desc(), self._trade_events.c.id.desc())
                ).mappings().all()
                for row in event_rows:
                    event = TradeEventRecord(**dict(row))
                    events = event_map[event.position_id]
                    if len(events) < 5:
                        events.append(event)

        open_positions = [
            PositionSnapshot(
                position=PositionRecord(**dict(position_row)),
                legs=leg_map.get(position_row["id"], []),
                recent_events=event_map.get(position_row["id"], []),
            )
            for position_row in position_rows
        ]
        return PortfolioSnapshot(user=user, portfolio=portfolio, open_positions=open_positions)

    def _get_or_create_user(
        self,
        session: Session,
        *,
        discord_user_id: int,
        username: str | None,
    ) -> PortfolioUserRecord:
        row = session.execute(
            select(*self._users.c).where(self._users.c.discord_user_id == discord_user_id)
        ).mappings().first()
        if row is not None:
            if username and row["username"] != username:
                row = session.execute(
                    update(self._users)
                    .where(self._users.c.id == row["id"])
                    .values(username=username, updated_at=datetime.now(tz=UTC))
                    .returning(*self._users.c)
                ).mappings().one()
            return PortfolioUserRecord(**dict(row))

        row = session.execute(
            insert(self._users)
            .values(discord_user_id=discord_user_id, username=username, updated_at=datetime.now(tz=UTC))
            .returning(*self._users.c)
        ).mappings().one()
        return PortfolioUserRecord(**dict(row))

    def _get_or_create_default_portfolio_for_user(self, session: Session, user_id: int) -> PortfolioRecord:
        row = session.execute(
            select(*self._portfolios.c)
            .where(self._portfolios.c.user_id == user_id)
            .where(self._portfolios.c.is_default.is_(True))
        ).mappings().first()
        if row is None:
            row = session.execute(
                insert(self._portfolios)
                .values(user_id=user_id, name="default", is_default=True, updated_at=datetime.now(tz=UTC))
                .returning(*self._portfolios.c)
            ).mappings().one()
        return PortfolioRecord(**dict(row))

    def _get_position_for_user(self, session: Session, *, discord_user_id: int, position_id: int) -> PositionRecord | None:
        row = session.execute(
            select(*self._positions.c)
            .select_from(
                self._positions.join(self._portfolios, self._positions.c.portfolio_id == self._portfolios.c.id).join(
                    self._users, self._portfolios.c.user_id == self._users.c.id
                )
            )
            .where(self._users.c.discord_user_id == discord_user_id)
            .where(self._positions.c.id == position_id)
        ).mappings().first()
        if row is None:
            return None
        return PositionRecord(**dict(row))

    def _get_open_position_for_user(self, session: Session, *, discord_user_id: int, position_id: int) -> PositionRecord | None:
        row = session.execute(
            select(*self._positions.c)
            .select_from(
                self._positions.join(self._portfolios, self._positions.c.portfolio_id == self._portfolios.c.id).join(
                    self._users, self._portfolios.c.user_id == self._users.c.id
                )
            )
            .where(self._users.c.discord_user_id == discord_user_id)
            .where(self._positions.c.id == position_id)
            .where(self._positions.c.status == "open")
        ).mappings().first()
        if row is None:
            return None
        return PositionRecord(**dict(row))

    def _insert_trade_event(
        self,
        session: Session,
        *,
        position_id: int,
        position_leg_id: int | None,
        event_type: str,
        occurred_at: datetime,
        notes: str | None,
        details: dict[str, Any] | None = None,
    ) -> None:
        session.execute(
            insert(self._trade_events).values(
                position_id=position_id,
                position_leg_id=position_leg_id,
                event_type=event_type,
                occurred_at=occurred_at,
                notes=notes,
                details=details or {},
            )
        )