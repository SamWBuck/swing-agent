from __future__ import annotations

import json
import logging
from pathlib import Path

from copilot import CopilotClient, PermissionHandler
from copilot.generated.session_events import SessionEventType

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


def _get_system_message(prompt_path: Path) -> str:
    return prompt_path.read_text(encoding="utf-8").strip()


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
) -> tuple[dict, str]:
    log.info(
        "Starting Copilot structured analysis prompt=%s sec_edgar=%s yahoo=%s price_data=%s",
        settings.automation_prompt_path,
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
                "content": _get_system_message(settings.automation_prompt_path),
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
    observed_mcp_tool_calls: list[str] = []

    def _handle_session_event(event) -> None:
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
        if observed_mcp_tool_calls:
            log.info("Automation Copilot session %s used MCP tools: %s", session.session_id, ", ".join(observed_mcp_tool_calls))
        else:
            log.info("Automation Copilot session %s completed without MCP tool calls", session.session_id)
        log.info("Structured analysis completed for session %s", session.session_id)
        return extract_json_payload(content), content
    finally:
        try:
            await session.disconnect()
        except Exception:
            log.debug("Failed to disconnect automation Copilot session cleanly", exc_info=True)