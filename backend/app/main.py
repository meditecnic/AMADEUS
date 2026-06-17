from contextlib import asynccontextmanager
import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app import config
from app.routers import chat_ws
from app.services.tts_queue import tts_manager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    print("--------------------------------------------------")
    print("Amadeus Backend starting...")
    print(f"DeepSeek Model: {config.DEEPSEEK_MODEL}")
    print(f"GPT-SoVITS URL: {config.SOVITS_URL}")
    print(f"Assets Directory: {config.ASSETS_DIR}")
    print(f"History Compress Threshold: {config.HISTORY_COMPRESS_THRESHOLD} rounds")
    
    # Initialize async TTS client and test connection
    await tts_manager.init_client()
    try:
        await tts_manager.client.get(config.SOVITS_URL, timeout=1.0)
        print("[GPT-SoVITS] Voice Synthesizer reachable.")
    except Exception:
        print("[WARN] [GPT-SoVITS] Voice Synthesizer is OFFLINE.")
        print("[WARN] Run GPT-SoVITS server on port 9880 before voice interaction.")
    print("--------------------------------------------------")
    
    yield
    
    # Shutdown
    print("Amadeus Backend shutting down, cleaning resources...")
    await tts_manager.close_client()

app = FastAPI(title="Amadeus Backend", version="3.0.0", lifespan=lifespan)

# Setup CORS
allowed_origins = [
    "http://localhost",
    f"http://localhost:{config.FRONTEND_PORT}",
    "http://127.0.0.1",
    f"http://127.0.0.1:{config.FRONTEND_PORT}",
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

@app.get("/health")
async def health_check():
    return {"status": "ok"}
