# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Cross-provider native voice registry.

core_api_types with built-in TTS voices (e.g. Gemini, StepFun, and possibly future
OpenAI/Qwen native voices) register a NativeVoiceProvider here.
Config validation, the character UI, TTS worker dispatch and realtime voice routing
all query this registry, avoiding hardcoded core_api_type checks scattered everywhere.

Registration has two layers, avoiding circular imports:
  1. Provider metadata modules only create and register a NativeVoiceProvider at import time.
  2. TTS worker modules register workers and auth-resolution functions after the workers
     are defined, so metadata modules don't pull in heavy deps like httpx and soxr early.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlparse

if TYPE_CHECKING:
    from utils.config_manager import ConfigManager


VoiceIdExists = Callable[[str], bool]
TTSWorkerResolver = Callable[["ConfigManager"], "tuple[Callable[..., Any], str]"]


@dataclass(frozen=True)
class NativeVoiceProvider:
    """Metadata of a single core API's built-in TTS voice catalog.

    key corresponds to core_api_type / realtime api_type in code. Catalog keys are the
    canonical voice names accepted by the upstream API; values serve as supplementary
    labels by default; aliases map user-friendly input back to canonical voice names.
    """

    key: str
    catalog: Mapping[str, str]
    aliases: Mapping[str, str]
    default_voice: str
    default_male_voice: str
    catalog_prefix: str
    catalog_value_is_display_name: bool = False
    _voice_lookup: dict[str, str] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "_voice_lookup",
            {name.casefold(): name for name in self.catalog},
        )

    def normalize(self, voice_id: str | None) -> tuple[str, bool]:
        """Return (canonical voice name, recognized).

        Empty values count as unrecognized, helping callers distinguish "the user
        explicitly chose a native voice" from "the system uses the default".
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
        """Return the voice list structure needed by the character UI."""
        def format_prefix(voice_name: str, group: str, display_name: str) -> str:
            if self.catalog_value_is_display_name:
                return display_name
            return f"{self.catalog_prefix} {display_name} ({group})"

        def split_catalog_value(voice_name: str, value: str) -> tuple[str, str]:
            if self.catalog_value_is_display_name:
                return "", value or voice_name
            return value, voice_name

        catalog_for_ui: dict[str, dict[str, str | bool]] = {}
        for voice_name, catalog_value in self.catalog.items():
            gender, display_name = split_catalog_value(voice_name, catalog_value)
            catalog_for_ui[voice_name] = {
                "prefix": format_prefix(voice_name, gender, display_name),
                "provider": self.key,
                "provider_label": self.catalog_prefix,
                "gender": gender,
                "display_name": display_name,
                "builtin": True,
            }
        return catalog_for_ui

    def resolve_for_routing(
        self,
        voice_id: str | None,
        voice_id_exists: VoiceIdExists | None = None,
    ) -> tuple[str, bool]:
        """Return (voice, whether to use the native voice).

        When the input doesn't hit the current provider's catalog, the stripped raw
        input is returned, so user-defined custom voices aren't silently replaced
        with the default native voice.

        If the canonical voice name collides with a user-cloned voice, the canonical
        name is returned but native routing is disabled, letting the caller treat it
        as a custom voice.
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


NativeTTSApiKeySource = Literal['core_api_key', 'tts_default_api_key']


def make_native_tts_resolver(
    worker: Callable[..., Any],
    api_key_source: NativeTTSApiKeySource,
    *,
    worker_kwargs: Mapping[str, Any] | None = None,
) -> TTSWorkerResolver:
    """Build a `register_tts_worker_resolver`-compatible resolver from the
    two axes shared by every native-voice provider so far:

    * `worker` — the TTS worker callable to dispatch to.
    * `api_key_source` — where to read the api key from on the active
      `ConfigManager`:
        - ``'core_api_key'`` for providers whose native voices bill against
          the same key as the realtime/LLM endpoint (Gemini, Grok).
        - ``'tts_default_api_key'`` for providers using the TTS slot the
          user configured separately (StepFun, free-mode lanlan TTS).
    * `worker_kwargs` (optional) — bound via `partial` for variants that
      share a worker but flip a mode flag (e.g. `free_mode=True` for the
      free-tier StepFun route).

    Future providers whose api key sourcing falls outside these two
    branches can register a hand-written resolver directly via
    `register_tts_worker_resolver`; adding a third literal here is also
    fine when the new source generalizes.
    """
    def resolver(cm: "ConfigManager") -> "tuple[Callable[..., Any], str]":
        if api_key_source == 'core_api_key':
            api_key = (cm.get_core_config() or {}).get('CORE_API_KEY', '')
        elif api_key_source == 'tts_default_api_key':
            api_key = cm.get_model_api_config('tts_default').get('api_key', '')
        else:
            raise ValueError(f"unknown api_key_source: {api_key_source!r}")
        bound = partial(worker, **dict(worker_kwargs)) if worker_kwargs else worker
        return bound, api_key

    return resolver


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


