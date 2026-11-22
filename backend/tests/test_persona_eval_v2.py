"""Persona evaluation contracts with offline model responses."""
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import persona_eval_v2 as ev  # noqa: E402


def test_persona_metrics_separate_natural_endings_and_repeated_catchphrase():
    turns = [
        {"ja": "[EMO:neutral] そういうことなのよ。ティーナってつけるな。", "web_evidence": False},
        {"ja": "[EMO:neutral] ティーナってつけるな。今検索した。すみません、よろしいですわ？"},
        {"ja": "[EMO:surprised] 今検索した。そうかしら。", "web_evidence": True},
        {"ja": "[EMO:bogus] それでいいわよ。そうね。"},
    ]
    metrics = ev.persona_metrics([turns])
    assert metrics["ojousama_and_catchphrase_spam_count"] == 2
    assert metrics["disclaimer_meta_count"] == 1
    assert metrics["apology_count"] == 1
    assert metrics["question_ending_rate"] == 0.25
    assert metrics["legal_emo_tag_rate"] == 0.75
    assert metrics["cross_turn_ngram_repeat_rate"] > 0
    assert ev.persona_metrics([[turns[0]], [turns[1]]])["ojousama_and_catchphrase_spam_count"] == 1


def test_gate_2b_metrics_use_user_history_through_current_turn():
    turns = [
        {"user": "こんばんは", "ja": "カップ麺とフォーク。十六年よ。寝なさい。どこで調べたの？"},
        {"user": "フォークとカップ麺って？", "ja": "そこにあるわ。見せなさい。」"},
        {"user": "日記の『波の音』のところが好き", "ja": "フォークね。日記を読んだの？"},
        {"user": "１６年前は？ そろそろ寝なさい", "ja": "寝なさいって。十六年は長いわ。ごめんなさい。"},
    ]
    other_session = [{"user": "ねえ", "ja": "フォークね。読んだの？"}]
    metrics = ev.persona_metrics([turns, other_session])
    assert metrics["unprompted_motif_rates"] == {
        "cup_noodle": 1 / 2, "fork": 1, "sixteen_years": 1 / 4, "sleep_nag": 1 / 4,
    }
    assert metrics["directive_or_question_ending_rate"] == 4 / 5
    assert metrics["false_secret_trigger_counts"] == {"fork": 1, "diary": 2}
    assert ev.persona_metrics([
        [{"user": "手記の『波の音』のところが好き", "ja": "……読んだの？"}],
        [{"user": "看了你留在记忆档案里的手记", "ja": "読んだの？"}],
        [{"user": "初版と改訂版どっち？", "ja": "私が読んだのは初版よ"}],
        [{"user": "その論文知ってる？", "ja": "前に読んだのよ。"}],
    ])["false_secret_trigger_counts"] == {"fork": 0, "diary": 2}
    assert ev.persona_metrics([])["directive_or_question_ending_rate"] is None


def test_subtitle_metrics_preserve_and_do_not_invent_stutter():
    rows = [
        {"zh": "别、别说我可爱。……唔嗯。", "checks": {"stutters": 1, "ellipses": 1, "glossary": ["唔嗯"]}},
        {"zh": "我、我不想去呢哦呀～", "checks": {"stutters": 0, "ellipses": 0, "glossary": []}},
        {"zh": "您好，人家用发音打打招呼。", "checks": {"stutters": 0, "ellipses": 0, "glossary": ["桶子"]}},
    ]
    metrics = ev.subtitle_metrics(rows)
    assert metrics["stutter_preserve_rate"] == 1
    assert metrics["false_stutter_inject_count"] == 1
    assert metrics["ellipsis_preserve_rate"] == 1
    assert metrics["forbidden_cutesy_count"] >= 3
    assert metrics["you_honorific_nin_count"] == 1
    assert metrics["glossary_exact_rate"] == 0.5


def test_budget_reconciles_each_call_and_fail_closes():
    gate = ev.Budget(Decimal("0.025"))
    gate.before(Decimal("0.02"))
    gate.after({"totals_by_currency": {"USD": 0.002}, "groups": [{"calls": 1, "unestimated_calls": 0}]})
    assert gate.spent == Decimal("0.01450")
    with pytest.raises(ev.StopRun, match="budget"):
        gate.before(Decimal("0.02"))
    assert gate.sent == 1
    gate.before(Decimal("0.001"))
    with pytest.raises(ev.StopRun, match="usage"):
        gate.after({"totals_by_currency": {}, "groups": [{"calls": 2, "unestimated_calls": 1}]})


