from __future__ import annotations

import argparse
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from typing import Any, Callable

from world_engine import WorldEngine
from world_engine.music import MusicResolver, youtube_video_id

ROOT = Path(__file__).resolve().parent
DEFAULT_DB = ROOT / "data" / "world_engine.sqlite3"
DEFAULT_CATALOG = ROOT / "data" / "music_catalog.json"


def player_html(origin: str = "http://127.0.0.1") -> str:
    # Keep the YouTube player visible and >= 200x200 per YouTube embedded-player requirements.
    return r'''<!doctype html>
<html>
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<meta name="referrer" content="strict-origin-when-cross-origin" />
<title>World Engine Music</title>
<style>
:root { color-scheme: dark; font-family: Segoe UI, Arial, sans-serif; }
body { margin: 0; background:#111318; color:#e8ebf0; }
main { width: 520px; margin: 0 auto; padding: 12px; box-sizing:border-box; }
#playerWrap { width: 480px; height:270px; margin: 0 auto; background:#000; }
#player, #player iframe { width:480px !important; height:270px !important; }
.row { display:flex; gap:8px; align-items:center; margin-top:10px; }
button, input, select { background:#20242b; color:#eee; border:1px solid #3b414c; border-radius:6px; padding:7px 9px; }
button { cursor:pointer; }
button.primary { background:#2c5caa; }
button:disabled { opacity:.5; cursor:not-allowed; }
#status { font-size:13px; color:#b9c0ca; min-height:18px; }
#track { font-size:16px; font-weight:600; margin-top:8px; min-height:22px; }
#why { font-size:12px; color:#8f99a6; min-height:34px; }
#context { font-size:12px; color:#9eacba; }
input[type=text] { flex:1; min-width:0; }
input[type=range] { flex:1; }
.small { font-size:11px; color:#7d8794; line-height:1.35; margin-top:8px; }
</style>
</head>
<body>
<main>
  <div id="playerWrap"><div id="player"></div></div>
  <div id="track">No track selected</div>
  <div id="status">Waiting for World Engine context…</div>
  <div id="why"></div>
  <div id="context"></div>
  <div class="row">
    <button id="enable" class="primary" onclick="enableAudio()">Enable Background Music</button>
    <button onclick="togglePlay()">Play / Pause</button>
    <label>Volume</label><input id="vol" type="range" min="0" max="100" value="55" oninput="setUserVolume(this.value)" />
  </div>
  <div class="row">
    <input id="url" type="text" placeholder="Paste YouTube URL or video ID" />
    <input id="name" type="text" placeholder="Track name (optional)" />
  </div>
  <div class="row">
    <select id="scope">
      <option value="location">Current location ambience</option>
      <option value="combat">General combat</option>
      <option value="location_combat">Current-location combat</option>
      <option value="scene">Current scene type</option>
      <option value="director">Current director / deity / power</option>
      <option value="fallback">Fallback</option>
    </select>
    <button onclick="saveTrack()">Save Track for Context</button>
    <button onclick="testPlayer()">Test Player</button>
  </div>
  <div class="small">
    One click enables audio for this player session. After that, World Engine automatically switches tracks when location, scene, combat, weather/time, or configured director rules change. The YouTube video remains visible because embedded-player policy requires a visible player for automatic playback.
  </div>
</main>
<script>
const PLAYER_ORIGIN = __PLAYER_ORIGIN__;
let ytPlayer = null;
let ytReady = false;
let userEnabled = false;
let desired = null;
let currentVideo = null;
let userVolume = 55;
let playing = false;

const tag = document.createElement('script');
tag.src = 'https://www.youtube.com/iframe_api';
document.head.appendChild(tag);

window.onYouTubeIframeAPIReady = function() {
  ytPlayer = new YT.Player('player', {
    width: 480, height: 270,
    playerVars: { autoplay: 0, controls: 1, playsinline: 1, rel: 0, origin: PLAYER_ORIGIN, widget_referrer: PLAYER_ORIGIN },
    events: {
      onReady: () => { ytReady = true; ytPlayer.mute(); applyDesired(false); document.getElementById('status').textContent='Player ready — click Enable Background Music once.'; },
      onStateChange: (e) => {
        playing = e.data === YT.PlayerState.PLAYING;
        if (e.data === YT.PlayerState.ENDED && desired && desired.loop) {
          ytPlayer.seekTo(0, true);
          if (userEnabled) ytPlayer.playVideo();
        }
      },
      onAutoplayBlocked: () => { document.getElementById('status').textContent='Browser blocked autoplay. Click Enable Background Music again.'; },
      onError: async (e) => {
        const code = Number(e.data);
        const msg = code === 2
          ? 'YouTube Error 2: invalid player parameter or video ID. World Engine will reject this track and try the next candidate.'
          : code === 5
          ? 'YouTube Error 5: HTML5 playback failed. World Engine will try the next candidate.'
          : code === 100
          ? 'YouTube Error 100: video unavailable/private/removed. World Engine will try the next candidate.'
          : (code === 101 || code === 150)
          ? 'YouTube Error '+code+': embedding is disabled for this video. World Engine will try the next candidate.'
          : code === 153
          ? 'YouTube Error 153: client identity/referrer was rejected. Restart this v5.0.0 player so it loads from its local HTTP origin.'
          : ('YouTube player error '+code+'.');
        document.getElementById('status').textContent=msg;
        if (window.pywebview && window.pywebview.api && window.pywebview.api.report_player_error) {
          try {
            const receipt = await window.pywebview.api.report_player_error(code, msg, currentVideo || (desired && desired.videoId) || null);
            if (receipt && receipt.next_decision) {
              setDecision(receipt.next_decision);
            }
          } catch (reportError) {
            document.getElementById('status').textContent=msg+' Fallback report failed: '+String(reportError);
          }
        }
      }
    }
  });
};

function enableAudio() {
  userEnabled = true;
  document.getElementById('enable').textContent='Music Enabled';
  document.getElementById('enable').disabled=true;
  if (ytReady) {
    ytPlayer.unMute();
    ytPlayer.setVolume(userVolume);
    applyDesired(true);
  }
}
function togglePlay() {
  if (!ytReady) return;
  if (!userEnabled) enableAudio();
  if (playing) ytPlayer.pauseVideo(); else ytPlayer.playVideo();
}
function setUserVolume(v) {
  userVolume = Number(v);
  if (ytReady) ytPlayer.setVolume(userVolume);
}
function applyDesired(forcePlay) {
  if (!ytReady || !desired || !desired.videoId) return;
  const changed = currentVideo !== desired.videoId;
  if (changed) {
    currentVideo = desired.videoId;
    userVolume = Number(desired.volume ?? userVolume);
    document.getElementById('vol').value=String(userVolume);
    if (userEnabled) {
      ytPlayer.loadVideoById({videoId: desired.videoId});
      ytPlayer.unMute();
      ytPlayer.setVolume(userVolume);
    } else {
      ytPlayer.cueVideoById({videoId: desired.videoId});
      ytPlayer.mute();
    }
  } else if (forcePlay && userEnabled) {
    ytPlayer.unMute(); ytPlayer.setVolume(userVolume); ytPlayer.playVideo();
  }
}
function setDecision(payload) {
  const d = typeof payload === 'string' ? JSON.parse(payload) : payload;
  if (!d || !d.track) {
    desired = null;
    document.getElementById('track').textContent='No matching track configured';
    document.getElementById('why').textContent=(d && d.reasons || []).join(' · ');
    document.getElementById('context').textContent=contextText(d && d.context || {});
    return;
  }
  desired = {
    videoId: d.track.youtube_video_id,
    volume: d.track.volume ?? 55,
    loop: d.track.loop !== false,
    name: d.track.name || d.track.id || d.track.youtube_video_id
  };
  document.getElementById('track').textContent=desired.name;
  document.getElementById('why').textContent=(d.reasons || []).join(' · ');
  document.getElementById('context').textContent=contextText(d.context || {});
  document.getElementById('status').textContent=userEnabled ? 'Automatic soundtrack active.' : 'Track selected — click Enable Background Music once.';
  applyDesired(false);
}
function contextText(c) {
  const bits=[];
  if (c.location_name || c.location_id) bits.push('Location: '+(c.location_name || c.location_id));
  if (c.scene_type) bits.push('Scene: '+c.scene_type);
  if (c.combat) bits.push('COMBAT');
  if (c.weather) bits.push('Weather: '+c.weather);
  if (c.time_of_day) bits.push(c.time_of_day);
  if (c.director_names && c.director_names.length) bits.push('Directors: '+c.director_names.join(', '));
  return bits.join(' · ');
}
async function saveTrack() {
  const url=document.getElementById('url').value.trim();
  const name=document.getElementById('name').value.trim();
  const scope=document.getElementById('scope').value;
  if (!url) { document.getElementById('status').textContent='Paste a YouTube URL or video ID first.'; return; }
  try {
    const r=await window.pywebview.api.add_track(url,name,scope,Number(document.getElementById('vol').value));
    document.getElementById('status').textContent='Saved: '+r.name+'. Resolver will apply it automatically.';
    document.getElementById('url').value='';
  } catch(e) { document.getElementById('status').textContent='Could not save track: '+e; }
}
function testPlayer() {
  desired={videoId:'M7lc1UVf-VE', volume:userVolume, loop:true, name:'YouTube IFrame API test clip'};
  document.getElementById('track').textContent=desired.name;
  if (!userEnabled) enableAudio(); else applyDesired(true);
}
window.worldEngineMusic = { setDecision };
</script>
</body>
</html>'''.replace('__PLAYER_ORIGIN__', json.dumps(origin))


