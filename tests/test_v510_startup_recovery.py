"""World Engine 5.1 startup recovery integration and live Windows lifecycle tests."""

from __future__ import annotations

import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import world_engine_startup as startup
from world_engine.process_guard import (
    ReclaimReport,
    reclaim_stale_backend,
)

ROOT = Path(__file__).resolve().parents[1]


class StartupRecoveryWiringTests(unittest.TestCase):
    def test_start_backend_reclaims_auth_mismatch_then_starts(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data = root / "data"
            process = Mock(pid=7331, returncode=None)
            process.poll.return_value = None
            with patch.object(startup, "local_health", side_effect=[True, True]), \
                 patch.object(
                     startup,
                     "probe",
                     side_effect=[(False, 401, '{"detail": "Invalid World Engine API key"}'), (True, 200, "ok")],
                 ), \
                 patch.object(
                     startup,
                     "reclaim_stale_backend",
                     return_value=ReclaimReport(
                         reclaimed=True,
                         reason="stopped stale World Engine backend",
                         pid=4242,
                     ),
                 ) as reclaim, \
                 patch.object(
                     startup,
                     "load_json",
                     return_value={"admin_key": "admin-key-0123456789-abcdef"},
                 ), \
                 patch.object(startup.subprocess, "Popen", return_value=process) as popen, \
                 patch.object(startup.time, "sleep", return_value=None):
                result = startup.start_backend(
                    root,
                    data,
                    "api-key-0123456789-abcdefgh",
                    Path(sys.executable),
                    status=lambda _message: None,
                )

        self.assertEqual("STARTED", result["status"])
        reclaim.assert_called_once_with(8000)
        popen.assert_called_once()

    def test_start_backend_never_starts_after_cleanup_refusal(self) -> None:
        with tempfile.TemporaryDirectory() as td, \
             patch.object(startup, "local_health", return_value=True), \
             patch.object(startup, "probe", return_value=(False, 401, '{"detail": "Invalid World Engine API key"}')), \
             patch.object(
                 startup,
                 "reclaim_stale_backend",
                 return_value=ReclaimReport(
                     reclaimed=False,
                     reason="listener is not a recognized World Engine process",
                     pid=4242,
                 ),
             ), \
             patch.object(startup.subprocess, "Popen") as popen:
            with self.assertRaisesRegex(startup.StartupError, "No process was terminated"):
                startup.start_backend(
                    Path(td),
                    Path(td) / "data",
                    "api-key-0123456789-abcdefgh",
                    Path(sys.executable),
                    status=lambda _message: None,
                )
        popen.assert_not_called()

    def test_unexpected_protected_failure_never_reclaims_or_starts(self) -> None:
        with tempfile.TemporaryDirectory() as td, \
             patch.object(startup, "local_health", return_value=True), \
             patch.object(startup, "probe", return_value=(False, 500, "internal error")), \
             patch.object(startup, "reclaim_stale_backend") as reclaim, \
             patch.object(startup.subprocess, "Popen") as popen:
            with self.assertRaisesRegex(startup.StartupError, "automatic termination was not attempted"):
                startup.start_backend(
                    Path(td),
                    Path(td) / "data",
                    "api-key-0123456789-abcdefgh",
                    Path(sys.executable),
                    status=lambda _message: None,
                )
        reclaim.assert_not_called()
        popen.assert_not_called()

    def test_spawned_backend_is_cleaned_when_authentication_fails(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data = root / "data"
            process = Mock(pid=7331, returncode=None)
            process.poll.return_value = None
            with patch.object(startup, "local_health", side_effect=[False, True]), \
                 patch.object(startup, "probe", return_value=(False, 500, "internal error")), \
                 patch.object(
                     startup,
                     "load_json",
                     return_value={"admin_key": "admin-key-0123456789-abcdef"},
                 ), \
                 patch.object(startup.subprocess, "Popen", return_value=process), \
                 patch.object(startup, "terminate_owned_process_tree", return_value=True) as terminate:
                with self.assertRaisesRegex(startup.StartupError, "protected auth failed"):
                    startup.start_backend(
                        root,
                        data,
                        "api-key-0123456789-abcdefgh",
                        Path(sys.executable),
                        status=lambda _message: None,
                    )
        terminate.assert_called_once_with(process)

    def test_spawned_backend_is_cleaned_after_health_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data = root / "data"
            process = Mock(pid=7331, returncode=None)
            process.poll.return_value = None
            with patch.object(startup, "local_health", return_value=False), \
                 patch.object(
                     startup,
                     "load_json",
                     return_value={"admin_key": "admin-key-0123456789-abcdef"},
                 ), \
                 patch.object(startup.subprocess, "Popen", return_value=process), \
                 patch.object(startup.time, "sleep", return_value=None), \
                 patch.object(startup, "terminate_owned_process_tree", return_value=True) as terminate:
                with self.assertRaisesRegex(startup.StartupError, "did not become healthy"):
                    startup.start_backend(
                        root,
                        data,
                        "api-key-0123456789-abcdefgh",
                        Path(sys.executable),
                        status=lambda _message: None,
                    )
        terminate.assert_called_once_with(process)


@unittest.skipUnless(os.name == "nt", "Windows process ownership integration")
class LiveWindowsRecoveryTests(unittest.TestCase):
    def test_real_world_engine_listener_is_reclaimed_and_port_released(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = int(reservation.getsockname()[1])

        process: subprocess.Popen[bytes] | None = None
        with tempfile.TemporaryDirectory() as td:
            data = Path(td)
            env = os.environ.copy()
            env.update(
                {
                    "WORLD_ENGINE_DATA_DIR": str(data),
                    "WORLD_ENGINE_DB": str(data / "live.sqlite3"),
                    "WORLD_ENGINE_API_KEY": secrets.token_urlsafe(32),
                    "WORLD_ENGINE_ADMIN_KEY": secrets.token_urlsafe(32),
                    "WORLD_ENGINE_HOST": "127.0.0.1",
                    "PORT": str(port),
                }
            )
            try:
                process = subprocess.Popen(
                    [sys.executable, str(ROOT / "app.py")],
                    cwd=ROOT,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                health_url = f"http://127.0.0.1:{port}/health"
                deadline = time.monotonic() + 30.0
                payload = b""
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        self.fail(f"live app exited before health check: {process.returncode}")
                    try:
                        with urllib.request.urlopen(health_url, timeout=1.0) as response:
                            payload = response.read()
                        break
                    except Exception:
                        time.sleep(0.1)
                self.assertIn(b'"service":"world-engine"', payload.replace(b" ", b""))

                report = reclaim_stale_backend(port)
                self.assertTrue(report.reclaimed, report.as_dict())
                # A Windows venv python.exe can be a redirector whose PID is
                # different from the base-Python child that owns the socket.
                # The listener PID is authoritative; the wrapper must still exit.
                self.assertIsNotNone(report.pid)
                process.wait(timeout=10)
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                    self.assertNotEqual(0, probe.connect_ex(("127.0.0.1", port)))
            finally:
                if process is not None and process.poll() is None:
                    subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        capture_output=True,
                        timeout=10,
                        check=False,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )


if __name__ == "__main__":
    unittest.main()
