from __future__ import annotations

import json
from typing import Any

import httpx


def _parse_mcp_sse_payload(response_text: str) -> dict[str, Any]:
    data_lines = [line[5:].strip() for line in response_text.splitlines() if line.startswith("data:")]
    if not data_lines:
        raise ValueError("MCP server returned no data payload")
    return json.loads("\n".join(data_lines))


async def fetch_mcp_tool_catalog(
    *,
    url: str,
    client_name: str,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    headers = {"Accept": "application/json, text/event-stream"}
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        initialize_response = await client.post(
            url,
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": client_name, "version": "1.0"},
                },
            },
        )
        initialize_response.raise_for_status()
        initialize_payload = _parse_mcp_sse_payload(initialize_response.text)

        session_id = initialize_response.headers.get("mcp-session-id")
        if session_id:
            headers["mcp-session-id"] = session_id

        list_response = await client.post(
            url,
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
                "params": {},
            },
        )
        list_response.raise_for_status()
        list_payload = _parse_mcp_sse_payload(list_response.text)

    initialize_result = initialize_payload.get("result") or {}
    list_result = list_payload.get("result") or {}
    tools = list_result.get("tools") or []
    return {
        "server_info": initialize_result.get("serverInfo") or {},
        "tool_count": len(tools),
        "tools": [
            {
                "name": tool.get("name"),
                "description": tool.get("description") or "",
            }
            for tool in tools
            if isinstance(tool, dict) and tool.get("name")
        ],
    }


def format_mcp_tool_catalog_context(server_catalogs: dict[str, dict[str, Any]]) -> str:
    lines = ["Live MCP tool catalog discovered for this session:"]
    for server_name, catalog in server_catalogs.items():
        server_info = catalog.get("server_info") or {}
        version = server_info.get("version") or "unknown"
        tool_count = catalog.get("tool_count") or 0
        lines.append(f"- {server_name} (version {version}, tools={tool_count})")
        for tool in catalog.get("tools") or []:
            description = str(tool.get("description") or "").strip().replace("\n", " ")
            lines.append(f"  - {tool['name']}: {description}")
    return "\n".join(lines)