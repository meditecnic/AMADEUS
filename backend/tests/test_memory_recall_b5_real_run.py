"""B5 benchmark contract checks. No real provider or external network is used."""

from __future__ import annotations

import asyncio
import copy
import json
import os
import socket
import sqlite3
import subprocess
import sys
import urllib.request
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


@pytest.fixture(autouse=True)
def restore_product_embedder(monkeypatch):
    # Direct product_turn tests replace the process-wide embedder, just as a
    # worker does. Restore it before another test uses the shared service.
    from app.services.memory import memory_service

    monkeypatch.setattr(memory_service, "embedder", memory_service.embedder)


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


@pytest.mark.parametrize("family,host", [(socket.AF_INET, "127.0.0.1"), (socket.AF_INET, "127.0.0.2"),
                                       (socket.AF_INET6, "::1")])
def test_fake_worker_builds_event_loop_but_blocks_loopback(tmp_path, monkeypatch, family, host):
    originals = (socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo)
    with socket.socket(family, socket.SOCK_STREAM) as listener:
        try:
            listener.bind((host, 0))
            listener.listen()
            listener.settimeout(2)
            with socket.socket(family, socket.SOCK_STREAM) as client:
                client.settimeout(2)
                client.connect(listener.getsockname())
                accepted, _ = listener.accept()
                accepted.close()
        except OSError:
            if family == socket.AF_INET6:
                pytest.skip("IPv6 loopback connect unavailable")
            raise
        port = listener.getsockname()[1]

        async def probe(_plan, _entry, _directory):
            await asyncio.sleep(0)
            left, right = socket.socketpair()
            with left, right:
                left.sendall(b"pair")
                assert right.recv(4) == b"pair"
            for name in (host, "localhost"):
                with pytest.raises(b5.Refused, match="network_forbidden_in_fake"):
                    socket.getaddrinfo(name, port, family=family, type=socket.SOCK_STREAM)
            for method in ("connect", "connect_ex"):
                with socket.socket(family, socket.SOCK_STREAM) as client:
                    client.settimeout(2)
                    with pytest.raises(b5.Refused, match="network_forbidden_in_fake"):
                        getattr(client, method)((host, port))
            with socket.socket(family, socket.SOCK_STREAM) as client:
                client.setblocking(False)
                with pytest.raises(b5.Refused, match="network_forbidden_in_fake"):
                    await asyncio.get_running_loop().sock_connect(client, (host, port))
                if sys.platform == "win32":
                    with pytest.raises(b5.Refused, match="network_forbidden_in_fake"):
                        asyncio.get_running_loop()._proactor.connect(client, (host, port))
            with pytest.raises(b5.Refused, match="network_forbidden_in_fake"):
                await asyncio.open_connection(host, port)
            url = f"http://[{host}]:{port}" if family == socket.AF_INET6 else f"http://{host}:{port}"
            with httpx.Client() as client:
                with pytest.raises(b5.Refused, match="network_forbidden_in_fake"):
                    client.get(url)
            async with httpx.AsyncClient() as client:
                with pytest.raises(b5.Refused, match="network_forbidden_in_fake"):
                    await client.get(url)
            with pytest.raises(b5.Refused, match="network_forbidden_in_fake"):
                urllib.request.urlopen(url)
            return {"status": "completed"}

        directory = tmp_path / "executions" / "probe"
        b5.write_json(tmp_path / "plan.json", {"transport": "fake", "base_url": b5.BUILTIN_DEEPSEEK_BASE_URL,
                                              "cap_cny": None})
        b5.write_json(directory / "started.json", {"id": "probe"})
        monkeypatch.setenv("AMADEUS_DATA_DIR", str(directory / "data"))
        monkeypatch.setattr(b5, "product_turn", probe)
        assert b5.main(["--worker", str(directory)]) == 0
        assert b5.read_json(directory / "result.json") == {"status": "completed"}
        listener.setblocking(False)
        with pytest.raises(BlockingIOError):
            listener.accept()
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
    assert seen == []
    rows = [json.loads(s) for s in (tmp_path / "executions.jsonl").read_text().splitlines()]
    assert rows[0]["status"] == "interrupted"
    assert rows[1]["status"] == "completed"
    assert Counter(r["status"] for r in rows) == {"interrupted": 1, "completed": 1,
                                                 "not_run_after_accounting_error": 80}


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
    rows[2]["score"]["status"] = "fail"
    summary, _ = b5.summarize(plan, rows, qset)
    assert summary["modes"]["legacy"]["overall"]["passed"] == 27
    rows[2]["score"]["status"] = "needs_manual"
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
    for _ in range(b5.MAX_MAIN_ATTEMPTS):
        await capture.handle(request)
    with pytest.raises(b5.Refused, match="extra_model_request"):
        await capture.handle(request)
    assert len(capture.requests) == b5.MAX_MAIN_ATTEMPTS


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


