(function () {
  "use strict";
  var context = null, master = null, voices = [], timer = null;
  var active = false, wanted = false, transition = null, profile = "calm";
  var PROFILES = {
    calm: [[55, 65.406, 82.407], [49, 73.416, 97.999]],
    travel: [[58.27, 73.416, 87.307], [55, 82.407, 110]],
    tension: [[51.913, 61.735, 77.782], [46.249, 69.296, 92.499]],
    combat: [[73.416, 110, 146.832], [82.407, 123.471, 164.814]],
    fantasy: [[65.406, 82.407, 98], [61.735, 92.499, 123.471]]
  };
  function byId(id) { return document.getElementById(id); }
  function controls() { return { toggle: byId("ambience-toggle"), volume: byId("ambience-volume"), status: byId("ambience-status") }; }
  function status(message, state) { var e = controls().status; if (e) { e.textContent = message; e.dataset.state = state; } }
  function sync() { var e = controls().toggle; if (e) { e.textContent = wanted ? "Pause ambience" : "Play ambience"; e.setAttribute("aria-pressed", wanted ? "true" : "false"); } }
  function gain(value) { if (!master || !context) { return; } var n = Math.max(0, Math.min(100, Number(value) || 0)) / 100, v = n * n * .16; if (master.gain.setTargetAtTime) { master.gain.setTargetAtTime(v, context.currentTime, .03); } else { master.gain.value = v; } }
  function clear() { if (timer !== null) { window.clearInterval(timer); timer = null; } voices.forEach(function (v) { try { v.stop(); } catch (_ignored) {} }); voices = []; }
  function chord() { var set = PROFILES[profile] || PROFILES.calm, notes = set[(window.__weChordIndex = (window.__weChordIndex || 0) + 1) % set.length]; clear(); notes.forEach(function (frequency, index) { var oscillator = context.createOscillator(), voice = context.createGain(); oscillator.type = index === 0 ? "sine" : "triangle"; oscillator.frequency.value = frequency; voice.gain.value = profile === "combat" ? .14 : .20; oscillator.connect(voice); voice.connect(master); oscillator.start(); voices.push(oscillator); }); timer = window.setInterval(chord, profile === "combat" ? 1900 : 4800); }
  function create() { var Ctor = window.AudioContext || window.webkitAudioContext; if (!Ctor) { throw new Error("Web Audio unavailable"); } context = new Ctor(); master = context.createGain(); master.gain.value = 0; master.connect(context.destination); gain(controls().volume ? controls().volume.value : 35); }
  function startNow() { try { if (!context) { create(); } return Promise.resolve(context.resume()).then(function () { if (context.state && context.state !== "running") { throw new Error("Audio did not start"); } if (!voices.length) { chord(); } active = true; status("Local " + profile + " ambience playing", "playing"); return true; }).catch(function () { return false; }); } catch (_error) { return Promise.resolve(false); } }
  function pauseNow() { return Promise.resolve(context && context.suspend ? context.suspend() : undefined).then(function () { clear(); active = false; status("Local ambience paused", "paused"); }); }
  function request(next) { wanted = Boolean(next); sync(); if (transition) { return transition; } transition = (function loop() { if (wanted === active) { return Promise.resolve(); } return (wanted ? startNow() : pauseNow()).then(function (ok) { if (ok === false) { wanted = false; active = false; status("Audio unavailable. Your game is unaffected.", "error"); } return loop(); }); }()).finally(function () { transition = null; }); return transition; }
  function setContext(data) { var d = data || {}, scene = String(d.scene_type || "").toLowerCase(), weather = String(d.weather || "").toLowerCase(); var next = d.combat ? "combat" : (/travel|journey/.test(scene) ? "travel" : (/ritual|magic|arcane/.test(scene) ? "fantasy" : (/storm|danger|tension/.test(scene + " " + weather) ? "tension" : "calm"))); if (next === profile) { return; } profile = next; if (active && context) { chord(); status("Local " + profile + " ambience playing", "playing"); } }
  function bind() { var c = controls(); if (!c.toggle || !c.volume || !c.status || c.toggle.dataset.bound === "true") { return; } c.toggle.dataset.bound = "true"; sync(); status("Local " + profile + " ambience off", "off"); c.toggle.addEventListener("click", function () { return request(!wanted); }); c.volume.addEventListener("input", function () { gain(c.volume.value); }); window.addEventListener("pagehide", function () { wanted = false; clear(); if (context && context.close) { context.close(); } }); }
  window.WorldEngineAmbience = { bind: bind, setContext: setContext, setProfile: function (next) { if (PROFILES[next]) { setContext({ scene_type: next === "fantasy" ? "ritual" : next, combat: next === "combat" }); } }, diagnostics: function () { return { active: active, wanted: wanted, profile: profile, context_state: context ? String(context.state || "") : null, voice_count: voices.length }; } };
  if (document.readyState === "loading") { document.addEventListener("DOMContentLoaded", bind, { once: true }); } else { bind(); }
}());