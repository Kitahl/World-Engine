from __future__ import annotations

import inspect
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import world_engine_permanent_endpoint as endpoint


STORE_PRODUCT_ID = "9MVS1J51GMK6"
NGROK_PACKAGE_FAMILY = "ngrok.ngrok_1g87z0zv29zzc"
WINGET_PACKAGE_FAMILY = "Microsoft.DesktopAppInstaller_8wekyb3d8bbwe"
EMPTY_CLEANUP = {"removed": [], "failed": [], "refused": []}


class SafeNgrokStoreTests(unittest.TestCase):
    def _completed(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)

    def test_store_install_command_is_pinned_to_the_ngrok_store_package(self):
        self.assertEqual(STORE_PRODUCT_ID, endpoint.NGROK_WINDOWS_STORE_PRODUCT_ID)
        self.assertEqual(
            (
                "winget", "install", "--id", STORE_PRODUCT_ID, "--exact",
                "--source", "msstore", "--accept-source-agreements",
                "--accept-package-agreements", "--disable-interactivity", "--silent",
            ),
            endpoint.NGROK_WINDOWS_INSTALL_COMMAND,
        )

    def test_no_direct_ngrok_download_or_path_executable_discovery_remains(self):
        source = Path(endpoint.__file__).read_text(encoding="utf-8")
        installer_source = inspect.getsource(endpoint.download_portable_ngrok_windows)
        discovery_source = inspect.getsource(endpoint.find_ngrok)

        self.assertNotIn("bin.ngrok.com", source)
        self.assertNotIn("ngrok-windows-amd64.zip", installer_source)
        self.assertNotIn("urlopen", installer_source)
        self.assertNotIn("ZipFile", installer_source)
        self.assertNotIn("shutil.which", installer_source)

    def test_arbitrary_path_ngrok_is_rejected_when_no_trusted_alias_exists(self):
        with mock.patch.object(endpoint.os, "name", "nt"), \
             mock.patch.object(endpoint, "_remove_legacy_portable_ngrok", return_value=EMPTY_CLEANUP), \
             mock.patch.object(endpoint, "_windows_app_alias", return_value=None), \
             mock.patch.object(endpoint.shutil, "which", return_value=r"C:\\untrusted\\ngrok.exe") as which:
            self.assertIsNone(endpoint.find_ngrok())
        which.assert_not_called()

    def test_broken_trusted_alias_is_not_reused(self):
        alias = Path(r"C:\\Users\\test\\AppData\\Local\\Microsoft\\WindowsApps\\ngrok.exe")
        with mock.patch.object(endpoint.os, "name", "nt"), \
             mock.patch.object(endpoint, "_remove_legacy_portable_ngrok", return_value=EMPTY_CLEANUP), \
             mock.patch.object(endpoint, "_windows_app_alias", return_value=alias), \
             mock.patch.object(endpoint, "_probe_ngrok_executable", return_value=False):
            self.assertIsNone(endpoint.find_ngrok())

    def test_legacy_cache_cleanup_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            data = Path(temp_dir)
            tools = data / "tools"
            tools.mkdir()
            (tools / "ngrok.exe").write_bytes(b"obsolete executable")
            (tools / "ngrok-windows-amd64.zip.download").write_bytes(b"obsolete archive")

            with mock.patch.object(endpoint, "_persistent_data_dir_lexical", return_value=data):
                first = endpoint._remove_legacy_portable_ngrok()
                second = endpoint._remove_legacy_portable_ngrok()

            self.assertFalse((tools / "ngrok.exe").exists())
            self.assertFalse((tools / "ngrok-windows-amd64.zip.download").exists())
            self.assertEqual(2, len(first["removed"]))
            self.assertEqual(EMPTY_CLEANUP, second)

    def test_cleanup_runs_before_a_trusted_preinstalled_alias_is_reused(self):
        alias = Path(r"C:\\Users\\test\\AppData\\Local\\Microsoft\\WindowsApps\\ngrok.exe")
        events: list[str] = []

        def cleanup() -> dict[str, list[str]]:
            events.append("cleanup")
            return EMPTY_CLEANUP

        def alias_lookup(filename: str, expected_package_family: str) -> Path | None:
            self.assertEqual("ngrok.exe", filename)
            self.assertEqual(NGROK_PACKAGE_FAMILY, expected_package_family)
            events.append("alias")
            return alias

        def probe(candidate: Path | str) -> bool:
            self.assertEqual(alias, candidate)
            events.append("probe")
            return True

        with mock.patch.object(endpoint.os, "name", "nt"), \
             mock.patch.object(endpoint, "_remove_legacy_portable_ngrok", side_effect=cleanup), \
             mock.patch.object(endpoint, "_windows_app_alias", side_effect=alias_lookup), \
             mock.patch.object(endpoint, "_probe_ngrok_executable", side_effect=probe), \
             mock.patch.object(endpoint, "_run_packaged") as run:
            self.assertEqual(str(alias), endpoint.download_portable_ngrok_windows())

        self.assertEqual(["cleanup", "alias", "probe"], events)
        run.assert_not_called()

    def test_missing_or_path_winget_fails_closed_without_network_download(self):
        with mock.patch.object(endpoint.os, "name", "nt"), \
             mock.patch.object(endpoint, "find_ngrok", return_value=None), \
             mock.patch.object(endpoint, "_windows_app_alias", return_value=None), \
             mock.patch.object(endpoint.shutil, "which", return_value=r"C:\\untrusted\\winget.exe") as which, \
             mock.patch.object(endpoint, "_run_packaged") as run:
            with self.assertRaisesRegex(RuntimeError, "WinGet|Microsoft Store"):
                endpoint.download_portable_ngrok_windows()
        run.assert_not_called()
        which.assert_not_called()

    def test_nonzero_winget_fails_closed_without_fallback(self):
        winget = Path(r"C:\\Users\\test\\AppData\\Local\\Microsoft\\WindowsApps\\winget.exe")
        with mock.patch.object(endpoint.os, "name", "nt"), \
             mock.patch.object(endpoint, "find_ngrok", return_value=None), \
             mock.patch.object(endpoint, "_windows_app_alias", return_value=winget), \
             mock.patch.object(endpoint, "_probe_winget_executable", return_value=True), \
             mock.patch.object(endpoint, "_trusted_msstore_source", return_value=True), \
             mock.patch.object(endpoint, "_run_packaged", return_value=self._completed(1, stderr="store failure")) as run:
            with self.assertRaisesRegex(RuntimeError, "Microsoft Store|WinGet"):
                endpoint.download_portable_ngrok_windows()

        run.assert_called_once_with(
            [str(winget), *endpoint.NGROK_WINDOWS_INSTALL_COMMAND[1:]],
            WINGET_PACKAGE_FAMILY,
            timeout=600,
        )

    def test_timed_out_winget_fails_closed_without_fallback(self):
        winget = Path(r"C:\\Users\\test\\AppData\\Local\\Microsoft\\WindowsApps\\winget.exe")
        with mock.patch.object(endpoint.os, "name", "nt"), \
             mock.patch.object(endpoint, "find_ngrok", return_value=None), \
             mock.patch.object(endpoint, "_windows_app_alias", return_value=winget), \
             mock.patch.object(endpoint, "_probe_winget_executable", return_value=True), \
             mock.patch.object(endpoint, "_trusted_msstore_source", return_value=True), \
             mock.patch.object(endpoint, "_run_packaged", side_effect=subprocess.TimeoutExpired([str(winget)], 600)):
            with self.assertRaisesRegex(RuntimeError, "timed out|WinGet|Microsoft Store"):
                endpoint.download_portable_ngrok_windows()

    def test_untrusted_store_source_metadata_fails_closed(self):
        winget = Path(r"C:\\Users\\test\\AppData\\Local\\Microsoft\\WindowsApps\\winget.exe")
        bad_source = self._completed(
            stdout='{"Name":"msstore","Identifier":"wrong","Arg":"https://storeedgefd.dsx.mp.microsoft.com/v9.0","TrustLevel":["Trusted"]}',
        )
        with mock.patch.object(endpoint, "_run_packaged", return_value=bad_source) as run:
            self.assertFalse(endpoint._trusted_msstore_source(winget))
        run.assert_called_once_with(
            [str(winget), "source", "export", "msstore"],
            WINGET_PACKAGE_FAMILY,
            timeout=30,
        )

    def test_packaged_process_family_mismatch_fails_before_command_output_is_trusted(self):
        winget = Path(r"C:\\Users\\test\\AppData\\Local\\Microsoft\\WindowsApps\\winget.exe")
        process = mock.MagicMock()
        process.communicate.return_value = ("", "")
        popen = mock.MagicMock()
        popen.return_value.__enter__.return_value = process

        with mock.patch.object(endpoint.os, "name", "nt"), \
             mock.patch.object(endpoint.subprocess, "Popen", popen), \
             mock.patch.object(endpoint, "_is_app_execution_alias", return_value=True), \
             mock.patch.object(endpoint, "_process_package_family", return_value="untrusted.package_123"):
            with self.assertRaisesRegex(RuntimeError, "package identity"):
                endpoint._run_packaged([str(winget), "--version"], WINGET_PACKAGE_FAMILY, timeout=15)

        process.kill.assert_called_once()

    def test_delayed_alias_is_probed_until_a_healthy_store_alias_appears(self):
        ngrok = Path(r"C:\\Users\\test\\AppData\\Local\\Microsoft\\WindowsApps\\ngrok.exe")
        winget = Path(r"C:\\Users\\test\\AppData\\Local\\Microsoft\\WindowsApps\\winget.exe")
        with mock.patch.object(endpoint.os, "name", "nt"), \
             mock.patch.object(endpoint, "find_ngrok", side_effect=[None, None, str(ngrok)]), \
             mock.patch.object(endpoint, "_windows_app_alias", return_value=winget), \
             mock.patch.object(endpoint, "_probe_winget_executable", return_value=True), \
             mock.patch.object(endpoint, "_trusted_msstore_source", return_value=True), \
             mock.patch.object(endpoint, "_run_packaged", return_value=self._completed()) as run, \
             mock.patch.object(endpoint.time, "sleep") as sleep:
            self.assertEqual(str(ngrok), endpoint.download_portable_ngrok_windows())

        run.assert_called_once_with(
            [str(winget), *endpoint.NGROK_WINDOWS_INSTALL_COMMAND[1:]],
            WINGET_PACKAGE_FAMILY,
            timeout=600,
        )
        self.assertGreaterEqual(sleep.call_count, 1)


    def test_non_alias_executable_is_rejected_before_popen(self):
        winget = Path(r"C:\\Users\\test\\AppData\\Local\\Microsoft\\WindowsApps\\winget.exe")
        with mock.patch.object(endpoint.os, "name", "nt"), \
             mock.patch.object(endpoint, "_is_app_execution_alias", return_value=False), \
             mock.patch.object(endpoint.subprocess, "Popen") as popen:
            with self.assertRaisesRegex(RuntimeError, "App Execution Alias"):
                endpoint._run_packaged([str(winget), "--version"], WINGET_PACKAGE_FAMILY, timeout=15)
        popen.assert_not_called()

    def test_packaged_command_success_and_timeout_reap_paths(self):
        winget = Path(r"C:\\Users\\test\\AppData\\Local\\Microsoft\\WindowsApps\\winget.exe")
        success_process = mock.MagicMock()
        success_process.returncode = 0
        success_process.communicate.return_value = ("v1.9.0", "")
        success_popen = mock.MagicMock()
        success_popen.return_value.__enter__.return_value = success_process
        with mock.patch.object(endpoint.os, "name", "nt"), \
             mock.patch.object(endpoint, "_is_app_execution_alias", return_value=True), \
             mock.patch.object(endpoint.subprocess, "Popen", success_popen), \
             mock.patch.object(endpoint, "_process_package_family", return_value=WINGET_PACKAGE_FAMILY):
            result = endpoint._run_packaged([str(winget), "--version"], WINGET_PACKAGE_FAMILY, timeout=15)
        self.assertEqual((0, "v1.9.0"), (result.returncode, result.stdout))
        success_process.communicate.assert_called_once_with(timeout=15)

        timeout_process = mock.MagicMock()
        timeout_process.communicate.side_effect = [subprocess.TimeoutExpired([str(winget)], 15), ("", "")]
        timeout_popen = mock.MagicMock()
        timeout_popen.return_value.__enter__.return_value = timeout_process
        with mock.patch.object(endpoint.os, "name", "nt"), \
             mock.patch.object(endpoint, "_is_app_execution_alias", return_value=True), \
             mock.patch.object(endpoint.subprocess, "Popen", timeout_popen), \
             mock.patch.object(endpoint, "_process_package_family", return_value=WINGET_PACKAGE_FAMILY):
            with self.assertRaises(subprocess.TimeoutExpired):
                endpoint._run_packaged([str(winget), "--version"], WINGET_PACKAGE_FAMILY, timeout=15)
        timeout_process.kill.assert_called_once()
        self.assertEqual(2, timeout_process.communicate.call_count)

    def test_store_source_requires_object_type_and_explicit_pins(self):
        winget = Path(r"C:\\Users\\test\\AppData\\Local\\Microsoft\\WindowsApps\\winget.exe")
        valid = {
            "Name": "msstore", "Identifier": "StoreEdgeFD",
            "Arg": "https://storeedgefd.dsx.mp.microsoft.com/v9.0",
            "Type": "Microsoft.Rest", "Explicit": False, "TrustLevel": ["Trusted"],
        }
        for value, expected in (
            (valid, True),
            ([], False),
            ({**valid, "Type": "Wrong.Type"}, False),
            ({**valid, "Explicit": True}, False),
        ):
            with self.subTest(value=value), \
                 mock.patch.object(endpoint, "_run_packaged", return_value=self._completed(stdout=endpoint.json.dumps(value))):
                self.assertIs(expected, endpoint._trusted_msstore_source(winget))

    def test_legacy_cleanup_refuses_reparse_paths_and_noncanonical_command_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            data = Path(temp_dir)
            tools = data / "tools"
            tools.mkdir()
            for answers in ([True], [False, True]):
                with self.subTest(answers=answers), \
                     mock.patch.object(endpoint, "_persistent_data_dir_lexical", return_value=data), \
                     mock.patch.object(endpoint, "_path_has_reparse_component", side_effect=answers):
                    report = endpoint._remove_legacy_portable_ngrok()
                self.assertEqual({"removed": [], "failed": [], "refused": [str(tools)]}, report)
        with mock.patch.object(endpoint, "_canonical_ngrok_alias", return_value=None), \
             mock.patch.object(endpoint, "_run_packaged") as run:
            with self.assertRaisesRegex(RuntimeError, "canonical package-bound"):
                endpoint.run_ngrok_command(r"C:\\untrusted\\ngrok.exe", ["version"], timeout=15)
        run.assert_not_called()

    def test_new_tunnel_process_pfn_mismatch_terminates_and_fails(self):
        alias = Path(r"C:\\Users\\test\\AppData\\Local\\Microsoft\\WindowsApps\\ngrok.exe")
        process = mock.MagicMock()
        process.pid = 1234
        with tempfile.TemporaryDirectory() as temp_dir:
            data = Path(temp_dir)
            endpoint.ngrok_config_path(data).write_text("version: 3\n", encoding="utf-8")
            with mock.patch.object(endpoint.os, "name", "nt"), \
                 mock.patch.object(endpoint, "_canonical_ngrok_alias", return_value=alias), \
                 mock.patch.object(endpoint, "ngrok_public_url", return_value=None), \
                 mock.patch.object(endpoint.subprocess, "Popen", return_value=process), \
                 mock.patch.object(endpoint, "_process_package_family", return_value="untrusted.package_123"):
                with self.assertRaisesRegex(RuntimeError, "package identity"):
                    endpoint.start_ngrok_user_endpoint(str(alias), data=data)
        process.terminate.assert_called_once()
        process.wait.assert_called_once_with(timeout=5)

    def test_non_windows_bounded_command_preserves_existing_platform_support(self):
        completed = self._completed(stdout="ngrok version 3.0.0")
        with mock.patch.object(endpoint.os, "name", "posix"), \
             mock.patch.object(endpoint.subprocess, "run", return_value=completed) as run:
            result = endpoint.run_ngrok_command("/usr/local/bin/ngrok", ["version"], timeout=15)
        self.assertIs(completed, result)
        run.assert_called_once_with(
            ["/usr/local/bin/ngrok", "version"],
            capture_output=True,
            text=True,
            timeout=15,
        )

    def test_windows_existing_endpoint_reuse_requires_trusted_ngrok_listener(self):
        alias = Path(r"C:\\Users\\test\\AppData\\Local\\Microsoft\\WindowsApps\\ngrok.exe")
        public_url = "https://stable.ngrok-free.app"
        with tempfile.TemporaryDirectory() as temp_dir:
            data = Path(temp_dir)
            with mock.patch.object(endpoint.os, "name", "nt"), \
                 mock.patch.object(endpoint, "_canonical_ngrok_alias", return_value=alias), \
                 mock.patch.object(endpoint, "ngrok_public_url", return_value=public_url), \
                 mock.patch.object(endpoint, "_trusted_ngrok_listener", return_value=False), \
                 mock.patch.object(endpoint.subprocess, "Popen") as popen:
                with self.assertRaisesRegex(RuntimeError, "not the pinned Microsoft Store package"):
                    endpoint.start_ngrok_user_endpoint(str(alias), data=data, expected_url=public_url)
            popen.assert_not_called()
            with mock.patch.object(endpoint.os, "name", "nt"), \
                 mock.patch.object(endpoint, "_canonical_ngrok_alias", return_value=alias), \
                 mock.patch.object(endpoint, "ngrok_public_url", return_value=public_url), \
                 mock.patch.object(endpoint, "_trusted_ngrok_listener", return_value=True):
                result = endpoint.start_ngrok_user_endpoint(str(alias), data=data, expected_url=public_url)
        self.assertEqual({"status": "ALREADY_RUNNING", "public_url": public_url, "pid": None}, result)

    def test_cleanup_uses_lexical_root_and_refuses_reparse_components(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            lexical = Path(temp_dir) / "lexical-root"
            lexical.mkdir()
            tools = lexical / "tools"
            with mock.patch.object(endpoint, "_persistent_data_dir_lexical", return_value=lexical), \
                 mock.patch.object(endpoint, "persistent_data_dir", side_effect=AssertionError("resolved root must not be used")) as resolved, \
                 mock.patch.object(endpoint, "_path_has_reparse_component", side_effect=[False, True]) as reparse:
                report = endpoint._remove_legacy_portable_ngrok()
        self.assertEqual({"removed": [], "failed": [], "refused": [str(tools)]}, report)
        resolved.assert_not_called()
        self.assertEqual([mock.call(lexical), mock.call(tools)], reparse.call_args_list)

if __name__ == "__main__":
    unittest.main()