class _PlayerPageHandler(BaseHTTPRequestHandler):
    html_factory = staticmethod(player_html)
    origin = "http://127.0.0.1"

    def do_GET(self) -> None:
        if self.path not in {"/", "/player", "/player/"}:
            self.send_error(404)
            return
        body = self.html_factory(self.origin).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: Any) -> None:
        return


def start_player_server() -> tuple[ThreadingHTTPServer, str]:
    """Serve player HTML from a real loopback HTTP origin.

    YouTube error 153 is emitted when an embedded player lacks an HTTP Referer
    or equivalent API-client identification. A pywebview in-memory HTML document
    has no reliable web origin. Serving the same page from loopback gives WebView2
    an ordinary top-level origin so its YouTube iframe request carries a Referer.
    """
    server = ThreadingHTTPServer(("127.0.0.1", 0), _PlayerPageHandler)
    host, port = server.server_address[:2]
    origin = f"http://{host}:{port}"
    server.RequestHandlerClass.origin = origin  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, name="world-engine-music-http", daemon=True)
    thread.start()
    return server, origin


def make_youtube_referrer_handler(origin: str) -> Callable[[Any], None]:
    """Return a pywebview request hook that identifies the local embed host.

    pywebview 6.x allows request_sent handlers to mutate request headers before
    Edge WebView2 sends them. YouTube error 153 specifically means Referer/client
    identification is absent. We set Referer only on YouTube-owned embed/API
    requests and leave unrelated traffic untouched.
    """
    referer = origin.rstrip("/") + "/player"
    allowed_hosts = {
        "www.youtube.com",
        "youtube.com",
        "www.youtube-nocookie.com",
        "youtube-nocookie.com",
    }

    def _handler(request: Any) -> None:
        try:
            host = (urlparse(str(request.url)).hostname or "").lower()
            if host in allowed_hosts or host.endswith(".youtube.com") or host.endswith(".youtube-nocookie.com"):
                request.headers["Referer"] = referer
        except Exception as exc:
            # Playback diagnostics must never crash the game/runtime.
            print(f"[MUSIC] Could not attach YouTube Referer: {exc}", flush=True)

    return _handler


