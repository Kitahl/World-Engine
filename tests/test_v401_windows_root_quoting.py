from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import world_engine_startup as startup
from world_engine_connection_guard import normalize_install_root


class V401WindowsRootQuotingTests(unittest.TestCase):
    def test_normalize_install_root_strips_stray_closing_quote(self):
        with tempfile.TemporaryDirectory() as td:
            expected = Path(td).resolve()
            broken = str(expected) + '"'
            self.assertEqual(expected, normalize_install_root(broken))

    def test_automatic_startup_accepts_broken_quoted_root_from_old_batch(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "world_engine_chatgpt_v4_0_1"
            root.mkdir()
            (root / "app.py").write_text("", encoding="utf-8")
            (root / "data").mkdir()
            data = Path(td) / "persistent"
            schema = root / "openapi_actions_PERMANENT.json"
            schema.write_text("{}", encoding="utf-8")
            endpoint_result = {
                "status": "PASS", "provider": "ngrok_user",
                "public_url": "https://assigned.ngrok-free.app",
                "schema": str(schema), "verification": {"health_ok": True, "protected_auth_ok": True},
                "reused": True,
            }
            broken = str(root) + '"'
            with patch.object(startup, "persistent_data_dir", return_value=data), \
                 patch.object(startup, "migrate_legacy_data", return_value={"copied": []}), \
                 patch.object(startup, "auto_migrate_from_previous_install", return_value={"status": "NONE"}), \
                 patch.object(startup, "install_environment"), \
                 patch.object(startup, "ensure_launcher_config", return_value=("world-engine-key", False)), \
                 patch.object(startup, "ensure_runtime_python", return_value=Path("python.exe")), \
                 patch.object(startup, "register_current_install"), \
                 patch.object(startup, "start_backend", return_value={"status": "STARTED"}) as backend, \
                 patch.object(startup, "ensure_endpoint", return_value=endpoint_result), \
                 patch.object(startup, "install_combined_user_startup", return_value={"status": "INSTALLED"}), \
                 patch.object(startup, "start_supervisor_process", return_value={"status": "STARTED"}), \
                 patch.object(startup, "verify_endpoint", return_value={"health_ok": True, "protected_auth_ok": True}), \
                 patch.object(startup, "clipboard_write", return_value=True), \
                 patch.object(startup, "reveal_file", return_value=True), \
                 patch.object(startup, "launch_launcher"):
                result = startup.automatic_startup(broken, reveal_setup_artifacts=False)
            self.assertEqual("PASS", result["status"])
            self.assertEqual(root.resolve(), backend.call_args.args[0])

    def test_batch_root_arguments_are_quote_safe(self):
        root = Path(startup.__file__).resolve().parent
        offenders = []
        for bat in root.glob("*.bat"):
            text = bat.read_text(encoding="utf-8", errors="replace")
            if '--root "%~dp0"' in text:
                offenders.append(bat.name)
        self.assertEqual([], offenders)
        start = (root / "START_WORLD_ENGINE.bat").read_text(encoding="utf-8", errors="replace")
        install = (root / "INSTALL_PERMANENT_ENDPOINT.bat").read_text(encoding="utf-8", errors="replace")
        self.assertIn('--root "%~dp0."', start)
        self.assertIn('--root "%~dp0."', install)


if __name__ == "__main__":
    unittest.main()
