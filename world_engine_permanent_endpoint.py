from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from ctypes import wintypes
from pathlib import Path
from typing import Any

from world_engine.process_guard import open_no_redirect
from world_engine_connection_guard import normalize_install_root

VERSION = "5.1.0"
PERMANENT_CONFIG = "permanent_endpoint.json"
TAILSCALE_PORT = 8000
TAILSCALE_PROVIDER = "tailscale_funnel"
CLOUDFLARE_PROVIDER = "cloudflare_named"
NGROK_PROVIDER = "ngrok_user"
NGROK_WINDOWS_STORE_PRODUCT_ID = "9MVS1J51GMK6"
NGROK_WINDOWS_STORE_PACKAGE_FAMILY = "ngrok.ngrok_1g87z0zv29zzc"
WINGET_WINDOWS_STORE_PACKAGE_FAMILY = "Microsoft.DesktopAppInstaller_8wekyb3d8bbwe"
NGROK_WINDOWS_INSTALL_COMMAND = (
    "winget", "install", "--id", NGROK_WINDOWS_STORE_PRODUCT_ID, "--exact",
    "--source", "msstore", "--accept-source-agreements",
    "--accept-package-agreements", "--disable-interactivity", "--silent",
)
NGROK_AUTHTOKEN_RE = re.compile(r"^[A-Za-z0-9_.=-]{20,512}$")
NGROK_WEB_ADDR = "127.0.0.1:4040"
CLOUDFLARED_VERSION = "2026.8.2"
CLOUDFLARED_WINDOWS_AMD64_SHA256 = "c29eee2b121f5436a642eed69fd9767da7e7b8c510fa50aaa130337f931357b5"
CLOUDFLARED_WINDOWS_AMD64_URL = (
    f"https://github.com/cloudflare/cloudflared/releases/download/{CLOUDFLARED_VERSION}/"
    "cloudflared-windows-amd64.exe"
)


def _persistent_data_dir_lexical() -> Path:
    override = os.environ.get("WORLD_ENGINE_DATA_DIR", "").strip()
    if override:
        path = Path(override).expanduser()
    elif os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
        path = Path(base) / "WorldEngine"
    else:
        xdg = os.environ.get("XDG_DATA_HOME", "").strip()
        path = Path(xdg).expanduser() / "world-engine" if xdg else Path.home() / ".local" / "share" / "world-engine"
    return Path(os.path.abspath(str(path)))


def persistent_data_dir() -> Path:
    return _persistent_data_dir_lexical().resolve()


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
        with open_no_redirect(req, timeout) as r:
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


def _windows_system_executable(filename: str) -> str:
    """Resolve a Windows system executable without current-directory/PATH search."""
    if os.name != "nt":
        raise RuntimeError("Windows system executables are unavailable on this platform")
    if not filename or Path(filename).name != filename:
        raise ValueError("Windows system executable name must not contain a path")
    capacity = 32768
    buffer = ctypes.create_unicode_buffer(capacity)
    get_system_directory = ctypes.windll.kernel32.GetSystemDirectoryW
    get_system_directory.argtypes = [wintypes.LPWSTR, wintypes.UINT]
    get_system_directory.restype = wintypes.UINT
    length = int(get_system_directory(buffer, capacity))
    if length == 0 or length >= capacity:
        raise RuntimeError("Windows GetSystemDirectoryW failed")
    executable = Path(buffer.value) / filename
    if not executable.is_file():
        raise RuntimeError(f"required Windows system executable is missing: {executable}")
    return str(executable)


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
        print(f"[{VERSION}] One-time Tailscale login required: {auth_url}")
        try:
            webbrowser.open(auth_url)
        except Exception:
            pass
        print(f"[{VERSION}] Complete the login in your browser. Waiting for Tailscale...")
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
            print(f"[{VERSION}] One-time Funnel approval required: {approval_url}")
            try:
                webbrowser.open(approval_url)
            except Exception:
                pass
            print(f"[{VERSION}] Approve Funnel in the browser, then the installer will retry automatically.")
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


