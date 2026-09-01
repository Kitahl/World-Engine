"""Verified reclaim of a stale World Engine backend on the loopback port.

The problem this solves
-----------------------
``world_engine_startup.start_backend`` spawns the API with ``DETACHED_PROCESS``
so it survives the launcher. When the launcher or companion closes without
stopping it, the backend keeps holding ``127.0.0.1:8000``. The next startup
generates a fresh API key, the protected probe against the old process fails,
and startup aborted with:

    StartupError: Port 8000 is occupied by a World Engine-compatible process
    using a different API key.

...leaving the user to find and kill a process by hand.

The safety problem
------------------
"Something is on port 8000, kill it" is unacceptable: the port may belong to an
unrelated program. So reclaiming is gated behind a chain of identity checks and
**fails closed** - any ambiguity means nothing is killed and the caller is told
why. The checks are deliberately conservative:

1. ``/health`` answers 200 with the exact payload ``status == "ok"`` and
   ``service == "world-engine"``.
2. The listener is bound to exact loopback ``127.0.0.1:<port>`` - not
   ``0.0.0.0``, not another interface.
3. Windows reports exactly one owning PID for that endpoint.
4. That PID is a Python process running a recognized World Engine entry point.
5. The PID, creation time, executable and command line are re-read immediately
   before termination, and must be unchanged - PIDs get recycled, and a stale
   read is how the wrong process gets killed.

Every OS interaction is injected, so the decision logic is testable without
spawning real processes or opening real sockets.
"""

from __future__ import annotations

import ctypes
import json
import ntpath
import os
import posixpath
import re
import shlex
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from ctypes import wintypes
from dataclasses import dataclass, field
from typing import Any

HEALTH_SERVICE = "world-engine"
HEALTH_STATUS = "ok"

# Recognized World Engine entry points. A python process on the port that is
# NOT running one of these is not ours and is never terminated.
ENTRY_SCRIPTS = ("app.py", "run_companion_demo.py")
_PYTHON_EXE_RE = re.compile(r"(?:^|[\\/])(?:python|pythonw|py)(?:\d+(?:\.\d+)?)?\.exe$", re.IGNORECASE)
_NETSTAT_RE = re.compile(
    r"^\s*TCP\s+(?P<local>\S+)\s+(?P<remote>\S+)\s+(?P<state>\w+)\s+(?P<pid>\d+)\s*$",
    re.IGNORECASE,
)


class ReclaimRefused(RuntimeError):
    """Raised when identity could not be established. No process was killed."""


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    executable: str = ""
    command_line: str = ""
    creation_time: str = ""

    def fingerprint(self) -> tuple[int, str, str, str]:
        """The tuple that must stay stable between decision and termination."""
        return (self.pid, self.executable.lower(), self.command_line, self.creation_time)


@dataclass
class ReclaimReport:
    reclaimed: bool = False
    reason: str = ""
    pid: int | None = None
    killed: bool = False
    graceful: bool = False
    graceful_attempted: bool = False
    force_attempted: bool = False
    port_released: bool = False
    checks: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "reclaimed": self.reclaimed,
            "reason": self.reason,
            "pid": self.pid,
            "killed": self.killed,
            "graceful": self.graceful,
            "graceful_attempted": self.graceful_attempted,
            "force_attempted": self.force_attempted,
            "port_released": self.port_released,
            "checks": list(self.checks),
        }


# --------------------------------------------------------------------------- #
# Pure parsers / classifiers - no OS access, fully unit-testable
# --------------------------------------------------------------------------- #

def health_identity_ok(payload: Any) -> bool:
    """Exact payload identity. A 200 alone proves only that *something* answers."""
    if not isinstance(payload, dict):
        return False
    return payload.get("status") == HEALTH_STATUS and payload.get("service") == HEALTH_SERVICE


def is_api_key_rejection(status: int | None, body: str) -> bool:
    """Recognize only the authoritative wrong-key response, never 5xx/timeouts."""
    if status != 401:
        return False
    try:
        payload = json.loads(body)
    except (TypeError, ValueError):
        return False
    return isinstance(payload, dict) and payload.get("detail") == "Invalid World Engine API key"


