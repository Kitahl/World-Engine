"""Executable contract tests for the launcher-default offline music player."""

from __future__ import annotations

import base64
import inspect
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

import music_player
from music_player import player_html
from world_engine import WorldEngine
from world_engine.music import MusicResolver


def _script(html: str) -> str:
    match = re.search(r"<script>\s*(.*?)\s*</script>", html, re.S)
    assert match, "offline player must contain its Web Audio wiring"
    return match.group(1)


def test_player_page_is_offline_and_explicitly_gesture_started() -> None:
    html = player_html()
    script = _script(html)
    assert "AudioContext" in script and "createOscillator" in script
    assert "requestPlayback" in script and "audioContext.resume()" in script
    assert "audioContext.suspend()" in script and "setTargetAtTime" in script
    assert "https://" not in html and "iframe" not in html.lower()
    assert "fetch(" not in script and "XMLHttpRequest" not in script and "youtube.com" not in html.lower()


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is required for the Web Audio behaviour canary")
def test_actual_player_wiring_defers_context_and_serializes_rapid_clicks() -> None:
    source = base64.b64encode(_script(player_html()).encode()).decode()
    harness = r'''
const source=Buffer.from(process.argv[1],"base64").toString("utf8");let contexts=0,resumes=0,suspends=0,oscillators=0,resumeRelease;
class El{constructor(value=""){this.value=value;this.textContent="";this.dataset={};this.handlers={};}addEventListener(n,f){this.handlers[n]=f;}setAttribute(){}}
const els={enable:new El(),vol:new El("35"),status:new El(),track:new El(),why:new El(),context:new El()};
class Ctx{constructor(){contexts++;this.state="suspended";this.destination={};this.currentTime=0;}createGain(){return {gain:{value:0,setTargetAtTime(){}},connect(){}};}createOscillator(){oscillators++;return {frequency:{value:0},connect(){},start(){},stop(){}};}resume(){resumes++;return new Promise(resolve=>{resumeRelease=()=>{this.state="running";resolve();};});}suspend(){suspends++;this.state="suspended";return Promise.resolve();}}
global.document={getElementById:id=>els[id]};global.window={AudioContext:Ctx,setInterval:()=>1,clearInterval:()=>{},addEventListener:()=>{}};eval(source);if(contexts!==0)throw Error("context before gesture");const a=els.enable.handlers.click(),b=els.enable.handlers.click();if(contexts!==1||resumes!==1)throw Error("double click raced");resumeRelease();Promise.all([a,b]).then(()=>{if(suspends!==1||oscillators<2)throw Error("transition/audio canary failed");console.log("ok");}).catch(e=>{console.error(e);process.exit(1);});
'''
    result = subprocess.run(["node", "-e", harness, source], text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_youtube_only_catalog_receives_local_fallback_and_offline_resolves_it() -> None:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        engine = WorldEngine(root / "world.sqlite3")
        engine.ensure_campaign("c")
        resolver = MusicResolver(engine, root / "music.json")
        resolver.save_catalog({"version": 1, "tracks": [{"id": "legacy", "youtube": "M7lc1UVf-VE", "match": {}}]})
        catalog = resolver.load_catalog()
        assert any(track.get("source") == "procedural" for track in catalog["tracks"])
        decision = resolver.resolve("c", offline_only=True)
        assert decision.track is not None and decision.track["source"] == "procedural"
        assert decision.track["id"] == "local-adaptive-ambience"


def test_offline_player_window_exports_no_python_callable_surface() -> None:
    html = player_html()
    source = inspect.getsource(music_player.main)
    assert "window.pywebview" not in html
    assert "js_api=" not in source
    assert "PlayerApi(" not in source
@pytest.mark.skipif(shutil.which("node") is None, reason="Node is required for Companion Audio state canary")
def test_companion_audio_requires_running_state_before_reporting_playing() -> None:
    source = base64.b64encode((Path("companion_ui/ambient_audio.js").read_text(encoding="utf-8")).encode()).decode()
    harness = r'''
const src=Buffer.from(process.argv[1],"base64").toString("utf8"), running=process.argv[2]==="running";let contexts=0,osc=0;class E{constructor(){this.dataset={};this.handlers={};this.value="35";this.textContent="";}addEventListener(n,f){this.handlers[n]=f;}setAttribute(){}}const e={"ambience-toggle":new E(),"ambience-volume":new E(),"ambience-status":new E()};class C{constructor(){contexts++;this.state="suspended";this.destination={};this.currentTime=0;}createGain(){return {gain:{setTargetAtTime(){}},connect(){}}}createOscillator(){osc++;return {frequency:{},connect(){},start(){},stop(){}}}resume(){if(running)this.state="running";return Promise.resolve()}suspend(){this.state="suspended";return Promise.resolve()}}global.document={readyState:"complete",getElementById:k=>e[k],addEventListener(){}};global.window={AudioContext:C,setInterval:()=>1,clearInterval(){},addEventListener(){}};eval(src);if(contexts!==0)throw Error("context before gesture");e["ambience-toggle"].handlers.click();setTimeout(()=>{const d=window.WorldEngineAmbience.diagnostics();if(running){if(!d.active||d.voice_count<2)throw Error("running graph absent")}else if(d.active||d.context_state!=="suspended")throw Error("suspended incorrectly playing");console.log("ok")},0);
'''
    for state in ("suspended", "running"):
        result = subprocess.run(["node", "-e", harness, source, state], text=True, capture_output=True, check=False)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "ok"