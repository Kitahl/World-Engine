from __future__ import annotations

from typing import Any

from .models import Principal


PRINCIPAL_KINDS = {"player", "character", "npc", "gm", "system"}
SENSITIVITIES = {"NORMAL", "PRIVATE", "SECRET"}
SCOPE_TYPES = {"PUBLIC", "WORLD", "PLAYER", "ENTITY", "GM"}


def resolve_principal(
    viewer_kind: str | None,
    viewer_id: str | None,
    *,
    actor_kind: str | None = None,
    actor_id: str | None = None,
) -> Principal:
    """Resolve a closed viewer principal without consulting model-generated text."""

    kind = str(viewer_kind or "player").strip().lower()
    if kind not in PRINCIPAL_KINDS:
        raise ValueError(f"unsupported viewer principal kind: {kind}")
    ident = str(viewer_id or ("local-player" if kind == "player" else kind)).strip()
    if not ident:
        raise ValueError("viewer principal id is required")
    a_kind = str(actor_kind).strip().lower() if actor_kind else None
    if a_kind is not None and a_kind not in {"character", "npc"}:
        raise ValueError("actor_kind must be character or npc")
    a_id = str(actor_id).strip() if actor_id else None
    return Principal(kind=kind, id=ident, actor_kind=a_kind, actor_id=a_id)


def authorize_candidate(candidate: dict[str, Any], principal: Principal) -> tuple[bool, str]:
    """Authorization is a hard pre-ranking filter.

    Relevance, FTS rank, salience, age, or token cost are deliberately absent
    from this function, so no scoring path can promote an unauthorized record.
    """

    sensitivity = str(candidate.get("sensitivity") or "NORMAL").upper()
    scope_type = str(candidate.get("scope_type") or "PUBLIC").upper()
    scope_kind = candidate.get("scope_kind")
    scope_id = candidate.get("scope_id")

    if sensitivity not in SENSITIVITIES:
        return False, "INVALID_SENSITIVITY"
    if scope_type not in SCOPE_TYPES:
        return False, "INVALID_SCOPE"

    if principal.kind in {"gm", "system"}:
        return True, "PRIVILEGED_PRINCIPAL"
    if sensitivity == "SECRET" or scope_type == "GM":
        return False, "GM_SECRET"

    if scope_type in {"PUBLIC", "WORLD"}:
        return True, "PUBLIC_OR_WORLD"

    if scope_type == "PLAYER":
        return (principal.kind == "player" and str(scope_id or principal.id) == principal.id,
                "PLAYER_SCOPE" if principal.kind == "player" and str(scope_id or principal.id) == principal.id else "WRONG_PLAYER")

    # ENTITY-private context is visible to the entity itself. A player may also
    # see the private state of the explicit actor they are controlling for this
    # turn; this does not grant visibility into target NPC cognition.
    wanted = f"{scope_kind}:{scope_id}" if scope_kind and scope_id else None
    if wanted and principal.entity_key == wanted:
        return True, "ENTITY_SELF"
    if wanted and principal.kind == "player" and principal.controlled_actor_key == wanted:
        return True, "CONTROLLED_ACTOR"
    return False, "NOT_KNOWN_TO_PRINCIPAL"
