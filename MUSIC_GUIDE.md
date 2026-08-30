# World Engine v3.6 — YouTube Background Music

## Fast setup

1. Start `START_WORLD_ENGINE.bat`.
2. Wait for the music window.
3. Click **Enable Background Music** once.
4. Paste a YouTube URL or 11-character video ID.
5. Choose a binding:
   - Current location ambience
   - General combat
   - Current-location combat
   - Current scene type
   - Current director / deity / power
   - Fallback
6. Click **Save Track for Context**.

From then on, World Engine automatically selects the highest-specificity matching configured track.

## Resolver order

The resolver is deterministic. A configured location-specific combat track normally beats generic combat; combat beats ordinary ambience; scene/director bindings can override lower-priority ambient tracks. Users can edit `priority` values directly in `data/music_catalog.json` for custom ordering.

Matching fields supported in the JSON catalog:

- `location_ids`
- `regions`
- `realm_ids`
- `combat`
- `scene_types`
- `director_ids`
- `director_kinds`
- `weather`
- `time_of_day`
- `location_tags_any`
- `scene_tags_any`

## Autoplay limitation

YouTube and Chromium autoplay rules prevent a normal app from reliably starting audible media without user interaction. v3.6 therefore uses one explicit enable click. Subsequent automatic track changes are programmatic YouTube IFrame API calls.

The YouTube player is kept visible at 480×270. Do not hide it or reduce it below YouTube's documented minimum player size.

## Error 153 hotfix (v3.7.1)

YouTube defines error 153 as a missing `HTTP Referer` or equivalent API-client identity. Earlier World Engine music builds loaded the player from in-memory HTML, which can leave desktop WebViews without a usable referrer. v3.7.1 serves the player from a loopback HTTP origin and explicitly attaches that origin as the YouTube request referrer in Edge WebView2.

If an individual video returns error 101/150 instead, that video's owner has disabled third-party embedding; choose another video. Error 100 means the video is unavailable/private.

## v3.9.2 playback reliability hardening

The player now canonicalizes YouTube inputs before they reach `YT.Player`. Accepted inputs include a raw 11-character video ID and supported `youtube.com/watch`, `youtu.be`, `youtube.com/embed`, `youtube.com/shorts`, and `music.youtube.com/watch` URLs. Playlist-only URLs, malformed IDs, whitespace-contaminated values, and non-video identifiers are rejected instead of being sent to the player.

Runtime player errors use deterministic failover:

- **2** — invalid parameter/video ID: reject/blacklist that candidate for the current player session and resolve the next matching track.
- **5** — HTML5 playback failure: skip the candidate for the session and try the next match.
- **100** — unavailable/private/removed video: skip the candidate for the session.
- **101 / 150** — owner disabled embedding: skip the candidate for the session.
- **153** — missing/invalid Referer or equivalent client identity: do **not** blacklist the track. This is a player/origin problem, and the v3.7.1 loopback-origin/Referer mitigation remains active.

The resolver never repeatedly selects a track already rejected for one of the track-specific errors above during the same player session. If every matching candidate fails, it falls through to less-specific configured bindings and ultimately silence rather than looping forever.

World Engine does not download, extract, proxy, or bypass YouTube audio, embedding restrictions, or browser autoplay restrictions. The user still performs the initial **Enable Background Music** gesture required by browser autoplay policy.

### Verification boundary

Unit/integration tests validate URL parsing, error classification, deterministic candidate failover, and the Error-153 non-blacklist invariant. A Linux/headless build environment cannot prove audible playback in the Windows Edge WebView2 runtime. Until a physical Windows smoke test is run, report **LOGIC VERIFIED / REAL WINDOWS WEBVIEW2 PLAYBACK NOT VERIFIED**.
