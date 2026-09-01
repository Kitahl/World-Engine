from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
import unittest
from contextlib import ExitStack
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import world_engine_startup as startup

VALID_TOKEN = "2abcdefghijk_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


class LocalFirstStartupTests(unittest.TestCase):
    def test_retained_venv_without_pywebview_triggers_dependency_sync(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            py = root / ".venv" / "Scripts" / "python.exe"
            py.parent.mkdir(parents=True)
            py.write_bytes(b"placeholder")
            (root / "requirements.txt").write_text("pywebview>=6.2.1,<7\n", encoding="utf-8")
            with patch.object(startup, "venv_python", return_value=py), \
                 patch.object(startup, "run_text", return_value=SimpleNamespace(returncode=1)) as probe, \
                 patch.object(startup.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as install:
                result = startup.ensure_runtime_python(root, status=lambda _message: None)
        self.assertEqual(py, result)
        self.assertIn("webview", probe.call_args.args[0][-1])
        self.assertIn(str(root / "requirements.txt"), install.call_args.args[0])

    def _root(self, temp: Path) -> tuple[Path, Path]:
        root = temp / "world-engine"
        data = temp / "persistent"
        root.mkdir()
        (root / "data").mkdir()
        (root / "app.py").write_text("", encoding="utf-8")
        return root, data

    def _startup_patches(
        self,
        stack: ExitStack,
        data: Path,
        *,
        endpoint_result: dict | None = None,
        endpoint_error: Exception | None = None,
        key_created: bool = False,
        events: list[str] | None = None,
    ) -> tuple[object, object, object]:
        events = events if events is not None else []
        stack.enter_context(patch.object(startup, "persistent_data_dir", return_value=data))
        stack.enter_context(patch.object(startup, "migrate_legacy_data", return_value={"copied": []}))
        stack.enter_context(patch.object(startup, "auto_migrate_from_previous_install", return_value={"status": "NONE"}))
        stack.enter_context(patch.object(startup, "install_environment"))
        stack.enter_context(patch.object(startup, "ensure_launcher_config", return_value=("world-engine-key", key_created)))
        stack.enter_context(patch.object(startup, "ensure_runtime_python", return_value=Path("python.exe")))
        stack.enter_context(patch.object(startup, "register_current_install"))
        backend = stack.enter_context(
            patch.object(
                startup,
                "start_backend",
                side_effect=lambda *_args, **_kwargs: events.append("backend") or {"status": "STARTED"},
            )
        )
        ui = stack.enter_context(
            patch.object(
                startup,
                "launch_companion_ui",
                side_effect=lambda *_args, **_kwargs: events.append("desktop"),
            )
        )
        stack.enter_context(
            patch.object(
                startup,
                "launch_launcher",
                side_effect=lambda *_args, **_kwargs: events.append("launcher"),
            )
        )
        if endpoint_error is not None:
            endpoint = stack.enter_context(
                patch.object(
                    startup,
                    "ensure_endpoint",
                    side_effect=lambda *_args, **_kwargs: (
                        events.append("endpoint"),
                        (_ for _ in ()).throw(endpoint_error),
                    )[1],
                )
            )
        else:
            endpoint = stack.enter_context(
                patch.object(
                    startup,
                    "ensure_endpoint",
                    side_effect=lambda *_args, **_kwargs: events.append("endpoint") or dict(endpoint_result or {}),
                )
            )
        stack.enter_context(patch.object(startup, "load_permanent_config", return_value={}))
        stack.enter_context(patch.object(startup, "install_combined_user_startup", return_value={"status": "INSTALLED"}))
        stack.enter_context(patch.object(startup, "start_supervisor_process", return_value={"status": "STARTED"}))
        stack.enter_context(patch.object(startup, "reveal_file", return_value=True))
        return backend, ui, endpoint

    def test_missing_auth_keeps_local_engine_and_desktop_ready(self):
        with tempfile.TemporaryDirectory() as td, ExitStack() as stack:
            root, data = self._root(Path(td))
            events: list[str] = []
            _backend, ui, endpoint = self._startup_patches(
                stack,
                data,
                endpoint_error=startup.EndpointAuthRequired("missing"),
                events=events,
            )
            verify = stack.enter_context(patch.object(startup, "verify_endpoint"))
            copy = stack.enter_context(patch.object(startup, "clipboard_write"))
            result = startup.automatic_startup(root, status=lambda _message: None)
            local_ready_exists = Path(result["local_ready_file"]).is_file()

        self.assertEqual("DEGRADED", result["status"])
        self.assertEqual(startup.EndpointStatus.AUTH_REQUIRED.value, result["endpoint"]["status"])
        self.assertEqual(["backend", "desktop", "endpoint"], events)
        ui.assert_called_once_with(root.resolve(), Path("python.exe"))
        self.assertFalse(endpoint.call_args.kwargs["interactive"])
        verify.assert_not_called()
        copy.assert_not_called()
        self.assertIsNone(result["ready_file"])
        self.assertTrue(local_ready_exists)

    def test_clipboard_timeout_is_degraded_and_main_exits_zero(self):
        with tempfile.TemporaryDirectory() as td, ExitStack() as stack:
            root, data = self._root(Path(td))
            self._startup_patches(
                stack,
                data,
                endpoint_error=startup.EndpointAuthTimeout("timeout"),
            )
            stack.enter_context(patch.object(startup, "verify_endpoint"))
            result = startup.automatic_startup(root, status=lambda _message: None)
        self.assertEqual("DEGRADED", result["status"])
        self.assertEqual("NGROK_AUTH_TIMEOUT", result["endpoint"]["error_code"])

        with tempfile.TemporaryDirectory() as td, \
             patch.object(startup.sys, "argv", ["world_engine_startup.py"]), \
             patch.object(startup, "automatic_startup", return_value=result), \
             patch.object(startup, "persistent_data_dir", return_value=Path(td)), \
             patch("builtins.print"):
            self.assertEqual(0, startup.main())

    def test_reused_verified_endpoint_is_pass(self):
        with tempfile.TemporaryDirectory() as td, ExitStack() as stack:
            root, data = self._root(Path(td))
            schema = root / "openapi_actions_PERMANENT.json"
            schema.write_text("{}", encoding="utf-8")
            self._startup_patches(
                stack,
                data,
                endpoint_result={
                    "status": startup.EndpointStatus.READY.value,
                    "provider": "ngrok_user",
                    "public_url": "https://stable.ngrok-free.app",
                    "schema": str(schema),
                    "reused": True,
                },
            )
            stack.enter_context(
                patch.object(startup, "verify_endpoint", return_value={"health_ok": True, "protected_auth_ok": True})
            )
            copy = stack.enter_context(patch.object(startup, "clipboard_write"))
            result = startup.automatic_startup(root, status=lambda _message: None)
            ready_exists = Path(result["ready_file"]).is_file()
        self.assertEqual("PASS", result["status"])
        self.assertEqual(startup.EndpointStatus.READY.value, result["endpoint"]["status"])
        self.assertTrue(ready_exists)
        copy.assert_not_called()

    def test_final_public_verification_failure_can_never_report_pass(self):
        with tempfile.TemporaryDirectory() as td, ExitStack() as stack:
            root, data = self._root(Path(td))
            self._startup_patches(
                stack,
                data,
                endpoint_result={
                    "status": startup.EndpointStatus.READY.value,
                    "provider": "ngrok_user",
                    "public_url": "https://stable.ngrok-free.app",
                    "schema": str(root / "schema.json"),
                    "reused": True,
                },
            )
            stack.enter_context(
                patch.object(startup, "verify_endpoint", return_value={"health_ok": False, "protected_auth_ok": False})
            )
            result = startup.automatic_startup(root, status=lambda _message: None)
        self.assertEqual("DEGRADED", result["status"])
        self.assertEqual(startup.EndpointStatus.FAILED.value, result["endpoint"]["status"])
        self.assertEqual("ENDPOINT_VERIFICATION_FAILED", result["endpoint"]["error_code"])

    def test_invalid_one_time_token_is_closed_and_does_not_touch_ngrok(self):
        with patch.object(startup, "find_ngrok") as find, patch.object(startup, "configure_ngrok_authtoken") as configure:
            result = startup.configure_ngrok_token_once("not-a-token")
        self.assertEqual(startup.EndpointStatus.AUTH_REQUIRED.value, result["status"])
        self.assertEqual("NGROK_AUTH_INVALID", result["error_code"])
        self.assertNotIn("not-a-token", json.dumps(result))
        find.assert_not_called()
        configure.assert_not_called()

    def test_rejected_one_time_token_restores_config_and_success_returns_fingerprint_only(self):
        with tempfile.TemporaryDirectory() as td:
            data = Path(td)
            config = data / "ngrok.yml"
            original = b"version: 3\nauthtoken: prior-safe-token-value-12345\n"
            config.write_bytes(original)
            with patch.object(startup, "persistent_data_dir", return_value=data), \
                 patch.object(startup, "find_ngrok", return_value="trusted-ngrok"), \
                 patch.object(startup, "ngrok_config_path", return_value=config), \
                 patch.object(startup, "configure_ngrok_authtoken"), \
                 patch.object(startup, "validate_ngrok_config", return_value=(False, "rejected")), \
                 patch.object(startup, "clipboard_read", return_value=""):
                rejected = startup.configure_ngrok_token_once(VALID_TOKEN)
            self.assertEqual(startup.EndpointStatus.AUTH_REQUIRED.value, rejected["status"])
            self.assertEqual(original, config.read_bytes())

            cleared: list[str] = []
            with patch.object(startup, "persistent_data_dir", return_value=data), \
                 patch.object(startup, "find_ngrok", return_value="trusted-ngrok"), \
                 patch.object(startup, "ngrok_config_path", return_value=config), \
                 patch.object(startup, "configure_ngrok_authtoken"), \
                 patch.object(startup, "validate_ngrok_config", return_value=(True, "valid")), \
                 patch.object(startup, "clipboard_read", return_value=VALID_TOKEN), \
                 patch.object(startup, "clipboard_write", side_effect=cleared.append):
                accepted = startup.configure_ngrok_token_once(VALID_TOKEN)
            self.assertEqual(startup.EndpointStatus.READY.value, accepted["status"])
            self.assertEqual(startup.api_key_fingerprint(VALID_TOKEN), accepted["token_fingerprint"])
            self.assertNotIn(VALID_TOKEN, json.dumps(accepted))
            self.assertEqual([""], cleared)

    def test_companion_lock_is_per_user_nonce_owned_and_recovers_stale_receipt(self):
        with tempfile.TemporaryDirectory() as td:
            data = Path(td)
            receipt = data / startup.COMPANION_INSTANCE_RECEIPT
            startup.atomic_json(receipt, {"instance_id": "stale", "pid": 999999})
            first = startup.claim_companion_instance(
                data,
                entrypoint=Path("world_engine_companion.py"),
                executable=Path("pythonw.exe"),
            )
            self.assertIsNotNone(first)
            assert first is not None
            current = json.loads(receipt.read_text(encoding="utf-8"))
            self.assertNotEqual("stale", current["instance_id"])
            self.assertEqual(os.getpid(), current["pid"])
            self.assertIsNone(
                startup.claim_companion_instance(
                    data,
                    entrypoint=Path("world_engine_companion.py"),
                    executable=Path("pythonw.exe"),
                )
            )

            # A stale/foreign receipt must never be removed by the old owner.
            startup.atomic_json(receipt, {"instance_id": "new-owner"})
            startup.release_companion_instance(first)
            self.assertTrue(receipt.is_file())

            recovered = startup.claim_companion_instance(
                data,
                entrypoint=Path("world_engine_companion.py"),
                executable=Path("pythonw.exe"),
            )
            self.assertIsNotNone(recovered)
            assert recovered is not None
            self.assertNotEqual(
                "new-owner",
                json.loads(receipt.read_text(encoding="utf-8"))["instance_id"],
            )
            startup.release_companion_instance(recovered)
            self.assertFalse(receipt.exists())

    def test_concurrent_companion_claims_have_exactly_one_winner(self):
        with tempfile.TemporaryDirectory() as td:
            data = Path(td)
            barrier = threading.Barrier(8)

            def claim(_index: int):
                barrier.wait(timeout=5)
                return startup.claim_companion_instance(
                    data,
                    entrypoint=Path("world_engine_companion.py"),
                    executable=Path("pythonw.exe"),
                )

            with ThreadPoolExecutor(max_workers=8) as pool:
                claims = list(pool.map(claim, range(8)))
            winners = [item for item in claims if item is not None]
            self.assertEqual(1, len(winners))
            startup.release_companion_instance(winners[0])

    def test_repeated_startup_does_not_spawn_when_exact_companion_lock_is_held(self):
        with tempfile.TemporaryDirectory() as td:
            temp = Path(td)
            root = temp / "install"
            data = temp / "data"
            root.mkdir()
            (root / "world_engine_companion.py").write_text("", encoding="utf-8")
            claim = startup.claim_companion_instance(
                data,
                entrypoint=root / "world_engine_companion.py",
                executable=Path("pythonw.exe"),
            )
            self.assertIsNotNone(claim)
            assert claim is not None
            try:
                with patch.object(startup, "persistent_data_dir", return_value=data), patch.object(
                    startup.subprocess, "Popen"
                ) as popen:
                    result = startup.launch_companion_ui(root, Path("python.exe"))
            finally:
                startup.release_companion_instance(claim)
        self.assertEqual("ALREADY_RUNNING", result["status"])
        popen.assert_not_called()

    def test_companion_receives_no_api_secret_in_environment_or_argv(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "launcher.py").write_text("", encoding="utf-8")
            (root / "world_engine_companion.py").write_text("", encoding="utf-8")
            secrets = {
                "WORLD_ENGINE_API_KEY": "secret-api-key",
                "WORLD_ENGINE_ADMIN_KEY": "secret-admin-key",
                "NGROK_AUTHTOKEN": "secret-ngrok-token",
                "CLOUDFLARE_TUNNEL_TOKEN": "secret-cloudflare-token",
                "TAILSCALE_AUTHKEY": "secret-tailscale-key",
            }
            with patch.dict(os.environ, secrets, clear=False), \
                 patch.object(startup, "persistent_data_dir", return_value=Path(td)), \
                 patch.object(startup, "companion_instance_running", return_value=False), \
                 patch.object(startup.subprocess, "Popen") as popen:
                popen.return_value.pid = 42
                startup.launch_companion_ui(root, Path("python.exe"))
        self.assertEqual(1, popen.call_count)
        companion_call = popen.call_args
        self.assertNotIn("secret-api-key", companion_call.args[0])
        child_env = companion_call.kwargs["env"]
        for name, secret in secrets.items():
            self.assertNotIn(name, child_env)
            self.assertNotIn(secret, child_env.values())
        self.assertIn("PATH", child_env)
        self.assertIs(subprocess.DEVNULL, companion_call.kwargs["stdout"])
        self.assertIs(subprocess.DEVNULL, companion_call.kwargs["stderr"])
        self.assertEqual(0, companion_call.kwargs.get("creationflags", 0) & getattr(subprocess, "CREATE_NEW_CONSOLE", 0))


if __name__ == "__main__":
    unittest.main()
