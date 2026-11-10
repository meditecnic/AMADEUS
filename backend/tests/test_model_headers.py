import pytest

from app.services.model_headers import model_headers
from app.services.provider_runtime import provider_registry


@pytest.mark.parametrize("version,expected", [("1.2.3", "AMADEUS/1.2.3"), ("", "AMADEUS/dev")])
def test_model_identity_headers(monkeypatch, version, expected):
    monkeypatch.setenv("AMADEUS_APP_VERSION", version)
    assert model_headers()["User-Agent"] == expected
    for provider in ["glm", "custom", "openai", "gemini"]:
        adapter = provider_registry.snapshot(provider).adapter
        assert adapter._headers("fake")["User-Agent"] == expected


def test_opencode_session_is_scoped_private_and_inherited():
    from app.services.model_usage import usage_scope
    endpoint = "https://opencode.ai/inference/go/v1"
    first = model_headers(endpoint)["x-opencode-session"]
    assert first != model_headers(endpoint)["x-opencode-session"]
    with usage_scope(source="chat", conversation_id="private-conversation"):
        session = model_headers(endpoint)["x-opencode-session"]
        assert "private-conversation" not in session
        with usage_scope(source="memory"):
            assert model_headers(endpoint)["x-opencode-session"] == session
        with usage_scope(source="translation"):
            assert model_headers(endpoint)["x-opencode-session"] == session
    with usage_scope(source="chat", conversation_id="other"):
        assert model_headers(endpoint)["x-opencode-session"] != session
    for address in ["https://opencode.ai/inference/gone", "https://opencode.ai.evil/inference/go", "https://example.invalid/v1"]:
        assert "x-opencode-session" not in model_headers(address)
