from __future__ import annotations

import json
import hashlib
import os
import queue
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from world_engine_connection_guard import persistent_data_dir, migrate_legacy_data, auto_migrate_from_previous_install, install_environment, ensure_guard_config
from world_engine_permanent_endpoint import (
    CLOUDFLARED_VERSION,
    CLOUDFLARED_WINDOWS_AMD64_SHA256,
    CLOUDFLARED_WINDOWS_AMD64_URL,
    ensure_permanent_runtime,
    load_permanent_config,
)
from world_engine_autostart import register_current_install
from world_engine.openapi_compat import (
    PUBLIC_ACTION_OPERATION_IDS,
    ensure_object_properties,
    mark_actions_non_consequential,
    object_schema_paths_missing_properties,
)
try:
    import tkinter as tk
    from tkinter import messagebox, ttk
except ImportError:  # helper functions remain importable on headless/minimal Python installs
    class _HeadlessTk:
        class Tk:  # enough for class definition; GUI construction still fails explicitly below
            pass
    class _MissingTkModule:
        def __getattr__(self, name):
            raise RuntimeError("Tkinter is required to run the World Engine GUI launcher")
    tk = _HeadlessTk()
    messagebox = _MissingTkModule()
    ttk = _MissingTkModule()

ROOT = Path(__file__).resolve().parent
LEGACY_DATA_DIR = ROOT / "data"
DATA_DIR = persistent_data_dir()
DATA_DIR.mkdir(parents=True, exist_ok=True)
V398_MIGRATION = migrate_legacy_data(LEGACY_DATA_DIR, DATA_DIR)
V398_PREVIOUS_MIGRATION = auto_migrate_from_previous_install(ROOT, DATA_DIR)
install_environment(DATA_DIR)
V398_CONNECTION_GUARD = ensure_guard_config(DATA_DIR)
V398_RUNTIME_INSTALL = register_current_install(ROOT, python_exe=sys.executable, data=DATA_DIR)
TOOLS_DIR = DATA_DIR / "tools"
VENV_DIR = ROOT / ".venv"
CONFIG_PATH = DATA_DIR / "launcher_config.json"
DB_PATH = DATA_DIR / "world_engine.sqlite3"
MUSIC_CATALOG_PATH = DATA_DIR / "music_catalog.json"
MUSIC_TEMPLATE_PATH = ROOT / "MUSIC_CATALOG_TEMPLATE.json"
LOCAL_URL = "http://127.0.0.1:8000"
CLOUDFLARED_PATH = TOOLS_DIR / f"cloudflared-{CLOUDFLARED_VERSION}-windows-amd64.exe"
CLOUDFLARED_URL = CLOUDFLARED_WINDOWS_AMD64_URL
CLOUDFLARED_SHA256 = CLOUDFLARED_WINDOWS_AMD64_SHA256
TUNNEL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com", re.I)


