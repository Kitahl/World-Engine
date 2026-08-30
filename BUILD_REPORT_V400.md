# World Engine 4.0.0 Build Report

## Numerical result

| Gate | Result |
|---|---:|
| Engine version | **4.0.0** |
| Database schema | **13** |
| Turn protocol | **WETP-1.0** |
| Default capability manifests | **29** |
| Capability modes | **5** |
| Context tiers | **4** |
| Source regression tests | **280/280 PASS** |
| Exact clean-ZIP regression tests | **280/280 PASS** |
| OpenAPI operations | **30** |
| Unique operation IDs | **30** |
| `x-openai-isConsequential:false` | **30/30** |
| Missing object `properties` | **0** |
| Unresolved local `$ref` | **0** |
| Fresh SQLite integrity | **ok** |
| Foreign-key violations | **0** |
| Local HTTP `/health` | **200** |
| Local HTTP `ensureCampaign` | **200** |
| Local HTTP `resolveTurn(capabilities)` | **200 / 29 manifests** |
| Local HTTP `resolveTurn(execute)` | **completed** |
| Idempotent replay | **PASS** |
| GPT instructions | **7,861 UTF-8 bytes** |

## Main implementation

- unified WETP-1.0 `resolveTurn` endpoint;
- 29 typed capability manifests over existing deterministic providers;
- universal entity and relationship graph;
- canonical facts separated from entity beliefs;
- traceable information transfer and rumor provenance;
- deterministic HOT/WARM/COLD/ARCHIVE context compiler;
- expected-revision protection and idempotent turn replay;
- no-paste automatic ngrok authentication discovery;
- automatic persistent API-key retrieval/creation;
- automatic backend + stable HTTPS + schema + public-auth verification;
- one per-user no-admin supervisor with duplicate-process lock and self-repair.

## Security/authority boundary

The local startup controller does not log raw ngrok or World Engine secrets. It cannot log into an external account or edit private GPT Builder authentication settings. First use may require the account owner to sign in and press the official ngrok dashboard Copy button, followed by one GPT Builder schema import/Bearer paste. Normal later local startup is automatic.

## Evidence files

- `TEST_OUTPUT_V400_SOURCE.txt`
- `WORLD_ENGINE_V400_OPENAPI_AUDIT.json`
- `WORLD_ENGINE_V400_SQLITE_AUDIT.json`
- `WORLD_ENGINE_V400_HTTP_CHECK.txt`
- `TEST_OUTPUT_V400_CLEAN_PACKAGE.txt`
- `TEST_OUTPUT_V400_CLEAN_ZIP.txt`
- `WORLD_ENGINE_V400_CLEAN_ZIP_OPENAPI_AUDIT.json`
- `WORLD_ENGINE_V400_CLEAN_ZIP_SQLITE_AUDIT.json`
- `WORLD_ENGINE_V400_CLEAN_ZIP_HTTP_CHECK.txt`
