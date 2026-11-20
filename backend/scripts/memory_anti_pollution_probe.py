"""[266] 2-C anti-pollution extraction probe (offline fake or budgeted live DeepSeek).

Usage (from backend/):
  python scripts/memory_anti_pollution_probe.py --run-dir /path/to/run --fake
  python scripts/memory_anti_pollution_probe.py --run-dir /path/to/run --live --rounds 3 --cap-cny 1.0
"""
from __future__ import annotations

import argparse
import asyncio
from decimal import Decimal
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

import httpx

SCRIPT = Path(__file__).resolve()
BACKEND = SCRIPT.parents[1]
sys.path.insert(0, str(SCRIPT.parent))
sys.path.insert(0, str(BACKEND))

from _real_run_common import (  # noqa: E402
    containing_live_root,
    deep_resolve,
    official_deepseek_base_url,
    production_live_roots,
)

FIXTURE = BACKEND / "tests" / "fixtures" / "memory_extraction_anti_pollution_v1.json"
DEFAULT_ENV_FILE = Path.home() / ".config" / "amadeus" / "deepseek.env"
DEFAULT_MODEL = "deepseek-flash"
USD_CNY = Decimal("7.25")
MAX_OUTPUT_TOKENS = 1024


class BudgetExceeded(RuntimeError):
    """Raised before or after a call when spend would exceed the hard cap."""


class FakeEmbedder:
    """Deterministic unit-norm 768-dim embedder for isolated write probing."""

    model_name = "fake-768"
    dimensions = 768

    async def encode_passage(self, text: str) -> list[float]:
        vec = [0.0] * self.dimensions
        vec[0] = 1.0
        return vec

    async def encode_query(self, text: str) -> list[float]:
        return await self.encode_passage(text)


def guard_path(raw: str | Path) -> Path:
    resolved = deep_resolve(Path(raw))
    if containing_live_root(resolved, production_live_roots()):
        raise ValueError("run_dir must not point inside live user data roots")
    return resolved


def load_deepseek_key(env_file: Path) -> str:
    """Read DEEPSEEK_API_KEY from env_file without logging or printing its value."""
    if not env_file.is_file():
        raise FileNotFoundError(f"DeepSeek env file not found: {env_file}")
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == "DEEPSEEK_API_KEY":
            cleaned = value.strip().strip('"').strip("'")
            if len(cleaned) >= 8:
                return cleaned
    raise ValueError("DEEPSEEK_API_KEY missing or invalid in env file")


def request_cost_bound_cny(payload: dict, price: dict | None) -> Decimal:
    if not price or price.get("currency") not in {"CNY", "USD"}:
        raise BudgetExceeded("missing catalog pricing for selected model")
    rates = (
        [price, price["schedule"]["peak"], price["schedule"]["off_peak"]]
        if price.get("schedule")
        else [price]
    )
    in_rate = max(Decimal(str(r["input"])) for r in rates)
    out_rate = max(Decimal(str(r["output"])) for r in rates)
    messages = payload.get("messages") or []
    upper_in = len(json.dumps(messages, ensure_ascii=False).encode("utf-8")) + 64 * len(messages) + 256
    upper_out = int(payload.get("max_tokens") or MAX_OUTPUT_TOKENS)
    fx = USD_CNY if price["currency"] == "USD" else Decimal(1)
    return (Decimal(upper_in) * in_rate + Decimal(upper_out) * out_rate) * fx / Decimal(1_000_000)


def report_spent_cny(report: dict) -> Decimal:
    totals = report.get("totals_by_currency") or {}
    cny = Decimal(str(totals.get("CNY", 0)))
    usd = Decimal(str(totals.get("USD", 0)))
    return cny + usd * USD_CNY


