# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""New-user icebreaker endpoints.

This router intentionally does not use the game-route lifecycle. The
icebreaker can append context and speak fixed onboarding lines, but it must not
make ``/api/game/route/active`` report an open mini-game window.
"""
from __future__ import annotations

import asyncio
import re
from typing import Any, Dict

from fastapi import APIRouter, Request

from config import (
    ICEBREAKER_FREE_TEXT_ASSISTANT_LINE_MAX_TOKENS,
    ICEBREAKER_FREE_TEXT_INTERPRETER_TIMEOUT_SECONDS,
    ICEBREAKER_FREE_TEXT_OUTPUT_MAX_TOKENS,
    ICEBREAKER_FREE_TEXT_USER_TEXT_MAX_TOKENS,
)
from config.prompts.prompts_icebreaker import build_icebreaker_free_text_prompts
from main_logic.mirror_meta import build_mirror_meta
from utils.icebreaker_free_text import (
    normalize_icebreaker_free_text_derail_streak,
    normalize_icebreaker_free_text_options,
    normalize_icebreaker_recent_free_text_turns,
    parse_icebreaker_free_text_decision,
    trim_icebreaker_token_text,
)
from utils.icebreaker_route_state import (
    _get_active_icebreaker_route_state,
    _get_icebreaker_route_lock,
    _public_icebreaker_route_state,
    activate_icebreaker_route,
    finalize_icebreaker_route,
    touch_icebreaker_route,
)
from utils.language_utils import is_supported_language_code, normalize_language_code
from utils.llm_client import HumanMessage, SystemMessage, create_chat_llm_async
from utils.logger_config import get_module_logger
from utils.token_tracker import set_call_type
from utils.tutorial_choices_state import record_tutorial_choice

from .shared_state import get_config_manager, get_session_manager


logger = get_module_logger(__name__, "Icebreaker")
router = APIRouter(tags=["icebreaker"], prefix="/api/icebreaker")

ICEBREAKER_SOURCE = "new_user_icebreaker"
MAX_ICEBREAKER_CONTEXT_TEXT_LENGTH = 2000
ICEBREAKER_MEMORY_CACHE_TIMEOUT_SECONDS = 10.0
_SSML_TAG_PATTERN = re.compile(
    r"</?(?:[a-z][\w-]*:)?(?:"
    r"speak|p|s|break|say-as|phoneme|sub|prosody|emphasis|voice|audio|mark|lang|w|token|express-as|effect"
    r")(?:\s+[^<>\n]{0,120})?\s*/?>",
    re.IGNORECASE,
)


def _resolve_lanlan_name(raw: Any = None) -> str:
    lanlan_name = str(raw or "").strip()
    if lanlan_name:
        return lanlan_name
    try:
        characters = get_config_manager().load_characters()
        return str(characters.get("当前猫娘") or "").strip()
    except Exception:
        return ""


def _absorb_request_language(data: Any, lanlan_name: str | None) -> str | None:
    if not isinstance(data, dict):
        return None
    raw = data.get("i18n_language") or data.get("language") or data.get("lang")
    if not raw or not is_supported_language_code(raw):
        return None
    try:
        normalized_short = normalize_language_code(str(raw), format="short")
    except Exception:
        return None
    if not normalized_short:
        return None
    try:
        manager = get_session_manager().get(str(lanlan_name or "").strip())
        if manager is not None:
            normalized_full = normalize_language_code(str(raw), format="full")
            if normalized_full and getattr(manager, "user_language", None) != normalized_full:
                setter = getattr(manager, "set_user_language", None)
                if callable(setter):
                    setter(str(raw))
    except Exception:
        logger.debug("icebreaker absorb request language failed lanlan=%s", lanlan_name, exc_info=True)
    return normalized_short


def _strip_ssml_like_tags(text: str) -> str:
    line = _SSML_TAG_PATTERN.sub("", str(text or ""))
    line = re.sub(r"\s+", " ", line).strip()
    return line[:240]


def _coerce_payload_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off"}:
            return False
    return None


def _build_icebreaker_memory_message(role: str, text: str) -> dict | None:
    normalized_role = str(role or "").strip().lower()
    if normalized_role not in {"assistant", "user"}:
        return None
    content = str(text or "").strip()
    if not content:
        return None
    return {
        "role": normalized_role,
        "content": [{"type": "text", "text": content}],
    }


async def _cache_icebreaker_context_memory(*, lanlan_name: str, role: str, text: str) -> tuple[bool, str]:
    message = _build_icebreaker_memory_message(role, text)
    if message is None:
        return False, "invalid_memory_message"
    try:
        from main_logic.cross_server import _post_memory_server

        ok, err_detail, _ = await _post_memory_server(
            "cache",
            lanlan_name,
            [message],
            timeout_s=ICEBREAKER_MEMORY_CACHE_TIMEOUT_SECONDS,
        )
        return bool(ok), str(err_detail or "")
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def _stale_icebreaker_session_response(state: dict | None, session_id: str, *, lanlan_name: str, method: str) -> dict | None:
    if not (state and session_id and session_id != str(state.get("session_id") or "")):
        return None
    result: Dict[str, Any] = {
        "ok": True,
        "skipped": "stale_session",
        "reason": "session_id_mismatch",
        "handled": False,
        "lanlan_name": lanlan_name,
        "method": method,
        "state": _public_icebreaker_route_state(state),
    }
    if method == "project_tts":
        result.update({
            "audio_sent": False,
            "audio_committed": False,
            "voice_source": {
                "provider": "project_tts",
                "method": "project_tts",
                "skipped": "stale_session",
            },
        })
    return result


def _validate_icebreaker_local_mutation(request: Request, data: dict) -> Any:
    from .system_router import _validate_local_mutation_request

    return _validate_local_mutation_request(
        request,
        payload=data,
        error_defaults={"ok": False, "reason": "csrf_validation_failed"},
    )


async def _speak_icebreaker_line_via_project_tts(
    mgr: Any,
    line: str,
    *,
    request_id: str | None = None,
    session_id: str = "",
    mirror_text: bool = True,
    emit_turn_end: bool = True,
    interrupt_audio: bool = False,
    event: dict | None = None,
) -> Dict[str, Any]:
    speak = getattr(mgr, "mirror_assistant_speech", None)
    if not callable(speak):
        return {"ok": False, "reason": "project_tts_method_unavailable", "audio_sent": False}
    metadata = build_mirror_meta(
        source=ICEBREAKER_SOURCE,
        kind=ICEBREAKER_SOURCE,
        session_id=session_id,
        event=event if isinstance(event, dict) else {},
    )
    return await speak(
        line,
        metadata=metadata,
        request_id=request_id,
        mirror_text=mirror_text,
        emit_turn_end_after=emit_turn_end,
        interrupt_audio=interrupt_audio,
    )


@router.post("/route/start")
async def icebreaker_route_start(request: Request):
    try:
        data = await request.json()
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    validation_error = _validate_icebreaker_local_mutation(request, data)
    if validation_error is not None:
        return validation_error

    lanlan_name = _resolve_lanlan_name(data.get("lanlan_name"))
    if not lanlan_name:
        return {"ok": False, "reason": "missing_lanlan_name"}
    _absorb_request_language(data, lanlan_name)
    session_id = str(data.get("session_id") or "")
    if not session_id:
        return {"ok": False, "reason": "missing_session_id"}

    async with _get_icebreaker_route_lock(lanlan_name):
        state = activate_icebreaker_route(lanlan_name, session_id)
    return {"ok": True, "state": _public_icebreaker_route_state(state)}


@router.post("/route/end")
async def icebreaker_route_end(request: Request):
    try:
        data = await request.json()
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    validation_error = _validate_icebreaker_local_mutation(request, data)
    if validation_error is not None:
        return validation_error

    lanlan_name = _resolve_lanlan_name(data.get("lanlan_name"))
    if not lanlan_name:
        return {"ok": False, "reason": "missing_lanlan_name"}
    session_id = str(data.get("session_id") or "")
    reason = str(data.get("reason") or "icebreaker_end")
    async with _get_icebreaker_route_lock(lanlan_name):
        active_state = _get_active_icebreaker_route_state(lanlan_name)
        if active_state and session_id and session_id != str(active_state.get("session_id") or ""):
            return {
                "ok": False,
                "reason": "session_id_mismatch",
                "handled": False,
                "lanlan_name": lanlan_name,
                "method": "route_end",
                "state": _public_icebreaker_route_state(active_state),
            }
        state = finalize_icebreaker_route(lanlan_name, session_id=session_id, reason=reason)
    return {"ok": True, "state": _public_icebreaker_route_state(state)}


@router.get("/route/state")
async def icebreaker_route_state(lanlan_name: str = ""):
    resolved = _resolve_lanlan_name(lanlan_name)
    state = _get_active_icebreaker_route_state(resolved) if resolved else None
    return {"ok": True, "state": _public_icebreaker_route_state(state)}


@router.post("/context")
async def icebreaker_context(request: Request):
    try:
        data = await request.json()
    except Exception:
        return {"ok": False, "reason": "invalid_body"}
    if not isinstance(data, dict):
        return {"ok": False, "reason": "invalid_body"}

    validation_error = _validate_icebreaker_local_mutation(request, data)
    if validation_error is not None:
        return validation_error

    role = str(data.get("role") or "").strip()
    text = str(data.get("text") or "").strip()
    if role not in {"assistant", "user"}:
        return {"ok": False, "reason": "invalid_role"}
    if not text:
        return {"ok": False, "reason": "missing_text"}
    if len(text) > MAX_ICEBREAKER_CONTEXT_TEXT_LENGTH:
        return {"ok": False, "reason": "invalid_text_length"}
    if "lanlan_name" not in data or data.get("lanlan_name") is None or str(data.get("lanlan_name") or "").strip() == "":
        return {"ok": False, "reason": "missing_lanlan_name"}

    lanlan_name = _resolve_lanlan_name(data.get("lanlan_name"))
    _absorb_request_language(data, lanlan_name)
    requested_session_id = str(data.get("session_id") or "")
    event = data.get("event") if isinstance(data.get("event"), dict) else {}
    request_id = str(data.get("request_id") or event.get("request_id") or "").strip()
    state = _get_active_icebreaker_route_state(lanlan_name)
    if not state:
        return {
            "ok": False,
            "reason": "route_not_active",
            "lanlan_name": lanlan_name,
            "source": ICEBREAKER_SOURCE,
            "method": "project_session_history",
        }
    stale_response = _stale_icebreaker_session_response(
        state,
        requested_session_id,
        lanlan_name=lanlan_name,
        method="project_session_history",
    )
    if stale_response:
        return stale_response
    session_id = requested_session_id or str(state.get("session_id") or "")

    mgr = get_session_manager().get(lanlan_name)
    if not mgr:
        return {"ok": False, "reason": "no_session_manager", "lanlan_name": lanlan_name}

    append_context = getattr(mgr, "append_context", None)
    try:
        if not callable(append_context):
            return {"ok": False, "reason": "context_method_unavailable", "lanlan_name": lanlan_name}
        append_result = await append_context(
            source="icebreaker",
            role=role,
            text=text,
            audience="model",
            timing="when_ready",
            lifetime="session_family",
            request_id=request_id or None,
            ordering_key=session_id or None,
            metadata={
                "source": ICEBREAKER_SOURCE,
                "session_id": session_id,
            },
        )
    except Exception as exc:
        logger.warning("icebreaker context append failed for %s: %s", lanlan_name, exc, exc_info=True)
        return {
            "ok": False,
            "reason": "context_write_failed",
            "error": str(exc),
            "lanlan_name": lanlan_name,
            "source": ICEBREAKER_SOURCE,
            "session_id": session_id,
        }
    if getattr(append_result, "deduped", False):
        return {
            "ok": True,
            "deduped": True,
            "method": "project_session_history",
            "lanlan_name": lanlan_name,
            "source": ICEBREAKER_SOURCE,
            "session_id": session_id,
            "memory_cached": False,
        }
    ok = getattr(append_result, "appended", False)
    if not ok:
        return {
            "ok": False,
            "reason": getattr(append_result, "reason", None) or "context_write_failed",
            "lanlan_name": lanlan_name,
            "source": ICEBREAKER_SOURCE,
            "session_id": session_id,
        }

    memory_cached, memory_cache_error = await _cache_icebreaker_context_memory(
        lanlan_name=lanlan_name,
        role=role,
        text=text,
    )
    if not memory_cached:
        logger.warning(
            "icebreaker memory cache failed for %s role=%s session=%s: %s",
            lanlan_name,
            role,
            session_id,
            memory_cache_error,
        )

    touch_icebreaker_route(state)
    result = {
        "ok": True,
        "method": "project_session_history",
        "lanlan_name": lanlan_name,
        "source": ICEBREAKER_SOURCE,
        "session_id": session_id,
        "memory_cached": memory_cached,
    }
    return result


@router.post("/free-text/interpret")
async def icebreaker_free_text_interpret(request: Request):
    try:
        data = await request.json()
    except Exception:
        return {"ok": False, "reason": "invalid_body"}
    if not isinstance(data, dict):
        return {"ok": False, "reason": "invalid_body"}

    validation_error = _validate_icebreaker_local_mutation(request, data)
    if validation_error is not None:
        return validation_error

    lanlan_name = _resolve_lanlan_name(data.get("lanlan_name"))
    if not lanlan_name:
        return {"ok": False, "reason": "missing_lanlan_name"}

    user_text = str(data.get("user_text") or "").strip()
    if not user_text:
        return {"ok": False, "reason": "missing_user_text"}
    requested_session_id = str(data.get("session_id") or "")
    if not requested_session_id:
        return {"ok": False, "reason": "missing_session_id", "lanlan_name": lanlan_name}
    state = _get_active_icebreaker_route_state(lanlan_name)
    if not state:
        return {
            "ok": False,
            "reason": "route_not_active",
            "lanlan_name": lanlan_name,
            "source": ICEBREAKER_SOURCE,
            "method": "free_text_interpreter",
        }
    stale_response = _stale_icebreaker_session_response(
        state,
        requested_session_id,
        lanlan_name=lanlan_name,
        method="free_text_interpreter",
    )
    if stale_response:
        return stale_response

    _absorb_request_language(data, lanlan_name)

    options = normalize_icebreaker_free_text_options(data.get("options"))
    if not options:
        return {"ok": False, "reason": "missing_options", "lanlan_name": lanlan_name}

    try:
        api_config = get_config_manager().get_model_api_config("emotion")
    except Exception as exc:
        logger.warning("icebreaker free-text: failed to read emotion API config: %s", exc)
        return {"ok": False, "reason": "llm_api_not_configured", "error": str(exc)}
    model = (api_config or {}).get("model")
    if not model:
        return {"ok": False, "reason": "llm_api_not_configured", "error": "emotion model not set"}

    llm = None
    try:
        llm = await create_chat_llm_async(
            model,
            (api_config or {}).get("base_url"),
            (api_config or {}).get("api_key"),
            provider_type=(api_config or {}).get("provider_type"),
            timeout=ICEBREAKER_FREE_TEXT_INTERPRETER_TIMEOUT_SECONDS,
            max_retries=1,
            max_completion_tokens=ICEBREAKER_FREE_TEXT_OUTPUT_MAX_TOKENS,
        )
        prompt_data = dict(data)
        prompt_data["assistant_line"] = trim_icebreaker_token_text(
            data.get("assistant_line"),
            ICEBREAKER_FREE_TEXT_ASSISTANT_LINE_MAX_TOKENS,
        )
        prompt_data["user_text"] = trim_icebreaker_token_text(
            data.get("user_text"),
            ICEBREAKER_FREE_TEXT_USER_TEXT_MAX_TOKENS,
        )
        system_prompt, user_prompt = build_icebreaker_free_text_prompts(
            prompt_data,
            options,
            recent_turns=normalize_icebreaker_recent_free_text_turns(data.get("recent_free_text_turns")),
            derail_streak=normalize_icebreaker_free_text_derail_streak(data.get("free_text_derail_streak")),
        )
        set_call_type("icebreaker")
        response = await llm.ainvoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ])
        decision = parse_icebreaker_free_text_decision(getattr(response, "content", ""))
    except Exception as exc:
        logger.warning("icebreaker free-text interpreter failed for %s: %s", lanlan_name, exc, exc_info=True)
        return {"ok": False, "reason": "free_text_interpreter_failed", "error": str(exc)}
    finally:
        if llm is not None:
            try:
                await llm.aclose()
            except Exception:
                logger.debug("icebreaker free-text llm close failed", exc_info=True)

    touch_icebreaker_route(state)
    return {"ok": True, **decision}


@router.post("/choice")
async def icebreaker_choice(request: Request):
    """Persist a single effective tutorial choice into the durable choices pool.

    Write-only for now: the choice is recorded so it survives across sessions,
    but it does not enter the memory system and does not influence the model.
    Kept separate from ``/context`` (which feeds transient session history) so the
    pool stays an independent signal we can consume incrementally later.
    """
    try:
        data = await request.json()
    except Exception:
        return {"ok": False, "reason": "invalid_body"}
    if not isinstance(data, dict):
        return {"ok": False, "reason": "invalid_body"}

    validation_error = _validate_icebreaker_local_mutation(request, data)
    if validation_error is not None:
        return validation_error

    lanlan_name = _resolve_lanlan_name(data.get("lanlan_name"))
    if not lanlan_name:
        return {"ok": False, "reason": "missing_lanlan_name"}

    requested_session_id = str(data.get("session_id") or "")
    # 选项必须属于当前 active 的破冰 route，才能写进「有效路径」池——与 /context、
    # /speak 完全对齐：无 active route → route_not_active；session 不匹配（双 tab 下
    # 新 session 顶掉旧的）→ stale 拒绝。这样被取代的旧 tab、以及 route 结束后迟到的
    # 旧请求都进不来，池子不被污染。
    # 不必担心丢掉收尾的 handoff 选择：前端 recordChoiceToPool 在 handleChoice 开头
    # 同步发起本请求，而 route/end 要等之后两次 appendChatMessage（各含一个 /context
    # 往返）才发起，本请求实质上比 route/end 早约两个往返，落地时 route 仍 active。
    active_state = _get_active_icebreaker_route_state(lanlan_name)
    if not active_state:
        return {
            "ok": False,
            "reason": "route_not_active",
            "lanlan_name": lanlan_name,
            "source": ICEBREAKER_SOURCE,
            "method": "tutorial_choices",
        }
    stale_response = _stale_icebreaker_session_response(
        active_state,
        requested_session_id,
        lanlan_name=lanlan_name,
        method="tutorial_choices",
    )
    if stale_response:
        return stale_response

    payload = {
        "lanlan_name": lanlan_name,
        "session_id": data.get("session_id"),
        "day": data.get("day"),
        "node_id": data.get("node_id"),
        "choice": data.get("choice"),
        "label": data.get("label"),
        "handoff": data.get("handoff"),
        "completed": data.get("completed"),
        "seq": data.get("seq"),
        "source": ICEBREAKER_SOURCE,
    }
    try:
        result = await asyncio.to_thread(record_tutorial_choice, payload)
    except Exception as exc:
        logger.warning("icebreaker choice persist failed for %s: %s", lanlan_name, exc, exc_info=True)
        return {
            "ok": False,
            "reason": "choice_write_failed",
            "error": str(exc),
            "lanlan_name": lanlan_name,
            "source": ICEBREAKER_SOURCE,
        }
    result.setdefault("source", ICEBREAKER_SOURCE)
    return result


@router.post("/speak")
async def icebreaker_speak(request: Request):
    try:
        data = await request.json()
    except Exception:
        return {"ok": False, "reason": "invalid_body"}
    if not isinstance(data, dict):
        return {"ok": False, "reason": "invalid_body"}
    validation_error = _validate_icebreaker_local_mutation(request, data)
    if validation_error is not None:
        return validation_error

    line = _strip_ssml_like_tags(str(data.get("line") or "").strip())
    if not line:
        return {"ok": False, "reason": "missing_line"}
    lanlan_name = _resolve_lanlan_name(data.get("lanlan_name"))
    if not lanlan_name:
        return {"ok": False, "reason": "missing_lanlan_name"}
    _absorb_request_language(data, lanlan_name)
    requested_session_id = str(data.get("session_id") or "")
    state = _get_active_icebreaker_route_state(lanlan_name)
    if not state:
        return {
            "ok": False,
            "reason": "route_not_active",
            "lanlan_name": lanlan_name,
            "source": ICEBREAKER_SOURCE,
            "method": "project_tts",
            "audio_sent": False,
        }
    stale_response = _stale_icebreaker_session_response(
        state,
        requested_session_id,
        lanlan_name=lanlan_name,
        method="project_tts",
    )
    if stale_response:
        return stale_response
    session_id = requested_session_id or str(state.get("session_id") or "")
    mgr = get_session_manager().get(lanlan_name)
    if not mgr:
        return {"ok": False, "reason": "no_session_manager", "lanlan_name": lanlan_name}

    try:
        result = await _speak_icebreaker_line_via_project_tts(
            mgr,
            line,
            request_id=str(data.get("request_id") or "") or None,
            session_id=session_id,
            mirror_text=_coerce_payload_bool(data.get("mirror_text", True)) is not False,
            emit_turn_end=_coerce_payload_bool(data.get("emit_turn_end", True)) is not False,
            interrupt_audio=_coerce_payload_bool(data.get("interrupt_audio")) is True,
            event=data.get("event") if isinstance(data.get("event"), dict) else {},
        )
    except Exception as exc:
        logger.warning("icebreaker project_tts failed for %s: %s", lanlan_name, exc, exc_info=True)
        return {
            "ok": False,
            "reason": "project_tts_failed",
            "error": str(exc),
            "lanlan_name": lanlan_name,
            "source": ICEBREAKER_SOURCE,
            "session_id": session_id,
            "method": "project_tts",
            "audio_sent": False,
            "voice_source": {"provider": "project_tts", "method": "project_tts"},
        }
    touch_icebreaker_route(state)
    result.setdefault("lanlan_name", lanlan_name)
    result.setdefault("method", "project_tts")
    result.setdefault("voice_source", {"provider": "project_tts", "method": "project_tts"})
    return result
