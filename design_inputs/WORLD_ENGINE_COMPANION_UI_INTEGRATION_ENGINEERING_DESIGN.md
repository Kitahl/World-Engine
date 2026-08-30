---
doc_id: WE-COMPANION-001
title: World Engine Companion UI Integration — Software Engineering Design
status: DESIGN_READY
target: World Engine 4.x / proposed 4.1 companion integration
date: 2026-08-30
domain: fictional tabletop-RPG simulation software
architecture: ChatGPT Custom GPT Actions + World Engine authoritative backend + realtime companion UI
primary_goal: reuse prior art; do not hand-build solved infrastructure
evidence_policy:
  VERIFIED: primary/official repository or current project documentation checked
  INTERNAL: existing World Engine project document
  PROPOSED: engineering design decision for World Engine
  UNVERIFIED: must be checked before code reuse
---

# 0. TL;DR

## Decision

Build a **World Engine Companion** rather than scraping or embedding the consumer ChatGPT webpage.

The companion is a separate browser/PWA-style client that reads authoritative World Engine state and receives ChatGPT-rendered presentation through an explicit World Engine Action.

The intended architecture is:

```text
PLAYER
  │
  ▼
CHATGPT CUSTOM GPT
  │
  ├── resolveTurn / existing Actions
  │          │
  │          ▼
  │      WORLD ENGINE
  │      authoritative DB
  │
  └── publishPresentation
             │
             ▼
        Presentation Store
             │
      Socket.IO / WebSocket
             │
             ▼
     WORLD ENGINE COMPANION
```

The companion provides:

- central narration pane;
- global map;
- local minimap;
- character sheet;
- equipment/inventory;
- party;
- quests;
- NPC directory;
- factions;
- relationship graph;
- journal/timeline;
- combat panel;
- conditions/resources;
- weather/time;
- music/presentation state;
- system/debug panel.

## What is reused

| Component | Source | Licence | Decision |
|---|---|---:|---|
| Custom GPT → backend → separate realtime dashboard architecture | HAIP | Apache-2.0 | **ADOPT architecture and reusable infrastructure patterns** |
| External VTT → relay → REST/WebSocket application bridge | Foundry VTT REST API | MIT | **OPTIONAL ADOPT** |
| Server-state querying/caching | TanStack Query | MIT | **ADOPT** |
| Realtime transport client/server | Socket.IO + python-socketio | MIT | **ADOPT** |
| Interactive world-map renderer | MapLibre GL JS | BSD-3-Clause | **ADOPT** |
| RPG frontend/store/layout reference | Marinara Engine | AGPL-3.0 | **REFERENCE ONLY unless AGPL deployment is explicitly accepted** |
| RPG HUD/minimap UX | ST RPG HUD | licence not established in this research pass | **REFERENCE ONLY until licence verified** |
| D&D-style map UX | Silly Map | AGPL-3.0 | **REFERENCE ONLY** |
| Older RPG Companion widgets | RPG Companion for SillyTavern | AGPL-3.0 | **REFERENCE ONLY** |

## What remains custom

Only the World Engine-specific adapter and contracts:

1. `presentation.publish`
2. presentation persistence
3. realtime event projection
4. World Engine → UI view-model normalization
5. fog-of-war / player-knowledge filtering
6. panel composition for World Engine entity types
7. revision/hash synchronization
8. setting-specific presentation themes

Do **not** build a new chat model runtime.

Do **not** scrape ChatGPT DOM/output.

Do **not** let the UI become a second authority.

---

# 1. SOURCE BASIS

## 1.1 Internal World Engine evidence

[INTERNAL] The 1.63 comparison/gap analysis explicitly identifies missing user-facing state inspection surfaces including a state inspector, memory activation inspector, world timeline UI, faction graph UI, relationship graph UI, economy dashboard, population dashboard, debugger, heatmaps, and political/economic/population/ecology map layers. Internal source: `turn0file0`, lines 267–284.

[INTERNAL] The same analysis concludes that the major architectural move is not more prompt modules but foundational engines underneath them: entity/relationship graph, geography/pathfinding, population/lifecycle, economy/logistics, politics/law/warfare, ecology/environment, agent planning/knowledge, and authoritative state/event/memory. Internal source: `turn0file0`, lines 288–307.

[INTERNAL] The modern World Engine rules directive requires strict separation of player intent, deterministic RESOLVED mechanics, SIMULATED persistent consequences, and GPT NARRATION. Narration must not silently mutate authoritative state. Internal source: `turn0file1`, lines 21–50.

[INTERNAL] The 1.63 replacement analysis recommends real backends rather than prompt-only simulations and specifically identifies Foundry scenes for real grids/tokens/fog/distances while describing Custom GPT Actions or MCP as the glue that exposes external systems to ChatGPT. Internal source: `turn0file8`, lines 150–171.

[INTERNAL] The current World Engine simulation design also explicitly separates SIMULATED, RESOLVED and NARRATED systems. ASCII/visual output belongs in NARRATED presentation, while simulation state remains backend-owned. Internal source: `turn0file5`, lines 10–24 and 97–109.

## 1.2 External prior art

### HAIP — exact transport architecture precedent

[VERIFIED] Repository: `https://github.com/TelivityAI/haip`

HAIP has:

- a React dashboard;
- a ChatGPT Gateway specifically for GPT Actions;
- a REST/OpenAPI backend;
- a WebSocket gateway;
- realtime Socket.IO broadcasting;
- event names using `entity.action`;
- Apache-2.0 licensing.