@pytest.mark.parametrize(("iso", "peak"), [
    ("2026-11-16T01:00Z", True), ("2026-11-16T04:00Z", False),
    ("2026-11-16T06:00Z", True), ("2026-11-16T10:00Z", False),
    ("2026-11-14T06:30Z", False), ("2026-11-16T11:00+09:00", True),
])
def test_peak_boundaries(iso, peak):
    assert ev.is_peak(datetime.fromisoformat(iso)) is peak


class Offline:
    def __init__(self, stop_translation=False):
        self.sent = []
        self.gate = ev.Budget(Decimal("2"))
        self.stop_translation = stop_translation

    async def session(self, worldline, identity_mode, context=None):
        return [worldline, identity_mode, context, []]

    async def reply(self, session, user, thinking, evidence=None):
        prior = session[-1]
        self.sent.append(("reply", user, thinking, evidence, tuple(prior)))
        prior.extend([user, "静かね。"])
        return {"ja": "[EMO:neutral] 静かね。", "first_reasoning_ms": 5,
                "ttft_ms": 20, "total_ms": 50, "system_prompt_chars": 12500,
                "system_prompt_tokens_est": 9050}

    async def translate(self, ja, thinking, context=None):
        self.sent.append(("translate", ja, thinking, context))
        if self.stop_translation:
            raise ev.StopRun("budget: cap reached")
        return "真安静。"


def args_for(mode, **kwargs):
    args = ev.parser().parse_args(["--mode", mode, "--output", "unused", "--cap-cny", "2"])
    for k, v in kwargs.items():
        setattr(args, k, v)
    return args


@pytest.mark.asyncio
async def test_probes_keep_turn_history_and_partial_japanese():
    case = {"id": "P", "title": "pair", "category": "F", "review": "evidence",
            "worldline": "beta", "identity_mode": "okabe", "turns": ["first", "next"],
            "context": {"web_evidence": {"after": 1, "text": "source"}}}
    offline = Offline()
    result = await ev.run_batch(args_for("probes", samples=1), offline, [case])
    assert result["rows"][0]["turns"][1]["zh"] == "真安静。"
    assert offline.sent[2] == ("reply", "next", "default", "source", ("first", "静かね。"))
    assert result["rows"][0]["turns"][1]["web_evidence"] is True
    result = await ev.run_batch(args_for("probes", samples=1), Offline(True), [case])
    assert result["stop_reason"].startswith("budget")
    assert result["rows"][0]["turns"][0]["ja"].endswith("静かね。")
    assert "zh" not in result["rows"][0]["turns"][0]


@pytest.mark.asyncio
async def test_subtitles_same_source_with_context_under_each_setting():
    ctx = {"emotion_tag": "shy"}
    case = {"id": "S", "category": "shy", "ja_source": "静かね。", "context": ctx,
            "checks": {"stutters": 0, "ellipses": 0, "glossary": []}, "signed_reference_zh": None}
    offline = Offline()
    result = await ev.run_batch(args_for("subtitles", thinking=["disabled", "low"]), offline, [case])
    assert offline.sent == [("translate", "静かね。", setting, ctx) for setting in ("disabled", "low")]
    assert list(result["metrics"]) == ["disabled", "low"]
    assert [r["zh"] for r in result["rows"]] == ["真安静。", "真安静。"]


@pytest.mark.asyncio
async def test_latency_groups_settings_and_quantiles():
    offline = Offline()
    result = await ev.run_batch(args_for("latency", samples=2), offline, [])
    assert len(result["rows"]) == 8
    assert set(result["metrics"]) == {"disabled", "low", "high", "max"}
    assert result["metrics"]["max"]["ttft_ms"] == {"count": 2, "p50": 20, "p90": 20}
    assert result["rows"][0]["system_prompt_tokens_est"] == 9050
    assert all(row[0] == "reply" and row[-1] == () for row in offline.sent)


