# World Engine v3.4 Sources / Evidence Ledger

## Project design inputs

- `design_inputs/World_Engine_Layers_and_Codex_Guide.md`
- `design_inputs/World_Engine_Simulation_Plan.md`
- `design_inputs/World_Engine_Time_Design.md`
- supplied DECIDE/CASCADE/clock/simulation reference prototypes
- WE31 work-order and simulation triage documents supplied during development

No third-party simulation engine code was copied into the World Engine runtime from the public comparison projects below.

## Public architecture comparisons

| Project | URL | Relevance |
|---|---|---|
| Project Infinity | https://github.com/electronistu/Project_Infinity | Dedicated D&D 5e engine; external deterministic dice/state; deeper spell/combat rules |
| Mnehmos Engine | https://github.com/Mnehmos/mnehmos-engine | Persistent multi-region generative simulation; bounded LLM agents + deterministic validation |
| Bunnyland | https://github.com/thalismind/bunnyland-server | Persistent ECS, shared validated action surface, plugins/clients/MCP/observability |
| llm-fortress | https://github.com/kttalley/llm-fortress | Dense deterministic local simulation, pathfinding, animals, weather, world history |
| llm-rpg-world-simulator | https://github.com/subho004/llm-rpg-world-simulator | Python persistent RPG world, LLM planning behind deterministic validation |

## Simulation prior art / design references

| Topic | Source | What it supports |
|---|---|---|
| Utility AI | GDC Vault — Dave Mark & Kevin Dill, *Improving AI Decision Modeling Through Utility Theory* | utility response curves and weighted/stochastic selection patterns |
| Smart objects | IEEE Spectrum, *Mind Games* | Sims-style objects advertising need satisfaction/capabilities |
| Commitment | Photon Quantum Utility Theory docs | momentum/commitment/hysteresis concept |
| Social reasons | Comme il Faut / Prom Week; Versu implementation writing | social facts/history and explicit reasons beyond scalar relationships |
| Mortality | Gompertz-Makeham literature | thin age-dependent mortality model prior art |

## Launcher supply-chain pin

The one-click launcher pins the Windows amd64 `cloudflared` asset to release `2026.8.1` and verifies SHA-256 before use.

## Current policy

- authoritative state stays in local SQLite;
- off-screen decisions remain deterministic rather than LLM-driven;
- public projects are comparison/reference material unless their code is separately vetted for license/dependency compatibility;
- internal engineering scores in `COMPETITOR_COMPARISON.md` are not external benchmark results.

## v3.6 music sources

- pywebview GitHub: https://github.com/r0x0r/pywebview — native Python WebView host, JS/Python bridge, BSD-3-Clause.
- pywebview API docs: https://github.com/r0x0r/pywebview/blob/master/docs/api/README.md
- pywebview Windows installation: https://github.com/r0x0r/pywebview/blob/master/docs/guide/installation.md
- YouTube IFrame Player API: https://developers.google.com/youtube/iframe_api_reference
- YouTube embedded-player parameters: https://developers.google.com/youtube/player_parameters
- YouTube Required Minimum Functionality: https://developers.google.com/youtube/terms/required-minimum-functionality
- gajus/youtube-player (studied only): https://github.com/gajus/youtube-player
- feross/yt-player (studied only): https://github.com/feross/yt-player

## v3.7 rules-kernel research references

- Project build directive: `design_inputs/WORLD_ENGINE_V3_7_BUILD_DIRECTIVE.md`.
- Foundry VTT D&D5e repository: https://github.com/foundryvtt/dnd5e
- Foundry D&D5e Activities documentation: https://github.com/foundryvtt/dnd5e/wiki/Activities
- Foundry D&D5e Attack Activity documentation: https://github.com/foundryvtt/dnd5e/wiki/Activity-Type-Attack
- Foundry D&D5e Hooks documentation: https://github.com/foundryvtt/dnd5e/wiki/Hooks
- Project Infinity comparison/reference: https://github.com/electronistu/Project_Infinity

These sources informed architecture and comparison. No third-party runtime is required by the v3.7 kernel, and no official rules-content dataset is bundled.
