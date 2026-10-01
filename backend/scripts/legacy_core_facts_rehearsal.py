"""Offline rehearsal CLI for the legacy core-fact migration (preview / apply).

Copy-only rehearsal on explicit paths:

  python scripts/legacy_core_facts_rehearsal.py preview \
      --source <snapshot_dir> --out <preview.json>

  python scripts/legacy_core_facts_rehearsal.py apply \
      --source <snapshot_dir> --target <rehearsal_target_dir> \
      --preview <preview.json>

The snapshot directory must contain BOTH worldline snapshots
(``worldlines/{sg,beta}/{history,memory}.sqlite3`` — an isolated copy of the
real store). The target directory is created fresh by this tool and bound to
the source via ``manifest.json``; re-running apply on the same target is
idempotent. The target is memory-only: it cannot run the full app or replace
the live data directory.

Entry validation happens BEFORE any application module is imported and
covers every actual I/O position: source databases, target databases and
manifest, and the preview output file. Reports are always NEW files (an
existing --out is refused), may not resolve into the source snapshot or a
formal data root (%APPDATA%/Amadeus or ~/.amadeus, junctions/symlinks
resolved), and AMADEUS_DB_PATH/AMADEUS_DATA_DIR overrides are refused.

Never auto-discovers APPDATA, never connects to live services, never calls
external models or embedding APIs.
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


_SOURCE_IO_PATHS = tuple(
    Path("worldlines") / namespace / filename
    for namespace in ("sg", "beta")
    for filename in ("history.sqlite3", "memory.sqlite3")
)
_TARGET_IO_PATHS = (
    Path("manifest.json"),
    Path("manifest.json.tmp"),
    Path("worldlines/sg/memory.sqlite3"),
    Path("worldlines/beta/memory.sqlite3"),
)


def _fail(code: str, message: str) -> None:
    print(f"error[{code}]: {message}", file=sys.stderr)
    raise SystemExit(2)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="legacy_core_facts_rehearsal",
        description=(
            "Offline, copy-only rehearsal for migrating legacy core facts "
            "into an isolated v11 target (preview/apply, explicit paths)."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    preview = sub.add_parser(
        "preview", help="read-only classification of a source snapshot"
    )
    preview.add_argument(
        "--source",
        required=True,
        help="isolated snapshot dir containing worldlines/{sg,beta}/{history,memory}.sqlite3",
    )
    preview.add_argument(
        "--out", required=True, help="output path for the preview JSON report"
    )

    apply_cmd = sub.add_parser(
        "apply", help="import qualified facts into a rehearsal target"
    )
    apply_cmd.add_argument("--source", required=True, help="same snapshot used for preview")
    apply_cmd.add_argument(
        "--target",
        required=True,
        help="rehearsal target dir (created fresh; re-runs are idempotent)",
    )
    apply_cmd.add_argument(
        "--preview", required=True, help="preview JSON produced by the preview command"
    )
    return parser


def _live_data_roots() -> list[Path]:
    """Formal data roots this tool must never read or write (shared rule set)."""
    return live_data_roots()


def _deep_resolve(raw: Path) -> Path:
    """Deep-resolved absolute path (shared rule set, junction/symlink aware)."""
    return deep_resolve(raw)


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _resolve_owned_io_paths(
    *, label: str, root: Path, relative_paths: tuple[Path, ...], live_roots: list[Path]
) -> list[Path]:
    """Resolve concrete I/O paths and require every one to stay under its root."""
    resolved_paths: list[Path] = []
    for relative_path in relative_paths:
        resolved = _deep_resolve(root / relative_path)
        if not _is_within(resolved, root):
            _fail(
                "path_escape",
                f"{label} I/O path escapes its root through a filesystem alias: "
                f"{resolved}",
            )
        for live_root in live_roots:
            if _is_within(resolved, live_root):
                _fail(
                    "live_data_path",
                    f"{label} I/O path {resolved} is inside the live Amadeus "
                    f"data root {live_root}",
                )
        resolved_paths.append(resolved)
    return resolved_paths


def _entry_validation(args: argparse.Namespace) -> None:
    """Fail-closed validation of every actual I/O position BEFORE app import.

    Covers the files this tool really reads/writes: source databases, target
    databases and manifest (via the source/target roots), the preview output
    file and the preview input file. Paths are deep-resolved so junctions and
    symlinks cannot alias an output into a protected location.
    """
    override = env_override_present()
    if override is not None:
        _fail(
            "env_override_present",
            f"refusing to run with {override} set; unset it so explicit paths "
            "cannot be silently redirected by app path resolution",
        )

    live_roots = _live_data_roots()
    source_resolved = _deep_resolve(Path(args.source))

    # source (read) and, for apply, target (write) must never touch the
    # formal data roots.
    positions: list[tuple[str, Path]] = [("source", source_resolved)]
    target_resolved: Path | None = None
    if args.command == "apply":
        target_resolved = _deep_resolve(Path(args.target))
        positions.append(("target", target_resolved))
    for label, resolved in positions:
        for root in live_roots:
            if _is_within(resolved, root):
                _fail(
                    "live_data_path",
                    f"{label} path {resolved} is inside the live Amadeus data root "
                    f"{root}; operate on isolated copies only",
                )

    if target_resolved is not None:
        if (
            source_resolved == target_resolved
            or source_resolved in target_resolved.parents
            or target_resolved in source_resolved.parents
        ):
            _fail(
                "overlapping_paths",
                f"source and target must be distinct, non-nested paths "
                f"(source={source_resolved}, target={target_resolved})",
            )

    source_io_paths = _resolve_owned_io_paths(
        label="source",
        root=source_resolved,
        relative_paths=_SOURCE_IO_PATHS,
        live_roots=live_roots,
    )
    target_io_paths: list[Path] = []
    if target_resolved is not None:
        target_io_paths = _resolve_owned_io_paths(
            label="target",
            root=target_resolved,
            relative_paths=_TARGET_IO_PATHS,
            live_roots=live_roots,
        )
        for source_path in source_io_paths:
            if not source_path.exists():
                continue
            for target_path in target_io_paths:
                if target_path.exists() and source_path.samefile(target_path):
                    _fail(
                        "overlapping_paths",
                        "source and target I/O files must not alias the same "
                        f"existing file ({source_path}, {target_path})",
                    )

    if args.command == "preview":
        # The report is a NEW file: never overwrite anything existing, and
        # never resolve into the source snapshot or a formal data root.
        out_resolved = _deep_resolve(Path(args.out))
        if out_resolved.exists():
            _fail(
                "output_exists",
                f"--out already exists; reports are new files and never "
                f"overwrite: {out_resolved}",
            )
        for root in live_roots:
            if _is_within(out_resolved, root):
                _fail(
                    "output_path_forbidden",
                    f"--out resolves inside the live Amadeus data root "
                    f"{root}: {out_resolved}",
                )
        if _is_within(out_resolved, source_resolved):
            _fail(
                "output_path_forbidden",
                f"--out resolves inside the source snapshot: {out_resolved}",
            )
    else:
        preview_resolved = _deep_resolve(Path(args.preview))
        if not preview_resolved.is_file():
            _fail(
                "preview_file_missing",
                f"preview file not found: {preview_resolved}",
            )
        forbidden_roots = [source_resolved, target_resolved, *live_roots]
        if any(
            root is not None and _is_within(preview_resolved, root)
            for root in forbidden_roots
        ):
            _fail(
                "preview_path_forbidden",
                "--preview must be outside the source snapshot, rehearsal "
                f"target and live data roots: {preview_resolved}",
            )
        for owned_path in [*source_io_paths, *target_io_paths]:
            if owned_path.exists() and preview_resolved.samefile(owned_path):
                _fail(
                    "preview_path_forbidden",
                    "--preview must not alias a source or target I/O file: "
                    f"{preview_resolved}",
                )


def _run_preview(args: argparse.Namespace) -> None:
    from app.services.memory_v11.migration import build_legacy_core_facts_preview

    report = build_legacy_core_facts_preview(args.source)
    out_path = _deep_resolve(Path(args.out))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with out_path.open("x", encoding="utf-8") as output:
            json.dump(report, output, ensure_ascii=False, indent=2)
    except FileExistsError:
        _fail(
            "output_exists",
            f"--out already exists; reports are new files and never "
            f"overwrite: {out_path}",
        )
    summary = report["summary"]
    print(f"preview written: {out_path}")
    print(f"source_id: {report['source']['source_id']}")
    for wl, meta in report["source"]["worldlines"].items():
        print(
            f"  {wl}: core_facts={meta['core_facts_total']} "
            f"messages={meta['messages_total']} "
            f"erasure_requests={meta['erasure_requests_total']} "
            f"tombstones={meta['tombstones_total']}"
        )
    print(
        f"planned={summary['planned']} skip={summary['skip']} defer={summary['defer']}"
    )
    print(
        "by_reason: "
        + json.dumps(summary["by_reason"], ensure_ascii=False, sort_keys=True)
    )
    if report["duplicate_hints"]:
        print(
            f"duplicate_hints: {len(report['duplicate_hints'])} "
            "(shown only, not merged)"
        )
    pin_audit = report.get("pin_audit") or {}
    if pin_audit.get("any_exceeds_limit"):
        print(
            "warning[pin_limit_exceeded]: at least one scope exceeds the pin "
            f"limit ({pin_audit.get('pin_limit_per_scope')}); NOT eligible for "
            "production cutover under the current pin policy"
        )


def _run_apply(args: argparse.Namespace) -> None:
    from app.services.memory_v11.migration import apply_legacy_core_facts

    preview_path = Path(args.preview).expanduser().resolve()
    if not preview_path.is_file():
        _fail("preview_file_missing", f"preview file not found: {preview_path}")
    try:
        preview_payload = json.loads(preview_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        _fail("invalid_preview", f"preview file is not valid JSON: {exc}")
    if not isinstance(preview_payload, dict):
        _fail("invalid_preview", "preview file must contain a JSON object")
    result = apply_legacy_core_facts(
        source_dir=args.source, target_dir=args.target, preview=preview_payload
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result.get("ok"):
        raise SystemExit(3)


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)
    _entry_validation(args)

    backend_root = Path(__file__).resolve().parents[1]
    if str(backend_root) not in sys.path:
        sys.path.insert(0, str(backend_root))

    from app.services.memory_v11.migration import MigrationRehearsalError

    try:
        if args.command == "preview":
            _run_preview(args)
        else:
            _run_apply(args)
    except MigrationRehearsalError as exc:
        _fail(exc.code, str(exc))


if __name__ == "__main__":
    main()
