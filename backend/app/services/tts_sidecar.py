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

    async def ensure_available(self) -> SidecarState:
        command = os.getenv("SOVITS_SIDECAR_COMMAND", "").strip()
        ports = range(9880, 9891) if command else (9880,)
        for port in ports:
            url = f"http://127.0.0.1:{port}"
            if await self._compatible(url):
                self.state = SidecarState("ready", url, False, None)
                return self.state
            if port == 9880 and await self._occupied(url):
                continue
            if not command:
                continue
            env = dict(os.environ, SOVITS_PORT=str(port))
            creationflags = 0x08000000 if os.name == "nt" else 0
            self.process = await asyncio.create_subprocess_exec(*shlex.split(command), env=env, creationflags=creationflags)
            for _ in range(20):
                await asyncio.sleep(0.25)
                if await self._compatible(url):
                    self.state = SidecarState("ready", url, True, None)
                    return self.state
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
        if self.state.status != "ready":
            await self.ensure_available()
        return {"ok": self.state.status == "ready", "status": self.state.status, "url": self.state.url, "managed": self.state.managed, "error": self.state.error}

    async def close(self) -> None:
        if self.process and self.process.returncode is None:
            self.process.terminate()
            await self.process.wait()


sidecar_supervisor = SidecarSupervisor()
