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

"""The per-feature speech-recognition slot of the API settings page.

The slot lets the user pick an ASR vendor independently of the Core: any
vendor below, or a self-hosted OpenAI-compatible server. When it is active it
overrides the Core-derived ASR route for every Core, the free Core included.

The slot is stored as two dimensions: a vendor (``asrModelProvider``) and a
transport (``asrModelTransport``). Only ``"request"`` exists today: one HTTP
request per SmartTurn-sealed utterance (non-streaming). The pair maps onto a
registry key through :data:`ASR_SLOT_ROUTES`, so a later streaming transport
adds table rows instead of a config migration. The slot's registry keys are
kept apart from the Core-derived (often streaming) keys on purpose: a
``*_request`` key is always segmented, never a realtime stream.
"""

from __future__ import annotations

import urllib.parse
from dataclasses import dataclass, field
from typing import Literal

from ._registry_meta import CORE_ASR_ROUTES, AsrProviderAvailability


AsrSlotTransport = Literal["request"]
AsrSlotProtocol = Literal["openai_multipart", "qwen_chat", "gemini"]

ASR_SLOT_FOLLOW_CORE = "follow_core"
ASR_SLOT_CUSTOM = "custom"
ASR_SLOT_DEFAULT_TRANSPORT: AsrSlotTransport = "request"
ASR_SLOT_TRANSPORTS: frozenset[str] = frozenset({"request"})

_TRANSCRIPTIONS_PATH = "/audio/transcriptions"


@dataclass(frozen=True, slots=True)
class AsrSlotVendor:
    """One vendor the slot can select, with its non-streaming request shape."""

    vendor: str
    protocol: AsrSlotProtocol
    # Request URL used as is (vendors) or the fallback when nothing is saved.
    default_url: str
    default_model: str
    # core_config snapshot key holding the vendor's Key Book credential.
    # Empty for ``custom``, whose key lives in the slot itself.
    credential_field: str
    # Lower-case substrings naming the model families this vendor's
    # ``protocol`` actually serves -- not "anything that sounds like ASR": a
    # vendor's /models also lists ASR models that need another API. A listed
    # model is a candidate when its id contains a ``model_include`` token and
    # no ``model_exclude`` token. Excluding "realtime"/"live"/"stream" keeps
    # streaming-only models out of a non-streaming slot. Empty
    # ``model_include`` means the list is not filtered (``custom``: a
    # self-hosted server names its models freely, so any guess would miss).
    model_include: tuple[str, ...] = ()
    model_exclude: tuple[str, ...] = ("realtime", "live", "stream", "tts")

    @property
    def filters_models(self) -> bool:
        return bool(self.model_include)


def _route_credential(core_key: str) -> str:
    return CORE_ASR_ROUTES[core_key].credential_field


ASR_SLOT_VENDORS: dict[str, AsrSlotVendor] = {
    "openai": AsrSlotVendor(
        vendor="openai",
        protocol="openai_multipart",
        default_url="https://api.openai.com/v1/audio/transcriptions",
        default_model="gpt-transcribe",
        credential_field=_route_credential("openai"),
        model_include=("transcribe", "whisper"),
    ),
    "grok": AsrSlotVendor(
        vendor="grok",
        protocol="openai_multipart",
        default_url="https://api.x.ai/v1/stt",
        default_model="grok-voice-transcribe-2.0",
        credential_field=_route_credential("grok"),
        model_include=("transcribe", "stt"),
    ),
    "glm": AsrSlotVendor(
        vendor="glm",
        protocol="openai_multipart",
        default_url="https://open.bigmodel.cn/api/paas/v4/audio/transcriptions",
        default_model="glm-asr-2512",
        credential_field=_route_credential("glm"),
        model_include=("asr",),
    ),
    "gemini": AsrSlotVendor(
        vendor="gemini",
        protocol="gemini",
        # The native SDK picks its own endpoint; kept for display only.
        default_url="https://generativelanguage.googleapis.com",
        default_model="gemini-3.1-flash-lite",
        credential_field=_route_credential("gemini"),
        # Gemini transcribes with its general multimodal models.
        model_include=("gemini",),
        model_exclude=(
            "realtime", "live", "stream", "tts", "embedding", "image", "imagen", "veo",
        ),
    ),
    "qwen": AsrSlotVendor(
        vendor="qwen",
        protocol="qwen_chat",
        default_url="https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        default_model="qwen3-asr-flash",
        credential_field=_route_credential("qwen"),
        # Compatible mode serves only the Qwen3-ASR-Flash family. The listing
        # also carries qwen-audio-*-asr and fun-asr models, which answer this
        # request with 400 "format is empty" (native DashScope API only), and
        # the async file-transcription variant.
        model_include=("qwen3-asr-flash",),
        model_exclude=("realtime", "live", "stream", "tts", "filetrans"),
    ),
    # The US region does not offer the OpenAI-compatible mode, so the
    # international vendor always uses the Singapore endpoint.
    "qwen_intl": AsrSlotVendor(
        vendor="qwen_intl",
        protocol="qwen_chat",
        default_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions",
        default_model="qwen3-asr-flash",
        credential_field=_route_credential("qwen_intl"),
        model_include=("qwen3-asr-flash",),
        model_exclude=("realtime", "live", "stream", "tts", "filetrans"),
    ),
    ASR_SLOT_CUSTOM: AsrSlotVendor(
        vendor=ASR_SLOT_CUSTOM,
        protocol="openai_multipart",
        default_url="",
        default_model="",
        credential_field="",
    ),
}


