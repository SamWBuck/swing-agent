"""Discord bot backed by GitHub Copilot SDK (copilot package) with MCP tools.

Run via VS Code launch config or: python discord_bot.py
Requires DISCORD_BOT_TOKEN in .env (root directory).
MCP services must be reachable at their configured localhost ports.
"""

import asyncio
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

import discord
from discord import app_commands
from copilot import CopilotClient, PermissionHandler
from dotenv import load_dotenv
from swing_agent_database import (
    OptionLegInput,
    PortfolioSnapshot,
    PortfolioStore,
    SymbolAvailabilityStore,
    load_portfolio_store_settings,
    load_symbol_availability_settings,
)
import yfinance as yf

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logging.getLogger("discord").setLevel(logging.WARNING)
logging.getLogger("asyncio").setLevel(logging.WARNING)
log = logging.getLogger(__name__)

DISCORD_BOT_TOKEN = os.environ["DISCORD_BOT_TOKEN"]
DISCORD_INCOME_AGENT_CHANNEL_ID = int(os.environ["DISCORD_INCOME_AGENT_CHANNEL_ID"])

DEFAULT_PROMPT_PATH = BASE_DIR / "options-income.prompt.md"
CHANNEL_PROMPT_PATHS = {
    DISCORD_INCOME_AGENT_CHANNEL_ID: BASE_DIR / "options-income.prompt.md",
}
ALLOWED_CHANNEL_IDS = set(CHANNEL_PROMPT_PATHS)
DISCORD_PROMPT_SUFFIX = """

Additional runtime instructions:

- Respond with Discord-compatible markdown.
- Keep answers readable in chat: short sections, compact bullets, and no tables wider than necessary.
- If tool output is incomplete or unavailable, say so directly instead of guessing.
- If MCP tools are unavailable in the session, say that clearly and stop instead of exploring files, shells, or subagents.
""".strip()


def _get_system_message(channel_id: int) -> str:
    """Return the system message configured for a Discord channel."""
    prompt_path = CHANNEL_PROMPT_PATHS.get(channel_id, DEFAULT_PROMPT_PATH)
    base_prompt = prompt_path.read_text(encoding="utf-8").strip()
    return f"{base_prompt}\n\n{DISCORD_PROMPT_SUFFIX}"

_MCP_SERVERS: dict = {
    "sec-edgar": {
        "type": "http",
        "url": "http://localhost:9870/mcp",
    },
    "yahoo-finance": {
        "type": "http",
        "url": "http://localhost:8809/mcp",
        "tools": [
            "get_stock_info",
            "get_historical_stock_prices",
            "get_option_expiration_dates",
            "get_option_chain",
            "get_yahoo_finance_news",
            "get_financial_statement",
            "get_holder_info",
            "get_stock_actions",
            "get_recommendations",
        ],
    },
    "price-data": {
        "type": "http",
        "url": "http://localhost:8810/mcp",
        "tools": [
            "describe_price_source",
            "list_symbols",
            "list_intervals",
            "get_raw_candles",
            "get_indicator_catalog",
            "calculate_indicators",
            "get_support_resistance",
            "summarize_market_data",
        ],
    },
}

_EXCLUDED_TOOLS = [
    "glob",
    "grep",
    "powershell",
    "read_agent",
    "report_intent",
    "task",
    "view",
    "web_fetch",
]

_DISCORD_MAX_CHARS = 2000
_SESSION_IDLE_TTL_SECONDS = 15 * 60
_SESSION_RESPONSE_TIMEOUT_SECONDS = 600.0
_SESSION_CLEANUP_INTERVAL_SECONDS = 60.0
_FLOW_IDLE_TTL_SECONDS = 10 * 60

_INTERVAL_COLUMNS = [
    ("1m", "latest_1m_ts"),
    ("5m", "latest_5m_ts"),
    ("10m", "latest_10m_ts"),
    ("15m", "latest_15m_ts"),
    ("30m", "latest_30m_ts"),
    ("1d", "latest_day_ts"),
    ("1w", "latest_week_ts"),
]

_INTERVAL_FRESHNESS = {
    "1m": timedelta(minutes=1),
    "5m": timedelta(minutes=5),
    "10m": timedelta(minutes=10),
    "15m": timedelta(minutes=15),
    "30m": timedelta(minutes=30),
    "1d": timedelta(days=1),
    "1w": timedelta(weeks=1),
}

intents = discord.Intents.default()
intents.message_content = True

discord_client = discord.Client(intents=intents)
command_tree = app_commands.CommandTree(discord_client)
copilot_client = CopilotClient()
_symbol_availability_store: SymbolAvailabilityStore | None = None
_portfolio_store: PortfolioStore | None = None


@dataclass
class UserSessionEntry:
    session: object
    channel_id: int
    last_used_at: float = field(default_factory=time.monotonic)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


@dataclass
class PendingPortfolioFlow:
    action: str
    channel_id: int
    created_at: float = field(default_factory=time.monotonic)
    data: dict = field(default_factory=dict)


_user_sessions: dict[int, UserSessionEntry] = {}
_session_cleanup_task: asyncio.Task | None = None
_portfolio_flows: dict[int, PendingPortfolioFlow] = {}
_portfolio_view_messages: dict[int, int] = {}


def _strip_mention(content: str, bot_user: discord.ClientUser) -> str:
    """Remove @bot mention(s) from message text."""
    return re.sub(rf"<@!?{bot_user.id}>", "", content).strip()


def _is_targeted(message: discord.Message) -> bool:
    """Return True if the bot is directly @mentioned."""
    return discord_client.user in message.mentions


def _is_allowed_channel(channel_id: int) -> bool:
    """Return True if the bot is configured to operate in the channel."""
    return channel_id in ALLOWED_CHANNEL_IDS


def _get_symbol_availability_store() -> SymbolAvailabilityStore:
    global _symbol_availability_store

    if _symbol_availability_store is None:
        _symbol_availability_store = SymbolAvailabilityStore(
            load_symbol_availability_settings(consumer_name="discord-bot")
        )
    return _symbol_availability_store


