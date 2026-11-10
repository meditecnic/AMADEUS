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
