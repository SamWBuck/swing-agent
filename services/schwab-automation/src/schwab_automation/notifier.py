from __future__ import annotations

import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)


class DiscordNotifier:
    _MAX_MESSAGE_LENGTH = 1900

    def __init__(
        self,
        webhook_url: str | None,
        *,
        timeout_seconds: int,
        bot_token: str | None = None,
        channel_id: int | None = None,
    ) -> None:
        self._webhook_url = webhook_url
        self._timeout_seconds = timeout_seconds
        self._bot_token = bot_token
        self._channel_id = channel_id

    def _chunk_content(self, content: str) -> list[str]:
        if len(content) <= self._MAX_MESSAGE_LENGTH:
            return [content]

        chunks: list[str] = []
        current: list[str] = []
        current_length = 0
        for line in content.splitlines():
            added_length = len(line) + (1 if current else 0)
            if current and current_length + added_length > self._MAX_MESSAGE_LENGTH:
                chunks.append("\n".join(current))
                current = [line]
                current_length = len(line)
                continue
            current.append(line)
            current_length += added_length

        if current:
            chunks.append("\n".join(current))
        return chunks

    async def _send_via_channel(self, content: str) -> None:
        if not self._bot_token or not self._channel_id:
            raise RuntimeError("Discord channel delivery requires bot token and channel ID")

        headers = {
            "Authorization": f"Bot {self._bot_token}",
            "Content-Type": "application/json",
        }
        url = f"https://discord.com/api/v10/channels/{self._channel_id}/messages"
        async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
            for chunk in self._chunk_content(content):
                response = await client.post(url, headers=headers, json={"content": chunk})
                response.raise_for_status()

    async def _send_via_webhook(self, content: str) -> None:
        if not self._webhook_url:
            raise RuntimeError("Discord webhook delivery requires a configured webhook URL")

        async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
            for chunk in self._chunk_content(content):
                response = await client.post(self._webhook_url, json={"content": chunk})
                response.raise_for_status()

    async def send(self, content: str) -> None:
        if self._bot_token and self._channel_id:
            await self._send_via_channel(content)
            return
        if self._webhook_url:
            await self._send_via_webhook(content)
            return
        log.info("Discord delivery not configured; message=%s", content)

    async def send_run_summary(
        self,
        *,
        account_label: str,
        positions_count: int,
        cash_available: str | None,
        liquidation_value: str | None,
        run_details: dict[str, Any],
    ) -> None:
        lines = [
            f"[{run_details['service_name']}] {run_details['run_type']} reconcile completed",
            f"Account: {account_label}",
            f"Positions: {positions_count}",
        ]
        if cash_available is not None:
            lines.append(f"Cash balance: {cash_available}")
        if liquidation_value is not None:
            lines.append(f"Liquidation value: {liquidation_value}")
        lines.append(f"Dry run: {'yes' if run_details['dry_run'] else 'no'}")
        await self.send("\n".join(lines))

    async def send_failure(self, *, service_name: str, run_type: str, error_text: str) -> None:
        await self.send(
            "\n".join(
                [
                    f"[{service_name}] {run_type} run failed",
                    error_text,
                ]
            )
        )