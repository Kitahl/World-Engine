"""World Engine 5.1.0 — stale-backend reclaim gates.

Every OS interaction in ``StaleBackendGuard`` is injected, so these tests drive
the decision logic directly: which processes are recognized, which are refused,
and what happens when identity shifts under us. The refusal cases matter more
than the success case — the danger is killing something that merely happens to
own the port.
"""

from __future__ import annotations

import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from world_engine.process_guard import (
    ProcessIdentity,
    StaleBackendGuard,
    _trusted_windows_tool,
    health_identity_ok,
    is_api_key_rejection,
    parse_listener_pids,
    parse_port_listener_pids,
    parse_process_identity,
    terminate_owned_process_tree,
    world_engine_health_ok,
)
from world_engine.process_guard import (
    is_world_engine_process as classify_process,
)

NETSTAT_SINGLE = """
Active Connections

  Proto  Local Address          Foreign Address        State           PID
  TCP    0.0.0.0:135            0.0.0.0:0              LISTENING       1892
  TCP    127.0.0.1:8000         0.0.0.0:0              LISTENING       4242
  TCP    127.0.0.1:8000         127.0.0.1:51515        ESTABLISHED     7777
  TCP    [::1]:8000             [::]:0                 LISTENING       9999
"""

AUTHORIZED_ROOTS = (r"C:\WE",)


def is_world_engine_process(identity: ProcessIdentity) -> bool:
    return classify_process(identity, AUTHORIZED_ROOTS)


NETSTAT_WILDCARD = """
  Proto  Local Address          Foreign Address        State           PID
  TCP    0.0.0.0:8000           0.0.0.0:0              LISTENING       5150
"""

NETSTAT_NONE = """
  Proto  Local Address          Foreign Address        State           PID
  TCP    127.0.0.1:9999         0.0.0.0:0              LISTENING       1234
"""

CIM_APP = (
    '{"ProcessId":4242,"ExecutablePath":"C:\\\\WE\\\\.venv\\\\Scripts\\\\python.exe",'
    '"CommandLine":"\\"C:\\\\WE\\\\.venv\\\\Scripts\\\\python.exe\\" \\"C:\\\\WE\\\\app.py\\"",'
    '"CreationDate":"2026-08-31T10:00:00.0000000Z"}'
)
CIM_UNRELATED = (
    '{"ProcessId":4242,"ExecutablePath":"C:\\\\Program Files\\\\nginx\\\\nginx.exe",'
    '"CommandLine":"nginx.exe -g daemon off;","CreationDate":"2026-08-31T10:00:00.0000000Z"}'
)


def health_ok(url: str, timeout: float = 2.0):
    return 200, {"status": "ok", "service": "world-engine"}


def health_wrong_service(url: str, timeout: float = 2.0):
    return 200, {"status": "ok", "service": "some-other-app"}


def health_dead(url: str, timeout: float = 2.0):
    return 0, None


