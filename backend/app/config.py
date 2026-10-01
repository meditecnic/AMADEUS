import os
from pathlib import Path
from dotenv import load_dotenv

# Resolve paths
backend_dir = Path(__file__).resolve().parent.parent
env_path = backend_dir / ".env"
# Isolated runtimes set PYTHON_DOTENV_DISABLED before importing this module.
# The flag is checked here so config import cannot scrub or inherit a real .env.
_DOTENV_DISABLED = os.environ.get("PYTHON_DOTENV_DISABLED", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
if not _DOTENV_DISABLED:
    load_dotenv(dotenv_path=env_path)

# Load variables
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-flash")
SOVITS_URL = os.getenv("SOVITS_URL", "http://127.0.0.1:9880").rstrip("/")
FRONTEND_PORT = int(os.getenv("FRONTEND_PORT", "5173"))
BACKEND_PORT = int(os.getenv("BACKEND_PORT", "8000"))
ASSETS_DIR = Path(os.getenv("ASSETS_DIR", str(backend_dir.parent / "frontend" / "public" / "assets")))
HISTORY_COMPRESS_THRESHOLD = int(os.getenv("HISTORY_COMPRESS_THRESHOLD", "15"))

PROMPTS_DIR = backend_dir / "prompts"
CHARACTERS_DIR = backend_dir / "characters"

# Memory and prompt compiler invariants
EMBEDDING_MODEL = os.getenv("AMADEUS_EMBEDDING_MODEL", "intfloat/multilingual-e5-small")
EMBEDDING_DIMENSION = int(os.getenv("AMADEUS_EMBEDDING_DIMENSION", "384"))
PROMPT_INPUT_BUDGET = int(os.getenv("AMADEUS_PROMPT_INPUT_BUDGET", "12000"))
PROMPT_OUTPUT_RESERVE = int(os.getenv("AMADEUS_PROMPT_OUTPUT_RESERVE", "2000"))

# Voice endpoint defaults
VAD_SILENCE_MS = int(os.getenv("AMADEUS_VAD_SILENCE_MS", "600"))
VAD_MIN_SPEECH_MS = int(os.getenv("AMADEUS_VAD_MIN_SPEECH_MS", "250"))
VAD_SPEECH_PAD_MS = int(os.getenv("AMADEUS_VAD_SPEECH_PAD_MS", "120"))
VAD_MAX_UTTERANCE_S = int(os.getenv("AMADEUS_VAD_MAX_UTTERANCE_S", "30"))
VAD_THRESHOLD = float(os.getenv("AMADEUS_VAD_THRESHOLD", "0.5"))

if not 300 <= VAD_SILENCE_MS <= 2000:
    raise ValueError("AMADEUS_VAD_SILENCE_MS must be between 300 and 2000")

# TTS reference audio paths (overridable via env vars)
# Optimized 9-emotion mapping using RMS-normalized reference clips.
DEFAULT_REF_AUDIO_DIR = backend_dir.parent / "desktop" / "public" / "assets" / "audio"
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
