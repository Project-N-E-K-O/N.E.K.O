"""Provider-agnostic registry for core API native voice catalogs.

Each `core_api_type` that ships built-in TTS voices (e.g. Gemini's Puck/Leda,
future OpenAI/Qwen native voices) registers a `NativeVoiceProvider` here.
Cross-cutting code (config validation, character UI, TTS worker dispatch,
realtime voice routing) consults the registry instead of hard-coding
`if core_api_type == 'gemini'` branches, so adding a second provider is one
adapter file plus one worker registration — not edits in five places.

Two-phase registration avoids circular imports:
  1. The provider metadata module (e.g. `utils/gemini_tts_voices.py`) creates
     and registers a `NativeVoiceProvider` at import time — pure data.
  2. The TTS worker module (`main_logic/tts_client.py`) registers the worker
     callable + api-key resolver after defining the worker, since the worker
     pulls in heavy deps (httpx, soxr, etc.) the metadata module must not.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from utils.config_manager import ConfigManager


VoiceIdExists = Callable[[str], bool]
TTSWorkerResolver = Callable[["ConfigManager"], "tuple[Callable[..., Any], str]"]


@dataclass(frozen=True)
class NativeVoiceProvider:
    """Metadata for one core API's built-in TTS voice catalog.

    `key` matches the `core_api_type` / realtime `api_type` string used
    elsewhere in the codebase (e.g. "gemini"). `catalog` maps canonical voice
    names (case-sensitive as the upstream API expects) to a gender label;
    `aliases` maps casefolded user-friendly inputs (e.g. "中文男", "female")
    to canonical voice names so users can type either form.
    """

    key: str
    catalog: Mapping[str, str]
    aliases: Mapping[str, str]
    default_voice: str
    default_male_voice: str
    catalog_prefix: str
    _voice_lookup: dict[str, str] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "_voice_lookup",
            {name.casefold(): name for name in self.catalog},
        )

    def normalize(self, voice_id: str | None) -> tuple[str, bool]:
        """Return (canonical_voice_name, recognized).

        Empty / whitespace input is treated as unrecognized so callers can
        tell "user explicitly chose a native voice" apart from "we picked the
        default."
        """
        normalized = (voice_id or "").strip()
        if not normalized:
            return self.default_voice, False

        exact = self._voice_lookup.get(normalized.casefold())
        if exact:
            return exact, True

        alias = self.aliases.get(normalized.casefold())
        if alias:
            return alias, True

        return self.default_voice, False

    def is_voice(self, voice_id: str | None) -> bool:
        return self.normalize(voice_id)[1]

    def voice_catalog_for_ui(self) -> dict[str, dict[str, str | bool]]:
        """Return the voice list in the shape the character UI expects."""
        return {
            voice_name: {
                "prefix": f"{self.catalog_prefix} {voice_name} ({gender})",
                "provider": self.key,
                "gender": gender,
                "builtin": True,
            }
            for voice_name, gender in self.catalog.items()
        }

    def resolve_for_routing(
        self,
        voice_id: str | None,
        voice_id_exists: VoiceIdExists | None = None,
    ) -> tuple[str, bool]:
        """Return (voice, use_native).

        When the input is not in this provider's catalog, the returned
        `voice` is the caller's stripped input verbatim — symmetric with
        the module-level `resolve_native_voice_for_routing` helper (which
        returns the input as-is when no native provider matches the
        `core_api_type`). That keeps the "use_native=False" contract
        consistent: callers that inspect `voice` in the False branch see
        the original id, not a fallback default they never asked for.

        Collision branch behaves differently on purpose: when the input
        canonicalizes to a voice the user has cloned, we return the
        canonical name with use_native=False, so callers can route to that
        clone by canonical id (e.g. user typed alias "中文男", clone is
        stored as "Puck" — caller wants "Puck", not "中文男").
        """
        normalized_voice, recognized = self.normalize(voice_id)
        if not recognized:
            return (voice_id or "").strip(), False

        if voice_id_exists is None:
            return normalized_voice, True

        candidates = {voice_id, normalized_voice}
        has_collision = any(
            voice_id_exists(candidate)
            for candidate in candidates
            if candidate
        )
        return normalized_voice, not has_collision


_PROVIDERS: dict[str, NativeVoiceProvider] = {}
_TTS_WORKER_RESOLVERS: dict[str, TTSWorkerResolver] = {}


def register_provider(provider: NativeVoiceProvider) -> None:
    """Register a provider's voice catalog. Idempotent: re-registering the
    same key replaces the previous entry (useful for tests / hot-reload)."""
    _PROVIDERS[provider.key] = provider


def register_tts_worker_resolver(
    provider_key: str,
    resolver: TTSWorkerResolver,
) -> None:
    """Register the TTS worker callable + api-key resolver for a provider.

    The resolver is invoked with the active ConfigManager and returns
    (worker_callable, api_key) — `get_native_tts_worker` packages this with
    the provider key for the dispatcher.
    """
    _TTS_WORKER_RESOLVERS[provider_key] = resolver


def get_provider(key: str | None) -> NativeVoiceProvider | None:
    if not key:
        return None
    return _PROVIDERS.get(key)


def list_providers() -> list[str]:
    return list(_PROVIDERS.keys())


def is_native_voice(
    voice_id: str | None,
    provider_key: str | None = None,
) -> bool:
    """Check catalog membership.

    With `provider_key`, check that provider only. Without, check whether the
    voice belongs to *any* registered provider (used by validators that don't
    know which provider the voice came from).
    """
    if provider_key is not None:
        provider = _PROVIDERS.get(provider_key)
        return bool(provider and provider.is_voice(voice_id))
    return any(provider.is_voice(voice_id) for provider in _PROVIDERS.values())


def normalize_native_voice(
    provider_key: str,
    voice_id: str | None,
) -> tuple[str, bool]:
    """Normalize through a specific provider. Raises KeyError if unknown."""
    return _PROVIDERS[provider_key].normalize(voice_id)


def get_native_voice_catalog_for_ui(
    provider_key: str | None,
) -> dict[str, dict[str, str | bool]] | None:
    provider = get_provider(provider_key)
    if provider is None:
        return None
    return provider.voice_catalog_for_ui()


def resolve_native_voice_for_routing(
    core_api_type: str | None,
    voice_id: str | None,
    voice_id_exists: VoiceIdExists | None = None,
) -> tuple[str, bool]:
    """Look up provider by core_api_type, then delegate to its resolver.

    Returns (voice_or_input, use_native). When core_api_type isn't a
    registered native-voice provider, returns the stripped input verbatim
    with use_native=False so callers fall through to custom TTS routing.
    """
    provider = get_provider(core_api_type)
    if provider is None:
        return (voice_id or "").strip(), False
    return provider.resolve_for_routing(voice_id, voice_id_exists)


def get_active_realtime_native_provider(cm: "ConfigManager") -> str | None:
    """Return provider key when the active realtime API ships native voices.

    Falls back to core_api_type when realtime config is unavailable, matching
    the behavior of the previous `is_gemini_realtime_api_active` helper.
    Returns None when neither realtime nor core api type is a registered
    native-voice provider.
    """
    try:
        realtime_config = cm.get_model_api_config('realtime')
        api_type = realtime_config.get('api_type')
    except Exception:
        api_type = (cm.get_core_config() or {}).get('CORE_API_TYPE')
    return api_type if api_type in _PROVIDERS else None


_BUILTIN_PROVIDER_MODULES: tuple[str, ...] = (
    "utils.gemini_tts_voices",
    "utils.grok_tts_voices",
)


def ensure_builtin_native_voice_providers_loaded() -> None:
    """Force-import built-in provider adapters so their `register_provider`
    side effects fire before any registry query.

    Called once when this module is imported (see bottom of file). The reason
    auto-bootstrap lives here, not in cross-cutting callers: a callsite that
    runs before any TTS/realtime client has imported a provider module would
    otherwise query an empty registry, and the failure mode (silent
    fall-through to external TTS) is non-obvious.

    To add a new built-in provider: write the adapter module (it must call
    `register_provider(...)` at import time) and append its dotted name to
    `_BUILTIN_PROVIDER_MODULES`. No edits in `config_manager` / `core` /
    `characters_router` / `tts_client` are required for the metadata side.
    """
    import importlib

    for module_name in _BUILTIN_PROVIDER_MODULES:
        importlib.import_module(module_name)


def get_native_tts_worker(
    core_api_type: str | None,
    cm: "ConfigManager",
    voice_id: str | None,
) -> tuple[Callable[..., Any], str, str] | None:
    """Resolve (worker, api_key, provider_key) when the user has selected a
    native voice for an active native-voice provider, else None.

    Used by `tts_client.get_tts_worker` to short-circuit the worker dispatch
    when the user explicitly picked a built-in voice (e.g. Gemini "Puck"):
    we must use the provider's native worker even if no voice clone exists,
    otherwise the fallthrough would route to GPT-SoVITS or local CosyVoice
    with the wrong api key.
    """
    if not core_api_type:
        return None
    provider = _PROVIDERS.get(core_api_type)
    if provider is None or not provider.is_voice(voice_id):
        return None
    resolver = _TTS_WORKER_RESOLVERS.get(core_api_type)
    if resolver is None:
        return None
    worker, api_key = resolver(cm)
    return worker, api_key, core_api_type


# Auto-bootstrap on module import: any consumer of this registry gets a
# populated provider list without each cross-cutting file having to remember a
# `from utils import gemini_tts_voices  # noqa: F401` side-effect import.
# Trades a one-line coupling (registry knows the dotted module names of its
# built-in providers via `_BUILTIN_PROVIDER_MODULES`) for "no caller can forget
# to bootstrap." Adapter modules import this registry to call
# `register_provider`, so by the time we reach this line the registry's public
# API is fully defined and the circular import resolves cleanly.
ensure_builtin_native_voice_providers_loaded()
