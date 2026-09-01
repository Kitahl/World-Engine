import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import launcher
import world_engine_permanent_endpoint as permanent_endpoint
from world_engine.process_guard import ReclaimReport


class LauncherHelperTests(unittest.TestCase):
    def test_start_engine_reclaims_verified_auth_mismatch_then_starts(self):
        callbacks = []
        logs = []
        busy = []

        class FakeThread:
            def __init__(self, *, target, daemon):
                self.target = target
                self.daemon = daemon

            def start(self):
                callbacks.append(self.target)

        fake = SimpleNamespace(
            busy=False,
            api_key="current-key-0123456789-abcdef",
            status_var=SimpleNamespace(set=lambda _value: None),
            post_log=logs.append,
            after=lambda _delay, callback: callbacks.append(callback),
            set_busy=busy.append,
            _start_engine_worker=lambda: None,
            start_music=lambda: None,
            start_tunnel_async=lambda: None,
        )
        with patch.object(launcher, "local_health", return_value=True), \
             patch.object(launcher, "authenticated_probe", return_value=(False, 401, '{"detail": "Invalid World Engine API key"}')), \
             patch.object(
                 launcher,
                 "reclaim_stale_backend",
                 return_value=ReclaimReport(reclaimed=True, reason="stopped stale backend", pid=42),
             ) as reclaim, \
             patch.object(launcher.threading, "Thread", FakeThread):
            launcher.Launcher.start_engine(fake)

        reclaim.assert_called_once_with(8000)
        self.assertEqual([True], busy)
        self.assertIn(fake._start_engine_worker, callbacks)
        self.assertTrue(any("starting this installation" in line for line in logs))

    def test_start_engine_refuses_unverified_auth_mismatch(self):
        callbacks = []
        logs = []
        statuses = []
        fake = SimpleNamespace(
            busy=False,
            api_key="current-key-0123456789-abcdef",
            status_var=SimpleNamespace(set=statuses.append),
            post_log=logs.append,
            after=lambda _delay, callback: callbacks.append(callback),
            set_busy=lambda _value: self.fail("must not start after cleanup refusal"),
            _start_engine_worker=lambda: self.fail("must not start after cleanup refusal"),
        )
        with patch.object(launcher, "local_health", return_value=True), \
             patch.object(launcher, "authenticated_probe", return_value=(False, 401, '{"detail": "Invalid World Engine API key"}')), \
             patch.object(
                 launcher,
                 "reclaim_stale_backend",
                 return_value=ReclaimReport(reclaimed=False, reason="unrecognized process", pid=42),
             ), \
             patch.object(launcher.messagebox, "showerror") as showerror:
            launcher.Launcher.start_engine(fake)
            self.assertEqual(1, len(callbacks))
            callbacks[0]()

        showerror.assert_called_once()
        self.assertIn("No process was killed", " ".join(logs))
        self.assertIn("PORT 8000 AUTH MISMATCH", statuses)

    def test_start_engine_does_not_reclaim_on_unexpected_protected_failure(self):
        callbacks = []
        logs = []
        statuses = []
        fake = SimpleNamespace(
            busy=False,
            api_key="current-key-0123456789-abcdef",
            status_var=SimpleNamespace(set=statuses.append),
            post_log=logs.append,
            after=lambda _delay, callback: callbacks.append(callback),
            set_busy=lambda _value: self.fail("must not start after verification failure"),
            _start_engine_worker=lambda: self.fail("must not start after verification failure"),
        )
        with patch.object(launcher, "local_health", return_value=True), \
             patch.object(launcher, "authenticated_probe", return_value=(False, 500, "internal error")), \
             patch.object(launcher, "reclaim_stale_backend") as reclaim, \
             patch.object(launcher.messagebox, "showerror") as showerror:
            launcher.Launcher.start_engine(fake)
            self.assertEqual(1, len(callbacks))
            callbacks[0]()

        reclaim.assert_not_called()
        showerror.assert_called_once()
        self.assertIn("No process was killed", " ".join(logs))
        self.assertIn("PORT 8000 VERIFY FAILED", statuses)

    def test_stop_engine_reclaims_detached_backend(self):
        statuses = []
        logs = []
        fake = SimpleNamespace(
            server_proc=None,
            stop_tunnel=lambda: None,
            stop_music=lambda: None,
            post_log=logs.append,
            status_var=SimpleNamespace(set=statuses.append),
        )
        with patch.object(
                 launcher,
                 "active_listener_pids",
                 # Occupied before cleanup, released afterwards: stop_engine
                 # verifies release rather than assuming it.
                 side_effect=[[4242], []],
             ), \
             patch.object(
                 launcher,
                 "reclaim_stale_backend",
                 return_value=ReclaimReport(reclaimed=True, reason="stopped stale backend", pid=42),
             ) as reclaim:
            stopped = launcher.Launcher.stop_engine(fake)

        reclaim.assert_called_once_with(8000)
        self.assertTrue(stopped)
        self.assertEqual("STOPPED", statuses[-1])
        self.assertTrue(any("stopped stale backend" in line for line in logs))

    def test_stop_engine_reports_refused_cleanup(self):
        statuses = []
        fake = SimpleNamespace(
            server_proc=None,
            stop_tunnel=lambda: None,
            stop_music=lambda: None,
            post_log=lambda _value: None,
            status_var=SimpleNamespace(set=statuses.append),
        )
        with patch.object(launcher, "active_listener_pids", return_value=[4242]), \
             patch.object(
                 launcher,
                 "reclaim_stale_backend",
                 return_value=ReclaimReport(reclaimed=False, reason="unrecognized process", pid=42),
             ):
            stopped = launcher.Launcher.stop_engine(fake)

        self.assertFalse(stopped)
        self.assertEqual("STOP FAILED", statuses[-1])

    def test_stop_engine_fails_closed_when_port_query_is_unavailable(self):
        statuses = []
        fake = SimpleNamespace(
            server_proc=None,
            stop_tunnel=lambda: None,
            stop_music=lambda: None,
            post_log=lambda _value: None,
            status_var=SimpleNamespace(set=statuses.append),
        )
        with patch.object(launcher, "active_listener_pids", return_value=None), \
             patch.object(launcher, "reclaim_stale_backend") as reclaim:
            stopped = launcher.Launcher.stop_engine(fake)
        self.assertFalse(stopped)
        reclaim.assert_not_called()
        self.assertEqual("STOP FAILED", statuses[-1])

    def test_close_keeps_window_open_when_shutdown_is_unconfirmed(self):
        fake = SimpleNamespace(stop_engine=lambda: False, destroy=Mock())
        with patch.object(launcher.messagebox, "askokcancel", return_value=True), \
             patch.object(launcher.messagebox, "showerror") as showerror:
            launcher.Launcher.on_close(fake)
        fake.destroy.assert_not_called()
        showerror.assert_called_once()

    def test_close_destroys_window_after_confirmed_shutdown(self):
        fake = SimpleNamespace(stop_engine=lambda: True, destroy=Mock())
        with patch.object(launcher.messagebox, "askokcancel", return_value=True), \
             patch.object(launcher.messagebox, "showerror") as showerror:
            launcher.Launcher.on_close(fake)
        fake.destroy.assert_called_once()
        showerror.assert_not_called()

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
        windows = (root / "run_windows.bat").read_text(encoding="utf-8")
        self.assertNotIn("change-me-before-public-use", windows)
        self.assertIn('cd /d "%~dp0"', windows)
        self.assertIn(r'.venv\Scripts\python.exe', windows)
        self.assertIn('"%WORLD_ENGINE_PRIVATE_PYTHON%" -m pip', windows)
        self.assertIn('"%WORLD_ENGINE_PRIVATE_PYTHON%" app.py', windows)
        self.assertNotIn(":-change-me-before-public-use", (root / "docker-compose.yml").read_text(encoding="utf-8"))

    @unittest.skipUnless(os.name == "nt", "Windows batch parser regression")
    def test_run_windows_uses_python_fallback_when_py_launcher_is_absent(self):
        with tempfile.TemporaryDirectory() as td:
            temp = Path(td)
            script = temp / "run_windows.bat"
            script.write_bytes((Path(launcher.ROOT) / "run_windows.bat").read_bytes())
            marker = temp / "fallback-called.txt"
            (temp / "python.cmd").write_text(
                "@echo off\r\n"
                f">>\"{marker}\" echo python\r\n"
                "exit /b 1\r\n",
                encoding="utf-8",
            )
            env = dict(os.environ)
            env["PATH"] = os.pathsep.join([str(temp), str(Path(env["WINDIR"]) / "System32")])
            completed = subprocess.run(
                [env.get("COMSPEC", "cmd.exe"), "/d", "/c", str(script)],
                cwd=temp,
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(1, completed.returncode)
            self.assertTrue(marker.is_file(), completed.stdout + completed.stderr)

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
        self.assertEqual(permanent_endpoint.CLOUDFLARED_VERSION, launcher.CLOUDFLARED_VERSION)
        self.assertEqual(permanent_endpoint.CLOUDFLARED_WINDOWS_AMD64_URL, launcher.CLOUDFLARED_URL)
        self.assertEqual(permanent_endpoint.CLOUDFLARED_WINDOWS_AMD64_SHA256, launcher.CLOUDFLARED_SHA256)
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

    def test_generated_action_schema_exposes_only_the_five_public_operations(self):
        sample = {
            "paths": {
                "/api/visual/preferences": {"get": {"operationId": "getVisualPreferences"}, "post": {"operationId": "setVisualPreferences"}},
                "/api/snapshot": {"get": {"operationId": "getCampaignSnapshot"}},
                "/api/visual/profile/{entity_kind}/{entity_id}": {"get": {"operationId": "getVisualProfile"}},
                "/api/visual/profile": {"post": {"operationId": "saveVisualProfile"}},
                "/api/turn": {"post": {"operationId": "resolveTurn"}},
                "/api/presentation": {"post": {"operationId": "publishPresentation"}},
                "/api/visual/cue": {"post": {"operationId": "buildImageCue"}},
                "/api/visual/generated": {"post": {"operationId": "recordImageGeneration"}},
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
            self.assertEqual(5, len(ops))
            self.assertNotIn("configureSimulation", ops)
            self.assertNotIn("authorWorldContent", ops)
            self.assertEqual({
                "buildImageCue", "publishPresentation", "recordImageGeneration",
                "resolveTurn", "saveVisualProfile",
            }, set(ops))
            self.assertNotIn("saveVisualState", ops)
            self.assertNotIn("getVisualPreferences", ops)
            self.assertNotIn("getInternalStateBlock", ops)

    def test_generated_action_schema_adds_empty_properties_to_object_schemas(self):
        sample = {
            "paths": {
                "/api/turn": {
                    "post": {
                        "operationId": "resolveTurn",
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
                "/api/presentation": {
                    "post": {
                        "operationId": "publishPresentation",
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
            self.assertEqual({}, out["paths"]["/api/turn"]["post"]["responses"]["200"]["content"]["application/json"]["schema"]["properties"])
            any_of = out["paths"]["/api/presentation"]["post"]["responses"]["200"]["content"]["application/json"]["schema"]["anyOf"]
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
            # A real Launcher always has this; the failure path now inspects
            # it to clean up a partially started backend.
            server_proc=None,
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

    def test_engine_worker_cleans_up_partially_started_backend(self):
        callbacks = []
        statuses = []
        logs = []
        busy = []
        process = Mock(pid=7331, returncode=None, stdout=None)
        process.poll.return_value = None
        fake = SimpleNamespace(
            after=lambda _delay, callback: callbacks.append(callback),
            set_status=statuses.append,
            post_log=logs.append,
            set_busy=busy.append,
            server_proc=None,
            api_key="api-key-0123456789-abcdefgh",
            admin_key="admin-key-0123456789-abcdef",
            _stream_process=lambda *_args: None,
            start_music=lambda: None,
            start_tunnel_async=lambda: None,
        )
        thread = SimpleNamespace(start=lambda: None)
        with patch.object(launcher, "venv_python", return_value=Path(sys.executable)), \
             patch.object(launcher.subprocess, "run", return_value=SimpleNamespace(returncode=0)), \
             patch.object(launcher.subprocess, "Popen", return_value=process), \
             patch.object(launcher.threading, "Thread", return_value=thread), \
             patch.object(launcher, "local_health", return_value=True), \
             patch.object(launcher, "authenticated_probe", return_value=(False, 500, "internal error")), \
             patch.object(launcher, "terminate_owned_process_tree", return_value=True) as terminate, \
             patch.object(launcher.messagebox, "showerror") as showerror:
            launcher.Launcher._start_engine_worker(fake)
            self.assertEqual(1, len(callbacks))
            callbacks[0]()

        terminate.assert_called_once_with(process)
        showerror.assert_called_once()
        self.assertTrue(any("Cleaned up" in line for line in logs))
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