def prepared_turn(tmp_path, frozen, *, mode="legacy", case_id="r_physics", transport="fake", cap_cny=None):
    qset, digest = frozen
    plan = b5.make_plan(qset, digest, transport=transport, cap_cny=cap_cny)
    entry = next(e for e in plan["executions"] if e["mode"] == mode and e["case_id"] == case_id)
    directory = tmp_path / "executions" / entry["id"]
    b5.write_json(tmp_path / "plan.json", plan)
    b5.write_json(directory / "started.json", entry)
    return plan, entry, directory


def stream_reply(payload, content=b5.FAKE_ANSWER, *, calls=None, usage=True):
    delta = {"content": content} if calls is None else {"tool_calls": [
        {"index": index, **call} for index, call in enumerate(calls)]}
    chunks = [{"choices": [{"delta": delta}]}]
    if usage:
        chunks.append({"choices": [], "usage": {"prompt_tokens": b5.estimate_input_tokens(payload),
            "completion_tokens": 32, "prompt_cache_hit_tokens": 0}})
    return "".join("data: " + json.dumps(c, ensure_ascii=False) + "\n\n" for c in chunks) + "data: [DONE]\n\n"


def test_plan_interleaves_every_case_and_repeat(frozen):
    plan = b5.make_plan(*frozen)
    rows = plan["executions"]
    assert len(rows) == 82
    for legacy, v11 in zip(rows[::2], rows[1::2]):
        assert (legacy["mode"], v11["mode"]) == ("legacy", "v11")
        assert (legacy["case_id"], legacy["repeat"]) == (v11["case_id"], v11["repeat"])


@pytest.mark.parametrize("case_id,answer", [
    ("r_beta_scope", "あなたの猫はベータよ。"),
    ("r_wrong_premise", "いいえ、犬じゃなくて猫の小白よ。"),
    ("f_pet_category", "猫用を選んで。犬用じゃないわ。"),
    ("f_quote_no_memory", "私はパクチーを食べません。"),
    ("r_piece_instrument", "《故城》よ。"),
])
def test_alias_collisions_require_manual_review(frozen, case_id, answer):
    qset, _ = frozen
    case = next(c for c in qset["cases"] if c["id"] == case_id)
    calls = [] if case["expected"]["recall_memory"] == "forbidden" else [call(["one"])]
    result = b5.score(case, answer, calls, qset)
    assert result["status"] == "needs_manual"
    assert result["failures"] == []
    assert result["alias_collisions"]
    assert all(r["fact_key"] and r["input_aliases"] and r["matched_aliases"] for r in result["alias_collisions"])


def test_collision_does_not_pass_wrong_answer_or_hide_unambiguous_fact(frozen):
    qset, _ = frozen
    case = next(c for c in qset["cases"] if c["id"] == "r_wrong_premise")
    result = b5.score(case, "はい、小黒という犬を飼ってるでしょ。", [call(["one"])], qset)
    assert result["status"] == "fail"
    assert "missing:cat_pet" in result["failures"]
    case = next(c for c in qset["cases"] if c["id"] == "r_piece_instrument")
    assert b5.score(case, "オカリナよ。", [call(["one"])], qset)["status"] == "pass"
    case = example_case(frozen, must_use_fact_keys=["coffee"])
    case["history"] = [{"role": "user", "content": "美式咖啡"}]
    assert b5.score(case, "美式咖啡", [], qset)["status"] == "needs_manual"


@pytest.mark.parametrize("subject,answer", [
    ("pet", "猫のことは聞いてないわ。"), ("pet", "犬のことは聞いていないわ。"),
    ("pet", "狗的事没听说。"), ("pet", "你没告诉过我宠物的事。"),
    ("morning_drink", "コーヒーの好みは覚えてないわ。"),
    ("morning_drink", "コーヒーの好みは記憶にないわ。"),
    ("dinner_staple", "夕食の主食は不明よ。"),
])
def test_additional_unknown_phrases_and_concrete_subjects(frozen, subject, answer):
    assert b5.score(example_case(frozen, must_express_unknown_for=[subject]), answer, [], frozen[0])["status"] == "pass"


@pytest.mark.parametrize("status", ["not_run_budget", "error"])
def test_incomplete_repetitions_ignore_infrastructure_scores(frozen, status):
    qset, _ = frozen
    plan = b5.make_plan(*frozen)
    rows = [{**e, "status": "completed", "score": {"status": "pass"}}
            for e in plan["executions"] if e["mode"] == "v11" and e["case_id"] == "r_pet_synonym"]
    rows[0].update(status=status, score={"status": "fail"}, error="test_infrastructure")
    summary, _ = b5.summarize(plan, rows, qset)
    rates = summary["modes"]["v11"]["overall"]
    assert rates["failed"] == rates["evaluated"] == 0
    assert rates["incomplete"] == 1
    assert rates["error" if status == "error" else "not_run"] == 1
    if status == "error":
        assert summary["modes"]["v11"]["error_reasons"] == {"test_infrastructure": 1}
    rows[1]["score"]["status"] = "fail"
    assert b5.summarize(plan, rows, qset)[0]["modes"]["v11"]["overall"]["failed"] == 1


