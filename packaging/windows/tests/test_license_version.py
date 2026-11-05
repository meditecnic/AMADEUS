"""Version bumps must reach both machine-readable and human source notices."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from license_bundle import Bundle, native_notices
from source_materials import application_version


class LicenseVersionTests(unittest.TestCase):
    def test_default_version_comes_from_application_config(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "desktop/src-tauri/tauri.conf.json"
            config.parent.mkdir(parents=True)
            config.write_text('{"version": "9.8.7"}', encoding="utf-8")
            with patch("source_materials.HERE", root / "packaging/windows"):
                self.assertEqual(application_version(), "9.8.7")

    def test_native_source_notices_use_selected_installer_version(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backend = root / "backend"
            backend.mkdir()
            (root / "vc-runtime-audit.json").write_text(json.dumps({
                "redist": {"path": "synthetic-redist", "file_version": "14.51"}, "runtimes": [],
            }), encoding="utf-8")
            license_file = root / "COPYING"
            license_file.write_text("synthetic license", encoding="utf-8")
            pins = [{"name": "eSpeak NG", "file": "eSpeak-NG-COPYING", "version": "1.0", "url": "synthetic",
                     "ref": "synthetic", "local_path": str(license_file)},
                    {"file": "gcc-libquadmath-COPYING.LIB", "url": "synthetic", "ref": "synthetic",
                     "local_path": str(license_file)}]
            for filename, stem in (("espeak-ng-for-piper.cmake", "espeak_ng"), ("piper-phonemize.cmake", "piper_phonemize")):
                path = root / filename
                path.write_text(f'set({stem}_URL "https://example.invalid/source.zip")\n'
                                f'set({stem}_HASH "SHA256={"a" * 64}")', encoding="utf-8")
                pins.append({"file": filename, "local_path": str(path), "url": "synthetic", "sha256": "a" * 64})
            output = root / "licenses"
            native_notices(Bundle(output, backend, "Amadeus.exe"), {"work": str(root)}, pins, {}, "9.8.7")
            note = json.loads((output / "copyleft/SPEECH-SOURCE.json").read_text(encoding="utf-8"))
            self.assertEqual(note["source_package"], "amadeus-9.8.7-speech-sources.zip")
            self.assertIn("--version 9.8.7", note["download_command"])
            self.assertIn(note["source_package"], (output / "copyleft/SPEECH-SOURCE.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