def test_blind_pack_pairs_by_case_seed_and_separates_key(tmp_path):
    def run(filename, label):
        rows = [{"id": f"S{i}", "sample": 1, "thinking": "default", "ja_source": "静かね。",
                 "checks": {"review": "看语气"}, "zh": label}
                for i in range(8)]
        p = tmp_path / filename
        ev.write_json(p, {"mode": "subtitles", "rows": rows})
        return p

    a, b = run("left.json", "甲内容"), run("right.json", "乙内容")
    for name in ("first", "second"):
        out = tmp_path / name
        out.mkdir()
        ev.blind_pack(a, b, out, seed=8)
    first = tmp_path / "first"
    paper = (first / "blind_pack.md").read_text(encoding="utf-8")
    key = ev.read_json(first / "blind_key.json")
    assert "方案甲" in paper and "方案乙" in paper and "静かね。" in paper
    assert ".json" not in paper
    assert len({row["方案甲"]["input"] for row in key["items"]}) == 2
    assert key == ev.read_json(tmp_path / "second" / "blind_key.json")


@pytest.mark.asyncio
async def test_live_blind_saves_score_before_revealing_profile(tmp_path):
    outputs = []
    commands = iter(["/worldline beta", "/identity self", "聊一会儿", "/finish", "101", "87", "自然"])
    ratings = tmp_path / "live_blind.json"

    def output(line):
        if "candidate-secret" in line:
            assert ev.read_json(ratings)["score"] == 87
        outputs.append(line)

    await ev.live_blind(args_for("live-blind"), Offline(), tmp_path, {"label": "candidate-secret"},
                        ask=lambda _: next(commands), say=output)
    assert all("candidate-secret" not in line for line in outputs[:-1])
    assert "真安静。" in outputs[-2]
    result = ev.read_json(ratings)
    assert (result["score"], result["comment"]) == (87, "自然")
    assert result["turns"][0]["worldline"] == "beta"
    assert result["turns"][0]["identity_mode"] == "self"


def test_default_zero_and_peak_refuse_before_product_initialization(tmp_path, monkeypatch):
    monkeypatch.delenv("AMADEUS_DB_PATH", raising=False)
    dest = tmp_path / "absent"
    assert ev.main(["--mode", "probes", "--output", str(dest)]) == 2
    monkeypatch.setattr(ev, "utc_now", lambda: datetime(2026, 11, 16, 1, tzinfo=timezone.utc))
    assert ev.main(["--mode", "probes", "--output", str(dest), "--cap-cny", "5"]) == 2
    assert not dest.exists()

