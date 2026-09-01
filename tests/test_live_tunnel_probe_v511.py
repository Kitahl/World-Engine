from __future__ import annotations

import ast
import importlib.util
import inspect
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "live_tunnel_probe_v511.py"
SPEC = importlib.util.spec_from_file_location("live_tunnel_probe_v511", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


class _Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def monotonic(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds


class LiveTunnelReadinessTests(unittest.TestCase):
    def test_probe_server_suppresses_only_expected_diagnostic_traceback_hook(self) -> None:
        server = object.__new__(probe.QuietThreadingHTTPServer)
        with mock.patch("socketserver.BaseServer.handle_error") as inherited:
            server.handle_error(object(), ("127.0.0.1", 1))
        inherited.assert_not_called()

    def test_transient_transport_error_is_retried_before_auth_contract(self) -> None:
        clock = _Clock()
        calls: list[tuple[str, str | None]] = []
        responses = iter(
            [
                {"status": None, "body": "", "redirected": None, "category": "transport_error"},
                {"status": 200, "body": "{}", "redirected": False, "category": "response"},
                {"status": 200, "body": "{}", "redirected": False, "category": "response"},
                {
                    "status": 401,
                    "body": probe.WRONG_KEY_BODY,
                    "redirected": False,
                    "category": "http_error",
                },
            ]
        )

        def request(url: str, *, api_key: str | None, timeout: float):
            del timeout
            calls.append((url, api_key))
            return next(responses)

        with mock.patch.object(probe, "_request", side_effect=request), \
             mock.patch.object(probe.time, "monotonic", side_effect=clock.monotonic), \
             mock.patch.object(probe.time, "sleep", side_effect=clock.sleep):
            ok, health, correct, wrong, readiness = probe._await_external_contract(
                "https://temporary.trycloudflare.com",
                api_key="temporary-correct-key",
                wrong_key="temporary-wrong-key",
                timeout_seconds=3,
            )

        self.assertTrue(ok)
        self.assertEqual(2, readiness["attempts"])
        self.assertEqual(["transport_error"], readiness["failure_categories"])
        self.assertEqual({"transport_error": 1}, readiness["failure_category_counts"])
        self.assertEqual(200, health["status"])
        self.assertEqual(200, correct["status"])
        self.assertEqual(401, wrong["status"])
        self.assertEqual(2, sum(1 for url, _ in calls if url.endswith("/health")))
        self.assertTrue(all("/api/context" not in url for url, _ in calls[:1]))

    def test_auth_is_never_called_when_public_health_never_becomes_ready(self) -> None:
        clock = _Clock()
        calls: list[str] = []

        def request(url: str, *, api_key: str | None, timeout: float):
            del api_key, timeout
            calls.append(url)
            return {"status": None, "body": "", "redirected": None, "category": "timeout"}

        with mock.patch.object(probe, "_request", side_effect=request), \
             mock.patch.object(probe.time, "monotonic", side_effect=clock.monotonic), \
             mock.patch.object(probe.time, "sleep", side_effect=clock.sleep):
            ok, health, correct, wrong, readiness = probe._await_external_contract(
                "https://temporary.trycloudflare.com",
                api_key="temporary-correct-key",
                wrong_key="temporary-wrong-key",
                timeout_seconds=1,
            )

        self.assertFalse(ok)
        self.assertEqual(1, readiness["attempts"])
        self.assertEqual(["timeout"], readiness["failure_categories"])
        self.assertEqual({"timeout": 1}, readiness["failure_category_counts"])
        self.assertIsNone(health["status"])
        self.assertIsNone(correct["status"])
        self.assertIsNone(wrong["status"])
        self.assertTrue(all(url.endswith("/health") for url in calls))


    def test_explicit_probe_data_root_never_consults_global_world_engine_data(self) -> None:
        endpoint = probe.endpoint
        fake_os = mock.Mock(wraps=endpoint.os)
        fake_os.name = "nt"

        with tempfile.TemporaryDirectory(prefix="we-v511-isolation-") as temp:
            data = Path(temp) / "probe-data"
            expected = (
                data
                / "tools"
                / f"cloudflared-{endpoint.CLOUDFLARED_VERSION}-windows-amd64.exe"
            )

            def retrieve(_url: str, target: str | Path) -> None:
                Path(target).write_bytes(b"isolated-pinned-helper")

            with (
                mock.patch.object(endpoint, "os", fake_os),
                mock.patch.object(
                    endpoint,
                    "persistent_data_dir",
                    side_effect=AssertionError("global WorldEngine data was consulted"),
                ) as global_data,
                mock.patch.object(
                    endpoint.urllib.request,
                    "urlretrieve",
                    side_effect=retrieve,
                ),
                mock.patch.object(
                    endpoint,
                    "sha256_file",
                    return_value=endpoint.CLOUDFLARED_WINDOWS_AMD64_SHA256,
                ),
            ):
                resolved = endpoint.automatic_cloudflared(
                    allow_download=True,
                    data=data,
                )

            self.assertEqual(expected, Path(resolved))
            self.assertTrue(expected.is_file())
            global_data.assert_not_called()

    def test_live_probe_forwards_its_temporary_data_root_to_resolver(self) -> None:
        tree = ast.parse(inspect.getsource(probe._run))
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "automatic_cloudflared"
        ]
        self.assertEqual(1, len(calls))
        data_keyword = next((item for item in calls[0].keywords if item.arg == "data"), None)
        self.assertIsNotNone(data_keyword)
        self.assertIsInstance(data_keyword.value, ast.Name)
        self.assertEqual("data", data_keyword.value.id)

if __name__ == "__main__":
    unittest.main()