def parse_listener_pids(netstat_output: str, port: int) -> list[int]:
    """PIDs LISTENING on exact loopback ``127.0.0.1:<port>``.

    Binding to ``0.0.0.0`` or another address is deliberately not matched: the
    supported deployment is loopback-only, and a wildcard listener is a
    different program with different intent.
    """
    wanted = f"127.0.0.1:{port}"
    pids: list[int] = []
    for line in netstat_output.splitlines():
        match = _NETSTAT_RE.match(line)
        if not match:
            continue
        if match.group("state").upper() != "LISTENING":
            continue
        if match.group("local").strip() != wanted:
            continue
        pid = int(match.group("pid"))
        if pid > 0 and pid not in pids:
            pids.append(pid)
    return pids


def parse_port_listener_pids(netstat_output: str, port: int) -> list[int]:
    """Return every PID listening on a local address for ``port``.

    This broader parser is read-only and is used only to verify that shutdown
    released the port. Reclaim decisions continue to require exact 127.0.0.1.
    """
    pids: list[int] = []
    for line in netstat_output.splitlines():
        match = _NETSTAT_RE.match(line)
        if not match or match.group("state").upper() != "LISTENING":
            continue
        local = match.group("local").strip()
        if local.rsplit(":", 1)[-1] != str(port):
            continue
        pid = int(match.group("pid"))
        if pid > 0 and pid not in pids:
            pids.append(pid)
    return pids


def _path_key(value: str | os.PathLike[str]) -> str:
    raw = str(value).strip().strip('"').replace("\\", "/")
    if re.match(r"^[A-Za-z]:/", raw):
        return ntpath.normcase(ntpath.normpath(raw.replace("/", "\\"))).replace("\\", "/")
    return posixpath.normpath(raw).casefold()


def _owned_path(candidate: str, roots: Sequence[str]) -> bool:
    return any(candidate == root or candidate.startswith(root.rstrip("/") + "/") for root in roots)


def is_world_engine_process(
    identity: ProcessIdentity,
    authorized_roots: Sequence[str | os.PathLike[str]] = (),
) -> bool:
    """True only for a known entry point owned by an authorized install root."""
    roots = tuple(dict.fromkeys(_path_key(root) for root in authorized_roots if str(root).strip()))
    if not roots:
        return False
    command = (identity.command_line or "").strip()
    creation_time = (identity.creation_time or "").strip()
    if not command or not creation_time:
        return False
    executable = (identity.executable or "").strip()
    if not executable or not _PYTHON_EXE_RE.search(executable):
        return False
    try:
        tokens = shlex.split(command, posix=False)
    except ValueError:
        return False
    tokens = [token[1:-1] if len(token) >= 2 and token[0] == token[-1] == '"' else token for token in tokens]
    if len(tokens) < 2:
        return False
    arguments = tokens[1:]
    raw_entrypoint = arguments[0]
    entrypoint = raw_entrypoint.replace("\\", "/").rsplit("/", 1)[-1].casefold()
    if entrypoint not in ENTRY_SCRIPTS:
        return False
    absolute = bool(re.match(r"^[A-Za-z]:[\\/]", raw_entrypoint) or raw_entrypoint.startswith("/"))
    if not absolute:
        return False
    entry_key = _path_key(raw_entrypoint)
    parent = entry_key.rsplit("/", 1)[0] if "/" in entry_key else ""
    return parent in roots


def parse_process_identity(pid: int, cim_output: str) -> ProcessIdentity | None:
    """Parse ``Get-CimInstance Win32_Process`` JSON for one PID."""
    text = (cim_output or "").strip()
    if not text:
        return None
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return None
    if isinstance(data, list):
        data = next((row for row in data if str(row.get("ProcessId")) == str(pid)), None)
    if not isinstance(data, dict):
        return None
    if str(data.get("ProcessId")) != str(pid):
        return None
    return ProcessIdentity(
        pid=pid,
        executable=str(data.get("ExecutablePath") or ""),
        command_line=str(data.get("CommandLine") or ""),
        creation_time=str(data.get("CreationDate") or ""),
    )


# --------------------------------------------------------------------------- #
# Default OS adapters - each is injectable so tests never touch the machine
# --------------------------------------------------------------------------- #

