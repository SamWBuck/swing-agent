from __future__ import annotations

import re

import discord


_EMBED_DESCRIPTION_MAX = 4096


def split_discord_response(text: str, *, max_chars: int = _EMBED_DESCRIPTION_MAX) -> list[str]:
    """Split a long response into Discord-safe chunks with strategy-aware boundaries."""
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
        if len(part) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            for index in range(0, len(part), max_chars):
                chunks.append(part[index : index + max_chars])
            continue
        if len(current) + len(separator) + len(part) <= max_chars:
            current = f"{current}{separator}{part}" if current else part
        else:
            chunks.append(current)
            current = part

    if current:
        chunks.append(current)
    return chunks


def build_response_embeds(
    text: str,
    *,
    title: str = "Swing Agent",
    color: int = 0x2F80ED,
    max_chars: int = _EMBED_DESCRIPTION_MAX,
) -> list[discord.Embed]:
    """Build one or more Discord embeds from a text response."""
    chunks = split_discord_response(text, max_chars=max_chars)
    embeds: list[discord.Embed] = []
    total = len(chunks)
    for index, chunk in enumerate(chunks, start=1):
        embed_title = title if total == 1 else f"{title} ({index}/{total})"
        embed = discord.Embed(title=embed_title, description=chunk, color=color)
        embeds.append(embed)
    return embeds


async def reply_to_message_chunked(message: discord.Message, text: str, *, max_chars: int = 2000) -> None:
    """Reply to a Discord message with embed-based chunked output."""
    first = True
    for embed in build_response_embeds(text, max_chars=max_chars):
        if first:
            await message.reply(embed=embed)
            first = False
        else:
            await message.channel.send(embed=embed)


async def send_followup_chunked(interaction: discord.Interaction, text: str, *, max_chars: int = 2000) -> None:
    """Send embed-based chunked follow-up output for Discord interactions."""
    for embed in build_response_embeds(text, max_chars=max_chars):
        await interaction.followup.send(embed=embed)