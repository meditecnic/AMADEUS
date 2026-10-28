"""Frozen B5 read-only benchmark; fake by default, explicitly gated paid HTTP.

Run from backend: python scripts/memory_recall_b5_real_run.py --run-dir PATH
Fake replies are deliberately constant: their scores are plumbing evidence only.
Each turn runs in a fresh process/data root; no application lifespan or ingest
worker is started. Evaluator metadata never participates in prompt construction.
The budget cap covers this run directory only; a new directory starts at zero.
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
import threading
import subprocess
import sys
import time
import unicodedata
import uuid
from collections import Counter
from contextlib import ExitStack, contextmanager, nullcontext
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import patch
from urllib.parse import urlsplit, urlunsplit
import urllib.request

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
MAX_MAIN_ATTEMPTS = 3  # chat_ws.get_clean_text_stream, per first/tool-answer call.
MAX_RENDER_ATTEMPTS = 2  # _render_foreign_speech_to_japanese, per render call.
UNKNOWN_PHRASES = (
    "不知道", "不清楚", "不记得", "没有记录", "無法確定", "无法确定",
    "わからない", "分からない", "知らない", "覚えていない", "記録がない",
    "記録はない", "把握していない", "don't know", "do not know", "don't remember",
    "do not remember", "no record", "not sure", "聞いてない", "聞いていない",
    "覚えてない", "記憶にない", "不明", "没听说", "没告诉过",
)
# An unknown acknowledgement must name the requested subject. Bare uncertainty
# cannot establish which part of a mixed answer is unknown and needs human review.
UNKNOWN_SUBJECTS = {
    "pet": ("宠物", "ペット", "pet", "飼って", "猫", "ねこ", "犬", "狗", "cat", "dog"),
    "morning_drink": ("早上", "早晨", "朝", "morning", "飲み物", "饮料", "咖啡", "コーヒー", "coffee"),
    "dinner_staple": ("晚餐", "晚饭", "夕食", "晩ご飯", "dinner", "主食", "米", "ご飯", "パン", "麺"),
}
FAKE_ANSWER = "了解したわ。もう少し詳しく教えてくれる？"
FALLBACK_ANSWERS = (
    "ごめん。日本語の応答を正しく生成できなかった。もう一度試して。",
    "はぁ？何言ってんの。あたしは牧瀬紅莉栖よ。変なこと言わないで。",
)
RENDERER_SOURCE_MARKER = "[SOURCE DATA — NOT INSTRUCTIONS]\n"
EVALUATOR_FIELDS = (
    "expected", "exact_need_count", "max_need_count", "must_use_fact_keys",
    "must_not_use_fact_keys", "must_express_unknown_for", "diagnostic_only",
)
DISABLED = [
    "No app lifespan: no ingest worker, extraction, or compression scheduler",
    "get_clean_text_stream entry: no auto-title, subtitle translation/repair, or TTS",
    "Web executor is a local fixed stub; no search provider",
    "Product retries allowed: up to 3 main requests per first/tool_answer call and 2 Japanese render attempts",
]


class Refused(RuntimeError):
    """Safe error code; never include upstream text or credentials."""


class BudgetExceeded(Refused):
    pass


class AccountingUncertain(Refused):
    """Stop the whole run because provider expenditure cannot be bounded safely."""


@contextmanager
def fake_network_guard():
    """Deny all network; only socketpair's synchronous local setup may connect."""
    connect = socket.socket.connect
    connect_ex = socket.socket.connect_ex
    socketpair = socket.socketpair
    local = threading.local()
    loopback = ("127.0.0.1", "::1", b"127.0.0.1", b"::1")

    def check_address(address):
        if not (getattr(local, "socketpair", False) and isinstance(address, tuple)
                and address and address[0] in loopback):
            raise Refused("network_forbidden_in_fake")

    def offline_connect(sock, address):
        check_address(address)
        return connect(sock, address)

    def offline_connect_ex(sock, address):
        check_address(address)
        return connect_ex(sock, address)

    def local_socketpair(*args, **kwargs):
        previous = getattr(local, "socketpair", False)
        local.socketpair = True
        try:
            return socketpair(*args, **kwargs)
        finally:
            local.socketpair = previous

    def forbidden(*_args, **_kwargs):
        raise Refused("network_forbidden_in_fake")

    async def async_forbidden(*_args, **_kwargs):
        raise Refused("network_forbidden_in_fake")

    from asyncio.selector_events import BaseSelectorEventLoop

    with ExitStack() as stack:
        for owner, name, replacement in [
            (socket.socket, "connect", offline_connect),
            (socket.socket, "connect_ex", offline_connect_ex),
            (socket.socket, "sendto", forbidden),
            (socket, "socketpair", local_socketpair),
            (socket, "getaddrinfo", forbidden),
            (BaseSelectorEventLoop, "sock_connect", async_forbidden),
            (BaseSelectorEventLoop, "sock_sendto", async_forbidden),
            (asyncio.BaseEventLoop, "create_datagram_endpoint", async_forbidden),
            (httpx.HTTPTransport, "handle_request", forbidden),
            (httpx.AsyncHTTPTransport, "handle_async_request", async_forbidden),
            (urllib.request, "urlopen", forbidden),
        ]:
            stack.enter_context(patch.object(owner, name, replacement))
        if hasattr(socket.socket, "sendmsg"):
            stack.enter_context(patch.object(socket.socket, "sendmsg", forbidden))
        if sys.platform == "win32":
            from asyncio.proactor_events import BaseProactorEventLoop
            from asyncio.windows_events import IocpProactor
            stack.enter_context(patch.object(BaseProactorEventLoop, "sock_connect", async_forbidden))
            stack.enter_context(patch.object(BaseProactorEventLoop, "sock_sendto", async_forbidden))
            stack.enter_context(patch.object(IocpProactor, "connect", forbidden))
            stack.enter_context(patch.object(IocpProactor, "sendto", forbidden))
        yield


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


