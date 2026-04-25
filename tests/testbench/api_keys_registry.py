"""Read-only view of ``tests/api_keys.json`` for the Settings UI.

The existing tests read API keys from ``tests/api_keys.json`` (gitignored, user
supplies their own). The testbench UI wants to show "configured / missing"
statuses + a "Reload from disk" button *without ever leaking plaintext* into
responses or the browser.

All functions here return either booleans or provider-key metadata — the only
place plaintext flows is :func:`get_api_key_for_provider`, which is invoked
server-side when building a :class:`ChatOpenAI` request.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from tests.testbench.config import PROJECT_ROOT

logger = logging.getLogger("testbench.api_keys")


# ---------------------------------------------------------------------------
# Provider → api_keys.json field mapping.
#
# Mirrors ``tests/conftest.KEY_MAPPING`` (env vars) but keyed by the provider
# short code used in ``config/api_providers.json`` so the UI can do a quick
# ``providers[i].has_key = status[mapping[key]]``. Keep in sync if conftest
# adds a new provider.
# ---------------------------------------------------------------------------
PROVIDER_TO_KEY_FIELD: dict[str, str] = {
    "qwen": "assistApiKeyQwen",
    "qwen_intl": "assistApiKeyQwen",  # Intl uses same key slot as mainland.
    "openai": "assistApiKeyOpenai",
    "glm": "assistApiKeyGlm",
    "step": "assistApiKeyStep",
    "silicon": "assistApiKeySilicon",
    "gemini": "assistApiKeyGemini",
    "kimi": "assistApiKeyKimi",
}

# Canonical set of key fields the UI surfaces — any field in api_keys.json that
# is NOT in this set gets listed under "extra" so the user knows it's there
# but outside the testbench's expected schema.
KNOWN_KEY_FIELDS: tuple[str, ...] = tuple(sorted(set(PROVIDER_TO_KEY_FIELD.values())))


API_KEYS_PATH: Path = PROJECT_ROOT / "tests" / "api_keys.json"


class ApiKeysRegistry:
    """Process-wide cache of ``tests/api_keys.json`` contents.

    The registry is lazily loaded on first access and can be force-reloaded
    via :meth:`reload`. It is **not** an async helper — the file is small
    (single digit KB) and rarely touched; blocking reads are acceptable.
    """

    def __init__(self, path: Path = API_KEYS_PATH) -> None:
        self.path = path
        self._cache: dict[str, str] | None = None
        self._last_mtime: float | None = None
        self._load_error: str | None = None

    # ── load / reload ──────────────────────────────────────────────

    def _load(self) -> dict[str, str]:
        self._load_error = None
        if not self.path.exists():
            logger.info("api_keys.json missing at %s; returning empty registry.", self.path)
            self._cache = {}
            self._last_mtime = None
            return {}
        try:
            raw = self.path.read_text(encoding="utf-8")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("Top-level JSON must be an object")
            self._cache = {k: v for k, v in data.items() if isinstance(v, str)}
            self._last_mtime = self.path.stat().st_mtime
            logger.info("Loaded api_keys.json (%d fields).", len(self._cache))
            return self._cache
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            logger.warning("Failed to load api_keys.json: %s", exc)
            self._cache = {}
            self._last_mtime = None
            self._load_error = f"{type(exc).__name__}: {exc}"
            return {}

    def _ensure_loaded(self) -> dict[str, str]:
        if self._cache is None:
            self._load()
        return self._cache or {}

    def reload(self) -> dict[str, Any]:
        """Force re-read from disk. Returns a status dict for the UI."""
        self._cache = None
        self._ensure_loaded()
        return self.status_report()

    # ── read-only accessors ────────────────────────────────────────

    def is_present(self, field: str) -> bool:
        """True if ``field`` has a non-empty, non-placeholder value.

        Matches the heuristic used by ``llm_judger._init_llms``: treats
        ``sk-...`` / empty string as missing.
        """
        value = self._ensure_loaded().get(field, "")
        if not value or not isinstance(value, str):
            return False
        stripped = value.strip()
        if not stripped:
            return False
        # Common placeholder pattern from api_keys.json.template.
        if stripped.lower() in {"sk-...", "your-api-key"}:
            return False
        return True

    def get_api_key_for_provider(self, provider_key: str) -> str | None:
        """Look up the plaintext API key for a provider preset.

        Callers: server-side request building only. Never echo to clients.
        Returns ``None`` when the mapping or value is missing.
        """
        field = PROVIDER_TO_KEY_FIELD.get(provider_key)
        if not field:
            return None
        value = self._ensure_loaded().get(field, "")
        return value if self.is_present(field) else None

    def status_report(self) -> dict[str, Any]:
        """Serializable status blob for ``GET /api/config/api_keys_status``.

        Shape::

            {
              "path": "<abs path>",
              "exists": true/false,
              "last_mtime": 1730000000.0 | null,
              "load_error": null | "...",
              "known": {"assistApiKeyQwen": true, ...},
              "extra": ["someOtherField", ...],
              "provider_map": {"qwen": "assistApiKeyQwen", ...}
            }
        """
        data = self._ensure_loaded()
        known_status = {field: self.is_present(field) for field in KNOWN_KEY_FIELDS}
        extra = sorted(
            field for field in data.keys() if field not in KNOWN_KEY_FIELDS
        )
        return {
            "path": str(self.path),
            "exists": self.path.exists(),
            "last_mtime": self._last_mtime,
            "load_error": self._load_error,
            "known": known_status,
            "extra": extra,
            "provider_map": dict(PROVIDER_TO_KEY_FIELD),
        }


# ── module-level helpers (not tied to the api_keys.json registry) ───
#
# These read ``config/api_providers.json`` via ``utils.api_config_loader``
# and intentionally live *outside* :class:`ApiKeysRegistry` — the registry's
# contract is "mirror tests/api_keys.json"; preset-bundled keys come from a
# different file and shouldn't inherit the registry's cache/reload cycle.


def get_preset_bundled_api_key(provider_key: str) -> str | None:
    """Return the api_key that ships INSIDE ``config/api_providers.json`` for
    an assist-tier preset, if any.

    Rationale: some presets carry their own canonical api_key string that
    the backend (or the vendor) recognizes as a "no-auth" token. The
    ``free`` preset is the canonical case — it ships
    ``openrouter_api_key: "free-access"``, and upstream
    ``utils/config_manager.py`` treats this literal as the free-tier marker.
    Testers who pick this preset should not be forced to type a key.

    We also defensively check ``audio_api_key`` as a secondary fallback
    (the realtime / TTS path uses it). Returns ``None`` if neither key is
    present on the preset or if the loader cannot be imported.

    Never called during module import so ``api_config_loader`` stays lazy;
    the registry itself does not depend on ``config/api_providers.json``.
    """
    if not provider_key:
        return None
    try:
        from utils.api_config_loader import get_config
    except Exception:
        # Shouldn't happen outside bootstrap, but don't crash the request
        # path just because api_providers.json can't be parsed.
        return None
    try:
        raw = get_config()
    except Exception:
        return None
    assist = (raw or {}).get("assist_api_providers", {}) or {}
    profile = assist.get(provider_key) or {}
    # ``openrouter_api_key`` is the assist-tier field the chat backend would
    # actually use; ``audio_api_key`` is a belt-and-braces fallback (same
    # string for the "free" preset today).
    for field_name in ("openrouter_api_key", "audio_api_key"):
        candidate = profile.get(field_name)
        if isinstance(candidate, str):
            stripped = candidate.strip()
            if stripped:
                return stripped
    return None


def preset_has_bundled_api_key(provider_key: str) -> bool:
    """Cheap boolean view for UI — does this preset ship its own api_key?

    Used by ``GET /api/config/providers`` so the UI can render
    "此预设内置 API Key, 无需填写" instead of the default "未配置" status.
    """
    return bool(get_preset_bundled_api_key(provider_key))


# ── module-level singleton ──────────────────────────────────────────

_registry: ApiKeysRegistry | None = None


def get_api_keys_registry() -> ApiKeysRegistry:
    global _registry
    if _registry is None:
        _registry = ApiKeysRegistry()
    return _registry


__all__ = [
    "API_KEYS_PATH",
    "ApiKeysRegistry",
    "KNOWN_KEY_FIELDS",
    "PROVIDER_TO_KEY_FIELD",
    "get_api_keys_registry",
    "get_preset_bundled_api_key",
    "preset_has_bundled_api_key",
]
