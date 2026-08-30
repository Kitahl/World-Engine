# v3.9.2 Reasoning Policy

World Engine does **not** change ChatGPT's model picker or reasoning slider. It returns a deterministic advisory classification in `_turn_directives.reasoning`.

- **fast / Instant** — routine context, dialogue, simple checks, ordinary attacks and movement whose mechanics are already resolved by the backend.
- **standard / Medium** — new scene/location synthesis, battle staging, several meaningful options, moderate ambiguity.
- **deep / High** — world generation, custom setting synthesis, major quest/faction/political decisions, broad multi-system consequences.

The backend never asks ChatGPT to recompute authoritative simulation. More reasoning is used only for intent interpretation, ambiguity resolution, synthesis and narration.

On eligible GPT-5.6 plans, the user can leave **Instant** selected and enable **Settings → General → Higher intelligence** so ChatGPT can automatically use more reasoning for complex requests. The Action response is advisory; the platform controls actual reasoning escalation.
