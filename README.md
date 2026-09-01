# World Engine 5.1.1 — Automatic Tunnel + Offline Music

World Engine is a persistent, deterministic tabletop-RPG backend with a standalone Windows Companion and an optional five-operation ChatGPT GPT Actions bridge. The engine owns canon, rules, random outcomes, player knowledge, progression, environment, economy, population, politics, actor agency, incidents, executable quests, and consequences. ChatGPT interprets intent and renders only authorized results.

Version 5.1.1 preserves SQLite schema 24, the five-Action boundary, `WEGEN-2.0`, and the `WE-DESKTOP-5.1.0` projection. It removes the first-run ngrok copy-token dependency: a no-account Cloudflare Quick tunnel is created automatically when an external GPT URL is needed. It also ships locally generated, offline background music rather than depending on YouTube or another streaming service.

## Release contract

| Component | Contract |
| --- | --- |
| Release | **5.1.1** |
| SQLite schema | **24** |
| Procedural generator | **WEGEN-2.0**; staged WEGEN-1.0/1.1/1.2 remain validatable |
| PBEM boundary | **PBEM-2.2**, enforced on public turns |
| Narrative packet/receipt | **NRP-1.2 / NQR-1.2** |
| Mechanism contract | **MOP-1.0**, transaction-aware trusted execution |
| Desktop projection | **WE-DESKTOP-5.1.0** |
| Environment projection | **WE-ENV-PUBLIC-1.0** |
| Capability manifests | **33** |
| GPT Actions | **5** |
| Normal gameplay gateway | **resolveTurn** |

The public GPT Actions are `resolveTurn`, `publishPresentation`, `saveVisualProfile`, `buildImageCue`, and `recordImageGeneration`. Direct setup, mechanics, simulation, authoring, and administrator routes remain outside the GPT schema and require a separate operator key.

## Start on Windows

Double-click:

```text
START_WORLD_ENGINE.vbs
```

`START_WORLD_ENGINE.vbs` is the normal hidden-helper launcher. `START_WORLD_ENGINE.bat` is retained as a visible diagnostic/fallback launcher.

Startup creates or reuses the private `.venv`, starts the loopback API, opens **one** standalone Companion window, and then prepares the optional HTTPS connection. The backend, tunnel, and supervisor run as hidden helpers; normal play does not open a separate launcher, music, or console window. `launcher.py` remains a diagnostic/manual compatibility tool if it is needed. A local game and Companion do **not** need a tunnel and remain usable if external connectivity is unavailable.

Use `CUSTOM_GPT_INSTRUCTIONS_V510.txt` and the generated GPT Actions schema in the GPT Builder. The active instruction contract is intentionally still V510 because the five public operations and their protocol did not change.

## Automatic external connection

A tunnel only matters if you want ChatGPT GPT Actions to reach the game running on your PC. It is never required for local play.

On first use, World Engine automatically creates an account-free **Cloudflare Quick Tunnel**. It uses a World-Engine-owned isolated configuration area, does not read or alter a personal Cloudflare configuration, and owns the process it starts so it can stop or replace only that process. No ngrok token needs to be copied, pasted, or stored for this default path.

A Quick Tunnel URL is random and temporary. If the engine restarts or creates a different URL, re-import the generated GPT Actions schema into the GPT Builder. The Companion shows that the endpoint is temporary and keeps the re-import warning visible until you acknowledge it.

Ngrok, a named Cloudflare Tunnel, and Tailscale remain optional stable routes. They require their own account or device setup, and World Engine reuses an already configured provider rather than silently changing it. Ngrok cannot safely obtain an account authtoken automatically: that credential is issued to the user account. This release therefore uses the no-account Quick Tunnel for automatic first-run access rather than asking you to copy a key.

## Standalone Companion UI and music

The Companion is a Python/`pywebview` desktop application over loopback, not a hosted web companion. It provides Story, Dialogue, Explore, Combat, Character, World Map, Investigation, Forge, and connection diagnostics while the engine remains authoritative.

Background music is generated locally with Web Audio: no YouTube embed, media URL, account, advertisement, or network request is required. It begins only when you press **Play**, because Windows/WebView browsers prohibit audible autoplay without an explicit user gesture. Play, pause, and volume controls are provided; saved older music catalogs fall back to the built-in procedural soundtrack if they point to unavailable streaming media.

The desktop receives safe local projections only. Browser-visible HTML and JavaScript never receive the GPT bearer key or operator key. Complete backups use SQLite's online backup path; the legacy JSON snapshot is a core-domain diagnostic and not a complete mechanism/economy/population backup.

Launch the desktop independently with:

```text
START_COMPANION_UI.bat
```

## Procedural world generation

WEGEN-2.0 deterministically creates a connected campaign runtime from seed and namespace: settlements, routes, neighboring biomes and climates, factions, NPCs, a starting character, resources, quests, market/logistics state, population cohorts, MOP-backed quest DAGs, actor goals, territorial control, incidents, and a bootstrap World Bible.

Generated content is never written directly to canon. The operator flow is:

```text
generate → stage → validate → dry-run (maximum one simulated year) → promote atomically
```

Expansion batches are additive, revision-bound, namespace-isolated, and connected to an existing anchor. This is deterministic, connected, stateful campaign generation—not centimeter-scale terrain synthesis or a physical planet simulator.

## Runtime safeguards

- Public `resolveTurn` requests enforce PBEM actor identity, server-derived checks and outcomes, locality/ownership gates, bounded time advancement, and idempotency.
- Public projections require both `PUBLIC` sensitivity and `WORLD` scope. Private, secret, entity, and GM state stays outside GPT and desktop world-public views.
- Environment transitions, consequences, economy, population, incidents, politics, agency, and quests run through canonical deterministic writers and receipts.
- In narrative `enforce` mode, the model receives only a redacted narrative packet; private validation context is never returned.

## Important files

- `CUSTOM_GPT_INSTRUCTIONS_V510.txt` — active five-Action GPT behavior contract
- `openapi_actions.json` — five-operation portable schema
- `world_engine_companion.py` and `companion_ui/` — standalone desktop
- `world_engine/procedural.py` — deterministic scaffold generator
- `world_engine_permanent_endpoint.py` — optional stable providers and automatic temporary endpoint lifecycle
- `music_player.py` and `world_engine/music.py` — offline procedural music player and catalog fallback
- `scripts/release_verify_v511.py` — active release verifier
- `scripts/package_v511.py` — full source and clean-extracted package verifier
- `V5_1_1_CHANGELOG.md` — release changes
- `BUGFIX_REPORT_V511.md` — defect, safety, and verification report
- `WORLD_ENGINE_V511_HANDOFF.json` — generated package hashes and verification evidence

## Verification boundaries

Automated gates cover source and clean-extracted packages, schema/integrity, the Action surface, deterministic generation and simulation, PBEM, environment, startup logic, API policy, desktop projections, Quick Tunnel ownership/config isolation, and offline-music controls.

Automated Web Audio checks can verify that the audio graph starts and pauses after the required user gesture. They cannot prove that a particular speaker, driver, mixer, or mute switch is physically audible. Similarly, automated tests cannot claim live named-provider account connectivity, Windows Service Control Manager behavior, Foundry relay delivery, or external GPT Builder configuration; those remain explicitly unverified machine/account boundaries.
