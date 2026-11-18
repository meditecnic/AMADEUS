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

            # 5. Unsafe / invalid /DATADIR= paths fail closed without writing data-location.json
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


if __name__ == "__main__":
    unittest.main()
