"""Select the configured Tauri installer without disturbing older builds."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from build_installer import select_installer


class InstallerSelectionTests(unittest.TestCase):
    def test_selects_current_product_and_version_among_old_installers(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            old = directory / 'Amadeus_0.1.0_x64-setup.exe'
            old.touch()
            current = directory / 'Amadeus T4_0.1.152_x64-setup.exe'
            current.touch()
            self.assertEqual(select_installer(directory, 'Amadeus T4', '0.1.152'), current)
            self.assertTrue(old.is_file())

    def test_old_product_or_version_cannot_replace_missing_current_installer(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for name in ('Amadeus_0.1.152_x64-setup.exe', 'Amadeus T4_0.1.0_x64-setup.exe'):
                (directory / name).touch()
            with self.assertRaisesRegex(RuntimeError, 'Missing NSIS installer'):
                select_installer(directory, 'Amadeus T4', '0.1.152')
            self.assertEqual(len(list(directory.iterdir())), 2)
