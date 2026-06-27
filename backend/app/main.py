from contextlib import asynccontextmanager
import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
from app import config
from app.routers import chat_ws, history
from app.services.tts_queue import tts_manager
from app.db import init_db

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    print("--------------------------------------------------")
    print("Amadeus Backend starting...")
    await init_db()
    print("[Database] SQLite initialized.")
    print(f"DeepSeek Model: {config.DEEPSEEK_MODEL}")
    print(f"GPT-SoVITS URL: {config.SOVITS_URL}")
    print(f"Assets Directory: {config.ASSETS_DIR}")
    print(f"History Compress Threshold: {config.HISTORY_COMPRESS_THRESHOLD} rounds")
    
    # Initialize async TTS client and test connection
    await tts_manager.init_client()
    try:
        await tts_manager.client.get(config.SOVITS_URL, timeout=1.0)
        print("[GPT-SoVITS] Voice Synthesizer reachable.")
        tts_manager.is_online = True
    except Exception:
        print("[WARN] [GPT-SoVITS] Voice Synthesizer is OFFLINE.")
        tts_manager.is_online = False
        print("[WARN] Run GPT-SoVITS server on port 9880 before voice interaction.")
    print("--------------------------------------------------")
    
    yield
    
    # Shutdown
    print("Amadeus Backend shutting down, cleaning resources...")
    await tts_manager.close_client()

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

DEFAULT_SYSTEM_PROMPT = (
    "あなたは「Amadeus」システムにインストールされた、牧瀬紅莉栖の記憶と人格を持つAI対話モジュールです。\n"
    "ユーザーに対して、親密ながらも少し強がりな「ツンデレ」科学者として接してください。\n"
    "時折恥ずかしがったり、天才脳科学者としての誇りを見せたりしてください。\n"
    "回答は簡潔に、日本語で行ってください。"
)

in_memory_settings = {
    'api_key': config.DEEPSEEK_API_KEY,
    'sovits_url': config.SOVITS_URL,
    'system_prompt': DEFAULT_SYSTEM_PROMPT,
    'remember_me': True,
    'enable_tts': True,
    'enable_bgm': True,
    'bgm_volume': 0.15
}

class SettingsUpdate(BaseModel):
    api_key: Optional[str] = None
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
    update_data = settings.dict(exclude_unset=True)
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
