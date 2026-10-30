import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.tts_sidecar import SidecarState, SidecarSupervisor


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


@pytest.fixture
def managed_sidecar(monkeypatch):
    monkeypatch.setenv("SOVITS_SIDECAR_COMMAND", "python fake_sidecar.py --port {port}")
    supervisor = SidecarSupervisor()
    supervisor._compatible = AsyncMock(return_value=False)
    supervisor._occupied = AsyncMock(return_value=False)
    supervisor._load_configured_weights = AsyncMock(return_value=True)
    process = MagicMock(returncode=None)
    process.wait = AsyncMock()

    def terminate():
        process.returncode = -15

    process.terminate.side_effect = terminate
    spawn = AsyncMock(return_value=process)
    monkeypatch.setattr("app.services.tts_sidecar.asyncio.create_subprocess_exec", spawn)
    monkeypatch.setattr("app.services.tts_sidecar.asyncio.sleep", AsyncMock())
    clock = MagicMock(return_value=100.0)
    monkeypatch.setattr("app.services.tts_sidecar.monotonic", clock)
    return supervisor, process, spawn, clock


@pytest.mark.asyncio
async def test_concurrent_health_starts_only_one_sidecar(monkeypatch):
    # Yield during fake creation so all five requests overlap at the launch.
    real_sleep = asyncio.sleep
    with monkeypatch.context() as mp:
        mp.setenv("SOVITS_SIDECAR_COMMAND", "python fake_sidecar.py --port {port}")
        supervisor = SidecarSupervisor()
        running = False
        process = MagicMock(returncode=None)

        async def launch(*args, **kwargs):
            nonlocal running
            await real_sleep(0)
            running = True
            return process

        supervisor._compatible = AsyncMock(side_effect=lambda url: running)
        supervisor._occupied = AsyncMock(return_value=False)
        supervisor._load_configured_weights = AsyncMock(return_value=True)
        spawn = AsyncMock(side_effect=launch)
        mp.setattr("app.services.tts_sidecar.asyncio.create_subprocess_exec", spawn)
        mp.setattr("app.services.tts_sidecar.asyncio.sleep", AsyncMock())

        results = await asyncio.gather(*(supervisor.health() for _ in range(5)))

    spawn.assert_awaited_once()
    assert spawn.await_args.args == ("python", "fake_sidecar.py", "--port", "9880")
    assert supervisor._compatible.await_count == 2
    supervisor._load_configured_weights.assert_awaited_once_with("http://127.0.0.1:9880")
    assert all(result == {
        "ok": True, "status": "ready", "url": "http://127.0.0.1:9880",
        "managed": True, "error": None,
    } for result in results)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["exit", "timeout"])
async def test_failed_launch_cools_down_but_probes_external_sidecars(managed_sidecar, failure):
    supervisor, process, spawn, clock = managed_sidecar
    if failure == "exit":
        process.returncode = 1

    failed = await supervisor.health()

    assert failed == {
        "ok": False, "status": "degraded", "url": None, "managed": False,
        "error": "no compatible GPT-SoVITS endpoint on ports 9880-9890",
    }
    spawn.assert_awaited_once()
    if failure == "timeout":
        process.terminate.assert_called_once()
        process.wait.assert_awaited_once()
    else:
        process.terminate.assert_not_called()

    clock.return_value += supervisor.STARTUP_COOLDOWN_SECONDS - 1
    supervisor._compatible.reset_mock()
    results = await asyncio.gather(*(supervisor.health() for _ in range(5)))

    assert all(result == failed for result in results)
    spawn.assert_awaited_once()
    assert supervisor._compatible.await_count == 5 * 11

    # Discovery must reach later ports even while managed launches cool down.
    supervisor._compatible.side_effect = lambda url: url.endswith(":9884")
    recovered = await supervisor.health()

    assert recovered == {
        "ok": True, "status": "ready", "url": "http://127.0.0.1:9884",
        "managed": False, "error": None,
    }
    spawn.assert_awaited_once()
    supervisor._load_configured_weights.assert_awaited_once_with("http://127.0.0.1:9884")


@pytest.mark.asyncio
async def test_failed_launch_retries_after_cooldown(managed_sidecar, monkeypatch):
    supervisor, process, spawn, clock = managed_sidecar
    monkeypatch.setattr(supervisor, "STARTUP_COOLDOWN_SECONDS", 2.0)
    process.returncode = 1
    await supervisor.ensure_available()
    spawn.assert_awaited_once()

    clock.return_value += 1.99
    await supervisor.ensure_available()
    spawn.assert_awaited_once()

    clock.return_value = 102.0
    await supervisor.ensure_available()
    assert spawn.await_count == 2


@pytest.mark.asyncio
async def test_ready_ensure_available_does_not_reprobe_or_restart(managed_sidecar):
    supervisor, _, spawn, _ = managed_sidecar
    ready = SidecarState("ready", "http://127.0.0.1:9880", True, None)
    supervisor.state = ready

    results = await asyncio.gather(*(supervisor.ensure_available() for _ in range(5)))

    assert all(state is ready for state in results)
    supervisor._compatible.assert_not_awaited()
    supervisor._occupied.assert_not_awaited()
    supervisor._load_configured_weights.assert_not_awaited()
    spawn.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_command_keeps_probing_without_launching(managed_sidecar, monkeypatch):
    supervisor, _, spawn, _ = managed_sidecar
    monkeypatch.delenv("SOVITS_SIDECAR_COMMAND")
    supervisor._last_start_failure = 100.0

    assert (await supervisor.health())["status"] == "degraded"
    supervisor._compatible.assert_awaited_once_with("http://127.0.0.1:9880")
    supervisor._compatible.return_value = True
    assert (await supervisor.health())["ok"] is True
    spawn.assert_not_awaited()
