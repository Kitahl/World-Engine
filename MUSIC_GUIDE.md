# World Engine 5.1.1 — Offline Companion Music

World Engine’s background soundtrack is part of the Companion window. It is generated locally with Web Audio rather than loaded from YouTube, an iframe, a media URL, or a streaming account.

## Use it

1. Start World Engine with `START_WORLD_ENGINE.vbs`.
2. In the Companion, use the music Play, Pause, and volume controls.
3. Press **Play** once to begin the soundtrack.

The explicit Play press is required by browser/WebView autoplay policy. After that user gesture, the Companion can maintain its local adaptive ambience without any network connection. The normal startup path does not open a separate music window.

## Offline catalog behavior

The resolver selects the local procedural soundtrack by default. Older saved catalogs that contain only streaming/YouTube entries automatically receive the local fallback, so an unavailable external track does not leave the game silent or cause a streaming dependency to reappear.

No remote audio is downloaded, proxied, extracted, or embedded. There is no YouTube URL/ID paste flow in the 5.1.1 Companion.

## Verification boundary

Automated tests verify the offline page has no remote/embed/fetch dependencies, creates its audio graph only after an explicit gesture, handles rapid control changes, supports pause/volume behavior, and keeps the desktop bridge closed. This proves the runtime wiring, not the physical state of a particular Windows speaker, driver, mixer, or mute switch.