def load_config() -> dict:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_config(config: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


def ensure_api_key(config: dict) -> str:
    key = str(config.get("api_key") or "").strip()
    if len(key) < 24:
        key = secrets.token_urlsafe(32)
        config["api_key"] = key
        save_config(config)
    return key


def ensure_admin_key(config: dict, api_key: str) -> str:
    key = str(config.get("admin_key") or "").strip()
    if len(key) < 24 or secrets.compare_digest(key, api_key):
        key = secrets.token_urlsafe(32)
        config["admin_key"] = key
        config["admin_key_fingerprint"] = api_key_fingerprint(key)
        save_config(config)
    return key


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def cloudflared_hash_ok(path: Path) -> bool:
    return path.is_file() and file_sha256(path).lower() == CLOUDFLARED_SHA256.lower()


def venv_python() -> Path:
    if os.name == "nt":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def generate_action_schema(public_url: str, destination: Path | None = None) -> Path:
    destination = destination or (ROOT / "openapi_actions_live.json")
    with urllib.request.urlopen(f"{LOCAL_URL}/openapi.json", timeout=5) as r:
        schema = json.load(r)
    schema["servers"] = [{"url": public_url.rstrip("/")}]
    ensure_object_properties(schema)
    paths = schema.get("paths", {})
    # GPT Actions builder accepts at most 30 operations. These helper reads remain
    # available on the backend, but buildImageCue consumes them internally.
    for hidden_path in (
        "/api/snapshot",
        "/api/visual/profile/{entity_kind}/{entity_id}",
        "/api/visual/state/{scope_type}/{scope_id}",
        "/api/visual/recent",
        "/api/context",
        "/api/entity/{kind}/{entity_id}",
        "/api/setup/npc",
        "/api/setup/faction",
        "/api/npc/state",
        "/api/faction/adjust",
        "/api/world/state",
        "/api/sim/configure",
        "/api/authoring",
    ):
        paths.pop(hidden_path, None)
    # Visual preferences remain backend/launcher configuration. Hide both
    # operations from GPT Actions to make room for publishPresentation.
    paths.pop("/api/visual/preferences", None)
    # v3.9.8 exposes saveVisualProfile for canonical identity-reference generation; hide dev-only internal state instead.
    paths.pop("/api/internal/state", None)
    # saveVisualState remains backend/MCP-only; scene mutations and image cue generation preserve visual continuity.
    paths.get("/api/visual/state", {}).pop("post", None)
    # World Engine 4.0 routes narrative/world-event commits through resolveTurn; keep the low-level endpoint backend/MCP-only.
    paths.pop("/api/world/event", None)
    http_methods = {"get", "post", "put", "patch", "delete", "options", "head"}
    for path, methods in list(paths.items()):
        if not isinstance(methods, dict):
            continue
        for method, operation in list(methods.items()):
            if (
                method in http_methods
                and isinstance(operation, dict)
                and operation.get("operationId") not in PUBLIC_ACTION_OPERATION_IDS
            ):
                methods.pop(method, None)
        if not any(method in http_methods for method in methods):
            paths.pop(path, None)
    operation_count = sum(
        1
        for methods in paths.values()
        for operation in methods.values()
        if isinstance(operation, dict) and operation.get("operationId")
    )
    if operation_count > 30:
        raise RuntimeError(f"GPT Actions schema has {operation_count} operations; maximum is 30")
    mark_actions_non_consequential(schema)
    missing_object_properties = object_schema_paths_missing_properties(schema)
    if missing_object_properties:
        raise RuntimeError(f"OpenAI-incompatible object schemas remain: {missing_object_properties[:10]}")
    destination.write_text(json.dumps(schema, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return destination



def ensure_music_catalog() -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not MUSIC_CATALOG_PATH.exists():
        if MUSIC_TEMPLATE_PATH.exists():
            shutil.copyfile(MUSIC_TEMPLATE_PATH, MUSIC_CATALOG_PATH)
        else:
            MUSIC_CATALOG_PATH.write_text('{"version":1,"defaults":{"volume":55,"poll_seconds":2.0},"tracks":[]}\n', encoding="utf-8")
    return MUSIC_CATALOG_PATH

def local_health() -> bool:
    try:
        with urllib.request.urlopen(f"{LOCAL_URL}/health", timeout=1.5) as r:
            return r.status == 200
    except Exception:
        return False


def public_health(base_url: str) -> bool:
    """Verify the exact HTTPS endpoint that GPT Actions will call."""
    try:
        with urllib.request.urlopen(base_url.rstrip("/") + "/health", timeout=5.0) as r:
            return r.status == 200
    except Exception:
        return False


def api_key_fingerprint(api_key: str) -> str:
    """Non-secret fingerprint used to identify which launcher key a GPT should be configured with."""
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:12]


def authenticated_probe(base_url: str, api_key: str, timeout: float = 5.0) -> tuple[bool, int | None, str]:
    """Exercise a protected, non-mutating endpoint using the exact Bearer key expected by GPT Actions."""
    url = base_url.rstrip("/") + "/api/context?campaign_id=default&event_limit=1&entity_limit=1"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key}", "User-Agent": "WorldEngineLauncher/5.0.1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read(2048).decode("utf-8", errors="replace")
            return r.status == 200, int(r.status), body
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read(2048).decode("utf-8", errors="replace")
        except Exception:
            body = str(exc)
        return False, int(exc.code), body
    except Exception as exc:
        return False, None, f"{type(exc).__name__}: {exc}"


def schema_server_url(schema_path: Path) -> str | None:
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        servers = schema.get("servers") or []
        if servers and isinstance(servers[0], dict):
            return str(servers[0].get("url") or "").rstrip("/") or None
    except Exception:
        return None
    return None


def connection_diagnostics(public_url: str | None, api_key: str, schema_path: Path | None = None) -> dict:
    """Classify launcher-side Action connectivity without mutating campaign state."""
    local_auth_ok, local_status, local_body = authenticated_probe(LOCAL_URL, api_key, timeout=2.0)
    result = {
        "local_health": local_health(),
        "local_auth_ok": local_auth_ok,
        "local_auth_status": local_status,
        "local_auth_body": local_body[:500],
        "api_key_fingerprint": api_key_fingerprint(api_key),
        "public_url": public_url,
        "public_health": False,
        "public_auth_ok": False,
        "public_auth_status": None,
        "public_auth_body": "",
        "schema_server_url": None,
        "schema_matches_public_url": None,
    }
    if public_url:
        result["public_health"] = public_health(public_url)
        ok, status, body = authenticated_probe(public_url, api_key, timeout=7.0)
        result["public_auth_ok"] = ok
        result["public_auth_status"] = status
        result["public_auth_body"] = body[:500]
    if schema_path and schema_path.exists():
        server = schema_server_url(schema_path)
        result["schema_server_url"] = server
        if public_url and server:
            result["schema_matches_public_url"] = server == public_url.rstrip("/")
    return result


class Launcher(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("World Engine v5.0.1 — Action Connection Diagnostics")
        self.geometry("860x720")
        self.minsize(720, 560)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        self.config_data = load_config()
        self.api_key = ensure_api_key(self.config_data)
        self.admin_key = ensure_admin_key(self.config_data, self.api_key)
        self.server_proc: subprocess.Popen | None = None
        self.tunnel_proc: subprocess.Popen | None = None
        self.music_proc: subprocess.Popen | None = None
        _v397_cfg = load_permanent_config(DATA_DIR)
        self.public_url: str | None = str(_v397_cfg.get('public_url') or '').strip().rstrip('/') or None
        self.log_queue: queue.Queue[str] = queue.Queue()
        self.busy = False

        self.status_var = tk.StringVar(value="Ready")
        self.local_var = tk.StringVar(value=LOCAL_URL)
        self.public_var = tk.StringVar(value=self.public_url or "Not configured")
        self.db_var = tk.StringVar(value=str(DB_PATH))
        self.music_var = tk.StringVar(value="Stopped")

        self._build_ui()
        self.after(100, self._drain_logs)
        self.after(350, self.start_engine)

    def _build_ui(self) -> None:
        pad = {"padx": 10, "pady": 6}
        title = ttk.Label(self, text="World Engine v5.0.1", font=("Segoe UI", 18, "bold"))
        title.pack(anchor="w", padx=16, pady=(16, 2))
        ttk.Label(self, text="Persistent world runtime + stable permanent HTTPS endpoint for GPT Actions").pack(anchor="w", padx=16, pady=(0, 10))

        info = ttk.LabelFrame(self, text="Runtime")
        info.pack(fill="x", padx=16, pady=6)
        for row, (label, var) in enumerate([
            ("Status", self.status_var),
            ("Local URL", self.local_var),
            ("Public HTTPS", self.public_var),
            ("Music Player", self.music_var),
            ("Database", self.db_var),
        ]):
            ttk.Label(info, text=label + ":", width=16).grid(row=row, column=0, sticky="nw", **pad)
            ttk.Label(info, textvariable=var, wraplength=600).grid(row=row, column=1, sticky="nw", **pad)
        info.columnconfigure(1, weight=1)

        buttons = ttk.Frame(self)
        buttons.pack(fill="x", padx=16, pady=6)
        self.start_btn = ttk.Button(buttons, text="Start / Repair Engine", command=self.start_engine)
        self.start_btn.grid(row=0, column=0, padx=4, pady=4, sticky="ew")
        self.stop_btn = ttk.Button(buttons, text="Stop Engine", command=self.stop_engine)
        self.stop_btn.grid(row=0, column=1, padx=4, pady=4, sticky="ew")
        self.tunnel_btn = ttk.Button(buttons, text="Start / Repair HTTPS", command=self.start_tunnel_async)
        self.tunnel_btn.grid(row=0, column=2, padx=4, pady=4, sticky="ew")
        self.permanent_btn = ttk.Button(buttons, text="Automatic Setup / Repair", command=self.setup_permanent_endpoint)
        self.permanent_btn.grid(row=0, column=3, padx=4, pady=4, sticky="ew")
        self.stop_tunnel_btn = ttk.Button(buttons, text="Stop HTTPS", command=self.stop_tunnel)
        self.stop_tunnel_btn.grid(row=0, column=4, padx=4, pady=4, sticky="ew")
        for i in range(5):
            buttons.columnconfigure(i, weight=1)

        tools = ttk.Frame(self)
        tools.pack(fill="x", padx=16, pady=2)
        ttk.Button(tools, text="Copy API Key", command=lambda: self.copy_text(self.api_key, "API key copied")).grid(row=0, column=0, padx=4, pady=4, sticky="ew")
        ttk.Button(tools, text="Copy Public URL", command=self.copy_public).grid(row=0, column=1, padx=4, pady=4, sticky="ew")
        ttk.Button(tools, text="Open Action Schema", command=self.open_schema).grid(row=0, column=2, padx=4, pady=4, sticky="ew")
        ttk.Button(tools, text="Test Action Connection", command=self.test_action_connection).grid(row=0, column=3, padx=4, pady=4, sticky="ew")
        ttk.Button(tools, text="Open Save Folder", command=self.open_save_folder).grid(row=0, column=4, padx=4, pady=4, sticky="ew")
        for i in range(5):
            tools.columnconfigure(i, weight=1)

        music_tools = ttk.Frame(self)
        music_tools.pack(fill="x", padx=16, pady=2)
        ttk.Button(music_tools, text="Start Music Player", command=self.start_music).grid(row=0, column=0, padx=4, pady=4, sticky="ew")
        ttk.Button(music_tools, text="Stop Music Player", command=self.stop_music).grid(row=0, column=1, padx=4, pady=4, sticky="ew")
        ttk.Button(music_tools, text="Open Music Catalog", command=self.open_music_catalog).grid(row=0, column=2, padx=4, pady=4, sticky="ew")
        ttk.Button(music_tools, text="Music Setup Help", command=self.music_help).grid(row=0, column=3, padx=4, pady=4, sticky="ew")
        for i in range(4):
            music_tools.columnconfigure(i, weight=1)

        note = ttk.LabelFrame(self, text="How to use with your GPT")
        note.pack(fill="x", padx=16, pady=6)
        text = (
            "START_WORLD_ENGINE.bat automatically starts/repairs the backend, permanent HTTPS, schema, and authenticated connection tests.\n"
            "First ngrok setup only: the official dashboard opens; click Copy once. No paste field is used. World Engine captures/configures it automatically.\n"
            "Import openapi_actions_PERMANENT.json and paste the World Engine API key into GPT Bearer authentication once; private GPT settings cannot be edited locally.\n"
            "Later starts reuse the persistent database, API key, ngrok configuration, and stable hostname automatically.\n"
            "Music: click Enable Background Music once; context changes then switch tracks automatically."
        )
        ttk.Label(note, text=text, wraplength=760, justify="left").pack(fill="x", padx=10, pady=8)

        log_frame = ttk.LabelFrame(self, text="Log")
        log_frame.pack(fill="both", expand=True, padx=16, pady=(6, 16))
        self.log = tk.Text(log_frame, height=12, wrap="word", state="disabled", font=("Consolas", 9))
        self.log.pack(fill="both", expand=True, padx=6, pady=6)

    def post_log(self, msg: str) -> None:
        self.log_queue.put(msg.rstrip())

    def _drain_logs(self) -> None:
        try:
            while True:
                msg = self.log_queue.get_nowait()
                self.log.configure(state="normal")
                self.log.insert("end", f"{time.strftime('%H:%M:%S')}  {msg}\n")
                self.log.see("end")
                self.log.configure(state="disabled")
        except queue.Empty:
            pass
        self.after(100, self._drain_logs)

    def set_status(self, text: str) -> None:
        self.after(0, lambda: self.status_var.set(text))

    def set_busy(self, value: bool) -> None:
        self.busy = value
        state = "disabled" if value else "normal"
        self.after(0, lambda: self.start_btn.configure(state=state))

    def start_engine(self) -> None:
        if self.busy:
            return
        if local_health():
            auth_ok, auth_status, auth_body = authenticated_probe(LOCAL_URL, self.api_key, timeout=2.0)
            if auth_ok:
                self.status_var.set("RUNNING")
                self.post_log(f"World Engine is already responding on port 8000 and accepts this launcher's API key (fingerprint {api_key_fingerprint(self.api_key)}).")
                self.after(0, self.start_music)
                self.after(0, self.start_tunnel_async)
            else:
                self.status_var.set("PORT 8000 AUTH MISMATCH")
                self.post_log(
                    "P0 CONNECTION BLOCKER: port 8000 answers /health but rejects this launcher's Bearer key "
                    f"(status={auth_status}, body={auth_body[:200]}). An older/different World Engine process may still be running, "
                    "or launcher_config.json was not preserved during upgrade. Close the old process or restore the matching launcher_config.json, then Start / Repair Engine."
                )
                self.after(0, lambda: messagebox.showerror(
                    "World Engine API-key mismatch",
                    "Port 8000 is occupied by a World Engine-compatible service, but it does not accept this installation's API key.\n\n"
                    "Close any older World Engine process, or restore the launcher_config.json that belongs to the running campaign, then click Start / Repair Engine.\n\n"
                    f"This install's API-key fingerprint: {api_key_fingerprint(self.api_key)}",
                ))
            return
        self.set_busy(True)
        threading.Thread(target=self._start_engine_worker, daemon=True).start()

    def _start_engine_worker(self) -> None:
        try:
            self.set_status("Preparing Python environment…")
            py = venv_python()
            if not py.exists():
                self.post_log("Creating private Python environment (.venv)…")
                subprocess.run([sys.executable, "-m", "venv", str(VENV_DIR)], cwd=ROOT, check=True)
            self.set_status("Installing / checking dependencies…")
            self.post_log("Checking runtime dependencies…")
            subprocess.run([str(py), "-m", "pip", "install", "-r", str(ROOT / "requirements.txt"), "--disable-pip-version-check"], cwd=ROOT, check=True)

            env = os.environ.copy()
            env["WORLD_ENGINE_API_KEY"] = self.api_key
            env["WORLD_ENGINE_ADMIN_KEY"] = self.admin_key
            env["WORLD_ENGINE_DB"] = str(DB_PATH)
            env["WORLD_ENGINE_HOST"] = "127.0.0.1"
            env["PORT"] = "8000"
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
            self.server_proc = subprocess.Popen(
                [str(py), "app.py"], cwd=ROOT, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, creationflags=creationflags,
            )
            threading.Thread(target=self._stream_process, args=(self.server_proc, "API"), daemon=True).start()
            self.set_status("Starting API…")
            for _ in range(40):
                if local_health():
                    auth_ok, auth_status, auth_body = authenticated_probe(LOCAL_URL, self.api_key, timeout=3.0)
                    if not auth_ok:
                        raise RuntimeError(f"API became healthy but protected authentication failed: status={auth_status}, body={auth_body[:200]}")
                    self.set_status("RUNNING")
                    self.post_log(f"World Engine running at {LOCAL_URL}; protected authentication PASS (fingerprint {api_key_fingerprint(self.api_key)}).")
                    self.after(0, self.start_music)
                    self.after(0, self.start_tunnel_async)
                    return
                if self.server_proc.poll() is not None:
                    raise RuntimeError(f"API exited with code {self.server_proc.returncode}")
                time.sleep(0.25)
            raise RuntimeError("API did not become healthy within 10 seconds")
        except Exception as exc:
            self.set_status("ERROR")
            self.post_log(f"ERROR: {exc}")
            error = str(exc)
            self.after(
                0, lambda error=error: messagebox.showerror("World Engine", f"Could not start the engine.\n\n{error}\n\nSee the log for details.")
            )
        finally:
            self.set_busy(False)

    def _stream_process(self, proc: subprocess.Popen, label: str) -> None:
        if not proc.stdout:
            return
        for line in proc.stdout:
            self.post_log(f"[{label}] {line.rstrip()}")

    def stop_engine(self) -> None:
        self.stop_tunnel()
        self.stop_music()
        if self.server_proc and self.server_proc.poll() is None:
            self.post_log("Stopping World Engine…")
            self.server_proc.terminate()
            try:
                self.server_proc.wait(timeout=4)
            except subprocess.TimeoutExpired:
                self.server_proc.kill()
        self.server_proc = None
        self.status_var.set("STOPPED")

    def _ensure_cloudflared(self) -> Path:
        TOOLS_DIR.mkdir(parents=True, exist_ok=True)
        if CLOUDFLARED_PATH.exists():
            if cloudflared_hash_ok(CLOUDFLARED_PATH):
                return CLOUDFLARED_PATH
            CLOUDFLARED_PATH.unlink(missing_ok=True)
        self.set_status("Downloading HTTPS tunnel helper…")
        self.post_log(f"Downloading pinned Cloudflare Tunnel helper {CLOUDFLARED_VERSION}…")
        req = urllib.request.Request(CLOUDFLARED_URL, headers={"User-Agent": "WorldEngineLauncher/5.0.1"})
        with urllib.request.urlopen(req, timeout=60) as r, open(CLOUDFLARED_PATH, "wb") as out:
            shutil.copyfileobj(r, out)
        if not cloudflared_hash_ok(CLOUDFLARED_PATH):
            CLOUDFLARED_PATH.unlink(missing_ok=True)
            raise RuntimeError("cloudflared SHA-256 verification failed")
        return CLOUDFLARED_PATH

    def setup_permanent_endpoint(self) -> None:
        installer = ROOT / "INSTALL_PERMANENT_ENDPOINT.bat"
        if not installer.exists():
            messagebox.showerror("World Engine", f"Permanent endpoint installer is missing: {installer}")
            return
        if os.name == "nt":
            try:
                os.startfile(str(installer))
                self.post_log("Opened automatic setup/repair. If ngrok is not configured, its official dashboard opens: click Copy once. No paste box is used; World Engine captures the token, configures HTTPS, generates the schema, copies the World Engine API key, and tests the connection automatically.")
            except Exception as exc:
                messagebox.showerror("World Engine", f"Could not open permanent endpoint installer.\n\n{exc}")
        else:
            messagebox.showinfo("World Engine", "Permanent endpoint automatic setup is currently packaged for Windows. See PERMANENT_ENDPOINT_GUIDE.md for manual setup.")

    def start_temporary_tunnel_for_testing(self) -> None:
        if self.tunnel_proc and self.tunnel_proc.poll() is None:
            self.post_log("Temporary HTTPS tunnel is already running.")
            return
        if not local_health():
            messagebox.showinfo("World Engine", "Start the World Engine first.")
            return
        threading.Thread(target=self._start_tunnel_worker, daemon=True).start()

    def start_tunnel_async(self) -> None:
        if self.busy:
            return
        threading.Thread(target=self.start_tunnel, daemon=True).start()

    def start_tunnel(self) -> None:
        _v397 = load_permanent_config(DATA_DIR)
        _v397_url = str(_v397.get("public_url") or "").strip().rstrip("/")
        if _v397_url:
            if self.busy:
                return
            if not local_health():
                self.after(0, lambda: messagebox.showwarning(
                    "World Engine",
                    "Start the engine before checking the permanent HTTPS endpoint.",
                ))
                return
            self.public_url = _v397_url
            self.after(0, lambda: self.public_var.set(self.public_url or "Not configured"))
            self.post_log(f"Permanent HTTPS configured: {self.public_url}")
            if not public_health(self.public_url):
                self.set_status("RESTARTING PERMANENT HTTPS…")
                self.post_log("Permanent endpoint is unreachable. Attempting automatic provider restart before declaring failure…")
                repair = ensure_permanent_runtime(ROOT, data=DATA_DIR)
                self.post_log("Permanent endpoint repair: " + json.dumps(repair, ensure_ascii=False))
                healthy = False
                for _ in range(20):
                    if public_health(self.public_url):
                        healthy = True
                        break
                    time.sleep(0.5)
                if not healthy:
                    self.set_status("PERMANENT HTTPS UNREACHABLE")
                    self.post_log("Automatic endpoint restart failed. Run Permanent Endpoint Setup again; no Quick Tunnel replacement was created.")
                    return
            auth_ok, auth_status, auth_body = authenticated_probe(self.public_url, self.api_key, timeout=7.0)
            if not auth_ok:
                self.set_status("PERMANENT HTTPS AUTH FAILURE")
                self.post_log(f"Permanent endpoint reached the backend but protected auth failed: status={auth_status}, body={auth_body[:200]}")
                return
            schema_path = generate_action_schema(self.public_url, ROOT / "openapi_actions_PERMANENT.json")
            self.config_data["last_public_url"] = self.public_url
            save_config(self.config_data)
            self.post_log(f"Permanent public API PASS: {self.public_url}")
            self.post_log(f"Permanent GPT Actions schema: {schema_path.name}")
            self.post_log("This hostname is stable. Do not re-import the Action schema on normal restarts.")
            self.set_status("RUNNING + PERMANENT HTTPS")
            return
        self.set_status("AUTOMATIC ENDPOINT SETUP REQUIRED")
        self.post_log("Permanent HTTPS is not configured. Opening automatic no-paste setup now. Quick Tunnel is disabled as an automatic fallback because changing URLs break GPT Actions.")
        self.after(0, self.setup_permanent_endpoint)

    def _start_tunnel_worker(self) -> None:
        try:
            exe = self._ensure_cloudflared()
            self.set_status("Starting temporary HTTPS…")
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
            self.tunnel_proc = subprocess.Popen(
                [str(exe), "tunnel", "--url", LOCAL_URL, "--no-autoupdate"],
                cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, creationflags=creationflags,
            )
            if not self.tunnel_proc.stdout:
                raise RuntimeError("could not read cloudflared output")
            deadline = time.time() + 45
            for line in self.tunnel_proc.stdout:
                self.post_log(f"[HTTPS] {line.rstrip()}")
                match = TUNNEL_RE.search(line)
                if match and not self.public_url:
                    self.public_url = match.group(0)
                    self.after(0, lambda: self.public_var.set(self.public_url or "Not running"))
                    externally_healthy = False
                    for _ in range(12):
                        if public_health(self.public_url):
                            externally_healthy = True
                            break
                        time.sleep(0.5)
                    if not externally_healthy:
                        raise RuntimeError("temporary HTTPS URL was created but its /health endpoint is not externally reachable")
                    schema_path = generate_action_schema(self.public_url)
                    auth_ok, auth_status, auth_body = authenticated_probe(self.public_url, self.api_key, timeout=7.0)
                    if not auth_ok:
                        raise RuntimeError(f"temporary HTTPS reaches /health but protected API authentication failed: status={auth_status}, body={auth_body[:200]}")
                    previous_url = str(self.config_data.get("last_public_url") or "").strip()
                    self.config_data["last_public_url"] = self.public_url
                    self.config_data["api_key_fingerprint"] = api_key_fingerprint(self.api_key)
                    save_config(self.config_data)
                    self.post_log(f"Public HTTPS verified: {self.public_url}/health")
                    self.post_log(f"Public protected API verified with launcher Bearer key (fingerprint {api_key_fingerprint(self.api_key)}).")
                    self.post_log(f"Generated GPT Actions schema: {schema_path.name}")
                    if previous_url and previous_url.rstrip("/") != self.public_url.rstrip("/"):
                        self.post_log(f"ACTION REIMPORT REQUIRED: tunnel URL changed from {previous_url} to {self.public_url}.")
                    self.post_log("Keep this HTTPS tunnel running during play. If the public URL changes after a restart, re-import the newly generated Actions schema and ensure the GPT Action Bearer token matches Copy API Key.")
                    self.set_status("RUNNING + HTTPS")
                if time.time() > deadline and not self.public_url:
                    raise RuntimeError("temporary HTTPS URL was not produced within 45 seconds")
            if self.tunnel_proc.poll() is not None and self.public_url:
                self.post_log("HTTPS tunnel stopped.")
                self.public_url = None
                self.after(0, lambda: self.public_var.set("Not running"))
                if local_health():
                    self.set_status("RUNNING")
        except Exception as exc:
            self.post_log(f"HTTPS ERROR: {exc}")
            if local_health():
                self.set_status("RUNNING")
            error = str(exc)
            self.after(
                0, lambda error=error: messagebox.showerror("Temporary HTTPS", f"Could not start the temporary HTTPS tunnel.\n\n{error}")
            )

    def stop_tunnel(self) -> None:
        permanent = load_permanent_config(DATA_DIR)
        permanent_url = str(permanent.get("public_url") or "").strip().rstrip("/")
        if permanent_url and not (self.tunnel_proc and self.tunnel_proc.poll() is None):
            self.public_url = permanent_url
            self.public_var.set(permanent_url)
            self.post_log("Permanent HTTPS is provider-managed and remains configured. Use Tailscale/Cloudflare controls only if you intentionally want to disable public access.")
            if local_health():
                self.status_var.set("RUNNING + PERMANENT CONFIGURED")
            return
        if self.tunnel_proc and self.tunnel_proc.poll() is None:
            self.post_log("Stopping temporary HTTPS testing tunnel…")
            self.tunnel_proc.terminate()
            try:
                self.tunnel_proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.tunnel_proc.kill()
        self.tunnel_proc = None
        self.public_url = permanent_url or None
        self.public_var.set(self.public_url or "Not configured")
        if local_health():
            self.status_var.set("RUNNING")

    def start_music(self) -> None:
        if self.music_proc and self.music_proc.poll() is None:
            self.music_var.set("Running")
            return
        threading.Thread(target=self._start_music_worker, daemon=True).start()

    def _start_music_worker(self) -> None:
        try:
            ensure_music_catalog()
            py = venv_python()
            if not py.exists():
                self.post_log("Music player is waiting for the World Engine Python environment.")
                return
            self.after(0, lambda: self.music_var.set("Installing/checking…"))
            self.post_log("Checking pywebview music-player dependency…")
            subprocess.run([str(py), "-m", "pip", "install", "-r", str(ROOT / "requirements-music.txt"), "--disable-pip-version-check"], cwd=ROOT, check=True)
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
            self.music_proc = subprocess.Popen(
                [str(py), "music_player.py", "--db", str(DB_PATH), "--catalog", str(MUSIC_CATALOG_PATH), "--campaign", "default"],
                cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, creationflags=creationflags,
            )
            threading.Thread(target=self._stream_process, args=(self.music_proc, "MUSIC"), daemon=True).start()
            self.after(0, lambda: self.music_var.set("Running — enable audio once"))
            self.post_log("Music player started. Click Enable Background Music once in its visible window; track switching is automatic afterward.")
        except Exception as exc:
            self.after(0, lambda: self.music_var.set("Error"))
            self.post_log(f"MUSIC ERROR: {exc}")

    def stop_music(self) -> None:
        if self.music_proc and self.music_proc.poll() is None:
            self.post_log("Stopping music player…")
            self.music_proc.terminate()
            try:
                self.music_proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.music_proc.kill()
        self.music_proc = None
        try:
            self.music_var.set("Stopped")
        except Exception:
            pass

    def open_music_catalog(self) -> None:
        path = ensure_music_catalog()
        try:
            os.startfile(path)  # type: ignore[attr-defined]
        except AttributeError:
            subprocess.Popen(["xdg-open", str(path)])

    def music_help(self) -> None:
        messagebox.showinfo(
            "World Engine Music",
            "The music window uses a visible official YouTube embedded player.\n\n"
            "1. Click Enable Background Music once.\n"
            "2. Paste a YouTube URL in the music window.\n"
            "3. Choose Current location, Combat, Location combat, Scene type, Director/deity, or Fallback.\n"
            "4. Click Save Track for Context.\n\n"
            "World Engine then picks the highest-specificity matching track automatically whenever the game state changes."
        )

    def test_action_connection(self) -> None:
        threading.Thread(target=self._test_action_connection_worker, daemon=True).start()

    def _test_action_connection_worker(self) -> None:
        schema_path = ROOT / ("openapi_actions_PERMANENT.json" if (ROOT / "openapi_actions_PERMANENT.json").exists() else "openapi_actions_live.json")
        report = connection_diagnostics(self.public_url, self.api_key, schema_path)
        fp = report["api_key_fingerprint"]
        self.post_log(f"Action diagnostic: API-key fingerprint {fp}")
        self.post_log(f"Action diagnostic: local health={report['local_health']} protected-auth={report['local_auth_ok']} status={report['local_auth_status']}")
        if self.public_url:
            self.post_log(f"Action diagnostic: public health={report['public_health']} protected-auth={report['public_auth_ok']} status={report['public_auth_status']}")
            self.post_log(f"Action diagnostic: schema server={report['schema_server_url']!r} matches current tunnel={report['schema_matches_public_url']}")
        if not report["local_health"]:
            diagnosis = "LOCAL API DOWN — start/repair the engine first."
        elif not report["local_auth_ok"]:
            diagnosis = "LOCAL AUTH FAILURE — launcher/server key binding is inconsistent. Restart/repair the engine."
        elif not self.public_url:
            diagnosis = "NO PUBLIC HTTPS — run Automatic Setup / Repair."
        elif not report["public_health"]:
            diagnosis = "PUBLIC ENDPOINT UNREACHABLE — run Automatic Setup / Repair; the stable endpoint will be restarted and retested."
        elif not report["public_auth_ok"]:
            diagnosis = "PUBLIC AUTH FAILURE — tunnel reaches the engine but the protected request fails. Restart engine+tunnel."
        elif report["schema_matches_public_url"] is False:
            diagnosis = "STALE ACTION SCHEMA — the schema server does not match the configured endpoint. Re-import the current permanent/live schema."
        else:
            diagnosis = (
                "LAUNCHER SIDE PASS — public authenticated API is healthy. If GPT still reports ClientResponseError, "
                "re-import the current Action schema and replace the GPT Action Bearer token with Copy API Key. "
                f"Expected key fingerprint: {fp}."
            )
        self.post_log("Action diagnostic result: " + diagnosis)
        self.after(0, lambda: messagebox.showinfo("GPT Action Connection", diagnosis + f"\n\nAPI-key fingerprint: {fp}"))

    def copy_text(self, value: str, note: str) -> None:
        self.clipboard_clear()
        self.clipboard_append(value)
        self.update()
        self.post_log(note)

    def copy_public(self) -> None:
        if not self.public_url:
            messagebox.showinfo("World Engine", "Run Automatic Setup / Repair first.")
            return
        self.copy_text(self.public_url, "Public URL copied")

    def open_schema(self) -> None:
        path = ROOT / "openapi_actions_PERMANENT.json"
        if not path.exists():
            path = ROOT / "openapi_actions_live.json"
        if not path.exists():
            if not self.public_url:
                messagebox.showinfo("World Engine", "Run Automatic Setup / Repair first. The schema is generated after public health and Bearer authentication pass.")
                return
            path = generate_action_schema(self.public_url, ROOT / ("openapi_actions_PERMANENT.json" if load_permanent_config(DATA_DIR).get("public_url") else "openapi_actions_live.json"))
        try:
            os.startfile(path)  # type: ignore[attr-defined]
        except AttributeError:
            subprocess.Popen(["xdg-open", str(path)])

    def open_save_folder(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(DATA_DIR)  # type: ignore[attr-defined]
        except AttributeError:
            subprocess.Popen(["xdg-open", str(DATA_DIR)])

    def on_close(self) -> None:
        if messagebox.askokcancel("Quit", "Stop the local World Engine and close the launcher?\n\nYour campaign database will remain saved."):
            self.stop_engine()
            self.destroy()


if __name__ == "__main__":
    Launcher().mainloop()
