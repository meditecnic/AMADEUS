"""Local usage totals and catalogue-price estimates, without currency conversion."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import sqlite3

from app import db
from app.services.model_catalog import model_catalog


def _price_at(price: dict | None, time_utc: str) -> dict | None:
    if price is None or not price.get("schedule"):
        return price
    try:
        instant = datetime.fromisoformat(time_utc)
    except (TypeError, ValueError):
        return None
    if instant.tzinfo is None:
        return None
    instant = instant.astimezone(timezone.utc)
    minute = instant.hour * 60 + instant.minute
    schedule = price["schedule"]
    peak = instant.weekday() in schedule["weekdays"] and any(
        start <= minute < end for start, end in schedule["utc_windows"]
    )
    return {**price, **schedule["peak" if peak else "off_peak"]}


def usage_report(period: str, conversation_id: str | None, tz: int, *, now=None) -> dict:
    clauses, params = [], []
    if period == "conversation":
        clauses.append("conversation_id=?")
        params.append(conversation_id)
    elif period == "month":
        local = (now or datetime.now(timezone.utc)).astimezone(timezone(timedelta(minutes=tz)))
        start = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        clauses.extend(["time_utc>=?", "time_utc<?"])
        params.extend([start.astimezone(timezone.utc).isoformat(), end.astimezone(timezone.utc).isoformat()])
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    groups, totals, unpriced = {}, {}, []
    with closing(sqlite3.connect(db._resolve_path("steins_gate", "control"))) as conn:
        conn.row_factory = sqlite3.Row
        # One cursor = one consistent SQLite read snapshot. Iterate, don't load history.
        for row in conn.execute("SELECT * FROM model_usage" + where, params):
            key = row["provider_id"], row["model_id"]
            if key not in groups:
                pricing = model_catalog.pricing_for(*key)
                groups[key] = {
                    "provider_id": key[0], "model_id": key[1], "calls": 0,
                    "input_tokens": None, "output_tokens": None, "cached_tokens": None,
                    "unreported_calls": 0, "unestimated_calls": 0,
                    "pricing": pricing, "estimated_cost": None,
                }
                if pricing is None:
                    unpriced.append({"provider_id": key[0], "model_id": key[1]})
            group = groups[key]
            group["calls"] += 1
            group["unreported_calls"] += not row["reported"]
            for name in ("input_tokens", "output_tokens", "cached_tokens"):
                if row[name] is not None:
                    group[name] = (group[name] or 0) + row[name]
            price = _price_at(group["pricing"], row["time_utc"])
            limit = price.get("max_input_tokens") if price else None
            if not price or not row["reported"] or (limit is not None and row["input_tokens"] >= limit):
                group["unestimated_calls"] += 1
                continue
            cached = min(row["cached_tokens"] or 0, row["input_tokens"])
            amount = sum((
                Decimal(row["input_tokens"] - cached) * Decimal(str(price["input"])),
                Decimal(row["output_tokens"]) * Decimal(str(price["output"])),
                Decimal(cached) * Decimal(str(price["cached_input"])),
            )) / Decimal(1_000_000)
            group["estimated_cost"] = (group["estimated_cost"] or Decimal(0)) + amount
            currency = price["currency"]
            totals[currency] = totals.get(currency, Decimal(0)) + amount
    result = sorted(groups.values(), key=lambda g: (g["provider_id"], g["model_id"]))
    for group in result:
        if group["estimated_cost"] is not None:
            group["estimated_cost"] = float(group["estimated_cost"])
    return {"estimate": True, "range": period, "tz": tz, "groups": result,
            "totals_by_currency": {k: float(v) for k, v in sorted(totals.items())},
            "unreported_calls": sum(g["unreported_calls"] for g in result),
            "unpriced_models": unpriced}