# (vendor, transport) -> registry key. Streaming later adds rows such as
# ("qwen", "stream") -> "qwen" without changing persisted settings.
ASR_SLOT_ROUTES: dict[tuple[str, str], str] = {
    ("openai", "request"): "openai_request",
    ("grok", "request"): "grok_request",
    ("glm", "request"): "glm_request",
    ("gemini", "request"): "gemini_request",
    ("qwen", "request"): "qwen_request",
    ("qwen_intl", "request"): "qwen_request",
    (ASR_SLOT_CUSTOM, "request"): "custom",
}

ASR_SLOT_PROVIDER_KEYS: frozenset[str] = frozenset(ASR_SLOT_ROUTES.values())


@dataclass(frozen=True, slots=True)
class AsrSlotEndpoint:
    """A resolved slot: which worker to run and where it sends audio."""

    vendor: str
    transport: AsrSlotTransport
    provider_key: str
    protocol: AsrSlotProtocol
    url: str
    model: str
    availability: AsrProviderAvailability = AsrProviderAvailability.IMPLEMENTED
    api_key: str = field(default="", repr=False, compare=False)


def normalize_custom_asr_url(raw_url: object) -> str:
    """Turn a base URL or a full transcription URL into the request URL.

    ``http://host:8081`` -> ``http://host:8081/v1/audio/transcriptions``;
    ``http://host:8081/v1`` -> ``.../v1/audio/transcriptions``; a URL that
    already ends in ``/audio/transcriptions`` is kept. Anything that is not an
    absolute http(s) URL returns ``""``.
    """

    url = str(raw_url or "").strip()
    if not url:
        return ""
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return ""
    if parts.scheme.lower() not in ("http", "https") or not parts.netloc:
        return ""
    path = parts.path.rstrip("/")
    if path.lower().endswith(_TRANSCRIPTIONS_PATH):
        pass
    elif not path:
        path = "/v1" + _TRANSCRIPTIONS_PATH
    else:
        path = path + _TRANSCRIPTIONS_PATH
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, parts.query, ""))


def custom_asr_models_base_url(raw_url: object) -> str:
    """The OpenAI-compatible base (``.../v1``) whose ``/models`` lists models."""

    request_url = normalize_custom_asr_url(raw_url)
    if not request_url:
        return ""
    parts = urllib.parse.urlsplit(request_url)
    path = parts.path[: -len(_TRANSCRIPTIONS_PATH)]
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def is_asr_model_candidate(vendor: str, model_id: object) -> bool:
    """Whether a listed model id is one the vendor's slot request can use.

    Always ``False`` for a vendor that does not filter (``custom``).
    """

    spec = ASR_SLOT_VENDORS.get(str(vendor or "").strip().lower())
    normalized = str(model_id or "").strip().lower()
    if spec is None or not normalized or not spec.filters_models:
        return False
    if any(token in normalized for token in spec.model_exclude):
        return False
    return any(token in normalized for token in spec.model_include)


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def _misconfigured(vendor: str, transport: str, spec: AsrSlotVendor | None) -> AsrSlotEndpoint:
    # Keep a registered provider key so the session layer reaches the
    # availability check (and its fail-closed status) instead of a lookup error.
    provider_key = (
        ASR_SLOT_ROUTES.get((vendor, transport))
        or ASR_SLOT_ROUTES.get((vendor, ASR_SLOT_DEFAULT_TRANSPORT))
        or ASR_SLOT_ROUTES[(ASR_SLOT_CUSTOM, ASR_SLOT_DEFAULT_TRANSPORT)]
    )
    return AsrSlotEndpoint(
        vendor=vendor,
        transport=ASR_SLOT_DEFAULT_TRANSPORT,
        provider_key=provider_key,
        protocol=spec.protocol if spec is not None else "openai_multipart",
        url="",
        model="",
        availability=AsrProviderAvailability.MISCONFIGURED,
    )


