from __future__ import annotations

import argparse
import hmac
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from world_engine_autostart import authorized_install_roots, register_current_install
from world_engine_connection_guard import (
    atomic_json,
    auto_migrate_from_previous_install,
    install_environment,
    migrate_legacy_data,
    normalize_install_root,
    persistent_data_dir,
)
from world_engine_permanent_endpoint import (
    NGROK_PROVIDER,
    api_key_fingerprint,
    configure_ngrok_authtoken,
    download_portable_ngrok_windows,
    ensure_permanent_runtime,
    find_ngrok,
    load_json,
    load_permanent_config,
    ngrok_config_path,
    probe,
    run_ngrok_command,
    save_permanent_config,
    start_ngrok_user_endpoint,
    verify_endpoint,
    write_permanent_schema,
)

VERSION = "5.1.0"
LOCAL_URL = "http://127.0.0.1:8000"
AUTHTOKEN_URL = "https://dashboard.ngrok.com/get-started/your-authtoken"
TOKEN_ENV_VARS = ("WORLD_ENGINE_NGROK_AUTHTOKEN", "NGROK_AUTHTOKEN")
TOKEN_RE = re.compile(r"^[A-Za-z0-9_.=-]{20,512}$")
CONFIG_TOKEN_RE = re.compile(r"(?mi)^\s*authtoken\s*:\s*['\"]?([^'\"\s#]+)")
_CLIPBOARD_READ_HOST_FAILURES: dict[str, float] = {}
_CLIPBOARD_WRITE_HOST_FAILURES: dict[str, float] = {}
_CLIPBOARD_HOST_RETRY_SECONDS = 30.0
_CLIPBOARD_HELPER_TIMEOUT_SECONDS = 5.0
_TK_CLIPBOARD_READ_SCRIPT = """
import sys
import tkinter as tk
sys.stdout.reconfigure(encoding="utf-8")
root = tk.Tk()
root.withdraw()
root.update()
try:
    sys.stdout.write(str(root.clipboard_get()))
finally:
    root.destroy()
"""
_TK_CLIPBOARD_WRITE_SCRIPT = """
import sys
import tkinter as tk
sys.stdin.reconfigure(encoding="utf-8")
value = sys.stdin.read()
root = tk.Tk()
root.withdraw()
try:
    root.clipboard_clear()
    root.clipboard_append(value)
    root.update()
finally:
    root.destroy()
"""


from world_engine.process_guard import (
    is_api_key_rejection,
    reclaim_stale_backend,
    terminate_owned_process_tree,
    world_engine_health_ok,
)


class StartupError(RuntimeError):
    pass


class EndpointStatus(str, Enum):
    """Public, non-secret state of the optional GPT endpoint."""

    READY = "READY"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    INSTALL_REQUIRED = "INSTALL_REQUIRED"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
    UNAVAILABLE = "UNAVAILABLE"
    FAILED = "FAILED"


@dataclass(frozen=True)
class EndpointOutcome:
    status: EndpointStatus
    provider: str | None = None
    public_url: str | None = None
    schema: str | None = None
    error_code: str | None = None
    message: str | None = None
    retryable: bool = True

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "status": self.status.value,
            "provider": self.provider,
            "public_url": self.public_url,
            "schema": self.schema,
            "retryable": self.retryable,
        }
        if self.error_code:
            result["error_code"] = self.error_code
        if self.message:
            result["message"] = self.message
        return result


class EndpointError(StartupError):
    endpoint_status = EndpointStatus.FAILED
    error_code = "ENDPOINT_FAILED"
    public_message = "The optional GPT endpoint could not be prepared."
    retryable = True


class EndpointAuthRequired(EndpointError):
    endpoint_status = EndpointStatus.AUTH_REQUIRED
    error_code = "NGROK_AUTH_REQUIRED"
    public_message = "ngrok authentication is required; the local engine and desktop remain ready."


class EndpointAuthTimeout(EndpointAuthRequired):
    error_code = "NGROK_AUTH_TIMEOUT"
    public_message = "Timed out waiting for the one-time ngrok Copy action; retry from the desktop when ready."


class EndpointAuthInvalid(EndpointAuthRequired):
    error_code = "NGROK_AUTH_INVALID"
    public_message = "The supplied ngrok token was rejected."


class EndpointInstallRequired(EndpointError):
    endpoint_status = EndpointStatus.INSTALL_REQUIRED
    error_code = "NGROK_INSTALL_REQUIRED"
    public_message = "The trusted Microsoft Store ngrok package is required."


class EndpointRecoveryRequired(EndpointError):
    endpoint_status = EndpointStatus.RECOVERY_REQUIRED
    error_code = "ENDPOINT_RECOVERY_REQUIRED"
    public_message = "The configured endpoint provider requires repair; no cross-provider fallback was attempted."


class EndpointUnavailable(EndpointError):
    endpoint_status = EndpointStatus.UNAVAILABLE
    error_code = "ENDPOINT_UNAVAILABLE"
    public_message = "The optional GPT endpoint is currently unavailable."


class EndpointVerificationFailed(EndpointError):
    endpoint_status = EndpointStatus.FAILED
    error_code = "ENDPOINT_VERIFICATION_FAILED"
    public_message = "The public endpoint did not pass authenticated verification."


def _creationflags() -> int:
    if os.name != "nt":
        return 0
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def run_text(command: list[str], *, timeout: float = 60.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
        creationflags=_creationflags(),
        check=False,
    )


