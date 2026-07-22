"""Lifecycle-only integration tests for diagnostics health exposure."""

import asyncio
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
import pytest

from app.services.diagnostics import DiagnosticRuntime


class _BrokenStore:
    def initialize(self):
        raise sqlite3.OperationalError("cannot open C:\\private\\diagnostics.sqlite3")


def test_runtime_failure_is_redacted_and_never_raises():
    runtime = DiagnosticRuntime(store_factory=_BrokenStore)
    assert runtime.start() is False
    assert runtime.public_health() == {
        "state": "degraded",
        "observing": False,
        "mapping_fallbacks": 0,
        "cumulative_failures": 1,
        "last_failure_time": runtime.public_health()["last_failure_time"],
        "last_error_category": "open_failed",
    }
    assert "private" not in str(runtime.public_health())


def test_broken_diagnostics_store_does_not_block_application_startup(
    tmp_path,
    monkeypatch,
):
    from app.services.diagnostics import diagnostic_runtime
    from app.main import app

    monkeypatch.setenv("AMADEUS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(diagnostic_runtime, "_store_factory", _BrokenStore)
    with patch(
        "app.main.sidecar_supervisor.ensure_available",
        new=AsyncMock(return_value=SimpleNamespace(url=None)),
    ), patch(
        "app.main.sidecar_supervisor.close",
        new=AsyncMock(),
    ):
        with TestClient(app) as client:
            response = client.get("/health/dependencies")
    assert response.status_code == 200
    body = response.json()
    assert set(body["dependencies"]["diagnostics"]) == {
        "state",
        "observing",
        "mapping_fallbacks",
        "cumulative_failures",
        "last_failure_time",
        "last_error_category",
    }
    assert body["dependencies"]["diagnostics"]["state"] == "degraded"
    assert body["status"] in {"ready", "degraded"}
    assert "path" not in str(body["dependencies"]["diagnostics"]).lower()


@pytest.mark.asyncio
async def test_diagnostics_sweep_loop_runs_immediately_then_waits_one_day(monkeypatch):
    from app.main import _diagnostics_sweep_loop
    from app.services.diagnostics import diagnostic_runtime

    calls = []
    monkeypatch.setattr(diagnostic_runtime, "sweep_best_effort", lambda: calls.append("sweep"))

    async def stop_after_first_interval(seconds):
        assert seconds == 24 * 60 * 60
        raise asyncio.CancelledError

    monkeypatch.setattr(asyncio, "sleep", stop_after_first_interval)
    with pytest.raises(asyncio.CancelledError):
        await _diagnostics_sweep_loop()
    assert calls == ["sweep"]
