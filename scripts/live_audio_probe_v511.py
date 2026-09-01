#!/usr/bin/env python3
"""Bounded, real-WebView2 verification of the Companion's offline ambience.

This is release-diagnostic code, not application runtime code. It serves the
real Companion page with ``world_engine_companion.AssetHandler`` and deliberately
opens pywebview with no ``js_api``. A genuine Windows SendInput click activates
Web Audio, which browsers otherwise restrict to trusted user gestures.
"""
from __future__ import annotations

import ctypes
import http.server
import inspect
import json
import queue
import sys
import threading
import time
import urllib.request
import uuid
from ctypes import wintypes
from dataclasses import asdict, dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOAD_TIMEOUT_SECONDS = 25.0
STATE_TIMEOUT_SECONDS = 12.0
WINDOW_X, WINDOW_Y, WINDOW_WIDTH, WINDOW_HEIGHT = 120, 120, 960, 720

@dataclass
class ProbeResult:
    passed: bool
    served_path: str | None = None
    hwnd: int | None = None
    start_diagnostics: dict | None = None
    playing_diagnostics: dict | None = None
    paused_diagnostics: dict | None = None
    cleanup_window_closed: bool = False
    cleanup_server_stopped: bool = False
    error: str | None = None
    physical_output_note: str = "Software verified the Web Audio graph only; it cannot confirm a connected, unmuted, audible physical speaker."

class ProbeFailure(RuntimeError):
    pass

user32 = ctypes.WinDLL("user32", use_last_error=True)
FindWindowW = user32.FindWindowW
FindWindowW.argtypes, FindWindowW.restype = (wintypes.LPCWSTR, wintypes.LPCWSTR), wintypes.HWND
GetWindowTextLengthW = user32.GetWindowTextLengthW
GetWindowTextLengthW.argtypes, GetWindowTextLengthW.restype = (wintypes.HWND,), ctypes.c_int
GetWindowTextW = user32.GetWindowTextW
GetWindowTextW.argtypes, GetWindowTextW.restype = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int), ctypes.c_int
IsWindow = user32.IsWindow
IsWindow.argtypes, IsWindow.restype = (wintypes.HWND,), wintypes.BOOL
IsWindowVisible = user32.IsWindowVisible
IsWindowVisible.argtypes, IsWindowVisible.restype = (wintypes.HWND,), wintypes.BOOL
ClientToScreen = user32.ClientToScreen
ClientToScreen.argtypes, ClientToScreen.restype = (wintypes.HWND, ctypes.c_void_p), wintypes.BOOL
WindowFromPoint = user32.WindowFromPoint
WindowFromPoint.argtypes, WindowFromPoint.restype = (wintypes.POINT,), wintypes.HWND
GetAncestor = user32.GetAncestor
GetAncestor.argtypes, GetAncestor.restype = (wintypes.HWND, wintypes.UINT), wintypes.HWND
SetCursorPos = user32.SetCursorPos
SetCursorPos.argtypes, SetCursorPos.restype = (ctypes.c_int, ctypes.c_int), wintypes.BOOL
SendInput = user32.SendInput
SendInput.argtypes = (wintypes.UINT, ctypes.c_void_p, ctypes.c_int)
SendInput.restype = wintypes.UINT
GetSystemMetrics = user32.GetSystemMetrics

GA_ROOT = 2
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79
INPUT_MOUSE = 0
MOUSEEVENTF_MOVE, MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0001, 0x0002, 0x0004
MOUSEEVENTF_ABSOLUTE, MOUSEEVENTF_VIRTUALDESK = 0x8000, 0x4000

POINT = wintypes.POINT
class MOUSEINPUT(ctypes.Structure):
    _fields_ = (("dx", ctypes.c_long), ("dy", ctypes.c_long), ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t))
class INPUTUNION(ctypes.Union):
    _fields_ = (("mi", MOUSEINPUT),)
class INPUT(ctypes.Structure):
    _fields_ = (("type", wintypes.DWORD), ("union", INPUTUNION))