class _Guid(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


_FOLDERID_LOCAL_APP_DATA = _Guid(
    0xF1B32785, 0x6FBA, 0x4FCF,
    (ctypes.c_ubyte * 8)(0x9D, 0x55, 0x7B, 0x8E, 0x7F, 0x15, 0x70, 0x91),
)
_ERROR_INSUFFICIENT_BUFFER = 122
_IO_REPARSE_TAG_APPEXECLINK = 0x8000001B
_FSCTL_GET_REPARSE_POINT = 0x000900A8
_FILE_SHARE_ALL = 0x00000001 | 0x00000002 | 0x00000004
_OPEN_EXISTING = 3
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_AF_INET = 2
_MIB_TCP_STATE_LISTEN = 2
_TCP_TABLE_OWNER_PID_LISTENER = 3


class _MibTcpRowOwnerPid(ctypes.Structure):
    _fields_ = [
        ("dwState", wintypes.DWORD),
        ("dwLocalAddr", wintypes.DWORD),
        ("dwLocalPort", wintypes.DWORD),
        ("dwRemoteAddr", wintypes.DWORD),
        ("dwRemotePort", wintypes.DWORD),
        ("dwOwningPid", wintypes.DWORD),
    ]


def _known_folder_path(folder_id: _Guid) -> Path | None:
    """Resolve a Windows known folder without trusting process environment variables."""
    if os.name != "nt":
        return None
    raw = ctypes.c_void_p()
    ole32 = None
    try:
        shell32 = ctypes.windll.shell32
        ole32 = ctypes.windll.ole32
        shell32.SHGetKnownFolderPath.argtypes = [
            ctypes.POINTER(_Guid), wintypes.DWORD, wintypes.HANDLE,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        shell32.SHGetKnownFolderPath.restype = ctypes.c_long
        if shell32.SHGetKnownFolderPath(ctypes.byref(folder_id), 0, None, ctypes.byref(raw)) != 0:
            return None
        return Path(ctypes.wstring_at(raw.value))
    except (AttributeError, OSError, ValueError):
        return None
    finally:
        if raw.value and ole32 is not None:
            try:
                ole32.CoTaskMemFree(raw)
            except (AttributeError, OSError):
                pass


def _windows_app_alias(filename: str, expected_package_family: str) -> Path | None:
    """Return a canonical alias path for one explicitly allowed Store package."""
    allowed = {
        "ngrok.exe": NGROK_WINDOWS_STORE_PACKAGE_FAMILY,
        "winget.exe": WINGET_WINDOWS_STORE_PACKAGE_FAMILY,
    }
    key = str(filename).casefold()
    if os.name != "nt" or allowed.get(key, "").casefold() != str(expected_package_family).casefold():
        return None
    local_app_data = _known_folder_path(_FOLDERID_LOCAL_APP_DATA)
    if not local_app_data:
        return None
    alias = local_app_data / "Microsoft" / "WindowsApps" / key
    return alias if alias.exists() and _is_app_execution_alias(alias) else None


def _is_app_execution_alias(path: Path) -> bool:
    """Reject ordinary files before launch; accept only Microsoft's AppExecLink tag."""
    if os.name != "nt":
        return False
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.DeviceIoControl.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
        ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p,
    ]
    kernel32.DeviceIoControl.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.CreateFileW(
        str(path), 0, _FILE_SHARE_ALL, None, _OPEN_EXISTING,
        _FILE_FLAG_OPEN_REPARSE_POINT, None,
    )
    invalid_handle = ctypes.c_void_p(-1).value
    if handle in {None, invalid_handle}:
        return False
    try:
        buffer = ctypes.create_string_buffer(16 * 1024)
        returned = wintypes.DWORD()
        ok = kernel32.DeviceIoControl(
            handle, _FSCTL_GET_REPARSE_POINT, None, 0, buffer,
            len(buffer), ctypes.byref(returned), None,
        )
        return bool(ok and returned.value >= 8 and int.from_bytes(buffer.raw[0:4], "little") == _IO_REPARSE_TAG_APPEXECLINK)
    except (OSError, ValueError):
        return False
    finally:
        kernel32.CloseHandle(handle)


def _package_family_for_handle(handle_value: int) -> str | None:
    if os.name != "nt" or not handle_value:
        return None
    kernel32 = ctypes.windll.kernel32
    kernel32.GetPackageFamilyName.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(wintypes.UINT), wintypes.LPWSTR,
    ]
    kernel32.GetPackageFamilyName.restype = ctypes.c_long
    length = wintypes.UINT(0)
    handle = wintypes.HANDLE(int(handle_value))
    first = kernel32.GetPackageFamilyName(handle, ctypes.byref(length), None)
    if first != _ERROR_INSUFFICIENT_BUFFER or length.value < 2:
        return None
    buffer = ctypes.create_unicode_buffer(length.value)
    if kernel32.GetPackageFamilyName(handle, ctypes.byref(length), buffer) != 0:
        return None
    return str(buffer.value or "") or None


