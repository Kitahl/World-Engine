# World Engine 5.1.1 — automatic tunnel and offline music bug-fix report

## Scope

This patch addresses two reported failures:

1. ngrok required a copied account key on a normal first run; the copy path could fail and should not be a mandatory game-start step.
2. The Companion music path depended on external playback and was not reliably audible.

It also keeps the normal desktop interaction to a single Companion window. `START_WORLD_ENGINE.vbs` is the normal hidden-helper start flow; backend, endpoint, and lifecycle processes are hidden helpers. The visible batch launcher and diagnostic tooling are not part of normal play.

## Root cause and correction

| Finding | Correction | Safety boundary |
| --- | --- | --- |
| Ngrok needs an account authtoken, which cannot be safely acquired from an application without user account authorization. | Default first-use connection uses an automatic Cloudflare Quick Tunnel rather than token clipboard automation. | The URL is temporary/random; no claim of a stable hostname. |
| A temporary URL can change after restart, invalidating a previously imported GPT Actions schema. | Persist and surface a re-import-required warning until explicit acknowledgement. | Existing stable providers are reused rather than silently replaced. |
| A tunnel helper can inherit a user's Cloudflare configuration or stop an unrelated process if ownership is not recorded. | Use a World-Engine-owned isolated configuration/home and an owned-child receipt before lifecycle operations. | Never read, rename, or mutate a user Cloudflare configuration; only terminate a verified owned child. |
| Remote media/embed playback is subject to availability, embed policy, and autoplay restrictions. | Generate the soundtrack locally with Web Audio and expose Companion Play/Pause/volume controls. | Audio begins only from an explicit user gesture; no claim that automated code can prove physical speaker output. |
| Multiple visible helpers made basic start confusing. | The normal path opens one Companion window; helpers are hidden. | `launcher.py` remains diagnostic/manual compatibility tooling if retained. |

## Retained contracts

| Contract | 5.1.1 status |
| --- | --- |
| SQLite schema 24 | retained |
| `WE-DESKTOP-5.1.0` projection | retained |
| `procedural_desktop_companion` feature introduction 5.1.0 | retained |
| `CUSTOM_GPT_INSTRUCTIONS_V510.txt` | retained |
| Five public GPT Actions | retained |
| Procedural generator `WEGEN-2.0` | retained |

## Required evidence

The release package is accepted only when `scripts/package_v511.py` records all of the following in `WORLD_ENGINE_V511_HANDOFF.json`:

- Python compilation, source test suite, static Action audit, release audit, and narrative audit;
- the same verification after clean extraction of the generated ZIP;
- archive membership and critical-file hashes, including the new automatic-tunnel and offline-music tests;
- the final source commit and ZIP SHA-256.

Focused tunnel tests must exercise no-account command construction, configuration isolation, single-owner/restart behavior, and re-import-warning persistence. Focused music tests must exercise offline fallback, explicit gesture start, pause/volume control, and a restricted desktop bridge surface.

## Explicitly unverified boundaries

- Physical audibility through a particular Windows audio driver, mixer, device, or mute state.
- Live named ngrok, Cloudflare, or Tailscale account connectivity.
- Windows double-click and Service Control Manager execution on each target machine.
- Live Foundry relay delivery and external GPT Builder re-import completion.

## Accepted base provenance

Supplied base archive SHA-256:

```text
57826818CAE1835B8075182FE935D289B55858B5F14A29E81B31D4A83286A205
```

Supplied evidence states that a line-ending-insensitive diff against certified commit `a7ca3365008e8d59d92ea59260efa90d60c0c430` had no semantic changes. That establishes the accepted starting point; the 5.1.1 handoff independently records the resulting artifact's own identity and verification results.