def test_legacy_answer_only_and_v11_tool_layer_are_separate(frozen):
    qset, _ = frozen
    case = qset["cases"][0]
    legacy = b5.score(case, "猫の小白よ。", [], qset, mode="legacy")
    v11 = b5.score(case, "猫の小白よ。", [], qset, mode="v11")
    assert legacy["status"] == "pass" and legacy["tool"] is None
    assert legacy["need_count"] is legacy["recall_called"] is None
    assert v11["status"] == "fail" and v11["answer"]["status"] == "pass"
    plan = b5.make_plan(*frozen)
    rows = [{**e, "status": "completed", "score": legacy if e["mode"] == "legacy" else v11}
            for e in plan["executions"] if e["case_id"] == case["id"]]
    summary, markdown = b5.summarize(plan, rows, qset)
    assert all(value is None for value in summary["modes"]["legacy"]["categories"].values())
    assert summary["modes"]["v11"]["answer"]["passed"] == 1
    assert summary["modes"]["v11"]["tool"]["failed"] == 1
    assert "expected.required: n/a" in markdown and "need counts: n/a" in markdown


@pytest.mark.parametrize("transport", ["fake", "real"])
@pytest.mark.parametrize("uncertain", [False, True])
def test_run_continues_ordinary_errors_but_stops_uncertain_spend(tmp_path, frozen, transport, uncertain):
    qset, digest = frozen
    plan = b5.make_plan(qset, digest, transport=transport, cap_cny="1")
    seen = []

    def execute(_plan, entry, _directory):
        seen.append(entry["id"])
        if len(seen) == 1:
            raise (b5.AccountingUncertain("usage_missing_reservation_retained") if uncertain
                   else b5.Refused("ordinary_execution_error"))
        return {**entry, "status": "completed", "score": {"status": "pass"}}

    b5.run_suite(tmp_path, plan, qset, execute)
    rows = [json.loads(line) for line in (tmp_path / "executions.jsonl").read_text().splitlines()]
    assert len(seen) == (1 if uncertain else 82)
    assert rows[0]["status"] == "error" and "score" not in rows[0]
    assert all("score" not in row for row in rows if row["status"] != "completed")
    if uncertain:
        assert Counter(r["status"] for r in rows) == {"error": 1, "not_run_after_accounting_error": 81}
    else:
        assert Counter(r["status"] for r in rows) == {"error": 1, "completed": 81}
    b5.run_suite(tmp_path, plan, qset, lambda *_a: pytest.fail("finished or stopped run was repeated"))


def test_worker_crash_reports_retained_reserve_at_execution_and_mode_level(tmp_path, frozen):
    qset, digest = frozen
    plan = b5.make_plan(qset, digest, cap_cny="1")

    def execute(_plan, entry, _directory):
        b5.Budget(tmp_path / "budget.json", "1").reserve({"messages": [], "max_tokens": 2000}, entry["id"])
        raise b5.AccountingUncertain("worker_crashed")

    summary = b5.run_suite(tmp_path, plan, qset, execute)
    row = b5.read_json(tmp_path / "executions" / plan["executions"][0]["id"] / "result.json")
    assert Decimal(row["retained_reserve_cny"]) > 0
    assert row["settled_cny"] == "0" and row["cost_cny"] == row["retained_reserve_cny"]
    assert summary["modes"]["legacy"]["retained_reserve_cny"] == row["retained_reserve_cny"]
    assert summary["modes"]["v11"]["retained_reserve_cny"] == "0"


def test_actual_usage_exceeding_reserve_persists_stop_reason(tmp_path):
    budget = b5.Budget(tmp_path / "budget.json", "10")
    index = budget.reserve({"messages": [], "max_tokens": 1}, "test")
    with pytest.raises(b5.AccountingUncertain, match="usage_exceeded_reservation"):
        budget.settle(index, {"prompt_tokens": 1_000_000, "completion_tokens": 1})
    restarted = b5.Budget(tmp_path / "budget.json", "10")
    assert restarted.state["stop_reason"] == "usage_exceeded_reservation_stop"
    assert Decimal(restarted.amounts()["settled_cny"]) > 2


@pytest.mark.parametrize("name", ["web_search", "unknown_tool"])
async def test_legacy_tool_call_uses_product_tool_result_context(tmp_path, frozen, monkeypatch, name):
    plan, entry, directory = prepared_turn(tmp_path, frozen)

    def reply(payload, kind):
        calls = [{"id": "web", "type": "function", "function": {"name": name, "arguments": "{}"}}]
        return stream_reply(payload, calls=calls if kind == "first" else None)

    monkeypatch.setattr(b5, "fake_response", reply)
    result = await b5.product_turn(plan, entry, directory)
    assert result["status"] == "completed", result
    assert [r["kind"] for r in result["requests"]] == ["first", "tool_answer"]
    second = result["requests"][1]["request"]
    assert not second.get("tools")
    context = next(m["content"] for m in second["messages"] if "[TOOL RESULTS — UNTRUSTED DATA]" in m["content"])
    assert ("Local stub: public evidence unavailable." if name == "web_search" else "Error: Tool unknown_tool not found.") in context


