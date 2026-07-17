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

from ._infra import (
    AsrSessionConfig,
    RealtimeAsrSession,
    AsrWorkerFn as _AsrWorkerFn,
    _RealtimeAsrSessionImpl,
)
from ._registry_meta import (
    ASR_PROVIDER_REGISTRY as _ASR_PROVIDER_REGISTRY,
    CORE_ASR_ROUTES as _CORE_ASR_ROUTES,
)
from .workers.dummy import dummy_asr_worker as _dummy_asr_worker


__all__ = [
    "AsrSessionConfig",
    "RealtimeAsrSession",
    "create_asr_session",
]


_IMPLEMENTED_WORKERS: dict[str, _AsrWorkerFn] = {
    "dummy": _dummy_asr_worker,
}


def _get_asr_worker(core_type: str) -> tuple[_AsrWorkerFn, str | None, str]:
    """Resolve one worker without exposing provider internals to callers."""

    core_key = str(core_type or "").strip().lower()
    provider_key = _CORE_ASR_ROUTES.get(core_key)
    if provider_key is None:
        raise RuntimeError(f"ASR_UNKNOWN_CORE: {core_key or '<empty>'}")

    provider_override = os.getenv("ASR_PROVIDER", "").strip().lower()
    if provider_override:
        if provider_override != "dummy":
            raise RuntimeError(
                "ASR_INVALID_CONFIG: ASR_PROVIDER only supports the development "
                "value 'dummy' in Phase 1"
            )
        provider_key = "dummy"

    meta = _ASR_PROVIDER_REGISTRY[provider_key]
    if meta.implementation_status == "blocked_backend":
        raise RuntimeError(f"ASR_BACKEND_BLOCKED: {core_key}")
    if meta.implementation_status == "blocked_credentials":
        raise RuntimeError(f"ASR_CREDENTIALS_MISSING: {provider_key}")
    if meta.implementation_status != "implemented":
        raise RuntimeError(f"ASR_BACKEND_NOT_IMPLEMENTED: {core_key}")

    worker_fn = _IMPLEMENTED_WORKERS.get(provider_key)
    if worker_fn is None:
        raise RuntimeError(f"ASR_BACKEND_NOT_IMPLEMENTED: {core_key}")

    # Dummy deliberately receives an empty key. Real workers added in later
    # phases must resolve their own provider-specific credentials here rather
    # than borrowing another provider's key as a fallback.
    return worker_fn, "", provider_key


def create_asr_session(
    core_type: str,
    *,
    config: AsrSessionConfig | None = None,
    on_input_transcript: Callable[[str], Awaitable[None]],
    on_connection_error: Callable[[str], Awaitable[None]],
    on_status_message: Callable[[str], Awaitable[None]] | None = None,
) -> RealtimeAsrSession:
    """Create an isolated ASR session or fail fast for unsupported routes."""

    if config is not None and not isinstance(config, AsrSessionConfig):
        raise TypeError("ASR_INVALID_CONFIG: config must be AsrSessionConfig")
    session_config = config if config is not None else AsrSessionConfig()
    worker_fn, api_key_override, provider_key = _get_asr_worker(core_type)

    if provider_key == "dummy" and session_config.endpointing_mode != "manual":
        raise RuntimeError(
            "ASR_INVALID_CONFIG: dummy requires endpointing_mode='manual'"
        )

    return _RealtimeAsrSessionImpl(
        worker_fn=worker_fn,
        api_key=api_key_override or "",
        config=session_config,
        on_input_transcript=on_input_transcript,
        on_connection_error=on_connection_error,
        on_status_message=on_status_message,
    )