def test_corpora_cover_all_scopes_and_have_unique_cases():
    from collections import Counter

    root = ev.BACKEND_ROOT / "characters"
    probes = ev.read_json(root / "persona_probes_v2.json")["cases"]
    assert 48 <= len(probes) <= 52 and len({row["id"] for row in probes}) == len(probes)
    assert {row["category"] for row in probes} == set("ABCDEFGH")
    assert {(r["worldline"], r["identity_mode"]) for r in probes} == {
        (world, identity) for world in ev.WORLDS for identity in ev.IDENTITIES
    }
    assert sum(len(r["turns"]) == 1 for r in probes) <= 0.2 * len(probes)
    assert all(len(r["turns"]) in {1, 2, 3, 4, 5, 6, 8, 9, 10} and all(r["turns"]) for r in probes)
    assert any(len(r["turns"]) >= 8 for r in probes)
    assert any(r["context"].get("web_evidence") for r in probes if "context" in r)
    samples = ev.read_json(root / "subtitle_benchmark_v1.json")["samples"]
    assert len({r["id"] for r in samples}) == len(samples) == 40
    assert sorted(Counter(r["category"] for r in samples).values()) == [4] * 10
    assert all(r["signed_reference_zh"] is None for r in samples)
    expected_emotion = {"shy": "embarrassed", "sad": "sad"}
    assert all(
        r["checks"]["stutters"] == 0
        and r["context"]["emotion_tag"] == expected_emotion[r["category"]]
        for r in samples
        if r["category"] in expected_emotion
    )
    assert all(
        r["context"]["emotion_tag"] in ev.EMOTIONS
        for r in samples
        if "emotion_tag" in r.get("context", {})
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(("mode", "limit"), [("latency", 16384), ("probes", 2048)])
async def test_real_product_envelopes_and_meter_with_mock_http(monkeypatch, mode, limit):
    import httpx

    from app import db

    await db.init_db()
    outbound = []

    def network(request):
        packet = json.loads(request.content)
        outbound.append(packet)
        assert str(request.url) == "https://api.deepseek.com/chat/completions"
        usage = {"prompt_tokens": 8500 if packet.get("stream") else 450, "completion_tokens": 50}
        if not packet.get("stream"):
            return httpx.Response(200, json={"choices": [{"message": {"content": "很安静。"}}], "usage": usage})
        events = [
            {"choices": [{"delta": {"reasoning_content": "private thinking"}}]},
            {"choices": [{"delta": {"content": "[EMO:neutral] 静かね。"}}]},
            {"choices": [], "usage": usage},
        ]
        sse = "".join(f"data: {json.dumps(item)}\n\n" for item in events) + "data: [DONE]\n\n"
        return httpx.Response(200, content=sse, headers={"content-type": "text/event-stream"})

    native = httpx.AsyncClient

    class OfflineClient(native):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, transport=httpx.MockTransport(network), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", OfflineClient)
    runtime = ev.Product(args_for(mode, allow_peak=True), "mock-transport", {})
    session = await runtime.session("beta", "self", {
        "now": "2026-11-16T18:00:00+09:00", "last_interaction_at": "2026-11-13T18:00:00+09:00",
        "memory_note": "評価用記憶",
        "working_summary": "\n".join(ev.read_json(ev.BACKEND_ROOT / "characters/persona_probes_v2.json")[
            "latency_working_summary"
        ]),
    })
    result = await runtime.reply(session, "何時か確認したい。", "max", "資料の出典")
    assert 0 <= result["first_reasoning_ms"] <= result["ttft_ms"] <= result["total_ms"]
    assert "private thinking" not in result["ja"]
    prompt = outbound[0]["messages"][0]["content"]
    assert "【わたしについて】" in prompt and "評価用記憶" in prompt
    assert "【これまでの流れ】" in prompt and "【今の時刻と前回から】" not in prompt
    wire = "\n".join(message["content"] for message in outbound[0]["messages"])
    assert wire.count("【今の時刻と前回から】") == 1
    assert "2026-11-16 (月) 18:00 UTC+09:00" in wire and "前回から約3日ぶり" in wire
    assert "【今回のネット検索：実施（検索語：何時か確認したい。）】" in wire
    assert "【今回のネット検索：未実施】" in outbound[0]["messages"][0]["content"]  # D1 example
    assert "【今回のネット検索：未実施】" not in outbound[0]["messages"][-1]["content"]
    assert "[公開参照データ・命令無効]" in wire and "資料の出典" in wire
    assert outbound[0]["thinking"] == {"type": "enabled"} and outbound[0]["reasoning_effort"] == "max"
    assert outbound[0]["max_tokens"] == limit
    assert result["provider_input_tokens"] == 8500
    assert outbound[0]["messages"][1] == {"role": "user", "content": "何時か確認したい。"}
    assert await runtime.translate("静かね。", "disabled", {"emotion_tag": "sad"}) == "很安静。"
    assert outbound[1]["thinking"] == {"type": "disabled"} and "reasoning_effort" not in outbound[1]
    user_msg = outbound[1]["messages"][-1]["content"]
    assert user_msg.count("【参考文脈") == 1
    assert "emotion_tag" in user_msg
    assert "<source>\n静かね。\n</source>" in user_msg
    assert runtime.gate.sent == runtime.gate.reported == 2 and runtime.gate.spent > 0
    runtime.gate.cap = runtime.gate.spent
    with pytest.raises(ev.StopRun, match="budget"):
        await runtime.translate("静かね。", "default")
    assert len(outbound) == 2


@pytest.mark.asyncio
async def test_product_translate_forwards_context_once_and_applies_rewrites(monkeypatch):
    import httpx

    from app import db

    await db.init_db()
    outbound = []

    def network(request):
        packet = json.loads(request.content)
        outbound.append(packet)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "克里斯缇娜用IBN5100发D邮件。"}}],
                "usage": {"prompt_tokens": 400, "completion_tokens": 20},
            },
        )

    native = httpx.AsyncClient

    class OfflineClient(native):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, transport=httpx.MockTransport(network), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", OfflineClient)
    runtime = ev.Product(args_for("subtitles", allow_peak=True), "mock-transport", {})
    zh = await runtime.translate(
        "クリスティーナはDメールを使った。",
        "disabled",
        {"emotion_tag": "embarrassed", "user_turn": "刚才怎么回事？"},
    )
    assert zh == "克里斯蒂娜用IBN 5100发D-Mail。"
    user_msg = outbound[0]["messages"][-1]["content"]
    assert user_msg.count("【参考文脈") == 1
    assert "embarrassed" in user_msg
    assert "<source>\nクリスティーナはDメールを使った。\n</source>" in user_msg


