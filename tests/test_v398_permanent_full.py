from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from world_engine_connection_guard import auto_migrate_from_previous_install, migrate_legacy_data, persistent_data_dir
from world_engine_permanent_endpoint import load_permanent_config, save_permanent_config, write_permanent_schema
from world_engine_autostart import register_current_install


ROOT = Path(__file__).resolve().parents[1]


class V398PermanentFullTests(unittest.TestCase):
    def test_persistent_data_dir_honors_override(self):
        with tempfile.TemporaryDirectory() as td:
            old = os.environ.get("WORLD_ENGINE_DATA_DIR")
            try:
                os.environ["WORLD_ENGINE_DATA_DIR"] = td
                self.assertEqual(Path(td).resolve(), persistent_data_dir())
            finally:
                if old is None:
                    os.environ.pop("WORLD_ENGINE_DATA_DIR", None)
                else:
                    os.environ["WORLD_ENGINE_DATA_DIR"] = old

    def test_migration_never_overwrites_existing_persistent_state(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            legacy = base / "legacy"; persistent = base / "persistent"
            legacy.mkdir(); persistent.mkdir()
            (legacy / "world_engine.sqlite3").write_bytes(b"legacy-db")
            (legacy / "launcher_config.json").write_text('{"api_key":"legacy"}', encoding="utf-8")
            (persistent / "world_engine.sqlite3").write_bytes(b"authoritative-db")
            report = migrate_legacy_data(legacy, persistent)
            self.assertEqual(b"authoritative-db", (persistent / "world_engine.sqlite3").read_bytes())
            self.assertEqual('{"api_key":"legacy"}', (persistent / "launcher_config.json").read_text(encoding="utf-8"))
            self.assertEqual(1, len(report["conflicts_preserved"]))
            self.assertTrue(Path(report["conflicts_preserved"][0]).exists())


    def test_auto_migrates_newest_sibling_install_only_when_persistent_empty(self):
        with tempfile.TemporaryDirectory() as td:
            parent = Path(td)
            current = parent / "world_engine_chatgpt_v3_9_8"; current.mkdir()
            old1 = parent / "world_engine_chatgpt_v3_9_4" / "data"; old1.mkdir(parents=True)
            old2 = parent / "world_engine_chatgpt_v3_9_5" / "data"; old2.mkdir(parents=True)
            (old1 / "world_engine.sqlite3").write_bytes(b"older")
            (old2 / "world_engine.sqlite3").write_bytes(b"newer")
            (old2 / "launcher_config.json").write_text('{"api_key":"same-key"}', encoding="utf-8")
            os.utime(old1 / "world_engine.sqlite3", (1, 1))
            os.utime(old2 / "world_engine.sqlite3", (2, 2))
            persistent = parent / "persistent"
            report = auto_migrate_from_previous_install(current, persistent)
            self.assertEqual("MIGRATED_PREVIOUS_INSTALL", report["status"])
            self.assertEqual(b"newer", (persistent / "world_engine.sqlite3").read_bytes())
            self.assertTrue((persistent / "launcher_config.json").exists())
            # Existing persistent state wins on later launches.
            (old2 / "world_engine.sqlite3").write_bytes(b"changed")
            again = auto_migrate_from_previous_install(current, persistent)
            self.assertEqual("PERSISTENT_STATE_EXISTS", again["status"])
            self.assertEqual(b"newer", (persistent / "world_engine.sqlite3").read_bytes())

    def test_permanent_schema_has_fixed_server_and_preserves_30_action_contract(self):
        with tempfile.TemporaryDirectory() as td:
            data = Path(td) / "data"
            out = write_permanent_schema(ROOT, "https://world-engine.test.ts.net", data=data)
            schema = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual("https://world-engine.test.ts.net", schema["servers"][0]["url"])
            ops = [op for methods in schema["paths"].values() for op in methods.values() if isinstance(op, dict) and op.get("operationId")]
            self.assertLessEqual(len(ops), 30)
            self.assertEqual(len(ops), len({op["operationId"] for op in ops}))
            self.assertTrue(all(op.get("x-openai-isConsequential") is False for op in ops))
            self.assertTrue((data / "openapi_actions_PERMANENT.json").exists())
            out.unlink(missing_ok=True)

    def test_permanent_config_and_runtime_install_are_version_independent(self):
        with tempfile.TemporaryDirectory() as td:
            data = Path(td)
            cfg_path = save_permanent_config("tailscale_funnel", "https://machine.tailnet.ts.net", "a" * 32, data=data)
            cfg = load_permanent_config(data)
            self.assertEqual("https://machine.tailnet.ts.net", cfg["public_url"])
            self.assertTrue(cfg["permanent"])
            self.assertTrue(cfg_path.exists())
            runtime = register_current_install(ROOT, python_exe=sys.executable, data=data)
            payload = json.loads(runtime.read_text(encoding="utf-8"))
            self.assertEqual(str(ROOT.resolve()), payload["install_root"])
            self.assertEqual(sys.executable, payload["python_exe"])

    def test_full_package_contains_integrated_permanent_setup_and_compact_instructions(self):
        required = [
            "INSTALL_PERMANENT_ENDPOINT.bat",
            "VERIFY_PERMANENT_ENDPOINT.bat",
            "INSTALL_CLOUDFLARE_NAMED.bat",
            "world_engine_connection_guard.py",
            "world_engine_permanent_endpoint.py",
            "world_engine_autostart.py",
            "CUSTOM_GPT_INSTRUCTIONS_V399.txt",
            "PERMANENT_ENDPOINT_GUIDE.md",
        ]
        for name in required:
            self.assertTrue((ROOT / name).exists(), name)
        instructions = (ROOT / "CUSTOM_GPT_INSTRUCTIONS_V399.txt").read_text(encoding="utf-8")
        self.assertLessEqual(len(instructions), 8000)
        self.assertLessEqual(len(instructions.encode("utf-8")), 8000)
        self.assertIn("openapi_actions_PERMANENT.json", instructions)
        self.assertIn("do not request or depend on a Quick Tunnel", instructions)

    def test_launcher_uses_persistent_data_and_blocks_implicit_quick_tunnel(self):
        source = (ROOT / "launcher.py").read_text(encoding="utf-8")
        self.assertIn("DATA_DIR = persistent_data_dir()", source)
        self.assertIn("Permanent Endpoint Setup", source)
        self.assertIn("Permanent HTTPS is not configured", source)
        self.assertIn("Quick Tunnel is disabled as an automatic fallback", source)
        self.assertIn("openapi_actions_PERMANENT.json", source)

    def test_app_can_authenticate_from_persistent_launcher_config_without_env_key(self):
        with tempfile.TemporaryDirectory() as td:
            data = Path(td)
            key = "persistent-test-secret-0123456789-abcdef"
            (data / "launcher_config.json").write_text(json.dumps({"api_key": key}), encoding="utf-8")
            code = r'''
import os
from fastapi.testclient import TestClient
import app
client=TestClient(app.app)
r=client.post('/api/campaign',headers={'Authorization':'Bearer persistent-test-secret-0123456789-abcdef'},json={'campaign_id':'persistent-auth','name':'Persistent'})
print(r.status_code)
print(r.json().get('id'))
'''
            env = os.environ.copy()
            env["WORLD_ENGINE_DATA_DIR"] = str(data)
            env.pop("WORLD_ENGINE_API_KEY", None)
            env.pop("WORLD_ENGINE_DB", None)
            cp = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, text=True, capture_output=True, timeout=30)
            self.assertEqual(0, cp.returncode, cp.stderr)
            self.assertIn("200", cp.stdout)
            self.assertIn("persistent-auth", cp.stdout)


if __name__ == "__main__":
    unittest.main()