async def inspect_session_writes(session_id: str) -> dict:
    from app.db import get_db

    db = await get_db("steins_gate", "memory")
    try:
        facts = [
            str(r["display_text"])
            for r in await (
                await db.execute(
                    """SELECT v.display_text
                         FROM stable_facts f
                         JOIN stable_fact_versions v
                           ON v.fact_id = f.fact_id AND v.version_no = f.active_version
                        WHERE f.session_id = ? AND f.state = 'active'
                        ORDER BY f.created_at ASC""",
                    (session_id,),
                )
            ).fetchall()
        ]
        experiences = [
            str(r["display_text"])
            for r in await (
                await db.execute(
                    """SELECT display_text FROM experiences
                        WHERE session_id = ? ORDER BY created_at ASC""",
                    (session_id,),
                )
            ).fetchall()
        ]
        observations = [
            {"display_text": str(r["display_text"]), "status": str(r["status"])}
            for r in await (
                await db.execute(
                    """SELECT display_text, status FROM memory_observations
                        WHERE session_id = ? ORDER BY created_at ASC""",
                    (session_id,),
                )
            ).fetchall()
        ]
        non_rejected_obs = [o for o in observations if o["status"] != "rejected"]
        return {
            "stable_facts": facts,
            "experiences": experiences,
            "observations": observations,
            "written_count": len(facts) + len(experiences),
            "non_rejected_obs_count": len(non_rejected_obs),
        }
    finally:
        await db.close()


