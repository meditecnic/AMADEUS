"""Model limits travel with each turn and bound all chat request paths."""

import pytest

from test_chat_ws_background_tasks import isolated_store  # noqa: F401

from app.services.model_catalog import ModelCatalog, model_catalog
from app.services.provider_registry import (
    ProviderCapabilities,
    ProviderDefinition,
    ProviderRegistry,
    ProviderTask,
)


def registry_with_model(provider_id="custom", model_id="local-8k", limits=None):
    catalog = ModelCatalog.from_mapping(model_catalog.manifest)
    if limits is not None:
        catalog.reconcile(provider_id, [model_id], [{"id": model_id, **limits}])
    registry = ProviderRegistry(model_catalog=catalog)
    registry.register(ProviderDefinition(
        id=provider_id, display_name="Budget test", default_model=model_id,
        capabilities=ProviderCapabilities(tasks=frozenset({ProviderTask.CHAT})),
    ), object())
    return registry, catalog


@pytest.mark.parametrize("kind", ["static", "discovered", "unknown"])
def test_snapshot_carries_catalog_limits(kind):
    provider, model = ("deepseek", "deepseek-flash") if kind == "static" else ("custom", "local-8k")
    limits = {"context_window": 8192, "max_output_tokens": 1024} if kind == "discovered" else None
    registry, catalog = registry_with_model(provider, model, limits)
    snapshot = registry.snapshot(provider, model)
    expected = catalog.public_model(provider, model)
    assert snapshot.context_window == expected["context_window"]
    assert snapshot.max_output_tokens == expected["max_output_tokens"]
    if kind == "discovered":
        catalog.reconcile(provider, [model], [{"id": model, "context_window": 16384}])
        assert snapshot.context_window == 8192  # Frozen for this turn.
    elif kind == "unknown":
        assert snapshot.context_window is None
        assert snapshot.max_output_tokens is None


def test_snapshot_without_catalog_has_no_limits():
    registry, _ = registry_with_model()
    registry._model_catalog = None
    snapshot = registry.snapshot("custom")
    assert snapshot.context_window is None
    assert snapshot.max_output_tokens is None


@pytest.mark.parametrize(("window", "catalog_output", "request_output", "expected", "source"), [
    (None, 1024, 1024, 32000, "config"),
    (0, None, None, 32000, "config"),
    (-1, None, None, 32000, "config"),
    ("8192", None, None, 32000, "config"),
    (True, None, None, 32000, "config"),
    (8192.0, None, None, 32000, "config"),
    (8192, 4096, 1024, 6144, "context_window"),
    (8192, 1024, None, 6144, "context_window"),
    (8192, None, None, 5120, "context_window"),
    (200000, None, None, 32000, "context_window"),
    (1000, None, None, 2048, "context_window"),
])
def test_effective_budget(window, catalog_output, request_output, expected, source):
    from dataclasses import replace
    from app.services.turn_tool_orchestrator import effective_request_budget

    registry, _ = registry_with_model()
    snapshot = replace(registry.snapshot("custom"), context_window=window, max_output_tokens=catalog_output)
    assert effective_request_budget(snapshot, request_output) == (expected, source)


