"""Release input gates and the files delivered to users."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from release_artifacts import PUBLIC_KEY_PLACEHOLDER, check_signing, check_version, collect, latest_manifest


class ReleaseArtifactsTests(unittest.TestCase):
    def test_tag_requires_public_and_private_keys_but_pr_needs_neither(self):
        config = {"plugins": {"updater": {"pubkey": PUBLIC_KEY_PLACEHOLDER}}}
        check_signing(config, "", False)
        with self.assertRaisesRegex(ValueError, "placeholder"):
            check_signing(config, "v1.2.3", True)
        config["plugins"]["updater"]["pubkey"] = "synthetic-public-key"
        with self.assertRaisesRegex(ValueError, "TAURI_SIGNING_PRIVATE_KEY"):
            check_signing(config, "v1.2.3", False)
        check_signing(config, "v1.2.3", True)

    def test_latest_has_encoded_download_url_and_signature_contents(self):
        result = latest_manifest("1.2.3", Path("Amadeus Test.exe"), " signature\n", "更新说明", "owner/repo")
        self.assertEqual(result, {"version": "1.2.3", "notes": "更新说明", "platforms": {
            "windows-x86_64": {"signature": "signature",
                               "url": "https://github.com/owner/repo/releases/download/v1.2.3/Amadeus%20Test.exe"},
        }})
        with self.assertRaisesRegex(ValueError, "empty"):
            latest_manifest("1.2.3", Path("setup.exe"), "\n", "notes", "owner/repo")

    def test_tag_must_match_application_version(self):
        self.assertEqual(check_version({"version": "1.2.3"}, "v1.2.3"), "1.2.3")
        self.assertEqual(check_version({"version": "1.2.3"}, ""), "1.2.3")
        with self.assertRaisesRegex(ValueError, "does not match"):
            check_version({"version": "1.2.3"}, "v1.2.4")

    def test_collects_installer_sources_and_matching_checksums(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            installer = work / "Amadeus_1.2.3_x64-setup.exe"
            installer.write_bytes(b"installer")
            (work / "materials").mkdir()
            (work / "materials/amadeus-1.2.3-speech-sources.zip").write_bytes(b"sources")
            (work / "installer-result.json").write_text(json.dumps({
                "config": {"version": "1.2.3"}, "installer": str(installer),
            }), encoding="utf-8")
            output = work / "release"
            collect(work, output, "notes", "meditecnic/AMADEUS", "")
            lines = (output / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 2)
            for line in lines:
                expected, filename = line.split("  ")
                self.assertEqual(hashlib.sha256((output / filename).read_bytes()).hexdigest(), expected)
            self.assertFalse((output / "latest.json").exists())
            with self.assertRaisesRegex(ValueError, "missing the signed"):
                collect(work, work / "missing-signature", "notes", "meditecnic/AMADEUS", "v1.2.3")
            signature = Path(str(installer) + ".sig")
            signature.write_text("signed-artifact\n", encoding="utf-8")
            result_path = work / "installer-result.json"
            result = json.loads(result_path.read_text(encoding="utf-8"))
            result["updater_signature"] = str(signature)
            result_path.write_text(json.dumps(result), encoding="utf-8")
            signed = work / "signed"
            collect(work, signed, "更新说明", "meditecnic/AMADEUS", "v1.2.3")
            latest = json.loads((signed / "latest.json").read_text(encoding="utf-8"))
            self.assertEqual(latest["platforms"]["windows-x86_64"]["signature"], "signed-artifact")
            self.assertEqual(latest["notes"], "更新说明")
            lines = (signed / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 4)
            for line in lines:
                expected, filename = line.split("  ")
                self.assertEqual(hashlib.sha256((signed / filename).read_bytes()).hexdigest(), expected)


if __name__ == "__main__":
    unittest.main()