def _process_package_family(process: subprocess.Popen[str]) -> str | None:
    """Return the documented Windows package family for a retained process handle."""
    if not hasattr(process, "_handle"):
        return None
    return _package_family_for_handle(int(process._handle))


def _windows_pid_package_family(pid: int) -> str | None:
    """Open one live process by PID and return its Windows package family."""
    if os.name != "nt" or int(pid) <= 0:
        return None
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return None
    try:
        return _package_family_for_handle(int(handle))
    finally:
        kernel32.CloseHandle(handle)


def _windows_tcp_listener_pid(web_addr: str) -> int | None:
    """Return the PID owning the fixed IPv4 loopback TCP listener."""
    if os.name != "nt":
        return None
    host, separator, port_text = str(web_addr).rpartition(":")
    if not separator or host != "127.0.0.1":
        return None
    try:
        port = int(port_text)
    except ValueError:
        return None
    if not 0 < port <= 65535:
        return None
    iphlpapi = ctypes.windll.iphlpapi
    iphlpapi.GetExtendedTcpTable.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), wintypes.BOOL,
        wintypes.ULONG, ctypes.c_int, wintypes.ULONG,
    ]
    iphlpapi.GetExtendedTcpTable.restype = wintypes.DWORD
    size = wintypes.DWORD(0)
    first = iphlpapi.GetExtendedTcpTable(
        None, ctypes.byref(size), False, _AF_INET, _TCP_TABLE_OWNER_PID_LISTENER, 0,
    )
    if first not in {0, _ERROR_INSUFFICIENT_BUFFER} or size.value < ctypes.sizeof(wintypes.DWORD):
        return None
    buffer = ctypes.create_string_buffer(size.value)
    if iphlpapi.GetExtendedTcpTable(
        buffer, ctypes.byref(size), False, _AF_INET, _TCP_TABLE_OWNER_PID_LISTENER, 0,
    ) != 0:
        return None
    count = wintypes.DWORD.from_buffer_copy(buffer.raw[:4]).value
    row_size = ctypes.sizeof(_MibTcpRowOwnerPid)
    loopback = int.from_bytes(b"\x7f\x00\x00\x01", "little")
    for index in range(count):
        offset = ctypes.sizeof(wintypes.DWORD) + index * row_size
        if offset + row_size > size.value:
            return None
        row = _MibTcpRowOwnerPid.from_buffer_copy(buffer.raw[offset:offset + row_size])
        encoded_port = int(row.dwLocalPort) & 0xFFFF
        local_port = ((encoded_port & 0xFF) << 8) | ((encoded_port >> 8) & 0xFF)
        if (
            row.dwState == _MIB_TCP_STATE_LISTEN
            and local_port == port
            and int(row.dwLocalAddr) in {0, loopback}
        ):
            return int(row.dwOwningPid) or None
    return None


