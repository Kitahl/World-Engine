# World Engine 4.0.0 Final Verification

| Gate | Result |
|---|---:|
| Engine | **4.0.0** |
| Database schema | **13** |
| Turn protocol | **WETP-1.0** |
| Default capability manifests | **29** |
| Capability modes | **5** |
| Context tiers | **4** |
| Source regression tests | **280/280 PASS** |
| Exact clean-ZIP regression tests | **280/280 PASS** |
| GPT-visible Actions | **30** |
| Unique operation IDs | **30** |
| `x-openai-isConsequential:false` | **30/30** |
| Missing object `properties` | **0** |
| Unresolved local `$ref` | **0** |
| `resolveTurn` | **present at `/api/turn`** |
| Fresh SQLite integrity | **ok** |
| Foreign-key violations | **0** |
| WETP router tables | **8** |
| HTTP `/health` | **200** |
| HTTP `ensureCampaign` | **200** |
| HTTP capability discovery | **200 / 29** |
| HTTP context-only compilation | **200 / digest present** |
| HTTP execute | **completed** |
| HTTP idempotent replay | **PASS** |
| Engine receipt/directives | **present** |
| GPT instructions | **7,861 UTF-8 bytes** |
| Administrator required | **NO** |
| Console token paste field | **NO** |

## Startup boundary

The local startup path is automatic after one-time account authorization: persistent key retrieval, backend start, stable endpoint repair/start, public health, protected authentication, schema generation, supervisor launch, and final receipt. If no ngrok credential exists, the official account page opens and the account owner presses Copy; World Engine captures and configures that clipboard value. Private GPT Builder schema/Bearer configuration remains a one-time user-controlled security boundary.

## Physical external boundaries

The test environment cannot certify a real Windows desktop clipboard, ngrok account login, external assigned domain, native ChatGPT image invocation, or speaker output. Those require their actual external environments and are not inferred from mocks.