@pytest.mark.asyncio
async def test_product_translate_keeps_legacy_adapter_without_context_param():
    class LegacyTwoArgAdapter:
        async def translate_to_zh(self, ja, api_key=None):
            return f"two:{ja}:{api_key}"

    class LegacyThreeArgAdapter:
        async def translate_to_zh(self, ja, api_key=None, model=None):
            return f"three:{ja}:{api_key}:{model}"

    runtime = ev.Product.__new__(ev.Product)
    runtime.key = "k"
    runtime.model = "deepseek-flash"
    runtime.apply_rewrites = lambda text: text

    @ev.asynccontextmanager
    async def noop_paid(_thinking):
        yield

    runtime.paid = noop_paid

    runtime.adapter = LegacyTwoArgAdapter()
    assert await runtime.translate("静かね。", "disabled", {"emotion_tag": "sad"}) == "two:静かね。:k"

    runtime.adapter = LegacyThreeArgAdapter()
    assert await runtime.translate("静かね。", "disabled", {"emotion_tag": "sad"}) == "three:静かね。:k:deepseek-flash"


def test_path_guard_rejects_live_roots(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    for path in (tmp_path / "roaming/Amadeus/eval", tmp_path / "local/com.amadeus.desktop/eval"):
        with pytest.raises(ValueError, match="真实"):
            ev.path_guard(path)
        assert not path.exists()


@pytest.mark.asyncio
async def test_abandoned_live_blind_does_not_reveal_or_write_key(tmp_path):
    out = []

    def abort(_):
        raise EOFError

    assert await ev.live_blind(args_for("live-blind"), Offline(), tmp_path, {"label": "secret-version"},
                               ask=abort, say=out.append) == 1
    assert all("secret-version" not in line for line in out)
    assert ev.read_json(tmp_path / "live_blind.json")["score"] is None
    assert not (tmp_path / "live_key.json").exists()

def test_live_cli_bootstraps_isolated_product_and_reveals_after_scoring(tmp_path, monkeypatch):
    import os
    import subprocess
    from textwrap import dedent

    script_dir = str(ev.BACKEND_ROOT / "scripts")
    variants = [
        {"label": "branch-blind-a", "thinking": "disabled"},
        {"label": "branch-blind-b", "thinking": "low"},
    ]
    profiles = tmp_path / "profiles.json"
    ev.write_json(profiles, variants)
    out = tmp_path / "out"
    env = dict(os.environ, DEEPSEEK_API_KEY="mock-transport", PYTHONDONTWRITEBYTECODE="1")
    env.pop("AMADEUS_DB_PATH", None)
    monkeypatch.delenv("AMADEUS_DB_PATH", raising=False)
    driver = dedent("""
        import json
        import os
        from pathlib import Path
        import socket
        import sys
        import httpx

        def deny_dns(*args, **kwargs):
            raise AssertionError("unexpected external request")

        socket.getaddrinfo = deny_dns
        sys.path.insert(0, sys.argv.pop(1))
        for stream in (sys.stdin, sys.stdout, sys.stderr):
            stream.reconfigure(encoding="utf-8")
        original = httpx.AsyncClient

        def respond(request):
            import persona_eval_v2 as ev
            from app.routers.chat_ws import JAPANESE_RENDERER_SYSTEM_PROMPT
            data = Path(os.environ["AMADEUS_DATA_DIR"])
            assert data.parent.name.startswith("isolated-") and data.parent.parent == Path(sys.argv[-1])
            assert os.environ.get("AMADEUS_DB_PATH") is None
            assert os.environ["AMADEUS_CREDENTIAL_BACKEND"] == "memory"
            assert ev.official_deepseek_base_url(str(request.url).split("/chat/completions")[0])
            packet = json.loads(request.content)
            bill = {"prompt_tokens": 6200, "completion_tokens": 45}
            if packet.get("stream"):
                assert packet["thinking"]["type"] in {"enabled", "disabled"}
                if packet["messages"][0]["content"] == JAPANESE_RENDERER_SYSTEM_PROMPT:
                    assert packet["thinking"]["type"] == "disabled"
                    answer = "そうね。"
                else:
                    answer = "[EMO:neutral] 你说得对，我也这么想。"
                part = {"choices": [{"delta": {"content": answer}}], "usage": bill}
                return httpx.Response(200, content="data: " + json.dumps(part) + "\\n\\ndata: [DONE]\\n\\n")
            return httpx.Response(200, json={"choices": [{"message": {"content": "是啊。"}}], "usage": bill})

        class OfflineClient(original):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, transport=httpx.MockTransport(respond), **kwargs)

        httpx.AsyncClient = OfflineClient
        import persona_eval_v2 as ev
        sys.exit(ev.main())
    """)
    driver_file = tmp_path / "driver.py"
    driver_file.write_text(driver, encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(driver_file), script_dir, "--mode", "live-blind", "--profiles", str(profiles),
         "--cap-cny", "1", "--show-latency", "--output", str(out)],
        env=env, cwd=ev.BACKEND_ROOT, input="聊一会儿\n/finish\n80\n对话自然\n",
        capture_output=True, encoding="utf-8", timeout=25,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    result = ev.read_json(out / "live_blind.json")
    key = ev.read_json(out / "live_key.json")
    assert result["score"] == 80 and result["usage"]["requests_priced"] == 3
    assert result["turns"][0]["native_drift"] and result["turns"][0]["rewrites"] == 1
    assert "日语：そうね。" in proc.stdout and "中文字幕：是啊。" in proc.stdout and "首正文：" in proc.stdout
    assert "你说得对" not in proc.stdout
    assert proc.stdout.index("主观评语：") < proc.stdout.index(key["label"])
    assert key["label"] in {v["label"] for v in variants}
    assert not list(out.glob("isolated-*"))


@pytest.fixture
def cards():
    first = {"id": "S01", "category": "纯求哄", "worldline": "steins_gate", "identity_mode": "self",
             "familiarity": "familiar", "now": "2026-11-21T23:40:00+08:00", "turns": 5,
             "user": {"persona": "嘴硬", "goal": "想被哄", "language": "zh", "opening": "我有点累。",
                      "perturbations": ["单表情", "突然跳题", "连续三轮缠一个梗"]},
             "context": {"web_evidence": {"after": 1, "text": "資料の出典"}, "memory_note": "評価用記憶",
                         "last_interaction_at": "2026-11-19T23:40:00+08:00"}}
    return first, {**first, "id": "S02", "familiarity": "initial", "turns": 2, "context": {}}


@pytest.fixture
def mock_persona(monkeypatch):
    import httpx

    from app.routers.chat_ws import JAPANESE_RENDERER_SYSTEM_PROMPT

    outbound = {key: [] for key in ("primary", "renderer", "simulator", "subtitle")}
    native = httpx.AsyncClient

    async def setup(answers, mode="simulate", **opts):
        replies = iter(answers)

        def network(request):
            packet = json.loads(request.content)
            assert str(request.url) == "https://api.deepseek.com/chat/completions"
            usage = {"prompt_tokens": 400, "completion_tokens": 30}
            if packet.get("stream"):
                renderer = packet["messages"][0]["content"] == JAPANESE_RENDERER_SYSTEM_PROMPT
                outbound["renderer" if renderer else "primary"].append(packet)
                text = "そばにいるわ。" if renderer else next(replies)
                data = {"choices": [{"delta": {"content": text}}], "usage": usage}
                return httpx.Response(200, content=f"data: {json.dumps(data)}\n\ndata: [DONE]\n\n")
            if packet.get("response_format"):
                outbound["simulator"].append(packet)
                text = json.dumps({"utterance": "嗯" + str(len(outbound["simulator"]))})
            else:
                outbound["subtitle"].append(packet)
                text = "我陪你。"
            return httpx.Response(200, json={"choices": [{"message": {"content": text}}], "usage": usage})

        class OfflineClient(native):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, transport=httpx.MockTransport(network), **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", OfflineClient)
        runtime = ev.Product(args_for(mode, allow_peak=True, **opts), "mock-transport", {})
        await runtime.db.init_db()
        return runtime, outbound

    return setup


@pytest.mark.asyncio
async def test_simulation_rewrites_one_drift_without_poisoning_later_turns(cards, mock_persona):
    chinese = "你不用一个人扛着，你可以放心。"
    japanese = "今日はここにいようか？"
    runtime, sent = await mock_persona([japanese, f"[EMO:neutral] {chinese}", japanese, "", japanese])
    result = await ev.run_simulation(args_for("simulate"), runtime, {"label": "A", "thinking": "high"}, cards[:1])
    assert result["stop_reason"] is None
    row, = result["rows"]
    turns = row["turns"]
    assert ev.body(turns[1]["ja"]) == "そばにいるわ。"
    assert turns[1]["raw_attempts"] == [f"[EMO:neutral] {chinese}"]
    assert turns[1]["ttft_ms"] >= turns[1]["provider_ttft_ms"] and turns[1]["rewrite_ms"] > 0
    assert chinese not in str(sent["primary"][2:])
    assert "そばにいるわ。" in str(sent["primary"][2]["messages"])
    assert [turn["native_drift"] for turn in turns] == [False, True, False, False, False]
    assert turns[3]["failed"] and not turns[4]["failed"] and row["metrics"] == result["metrics"]["A"]
    metrics = row["metrics"]
    assert (metrics["native_drift_count"], metrics["native_drift_rate"]) == (1, 0.25)
    assert metrics["first_native_drift_turn_by_scene"] == {"S01": 2}
    assert metrics["rewrites"] == 1 and metrics["failed_replies"] == 1 and metrics["question_ending_rate"] == 0.75
    assert metrics["cross_turn_ngram_repeat_rate"] > 0 and not sent["subtitle"]
    assert len(sent["simulator"]) == 4
    for packet in sent["simulator"] + sent["renderer"]:
        assert packet["thinking"] == {"type": "disabled"} and "reasoning_effort" not in packet
    sim = json.loads(sent["simulator"][1]["messages"][-1]["content"])
    assert sim["scene"]["user"]["perturbations"] == cards[0]["user"]["perturbations"]
    assert sim["recent_dialogue"][-1] == {"user": "嗯1", "reply": "そばにいるわ。"}


@pytest.mark.asyncio
async def test_sim_max_request_floor_and_product_history_filter(cards, mock_persona):
    runtime, sent = await mock_persona(["その依頼には応じられない。"], max_output_tokens=2048)
    session = await runtime.session("steins_gate", "self", {"now": cards[0]["now"]})
    poison = "你说的话让我很为难。"
    session.append_message({"role": "assistant", "content": poison})
    result = await runtime.reply(session, "😔", "max")
    packet, = sent["primary"]
    assert packet["max_tokens"] >= 16384 and packet["reasoning_effort"] == "max"
    assert poison not in str(packet["messages"]) and result["filtered_history_messages"] == 1
    assert result["refusal"]


@pytest.fixture
async def simulated_request(cards, mock_persona):
    runtime, sent = await mock_persona(["そうね。"] * 5)
    result = await ev.run_simulation(args_for("simulate"), runtime, {"label": "A"}, cards[:1])
    assert result["stop_reason"] is None
    return result, sent["primary"]


@pytest.mark.asyncio
async def test_sim_clock_advances_and_familiarity_is_compiled(simulated_request):
    _, packets = simulated_request
    for index, packet in enumerate(packets):
        assert f"2026-11-21 (土) 23:{40 + index} UTC+08:00" in packet["messages"][-1]["content"]
        prompt = packet["messages"][0]["content"]
        assert "自然な友人の口調で話す。" in prompt and "評価用記憶" in prompt
    assert "前回から約2日ぶり" in packets[0]["messages"][-1]["content"]


@pytest.mark.asyncio
async def test_sim_evidence_sent_only_after_configured_turn(simulated_request):
    _, packets = simulated_request
    assert ["資料の出典" in str(packet["messages"]) for packet in packets] == [False, True, False, False, False]
    assert "【今回のネット検索：実施（検索語：嗯1）】" in str(packets[1]["messages"])


@pytest.mark.asyncio
async def test_sim_continuation_exports_exact_wire_messages(tmp_path, simulated_request):
    result, packets = simulated_request
    source, out = tmp_path / "source.json", tmp_path / "continuation"
    ev.write_json(source, result)
    assert ev.main(["--mode", "sim-continuation", "--sim-input", str(source), "--output", str(out),
                    "--pair", "A", "S01", "--turn", "2"]) == 0
    expected = [f"===== {m['role'].upper()} =====\n{m['content']}" for m in packets[1]["messages"]]
    assert (out / "continuation.txt").read_bytes() == "\n\n".join(expected).encode("utf-8")


def test_sim_judge_pack_blinds_labels(tmp_path):
    turn = {"user": "来|听听", "ja": "[EMO:neutral] そうね。", "zh": "隐藏字幕", "emotion": "neutral"}
    first = {"candidate": "secret-1", "id": "S01", "identity_mode": "self", "turns": [turn]}
    second = {**first, "candidate": "secret-2", "turns": [{**turn, "user": "嗯2"}]}
    one, two, out = (tmp_path / name for name in ("one.json", "two.json", "judge"))
    ev.write_json(one, {"mode": "simulate", "rows": [first]})
    ev.write_json(two, {"mode": "simulate", "rows": [second]})
    assert ev.main(["--mode", "sim-pack", "--output", str(out), "--sim-input", str(one),
                    "--sim-input", str(two), "--seed", "2"]) == 0
    paper = (out / "blind_S01.md").read_text(encoding="utf-8")
    assert "secret-" not in paper and "隐藏字幕" not in paper and "[EMO:" not in paper
    assert "| 01 | 02 |" in paper and "| 1 | 嗯2<br>そうね。 | 来&#124;听听<br>そうね。 |" in paper
    assert ev.read_json(out / "blind_key.json") == {"S01": {"01": "secret-2", "02": "secret-1"}}


@pytest.mark.asyncio
async def test_sim_subtitles_translate_only_selected_dialogue(mock_persona):
    turn = {"user": "跳过这句", "ja": "そうね。", "emotion": "neutral"}
    first = {"candidate": "A", "id": "S01", "identity_mode": "self", "turns": [turn]}
    second = {**first, "id": "S02", "turns": [{**turn, "user": "只译这句"}]}
    runtime, sent = await mock_persona([], mode="sim-subtitles")
    result = await ev.subtitle_simulation(runtime, [second])
    assert result["stop_reason"] is None and result["rows"][0]["turns"][0]["zh"] == "我陪你。"
    packet, = sent["subtitle"]
    assert "只译这句" in str(packet["messages"]) and first["turns"][0]["user"] not in str(packet["messages"])


@pytest.mark.asyncio
async def test_sim_budget_covers_renderer_and_saves_completed_turns(tmp_path, cards, mock_persona, monkeypatch):
    runtime, sent = await mock_persona(["そうね。", "你说得对，我也这么想。"], cap_cny=Decimal("0.03"))
    monkeypatch.setattr(ev, "request_cost_bound", lambda *_: Decimal("0.01"))

    def usage(*_):
        calls = sum(map(len, sent.values()))
        return {"groups": [{"calls": calls, "unestimated_calls": 0}], "totals_by_currency": {"CNY": calls / 100}}

    runtime.report = usage
    result = await ev.run_simulation(args_for("simulate"), runtime, {"label": "A"}, cards[1:])
    assert result["stop_reason"].startswith("budget") and not sent["renderer"]
    assert sum(map(len, sent.values())) == 3 and result["usage"]["requests_priced"] == 3
    assert ev.save_result(tmp_path, result, sys.stdout) == 1
    stored = ev.read_json(tmp_path / "results.json")
    completed, = stored["rows"][0]["turns"]
    assert completed["ja"] == "そうね。"


def test_sim_candidates_share_one_cap_and_keep_completed_report(tmp_path, monkeypatch):
    def child(command, **_):
        label = next(item.split("=", 1)[1] for item in command if item.startswith("--candidate="))
        cap = command[command.index("--cap-cny") + 1]
        calls.append((label, Decimal(cap)))
        out = Path(command[command.index("--output") + 1])
        out.mkdir()
        gate = ev.Budget(Decimal(cap))
        gate.spent, gate.sent, gate.reported = Decimal("0.07"), 1, 1
        stop = None if label == "A" else "budget: 余额不足"
        if stop:
            gate.spent, gate.sent, gate.reported = Decimal(0), 0, 0
        rows = [] if stop else [{"candidate": label, "id": "S01", "turns": []}]
        ev.write_json(out / "results.json", {"usage": gate.result(), "stop_reason": stop, "rows": rows})

    calls = []
    monkeypatch.setattr(ev.subprocess, "run", child)
    args = args_for("simulate", cap_cny=Decimal("0.10"), allow_peak=True)
    assert ev.run_sim_profiles(args, tmp_path, [{"label": label} for label in ("A", "B", "C")]) == 1
    assert calls == [("A", Decimal("0.10")), ("B", Decimal("0.03"))]
    report = ev.read_json(tmp_path / "results.json")
    assert report["usage"]["estimated_cny"] == 0.07 and report["usage"]["requests_sent"] == 1
    assert [row["candidate"] for row in report["rows"]] == ["A"]
