# WETP-1.0 — World Engine Turn Protocol

## Purpose

WETP standardizes how one player prompt is converted into bounded authoritative work. It prevents ChatGPT from receiving the entire database, choosing arbitrary internal tables, or narrating mutations the backend did not commit.

## Entry point

```http
POST /api/turn
operationId: resolveTurn
```

## Request envelope

```json
{
  "campaign_id": "main",
  "actor_kind": "character",
  "actor_id": "avelin",
  "player_text": "I question Mara, then follow the eastern road if she lies.",
  "expected_revision": 918,
  "idempotency_key": "session-42:user-message-107",
  "mode": "execute",
  "continue_on_error": false,
  "max_context_chars": 18000,
  "include_archive": false,
  "intents": [
    {
      "id": "ask-mara",
      "type": "interact",
      "params": {"target_id": "mara", "topic": "caravan_attack"}
    },
    {
      "id": "follow-road",
      "type": "move",
      "depends_on": ["ask-mara"],
      "params": {"location": "eastern_road"}
    }
  ]
}
```

ChatGPT performs natural-language interpretation. The backend validates only explicit structured intents; it does not infer unrequested player actions.

## Modes

| Mode | Mutation | Output |
|---|---:|---|
| `execute` | according to capability | context, plan, steps, revisions, directives |
| `plan` | no | normalized intents + capability plan + context |
| `context_only` | no | bounded context packet and activation inspector |
| `capabilities` | no | enabled capability contracts |

## Capability modes

Every manifest has exactly one authority mode:

- `READ`: retrieve authoritative information;
- `RESOLVED`: compute and commit a deterministic requested action;
- `SIMULATED`: advance autonomous world state;
- `NARRATED`: provide facts/directives for model-authored presentation;
- `AUTHOR`: stage/validate/dry-run/promote missing reusable content.

## Context tiers

- `HOT`: actor, current location, SCENE, combat and direct action state;
- `WARM`: nearby entities, relations, active quests and relevant knowledge;
- `COLD`: regional summaries and broader systems;
- `ARCHIVE`: opt-in historical records.

The compiler deterministically orders candidates, enforces the character budget, records included/omitted items and produces a digest.

## Dependency and failure semantics

- `depends_on` creates an explicit directed dependency.
- Unknown, duplicate or cyclic intent IDs fail validation.
- Required dependency failure causes the dependent step to be skipped.
- `optional=true` permits a failed optional step without reclassifying successful required work.
- Default `continue_on_error=false` stops after the first required failure.
- Completed earlier commands remain committed because the turn model is atomic per command.

## Revisions

Mutating requests should carry `expected_revision`. A mismatch causes a revision conflict before mutation. The caller must re-read context and re-plan.

## Idempotency

`idempotency_key` identifies the user turn. Repeating the same committed request returns the recorded result with `idempotent_replay=true` instead of duplicating mutations. Reusing a key with conflicting content is rejected.

## Response envelope

```json
{
  "protocol_version": "WETP-1.0",
  "turn_id": "...",
  "status": "completed",
  "capability_plan": [],
  "context_packet": {},
  "steps": [
    {"intent_id": "ask-mara", "status": "completed", "result": {}}
  ],
  "revision_before": 918,
  "revision_after": 919,
  "idempotent_replay": false,
  "_turn_directives": {},
  "_engine_receipt": {}
}
```

Only `completed` step results may be narrated as facts.

## Low-level Actions

The existing low-level Actions remain for campaign/setup, diagnostics, image recording, and bounded edge cases. Normal gameplay should prefer `resolveTurn` so context, routing, dependency, revision, and idempotency behavior is standardized.
