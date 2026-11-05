"""Release input gates and the files delivered to users."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from release_artifacts import check_version, collect


class ReleaseArtifactsTests(unittest.TestCase):
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
            collect(work, output, "notes", "meditecnic/AMADEUS", "v1.2.3")
            lines = (output / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 2)
            for line in lines:
                expected, filename = line.split("  ")
                self.assertEqual(hashlib.sha256((output / filename).read_bytes()).hexdigest(), expected)


if __name__ == "__main__":
    unittest.main()
