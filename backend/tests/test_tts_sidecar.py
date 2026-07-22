from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.tts_sidecar import SidecarSupervisor


def test_sidecar_command_expands_the_selected_port():
    command = "python api_v2.py -a 127.0.0.1 -p {port}"
    assert SidecarSupervisor._command_args(command, 9884)[-1] == "9884"


@pytest.mark.asyncio
async def test_configured_weights_are_loaded_before_existing_endpoint_is_ready(monkeypatch, tmp_path):
    gpt_weight = tmp_path / "kurisu.ckpt"
    sovits_weight = tmp_path / "kurisu.pth"
    monkeypatch.setenv("SOVITS_GPT_WEIGHTS", str(gpt_weight))
    monkeypatch.setenv("SOVITS_SOVITS_WEIGHTS", str(sovits_weight))
    supervisor = SidecarSupervisor()
    supervisor._compatible = AsyncMock(return_value=True)

    gpt_response = MagicMock(status_code=200)
    gpt_response.json.return_value = {"message": "success"}
    sovits_response = MagicMock(status_code=200)
    sovits_response.json.return_value = {"message": "success"}
    client = AsyncMock()
    client.get = AsyncMock(side_effect=[gpt_response, sovits_response])
    context = AsyncMock()
    context.__aenter__.return_value = client
    context.__aexit__.return_value = None

    with patch("app.services.tts_sidecar.httpx.AsyncClient", return_value=context):
        state = await supervisor.ensure_available()

    assert state.status == "ready"
    assert state.url == "http://127.0.0.1:9880"
    assert client.get.await_count == 2
    assert client.get.await_args_list[0].kwargs["params"] == {
        "weights_path": str(gpt_weight)
    }
    assert client.get.await_args_list[1].kwargs["params"] == {
        "weights_path": str(sovits_weight)
    }


@pytest.mark.asyncio
async def test_http_200_weight_rejection_does_not_report_ready(monkeypatch, tmp_path):
    monkeypatch.setenv("SOVITS_GPT_WEIGHTS", str(tmp_path / "kurisu.ckpt"))
    monkeypatch.setenv("SOVITS_SOVITS_WEIGHTS", str(tmp_path / "kurisu.pth"))
    supervisor = SidecarSupervisor()
    supervisor._compatible = AsyncMock(return_value=True)

    rejected = MagicMock(status_code=200)
    rejected.json.return_value = {"message": "weight loading failed"}
    client = AsyncMock()
    client.get = AsyncMock(return_value=rejected)
    context = AsyncMock()
    context.__aenter__.return_value = client
    context.__aexit__.return_value = None

    with patch("app.services.tts_sidecar.httpx.AsyncClient", return_value=context):
        state = await supervisor.ensure_available()

    assert state.status == "degraded"
    assert state.error == "configured GPT-SoVITS weights failed to load"
    assert client.get.await_count == 1


@pytest.mark.asyncio
async def test_partial_weight_configuration_is_degraded(monkeypatch, tmp_path):
    monkeypatch.setenv("SOVITS_GPT_WEIGHTS", str(tmp_path / "kurisu.ckpt"))
    monkeypatch.delenv("SOVITS_SOVITS_WEIGHTS", raising=False)
    supervisor = SidecarSupervisor()
    supervisor._compatible = AsyncMock(return_value=True)

    state = await supervisor.ensure_available()

    assert state.status == "degraded"
    assert state.error == "configured GPT-SoVITS weights failed to load"


@pytest.mark.asyncio
async def test_health_reprobes_a_sidecar_that_died_after_ready():
    supervisor = SidecarSupervisor()
    supervisor.state.status = "ready"
    supervisor.state.url = "http://127.0.0.1:9880"
    supervisor._weights_ready_url = supervisor.state.url
    supervisor._compatible = AsyncMock(return_value=False)

    async def recover():
        supervisor.state.status = "degraded"
        supervisor.state.url = None
        supervisor.state.error = "restart failed"
        return supervisor.state

    supervisor.ensure_available = AsyncMock(side_effect=recover)

    health = await supervisor.health()

    assert supervisor.ensure_available.await_count == 1
    assert health["ok"] is False
    assert health["error"] == "restart failed"
    assert supervisor._weights_ready_url is None
