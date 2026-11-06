"""Synthetic keys stay in memory; exercise the release gate on Linux and Windows."""
import base64
import contextlib
import hashlib
import io
import json
from pathlib import Path
import secrets
import sys
import tempfile
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from verify_update_signature import main, verify_update


def b64(data):
    return base64.b64encode(data).decode("ascii")


class UpdateSignatureTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.installer = root / "Amadeus Test_x64-setup.exe"
        self.signature = root / (self.installer.name + ".sig")
        self.config = root / "tauri.conf.json"
        self.latest = root / "latest.json"
        self.private = Ed25519PrivateKey.generate()
        self.key_id = secrets.token_bytes(8)
        self.installer.write_bytes(b"fake installer\x00\xff")
        self.write_public(self.private, self.key_id)
        self.sign()

    def write_public(self, private, key_id):
        public = b"Ed" + key_id + private.public_key().public_bytes_raw()
        self.config.write_text(json.dumps({"plugins": {"updater": {
            "pubkey": b64(b"untrusted comment: public key\n" + b64(public).encode() + b"\n"),
        }}}), encoding="utf-8")

    def sign(self, algorithm=b"ED", line_end="\n"):
        data = self.installer.read_bytes()
        message = hashlib.blake2b(data, digest_size=64).digest() if algorithm == b"ED" else data
        signed = self.private.sign(message)
        comment = "timestamp:123\tfile:" + self.installer.name
        global_sig = self.private.sign(signed + comment.encode("utf-8"))
        text = line_end.join(("untrusted comment: signature", b64(algorithm + self.key_id + signed),
                              "trusted comment: " + comment, b64(global_sig), ""))
        self.write_signature(b64(text.encode("utf-8")))

    def write_signature(self, text):
        self.signature.write_text(text + "\n", encoding="utf-8")
        self.latest.write_text(json.dumps({"platforms": {"windows-x86_64": {
            "signature": text, "url": "https://example.invalid/Amadeus%20Test_x64-setup.exe",
        }}}), encoding="utf-8")

    def verify(self):
        return verify_update(self.installer, self.signature, self.config, self.latest)

    def edit_manifest(self, field, value):
        latest = json.loads(self.latest.read_text(encoding="utf-8"))
        latest["platforms"]["windows-x86_64"][field] = value
        self.latest.write_text(json.dumps(latest), encoding="utf-8")

    def test_pass_and_summary(self):
        data = self.installer.read_bytes()
        self.assertEqual(self.verify(), {"key_id": self.key_id[::-1].hex().upper(),
                                        "filename": self.installer.name, "size": len(data),
                                        "sha256": hashlib.sha256(data).hexdigest()})

    def test_legacy_and_crlf_match_updater_formats(self):
        for algorithm in (b"ED", b"Ed"):
            with self.subTest(algorithm=algorithm):
                self.sign(algorithm, "\r\n")
                self.verify()

    def test_one_byte_installer_change(self):
        self.installer.write_bytes(b"Fake installer\x00\xff")
        with self.assertRaisesRegex(ValueError, "installer signature"):
            self.verify()

    def test_different_public_key_and_id(self):
        self.write_public(Ed25519PrivateKey.generate(), secrets.token_bytes(8))
        with self.assertRaisesRegex(ValueError, "key id"):
            self.verify()

    def test_key_id_is_checked_even_when_crypto_key_is_identical(self):
        changed = bytes([self.key_id[0] ^ 1]) + self.key_id[1:]
        self.write_public(self.private, changed)
        with self.assertRaisesRegex(ValueError, "key id"):
            self.verify()

    def test_wrong_crypto_key_with_same_id(self):
        self.write_public(Ed25519PrivateKey.generate(), self.key_id)
        with self.assertRaisesRegex(ValueError, "installer signature"):
            self.verify()

    def test_manifest_signature_mismatch(self):
        self.edit_manifest("signature", "different")
        with self.assertRaisesRegex(ValueError, "latest.json signature"):
            self.verify()

    def test_manifest_url_mismatch(self):
        self.edit_manifest("url", "https://example.invalid/other.exe")
        with self.assertRaisesRegex(ValueError, "latest.json url"):
            self.verify()

    def test_trusted_comment_tampering(self):
        text = base64.b64decode(self.signature.read_text(encoding="utf-8")).decode("utf-8")
        self.write_signature(b64(text.replace("timestamp:123", "timestamp:124").encode("utf-8")))
        with self.assertRaisesRegex(ValueError, "trusted comment global signature"):
            self.verify()

    def test_global_signature_tampering(self):
        lines = base64.b64decode(self.signature.read_text(encoding="utf-8")).decode("utf-8").splitlines()
        global_sig = base64.b64decode(lines[3])
        lines[3] = b64(bytes([global_sig[0] ^ 1]) + global_sig[1:])
        self.write_signature(b64("\n".join(lines).encode("utf-8")))
        with self.assertRaisesRegex(ValueError, "trusted comment global signature"):
            self.verify()

    def test_cli_success_one_json_line_and_failure_exit(self):
        args = ["verify", "--installer", str(self.installer), "--signature", str(self.signature),
                "--config", str(self.config), "--latest", str(self.latest)]
        output, errors = io.StringIO(), io.StringIO()
        with patch.object(sys, "argv", args), contextlib.redirect_stdout(output):
            self.assertEqual(main(), 0)
        self.assertEqual(len(output.getvalue().splitlines()), 1)
        self.assertEqual(json.loads(output.getvalue().removeprefix("UPDATE_SIGNATURE_OK ")), self.verify())
        self.installer.write_bytes(b"modified")
        with patch.object(sys, "argv", args), contextlib.redirect_stderr(errors):
            self.assertEqual(main(), 1)
        self.assertIn("installer signature", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