def _get_portfolio_store() -> PortfolioStore:
    global _portfolio_store

    if _portfolio_store is None:
        _portfolio_store = PortfolioStore(load_portfolio_store_settings(consumer_name="discord-bot"))
    return _portfolio_store


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _format_age(delta: timedelta) -> str:
    total_seconds = max(int(delta.total_seconds()), 0)
    if total_seconds < 60:
        return f"{total_seconds}s"

    minutes = total_seconds // 60
    if minutes < 60:
        return f"{minutes}m"

    hours = minutes // 60
    if hours < 24:
        return f"{hours}h"

    days = hours // 24
    if days < 7:
        return f"{days}d"

    weeks = days // 7
    return f"{weeks}w"


def _format_freshness_cell(timestamp: datetime | None, interval: str, now: datetime) -> str:
    if timestamp is None:
        return "MISS"

    age = now - _ensure_utc(timestamp)
    status = "F" if age <= _INTERVAL_FRESHNESS[interval] else "S"
    return f"{status} {_format_age(age)}"


def _row_needs_sync(row: object, now: datetime) -> bool:
    for interval, attribute in _INTERVAL_COLUMNS:
        timestamp = getattr(row, attribute)
        if timestamp is None:
            return True
        if now - _ensure_utc(timestamp) > _INTERVAL_FRESHNESS[interval]:
            return True
    return False


def _render_symbol_availability_table(rows: list[object], *, title: str) -> str:
    now = datetime.now(tz=UTC)
    symbol_width = max(len("SYMBOL"), *(len(row.symbol) for row in rows))
    header = ["SYMBOL".ljust(symbol_width), *[interval.rjust(5) for interval, _ in _INTERVAL_COLUMNS]]
    lines = [title, "F=within window | S=stale | MISS=not loaded yet", "```text", " ".join(header)]

    for row in rows:
        cells = [row.symbol.ljust(symbol_width)]
        for interval, attribute in _INTERVAL_COLUMNS:
            cells.append(_format_freshness_cell(getattr(row, attribute), interval, now).rjust(5))
        lines.append(" ".join(cells))

    lines.append("```")
    return "\n".join(lines)


def _normalize_symbol(raw_symbol: str) -> str:
    return raw_symbol.strip().upper()


def _parse_decimal(raw_value: str, *, field_name: str) -> Decimal:
    try:
        return Decimal(raw_value)
    except InvalidOperation as exc:
        raise ValueError(f"Invalid {field_name}: `{raw_value}`") from exc


def _parse_positive_int(raw_value: str, *, field_name: str) -> int:
    try:
        parsed = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"Invalid {field_name}: `{raw_value}`") from exc
    if parsed <= 0:
        raise ValueError(f"{field_name.capitalize()} must be positive.")
    return parsed


def _validate_command_symbol(symbol: str) -> str | None:
    if not symbol:
        return "Usage: `add <ticker>`"
    if not re.fullmatch(r"[A-Z][A-Z0-9./-]*", symbol):
        return f"`{symbol}` is not a supported ticker format."
    return None


def _verify_ticker_exists(symbol: str) -> tuple[bool, str]:
    ticker = yf.Ticker(symbol)
    history = ticker.history(period="5d", interval="1d", auto_adjust=False)
    if history.empty:
        return False, f"Yahoo Finance did not return recent price history for `{symbol}`."

    metadata = getattr(ticker, "history_metadata", None) or {}
    instrument_type = metadata.get("instrumentType")
    if instrument_type and instrument_type not in {"EQUITY", "ETF", "INDEX", "MUTUALFUND"}:
        return False, f"`{symbol}` resolved to unsupported instrument type `{instrument_type}`."

    return True, ""


def _is_symbol_tracked(symbol: str) -> bool:
    return _get_symbol_availability_store().get_symbol(symbol) is not None


def _format_money(value: Decimal) -> str:
    return f"${value:,.2f}"


def _format_leg_summary(leg: object) -> str:
    entry_price = Decimal(leg.entry_price)
    if leg.leg_type == "stock":
        return f"stock {leg.quantity} @ {_format_money(entry_price)}"

    expiration = leg.expiration.isoformat() if leg.expiration is not None else "n/a"
    return (
        f"{leg.side} {leg.quantity} {leg.option_type} {leg.strike} {expiration} "
        f"@ {_format_money(entry_price)}"
    )


def _render_portfolio_summary(snapshot: PortfolioSnapshot) -> str:
    lines = [
        f"Portfolio `{snapshot.portfolio.name}`",
        f"Cash available: {_format_money(snapshot.portfolio.cash_available)}",
        f"Cash reserved: {_format_money(snapshot.portfolio.cash_reserved)}",
    ]

    if not snapshot.open_positions:
        lines.append("Open positions: none")
        return "\n".join(lines)

    lines.append("Open positions:")
    for item in snapshot.open_positions:
        position = item.position
        lines.append(
            f"- #{position.id} {position.symbol} | {position.strategy_type} | qty {position.quantity} | opened {position.opened_at.date().isoformat()}"
        )
        for leg in item.legs:
            lines.append(f"  - {_format_leg_summary(leg)}")

    return "\n".join(lines)


def _build_portfolio_context(snapshot: PortfolioSnapshot) -> str:
    lines = [
        "Saved portfolio context:",
        f"- Portfolio: {snapshot.portfolio.name}",
        f"- Cash available: {_format_money(snapshot.portfolio.cash_available)}",
        f"- Cash reserved: {_format_money(snapshot.portfolio.cash_reserved)}",
    ]

    if not snapshot.open_positions:
        lines.append("- Open positions: none")
        return "\n".join(lines)

    lines.append("- Open positions:")
    for item in snapshot.open_positions:
        position = item.position
        lines.append(
            f"  - Position #{position.id}: {position.symbol} {position.strategy_type} qty={position.quantity} opened={position.opened_at.isoformat()} notes={position.notes or 'none'}"
        )
        for leg in item.legs:
            lines.append(f"    - Leg #{leg.id}: {_format_leg_summary(leg)}")
        for event in item.recent_events:
            lines.append(
                f"    - Event: {event.event_type} at {event.occurred_at.isoformat()} notes={event.notes or 'none'}"
            )

    return "\n".join(lines)