Its architecture is the closest verified precedent for:

```text
ChatGPT Custom GPT
→ GPT Actions
→ authoritative application backend
→ realtime events
→ separate React dashboard
```

World Engine should reuse this architecture rather than inventing a bespoke synchronization protocol.

### Foundry VTT REST API — optional mature VTT lane

[VERIFIED] Repository: `https://github.com/ThreeHats/foundryvtt-rest-api`

Foundry REST API provides:

```text
Foundry VTT
↔ WebSocket relay
↔ REST API
↔ external application
```

The project explicitly lists custom dashboards and companion apps as use cases, has automatic reconnect behavior, and is MIT licensed.

Use this if World Engine wants Foundry to render tactical scenes, tokens, fog, lighting, sheets or maps.

It must remain optional because World Engine's authoritative state and campaign operation cannot require a proprietary VTT.

### Marinara Engine — strongest RPG frontend reference

[VERIFIED] Repository: `https://github.com/Pasta-Devs/Marinara-Engine`

Its frontend documentation describes dedicated RPG state stores containing:

- current date/time;
- location;
- weather;
- present characters;
- events;
- player statistics;
- quests;
- inventory.

Its encounter state includes:

- party;
- enemies;
- HP;
- attacks;
- statuses;
- environment;
- player actions;
- combat log;
- combat result.

Its Game Mode tracks maps, party, NPCs, items, quests, time and weather across sessions.

Its frontend stack currently uses React, TypeScript, Vite, Zustand, TanStack Query, Framer Motion, Lucide and DOMPurify.

**Licence: AGPL-3.0.**

World Engine may inspect the architecture and user experience.

Do not copy or vendor AGPL code into a deployment that is unwilling to satisfy AGPL obligations.

### ST RPG HUD — exact side-panel UX precedent

[VERIFIED FEATURES; LICENCE UNVERIFIED IN THIS PASS]

Repository: `https://github.com/ets1odoo-beep/st-rpg-hud`

The project already implements the interface concept requested for World Engine:

- tabbed RPG HUD;
- stats;
- skills;
- inventory;
- party;
- quests;
- NPCs;
- relationship matrices;
- map;
- log;
- dynamic minimap;
- fog of war;
- ally/hostile dots;
- clickable actions/macros.

Its weakness for World Engine is architectural: it relies on AI-generated hidden XML as state.

World Engine should reproduce the UX using authoritative backend state instead.

Do not vendor code until the repository licence is verified.

### Silly Map

[VERIFIED] Repository: `https://github.com/Jeka201216/Silly-Map`

Features include interactive D&D-style maps, persistent local map configuration, automatic location selection and character placement.

**Licence: AGPL-3.0.**

Reference its UX and map-management ideas only unless AGPL obligations are accepted.

### RPG Companion for SillyTavern

[VERIFIED] Repository: `https://github.com/SpicyMarinara/rpg-companion-sillytavern`

The project contains RPG widgets for statistics, inventory, time, weather, location, events, characters, relationships and thoughts.

**Licence: AGPL-3.0.**

Reference only.

---

# 2. ENGINEERING GOALS

## G-01 — Zero model API dependency for companion display

The companion must not call the OpenAI API.

The existing ChatGPT Custom GPT remains the model-facing interaction surface.

World Engine Actions carry state/results to and from the backend.

## G-02 — No ChatGPT scraping

The companion must not:

- inspect ChatGPT DOM;
- automate browser scraping;
- intercept private product traffic;
- read the ChatGPT page using browser automation;
- impersonate an official ChatGPT API.

ChatGPT sends presentation to World Engine voluntarily using a declared Action.

## G-03 — One authority

World Engine remains authoritative for:

- map;
- movement;
- character state;
- items;
- quests;
- combat;
- conditions;
- progression;
- NPC knowledge;
- factions;
- relationships;
- world time;
- simulation.

The companion is a projection.

ChatGPT narration is presentation.

## G-04 — Live synchronization

A committed World Engine mutation should appear in the companion without requiring a page refresh.

Target:

- local update delivery p95 < 250 ms;
- reconnect without state loss;
- client can recover by revision snapshot after missed events.

## G-05 — Setting-agnostic UI

One backend.

Presentation packs may make it look like:

- fantasy journal;
- cyberpunk deck;
- Pip-Boy-like terminal;
- hard-SF operations console;
- gothic manuscript;
- roguelike terminal.

No setting-specific game logic belongs in the client.

## G-06 — Player knowledge boundary

The UI must never display canonical information the player has not learned.

Every panel must consume a **player-visible projection**, not unrestricted backend tables.

## G-07 — Existing components first

Before writing a component, Codex must search the adopted/reference projects and permissive libraries.

Hand-build only when:

1. no compatible component exists;
2. licence prevents reuse;
3. integration cost exceeds reimplementation;
4. World Engine semantics materially differ.

---

# 3. NON-GOALS

The first release does not attempt to:

- replace the ChatGPT input box;
- send arbitrary companion text automatically into consumer ChatGPT;
- stream consumer ChatGPT tokens directly into the external UI;
- implement another LLM provider layer;
- replace World Engine with Foundry;
- build a dense Dwarf Fortress simulation;
- make the browser UI authoritative;
- copy Diablo visual assets;
- copy commercial game UI source/assets;
- vendor AGPL code accidentally.

A future standalone World Engine client with its own prompt box requires an officially supported model API/provider integration or a supported ChatGPT app surface.

