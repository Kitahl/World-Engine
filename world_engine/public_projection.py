"""Pure player-facing response projection for WETP and narrative output."""

from __future__ import annotations

from typing import Any

from .turn_policy import turn_directives


ENFORCE_PUBLIC_TURN_FIELDS = (
    "protocol_version",
    "campaign_id",
    "turn_id",
    "mode",
    "status",
    "actor_key",
    "completed_intents",
    "failed_intents",
    "authoritative",
    "idempotent_replay",
    "turn_record_status",
    "retry_blocked",
)
PUBLIC_NRP_TOP_LEVEL_FIELDS = frozenset({
    "packet_version", "engine_version", "enabled", "campaign_id", "turn_id", "mode",
    "activation", "authority", "scene", "narrative_director", "dialogue_plan",
    "style_profile", "motif_thread", "cutscene_packet", "render_contract",
    "quality_contract", "generation_plan", "field_authority", "packet_id", "digest",
    "packet_hash",
})
PRIVATE_NRP_KEYS = frozenset({
    "validation_context", "validation_context_json", "context_digest", "forbidden_literals",
    "information_to_withhold", "forbidden_facts", "facts_to_conceal", "concealed_fact_ids",
    "context_packet", "activation_inspector", "principal", "_engine_receipt",
    "capability_plan", "commit_model", "debug",
})
NARRATIVE_MODES = frozenset({"off", "shadow", "compare", "enforce"})


def _contains_private_nrp_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key) in PRIVATE_NRP_KEYS or _contains_private_nrp_key(item):
                return True
    elif isinstance(value, (list, tuple)):
        return any(_contains_private_nrp_key(item) for item in value)
    return False


def project_public_narrative_packet(packet: dict[str, Any]) -> dict[str, Any]:
    """Validate the closed NRP envelope before it becomes player/model facing."""
    if not isinstance(packet, dict):
        raise ValueError("NARRATIVE_PACKET_INVALID")
    unknown = set(map(str, packet)) - PUBLIC_NRP_TOP_LEVEL_FIELDS
    if unknown or _contains_private_nrp_key(packet):
        raise ValueError("NARRATIVE_PACKET_PRIVATE_FIELD")
    mode = str(packet.get("mode") or "off")
    if mode not in NARRATIVE_MODES:
        raise ValueError("NARRATIVE_PACKET_MODE_INVALID")
    if packet.get("enabled"):
        required = {
            "packet_version", "engine_version", "campaign_id", "turn_id", "packet_id",
            "digest", "packet_hash", "authority", "scene", "render_contract",
            "quality_contract",
        }
        if not required.issubset(packet):
            raise ValueError("NARRATIVE_PACKET_INCOMPLETE")
        if packet.get("packet_version") != "NRP-1.2":
            raise ValueError("NARRATIVE_PACKET_VERSION_INVALID")
    return {key: packet[key] for key in PUBLIC_NRP_TOP_LEVEL_FIELDS if key in packet}




def attach_turn_directives(
    result: dict[str, Any],
    *,
    cue: dict[str, Any] | None = None,
    task: str = "routine",
    trigger_type: str | None = None,
    context: dict[str, Any] | None = None,
    choice_options: list[str] | tuple[str, ...] = (),
    major_consequence: bool = False,
    narrative_packet: dict[str, Any] | None = None,
    narrative_error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Project a turn without importing or initializing the FastAPI app."""
    directives = turn_directives(
        cue=cue,
        task=task,
        trigger_type=trigger_type,
        context=context,
        choice_options=choice_options,
        major_consequence=major_consequence,
        narrative_packet=narrative_packet,
    )
    packet = project_public_narrative_packet(dict(narrative_packet or {}))
    mode = str(packet.get("mode") or "off")
    if mode == "enforce" and packet.get("enabled"):
        out = {key: result[key] for key in ENFORCE_PUBLIC_TURN_FIELDS if key in result}
        out["response_projection_version"] = "WETP-PUBLIC-1.0"
        out["_turn_directives"] = {
            "narrative": directives["narrative"],
            "narrative_runtime": directives["narrative_runtime"],
        }
    else:
        out = dict(result)
        out["_turn_directives"] = directives
    if mode == "shadow" and packet.get("enabled"):
        out["_narrative_shadow"] = packet
    elif mode == "compare" and packet.get("enabled"):
        out["_narrative_compare"] = {
            "baseline_policy": out["_turn_directives"]["narrative"],
            "candidate_packet": packet,
            "player_facing_default": "baseline",
        }
    elif mode == "enforce" and packet.get("enabled"):
        out["_narrative_render_packet"] = packet
    elif mode == "off":
        out["_narrative"] = {"mode": "off", "enabled": False}
    if narrative_error:
        out["_narrative_runtime_error"] = {
            "mode": str(narrative_error.get("mode")) if narrative_error.get("mode") in NARRATIVE_MODES else "off",
            "code": "NARRATIVE_RUNTIME_FAILED",
            "baseline_preserved": bool(narrative_error.get("baseline_preserved", True)),
        }
    return out
