from contextlib import asynccontextmanager
import asyncio
import os
import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, SecretStr
from typing import Optional
from app import config
from app.routers import asset_pack, chat_ws, conversations, history, providers, system_api, voice_ws
from app.services.tts_queue import tts_manager
from app.services.tts_sidecar import sidecar_supervisor
from app.services.search import search_service
from app.services.soul_engine import soul_engine
from app.services.diagnostics import diagnostic_runtime
from app.db import init_db


DIAGNOSTICS_SWEEP_INTERVAL_SECONDS = 24 * 60 * 60


async def _diagnostics_sweep_loop() -> None:
    while True:
        await asyncio.to_thread(diagnostic_runtime.sweep_best_effort)
        await asyncio.sleep(DIAGNOSTICS_SWEEP_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    print("--------------------------------------------------")
    print("Amadeus Backend starting...")
    from app.services.credentials import credential_store, scrub_dotenv_key
    from app.services.provider_runtime import deepseek_service

    if "PYTEST_CURRENT_TEST" not in os.environ:
        legacy_key = config.DEEPSEEK_API_KEY.strip()
        if legacy_key and credential_store.get("deepseek") is None:
            credential_store.set("deepseek", legacy_key)
        if legacy_key:
            scrub_dotenv_key(config.env_path, "DEEPSEEK_API_KEY")
        os.environ.pop("DEEPSEEK_API_KEY", None)
        config.DEEPSEEK_API_KEY = ""
        deepseek_service.api_key = ""
    await init_db()
    from app.services.conversation_erasure import resume_erasures
    for worldline in ('steins_gate', 'beta'):
        await resume_erasures(worldline=worldline)
    from app.services.provider_runtime import restore_custom_provider_configuration
    await restore_custom_provider_configuration()
    soul_engine.load_profiles()
    await asyncio.to_thread(diagnostic_runtime.start)
    print("[Database] SQLite initialized.")
    print(f"DeepSeek Model: {config.DEEPSEEK_MODEL}")
    print(f"GPT-SoVITS URL: {config.SOVITS_URL}")
    print(f"Assets Directory: {config.ASSETS_DIR}")
    print(f"History Compress Threshold: {config.HISTORY_COMPRESS_THRESHOLD} rounds")
    
    # Initialize async TTS client (health check per-request, not at startup)
    await tts_manager.init_client()
    await search_service.start()
    sidecar_state = await sidecar_supervisor.ensure_available()
    if sidecar_state.url:
        tts_manager.sovits_url = sidecar_state.url
    print("[GPT-SoVITS] TTS will be attempted per request.")

    # MEMORY-V11: background worker only when shadow/v11 is explicitly enabled.
    from app.services.memory_v11.jobs import (
        memory_mode,
        start_background_worker,
        stop_background_worker,
    )

    _mem_mode = memory_mode()
    if _mem_mode in {"shadow", "v11"}:
        await start_background_worker()
        print(f"[MemoryV11] worker started (AMADEUS_MEMORY_MODE={_mem_mode})")
    else:
        print(f"[MemoryV11] mode={_mem_mode} (legacy ingest; no v11 worker)")
    print("--------------------------------------------------")

    diagnostics_sweep = asyncio.create_task(_diagnostics_sweep_loop())
    yield
    
    # Shutdown
    print("Amadeus Backend shutting down, cleaning resources...")
    diagnostics_sweep.cancel()
    await stop_background_worker()
    await tts_manager.close_client()
    await search_service.close()
    await sidecar_supervisor.close()
    from app.services.memory import memory_service
    await memory_service.close()
    from app.services.conversations import conversation_service
    await conversation_service.close()
    await asyncio.gather(diagnostics_sweep, return_exceptions=True)

app = FastAPI(title="Amadeus Backend", version="3.0.0", lifespan=lifespan)

allowed_origins = [
    "http://localhost",
    f"http://localhost:{config.FRONTEND_PORT}",
    "http://127.0.0.1",
    f"http://127.0.0.1:{config.FRONTEND_PORT}",
    f"http://127.0.0.1:{config.BACKEND_PORT}",
    f"http://localhost:{config.BACKEND_PORT}",
    "http://localhost:1420",
    "tauri://localhost",
    "https://tauri.localhost"
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include WebSocket router
app.include_router(chat_ws.router)
app.include_router(history.router)
app.include_router(system_api.router)
app.include_router(voice_ws.router)
app.include_router(providers.router)
app.include_router(conversations.router)
app.include_router(asset_pack.router)

from app.security.prompt import SYSTEM_PROMPT_BASE as DEFAULT_SYSTEM_PROMPT

in_memory_settings = {
    'sovits_url': config.SOVITS_URL,
    'system_prompt': DEFAULT_SYSTEM_PROMPT,
    'remember_me': True,
    'enable_tts': True,
    'enable_bgm': True,
    'bgm_volume': 0.15
}

class SettingsUpdate(BaseModel):
    api_key: Optional[SecretStr] = None
    sovits_url: Optional[str] = None
    system_prompt: Optional[str] = None
    remember_me: Optional[bool] = None
    enable_tts: Optional[bool] = None
    enable_bgm: Optional[bool] = None
    bgm_volume: Optional[float] = None

@app.get("/api/settings")
async def get_settings():
    return in_memory_settings

@app.post("/api/settings")
async def update_settings(settings: SettingsUpdate):
    update_data = settings.model_dump(exclude_unset=True)
    secret = update_data.pop("api_key", None)
    if secret is not None:
        from app.services.credentials import credential_store

        credential_store.set("deepseek", secret.get_secret_value())
    in_memory_settings.update(update_data)
    return {"status": "success"}

class SalieriAuth(BaseModel):
    password: str

def normalize_password(p: str) -> str:
    return p.lower().replace("ü", "u").replace("ue", "u").strip()

@app.post("/api/auth/salieri")
async def auth_salieri(auth: SalieriAuth):
    if normalize_password(auth.password) == "gott wurfelt nicht":
        return {"status": "success"}
    from fastapi import HTTPException
    raise HTTPException(status_code=401, detail="Invalid password")

@app.get("/health")
async def health_check():
    return {"status": "ok"}
