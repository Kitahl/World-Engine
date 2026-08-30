# World Engine v3.9.1

## GPT Actions / OpenAPI compatibility hotfix

- Normalizes every OpenAPI/JSON-Schema node with `type: object` so it explicitly contains `properties: {}` when FastAPI/Pydantic omitted it.
- Applies the normalization to the app-level `/openapi.json`, static `scripts/export_openapi.py`, and launcher-generated `openapi_actions_live.json`.
- Preserves `additionalProperties` semantics and does not invent response fields.
- Adds recursive regression tests, including nested `anyOf` and array item object schemas.
- GPT-visible operation count remains exactly 30.
