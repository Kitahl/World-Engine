# World Engine 4.2.0 build report

Build date: 2026-08-30  
Platform: Windows, Python 3.12

## Build outcome

The v4.2 candidate is a reconciled merge:

- v4.0.1 supplies the hardened context compiler and post-commit recompilation.
- v4.0.2 supplies the primary narrative runtime and fail-closed enforce behavior.
- v4.1 supplies the opt-in default and compatible narrative data through a one-time, non-destructive importer.
- schema 15 and `we42_schema_features` resolve the incompatible schema-14 collision between the three source releases.

No simple ZIP overlay was used.

## Executable verification

| Gate | Result |
|---|---|
| Full inherited + v4.2 pytest suite | 320 passed |
| High-risk compiler/narrative/merge suite | 34 passed in each of 3 consecutive runs |
| Previously failing inherited cases after contract fixes | 9 passed |
| Final v4.2 migration/secrecy/instructions checks | passed, including injected rollback and rehashed-packet override probes |
| Actual v4.0.1 database upgrade | schema 15; integrity OK; idempotent; no private marker |
| Actual v4.0.2 database upgrade | rows and enforce mode preserved; integrity OK; no private marker |
| Actual v4.1 database upgrade | source rows preserved; compatible rows imported; 2 imitation fields removed; integrity OK; no private marker |
| Actual mixed v4.0.1 + v4.1 database upgrade | schema 15; both feature families active; integrity OK; no private marker |
| Actual legacy v4.0.2 NRP-1.0 packet | verified successfully under v4.2 |
| OpenAPI export | 30 operations; 30 unique IDs; all non-consequential; compatible object schemas |
| HTTP/SQLite/source release audit | passed |
| Narrative runtime audit | passed; NRP-1.1/NQR-1.1; schema 15; packet hash present |
| Python bytecode compilation | passed |
| Ruff fatal/error-class checks | passed |
| Merge-specific mypy check | passed |
| Vulture at 90% confidence | no findings |
| Dependency vulnerability audit | no known vulnerabilities in tested environment |
| Bandit | no high findings; 33 medium inherited warnings |
| Graphify | 1,669 nodes; 4,670 edges; no malformed, duplicate, dangling, self-loop, or collapsed edges |

The clean-ZIP verification result and final archive hash are recorded in `WORLD_ENGINE_V420_FINAL_VERIFICATION.md` and `WORLD_ENGINE_V420_RELEASE_MANIFEST.json`.

## Residual debt

The broad inherited source tree is not fully mypy-clean: it reports 46 errors across eight imported modules. The broad all-rule Ruff pass also reports extensive legacy formatting/style debt. Bandit reports 33 medium-confidence dynamic-SQL warnings; inspection found that the reported identifiers are selected from internal table/column maps or bounded query fragments, while values remain parameterized. These are not represented as zero-risk or as fully remediated.

The FastAPI test client emits one deprecation warning about the current `httpx` adapter. Runtime tests pass, but this should be revisited when the next FastAPI/Starlette test-client migration is adopted.

## Release contents

The release package includes the runtime, Windows startup scripts, GPT instructions, OpenAPI schema, migration/changelog material, source tests, and generated v4.2 audits. It excludes caches, bytecode, runtime databases, logs, local secrets/configuration, and temporary graph/audit workspaces.
