from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from app import config
from app.services import pi_agent, pi_delegation
from app.services.pi_agent import PiAgentError, PiSupervisor
from app.services.provider_runtime import provider_registry


FAKE_RPC = '''
import json, os, sys
from pathlib import Path

def emit(record):
    data = json.dumps(record, ensure_ascii=False).encode("utf-8") + b"\\n"
    sys.stdout.buffer.write(data[:11])
    sys.stdout.buffer.flush()
    sys.stdout.buffer.write(data[11:])
    sys.stdout.buffer.flush()

def events():
    emit({"type":"tool_execution_start", "toolName":"read", "args":{"path":os.environ["AMADEUS_PI_API_KEY"]}})
    emit({"type":"message_end", "message":{"role":"assistant", "content":[{"type":"text", "text":"hello\\u2028world\\u2029end"}]}})
    emit({"type":"agent_settled", "aborted":False})

mode = os.environ["PI_TEST_MODE"]
for line in sys.stdin.buffer:
    r = json.loads(line)
    kind = r["type"]
    if kind == "abort":
        Path(os.environ["PI_TEST_ABORT_FILE"]).write_text("aborted", encoding="utf-8")
    elif kind == "get_state":
        emit({"type":"response", "id":r["id"], "success":True, "data":{}})
    elif kind == "prompt":
        if mode == "crash":
            print(os.environ["AMADEUS_PI_API_KEY"], file=sys.stderr)
            sys.exit(7)
        if mode == "error":
            print(os.environ["AMADEUS_PI_API_KEY"], file=sys.stderr)
            emit({"type":"response", "id":r["id"], "success":False, "error":os.environ["AMADEUS_PI_API_KEY"]})
            continue
        if mode == "early":
            events()
        emit({"type":"response", "id":r["id"], "success":True, "data":{"disposition":"started"}})
        if mode in ("retry", "message_error"):
            emit({"type":"message_end", "message":{"role":"assistant", "stopReason":"error", "content":[]}})
            emit({"type":"agent_end", "willRetry":mode == "retry"})
        if mode == "message_error":
            emit({"type":"agent_settled", "aborted":False})
            continue
        if mode == "timeout":
            continue
        if mode != "early":
            events()
    elif kind == "get_session_stats":
        emit({"type":"response", "id":r["id"], "success":True, "data":{"tokens":{"input":100,"output":20}}})
'''


@pytest.fixture
def fake_pi(tmp_path, monkeypatch):
    script = tmp_path / "fake_pi.py"
    script.write_text(FAKE_RPC, encoding="utf-8")
    abort_file = tmp_path / "abort.txt"
    state = SimpleNamespace(mode="ok", launches=[], abort_file=abort_file, start_delay=0)
    original = asyncio.create_subprocess_exec

    async def launch(*args, **kwargs):
        env = kwargs["env"]
        agent = Path(env["PI_CODING_AGENT_DIR"])
        state.launches.append({"args": args, "kwargs": kwargs, "run_dir": agent.parent,
                               "models": (agent / "models.json").read_text(encoding="utf-8")})
        env.update(PI_TEST_MODE=state.mode, PI_TEST_ABORT_FILE=str(abort_file))
        process = await original(sys.executable, "-u", str(script), **kwargs)
        await asyncio.sleep(state.start_delay)
        return process

    monkeypatch.setattr(config, "PI_AGENT_ENABLED", True)
    monkeypatch.setattr(config, "PI_AGENT_EXECUTABLE", "synthetic-pi")
    monkeypatch.setattr(pi_agent.asyncio, "create_subprocess_exec", launch)
    return state


async def run(supervisor, tmp_path, **kwargs):
    return await supervisor.run("read the notes", str(tmp_path), base_url="http://127.0.0.1:1/v1",
                                model="synthetic-model", api_key="synthetic-private-value", **kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["ok", "early", "retry"])
async def test_lf_framing_isolation_usage_and_secret_redaction(fake_pi, tmp_path, caplog, mode):
    fake_pi.mode = mode
    caplog.set_level("INFO", logger=pi_agent.__name__)
    supervisor = PiSupervisor()
    assert await run(supervisor, tmp_path) == "hello\u2028world\u2029end"
    assert supervisor.process.returncode == 0
    launch = fake_pi.launches[0]
    args = launch["args"]
    assert args[args.index("--tools") + 1] == "read,ls,grep"
    for flag in ("--no-extensions", "--no-skills", "--no-mcp", "--no-context-files",
                 "--no-prompt-templates", "--no-themes", "--no-approve", "--no-session", "--offline"):
        assert flag in args
    assert "pi_root_guard.ts" in args[-1]
    assert launch["kwargs"]["cwd"] == str(tmp_path)
    assert launch["kwargs"]["env"]["PI_OFFLINE"] == "1"
    assert launch["kwargs"]["env"]["PI_TELEMETRY"] == "0"
    assert not launch["run_dir"].exists()
    assert "synthetic-private-value" not in str(args) + launch["models"] + caplog.text
    assert "input_tokens=100 output_tokens=20" in caplog.text
    assert "pi tool_execution_start" in caplog.text
    assert str(tmp_path) not in caplog.text


