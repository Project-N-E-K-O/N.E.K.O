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

"""Unified TTS provider registry.

One declarative entry per dispatchable TTS provider, organized along two
orthogonal axes (see docs/design/tts-voice-source-unification.md):

* **kind** — where the engine lives: ``local`` (a TTS endpoint the user runs
  themselves, e.g. vLLM-Omni, GPT-SoVITS) or ``hosted`` (a SaaS with its own
  voice system, e.g. CosyVoice, MiniMax, ElevenLabs, MiMo). Core-native voices
  (Gemini/Step/Grok/free) still live in :mod:`utils.tts.native_voice_registry` and
  are folded in later.
* **capabilities** — which *voice sources* the provider actually supports, any
  combination of ``preset`` (official built-in voices) / ``clone`` (user-cloned
  voices) / ``design`` (text-described, generated). The three stack on a
  single provider entry, declared per real capability — this is the organizing
  principle of the refactor, not a per-provider special-case. e.g. ElevenLabs =
  {clone, design}, CosyVoice = {clone, design}, GPT-SoVITS = {clone},
  vLLM-Omni = {preset}.

A provider declares how it is selected (``is_selected``) and how it resolves to
a worker (``resolve``), plus declarative UI / probe metadata so the settings
frontend and the connectivity probe derive from this single source of truth
instead of restating each provider inline.

Layering mirrors ``native_voice_registry`` to avoid circular imports: metadata
lives here; the worker callables (which pull heavy deps like ``soxr`` /
``websockets``) are bound by ``main_logic.tts_client`` after the workers are
defined, via :func:`register`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from utils.logger_config import get_module_logger

if TYPE_CHECKING:
    from utils.config_manager import ConfigManager

logger = get_module_logger(__name__, "Main")


# A voice source — where the actual voice identity comes from. Providers stack
# any subset of these (declared by real capability).
VoiceSource = Literal["preset", "clone", "design"]

# Where the synthesis engine lives. Core-native providers are not in this
# registry yet (see module docstring); kept out of the Literal until folded in.
ProviderKind = Literal["local", "hosted"]

_VOICE_META_UNSET = object()


@dataclass
class DispatchContext:
    """Everything a provider needs to decide selection / resolve a worker.

    Unifies the two selection mechanisms (see design doc §3.1):

    * config-selected providers (vLLM-Omni / GPT-SoVITS) read ``core_config`` /
      ``cm`` only;
    * voice-metadata-selected providers (the clone families — MiniMax /
      ElevenLabs / CosyVoice) read ``voice_meta`` (the picked cloned voice's
      stored metadata), plus ``voice_id`` / ``has_custom_voice``.

    ``get_tts_worker`` builds this once and hands it to every provider's
    ``is_selected`` / ``resolve``; providers ignore the fields they don't need.

    ``voice_meta`` is **lazy**: it is loaded (and cached) only on first access via
    ``voice_meta_loader``, so config-selected providers — checked first by
    priority — can win without ever touching voice storage. This preserves the
    contract that an explicit provider pick short-circuits before any voice
    metadata lookup (see test_get_tts_worker_routes_explicit_vllm_before_cloned_voice).
    """

    core_config: Mapping[str, Any]
    cm: "ConfigManager"
    voice_id: str = ""
    has_custom_voice: bool = False
    # Injected by the caller (get_tts_worker) — typically ``lambda: _get_voice_meta(voice_id)``.
    # The registry can't import _get_voice_meta (it lives in tts_client) without a
    # circular import, so the loader is passed in.
    voice_meta_loader: "Callable[[], dict | None] | None" = None
    _voice_meta_cache: Any = field(default=_VOICE_META_UNSET, init=False, repr=False, compare=False)

    @property
    def voice_meta(self) -> "dict | None":
        """The picked cloned voice's metadata, or None when there's no custom
        voice / no local metadata. Computed once on first access, then cached."""
        if self._voice_meta_cache is _VOICE_META_UNSET:
            result = None
            if self.voice_meta_loader and self.has_custom_voice and self.voice_id:
                try:
                    result = self.voice_meta_loader()
                except Exception:
                    logger.warning(
                        "voice_meta_loader 失败 (voice_id=%s)，按无元数据处理",
                        self.voice_id, exc_info=True,
                    )
                    result = None
            self._voice_meta_cache = result
        return self._voice_meta_cache


SelectPredicate = Callable[["DispatchContext"], bool]
DispatchResolver = Callable[
    ["DispatchContext"],
    "tuple[Callable[..., Any], str | None, str]",
]

# How the settings page probes this provider's endpoint for liveness.
#   'ws_handshake' — open the WebSocket and watch the handshake for auth errors
#                    (vLLM-Omni). 'local_http' — hit the local service over HTTP
#                    (GPT-SoVITS). 'none' — no preflight probe.
ProbeKind = Literal["ws_handshake", "local_http", "http_tts", "none"]


@dataclass(frozen=True)
class PresetCatalog:
    """A static, declarative built-in (preset) voice catalog for a provider.

    Dual to :class:`utils.tts.native_voice_registry.NativeVoiceProvider`'s catalog
    half, but lives on the unified :class:`TTSProvider` so a SaaS provider that
    ships its own voice set (e.g. MiMo) can advertise its presets without being
    mis-registered as a core-native provider (see design doc §3/§4: ``mimo`` is
    ``hosted``, not ``native``). The shape returned by :meth:`catalog_for_ui`
    intentionally mirrors ``NativeVoiceProvider.voice_catalog_for_ui`` so the
    ``/voices`` endpoint and the source-first picker consume one structure
    regardless of whether the catalog came from native or hosted.

    ``catalog`` maps a canonical voice name to a supplementary label (a gender by
    default, or the display name itself when ``catalog_value_is_display_name``);
    ``aliases`` maps casefolded user-friendly input back to canonical names. The
    catalog is static data — dynamic ``/voices``-style fetching (e.g. GPT-SoVITS)
    is a future variant declared on the same field, not a special-case here.
    """

    catalog: Mapping[str, str]
    aliases: Mapping[str, str] = field(default_factory=dict)
    default_voice: str = ""
    catalog_prefix: str = ""
    catalog_value_is_display_name: bool = False
    _voice_lookup: dict[str, str] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "_voice_lookup",
            {name.casefold(): name for name in self.catalog},
        )

    def normalize(self, voice_id: str | None) -> tuple[str, bool]:
        """Return (canonical voice name, recognized). Empty input is unrecognized."""
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

    def catalog_for_ui(self, provider_key: str) -> dict[str, dict[str, str | bool]]:
        """Voice list structure for the character UI, keyed by canonical voice name.

        ``provider_key`` is the owning :class:`TTSProvider` key, stamped into each
        entry's ``provider`` field so the source-first picker groups it under
        that provider's preset source.
        """
        def format_prefix(group: str, display_name: str) -> str:
            if self.catalog_value_is_display_name:
                return display_name
            return f"{self.catalog_prefix} {display_name} ({group})"

        def split_value(voice_name: str, value: str) -> tuple[str, str]:
            if self.catalog_value_is_display_name:
                return "", value or voice_name
            return value, voice_name

        catalog_for_ui: dict[str, dict[str, str | bool]] = {}
        for voice_name, catalog_value in self.catalog.items():
            gender, display_name = split_value(voice_name, catalog_value)
            catalog_for_ui[voice_name] = {
                "prefix": format_prefix(gender, display_name),
                "provider": provider_key,
                "provider_label": self.catalog_prefix,
                "gender": gender,
                "display_name": display_name,
                "builtin": True,
            }
        return catalog_for_ui


@dataclass(frozen=True)
class VoiceDesignMetadata:
    """Documented upstream constraints for a provider's Voice Design API.

    ``None`` and an empty tuple mean the upstream documentation does not impose
    that constraint. Language hints use NEKO's request-language values (for
    example ``ch``), which provider adapters translate to upstream values.
    """

    prompt_min: int | None = None
    prompt_max: int | None = None
    prefix_max: int | None = None
    prefix_pattern: str = ""
    language_hints: tuple[str, ...] = ()

    def for_ui(self) -> dict[str, Any]:
        return {
            "prompt_min": self.prompt_min,
            "prompt_max": self.prompt_max,
            "prefix_max": self.prefix_max,
            "prefix_pattern": self.prefix_pattern,
            "language_hints": list(self.language_hints),
        }


@dataclass(frozen=True)
class TTSProvider:
    """One dispatchable TTS provider, declared in a single place.

    ``priority`` orders evaluation in the dispatcher: lower runs first (mirrors
    the original hand-written precedence in ``get_tts_worker`` — GPT-SoVITS
    ahead of vLLM-Omni, both ahead of clone-voice routing).

    ``is_selected`` / ``resolve`` carry provider-specific selection/dispatch.
    They stay callables (rather than further declarative fields) because real
    providers diverge in *how* they are selected (an explicit dropdown choice vs
    an enable flag vs a cloned voice's metadata). The declarative fields capture
    the axes that genuinely are shared — capabilities, the settings UI, the probe.
    """

    key: str
    kind: ProviderKind
    priority: int
    # Which voice sources this provider supports — preset / clone / design stack
    # here per real capability.
    capabilities: frozenset[VoiceSource]
    is_selected: SelectPredicate
    resolve: DispatchResolver

    # Static built-in (preset) voice catalog, when this provider ships one (e.g.
    # MiMo). None for providers whose presets are user-entered ids against a
    # configured endpoint (vLLM-Omni) or that have no preset source at all. This
    # is the single source of truth for the provider's preset voices — the UI
    # ``/voices`` endpoint and ``validate_voice_id`` query it instead of restating
    # the catalog elsewhere (see design doc §3 ``preset_catalog``).
    preset_catalog: "PresetCatalog | None" = None

    # Alternate provider ids that share this provider's implementation and
    # capabilities while retaining provider-specific runtime configuration.
    aliases: frozenset[str] = frozenset()
    # Upstream-enforced Voice Design constraints. Design-capable providers use
    # an empty VoiceDesignMetadata when the API documents no hard limits.
    voice_design: VoiceDesignMetadata | None = None

    # ── Declarative UI / probe metadata (single source of truth for frontend) ──
    # Whether this provider appears only in the TTS model dropdown and never
    # pollutes the LLM-role dropdowns (conversation/summary/.../agent).
    tts_dropdown_only: bool = True
    # Whether this provider should be selectable in the user-facing TTS model
    # configuration dropdown. Clone-only hosted providers can still synthesize
    # saved voices without asking users to configure endpoint/resource fields.
    tts_config_visible: bool = True
    # Default endpoint / model / voice prefilled when the user first selects it.
    default_url: str = ""
    default_model: str = ""
    default_voice: str = ""
    # core_config field names this provider reads its runtime config from.
    url_field: str = "ttsModelUrl"
    model_field: str = "ttsModelId"
    voice_field: str = "ttsVoiceId"
    api_key_field: str = "ttsModelApiKey"
    # Whether the settings page unlocks URL / model / voice / key inputs for this
    # provider (a user-pointed endpoint) rather than locking them to a preset.
    editable_endpoint: bool = False
    # Connectivity preflight strategy for the settings light / save path.
    probe_kind: ProbeKind = "none"
    # Optional probe sub_type tag the frontend sends so the backend routes to the
    # matching probe (e.g. 'vllm_omni_tts').
    probe_sub_type: str = ""
    # For ``probe_kind == 'ws_handshake'``: the path suffix appended to the
    # configured base URL to reach the stream endpoint the worker connects to
    # (e.g. '/audio/speech/stream'). Data, not hardcoded in the frontend probe.
    probe_ws_path: str = ""


_REGISTRY: dict[str, TTSProvider] = {}


def register(provider: TTSProvider) -> None:
    """Register (or replace) a TTS provider. Idempotent by key so tests and
    hot-reload can re-register without piling up duplicates."""
    _REGISTRY[provider.key] = provider


def get(key: str | None) -> TTSProvider | None:
    if not key:
        return None
    provider = _REGISTRY.get(key)
    if provider is not None:
        return provider
    return next((item for item in _REGISTRY.values() if key in item.aliases), None)


def all_providers() -> list[TTSProvider]:
    """All registered providers, lowest ``priority`` first (dispatch order)."""
    return sorted(_REGISTRY.values(), key=lambda p: p.priority)


def selected_provider(ctx: "DispatchContext") -> "TTSProvider | None":
    """Return the first provider selected for ``ctx`` in priority order, or None.

    The single place the registry decides "which provider wins for this context".
    ``resolve_selected`` (dispatch) and the UI / validation preset helpers all go
    through here so they agree on precedence — e.g. an explicit GPT-SoVITS pick
    (priority 10) wins over MiMo's preset catalog (priority 60), so the catalog is
    hidden in exactly the cases dispatch wouldn't route to it.
    """
    for provider in all_providers():
        try:
            selected = provider.is_selected(ctx)
        except Exception:
            # is_selected 判定异常不应连带打挂整个 dispatch，跳过该 provider 继续；
            # 但静默会掩盖配置/适配器 bug，至少留一条带堆栈的可观测日志。
            logger.warning(
                "TTS provider %r is_selected 判定异常，跳过该 provider", provider.key,
                exc_info=True,
            )
            selected = False
        if selected:
            return provider
    return None


def resolve_selected(
    ctx: "DispatchContext",
) -> "tuple[Callable[..., Any], str | None, str] | None":
    """Return the dispatch tuple for the first provider selected for ``ctx``, in
    priority order, or ``None`` when none apply.

    ``get_tts_worker`` builds ``ctx`` and calls this near the top (after the
    DISABLE_TTS check) so a user's provider choice — whether an explicit dropdown
    pick or an implied clone-voice provider — wins over native / core default
    routing in the original hand-written precedence (priority order).
    """
    provider = selected_provider(ctx)
    return provider.resolve(ctx) if provider is not None else None


# ── Preset-catalog queries (single source of truth for built-in voices) ──────
# Primitives are keyed by provider; the ``selected_*`` variants gate on which
# provider actually wins for the current config (via ``selected_provider``), so
# a provider's preset voices only surface / validate when that provider is the
# one dispatch would route to. The registry must be populated first — callers
# that run outside the dispatch path ensure ``import main_logic.tts_client``
# (the heavy worker module that registers the hosted providers), mirroring
# ``config_router``'s ``ui_metadata`` call site.


def preset_catalog_for_ui(provider_key: str | None) -> "dict[str, dict[str, str | bool]] | None":
    """The UI voice catalog for ``provider_key``'s preset_catalog, or None."""
    provider = get(provider_key)
    if provider is None or provider.preset_catalog is None:
        return None
    return provider.preset_catalog.catalog_for_ui(provider.key)


def is_preset_voice(provider_key: str | None, voice_id: str | None) -> bool:
    """Whether ``voice_id`` is a built-in voice of ``provider_key``'s catalog."""
    provider = get(provider_key)
    if provider is None or provider.preset_catalog is None:
        return False
    return provider.preset_catalog.is_voice(voice_id)


def selected_provider_key(
    core_config: Mapping[str, Any],
    cm: "ConfigManager",
) -> str | None:
    """Key of the provider currently selected for ``core_config`` / ``cm``, or None.

    UI precedence helper: the ``/voices`` endpoint reads this to mirror dispatch —
    if the winner ships a static catalog (``preset_catalog_for_ui(key)`` non-None)
    show it; if a registry provider wins but ships no catalog (vLLM-Omni /
    GPT-SoVITS — user-entered or self-hosted voices), core-native voices must be
    suppressed too, since selecting one would be misrouted to that winner. None
    means no registry provider won → fall back to core-native voices."""
    provider = selected_provider(DispatchContext(core_config=core_config, cm=cm))
    return provider.key if provider is not None else None


def selected_preset_provider_key(
    core_config: Mapping[str, Any],
    cm: "ConfigManager",
    voice_id: str | None,
) -> str | None:
    """Key of the currently selected provider **iff** ``voice_id`` is one of its
    preset_catalog voices, else None — resolved in a single ``selected_provider``
    dispatch.

    Write-side dual of :func:`is_selected_preset_voice`: the normalizer
    (``ConfigManager.normalize_voice_id_to_config``) needs both "is this a selected
    preset" and "whose preset" together to tag ``{source:preset, provider:<key>}``.
    Folding the two into one dispatch (instead of ``is_selected_preset_voice`` +
    ``selected_provider_key``) closes the window where a future provider whose
    ``is_selected`` depends on ``ctx.voice_id`` could answer the membership and the
    key queries with different winners."""
    provider = selected_provider(
        DispatchContext(core_config=core_config, cm=cm, voice_id=voice_id or "")
    )
    if provider is None or not is_preset_voice(provider.key, voice_id):
        return None
    return provider.key


def is_selected_preset_voice(
    core_config: Mapping[str, Any],
    cm: "ConfigManager",
    voice_id: str | None,
) -> bool:
    """Whether ``voice_id`` is a built-in voice of the currently selected provider
    (used by ``validate_voice_id`` so a hosted provider's presets are saveable
    only while that provider is selected — dual to ``is_saveable_native_voice``)."""
    return selected_preset_provider_key(core_config, cm, voice_id) is not None


def ui_metadata() -> list[dict[str, Any]]:
    """Serializable per-provider metadata for the settings frontend / probe.

    Lets the ``/api_providers`` endpoint and the connectivity probe read one
    source of truth instead of restating each provider inline in JS. Includes
    ``kind`` and ``capabilities`` so the frontend can render a source-first
    picker (preset / clone / design) per the provider's real capabilities.
    """
    return [
        {
            "key": p.key,
            "aliases": sorted(p.aliases),
            "kind": p.kind,
            "capabilities": sorted(p.capabilities),
            "voice_design": p.voice_design.for_ui() if p.voice_design is not None else None,
            "tts_dropdown_only": p.tts_dropdown_only,
            "tts_config_visible": p.tts_config_visible,
            "default_url": p.default_url,
            "default_model": p.default_model,
            "default_voice": p.default_voice,
            "url_field": p.url_field,
            "model_field": p.model_field,
            "voice_field": p.voice_field,
            "api_key_field": p.api_key_field,
            "editable_endpoint": p.editable_endpoint,
            "probe_kind": p.probe_kind,
            "probe_sub_type": p.probe_sub_type,
            "probe_ws_path": p.probe_ws_path,
        }
        for p in all_providers()
    ]