def _get_active_portfolio_flow(user_id: int, channel_id: int) -> PendingPortfolioFlow | None:
    flow = _portfolio_flows.get(user_id)
    if flow is None:
        return None
    if flow.channel_id != channel_id:
        return None
    if time.monotonic() - flow.created_at >= _FLOW_IDLE_TTL_SECONDS:
        _portfolio_flows.pop(user_id, None)
        return None
    return flow


def _set_portfolio_flow(user_id: int, flow: PendingPortfolioFlow) -> None:
    flow.created_at = time.monotonic()
    _portfolio_flows[user_id] = flow


def _clear_portfolio_flow(user_id: int) -> None:
    _portfolio_flows.pop(user_id, None)


def _build_portfolio_panel_content(snapshot: PortfolioSnapshot) -> str:
    lines = [
        f"Portfolio `{snapshot.portfolio.name}`",
        f"Cash available: {_format_money(snapshot.portfolio.cash_available)}",
        f"Cash reserved: {_format_money(snapshot.portfolio.cash_reserved)}",
        "",
        "Positions:",
    ]

    if not snapshot.open_positions:
        lines.append("- none")
        lines.append("")
        lines.append("Use the buttons below to open modals for cash and position entry, or confirm the portfolio for analysis.")
        return "\n".join(lines)

    for item in snapshot.open_positions:
        position = item.position
        lines.append(
            f"- #{position.id} {position.symbol} | {position.strategy_type} | qty {position.quantity} | opened {position.opened_at.date().isoformat()}"
        )
        for leg in item.legs:
            lines.append(f"  {_format_leg_summary(leg)}")

    lines.append("")
    lines.append("Use the buttons below to open modals for edits, log exits, refresh the panel, or confirm the portfolio for analysis.")
    return "\n".join(lines)


def _is_empty_portfolio(snapshot: PortfolioSnapshot) -> bool:
    return (
        not snapshot.open_positions
        and snapshot.portfolio.cash_available == Decimal("0")
        and snapshot.portfolio.cash_reserved == Decimal("0")
    )


def _build_request_prompt_for_user(user_id: int, username: str | None, prompt: str) -> str:
    snapshot = _get_portfolio_store().build_portfolio_snapshot(
        discord_user_id=user_id,
        username=username,
    )
    portfolio_context = _build_portfolio_context(snapshot)
    if not portfolio_context:
        return prompt
    return f"{portfolio_context}\n\nUser request:\n{prompt}"


async def _run_recommendation_request(
    *,
    user_id: int,
    username: str | None,
    channel_id: int,
    prompt: str,
) -> str:
    request_prompt = await asyncio.to_thread(_build_request_prompt_for_user, user_id, username, prompt)
    entry = await _get_or_create_user_session(user_id, channel_id)

    async with entry.lock:
        entry.last_used_at = time.monotonic()
        response_event = await entry.session.send_and_wait(
            {"prompt": request_prompt},
            timeout=_SESSION_RESPONSE_TIMEOUT_SECONDS,
        )
        entry.last_used_at = time.monotonic()
        return _extract_response_text(response_event)


def _parse_option_legs(raw_value: str) -> list[OptionLegInput]:
    legs: list[OptionLegInput] = []
    for raw_line in raw_value.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) != 6:
            raise ValueError(
                "Each option leg must use `<buy|sell> <call|put> <quantity> <strike> <expiration YYYY-MM-DD> <entry_price>`."
            )
        side = parts[0].lower()
        option_type = parts[1].lower()
        if side not in {"buy", "sell"} or option_type not in {"call", "put"}:
            raise ValueError("Option legs must use `buy|sell` and `call|put`.")
        quantity = _parse_positive_int(parts[2], field_name="quantity")
        strike = _parse_decimal(parts[3], field_name="strike")
        expiration = datetime.fromisoformat(parts[4]).date()
        entry_price = _parse_decimal(parts[5], field_name="entry price")
        legs.append(
            OptionLegInput(
                side=side,
                quantity=quantity,
                option_type=option_type,
                strike=strike,
                expiration=expiration,
                entry_price=entry_price,
            )
        )
    if not legs:
        raise ValueError("Enter at least one option leg.")
    return legs


async def _refresh_portfolio_panel(interaction: discord.Interaction, view: "PortfolioPanelView") -> None:
    snapshot = await asyncio.to_thread(
        _get_portfolio_store().build_portfolio_snapshot,
        discord_user_id=view.owner_user_id,
        username=view.owner_username,
    )
    refreshed_view = PortfolioPanelView(
        owner_user_id=view.owner_user_id,
        owner_username=view.owner_username,
        channel_id=view.channel_id,
        snapshot=snapshot,
    )
    await interaction.response.edit_message(
        content=_build_portfolio_panel_content(snapshot),
        view=refreshed_view,
    )


async def _respond_with_portfolio_panel(
    interaction: discord.Interaction,
    *,
    owner_user_id: int,
    owner_username: str | None,
    channel_id: int,
    edit_existing: bool,
) -> None:
    log.info(
        "Rendering portfolio panel for user=%s channel=%s edit_existing=%s",
        owner_user_id,
        channel_id,
        edit_existing,
    )
    snapshot = await asyncio.to_thread(
        _get_portfolio_store().build_portfolio_snapshot,
        discord_user_id=owner_user_id,
        username=owner_username,
    )
    view = PortfolioPanelView(
        owner_user_id=owner_user_id,
        owner_username=owner_username,
        channel_id=channel_id,
        snapshot=snapshot,
    )
    content = _build_portfolio_panel_content(snapshot)
    if edit_existing:
        await interaction.response.edit_message(content=content, view=view)
        return
    await interaction.response.send_message(content, view=view)


async def _send_portfolio_panel_message(
    message: discord.Message,
    *,
    owner_user_id: int,
    owner_username: str | None,
) -> None:
    snapshot = await asyncio.to_thread(
        _get_portfolio_store().build_portfolio_snapshot,
        discord_user_id=owner_user_id,
        username=owner_username,
    )
    view = PortfolioPanelView(
        owner_user_id=owner_user_id,
        owner_username=owner_username,
        channel_id=message.channel.id,
        snapshot=snapshot,
    )
    await message.reply(_build_portfolio_panel_content(snapshot), view=view)