def canonical_base_url(base_url: str) -> str:
    try:
        parts = urlsplit(base_url)
        if not official_deepseek_base_url(base_url):
            raise ValueError
        normalized = urlunsplit((parts.scheme.lower(), (parts.hostname or "").lower(),
                                 parts.path.rstrip("/"), parts.query, parts.fragment))
    except ValueError:
        raise Refused("unofficial_base_url") from None
    if normalized != BUILTIN_DEEPSEEK_BASE_URL:
        raise Refused("unofficial_base_url")
    return normalized


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
    canonical_base_url(base_url)
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
        for case in qset["cases"] for repeat in range(1, case["repeats"] + 1)
        for mode in ("legacy", "v11")
    ]
    if Counter(row["mode"] for row in rows) != {"legacy": 41, "v11": 41}:
        raise Refused("frozen_execution_count_changed")
    return {"version": 1, "question_sha256": digest, "script_sha256": sha256(SCRIPT),
            "transport": transport, "base_url": canonical_base_url(base_url), "cap_cny": cap_cny,
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


def contains_expected_object(payload: Any, qset: dict) -> bool:
    """Inspect objects even when embedded in JSON strings; never whitelist them."""
    expected = [case["expected"] for case in qset["cases"]]
    decoder = json.JSONDecoder()

    def visit(value):
        if isinstance(value, dict):
            return value in expected or any(visit(child) for child in value.values())
        if isinstance(value, list):
            return any(visit(child) for child in value)
        if isinstance(value, str):
            for index, char in enumerate(value):
                if char != "{":
                    continue
                try:
                    nested, _ = decoder.raw_decode(value, index)
                except (ValueError, RecursionError):
                    continue
                if visit(nested):
                    return True
        return False

    return visit(payload)


def rewrite_validation_payload(payload: dict, prior_outputs: list[str], qset: dict) -> dict:
    """Project a verified renderer draft out of leak checks, never out of HTTP."""
    if contains_expected_object(payload, qset):
        raise Refused("request_leakage:expected_object")
    messages = [dict(message) for message in payload["messages"]]
    sources = [message for message in messages if isinstance(message.get("content"), str)
               and message["content"].startswith(RENDERER_SOURCE_MARKER)]
    if len(sources) != 1:
        raise Refused("request_leakage:unverified_assistant_draft")
    try:
        source = json.loads(sources[0]["content"][len(RENDERER_SOURCE_MARKER):])
    except ValueError:
        raise Refused("request_leakage:unverified_assistant_draft") from None
    if (not isinstance(source, dict) or not isinstance(source.get("assistant_draft"), str)
            or not isinstance(source.get("original_user_request"), str)):
        raise Refused("request_leakage:unverified_assistant_draft")

    def without_prefix(text):
        return re.sub(r"^\s*\[(?:E?MO)[:=][a-zA-Z0-9_]+\]\s*", "", text, count=1).strip()

    def from_previous_output(draft, output):
        from app.domain.segments import IncrementalJapaneseSegmenter

        previous = without_prefix(output)
        if draft in previous:
            return True
        # The product strips each segment before concatenating foreign_tail,
        # removing line breaks and sentence-boundary whitespace from the SSE.
        segmenter = IncrementalJapaneseSegmenter()
        segments = list(segmenter.feed(previous)) + list(segmenter.finish())
        return draft in "".join(segment.text for segment in segments)

    draft = without_prefix(source["assistant_draft"])
    if not draft or not any(from_previous_output(draft, output) for output in prior_outputs):
        raise Refused("request_leakage:unverified_assistant_draft")
    # Inspect the remaining source fields structurally, avoiding the renderer's
    # extra JSON-string escaping. The actual request/capture keeps the full draft.
    sources[0]["content"] = {**source, "assistant_draft": ""}
    return {**payload, "messages": messages}


def leakage_violations(payload: dict, qset: dict, allowed_texts=()) -> list[str]:
    raw = json.dumps(payload, ensure_ascii=False)
    # Display values, actual input/history, and prior model output are legitimate
    # sources; labels accidentally identical to words in those sources are not
    # evaluator leakage. Never whitelist the expected object itself.
    text = raw
    for value in sorted(set(allowed_texts), key=len, reverse=True):
        if value:
            text = text.replace(json.dumps(value, ensure_ascii=False)[1:-1], " ")
    failures = [f"evaluator_field:{key}" for key in EVALUATOR_FIELDS
                if re.search(r'\b' + key + r'\b', text)]
    labels = {row["key"] for row in fact_catalog(qset)}
    labels.update(value for case in qset["cases"]
                  for value in case["expected"].get("must_express_unknown_for", []))
    for key in sorted(labels):
        if re.search(r"(?<![\w])" + re.escape(key) + r"(?![\w])", text, re.I):
            failures.append(f"evaluator_label:{key}")
    # Detect an expected JSON object pasted as text, including category values;
    # scalar 1/3 and 'required' also occur in the live tool schema legitimately.
    if contains_expected_object(payload, qset):
        failures.append("expected_object")
    return failures


def scope_violations(payload: dict, qset: dict, case: dict) -> list[str]:
    text = json.dumps(payload.get("messages", []), ensure_ascii=False)
    rows, _ = dataset_rows(qset, case)
    return [row["key"] for row in rows
            if (row["scope"] != case["scope"] or row["state"] != "active")
            and row["display_text"] in text]


def score(case: dict, answer: str, calls: list[dict], qset: dict, *, mode="v11") -> dict:
    from app.services.turn_tool_orchestrator import collect_recall_payloads

    expected = case["expected"]
    recalls = [c for c in calls if (c.get("function") or {}).get("name") == "recall_memory"]
    needs, error = collect_recall_payloads(recalls)
    tool_failures = []
    category = expected["recall_memory"]
    if category == "required" and not recalls:
        tool_failures.append("recall_required")
    if category == "forbidden" and recalls:
        tool_failures.append("recall_forbidden")
    if error:
        tool_failures.append("invalid_needs")
    if recalls and expected["exact_need_count"] is not None and len(needs) != expected["exact_need_count"]:
        tool_failures.append("exact_need_count")
    if len(needs) > expected["max_need_count"]:
        tool_failures.append("max_need_count")
    text = normalize(answer)
    sources = [normalize(case["query"]), *(normalize(m["content"]) for m in case["history"])]
    matches, collisions = {}, {}
    for row in fact_catalog(qset):
        aliases = [row["display_text"], *row.get("aliases", [])]
        matches[row["key"]] = [a for a in aliases if normalize(a) and normalize(a) in text]
        collisions[row["key"]] = [a for a in aliases if normalize(a) and any(normalize(a) in s for s in sources)]
    used = sorted(key for key, aliases in matches.items() if aliases)
    failures, manual, collision_details = [], [], []
    for rule, keys in (("must_use", expected["must_use_fact_keys"]),
                       ("must_not", expected["must_not_use_fact_keys"])):
        for key in keys:
            matched, shared = matches.get(key, []), collisions.get(key, [])
            ambiguous = bool(matched and shared and (
                rule == "must_not" or all(normalize(a) in {normalize(s) for s in shared} for a in matched)))
            if ambiguous:
                manual.append(f"{rule}_collision:{key}")
                collision_details.append({"fact_key": key, "rule": rule,
                                          "input_aliases": shared, "matched_aliases": matched})
            elif rule == "must_use" and not matched:
                failures.append(f"missing:{key}")
            elif rule == "must_not" and matched:
                failures.append(f"forbidden:{key}")
    for subject in expected["must_express_unknown_for"]:
        # Restrict uncertainty to the same sentence as its subject. Negation and
        # implied/contradictory unknowns still require manual review.
        sentences = re.split(r"[。.!?！？\n]+", text)
        recognized = any(any(normalize(p) in sentence for p in UNKNOWN_PHRASES)
                         and any(normalize(p) in sentence for p in UNKNOWN_SUBJECTS.get(subject, ()))
                         for sentence in sentences)
        if not recognized:
            manual.append(subject)
    fallback = any(normalize(phrase) in text for phrase in FALLBACK_ANSWERS)
    if fallback:
        failures, tool_failures, manual = [], [], ["product_fallback"]
    answer_status = "fail" if failures else "needs_manual" if manual else "pass"
    if mode == "legacy":
        tool_failures = []
    combined = failures + tool_failures
    status = "fail" if combined else "needs_manual" if manual else "pass"
    return {"status": status, "failures": combined, "needs_manual": manual, "fallback": fallback,
            "answer": {"status": answer_status, "failures": failures, "needs_manual": manual},
            "tool": {"status": "needs_manual" if fallback else "fail" if tool_failures else "pass",
                     "failures": tool_failures, "needs_manual": ["product_fallback"] if fallback else []}
                     if mode == "v11" else None,
            "alias_collisions": collision_details,
            "used_fact_keys": used, "recall_called": bool(recalls) if mode == "v11" else None,
            "need_count": len(needs) if mode == "v11" else None,
            "needs": needs if mode == "v11" else None, "diagnostic_only": bool(expected["diagnostic_only"])}


def usage_cost(usage: dict) -> Decimal:
    def tokens(key):
        value = usage.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise AccountingUncertain("invalid_usage")
        return value

    prompt, output = tokens("prompt_tokens"), tokens("completion_tokens")
    hit = usage.get("prompt_cache_hit_tokens", (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0))
    if isinstance(hit, bool) or not isinstance(hit, int) or not 0 <= hit <= prompt:
        raise AccountingUncertain("invalid_cache_usage")
    if "prompt_cache_miss_tokens" in usage and tokens("prompt_cache_miss_tokens") != prompt - hit:
        raise AccountingUncertain("invalid_cache_usage")
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

    def amounts(self, execution_id: str | None = None) -> dict:
        rows = [r for r in self.state["attempts"] if execution_id is None or r["execution_id"] == execution_id]
        return {"settled_cny": str(sum((Decimal(r["charge_cny"]) for r in rows if r["status"] == "settled"), Decimal(0))),
                "retained_reserve_cny": str(sum((Decimal(r["charge_cny"]) for r in rows if r["status"] == "reserved"), Decimal(0)))}

    def reserve(self, payload: dict, execution_id: str, *, kind="first", attempt=1, phase="first") -> int:
        reserve = (Decimal(estimate_input_tokens(payload)) * Decimal(str(PEAK_INPUT_CNY_PER_MILLION))
                   + Decimal(payload["max_tokens"]) * Decimal(str(PEAK_OUTPUT_CNY_PER_MILLION))) / 1_000_000
        if self.committed + reserve > self.cap:
            raise BudgetExceeded("not_run_budget")
        index = len(self.state["attempts"])
        self.state["attempts"].append({"execution_id": execution_id, "charge_cny": str(reserve),
                                       "reserve_cny": str(reserve), "status": "reserved",
                                       "kind": kind, "attempt": attempt, "phase": phase})
        write_json(self.path, self.state)
        return index

    def settle(self, index: int, usage: dict | None) -> None:
        if usage is None:
            raise AccountingUncertain("usage_missing_reservation_retained")
        cost = usage_cost(usage)
        row = self.state["attempts"][index]
        row.update(charge_cny=str(cost), status="settled", usage=usage)
        if cost > Decimal(row["reserve_cny"]):
            self.state["stop_reason"] = "usage_exceeded_reservation_stop"
        write_json(self.path, self.state)
        if cost > Decimal(row["reserve_cny"]):
            raise AccountingUncertain("usage_exceeded_reservation_stop")


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
        self.main_attempts = Counter()
        self.renderer_attempts = Counter()
        self.refusals: list[Refused] = []
        self.allowed = [case["query"], *(m["content"] for m in case["history"]),
                        *(row["display_text"] for row in fact_catalog(qset))]
        self.output_indices: list[int] = []
        self.budget = Budget(directory.parents[1] / "budget.json", plan["cap_cny"]) if plan["transport"] == "real" else None

    async def handle(self, request: httpx.Request) -> httpx.Response:
        self.raise_if_refused()
        try:
            return await self._handle(request)
        except Exception as exc:
            if self.budget and (Decimal(self.budget.amounts(self.entry["id"])["retained_reserve_cny"])
                                or self.budget.state.get("stop_reason")) and not isinstance(exc, AccountingUncertain):
                exc = AccountingUncertain("request_failed_reservation_retained")
            if isinstance(exc, Refused):
                self.refusals.append(exc)
            raise exc from None
        finally:
            if self.requests:
                if self.budget:
                    for record in self.requests:
                        row = self.budget.state["attempts"][record["budget_index"]]
                        record["settled_cny"] = row["charge_cny"] if row["status"] == "settled" else "0"
                        record["retained_reserve_cny"] = row["charge_cny"] if row["status"] == "reserved" else "0"
                write_json(self.directory / "requests.json", self.requests)

    def raise_if_refused(self):
        # The product renderer catches provider errors; never score its fallback
        # as a successful benchmark answer after our transport refused a request.
        for kind in (AccountingUncertain, BudgetExceeded, Refused):
            for exc in self.refusals:
                if isinstance(exc, kind):
                    raise exc

    async def _handle(self, request: httpx.Request) -> httpx.Response:
        check_real_gate(self.plan["transport"], self.plan["base_url"], self.plan["cap_cny"])
        payload = json.loads(request.content)
        kind = request_kind(payload, self.phase)
        if kind == "japanese_rewrite":
            attempt = self.renderer_attempts[self.phase] + 1
            if attempt > MAX_RENDER_ATTEMPTS or not self.main_attempts[self.phase]:
                raise Refused("extra_model_request_blocked")
        else:
            attempt = self.main_attempts[self.phase] + 1
            if self.phase not in {"first", "tool_answer"} or attempt > MAX_MAIN_ATTEMPTS or self.renderer_attempts[self.phase]:
                raise Refused("extra_model_request_blocked")
        if request.method != "POST" or str(request.url) != self.plan["base_url"].rstrip("/") + "/chat/completions":
            raise Refused("unexpected_model_destination")
        validation_payload = (rewrite_validation_payload(payload,
            [self.allowed[index] for index in self.output_indices], self.qset)
            if kind == "japanese_rewrite" else payload)
        leaks = leakage_violations(validation_payload, self.qset, self.allowed)
        scope = scope_violations(payload, self.qset, self.case)
        if leaks or scope:
            raise Refused("request_leakage:" + ",".join(leaks + scope))
        payload["max_tokens"] = MAX_OUTPUT_TOKENS
        payload["stream_options"] = {"include_usage": True}
        # The request below is the intercepted product envelope plus the two
        # explicit billing controls above; captures show exactly what is sent.
        record = {"kind": kind, "phase": self.phase, "attempt": attempt, "request": payload,
                  "input_estimate_tokens": estimate_input_tokens(payload)}
        index = self.budget.reserve(payload, self.entry["id"], kind=kind, attempt=attempt,
                                    phase=self.phase) if self.budget else None
        if kind == "japanese_rewrite":
            self.renderer_attempts[self.phase] = attempt
        else:
            self.main_attempts[self.phase] = attempt
        record["main_attempt"] = self.main_attempts[self.phase]
        if self.budget:
            record["budget_index"] = index
            record["reserve_cny"] = self.budget.state["attempts"][index]["reserve_cny"]
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
        content_parts, arguments = [], {}
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
                        content_parts.append(delta["content"])
                    for call in delta.get("tool_calls", []):
                        key = call.get("index", call.get("id", 0))
                        arguments[key] = arguments.get(key, "") + (call.get("function") or {}).get("arguments", "")
        # Only this complete-response entry can establish renderer provenance.
        self.output_indices.append(len(self.allowed))
        self.allowed.append("".join(content_parts))
        for raw in arguments.values():
            self.allowed.append(raw)
            try:
                from app.services.memory_v11.recall import normalize_needs
                for value in json.loads(raw).get("needs", []):
                    if isinstance(value, str):
                        self.allowed.extend([value, *normalize_needs([value]), normalize(value)])
            except (ValueError, TypeError, AttributeError):
                pass
        record["usage"] = usage
        if usage is None:
            raise AccountingUncertain("usage_missing_reservation_retained")
        record["cost_cny"] = str(usage_cost(usage))
        record.update(settled_cny=record["cost_cny"], retained_reserve_cny="0")
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
    from app.routers.chat_ws import (
        SessionState, get_clean_text_stream, should_force_search,
        build_web_search_evidence_context, build_tool_result_context,
    )
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
            if _name != "web_search":
                return f"Error: Tool {_name} not found."
            return case.get("web_stub", {}).get("result", "Local stub: public evidence unavailable.")

        async def clean(**kwargs):
            response = await get_clean_text_stream(**kwargs)
            capture.raise_if_refused()
            return response

        # Match the product's v11 direct-search evidence path, with a local stub.
        if force_search:
            turn.web_results.append(("web_search", await web_stub("web_search", "{}")))
            history = (build_v11_web_evidence_context(history, turn.web_results) if entry["mode"] == "v11"
                       else build_web_search_evidence_context(history, turn.web_results[0][1]))
        tools = plan_first_round_tools(v11=entry["mode"] == "v11", force_search=force_search,
                                       provider_id="deepseek", adapter_kind=type(adapter).__name__).tools
        if entry["mode"] == "legacy" and not force_search:
            tools = list(AMADEUS_TOOLS)
        answer, _, calls, _ = await clean(active_history=history, memory_summary="",
            session=session, tools=tools, tool_choice="auto" if tools else None,
            provider_snapshot=provider, mode_state=turn.mode_state if entry["mode"] == "v11" else None)
        result["calls"] = list(calls.values())
        if calls:
            refresh = None
            if entry["mode"] == "v11":
                outcome = await apply_v11_tool_batch(record=turn, session=session, provider=provider,
                    calls=result["calls"], web_executor=web_stub)
                extra_web = turn.web_results[1:] if force_search else turn.web_results
                if outcome.recall_result is not None:
                    payload = prepare_second_round_payload(history, outcome.recall_result,
                        extra_web or None, system_prompt=session.system_prompt)
                    if payload.result is not None:
                        turn.recall_result = payload.result
                        turn.ranked_items = list(payload.result.ranked_items or [])
                    second_history = payload.history

                    async def refresh(_attempt):
                        recalled = await refresh_candidates_for_send(turn, session, provider)
                        return prepare_second_round_payload(history, recalled, extra_web or None,
                            system_prompt=session.system_prompt).history
                elif turn.web_results:
                    second_history = build_v11_web_evidence_context(history, list(turn.web_results))
                else:
                    second_history = build_tool_result_context(history, [])
            else:
                tool_results = []
                for call in result["calls"]:
                    name = (call.get("function") or {}).get("name")
                    arguments = (call.get("function") or {}).get("arguments")
                    tool_results.append((name or "unknown_tool", await web_stub(name, arguments)))
                second_history = build_tool_result_context(history, tool_results)
            capture.phase = "tool_answer"
            answer, _, _, _ = await clean(active_history=second_history,
                memory_summary="", session=session, tools=None, tool_choice=None, provider_snapshot=provider,
                mode_state=TurnModeState() if entry["mode"] == "v11" else None, before_send=refresh)
        result["answer"] = answer
        result["score"] = score(case, answer, result["calls"], qset, mode=entry["mode"])
        result["fallback"] = result["score"]["fallback"]
    except Exception as exc:
        try:
            capture.raise_if_refused()
        except Refused as refused:
            exc = refused
        result.update(status="not_run_budget" if isinstance(exc, BudgetExceeded) else "error",
                      error=str(exc) if isinstance(exc, Refused) else type(exc).__name__,
                      stop_run=isinstance(exc, AccountingUncertain))
    finally:
        compiler._select_v11_memory_supplement = original_supplement
    result.update(requests=capture.requests, request_count=len(capture.requests),
                  elapsed_ms=(time.perf_counter() - started) * 1000)
    execution_accounting(result, directory.parents[1], plan)
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
    if plan["transport"] == "fake":
        env.update(NO_PROXY="*", no_proxy="*")
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
    try:
        proc = subprocess.run([sys.executable, str(SCRIPT), "--worker", str(directory)],
                              cwd=BACKEND, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=600, check=False)
    except subprocess.TimeoutExpired:
        raise AccountingUncertain("worker_timeout") from None
    except OSError:
        raise AccountingUncertain("worker_start_failed") from None
    # Only a final, allowlisted refusal code may escape raw subprocess output.
    if proc.returncode or not (directory / "result.json").is_file():
        failure = "worker_failed_" + str(proc.returncode)
        lines = proc.stderr.splitlines()
        code = re.fullmatch(rb"B5 refused: ([a-z0-9_:,]+)", lines[-1]) if lines else None
        if code:
            failure += ":" + code[1].decode("ascii")
        raise AccountingUncertain(failure)
    try:
        result = read_json(directory / "result.json")
        if not isinstance(result, dict) or result.get("id") != entry["id"] or "status" not in result:
            raise ValueError
    except (ValueError, OSError):
        raise AccountingUncertain("worker_result_invalid") from None
    result["execution_wall_ms"] = (time.perf_counter() - started) * 1000
    return result


def execution_accounting(row: dict, run_dir: Path, plan: dict) -> None:
    budget_path = run_dir / "budget.json"
    if budget_path.exists():
        budget = Budget(budget_path, plan["cap_cny"])
        row.update(budget.amounts(row["id"]))
        if Decimal(row["retained_reserve_cny"]) or budget.state.get("stop_reason"):
            row["stop_run"] = True
    else:
        row["settled_cny"] = str(sum((Decimal(r.get("cost_cny") or "0") for r in row.get("requests", [])), Decimal(0)))
        row["retained_reserve_cny"] = "0"
    row["cost_cny"] = str(Decimal(row["settled_cny"]) + Decimal(row["retained_reserve_cny"]))
    row["actual_cost_cny"] = row["settled_cny"] if plan["transport"] == "real" else "0"


def percentile(values: list, p: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    return values[max(0, math.ceil(len(values) * p) - 1)]


def summarize(plan: dict, rows: list[dict], qset: dict) -> tuple[dict, str]:
    cases = {c["id"]: c for c in qset["cases"]}
    def rates(selected, layer=None):
        # Repeated cases are a unit: all frozen repetitions must pass.
        unique = sorted({r["case_id"] for r in selected})
        eligible = [c for c in unique if not cases[c]["expected"]["diagnostic_only"]]
        counts = Counter()
        for case_id in eligible:
            found = [r for r in selected if r["case_id"] == case_id]
            scores = [r.get("score", {}).get(layer, r.get("score", {})) if layer else r.get("score", {})
                      for r in found if r["status"] == "completed"]
            has_error = any(r["status"] in {"error", "interrupted"} for r in found)
            has_unrun = (len(found) != cases[case_id]["repeats"] or any(
                r["status"] not in {"completed", "error", "interrupted"} for r in found))
            if any(s.get("status") == "fail" for s in scores):
                counts["failed"] += 1
            elif has_error or has_unrun:
                counts["incomplete"] += 1
                counts["error"] += int(has_error)
                counts["not_run"] += int(has_unrun)
            elif any(s.get("status") != "pass" for s in scores):
                counts["needs_manual"] += 1
            else:
                counts["passed"] += 1
        evaluated = counts["passed"] + counts["failed"]
        return {**{key: counts[key] for key in ("passed", "failed", "needs_manual", "not_run", "error", "incomplete")},
                "total": len(eligible), "evaluated": evaluated,
                "rate": counts["passed"] / evaluated if evaluated else None}

    summary = {"transport": plan["transport"], "question_sha256": plan["question_sha256"], "modes": {}}
    lines = ["# B5 read benchmark", "", f"transport: {plan['transport']}",
             f"question_sha256: {plan['question_sha256']}",
             "Rates = passed/(passed+failed) cases; every repetition must pass. Manual/incomplete/diagnostic excluded.",
             "Only completed executions are scored. Legacy overall is answer-only; v11 overall includes tools.",
             "Budget cap is per run directory; a new directory starts at zero.",
             "Fake scores validate plumbing only. Fake usage is a UTF-8 byte upper estimate.", ""]
    for mode in ("legacy", "v11"):
        selected = [r for r in rows if r["mode"] == mode]
        requests = [request for r in selected for request in r.get("requests", [])]
        durations = [r.get("execution_wall_ms", r.get("elapsed_ms")) for r in selected
                     if "execution_wall_ms" in r or "elapsed_ms" in r]
        model_summary = {"executions": len(selected), "overall": rates(selected),
            "answer": rates(selected, "answer"),
            "tool": rates(selected, "tool") if mode == "v11" else None,
            "cohorts": {c: rates([r for r in selected if r["cohort"] == c]) for c in ("regression", "first_look")},
            "categories": {c: rates([r for r in selected if cases[r["case_id"]]["expected"]["recall_memory"] == c], "tool")
                           if mode == "v11" else None for c in ("required", "forbidden", "allowed")},
            "need_counts": dict(Counter(r.get("score", {}).get("need_count") for r in selected
                                         if r["status"] == "completed")) if mode == "v11" else None,
            "needs_manual": sum(bool(r.get("score", {}).get("needs_manual")) or r["status"] == "interrupted" for r in selected),
            "statuses": dict(Counter(r["status"] for r in selected)),
            "error_executions": sum(r["status"] == "error" for r in selected),
            "fallback_executions": sum(bool(r.get("fallback")) for r in selected),
            "error_reasons": dict(Counter(r.get("error", "unknown") for r in selected if r["status"] == "error")),
            "request_kinds": dict(Counter(r["kind"] for r in requests)),
            "request_count_per_execution": dict(Counter(r.get("request_count", 0) for r in selected)),
            "cost_cny": str(sum((Decimal(r.get("cost_cny") or "0") for r in selected), Decimal(0))),
            "settled_cny": str(sum((Decimal(r.get("settled_cny") or "0") for r in selected), Decimal(0))),
            "retained_reserve_cny": str(sum((Decimal(r.get("retained_reserve_cny") or "0") for r in selected), Decimal(0))),
            "actual_cost_cny": "0" if plan["transport"] == "fake" else str(sum(
                (Decimal(r.get("actual_cost_cny") or "0") for r in selected), Decimal(0))),
            "elapsed_ms_p50": statistics.median(durations) if durations else None,
            "elapsed_ms_p95": percentile(durations, .95),
            "input_tokens_p50": statistics.median(tokens) if (tokens := [r["usage"]["prompt_tokens"] for r in requests if r.get("usage")]) else None,
            "input_tokens_max": max((r["usage"]["prompt_tokens"] for r in requests if r.get("usage")), default=None),
            "e5_loaded_executions": sum(r.get("e5", {}).get("loaded", False) for r in selected)}
        summary["modes"][mode] = model_summary
        def rate_text(value):
            if value is None:
                return "n/a (legacy has no recall_memory tool)"
            rate = f"{value['rate']:.1%}" if value["rate"] is not None else "n/a"
            return (f"{value['passed']}/{value['evaluated']} ({rate}); "
                    f"planned={value['total']}, manual={value['needs_manual']}, "
                    f"incomplete={value['incomplete']}, error={value['error']}, unrun={value['not_run']}")
        lines.extend([f"## {mode}", f"executions: {len(selected)}; statuses: {model_summary['statuses']}",
                      "overall: " + rate_text(model_summary["overall"]),
                      "answer: " + rate_text(model_summary["answer"]),
                      "tool: " + rate_text(model_summary["tool"]),
                      *[f"{key}: {rate_text(value)}" for key, value in model_summary["cohorts"].items()],
                      *[f"expected.{key}: {rate_text(value)}" for key, value in model_summary["categories"].items()],
                      f"need counts: {model_summary['need_counts'] if mode == 'v11' else 'n/a'}",
                      f"needs_manual executions: {model_summary['needs_manual']}",
                      f"fallback executions: {model_summary['fallback_executions']}",
                      f"error executions: {model_summary['error_executions']}; reasons: {model_summary['error_reasons']}",
                      f"requests: {model_summary['request_kinds']}; per execution: {model_summary['request_count_per_execution']}",
                      f"{'synthetic estimated' if plan['transport'] == 'fake' else 'actual'} cost_cny: {model_summary['cost_cny']}",
                      f"actual cost_cny: {model_summary['actual_cost_cny']}",
                      f"{'synthetic ' if plan['transport'] == 'fake' else ''}settled_cny: {model_summary['settled_cny']}; retained_reserve_cny: {model_summary['retained_reserve_cny']}",
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
        for row in rows:
            execution_accounting(row, run_dir, plan)
        if not stop_status and any(r.get("stop_run") or r["status"] == "interrupted" for r in rows):
            stop_status = "not_run_after_accounting_error"
        if (run_dir / "budget.json").exists():
            budget = Budget(run_dir / "budget.json", plan["cap_cny"])
            if Decimal(budget.amounts()["retained_reserve_cny"]) or budget.state.get("stop_reason"):
                stop_status = "not_run_after_accounting_error"
        for entry in plan["executions"]:
            if entry["id"] in done:
                continue
            directory = run_dir / "executions" / entry["id"]
            result_file = directory / "result.json"
            if result_file.exists():
                row = read_json(result_file)
            elif (directory / "started.json").exists():
                # An interrupted paid turn must never be automatically repeated.
                row = {**entry, "status": "interrupted", "stop_run": True}
                if (directory / "requests.json").exists():
                    row["requests"] = read_json(directory / "requests.json")
                stop_status = "not_run_after_accounting_error"
            elif stop_status:
                row = {**entry, "status": stop_status, "request_count": 0}
            else:
                directory.mkdir(parents=True, exist_ok=True)
                write_json(directory / "started.json", entry)
                try:
                    row = executor(plan, entry, directory)
                except Exception as exc:
                    row = {**entry, "status": "not_run_budget" if isinstance(exc, BudgetExceeded) else "error",
                           "error": str(exc) if isinstance(exc, Refused) else type(exc).__name__,
                           "stop_run": isinstance(exc, AccountingUncertain)}
                    if (directory / "requests.json").exists():
                        row["requests"] = read_json(directory / "requests.json")
            if row["status"] != "completed":
                row.pop("score", None)
            row.setdefault("request_count", len(row.get("requests", [])))
            execution_accounting(row, run_dir, plan)
            write_json(result_file, row)
            rows.append(row)
            append_json(journal, row)
            if row["status"] == "not_run_budget":
                stop_status = "not_run_budget"
            elif row.get("stop_run") or row["status"] == "interrupted":
                stop_status = "not_run_after_accounting_error"
        if sha256(QUESTION_SET) != plan["question_sha256"]:
            raise Refused("question_set_changed_after_run")
        summary, markdown = summarize(plan, rows, qset)
        if (run_dir / "budget.json").exists():
            budget = Budget(run_dir / "budget.json", plan["cap_cny"])
            summary["budget"] = budget.state
            markdown += f"\nBudget committed (including unresolved reserves): {budget.committed} CNY\n"
        write_json(run_dir / "summary.json", summary)
        (run_dir / "summary.md").write_text(redact(markdown), encoding="utf-8", newline="\n")
        return summary


def check_worker_isolation(directory: Path) -> None:
    configured = os.environ.get("AMADEUS_DATA_DIR")
    try:
        valid = bool(configured) and Path(configured).expanduser().resolve() == (directory / "data").resolve()
        # This legacy override takes precedence over AMADEUS_DATA_DIR in app.db.
        # The parent never forwards it; a direct worker must not inherit it.
        valid = valid and not os.environ.get("AMADEUS_DB_PATH")
    except (OSError, ValueError, RuntimeError):
        valid = False
    if not valid:
        raise Refused("worker_data_directory_not_isolated")


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
            check_worker_isolation(directory)
            plan = read_json(directory.parents[1] / "plan.json")
            entry = read_json(directory / "started.json")
            check_real_gate(plan["transport"], plan["base_url"], plan["cap_cny"])
            with fake_network_guard() if plan["transport"] == "fake" else nullcontext():
                write_json(directory / "result.json", asyncio.run(product_turn(plan, entry, directory)))
            return 0
        check_real_gate(args.transport, args.base_url, args.cap_cny)
        if args.run_dir is None:
            raise Refused("run_directory_required")
        qset, digest = load_questions()
        plan = make_plan(qset, digest, transport=args.transport, base_url=args.base_url,
                         cap_cny=args.cap_cny, model=args.model, e5_snapshot=args.e5_snapshot)
        print("Budget cap covers this run directory only; a new directory starts at zero.", flush=True)
        summary = run_suite(args.run_dir, plan, qset)
        print(json.dumps({"run_dir": str(args.run_dir.resolve()), "transport": args.transport,
                          "statuses": {mode: row["statuses"] for mode, row in summary["modes"].items()}}, ensure_ascii=False))
        return int(any(row["statuses"].get("error", 0) or row["statuses"].get("interrupted", 0) for row in summary["modes"].values()))
    except Exception as exc:
        print("B5 refused: " + (str(exc) if isinstance(exc, Refused) else type(exc).__name__), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
