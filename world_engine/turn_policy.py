from __future__ import annotations

from typing import Any, Sequence


FAST = "fast"
STANDARD = "standard"
DEEP = "deep"


def select_reasoning_profile(
    *,
    task: str = "routine",
    trigger_type: str | None = None,
    context: dict[str, Any] | None = None,
    choice_options: Sequence[str] = (),
    major_consequence: bool = False,
) -> dict[str, Any]:
    """Return a deterministic orchestration recommendation for model reasoning effort.

    This is deliberately not a claim that the Action can change ChatGPT's reasoning
    slider.  World Engine can classify the turn and explain the classification; the
    ChatGPT product remains responsible for any automatic reasoning escalation.

    Design rule: deterministic mechanics stay cheap because the backend resolves
    them.  More model reasoning is reserved for synthesis, ambiguity and broad
    persistent consequences.
    """
    ctx = context or {}
    task = str(task or "routine").strip().lower()
    trigger = str(trigger_type or "").strip().lower()
    score = 0
    reasons: list[str] = []

    if task in {"world_generation", "world_bible", "custom_setting", "authoring", "campaign_setup"}:
        score += 8
        reasons.append("world/content synthesis")
    elif task in {"major_plot", "politics", "quest_branch"}:
        score += 6
        reasons.append("major persistent narrative consequences")
    elif task == "multi_system":
        score += 5
        reasons.append("multiple persistent systems may interact")
    elif task in {"combat", "rules", "routine_check", "movement"}:
        score += 1
        reasons.append("deterministic backend mechanics")

    # New scenes deserve enough reasoning for coherent staging and continuity, but
    # they do not normally need the same effort as world generation.
    if trigger in {"scene_start", "new_location"}:
        score += 3
        reasons.append("new scene/location synthesis")
    elif trigger == "battle_start":
        score += 2
        reasons.append("battle staging")
    elif trigger == "event_choice":
        score += 3
        reasons.append("player decision point")

    option_count = len([x for x in choice_options if str(x).strip()])
    if option_count >= 4:
        score += 2
        reasons.append("four-or-more meaningful options")
    elif option_count >= 2:
        score += 1
        reasons.append("multiple meaningful options")

    if major_consequence:
        score += 3
        reasons.append("major persistent consequence")

    director_count = int(ctx.get("director_count") or 0)
    if director_count >= 3:
        score += 2
        reasons.append("overlapping authorities")
    elif director_count:
        score += 1
        reasons.append("active authority context")

    active_combats = ctx.get("active_combats") or []
    if isinstance(active_combats, list) and len(active_combats) > 1:
        score += 2
        reasons.append("multiple active combats")

    quest_counts = ctx.get("quest_counts") or {}
    if isinstance(quest_counts, dict):
        active_quests = int(quest_counts.get("active") or 0)
        if active_quests >= 4:
            score += 1
            reasons.append("dense active quest state")

    if score >= 7:
        profile = DEEP
        recommended_level = "High"
        effort = "extended"
    elif score >= 3:
        profile = STANDARD
        recommended_level = "Medium"
        effort = "standard"
    else:
        profile = FAST
        recommended_level = "Instant"
        effort = "light"

    return {
        "profile": profile,
        "score": score,
        "recommended_reasoning_level": recommended_level,
        # Compatibility name retained for clients from the early v3.9.2 draft.
        "recommended_chatgpt_mode": recommended_level,
        "recommended_effort": effort,
        "reasons": reasons or ["routine scene"],
        "automatic_policy": True,
        "platform_note": (
            "Advisory only: a GPT Action response cannot change the user's ChatGPT reasoning slider. "
            "When ChatGPT automatic reasoning/Higher intelligence is enabled, the platform may automatically "
            "use more reasoning for complex requests."
        ),
    }


