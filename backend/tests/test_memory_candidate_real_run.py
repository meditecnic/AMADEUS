from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = BACKEND_ROOT / "scripts" / "memory_candidate_real_run.py"
PYTHON = Path(sys.executable)

sys.path.insert(0, str(BACKEND_ROOT / "scripts"))
# PYTHON_DOTENV_DISABLED and AMADEUS_CREDENTIAL_BACKEND=memory are set by
# tests/conftest.py before any test module is imported.
import memory_candidate_real_run as b3  # noqa: E402


def _restore_at_teardown(monkeypatch, obj, *names: str) -> None:
    """Have ``monkeypatch`` put ``obj.<name>`` back at teardown.

    Script helpers called by these tests (install_allowlisted_httpx,
    memory_candidate_runtime._install_external_fakes) replace shared module,
    class and instance attributes and never undo it. Registering the current
    values first makes this test's monkeypatch restore them, whoever replaces
    them later. Instance attributes are tracked through the instance
    ``__dict__``, so an override of a class-provided method is removed again
    rather than pinned on the instance.
    """
    import types

    for name in names:
        if isinstance(obj, (type, types.ModuleType)):
            monkeypatch.setattr(obj, name, getattr(obj, name))
        else:
            monkeypatch.setitem(vars(obj), name, getattr(obj, name))


def _restore_httpx_at_teardown(monkeypatch) -> None:
    import httpx

    _restore_at_teardown(monkeypatch, httpx, "get", "request")
    _restore_at_teardown(monkeypatch, httpx.AsyncClient, "send", "post", "stream")
    _restore_at_teardown(monkeypatch, httpx.Client, "send")
    _restore_at_teardown(monkeypatch, httpx.HTTPTransport, "handle_request")
    _restore_at_teardown(monkeypatch, httpx.AsyncHTTPTransport, "handle_async_request")


def _cli(*argv: str, check: bool = True, timeout: int = 60) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(BACKEND_ROOT)
    env["PYTHON_DOTENV_DISABLED"] = "1"
    env["AMADEUS_CREDENTIAL_BACKEND"] = "memory"
    env.pop("AMADEUS_DATA_DIR", None)
    env.pop("DEEPSEEK_API_KEY", None)
    return subprocess.run(
        [str(PYTHON), str(SCRIPT), *argv],
        cwd=str(BACKEND_ROOT),
        env=env,
        text=True,
        encoding="utf-8",
        errors="strict",
        capture_output=True,
        timeout=timeout,
        check=check,
    )


def test_cli_rejects_credential_flags():
    probed = _cli("--api-key", "secret", "print-plan", check=False)
    assert probed.returncode != 0
    assert "credential_on_cli" in probed.stderr
    assert "secret" not in probed.stdout


def test_print_plan_has_gated_real_http_and_cny_cap():
    probed = _cli("print-plan", check=False)
    assert probed.returncode == 0, probed.stderr
    payload = json.loads(probed.stdout)
    assert payload["request_id"] == "deepseek-flash"
    assert payload["provider_id"] == "deepseek"
    assert payload["max_http_attempts"] == 24
    assert payload["max_user_turns"] == 12
    assert payload["currency_cap"] == 10
    assert payload["currency_cap_cny"] == 10
    assert payload["pricing"]["reserve_cny_per_completion"] == 6
    assert payload["pricing"]["peak_input_cny_per_million"] == 2
    assert payload["pricing"]["peak_output_cny_per_million"] == 8
    assert payload["real_http_authorized"] is False
    assert payload["credential"]["cli"] is False
    channels = [row["channel"] for row in payload["synthetic_dialogue"]]
    assert any("PATCH /api/memory/facts" in channel for channel in channels)
    assert any("forget" in channel for channel in channels)
    assert any("GET /api/memory/facts" in channel for channel in channels)
    assert "陶笛" in probed.stdout
    assert "故城" in probed.stdout
    assert payload["compile_budget"]["prompt_input_budget"] == 12000
    assert payload["request_max_tokens"]["chat"] == 2000
    assert payload["real_http_gate"]["still_closed_by_default"] is True
    assert "real embedding" in payload["future_real_needs"]["still_missing"][0]


def test_budget_probe_stops_before_overrun_and_blocks_foreign_hosts(tmp_path):
    run_root = tmp_path / "b3-budget"
    probed = _cli(
        "budget-probe",
        "--run-root",
        str(run_root),
        "--max-http-attempts",
        "3",
        check=False,
        timeout=30,
    )
    assert probed.returncode == 0, probed.stderr + probed.stdout
    payload = json.loads(probed.stdout)
    assert payload["ok"] is True
    assert payload["blocked_foreign"] is True
    assert payload["budget_stop"]
    assert payload["http_attempts"] == 3
    ids = [row["id"] for row in payload["models"]["data"]]
    assert ids == ["deepseek-flash", "deepseek-v4-flash"]
    report = json.loads((run_root / "captures" / "budget_probe.json").read_text(encoding="utf-8"))
    assert report["ok"] is True


def test_auth_fail_inject_does_not_retry_or_swap_provider(tmp_path):
    run_root = tmp_path / "b3-auth"
    probed = _cli(
        "budget-probe",
        "--run-root",
        str(run_root),
        "--max-http-attempts",
        "4",
        "--inject",
        "auth_fail",
        check=False,
        timeout=30,
    )
    assert probed.returncode == 0, probed.stderr + probed.stdout
    payload = json.loads(probed.stdout)
    assert payload["auth_fail_no_retry"] is True


