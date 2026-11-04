"""Exercise download retries with synthetic responses and no network or waits."""
from contextlib import redirect_stdout
import hashlib
import http.client
import io
from pathlib import Path
import ssl
import sys
import tempfile
import unittest
from unittest.mock import Mock, call, patch
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from source_materials import download


class DownloadTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.target = self.root / "source.zip"
        self.url = "https://example.invalid/source.zip"
        self.body = b"synthetic source archive"
        self.expected = hashlib.sha256(self.body).hexdigest()
        self.stdout = io.StringIO()
        self.enterContext(redirect_stdout(self.stdout))
        self.urlopen = self.enterContext(patch("source_materials.urllib.request.urlopen"))
        self.sleep = self.enterContext(patch("source_materials.time.sleep"))

    def response(self, body=None):
        response = io.BytesIO(self.body if body is None else body)
        response.headers = {}
        self.addCleanup(response.close)
        return response

    def test_two_disconnects_then_success(self):
        first = self.response()
        first.read = Mock(side_effect=[b"partial", http.client.IncompleteRead(b"", 10)])
        second = self.response()
        second.read = Mock(side_effect=[b"partial", ConnectionResetError("disconnected")])
        responses = iter((first, second, self.response()))

        def open_response(*args, **kwargs):
            self.assertEqual(list(self.root.glob("*.part")), [])
            return next(responses)

        self.urlopen.side_effect = open_response
        result = download(self.url, self.target, self.expected)
        self.assertEqual(self.target.read_bytes(), self.body)
        self.assertEqual(result, {"url": self.url, "file": "source.zip",
                                  "sha256": self.expected, "bytes": len(self.body)})
        self.assertEqual(self.urlopen.call_count, 3)
        self.assertEqual(self.sleep.call_args_list, [call(2), call(4)])
        self.assertEqual(self.stdout.getvalue().splitlines(), [
            f"DOWNLOAD {self.url}",
            f"DOWNLOAD_RETRY {self.url} attempt=1 error=IncompleteRead",
            f"DOWNLOAD_RETRY {self.url} attempt=2 error=ConnectionResetError",
        ])
        self.assertEqual(list(self.root.glob("*.part")), [])

    def test_four_failures_preserve_original_exception(self):
        errors = (urllib.error.URLError("disconnected"), ssl.SSLError("disconnected"),
                  ConnectionError("disconnected"), TimeoutError("timed out"),
                  http.client.IncompleteRead(b"partial", 10))
        for error in errors:
            with self.subTest(error=type(error).__name__):
                self.urlopen.reset_mock()
                self.sleep.reset_mock()
                self.stdout.seek(0)
                self.stdout.truncate(0)
                self.urlopen.side_effect = error
                with self.assertRaises(type(error)) as caught:
                    download(self.url, self.target, self.expected)
                self.assertIs(caught.exception, error)
                self.assertEqual(self.urlopen.call_count, 4)
                self.assertEqual(self.sleep.call_args_list, [call(2), call(4), call(8)])
                self.assertEqual(self.stdout.getvalue().count("DOWNLOAD_RETRY "), 3)
                self.assertFalse(self.target.exists())
                self.assertEqual(list(self.root.glob("*.part")), [])

    def test_hash_mismatch_is_not_retried(self):
        self.urlopen.side_effect = lambda *args, **kwargs: self.response(b"wrong content")
        with self.assertRaisesRegex(RuntimeError, "Source hash mismatch"):
            download(self.url, self.target, self.expected)
        self.urlopen.assert_called_once()
        self.sleep.assert_not_called()
        self.assertFalse(self.target.exists())
        self.assertEqual(list(self.root.glob("*.part")), [])

    def test_404_is_not_retried(self):
        error = urllib.error.HTTPError(self.url, 404, "Not Found", {}, None)
        self.urlopen.side_effect = error
        with self.assertRaises(urllib.error.HTTPError) as caught:
            download(self.url, self.target, self.expected)
        self.assertIs(caught.exception, error)
        self.urlopen.assert_called_once()
        self.sleep.assert_not_called()
        self.assertFalse(self.target.exists())

    def test_size_limit_is_not_retried(self):
        for declared in (False, True):
            with self.subTest(content_length=declared):
                self.urlopen.reset_mock()
                response = self.response()
                if declared:
                    response.headers["Content-Length"] = str(len(self.body))
                self.urlopen.return_value = response
                with self.assertRaisesRegex(RuntimeError, "Source size limit exceeded"):
                    download(self.url, self.target, self.expected, max_bytes=1)
                self.urlopen.assert_called_once()
                self.sleep.assert_not_called()
                self.assertFalse(self.target.exists())
                self.assertEqual(list(self.root.glob("*.part")), [])


if __name__ == "__main__":
    unittest.main()