async def _open_setup_modal_or_fallback(
    interaction: discord.Interaction,
    *,
    view: "PortfolioPanelView",
    title: str,
    edit_existing_message: bool,
) -> None:
    log.info(
        "Attempting portfolio modal launch for user=%s channel=%s title=%s edit_existing=%s",
        view.owner_user_id,
        view.channel_id,
        title,
        edit_existing_message,
    )
    try:
        await interaction.response.send_modal(
            SetCashModal(
                view=view,
                title=title,
                edit_existing_message=edit_existing_message,
            )
        )
        log.info("Portfolio modal launch sent for user=%s", view.owner_user_id)
    except Exception:
        log.exception("Portfolio modal launch failed for user=%s; falling back to panel", view.owner_user_id)
        if interaction.response.is_done():
            await interaction.followup.send(
                "I couldn't open the setup modal directly. Use the portfolio panel buttons instead.",
                view=view,
            )
            return
        await interaction.response.send_message(
            "I couldn't open the setup modal directly. Use the portfolio panel buttons instead.",
            view=view,
        )


class PortfolioActionModal(discord.ui.Modal):
    def __init__(self, *, title: str, view: "PortfolioPanelView", edit_existing_message: bool = True) -> None:
        super().__init__(title=title)
        self.portfolio_view = view
        self.edit_existing_message = edit_existing_message


class SetCashModal(PortfolioActionModal):
    available = discord.ui.TextInput(label="Cash available", required=True, placeholder="25000")
    reserved = discord.ui.TextInput(label="Cash reserved", required=False, placeholder="5000")

    def __init__(self, *, view: "PortfolioPanelView", title: str = "Set Portfolio Cash", edit_existing_message: bool = True) -> None:
        super().__init__(title=title, view=view, edit_existing_message=edit_existing_message)
        self.available.default = f"{view.snapshot.portfolio.cash_available:.2f}"
        self.reserved.default = f"{view.snapshot.portfolio.cash_reserved:.2f}"

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            available = _parse_decimal(str(self.available), field_name="cash available")
            reserved_value = str(self.reserved).strip()
            reserved = Decimal("0") if not reserved_value else _parse_decimal(reserved_value, field_name="cash reserved")
            await asyncio.to_thread(
                _get_portfolio_store().set_cash_balances,
                discord_user_id=self.portfolio_view.owner_user_id,
                username=self.portfolio_view.owner_username,
                cash_available=available,
                cash_reserved=reserved,
            )
            await _respond_with_portfolio_panel(
                interaction,
                owner_user_id=self.portfolio_view.owner_user_id,
                owner_username=self.portfolio_view.owner_username,
                channel_id=self.portfolio_view.channel_id,
                edit_existing=self.edit_existing_message,
            )
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)


class AddStockModal(PortfolioActionModal):
    symbol = discord.ui.TextInput(label="Ticker", required=True, placeholder="NVDA")
    shares = discord.ui.TextInput(label="Shares", required=True, placeholder="100")
    entry_price = discord.ui.TextInput(label="Entry price", required=True, placeholder="118.45")
    notes = discord.ui.TextInput(label="Notes", required=False, style=discord.TextStyle.paragraph)

    def __init__(self, *, view: "PortfolioPanelView", edit_existing_message: bool = True) -> None:
        super().__init__(title="Add Stock Position", view=view, edit_existing_message=edit_existing_message)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            symbol = _normalize_symbol(str(self.symbol))
            validation_error = _validate_command_symbol(symbol)
            if validation_error is not None:
                raise ValueError(validation_error)
            is_tracked = await asyncio.to_thread(_is_symbol_tracked, symbol)
            if not is_tracked:
                raise ValueError(f"`{symbol}` is not in `symbol_availability`. Add it first so the bot can analyze it.")
            shares = _parse_positive_int(str(self.shares), field_name="shares")
            entry_price = _parse_decimal(str(self.entry_price), field_name="entry price")
            notes = str(self.notes).strip() or None
            await asyncio.to_thread(
                _get_portfolio_store().add_stock_position,
                discord_user_id=self.portfolio_view.owner_user_id,
                username=self.portfolio_view.owner_username,
                symbol=symbol,
                shares=shares,
                entry_price=entry_price,
                notes=notes,
            )
            await _respond_with_portfolio_panel(
                interaction,
                owner_user_id=self.portfolio_view.owner_user_id,
                owner_username=self.portfolio_view.owner_username,
                channel_id=self.portfolio_view.channel_id,
                edit_existing=self.edit_existing_message,
            )
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)


class AddOptionModal(PortfolioActionModal):
    symbol = discord.ui.TextInput(label="Ticker", required=True, placeholder="PLTR")
    strategy_type = discord.ui.TextInput(label="Strategy type", required=True, placeholder="csp")
    legs = discord.ui.TextInput(
        label="Legs",
        required=True,
        style=discord.TextStyle.paragraph,
        placeholder="sell put 1 80 2026-04-17 2.15",
    )
    notes = discord.ui.TextInput(label="Notes", required=False, style=discord.TextStyle.paragraph)

    def __init__(self, *, view: "PortfolioPanelView", edit_existing_message: bool = True) -> None:
        super().__init__(title="Add Option Position", view=view, edit_existing_message=edit_existing_message)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            symbol = _normalize_symbol(str(self.symbol))
            validation_error = _validate_command_symbol(symbol)
            if validation_error is not None:
                raise ValueError(validation_error)
            is_tracked = await asyncio.to_thread(_is_symbol_tracked, symbol)
            if not is_tracked:
                raise ValueError(f"`{symbol}` is not in `symbol_availability`. Add it first so the bot can analyze it.")
            legs = _parse_option_legs(str(self.legs))
            strategy_type = str(self.strategy_type).strip().lower()
            notes = str(self.notes).strip() or None
            quantity = max(leg.quantity for leg in legs)
            await asyncio.to_thread(
                _get_portfolio_store().add_option_position,
                discord_user_id=self.portfolio_view.owner_user_id,
                username=self.portfolio_view.owner_username,
                symbol=symbol,
                strategy_type=strategy_type,
                quantity=quantity,
                legs=legs,
                notes=notes,
            )
            await _respond_with_portfolio_panel(
                interaction,
                owner_user_id=self.portfolio_view.owner_user_id,
                owner_username=self.portfolio_view.owner_username,
                channel_id=self.portfolio_view.channel_id,
                edit_existing=self.edit_existing_message,
            )
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)