def test_load_credential_from_file_not_argv(tmp_path):
    secret_file = tmp_path / "key.txt"
    secret_file.write_text("not-for-cli\n", encoding="utf-8")
    value = b3.load_credential_from_env(
        {"AMADEUS_B3_CREDENTIAL_FILE": str(secret_file)}
    )
    assert value == "not-for-cli"
    assert b3.load_credential_from_env({"AMADEUS_B3_PROVIDER_KEY": "from-env"}) == "from-env"
    assert b3.redact_headers({"Authorization": "Bearer abc", "X-Request-Id": "1"}) == {
        "Authorization": "<redacted>",
        "X-Request-Id": "1",
    }


def test_http_budget_stops_before_the_next_request():
    budget = b3.HttpBudget(max_http_attempts=1, max_user_turns=1)
    budget.before_http("http://127.0.0.1/models")
    with pytest.raises(b3.BudgetExhausted, match="budget_http_exhausted"):
        budget.before_http("http://127.0.0.1/models")
    budget.before_user_turn()
    with pytest.raises(b3.BudgetExhausted, match="budget_turns_exhausted"):
        budget.before_user_turn()


def test_real_transport_is_gated():
    with pytest.raises(SystemExit):
        b3.cmd_local_serve(
            type("Args", (), {
                "transport": "real",
                "run_root": "unused",
                "backend_port": 8001,
                "frontend_port": 1422,
                "inject": "",
            })()
        )


def test_print_plan_output_file_is_utf8(tmp_path):
    target = tmp_path / "print-plan.json"
    probed = _cli("print-plan", "--output", str(target), check=False)
    assert probed.returncode == 0, probed.stderr
    text = target.read_text(encoding="utf-8")
    assert "陶笛" in text
    assert "口琴" in text
    assert "forget_long_term" in json.dumps(json.loads(text), ensure_ascii=False)


def test_verify_turns_are_lexical_hits_on_revised_fact():
    assert b3.TURN_AFTER_EDIT in b3.REVISED_DISPLAY
    assert b3.TURN_AFTER_FORGET in b3.REVISED_DISPLAY
    assert b3.FACT_MARKER_HARMONICA in b3.REVISED_DISPLAY
    assert b3.FACT_MARKER_HARMONICA not in b3.TURN_AFTER_EDIT
    assert b3.FACT_MARKER_HARMONICA not in b3.TURN_AFTER_FORGET
    assert b3.TURN_EXTRACT not in b3.REVISED_DISPLAY