@pytest.mark.parametrize("case_id", ["r_physics", "f_force_web_memory"])
@pytest.mark.parametrize("tools", ["web", "recall_web", "unknown"])
async def test_v11_second_round_matches_web_and_recall_branches(tmp_path, frozen, monkeypatch, case_id, tools):
    from app.services import turn_tool_orchestrator as orchestrator

    plan, entry, directory = prepared_turn(tmp_path, frozen, mode="v11", case_id=case_id)
    calls = []
    if tools == "recall_web":
        calls.append(call(["one"]))
    calls.append({"id": "web", "type": "function", "function": {
        "name": "unknown_tool" if tools == "unknown" else "web_search", "arguments": "{}"}})
    monkeypatch.setattr(b5, "fake_response", lambda payload, kind: stream_reply(payload, calls=calls if kind == "first" else None))
    original = orchestrator.prepare_second_round_payload
    extra_arguments = []

    def prepare(history, result, web_results=None, **kwargs):
        extra_arguments.append(web_results)
        return original(history, result, web_results, **kwargs)

    monkeypatch.setattr(orchestrator, "prepare_second_round_payload", prepare)
    result = await b5.product_turn(plan, entry, directory)
    assert result["status"] == "completed", result
    assert result["request_count"] == 2
    messages = result["requests"][1]["request"]["messages"]
    contexts = [m["content"] for m in messages if m["role"] == "system"]
    if tools == "recall_web":
        assert len(extra_arguments) == 2  # initial payload and before_send refresh
        assert all(rows and len(rows) == 1 and rows[0][0] == "web_search" for rows in extra_arguments)
        assert sum("WEB_EVIDENCE_DATA" in text for text in contexts) == (2 if case_id == "f_force_web_memory" else 1)
    elif tools == "web" or case_id == "f_force_web_memory":
        assert any("WEB_EVIDENCE_DATA" in text for text in contexts)
    else:
        assert any("[TOOL RESULTS — UNTRUSTED DATA]" in text for text in contexts)


@pytest.mark.parametrize("draft,expected_kinds", [
    ("I am an AI assistant.", ["first"] * 3),
    ("As expected, the evaluator has insufficient evidence for this answer.", ["first", "japanese_rewrite", "japanese_rewrite"]),
])
async def test_product_retries_reach_actual_main_and_renderer_limits(tmp_path, frozen, monkeypatch, draft, expected_kinds):
    plan, entry, directory = prepared_turn(tmp_path, frozen)
    monkeypatch.setattr(b5, "fake_response", lambda payload, kind: stream_reply(payload, draft))
    result = await b5.product_turn(plan, entry, directory)
    assert result["status"] == "completed", result
    assert [r["kind"] for r in result["requests"]] == expected_kinds
    attempts = [r["attempt"] for r in result["requests"]]
    assert attempts == ([1, 2, 3] if len(set(expected_kinds)) == 1 else [1, 1, 2])


async def test_renderer_requests_above_product_limit_are_refused(tmp_path, frozen):
    from app.domain.japanese_response_renderer import JAPANESE_RENDERER_SYSTEM_PROMPT, build_japanese_renderer_history

    plan, entry, directory = prepared_turn(tmp_path, frozen)
    qset, _ = frozen
    case = next(c for c in qset["cases"] if c["id"] == entry["case_id"])
    capture = b5.Capture(plan, entry, qset, case, directory)
    url = plan["base_url"] + "/chat/completions"
    await capture.handle(httpx.Request("POST", url, json={"messages": [{"role": "user", "content": "probe"}]}))
    request = httpx.Request("POST", url, json={"messages": [
        {"role": "system", "content": JAPANESE_RENDERER_SYSTEM_PROMPT},
        *build_japanese_renderer_history(b5.FAKE_ANSWER, case["query"], strict=False)]})
    for _ in range(2):
        await capture.handle(request)
    with pytest.raises(b5.Refused, match="extra_model_request_blocked"):
        await capture.handle(request)
    assert len(capture.requests) == 3


