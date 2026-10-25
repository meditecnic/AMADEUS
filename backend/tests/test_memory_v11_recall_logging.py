"""Dispatch 64 item 3: recall failure logs carry no fact content.

recall.py logs only the exception type name and a fixed reason for the
scoped fact load failure and the unexpected-failure fallback: no exc_info,
no str(exc). The synthetic marker stands in for fact text that an inner
exception message could carry; it must not reach any log record, neither
the message nor the formatted record (which would include a traceback).
"""
from __future__ import annotations

import logging
from uuid import uuid4

import pytest

import app.services.memory_v11.recall as recall_module
from app.services.memory_v11.recall import MemoryScope, RecallRequest, recall_stable_facts
from app.services.prompt_compiler import compile_for_session
from tests.test_memory_v11_resident_profile import (
    SUPPLEMENT_QUERY,
    WORLDLINE,
    _deepseek_provider,
    _fact_lines,
    _v11_session,
    isolated_store,  # noqa: F401  (fixture)
    seed_forty,
)

MARKER_TOKEN = "SYNTH_FACT_MARKER"
MARKER = f"{MARKER_TOKEN} 紅茶はミルク"


def _scope(session_id: str) -> MemoryScope:
    return MemoryScope(session_id=session_id, worldline=WORLDLINE, identity_mode="self")


def _assert_records_carry_no_marker(records: list[logging.LogRecord]) -> None:
    formatter = logging.Formatter()
    for record in records:
        message = record.getMessage()
        formatted = formatter.format(record)  # includes the traceback if any
        # The user message itself contains "紅茶はミルク"; the token is unique.
        for needle in (MARKER, MARKER_TOKEN):
            assert needle not in message, (record.name, message)
            assert needle not in formatted, (record.name, formatted)


def _assert_type_only_recall_warning(records: list[logging.LogRecord], expected: str) -> None:
    for record in records:
        assert record.exc_info is None, (record.name, record.getMessage())
    recall_warnings = [
        record
        for record in records
        if record.name == recall_module.logger.name and record.levelno == logging.WARNING
    ]
    assert [record.getMessage() for record in recall_warnings] == [expected]


@pytest.mark.asyncio
async def test_scoped_fact_load_failure_logs_type_name_only(isolated_store, monkeypatch, caplog):
    """_load_scoped_facts raising -> unavailable/db, warning without content."""

    async def broken(_scope):
        raise RuntimeError(MARKER)

    monkeypatch.setattr(recall_module, "_load_scoped_facts", broken)
    with caplog.at_level(logging.DEBUG):
        result = await recall_stable_facts(
            scope=_scope(f"log-db-{uuid4()}"),
            request=RecallRequest(needs=("紅茶",)),
        )
    assert result.status == "unavailable"
    assert result.diagnostics == {"reason": "db"}
    assert caplog.records
    _assert_records_carry_no_marker(caplog.records)
    _assert_type_only_recall_warning(
        caplog.records, "recall: scoped fact load failed (RuntimeError)"
    )


@pytest.mark.asyncio
async def test_unexpected_recall_failure_logs_type_name_only(isolated_store, monkeypatch, caplog):
    """_recall_body raising -> unavailable/error, warning without content."""

    async def broken(**_kwargs):
        raise RuntimeError(MARKER)

    monkeypatch.setattr(recall_module, "_recall_body", broken)
    with caplog.at_level(logging.DEBUG):
        result = await recall_stable_facts(
            scope=_scope(f"log-error-{uuid4()}"),
            request=RecallRequest(needs=("紅茶",)),
        )
    assert result.status == "unavailable"
    assert result.diagnostics == {"reason": "error"}
    assert caplog.records
    _assert_records_carry_no_marker(caplog.records)
    _assert_type_only_recall_warning(caplog.records, "recall: unexpected failure (RuntimeError)")


@pytest.mark.asyncio
async def test_compile_for_session_scoped_load_failure_logs_no_content(
    isolated_store, monkeypatch, caplog
):
    """End to end (v11, 40 facts, partial profile): supplement load fails.

    The resident loader has its own query (load_resident_fact_candidates),
    so CORE FACTS still carries 32 facts; MEMORY CANDIDATES is omitted and
    no log record from any logger carries the marker.
    """
    monkeypatch.setenv("AMADEUS_MEMORY_MODE", "v11")
    sid = f"log-e2e-{uuid4()}"
    await seed_forty(sid)
    load_calls = {"n": 0}

    async def broken(_scope):
        load_calls["n"] += 1
        raise RuntimeError(MARKER)

    monkeypatch.setattr(recall_module, "_load_scoped_facts", broken)
    session = _v11_session(sid)
    with caplog.at_level(logging.DEBUG):
        prompt = await compile_for_session(session, SUPPLEMENT_QUERY, provider=_deepseek_provider())
    assert load_calls["n"] == 1  # the supplement really went through the broken loader
    assert "MEMORY CANDIDATES" not in prompt
    assert len(_fact_lines(prompt)) == 32
    assert len(session.turn_injected_fact_ids) == 32
    assert caplog.records
    _assert_records_carry_no_marker(caplog.records)
    messages = [record.getMessage() for record in caplog.records]
    assert "recall: scoped fact load failed (RuntimeError)" in messages
    assert any("memory supplement unavailable (db)" in message for message in messages)
