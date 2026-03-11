from __future__ import annotations

import json
import logging
from pathlib import Path

from copilot import CopilotClient, PermissionHandler
from copilot.generated.session_events import SessionEventType
from swing_agent_database.mcp_discovery import fetch_mcp_tool_catalog, format_mcp_tool_catalog_context

from .config import Settings

log = logging.getLogger(__name__)

_EXCLUDED_TOOLS = [
    "glob",
    "powershell",
    "read_agent",
    "report_intent",
    "task",
    "view",
    "web_fetch",
]


async def _build_tool_catalog_context(settings: Settings) -> str:
    server_configs = {
        "sec-edgar": settings.sec_edgar_mcp_url,
        "yahoo-finance": settings.yahoo_finance_mcp_url,
        "price-data": settings.price_data_mcp_url,
    }
    catalogs: dict[str, dict] = {}
    for server_name, url in server_configs.items():
        try:
            catalogs[server_name] = await fetch_mcp_tool_catalog(
                url=url,
                client_name=f"swing-agent-automation-{server_name}",
            )
        except Exception as exc:
            log.warning("Failed to fetch MCP tool catalog for %s: %s", server_name, exc)
            catalogs[server_name] = {
                "server_info": {"version": "unavailable"},
                "tool_count": 0,
                "tools": [{"name": "unavailable", "description": f"Catalog discovery failed: {exc}"}],
            }

    summary = ", ".join(f"{server_name}={catalog['tool_count']}" for server_name, catalog in catalogs.items())
    log.info("Discovered live MCP tool catalogs: %s", summary)
    return format_mcp_tool_catalog_context(catalogs)


def _get_system_message(prompt_path: Path, *, tool_catalog_context: str) -> str:
    return f"{prompt_path.read_text(encoding='utf-8').strip()}\n\n{tool_catalog_context}"


def extract_json_payload(text: str) -> dict:
    normalized = text.strip()
    if normalized.startswith("```"):
        lines = normalized.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        normalized = "\n".join(lines).strip()
    try:
        payload = json.loads(normalized)
    except json.JSONDecodeError:
        start = normalized.find("{")
        end = normalized.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        payload = json.loads(normalized[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("LLM response must be a JSON object")
    return payload


async def run_structured_analysis(
    settings: Settings,
    *,
    user_prompt: str,
    prompt_path: Path | None = None,
) -> tuple[dict, str, bool]:
    selected_prompt_path = prompt_path or settings.automation_prompt_path
    tool_catalog_context = await _build_tool_catalog_context(settings)
    log.info(
        "Starting Copilot structured analysis prompt=%s sec_edgar=%s yahoo=%s price_data=%s",
        selected_prompt_path,
        settings.sec_edgar_mcp_url,
        settings.yahoo_finance_mcp_url,
        settings.price_data_mcp_url,
    )
    client = CopilotClient()
    await client.start()
    session = await client.create_session(
        {
            "model": "gpt-5.4",
            "client_name": "swing-agent-automation",
            "on_permission_request": PermissionHandler.approve_all,
            "system_message": {
                "mode": "append",
                "content": _get_system_message(selected_prompt_path, tool_catalog_context=tool_catalog_context),
            },
            "mcp_servers": {
                "sec-edgar": {"type": "http", "url": settings.sec_edgar_mcp_url},
                "yahoo-finance": {"type": "http", "url": settings.yahoo_finance_mcp_url},
                "price-data": {"type": "http", "url": settings.price_data_mcp_url},
            },
            "excluded_tools": _EXCLUDED_TOOLS,
            "infinite_sessions": {"enabled": False},
        }
    )
    observed_event_types: set[str] = set()
    observed_mcp_tool_calls: list[str] = []

    def _handle_session_event(event) -> None:
        event_name = getattr(event.type, "name", str(event.type))
        observed_event_types.add(event_name)
        if event.type == SessionEventType.EXTERNAL_TOOL_REQUESTED and getattr(event.data, "mcp_server_name", None):
            server_name = getattr(event.data, "mcp_server_name", None)
            tool_name = getattr(event.data, "mcp_tool_name", None) or getattr(event.data, "tool_name", None)
            if server_name and tool_name:
                observed_mcp_tool_calls.append(f"{server_name}:{tool_name}")
            elif server_name:
                observed_mcp_tool_calls.append(server_name)
        log.debug(
            "[automation session %s] event=%s mcp_server=%s mcp_tool=%s error_type=%s message=%s",
            session.session_id,
            event.type,
            getattr(event.data, "mcp_server_name", None),
            getattr(event.data, "mcp_tool_name", None),
            getattr(event.data, "error_type", None),
            getattr(event.data, "message", None),
        )

    session.on(_handle_session_event)
    log.info("Created automation Copilot session %s", session.session_id)

    try:
        response = await session.send_and_wait({"prompt": user_prompt}, timeout=float(settings.analysis_timeout_seconds))
        content = getattr(response.data, "content", None)
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Copilot session returned empty content")
        had_mcp_tool_calls = bool(observed_mcp_tool_calls)
        if not had_mcp_tool_calls:
            log.info(
                "Automation Copilot session %s completed without MCP CallTool requests; live MCP tool catalogs were discovered before prompting",
                session.session_id,
            )
        else:
            log.info(
                "Automation Copilot session %s used MCP tools: %s",
                session.session_id,
                ", ".join(observed_mcp_tool_calls),
            )
        log.info("Structured analysis completed for session %s", session.session_id)
        return extract_json_payload(content), content, had_mcp_tool_calls
    finally:
        try:
            await session.disconnect()
        except Exception:
            log.debug("Failed to disconnect automation Copilot session cleanly", exc_info=True)