# World Engine v3.9.8 — Verification

- User-supplied full v3.9.5 baseline: 229/229 PASS before integration.
- Integrated permanent-endpoint focused tests: 8/8 PASS.
- Combined source suite: 237/237 PASS.
- Engine/API version: 3.9.8.
- Database schema: 12.
- GPT Actions: 30 operations, 30 unique operation IDs, 30/30 `x-openai-isConsequential:false`.
- OpenAPI object schemas missing explicit `properties`: 0.
- Unresolved local OpenAPI references: 0.
- GPT instructions: 7,621 characters / 7,688 UTF-8 bytes.

The final clean-ZIP test result is added to `WORLD_ENGINE_V398_FINAL_VERIFICATION.md` after packaging.

External acceptance: Tailscale/Cloudflare account authorization and live internet reachability cannot be physically exercised in the Linux build container. The installer fails closed unless the real stable public `/health` and protected Bearer-auth API probes pass on the Windows host.
