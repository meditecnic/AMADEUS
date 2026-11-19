"""Verify Tauri NSIS data-location hooks, JSON escaping, and silent CLI contracts (stdlib-only)."""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path, PureWindowsPath
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "packaging/windows"))
import install_smoke as smoke  # noqa: E402


def _find_makensis() -> Path | None:
    found = shutil.which("makensis")
    if found:
        return Path(found)
    local_app = os.environ.get("LOCALAPPDATA", "")
    if local_app:
        cached = Path(local_app) / "tauri/NSIS/makensis.exe"
        if cached.is_file():
            return cached
    return None


def _nsis_escape_json_string(raw_path: str) -> str:
    return raw_path.replace("\\", "\\\\").replace('"', '\\"')


class NsisHookConfigAndSmokeCliTests(unittest.TestCase):
    def test_tauri_conf_wires_nsis_installer_hooks_without_utf8_bom(self):
        conf_path = REPO / "desktop/src-tauri/tauri.conf.json"
        conf = json.loads(conf_path.read_text(encoding="utf-8"))
        nsis = conf["bundle"]["windows"]["nsis"]
        self.assertEqual(nsis.get("installMode"), "currentUser")
        self.assertEqual(nsis.get("installerHooks"), "windows/hooks.nsh")

        hooks_entry = REPO / "desktop/src-tauri" / nsis["installerHooks"]
        hooks_impl = REPO / "desktop/src-tauri/windows/data-location.nsh"
        for script in (hooks_entry, hooks_impl):
            self.assertTrue(script.is_file(), f"missing NSIS script: {script}")
            raw = script.read_bytes()
            self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), f"unexpected UTF-8 BOM in {script}")
            raw.decode("utf-8")

        impl_text = hooks_impl.read_text(encoding="utf-8")
        self.assertIn("数据储存位置", impl_text)
        self.assertIn("!macro NSIS_HOOK_PREINSTALL", impl_text)
        self.assertIn("!macro NSIS_HOOK_POSTINSTALL", impl_text)
        self.assertIn("Page custom AmadeusDataDirPageCreate AmadeusDataDirPageLeave", impl_text)

    def test_install_smoke_default_and_custom_datadir_cli_contracts(self):
        installer = Path("Amadeus_0.1.0_x64-setup.exe")
        self.assertEqual(smoke.installer_command(installer), [str(installer), "/S"])

        with tempfile.TemporaryDirectory() as temp:
            custom = (Path(temp) / "自定义 数据" / "AmadeusData").resolve()
            self.assertEqual(
                smoke.installer_command(installer, custom),
                [str(installer), "/S", f"/DATADIR={custom}"],
            )
            local = Path(temp) / "localappdata"
            local.mkdir()
            self.assertEqual(smoke.check_clean(local, custom), custom)
            identity_root = local / smoke.IDENTIFIER
            self.assertEqual(
                smoke.installed_backend_logs(custom, identity_root),
                identity_root / "logs",
            )
            custom.mkdir(parents=True)
            with self.assertRaisesRegex(RuntimeError, "Refusing existing custom data directory"):
                smoke.check_clean(local, custom)

        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                smoke.arguments([
                    "--installer", "setup.exe",
                    "--evidence-dir", "evidence",
                    "--data-dir", "relative/data",
                ])

    def test_nsis_json_escape_format_matches_utf8_no_bom_contract(self):
        for sample in (
            r"D:\AmadeusData",
            r"D:\自定义 数据\Amadeus Storage\data",
            r"E:\Users\test_dir\Amadeus (Custom)",
        ):
            escaped = _nsis_escape_json_string(sample)
            raw_bytes = f'{{"path":"{escaped}"}}'.encode("utf-8")
            self.assertFalse(raw_bytes.startswith(b"\xef\xbb\xbf"))
            parsed = json.loads(raw_bytes.decode("utf-8"))
            self.assertEqual(parsed, {"path": sample})
            self.assertTrue(PureWindowsPath(parsed["path"]).is_absolute())

        marker_bytes = b'{"format":1}'
        self.assertFalse(marker_bytes.startswith(b"\xef\xbb\xbf"))
        self.assertEqual(json.loads(marker_bytes.decode("utf-8")), {"format": 1})

        with self.assertRaises(json.JSONDecodeError):
            json.loads(r'{"path":"D:\unescaped\ndata"}')


