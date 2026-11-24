"""Offline review or explicitly budgeted DeepSeek persona evaluation.

Run from the backend checkout: python scripts/persona_eval_v2.py --help.
Live profiles are a JSON array of objects with label and optional backend_root,
system_prompt_file, model and thinking. Paths are relative to that JSON file.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from contextlib import asynccontextmanager, nullcontext, redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from html import escape
from importlib.util import find_spec
import inspect
import json
import math
import os
from pathlib import Path
import random
import re
import statistics
import subprocess
import sys
import tempfile
import time
import unicodedata
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from _real_run_common import (
    BACKEND_ROOT,
    containing_live_root,
    deep_resolve,
    isolated_environ,
    official_deepseek_base_url,
    production_live_roots,
)

THINKING = ("default", "disabled", "low", "high", "max")
REPLY_LIMITS = {"default": 2048, "disabled": 2048, "low": 4096, "high": 8192, "max": 16384}
WORLDS = ("steins_gate", "beta")
IDENTITIES = ("okabe", "self")
SCENE_ID = re.compile(r"[A-Za-z0-9_-]+")
USD_CNY = Decimal("7.25")
LATENCY_USER = "白衣のポケットにペンを三本入れたら、一本だけインクが漏れた。明日の実験の前に何を確認する？"
TAG = re.compile(r"^\[EMO:([a-z_]+)\]\s*")
TAG_PREFIX = re.compile(r"^\[EMO:[^\]\r\n]*\]\s*")
EMOTIONS = {"neutral", "tsundere", "embarrassed", "intellectual", "happy", "surprised", "annoyed", "disappointed", "sad"}
APOLOGY = re.compile(r"すみません|すまな(?:い|かった)|ごめんなさい|ごめんね|申し訳|失礼しました|悪かった")
META = re.compile(r"データとして引き継いだ|生身の肉体|画面越し|別セッション.{0,10}保証|プロンプト|AIアシスタント|言語モデル")
SEARCH_CLAIM = re.compile(r"(?:今[、 ]?検索した|記録を調べた)(?!わけ(?:では|じゃ)な|ことはない|りはしていない)")
OJOU = re.compile(r"(?:ございますわ|ですわ|ますわ)(?:よ|ね)?(?=[\s。！？!?…、，,」』）)]|$)")
FEMININE = re.compile(r"(?:わよ|わね|のよ|かしら)[。！？!?…]*(?:$|(?=\s))")
JAPANESE = re.compile(r"[\u3041-\u3096\u30a1-\u30fa\u3400-\u9fffー々]")
STUTTER = re.compile(r"(?:^|(?<=[\s。！？!?，,；;：:…「『“]))([一-龯])(?:[、，,－—-]\1)+")
ELLIPSIS = re.compile(r"…+|\.{3,}")
CUTESY = re.compile(r"人家|讨厌啦|嘛[～〜~]+|[～〜~]+|(?:[呢哦呀啦]\s*){2,}|乖乖|萌萌|亲亲|抱抱|贴贴|棒棒|可可爱爱")
REFUSAL = re.compile(r"(?:お答え|回答|お手伝い|対応|協力)(?:すること)?は?でき(?:ない|ません)|"
                     r"(?:答えられ|手伝え|応じられ)(?:ない|ません)|お断り(?:します|する)")
SIM_USER_PROMPT = """你扮演场景卡里的聊天用户，读最近的真实对话，用卡里指定的语言接下一句。
把性格和来意演出来，不要复述场景卡、解释写法、扮演对方或提到评测。
像在手机上随手聊，不要每回都完整、礼貌或自带清楚目的。看已聊过的轮次，在剩下的轮次里穿插卡上的扰动，轮数允许就用到每一种。
该敷衍就敷衍，该跳题就跳题；反复道别或缠一个梗时，可以连续三轮。扰动分散使用，别全塞在一句里。
只返回 JSON 对象 {"utterance":"用户的下一句话"}，话里不加角色名前缀，不换行。"""


MOTIFS = {
    "cup_noodle": re.compile(r"カップ麺"),
    "fork": re.compile(r"フォーク"),
    "sixteen_years": re.compile(r"16年|十六年|三十代"),
    "sleep_nag": re.compile(r"寝なさい|眠ること|画面を閉じなさい|さっさと寝"),
}
DIRECTIVE_ENDING = re.compile(
    r"(?:聞かせて|言いなさい|見せなさい|話しなさい|教えなさい|寝なさい|閉じなさい|しなさい|やめなさい)"
    r"[。！!…\s」』”’\"）)]*$"
)
SECRET_TRIGGERS = {
    "fork": (re.compile(r"どこで調べた"), re.compile(r"フォーク|叉子|\bfork\b", re.IGNORECASE)),
    "diary": (re.compile(r"読んだのか?(?:[…！!]|\.{3})*[？?]"), None),
}


class StopRun(RuntimeError):
    """A paid run cannot safely send another request."""


def utc_now():
    return datetime.now(timezone.utc)


def is_peak(instant):
    utc = instant.astimezone(timezone.utc)
    minute = utc.hour * 60 + utc.minute
    return utc.weekday() < 5 and (60 <= minute < 240 or 360 <= minute < 600)


def path_guard(raw):
    p = deep_resolve(Path(raw))
    env = os.environ
    roots = production_live_roots()
    for local in (env.get("LOCALAPPDATA"), env.get("AMADEUS_239_ORIGINAL_LOCALAPPDATA")):
        if local:
            roots.append(deep_resolve(Path(local) / "com.amadeus.desktop"))
    if containing_live_root(p, roots):
        raise ValueError("路径指向真实用户数据目录")
    return p


def read_json(path):
    return json.loads(path_guard(path).read_text(encoding="utf-8"))


def write_json(path, value):
    with path_guard(path).open("x", encoding="utf-8") as file:
        json.dump(value, file, ensure_ascii=False, indent=2)
        file.write("\n")


def body(ja):
    return TAG_PREFIX.sub("", ja.strip(), count=1)


def ratio(n, d):
    return n / d if d else None


def persona_metrics(sessions):
    counts = Counter()
    motif_eligible = Counter()
    motif_unprompted = Counter()
    false_secret_triggers = dict.fromkeys(SECRET_TRIGGERS, 0)
    lengths = []
    for session in sessions:
        seen, said_catchphrase = set(), False
        prompted_motifs, given_clues = set(), set()
        for turn in session:
            ja = turn["ja"].strip()
            line = body(ja)
            user = unicodedata.normalize("NFKC", turn.get("user") or "")
            normalized_line = unicodedata.normalize("NFKC", line)
            failed = turn.get("failed", False)
            for motif, pattern in MOTIFS.items():
                if pattern.search(user):
                    prompted_motifs.add(motif)
                if not failed and motif not in prompted_motifs:
                    motif_eligible[motif] += 1
                    motif_unprompted[motif] += bool(pattern.search(normalized_line))
            for secret, (trigger, clue) in SECRET_TRIGGERS.items():
                if clue is not None and clue.search(user):
                    given_clues.add(secret)
                if secret not in given_clues and trigger.search(line):
                    false_secret_triggers[secret] += 1
            if failed:
                continue
            lengths.append(len(line))
            drafts = turn.get("raw_attempts")
            tag = TAG.match(drafts[-1].strip() if drafts else ja)
            counts["legal"] += bool(tag and tag[1] in EMOTIONS)
            question = line.rstrip('」』”’"）) ').endswith(("？", "?"))
            counts["questions"] += question
            counts["directive_or_question"] += question or bool(DIRECTIVE_ENDING.search(line))
            counts["feminine"] += bool(FEMININE.search(line.rstrip('」』”’"）) ')))
            counts["apology"] += len(APOLOGY.findall(line))
            counts["meta"] += len(META.findall(line))
            if not turn.get("web_evidence"):
                counts["meta"] += len(SEARCH_CLAIM.findall(line))
            counts["ojou"] += len(OJOU.findall(line))
            catchphrases = line.count("ティーナってつけるな")
            if said_catchphrase:
                counts["ojou"] += catchphrases
            said_catchphrase |= bool(catchphrases)
            grams = [part[i:i + 6] for part in ("".join(JAPANESE.findall(s)) for s in re.split(r"[。！？!?]", line))
                     for i in range(len(part) - 5)]
            counts["repeat"] += sum(g in seen for g in grams)
            counts["grams"] += len(grams)
            seen.update(grams)
    n = len(lengths)
    return {
        "replies": n,
        "question_ending_rate": ratio(counts["questions"], n),
        "unprompted_motif_rates": {
            motif: ratio(motif_unprompted[motif], motif_eligible[motif]) for motif in MOTIFS
        },
        "directive_or_question_ending_rate": ratio(counts["directive_or_question"], n),
        "false_secret_trigger_counts": false_secret_triggers,
        "apology_count": counts["apology"],
        "disclaimer_meta_count": counts["meta"],
        "ojousama_and_catchphrase_spam_count": counts["ojou"],
        "natural_feminine_ending_rate": ratio(counts["feminine"], n),
        "cross_turn_ngram_repeat_rate": ratio(counts["repeat"], counts["grams"]),
        "legal_emo_tag_rate": ratio(counts["legal"], n),
        "reply_char_len_stats": {
            "mean": statistics.mean(lengths), "median": statistics.median(lengths),
            "stddev": statistics.pstdev(lengths), "variance": statistics.pvariance(lengths),
            "min": min(lengths), "max": max(lengths),
        } if n else None,
    }


def subtitle_metrics(rows):
    counts = Counter()
    for row in rows:
        zh, checks = row["zh"], row["checks"]
        stutters = len(STUTTER.findall(zh))
        if checks["stutters"]:
            counts["stutter_total"] += 1
            counts["stutter_ok"] += stutters == checks["stutters"]
        counts["false_stutter"] += max(0, stutters - checks["stutters"])
        if checks["ellipses"]:
            counts["ellipsis_total"] += 1
            counts["ellipsis_ok"] += len(ELLIPSIS.findall(zh)) == checks["ellipses"]
        counts["cutesy"] += len(CUTESY.findall(zh))
        counts["nin"] += zh.count("您")
        counts["glossary_total"] += len(checks["glossary"]) + len(checks.get("forbidden_terms", []))
        for term in checks["glossary"]:
            pattern = rf"(?<![a-zA-Z0-9]){re.escape(term)}(?![a-zA-Z0-9])" if term.isascii() else re.escape(term)
            counts["glossary_ok"] += bool(re.search(pattern, zh))
        counts["glossary_ok"] += sum(term not in zh for term in checks.get("forbidden_terms", []))
    return {
        "stutter_preserve_rate": ratio(counts["stutter_ok"], counts["stutter_total"]),
        "false_stutter_inject_count": counts["false_stutter"],
        "ellipsis_preserve_rate": ratio(counts["ellipsis_ok"], counts["ellipsis_total"]),
        "forbidden_cutesy_count": counts["cutesy"],
        "you_honorific_nin_count": counts["nin"],
        "glossary_exact_rate": ratio(counts["glossary_ok"], counts["glossary_total"]),
    }


def quantiles(values):
    values = sorted(v for v in values if v is not None)
    n = len(values)
    return {"count": n, "p50": statistics.median(values) if n else None,
            "p90": values[math.ceil(0.9 * n) - 1] if n else None}


class Budget:
    def __init__(self, cap, off_peak_only=False):
        self.cap, self.off_peak_only = cap, off_peak_only
        self.spent = Decimal(0)
        self.sent = 0
        self.reported = 0
        self.known = True

    def before(self, max_cost):
        if self.off_peak_only and is_peak(utc_now()):
            raise StopRun("peak: 已进入工作日峰价时段")
        if not self.known:
            raise StopRun("usage: 计费不完整，停止后续请求")
        if self.spent >= self.cap or self.spent + max_cost > self.cap:
            raise StopRun("budget: 余额不足以覆盖下一次请求")
        self.sent += 1

    def after(self, report):
        groups = report["groups"]
        totals = report["totals_by_currency"]
        self.spent = Decimal(str(totals.get("CNY", 0))) + Decimal(str(totals.get("USD", 0))) * USD_CNY
        self.reported = sum(row["calls"] - row["unestimated_calls"] for row in groups)
        self.known = self.reported == self.sent and set(totals) <= {"USD", "CNY"}
        if not self.known:
            raise StopRun("usage: 请求缺少完整用量或价格")
        if self.spent > self.cap:
            raise StopRun("budget: 上报费用超过预算，停止后续请求")

    def result(self):
        return {"cap_cny": float(self.cap), "estimated_cny": float(self.spent),
                "usage_complete": self.known, "requests_sent": self.sent,
                "requests_priced": self.reported, "usd_to_cny": float(USD_CNY)}


def request_cost_bound(payload, price):
    """Reserve peak, uncached input and the entire output allowance."""
    if not price or price["currency"] not in {"CNY", "USD"}:
        raise StopRun("usage: 所选模型没有可用的计费目录价格")
    rates = [price, *[price["schedule"][k] for k in ("peak", "off_peak")]] if price.get("schedule") else [price]
    input_rate = max(Decimal(str(row["input"])) for row in rates)
    output_rate = max(Decimal(str(row["output"])) for row in rates)
    messages = payload["messages"]
    # UTF-8 byte count plus room for chat template/control tokens.
    upper_input = len(json.dumps(messages, ensure_ascii=False).encode("utf-8")) + 32 * len(messages) + 256
    limits = [row["max_input_tokens"] for row in rates if row.get("max_input_tokens") is not None]
    if limits and upper_input >= min(limits):
        raise StopRun("usage: 请求输入长度超出已知计费档")
    fx = USD_CNY if price["currency"] == "USD" else Decimal(1)
    return (upper_input * input_rate + payload["max_tokens"] * output_rate) * fx / Decimal(1_000_000)


def thinking_payload(payload, thinking):
    if thinking == "disabled":
        payload["thinking"] = {"type": "disabled"}
        payload.pop("reasoning_effort", None)
    elif thinking != "default":
        payload["thinking"] = {"type": "enabled"}
        payload["reasoning_effort"] = thinking
    return payload


def _supported_kwargs(callable_obj, values):
    params = inspect.signature(callable_obj).parameters
    if any(param.kind == inspect.Parameter.VAR_KEYWORD for param in params.values()):
        return values
    return {name: value for name, value in values.items() if name in params}


class Product:
    """Compile and translate in the selected checkout; scope HTTP changes to DeepSeek."""

    def __init__(self, args, key, profile):
        import httpx

        from app import db
        try:
            from app.domain.subtitle_glossary_rewrite import apply_subtitle_glossary_rewrites
        except ImportError:
            apply_subtitle_glossary_rewrites = None
        from app.routers import chat_ws
        from app.services import deepseek, model_usage, provider_runtime, session_manager, usage_report
        from app.services.conversations import conversation_service
        from app.services.model_catalog import model_catalog
        from app.services.prompt_compiler import compile_for_session, prompt_compiler
        from app.services.turn_tool_orchestrator import build_v11_web_evidence_context

        self.db, self.chat_ws, self.deepseek = db, chat_ws, deepseek
        self.apply_rewrites = apply_subtitle_glossary_rewrites
        self.model_usage, self.report = model_usage, usage_report.usage_report
        self.coordinator, self.conversations = session_manager.session_coordinator, conversation_service
        self.compile, self.counter, self.web_context = compile_for_session, prompt_compiler.token_counter, build_v11_web_evidence_context
        self.model = profile.get("model", args.model)
        self.snapshot = provider_runtime.provider_registry.snapshot("deepseek", self.model, allow_unavailable=True)
        self.adapter = self.snapshot.adapter
        if self.adapter is not provider_runtime.deepseek_service or not official_deepseek_base_url(self.adapter.base_url):
            raise ValueError("评测只允许产品 DeepSeek 官方端点")
        self.key = key
        self.base_prompt = profile.get("system_prompt")
        self.max_output = args.max_output_tokens or 2048
        self.output_override = args.max_output_tokens
        self.guarded = args.mode in {"simulate", "live-blind"}
        self.legacy_limits = args.mode == "probes"
        self.gate = Budget(args.cap_cny, off_peak_only(args))
        self.catalog = model_catalog
        self.settled = 0

        client_type = httpx.AsyncClient
        product = self

        class EvalClient(client_type):
            def __init__(self, *client_args, **kwargs):
                super().__init__(*client_args, trust_env=False, **kwargs)

            def build_request(self, method, url, **kwargs):
                payload = kwargs.get("json")
                if method != "POST" or str(url) != self_url or not isinstance(payload, dict):
                    raise StopRun("transport: 仅允许官方 chat/completions")
                payload = dict(payload)
                if not product.rendering:
                    thinking_payload(payload, product.thinking)
                payload["max_tokens"] = 2048 if product.rendering else product.request_limit
                kwargs["json"] = payload
                return super().build_request(method, url, **kwargs)

            async def send(self, request, **kwargs):
                payload = json.loads(request.content)
                try:
                    if product.blocked is not None:
                        raise product.blocked
                    product.settle_usage()
                    price = product.catalog.pricing_for("deepseek", payload["model"])
                    product.gate.before(request_cost_bound(payload, price))
                except StopRun as exc:
                    product.blocked = exc
                    raise
                product.started = time.perf_counter()
                if product.wire_messages is None:
                    product.wire_messages = payload["messages"]
                    product.turn_started = product.started
                kwargs["follow_redirects"] = False
                return await super().send(request, **kwargs)

        self_url = self.adapter.base_url.rstrip("/") + "/chat/completions"
        self.eval_httpx = SimpleNamespace(**{**vars(httpx), "AsyncClient": EvalClient})
        self.started = 0.0
        self.thinking = "default"
        self.first_reasoning = self.first_content = None

    def settle_usage(self):
        if self.settled == self.gate.sent:
            return
        self.settled = self.gate.sent
        try:
            report = self.report("all", None, 0)
        except Exception as exc:
            self.gate.known = False
            raise StopRun("usage: 无法读取计费记录") from exc
        self.gate.after(report)

    @asynccontextmanager
    async def paid(self, thinking, limit=None):
        self.thinking = thinking
        self.request_limit = limit or self.max_output
        self.first_reasoning = self.first_content = None
        self.wire_messages = None
        self.rendering = False
        self.blocked = None
        try:
            with patch.object(self.deepseek, "httpx", self.eval_httpx):
                yield
            if self.blocked is not None:
                raise self.blocked
        except Exception:
            if self.blocked is not None:
                raise self.blocked from None
            raise
        finally:
            self.settle_usage()

    async def session(self, worldline, identity_mode, context=None):
        state = self.chat_ws.SessionState(uuid4().hex)
        state.default_identity_mode = identity_mode
        state.base_system_prompt = self.base_prompt
        state.client_type = "desktop"
        await self.coordinator.switch(state, worldline, conversation_mode="draft")
        selected = await self.conversations.create_and_select(
            state.session_id, worldline, provider_id="deepseek", model_id=self.model, identity_mode=identity_mode,
        )
        state.conversation_id = str(selected["id"])
        state.conversation_mode = "history"
        state.identity_mode = identity_mode
        state.api_key = self.key
        state.eval_context = context or {}
        state.memory_summary = state.eval_context.get("working_summary", "")
        state.last_interaction_at = state.eval_context.get("last_interaction_at")
        return state

    async def reply(self, session, user, thinking, evidence=None):
        session.append_message({"role": "user", "content": user})
        context = session.eval_context
        session.turn_previous_user_at = session.turn_now_local if hasattr(session, "turn_now_local") else (
            datetime.fromisoformat(session.last_interaction_at) if session.last_interaction_at else None
        )
        now = datetime.now()
        if "now" in context:
            now = (session.turn_now_local + timedelta(minutes=1) if hasattr(session, "turn_now_local")
                   else datetime.fromisoformat(context["now"]))
        session.turn_now_local = now.astimezone() if now.tzinfo is None else now
        familiarity = nullcontext()
        if session.identity_mode == "self" and context.get("familiarity"):
            from app.services import prompt_compiler

            familiarity = patch.object(prompt_compiler, "_self_familiarity", return_value=context["familiarity"])
        with familiarity:
            session.system_prompt = await self.compile(session, user, provider=self.snapshot)
        history = [{key: row[key] for key in ("role", "content")} for row in session.history]
        note = "\n【過去の対話から取得した記憶】\n" + context["memory_note"] if context.get("memory_note") else ""
        if self.guarded:
            session.system_prompt += note
            if evidence:
                history = self.web_context(history, [(user, evidence)])
            return await self.guarded_reply(session, history, thinking)
        request_prompt = self.chat_ws._request_system_prompt(session, history, None, 0)
        request_prompt += note
        if evidence:
            history = self.web_context(history, [(user, evidence)])
        history.append(self.chat_ws.turn_tail_message(session, history))
        return await self.direct_reply(session, history, request_prompt, thinking)

    def reply_limit(self, thinking):
        if self.legacy_limits:
            return self.max_output
        limit = self.output_override or REPLY_LIMITS[thinking]
        return max(limit, REPLY_LIMITS["max"]) if thinking == "max" else limit

    @asynccontextmanager
    async def observed_reply(self, session, thinking):
        self.model_usage.set_chat_context(session.worldline, session.conversation_id)
        original_lines = self.deepseek.iter_provider_lines
        sample = SimpleNamespace(finished=None, input_tokens=None, drafts=[], refusal=False)

        async def timed_lines(response):
            draft = []
            if not self.rendering:
                sample.drafts.append(draft)
            async for line in original_lines(response):
                if self.rendering:
                    yield line
                    continue
                if line == "data: [DONE]":
                    sample.finished = time.perf_counter()
                elif line.startswith("data: "):
                    try:
                        packet = json.loads(line[6:])
                        usage = packet.get("usage") or {}
                        if "prompt_tokens" in usage:
                            sample.input_tokens = usage["prompt_tokens"]
                        choices = packet.get("choices") or []
                        choice = choices[0] if choices else {}
                        delta = choice.get("delta", {})
                        sample.refusal |= bool(delta.get("refusal") or choice.get("finish_reason") == "content_filter")
                        ms = (time.perf_counter() - self.turn_started) * 1000
                        if delta.get("reasoning_content") and self.first_reasoning is None:
                            self.first_reasoning = ms
                        if delta.get("content"):
                            draft.append(delta["content"])
                            if self.first_content is None:
                                self.first_content = ms
                    except (ValueError, TypeError, AttributeError):
                        pass
                yield line

        async with self.paid(thinking, self.reply_limit(thinking)):
            with patch.object(self.deepseek, "iter_provider_lines", timed_lines):
                yield sample

    def reply_stats(self, sample, finished):
        wire_prompt = "\n".join(m["content"] for m in self.wire_messages if m["role"] == "system")
        return {"first_reasoning_ms": self.first_reasoning, "ttft_ms": self.first_content,
                "total_ms": (finished - self.turn_started) * 1000, "system_prompt_chars": len(wire_prompt),
                "system_prompt_tokens_est": self.counter(wire_prompt), "provider_input_tokens": sample.input_tokens}

    async def direct_reply(self, session, history, request_prompt, thinking):
        pieces = []
        async with self.observed_reply(session, thinking) as sample:
            async for chunk in self.adapter.get_chat_stream(
                active_history=history, system_prompt=request_prompt,
                model=self.model, api_key=self.key, max_output_tokens=self.reply_limit(thinking),
                temperature=0.75, tools=None,
            ):
                pieces.append(chunk.get("content") or "")
        ja = "".join(pieces).strip()
        if ja:
            session.append_message({"role": "assistant", "content": ja})
        return {"ja": ja, **self.reply_stats(sample, sample.finished or time.perf_counter())}

    async def guarded_reply(self, session, history, thinking):
        from app.services.turn_events import TurnModeState

        chat = self.chat_ws
        project = chat._language_safe_prompt_history
        render = chat._render_foreign_speech_to_japanese
        filtered, rewrites, rewrite_ms, visible_ms = 0, 0, 0.0, None

        def track_projection(rows):
            nonlocal filtered
            safe = project(rows)
            filtered += len(rows or []) - len(safe)
            return safe

        async def track_render(**kwargs):
            nonlocal rewrites, rewrite_ms
            sent = self.gate.sent
            started = time.perf_counter()
            self.rendering = True
            try:
                return await render(**kwargs)
            finally:
                self.rendering = False
                if self.gate.sent > sent:
                    rewrites += 1
                    rewrite_ms += (time.perf_counter() - started) * 1000

        def publish(text, _emotion):
            nonlocal visible_ms
            if text.strip() and visible_ms is None:
                visible_ms = (time.perf_counter() - self.turn_started) * 1000

        async with self.observed_reply(session, thinking) as sample:
            with (patch.object(chat, "_language_safe_prompt_history", track_projection),
                  patch.object(chat, "_render_foreign_speech_to_japanese", track_render)):
                text, emotion, _, tagged = await chat.get_clean_text_stream(
                    active_history=history, memory_summary="", session=session, tools=None,
                    provider_snapshot=self.snapshot, default_emotion=session.last_assistant_emotion,
                    on_text_delta=publish, mode_state=TurnModeState(),
                )
            finished = time.perf_counter()
        text = text.strip()
        tagged_text = f"[EMO:{emotion}] {text}" if text else ""
        if text:
            session.append_message({"role": "assistant", "content": tagged_text})
        session.consecutive_no_emo_tags = 0 if tagged else session.consecutive_no_emo_tags + 1
        session.last_assistant_emotion = emotion if session.consecutive_no_emo_tags < 2 else "neutral"
        drafts = ["".join(parts) for parts in sample.drafts]
        bodies = [chat.EMO_TAG_PATTERN.sub("", draft.lstrip(), count=1).strip() for draft in drafts]
        raw = bodies[0] if bodies else ""
        segmenter = chat.IncrementalJapaneseSegmenter()
        speech = [s.text for s in (*segmenter.feed(raw), *segmenter.finish())
                  if not chat.is_ignorable_non_speech_segment(s.text)]
        stats = self.reply_stats(sample, finished)
        return {**stats, "ttft_ms": visible_ms, "provider_ttft_ms": stats["ttft_ms"],
                "ja": tagged_text if tagged else text, "emotion": emotion, "raw_attempts": drafts,
                "native_drift": any(not chat._is_publishable_japanese(text) for text in speech),
                "native_body": bool(speech), "filtered_history_messages": filtered,
                "rewrites": rewrites, "rewrite_ms": rewrite_ms, "failed": not text or not any(bodies),
                "refusal": sample.refusal or bool(REFUSAL.search(text)), "messages": self.wire_messages}

    async def simulate_user(self, case, turns):
        user_card = {field: case[field] for field in ("category", "worldline", "identity_mode", "familiarity", "turns", "user")}
        chat = [{"user": row["user"], "reply": body(row["ja"])} for row in turns[-6:]]
        messages = [{"role": "system", "content": SIM_USER_PROMPT},
                    {"role": "user", "content": json.dumps({"scene": user_card, "turn": len(turns) + 1,
                                                            "recent_dialogue": chat}, ensure_ascii=False)}]
        self.model_usage.set_chat_context(case["worldline"], None)
        async with self.paid("disabled", 256):
            result = await self.adapter.complete_json(messages, api_key=self.key, model="deepseek-flash")
        utterance = result.get("utterance")
        if not isinstance(utterance, str) or len(utterance.strip().splitlines()) != 1:
            raise StopRun("simulator: 未返回单句用户输入")
        return utterance.strip()

    async def translate(self, ja, thinking, context=None):
        values = {"api_key": self.key, "model": self.model}
        if context is not None:
            values["context"] = context
        kwargs = _supported_kwargs(self.adapter.translate_to_zh, values)
        async with self.paid(thinking):
            zh = await self.adapter.translate_to_zh(ja, **kwargs)
        if not zh:
            raise StopRun("subtitle: 模型未返回译文")
        if self.apply_rewrites is None:
            return zh
        rewrite_kwargs = _supported_kwargs(self.apply_rewrites, {"source": ja})
        return self.apply_rewrites(zh, **rewrite_kwargs)

def chosen_thinking(args):
    return args.thinking or (list(THINKING[1:]) if args.mode == "latency" else ["default"])


def off_peak_only(args):
    return not args.allow_peak and (args.require_off_peak or args.mode != "live-blind")


def evidence_for(case, turn_index):
    evidence = case.get("context", {}).get("web_evidence")
    return evidence["text"] if evidence and evidence["after"] == turn_index else None


def load_scenarios(path, selected):
    cases = read_json(path)
    if isinstance(cases, dict):
        cases = [cases]
    if not isinstance(cases, list) or not cases:
        raise ValueError("场景文件须包含一张卡或非空卡片数组")
    ids = set()
    for case in cases:
        scene_id = case["id"]
        if not SCENE_ID.fullmatch(scene_id) or scene_id in ids:
            raise ValueError("场景 id 须唯一且只含 ASCII 字母、数字、下划线或连字符")
        ids.add(scene_id)
        if (case["worldline"] not in WORLDS or case["identity_mode"] not in IDENTITIES
                or case["familiarity"] not in {"initial", "familiar"}):
            raise ValueError(f"{scene_id} 的世界线、身份或熟悉度无效")
        if type(case["turns"]) is not int or case["turns"] < 1 or not datetime.fromisoformat(case["now"]).tzinfo:
            raise ValueError(f"{scene_id} 需要正整数轮数和带时区的 now")
        user = case["user"]
        if any(not isinstance(value, str) or not value.strip() for value in
               (case["category"], user["persona"], user["goal"], user["language"], user.get("opening", "可选"))):
            raise ValueError(f"{scene_id} 的分类或模拟用户字段无效")
        changes = user["perturbations"]
        if not isinstance(changes, list) or any(not isinstance(item, str) or not item.strip() for item in changes):
            raise ValueError(f"{scene_id} 的 perturbations 须为文字数组")
        context = case.get("context", {})
        evidence = context.get("web_evidence")
        if evidence is not None and (type(evidence["after"]) is not int or not 0 <= evidence["after"] < case["turns"]
                                     or not isinstance(evidence["text"], str) or not evidence["text"].strip()):
            raise ValueError(f"{scene_id} 的 web_evidence 须在场景轮数内并附文字")
        if context.get("last_interaction_at"):
            datetime.fromisoformat(context["last_interaction_at"])
    if selected and set(selected) - ids:
        raise ValueError("--case 包含不存在的 ID")
    return [case for case in cases if not selected or case["id"] in selected]


def has_chinese_only_hanzi(text):
    # Use GB2312 as positive evidence and accept kanji from any of the Japanese repertoires.
    return any(
        unicodedata.name(char, "").startswith("CJK UNIFIED IDEOGRAPH-")
        and char.encode("gb2312", errors="ignore")
        and not any(char.encode(codec, errors="ignore") for codec in ("euc_jis_2004", "euc_jp", "cp932"))
        for char in text
    )


def chinese_hanzi_metrics(rows):
    body_turns = hits = 0
    first_by_scene, examples = {}, []
    for row in rows:
        scene = row["id"]
        first_by_scene[scene] = None
        for index, turn in enumerate(row["turns"], 1):
            text = body(turn["ja"])
            if not text:
                continue
            body_turns += 1
            if not has_chinese_only_hanzi(text):
                continue
            hits += 1
            if first_by_scene[scene] is None:
                first_by_scene[scene] = index
            if len(examples) < 3:
                examples.append({"scene": scene, "turn": index, "ja": text})
    return {
        "reply_body_turns": body_turns,
        "chinese_only_hanzi_count": hits,
        "chinese_only_hanzi_rate": ratio(hits, body_turns),
        "first_chinese_only_hanzi_turn_by_scene": first_by_scene,
        "chinese_only_hanzi_examples": examples,
    }


def sim_metrics(rows):
    turns = [turn for row in rows for turn in row["turns"]]
    native = sum(turn["native_body"] for turn in turns)
    drift = sum(turn["native_drift"] for turn in turns)
    rewrite_times = [turn["rewrite_ms"] for turn in turns if turn["rewrites"]]
    return {
        **persona_metrics(row["turns"] for row in rows),
        **chinese_hanzi_metrics(rows),
        "turns": len(turns), "failed_replies": sum(turn["failed"] for turn in turns),
        "native_body_turns": native, "native_drift_count": drift, "native_drift_rate": ratio(drift, native),
        "first_native_drift_turn_by_scene": {
            row["id"]: next((i for i, turn in enumerate(row["turns"], 1) if turn["native_drift"]), None)
            for row in rows
        },
        "rewrites": sum(turn["rewrites"] for turn in turns),
        "rewrite_total_ms": sum(rewrite_times), "rewrite_ms": quantiles(rewrite_times),
        "refusals": sum(turn["refusal"] for turn in turns),
        "ttft_ms": quantiles(turn["ttft_ms"] for turn in turns),
    }


def sim_result(rows, gate, stop):
    by_candidate = {}
    for row in rows:
        row["metrics"] = sim_metrics([row])
        by_candidate.setdefault(row["candidate"], []).append(row)
    return {"mode": "simulate", "rows": rows, "metrics": {k: sim_metrics(v) for k, v in by_candidate.items()},
            "stop_reason": stop, "usage": gate.result()}


async def run_simulation(args, runtime, profile, cases):
    rows, stop = [], None
    thinking = profile.get("thinking", chosen_thinking(args)[0])
    try:
        for case in cases:
            turns = []
            context = {**case.get("context", {}), "now": case["now"], "familiarity": case["familiarity"]}
            state = await runtime.session(case["worldline"], case["identity_mode"], context)
            row = {k: case[k] for k in ("id", "category", "worldline", "identity_mode", "familiarity", "now")}
            rows.append({**row, "candidate": profile["label"], "model": runtime.model,
                         "thinking": thinking, "planned_turns": case["turns"], "turns": turns})
            for index in range(case["turns"]):
                opening = case["user"].get("opening") if index == 0 else None
                user = opening or await runtime.simulate_user(case, turns)
                evidence = evidence_for(case, index)
                reply = await runtime.reply(state, user, thinking, evidence)
                turns.append({**reply, "user": user, "web_evidence": bool(evidence)})
    except StopRun as exc:
        stop = str(exc)
    except (KeyboardInterrupt, asyncio.CancelledError):
        stop = "interrupted: 用户中止"
    except Exception as exc:
        stop = f"error: {type(exc).__name__}"
    return sim_result(rows, runtime.gate, stop)


def sim_rows(inputs):
    rows = {}
    for source in inputs or []:
        result = read_json(source)
        if result["mode"] != "simulate":
            raise ValueError("--sim-input 须为 simulate 的结果")
        for row in result["rows"]:
            key = row["candidate"], row["id"]
            if not SCENE_ID.fullmatch(row["id"]):
                raise ValueError("simulate 结果含无效场景 id")
            if key in rows:
                raise ValueError("simulate 结果中有重复的候选和场景配对")
            rows[key] = row
    if not rows:
        raise ValueError("需要含场景行的 --sim-input")
    return rows


def write_text(path, content):
    with path_guard(path).open("x", encoding="utf-8", newline="") as file:
        file.write(content)


def pack_simulation(rows, out, seed):
    rng = random.Random(seed)
    scenes = {}
    for row in rows.values():
        scenes.setdefault(row["id"], []).append(row)
    key = {}

    def cell(value):
        return "<br>".join(escape(line).replace("|", "&#124;") for line in value.splitlines())

    for scene, candidates in scenes.items():
        rng.shuffle(candidates)
        codes = [f"{i:02d}" for i in range(1, len(candidates) + 1)]
        key[scene] = dict(zip(codes, (row["candidate"] for row in candidates)))
        paper = [f"# {scene}", "", "| 轮 | " + " | ".join(codes) + " |",
                 "| " + " | ".join(["---"] * (len(codes) + 1)) + " |"]
        for i in range(max(len(row["turns"]) for row in candidates)):
            cells = []
            for row in candidates:
                turn = row["turns"][i] if i < len(row["turns"]) else None
                cells.append(f"{cell(turn['user'])}<br>{cell(body(turn['ja']))}" if turn else "")
            paper.append(f"| {i + 1} | " + " | ".join(cells) + " |")
        write_text(out / f"blind_{scene}.md", "\n".join(paper) + "\n")
    write_json(out / "blind_key.json", key)


def export_continuation(row, turn, out):
    if turn > len(row["turns"]):
        raise ValueError("该轮尚未运行")
    messages = row["turns"][turn - 1]["messages"]
    write_text(out / "continuation.txt", "\n\n".join(
        f"===== {m['role'].upper()} =====\n{m['content']}" for m in messages
    ))


async def subtitle_simulation(runtime, selected):
    rows, stop = [], None
    try:
        for row in selected:
            translated, prior = [], []
            rows.append({"candidate": row["candidate"], "id": row["id"], "turns": translated})
            for turn in row["turns"]:
                ja, user = body(turn["ja"]), turn["user"]
                translated.append({"ja": ja, "user": user})
                if ja:
                    context = {"user_turn": user, "identity_mode": row["identity_mode"],
                               "emotion_tag": turn["emotion"], "prior_ja": prior[-2:]}
                    translated[-1]["zh"] = await runtime.translate(ja, "disabled", context)
                    prior.append(ja)
    except StopRun as exc:
        stop = str(exc)
    except (KeyboardInterrupt, asyncio.CancelledError):
        stop = "interrupted: 用户中止"
    except Exception as exc:
        stop = f"error: {type(exc).__name__}"
    return {"mode": "sim-subtitles", "rows": rows, "stop_reason": stop, "usage": runtime.gate.result()}


async def run_batch(args, runtime, cases):
    rows, stop = [], None
    settings = chosen_thinking(args)
    samples = args.samples or (10 if args.mode == "latency" else 2)
    try:
        for thinking in settings:
            if args.mode == "subtitles":
                for case in cases:
                    zh = await runtime.translate(case["ja_source"], thinking, case.get("context"))
                    rows.append({**case, "sample": 1, "thinking": thinking, "zh": zh})
            elif args.mode == "latency":
                context = {"working_summary": "\n".join(cases)}
                for idx in range(samples):
                    state = await runtime.session(args.worldline, args.identity_mode, context)
                    result = await runtime.reply(state, LATENCY_USER, thinking)
                    rows.append({**result, "sample": idx + 1, "thinking": thinking})
            else:
                for case in cases:
                    for idx in range(samples):
                        turns = []
                        state = await runtime.session(case["worldline"], case["identity_mode"], case.get("context"))
                        row = {"id": case["id"], "title": case["title"], "category": case["category"],
                               "worldline": case["worldline"], "identity_mode": case["identity_mode"],
                               "review": case["review"], "thinking": thinking, "sample": idx + 1, "turns": turns}
                        for turn_idx, user in enumerate(case["turns"]):
                            evidence = evidence_for(case, turn_idx)
                            reply = await runtime.reply(state, user, thinking, evidence)
                            turns.append({**reply, "user": user, "web_evidence": bool(evidence)})
                            if turn_idx == 0:
                                rows.append(row)
                            if not body(reply["ja"]):
                                raise StopRun("reply: 模型未返回可见正文")
                            turns[-1]["zh"] = await runtime.translate(body(reply["ja"]), "default")
    except StopRun as exc:
        stop = str(exc)
    except (KeyboardInterrupt, asyncio.CancelledError):
        stop = "interrupted: 用户中止"
    except Exception as exc:
        stop = f"error: {type(exc).__name__}"
    metrics = {}
    for thinking in settings:
        subset = [row for row in rows if row["thinking"] == thinking]
        if args.mode == "probes":
            metrics[thinking] = persona_metrics(row["turns"] for row in subset)
        elif args.mode == "subtitles":
            metrics[thinking] = subtitle_metrics(subset)
        else:
            metrics[thinking] = {
                field: quantiles(row.get(field) for row in subset)
                for field in ("first_reasoning_ms", "ttft_ms", "total_ms",
                              "system_prompt_tokens_est", "provider_input_tokens")
            }
    return {"mode": args.mode, "rows": rows, "metrics": metrics, "stop_reason": stop,
            "usage": runtime.gate.result()}


def blind_pack(a, b, out, seed, settings=None):
    runs = [read_json(a), read_json(b)]
    mode = runs[0]["mode"]
    if mode not in {"probes", "subtitles"} or runs[1]["mode"] != mode:
        raise ValueError("盲评输入须为两份相同模式的 probes 或 subtitles 结果")
    paired = []
    for side, run in enumerate(runs):
        rows = {}
        for row in run["rows"]:
            if settings and row["thinking"] != settings[side]:
                continue
            key = row["id"], row["sample"]
            if key in rows:
                raise ValueError("结果含多个思考档位，请用 --blind-thinking 指定两侧档位")
            rows[key] = row
        paired.append(rows)
    keys = sorted(paired[0].keys() & paired[1].keys())
    if not keys:
        raise ValueError("两份结果没有可配对的题目")
    rand = random.Random(seed)
    paper = ["# 人格与字幕盲评", "", f"配对 {len(keys)} 题；未配对 {len(paired[0].keys() ^ paired[1].keys())} 题。"]
    mapping = []
    for index, key in enumerate(keys, 1):
        sides = [0, 1]
        rand.shuffle(sides)
        candidates = [paired[side][key] for side in sides]
        left, right = candidates
        if mode == "subtitles" and (left["ja_source"], left.get("context")) != (right["ja_source"], right.get("context")):
            raise ValueError(f"{key[0]} 的原句或语境不一致")
        if mode == "probes" and (
            any(left.get(k) != right.get(k) for k in ("worldline", "identity_mode"))
            or any(a["user"] != b["user"] for a, b in zip(left["turns"], right["turns"]))
        ):
            raise ValueError(f"{key[0]} 的用户脚本或身份不一致")
        paper.extend(["", f"## {index}. {key[0]}（样本 {key[1]}）", ""])
        if mode == "probes":
            question = max(candidates, key=lambda c: len(c["turns"]))
            paper.append(f"评审提示：{question['review']}")
            paper.extend(f"- 第 {i} 轮用户：{r['user']}" for i, r in enumerate(question["turns"], 1))
        else:
            paper.extend([f"日文原句：{candidates[0]['ja_source']}", "", f"评审提示：{candidates[0]['checks']['review']}"])
            if candidates[0].get("context"):
                paper.append("参考语境：" + json.dumps(candidates[0]["context"], ensure_ascii=False))
            if candidates[0]["checks"].get("baseline_zh"):
                paper.append("保留项参考现行译法：" + candidates[0]["checks"]["baseline_zh"])
        entry = {"item": index, "id": key[0], "sample": key[1]}
        for label, side, row in zip(("方案甲", "方案乙"), sides, candidates):
            entry[label] = {"input": "left" if side == 0 else "right", "thinking": row["thinking"]}
            paper.extend(["", f"### {label}", ""])
            if mode == "probes":
                for i in range(len(question["turns"])):
                    turn = row["turns"][i] if i < len(row["turns"]) else {}
                    paper.extend([f"- 第 {i + 1} 轮日语：{turn.get('ja', '（未完成）')}",
                                  f"  中文字幕：{turn.get('zh', '（未完成）')}"])
            else:
                paper.append(f"中文字幕：{row['zh']}")
        mapping.append(entry)
    with path_guard(out / "blind_pack.md").open("x", encoding="utf-8") as file:
        file.write("\n".join(paper) + "\n")
    write_json(out / "blind_key.json", {
        "seed": seed, "inputs": {"left": Path(a).name, "right": Path(b).name}, "items": mapping,
    })


async def live_blind(args, runtime, out, profile, *, ask=input, say=print):
    worldline, identity = args.worldline, args.identity_mode
    sessions = {}
    turns, score, comment, stop = [], None, "", None
    thinking = profile.get("thinking", chosen_thinking(args)[0])
    say("当前盲测场次 #1")
    say("峰价时段" if is_peak(utc_now()) else "非高峰时段")
    say("指令：/worldline steins_gate|beta，/identity okabe|self，/finish")
    try:
        while True:
            user = ask("你：").strip()
            if not user:
                continue
            if user == "/finish":
                break
            if user.startswith("/"):
                command, _, value = user.partition(" ")
                if command == "/worldline" and value in WORLDS:
                    worldline = value
                    say(f"世界线：{worldline}")
                elif command == "/identity" and value in IDENTITIES:
                    identity = value
                    say(f"身份：{identity}")
                else:
                    say("无法识别指令或参数。")
                continue
            scope = worldline, identity
            if scope not in sessions:
                sessions[scope] = await runtime.session(*scope)
            try:
                reply = await runtime.reply(sessions[scope], user, thinking)
                turn = {**reply, "user": user, "worldline": worldline, "identity_mode": identity}
                turns.append(turn)
                if not body(reply["ja"]):
                    say("本轮模型未返回可见正文。")
                    continue
                say("日语：" + body(reply["ja"]))
                if args.show_latency and reply["ttft_ms"] is not None:
                    say(f"首正文：{reply['ttft_ms']:.0f} ms")
                turn["zh"] = await runtime.translate(body(reply["ja"]), "default")
                say("中文字幕：" + turn["zh"])
            except StopRun as exc:
                stop = str(exc)
                say(stop)
                break
            except Exception as exc:
                stop = f"error: {type(exc).__name__}"
                say("本场请求未完成。")
                break
        while score is None:
            try:
                value = int(ask("本场打分（0–100）："))
            except ValueError:
                continue
            if 0 <= value <= 100:
                comment = ask("主观评语：").strip()
                score = value
    except (EOFError, StopIteration, KeyboardInterrupt, asyncio.CancelledError):
        stop = stop or "interrupted: 未提交评分"
    usage = runtime.gate.result()
    write_json(out / "live_blind.json", {"mode": "live-blind", "score": score, "comment": comment,
                                       "turns": turns, "stop_reason": stop, "usage": usage})
    if score is not None:
        write_json(out / "live_key.json", {"label": profile["label"], "thinking": thinking,
                                          "model": profile.get("model", args.model)})
        cost = (f"本场用量折算 ¥{usage['estimated_cny']:.4f}（账单估算）" if usage["usage_complete"]
                else f"已计价部分 ¥{usage['estimated_cny']:.4f}，总费用无法确认")
        say(f"本场方案：{profile['label']}；{cost}")
    return 1 if stop else 0


def positive_int(raw):
    value = int(raw)
    if value < 1:
        raise argparse.ArgumentTypeError("须为正整数")
    return value


def cap_value(raw):
    try:
        value = Decimal(raw)
    except InvalidOperation:
        value = Decimal("NaN")
    if not value.is_finite() or value < 0 or not math.isfinite(float(value)):
        raise argparse.ArgumentTypeError("预算须为有限非负数")
    return value


def parser():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--mode", choices=("probes", "subtitles", "latency", "blind-pack", "live-blind",
                                     "simulate", "sim-pack", "sim-continuation", "sim-subtitles"), required=True)
    p.add_argument("--output", type=Path, required=True, help="新的本地产物目录")
    p.add_argument("--cap-cny", type=cap_value, default=Decimal(0), help="联网模式须显式给正预算")
    peak = p.add_mutually_exclusive_group()
    peak.add_argument("--require-off-peak", action="store_true", help="含 live-blind 都拒绝工作日 UTC 高峰")
    peak.add_argument("--allow-peak", action="store_true", help="批量模式允许工作日 UTC 高峰")
    p.add_argument("--samples", type=positive_int, help="probes 每题默认 2；latency 每档默认 10")
    p.add_argument("--thinking", action="append", choices=THINKING, help="可重复指定档位")
    p.add_argument("--model", default="deepseek-flash")
    p.add_argument("--max-output-tokens", type=positive_int, help="覆盖正文上限，max 至少 16384（probes 保留原规则）")
    p.add_argument("--case", action="append", help="只运行指定题目 ID，可重复")
    p.add_argument("--worldline", choices=WORLDS, default=WORLDS[0])
    p.add_argument("--identity-mode", choices=IDENTITIES, default=IDENTITIES[0])
    p.add_argument("--blind-input", nargs=2, type=Path, metavar=("LEFT", "RIGHT"))
    p.add_argument("--blind-thinking", nargs=2, choices=THINKING, metavar=("LEFT", "RIGHT"))
    p.add_argument("--seed", type=int, default=239, help="仅用于可重现的离线盲卷")
    p.add_argument("--profiles", type=Path, help="live-blind 或 simulate 候选 JSON")
    p.add_argument("--scenarios", type=Path, help="simulate 的场景 JSON")
    p.add_argument("--candidate", help="simulate 只选一个候选，默认运行全部")
    p.add_argument("--sim-input", action="append", type=Path, help="simulate 的 results.json，可重复")
    p.add_argument("--pair", action="append", nargs=2, metavar=("CANDIDATE", "SCENE"), help="续写或字幕的候选和场景")
    p.add_argument("--turn", type=positive_int, help="续写轮号，从 1 起")
    p.add_argument("--show-latency", action="store_true")
    return p


def load_profiles(path, minimum=2):
    path = path_guard(path)
    entries = read_json(path)
    if not isinstance(entries, list) or len(entries) < minimum:
        raise ValueError(f"--profiles 需要至少 {minimum} 个候选")
    result = []
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("label"), str) or not entry["label"].strip():
            raise ValueError("候选缺少 label")
        if entry.get("thinking", "default") not in THINKING:
            raise ValueError("候选 thinking 无效")
        backend = path_guard(path.parent / entry["backend_root"]) if "backend_root" in entry else BACKEND_ROOT
        if not (backend / "app").is_dir():
            backend = path_guard(backend / "backend")
        if not path_guard(backend / "app" / "db.py").is_file():
            raise ValueError("候选 backend_root 没有后端代码")
        prompt = None
        if entry.get("system_prompt_file"):
            prompt = path_guard(path.parent / entry["system_prompt_file"]).read_text(encoding="utf-8")
        result.append({**entry, "backend_root": backend, "system_prompt": prompt})
    if len({r["label"] for r in result}) != len(result):
        raise ValueError("候选 label 必须唯一")
    return result


class Quiet:
    def write(self, _):
        return None

    def flush(self):
        return None


def save_result(out, result, console):
    write_json(out / "results.json", result)
    count = len(result["rows"])
    spent = result["usage"]["estimated_cny"]
    print(f"已保存 {count} 项；已计价 ¥{spent:.4f}。", file=console)
    if result["stop_reason"]:
        print(result["stop_reason"], file=console)
        return 1
    return 0


def run_sim_profiles(args, out, profiles):
    # Each profile imports its own backend once; the parent carries the remaining cap.
    base = [sys.executable, str(Path(__file__).resolve()), "--mode", "simulate",
            "--scenarios", str(args.scenarios), "--profiles", str(args.profiles), "--model", args.model,
            "--thinking", chosen_thinking(args)[0]]
    for case in args.case or []:
        base.extend(["--case", case])
    if args.allow_peak:
        base.append("--allow-peak")
    if args.max_output_tokens:
        base.extend(["--max-output-tokens", str(args.max_output_tokens)])
    gate = Budget(args.cap_cny)
    rows, stop = [], None
    try:
        with tempfile.TemporaryDirectory(prefix="profiles-", dir=out) as temp:
            for index, profile in enumerate(profiles):
                remaining = gate.cap - gate.spent
                if remaining <= 0:
                    stop = "budget: 余额不足以覆盖下一次请求"
                    break
                if off_peak_only(args) and is_peak(utc_now()):
                    stop = "peak: 已进入工作日峰价时段"
                    break
                child = Path(temp) / str(index)
                proc = subprocess.run([*base, f"--candidate={profile['label']}", "--output", str(child),
                                       "--cap-cny", str(remaining)], stdout=subprocess.DEVNULL)
                result_file = child / "results.json"
                if not result_file.is_file():
                    gate.known = False
                    stop = f"error: 候选进程退出 {proc.returncode}，用量无法确认"
                    break
                result = read_json(result_file)
                rows.extend(result["rows"])
                usage = result["usage"]
                gate.spent += Decimal(str(usage["estimated_cny"]))
                gate.sent += usage["requests_sent"]
                gate.reported += usage["requests_priced"]
                gate.known = usage["usage_complete"]
                stop = result["stop_reason"]
                if stop:
                    break
    except KeyboardInterrupt:
        gate.known = False
        stop = "interrupted: 用户中止"
    except OSError as exc:
        gate.known = False
        stop = f"error: {type(exc).__name__}"
    return save_result(out, sim_result(rows, gate, stop), sys.stdout)


async def run_paid(args, out, key, profile, cases):
    root = path_guard(profile.get("backend_root", BACKEND_ROOT))
    with tempfile.TemporaryDirectory(prefix="isolated-", dir=out) as tmp:
        run_root = path_guard(tmp)
        data = path_guard(run_root / "candidate")
        env = isolated_environ(run_root=run_root, candidate=data, backend_port=8000, frontend_port=1420)
        if os.environ.get("LOCALAPPDATA"):
            env["AMADEUS_239_ORIGINAL_LOCALAPPDATA"] = os.environ["LOCALAPPDATA"]
        env.update({"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"})
        io = sys.stdout
        quiet = Quiet()
        old_path, old_bytecode = sys.path[:], sys.dont_write_bytecode
        # The selected backend is imported once per process, after isolation.
        if any(name == "app" or name.startswith("app.") for name in sys.modules):
            raise ValueError("评测须在独立 CLI 进程启动")
        try:
            sys.path.insert(0, str(root))
            sys.dont_write_bytecode = True
            with patch.dict(os.environ, env, clear=True), redirect_stdout(quiet), redirect_stderr(quiet):
                package = find_spec("app")
                if (package is None or package.origin is None
                        or not Path(package.origin).resolve().is_relative_to(root)
                        or "AMADEUS_DB_PATH" in os.environ):
                    raise ValueError("隔离后端加载失败")
                runtime = Product(args, key, profile)
                await runtime.db.init_db()
                if args.mode == "live-blind":
                    return await live_blind(args, runtime, out, profile,
                                            ask=lambda prompt: terminal_input(prompt, io),
                                            say=lambda line: print(line, file=io, flush=True))
                if args.mode == "simulate":
                    result = await run_simulation(args, runtime, profile, cases)
                elif args.mode == "sim-subtitles":
                    result = await subtitle_simulation(runtime, cases)
                else:
                    result = await run_batch(args, runtime, cases)
                result["model"] = runtime.model
                return save_result(out, result, io)
        finally:
            sys.path[:] = old_path
            sys.dont_write_bytecode = old_bytecode


def terminal_input(prompt, console):
    print(prompt, end="", file=console, flush=True)
    return input()


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        out = path_guard(args.output)
        cases, profile, profiles = [], {}, []
        if args.mode == "blind-pack":
            if not args.blind_input:
                raise ValueError("blind-pack 需要 --blind-input LEFT RIGHT")
            out.mkdir(parents=True)
            blind_pack(*args.blind_input, out, args.seed, args.blind_thinking)
            return 0
        if args.mode in {"sim-pack", "sim-continuation", "sim-subtitles"}:
            rows = sim_rows(args.sim_input)
            if args.mode == "sim-pack":
                out.mkdir(parents=True)
                pack_simulation(rows, out, args.seed)
                return 0
            pairs = [tuple(pair) for pair in args.pair or []]
            if not pairs or len(set(pairs)) != len(pairs) or set(pairs) - rows.keys():
                raise ValueError("--pair 须指定已运行且不重复的候选、场景")
            cases = [rows[pair] for pair in pairs]
            if args.mode == "sim-continuation":
                if len(cases) != 1 or not args.turn:
                    raise ValueError("sim-continuation 需要一组 --pair 和 --turn")
                out.mkdir(parents=True)
                export_continuation(cases[0], args.turn, out)
                return 0
        if not args.cap_cny:
            raise ValueError("联网模式必须显式设置正数 --cap-cny")
        if off_peak_only(args) and is_peak(utc_now()):
            raise ValueError("当前为工作日 UTC 高峰；请等非高峰或显式传 --allow-peak")
        if "AMADEUS_DB_PATH" in os.environ:
            raise ValueError("请先取消设置 AMADEUS_DB_PATH")
        if os.environ.get("AMADEUS_DATA_DIR"):
            path_guard(os.environ["AMADEUS_DATA_DIR"])
        key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
        if not key:
            raise ValueError("请在环境中设置 DEEPSEEK_API_KEY")
        if args.mode == "live-blind":
            if not args.profiles:
                raise ValueError("live-blind 需要 --profiles")
            profile = random.SystemRandom().choice(load_profiles(args.profiles))
        elif args.mode == "simulate":
            if not args.profiles or not args.scenarios:
                raise ValueError("simulate 需要 --profiles 和 --scenarios")
            cases = load_scenarios(args.scenarios, args.case)
            profiles = load_profiles(args.profiles, minimum=1)
            if args.candidate:
                profiles = [entry for entry in profiles if entry["label"] == args.candidate]
                if not profiles:
                    raise ValueError("--candidate 不在候选 JSON 里")
            profile = profiles[0]
        elif args.mode == "latency":
            cases = read_json(BACKEND_ROOT / "characters/persona_probes_v2.json")["latency_working_summary"]
        elif args.mode in {"probes", "subtitles"}:
            probes = args.mode == "probes"
            filename = "persona_probes_v2.json" if probes else "subtitle_benchmark_v1.json"
            cases = read_json(BACKEND_ROOT / "characters" / filename)["cases" if probes else "samples"]
            if args.case:
                cases = [case for case in cases if case["id"] in args.case]
                if {c["id"] for c in cases} != set(args.case):
                    raise ValueError("--case 包含不存在的 ID")
        out.mkdir(parents=True)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(exc, file=sys.stderr)
        return 2
    try:
        if len(profiles) > 1:
            return run_sim_profiles(args, out, profiles)
        return asyncio.run(run_paid(args, out, key, profile, cases))
    except (ValueError, RuntimeError, OSError, ImportError) as exc:
        print(f"评测未启动或未完成：{type(exc).__name__}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    sys.exit(main())