def install_youtube_referrer_hook(window: Any, origin: str) -> None:
    """Install the YouTube Referer hook without pywebview's async race.

    In pywebview 6.2.x, request_sent is documented as mutable, but ordinary
    Event handlers are dispatched on a worker thread. EdgeChromium compares the
    request headers immediately after firing the event. Marking this one event
    synchronous ensures the header mutation is complete before WebView2 sends
    the request. This is intentionally guarded and optional; the real local HTTP
    origin remains the primary standards-based fix.
    """
    event = window.events.request_sent
    if hasattr(event, "_should_lock"):
        event._should_lock = True
    event += make_youtube_referrer_handler(origin)


class PlayerApi:
    def __init__(self, resolver: MusicResolver, campaign_id: str):
        self.resolver = resolver
        self.campaign_id = campaign_id
        self.failed_video_ids: set[str] = set()
        self.last_player_error: dict[str, Any] | None = None

    def add_track(self, youtube: str, name: str, scope: str, volume: int = 55) -> dict[str, Any]:
        return self.resolver.add_track_for_context(
            self.campaign_id, youtube, name=name, scope=scope, volume=volume
        )

    def current_context(self) -> dict[str, Any]:
        return self.resolver.current_context(self.campaign_id)

    def active_failed_video_ids(self) -> set[str]:
        active = self.resolver.active_failed_video_ids()
        self.failed_video_ids.intersection_update(active)
        return set(active)

    def report_player_error(self, code: int, message: str, video_id: str | None = None) -> dict[str, Any]:
        # Presentation-only circuit breaker. Playback/content failures receive a
        # bounded cooldown and immediate deterministic fallback. Error 153 is a
        # player-origin/client-identity fault and never blacklists the video.
        code = int(code)
        canonical = None
        if video_id:
            try:
                canonical = youtube_video_id(video_id)
            except ValueError:
                canonical = None
        receipt = self.resolver.record_player_error(
            canonical, code, context=self.current_context(), message=str(message)
        ) if canonical else {"accepted": False, "fallback_requested": False}
        if receipt.get("fallback_requested") and canonical:
            self.failed_video_ids.add(canonical)
        self.last_player_error = {"code": code, "message": str(message), "video_id": canonical}
        print(f"[MUSIC] YouTube player error {code}: {message}; video={canonical}", flush=True)
        next_decision = None
        if receipt.get("fallback_requested"):
            next_decision = self.resolver.resolve(
                self.campaign_id, exclude_video_ids=self.active_failed_video_ids()
            ).as_dict()
        return {
            "reported": True,
            "code": code,
            "video_id": canonical,
            "fallback": bool(receipt.get("fallback_requested")),
            "cooldown_seconds": int(receipt.get("cooldown_seconds", 0)),
            "validation_persisted": bool(receipt.get("validation_persisted", False)),
            "excluded_video_ids": sorted(self.active_failed_video_ids()),
            "next_decision": next_decision,
        }