@pytest.mark.asyncio
async def test_timeout_aborts_reaps_and_removes_run_directory(fake_pi, tmp_path):
    fake_pi.mode = "timeout"
    supervisor = PiSupervisor()
    with pytest.raises(PiAgentError, match="timed out"):
        await run(supervisor, tmp_path, timeout=0.3)
    assert supervisor.process.returncode == 0
    assert fake_pi.abort_file.read_text(encoding="utf-8") == "aborted"
    assert not fake_pi.launches[0]["run_dir"].exists()


@pytest.mark.asyncio
async def test_cancellation_aborts_reaps_and_propagates(fake_pi, tmp_path):
    fake_pi.mode = "timeout"
    supervisor = PiSupervisor()
    task = asyncio.create_task(run(supervisor, tmp_path))
    while supervisor.process is None:
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert supervisor.process.returncode == 0
    assert fake_pi.abort_file.read_text(encoding="utf-8") == "aborted"
    assert not fake_pi.launches[0]["run_dir"].exists()


@pytest.mark.asyncio
async def test_cancel_during_startup_still_reaps_child(fake_pi, tmp_path):
    fake_pi.mode = "timeout"
    fake_pi.start_delay = 0.1
    supervisor = PiSupervisor()
    task = asyncio.create_task(run(supervisor, tmp_path))
    while not fake_pi.launches:
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert supervisor.process.returncode == 0
    assert not fake_pi.launches[0]["run_dir"].exists()


@pytest.mark.asyncio
async def test_result_is_bounded(fake_pi, tmp_path, monkeypatch):
    monkeypatch.setattr(pi_agent, "MAX_RESULT_CHARS", 5)
    assert await run(PiSupervisor(), tmp_path) == "hello"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["crash", "error", "message_error"])
async def test_exit_and_rpc_error_do_not_leak_credentials(fake_pi, tmp_path, caplog, mode):
    fake_pi.mode = mode
    supervisor = PiSupervisor()
    with pytest.raises(PiAgentError) as error:
        await run(supervisor, tmp_path)
    assert supervisor.process.returncode is not None
    assert "synthetic-private-value" not in str(error.value) + caplog.text
    assert not fake_pi.launches[0]["run_dir"].exists()


def test_disabled_is_default_and_tools_remain_identical(monkeypatch):
    from app.agent_tools import MEMORY_WEB_TOOLS
    from app.services.turn_tool_orchestrator import plan_first_round_tools

    # Literal expected wire schema comes from the pre-spike tool surface.
    monkeypatch.setattr(config, "PI_AGENT_ENABLED", False)
    provider = provider_registry.snapshot("deepseek")
    plan = plan_first_round_tools(force_search=False, provider_id="deepseek", adapter_kind="DeepSeekService")
    before = json.dumps(MEMORY_WEB_TOOLS, ensure_ascii=False).encode("utf-8")
    actual = pi_delegation.offer_tools(plan.tools, provider)
    assert actual is plan.tools
    assert json.dumps(actual, ensure_ascii=False).encode("utf-8") == before
    assert b"delegate_task" not in before


@pytest.mark.asyncio
async def test_disabled_does_not_spawn_even_for_a_hallucinated_call(monkeypatch):
    monkeypatch.setattr(config, "PI_AGENT_ENABLED", False)
    assert await pi_delegation.execute_delegations(
        [{"function": {"name": "delegate_task", "arguments": "{}"}}],
        provider_registry.snapshot("deepseek"), "synthetic-private-value",
    ) == []


def test_offer_only_for_compatible_tool_capable_providers(monkeypatch):
    monkeypatch.setattr(config, "PI_AGENT_ENABLED", True)
    compatible = provider_registry.snapshot("custom")
    assert pi_delegation.offer_tools(None, compatible)[0]["function"]["name"] == "delegate_task"
    assert pi_delegation.offer_tools(None, provider_registry.snapshot("gemini")) is None
    incapable = SimpleNamespace(capabilities=SimpleNamespace(tools=False), adapter=compatible.adapter)
    assert pi_delegation.offer_tools(None, incapable) is None


@pytest.mark.asyncio
async def test_bad_or_cancelled_delegations_do_not_start_pi(fake_pi):
    provider = provider_registry.snapshot("custom")
    for args in ("[]", "{}", '{"task":1,"folder":"/tmp"}', "not-json"):
        result = await pi_delegation.execute_delegations(
            [{"function": {"name": "delegate_task", "arguments": args}}], provider, None,
        )
        assert result == [("delegate_task", "Delegation unavailable.")]
    assert fake_pi.launches == []
