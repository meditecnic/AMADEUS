"""Require frozen service and self-check to refuse unset AMADEUS_DATA_DIR."""
import argparse
from pathlib import Path
import subprocess

from bundle_support import sanitized_build_environment


def check(exe: Path, work: Path) -> None:
    for name, arguments in (("service", []), ("self-check", ["--self-check"])):
        root = work / name
        root.mkdir(parents=True, exist_ok=False)
        env = sanitized_build_environment()
        env.update({key: str(root / value) for key, value in {
            "HOME": "user", "USERPROFILE": "user", "APPDATA": "roaming", "LOCALAPPDATA": "local",
            "TEMP": "tmp", "TMP": "tmp",
        }.items()})
        # The packaged entry checks the startup token before the data directory; give it one so
        # the data-directory gate is the check that refuses to start.
        env["AMADEUS_LOCAL_API_TOKEN"] = "no-data-dir-check-only"
        before = set(root.rglob("*"))
        result = subprocess.run([str(exe.resolve()), *arguments], cwd=root, env=env,
                                input="", capture_output=True, text=True, encoding="utf-8", timeout=30)
        lines = result.stderr.strip().splitlines()
        if (result.returncode == 0 or len(lines) != 1 or "AMADEUS_DATA_DIR" not in lines[0]
                or set(root.rglob("*")) != before):
            raise RuntimeError(f"Missing data-dir gate failed: {name}: exit={result.returncode}; stderr={result.stderr!r}")
        print(f"NO_DATA_DIR_OK {name}: exit={result.returncode}; {lines[0]}; no files or directories created")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    args = parser.parse_args()
    check(args.exe, args.work_dir.resolve())