class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Bind a health result to the exact local listener instead of a redirect."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def open_no_redirect(request: str | urllib.request.Request, timeout: float):
    return urllib.request.build_opener(_NoRedirectHandler()).open(request, timeout=timeout)


def _default_health_reader(url: str, timeout: float = 2.0) -> tuple[int, Any]:
    try:
        with open_no_redirect(url, timeout) as response:
            body = response.read(8192).decode("utf-8", "replace")
            try:
                payload = json.loads(body)
            except ValueError:
                payload = None
            return int(response.status), payload
    except urllib.error.HTTPError as exc:
        return int(exc.code), None
    except Exception:
        return 0, None


def _run(args: Sequence[str], timeout: float) -> str:
    try:
        completed = subprocess.run(
            list(args),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if completed.returncode != 0:
        return ""
    return completed.stdout or ""


def _trusted_windows_tool(relative_path: str) -> str:
    """Resolve an OS utility from System32, never cwd or ambient PATH."""
    if os.name != "nt":
        return relative_path
    capacity = 32768
    buffer = ctypes.create_unicode_buffer(capacity)
    get_system_directory = ctypes.windll.kernel32.GetSystemDirectoryW
    get_system_directory.argtypes = [wintypes.LPWSTR, wintypes.UINT]
    get_system_directory.restype = wintypes.UINT
    length = int(get_system_directory(buffer, capacity))
    if length == 0 or length >= capacity:
        return ""
    system32 = os.path.abspath(buffer.value)
    candidate = os.path.abspath(os.path.join(system32, relative_path))
    if os.path.commonpath((system32, candidate)) != system32 or not os.path.isfile(candidate):
        return ""
    return candidate


def _default_netstat_reader(timeout: float = 8.0) -> str:
    tool = _trusted_windows_tool("netstat.exe")
    return _run([tool, "-ano", "-p", "TCP"], timeout) if tool else ""


def _default_cim_reader(pid: int, timeout: float = 15.0) -> str:
    powershell = _trusted_windows_tool("WindowsPowerShell\\v1.0\\powershell.exe")
    if not powershell:
        return ""
    script = (
        f"Get-CimInstance Win32_Process -Filter 'ProcessId={int(pid)}' | "
        "Select-Object ProcessId,ExecutablePath,CommandLine,"
        "@{N='CreationDate';E={$_.CreationDate.ToString('o')}} | ConvertTo-Json -Compress"
    )
    return _run([powershell, "-NoProfile", "-NonInteractive", "-Command", script], timeout)


def _default_terminator(pid: int, force: bool, timeout: float = 15.0) -> int:
    taskkill = _trusted_windows_tool("taskkill.exe")
    if not taskkill:
        return 1
    args = [taskkill, "/PID", str(int(pid)), "/T"]
    if force:
        args.append("/F")
    try:
        completed = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
        )
    except (OSError, subprocess.SubprocessError):
        return 1
    return int(completed.returncode)


def world_engine_health_ok(url: str, timeout: float = 2.0) -> bool:
    """Exact, non-redirecting World Engine health recognition."""
    status, payload = _default_health_reader(url, timeout)
    return status == 200 and health_identity_ok(payload)


def active_listener_pids(port: int) -> list[int] | None:
    """Read-only port-release check; ``None`` means the OS query failed."""
    output = _default_netstat_reader()
    if not output.strip():
        return None
    return parse_port_listener_pids(output, int(port))


def terminate_owned_process_tree(process: subprocess.Popen[Any], timeout: float = 5.0) -> bool:
    """Terminate a subprocess the caller itself created, including its children."""
    if process.poll() is not None:
        return True
    if os.name == "nt":
        termination_rc = _default_terminator(int(process.pid), True, timeout)
        if termination_rc != 0:
            return False
    else:
        try:
            process.terminate()
        except OSError:
            pass
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
            process.wait(timeout=timeout)
        except (OSError, subprocess.TimeoutExpired):
            return False
    return process.poll() is not None


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #

class StaleBackendGuard:
    """Identify - and only then reclaim - a stale World Engine on a local port."""

    def __init__(
        self,
        *,
        port: int = 8000,
        authorized_roots: Sequence[str | os.PathLike[str]] = (),
        health_reader: Callable[[str, float], tuple[int, Any]] | None = None,
        netstat_reader: Callable[[], str] | None = None,
        cim_reader: Callable[[int], str] | None = None,
        terminator: Callable[[int, bool], int] | None = None,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        self.port = int(port)
        self._authorized_roots = tuple(authorized_roots)
        self._health = health_reader or _default_health_reader
        self._netstat = netstat_reader or _default_netstat_reader
        self._cim = cim_reader or _default_cim_reader
        self._terminate = terminator or _default_terminator
        self._sleep = sleeper or time.sleep

    # -- identity ---------------------------------------------------------- #

    def health_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/health"

    def responds_as_world_engine(self) -> bool:
        status, payload = self._health(self.health_url(), 2.0)
        return status == 200 and health_identity_ok(payload)

    def listener_pid(self) -> int | None:
        pids = parse_listener_pids(self._netstat(), self.port)
        if len(pids) != 1:
            return None
        return pids[0]

    def identify(self) -> ProcessIdentity | None:
        pid = self.listener_pid()
        if pid is None:
            return None
        identity = parse_process_identity(pid, self._cim(pid))
        if identity is None or not is_world_engine_process(identity, self._authorized_roots):
            return None
        return identity

    # -- reclaim ----------------------------------------------------------- #

    def reclaim(self) -> ReclaimReport:
        """Stop a verified stale World Engine backend. Fails closed."""
        report = ReclaimReport()

        if not self.responds_as_world_engine():
            report.reason = "port does not answer with the World Engine health identity"
            return report
        report.checks.append("health_identity")

        pids = parse_listener_pids(self._netstat(), self.port)
        if len(pids) != 1:
            report.reason = (
                "no single owning PID for exact loopback listener "
                f"127.0.0.1:{self.port} (found {len(pids)})"
            )
            return report
        pid = pids[0]
        report.pid = pid
        report.checks.append("single_loopback_listener")

        before = parse_process_identity(pid, self._cim(pid))
        if before is None:
            report.reason = f"could not read process identity for PID {pid}"
            return report
        if not is_world_engine_process(before, self._authorized_roots):
            report.reason = f"PID {pid} is not a recognized World Engine entry point"
            return report
        report.checks.append("recognized_entry_point")

        # Re-read immediately before terminating: between the decision and the
        # kill the PID may have been recycled by an unrelated process.
        after = parse_process_identity(pid, self._cim(pid))
        if after is None or after.fingerprint() != before.fingerprint():
            report.reason = f"process identity for PID {pid} changed before termination"
            return report
        report.checks.append("stable_identity")

        report.graceful_attempted = True
        graceful_rc = self._terminate(pid, False)
        if graceful_rc == 0:
            report.graceful = True
            for _ in range(20):
                if self._port_is_free():
                    report.killed = True
                    break
                self._sleep(0.25)

        if not report.killed:
            # A graceful attempt and its bounded wait create a second PID-reuse
            # window. Re-establish the complete identity immediately before the
            # destructive /F escalation; never force-kill on a stale fingerprint.
            before_force = parse_process_identity(pid, self._cim(pid))
            if (
                before_force is None
                or before_force.fingerprint() != after.fingerprint()
                or not is_world_engine_process(before_force, self._authorized_roots)
            ):
                report.reason = f"process identity for PID {pid} changed before forced termination"
                return report
            report.checks.append("stable_identity_before_force")
            report.force_attempted = True
            force_rc = self._terminate(pid, True)
            if force_rc != 0:
                report.reason = f"forced process-tree termination failed for PID {pid}"
                return report
            for _ in range(20):
                if self._port_is_free():
                    report.killed = True
                    break
                self._sleep(0.25)

        if not report.killed:
            report.reason = f"port {self.port} remains occupied after graceful and forced termination of PID {pid}"
            return report

        report.checks.append("port_released")
        report.port_released = True
        report.reclaimed = True
        report.reason = f"stopped stale World Engine backend (PID {pid})"
        return report

    def _port_is_free(self) -> bool:
        output = self._netstat()
        if not output.strip():
            return False
        return parse_port_listener_pids(output, self.port) == []


def reclaim_stale_backend(port: int = 8000, **kwargs: Any) -> ReclaimReport:
    """Convenience wrapper used by startup and the launcher."""
    return StaleBackendGuard(port=port, **kwargs).reclaim()