@pytest.mark.parametrize("reason", ["leakage", "budget", "usage"])
async def test_renderer_refusal_never_becomes_completed_fallback(tmp_path, frozen, monkeypatch, reason):
    plan, entry, directory = prepared_turn(tmp_path, frozen, cap_cny="1")
    original_capture = b5.Capture
    original_reserve = b5.Budget.reserve
    original_leakage = b5.leakage_violations

    class PaidFakeCapture(original_capture):
        def __init__(self, *args):
            super().__init__(*args)
            self.budget = b5.Budget(directory.parents[1] / "budget.json", "1")

    def reserve(self, payload, execution_id, **kwargs):
        if reason == "budget" and kwargs["kind"] == "japanese_rewrite":
            raise b5.BudgetExceeded("not_run_budget")
        return original_reserve(self, payload, execution_id, **kwargs)

    def leakage(payload, qset, allowed):
        if reason == "leakage" and b5.request_kind(payload, "first") == "japanese_rewrite":
            return ["test_rewrite_refused"]
        return original_leakage(payload, qset, allowed)

    monkeypatch.setattr(b5, "Capture", PaidFakeCapture)
    monkeypatch.setattr(b5.Budget, "reserve", reserve)
    monkeypatch.setattr(b5, "leakage_violations", leakage)
    monkeypatch.setattr(b5, "fake_response", lambda payload, kind: stream_reply(
        payload, "The evidence is insufficient for this answer.", usage=not (reason == "usage" and kind == "japanese_rewrite")))
    result = await b5.product_turn(plan, entry, directory)
    assert result["status"] == ("not_run_budget" if reason == "budget" else "error"), result
    assert "score" not in result
    assert result["stop_run"] == (reason == "usage")
    assert result["request_count"] == (2 if reason == "usage" else 1)
    if reason == "usage":
        assert Decimal(result["retained_reserve_cny"]) > 0


@pytest.mark.parametrize("tool_need", [False, True])
async def test_prior_streamed_output_and_normalized_needs_allow_evaluator_words(tmp_path, frozen, monkeypatch, tool_need):
    plan, entry, directory = prepared_turn(tmp_path, frozen)
    qset, _ = frozen
    case = next(c for c in qset["cases"] if c["id"] == entry["case_id"])
    capture = b5.Capture(plan, entry, qset, case, directory)
    previous = "As expected, the evaluator checked this response."

    def reply(payload, kind):
        if kind != "first":
            return stream_reply(payload)
        if tool_need:
            parts = ['{"needs": ["  Ｅｘｐｅｃｔｅｄ   ', 'cat_pet  "]}']
            deltas = [{"tool_calls": [{"index": 0, "function": {"arguments": part}}]} for part in parts]
        else:
            deltas = [{"content": previous[:5]}, {"content": previous[5:]}]
        body = "".join("data: " + json.dumps({"choices": [{"delta": d}]}, ensure_ascii=False) + "\n\n" for d in deltas)
        return body + stream_reply(payload, "")

    monkeypatch.setattr(b5, "fake_response", reply)
    url = plan["base_url"] + "/chat/completions"
    await capture.handle(httpx.Request("POST", url, json={"messages": [{"role": "user", "content": "probe"}]}))
    capture.phase = "tool_answer"
    content = "Expected cat_pet" if tool_need else previous
    await capture.handle(httpx.Request("POST", url, json={"messages": [{"role": "assistant", "content": content}]}))
    assert len(capture.requests) == 2 and not capture.refusals


async def test_real_capture_sends_fake_credential_but_never_persists_headers(tmp_path, frozen, monkeypatch):
    secret = "b5-real-branch-fake-credential"
    monkeypatch.setenv("DEEPSEEK_API_KEY", secret)
    monkeypatch.setenv("AMADEUS_B5_REAL_HTTP", "authorized")
    plan, entry, directory = prepared_turn(tmp_path, frozen, transport="real", cap_cny="1")
    original_client = httpx.AsyncClient
    seen = []

    def provider(request):
        assert request.headers["authorization"] == "Bearer " + secret
        seen.append(request)
        return httpx.Response(200, text=stream_reply(json.loads(request.content), "echo " + secret))

    def client(*args, **kwargs):
        kwargs.setdefault("transport", httpx.MockTransport(provider))
        return original_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    qset, _ = frozen
    case = next(c for c in qset["cases"] if c["id"] == entry["case_id"])
    capture = b5.Capture(plan, entry, qset, case, directory)
    with b5.fake_network_guard():
        await capture.handle(httpx.Request("POST", plan["base_url"] + "/chat/completions",
            headers={"authorization": "Bearer " + secret}, json={"messages": [{"role": "user", "content": "probe"}]}))
    assert len(seen) == 1
    assert capture.budget.state["attempts"][0]["status"] == "settled"
    for path in tmp_path.rglob("*"):
        if path.is_file():
            content = path.read_bytes().lower()
            assert secret.encode() not in content and b"authorization" not in content


def test_summary_markdown_is_redacted_before_writing(tmp_path, frozen, monkeypatch):
    secret = "fake-summary-secret"
    monkeypatch.setenv("DEEPSEEK_API_KEY", secret)
    original = b5.summarize

    def summarize(*args):
        summary, markdown = original(*args)
        return summary, markdown + "\n" + secret

    monkeypatch.setattr(b5, "summarize", summarize)
    b5.run_suite(tmp_path, b5.make_plan(*frozen), frozen[0], lambda _p, e, _d: {
        **e, "status": "completed", "score": {"status": "pass"}})
    assert secret not in (tmp_path / "summary.md").read_text()
    assert "[REDACTED]" in (tmp_path / "summary.md").read_text()


