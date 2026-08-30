from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

DATA_FILES = ("world_engine.sqlite3", "launcher_config.json", "music_catalog.json")


def normalize_install_root(root: str | Path) -> Path:
    """Normalize an installation root passed through Windows batch/Python argv.

    Windows ``cmd.exe`` can preserve a literal closing quote when a quoted
    argument ends in the trailing backslash produced by ``%~dp0``.  Windows
    paths cannot legally contain a double-quote, so stripping accidental
    wrapping/trailing quote characters is safe and makes startup robust even
    when an older launcher invokes the Python entry point incorrectly.
    """
    raw = str(root).strip()
    while raw.startswith(('"', "'")):
        raw = raw[1:].lstrip()
    while raw.endswith(('"', "'")):
        raw = raw[:-1].rstrip()
    raw = os.path.expandvars(os.path.expanduser(raw or "."))
    return Path(raw).resolve()


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


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def migrate_legacy_data(legacy_data: Path, persistent: Path) -> dict[str, Any]:
    """Move installation-bound state to a version-independent directory.

    Existing persistent files always win. Legacy files are copied only when the
    persistent counterpart does not exist. Conflicting legacy files are retained
    in a timestamped migration-conflicts directory rather than overwritten.
    """
    legacy_data = Path(legacy_data)
    persistent = Path(persistent)
    persistent.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "persistent_data": str(persistent),
        "legacy_data": str(legacy_data),
        "copied": [],
        "already_persistent": [],
        "conflicts_preserved": [],
    }
    conflict_dir: Path | None = None
    for name in DATA_FILES:
        src = legacy_data / name
        dst = persistent / name
        if not src.exists():
            continue
        if not dst.exists():
            shutil.copy2(src, dst)
            report["copied"].append(name)
            continue
        try:
            same = src.stat().st_size == dst.stat().st_size and file_sha256(src) == file_sha256(dst)
        except OSError:
            same = False
        if same:
            report["already_persistent"].append(name)
            continue
        if conflict_dir is None:
            stamp = time.strftime("%Y%m%d-%H%M%S")
            conflict_dir = persistent / "migration-conflicts" / stamp
            conflict_dir.mkdir(parents=True, exist_ok=True)
        backup = conflict_dir / name
        shutil.copy2(src, backup)
        report["conflicts_preserved"].append(str(backup))
    return report



def auto_migrate_from_previous_install(root: Path, persistent: Path) -> dict[str, Any]:
    """Best-effort one-time migration from the newest sibling World Engine install.

    This runs only when the persistent store does not yet contain a campaign DB
    or launcher config. Existing persistent state always wins.
    """
    root = normalize_install_root(root)
    persistent = Path(persistent).resolve()
    if (persistent / "world_engine.sqlite3").exists() or (persistent / "launcher_config.json").exists():
        return {"status": "PERSISTENT_STATE_EXISTS", "source": None}
    parent = root.parent
    candidates: list[tuple[float, Path]] = []
    try:
        siblings = list(parent.iterdir())
    except OSError:
        siblings = []
    for sibling in siblings:
        if not sibling.is_dir() or sibling.resolve() == root:
            continue
        name = sibling.name.lower()
        if "world_engine" not in name and "world-engine" not in name:
            continue
        data = sibling / "data"
        if not data.is_dir():
            continue
        files = [data / n for n in DATA_FILES if (data / n).exists()]
        if not files:
            continue
        stamp = max(f.stat().st_mtime for f in files)
        candidates.append((stamp, data))
    if not candidates:
        return {"status": "NO_PREVIOUS_INSTALL_FOUND", "source": None}
    candidates.sort(key=lambda x: x[0], reverse=True)
    source = candidates[0][1]
    report = migrate_legacy_data(source, persistent)
    report.update({"status": "MIGRATED_PREVIOUS_INSTALL", "source": str(source)})
    return report

def install_environment(persistent: Path) -> None:
    persistent = Path(persistent)
    persistent.mkdir(parents=True, exist_ok=True)
    os.environ["WORLD_ENGINE_DATA_DIR"] = str(persistent)
    os.environ["WORLD_ENGINE_DB"] = str(persistent / "world_engine.sqlite3")


def load_json(path: Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else dict(default or {})
    except Exception:
        return dict(default or {})


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def ensure_guard_config(persistent: Path) -> dict[str, Any]:
    path = Path(persistent) / "connection_guard.json"
    cfg = load_json(path, {
        "version": 1,
        "mode": "quick",
        "stable_public_url": "",
        "stable_tunnel_token_env": "WORLD_ENGINE_CLOUDFLARE_TUNNEL_TOKEN",
        "require_action_reimport_ack": True,
    })
    cfg.setdefault("version", 1)
    cfg.setdefault("mode", "quick")
    cfg.setdefault("stable_public_url", "")
    cfg.setdefault("stable_tunnel_token_env", "WORLD_ENGINE_CLOUDFLARE_TUNNEL_TOKEN")
    cfg.setdefault("require_action_reimport_ack", True)
    atomic_json(path, cfg)
    return cfg


def action_reimport_marker(persistent: Path) -> Path:
    return Path(persistent) / "ACTION_REIMPORT_REQUIRED.txt"


def mark_action_reimport_required(persistent: Path, old_url: str, new_url: str) -> Path:
    marker = action_reimport_marker(persistent)
    marker.write_text(
        "WORLD ENGINE GPT ACTION REIMPORT REQUIRED\n\n"
        f"Old Action server: {old_url or '(none)'}\n"
        f"Current Action server: {new_url or '(none)'}\n\n"
        "The GPT editor stores the Action server URL. A Cloudflare Quick Tunnel URL is temporary.\n"
        "Re-import the newly generated openapi_actions_live.json before authoritative play.\n"
        "For zero-reimport operation, configure a stable hostname/tunnel in connection_guard.json.\n",
        encoding="utf-8",
    )
    return marker


def clear_action_reimport_required(persistent: Path) -> None:
    try:
        action_reimport_marker(persistent).unlink()
    except FileNotFoundError:
        pass


def normalized_url(value: str | None) -> str:
    return str(value or "").strip().rstrip("/")


def should_block_for_url_change(old_url: str | None, new_url: str | None, mode: str = "quick") -> bool:
    old = normalized_url(old_url)
    new = normalized_url(new_url)
    if str(mode).lower() == "stable":
        return bool(old and new and old != new)
    return bool(old and new and old != new)


def stable_endpoint_ready(cfg: dict[str, Any]) -> tuple[bool, str]:
    if str(cfg.get("mode", "quick")).lower() != "stable":
        return False, "connection_guard mode is not stable"
    url = normalized_url(cfg.get("stable_public_url"))
    if not url.startswith("https://"):
        return False, "stable_public_url must be an https:// URL"
    env_name = str(cfg.get("stable_tunnel_token_env") or "WORLD_ENGINE_CLOUDFLARE_TUNNEL_TOKEN")
    if not os.environ.get(env_name, "").strip():
        return False, f"missing tunnel token environment variable: {env_name}"
    return True, url