def _trusted_ngrok_listener(web_addr: str = NGROK_WEB_ADDR) -> bool:
    pid = _windows_tcp_listener_pid(web_addr)
    family = _windows_pid_package_family(pid) if pid else None
    return bool(family and family.casefold() == NGROK_WINDOWS_STORE_PACKAGE_FAMILY.casefold())


def _run_packaged(
    command: list[str], expected_package_family: str, *, timeout: float,
) -> subprocess.CompletedProcess[str]:
    """Run an absolute App Execution Alias and authenticate its actual process package."""
    if os.name != "nt" or not command or not Path(command[0]).is_absolute():
        raise RuntimeError("packaged executable must use an absolute Windows App Execution Alias")
    if not _is_app_execution_alias(Path(command[0])):
        raise RuntimeError("packaged executable is not a registered Windows App Execution Alias")
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as process:
        family = _process_package_family(process)
        if not family or family.casefold() != expected_package_family.casefold():
            process.kill()
            stdout, stderr = process.communicate()
            raise RuntimeError("App Execution Alias package identity could not be verified")
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            raise
        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def _is_reparse_point(path: Path) -> bool:
    try:
        attributes = getattr(os.lstat(path), "st_file_attributes", 0)
        return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    except OSError:
        return False


def _path_has_reparse_component(path: Path) -> bool:
    """Fail closed when an existing lexical path component is a reparse point or unreadable."""
    absolute = Path(os.path.abspath(str(path)))
    parts = absolute.parts
    if not parts:
        return True
    current = Path(parts[0])
    for part in parts[1:]:
        current /= part
        try:
            attributes = getattr(os.lstat(current), "st_file_attributes", 0)
        except FileNotFoundError:
            continue
        except OSError:
            return True
        if attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
            return True
    return False


def _remove_legacy_portable_ngrok() -> dict[str, list[str]]:
    """Delete only the old World Engine cache, never through a junction."""
    data = _persistent_data_dir_lexical()
    tools = data / "tools"
    report: dict[str, list[str]] = {"removed": [], "failed": [], "refused": []}
    if _path_has_reparse_component(data) or _path_has_reparse_component(tools):
        report["refused"].append(str(tools))
        return report
    for path in (tools / "ngrok.exe", tools / "ngrok-windows-amd64.zip.download"):
        try:
            existed = path.exists() or path.is_symlink()
            path.unlink(missing_ok=True)
            if existed:
                report["removed"].append(str(path))
        except OSError as exc:
            report["failed"].append(f"{path}: {type(exc).__name__}")
    return report


def _probe_ngrok_executable(ngrok: str | Path) -> bool:
    try:
        cp = run_ngrok_command(ngrok, ["version"], timeout=15)
    except (OSError, RuntimeError, subprocess.SubprocessError):
        return False
    text = f"{cp.stdout or ''}\n{cp.stderr or ''}"
    return cp.returncode == 0 and bool(re.search(r"(?im)^\s*ngrok version\s+\d", text))


def _probe_winget_executable(winget: str | Path) -> bool:
    try:
        cp = _run_packaged(
            [str(winget), "--version"], WINGET_WINDOWS_STORE_PACKAGE_FAMILY, timeout=15,
        )
    except (OSError, RuntimeError, subprocess.SubprocessError):
        return False
    return cp.returncode == 0 and bool(re.fullmatch(r"v?\d+(?:\.\d+){1,3}", str(cp.stdout or "").strip()))


def _canonical_ngrok_alias(ngrok: str | Path) -> Path | None:
    alias = _windows_app_alias("ngrok.exe", NGROK_WINDOWS_STORE_PACKAGE_FAMILY)
    if not alias:
        return None
    supplied = os.path.normcase(os.path.abspath(str(ngrok)))
    expected = os.path.normcase(os.path.abspath(str(alias)))
    return alias if supplied == expected else None