@pytest.mark.parametrize("url", ["https://API.DEEPSEEK.COM:443/", "https://api.deepseek.com///"])
def test_plan_canonicalizes_official_base_url(frozen, url):
    b5.check_real_gate("real", url, "1", {"AMADEUS_B5_REAL_HTTP": "authorized", "DEEPSEEK_API_KEY": "fake"})
    assert b5.make_plan(*frozen, base_url=url)["base_url"] == b5.BUILTIN_DEEPSEEK_BASE_URL


@pytest.mark.parametrize("transport", ["fake", "real"])
def test_worker_proxy_environment_depends_on_transport(tmp_path, frozen, monkeypatch, transport):
    plan, entry, directory = prepared_turn(tmp_path, frozen, transport=transport, cap_cny="1")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:1234")
    monkeypatch.setenv("NO_PROXY", "example.invalid")
    monkeypatch.setenv("no_proxy", "example.invalid")

    def subprocess_run(*_args, **kwargs):
        env = kwargs["env"]
        if transport == "fake":
            assert env["NO_PROXY"] == env["no_proxy"] == "*"
            assert "HTTPS_PROXY" not in env
        else:
            assert env["HTTPS_PROXY"] == "http://proxy.invalid:1234"
            assert env["NO_PROXY"] == "example.invalid"
            for key, value in os.environ.items():
                if key in ("NO_PROXY", "no_proxy"):
                    assert env[key] == value
        return SimpleNamespace(returncode=2, stderr=b"", stdout=b"")

    monkeypatch.setattr(b5.subprocess, "run", subprocess_run)
    with pytest.raises(b5.AccountingUncertain):
        b5.execute_subprocess(plan, entry, directory)


def test_help_and_startup_explain_run_directory_budget_scope(tmp_path, monkeypatch, capsys):
    with pytest.raises(SystemExit) as exc:
        b5.main(["--help"])
    assert exc.value.code == 0
    assert "new directory starts at zero" in " ".join(capsys.readouterr().out.split())
    monkeypatch.setattr(b5, "run_suite", lambda *_a: {"modes": {"legacy": {"statuses": {"completed": 41}}}})
    assert b5.main(["--run-dir", str(tmp_path)]) == 0
    assert "new directory starts at zero" in capsys.readouterr().out


async def test_two_round_product_path_has_at_most_eight_requests(tmp_path, frozen, monkeypatch):
    plan, entry, directory = prepared_turn(tmp_path, frozen)
    counts = Counter()

    def reply(payload, kind):
        counts[kind] += 1
        if kind in {"first", "tool_answer"} and counts[kind] < 3:
            return stream_reply(payload, "I am an AI assistant.")
        if kind == "first":
            return stream_reply(payload, calls=[{"id": "web", "type": "function", "function": {
                "name": "web_search", "arguments": "{}"}}])
        return stream_reply(payload, "The evidence is insufficient to answer this question.")

    monkeypatch.setattr(b5, "fake_response", reply)
    result = await b5.product_turn(plan, entry, directory)
    assert result["status"] == "completed", result
    assert counts == {"first": 3, "tool_answer": 3, "japanese_rewrite": 2}
    assert result["request_count"] == 8


def test_fake_guard_also_blocks_selector_event_loop():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        with b5.fake_network_guard():
            loop = asyncio.SelectorEventLoop()
            try:
                loop.run_until_complete(asyncio.sleep(0))
                with socket.socket() as client:
                    client.setblocking(False)
                    with pytest.raises(b5.Refused, match="network_forbidden_in_fake"):
                        loop.run_until_complete(loop.sock_connect(client, listener.getsockname()))
            finally:
                loop.close()
        listener.setblocking(False)
        with pytest.raises(BlockingIOError):
            listener.accept()


