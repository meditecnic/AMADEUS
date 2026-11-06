"""Portable rejection probes for the installed-desktop release gate."""
import contextlib
import io
import json
import os
from pathlib import Path, PureWindowsPath
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import install_smoke as smoke


class InstallSmokeLogicTests(unittest.TestCase):
    def test_arguments_bound_timeouts(self):
        common = ["--installer", "setup.exe", "--evidence-dir", "evidence"]
        self.assertEqual(smoke.arguments(common).startup_timeout, 120)
        for value in ("0", "-1", "301", "nan", "inf"):
            with self.subTest(value=value), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    smoke.arguments([*common, "--startup-timeout", value])

    def test_existing_identity_root_refused_even_without_data_subdirectory(self):
        with tempfile.TemporaryDirectory() as temp:
            local = Path(temp)
            self.assertEqual(smoke.check_clean(local), local / smoke.IDENTIFIER / "data")
            (local / smoke.IDENTIFIER).mkdir()
            with self.assertRaisesRegex(RuntimeError, "Refusing existing user data"):
                smoke.check_clean(local)

    def test_nsis_quoted_install_location_is_resolved(self):
        winreg = MagicMock()
        winreg.QueryInfoKey.return_value = (1, 0, 0)
        winreg.QueryValueEx.side_effect = [("Amadeus", 1), ('"C:\\Apps\\Amadeus"', 1)]
        with patch.dict(sys.modules, {"winreg": winreg}):
            self.assertEqual(smoke.registration(), {"location": r"C:\Apps\Amadeus"})

    def test_logs_strip_auth_headers_and_url_queries(self):
        token = "a" * 43
        self.assertNotIn(token, smoke.safe_text("Bearer " + token))
        self.assertEqual(smoke.safe_url(f"https://user:secret@example.com/path?token={token}#fragment"),
                         "https://example.com/path")
        self.assertTrue(smoke.is_update_url("https://github.com/meditecnic/AMADEUS/releases/latest/download/latest.json"))
        self.assertTrue(smoke.is_update_url("https://github.com/meditecnic/AMADEUS/releases/download/v1/setup.exe"))
        self.assertFalse(smoke.is_update_url("http://127.0.0.1:1234/health"))

    def test_failed_installer_is_nonzero_and_has_timing(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(smoke.subprocess, "run", return_value=subprocess.CompletedProcess([], 17)):
                with self.assertRaisesRegex(RuntimeError, "install exited 17"):
                    smoke.command_step(["setup.exe", "/S"], "install", Path(temp), {})
            with patch.object(smoke.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)):
                result = smoke.command_step(["setup.exe", "/S"], "install", Path(temp), {})
                self.assertEqual(result["exit_code"], 0)
                self.assertGreaterEqual(result["seconds"], 0)

    def test_timeout_has_stage_specific_diagnostic(self):
        ticks = Mock()
        with patch.object(smoke.time, "monotonic", side_effect=[0, 0, 2]), patch.object(smoke.time, "sleep"):
            with self.assertRaisesRegex(RuntimeError, "residual backend"):
                smoke.wait_until(lambda: False, 1, "residual backend", ticks)
        ticks.assert_called_once()

    def test_main_emits_result_json_on_success_and_failure(self):
        for failure in (None, RuntimeError("second backend detected")):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temp:
                root = Path(temp) / "evidence"
                installer = Path(temp) / "setup.exe"
                installer.touch()

                @contextlib.contextmanager
                def fake(*args, **kwargs):
                    yield 1234, []

                output = io.StringIO()
                with patch.object(smoke, "os", SimpleNamespace(name="nt")), patch.object(smoke, "fake_service", fake), \
                        patch.object(smoke, "exercise", side_effect=failure), contextlib.redirect_stdout(output):
                    code = smoke.main(["--installer", str(installer), "--evidence-dir", str(root)])
                result = json.loads((root / "result.json").read_text(encoding="utf-8"))
                self.assertEqual(result["ok"], failure is None)
                self.assertEqual(code, 0 if failure is None else 1)
                if failure:
                    self.assertNotIn("INSTALL_SMOKE_OK", output.getvalue())
                    self.assertIn("second backend detected", output.getvalue())
                else:
                    payload = output.getvalue().split("INSTALL_SMOKE_OK ", 1)[1]
                    self.assertEqual(json.loads(payload), result)

    def test_bad_installer_step_makes_entire_gate_fail_with_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            root, installer = Path(temp) / "evidence", Path(temp) / "setup.exe"
            installer.touch()

            @contextlib.contextmanager
            def fake(*args, **kwargs):
                yield 1234, []

            observed = io.StringIO()
            with patch.object(smoke, "os", SimpleNamespace(name="nt", environ={"LOCALAPPDATA": temp})), \
                    patch.object(smoke, "fake_service", fake), patch.object(smoke, "registration", return_value=None), \
                    patch.object(smoke, "backend_pids", return_value=set()), \
                    patch.object(smoke, "sanitized_build_environment", return_value={}), \
                    patch.object(smoke.subprocess, "run", return_value=subprocess.CompletedProcess([], 17)), \
                    contextlib.redirect_stdout(observed):
                code = smoke.main(["--installer", str(installer), "--evidence-dir", str(root)])
            self.assertEqual(code, 1)
            result = json.loads((root / "result.json").read_text(encoding="utf-8"))
            self.assertFalse(result["ok"])
            self.assertIn("install exited 17", result["error"])
            self.assertIn("INSTALL_SMOKE_FAILED", observed.getvalue())
            self.assertNotIn("INSTALL_SMOKE_OK", observed.getvalue())

    @unittest.skipIf(os.name == "nt", "Linux platform rejection probe")
    def test_linux_rejected_without_installing(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(smoke, "exercise") as exercise:
            code = smoke.main(["--installer", "missing.exe", "--evidence-dir", str(Path(temp) / "evidence")])
            self.assertEqual(code, 1)
            exercise.assert_not_called()


class CDPTests(unittest.TestCase):
    def client(self):
        import websocket

        with patch.object(websocket, "create_connection", return_value=Mock()):
            return smoke.CDP("ws://127.0.0.1:9222/page")

    def test_replays_events_while_waiting_for_rpc_response(self):
        client = self.client()
        client.ws.recv.side_effect = [
            json.dumps({"method": "Network.requestWillBeSent", "params": {"request": {
                "method": "GET", "url": "http://127.0.0.1/health?token=secret", "headers": {"Authorization": "secret"}}}}),
            json.dumps({"id": 1, "result": {"result": {"value": True}}}),
        ]
        self.assertTrue(client.evaluate("true"))
        self.assertEqual(client.requests, [{"method": "GET", "url": "http://127.0.0.1/health"}])
        self.assertNotIn("secret", json.dumps(client.requests))

    def test_console_network_and_js_errors_fail_but_only_expected_404_allowed(self):
        client = self.client()
        for url, text, source in (
            ("http://127.0.0.1/api/asset-pack/manifest.json", "Failed to load resource: 404", "network"),
            ("http://127.0.0.1/api/other", "Failed to load resource: 404", "network"),
            ("http://127.0.0.1/api/asset-pack/manifest.json", "Failed to load resource: 500", "network"),
            ("", "Content Security Policy refused connection", "security"),
        ):
            client.event({"method": "Log.entryAdded", "params": {"entry": {
                "level": "error", "source": source, "text": text, "url": url}}})
        client.event({"method": "Runtime.consoleAPICalled", "params": {
            "type": "warning", "args": [{"value": "THREE.WebGLProgram X4122"}]}})
        client.event({"method": "Runtime.consoleAPICalled", "params": {
            "type": "error", "args": [{"value": "fatal render error"}]}})
        client.event({"method": "Runtime.exceptionThrown", "params": {"exceptionDetails": {"text": "Uncaught"}}})
        self.assertEqual(len(client.allowed), 1)
        self.assertEqual(len(client.errors), 5)

    def test_websocket_evidence_keeps_types_without_auth_payloads(self):
        client = self.client()
        for payload in ('{"type":"auth","token":"secret"}', '{"type":"chat.send","content":"no"}', 'ping'):
            client.event({"method": "Network.webSocketFrameSent", "params": {"response": {"payloadData": payload}}})
        self.assertEqual(client.sent_types, ["auth", "chat.send"])
        self.assertNotIn("secret", json.dumps(client.sent_types))


class NetworkAuditTests(unittest.TestCase):
    def row(self, address="127.0.0.1", application=r"\device\harddiskvolume3\Apps\Amadeus\desktop.exe", direction="%%14593"):
        return {"Application": application, "ProcessID": "0x1234", "DestAddress": address,
                "DestPort": "443", "Direction": direction}

    def inspect(self, rows, path):
        calibration = self.row(application=r"\device\python.exe")
        calibration.update(ProcessID=hex(os.getpid()), DestPort="4567")
        with patch.object(smoke, "powershell", return_value=[calibration, *rows]):
            return smoke.audit_finish(10, PureWindowsPath(r"C:\Apps\Amadeus"), 4567, path)

    def test_accepts_only_loopback_for_installed_native_processes(self):
        with tempfile.TemporaryDirectory() as temp:
            rows = self.inspect([self.row(), self.row("::1"), self.row("8.8.8.8", r"\device\runner.exe")],
                                Path(temp) / "network.json")
            self.assertEqual(len(rows), 2)

    def test_external_connection_attempt_and_missing_audit_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "network.json"
            for address in ("140.82.112.3", "::ffff:140.82.112.3", "0.0.0.0"):
                with self.subTest(address=address), self.assertRaisesRegex(RuntimeError, "external network"):
                    self.inspect([self.row(address)], path)
                self.assertIn(address, path.read_text(encoding="utf-8"))
            with patch.object(smoke, "powershell", return_value=[]):
                with self.assertRaisesRegex(RuntimeError, "calibration"):
                    smoke.audit_finish(10, PureWindowsPath(r"C:\Apps\Amadeus"), 4567, path)

    def test_webview2_debug_policy_is_set_for_launch_and_restored(self):
        values = {}

        @contextlib.contextmanager
        def create_key(hive, path, reserved, access):
            self.assertEqual((hive, path), ("HKLM", smoke.WEBVIEW2_POLICY))
            yield "key"

        def query(key, name):
            if name not in values:
                raise FileNotFoundError(name)
            return values[name]

        fake = SimpleNamespace(
            HKEY_LOCAL_MACHINE="HKLM", KEY_QUERY_VALUE=1, KEY_SET_VALUE=2, REG_SZ=1, CreateKeyEx=create_key,
            QueryValueEx=query, SetValueEx=lambda key, name, _, kind, value: values.__setitem__(name, (value, kind)),
            DeleteValue=lambda key, name: values.pop(name))
        with patch.dict(sys.modules, {"winreg": fake}):
            with smoke.webview2_debug_policy("desktop.exe", "--remote-debugging-port=4567"):
                self.assertEqual(values, {"desktop.exe": ("--remote-debugging-port=4567", 1)})
            self.assertEqual(values, {})
            values["desktop.exe"] = ("--keep", 1)
            with self.assertRaises(RuntimeError):
                with smoke.webview2_debug_policy("desktop.exe", "--remote-debugging-port=4567"):
                    raise RuntimeError("launch failed")
            self.assertEqual(values, {"desktop.exe": ("--keep", 1)})

    @unittest.skipUnless(os.name == "nt", "Win32 APIs require Windows")
    def test_native_snapshot_and_window_enumeration(self):
        self.assertIn(os.getpid(), smoke.process_snapshot())
        self.assertEqual(smoke.close_windows(0), 0)


if __name__ == "__main__":
    unittest.main()
