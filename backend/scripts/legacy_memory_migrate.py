#!/usr/bin/env python3
"""Explicit, offline migration of legacy facts. Close Amadeus before running."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import redirect_stdout
import json
import os
from pathlib import Path
import sys


# Direct script invocation from either the repository or backend directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _parser():
    parser = argparse.ArgumentParser(description="Migrate legacy facts offline. Close Amadeus before running (先关闭 Amadeus).")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("preview", "apply", "consolidate-preview", "consolidate-apply", "consolidate-cancel"):
        child = sub.add_parser(command)
        child.add_argument("--data-dir", type=Path, required=True, help="Explicit existing Amadeus data directory; no automatic discovery")
        child.add_argument("--output", type=Path, required=True, help="New JSON report path; existing files are refused")
        if command == "apply":
            child.add_argument("--preview", type=Path, required=True)
        if command.startswith("consolidate-"):
            child.add_argument("--worldline", choices=("steins_gate", "beta"), required=True)
        if command == "consolidate-preview":
            child.add_argument("--session-id", required=True)
            child.add_argument("--identity-mode", choices=("self", "okabe"), required=True)
        if command in ("consolidate-apply", "consolidate-cancel"):
            child.add_argument("--proposal", required=True)
    return parser


async def _run(args):
    from app.services.memory_v11 import migration as mig

    if "AMADEUS_DB_PATH" in os.environ:
        raise mig.MigrationRehearsalError("db_override_forbidden")

    if args.command == "preview":
        return mig.build_live_migration_preview()
    if args.command == "apply":
        preview = json.loads(args.preview.read_text(encoding="utf-8"))
        # Read-only validation before backup or writes; apply rechecks under locks.
        current = mig.build_live_migration_preview()
        if current["source_id"] != preview.get("source_id"):
            raise mig.MigrationRehearsalError("preview_stale")
        if current.get("kind") != preview.get("kind") or current["items"] != preview.get("items"):
            raise mig.MigrationRehearsalError("preview_mismatch")
        backups = mig.backup_live_migration_databases()
        for path in backups:
            print(f"backup: {path}", file=sys.stderr)
        result = await mig.apply_live_migration(preview=preview)
        result["backups"] = backups
        return result
    if args.command == "consolidate-preview":
        return await mig.preview_consolidation(session_id=args.session_id, worldline=args.worldline,
                                               identity_mode=args.identity_mode) or dict(status="no_duplicates")
    finish = mig.apply_consolidation if args.command == "consolidate-apply" else mig.cancel_consolidation
    return await finish(proposal_id=args.proposal, worldline=args.worldline)


def main(argv=None):
    parser = _parser()
    args = parser.parse_args(argv)
    if "AMADEUS_DB_PATH" in os.environ:
        parser.error("AMADEUS_DB_PATH must be unset")
    root = args.data_dir.expanduser().resolve()
    if not root.is_dir():
        parser.error("--data-dir must be an existing directory")
    # Set offline mode before importing application/model modules. E5 may only
    # read the existing local cache; no model or provider downloads are allowed.
    os.environ["AMADEUS_DATA_DIR"] = str(root)
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    try:
        # Reserve the report before mutations, so a preexisting output never
        # causes successful but unreported migration/consolidation.
        with args.output.open("x", encoding="utf-8") as report:
            with redirect_stdout(sys.stderr):
                result = asyncio.run(_run(args))
            json.dump(result, report, ensure_ascii=False, indent=2)
            report.write("\n")
        scopes = result.get("reconciliation", [])
        print(json.dumps(dict(scopes=len(scopes), total=sum(s["total"] for s in scopes),
                              migrated=sum(s["migrated"] for s in scopes),
                              merged=sum(s["merged_into_local"] for s in scopes))))
        return 0
    except Exception as exc:
        # Only stable error codes/types, never facts, source bodies or SQL args.
        print(f"migration failed: {getattr(exc, 'code', type(exc).__name__)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
