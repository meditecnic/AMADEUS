import os
from pathlib import Path
from dotenv import load_dotenv

# Resolve paths
backend_dir = Path(__file__).resolve().parent.parent
env_path = backend_dir / ".env"
load_dotenv(dotenv_path=env_path)

# Load variables
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
SOVITS_URL = os.getenv("SOVITS_URL", "http://127.0.0.1:9880").rstrip("/")
FRONTEND_PORT = int(os.getenv("FRONTEND_PORT", "5173"))
BACKEND_PORT = int(os.getenv("BACKEND_PORT", "8000"))
ASSETS_DIR = Path(os.getenv("ASSETS_DIR", "<LOCAL_PATH>"))
HISTORY_COMPRESS_THRESHOLD = int(os.getenv("HISTORY_COMPRESS_THRESHOLD", "15"))

PROMPTS_DIR = backend_dir / "prompts"