---

# 4. TARGET ARCHITECTURE

```text
┌───────────────────────────────────────────────────────────┐
│                    CHATGPT CUSTOM GPT                     │
│                                                           │
│ player input → intent normalization → narration           │
└───────────────┬─────────────────────┬─────────────────────┘
                │                     │
                │ resolveTurn         │ publishPresentation
                ▼                     ▼
┌───────────────────────────────────────────────────────────┐
│                       WORLD ENGINE                        │
│                                                           │
│  WETP router                                              │
│  rules / sim / NPC / graph / knowledge / map             │
│                                                           │
│  authoritative SQLite                                    │
│        │                                                  │
│        ├── revision/event ledger                          │
│        ├── UI projection service                          │
│        └── presentation store                             │
│                     │                                     │
│             Socket.IO gateway                             │
└─────────────────────┼─────────────────────────────────────┘
                      │
                      ▼
┌───────────────────────────────────────────────────────────┐
│               WORLD ENGINE COMPANION                      │
│                                                           │
│ React/Vite                                                │
│ TanStack Query                                            │
│ Socket.IO client                                          │
│ MapLibre                                                  │
│                                                           │
│ narration │ maps │ character │ inventory │ quests        │
│ NPCs │ factions │ relationships │ journal │ combat       │
└───────────────────────────────────────────────────────────┘
```

---

# 5. REALTIME TRANSPORT

## 5.1 Decision

Use **Socket.IO** rather than writing a custom WebSocket protocol.

Recommended server:

- `python-socketio` mounted alongside FastAPI.

Recommended client:

- official `socket.io-client`.

[VERIFIED] `python-socketio` is actively maintained and MIT licensed.

[VERIFIED] the reference JavaScript Socket.IO implementation is MIT licensed.

## 5.2 Why

Required behavior already exists:

- WebSocket when available;
- fallback transport;
- reconnection;
- rooms;
- event names;
- acknowledgements;
- connection state.

HAIP already demonstrates this pattern for a GPT-connected application dashboard.

## 5.3 Rooms

Clients subscribe by campaign:

```text
campaign:{campaign_id}
```

Optional subrooms:

```text
campaign:{campaign_id}:gm
campaign:{campaign_id}:player:{character_id}
```

Do not create one room per entity unless profiling proves it necessary.

---

# 6. EVENT CONTRACT

Use a normalized envelope.

```json
{
  "event_id": "01J...",
  "event_type": "character.hp_changed",
  "campaign_id": "main",
  "revision": 1842,
  "world_time": "1492-04-18T21:43:00",
  "scope": {
    "entity_keys": ["character:avelin"],
    "location_id": "east_gate"
  },
  "payload": {
    "hp": 17,
    "max_hp": 24,
    "delta": -7
  },
  "visibility": "player",
  "created_at": "2026-08-30T12:40:00-07:00"
}
```

## 6.1 Event naming

Follow the HAIP-style `entity.action` convention.

Examples:

```text
campaign.updated
clock.advanced
weather.changed
character.updated
character.hp_changed
character.condition_changed
inventory.changed
quest.updated
npc.updated
relationship.changed
faction.updated
location.updated
map.changed
combat.started
combat.updated
combat.ended
presentation.published
music.changed
connection.recovered
```

## 6.2 Event rule

An event notification is not the authoritative payload.

It is a synchronization hint.

If revision continuity is broken:

```text
last_client_revision + 1 != event.revision
```

client performs:

```text
GET /api/ui/snapshot
```

and replaces its projection.

---

# 7. PRESENTATION MIRROR

## 7.1 Purpose

World Engine currently owns state but ChatGPT owns final prose.

To mirror that prose into the companion without scraping ChatGPT, add:

```text
POST /api/presentation
operationId: publishPresentation
```

This is a **NARRATED** capability.

It may write only presentation storage.

It must never mutate simulation state.

## 7.2 Schema

```sql
CREATE TABLE presentations (
    campaign_id TEXT NOT NULL,
    presentation_id TEXT NOT NULL,
    turn_id TEXT,
    revision INTEGER NOT NULL,
    scene_id TEXT,
    narration_md TEXT NOT NULL,
    choices_json TEXT NOT NULL DEFAULT '[]',
    presentation_json TEXT NOT NULL DEFAULT '{}',
    narration_sha256 TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'published',
    created_at TEXT NOT NULL,
    PRIMARY KEY (campaign_id, presentation_id)
);

CREATE INDEX idx_presentations_revision
ON presentations(campaign_id, revision DESC);
```

## 7.3 Request

```json
{
  "campaign_id": "main",
  "presentation_id": "turn-1842",
  "turn_id": "turn-1842",
  "revision": 1842,
  "scene_id": "scene:east_gate",
  "narration_md": "Rain rattles against the shutters...",
  "choices": [
    {
      "choice_id": "question-mara",
      "label": "Question Mara",
      "intent_text": "I ask Mara who attacked the caravan."
    }
  ],
  "presentation": {
    "pack": "dark_fantasy",
    "panel": "investigation"
  }
}
```

## 7.4 Response

```json
{
  "status": "published",
  "presentation_id": "turn-1842",
  "revision": 1842,
  "narration_sha256": "sha256:...",
  "broadcast": true
}
```

## 7.5 Idempotency

`presentation_id` is idempotent.

Same ID + same hash:

```text
200 replay
```

Same ID + different hash:

```text
409 presentation conflict
```

## 7.6 30-operation constraint

