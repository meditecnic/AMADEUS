import hashlib
import math
import os
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# User-directory isolation (audit P0).
#
# Some paths are resolved at import time (for example
# app.config.DEFAULT_REF_AUDIO_DIR and speech_service._cache_dir both call
# get_data_root() while the module is imported, and app.config loads
# backend/.env on import). Test modules import app code during collection,
# before any fixture runs, so the environment is prepared here, when pytest
# imports this conftest and before any test module (and therefore any app
# module) is imported.
# ---------------------------------------------------------------------------

# Path-like variables read by backend/app and backend/scripts. An inherited
# value would point tests at real files, so they are removed for the whole
# session and again before every test. A test may still set its own value
# with monkeypatch; that takes effect normally.
_CLEARED_PATH_ENV_KEYS = (
    "AMADEUS_DB_PATH",  # app/db.py: takes precedence over AMADEUS_DATA_DIR
    "AMADEUS_ASSET_PACK_DIR",  # app/routers/asset_pack.py
    "ASSETS_DIR",  # app/config.py (default: repo desktop/public/assets)
    "AMADEUS_REF_NEUTRAL",  # app/config.py reference audio paths ...
    "AMADEUS_REF_TSUNDERE",
    "AMADEUS_REF_EMBARRASSED",
    "AMADEUS_REF_INTELLECTUAL",
    "AMADEUS_REF_HAPPY",
    "AMADEUS_REF_SURPRISED",
    "AMADEUS_REF_ANNOYED",
    "AMADEUS_REF_DISAPPOINTED",
    "AMADEUS_REF_SAD",  # ... (default: <data root>/voice-pack/ref)
    "AMADEUS_VAD_MODEL",  # app/services/speech.py
    "SENSEVOICE_MODEL",  # app/services/speech.py
    "SENSEVOICE_TOKENS",  # app/services/speech.py
    "SOVITS_GPT_WEIGHTS",  # app/services/tts_sidecar.py
    "SOVITS_SOVITS_WEIGHTS",  # app/services/tts_sidecar.py
    "SOVITS_SIDECAR_COMMAND",  # app/services/tts_sidecar.py (launches a process)
    "SOVITS_SIDECAR_CWD",  # app/services/tts_sidecar.py
    "AMADEUS_B3_CREDENTIAL_FILE",  # scripts/memory_candidate_real_run.py
    # Model caches: never read or fill the user's Hugging Face cache.
    "HF_HUB_CACHE",
    "TRANSFORMERS_CACHE",
    "SENTENCE_TRANSFORMERS_HOME",
)
# Secrets and real-network switches read by backend/app and backend/scripts.
# An inherited value would hand tests a real key or let them reach a real
# service, so they are cleared exactly like the path variables above (session
# start and before every test). A test may still set its own fake value with
# monkeypatch; that takes effect normally.
_CLEARED_SECRET_ENV_KEYS = (
    "DEEPSEEK_API_KEY",  # app/config.py
    "TAVILY_API_KEY",  # app/services/search.py env fallback
    "FIRECRAWL_API_KEY",  # app/services/search.py env fallback
    "AMADEUS_B3_PROVIDER_KEY",  # scripts/memory_candidate_real_run.py
    "AMADEUS_B3_REAL_HTTP",  # scripts/memory_candidate_real_run.py (real HTTP switch)
    "AMADEUS_B3_BASE_URL",  # scripts/memory_candidate_real_run.py
    "SOVITS_URL",  # app/config.py TTS endpoint (default: 127.0.0.1)
    "HF_TOKEN",  # Hugging Face libraries
    "HUGGING_FACE_HUB_TOKEN",
)
_CLEARED_ENV_KEYS = (*_CLEARED_PATH_ENV_KEYS, *_CLEARED_SECRET_ENV_KEYS)
# Opt-in for tests marked real_credential_store: set to 1 to let them construct
# the real Windows Credential Manager store. Unset by default.
_REAL_CREDENTIAL_STORE_OPT_IN = os.environ.get("AMADEUS_TEST_REAL_CREDENTIAL_STORE", "").strip() == "1"
# Opt-in for tests marked real_embedding: a local, already-populated Hugging
# Face cache directory to read the model from (offline). Unset by default.
_REAL_EMBEDDING_HF_HOME = os.environ.get("AMADEUS_TEST_HF_HOME", "").strip()

_SESSION_ROOT = Path(tempfile.mkdtemp(prefix="amadeus-tests-"))
_SESSION_HOME = _SESSION_ROOT / "home"