def _tk_clipboard_read_bounded() -> str:
    try:
        cp = subprocess.run(
            [sys.executable, "-c", _TK_CLIPBOARD_READ_SCRIPT],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_CLIPBOARD_HELPER_TIMEOUT_SECONDS,
            creationflags=_creationflags(),
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return cp.stdout if cp.returncode == 0 else ""


def _tk_clipboard_write_bounded(value: str) -> bool:
    try:
        cp = subprocess.run(
            [sys.executable, "-c", _TK_CLIPBOARD_WRITE_SCRIPT],
            input=value,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_CLIPBOARD_HELPER_TIMEOUT_SECONDS,
            creationflags=_creationflags(),
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return cp.returncode == 0


def clipboard_read() -> str:
    """Read the current user's clipboard without requiring a paste control.

    Clipboard owners can become temporarily unresponsive on Windows. Keep this
    best-effort read bounded so credential discovery can continue polling instead
    of aborting the entire startup sequence.
    """
    if os.name == "nt":
        attempted: set[str] = set()
        for executable in ("powershell.exe", "pwsh.exe", "powershell", "pwsh"):
            resolved = shutil.which(executable)
            if not resolved:
                continue
            identity = os.path.normcase(os.path.abspath(resolved))
            if identity in attempted:
                continue
            if _CLIPBOARD_READ_HOST_FAILURES.get(identity, 0.0) > time.monotonic():
                continue
            _CLIPBOARD_READ_HOST_FAILURES.pop(identity, None)
            attempted.add(identity)
            try:
                cp = run_text(
                    [resolved, "-Sta", "-NoProfile", "-NonInteractive", "-Command", "Get-Clipboard -Raw"],
                    timeout=5,
                )
            except (OSError, subprocess.SubprocessError):
                _CLIPBOARD_READ_HOST_FAILURES[identity] = time.monotonic() + _CLIPBOARD_HOST_RETRY_SECONDS
                continue
            if cp.returncode == 0:
                return cp.stdout.rstrip("\r\n")
            _CLIPBOARD_READ_HOST_FAILURES[identity] = time.monotonic() + _CLIPBOARD_HOST_RETRY_SECONDS
        return _tk_clipboard_read_bounded()
    try:
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        root.update()
        try:
            return str(root.clipboard_get()).strip()
        finally:
            root.destroy()
    except Exception:
        return ""


def clipboard_write(value: str) -> bool:
    value = str(value)
    if os.name == "nt":
        attempted: set[str] = set()
        for executable in ("powershell.exe", "pwsh.exe", "powershell", "pwsh"):
            resolved = shutil.which(executable)
            if not resolved:
                continue
            identity = os.path.normcase(os.path.abspath(resolved))
            if identity in attempted:
                continue
            if _CLIPBOARD_WRITE_HOST_FAILURES.get(identity, 0.0) > time.monotonic():
                continue
            _CLIPBOARD_WRITE_HOST_FAILURES.pop(identity, None)
            attempted.add(identity)
            try:
                cp = subprocess.run(
                    [resolved, "-Sta", "-NoProfile", "-NonInteractive", "-Command", "Set-Clipboard -Value ([Console]::In.ReadToEnd())"],
                    input=value,
                    text=True,
                    capture_output=True,
                    timeout=5,
                    creationflags=_creationflags(),
                    check=False,
                )
            except (OSError, subprocess.SubprocessError):
                _CLIPBOARD_WRITE_HOST_FAILURES[identity] = time.monotonic() + _CLIPBOARD_HOST_RETRY_SECONDS
                continue
            if cp.returncode == 0:
                return True
            _CLIPBOARD_WRITE_HOST_FAILURES[identity] = time.monotonic() + _CLIPBOARD_HOST_RETRY_SECONDS
        return _tk_clipboard_write_bounded(value)
    try:
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        root.clipboard_clear()
        root.clipboard_append(value)
        root.update()
        root.destroy()
        return True
    except Exception:
        return False


def token_candidate(value: str | None) -> str | None:
    token = str(value or "").strip()
    if not TOKEN_RE.fullmatch(token):
        return None
    if "://" in token or token.lower().startswith(("ngrok ", "http")):
        return None
    return token


def read_ngrok_authtoken(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    match = CONFIG_TOKEN_RE.search(text)
    return token_candidate(match.group(1)) if match else None


def config_contains_authtoken(path: Path) -> bool:
    return read_ngrok_authtoken(path) is not None


def validate_ngrok_config(ngrok: str, path: Path) -> tuple[bool, str]:
    if not path.is_file() or not config_contains_authtoken(path):
        return False, "configuration has no usable authtoken"
    try:
        cp = run_ngrok_command(ngrok, ["config", "check", "--config", str(path)], timeout=30)
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        return False, f"verified ngrok config check failed: {type(exc).__name__}"
    message = ((cp.stdout or "") + "\n" + (cp.stderr or "")).strip()
    return cp.returncode == 0, message[-1000:]


def default_ngrok_config_candidates(data: Path) -> list[Path]:
    candidates: list[Path] = [ngrok_config_path(data)]
    configured = os.environ.get("NGROK_CONFIG", "").strip()
    if configured:
        candidates.append(Path(configured).expanduser())
    home = Path.home()
    candidates.extend([
        home / "AppData" / "Local" / "ngrok" / "ngrok.yml",
        home / "AppData" / "Local" / "ngrok" / "ngrok.yaml",
        home / ".config" / "ngrok" / "ngrok.yml",
        home / ".config" / "ngrok" / "ngrok.yaml",
        home / ".ngrok2" / "ngrok.yml",
    ])
    local = os.environ.get("LOCALAPPDATA", "").strip()
    if local:
        candidates.extend([Path(local) / "ngrok" / "ngrok.yml", Path(local) / "ngrok" / "ngrok.yaml"])
    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = os.path.normcase(str(candidate.expanduser().resolve(strict=False)))
        if key not in seen:
            seen.add(key)
            unique.append(candidate.expanduser())
    return unique


def adopt_existing_ngrok_config(ngrok: str, data: Path) -> dict[str, Any] | None:
    destination = ngrok_config_path(data)
    for candidate in default_ngrok_config_candidates(data):
        token = read_ngrok_authtoken(candidate)
        if not token:
            continue
        ok, detail = validate_ngrok_config(ngrok, candidate)
        if not ok:
            continue
        if candidate.resolve(strict=False) != destination.resolve(strict=False):
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                backup = destination.with_name(f"{destination.name}.pre-v4-{int(time.time())}.bak")
                shutil.copy2(destination, backup)
            # Import only the allowlisted credential into a World Engine-owned
            # minimal config; do not inherit proxy, server, or web-interface fields.
            configure_ngrok_authtoken(ngrok, token, data=data)
            ok, detail = validate_ngrok_config(ngrok, destination)
            if not ok:
                raise StartupError(f"copied ngrok configuration failed validation: {detail}")
        return {"status": "EXISTING_CONFIG", "path": str(destination), "source": str(candidate)}
    return None


def acquire_ngrok_token_from_clipboard(
    *,
    timeout_seconds: int = 600,
    rejected_fingerprints: set[str] | None = None,
    read_clipboard: Callable[[], str] = clipboard_read,
    open_browser: Callable[[str], Any] = webbrowser.open,
    sleep: Callable[[float], None] = time.sleep,
    status: Callable[[str], None] = print,
) -> str:
    """Open ngrok's official dashboard and capture the token after Copy is clicked.

    No paste box is used. The full token cannot be retrieved from ngrok after its
    one-time display, so the account owner must complete the browser login/copy
    boundary once. Subsequent launches reuse the persistent ngrok configuration.
    """
    rejected = set(rejected_fingerprints or set())
    current = read_clipboard()
    candidate = token_candidate(current)
    if candidate and api_key_fingerprint(candidate) not in rejected:
        status("[5.1.0] Found an ngrok-token-shaped value already on the clipboard; validating it without displaying it.")
        return candidate
    baseline = current
    status("[5.1.0] Opening the official ngrok authtoken page.")
    status("[5.1.0] Sign in if needed and press the dashboard Copy button. Do not paste into this window.")
    try:
        open_browser(AUTHTOKEN_URL)
    except Exception:
        pass
    deadline = time.monotonic() + max(30, int(timeout_seconds))
    last = baseline
    while time.monotonic() < deadline:
        value = read_clipboard()
        if value != last:
            last = value
            candidate = token_candidate(value)
            if candidate and api_key_fingerprint(candidate) not in rejected:
                status("[5.1.0] Authtoken captured from the clipboard; configuring ngrok securely.")
                return candidate
        sleep(0.5)
    raise EndpointAuthTimeout(
        "Timed out waiting for the ngrok Copy action. Re-run START_WORLD_ENGINE.bat, open the official authtoken page, and click Copy."
    )


def ensure_launcher_config(data: Path) -> tuple[str, bool]:
    path = data / "launcher_config.json"
    cfg = load_json(path)
    key = str(cfg.get("api_key") or "").strip()
    created = False
    if len(key) < 24:
        key = secrets.token_urlsafe(32)
        cfg["api_key"] = key
        cfg["created_by"] = f"World Engine {VERSION} automatic startup"
        created = True
    admin_key = str(cfg.get("admin_key") or "").strip()
    if len(admin_key) < 24 or secrets.compare_digest(admin_key, key):
        admin_key = secrets.token_urlsafe(32)
        cfg["admin_key"] = admin_key
        cfg["admin_key_created_by"] = f"World Engine {VERSION} automatic startup"
    cfg["engine_version"] = VERSION
    cfg["api_key_fingerprint"] = api_key_fingerprint(key)
    cfg["admin_key_fingerprint"] = api_key_fingerprint(admin_key)
    cfg["updated_at_unix"] = int(time.time())
    atomic_json(path, cfg)
    return key, created


def venv_python(root: Path) -> Path:
    return root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def ensure_runtime_python(root: Path, status: Callable[[str], None] = print) -> Path:
    py = venv_python(root)
    if not py.exists():
        status("[5.1.0] Creating the private Python runtime...")
        subprocess.run([sys.executable, "-m", "venv", str(root / ".venv")], check=True)
    check = run_text([str(py), "-c", "import fastapi,pydantic,uvicorn,webview"], timeout=30)
    if check.returncode != 0:
        status("[5.1.0] Installing/checking World Engine runtime dependencies...")
        cp = subprocess.run(
            [str(py), "-m", "pip", "install", "-r", str(root / "requirements.txt"), "--disable-pip-version-check"],
            cwd=root,
            check=False,
        )
        if cp.returncode != 0:
            raise StartupError(f"dependency installation failed with exit code {cp.returncode}")
    return py


def local_health(timeout: float = 1.5) -> bool:
    """True only when the port answers with the World Engine health identity.

    A bare 200 proves only that *something* is listening; the reclaim path below
    decides whether a process may be terminated, so identity has to be exact.
    """
    return world_engine_health_ok(LOCAL_URL + "/health", timeout)


def start_backend(root: Path, data: Path, api_key: str, python_exe: Path, *, status: Callable[[str], None] = print) -> dict[str, Any]:
    protected = LOCAL_URL + "/api/context?campaign_id=default&event_limit=1&entity_limit=1"
    if local_health():
        auth_ok, auth_status, auth_body = probe(protected, api_key=api_key, timeout=3)
        if auth_ok:
            return {"status": "ALREADY_RUNNING", "auth_status": auth_status}
        if not is_api_key_rejection(auth_status, auth_body):
            raise StartupError(
                "Port 8000 answers as World Engine, but protected verification did not return the "
                "authoritative wrong-key response; automatic termination was not attempted. "
                f"Protected status={auth_status}; detail: {auth_body[:200]}"
            )
        # A World Engine answers but rejects this installation's key: it is a
        # backend left detached by an earlier launcher/companion session. Stop
        # it - but only after the identity gates in process_guard prove it is
        # ours. If any check is ambiguous nothing is killed and we surface the
        # original manual instruction rather than guessing.
        status("[5.1.0] Port 8000 holds a stale World Engine; verifying before reclaiming...")
        report = reclaim_stale_backend(
            8000,
            authorized_roots=authorized_install_roots(root, data=data),
        )
        if not report.reclaimed:
            # P0 gate: a refusal must state plainly that nothing was killed,
            # so the operator never has to infer it from the absence of a claim.
            action = (
                "No process was terminated."
                if not report.graceful_attempted and not report.force_attempted
                else "Termination was attempted but port release was not confirmed; "
                     "no process was confirmed terminated."
            )
            raise StartupError(
                "Port 8000 is occupied by a World Engine-compatible process using a different API key, "
                f"and it could not be safely reclaimed ({report.reason}). {action} "
                f"Close the older process and retry. Protected status={auth_status}; detail: {auth_body[:200]}"
            )
        status(f"[5.1.0] {report.reason}; starting this installation.")
    env = os.environ.copy()
    admin_key = str(load_json(data / "launcher_config.json").get("admin_key") or "").strip()
    if len(admin_key) < 24 or secrets.compare_digest(admin_key, api_key):
        raise StartupError("launcher_config.json is missing a distinct secure operator key; rerun startup configuration")
    env.update({
        "WORLD_ENGINE_DATA_DIR": str(data),
        "WORLD_ENGINE_DB": str(data / "world_engine.sqlite3"),
        "WORLD_ENGINE_API_KEY": api_key,
        "WORLD_ENGINE_ADMIN_KEY": admin_key,
        "WORLD_ENGINE_HOST": "127.0.0.1",
        "PORT": "8000",
    })
    logs = data / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    log_file = (logs / "world_engine_api.log").open("a", encoding="utf-8")
    kwargs: dict[str, Any] = {
        "cwd": str(root), "env": env, "stdin": subprocess.DEVNULL,
        "stdout": log_file, "stderr": subprocess.STDOUT,
    }
    if os.name == "nt":
        kwargs["creationflags"] = (
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
    try:
        process = subprocess.Popen([str(python_exe), str(root / "app.py")], **kwargs)
    finally:
        log_file.close()
    try:
        for _ in range(80):
            if local_health():
                auth_ok, auth_status, auth_body = probe(protected, api_key=api_key, timeout=3)
                if auth_ok:
                    return {"status": "STARTED", "pid": process.pid, "auth_status": auth_status}
                raise StartupError(f"World Engine started but protected auth failed: {auth_status} {auth_body[:200]}")
            if process.poll() is not None:
                raise StartupError(f"World Engine backend exited with code {process.returncode}; inspect {logs / 'world_engine_api.log'}")
            time.sleep(0.25)
        raise StartupError("World Engine backend did not become healthy within 20 seconds")
    except Exception:
        if process.poll() is None:
            terminate_owned_process_tree(process)
        raise


def _configure_from_environment(ngrok: str, data: Path) -> dict[str, Any] | None:
    for name in TOKEN_ENV_VARS:
        token = token_candidate(os.environ.get(name))
        if token:
            configure_ngrok_authtoken(ngrok, token, data=data)
            return {"status": "ENV_TOKEN", "environment_variable": name, "path": str(ngrok_config_path(data))}
    return None


def ensure_ngrok_authentication(
    ngrok: str,
    data: Path,
    *,
    interactive: bool = True,
    clipboard_timeout: int = 600,
    status: Callable[[str], None] = print,
) -> dict[str, Any]:
    adopted = adopt_existing_ngrok_config(ngrok, data)
    if adopted:
        return adopted
    configured = _configure_from_environment(ngrok, data)
    if configured:
        ok, detail = validate_ngrok_config(ngrok, ngrok_config_path(data))
        if not ok:
            raise EndpointAuthInvalid("ngrok environment token created an invalid configuration")
        return configured
    if not interactive:
        raise EndpointAuthRequired(
            "ngrok authentication is not configured. Set NGROK_AUTHTOKEN once or run START_WORLD_ENGINE.bat interactively."
        )
    attempted: set[str] = set()
    for _ in range(5):
        token = acquire_ngrok_token_from_clipboard(
            timeout_seconds=clipboard_timeout, rejected_fingerprints=attempted, status=status,
        )
        fingerprint = api_key_fingerprint(token)
        if fingerprint in attempted:
            raise EndpointAuthInvalid("The same rejected clipboard token was captured again.")
        attempted.add(fingerprint)
        configure_ngrok_authtoken(ngrok, token, data=data)
        ok, detail = validate_ngrok_config(ngrok, ngrok_config_path(data))
        if ok:
            return {"status": "CLIPBOARD_TOKEN", "path": str(ngrok_config_path(data)), "token_fingerprint": fingerprint}
        status(f"[5.1.0] Copied token did not produce a valid ngrok configuration: {detail}")
    raise EndpointAuthInvalid("Could not configure ngrok from the copied authtoken")


def install_ngrok_from_config(
    root: Path, data: Path, api_key: str, ngrok: str, *, expected_url: str | None = None,
) -> dict[str, Any]:
    result = start_ngrok_user_endpoint(ngrok, data=data, expected_url=expected_url)
    url = str(result["public_url"]).rstrip("/")
    verification = verify_endpoint(url, api_key, attempts=45, delay=1.0)
    if not verification.get("health_ok") or not verification.get("protected_auth_ok"):
        raise EndpointVerificationFailed("public endpoint did not pass authenticated verification")
    schema = write_permanent_schema(root, url, data=data)
    config = save_permanent_config(
        NGROK_PROVIDER,
        url,
        api_key,
        data=data,
        extra={
            "ngrok_exe": ngrok,
            "ngrok_config": str(ngrok_config_path(data)),
            "startup_mode": "user_login",
            "requires_admin": False,
            "assigned_dev_domain": True,
            "automatic_startup": True,
        },
    )
    return {
        "status": EndpointStatus.READY.value, "provider": NGROK_PROVIDER, "public_url": url,
        "schema": str(schema), "config": str(config), "verification": verification,
    }


def ensure_endpoint(
    root: Path,
    data: Path,
    api_key: str,
    *,
    interactive: bool = True,
    allow_download: bool = True,
    clipboard_timeout: int = 600,
    status: Callable[[str], None] = print,
) -> dict[str, Any]:
    existing = load_permanent_config(data)
    expected_url = str(existing.get("public_url") or "").strip().rstrip("/") or None
    existing_provider = str(existing.get("provider") or "").strip()
    if expected_url:
        status("[5.1.0] Reusing the configured permanent endpoint...")
        try:
            repair = ensure_permanent_runtime(root, data=data)
        except Exception as exc:
            if existing_provider != NGROK_PROVIDER:
                raise EndpointRecoveryRequired(
                    "the configured non-ngrok provider could not be recovered"
                ) from exc
            raise EndpointUnavailable("the configured ngrok runtime could not be recovered") from exc
        verification = verify_endpoint(expected_url, api_key, attempts=20, delay=0.5)
        if verification.get("health_ok") and verification.get("protected_auth_ok"):
            schema = write_permanent_schema(root, str(existing["public_url"]), data=data)
            return {
                "status": EndpointStatus.READY.value, "provider": existing.get("provider"),
                "public_url": expected_url, "schema": str(schema),
                "verification": verification, "repair": repair, "reused": True,
            }
        if existing_provider != NGROK_PROVIDER:
            provider_label = repr(existing_provider) if existing_provider else "<missing>"
            raise EndpointRecoveryRequired(
                f"configured permanent provider {provider_label} did not recover at {expected_url}; "
                "refusing to replace or impersonate that hostname with ngrok. "
                "Repair the configured provider and retry."
            )
        status("[5.1.0] Existing ngrok endpoint did not recover; validating its local ngrok configuration before repair.")
    ngrok = find_ngrok()
    if not ngrok and allow_download:
        ngrok = download_portable_ngrok_windows()
    if not ngrok:
        raise EndpointInstallRequired(
            "Verified Microsoft Store ngrok is unavailable and automatic Store installation "
            "is disabled or unsupported"
        )
    auth = ensure_ngrok_authentication(
        ngrok, data, interactive=interactive, clipboard_timeout=clipboard_timeout, status=status,
    )
    installed = install_ngrok_from_config(root, data, api_key, ngrok, expected_url=expected_url)
    installed["authentication"] = auth
    installed["reused"] = False
    return installed


def _restore_ngrok_config(path: Path, previous: bytes | None) -> bool:
    """Best-effort rollback after a rejected one-time credential update."""
    try:
        if previous is None:
            path.unlink(missing_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(previous)
        return True
    except OSError:
        return False


def _clear_clipboard_if_matches(secret: str) -> None:
    """Clear only the captured secret; never clobber newer clipboard content."""
    try:
        current = str(clipboard_read() or "").strip()
        if current and hmac.compare_digest(current, secret):
            clipboard_write("")
    except Exception:
        pass


def configure_ngrok_token_once(token: str) -> dict[str, Any]:
    """Configure one UI-supplied ngrok token without exposing it in the result.

    This is intentionally a closed bridge: the caller cannot choose an
    executable, config path, command, or provider. Only the trusted Store alias
    discovered by ``find_ngrok`` is used. A rejected update restores the prior
    config, and the clipboard is cleared only when it still contains this exact
    token so unrelated user clipboard data is never destroyed.
    """
    candidate = token_candidate(token)
    if candidate is None:
        return EndpointOutcome(
            EndpointStatus.AUTH_REQUIRED,
            provider=NGROK_PROVIDER,
            error_code=EndpointAuthInvalid.error_code,
            message=EndpointAuthInvalid.public_message,
        ).as_dict()
    data = persistent_data_dir()
    data.mkdir(parents=True, exist_ok=True)
    ngrok = find_ngrok()
    if not ngrok:
        _clear_clipboard_if_matches(candidate)
        return EndpointOutcome(
            EndpointStatus.INSTALL_REQUIRED,
            provider=NGROK_PROVIDER,
            error_code=EndpointInstallRequired.error_code,
            message=EndpointInstallRequired.public_message,
        ).as_dict()
    config_path = ngrok_config_path(data)
    try:
        previous = config_path.read_bytes() if config_path.is_file() else None
    except OSError:
        previous = None
    try:
        configure_ngrok_authtoken(ngrok, candidate, data=data)
        valid, _detail = validate_ngrok_config(ngrok, config_path)
    except Exception:
        restored = _restore_ngrok_config(config_path, previous)
        _clear_clipboard_if_matches(candidate)
        return EndpointOutcome(
            EndpointStatus.FAILED,
            provider=NGROK_PROVIDER,
            error_code="NGROK_AUTH_CONFIGURATION_FAILED" if restored else "NGROK_CONFIG_ROLLBACK_FAILED",
            message=(
                "ngrok authentication could not be configured."
                if restored
                else "ngrok authentication failed and its prior config could not be restored."
            ),
        ).as_dict()
    if not valid:
        restored = _restore_ngrok_config(config_path, previous)
        _clear_clipboard_if_matches(candidate)
        if not restored:
            return EndpointOutcome(
                EndpointStatus.FAILED,
                provider=NGROK_PROVIDER,
                error_code="NGROK_CONFIG_ROLLBACK_FAILED",
                message="The rejected token could not be rolled back safely.",
            ).as_dict()
        return EndpointOutcome(
            EndpointStatus.AUTH_REQUIRED,
            provider=NGROK_PROVIDER,
            error_code=EndpointAuthInvalid.error_code,
            message=EndpointAuthInvalid.public_message,
        ).as_dict()
    fingerprint = api_key_fingerprint(candidate)
    _clear_clipboard_if_matches(candidate)
    return {
        "status": EndpointStatus.READY.value,
        "provider": NGROK_PROVIDER,
        "token_fingerprint": fingerprint,
        "retryable": False,
    }


def ensure_endpoint_outcome(
    root: Path,
    data: Path,
    api_key: str,
    *,
    interactive: bool = True,
    allow_download: bool = True,
    clipboard_timeout: int = 600,
    status: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Return a typed endpoint outcome; never let endpoint setup kill local use."""
    try:
        configured = load_permanent_config(data)
        configured_provider = str(configured.get("provider") or "").strip() or None
    except Exception:
        configured_provider = None
    try:
        result = ensure_endpoint(
            root,
            data,
            api_key,
            interactive=interactive,
            allow_download=allow_download,
            clipboard_timeout=clipboard_timeout,
            status=status,
        )
    except EndpointError as exc:
        return EndpointOutcome(
            exc.endpoint_status,
            provider=configured_provider,
            error_code=exc.error_code,
            message=exc.public_message,
            retryable=exc.retryable,
        ).as_dict()
    except (OSError, subprocess.SubprocessError, RuntimeError):
        return EndpointOutcome(
            EndpointStatus.UNAVAILABLE,
            provider=configured_provider,
            error_code=EndpointUnavailable.error_code,
            message=EndpointUnavailable.public_message,
        ).as_dict()
    except Exception:
        return EndpointOutcome(
            EndpointStatus.FAILED,
            provider=configured_provider,
            error_code=EndpointError.error_code,
            message=EndpointError.public_message,
        ).as_dict()
    normalized = dict(result)
    normalized["status"] = EndpointStatus.READY.value
    normalized["retryable"] = False
    return normalized


def write_startup_receipt(data: Path, payload: dict[str, Any]) -> Path:
    receipt = {
        "version": VERSION,
        "recorded_at_unix": int(time.time()),
        **payload,
    }
    path = data / "last_startup_result.json"
    atomic_json(path, receipt)
    return path


def install_combined_user_startup(
    root: Path,
    python_exe: Path,
    data: Path,
    *,
    platform_name: str | None = None,
    appdata_dir: Path | None = None,
) -> dict[str, Any]:
    """Install one ordered per-user startup task: backend -> endpoint -> verification.

    The Startup-folder VBS requires no Administrator rights and replaces the older
    two-entry race between backend and tunnel bootstraps. Optional platform/path
    arguments make the generated startup artifact independently testable.
    """
    platform_name = platform_name or os.name
    if platform_name != "nt":
        return {"status": "NOT_WINDOWS"}
    base = Path(appdata_dir) if appdata_dir is not None else Path(os.environ.get("APPDATA") or str(Path.home()))
    startup_dir = base / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    startup_dir.mkdir(parents=True, exist_ok=True)
    for stale in ("WorldEngineBackend.vbs", "WorldEnginePermanentUser.vbs", "WorldEngine4Auto.vbs"):
        try:
            (startup_dir / stale).unlink()
        except FileNotFoundError:
            pass
    script = root / "world_engine_startup.py"
    command = f'"{python_exe}" "{script}" --root "{root}" --supervise --non-interactive --no-launcher --no-reveal'
    escaped = command.replace('"', '""')
    vbs = startup_dir / "WorldEngine4Auto.vbs"
    vbs.write_text(
        'Set WshShell = CreateObject("WScript.Shell")\n'
        f'WshShell.Run "{escaped}", 0, False\n',
        encoding="utf-8",
    )
    return {"status": "INSTALLED_USER_STARTUP", "startup": str(vbs), "ordered": ["backend", "permanent_endpoint", "verification", "continuous_supervision"], "requires_admin": False}


def _acquire_supervisor_lock(data: Path):
    """Acquire a process-lifetime single-instance lock and return its file handle."""
    data.mkdir(parents=True, exist_ok=True)
    path = data / "world_engine_v4_supervisor.lock"
    handle = path.open("a+b")
    try:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return handle
    except Exception:
        handle.close()
        return None


def supervisor_cycle(root: Path, *, status: Callable[[str], None] = print) -> dict[str, Any]:
    """Run one idempotent backend/endpoint health-and-repair cycle."""
    root = normalize_install_root(root)
    data = persistent_data_dir()
    data.mkdir(parents=True, exist_ok=True)
    install_environment(data)
    api_key, _ = ensure_launcher_config(data)
    py = venv_python(root)
    if not py.exists():
        py = ensure_runtime_python(root, status=status)
    register_current_install(root, python_exe=str(py), data=data)
    backend = start_backend(root, data, api_key, py, status=status)
    endpoint = ensure_endpoint_outcome(
        root, data, api_key, interactive=False, allow_download=False, status=status,
    )
    if endpoint.get("status") == EndpointStatus.READY.value and endpoint.get("public_url"):
        verification = verify_endpoint(endpoint["public_url"], api_key, attempts=3, delay=0.5)
        ok = bool(verification.get("health_ok") and verification.get("protected_auth_ok"))
        if not ok:
            endpoint = {
                **endpoint,
                "status": EndpointStatus.FAILED.value,
                "error_code": EndpointVerificationFailed.error_code,
                "message": EndpointVerificationFailed.public_message,
                "retryable": True,
            }
    else:
        verification = {"skipped": True, "reason": endpoint.get("status")}
        ok = False
    result = {
        "status": "PASS" if ok else "DEGRADED",
        "version": VERSION,
        "recorded_at_unix": int(time.time()),
        "backend": backend,
        "endpoint": endpoint,
        "verification": verification,
        "api_key_fingerprint": api_key_fingerprint(api_key),
    }
    atomic_json(data / "supervisor_status.json", result)
    return result


def supervise(root: Path, *, interval_seconds: int = 30, status: Callable[[str], None] = print) -> int:
    """Continuously repair the user-session runtime without Administrator rights."""
    root = normalize_install_root(root)
    data = persistent_data_dir()
    lock = _acquire_supervisor_lock(data)
    if lock is None:
        status("[5.1.0] Supervisor is already running.")
        return 0
    logs = data / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    log_path = logs / "world_engine_supervisor.log"
    delay = max(10, min(int(interval_seconds), 300))
    try:
        while True:
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            try:
                result = supervisor_cycle(root, status=lambda message: None)
                line = f"{stamp} {result['status']} {result['endpoint'].get('public_url') or result['endpoint'].get('status')}\n"
            except Exception as exc:
                failure = {
                    "status": "FAILED", "version": VERSION, "recorded_at_unix": int(time.time()),
                    "error_type": type(exc).__name__, "error": str(exc),
                }
                atomic_json(data / "supervisor_status.json", failure)
                line = f"{stamp} FAILED {type(exc).__name__}: {exc}\n"
            with log_path.open("a", encoding="utf-8") as log:
                log.write(line)
            time.sleep(delay)
    except KeyboardInterrupt:
        return 0
    finally:
        lock.close()


def start_supervisor_process(root: Path, python_exe: Path) -> dict[str, Any]:
    """Launch the no-admin user-session supervisor; duplicate launches exit safely."""
    kwargs: dict[str, Any] = {
        "cwd": str(root), "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
    }
    if os.name == "nt":
        kwargs["creationflags"] = (
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
    proc = subprocess.Popen([
        str(python_exe), str(Path(root) / "world_engine_startup.py"),
        "--root", str(root), "--supervise", "--non-interactive", "--no-launcher", "--no-reveal",
    ], **kwargs)
    return {"status": "STARTED", "pid": proc.pid, "requires_admin": False}


def reveal_file(path: Path) -> bool:
    """Reveal a generated setup artifact without exposing its contents in logs."""
    try:
        path = Path(path).resolve()
        if os.name == "nt":
            subprocess.Popen(["explorer.exe", "/select,", str(path)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path.parent)])
        return True
    except Exception:
        return False


def launch_launcher(root: Path, python_exe: Path) -> None:
    """Launch the local diagnostics/repair window."""
    kwargs: dict[str, Any] = {"cwd": str(root), "stdin": subprocess.DEVNULL}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen([str(python_exe), str(root / "launcher.py")], **kwargs)


def companion_environment(parent: dict[str, str] | None = None) -> dict[str, str]:
    """Copy the desktop environment while stripping engine/tunnel secrets."""
    source = dict(os.environ if parent is None else parent)
    exact = {
        "WORLD_ENGINE_API_KEY",
        "WORLD_ENGINE_ADMIN_KEY",
        "WORLD_ENGINE_NGROK_AUTHTOKEN",
        "NGROK_AUTHTOKEN",
        "NGROK_API_KEY",
        "CF_TUNNEL_TOKEN",
        "CLOUDFLARE_TUNNEL_TOKEN",
        "TAILSCALE_AUTHKEY",
        "TAILSCALE_API_KEY",
    }
    for name in list(source):
        upper = name.upper()
        provider_secret = upper.startswith(("NGROK_", "CLOUDFLARE_", "TAILSCALE_")) and any(
            marker in upper for marker in ("TOKEN", "API_KEY", "AUTHKEY")
        )
        if upper in exact or provider_secret:
            source.pop(name, None)
    return source


def launch_companion_ui(root: Path, python_exe: Path) -> None:
    """Launch the local-DB desktop before optional endpoint setup."""
    companion = root / "world_engine_companion.py"
    if not companion.is_file():
        return
    kwargs: dict[str, Any] = {
        "cwd": str(root),
        "stdin": subprocess.DEVNULL,
        "env": companion_environment(),
    }
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen([str(python_exe), str(companion)], **kwargs)


def automatic_startup(
    root: Path,
    *,
    interactive: bool = True,
    allow_download: bool = True,
    clipboard_timeout: int = 600,
    launch_ui: bool = True,
    reveal_setup_artifacts: bool = True,
    force_copy_api_key: bool = False,
    start_supervisor: bool = True,
    status: Callable[[str], None] = print,
) -> dict[str, Any]:
    root = normalize_install_root(root)
    if not (root / "app.py").is_file():
        raise StartupError(f"World Engine app.py not found under {root}")
    data = persistent_data_dir()
    data.mkdir(parents=True, exist_ok=True)
    migration = migrate_legacy_data(root / "data", data)
    previous_migration = auto_migrate_from_previous_install(root, data)
    install_environment(data)
    api_key, key_created = ensure_launcher_config(data)
    python_exe = ensure_runtime_python(root, status=status)
    register_current_install(root, python_exe=str(python_exe), data=data)
    backend = start_backend(root, data, api_key, python_exe, status=status)
    local_ready_path = data / "LOCAL_ENGINE_READY.txt"
    local_ready_path.write_text(
        "WORLD ENGINE LOCAL RUNTIME READY\n\n"
        f"Local URL: {LOCAL_URL}\n"
        "GPT endpoint status: CHECKING\n"
        "The local engine and desktop are available independently of the optional GPT link.\n",
        encoding="utf-8",
    )
    if launch_ui:
        launch_companion_ui(root, python_exe)
    endpoint = ensure_endpoint_outcome(
        root,
        data,
        api_key,
        # Normal desktop startup must never sit in a ten-minute clipboard poll.
        # The companion owns explicit one-time authorization. Headless/manual
        # starts retain the legacy bounded clipboard workflow.
        interactive=interactive and not launch_ui,
        allow_download=allow_download,
        clipboard_timeout=clipboard_timeout, status=status,
    )
    # One ordered per-user bootstrap avoids a race between separate backend and endpoint entries.
    backend_startup = install_combined_user_startup(root, python_exe, data)
    supervisor = start_supervisor_process(root, python_exe) if start_supervisor else {"status": "DISABLED"}
    endpoint_ready = endpoint.get("status") == EndpointStatus.READY.value and bool(endpoint.get("public_url"))
    if endpoint_ready:
        verification = verify_endpoint(str(endpoint["public_url"]), api_key, attempts=3, delay=0.5)
        if not verification.get("health_ok") or not verification.get("protected_auth_ok"):
            endpoint = {
                **endpoint,
                "status": EndpointStatus.FAILED.value,
                "error_code": EndpointVerificationFailed.error_code,
                "message": EndpointVerificationFailed.public_message,
                "retryable": True,
            }
            endpoint_ready = False
    else:
        verification = {"skipped": True, "reason": endpoint.get("status")}
    first_endpoint_setup = endpoint_ready and not bool(endpoint.get("reused"))
    should_copy_key = endpoint_ready and interactive and (force_copy_api_key or key_created or first_endpoint_setup)
    key_copied = clipboard_write(api_key) if should_copy_key else False
    ready_path = data / "GPT_ACTION_SETUP_READY.txt"
    if endpoint_ready:
        ready_path.write_text(
            "WORLD ENGINE 5.1.0 CONNECTION READY\n\n"
            f"Permanent URL: {endpoint['public_url']}\n"
            f"API-key fingerprint: {api_key_fingerprint(api_key)}\n"
            f"Action schema: {endpoint['schema']}\n"
            f"API key copied to clipboard: {'YES' if key_copied else 'NO'}\n\n"
            "ONE-TIME CHATGPT SECURITY BOUNDARY:\n"
            "Import openapi_actions_PERMANENT.json and set the Action authentication to Bearer using the copied key. "
            "A local program cannot modify the private GPT Builder authentication setting. Later starts are automatic.\n",
            encoding="utf-8",
        )
    else:
        ready_path.unlink(missing_ok=True)
    local_ready_path.write_text(
        "WORLD ENGINE LOCAL RUNTIME READY\n\n"
        f"Local URL: {LOCAL_URL}\n"
        f"GPT endpoint status: {endpoint.get('status')}\n"
        "The local engine and desktop remain usable while the optional GPT link is configured.\n",
        encoding="utf-8",
    )
    overall_status = "PASS" if endpoint_ready else "DEGRADED"
    result = {
        "status": overall_status,
        "version": VERSION,
        "data_dir": str(data),
        "api_key_fingerprint": api_key_fingerprint(api_key),
        "api_key_created": key_created,
        "api_key_copied_to_clipboard": key_copied,
        "migration": migration,
        "previous_migration": previous_migration,
        "backend": backend,
        "endpoint": endpoint,
        "final_verification": verification,
        "backend_startup": backend_startup,
        "supervisor": supervisor,
        "ready_file": str(ready_path) if endpoint_ready else None,
        "local_ready_file": str(local_ready_path),
    }
    receipt = write_startup_receipt(data, result)
    result["receipt"] = str(receipt)
    if endpoint_ready:
        status(f"[5.1.0] PASS — {endpoint['public_url']}")
        status(f"[5.1.0] Action schema — {endpoint['schema']}")
    else:
        status(f"[5.1.0] DEGRADED — local engine/desktop ready; GPT endpoint {endpoint.get('status')}")
    status(f"[5.1.0] API-key fingerprint — {api_key_fingerprint(api_key)}")
    if key_copied:
        status("[5.1.0] The World Engine API key is on the clipboard for the one-time GPT Builder Bearer field.")
    if endpoint_ready and interactive and reveal_setup_artifacts and first_endpoint_setup:
        revealed = reveal_file(Path(endpoint["schema"]))
        status("[5.1.0] Opened the generated permanent Action schema in File Explorer." if revealed else "[5.1.0] Action schema is ready; open the path shown above.")
    if launch_ui:
        launch_launcher(root, python_exe)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="World Engine 5.1.0 automatic backend + permanent HTTPS startup")
    parser.add_argument("--root", default=str(Path(__file__).resolve().parent))
    parser.add_argument("--non-interactive", action="store_true")
    parser.add_argument("--no-download", action="store_true")
    parser.add_argument("--no-launcher", action="store_true")
    parser.add_argument("--clipboard-timeout", type=int, default=600)
    parser.add_argument("--no-reveal", action="store_true")
    parser.add_argument("--copy-api-key", action="store_true")
    parser.add_argument("--supervise", action="store_true")
    parser.add_argument("--supervisor-interval", type=int, default=30)
    args = parser.parse_args()
    if args.supervise:
        return supervise(Path(args.root), interval_seconds=args.supervisor_interval)
    try:
        result = automatic_startup(
            Path(args.root), interactive=not args.non_interactive,
            allow_download=not args.no_download,
            clipboard_timeout=args.clipboard_timeout,
            launch_ui=not args.no_launcher,
            reveal_setup_artifacts=not args.no_reveal,
            force_copy_api_key=args.copy_api_key,
        )
        print(json.dumps({
            "status": result["status"],
            "version": result["version"],
            "public_url": result["endpoint"].get("public_url"),
            "schema": result["endpoint"].get("schema"),
            "endpoint_status": result["endpoint"].get("status"),
            "api_key_fingerprint": result["api_key_fingerprint"],
            "api_key_copied_to_clipboard": result["api_key_copied_to_clipboard"],
            "receipt": result["receipt"],
        }, indent=2))
        return 0
    except Exception as exc:
        data = persistent_data_dir()
        data.mkdir(parents=True, exist_ok=True)
        failure = {"status": "FAILED", "version": VERSION, "error_type": type(exc).__name__, "error": str(exc)}
        write_startup_receipt(data, failure)
        print(f"\nWORLD ENGINE {VERSION} STARTUP FAILED\n{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
