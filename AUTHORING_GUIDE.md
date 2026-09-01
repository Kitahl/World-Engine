# World Engine 4.7 — Safe Operator Authoring

## Invariant

**The model authors content. The database owns facts. The deterministic tick owns runtime decisions.**

The operator may stage a `world_bible`, NPC archetypes, rules, reactions, recipes, mechanism operators, geography/climate, economy/logistics, settlement/population aggregates, and lazy NPC instances. Runtime decisions remain deterministic and canonical.

## Trusted local workflow

Authoring is not a public GPT Action. Use the standalone Companion Forge or the authenticated trusted operator API. The core actions are:

1. `stage` — store one structured bulk payload in an authoring batch.
2. `validate` — static schema/reference/safety validation.
3. `dry_run` — clone the live database, promote into the clone, simulate at most 365 days, return digest/checks/warnings, then delete the clone.
4. `promote` — atomically promote only a batch whose latest dry-run passed.
5. `materialization_brief` — return the simulated aggregates + world bible for a location that needs named detail.
6. `digest` — compact current-world metrics for critique/tuning.
7. `lock` — explicitly canon-lock a generated object.
8. `list_gaps` / `log_gap` / `resolve_gap` — reactive authoring.

## Bootstrap mode

Session-zero content should normally be structured and reusable:

- one `world_bible`;
- ~20 NPC archetypes;
- a small set of rule templates;
- region/location aggregates;
- shared actions/reactions/recipes/items;
- canonical mechanism operators;
- finite markets, inventories, balances, production and logistics;
- settlement profiles and aggregate population cohorts;
- thin NPC instances only where already needed.

Prefer one structured payload over hundreds of sequential API calls.

## Lazy materialisation

When trusted context reports `content_materialization.needs_materialization=true`:

1. Request the trusted local `materialization_brief` for the location.
2. Generate named detail consistent with the returned aggregates and `world_bible`.
3. Use archetype references + deviations rather than bespoke behavior definitions.
4. Stage → validate → dry-run → promote the lazy batch.
5. Materialized NPCs have `materialized=1`.
6. If real gameplay mutates a materialized NPC, the backend automatically canon-locks it.
7. When a scene ends, a materialized NPC with no canon lock, no persistent event and no relationship is deleted from named state; the WORLD aggregate remains unchanged.

## Reactive authoring

A CHANCE rule may declare:

```json
{
  "requires_content": {
    "kind": "faction",
    "id": "red_knives",
    "gap_key": "faction:red_knives",
    "summary": "Bandit activity requires a named faction"
  }
}
```

If the referenced content does not exist, the simulation logs a `content_gap` and suppresses the unresolved event rather than inventing the missing faction at runtime. `getWorldContext.open_content_gaps` surfaces the gap next turn.

## Static gate

Generated content is rejected when it contains, among other things:

- unsupported archetypes or targets;
- invalid foreign references;
- generated CHANCE probability above `0.2`;
- DRIFT `k <= 0` or `k > 0.5`;
- invalid need values/curves;
- missing reaction `repeat_policy`;
- unsafe self-triggering reactions;
- excessive action weights;
- invalid recipe references, DC or time;
- invalid/non-finite mechanism, economy, or population values;
- broken market, route, resource, settlement, or cohort references;
- any attempted overwrite of canon-locked rows.

Very small drift values are **warned, not automatically rejected**, because v3.4+ uses fractional drift accumulators and no longer suffers the original integer-rounding permanent no-op.

## Dry-run gate

A dry-run is done on a scratch SQLite copy; it never mutates the live campaign. It checks:

- population remains inside a broad declared safety band;
- no cascade safety cap was hit;
- relationships do not saturate at their bounds en masse;
- total event volume remains bounded.

It additionally warns on:

- severe social entropy;
- 95%+ final action monoculture;
- >50% need saturation;
- depleted resource stocks.

Ordinary `sim_decision` events remain in the causal queue during validation but are not persisted to the scratch event ledger, preventing a 200-agent dry-run from generating tens of thousands of low-value history rows.

## Canon lock

Canon locks are enforced during validation and promotion. Once an NPC is materially involved in gameplay, public gameplay mutators automatically lock a generated NPC. The explicit trusted-local `lock` action remains available for facts that become canon through narration without a state mutation.

## World bible

The world bible is included in trusted context and lazy materialisation briefs, and translated into qualitative constraints in image prompts. It is the stable source for setting tone, technology, naming, magic prevalence and similar generation constants.