def run_ngrok_command(
    ngrok: str | Path, arguments: list[str], *, timeout: float,
) -> subprocess.CompletedProcess[str]:
    """Run and authenticate the actual Store ngrok child used for a bounded command."""
    if os.name != "nt":
        return subprocess.run(
            [str(ngrok), *arguments], capture_output=True, text=True, timeout=timeout,
        )
    alias = _canonical_ngrok_alias(ngrok)
    if not alias:
        raise RuntimeError("ngrok is not the canonical package-bound App Execution Alias")
    return _run_packaged(
        [str(alias), *arguments], NGROK_WINDOWS_STORE_PACKAGE_FAMILY, timeout=timeout,
    )


def _trusted_msstore_source(winget: str | Path) -> bool:
    try:
        cp = _run_packaged(
            [str(winget), "source", "export", "msstore"],
            WINGET_WINDOWS_STORE_PACKAGE_FAMILY,
            timeout=30,
        )
        payload = json.loads(cp.stdout or "")
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
        return False
    if not isinstance(payload, dict):
        return False
    trust = payload.get("TrustLevel")
    return bool(
        cp.returncode == 0
        and payload.get("Name") == "msstore"
        and payload.get("Identifier") == "StoreEdgeFD"
        and payload.get("Arg") == "https://storeedgefd.dsx.mp.microsoft.com/v9.0"
        and payload.get("Type") == "Microsoft.Rest"
        and payload.get("Explicit") is False
        and isinstance(trust, list)
        and "Trusted" in trust
    )


def find_ngrok() -> str | None:
    """Remove obsolete cache and locate only the pinned Store package on Windows."""
    if os.name != "nt":
        return shutil.which("ngrok")
    cleanup = _remove_legacy_portable_ngrok()
    if cleanup["failed"] or cleanup["refused"]:
        print(
            "[5.1.0-SAFE] Obsolete portable ngrok cache could not be fully removed; "
            "it remains disabled and will not be executed.",
            file=sys.stderr,
        )
    alias = _windows_app_alias("ngrok.exe", NGROK_WINDOWS_STORE_PACKAGE_FAMILY)
    if alias and _probe_ngrok_executable(alias):
        return str(alias)
    return None


def download_portable_ngrok_windows() -> str:
    """Compatibility entry point: install the pinned Microsoft Store ngrok package.

    The historical function name is retained for existing callers. No executable
    or archive is downloaded by World Engine.
    """
    if os.name != "nt":
        found = find_ngrok()
        if found:
            return found
        raise RuntimeError("ngrok is not available in PATH")

    found = find_ngrok()
    if found:
        return found
    winget = _windows_app_alias("winget.exe", WINGET_WINDOWS_STORE_PACKAGE_FAMILY)
    if not winget or not _probe_winget_executable(winget) or not _trusted_msstore_source(winget):
        raise RuntimeError(
            "Microsoft App Installer/WinGet or its Microsoft Store source is unavailable or "
            "could not be verified. Repair App Installer, then rerun World Engine. World "
            "Engine will not download a standalone ngrok.exe."
        )
    command = [str(winget), *NGROK_WINDOWS_INSTALL_COMMAND[1:]]
    print("[5.1.0-SAFE] Installing the pinned ngrok package from Microsoft Store via WinGet...")
    try:
        cp = _run_packaged(command, WINGET_WINDOWS_STORE_PACKAGE_FAMILY, timeout=600)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            "Microsoft Store ngrok installation timed out. World Engine did not fall back "
            "to a direct executable download."
        ) from exc
    if cp.returncode != 0:
        combined = (str(cp.stdout or "") + "\n" + str(cp.stderr or "")).strip()
        raise RuntimeError(
            "Microsoft Store ngrok installation failed. World Engine did not fall back "
            "to a direct executable download.\n" + combined[-2000:]
        )
    for _ in range(40):
        found = find_ngrok()
        if found:
            return found
        time.sleep(0.25)
    raise RuntimeError(
        "The pinned Microsoft Store ngrok package installed, but its package-authenticated "
        "App Execution Alias is unavailable or disabled. Enable the ngrok alias in Windows "
        "App execution aliases and rerun World Engine; do not download a standalone ngrok.exe."
    )