class ParserTests(unittest.TestCase):
    def test_exact_loopback_listener_only(self) -> None:
        self.assertEqual([4242], parse_listener_pids(NETSTAT_SINGLE, 8000))

    def test_wildcard_bind_is_not_matched(self) -> None:
        self.assertEqual([], parse_listener_pids(NETSTAT_WILDCARD, 8000))

    def test_absent_port_yields_nothing(self) -> None:
        self.assertEqual([], parse_listener_pids(NETSTAT_NONE, 8000))

    def test_established_connections_are_ignored(self) -> None:
        self.assertNotIn(7777, parse_listener_pids(NETSTAT_SINGLE, 8000))

    def test_read_only_port_parser_finds_any_listener_address(self) -> None:
        self.assertEqual([5150], parse_port_listener_pids(NETSTAT_WILDCARD, 8000))
        self.assertEqual([4242, 9999], parse_port_listener_pids(NETSTAT_SINGLE, 8000))
        self.assertNotIn(7777, parse_port_listener_pids(NETSTAT_SINGLE, 8000))

    def test_health_identity_requires_exact_payload(self) -> None:
        self.assertTrue(health_identity_ok({"status": "ok", "service": "world-engine"}))
        self.assertFalse(health_identity_ok({"status": "ok", "service": "other"}))
        self.assertFalse(health_identity_ok({"status": "degraded", "service": "world-engine"}))
        self.assertFalse(health_identity_ok({}))
        self.assertFalse(health_identity_ok(None))
        self.assertFalse(health_identity_ok("ok"))

    def test_api_key_rejection_requires_authoritative_401_payload(self) -> None:
        exact = '{"detail":"Invalid World Engine API key"}'
        self.assertTrue(is_api_key_rejection(401, exact))
        self.assertFalse(is_api_key_rejection(500, exact))
        self.assertFalse(is_api_key_rejection(401, '{"detail":"different"}'))
        self.assertFalse(is_api_key_rejection(401, "not-json"))
        self.assertFalse(is_api_key_rejection(None, exact))

    def test_health_check_does_not_follow_redirects(self) -> None:
        class HealthHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = b'{"status":"ok","service":"world-engine"}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format, *_args):
                return

        health = ThreadingHTTPServer(("127.0.0.1", 0), HealthHandler)
        target = f"http://127.0.0.1:{health.server_port}/health"

        class RedirectHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(302)
                self.send_header("Location", target)
                self.end_headers()

            def log_message(self, _format, *_args):
                return

        redirect = ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
        threads = [
            threading.Thread(target=health.serve_forever, daemon=True),
            threading.Thread(target=redirect.serve_forever, daemon=True),
        ]
        for thread in threads:
            thread.start()
        try:
            self.assertTrue(world_engine_health_ok(target))
            self.assertFalse(
                world_engine_health_ok(f"http://127.0.0.1:{redirect.server_port}/health")
            )
        finally:
            redirect.shutdown()
            health.shutdown()
            redirect.server_close()
            health.server_close()
            for thread in threads:
                thread.join(timeout=2)

    def test_process_identity_parses_cim_json(self) -> None:
        identity = parse_process_identity(4242, CIM_APP)
        assert identity is not None
        self.assertEqual(4242, identity.pid)
        self.assertTrue(identity.executable.lower().endswith("python.exe"))
        self.assertIn("app.py", identity.command_line)

    def test_process_identity_rejects_pid_mismatch(self) -> None:
        self.assertIsNone(parse_process_identity(9999, CIM_APP))

    def test_process_identity_rejects_garbage(self) -> None:
        self.assertIsNone(parse_process_identity(4242, "not json"))
        self.assertIsNone(parse_process_identity(4242, ""))


class ClassifierTests(unittest.TestCase):
    def _identity(self, command: str, executable: str = r"C:\WE\.venv\Scripts\python.exe"):
        return ProcessIdentity(pid=1, executable=executable, command_line=command, creation_time="t")

    def test_recognizes_app_py(self) -> None:
        self.assertTrue(is_world_engine_process(self._identity(r'"python.exe" "C:\WE\app.py"')))

    def test_recognizes_companion_demo(self) -> None:
        self.assertTrue(is_world_engine_process(self._identity(r'python.exe C:\WE\run_companion_demo.py')))

    def test_refuses_ambiguous_relative_app_py(self) -> None:
        self.assertFalse(is_world_engine_process(self._identity("python.exe app.py")))

    def test_refuses_ambiguous_uvicorn_module(self) -> None:
        self.assertFalse(is_world_engine_process(self._identity("python.exe -m uvicorn app:app --port 8000")))

    def test_refuses_unrelated_python_script(self) -> None:
        self.assertFalse(is_world_engine_process(self._identity(r"python.exe C:\other\server.py")))

    def test_refuses_non_python_executable(self) -> None:
        self.assertFalse(
            is_world_engine_process(
                self._identity("nginx.exe -g daemon off;", executable=r"C:\nginx\nginx.exe")
            )
        )

    def test_refuses_uvicorn_for_another_app(self) -> None:
        self.assertFalse(is_world_engine_process(self._identity("python.exe -m uvicorn other:app")))

    def test_refuses_empty_command_line(self) -> None:
        self.assertFalse(is_world_engine_process(self._identity("")))

    def test_refuses_missing_executable_or_creation_time(self) -> None:
        self.assertFalse(is_world_engine_process(self._identity("python.exe app.py", executable="")))
        self.assertFalse(
            is_world_engine_process(
                ProcessIdentity(
                    pid=1,
                    executable=r"C:\WE\.venv\Scripts\python.exe",
                    command_line="python.exe app.py",
                    creation_time="",
                )
            )
        )

    def test_does_not_match_app_py_as_substring(self) -> None:
        self.assertFalse(is_world_engine_process(self._identity(r"python.exe C:\x\notapp.pyc")))

    def test_refuses_app_py_in_a_later_unrelated_argument(self) -> None:
        self.assertFalse(
            is_world_engine_process(
                self._identity(r"python.exe C:\other\server.py --config C:\cfg\app.py")
            )
        )

    def test_refuses_app_py_inside_python_code(self) -> None:
        self.assertFalse(is_world_engine_process(self._identity("python.exe -c app.py")))

    def test_refuses_absolute_app_py_outside_authorized_roots(self) -> None:
        identity = self._identity(
            r'"C:\unrelated\.venv\Scripts\python.exe" "C:\unrelated\app.py"',
            executable=r"C:\unrelated\.venv\Scripts\python.exe",
        )
        self.assertFalse(classify_process(identity, AUTHORIZED_ROOTS))

    def test_refuses_generic_uvicorn_from_unrelated_interpreter(self) -> None:
        identity = self._identity(
            r'"C:\unrelated\.venv\Scripts\python.exe" -m uvicorn app:app',
            executable=r"C:\unrelated\.venv\Scripts\python.exe",
        )
        self.assertFalse(classify_process(identity, AUTHORIZED_ROOTS))

    def test_refuses_every_process_without_authorized_roots(self) -> None:
        self.assertFalse(classify_process(self._identity(r"python.exe C:\WE\app.py")))