def resolve_asr_slot(core_config: dict | None) -> AsrSlotEndpoint | None:
    """Resolve the slot from a ``get_core_config`` snapshot.

    Returns ``None`` only when the slot does not override the Core: per-feature
    models are off, or the slot is empty / follows the Core. Once the user has
    picked a slot it always overrides, even when it cannot run: an unknown
    vendor or transport, or a custom slot without a usable http(s) URL,
    reports ``MISCONFIGURED``, and a named vendor without its Key Book
    credential reports ``MISSING_CREDENTIALS``. Either way the session fails
    closed instead of quietly sending audio to the Core's provider.
    """

    config = core_config if isinstance(core_config, dict) else {}
    if not _as_bool(config.get("ENABLE_CUSTOM_API", False)):
        return None
    vendor = str(config.get("asrModelProvider") or "").strip().lower()
    if not vendor or vendor == ASR_SLOT_FOLLOW_CORE:
        return None
    spec = ASR_SLOT_VENDORS.get(vendor)
    transport = (
        str(config.get("asrModelTransport") or "").strip().lower()
        or ASR_SLOT_DEFAULT_TRANSPORT
    )
    provider_key = ASR_SLOT_ROUTES.get((vendor, transport))
    if spec is None or provider_key is None:
        return _misconfigured(vendor, transport, spec)
    model = str(config.get("asrModelId") or "").strip() or spec.default_model

    if vendor == ASR_SLOT_CUSTOM:
        url = normalize_custom_asr_url(config.get("asrModelUrl"))
        if not url:
            return _misconfigured(vendor, transport, spec)
        return AsrSlotEndpoint(
            vendor=vendor,
            transport=transport,  # type: ignore[arg-type]
            provider_key=provider_key,
            protocol=spec.protocol,
            url=url,
            model=model,
            api_key=str(config.get("ASR_MODEL_API_KEY") or "").strip(),
        )

    # ASR_MODEL_API_KEY holds the selected vendor's own Key Book entry (see
    # the core-config snapshot), never another vendor's chat key.
    api_key = str(config.get("ASR_MODEL_API_KEY") or "").strip()
    return AsrSlotEndpoint(
        vendor=vendor,
        transport=transport,  # type: ignore[arg-type]
        provider_key=provider_key,
        protocol=spec.protocol,
        url=spec.default_url,
        model=model,
        availability=(
            AsrProviderAvailability.IMPLEMENTED
            if api_key
            else AsrProviderAvailability.MISSING_CREDENTIALS
        ),
        api_key=api_key,
    )


def validate_asr_slot_fields(provider: object, transport: object, url: object) -> str | None:
    """Save-time check of the slot fields; returns an error message or ``None``."""

    vendor = str(provider or "").strip().lower()
    if vendor and vendor != ASR_SLOT_FOLLOW_CORE and vendor not in ASR_SLOT_VENDORS:
        return "Unsupported speech-recognition provider"
    normalized_transport = str(transport or "").strip().lower()
    if normalized_transport and normalized_transport not in ASR_SLOT_TRANSPORTS:
        return "Unsupported speech-recognition transport"
    if vendor == ASR_SLOT_CUSTOM and not normalize_custom_asr_url(url):
        return "Custom speech-recognition URL must be an http(s) URL"
    return None


__all__ = [
    "ASR_SLOT_CUSTOM",
    "ASR_SLOT_DEFAULT_TRANSPORT",
    "ASR_SLOT_FOLLOW_CORE",
    "ASR_SLOT_PROVIDER_KEYS",
    "ASR_SLOT_ROUTES",
    "ASR_SLOT_TRANSPORTS",
    "ASR_SLOT_VENDORS",
    "AsrSlotEndpoint",
    "AsrSlotVendor",
    "custom_asr_models_base_url",
    "is_asr_model_candidate",
    "normalize_custom_asr_url",
    "resolve_asr_slot",
    "validate_asr_slot_fields",
]
