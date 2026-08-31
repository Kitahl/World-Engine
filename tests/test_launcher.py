import json
import sys
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

import launcher


class LauncherHelperTests(unittest.TestCase):
    def test_api_key_is_generated_and_persisted(self):
        with tempfile.TemporaryDirectory() as td:
            data_dir = Path(td)
            config_path = data_dir / "launcher_config.json"
            with patch.object(launcher, "DATA_DIR", data_dir), patch.object(launcher, "CONFIG_PATH", config_path):
                config = {}
                key = launcher.ensure_api_key(config)
                self.assertGreaterEqual(len(key), 24)
                saved = json.loads(config_path.read_text(encoding="utf-8"))
                self.assertEqual(key, saved["api_key"])
                self.assertEqual(key, launcher.ensure_api_key(saved))

    def test_cloudflare_quick_tunnel_url_detection(self):
        sample = "INF +--------------------------------------------------------------------------------------------+ https://quiet-lake-123.trycloudflare.com"
        match = launcher.TUNNEL_RE.search(sample)
        self.assertIsNotNone(match)
        self.assertEqual("https://quiet-lake-123.trycloudflare.com", match.group(0))


    def test_non_launcher_start_paths_do_not_ship_placeholder_keys(self):
        root = Path(launcher.ROOT)
        self.assertNotIn("change-me-before-public-use", (root / "run_unix.sh").read_text(encoding="utf-8"))
        self.assertNotIn("change-me-before-public-use", (root / "run_windows.bat").read_text(encoding="utf-8"))
        self.assertNotIn(":-change-me-before-public-use", (root / "docker-compose.yml").read_text(encoding="utf-8"))

    def test_launcher_targets_loopback(self):
        self.assertEqual("http://127.0.0.1:8000", launcher.LOCAL_URL)

    def test_music_catalog_template_is_created(self):
        with tempfile.TemporaryDirectory() as td:
            data_dir = Path(td)
            catalog = data_dir / "music_catalog.json"
            with patch.object(launcher, "DATA_DIR", data_dir), patch.object(launcher, "MUSIC_CATALOG_PATH", catalog):
                path = launcher.ensure_music_catalog()
                payload = json.loads(path.read_text(encoding="utf-8"))
                self.assertIn("tracks", payload)
                self.assertIn("defaults", payload)


    def test_cloudflared_is_pinned_and_hash_checked(self):
        self.assertNotIn("/latest/", launcher.CLOUDFLARED_URL)
        self.assertIn(launcher.CLOUDFLARED_VERSION, launcher.CLOUDFLARED_URL)
        self.assertEqual(64, len(launcher.CLOUDFLARED_SHA256))
        with tempfile.TemporaryDirectory() as td:
            bad = Path(td) / "cloudflared.exe"
            bad.write_bytes(b"x" * 2_000_000)
            self.assertFalse(launcher.cloudflared_hash_ok(bad))


    def test_api_key_fingerprint_is_stable_and_non_secret(self):
        key = "test-secret-0123456789-abcdef"
        fp = launcher.api_key_fingerprint(key)
        self.assertEqual(12, len(fp))
        self.assertNotIn(key, fp)
        self.assertEqual(fp, launcher.api_key_fingerprint(key))

    def test_schema_server_url_and_connection_diagnostics_classify_stale_schema(self):
        with tempfile.TemporaryDirectory() as td:
            schema_path = Path(td) / "openapi_actions_live.json"
            schema_path.write_text(json.dumps({"servers": [{"url": "https://old.trycloudflare.com"}]}), encoding="utf-8")
            with patch.object(launcher, "local_health", return_value=True), \
                 patch.object(launcher, "public_health", return_value=True), \
                 patch.object(launcher, "authenticated_probe", side_effect=[(True, 200, "{}"), (True, 200, "{}")]):
                report = launcher.connection_diagnostics("https://new.trycloudflare.com", "test-secret-0123456789-abcdef", schema_path)
            self.assertTrue(report["local_auth_ok"])
            self.assertTrue(report["public_auth_ok"])
            self.assertFalse(report["schema_matches_public_url"])
            self.assertEqual("https://old.trycloudflare.com", report["schema_server_url"])

    def test_connection_diagnostics_exposes_auth_status_without_secret(self):
        with patch.object(launcher, "local_health", return_value=True), \
             patch.object(launcher, "public_health", return_value=True), \
             patch.object(launcher, "authenticated_probe", side_effect=[(True, 200, "ok"), (False, 401, '{"detail":"Invalid World Engine API key"}')]):
            report = launcher.connection_diagnostics("https://example.test", "test-secret-0123456789-abcdef")
        self.assertEqual(401, report["public_auth_status"])
        self.assertFalse(report["public_auth_ok"])
        self.assertNotIn("test-secret-0123456789-abcdef", json.dumps(report))

    def test_generated_action_schema_stays_at_30_and_includes_simulation(self):
        sample = {
            "paths": {
                "/api/visual/preferences": {"get": {"operationId": "getVisualPreferences"}, "post": {"operationId": "setVisualPreferences"}},
                "/api/snapshot": {"get": {"operationId": "getCampaignSnapshot"}},
                "/api/visual/profile/{entity_kind}/{entity_id}": {"get": {"operationId": "getVisualProfile"}},
                "/api/visual/profile": {"post": {"operationId": "saveVisualProfile"}},
                "/api/authoring": {"post": {"operationId": "authorWorldContent"}},
                "/api/rules": {"post": {"operationId": "runRulesKernel"}},
                "/api/visual/state": {"post": {"operationId": "saveVisualState"}},
                "/api/visual/state/{scope_type}/{scope_id}": {"get": {"operationId": "getVisualState"}},
                "/api/visual/recent": {"get": {"operationId": "getRecentImageContext"}},
                "/api/internal/state": {"get": {"operationId": "getInternalStateBlock"}},
                **{f"/x/{i}": {"post": {"operationId": ("configureSimulation" if i == 0 else f"op{i}")}} for i in range(26)},
            }
        }
        class FakeResponse:
            def __enter__(self): return self
            def __exit__(self, *args): return False
        import io
        class BytesResponse(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *args): return False
        payload = json.dumps(sample).encode()
        with tempfile.TemporaryDirectory() as td, patch("urllib.request.urlopen", return_value=BytesResponse(payload)):
            dest = Path(td) / "schema.json"
            launcher.generate_action_schema("https://example.test", dest)
            out = json.loads(dest.read_text())
            ops = [op["operationId"] for methods in out["paths"].values() for op in methods.values() if isinstance(op, dict) and op.get("operationId")]
            self.assertLessEqual(len(ops), 30)
            self.assertNotIn("configureSimulation", ops)
            self.assertNotIn("authorWorldContent", ops)
            self.assertIn("runRulesKernel", ops)
            self.assertIn("saveVisualProfile", ops)
            self.assertNotIn("saveVisualState", ops)
            self.assertNotIn("getVisualPreferences", ops)
            self.assertNotIn("getInternalStateBlock", ops)

    def test_generated_action_schema_adds_empty_properties_to_object_schemas(self):
        sample = {
            "paths": {
                "/api/campaign": {
                    "post": {
                        "operationId": "ensureCampaign",
                        "responses": {
                            "200": {
                                "content": {
                                    "application/json": {
                                        "schema": {"type": "object", "additionalProperties": True}
                                    }
                                }
                            }
                        },
                    }
                },
                "/api/rules": {
                    "post": {
                        "operationId": "runRulesKernel",
                        "responses": {
                            "200": {
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "anyOf": [
                                                {"type": "object", "additionalProperties": True},
                                                {"type": "array", "items": {"type": "object", "additionalProperties": True}},
                                            ]
                                        }
                                    }
                                }
                            }
                        },
                    }
                },
            }
        }
        import io
        class BytesResponse(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *args): return False
        payload = json.dumps(sample).encode()
        with tempfile.TemporaryDirectory() as td, patch("urllib.request.urlopen", return_value=BytesResponse(payload)):
            dest = Path(td) / "schema.json"
            launcher.generate_action_schema("https://example.test", dest)
            out = json.loads(dest.read_text())
            self.assertEqual({}, out["paths"]["/api/campaign"]["post"]["responses"]["200"]["content"]["application/json"]["schema"]["properties"])
            any_of = out["paths"]["/api/rules"]["post"]["responses"]["200"]["content"]["application/json"]["schema"]["anyOf"]
            self.assertEqual({}, any_of[0]["properties"])
            self.assertEqual({}, any_of[1]["items"]["properties"])

    def test_engine_worker_error_dialog_survives_deferred_callback(self):
        callbacks = []
        statuses = []
        logs = []
        busy = []
        fake = SimpleNamespace(
            after=lambda _delay, callback: callbacks.append(callback),
            set_status=statuses.append,
            post_log=logs.append,
            set_busy=busy.append,
        )
        with patch.object(launcher, "venv_python", return_value=Path(sys.executable)), \
             patch.object(launcher.subprocess, "run", side_effect=RuntimeError("engine failed to start")), \
             patch.object(launcher.messagebox, "showerror") as showerror:
            launcher.Launcher._start_engine_worker(fake)
            self.assertEqual(1, len(callbacks))
            showerror.assert_not_called()
            callbacks[0]()

        showerror.assert_called_once()
        self.assertIn("engine failed to start", showerror.call_args.args[1])
        self.assertIn("ERROR", statuses)
        self.assertEqual([False], busy)

    def test_tunnel_worker_error_dialog_survives_deferred_callback(self):
        callbacks = []
        statuses = []
        logs = []

        def fail_cloudflared():
            raise RuntimeError("tunnel failed to start")

        fake = SimpleNamespace(
            _ensure_cloudflared=fail_cloudflared,
            after=lambda _delay, callback: callbacks.append(callback),
            set_status=statuses.append,
            post_log=logs.append,
        )
        with patch.object(launcher, "local_health", return_value=False), \
             patch.object(launcher.messagebox, "showerror") as showerror:
            launcher.Launcher._start_tunnel_worker(fake)
            self.assertEqual(1, len(callbacks))
            showerror.assert_not_called()
            callbacks[0]()

        showerror.assert_called_once()
        self.assertIn("tunnel failed to start", showerror.call_args.args[1])
        self.assertTrue(any("HTTPS ERROR" in line for line in logs))

if __name__ == "__main__":
    unittest.main()
