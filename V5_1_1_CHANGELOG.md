# World Engine 5.1.1 changelog

## Purpose

5.1.1 is a patch release over the accepted 5.1.0 desktop/Companion base. It fixes the first-run external-connection and music experience without changing the public GPT Action contract, database schema, desktop projection schema, or procedural-generation contract.

## User-facing changes

- First external GPT connection now starts an account-free Cloudflare Quick Tunnel automatically. No ngrok authtoken copy/paste step is required.
- The automatic endpoint is correctly identified as temporary and random. When it changes, World Engine keeps a GPT Action schema re-import warning until the operator acknowledges it.
- Existing configured ngrok, named Cloudflare, and Tailscale routes continue to be reused as optional stable routes. They retain their provider account/device prerequisites.
- The normal Windows flow begins with `START_WORLD_ENGINE.vbs` and opens one Companion window. The backend, endpoint helper, and lifecycle supervisor are hidden helpers; music controls are inside the Companion. `START_WORLD_ENGINE.bat` and `launcher.py` remain available only for visible diagnostic/manual compatibility use.
- Background music is generated offline with Web Audio rather than loaded from YouTube or an external media site. Older unavailable streaming entries fall back to the bundled procedural soundtrack.

## Safety and compatibility

- SQLite schema remains **24**.
- Desktop projection remains **WE-DESKTOP-5.1.0**.
- `procedural_desktop_companion` remains introduced at **5.1.0**.
- Active GPT instructions remain `CUSTOM_GPT_INSTRUCTIONS_V510.txt`; the five public actions remain unchanged.
- Automatic tunnel work is isolated from personal Cloudflare configuration and must retain ownership evidence before it stops a child process.
- The local UI does not require any tunnel.

## Accepted base evidence

The supplied accepted 5.1.0 archive hash is:

```text
57826818CAE1835B8075182FE935D289B55858B5F14A29E81B31D4A83286A205
```

The supplied line-ending-insensitive comparison against certified commit `a7ca3365008e8d59d92ea59260efa90d60c0c430` reported no semantic changes. This release treats that archive as the accepted base; the final 5.1.1 package has its own independently generated hash and clean-extracted verification handoff.

## Verification

`scripts/release_verify_v511.py` keeps the mature 5.1.0 audit surface and adds the 5.1.1 API-receipt, retained-contract, endpoint-documentation, offline-music, and new-test-presence gates. `scripts/package_v511.py` verifies source and a clean-extracted archive before emitting `WORLD_ENGINE_V511_HANDOFF.json`.

Physical speaker audibility, live stable-provider accounts, Windows Service Control Manager behavior, Foundry relay delivery, and the external GPT Builder remain machine/account boundaries and are not represented as automated passes.