def _window_title(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(GetWindowTextLengthW(hwnd) + 1)
    GetWindowTextW(hwnd, buf, len(buf))
    return buf.value

def _require_exact_visible_window(title: str) -> int:
    hwnd = int(FindWindowW(None, title) or 0)
    if not hwnd:
        raise ProbeFailure("Uniquely titled pywebview window not found")
    if _window_title(hwnd) != title or not IsWindowVisible(hwnd):
        raise ProbeFailure("Refusing SendInput: exact visible probe-window identity was not verified")
    return hwnd

def _require_input_target(hwnd: int, title: str, point: POINT) -> None:
    if not IsWindow(hwnd) or not IsWindowVisible(hwnd) or _window_title(hwnd) != title:
        raise ProbeFailure("Refusing SendInput: exact visible probe-window identity changed")
    child = int(WindowFromPoint(point) or 0)
    root = int(GetAncestor(child, GA_ROOT) or 0) if child else 0
    if root != int(hwnd):
        raise ProbeFailure("Refusing SendInput: another window occludes the probe target")

def _send_click(hwnd: int, title: str, x: float, y: float) -> None:
    point = POINT(round(x), round(y))
    if not ClientToScreen(hwnd, ctypes.byref(point)):
        raise ProbeFailure(f"ClientToScreen failed: {ctypes.get_last_error()}")
    vx, vy = GetSystemMetrics(SM_XVIRTUALSCREEN), GetSystemMetrics(SM_YVIRTUALSCREEN)
    vw, vh = GetSystemMetrics(SM_CXVIRTUALSCREEN), GetSystemMetrics(SM_CYVIRTUALSCREEN)
    if vw < 2 or vh < 2:
        raise ProbeFailure("Invalid virtual desktop dimensions")
    if not (vx <= point.x < vx + vw and vy <= point.y < vy + vh):
        raise ProbeFailure("Refusing SendInput: probe target is outside the virtual desktop")
    if not SetCursorPos(point.x, point.y):
        raise ProbeFailure(f"SetCursorPos failed: {ctypes.get_last_error()}")
    # Windows can deny focus changes and another window can cover the target
    # between geometry discovery and input.  Hit-test the exact top-level probe
    # window immediately before the only SendInput call and fail closed.
    _require_input_target(hwnd, title, point)
    inputs = (INPUT * 2)(
        INPUT(INPUT_MOUSE, INPUTUNION(MOUSEINPUT(0, 0, 0, MOUSEEVENTF_LEFTDOWN, 0, 0))),
        INPUT(INPUT_MOUSE, INPUTUNION(MOUSEINPUT(0, 0, 0, MOUSEEVENTF_LEFTUP, 0, 0))),
    )
    sent = SendInput(len(inputs), ctypes.byref(inputs), ctypes.sizeof(INPUT))
    if sent != len(inputs):
        raise ProbeFailure(f"SendInput sent {sent}/{len(inputs)} events: {ctypes.get_last_error()}")

def _handler_factory(asset_handler: type[http.server.BaseHTTPRequestHandler]):
    params = inspect.signature(asset_handler).parameters
    supports_directory = "directory" in params or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())
    def factory(*args, **kwargs):
        if supports_directory:
            kwargs["directory"] = str(PROJECT_ROOT)
        return asset_handler(*args, **kwargs)
    return factory

def _wait_for_page(base: str) -> str:
    last: Exception | None = None
    for path in ("/ui", "/", "/companion_ui/index.html"):
        try:
            with urllib.request.urlopen(base + path, timeout=4) as response:
                body = response.read(256000)
            if b"ambience-toggle" in body or b"WorldEngineAmbience" in body:
                return path
        except Exception as exc:  # noqa: BLE001 - bounded diagnostic tries alternate local paths
            last = exc
    raise ProbeFailure(f"AssetHandler did not serve the Companion ambience page: {last}")

def _evaluate(window, js: str):
    try:
        return window.evaluate_js(js)
    except Exception as exc:
        raise ProbeFailure(f"JavaScript evaluation failed: {exc}") from exc

def _target_geometry(window) -> dict:
    value = _evaluate(window, """(() => { const e=document.querySelector('#ambience-toggle'); if(!e)return {ok:false,reason:'missing #ambience-toggle'}; const r=e.getBoundingClientRect(), x=r.left+r.width/2, y=r.top+r.height/2, hit=document.elementFromPoint(x,y); return {ok:r.width>1&&r.height>1&&!!hit&&(hit===e||e.contains(hit)),reason:!hit?'center has no element':(hit===e||e.contains(hit)?'':'center belongs to a different element'),x,y,hit_id:hit&&hit.id,hit_tag:hit&&hit.tagName}; })()""")
    if not isinstance(value, dict) or not value.get("ok"):
        raise ProbeFailure(f"Refusing SendInput: button identity/geometry failed: {value}")
    return value

