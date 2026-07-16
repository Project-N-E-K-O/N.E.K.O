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

"""Stable public entry point for realtime speech recognition sessions."""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import partial
from typing import Literal

from ._infra import (
    AsrSessionConfig,
    AsrTranscriptEvent,
    RealtimeAsrSession,
    AsrWorkerFn as _AsrWorkerFn,
    _RealtimeAsrSessionImpl,
)
from ._registry_meta import (
    ASR_PROVIDER_REGISTRY as _ASR_PROVIDER_REGISTRY,
    CORE_ASR_ROUTES as _CORE_ASR_ROUTES,
    AsrEndpointingMode as _AsrEndpointingMode,
    AsrTurnCapabilities as _AsrTurnCapabilities,
)
from .workers.dummy import dummy_asr_worker as _dummy_asr_worker
from .workers.grok import grok_asr_worker as _grok_asr_worker
from .workers.openai import openai_asr_worker as _openai_asr_worker
from .workers.qwen import qwen_asr_worker as _qwen_asr_worker
from .workers.step import step_asr_worker as _step_asr_worker
from .workers.soniox import soniox_asr_worker as _soniox_asr_worker


__all__ = [
    "AsrSessionConfig",
    "AsrTranscriptEvent",
    "RealtimeAsrSession",
    "create_asr_session",
]


_IMPLEMENTED_WORKERS: dict[str, _AsrWorkerFn] = {
    "dummy": _dummy_asr_worker,
    "qwen": _qwen_asr_worker,
    "openai": _openai_asr_worker,
    "step": _step_asr_worker,
    "grok": _grok_asr_worker,
    "soniox": _soniox_asr_worker,
}

_AsrRoutingMode = Literal["auto", "core", "soniox"]


@dataclass(frozen=True, slots=True)
class _AsrSelection:
    provider_key: str
    routing_mode: _AsrRoutingMode
    soniox_region: Literal["us", "eu", "jp"] | None
    turn_capabilities: _AsrTurnCapabilities


def _load_core_config() -> dict:
    from utils.config_manager import get_config_manager

    try:
        return get_config_manager().get_core_config() or {}
    except Exception:
        return {}


def _mapped_soniox_region(user_region: str) -> Literal["us", "eu", "jp"]:
    normalized = user_region.strip().lower().replace("_", "-")
    if normalized in {
        "eu",
        "europe",
        "eea",
        "uk",
        "gb",
        "at",
        "be",
        "bg",
        "ch",
        "cy",
        "cz",
        "de",
        "dk",
        "ee",
        "es",
        "fi",
        "fr",
        "gr",
        "hr",
        "hu",
        "ie",
        "is",
        "it",
        "li",
        "lt",
        "lu",
        "lv",
        "mt",
        "nl",
        "no",
        "pl",
        "pt",
        "ro",
        "se",
        "si",
        "sk",
    }:
        return "eu"
    if normalized in {
        "jp",
        "japan",
        "apac",
        "asia",
        "asia-pacific",
        "au",
        "nz",
        "kr",
        "sg",
        "hk",
        "tw",
        "in",
        "id",
        "my",
        "ph",
        "th",
        "vn",
    }:
        return "jp"
    return "us"


def _is_mainland_region(user_region: str) -> bool:
    normalized = user_region.strip().lower().replace("_", "-")
    return normalized in {"", "cn", "china", "mainland", "zh-cn"}


def _resolve_asr_selection(
    core_type: str,
    *,
    routing_mode: str | None = None,
    user_region: str | None = None,
    force_core: bool = False,
    include_dev_override: bool = True,
) -> _AsrSelection:
    core_key = str(core_type or "").strip().lower()
    route = _CORE_ASR_ROUTES.get(core_key)
    if route is None:
        raise RuntimeError(f"ASR_UNKNOWN_CORE: {core_key or '<empty>'}")
    core_config = _load_core_config()
    raw_mode = (
        "core"
        if force_core
        else routing_mode
        or os.getenv("ASR_ROUTING_MODE")
        or core_config.get("ASR_ROUTING_MODE")
        or "auto"
    )
    mode = str(raw_mode).strip().lower()
    if mode not in {"auto", "core", "soniox"}:
        raise RuntimeError("ASR_INVALID_CONFIG: routing mode must be auto, core, or soniox")

    provider_override = (
        os.getenv("ASR_PROVIDER", "").strip().lower()
        if include_dev_override
        else ""
    )
    if provider_override:
        if provider_override != "dummy":
            raise RuntimeError(
                "ASR_INVALID_CONFIG: ASR_PROVIDER only supports the development "
                "value 'dummy'"
            )
        meta = _ASR_PROVIDER_REGISTRY["dummy"]
        return _AsrSelection(
            provider_key="dummy",
            routing_mode=mode,  # type: ignore[arg-type]
            soniox_region=None,
            turn_capabilities=meta.turn_capabilities,
        )

    resolved_user_region = str(
        user_region
        or os.getenv("ASR_USER_REGION")
        or core_config.get("ASR_USER_REGION")
        or "cn"
    )
    soniox_key = str(
        core_config.get("SONIOX_API_KEY") or os.getenv("SONIOX_API_KEY") or ""
    ).strip()
    use_soniox = mode == "soniox" or (
        mode == "auto" and not _is_mainland_region(resolved_user_region) and soniox_key
    )
    provider_key = "soniox" if use_soniox else route.provider_key
    region: Literal["us", "eu", "jp"] | None = None
    if provider_key == "soniox":
        raw_region = str(
            os.getenv("SONIOX_REGION")
            or core_config.get("SONIOX_REGION")
            or _mapped_soniox_region(resolved_user_region)
        ).strip().lower()
        if raw_region not in {"us", "eu", "jp"}:
            raise RuntimeError("ASR_INVALID_CONFIG: SONIOX_REGION must be us, eu, or jp")
        region = raw_region  # type: ignore[assignment]
    meta = _ASR_PROVIDER_REGISTRY[provider_key]
    return _AsrSelection(
        provider_key=provider_key,
        routing_mode=mode,  # type: ignore[arg-type]
        soniox_region=region,
        turn_capabilities=meta.turn_capabilities,
    )


