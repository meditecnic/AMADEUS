"""Exercise the environment passed to actual build child processes."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bundle_support import sanitized_build_environment


class BuildEnvironmentTests(unittest.TestCase):
    def test_child_keeps_tools_and_proxy_but_receives_no_unapproved_configuration(self):
        needed = {"SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "COMSPEC", "PATH", "PATHEXT",
                  "USERPROFILE", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP"}
        parent = {key: value for key, value in os.environ.items() if key.upper() in needed}
        parent.update({"HTTPS_PROXY": "http://127.0.0.1:9",
                       "CARGO_HOME": "D:/synthetic-cargo", "RUSTUP_HOME": "D:/synthetic-rustup"})
        excluded = ["COMMANDCODE_API_KEY", "CONTEXT7_API_KEY", "GITHUB_PERSONAL_ACCESS_TOKEN",
                    "DEEPSEEK_API_KEY", "AMADEUS_ENV_TEST", "ODDLY_NAMED_CONFIG",
                    "CARGO_REGISTRIES_CRATES_IO_TOKEN", "NODE_OPTIONS"]
        parent.update({name: "synthetic-do-not-inherit" for name in excluded})
        with patch.dict(os.environ, parent, clear=True):
            environment = sanitized_build_environment()
        for name in ["PATH", "HTTPS_PROXY", "CARGO_HOME", "RUSTUP_HOME"]:
            self.assertTrue(environment.get(name) == parent[name], name + " must be retained")
        code = ("import json,os; print(json.dumps({name: name in os.environ for name in "
                + repr(excluded) + "}))")
        observed = json.loads(subprocess.check_output([sys.executable, "-I", "-c", code], env=environment))
        self.assertEqual(observed, dict.fromkeys(excluded, False))


if __name__ == "__main__":
    unittest.main()
