"""Check that the runner gate detects successful starts and directory writes."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from check_no_data_dir import check


class NoDataDirectoryTests(unittest.TestCase):
    def test_checks_both_modes_with_no_inherited_data_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict("os.environ", {"AMADEUS_DATA_DIR": "must-not-inherit"}):
                with patch("check_no_data_dir.subprocess.run", return_value=subprocess.CompletedProcess(
                        [], 1, "", "AMADEUS_DATA_DIR required\n")) as run:
                    check(Path("backend.exe"), Path(directory))
            self.assertEqual([call.args[0][1:] for call in run.call_args_list], [[], ["--self-check"]])
            for call in run.call_args_list:
                self.assertNotIn("AMADEUS_DATA_DIR", call.kwargs["env"])

    def test_rejects_wrong_exit_message_or_filesystem_writes(self):
        for code, stderr, write in ((0, "AMADEUS_DATA_DIR required", False), (1, "other error", False),
                                    (1, "AMADEUS_DATA_DIR required\nextra line", False),
                                    (1, "AMADEUS_DATA_DIR required", True)):
            with self.subTest(code=code, stderr=stderr, write=write), tempfile.TemporaryDirectory() as directory:
                def fake(command, **kwargs):
                    if write:
                        (kwargs["cwd"] / "unexpected").mkdir()
                    return subprocess.CompletedProcess(command, code, "", stderr)
                with patch("check_no_data_dir.subprocess.run", side_effect=fake):
                    with self.assertRaisesRegex(RuntimeError, "gate failed"):
                        check(Path("backend.exe"), Path(directory))


if __name__ == "__main__":
    unittest.main()
