from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from world_engine_connection_guard import normalize_install_root
from typing import Any




def persistent_data_dir() -> Path:
    override = os.environ.get("WORLD_ENGINE_DATA_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
        return (Path(base) / "WorldEngine").resolve()
    xdg = os.environ.get("XDG_DATA_HOME", "").strip()
    if xdg:
        return (Path(xdg).expanduser() / "world-engine").resolve()
    return (Path.home() / ".local" / "share" / "world-engine").resolve()


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)

RUNTIME_FILE = "runtime_install.json"
BOOTSTRAP_COPY = "world_engine_autostart.py"
STARTUP_NAME = "WorldEngineBackend.vbs"


def local_health(timeout: float = 0.8) -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=timeout) as r:
            return int(r.status) == 200
    except Exception:
        return False


def runtime_path(data: Path | None = None) -> Path:
    return (data or persistent_data_dir()) / RUNTIME_FILE


def authorized_install_roots(
    current_root: Path | None = None,
    *,
    data: Path | None = None,
) -> tuple[Path, ...]:
    """Canonical roots whose World Engine processes may be reclaimed.

    The registry retains a short upgrade history so a newly extracted build
    can stop the immediately older detached backend without accepting an
    arbitrary Python project whose script merely happens to be named app.py.
    """
    payload = load_json(runtime_path(data))
    raw_roots: list[object] = []
    if current_root is not None:
        raw_roots.append(current_root)
    raw_roots.append(payload.get("install_root"))
    history = payload.get("authorized_install_roots")
    if isinstance(history, list):
        raw_roots.extend(history)
    roots: list[Path] = []
    seen: set[str] = set()
    for raw in raw_roots:
        if not isinstance(raw, (str, Path)) or not str(raw).strip():
            continue
        root = normalize_install_root(raw)
        key = os.path.normcase(str(root))
        if key not in seen:
            seen.add(key)
            roots.append(root)
    return tuple(roots[:8])


def register_current_install(root: Path, *, python_exe: str | None = None, data: Path | None = None) -> Path:
    root = normalize_install_root(root)
    data = data or persistent_data_dir()
    data.mkdir(parents=True, exist_ok=True)
    payload = load_json(runtime_path(data))
    roots = authorized_install_roots(root, data=data)
    payload.update({
        "version": 1,
        "install_root": str(root),
        "authorized_install_roots": [str(path) for path in roots],
        "python_exe": str(python_exe or sys.executable),
        "updated_at_unix": int(time.time()),
    })
    atomic_json(runtime_path(data), payload)
    return runtime_path(data)


def ensure_api_key(data: Path) -> str:
    cfg_path = data / "launcher_config.json"
    cfg = load_json(cfg_path)
    key = str(cfg.get("api_key") or "").strip()
    if len(key) < 24:
        key = secrets.token_urlsafe(32)
        cfg["api_key"] = key
        cfg.setdefault("created_by", "World Engine persistent autostart")
        atomic_json(cfg_path, cfg)
    return key


def start_backend_from_runtime(*, data: Path | None = None) -> dict[str, Any]:
    data = data or persistent_data_dir()
    data.mkdir(parents=True, exist_ok=True)
    if local_health():
        return {"status": "ALREADY_RUNNING", "data": str(data)}
    cfg = load_json(runtime_path(data))
    root = Path(str(cfg.get("install_root") or "")).expanduser()
    app = root / "app.py"
    if not app.exists():
        return {"status": "INSTALL_ROOT_MISSING", "install_root": str(root), "runtime_file": str(runtime_path(data))}
    python_exe = Path(str(cfg.get("python_exe") or sys.executable))
    if not python_exe.exists():
        python_exe = Path(sys.executable)
    api_key = ensure_api_key(data)
    env = os.environ.copy()
    env["WORLD_ENGINE_DATA_DIR"] = str(data)
    env["WORLD_ENGINE_DB"] = str(data / "world_engine.sqlite3")
    env["WORLD_ENGINE_API_KEY"] = api_key
    env["WORLD_ENGINE_HOST"] = "127.0.0.1"
    env["PORT"] = "8000"
    kwargs: dict[str, Any] = {
        "cwd": str(root),
        "env": env,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if os.name == "nt":
        kwargs["creationflags"] = (
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
    proc = subprocess.Popen([str(python_exe), str(app)], **kwargs)
    for _ in range(40):
        if local_health():
            return {"status": "STARTED", "pid": proc.pid, "install_root": str(root), "data": str(data)}
        if proc.poll() is not None:
            return {"status": "EXITED", "returncode": proc.returncode, "install_root": str(root)}
        time.sleep(0.25)
    return {"status": "STARTED_NOT_HEALTHY_YET", "pid": proc.pid, "install_root": str(root)}


def install_windows_autostart(root: Path, *, python_exe: str | None = None, data: Path | None = None) -> dict[str, Any]:
    if os.name != "nt":
        return {"status": "NOT_WINDOWS"}
    root = normalize_install_root(root)
    data = data or persistent_data_dir()
    data.mkdir(parents=True, exist_ok=True)
    register_current_install(root, python_exe=python_exe, data=data)
    # Copy this bootstrap into version-independent persistent storage. It imports
    # the connection guard from the current install, so add that directory to sys.path.
    persistent_script = data / BOOTSTRAP_COPY
    source = Path(__file__).resolve()
    script = source.read_text(encoding="utf-8")
    persistent_script.write_text(script, encoding="utf-8")
    startup_dir = Path(os.environ.get("APPDATA", str(Path.home()))) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    startup_dir.mkdir(parents=True, exist_ok=True)
    vbs = startup_dir / STARTUP_NAME
    py = str(python_exe or sys.executable).replace('"', '""')
    install_root = str(root).replace('"', '""')
    persistent = str(persistent_script).replace('"', '""')
    command = f'\"{py}\" \"{persistent}\" --boot'
    command_vbs = command.replace('"', '""')
    vbs.write_text(
        'Set WshShell = CreateObject("WScript.Shell")\n'
        f'WshShell.Run "{command_vbs}", 0, False\n',
        encoding="utf-8",
    )
    return {"status": "INSTALLED", "startup": str(vbs), "runtime": str(runtime_path(data)), "bootstrap": str(persistent_script)}


def main() -> int:
    if "--boot" in sys.argv:
        result = start_backend_from_runtime()
        # Persist a small diagnostic receipt without secrets.
        data = persistent_data_dir(); data.mkdir(parents=True, exist_ok=True)
        atomic_json(data / "last_autostart_result.json", result)
        return 0 if result.get("status") in {"STARTED", "ALREADY_RUNNING", "STARTED_NOT_HEALTHY_YET"} else 1
    root = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd().resolve()
    print(json.dumps(install_windows_autostart(root), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
