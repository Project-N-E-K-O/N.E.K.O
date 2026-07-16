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
from main_logic.voice_turn.contracts import SpeechActivityEvent
from typing import Literal

from ._infra import (
    AsrSessionConfig,
    RealtimeAsrSession,
    AsrWorkerFn as _AsrWorkerFn,
    _RealtimeAsrSessionImpl,
)
from ._registry_meta import (
    ASR_PROVIDER_REGISTRY as _ASR_PROVIDER_REGISTRY,
    CORE_ASR_ROUTES as _CORE_ASR_ROUTES,
    AsrEndpointingMode as _AsrEndpointingMode,
)
from ._voice_turn import _create_voice_turn_adapter
from .workers.dummy import dummy_asr_worker as _dummy_asr_worker
from .workers.gemini import gemini_asr_worker as _gemini_asr_worker
from .workers.glm import glm_asr_worker as _glm_asr_worker
from .workers.grok import grok_asr_worker as _grok_asr_worker
from .workers.openai import openai_asr_worker as _openai_asr_worker
from .workers.qwen import qwen_asr_worker as _qwen_asr_worker
from .workers.soniox import soniox_asr_worker as _soniox_asr_worker
from .workers.step import step_asr_worker as _step_asr_worker


__all__ = [
    "AsrSessionConfig",
    "RealtimeAsrSession",
    "create_asr_session",
]


_IMPLEMENTED_WORKERS: dict[str, _AsrWorkerFn] = {
    "dummy": _dummy_asr_worker,
    "qwen": _qwen_asr_worker,
    "openai": _openai_asr_worker,
    "step": _step_asr_worker,
    "grok": _grok_asr_worker,
    "glm": _glm_asr_worker,
    "gemini": _gemini_asr_worker,
    "soniox": _soniox_asr_worker,
}


@dataclass(frozen=True, slots=True)
class _AsrSelection:
    provider_key: str
    endpointing_mode: _AsrEndpointingMode
    soniox_region: Literal["us", "eu", "jp"] | None = None


def _load_core_config() -> dict:
    from utils.config_manager import get_config_manager

    try:
        return get_config_manager().get_core_config() or {}
    except Exception:
        return {}


def _is_explicit_intl_region(user_region: str) -> bool:
    normalized = user_region.strip().lower().replace("_", "-")
    return normalized in {
        "intl",
        "international",
        "us",
        "eu",
        "jp",
        "europe",
        "japan",
        "uk",
        "gb",
        "au",
        "nz",
        "kr",
        "sg",
    }


def _mapped_soniox_region(user_region: str) -> Literal["us", "eu", "jp"]:
    normalized = user_region.strip().lower().replace("_", "-")
    if normalized in {"eu", "europe", "uk", "gb"}:
        return "eu"
    if normalized in {"jp", "japan", "au", "nz", "kr", "sg"}:
        return "jp"
    return "us"


def _resolve_asr_selection(
    core_type: str,
    *,
    user_region: str | None = None,
    include_dev_override: bool = True,
) -> _AsrSelection:
    """Resolve one pre-audio provider choice without opening a session."""

    core_key = str(core_type or "").strip().lower()
    route = _CORE_ASR_ROUTES.get(core_key)
    if route is None:
        raise RuntimeError(f"ASR_UNKNOWN_CORE: {core_key or '<empty>'}")

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
        return _AsrSelection(provider_key="dummy", endpointing_mode="manual")

    core_config = _load_core_config()
    resolved_region = str(
        user_region
        or os.getenv("ASR_USER_REGION", "")
        or core_config.get("ASR_USER_REGION")
        or ""
    ).strip()
    soniox_key = str(
        core_config.get("SONIOX_API_KEY")
        or os.getenv("SONIOX_API_KEY", "")
        or ""
    ).strip()
    if _is_explicit_intl_region(resolved_region) and soniox_key:
        raw_soniox_region = str(
            os.getenv("SONIOX_REGION", "")
            or core_config.get("SONIOX_REGION")
            or _mapped_soniox_region(resolved_region)
        ).strip().lower()
        if raw_soniox_region not in {"us", "eu", "jp"}:
            raise RuntimeError(
                "ASR_INVALID_CONFIG: SONIOX_REGION must be us, eu, or jp"
            )
        return _AsrSelection(
            provider_key="soniox",
            endpointing_mode="provider",
            soniox_region=raw_soniox_region,  # type: ignore[arg-type]
        )

    return _AsrSelection(
        provider_key=route.provider_key,
        endpointing_mode=route.default_endpointing_mode,
    )


