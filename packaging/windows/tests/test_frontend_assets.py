"""Check embedded Tauri asset keys without building a Windows executable."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bundle_support import verify_frontend_assets


class FrontendAssetTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.frontend = root / "frontend-dist"
        self.exe = root / "desktop.exe"
        self.keys = (
            "/index.html", "/assets/main-123.js", "/assets/chunks/lazy-456.js",
            "/assets/main-123.css", "/fonts/字体.woff2",
        )
        for key in self.keys:
            path = self.frontend / key.lstrip("/")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"synthetic frontend content")

    def write_exe(self, keys):
        self.exe.write_bytes(b"MZ\0" + b"\0".join(key.encode("utf-8") for key in keys) + b"\0")

    def test_all_asset_keys_are_embedded(self):
        self.write_exe(self.keys)
        with patch("builtins.print") as output:
            verify_frontend_assets(self.exe, self.frontend)
        output.assert_called_once_with('FRONTEND_EMBEDDED {"files": 5}', flush=True)

    def test_missing_asset_key_stops_the_build(self):
        for missing in self.keys:
            with self.subTest(missing=missing):
                self.write_exe(key for key in self.keys if key != missing)
                with self.assertRaises(RuntimeError) as caught:
                    verify_frontend_assets(self.exe, self.frontend)
                self.assertIn(missing, str(caught.exception))

    def test_absolute_frontend_path_without_keys_stops_the_build(self):
        self.exe.write_bytes(b"MZ\0D:\\build\\runs\\synthetic\\frontend-dist\0")
        with self.assertRaisesRegex(RuntimeError, "Frontend asset not embedded"):
            verify_frontend_assets(self.exe, self.frontend)


if __name__ == "__main__":
    unittest.main()
