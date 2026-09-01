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
    return offline_player_html()


def offline_player_html() -> str:
    """Self-contained offline Web Audio player used by the launcher by default."""
    return r'''<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>World Engine Local Ambience</title><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'"><style>
:root { color-scheme:dark; font-family:Segoe UI,Arial,sans-serif; } body { margin:0; background:#111318; color:#e8ebf0; }
main { width:min(520px,calc(100vw - 24px)); margin:0 auto; padding:18px 0; } .panel { margin-top:14px; padding:14px; border:1px solid #3b414c; border-radius:10px; background:#181c22; }
.row { display:flex; gap:8px; align-items:center; margin-top:10px; flex-wrap:wrap; } button,input { background:#20242b; color:#eee; border:1px solid #3b414c; border-radius:6px; padding:8px 10px; }
button { cursor:pointer; } button.primary { background:#2c5caa; } input[type=range] { flex:1; min-width:160px; } #status { min-height:18px; font-size:13px; } #status[data-state=error] { color:#f28a8a; }
#track { font-size:16px; font-weight:600; min-height:22px; } #why,#context,.small { font-size:12px; color:#9eacba; line-height:1.45; }
</style></head><body><main>
<h1>World Engine local ambience</h1><p>This player generates sound inside this window. It never contacts YouTube or another media service.</p>
<section class="panel" aria-label="Local ambience controls"><div id="track">Local adaptive ambience</div><div id="status" data-state="off">Local ambience off</div><div id="why"></div><div id="context"></div>
<div class="row"><button id="enable" class="primary" type="button" aria-pressed="false">Play ambience</button><label for="vol">Volume</label><input id="vol" type="range" min="0" max="100" value="35"></div></section>
<p class="small">Sound begins only after you press Play. World state may change the local ambience profile, but never starts audio by itself.</p></main>
<script>
let audioContext=null, masterGain=null, voices=[], userEnabled=false, desiredPlayback=false, transition=null, currentProfile="adaptive";
function status(message,state){const e=document.getElementById("status");e.textContent=message;e.dataset.state=state;}
function sync(){const e=document.getElementById("enable");e.textContent=desiredPlayback?"Pause ambience":"Play ambience";e.setAttribute("aria-pressed",desiredPlayback?"true":"false");}
function profile(decision){const c=decision&&decision.context||{}, p=decision&&decision.track&&decision.track.profile;return p==="ambient"||p==="combat"?p:(c.combat?"combat":"ambient");}
function volume(value){if(!masterGain||!audioContext)return;const n=Math.max(0,Math.min(100,Number(value)||0))/100,t=n*n*.16;if(masterGain.gain.setTargetAtTime)masterGain.gain.setTargetAtTime(t,audioContext.currentTime,.03);else masterGain.gain.value=t;}
let arpeggioTimer=null,chordIndex=0;
function clearVoices(){if(arpeggioTimer!==null){window.clearInterval(arpeggioTimer);arpeggioTimer=null;}voices.forEach(v=>{try{v.stop();}catch(_ignored){}});voices=[];}
function buildVoices(kind){if(!audioContext||!masterGain)return;clearVoices();const chords=kind==="combat"?[[73.416,110,146.832],[82.407,123.471,164.814]]:[[55,65.406,82.407],[49,73.416,97.999]];const playChord=()=>{voices.forEach(v=>{try{v.stop();}catch(_ignored){}});voices=[];chords[chordIndex++%chords.length].forEach((frequency,index)=>{const voice=audioContext.createOscillator(),gain=audioContext.createGain();voice.type=index===0?"sine":"triangle";voice.frequency.value=frequency;gain.gain.value=kind==="combat"?.14:.20;voice.connect(gain);gain.connect(masterGain);voice.start();voices.push(voice);});};playChord();arpeggioTimer=window.setInterval(playChord,kind==="combat"?1900:4800);}
function createAudio(){const Ctor=window.AudioContext||window.webkitAudioContext;if(!Ctor)throw new Error("Web Audio unavailable");audioContext=new Ctor();masterGain=audioContext.createGain();masterGain.gain.value=0;masterGain.connect(audioContext.destination);buildVoices(currentProfile);volume(document.getElementById("vol").value);}
async function startNow(){try{if(!audioContext)createAudio();await audioContext.resume();if(!voices.length)buildVoices(currentProfile);if(audioContext.state&&audioContext.state!=="running")throw new Error("Audio did not start");userEnabled=true;status("Local ambience playing","playing");return true;}catch(_error){desiredPlayback=false;userEnabled=false;status("Audio is unavailable on this device. Your game is unaffected.","error");return false;}finally{sync();}}
async function pauseNow(){try{if(audioContext&&audioContext.suspend)await audioContext.suspend();clearVoices();userEnabled=false;status("Local ambience paused","paused");}catch(_error){status("Audio could not pause safely.","error");}finally{sync();}}
function requestPlayback(next){desiredPlayback=Boolean(next);sync();if(transition)return transition;transition=(async()=>{while(desiredPlayback!==userEnabled){if(desiredPlayback){if(!(await startNow()))break;}else await pauseNow();}})().finally(()=>{transition=null;});return transition;}
function contextText(c){const bits=[];if(c.location_name||c.location_id)bits.push("Location: "+(c.location_name||c.location_id));if(c.scene_type)bits.push("Scene: "+c.scene_type);if(c.combat)bits.push("Combat");if(c.weather)bits.push("Weather: "+c.weather);if(c.time_of_day)bits.push(c.time_of_day);return bits.join(" · ");}
function setDecision(payload){const d=typeof payload==="string"?JSON.parse(payload):payload||{},next=profile(d),changed=next!==currentProfile;currentProfile=next;document.getElementById("track").textContent="Local "+next+" ambience";document.getElementById("why").textContent=(d.reasons||[]).join(" · ");document.getElementById("context").textContent=contextText(d.context||{});if(audioContext&&changed)buildVoices(next);if(!userEnabled)status("Local ambience ready — press Play once.","ready");}
document.getElementById("enable").addEventListener("click",()=>requestPlayback(!desiredPlayback));document.getElementById("vol").addEventListener("input",e=>volume(e.target.value));window.worldEngineMusic={setDecision};window.addEventListener("pagehide",()=>{desiredPlayback=false;clearVoices();if(audioContext&&audioContext.close)audioContext.close();});
</script></body></html>'''


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

    def offline_decision(self) -> dict[str, Any]:
        return self.resolver.resolve(self.campaign_id, offline_only=True).as_dict()

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
            decision = api.offline_decision() if api else resolver.resolve(campaign_id, offline_only=True).as_dict()
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
    parser = argparse.ArgumentParser(description="World Engine offline local-ambience player")
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
    stop = threading.Event()
    window = webview.create_window(
        "World Engine — Local Ambience",
        html=player_html(),
        width=560,
        height=380,
        resizable=True,
        min_size=(520, 340),
        background_color="#111318",
    )

    def worker() -> None:
        poll_music(window, resolver, args.campaign, stop)

    try:
        webview.start(
            worker,
            gui="edgechromium" if os.name == "nt" else None,
            private_mode=False,
            storage_path=str(ROOT / "data" / "music_webview_storage"),
        )
    finally:
        stop.set()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