def _get_asr_worker(
    core_type: str,
    endpointing_mode: _AsrEndpointingMode = "manual",
    *,
    provider_key_override: str | None = None,
    soniox_region: str | None = None,
) -> tuple[_AsrWorkerFn, str | None, str]:
    """Resolve one worker without exposing provider internals to callers."""

    core_key = str(core_type or "").strip().lower()
    route = _CORE_ASR_ROUTES.get(core_key)
    if route is None:
        raise RuntimeError(f"ASR_UNKNOWN_CORE: {core_key or '<empty>'}")

    provider_key = provider_key_override or route.provider_key

    meta = _ASR_PROVIDER_REGISTRY[provider_key]
    if endpointing_mode not in meta.supported_endpointing_modes:
        raise RuntimeError(
            "ASR_ENDPOINTING_NOT_SUPPORTED: "
            f"{provider_key} does not support {endpointing_mode}"
        )
    if meta.implementation_status == "blocked_backend":
        raise RuntimeError(f"ASR_BACKEND_BLOCKED: {core_key}")
    if meta.implementation_status not in {"implemented", "blocked_credentials"}:
        raise RuntimeError(f"ASR_BACKEND_NOT_IMPLEMENTED: {core_key}")

    worker_fn = _IMPLEMENTED_WORKERS.get(provider_key)
    if worker_fn is None:
        raise RuntimeError(f"ASR_BACKEND_NOT_IMPLEMENTED: {core_key}")

    if provider_key == "dummy":
        return worker_fn, "", provider_key

    # ConfigManager owns the only permitted provider-specific credential
    # fallback: a matching Core may supply CORE_API_KEY through its matching
    # ASSIST_API_KEY_* slot. No audio/TTS/other-provider key is consulted here.
    core_config = _load_core_config()
    credential_field = (
        "SONIOX_API_KEY" if provider_key == "soniox" else route.credential_field
    )
    api_key = str(
        core_config.get(credential_field)
        or (os.getenv("SONIOX_API_KEY") if provider_key == "soniox" else "")
        or ""
    ).strip()
    if not api_key:
        raise RuntimeError(f"ASR_CREDENTIALS_MISSING: {provider_key}")

    if provider_key == "qwen":
        worker_fn = partial(worker_fn, region=route.region or "cn")  # type: ignore[assignment]
    elif provider_key == "soniox":
        worker_fn = partial(worker_fn, region=soniox_region or "us")  # type: ignore[assignment]
    return worker_fn, api_key, provider_key


def create_asr_session(
    core_type: str,
    *,
    config: AsrSessionConfig | None = None,
    on_input_transcript: Callable[[str], Awaitable[None]],
    on_connection_error: Callable[[str], Awaitable[None]],
    on_transcript_event: Callable[[AsrTranscriptEvent], Awaitable[None]] | None = None,
    on_status_message: Callable[[str], Awaitable[None]] | None = None,
    routing_mode: str | None = None,
    user_region: str | None = None,
    force_core: bool = False,
) -> RealtimeAsrSession:
    """Create an isolated ASR session or fail fast for unsupported routes."""

    if config is not None and not isinstance(config, AsrSessionConfig):
        raise TypeError("ASR_INVALID_CONFIG: config must be AsrSessionConfig")
    session_config = config if config is not None else AsrSessionConfig()
    selection = _resolve_asr_selection(
        core_type,
        routing_mode=routing_mode,
        user_region=user_region,
        force_core=force_core,
    )
    worker_fn, api_key_override, provider_key = _get_asr_worker(
        core_type,
        session_config.endpointing_mode,
        provider_key_override=selection.provider_key,
        soniox_region=selection.soniox_region,
    )

    return _RealtimeAsrSessionImpl(
        worker_fn=worker_fn,
        api_key=api_key_override or "",
        config=session_config,
        on_input_transcript=on_input_transcript,
        on_transcript_event=on_transcript_event,
        on_connection_error=on_connection_error,
        on_status_message=on_status_message,
    )
