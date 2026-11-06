"""Exercise retention when native or Python consumers appear."""
import hashlib
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vc_runtime import pe_details, prune_onnxruntime_dll, prune_unused_dlls


class UnusedDllTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.dist = self.root / "dist"
        self.source = self.root / "onnxruntime"
        self.source.mkdir()
        self.dll = self.dist / "_internal/onnxruntime/capi/onnxruntime.dll"
        self.dll.parent.mkdir(parents=True)
        self.dll.write_bytes(b"unused-runtime")
        (self.dist / "consumer.pyd").write_bytes(b"consumer")
        self.report = self.root / "audit.json"
        self.details = self.enterContext(patch("vc_runtime.pe_details", return_value={"imports": []}))

    def test_no_references_removes_dll_and_records_size(self):
        result = prune_onnxruntime_dll(self.dist, self.source, self.report)
        self.assertFalse(self.dll.exists())
        self.assertEqual(result["before"]["bytes"] - result["after"]["bytes"], len(b"unused-runtime"))
        self.assertEqual(result["removed"][0]["bundle_path"], "_internal/onnxruntime/capi/onnxruntime.dll")

    def test_importer_keeps_dll(self):
        self.details.side_effect = lambda path: {"imports": ["onnxruntime.dll"] if path.name == "consumer.pyd" else []}
        result = prune_onnxruntime_dll(self.dist, self.source, self.report)
        self.assertTrue(self.dll.exists())
        self.assertEqual(result["importers"][0]["bundle_path"], "consumer.pyd")

    def test_unreviewed_loader_or_explicit_reference_keeps_dll(self):
        for source in ('import ctypes; ctypes.CDLL(selected_path)', 'name = "onnxruntime.dll"'):
            with self.subTest(source=source):
                (self.source / "loader.py").write_text(source, encoding="utf-8")
                result = prune_onnxruntime_dll(self.dist, self.source, self.report)
                self.assertTrue(self.dll.exists())
                self.assertTrue(result["python_loader_calls"])

    def test_reviewed_loader_must_match_every_byte(self):
        loader = self.source / "loader.py"
        loader.write_text('import ctypes; ctypes.CDLL("other.dll")', encoding="utf-8")
        reviewed = {"loader.py": hashlib.sha256(loader.read_bytes()).hexdigest()}
        loader.write_text('import ctypes; ctypes.CDLL("new.dll")', encoding="utf-8")
        result = prune_unused_dlls(self.dist, self.source, self.report, {"onnxruntime.dll"}, reviewed)
        self.assertTrue(self.dll.exists())
        self.assertTrue(result["python_loader_calls"])
        reviewed["loader.py"] = hashlib.sha256(loader.read_bytes()).hexdigest()
        result = prune_unused_dlls(self.dist, self.source, self.report, {"onnxruntime.dll"}, reviewed)
        self.assertFalse(self.dll.exists())
        self.assertFalse(result["python_loader_calls"])

    def test_pe_inspection_includes_delay_imports(self):
        pe = SimpleNamespace(OPTIONAL_HEADER=SimpleNamespace(MajorLinkerVersion=14, MinorLinkerVersion=51),
                             DIRECTORY_ENTRY_DELAY_IMPORT=[SimpleNamespace(dll=b"ONNXRUNTIME.dll")])
        with patch("pefile.PE") as reader:
            reader.return_value.__enter__.return_value = pe
            pe.parse_data_directories = lambda **kwargs: None
            self.assertIn("onnxruntime.dll", pe_details(self.dll)["imports"])


if __name__ == "__main__":
    unittest.main()