def test_budget_persists_across_load_and_rejects_corrupt_or_incompatible(tmp_path):
    path = tmp_path / "budget.json"
    first = b3.HttpBudget.load_or_create(
        path,
        max_http_attempts=4,
        max_user_turns=2,
        candidate="cand-a",
    )
    first.before_http("http://127.0.0.1/models", kind="catalog")
    first.before_user_turn()
    first.record_usage({"usage": {"prompt_tokens": 11, "completion_tokens": 5}})
    loaded = b3.HttpBudget.load_or_create(
        path,
        max_http_attempts=4,
        max_user_turns=2,
        candidate="cand-a",
    )
    assert loaded.http_attempts == 1
    assert loaded.user_turns == 1
    assert loaded.input_tokens == 11
    assert loaded.output_tokens == 5
    assert loaded.usage_known == 1
    with pytest.raises(b3.BudgetStateError, match="do not match"):
        b3.HttpBudget.load(
            path,
            max_http_attempts=8,
            max_user_turns=2,
            candidate="cand-a",
        )
    with pytest.raises(b3.BudgetStateError, match="candidate mismatch"):
        b3.HttpBudget.load(
            path,
            max_http_attempts=4,
            max_user_turns=2,
            candidate="cand-b",
        )
    path.write_text("{", encoding="utf-8")
    with pytest.raises(b3.BudgetStateError, match="unreadable"):
        b3.HttpBudget.load(
            path,
            max_http_attempts=4,
            max_user_turns=2,
            candidate="cand-a",
        )
    path.write_text(
        json.dumps(
            {
                "http_attempts": 1,
                "max_http_attempts": 4,
                "user_turns": 0,
                "max_user_turns": 2,
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(b3.BudgetStateError, match="missing"):
        b3.HttpBudget.load(
            path,
            max_http_attempts=4,
            max_user_turns=2,
            candidate="cand-a",
        )


def test_budget_unknown_usage_is_not_recorded_as_zero():
    budget = b3.HttpBudget(max_http_attempts=3, max_user_turns=1)
    budget.before_http("http://127.0.0.1/models", kind="catalog")
    budget.record_usage({"object": "list"})
    budget.before_http("http://127.0.0.1/chat/completions", kind="chat")
    budget.record_usage({"usage": {"prompt_tokens": 9, "completion_tokens": 3}})
    assert budget.usage_unknown == 1
    assert budget.usage_known == 1
    assert budget.input_tokens == 9
    assert budget.output_tokens == 3


def test_concurrent_http_budget_does_not_overshoot():
    budget = b3.HttpBudget(max_http_attempts=20, max_user_turns=20)
    errors: list[str] = []

    def worker():
        try:
            budget.before_http("http://127.0.0.1/models", kind="catalog")
        except b3.BudgetExhausted:
            errors.append("exhausted")

    threads = [threading.Thread(target=worker) for _ in range(50)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert budget.http_attempts == 20
    assert len(errors) == 30
    assert len(budget.rejected) == 30


def test_complete_json_extract_reaches_local_mock(tmp_path, monkeypatch):
    import asyncio

    import httpx

    from app.services.provider_runtime import deepseek_service

    capture = tmp_path / "captures"
    capture.mkdir()
    budget = b3.HttpBudget(max_http_attempts=4, max_user_turns=1, path=capture / "budget.json")
    server, base_url = b3.start_local_provider(
        inject="fail_first_extract",
        capture_log=capture / "mock_http.jsonl",
    )
    _restore_httpx_at_teardown(monkeypatch)
    try:
        b3.install_allowlisted_httpx(
            budget=budget,
            allowed_hosts=b3.allowed_hosts_for(base_url),
            capture_dir=capture,
            original_async_send=httpx.AsyncClient.send,
            original_sync_send=httpx.Client.send,
        )
        monkeypatch.setattr(deepseek_service, "base_url", base_url)
        messages = [
            {"role": "system", "content": "You extract durable user memory from one user utterance.\n" * 20},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "source_message_id": 7,
                        "user_text": b3.TURN_EXTRACT,
                    },
                    ensure_ascii=False,
                ),
            },
        ]

        async def _call():
            try:
                await deepseek_service.complete_json(
                    messages,
                    api_key=b3.LOCAL_MOCK_CREDENTIAL,
                    model="deepseek-flash",
                )
            except Exception:
                pass
            return await deepseek_service.complete_json(
                messages,
                api_key=b3.LOCAL_MOCK_CREDENTIAL,
                model="deepseek-flash",
            )

        payload = asyncio.run(asyncio.wait_for(_call(), timeout=8))
        assert payload["observations"][0]["relation"] == "new"
        kinds = [row["kind"] for row in b3._mock_rows({"captures": capture})]
        assert "extract" in kinds
    finally:
        server.shutdown()
        server.server_close()


def test_start_real_is_gated_without_authorization():
    probed = _cli(
        "start-real",
        "--run-root",
        str(Path("unused")),
        check=False,
    )
    assert probed.returncode != 0
    assert "real_http_not_authorized" in probed.stderr


def test_two_budget_instances_share_disk_authority(tmp_path):
    path = tmp_path / "budget.json"
    first = b3.HttpBudget.load_or_create(
        path, max_http_attempts=1, max_user_turns=1, candidate="synthetic"
    )
    second = b3.HttpBudget.load_or_create(
        path, max_http_attempts=1, max_user_turns=1, candidate="synthetic"
    )
    accepted = 0
    for budget in (first, second):
        try:
            budget.before_http("https://example.invalid/test", kind="chat")
            accepted += 1
        except b3.BudgetExhausted:
            pass
    disk = json.loads(path.read_text(encoding="utf-8"))
    assert accepted == 1
    assert disk["http_attempts"] == 1
    assert disk["http_attempts"] == disk["max_http_attempts"]


def test_usage_attaches_to_own_attempt_when_completion_is_interleaved():
    budget = b3.HttpBudget(max_http_attempts=2, max_user_turns=2)
    chat_id = budget.before_http("https://example.invalid/chat", kind="chat")
    extract_id = budget.before_http("https://example.invalid/extract", kind="extract")
    budget.record_usage(
        {"usage": {"prompt_tokens": 22, "completion_tokens": 4}},
        attempt_id=extract_id,
    )
    budget.record_usage(
        {"usage": {"prompt_tokens": 11, "completion_tokens": 3}},
        attempt_id=chat_id,
    )
    by_kind = {row["kind"]: row for row in budget.snapshot()["attempts"]}
    assert by_kind["chat"]["usage"] == {"prompt_tokens": 11, "completion_tokens": 3}
    assert by_kind["extract"]["usage"] == {"prompt_tokens": 22, "completion_tokens": 4}
    assert budget.input_tokens == 33
    assert budget.output_tokens == 7
    budget.record_usage(
        {"usage": {"prompt_tokens": 99, "completion_tokens": 99}},
        attempt_id=chat_id,
    )
    assert budget.input_tokens == 33


def test_usage_fifo_without_ids_and_errors_are_terminal():
    budget = b3.HttpBudget(max_http_attempts=3, max_user_turns=1)
    budget.before_http("https://example.invalid/chat", kind="chat")
    budget.before_http("https://example.invalid/extract", kind="extract")
    budget.record_usage({"usage": {"prompt_tokens": 11, "completion_tokens": 3}})
    budget.record_usage({"usage": {"prompt_tokens": 22, "completion_tokens": 4}})
    snap = budget.snapshot()
    assert snap["attempts"][0]["kind"] == "chat"
    assert snap["attempts"][0]["usage"]["prompt_tokens"] == 11
    assert snap["attempts"][1]["kind"] == "extract"
    assert snap["attempts"][1]["usage"]["prompt_tokens"] == 22
    failed = budget.before_http("https://example.invalid/timeout", kind="catalog")
    budget.record_usage(None, attempt_id=failed, error="TimeoutException")
    snap = budget.snapshot()
    assert snap["attempts"][2]["status"] == "error"
    assert snap["attempts"][2]["usage_unknown"] is True
    assert snap["usage_unknown"] == 1
    budget.record_usage(None, attempt_id=failed, error="TimeoutException")
    assert budget.snapshot()["usage_unknown"] == 1


def test_local_transport_does_not_reach_original_ddgs_or_weather(tmp_path, monkeypatch):
    import httpx
    from app.main import in_memory_settings
    from app.routers import chat_ws, providers, system_api, voice_ws
    from app.services import credentials as cred_mod
    from app.services import provider_runtime as runtime_mod
    from app.services import search as search_mod
    from app.services.memory import memory_service
    from app.services.search import search_service
    from app.services.tts_queue import tts_manager
    from app.services.tts_sidecar import sidecar_supervisor

    import memory_candidate_runtime as b2_runtime

    ddg_calls: list[str] = []
    get_calls: list[str] = []

    class SentinelDDGS:
        def __init__(self, *args, **kwargs):
            ddg_calls.append("original DDGS constructor reached")
            raise RuntimeError("local sentinel: no network performed")

    def sentinel_get(url, *args, **kwargs):
        get_calls.append(str(url))
        raise RuntimeError("local sentinel: no network performed")

    # Everything _install_external_fakes, its outbound guards and
    # install_allowlisted_httpx replace is restored when the test ends.
    _restore_httpx_at_teardown(monkeypatch)
    _restore_at_teardown(monkeypatch, search_mod, "DDGS")
    for mod_name in ("ddgs", "duckduckgo_search"):
        mod = sys.modules.get(mod_name)
        if mod is not None and hasattr(mod, "DDGS"):
            _restore_at_teardown(monkeypatch, mod, "DDGS")
    for module in (cred_mod, chat_ws, providers, system_api, voice_ws, search_mod):
        _restore_at_teardown(monkeypatch, module, "credential_store")
    _restore_at_teardown(monkeypatch, cred_mod.WindowsCredentialStore, "get", "set", "delete")
    _restore_at_teardown(monkeypatch, memory_service, "embedder")
    monkeypatch.setitem(in_memory_settings, "enable_tts", in_memory_settings["enable_tts"])
    _restore_at_teardown(monkeypatch, sidecar_supervisor, "ensure_available")
    _restore_at_teardown(monkeypatch, search_service, "search")
    _restore_at_teardown(
        monkeypatch,
        tts_manager,
        *[name for name in ("synthesize_async", "synthesize_and_queue") if hasattr(tts_manager, name)],
    )
    for adapter_name in ("openai_adapter", "gemini_adapter"):
        adapter = getattr(runtime_mod, adapter_name, None)
        if adapter is not None:
            _restore_at_teardown(monkeypatch, adapter, "get_chat_stream", "complete_json", "list_models")
    _restore_at_teardown(
        monkeypatch,
        runtime_mod.deepseek_service,
        "get_chat_stream",
        "translate_to_zh",
        "complete_json",
        "list_models",
    )

    monkeypatch.setattr(search_mod, "DDGS", SentinelDDGS)
    monkeypatch.setattr(httpx, "get", sentinel_get)
    captures = tmp_path / "captures"
    captures.mkdir()
    b2_runtime._install_external_fakes(
        captures,
        provider_transport="local",
        provider_credential="synthetic-test-key",
    )
    b3.install_allowlisted_httpx(
        budget=b3.HttpBudget(max_http_attempts=2, max_user_turns=2),
        allowed_hosts={"127.0.0.1"},
        capture_dir=captures,
        original_async_send=httpx.AsyncClient.send,
        original_sync_send=httpx.Client.send,
    )
    search_text = search_mod.web_search("synthetic local search probe")
    weather_text = search_mod.web_search("东京天气怎么样")
    assert ddg_calls == []
    assert get_calls == []
    assert "wttr.in" not in weather_text.lower()
    assert "blocked DDGS" in search_text or "Search error" in search_text


def test_fail_if_run_active_and_serve_lock(tmp_path, capsys):
    paths = {
        "pids": tmp_path / "pids.json",
        "root": tmp_path,
    }
    b3.b2._write_json(paths["pids"], {"backend_pid": os.getpid()})
    with pytest.raises(SystemExit) as raised:
        b3._fail_if_run_active(paths)
    assert raised.value.code == 2
    assert "run_already_active" in capsys.readouterr().err
    b3.b2._write_json(paths["pids"], {"backend_pid": 0})
    b3._fail_if_run_active(paths)
    first = b3._try_exclusive_lock(tmp_path / "serve.lock")
    assert first is not None
    second = b3._try_exclusive_lock(tmp_path / "serve.lock")
    assert second is None
    first.release()


def test_official_origin_pin_and_peak_cost():
    assert b3.official_deepseek_base_url("https://api.deepseek.com")
    assert b3.official_deepseek_base_url("https://api.deepseek.com/")
    assert not b3.official_deepseek_base_url("http://api.deepseek.com")
    assert not b3.official_deepseek_base_url("https://api.deepseek.com/v1")
    assert not b3.official_deepseek_base_url("https://gateway.example/deepseek")
    assert b3.peak_usage_micros(1_000_000, 0) == 2_000_000
    assert b3.peak_usage_micros(0, 1_000_000) == 8_000_000
    assert b3.reserve_micros_for_kind("catalog", currency_cap_micros=10_000_000) == 0
    assert (
        b3.reserve_micros_for_kind("chat", currency_cap_micros=10_000_000)
        == b3.RESERVE_MICROS_PER_COMPLETION
    )
    assert b3.reserve_micros_for_kind("chat", currency_cap_micros=None) == 0
    worst = b3.peak_usage_micros(b3.WORST_CASE_INPUT_TOKENS, b3.WORST_CASE_OUTPUT_TOKENS)
    assert worst <= b3.RESERVE_MICROS_PER_COMPLETION
    assert worst == 5_242_880


def test_money_budget_exhaustion_hold_duplicate_and_restart(tmp_path):
    reserve = b3.RESERVE_MICROS_PER_COMPLETION
    cap = b3.CURRENCY_CAP_MICROS
    budget = b3.HttpBudget(
        max_http_attempts=8,
        max_user_turns=8,
        currency_cap_micros=reserve,
    )
    budget.before_http("https://api.deepseek.com/chat/completions", kind="chat")
    with pytest.raises(b3.BudgetExhausted, match="budget_currency_exhausted"):
        budget.before_http("https://api.deepseek.com/chat/completions", kind="extract")
    assert budget.http_attempts == 1
    assert budget.reserved_micros == reserve

    path = tmp_path / "money.json"
    hold = b3.HttpBudget.load_or_create(
        path,
        max_http_attempts=8,
        max_user_turns=8,
        candidate="money",
        currency_cap_micros=cap,
    )
    attempt = hold.before_http("https://api.deepseek.com/chat/completions", kind="chat")
    hold.record_usage({"object": "chat.completion"}, attempt_id=attempt)
    assert hold.usage_unknown == 1
    assert hold.reserved_micros == reserve
    with pytest.raises(b3.BudgetExhausted, match="budget_currency_exhausted"):
        hold.before_http("https://api.deepseek.com/chat/completions", kind="extract")
    hold.record_usage(
        {"usage": {"prompt_tokens": 1, "completion_tokens": 1}},
        attempt_id=attempt,
    )
    assert hold.reserved_micros == reserve
    assert hold.settled_micros == 0

    loaded = b3.HttpBudget.load_or_create(
        path,
        max_http_attempts=8,
        max_user_turns=8,
        candidate="money",
        currency_cap_micros=cap,
    )
    assert loaded.reserved_micros == reserve
    assert loaded.http_attempts == 1
    with pytest.raises(b3.BudgetExhausted, match="budget_currency_exhausted"):
        loaded.before_http("https://api.deepseek.com/chat/completions", kind="title")
    with pytest.raises(b3.BudgetStateError, match="currency cap mismatch"):
        b3.HttpBudget.load(
            path,
            max_http_attempts=8,
            max_user_turns=8,
            candidate="money",
            currency_cap_micros=None,
        )

    settle = b3.HttpBudget(max_http_attempts=4, max_user_turns=4, currency_cap_micros=cap)
    chat_id = settle.before_http("https://api.deepseek.com/chat/completions", kind="chat")
    settle.record_usage(
        {"usage": {"prompt_tokens": 500, "completion_tokens": 50}},
        attempt_id=chat_id,
    )
    expected = b3.peak_usage_micros(500, 50)
    assert settle.settled_micros == expected
    assert settle.reserved_micros == 0
    settle.record_usage(
        {"usage": {"prompt_tokens": 9, "completion_tokens": 9}},
        attempt_id=chat_id,
    )
    assert settle.settled_micros == expected
    next_id = settle.before_http("https://api.deepseek.com/chat/completions", kind="extract")
    assert next_id
    settle.record_usage(None, attempt_id=next_id, error="TimeoutException")
    assert settle.reserved_micros == reserve
    assert settle.snapshot()["attempts"][-1]["status"] == "error"


def test_money_probe_cli(tmp_path):
    probed = _cli(
        "money-probe",
        "--run-root",
        str(tmp_path / "money-probe"),
        check=False,
        timeout=30,
    )
    assert probed.returncode == 0, probed.stderr + probed.stdout
    payload = json.loads(probed.stdout)
    assert payload["ok"] is True
    assert payload["cases"]["exhaustion"]["second_blocked"] is True
    assert payload["cases"]["concurrency"]["accepted"] == 1
    assert payload["cases"]["duplicate_callback"]["reserved_after"] == 0
    assert payload["cases"]["no_usage_hold"]["second_blocked"] is True
    assert payload["cases"]["restart_accumulation"]["second_blocked"] is True
    assert payload["cases"]["catalog_zero_reserve"]["chat_accepted_after_catalog"] is True
    assert payload["cases"]["request_params"]["max_tokens"] == 2000
    assert payload["cases"]["request_params"]["model"] == "deepseek-flash"


def test_start_real_authorized_without_credential_is_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("AMADEUS_B3_PROVIDER_KEY", raising=False)
    monkeypatch.delenv("AMADEUS_B3_CREDENTIAL_FILE", raising=False)
    monkeypatch.setenv("AMADEUS_B3_REAL_HTTP", "authorized")
    monkeypatch.setattr(b3, "_read_windows_deepseek_credential", lambda: None)
    with pytest.raises(SystemExit):
        b3.cmd_start_real(
            type(
                "Args",
                (),
                {
                    "run_root": str(tmp_path / "unused"),
                    "backend_port": 8001,
                    "frontend_port": 1422,
                    "python": "",
                    "backend_only": True,
                    "max_http_attempts": 24,
                    "max_user_turns": 12,
                    "currency_cap_cny": 10,
                },
            )()
        )


_RECORDED_OK_REPLY = [
    {
        "type": "segment.ready",
        "ja": "口琴の練習曲くらい、覚えてるわよ。",
        "zh": "",
        "emotion": "neutral",
    },
    {"type": "status", "dsk": "idle", "state": "done", "tts": "idle"},
]

_RECORDED_BLOCKED_THIRD = [
    {
        "type": "turn.started",
        "conversation_id": "aeb35b34-1025-4174-85a5-94b81390b791",
        "turn_id": "0e06f678-cf67-41be-b0d0-5efd41f1161e",
        "generation": 3,
    },
    {"type": "status", "dsk": "thinking", "state": "thinking", "tts": "idle"},
    {
        "type": "turn.cancelled",
        "conversation_id": "aeb35b34-1025-4174-85a5-94b81390b791",
        "turn_id": "0e06f678-cf67-41be-b0d0-5efd41f1161e",
        "generation": 3,
    },
    {
        "type": "error",
        "code": "turn_failed",
        "recoverable": True,
        "message": (
            "Server error: budget_currency_exhausted before "
            "https://api.deepseek.com/chat/completions: settled=33664 "
            "reserved=6000000 need=6000000 cap=10000000"
        ),
    },
]


def test_classify_verify_real_blocked_third_is_partial_not_ok():
    result = b3.classify_verify_real_status(
        auth_failed=False,
        over_cap=False,
        official_host_only=True,
        chat_kind_present=True,
        promoted=True,
        first_events=_RECORDED_OK_REPLY,
        after_edit_events=_RECORDED_OK_REPLY,
        after_forget_events=_RECORDED_BLOCKED_THIRD,
    )
    assert result["status"] == "partial"
    assert result["ok"] is False
    assert result["stop"] == "budget_or_cancel"
    assert result["incomplete_turn"] == "after_forget"


def test_classify_verify_real_complete_when_required_replies_exist():
    result = b3.classify_verify_real_status(
        auth_failed=False,
        over_cap=False,
        official_host_only=True,
        chat_kind_present=True,
        promoted=True,
        first_events=_RECORDED_OK_REPLY,
        after_edit_events=_RECORDED_OK_REPLY,
        after_forget_events=_RECORDED_OK_REPLY,
    )
    assert result["status"] == "complete"
    assert result["ok"] is True
    assert result.get("stop") is None


def test_classify_verify_real_auth_failure_is_failed():
    result = b3.classify_verify_real_status(
        auth_failed=True,
        over_cap=False,
        official_host_only=True,
        chat_kind_present=True,
        promoted=True,
        first_events=_RECORDED_BLOCKED_THIRD,
        after_edit_events=[],
        after_forget_events=[],
    )
    assert result["status"] == "failed"
    assert result["ok"] is False
    assert result["stop"] == "auth_failed"


def test_completion_guard_rejects_other_model_before_budget(tmp_path, monkeypatch):
    import httpx

    capture = tmp_path / "captures"
    capture.mkdir()
    budget = b3.HttpBudget(
        max_http_attempts=8,
        max_user_turns=4,
        path=capture / "budget.json",
        currency_cap_micros=b3.CURRENCY_CAP_MICROS,
    )
    server, base_url = b3.start_local_provider(capture_log=capture / "mock_http.jsonl")
    _restore_httpx_at_teardown(monkeypatch)
    try:
        installed = b3.install_allowlisted_httpx(
            budget=budget,
            allowed_hosts=b3.allowed_hosts_for(base_url),
            capture_dir=capture,
            original_async_send=httpx.AsyncClient.send,
            original_sync_send=httpx.Client.send,
        )
        before_attempts = budget.http_attempts
        before_reserved = budget.reserved_micros
        with httpx.Client(timeout=5.0) as client:
            with pytest.raises(
                RuntimeError, match="blocked unauthorized completion model"
            ):
                client.post(
                    f"{base_url}/chat/completions",
                    json={
                        "model": "deepseek-v4-pro",
                        "messages": [{"role": "user", "content": "hi"}],
                    },
                )
            with pytest.raises(
                RuntimeError, match="blocked unauthorized completion model"
            ):
                client.post(
                    f"{base_url}/chat/completions",
                    json={
                        "model": "deepseek-v4-flash",
                        "messages": [{"role": "user", "content": "hi"}],
                    },
                )
            with pytest.raises(RuntimeError, match="blocked non-allowlisted"):
                client.get("https://example.com/")
        assert budget.http_attempts == before_attempts
        assert budget.reserved_micros == before_reserved
        assert budget.settled_micros == 0
        mock_after_reject = len(b3._mock_rows({"captures": capture}))
        with httpx.Client(timeout=5.0) as client:
            response = client.post(
                f"{base_url}/chat/completions",
                json={
                    "model": "deepseek-flash",
                    "messages": [{"role": "user", "content": "hi"}],
                    "max_tokens": 64,
                },
            )
        assert response.status_code == 200
        assert budget.http_attempts == before_attempts + 1
        assert len(b3._mock_rows({"captures": capture})) == mock_after_reject + 1
        assert any(
            row.get("model") == "deepseek-v4-pro" for row in _outbound_rows(capture)
        )
        assert installed["blocked"]
    finally:
        server.shutdown()
        server.server_close()


def _outbound_rows(capture: Path) -> list[dict]:
    log = capture / "outbound_http.jsonl"
    if not log.is_file():
        return []
    return [
        json.loads(line)
        for line in log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


@pytest.mark.parametrize("mode", ["sync", "async", "sync_stream", "async_stream"])
@pytest.mark.parametrize("status", [200, 401, 403])
async def test_guard_records_http_status_before_stream_consumption(tmp_path, monkeypatch, mode, status):
    import httpx

    _restore_httpx_at_teardown(monkeypatch)
    capture = tmp_path / "captures"
    capture.mkdir()
    budget = b3.HttpBudget(path=capture / "budget.json", currency_cap_micros=10_000_000)
    payload = {"usage": {"prompt_tokens": 10, "completion_tokens": 2}}
    stream = mode.endswith("stream")

    def respond(request):
        if stream:
            return httpx.Response(status, text="data: " + json.dumps(payload) + "\n\ndata: [DONE]\n\n")
        return httpx.Response(status, json=payload)

    b3.install_allowlisted_httpx(
        budget=budget, allowed_hosts={"provider.invalid"}, capture_dir=capture,
        original_async_send=httpx.AsyncClient.send, original_sync_send=httpx.Client.send,
    )
    request_payload = {
        "model": b3.DEFAULT_REQUEST_ID, "stream": stream,
        "messages": [{"role": "user", "content": "hello"}],
    }

    def check_record():
        stored = json.loads((capture / "budget.json").read_text(encoding="utf-8"))
        attempt = stored["attempts"][0]
        assert attempt["status_code"] == status
        assert b3._events_indicate_auth_failure(stored) is (status in {401, 403})
        if stream:
            assert attempt["status"] == "occupied"
            assert attempt["settled_micros"] is None
            assert stored["reserved_micros"] == b3.RESERVE_MICROS_PER_COMPLETION

    if mode.startswith("async"):
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            request = client.build_request("POST", "https://provider.invalid/chat/completions", json=request_payload)
            response = await client.send(request, stream=stream)
            check_record()
            if stream and status == 200:
                assert [line async for line in response.aiter_lines()]
            await response.aclose()
    else:
        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            request = client.build_request("POST", "https://provider.invalid/chat/completions", json=request_payload)
            response = client.send(request, stream=stream)
            check_record()
            if stream and status == 200:
                assert list(response.iter_lines())
            response.close()
    if status == 200:
        assert budget.snapshot()["usage_known"] == 1
        assert budget.snapshot()["settled_micros"] == b3.peak_usage_micros(10, 2)


@pytest.mark.parametrize("phase", ["first", "after_edit", "after_forget"])
@pytest.mark.parametrize("reason", ["budget", "auth", "cancel"])
def test_classification_ignores_error_message_with_run_records(phase, reason):
    results = []
    for message in ["Server error: budget_currency_exhausted API error (401)", "Server error"]:
        events = {name: _RECORDED_OK_REPLY for name in ("first", "after_edit", "after_forget")}
        events[phase] = [{"type": "error", "code": "turn_failed", "recoverable": True, "message": message}]
        records = {"attempts": [], "rejected": []}
        if reason == "budget":
            records["rejected"] = [{"reason": "budget_currency_exhausted"}]
        elif reason == "auth":
            records["attempts"] = [{"kind": "chat", "status_code": 401}]
        else:
            events[phase].insert(0, {"type": "turn.cancelled"})
        results.append(b3.classify_verify_real_status(
            auth_failed=b3._events_indicate_auth_failure(records),
            over_cap=False, official_host_only=True, chat_kind_present=True, promoted=True,
            first_events=events["first"], after_edit_events=events["after_edit"],
            after_forget_events=events["after_forget"], turn_records={phase: records},
        ))
    assert results[0] == results[1]
    if reason == "auth":
        assert results[0] == {"status": "failed", "ok": False, "stop": "auth_failed"}
    else:
        assert results[0] == {
            "status": "partial", "ok": False, "stop": "budget_or_cancel", "incomplete_turn": phase,
        }


def test_budget_reason_comes_from_records_or_code_never_prose():
    events = [{"type": "error", "code": "turn_failed", "recoverable": True, "message": "Server error"}]
    budget = b3.HttpBudget(currency_cap_micros=10_000_000)
    budget.before_http("https://provider.invalid/chat/completions", kind="chat")
    with pytest.raises(b3.BudgetExhausted):
        budget.before_http("https://provider.invalid/chat/completions", kind="chat")
    assert b3.events_indicate_budget_block(events, budget.snapshot()) is True
    events[0]["message"] = "Server error: budget_currency_exhausted"
    assert b3.events_indicate_budget_block(events, {"rejected": []}) is False
    events[0]["code"] = "budget_currency_exhausted"
    assert b3.events_indicate_budget_block(events) is True


async def test_turn_records_exclude_prior_auth_and_budget_failures(tmp_path, monkeypatch):
    capture = tmp_path / "captures"
    capture.mkdir()
    budget = b3.HttpBudget(path=capture / "budget.json")
    old_id = budget.before_http("https://provider.invalid/chat/completions", kind="chat")
    budget.record_http_status(old_id, 401)
    # Fill the bounded rejection history; a new rejection evicts an old row.
    with budget._locked():
        budget.rejected = [{"at": str(i), "reason": "budget_currency_exhausted"} for i in range(64)]
        budget._persist_unlocked()

    async def send(ws, text, *, timeout):
        attempt_id = budget.before_http("https://provider.invalid/chat/completions", kind="chat")
        budget.record_http_status(attempt_id, 200)
        if text == "blocked":
            with budget._locked():
                budget.rejected.append({"at": "new", "reason": "budget_currency_exhausted"})
                budget._persist_unlocked()
        return [{"type": "error", "message": "Server error"}]

    monkeypatch.setattr(b3, "_send_turn_or_error", send)
    events, records = await b3._send_turn_with_records(None, "healthy", {"captures": capture}, timeout=1)
    assert len(records["attempts"]) == 1
    assert b3._events_indicate_auth_failure(records) is False
    assert b3.events_indicate_budget_block(events, records) is False
    events, records = await b3._send_turn_with_records(None, "blocked", {"captures": capture}, timeout=1)
    assert records["rejected"] == [{"at": "new", "reason": "budget_currency_exhausted"}]
    assert b3.events_indicate_budget_block(events, records) is True


def test_auth_uses_completion_status():
    for status in (401, 403):
        assert b3._events_indicate_auth_failure({"attempts": [{"kind": "chat", "status_code": status}]})
    assert not b3._events_indicate_auth_failure({"attempts": [{"kind": "catalog", "status_code": 401}]})
    assert not b3._events_indicate_auth_failure({"attempts": [{"kind": "chat", "status_code": 500}]})


@pytest.mark.parametrize("late", [False, True])
@pytest.mark.parametrize("status", [401, 403])
def test_verify_real_detects_early_and_late_auth_from_records(tmp_path, monkeypatch, late, status):
    paths = b3.b2.run_paths(tmp_path)
    paths["captures"].mkdir()
    b3.b2._write_json(paths["pids"], {
        "transport": "real", "backend_port": 12345, "candidate": str(paths["candidate"]),
    })
    budget = b3.HttpBudget(path=paths["captures"] / "budget.json")
    attempt_id = None

    async def send(*args, **kwargs):
        nonlocal attempt_id
        attempt_id = budget.before_http("https://provider.invalid/chat/completions", kind="extract" if late else "chat")
        if not late:
            budget.record_http_status(attempt_id, status)
        return [{"type": "error", "code": "turn_failed", "recoverable": True, "message": "Server error"}]

    def finish_pending(*args, **kwargs):
        budget.record_http_status(attempt_id, status)
        return budget.snapshot()

    wait_fact = Mock(side_effect=AssertionError("no promoted fact"))
    monkeypatch.setattr(b3, "_ws_session_b3", AsyncMock(return_value=SimpleNamespace(close=AsyncMock())))
    monkeypatch.setattr(b3, "_send_turn_or_error", send)
    monkeypatch.setattr(b3, "_wait_budget_and_jobs_idle", finish_pending)
    monkeypatch.setattr(b3.b2, "_wait_fact", wait_fact)
    monkeypatch.setattr(b3, "_wait_facts", Mock(return_value={}))
    monkeypatch.setattr(b3, "_list_conversations", Mock(return_value=[]))
    monkeypatch.setattr(b3, "_job_rows", Mock(return_value=[]))
    with pytest.raises(SystemExit) as exc:
        b3.cmd_verify_real(SimpleNamespace(run_root=str(tmp_path), session_id="owner"))
    assert exc.value.code == 2
    report = json.loads((paths["captures"] / "verify_real.json").read_text(encoding="utf-8"))
    assert report["auth_failed"] is True
    assert (report["status"], report["stop"]) == ("failed", "auth_failed")
    assert wait_fact.call_count == int(late)  # early HTTP auth failures still exit immediately


@pytest.mark.parametrize("reason", ["late_auth", "budget", "historical_failure", "background_auth"])
def test_final_turn_classifies_current_records_after_background_idle(tmp_path, monkeypatch, reason):
    paths = b3.b2.run_paths(tmp_path)
    paths["captures"].mkdir()
    b3.b2._write_json(paths["pids"], {
        "transport": "real", "backend_port": 12345, "candidate": str(paths["candidate"]),
    })
    budget = b3.HttpBudget(path=paths["captures"] / "budget.json", currency_cap_micros=10_000_000)
    if reason == "historical_failure":
        old_id = budget.before_http("https://provider.invalid/chat/completions", kind="chat")
        budget.record_http_status(old_id, 401)
        budget.record_usage({"usage": {"prompt_tokens": 0, "completion_tokens": 0}}, attempt_id=old_id)
        with budget._locked():
            budget.rejected.append({"reason": "budget_currency_exhausted"})
            budget._persist_unlocked()
    pending = None

    async def send(*args, **kwargs):
        nonlocal pending
        pending = budget.before_http("https://provider.invalid/chat/completions", kind="chat")
        if reason == "background_auth":
            budget.record_http_status(pending, 200)
            budget.record_usage({"usage": {"prompt_tokens": 10, "completion_tokens": 2}}, attempt_id=pending)
            pending = budget.before_http("https://provider.invalid/chat/completions", kind="extract")
        if reason == "budget":
            with pytest.raises(b3.BudgetExhausted):
                budget.before_http("https://provider.invalid/chat/completions", kind="chat")
            return [{"type": "error", "code": "turn_failed", "recoverable": True, "message": "Server error"}]
        return _RECORDED_OK_REPLY

    def idle(*args, **kwargs):
        if pending is not None:
            budget.record_http_status(pending, 401 if reason in {"late_auth", "background_auth"} else 200)
        return budget.snapshot()

    monkeypatch.setattr(b3, "_ws_session_b3", AsyncMock(return_value=SimpleNamespace(close=AsyncMock())))
    monkeypatch.setattr(b3, "_send_turn_or_error", send)
    monkeypatch.setattr(b3, "_wait_budget_and_jobs_idle", idle)
    monkeypatch.setattr(b3, "_open_ingest_jobs", Mock(return_value=[]))
    monkeypatch.setattr(b3, "_sqlite_fact_state", Mock(return_value={"fact": {"state": "active"}}))
    monkeypatch.setattr(b3, "_sqlite_conversation_messages", Mock(return_value=[]))
    monkeypatch.setattr(b3, "_wait_facts", Mock(return_value={}))
    monkeypatch.setattr(b3, "_http_json", Mock(return_value=(200, {})))
    args = SimpleNamespace(
        run_root=str(tmp_path), session_id="owner", conversation_id="conversation", fact_id="fact",
        output_dir=str(tmp_path / "output"),
    )
    if reason == "historical_failure":
        b3.cmd_final_turn(args)
    else:
        with pytest.raises(SystemExit) as exc:
            b3.cmd_final_turn(args)
        assert exc.value.code == 2
    report = json.loads((paths["captures"] / "supplement_final_turn.json").read_text(encoding="utf-8"))
    if reason == "historical_failure":
        assert report["status"] == "complete"
        assert report["auth_failed"] is False
    else:
        assert report["status"] == "partial"
        assert report["stop"] == ("auth_failed" if reason in {"late_auth", "background_auth"} else "budget_or_cancel")
    if reason == "background_auth":
        assert report["events"] == _RECORDED_OK_REPLY
        assert report["auth_failed"] is True
        assert [(row["kind"], row["status_code"]) for row in budget.snapshot()["attempts"]] == [
            ("chat", 200), ("extract", 401),
        ]


