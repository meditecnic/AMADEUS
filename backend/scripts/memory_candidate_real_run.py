"""B3 isolated real-provider run entry.

Default B2 fake transport is unchanged. Local-mock serve uses the real
WebSocket → processor → httpx → worker path with a shared persisted budget.
A gated real transport reuses that same serve structure; it stays closed
until AMADEUS_B3_REAL_HTTP=authorized and a non-CLI credential are present.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import portalocker

SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_ROOT = SCRIPT_DIR.parent
WORKTREE_ROOT = BACKEND_ROOT.parent
sys.path.insert(0, str(SCRIPT_DIR))

import memory_candidate_runtime as b2  # noqa: E402

MAX_USER_TURNS = 12
MAX_HTTP_ATTEMPTS = 24
BUDGET_SCHEMA_VERSION = 3
_SERVE_LEASE: Any | None = None
DEFAULT_PROVIDER_ID = "deepseek"
DEFAULT_REQUEST_ID = "deepseek-flash"
BUILTIN_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
OFFICIAL_DEEPSEEK_HOST = "api.deepseek.com"
LOCAL_MOCK_CREDENTIAL = "b3-local-mock-credential"
REAL_HTTP_AUTHORIZED_VALUE = "authorized"
MICROS_PER_CNY = 1_000_000
PEAK_INPUT_CNY_PER_MILLION = 2
PEAK_OUTPUT_CNY_PER_MILLION = 8
PEAK_CACHE_HIT_CNY_PER_MILLION = 0.04
WORST_CASE_INPUT_TOKENS = 1_048_576
WORST_CASE_OUTPUT_TOKENS = 393_216
RESERVE_CNY_PER_COMPLETION = 6
RESERVE_MICROS_PER_COMPLETION = RESERVE_CNY_PER_COMPLETION * MICROS_PER_CNY
CURRENCY_CAP_CNY = 10
CURRENCY_CAP_MICROS = CURRENCY_CAP_CNY * MICROS_PER_CNY
PRICING_PAGE = "https://api-docs.deepseek.com/zh-cn/quick_start/pricing/"
PRICING_PAGE_EN = "https://api-docs.deepseek.com/quick_start/pricing"
FACT_MARKER_PIECE = "故城"
FACT_MARKER_OCARINA = "陶笛"
FACT_MARKER_HARMONICA = "口琴"
EXTRACT_DISPLAY = "长期在学陶笛，练习曲是《故城》"
REVISED_DISPLAY = "长期在学口琴，练习曲是《故城》"
TURN_EXTRACT = (
    "我长期在学陶笛，练习曲是《故城》。这是稳定爱好，请记住。"
)
# Fake/lexical retrieval only injects a fact when the user message is a
# substring of display_text (CJK has no whitespace terms). Keep these probes
# inside the revised fact; they must not mention 口琴 themselves.
TURN_AFTER_EDIT = "练习曲是《故城》"
TURN_AFTER_FORGET = "《故城》"
AFTER_FORGET_CONVERSATION_ID = "aeb35b34-1025-4174-85a5-94b81390b791"
HARMONICA_FACT_ID = "65c12b60-ffd3-491b-827f-1605276d669d"
DEFAULT_REAL_SESSION_ID = "b3-real-owner-20260912"
SUPPLEMENT_PHASE = "supplement-final-turn"
OPEN_INGEST_JOB_STATES = frozenset({"pending", "processing"})
REQUEST_MAX_TOKENS = {
    "chat": 2000,
    "chat_json": 2000,
    "extract": 800,
    "title": 64,
    "translate": 500,
}
COMPILE_BUDGET = {
    "prompt_input_budget": 12000,
    "prompt_output_reserve": 2000,
    "note": (
        "PROMPT_INPUT_BUDGET / PROMPT_OUTPUT_RESERVE are compiler limits, "
        "not provider billing. Request max_tokens below is the HTTP parameter. "
        "They are not the money-reserve basis."
    ),
}
PRICING = {
    "source": PRICING_PAGE,
    "source_en": PRICING_PAGE_EN,
    "model": DEFAULT_REQUEST_ID,
    "basis": (
        "Peak CNY table from the zh-CN official pricing page as recorded by C "
        "on 2026-09-12 and rechecked before real HTTP. All input billed as "
        "cache-miss. Off-peak and cache-hit discounts are not used."
    ),
    "peak_input_cny_per_million": PEAK_INPUT_CNY_PER_MILLION,
    "peak_output_cny_per_million": PEAK_OUTPUT_CNY_PER_MILLION,
    "peak_cache_hit_cny_per_million": PEAK_CACHE_HIT_CNY_PER_MILLION,
    "worst_case_input_tokens": WORST_CASE_INPUT_TOKENS,
    "worst_case_output_tokens": WORST_CASE_OUTPUT_TOKENS,
    "reserve_cny_per_completion": RESERVE_CNY_PER_COMPLETION,
    "reserve_micros_per_completion": RESERVE_MICROS_PER_COMPLETION,
    "currency_cap_cny": CURRENCY_CAP_CNY,
    "catalog_reserve_micros": 0,
    "catalog_reserve_reason": (
        "GET /models is counted against HTTP attempts but is not a billed "
        "completion. A 6 CNY completion reserve on catalog would permanently "
        "block the run after one successful list (no usage). Catalog money "
        "reserve is therefore 0; missing catalog usage is not treated as a "
        "6 CNY hold."
    ),
}
SYNTHETIC_DIALOGUE = [
    {
        "step": 1,
        "channel": "ws chat.send",
        "user": TURN_EXTRACT,
        "expect": (
            "Natural worker extraction via default_memory_completion over local "
            "model HTTP. Local mock emits CREATE at live AUTO_STABLE_CONFIDENCE; "
            "a real model is not required to promote one short-term line."
        ),
        "counts_user_turn": True,
    },
    {
        "step": 2,
        "channel": "GET /api/memory/facts?query=故城",
        "user": None,
        "expect": (
            "Lexical/fake embedding retrieval can hit 故城. Observe only; do not "
            "treat paraphrase recall as accepted under fake embeddings."
        ),
        "counts_user_turn": False,
        "acceptance": "observe_retrieval_only_under_fake_embedding",
    },
    {
        "step": 3,
        "channel": "PATCH /api/memory/facts/{fact_id}",
        "user": None,
        "expect": "Archive user_edit: 陶笛 → 口琴, keep 故城. Not a chat sentence.",
        "counts_user_turn": False,
    },
    {
        "step": 4,
        "channel": "ws chat.send in a new conversation, same owner/worldline/identity",
        "user": TURN_AFTER_EDIT,
        "expect": (
            "Lexical-stable probe 练习曲是《故城》 reaches CORE FACTS with "
            "harmonica from Memory; the previous 陶笛 utterance is not in "
            "this conversation history."
        ),
        "counts_user_turn": True,
    },
    {
        "step": 5,
        "channel": "POST /api/conversations/{id}/forget",
        "user": None,
        "expect": "forget_long_term=true on the source conversation (product erasure).",
        "counts_user_turn": False,
    },
    {
        "step": 6,
        "channel": "ws chat.send in another new conversation",
        "user": TURN_AFTER_FORGET,
        "expect": "CORE FACTS / archive no longer present harmonica as current.",
        "counts_user_turn": True,
    },
]


class BudgetExhausted(RuntimeError):
    """A new provider HTTP attempt or user turn would exceed the run budget."""


class BudgetStateError(RuntimeError):
    """Persisted budget.json is corrupt or incompatible with this run."""


def _as_int(payload: dict[str, Any], key: str, *, minimum: int = 0) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise BudgetStateError(f"budget field {key!r} is not an int")
    if value < minimum:
        raise BudgetStateError(f"budget field {key!r} is below {minimum}")
    return value


def _as_optional_int(payload: dict[str, Any], key: str) -> int | None:
    if key not in payload or payload.get(key) is None:
        return None
    return _as_int(payload, key, minimum=0)


def cny_to_micros(cny: float | int) -> int:
    return int(round(float(cny) * MICROS_PER_CNY))


def micros_to_cny(micros: int) -> float:
    return micros / MICROS_PER_CNY


def peak_usage_micros(prompt_tokens: int | None, completion_tokens: int | None) -> int:
    prompt = max(0, int(prompt_tokens or 0))
    completion = max(0, int(completion_tokens or 0))
    return (
        prompt * PEAK_INPUT_CNY_PER_MILLION
        + completion * PEAK_OUTPUT_CNY_PER_MILLION
    )


def reserve_micros_for_kind(
    kind: str,
    *,
    currency_cap_micros: int | None,
) -> int:
    if currency_cap_micros is None:
        return 0
    if kind == "catalog":
        return 0
    return RESERVE_MICROS_PER_COMPLETION


def official_deepseek_base_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    port = parsed.port
    path = parsed.path or ""
    return (
        parsed.scheme == "https"
        and host == OFFICIAL_DEEPSEEK_HOST
        and port in {None, 443}
        and path.rstrip("/") == ""
        and parsed.username is None
        and parsed.query == ""
        and parsed.fragment == ""
    )


def _budget_lock_path(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".lock")


def _read_budget_payload(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
        payload = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise BudgetStateError(f"budget.json is unreadable: {exc}") from exc
    if not isinstance(payload, dict):
        raise BudgetStateError("budget.json is not an object")
    required = (
        "schema_version",
        "candidate",
        "max_http_attempts",
        "max_user_turns",
        "http_attempts",
        "user_turns",
        "currency_cap_micros",
    )
    missing = [key for key in required if key not in payload]
    if missing:
        raise BudgetStateError(f"budget.json missing {missing}")
    if payload.get("schema_version") != BUDGET_SCHEMA_VERSION:
        raise BudgetStateError(
            f"budget schema {payload.get('schema_version')!r} incompatible "
            f"with {BUDGET_SCHEMA_VERSION}"
        )
    return payload


def _reject_incompatible_budget(
    payload: dict[str, Any],
    *,
    max_http_attempts: int,
    max_user_turns: int,
    candidate: str,
    currency_cap_micros: int | None = None,
) -> None:
    stored_max_http = _as_int(payload, "max_http_attempts", minimum=1)
    stored_max_turns = _as_int(payload, "max_user_turns", minimum=1)
    if stored_max_http != max_http_attempts or stored_max_turns != max_user_turns:
        raise BudgetStateError(
            "budget limits do not match this start: "
            f"stored={stored_max_http}/{stored_max_turns} "
            f"requested={max_http_attempts}/{max_user_turns}"
        )
    stored_candidate = str(payload.get("candidate") or "")
    expected = str(candidate or "")
    if expected and stored_candidate and stored_candidate != expected:
        raise BudgetStateError(
            f"budget candidate mismatch: {stored_candidate} != {expected}"
        )
    stored_cap = _as_optional_int(payload, "currency_cap_micros")
    if stored_cap != currency_cap_micros:
        raise BudgetStateError(
            "budget currency cap mismatch: "
            f"stored={stored_cap} requested={currency_cap_micros}"
        )


def _attempt_is_open(row: dict[str, Any]) -> bool:
    status = str(row.get("status") or "")
    if status in {"recorded", "unknown", "error"}:
        return False
    if row.get("usage_unknown") is True or row.get("usage_unknown") is False:
        return False
    if isinstance(row.get("usage"), dict):
        return False
    return True


def _find_attempt_row(
    attempts: list[dict[str, Any]],
    attempt_id: str | None,
) -> dict[str, Any] | None:
    if attempt_id:
        for row in attempts:
            if str(row.get("attempt_id") or "") == str(attempt_id):
                return row
        return None
    for row in attempts:
        if _attempt_is_open(row):
            return row
    return None


def _try_exclusive_lock(path: Path) -> Any | None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = portalocker.Lock(str(path), timeout=0)
    try:
        lock.acquire()
    except (portalocker.AlreadyLocked, portalocker.LockException):
        return None
    return lock


def _live_run_backend_labels(paths: dict[str, Path]) -> list[str]:
    labels: list[str] = []
    pids: dict[str, Any] = {}
    if paths["pids"].is_file():
        try:
            loaded = b2._read_json(paths["pids"])
            if isinstance(loaded, dict):
                pids = loaded
        except Exception:
            pids = {}
    seen: set[int] = set()
    for key in ("backend_pid",):
        pid = int(pids.get(key) or 0)
        if pid and pid not in seen and b2._pid_running(pid):
            labels.append(f"{key}={pid}")
            seen.add(pid)
    serve_pid_file = paths["root"] / "serve.pid"
    if serve_pid_file.is_file():
        try:
            extra = int(serve_pid_file.read_text(encoding="utf-8").strip() or "0")
        except ValueError:
            extra = 0
        if extra and extra not in seen and b2._pid_running(extra):
            labels.append(f"serve.pid={extra}")
    return labels


def _fail_if_run_active(paths: dict[str, Path]) -> None:
    live = _live_run_backend_labels(paths)
    if live:
        b2._fail(
            "run_already_active",
            "run already has a live backend (" + ", ".join(live) + "); stop first",
        )


class HttpBudget:
    def __init__(
        self,
        *,
        max_http_attempts: int = MAX_HTTP_ATTEMPTS,
        max_user_turns: int = MAX_USER_TURNS,
        path: Path | None = None,
        candidate: str = "",
        currency_cap_micros: int | None = None,
    ) -> None:
        if max_http_attempts <= 0 or max_user_turns <= 0:
            raise ValueError("budgets must be positive")
        if currency_cap_micros is not None and currency_cap_micros <= 0:
            raise ValueError("currency_cap_micros must be positive when set")
        self.max_http_attempts = max_http_attempts
        self.max_user_turns = max_user_turns
        self.currency_cap_micros = currency_cap_micros
        self.http_attempts = 0
        self.user_turns = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.usage_known = 0
        self.usage_unknown = 0
        self.settled_micros = 0
        self.reserved_micros = 0
        self.by_kind: dict[str, int] = {}
        self.attempts: list[dict[str, Any]] = []
        self.rejected: list[dict[str, Any]] = []
        self.candidate = candidate
        self.path = path
        self._lock = threading.Lock()
        if path is not None:
            self._persist_unlocked()

    @classmethod
    def load_or_create(
        cls,
        path: Path,
        *,
        max_http_attempts: int,
        max_user_turns: int,
        candidate: str = "",
        currency_cap_micros: int | None = None,
    ) -> "HttpBudget":
        lock_path = _budget_lock_path(path)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with portalocker.Lock(str(lock_path), timeout=10):
            if path.is_file():
                return cls._from_file_unlocked(
                    path,
                    max_http_attempts=max_http_attempts,
                    max_user_turns=max_user_turns,
                    candidate=candidate,
                    currency_cap_micros=currency_cap_micros,
                )
            return cls(
                max_http_attempts=max_http_attempts,
                max_user_turns=max_user_turns,
                path=path,
                candidate=candidate,
                currency_cap_micros=currency_cap_micros,
            )

    @classmethod
    def load(
        cls,
        path: Path,
        *,
        max_http_attempts: int,
        max_user_turns: int,
        candidate: str = "",
        currency_cap_micros: int | None = None,
    ) -> "HttpBudget":
        lock_path = _budget_lock_path(path)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with portalocker.Lock(str(lock_path), timeout=10):
            return cls._from_file_unlocked(
                path,
                max_http_attempts=max_http_attempts,
                max_user_turns=max_user_turns,
                candidate=candidate,
                currency_cap_micros=currency_cap_micros,
            )

    @classmethod
    def _from_file_unlocked(
        cls,
        path: Path,
        *,
        max_http_attempts: int,
        max_user_turns: int,
        candidate: str = "",
        currency_cap_micros: int | None = None,
    ) -> "HttpBudget":
        payload = _read_budget_payload(path)
        _reject_incompatible_budget(
            payload,
            max_http_attempts=max_http_attempts,
            max_user_turns=max_user_turns,
            candidate=candidate,
            currency_cap_micros=currency_cap_micros,
        )
        budget = cls.__new__(cls)
        budget._lock = threading.Lock()
        budget.path = path
        budget.max_http_attempts = max_http_attempts
        budget.max_user_turns = max_user_turns
        budget.currency_cap_micros = currency_cap_micros
        budget.candidate = str(payload.get("candidate") or candidate or "")
        budget._apply_payload_unlocked(payload)
        return budget

    def _apply_payload_unlocked(self, payload: dict[str, Any]) -> None:
        self.http_attempts = _as_int(payload, "http_attempts")
        self.user_turns = _as_int(payload, "user_turns")
        if self.http_attempts > self.max_http_attempts:
            raise BudgetStateError("http_attempts exceeds max_http_attempts")
        if self.user_turns > self.max_user_turns:
            raise BudgetStateError("user_turns exceeds max_user_turns")
        self.currency_cap_micros = _as_optional_int(payload, "currency_cap_micros")
        self.input_tokens = int(payload.get("input_tokens") or 0)
        self.output_tokens = int(payload.get("output_tokens") or 0)
        self.usage_known = int(payload.get("usage_known") or 0)
        self.usage_unknown = int(payload.get("usage_unknown") or 0)
        by_kind = payload.get("by_kind") or {}
        if not isinstance(by_kind, dict):
            raise BudgetStateError("by_kind is not an object")
        self.by_kind = {
            str(key): int(value)
            for key, value in by_kind.items()
            if isinstance(value, int) and not isinstance(value, bool)
        }
        attempts = payload.get("attempts") or []
        rejected = payload.get("rejected") or []
        if not isinstance(attempts, list) or not isinstance(rejected, list):
            raise BudgetStateError("attempts/rejected must be lists")
        self.attempts = [row for row in attempts if isinstance(row, dict)]
        self.rejected = [row for row in rejected if isinstance(row, dict)]
        self._recompute_money_unlocked()

    def _recompute_money_unlocked(self) -> None:
        settled = 0
        reserved = 0
        for row in self.attempts:
            stored = row.get("settled_micros")
            reserve = int(row.get("reserve_micros") or 0)
            if isinstance(stored, int) and not isinstance(stored, bool):
                settled += stored
            elif reserve:
                reserved += reserve
        self.settled_micros = settled
        self.reserved_micros = reserved

    def _reload_unlocked(self) -> None:
        if self.path is None or not self.path.is_file():
            return
        payload = _read_budget_payload(self.path)
        _reject_incompatible_budget(
            payload,
            max_http_attempts=self.max_http_attempts,
            max_user_turns=self.max_user_turns,
            candidate=self.candidate,
            currency_cap_micros=self.currency_cap_micros,
        )
        stored_candidate = str(payload.get("candidate") or "")
        if stored_candidate:
            self.candidate = stored_candidate
        self._apply_payload_unlocked(payload)

    @contextmanager
    def _locked(self):
        with self._lock:
            if self.path is None:
                yield
                return
            lock_path = _budget_lock_path(self.path)
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            with portalocker.Lock(str(lock_path), timeout=10):
                yield

    def snapshot(self) -> dict[str, Any]:
        with self._locked():
            self._reload_unlocked()
            return self._snapshot_unlocked()

    def _snapshot_unlocked(self) -> dict[str, Any]:
        cap = self.currency_cap_micros
        committed = self.settled_micros + self.reserved_micros
        return {
            "schema_version": BUDGET_SCHEMA_VERSION,
            "candidate": self.candidate,
            "http_attempts": self.http_attempts,
            "max_http_attempts": self.max_http_attempts,
            "user_turns": self.user_turns,
            "max_user_turns": self.max_user_turns,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "usage_known": self.usage_known,
            "usage_unknown": self.usage_unknown,
            "by_kind": dict(self.by_kind),
            "compile_budget": COMPILE_BUDGET,
            "request_max_tokens": REQUEST_MAX_TOKENS,
            "pricing": PRICING,
            "currency_cap": None if cap is None else micros_to_cny(cap),
            "currency_cap_micros": cap,
            "settled_micros": self.settled_micros,
            "reserved_micros": self.reserved_micros,
            "committed_micros": committed,
            "settled_cny": micros_to_cny(self.settled_micros),
            "reserved_cny": micros_to_cny(self.reserved_micros),
            "committed_cny": micros_to_cny(committed),
            "attempts": list(self.attempts),
            "rejected": list(self.rejected[-64:]),
        }

    def _persist_unlocked(self) -> None:
        if self.path is None:
            return
        b2._write_json(self.path, self._snapshot_unlocked())

    def before_http(self, url: str, *, kind: str = "other") -> str:
        with self._locked():
            self._reload_unlocked()
            reserve = reserve_micros_for_kind(
                kind, currency_cap_micros=self.currency_cap_micros
            )
            if self.http_attempts >= self.max_http_attempts:
                reason = (
                    f"budget_http_exhausted before {url}: "
                    f"{self.http_attempts}/{self.max_http_attempts}"
                )
                self.rejected.append(
                    {
                        "at": b2._utc_now(),
                        "kind": kind,
                        "url": url,
                        "reason": "budget_http_exhausted",
                    }
                )
                self._persist_unlocked()
                raise BudgetExhausted(reason)
            if self.currency_cap_micros is not None:
                committed = self.settled_micros + self.reserved_micros
                if committed + reserve > self.currency_cap_micros:
                    reason = (
                        f"budget_currency_exhausted before {url}: "
                        f"settled={self.settled_micros} reserved={self.reserved_micros} "
                        f"need={reserve} cap={self.currency_cap_micros}"
                    )
                    self.rejected.append(
                        {
                            "at": b2._utc_now(),
                            "kind": kind,
                            "url": url,
                            "reason": "budget_currency_exhausted",
                            "settled_micros": self.settled_micros,
                            "reserved_micros": self.reserved_micros,
                            "need_micros": reserve,
                        }
                    )
                    self._persist_unlocked()
                    raise BudgetExhausted(reason)
            self.http_attempts += 1
            self.by_kind[kind] = int(self.by_kind.get(kind) or 0) + 1
            attempt_id = str(uuid.uuid4())
            self.attempts.append(
                {
                    "attempt_id": attempt_id,
                    "at": b2._utc_now(),
                    "kind": kind,
                    "url": url,
                    "usage": None,
                    "usage_unknown": None,
                    "status": "occupied",
                    "reserve_micros": reserve,
                    "settled_micros": None,
                }
            )
            self._recompute_money_unlocked()
            self._persist_unlocked()
            return attempt_id

    def before_user_turn(self) -> None:
        with self._locked():
            self._reload_unlocked()
            if self.user_turns >= self.max_user_turns:
                reason = (
                    f"budget_turns_exhausted: {self.user_turns}/{self.max_user_turns}"
                )
                self.rejected.append(
                    {
                        "at": b2._utc_now(),
                        "kind": "user_turn",
                        "reason": "budget_turns_exhausted",
                    }
                )
                self._persist_unlocked()
                raise BudgetExhausted(reason)
            self.user_turns += 1
            self._persist_unlocked()

    def record_usage(
        self,
        payload: dict[str, Any] | None,
        *,
        attempt_id: str | None = None,
        error: str | None = None,
    ) -> None:
        usage = payload.get("usage") if isinstance(payload, dict) else None
        with self._locked():
            self._reload_unlocked()
            row = _find_attempt_row(self.attempts, attempt_id)
            if row is None:
                self._persist_unlocked()
                return
            if str(row.get("status") or "") not in {"", "occupied"}:
                self._persist_unlocked()
                return
            prompt = None
            completion = None
            if isinstance(usage, dict):
                if isinstance(usage.get("prompt_tokens"), int) and not isinstance(
                    usage.get("prompt_tokens"), bool
                ):
                    prompt = usage["prompt_tokens"]
                if isinstance(usage.get("completion_tokens"), int) and not isinstance(
                    usage.get("completion_tokens"), bool
                ):
                    completion = usage["completion_tokens"]
            if error:
                self.usage_unknown += 1
                row["usage"] = None
                row["usage_unknown"] = True
                row["status"] = "error"
                row["error"] = str(error)
                row["settled_micros"] = None
            elif prompt is None and completion is None:
                self.usage_unknown += 1
                row["usage"] = None
                row["usage_unknown"] = True
                row["status"] = "unknown"
                row["settled_micros"] = None
            else:
                self.usage_known += 1
                if prompt is not None and prompt >= 0:
                    self.input_tokens += prompt
                if completion is not None and completion >= 0:
                    self.output_tokens += completion
                cost = peak_usage_micros(prompt, completion)
                row["usage"] = {
                    "prompt_tokens": prompt,
                    "completion_tokens": completion,
                }
                row["usage_unknown"] = False
                row["status"] = "recorded"
                row["settled_micros"] = cost
                row["peak_cost_micros"] = cost
            self._recompute_money_unlocked()
            self._persist_unlocked()


def allowed_hosts_for(base_url: str, *, loopback: bool = True) -> set[str]:
    host = (urlparse(base_url).hostname or "").lower()
    hosts: set[str] = set()
    if loopback:
        hosts.update({"127.0.0.1", "localhost", "::1"})
    if host:
        hosts.add(host)
    return hosts


def load_credential_from_env(
    environ: dict[str, str] | None = None,
) -> str | None:
    """Read a B3 credential without scanning other apps or printing the secret."""
    env = environ if environ is not None else dict(os.environ)
    file_path = (env.get("AMADEUS_B3_CREDENTIAL_FILE") or "").strip()
    if file_path:
        text = Path(file_path).read_text(encoding="utf-8").strip()
        return text or None
    value = (env.get("AMADEUS_B3_PROVIDER_KEY") or "").strip()
    return value or None


def redact_headers(headers: dict[str, Any]) -> dict[str, Any]:
    redacted = {}
    for key, value in headers.items():
        lowered = str(key).lower()
        if lowered in {"authorization", "api-key", "x-api-key"}:
            redacted[key] = "<redacted>"
        else:
            redacted[key] = value
    return redacted


def classify_provider_request(method: str, url: str, payload: dict[str, Any] | None) -> str:
    path = urlparse(url).path.rstrip("/")
    if path.endswith("/models"):
        return "catalog"
    if not path.endswith("/chat/completions"):
        return "other"
    body = payload or {}
    if body.get("stream"):
        return "chat"
    messages = body.get("messages") or []
    system = ""
    blob = ""
    if messages:
        system = str(messages[0].get("content") or "")
        blob = json.dumps(messages, ensure_ascii=False)
    if "extract durable user memory" in system:
        return "extract"
    if "生成简洁" in system or "标题" in system:
        return "title"
    if "<source>" in blob or "翻译" in system:
        return "translate"
    return "chat_json"


def _request_payload(request: Any) -> dict[str, Any] | None:
    raw = getattr(request, "content", b"") or b""
    if not raw:
        return None
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
        return None
    return payload if isinstance(payload, dict) else None


def _apply_request_max_tokens(payload: dict[str, Any] | None, kind: str) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return payload
    limit = REQUEST_MAX_TOKENS.get(kind)
    if limit is None:
        return payload
    updated = dict(payload)
    updated["max_tokens"] = limit
    return updated


def _rewrite_request_payload(request: Any, payload: dict[str, Any] | None) -> Any:
    import httpx

    if not isinstance(payload, dict):
        return request
    encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = httpx.Headers(request.headers)
    headers["content-length"] = str(len(encoded))
    headers.pop("expect", None)
    return httpx.Request(
        request.method,
        request.url,
        headers=headers,
        content=encoded,
        extensions=request.extensions,
    )


def _without_expect_header(request: Any) -> Any:
    """Simple local mock servers hang on HTTP Expect: 100-continue."""
    import httpx

    headers = httpx.Headers(request.headers)
    if "expect" not in headers:
        return request
    headers.pop("expect", None)
    return httpx.Request(
        request.method,
        request.url,
        headers=headers,
        content=request.content,
        extensions=request.extensions,
    )


def _core_facts_from_messages(messages: list[Any]) -> str:
    if not messages:
        return ""
    system = str(messages[0].get("content") or "")
    return b2._core_facts_block(system)


def _extract_payload(user_text: str, source_message_id: int) -> dict[str, Any]:
    from app.services.memory_v11.contracts import AUTO_STABLE_CONFIDENCE

    confidence = 0.96
    assert AUTO_STABLE_CONFIDENCE == 0.85
    assert confidence >= AUTO_STABLE_CONFIDENCE
    if FACT_MARKER_OCARINA in user_text and FACT_MARKER_PIECE in user_text:
        return {
            "observations": [
                {
                    "observation_ref": "o1",
                    "source_message_ids": [int(source_message_id)],
                    "display_text": EXTRACT_DISPLAY,
                    "semantic": {
                        "subject": "user",
                        "predicate": "likes",
                        "object": {"name": FACT_MARKER_PIECE},
                    },
                    "evidence_kind": "direct_user",
                    "memory_class": "stable_candidate",
                    "confidence": confidence,
                    "topic_label": "爱好",
                }
            ],
            "operations": [
                {
                    "op": "CREATE",
                    "observation_ref": "o1",
                    "target_fact_id": None,
                    "expected_version": None,
                    "fact_text": EXTRACT_DISPLAY,
                    "reason_code": "direct_durable_statement",
                }
            ],
        }
    return {"observations": [], "operations": []}


class LocalProviderHandler(BaseHTTPRequestHandler):
    inject: str = ""
    request_ids: list[str]
    attempts: list[str]
    capture_log: Path | None = None
    _extract_failures_left: int = 0
    _lock = threading.Lock()

    def handle_expect_100(self) -> bool:
        self.send_response_only(100)
        self.end_headers()
        return True

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            payload = {}
        return payload if isinstance(payload, dict) else {}

    def _record(self, method: str, path: str, status: int, kind: str) -> None:
        row = {
            "at": b2._utc_now(),
            "method": method,
            "path": path,
            "status": status,
            "kind": kind,
        }
        with self._lock:
            self.attempts.append(f"{method} {path} {status}")
        if self.capture_log is not None:
            b2._append_jsonl(self.capture_log, row)

    def _send(self, status: int, payload: dict[str, Any] | bytes, *, content_type: str) -> None:
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        kind = classify_provider_request("GET", self.path, None)
        if self.path.rstrip("/").endswith("/models"):
            self._record("GET", self.path, 200, kind)
            self._send(
                200,
                {
                    "object": "list",
                    "data": [
                        {"id": "deepseek-flash", "object": "model", "owned_by": "deepseek"},
                        {"id": "deepseek-v4-flash", "object": "model", "owned_by": "deepseek"},
                    ],
                },
                content_type="application/json",
            )
            return
        self._record("GET", self.path, 404, kind)
        self._send(404, {"error": "not_found"}, content_type="application/json")

    def do_POST(self) -> None:  # noqa: N802
        payload = self._read_json()
        kind = classify_provider_request("POST", self.path, payload)
        model_id = str(payload.get("model") or "")
        self.request_ids.append(model_id)
        if self.inject == "timeout":
            self._record("POST", self.path, 0, kind)
            self.close_connection = True
            return
        if self.inject == "auth_fail":
            self._record("POST", self.path, 401, kind)
            self._send(
                401,
                {"error": {"message": "invalid_api_key"}},
                content_type="application/json",
            )
            return
        if not self.path.rstrip("/").endswith("/chat/completions"):
            self._record("POST", self.path, 404, kind)
            self._send(404, {"error": "not_found"}, content_type="application/json")
            return
        fail_extract = False
        if kind == "extract":
            with self._lock:
                if LocalProviderHandler._extract_failures_left > 0:
                    LocalProviderHandler._extract_failures_left -= 1
                    fail_extract = True
        if fail_extract:
            self._record("POST", self.path, 500, kind)
            self._send(
                500,
                {"error": {"message": "transient_extract_failure"}},
                content_type="application/json",
            )
            return
        usage = {
            "prompt_tokens": 16 if kind != "extract" else 24,
            "completion_tokens": 8 if kind != "extract" else 12,
            "total_tokens": 24 if kind != "extract" else 36,
        }
        if payload.get("stream"):
            chunk = {
                "id": "local-mock",
                "model": model_id or DEFAULT_REQUEST_ID,
                "choices": [
                    {"delta": {"content": b2.REPLY_JA}, "index": 0, "finish_reason": None}
                ],
            }
            usage_chunk = {
                "id": "local-mock",
                "model": model_id or DEFAULT_REQUEST_ID,
                "choices": [],
                "usage": usage,
            }
            body = (
                f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
                f"data: {json.dumps(usage_chunk, ensure_ascii=False)}\n\n"
                "data: [DONE]\n\n"
            ).encode("utf-8")
            self._record("POST", self.path, 200, kind)
            self._send(200, body, content_type="text/event-stream")
            return
        messages = payload.get("messages") or []
        system = ""
        user = ""
        if messages:
            system = str(messages[0].get("content") or "")
            user = str(messages[-1].get("content") or "")
        if kind == "extract":
            source_id = 0
            try:
                parsed = json.loads(user)
                source_id = int(parsed.get("source_message_id") or 0)
                user_text = str(parsed.get("user_text") or "")
            except (json.JSONDecodeError, TypeError, ValueError):
                user_text = user
            content = json.dumps(
                _extract_payload(user_text, source_id),
                ensure_ascii=False,
            )
        elif kind == "title":
            content = json.dumps({"title": "陶笛与故城"}, ensure_ascii=False)
        elif kind == "translate":
            content = "好的。"
        else:
            content = json.dumps({"ok": True, "echo": user[:80]}, ensure_ascii=False)
        self._record("POST", self.path, 200, kind)
        self._send(
            200,
            {
                "id": "local-mock",
                "model": model_id or DEFAULT_REQUEST_ID,
                "choices": [{"message": {"role": "assistant", "content": content}}],
                "usage": usage,
            },
            content_type="application/json",
        )


def start_local_provider(
    *,
    inject: str = "",
    capture_log: Path | None = None,
) -> tuple[ThreadingHTTPServer, str]:
    LocalProviderHandler.inject = inject
    LocalProviderHandler.request_ids = []
    LocalProviderHandler.attempts = []
    LocalProviderHandler.capture_log = capture_log
    LocalProviderHandler._extract_failures_left = 1 if inject == "fail_first_extract" else 0
    server = ThreadingHTTPServer(("127.0.0.1", 0), LocalProviderHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    return server, f"http://{host}:{port}"


class _UsageTrackingResponse:
    def __init__(
        self,
        inner: Any,
        budget: HttpBudget,
        kind: str,
        *,
        attempt_id: str,
    ) -> None:
        object.__setattr__(self, "_inner", inner)
        object.__setattr__(self, "_budget", budget)
        object.__setattr__(self, "_kind", kind)
        object.__setattr__(self, "_attempt_id", attempt_id)
        object.__setattr__(self, "_saw_usage", False)
        object.__setattr__(self, "_finished", False)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def _ingest_line(self, line: str) -> None:
        if not line.startswith("data: "):
            return
        data = line[6:].strip()
        if not data or data == "[DONE]":
            return
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            return
        if isinstance(payload, dict) and isinstance(payload.get("usage"), dict):
            object.__setattr__(self, "_saw_usage", True)
            self._budget.record_usage(payload, attempt_id=self._attempt_id)

    def _finish(self, *, error: str | None = None) -> None:
        if self._finished:
            return
        object.__setattr__(self, "_finished", True)
        if self._saw_usage:
            return
        self._budget.record_usage(
            None,
            attempt_id=self._attempt_id,
            error=error,
        )

    async def aiter_lines(self):
        try:
            async for line in self._inner.aiter_lines():
                self._ingest_line(line)
                yield line
            self._finish()
        except Exception as exc:
            self._finish(error=type(exc).__name__)
            raise

    def iter_lines(self, *args, **kwargs):
        try:
            for line in self._inner.iter_lines(*args, **kwargs):
                text = line.decode("utf-8", "replace") if isinstance(line, bytes) else str(line)
                self._ingest_line(text)
                yield line
            self._finish()
        except Exception as exc:
            self._finish(error=type(exc).__name__)
            raise


def install_allowlisted_httpx(
    *,
    budget: HttpBudget,
    allowed_hosts: set[str],
    capture_dir: Path,
    original_async_send: Any,
    original_sync_send: Any,
    require_https: bool = False,
) -> dict[str, Any]:
    import httpx

    outbound_log = capture_dir / "outbound_http.jsonl"
    chat_log = capture_dir / "provider_chat.jsonl"
    blocked: list[str] = []

    original_async_post = httpx.AsyncClient.post
    original_async_stream = httpx.AsyncClient.stream
    original_get = httpx.get
    original_request = httpx.request

    def _limit_kwargs(method: str, url: str, kwargs: dict[str, Any]) -> dict[str, Any]:
        payload = kwargs.get("json")
        if not isinstance(payload, dict):
            return kwargs
        kind = classify_provider_request(method, url, payload)
        limited = _apply_request_max_tokens(payload, kind)
        updated = dict(kwargs)
        updated["json"] = limited
        return updated

    async def limited_async_post(self, url, *args, **kwargs):
        return await original_async_post(
            self, url, *args, **_limit_kwargs("POST", str(url), kwargs)
        )

    def limited_async_stream(self, method, url, *args, **kwargs):
        return original_async_stream(
            self, method, url, *args, **_limit_kwargs(str(method), str(url), kwargs)
        )

    httpx.AsyncClient.post = limited_async_post  # type: ignore[method-assign]
    httpx.AsyncClient.stream = limited_async_stream  # type: ignore[method-assign]

    def _prepare(request: Any) -> tuple[Any, str, dict[str, Any] | None]:
        payload = _request_payload(request)
        kind = classify_provider_request(request.method, str(request.url), payload)
        limited = _apply_request_max_tokens(payload, kind)
        if limited is not payload:
            request = _rewrite_request_payload(request, limited)
        return request, kind, limited

    def _guard(kind: str, method: str, url: str, payload: dict[str, Any] | None) -> None:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        messages = (payload or {}).get("messages") if isinstance(payload, dict) else None
        core_facts = ""
        if kind == "chat" and isinstance(messages, list):
            core_facts = _core_facts_from_messages(messages)
            b2._append_jsonl(
                chat_log,
                {
                    "at": b2._utc_now(),
                    "model": (payload or {}).get("model"),
                    "max_tokens": (payload or {}).get("max_tokens"),
                    "core_facts": core_facts,
                    "history_roles": [
                        str(item.get("role"))
                        for item in messages
                        if isinstance(item, dict)
                    ],
                    "user_tail": str(messages[-1].get("content") or "")[:200]
                    if messages
                    else "",
                    "contains_extract_utterance": TURN_EXTRACT
                    in json.dumps(messages, ensure_ascii=False),
                },
            )
        b2._append_jsonl(
            outbound_log,
            {
                "at": b2._utc_now(),
                "kind": kind,
                "method": method,
                "url": url,
                "host": host,
                "scheme": parsed.scheme,
                "model": (payload or {}).get("model") if payload else None,
                "max_tokens": (payload or {}).get("max_tokens") if payload else None,
                "stream": bool((payload or {}).get("stream")) if payload else False,
            },
        )
        if require_https and parsed.scheme != "https":
            blocked.append(url)
            raise RuntimeError(f"blocked non-https HTTP: {url}")
        if host not in allowed_hosts:
            blocked.append(url)
            raise RuntimeError(f"blocked non-allowlisted HTTP: {url}")
        if kind != "catalog":
            model = (payload or {}).get("model") if isinstance(payload, dict) else None
            if model != DEFAULT_REQUEST_ID:
                blocked.append(url)
                raise RuntimeError(f"blocked unauthorized completion model: {model}")
        return budget.before_http(url, kind=kind)

    def _reject_redirect(response: Any) -> None:
        status = int(getattr(response, "status_code", 0) or 0)
        if status not in {301, 302, 303, 307, 308}:
            return
        headers = getattr(response, "headers", {}) or {}
        location = headers.get("location") or headers.get("Location") or ""
        blocked.append(str(location) or f"status:{status}")
        raise RuntimeError(f"blocked redirect: {location or status}")

    def _record_send_result(
        *,
        attempt_id: str | None,
        payload: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        if not attempt_id:
            return
        budget.record_usage(payload, attempt_id=attempt_id, error=error)

    def guarded_get(url, *args, **kwargs):
        parsed = urlparse(str(url))
        host = (parsed.hostname or "").lower()
        if require_https and parsed.scheme != "https":
            blocked.append(str(url))
            raise RuntimeError(f"blocked non-https HTTP: {url}")
        if host not in allowed_hosts:
            blocked.append(str(url))
            raise RuntimeError(f"blocked non-allowlisted HTTP: {url}")
        return original_get(url, *args, **kwargs)

    def guarded_request(method, url, *args, **kwargs):
        parsed = urlparse(str(url))
        host = (parsed.hostname or "").lower()
        if require_https and parsed.scheme != "https":
            blocked.append(str(url))
            raise RuntimeError(f"blocked non-https HTTP: {url}")
        if host not in allowed_hosts:
            blocked.append(str(url))
            raise RuntimeError(f"blocked non-allowlisted HTTP: {url}")
        return original_request(method, url, *args, **kwargs)

    async def guarded_async_send(self, request, *args, **kwargs):
        attempt_id = None
        try:
            request, kind, payload = _prepare(request)
            request = _without_expect_header(request)
            attempt_id = _guard(kind, request.method, str(request.url), payload)
            send_kwargs = dict(kwargs)
            send_kwargs["follow_redirects"] = False
            stream = bool(send_kwargs.get("stream"))
            response = await original_async_send(self, request, *args, **send_kwargs)
            _reject_redirect(response)
            if stream:
                return _UsageTrackingResponse(
                    response, budget, kind, attempt_id=attempt_id
                )
            try:
                data = response.json()
            except Exception:
                data = None
            _record_send_result(
                attempt_id=attempt_id,
                payload=data if isinstance(data, dict) else None,
            )
            return response
        except BudgetExhausted:
            raise
        except Exception as exc:
            _record_send_result(attempt_id=attempt_id, error=type(exc).__name__)
            raise

    def guarded_sync_send(self, request, *args, **kwargs):
        attempt_id = None
        try:
            request, kind, payload = _prepare(request)
            request = _without_expect_header(request)
            attempt_id = _guard(kind, request.method, str(request.url), payload)
            send_kwargs = dict(kwargs)
            send_kwargs["follow_redirects"] = False
            stream = bool(send_kwargs.get("stream"))
            response = original_sync_send(self, request, *args, **send_kwargs)
            _reject_redirect(response)
            if stream:
                return _UsageTrackingResponse(
                    response, budget, kind, attempt_id=attempt_id
                )
            try:
                data = response.json()
            except Exception:
                data = None
            _record_send_result(
                attempt_id=attempt_id,
                payload=data if isinstance(data, dict) else None,
            )
            return response
        except BudgetExhausted:
            raise
        except Exception as exc:
            _record_send_result(attempt_id=attempt_id, error=type(exc).__name__)
            raise

    httpx.AsyncClient.send = guarded_async_send  # type: ignore[method-assign]
    httpx.Client.send = guarded_sync_send  # type: ignore[method-assign]
    httpx.get = guarded_get  # type: ignore[assignment]
    httpx.request = guarded_request  # type: ignore[assignment]
    return {"blocked": blocked, "outbound_log": str(outbound_log)}


def install_processor_budget(budget: HttpBudget) -> None:
    from app.routers import chat_ws

    original = chat_ws.processor_loop

    async def guarded_processor_loop(
        session, user_msg, send_queue, epoch, start_history_epoch
    ):
        try:
            budget.before_user_turn()
        except BudgetExhausted as exc:
            session.is_busy = False
            await send_queue.put(
                (
                    epoch,
                    {
                        "type": "error",
                        "code": "budget_turns_exhausted",
                        "message": str(exc),
                    },
                )
            )
            return
        return await original(
            session, user_msg, send_queue, epoch, start_history_epoch
        )

    chat_ws.processor_loop = guarded_processor_loop  # type: ignore[method-assign]


def plan_payload() -> dict[str, Any]:
    return {
        "provider_id": DEFAULT_PROVIDER_ID,
        "request_id": DEFAULT_REQUEST_ID,
        "builtin_base_url": BUILTIN_DEEPSEEK_BASE_URL,
        "base_url_source": (
            "Isolated real-run process is pinned to https://api.deepseek.com. "
            "Other AMADEUS_B3_BASE_URL values are rejected. Cross-origin "
            "redirects are refused."
        ),
        "max_user_turns": MAX_USER_TURNS,
        "max_http_attempts": MAX_HTTP_ATTEMPTS,
        "compile_budget": COMPILE_BUDGET,
        "request_max_tokens": REQUEST_MAX_TOKENS,
        "pricing": PRICING,
        "currency_cap": CURRENCY_CAP_CNY,
        "currency_cap_cny": CURRENCY_CAP_CNY,
        "currency_cap_micros": CURRENCY_CAP_MICROS,
        "currency_cap_reason": (
            "Authorized 2026-09-12 real run: 10 CNY peak-miss ledger on the "
            "unique run root. Count budget (12 turns / 24 HTTP) is separate. "
            "Each billed completion reserves 6 CNY until trusted usage; catalog "
            "GET /models reserves 0 CNY."
        ),
        "credential": {
            "cli": False,
            "env": "AMADEUS_B3_PROVIDER_KEY",
            "file": "AMADEUS_B3_CREDENTIAL_FILE",
            "project_store": "WindowsCredentialStore.get('deepseek') in start-real parent only",
            "production_dotenv": False,
        },
        "synthetic_dialogue": SYNTHETIC_DIALOGUE,
        "embedding": "fake/lexical unless C separately enables real embeddings",
        "semantic_recall": (
            "Paraphrase recall is observe-only under fake embeddings. Enable a "
            "separate real-embedding run before accepting meaning-only retrieval."
        ),
        "real_http_authorized": False,
        "real_http_gate": {
            "env": "AMADEUS_B3_REAL_HTTP",
            "value": REAL_HTTP_AUTHORIZED_VALUE,
            "serve": "start-real / local-serve --transport real",
            "still_closed_by_default": True,
        },
        "unique_real_run": (
            ".scratch/memory-runtime-b3/real-runs/deepseek-official-20260912"
        ),
        "future_real_needs": {
            "config_or_auth_only": [
                "AMADEUS_B3_REAL_HTTP=authorized",
                "project store get('deepseek') or AMADEUS_B3_PROVIDER_KEY / FILE",
            ],
            "still_missing": [
                "real embedding (optional, separate authorization)",
                "account invoice for this task (report peak-price usage separately)",
            ],
        },
    }


def cmd_print_plan(args: argparse.Namespace) -> None:
    text = json.dumps(plan_payload(), ensure_ascii=False, indent=2)
    output = str(getattr(args, "output", "") or "").strip()
    if output:
        Path(output).write_text(text + "\n", encoding="utf-8")
    print(text)


def cmd_budget_probe(args: argparse.Namespace) -> None:
    import httpx

    run_root = b2.deep_resolve(Path(args.run_root))
    paths = b2.run_paths(run_root)
    paths["captures"].mkdir(parents=True, exist_ok=True)
    budget = HttpBudget(
        max_http_attempts=int(args.max_http_attempts),
        path=paths["captures"] / "budget.json",
        candidate="budget-probe",
    )
    server, base_url = start_local_provider(inject=args.inject)
    original_async = httpx.AsyncClient.send
    original_sync = httpx.Client.send
    try:
        install_allowlisted_httpx(
            budget=budget,
            allowed_hosts=allowed_hosts_for(base_url),
            capture_dir=paths["captures"],
            original_async_send=original_async,
            original_sync_send=original_sync,
        )
        report: dict[str, Any] = {
            "ok": True,
            "base_url": base_url,
            "models": None,
            "budget_stop": None,
            "blocked_foreign": None,
            "auth_fail_no_retry": None,
            "request_ids": [],
        }
        with httpx.Client(timeout=5.0) as client:
            models = client.get(f"{base_url}/models")
            report["models"] = models.json()
            try:
                client.get("https://example.com/")
                report["blocked_foreign"] = False
            except RuntimeError as exc:
                report["blocked_foreign"] = str(exc).startswith("blocked ")
            if args.inject == "auth_fail":
                failed = client.post(
                    f"{base_url}/chat/completions",
                    json={"model": DEFAULT_REQUEST_ID, "messages": []},
                )
                report["auth_fail_no_retry"] = failed.status_code == 401
            remaining = budget.max_http_attempts - budget.http_attempts
            for _ in range(remaining):
                client.get(f"{base_url}/models")
            try:
                client.get(f"{base_url}/models")
                report["budget_stop"] = False
            except BudgetExhausted as exc:
                report["budget_stop"] = str(exc)
        report["http_attempts"] = budget.http_attempts
        report["usage_unknown"] = budget.usage_unknown
        report["request_ids"] = list(LocalProviderHandler.request_ids)
        report["mock_attempts"] = list(LocalProviderHandler.attempts)
        if not report["blocked_foreign"] or not report["budget_stop"]:
            report["ok"] = False
        b2._write_json(paths["captures"] / "budget_probe.json", report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if not report["ok"]:
            raise SystemExit(2)
    finally:
        server.shutdown()
        server.server_close()
        httpx.AsyncClient.send = original_async  # type: ignore[method-assign]
        httpx.Client.send = original_sync  # type: ignore[method-assign]


def cmd_money_probe(args: argparse.Namespace) -> None:
    import httpx

    run_root = b2.deep_resolve(Path(args.run_root))
    paths = b2.run_paths(run_root)
    paths["captures"].mkdir(parents=True, exist_ok=True)
    cap = CURRENCY_CAP_MICROS
    reserve = RESERVE_MICROS_PER_COMPLETION
    report: dict[str, Any] = {"ok": True, "command": "money-probe", "cases": {}}

    exhaust = HttpBudget(
        max_http_attempts=8,
        max_user_turns=8,
        currency_cap_micros=reserve,
    )
    first = exhaust.before_http("https://api.deepseek.com/chat/completions", kind="chat")
    exhaust_blocked = False
    try:
        exhaust.before_http("https://api.deepseek.com/chat/completions", kind="chat")
    except BudgetExhausted as exc:
        exhaust_blocked = "budget_currency_exhausted" in str(exc)
    report["cases"]["exhaustion"] = {
        "first_attempt": first,
        "second_blocked": exhaust_blocked,
        "reserved_micros": exhaust.reserved_micros,
        "committed_micros": exhaust.settled_micros + exhaust.reserved_micros,
    }
    if not exhaust_blocked or exhaust.reserved_micros != reserve:
        report["ok"] = False

    concurrent = HttpBudget(
        max_http_attempts=20,
        max_user_turns=20,
        currency_cap_micros=reserve,
    )
    errors: list[str] = []

    def _occupy() -> None:
        try:
            concurrent.before_http("https://api.deepseek.com/chat/completions", kind="chat")
        except BudgetExhausted:
            errors.append("exhausted")

    threads = [threading.Thread(target=_occupy) for _ in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    report["cases"]["concurrency"] = {
        "accepted": concurrent.http_attempts,
        "rejected": len(errors),
        "reserved_micros": concurrent.reserved_micros,
    }
    if concurrent.http_attempts != 1 or len(errors) != 11:
        report["ok"] = False

    dup = HttpBudget(max_http_attempts=4, max_user_turns=4, currency_cap_micros=cap)
    dup_id = dup.before_http("https://api.deepseek.com/chat/completions", kind="chat")
    dup.record_usage(
        {"usage": {"prompt_tokens": 1000, "completion_tokens": 100}},
        attempt_id=dup_id,
    )
    after_first = dup.settled_micros
    dup.record_usage(
        {"usage": {"prompt_tokens": 999999, "completion_tokens": 999999}},
        attempt_id=dup_id,
    )
    expected_dup = peak_usage_micros(1000, 100)
    report["cases"]["duplicate_callback"] = {
        "settled_after_first": after_first,
        "settled_after_duplicate": dup.settled_micros,
        "expected": expected_dup,
        "reserved_after": dup.reserved_micros,
    }
    if dup.settled_micros != expected_dup or dup.reserved_micros != 0:
        report["ok"] = False

    hold_path = paths["captures"] / "money_hold_budget.json"
    if hold_path.exists():
        hold_path.unlink()
    hold = HttpBudget.load_or_create(
        hold_path,
        max_http_attempts=8,
        max_user_turns=8,
        candidate="money-hold",
        currency_cap_micros=cap,
    )
    hold_id = hold.before_http("https://api.deepseek.com/chat/completions", kind="chat")
    hold.record_usage({"object": "chat.completion"}, attempt_id=hold_id)
    hold_blocked = False
    try:
        hold.before_http("https://api.deepseek.com/chat/completions", kind="extract")
    except BudgetExhausted as exc:
        hold_blocked = "budget_currency_exhausted" in str(exc)
    report["cases"]["no_usage_hold"] = {
        "status": hold.snapshot()["attempts"][0]["status"],
        "reserved_micros": hold.reserved_micros,
        "usage_unknown": hold.usage_unknown,
        "second_blocked": hold_blocked,
    }
    if not hold_blocked or hold.reserved_micros != reserve or hold.usage_unknown != 1:
        report["ok"] = False

    restarted = HttpBudget.load_or_create(
        hold_path,
        max_http_attempts=8,
        max_user_turns=8,
        candidate="money-hold",
        currency_cap_micros=cap,
    )
    restart_blocked = False
    try:
        restarted.before_http("https://api.deepseek.com/chat/completions", kind="title")
    except BudgetExhausted as exc:
        restart_blocked = "budget_currency_exhausted" in str(exc)
    report["cases"]["restart_accumulation"] = {
        "reserved_micros": restarted.reserved_micros,
        "http_attempts": restarted.http_attempts,
        "second_blocked": restart_blocked,
        "schema_version": restarted.snapshot()["schema_version"],
    }
    if not restart_blocked or restarted.reserved_micros != reserve:
        report["ok"] = False

    catalog = HttpBudget(
        max_http_attempts=4,
        max_user_turns=4,
        currency_cap_micros=reserve,
    )
    catalog.before_http("https://api.deepseek.com/models", kind="catalog")
    catalog_second_ok = True
    try:
        catalog.before_http("https://api.deepseek.com/chat/completions", kind="chat")
    except BudgetExhausted:
        catalog_second_ok = False
    report["cases"]["catalog_zero_reserve"] = {
        "http_attempts": catalog.http_attempts,
        "reserved_after_catalog": catalog.reserved_micros,
        "chat_accepted_after_catalog": catalog_second_ok,
    }
    if catalog.reserved_micros != reserve or not catalog_second_ok:
        report["ok"] = False

    server, base_url = start_local_provider()
    original_async = httpx.AsyncClient.send
    original_sync = httpx.Client.send
    param_budget = HttpBudget(
        max_http_attempts=4,
        max_user_turns=4,
        currency_cap_micros=cap,
        path=paths["captures"] / "money_params_budget.json",
    )
    try:
        install_allowlisted_httpx(
            budget=param_budget,
            allowed_hosts=allowed_hosts_for(base_url),
            capture_dir=paths["captures"],
            original_async_send=original_async,
            original_sync_send=original_sync,
        )
        with httpx.Client(timeout=5.0) as client:
            client.post(
                f"{base_url}/chat/completions",
                json={
                    "model": DEFAULT_REQUEST_ID,
                    "messages": [{"role": "user", "content": "ping"}],
                    "stream": False,
                },
            )
        outbound = []
        log = paths["captures"] / "outbound_http.jsonl"
        if log.is_file():
            outbound = [
                json.loads(line)
                for line in log.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        chat_rows = [
            row
            for row in outbound
            if row.get("kind") in {"chat", "chat_json", "extract", "title", "translate"}
        ]
        last = chat_rows[-1] if chat_rows else {}
        max_tokens = last.get("max_tokens")
        expected_cost = peak_usage_micros(16, 8)
        report["cases"]["request_params"] = {
            "max_tokens": max_tokens,
            "expected_max_tokens": REQUEST_MAX_TOKENS["chat"],
            "settled_micros": param_budget.settled_micros,
            "expected_settled_micros": expected_cost,
            "reserved_after_usage": param_budget.reserved_micros,
            "model": last.get("model"),
            "kind": last.get("kind"),
        }
        if max_tokens != REQUEST_MAX_TOKENS["chat"]:
            report["ok"] = False
        if param_budget.settled_micros != expected_cost or param_budget.reserved_micros != 0:
            report["ok"] = False
    finally:
        server.shutdown()
        server.server_close()
        httpx.AsyncClient.send = original_async  # type: ignore[method-assign]
        httpx.Client.send = original_sync  # type: ignore[method-assign]

    report["pricing"] = PRICING
    report["currency_cap_micros"] = cap
    b2._write_json(paths["captures"] / "money_probe.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["ok"]:
        raise SystemExit(2)


def _require_real_http_gate() -> None:
    if os.environ.get("AMADEUS_B3_REAL_HTTP") != REAL_HTTP_AUTHORIZED_VALUE:
        b2._fail(
            "real_http_not_authorized",
            "real transport is gated until C sets AMADEUS_B3_REAL_HTTP="
            f"{REAL_HTTP_AUTHORIZED_VALUE} and confirms endpoint, credential, "
            "content and budget",
        )


def _require_real_authorization(*, require_env_credential: bool = True) -> None:
    _require_real_http_gate()
    if require_env_credential and not load_credential_from_env():
        b2._fail(
            "missing_b3_credential",
            "real transport needs AMADEUS_B3_PROVIDER_KEY or AMADEUS_B3_CREDENTIAL_FILE",
        )


def _read_windows_deepseek_credential() -> str | None:
    try:
        from app.services.credentials import WindowsCredentialStore

        store = WindowsCredentialStore()
        value = store.get("deepseek")
    except Exception:
        return None
    if not value:
        return None
    text = str(value).strip()
    return text or None


def load_real_credential() -> str | None:
    env_value = load_credential_from_env()
    if env_value:
        return env_value
    return _read_windows_deepseek_credential()


def _currency_cap_micros_from_args(
    args: argparse.Namespace,
    *,
    transport: str,
) -> int | None:
    raw = getattr(args, "currency_cap_cny", None)
    if transport == "real":
        if raw is None:
            return CURRENCY_CAP_MICROS
        return cny_to_micros(raw)
    if raw is None:
        return None
    return cny_to_micros(raw)


def cmd_local_serve(args: argparse.Namespace) -> None:
    global _SERVE_LEASE
    transport = str(getattr(args, "transport", "local") or "local")
    if transport == "real":
        _require_real_authorization(require_env_credential=True)
    run_root, paths, candidate, backend_port, frontend_port = b2._load_prepared_run(args)
    b2.require_loopback_port(backend_port, role="backend")
    serve_lease = _try_exclusive_lock(paths["root"] / "serve.lock")
    if serve_lease is None:
        b2._fail(
            "run_already_active",
            "another local-serve already holds this run; different ports cannot share it",
        )
    _SERVE_LEASE = serve_lease
    inject = str(getattr(args, "inject", "") or "")
    if transport == "real" and inject:
        b2._fail("inject_not_allowed_on_real", inject)
    max_http = int(getattr(args, "max_http_attempts", MAX_HTTP_ATTEMPTS) or MAX_HTTP_ATTEMPTS)
    max_turns = int(getattr(args, "max_user_turns", MAX_USER_TURNS) or MAX_USER_TURNS)
    currency_cap_micros = _currency_cap_micros_from_args(args, transport=transport)
    mock_server = None
    mock_url = ""
    require_https = False
    if transport == "local":
        mock_server, mock_url = start_local_provider(
            inject=inject,
            capture_log=paths["captures"] / "mock_http.jsonl",
        )
        credential = LOCAL_MOCK_CREDENTIAL
        base_url = mock_url
        loopback = True
        os.environ.pop("AMADEUS_B3_PROVIDER_KEY", None)
    else:
        credential = load_credential_from_env() or ""
        requested = (os.environ.get("AMADEUS_B3_BASE_URL") or BUILTIN_DEEPSEEK_BASE_URL).rstrip(
            "/"
        )
        if not official_deepseek_base_url(requested):
            b2._fail("non_official_base_url", "real transport is pinned to official origin")
        base_url = BUILTIN_DEEPSEEK_BASE_URL
        loopback = False
        require_https = True
    _paths, live_roots = b2._apply_isolated_process_env(
        run_root=run_root,
        candidate=candidate,
        backend_port=backend_port,
        frontend_port=frontend_port,
    )
    app, _cred_mod, inspection = b2._import_isolated_app(candidate, live_roots)
    fakes = b2._install_external_fakes(
        paths["captures"],
        provider_transport=transport,
        provider_credential=credential,
    )
    from app import config as app_config
    from app.db import get_data_root
    from app.main import app as fastapi_app
    from app.services.provider_runtime import deepseek_service
    import httpx
    import uvicorn

    app_config.env_path = paths["env_file"]
    data_root = get_data_root()
    if data_root != candidate.resolve():
        if mock_server is not None:
            mock_server.shutdown()
        b2._fail("data_root_mismatch", f"{data_root} != {candidate.resolve()}")
    deepseek_service.base_url = base_url
    budget_path = paths["captures"] / "budget.json"
    try:
        budget = HttpBudget.load_or_create(
            budget_path,
            max_http_attempts=max_http,
            max_user_turns=max_turns,
            candidate=str(candidate.resolve()),
            currency_cap_micros=currency_cap_micros,
        )
    except BudgetStateError as exc:
        if mock_server is not None:
            mock_server.shutdown()
        b2._fail("budget_state", str(exc))
    install_allowlisted_httpx(
        budget=budget,
        allowed_hosts=allowed_hosts_for(base_url, loopback=loopback),
        capture_dir=paths["captures"],
        original_async_send=httpx.AsyncClient.send,
        original_sync_send=httpx.Client.send,
        require_https=require_https,
    )
    install_processor_budget(budget)
    serve_meta = {
        "event": "b3_local_serve_starting",
        "app_file": str(Path(app.__file__).resolve()),
        "data_root": str(data_root),
        "backend_port": backend_port,
        "mock_url": mock_url,
        "base_url": base_url,
        "transport": transport,
        "request_id": DEFAULT_REQUEST_ID,
        "windows_store_constructed": fakes["windows_store_constructed"],
        "inspection": inspection,
        "pid": os.getpid(),
        "inject": inject,
        "max_http_attempts": max_http,
        "max_user_turns": max_turns,
        "currency_cap_micros": currency_cap_micros,
        "budget_http_attempts": budget.http_attempts,
        "budget_user_turns": budget.user_turns,
        "budget_committed_micros": budget.settled_micros + budget.reserved_micros,
    }
    print(json.dumps(serve_meta, ensure_ascii=False), flush=True)
    (paths["root"] / "serve.pid").write_text(str(os.getpid()), encoding="utf-8")
    b2._write_json(paths["captures"] / "local_serve.json", serve_meta)
    try:
        uvicorn.run(
            fastapi_app,
            host="127.0.0.1",
            port=backend_port,
            log_level="info",
        )
    finally:
        if mock_server is not None:
            mock_server.shutdown()
            mock_server.server_close()


def _start_backend(args: argparse.Namespace, *, transport: str) -> None:
    if transport == "real":
        _require_real_authorization()
    run_root = b2.deep_resolve(Path(args.run_root))
    paths = b2.run_paths(run_root)
    if not paths["meta"].is_file():
        b2._fail("missing_run", "prepare first")
    _fail_if_run_active(paths)
    start_lock = _try_exclusive_lock(paths["root"] / "start.lock")
    if start_lock is None:
        b2._fail(
            "run_already_active",
            "another start is already running for this run_root",
        )
    try:
        _fail_if_run_active(paths)
        _start_backend_locked(args, transport=transport, run_root=run_root, paths=paths)
    finally:
        try:
            start_lock.release()
        except Exception:
            pass


def _start_backend_locked(
    args: argparse.Namespace,
    *,
    transport: str,
    run_root: Path,
    paths: dict[str, Path],
) -> None:
    meta = b2._read_json(paths["meta"])
    candidate = Path(meta["candidate"])
    live_roots = b2.production_live_roots()
    try:
        b2.inspect_published_candidate(candidate, live_roots=live_roots)
    except ValueError as exc:
        b2._fail("invalid_candidate", str(exc))
    backend_port = int(args.backend_port)
    frontend_port = int(args.frontend_port)
    b2.require_loopback_port(backend_port, role="backend")
    if not getattr(args, "backend_only", False):
        b2.require_loopback_port(frontend_port, role="frontend")
    env = b2.isolated_environ(
        run_root=run_root,
        candidate=candidate,
        backend_port=backend_port,
        frontend_port=frontend_port,
    )
    env["AMADEUS_B3_TRANSPORT"] = transport
    if transport == "real":
        env["AMADEUS_B3_REAL_HTTP"] = REAL_HTTP_AUTHORIZED_VALUE
        env["AMADEUS_B3_BASE_URL"] = BUILTIN_DEEPSEEK_BASE_URL
        key = os.environ.get("AMADEUS_B3_PROVIDER_KEY")
        if key:
            env["AMADEUS_B3_PROVIDER_KEY"] = key
        file_path = os.environ.get("AMADEUS_B3_CREDENTIAL_FILE")
        if file_path and not key:
            env["AMADEUS_B3_CREDENTIAL_FILE"] = file_path
    python = args.python or sys.executable
    currency_cap_micros = _currency_cap_micros_from_args(args, transport=transport)
    serve_cmd = [
        python,
        str(SCRIPT_DIR / "memory_candidate_real_run.py"),
        "local-serve",
        "--run-root",
        str(run_root),
        "--backend-port",
        str(backend_port),
        "--frontend-port",
        str(frontend_port),
        "--transport",
        transport,
        "--max-http-attempts",
        str(int(getattr(args, "max_http_attempts", MAX_HTTP_ATTEMPTS) or MAX_HTTP_ATTEMPTS)),
        "--max-user-turns",
        str(int(getattr(args, "max_user_turns", MAX_USER_TURNS) or MAX_USER_TURNS)),
    ]
    if currency_cap_micros is not None:
        serve_cmd.extend(
            ["--currency-cap-cny", str(micros_to_cny(currency_cap_micros))]
        )
    if transport != "real" and getattr(args, "inject", ""):
        serve_cmd.extend(["--inject", str(args.inject)])
    backend = b2._spawn_hidden(
        serve_cmd,
        cwd=BACKEND_ROOT,
        env=env,
        log_path=paths["logs"] / "backend.log",
    )
    frontend = None
    if not getattr(args, "backend_only", False):
        frontend_env = dict(os.environ)
        frontend_env.update(
            {
                "AMADEUS_BACKEND_ORIGIN": f"http://127.0.0.1:{backend_port}",
                "AMADEUS_FRONTEND_PORT": str(frontend_port),
                "BROWSER": "none",
            }
        )
        for key in b2.PROVIDER_ENV_KEYS:
            frontend_env.pop(key, None)
        frontend_env.pop("AMADEUS_B3_PROVIDER_KEY", None)
        npm = "npm.cmd" if os.name == "nt" else "npm"
        frontend = b2._spawn_hidden(
            [
                npm,
                "run",
                "dev",
                "--",
                "--host",
                "127.0.0.1",
                "--port",
                str(frontend_port),
                "--strictPort",
            ],
            cwd=WORKTREE_ROOT / "desktop",
            env=frontend_env,
            log_path=paths["logs"] / "frontend.log",
        )
    pids = {
        "backend_pid": backend.pid,
        "frontend_pid": frontend.pid if frontend is not None else 0,
        "backend_port": backend_port,
        "frontend_port": frontend_port,
        "python": python,
        "started_at": b2._utc_now(),
        "run_root": str(run_root),
        "candidate": str(candidate),
        "backend_only": bool(getattr(args, "backend_only", False)),
        "transport": transport,
        "currency_cap_micros": currency_cap_micros,
    }
    b2._write_json(paths["pids"], pids)
    health = f"http://127.0.0.1:{backend_port}/health"
    deadline = time.monotonic() + 60
    last = ""
    try:
        while time.monotonic() < deadline:
            if backend.poll() is not None:
                log_path = paths["logs"] / "backend.log"
                log_text = (
                    log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
                    if log_path.is_file()
                    else ""
                )
                raise RuntimeError(log_text or f"backend exit {backend.returncode}")
            try:
                with urllib.request.urlopen(health, timeout=2) as response:
                    body = response.read().decode("utf-8", "replace")
                    if response.status == 200 and '"ok"' in body:
                        break
                    last = body[:200]
            except Exception as exc:
                last = str(exc)
            time.sleep(0.25)
        else:
            raise RuntimeError(f"{health} not ready: {last}")
        if frontend is not None:
            b2._wait_http(f"http://127.0.0.1:{frontend_port}/", timeout=90)
    except Exception as exc:
        b2.cmd_stop(argparse.Namespace(run_root=str(run_root)))
        if "budget_state" in str(exc):
            b2._fail("budget_state", str(exc))
        if isinstance(exc, SystemExit):
            raise
        b2._fail("startup_timeout", str(exc))
    print(json.dumps({"ok": True, "command": f"start-{transport}", **pids}, ensure_ascii=False, indent=2))


def cmd_start_local(args: argparse.Namespace) -> None:
    _start_backend(args, transport="local")


def cmd_start_real(args: argparse.Namespace) -> None:
    _require_real_http_gate()
    credential = load_real_credential()
    if not credential:
        b2._fail(
            "credential_missing",
            "no AMADEUS_B3_PROVIDER_KEY / AMADEUS_B3_CREDENTIAL_FILE / project store deepseek key",
        )
    os.environ["AMADEUS_B3_PROVIDER_KEY"] = credential
    os.environ["AMADEUS_B3_REAL_HTTP"] = REAL_HTTP_AUTHORIZED_VALUE
    os.environ.pop("AMADEUS_B3_BASE_URL", None)
    try:
        _start_backend(args, transport="real")
    finally:
        os.environ.pop("AMADEUS_B3_PROVIDER_KEY", None)


def _http_json(
    method: str,
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    timeout: float = 15.0,
) -> tuple[int, Any]:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if payload is not None else {},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            body = json.loads(raw) if raw else None
            return int(response.status), body
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            body = json.loads(raw) if raw else {"error": raw}
        except json.JSONDecodeError:
            body = {"error": raw}
        return int(exc.code), body


def _scope_query(session_id: str) -> str:
    return urllib.parse.urlencode(
        {
            "session_id": session_id,
            "worldline": "steins_gate",
            "identity_mode": "okabe",
        }
    )


async def _ws_session_b3(
    *,
    backend_port: int,
    session_id: str,
    conversation_id: str | None = None,
):
    import websockets

    uri = f"ws://127.0.0.1:{backend_port}/ws/chat?session_id={session_id}"
    ws = await websockets.connect(uri, open_timeout=10)
    auth: dict[str, Any] = {
        "type": "auth",
        "conversation_mode": "history" if conversation_id else "draft",
        "provider_id": DEFAULT_PROVIDER_ID,
        "model": DEFAULT_REQUEST_ID,
        "enable_tts": False,
        "temperature": 0.0,
        "worldline": "steins_gate",
        "default_identity_mode": "okabe",
        "self_name": "",
        "client": "desktop",
        "protocol_version": 2,
    }
    if conversation_id:
        auth["conversation_id"] = conversation_id
    await ws.send(json.dumps(auth))
    return ws


def _list_conversations(backend_port: int, session_id: str) -> list[dict[str, Any]]:
    params = urllib.parse.urlencode(
        {"session_id": session_id, "worldline": "steins_gate"}
    )
    status, body = _http_json(
        "GET",
        f"http://127.0.0.1:{backend_port}/api/conversations?{params}",
    )
    if status != 200 or not isinstance(body, list):
        raise RuntimeError(f"list conversations failed: {status} {body}")
    return body


def _job_rows(candidate: Path) -> list[dict[str, Any]]:
    conn = sqlite3.connect(str(candidate / "worldlines" / "sg" / "memory.sqlite3"))
    conn.row_factory = sqlite3.Row
    try:
        return [
            dict(r)
            for r in conn.execute(
                """SELECT job_id, state, retryable, last_error_code, source_message_id,
                          attempt_count, conversation_id
                     FROM memory_ingest_jobs
                    ORDER BY created_at DESC"""
            )
        ]
    finally:
        conn.close()


def _read_budget(paths: dict[str, Path]) -> dict[str, Any]:
    return b2._read_json(paths["captures"] / "budget.json")


def _mock_rows(paths: dict[str, Path]) -> list[dict[str, Any]]:
    log = paths["captures"] / "mock_http.jsonl"
    if not log.is_file():
        return []
    rows = []
    for line in log.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _last_core_facts(paths: dict[str, Path]) -> str:
    log = paths["captures"] / "provider_chat.jsonl"
    if not log.is_file():
        return ""
    rows = [
        json.loads(line)
        for line in log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not rows:
        return ""
    return str(rows[-1].get("core_facts") or "")


def _create_conversation(backend_port: int, session_id: str, title: str) -> dict[str, Any]:
    status, body = _http_json(
        "POST",
        f"http://127.0.0.1:{backend_port}/api/conversations",
        payload={
            "session_id": session_id,
            "worldline": "steins_gate",
            "identity_mode": "okabe",
            "provider_id": DEFAULT_PROVIDER_ID,
            "model_id": DEFAULT_REQUEST_ID,
            "title": title,
        },
    )
    if status != 201 or not isinstance(body, dict):
        raise RuntimeError(f"create conversation failed: {status} {body}")
    return body


def _wait_facts(
    *,
    backend_port: int,
    session_id: str,
    query: str,
    timeout: float = 20.0,
) -> dict[str, Any]:
    params = urllib.parse.urlencode(
        {
            "session_id": session_id,
            "worldline": "steins_gate",
            "identity_mode": "okabe",
            "query": query,
        }
    )
    url = f"http://127.0.0.1:{backend_port}/api/memory/facts?{params}"
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        status, body = _http_json("GET", url)
        if status == 200 and isinstance(body, dict):
            last = body
            return body
        time.sleep(0.4)
    return last


def _history_has_text(paths: dict[str, Path], text: str) -> bool:
    log = paths["captures"] / "provider_chat.jsonl"
    if not log.is_file():
        return False
    rows = [
        json.loads(line)
        for line in log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not rows:
        return False
    last = rows[-1]
    if "contains_extract_utterance" in last:
        return bool(last.get("contains_extract_utterance"))
    return text in str(last.get("user_tail") or "")


async def _send_turn_or_error(ws, text: str, *, timeout: float = 40.0) -> list[dict[str, Any]]:
    await b2._drain_ws(ws)
    events: list[dict[str, Any]] = []
    await ws.send(json.dumps({"type": "chat.send", "content": text}))
    deadline = time.monotonic() + timeout
    saw_thinking = False
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, remaining))
        except asyncio.TimeoutError:
            break
        payload = json.loads(raw)
        events.append(payload)
        if payload.get("type") == "error":
            break
        if payload.get("type") == "status" and payload.get("state") == "thinking":
            saw_thinking = True
        if payload.get("type") == "status" and payload.get("state") == "done" and saw_thinking:
            break
        if payload.get("type") == "turn.completed" and saw_thinking:
            break
    return events


def cmd_verify_local(args: argparse.Namespace) -> None:
    run_root = b2.deep_resolve(Path(args.run_root))
    paths = b2.run_paths(run_root)
    pids = b2._read_json(paths["pids"])
    if pids.get("transport") != "local":
        b2._fail("wrong_transport", "verify-local requires start-local")
    backend_port = int(pids["backend_port"])
    candidate = Path(pids["candidate"])
    session_id = args.session_id or "b3-verify-owner"
    chat_log = paths["captures"] / "provider_chat.jsonl"
    if chat_log.exists():
        chat_log.write_text("", encoding="utf-8")

    async def _run() -> dict[str, Any]:
        ws = await _ws_session_b3(backend_port=backend_port, session_id=session_id)
        try:
            first = await b2._ws_send_turn(ws, TURN_EXTRACT, timeout=45.0)
            ja_ok = any(
                b2.REPLY_JA in json.dumps(event, ensure_ascii=False) for event in first
            )
            if not ja_ok:
                raise AssertionError(f"missing japanese reply: {first!r}")
        finally:
            await ws.close()
        await asyncio.sleep(0.5)
        try:
            fact = await asyncio.to_thread(
                b2._wait_fact,
                backend_port=backend_port,
                session_id=session_id,
                marker=FACT_MARKER_PIECE,
                timeout=70.0,
            )
        except AssertionError as exc:
            debug = {
                "jobs": _job_rows(candidate)[:12],
                "budget": _read_budget(paths),
                "mock": _mock_rows(paths)[-20:],
                "facts_okabe": _wait_facts(
                    backend_port=backend_port,
                    session_id=session_id,
                    query="",
                    timeout=2.0,
                ),
            }
            raise AssertionError(json.dumps(debug, ensure_ascii=False)[:4000]) from exc
        retrieved = await asyncio.to_thread(
            _wait_facts,
            backend_port=backend_port,
            session_id=session_id,
            query=FACT_MARKER_PIECE,
        )
        convos = _list_conversations(backend_port, session_id)
        source = next((row for row in convos if row.get("is_selected")), convos[0])
        fact_id = str(fact["fact_id"])
        patch_status, patched = _http_json(
            "PATCH",
            f"http://127.0.0.1:{backend_port}/api/memory/facts/{fact_id}?{_scope_query(session_id)}",
            payload={
                "display_text": REVISED_DISPLAY,
                "expected_version": int(fact.get("active_version") or 1),
            },
        )
        if patch_status != 200:
            raise RuntimeError(f"patch failed: {patch_status} {patched}")
        archive_after_edit: dict[str, Any] = {}
        deadline_edit = time.monotonic() + 10
        while time.monotonic() < deadline_edit:
            archive_after_edit = await asyncio.to_thread(
                _wait_facts,
                backend_port=backend_port,
                session_id=session_id,
                query=FACT_MARKER_PIECE,
                timeout=2.0,
            )
            texts = [
                str(row.get("display_text") or "")
                for row in (archive_after_edit.get("facts") or [])
            ]
            if any(FACT_MARKER_HARMONICA in text for text in texts):
                break
            await asyncio.sleep(0.3)
        after_edit_convo = _create_conversation(
            backend_port, session_id, "b3-after-edit"
        )
        ws2 = await _ws_session_b3(
            backend_port=backend_port,
            session_id=session_id,
            conversation_id=str(after_edit_convo["id"]),
        )
        try:
            after_edit = await b2._ws_send_turn(ws2, TURN_AFTER_EDIT, timeout=45.0)
        finally:
            await ws2.close()
        await asyncio.sleep(0.5)
        core_after_edit = _last_core_facts(paths)
        history_has_ocarina = _history_has_text(paths, TURN_EXTRACT)
        forget_status, forgot = _http_json(
            "POST",
            (
                f"http://127.0.0.1:{backend_port}/api/conversations/"
                f"{source['id']}/forget?session_id={urllib.parse.quote(session_id)}"
                f"&worldline=steins_gate"
            ),
            payload={"forget_long_term": True},
        )
        if forget_status != 200:
            raise RuntimeError(f"forget failed: {forget_status} {forgot}")
        deadline = time.monotonic() + 20
        archive_after: dict[str, Any] = {}
        while time.monotonic() < deadline:
            archive_after = await asyncio.to_thread(
                _wait_facts,
                backend_port=backend_port,
                session_id=session_id,
                query=FACT_MARKER_HARMONICA,
                timeout=2.0,
            )
            texts = [
                str(row.get("display_text") or "")
                for row in (archive_after.get("facts") or [])
            ]
            if not any(FACT_MARKER_HARMONICA in text for text in texts):
                break
            await asyncio.sleep(0.4)
        after_forget_convo = _create_conversation(
            backend_port, session_id, "b3-after-forget"
        )
        ws3 = await _ws_session_b3(
            backend_port=backend_port,
            session_id=session_id,
            conversation_id=str(after_forget_convo["id"]),
        )
        try:
            after_forget = await b2._ws_send_turn(ws3, TURN_AFTER_FORGET, timeout=45.0)
        finally:
            await ws3.close()
        core_after_forget = _last_core_facts(paths)
        return {
            "japanese_reply": ja_ok,
            "first_events": first[-3:],
            "fact": fact,
            "retrieval": retrieved,
            "source_conversation_id": source.get("id"),
            "patch_status": patch_status,
            "patched": patched,
            "archive_after_edit": archive_after_edit,
            "after_edit_events": after_edit[-2:],
            "core_after_edit": core_after_edit,
            "history_has_extract_utterance": history_has_ocarina,
            "forget_status": forget_status,
            "forgot": forgot,
            "after_forget_events": after_forget[-2:],
            "core_after_forget": core_after_forget,
            "archive_after_forget": archive_after,
            "after_edit_conversation_id": after_edit_convo.get("id"),
            "after_forget_conversation_id": after_forget_convo.get("id"),
        }

    bundle = asyncio.run(_run())
    jobs = _job_rows(candidate)
    live_jobs = [
        row
        for row in jobs
        if row.get("last_error_code") != "candidate_build_freeze"
    ]
    budget = _read_budget(paths)
    mock = _mock_rows(paths)
    outbound = []
    outbound_log = paths["captures"] / "outbound_http.jsonl"
    if outbound_log.is_file():
        outbound = [
            json.loads(line)
            for line in outbound_log.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    kinds = sorted({str(row.get("kind")) for row in outbound})
    retrieved_texts = [
        str(row.get("display_text") or "")
        for row in ((bundle.get("retrieval") or {}).get("facts") or [])
    ]
    archive_after_edit_texts = [
        str(row.get("display_text") or "")
        for row in ((bundle.get("archive_after_edit") or {}).get("facts") or [])
    ]
    archive_texts = [
        str(row.get("display_text") or "")
        for row in ((bundle.get("archive_after_forget") or {}).get("facts") or [])
    ]
    source_conversation_id = str(bundle.get("source_conversation_id") or "")
    source_jobs = [
        row
        for row in live_jobs
        if source_conversation_id
        and str(row.get("conversation_id") or "") == source_conversation_id
    ]
    source_message_id = next(
        (
            row.get("source_message_id")
            for row in source_jobs
            if row.get("source_message_id")
        ),
        (bundle.get("fact") or {}).get("source_message_id"),
    )
    report = {
        "ok": True,
        "command": "verify-local",
        "session_id": session_id,
        "fact_id": (bundle.get("fact") or {}).get("fact_id"),
        "source_message_id": source_message_id,
        "job_ids": [row["job_id"] for row in live_jobs[:8]],
        "job_states": [row["state"] for row in live_jobs[:8]],
        "extract_attempts": [row.get("attempt_count") for row in source_jobs],
        "japanese_reply": bundle.get("japanese_reply"),
        "retrieval_hit_piece": any(FACT_MARKER_PIECE in text for text in retrieved_texts),
        "archive_after_edit_has_harmonica": any(
            FACT_MARKER_HARMONICA in text for text in archive_after_edit_texts
        ),
        "core_after_edit_has_harmonica": FACT_MARKER_HARMONICA in str(bundle.get("core_after_edit") or ""),
        "core_after_edit_has_ocarina_utterance": TURN_EXTRACT in str(bundle.get("core_after_edit") or ""),
        "new_session_history_has_extract_utterance": bundle.get("history_has_extract_utterance"),
        "core_after_forget_has_harmonica": FACT_MARKER_HARMONICA in str(bundle.get("core_after_forget") or ""),
        "archive_after_forget_has_harmonica": any(
            FACT_MARKER_HARMONICA in text for text in archive_texts
        ),
        "budget": budget,
        "outbound_kinds": kinds,
        "mock_count": len(mock),
        "http_attempts": budget.get("http_attempts"),
        "user_turns": budget.get("user_turns"),
        "usage_known": budget.get("usage_known"),
        "usage_unknown": budget.get("usage_unknown"),
        "how": {
            "chat": "WS chat.send → processor_loop → DeepSeekService httpx to local mock",
            "extract": "natural Memory v11 worker / default_memory_completion",
            "revision": "PATCH /api/memory/facts user_edit",
            "forget": "POST /api/conversations/{id}/forget forget_long_term=true",
            "history_isolation": "new draft conversation, same owner/worldline/okabe",
        },
    }
    if not report["retrieval_hit_piece"]:
        report["ok"] = False
    if not report["archive_after_edit_has_harmonica"]:
        report["ok"] = False
    if not report["core_after_edit_has_harmonica"]:
        report["ok"] = False
    if report["new_session_history_has_extract_utterance"]:
        report["ok"] = False
    if report["core_after_forget_has_harmonica"] or report["archive_after_forget_has_harmonica"]:
        report["ok"] = False
    if "chat" not in kinds or "extract" not in kinds:
        report["ok"] = False
    if int(report["user_turns"] or 0) < 3:
        report["ok"] = False
    b2._write_json(paths["captures"] / "verify_local.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["ok"]:
        raise SystemExit(2)


def events_have_model_reply(events: list[dict[str, Any]] | None) -> bool:
    for event in events or []:
        if event.get("type") == "segment.ready" and str(event.get("ja") or "").strip():
            return True
        if event.get("type") == "delta" and str(event.get("content") or "").strip():
            return True
        if event.get("type") == "text_chunk" and str(event.get("content") or "").strip():
            return True
    return False


def events_indicate_budget_block(events: list[dict[str, Any]] | None) -> bool:
    blob = json.dumps(events or [], ensure_ascii=False)
    if "budget_currency_exhausted" in blob:
        return True
    return any(
        str(event.get("code") or "") == "budget_currency_exhausted"
        for event in (events or [])
    )


def events_indicate_cancel(events: list[dict[str, Any]] | None) -> bool:
    return any(event.get("type") == "turn.cancelled" for event in (events or []))


def classify_verify_real_status(
    *,
    auth_failed: bool,
    over_cap: bool,
    official_host_only: bool,
    chat_kind_present: bool,
    promoted: bool,
    first_events: list[dict[str, Any]] | None,
    after_edit_events: list[dict[str, Any]] | None,
    after_forget_events: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    if auth_failed:
        return {"status": "failed", "ok": False, "stop": "auth_failed"}
    if over_cap:
        return {"status": "failed", "ok": False, "stop": "currency_over_cap"}
    if not official_host_only:
        return {"status": "failed", "ok": False, "stop": "non_official_host"}
    if not chat_kind_present:
        return {"status": "failed", "ok": False, "stop": "missing_chat_http"}
    if not promoted:
        return {"status": "partial", "ok": False, "stop": "not_promoted"}
    for name, events in (
        ("first", first_events),
        ("after_edit", after_edit_events),
        ("after_forget", after_forget_events),
    ):
        if events_indicate_budget_block(events) or events_indicate_cancel(events):
            return {
                "status": "partial",
                "ok": False,
                "stop": "budget_or_cancel",
                "incomplete_turn": name,
            }
        if not events_have_model_reply(events):
            return {
                "status": "partial",
                "ok": False,
                "stop": "missing_model_reply",
                "incomplete_turn": name,
            }
    return {"status": "complete", "ok": True, "stop": None}


def _events_indicate_auth_failure(events: list[dict[str, Any]]) -> bool:
    blob = json.dumps(events, ensure_ascii=False)
    markers = (
        "invalid_api_key",
        "API error (401)",
        "API error (403)",
        "status_code=401",
        "status_code=403",
        "DeepSeek API error (401)",
        "DeepSeek API error (403)",
        "error (401)",
        "error (403)",
    )
    return any(marker in blob for marker in markers)


def _outbound_hosts(paths: dict[str, Path]) -> list[str]:
    log = paths["captures"] / "outbound_http.jsonl"
    if not log.is_file():
        return []
    hosts = []
    for line in log.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        hosts.append(str(row.get("host") or ""))
    return hosts


def cmd_catalog_check(args: argparse.Namespace) -> None:
    run_root = b2.deep_resolve(Path(args.run_root))
    paths = b2.run_paths(run_root)
    pids = b2._read_json(paths["pids"])
    if pids.get("transport") != "real":
        b2._fail("wrong_transport", "catalog-check requires start-real")
    backend_port = int(pids["backend_port"])
    before = _read_budget(paths)
    status, body = _http_json(
        "GET",
        f"http://127.0.0.1:{backend_port}/api/providers/deepseek/models?refresh=true",
        timeout=45.0,
    )
    after = _read_budget(paths)
    outbound = []
    log = paths["captures"] / "outbound_http.jsonl"
    if log.is_file():
        outbound = [
            json.loads(line)
            for line in log.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    catalog_rows = [row for row in outbound if row.get("kind") == "catalog"]
    hosts = sorted({str(row.get("host") or "") for row in catalog_rows})
    models = []
    if isinstance(body, dict):
        raw_models = body.get("models") or []
        for item in raw_models:
            if isinstance(item, str):
                models.append(item)
            elif isinstance(item, dict):
                models.append(str(item.get("id") or item.get("model") or ""))
    auth_failed = status in {401, 403} or (
        isinstance(body, dict)
        and str((body.get("detail") or {}).get("code") if isinstance(body.get("detail"), dict) else "")
        in {"unauthorized", "forbidden"}
    )
    report = {
        "ok": True,
        "command": "catalog-check",
        "status": status,
        "discovery_status": body.get("discovery_status") if isinstance(body, dict) else None,
        "preferred_model_id": body.get("preferred_model_id") if isinstance(body, dict) else None,
        "models": models[:20],
        "has_deepseek_flash": DEFAULT_REQUEST_ID in models,
        "outbound_hosts": hosts,
        "official_host_only": hosts == [OFFICIAL_DEEPSEEK_HOST],
        "http_attempts_before": before.get("http_attempts"),
        "http_attempts_after": after.get("http_attempts"),
        "committed_cny": after.get("committed_cny"),
        "reserved_cny": after.get("reserved_cny"),
        "auth_failed": auth_failed,
    }
    if status != 200:
        report["ok"] = False
    if auth_failed:
        report["ok"] = False
        report["stop"] = "auth_failed"
    if not report["has_deepseek_flash"]:
        report["ok"] = False
    if not report["official_host_only"]:
        report["ok"] = False
    if after.get("currency_cap_micros") != CURRENCY_CAP_MICROS:
        report["ok"] = False
    b2._write_json(paths["captures"] / "catalog_check.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if auth_failed:
        raise SystemExit(2)
    if not report["ok"]:
        raise SystemExit(2)


def cmd_verify_real(args: argparse.Namespace) -> None:
    run_root = b2.deep_resolve(Path(args.run_root))
    paths = b2.run_paths(run_root)
    pids = b2._read_json(paths["pids"])
    if pids.get("transport") != "real":
        b2._fail("wrong_transport", "verify-real requires start-real")
    backend_port = int(pids["backend_port"])
    candidate = Path(pids["candidate"])
    session_id = args.session_id or "b3-real-owner"
    turn_timeout = 120.0
    fact_timeout = 180.0

    async def _run() -> dict[str, Any]:
        result: dict[str, Any] = {
            "auth_failed": False,
            "promoted": False,
            "fact": None,
            "patch_status": None,
            "forget_status": None,
        }
        ws = await _ws_session_b3(backend_port=backend_port, session_id=session_id)
        try:
            first = await _send_turn_or_error(ws, TURN_EXTRACT, timeout=turn_timeout)
        finally:
            await ws.close()
        result["first_events"] = first[-8:]
        result["auth_failed"] = _events_indicate_auth_failure(first)
        if result["auth_failed"]:
            return result
        await asyncio.sleep(1.0)
        fact = None
        try:
            fact = await asyncio.to_thread(
                b2._wait_fact,
                backend_port=backend_port,
                session_id=session_id,
                marker=FACT_MARKER_PIECE,
                timeout=fact_timeout,
            )
        except AssertionError:
            fact = None
        result["fact"] = fact
        result["promoted"] = bool(fact and fact.get("fact_id"))
        retrieved = await asyncio.to_thread(
            _wait_facts,
            backend_port=backend_port,
            session_id=session_id,
            query=FACT_MARKER_PIECE,
            timeout=8.0,
        )
        result["retrieval"] = retrieved
        convos = _list_conversations(backend_port, session_id)
        source = next((row for row in convos if row.get("is_selected")), convos[0] if convos else {})
        result["source_conversation_id"] = source.get("id")
        if not result["promoted"]:
            return result
        fact_id = str(fact["fact_id"])
        patch_status, patched = _http_json(
            "PATCH",
            f"http://127.0.0.1:{backend_port}/api/memory/facts/{fact_id}?{_scope_query(session_id)}",
            payload={
                "display_text": REVISED_DISPLAY,
                "expected_version": int(fact.get("active_version") or 1),
            },
        )
        result["patch_status"] = patch_status
        result["patched"] = patched
        archive_after_edit: dict[str, Any] = {}
        deadline_edit = time.monotonic() + 15
        while time.monotonic() < deadline_edit:
            archive_after_edit = await asyncio.to_thread(
                _wait_facts,
                backend_port=backend_port,
                session_id=session_id,
                query=FACT_MARKER_PIECE,
                timeout=2.0,
            )
            texts = [
                str(row.get("display_text") or "")
                for row in (archive_after_edit.get("facts") or [])
            ]
            if any(FACT_MARKER_HARMONICA in text for text in texts):
                break
            await asyncio.sleep(0.4)
        result["archive_after_edit"] = archive_after_edit
        after_edit_convo = _create_conversation(backend_port, session_id, "b3-real-after-edit")
        result["after_edit_conversation_id"] = after_edit_convo.get("id")
        ws2 = await _ws_session_b3(
            backend_port=backend_port,
            session_id=session_id,
            conversation_id=str(after_edit_convo["id"]),
        )
        try:
            after_edit = await _send_turn_or_error(ws2, TURN_AFTER_EDIT, timeout=turn_timeout)
        finally:
            await ws2.close()
        result["after_edit_events"] = after_edit[-6:]
        result["auth_failed"] = result["auth_failed"] or _events_indicate_auth_failure(after_edit)
        result["core_after_edit"] = _last_core_facts(paths)
        result["history_has_extract_utterance"] = _history_has_text(paths, TURN_EXTRACT)
        if result["auth_failed"]:
            return result
        forget_status, forgot = _http_json(
            "POST",
            (
                f"http://127.0.0.1:{backend_port}/api/conversations/"
                f"{source['id']}/forget?session_id={urllib.parse.quote(session_id)}"
                f"&worldline=steins_gate"
            ),
            payload={"forget_long_term": True},
        )
        result["forget_status"] = forget_status
        result["forgot"] = forgot
        deadline = time.monotonic() + 25
        archive_after: dict[str, Any] = {}
        while time.monotonic() < deadline:
            archive_after = await asyncio.to_thread(
                _wait_facts,
                backend_port=backend_port,
                session_id=session_id,
                query=FACT_MARKER_HARMONICA,
                timeout=2.0,
            )
            texts = [
                str(row.get("display_text") or "")
                for row in (archive_after.get("facts") or [])
            ]
            if not any(FACT_MARKER_HARMONICA in text for text in texts):
                break
            await asyncio.sleep(0.4)
        result["archive_after_forget"] = archive_after
        after_forget_convo = _create_conversation(
            backend_port, session_id, "b3-real-after-forget"
        )
        result["after_forget_conversation_id"] = after_forget_convo.get("id")
        ws3 = await _ws_session_b3(
            backend_port=backend_port,
            session_id=session_id,
            conversation_id=str(after_forget_convo["id"]),
        )
        try:
            after_forget = await _send_turn_or_error(
                ws3, TURN_AFTER_FORGET, timeout=turn_timeout
            )
        finally:
            await ws3.close()
        result["after_forget_events"] = after_forget[-6:]
        result["core_after_forget"] = _last_core_facts(paths)
        result["auth_failed"] = result["auth_failed"] or _events_indicate_auth_failure(
            after_forget
        )
        return result

    bundle = asyncio.run(_run())
    jobs = _job_rows(candidate)
    live_jobs = [
        row
        for row in jobs
        if row.get("last_error_code") != "candidate_build_freeze"
    ]
    budget = _read_budget(paths)
    outbound = []
    outbound_log = paths["captures"] / "outbound_http.jsonl"
    if outbound_log.is_file():
        outbound = [
            json.loads(line)
            for line in outbound_log.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    kinds = sorted({str(row.get("kind")) for row in outbound})
    hosts = sorted({str(row.get("host") or "") for row in outbound if row.get("host")})
    retrieved_texts = [
        str(row.get("display_text") or "")
        for row in ((bundle.get("retrieval") or {}).get("facts") or [])
    ]
    archive_after_edit_texts = [
        str(row.get("display_text") or "")
        for row in ((bundle.get("archive_after_edit") or {}).get("facts") or [])
    ]
    archive_texts = [
        str(row.get("display_text") or "")
        for row in ((bundle.get("archive_after_forget") or {}).get("facts") or [])
    ]
    committed = int(budget.get("committed_micros") or 0)
    cap = budget.get("currency_cap_micros")
    over_cap = isinstance(cap, int) and committed > cap
    judgement = {
        "natural_extract": "promoted" if bundle.get("promoted") else "not_promoted",
        "fact_in_later_core_facts": FACT_MARKER_HARMONICA
        in str(bundle.get("core_after_edit") or ""),
        "patch_changed_archive": any(
            FACT_MARKER_HARMONICA in text for text in archive_after_edit_texts
        ),
        "forget_cleared_harmonica": (
            bundle.get("forget_status") == 200
            and not any(FACT_MARKER_HARMONICA in text for text in archive_texts)
            and FACT_MARKER_HARMONICA not in str(bundle.get("core_after_forget") or "")
        ),
        "new_session_no_old_history": bundle.get("history_has_extract_utterance") is False,
        "official_host_only": hosts == [OFFICIAL_DEEPSEEK_HOST],
    }
    classification = classify_verify_real_status(
        auth_failed=bool(bundle.get("auth_failed")),
        over_cap=over_cap,
        official_host_only=bool(judgement.get("official_host_only")),
        chat_kind_present="chat" in kinds,
        promoted=bool(bundle.get("promoted")),
        first_events=list(bundle.get("first_events") or []),
        after_edit_events=list(bundle.get("after_edit_events") or []),
        after_forget_events=list(bundle.get("after_forget_events") or []),
    )
    report = {
        "ok": classification["ok"],
        "status": classification["status"],
        "command": "verify-real",
        "session_id": session_id,
        "auth_failed": bundle.get("auth_failed"),
        "fact_id": (bundle.get("fact") or {}).get("fact_id") if bundle.get("fact") else None,
        "job_ids": [row["job_id"] for row in live_jobs[:8]],
        "job_states": [row["state"] for row in live_jobs[:8]],
        "retrieval_hit_piece": any(FACT_MARKER_PIECE in text for text in retrieved_texts),
        "core_after_edit": bundle.get("core_after_edit"),
        "core_after_forget": bundle.get("core_after_forget"),
        "patch_status": bundle.get("patch_status"),
        "forget_status": bundle.get("forget_status"),
        "first_events_tail": bundle.get("first_events"),
        "after_edit_events_tail": bundle.get("after_edit_events"),
        "after_forget_events_tail": bundle.get("after_forget_events"),
        "outbound_kinds": kinds,
        "outbound_hosts": hosts,
        "http_attempts": budget.get("http_attempts"),
        "user_turns": budget.get("user_turns"),
        "usage_known": budget.get("usage_known"),
        "usage_unknown": budget.get("usage_unknown"),
        "settled_cny": budget.get("settled_cny"),
        "reserved_cny": budget.get("reserved_cny"),
        "committed_cny": budget.get("committed_cny"),
        "currency_cap_cny": budget.get("currency_cap"),
        "judgement": judgement,
        "how": {
            "chat": "WS chat.send → processor_loop → DeepSeekService httpx to api.deepseek.com",
            "extract": "natural Memory v11 worker / default_memory_completion",
            "revision": "PATCH /api/memory/facts user_edit",
            "forget": "POST /api/conversations/{id}/forget forget_long_term=true",
            "history_isolation": "new draft conversation, same owner/worldline/okabe",
            "mock_asserts": False,
        },
    }
    if classification.get("stop"):
        report["stop"] = classification["stop"]
    if classification.get("incomplete_turn"):
        report["incomplete_turn"] = classification["incomplete_turn"]
    b2._write_json(paths["captures"] / "verify_real.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if classification["status"] != "complete":
        raise SystemExit(2)


def _jsonl_line_count(path: Path) -> int:
    if not path.is_file():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def _capture_log_offsets(paths: dict[str, Path]) -> dict[str, int]:
    captures = paths["captures"]
    logs = paths["logs"]
    return {
        "outbound_http": _jsonl_line_count(captures / "outbound_http.jsonl"),
        "provider_chat": _jsonl_line_count(captures / "provider_chat.jsonl"),
        "credentials": _jsonl_line_count(captures / "credentials.jsonl"),
        "sidecar": _jsonl_line_count(captures / "sidecar.jsonl"),
        "backend_log_chars": (
            (logs / "backend.log").stat().st_size if (logs / "backend.log").is_file() else 0
        ),
    }


def _jsonl_slice(path: Path, start_line: int) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for index, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if index <= start_line or not line.strip():
            continue
        rows.append(json.loads(line))
    return rows


def _occupied_attempts(budget: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        row
        for row in (budget.get("attempts") or [])
        if str(row.get("status") or "") == "occupied"
    ]


def _open_ingest_jobs(candidate: Path) -> list[dict[str, Any]]:
    return [
        row
        for row in _job_rows(candidate)
        if row.get("last_error_code") != "candidate_build_freeze"
        and str(row.get("state") or "") in OPEN_INGEST_JOB_STATES
    ]


def _wait_budget_and_jobs_idle(
    paths: dict[str, Path],
    candidate: Path,
    *,
    timeout: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last = _read_budget(paths)
    while time.monotonic() < deadline:
        last = _read_budget(paths)
        occupied = _occupied_attempts(last)
        reserved = int(last.get("reserved_micros") or 0)
        open_jobs = _open_ingest_jobs(candidate)
        if not occupied and reserved == 0 and not open_jobs:
            return last
        time.sleep(0.5)
    return last


def _sqlite_fact_state(candidate: Path, fact_id: str) -> dict[str, Any]:
    memory = candidate / "worldlines" / "sg" / "memory.sqlite3"
    conn = sqlite3.connect(f"file:{memory.resolve().as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        fact = conn.execute(
            """SELECT fact_id, session_id, identity_mode, state, active_version
                 FROM stable_facts WHERE fact_id=?""",
            (fact_id,),
        ).fetchone()
        versions = [
            dict(row)
            for row in conn.execute(
                """SELECT fact_id, version_no, display_text, change_kind, invalid_at
                     FROM stable_fact_versions
                    WHERE fact_id=?
                    ORDER BY version_no""",
                (fact_id,),
            )
        ]
        return {
            "fact": dict(fact) if fact is not None else None,
            "versions": versions,
        }
    finally:
        conn.close()


def _sqlite_conversation_messages(
    candidate: Path, conversation_id: str
) -> list[dict[str, Any]]:
    history = candidate / "worldlines" / "sg" / "history.sqlite3"
    conn = sqlite3.connect(f"file:{history.resolve().as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return [
            dict(row)
            for row in conn.execute(
                """SELECT id, conversation_id, role, substr(content, 1, 240) AS content
                     FROM messages
                    WHERE conversation_id=?
                    ORDER BY id""",
                (conversation_id,),
            )
        ]
    finally:
        conn.close()


def _japanese_from_events(events: list[dict[str, Any]] | None) -> str:
    parts: list[str] = []
    for event in events or []:
        if event.get("type") == "segment.ready" and str(event.get("ja") or "").strip():
            parts.append(str(event.get("ja")))
    return "".join(parts)


def _remaining_currency_room(budget: dict[str, Any]) -> int:
    cap = int(budget.get("currency_cap_micros") or 0)
    settled = int(budget.get("settled_micros") or 0)
    reserved = int(budget.get("reserved_micros") or 0)
    return cap - settled - reserved


def cmd_final_turn(args: argparse.Namespace) -> None:
    run_root = b2.deep_resolve(Path(args.run_root))
    paths = b2.run_paths(run_root)
    pids = b2._read_json(paths["pids"])
    if pids.get("transport") != "real":
        b2._fail("wrong_transport", "final-turn requires start-real")
    backend_port = int(pids["backend_port"])
    candidate = Path(pids["candidate"])
    session_id = args.session_id or DEFAULT_REAL_SESSION_ID
    conversation_id = args.conversation_id or AFTER_FORGET_CONVERSATION_ID
    fact_id = args.fact_id or HARMONICA_FACT_ID
    output_dir = (
        b2.deep_resolve(Path(args.output_dir))
        if args.output_dir
        else WORKTREE_ROOT / ".scratch" / "memory-runtime-b3" / "real" / "supplement"
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    marker_log = paths["captures"] / "supplement_markers.jsonl"
    offsets_before = _capture_log_offsets(paths)
    b2._append_jsonl(
        marker_log,
        {
            "at": b2._utc_now(),
            "phase": SUPPLEMENT_PHASE,
            "event": "before_idle_wait",
            "offsets": offsets_before,
        },
    )
    idle = _wait_budget_and_jobs_idle(paths, candidate, timeout=120.0)
    occupied = _occupied_attempts(idle)
    reserved = int(idle.get("reserved_micros") or 0)
    remaining = _remaining_currency_room(idle)
    open_jobs = _open_ingest_jobs(candidate)
    budget_before = _read_budget(paths)
    b2._write_json(output_dir / "budget_before.json", budget_before)
    fact_before = _sqlite_fact_state(candidate, fact_id)
    b2._write_json(output_dir / "fact_before.json", fact_before)
    archive_before = _wait_facts(
        backend_port=backend_port,
        session_id=session_id,
        query="",
        timeout=8.0,
    )
    b2._write_json(output_dir / "facts_archive_before.json", archive_before)
    conversation_before = _sqlite_conversation_messages(candidate, conversation_id)
    b2._write_json(output_dir / "conversation_before.json", conversation_before)
    fact_details_status, fact_details = _http_json(
        "GET",
        (
            f"http://127.0.0.1:{backend_port}/api/memory/facts/{fact_id}"
            f"/details?{_scope_query(session_id)}"
        ),
        timeout=10.0,
    )
    b2._write_json(
        output_dir / "fact_details_before.json",
        {"status": fact_details_status, "body": fact_details},
    )

    report: dict[str, Any] = {
        "ok": False,
        "status": "partial",
        "command": "final-turn",
        "phase": SUPPLEMENT_PHASE,
        "session_id": session_id,
        "conversation_id": conversation_id,
        "fact_id": fact_id,
        "sent": False,
        "offsets_before": offsets_before,
        "idle_wait": {
            "occupied": occupied,
            "reserved_micros": reserved,
            "remaining_micros": remaining,
            "open_jobs": open_jobs,
        },
        "harmonica_fact_state": (fact_before.get("fact") or {}).get("state"),
    }

    block_reason = ""
    if occupied:
        block_reason = "occupied_attempt"
    elif reserved != 0:
        block_reason = "reserved_nonzero"
    elif remaining < RESERVE_MICROS_PER_COMPLETION:
        block_reason = "insufficient_reserve_room"
    elif open_jobs:
        block_reason = "open_ingest_job"

    events: list[dict[str, Any]] = []
    if block_reason:
        report["stop"] = block_reason
        report["events"] = []
    else:
        b2._append_jsonl(
            marker_log,
            {
                "at": b2._utc_now(),
                "phase": SUPPLEMENT_PHASE,
                "event": "before_send",
                "conversation_id": conversation_id,
                "user": TURN_AFTER_FORGET,
            },
        )

        async def _run() -> list[dict[str, Any]]:
            ws = await _ws_session_b3(
                backend_port=backend_port,
                session_id=session_id,
                conversation_id=conversation_id,
            )
            try:
                return await _send_turn_or_error(
                    ws, TURN_AFTER_FORGET, timeout=120.0
                )
            finally:
                await ws.close()

        events = asyncio.run(_run())
        report["sent"] = True
        report["events"] = events
        report["auth_failed"] = _events_indicate_auth_failure(events)
        if (
            report["auth_failed"]
            or events_indicate_budget_block(events)
            or events_indicate_cancel(events)
            or not events_have_model_reply(events)
        ):
            report["status"] = "partial"
            if report["auth_failed"]:
                report["stop"] = "auth_failed"
            elif events_indicate_budget_block(events) or events_indicate_cancel(events):
                report["stop"] = "budget_or_cancel"
            else:
                report["stop"] = "missing_model_reply"
        else:
            report["status"] = "complete"
            report["ok"] = True
        _wait_budget_and_jobs_idle(paths, candidate, timeout=180.0)

    budget_after = _read_budget(paths)
    b2._write_json(output_dir / "budget_after.json", budget_after)
    offsets_after = _capture_log_offsets(paths)
    outbound_delta = _jsonl_slice(
        paths["captures"] / "outbound_http.jsonl",
        int(offsets_before.get("outbound_http") or 0),
    )
    chat_delta = _jsonl_slice(
        paths["captures"] / "provider_chat.jsonl",
        int(offsets_before.get("provider_chat") or 0),
    )
    b2._write_json(output_dir / "outbound_delta.json", outbound_delta)
    b2._write_json(output_dir / "provider_chat_delta.json", chat_delta)
    b2._write_json(output_dir / "events.json", events)
    core_at_send = ""
    if chat_delta:
        core_at_send = str(chat_delta[-1].get("core_facts") or "")
    b2._write_json(
        output_dir / "core_facts_at_send.json",
        {"core_facts": core_at_send, "rows": chat_delta},
    )
    conversation_after = _sqlite_conversation_messages(candidate, conversation_id)
    b2._write_json(output_dir / "conversation_after.json", conversation_after)
    report["offsets_after"] = offsets_after
    report["http_attempts"] = budget_after.get("http_attempts")
    report["user_turns"] = budget_after.get("user_turns")
    report["settled_cny"] = budget_after.get("settled_cny")
    report["reserved_cny"] = budget_after.get("reserved_cny")
    report["committed_cny"] = budget_after.get("committed_cny")
    report["model_japanese"] = _japanese_from_events(events)
    report["core_facts_at_send"] = core_at_send
    before_ids = {
        str(row.get("attempt_id"))
        for row in (budget_before.get("attempts") or [])
        if row.get("attempt_id")
    }
    report["attempt_ids"] = [
        str(row.get("attempt_id"))
        for row in (budget_after.get("attempts") or [])
        if row.get("attempt_id") and str(row.get("attempt_id")) not in before_ids
    ]
    report["outbound_hosts"] = sorted(
        {str(row.get("host") or "") for row in outbound_delta if row.get("host")}
    )
    report["outbound_models"] = sorted(
        {
            str(row.get("model") or "")
            for row in outbound_delta
            if row.get("model")
        }
    )
    b2._append_jsonl(
        marker_log,
        {
            "at": b2._utc_now(),
            "phase": SUPPLEMENT_PHASE,
            "event": "after_turn",
            "status": report["status"],
            "stop": report.get("stop"),
            "offsets": offsets_after,
        },
    )
    b2._write_json(paths["captures"] / "supplement_final_turn.json", report)
    b2._write_json(output_dir / "final_turn.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["status"] != "complete":
        raise SystemExit(2)


def cmd_prove_boundaries(args: argparse.Namespace) -> None:
    run_root = b2.deep_resolve(Path(args.run_root))
    paths = b2.run_paths(run_root)
    pids = b2._read_json(paths["pids"])
    backend_port = int(pids["backend_port"])
    session_id = args.session_id or "b3-boundary-owner"
    before_budget = _read_budget(paths)
    before_mock = _mock_rows(paths)
    models_url = f"http://127.0.0.1:{backend_port}/api/providers/deepseek/models?refresh=true"

    turn_events: list[dict[str, Any]] = []

    async def _extra_turn() -> list[dict[str, Any]]:
        ws = await _ws_session_b3(backend_port=backend_port, session_id=session_id)
        try:
            return await _send_turn_or_error(ws, "预算外的第四轮不应发出模型请求。")
        finally:
            await ws.close()

    remaining_turns = int(before_budget["max_user_turns"]) - int(before_budget["user_turns"])
    for _ in range(max(0, remaining_turns)):
        asyncio.run(_extra_turn())
    mock_before_reject = len(_mock_rows(paths))
    chat_before_reject = sum(1 for row in _mock_rows(paths) if row.get("kind") == "chat")
    turn_events = asyncio.run(_extra_turn())
    after_turn = _read_budget(paths)
    after_turn_mock = _mock_rows(paths)
    turn_rejected = any(
        event.get("code") == "budget_turns_exhausted" or "budget_turns_exhausted" in str(event)
        for event in turn_events
    )
    chat_after_reject = sum(1 for row in after_turn_mock if row.get("kind") == "chat")
    current = _read_budget(paths)
    while int(current["http_attempts"]) < int(current["max_http_attempts"]) - 1:
        _http_json("GET", models_url)
        current = _read_budget(paths)
        if int(current["http_attempts"]) >= int(current["max_http_attempts"]):
            break
    pre_race = _read_budget(paths)
    pre_race_mock = len(_mock_rows(paths))
    results: list[tuple[int, Any]] = []

    def _refresh() -> None:
        results.append(_http_json("GET", models_url, timeout=10.0))

    threads = [threading.Thread(target=_refresh) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    post_race = _read_budget(paths)
    post_race_mock = _mock_rows(paths)
    overflow = _http_json("GET", models_url)
    final_budget = _read_budget(paths)
    final_mock = _mock_rows(paths)
    report = {
        "ok": True,
        "command": "prove-boundaries",
        "before": {
            "http_attempts": before_budget.get("http_attempts"),
            "user_turns": before_budget.get("user_turns"),
            "mock_count": len(before_mock),
        },
        "turn_exhausted": {
            "error": turn_rejected,
            "user_turns": after_turn.get("user_turns"),
            "max_user_turns": after_turn.get("max_user_turns"),
            "mock_did_not_grow": len(after_turn_mock) == mock_before_reject,
            "chat_did_not_grow": chat_after_reject == chat_before_reject,
            "events_tail": turn_events[-3:],
        },
        "http_race": {
            "pre_attempts": pre_race.get("http_attempts"),
            "post_attempts": post_race.get("http_attempts"),
            "max_http_attempts": post_race.get("max_http_attempts"),
            "pre_mock": pre_race_mock,
            "post_mock": len(post_race_mock),
            "mock_delta": len(post_race_mock) - pre_race_mock,
            "status_codes": [row[0] for row in results],
        },
        "http_exhausted": {
            "attempts": final_budget.get("http_attempts"),
            "max_http_attempts": final_budget.get("max_http_attempts"),
            "overflow_status": overflow[0],
            "mock_count": len(final_mock),
            "rejected": final_budget.get("rejected")[-6:],
        },
        "usage": {
            "known": final_budget.get("usage_known"),
            "unknown": final_budget.get("usage_unknown"),
            "input_tokens": final_budget.get("input_tokens"),
            "output_tokens": final_budget.get("output_tokens"),
        },
    }
    if int(after_turn.get("user_turns") or 0) != int(after_turn.get("max_user_turns") or 0):
        report["ok"] = False
    if not turn_rejected:
        report["ok"] = False
    if not report["turn_exhausted"].get("chat_did_not_grow"):
        report["ok"] = False
    if int(post_race.get("http_attempts") or 0) > int(post_race.get("max_http_attempts") or 0):
        report["ok"] = False
    if int(report["http_race"]["mock_delta"]) > 1:
        report["ok"] = False
    if int(final_budget.get("http_attempts") or 0) != int(final_budget.get("max_http_attempts") or 0):
        report["ok"] = False
    b2._write_json(paths["captures"] / "prove_boundaries.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["ok"]:
        raise SystemExit(2)


def _add_budget_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--max-http-attempts", type=int, default=MAX_HTTP_ATTEMPTS)
    parser.add_argument("--max-user-turns", type=int, default=MAX_USER_TURNS)
    parser.add_argument("--currency-cap-cny", type=float, default=None)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="B3 isolated real-run entry")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_run(p: argparse.ArgumentParser) -> None:
        p.add_argument("--run-root", required=True)
        p.add_argument("--backend-port", type=int, default=b2.DEFAULT_BACKEND_PORT)
        p.add_argument("--frontend-port", type=int, default=b2.DEFAULT_FRONTEND_PORT)

    prepare = sub.add_parser("prepare")
    add_run(prepare)
    prepare.add_argument("--force", action="store_true")
    prepare.set_defaults(func=b2.cmd_prepare)

    plan = sub.add_parser("print-plan")
    plan.add_argument("--output", default="")
    plan.set_defaults(func=cmd_print_plan)

    probe = sub.add_parser("budget-probe")
    add_run(probe)
    _add_budget_flags(probe)
    probe.add_argument("--inject", default="", choices=("", "timeout", "auth_fail"))
    probe.set_defaults(func=cmd_budget_probe)

    money = sub.add_parser("money-probe")
    add_run(money)
    money.set_defaults(func=cmd_money_probe)

    local_serve = sub.add_parser("local-serve")
    add_run(local_serve)
    _add_budget_flags(local_serve)
    local_serve.add_argument(
        "--inject", default="", choices=("", "timeout", "auth_fail", "fail_first_extract")
    )
    local_serve.add_argument("--transport", default="local", choices=("local", "real"))
    local_serve.set_defaults(func=cmd_local_serve)

    start = sub.add_parser("start-local")
    add_run(start)
    _add_budget_flags(start)
    start.add_argument("--python", default="")
    start.add_argument("--backend-only", action="store_true")
    start.add_argument(
        "--inject", default="", choices=("", "timeout", "auth_fail", "fail_first_extract")
    )
    start.set_defaults(func=cmd_start_local)

    start_real = sub.add_parser("start-real")
    add_run(start_real)
    _add_budget_flags(start_real)
    start_real.add_argument("--python", default="")
    start_real.add_argument("--backend-only", action="store_true")
    start_real.set_defaults(func=cmd_start_real)

    verify = sub.add_parser("verify-local")
    add_run(verify)
    verify.add_argument("--session-id", default="")
    verify.set_defaults(func=cmd_verify_local)

    catalog = sub.add_parser("catalog-check")
    add_run(catalog)
    catalog.set_defaults(func=cmd_catalog_check)

    verify_real = sub.add_parser("verify-real")
    add_run(verify_real)
    verify_real.add_argument("--session-id", default="")
    verify_real.set_defaults(func=cmd_verify_real)

    final_turn = sub.add_parser("final-turn")
    add_run(final_turn)
    final_turn.add_argument("--session-id", default=DEFAULT_REAL_SESSION_ID)
    final_turn.add_argument("--conversation-id", default=AFTER_FORGET_CONVERSATION_ID)
    final_turn.add_argument("--fact-id", default=HARMONICA_FACT_ID)
    final_turn.add_argument("--output-dir", default="")
    final_turn.set_defaults(func=cmd_final_turn)

    prove = sub.add_parser("prove-boundaries")
    add_run(prove)
    prove.add_argument("--session-id", default="")
    prove.set_defaults(func=cmd_prove_boundaries)

    stop = sub.add_parser("stop")
    stop.add_argument("--run-root", required=True)
    stop.set_defaults(func=b2.cmd_stop)
    return parser


def main(argv: list[str] | None = None) -> None:
    b2._configure_utf8_stdio()
    raw = argv if argv is not None else sys.argv[1:]
    if any(token.startswith("--api-key") or token.startswith("--credential") for token in raw):
        b2._fail("credential_on_cli", "credentials must not be passed on the command line")
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