If GPT Actions still enforce the current 30-operation ceiling, expose `publishPresentation` and hide one redundant low-level/admin operation from the GPT schema.

Do not remove backend/MCP functionality.

Candidate to hide:

- raw world-event injection;
- setup-only visual preference write;
- another low-level operation already covered by `resolveTurn`.

Make this decision from the current 4.x schema at implementation time.

---

# 8. UI PROJECTION API

Do not make the browser reconstruct game state by reading dozens of low-level endpoints.

Add UI-specific read models.

## 8.1 Snapshot

```text
GET /api/ui/snapshot
```

Query:

```text
campaign_id
player_id
```

Returns one player-visible projection.

```json
{
  "campaign": {},
  "revision": 1842,
  "clock": {},
  "weather": {},
  "player": {},
  "party": [],
  "location": {},
  "local_map": {},
  "world_map": {},
  "combat": null,
  "quests": [],
  "inventory": [],
  "known_npcs": [],
  "known_factions": [],
  "known_relationships": [],
  "journal": [],
  "presentation": {}
}
```

## 8.2 Panel endpoints

Optional routes for lazy loading:

```text
GET /api/ui/map/world
GET /api/ui/map/local
GET /api/ui/character
GET /api/ui/inventory
GET /api/ui/quests
GET /api/ui/npcs
GET /api/ui/factions
GET /api/ui/relationships
GET /api/ui/journal
GET /api/ui/combat
GET /api/ui/presentation/latest
```

These are frontend/backend routes.

They do not need to consume GPT Action slots.

---

# 9. PLAYER-VISIBLE PROJECTION

This is a hard security/canon boundary.

Never return raw:

```text
world_facts
npc_beliefs
director hidden state
secret quests
unrevealed locations
GM-only map nodes
hidden enemies
untriggered traps
```

to the normal player client.

Create:

```python
build_player_projection(
    campaign_id,
    player_entity_id,
    revision
)
```

Projection logic must use:

- discovered locations;
- player-known facts;
- visibility/fog state;
- known NPCs;
- known factions;
- observed combatants;
- current inventory;
- current quests;
- player-visible event ledger.

Add regression tests for hidden-information leakage.

---

# 10. FRONTEND TECHNOLOGY

## 10.1 Recommended stack

```text
React
TypeScript
Vite
TanStack Query
Zustand
Socket.IO client
MapLibre GL JS
Lucide icons
DOMPurify
```

### Licence status checked

- TanStack Query: MIT.
- Socket.IO JS: MIT.
- python-socketio: MIT.
- MapLibre GL JS: BSD-3-Clause.

Use these directly rather than writing equivalents.

## 10.2 State ownership

Use TanStack Query for server state.

Use Zustand only for local UI state.

### Server state

```text
campaign snapshot
maps
quests
inventory
NPCs
factions
combat
journal
presentation
```

### UI state

```text
active tab
panel sizes
collapsed panels
theme
zoom
selected entity
debug visibility
mobile drawer state
```

Do not duplicate authoritative state into persistent Zustand/localStorage.

---

# 11. FRONTEND LAYOUT

Desktop baseline:

```text
┌─────────────────────────────────────────────────────────────────────┐
│ WORLD ENGINE     DAY 38 · 21:43 · RAIN          ⚙  ◉ CONNECTED    │
├───────────────┬─────────────────────────────────┬───────────────────┤
│ GLOBAL MAP    │                                 │ LOCAL MAP         │
│               │        NARRATIVE                │                   │
│               │                                 │                   │
│               │                                 │                   │
├───────────────┤                                 ├───────────────────┤
│ CHARACTER     │                                 │ QUESTS            │
│ HP / AC / XP  │                                 │                   │
│ CONDITIONS    │                                 │                   │
├───────────────┴─────────────────────────────────┴───────────────────┤
│ Inventory │ Party │ NPCs │ Factions │ Relations │ Journal │ System │
└─────────────────────────────────────────────────────────────────────┘
```

## 11.1 Central pane

Primary:

- narration;
- choices;
- presentation images;
- location transitions;
- combat narration.

The companion does not need to reproduce ChatGPT's input box in v1.

## 11.2 Choice buttons

Choice clicks cannot silently inject a prompt into consumer ChatGPT.

Supported v1 behavior:

```text
click
→ copy intent_text to clipboard
→ visual confirmation
→ user pastes/sends in ChatGPT
```

Optional:

```text
Open ChatGPT
```

only if a stable official navigation mechanism is available.

Do not use DOM automation.

---

# 12. GLOBAL MAP

## 12.1 Decision

Use **MapLibre GL JS** rather than building pan/zoom/layer rendering manually.

## 12.2 World Engine data

World Engine already has:

- location IDs;
- x/y/z spatial state;
- location links;
- region/realm associations;
- routes;
- player discoveries;
- faction/director scope.

Expose them as GeoJSON or equivalent frontend projection.

## 12.3 Layers

```text
base geography
known locations
roads/routes
regions
political ownership
faction influence
known quests
known hazards
trade
population
weather
ecology
```

Only show layers backed by actual state.

## 12.4 Fog

World map visibility states:

```text
unknown
rumored
discovered
visited
current
```

---

# 13. LOCAL MINIMAP

Do not reuse the global-map engine for tactical cells if that creates complexity.

Render SCENE/local spatial state separately.

Data:

```text
scene bounds
player position
known NPC positions
known enemies
doors
obstacles
terrain
cover
interactive objects
known exits
```

