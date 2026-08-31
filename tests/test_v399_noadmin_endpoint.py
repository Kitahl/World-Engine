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

    def test_default_bat_does_not_request_admin(self):
        root=Path(__file__).resolve().parents[1]
        text=(root/"INSTALL_PERMANENT_ENDPOINT.bat").read_text(encoding="utf-8")
        self.assertNotIn("RunAs",text)
        self.assertNotIn("net session",text)
        self.assertIn("--provider ngrok",text)

    def test_optional_admin_tailscale_is_separate(self):
        root=Path(__file__).resolve().parents[1]
        text=(root/"INSTALL_TAILSCALE_UNATTENDED_ADMIN.bat").read_text(encoding="utf-8")
        self.assertIn("RunAs",text)
        self.assertIn("tailscale-admin",text)

if __name__=='__main__': unittest.main()