def ngrok_config_path(data: Path | None = None) -> Path:
    data = data or persistent_data_dir()
    return data / "ngrok.yml"


def configure_ngrok_authtoken(ngrok: str, token: str, *, data: Path | None = None) -> Path:
    token = str(token or "").strip()
    if not NGROK_AUTHTOKEN_RE.fullmatch(token):
        raise ValueError("ngrok authtoken is missing or malformed")
    data = data or persistent_data_dir()
    cfg = ngrok_config_path(data)
    cfg.parent.mkdir(parents=True, exist_ok=True)
    tmp = cfg.with_suffix(cfg.suffix + ".tmp")
    tmp.write_text(f"version: 3\nauthtoken: {token}\n", encoding="utf-8")
    os.replace(tmp, cfg)
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
    if os.name == "nt":
        alias = _canonical_ngrok_alias(ngrok)
        if not alias:
            raise RuntimeError("ngrok is not the canonical package-bound App Execution Alias")
        ngrok = str(alias)
    current = ngrok_public_url(target_port=port)
    if current:
        if os.name == "nt" and not _trusted_ngrok_listener(NGROK_WEB_ADDR):
            raise RuntimeError(
                "an existing ngrok API listener is not the pinned Microsoft Store package; "
                "stop the old tunnel and rerun World Engine"
            )
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
    if os.name == "nt":
        family = _process_package_family(proc)
        if not family or family.casefold() != NGROK_WINDOWS_STORE_PACKAGE_FAMILY.casefold():
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            raise RuntimeError("started ngrok tunnel process package identity could not be verified")
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
    """Default endpoint: pinned Store ngrok + current-user Startup. No elevation required."""
    root = normalize_install_root(root)
    data = persistent_data_dir(); data.mkdir(parents=True, exist_ok=True)
    api_key = load_launcher_api_key(data)
    ngrok = find_ngrok()
    if not ngrok and allow_download:
        ngrok = download_portable_ngrok_windows()
    if not ngrok:
        raise RuntimeError(
            "Verified Microsoft Store ngrok is unavailable and automatic Store installation "
            "is disabled or unsupported. No standalone executable will be downloaded."
        )
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
    if provider == CLOUDFLARE_PROVIDER:
        # A named Cloudflare tunnel is installed as a Windows service. Its
        # installation token is deliberately not persisted, so recovery may
        # restart that service but must never attempt to reinstall it.
        if os.name != "nt":
            return {"status":"EXTERNAL_PROVIDER","provider":provider,"public_url":url}
        try:
            sc = _windows_system_executable("sc.exe")
            cp = run([sc, "start", "cloudflared"], timeout=60)
            combined = ((cp.stdout or "") + "\n" + (cp.stderr or "")).strip()
            already_running = any(
                marker in combined.lower()
                for marker in ("already running", "service has already been started", "error 1056")
            )
            if cp.returncode in (0, 1056) or already_running:
                return {
                    "status":"RUNNING",
                    "provider":provider,
                    "public_url":url,
                    "service":"cloudflared",
                }
            return {
                "status":"FAILED",
                "provider":provider,
                "public_url":url,
                "error":combined[-2000:] or f"sc start cloudflared exited {cp.returncode}",
            }
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
        print(f"[{VERSION}] Downloading pinned cloudflared {CLOUDFLARED_VERSION}...")
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
        run([_windows_system_executable("sc.exe"), "start", "cloudflared"], timeout=60)
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
