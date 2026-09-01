# World Engine 5.1.0

Security, process-lifecycle, and companion UI adaptation release.

## Identity

- Application/package release: **5.1.0**
- SQLite schema: **24** (unchanged)
- Desktop projection contract: **WE-DESKTOP-5.0.0 → WE-DESKTOP-5.1.0**
- Public GPT Actions: **exactly five** (unchanged)
- PBEM **2.2**, NRP **1.2** / NQR **1.2**, WEGEN **2.0** (unchanged)

## Security — pywebview bridge closed

`CompanionApi` stored the engine and projection as public attributes. pywebview's
exporter (`webview.util.inject_pywebview` → `get_functions`) recurses into any
public non-callable attribute carrying a `__module__` and publishes its methods
to JavaScript.

Measured on this build before the fix: **603 exported functions, 595 unintended**
(593 `engine.*`, 2 `projection.*`), including `engine.get_internal_state_block`.
After renaming to `_engine` / `_projection`: **exactly 8**, matching the
allowlist, with zero nested namespaces.

`tests/test_v510_bridge_surface.py` executes pywebview's own walker, extracted
from the installed package at run time rather than paraphrased, and carries a
positive control that re-attaches a public attribute to prove the gate can fail.

## Lifecycle — stale backend reclaim

The backend is spawned `DETACHED_PROCESS` and outlives the launcher. On the next
run a fresh API key made the protected probe fail, and startup aborted telling
the user to close the process by hand. `Launcher.stop_engine()` only stopped a
backend it held a handle to, so "Stop" left an auto-started one holding port
8000.

`world_engine/process_guard.py` reclaims such a backend only behind fail-closed
identity gates: exact `/health` payload identity, exact loopback listener, a
single owning PID, a recognized Python entry point, and an identity re-read
immediately before termination to defeat PID reuse. Graceful stop, then bounded
force, then confirmation the port was released. Any ambiguity terminates nothing
and reports why. `local_health()` now asserts payload identity, not a bare 200.

## Projection — additive only

Adds `projection_sequence` (equals the campaign revision), `terrain_seed`
(presentation-only), and `notification_summary` (severity computed in Python).
No existing field is removed or renamed. The snapshot now reads inside one
`BEGIN DEFERRED` transaction so a concurrent write cannot produce a projection
mixing pre-commit and post-commit state.

## Companion UI

Monochrome base with a single accent derived from the projection, collapsible
navigation rail and contextual drawer, deterministic procedural scene artwork,
visual-novel scene opening on public location change, server-derived alert tier,
thin scrollbars, and `prefers-reduced-motion` support. The renderer refuses an
unknown projection schema, discards out-of-order snapshots, and never overlaps
polls.

No browser companion, no `fetch`/XHR/WebSocket, no bearer key or base URL entry,
no remote assets, and no executable DOM sinks.

## Release engineering

`.gitattributes` pins line endings for SHA-256-verified release assets. With
`core.autocrlf=true` and no attributes file, Windows checkout rewrote
`legacy/World_Engine_1.63.txt` from LF to CRLF (73061 → 75443 bytes), so
`release_verify`'s `legacy_source_unchanged` gate failed for an environmental
reason in any Windows git checkout.

## Final adversarial corrections

The release gate found and corrected additional defects after the first broad
green run:

- stale cleanup now binds absolute `app.py`/`run_companion_demo.py` command
  lines to a bounded current/prior-install root registry; relative scripts,
  generic uvicorn modules, unrelated same-name projects, PID handoff, failed
  `taskkill`, redirected auth responses, and unverified port release all fail
  closed;
- launcher and automatic startup use absolute backend entrypoints, and normal
  launcher close does not destroy its window until the backend tree is stopped
  and port 8000 is independently confirmed free;
- the desktop snapshot uses one SQLite read transaction for campaign, player,
  public systems, simulation seed, and acceptance-chain-validated Chronicle;
- hidden remote relationships and arbitrary nested character JSON are filtered
  through explicit visibility/type/size allowlists;
- unexpected public bridge exceptions become generic bounded receipts rather
  than pywebview traceback payloads, and authoring stage exposes core counts
  instead of generated IDs/config/digests;
- endpoint operations are serialized and their results pass a secondary
  allowlist; engine/admin/tunnel credentials are stripped from the Companion
  child environment;
- the map supports local-only pan, zoom, and accessible selection; Chronicle
  renders accepted public presentations; compact rail/drawer behavior is
  corrected; and every duplicate or lower projection sequence is rejected.

Schema 24, the five public Actions, PBEM 2.2, NRP/NQR 1.2, WEGEN-2.0, and
