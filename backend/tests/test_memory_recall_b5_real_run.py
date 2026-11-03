"""B5 benchmark contract checks. No real provider or external network is used."""

from __future__ import annotations

import asyncio
import copy

import json

import os

import socket


import subprocess

import sys



from decimal import Decimal

from pathlib import Path


import httpx

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import memory_recall_b5_real_run as b5

@pytest.fixture
def frozen():
    return b5.load_questions()

@pytest.fixture(autouse=True)
def restore_product_embedder(monkeypatch, tmp_path):
    # Direct product_turn tests replace the process-wide embedder, just as a
    # worker does. Restore it before another test uses the shared service.
    from app.services.memory import memory_service

    monkeypatch.setattr(memory_service, "embedder", memory_service.embedder)
    original = b5.load_embedder
    monkeypatch.setattr(b5, "load_embedder", lambda plan: original(
        {**plan, "transport": "fake", "model_dir": str(tmp_path / 'no-model')}))

def call(needs):
    return {"id": "test-call", "type": "function", "function": {
        "name": "recall_memory", "arguments": json.dumps({"needs": needs})}}

def example_case(frozen, **expected):
    case = copy.deepcopy(frozen[0]["cases"][0])
    case["expected"].update(recall_memory="allowed", exact_need_count=None,
                            must_use_fact_keys=[], must_not_use_fact_keys=[])
    case["expected"].update(expected)
    return case

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

@pytest.mark.parametrize("leaked", ["must_use_fact_keys", "b5c_board", "must_express_unknown_for", "shoe_size"])
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

@pytest.mark.parametrize("category,calls,status", [
    ("required", [], "fail"), ("required", [call(["one"])], "pass"),
    ("forbidden", [], "pass"), ("forbidden", [call(["one"])], "fail"),
    ("allowed", [], "pass"), ("allowed", [call(["one"])], "pass"),
])
def test_scoring_recall_categories(frozen, category, calls, status):
    case = example_case(frozen, recall_memory=category)
    assert b5.score(case, "了解したわ。", calls, frozen[0])["status"] == status

def test_redaction_handles_quoted_and_unicode_secret(tmp_path, monkeypatch):
    secret = 'fake-"密钥\\value'
    # POSIX ASCII environments cannot export Unicode; redact only needs a mapping.
    monkeypatch.setattr(b5.os, "environ", {**os.environ, "DEEPSEEK_API_KEY": secret})
    b5.write_json(tmp_path / "redacted.json", {"reply": "echo " + secret})
    assert b5.read_json(tmp_path / "redacted.json") == {"reply": "echo [REDACTED]"}
    for ascii_only in (True, False):
        b5.write_json(tmp_path / "sse.json", {"sse": json.dumps({"content": secret}, ensure_ascii=ascii_only)})
        assert b5.read_json(tmp_path / "sse.json") == {"sse": '{"content": "[REDACTED]"}'}

def prepared_turn(tmp_path, frozen, *, mode="v11", case_id="irrelevant_arithmetic", transport="fake", cap_cny=None):
    qset, digest = frozen
    plan = b5.make_plan(qset, digest, cap_cny=cap_cny)
    plan["transport"] = transport
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

def test_actual_usage_exceeding_reserve_persists_stop_reason(tmp_path):
    budget = b5.Budget(tmp_path / "budget.json", "10")
    index = budget.reserve({"messages": [], "max_tokens": 1}, "test")
    with pytest.raises(b5.AccountingUncertain, match="usage_exceeded_reservation"):
        budget.settle(index, {"prompt_tokens": 1_000_000, "completion_tokens": 1})
    restarted = b5.Budget(tmp_path / "budget.json", "10")
    assert restarted.state["stop_reason"] == "usage_exceeded_reservation_stop"
    assert Decimal(restarted.amounts()["settled_cny"]) > 2

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

