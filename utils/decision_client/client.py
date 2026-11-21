# -*- coding: utf-8 -*-
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

"""Unified dual-mode DecisionClient with native-protocol dispatch and fail-safe fallback."""

from __future__ import annotations

import time
from typing import Any, Mapping

from utils.logger_config import get_module_logger

from .adapters import ChatFallbackDecisionAdapter, get_decision_adapter
from .schema import (
    DecisionProtocolError,
    DecisionQuestion,
    DecisionResponse,
    infer_decision_protocol,
    is_native_decision_protocol,
    resolve_decision_endpoint,
)

logger = get_module_logger(__name__)

# Process-local negative cooldown when a native Decisions endpoint reports a
# permanent route/model error (400/404/405/422, e.g. preview model retired or
# not enabled on the user's workspace). Prevents burning a doomed HTTP
# round-trip on every subsequent call while automatically retrying later or
# immediately when the route/model config changes.
_NATIVE_COOLDOWN_SECONDS = 300.0
_native_cooldown_until: dict[tuple[str, str, str], float] = {}


def clear_native_cooldown() -> None:
    """Clear the process-local native Decisions endpoint negative cache."""
    _native_cooldown_until.clear()


def _is_native_in_cooldown(key: tuple[str, str, str]) -> bool:
    deadline = _native_cooldown_until.get(key)
    if deadline is None:
        return False
    if time.monotonic() >= deadline:
        _native_cooldown_until.pop(key, None)
        return False
    return True


def _mark_native_cooldown(key: tuple[str, str, str]) -> None:
    _native_cooldown_until[key] = time.monotonic() + _NATIVE_COOLDOWN_SECONDS


def _record_native_usage(response: DecisionResponse, *, call_type: str | None) -> None:
    try:
        from utils.token_tracker import get_call_type, get_token_tracker

        tracker = get_token_tracker()
        ct = call_type or get_call_type() or "decision"
        tracker.record(
            model=response.model,
            prompt_tokens=response.prompt_tokens,
            completion_tokens=response.completion_tokens,
            total_tokens=response.prompt_tokens + response.completion_tokens,
            cached_tokens=response.cached_tokens,
            call_type=ct,
            source=f"decision:{response.protocol}",
            success=True,
        )
    except Exception:
        # Telemetry must never break decision execution.
        pass


