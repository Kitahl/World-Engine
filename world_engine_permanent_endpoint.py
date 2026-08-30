from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
import zipfile
from pathlib import Path

from world_engine_connection_guard import normalize_install_root
from typing import Any

VERSION = "4.3.0"
PERMANENT_CONFIG = "permanent_endpoint.json"
TAILSCALE_PORT = 8000
TAILSCALE_PROVIDER = "tailscale_funnel"
CLOUDFLARE_PROVIDER = "cloudflare_named"
NGROK_PROVIDER = "ngrok_user"
NGROK_WINDOWS_AMD64_URL = "https://bin.ngrok.com/c/bNyj1mQVY4c/ngrok-v3-stable-windows-amd64.zip"
NGROK_WEB_ADDR = "127.0.0.1:4040"
CLOUDFLARED_VERSION = "2026.8.2"
CLOUDFLARED_WINDOWS_AMD64_SHA256 = "c29eee2b121f5436a642eed69fd9767da7e7b8c510fa50aaa130337f931357b5"
CLOUDFLARED_WINDOWS_AMD64_URL = (
    f"https://github.com/cloudflare/cloudflared/releases/download/{CLOUDFLARED_VERSION}/"
    "cloudflared-windows-amd64.exe"
)


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


def load_json(path: Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else dict(default or {})
    except Exception:
        return dict(default or {})


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def api_key_fingerprint(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:12]


def normalize_https_url(value: str) -> str:
    value = str(value or "").strip().rstrip("/")
    if not value.startswith("https://"):
        raise ValueError("permanent endpoint must use https://")
    return value


def probe(url: str, api_key: str | None = None, timeout: float = 8.0) -> tuple[bool, int | None, str]:
    headers = {"User-Agent": f"WorldEnginePermanent/{VERSION}"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read(4096).decode("utf-8", errors="replace")
            return int(r.status) == 200, int(r.status), body
    except urllib.error.HTTPError as e:
        try:
            body = e.read(4096).decode("utf-8", errors="replace")
        except Exception:
            body = str(e)
        return False, int(e.code), body
    except Exception as e:
        return False, None, f"{type(e).__name__}: {e}"


def run(cmd: list[str], *, timeout: float | None = 120, check: bool = False) -> subprocess.CompletedProcess[str]:
    cp = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if check and cp.returncode != 0:
        raise RuntimeError(f"command failed ({cp.returncode}): {' '.join(cmd)}\n{cp.stdout}\n{cp.stderr}")
    return cp


def find_tailscale() -> str | None:
    candidates: list[Path | str] = []
    if os.name == "nt":
        pf = os.environ.get("ProgramFiles", r"C:\Program Files")
        pfx86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
        candidates += [Path(pf) / "Tailscale" / "tailscale.exe", Path(pfx86) / "Tailscale" / "tailscale.exe"]
    candidates += ["tailscale.exe" if os.name == "nt" else "tailscale"]
    for candidate in candidates:
        if isinstance(candidate, Path):
            if candidate.exists():
                return str(candidate)
        else:
            found = shutil.which(candidate)
            if found:
                return found
    return None


def install_tailscale_windows() -> str:
    if os.name != "nt":
        raise RuntimeError("automatic Tailscale installation is implemented for Windows only")
    winget = shutil.which("winget")
    if not winget:
        raise RuntimeError("Tailscale is not installed and winget is unavailable. Install Tailscale once, then rerun.")
    cp = run([
        winget, "install", "--id", "Tailscale.Tailscale", "-e",
        "--accept-package-agreements", "--accept-source-agreements", "--silent",
    ], timeout=600)
    if cp.returncode not in {0}:
        raise RuntimeError(f"winget could not install Tailscale:\n{cp.stdout}\n{cp.stderr}")
    found = find_tailscale()
    if not found:
        raise RuntimeError("Tailscale installation completed but tailscale.exe was not found")
    return found


def tailscale_status(tailscale: str) -> dict[str, Any]:
    cp = run([tailscale, "status", "--json"], timeout=30)
    if cp.returncode != 0:
        return {"BackendState": "Unknown", "_error": (cp.stderr or cp.stdout).strip()}
    try:
        value = json.loads(cp.stdout)
        return value if isinstance(value, dict) else {}
    except Exception:
        return {"BackendState": "Unknown", "_error": "invalid JSON from tailscale status"}


def tailscale_dns_name(status: dict[str, Any]) -> str | None:
    self_obj = status.get("Self") if isinstance(status.get("Self"), dict) else {}
    name = str(self_obj.get("DNSName") or "").strip().rstrip(".")
    return name or None


def extract_auth_url(text: str) -> str | None:
    matches = re.findall(r"https://[^\s]+", text or "")
    for url in matches:
        if "tailscale.com" in url:
            return url.rstrip(".,)")
    return None


def ensure_tailscale_online(tailscale: str, *, interactive: bool = True, unattended: bool = False) -> dict[str, Any]:
    status = tailscale_status(tailscale)
    if str(status.get("BackendState", "")).lower() == "running" and tailscale_dns_name(status):
        return status
    up_cmd = [tailscale, "up"] + (["--unattended=true"] if unattended else [])
    cp = run(up_cmd, timeout=120)
    combined = (cp.stdout or "") + "\n" + (cp.stderr or "")
    auth_url = extract_auth_url(combined)
    if auth_url and interactive:
        print(f"[V400] One-time Tailscale login required: {auth_url}")
        try:
            webbrowser.open(auth_url)
        except Exception:
            pass
        print("[V400] Complete the login in your browser. Waiting for Tailscale...")
    for _ in range(120):
        status = tailscale_status(tailscale)
        if str(status.get("BackendState", "")).lower() == "running" and tailscale_dns_name(status):
            return status
        time.sleep(1)
    raise RuntimeError("Tailscale did not become connected. Complete the one-time Tailscale login and rerun.")


def enable_tailscale_funnel(tailscale: str, *, port: int = TAILSCALE_PORT, interactive: bool = True, unattended: bool = False) -> tuple[str, dict[str, Any]]:
    status = ensure_tailscale_online(tailscale, interactive=interactive, unattended=unattended)
    dns = tailscale_dns_name(status)
    if not dns:
        raise RuntimeError("Tailscale is connected but has no MagicDNS name")
    # --bg is the important permanence property: Tailscale persists this config
    # and resumes it after client/device restarts.
    cp = run([tailscale, "funnel", "--bg", "--yes", str(int(port))], timeout=120)
    combined = (cp.stdout or "") + "\n" + (cp.stderr or "")
    if cp.returncode != 0:
        approval_url = extract_auth_url(combined)
        if approval_url and interactive:
            print(f"[V400] One-time Funnel approval required: {approval_url}")
            try:
                webbrowser.open(approval_url)
            except Exception:
                pass
            print("[V400] Approve Funnel in the browser, then the installer will retry automatically.")
            for _ in range(120):
                time.sleep(1)
                retry = run([tailscale, "funnel", "--bg", "--yes", str(int(port))], timeout=60)
                if retry.returncode == 0:
                    break
            else:
                raise RuntimeError("Funnel approval did not complete; rerun after approving Tailscale Funnel")
        else:
            raise RuntimeError(f"tailscale funnel failed:\n{combined}")
    return f"https://{dns}", tailscale_status(tailscale)


def find_ngrok() -> str | None:
    data = persistent_data_dir()
    candidates: list[Path | str] = [data / "tools" / "ngrok.exe"]
    candidates += ["ngrok.exe" if os.name == "nt" else "ngrok"]
    for c in candidates:
        if isinstance(c, Path):
            if c.exists():
                return str(c)
        else:
            found = shutil.which(c)
            if found:
                return found
    return None


def download_portable_ngrok_windows() -> str:
    """Install ngrok into the current user's data directory; no elevation required."""
    if os.name != "nt":
        found = find_ngrok()
        if found:
            return found
        raise RuntimeError("Automatic portable ngrok download is packaged for Windows. Install ngrok in PATH and retry.")
    data = persistent_data_dir()
    dest = data / "tools" / "ngrok.exe"
    if dest.exists() and dest.stat().st_size > 1_000_000:
        return str(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    zip_path = dest.parent / "ngrok-windows-amd64.zip.download"
    print("[V400] Downloading official standalone ngrok agent (no Administrator install)...")
    req = urllib.request.Request(NGROK_WINDOWS_AMD64_URL, headers={"User-Agent": "WorldEngine/4.3.0"})
    with urllib.request.urlopen(req, timeout=120) as r, zip_path.open("wb") as out:
        shutil.copyfileobj(r, out)
    if zip_path.stat().st_size < 1_000_000:
        zip_path.unlink(missing_ok=True)
        raise RuntimeError("ngrok download was unexpectedly small")
    with zipfile.ZipFile(zip_path) as z:
        member = next((n for n in z.namelist() if Path(n).name.lower() == "ngrok.exe"), None)
        if not member:
            raise RuntimeError("ngrok.exe not found in downloaded archive")
        with z.open(member) as src, dest.open("wb") as out:
            shutil.copyfileobj(src, out)
    zip_path.unlink(missing_ok=True)
    if not dest.exists() or dest.stat().st_size < 1_000_000:
        raise RuntimeError("portable ngrok extraction failed")
    return str(dest)


def ngrok_config_path(data: Path | None = None) -> Path:
    data = data or persistent_data_dir()
    return data / "ngrok.yml"


def configure_ngrok_authtoken(ngrok: str, token: str, *, data: Path | None = None) -> Path:
    token = str(token or "").strip()
    if not token:
        raise ValueError("ngrok authtoken is required")
    data = data or persistent_data_dir()
    cfg = ngrok_config_path(data)
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cp = run([ngrok, "config", "add-authtoken", token, "--config", str(cfg)], timeout=60)
    if cp.returncode != 0:
        raise RuntimeError(f"ngrok could not save the authtoken:\n{cp.stdout}\n{cp.stderr}")
    return cfg


def _ngrok_tunnels(web_addr: str = NGROK_WEB_ADDR) -> list[dict[str, Any]]:
    try:
        with urllib.request.urlopen(f"http://{web_addr}/api/tunnels", timeout=1.5) as r:
            payload = json.loads(r.read().decode("utf-8", errors="replace"))
        tunnels = payload.get("tunnels") if isinstance(payload, dict) else []
        return [x for x in tunnels if isinstance(x, dict)] if isinstance(tunnels, list) else []
    except Exception:
        return []


def ngrok_public_url(web_addr: str = NGROK_WEB_ADDR, *, target_port: int = TAILSCALE_PORT) -> str | None:
    for tunnel in _ngrok_tunnels(web_addr):
        public_url = str(tunnel.get("public_url") or "").strip().rstrip("/")
        cfg = tunnel.get("config") if isinstance(tunnel.get("config"), dict) else {}
        addr = str(cfg.get("addr") or "")
        if public_url.startswith("https://") and (str(target_port) in addr or not addr):
            return public_url
    return None


def start_ngrok_user_endpoint(ngrok: str, *, data: Path | None = None, port: int = TAILSCALE_PORT, expected_url: str | None = None) -> dict[str, Any]:
    """Start or reuse a user-session ngrok endpoint. No Windows service/elevation."""
    data = data or persistent_data_dir()
    current = ngrok_public_url(target_port=port)
    if current:
        if expected_url and normalize_https_url(expected_url) != normalize_https_url(current):
            raise RuntimeError(f"ngrok returned {current}, but the permanent GPT schema expects {expected_url}")
        return {"status": "ALREADY_RUNNING", "public_url": current, "pid": None}
    cfg = ngrok_config_path(data)
    if not cfg.exists():
        raise RuntimeError("ngrok authentication is not configured; run Permanent Endpoint Setup once")
    logs = data / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    log_path = logs / "ngrok.log"
    cmd = [ngrok, "http", str(int(port)), "--config", str(cfg), "--log", str(log_path), "--log-format", "json"]
    if expected_url:
        cmd += ["--url", normalize_https_url(expected_url)]
    kwargs: dict[str, Any] = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.Popen(cmd, **kwargs)
    for _ in range(60):
        url = ngrok_public_url(target_port=port)
        if url:
            if expected_url and normalize_https_url(expected_url) != normalize_https_url(url):
                try:
                    proc.terminate()
                except Exception:
                    pass
                raise RuntimeError(f"ngrok endpoint changed from {expected_url} to {url}; do not silently rewrite the GPT endpoint")
            return {"status": "STARTED", "public_url": url, "pid": proc.pid, "log": str(log_path)}
        if proc.poll() is not None:
            tail = ""
            try:
                tail = log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
            except Exception:
                pass
            raise RuntimeError(f"ngrok exited before creating an endpoint (code {proc.returncode}).\n{tail}")
        time.sleep(0.5)
    raise RuntimeError(f"ngrok did not publish an endpoint; inspect {log_path}")


def install_user_startup_bootstrap(root: Path, *, data: Path | None = None) -> dict[str, Any]:
    """Install a per-user Windows Startup entry; intentionally requires no Administrator rights."""
    data = data or persistent_data_dir()
    if os.name != "nt":
        return {"status": "NOT_WINDOWS"}
    startup_dir = Path(os.environ.get("APPDATA", str(Path.home()))) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    startup_dir.mkdir(parents=True, exist_ok=True)
    bootstrap = data / "world_engine_user_bootstrap.py"
    bootstrap.write_text(
        "from pathlib import Path\nimport json, subprocess, sys, time, urllib.request, os\n"
        "data=Path(__file__).resolve().parent\n"
        "runtime=json.loads((data/'runtime_install.json').read_text(encoding='utf-8'))\n"
        "root=Path(runtime['install_root'])\n"
        "sys.path.insert(0,str(root))\n"
        "from world_engine_autostart import start_backend_from_runtime\n"
        "from world_engine_permanent_endpoint import load_permanent_config, find_ngrok, start_ngrok_user_endpoint\n"
        "start_backend_from_runtime(data=data)\n"
        "cfg=load_permanent_config(data)\n"
        "if cfg.get('provider')=='ngrok_user':\n"
        "    ng=find_ngrok()\n"
        "    if ng: start_ngrok_user_endpoint(ng,data=data,expected_url=cfg.get('public_url'))\n",
        encoding="utf-8",
    )
    python_exe = str(load_json(data / "runtime_install.json").get("python_exe") or sys.executable).replace('"','""')
    boot = str(bootstrap).replace('"','""')
    vbs = startup_dir / "WorldEnginePermanentUser.vbs"
    cmd = f'"{python_exe}" "{boot}"'
    vbs.write_text('Set WshShell = CreateObject("WScript.Shell")\n' + f'WshShell.Run "{cmd.replace(chr(34), chr(34)*2)}", 0, False\n', encoding="utf-8")
    return {"status": "INSTALLED_USER_STARTUP", "startup": str(vbs), "bootstrap": str(bootstrap)}


def install_ngrok_user_permanent(root: Path, authtoken: str, *, allow_download: bool = True) -> dict[str, Any]:
    """Default permanent endpoint: portable ngrok + current-user Startup. No elevation required."""
    root = normalize_install_root(root)
    data = persistent_data_dir(); data.mkdir(parents=True, exist_ok=True)
    api_key = load_launcher_api_key(data)
    ngrok = find_ngrok()
    if not ngrok and allow_download:
        ngrok = download_portable_ngrok_windows()
    if not ngrok:
        raise RuntimeError("ngrok not found; download the standalone agent from https://ngrok.com/download/windows and rerun")
    configure_ngrok_authtoken(ngrok, authtoken, data=data)
    result = start_ngrok_user_endpoint(ngrok, data=data)
    url = normalize_https_url(result["public_url"])
    verification = verify_endpoint(url, api_key, attempts=45, delay=1.0)
    if not verification.get("health_ok") or not verification.get("protected_auth_ok"):
        raise RuntimeError(f"ngrok endpoint did not verify: {json.dumps(verification, indent=2)}")
    schema = write_permanent_schema(root, url, data=data)
    startup = install_user_startup_bootstrap(root, data=data)
    config_path = save_permanent_config(
        NGROK_PROVIDER, url, api_key, data=data,
        extra={"ngrok_exe": ngrok, "ngrok_config": str(ngrok_config_path(data)), "startup_mode": "user_login", "requires_admin": False, "assigned_dev_domain": True},
    )
    return {"status":"PASS","provider":NGROK_PROVIDER,"public_url":url,"schema":str(schema),"config":str(config_path),"verification":verification,"startup":startup}


def ensure_permanent_runtime(root: Path | None = None, *, data: Path | None = None) -> dict[str, Any]:
    """Self-heal a configured user-mode permanent endpoint before declaring it unreachable."""
    data = data or persistent_data_dir()
    cfg = load_permanent_config(data)
    provider = str(cfg.get("provider") or "")
    url = str(cfg.get("public_url") or "").strip()
    if not url:
        return {"status":"NOT_CONFIGURED"}
    if provider == NGROK_PROVIDER:
        ngrok = find_ngrok()
        if not ngrok:
            return {"status":"NGROK_MISSING","public_url":url}
        try:
            runtime = start_ngrok_user_endpoint(ngrok, data=data, expected_url=url)
            return {"status":"RUNNING","provider":provider,"public_url":url,"runtime":runtime}
        except Exception as exc:
            return {"status":"FAILED","provider":provider,"public_url":url,"error":f"{type(exc).__name__}: {exc}"}
    if provider == TAILSCALE_PROVIDER:
        tailscale = find_tailscale()
        if not tailscale:
            return {"status":"TAILSCALE_MISSING","public_url":url}
        try:
            enable_tailscale_funnel(tailscale, port=TAILSCALE_PORT, interactive=False, unattended=bool(cfg.get("unattended", False)))
            return {"status":"RUNNING","provider":provider,"public_url":url}
        except Exception as exc:
            return {"status":"FAILED","provider":provider,"public_url":url,"error":f"{type(exc).__name__}: {exc}"}
    return {"status":"EXTERNAL_PROVIDER","provider":provider,"public_url":url}


def find_cloudflared(root: Path | None = None) -> str | None:
    candidates: list[Path | str] = []
    data = persistent_data_dir()
    if root:
        candidates += [root / "cloudflared.exe", root / "tools" / "cloudflared.exe"]
    candidates += [data / "tools" / f"cloudflared-{CLOUDFLARED_VERSION}-windows-amd64.exe"]
    if os.name == "nt":
        candidates += [Path(r"C:\Program Files (x86)\cloudflared\cloudflared.exe"), Path(r"C:\Cloudflared\bin\cloudflared.exe")]
    candidates += ["cloudflared.exe" if os.name == "nt" else "cloudflared"]
    for c in candidates:
        if isinstance(c, Path):
            if c.exists(): return str(c)
        else:
            found = shutil.which(c)
            if found: return found
    return None


def download_pinned_cloudflared() -> str:
    if os.name != "nt":
        raise RuntimeError("pinned cloudflared auto-install is implemented for Windows only")
    dest = persistent_data_dir() / "tools" / f"cloudflared-{CLOUDFLARED_VERSION}-windows-amd64.exe"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists() or sha256_file(dest) != CLOUDFLARED_WINDOWS_AMD64_SHA256:
        tmp = dest.with_suffix(".download")
        print(f"[V400] Downloading pinned cloudflared {CLOUDFLARED_VERSION}...")
        urllib.request.urlretrieve(CLOUDFLARED_WINDOWS_AMD64_URL, tmp)
        digest = sha256_file(tmp)
        if digest != CLOUDFLARED_WINDOWS_AMD64_SHA256:
            tmp.unlink(missing_ok=True)
            raise RuntimeError(f"cloudflared SHA-256 mismatch: {digest}")
        os.replace(tmp, dest)
    return str(dest)


def install_cloudflare_named_service(token: str, stable_url: str, *, root: Path | None = None) -> str:
    if not token.strip():
        raise ValueError("Cloudflare tunnel token is required")
    stable_url = normalize_https_url(stable_url)
    cf = find_cloudflared(root) or download_pinned_cloudflared()
    # Remotely managed named tunnel. Cloudflare's dashboard must already map the
    # public hostname to http://127.0.0.1:8000.
    cp = run([cf, "service", "install", token.strip()], timeout=180)
    combined = (cp.stdout or "") + "\n" + (cp.stderr or "")
    if cp.returncode != 0 and "already" not in combined.lower():
        raise RuntimeError(f"cloudflared service install failed:\n{combined}")
    if os.name == "nt":
        run(["sc", "start", "cloudflared"], timeout=60)
    return stable_url


def load_launcher_api_key(data: Path | None = None) -> str:
    data = data or persistent_data_dir()
    cfg = load_json(data / "launcher_config.json")
    key = str(cfg.get("api_key") or "").strip()
    if not key:
        raise RuntimeError(f"{data / 'launcher_config.json'} has no api_key; run World Engine launcher once first")
    return key


def verify_endpoint(url: str, api_key: str, *, attempts: int = 30, delay: float = 1.0) -> dict[str, Any]:
    url = normalize_https_url(url)
    last: dict[str, Any] = {}
    for _ in range(max(1, attempts)):
        health_ok, health_status, health_body = probe(url + "/health", timeout=8)
        auth_ok, auth_status, auth_body = probe(
            url + "/api/context?campaign_id=default&event_limit=1&entity_limit=1",
            api_key=api_key,
            timeout=8,
        )
        last = {
            "url": url,
            "health_ok": health_ok,
            "health_status": health_status,
            "health_body": health_body[:500],
            "protected_auth_ok": auth_ok,
            "protected_auth_status": auth_status,
            "protected_auth_body": auth_body[:500],
            "api_key_fingerprint": api_key_fingerprint(api_key),
        }
        if health_ok and auth_ok:
            return last
        time.sleep(delay)
    return last


def write_permanent_schema(root: Path, public_url: str, *, data: Path | None = None) -> Path:
    root = normalize_install_root(root)
    data = data or persistent_data_dir()
    candidates = [root / "openapi_actions.json", root / "openapi_actions_live.json"]
    source = next((p for p in candidates if p.exists()), None)
    if source is None:
        raise FileNotFoundError("openapi_actions.json not found in World Engine installation")
    schema = json.loads(source.read_text(encoding="utf-8"))
    schema["servers"] = [{"url": normalize_https_url(public_url)}]
    out = root / "openapi_actions_PERMANENT.json"
    out.write_text(json.dumps(schema, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    data.mkdir(parents=True, exist_ok=True)
    persistent_copy = data / "openapi_actions_PERMANENT.json"
    persistent_copy.write_bytes(out.read_bytes())
    return out


def save_permanent_config(provider: str, url: str, api_key: str, *, data: Path | None = None, extra: dict[str, Any] | None = None) -> Path:
    data = data or persistent_data_dir()
    payload = {
        "version": VERSION,
        "provider": provider,
        "public_url": normalize_https_url(url),
        "origin": "http://127.0.0.1:8000",
        "api_key_fingerprint": api_key_fingerprint(api_key),
        "installed_at_unix": int(time.time()),
        "permanent": True,
        "quick_tunnel_required": False,
    }
    if extra:
        payload.update(extra)
    path = data / PERMANENT_CONFIG
    atomic_json(path, payload)
    # v3.9.6 guard interoperability.
    guard_path = data / "connection_guard.json"
    guard = load_json(guard_path)
    guard.update({
        "version": max(2, int(guard.get("version", 1) or 1)),
        "mode": "permanent",
        "stable_public_url": payload["public_url"],
        "require_action_reimport_ack": False,
    })
    atomic_json(guard_path, guard)
    try:
        (data / "ACTION_REIMPORT_REQUIRED.txt").unlink()
    except FileNotFoundError:
        pass
    return path


def load_permanent_config(data: Path | None = None) -> dict[str, Any]:
    data = data or persistent_data_dir()
    return load_json(data / PERMANENT_CONFIG)


def permanent_status(api_key: str | None = None, *, data: Path | None = None) -> dict[str, Any]:
    data = data or persistent_data_dir()
    cfg = load_permanent_config(data)
    url = str(cfg.get("public_url") or "").strip()
    if not url:
        return {"configured": False, "reason": "permanent endpoint is not configured"}
    if api_key is None:
        api_key = load_launcher_api_key(data)
    result = verify_endpoint(url, api_key, attempts=1)
    result.update({"configured": True, "provider": cfg.get("provider"), "permanent": bool(cfg.get("permanent"))})
    return result


def install_tailscale_permanent(root: Path, *, allow_install: bool = True, interactive: bool = True, unattended: bool = False) -> dict[str, Any]:
    root = normalize_install_root(root)
    data = persistent_data_dir()
    data.mkdir(parents=True, exist_ok=True)
    api_key = load_launcher_api_key(data)
    tailscale = find_tailscale()
    if not tailscale and allow_install:
        tailscale = install_tailscale_windows()
    if not tailscale:
        raise RuntimeError("Tailscale not found")
    status = ensure_tailscale_online(tailscale, interactive=interactive, unattended=unattended)
    url, status = enable_tailscale_funnel(tailscale, port=TAILSCALE_PORT, interactive=interactive, unattended=unattended)
    verification = verify_endpoint(url, api_key, attempts=45, delay=1.0)
    if not verification.get("health_ok") or not verification.get("protected_auth_ok"):
        raise RuntimeError(f"permanent Tailscale endpoint did not verify: {json.dumps(verification, indent=2)}")
    schema = write_permanent_schema(root, url, data=data)
    config_path = save_permanent_config(
        TAILSCALE_PROVIDER, url, api_key, data=data,
        extra={"tailscale_dns_name": tailscale_dns_name(status), "tailscale_cli": tailscale, "funnel_background": True, "unattended": bool(unattended), "requires_admin": bool(unattended)},
    )
    return {"status": "PASS", "provider": TAILSCALE_PROVIDER, "public_url": url, "schema": str(schema), "config": str(config_path), "verification": verification}


def install_cloudflare_permanent(root: Path, stable_url: str, token: str) -> dict[str, Any]:
    root = normalize_install_root(root)
    data = persistent_data_dir(); data.mkdir(parents=True, exist_ok=True)
    api_key = load_launcher_api_key(data)
    url = install_cloudflare_named_service(token, stable_url, root=root)
    verification = verify_endpoint(url, api_key, attempts=60, delay=1.0)
    if not verification.get("health_ok") or not verification.get("protected_auth_ok"):
        raise RuntimeError(f"named Cloudflare endpoint did not verify: {json.dumps(verification, indent=2)}")
    schema = write_permanent_schema(root, url, data=data)
    config_path = save_permanent_config(
        CLOUDFLARE_PROVIDER, url, api_key, data=data,
        extra={"cloudflared_version": CLOUDFLARED_VERSION, "windows_service": True},
    )
    return {"status": "PASS", "provider": CLOUDFLARE_PROVIDER, "public_url": url, "schema": str(schema), "config": str(config_path), "verification": verification}
