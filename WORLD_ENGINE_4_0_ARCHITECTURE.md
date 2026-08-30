# World Engine 4.0 Architecture

## Design objective

Convert World Engine from a collection of powerful subsystems into one routable authoritative platform. The architecture follows eight foundational domains instead of implementing hundreds of isolated feature modules.

## Eight foundations

| # | Foundation | v4.0 status |
|---:|---|---|
| 1 | Entity + Relationship Graph | universal registry/relations implemented |
| 2 | Space / Geography / Pathfinding | existing graph + sparse x/y/z routing registered |
| 3 | Population + Lifecycle | existing population/lifecycle providers registered; deeper cohorts remain future work |
| 4 | Economy + Production + Logistics | existing stocks/recipes/rewards registered; full logistics provider remains future work |
| 5 | Politics + Law + Warfare | faction provider registered; full treaty/war-goal layer remains future work |
| 6 | Ecology + Environment | weather/resources/wildlife/world-state foundation retained; dense physics rejected |
| 7 | Autonomous Agent Planning + Knowledge | DECIDE/GOAP/cognition plus facts/beliefs/transfers implemented |
| 8 | Authoritative State / Event / Memory | SQLite, revisions, transactions, event ledger, WETP, context compiler implemented |

## Component map

```text
ChatGPT
  ├─ player-language interpretation
  ├─ narrative/dialogue
  ├─ image invocation
  └─ calls resolveTurn
          │
          ▼
TurnRouter
  ├─ IntentNormalizer
  ├─ CapabilityRegistry (30 manifests)
  ├─ UniversalEntityGraph
  ├─ KnowledgeProvenance
  ├─ ContextCompiler
  ├─ DependencyPlanner
  ├─ RevisionGuard
  ├─ IdempotencyLedger
  ├─ Narrative Director / Dialogue Planner / Quality Gate
  └─ ProviderDispatcher
          │
          ├─ core engine
          ├─ rules kernel
          ├─ simulation kernel
          ├─ NPC life / GOAP
          ├─ world systems / spatial map
          ├─ authoring kernel
          └─ visual/music directives
                  │
                  ▼
              SQLite schema 14
```

## Universal entity graph

Typed game tables remain authoritative for domain-specific fields. `we4_entities` supplies a universal key and stable identity across characters, NPCs, factions, locations, items, organizations and future entity types. `we4_relations` supplies generalized typed edges without replacing efficient typed tables.

## Knowledge separation

`we4_facts` stores canonical propositions. `we4_beliefs` records what a particular entity believes about a fact. `we4_information_transfers` records communication provenance and mutation lineage. This prevents dialogue from automatically granting every NPC access to world truth.

## Context inspection

Every compilation is persisted with:

- requested capabilities;
- actor and location;
- context budget;
- included and omitted records;
- activation reasons;
- estimated token count;
- deterministic digest.

This is an auditable alternative to hidden prompt dumping.

## Extension contract

A future provider should add a capability manifest rather than a new GPT endpoint whenever possible. The manifest declares:

- stable capability ID;
- authority mode;
- provider/domain/version;
- required inputs;
- written state classes;
- context tiers;
- input schema;
- priority and metadata.

The current 30-operation GPT limit is preserved by routing future providers through `resolveTurn`, `runRulesKernel`, `configureSimulation`, or `authorWorldContent` rather than multiplying endpoints.

## Safety and integrity

- expected-revision optimistic concurrency;
- campaign-scoped durable IDs;
- referential integrity;
- serialized SQLite writes;
- deterministic campaign RNG;
- per-command atomicity;
- idempotent replay;
- bounded context and intent count;
- dependency-cycle rejection;
- fail-closed backend connection behavior;
- no GPT-owned authoritative mutation.
## Narrative presentation authority (4.0.2)

Schema 14 adds a presentation-only narrative layer. It consumes completed authoritative results and compiles NRP-1.0 packets. It cannot resolve mechanics or modify truth. Persistent beats/motifs advance only after a hard-passing accepted output is recorded. Default mode is `shadow`; see `NARRATIVE_ARCHITECTURE_V402.md`.

