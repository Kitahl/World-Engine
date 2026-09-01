from __future__ import annotations

"""PBEM 2.2 public player-intent boundary for World Engine 4.7.

The module is deliberately narrow.  It does not replace rules resolution,
context authorization, simulation, narrative policy, or authoring.  It is a
pre-execution policy gate for the public player turn surface.  Exact mechanics
remain owned by the existing kernels.
"""

from dataclasses import dataclass
import math
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .engine import WorldEngine


PBEM_VERSION = "2.2"
PBEM_INTEGRATION_VERSION = "WE4.7-PBEM-2.2"

# Public player turns may ask the rules dispatcher to execute an already-authored
# activity or a tightly actor-scoped runtime operation.  Definition/grant/config
# operations remain trusted-authoring/admin work and must not be reachable by a
# player prompt through rules.generic.
PLAYER_SAFE_RULE_OPERATIONS = frozenset({
    "resolve_activity",
    "move",
    "rest",
    "death_save",
    "list_effects",
    "get_actor_rules",
})

# These capabilities write consequences/state directly rather than representing
# a player attempt.  They remain available to trusted internal/admin code, but a
# public player prompt may not use them as if the player authored the result.
DIRECT_CONSEQUENCE_CAPABILITIES = frozenset({
    "actor.condition",
    "actor.resources",
    "social.relationship.adjust",
    "quest.update",
    "progression.manage",
    "world.event.commit",
})

# Legacy rules.attack accepts caller-supplied attack bonus and damage expression.
# PBEM rejects it on the public turn surface so a player/model cannot invent its
# own mechanical authority.  Authored rule activities remain the supported path.
LEGACY_UNSAFE_PUBLIC_CAPABILITIES = frozenset({"rules.attack"})

SKILL_TO_ABILITY = {
    "acrobatics": "dex",
    "animal_handling": "wis",
    "arcana": "int",
    "athletics": "str",
    "deception": "cha",
    "history": "int",
    "insight": "wis",
    "intimidation": "cha",
    "investigation": "int",
    "medicine": "wis",
    "nature": "int",
    "perception": "wis",
    "performance": "cha",
    "persuasion": "cha",
    "religion": "int",
    "sleight_of_hand": "dex",
    "stealth": "dex",
    "survival": "wis",
}
ABILITY_KEYS = frozenset({"str", "dex", "con", "int", "wis", "cha"})

# PBEM 2.2 FPC bands.  They are engine policy constants, not user-provided DCs.
FPC_DC = {"mild": 15, "severe": 22, "world_break": 30}
FPC_SEVERITY_ALIASES = {
    "minor": "mild",
    "major": "severe",
    "world": "world_break",
    "world_narrative_break": "world_break",
}


def _norm_token(value: Any) -> str:
    return str(value or "").strip().lower().replace(" ", "_").replace("-", "_")


@dataclass(frozen=True)
class PBEMDecision:
    decision: str
    code: str
    challengeable: bool
    effective_parameters: dict[str, Any]
    audit: dict[str, Any]

    @property
    def allowed(self) -> bool:
        return self.decision == "allow"

    def public(self) -> dict[str, Any]:
        return {
            "version": PBEM_VERSION,
            "decision": self.decision,
            "code": self.code,
            "challengeable": self.challengeable,
            "audit": dict(self.audit),
        }