def _diagnostics(window) -> dict:
    value = _evaluate(window, """(() => { const api=globalThis.WorldEngineAmbience; if(!api||typeof api.diagnostics!=='function') return {error:'WorldEngineAmbience.diagnostics unavailable'}; return {diagnostics:api.diagnostics(), button_text:document.querySelector('#ambience-toggle')?.textContent||null}; })()""")
    if not isinstance(value, dict) or value.get("error") or not isinstance(value.get("diagnostics"), dict):
        raise ProbeFailure(f"Invalid ambience diagnostics: {value}")
    return value["diagnostics"]

def _active(d: dict) -> bool:
    return d.get("context_state") == "running" and d.get("active") is True and int(d.get("voice_count", 0)) >= 2
def _paused(d: dict) -> bool:
    return d.get("context_state") == "suspended" and d.get("active") is False
def _poll(window, predicate, label: str) -> dict:
    deadline, last = time.monotonic()+STATE_TIMEOUT_SECONDS, None
    while time.monotonic() < deadline:
        last = _diagnostics(window)
        if predicate(last):
            return last
        time.sleep(0.2)
    raise ProbeFailure(f"Timed out waiting for {label}; last diagnostics={last}")

def run_probe() -> ProbeResult:
    result = ProbeResult(passed=False)
    server = thread = window = None
    hwnd: int | None = None
    title = f"World Engine 5.1.1 ambience probe {uuid.uuid4()}"
    try:
        if sys.platform != "win32":
            raise ProbeFailure("Requires Windows SendInput and EdgeChromium")
        sys.path.insert(0, str(PROJECT_ROOT))
        import webview

        from world_engine_companion import AssetHandler
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _handler_factory(AssetHandler))
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, name="we-audio-probe-http", daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        result.served_path = _wait_for_page(base)
        loaded, stages = threading.Event(), queue.Queue(maxsize=1)
        # No js_api by design: the shipped offline page needs no Python bridge.
        window = webview.create_window(title, url=base+result.served_path, width=WINDOW_WIDTH, height=WINDOW_HEIGHT, x=WINDOW_X, y=WINDOW_Y, resizable=False, on_top=True)
        window.events.loaded += lambda: loaded.set()
        def after_start():
            nonlocal hwnd
            try:
                if not loaded.wait(LOAD_TIMEOUT_SECONDS):
                    raise ProbeFailure("Timed out waiting for the Companion page")
                hwnd = _require_exact_visible_window(title)
                result.hwnd = hwnd
                first = _target_geometry(window)
                result.start_diagnostics = _diagnostics(window)
                if _active(result.start_diagnostics):
                    raise ProbeFailure("Ambience was active before the trusted click")
                _send_click(hwnd, title, first["x"], first["y"])
                result.playing_diagnostics = _poll(window, _active, "active Web Audio after Play")
                second = _target_geometry(window)
                _send_click(hwnd, title, second["x"], second["y"])
                result.paused_diagnostics = _poll(window, _paused, "suspended Web Audio after Pause")
                stages.put(None)
            except Exception as exc:  # noqa: BLE001 - worker returns a typed probe failure
                stages.put(exc)
            finally:
                try: window.destroy()
                except Exception: pass  # noqa: BLE001, S110 - best-effort teardown continues below
        webview.start(after_start, gui="edgechromium")
        try: failure = stages.get_nowait()
        except queue.Empty as exc: raise ProbeFailure("WebView loop ended without a probe result") from exc
        if failure: raise failure
        result.cleanup_window_closed = hwnd is None or not bool(IsWindow(hwnd))
        if not result.cleanup_window_closed:
            raise ProbeFailure("Probe window remained alive after destroy")
        result.passed = True
    except Exception as exc:  # noqa: BLE001 - top-level diagnostic reports failure without input retry
        result.error = str(exc)
    finally:
        if window is not None:
            try: window.destroy()
            except Exception: pass  # noqa: BLE001, S110 - server cleanup must still run
        if server is not None:
            try:
                server.shutdown(); server.server_close()
            finally:
                if thread is not None:
                    thread.join(timeout=5)
                    result.cleanup_server_stopped = not thread.is_alive()
        if server is not None and not result.cleanup_server_stopped and result.error is None:
            result.error, result.passed = "Probe HTTP server did not stop during cleanup", False
    return result

def main() -> int:
    result = run_probe()
    print(json.dumps(asdict(result), indent=2, sort_keys=True))
    return 0 if result.passed else 1
if __name__ == "__main__":
    raise SystemExit(main())
