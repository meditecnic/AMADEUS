"""Offline stage-A candidate builder CLI (task memory-candidate-glm-20260909).

Builds the COMPLETE memory candidate directory from an explicit five-DB
read-only snapshot plus an already-reviewed rehearsal preview:

  python scripts/build_memory_candidate.py \
      --snapshot <snapshot_dir> --preview <preview.json> --out <candidate_dir>

The snapshot must contain control.sqlite3 and
worldlines/{sg,beta}/{history,memory}.sqlite3 (an isolated copy of the real
store). The candidate directory is created fresh (an existing --out is
refused), built inside a private staging tree, and only published after
every validation passes — a failed build leaves a clearly-incomplete
staging tree and no candidate.

Entry validation runs BEFORE any module with import side effects and covers
every actual I/O position (the five source DBs, output/staging/manifest,
temp donor and the preview file) using the shared offline-CLI guard rules:
AMADEUS_DB_PATH/AMADEUS_DATA_DIR overrides refused; deep-resolved paths so
junctions/symlinks cannot alias positions into the source or a live data
root (%APPDATA%/Amadeus or ~/.amadeus). The preview JSON is rejected when
it sits inside a live data root, the source snapshot, or the candidate
output path. Preview must have exactly one filesystem link: copy a linked
preview to a standalone file first. This avoids enumerating protected roots
to determine where other hardlinks might exist.

Never imports app.main/provider_runtime/credentials, never calls
lifespan/init_db, never touches the Windows Credential Manager, never calls
a model, never auto-discovers APPDATA.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _path_guards import (  # noqa: E402  (shared guard rules for offline CLIs)
    deep_resolve,
    env_override_present,
    live_data_roots,
)


def _fail(code: str, message: str) -> None:
    print(f"error[{code}]: {message}", file=sys.stderr)
    raise SystemExit(2)


_SNAPSHOT_FILES = (
    Path("control.sqlite3"),
    Path("worldlines") / "sg" / "history.sqlite3",
    Path("worldlines") / "sg" / "memory.sqlite3",
    Path("worldlines") / "beta" / "history.sqlite3",
    Path("worldlines") / "beta" / "memory.sqlite3",
)


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _entry_validation(args: argparse.Namespace) -> None:
    """Fail-closed validation of every actual I/O position BEFORE app import."""
    override = env_override_present()
    if override is not None:
        _fail(
            "env_override_present",
            f"refusing to run with {override} set; unset it so explicit paths "
            "cannot be silently redirected by app path resolution",
        )

    live_roots = live_data_roots()
    snapshot_resolved = deep_resolve(Path(args.snapshot))
    preview_resolved = deep_resolve(Path(args.preview))
    output_resolved = deep_resolve(Path(args.out))

    if not snapshot_resolved.is_dir():
        _fail("source_not_found", f"snapshot directory missing: {snapshot_resolved}")
    if not preview_resolved.is_file():
        _fail("preview_file_missing", f"preview file not found: {preview_resolved}")
    try:
        preview_links = preview_resolved.stat().st_nlink
    except OSError as exc:
        _fail("preview_path_unverifiable", f"cannot verify preview file identity: {exc}")
    if preview_links != 1:
        _fail(
            "preview_path_forbidden",
            "--preview must be a standalone file with one filesystem link; "
            "copy it to an isolated file before building",
        )
    if any(_is_within(snapshot_resolved, live_root) for live_root in live_roots):
        _fail(
            "live_data_path",
            f"snapshot path {snapshot_resolved} is inside the live Amadeus data "
            "root; operate on isolated copies only",
        )

    # The five source DBs must stay under the snapshot root (no alias escape).
    for relative in _SNAPSHOT_FILES:
        resolved = deep_resolve(snapshot_resolved / relative)
        if not _is_within(resolved, snapshot_resolved):
            _fail(
                "path_escape",
                f"source I/O path escapes its root through a filesystem alias: {resolved}",
            )

    # Preview isolation runs BEFORE output_exists so a review file planted
    # inside a would-be output directory is reported as a path violation.
    for live_root in live_roots:
        if _is_within(preview_resolved, live_root):
            _fail(
                "preview_path_forbidden",
                f"--preview resolves inside the live Amadeus data root "
                f"{live_root}: {preview_resolved}",
            )
    if _is_within(preview_resolved, snapshot_resolved):
        _fail(
            "preview_path_forbidden",
            f"--preview must be outside the source snapshot: {preview_resolved}",
        )
    if _is_within(preview_resolved, output_resolved):
        _fail(
            "preview_path_forbidden",
            f"--preview must be outside the candidate output path: {preview_resolved}",
        )
    for relative in _SNAPSHOT_FILES:
        owned = deep_resolve(snapshot_resolved / relative)
        if owned.exists() and preview_resolved.samefile(owned):
            _fail(
                "preview_path_forbidden",
                f"--preview must not alias a source database file: {preview_resolved}",
            )

    if output_resolved.exists():
        _fail(
            "output_exists",
            f"--out already exists; candidates are new directories and are "
            f"never overwritten: {output_resolved}",
        )
    if (
        snapshot_resolved == output_resolved
        or snapshot_resolved in output_resolved.parents
        or output_resolved in snapshot_resolved.parents
    ):
        _fail(
            "overlapping_paths",
            f"snapshot and output must be distinct, non-nested paths "
            f"(snapshot={snapshot_resolved}, output={output_resolved})",
        )
    for live_root in live_roots:
        if _is_within(output_resolved, live_root):
            _fail(
                "output_path_forbidden",
                f"--out resolves inside the live Amadeus data root "
                f"{live_root}: {output_resolved}",
            )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="build_memory_candidate",
        description=(
            "Stage-A offline builder: complete memory candidate directory from a "
            "five-DB read-only snapshot plus a reviewed rehearsal preview."
        ),
    )
    parser.add_argument(
        "--snapshot",
        required=True,
        help="isolated snapshot dir with control.sqlite3 and "
        "worldlines/{sg,beta}/{history,memory}.sqlite3",
    )
    parser.add_argument(
        "--preview", required=True,
        help="standalone preview JSON from legacy_core_facts_rehearsal.py (hardlinks refused)"
    )
    parser.add_argument(
        "--out", required=True, help="candidate output dir (must not exist; never overwritten)"
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)
    _entry_validation(args)

    backend_root = Path(__file__).resolve().parents[1]
    if str(backend_root) not in sys.path:
        sys.path.insert(0, str(backend_root))

    from app.services.memory_v11.candidate import CandidateBuildError, build_memory_candidate

    try:
        result = build_memory_candidate(
            snapshot_dir=args.snapshot, preview_path=args.preview, output_dir=args.out
        )
    except CandidateBuildError as exc:
        _fail(exc.code, str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(
        "candidate published; a failed build would instead leave an "
        "INCOMPLETE staging tree and no candidate"
    )


if __name__ == "__main__":
    main()
