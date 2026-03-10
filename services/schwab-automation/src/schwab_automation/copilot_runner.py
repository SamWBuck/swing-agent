from __future__ import annotations

import json
import logging
from pathlib import Path

from copilot import CopilotClient, PermissionHandler

from .config import Settings

log = logging.getLogger(__name__)

_DISCORD_SUFFIX = "Return JSON only. Do not wrap the JSON in markdown fences."
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


def _get_system_message(prompt_path: Path) -> str:
    return f"{prompt_path.read_text(encoding='utf-8').strip()}\n\n{_DISCORD_SUFFIX}"


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


async def run_structured_analysis(settings: Settings, *, user_prompt: str, prompt_path: Path | None = None) -> tuple[dict, str]:
    client = CopilotClient()
    await client.start()
    session = await client.create_session(
        {
            "model": "gpt-5.4",
            "client_name": "swing-agent-automation",
            "on_permission_request": PermissionHandler.approve_all,
            "system_message": {
                "mode": "append",
                "content": _get_system_message(prompt_path or settings.automation_prompt_path),
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

    try:
        response = await session.send_and_wait({"prompt": user_prompt}, timeout=float(settings.analysis_timeout_seconds))
        content = getattr(response.data, "content", None)
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Copilot session returned empty content")
        return extract_json_payload(content), content
    finally:
        try:
            await session.disconnect()
        except Exception:
            log.debug("Failed to disconnect automation Copilot session cleanly", exc_info=True)