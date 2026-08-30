from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import world_engine_startup as startup
import world_engine_permanent_endpoint as endpoint


VALID_TOKEN_A = "2abcdefghijk_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
VALID_TOKEN_B = "3abcdefghijk_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


class AutomaticStartupTests(unittest.TestCase):
    def test_token_candidate_rejects_commands_urls_and_short_values(self):
        self.assertEqual(VALID_TOKEN_A, startup.token_candidate(VALID_TOKEN_A))
        self.assertIsNone(startup.token_candidate("short"))
        self.assertIsNone(startup.token_candidate("https://dashboard.ngrok.com/token/value"))
        self.assertIsNone(startup.token_candidate("ngrok config add-authtoken " + VALID_TOKEN_A))

    def test_clipboard_capture_requires_no_paste_and_skips_rejected_value(self):
        rejected = startup.api_key_fingerprint(VALID_TOKEN_A)
        reads = iter([VALID_TOKEN_A, VALID_TOKEN_B])
        opened: list[str] = []
        messages: list[str] = []
        value = startup.acquire_ngrok_token_from_clipboard(
            timeout_seconds=30,
            rejected_fingerprints={rejected},
            read_clipboard=lambda: next(reads, VALID_TOKEN_B),
            open_browser=lambda url: opened.append(url),
            sleep=lambda _: None,
            status=messages.append,
        )
        self.assertEqual(VALID_TOKEN_B, value)
        self.assertEqual([startup.AUTHTOKEN_URL], opened)
        self.assertTrue(any("Copy button" in message for message in messages))

    def test_existing_ngrok_config_is_discovered_and_copied(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data = root / "persistent"
            source = root / "default-ngrok.yml"
            source.write_text("version: 3\nauthtoken: " + VALID_TOKEN_A + "\n", encoding="utf-8")
            destination = data / "ngrok.yml"
            with patch.object(startup, "default_ngrok_config_candidates", return_value=[source]), \
                 patch.object(startup, "ngrok_config_path", return_value=destination), \
                 patch.object(startup, "validate_ngrok_config", return_value=(True, "valid")):
                result = startup.adopt_existing_ngrok_config("ngrok", data)
            self.assertEqual("EXISTING_CONFIG", result["status"])
            self.assertEqual(source.read_bytes(), destination.read_bytes())

    def test_environment_token_is_configured_without_interactive_prompt(self):
        with tempfile.TemporaryDirectory() as td:
            data = Path(td)
            with patch.dict(os.environ, {"NGROK_AUTHTOKEN": VALID_TOKEN_A}, clear=False), \
                 patch.object(startup, "adopt_existing_ngrok_config", return_value=None), \
                 patch.object(startup, "configure_ngrok_authtoken") as configure, \
                 patch.object(startup, "validate_ngrok_config", return_value=(True, "valid")):
                result = startup.ensure_ngrok_authentication("ngrok", data, interactive=False)
            self.assertEqual("ENV_TOKEN", result["status"])
            configure.assert_called_once_with("ngrok", VALID_TOKEN_A, data=data)

    def test_endpoint_repair_requires_original_stable_hostname(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data = root / "data"
            expected = "https://assigned-domain.ngrok-free.app"
            installed = {
                "status": "PASS", "provider": "ngrok_user", "public_url": expected,
                "schema": str(root / "openapi_actions_PERMANENT.json"),
            }
            with patch.object(startup, "load_permanent_config", return_value={"public_url": expected, "provider": "ngrok_user"}), \
                 patch.object(startup, "ensure_permanent_runtime", return_value={"status": "FAILED"}), \
                 patch.object(startup, "verify_endpoint", return_value={"health_ok": False, "protected_auth_ok": False}), \
                 patch.object(startup, "find_ngrok", return_value="ngrok.exe"), \
                 patch.object(startup, "ensure_ngrok_authentication", return_value={"status": "EXISTING_CONFIG"}), \
                 patch.object(startup, "install_ngrok_from_config", return_value=installed) as install:
                result = startup.ensure_endpoint(root, data, "world-engine-key", interactive=False, allow_download=False)
            self.assertEqual(expected, result["public_url"])
            install.assert_called_once_with(root, data, "world-engine-key", "ngrok.exe", expected_url=expected)

    def test_combined_user_startup_is_one_ordered_no_admin_entry(self):
        with tempfile.TemporaryDirectory() as td:
            temp = Path(td)
            root = temp / "install"
            root.mkdir()
            (root / "world_engine_startup.py").write_text("# startup\n", encoding="utf-8")
            python_exe = temp / "python.exe"
            python_exe.write_text("", encoding="utf-8")
            appdata = temp / "AppData" / "Roaming"
            result = startup.install_combined_user_startup(
                root, python_exe, temp / "data", platform_name="nt", appdata_dir=appdata,
            )
            vbs = Path(result["startup"])
            text = vbs.read_text(encoding="utf-8")
            self.assertIn("world_engine_startup.py", text)
            self.assertIn("--non-interactive", text)
            self.assertEqual(["backend", "permanent_endpoint", "verification", "continuous_supervision"], result["ordered"])
            self.assertIn("--supervise", text)
            self.assertFalse(result.get("requires_admin", False))

    def test_automatic_startup_retrieves_key_starts_all_layers_and_writes_receipt(self):
        with tempfile.TemporaryDirectory() as td:
            temp = Path(td)
            root = temp / "world-engine"
            data = temp / "persistent"
            root.mkdir(); (root / "data").mkdir(); (root / "app.py").write_text("", encoding="utf-8")
            schema = root / "openapi_actions_PERMANENT.json"
            schema.write_text("{}", encoding="utf-8")
            copied: list[str] = []
            logs: list[str] = []
            endpoint_result = {
                "status": "PASS", "provider": "ngrok_user",
                "public_url": "https://assigned.ngrok-free.app",
                "schema": str(schema), "verification": {"health_ok": True, "protected_auth_ok": True},
                "reused": False,
            }
            with patch.object(startup, "persistent_data_dir", return_value=data), \
                 patch.object(startup, "migrate_legacy_data", return_value={"copied": []}), \
                 patch.object(startup, "auto_migrate_from_previous_install", return_value={"status": "NONE"}), \
                 patch.object(startup, "install_environment"), \
                 patch.object(startup, "ensure_runtime_python", return_value=Path("python.exe")), \
                 patch.object(startup, "register_current_install"), \
                 patch.object(startup, "start_backend", return_value={"status": "STARTED"}) as backend, \
                 patch.object(startup, "ensure_endpoint", return_value=endpoint_result) as endpoint_call, \
                 patch.object(startup, "install_combined_user_startup", return_value={"status": "INSTALLED"}), \
                 patch.object(startup, "start_supervisor_process", return_value={"status": "STARTED", "pid": 99}) as supervisor, \
                 patch.object(startup, "verify_endpoint", return_value={"health_ok": True, "protected_auth_ok": True}), \
                 patch.object(startup, "clipboard_write", side_effect=lambda value: copied.append(value) or True), \
                 patch.object(startup, "reveal_file", return_value=True), \
                 patch.object(startup, "launch_launcher"):
                result = startup.automatic_startup(root, status=logs.append)
            config = json.loads((data / "launcher_config.json").read_text(encoding="utf-8"))
            self.assertEqual("PASS", result["status"])
            self.assertEqual(config["api_key"], copied[0])
            backend.assert_called_once()
            endpoint_call.assert_called_once()
            supervisor.assert_called_once()
            self.assertTrue((data / "last_startup_result.json").is_file())
            self.assertTrue((data / "GPT_ACTION_SETUP_READY.txt").is_file())
            self.assertTrue(result["api_key_copied_to_clipboard"])

    def test_supervisor_cycle_repairs_backend_endpoint_and_verifies(self):
        with tempfile.TemporaryDirectory() as td:
            temp = Path(td)
            root = temp / "install"; root.mkdir()
            data = temp / "persistent"; data.mkdir()
            py = startup.venv_python(root); py.parent.mkdir(parents=True); py.write_text("")
            with patch.object(startup, "persistent_data_dir", return_value=data), \
                 patch.object(startup, "install_environment"), \
                 patch.object(startup, "ensure_launcher_config", return_value=("world-engine-key", False)), \
                 patch.object(startup, "register_current_install"), \
                 patch.object(startup, "start_backend", return_value={"status": "ALREADY_RUNNING"}) as backend, \
                 patch.object(startup, "ensure_endpoint", return_value={"status":"PASS","public_url":"https://assigned.ngrok-free.app"}) as endpoint_call, \
                 patch.object(startup, "verify_endpoint", return_value={"health_ok":True,"protected_auth_ok":True}):
                result = startup.supervisor_cycle(root)
            self.assertEqual("PASS", result["status"])
            backend.assert_called_once()
            endpoint_call.assert_called_once()
            self.assertTrue((data / "supervisor_status.json").is_file())

    def test_start_and_install_scripts_have_no_getpass_or_input_prompt(self):
        root = Path(startup.__file__).resolve().parent
        combined = "\n".join(
            (root / name).read_text(encoding="utf-8", errors="replace")
            for name in ("world_engine_startup.py", "INSTALL_PERMANENT_ENDPOINT_V400.py", "START_WORLD_ENGINE.bat")
        )
        self.assertNotIn("getpass", combined)
        self.assertNotRegex(combined, r"\binput\s*\(")
        self.assertIn("world_engine_startup.py", (root / "START_WORLD_ENGINE.bat").read_text(encoding="utf-8"))

    def test_compact_gpt_instructions_fit_limit_and_require_router_and_fail_closed_connection(self):
        root = Path(startup.__file__).resolve().parent
        instructions = (root / "CUSTOM_GPT_INSTRUCTIONS_V420.txt").read_text(encoding="utf-8")
        self.assertLessEqual(len(instructions.encode("utf-8")), 8000)
        self.assertIn("resolveTurn", instructions)
        self.assertIn("_narrative_render_packet", instructions)
        self.assertIn("quality_check", instructions)
        self.assertIn("record_output", instructions)
        self.assertIn("PLAYER AUTHORSHIP", instructions)
        self.assertIn("expected_revision", instructions)
        self.assertIn("idempotency_key", instructions)
        self.assertIn("ClientResponseError", instructions)
        self.assertIn("STOP", instructions)

    def test_official_ngrok_download_host_is_used(self):
        self.assertTrue(endpoint.NGROK_WINDOWS_AMD64_URL.startswith("https://bin.ngrok.com/"))


if __name__ == "__main__":
    unittest.main()