class ExitPositionModal(PortfolioActionModal):
    exit_price = discord.ui.TextInput(label="Exit price", required=True, placeholder="3.25")
    notes = discord.ui.TextInput(label="Notes", required=False, style=discord.TextStyle.paragraph)

    def __init__(self, *, view: "PortfolioPanelView", position_id: int, symbol: str, edit_existing_message: bool = True) -> None:
        super().__init__(title=f"Exit {symbol} #{position_id}", view=view, edit_existing_message=edit_existing_message)
        self.position_id = position_id
        self.symbol = symbol

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            exit_price = _parse_decimal(str(self.exit_price), field_name="exit price")
            notes = str(self.notes).strip() or None
            position = await asyncio.to_thread(
                _get_portfolio_store().close_position,
                discord_user_id=self.portfolio_view.owner_user_id,
                username=self.portfolio_view.owner_username,
                position_id=self.position_id,
                exit_price=exit_price,
                notes=notes,
            )
            if position is None:
                raise ValueError(f"Position `{self.position_id}` is no longer open.")
            await _respond_with_portfolio_panel(
                interaction,
                owner_user_id=self.portfolio_view.owner_user_id,
                owner_username=self.portfolio_view.owner_username,
                channel_id=self.portfolio_view.channel_id,
                edit_existing=self.edit_existing_message,
            )
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)


class ExitPositionButton(discord.ui.Button):
    def __init__(self, *, position_id: int, symbol: str, row: int) -> None:
        super().__init__(label=f"Exit #{position_id}", style=discord.ButtonStyle.danger, row=row)
        self.position_id = position_id
        self.symbol = symbol

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, PortfolioPanelView):
            await interaction.response.send_message("The portfolio panel is no longer active.", ephemeral=True)
            return
        await interaction.response.send_modal(
            ExitPositionModal(view=view, position_id=self.position_id, symbol=self.symbol)
        )


