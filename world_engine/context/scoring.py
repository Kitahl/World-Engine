from __future__ import annotations

from typing import Mapping


SCORE_SCALE = 10_000


def _clamp(value: int) -> int:
    return max(0, min(SCORE_SCALE, int(value)))


def fixed_point_score(components: Mapping[str, int]) -> int:
    """Return a deterministic integer score in 0..10000.

    Intent match dominates, followed by mechanical importance, proximity,
    recency and continuity. Integer arithmetic avoids platform float drift.
    """

    intent = _clamp(components.get("intent", 0))
    importance = _clamp(components.get("importance", 0))
    proximity = _clamp(components.get("proximity", 0))
    recency = _clamp(components.get("recency", 0))
    continuity = _clamp(components.get("continuity", 0))
    return (35 * intent + 25 * importance + 15 * proximity + 15 * recency + 10 * continuity) // 100
