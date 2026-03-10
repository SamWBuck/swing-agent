from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class EntryCandidateIdea:
    symbol: str
    strategy_type: str
    option_type: str
    rationale: list[str]
    confidence: str


def parse_entry_candidate_ideas(payload: dict[str, Any]) -> list[EntryCandidateIdea]:
    raw_candidates = payload.get("candidates") or []
    if not isinstance(raw_candidates, list):
        raise ValueError("candidates must be a list")

    ideas: list[EntryCandidateIdea] = []
    seen: set[tuple[str, str, str]] = set()
    for raw in raw_candidates:
        if not isinstance(raw, dict):
            continue
        symbol = str(raw.get("symbol") or "").strip().upper()
        strategy_type = str(raw.get("strategy_type") or "").strip()
        option_type = str(raw.get("option_type") or "").strip().upper()
        rationale = raw.get("rationale") or []
        confidence = str(raw.get("confidence") or "medium").strip().lower()
        if not symbol or not strategy_type or option_type not in {"CALL", "PUT"}:
            continue
        if not isinstance(rationale, list):
            rationale = [str(rationale)]
        key = (symbol, strategy_type, option_type)
        if key in seen:
            continue
        seen.add(key)
        ideas.append(
            EntryCandidateIdea(
                symbol=symbol,
                strategy_type=strategy_type,
                option_type=option_type,
                rationale=[str(item) for item in rationale if str(item).strip()],
                confidence=confidence,
            )
        )
    return ideas