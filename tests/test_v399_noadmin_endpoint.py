from __future__ import annotations
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import world_engine_permanent_endpoint as pe

class V399NoAdminEndpointTests(unittest.TestCase):
    def test_ngrok_config_path_is_persistent_user_data(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(Path(td)/"ngrok.yml", pe.ngrok_config_path(Path(td)))

    def test_ngrok_public_url_selects_https_world_engine_tunnel(self):
        payload=[
            {"public_url":"http://bad.example","config":{"addr":"http://localhost:8000"}},
            {"public_url":"https://stable.ngrok-free.app","config":{"addr":"http://localhost:8000"}},
        ]
        with mock.patch.object(pe,"_ngrok_tunnels",return_value=payload):
            self.assertEqual("https://stable.ngrok-free.app",pe.ngrok_public_url())

    def test_user_startup_installer_requires_no_service(self):
        import inspect
        src=inspect.getsource(pe.install_user_startup_bootstrap)
        self.assertIn("Start Menu",src)
        self.assertIn("Startup",src)
        self.assertNotIn("service install",src.lower())
        self.assertNotIn("runas",src.lower())

    def test_start_ngrok_reuses_existing_endpoint(self):
        with tempfile.TemporaryDirectory() as td:
            alias=Path(td)/"WindowsApps"/"ngrok.exe"
            with mock.patch.object(pe,"_canonical_ngrok_alias",return_value=alias), mock.patch.object(pe,"ngrok_public_url",return_value="https://stable.ngrok-free.app"), mock.patch.object(pe,"_trusted_ngrok_listener",return_value=True):
                result=pe.start_ngrok_user_endpoint(str(alias),data=Path(td),expected_url="https://stable.ngrok-free.app")
            self.assertEqual("ALREADY_RUNNING",result["status"])

    def test_start_ngrok_refuses_hostname_drift(self):
        with tempfile.TemporaryDirectory() as td:
            alias=Path(td)/"WindowsApps"/"ngrok.exe"
            with mock.patch.object(pe,"_canonical_ngrok_alias",return_value=alias), mock.patch.object(pe,"ngrok_public_url",return_value="https://different.ngrok-free.app"), mock.patch.object(pe,"_trusted_ngrok_listener",return_value=True):
                with self.assertRaisesRegex(RuntimeError,"expects"):
                    pe.start_ngrok_user_endpoint(str(alias),data=Path(td),expected_url="https://stable.ngrok-free.app")

    def test_ensure_runtime_self_heals_ngrok(self):
        with tempfile.TemporaryDirectory() as td:
            data=Path(td)
            pe.save_permanent_config(pe.NGROK_PROVIDER,"https://stable.ngrok-free.app","a"*32,data=data)
            with mock.patch.object(pe,"find_ngrok",return_value="ngrok.exe"), mock.patch.object(pe,"start_ngrok_user_endpoint",return_value={"status":"STARTED","public_url":"https://stable.ngrok-free.app"}):
                result=pe.ensure_permanent_runtime(data=data)
            self.assertEqual("RUNNING",result["status"])
            self.assertEqual(pe.NGROK_PROVIDER,result["provider"])

    def test_ensure_runtime_restarts_existing_cloudflare_service_without_token(self):
        with tempfile.TemporaryDirectory() as td:
            data=Path(td)
            completed=mock.Mock(returncode=0,stdout="START_PENDING",stderr="")
            config={"provider":pe.CLOUDFLARE_PROVIDER,"public_url":"https://worldengine.example.com"}
            with mock.patch.object(pe,"load_permanent_config",return_value=config), \
                 mock.patch.object(pe.os,"name","nt"), \
                 mock.patch.object(pe,"_windows_system_executable",return_value=r"C:\Windows\System32\sc.exe"), \
                 mock.patch.object(pe,"run",return_value=completed) as run:
                result=pe.ensure_permanent_runtime(data=data)
            self.assertEqual("RUNNING",result["status"])
            self.assertEqual(pe.CLOUDFLARE_PROVIDER,result["provider"])
            self.assertEqual("cloudflared",result["service"])
            run.assert_called_once_with([r"C:\Windows\System32\sc.exe","start","cloudflared"],timeout=60)

    def test_ensure_runtime_treats_numeric_cloudflare_already_running_as_success(self):
        with tempfile.TemporaryDirectory() as td:
            data=Path(td)
            completed=mock.Mock(returncode=1056,stdout="",stderr="")
            config={"provider":pe.CLOUDFLARE_PROVIDER,"public_url":"https://worldengine.example.com"}
            with mock.patch.object(pe,"load_permanent_config",return_value=config), \
                 mock.patch.object(pe.os,"name","nt"), \
                 mock.patch.object(pe,"_windows_system_executable",return_value=r"C:\Windows\System32\sc.exe"), \
                 mock.patch.object(pe,"run",return_value=completed):
                result=pe.ensure_permanent_runtime(data=data)
            self.assertEqual("RUNNING",result["status"])
            self.assertEqual(pe.CLOUDFLARE_PROVIDER,result["provider"])

    def test_ensure_runtime_leaves_cloudflare_external_off_windows(self):
        with tempfile.TemporaryDirectory() as td:
            data=Path(td)
            config={"provider":pe.CLOUDFLARE_PROVIDER,"public_url":"https://worldengine.example.com"}
            with mock.patch.object(pe,"load_permanent_config",return_value=config), \
                 mock.patch.object(pe.os,"name","posix"), \
                 mock.patch.object(pe,"run") as run:
                result=pe.ensure_permanent_runtime(data=data)
            self.assertEqual("EXTERNAL_PROVIDER",result["status"])
            self.assertEqual(pe.CLOUDFLARE_PROVIDER,result["provider"])
            run.assert_not_called()

    def test_ensure_runtime_reports_cloudflare_service_start_failure(self):
        with tempfile.TemporaryDirectory() as td:
            data=Path(td)
            completed=mock.Mock(returncode=5,stdout="",stderr="Access is denied")
            config={"provider":pe.CLOUDFLARE_PROVIDER,"public_url":"https://worldengine.example.com"}
            with mock.patch.object(pe,"load_permanent_config",return_value=config), \
                 mock.patch.object(pe.os,"name","nt"), \
                 mock.patch.object(pe,"_windows_system_executable",return_value=r"C:\Windows\System32\sc.exe"), \
                 mock.patch.object(pe,"run",return_value=completed):
                result=pe.ensure_permanent_runtime(data=data)
            self.assertEqual("FAILED",result["status"])
            self.assertEqual(pe.CLOUDFLARE_PROVIDER,result["provider"])
            self.assertIn("Access is denied",result["error"])

    @unittest.skipUnless(os.name=="nt","Windows system directory resolution")
    def test_cloudflare_recovery_resolves_sc_from_windows_system_directory(self):
        resolved=Path(pe._windows_system_executable("sc.exe"))
        self.assertEqual("sc.exe",resolved.name.lower())
        self.assertTrue(resolved.is_file())

    def test_cloudflare_installer_uses_absolute_windows_system_sc(self):
        completed=mock.Mock(returncode=0,stdout="",stderr="")
        with mock.patch.object(pe.os,"name","nt"), \
             mock.patch.object(pe,"find_cloudflared",return_value=r"C:\WorldEngine\cloudflared.exe"), \
             mock.patch.object(pe,"_windows_system_executable",return_value=r"C:\Windows\System32\sc.exe"), \
             mock.patch.object(pe,"run",return_value=completed) as run:
            result=pe.install_cloudflare_named_service(
                "cloudflare-service-token",
                "https://worldengine.example.com",
            )
        self.assertEqual("https://worldengine.example.com",result)
        self.assertEqual(
            [r"C:\Windows\System32\sc.exe","start","cloudflared"],
            run.call_args_list[1].args[0],
        )

    def test_default_bat_does_not_request_admin(self):
        root=Path(__file__).resolve().parents[1]
        text=(root/"INSTALL_PERMANENT_ENDPOINT.bat").read_text(encoding="utf-8")
        self.assertNotIn("RunAs",text)
        self.assertNotIn("net session",text)
        self.assertIn("--provider auto",text)
        self.assertNotIn("click its Copy button",text)

    def test_optional_admin_tailscale_is_separate(self):
        root=Path(__file__).resolve().parents[1]
        text=(root/"INSTALL_TAILSCALE_UNATTENDED_ADMIN.bat").read_text(encoding="utf-8")
        self.assertIn("RunAs",text)
        self.assertIn("tailscale-admin",text)

if __name__=='__main__': unittest.main()