class PBEMPolicy:
    """Fail-closed policy for public, player-controlled WETP turns.

    PBEM reads canonical actor/rules state and either:
      * allows the existing capability unchanged,
      * sanitizes a bounded check into canonical modifier/DC inputs, or
      * rejects the intent before its provider can mutate state.

    It never creates class features, spells, inventory, quest state, relationship
    deltas, or other mechanical authority.
    """

    def __init__(self, engine: "WorldEngine"):
        self.e = engine

    @staticmethod
    def _deny(code: str, params: dict[str, Any], *, challengeable: bool = False, **audit: Any) -> PBEMDecision:
        return PBEMDecision("deny", code, challengeable, dict(params), audit)

    @staticmethod
    def _allow(code: str, params: dict[str, Any], **audit: Any) -> PBEMDecision:
        return PBEMDecision("allow", code, False, dict(params), audit)

    def _character(self, campaign_id: str, actor_kind: str | None, actor_id: str | None) -> dict[str, Any] | None:
        if actor_kind != "character" or not actor_id:
            return None
        try:
            return self.e.get_character(campaign_id, actor_id)
        except KeyError:
            return None

    def _rules_profile(self, campaign_id: str, actor_id: str) -> dict[str, Any]:
        try:
            return self.e.rules_dispatch(
                "get_actor_rules",
                campaign_id,
                {"actor_kind": "character", "actor_id": actor_id},
            )
        except (KeyError, ValueError):
            return {"profile": {}, "objects": [], "resources": [], "effects": []}

    @staticmethod
    def _actor_ref_mismatch(
        params: dict[str, Any],
        *,
        actor_id: str,
        id_keys: tuple[str, ...],
        kind_keys: tuple[str, ...] = (),
    ) -> bool:
        for key in kind_keys:
            if key in params and params[key] not in (None, "", "character"):
                return True
        for key in id_keys:
            if key in params and params[key] not in (None, "", actor_id):
                return True
        return False

    @staticmethod
    def _extract_actor_payload(operation: str, params: dict[str, Any]) -> dict[str, Any]:
        if operation == "resolve_activity":
            return params
        return params

    def _rule_operation_decision(
        self,
        campaign_id: str,
        actor_id: str,
        params: dict[str, Any],
    ) -> PBEMDecision:
        operation = _norm_token(params.get("operation"))
        payload = params.get("payload")
        if payload is None:
            payload = {k: v for k, v in params.items() if k != "operation"}
        if not isinstance(payload, dict):
            return self._deny("PBEM_RULE_PAYLOAD_INVALID", params)
        if operation not in PLAYER_SAFE_RULE_OPERATIONS:
            return self._deny(
                "PBEM_RULE_OPERATION_NOT_PLAYER_SAFE",
                params,
                challengeable=False,
                operation=operation or None,
            )

        actor_scoped = operation in {"resolve_activity", "move", "rest", "death_save", "get_actor_rules"}
        if actor_scoped and self._actor_ref_mismatch(
            payload,
            actor_id=actor_id,
            id_keys=("actor_id",),
            kind_keys=("actor_kind",),
        ):
            return self._deny(
                "PBEM_ACTOR_SCOPE_MISMATCH",
                params,
                challengeable=False,
                operation=operation,
            )

        if actor_scoped:
            payload = dict(payload)
            payload["actor_kind"] = "character"
            payload["actor_id"] = actor_id

        if operation == "list_effects":
            # Empty filters mean "all effects" in RulesKernel, which is too broad
            # for a player principal.  Bind the read to the controlled character.
            payload = dict(payload)
            payload["actor_kind"] = "character"
            payload["actor_id"] = actor_id

        if operation == "resolve_activity":
            payload = dict(payload)
            payload["actor_kind"] = "character"
            payload["actor_id"] = actor_id
            activity_id = payload.get("activity_id")
            if not activity_id:
                return self._deny("PBEM_ACTIVITY_REQUIRED", params, challengeable=False)
            try:
                activity = self.e.rules_dispatch("get_actor_rules", campaign_id, {
                    "actor_kind": "character", "actor_id": actor_id,
                })
                # Ownership is deliberately checked only at the rule-object level;
                # targeting, resources, action economy, conditions, range, saves,
                # damage, and effects remain RulesKernel responsibilities.
                with self.e._db() as db:
                    row = db.execute(
                        "SELECT object_id,enabled FROM rule_activities WHERE campaign_id=? AND id=?",
                        (campaign_id, str(activity_id)),
                    ).fetchone()
                    if row is None or not bool(row["enabled"]):
                        return self._deny("PBEM_ACTIVITY_NOT_AVAILABLE", params, challengeable=False)
                    object_id = row["object_id"]
                if not object_id:
                    return self._deny(
                        "PBEM_ACTIVITY_UNBOUND_TO_RULE_OBJECT",
                        params,
                        challengeable=False,
                        activity_id=str(activity_id),
                    )
                owned = {str(item.get("id")) for item in activity.get("objects", [])}
                if str(object_id) not in owned:
                    return self._deny(
                        "PBEM_RULE_OBJECT_NOT_OWNED",
                        params,
                        challengeable=False,
                        activity_id=str(activity_id),
                        object_id=str(object_id),
                    )
            except (KeyError, ValueError):
                return self._deny("PBEM_ACTIVITY_NOT_AVAILABLE", params, challengeable=False)

        effective = {"operation": operation, "payload": payload}
        return self._allow("PBEM_RULE_OPERATION_ALLOWED", effective, operation=operation)

    def _check_modifier(self, campaign_id: str, actor_id: str, *, ability: str, skill: str | None = None) -> tuple[int, dict[str, Any]]:
        actor = self.e.get_character(campaign_id, actor_id)
        ability = _norm_token(ability)
        if ability not in ABILITY_KEYS:
            raise ValueError("invalid ability")
        abilities = actor.get("abilities") if isinstance(actor.get("abilities"), dict) else {}
        ability_mod = int(abilities.get(ability, 0) or 0)
        prof = int(actor.get("proficiency_bonus", 2) or 0)
        rules = self._rules_profile(campaign_id, actor_id)
        profile = rules.get("profile") if isinstance(rules.get("profile"), dict) else {}
        proficiencies = {_norm_token(x) for x in (profile.get("skill_proficiencies") or [])}
        metadata = profile.get("metadata") if isinstance(profile.get("metadata"), dict) else {}
        expertise = {
            _norm_token(x)
            for key in ("expertise", "skill_expertise", "expertise_skills")
            for x in (metadata.get(key) or [])
            if isinstance(metadata.get(key) or [], (list, tuple, set))
        }
        skill_token = _norm_token(skill) if skill else None
        proficient = bool(skill_token and skill_token in proficiencies)
        expert = bool(skill_token and skill_token in expertise)
        multiplier = 2 if expert else (1 if proficient else 0)
        modifier = ability_mod + prof * multiplier
        return modifier, {
            "basis": "authoritative_actor_state",
            "ability": ability,
            "skill": skill_token,
            "ability_modifier": ability_mod,
            "proficiency_bonus": prof,
            "proficient": proficient,
            "expertise": expert,
            "computed_modifier": modifier,
        }

    def _check_decision(self, campaign_id: str, actor_id: str, params: dict[str, Any]) -> PBEMDecision:
        p = dict(params)
        fpc = p.get("pbem_fpc")
        if fpc:
            spec = dict(fpc) if isinstance(fpc, dict) else {}
            severity = _norm_token(spec.get("severity") or p.get("severity") or "severe")
            severity = FPC_SEVERITY_ALIASES.get(severity, severity)
            if severity not in FPC_DC:
                return self._deny("PBEM_FPC_SEVERITY_INVALID", params, challengeable=False)
            skill = _norm_token(spec.get("skill") or p.get("skill")) or None
            ability = _norm_token(spec.get("ability") or p.get("ability")) or None
            if skill:
                mapped = SKILL_TO_ABILITY.get(skill)
                if mapped is None:
                    return self._deny("PBEM_FPC_SKILL_INVALID", params, challengeable=False)
                if ability and ability != mapped:
                    return self._deny("PBEM_FPC_ABILITY_SKILL_MISMATCH", params, challengeable=False)
                ability = mapped
            if not ability or ability not in ABILITY_KEYS:
                return self._deny("PBEM_FPC_CHECK_BASIS_REQUIRED", params, challengeable=True)
            try:
                modifier, basis = self._check_modifier(campaign_id, actor_id, ability=ability, skill=skill)
            except (TypeError, ValueError):
                return self._deny("PBEM_FPC_ACTOR_CHECK_STATE_INVALID", params, challengeable=False)
            # FPC is a server-owned challenge protocol; caller-provided
            # advantage/disadvantage must not lower or raise its boundary.
            effective = {"modifier": modifier, "dc": FPC_DC[severity], "mode": "normal"}
            return self._allow(
                "PBEM_FPC_CHECK_AUTHORIZED",
                effective,
                fpc=True,
                severity=severity,
                dc=FPC_DC[severity],
                outcome_scope="constrained_only",
                **basis,
            )

        # Preferred ordinary-check path: derive the modifier from the controlled
        # actor when an ability/skill basis is supplied.
        skill = _norm_token(p.get("skill")) or None
        ability = _norm_token(p.get("ability")) or None
        if skill or ability:
            if skill:
                mapped = SKILL_TO_ABILITY.get(skill)
                if mapped is None:
                    return self._deny("PBEM_CHECK_SKILL_INVALID", params)
                if ability and ability != mapped:
                    return self._deny("PBEM_CHECK_ABILITY_SKILL_MISMATCH", params)
                ability = mapped
            if not ability or ability not in ABILITY_KEYS:
                return self._deny("PBEM_CHECK_BASIS_REQUIRED", params)
            try:
                modifier, basis = self._check_modifier(campaign_id, actor_id, ability=ability, skill=skill)
                dc = int(p.get("dc", 10))
            except (TypeError, ValueError):
                return self._deny("PBEM_CHECK_NUMERIC_INPUT_INVALID", params)
            if not 1 <= dc <= 30:
                return self._deny("PBEM_CHECK_DC_OUT_OF_PUBLIC_RANGE", params)
            effective = {"modifier": modifier, "dc": dc, "mode": p.get("mode", "normal")}
            return self._allow("PBEM_ACTOR_CHECK_AUTHORIZED", effective, **basis, dc=dc)

        # Backward compatibility for existing v4.3 GPT clients: numeric checks are
        # admitted only when the supplied positive modifier fits an actor-derived
        # upper envelope.  New clients should always send ability/skill.
        if "modifier" not in p or "dc" not in p:
            return self._deny("PBEM_CHECK_BASIS_REQUIRED", params, challengeable=True)
        try:
            supplied_modifier = int(p["modifier"])
            dc = int(p["dc"])
        except (TypeError, ValueError):
            return self._deny("PBEM_CHECK_NUMERIC_INPUT_INVALID", params)
        if not 1 <= dc <= 30:
            return self._deny("PBEM_CHECK_DC_OUT_OF_PUBLIC_RANGE", params)
        actor = self.e.get_character(campaign_id, actor_id)
        abilities = actor.get("abilities") if isinstance(actor.get("abilities"), dict) else {}
        max_ability = max([int(v) for v in abilities.values() if isinstance(v, (int, float))] or [0])
        prof = int(actor.get("proficiency_bonus", 2) or 0)
        rules = self._rules_profile(campaign_id, actor_id)
        profile = rules.get("profile") if isinstance(rules.get("profile"), dict) else {}
        metadata = profile.get("metadata") if isinstance(profile.get("metadata"), dict) else {}
        explicit_bonus = int(metadata.get("pbem_check_bonus_cap", 0) or 0)
        upper = max_ability + (2 * max(0, prof)) + max(0, explicit_bonus)
        if supplied_modifier > upper:
            return self._deny(
                "PBEM_CHECK_MODIFIER_EXCEEDS_ACTOR_ENVELOPE",
                params,
                challengeable=False,
                supplied_modifier=supplied_modifier,
                actor_modifier_upper_bound=upper,
            )
        effective = {"modifier": supplied_modifier, "dc": dc, "mode": p.get("mode", "normal")}
        return self._allow(
            "PBEM_LEGACY_NUMERIC_CHECK_ALLOWED",
            effective,
            legacy_numeric_check=True,
            actor_modifier_upper_bound=upper,
        )

    def evaluate(
        self,
        campaign_id: str,
        *,
        actor_kind: str | None,
        actor_id: str | None,
        capability_id: str,
        parameters: dict[str, Any],
        successful_prerequisite: bool = False,
    ) -> PBEMDecision:
        params = dict(parameters or {})
        capability_id = str(capability_id or "").strip().lower()

        # Read/presentation operations can be actorless; confidentiality remains
        # enforced by the context authorization layer and the public capability
        # allowlist, not PBEM.
        if capability_id in {"context.compile", "space.route", "visual.cue"}:
            return self._allow("PBEM_NON_AUTHORITATIVE_CAPABILITY", params)

        character = self._character(campaign_id, actor_kind, actor_id)
        if character is None:
            return self._deny("PBEM_CONTROLLED_CHARACTER_REQUIRED", params, challengeable=False)
        assert actor_id is not None

        if capability_id == "npc.dialogue.context":
            npc_id = str(params.get("npc_id") or params.get("target_id") or "").strip()
            if not npc_id:
                return self._deny("PBEM_DIALOGUE_TARGET_REQUIRED", params)
            with self.e._db() as db:
                npc = db.execute(
                    "SELECT location FROM npcs WHERE campaign_id=? AND id=?",
                    (campaign_id, npc_id),
                ).fetchone()
            if npc is None:
                return self._deny("PBEM_DIALOGUE_TARGET_NOT_FOUND", params)
            if str(npc["location"]) != str(character.get("location") or ""):
                return self._deny(
                    "PBEM_DIALOGUE_TARGET_NOT_LOCAL",
                    params,
                    challengeable=True,
                )
            effective = dict(params)
            effective.pop("target_id", None)
            effective["npc_id"] = npc_id
            return self._allow("PBEM_DIALOGUE_TARGET_LOCAL", effective)

        if capability_id in DIRECT_CONSEQUENCE_CAPABILITIES:
            return self._deny(
                "PBEM_DIRECT_CONSEQUENCE_WRITE_FORBIDDEN",
                params,
                challengeable=False,
                capability_id=capability_id,
            )
        if capability_id in LEGACY_UNSAFE_PUBLIC_CAPABILITIES:
            return self._deny(
                "PBEM_LEGACY_MECHANICS_INPUT_FORBIDDEN",
                params,
                challengeable=False,
                capability_id=capability_id,
                replacement="rules.generic:resolve_activity",
            )

        if capability_id == "actor.move":
            if self._actor_ref_mismatch(
                params, actor_id=actor_id,
                id_keys=("actor_id",), kind_keys=("kind",),
            ):
                return self._deny("PBEM_ACTOR_SCOPE_MISMATCH", params, challengeable=False)
            effective = dict(params)
            effective["kind"] = "character"
            effective["actor_id"] = actor_id
            destination = str(effective.get("destination") or "")
            current = str(character.get("location") or "")
            if destination and destination != current:
                with self.e._db() as db:
                    adjacent = db.execute(
                        "SELECT 1 FROM location_links WHERE campaign_id=? AND from_id=? AND to_id=?",
                        (campaign_id, current, destination),
                    ).fetchone() is not None
                if not adjacent and not successful_prerequisite:
                    return self._deny(
                        "PBEM_MOVE_REQUIRES_SUCCESSFUL_FPC",
                        effective,
                        challengeable=True,
                        current_location=current,
                        destination=destination,
                    )
            return self._allow("PBEM_ACTOR_BOUND_MOVE", effective)

        if capability_id == "rules.check":
            return self._check_decision(campaign_id, actor_id, params)

        if capability_id == "rules.generic":
            return self._rule_operation_decision(campaign_id, actor_id, params)

        if capability_id == "environment.interact":
            action = _norm_token(params.get("action"))
            if action not in {"inspect", "ignite", "extinguish", "douse"}:
                return self._deny("PBEM_ENVIRONMENT_ACTION_NOT_PLAYER_SAFE", params, challengeable=False)
            if not isinstance(params.get("target"), dict):
                return self._deny("PBEM_ENVIRONMENT_TARGET_REQUIRED", params, challengeable=False)
            return self._allow("PBEM_ENVIRONMENT_INTERACTION_ACTOR_BOUND", params)

        if capability_id == "economy.interact":
            if self._actor_ref_mismatch(
                params,
                actor_id=actor_id,
                id_keys=("actor_id", "owner_id"),
                kind_keys=("actor_kind", "owner_kind"),
            ):
                return self._deny("PBEM_ACTOR_SCOPE_MISMATCH", params, challengeable=False)
            action = _norm_token(params.get("action") or "inspect")
            if action not in {"inspect", "browse", "market", "quote", "buy", "sell"}:
                return self._deny("PBEM_ECONOMY_ACTION_NOT_PLAYER_SAFE", params, challengeable=False)
            market_id = params.get("market_id") or params.get("market") or params.get("target_id")
            if not market_id:
                return self._deny("PBEM_ECONOMY_MARKET_REQUIRED", params)
            item_id = params.get("item_id") or params.get("item")
            if action in {"quote", "buy", "sell"} and not item_id:
                return self._deny("PBEM_ECONOMY_ITEM_REQUIRED", params)
            try:
                qty = float(params.get("qty", params.get("quantity", 1)))
            except (TypeError, ValueError):
                return self._deny("PBEM_ECONOMY_QUANTITY_INVALID", params)
            if not math.isfinite(qty) or qty <= 0 or qty > 1_000_000_000_000:
                return self._deny("PBEM_ECONOMY_QUANTITY_INVALID", params)
            effective = {
                "action": action,
                "market_id": str(market_id),
                "item_id": str(item_id) if item_id is not None else None,
                "qty": qty,
                "reason": str(params.get("reason") or "player market interaction")[:500],
            }
            return self._allow(
                "PBEM_ECONOMY_INTERACTION_ACTOR_BOUND",
                effective,
                stripped_identity_or_replay_keys=any(
                    key in params
                    for key in ("actor_kind", "actor_id", "owner_kind", "owner_id", "transaction_key", "idempotency_key")
                ),
            )

        if capability_id == "population.inspect":
            try:
                limit = int(params.get("limit", 50))
            except (TypeError, ValueError):
                return self._deny("PBEM_POPULATION_LIMIT_INVALID", params)
            if not 1 <= limit <= 100:
                return self._deny("PBEM_POPULATION_LIMIT_INVALID", params)
            return self._allow(
                "PBEM_POPULATION_INSPECTION_ACTOR_LOCAL",
                {"location_id": character.get("location"), "limit": limit},
                stripped_location_override=any(
                    key in params for key in ("location_id", "location", "target_id")
                ),
            )

        if capability_id == "world.advance":
            try:
                minutes = int(params.get("minutes", 0))
            except (TypeError, ValueError):
                return self._deny("PBEM_TIME_ADVANCE_INVALID", params)
            if not 0 <= minutes <= 60 * 24:
                return self._deny("PBEM_TIME_ADVANCE_OUT_OF_RANGE", params)
            # Time passage is a legitimate player intent; weather/season and the
            # choice to bypass simulation are world-authority inputs, not player
            # inputs.  Keep those owned by SimulationKernel.
            effective = {
                "minutes": minutes,
                "reason": str(params.get("reason") or "elapsed time")[:500],
                "simulate": True,
            }
            return self._allow(
                "PBEM_TIME_ADVANCE_ALLOWED", effective,
                stripped_world_overrides=any(k in params for k in ("weather", "season", "simulate")),
            )

        if capability_id == "combat.start":
            participants = params.get("participants") or []
            if not isinstance(participants, list):
                return self._deny("PBEM_COMBAT_PARTICIPANTS_INVALID", params)
            actor_present = any(
                isinstance(item, dict)
                and item.get("kind") == "character"
                and item.get("id") == actor_id
                for item in participants
            )
            if not actor_present:
                return self._deny("PBEM_COMBAT_MUST_INCLUDE_CONTROLLED_ACTOR", params, challengeable=False)
            return self._allow("PBEM_COMBAT_START_ACTOR_BOUND", params)

        if capability_id in {"combat.next", "combat.end"}:
            combat_id = params.get("combat_id")
            if not combat_id:
                return self._deny("PBEM_COMBAT_ID_REQUIRED", params)
            try:
                combat = self.e.get_combat(campaign_id, str(combat_id))
            except KeyError:
                return self._deny("PBEM_COMBAT_NOT_FOUND", params)
            if not any(
                item.get("kind") == "character" and item.get("id") == actor_id
                for item in combat.get("participants", [])
                if isinstance(item, dict)
            ):
                return self._deny("PBEM_COMBAT_ACTOR_NOT_PARTICIPANT", params, challengeable=False)
            return self._allow("PBEM_COMBAT_ACTOR_BOUND", params)

        # Anything that reaches this branch was explicitly admitted by the
        # public API but has no reviewed PBEM rule.  Fail closed rather than let
        # future capabilities silently bypass the boundary.
        return self._deny(
            "PBEM_CAPABILITY_NOT_REVIEWED_FOR_PLAYER_SURFACE",
            params,
            challengeable=False,
            capability_id=capability_id,
        )


def explicit_step_success(step: dict[str, Any]) -> bool | None:
    """Return an explicit success signal for requires_success_of.

    None means the referenced step completed but did not publish a boolean
    success signal, so a success-gated dependent action must fail closed.
    """

    if step.get("status") != "completed":
        return False
    result = step.get("result")
    if not isinstance(result, dict):
        return None
    if isinstance(result.get("success"), bool):
        return bool(result["success"])
    check = result.get("check")
    if isinstance(check, dict) and isinstance(check.get("success"), bool):
        return bool(check["success"])
    return None