class PortfolioPanelView(discord.ui.View):
    def __init__(
        self,
        *,
        owner_user_id: int,
        owner_username: str | None,
        channel_id: int,
        snapshot: PortfolioSnapshot,
    ) -> None:
        super().__init__(timeout=15 * 60)
        self.owner_user_id = owner_user_id
        self.owner_username = owner_username
        self.channel_id = channel_id
        self.snapshot = snapshot
        self._add_exit_buttons()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_user_id:
            await interaction.response.send_message("This portfolio panel belongs to another user.", ephemeral=True)
            return False
        return True

    def _add_exit_buttons(self) -> None:
        max_positions = 20
        for index, item in enumerate(self.snapshot.open_positions[:max_positions]):
            row = 1 + (index // 5)
            self.add_item(ExitPositionButton(position_id=item.position.id, symbol=item.position.symbol, row=row))

    @discord.ui.button(label="Set Cash", style=discord.ButtonStyle.secondary, row=0)
    async def set_cash(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_setup_modal_or_fallback(
            interaction,
            view=self,
            title="Set Portfolio Cash",
            edit_existing_message=True,
        )

    @discord.ui.button(label="Add Stock", style=discord.ButtonStyle.secondary, row=0)
    async def add_stock(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(AddStockModal(view=self))

    @discord.ui.button(label="Add Option", style=discord.ButtonStyle.secondary, row=0)
    async def add_option(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(AddOptionModal(view=self))

    @discord.ui.button(label="Confirm Portfolio", style=discord.ButtonStyle.primary, row=0)
    async def confirm_portfolio(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer(thinking=True)
        try:
            response_text = await _run_recommendation_request(
                user_id=self.owner_user_id,
                username=self.owner_username,
                channel_id=self.channel_id,
                prompt=(
                    "I confirm this is my current portfolio. Analyze my holdings, cash, and current market indicators. "
                    "Tell me what to close, what to roll or manage, what to leave alone, and what new income trades to open next."
                ),
            )
            await _send_followup_chunked(interaction, response_text)
        except Exception:
            log.exception("Portfolio confirmation analysis failed for user %d", self.owner_user_id)
            await interaction.followup.send("I couldn't analyze the confirmed portfolio cleanly. Try again.", ephemeral=True)

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary, row=0)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _refresh_portfolio_panel(interaction, self)


async def _handle_availability_command(message: discord.Message) -> None:
    try:
        rows = await asyncio.to_thread(_get_symbol_availability_store().list_symbol_availability)
    except Exception:
        log.exception("Failed to list symbol availability")
        await message.reply("I couldn't read symbol availability from the database.")
        return

    if not rows:
        await message.reply("No tracked symbols are present in `symbol_availability`.")
        return

    await _reply_to_message_chunked(
        message,
        _render_symbol_availability_table(rows, title="Tracked Symbol Freshness"),
    )


async def _handle_needs_sync_command(message: discord.Message) -> None:
    try:
        rows = await asyncio.to_thread(_get_symbol_availability_store().list_symbol_availability)
    except Exception:
        log.exception("Failed to list symbol availability for needs-sync")
        await message.reply("I couldn't read symbol availability from the database.")
        return

    if not rows:
        await message.reply("No tracked symbols are present in `symbol_availability`.")
        return

    now = datetime.now(tz=UTC)
    filtered = [row for row in rows if _row_needs_sync(row, now)]
    if not filtered:
        await message.reply("All tracked symbols are currently fresh across every interval.")
        return

    await _reply_to_message_chunked(
        message,
        _render_symbol_availability_table(filtered, title="Symbols Needing Sync"),
    )


async def _handle_add_symbol_command(message: discord.Message, raw_symbol: str) -> None:
    symbol = _normalize_symbol(raw_symbol)
    validation_error = _validate_command_symbol(symbol)
    if validation_error is not None:
        await message.reply(validation_error)
        return

    try:
        existing = await asyncio.to_thread(_get_symbol_availability_store().get_symbol, symbol)
    except Exception:
        log.exception("Failed to query symbol availability for %s", symbol)
        await message.reply("I couldn't query the database to check whether that symbol is already tracked.")
        return

    if existing is not None:
        await message.reply(f"`{symbol}` is already present in `symbol_availability`.")
        return

    try:
        is_valid, error_message = await asyncio.to_thread(_verify_ticker_exists, symbol)
    except Exception:
        log.exception("Ticker validation failed for %s", symbol)
        await message.reply(f"I couldn't validate `{symbol}` against Yahoo Finance.")
        return

    if not is_valid:
        await message.reply(error_message)
        return

    try:
        created = await asyncio.to_thread(_get_symbol_availability_store().add_symbol, symbol)
    except Exception:
        log.exception("Failed to insert symbol availability for %s", symbol)
        await message.reply(f"I validated `{symbol}`, but I couldn't insert it into `symbol_availability`.")
        return

    if not created:
        await message.reply(f"`{symbol}` was already added by another process.")
        return

    await message.reply(
        f"Added `{symbol}` to `symbol_availability` with null freshness columns. "
        "The next sync run will pick it up."
    )


async def _handle_portfolio_flow(message: discord.Message, flow: PendingPortfolioFlow) -> bool:
    content = message.content.strip()
    if content.lower() == "cancel":
        _clear_portfolio_flow(message.author.id)
        await message.reply("Cancelled portfolio entry.")
        return True

    flow.created_at = time.monotonic()

    try:
        if flow.action == "set_cash":
            parts = content.split()
            if len(parts) not in {1, 2}:
                await message.reply("Reply with `available` or `available reserved`, or `cancel`.")
                return True
            available = _parse_decimal(parts[0], field_name="available cash")
            reserved = _parse_decimal(parts[1], field_name="reserved cash") if len(parts) == 2 else Decimal("0")
            portfolio = await asyncio.to_thread(
                _get_portfolio_store().set_cash_balances,
                discord_user_id=message.author.id,
                username=message.author.name,
                cash_available=available,
                cash_reserved=reserved,
            )
            _clear_portfolio_flow(message.author.id)
            await message.reply(
                f"Updated cash for `{portfolio.name}`: available {_format_money(portfolio.cash_available)}, reserved {_format_money(portfolio.cash_reserved)}."
            )
            return True

        if flow.action == "add_stock":
            parts = content.split(maxsplit=3)
            if len(parts) < 3:
                await message.reply("Reply with `<ticker> <shares> <entry_price> [notes]`, or `cancel`.")
                return True
            symbol = _normalize_symbol(parts[0])
            validation_error = _validate_command_symbol(symbol)
            if validation_error is not None:
                await message.reply(validation_error)
                return True
            if not await asyncio.to_thread(_is_symbol_tracked, symbol):
                await message.reply(f"`{symbol}` is not in `symbol_availability`. Add it first so the bot can analyze it.")
                return True
            shares = _parse_positive_int(parts[1], field_name="shares")
            entry_price = _parse_decimal(parts[2], field_name="entry price")
            notes = parts[3] if len(parts) == 4 else None
            position = await asyncio.to_thread(
                _get_portfolio_store().add_stock_position,
                discord_user_id=message.author.id,
                username=message.author.name,
                symbol=symbol,
                shares=shares,
                entry_price=entry_price,
                notes=notes,
            )
            _clear_portfolio_flow(message.author.id)
            await message.reply(f"Added stock position #{position.id} for `{symbol}`.")
            return True

        if flow.action == "add_option_header":
            parts = content.split(maxsplit=2)
            if len(parts) < 2:
                await message.reply("Reply with `<ticker> <strategy_type> [notes]`, or `cancel`.")
                return True
            symbol = _normalize_symbol(parts[0])
            validation_error = _validate_command_symbol(symbol)
            if validation_error is not None:
                await message.reply(validation_error)
                return True
            if not await asyncio.to_thread(_is_symbol_tracked, symbol):
                await message.reply(f"`{symbol}` is not in `symbol_availability`. Add it first so the bot can analyze it.")
                return True
            flow.action = "add_option_legs"
            flow.data = {
                "symbol": symbol,
                "strategy_type": parts[1].lower(),
                "notes": parts[2] if len(parts) == 3 else None,
                "legs": [],
            }
            await message.reply(
                "Leg 1: reply with `<buy|sell> <call|put> <quantity> <strike> <expiration YYYY-MM-DD> <entry_price>`. "
                "Send more legs using the same format, then send `done`."
            )
            return True

        if flow.action == "add_option_legs":
            if content.lower() == "done":
                legs: list[OptionLegInput] = flow.data.get("legs", [])
                if not legs:
                    await message.reply("Add at least one leg before sending `done`.")
                    return True
                quantity = max(leg.quantity for leg in legs)
                position = await asyncio.to_thread(
                    _get_portfolio_store().add_option_position,
                    discord_user_id=message.author.id,
                    username=message.author.name,
                    symbol=flow.data["symbol"],
                    strategy_type=flow.data["strategy_type"],
                    quantity=quantity,
                    legs=legs,
                    notes=flow.data.get("notes"),
                )
                _clear_portfolio_flow(message.author.id)
                await message.reply(f"Added option position #{position.id} for `{flow.data['symbol']}`.")
                return True

            parts = content.split()
            if len(parts) != 6:
                await message.reply(
                    "Reply with `<buy|sell> <call|put> <quantity> <strike> <expiration YYYY-MM-DD> <entry_price>`, `done`, or `cancel`."
                )
                return True
            side = parts[0].lower()
            option_type = parts[1].lower()
            if side not in {"buy", "sell"} or option_type not in {"call", "put"}:
                await message.reply("Leg side must be `buy` or `sell`, and option type must be `call` or `put`.")
                return True
            quantity = _parse_positive_int(parts[2], field_name="quantity")
            strike = _parse_decimal(parts[3], field_name="strike")
            expiration = datetime.fromisoformat(parts[4]).date()
            entry_price = _parse_decimal(parts[5], field_name="entry price")
            legs = flow.data.setdefault("legs", [])
            legs.append(
                OptionLegInput(
                    side=side,
                    quantity=quantity,
                    option_type=option_type,
                    strike=strike,
                    expiration=expiration,
                    entry_price=entry_price,
                )
            )
            await message.reply(f"Added leg {len(legs)}. Send another leg or `done`.")
            return True
    except ValueError as exc:
        await message.reply(str(exc))
        return True
    except Exception:
        log.exception("Portfolio flow failed for action %s", flow.action)
        _clear_portfolio_flow(message.author.id)
        await message.reply("I couldn't complete that portfolio action. Start it again with a fresh command.")
        return True

    return False


async def _handle_portfolio_command(message: discord.Message, raw_command: str) -> None:
    parts = raw_command.split(maxsplit=2)

    try:
        if len(parts) == 1 or parts[1].lower() in {"summary", "positions"}:
            await _send_portfolio_panel_message(
                message,
                owner_user_id=message.author.id,
                owner_username=message.author.name,
            )
            return

        subcommand = parts[1].lower()
        if subcommand == "set-cash":
            _set_portfolio_flow(message.author.id, PendingPortfolioFlow(action="set_cash", channel_id=message.channel.id))
            await message.reply("Reply with `available` or `available reserved`, or `cancel`.")
            return

        if subcommand == "add-stock":
            _set_portfolio_flow(message.author.id, PendingPortfolioFlow(action="add_stock", channel_id=message.channel.id))
            await message.reply("Reply with `<ticker> <shares> <entry_price> [notes]`, or `cancel`.")
            return

        if subcommand == "add-option":
            _set_portfolio_flow(message.author.id, PendingPortfolioFlow(action="add_option_header", channel_id=message.channel.id))
            await message.reply("Reply with `<ticker> <strategy_type> [notes]`, or `cancel`.")
            return

        if subcommand == "close":
            if len(parts) < 3:
                await message.reply("Usage: `portfolio close <position_id> [note]`")
                return
            close_parts = parts[2].split(maxsplit=1)
            position_id = _parse_positive_int(close_parts[0], field_name="position id")
            note = close_parts[1] if len(close_parts) == 2 else None
            position = await asyncio.to_thread(
                _get_portfolio_store().close_position,
                discord_user_id=message.author.id,
                username=message.author.name,
                position_id=position_id,
                notes=note,
            )
            if position is None:
                await message.reply(f"Open position `{position_id}` was not found in your portfolio.")
                return
            await message.reply(f"Closed position #{position.id} for `{position.symbol}`.")
            return

        if subcommand == "note":
            if len(parts) < 3:
                await message.reply("Usage: `portfolio note <position_id> <note>`")
                return
            note_parts = parts[2].split(maxsplit=1)
            if len(note_parts) != 2:
                await message.reply("Usage: `portfolio note <position_id> <note>`")
                return
            position_id = _parse_positive_int(note_parts[0], field_name="position id")
            updated = await asyncio.to_thread(
                _get_portfolio_store().add_position_note,
                discord_user_id=message.author.id,
                username=message.author.name,
                position_id=position_id,
                notes=note_parts[1],
            )
            if not updated:
                await message.reply(f"Position `{position_id}` was not found in your portfolio.")
                return
            await message.reply(f"Saved note on position #{position_id}.")
            return
    except ValueError as exc:
        await message.reply(str(exc))
        return
    except Exception:
        log.exception("Portfolio command failed for user %d", message.author.id)
        await message.reply("I couldn't update your portfolio right now.")
        return

    await message.reply(
        "Portfolio commands: `portfolio`, `portfolio set-cash`, `portfolio add-stock`, `portfolio add-option`, `portfolio close`, `portfolio note`."
    )


async def _maybe_handle_command(message: discord.Message, prompt: str) -> bool:
    command, _, remainder = prompt.partition(" ")
    normalized = command.lower().lstrip("/")

    if normalized in {"availability", "freshness", "symbols"}:
        await _handle_availability_command(message)
        return True

    if normalized in {"needs-sync", "stale", "missing", "gaps"}:
        await _handle_needs_sync_command(message)
        return True

    if normalized == "add":
        await _handle_add_symbol_command(message, remainder)
        return True

    if normalized == "portfolio":
        await _handle_portfolio_command(message, prompt)
        return True

    return False


async def _create_session(channel_id: int):
    """Create a fresh Copilot session for a Discord user."""
    system_message = _get_system_message(channel_id)
    session = await copilot_client.create_session({
        "model": "gpt-5.4",
        "client_name": "swing-agent-discord",
        "on_permission_request": PermissionHandler.approve_all,
        "system_message": {
            "mode": "append",
            "content": system_message,
        },
        "mcp_servers": _MCP_SERVERS,
        "excluded_tools": _EXCLUDED_TOOLS,
        "infinite_sessions": {"enabled": False},
    })
    session.on(lambda event: log.debug(
        "[session %s] event=%s error_type=%s message=%s",
        session.session_id,
        event.type,
        getattr(event.data, "error_type", None),
        getattr(event.data, "message", None),
    ))
    log.info(
        "Created Copilot session %s for channel %d using %s",
        session.session_id,
        channel_id,
        CHANNEL_PROMPT_PATHS.get(channel_id, DEFAULT_PROMPT_PATH).name,
    )
    return session


def _split_discord_response(text: str) -> list[str]:
    normalized = text.strip()
    if not normalized:
        return ["I didn't get a usable response from the session."]

    strategy_pattern = re.compile(
        r"(?=^Trade\s+\d+:|^Current positions:|^Summary:|^Rejected:|^Portfolio:)",
        flags=re.MULTILINE,
    )
    candidate_parts = [part.strip() for part in strategy_pattern.split(normalized) if part.strip()]

    if not candidate_parts:
        candidate_parts = [normalized]

    chunks: list[str] = []
    current = ""
    for part in candidate_parts:
        separator = "\n\n" if current else ""
        if len(part) > _DISCORD_MAX_CHARS:
            if current:
                chunks.append(current)
                current = ""
            for i in range(0, len(part), _DISCORD_MAX_CHARS):
                chunks.append(part[i : i + _DISCORD_MAX_CHARS])
            continue
        if len(current) + len(separator) + len(part) <= _DISCORD_MAX_CHARS:
            current = f"{current}{separator}{part}" if current else part
        else:
            chunks.append(current)
            current = part

    if current:
        chunks.append(current)

    return chunks


async def _reply_to_message_chunked(message: discord.Message, text: str) -> None:
    """Reply to the original Discord message with strategy-aware chunked output."""
    first = True
    for chunk in _split_discord_response(text):
        if first:
            await message.reply(chunk)
            first = False
        else:
            await message.channel.send(chunk)


async def _send_followup_chunked(interaction: discord.Interaction, text: str) -> None:
    """Send strategy-aware follow-up output for interactions."""
    for chunk in _split_discord_response(text):
        await interaction.followup.send(chunk)


async def _disconnect_session(user_id: int) -> None:
    """Disconnect and forget a cached session for a Discord user."""
    entry = _user_sessions.pop(user_id, None)
    if entry is None:
        return
    try:
        await entry.session.disconnect()
    except Exception:
        log.debug("Failed to disconnect session for user %d cleanly", user_id, exc_info=True)


async def _get_or_create_user_session(user_id: int, channel_id: int) -> UserSessionEntry:
    """Return the cached session for a user or create a new one."""
    entry = _user_sessions.get(user_id)
    if entry is not None:
        entry.last_used_at = time.monotonic()
        return entry

    session = await _create_session(channel_id)
    entry = UserSessionEntry(session=session, channel_id=channel_id)
    _user_sessions[user_id] = entry
    return entry


async def _expire_idle_sessions() -> None:
    """Disconnect user sessions that have been idle past the configured TTL."""
    now = time.monotonic()
    expired_user_ids = [
        user_id
        for user_id, entry in _user_sessions.items()
        if now - entry.last_used_at >= _SESSION_IDLE_TTL_SECONDS
    ]
    for user_id in expired_user_ids:
        log.info("Expiring idle session for user %d after %.0f seconds", user_id, _SESSION_IDLE_TTL_SECONDS)
        await _disconnect_session(user_id)

    expired_flow_user_ids = [
        user_id
        for user_id, flow in _portfolio_flows.items()
        if now - flow.created_at >= _FLOW_IDLE_TTL_SECONDS
    ]
    for user_id in expired_flow_user_ids:
        log.info("Expiring portfolio flow for user %d after %.0f seconds", user_id, _FLOW_IDLE_TTL_SECONDS)
        _clear_portfolio_flow(user_id)


async def _session_cleanup_loop() -> None:
    """Periodically expire idle user sessions."""
    try:
        while True:
            await asyncio.sleep(_SESSION_CLEANUP_INTERVAL_SECONDS)
            await _expire_idle_sessions()
    except asyncio.CancelledError:
        raise


def _extract_response_text(response_event) -> str:
    """Normalize the Copilot SDK response event into Discord message text."""
    if response_event is None:
        return "I didn't get a final response from the session."
    content = getattr(response_event.data, "content", None)
    if isinstance(content, str) and content.strip():
        return content.strip()
    return "I didn't get a usable response from the session."


@command_tree.command(name="portfolio", description="Open the interactive portfolio panel")
async def portfolio_command(interaction: discord.Interaction) -> None:
    if not _is_allowed_channel(interaction.channel_id):
        await interaction.response.send_message("This command is not enabled in this channel.", ephemeral=True)
        return

    snapshot = await asyncio.to_thread(
        _get_portfolio_store().build_portfolio_snapshot,
        discord_user_id=interaction.user.id,
        username=interaction.user.name,
    )
    view = PortfolioPanelView(
        owner_user_id=interaction.user.id,
        owner_username=interaction.user.name,
        channel_id=interaction.channel_id,
        snapshot=snapshot,
    )
    log.info(
        "Received /portfolio interaction for user=%s channel=%s",
        interaction.user.id,
        interaction.channel_id,
    )
    await _open_setup_modal_or_fallback(
        interaction,
        view=view,
        title="Portfolio Setup",
        edit_existing_message=False,
    )


@discord_client.event
async def on_ready() -> None:
    global _session_cleanup_task

    await copilot_client.start()
    try:
        synced_commands = await command_tree.sync()
        log.info("Synced %d Discord app commands", len(synced_commands))
    except Exception:
        log.exception("Failed to sync Discord app commands")
    if _session_cleanup_task is None or _session_cleanup_task.done():
        _session_cleanup_task = asyncio.create_task(_session_cleanup_loop())
    log.info("Logged in as %s | Copilot client connected", discord_client.user)
    log.info("Income agent channel %d uses %s", DISCORD_INCOME_AGENT_CHANNEL_ID, CHANNEL_PROMPT_PATHS[DISCORD_INCOME_AGENT_CHANNEL_ID].name)
    log.info("Allowed Discord channels: %s", sorted(ALLOWED_CHANNEL_IDS))
    log.info("Yahoo and price-data MCP servers use Docker HTTP endpoints for the bot")
    log.info("User sessions expire after %.0f minutes of inactivity", _SESSION_IDLE_TTL_SECONDS / 60)


@discord_client.event
async def on_message(message: discord.Message) -> None:
    # Ignore all bot messages (including self).
    if message.author.bot:
        return

    if not _is_allowed_channel(message.channel.id):
        return

    pending_flow = _get_active_portfolio_flow(message.author.id, message.channel.id)
    if pending_flow is not None and not _is_targeted(message):
        if await _handle_portfolio_flow(message, pending_flow):
            return

    if not _is_targeted(message):
        return

    prompt = _strip_mention(message.content, discord_client.user)
    if not prompt:
        await message.reply("What would you like me to research?")
        return

    if await _maybe_handle_command(message, prompt):
        return

    try:
        response_text = await _run_recommendation_request(
            user_id=message.author.id,
            username=message.author.name,
            channel_id=message.channel.id,
            prompt=prompt,
        )
        await _reply_to_message_chunked(message, response_text)
    except Exception:
        log.exception("Session request failed for user %d in channel %d", message.author.id, message.channel.id)
        await _disconnect_session(message.author.id)
        await message.reply(
            "I couldn't finish that request cleanly. Please mention me again to start a fresh session."
        )


@discord_client.event
async def on_disconnect() -> None:
    global _session_cleanup_task

    if _session_cleanup_task is not None:
        _session_cleanup_task.cancel()
        _session_cleanup_task = None


if __name__ == "__main__":
    discord_client.run(DISCORD_BOT_TOKEN)
