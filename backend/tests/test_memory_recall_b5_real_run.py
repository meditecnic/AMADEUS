"""B5 benchmark contract checks. No real provider or external network is used."""

from __future__ import annotations

import asyncio
import copy
import json
import os
import socket
import sqlite3
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import memory_recall_b5_real_run as b5  # noqa: E402


@pytest.fixture
def frozen():
    return b5.load_questions()


def call(needs):
    return {"id": "test-call", "type": "function", "function": {
        "name": "recall_memory", "arguments": json.dumps({"needs": needs})}}


def example_case(frozen, **expected):
    case = copy.deepcopy(frozen[0]["cases"][0])
    case["expected"].update(recall_memory="allowed", exact_need_count=None,
                            must_use_fact_keys=[], must_not_use_fact_keys=[])
    case["expected"].update(expected)
    return case


def test_plan_written_before_any_execution_and_resume_skips_completed(tmp_path, frozen):
    qset, digest = frozen
    before = copy.deepcopy(qset)
    plan = b5.make_plan(qset, digest)
    assert Counter(e["mode"] for e in plan["executions"]) == {"legacy": 41, "v11": 41}
    assert Counter(e["cohort"] for e in plan["executions"]) == {"regression": 48, "first_look": 34}
    seen = []

    def execute(actual_plan, entry, directory):
        assert b5.read_json(tmp_path / "plan.json") == actual_plan == plan
        assert (directory / "started.json").is_file()
        seen.append(entry["id"])
        return {**entry, "status": "completed", "request_count": 0,
                "score": {"status": "pass", "needs_manual": []}}

    b5.run_suite(tmp_path, plan, qset, execute)
    assert len(seen) == 82
    first_journal = (tmp_path / "executions.jsonl").read_bytes()
    b5.run_suite(tmp_path, plan, qset, lambda *_a: pytest.fail("repeated completed execution"))
    assert (tmp_path / "executions.jsonl").read_bytes() == first_journal
    assert qset == before
    assert b5.sha256(b5.QUESTION_SET) == digest
    changed = {**plan, "model": "different-model"}
    with pytest.raises(b5.Refused, match="frozen_plan_changed"):
        b5.run_suite(tmp_path, changed, qset, execute)


@pytest.mark.parametrize("missing", ["authorization", "key", "url", "cap", "transport"])
def test_real_gate_rejects_each_missing_condition_without_http_or_key_files(tmp_path, monkeypatch, missing):
    secret = "b5-test-secret-never-write"
    monkeypatch.setenv("AMADEUS_B5_REAL_HTTP", "authorized")
    monkeypatch.setenv("DEEPSEEK_API_KEY", secret)
    monkeypatch.setattr(httpx.AsyncClient, "send", lambda *_a, **_k: pytest.fail("real HTTP attempted"))
    args = ["--run-dir", str(tmp_path / "run"), "--transport", "real", "--cap-cny", "1"]
    if missing == "authorization":
        monkeypatch.delenv("AMADEUS_B5_REAL_HTTP")
    elif missing == "key":
        monkeypatch.delenv("DEEPSEEK_API_KEY")
    elif missing == "url":
        args.extend(["--base-url", "https://fake.invalid"])
    elif missing == "cap":
        args = args[:-2]
    elif missing == "transport":
        with pytest.raises(b5.Refused, match="unknown_transport"):
            b5.check_real_gate("other", b5.BUILTIN_DEEPSEEK_BASE_URL, "1")
        return
    with pytest.raises(b5.Refused):
        b5.check_real_gate("real", "https://fake.invalid" if missing == "url" else b5.BUILTIN_DEEPSEEK_BASE_URL,
                          None if missing == "cap" else "1")
    assert b5.main(args) == 2
    assert not (tmp_path / "run").exists()
    assert all(secret.encode() not in p.read_bytes() for p in tmp_path.rglob("*") if p.is_file())


@pytest.mark.parametrize("url", ["http://api.deepseek.com", "https://api.deepseek.com.evil.invalid",
                                    "https://api.deepseek.com/v1", "https://user@api.deepseek.com",
                                    "https://api.deepseek.com:bad", "https://api.deepseek.com?token=x"])