@unittest.skipUnless(os.name == "nt" and _find_makensis() is not None, "Requires Windows and makensis.exe")
class NsisCompiledHookExecutionTests(unittest.TestCase):
    def _compile_harness(self, work: Path, identity_root: Path, install_dir: Path) -> Path:
        makensis = _find_makensis()
        assert makensis is not None
        hooks_path = REPO / "desktop/src-tauri/windows/hooks.nsh"
        out_exe = work / "nsis-hook-harness.exe"
        nsi_path = work / "harness.nsi"

        # Mirrors Tauri v2 installer.nsi include order (hooks included right after StrFunc,
        # before BUNDLEID/PRODUCTNAME defines and MUI_PAGE_DIRECTORY).
        script = f"""Unicode true
SetCompress off
!include MUI2.nsh
!include FileFunc.nsh
!include WordFunc.nsh
!include "StrFunc.nsh"
${{StrCase}}
${{StrLoc}}

!define AMADEUS_IDENTITY_ROOT "{identity_root}"
!include "{hooks_path}"

!define PRODUCTNAME "Amadeus Test"
!define BUNDLEID "com.amadeus.nsis.test"

Var PassiveMode
Var UpdateMode

Name "${{PRODUCTNAME}}"
OutFile "{out_exe}"
RequestExecutionLevel user
InstallDir "{install_dir}"

Function SkipIfPassive
  ${{IfThen}} $PassiveMode = 1 ${{|}} Abort ${{|}}
FunctionEnd

!define MUI_PAGE_CUSTOMFUNCTION_PRE SkipIfPassive
!insertmacro MUI_PAGE_WELCOME
!define MUI_PAGE_CUSTOMFUNCTION_PRE SkipIfPassive
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_LANGUAGE "SimpChinese"

Section Install
  SetOutPath $INSTDIR
  !ifmacrodef NSIS_HOOK_PREINSTALL
    !insertmacro NSIS_HOOK_PREINSTALL
  !endif
  CreateDirectory "$INSTDIR"
  !ifmacrodef NSIS_HOOK_POSTINSTALL
    !insertmacro NSIS_HOOK_POSTINSTALL
  !endif
SectionEnd
"""
        nsi_path.write_text(script, encoding="utf-8")
        proc = subprocess.run(
            [str(makensis), str(nsi_path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        self.assertEqual(proc.returncode, 0, f"makensis failed:\n{proc.stdout}\n{proc.stderr}")
        self.assertTrue(out_exe.is_file())
        return out_exe

    def test_compiled_nsis_hooks_silent_default_custom_upgrade_reset_and_rejections(self):
        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp)
            identity_root = (work / "localappdata" / "com.amadeus.nsis.test").resolve()
            install_dir = (work / "program" / "Amadeus Test").resolve()
            exe = self._compile_harness(work, identity_root, install_dir)

            # 1. Fresh silent /S install without /DATADIR= -> writes NO data-location.json
            res_default = subprocess.run([str(exe), "/S"], timeout=30)
            self.assertEqual(res_default.returncode, 0)
            self.assertFalse((identity_root / "data-location.json").exists())

            # 2. Silent /S with custom directory containing spaces & Simplified Chinese
            custom_dir = (work / "用户 数据" / "AmadeusStorage").resolve()
            res_custom = subprocess.run([str(exe), "/S", f"/DATADIR={custom_dir}"], timeout=30)
            self.assertEqual(res_custom.returncode, 0)
            self.assertTrue(custom_dir.is_dir())

            marker = custom_dir / "amadeus-data.json"
            location_file = identity_root / "data-location.json"
            self.assertTrue(marker.is_file())
            self.assertTrue(location_file.is_file())
            self.assertEqual(marker.read_bytes(), b'{"format":1}')
            self.assertFalse(location_file.read_bytes().startswith(b"\xef\xbb\xbf"))
            parsed_loc = json.loads(location_file.read_text(encoding="utf-8"))
            self.assertEqual(parsed_loc, {"path": str(custom_dir)})
            self.assertTrue(Path(parsed_loc["path"]).is_absolute())

            # 3. Upgrade install: existing data-location.json written by Python json.dump (ensure_ascii=True,
            #    with previous_path BEFORE path and \uXXXX escapes) is preserved untouched on /S upgrade!
            migrated_dir = (work / "迁移目录" / "NextData").resolve()
            migrated_dir.mkdir(parents=True)
            (migrated_dir / "amadeus-data.json").write_text(
                json.dumps({"format": 1, "moved_at": "2026-11-18T00:00:00Z"}),
                encoding="utf-8",
            )
            rich_location_bytes = json.dumps(
                {
                    "previous_path": str(identity_root / "data"),
                    "moved_at": "2026-11-18T00:00:00Z",
                    "path": str(migrated_dir),
                },
                ensure_ascii=True,
            ).encode("utf-8")
            location_file.write_bytes(rich_location_bytes)

            res_upgrade = subprocess.run([str(exe), "/S"], timeout=30)
            self.assertEqual(res_upgrade.returncode, 0)
            self.assertEqual(location_file.read_bytes(), rich_location_bytes)
            self.assertEqual(
                json.loads((migrated_dir / "amadeus-data.json").read_text(encoding="utf-8"))["moved_at"],
                "2026-11-18T00:00:00Z",
            )

            # 4. Explicit reset back to default directory deletes data-location.json
            default_data = identity_root / "data"
            res_reset = subprocess.run([str(exe), "/S", f"/DATADIR={default_data}"], timeout=30)
            self.assertEqual(res_reset.returncode, 0)
            self.assertFalse(location_file.exists())

            # 5. Upgrade when pointer references a MISSING non-default directory (no /DATADIR) ->
            #    must exit 0, keep pointer intact, and MUST NOT recreate empty dir or marker!
            missing_custom = (work / "missing-custom-drive" / "AmadeusData").resolve()
            missing_loc_bytes = json.dumps(
                {"path": str(missing_custom)}, ensure_ascii=True
            ).encode("utf-8")
            location_file.write_bytes(missing_loc_bytes)
            res_missing_upgrade = subprocess.run([str(exe), "/S"], timeout=30)
            self.assertEqual(res_missing_upgrade.returncode, 0)
            self.assertEqual(location_file.read_bytes(), missing_loc_bytes)
            self.assertFalse(missing_custom.exists())

            # 6. Upgrade when pointer was migrated back to default (path=default + previous_path) ->
            #    must exit 0 and preserve data-location.json untouched so previous_path cleanup stays working!
            back_to_default_bytes = json.dumps(
                {
                    "path": str(default_data),
                    "previous_path": str(migrated_dir),
                    "moved_at": "2026-11-18T01:00:00Z",
                },
                ensure_ascii=True,
            ).encode("utf-8")
            location_file.write_bytes(back_to_default_bytes)
            res_default_upgrade = subprocess.run([str(exe), "/S"], timeout=30)
            self.assertEqual(res_default_upgrade.returncode, 0)
            self.assertTrue(location_file.is_file())
            self.assertEqual(location_file.read_bytes(), back_to_default_bytes)

            # 7. Selecting a custom dir with invalid amadeus-data.json (format!=1 / string "1" / broken JSON) ->
            #    must abort non-zero and preserve existing data-location.json untouched!
            for bad_marker in (b'{"format":2}', b'{"format":"1"}', b"{not-json"):
                bad_marker_dir = (work / f"bad-marker-{len(bad_marker)}").resolve()
                bad_marker_dir.mkdir(parents=True, exist_ok=True)
                (bad_marker_dir / "amadeus-data.json").write_bytes(bad_marker)
                res_bad_marker = subprocess.run([str(exe), "/S", f"/DATADIR={bad_marker_dir}"], timeout=30)
                self.assertNotEqual(res_bad_marker.returncode, 0, f"expected failure for marker {bad_marker!r}")
                self.assertEqual(location_file.read_bytes(), back_to_default_bytes)
                self.assertEqual((bad_marker_dir / "amadeus-data.json").read_bytes(), bad_marker)

            # Reset location_file back to absent before negative /DATADIR= checks
            location_file.unlink()

            # 8. Unsafe / invalid /DATADIR= paths fail closed without writing data-location.json
            outside = work / "outside-target"
            outside.mkdir()
            junc = work / "junction-data"
            subprocess.run(["cmd", "/c", "mklink", "/J", str(junc), str(outside)], check=True, capture_output=True)
            try:
                for bad_arg in (
                    "/DATADIR=relative\\folder",
                    f"/DATADIR={install_dir / 'subdata'}",
                    f"/DATADIR={junc}",
                ):
                    bad_res = subprocess.run([str(exe), "/S", bad_arg], timeout=30)
                    self.assertNotEqual(bad_res.returncode, 0, f"expected non-zero exit for {bad_arg}")
                    self.assertFalse(location_file.exists(), f"unexpected data-location.json for {bad_arg}")
            finally:
                if junc.exists() or junc.is_symlink():
                    os.rmdir(junc)


    # ------------------------------------------------------------------
    # Strict whole-file JSON validation of an existing amadeus-data.json ([253] rework 2)
    # ------------------------------------------------------------------
    _OLD_POINTER_NAME = "data-location.json"

    def _marker_case(self, exe: Path, work: Path, identity_root: Path, tag: str, marker: bytes):
        """Run /S /DATADIR=<dir with marker>; return (returncode, dir, pointer_before, pointer_after)."""
        target = (work / f"marker-case-{tag}").resolve()
        target.mkdir(parents=True, exist_ok=True)
        (target / "amadeus-data.json").write_bytes(marker)
        location_file = identity_root / self._OLD_POINTER_NAME
        old_pointer = json.dumps({"path": str(work / "old-registered-dir")}).encode("utf-8")
        identity_root.mkdir(parents=True, exist_ok=True)
        location_file.write_bytes(old_pointer)
        proc = subprocess.run([str(exe), "/S", f"/DATADIR={target}"], timeout=30)
        after = location_file.read_bytes()
        marker_after = (target / "amadeus-data.json").read_bytes()
        self.assertEqual(marker_after, marker, f"marker modified for {tag}")
        return proc.returncode, target, old_pointer, after

    def test_compiled_hook_rejects_malformed_or_non_format_one_marker_and_keeps_pointer(self):
        bom = b"\xef\xbb\xbf"
        invalid = {
            "truncated": b'{"format":1',
            "truncated-after-comma": b'{"format":1,',
            "trailing-comma": b'{"format":1,}',
            "missing-comma": b'{"format":1 "a":2}',
            "bad-following-field": b'{"format":1,"moved_at":bad}',
            "trailing-garbage": b'{"format":1}x',
            "second-document": b'{"format":1}{"format":1}',
            "nested-array-trailing-comma": b'{"format":1,"meta":[1,]}',
            "nested-object-trailing-comma": b'{"format":1,"meta":{"a":1,}}',
            "nested-missing-colon": b'{"format":1,"meta":{"a" 1}}',
            "unterminated-nested": b'{"format":1,"meta":[1,2}',
            "mismatched-brackets": b'{"format":1,"meta":[1,2}]}',
            "duplicate-key-last-wins-2": b'{"format":1,"format":2}',
            "duplicate-key-last-wins-string": b'{"format":1,"format":"1"}',
            "key-case-FORMAT": b'{"FORMAT":1}',
            "key-case-Format": b'{"Format":1}',
            "literal-TRUE": b'{"format":1,"ok":TRUE}',
            "literal-True": b'{"format":1,"ok":True}',
            "literal-NULL": b'{"format":1,"ok":NULL}',
            "literal-truex": b'{"format":1,"ok":truex}',
            "literal-nul": b'{"format":1,"ok":nul}',
            "linguistically-equal-key": b'{"for-mat":1}',
            "soft-hyphen-key": '{"for\u00admat":1}'.encode("utf-8"),
            "format-string": b'{"format":"1"}',
            "format-float": b'{"format":1.0}',
            "format-exp": b'{"format":1e0}',
            "format-leading-zero": b'{"format":01}',
            "format-plus": b'{"format":+1}',
            "format-two": b'{"format":2}',
            "format-minus": b'{"format":-1}',
            "format-true": b'{"format":true}',
            "format-array": b'{"format":[1]}',
            "format-object": b'{"format":{"a":1}}',
            "format-missing": b'{"moved_at":"2026-11-18T00:00:00Z"}',
            "format-nested-only": b'{"meta":{"format":1}}',
            "empty-object": b"{}",
            "bom": bom + b'{"format":1}',
            "bom-only": bom,
            "empty-file": b"",
            "whitespace-only": b"  \n",
            "top-level-array": b'[{"format":1}]',
            "top-level-number": b"1",
            "single-quotes": b"{'format':1}",
            "unquoted-key": b"{format:1}",
            "bad-number-trailing-dot": b'{"format":1,"a":1.}',
            "bad-number-leading-dot": b'{"format":1,"a":.5}',
            "bad-number-lone-minus": b'{"format":1,"a":-}',
            "bad-number-empty-exponent": b'{"format":1,"a":1e}',
            "bad-number-leading-zero": b'{"format":1,"a":007}',
            "raw-control-char-in-string": b'{"format":1,"a":"x\x01y"}',
            "raw-newline-in-string": b'{"format":1,"a":"x\ny"}',
            "bad-escape": b'{"format":1,"a":"\\x"}',
            "bad-unicode-escape": b'{"format":1,"a":"\\u12G4"}',
            "short-unicode-escape": b'{"format":1,"a":"\\u12"}',
            "lone-high-surrogate": b'{"format":1,"a":"\\ud800"}',
            "lone-low-surrogate": b'{"format":1,"a":"\\udc00"}',
            "unterminated-string": b'{"format":1,"a":"abc}',
            "invalid-utf8": b'{"format":1,"a":"\xff"}',
            "form-feed-whitespace": b'{"format":1}\x0c',
            "nbsp-whitespace": '{"format":1}\u00a0'.encode("utf-8"),
            "embedded-nul": b'{"format":1}\x00x',
            "oversize-trailing-garbage": b'{"format":1}' + b" " * 9000 + b"x",
            "nesting-too-deep": b'{"format":1,"d":' + b"[" * 40 + b"]" * 40 + b"}",
        }
        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp)
            identity_root = (work / "localappdata" / "com.amadeus.nsis.test").resolve()
            install_dir = (work / "program" / "Amadeus Test").resolve()
            exe = self._compile_harness(work, identity_root, install_dir)
            for tag, marker in invalid.items():
                with self.subTest(case=tag):
                    code, _target, before, after = self._marker_case(exe, work, identity_root, tag, marker)
                    self.assertNotEqual(code, 0, f"installer accepted invalid marker {tag}: {marker[:60]!r}")
                    self.assertEqual(after, before, f"pointer changed for rejected marker {tag}")

    def test_compiled_hook_accepts_strict_valid_format_one_marker_in_any_key_order(self):
        rich = json.dumps(
            {
                "meta": {"list": [1, -0.5e+10, 2E3, True, False, None, "x\"\\/\n\t\u00e9\u4e2d\ud83d\ude00"], "e": {}},
                "moved_at": "2026-11-18T00:00:00Z",
                "format": 1,
                "tail": [],
            }
        )
        deep = b'{"format":1,"d":' + b"[" * 28 + b"]" * 28 + b"}"
        valid = {
            "minimal": b'{"format":1}',
            "trailing-whitespace": b'{"format":1}\r\n \t\n',
            "leading-and-inner-whitespace": b'\r\n  { "moved_at" : "2026-11-18T00:00:00Z" ,\n "format" : 1 }\n',
            "format-last": b'{"moved_at":"2026-11-18T00:00:00Z","format":1}',
            "duplicate-key-last-wins-1": b'{"format":2,"format":1}',
            "escaped-key": b'{"fo\\u0072mat":1}',
            "ensure-ascii-rich": rich.encode("utf-8"),
            "unicode-rich": json.dumps(json.loads(rich), ensure_ascii=False).encode("utf-8"),
            "nested-format-key-ignored": b'{"meta":{"format":2},"format":1}',
            "nested-array-of-objects": b'{"format":1,"a":[{"b":[{}]},[],[[]]]}',
            "deep": deep,
        }
        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp)
            identity_root = (work / "localappdata" / "com.amadeus.nsis.test").resolve()
            install_dir = (work / "program" / "Amadeus Test").resolve()
            exe = self._compile_harness(work, identity_root, install_dir)
            for tag, marker in valid.items():
                with self.subTest(case=tag):
                    parsed = json.loads(marker.decode("utf-8"))
                    self.assertIs(type(parsed["format"]), int)
                    self.assertEqual(parsed["format"], 1)
                    code, target, _before, after = self._marker_case(exe, work, identity_root, tag, marker)
                    self.assertEqual(code, 0, f"installer rejected valid marker {tag}")
                    self.assertEqual(json.loads(after.decode("utf-8")), {"path": str(target)})
                    self.assertEqual((target / "amadeus-data.json").read_bytes(), marker)


if __name__ == "__main__":
    unittest.main()
