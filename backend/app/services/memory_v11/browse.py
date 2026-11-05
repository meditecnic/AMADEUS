"""Shared, read-only creation-time predicates for the archive browse queries."""

from datetime import datetime

from app.services.memory_v11.projection import _parse_row_time


async def apply_created_time_filters(
    db, clauses: list[str], params: list, *, column: str,
    created_from: datetime | None, created_to: datetime | None,
) -> None:
    if created_from is None and created_to is None:
        return
    # SQLite's date functions round to milliseconds. Normalize to UTC text
    # instead, preserving the stored microseconds and equivalent ISO offsets.
    await db.create_function(
        "archive_created_at_utc", 1,
        lambda raw: _parse_row_time(raw).isoformat(timespec="microseconds"),
        deterministic=True,
    )
    for bound, operator in ((created_from, ">="), (created_to, "<")):
        if bound is not None:
            clauses.append(f"archive_created_at_utc({column}) {operator} ?")
            params.append(_parse_row_time(bound).isoformat(timespec="microseconds"))