def _get_default_endpointing_mode(core_type: str) -> _AsrEndpointingMode:
    return _resolve_asr_selection(core_type).endpointing_mode


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

    meta = _ASR_PROVIDER_REGISTRY.get(provider_key)
    if meta is None:
        raise RuntimeError(f"ASR_BACKEND_NOT_IMPLEMENTED: {provider_key}")
    if endpointing_mode not in meta.supported_endpointing_modes:
        raise RuntimeError(
            "ASR_ENDPOINTING_NOT_SUPPORTED: "
            f"{provider_key} does not support {endpointing_mode}"
        )
    if meta.implementation_status == "blocked_backend":
        raise RuntimeError(f"ASR_BACKEND_BLOCKED: {core_key}")
    if meta.implementation_status == "blocked_credentials":
        raise RuntimeError(f"ASR_CREDENTIALS_MISSING: {provider_key}")
    if meta.implementation_status != "implemented":
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
        or (os.getenv("SONIOX_API_KEY", "") if provider_key == "soniox" else "")
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
    on_status_message: Callable[[str], Awaitable[None]] | None = None,
    on_speech_activity: Callable[[SpeechActivityEvent], Awaitable[None]] | None = None,
    user_region: str | None = None,
) -> RealtimeAsrSession:
    """Create an isolated ASR session or fail fast for unsupported routes."""

    if config is not None and not isinstance(config, AsrSessionConfig):
        raise TypeError("ASR_INVALID_CONFIG: config must be AsrSessionConfig")
    selection = _resolve_asr_selection(core_type, user_region=user_region)
    session_config = config or AsrSessionConfig(
        endpointing_mode=selection.endpointing_mode
    )
    route = _CORE_ASR_ROUTES[str(core_type or "").strip().lower()]
    worker_kwargs = {}
    if selection.provider_key != route.provider_key:
        worker_kwargs = {
            "provider_key_override": selection.provider_key,
            "soniox_region": selection.soniox_region,
        }
    worker_fn, api_key_override, provider_key = _get_asr_worker(
        core_type,
        session_config.endpointing_mode,
        **worker_kwargs,
    )

    return _RealtimeAsrSessionImpl(
        worker_fn=worker_fn,
        api_key=api_key_override or "",
        config=session_config,
        on_input_transcript=on_input_transcript,
        on_connection_error=on_connection_error,
        on_status_message=on_status_message,
        voice_turn_factory=(
            partial(_create_voice_turn_adapter, on_activity=on_speech_activity)
            if (
                _ASR_PROVIDER_REGISTRY[provider_key].requires_smart_turn
                and session_config.endpointing_mode == "manual"
            )
            else None
        ),
    )


def _create_core_follow_asr_session(
    core_type: str,
    *,
    on_input_transcript: Callable[[str], Awaitable[None]],
    on_connection_error: Callable[[str], Awaitable[None]],
    on_status_message: Callable[[str], Awaitable[None]] | None = None,
    on_speech_activity: Callable[[SpeechActivityEvent], Awaitable[None]] | None = None,
) -> RealtimeAsrSession:
    """Create the configured Core's ASR without applying regional preference."""

    core_key = str(core_type or "").strip().lower()
    route = _CORE_ASR_ROUTES.get(core_key)
    if route is None:
        raise RuntimeError(f"ASR_UNKNOWN_CORE: {core_key or '<empty>'}")
    endpointing_mode = route.default_endpointing_mode
    worker_fn, api_key_override, provider_key = _get_asr_worker(
        core_key,
        endpointing_mode,
    )
    return _RealtimeAsrSessionImpl(
        worker_fn=worker_fn,
        api_key=api_key_override or "",
        config=AsrSessionConfig(endpointing_mode=endpointing_mode),
        on_input_transcript=on_input_transcript,
        on_connection_error=on_connection_error,
        on_status_message=on_status_message,
        voice_turn_factory=(
            partial(_create_voice_turn_adapter, on_activity=on_speech_activity)
            if (
                _ASR_PROVIDER_REGISTRY[provider_key].requires_smart_turn
                and endpointing_mode == "manual"
            )
            else None
        ),
    )


def _attach_partial_callback(
    session: RealtimeAsrSession,
    callback: Callable[[str], Awaitable[None]] | None,
) -> None:
    """Attach display-only partial delivery to the built-in session."""

    if isinstance(session, _RealtimeAsrSessionImpl):
        session._on_partial_transcript = callback
