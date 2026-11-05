"""Opt-in, ephemeral pi RPC subprocess; no executable is shipped by Amadeus."""

from __future__ import annotations

import asyncio
from collections import deque
import json
import logging
import os
from pathlib import Path
import tempfile
from time import monotonic

from app import config
from app.db import get_data_root

logger = logging.getLogger(__name__)
MAX_RESULT_CHARS = 6000
MAX_RECORD_BYTES = 4 * 1024 * 1024


class PiAgentError(RuntimeError):
    pass


class PiSupervisor:
    """One process per delegation; cancellation owns and reaps that process."""

    def __init__(self):
        self.process: asyncio.subprocess.Process | None = None
        self._stderr_task: asyncio.Task | None = None
        self._key = ""
        self._events: deque[dict] = deque()

    async def _send(self, record: dict) -> None:
        assert self.process is not None and self.process.stdin is not None
        self.process.stdin.write(json.dumps(record, ensure_ascii=False).encode("utf-8") + b"\n")
        await self.process.stdin.drain()

    async def _read(self) -> dict:
        assert self.process is not None and self.process.stdout is not None
        # Binary readline splits only on LF, including when JSON contains U+2028.
        line = await self.process.stdout.readline()
        if not line:
            raise PiAgentError("pi process exited before completing the task")
        if not line.endswith(b"\n"):
            raise PiAgentError("pi returned an incomplete JSONL record")
        record = json.loads(line)
        if not isinstance(record, dict):
            raise PiAgentError("pi returned an invalid protocol record")
        return record

    async def _response(self, request_id: str) -> dict:
        while True:
            record = await self._read()
            if record.get("type") == "response" and record.get("id") == request_id:
                if not record.get("success"):
                    # Provider errors and stderr can contain credentials. Never relay them.
                    raise PiAgentError("pi rejected an RPC command")
                return record.get("data") or {}
            if record.get("type") != "response":
                self._events.append(record)

    async def _discard(self, stream: asyncio.StreamReader) -> None:
        while await stream.read(8192):
            pass

    async def close(self, *, abort: bool = False) -> None:
        process = self.process
        if process is None:
            return
        drain = asyncio.create_task(self._discard(process.stdout)) if process.stdout is not None else None
        try:
            if process.returncode is None:
                if abort:
                    try:
                        await asyncio.wait_for(self._send({"type": "abort", "id": "abort"}), 0.5)
                    except (OSError, asyncio.TimeoutError):
                        pass
                if process.stdin is not None:
                    process.stdin.close()
                try:
                    await asyncio.wait_for(process.wait(), 2.0)
                except asyncio.TimeoutError:
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
                    await process.wait()
        finally:
            if self._stderr_task is not None:
                self._stderr_task.cancel()
                await asyncio.gather(self._stderr_task, return_exceptions=True)
            if drain is not None:
                drain.cancel()
                await asyncio.gather(drain, return_exceptions=True)

    async def run(
        self, task: str, folder: str, *, base_url: str, model: str,
        api_key: str | None, timeout: float = 120.0,
    ) -> str:
        if not config.PI_AGENT_ENABLED or not config.PI_AGENT_EXECUTABLE:
            raise PiAgentError("pi delegation is disabled or its executable is not configured")
        cwd = Path(folder).resolve(strict=True)
        if not cwd.is_dir():
            raise PiAgentError("delegation requires a folder")
        root = get_data_root() / "pi-agent"
        root.mkdir(parents=True, exist_ok=True)
        started = monotonic()
        self._key = api_key or ""
        completed = False
        try:
            with tempfile.TemporaryDirectory(prefix="run-", dir=root) as temporary:
                run_dir = Path(temporary)
                agent_dir = run_dir / "agent"
                agent_dir.mkdir()
                (run_dir / "tmp").mkdir()
                # This file contains only an environment reference, never the credential.
                (agent_dir / "models.json").write_text(json.dumps({"providers": {"amadeus": {
                    "baseUrl": base_url, "api": "openai-completions",
                    "apiKey": "$AMADEUS_PI_API_KEY", "authHeader": bool(api_key),
                    "models": [{"id": model}],
                }}}), encoding="utf-8")
                env = dict(os.environ)
                env.update({
                    "PI_CODING_AGENT_DIR": str(agent_dir), "PI_OFFLINE": "1",
                    "PI_SKIP_VERSION_CHECK": "1", "PI_TELEMETRY": "0",
                    "AMADEUS_PI_API_KEY": api_key or "amadeus-local-no-key",
                    "TMPDIR": str(run_dir / "tmp"), "TEMP": str(run_dir / "tmp"),
                    "TMP": str(run_dir / "tmp"),
                })
                args = [
                    config.PI_AGENT_EXECUTABLE, "--mode", "rpc", "--offline",
                    "--provider", "amadeus", "--model", model, "--no-session",
                    "--session-dir", str(run_dir / "sessions"),
                    "--tools", "read,ls,grep", "--no-extensions", "--no-skills",
                    "--no-mcp", "--no-context-files", "--no-prompt-templates",
                    "--no-themes", "--no-approve", "--extension",
                    str(Path(__file__).with_name("pi_root_guard.ts")),
                ]
                try:
                    async with asyncio.timeout(timeout):
                        self.process = await asyncio.create_subprocess_exec(
                            *args, cwd=str(cwd), env=env, stdin=asyncio.subprocess.PIPE,
                            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                            limit=MAX_RECORD_BYTES, creationflags=0x08000000 if os.name == "nt" else 0,
                        )
                        self._stderr_task = asyncio.create_task(self._discard(self.process.stderr))
                        await self._send({"type": "get_state", "id": "startup"})
                        await self._response("startup")
                        logger.info("pi cold_start_seconds=%.3f", monotonic() - started)
                        await self._send({"type": "prompt", "id": "task", "message": (
                            "Complete this read-only task in the delegated folder.\n" + task
                        )})
                        accepted = await self._response("task")
                        if accepted.get("disposition") == "handled":
                            raise PiAgentError("pi did not start an agent task")
                        text = ""
                        while True:
                            event = self._events.popleft() if self._events else await self._read()
                            kind = event.get("type")
                            if kind == "tool_execution_start":
                                path = str((event.get("args") or {}).get("path", "."))[:512]
                                if self._key:
                                    path = path.replace(self._key, "[redacted]")
                                logger.info("pi tool=%s path=%s", event.get("toolName"), json.dumps(path))
                            elif kind == "message_end":
                                message = event.get("message") or {}
                                if message.get("role") == "assistant":
                                    if message.get("stopReason") in {"error", "aborted"}:
                                        raise PiAgentError("pi provider failed or the task was aborted")
                                    text = "".join(
                                        part.get("text", "") for part in message.get("content", [])
                                        if part.get("type") == "text"
                                    )
                            elif kind == "agent_settled":
                                if event.get("aborted"):
                                    raise PiAgentError("pi task was aborted")
                                break
                        await self._send({"type": "get_session_stats", "id": "stats"})
                        stats = await self._response("stats")
                        tokens = stats.get("tokens") or {}
                        logger.info("pi elapsed_seconds=%.3f input_tokens=%d output_tokens=%d",
                                    monotonic() - started, int(tokens.get("input", 0)), int(tokens.get("output", 0)))
                        completed = True
                        if self._key:
                            text = text.replace(self._key, "[redacted]")
                        return text[:MAX_RESULT_CHARS] or "pi completed without a text result."
                finally:
                    # Reap before TemporaryDirectory removes files (also on Windows).
                    await asyncio.shield(self.close(abort=not completed))
        except asyncio.TimeoutError:
            raise PiAgentError("pi task timed out") from None
        except (OSError, ValueError, TypeError, KeyError, AssertionError):
            raise PiAgentError("pi could not complete the task") from None
        finally:
            self._key = ""
