from __future__ import annotations

import logging

import httpx

log = logging.getLogger(__name__)


class DiscordNotifier:
    _MAX_EMBED_DESCRIPTION_LENGTH = 3900
    _DEFAULT_COLOR = 0x2F80ED
    _FAILURE_COLOR = 0xC0392B

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
        if len(content) <= self._MAX_EMBED_DESCRIPTION_LENGTH:
            return [content]

        chunks: list[str] = []
        current: list[str] = []
        current_length = 0
        for line in content.splitlines():
            added_length = len(line) + (1 if current else 0)
            if current and current_length + added_length > self._MAX_EMBED_DESCRIPTION_LENGTH:
                chunks.append("\n".join(current))
                current = [line]
                current_length = len(line)
                continue
            current.append(line)
            current_length += added_length

        if current:
            chunks.append("\n".join(current))
        return chunks

    def _build_embed_payloads(self, content: str, *, title: str, color: int) -> list[dict[str, object]]:
        payloads: list[dict[str, object]] = []
        chunks = self._chunk_content(content)
        total = len(chunks)
        for index, chunk in enumerate(chunks, start=1):
            embed_title = title if total == 1 else f"{title} ({index}/{total})"
            payloads.append(
                {
                    "embeds": [
                        {
                            "title": embed_title,
                            "description": chunk,
                            "color": color,
                        }
                    ]
                }
            )
        return payloads

    async def _send_via_channel(self, content: str, *, title: str, color: int) -> None:
        if not self._bot_token or not self._channel_id:
            raise RuntimeError("Discord channel delivery requires bot token and channel ID")

        headers = {
            "Authorization": f"Bot {self._bot_token}",
            "Content-Type": "application/json",
        }
        url = f"https://discord.com/api/v10/channels/{self._channel_id}/messages"
        async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
            for payload in self._build_embed_payloads(content, title=title, color=color):
                response = await client.post(url, headers=headers, json=payload)
                response.raise_for_status()

    async def _send_via_webhook(self, content: str, *, title: str, color: int) -> None:
        if not self._webhook_url:
            raise RuntimeError("Discord webhook delivery requires a configured webhook URL")

        async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
            for payload in self._build_embed_payloads(content, title=title, color=color):
                response = await client.post(self._webhook_url, json=payload)
                response.raise_for_status()

    async def send(self, content: str, *, title: str = "Swing Agent Automation", color: int = _DEFAULT_COLOR) -> None:
        if self._bot_token and self._channel_id:
            await self._send_via_channel(content, title=title, color=color)
            return
        if self._webhook_url:
            await self._send_via_webhook(content, title=title, color=color)
            return
        log.info("Discord delivery not configured; message=%s", content)

    async def send_failure(self, *, service_name: str, run_type: str, error_text: str) -> None:
        await self.send(
            "\n".join(
                [
                    f"[{service_name}] {run_type} run failed",
                    error_text,
                ]
            ),
            title="Swing Agent Failure",
            color=self._FAILURE_COLOR,
        )