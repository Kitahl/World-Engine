# World Engine v3.6.0 Changelog

## Added

- Visible YouTube background-music player hosted by **pywebview**.
- Official YouTube IFrame API control: play/pause, volume, video switching, single-video loop.
- One-time **Enable Background Music** user gesture, followed by automatic track switching.
- Deterministic music resolver driven by authoritative World Engine context:
  - location
  - region / realm
  - SCENE type
  - combat state
  - director / deity / powerful actor
  - weather
  - time-of-day
  - scene/location music tags
- In-player context binding: paste a YouTube URL and save it as location ambience, generic combat, location combat, scene type, director/deity/power, or fallback.
- `data/music_catalog.json` configuration with atomic saves.
- `requirements-music.txt` so music dependencies do not threaten the core backend install.
- Launcher controls: Start/Stop Music Player, Open Music Catalog, Music Setup Help.

## Deliberate constraints

- No `yt-dlp`, audio extraction, or downloaded YouTube media.
- No hidden/minimized autoplay workaround. YouTube requires the embedded player to remain visible for automatic playback; v3.6 uses a 480×270 visible player.
- Music state never mutates authoritative simulation state or increments campaign revision.
- No additional GPT Action; music is local presentation controlled by persisted world/scene state.
