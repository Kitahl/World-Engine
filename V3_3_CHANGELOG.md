# World Engine v3.3.0 Changelog

## Fixed from supplied DECIDE/CASCADE prototypes

- Replaced deterministic argmax selection with seeded softmax top-K.
- Added per-need response curve storage (`linear`, `quadratic`, `urgent`, `threshold`).
- Added hard action requirements and action duration/commitment.
- Added graph-derived utility proximity.
- Added reaction selectors and repeat policies.
- Fixed `same_location` so the event target and dead NPCs do not react.
- Replaced coarse repeat suppression with reaction-level configurable count semantics.

## Spatial model

- Added persistent world graph (`x`,`y`,`location_links`).
- Added shortest-route and graph-derived LOD calculations.
- Added road-aware SPREAD.
- Added disposable combat positions/terrain.
- Added range, cover and line-of-sight handling to baseline attacks.
- Combat grid data is deleted on combat end.
- Battle image cues now incorporate tactical staging.

## Thin systems

- Item definitions and inventories.
- STOCK-derived scarcity prices.
- Thin lifecycle + optional mortality checks.
- Drama-manager weighting over CHANCE rules.

## Verification

- 71/71 full tests pass in the build environment.
- 62/62 engine/simulation/launcher tests pass in a fresh empty stdlib-only virtualenv.
- Full clean dependency installation could not be executed in this sandbox because package-network DNS access is disabled; this limitation is recorded rather than represented as a pass.
- GPT Action schema: 30 operations, 0 duplicates.
