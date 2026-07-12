import os
from pathlib import Path
from dotenv import load_dotenv

# Resolve paths
backend_dir = Path(__file__).resolve().parent.parent
env_path = backend_dir / ".env"
load_dotenv(dotenv_path=env_path)

# Load variables
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
SOVITS_URL = os.getenv("SOVITS_URL", "http://127.0.0.1:9880").rstrip("/")
FRONTEND_PORT = int(os.getenv("FRONTEND_PORT", "5173"))
BACKEND_PORT = int(os.getenv("BACKEND_PORT", "8000"))
ASSETS_DIR = Path(os.getenv("ASSETS_DIR", "<LOCAL_PATH>"))
HISTORY_COMPRESS_THRESHOLD = int(os.getenv("HISTORY_COMPRESS_THRESHOLD", "15"))

PROMPTS_DIR = backend_dir / "prompts"

# TTS reference audio paths (overridable via env vars)
# Optimized 9-emotion mapping using RMS-normalized reference clips.
DEFAULT_REF_AUDIO_DIR = r"<LOCAL_PATH>"
REF_AUDIO_NEUTRAL = os.getenv("AMADEUS_REF_NEUTRAL", str(Path(DEFAULT_REF_AUDIO_DIR) / "ref" / "ref_neutral.wav"))
REF_AUDIO_TSUNDERE = os.getenv("AMADEUS_REF_TSUNDERE", str(Path(DEFAULT_REF_AUDIO_DIR) / "ref" / "ref_tsundere.wav"))
REF_AUDIO_EMBARRASSED = os.getenv("AMADEUS_REF_EMBARRASSED", str(Path(DEFAULT_REF_AUDIO_DIR) / "ref" / "ref_embarrassed.wav"))
REF_AUDIO_INTELLECTUAL = os.getenv("AMADEUS_REF_INTELLECTUAL", str(Path(DEFAULT_REF_AUDIO_DIR) / "ref" / "ref_intellectual.wav"))
REF_AUDIO_HAPPY = os.getenv("AMADEUS_REF_HAPPY", str(Path(DEFAULT_REF_AUDIO_DIR) / "ref" / "ref_happy.wav"))
REF_AUDIO_SURPRISED = os.getenv("AMADEUS_REF_SURPRISED", str(Path(DEFAULT_REF_AUDIO_DIR) / "ref" / "ref_surprised.wav"))
REF_AUDIO_ANNOYED = os.getenv("AMADEUS_REF_ANNOYED", str(Path(DEFAULT_REF_AUDIO_DIR) / "ref" / "ref_annoyed.wav"))
REF_AUDIO_DISAPPOINTED = os.getenv("AMADEUS_REF_DISAPPOINTED", str(Path(DEFAULT_REF_AUDIO_DIR) / "ref" / "ref_disappointed.wav"))
REF_AUDIO_SAD = os.getenv("AMADEUS_REF_SAD", str(Path(DEFAULT_REF_AUDIO_DIR) / "ref" / "ref_sad.wav"))

# TTS timeout (seconds)
TTS_TIMEOUT = float(os.getenv("AMADEUS_TTS_TIMEOUT", "60.0"))
