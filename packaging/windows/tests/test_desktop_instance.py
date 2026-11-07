"""Run the actual Rust instance-preflight module without Tauri or a native WebView."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class InstancePreflightTests(unittest.TestCase):
    def test_rust_instance_preflight(self):
        compiler = os.environ.get("RUSTC") or shutil.which("rustc")
        if not compiler:
            self.skipTest("rustc is required for the desktop instance-preflight tests")
        source = Path(__file__).resolve().parents[3] / "desktop/src-tauri/src/instance.rs"
        with tempfile.TemporaryDirectory() as temporary:
            binary = Path(temporary) / ("instance-tests.exe" if os.name == "nt" else "instance-tests")
            compiled = subprocess.run([compiler, "--edition=2021", "--test", str(source), "-o", str(binary)],
                                      capture_output=True, encoding="utf-8", timeout=60)
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
            tested = subprocess.run([str(binary)], capture_output=True, encoding="utf-8", timeout=30)
            self.assertEqual(tested.returncode, 0, tested.stdout + tested.stderr)


if __name__ == "__main__":
    unittest.main()