@pytest.fixture
def paid_mock(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-placeholder")
    monkeypatch.setenv("AMADEUS_B5_REAL_HTTP", "authorized")
    original = httpx.AsyncClient
    waits = []

    async def sleep(seconds):
        waits.append(seconds)

    def install(handler):
        def client(*args, **kwargs):
            kwargs.setdefault("transport", httpx.MockTransport(handler))
            return original(*args, **kwargs)
        monkeypatch.setattr(httpx, "AsyncClient", client)

    monkeypatch.setattr(b5.asyncio, "sleep", sleep)
    return install, waits

async def test_transport_retry_completes_and_charges_failed_reserve(tmp_path, frozen, paid_mock):
    install, waits = paid_mock
    seen = []
    detail = "sk-" + "x" * 30 + " Bearer hidden-token " + "断" * 400

    def provider(request):
        seen.append(json.loads(request.content))
        if len(seen) == 1:
            raise httpx.ReadTimeout(detail)
        return httpx.Response(200, text=stream_reply(seen[-1]))

    install(provider)
    plan, entry, directory = prepared_turn(tmp_path, frozen, transport="real", cap_cny="1")
    with b5.fake_network_guard():
        result = await b5.product_turn(plan, entry, directory)
    b5.write_json(directory / "result.json", result)
    assert result["status"] == "completed" and not result.get("stop_run")
    assert waits == [5] and seen[0] == seen[1]
    rows = b5.read_json(directory / "requests.json")
    assert len(rows) == 2 and rows[0]["error_type"] == "ReadTimeout"
    assert len(rows[0]["error_detail"]) == 300
    assert "sk-" not in rows[0]["error_detail"] and "hidden-token" not in rows[0]["error_detail"]
    assert rows[0]["error_detail"].startswith("[REDACTED] Bearer [REDACTED]")
    assert result["error_detail"] == rows[0]["error_detail"]
    budget = b5.Budget(tmp_path / "budget.json", "1")
    failed, succeeded = budget.state["attempts"]
    assert failed["status"] == "reserved" and failed["charge_cny"] == failed["reserve_cny"]
    assert succeeded["status"] == "settled"
    assert Decimal(result["retained_reserve_cny"]) == Decimal(failed["reserve_cny"]) > 0
    assert Decimal(result["cost_cny"]) == budget.committed == Decimal(failed["reserve_cny"]) + Decimal(succeeded["charge_cny"])
    assert not budget.uncertain()

@pytest.mark.parametrize("failure,count,error_type", [
    (httpx.ReadTimeout("timeout"), 2, "ReadTimeout"),
    (httpx.RemoteProtocolError("disconnected"), 2, "RemoteProtocolError"),
    (429, 2, "429"), (503, 2, "503"), (400, 1, "400"),
    (b5.Refused("refused"), 1, "Refused"), ("missing_usage", 1, "AccountingUncertain"),
])
async def test_only_network_failures_retry(tmp_path, frozen, paid_mock, failure, count, error_type):
    install, waits = paid_mock
    seen = []

    def provider(request):
        seen.append(request)
        if isinstance(failure, Exception):
            raise failure
        if isinstance(failure, int):
            return httpx.Response(failure)
        return httpx.Response(200, text=stream_reply(json.loads(request.content), usage=False))

    install(provider)
    plan, entry, directory = prepared_turn(tmp_path, frozen, transport="real", cap_cny="1")
    capture = b5.Capture(plan, entry, frozen[0], frozen[0]["cases"][0], directory)
    with b5.fake_network_guard(), pytest.raises(b5.Refused):
        await capture.handle(httpx.Request("POST", plan["base_url"] + "/chat/completions", json={"messages": []}))
    assert len(seen) == count and waits == ([5] if count == 2 else [])
    rows = b5.read_json(directory / "requests.json")
    assert all(r["error_type"] == error_type for r in rows)
    assert all(Decimal(r["retained_reserve_cny"]) > 0 for r in rows)

async def test_transport_retry_still_checks_cap_including_retained_reserve(tmp_path, frozen, paid_mock):
    install, waits = paid_mock
    seen = []

    def provider(request):
        seen.append(request)
        raise httpx.ReadTimeout("timeout")

    install(provider)
    plan, entry, directory = prepared_turn(tmp_path, frozen, transport="real", cap_cny="0.025")
    capture = b5.Capture(plan, entry, frozen[0], frozen[0]["cases"][0], directory)
    with b5.fake_network_guard(), pytest.raises(b5.BudgetExceeded):
        await capture.handle(httpx.Request("POST", plan["base_url"] + "/chat/completions", json={"messages": []}))
    assert len(seen) == 1 and waits == [5]
    assert Decimal(capture.budget.amounts()["retained_reserve_cny"]) > 0
    assert len(capture.budget.state["attempts"]) == 1

@pytest.mark.parametrize("failures", [1, 3])
def test_transport_errors_continue_until_third_execution_and_resume(tmp_path, frozen, paid_mock, monkeypatch, failures):
    install, waits = paid_mock
    qset, digest = frozen
    plan = b5.make_plan(qset, digest, cap_cny="1")
    plan["transport"] = "real"
    plan["executions"] = plan["executions"][:5]
    active, sent = [], []

    def provider(request):
        sent.append(active[-1])
        if active[-1] <= failures:
            raise httpx.ReadTimeout("timeout")
        return httpx.Response(200, text=stream_reply(json.loads(request.content)))

    install(provider)

    def executor(plan, entry, directory):
        active.append(len(active) + 1)
        monkeypatch.setenv("AMADEUS_DATA_DIR", str(directory / "data"))
        return asyncio.run(b5.product_turn(plan, entry, directory))

    with b5.fake_network_guard():
        b5.run_suite(tmp_path, plan, qset, executor)
        original_count = len(sent)
        b5.run_suite(tmp_path, plan, qset, executor)
    assert len(sent) == original_count
    rows = [b5.read_json(tmp_path / "executions" / e["id"] / "result.json") for e in plan["executions"]]
    assert [r["status"] for r in rows] == ["error"] * failures + (
        ["completed"] * 4 if failures == 1 else ["not_run_after_accounting_error"] * 2)
    assert all(r["error_type"] == "ReadTimeout" and not r.get("stop_run") for r in rows[:failures])
    assert waits == [5] * failures


@pytest.mark.parametrize('forced_search', [False, True])
async def test_current_product_request_budget_and_second_round(tmp_path, frozen, monkeypatch, forced_search):
    from app import config
    from app.routers import chat_ws
    from app.services import turn_tool_orchestrator as orch

    plan, entry, directory = prepared_turn(tmp_path, frozen, case_id='outside_paraphrase_board')
    budgets, second, order = [], [], []
    trim, prepare = chat_ws.trim_request_history, orch.prepare_second_round_payload
    split, envelope = chat_ws.split_current_turn, chat_ws.request_envelope_tokens

    def tracked_split(*args, **kwargs):
        order.append('split')
        return split(*args, **kwargs)

    def tracked_trim(*args, **kwargs):
        order.append('trim')
        budgets.append(args[-1])
        return trim(*args, **kwargs)

    def tracked_envelope(*args, **kwargs):
        order.append('envelope')
        return envelope(*args, **kwargs)

    def tracked_prepare(*args, **kwargs):
        second.append(kwargs)
        return prepare(*args, **kwargs)

    monkeypatch.setattr(chat_ws, 'should_force_search', lambda query: forced_search)
    monkeypatch.setattr(chat_ws, 'split_current_turn', tracked_split)
    monkeypatch.setattr(chat_ws, 'trim_request_history', tracked_trim)
    monkeypatch.setattr(chat_ws, 'request_envelope_tokens', tracked_envelope)
    monkeypatch.setattr(orch, 'prepare_second_round_payload', tracked_prepare)
    with b5.fake_network_guard():
        row = await b5.product_turn(plan, entry, directory)
    assert row['status'] == 'completed', row
    assert budgets and all(budget == config.REQUEST_INPUT_BUDGET for budget in budgets)
    assert order[:2] == ['split', 'trim'] and order.index('envelope') > order.index('trim')
    assert second and all(callable(kwargs['message_builder']) for kwargs in second)
    assert row['request_count'] == 2 and row['calls'][0]['function']['name'] == 'recall_memory'