@pytest.mark.parametrize("window", [None, 8192], ids=["unknown", "8k"])
@pytest.mark.parametrize(("provider", "kind"), [
    ("deepseek", "first"), ("deepseek", "forced"), ("deepseek", "web_second"),
    ("deepseek", "recall"), ("deepseek", "refresh"), ("custom", "first"),
])
async def test_model_budget_all_requests(isolated_store, monkeypatch, caplog, window, provider, kind):
    import asyncio
    from copy import deepcopy
    from datetime import datetime
    import json
    from unittest.mock import AsyncMock

    import httpx

    from app.routers import chat_ws
    from app.services import turn_tool_orchestrator as orchestrator
    from app.services.provider_adapters import OpenAICompatibleAdapter
    from app.services.prompt_compiler import PromptCompiler
    from test_chat_ws_background_tasks import _new_session, _suppress_auto_title
    from test_request_budget_182 import CURRENT, SYSTEM, WEB, history, memory

    budget = 32000 if window is None else 6144
    limits = None if window is None else {"context_window": window, "max_output_tokens": 1024}
    model = "deepseek-flash" if provider == "deepseek" else "local-8k"
    registry, catalog = registry_with_model(provider, model, limits)
    if window is None:
        public_model = catalog.public_model
        monkeypatch.setattr(catalog, "public_model", lambda *args: {
            **public_model(*args), "context_window": None, "max_output_tokens": None,
        })
    session = await _new_session()
    session.provider_id = provider
    session.model = model
    session.history = history()
    original = deepcopy(session.history)
    captured, prepared = [], []
    recalled = memory(300)

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 11, 4, 12, 0, tzinfo=tz)

    def frame(delta):
        return ("data: " + json.dumps({"choices": [{"delta": delta}]}, ensure_ascii=False) + "\n\n").encode("utf-8")

    async def handler(request):
        captured.append(json.loads(request.content))
        if len(captured) == 1 and kind in {"web_second", "recall", "refresh"}:
            if kind == "web_second":
                session.turn_execution.web_results.extend(WEB)
            delta = {"tool_calls": [{"index": 0, "id": "budget202", "type": "function",
                                     "function": {"name": "web_search", "arguments": "{}"}}]}
        elif len(captured) == 2 and kind == "refresh":
            delta = {"content": "私はAIアシスタントです。"}
        else:
            delta = {"content": "条件を確認するわ。"}
        return httpx.Response(200, content=frame(delta) + b"data: [DONE]\n\n")

    adapter = OpenAICompatibleAdapter(
        provider_id=provider, base_url="https://budget-test.invalid/v1", default_model=model,
        message_builder=chat_ws.deepseek_service.build_persona_messages,
        credential_required=False, transport=httpx.MockTransport(handler),
    )
    definition = next(row for row in chat_ws.provider_registry.list_definitions() if row.id == provider)
    registry.upsert(definition, adapter)
    if provider == "deepseek":
        monkeypatch.setattr(chat_ws, "deepseek_service", adapter)
    monkeypatch.setattr(chat_ws, "provider_registry", registry)
    monkeypatch.setattr(chat_ws, "model_catalog", catalog)
    monkeypatch.setattr("datetime.datetime", FixedDatetime)
    monkeypatch.setattr(chat_ws, "compile_for_session", AsyncMock(return_value=SYSTEM))
    monkeypatch.setattr(chat_ws, "should_force_search", lambda text: kind == "forced")
    monkeypatch.setattr(chat_ws, "execute_tool_call", AsyncMock(return_value=WEB[0][1]))
    monkeypatch.setattr(chat_ws.config, "HISTORY_COMPRESS_THRESHOLD", 1000)
    monkeypatch.setattr(chat_ws, "translate_with_provider", AsyncMock(return_value="翻译"))
    monkeypatch.setattr("app.services.memory_v11.jobs.enqueue_memory_job", AsyncMock())
    monkeypatch.setattr(orchestrator, "apply_v11_tool_batch", AsyncMock(return_value=orchestrator.ToolBatchOutcome(
        status="ok", recall_result=recalled if kind in {"recall", "refresh"} else None,
    )))
    refresh = AsyncMock(return_value=recalled)
    monkeypatch.setattr(orchestrator, "refresh_candidates_for_send", refresh)
    prepare = orchestrator.prepare_second_round_payload

    def prepare_capture(*args, **kwargs):
        payload = prepare(*args, **kwargs)
        prepared.append((payload, kwargs))
        return payload

    monkeypatch.setattr(orchestrator, "prepare_second_round_payload", prepare_capture)
    _suppress_auto_title(monkeypatch)
    with caplog.at_level("INFO", logger=chat_ws.__name__):
        await chat_ws.processor_loop(session, CURRENT, asyncio.Queue(), 0, 0)
    if session.background_tasks:
        await asyncio.gather(*session.background_tasks)

    assert len(captured) == (3 if kind == "refresh" else 2 if kind in {"web_second", "recall"} else 1)
    assert f"budget={budget}" in caplog.text
    assert f"source={'config' if window is None else 'context_window'}" in caplog.text
    assert session.history[:-2] == original[:-1]
    for request in captured:
        messages = request["messages"]
        assert sum(PromptCompiler._estimate_tokens(row["content"]) for row in messages) <= budget
        assert SYSTEM in messages[0]["content"]
        assert any(CURRENT in row["content"] for row in messages if row["role"] == "user")
        kept = [row for row in messages if row in original[:-1]]
        assert kept == original[len(original) - 1 - len(kept):-1]
        assert "max_tokens" not in request  # Metadata reserves output without changing output controls.
    if kind in {"forced", "web_second"}:
        assert any(WEB[0][1] in row["content"] for row in captured[-1]["messages"])
    if kind in {"recall", "refresh"}:
        assert len(prepared) == (3 if kind == "refresh" else 2)
        for payload, kwargs in prepared:
            assert payload.result.records == recalled.records
            assert orchestrator.request_envelope_tokens(
                payload.history, kwargs["system_prompt"], "", kwargs["message_builder"],
            ) <= budget
        for request in captured[1:]:
            assert any(recalled.block in row["content"] for row in request["messages"])
        assert refresh.await_count == (2 if kind == "refresh" else 1)