def is_free_lanlan_app_route(
    core_api_type: str | None,
    realtime_base_url: str | None,
) -> bool:
    """Whether this is the overseas free route (core_api_type='free' with host in the lanlan.app domain).

    The overseas free upstream is lanlan.app's Gemini proxy; available voices are the
    full Gemini set + the branded yui, so the "effective native voice provider" of
    this route is not the StepFun-based 'free' but 'free_intl' (see
    _effective_native_provider_key). Host checks are centralized here instead of
    leaking into cross-cutting files.
    """
    raw_url = str(realtime_base_url or "").strip()
    parsed = urlparse(raw_url if "://" in raw_url else f"//{raw_url}")
    hostname = (parsed.hostname or "").lower()
    return bool(
        str(core_api_type or "").lower() == "free"
        and (hostname == "lanlan.app" or hostname.endswith(".lanlan.app"))
    )


def _effective_native_provider_key(
    core_api_type: str | None,
    realtime_base_url: str | None,
) -> str | None:
    """Normalize (core_api_type, host) into the native voice provider key actually used for lookups.

    The only host-dependent divergence: overseas free (free + *.lanlan.app) → 'free_intl'
    (full Gemini + yui). In all other cases provider key == core_api_type. The remap is
    centralized inside the registry; cross-cutting callers just pass base_url through
    instead of doing their own if host == ... checks.
    """
    if is_free_lanlan_app_route(core_api_type, realtime_base_url):
        return "free_intl"
    return core_api_type


def resolve_native_voice_for_routing(
    core_api_type: str | None,
    voice_id: str | None,
    voice_id_exists: VoiceIdExists | None = None,
    realtime_base_url: str | None = None,
) -> tuple[str, bool]:
    """Look up provider by core_api_type, then delegate to its resolver.

    Returns (voice_or_input, use_native). When core_api_type isn't a
    registered native-voice provider, returns the stripped input verbatim
    with use_native=False so callers fall through to custom TTS routing.

    When realtime_base_url is passed, the overseas free route (free + *.lanlan.app)
    is remapped to 'free_intl' (full Gemini + yui), so yui / Gemini voices are
    recognized as native on that route; without it the lookup uses core_api_type
    as-is (backward compatible with old callers and non-free routes).
    """
    provider_key = _effective_native_provider_key(core_api_type, realtime_base_url)
    provider = get_provider(provider_key)
    if provider is None:
        return (voice_id or "").strip(), False
    return provider.resolve_for_routing(voice_id, voice_id_exists)


def is_free_preset_voice_id(voice_id: str | None) -> bool:
    """Whether voice_id belongs to the free_voices list of api_providers.json."""
    from utils.api_config_loader import get_free_voices  # 延迟导入避免循环

    voice = (voice_id or "").strip()
    if not voice:
        return False
    return voice in set(get_free_voices().values())


def _read_realtime_api_type(cm: "ConfigManager") -> str | None:
    try:
        return cm.get_model_api_config('realtime').get('api_type')
    except Exception:
        return (cm.get_core_config() or {}).get('CORE_API_TYPE')


def _read_realtime_base_url(cm: "ConfigManager") -> str:
    base_url = ""
    try:
        base_url = str(cm.get_model_api_config('realtime').get('base_url') or '')
    except Exception:
        base_url = ""
    if not base_url:
        try:
            base_url = str((cm.get_core_config() or {}).get('CORE_URL') or '')
        except Exception:
            base_url = ""
    return base_url


def _gptsovits_tts_overrides_native_tts_for_ui(
    cm: "ConfigManager",
    core_config: Mapping[str, Any],
) -> bool:
    if not core_config.get('GPTSOVITS_ENABLED', False):
        return False
    try:
        tts_config = cm.get_model_api_config('tts_custom') or {}
    except Exception:
        return False
    return bool(tts_config.get('is_custom'))