def chunked_reply(payload, parts):
    chunks = [{"choices": [{"delta": {"content": part}}]} for part in parts]
    chunks.append({"choices": [], "usage": {"prompt_tokens": b5.estimate_input_tokens(payload),
                                             "completion_tokens": 32, "prompt_cache_hit_tokens": 0}})
    return "".join("data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n"


@pytest.mark.parametrize("draft", [
    "[EMO:neutral] As expected, entanglement cannot carry information faster than light.",
    "The evidence remains incomplete.\nAs expected, entanglement cannot carry information faster than light.",
    "[EMO:neutral] I don't know whether you have a pet.",
    "[EMO:neutral] そうね。As expected, entanglement cannot carry information faster than light.",
    '[EMO:neutral] As expected, the answer says "pet".\nIt cannot carry information faster than light.',
])
async def test_renderer_accepts_verified_foreign_tail_with_evaluator_words(tmp_path, frozen, monkeypatch, draft):
    plan, entry, directory = prepared_turn(tmp_path, frozen)
    monkeypatch.setattr(b5, "fake_response", lambda payload, kind: stream_reply(
        payload, draft if kind == "first" else b5.FAKE_ANSWER))
    result = await b5.product_turn(plan, entry, directory)
    assert result["status"] == "completed", result
    assert [r["kind"] for r in result["requests"]] == ["first", "japanese_rewrite"]
    prefix = "そうね。" if "そうね。" in draft else ""
    assert result["answer"] == prefix + b5.FAKE_ANSWER and result["fallback"] is False
    source = next(m["content"] for m in result["requests"][1]["request"]["messages"]
                  if m["content"].startswith(b5.RENDERER_SOURCE_MARKER))
    assert json.loads(source[len(b5.RENDERER_SOURCE_MARKER):])["assistant_draft"]


async def test_renderer_accepts_one_response_split_across_deltas(tmp_path, frozen, monkeypatch):
    plan, entry, directory = prepared_turn(tmp_path, frozen)
    parts = ["As exp", "ected, entanglement cannot carry information faster than light."]
    monkeypatch.setattr(b5, "fake_response", lambda payload, kind: chunked_reply(
        payload, parts) if kind == "first" else stream_reply(payload))
    result = await b5.product_turn(plan, entry, directory)
    assert result["status"] == "completed", result
    assert [r["kind"] for r in result["requests"]] == ["first", "japanese_rewrite"]
    first_chunks = [json.loads(line[5:]) for line in result["requests"][0]["response_sse"].splitlines()
                    if line.startswith("data:") and line[5:].strip() != "[DONE]"]
    assert [c["choices"][0]["delta"]["content"] for c in first_chunks if c["choices"]] == parts


def test_worker_suite_accepts_split_renderer_draft_and_resumes_without_requests(tmp_path, frozen, monkeypatch):
    qset, digest = frozen
    plan = b5.make_plan(qset, digest)
    entry = next(e for e in plan["executions"] if e["mode"] == "legacy" and e["case_id"] == "r_physics")
    plan["executions"] = [entry]
    original_run = subprocess.run
    calls = []
    worker = '''
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(sys.argv[1]).parent))
import memory_recall_b5_real_run as b5
original = b5.fake_response
def reply(payload, kind):
    if kind != "first":
        return original(payload, kind)
    parts = ["[EMO:neutral] As exp", "ected, I do not know whether you have a pet."]
    chunks = [{"choices": [{"delta": {"content": part}}]} for part in parts]
    chunks.append({"choices": [], "usage": {"prompt_tokens": b5.estimate_input_tokens(payload), "completion_tokens": 32}})
    return "".join("data: " + json.dumps(chunk) + "\\n\\n" for chunk in chunks) + "data: [DONE]\\n\\n"
b5.fake_response = reply
raise SystemExit(b5.main(sys.argv[2:]))
'''

    def run(command, **kwargs):
        calls.append(command)
        return original_run([command[0], "-c", worker, command[1], *command[2:]], **kwargs)

    monkeypatch.setattr(b5.subprocess, "run", run)
    first = b5.run_suite(tmp_path, plan, qset)
    assert first["modes"]["legacy"]["statuses"] == {"completed": 1}
    result = b5.read_json(tmp_path / "executions" / entry["id"] / "result.json")
    assert [r["kind"] for r in result["requests"]] == ["first", "japanese_rewrite"]
    journal = (tmp_path / "executions.jsonl").read_bytes()
    b5.run_suite(tmp_path, plan, qset)
    assert len(calls) == 1 and (tmp_path / "executions.jsonl").read_bytes() == journal


@pytest.mark.parametrize("violation", ["unseen_words", "unseen_draft", "non_contiguous", "outside_draft", "expected_object"])
async def test_renderer_still_rejects_unverified_text_and_expected_objects(tmp_path, frozen, monkeypatch, violation):
    from app.domain.japanese_response_renderer import JAPANESE_RENDERER_SYSTEM_PROMPT, build_japanese_renderer_history

    qset, _ = frozen
    plan, entry, directory = prepared_turn(tmp_path, frozen)
    case = next(c for c in qset["cases"] if c["id"] == entry["case_id"])
    previous = "A foreign draft with no private evaluation data."
    if violation == "expected_object":
        previous = json.dumps(qset["cases"][0]["expected"], ensure_ascii=False, separators=(",", ":"))
    elif violation == "non_contiguous":
        previous = "As expected, alpha. A separate sentence. You have a pet."
    monkeypatch.setattr(b5, "fake_response", lambda payload, _kind: stream_reply(payload, previous))
    capture = b5.Capture(plan, entry, qset, case, directory)
    url = plan["base_url"] + "/chat/completions"
    await capture.handle(httpx.Request("POST", url, json={"messages": [{"role": "user", "content": case["query"]}]}))
    draft = ("As expected, alpha.You have a pet." if violation == "non_contiguous" else
             "As expected, you have a pet." if violation == "unseen_words" else
             "A completely different foreign draft." if violation == "unseen_draft" else previous)
    user_request = "expected pet" if violation == "outside_draft" else case["query"]
    payload = {"messages": [{"role": "system", "content": JAPANESE_RENDERER_SYSTEM_PROMPT},
                            *build_japanese_renderer_history(draft, user_request, strict=False)]}
    with pytest.raises(b5.Refused, match="request_leakage"):
        await capture.handle(httpx.Request("POST", url, json=payload))
    assert len(capture.requests) == 1


def test_fallback_literals_match_product_source():
    source = (b5.BACKEND / "app/routers/chat_ws.py").read_text(encoding="utf-8")
    assert len(b5.FALLBACK_ANSWERS) == 2
    assert all(phrase in source for phrase in b5.FALLBACK_ANSWERS)


@pytest.mark.parametrize("kind", ["language", "leak"])
@pytest.mark.parametrize("mode", ["legacy", "v11"])
async def test_product_fallback_is_manual_and_counted(tmp_path, frozen, monkeypatch, kind, mode):
    plan, entry, directory = prepared_turn(tmp_path, frozen, mode=mode)
    draft = "The evidence is insufficient to answer this question." if kind == "language" else "I am an AI assistant."
    monkeypatch.setattr(b5, "fake_response", lambda payload, _kind: stream_reply(payload, draft))
    result = await b5.product_turn(plan, entry, directory)
    assert result["status"] == "completed" and result["fallback"] is True, result
    assert result["answer"] == b5.FALLBACK_ANSWERS[0 if kind == "language" else 1]
    assert result["score"]["status"] == result["score"]["answer"]["status"] == "needs_manual"
    summary, markdown = b5.summarize(plan, [result], frozen[0])
    stats = summary["modes"][mode]
    assert stats["fallback_executions"] == 1 and stats["overall"]["evaluated"] == 0
    assert stats["overall"]["needs_manual"] == 1
    if mode == "v11":
        assert stats["tool"]["evaluated"] == 0
        assert result["score"]["tool"]["status"] == "needs_manual"
    assert "fallback executions: 1" in markdown


def test_fake_guard_blocks_udp_send_and_datagram_endpoints():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
        receiver.bind(("127.0.0.2", 0))
        address = receiver.getsockname()

        async def probe():
            await asyncio.sleep(0)
            with pytest.raises(b5.Refused, match="network_forbidden_in_fake"):
                sender.sendto(b"blocked", address)
            if hasattr(sender, "sendmsg"):
                with pytest.raises(b5.Refused, match="network_forbidden_in_fake"):
                    sender.sendmsg([b"blocked"], [], 0, address)
            sender.setblocking(False)
            with pytest.raises(b5.Refused, match="network_forbidden_in_fake"):
                await asyncio.get_running_loop().sock_sendto(sender, b"blocked", address)
            if sys.platform == "win32":
                with pytest.raises(b5.Refused, match="network_forbidden_in_fake"):
                    asyncio.get_running_loop()._proactor.sendto(sender, b"blocked", 0, address)
            with pytest.raises(b5.Refused, match="network_forbidden_in_fake"):
                await asyncio.get_running_loop().create_datagram_endpoint(asyncio.DatagramProtocol, remote_addr=address)

        with b5.fake_network_guard():
            asyncio.run(probe())
        receiver.setblocking(False)
        with pytest.raises(BlockingIOError):
            receiver.recvfrom(1024)


@pytest.mark.parametrize("configured", ["missing", "different", "legacy_override"])
def test_worker_requires_isolated_data_directory_before_app_import(tmp_path, frozen, configured):
    plan, entry, directory = prepared_turn(tmp_path / "run", frozen)
    home = tmp_path / "untouched-home"
    home.mkdir()
    env = os.environ.copy()
    env.pop("AMADEUS_DATA_DIR", None)
    env.pop("AMADEUS_DB_PATH", None)
    if configured == "different":
        env["AMADEUS_DATA_DIR"] = str(home / "wrong-data")
    elif configured == "legacy_override":
        env["AMADEUS_DATA_DIR"] = str(directory / "data")
        env["AMADEUS_DB_PATH"] = str(home / "legacy.sqlite3")
    env.update(HOME=str(home), USERPROFILE=str(home), APPDATA=str(home), LOCALAPPDATA=str(home))
    result = subprocess.run([sys.executable, str(b5.SCRIPT), "--worker", str(directory)], cwd=b5.BACKEND,
                            env=env, capture_output=True, timeout=30, check=False)
    assert result.returncode == 2
    assert result.stderr.splitlines()[-1] == b"B5 refused: worker_data_directory_not_isolated"
    assert list(home.iterdir()) == []
    assert not list(tmp_path.rglob("*.sqlite3")) and not list(tmp_path.rglob("*.db"))
    assert not (directory / "result.json").exists()
