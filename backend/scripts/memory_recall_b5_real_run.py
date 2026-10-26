"""Frozen B5 read-only benchmark; fake by default, explicitly gated paid HTTP.

Run from backend: python scripts/memory_recall_b5_real_run.py --run-dir PATH
Fake replies are deliberately constant: their scores are plumbing evidence only.
Each turn runs in a fresh process/data root; no application lifespan or ingest
worker is started. Evaluator metadata never participates in prompt construction.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import re
import socket
import statistics
import subprocess
import sys
import time
import unicodedata
import uuid
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import portalocker

SCRIPT = Path(__file__).resolve()
BACKEND = SCRIPT.parents[1]
QUESTION_SET = BACKEND / "tests/fixtures/memory_recall_b5b_question_set.json"
sys.path.insert(0, str(SCRIPT.parent))
sys.path.insert(0, str(BACKEND))
import memory_candidate_runtime as isolation  # noqa: E402
from memory_candidate_real_run import (  # noqa: E402
    BUILTIN_DEEPSEEK_BASE_URL,
    CURRENCY_CAP_CNY,
    PEAK_CACHE_HIT_CNY_PER_MILLION,
    PEAK_INPUT_CNY_PER_MILLION,
    PEAK_OUTPUT_CNY_PER_MILLION,
    official_deepseek_base_url,
)

MAX_OUTPUT_TOKENS = 2000
UNKNOWN_PHRASES = (
    "不知道", "不清楚", "不记得", "没有记录", "無法確定", "无法确定",
    "わからない", "分からない", "知らない", "覚えていない", "記録がない",
    "記録はない", "把握していない", "don't know", "do not know", "don't remember",
    "do not remember", "no record", "not sure",
)
# An unknown acknowledgement must name the requested subject. Bare uncertainty
# cannot establish which part of a mixed answer is unknown and needs human review.
UNKNOWN_SUBJECTS = {
    "pet": ("宠物", "ペット", "pet", "飼って"),
    "morning_drink": ("早上", "早晨", "朝", "morning", "飲み物", "饮料"),
    "dinner_staple": ("晚餐", "晚饭", "夕食", "晩ご飯", "dinner"),
}
FAKE_ANSWER = "了解したわ。もう少し詳しく教えてくれる？"
EVALUATOR_FIELDS = (
    "expected", "exact_need_count", "max_need_count", "must_use_fact_keys",
    "must_not_use_fact_keys", "must_express_unknown_for", "diagnostic_only",
)
DISABLED = [
    "No app lifespan: no ingest worker, extraction, or compression scheduler",
    "get_clean_text_stream entry: no auto-title, subtitle translation/repair, or TTS",
    "Web executor is a local fixed stub; no search provider",
    "Transport rejects retries and extra rounds; at most first/tool_answer/japanese_rewrite",
]


class Refused(RuntimeError):
    """Safe error code; never include upstream text or credentials."""


class BudgetExceeded(Refused):
    pass


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def redact(value: Any) -> Any:
    # Also remove a provider accidentally echoing the credential into its body.
    secrets = [os.environ[name] for name in isolation.PROVIDER_ENV_KEYS
               if "KEY" in name and os.environ.get(name)]

    def clean(item):
        if isinstance(item, str):
            for secret in secrets:
                # response_sse and tool arguments may contain JSON-escaped text.
                for ensure_ascii in (True, False):
                    item = item.replace(json.dumps(secret, ensure_ascii=ensure_ascii)[1:-1], "[REDACTED]")
                item = item.replace(secret, "[REDACTED]")
            return item
        if isinstance(item, dict):
            return {clean(key): clean(child) for key, child in item.items()}
        if isinstance(item, (list, tuple)):
            return [clean(child) for child in item]
        return item

    return clean(value)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(redact(value), handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    tmp.replace(path)


def append_json(path: Path, value: Any) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(redact(value), ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def check_real_gate(transport: str, base_url: str, cap_cny: str | None,
                    environ: dict | None = None) -> None:
    env = os.environ if environ is None else environ
    if transport == "fake":
        return
    if transport != "real":
        raise Refused("unknown_transport")
    if env.get("AMADEUS_B5_REAL_HTTP") != "authorized":
        raise Refused("real_http_not_authorized")
    if not str(env.get("DEEPSEEK_API_KEY", "")).strip():
        raise Refused("real_credential_missing")
    try:
        official = official_deepseek_base_url(base_url)
    except ValueError:
        official = False
    if not official:
        raise Refused("unofficial_base_url")
    try:
        cap = Decimal(cap_cny or "NaN")
    except Exception:
        raise Refused("invalid_cap") from None
    if not cap.is_finite() or not 0 < cap <= CURRENCY_CAP_CNY:
        raise Refused("cap_required_positive_at_most_10_cny")


def load_questions() -> tuple[dict, str]:
    raw = QUESTION_SET.read_bytes()
    qset = json.loads(raw)
    if qset["version"] != "b5a-gate2-qset-v1":
        raise Refused("unknown_question_set_version")
    return qset, hashlib.sha256(raw).hexdigest()


def make_plan(qset: dict, digest: str, *, transport="fake", base_url=BUILTIN_DEEPSEEK_BASE_URL,
              cap_cny=None, model="deepseek-flash", e5_snapshot="") -> dict:
    rows = [
        {"id": f"{mode}-{case['id']}-{repeat}", "mode": mode,
         "case_id": case["id"], "cohort": case["cohort"], "repeat": repeat}
        for mode in ("legacy", "v11") for case in qset["cases"]
        for repeat in range(1, case["repeats"] + 1)
    ]
    if Counter(row["mode"] for row in rows) != {"legacy": 41, "v11": 41}:
        raise Refused("frozen_execution_count_changed")
    return {"version": 1, "question_sha256": digest, "script_sha256": sha256(SCRIPT),
            "transport": transport, "base_url": base_url, "cap_cny": cap_cny,
            "model": model, "max_tokens": MAX_OUTPUT_TOKENS, "e5_snapshot": e5_snapshot,
            "executions": rows, "rules": qset["rules"]}


def dataset_rows(qset: dict, case: dict) -> tuple[list[dict], list[dict]]:
    """Build seed copies; neither the question set nor its cases are mutated."""
    variant = qset["dataset_variants"].get(case["dataset"], {})
    removed = set(variant.get("remove", []) + variant.get("replace", []))
    superseded = set(variant.get("supersede", []))
    rows = []
    for row in qset["facts"] + qset["scope_sentinels"]:
        if row["key"] not in removed:
            rows.append({**row, "state": "superseded" if row["key"] in superseded
                         else row.get("state", "active")})
    rows.extend({**row, "scope": case["scope"], "state": "active"}
                for row in variant.get("add", []))
    return rows, [dict(row) for row in variant.get("experience", [])]


def fact_catalog(qset: dict) -> list[dict]:
    rows = qset["facts"] + qset["scope_sentinels"]
    for variant in qset["dataset_variants"].values():
        rows = rows + variant.get("add", []) + [
            {**row, "display_text": row["text"]} for row in variant.get("experience", [])]
    return rows


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text).casefold()).strip()


def leakage_violations(payload: dict, qset: dict, allowed_texts=()) -> list[str]:
    raw = json.dumps(payload, ensure_ascii=False)
    failures = [f"evaluator_field:{key}" for key in EVALUATOR_FIELDS
                if re.search(r'\b' + key + r'\b', raw)]
    # Display values, actual input/history, and prior model output are legitimate
    # sources; labels accidentally identical to words in those sources are not
    # evaluator leakage. Never whitelist the expected object itself.
    text = raw
    for value in sorted(set(allowed_texts), key=len, reverse=True):
        if value:
            text = text.replace(value, " ")
    labels = {row["key"] for row in fact_catalog(qset)}
    labels.update(value for case in qset["cases"]
                  for value in case["expected"].get("must_express_unknown_for", []))
    for key in sorted(labels):
        if re.search(r"(?<![\w])" + re.escape(key) + r"(?![\w])", text, re.I):
            failures.append(f"evaluator_label:{key}")
    # Detect an expected JSON object pasted as text, including category values;
    # scalar 1/3 and 'required' also occur in the live tool schema legitimately.
    for case in qset["cases"]:
        if json.dumps(case["expected"], ensure_ascii=False) in raw:
            failures.append("expected_object")
    return failures


def scope_violations(payload: dict, qset: dict, case: dict) -> list[str]:
    text = json.dumps(payload.get("messages", []), ensure_ascii=False)
    rows, _ = dataset_rows(qset, case)
    return [row["key"] for row in rows
            if (row["scope"] != case["scope"] or row["state"] != "active")
            and row["display_text"] in text]


def score(case: dict, answer: str, calls: list[dict], qset: dict) -> dict:
    from app.services.turn_tool_orchestrator import collect_recall_payloads

    expected = case["expected"]
    recalls = [c for c in calls if (c.get("function") or {}).get("name") == "recall_memory"]
    needs, error = collect_recall_payloads(recalls)
    failures = []
    category = expected["recall_memory"]
    if category == "required" and not recalls:
        failures.append("recall_required")
    if category == "forbidden" and recalls:
        failures.append("recall_forbidden")
    if error:
        failures.append("invalid_needs")
    if recalls and expected["exact_need_count"] is not None and len(needs) != expected["exact_need_count"]:
        failures.append("exact_need_count")
    if len(needs) > expected["max_need_count"]:
        failures.append("max_need_count")
    text = normalize(answer)
    used = sorted({row["key"] for row in fact_catalog(qset)
                   if any(normalize(alias) in text for alias in
                          [row["display_text"], *row.get("aliases", [])] if normalize(alias))})
    failures.extend(f"missing:{key}" for key in expected["must_use_fact_keys"] if key not in used)
    failures.extend(f"forbidden:{key}" for key in expected["must_not_use_fact_keys"] if key in used)
    manual = []
    for subject in expected["must_express_unknown_for"]:
        # Restrict uncertainty to the same sentence as its subject. Negation and
        # implied/contradictory unknowns still require manual review.
        sentences = re.split(r"[。.!?！？\n]+", text)
        recognized = any(any(normalize(p) in sentence for p in UNKNOWN_PHRASES)
                         and any(normalize(p) in sentence for p in UNKNOWN_SUBJECTS.get(subject, ()))
                         for sentence in sentences)
        if not recognized:
            manual.append(subject)
    status = "fail" if failures else "needs_manual" if manual else "pass"
    return {"status": status, "failures": failures, "needs_manual": manual,
            "used_fact_keys": used, "recall_called": bool(recalls), "need_count": len(needs),
            "needs": needs, "diagnostic_only": bool(expected["diagnostic_only"])}


def usage_cost(usage: dict) -> Decimal:
    def tokens(key):
        value = usage.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise Refused("invalid_usage")
        return value

    prompt, output = tokens("prompt_tokens"), tokens("completion_tokens")
    hit = usage.get("prompt_cache_hit_tokens", (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0))
    if isinstance(hit, bool) or not isinstance(hit, int) or not 0 <= hit <= prompt:
        raise Refused("invalid_cache_usage")
    if "prompt_cache_miss_tokens" in usage and tokens("prompt_cache_miss_tokens") != prompt - hit:
        raise Refused("invalid_cache_usage")
    return (Decimal(prompt - hit) * Decimal(str(PEAK_INPUT_CNY_PER_MILLION))
            + Decimal(hit) * Decimal(str(PEAK_CACHE_HIT_CNY_PER_MILLION))
            + Decimal(output) * Decimal(str(PEAK_OUTPUT_CNY_PER_MILLION))) / 1_000_000


def estimate_input_tokens(payload: dict) -> int:
    # UTF-8 bytes conservatively bound byte-level tokens; include tool schemas
    # and a framing allowance. This is a reserve estimate, not billed usage.
    return len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) + 128


class Budget:
    """Whole-run lease serializes callers; reserve is durable before HTTP."""

    def __init__(self, path: Path, cap_cny: str):
        self.path = path
        self.cap = Decimal(cap_cny)
        if not self.cap.is_finite() or self.cap <= 0:
            raise Refused("invalid_budget_cap")
        if path.exists():
            self.state = read_json(path)
            if Decimal(self.state["cap_cny"]) != self.cap or self.state["version"] != 1:
                raise Refused("budget_configuration_changed")
            # Corrupt/unknown state never resets the amount already reserved.
            for row in self.state["attempts"]:
                amount = Decimal(row["charge_cny"])
                if not amount.is_finite() or amount < 0:
                    raise Refused("invalid_budget_state")
        else:
            self.state = {"version": 1, "cap_cny": str(self.cap), "attempts": []}
            write_json(path, self.state)

    @property
    def committed(self) -> Decimal:
        return sum((Decimal(row["charge_cny"]) for row in self.state["attempts"]), Decimal(0))

    def reserve(self, payload: dict, execution_id: str) -> int:
        reserve = (Decimal(estimate_input_tokens(payload)) * Decimal(str(PEAK_INPUT_CNY_PER_MILLION))
                   + Decimal(payload["max_tokens"]) * Decimal(str(PEAK_OUTPUT_CNY_PER_MILLION))) / 1_000_000
        if self.committed + reserve > self.cap:
            raise BudgetExceeded("not_run_budget")
        index = len(self.state["attempts"])
        self.state["attempts"].append({"execution_id": execution_id, "charge_cny": str(reserve),
                                       "reserve_cny": str(reserve), "status": "reserved"})
        write_json(self.path, self.state)
        return index

    def settle(self, index: int, usage: dict | None) -> None:
        if usage is None:
            raise Refused("usage_missing_reservation_retained")
        cost = usage_cost(usage)
        row = self.state["attempts"][index]
        row.update(charge_cny=str(cost), status="settled", usage=usage)
        write_json(self.path, self.state)
        if cost > Decimal(row["reserve_cny"]):
            raise Refused("usage_exceeded_reservation_stop")


def request_kind(payload: dict, phase: str) -> str:
    from app.domain.japanese_response_renderer import JAPANESE_RENDERER_SYSTEM_PROMPT

    if any(m.get("content") == JAPANESE_RENDERER_SYSTEM_PROMPT for m in payload["messages"]):
        return "japanese_rewrite"
    return phase


def fake_response(payload: dict, kind: str) -> str:
    """Fixed fake behavior reads the request envelope only, never expected."""
    names = {(t.get("function") or {}).get("name") for t in payload.get("tools", [])}
    if kind == "first" and "recall_memory" in names:
        query = next((m["content"] for m in reversed(payload["messages"]) if m["role"] == "user"), "probe")
        delta = {"tool_calls": [{"index": 0, "id": "fake-recall", "type": "function",
                  "function": {"name": "recall_memory", "arguments": json.dumps({"needs": [query[:160]]}, ensure_ascii=False)}}]}
    else:
        delta = {"content": "[EMO:neutral] " + FAKE_ANSWER}
    # Synthetic token counts are deterministic estimates, never claimed as real
    # provider tokenization. Use the same byte bound for preflight cost planning.
    usage = {"prompt_tokens": estimate_input_tokens(payload), "completion_tokens": 32,
             "prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": estimate_input_tokens(payload)}
    return "data: " + json.dumps({"choices": [{"delta": delta}]}, ensure_ascii=False) + "\n\n" + \
        "data: " + json.dumps({"choices": [], "usage": usage}) + "\n\ndata: [DONE]\n\n"


class Capture:
    def __init__(self, plan: dict, entry: dict, qset: dict, case: dict, directory: Path):
        self.plan, self.entry, self.qset, self.case, self.directory = plan, entry, qset, case, directory
        self.phase = "first"
        self.requests: list[dict] = []
        self.kinds: set[str] = set()
        self.allowed = [case["query"], *(m["content"] for m in case["history"]),
                        *(row["display_text"] for row in fact_catalog(qset))]
        self.budget = Budget(directory.parents[1] / "budget.json", plan["cap_cny"]) if plan["transport"] == "real" else None

    async def handle(self, request: httpx.Request) -> httpx.Response:
        check_real_gate(self.plan["transport"], self.plan["base_url"], self.plan["cap_cny"])
        payload = json.loads(request.content)
        kind = request_kind(payload, self.phase)
        if kind in self.kinds or len(self.kinds) >= 3:
            raise Refused("extra_model_request_blocked")
        if request.method != "POST" or str(request.url) != self.plan["base_url"].rstrip("/") + "/chat/completions":
            raise Refused("unexpected_model_destination")
        leaks = leakage_violations(payload, self.qset, self.allowed)
        scope = scope_violations(payload, self.qset, self.case)
        if leaks or scope:
            raise Refused("request_leakage:" + ",".join(leaks + scope))
        payload["max_tokens"] = MAX_OUTPUT_TOKENS
        payload["stream_options"] = {"include_usage": True}
        # The request below is the intercepted product envelope plus the two
        # explicit billing controls above; captures show exactly what is sent.
        record = {"kind": kind, "request": payload, "input_estimate_tokens": estimate_input_tokens(payload)}
        index = self.budget.reserve(payload, self.entry["id"]) if self.budget else None
        self.kinds.add(kind)
        self.requests.append(record)
        write_json(self.directory / "requests.json", self.requests)
        start = time.perf_counter()
        if self.plan["transport"] == "fake":
            body = fake_response(payload, kind)
            record["first_token_ms"] = (time.perf_counter() - start) * 1000
        else:
            chunks = []
            # The nested client is the sole network boundary. Preserve inherited
            # proxy and CA trust; no redirects or transport retries.
            async with httpx.AsyncClient(timeout=120, follow_redirects=False) as client:
                headers = {key: value for key, value in request.headers.items() if key.lower() != "content-length"}
                async with client.stream("POST", str(request.url), headers=headers, json=payload) as response:
                    if response.status_code != 200:
                        raise Refused("provider_http_" + str(response.status_code))
                    async for line in response.aiter_lines():
                        if "first_token_ms" not in record and line.startswith("data:"):
                            try:
                                delta = json.loads(line[5:].strip()).get("choices", [{}])[0].get("delta", {})
                            except (ValueError, IndexError):
                                delta = {}
                            if delta.get("content") or delta.get("tool_calls"):
                                record["first_token_ms"] = (time.perf_counter() - start) * 1000
                        chunks.append(line)
            body = "\n".join(chunks) + "\n"
        record["elapsed_ms"] = (time.perf_counter() - start) * 1000
        record["response_sse"] = body
        usage = None
        for line in body.splitlines():
            if line.startswith("data:"):
                try:
                    chunk = json.loads(line[5:].strip())
                except ValueError:
                    continue
                if chunk.get("usage"):
                    usage = chunk["usage"]
                for choice in chunk.get("choices", []):
                    delta = choice.get("delta", {})
                    if delta.get("content"):
                        self.allowed.append(delta["content"])
                    for call in delta.get("tool_calls", []):
                        arguments = (call.get("function") or {}).get("arguments", "")
                        self.allowed.append(arguments)
                        try:
                            self.allowed.extend(value for value in json.loads(arguments).get("needs", []) if isinstance(value, str))
                        except (ValueError, AttributeError):
                            pass
        record["usage"] = usage
        record["cost_cny"] = str(usage_cost(usage)) if usage is not None else None
        write_json(self.directory / "requests.json", self.requests)
        if self.budget:
            self.budget.settle(index, usage)
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})


async def seed_store(qset: dict, case: dict, mode: str, embedder: Any) -> None:
    from app.db import get_db
    from app.services.memory_v11.repository import create_stable_fact

    rows, experiences = dataset_rows(qset, case)
    # The frozen drink sentinel and replacement describe one fact's history.
    # v11 has no 'superseded' fact state: keep past values as closed versions of
    # the live drink, with exactly one open version and a valid active pointer.
    drink_chain = [row for key in ("superseded_tea", "coffee", "coffee_new")
                   for row in rows if row["key"] == key]
    drink_current = next((row for row in reversed(drink_chain) if row["state"] == "active"), None)
    histories = [row for row in drink_chain if row is not drink_current]
    if mode == "v11" and histories:
        if drink_current is None:
            raise Refused("superseded_seed_without_successor")
        rows = [row for row in rows if row not in histories]
    for number, row in enumerate(rows):
        owner, worldline, identity = row["scope"].split("/")
        if mode == "v11":
            saved = await create_stable_fact(session_id=owner, worldline=worldline, identity_mode=identity,
                                            display_text=row["display_text"],
                                            semantic_json={"subject": "user", "predicate": "note", "object": str(number)})
            db = await get_db(worldline, "memory")
            try:
                version = len(histories) + 1 if row is drink_current else 1
                if version > 1:
                    await db.execute("""UPDATE stable_fact_versions SET version_no=?,previous_version=?,
                        change_kind='supersede' WHERE fact_id=? AND version_no=1""",
                                     (version, version - 1, saved["fact_id"]))
                    for old_version, old in enumerate(histories, 1):
                        text = old["display_text"]
                        semantic = json.dumps({"subject": "user", "predicate": "note", "object": text}, ensure_ascii=False)
                        await db.execute("""INSERT INTO stable_fact_versions(fact_id,version_no,display_text,
                            semantic_json,semantic_fingerprint,confidence,change_kind,previous_version,
                            valid_from,invalid_at) VALUES(?,?,?,?,?,0.9,?,?,?,?)""",
                            (saved["fact_id"], old_version, text, semantic, hashlib.sha256(semantic.encode()).hexdigest(),
                             "create" if old_version == 1 else "supersede", old_version - 1 if old_version > 1 else None,
                             f"2026-01-{old_version:02d}T00:00:00Z", f"2026-01-{old_version + 1:02d}T00:00:00Z"))
                    await db.execute("UPDATE stable_facts SET active_version=? WHERE fact_id=?", (version, saved["fact_id"]))
                if row["state"] != "active":
                    if row["state"] != "deleted":
                        raise Refused("unsupported_superseded_seed")
                    await db.execute("UPDATE stable_facts SET state='deleted',active_version=NULL,deleted_at=? WHERE fact_id=?",
                                     ("2026-01-01T00:00:00Z", saved["fact_id"]))
                    await db.execute("UPDATE stable_fact_versions SET invalid_at='2026-01-01T00:00:00Z' WHERE fact_id=?", (saved["fact_id"],))
                elif embedder._model is not None:
                    from app.services.memory_v11.indexing import insert_version_embedding, prepare_passage_embedding
                    prepared = await prepare_passage_embedding(row["display_text"], embedder=embedder)
                    await insert_version_embedding(db, fact_id=saved["fact_id"], version_no=version, prepared=prepared)
                await db.commit()
            finally:
                await db.close()
        else:
            db = await get_db(worldline, "memory")
            try:
                # Opaque seed key prevents evaluator fact labels reaching prompts.
                await db.execute("""INSERT INTO core_facts(session_id,identity_mode,fact_key,fact_value,
                    confidence,importance,is_current,source_message_ids,created_at)
                    VALUES(?,?,?,?,0.9,0.5,?,'[]','2026-10-01T00:00:00+00:00')""",
                                 (owner, identity, f"entry-{number:03d}", row["display_text"], int(row["state"] == "active")))
                await db.commit()
            finally:
                await db.close()
    for experience in experiences:
        owner, worldline, identity = case["scope"].split("/")
        db = await get_db(worldline, "memory")
        try:
            if mode == "legacy":
                await db.execute("""INSERT INTO episodic_memories(session_id,identity_mode,content,
                    confidence,importance,source_message_ids,created_at) VALUES(?,?,?,0.9,0.5,'[]',?)""",
                                 (owner, identity, experience["text"], "2026-10-01T00:00:00+00:00"))
            else:
                from app.services.memory_v11.experiences import insert_experience
                observation = str(uuid.uuid4())
                await db.execute("""INSERT INTO memory_observations(observation_id,session_id,identity_mode,
                    display_text,evidence_kind,memory_class,status,created_at,updated_at)
                    VALUES(?,?,?,?,'direct_user','episodic','attached',?,?)""",
                                 (observation, owner, identity, experience["text"], "2026-10-01", "2026-10-01"))
                await insert_experience(db, session_id=owner, conversation_id="seed-experience",
                                        identity_mode=identity, observation_id=observation,
                                        display_text=experience["text"], semantic={"event": "past"}, confidence=0.9)
            await db.commit()
        finally:
            await db.close()
    if mode == "v11":
        from app.services.memory_v11.repository import validate_active_version_invariants
        for worldline in {row["scope"].split("/")[1] for row in rows}:
            await validate_active_version_invariants(worldline=worldline)


async def product_turn(plan: dict, entry: dict, directory: Path) -> dict:
    # Environment isolation happens in the parent before any app import here.
    from app import models
    from app.db import init_db, reset_initialization_cache
    from app.agent_tools import AMADEUS_TOOLS
    from app.routers.chat_ws import SessionState, get_clean_text_stream, should_force_search, build_web_search_evidence_context
    from app.services import prompt_compiler as compiler
    from app.services.conversations import conversation_service
    from app.services.memory import EmbeddingAdapter, memory_service
    from app.services.provider_adapters import OpenAICompatibleAdapter
    from app.services.provider_registry import ProviderCapabilities, ProviderSnapshot, ProviderTask
    from app.services.provider_runtime import deepseek_service
    from app.services.turn_events import TurnModeState
    from app.services.turn_tool_orchestrator import (
        TurnExecutionRecord, apply_v11_tool_batch, build_v11_web_evidence_context,
        plan_first_round_tools, prepare_second_round_payload, refresh_candidates_for_send,
        snapshot_from_session,
    )

    qset, digest = load_questions()
    if digest != plan["question_sha256"]:
        raise Refused("question_set_changed")
    case = next(c for c in qset["cases"] if c["id"] == entry["case_id"])
    reset_initialization_cache()
    await init_db()

    class LocalE5(EmbeddingAdapter):
        def _load(self):
            if self._model is None:
                raise RuntimeError("local_e5_unavailable")
            return self._model

        def readiness(self):
            return {"ok": self._model is not None, "loaded": self._model is not None,
                    "model": self.model_name, "dimensions": 384}

    embedder = LocalE5()
    e5 = {"loaded": False, "reason": "local_snapshot_missing"}
    snapshot = Path(plan["e5_snapshot"])
    if plan["e5_snapshot"] and snapshot.is_dir():
        try:
            from sentence_transformers import SentenceTransformer
            embedder._model = SentenceTransformer(str(snapshot), local_files_only=True, trust_remote_code=False)
            e5 = {"loaded": True, "reason": "local_snapshot_loaded"}
        except Exception as exc:
            e5 = {"loaded": False, "reason": type(exc).__name__}
    memory_service.embedder = embedder
    await seed_store(qset, case, entry["mode"], embedder)
    capture = Capture(plan, entry, qset, case, directory)
    adapter = OpenAICompatibleAdapter(provider_id="deepseek", base_url=plan["base_url"],
        default_model=plan["model"], message_builder=deepseek_service.build_persona_messages,
        credential_required=plan["transport"] == "real", transport=httpx.MockTransport(capture.handle))
    provider = ProviderSnapshot(provider_id="deepseek", model_id=plan["model"], adapter=adapter,
        capabilities=ProviderCapabilities(tasks=frozenset({ProviderTask.CHAT})), credential_required=False)
    owner, worldline, identity = case["scope"].split("/")
    session = SessionState(owner)
    session.worldline, session.identity_mode = worldline, identity
    session.protocol_version, session.enable_tts, session.client_type = 1, False, "desktop"
    session.model = plan["model"]
    session.api_key = os.environ.get("DEEPSEEK_API_KEY") if plan["transport"] == "real" else None
    selected = await conversation_service.create_and_select(owner, worldline, provider_id="deepseek",
        model_id=plan["model"], identity_mode=identity)
    session.conversation_id = str(selected["id"])
    for message in [*case["history"], {"role": "user", "content": case["query"]}]:
        mid = await models.save_message(owner, message["role"], message["content"], worldline=worldline,
                                        conversation_id=session.conversation_id)
        session.append_message({**message, "id": mid})
    supplement_ms = []
    original_supplement = compiler._select_v11_memory_supplement

    async def timed_supplement(**kwargs):
        start = time.perf_counter()
        try:
            return await original_supplement(**kwargs)
        finally:
            supplement_ms.append((time.perf_counter() - start) * 1000)

    compiler._select_v11_memory_supplement = timed_supplement
    started = time.perf_counter()
    result = {**entry, "status": "completed", "calls": [], "e5": e5, "disabled": DISABLED}
    try:
        session.system_prompt = await compiler.compile_for_session(session, case["query"], provider=provider)
        result.update(core_facts="CORE FACTS" in session.system_prompt,
                      memory_supplement="MEMORY CANDIDATES" in session.system_prompt,
                      supplement_ms=sum(supplement_ms), supplement_calls=len(supplement_ms))
        force_search = should_force_search(case["query"])
        turn = TurnExecutionRecord(snapshot=snapshot_from_session(session, provider),
                                   injected_fact_ids=getattr(session, "turn_injected_fact_ids", ()))
        history = list(session.history)

        async def web_stub(_name, _arguments):
            return case.get("web_stub", {}).get("result", "Local stub: public evidence unavailable.")

        # Match the product's v11 direct-search evidence path, with a local stub.
        if force_search:
            turn.web_results.append(("web_search", await web_stub("web_search", "{}")))
            history = (build_v11_web_evidence_context(history, turn.web_results) if entry["mode"] == "v11"
                       else build_web_search_evidence_context(history, turn.web_results[0][1]))
        tools = plan_first_round_tools(v11=entry["mode"] == "v11", force_search=force_search,
                                       provider_id="deepseek", adapter_kind=type(adapter).__name__).tools
        if entry["mode"] == "legacy" and not force_search:
            tools = list(AMADEUS_TOOLS)
        answer, _, calls, _ = await get_clean_text_stream(active_history=history, memory_summary="",
            session=session, tools=tools, tool_choice="auto" if tools else None,
            provider_snapshot=provider, mode_state=turn.mode_state if entry["mode"] == "v11" else None)
        result["calls"] = list(calls.values())
        if calls:
            if entry["mode"] != "v11":
                raise Refused("unexpected_legacy_tool")
            outcome = await apply_v11_tool_batch(record=turn, session=session, provider=provider,
                calls=result["calls"], web_executor=web_stub)
            if outcome.recall_result is not None:
                async def refresh(_attempt):
                    recalled = await refresh_candidates_for_send(turn, session, provider)
                    return prepare_second_round_payload(history, recalled,
                        system_prompt=session.system_prompt).history
                second_history = await refresh(0)
            else:
                refresh = None
                second_history = build_v11_web_evidence_context(history, turn.web_results)
            capture.phase = "tool_answer"
            answer, _, late_calls, _ = await get_clean_text_stream(active_history=second_history,
                memory_summary="", session=session, provider_snapshot=provider,
                mode_state=TurnModeState(), before_send=refresh)
            if late_calls:
                raise Refused("late_tool_calls")
        result["answer"] = answer
        result["score"] = score(case, answer, result["calls"], qset)
    except Exception as exc:
        result.update(status="not_run_budget" if isinstance(exc, BudgetExceeded) else "error",
                      error=str(exc) if isinstance(exc, Refused) else type(exc).__name__,
                      score={"status": "fail", "failures": ["execution_error"],
                             "needs_manual": [], "diagnostic_only": case["expected"]["diagnostic_only"]})
    finally:
        compiler._select_v11_memory_supplement = original_supplement
    result.update(requests=capture.requests, request_count=len(capture.requests),
                  elapsed_ms=(time.perf_counter() - started) * 1000,
                  cost_cny=str(sum((Decimal(r.get("cost_cny") or "0") for r in capture.requests), Decimal(0))) )
    if sha256(QUESTION_SET) != digest:
        raise Refused("question_set_changed_after_execution")
    return result


def execute_subprocess(plan: dict, entry: dict, directory: Path) -> dict:
    started = time.perf_counter()
    root = directory / "data"
    if root.exists():
        raise Refused("execution_data_already_exists")
    root.mkdir()
    env = isolation.isolated_environ(run_root=directory, candidate=root, backend_port=18001, frontend_port=18422)
    env.update(AMADEUS_MEMORY_MODE=entry["mode"], HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
               HF_HUB_DISABLE_TELEMETRY="1", TOKENIZERS_PARALLELISM="false")
    # User-installed packages must remain importable after the child's HOME is
    # isolated. Only package directories are inherited, never user startup files.
    env["PYTHONPATH"] = os.pathsep.join([str(BACKEND), *[
        path for path in sys.path if Path(path).name in {"site-packages", "dist-packages"}]])
    if plan["transport"] == "real":
        # Keep runtime proxy/CA/auth settings for the real nested HTTP client.
        for key, value in os.environ.items():
            if key not in env and (key.upper().endswith("_PROXY") or key in {
                "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
                "DEEPSEEK_API_KEY", "AMADEUS_B5_REAL_HTTP"}):
                env[key] = value
    proc = subprocess.run([sys.executable, str(SCRIPT), "--worker", str(directory)],
                          cwd=BACKEND, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          timeout=600, check=False)
    # Raw subprocess stdout/stderr is discarded; errors may contain secrets.
    if proc.returncode or not (directory / "result.json").is_file():
        raise Refused("worker_failed_" + str(proc.returncode))
    result = read_json(directory / "result.json")
    result["execution_wall_ms"] = (time.perf_counter() - started) * 1000
    return result


def percentile(values: list, p: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    return values[max(0, math.ceil(len(values) * p) - 1)]


def summarize(plan: dict, rows: list[dict], qset: dict) -> tuple[dict, str]:
    cases = {c["id"]: c for c in qset["cases"]}
    def rates(selected):
        # Repeated cases are a unit: all frozen repetitions must pass.
        unique = sorted({r["case_id"] for r in selected})
        eligible = [c for c in unique if not cases[c]["expected"]["diagnostic_only"]]
        counts = Counter()
        for case_id in eligible:
            found = [r for r in selected if r["case_id"] == case_id]
            if any(r["status"] == "error" or r.get("score", {}).get("status") == "fail" for r in found):
                counts["failed"] += 1
            elif any(r["status"] == "interrupted" or r.get("score", {}).get("status") == "needs_manual" for r in found):
                counts["needs_manual"] += 1
            elif len(found) != cases[case_id]["repeats"] or any(r["status"] != "completed" for r in found):
                counts["not_run"] += 1
            else:
                counts["passed"] += 1
        evaluated = counts["passed"] + counts["failed"]
        return {**{key: counts[key] for key in ("passed", "failed", "needs_manual", "not_run")},
                "total": len(eligible), "evaluated": evaluated,
                "rate": counts["passed"] / evaluated if evaluated else None}

    summary = {"transport": plan["transport"], "question_sha256": plan["question_sha256"], "modes": {}}
    lines = ["# B5 read benchmark", "", f"transport: {plan['transport']}",
             f"question_sha256: {plan['question_sha256']}",
             "Rates = passed/(passed+failed) cases; every repetition must pass. Manual/unrun/diagnostic excluded.",
             "Fake scores validate plumbing only. Fake usage is a UTF-8 byte upper estimate.", ""]
    for mode in ("legacy", "v11"):
        selected = [r for r in rows if r["mode"] == mode]
        requests = [request for r in selected for request in r.get("requests", [])]
        durations = [r.get("execution_wall_ms", r.get("elapsed_ms")) for r in selected
                     if "execution_wall_ms" in r or "elapsed_ms" in r]
        model_summary = {"executions": len(selected), "overall": rates(selected),
            "cohorts": {c: rates([r for r in selected if r["cohort"] == c]) for c in ("regression", "first_look")},
            "categories": {c: rates([r for r in selected if cases[r["case_id"]]["expected"]["recall_memory"] == c])
                           for c in ("required", "forbidden", "allowed")},
            "needs_manual": sum(bool(r.get("score", {}).get("needs_manual")) or r["status"] == "interrupted" for r in selected),
            "statuses": dict(Counter(r["status"] for r in selected)),
            "request_kinds": dict(Counter(r["kind"] for r in requests)),
            "request_count_per_execution": dict(Counter(r.get("request_count", 0) for r in selected)),
            "cost_cny": str(sum((Decimal(r.get("cost_cny") or "0") for r in selected), Decimal(0))),
            "actual_cost_cny": "0" if plan["transport"] == "fake" else str(sum(
                (Decimal(r.get("cost_cny") or "0") for r in selected), Decimal(0))),
            "elapsed_ms_p50": statistics.median(durations) if durations else None,
            "elapsed_ms_p95": percentile(durations, .95),
            "input_tokens_p50": statistics.median(tokens) if (tokens := [r["usage"]["prompt_tokens"] for r in requests if r.get("usage")]) else None,
            "input_tokens_max": max((r["usage"]["prompt_tokens"] for r in requests if r.get("usage")), default=None),
            "e5_loaded_executions": sum(r.get("e5", {}).get("loaded", False) for r in selected)}
        summary["modes"][mode] = model_summary
        def rate_text(value):
            rate = f"{value['rate']:.1%}" if value["rate"] is not None else "n/a"
            return (f"{value['passed']}/{value['evaluated']} ({rate}); "
                    f"planned={value['total']}, manual={value['needs_manual']}, unrun={value['not_run']}")
        lines.extend([f"## {mode}", f"executions: {len(selected)}; statuses: {model_summary['statuses']}",
                      "overall: " + rate_text(model_summary["overall"]),
                      *[f"{key}: {rate_text(value)}" for key, value in model_summary["cohorts"].items()],
                      *[f"expected.{key}: {rate_text(value)}" for key, value in model_summary["categories"].items()],
                      f"needs_manual executions: {model_summary['needs_manual']}",
                      f"requests: {model_summary['request_kinds']}; per execution: {model_summary['request_count_per_execution']}",
                      f"{'synthetic estimated' if plan['transport'] == 'fake' else 'actual'} cost_cny: {model_summary['cost_cny']}",
                      f"actual cost_cny: {model_summary['actual_cost_cny']}",
                      f"execution wall_ms P50/P95: {model_summary['elapsed_ms_p50']}/{model_summary['elapsed_ms_p95']}",
                      f"input tokens P50/max: {model_summary['input_tokens_p50']}/{model_summary['input_tokens_max']}",
                      f"E5 loaded executions: {model_summary['e5_loaded_executions']}", ""])
    return summary, "\n".join(lines)


def run_suite(run_dir: Path, plan: dict, qset: dict, executor=execute_subprocess) -> dict:
    run_dir = run_dir.resolve()
    if isolation.containing_live_root(run_dir, isolation.production_live_roots()) is not None:
        raise Refused("run_directory_is_live_data")
    run_dir.mkdir(parents=True, exist_ok=True)
    with portalocker.Lock(str(run_dir / "run.lock"), timeout=0):
        plan_path = run_dir / "plan.json"
        if plan_path.exists():
            if read_json(plan_path) != plan:
                raise Refused("frozen_plan_changed")
        else:
            if any(run_dir.iterdir()) and set(p.name for p in run_dir.iterdir()) != {"run.lock"}:
                raise Refused("nonempty_directory_without_plan")
            write_json(plan_path, plan)
        if plan["transport"] == "real":
            if not (run_dir / "budget.json").exists() and (run_dir / "executions").exists():
                raise Refused("budget_missing_existing_run")
            Budget(run_dir / "budget.json", plan["cap_cny"])
        journal = run_dir / "executions.jsonl"
        rows = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()] if journal.exists() else []
        done = {row["id"] for row in rows}
        if len(done) != len(rows) or not done <= {e["id"] for e in plan["executions"]}:
            raise Refused("execution_journal_invalid")
        stop_status = "not_run_budget" if any(r["status"] == "not_run_budget" for r in rows) else None
        if not stop_status and plan["transport"] == "real" and any(r["status"] in {"error", "interrupted"} for r in rows):
            stop_status = "not_run_after_error"
        for entry in plan["executions"]:
            if entry["id"] in done:
                continue
            directory = run_dir / "executions" / entry["id"]
            result_file = directory / "result.json"
            if result_file.exists():
                row = read_json(result_file)
            elif (directory / "started.json").exists():
                # An interrupted paid turn must never be automatically repeated.
                row = {**entry, "status": "interrupted", "score": {"status": "needs_manual", "needs_manual": ["interrupted"]}}
                if (directory / "requests.json").exists():
                    row["requests"] = read_json(directory / "requests.json")
                if plan["transport"] == "real":
                    stop_status = stop_status or "not_run_after_error"
            elif stop_status:
                row = {**entry, "status": stop_status, "request_count": 0}
            else:
                directory.mkdir(parents=True, exist_ok=True)
                write_json(directory / "started.json", entry)
                try:
                    row = executor(plan, entry, directory)
                except Exception as exc:
                    row = {**entry, "status": "error", "error": str(exc) if isinstance(exc, Refused) else type(exc).__name__}
                    if (directory / "requests.json").exists():
                        row["requests"] = read_json(directory / "requests.json")
                write_json(result_file, row)
            row.setdefault("request_count", len(row.get("requests", [])))
            row.setdefault("cost_cny", str(sum((Decimal(r.get("cost_cny") or "0")
                                               for r in row.get("requests", [])), Decimal(0))))
            rows.append(row)
            append_json(journal, row)
            if row["status"] == "not_run_budget":
                stop_status = "not_run_budget"
            elif row["status"] in {"error", "interrupted"} and plan["transport"] == "real":
                stop_status = stop_status or "not_run_after_error"
        if sha256(QUESTION_SET) != plan["question_sha256"]:
            raise Refused("question_set_changed_after_run")
        summary, markdown = summarize(plan, rows, qset)
        if (run_dir / "budget.json").exists():
            budget = Budget(run_dir / "budget.json", plan["cap_cny"])
            summary["budget"] = budget.state
            markdown += f"\nBudget committed (including unresolved reserves): {budget.committed} CNY\n"
        write_json(run_dir / "summary.json", summary)
        (run_dir / "summary.md").write_text(markdown, encoding="utf-8", newline="\n")
        return summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--transport", choices=("fake", "real"), default="fake")
    parser.add_argument("--base-url", default=BUILTIN_DEEPSEEK_BASE_URL)
    parser.add_argument("--cap-cny")
    parser.add_argument("--model", default="deepseek-flash")
    parser.add_argument("--e5-snapshot", default=str(Path.home() / ".cache/huggingface/hub/models--intfloat--multilingual-e5-small/snapshots/614241f622f53c4eeff9890bdc4f31cfecc418b3"))
    parser.add_argument("--worker", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        if args.worker:
            directory = args.worker.resolve()
            plan = read_json(directory.parents[1] / "plan.json")
            entry = read_json(directory / "started.json")
            check_real_gate(plan["transport"], plan["base_url"], plan["cap_cny"])
            if plan["transport"] == "fake":
                def offline_connect(*_args, **_kwargs):
                    raise Refused("network_forbidden_in_fake")
                socket.socket.connect = offline_connect
                socket.socket.connect_ex = offline_connect
                socket.getaddrinfo = offline_connect
            write_json(directory / "result.json", asyncio.run(product_turn(plan, entry, directory)))
            return 0
        check_real_gate(args.transport, args.base_url, args.cap_cny)
        if args.run_dir is None:
            raise Refused("run_directory_required")
        qset, digest = load_questions()
        plan = make_plan(qset, digest, transport=args.transport, base_url=args.base_url,
                         cap_cny=args.cap_cny, model=args.model, e5_snapshot=args.e5_snapshot)
        summary = run_suite(args.run_dir, plan, qset)
        print(json.dumps({"run_dir": str(args.run_dir.resolve()), "transport": args.transport,
                          "statuses": {mode: row["statuses"] for mode, row in summary["modes"].items()}}, ensure_ascii=False))
        return int(any(row["statuses"].get("error", 0) or row["statuses"].get("interrupted", 0) for row in summary["modes"].values()))
    except Exception as exc:
        print("B5 refused: " + (str(exc) if isinstance(exc, Refused) else type(exc).__name__), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