def _read_tts_native_provider_for_ui(cm: "ConfigManager") -> str | None:
    try:
        core_config = cm.get_core_config() or {}
    except Exception:
        core_config = {}

    if _gptsovits_tts_overrides_native_tts_for_ui(cm, core_config):
        return None

    # MiMo（assistApi=mimo / TTS_PROVIDER=mimo）不再走这里：它是 hosted provider，
    # 预制目录由 tts_provider_registry 的 preset_catalog 提供，/voices 与校验改查
    # 注册表（见设计文档 §4，MiMo 归 hosted）。此处只认仍属 native 的 TTS_PROVIDER。
    tts_provider = str(
        core_config.get('TTS_PROVIDER') or core_config.get('ttsProvider') or ''
    ).strip().lower()
    if tts_provider in _PROVIDERS:
        return tts_provider

    return None


def get_active_realtime_native_provider(cm: "ConfigManager") -> str | None:
    """Return the native voice provider key registered for the current realtime API (route-agnostic).

    Only checks whether api_type maps to a registered provider, with no host remapping —
    under overseas free, api_type is still 'free'. The validate / cleanup chain uses
    this route-agnostic version plus `is_saveable_native_voice`'s free_intl candidates,
    recognizing overseas Gemini/yui voices while staying lenient enough that switching
    routes doesn't wrongly purge Step native voices.
    """
    api_type = _read_realtime_api_type(cm)
    return api_type if api_type in _PROVIDERS else None


def is_saveable_native_voice(cm: "ConfigManager", voice_id: str | None) -> bool:
    """Whether voice_id is a savable native voice on the current route.

    Candidate providers = registered api_type (route-agnostic, so switching routes
    doesn't wrongly purge Step native voices saved in characters.json) ∪ the effective
    provider after host remapping (overseas free adds free_intl's full Gemini + yui).
    Hitting either counts as valid.
    """
    api_type = _read_realtime_api_type(cm)
    base_url = _read_realtime_base_url(cm)
    candidates = {
        api_type,
        _effective_native_provider_key(api_type, base_url),
        _read_tts_native_provider_for_ui(cm),
    }
    return any(is_native_voice(voice_id, key) for key in candidates if key)


def get_active_realtime_native_provider_for_ui(cm: "ConfigManager") -> str | None:
    """Return the effective provider key that the /voices endpoint and native voice previews should display.

    Difference from the route-agnostic version: this one does the host remap — overseas
    free (free + *.lanlan.app) displays 'free_intl' (full Gemini + yui), domestic free
    displays 'free' (StepFun native). The UI only exposes the voice catalog actually
    usable on the route.
    """
    tts_provider = _read_tts_native_provider_for_ui(cm)
    if tts_provider:
        return tts_provider

    api_type = _read_realtime_api_type(cm)
    base_url = _read_realtime_base_url(cm)
    key = _effective_native_provider_key(api_type, base_url)
    return key if key in _PROVIDERS else None


_BUILTIN_PROVIDER_MODULES: tuple[str, ...] = (
    "utils.tts.providers.gemini",
    "utils.tts.providers.stepfun",
    "utils.tts.providers.grok",
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
    # host 重映射：海外免费（free + *.lanlan.app）的 yui/Gemini 音色走 free_intl
    # worker（Gemini 代理），否则 provider key == core_api_type。
    provider_key = _effective_native_provider_key(core_api_type, _read_realtime_base_url(cm))
    provider = _PROVIDERS.get(provider_key)
    if provider is None or not provider.is_voice(voice_id):
        return None
    resolver = _TTS_WORKER_RESOLVERS.get(provider_key)
    if resolver is None:
        return None
    worker, api_key = resolver(cm)
    return worker, api_key, provider_key


# Auto-bootstrap on module import: any consumer of this registry gets a
# populated provider list without each cross-cutting file having to remember a
# `from utils import gemini_tts_voices  # noqa: F401` side-effect import.
# Trades a one-line coupling (registry knows the dotted module names of its
# built-in providers via `_BUILTIN_PROVIDER_MODULES`) for "no caller can forget
# to bootstrap." Adapter modules import this registry to call
# `register_provider`, so by the time we reach this line the registry's public
# API is fully defined and the circular import resolves cleanly.
ensure_builtin_native_voice_providers_loaded()
