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

import json
import re
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

HEALTH_SERVICE = "world-engine"
HEALTH_STATUS = "ok"

# Recognized World Engine entry points. A python process on the port that is
# NOT running one of these is not ours and is never terminated.
ENTRY_SCRIPTS = ("app.py", "run_companion_demo.py", "world_engine_companion.py")
_UVICORN_RE = re.compile(r"(?:^|\s)-m\s+uvicorn\b.*\bapp:app\b")
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
    checks: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "reclaimed": self.reclaimed,
            "reason": self.reason,
            "pid": self.pid,
            "killed": self.killed,
            "graceful": self.graceful,
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


def is_world_engine_process(identity: ProcessIdentity) -> bool:
    """True only for a Python interpreter running a known entry point."""
    command = (identity.command_line or "").strip()
    if not command:
        return False
    executable = (identity.executable or "").strip()
    if executable and not _PYTHON_EXE_RE.search(executable):
        return False
    if not executable:
        # Fall back to the command line's own program token.
        head = command.split()[0].strip('"')
        if not _PYTHON_EXE_RE.search(head):
            return False
    if _UVICORN_RE.search(command):
        return True
    normalized = command.replace("\\", "/").lower()
    for script in ENTRY_SCRIPTS:
        if re.search(rf"(?:^|[\s/\"]){re.escape(script.lower())}(?:\s|\"|$)", normalized):
            return True
    return False


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

def _default_health_reader(url: str, timeout: float = 2.0) -> tuple[int, Any]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
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
            list(args), capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return completed.stdout or ""


def _default_netstat_reader(timeout: float = 8.0) -> str:
    return _run(["netstat", "-ano", "-p", "TCP"], timeout)


def _default_cim_reader(pid: int, timeout: float = 15.0) -> str:
    script = (
        f"Get-CimInstance Win32_Process -Filter 'ProcessId={int(pid)}' | "
        "Select-Object ProcessId,ExecutablePath,CommandLine,"
        "@{N='CreationDate';E={$_.CreationDate.ToString('o')}} | ConvertTo-Json -Compress"
    )
    return _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], timeout)


def _default_terminator(pid: int, force: bool, timeout: float = 15.0) -> int:
    args = ["taskkill", "/PID", str(int(pid)), "/T"]
    if force:
        args.append("/F")
    try:
        completed = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return 1
    return int(completed.returncode)


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #

class StaleBackendGuard:
    """Identify - and only then reclaim - a stale World Engine on a local port."""

    def __init__(
        self,
        *,
        port: int = 8000,
        health_reader: Callable[[str, float], tuple[int, Any]] | None = None,
        netstat_reader: Callable[[], str] | None = None,
        cim_reader: Callable[[int], str] | None = None,
        terminator: Callable[[int, bool], int] | None = None,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        self.port = int(port)
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
        if identity is None or not is_world_engine_process(identity):
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
        if not is_world_engine_process(before):
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

        if self._terminate(pid, False) == 0:
            report.graceful = True
        for _ in range(20):
            if not self._still_owns_port(pid):
                report.killed = True
                break
            self._sleep(0.25)

        if not report.killed:
            self._terminate(pid, True)
            for _ in range(20):
                if not self._still_owns_port(pid):
                    report.killed = True
                    break
                self._sleep(0.25)

        if not report.killed:
            report.reason = f"PID {pid} still owns port {self.port} after graceful and forced termination"
            return report

        report.checks.append("port_released")
        report.reclaimed = True
        report.reason = f"stopped stale World Engine backend (PID {pid})"
        return report

    def _still_owns_port(self, pid: int) -> bool:
        return pid in parse_listener_pids(self._netstat(), self.port)


def reclaim_stale_backend(port: int = 8000, **kwargs: Any) -> ReclaimReport:
    """Convenience wrapper used by startup and the launcher."""
    return StaleBackendGuard(port=port, **kwargs).reclaim()