def poll_music(window: Any, resolver: MusicResolver, campaign_id: str, stop: threading.Event, api: PlayerApi | None = None) -> None:
    try:
        window.events.loaded.wait(timeout=20)
    except Exception:
        pass
    last_payload = None
    while not stop.is_set():
        try:
            decision = resolver.resolve(campaign_id, exclude_video_ids=(api.active_failed_video_ids() if api else None)).as_dict()
            payload = json.dumps(decision, ensure_ascii=False, sort_keys=True)
            if payload != last_payload:
                window.run_js(f"window.worldEngineMusic && window.worldEngineMusic.setDecision({json.dumps(payload)});")
                last_payload = payload
            catalog = resolver.load_catalog()
            poll_seconds = float((catalog.get("defaults") or {}).get("poll_seconds", 2.0))
            poll_seconds = max(0.5, min(15.0, poll_seconds))
        except Exception as exc:
            try:
                window.run_js(
                    "document.getElementById('status').textContent="
                    + json.dumps(f"World Engine music resolver error: {exc}")
                    + ";"
                )
            except Exception:
                pass
            poll_seconds = 2.0
        stop.wait(poll_seconds)


def main() -> int:
    parser = argparse.ArgumentParser(description="World Engine visible YouTube background-music player")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--catalog", default=str(DEFAULT_CATALOG))
    parser.add_argument("--campaign", default="default")
    args = parser.parse_args()

    try:
        import webview
    except ImportError:
        raise SystemExit("pywebview is not installed. Install requirements-music.txt or start music from the one-click launcher.")

    engine = WorldEngine(Path(args.db))
    engine.ensure_campaign(args.campaign)
    resolver = MusicResolver(engine, Path(args.catalog))
    resolver.ensure_catalog()
    api = PlayerApi(resolver, args.campaign)
    stop = threading.Event()
    player_server, player_origin = start_player_server()
    window = webview.create_window(
        "World Engine — YouTube Background Music",
        url=player_origin + "/player",
        js_api=api,
        width=560,
        height=520,
        resizable=True,
        min_size=(520, 480),
        background_color="#111318",
    )
    # Defense in depth for YouTube error 153: the top-level page has a real
    # loopback origin, and pywebview/WebView2 is also told to send that same
    # origin as Referer on YouTube embed/API requests.
    install_youtube_referrer_hook(window, player_origin)

    def worker() -> None:
        poll_music(window, resolver, args.campaign, stop, api)

    try:
        webview.start(
            worker,
            gui="edgechromium" if os.name == "nt" else None,
            private_mode=False,
            storage_path=str(ROOT / "data" / "music_webview_storage"),
        )
    finally:
        stop.set()
        player_server.shutdown()
        player_server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
