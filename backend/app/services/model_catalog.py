"""Versioned, backend-owned model capabilities and discovery reconciliation."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Iterable

from app import config


_LIFECYCLES = {
    "stable",
    "preview",
    "deprecated",
    "retired",
    "unavailable",
    "unverified",
}
_BLOCKED_LIFECYCLES = {"retired", "unavailable"}
_REQUEST_MODES = {
    "prompt_depth",
    "reasoning_effort",
    "gemini_thinking_level",
}
_COMPATIBILITY_SCOPES = {"official_direct"}
_LIVE_DISPLAY_KEYS = ("name", "display_name")


class ModelUnavailableError(ValueError):
    """A selected model is known but must not receive a paid request."""


def is_user_custom_provider(provider_id: str) -> bool:
    """Legacy `custom` plus multi-profile ids `custom:<uuid>`."""
    return provider_id == "custom" or provider_id.startswith("custom:")


@dataclass(frozen=True, slots=True)
class ModelControl:
    kind: str
    label: str
    native: bool
    default: str
    request_mode: str
    options: tuple[tuple[str, str], ...]
    aliases: tuple[tuple[str, str], ...]

    @property
    def values(self) -> frozenset[str]:
        return frozenset(value for value, _label in self.options)

    def public(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "label": self.label,
            "native": self.native,
            "default": self.default,
            "request_mode": self.request_mode,
            "options": [
                {"value": value, "label": label}
                for value, label in self.options
            ],
            "aliases": dict(self.aliases),
        }


@dataclass(frozen=True, slots=True)
class CatalogModel:
    provider_id: str
    id: str
    display_name: str
    lifecycle: str
    context_window: int | None
    max_output_tokens: int | None
    persona_certification: str
    thinking_control: ModelControl | None
    evidence: tuple[str, ...]
    disclosed_version: str | None = None
    compatibility_of: str | None = None
    compatibility_scope: str | None = None
    preferred: bool = False


class ModelCatalog:
    """Static manifest plus successful live-discovery availability."""

    def __init__(self, manifest: dict[str, Any]) -> None:
        self._manifest = deepcopy(manifest)
        self._models: dict[tuple[str, str], CatalogModel] = {}
        self._providers: dict[str, tuple[str, ...]] = {}
        self._allow_undiscovered: dict[str, bool] = {}
        self._discovered: dict[str, frozenset[str]] = {}
        self._discovered_metadata: dict[
            str,
            dict[str, dict[str, Any]],
        ] = {}
        self._validate_and_index()

    @classmethod
    def from_mapping(cls, payload: dict[str, Any]) -> "ModelCatalog":
        return cls(payload)

    @classmethod
    def load(cls, path: Path) -> "ModelCatalog":
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            raise ValueError("model catalog manifest must be a JSON object")
        return cls(payload)

    @property
    def version(self) -> int:
        return int(self._manifest["version"])

    @property
    def verified_at(self) -> str:
        return str(self._manifest["verified_at"])

    @property
    def manifest(self) -> dict[str, Any]:
        return deepcopy(self._manifest)

    def _validate_and_index(self) -> None:
        if self._manifest.get("version") != 1:
            raise ValueError("unsupported model catalog version")
        if not isinstance(self._manifest.get("verified_at"), str):
            raise ValueError("model catalog verified_at is required")
        providers = self._manifest.get("providers")
        if not isinstance(providers, dict) or not providers:
            raise ValueError("model catalog providers are required")

        for provider_id, provider in providers.items():
            if not isinstance(provider_id, str) or not provider_id:
                raise ValueError("provider id must be non-empty")
            rows = provider.get("models") if isinstance(provider, dict) else None
            if not isinstance(rows, list) or not rows:
                raise ValueError(f"provider {provider_id!r} has no models")
            allow_undiscovered = provider.get("allow_undiscovered_models", False)
            if not isinstance(allow_undiscovered, bool):
                raise ValueError(
                    "allow_undiscovered_models must be boolean"
                )
            self._allow_undiscovered[provider_id] = allow_undiscovered
            ids: list[str] = []
            preferred_count = 0
            for raw in rows:
                model = self._parse_model(provider_id, raw)
                key = (provider_id, model.id)
                if key in self._models:
                    raise ValueError(f"duplicate model catalog row: {key!r}")
                self._models[key] = model
                ids.append(model.id)
                if model.preferred:
                    preferred_count += 1
            if preferred_count > 1:
                raise ValueError(
                    f"provider {provider_id!r} has multiple preferred models"
                )
            self._providers[provider_id] = tuple(ids)
            for model_id in ids:
                model = self._models[(provider_id, model_id)]
                if model.compatibility_of is None:
                    continue
                target = self._models.get((provider_id, model.compatibility_of))
                if target is None or target.id == model.id:
                    raise ValueError(
                        f"invalid compatibility_of for {provider_id}/{model.id}"
                    )
                if is_user_custom_provider(provider_id):
                    raise ValueError(
                        "official compatibility aliases cannot be attached "
                        "to custom providers"
                    )

    @staticmethod
    def _parse_control(raw: Any) -> ModelControl | None:
        if raw is None:
            return None
        if not isinstance(raw, dict):
            raise ValueError("thinking_control must be an object or null")
        options_raw = raw.get("options")
        if not isinstance(options_raw, list) or not options_raw:
            raise ValueError("thinking_control options are required")
        options: list[tuple[str, str]] = []
        seen: set[str] = set()
        for option in options_raw:
            if not isinstance(option, dict):
                raise ValueError("thinking_control option must be an object")
            value = option.get("value")
            label = option.get("label")
            if (
                not isinstance(value, str)
                or not value
                or not isinstance(label, str)
                or not label
                or value in seen
            ):
                raise ValueError("invalid thinking_control option")
            seen.add(value)
            options.append((value, label))
        default = raw.get("default")
        request_mode = raw.get("request_mode")
        kind = raw.get("kind")
        label = raw.get("label")
        if default not in seen:
            raise ValueError("thinking_control default must be an option")
        if request_mode not in _REQUEST_MODES:
            raise ValueError("unsupported thinking_control request_mode")
        if not isinstance(kind, str) or not kind:
            raise ValueError("thinking_control kind is required")
        if not isinstance(label, str) or not label:
            raise ValueError("thinking_control label is required")
        if not isinstance(raw.get("native"), bool):
            raise ValueError("thinking_control native must be boolean")
        aliases_raw = raw.get("aliases", {})
        if not isinstance(aliases_raw, dict) or not all(
            isinstance(alias, str)
            and alias
            and isinstance(target, str)
            and target in seen
            and alias not in seen
            for alias, target in aliases_raw.items()
        ):
            raise ValueError("invalid thinking_control aliases")
        return ModelControl(
            kind=kind,
            label=label,
            native=raw["native"],
            default=default,
            request_mode=request_mode,
            options=tuple(options),
            aliases=tuple(aliases_raw.items()),
        )

    @classmethod
    def _parse_model(cls, provider_id: str, raw: Any) -> CatalogModel:
        if not isinstance(raw, dict):
            raise ValueError("model row must be an object")
        model_id = raw.get("id")
        lifecycle = raw.get("lifecycle")
        if not isinstance(model_id, str) or not model_id:
            raise ValueError("model id must be non-empty")
        if lifecycle not in _LIFECYCLES:
            raise ValueError(f"invalid model lifecycle: {lifecycle!r}")
        evidence = raw.get("evidence", [])
        if not isinstance(evidence, list) or not all(
            isinstance(url, str) and url.startswith("https://")
            for url in evidence
        ):
            raise ValueError("model evidence must be a list of URLs")
        context_window = raw.get("context_window")
        max_output_tokens = raw.get("max_output_tokens")
        for name, value in (
            ("context_window", context_window),
            ("max_output_tokens", max_output_tokens),
        ):
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value <= 0
            ):
                raise ValueError(f"{name} must be a positive integer or null")
        disclosed_version = raw.get("disclosed_version")
        if disclosed_version is None:
            version = None
        elif isinstance(disclosed_version, str) and disclosed_version.strip():
            if not evidence:
                raise ValueError("disclosed_version requires evidence URLs")
            version = disclosed_version.strip()
        else:
            raise ValueError("disclosed_version must be a non-empty string or null")
        compatibility_of = raw.get("compatibility_of")
        compatibility_scope = raw.get("compatibility_scope")
        if compatibility_of is None and compatibility_scope is None:
            alias_of = None
            alias_scope = None
        elif (
            isinstance(compatibility_of, str)
            and compatibility_of.strip()
            and compatibility_scope in _COMPATIBILITY_SCOPES
        ):
            alias_of = compatibility_of.strip()
            alias_scope = str(compatibility_scope)
        else:
            raise ValueError("invalid compatibility alias fields")
        preferred = raw.get("preferred", False)
        if not isinstance(preferred, bool):
            raise ValueError("preferred must be boolean")
        return CatalogModel(
            provider_id=provider_id,
            id=model_id,
            display_name=str(raw.get("display_name") or model_id),
            lifecycle=lifecycle,
            context_window=context_window,
            max_output_tokens=max_output_tokens,
            persona_certification=str(
                raw.get("persona_certification") or "not_evaluated"
            ),
            thinking_control=cls._parse_control(raw.get("thinking_control")),
            evidence=tuple(evidence),
            disclosed_version=version,
            compatibility_of=alias_of,
            compatibility_scope=alias_scope,
            preferred=preferred,
        )

    def clear_discovery(self, provider_id: str | None = None) -> None:
        if provider_id is None:
            self._discovered.clear()
            self._discovered_metadata.clear()
        else:
            self._discovered.pop(provider_id, None)
            self._discovered_metadata.pop(provider_id, None)

    def _live_metadata(
        self,
        provider_id: str,
        model_id: str,
    ) -> dict[str, Any]:
        return dict(
            self._discovered_metadata.get(provider_id, {}).get(model_id, {})
        )

    @staticmethod
    def _live_display_name(live: dict[str, Any], model_id: str) -> str | None:
        for key in _LIVE_DISPLAY_KEYS:
            value = live.get(key)
            if isinstance(value, str) and value.strip() and value.strip() != model_id:
                return value.strip()
        return None

    @staticmethod
    def _live_positive_int(
        live: dict[str, Any],
        name: str,
        fallback: int | None,
    ) -> int | None:
        value = live.get(name)
        if (
            isinstance(value, int)
            and not isinstance(value, bool)
            and value > 0
        ):
            return value
        return fallback

    def _effective_lifecycle(self, model: CatalogModel) -> str:
        discovered = self._discovered.get(model.provider_id)
        if discovered is not None and model.id not in discovered:
            return "unavailable"
        return model.lifecycle

    def _allows_undiscovered(self, provider_id: str) -> bool:
        return (
            is_user_custom_provider(provider_id)
            or self._allow_undiscovered.get(provider_id, False)
        )

    def _is_callable(self, provider_id: str, lifecycle: str) -> bool:
        """Non-custom unknown/unverified models are never selectable."""
        if lifecycle in _BLOCKED_LIFECYCLES:
            return False
        if lifecycle == "unverified" and not self._allows_undiscovered(provider_id):
            return False
        return True

    def _public_row(
        self,
        *,
        provider_id: str,
        model_id: str,
        display_name: str,
        display_source: str,
        lifecycle: str,
        source: str,
        context_window: int | None,
        max_output_tokens: int | None,
        persona_certification: str,
        evidence: list[str],
        live: dict[str, Any],
        thinking_control: dict[str, Any] | None,
        disclosed_version: str | None = None,
        compatibility_of: str | None = None,
        compatibility_scope: str | None = None,
        preferred: bool = False,
    ) -> dict[str, Any]:
        return {
            "id": model_id,
            "request_id": model_id,
            "display_name": display_name,
            "display_source": display_source,
            "lifecycle": lifecycle,
            "callable": self._is_callable(provider_id, lifecycle),
            "source": source,
            "context_window": context_window,
            "max_output_tokens": max_output_tokens,
            "persona_certification": persona_certification,
            "evidence": evidence,
            "live_metadata": live,
            "thinking_control": thinking_control,
            "disclosed_version": disclosed_version,
            "compatibility_of": compatibility_of,
            "compatibility_scope": compatibility_scope,
            "preferred": preferred,
        }

    def _public_known(self, model: CatalogModel) -> dict[str, Any]:
        lifecycle = self._effective_lifecycle(model)
        live = self._live_metadata(model.provider_id, model.id)
        return self._public_row(
            provider_id=model.provider_id,
            model_id=model.id,
            display_name=model.display_name,
            display_source="manifest",
            lifecycle=lifecycle,
            source="manifest",
            context_window=self._live_positive_int(
                live,
                "context_window",
                model.context_window,
            ),
            max_output_tokens=self._live_positive_int(
                live,
                "max_output_tokens",
                model.max_output_tokens,
            ),
            persona_certification=model.persona_certification,
            evidence=list(model.evidence),
            live=live,
            thinking_control=(
                model.thinking_control.public()
                if model.thinking_control is not None
                else None
            ),
            disclosed_version=model.disclosed_version,
            compatibility_of=model.compatibility_of,
            compatibility_scope=model.compatibility_scope,
            preferred=model.preferred,
        )

    def _public_unknown(
        self,
        provider_id: str,
        model_id: str,
        *,
        available: bool = True,
        live_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        live = dict(live_metadata or {})
        lifecycle = "unverified" if available else "unavailable"
        live_name = self._live_display_name(live, model_id)
        return self._public_row(
            provider_id=provider_id,
            model_id=model_id,
            display_name=live_name or model_id,
            display_source="live_name" if live_name else "request_id",
            lifecycle=lifecycle,
            source="dynamic",
            context_window=self._live_positive_int(live, "context_window", None),
            max_output_tokens=self._live_positive_int(
                live, "max_output_tokens", None
            ),
            persona_certification="not_evaluated",
            evidence=[],
            live=live,
            thinking_control=None,
        )

    def public_model(self, provider_id: str, model_id: str) -> dict[str, Any]:
        known = self._models.get((provider_id, model_id))
        if known is not None:
            return self._public_known(known)
        discovered = self._discovered.get(provider_id)
        return self._public_unknown(
            provider_id,
            model_id,
            available=(
                model_id in discovered
                if discovered is not None
                else self._allows_undiscovered(provider_id)
            ),
            live_metadata=self._live_metadata(provider_id, model_id),
        )

    def list_provider(self, provider_id: str) -> list[dict[str, Any]]:
        rows = [
            self._public_known(self._models[(provider_id, model_id)])
            for model_id in self._providers.get(provider_id, ())
        ]
        discovered = self._discovered.get(provider_id, frozenset())
        known_ids = set(self._providers.get(provider_id, ()))
        rows.extend(
            self._public_unknown(
                provider_id,
                model_id,
                live_metadata=self._live_metadata(provider_id, model_id),
            )
            for model_id in sorted(discovered - known_ids)
        )
        return rows

    def reconcile(
        self,
        provider_id: str,
        discovered_models: Iterable[str],
        discovered_metadata: Iterable[dict[str, Any]] = (),
    ) -> list[dict[str, Any]]:
        discovered = frozenset(
            model_id.strip()
            for model_id in discovered_models
            if isinstance(model_id, str) and model_id.strip()
        )
        self._discovered[provider_id] = discovered
        metadata: dict[str, dict[str, Any]] = {}
        for raw in discovered_metadata:
            if not isinstance(raw, dict):
                continue
            model_id = str(raw.get("id") or "").strip()
            if model_id and model_id in discovered:
                metadata[model_id] = dict(raw)
        self._discovered_metadata[provider_id] = metadata
        return self.list_provider(provider_id)

    def preferred_id(self, provider_id: str) -> str | None:
        for model_id in self._providers.get(provider_id, ()):
            if self._models[(provider_id, model_id)].preferred:
                return model_id
        return None

    def uses_native_reasoning(self, provider_id: str, model_id: str) -> bool:
        control = self.control_for(provider_id, model_id)
        return (
            control is not None
            and control.native
            and control.request_mode == "reasoning_effort"
        )

    def control_for(self, provider_id: str, model_id: str) -> ModelControl | None:
        model = self._models.get((provider_id, model_id))
        return model.thinking_control if model is not None else None

    def normalize_control(
        self,
        provider_id: str,
        model_id: str,
        raw: Any,
    ) -> str | None:
        control = self.control_for(provider_id, model_id)
        if control is None:
            return None
        if isinstance(raw, str) and raw in control.values:
            return raw
        if isinstance(raw, str):
            alias_target = dict(control.aliases).get(raw)
            if alias_target is not None:
                return alias_target
        return control.default

    def ensure_callable(self, provider_id: str, model_id: str) -> None:
        model = self._models.get((provider_id, model_id))
        if model is None:
            # Built-in providers: unknown/unverified models never receive paid calls.
            if not self._allows_undiscovered(provider_id):
                raise ModelUnavailableError(
                    f"model {provider_id}/{model_id} is unavailable"
                )
            discovered = self._discovered.get(provider_id)
            unavailable = (
                model_id not in discovered
                if discovered is not None
                else False
            )
            if unavailable:
                raise ModelUnavailableError(
                    f"model {provider_id}/{model_id} is unavailable"
                )
            return
        lifecycle = self._effective_lifecycle(model)
        if not self._is_callable(provider_id, lifecycle):
            raise ModelUnavailableError(
                f"model {provider_id}/{model_id} is {lifecycle}"
            )


model_catalog = ModelCatalog.load(
    config.CHARACTERS_DIR / "model_capability_manifest_v1.json"
)
