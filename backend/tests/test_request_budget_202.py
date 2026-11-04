"""Model limits travel with each turn and bound all chat request paths."""

import pytest

from app.services.model_catalog import ModelCatalog, model_catalog
from app.services.provider_registry import (
    ProviderCapabilities,
    ProviderDefinition,
    ProviderRegistry,
    ProviderTask,
)


def registry_with_model(provider_id="custom", model_id="local-8k", limits=None):
    catalog = ModelCatalog.from_mapping(model_catalog.manifest)
    if limits is not None:
        catalog.reconcile(provider_id, [model_id], [{"id": model_id, **limits}])
    registry = ProviderRegistry(model_catalog=catalog)
    registry.register(ProviderDefinition(
        id=provider_id, display_name="Budget test", default_model=model_id,
        capabilities=ProviderCapabilities(tasks=frozenset({ProviderTask.CHAT})),
    ), object())
    return registry, catalog


@pytest.mark.parametrize("kind", ["static", "discovered", "unknown"])
def test_snapshot_carries_catalog_limits(kind):
    provider, model = ("deepseek", "deepseek-flash") if kind == "static" else ("custom", "local-8k")
    limits = {"context_window": 8192, "max_output_tokens": 1024} if kind == "discovered" else None
    registry, catalog = registry_with_model(provider, model, limits)
    snapshot = registry.snapshot(provider, model)
    expected = catalog.public_model(provider, model)
    assert snapshot.context_window == expected["context_window"]
    assert snapshot.max_output_tokens == expected["max_output_tokens"]
    if kind == "discovered":
        catalog.reconcile(provider, [model], [{"id": model, "context_window": 16384}])
        assert snapshot.context_window == 8192  # Frozen for this turn.
    elif kind == "unknown":
        assert snapshot.context_window is None
        assert snapshot.max_output_tokens is None


def test_snapshot_without_catalog_has_no_limits():
    registry, _ = registry_with_model()
    registry._model_catalog = None
    snapshot = registry.snapshot("custom")
    assert snapshot.context_window is None
    assert snapshot.max_output_tokens is None
