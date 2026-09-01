from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import world_engine_startup as startup
import world_engine_permanent_endpoint as endpoint
from scripts import release_verify_v470 as release_verify


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

    def test_windows_clipboard_timeout_falls_back_and_circuit_breaks(self):
        timeout = subprocess.TimeoutExpired(["powershell.exe", "Get-Clipboard"], 5)
        with patch.object(startup, "_CLIPBOARD_READ_HOST_FAILURES", {}), \
             patch.object(startup.os, "name", "nt"), \
             patch.object(
                 startup.shutil,
                 "which",
                 return_value=r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
             ), \
             patch.object(startup, "run_text", side_effect=timeout) as run, \
             patch.object(startup, "_tk_clipboard_read_bounded", return_value="fallback value") as fallback:
            self.assertEqual("fallback value", startup.clipboard_read())
            self.assertEqual("fallback value", startup.clipboard_read())
        run.assert_called_once()
        self.assertEqual(2, fallback.call_count)

    def test_windows_clipboard_reader_requests_sta_mode(self):
        completed = subprocess.CompletedProcess([], 0, "clipboard value\r\n", "")
        with patch.object(startup.os, "name", "nt"), \
             patch.object(
                 startup.shutil,
                 "which",
                 return_value=r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
             ), \
             patch.object(startup, "run_text", return_value=completed) as run:
            self.assertEqual("clipboard value", startup.clipboard_read())
        command = run.call_args.args[0]
        self.assertIn("-Sta", command)
        self.assertEqual(5, run.call_args.kwargs["timeout"])

    def test_windows_clipboard_write_timeout_falls_back_and_circuit_breaks(self):
        timeout = subprocess.TimeoutExpired(["powershell.exe", "Set-Clipboard"], 5)
        with patch.object(startup, "_CLIPBOARD_WRITE_HOST_FAILURES", {}), \
             patch.object(startup.os, "name", "nt"), \
             patch.object(
                 startup.shutil,
                 "which",
                 return_value=r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
             ), \
             patch.object(startup.subprocess, "run", side_effect=timeout) as run, \
             patch.object(startup, "_tk_clipboard_write_bounded", return_value=True) as fallback:
            self.assertTrue(startup.clipboard_write("secret value"))
            self.assertTrue(startup.clipboard_write("secret value"))
        run.assert_called_once()
        self.assertEqual(2, fallback.call_count)

    def test_clipboard_capture_recovers_after_shell_timeout(self):
        timeout = subprocess.TimeoutExpired(["powershell.exe", "Get-Clipboard"], 5)
        opened: list[str] = []
        with patch.object(startup, "_CLIPBOARD_READ_HOST_FAILURES", {}), \
             patch.object(startup.os, "name", "nt"), \
             patch.object(
                 startup.shutil,
                 "which",
                 return_value=r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
             ), \
             patch.object(startup, "run_text", side_effect=timeout) as run, \
             patch.object(startup, "_tk_clipboard_read_bounded", side_effect=["", VALID_TOKEN_A]):
            token = startup.acquire_ngrok_token_from_clipboard(
                timeout_seconds=30,
                read_clipboard=startup.clipboard_read,
                open_browser=opened.append,
                sleep=lambda _: None,
                status=lambda _: None,
            )
        self.assertEqual(VALID_TOKEN_A, token)
        self.assertEqual([startup.AUTHTOKEN_URL], opened)
        run.assert_called_once()

    def test_tk_clipboard_helpers_are_bounded_and_nonfatal(self):
        timeout = subprocess.TimeoutExpired([startup.sys.executable, "-c"], 5)
        with patch.object(startup.subprocess, "run", side_effect=timeout) as run:
            self.assertEqual("", startup._tk_clipboard_read_bounded())
            self.assertFalse(startup._tk_clipboard_write_bounded("secret value"))
        self.assertEqual(2, run.call_count)
        for call in run.call_args_list:
            self.assertEqual(startup._CLIPBOARD_HELPER_TIMEOUT_SECONDS, call.kwargs["timeout"])

    def test_main_failure_banner_uses_current_version(self):
        with tempfile.TemporaryDirectory() as td, \
             patch.object(startup.sys, "argv", ["world_engine_startup.py"]), \
             patch.object(startup, "automatic_startup", side_effect=startup.StartupError("expected failure")), \
             patch.object(startup, "persistent_data_dir", return_value=Path(td)), \
             patch.object(startup, "write_startup_receipt"), \
             patch("builtins.print") as output:
            self.assertEqual(1, startup.main())
        rendered = "\n".join(
            str(call.args[0])
            for call in output.call_args_list
            if call.args
        )
        self.assertIn(f"WORLD ENGINE {startup.VERSION} STARTUP FAILED", rendered)
        self.assertNotIn("WORLD ENGINE 4.2 STARTUP FAILED", rendered)

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

    def test_existing_ngrok_config_import_drops_noncredential_fields(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data = root / "persistent"
            source = root / "default-ngrok.yml"
            source.write_text(
                "version: 3\n"
                "authtoken: " + VALID_TOKEN_A + "\n"
                "proxy_url: http://127.0.0.1:9999\n"
                "web_addr: 0.0.0.0:4040\n",
                encoding="utf-8",
            )
            destination = data / "ngrok.yml"
            with patch.object(startup, "default_ngrok_config_candidates", return_value=[source]), \
                 patch.object(startup, "ngrok_config_path", return_value=destination), \
                 patch.object(startup, "validate_ngrok_config", return_value=(True, "valid")):
                result = startup.adopt_existing_ngrok_config("ngrok", data)
            imported = destination.read_text(encoding="utf-8")
            self.assertEqual("EXISTING_CONFIG", result["status"])
            self.assertIn("authtoken: " + VALID_TOKEN_A, imported)
            self.assertNotIn("proxy_url", imported)
            self.assertNotIn("web_addr", imported)

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

    def test_endpoint_repair_never_crosses_from_cloudflare_to_ngrok(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data = root / "data"
            expected = "https://worldengine.example.com"
            with patch.object(startup, "load_permanent_config", return_value={
                    "public_url": expected,
                    "provider": endpoint.CLOUDFLARE_PROVIDER,
                 }), \
                 patch.object(startup, "ensure_permanent_runtime", return_value={
                    "status": "FAILED",
                    "provider": endpoint.CLOUDFLARE_PROVIDER,
                 }), \
                 patch.object(startup, "verify_endpoint", return_value={
                    "health_ok": False,
                    "protected_auth_ok": False,
                 }), \
                 patch.object(startup, "find_ngrok") as find_ngrok:
                with self.assertRaisesRegex(startup.StartupError, "refusing to replace or impersonate"):
                    startup.ensure_endpoint(
                        root,
                        data,
                        "world-engine-key",
                        interactive=False,
                        allow_download=False,
                    )
            find_ngrok.assert_not_called()

    def test_endpoint_repair_rejects_missing_or_unknown_provider_identity(self):
        for configured_provider in ("", "unknown_provider"):
            with self.subTest(provider=configured_provider), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                data = root / "data"
                expected = "https://worldengine.example.com"
                with patch.object(startup, "load_permanent_config", return_value={
                        "public_url": expected,
                        "provider": configured_provider,
                     }), \
                     patch.object(startup, "ensure_permanent_runtime", return_value={
                        "status": "EXTERNAL_PROVIDER",
                        "provider": configured_provider,
                     }), \
                     patch.object(startup, "verify_endpoint", return_value={
                        "health_ok": False,
                        "protected_auth_ok": False,
                     }), \
                     patch.object(startup, "find_ngrok") as find_ngrok:
                    with self.assertRaisesRegex(startup.StartupError, "refusing to replace or impersonate"):
                        startup.ensure_endpoint(
                            root,
                            data,
                            "world-engine-key",
                            interactive=False,
                            allow_download=False,
                        )
                find_ngrok.assert_not_called()

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
            self.assertEqual(f"World Engine {startup.VERSION} automatic startup", config["created_by"])
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

    def test_current_compact_gpt_instructions_fit_limit_and_require_router_publication_and_fail_closed_connection(self):
        root = Path(startup.__file__).resolve().parent
        instructions = (root / "CUSTOM_GPT_INSTRUCTIONS_V470.txt").read_text(encoding="utf-8")
        self.assertLessEqual(len(instructions.encode("utf-8")), 8000)
        self.assertIn("resolveTurn", instructions)
        self.assertIn("publishPresentation", instructions)
        self.assertIn("NRP-1.2", instructions)
        self.assertIn("_narrative_render_packet", instructions)
        self.assertIn("PLAYER AUTHORSHIP", instructions)
        self.assertIn("expected_revision", instructions)
        self.assertIn("idempotency_key", instructions)
        self.assertIn("on backend/authentication failure, stop", instructions.lower())
        self.assertIn("semantic_review_required", instructions)
        self.assertIn("rejected", instructions)

    def test_release_verifier_audits_active_v470_instructions(self):
        result = release_verify.source_audit()
        self.assertTrue(result["passed"])
        self.assertEqual("CUSTOM_GPT_INSTRUCTIONS_V470.txt", result["active_instruction_file"])
        self.assertEqual([], result["missing_active_instruction_markers"])
        self.assertEqual(64, len(result["active_instruction_sha256"]))

    def test_ngrok_uses_pinned_microsoft_store_distribution(self):
        command = endpoint.NGROK_WINDOWS_INSTALL_COMMAND
        self.assertEqual(
            command[:8],
            ("winget", "install", "--id", "9MVS1J51GMK6", "--exact", "--source", "msstore", "--accept-source-agreements"),
        )
        self.assertIn("--disable-interactivity", command)
        self.assertIn("--silent", command)
        source = Path(endpoint.__file__).read_text(encoding="utf-8")
        self.assertNotIn("bin.ngrok.com", source)
        self.assertNotIn("NGROK_WINDOWS_AMD64_URL", source)

    def test_windows_entrypoints_reference_current_installers_and_private_runtime(self):
        root = Path(startup.__file__).resolve().parent
        cloudflare = (root / "INSTALL_CLOUDFLARE_NAMED.bat").read_text(encoding="utf-8")
        companion = (root / "START_COMPANION_WORKER.bat").read_text(encoding="utf-8")
        default_endpoint = (root / "INSTALL_PERMANENT_ENDPOINT.bat").read_text(encoding="utf-8")
        legacy_installer = (root / "INSTALL_PERMANENT_ENDPOINT_V399.py").read_text(encoding="utf-8")
        self.assertNotIn("INSTALL_PERMANENT_ENDPOINT_V398.py", cloudflare)
        self.assertIn("INSTALL_PERMANENT_ENDPOINT_V399.py", cloudflare)
        self.assertTrue((root / "INSTALL_PERMANENT_ENDPOINT_V399.py").is_file())
        self.assertIn(r".venv\Scripts\python.exe", companion)
        self.assertNotRegex(companion, r"(?mi)^python\s+scripts\\companion_worker\.py")
        self.assertIn("World Engine 4.7.0", default_endpoint)
        self.assertIn("World Engine 4.7.0 permanent endpoint installer", legacy_installer)


if __name__ == "__main__":
    unittest.main()
