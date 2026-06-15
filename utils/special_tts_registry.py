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

"""Special (user-configured) TTS provider registry.

This is the dual of :mod:`utils.native_voice_registry`. Where that registry
serves providers whose TTS rides on an LLM core's built-in voices (Gemini,
StepFun, Grok, MiMo), this one serves *special* TTS providers: ones the user
points at an explicit endpoint and configures with their own URL / model /
voice / api key, independent of the conversation core. Members today are
``vllm_omni`` (remote or local vLLM-Omni WebSocket TTS), ``gptsovits`` (local
GPT-SoVITS) and ``local_cosyvoice`` (local CosyVoice).

Why a registry at all
---------------------
Adding ``vllm_omni`` in PR #1764 meant editing, by hand, the same provider
identity in many disconnected places: the ``get_tts_worker`` dispatch branch,
three ``core.py`` helpers (``_is_vllm_omni_tts_enabled`` /
``_resolve_vllm_omni_runtime_config`` / ``resolve_tts_api_key``), the frontend
dropdown filter, the connectivity probe and the i18n names. Each new special
provider repeats that scatter. This registry collapses the *backend* axis of
that scatter into one declarative entry per provider:

* ``is_selected(core_config, cm)`` — has the user actively chosen this provider?
* ``resolve(core_config, cm)`` — the dispatch tuple
  ``(worker, api_key_override, provider_key)`` that ``get_tts_worker`` returns.
* declarative UI metadata (default url/model/voice, dropdown scope, probe kind)
  so the frontend and connectivity probe can be driven off the same source of
  truth instead of restating each provider inline.

Layering mirrors ``native_voice_registry`` to avoid circular imports: metadata
lives here; the actual worker callables (which pull heavy deps like ``soxr`` /
``websockets``) are bound by ``main_logic.tts_client`` after the workers are
defined, via :func:`register`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from utils.config_manager import ConfigManager


# resolve() returns the same shape get_tts_worker hands back to core.py:
#   (worker_callable, api_key_override_or_None, provider_key)
DispatchResult = "tuple[Callable[..., Any], str | None, str]"

SelectPredicate = Callable[[Mapping[str, Any], "ConfigManager"], bool]
DispatchResolver = Callable[[Mapping[str, Any], "ConfigManager"], "tuple[Callable[..., Any], str | None, str]"]

# How the settings page should probe this provider's endpoint for liveness.
#   'ws_handshake' — open the WebSocket and watch the handshake for auth errors
#                    (vllm_omni). 'local_http' — hit the local service over HTTP
#                    (gptsovits / local_cosyvoice). 'none' — no preflight probe.
ProbeKind = Literal["ws_handshake", "local_http", "none"]


@dataclass(frozen=True)
class SpecialTTSProvider:
    """One user-configured TTS provider, declared in a single place.

    ``priority`` orders evaluation in the dispatcher: lower runs first. The
    existing hand-written order in ``get_tts_worker`` put GPT-SoVITS ahead of
    vLLM-Omni (both ahead of clone-voice routing), so they keep priorities 10
    and 20 respectively; new providers slot in by choosing a number.

    ``is_selected`` / ``resolve`` carry the provider-specific behavior. They are
    plain callables rather than further declarative fields because the real
    providers diverge too much to share one config-field schema: vLLM-Omni reads
    a url/model/voice triple from ``ttsModelUrl/Id/VoiceId`` and forbids api-key
    fallback, while GPT-SoVITS gates on ``GPTSOVITS_ENABLED`` + the ``tts_custom``
    slot and packs its voice differently. The declarative fields below capture
    only the axis that genuinely *is* shared — the settings UI and probe.
    """

    key: str
    priority: int
    is_selected: SelectPredicate
    resolve: DispatchResolver

    # ── Declarative UI / probe metadata (single source of truth for阶段3) ──
    # Whether this provider should appear only in the TTS model dropdown and
    # never pollute the LLM-role dropdowns (conversation/summary/.../agent).
    tts_dropdown_only: bool = True
    # Default endpoint / model / voice prefilled when the user first selects it.
    default_url: str = ""
    default_model: str = ""
    default_voice: str = ""
    # core_config field names this provider reads its runtime config from.
    url_field: str = "ttsModelUrl"
    model_field: str = "ttsModelId"
    voice_field: str = "ttsVoiceId"
    api_key_field: str = "ttsModelApiKey"
    # Whether the settings page should unlock URL / model / voice / key inputs for
    # this provider (a user-pointed custom endpoint), rather than locking them to a
    # Key-Book-managed preset. True for vLLM-Omni-style providers shown in the
    # dropdown; legacy providers with their own bespoke UI (GPT-SoVITS) leave it
    # False and are not driven through the dropdown path.
    editable_endpoint: bool = False
    # Connectivity preflight strategy for the settings light / save path.
    probe_kind: ProbeKind = "none"
    # Optional probe sub_type tag the frontend sends so the backend routes to
    # the matching probe (e.g. 'vllm_omni_tts').
    probe_sub_type: str = ""
    # For ``probe_kind == 'ws_handshake'``: the path suffix appended to the
    # configured base URL to reach the TTS stream endpoint the worker actually
    # connects to (e.g. '/audio/speech/stream'). Keeps that protocol detail as
    # data instead of hardcoding it in the frontend probe builder.
    probe_ws_path: str = ""


_REGISTRY: dict[str, SpecialTTSProvider] = {}


def register(provider: SpecialTTSProvider) -> None:
    """Register (or replace) a special TTS provider. Idempotent by key so tests
    and hot-reload can re-register without piling up duplicates."""
    _REGISTRY[provider.key] = provider


def get(key: str | None) -> SpecialTTSProvider | None:
    if not key:
        return None
    return _REGISTRY.get(key)


def all_providers() -> list[SpecialTTSProvider]:
    """All registered providers, lowest ``priority`` first (dispatch order)."""
    return sorted(_REGISTRY.values(), key=lambda p: p.priority)


def resolve_selected(
    core_config: Mapping[str, Any],
    cm: "ConfigManager",
) -> "tuple[Callable[..., Any], str | None, str] | None":
    """Return the dispatch tuple for the first special provider the user has
    selected, in priority order, or ``None`` when none apply.

    ``get_tts_worker`` calls this near the top (after the DISABLE_TTS check) so a
    user's explicit special-provider choice wins over clone / native / core
    default routing, preserving the original hand-written precedence.
    """
    for provider in all_providers():
        try:
            selected = provider.is_selected(core_config, cm)
        except Exception:
            selected = False
        if selected:
            return provider.resolve(core_config, cm)
    return None


def ui_metadata() -> list[dict[str, Any]]:
    """Serializable per-provider metadata for the settings frontend / probe.

    Lets ``/tts_providers``-style endpoints and the connectivity probe read one
    source of truth instead of restating each special provider inline in JS.
    """
    return [
        {
            "key": p.key,
            "tts_dropdown_only": p.tts_dropdown_only,
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
