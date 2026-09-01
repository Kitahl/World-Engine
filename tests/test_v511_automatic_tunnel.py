from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

import world_engine_permanent_endpoint as endpoint
import world_engine_startup as startup


class AutomaticTunnelTests(unittest.TestCase):
    def test_quick_url_parser_rejects_prefix_confusion(self):
        valid = "https://quiet-lake-123.trycloudflare.com"
        self.assertEqual(valid, endpoint.cloudflare_quick_url("INF " + valid))
        for hostile in (
            valid + ".evil.invalid",
            valid + "@evil.invalid",
            "https://trycloudflare.com.evil.invalid",
            "https://evil.invalid/" + valid,
        ):
            with self.subTest(hostile=hostile):
                self.assertIsNone(endpoint.cloudflare_quick_url(hostile))

    def test_tunnel_child_environment_is_closed(self):
        child = endpoint.tunnel_child_environment(
            {
                "PATH": "safe-path",
                "SystemRoot": r"C:\Windows",
                "WORLD_ENGINE_API_KEY": "api-secret",
                "NGROK_AUTHTOKEN": "ngrok-secret",
                "CLOUDFLARE_TUNNEL_TOKEN": "cloudflare-secret",
                "AWS_SECRET_ACCESS_KEY": "other-secret",
            }
        )
        self.assertEqual("safe-path", child["PATH"])
        self.assertEqual(r"C:\Windows", child["SystemRoot"])
        encoded = json.dumps(child)
        for secret in (
            "api-secret",
            "ngrok-secret",
            "cloudflare-secret",
            "other-secret",
        ):
            self.assertNotIn(secret, encoded)

    def test_dead_process_cannot_return_url_and_is_cleaned(self):
        with tempfile.TemporaryDirectory() as td:
            data = Path(td)
            process = mock.Mock(pid=41, returncode=9)
            process.poll.return_value = 9
            with mock.patch.object(
                endpoint.subprocess, "Popen", return_value=process
            ) as popen, mock.patch.object(
                endpoint, "_stop_started_process"
            ) as stop, mock.patch.dict(
                os.environ,
                {
                    "PATH": "safe-path",
                    "WORLD_ENGINE_API_KEY": "must-not-reach-child",
                },
                clear=True,
            ):
                (data / "logs").mkdir()
                (data / "logs" / "cloudflare_quick.log").write_text(
                    "https://quiet-lake-123.trycloudflare.com", encoding="utf-8"
                )
                with self.assertRaisesRegex(RuntimeError, "exited"):
                    endpoint.start_cloudflare_quick_endpoint(
                        "cloudflared", data=data, timeout_seconds=1
                    )
            stop.assert_called_once_with(process)
            child_env = popen.call_args.kwargs["env"]
            self.assertNotIn("WORLD_ENGINE_API_KEY", child_env)
            self.assertNotIn("must-not-reach-child", child_env.values())

    def test_log_read_failure_cleans_child(self):
        with tempfile.TemporaryDirectory() as td:
            data = Path(td)
            executable = str((data / "cloudflared.exe").resolve())
            Path(executable).write_bytes(b"test")
            process = mock.Mock(pid=42, returncode=None)
            process.poll.return_value = None
            identity = endpoint.ProcessIdentity(
                42, executable, subprocess.list2cmdline([executable, "tunnel", "--url", "http://127.0.0.1:8000", "--no-autoupdate"]), "created"
            )
            with mock.patch.object(
                endpoint.subprocess, "Popen", return_value=process
            ), mock.patch.object(
                Path, "read_text", side_effect=OSError("log locked")
            ), mock.patch.object(
                endpoint, "_quick_process_identity", return_value=identity
            ), mock.patch.object(
                endpoint, "_stop_started_process"
            ) as stop, self.assertRaisesRegex(OSError, "log locked"):
                endpoint.start_cloudflare_quick_endpoint(
                    executable, data=data, timeout_seconds=1
                )
            stop.assert_called_once_with(process)

    def test_timeout_raises_and_cleans_child(self):
        with tempfile.TemporaryDirectory() as td:
            data = Path(td)
            executable = str((data / "cloudflared.exe").resolve())
            Path(executable).write_bytes(b"test")
            process = mock.Mock(pid=43, returncode=None)
            process.poll.return_value = None
            identity = endpoint.ProcessIdentity(
                43, executable, subprocess.list2cmdline([executable, "tunnel", "--url", "http://127.0.0.1:8000", "--no-autoupdate"]), "created"
            )
            with mock.patch.object(
                endpoint.subprocess, "Popen", return_value=process
            ), mock.patch.object(
                endpoint.time, "monotonic", side_effect=[0.0, 0.0, 2.0]
            ), mock.patch.object(
                endpoint, "_quick_process_identity", return_value=identity
            ), mock.patch.object(
                endpoint, "_stop_started_process"
            ) as stop, self.assertRaisesRegex(RuntimeError, "did not create"):
                endpoint.start_cloudflare_quick_endpoint(
                    executable, data=data, timeout_seconds=1
                )
            stop.assert_called_once_with(process)

    def test_unconfigured_provider_uses_quick_without_clipboard_or_ngrok_install(self):
        ready = {
            "status": startup.EndpointStatus.READY.value,
            "provider": endpoint.CLOUDFLARE_QUICK_PROVIDER,
            "public_url": "https://quiet-lake-123.trycloudflare.com",
            "schema": "schema.json",
            "reused": False,
        }
        with tempfile.TemporaryDirectory() as td, mock.patch.object(
            startup, "load_permanent_config", return_value={}
        ), mock.patch.object(
            startup, "find_ngrok", return_value=None
        ), mock.patch.object(
            startup, "ngrok_credentials_present", return_value=False
        ), mock.patch.object(
            startup, "install_cloudflare_quick", return_value=ready
        ) as quick, mock.patch.object(
            startup, "download_portable_ngrok_windows"
        ) as ngrok_install, mock.patch.object(
            startup, "acquire_ngrok_token_from_clipboard"
        ) as clipboard:
            result = startup.ensure_endpoint(
                Path(td), Path(td), "world-engine-key", interactive=True
            )
        self.assertEqual(endpoint.CLOUDFLARE_QUICK_PROVIDER, result["provider"])
        quick.assert_called_once()
        ngrok_install.assert_not_called()
        clipboard.assert_not_called()

    def test_configured_ngrok_remains_same_provider_without_clipboard(self):
        installed = {
            "status": startup.EndpointStatus.READY.value,
            "provider": endpoint.NGROK_PROVIDER,
            "public_url": "https://assigned.ngrok-free.app",
            "schema": "schema.json",
        }
        with tempfile.TemporaryDirectory() as td, mock.patch.object(
            startup, "load_permanent_config", return_value={}
        ), mock.patch.object(
            startup, "find_ngrok", return_value="trusted-ngrok"
        ), mock.patch.object(
            startup,
            "ensure_ngrok_authentication",
            return_value={"status": "EXISTING_CONFIG"},
        ) as auth, mock.patch.object(
            startup, "install_ngrok_from_config", return_value=installed
        ), mock.patch.object(
            startup, "install_cloudflare_quick"
        ) as quick, mock.patch.object(
            startup, "acquire_ngrok_token_from_clipboard"
        ) as clipboard:
            result = startup.ensure_endpoint(
                Path(td), Path(td), "world-engine-key", interactive=True
            )
        self.assertEqual(endpoint.NGROK_PROVIDER, result["provider"])
        self.assertFalse(auth.call_args.kwargs["interactive"])
        quick.assert_not_called()
        clipboard.assert_not_called()

    def test_quick_url_change_is_nonstable_and_requires_reimport(self):
        with tempfile.TemporaryDirectory() as td:
            data = Path(td)
            path = endpoint.save_quick_tunnel_config(
                "https://new-name.trycloudflare.com",
                "world-engine-key",
                data=data,
                pid=91,
                previous_url="https://old-name.trycloudflare.com",
            )
            saved = json.loads(path.read_text(encoding="utf-8"))
            guard = json.loads(
                (data / "connection_guard.json").read_text(encoding="utf-8")
            )
        self.assertFalse(saved["permanent"])
        self.assertFalse(saved["stable_hostname"])
        self.assertTrue(saved["action_reimport_required"])
        self.assertTrue(guard["require_action_reimport_ack"])
        self.assertIsNone(guard["stable_public_url"])

    def test_helper_failure_returns_typed_degraded_outcome(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.object(
            startup, "load_permanent_config", return_value={}
        ), mock.patch.object(
            startup, "find_ngrok", return_value=None
        ), mock.patch.object(
            startup, "ngrok_credentials_present", return_value=False
        ), mock.patch.object(
            startup,
            "install_cloudflare_quick",
            side_effect=RuntimeError("download failed"),
        ), mock.patch.object(
            startup, "acquire_ngrok_token_from_clipboard"
        ) as clipboard:
            result = startup.ensure_endpoint_outcome(
                Path(td), Path(td), "world-engine-key", interactive=True
            )
        self.assertEqual(startup.EndpointStatus.UNAVAILABLE.value, result["status"])
        self.assertEqual("ENDPOINT_UNAVAILABLE", result["error_code"])
        clipboard.assert_not_called()

    def test_failed_authenticated_verification_stops_child_and_writes_no_state(self):
        process = mock.Mock()
        runtime = {
            "status": "STARTED",
            "provider": endpoint.CLOUDFLARE_QUICK_PROVIDER,
            "public_url": "https://quiet-lake-123.trycloudflare.com",
            "pid": 92,
            "log": "quick.log",
            "_process": process,
        }
        with tempfile.TemporaryDirectory() as td, mock.patch.object(
            endpoint, "automatic_cloudflared", return_value="trusted-cloudflared"
        ), mock.patch.object(
            endpoint, "start_cloudflare_quick_endpoint", return_value=runtime
        ), mock.patch.object(
            endpoint,
            "verify_endpoint",
            return_value={"health_ok": True, "protected_auth_ok": False},
        ), mock.patch.object(
            endpoint, "_stop_started_process"
        ) as stop, mock.patch.object(
            endpoint, "write_permanent_schema"
        ) as schema, mock.patch.object(
            endpoint, "save_quick_tunnel_config"
        ) as save, self.assertRaisesRegex(RuntimeError, "authenticated"):
            endpoint.install_cloudflare_quick(
                Path(td), Path(td), "world-engine-key"
            )
        stop.assert_called_once_with(process)
        schema.assert_not_called()
        save.assert_not_called()

    def test_hostile_global_cloudflare_config_is_isolated(self):
        with tempfile.TemporaryDirectory() as td:
            data = Path(td)
            hostile = str(data / "hostile-user-home")
            child = endpoint.tunnel_child_environment(
                {
                    "PATH": "safe-path",
                    "USERPROFILE": hostile,
                    "HOME": hostile,
                    "APPDATA": hostile,
                    "LOCALAPPDATA": hostile,
                    "TUNNEL_TOKEN": "must-not-pass",
                },
                data=data,
            )
            owned = str(data / "runtime" / "cloudflare_quick_home")
            self.assertEqual({owned}, {child[name] for name in ("USERPROFILE", "HOME", "APPDATA", "LOCALAPPDATA")})
            self.assertNotIn(hostile, child.values())
            self.assertNotIn("TUNNEL_TOKEN", child)
            self.assertTrue((Path(owned) / ".cloudflared").is_dir())

    def test_reimport_warning_is_sticky_until_explicit_acknowledgment(self):
        with tempfile.TemporaryDirectory() as td:
            data = Path(td)
            endpoint.save_quick_tunnel_config(
                "https://new-name.trycloudflare.com",
                "world-engine-key",
                data=data,
                pid=91,
                previous_url="https://old-name.trycloudflare.com",
            )
            endpoint.save_quick_tunnel_config(
                "https://new-name.trycloudflare.com",
                "world-engine-key",
                data=data,
                pid=91,
                previous_url="https://new-name.trycloudflare.com",
            )
            self.assertTrue(endpoint.load_permanent_config(data)["action_reimport_required"])
            self.assertTrue((data / "ACTION_REIMPORT_REQUIRED.txt").is_file())
            endpoint.acknowledge_action_reimport(data=data)
            self.assertFalse(endpoint.load_permanent_config(data)["action_reimport_required"])
            self.assertFalse((data / "ACTION_REIMPORT_REQUIRED.txt").exists())

    def test_owned_quick_stop_refuses_identity_mismatch(self):
        with tempfile.TemporaryDirectory() as td:
            data = Path(td)
            receipt = {
                "pid": 77,
                "executable": str(data / "cloudflared.exe"),
                "argv": [str(data / "cloudflared.exe"), "tunnel"],
                "creation_time": "original",
            }
            endpoint.atomic_json(endpoint.quick_runtime_receipt_path(data), receipt)
            hostile = endpoint.ProcessIdentity(
                77, str(data / "other.exe"), '"other.exe" tunnel', "reused"
            )
            with mock.patch.object(
                endpoint, "_quick_process_identity", return_value=hostile
            ), mock.patch.object(endpoint, "_default_terminator") as terminate:
                result = endpoint.stop_owned_quick_tunnel(data)
            self.assertEqual("REFUSED", result["status"])
            terminate.assert_not_called()

    def test_concurrent_endpoint_ensure_installs_quick_once(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data = root / "data"
            data.mkdir()
            calls = []

            def install(_root, _data, _key, **_kwargs):
                calls.append(1)
                time.sleep(0.05)
                endpoint.atomic_json(
                    data / endpoint.PERMANENT_CONFIG,
                    {
                        "provider": endpoint.CLOUDFLARE_QUICK_PROVIDER,
                        "public_url": "https://one.trycloudflare.com",
                        "permanent": False,
                        "stable_hostname": False,
                        "requires_account": False,
                    },
                )
                return {
                    "status": startup.EndpointStatus.READY.value,
                    "provider": endpoint.CLOUDFLARE_QUICK_PROVIDER,
                    "public_url": "https://one.trycloudflare.com",
                    "schema": "schema.json",
                    "reused": False,
                }

            owner = endpoint.ProcessIdentity(os.getpid(), "python.exe", "python.exe", "now")
            with mock.patch.object(endpoint, "_quick_process_identity", return_value=owner), \
                 mock.patch.object(startup, "find_ngrok", return_value=None), \
                 mock.patch.object(startup, "ngrok_credentials_present", return_value=False), \
                 mock.patch.object(startup, "install_cloudflare_quick", side_effect=install), \
                 mock.patch.object(startup, "verify_endpoint", return_value={"health_ok": True, "protected_auth_ok": True}), \
                 mock.patch.object(startup, "write_permanent_schema", return_value=root / "schema.json"), \
                 ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _: startup.ensure_endpoint(root, data, "key"), range(2)))
            self.assertEqual(1, len(calls))
            self.assertEqual(2, len(results))


    def test_explicit_ngrok_switch_replaces_healthy_quick_provider(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "root"
            data = Path(td) / "data"
            root.mkdir()
            data.mkdir()
            endpoint.atomic_json(
                data / endpoint.PERMANENT_CONFIG,
                {
                    "provider": endpoint.CLOUDFLARE_QUICK_PROVIDER,
                    "public_url": "https://healthy-quick.trycloudflare.com",
                    "permanent": False,
                },
            )
            schema = root / "openapi_actions_PERMANENT.json"
            with (
                mock.patch.object(startup, "find_ngrok", return_value="trusted-ngrok"),
                mock.patch.object(startup, "validate_ngrok_config", return_value=(True, "ok")),
                mock.patch.object(
                    startup,
                    "start_ngrok_user_endpoint",
                    return_value={"public_url": "https://stable.ngrok.app"},
                ),
                mock.patch.object(
                    startup,
                    "verify_endpoint",
                    return_value={"health_ok": True, "protected_auth_ok": True},
                ),
                mock.patch.object(
                    startup,
                    "stop_owned_quick_tunnel",
                    return_value={"status": "STOPPED"},
                ) as stop_quick,
                mock.patch.object(startup, "write_permanent_schema", return_value=schema),
                mock.patch.object(
                    startup,
                    "save_permanent_config",
                    return_value=data / endpoint.PERMANENT_CONFIG,
                ) as save,
            ):
                result = startup.switch_to_ngrok_endpoint(root, data, "api-key")

            self.assertEqual(endpoint.NGROK_PROVIDER, result["provider"])
            self.assertEqual("https://stable.ngrok.app", result["public_url"])
            self.assertTrue(result["stable_hostname"])
            stop_quick.assert_called_once_with(data)
            self.assertEqual(endpoint.NGROK_PROVIDER, save.call_args.args[0])

    def test_ngrok_switch_refuses_unowned_quick_and_preserves_provider_config(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "root"
            data = Path(td) / "data"
            root.mkdir()
            data.mkdir()
            config_path = data / endpoint.PERMANENT_CONFIG
            endpoint.atomic_json(
                config_path,
                {
                    "provider": endpoint.CLOUDFLARE_QUICK_PROVIDER,
                    "public_url": "https://healthy-quick.trycloudflare.com",
                    "permanent": False,
                },
            )
            before = config_path.read_bytes()
            with (
                mock.patch.object(startup, "find_ngrok", return_value="trusted-ngrok"),
                mock.patch.object(startup, "validate_ngrok_config", return_value=(True, "ok")),
                mock.patch.object(
                    startup,
                    "start_ngrok_user_endpoint",
                    return_value={"public_url": "https://stable.ngrok.app"},
                ),
                mock.patch.object(
                    startup,
                    "verify_endpoint",
                    return_value={"health_ok": True, "protected_auth_ok": True},
                ),
                mock.patch.object(
                    startup,
                    "stop_owned_quick_tunnel",
                    return_value={"status": "REFUSED", "reason": "identity mismatch"},
                ),
                mock.patch.object(startup, "save_permanent_config") as save,
            ):
                with self.assertRaises(startup.EndpointRecoveryRequired):
                    startup.switch_to_ngrok_endpoint(root, data, "api-key")

            self.assertEqual(before, config_path.read_bytes())
            save.assert_not_called()

    def test_endpoint_lock_does_not_reclaim_live_owner_when_cim_is_transiently_missing(self):
        with tempfile.TemporaryDirectory() as td:
            data = Path(td)
            lock = data / endpoint.ENDPOINT_OPERATION_LOCK
            endpoint.atomic_json(lock, {"pid": 4242, "nonce": "owner", "created_unix": 1})
            with (
                mock.patch.object(endpoint, "_quick_process_identity", return_value=None),
                mock.patch.object(endpoint, "_pid_liveness", return_value=True),
                mock.patch.object(endpoint.time, "monotonic", side_effect=[0.0, 2.0]),
                mock.patch.object(endpoint.time, "sleep"),
            ):
                with self.assertRaisesRegex(RuntimeError, "timed out"):
                    with endpoint.endpoint_operation_lock(data, timeout_seconds=1):
                        self.fail("a live owner lock must not be reclaimed")
            self.assertEqual("owner", endpoint.load_json(lock).get("nonce"))

    def test_endpoint_lock_reclaims_only_os_proven_dead_owner(self):
        with tempfile.TemporaryDirectory() as td:
            data = Path(td)
            lock = data / endpoint.ENDPOINT_OPERATION_LOCK
            endpoint.atomic_json(lock, {"pid": 4242, "nonce": "dead", "created_unix": 1})
            with (
                mock.patch.object(endpoint, "_quick_process_identity", return_value=None),
                mock.patch.object(endpoint, "_pid_liveness", return_value=False),
            ):
                with endpoint.endpoint_operation_lock(data):
                    owner = endpoint.load_json(lock)
                    self.assertEqual(os.getpid(), owner["pid"])
                    self.assertNotEqual("dead", owner["nonce"])
            self.assertFalse(lock.exists())

    def test_setup_labels_quick_as_temporary_and_cli_as_public_https(self):
        source = Path(startup.__file__).read_text(encoding="utf-8")
        self.assertIn("Temporary public HTTPS URL", source)
        self.assertIn("Stable public HTTPS URL", source)
        self.assertIn("automatic backend + public HTTPS startup", source)
        self.assertNotIn("automatic backend + permanent HTTPS startup", source)

if __name__ == "__main__":
    unittest.main()