class ReclaimTests(unittest.TestCase):
    def _guard(self, **overrides):
        killed: list[tuple[int, bool]] = []
        state = {"owns": True}

        def netstat() -> str:
            return NETSTAT_SINGLE if state["owns"] else NETSTAT_NONE

        def cim(pid: int) -> str:
            return CIM_APP

        def terminator(pid: int, force: bool) -> int:
            killed.append((pid, force))
            if force or state.get("graceful_works", True):
                state["owns"] = False
                return 0
            return 1

        kwargs = dict(
            port=8000,
            health_reader=health_ok,
            authorized_roots=AUTHORIZED_ROOTS,
            netstat_reader=netstat,
            cim_reader=cim,
            terminator=terminator,
            sleeper=lambda _s: None,
        )
        kwargs.update(overrides)
        return StaleBackendGuard(**kwargs), killed, state

    def test_reclaims_verified_stale_backend(self) -> None:
        guard, killed, _ = self._guard()
        report = guard.reclaim()
        self.assertTrue(report.reclaimed)
        self.assertTrue(report.killed)
        self.assertEqual(4242, report.pid)
        self.assertTrue(report.graceful_attempted)
        self.assertFalse(report.force_attempted)
        self.assertTrue(report.port_released)
        self.assertEqual([(4242, False)], killed, "should stop gracefully without forcing")
        for gate in ("health_identity", "single_loopback_listener", "recognized_entry_point",
                     "stable_identity", "port_released"):
            self.assertIn(gate, report.checks)

    def test_escalates_to_forced_termination(self) -> None:
        guard, killed, state = self._guard()
        state["graceful_works"] = False
        report = guard.reclaim()
        self.assertTrue(report.reclaimed)
        self.assertTrue(report.graceful_attempted)
        self.assertTrue(report.force_attempted)
        self.assertTrue(report.port_released)
        self.assertIn((4242, True), killed, "forced termination must follow a failed graceful stop")

    def test_refuses_when_health_identity_is_wrong(self) -> None:
        guard, killed, _ = self._guard(health_reader=health_wrong_service)
        report = guard.reclaim()
        self.assertFalse(report.reclaimed)
        self.assertEqual([], killed, "must never kill a process that is not World Engine")
        self.assertIn("health identity", report.reason)

    def test_refuses_when_nothing_answers(self) -> None:
        guard, killed, _ = self._guard(health_reader=health_dead)
        report = guard.reclaim()
        self.assertFalse(report.reclaimed)
        self.assertEqual([], killed)

    def test_refuses_wildcard_listener(self) -> None:
        guard, killed, _ = self._guard(netstat_reader=lambda: NETSTAT_WILDCARD)
        report = guard.reclaim()
        self.assertFalse(report.reclaimed)
        self.assertEqual([], killed)
        self.assertIn("single owning PID", report.reason)

    def test_refuses_unrelated_process_on_the_port(self) -> None:
        guard, killed, _ = self._guard(cim_reader=lambda pid: CIM_UNRELATED)
        report = guard.reclaim()
        self.assertFalse(report.reclaimed)
        self.assertEqual([], killed, "owning the port is not grounds for termination")
        self.assertIn("not a recognized World Engine entry point", report.reason)

    def test_refuses_when_pid_is_recycled_between_reads(self) -> None:
        reads = {"n": 0}

        def flipping_cim(pid: int) -> str:
            reads["n"] += 1
            return CIM_APP if reads["n"] <= 1 else CIM_UNRELATED

        guard, killed, _ = self._guard(cim_reader=flipping_cim)
        report = guard.reclaim()
        self.assertFalse(report.reclaimed)
        self.assertEqual([], killed, "a changed identity must abort before any kill")
        self.assertIn("changed before termination", report.reason)

    def test_refuses_when_pid_is_recycled_before_forced_termination(self) -> None:
        reads = {"n": 0}

        def flipping_cim(pid: int) -> str:
            reads["n"] += 1
            return CIM_APP if reads["n"] <= 2 else CIM_UNRELATED

        guard, killed, state = self._guard(cim_reader=flipping_cim)
        state["graceful_works"] = False
        report = guard.reclaim()
        self.assertFalse(report.reclaimed)
        self.assertEqual([(4242, False)], killed, "must not force-kill a recycled PID")
        self.assertTrue(report.graceful_attempted)
        self.assertFalse(report.force_attempted)
        self.assertFalse(report.port_released)
        self.assertIn("changed before forced termination", report.reason)

    def test_reports_failure_when_port_never_releases(self) -> None:
        def stubborn(pid: int, force: bool) -> int:
            return 0

        guard, _killed, _ = self._guard(terminator=stubborn)
        report = guard.reclaim()
        self.assertFalse(report.reclaimed)
        self.assertIn("remains occupied", report.reason)

    def test_pid_handoff_does_not_count_as_port_release(self) -> None:
        state = {"handoff": False}

        def netstat() -> str:
            if state["handoff"]:
                return NETSTAT_SINGLE.replace("4242", "5150")
            return NETSTAT_SINGLE

        def terminator(_pid: int, _force: bool) -> int:
            state["handoff"] = True
            return 0

        guard, _killed, _ = self._guard(netstat_reader=netstat, terminator=terminator)
        report = guard.reclaim()
        self.assertFalse(report.reclaimed)
        self.assertFalse(report.port_released)
        self.assertNotIn("port_released", report.checks)

    @unittest.skipUnless(sys.platform == "win32", "Windows trusted-system-directory check")
    def test_systemroot_environment_cannot_redirect_trusted_tools(self) -> None:
        with patch.dict("os.environ", {"SystemRoot": r"C:\attacker"}):
            resolved = _trusted_windows_tool("netstat.exe")
        self.assertTrue(resolved.lower().endswith(r"\system32\netstat.exe"))
        self.assertNotIn(r"c:\attacker", resolved.lower())

    @unittest.skipUnless(sys.platform == "win32", "Windows taskkill return-code check")
    def test_failed_taskkill_is_never_reported_as_cleaned(self) -> None:
        process = Mock(pid=4242)
        process.poll.return_value = None
        with patch(
            "world_engine.process_guard._default_terminator",
            return_value=1,
        ) as terminator:
            cleaned = terminate_owned_process_tree(process)
        self.assertFalse(cleaned)
        terminator.assert_called_once()
        process.wait.assert_not_called()

    def test_failed_force_kill_cannot_claim_success_if_port_then_frees(self) -> None:
        state = {"released": False}

        def netstat() -> str:
            return NETSTAT_NONE if state["released"] else NETSTAT_SINGLE

        def terminator(_pid: int, force: bool) -> int:
            if force:
                state["released"] = True
            return 1

        guard, _killed, _state = self._guard(
            netstat_reader=netstat,
            terminator=terminator,
        )
        report = guard.reclaim()
        self.assertFalse(report.reclaimed)
        self.assertFalse(report.port_released)
        self.assertIn("forced process-tree termination failed", report.reason)

    def test_report_is_serializable(self) -> None:
        guard, _killed, _ = self._guard()
        payload = guard.reclaim().as_dict()
        self.assertIn("reclaimed", payload)
        self.assertIn("checks", payload)
        self.assertIn("graceful_attempted", payload)
        self.assertIn("force_attempted", payload)
        self.assertIn("port_released", payload)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