async def run_probe(
    *,
    run_dir: Path,
    mode: str,
    rounds: int,
    cap_cny: Decimal,
    model: str,
    env_file: Path,
) -> dict:
    run_dir = guard_path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    data_dir = run_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    os.environ["AMADEUS_DATA_DIR"] = str(data_dir)
    os.environ.pop("AMADEUS_DB_PATH", None)
    os.environ["AMADEUS_CREDENTIAL_BACKEND"] = "memory"

    from app import models
    from app.db import init_db, reset_initialization_cache
    from app.services import deepseek as deepseek_mod
    from app.services.conversations import conversation_service
    from app.services.credentials import credential_store
    from app.services.memory import memory_service
    from app.services.memory_v11 import jobs
    from app.services.model_catalog import model_catalog
    from app.services.provider_runtime import deepseek_service, provider_registry
    from app.services.usage_report import usage_report

    reset_initialization_cache()
    await init_db()
    memory_service.embedder = FakeEmbedder()

    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    cases = list(fixture["cases"])

    budget_state = {"spent_cny": Decimal(0), "calls": 0, "stopped_reason": None}
    price = model_catalog.pricing_for("deepseek", model)

    if mode == "live":
        if not official_deepseek_base_url(deepseek_service.base_url):
            raise RuntimeError("live probe only allows official DeepSeek endpoint")
        api_key = load_deepseek_key(env_file)
        credential_store.set("deepseek", api_key)
        expected_url = deepseek_service.base_url.rstrip("/") + "/chat/completions"

        base_client = httpx.AsyncClient

        class BudgetedClient(base_client):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, trust_env=False, **kwargs)

            def build_request(self, method, url, **kwargs):
                payload = kwargs.get("json")
                if method != "POST" or str(url) != expected_url or not isinstance(payload, dict):
                    raise BudgetExceeded("only official chat/completions POST is permitted")
                payload = dict(payload)
                payload["max_tokens"] = MAX_OUTPUT_TOKENS
                kwargs["json"] = payload
                return super().build_request(method, url, **kwargs)

            async def send(self, request, **kwargs):
                payload = json.loads(request.content)
                bound = request_cost_bound_cny(payload, price)
                if budget_state["spent_cny"] + bound > cap_cny:
                    budget_state["stopped_reason"] = (
                        f"budget_cap_precheck: spent={budget_state['spent_cny']:.6f} + "
                        f"bound={bound:.6f} > cap={cap_cny:.6f}"
                    )
                    raise BudgetExceeded(budget_state["stopped_reason"])
                budget_state["calls"] += 1
                kwargs["follow_redirects"] = False
                return await super().send(request, **kwargs)

        patched_httpx = SimpleNamespace(**{**vars(httpx), "AsyncClient": BudgetedClient})
        context_mgr = patch.object(deepseek_mod, "httpx", patched_httpx)
    else:
        current_case_ref: dict[str, dict] = {}

        class FakeAdapter:
            async def complete_json(self, *, messages, api_key, model, temperature=0.1):
                case = current_case_ref["case"]
                if not case["should_write"]:
                    return {"observations": []}
                text = case["mock_display_text"]
                return {
                    "observations": [
                        {
                            "observation_ref": "o1",
                            "display_text": text,
                            "fact_text": text,
                            "semantic": {
                                "subject": "user",
                                "predicate": case["category"],
                                "object": {"text": text},
                                "polarity": "positive",
                                "qualifiers": {},
                            },
                            "evidence_kind": "direct_user",
                            "memory_class": "stable_candidate",
                            "confidence": 0.92,
                            "topic_label": "对照",
                            "expires_at": None,
                            "relation": "new",
                            "candidate_index": None,
                            "reanalysis_target_observation_index": None,
                        }
                    ]
                }

        fake_snap = SimpleNamespace(
            adapter=FakeAdapter(),
            model_id=model,
            credential_required=True,
            require=lambda _task: None,
        )
        credential_store.set("deepseek", "sk-offline-fake-probe-key-266")
        context_mgr = patch.object(
            type(provider_registry), "snapshot", staticmethod(lambda *a, **kw: fake_snap)
        )

    round_results: list[dict] = []
    with context_mgr:
        for round_no in range(1, rounds + 1):
            round_cases: list[dict] = []
            for case in cases:
                if mode == "fake":
                    current_case_ref["case"] = case
                session_id = f"probe266-r{round_no}-{case['id']}"
                conv = await conversation_service.create(
                    session_id, "steins_gate", identity_mode="self"
                )
                cid = str(conv["id"])
                turn_id = f"turn-r{round_no}-{case['id']}"
                uid = await models.save_message(
                    session_id,
                    "user",
                    case["user_text"],
                    worldline="steins_gate",
                    turn_id=turn_id,
                    conversation_id=cid,
                )
                await models.save_message(
                    session_id,
                    "assistant",
                    case["assistant_reply"],
                    worldline="steins_gate",
                    turn_id=turn_id,
                    conversation_id=cid,
                )
                job = await jobs.enqueue_memory_job(
                    session_id=session_id,
                    worldline="steins_gate",
                    conversation_id=cid,
                    identity_mode="self",
                    source_message_id=int(uid),
                    provider_id="deepseek",
                    model_id=model,
                )
                try:
                    result = await jobs.process_memory_job(
                        job_id=job["job_id"],
                        lease_owner=f"probe-r{round_no}",
                        memory_completion=None,
                    )
                except BudgetExceeded as exc:
                    budget_state["stopped_reason"] = str(exc)
                    break

                if mode == "live":
                    rep = usage_report("all", None, 0)
                    budget_state["spent_cny"] = report_spent_cny(rep)
                    if budget_state["spent_cny"] > cap_cny:
                        budget_state["stopped_reason"] = (
                            f"budget_cap_postcheck: spent={budget_state['spent_cny']:.6f} > cap={cap_cny:.6f}"
                        )

                writes = await inspect_session_writes(session_id)
                is_mixed = bool(case.get("mixed"))
                if case["should_write"]:
                    texts = writes["stable_facts"] + writes["experiences"]
                    all_non_rejected = texts + [
                        o["display_text"]
                        for o in writes["observations"]
                        if o["status"] != "rejected"
                    ]
                    expected_any = case.get("expected_any") or []
                    forbidden_any = case.get("forbidden_any") or []
                    keyword_ok = (
                        any(any(k in t for k in expected_any) for t in texts)
                        if expected_any
                        else bool(texts)
                    )
                    forbidden_hit = [
                        bad
                        for bad in forbidden_any
                        if any(bad in t for t in all_non_rejected)
                    ]
                    passed = (
                        result["state"] == "completed"
                        and writes["written_count"] >= 1
                        and keyword_ok
                        and not forbidden_hit
                    )
                else:
                    forbidden_hit = []
                    passed = (
                        result["state"] == "completed"
                        and writes["written_count"] == 0
                        and writes["non_rejected_obs_count"] == 0
                    )

                round_cases.append(
                    {
                        "id": case["id"],
                        "category": case["category"],
                        "language": case["language"],
                        "should_write": case["should_write"],
                        "mixed": is_mixed,
                        "job_state": result["state"],
                        "last_error_code": result.get("last_error_code"),
                        "forbidden_hit": forbidden_hit,
                        "passed": passed,
                        **writes,
                    }
                )
                if budget_state["stopped_reason"]:
                    break

            neg = [r for r in round_cases if not r["should_write"]]
            mixed_rows = [r for r in round_cases if r["mixed"]]
            pos = [r for r in round_cases if r["should_write"] and not r["mixed"]]
            neg_polluted = [r for r in neg if not r["passed"]]
            mixed_failed = [r for r in mixed_rows if not r["passed"]]
            pos_missed = [r for r in pos if not r["passed"]]
            round_results.append(
                {
                    "round": round_no,
                    "total_cases": len(round_cases),
                    "negative_total": len(neg),
                    "negative_polluted": len(neg_polluted),
                    "mixed_total": len(mixed_rows),
                    "mixed_failed": len(mixed_failed),
                    "positive_total": len(pos),
                    "positive_missed": len(pos_missed),
                    "round_passed": (
                        len(round_cases) == len(cases)
                        and len(neg_polluted) == 0
                        and len(mixed_failed) == 0
                        and len(pos_missed) <= 1
                    ),
                    "cases": round_cases,
                }
            )
            if budget_state["stopped_reason"]:
                break

    final_usage = usage_report("all", None, 0) if mode == "live" else None
    spent_cny = float(report_spent_cny(final_usage)) if final_usage else 0.0
    all_passed = (
        budget_state["stopped_reason"] is None
        and len(round_results) == rounds
        and all(r["round_passed"] for r in round_results)
    )

    summary = {
        "mode": mode,
        "model": model,
        "rounds_requested": rounds,
        "rounds_completed": len(round_results),
        "cap_cny": float(cap_cny),
        "estimated_cny": spent_cny,
        "usd_to_cny": float(USD_CNY),
        "stopped_reason": budget_state["stopped_reason"],
        "all_passed": all_passed,
        "usage_report": final_usage,
        "rounds": round_results,
    }
    (run_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="[266] Anti-pollution extraction probe")
    parser.add_argument("--run-dir", required=True, help="Isolated output directory")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--fake", action="store_true", help="Offline fake adapter check")
    group.add_argument("--live", action="store_true", help="Real DeepSeek-V4.1-Flash run")
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--cap-cny", type=Decimal, default=Decimal("1.0"))
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    args = parser.parse_args()

    summary = asyncio.run(
        run_probe(
            run_dir=Path(args.run_dir),
            mode="live" if args.live else "fake",
            rounds=args.rounds,
            cap_cny=args.cap_cny,
            model=args.model,
            env_file=args.env_file,
        )
    )
    print(
        json.dumps(
            {
                "mode": summary["mode"],
                "model": summary["model"],
                "rounds_completed": summary["rounds_completed"],
                "estimated_cny": round(summary["estimated_cny"], 6),
                "stopped_reason": summary["stopped_reason"],
                "all_passed": summary["all_passed"],
                "per_round": [
                    {
                        "round": r["round"],
                        "neg_polluted": r["negative_polluted"],
                        "mixed_failed": r["mixed_failed"],
                        "pos_missed": r["positive_missed"],
                        "passed": r["round_passed"],
                    }
                    for r in summary["rounds"]
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if summary["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
