"""Discord bot backed by GitHub Copilot SDK (copilot package) with MCP tools.

Run via VS Code launch config or: python discord_bot.py
Requires DISCORD_BOT_TOKEN in .env (root directory).
MCP services must be reachable at their configured localhost ports.
"""

import asyncio
import logging
import os
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

import discord
from discord import app_commands
from copilot import CopilotClient, PermissionHandler
from discord_response import reply_to_message_chunked, send_followup_chunked
from discord_sessions import DiscordSessionManager, UserSessionEntry
from dotenv import load_dotenv
from swing_agent_database.mcp_discovery import fetch_mcp_tool_catalog, format_mcp_tool_catalog_context
from swing_agent_database import (
    SymbolAvailabilityStore,
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
SEC_EDGAR_MCP_URL = os.getenv("SEC_EDGAR_MCP_URL", "http://localhost:9870/mcp")
YAHOO_FINANCE_MCP_URL = os.getenv("YAHOO_FINANCE_MCP_URL", "http://localhost:8809/mcp")
PRICE_DATA_MCP_URL = os.getenv("PRICE_DATA_MCP_URL", "http://localhost:8810/mcp")

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


async def _build_tool_catalog_context() -> str:
    server_configs = {
        "sec-edgar": SEC_EDGAR_MCP_URL,
        "yahoo-finance": YAHOO_FINANCE_MCP_URL,
        "price-data": PRICE_DATA_MCP_URL,
    }
    catalogs: dict[str, dict] = {}
    for server_name, url in server_configs.items():
        try:
            catalogs[server_name] = await fetch_mcp_tool_catalog(
                url=url,
                client_name=f"swing-agent-discord-{server_name}",
            )
        except Exception as exc:
            log.warning("Failed to fetch MCP tool catalog for %s: %s", server_name, exc)
            catalogs[server_name] = {
                "server_info": {"version": "unavailable"},
                "tool_count": 0,
                "tools": [{"name": "unavailable", "description": f"Catalog discovery failed: {exc}"}],
            }

    summary = ", ".join(f"{server_name}={catalog['tool_count']}" for server_name, catalog in catalogs.items())
    log.info("Discovered Discord MCP tool catalogs: %s", summary)
    return format_mcp_tool_catalog_context(catalogs)

_MCP_SERVERS: dict = {
    "sec-edgar": {
        "type": "http",
        "url": SEC_EDGAR_MCP_URL,
    },
    "yahoo-finance": {
        "type": "http",
        "url": YAHOO_FINANCE_MCP_URL,
    },
    "price-data": {
        "type": "http",
        "url": PRICE_DATA_MCP_URL,
    },
}

_EXCLUDED_TOOLS = [
    "glob",
    "powershell",
    "read_agent",
    "report_intent",
    "task",
    "view",
    "web_fetch",
]

_SESSION_IDLE_TTL_SECONDS = 15 * 60
_SESSION_RESPONSE_TIMEOUT_SECONDS = 600.0
_SESSION_CLEANUP_INTERVAL_SECONDS = 60.0

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

_session_cleanup_task: asyncio.Task | None = None


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


async def _run_recommendation_request(
    *,
    user_id: int,
    channel_id: int,
    prompt: str,
) -> str:
    entry = await _get_or_create_user_session(user_id, channel_id)

    async with entry.lock:
        response_event = await entry.session.send_and_wait(
            {"prompt": prompt},
            timeout=_SESSION_RESPONSE_TIMEOUT_SECONDS,
        )
        return _extract_response_text(response_event)


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

    await reply_to_message_chunked(
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

    await reply_to_message_chunked(
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

    return False


async def _create_session(channel_id: int):
    """Create a fresh Copilot session for a Discord user."""
    tool_catalog_context = await _build_tool_catalog_context()
    system_message = f"{_get_system_message(channel_id)}\n\n{tool_catalog_context}"
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


_session_manager = DiscordSessionManager(session_factory=_create_session, idle_ttl_seconds=_SESSION_IDLE_TTL_SECONDS)
async def _disconnect_session(user_id: int) -> None:
    """Disconnect and forget a cached session for a Discord user."""
    try:
        await _session_manager.disconnect(user_id)
    except Exception:
        log.debug("Failed to disconnect session for user %d cleanly", user_id, exc_info=True)


async def _get_or_create_user_session(user_id: int, channel_id: int) -> UserSessionEntry:
    """Return the cached session for a user or create a new one."""
    return await _session_manager.get_or_create(user_id=user_id, channel_id=channel_id)


async def _expire_idle_sessions() -> None:
    """Disconnect user sessions that have been idle past the configured TTL."""
    expired_user_ids = await _session_manager.expire_idle()
    for user_id in expired_user_ids:
        log.info("Expiring idle session for user %d after %.0f seconds", user_id, _SESSION_IDLE_TTL_SECONDS)


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
    log.info(
        "Discord bot MCP endpoints: sec-edgar=%s yahoo-finance=%s price-data=%s",
        SEC_EDGAR_MCP_URL,
        YAHOO_FINANCE_MCP_URL,
        PRICE_DATA_MCP_URL,
    )
    log.info("User sessions expire after %.0f minutes of inactivity", _SESSION_IDLE_TTL_SECONDS / 60)


@discord_client.event
async def on_message(message: discord.Message) -> None:
    # Ignore all bot messages (including self).
    if message.author.bot:
        return

    if not _is_allowed_channel(message.channel.id):
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
            channel_id=message.channel.id,
            prompt=prompt,
        )
        await reply_to_message_chunked(message, response_text)
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