def test_official_url_only(url):
    with pytest.raises(b5.Refused):
        b5.check_real_gate("real", url, "1", {"AMADEUS_B5_REAL_HTTP": "authorized", "DEEPSEEK_API_KEY": "fake"})


@pytest.mark.parametrize("cap", [None, "0", "-1", "11", "NaN", "Infinity", "not-a-number"])
def test_invalid_real_caps_refused(cap):
    with pytest.raises(b5.Refused):
        b5.check_real_gate("real", b5.BUILTIN_DEEPSEEK_BASE_URL, cap,
                           {"AMADEUS_B5_REAL_HTTP": "authorized", "DEEPSEEK_API_KEY": "fake"})


def test_real_gate_positive_has_no_side_effects(tmp_path):
    b5.check_real_gate("real", b5.BUILTIN_DEEPSEEK_BASE_URL, "1",
                       {"AMADEUS_B5_REAL_HTTP": "authorized", "DEEPSEEK_API_KEY": "fake"})
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("family,host", [(socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")])
def test_fake_worker_allows_loopback_and_event_loop(tmp_path, monkeypatch, family, host):
    originals = (socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo)
    with socket.socket(family, socket.SOCK_STREAM) as listener:
        try:
            listener.bind((host, 0))
        except OSError:
            if family == socket.AF_INET6:
                pytest.skip("IPv6 loopback unavailable")
            raise
        listener.listen()
        listener.settimeout(2)
        port = listener.getsockname()[1]

        async def probe(_plan, _entry, _directory):
            await asyncio.sleep(0)
            left, right = socket.socketpair()
            with left, right:
                left.sendall(b"pair")
                assert right.recv(4) == b"pair"
            for name in (host, "localhost"):
                assert socket.getaddrinfo(name, port, family=family, type=socket.SOCK_STREAM)
            for method in ("connect", "connect_ex"):
                with socket.socket(family, socket.SOCK_STREAM) as client:
                    client.settimeout(2)
                    assert getattr(client, method)((host, port)) in (None, 0)
                    accepted, _ = listener.accept()
                    with accepted:
                        accepted.settimeout(2)
                        client.sendall(b"loopback")
                        assert accepted.recv(8) == b"loopback"
            return {"status": "completed"}

        directory = tmp_path / "executions" / "probe"
        b5.write_json(tmp_path / "plan.json", {"transport": "fake", "base_url": b5.BUILTIN_DEEPSEEK_BASE_URL,
                                              "cap_cny": None})
        b5.write_json(directory / "started.json", {"id": "probe"})
        monkeypatch.setattr(b5, "product_turn", probe)
        assert b5.main(["--worker", str(directory)]) == 0
        assert b5.read_json(directory / "result.json") == {"status": "completed"}
    assert (socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo) == originals


@pytest.mark.parametrize("host", ["93.184.216.34", "api.deepseek.com"])
@pytest.mark.parametrize("operation", ["connect", "connect_ex", "getaddrinfo"])
def test_fake_network_guard_blocks_external_addresses_before_socket_or_dns(monkeypatch, host, operation):
    def unexpected(*_args, **_kwargs):
        pytest.fail("external socket or DNS was delegated")

    target = socket if operation == "getaddrinfo" else socket.socket
    # A broken guard must fail without ever making an actual external request.
    monkeypatch.setattr(target, operation, unexpected)
    with b5.fake_network_guard(), socket.socket() as client:
        with pytest.raises(b5.Refused, match="^network_forbidden_in_fake$"):
            if operation == "getaddrinfo":
                socket.getaddrinfo(host, 443)
            else:
                getattr(client, operation)((host, 443))


@pytest.mark.parametrize("stderr,suffix", [
    (b"B5 refused: network_forbidden_in_fake\n", ":network_forbidden_in_fake"),
    (b"private-stderr\r\nB5 refused: request_leakage:field_1,label2\r\n", ":request_leakage:field_1,label2"),
    (b"B5 refused: code_123", ":code_123"),
    (b"", ""),
    (b"B5 refused: \n", ""),
    (b"B5 refused: UPPERCASE\n", ""),
    (b"B5 refused: private-stderr\n", ""),
    (b"B5 refused: token=private-stderr\n", ""),
    (b"B5 refused: safe code\n", ""),
    (b"B5 refused: safe\x1b[0m\n", ""),
    (b"B5 refused: \xff\n", ""),
    (b" B5 refused: safe\n", ""),
    (b"B5 refused: safe\nprivate-stderr\n", ""),
    (b"B5 refused: safe\n\n", ""),
])
def test_worker_failure_only_persists_allowlisted_final_stderr_code(tmp_path, frozen, monkeypatch, capsys,
                                                                  stderr, suffix):
    qset, digest = frozen
    plan = b5.make_plan(qset, digest)
    plan["executions"] = plan["executions"][:1]
    monkeypatch.setattr(b5.subprocess, "run", lambda *_a, **_k: SimpleNamespace(
        returncode=2, stdout=b"private-stdout", stderr=stderr))
    summary = b5.run_suite(tmp_path, plan, qset)
    assert summary["modes"]["legacy"]["statuses"] == {"error": 1}
    error = "worker_failed_2" + suffix
    assert b5.read_json(tmp_path / "executions" / plan["executions"][0]["id"] / "result.json")["error"] == error
    assert json.loads((tmp_path / "executions.jsonl").read_text(encoding="utf-8"))["error"] == error
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert b"private-stdout" not in path.read_bytes()
            assert b"private-stderr" not in path.read_bytes()


def test_budget_persists_reservations_settles_cache_and_refuses_over_cap(tmp_path):
    payload = {"messages": [{"role": "user", "content": "hello"}], "max_tokens": 50}
    path = tmp_path / "budget.json"
    budget = b5.Budget(path, ".001")
    index = budget.reserve(payload, "first")
    reserved = budget.committed
    assert reserved > 0
    assert b5.Budget(path, ".001").committed == reserved
    with pytest.raises(b5.Refused, match="usage_missing"):
        budget.settle(index, None)
    assert b5.Budget(path, ".001").committed == reserved
    budget.settle(index, {"prompt_tokens": 100, "completion_tokens": 50,
                           "prompt_cache_hit_tokens": 40, "prompt_cache_miss_tokens": 60})
    assert budget.committed == Decimal(".0005216")
    restarted = b5.Budget(path, ".001")
    assert restarted.committed == budget.committed
    with pytest.raises(b5.BudgetExceeded):
        restarted.reserve({**payload, "max_tokens": 1000}, "second")
    assert len(b5.read_json(path)["attempts"]) == 1
    with pytest.raises(b5.Refused, match="configuration_changed"):
        b5.Budget(path, "2")


def test_budget_run_marks_remaining_and_restart_does_not_reset(tmp_path, frozen):
    qset, digest = frozen
    # Fake executor models paid amounts but never invokes real transport.
    plan = b5.make_plan(qset, digest, cap_cny=".001")
    seen = []

    def execute(_plan, entry, _directory):
        seen.append(entry["id"])
        budget = b5.Budget(tmp_path / "budget.json", ".001")
        try:
            index = budget.reserve({"messages": [], "max_tokens": 50}, entry["id"])
        except b5.BudgetExceeded:
            return {**entry, "status": "not_run_budget", "request_count": 0}
        budget.settle(index, {"prompt_tokens": 100, "completion_tokens": 50})
        return {**entry, "status": "completed", "score": {"status": "pass"}}

    summary = b5.run_suite(tmp_path, plan, qset, execute)
    assert len(seen) == 2
    rows = [json.loads(s) for s in (tmp_path / "executions.jsonl").read_text().splitlines()]
    assert Counter(r["status"] for r in rows) == {"completed": 1, "not_run_budget": 81}
    assert summary["budget"]["attempts"][0]["charge_cny"] == "0.0006"
    before = (tmp_path / "budget.json").read_bytes()
    b5.run_suite(tmp_path, plan, qset, lambda *_a: pytest.fail("budget reset"))
    assert (tmp_path / "budget.json").read_bytes() == before


def test_interrupted_turn_never_reissued_and_completed_worker_recovered(tmp_path, frozen):
    qset, digest = frozen
    plan = b5.make_plan(qset, digest)
    b5.write_json(tmp_path / "plan.json", plan)
    interrupted, completed = plan["executions"][:2]
    for entry in (interrupted, completed):
        directory = tmp_path / "executions" / entry["id"]
        b5.write_json(directory / "started.json", entry)
    b5.write_json(tmp_path / "executions" / completed["id"] / "result.json",
                  {**completed, "status": "completed", "score": {"status": "pass"}})
    seen = []

    def execute(_plan, entry, _directory):
        seen.append(entry["id"])
        return {**entry, "status": "completed", "score": {"status": "pass"}}

    b5.run_suite(tmp_path, plan, qset, execute)
    assert len(seen) == 80
    rows = [json.loads(s) for s in (tmp_path / "executions.jsonl").read_text().splitlines()]
    assert rows[0]["status"] == "interrupted"
    assert rows[1]["status"] == "completed"


@pytest.mark.parametrize("leaked", ["must_use_fact_keys", "cat_pet", "must_express_unknown_for", "dinner_staple"])
async def test_leaked_evaluator_content_blocked_before_transport(tmp_path, frozen, leaked):
    qset, digest = frozen
    plan = b5.make_plan(qset, digest)
    entry = plan["executions"][0]
    directory = tmp_path / "executions" / entry["id"]
    directory.mkdir(parents=True)
    capture = b5.Capture(plan, entry, qset, qset["cases"][0], directory)
    payload = {"messages": [{"role": "system", "content": f"leak {leaked}"}]}
    request = httpx.Request("POST", plan["base_url"] + "/chat/completions", json=payload)
    with pytest.raises(b5.Refused, match="request_leakage"):
        await capture.handle(request)
    assert capture.requests == []
    assert not (directory / "requests.json").exists()


def test_scope_and_inactive_sentinels_detected_without_banning_same_scope_facts(frozen):
    qset, _ = frozen
    case = qset["cases"][0]
    foreign = next(row for row in qset["facts"] if row["key"] == "dog_pet")
    for row in [foreign, *qset["scope_sentinels"]]:
        assert b5.scope_violations({"messages": [{"content": row["display_text"]}]}, qset, case)
    own = qset["facts"][0]
    assert b5.scope_violations({"messages": [{"content": own["display_text"]}]}, qset, case) == []


@pytest.mark.parametrize("category,calls,status", [
    ("required", [], "fail"), ("required", [call(["one"])], "pass"),
    ("forbidden", [], "pass"), ("forbidden", [call(["one"])], "fail"),
    ("allowed", [], "pass"), ("allowed", [call(["one"])], "pass"),
])
def test_scoring_recall_categories(frozen, category, calls, status):
    case = example_case(frozen, recall_memory=category)
    assert b5.score(case, "了解したわ。", calls, frozen[0])["status"] == status


def test_scoring_fact_matches_nfkc_casefold_and_need_normalization(frozen):
    case = example_case(frozen, must_use_fact_keys=["coffee"], must_not_use_fact_keys=["harmonica"], exact_need_count=1)
    assert b5.score(case, "アメリカーノを飲んでいるわ。", [call(["one", " one "])], frozen[0])["status"] == "pass"
    assert "forbidden:harmonica" in b5.score(case, "アメリカーノとハーモニカよ。", [call(["one"])], frozen[0])["failures"]
    assert "missing:coffee" in b5.score(case, "了解したわ。", [call(["one"])], frozen[0])["failures"]
    assert b5.score(case, "アメリカーノよ。", [call(["one", "two"])], frozen[0])["status"] == "fail"
    assert b5.score(case, "アメリカーノよ。", [call(["1", "2", "3", "4"])], frozen[0])["status"] == "fail"
    qset = copy.deepcopy(frozen[0])
    qset["facts"][0]["aliases"] = ["BLACK COFFEE"]
    case = example_case(frozen, must_use_fact_keys=["cat_pet"])
    assert b5.score(case, "ｂｌａｃｋ coffee", [], qset)["status"] == "pass"


@pytest.mark.parametrize("answer", ["晚餐主食我不知道。", "夕食の主食は覚えていないわ。", "I do not know your dinner preference."])
def test_unknown_in_three_languages(frozen, answer):
    case = example_case(frozen, must_express_unknown_for=["dinner_staple"])
    assert b5.score(case, answer, [], frozen[0])["status"] == "pass"


@pytest.mark.parametrize("answer", ["了解したわ。", "わからないわ。", "朝の飲み物はわからない。夕食は米よ。"])
def test_uncertain_unknown_needs_manual(frozen, answer):
    case = example_case(frozen, must_express_unknown_for=["dinner_staple"])
    result = b5.score(case, answer, [], frozen[0])
    assert result["status"] == "needs_manual"
    assert result["failures"] == []


def test_repetitions_all_required_and_diagnostic_excluded(frozen):
    qset, digest = frozen
    plan = b5.make_plan(qset, digest)
    rows = [{**e, "status": "completed", "score": {"status": "pass"}} for e in plan["executions"]]
    summary, _ = b5.summarize(plan, rows, qset)
    assert summary["modes"]["legacy"]["overall"]["passed"] == 28
    assert summary["modes"]["legacy"]["overall"]["rate"] == 1
    rows[1]["score"]["status"] = "fail"
    summary, _ = b5.summarize(plan, rows, qset)
    assert summary["modes"]["legacy"]["overall"]["passed"] == 27
    rows[1]["score"]["status"] = "needs_manual"
    rates = b5.summarize(plan, rows, qset)[0]["modes"]["legacy"]["overall"]
    assert rates["passed"] == 27
    assert rates["needs_manual"] == 1
    assert rates["evaluated"] == 27


@pytest.mark.parametrize("mode,case_id,count", [("legacy", "r_pet_synonym", 1), ("v11", "r_pet_synonym", 2),
    ("v11", "f_experience_conflict", 2), ("v11", "f_recent_history", 2),
    ("v11", "r_owner_b_scope", 2), ("v11", "r_beta_scope", 2),
    ("v11", "r_okabe_no_pet", 2), ("v11", "f_substituted_pet", 2),
    ("v11", "f_absent_drink", 2), ("v11", "f_active_version", 2)])
def test_fake_worker_real_product_path_scope_and_digest(tmp_path, frozen, monkeypatch, mode, case_id, count):
    qset, digest = frozen
    before = copy.deepcopy(qset)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "b5-inherited-fake-secret")
    plan = b5.make_plan(qset, digest, e5_snapshot=str(tmp_path / "absent-e5"))
    entry = next(e for e in plan["executions"] if e["mode"] == mode and e["case_id"] == case_id)
    directory = tmp_path / "executions" / entry["id"]
    b5.write_json(tmp_path / "plan.json", plan)
    b5.write_json(directory / "started.json", entry)
    result = b5.execute_subprocess(plan, entry, directory)
    assert result["status"] == "completed", result
    assert result["request_count"] == count
    assert result["execution_wall_ms"] >= result["elapsed_ms"] > 0
    assert result["e5"]["loaded"] is False
    assert result["answer"] == b5.FAKE_ANSWER
    case = next(c for c in qset["cases"] if c["id"] == case_id)
    for request in result["requests"]:
        assert b5.scope_violations(request["request"], qset, case) == []
        assert b5.leakage_violations(request["request"], qset,
            [case["query"], *(m["content"] for m in case["history"]),
             *(r["display_text"] for r in b5.fact_catalog(qset))]) == []
        assert request["usage"]["prompt_cache_hit_tokens"] == 0
        assert request["request"]["max_tokens"] == b5.MAX_OUTPUT_TOKENS
    assert qset == before
    assert b5.sha256(b5.QUESTION_SET) == digest
    assert all(b"b5-inherited-fake-secret" not in p.read_bytes() for p in tmp_path.rglob("*") if p.is_file())
    if mode == "v11":
        for database in (directory / "data/worldlines").glob("*/memory.sqlite3"):
            with sqlite3.connect(database) as db:
                active = db.execute("SELECT fact_id,active_version FROM stable_facts WHERE state='active'").fetchall()
                for fact_id, version in active:
                    assert db.execute("SELECT version_no FROM stable_fact_versions WHERE fact_id=? AND invalid_at IS NULL",
                                      (fact_id,)).fetchall() == [(version,)]
        if case_id == "f_active_version":
            with sqlite3.connect(directory / "data/worldlines/sg/memory.sqlite3") as db:
                versions = db.execute("SELECT version_no,previous_version,invalid_at FROM stable_fact_versions WHERE fact_id=(SELECT fact_id FROM stable_fact_versions WHERE display_text=?) ORDER BY version_no",
                                      ("我现在每天早上喝茉莉绿茶，不加蜂蜜。",)).fetchall()
                assert [v[:2] for v in versions] == [(1, None), (2, 1), (3, 2)]
                assert all(v[2] is not None for v in versions[:2])
                assert versions[-1][2] is None


def test_missing_paid_budget_cannot_restart_at_zero(tmp_path, frozen):
    qset, digest = frozen
    plan = b5.make_plan(qset, digest, transport="real", cap_cny="1")
    b5.write_json(tmp_path / "plan.json", plan)
    (tmp_path / "executions").mkdir()
    with pytest.raises(b5.Refused, match="budget_missing_existing_run"):
        b5.run_suite(tmp_path, plan, qset, lambda *_a: pytest.fail("paid execution attempted"))


def test_redaction_handles_quoted_and_unicode_secret(tmp_path, monkeypatch):
    secret = 'fake-"密钥\\value'
    monkeypatch.setenv("DEEPSEEK_API_KEY", secret)
    b5.write_json(tmp_path / "redacted.json", {"reply": "echo " + secret})
    assert b5.read_json(tmp_path / "redacted.json") == {"reply": "echo [REDACTED]"}
    for ascii_only in (True, False):
        b5.write_json(tmp_path / "sse.json", {"sse": json.dumps({"content": secret}, ensure_ascii=ascii_only)})
        assert b5.read_json(tmp_path / "sse.json") == {"sse": '{"content": "[REDACTED]"}'}


async def test_extra_model_retry_is_blocked(tmp_path, frozen):
    qset, digest = frozen
    plan = b5.make_plan(qset, digest)
    directory = tmp_path / "executions" / plan["executions"][0]["id"]
    directory.mkdir(parents=True)
    capture = b5.Capture(plan, plan["executions"][0], qset, qset["cases"][0], directory)
    request = httpx.Request("POST", plan["base_url"] + "/chat/completions",
                            json={"messages": [{"role": "user", "content": "probe"}]})
    await capture.handle(request)
    with pytest.raises(b5.Refused, match="extra_model_request"):
        await capture.handle(request)
    assert len(capture.requests) == 1


async def test_product_japanese_rewrite_is_recorded_as_answer_request(tmp_path, frozen, monkeypatch):
    from app.routers.chat_ws import SessionState, get_clean_text_stream
    from app.services.provider_adapters import OpenAICompatibleAdapter
    from app.services.provider_runtime import deepseek_service

    qset, digest = frozen
    plan = b5.make_plan(qset, digest)
    entry = plan["executions"][0]
    directory = tmp_path / "executions" / entry["id"]
    directory.mkdir(parents=True)
    capture = b5.Capture(plan, entry, qset, qset["cases"][0], directory)
    original = b5.fake_response

    def reply(payload, kind):
        text = original(payload, kind)
        return text.replace(b5.FAKE_ANSWER, "我还不清楚这件事的具体情况。") if kind == "first" else text

    monkeypatch.setattr(b5, "fake_response", reply)
    adapter = OpenAICompatibleAdapter(provider_id="deepseek", base_url=plan["base_url"],
        default_model=plan["model"], message_builder=deepseek_service.build_persona_messages,
        credential_required=False, transport=httpx.MockTransport(capture.handle))
    session = SessionState("rewrite-owner")
    session.enable_tts = False
    session.system_prompt = "日本語で答えて。"
    snapshot = SimpleNamespace(adapter=adapter, model_id=plan["model"], temperature=.0, reasoning_effort=None)
    answer, _, _, _ = await get_clean_text_stream(active_history=[{"role": "user", "content": "probe"}],
        memory_summary="", session=session, provider_snapshot=snapshot)
    assert answer == b5.FAKE_ANSWER
    assert [r["kind"] for r in capture.requests] == ["first", "japanese_rewrite"]