class DecisionClient:
    """High-level async client executing closed-set decisions over native or chat-fallback models."""

    def __init__(self, config_manager: Any | None = None) -> None:
        self._config_manager = config_manager

    async def _resolve_decision_config(self, config_manager: Any | None) -> dict[str, Any]:
        cm = config_manager or self._config_manager
        if cm is None:
            from utils.config_manager import get_config_manager

            cm = get_config_manager()
        if hasattr(cm, "aget_model_api_config"):
            return await cm.aget_model_api_config("decision")
        return cm.get_model_api_config("decision")

    async def _resolve_fallback_chat_config(
        self,
        primary_cfg: Mapping[str, Any],
        config_manager: Any | None,
    ) -> dict[str, Any] | None:
        fallback_model = str(primary_cfg.get("fallback_model") or "").strip()
        base_url = str(primary_cfg.get("base_url") or "").strip()
        api_key = str(primary_cfg.get("api_key") or "").strip()
        provider_type = primary_cfg.get("provider_type")
        if fallback_model and base_url:
            return {
                "model": fallback_model,
                "base_url": base_url,
                "api_key": api_key,
                "provider_type": provider_type,
            }

        cm = config_manager or self._config_manager
        if cm is None:
            try:
                from utils.config_manager import get_config_manager

                cm = get_config_manager()
            except Exception:
                cm = None
        if cm is None:
            return None

        for tier in ("emotion", "summary"):
            try:
                cfg = (
                    await cm.aget_model_api_config(tier)
                    if hasattr(cm, "aget_model_api_config")
                    else cm.get_model_api_config(tier)
                )
            except Exception:
                continue
            if cfg and cfg.get("model") and cfg.get("base_url"):
                return dict(cfg)
        return None

    async def decide(
        self,
        *,
        state: Any,
        questions: Mapping[str, DecisionQuestion],
        config: Mapping[str, Any] | None = None,
        config_manager: Any | None = None,
        timeout: float = 8.0,
        allow_fallback: bool = True,
        call_type: str | None = None,
    ) -> DecisionResponse:
        """Evaluate *questions* against *state* using the configured decision slot.

        When the configured slot uses a native Decisions API (``systemone``,
        ``openai_decisions``, ``gemini_decisions``, ``openrouter_decisions``),
        dispatches to the matching REST adapter and records token usage.
        If the native endpoint fails (or is in negative cooldown) and
        ``allow_fallback=True``, transparently falls back to
        :class:`ChatFallbackDecisionAdapter`.
        """
        if not questions:
            return DecisionResponse(answers={}, model="", protocol="noop")

        cfg = dict(config) if config is not None else await self._resolve_decision_config(config_manager)
        model = str(cfg.get("model") or "").strip()
        base_url = str(cfg.get("base_url") or "").strip()
        api_key = str(cfg.get("api_key") or "").strip()
        decision_url = str(cfg.get("decision_url") or "").strip()
        provider_type = cfg.get("provider_type")

        protocol = str(cfg.get("decision_protocol") or "").strip().lower()
        if not protocol:
            protocol = infer_decision_protocol(model, decision_url or base_url)

        if call_type:
            try:
                from utils.token_tracker import set_call_type

                set_call_type(call_type)
            except Exception:
                pass

        if is_native_decision_protocol(protocol):
            endpoint = resolve_decision_endpoint(protocol, base_url, decision_url)
            cooldown_key = (protocol, endpoint, model)
            if not allow_fallback or not _is_native_in_cooldown(cooldown_key):
                adapter = get_decision_adapter(protocol)
                try:
                    resp = await adapter.execute(
                        state=state,
                        questions=questions,
                        model=model,
                        base_url=base_url,
                        api_key=api_key,
                        decision_url=decision_url,
                        provider_type=provider_type,
                        timeout=timeout,
                    )
                    _record_native_usage(resp, call_type=call_type)
                    return resp
                except Exception as exc:
                    if isinstance(exc, DecisionProtocolError) and exc.permanent_route_error:
                        _mark_native_cooldown(cooldown_key)
                    if not allow_fallback:
                        raise
                    logger.warning(
                        "[DecisionClient] native protocol=%s model=%s failed (%s); falling back to chat",
                        protocol,
                        model,
                        type(exc).__name__,
                    )
            else:
                logger.debug(
                    "[DecisionClient] native protocol=%s model=%s in cooldown; using chat fallback",
                    protocol,
                    model,
                )

            fb_cfg = await self._resolve_fallback_chat_config(cfg, config_manager)
            if not fb_cfg:
                raise DecisionProtocolError(
                    f"native decision protocol {protocol} failed and no chat fallback model is configured",
                    protocol=protocol,
                )
            fallback_adapter = ChatFallbackDecisionAdapter()
            fb_resp = await fallback_adapter.execute(
                state=state,
                questions=questions,
                model=str(fb_cfg.get("model") or ""),
                base_url=str(fb_cfg.get("base_url") or ""),
                api_key=str(fb_cfg.get("api_key") or ""),
                provider_type=fb_cfg.get("provider_type"),
                timeout=timeout,
            )
            return DecisionResponse(
                answers=fb_resp.answers,
                model=fb_resp.model,
                protocol=fb_resp.protocol,
                prompt_tokens=fb_resp.prompt_tokens,
                completion_tokens=fb_resp.completion_tokens,
                cached_tokens=fb_resp.cached_tokens,
                used_fallback=True,
            )

        # Direct chat_fallback slot
        chat_model = model
        chat_base_url = base_url
        chat_api_key = api_key
        chat_provider_type = provider_type
        if not (chat_model and chat_base_url):
            fb_cfg = await self._resolve_fallback_chat_config(cfg, config_manager)
            if not fb_cfg:
                raise DecisionProtocolError(
                    "decision model is not configured",
                    protocol="chat_fallback",
                )
            chat_model = str(fb_cfg.get("model") or "")
            chat_base_url = str(fb_cfg.get("base_url") or "")
            chat_api_key = str(fb_cfg.get("api_key") or "")
            chat_provider_type = fb_cfg.get("provider_type")

        adapter = ChatFallbackDecisionAdapter()
        return await adapter.execute(
            state=state,
            questions=questions,
            model=chat_model,
            base_url=chat_base_url,
            api_key=chat_api_key,
            provider_type=chat_provider_type,
            timeout=timeout,
        )


async def decide(
    *,
    state: Any,
    questions: Mapping[str, DecisionQuestion],
    config: Mapping[str, Any] | None = None,
    config_manager: Any | None = None,
    timeout: float = 8.0,
    allow_fallback: bool = True,
    call_type: str | None = None,
) -> DecisionResponse:
    """Convenience entry point wrapping :meth:`DecisionClient.decide`."""
    client = DecisionClient(config_manager=config_manager)
    return await client.decide(
        state=state,
        questions=questions,
        config=config,
        config_manager=config_manager,
        timeout=timeout,
        allow_fallback=allow_fallback,
        call_type=call_type,
    )
