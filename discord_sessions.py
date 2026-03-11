from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Generic, TypeVar


SessionT = TypeVar("SessionT")


@dataclass
class UserSessionEntry(Generic[SessionT]):
    session: SessionT
    channel_id: int
    last_used_at: float = field(default_factory=time.monotonic)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class DiscordSessionManager(Generic[SessionT]):
    """Cache and expire per-user Discord Copilot sessions."""

    def __init__(
        self,
        *,
        session_factory: Callable[[int], Awaitable[SessionT]],
        idle_ttl_seconds: float,
    ) -> None:
        self._session_factory = session_factory
        self._idle_ttl_seconds = idle_ttl_seconds
        self._entries: dict[int, UserSessionEntry[SessionT]] = {}

    async def disconnect(self, user_id: int) -> None:
        entry = self._entries.pop(user_id, None)
        if entry is None:
            return
        await entry.session.disconnect()

    async def get_or_create(self, *, user_id: int, channel_id: int) -> UserSessionEntry[SessionT]:
        entry = self._entries.get(user_id)
        if entry is not None:
            entry.last_used_at = time.monotonic()
            return entry

        session = await self._session_factory(channel_id)
        entry = UserSessionEntry(session=session, channel_id=channel_id)
        self._entries[user_id] = entry
        return entry

    async def expire_idle(self) -> list[int]:
        now = time.monotonic()
        expired_user_ids = [
            user_id
            for user_id, entry in self._entries.items()
            if now - entry.last_used_at >= self._idle_ttl_seconds
        ]
        for user_id in expired_user_ids:
            await self.disconnect(user_id)
        return expired_user_ids