def _isolated_env(data_dir: Path, home_dir: Path) -> dict[str, str]:
    return {
        "AMADEUS_DATA_DIR": str(data_dir),
        "HOME": str(home_dir),
        "USERPROFILE": str(home_dir),  # Path.home() on Windows
        "APPDATA": str(home_dir / "AppData" / "Roaming"),  # data root default on Windows
        "LOCALAPPDATA": str(home_dir / "AppData" / "Local"),
        "HF_HOME": _REAL_EMBEDDING_HF_HOME or str(home_dir / ".cache" / "huggingface"),
    }


_SESSION_ENV = _isolated_env(_SESSION_ROOT / "data", _SESSION_HOME)
_FIXED_ENV = {
    # app/config.py skips backend/.env when this is set: no real config/keys.
    "PYTHON_DOTENV_DISABLED": "1",
    # Never download models during tests.
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
    # app/services/credentials.py picks its process-wide store when it is
    # imported; "memory" keeps it (and child processes) away from the
    # Windows Credential Manager. Must be set before any app import.
    "AMADEUS_CREDENTIAL_BACKEND": "memory",
}
_ORIGINAL_ENV = {
    key: os.environ.get(key)
    for key in (*_SESSION_ENV, *_FIXED_ENV, *_CLEARED_ENV_KEYS)
}
for _key in _CLEARED_ENV_KEYS:
    os.environ.pop(_key, None)
for _key in ("AMADEUS_DATA_DIR", "HOME", "APPDATA", "LOCALAPPDATA"):
    Path(_SESSION_ENV[_key]).mkdir(parents=True, exist_ok=True)
os.environ.update(_SESSION_ENV)
os.environ.update(_FIXED_ENV)


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "real_embedding: needs the real SentenceTransformer embedding model; "
        "skipped unless AMADEUS_TEST_HF_HOME points at a local HF cache that has it",
    )
    config.addinivalue_line(
        "markers",
        "real_credential_store: may construct the real Windows Credential Manager store; "
        "skipped unless AMADEUS_TEST_REAL_CREDENTIAL_STORE=1",
    )


def pytest_unconfigure(config):
    for key, value in _ORIGINAL_ENV.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    shutil.rmtree(_SESSION_ROOT, ignore_errors=True)


@pytest.fixture(autouse=True)
def isolated_user_dirs(tmp_path_factory):
    """Point the data root and every home-directory variable at fresh temp dirs.

    Ordering is enforced by fixture dependency, not definition order: the
    ``monkeypatch`` override below requests this fixture, so isolation is set
    up before any fixture or test can use ``monkeypatch`` and is undone only
    after that ``monkeypatch`` has been undone. It uses its own MonkeyPatch,
    so a test calling ``monkeypatch.undo()`` cannot remove this isolation.
    """
    data_dir = tmp_path_factory.mktemp("amadeus-data")
    home_dir = tmp_path_factory.mktemp("home")
    mp = pytest.MonkeyPatch()
    try:
        for key in _CLEARED_ENV_KEYS:
            mp.delenv(key, raising=False)
        for key, value in {**_isolated_env(data_dir, home_dir), **_FIXED_ENV}.items():
            mp.setenv(key, value)
        yield data_dir
    finally:
        mp.undo()


@pytest.fixture
def monkeypatch(isolated_user_dirs):
    """Built-in ``monkeypatch``, made to depend on ``isolated_user_dirs``."""
    mp = pytest.MonkeyPatch()
    yield mp
    mp.undo()


# ---------------------------------------------------------------------------
# Offline embeddings.
#
# EmbeddingAdapter lazily loads a SentenceTransformer (E5). Tests must not
# download it, so by default the model is replaced by a deterministic local
# stand-in with the same interface (encode(text, normalize_embeddings=True)
# returning a unit vector of the configured dimension). Prefixes such as
# "query: " / "passage: " are ignored and the vector is built from hashed
# character n-grams, so texts sharing words still score as similar. Subclasses
# that override encode_* (e.g. test_memory_v11_api's stub) are unaffected.
# Tests that need the real model use @pytest.mark.real_embedding.
# ---------------------------------------------------------------------------
class _DeterministicVector(list):
    def tolist(self):
        return list(self)


class _DeterministicEmbeddingModel:
    def __init__(self, dimensions: int):
        self.dimensions = dimensions

    def encode(self, text, normalize_embeddings=True, **_kwargs):
        body = str(text)
        for prefix in ("query: ", "passage: "):
            if body.startswith(prefix):
                body = body[len(prefix):]
                break
        body = body.strip().lower()
        vector = [0.0] * self.dimensions
        grams = [body[i:i + n] for n in (1, 2, 3) for i in range(len(body) - n + 1)]
        for gram in grams or [""]:
            digest = hashlib.sha256(gram.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "little") % self.dimensions
            vector[index] += 1.0 if digest[4] & 1 else -1.0
        norm = math.sqrt(sum(v * v for v in vector))
        if norm == 0.0:
            vector[0], norm = 1.0, 1.0
        return _DeterministicVector(v / norm for v in vector)


