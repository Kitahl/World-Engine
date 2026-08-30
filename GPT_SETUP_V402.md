# GPT Setup — World Engine 4.0.2

## Files

- Active GPT instructions: `CUSTOM_GPT_INSTRUCTIONS_V402.txt`
- Static development schema: `openapi_actions.json`
- Generated stable schema: `openapi_actions_PERMANENT.json`

## Setup

1. Extract the complete ZIP.
2. Run `START_WORLD_ENGINE.bat` without Administrator mode.
3. If no ngrok authentication exists, sign in on the official dashboard and click its Copy control; the startup controller captures and validates the token without a console paste field.
4. Wait for `WORLD ENGINE 4.0 CONNECTION READY` and a PASS receipt.
5. Import `openapi_actions_PERMANENT.json` into GPT Builder.
6. Configure Bearer authentication and paste the generated World Engine API key once.
7. Paste `CUSTOM_GPT_INSTRUCTIONS_V402.txt` into GPT Instructions.
8. Test `ensureCampaign`, then `resolveTurn` with `mode=context_only`.

## Narrative rollout

The default is `shadow`. This preserves current prose while compiling private NRP-1.0 packets.

1. `shadow`: collect packets/receipts without changing player-facing prose.
2. `compare`: render blinded candidates while baseline remains player-facing.
3. Run `NARRATIVE_BENCHMARK_V402.md`.
4. Set `enforce` only after the promotion gate passes.
5. In enforce mode, quality-check and record only accepted output; compilation alone must not consume storylets/motifs.

Configure through `resolveTurn` with intent type `narrative`, operation `configure`, and payload `{"mode":"shadow|compare|enforce|off"}`.

## Recovery boundary

On connection failure, stop authoritative play. Run `START_WORLD_ENGINE.bat`; resume only after public health, protected authentication, `ensureCampaign` and `resolveTurn(context_only)` succeed.