UI style may be:

- HTML Canvas;
- SVG;
- lightweight grid renderer.

Before custom implementation, inspect permissively licensed existing tactical-grid components.

ST RPG HUD and Silly Map are useful UX references but not automatic code-reuse sources.

---

# 14. COMBAT MODE

When:

```text
combat.active = true
```

the client switches emphasis.

```text
┌─ COMBAT ──────────────────────────────────────────────┐
│ Initiative │ Round │ Conditions │ Concentration      │
├───────────────────────────────────────────────────────┤
│                    TACTICAL MAP                       │
├──────────────────────┬────────────────────────────────┤
│ PARTY                │ TARGET                         │
│ HP / resources       │ HP / visible conditions       │
├──────────────────────┴────────────────────────────────┤
│ Combat log                                             │
└───────────────────────────────────────────────────────┘
```

The combat UI reads deterministic World Engine state.

It must not calculate attacks/damage independently.

---

# 15. CHARACTER / DIABLO-LIKE PANEL

Use the information hierarchy, not copyrighted art/layout assets.

Panel groups:

```text
portrait/reference
level / XP
HP
AC/defense
primary resources
conditions
equipment slots
inventory
currency
abilities
progression
```

Equipment layout may use original slot positioning.

Do not replicate protected Diablo art, sounds, icons or exact trade dress.

---

# 16. INVENTORY

Server projection:

```json
{
  "items": [
    {
      "instance_id": "iteminst:123",
      "definition_id": "healing_potion",
      "name": "Healing Potion",
      "quantity": 2,
      "equipped": false,
      "slot": null,
      "known": true
    }
  ]
}
```

UI supports:

- filters;
- equipment;
- search;
- tooltip;
- quantity;
- known mechanical effects.

Clicking an item in v1:

```text
copy "I use Healing Potion." to clipboard
```

Do not mutate authoritative inventory locally.

---

# 17. QUESTS

Tabs:

```text
Active
Completed
Failed
Rumors
```

Each quest shows:

- title;
- status;
- player-known objective;
- known location;
- known participants;
- last relevant update.

Hidden objectives remain backend-only.

---

# 18. NPC DIRECTORY

Show only player-known NPCs.

Fields:

```text
name
portrait/reference
last known location
relationship summary
faction
role
known status
last interaction
player-known notes
```

Never display internal:

- hidden goal;
- private belief;
- DECIDE score;
- secret faction assignment

unless explicitly revealed by game mechanics.

---

# 19. RELATIONSHIP GRAPH

Use a general graph library only after licence verification.

Required semantics:

```text
player
party
major NPCs
factions
families
organizations
```

Edges:

```text
ally
hostile
family
member_of
employed_by
owns
owes
vassal_of
relationship/social
```

Filters prevent unreadable hairballs.

Default graph is player-centered, not all-world.

---

# 20. FACTIONS

Faction panel:

```text
name
known leader
known region
player reputation
known allies
known enemies
known goals
recent player-visible events
```

Optional world-map overlay.

---

# 21. JOURNAL / TIMELINE

This should become one of World Engine's strongest interfaces.

World Engine already values its event ledger.

UI views:

```text
chronological timeline
quest history
relationship changes
combat history
discoveries
faction history
location history
```

Filters operate over player-visible event records.

Every journal item links to entities and locations.

---

# 22. PRESENTATION / OUTPUT DIRECTOR INTEGRATION

The companion should understand output directives.

```json
{
  "presentation": {
    "pack": "cyberpunk_terminal",
    "importance": 4,
    "panel": "location_reveal",
    "icon_pack": "cyberpunk",
    "image_ref": null,
    "music": {}
  }
}
```

Client themes map semantic roles to appearance.

Game-state data remains unchanged.

---

# 23. SETTING PRESENTATION PACKS

Ship original packs:

```text
fantasy_journal
dark_fantasy
cyberpunk_terminal
hard_scifi
gothic
cozy
western
minimal
```

Pack controls:

- colors;
- typography;
- border grammar;
- icon set;
- map palette;
- panel density;
- animations.

It does not control rules.

---

# 24. OPTIONAL FOUNDRY MODE

## Purpose

For users who already own/use Foundry and want:

- tactical battlemaps;
- tokens;
- fog of war;
- dynamic lighting;
- Foundry sheets;
- combat tracker.

## Architecture

```text
World Engine
   ↕
adapter
   ↕
Foundry REST API relay
   ↕
Foundry VTT
```

World Engine remains authoritative.

The adapter translates state.

## Sync policy

Prefer one-way World Engine → Foundry for initial implementation.

If two-way writes are enabled, every Foundry mutation must become a validated World Engine command before becoming canonical.

Never allow both systems to independently own HP or inventory.

---

# 25. DATABASE CHANGES

Suggested migration:

```sql
CREATE TABLE presentations (...);

CREATE TABLE ui_client_sessions (
    campaign_id TEXT NOT NULL,
    client_id TEXT NOT NULL,
    player_entity_id TEXT,
    last_revision INTEGER NOT NULL DEFAULT 0,
    connected_at TEXT,
    last_seen_at TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY(campaign_id, client_id)
);

CREATE TABLE ui_preferences (
    campaign_id TEXT NOT NULL,
    player_entity_id TEXT NOT NULL,
    theme TEXT NOT NULL DEFAULT 'fantasy_journal',
    layout_json TEXT NOT NULL DEFAULT '{}',
    accessibility_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id, player_entity_id)
);
```