def narrative_policy(*, task: str="routine", trigger_type: str | None=None, major_consequence: bool=False) -> dict[str, Any]:
    """Player-facing prose guidance. Mechanics remain authoritative but mostly hidden."""
    task=str(task or "routine").lower(); trigger=str(trigger_type or "").lower()
    if trigger in {"scene_start","new_location"}:
        lo,hi,kind=350,550,"scene_opening"
    elif task in {"world_generation","campaign_setup","character_creation"}:
        lo,hi,kind=180,350,"setup_or_reveal"
    elif task in {"major_plot","quest_branch","politics","multi_system"} or major_consequence:
        lo,hi,kind=300,550,"major_consequence"
    elif task=="combat" or trigger=="battle_start":
        lo,hi,kind=90,180,"combat_beat"
    elif task in {"dialogue","npc_interaction","npc_introduction"}:
        lo,hi,kind=120,280,"dialogue_scene"
    elif task in {"routine_check","movement","rules"}:
        lo,hi,kind=100,220,"action_result"
    else:
        lo,hi,kind=120,250,"routine_adventure"
    return {
        "style":"novel-like narrative adventure prose",
        "response_kind":kind,"target_words":{"min":lo,"max":hi},
        "full_cinematic_scene_words":{"min":450,"max":700},
        "choice_or_question_words":{"min":30,"max":90},
        "dialogue_guidance":"Natural speech shaped by role/status, culture, faction, relationship, mood, beliefs, goals, recent memories and current motives. Interleave dialogue with brief action/body language. Usually 1-4 spoken sentences per NPC turn; avoid exposition dumps.",
        "description_guidance":"Use selective sensory detail, concrete verbs and varied sentence length. Avoid repetitive 'you see/you notice' phrasing and excessive adjective stacks.",
        "player_agency":"Do not invent the player character's private thoughts, feelings, decisions or dialogue. Sensory impressions, environmental implication, authoritative forced movement and mechanically supported involuntary physical effects are allowed; inner thought requires explicit player input.",
        "interiority_policy":"player_locked",
        "mechanics_visibility":"Keep hidden simulation/audit state out of ordinary prose. Surface numbers when tactically useful, explicitly requested, or required for combat/level-up clarity.",
        "ending":"Usually end on a live situation, natural question, or concise choices when choices genuinely help. Always accept free-form action.",
    }

def image_directive(cue: dict[str, Any] | None) -> dict[str, Any]:
    cue = dict(cue or {})
    should = bool(cue.get("should_generate"))
    return {
        "required": should,
        "tool": "native_chatgpt_image_generation",
        "order": "before_narration" if should else "none",
        "record_after_generation": should,
        "capability_requirement": "Enable the GPT's built-in Image generation capability.",
        "cue": cue,
        "instruction": (
            "MANDATORY WHEN THE GPT IMAGE CAPABILITY IS AVAILABLE: generate exactly one native ChatGPT scene "
            "image from cue.prompt before continuing the scene, then call recordImageGeneration only after "
            "successful generation. Do not substitute a prose-only description for a required image."
            if should
            else "No image generation is required for this turn."
        ),
    }


def turn_directives(
    *,
    cue: dict[str, Any] | None = None,
    task: str = "routine",
    trigger_type: str | None = None,
    context: dict[str, Any] | None = None,
    choice_options: Sequence[str] = (),
    major_consequence: bool = False,
    narrative_packet: dict[str, Any] | None = None,
) -> dict[str, Any]:
    packet = dict(narrative_packet or {})
    narrative_mode = str(packet.get("mode") or "off")
    if narrative_mode == "enforce":
        runtime_instruction = "Render only from _narrative_render_packet, then call publishPresentation with the exact packet, turn, revision, choices, and narration before displaying it. Display only an accepted publication."
    elif narrative_mode == "compare":
        runtime_instruction = "Keep the baseline player-facing output; independently render the candidate packet for blinded comparison. Do not consume beats or motifs until an accepted output is recorded."
    elif narrative_mode == "shadow":
        runtime_instruction = "Do not expose the shadow packet to the player. Keep current narrative policy as baseline; store the packet for comparison and quality evaluation."
    else:
        runtime_instruction = "Use the existing narrative policy; the narrative runtime is off."
    return {
        "image": image_directive(cue),
        "decision_image_policy": {
            "automatic": True,
            "when": "before presenting two or more meaningful player choices or a major irreversible decision",
            "action": "buildImageCue",
            "trigger_type": "event_choice",
            "instruction": "Build the event_choice cue, generate the native image first when should_generate=true, then present the choices.",
        },
        "narrative": narrative_policy(task=task, trigger_type=trigger_type, major_consequence=major_consequence),
        "narrative_runtime": {
            "engine_version": packet.get("engine_version", "4.3.0"),
            "packet_version": packet.get("packet_version", "NRP-1.2"),
            "mode": narrative_mode,
            "packet_id": packet.get("packet_id"),
            "digest": packet.get("digest"),
            "player_facing_candidate": narrative_mode == "enforce",
            "instruction": runtime_instruction,
        },
        "reasoning": select_reasoning_profile(
            task=task,
            trigger_type=trigger_type,
            context=context,
            choice_options=choice_options,
            major_consequence=major_consequence,
        ),
    }
