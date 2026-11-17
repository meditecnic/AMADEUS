"""Offline review or explicitly budgeted DeepSeek persona evaluation.

Run from the backend checkout: python scripts/persona_eval_v2.py --help.
Live profiles are a JSON array of objects with label and optional backend_root,
system_prompt_file, model and thinking. Paths are relative to that JSON file.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from contextlib import asynccontextmanager, redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from importlib.util import find_spec
import json
import math
import os
from pathlib import Path
import random
import re
import statistics
import sys
import tempfile
import time
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
WORLDS = ("steins_gate", "beta")
IDENTITIES = ("okabe", "self")
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
    lengths = []
    for session in sessions:
        seen, said_catchphrase = set(), False
        for turn in session:
            ja = turn["ja"].strip()
            line = body(ja)
            lengths.append(len(line))
            tag = TAG.match(ja)
            counts["legal"] += bool(tag and tag[1] in EMOTIONS)
            counts["questions"] += line.rstrip('」』”’"）) ').endswith(("？", "?"))
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


def subtitle_context(messages, context):
    if not context:
        return messages
    # Keep the source envelope intact; the extra material is translation context.
    note = "【参考文脈（翻訳対象は <source> のみ）】\n" + json.dumps(context, ensure_ascii=False)
    return [*messages[:-1], {**messages[-1], "content": note + "\n\n" + messages[-1]["content"]}]


class Product:
    """Compile and translate in the selected checkout; scope HTTP changes to DeepSeek."""

    def __init__(self, args, key, profile):
        import httpx

        from app import db
        from app.routers import chat_ws
        from app.services import deepseek, model_usage, provider_runtime, session_manager, usage_report
        from app.services.conversations import conversation_service
        from app.services.model_catalog import model_catalog
        from app.services.prompt_compiler import compile_for_session, prompt_compiler
        from app.services.turn_tool_orchestrator import build_v11_web_evidence_context

        self.db, self.chat_ws, self.deepseek = db, chat_ws, deepseek
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
        self.max_output = args.max_output_tokens
        self.gate = Budget(args.cap_cny, off_peak_only(args))
        self.price = model_catalog.pricing_for("deepseek", self.model)

        client_type = httpx.AsyncClient
        product = self

        class EvalClient(client_type):
            def __init__(self, *client_args, **kwargs):
                super().__init__(*client_args, trust_env=False, **kwargs)

            def build_request(self, method, url, **kwargs):
                payload = kwargs.get("json")
                if method != "POST" or str(url) != self_url or not isinstance(payload, dict):
                    raise StopRun("transport: 仅允许官方 chat/completions")
                payload = thinking_payload(dict(payload), product.thinking)
                payload["max_tokens"] = product.max_output
                if not payload.get("stream"):
                    payload["messages"] = subtitle_context(payload["messages"], product.translation_context)
                kwargs["json"] = payload
                return super().build_request(method, url, **kwargs)

            async def send(self, request, **kwargs):
                payload = json.loads(request.content)
                try:
                    product.gate.before(request_cost_bound(payload, product.price))
                except StopRun as exc:
                    product.blocked = exc
                    raise
                product.started = time.perf_counter()
                product.wire_prompt = "\n".join(
                    m["content"] for m in payload["messages"] if m["role"] == "system"
                )
                kwargs["follow_redirects"] = False
                return await super().send(request, **kwargs)

        self_url = self.adapter.base_url.rstrip("/") + "/chat/completions"
        self.eval_httpx = SimpleNamespace(**{**vars(httpx), "AsyncClient": EvalClient})
        self.started = 0.0
        self.wire_prompt = ""
        self.thinking = "default"
        self.translation_context = None
        self.first_reasoning = self.first_content = None

    @asynccontextmanager
    async def paid(self, thinking, context=None):
        self.thinking, self.translation_context = thinking, context
        self.first_reasoning = self.first_content = None
        self.blocked = None
        sent = self.gate.sent
        try:
            with patch.object(self.deepseek, "httpx", self.eval_httpx):
                yield
        except Exception:
            if self.blocked is not None:
                raise self.blocked from None
            raise
        finally:
            if sent != self.gate.sent:
                try:
                    report = self.report("all", None, 0)
                except Exception as exc:
                    self.gate.known = False
                    raise StopRun("usage: 无法读取计费记录") from exc
                self.gate.after(report)

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
        session.turn_now_local = (
            datetime.fromisoformat(context["now"]) + timedelta(minutes=(len(session.history) - 1) // 2)
            if "now" in context else datetime.now().astimezone()
        )
        session.system_prompt = await self.compile(session, user, provider=self.snapshot)
        history = [{key: row[key] for key in ("role", "content")} for row in session.history]
        request_prompt = self.chat_ws._request_system_prompt(session, history, None, 0)
        if context.get("memory_note"):
            request_prompt += "\n【過去の対話から取得した記憶】\n" + context["memory_note"]
        if evidence:
            history = self.web_context(history, [(user, evidence)])
        history.append(self.chat_ws.turn_tail_message(session, history))
        self.model_usage.set_chat_context(session.worldline, session.conversation_id)
        original_lines = self.deepseek.iter_provider_lines

        finished = input_tokens = None

        async def timed_lines(response):
            nonlocal finished, input_tokens
            async for line in original_lines(response):
                if line == "data: [DONE]":
                    finished = time.perf_counter()
                elif line.startswith("data: "):
                    try:
                        packet = json.loads(line[6:])
                        usage = packet.get("usage") or {}
                        if "prompt_tokens" in usage:
                            input_tokens = usage["prompt_tokens"]
                        choices = packet.get("choices") or []
                        delta = choices[0].get("delta", {}) if choices else {}
                        ms = (time.perf_counter() - self.started) * 1000
                        if delta.get("reasoning_content") and self.first_reasoning is None:
                            self.first_reasoning = ms
                        if delta.get("content") and self.first_content is None:
                            self.first_content = ms
                    except (ValueError, TypeError, AttributeError):
                        pass
                yield line

        pieces = []
        async with self.paid(thinking):
            with patch.object(self.deepseek, "iter_provider_lines", timed_lines):
                async for chunk in self.adapter.get_chat_stream(
                    active_history=history, system_prompt=request_prompt,
                    model=self.model, api_key=self.key, max_output_tokens=self.max_output,
                    temperature=0.75, tools=None,
                ):
                    pieces.append(chunk.get("content") or "")
            total = ((finished or time.perf_counter()) - self.started) * 1000
        ja = "".join(pieces).strip()
        if ja:
            session.append_message({"role": "assistant", "content": ja})
        return {"ja": ja, "first_reasoning_ms": self.first_reasoning, "ttft_ms": self.first_content,
                "total_ms": total, "system_prompt_chars": len(self.wire_prompt),
                "system_prompt_tokens_est": self.counter(self.wire_prompt), "provider_input_tokens": input_tokens}

    async def translate(self, ja, thinking, context=None):
        async with self.paid(thinking, context):
            zh = await self.adapter.translate_to_zh(ja, api_key=self.key, model=self.model)
        if not zh:
            raise StopRun("subtitle: 模型未返回译文")
        return zh

def chosen_thinking(args):
    return args.thinking or (list(THINKING[1:]) if args.mode == "latency" else ["default"])


def off_peak_only(args):
    return not args.allow_peak and (args.require_off_peak or args.mode != "live-blind")


def evidence_for(case, turn_index):
    evidence = case.get("context", {}).get("web_evidence")
    return evidence["text"] if evidence and evidence["after"] == turn_index else None


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
                    raise StopRun("reply: 模型未返回可见正文")
                say("日语：" + reply["ja"])
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
    p.add_argument("--mode", choices=("probes", "subtitles", "latency", "blind-pack", "live-blind"), required=True)
    p.add_argument("--output", type=Path, required=True, help="新的本地产物目录")
    p.add_argument("--cap-cny", type=cap_value, default=Decimal(0), help="联网模式须显式给正预算")
    peak = p.add_mutually_exclusive_group()
    peak.add_argument("--require-off-peak", action="store_true", help="含 live-blind 都拒绝工作日 UTC 高峰")
    peak.add_argument("--allow-peak", action="store_true", help="批量模式允许工作日 UTC 高峰")
    p.add_argument("--samples", type=positive_int, help="probes 每题默认 2；latency 每档默认 10")
    p.add_argument("--thinking", action="append", choices=THINKING, help="可重复指定档位")
    p.add_argument("--model", default="deepseek-flash")
    p.add_argument("--max-output-tokens", type=positive_int, default=2048)
    p.add_argument("--case", action="append", help="只运行指定题目 ID，可重复")
    p.add_argument("--worldline", choices=WORLDS, default=WORLDS[0])
    p.add_argument("--identity-mode", choices=IDENTITIES, default=IDENTITIES[0])
    p.add_argument("--blind-input", nargs=2, type=Path, metavar=("LEFT", "RIGHT"))
    p.add_argument("--blind-thinking", nargs=2, choices=THINKING, metavar=("LEFT", "RIGHT"))
    p.add_argument("--seed", type=int, default=239, help="仅用于可重现的离线盲卷")
    p.add_argument("--profiles", type=Path, help="live-blind 候选 JSON")
    p.add_argument("--show-latency", action="store_true")
    return p


def load_profiles(path):
    path = path_guard(path)
    entries = read_json(path)
    if not isinstance(entries, list) or len(entries) < 2:
        raise ValueError("live-blind 需要至少两个候选")
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
                result = await run_batch(args, runtime, cases)
                result["model"] = runtime.model
                write_json(out / "results.json", result)
                count = len(result["rows"])
                spent = result["usage"]["estimated_cny"]
                print(f"已保存 {count} 项；已计价 ¥{spent:.4f}。", file=io)
                if result["stop_reason"]:
                    print(result["stop_reason"], file=io)
                    return 1
                return 0
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
        if args.mode == "blind-pack":
            if not args.blind_input:
                raise ValueError("blind-pack 需要 --blind-input LEFT RIGHT")
            out.mkdir(parents=True)
            blind_pack(*args.blind_input, out, args.seed, args.blind_thinking)
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
        cases, profile = [], {}
        if args.mode == "live-blind":
            if not args.profiles:
                raise ValueError("live-blind 需要 --profiles")
            profile = random.SystemRandom().choice(load_profiles(args.profiles))
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