Do not store transient socket connections in SQLite unless needed for diagnostics.

---

# 26. BACKEND FILE PLAN

Proposed structure:

```text
world_engine/
  ui/
    __init__.py
    projection.py
    visibility.py
    presentation.py
    events.py
    socket_gateway.py
    schemas.py

app.py
  /api/presentation
  /api/ui/snapshot
  /api/ui/*
  Socket.IO ASGI mount
```

Optional:

```text
world_engine/integrations/foundry/
  client.py
  projection.py
  sync.py
```

---

# 27. FRONTEND FILE PLAN

Recommended:

```text
companion/
  package.json
  vite.config.ts
  src/
    app/
      App.tsx
      router.tsx

    api/
      client.ts
      queries.ts
      socket.ts
      contracts.ts

    stores/
      ui.store.ts

    panels/
      NarrativePanel.tsx
      CharacterPanel.tsx
      InventoryPanel.tsx
      QuestPanel.tsx
      PartyPanel.tsx
      NpcPanel.tsx
      FactionPanel.tsx
      RelationshipPanel.tsx
      JournalPanel.tsx
      CombatPanel.tsx
      SystemPanel.tsx

    maps/
      WorldMap.tsx
      LocalMap.tsx
      layers.ts
      geojson.ts

    presentation/
      PresentationRenderer.tsx
      packs/
      icons/

    layout/
      DesktopLayout.tsx
      TabletLayout.tsx
      MobileLayout.tsx
```

---

# 28. API AUTHENTICATION

The companion should not expose the same secret used by GPT Actions directly in browser source.

Recommended local model:

1. World Engine launcher opens companion locally.
2. Companion requests a short-lived client token.
3. Token is scoped to:
   - one campaign;
   - one player entity;
   - read/UI operations;
   - presentation socket subscription.

Suggested:

```text
POST /api/ui/session
```

The launcher may bootstrap this session.

Do not persist GPT Bearer tokens in browser localStorage.

---

# 29. REVISION SYNCHRONIZATION

Every snapshot/event includes:

```text
campaign_id
revision
```

Client maintains:

```text
last_revision
```

Rules:

```text
event.revision == last_revision + 1
→ apply event

event.revision <= last_revision
→ duplicate; ignore

event.revision > last_revision + 1
→ gap; refetch snapshot
```

Presentation may reference the same authoritative revision.

If ChatGPT publishes narration for an old revision:

```text
409 STALE_PRESENTATION
```

unless explicitly marked historical.

---

# 30. FAILURE RECOVERY

## Backend disconnect

UI shows:

```text
DISCONNECTED
last confirmed revision: N
```

No fake state.

Socket reconnect triggers snapshot.

## GPT presentation missing

State panels continue.

Narrative panel shows:

```text
Presentation pending
```

Never block authoritative state.

## Presentation conflict

Show existing canonical presentation record and log conflict.

Do not overwrite silently.

## Map payload invalid

Map panel fails independently.

Other panels continue.

---

# 31. PERFORMANCE BUDGETS

Initial targets:

| Metric | Target |
|---|---:|
| Initial local snapshot | < 1 MB typical |
| Initial render | < 2 s local desktop |
| Socket event UI update | p95 < 250 ms |
| Snapshot recovery after reconnect | < 2 s |
| World map locations rendered | 10,000 without DOM-per-node |
| Local SCENE actors | expected <= 100; optimize for <= 30 |
| Journal virtualization | required above 500 rows |
| Main JS bundle | target < 1 MB before maps |
| Map loaded lazily | yes |

Use virtualization and map GPU layers rather than thousands of DOM markers.

---

# 32. IMPLEMENTATION PHASES

## Phase 0 — Licence / source freeze

Before code:

1. record exact commit hashes of HAIP and Foundry REST API components considered for reuse;
2. copy their licence files into `THIRD_PARTY_NOTICES/`;
3. verify ST RPG HUD licence before copying anything;
4. explicitly flag Marinara/Silly Map/RPG Companion as AGPL reference-only.

Acceptance:

```text
0 unidentified copied source files
```

---

## Phase 1 — Presentation proof of concept

Build only:

```text
POST /api/presentation
GET /api/ui/presentation/latest
one Socket.IO event
one minimal React page
```

Test token:

```text
WORLD-ENGINE-BRIDGE-73921
```

Success requires:

```text
ChatGPT Action payload
=
stored narration hash
=
companion narration hash
```

Do not proceed until proven through the actual Custom GPT.

---

## Phase 2 — HAIP-style realtime infrastructure

Adopt:

- event naming;
- room subscription;
- reconnect;
- acknowledgement;
- dashboard query/realtime pattern.

Add:

```text
GET /api/ui/snapshot
Socket.IO campaign room
revision recovery
```

Acceptance:

- disconnect/reconnect 100 times;
- no missing final state;
- no duplicate persistent mutations.

---

## Phase 3 — frontend shell

Build the shell using existing permissive libraries.

Implement:

- desktop grid;
- tabs;
- resizable panels;
- theme;
- TanStack Query;
- Socket.IO;
- error boundary;
- loading state.

No game panels yet.

---

## Phase 4 — core panels

Order:

1. character
2. inventory
3. quests
4. journal
5. NPCs
6. factions
7. relationships
8. party

Each panel gets:

- typed server contract;
- visibility test;
- empty state;
- loading state;
- disconnect state.

---

## Phase 5 — global map

Use MapLibre.

Build adapter:

```text
World Engine locations
→ GeoJSON
→ MapLibre sources/layers
```

Do not manually implement pan/zoom/tile rendering.

---

## Phase 6 — local minimap

First search for a permissively licensed reusable tactical-grid component.

If none passes licence/complexity gates, implement only the thin World Engine-specific renderer.

Features:

- grid;
- fog;
- player;
- known actors;
- obstacles;
- exits;
- hover.

---

## Phase 7 — combat UI

Project existing combat state.

No combat calculations in client.

Add:

- initiative;
- HP/resources;
- conditions;
- local grid;
- combat log.

---

## Phase 8 — presentation packs

Add original World Engine packs.

Do not copy commercial visual assets.

---

## Phase 9 — optional Foundry adapter

Only after the standalone companion works.

Build:

```text
World Engine projection
→ Foundry REST operations
```

Start one-way.

---

# 33. TEST PLAN

## 33.1 Backend

```text
test_presentation_publish
test_presentation_idempotency
test_presentation_revision_conflict
test_player_projection_hides_secret_fact
test_player_projection_hides_unknown_location
test_ui_snapshot_revision
test_socket_event_revision
test_socket_reconnect_gap
test_ui_token_scope
test_presentation_does_not_mutate_campaign_state
```

## 33.2 Frontend

```text
snapshot renders
event patches query cache
revision gap forces refetch
socket duplicate ignored
map unknown location hidden
secret NPC omitted
inventory updates
quest updates
combat mode switch
presentation markdown sanitized
choice copies exact intent
```

## 33.3 Cross-system

Scenario:

```text
resolveTurn:
  move
  relationship change
  quest progression

then publishPresentation
```

Expected UI:

- current location updated;
- minimap updated;
- relationship panel updated;
- quest updated;
- narration appears;
- all refer to same revision.

## 33.4 Load

Simulate:

- 10,000 locations;
- 2,000 known NPCs;
- 10,000 journal events;
- 500 quest/history events;
- 5 simultaneous UI clients.

No UI should require loading the complete database.

---

# 34. ACCEPTANCE GATES

Release is blocked unless:

```text
[ ] Custom GPT → publishPresentation verified
[ ] narration hash matches companion
[ ] no ChatGPT scraping
[ ] World Engine remains sole authority
[ ] 0 secret fact leaks in visibility test suite
[ ] realtime reconnect recovers exact revision
[ ] client never mutates HP/inventory/quest directly
[ ] global map uses MapLibre or equivalent existing engine
[ ] presentation markdown sanitized
[ ] no GPT bearer token in localStorage
[ ] all third-party code licences recorded
[ ] no AGPL code accidentally vendored
[ ] responsive desktop/tablet/mobile layouts pass
[ ] disconnected state is explicit
[ ] current World Engine full regression suite remains green
```

---

# 35. LICENSING POLICY

## Permissive code

May be adopted after recording attribution/licence:

- Apache-2.0;
- MIT;
- BSD-2/3;
- compatible CC for non-code assets/data where appropriate.

## AGPL

AGPL is not "illegal for servers."

It imposes network-source obligations for covered modified deployments.

World Engine policy for this update:

```text
Do not vendor AGPL frontend code
unless the deployment/release model explicitly elects
to comply with AGPL obligations.
```

Therefore:

- Marinara: reference only by default.
- Silly Map: reference only by default.
- RPG Companion: reference only by default.

## Unknown licence

No licence means:

```text
NO CODE REUSE
```

until verified.

---

# 36. NO-HAND-BUILD CHECKLIST

Before implementing each feature:

```text
Realtime transport
→ use Socket.IO

REST/query cache
→ use TanStack Query

global map
→ use MapLibre

GPT/backend/dashboard architecture
→ adapt HAIP pattern

Foundry bridge
→ use Foundry REST API

RPG state/store UX
→ study Marinara

tabbed HUD/minimap UX
→ study ST RPG HUD

D&D map UX
→ study Silly Map
```

Codex must document:

```text
SEARCHED
FOUND
LICENCE
ADOPT/ADAPT/REJECT
```

before creating a replacement.

---

# 37. RISKS

| Risk | Severity | Mitigation |
|---|---:|---|
| UI leaks canonical secret state | Critical | dedicated player projection + tests |
| Companion becomes second authority | Critical | all writes route through World Engine commands |
| Narration mirrored at wrong revision | High | revision guard + 409 |
| GPT output differs from mirrored presentation | Medium | hash the Action-submitted narration; test visible ChatGPT separately |
| Socket event missed | High | revision gap snapshot recovery |
| UI dependency licence contamination | High | source freeze + notices + AGPL gate |
| Global map becomes slow | Medium | MapLibre GPU layers + clustering |
| Local map gets overengineered | Medium | SCENE-only bounded renderer |
| Foundry sync conflicts | High | one-way initial sync |
| User expects companion prompt box to control consumer ChatGPT | High | explicit no-API boundary |
| ChatGPT Action schema operation limit | Medium | expose publishPresentation; hide redundant low-level operation |
| External endpoint offline | High | fail closed; UI shows last confirmed revision |

---

# 38. OPTIONAL FUTURE: FULL STANDALONE CLIENT

Not part of the no-API companion.

Future architecture:

```text
World Engine Companion
       │
       ├── user prompt
       ▼
official model API/provider
       │
       ▼
World Engine
```

At that point the separate ChatGPT consumer UI is unnecessary.

Do not implement browser automation as a substitute.

---

