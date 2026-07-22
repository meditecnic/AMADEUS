from __future__ import annotations

import asyncio
import os
import shlex
from dataclasses import dataclass

import httpx


@dataclass(slots=True)
class SidecarState:
    status: str = "not_probed"
    url: str | None = None
    managed: bool = False
    error: str | None = None


class SidecarSupervisor:
    def __init__(self):
        self.state = SidecarState()
        self.process: asyncio.subprocess.Process | None = None
        self._weights_ready_url: str | None = None

    @staticmethod
    def _command_args(command: str, port: int) -> list[str]:
        return [part.replace("{port}", str(port)) for part in shlex.split(command)]

    @staticmethod
    def _weight_load_succeeded(response: httpx.Response) -> bool:
        if response.status_code != 200:
            return False
        try:
            return response.json().get("message") == "success"
        except (ValueError, AttributeError, TypeError):
            return False

    async def _load_configured_weights(self, url: str) -> bool:
        gpt_weights = os.getenv("SOVITS_GPT_WEIGHTS", "").strip()
        sovits_weights = os.getenv("SOVITS_SOVITS_WEIGHTS", "").strip()
        if not gpt_weights and not sovits_weights:
            return True
        if not gpt_weights or not sovits_weights:
            return False
        if self._weights_ready_url == url:
            return True
        try:
            async with httpx.AsyncClient(timeout=180.0, trust_env=False) as client:
                gpt_response = await client.get(
                    f"{url}/set_gpt_weights",
                    params={"weights_path": gpt_weights},
                )
                if not self._weight_load_succeeded(gpt_response):
                    return False
                sovits_response = await client.get(
                    f"{url}/set_sovits_weights",
                    params={"weights_path": sovits_weights},
                )
                if not self._weight_load_succeeded(sovits_response):
                    return False
        except Exception:
            return False
        self._weights_ready_url = url
        return True

    async def ensure_available(self) -> SidecarState:
        command = os.getenv("SOVITS_SIDECAR_COMMAND", "").strip()
        working_directory = os.getenv("SOVITS_SIDECAR_CWD", "").strip() or None
        ports = range(9880, 9891) if command else (9880,)
        for port in ports:
            url = f"http://127.0.0.1:{port}"
            if await self._compatible(url):
                if not await self._load_configured_weights(url):
                    self.state = SidecarState(
                        "degraded", url, False, "configured GPT-SoVITS weights failed to load"
                    )
                    return self.state
                self.state = SidecarState("ready", url, False, None)
                return self.state
            if port == 9880 and await self._occupied(url):
                continue
            if not command:
                continue
            env = dict(os.environ, SOVITS_PORT=str(port))
            creationflags = 0x08000000 if os.name == "nt" else 0
            self.process = await asyncio.create_subprocess_exec(
                *self._command_args(command, port),
                env=env,
                cwd=working_directory,
                creationflags=creationflags,
            )
            # Cold CUDA/model initialization can take well over 30 seconds on
            # this V2Pro build. Keep the probe bounded, but allow two minutes
            # before terminating a still-running child.
            for _ in range(480):
                await asyncio.sleep(0.25)
                if self.process.returncode is not None:
                    break
                if await self._compatible(url):
                    if await self._load_configured_weights(url):
                        self.state = SidecarState("ready", url, True, None)
                        return self.state
                    break
            if self.process.returncode is None:
                self.process.terminate()
                await self.process.wait()
        self.state = SidecarState("degraded", None, False, "no compatible GPT-SoVITS endpoint on ports 9880-9890")
        return self.state

    async def _compatible(self, url: str) -> bool:
        try:
            # Local sidecar discovery must never inherit a system proxy.
            async with httpx.AsyncClient(timeout=0.6, trust_env=False) as client:
                response = await client.get(f"{url}/openapi.json")
                return response.status_code == 200 and "/tts" in response.text
        except Exception:
            return False

    async def _occupied(self, url: str) -> bool:
        try:
            async with httpx.AsyncClient(timeout=0.4, trust_env=False) as client:
                await client.get(url)
            return True
        except httpx.HTTPStatusError:
            return True
        except Exception:
            return False

    async def health(self) -> dict[str, object]:
        # A user may start the external GPT-SoVITS API after Amadeus.  A
        # one-shot startup probe would otherwise leave the application
        # permanently marked as degraded until it is restarted.
        if (
            self.state.status == "ready"
            and self.state.url
            and not await self._compatible(self.state.url)
        ):
            self.state = SidecarState("not_probed", None, False, "sidecar stopped")
            self._weights_ready_url = None
        if self.state.status != "ready":
            await self.ensure_available()
        return {"ok": self.state.status == "ready", "status": self.state.status, "url": self.state.url, "managed": self.state.managed, "error": self.state.error}

    async def close(self) -> None:
        if self.process and self.process.returncode is None:
            self.process.terminate()
            await self.process.wait()


sidecar_supervisor = SidecarSupervisor()