@pytest.fixture(autouse=True)
def offline_embedding_model(request, monkeypatch):
    from app.services.memory import EmbeddingAdapter, memory_service

    if request.node.get_closest_marker("real_embedding"):
        if not _REAL_EMBEDDING_HF_HOME:
            pytest.skip("real_embedding: set AMADEUS_TEST_HF_HOME to a local HF cache with the model")
        try:
            EmbeddingAdapter()._load()
        except Exception as exc:  # model missing from the offline cache
            pytest.skip(f"real_embedding: model unavailable offline ({type(exc).__name__})")
        # Do not let a stand-in cached by an earlier test leak into this one.
        if isinstance(getattr(memory_service.embedder, "_model", None), _DeterministicEmbeddingModel):
            monkeypatch.setattr(memory_service.embedder, "_model", None)
        return

    def _load(self):
        if self._model is None:
            self._model = _DeterministicEmbeddingModel(self.dimensions)
        return self._model

    monkeypatch.setattr(EmbeddingAdapter, "_load", _load)


@pytest.fixture(autouse=True)
def forbid_real_credential_store(request, isolated_user_dirs):
    """Fail any attempt to construct WindowsCredentialStore during a test.

    Covers code that builds the store directly instead of using the
    process-wide ``credential_store`` (for example
    scripts/memory_candidate_real_run.py reading the DeepSeek key). Tests
    marked ``real_credential_store`` are skipped unless
    AMADEUS_TEST_REAL_CREDENTIAL_STORE=1, and only then may construct it.
    Uses its own MonkeyPatch so ``monkeypatch.undo()`` cannot lift it.
    """
    from app.services import credentials

    if request.node.get_closest_marker("real_credential_store"):
        if not _REAL_CREDENTIAL_STORE_OPT_IN:
            pytest.skip("real_credential_store: set AMADEUS_TEST_REAL_CREDENTIAL_STORE=1 to run")
        yield
        return
    assert not credentials.WINDOWS_CREDENTIAL_STORE_CONSTRUCTED, (
        "app.services.credentials was imported with the Windows Credential Manager store"
    )

    def _refuse(self, *args, **kwargs):
        raise RuntimeError(
            "tests must not construct WindowsCredentialStore; "
            "mark the test real_credential_store if it really needs it"
        )

    mp = pytest.MonkeyPatch()
    try:
        mp.setattr(credentials.WindowsCredentialStore, "__init__", _refuse)
        yield
    finally:
        mp.undo()


@pytest.fixture(autouse=True)
def isolated_provider_credentials(monkeypatch):
    """Keep tests out of the user's real Windows Credential Manager."""
    from app.routers import chat_ws, providers, system_api, voice_ws
    from app.services import credentials, search
    from app.services.credentials import InMemoryCredentialStore

    store = InMemoryCredentialStore()
    store.set("deepseek", "test-provider-credential")
    monkeypatch.setattr(credentials, "credential_store", store)
    monkeypatch.setattr(chat_ws, "credential_store", store)
    monkeypatch.setattr(voice_ws, "credential_store", store)
    monkeypatch.setattr(providers, "credential_store", store)
    monkeypatch.setattr(system_api, "credential_store", store)
    monkeypatch.setattr(search, "credential_store", store)
    providers.provider_model_cache.clear()
    return store


@pytest.fixture
def app_client(isolated_provider_credentials, tmp_path, monkeypatch):
    """Run the ASGI app in a managed portal without launching a TTS sidecar."""
    from app.main import app
    from app.db import reset_initialization_cache

    monkeypatch.delenv('AMADEUS_DB_PATH', raising=False)
    monkeypatch.setenv('AMADEUS_DATA_DIR', str(tmp_path))
    reset_initialization_cache()

    with patch(
        "app.main.sidecar_supervisor.ensure_available",
        new=AsyncMock(return_value=SimpleNamespace(url=None)),
    ), patch(
        "app.main.sidecar_supervisor.close",
        new=AsyncMock(),
    ):
        with TestClient(app, base_url="http://localhost", headers={"host": "localhost", "origin": "http://localhost:1420"}) as client:
            yield client
    reset_initialization_cache()


@pytest.fixture(autouse=True)
def isolated_speech_detector(monkeypatch):
    """Endpoint tests use synthetic speech detection without model downloads."""
    monkeypatch.setattr("app.services.speech.speech_service.has_speech", AsyncMock(return_value=True))