# 39. BUILD HANDOFF FOR CODEX

Codex should begin with:

1. read current `AGENTS.md` / repository instructions;
2. run full World Engine baseline suite;
3. inspect existing 30-operation OpenAPI schema;
4. inspect current SQLite schema/migrations;
5. create a branch for `WE-COMPANION-001`;
6. freeze prior-art commits/licences;
7. build only Phase 1;
8. test Phase 1 through the actual Custom GPT;
9. stop if GPT → presentation publishing does not work;
10. only then add the dashboard/realtime system.

Do not begin with visual styling.

Do not begin with maps.

Prove transport first.

---

# 40. PHASE-1 EXACT BUILD ORDER

## Backend

```text
1. migration: presentations table
2. Pydantic request/response types
3. presentation store functions
4. POST /api/presentation
5. GET /api/ui/presentation/latest
6. Socket.IO server
7. emit presentation.published
8. tests
```

## GPT schema

```text
9. expose publishPresentation
10. keep operation count <= supported ceiling
11. regenerate OpenAPI
12. validate recursive object properties/security
```

## Frontend

```text
13. initialize Vite/React app
14. install TanStack Query + socket.io-client
15. one narration component
16. one connection indicator
17. presentation hash/revision in debug
```

## Real test

```text
18. run World Engine
19. open companion
20. ask Custom GPT to publish WORLD-ENGINE-BRIDGE-73921
21. verify matching DB/UI hashes
22. record receipt
```

Only then move to Phase 2.

---

# 41. DEFINITION OF DONE

The companion update is successful when a user can:

1. run World Engine;
2. open the companion browser/PWA;
3. play through the existing Custom GPT subscription;
4. see authoritative map/state panels update live;
5. see the GPT's intentionally published narration mirrored in the companion;
6. inspect character/inventory/quests/NPCs/factions/journal without asking GPT to repeat them;
7. recover from a connection drop without state divergence;
8. change presentation theme without changing game mechanics;
9. do all of this without a separate OpenAI inference API bill.

---

# 42. EVIDENCE / REFERENCE INDEX

## Internal World Engine

- `turn0file0`, lines 267–284 — missing inspector/timeline/graph/dashboard/map-layer UI.
- `turn0file0`, lines 288–307 — eight foundational engines.
- `turn0file1`, lines 21–50 — deterministic authority → simulation → narration separation.
- `turn0file5`, lines 10–24, 97–109 — SIMULATED/RESOLVED/NARRATED triage.
- `turn0file8`, lines 150–171 — Foundry maps and GPT Actions/MCP glue.

## External verified sources

### HAIP
https://github.com/TelivityAI/haip

Evidence used:
- React dashboard
- ChatGPT Gateway for GPT Actions
- REST/OpenAPI backend
- WebSocket gateway
- Socket.IO events
- Apache-2.0

### Foundry REST API
https://github.com/ThreeHats/foundryvtt-rest-api

Evidence used:
- WebSocket relay
- external REST applications
- custom dashboard use case
- automatic reconnect
- MIT licence

### Marinara Engine
https://github.com/Pasta-Devs/Marinara-Engine
https://github.com/Pasta-Devs/Marinara-Engine/blob/main/docs/FRONTEND.md
https://github.com/Pasta-Devs/Marinara-Engine/blob/main/docs/development/frontend.md

Evidence used:
- game-state store
- encounter store
- maps
- party
- NPCs
- inventory
- quests
- time/weather
- React/TanStack/Zustand frontend design
- AGPL-3.0 licence

### ST RPG HUD
https://github.com/ets1odoo-beep/st-rpg-hud

Evidence used:
- tabbed HUD
- inventory/party/quests/NPCs/relationships
- canvas minimap
- fog of war
- NPC dots
- action macros

Licence:
- **not established in this research pass**
- code reuse blocked until verified

### Silly Map
https://github.com/Jeka201216/Silly-Map

Evidence used:
- interactive D&D-style maps
- persistent map configuration
- context-aware locations
- AGPL-3.0

### RPG Companion
https://github.com/SpicyMarinara/rpg-companion-sillytavern

Evidence used:
- RPG widgets/state presentation
- AGPL-3.0

### MapLibre GL JS
https://github.com/maplibre/maplibre-gl-js

Evidence used:
- interactive browser map renderer
- BSD-3-Clause

### TanStack Query
https://github.com/TanStack/query

Evidence used:
- server-state query/cache/synchronization
- MIT

### Socket.IO
https://github.com/socketio/socket.io

Evidence used:
- realtime event transport/reconnection
- MIT

### python-socketio
https://github.com/miguelgrinberg/python-socketio

Evidence used:
- Python Socket.IO server/client
- compatible with JavaScript Socket.IO protocol family
- MIT

---

# 43. FINAL ENGINEERING DECISION

Do **not** build a bespoke game frontend from an empty React project by inventing every subsystem.

Build the smallest World Engine-specific integration layer around mature components:

```text
HAIP pattern
+ Socket.IO
+ TanStack Query
+ MapLibre
+ World Engine projections
+ original World Engine presentation/layout
```

Use AGPL projects to learn from their RPG UX and state decomposition, not as silent vendored dependencies.

Keep Foundry as an optional high-fidelity tactical client.

The unique value is not the HUD itself.

The unique value is:

```text
authoritative deterministic World Engine
+
ChatGPT subscription as narrator/interpreter
+
player-visible knowledge projection
+
live external RPG interface
```

That is the system this update should build.
