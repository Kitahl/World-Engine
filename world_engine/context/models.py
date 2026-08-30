from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Principal:
    """Viewer identity used by the hard authorization boundary.

    ``actor_kind``/``actor_id`` identify the player-controlled actor for turns
    where the viewer itself is a player rather than an in-world entity.
    """

    kind: str
    id: str
    actor_kind: str | None = None
    actor_id: str | None = None

    @property
    def entity_key(self) -> str | None:
        if self.kind in {"character", "npc"} and self.id:
            return f"{self.kind}:{self.id}"
        return None

    @property
    def controlled_actor_key(self) -> str | None:
        if self.actor_kind in {"character", "npc"} and self.actor_id:
            return f"{self.actor_kind}:{self.actor_id}"
        return None

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "id": self.id,
            "actor_kind": self.actor_kind,
            "actor_id": self.actor_id,
        }


@dataclass
class ContextCandidate:
    candidate_id: str
    tier: str
    kind: str
    payload: Any
    reason: str
    mandatory: bool = False
    source_revision: int = 0
    authority: str = "CANONICAL_STATE"
    sensitivity: str = "NORMAL"
    scope_type: str = "PUBLIC"
    scope_kind: str | None = None
    scope_id: str | None = None
    dependencies: list[str] = field(default_factory=list)
    score_components: dict[str, int] = field(default_factory=dict)
    fixed_point_score: int = 0
    source: str = "world_engine"

    def scope_dict(self) -> dict[str, Any]:
        return {
            "type": self.scope_type,
            "entity_kind": self.scope_kind,
            "entity_id": self.scope_id,
        }
