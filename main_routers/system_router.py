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

"""
System Router

Handles system-related endpoints including:
- Server shutdown
- Emotion analysis
- Steam achievements
- File utilities (file-exists, find-first-image, proxy-image)

URL convention: routes declared WITHOUT trailing slash (no ``@router.get('/')``).
See ``main_routers/characters_router.py`` docstring or
``.agent/rules/neko-guide.md`` (§"API URL 末尾不带斜杠") for the rationale;
enforced by ``scripts/check_api_trailing_slash.py``.
"""

import os
import sys
import asyncio
import base64
import difflib
import hashlib
import hmac
import ipaddress
import json
import math
import random
import re
import secrets
import shutil
import subprocess
import tempfile
import time
from collections import deque
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit
from uuid import uuid4

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from openai import APIConnectionError, InternalServerError, RateLimitError
from utils.llm_client import (
    SystemMessage,
    HumanMessage,
    ThinkingStreamStripper,
    anthropic_retry_error_types,
    create_chat_llm_async,
)
from utils.tokenize import count_tokens
import ssl
import httpx
from PIL import Image

# Phase 2 proactive output ceiling. The model occasionally runs off; this
# fence cuts the stream and aborts TTS once the running output exceeds the
# token budget. We use sync `count_tokens` here on purpose:
#   - At fence time `full_text` is < 1 KB (we abort at 300 tokens ≈ 400 CJK
#     chars); tiktoken Rust encode of that size is sub-millisecond.
#   - tiktoken's Rust core releases the GIL inside `encode`, so a sync call
#     does NOT block other coroutines' IO callbacks for any meaningful time.
#   - `asyncio.to_thread` adds ~0.1 ms scheduling overhead per call (warmed
#     thread pool) — 3-4× the actual encode work. Across a 30-chunk stream
#     that's a few milliseconds saved per turn, but more importantly avoids
#     the cold-start case where the first thread hop can take much longer.
from cachetools import TTLCache

_PROACTIVE_LLM_RETRY_ERROR_TYPES = (
    asyncio.TimeoutError,
    APIConnectionError,
    InternalServerError,
    RateLimitError,
    *anthropic_retry_error_types(),
)

PROACTIVE_REASON_CHAT_DELIVERED = "CHAT_DELIVERED"
PROACTIVE_REASON_PASS_BUSY = "PASS_BUSY"
PROACTIVE_REASON_PASS_ACTIVITY_BUSY = "PASS_ACTIVITY_BUSY"
PROACTIVE_REASON_PASS_DELIVERY_BUSY = "PASS_DELIVERY_BUSY"
PROACTIVE_REASON_PASS_DISABLED = "PASS_DISABLED"
PROACTIVE_REASON_PASS_ROUTE_ACTIVE = "PASS_ROUTE_ACTIVE"
PROACTIVE_REASON_PASS_PRIVACY = "PASS_PRIVACY"
PROACTIVE_REASON_PASS_RESTRICTED_SCREEN_ONLY = "PASS_RESTRICTED_SCREEN_ONLY"
PROACTIVE_REASON_PASS_THROTTLED = "PASS_THROTTLED"
PROACTIVE_REASON_PASS_SOURCE_EMPTY = "PASS_SOURCE_EMPTY"
PROACTIVE_REASON_PASS_MODEL_PASS = "PASS_MODEL_PASS"
PROACTIVE_REASON_PASS_GENERATION_EMPTY = "PASS_GENERATION_EMPTY"
PROACTIVE_REASON_PASS_DUPLICATE = "PASS_DUPLICATE"
PROACTIVE_REASON_DELIVERY_PREEMPTED = "DELIVERY_PREEMPTED"
PROACTIVE_REASON_DELIVERY_FAILED = "DELIVERY_FAILED"
PROACTIVE_REASON_ERROR_TIMEOUT = "ERROR_TIMEOUT"
PROACTIVE_REASON_ERROR_INTERNAL = "ERROR_INTERNAL"
PROACTIVE_REASON_ERROR_CHARACTER_NOT_FOUND = "ERROR_CHARACTER_NOT_FOUND"
PROACTIVE_REASON_ERROR_SOURCE_FETCH_FAILED = "ERROR_SOURCE_FETCH_FAILED"
PROACTIVE_REASON_PASS_UNSPECIFIED = "PASS_UNSPECIFIED"

PROACTIVE_STAGE_ENTRY_GUARD = "entry_guard"
PROACTIVE_STAGE_ACTIVITY_GATE = "activity_gate"
PROACTIVE_STAGE_SOURCE_SELECTION = "source_selection"
PROACTIVE_STAGE_MODEL_DECISION = "model_decision"
PROACTIVE_STAGE_GENERATION = "generation"
PROACTIVE_STAGE_DEDUP = "dedup"
PROACTIVE_STAGE_DELIVERY = "delivery"
PROACTIVE_STAGE_RUNTIME_ERROR = "runtime_error"
PROACTIVE_STAGE_UNKNOWN = "unknown"

_PROACTIVE_REASON_STAGE: dict[str, str] = {
    PROACTIVE_REASON_CHAT_DELIVERED: PROACTIVE_STAGE_DELIVERY,
    PROACTIVE_REASON_PASS_BUSY: PROACTIVE_STAGE_ENTRY_GUARD,
    PROACTIVE_REASON_PASS_ACTIVITY_BUSY: PROACTIVE_STAGE_ACTIVITY_GATE,
    PROACTIVE_REASON_PASS_DELIVERY_BUSY: PROACTIVE_STAGE_DELIVERY,
    PROACTIVE_REASON_PASS_DISABLED: PROACTIVE_STAGE_ENTRY_GUARD,
    PROACTIVE_REASON_PASS_ROUTE_ACTIVE: PROACTIVE_STAGE_ENTRY_GUARD,
    PROACTIVE_REASON_PASS_PRIVACY: PROACTIVE_STAGE_ACTIVITY_GATE,
    PROACTIVE_REASON_PASS_RESTRICTED_SCREEN_ONLY: PROACTIVE_STAGE_ACTIVITY_GATE,
    PROACTIVE_REASON_PASS_THROTTLED: PROACTIVE_STAGE_ACTIVITY_GATE,
    PROACTIVE_REASON_PASS_SOURCE_EMPTY: PROACTIVE_STAGE_SOURCE_SELECTION,
    PROACTIVE_REASON_PASS_MODEL_PASS: PROACTIVE_STAGE_MODEL_DECISION,
    PROACTIVE_REASON_PASS_GENERATION_EMPTY: PROACTIVE_STAGE_GENERATION,
    PROACTIVE_REASON_PASS_DUPLICATE: PROACTIVE_STAGE_DEDUP,
    PROACTIVE_REASON_DELIVERY_PREEMPTED: PROACTIVE_STAGE_DELIVERY,
    PROACTIVE_REASON_DELIVERY_FAILED: PROACTIVE_STAGE_DELIVERY,
    PROACTIVE_REASON_ERROR_TIMEOUT: PROACTIVE_STAGE_RUNTIME_ERROR,
    PROACTIVE_REASON_ERROR_INTERNAL: PROACTIVE_STAGE_RUNTIME_ERROR,
    PROACTIVE_REASON_ERROR_CHARACTER_NOT_FOUND: PROACTIVE_STAGE_ENTRY_GUARD,
    PROACTIVE_REASON_ERROR_SOURCE_FETCH_FAILED: PROACTIVE_STAGE_SOURCE_SELECTION,
    PROACTIVE_REASON_PASS_UNSPECIFIED: PROACTIVE_STAGE_UNKNOWN,
}


def _proactive_stage_for_reason(reason_code: str | None) -> str:
    if not reason_code:
        return PROACTIVE_STAGE_UNKNOWN
    return _PROACTIVE_REASON_STAGE.get(reason_code, PROACTIVE_STAGE_UNKNOWN)


def _proactive_response_body(
    action: str | None,
    reason_code: str,
    *,
    success: bool,
    **extra: Any,
) -> dict[str, Any]:
    body: dict[str, Any] = {"success": success, "reason_code": reason_code}
    if action is not None:
        body["action"] = action
    body.update(extra)
    body["reason_code"] = reason_code
    if not body.get("stage"):
        body["stage"] = _proactive_stage_for_reason(reason_code)
    if action is not None:
        body["action"] = action
    return body


def _proactive_pass_body(reason_code: str, **extra: Any) -> dict[str, Any]:
    success = bool(extra.pop("success", True))
    return _proactive_response_body("pass", reason_code, success=success, **extra)


def _proactive_chat_body(
    reason_code: str = PROACTIVE_REASON_CHAT_DELIVERED,
    **extra: Any,
) -> dict[str, Any]:
    success = bool(extra.pop("success", True))
    return _proactive_response_body("chat", reason_code, success=success, **extra)


def _proactive_error_body(reason_code: str, **extra: Any) -> dict[str, Any]:
    success = bool(extra.pop("success", False))
    return _proactive_response_body(None, reason_code, success=success, **extra)


def _ensure_proactive_reason_code(
    body: dict[str, Any],
    *,
    default_reason_code: str | None = None,
) -> dict[str, Any]:
    existing_reason_code = body.get("reason_code")
    if existing_reason_code:
        if not body.get("stage"):
            body["stage"] = _proactive_stage_for_reason(str(existing_reason_code))
        return body
    action = body.get("action")
    if default_reason_code is None:
        if action == "chat":
            default_reason_code = PROACTIVE_REASON_CHAT_DELIVERED
        elif action == "pass":
            default_reason_code = PROACTIVE_REASON_PASS_UNSPECIFIED
        else:
            default_reason_code = PROACTIVE_REASON_ERROR_INTERNAL
    body["reason_code"] = default_reason_code
    if not body.get("stage"):
        body["stage"] = _proactive_stage_for_reason(default_reason_code)
    return body

from .shared_state import ensure_steamworks as get_steamworks, get_config_manager, get_sync_message_queue, get_session_manager
from main_logic.omni_realtime_client import OmniRealtimeClient
from main_logic.activity.system_signals import is_remote_backend_deployment
from main_logic.activity.tracker import _EXTERNAL_SIGNAL_MIN_INTERVAL
from config import (
    AUTOSTART_ALLOWED_ORIGINS,
    AUTOSTART_CSRF_TOKEN,
    MEMORY_SERVER_PORT,
    get_extra_body,
    focus_extra_body,
    leaks_thinking_in_content,
    PROACTIVE_PHASE1_FETCH_PER_SOURCE,
    PROACTIVE_PHASE1_TOTAL_TOPICS,
    PROACTIVE_EXTERNAL_PER_ITEM_MAX_TOKENS,
    PROACTIVE_EXTERNAL_TOTAL_MAX_TOKENS,
    PROACTIVE_PHASE2_OUTPUT_MAX_TOKENS as PHASE2_OUTPUT_MAX_TOKENS,
    PROACTIVE_PHASE2_GENERATE_MAX_TOKENS,
    PROACTIVE_PHASE1_UNIFIED_MAX_TOKENS,
    PROACTIVE_CHAT_HISTORY_MAX,
    ANTI_REPEAT_DROP_THRESHOLD,
    ANTI_REPEAT_INJECT_TOP_K,
    ANTI_REPEAT_REGEN_THRESHOLD,
    ANTI_REPEAT_EXEMPT_SOURCE_TAGS,
    MINI_GAME_INVITE_ENABLED,
    MINI_GAME_INVITE_FORCE_GAME_TYPE,
    MINI_GAME_INVITE_TRIGGER_PROBABILITY,
    MINI_GAME_INVITE_COOLDOWN_AFTER_ACCEPT_SECONDS,
    MINI_GAME_INVITE_COOLDOWN_AFTER_DECLINE_SECONDS,
    MINI_GAME_INVITE_COOLDOWN_CHATS,
    MINI_GAME_INVITE_NEW_USER_FORCE_AT,
    MINI_GAME_INVITE_AVAILABLE_GAMES,
    MINI_GAME_INVITE_LATER_SUPPRESS_SECONDS,
    MINI_GAME_LAUNCH_URL_BY_GAME,
    PROACTIVE_SOURCE_HARD_SKIP_SECONDS,
    PROACTIVE_SOURCE_HALF_LIFE_BY_KIND,
    PROACTIVE_SOURCE_HALF_LIFE_DEFAULT,
    PROACTIVE_SOURCE_FORGET_P,
    EMOTION_ANALYSIS_MAX_TOKENS,
)
from config.prompts.prompts_sys import _loc
from config.prompts.prompts_emotion import (
    get_outward_emotion_analysis_prompt,
    get_emotion_keywords_flat,
    get_angry_attack_patterns_flat,
    get_sad_vulnerable_patterns_flat,
    get_happy_playful_patterns_flat,
    get_heuristic_negation_tokens_flat,
    get_heuristic_tight_negation_tokens_flat,
    get_heuristic_negation_blocklist_flat,
    get_heuristic_contrast_conjunctions_flat,
    get_emotion_label_aliases_flat,
)
from config.prompts.prompts_directives import render_regen_avoid_instruction, render_format_fix_instruction
from config.prompts.prompts_proactive import (
    get_proactive_screen_prompt, get_proactive_generate_prompt,
    get_proactive_music_playing_hint,
    get_proactive_music_unknown_track_name,
    get_proactive_music_failsafe_hint,
    get_proactive_music_strict_constraint,
    get_proactive_format_sections,
    get_screen_section_header, get_screen_section_footer, get_screen_img_hint,
    RECENT_PROACTIVE_CHATS_HEADER, RECENT_PROACTIVE_CHATS_FOOTER,
    RECENT_PROACTIVE_TIME_LABELS, RECENT_PROACTIVE_CHANNEL_LABELS,
    BEGIN_GENERATE,
    SCREEN_WINDOW_TITLE,
    EXTERNAL_TOPIC_HEADER, EXTERNAL_TOPIC_FOOTER,
    MUSIC_SECTION_HEADER, MUSIC_SECTION_FOOTER,
    MEME_SECTION_HEADER, MEME_SECTION_FOOTER,
    get_meme_topic_line,
    PROACTIVE_SOURCE_LABELS,
    PROACTIVE_MUSIC_TAG_INSTRUCTIONS,
    MUSIC_SEARCH_RESULT_TEXTS,
    MINI_GAME_INVITE_LINES_BY_GAME,
    MINI_GAME_INVITE_OPTION_LABELS,
    MINI_GAME_INVITE_KEYWORDS,
    build_proactive_action_note,
)
from utils.file_utils import atomic_write_json_async, read_json
from utils.workshop_utils import get_workshop_path
from utils.screenshot_utils import (
    compress_screenshot,
    decode_and_compress_screenshot_b64,
    COMPRESS_TARGET_HEIGHT,
    COMPRESS_JPEG_QUALITY,
)
from utils.language_utils import detect_language, translate_text, normalize_language_code, get_global_language, get_global_language_full, is_supported_language_code
from utils.web_scraper import (
    fetch_trending_content, format_trending_content,
    fetch_window_context_content, format_window_context_content,
    fetch_video_content, format_video_content,
    fetch_news_content, format_news_content,
    fetch_personal_dynamics, format_personal_dynamics,
)
from utils.music_crawlers import fetch_music_content
from utils.meme_fetcher import fetch_meme_content, MEME_ALLOWED_HOSTS
from utils.meme_moderation import moderate_meme_image_url
from utils.logger_config import get_module_logger
from utils.autostart_prompt_state import (
    get_autostart_prompt_state_response,
    process_autostart_prompt_heartbeat,
    record_autostart_prompt_shown,
    record_autostart_prompt_decision,
)
from utils.tutorial_prompt_state import (
    get_tutorial_prompt_state_response,
    process_tutorial_prompt_heartbeat,
    record_tutorial_prompt_shown,
    record_tutorial_prompt_decision,
    record_tutorial_started,
    record_tutorial_completed,
    reset_tutorial_prompt_state,
)
from utils.storage_location_bootstrap import build_storage_location_bootstrap_payload
from utils.config_manager import get_config_manager as get_runtime_config_manager
from config import APP_NAME

router = APIRouter(prefix="/api", tags=["system"])
logger = get_module_logger(__name__, "Main")
_AUTOSTART_CSRF_HEADER = "X-CSRF-Token"
_YUI_GUIDE_HANDOFF_TOKEN_VERSION = 1
_YUI_GUIDE_HANDOFF_FLOW_ID = "home_yui_guide_v1"
_YUI_GUIDE_HANDOFF_TTL_SECONDS = 5 * 60
_YUI_GUIDE_HANDOFF_MAX_RECORDS = 128
_YUI_GUIDE_HANDOFF_SECRET = secrets.token_bytes(32)
_yui_guide_handoff_lock = asyncio.Lock()
_yui_guide_handoff_tokens: dict[str, dict[str, Any]] = {}


def _set_no_store_headers(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"


def _is_loopback_request(request: Request) -> bool:
    client_host = request.client.host if request.client else ""
    if client_host == "localhost":
        return True
    normalized_host = str(client_host or "").removeprefix("::ffff:")
    try:
        return ipaddress.ip_address(normalized_host).is_loopback
    except ValueError:
        return False


# /screenshot 和 /screenshot/interactive 都是在后端机器上抓屏的，部署到
# 远程服务器时抓出来的是服务器自己的桌面而不是用户的。loopback 校验
# 会被反向代理 / 隧道绕过，``NEKO_ACTIVITY_TRACKER_REMOTE`` 是运维显式
# 声明"后端不在用户本机"的硬开关，命中就直接拒绝本地截图。
#
# 真正的实现在 ``main_logic/activity/system_signals.is_remote_backend_deployment``
# —— PR #1015 给 activity tracker 用的，这里直接复用避免再发明一套部署变量。
# 私有别名保留是为了 ``tests/unit/test_system_screenshot_router.py`` 还
# 在调 ``system_router_module._is_remote_backend_deployment()``。
_is_remote_backend_deployment = is_remote_backend_deployment


def _run_macos_interactive_screenshot(output_path: str) -> tuple[int, str]:
    cmd = shutil.which("screencapture")
    if not cmd:
        raise FileNotFoundError("screencapture not found")
    completed = subprocess.run(
        [cmd, "-i", "-s", "-x", output_path],
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.returncode, (completed.stderr or "").strip()


def _image_path_to_jpeg_data_url(image_path: str) -> tuple[str, int]:
    with Image.open(image_path) as shot:
        if shot.mode in ("RGBA", "LA", "P"):
            shot = shot.convert("RGB")
        jpg_bytes = compress_screenshot(
            shot,
            target_h=COMPRESS_TARGET_HEIGHT,
            quality=COMPRESS_JPEG_QUALITY,
        )
    b64 = base64.b64encode(jpg_bytes).decode("utf-8")
    return f"data:image/jpeg;base64,{b64}", len(jpg_bytes)


def _is_interactive_screenshot_canceled(platform_name: str, returncode: int, stderr: str, file_size: int) -> bool:
    if file_size > 0:
        return False
    normalized_stderr = str(stderr or "").strip()
    if returncode == 0:
        return True
    if platform_name == "darwin":
        return returncode == 1
    return returncode == 1 and not normalized_stderr


def _format_backend_screenshot_error(exc: Exception) -> str:
    text = str(exc or "").strip()
    lower = text.lower()
    if sys.platform.startswith("linux") and "gnome-screenshot" in lower and not shutil.which("gnome-screenshot"):
        return "gnome-screenshot not installed; install it with: sudo apt install gnome-screenshot"
    if "pillow" in lower:
        try:
            Image.new("RGB", (1, 1))
            if "gnome-screenshot" in lower:
                return "gnome-screenshot not installed; install it with: sudo apt install gnome-screenshot"
        except Exception:
            pass
    return text or type(exc).__name__


def _json_no_store_response(content: dict, status_code: int = 200) -> JSONResponse:
    response = JSONResponse(content, status_code=status_code)
    _set_no_store_headers(response)
    return response


def _derive_system_lifecycle_state(storage_bootstrap: dict[str, Any]) -> str:
    if not isinstance(storage_bootstrap, dict):
        return "starting"

    if (
        bool(storage_bootstrap.get("selection_required"))
        or bool(storage_bootstrap.get("migration_pending"))
        or bool(storage_bootstrap.get("recovery_required"))
        or bool(str(storage_bootstrap.get("blocking_reason") or "").strip())
    ):
        return "migration_required"

    return "ready"


def _build_public_error_response(
    *,
    error_code: str,
    status_code: int,
    result: dict | None = None,
    defaults: dict | None = None,
):
    public_messages = {
        "status_failed": "Failed to read autostart status",
        "enable_failed": "Failed to enable autostart",
        "disable_failed": "Failed to disable autostart",
        "unsupported_platform": "Autostart is not supported on this platform",
        "launch_command_unavailable": "Autostart launch command is unavailable",
        "csrf_validation_failed": "Request could not be verified",
    }

    content = {}
    if defaults:
        content.update(defaults)
    if result:
        content.update(result)

    content["ok"] = False
    content["error_code"] = error_code
    content["error"] = public_messages.get(error_code, "Operation failed")
    return JSONResponse(status_code=status_code, content=content)


def _normalize_origin_value(raw_value: str | None) -> str:
    if not raw_value:
        return ""

    try:
        parsed = urlsplit(raw_value.strip())
    except ValueError:
        return ""

    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""

    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}".rstrip("/")


def _get_request_origin(request: Request) -> str:
    origin = _normalize_origin_value(request.headers.get("origin"))
    if origin:
        return origin
    return _normalize_origin_value(request.headers.get("referer"))


def _get_system_config_manager():
    try:
        return get_config_manager()
    except RuntimeError:
        # The storage bootstrap sentinel must keep working during limited startup
        # even if main_server shared_state is not fully published yet.
        return get_runtime_config_manager(APP_NAME, migrate=False)


def _get_allowed_local_origins(request: Request) -> set[str]:
    allowed_origins = {
        normalized_origin
        for origin in AUTOSTART_ALLOWED_ORIGINS
        if isinstance(origin, str)
        if (normalized_origin := _normalize_origin_value(origin))
    }
    request_origin = _normalize_origin_value(str(request.base_url))
    if request_origin:
        allowed_origins.add(request_origin)
    return allowed_origins


def _validate_local_mutation_request(
    request: Request,
    *,
    payload: dict[str, Any] | None = None,
    error_defaults: dict[str, Any] | None = None,
) -> JSONResponse | None:
    csrf_token = request.headers.get(_AUTOSTART_CSRF_HEADER, "")
    if not csrf_token and payload:
        body_token = payload.get("_csrf_token")
        csrf_token = body_token if isinstance(body_token, str) else ""
    has_valid_csrf = bool(
        csrf_token
        and AUTOSTART_CSRF_TOKEN
        and secrets.compare_digest(csrf_token, AUTOSTART_CSRF_TOKEN)
    )
    request_origin = _get_request_origin(request)
    allowed_origins = _get_allowed_local_origins(request)
    has_valid_origin = bool(request_origin and request_origin in allowed_origins)

    if has_valid_csrf and has_valid_origin:
        return None

    logger.warning(
        "Rejected local mutation request due to failed CSRF/origin validation: "
        "method=%r path=%r origin=%r allowed_origins=%r has_csrf=%s referer=%r",
        request.method,
        request.url.path,
        request_origin,
        sorted(allowed_origins),
        has_valid_csrf,
        request.headers.get("referer"),
    )
    return _build_public_error_response(
        error_code="csrf_validation_failed",
        status_code=403,
        defaults=error_defaults,
    )


async def _safe_fire_proactive_done(scope: dict) -> None:
    """Safely reset the state machine from proactive_chat's exception-handling path.

    The exception may occur before PROACTIVE_START (mgr unbound, _SE not imported)
    or after it; look everything up via the locals() dict to avoid NameError. The
    state-machine fire itself is idempotent: when the state is already IDLE,
    PROACTIVE_DONE is just a no-op.
    """
    mgr = scope.get("mgr")
    se = scope.get("_SE")
    emitted = scope.get("_proactive_done_emitted", False)
    if mgr is None or se is None or emitted:
        return
    try:
        await mgr.state.fire(se.PROACTIVE_DONE)
    except Exception as err:  # 状态机不该抛，但兜底 swallow
        logger.warning("safe_fire_proactive_done 异常: %s", err)


async def _read_json_object(request: Request) -> dict[str, object]:
    """Read a JSON request body and normalize non-object payloads to {}."""
    try:
        payload = await request.json()
    except Exception:
        return {}

    return payload if isinstance(payload, dict) else {}


def _normalize_yui_handoff_text(value: object, *, max_length: int = 160) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()[:max_length]


def _build_yui_handoff_signature(record: dict[str, Any]) -> str:
    signed_fields = (
        str(record.get("token") or ""),
        str(record.get("token_version") or ""),
        str(record.get("flow_id") or ""),
        str(record.get("source_origin") or ""),
        str(record.get("source_page") or ""),
        str(record.get("source_path") or ""),
        str(record.get("target_page") or ""),
        str(record.get("target_path") or ""),
        str(record.get("resume_scene") or ""),
        str(record.get("expires_at") or ""),
    )
    message = "\n".join(signed_fields).encode("utf-8")
    return hmac.new(_YUI_GUIDE_HANDOFF_SECRET, message, hashlib.sha256).hexdigest()


def _public_yui_handoff_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "token": record.get("token", ""),
        "token_version": record.get("token_version", _YUI_GUIDE_HANDOFF_TOKEN_VERSION),
        "flow_id": record.get("flow_id", _YUI_GUIDE_HANDOFF_FLOW_ID),
        "source_page": record.get("source_page", ""),
        "source_path": record.get("source_path", ""),
        "target_page": record.get("target_page", ""),
        "target_path": record.get("target_path", ""),
        "resume_scene": record.get("resume_scene") or None,
        "created_at": record.get("created_at", 0),
        "expires_at": record.get("expires_at", 0),
        "consumed": bool(record.get("consumed_at")),
        "consumed_by": record.get("consumed_by", ""),
        "consumed_at": record.get("consumed_at", 0),
        "signature": record.get("signature", ""),
        "authority": "server",
    }


def _prune_yui_handoff_records(now_ms: int) -> None:
    expired_tokens = [
        token
        for token, record in _yui_guide_handoff_tokens.items()
        if int(record.get("expires_at", 0) or 0) <= now_ms
    ]
    for token in expired_tokens:
        _yui_guide_handoff_tokens.pop(token, None)

    if len(_yui_guide_handoff_tokens) <= _YUI_GUIDE_HANDOFF_MAX_RECORDS:
        return

    ordered_tokens = sorted(
        _yui_guide_handoff_tokens,
        key=lambda token: int(_yui_guide_handoff_tokens[token].get("created_at", 0) or 0),
    )
    overflow = len(_yui_guide_handoff_tokens) - _YUI_GUIDE_HANDOFF_MAX_RECORDS
    for token in ordered_tokens[:overflow]:
        _yui_guide_handoff_tokens.pop(token, None)


@router.post("/yui-guide/handoff/create")
async def create_yui_guide_handoff(request: Request):
    payload = await _read_json_object(request)
    validation_error = _validate_local_mutation_request(request, payload=payload)
    if validation_error is not None:
        _set_no_store_headers(validation_error)
        return validation_error

    target_page = _normalize_yui_handoff_text(payload.get("target_page"), max_length=80)
    if not target_page:
        return _json_no_store_response(
            {
                "ok": False,
                "error_code": "invalid_target_page",
                "error": "target_page is required",
            },
            status_code=400,
        )

    now_ms = int(time.time() * 1000)
    request_origin = _get_request_origin(request) or _normalize_origin_value(str(request.base_url))
    record: dict[str, Any] = {
        "token": secrets.token_urlsafe(24),
        "token_version": _YUI_GUIDE_HANDOFF_TOKEN_VERSION,
        "flow_id": _normalize_yui_handoff_text(payload.get("flow_id"), max_length=80) or _YUI_GUIDE_HANDOFF_FLOW_ID,
        "source_origin": request_origin,
        "source_page": _normalize_yui_handoff_text(payload.get("source_page"), max_length=80) or "home",
        "source_path": _normalize_yui_handoff_text(payload.get("source_path"), max_length=240),
        "target_page": target_page,
        "target_path": _normalize_yui_handoff_text(payload.get("target_path"), max_length=240),
        "resume_scene": _normalize_yui_handoff_text(payload.get("resume_scene"), max_length=120) or None,
        "created_at": now_ms,
        "expires_at": now_ms + (_YUI_GUIDE_HANDOFF_TTL_SECONDS * 1000),
        "consumed_at": 0,
        "consumed_by": "",
    }
    record["signature"] = _build_yui_handoff_signature(record)

    async with _yui_guide_handoff_lock:
        _prune_yui_handoff_records(now_ms)
        _yui_guide_handoff_tokens[record["token"]] = record

    return _json_no_store_response({"ok": True, "token": _public_yui_handoff_record(record)})


@router.post("/yui-guide/handoff/consume")
async def consume_yui_guide_handoff(request: Request):
    payload = await _read_json_object(request)
    validation_error = _validate_local_mutation_request(request, payload=payload)
    if validation_error is not None:
        _set_no_store_headers(validation_error)
        return validation_error

    token = _normalize_yui_handoff_text(payload.get("token"), max_length=128)
    signature = _normalize_yui_handoff_text(payload.get("signature"), max_length=128)
    expected_page = _normalize_yui_handoff_text(payload.get("expected_page"), max_length=80)
    consumed_by = _normalize_yui_handoff_text(payload.get("consumer_id"), max_length=120)
    request_origin = _get_request_origin(request) or _normalize_origin_value(str(request.base_url))
    now_ms = int(time.time() * 1000)

    if not token or not signature:
        return _json_no_store_response(
            {
                "ok": False,
                "error_code": "invalid_handoff_token",
                "error": "token and signature are required",
            },
            status_code=400,
        )

    if not expected_page:
        return _json_no_store_response(
            {
                "ok": False,
                "error_code": "invalid_expected_page",
                "error": "expected_page is required",
            },
            status_code=400,
        )

    async with _yui_guide_handoff_lock:
        _prune_yui_handoff_records(now_ms)
        record = _yui_guide_handoff_tokens.get(token)
        if not record:
            return _json_no_store_response(
                {
                    "ok": False,
                    "error_code": "handoff_token_not_found",
                    "error": "handoff token not found",
                },
                status_code=404,
            )

        stored_signature = str(record.get("signature") or "")
        if not hmac.compare_digest(signature, stored_signature):
            return _json_no_store_response(
                {
                    "ok": False,
                    "error_code": "handoff_signature_mismatch",
                    "error": "handoff signature mismatch",
                },
                status_code=403,
            )

        source_origin = str(record.get("source_origin") or "")
        if source_origin and request_origin and request_origin != source_origin:
            return _json_no_store_response(
                {
                    "ok": False,
                    "error_code": "handoff_origin_mismatch",
                    "error": "handoff origin mismatch",
                },
                status_code=403,
            )

        target_page = str(record.get("target_page") or "")
        if expected_page != target_page:
            return _json_no_store_response(
                {
                    "ok": False,
                    "error_code": "handoff_target_mismatch",
                    "error": "handoff target mismatch",
                },
                status_code=403,
            )

        if record.get("consumed_at"):
            return _json_no_store_response(
                {
                    "ok": False,
                    "error_code": "handoff_token_consumed",
                    "error": "handoff token already consumed",
                },
                status_code=409,
            )

        record["consumed_at"] = now_ms
        record["consumed_by"] = consumed_by or request_origin or "unknown"
        return _json_no_store_response({"ok": True, "token": _public_yui_handoff_record(record)})


@router.get("/system/status")
async def get_system_status(response: Response):
    """Return a lightweight readiness snapshot for the web bootstrap sentinel."""
    _set_no_store_headers(response)

    try:
        config_manager = _get_system_config_manager()
        storage_bootstrap = build_storage_location_bootstrap_payload(config_manager)
        lifecycle_state = _derive_system_lifecycle_state(storage_bootstrap)
        return {
            "ok": True,
            "status": lifecycle_state,
            "ready": lifecycle_state == "ready",
            "storage": {
                "selection_required": bool(storage_bootstrap.get("selection_required")),
                "migration_pending": bool(storage_bootstrap.get("migration_pending")),
                "recovery_required": bool(storage_bootstrap.get("recovery_required")),
                "legacy_cleanup_pending": bool(storage_bootstrap.get("legacy_cleanup_pending")),
                "blocking_reason": str(storage_bootstrap.get("blocking_reason") or ""),
                "last_error_summary": str(storage_bootstrap.get("last_error_summary") or ""),
                "stage": storage_bootstrap.get("stage") or "",
            },
        }
    except Exception as exc:
        logger.warning("system status probe unavailable during startup: %s", exc)
        return {
            "ok": True,
            "status": "starting",
            "ready": False,
            "storage": {
                "selection_required": False,
                "migration_pending": False,
                "recovery_required": False,
                "legacy_cleanup_pending": False,
                "blocking_reason": "",
                "last_error_summary": "",
                "stage": "",
            },
        }


# 统一的表情包图源白名单由 utils.meme_fetcher 维护，本文件仅用于引入

# 多语言关键词/别名表统一在 config/prompts/prompts_emotion.py 维护，此处只做扁平索引。
_EMOTION_LABEL_ALIASES = get_emotion_label_aliases_flat()

_EMOTION_CANONICAL_LABELS = ("happy", "sad", "angry", "surprised", "neutral")
_EMOTION_NORMALIZED_ALIAS_LOOKUP = {}
_EMOTION_COMPACT_ALIAS_LOOKUP = {}
for _alias, _canonical in _EMOTION_LABEL_ALIASES.items():
    _normalized_alias = re.sub(r"[\s\-_]+", " ", str(_alias).strip().lower())
    if not _normalized_alias:
        continue
    _EMOTION_NORMALIZED_ALIAS_LOOKUP[_normalized_alias] = _canonical
    _compact_alias = re.sub(r"[\W_]+", "", _normalized_alias, flags=re.UNICODE)
    if _compact_alias and _compact_alias not in _EMOTION_COMPACT_ALIAS_LOOKUP:
        _EMOTION_COMPACT_ALIAS_LOOKUP[_compact_alias] = _canonical

_EMOTION_FUZZY_ALIAS_KEYS = tuple(_EMOTION_NORMALIZED_ALIAS_LOOKUP.keys())
_EMOTION_FUZZY_COMPACT_KEYS = tuple(_EMOTION_COMPACT_ALIAS_LOOKUP.keys())

_ASCII_EMOTION_ALIAS_RE = re.compile(r"^[a-z0-9]+(?:\s+[a-z0-9]+)*$")
_EMOTION_NEGATION_WORDS = frozenset((
    "not", "no", "never", "without",
    "안", "아니", "못", "않", "아니다", "아닌", "아님",
    "не", "нет", "никогда",
))
_EMOTION_NEGATION_PREFIXES = (
    "不是", "并不", "并非", "不太", "没那么", "没有", "并没有",
    "不", "没", "無", "无", "非", "别", "別",
    "안", "아니", "못",
    "не", "нет", "никогда",
)
_EMOTION_NEGATION_SUFFIXES = (
    "지 않", "지않", "지 않아", "지않아", "지 않다", "지않다", "지 않음", "지않음",
    "지 못", "지못", "지 못해", "지못해", "지 못하다", "지못하다",
    "않", "않아", "않다", "않음", "아냐", "아니야", "아니다", "아닌", "아님",
)
_EMOTION_TOKEN_RE = re.compile(r"[^\W_]+", flags=re.UNICODE)
_EMOTION_NEGATION_COMPACT_PREFIXES = tuple(sorted({
    re.sub(r"[\W_]+", "", str(negation).strip().lower(), flags=re.UNICODE)
    for negation in (*_EMOTION_NEGATION_PREFIXES, *_EMOTION_NEGATION_WORDS)
    if str(negation).strip()
}, key=len, reverse=True))
_EMOTION_NEGATION_COMPACT_SUFFIXES = tuple(sorted({
    re.sub(r"[\W_]+", "", str(negation).strip().lower(), flags=re.UNICODE)
    for negation in _EMOTION_NEGATION_SUFFIXES
    if str(negation).strip()
}, key=len, reverse=True))
_EMOTION_NEGATION_CONTEXT_WINDOW = max(
    (len(negation) for negation in _EMOTION_NEGATION_COMPACT_PREFIXES),
    default=6,
)


def _looks_like_emotion_compact_candidate(candidate, cutoff):
    if not candidate:
        return False
    if candidate in _EMOTION_COMPACT_ALIAS_LOOKUP:
        return True
    return bool(difflib.get_close_matches(
        candidate,
        _EMOTION_FUZZY_COMPACT_KEYS,
        n=1,
        cutoff=cutoff,
    ))


def _has_negated_emotion_phrase(normalized_text, compact_text, fuzzy_compact_cutoff):
    tokens = [token for token in _EMOTION_TOKEN_RE.findall(normalized_text) if token]
    if tokens and any(token in _EMOTION_NEGATION_WORDS for token in tokens):
        remaining_compact = re.sub(
            r"[\W_]+",
            "",
            "".join(token for token in tokens if token not in _EMOTION_NEGATION_WORDS),
            flags=re.UNICODE,
        )
        if _looks_like_emotion_compact_candidate(remaining_compact, fuzzy_compact_cutoff):
            return True

    for negation in _EMOTION_NEGATION_COMPACT_PREFIXES:
        if not compact_text.startswith(negation):
            continue
        if _looks_like_emotion_compact_candidate(compact_text[len(negation):], fuzzy_compact_cutoff):
            return True

    for negation in _EMOTION_NEGATION_COMPACT_SUFFIXES:
        marker_index = compact_text.find(negation)
        if marker_index <= 0:
            continue
        if _looks_like_emotion_compact_candidate(compact_text[:marker_index], fuzzy_compact_cutoff):
            return True

    return False

# 启发式关键词/patterns 全部在 config/prompts/prompts_emotion.py 按语种维护，此处只做扁平化。
_EMOTION_KEYWORDS = get_emotion_keywords_flat()
_SAD_VULNERABLE_PATTERNS = get_sad_vulnerable_patterns_flat()
_ANGRY_ATTACK_PATTERNS = get_angry_attack_patterns_flat()
_HAPPY_PLAYFUL_PATTERNS = get_happy_playful_patterns_flat()


def _normalize_emotion_label(raw_emotion, raw_confidence=None):
    emotion_text = str(raw_emotion or "").strip().lower()
    if not emotion_text:
        return "neutral"
    normalized_text = re.sub(r"[\s\-_]+", " ", emotion_text)
    if normalized_text in _EMOTION_NORMALIZED_ALIAS_LOOKUP:
        return _EMOTION_NORMALIZED_ALIAS_LOOKUP[normalized_text]

    compact_text = re.sub(r"[\W_]+", "", emotion_text, flags=re.UNICODE)
    if compact_text in _EMOTION_COMPACT_ALIAS_LOOKUP:
        return _EMOTION_COMPACT_ALIAS_LOOKUP[compact_text]

    high_confidence = raw_confidence is not None and _coerce_emotion_confidence(raw_confidence, 0.0) >= 0.72
    fuzzy_alias_cutoff = 0.74 if high_confidence else 0.9
    fuzzy_compact_cutoff = 0.72 if high_confidence else 0.88

    if _has_negated_emotion_phrase(normalized_text, compact_text, fuzzy_compact_cutoff):
        return "neutral"

    def _is_negated_ascii_match(match_start):
        prefix_tokens = _EMOTION_TOKEN_RE.findall(normalized_text[:match_start])
        return any(token in _EMOTION_NEGATION_WORDS for token in prefix_tokens[-3:])

    def _is_negated_compact_match(match_start):
        prefix = compact_text[max(0, match_start - _EMOTION_NEGATION_CONTEXT_WINDOW):match_start]
        return any(prefix.endswith(negation) for negation in _EMOTION_NEGATION_COMPACT_PREFIXES)

    alias_items = sorted(
        _EMOTION_NORMALIZED_ALIAS_LOOKUP.items(),
        key=lambda item: len(item[0]),
        reverse=True
    )
    for alias, canonical in alias_items:
        if not alias:
            continue
        if _ASCII_EMOTION_ALIAS_RE.match(alias):
            pattern = r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])"
            for match in re.finditer(pattern, normalized_text):
                if not _is_negated_ascii_match(match.start()):
                    return canonical
            continue

        compact_alias = re.sub(r"[\W_]+", "", alias, flags=re.UNICODE)
        if not compact_alias:
            continue
        search_start = 0
        while True:
            match_start = compact_text.find(compact_alias, search_start)
            if match_start < 0:
                break
            if not _is_negated_compact_match(match_start):
                return canonical
            search_start = match_start + len(compact_alias)

    fuzzy_alias_match = difflib.get_close_matches(
        normalized_text,
        _EMOTION_FUZZY_ALIAS_KEYS,
        n=1,
        cutoff=fuzzy_alias_cutoff
    )
    if fuzzy_alias_match:
        return _EMOTION_NORMALIZED_ALIAS_LOOKUP[fuzzy_alias_match[0]]

    if compact_text:
        fuzzy_compact_match = difflib.get_close_matches(
            compact_text,
            _EMOTION_FUZZY_COMPACT_KEYS,
            n=1,
            cutoff=fuzzy_compact_cutoff
        )
        if fuzzy_compact_match:
            return _EMOTION_COMPACT_ALIAS_LOOKUP[fuzzy_compact_match[0]]

    if high_confidence:
        fuzzy_canonical = difflib.get_close_matches(
            normalized_text,
            _EMOTION_CANONICAL_LABELS,
            n=1,
            cutoff=0.55
        )
        if fuzzy_canonical:
            return fuzzy_canonical[0]

    return "neutral"


def _push_emotion_update(lanlan_name, emotion, confidence):
    sync_message_queue = get_sync_message_queue()
    if lanlan_name and lanlan_name in sync_message_queue:
        sync_message_queue[lanlan_name].put({
            "type": "json",
            "data": {
                "type": "emotion",
                "emotion": emotion,
                "confidence": confidence
            }
        })


def _emotion_response(emotion, confidence):
    return {
        "emotion": emotion,
        "confidence": confidence
    }


def _coerce_emotion_confidence(raw_confidence, default=0.5):
    try:
        confidence = float(raw_confidence)
    except (TypeError, ValueError):
        confidence = float(default)
    if not math.isfinite(confidence):
        confidence = float(default)
    return max(0.0, min(1.0, confidence))


# 启发式打分时的否定回看 token / 转折连词表统一在 config/prompts/prompts_emotion.py 按语种维护。
_HEURISTIC_NEGATION_TOKENS = get_heuristic_negation_tokens_flat()
_HEURISTIC_TIGHT_NEGATION_TOKENS = get_heuristic_tight_negation_tokens_flat()
_HEURISTIC_NEGATION_BLOCKLIST = get_heuristic_negation_blocklist_flat()
_HEURISTIC_CONTRAST_CONJUNCTIONS = get_heuristic_contrast_conjunctions_flat()
_HEURISTIC_NEGATION_LOOKBACK = 14
# zh 单字否定（`不/没/别/未` 等）假阳率高，必须紧邻情绪词才算真否定，
# 避免 `不错/不思议/不具合` 等非否定词组里的单字误触发。
_HEURISTIC_TIGHT_NEGATION_LOOKBACK = 2
# 子句分隔符：回看窗口越过分隔符后的内容视为另一小句，不再修饰本次命中。
# 避免 "我不是难过，我是生气" 中 `生气` 的回看抓到前一小句的 `不` 而被误判否定。
_HEURISTIC_CLAUSE_DELIMITERS = (
    '.', ',', ';', '!', '?', '\n',
    '，', '。', '；', '！', '？', '、', '：', ':',
)


def _has_heuristic_negation_before(text_value, position):
    if position <= 0:
        return False
    start = max(0, position - _HEURISTIC_NEGATION_LOOKBACK)
    window = text_value[start:position]
    # 1) 窗口越过子句分隔符（标点）的部分丢掉，只看与命中关键词同小句的前文
    last_delim = -1
    for delim in _HEURISTIC_CLAUSE_DELIMITERS:
        idx = window.rfind(delim)
        if idx > last_delim:
            last_delim = idx
    if last_delim >= 0:
        window = window[last_delim + 1:]
    # 2) 句首场景补一个前导空格，统一处理带前导空格的 token（否定 ` no `、连词 ` but `）
    window = ' ' + window
    # 3) 让步/转折连词同样切断否定范围：处理 "not X but Y / 不是 X 而是 Y" 对比句，
    #    避免前半的否定被错误带到后半的情绪关键词。
    last_conj = -1
    for conj in _HEURISTIC_CONTRAST_CONJUNCTIONS:
        idx = window.rfind(conj)
        if idx >= 0:
            end_pos = idx + len(conj)
            if end_pos > last_conj:
                last_conj = end_pos
    if last_conj >= 0:
        window = window[last_conj:]
    # 4) 排除非否定固定搭配（`not only / 不仅 / не только` 等肯定结构里的 not/不/не
    #    并不是真否定）：把这些短语从 window 里替换成等长空白后再做 token 匹配。
    sanitized = window
    for phrase in _HEURISTIC_NEGATION_BLOCKLIST:
        if phrase and phrase in sanitized:
            sanitized = sanitized.replace(phrase, ' ' * len(phrase))
    # 5) 多字否定 token（宽 lookback）
    if any(token in sanitized for token in _HEURISTIC_NEGATION_TOKENS):
        return True
    # 6) zh 单字否定 token：仅在紧邻命中关键词的尾部窗口里才算真否定，
    #    避免 `不错/不思议/不具合` 等非否定词组里的单字误触发整个否定。
    if _HEURISTIC_TIGHT_NEGATION_TOKENS:
        tight_window = sanitized[-_HEURISTIC_TIGHT_NEGATION_LOOKBACK:]
        if any(token in tight_window for token in _HEURISTIC_TIGHT_NEGATION_TOKENS):
            return True
    return False


# 英文 keyword 用 ASCII-only 词边界匹配，避免 `happy` 命中 `unhappy`、`surprised`
# 命中 `unsurprised` 这类反向情绪嵌入。
# 注意：不能用 `\b`，因为 Python regex 默认 Unicode 模式下 CJK 也算 \w，
# 在 mixed-script 文本（如 `好happy啊 / 超annoyed欸`）里 `好` 和 `h` 之间没有
# word boundary，导致英文 keyword 完全失配。改用前后 ASCII 字母断言：
# `(?<![a-zA-Z])keyword(?![a-zA-Z])`，CJK / 标点 / 空白都允许作为边界。
_ASCII_WORD_KEYWORD_RE_CACHE = {}


def _is_ascii_word_keyword(keyword):
    if not keyword:
        return False
    return all(c.isascii() and (c.isalpha() or c in " '") for c in keyword)


def _count_keyword_hits(text_value, keyword):
    if not keyword or not text_value:
        return 0
    if _is_ascii_word_keyword(keyword):
        pattern = _ASCII_WORD_KEYWORD_RE_CACHE.get(keyword)
        if pattern is None:
            pattern = re.compile(r'(?<![a-zA-Z])' + re.escape(keyword) + r'(?![a-zA-Z])')
            _ASCII_WORD_KEYWORD_RE_CACHE[keyword] = pattern
        hits = 0
        for match in pattern.finditer(text_value):
            if not _has_heuristic_negation_before(text_value, match.start()):
                hits += 1
        return hits
    hits = 0
    search_start = 0
    while True:
        pos = text_value.find(keyword, search_start)
        if pos < 0:
            break
        if not _has_heuristic_negation_before(text_value, pos):
            hits += 1
        search_start = pos + len(keyword)
    return hits


def _infer_emotion_from_text(text):
    text_value = str(text or "").lower()
    if not text_value:
        return None, 0

    scores = {key: 0 for key in _EMOTION_KEYWORDS}
    for emotion, keywords in _EMOTION_KEYWORDS.items():
        for keyword in keywords:
            scores[emotion] += _count_keyword_hits(text_value, keyword)

    if "!!" in text_value or "！？" in text_value or "!?" in text_value or "??" in text_value:
        scores["surprised"] += 1

    sad_vulnerable_hits = sum(_count_keyword_hits(text_value, p) for p in _SAD_VULNERABLE_PATTERNS)
    angry_attack_hits = sum(_count_keyword_hits(text_value, p) for p in _ANGRY_ATTACK_PATTERNS)
    happy_playful_hits = sum(_count_keyword_hits(text_value, p) for p in _HAPPY_PLAYFUL_PATTERNS)

    if sad_vulnerable_hits:
        scores["sad"] += sad_vulnerable_hits * 2
    if angry_attack_hits:
        scores["angry"] += angry_attack_hits * 2
    if happy_playful_hits and not sad_vulnerable_hits and not angry_attack_hits:
        # playful patterns（哈哈/嘿嘿/嘻嘻/可爱/好耶 等）大量与 happy keyword 重叠，
        # 重复出现时 keyword 那边已经按命中数累加分数；这里只额外 +1 作为信号 boost，
        # 避免 `haha haha haha / 哈哈哈哈哈` 类 filler 文本被双倍放大触发 override。
        scores["happy"] += 1
    if sad_vulnerable_hits and happy_playful_hits:
        # 撒娇外壳下的委屈/想哭，优先视为 sad 而不是 happy
        scores["sad"] += 1

    best_emotion = None
    best_score = 0
    for emotion, score in scores.items():
        if score > best_score:
            best_emotion = emotion
            best_score = score

    if best_score <= 0:
        return None, 0
    return best_emotion, best_score


def _resolve_emotion_prompt_language(text):
    try:
        detected_lang = detect_language(str(text or ""))
        return normalize_language_code(detected_lang, format='short')
    except Exception:
        return 'zh'


@router.get("/token-usage")
async def get_token_usage(days: int = 7):
    """Return LLM token usage statistics for the last N days."""
    from utils.token_tracker import TokenTracker
    return TokenTracker.get_instance().get_stats(days=min(days, 90))


@router.get("/pending-notices")
async def get_pending_notices():
    """Fetch pending pop-up notices on frontend page load (read-only snapshot; does not clear the queue).
    
    Returns {"notices": [...], "cursor": N}; after display the frontend must pass the
    cursor back to the ack endpoint, ensuring only the notices shown this time are
    deleted and entries enqueued between the two requests are never lost.
    """
    from main_logic.core import peek_prominent_notices
    notices, cursor = peek_prominent_notices()
    return {"notices": notices, "cursor": cursor}


@router.post("/pending-notices/ack")
async def ack_pending_notices(request: Request):
    """Called after the frontend has shown the notices; deletes only notices up to the cursor (cursor ack, avoids TOCTOU)."""
    validation_error = _validate_local_mutation_request(request)
    if validation_error is not None:
        return validation_error

    from main_logic.core import drain_prominent_notices
    try:
        body = await _read_json_object(request)
        cursor = int(body.get("cursor", 0))
    except Exception:
        cursor = 0
    drain_prominent_notices(cursor)
    return {"ok": True}


@router.get("/tutorial-prompt/state")
async def get_tutorial_prompt_state():
    """Return a snapshot of the tutorial prompt state."""
    return get_tutorial_prompt_state_response(config_manager=get_config_manager())


@router.post("/tutorial-prompt/heartbeat")
async def post_tutorial_prompt_heartbeat(request: Request):
    """Record homepage idle and interaction state, and decide whether to prompt the tutorial."""
    payload = await _read_json_object(request)
    validation_error = _validate_local_mutation_request(request, payload=payload)
    if validation_error is not None:
        return validation_error

    return process_tutorial_prompt_heartbeat(payload, config_manager=get_config_manager())


@router.post("/tutorial-prompt/shown")
async def post_tutorial_prompt_shown(request: Request):
    """Record that the tutorial prompt was actually shown to the user."""
    validation_error = _validate_local_mutation_request(request)
    if validation_error is not None:
        return validation_error

    payload = await _read_json_object(request)

    try:
        return record_tutorial_prompt_shown(payload, config_manager=get_config_manager())
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(exc)})


@router.post("/tutorial-prompt/decision")
async def post_tutorial_prompt_decision(request: Request):
    """Record the user's decision on the tutorial prompt."""
    validation_error = _validate_local_mutation_request(request)
    if validation_error is not None:
        return validation_error

    payload = await _read_json_object(request)

    try:
        return record_tutorial_prompt_decision(payload, config_manager=get_config_manager())
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(exc)})


@router.post("/tutorial-prompt/reset")
async def post_tutorial_prompt_reset(request: Request):
    """Reset the homepage tutorial state, for the manual reset entry in the memory browser."""
    validation_error = _validate_local_mutation_request(request)
    if validation_error is not None:
        return validation_error

    return reset_tutorial_prompt_state(config_manager=get_config_manager())


@router.get("/autostart-prompt/state")
async def get_autostart_prompt_state():
    """Return a snapshot of the autostart prompt state."""
    return get_autostart_prompt_state_response(config_manager=get_config_manager())


@router.post("/autostart-prompt/heartbeat")
async def post_autostart_prompt_heartbeat(request: Request):
    """Record homepage idle and interaction state, and decide whether to prompt about autostart."""
    payload = await _read_json_object(request)
    validation_error = _validate_local_mutation_request(request, payload=payload)
    if validation_error is not None:
        return validation_error

    return process_autostart_prompt_heartbeat(payload, config_manager=get_config_manager())


@router.post("/autostart-prompt/shown")
async def post_autostart_prompt_shown(request: Request):
    """Record that the autostart prompt was actually shown to the user."""
    validation_error = _validate_local_mutation_request(request)
    if validation_error is not None:
        return validation_error

    payload = await _read_json_object(request)

    try:
        return record_autostart_prompt_shown(payload, config_manager=get_config_manager())
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(exc)})


@router.post("/autostart-prompt/decision")
async def post_autostart_prompt_decision(request: Request):
    """Record the user's decision on the autostart prompt."""
    validation_error = _validate_local_mutation_request(request)
    if validation_error is not None:
        return validation_error

    payload = await _read_json_object(request)

    try:
        return record_autostart_prompt_decision(payload, config_manager=get_config_manager())
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(exc)})


@router.post("/tutorial-prompt/tutorial-started")
async def post_tutorial_started(request: Request):
    """Record that the homepage tutorial actually started."""
    validation_error = _validate_local_mutation_request(request)
    if validation_error is not None:
        return validation_error

    payload = await _read_json_object(request)

    try:
        return record_tutorial_started(payload, config_manager=get_config_manager())
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(exc)})


@router.post("/tutorial-prompt/tutorial-completed")
async def post_tutorial_completed(request: Request):
    """Record that the homepage tutorial was completed."""
    validation_error = _validate_local_mutation_request(request)
    if validation_error is not None:
        return validation_error

    payload = await _read_json_object(request)

    try:
        return record_tutorial_completed(payload, config_manager=get_config_manager())
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(exc)})


# --- 版本更新日志 ---

@router.get("/changelog")
async def get_changelog(since: str = "", lang: str = ""):
    """Return all changelog entries since the given version.

    The frontend passes the lastNotifiedVersion stored in localStorage; the backend
    returns all changelog entries > since (ascending by version) plus the current
    version number.
    The lang parameter is the frontend locale (e.g. zh-CN / en / ja / ko / ru / zh-TW).
    A concrete locale (including Chinese variants like zh-TW) prefers its own subdir
    first; non-Chinese locales then fall back to en; everything finally lands on the
    Simplified Chinese base file. Mirrors the survey loader's fallback chain.
    """
    from config import APP_VERSION
    import glob as _glob

    def _parse_ver(s: str) -> tuple[int, ...]:
        """Convert '0.7.3' into a comparable int tuple; returns (0,) on parse failure."""
        try:
            return tuple(int(x) for x in s.strip().split("."))
        except (ValueError, AttributeError):
            return (0,)

    changelog_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "changelog")
    entries: list[dict] = []
    since_ver = _parse_ver(since) if since else (0,)

    # lang 来自 query string，下面会拼进 os.path.join(changelog_dir, lang, ...)，
    # 先白名单化挡路径穿越（与 survey 下发口共用 _safe_locale）。
    lang = _safe_locale(lang)
    # 确定 fallback 链，与 survey 下发口（_load_survey_for_version）保持一致：
    # 具体 locale（含 zh-TW 等中文变体）先试自己的子目录 -> 非中文再回退 en ->
    # 最后都落到简体中文原文（zh_content）。zh-TW 也 startswith("zh")，但简体
    # base 并无 zh-CN/ 子目录，所以简体请求自然落回原文，不受影响。
    is_chinese = lang.startswith("zh") if lang else True
    fallback_langs: list[str] = []
    if lang:
        fallback_langs.append(lang)
    if not is_chinese and "en" not in fallback_langs:
        fallback_langs.append("en")

    def _read_localized(stem: str, zh_content: str) -> str:
        """Look up the localized version along the fallback chain; returns the original Chinese when not found."""
        for loc in fallback_langs:
            loc_file = os.path.join(changelog_dir, loc, f"{stem}.md")
            try:
                with open(loc_file, "r", encoding="utf-8") as f:
                    return f.read()
            except Exception:
                continue
        return zh_content

    if os.path.isdir(changelog_dir):
        for md_file in sorted(_glob.glob(os.path.join(changelog_dir, "*.md")),
                              key=lambda p: _parse_ver(os.path.splitext(os.path.basename(p))[0])):
            stem = os.path.splitext(os.path.basename(md_file))[0]
            file_ver = _parse_ver(stem)
            if file_ver == (0,):
                continue
            if file_ver > since_ver:
                try:
                    with open(md_file, "r", encoding="utf-8") as f:
                        zh_content = f.read()
                except Exception:
                    zh_content = ""
                content = _read_localized(stem, zh_content)
                entries.append({"version": stem, "content": content})

    return {"current_version": APP_VERSION, "entries": entries}


_LOCALE_RE = re.compile(r'^[A-Za-z]{2,8}(-[A-Za-z0-9]{2,8})*$')


def _safe_locale(lang: object) -> str:
    """Whitelist a client-supplied locale (zh-CN / en / ja / ...) before it touches a filesystem path.

    ``lang`` arrives from the request query string and is joined into changelog /
    survey file paths; an unfiltered ``../`` or an absolute prefix would let a
    crafted value escape the content dir (path traversal). Anything not matching the
    locale shape returns '' (→ caller falls back to the Chinese base / en).
    """
    return lang if (isinstance(lang, str) and _LOCALE_RE.match(lang)) else ""


def _load_survey_for_version(version: str, lang: str) -> dict | None:
    """Load config/surveys/<version>.json with a per-locale fallback chain.

    Returns the parsed (localized) survey dict, or None when no survey exists for
    the version. Fallback: a concrete locale tries its own subdir first (incl.
    Chinese variants like zh-TW); Chinese variants then fall back to the Simplified
    base file, non-Chinese fall back to en, and everything finally lands on the
    base. This loader is independent of ``_load_changelog`` — changing it does not
    touch changelog's language fallback. The whole file is swapped per locale
    (question ids must stay identical across locales — answers are reported by id).
    """
    surveys_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "surveys")
    base_file = os.path.join(surveys_dir, f"{version}.json")
    if not os.path.isfile(base_file):
        return None

    # 任何具体 locale 先试自己的子目录（含 zh-TW 等中文变体，于是繁体不再被并入
    # 简体 base）；中文变体回退到简体 base，非中文回退 en，最后都落 base。
    candidates: list[str] = []
    if lang:
        candidates.append(os.path.join(surveys_dir, lang, f"{version}.json"))
    is_chinese = lang.startswith("zh") if lang else True
    if not is_chinese:
        en_path = os.path.join(surveys_dir, "en", f"{version}.json")
        if en_path not in candidates:
            candidates.append(en_path)
    candidates.append(base_file)

    for path in candidates:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        if isinstance(data, dict):
            # 强制归一到文件版本（= APP_VERSION），不用 setdefault：本地化文件若误写了
            # 别的 survey_version，会让前端去重键和上报版本错位、统计分裂。
            data["survey_version"] = version
            return data
    return None


def _sanitize_survey_answers(answers: object) -> dict:
    """Whitelist + cap the answer dict before forwarding (abuse / oversized-payload guard).

    Mirrors the remote server's data-minimization contract: at most 50 questions,
    keys <= 64 chars, string answers <= 2000 chars, list answers <= 50 items of
    <= 200 chars each. Anything else is dropped.
    """
    out: dict = {}
    if not isinstance(answers, dict):
        return out
    for i, (k, v) in enumerate(answers.items()):
        if i >= 50:
            break
        if not isinstance(k, str) or not k:
            continue
        key = k[:64]
        if isinstance(v, bool):
            out[key] = v
        elif isinstance(v, str):
            out[key] = v[:2000]
        elif isinstance(v, (int, float)):
            out[key] = v
        elif isinstance(v, list):
            out[key] = [str(x)[:200] for x in v[:50] if isinstance(x, (str, int, float, bool))]
    return out


def _resolve_survey_for_request(version: str, lang: str) -> dict | None:
    """Steam gate + localized survey load (sync; runs in a worker thread).

    Survey is Steam-only: a non-Steam install gets None (-> has_survey:false). The
    judgment is distribution=='steam' (live Steam64 / workshop subscription /
    workshop_config.json disk fallback; see survey_client.is_steam_user). On any
    error in the steam check we fail closed (None) — better to skip the popup than
    to show it to a possibly-non-Steam user.
    """
    try:
        from utils.survey_client import is_steam_user
        if not is_steam_user():
            return None
    except Exception:
        return None
    return _load_survey_for_version(version, lang)


@router.get("/survey")
async def get_survey(lang: str = ""):
    """Return the survey for the current app version, or {has_survey: false}.

    Two gates before content is served:
    - DNT: opted-out users (NEKO_DO_NOT_TRACK / DO_NOT_TRACK) get nothing — the same
      switch governs passive stats and surveys.
    - Steam-only: non-Steam installs get nothing (judged by the cached Steam64 +
      distribution==steam fallback).
    """
    from config import APP_VERSION

    try:
        from utils.survey_client import is_reporting_enabled
        if not is_reporting_enabled():
            return {"has_survey": False, "survey_version": APP_VERSION}
    except Exception:
        return {"has_survey": False, "survey_version": APP_VERSION}

    survey = await asyncio.to_thread(_resolve_survey_for_request, APP_VERSION, _safe_locale(lang))
    if not survey:
        return {"has_survey": False, "survey_version": APP_VERSION}
    return {
        "has_survey": True,
        "survey_version": survey.get("survey_version", APP_VERSION),
        "survey": survey,
    }


@router.post("/survey/submit")
async def submit_survey(request: Request):
    """Receive the user's survey answers (or a skip) and forward them, HMAC-signed, to the remote survey server.

    Best-effort: a failed upload still returns ok=True so the frontend records the
    survey as done and never re-prompts; uploaded reflects whether the remote 200'd.
    """
    payload = await _read_json_object(request)
    validation_error = _validate_local_mutation_request(request, payload=payload)
    if validation_error is not None:
        return validation_error

    from config import APP_VERSION

    action = payload.get("action")
    if action not in ("submit", "skip"):
        action = "submit"
    # survey_version 用服务端 APP_VERSION 权威值，不信客户端传入——否则恶意请求可写
    # 任意版本污染远端版本维度。问卷本就只对当前版本下发，没有跨版本提交的合法场景。
    survey_version = APP_VERSION
    answers = _sanitize_survey_answers(payload.get("answers"))

    uploaded = False
    try:
        from utils.survey_client import report_survey
        config_dir = None
        try:
            config_dir = get_config_manager().config_dir
        except Exception:
            config_dir = None
        uploaded = await asyncio.to_thread(
            report_survey, survey_version, action, answers, config_dir=config_dir
        )
    except Exception as e:
        logger.warning("survey submit forward failed: %s", e)

    return {"ok": True, "uploaded": bool(uploaded)}


# --- 主动搭话近期记录暂存区 ---
# {lanlan_name: deque([(timestamp, message), ...], maxlen=10)}
_proactive_chat_history: dict[str, deque] = {}

# --- 主动搭话"素材标识"近期去重暂存区（ANTI_REPEAT_EXEMPT_SOURCE_TAGS 用）---
# {lanlan_name: {source_tag: deque([(timestamp, material_key), ...], maxlen=N)}}
# 素材推送类 channel（MUSIC/MEME）豁免台词级复读判定，改按"素材本身"去重：
# MUSIC 看曲目（title|artist），MEME 看搜索关键词。本轮素材与近期不雷同就放行；
# 雷同才回落到台词判定。进程内、重启清零——短期复读保护，与 _proactive_chat_
# history / _mini_game_invite_state 同样是内存态即可。
_proactive_material_history: dict[str, dict[str, deque]] = {}
_PROACTIVE_MATERIAL_HISTORY_MAX = 10

# --- Mini-game 邀请短路状态（每角色独立）---
# {lanlan_name: {'delivered_at': float|None,
#                'responded_at': float|None,
#                'chats_since_response': int,
#                'last_game_type': str|None,
#                'last_response_choice': 'accept'|'decline'|None}}
# - delivered_at: 上次成功投递邀请的时间戳。None=从未发过。
# - responded_at: 投递后被用户回应（任何用户消息时间戳 > delivered_at）的时间。
#   pending（delivered_at!=None and responded_at=None）期间一律抑制掷骰，避免
#   邀请挂着不响应又再发第二次。
# - chats_since_response: responded_at 设上后成功投递的"普通主动搭话"次数。
#   两条件（time-by-choice 且 >= COOLDOWN_CHATS）都跨过才允许下次掷骰。
#   冷却跨 game_type 共享——每角色一个全局冷却，一次邀请 → 冷却窗内全部 mini-game
#   静默；spec 没说邀请要密集，多游戏只是丰富选项不是加密。
# - last_game_type: 上次邀请发的是哪个游戏（从 MINI_GAME_INVITE_AVAILABLE_GAMES
#   里 random.choice 出来的）；用于 PR-B 按钮判断"打开哪个游戏"。
# - last_response_choice: 上次回应是 accept 还是 decline；用于 cooldown 函数按
#   choice 取不同 SECONDS 阈值（accept=2h、decline=5h）。later/隐式 dismiss 走
#   reset 路径不会落到这里。None = 从未回应过（pending or 全新）。
# 进程内 dict，重启清零——时间+10 chats 是软冷却，重启后多发一次邀请的代价远小
# 于持久化存储引依赖的代价；与 _proactive_chat_history 同样是内存。
_mini_game_invite_state: dict[str, dict[str, Any]] = {}

# --- 持久化"该角色累计成功投递的主动搭话次数 + 是否曾被邀请过"---
# 单文件 schema：
#   {"version": 2,
#    "totals": {<lanlan_name>: <int>, ...},
#    "ever_delivered": {<lanlan_name>: true, ...}}
# 跨进程重启保留。两份数据合一个文件方便维护。
#
# - totals: 「新用户第 N 次主动搭话强制走 mini-game 邀请」(N=NEW_USER_FORCE_AT)
#   必须依赖跨重启的累计计数——否则用户每次重启 app，force-trigger 会反复触发，
#   体感邀请密度抖。计数语义与 _record_proactive_chat 对齐：仅在「成功投递给
#   用户」时 +1，PASS 不算（spec 上"第 N 次主动搭话"指用户实际收到的）。
# - ever_delivered: 「该角色是否曾经被发过 mini-game 邀请」一次性 true 标记，
#   force-first 的 "is new user" 判定基础。和 in-memory 的 ``state.delivered_at``
#   不同：后者跟随 PR-B 的 D2「回头再说」会被 reset，但 ever_delivered 一旦置
#   True 就不再翻——「曾经被邀请过」是历史事实，不能被反悔。codex review (P1)
#   指出，没这条 force-first 在重启后会把已邀请过的用户当新用户重新强制邀请。
_PROACTIVE_CHAT_TOTALS_FILENAME = "proactive_chat_totals.json"
_PROACTIVE_CHAT_TOTALS_SCHEMA_VERSION = 2
_proactive_chat_totals: dict[str, int] = {}
_invite_ever_delivered: dict[str, bool] = {}
_proactive_chat_totals_lock = asyncio.Lock()
_proactive_chat_totals_loaded = False

_RECENT_CHAT_MAX_AGE_SECONDS = 3600  # 1小时内的搭话记录
_PROACTIVE_SIMILARITY_THRESHOLD = 0.90  # 保守硬拦截阈值：90% 以上重复直接放弃本轮
_PHASE1_FETCH_PER_SOURCE = PROACTIVE_PHASE1_FETCH_PER_SOURCE  # Phase 1 每个信息源固定抓取条数
_PHASE1_TOTAL_TOPIC_TARGET = PROACTIVE_PHASE1_TOTAL_TOPICS  # Phase 1 输入给筛选模型的总候选目标条数

# --- 全局来源衰减历史（跨角色 / 持久化）---
# 主动搭话消费过的 web / music / image 链接进入这里，按 URL hash 索引。
# 5h 内硬 skip（p_skip=1），其后按 kind 各自半衰期指数衰减；p_skip 低于阈值
# 时直接遗忘。所有 IO 走 asyncio.to_thread / atomic_write_json_async，过滤
# 路径只读 dict + RNG，不阻塞 event loop。
# （衰减参数定义在 config/__init__.py 与项目其他 budget 常量统一维护）
_SOURCE_HISTORY_FILENAME = "proactive_source_history.json"
_SOURCE_HISTORY_SCHEMA_VERSION = 1

_source_history: dict[str, dict[str, Any]] = {}
_source_history_lock = asyncio.Lock()
_source_history_loaded = False


def _source_history_path() -> Path:
    return Path(get_config_manager().memory_dir) / _SOURCE_HISTORY_FILENAME


def _source_hash(url: str = '', fallback_title: str = '') -> str:
    """URL first, otherwise the normalized title as fallback. An empty string means "cannot be identified stably"."""
    norm = (url or '').strip().lower().rstrip('/')
    if norm:
        return hashlib.sha256(norm.encode('utf-8')).hexdigest()
    title_norm = re.sub(r'\s+', ' ', (fallback_title or '').strip().lower())
    if title_norm:
        return hashlib.sha256(('t::' + title_norm).encode('utf-8')).hexdigest()
    return ''


def _half_life_for(kind: str) -> float:
    return PROACTIVE_SOURCE_HALF_LIFE_BY_KIND.get(kind, PROACTIVE_SOURCE_HALF_LIFE_DEFAULT)


def _source_skip_probability(age: float, half_life: float) -> float:
    if age < PROACTIVE_SOURCE_HARD_SKIP_SECONDS:
        return 1.0
    decay_age = age - PROACTIVE_SOURCE_HARD_SKIP_SECONDS
    return 0.5 ** (decay_age / half_life)


def _should_skip_source(url_hash: str) -> bool:
    """Synchronous, purely in-memory check, O(1); callable directly inside the synchronous picking loop."""
    if not url_hash:
        return False
    entry = _source_history.get(url_hash)
    if not entry:
        return False
    age = time.time() - entry.get('ts', 0.0)
    p = _source_skip_probability(age, _half_life_for(entry.get('kind', 'web')))
    if p >= 1.0:
        return True
    if p <= 0.0:
        return False
    return random.random() < p


async def _ensure_source_history_loaded() -> None:
    """Lazy loading, idempotent. The file read goes to the thread pool and does not block the event loop."""
    global _source_history_loaded
    if _source_history_loaded:
        return
    async with _source_history_lock:
        if _source_history_loaded:
            return
        path = _source_history_path()
        try:
            data = await asyncio.to_thread(read_json, path)
            entries = data.get('entries') if isinstance(data, dict) else None
            if isinstance(entries, dict):
                # 加载时顺便丢掉早已遗忘阈值之下的条目
                now = time.time()
                for h, entry in entries.items():
                    if not isinstance(entry, dict):
                        continue
                    age = now - float(entry.get('ts', 0.0) or 0.0)
                    p = _source_skip_probability(
                        age, _half_life_for(entry.get('kind', 'web'))
                    )
                    if p >= PROACTIVE_SOURCE_FORGET_P:
                        _source_history[h] = entry
        except FileNotFoundError:
            # 首次运行 / 全新机器：尚无历史文件，按空历史继续
            pass
        except Exception as e:
            logger.warning(
                f"加载 {_SOURCE_HISTORY_FILENAME} 失败，按空历史处理: {type(e).__name__}: {e}"
            )
        _source_history_loaded = True


async def _record_source_used(
    *,
    url: str,
    kind: str,
    title: str = '',
) -> None:
    """Called after a source is consumed or deliberately suppressed: update memory → prune → persist.

    Concurrent records are serialized by an asyncio.Lock; persistence goes through
    atomic_write_json_async (fsync + os.replace in the thread pool), so the main
    coroutine is never stalled by disk IO.
    """
    h = _source_hash(url, title)
    if not h:
        return
    snapshot: dict[str, Any] | None = None
    async with _source_history_lock:
        _source_history[h] = {
            "ts": time.time(),
            "kind": kind,
            "title": (title or '')[:80],
        }
        # 顺手 prune：写盘前剔除已遗忘条目，文件体积自然有界
        now = time.time()
        forget = [
            hh for hh, entry in _source_history.items()
            if _source_skip_probability(
                now - float(entry.get('ts', 0.0) or 0.0),
                _half_life_for(entry.get('kind', 'web'))
            ) < PROACTIVE_SOURCE_FORGET_P
        ]
        for hh in forget:
            _source_history.pop(hh, None)
        snapshot = {
            "v": _SOURCE_HISTORY_SCHEMA_VERSION,
            "entries": dict(_source_history),
        }
    try:
        await atomic_write_json_async(_source_history_path(), snapshot)
    except Exception as e:
        # 写盘失败不影响主流程：下一次 record 会整文件重写覆盖
        logger.warning(
            f"落盘 {_SOURCE_HISTORY_FILENAME} 失败: {type(e).__name__}: {e}"
        )

# --- 来源动态权重系统 ---
_SOURCE_WEIGHT_DECAY_LAMBDA = 0.002   # 指数衰减系数，半衰期 ≈ 5.8 分钟
_SOURCE_WEIGHT_K = 0.30               # freshness 惩罚系数：freshness = 1 / (1 + k * raw_score)
_SOURCE_WEIGHT_FLOOR = 0.20           # 归一化权重绝对下限


def _extract_links_from_raw(mode: str, raw_data: dict) -> list[dict]:
    """
    Extract a list of link info entries from raw web data.
    args:
    - mode: data mode; supports 'news', 'video', 'home', 'personal', 'music'
    - raw_data: raw web data
    returns:
    - list[dict]: list of link info entries, each containing 'title', 'url' and 'source' fields
    """
    links = []
    try:
        if mode == 'news':
            news = raw_data.get('news', {})
            items = news.get('trending', [])
            for item in items:
                title = item.get('word', '') or item.get('name', '')
                url = item.get('url', '')
                if title and url:
                    links.append({'title': title, 'url': url, 'source': '微博' if raw_data.get('region', 'china') == 'china' else 'Twitter'})
        
        elif mode == 'video':
            video = raw_data.get('video', {})
            items = video.get('videos', []) or video.get('posts', [])
            for item in items:
                title = item.get('title', '')
                url = item.get('url', '')
                if title and url:
                    links.append({'title': title, 'url': url, 'source': 'B站' if raw_data.get('region', 'china') == 'china' else 'Reddit'})
        
        elif mode == 'home':
            bilibili = raw_data.get('bilibili', {})
            for v in (bilibili.get('videos', []) or []):
                if v.get('title') and v.get('url'):
                    links.append({'title': v['title'], 'url': v['url'], 'source': 'B站'})
            
            weibo = raw_data.get('weibo', {})
            for w in (weibo.get('trending', []) or []):
                if w.get('word') and w.get('url'):
                    links.append({'title': w['word'], 'url': w['url'], 'source': '微博'})
            
            reddit = raw_data.get('reddit', {})
            for r in (reddit.get('posts', []) or []):
                if r.get('title') and r.get('url'):
                    links.append({'title': r['title'], 'url': r['url'], 'source': 'Reddit'})
            
            twitter = raw_data.get('twitter', {})
            for t in (twitter.get('trending', []) or []):
                title = t.get('name', '') or t.get('word', '')
                if title and t.get('url'):
                    links.append({'title': title, 'url': t['url'], 'source': 'Twitter'})

        elif mode == 'personal':
            region = raw_data.get('region', 'china')
            if region == 'china':

                b_dyn = raw_data.get('bilibili_dynamic', {})
                for d in (b_dyn.get('dynamics', []) or []):
                    title = d.get('content', '')
                    url = d.get('url', '')
                    if title and url:
                        links.append({'title': title, 'url': url, 'source': 'B站'})
                
                w_dyn = raw_data.get('weibo_dynamic', {})
                for d in (w_dyn.get('statuses', []) or []):
                    title = d.get('content', '')
                    url = d.get('url', '')
                    if title and url:
                        links.append({'title': title, 'url': url, 'source': '微博'})
                        
                d_dyn = raw_data.get('douyin_dynamic', {})
                for d in (d_dyn.get('dynamics', []) or []):
                    title = d.get('content', '')
                    url = d.get('url', '')
                    if title and url:
                        links.append({'title': title, 'url': url, 'source': '抖音'})

                k_dyn = raw_data.get('kuaishou_dynamic', {})
                for d in (k_dyn.get('dynamics', []) or []):
                    title = d.get('content', '')
                    url = d.get('url', '')
                    if title and url:
                        links.append({'title': title, 'url': url, 'source': '快手'})
            else:
                r_dyn = raw_data.get('reddit_dynamic', {})
                for d in (r_dyn.get('posts', []) or []):
                    title = d.get('title', '') or d.get('content', '')
                    url = d.get('url', '')
                    if title and url:
                        links.append({'title': title, 'url': url, 'source': 'Reddit'})
                
                t_dyn = raw_data.get('twitter_dynamic', {})
                for d in (t_dyn.get('tweets', []) or []):
                    title = d.get('content', '')
                    url = d.get('url', '')
                    if title and url:
                        links.append({'title': title, 'url': url, 'source': 'Twitter'})

        elif mode == 'music':
            items = raw_data.get('data', [])
            for item in items:
                title = item.get('name', '')
                artist = item.get('artist', '')
                url = item.get('url', '')
                if title and url:
                    links.append({'title': f"{title} - {artist}", 'url': url, 'source': '音乐推荐'})

    except Exception as e:
        logger.warning(f"提取链接失败 [{mode}]: {e}")
    return links


def _parse_web_screening_result(text: str) -> dict | None:
    """
    Parse the structured result of the Phase 1 web-screening LLM.
    Expected format (Chinese or English labels):
      序号：N / No: N
      话题：xxx / Topic: xxx
      来源：xxx / Source: xxx
      简述：xxx / Summary: xxx
    Returns dict(title, source, number) or None
    """  # noqa: DOCSTRING_CJK
    result = {}
    # ^ + re.MULTILINE 锚定行首，防止匹配到 "有值得分享的话题：" 等前缀行
    # [ \t]* 替代 \s*，只吃水平空白，避免跨行捕获到下一行内容
    patterns = {
        'title': r'^[ \t]*(?:话题|Topic|話題|주제)[ \t]*[：:][ \t]*(.+)',
        'source': r'^[ \t]*(?:来源|Source|出典|출처)[ \t]*[：:][ \t]*(.+)',
        'number': r'^[ \t]*(?:序号|No|番号|번호)\.?[ \t]*[：:][ \t]*(\d+)',
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if match:
            result[key] = match.group(1).strip()
    
    if result.get('title'):
        return result
    return None


def _text_is_pass_sentinel(text: str) -> bool:
    """Return True when ``text`` as a whole is the PASS skip sentinel.

    Brackets are optional: matches both "[PASS]" (the prompted form) and a
    bare "PASS" the model occasionally emits. Phase-agnostic — used by both
    the Phase 1 section parser and the Phase 2 stream guards.
    """
    return bool(re.fullmatch(r'\s*\[?\s*PASS\s*\]?\s*', text or '', re.IGNORECASE))


def _parse_unified_phase1_result(text: str) -> dict:
    """
    Parse the merged Phase 1 LLM output.

    Split into sections by the [WEB] / [MUSIC] / [MEME] markers:
    - web section: reuse the existing regexes to extract title/source/number/summary
    - music section: extract the keyword (or recognize PASS)
    - meme section: same as above

    Returns:
        {
            'web': {'title': ..., 'source': ..., 'number': ...} | None,
            'music_keyword': str | None,    # None means no keyword
            'meme_keyword': str | None,     # None means no keyword
            'web_pass': bool,               # True means this channel explicitly passed
            'music_pass': bool,
            'meme_pass': bool,
        }
    """
    result: dict = {
        'web': None,
        'music_keyword': None,
        'meme_keyword': None,
        'web_pass': False,
        'music_pass': False,
        'meme_pass': False,
    }

    # 按 [WEB] / [MUSIC] / [MEME] 分段
    # 使用正则切分，保留标签
    sections: dict[str, str] = {}
    current_tag = None
    current_lines: list[str] = []

    for line in text.splitlines():
        stripped = line.strip()
        upper = stripped.upper()
        # 检测段标签
        if upper.startswith('[WEB]'):
            if current_tag:
                sections[current_tag] = '\n'.join(current_lines)
            current_tag = 'web'
            # 标签行后面可能有内容（如 [WEB] [PASS]）
            remainder = stripped[5:].strip()
            current_lines = [remainder] if remainder else []
        elif upper.startswith('[MUSIC]'):
            if current_tag:
                sections[current_tag] = '\n'.join(current_lines)
            current_tag = 'music'
            remainder = stripped[7:].strip()
            current_lines = [remainder] if remainder else []
        elif upper.startswith('[MEME]'):
            if current_tag:
                sections[current_tag] = '\n'.join(current_lines)
            current_tag = 'meme'
            remainder = stripped[6:].strip()
            current_lines = [remainder] if remainder else []
        else:
            current_lines.append(line)

    if current_tag:
        sections[current_tag] = '\n'.join(current_lines)

    # 如果 LLM 没有输出段标签（fallback：尝试当作纯 web 输出解析）
    if not sections:
        web_parsed = _parse_web_screening_result(text)
        if web_parsed:
            result['web'] = web_parsed
        return result

    # --- 解析 web 段 ---
    # 先尝试提取结构化字段；LLM 经常同时输出话题详情和模板里的
    # "If nothing is worth sharing: [WEB] [PASS]" 行，导致 [PASS]
    # 误杀已填好的话题。因此优先以 parse 结果为准。
    web_text = sections.get('web', '')
    if web_text:
        parsed_web = _parse_web_screening_result(web_text)
        if parsed_web:
            result['web'] = parsed_web
        elif _text_is_pass_sentinel(web_text):
            result['web_pass'] = True  # 确实是 PASS，web 保持 None

    # --- 解析 music 段 ---
    music_text = sections.get('music', '')
    if music_text:
        music_text = music_text.strip()
        if _text_is_pass_sentinel(music_text):
            result['music_pass'] = True
        elif music_text:
            # 去掉前缀标签（如"关键词：" "keyword:" 等）
            keyword = re.sub(
                r'(?i).*?(?:关键词|搜索(?:关键词)?|keyword|search|キーワード|検索|키워드|검색|ключевое\s*слово|поиск)[：:\s]+',
                '', music_text, count=1
            )
            keyword = keyword.strip('\'"「」【】[]《》<> \n\r\t')
            # 取第一行非空内容
            keyword = keyword.splitlines()[0].strip() if keyword else ''
            if keyword and not re.fullmatch(r'\[?\s*pass\s*\]?', keyword, re.IGNORECASE):
                result['music_keyword'] = keyword

    # --- 解析 meme 段 ---
    meme_text = sections.get('meme', '')
    if meme_text:
        meme_text = meme_text.strip()
        if _text_is_pass_sentinel(meme_text):
            result['meme_pass'] = True
        elif meme_text:
            keyword = re.sub(
                r'(?i).*?(?:关键词|keyword|キーワード|키워드|ключевое\s*слово)[：:\s]+',
                '', meme_text, count=1
            )
            keyword = keyword.strip('\'"「」【】[]《》<> \n\r\t')
            keyword = keyword.splitlines()[0].strip() if keyword else ''
            if keyword and not re.fullmatch(r'\[?\s*pass\s*\]?', keyword, re.IGNORECASE):
                result['meme_keyword'] = keyword

    return result


_PROACTIVE_LEGAL_SOURCE_TAGS = frozenset({"CHAT", "WEB", "PASS", "MUSIC", "MEME"})
_PROACTIVE_SCREEN_TAG_LEAKS = frozenset({"SCREEN", "SCREENSHOT", "VISION", "WINDOW"})
_PROACTIVE_BRACKET_TAG_RE = re.compile(r"^\[([A-Za-z][A-Za-z0-9_-]{0,31})\]\s*")
_PROACTIVE_LEGAL_TAG_RE = re.compile(r"^\[(CHAT|WEB|PASS|MUSIC|MEME)\]\s*", re.IGNORECASE)


def _strip_proactive_screen_tag_leak(text: str) -> tuple[str, str]:
    """Strip mistakenly emitted screen-source tags (e.g. ``[Screen]``) from Phase 2 text.

    In proactive screen-only scenarios the model occasionally spits the screen
    source out as a leading tag. Semantically such a tag just means "chatting
    about something seen on screen" = an ordinary chat, so it is normalized to
    ``CHAT``.

    Returns ``(cleaned_text, recovered_source_tag)``:
    - On hitting a known screen-leak tag → strip it. If a legal source tag
      immediately follows (combinations like ``[Screen][CHAT]``), strip that too
      and adopt the real tag; otherwise fall back to ``CHAT``.
    - No hit (no tag / legal tag / unknown tag) → returned unchanged with an
      empty recovered tag, handing back to the caller's existing no-tag handling
      (format-rescue regen / drop).

    Tag matching is case-insensitive.
    """
    if not text:
        return "", ""
    leading_len = len(text) - len(text.lstrip())
    leading = text[:leading_len]
    body = text[leading_len:]
    match = _PROACTIVE_BRACKET_TAG_RE.match(body)
    if not match:
        return text, ""
    tag = match.group(1).upper()
    if tag in _PROACTIVE_LEGAL_SOURCE_TAGS or tag not in _PROACTIVE_SCREEN_TAG_LEAKS:
        return text, ""
    rest = body[match.end():].lstrip()
    # 兼容 [Screen][CHAT] 组合：泄漏标签后若紧跟合法来源标签，剥掉并采用真实 tag
    # （否则该 [CHAT] 字面会作为正文漏给 TTS）；没有则按 CHAT 兜底。
    legal = _PROACTIVE_LEGAL_TAG_RE.match(rest)
    if legal:
        return leading + rest[legal.end():].lstrip(), legal.group(1).upper()
    return leading + rest, "CHAT"


# Decoration a model may wrap a leaked label in (markdown bold/heading/bullet,
# CJK + ASCII brackets). Stripped from both ends before matching so e.g.
# "**屏幕细节轻问**" / "【回忆线索】" still resolve to the bare label.
_INTENT_LABEL_DECOR = '*-•◦·#`_~【】「」[]《》（）() \t'


def _strip_proactive_intent_label_leak(text: str) -> str:
    """Strip an internal guidance label echoed as a leading heading.

    Weak models sometimes copy a tone-angle seed or memory-cue label from
    the proactive Phase 2 prompt and emit the bare label as the first line
    of the reply; the client then splits it into its own chat bubble. Such
    labels are pure scaffolding and must never be spoken.

    Removes, from the START of ``text`` only and repeating to peel stacked
    labels:
    - a standalone first line that exactly matches a known label (optional
      decoration / trailing colon), when real content follows on a later
      line;
    - a leading ``<label>:`` / ``<label>：`` prefix on the first line,
      keeping the rest of that line as content.

    Exact (decoration-trimmed, casefolded) matching against the derived
    label set keeps generic words from being scrubbed out of normal speech.
    Returns ``text`` unchanged when the leading segment is not a known label.
    """
    if not text:
        return text
    from config.prompts.prompts_activity import get_proactive_intent_leak_labels
    labels = get_proactive_intent_leak_labels()
    if not labels:
        return text

    def _norm(segment: str) -> str:
        out = segment.strip().strip(_INTENT_LABEL_DECOR)
        out = out.rstrip('：:').strip(_INTENT_LABEL_DECOR)
        return out.strip()

    # Bounded peel — a handful of stacked labels at most; never loop the body.
    for _ in range(4):
        body = text.lstrip()
        if not body:
            break
        nl = body.find('\n')
        first = body if nl == -1 else body[:nl]
        rest = '' if nl == -1 else body[nl + 1:]

        # Case 1: the whole first line is a label, with real content after it.
        if rest.strip() and _norm(first).casefold() in labels:
            text = rest
            continue

        # Case 2: "<label>：<content>" sharing one line. Take the EARLIEST
        # colon (full- or half-width), not full-width-first — otherwise a
        # half-width separator followed by a full-width colon in the body
        # (e.g. "Memory cues: ...：...") would split on the wrong colon and
        # leave the leading label unstripped.
        sep_idx = -1
        for sep in ('：', ':'):
            idx = first.find(sep)
            if idx > 0 and (sep_idx == -1 or idx < sep_idx):
                sep_idx = idx
        if sep_idx > 0:
            cand = _norm(first[:sep_idx]).casefold()
            after = first[sep_idx + 1:].strip()
            if cand in labels and (after or rest.strip()):
                if after:
                    text = after + ('\n' + rest if rest else '')
                else:
                    text = rest
                continue
        break
    return text


def _lookup_link_by_title(title: str, all_links: list[dict]) -> dict | None:
    """
    Look up the link matching a Phase 1 output title in all_web_links.
    Matching logic:
    - exact match (ignoring case and surrounding whitespace)
    - partial match (title contains or is contained, ignoring case and surrounding whitespace)
    """
    title_lower = title.lower().strip()
    for link in all_links:
        link_title = link.get('title', '').lower().strip()
        if not link_title:
            continue
        if link_title == title_lower or link_title in title_lower or title_lower in link_title:
            return link
    return None


def _format_recent_proactive_chats(lanlan_name: str, lang: str = 'zh') -> str:
    """
    Format recent proactive-chat records into a text block injectable into the prompt (with relative time and source channel).
    Logic:
    - fetch the given model's proactive-chat records from _proactive_chat_history
    - filter to records within the last _RECENT_CHAT_MAX_AGE_SECONDS seconds
    - format the time label according to lang ('zh', 'en', 'ja', 'ko')
    - format the source channel label ('vision', 'web')
    """
    history = _proactive_chat_history.get(lanlan_name)
    if not history:
        return ""
    now = time.time()
    recent = [entry for entry in history if now - entry[0] < _RECENT_CHAT_MAX_AGE_SECONDS]
    if not recent:
        return ""

    tl = RECENT_PROACTIVE_TIME_LABELS.get(lang, RECENT_PROACTIVE_TIME_LABELS['en'])
    cl = RECENT_PROACTIVE_CHANNEL_LABELS.get(lang, RECENT_PROACTIVE_CHANNEL_LABELS['en'])

    def _rel(ts):
        """
        Format the time label.
        args:
        - ts: timestamp (seconds)
        returns:
        - str: formatted time label
        """
        d = int(now - ts)
        if d < 60:
            return tl[0]
        m = d // 60
        if m < 60:
            return tl['m'].format(m)
        return tl['h'].format(m // 60)

    header = _loc(RECENT_PROACTIVE_CHATS_HEADER, lang)
    footer = _loc(RECENT_PROACTIVE_CHATS_FOOTER, lang)
    lines = []
    for entry in recent:
        ts, msg = entry[0], entry[1]
        ch = entry[2] if len(entry) > 2 else ''
        # 过滤掉 vision 通道的记录，避免 AI 引用已过期的屏幕内容产生幻觉
        if ch == 'vision':
            continue
        tag = _rel(ts)
        if ch:
            tag += f"·{cl.get(ch, ch)}"
        lines.append(f"- [{tag}] {msg}")
    if not lines:
        return ""
    return f"\n{header}\n" + "\n".join(lines) + f"\n{footer}\n"


# Reminiscence usage buffer — separate from _proactive_chat_history because
# the latter feeds dedup / similarity checks (_format_recent_proactive_chats /
# _is_similar_to_recent_proactive_chat) and any double-recording there would
# inflate similarity scores against its own message. This buffer is read
# only by _compute_source_weights to factor reminiscence into channel
# weight decay alongside web/news/etc.
#
# Why 50 (not tied to PROACTIVE_CHAT_HISTORY_MAX=10): the two buffers serve
# opposite sizing constraints. PROACTIVE_CHAT_HISTORY_MAX bounds *dedup*
# memory (1h text-similarity check, 10 entries are plenty). This buffer
# bounds *decay-signal completeness* — _compute_source_weights walks every
# timestamp inside the _SOURCE_WEIGHT_WINDOW (=1h) for the exponential
# decay sum, so the maxlen MUST be larger than the worst-case usage count
# in that window or oldest entries get evicted and the channel under-
# counts. 50 leaves ~5× safety margin for high-cadence proactive cycles.
# Kept as a private module constant alongside the other _SOURCE_WEIGHT_*
# tunables (_SOURCE_WEIGHT_DECAY_LAMBDA / _K / _FLOOR / _WINDOW) — it's
# tied to that model's calibration, not a user-facing config knob.
_REMINISCENCE_USAGE_MAX = 50
_reminiscence_usage_history: dict[str, deque[float]] = {}


def _record_reminiscence_usage(lanlan_name: str) -> None:
    """Record one reminiscence usage timestamp for source-weight decay.

    Kept separate from ``_record_proactive_chat`` to avoid polluting
    the dedup / similarity history (which compares the proactive
    response text against past entries by channel-agnostic match).
    """
    if lanlan_name not in _reminiscence_usage_history:
        _reminiscence_usage_history[lanlan_name] = deque(maxlen=_REMINISCENCE_USAGE_MAX)
    _reminiscence_usage_history[lanlan_name].append(time.time())


def _record_proactive_chat(lanlan_name: str, message: str, channel: str = ''):
    """
    Record one successful proactive chat (with its source channel).
    Logic:
    - get the current timestamp
    - append the record (timestamp, message content, channel) to the given model's queue in _proactive_chat_history
    - if the queue is full, the oldest record is popped automatically, keeping the length within maxlen (default 10)
    args:
    - lanlan_name: model name
    - message: chat content
    - channel: source channel (optional, default 'vision')
    """
    if lanlan_name not in _proactive_chat_history:
        _proactive_chat_history[lanlan_name] = deque(maxlen=PROACTIVE_CHAT_HISTORY_MAX)
    _proactive_chat_history[lanlan_name].append((time.time(), message, channel))

    # Telemetry：主动搭话实际投递。channel 是低基数 enum（vision/news/video/
    # personal/music/meme/mini_game/...），截断防意外高基数。配合 settings_state
    # 的 proactive 配置档，能看深度用户每天实际被主动搭话几次。
    #
    # 不在这里做 responded 回应率配对：用户消息分发在 core.py（main_logic 层），
    # 主动搭话在 main_routers 层，module-layering CI 禁止 core.py 反向 import
    # system_router；跨层共享"上次投递时刻"状态会破坏分层。回应率由 server 端
    # 用 proactive_fired 时刻与用户消息活动 timestamp 关联粗估即可，要精确配对
    # 再单独开 PR。
    try:
        from utils.instrument import counter as _instr_counter
        _instr_counter("proactive_fired", channel=(str(channel) or "default")[:24])
    except Exception:
        # 埋点失败不能影响主动搭话投递
        pass


def _normalize_material_key(raw: str) -> str:
    """Normalize a material identity string for exact-match dedup (lowercase + collapse whitespace)."""
    s = (raw or "").strip().lower()
    return re.sub(r'\s+', ' ', s)


def _proactive_material_key(
    source_tag: str | None,
    selected_music_link: dict | None,
    meme_content: dict | None,
) -> str:
    """Compute the dedup identity of the material this round pushes.

    - MUSIC → the picked track (title|artist); two different songs never collide
    - MEME → the **search keyword** (not the image): same keyword reused soon is a
      repeat, a fresh keyword is not. Random hot-word fallback has an empty keyword
      → empty key → treated as "never a repeat" (each random fetch is varied)

    Empty/unknown → "" (caller treats as non-repeat, i.e. always exempt).
    """
    if source_tag == 'MUSIC' and selected_music_link:
        title = (selected_music_link.get('title') or '').strip()
        artist = (selected_music_link.get('artist') or '').strip()
        return _normalize_material_key(f"{title}|{artist}") if (title or artist) else ""
    if source_tag == 'MEME' and meme_content:
        return _normalize_material_key(meme_content.get('keyword') or '')
    return ""


def _is_recent_proactive_material(lanlan_name: str, source_tag: str, key: str) -> bool:
    """Whether *key* was pushed for *source_tag* within the recent window (exact match).

    Empty key → never a repeat (no material identity to compare on).
    """
    if not key:
        return False
    bucket = _proactive_material_history.get(lanlan_name, {}).get(source_tag)
    if not bucket:
        return False
    now = time.time()
    return any(
        k == key and now - ts < _RECENT_CHAT_MAX_AGE_SECONDS
        for ts, k in bucket
    )


def _record_proactive_material(lanlan_name: str, source_tag: str, key: str) -> None:
    """Record one successfully delivered material identity (skip empty keys)."""
    if not key:
        return
    per_tag = _proactive_material_history.setdefault(lanlan_name, {})
    if source_tag not in per_tag:
        per_tag[source_tag] = deque(maxlen=_PROACTIVE_MATERIAL_HISTORY_MAX)
    per_tag[source_tag].append((time.time(), key))


def _open_threads_for_activity_state(activity_snapshot, fresh_open_threads) -> list[str]:
    """Return semantic open_threads that should render in activity state.

    ``unfinished_thread`` is a stronger, rule-based continuation signal (the
    previous AI question is still hanging and may bypass normal propensity).
    When it exists, suppress softer LLM-enriched open_threads so Phase 2 sees a
    single follow-up surface. Also suppress open_threads during
    ``restricted_screen_only`` states: those rounds allow screen-derived chatter
    only, with unfinished_thread as the explicit text-only continuation
    exception. Otherwise keep open_threads in activity state, where they sit
    next to live state/tone rather than old reminiscence.
    """
    if activity_snapshot is None:
        return list(fresh_open_threads or [])
    if getattr(activity_snapshot, 'unfinished_thread', None) is not None:
        return []
    if getattr(activity_snapshot, 'propensity', None) == 'restricted_screen_only':
        return []
    return list(fresh_open_threads or [])


def _render_followup_topic_hooks(
    proactive_lang: str,
    followup_topics: list[dict[str, Any]],
) -> tuple[str, list[Any]]:
    """Render follow-up topic hooks and return the surfaced reflection ids.

    Only reflections whose text actually survives build_topic_hook_prompt's
    blank/duplicate filter are reported as surfaced. Otherwise a blank or
    duplicate followup inside the first three would still be recorded via
    /record_surfaced and pushed into cooldown even though the model never saw
    it. Semantic open_threads intentionally do not flow through this helper:
    they render inside the activity-state section, where the live state/tone
    and decision rules can arbitrate them separately from old reminiscence.
    """
    if not followup_topics:
        return "", []

    from main_logic.topic.common import clean_text
    from main_logic.topic.hooks import build_topic_hook_prompt

    rendered_followup_topics = followup_topics[:3]
    prompt = build_topic_hook_prompt(
        proactive_lang,
        followup_topics=rendered_followup_topics,
    )
    if not prompt:
        return "", []

    # Mirror _iter_followup_texts: drop blanks/duplicates so the surfaced ids
    # match exactly what the prompt rendered.
    surfaced_reflection_ids: list[Any] = []
    seen_texts: set[str] = set()
    for topic in rendered_followup_topics:
        text = clean_text(topic.get('text'))
        if not text or text in seen_texts:
            continue
        seen_texts.add(text)
        if topic.get('id'):
            surfaced_reflection_ids.append(topic['id'])
    return prompt, surfaced_reflection_ids


# ---------- Mini-game 邀请短路状态管理 ----------
# 入口在 proactive_chat 内部、过完 propensity / skip_probability /
# restricted_screen_only 几道门之后调 _maybe_deliver_mini_game_invite。命中
# 即静态 i18n 模板 → feed_tts_chunk + finish_proactive_delivery 直投递；不走
# Phase 1/2 LLM。冷却语义：一次邀请被回应后，必须同时跨过
#   ``time.time() - responded_at >= threshold_by_choice``
#     其中 threshold = MINI_GAME_INVITE_COOLDOWN_AFTER_ACCEPT_SECONDS (2h) 若
#     last_response_choice='accept'，否则 ..._AFTER_DECLINE_SECONDS (5h)；
# 与
#   ``chats_since_response >= MINI_GAME_INVITE_COOLDOWN_CHATS``
# 才允许下次掷骰。pending（投递了但还没被回应）期间一律抑制，避免邀请挂着
# 不响应又再发第二次。

def _mini_game_invite_get_state(lanlan_name: str) -> dict[str, Any]:
    """Lazy-init per-character state。"""
    state = _mini_game_invite_state.get(lanlan_name)
    if state is None:
        state = {
            'delivered_at': None,
            'responded_at': None,
            'chats_since_response': 0,
            'last_game_type': None,
            'response_cooldowns': {},
            # 当前 pending 邀请的 session_id；endpoint 收到回应时校验匹配，避免
            # 用户点击过期邀请被错算成响应当前 pending。一旦投递新邀请会被刷新。
            'pending_session_id': None,
            # D2「回头再说」短期抑制：reset 后不允许下一次 proactive 立刻又掷骰。
            # _in_cooldown 多查一道这个 gate。秒级 epoch；None = 不抑制。
            'suppressed_until': None,
            # accept/decline 走不同 cooldown 阈值。later/隐式 dismiss reset 时
            # 清回 None。pending 期间也是 None；cooldown 函数只在 responded_at
            # !=None 时读它。
            'last_response_choice': None,
        }
        _mini_game_invite_state[lanlan_name] = state
    else:
        state.setdefault('response_cooldowns', {})
    return state


def _proactive_chat_totals_path() -> Path:
    return Path(get_config_manager().memory_dir) / _PROACTIVE_CHAT_TOTALS_FILENAME


async def _ensure_proactive_chat_totals_loaded() -> None:
    """Lazy-load the persisted cumulative counters + ever_delivered. Idempotent. File reads go to the thread pool.

    schema: {"version": 2,
             "totals": {<lanlan_name>: <int>, ...},
             "ever_delivered": {<lanlan_name>: true, ...}}

    A missing file / corrupted JSON is not fatal — start from empty, and the next
    increment writes a fresh file. The old schema v1 has no ever_delivered field,
    so it loads as an empty dict — after upgrading, the first proactive chat will
    "force-first re-deliver once" for existing users (at most once, because
    ever_delivered is set True and persisted immediately after delivery); this is
    a one-off v1→v2 migration cost and needs no dedicated migration script."""
    global _proactive_chat_totals_loaded
    if _proactive_chat_totals_loaded:
        return
    async with _proactive_chat_totals_lock:
        if _proactive_chat_totals_loaded:
            return
        path = _proactive_chat_totals_path()
        try:
            data = await asyncio.to_thread(read_json, path)
            totals = data.get('totals') if isinstance(data, dict) else None
            if isinstance(totals, dict):
                for k, v in totals.items():
                    if isinstance(k, str) and isinstance(v, (int, float)):
                        _proactive_chat_totals[k] = int(v)
            ever = data.get('ever_delivered') if isinstance(data, dict) else None
            if isinstance(ever, dict):
                for k, v in ever.items():
                    if isinstance(k, str) and bool(v):
                        _invite_ever_delivered[k] = True
        except FileNotFoundError:
            # 首次启动 / cleanup 后没文件——按全空起步，下次 increment 会创建。
            # 不是异常，不打 warning。
            pass
        except Exception as exc:
            logger.warning("proactive_chat_totals load failed: %s", exc)
        _proactive_chat_totals_loaded = True


def _get_proactive_chat_total(lanlan_name: str) -> int:
    """Synchronous read of cached counter. 0 if loaded-but-unset or not loaded yet.

    `_maybe_deliver_mini_game_invite` calls this after the caller has already
    awaited `_ensure_proactive_chat_totals_loaded()`, so there is no await here."""
    return int(_proactive_chat_totals.get(lanlan_name, 0))


def _was_invite_ever_delivered(lanlan_name: str) -> bool:
    """Synchronous read of ever-delivered flag.

    The caller must await ``_ensure_proactive_chat_totals_loaded()`` first."""
    return bool(_invite_ever_delivered.get(lanlan_name, False))


async def _persist_totals_unlocked() -> None:
    """Persist totals + ever_delivered to disk. The caller must hold _proactive_chat_totals_lock."""
    try:
        await atomic_write_json_async(
            _proactive_chat_totals_path(),
            {
                'version': _PROACTIVE_CHAT_TOTALS_SCHEMA_VERSION,
                'totals': dict(_proactive_chat_totals),
                'ever_delivered': dict(_invite_ever_delivered),
            },
            indent=2,
            ensure_ascii=False,
        )
    except Exception as exc:
        logger.warning(
            "proactive_chat_totals persist failed (in-memory still up-to-date): %s",
            exc,
        )


async def _increment_proactive_chat_total(lanlan_name: str) -> int:
    """+1 cached counter and persist atomically. Returns new value.

    Serialization is guaranteed by ``_proactive_chat_totals_lock``: concurrent
    proactive_chat calls each await a serial update, so no increment is lost.
    Persistence failures are not raised to the caller — the counter is
    best-effort; losing one +1 is not fatal, but the log line is kept."""
    await _ensure_proactive_chat_totals_loaded()
    async with _proactive_chat_totals_lock:
        new_value = _proactive_chat_totals.get(lanlan_name, 0) + 1
        _proactive_chat_totals[lanlan_name] = new_value
        await _persist_totals_unlocked()
    return new_value


async def _mark_invite_ever_delivered(lanlan_name: str) -> None:
    """One-shot set-True + persist. Skips the disk write when already True to save IO.

    Shares ``_proactive_chat_totals_lock`` with ``_increment_proactive_chat_total``
    so concurrent updates write totals + ever_delivered together atomically.

    ⚠️ The invite delivery path must not call ``_increment_proactive_chat_total +
    _mark_invite_ever_delivered`` separately — the lock is released between the
    two awaits, and a process dying in between leaves a ``totals: N+1,
    ever_delivered: stale`` half-state on disk, making force-first fire once more
    after restart. Use ``_record_invite_delivery_persistent`` for one atomic
    write under a single lock."""
    await _ensure_proactive_chat_totals_loaded()
    async with _proactive_chat_totals_lock:
        if _invite_ever_delivered.get(lanlan_name):
            return
        _invite_ever_delivered[lanlan_name] = True
        await _persist_totals_unlocked()


async def _record_invite_delivery_persistent(lanlan_name: str) -> int:
    """Atomic persistent record of one successfully delivered mini-game invite:
    counter +1 + ever_delivered=True written to disk once under one lock.
    Returns the new total.

    Reason to exist: doing +1 then mark as two separate awaits releases the lock
    in between; a process crash / coroutine cancel can leave a ``totals: N+1,
    ever_delivered: stale`` half-state on disk — after restart
    ``_was_invite_ever_delivered`` sees the stale false and force-first fires
    again. Pointed out by CodeRabbit Major review."""
    await _ensure_proactive_chat_totals_loaded()
    async with _proactive_chat_totals_lock:
        new_value = _proactive_chat_totals.get(lanlan_name, 0) + 1
        _proactive_chat_totals[lanlan_name] = new_value
        _invite_ever_delivered[lanlan_name] = True
        await _persist_totals_unlocked()
    return new_value


def _mini_game_invite_advance_response(
    lanlan_name: str, last_user_msg_at: float | None,
) -> dict[str, Any] | None:
    """During a pending invite the user sent any ordinary message (not an explicit
    choice / keyword hit) → silently dismiss the prompt + 5min short suppression,
    **without** starting the long cooldown.

    Returns: a dict with the same shape as ``_apply_mini_game_invite_choice``
    (containing ``action='suppress'`` + ``session_id``); the caller uses it to
    push the ``mini_game_invite_resolved`` WS event so the frontend dismisses the
    UI. Returns None when there is nothing to do.

    Called once on every entry into proactive_chat (both the voice fast path and
    the text path). last_user_msg_at is "the timestamp of the user's last
    activity" — the caller is responsible for deriving it from the right source:
    the text path back-computes it from activity_snapshot.seconds_since_user_msg;
    the voice path uses mgr.last_user_activity_time directly (voice does not go
    through the activity tracker, but the session itself tracks RMS / text input
    activity). If either is missing (None), this is a noop.

    Difference between historical and current semantics (changed after CodeRabbit
    Major):
    - Old PR #1141 era: no ChoicePrompt; "the user spoke after the invite" =
      "implicit response" → mark responded_at directly, starting the 1h+10 chats
      long cooldown.
    - Now PR #1145 introduces explicit three-option buttons + a keyword text
      fallback; the long-cooldown semantics should only be triggered by an
      **explicit choice** (accept / decline). Any non-matching message merely
      "dismisses the prompt" — keeping ever_delivered (force-first will not fire
      again) + a 5min short suppression (so the next proactive does not
      immediately re-invite), but no long lock. Equivalent to the 'later'
      choice. Otherwise, if the user first says something else and then clicks a
      button → the endpoint sees responded_at != None → "expired", and the state
      has quietly entered the 1h long cooldown (violating D2 semantics, bad UX)."""
    state = _mini_game_invite_state.get(lanlan_name)
    if not state:
        return None
    if state['delivered_at'] is None or state['responded_at'] is not None:
        return None
    if last_user_msg_at is None:
        return None
    if last_user_msg_at <= state['delivered_at']:
        return None
    # 任意消息 = 隐式 dismiss → 等同 'later' choice 的 reset+短抑制语义。
    # 复用 _apply_mini_game_invite_choice 保持单一事实源；source 标 'implicit_dismiss'
    # 让日志能区分按钮路径与隐式路径。
    return _apply_mini_game_invite_choice(
        lanlan_name, 'later', source='implicit_dismiss',
    )


def _mini_game_invite_in_cooldown(lanlan_name: str, game_type: str | None = None) -> bool:
    """Return whether this character is in a mini-game invite cooldown.

    A true value means the current turn should not roll another invite. This
    covers short suppression from the "later" choice, pending invites, and
    replied invites that have not crossed both the time and chat-count gates.
    Cooldowns after completed responses are scoped to the same game type, while
    pending invites still suppress all game types for the character.
    """
    state = _mini_game_invite_state.get(lanlan_name)
    if not state:
        return False
    suppressed_until = state.get('suppressed_until')
    if suppressed_until is not None and time.time() < float(suppressed_until):
        return True
    if game_type:
        cooldowns = state.get('response_cooldowns')
        if isinstance(cooldowns, dict) and isinstance(cooldowns.get(game_type), dict):
            response_state = cooldowns[game_type]
            elapsed = time.time() - float(response_state.get('responded_at') or 0.0)
            if response_state.get('last_response_choice') == 'decline':
                time_threshold = MINI_GAME_INVITE_COOLDOWN_AFTER_DECLINE_SECONDS
            else:
                time_threshold = MINI_GAME_INVITE_COOLDOWN_AFTER_ACCEPT_SECONDS
            chats_since_response = int(response_state.get('chats_since_response') or 0)
            if elapsed < time_threshold or chats_since_response < MINI_GAME_INVITE_COOLDOWN_CHATS:
                return True
            cooldowns.pop(game_type, None)
    if game_type:
        last_game_type = state.get('last_game_type')
        pending = state.get('delivered_at') is not None and state.get('responded_at') is None
        if last_game_type and last_game_type != game_type and not pending:
            return False
    if state['delivered_at'] is None:
        return False
    if state['responded_at'] is None:
        return True
    elapsed = time.time() - state['responded_at']
    if state.get('last_response_choice') == 'decline':
        time_threshold = MINI_GAME_INVITE_COOLDOWN_AFTER_DECLINE_SECONDS
    else:
        time_threshold = MINI_GAME_INVITE_COOLDOWN_AFTER_ACCEPT_SECONDS
    return (
        elapsed < time_threshold
        or state['chats_since_response'] < MINI_GAME_INVITE_COOLDOWN_CHATS
    )


def _mini_game_invite_record_delivered(lanlan_name: str, session_id: str) -> None:
    """Record a successfully delivered invite. Resets responded/counter, entering a new pending round.

    session_id comes from the caller (the uuid generated by
    ``_maybe_deliver_mini_game_invite`` at delivery); the endpoint verifies the
    user's response against the current pending one. A new delivery refreshes
    this id — a stale session_id left by the previous delivery is recognized as
    stale and rejected on the endpoint side."""
    state = _mini_game_invite_get_state(lanlan_name)
    state['delivered_at'] = time.time()
    state['responded_at'] = None
    state['chats_since_response'] = 0
    state['pending_session_id'] = session_id
    state['last_response_choice'] = None
    # 新邀请投递清掉 D2 的 short-suppression：本来是「上次回头再说」的窗口，
    # 既然现在又投了新邀请说明那个窗口已过期，没必要保留。
    state['suppressed_until'] = None


def _mini_game_invite_count_post_response_chat(lanlan_name: str) -> None:
    """Advance invite cooldown chat counters after a delivered proactive turn.

    This runs immediately after _record_proactive_chat. Any channel counts as
    long as the AI actually delivered a proactive message. Pending invites are
    no-ops so the invite message itself does not spend the response gate.
    """
    state = _mini_game_invite_state.get(lanlan_name)
    if not state:
        return
    if state.get('delivered_at') is not None and state.get('responded_at') is None:
        return
    if state.get('responded_at') is not None:
        state['chats_since_response'] += 1
    cooldowns = state.get('response_cooldowns')
    if isinstance(cooldowns, dict):
        for response_state in cooldowns.values():
            if isinstance(response_state, dict) and response_state.get('responded_at') is not None:
                response_state['chats_since_response'] = int(response_state.get('chats_since_response') or 0) + 1


def _mini_game_invite_record_response_cooldown(
    state: dict[str, Any],
    game_type: str,
    choice: str,
    responded_at: float,
) -> None:
    cooldowns = state.setdefault('response_cooldowns', {})
    if not isinstance(cooldowns, dict):
        cooldowns = {}
        state['response_cooldowns'] = cooldowns
    cooldowns[game_type] = {
        'responded_at': responded_at,
        'chats_since_response': 0,
        'last_response_choice': choice,
    }


def _mini_game_launch_url(game_type: str, lanlan_name: str, session_id: str) -> str | None:
    url_template = MINI_GAME_LAUNCH_URL_BY_GAME.get(game_type)
    if not url_template:
        return None
    from urllib.parse import urlencode as _urlencode

    query = {
        "lanlan_name": lanlan_name,
        "session_id": session_id,
    }
    separator = "&" if "?" in url_template else "?"
    return f"{url_template}{separator}{_urlencode(query)}"


def _pick_mini_game_type(lanlan_name: str | None = None) -> str | None:
    """Pick an available mini-game type with invite copy configured.

    Games missing invite lines are skipped, and character-specific cooldowns are
    respected when a character name is provided.
    """
    candidates = [
        g for g in MINI_GAME_INVITE_AVAILABLE_GAMES
        if g in MINI_GAME_INVITE_LINES_BY_GAME
    ]
    if lanlan_name:
        candidates = [
            g for g in candidates
            if not _mini_game_invite_in_cooldown(lanlan_name, g)
        ]
    if not candidates:
        return None
    import random as _random
    return _random.choice(candidates)


def _resolve_proactive_locale(data: dict, mgr) -> str:
    """Resolve the active user locale for proactive chat flows.

    Request data wins first, websocket session language is the second source of
    truth, and the process-level global language is only a final fallback. This
    keeps proactive invite copy and Phase 1-2 LLM output aligned with the live
    session whenever frontend i18n has already reported the user's language.
    """
    request_lang = data.get('language') or data.get('lang') or data.get('i18n_language')
    # 与 ``main_routers/game_router._absorb_request_language`` 同形：第三方客户端 /
    # corrupted localStorage 可能传 ``'undefined'`` / ``'estonian'`` 等 garbage，
    # ``normalize_language_code`` 对未识别值默认回退 ``'en'``——必须先用公共白名单
    # 挡掉，否则 proactive 邀请文案会被静默短路成英文，错过本应命中的 session 真值。
    if request_lang and is_supported_language_code(request_lang):
        normalized = normalize_language_code(request_lang, format='short')
        if normalized:
            return normalized
    session_lang = getattr(mgr, 'user_language', None)
    if session_lang:
        normalized = normalize_language_code(session_lang, format='short')
        if normalized:
            return normalized
    return get_global_language() or 'en'


def _resolve_topic_hook_locale(data: dict, mgr, *, fallback: str) -> str:
    """Resolve the locale for topic-hook prompts without collapsing zh-TW."""
    for raw_lang in (
        data.get('language'),
        data.get('lang'),
        data.get('i18n_language'),
        getattr(mgr, 'user_language', None),
    ):
        if raw_lang and is_supported_language_code(raw_lang):
            normalized = normalize_language_code(raw_lang, format='full')
            if normalized:
                return normalized
    global_lang = normalize_language_code(get_global_language_full(), format='full')
    if global_lang:
        return global_lang
    return fallback


async def _maybe_deliver_mini_game_invite(
    *,
    lanlan_name: str,
    mgr,
    activity_snapshot,
    invite_lang: str,
    master_name: str,
    user_toggle_enabled: bool = True,
) -> dict | None:
    """On a hit, deliver the mini-game invite and return the JSON dict for _end_proactive; returns None on no hit.

    Short-circuit conditions (any one unmet → return None and the caller
    continues the original Phase1/2 pipeline):
      - MINI_GAME_INVITE_ENABLED=False (global kill switch, the production master toggle)
      - user_toggle_enabled=False (the user turned the
        ``proactiveMiniGameInviteEnabled`` toggle off in the frontend CHAT_MODE_CONFIG)
      - activity_snapshot is None (privacy mode / tracker unavailable — be conservative, do not send)
      - propensity == 'restricted_screen_only' (focused_work / non-casual gaming)
      - state == 'away' (user absent; nobody to receive the invite)
      - activity_snapshot.unfinished_thread is not None (the AI just asked a
        question the user has not answered; following the thread takes priority
        over changing topics — aligned with the precedence convention of
        skip_probability / restricted_screen_only over unfinished_thread)
      - _mini_game_invite_in_cooldown
      - on the non-force-first path, random() >= MINI_GAME_INVITE_TRIGGER_PROBABILITY

    Debug flag: when ``config.MINI_GAME_INVITE_FORCE_GAME_TYPE`` is non-None it
    bypasses every gate except ``MINI_GAME_INVITE_ENABLED`` (including the user
    toggle, cooldown, probability, unfinished_thread, snapshot None / propensity
    / away, and the force-first decision), pinning game_type to the flag value.
    Local manual testing only; keep it None in production.

    Force-first branch: when
      ``state.delivered_at is None`` and
      ``proactive_chat_total >= MINI_GAME_INVITE_NEW_USER_FORCE_AT - 1``
    the 10% dice roll is bypassed and the invite goes straight out — giving
    users who have never played one deterministic "being invited" moment instead
    of relying on probability. The other gates (propensity / unfinished_thread /
    cooldown) still apply.

    The delivery path fully mirrors
    ``main_routers/game_router._deliver_postgame_text_bubble``:
    prepare_proactive_delivery → feed_tts_chunk → finish_proactive_delivery.
    No Phase 1/2 LLM involved; the line is picked from
    ``MINI_GAME_INVITE_LINES_BY_GAME[game_type]`` and game_type comes from
    random.choice over ``MINI_GAME_INVITE_AVAILABLE_GAMES``."""
    if not MINI_GAME_INVITE_ENABLED:
        return None

    # 调试旗标短路：非 None 时跳过所有 snapshot/cooldown/概率 gate，把 game_type
    # 钉到旗标值上。仍然要求该 game_type 有对应文案；非法值 warn + 退出而不 raise，
    # 避免在配置抖动时把整个 proactive 流水线带挂。Force-first 标记成 True 让 caller
    # 路径与正常 first-time 邀请等价（不影响 ever_delivered 持久化）。
    #
    # 但用户级 toggle (proactiveMiniGameInviteEnabled) 仍要尊重——开发者本机
    # 调试用旗标不应该绕过普通用户在前端关掉 mini-game source 的明确意图。
    force_game = MINI_GAME_INVITE_FORCE_GAME_TYPE
    debug_force = bool(force_game)
    if debug_force and not user_toggle_enabled:
        return None
    if debug_force:
        if force_game not in MINI_GAME_INVITE_LINES_BY_GAME:
            logger.warning(
                "[%s] MINI_GAME_INVITE_FORCE_GAME_TYPE=%r is not in "
                "MINI_GAME_INVITE_LINES_BY_GAME=%r — skipping invite. "
                "Set the flag to a valid key or back to None.",
                lanlan_name, force_game, list(MINI_GAME_INVITE_LINES_BY_GAME.keys()),
            )
            return None
        await _ensure_proactive_chat_totals_loaded()
        game_type = force_game
        # 让下面 success-log 共用同一字段；调试旗标语义上等同于 "强制走 first-time
        # 路径"，print 出来好认。
        force_first = True
    else:
        if not user_toggle_enabled:
            return None
        if activity_snapshot is None:
            return None
        propensity = getattr(activity_snapshot, 'propensity', None)
        state_label = getattr(activity_snapshot, 'state', None)
        if propensity == 'restricted_screen_only':
            return None
        if state_label == 'away':
            return None
        # AI 上一轮抛了问题（含 ?/吗/呢/么 等）用户还没接 → 跟进 thread 优先。
        # skip_probability 在 system_router.py 同一文件的 propensity 段也是这条
        # 优先级，统一不让 mini-game 邀请把 promised follow-up 抢走。
        if getattr(activity_snapshot, 'unfinished_thread', None) is not None:
            return None
        # Force-first：从未发过邀请 + 累计已成功投递 N-1 条主动搭话 → 本条强制变邀请。
        # proactive_chat_total 在 _record_proactive_chat 之后才 +1，所以"第 N 次"的
        # 当下值是 N-1。计数走持久化文件，跨重启保留——否则用户每次重启都再"第 N 次"
        # 一回，邀请密度抖。
        #
        # "is new user" 必须查持久化的 ever_delivered，不能查 in-memory 的
        # ``state.delivered_at is None``——后者会被 PR-B「回头再说」reset，且重启清零；
        # codex review (P1) 指出，没这条 force-first 在每次重启后都会把已邀请过的
        # 用户当新用户重新强制邀请。
        await _ensure_proactive_chat_totals_loaded()
        never_delivered = not _was_invite_ever_delivered(lanlan_name)
        total_so_far = _get_proactive_chat_total(lanlan_name)
        force_first = (
            never_delivered
            and total_so_far >= max(0, MINI_GAME_INVITE_NEW_USER_FORCE_AT - 1)
        )

        game_type = _pick_mini_game_type(lanlan_name)
        if game_type is None:
            logger.warning(
                "[%s] mini-game invite skipped: no game_type available "
                "(MINI_GAME_INVITE_AVAILABLE_GAMES=%r, LINES keys=%r)",
                lanlan_name,
                MINI_GAME_INVITE_AVAILABLE_GAMES,
                list(MINI_GAME_INVITE_LINES_BY_GAME.keys()),
            )
            return None

        if not force_first:
            import random as _random
            if _random.random() >= MINI_GAME_INVITE_TRIGGER_PROBABILITY:
                return None
    template = _loc(MINI_GAME_INVITE_LINES_BY_GAME[game_type], invite_lang)
    safe_master = (master_name or '').strip()
    try:
        invite_text = template.format(master_name=safe_master).strip()
    except Exception:
        invite_text = template.replace('{master_name}', safe_master).strip()
    if not invite_text:
        return None

    if not await mgr.prepare_proactive_delivery(min_idle_secs=10.0):
        return _proactive_pass_body(
            PROACTIVE_REASON_PASS_DELIVERY_BUSY,
            message="mini-game invite skipped: prepare_proactive_delivery refused",
        )
    proactive_sid = mgr.current_speech_id
    from main_logic.session_state import SessionEvent as _SE
    await mgr.state.fire(_SE.PROACTIVE_PHASE2)
    try:
        feed = getattr(mgr, 'feed_tts_chunk', None)
        if callable(feed):
            await feed(invite_text, expected_speech_id=proactive_sid)
    except Exception as exc:
        logger.warning(
            "[%s] mini-game invite feed_tts_chunk failed: %s", lanlan_name, exc,
        )
    committed = await mgr.finish_proactive_delivery(
        invite_text,
        expected_speech_id=proactive_sid,
    )
    if not committed:
        return _proactive_pass_body(
            PROACTIVE_REASON_DELIVERY_PREEMPTED,
            message="mini-game invite skipped: user took over before delivery",
        )
    # 给本次邀请生成独立 session_id，前端按钮点击 / 文本关键词命中走 endpoint 时
    # 必须带回这个 id 给后端校验：避免 stale 邀请的延迟回应被错算成响应当前 pending。
    invite_session_id = str(uuid4())

    _record_proactive_chat(lanlan_name, invite_text, channel='mini_game')
    _mini_game_invite_record_delivered(lanlan_name, invite_session_id)
    _mini_game_invite_get_state(lanlan_name)['last_game_type'] = game_type
    # counter +1 + ever_delivered=True 一把锁内原子写盘。两份持久化数据必须
    # 一起落盘，否则 partial-state（totals 已 +1 但 ever_delivered 还是旧 false）
    # 会让重启后 force-first 重复触发——CodeRabbit Major review 指出。
    await _record_invite_delivery_persistent(lanlan_name)

    try:
        from utils.instrument import counter as _instr_counter
        # channel 维度区分两条邀请投递通道：proactive（本函数）与 work_break
        # （水分提醒组合路径，见 _deliver_break_reminder_via_llm 下游）。两条都
        # 共享同一 invite state/cooldown，邀请总数需把两通道相加。force_first 仅
        # proactive 通道有意义。
        _instr_counter(
            "mini_game_invited",
            game_type=str(game_type)[:24],
            channel="proactive",
            force_first=bool(force_first),
        )
    except Exception:
        # 埋点失败不能影响邀请投递
        pass

    # 推 WS message 给前端展示三选项按钮。前端复用 ChoicePrompt 抽象（与 galgame
    # options 共用渲染），但 source='mini_game_invite' 走独立 endpoint，不翻
    # galgame mode 开关。Pet 主窗收到后通过现有 RAW_MESSAGE IPC forwarding 自动
    # 转给 chat.html，不需要新 IPC channel。
    options_payload = _build_mini_game_invite_options_payload(
        invite_lang=invite_lang,
        game_type=game_type,
        session_id=invite_session_id,
    )
    try:
        if mgr.websocket and hasattr(mgr.websocket, 'send_json'):
            client_state = getattr(mgr.websocket, 'client_state', None)
            if client_state is None or client_state == client_state.CONNECTED:
                await mgr.websocket.send_json(options_payload)
    except Exception as exc:
        logger.warning(
            "[%s] mini-game invite options WS push failed: %s",
            lanlan_name, exc,
        )

    print(
        f"[{lanlan_name}] Mini-game invite delivered "
        f"(game={game_type}, force_first={force_first}, "
        f"session_id={invite_session_id[:8]}…): {invite_text[:60]}…"
    )
    return _proactive_chat_body(
        PROACTIVE_REASON_CHAT_DELIVERED,
        message="mini-game invite delivered",
        channel="mini_game",
        game_type=game_type,
        force_first=force_first,
        lanlan_name=lanlan_name,
        turn_id=proactive_sid,
        invite_session_id=invite_session_id,
    )


# ---------- Break-reminder rendering + minimal-Phase-2 delivery ----------
# Two reminder paths emitted by ``main_logic/activity/tracker.py``:
#   * Anti-slack — fired when state transitions focused_work → leisure
#     after a real focus session. Higher priority (transition is more
#     time-sensitive than the cumulative water-break trigger).
#   * Water-break — fired when focused_work accumulator crosses
#     ``work_break_minutes``. 50% of the time, branches into a
#     "rest + game-invite" combo (LLM-generated) that shares the
#     mini-game cooldown so the two channels don't double-deliver.
#
# Both deliveries skip Phase 1 entirely (no source fetching, no
# enabled_modes parsing, no propensity gating). Phase 2 runs with a
# minimal SystemMessage (character_prompt + the env-notice template)
# so the model focuses on the single nudge instead of juggling sources.
# Mirrors ``_maybe_deliver_mini_game_invite`` in shape: try → fall
# through OR skip; never falls through to normal proactive flow when
# a pending exists (must-fire semantics).

def _resolve_break_reminder_label(
    canonical: str | None, lang: str, fallback_table: dict[str, str],
) -> str:
    """Pick a renderable app label, falling back to a localized generic."""
    label = (canonical or '').strip()
    if label:
        return label
    return fallback_table.get(lang, fallback_table.get('en', ''))


def _render_work_break_prompt(
    *,
    pending,                       # WorkBreakPending
    master_name: str,
    lang: str,
) -> tuple[str, str]:
    """Pick a seed + render the regular drink/stretch nudge prompt.

    Returns ``(system_prompt_text, seed)`` so the caller can log /
    record which seed was used. Seed is picked at delivery time (not
    pinned to the snapshot) so consecutive failed-then-retried
    deliveries naturally rotate the suggested action.
    """
    from config.prompts.prompts_activity import (
        WORK_BREAK_REMINDER_PROMPT, WORK_BREAK_SEED_HINTS,
        WORK_BREAK_GENERIC_WORK_LABEL,
    )
    import random as _random
    template = WORK_BREAK_REMINDER_PROMPT.get(
        lang, WORK_BREAK_REMINDER_PROMPT.get('en', WORK_BREAK_REMINDER_PROMPT['zh']),
    )
    seeds = WORK_BREAK_SEED_HINTS.get(
        lang, WORK_BREAK_SEED_HINTS.get('en', WORK_BREAK_SEED_HINTS['zh']),
    ) or ['']
    seed = _random.choice(seeds)
    app_label = _resolve_break_reminder_label(pending.app, lang, WORK_BREAK_GENERIC_WORK_LABEL)
    rendered = template.format(
        master=master_name or '',
        app=app_label,
        minutes=pending.minutes,
        seed=seed,
    )
    return rendered, seed


def _render_anti_slack_prompt(
    *,
    pending,                       # AntiSlackPending
    master_name: str,
    lang: str,
) -> str:
    """Render the focused→leisure 'back to work' nudge prompt.

    No seed slot — single behaviour, variation comes from prev/new app
    names + minute count + AI persona. Returns the system prompt text.
    """
    from config.prompts.prompts_activity import (
        ANTI_SLACK_REMINDER_PROMPT,
        WORK_BREAK_GENERIC_WORK_LABEL, WORK_BREAK_GENERIC_LEISURE_LABEL,
    )
    template = ANTI_SLACK_REMINDER_PROMPT.get(
        lang, ANTI_SLACK_REMINDER_PROMPT.get('en', ANTI_SLACK_REMINDER_PROMPT['zh']),
    )
    prev_app_label = _resolve_break_reminder_label(pending.prev_app, lang, WORK_BREAK_GENERIC_WORK_LABEL)
    new_app_label = _resolve_break_reminder_label(pending.new_app, lang, WORK_BREAK_GENERIC_LEISURE_LABEL)
    return template.format(
        master=master_name or '',
        prev_app=prev_app_label,
        new_app=new_app_label,
        minutes=pending.minutes,
    )


def _render_work_break_game_invite_prompt(
    *,
    pending,                       # WorkBreakPending
    game_type: str,
    master_name: str,
    lang: str,
) -> str | None:
    """Render the rest+game-invite combo prompt (50% branch).

    Returns the system prompt text, or None when no template exists for
    the given game_type (caller falls back to the regular water-break
    branch).
    """
    from config.prompts.prompts_activity import (
        WORK_BREAK_GAME_INVITE_PROMPTS_BY_GAME, WORK_BREAK_GENERIC_WORK_LABEL,
    )
    per_lang = WORK_BREAK_GAME_INVITE_PROMPTS_BY_GAME.get(game_type)
    if not per_lang:
        return None
    template = per_lang.get(lang, per_lang.get('en', per_lang.get('zh')))
    if not template:
        return None
    app_label = _resolve_break_reminder_label(pending.app, lang, WORK_BREAK_GENERIC_WORK_LABEL)
    return template.format(
        master=master_name or '',
        app=app_label,
        minutes=pending.minutes,
    )


async def _deliver_break_reminder_via_llm(
    *,
    lanlan_name: str,
    mgr,
    system_prompt: str,
    channel: str,                 # 'work_break' | 'anti_slack' | 'work_break_game_invite'
    lang: str,
    timeout_seconds: float = 25.0,
) -> tuple[str | None, str | None]:
    """Minimal Phase 2 LLM stream delivery for break reminders.

    No Phase 1, no sources, no full activity_state_section in the
    prompt — just ``character_prompt`` (already baked into
    ``system_prompt`` by the caller) + the env-notice block, so the
    model puts all attention on the single nudge.

    Returns ``(delivered_text, proactive_sid)`` on success.
    Returns ``(None, None)`` on:
      * ``prepare_proactive_delivery`` rejection (user just spoke /
        WS offline / etc — leave the source pending alone, next round
        can retry)
      * LLM error / timeout / preempt
      * Empty output / [PASS] emission (defensive)

    Caller is responsible for ``mark_*_used`` on success and for any
    follow-up UI push (e.g. the mini-game options popup in the
    work_break_game_invite branch).
    """
    # Model config — fetched here so the helper is self-contained
    # (caller in proactive_chat doesn't need to load it before our
    # must-fire branches, since those run before the existing config
    # fetch block at line ~4700). Returns None on any misconfig: a
    # working break reminder is strictly better than crashing the whole
    # proactive_chat round, and the source pending stays armed for the
    # next attempt once config is fixed.
    config_manager = get_config_manager()
    try:
        correction_config = config_manager.get_model_api_config('correction')
        correction_model = correction_config.get('model')
        correction_base_url = correction_config.get('base_url')
        correction_api_key = correction_config.get('api_key')
        correction_provider_type = correction_config.get('provider_type')
        if not correction_model or not correction_api_key:
            logger.warning(
                "[%s] break reminder skipped: correction model misconfigured",
                lanlan_name,
            )
            return None, None
    except Exception as cfg_err:
        logger.warning(
            "[%s] break reminder skipped: model config fetch failed: %s",
            lanlan_name, cfg_err,
        )
        return None, None

    # Idle gate (10s) — same threshold mini-game invite uses. If the
    # user just typed/spoke, don't interrupt.
    if not await mgr.prepare_proactive_delivery(min_idle_secs=10.0):
        return None, None

    proactive_sid = mgr.current_speech_id
    from main_logic.session_state import SessionEvent as _SE
    await mgr.state.fire(_SE.PROACTIVE_PHASE2)

    # Minimal HumanMessage — just ask the model to begin. The localized
    # ``BEGIN_GENERATE`` matches what normal Phase 2 uses, so the model
    # interprets the cue identically.
    begin_text = _loc(BEGIN_GENERATE, lang)
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=begin_text),
    ]

    print(
        f"\n{'='*60}\n[BREAK-REMINDER] channel={channel} lang={lang} model={correction_model}\n"
        f"{'='*60}\n{system_prompt}\n{'='*60}\n"
    )

    from utils.token_tracker import set_call_type
    set_call_type("proactive")
    full_text = ''
    aborted = False
    pass_probe = ''
    _PASS_PROBE_LEN = 5  # len("[PASS]") - 1

    try:
        async with asyncio.timeout(timeout_seconds):
            async with (await create_chat_llm_async(
                correction_model, correction_base_url, correction_api_key,
                provider_type=correction_provider_type,
                temperature=1.0,
                max_completion_tokens=PROACTIVE_PHASE2_GENERATE_MAX_TOKENS,
                streaming=True,
                timeout=timeout_seconds,  # mirror the asyncio.timeout() wrapping this stream
            )) as llm:
                async for chunk in llm.astream(messages):
                    if mgr.state.is_proactive_preempted(proactive_sid):
                        aborted = True
                        break
                    content = chunk.content if hasattr(chunk, 'content') else ''
                    if not content:
                        continue
                    combined = pass_probe + content
                    if '[PASS]' in combined.upper():
                        aborted = True
                        break
                    safe_text = combined[:-_PASS_PROBE_LEN] if len(combined) > _PASS_PROBE_LEN else ''
                    pass_probe = combined[-_PASS_PROBE_LEN:] if len(combined) >= _PASS_PROBE_LEN else combined
                    if safe_text:
                        # Token-budget cap mirrors the normal Phase 2
                        # path — break-reminder output should be short
                        # in any case, but defensive.
                        n_tokens = count_tokens(full_text + safe_text)
                        if n_tokens > PHASE2_OUTPUT_MAX_TOKENS:
                            aborted = True
                            break
                        full_text += safe_text
                        await mgr.feed_tts_chunk(safe_text, expected_speech_id=proactive_sid)
        # Flush remaining pass_probe (if it doesn't itself contain [PASS])
        if not aborted and pass_probe and '[PASS]' not in pass_probe.upper():
            full_text += pass_probe
            await mgr.feed_tts_chunk(pass_probe, expected_speech_id=proactive_sid)
    except (asyncio.TimeoutError, Exception) as e:
        logger.warning(
            "[%s] break reminder LLM stream failed (channel=%s): %s: %s",
            lanlan_name, channel, type(e).__name__, e,
        )
        aborted = True

    if aborted or not full_text.strip():
        if not mgr.state.is_proactive_preempted(proactive_sid):
            await mgr.handle_new_message()
        return None, None

    text = full_text.strip()
    committed = await mgr.finish_proactive_delivery(text, expected_speech_id=proactive_sid)
    if not committed:
        return None, None

    _record_proactive_chat(lanlan_name, text, channel=channel)
    print(
        f"[{lanlan_name}] break reminder delivered (channel={channel}): {text[:80]}…"
    )
    return text, proactive_sid


def _build_mini_game_invite_options_payload(
    *,
    invite_lang: str,
    game_type: str,
    session_id: str,
) -> dict[str, Any]:
    """Build the WS payload for the frontend ChoicePrompt.

    Labels go through i18n (the accept/decline/later options); choice is the
    wire-format identifier (used when the frontend button click posts back to
    the endpoint) and stays unchanged."""
    labels = MINI_GAME_INVITE_OPTION_LABELS.get(
        invite_lang,
        MINI_GAME_INVITE_OPTION_LABELS.get('zh', {}),
    )
    options = [
        {'choice': 'accept', 'label': labels.get('accept', 'Yes')},
        {'choice': 'decline', 'label': labels.get('decline', 'No')},
        {'choice': 'later', 'label': labels.get('later', 'Later')},
    ]
    return {
        'type': 'mini_game_invite_options',
        'session_id': session_id,
        'game_type': game_type,
        'options': options,
    }


def _clear_channel_from_proactive_history(lanlan_name: str, channel: str) -> int:
    """Blank out the channel mark of the given channel's entries in _proactive_chat_history.

    Purpose: when the user gives strong positive feedback (e.g. a recommended
    song played all the way through), that amounts to explicitly accepting this
    channel's recent output, so _compute_source_weights should no longer
    penalize the channel for "just used". Clearing the channel field stops
    raw_score from accumulating those entries, while the message text stays in
    the deque for dedup / similarity / format_recent_proactive_chats reuse.

    Returns the number of entries cleared.
    """
    history = _proactive_chat_history.get(lanlan_name)
    if not history:
        return 0
    rewritten: list[tuple] = []
    cleared = 0
    for entry in history:
        if len(entry) >= 3 and entry[2] == channel:
            rewritten.append((entry[0], entry[1], ''))
            cleared += 1
        else:
            rewritten.append(entry)
    if cleared == 0:
        return 0
    history.clear()
    history.extend(rewritten)
    return cleared


def _normalize_text_for_similarity(text: str) -> str:
    """
    Text normalization (conservative strategy):
    - lowercase
    - collapse consecutive whitespace
    Only light normalization, to avoid false kills from over-cleaning.
    """
    text = (text or "").strip().lower()
    return re.sub(r'\s+', ' ', text)


def _is_similar_to_recent_proactive_chat(lanlan_name: str, message: str) -> tuple[bool, float]:
    """
    Check whether message is highly similar to recent proactive chats (high threshold against false kills).
    Returns (is_duplicate, best_score).
    """
    history = _proactive_chat_history.get(lanlan_name)
    if not history or not message.strip():
        return False, 0.0

    now = time.time()
    current = _normalize_text_for_similarity(message)
    if not current:
        return False, 0.0

    best = 0.0
    for entry in history:
        ts, old_msg = entry[0], entry[1]
        if now - ts >= _RECENT_CHAT_MAX_AGE_SECONDS:
            continue
        old_norm = _normalize_text_for_similarity(old_msg)
        if not old_norm:
            continue
        score = difflib.SequenceMatcher(None, current, old_norm).ratio()
        if score > best:
            best = score
        if score >= _PROACTIVE_SIMILARITY_THRESHOLD:
            return True, score
    return False, best


def _compute_source_weights(
    lanlan_name: str,
    candidate_channels: list[str],
) -> dict[str, float]:
    """
    Compute normalized weights for each source.

    Algorithm:
    1. take records within 1h from _proactive_chat_history
    2. raw_score[ch] = Σ exp(-λ·age)  (each use accumulates with time decay)
    3. freshness[ch] = 1 / (1 + k·raw_score[ch])
    4. normalize: weight[ch] = freshness[ch] / Σ freshness

    With no history, returns a uniform distribution.

    Args:
        lanlan_name: character name
        candidate_channels: channels participating in the weighting (excluding vision)

    Returns:
        {channel: normalized_weight}, with weights summing to 1.0
    """
    import math
    n = len(candidate_channels)
    if n == 0:
        return {}

    # 收集 1h 内历史
    history = _proactive_chat_history.get(lanlan_name)
    now = time.time()

    raw_scores: dict[str, float] = {ch: 0.0 for ch in candidate_channels}

    if history:
        for ts, _msg, ch in history:
            age = now - ts
            if age > _SOURCE_WEIGHT_WINDOW:
                continue
            if ch in raw_scores:
                raw_scores[ch] += math.exp(-_SOURCE_WEIGHT_DECAY_LAMBDA * age)

    # Reminiscence usage lives in a separate buffer (kept out of
    # _proactive_chat_history to avoid polluting dedup / similarity
    # checks). Inject its decayed-frequency contribution here so the
    # weight calculation treats it on the same footing as web/news/etc.
    if 'reminiscence' in raw_scores:
        rem_buf = _reminiscence_usage_history.get(lanlan_name)
        if rem_buf:
            for ts in rem_buf:
                age = now - ts
                if age > _SOURCE_WEIGHT_WINDOW:
                    continue
                raw_scores['reminiscence'] += math.exp(-_SOURCE_WEIGHT_DECAY_LAMBDA * age)

    # freshness: 使用越多 → raw 越高 → freshness 越低
    freshness: dict[str, float] = {}
    for ch in candidate_channels:
        freshness[ch] = 1.0 / (1.0 + _SOURCE_WEIGHT_K * raw_scores[ch])

    total = sum(freshness.values())
    if total <= 0:
        # 不可能发生，但做防御
        return {ch: 1.0 / n for ch in candidate_channels}

    return {ch: freshness[ch] / total for ch in candidate_channels}


def _filter_sources_by_weight(weights: dict[str, float]) -> set[str]:
    """
    Return the set of channels that should be culled.

    Threshold = min(_SOURCE_WEIGHT_FLOOR, 1 / N)
    - with 4 channels, threshold=0.20; 2 uses trigger culling
    - with 6 channels, threshold=0.167; competition is fiercer

    Args:
        weights: normalized weights returned by _compute_source_weights

    Returns:
        set of channel names to cull
    """
    n = len(weights)
    if n <= 1:
        return set()  # 只剩 1 个来源时不剔除

    threshold = min(_SOURCE_WEIGHT_FLOOR, 1.0 / n)
    return {ch for ch, w in weights.items() if w < threshold}


# 复用 _RECENT_CHAT_MAX_AGE_SECONDS 作为权重窗口
_SOURCE_WEIGHT_WINDOW = _RECENT_CHAT_MAX_AGE_SECONDS


def _is_path_within_base(base_dir: str, candidate_path: str) -> bool:
    """
    
    Safety check that candidate_path is inside base_dir.
    Must use os.path.commonpath to prevent path traversal attacks.
    Before calling, both paths (candidate_path and base_dir) must be converted to
    absolute paths and resolved via os.path.realpath (resolving symlinks and ./..
    relative segments).
    args:
    - base_dir: base directory (absolute path)
    - candidate_path: candidate path (absolute path)
    returns:
    - bool: True if candidate_path is inside base_dir, False otherwise
    """
    try:
        # Normalize both paths for case-insensitivity on Windows
        norm_base = os.path.normcase(os.path.realpath(base_dir))
        norm_candidate = os.path.normcase(os.path.realpath(candidate_path))
        
        # os.path.commonpath raises ValueError if paths are on different drives (Windows)
        common = os.path.commonpath([norm_base, norm_candidate])
        return common == norm_base
    except (ValueError, TypeError):
        # Different drives or invalid paths
        return False

def _get_app_root():
    """
    Get the application root directory, compatible with both dev environments and PyInstaller-packaged builds.
    """
    if getattr(sys, 'frozen', False):
        if hasattr(sys, '_MEIPASS'):
            return sys._MEIPASS
        else:
            return os.path.dirname(sys.executable)
    else:
        return os.getcwd()


def _log_news_content(lanlan_name: str, news_content: dict):
    """
    Log news content fetch details.
    """
    region = news_content.get('region', 'china')
    news_data = news_content.get('news', {})
    if news_data.get('success'):
        trending_list = news_data.get('trending', [])
        words = [item.get('word', '') for item in trending_list[:5]]
        if words:
            source = "微博热议话题" if region == 'china' else "Twitter热门话题"
            print(f"[{lanlan_name}] 成功获取{source}:")
            for word in words:
                print(f"  - {word}")


def _log_video_content(lanlan_name: str, video_content: dict):
    """
    Log video content fetch details.
    """
    region = video_content.get('region', 'china')
    video_data = video_content.get('video', {})
    if video_data.get('success'):
        if region == 'china':
            videos = video_data.get('videos', [])
            titles = [video.get('title', '') for video in videos[:5]]
            if titles:
                print(f"[{lanlan_name}] 成功获取B站视频:")
                for title in titles:
                    print(f"  - {title}")
        else:
            posts = video_data.get('posts', [])
            titles = [post.get('title', '') for post in posts[:5]]
            if titles:
                print(f"[{lanlan_name}] 成功获取Reddit热门帖子:")
                for title in titles:
                    print(f"  - {title}")


def _log_trending_content(lanlan_name: str, trending_content: dict):
    """
    Log homepage recommendation content fetch details.
    """
    content_details = []
    
    bilibili_data = trending_content.get('bilibili', {})
    if bilibili_data.get('success'):
        videos = bilibili_data.get('videos', [])
        titles = [video.get('title', '') for video in videos[:5]]
        if titles:
            content_details.append("B站视频:")
            for title in titles:
                content_details.append(f"  - {title}")
    
    weibo_data = trending_content.get('weibo', {})
    if weibo_data.get('success'):
        trending_list = weibo_data.get('trending', [])
        words = [item.get('word', '') for item in trending_list[:5]]
        if words:
            content_details.append("微博话题:")
            for word in words:
                content_details.append(f"  - {word}")
    
    reddit_data = trending_content.get('reddit', {})
    if reddit_data.get('success'):
        posts = reddit_data.get('posts', [])
        titles = [post.get('title', '') for post in posts[:5]]
        if titles:
            content_details.append("Reddit热门帖子:")
            for title in titles:
                content_details.append(f"  - {title}")
    
    twitter_data = trending_content.get('twitter', {})
    if twitter_data.get('success'):
        trending_list = twitter_data.get('trending', [])
        words = [item.get('word', '') for item in trending_list[:5]]
        if words:
            content_details.append("Twitter热门话题:")
            for word in words:
                content_details.append(f"  - {word}")
    
    if content_details:
        print(f"[{lanlan_name}] 成功获取首页推荐:")
        for detail in content_details:
            print(detail)
    else:
        print(f"[{lanlan_name}] 成功获取首页推荐 - 但未获取到具体内容")

def _log_music_content(lanlan_name: str, music_content: dict):
    """Log music content fetch details."""
    if music_content.get('success'):
        tracks = music_content.get('data', [])
        titles = [f"{t.get('name', '')} - {t.get('artist', '')}" for t in tracks[:5]]
        if titles:
            logger.debug(f"[{lanlan_name}] 成功获取音乐推荐:")
            for title in titles:
                logger.debug(f"  - {title}")
    else:
        logger.warning(f"[{lanlan_name}] 音乐获取失败: {music_content.get('error', '未知错误')}")

def _format_music_content(music_content: dict, lang: str = 'zh') -> str:
    """Formats music content into a readable string with multi-language support."""
    if not music_content.get('success'):
        return ""
    
    t = MUSIC_SEARCH_RESULT_TEXTS.get(lang, MUSIC_SEARCH_RESULT_TEXTS['en'])
    
    output_lines = [t['title']]
    tracks = music_content.get('data', [])
    for i, track in enumerate(tracks[:5], 1):
        # 使用多语言字典中的"未知"占位符，替代硬编码的中文
        name = track.get('name') or t['unknown_track']
        artist = track.get('artist') or t['unknown_artist']
        album = track.get('album', '')
        
        if album:
            output_lines.append(f"{i}. 《{name}》 - {artist}（{t['album']}：{album}）")
        else:
            output_lines.append(f"{i}. 《{name}》 - {artist}")
    
    # 如果除了标题没有抓到任何歌曲，则返回空
    if len(output_lines) == 1:
        return ""
        
    # 删除了原来的 desc 尾注，保持素材的客观中立
    return "\n".join(output_lines)


def _append_music_recommendations(
    source_links: list[dict],
    music_content: dict | None,
    limit: int = 3,
) -> int:
    """Deduplicate and append music tracks from *music_content* into *source_links*.

    Returns the number of tracks actually appended (0 when nothing new).
    """
    music_raw = music_content.get('raw_data', {}) if music_content else {}
    tracks = music_raw.get('data')
    if not tracks:
        return 0

    existing_signatures = {
        (
            (link.get('url') or '').strip(),
            (link.get('title') or '').strip(),
            (link.get('artist') or '').strip(),
        )
        for link in source_links
        if isinstance(link, dict) and link.get('source') == '音乐推荐'
    }

    appended = 0
    for track in tracks[:limit]:
        title = (track.get('name') or '未知曲目').strip()
        artist = (track.get('artist') or '未知艺术家').strip()
        url = (track.get('url') or '').strip()
        sig = (url, title, artist)
        if sig in existing_signatures:
            continue
        source_links.append({
            'title': title,
            'artist': artist,
            'url': url,
            'cover': track.get('cover', ''),
            'source': '音乐推荐',
        })
        existing_signatures.add(sig)
        appended += 1
    return appended


def _log_personal_dynamics(lanlan_name: str, personal_content: dict):
    """
    Log personal feed content fetch details.
    """
    content_details = []
    
    bilibili_dynamic = personal_content.get('bilibili_dynamic', {})
    if bilibili_dynamic.get('success'):
        dynamics = bilibili_dynamic.get('dynamics', [])
        bilibili_contents = [dynamic.get('content', dynamic.get('title', '')) for dynamic in dynamics[:5]]
        if bilibili_contents:
            content_details.append("B站动态:")
            for content in bilibili_contents:
                content_details.append(f"  - {content}")
    
    weibo_dynamic = personal_content.get('weibo_dynamic', {})
    if weibo_dynamic.get('success'):
        dynamics = weibo_dynamic.get('statuses', [])
        weibo_contents = [dynamic.get('content', '') for dynamic in dynamics[:5]]
        if weibo_contents:
            content_details.append("微博动态:")
            for content in weibo_contents:
                content_details.append(f"  - {content}")
                
    if content_details:
        print(f"[{lanlan_name}] 成功获取个人动态:")
        for detail in content_details:
            print(detail)
    else:
        print(f"[{lanlan_name}] 成功获取个人动态 - 但未获取到具体内容")

@router.post('/emotion/analysis')
async def emotion_analysis(request: Request):
    """
    Emotion analysis endpoint.
    func:
    - receives text input, calls the configured emotion analysis model, and returns the emotion class and confidence
    - supports overriding the default API key and model name from request parameters for flexibility
    - parses the model response intelligently, tolerating different formats (plain text, markdown code blocks, JSON strings, etc.) for robustness
    - adjusts the emotion class by confidence, setting it to neutral when confidence is low, improving result reliability
    - pushes the result to the monitor system (when lanlan_name is provided) for realtime interaction and display with the frontend
    """
    validation_error = _validate_local_mutation_request(request)
    if validation_error is not None:
        return validation_error

    try:
        _config_manager = get_config_manager()
        data = await request.json()
        if not data or 'text' not in data:
            return {"error": "请求体中必须包含text字段"}
        
        text = data['text']
        lanlan_name = data.get('lanlan_name')
        if text is None or str(text).strip() == "":
            emotion = "neutral"
            confidence = 0.5
            _push_emotion_update(lanlan_name, emotion, confidence)
            return _emotion_response(emotion, confidence)

        api_key = data.get('api_key')
        model = data.get('model')
        
        # 使用参数或默认配置，使用 .get() 安全获取避免 KeyError
        emotion_config = _config_manager.get_model_api_config('emotion')
        emotion_api_key = emotion_config.get('api_key')
        emotion_model = emotion_config.get('model')
        emotion_base_url = emotion_config.get('base_url')
        emotion_provider_type = emotion_config.get('provider_type')
        
        # 优先使用请求参数，其次使用配置
        api_key = api_key or emotion_api_key
        model = model or emotion_model
        
        if not api_key:
            return {"error": "情绪分析模型配置缺失: API密钥未提供且配置中未设置默认密钥"}
        
        if not model:
            return {"error": "情绪分析模型配置缺失: 模型名称未提供且配置中未设置默认模型"}
       
        prompt_lang = _resolve_emotion_prompt_language(text)

        # 构建请求消息
        messages = [
            {
                "role": "system", 
                "content": get_outward_emotion_analysis_prompt(prompt_lang)
            },
            {
                "role": "user", 
                "content": text
            }
        ]

        from utils.token_tracker import set_call_type
        set_call_type("emotion")

        # 异步调用模型（使用统一工厂，自动处理 extra_body / provider 兼容）
        llm = await create_chat_llm_async(
            model,
            emotion_base_url,
            api_key,
            provider_type=emotion_provider_type,
            temperature=0.3,
            # Gemini 模型可能返回 markdown 格式，需要更多 token
            max_completion_tokens=EMOTION_ANALYSIS_MAX_TOKENS,
            timeout=30,
        )
        async with llm:
            result = await llm.ainvoke(messages)

        # 解析响应
        result_text = result.content.strip()

        # 处理 markdown 代码块格式（Gemini 可能返回 ```json {...} ``` 格式）
        # 首先尝试使用正则表达式提取第一个代码块
        code_block_match = re.search(r"```(?:json)?\s*(.+?)\s*```", result_text, flags=re.S)
        if code_block_match:
            result_text = code_block_match.group(1).strip()
        elif result_text.startswith("```"):
            # 回退到原有的行分割逻辑
            lines = result_text.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]  # 移除第一行
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]  # 移除最后一行
            result_text = "\n".join(lines).strip()
        
        # 尝试解析JSON响应
        emotion = "neutral"
        confidence = 0.5

        def _apply_degraded_emotion_fallback():
            heuristic_emotion, heuristic_score = _infer_emotion_from_text(text)
            if heuristic_emotion:
                return heuristic_emotion, min(0.62, 0.34 + heuristic_score * 0.1)
            # 当模型结果不可用或缺少足够关键词线索时，回退到 neutral。
            return "neutral", 0.5

        try:
            from utils.file_utils import robust_json_loads
            result = robust_json_loads(result_text)
            if not isinstance(result, dict):
                # 有效 JSON 也可能是 null/[]/"text"，此时复用降级启发式处理。
                emotion, confidence = _apply_degraded_emotion_fallback()
            else:
                # 获取emotion和confidence
                raw_emotion = result.get("emotion", "neutral")
                raw_confidence = result.get("confidence", 0.5)
                emotion = _normalize_emotion_label(raw_emotion, raw_confidence)
                confidence = _coerce_emotion_confidence(raw_confidence)
                decision_source = "model"

                heuristic_emotion, heuristic_score = _infer_emotion_from_text(text)
                if heuristic_emotion:
                    # 强 override：启发式分数较高（≥4）且模型置信度不算很高（<0.8）时
                    # 才推翻模型判断；避免单个吐槽词把模型 happy/neutral 翻成 angry。
                    if heuristic_emotion != emotion and heuristic_score >= 4 and confidence < 0.8:
                        emotion = heuristic_emotion
                        confidence = max(confidence, min(0.86, 0.44 + heuristic_score * 0.07))
                        decision_source = "heuristic_strong_override"
                    elif heuristic_emotion == "sad" and emotion == "happy" and heuristic_score >= 2:
                        emotion = heuristic_emotion
                        confidence = max(confidence, min(0.84, 0.5 + heuristic_score * 0.08))
                        decision_source = "heuristic_sad_override"
                    elif emotion == "neutral" and confidence < 0.6:
                        emotion = heuristic_emotion
                        confidence = max(confidence, min(0.78, 0.42 + heuristic_score * 0.12))
                        decision_source = "heuristic_from_neutral"
                    elif confidence < 0.25:
                        emotion = heuristic_emotion
                        confidence = max(confidence, min(0.65, 0.35 + heuristic_score * 0.1))
                        decision_source = "heuristic_from_low_confidence"

                # 当confidence很低时，自动将emotion设置为neutral，避免误报
                if confidence < 0.2:
                    emotion = "neutral"
                    decision_source = "neutral_fallback"
        except ValueError:
            emotion, confidence = _apply_degraded_emotion_fallback()

        _push_emotion_update(lanlan_name, emotion, confidence)
        return _emotion_response(emotion, confidence)
            
    except Exception as e:
        logger.error(f"情感分析失败: {e}")
        return {
            "error": f"情感分析失败: {str(e)}",
            "emotion": "neutral",
            "confidence": 0.0
        }


# Progress Stat for timed achievements. Steamworks Partner must bind
# ACH_TIME_* achievements to this stat with matching thresholds; Steam unlocks
# them automatically when StoreStats syncs a value past the bound threshold.
_PLAYTIME_PROGRESS_STAT = "PLAY_TIME_SECONDS"
_PLAYTIME_PROGRESS_ACHIEVEMENTS: tuple[str, ...] = (
    "ACH_TIME_5MIN",
    "ACH_TIME_1HR",
    "ACH_TIME_100HR",
)


async def _prepare_steam_user_stats(steamworks: Any) -> None:
    steamworks.UserStats.RequestCurrentStats()
    for _ in range(10):
        steamworks.run_callbacks()
        await asyncio.sleep(0.1)


async def _unlock_steam_achievement(steamworks: Any, name: str) -> dict[str, Any]:
    """Unlock one Steam achievement. Returns a status dict (no HTTP response)."""
    await _prepare_steam_user_stats(steamworks)
    achievement_status = steamworks.UserStats.GetAchievement(name)
    logger.info("Achievement status: %s=%s", name, achievement_status)
    if achievement_status:
        return {
            "success": True,
            "achievement": name,
            "newlyUnlocked": False,
            "alreadyUnlocked": True,
            "message": f"成就 {name} 已经解锁",
        }

    result = steamworks.UserStats.SetAchievement(name)
    if not result:
        logger.warning("设置成就首次尝试失败，正在重试: %s", name)
        await asyncio.sleep(0.5)
        steamworks.run_callbacks()
        result = steamworks.UserStats.SetAchievement(name)

    if not result:
        logger.error("设置成就失败: %s，请确认成就ID在Steam后台已配置", name)
        return {
            "success": False,
            "achievement": name,
            "newlyUnlocked": False,
            "alreadyUnlocked": False,
            "error": f"设置成就失败: {name}，请确认成就ID在Steam后台已配置",
        }

    steamworks.UserStats.StoreStats()
    steamworks.run_callbacks()
    logger.info("成功设置成就: %s", name)
    return {
        "success": True,
        "achievement": name,
        "newlyUnlocked": True,
        "alreadyUnlocked": False,
        "message": f"成就 {name} 已解锁",
    }


def _read_progress_unlocked_achievements(steamworks: Any) -> list[str]:
    """Read which progress-stat achievements Steam has already unlocked."""
    unlocked: list[str] = []
    for achievement_name in _PLAYTIME_PROGRESS_ACHIEVEMENTS:
        try:
            if steamworks.UserStats.GetAchievement(achievement_name):
                unlocked.append(achievement_name)
        except Exception as exc:
            logger.debug("读取进度成就状态失败 %s: %s", achievement_name, exc)
    return unlocked


@router.post('/steam/set-achievement-status/{name}')
async def set_achievement_status(name: str, request: Request):
    """
    Set Steam achievement status endpoint.
    func:
    - receives the achievement name as a path parameter and sets the achievement via the Steamworks API
    - first requests current stats and runs callbacks to ensure the data is loaded
    - checks the achievement's current state; if already unlocked, returns success directly
    - if not unlocked, tries to set it; returns success if it works, otherwise waits and retries once
    """
    validation_error = _validate_local_mutation_request(request)
    if validation_error is not None:
        return validation_error

    steamworks = get_steamworks()
    if steamworks is None:
        return JSONResponse(content={"success": False, "error": "Steamworks未初始化"}, status_code=503)

    try:
        result = await _unlock_steam_achievement(steamworks, name)
        if not result.get("success"):
            return JSONResponse(content=result, status_code=500)
        return JSONResponse(content=result)
    except Exception as e:
        logger.error("设置成就失败: %s", e)
        return JSONResponse(content={"success": False, "error": str(e)}, status_code=500)


@router.post('/steam/update-playtime')
async def update_playtime(request: Request):
    """
    Accumulate PLAY_TIME_SECONDS progress stat and StoreStats.

    Timed achievements (ACH_TIME_*) must be bound to this Progress Stat in
    Steamworks Partner. Steam unlocks them automatically when the synced value
    crosses the configured threshold — this endpoint never calls SetAchievement.
    """
    validation_error = _validate_local_mutation_request(request)
    if validation_error is not None:
        return validation_error

    steamworks = get_steamworks()
    if steamworks is None:
        return JSONResponse(content={"success": False, "error": "Steamworks未初始化"}, status_code=503)

    try:
        data = await request.json()
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}

    seconds_to_add = data.get("seconds", 10)
    try:
        seconds_to_add = int(seconds_to_add)
        if seconds_to_add < 0:
            return JSONResponse(
                content={"success": False, "error": "seconds must be non-negative"},
                status_code=400,
            )
    except (ValueError, TypeError, OverflowError):
        # OverflowError: json.loads accepts bare Infinity/-Infinity, which
        # int() cannot convert — treat it as invalid input, not a 500.
        return JSONResponse(
            content={"success": False, "error": "seconds must be a valid integer"},
            status_code=400,
        )

    # Cap a single report to 1 hour to limit abuse / clock-jump spikes.
    seconds_to_add = min(seconds_to_add, 3600)

    try:
        # Ensure Steam has delivered current stats before read/modify/write;
        # otherwise GetStatInt may return 0 and StoreStats would clobber progress.
        await _prepare_steam_user_stats(steamworks)

        try:
            current_playtime = steamworks.UserStats.GetStatInt(_PLAYTIME_PROGRESS_STAT)
        except Exception as e:
            logger.warning("获取 %s 失败，从 0 开始: %s", _PLAYTIME_PROGRESS_STAT, e)
            current_playtime = 0

        new_playtime = int(current_playtime) + seconds_to_add

        try:
            result = steamworks.UserStats.SetStat(_PLAYTIME_PROGRESS_STAT, new_playtime)
        except Exception as stat_error:
            logger.warning(
                "设置 Steam 进度统计失败: %s - 统计可能未在 Steamworks 后台配置",
                stat_error,
            )
            return JSONResponse(content={
                "success": True,
                "totalPlayTime": new_playtime,
                "added": seconds_to_add,
                "stat": _PLAYTIME_PROGRESS_STAT,
                "warning": "Steam progress stat not configured",
                "progressUnlocked": [],
            })

        if not result:
            logger.debug(
                "SetStat 返回 False - %s 统计可能未在 Steamworks 后台配置",
                _PLAYTIME_PROGRESS_STAT,
            )
            return JSONResponse(content={
                "success": True,
                "totalPlayTime": new_playtime,
                "added": seconds_to_add,
                "stat": _PLAYTIME_PROGRESS_STAT,
                "warning": "Steam progress stat not configured",
                "progressUnlocked": [],
            })

        steamworks.UserStats.StoreStats()
        # Give Steam a short window to apply Progress Stat → achievement unlocks.
        for _ in range(5):
            steamworks.run_callbacks()
            await asyncio.sleep(0.05)

        progress_unlocked = _read_progress_unlocked_achievements(steamworks)
        logger.debug(
            "游戏时长进度已更新: %ss -> %ss (+%ss); progressUnlocked=%s",
            current_playtime,
            new_playtime,
            seconds_to_add,
            progress_unlocked,
        )
        return JSONResponse(content={
            "success": True,
            "totalPlayTime": new_playtime,
            "added": seconds_to_add,
            "stat": _PLAYTIME_PROGRESS_STAT,
            "progressUnlocked": progress_unlocked,
        })
    except Exception as e:
        logger.error("更新游戏时长失败: %s", e)
        return JSONResponse(content={"success": False, "error": str(e)}, status_code=500)


@router.get('/steam/list-achievements')
async def list_achievements():
    """
    List all achievements configured in the Steam backend (for debugging).
    """
    steamworks = get_steamworks()
    if steamworks is not None:
        try:
            steamworks.UserStats.RequestCurrentStats()
            for _ in range(10):
                steamworks.run_callbacks()
                await asyncio.sleep(0.1)
            
            num_achievements = steamworks.UserStats.GetNumAchievements()
            achievements = []
            for i in range(num_achievements):
                name = steamworks.UserStats.GetAchievementName(i)
                if name:
                    # 如果是bytes类型，解码为字符串
                    if isinstance(name, bytes):
                        name = name.decode('utf-8')
                    status = steamworks.UserStats.GetAchievement(name)
                    achievements.append({"name": name, "unlocked": status})
            
            logger.info(f"Steam后台已配置 {num_achievements} 个成就: {achievements}")
            return JSONResponse(content={"count": num_achievements, "achievements": achievements})
        except Exception as e:
            logger.error(f"获取成就列表失败: {e}")
            return JSONResponse(content={"error": str(e)}, status_code=500)
    else:
        return JSONResponse(content={"error": "Steamworks未初始化"}, status_code=500)


@router.get('/file-exists')
async def check_file_exists(path: str = None):
    """
    Check whether a file exists.

    Security: Validates against path traversal attacks by:
    - URL-decoding the path
    - Normalizing the path (resolves . and ..)
    - Rejecting any path containing .. components (prevents escaping to parent dirs)
    - Using os.path.realpath to get the canonical path
    
    Note: This endpoint allows access to user Documents and Steam Workshop
    locations, so no whitelist restriction is applied.
    """
    try:
        if not path:
            return JSONResponse(content={"exists": False}, status_code=400)
        
        # 解码URL编码的路径
        decoded_path = unquote(path)
        
        # Windows路径处理 - normalize slashes
        if os.name == 'nt':
            decoded_path = decoded_path.replace('/', '\\')
        
        # Security: Reject path traversal attempts
        # Normalize first to catch encoded variants like %2e%2e
        normalized = os.path.normpath(decoded_path)
        
        # After normpath, check if path tries to escape via ..
        # Split and check each component to be thorough
        parts = normalized.split(os.sep)
        if '..' in parts:
            logger.warning(f"Rejected path traversal attempt in file-exists: {decoded_path}")
            return JSONResponse(content={"exists": False}, status_code=400)
        
        # Resolve to canonical absolute path
        real_path = os.path.realpath(normalized)
        
        # Check if the file exists
        exists = os.path.exists(real_path) and os.path.isfile(real_path)
        
        return JSONResponse(content={"exists": exists})
        
    except Exception as e:
        logger.error(f"检查文件存在失败: {e}")
        return JSONResponse(content={"exists": False}, status_code=500)


@router.get('/find-first-image')
async def find_first_image(folder: str = None):
    """
    Find a preview image in the given folder — hardened version with strict security checks.
    
    Security notes:
    1. only specific safe directories inside the project may be accessed
    2. prevents path traversal attacks
    3. limits returned info to avoid leaking filesystem details
    4. logs suspicious access attempts
    5. only returns images smaller than 1MB (the Steam Workshop preview size limit)
    """
    MAX_IMAGE_SIZE = 1 * 1024 * 1024  # 1MB
    
    try:
        # 检查参数有效性
        if not folder:
            logger.warning("收到空的文件夹路径请求")
            return JSONResponse(content={"success": False, "error": "无效的文件夹路径"}, status_code=400)
        
        # 安全警告日志记录
        logger.warning(f"预览图片查找请求: {folder}")
        
        # 获取基础目录和允许访问的目录列表
        base_dir = _get_app_root()
        allowed_dirs = [
            os.path.realpath(os.path.join(base_dir, 'static')),
            os.path.realpath(os.path.join(base_dir, 'assets'))
        ]
        
        # 添加"我的文档/Xiao8"目录到允许列表
        if os.name == 'nt':  # Windows系统
            documents_path = os.path.join(os.path.expanduser('~'), 'Documents', 'Xiao8')
            if os.path.exists(documents_path):
                real_doc_path = os.path.realpath(documents_path)
                allowed_dirs.append(real_doc_path)
                logger.info(f"find-first-image: 添加允许的文档目录: {real_doc_path}")
        
        # 解码URL编码的路径
        decoded_folder = unquote(folder)
        
        # Windows路径处理
        if os.name == 'nt':
            decoded_folder = decoded_folder.replace('/', '\\')
        
        # 额外的安全检查：拒绝包含路径遍历字符的请求
        if '..' in decoded_folder or '//' in decoded_folder:
            logger.warning(f"检测到潜在的路径遍历攻击: {decoded_folder}")
            return JSONResponse(content={"success": False, "error": "无效的文件夹路径"}, status_code=403)
        
        # 规范化路径以防止路径遍历攻击
        try:
            real_folder = os.path.realpath(decoded_folder)
        except Exception as e:
            logger.error(f"路径规范化失败: {e}")
            return JSONResponse(content={"success": False, "error": "无效的文件夹路径"}, status_code=400)
        
        # 检查路径是否在允许的目录内 - 使用 commonpath 防止前缀攻击
        is_allowed = any(_is_path_within_base(allowed_dir, real_folder) for allowed_dir in allowed_dirs)
        
        if not is_allowed:
            logger.warning(f"访问被拒绝：路径不在允许的目录内 - {real_folder}")
            return JSONResponse(content={"success": False, "error": "无效的文件夹路径"}, status_code=403)
        
        # 检查文件夹是否存在
        if not os.path.exists(real_folder) or not os.path.isdir(real_folder):
            return JSONResponse(content={"success": False, "error": "无效的文件夹路径"}, status_code=400)
        
        # 只查找指定的8个预览图片名称，按优先级顺序
        preview_image_names = [
            'preview.jpg', 'preview.png',
            'thumbnail.jpg', 'thumbnail.png',
            'icon.jpg', 'icon.png',
            'header.jpg', 'header.png'
        ]
        
        for image_name in preview_image_names:
            image_path = os.path.join(real_folder, image_name)
            try:
                # 检查文件是否存在
                if os.path.exists(image_path) and os.path.isfile(image_path):
                    # 检查文件大小是否小于 1MB
                    file_size = os.path.getsize(image_path)
                    if file_size >= MAX_IMAGE_SIZE:
                        logger.info(f"跳过大于1MB的图片: {image_name} ({file_size / 1024 / 1024:.2f}MB)")
                        continue
                    
                    # 再次验证图片文件路径是否在允许的目录内 - 使用 commonpath 防止前缀攻击
                    real_image_path = os.path.realpath(image_path)
                    if any(_is_path_within_base(allowed_dir, real_image_path) for allowed_dir in allowed_dirs):
                        # 只返回相对路径或文件名，不返回完整的文件系统路径，避免信息泄露
                        # 计算相对于base_dir的相对路径
                        try:
                            relative_path = os.path.relpath(real_image_path, base_dir)
                            return JSONResponse(content={"success": True, "imagePath": relative_path})
                        except ValueError:
                            # 如果无法计算相对路径（例如跨驱动器），只返回文件名
                            return JSONResponse(content={"success": True, "imagePath": image_name})
            except Exception as e:
                logger.error(f"检查图片文件 {image_name} 失败: {e}")
                continue
        
        return JSONResponse(content={"success": False, "error": "未找到小于1MB的预览图片文件"})
        
    except Exception as e:
        logger.error(f"查找预览图片文件失败: {e}")
        return JSONResponse(content={"success": False, "error": "服务器内部错误"}, status_code=500)

# 统一的表情包代理缓存，使用 byte-based 限制 (50MB)，防止 OOM
MEME_PROXY_CACHE = TTLCache(
    maxsize=50 * 1024 * 1024,  # 50MB 内存预算
    ttl=1800,
    getsizeof=lambda item: len(item.get('body', b''))
)

@router.get('/meme/proxy-image')
async def proxy_meme_image(url: str):
    """
    Proxy a remote meme image, solving CORS issues, with SSRF protection.
    """
    import time
    
    # 检查缓存
    cache_key = url
    if cache_key in MEME_PROXY_CACHE:
        logger.info(f"[Meme Proxy] 命中缓存: {url[:60]}...")
        cached = MEME_PROXY_CACHE[cache_key]
        return Response(
            content=cached['body'],
            media_type=cached['content_type'],
            headers={
                'Cache-Control': 'public, max-age=86400',
                'X-Cache': 'HIT',
                'X-Content-Type-Options': 'nosniff'
            }
        )
    
    try:
        logger.info(f"[Meme Proxy] 收到代理请求, url: {url[:100] if url else 'None'}...")
        
        if not url:
            return JSONResponse(content={"success": False, "error": "缺少URL参数"}, status_code=400)
        
        decoded_url = unquote(url)
        if not decoded_url.startswith(('http://', 'https://')):
            return JSONResponse(content={"success": False, "error": "无效的URL"}, status_code=400)
        
        allowed_hosts = MEME_ALLOWED_HOSTS
        
        from urllib.parse import urlparse, urljoin
        parsed = urlparse(decoded_url)
        hostname = (parsed.hostname or '').lower()
        
        if not any(hostname == host or hostname.endswith('.' + host) for host in allowed_hosts):
            logger.warning(f"[Meme Proxy] 非法域名请求: {hostname}")
            return JSONResponse(content={"success": False, "error": f"不允许代理该域名: {hostname}"}, status_code=403)

        # 构建请求头
        # 【修复】完善所有域名的 Referer 映射，避免被反爬拦截
        referer_map = {
            'img.soutula.com': 'https://fabiaoqing.com/',
            'fabiaoqing.com': 'https://fabiaoqing.com/',
            # 2026-04-16: doutub.com 域名易主挂黑产，停用
            # 'qn.doutub.com': 'https://www.doutub.com/',
            # 'doutub.com': 'https://www.doutub.com/',
            'i.imgflip.com': 'https://imgflip.com/',
            'imgflip.com': 'https://imgflip.com/',
            'soutula.com': 'https://fabiaoqing.com/',
            'img.doutupk.com': 'https://www.doutupk.com/',
            'doutupk.com': 'https://www.doutupk.com/',
        }
        referer = referer_map.get(hostname, f'{parsed.scheme}://{hostname}/')
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
            'Referer': referer,
            'Accept': 'image/avif,image/webp,image/apng,image/*,*/*;q=0.8'
        }

        # 使用流式下载以严格控制资源大小，防止内存溢出或大文件攻击
        MAX_IMAGE_SIZE = 10 * 1024 * 1024  # 10MB 限制

        # 已知 SSL 证书有问题的 CDN 域名（如七牛 CDN hostname mismatch），
        # 对这些域名首次请求即使用宽松 SSL，避免白白浪费一次超时。
        # 2026-04-16: qn.doutub.com 随 doutub.com 域名易主停用；白名单当前为空，
        # 其它域名仍走 ssl.SSLError 降级分支兜底。
        _SSL_RELAXED_HOSTS: set[str] = set()
        need_relaxed_ssl = hostname in _SSL_RELAXED_HOSTS

        def _make_client(relaxed: bool = False) -> httpx.AsyncClient:
            if relaxed:
                ctx = ssl.create_default_context()
                try:
                    ctx.set_ciphers('DEFAULT@SECLEVEL=1')
                except Exception as e:
                    logger.debug("[Meme Proxy] set_ciphers SECLEVEL=1 不可用，使用默认密码套件: %s", e)
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                return httpx.AsyncClient(timeout=15.0, follow_redirects=False, verify=ctx)
            return httpx.AsyncClient(timeout=15.0, follow_redirects=False)

        async with _make_client(relaxed=need_relaxed_ssl) as client:
            current_url = decoded_url
            for _ in range(4):  # 最多跟随 3 次重定向 (4次请求)
                async with client.stream("GET", current_url, headers=headers) as resp:
                    if resp.status_code in (301, 302, 303, 307, 308):
                        location = resp.headers.get("Location")
                        if not location:
                            break
                        
                        new_url = urljoin(current_url, location)
                        new_parsed = urlparse(new_url)
                        new_hostname = (new_parsed.hostname or '').lower()
                        
                        if not any(new_hostname == host or new_hostname.endswith('.' + host) for host in allowed_hosts):
                            logger.warning(f"[Meme Proxy] 重定向到非法域名: {new_hostname}")
                            return JSONResponse(content={"success": False, "error": "非法重定向"}, status_code=403)
                        
                        current_url = new_url
                        continue
                    
                    resp.raise_for_status()
                    
                    # 校验 Content-Type (严格白名单，防 SVG XSS 注入)
                    raw_content_type = resp.headers.get('Content-Type', '').lower()
                    content_type = raw_content_type.split(';', 1)[0].strip()
                    allowed_content_types = {
                        'image/jpeg', 'image/png', 'image/gif', 
                        'image/webp', 'image/avif', 'image/bmp'
                    }
                    if content_type not in allowed_content_types:
                        logger.warning(f"[Meme Proxy] 拒绝非安全图片内容: {raw_content_type}")
                        return JSONResponse(content={"success": False, "error": "格式不支持或含有潜在风险"}, status_code=403)
                    
                    # 校验 Content-Length (如果存在)
                    content_length = resp.headers.get('Content-Length')
                    if content_length:
                        try:
                            declared_size = int(content_length)
                        except (ValueError, TypeError):
                            declared_size = None  # 解析失败就当未知长度，靠流式校验兜底
                        if declared_size is not None and declared_size > MAX_IMAGE_SIZE:
                            logger.warning(f"[Meme Proxy] 资源过大 (Content-Length): {content_length}")
                            return JSONResponse(content={"success": False, "error": "资源超过大小限制 (10MB)"}, status_code=413)

                    # 流式读取内容并累加大小校验
                    body = bytearray()
                    async for chunk in resp.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > MAX_IMAGE_SIZE:
                            logger.warning(f"[Meme Proxy] 资源过大 (实际读取): {len(body)}")
                            return JSONResponse(content={"success": False, "error": "资源超过大小限制 (10MB)"}, status_code=413)

                    # 存入 TTLCache
                    MEME_PROXY_CACHE[cache_key] = {
                        'body': bytes(body),
                        'content_type': content_type
                    }
                    
                    return Response(
                        content=bytes(body),
                        media_type=content_type,
                        headers={
                            'Cache-Control': 'public, max-age=86400',
                            'X-Cache': 'MISS',
                            'X-Content-Type-Options': 'nosniff'
                        }
                    )
            
            return JSONResponse(content={"success": False, "error": "过多的重定向"}, status_code=400)

    except httpx.TimeoutException:
        return JSONResponse(content={"success": False, "error": "请求超时"}, status_code=504)
    except (ssl.SSLError, httpx.ConnectError) as e:
        # SSL 握手失败：对白名单内的表情包域名降级重试（宽松 SSL）
        is_ssl = isinstance(e, ssl.SSLError) or 'SSL' in str(e) or 'certificate' in str(e).lower()
        if is_ssl and not need_relaxed_ssl:
            logger.warning(f"[Meme Proxy] SSL 失败，降级重试: {hostname} ({e})")
            try:
                async with _make_client(relaxed=True) as fallback_client:
                    async with fallback_client.stream("GET", decoded_url, headers=headers) as resp:
                        resp.raise_for_status()
                        raw_ct = resp.headers.get('Content-Type', '').lower()
                        ct = raw_ct.split(';', 1)[0].strip()
                        allowed_ct = {'image/jpeg', 'image/png', 'image/gif', 'image/webp', 'image/avif', 'image/bmp'}
                        if ct not in allowed_ct:
                            return JSONResponse(content={"success": False, "error": "格式不支持"}, status_code=403)
                        body = bytearray()
                        async for chunk in resp.aiter_bytes():
                            body.extend(chunk)
                            if len(body) > MAX_IMAGE_SIZE:
                                return JSONResponse(content={"success": False, "error": "资源超过大小限制"}, status_code=413)
                        MEME_PROXY_CACHE[cache_key] = {'body': bytes(body), 'content_type': ct}
                        return Response(
                            content=bytes(body), media_type=ct,
                            headers={'Cache-Control': 'public, max-age=86400', 'X-Cache': 'MISS-SSL-FALLBACK', 'X-Content-Type-Options': 'nosniff'}
                        )
            except Exception as fallback_e:
                logger.error(f"[Meme Proxy] SSL 降级重试也失败: {fallback_e}")
                return JSONResponse(content={"success": False, "error": str(fallback_e)}, status_code=500)
        logger.error(f"[Meme Proxy] 代理失败: {str(e)}")
        return JSONResponse(content={"success": False, "error": str(e)}, status_code=500)
    except Exception as e:
        logger.error(f"[Meme Proxy] 代理失败: {str(e)}")
        return JSONResponse(content={"success": False, "error": str(e)}, status_code=500)


# 辅助函数

def _read_binary_file(path: str) -> bytes:
    """Synchronous binary read, called via asyncio.to_thread."""
    with open(path, 'rb') as f:
        return f.read()


@router.get('/steam/proxy-image')
async def proxy_image(image_path: str):
    """
    Proxy access to local image files, supporting absolute and relative paths, notably the Steam Workshop directory.
    """

    try:
        logger.info(f"代理图片请求，原始路径: {image_path}")
        
        # 解码URL编码的路径（处理双重编码情况）
        decoded_path = unquote(image_path)
        # 再次解码以处理可能的双重编码
        decoded_path = unquote(decoded_path)
        
        logger.info(f"解码后的路径: {decoded_path}")
        
        # 检查是否是远程URL，如果是则直接返回错误（目前只支持本地文件）
        if decoded_path.startswith(('http://', 'https://')):
            return JSONResponse(content={"success": False, "error": "暂不支持远程图片URL"}, status_code=400)
        
        # 获取基础目录和允许访问的目录列表
        base_dir = _get_app_root()
        allowed_dirs = [
            os.path.realpath(os.path.join(base_dir, 'static')),
            os.path.realpath(os.path.join(base_dir, 'assets'))
        ]
        
        
        # 添加get_workshop_path()返回的路径作为允许目录，支持相对路径解析
        try:
            workshop_base_dir = os.path.abspath(os.path.normpath(get_workshop_path()))
            if os.path.exists(workshop_base_dir):
                real_workshop_dir = os.path.realpath(workshop_base_dir)
                if real_workshop_dir not in allowed_dirs:
                    allowed_dirs.append(real_workshop_dir)
                    logger.info(f"添加允许的默认创意工坊目录: {real_workshop_dir}")
        except Exception as e:
            logger.warning(f"无法添加默认创意工坊目录: {str(e)}")
        
        # 动态添加路径到允许列表：如果请求的路径包含创意工坊相关标识，则允许访问
        try:
            # 检查解码后的路径是否包含创意工坊相关路径标识
            if ('steamapps\\workshop' in decoded_path.lower() or 
                'steamapps/workshop' in decoded_path.lower()):
                
                # 获取创意工坊父目录
                workshop_related_dir = None
                
                # 方法1：如果路径存在，获取文件所在目录或直接使用目录路径
                if os.path.exists(decoded_path):
                    if os.path.isfile(decoded_path):
                        workshop_related_dir = os.path.dirname(decoded_path)
                    else:
                        workshop_related_dir = decoded_path
                
                # 方法2：尝试从路径中提取创意工坊相关部分
                if not workshop_related_dir:
                    match = re.search(r'(.*?steamapps[/\\]workshop)', decoded_path, re.IGNORECASE)
                    if match:
                        workshop_related_dir = match.group(1)
                
                # 方法3：如果是Steam创意工坊内容路径，获取content目录
                if not workshop_related_dir:
                    content_match = re.search(r'(.*?steamapps[/\\]workshop[/\\]content)', decoded_path, re.IGNORECASE)
                    if content_match:
                        workshop_related_dir = content_match.group(1)
                
                # 方法4：如果是Steam创意工坊内容路径，添加整个steamapps/workshop目录
                if not workshop_related_dir:
                    steamapps_match = re.search(r'(.*?steamapps)', decoded_path, re.IGNORECASE)
                    if steamapps_match:
                        workshop_related_dir = os.path.join(steamapps_match.group(1), 'workshop')
                
                # 如果找到了相关目录，添加到允许列表
                if workshop_related_dir:
                    # 确保目录存在
                    if os.path.exists(workshop_related_dir):
                        real_workshop_dir = os.path.realpath(workshop_related_dir)
                        if real_workshop_dir not in allowed_dirs:
                            allowed_dirs.append(real_workshop_dir)
                            logger.info(f"动态添加允许的创意工坊相关目录: {real_workshop_dir}")
                    else:
                        # 如果目录不存在，尝试直接添加steamapps/workshop路径
                        workshop_match = re.search(r'(.*?steamapps[/\\]workshop)', decoded_path, re.IGNORECASE)
                        if workshop_match:
                            potential_dir = workshop_match.group(0)
                            if os.path.exists(potential_dir):
                                real_workshop_dir = os.path.realpath(potential_dir)
                                if real_workshop_dir not in allowed_dirs:
                                    allowed_dirs.append(real_workshop_dir)
                                    logger.info(f"动态添加允许的创意工坊目录: {real_workshop_dir}")
        except Exception as e:
            logger.warning(f"动态添加创意工坊路径失败: {str(e)}")
        
        logger.info(f"当前允许的目录列表: {allowed_dirs}")

        # Windows路径处理：确保路径分隔符正确
        if os.name == 'nt':  # Windows系统
            # 替换可能的斜杠为反斜杠，确保Windows路径格式正确
            decoded_path = decoded_path.replace('/', '\\')
            # 处理可能的双重编码问题
            if decoded_path.startswith('\\\\'):
                decoded_path = decoded_path[2:]  # 移除多余的反斜杠前缀
        
        # 尝试解析路径
        final_path = None
        
        # 特殊处理：如果路径包含steamapps/workshop，直接检查文件是否存在
        if ('steamapps\\workshop' in decoded_path.lower() or 'steamapps/workshop' in decoded_path.lower()):
            if os.path.exists(decoded_path) and os.path.isfile(decoded_path):
                final_path = decoded_path
                logger.info(f"直接允许访问创意工坊文件: {final_path}")
        
        # 尝试作为绝对路径
        if final_path is None:
            if os.path.exists(decoded_path) and os.path.isfile(decoded_path):
                # 规范化路径以防止路径遍历攻击
                real_path = os.path.realpath(decoded_path)
                # 检查路径是否在允许的目录内 - 使用 commonpath 防止前缀攻击
                if any(_is_path_within_base(allowed_dir, real_path) for allowed_dir in allowed_dirs):
                    final_path = real_path
        
        # 尝试备选路径格式
        if final_path is None:
            alt_path = decoded_path.replace('\\', '/')
            if os.path.exists(alt_path) and os.path.isfile(alt_path):
                real_path = os.path.realpath(alt_path)
                # 使用 commonpath 防止前缀攻击
                if any(_is_path_within_base(allowed_dir, real_path) for allowed_dir in allowed_dirs):
                    final_path = real_path
        
        # 尝试相对路径处理 - 相对于static目录
        if final_path is None:
            # 对于以../static开头的相对路径，尝试直接从static目录解析
            if decoded_path.startswith('..\\static') or decoded_path.startswith('../static'):
                # 提取static后面的部分
                relative_part = decoded_path.split('static')[1]
                if relative_part.startswith(('\\', '/')):
                    relative_part = relative_part[1:]
                # 构建完整路径
                relative_path = os.path.join(allowed_dirs[0], relative_part)  # static目录
                if os.path.exists(relative_path) and os.path.isfile(relative_path):
                    real_path = os.path.realpath(relative_path)
                    # 使用 commonpath 防止前缀攻击
                    if any(_is_path_within_base(allowed_dir, real_path) for allowed_dir in allowed_dirs):
                        final_path = real_path
        
        # 尝试相对于默认创意工坊目录的路径处理
        if final_path is None:
            try:
                workshop_base_dir = os.path.abspath(os.path.normpath(get_workshop_path()))
                
                # 尝试将解码路径作为相对于创意工坊目录的路径
                rel_workshop_path = os.path.join(workshop_base_dir, decoded_path)
                rel_workshop_path = os.path.normpath(rel_workshop_path)
                
                logger.info(f"尝试相对于创意工坊目录的路径: {rel_workshop_path}")
                
                if os.path.exists(rel_workshop_path) and os.path.isfile(rel_workshop_path):
                    real_path = os.path.realpath(rel_workshop_path)
                    # 确保路径在允许的目录内 - 使用 commonpath 防止前缀攻击
                    if _is_path_within_base(workshop_base_dir, real_path):
                        final_path = real_path
                        logger.info(f"找到相对于创意工坊目录的图片: {final_path}")
            except Exception as e:
                logger.warning(f"处理相对于创意工坊目录的路径失败: {str(e)}")
        
        # 如果仍未找到有效路径，返回错误
        if final_path is None:
            return JSONResponse(content={"success": False, "error": f"文件不存在或无访问权限: {decoded_path}"}, status_code=404)
        
        # 检查文件扩展名是否为图片
        image_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp']
        if os.path.splitext(final_path)[1].lower() not in image_extensions:
            return JSONResponse(content={"success": False, "error": "不是有效的图片文件"}, status_code=400)
        
        # 检查文件大小是否超过50MB限制
        MAX_IMAGE_SIZE = 50 * 1024 * 1024  # 50MB
        file_size = await asyncio.to_thread(os.path.getsize, final_path)
        if file_size > MAX_IMAGE_SIZE:
            logger.warning(f"图片文件大小超过限制: {final_path} ({file_size / 1024 / 1024:.2f}MB > 50MB)")
            return JSONResponse(content={"success": False, "error": f"图片文件大小超过50MB限制 ({file_size / 1024 / 1024:.2f}MB)"}, status_code=413)

        # 读取图片文件 —— 最多 50MB，事件循环上同步 read 会卡几十毫秒
        image_data = await asyncio.to_thread(_read_binary_file, final_path)
        
        # 根据文件扩展名设置MIME类型
        ext = os.path.splitext(final_path)[1].lower()
        mime_type = {
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.png': 'image/png',
            '.gif': 'image/gif',
            '.bmp': 'image/bmp',
            '.webp': 'image/webp'
        }.get(ext, 'application/octet-stream')
        
        # 返回图片数据
        return Response(content=image_data, media_type=mime_type)
    except Exception as e:
        logger.error(f"代理图片访问失败: {str(e)}")
        return JSONResponse(content={"success": False, "error": f"访问图片失败: {str(e)}"}, status_code=500)

@router.get('/get_window_title')
async def get_window_title_api():
    """
    Get the title of the currently active window (Windows only).
    """
    try:
        from utils.web_scraper import get_active_window_title
        title = get_active_window_title()
        if title:
            return JSONResponse({"success": True, "window_title": title})
        return JSONResponse({"success": False, "window_title": None})
    except Exception as e:
        logger.error(f"获取窗口标题失败: {e}")
        return JSONResponse({"success": False, "window_title": None})


@router.post('/screenshot')
async def backend_screenshot(request: Request):
    """
    Backend screenshot fallback: when all frontend screen-capture APIs fail, the backend captures the local screen with pyautogui.
    Security restriction: only requests from loopback addresses are allowed. Returns a JPEG base64 DataURL.
    """
    validation_error = _validate_local_mutation_request(
        request,
        error_defaults={"success": False},
    )
    if validation_error is not None:
        _set_no_store_headers(validation_error)
        return validation_error

    if not _is_loopback_request(request):
        return _json_no_store_response({"success": False, "error": "only available from localhost"}, status_code=403)

    if _is_remote_backend_deployment():
        return _json_no_store_response(
            {"success": False, "error": "backend is configured as remote (NEKO_ACTIVITY_TRACKER_REMOTE); local screenshot disabled"},
            status_code=501,
        )

    try:
        import pyautogui
    except ImportError:
        return _json_no_store_response({"success": False, "error": "pyautogui not installed"}, status_code=501)

    try:
        def _capture_rgb_screenshot():
            shot = pyautogui.screenshot()
            if shot.mode in ('RGBA', 'LA', 'P'):
                shot = shot.convert('RGB')
            return shot

        shot = await asyncio.to_thread(_capture_rgb_screenshot)

        # macOS 黑屏检测：仅在 macOS 上执行——未授权 Screen Recording 时 pyautogui 返回全黑图片
        # 其他平台（Windows/Linux）全黑截图属正常内容，不应拦截
        if sys.platform == "darwin":
            # 低分辨率采样：把图缩到 16×16 后用 PIL extrema 检测，避免全量 numpy 数组的内存开销
            try:
                thumb = shot.resize((16, 16))
                extrema = thumb.getextrema()  # ((min_r, max_r), (min_g, max_g), (min_b, max_b))
                if all(mx <= 1 for _, mx in extrema):
                    logger.warning("后端截图检测到全黑图片，可能缺少 Screen Recording 权限")
                    return _json_no_store_response(
                        {"success": False, "error": "screenshot is blank (Screen Recording permission may be denied)"},
                        status_code=403,
                    )
            except Exception:
                logger.debug("macOS blank-screen detection failed, skipping check", exc_info=True)

        jpg_bytes = await asyncio.to_thread(
            compress_screenshot, shot, target_h=COMPRESS_TARGET_HEIGHT, quality=COMPRESS_JPEG_QUALITY,
        )
        b64 = base64.b64encode(jpg_bytes).decode('utf-8')
        data_url = f"data:image/jpeg;base64,{b64}"
        return _json_no_store_response({"success": True, "data": data_url, "size": len(jpg_bytes)})
    except Exception as e:
        error_message = _format_backend_screenshot_error(e)
        logger.error(f"后端截图失败: {error_message}")
        return _json_no_store_response({"success": False, "error": error_message}, status_code=500)


@router.post('/screenshot/interactive')
async def backend_interactive_screenshot(request: Request):
    """
    System-native interactive screenshot: preferred by the chat screenshot button.
    Current implementation:
      - macOS: `screencapture` system-level region selection
      - Windows: local full-desktop overlay region selection
    Returns a JPEG DataURL of the user's selection.
    Security restrictions:
      - only requests from loopback addresses are allowed;
      - any request carrying `Origin` or `Referer` (i.e. coming from a browser)
        must still pass the local-mutation CSRF/origin checks, preventing
        arbitrary pages from blind-POSTing localhost to pop up the native
        selection UI (a localhost CSRF);
      - pure server-side loopback calls without `Origin`/`Referer` may skip
        CSRF, reserved for curl / local scripts / tests.
    """
    if not _is_loopback_request(request):
        return _json_no_store_response({"success": False, "error": "only available from localhost"}, status_code=403)

    # 用原始 header 是否存在来判断"这是不是浏览器请求"，而不是 _get_request_origin 的归一化结果。
    # 后者会把 `Origin: null`（sandboxed iframe / file:// / data:）和无效 `Referer` 归一成空串，
    # 让恶意页面可以通过 sandboxed iframe 故意送 `Origin: null` 来绕过 CSRF 校验。
    if request.headers.get("origin") is not None or request.headers.get("referer") is not None:
        validation_error = _validate_local_mutation_request(
            request,
            error_defaults={"success": False},
        )
        if validation_error is not None:
            _set_no_store_headers(validation_error)
            return validation_error

    if _is_remote_backend_deployment():
        return _json_no_store_response(
            {"success": False, "error": "backend is configured as remote (NEKO_ACTIVITY_TRACKER_REMOTE); local interactive screenshot disabled"},
            status_code=501,
        )

    if sys.platform == "darwin":
        runner = _run_macos_interactive_screenshot
    else:
        # Windows / Linux 没有可靠的"系统级框选 + 回传"原语，统一交给前端 Electron
        # 的 desktopCapturer 区域选择路径处理；这里直接 501 让 caller 走兜底链。
        return _json_no_store_response(
            {"success": False, "error": "interactive screenshot is only supported on macOS"},
            status_code=501,
        )

    fd, tmp_path = tempfile.mkstemp(prefix="neko-interactive-shot-", suffix=".png")
    os.close(fd)
    try:
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass

        returncode, stderr = await asyncio.to_thread(runner, tmp_path)
        file_exists = os.path.exists(tmp_path)
        file_size = os.path.getsize(tmp_path) if file_exists else 0

        if _is_interactive_screenshot_canceled(sys.platform, returncode, stderr, file_size):
            logger.info("系统原生交互截图已取消(returncode=%s, stderr=%s)", returncode, stderr)
            return _json_no_store_response({"success": False, "canceled": True}, status_code=200)

        if file_size <= 0:
            error_message = str(stderr or "").strip() or f"interactive screenshot failed with returncode {returncode}"
            logger.warning(
                "系统原生交互截图失败且未生成文件(returncode=%s, stderr=%s)",
                returncode,
                stderr,
            )
            return _json_no_store_response(
                {"success": False, "canceled": False, "error": error_message},
                status_code=500,
            )

        data_url, jpg_size = await asyncio.to_thread(_image_path_to_jpeg_data_url, tmp_path)
        return _json_no_store_response({
            "success": True,
            "data": data_url,
            "size": jpg_size,
            "interactive": True,
        })
    except FileNotFoundError as e:
        logger.warning("系统原生交互截图不可用: %s", e)
        return _json_no_store_response({"success": False, "error": str(e)}, status_code=501)
    except SystemExit as e:
        # Nuitka 等场景下，缺失某些可选依赖会用 SystemExit 当 sentinel 抛出（继承 BaseException
        # 而非 Exception）。如果不在这里截住，会逃出 asyncio worker thread → 拖死整个后端
        # 进程，连带 Electron shell 一起崩。这里转成普通 500，让前端能继续走兜底链。
        logger.error("系统原生交互截图 runner 抛 SystemExit: %s", e)
        return _json_no_store_response(
            {"success": False, "error": f"interactive screenshot runner aborted: {e}"},
            status_code=500,
        )
    except Exception as e:
        logger.error(f"系统原生交互截图失败: {e}")
        return _json_no_store_response({"success": False, "error": str(e)}, status_code=500)
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            logger.debug("清理交互截图临时文件失败: %s", tmp_path, exc_info=True)


# ── Frontend-pushed activity signals (cross-platform OS signal channel) ──
#
# Per-lanlan throttle for ``/api/activity_signal``. Keyed by lanlan_name,
# value is the timestamp of the last accepted push. Bounded — see
# ``_ACTIVITY_SIGNAL_THROTTLE_MAX_ENTRIES`` — to defend against an
# attacker spraying lanlan_names; in practice the dict has 1-3 entries.
# Concurrent access from FastAPI's worker pool is safe because Python
# dict ops are atomic under the GIL and we tolerate occasional rate-limit
# slippage (worst case: an extra push slips through).
_ACTIVITY_SIGNAL_THROTTLE: dict[str, float] = {}
_ACTIVITY_SIGNAL_THROTTLE_MAX_ENTRIES = 64


def _activity_signal_validate_float(
    data: dict, key: str, lo: float | None, hi: float | None,
) -> tuple[float | None, str | None]:
    """Coerce ``data[key]`` to a bounded float; ``None`` means absent.

    Tracker treats absent fields as neutral defaults (see
    ``UserActivityTracker.push_external_system_signal`` docstring), so
    we keep ``None`` distinct from a present-but-invalid value — the
    latter is a 400 with a specific error.

    Non-finite values (``NaN`` / ``±Infinity``) are rejected explicitly
    before range comparison — ``float('nan') < lo`` is silently
    ``False``, so they'd otherwise bypass the bounds check. Worse,
    serialising them downstream (state-machine logs, JSON responses)
    crashes since standard JSON forbids them. ``math.isfinite`` is the
    correct guard: it rejects NaN and both infinities while accepting
    every normal/subnormal float.
    """
    raw = data.get(key)
    if raw is None:
        return None, None
    # Reject booleans before float coercion (Codex F8 on PR #1477).
    # ``bool`` is a subclass of ``int`` in Python, so ``float(True)``
    # silently returns ``1.0`` and ``float(False)`` returns ``0.0``,
    # which would slip past the range checks below as legitimate signal
    # values. ``isinstance(raw, bool)`` catches both before the int
    # / float fast paths in ``float()``.
    if isinstance(raw, bool):
        return None, f"{key} must be a number"
    try:
        val = float(raw)
    except (TypeError, ValueError, OverflowError):
        # OverflowError is raised by ``float()`` when the integer is
        # too large to fit in a C double (Codex F9 on PR #1477) —
        # JSON allows arbitrary-precision ints which Python loads as
        # native big ints, and ``float(10**400)`` blows up. Without
        # this case the request becomes a 500 instead of a clean 400,
        # giving a low-cost crash vector to anyone POSTing oversized
        # numeric literals.
        return None, f"{key} must be a number"
    if not math.isfinite(val):
        return None, f"{key} must be finite"
    if lo is not None and val < lo:
        return None, f"{key} must be >= {lo}"
    if hi is not None and val > hi:
        return None, f"{key} must be <= {hi}"
    return val, None


def _activity_signal_validate_str(
    data: dict, key: str,
) -> tuple[str | None, str | None]:
    raw = data.get(key)
    if raw is None:
        return None, None
    if not isinstance(raw, str):
        return None, f"{key} must be a string"
    return raw, None


@router.post('/activity_signal')
async def push_activity_signal(request: Request):
    """Accept OS-activity signals pushed by the frontend on a heartbeat.

    Companion to ``UserActivityTracker.push_external_system_signal()``
    (PR #1015 ``main_logic/activity/tracker.py:347``), exposing the
    push channel as HTTP for the "backend doesn't run on the user's
    machine" deployments. The frontend (Electron preload reading
    ``powerMonitor.getSystemIdleTime`` + npm ``active-win`` +
    ``os.cpus()`` + ``nvidia-smi``) POSTs here every ``~5s``; the
    tracker treats anything fresher than ``_EXTERNAL_SIGNAL_TTL_SECONDS``
    (15s) as the authoritative OS view, falling back to the local
    collector when the heartbeat stops. Same fresh-then-fallback path
    feeds both the async ``get_snapshot`` and the sync variant — see
    ``tracker._select_system_snapshot``.

    Auth:

    * Unified ``_validate_local_mutation_request`` guard (issue #1479
      Step 2): same Origin + ``X-CSRF-Token`` contract every other
      browser-facing mutation endpoint uses (tutorial-prompt,
      screenshot, autostart-prompt, …). Replaces PR #1477's interim
      Origin-only gate. Same-origin Electron renderers and browser
      tabs send ``X-CSRF-Token`` via
      ``window.nekoLocalMutationSecurity`` (token is exposed by
      ``GET /api/config/page_config``); curl / Electron main-process /
      native scripts that don't run the token bootstrap are now
      rejected because *CSRF ≠ authentication* — pushing activity
      from outside the same browsing context isn't a supported path
      (see ``docs/design/security/local-mutation-auth.md`` for the
      threat model). The guard already rejects ``Origin: null`` /
      opaque origins because ``_normalize_origin_value`` returns
      ``""`` for them, which then fails the membership check.
    * Per-lanlan 5s rate limit below + the tracker's per-character
      lookup raise spam cost and bound the impact even if the guard
      somehow passes.

    Body fields (all optional except ``lanlan_name``):
      * ``lanlan_name`` (required) — which character's tracker to update
      * ``window_title`` — string, raw active-window title
      * ``process_name`` — string, owning process exe name (e.g. ``"Code.exe"``)
      * ``idle_seconds`` — float ≥ 0, OS-wide keyboard/mouse idle
      * ``cpu_avg_30s`` — float in ``[0, 100]``, rolling CPU average
      * ``gpu_utilization`` — float in ``[0, 100]``, primary GPU utilisation

    Returns 200 on success, 400 on malformed payload, 403 on
    Origin/CSRF rejection, 404 if ``lanlan_name`` isn't registered,
    429 if pushed faster than ``_EXTERNAL_SIGNAL_MIN_INTERVAL`` (5s),
    503 if the character's tracker hasn't initialised yet.
    """
    # ``error_defaults`` so the 403 body includes ``success: false``
    # alongside the unified guard's ``ok/error_code`` fields — keeps
    # the contract consistent with this endpoint's other error
    # branches (existing frontend / tests grep ``success``). Also
    # apply ``_set_no_store_headers`` since the rest of this handler's
    # responses use that and a cached 403 would mask post-bootstrap
    # success on the next tick (CodeRabbit Minor on PR #1532).
    validation_error = _validate_local_mutation_request(
        request,
        error_defaults={"success": False},
    )
    if validation_error is not None:
        _set_no_store_headers(validation_error)
        return validation_error

    try:
        data = await request.json()
    except Exception:
        return _json_no_store_response(
            {"success": False, "error": "invalid JSON body"},
            status_code=400,
        )
    if not isinstance(data, dict):
        return _json_no_store_response(
            {"success": False, "error": "body must be a JSON object"},
            status_code=400,
        )

    lanlan_name = data.get("lanlan_name")
    if not isinstance(lanlan_name, str) or not lanlan_name.strip():
        return _json_no_store_response(
            {"success": False, "error": "lanlan_name required"},
            status_code=400,
        )
    lanlan_name = lanlan_name.strip()

    idle_seconds, err = _activity_signal_validate_float(
        data, "idle_seconds", 0.0, None,
    )
    if err:
        return _json_no_store_response(
            {"success": False, "error": err}, status_code=400,
        )
    cpu_avg_30s, err = _activity_signal_validate_float(
        data, "cpu_avg_30s", 0.0, 100.0,
    )
    if err:
        return _json_no_store_response(
            {"success": False, "error": err}, status_code=400,
        )
    gpu_utilization, err = _activity_signal_validate_float(
        data, "gpu_utilization", 0.0, 100.0,
    )
    if err:
        return _json_no_store_response(
            {"success": False, "error": err}, status_code=400,
        )

    window_title, err = _activity_signal_validate_str(data, "window_title")
    if err:
        return _json_no_store_response(
            {"success": False, "error": err}, status_code=400,
        )
    process_name, err = _activity_signal_validate_str(data, "process_name")
    if err:
        return _json_no_store_response(
            {"success": False, "error": err}, status_code=400,
        )

    # ── Empty-signal guard (Codex F6 + CodeRabbit F7 on PR #1477) ──
    # If every signal field is absent, the tracker's
    # ``push_external_system_signal`` would still mark
    # ``os_signals_available=True`` and default missing numerics to
    # ``0.0`` — i.e., a payload of ``{"lanlan_name": "X"}`` would
    # silently overwrite real state with synthetic "idle=0 / cpu=0 /
    # no window". The frontend client already skips empty bridge
    # snapshots, but a malicious or buggy native caller could still
    # POST an empty payload, so we reject server-side too.
    #
    # Blank-string handling (CodeRabbit F7): ``"window_title": ""`` or
    # whitespace-only strings carry no information and have the same
    # poisoning effect as ``None``. Treat them as absent for the
    # all-empty check; non-blank strings (legit "no foreground window
    # right now" semantics with explicit ``""``) would still trip the
    # check if every other field is also None — which is the right
    # outcome, that payload tells the tracker literally nothing.
    if all(
        v is None or (isinstance(v, str) and not v.strip())
        for v in (
            idle_seconds, cpu_avg_30s, gpu_utilization,
            window_title, process_name,
        )
    ):
        return _json_no_store_response(
            {
                "success": False,
                "error": "at least one signal field required",
            },
            status_code=400,
        )

    # Per-lanlan throttle — matches the frontend's 5s heartbeat. TTL
    # is 15s (3× this interval) so even if 2 of every 3 pushes get
    # rate-limited the tracker stays inside its freshness window. Spam
    # control, not auth — the character lookup below is the real
    # integrity check.
    now = time.time()
    last_push = _ACTIVITY_SIGNAL_THROTTLE.get(lanlan_name)
    if last_push is not None and (now - last_push) < _EXTERNAL_SIGNAL_MIN_INTERVAL:
        retry_after = max(
            0.0, _EXTERNAL_SIGNAL_MIN_INTERVAL - (now - last_push),
        )
        resp = _json_no_store_response(
            {
                "success": False,
                "error": "rate limited",
                "retry_after_seconds": round(retry_after, 3),
            },
            status_code=429,
        )
        # Header is integer seconds per RFC 9110; round up so clients
        # don't retry into the same window.
        resp.headers["Retry-After"] = str(int(retry_after) + 1)
        return resp

    session_manager = get_session_manager()
    mgr = session_manager.get(lanlan_name)
    if not mgr:
        return _json_no_store_response(
            {
                "success": False,
                "error": f"lanlan_name {lanlan_name!r} not registered",
            },
            status_code=404,
        )
    tracker = getattr(mgr, "_activity_tracker", None)
    if tracker is None:
        return _json_no_store_response(
            {
                "success": False,
                "error": "activity tracker not initialised for this character",
            },
            status_code=503,
        )

    try:
        tracker.push_external_system_signal(
            window_title=window_title,
            process_name=process_name,
            idle_seconds=idle_seconds,
            cpu_avg_30s=cpu_avg_30s,
            gpu_utilization=gpu_utilization,
            now=now,
        )
    except Exception as e:
        logger.exception(
            "push_external_system_signal failed for %s", lanlan_name,
        )
        return _json_no_store_response(
            {"success": False, "error": f"tracker rejected push: {e}"},
            status_code=500,
        )

    _ACTIVITY_SIGNAL_THROTTLE[lanlan_name] = now
    # Bound the dict: in practice lanlan_names are 1-3, but if an
    # attacker sprays unique names we trim oldest. Sorted ascending by
    # timestamp; keep the freshest MAX entries.
    if len(_ACTIVITY_SIGNAL_THROTTLE) > _ACTIVITY_SIGNAL_THROTTLE_MAX_ENTRIES:
        excess = sorted(
            _ACTIVITY_SIGNAL_THROTTLE.items(), key=lambda kv: kv[1],
        )[:-_ACTIVITY_SIGNAL_THROTTLE_MAX_ENTRIES]
        for key, _ in excess:
            _ACTIVITY_SIGNAL_THROTTLE.pop(key, None)

    return _json_no_store_response({"success": True})


# ================================================================
# 主动搭话响应构建 (Response builder pure function)
# ================================================================
def build_proactive_response(source_tag: str, ctx: dict) -> tuple[str, list]:
    primary_channel = 'unknown'
    source_links = []
    lan_name = ctx.get('lanlan_name', 'System')
    
    match source_tag:
        case 'CHAT':
            primary_channel = 'chat'
        case 'WEB':
            # 使用细粒度 web 子通道（news/video/home/personal），fallback 到 'web'
            web_link = ctx.get('selected_web_link')
            primary_channel = web_link.get('mode', 'web') if web_link else 'web'
            if web_link:
                source_links.append(web_link)
                logger.debug(f"[{lan_name}] Phase 2 确定选择 WEB (子通道: {primary_channel})，已添加链接")
        case 'MUSIC':
            primary_channel = 'music'
            if ctx.get('selected_music_link'):
                source_links.append(ctx['selected_music_link'])
                logger.debug(f"[{lan_name}] Phase 2 确定选择 MUSIC，已添加链接")
        case 'MEME':
            primary_channel = 'meme'
            if ctx.get('selected_meme_link'):
                source_links.append(ctx['selected_meme_link'])
                logger.debug(f"[{lan_name}] Phase 2 确定选择 MEME，已添加相关链接")
            else:
                logger.warning(f"[{lan_name}] Phase 2 AI 选择 MEME 但无可用表情包链接，回退处理")
                if ctx.get('selected_web_link'):
                    primary_channel = ctx['selected_web_link'].get('mode', 'web')
                    source_links.append(ctx['selected_web_link'])
                    logger.debug(f"[{lan_name}] Phase 2 回退到 WEB 通道 (子通道: {primary_channel})")
                elif ctx.get('vision_content'):
                    primary_channel = 'vision'
                    logger.debug(f"[{lan_name}] Phase 2 回退到 VISION 通道")
                else:
                    logger.debug(f"[{lan_name}] Phase 2 MEME 无表情包且无回退通道，将跳过链接展示")
    return primary_channel, source_links

@router.post('/proactive_chat')
async def proactive_chat(request: Request):
    """
    Proactive chat: two-phase architecture — Phase 1 merged LLM (web screening + music/meme keywords, 1 call), Phase 2 persona-aware chat generation.
    """
    validation_error = _validate_local_mutation_request(request)
    if validation_error is not None:
        return validation_error

    try:
        _config_manager = get_config_manager()
        session_manager = get_session_manager()
        # 获取当前角色数据（包括完整人设）
        master_name_current, her_name_current, _, _, _, lanlan_prompt_map, _, _, _ = await _config_manager.aget_character_data()
        
        data = await request.json()
        lanlan_name = data.get('lanlan_name') or her_name_current
        is_playing_music = data.get('is_playing_music', False)
        current_track = data.get('current_track', None)
        music_cooldown = data.get('music_cooldown', False)
        
        # 获取session manager
        mgr = session_manager.get(lanlan_name)
        if not mgr:
            return JSONResponse(
                _proactive_error_body(
                    PROACTIVE_REASON_ERROR_CHARACTER_NOT_FOUND,
                    error=f"角色 {lanlan_name} 不存在",
                ),
                status_code=404,
            )

        if getattr(mgr, "is_goodbye_silent", lambda: False)():
            logger.info("[%s] 主动搭话本轮未发起：goodbye silent", lanlan_name)
            return JSONResponse(_proactive_pass_body(
                PROACTIVE_REASON_PASS_DISABLED,
                message="goodbye silent; proactive skipped",
            ))

        try:
            from main_routers.game_router import is_game_route_active
            if is_game_route_active(lanlan_name):
                logger.info("[%s] 主动搭话本轮未发起：游戏路由 active", lanlan_name)
                return JSONResponse(_proactive_pass_body(
                    PROACTIVE_REASON_PASS_ROUTE_ACTIVE,
                    message="game route active; ordinary proactive skipped",
                ))
        except Exception as game_route_err:
            logger.warning("[%s] proactive game-route guard failed closed: %s", lanlan_name, game_route_err)
            return JSONResponse(_proactive_pass_body(
                PROACTIVE_REASON_PASS_ROUTE_ACTIVE,
                message="game route guard unavailable; ordinary proactive skipped",
            ))
        
        # 检查能否发起新一轮主动搭话：状态机统一把 "AI 正在响应"（_is_responding）、
        # "另一轮 proactive 在跑"（phase != IDLE）两个信号收拢到 O(1) 判定。
        # mgr.is_active 仅用于判断 session 是否已实例化，故仍需保留。
        probe_session = mgr.session if mgr.is_active else None

        # ========== Voice mode fast path ==========
        # 语音模式下不走 Phase1/Phase2，不占 SM 的 proactive phase；先用只读
        # can_start_proactive 做 409 判定即可。
        if data.get('voice_mode') and mgr.is_active and isinstance(mgr.session, OmniRealtimeClient):
            # Mini-game invite 状态机推进：voice fast path 不走 activity tracker，
            # 直接用 session 自己跟踪的「用户最后一次真实消息时间」喂给
            # advance_response。否则纯 voice 用户收到 mini-game 邀请回应后，
            # pending 永远翻不掉，邀请会被永久抑制；CodeRabbit Major review 指出。
            #
            # ⚠️ 用 last_user_message_time（仅真实非空非 echo 用户输入）而非
            # last_user_activity_time（顶部无条件刷新，含 VAD 空噪声 + 麦克风录回
            # AI 自己 TTS 的回声）。后者会被 AI 念邀请台词的回声污染：邀请投递后
            # 回声立刻把 activity 刷到 > delivered_at，下一个 tick 的隐式 dismiss
            # 误判「用户已回应」→ 把 pending 邀请清成 'later'（5min）+ 撤掉按钮，
            # 用户随后点「现在不想玩」落到 expired、真正的 5h decline 起不来、邀请
            # 5min 后反复重来。改用真消息时间戳后，纯点按钮（不说话）的用户活动
            # 时间不会越过 delivered_at，pending 一直留到用户显式点按钮 / 说话。
            _voice_advance_outcome = _mini_game_invite_advance_response(
                lanlan_name, getattr(mgr, 'last_user_message_time', None),
            )
            # advance 触发了隐式 dismiss → 推 WS 让前端清掉 prompt UI（cross-window
            # 一致性）。codex P2 指出非按钮路径漏推 WS 让 UI 挂着。
            if _voice_advance_outcome and _voice_advance_outcome.get('session_id'):
                await _push_mini_game_invite_resolved(
                    mgr,
                    session_id=_voice_advance_outcome['session_id'],
                    action=_voice_advance_outcome.get('action', 'suppress'),
                )
            if not mgr.state.can_start_proactive(session=probe_session):
                logger.info("[%s] 主动搭话本轮未发起：语音模式 AI 正在响应中（409）", lanlan_name)
                return JSONResponse(
                    _proactive_error_body(
                        PROACTIVE_REASON_PASS_BUSY,
                        error="AI正在响应中，无法主动搭话",
                        message="请等待当前响应完成",
                        state=mgr.state.snapshot(),
                    ),
                    status_code=409,
                )
            delivered = await mgr.trigger_voice_proactive_nudge()
            if delivered:
                # 1h+10 chats 冷却的 chat counter：voice nudge 也算一次主动搭话，
                # 与 text path 在 _record_proactive_chat 之后调 count 对称。
                _mini_game_invite_count_post_response_chat(lanlan_name)
                # 持久化"累计成功投递的主动搭话总数"，给 force-first 用。
                await _increment_proactive_chat_total(lanlan_name)
            else:
                logger.info("[%s] 主动搭话本轮未发起：语音 nudge 被 guard 跳过", lanlan_name)
            # No Focus cooldown here: a voice nudge is realtime and never runs a
            # Focus thinking-on reply, so it is not a Focus proactive turn — the
            # cooldown is applied only at the text Phase-2 idle path (which is
            # where _focus_idle_thinking actually gates thinking-on).
            if delivered:
                return JSONResponse(_proactive_chat_body(
                    PROACTIVE_REASON_CHAT_DELIVERED,
                    message="voice proactive triggered",
                ))
            return JSONResponse(_proactive_pass_body(
                PROACTIVE_REASON_PASS_BUSY,
                message="voice proactive skipped (guard)",
            ))

        # ========== Text-mode proactive：原子 "检查 + 占坑" ==========
        # try_start_proactive 在 _write_lock 内完成 can_start_proactive 判定 + 翻
        # IDLE→PHASE1 + 订阅派发，避免并发请求双双通过 can_start_proactive 后
        # 各自 fire(PROACTIVE_START) 导致两路 proactive 同时进入 PHASE1。
        from main_logic.session_state import SessionEvent as _SE
        if not await mgr.state.try_start_proactive(session=probe_session):
            logger.info("[%s] 主动搭话本轮未发起：AI 正在响应或已有一轮在跑（409）", lanlan_name)
            return JSONResponse(
                _proactive_error_body(
                    PROACTIVE_REASON_PASS_BUSY,
                    error="AI正在响应中，无法主动搭话",
                    message="请等待当前响应完成",
                    state=mgr.state.snapshot(),
                ),
                status_code=409,
            )
        _proactive_done_emitted = False
        # Set after activity snapshot fetch — tells the frontend scheduler
        # to skip the regular tier backoff and use a flat baseInterval on
        # the next round (the backend will then inject a uniform
        # [0, 0.5*baseInterval] sleep to provide the jitter). See the
        # screen-only delay block further down and the matching
        # ``S.proactiveFixedScheduleMode`` branch in static/app-proactive.js.
        _next_schedule_fixed_mode = False

        # Focus idle cooldown bookkeeping (read by _end_proactive via closure).
        # Set only when the flow reaches the Phase-2 idle Focus decision, so
        # short-circuit replies (mini-game invite, break-reminder, must-fire)
        # that return before Phase 2 never spend Focus charge. The episode token
        # pins the decay to the episode the thinking decision observed.
        _focus_phase2_reached = False
        _focus_episode_token = None
        _focus_turn_token = None

        async def _end_proactive(resp: JSONResponse) -> JSONResponse:
            """Wraps every normal/short-circuit proactive exit: idempotently fires PROACTIVE_DONE.

            Also injects ``next_schedule_fixed_mode`` into the response body; the
            frontend reads it to decide whether the next round of scheduling uses
            tier backoff or the fixed base interval. The injection happens at this
            unified exit, so newly added response paths need no individual changes.
            """
            nonlocal _proactive_done_emitted
            if not _proactive_done_emitted:
                _proactive_done_emitted = True
                try:
                    await mgr.state.fire(_SE.PROACTIVE_DONE)
                except Exception as _done_err:
                    logger.warning("[%s] PROACTIVE_DONE fire 异常: %s", lanlan_name, _done_err)
            try:
                body = json.loads(resp.body)
            except Exception:
                return resp
            if not isinstance(body, dict):
                return resp
            body = _ensure_proactive_reason_code(body)
            # text-mode 占坑后的所有出口都经过这里。本轮最终没把话说出来
            # （action != "chat"：各种 guard/skip/内容为空/被用户接管）就在
            # info 留一条带原因的日志，原因取响应体 message（无则 error）。
            # 散落各分支无需各自记；排查"她这轮为什么没主动说话"看这条即可。
            # 占坑前的早退（游戏路由 / voice 与 text 的 409 并发拒绝）不经过
            # 本出口，各自就地补了同前缀（"主动搭话本轮未发起："）的 info。
            _replied = body.get("action") == "chat"
            if not _replied:
                logger.info(
                    "[%s] 主动搭话本轮未发起：%s",
                    lanlan_name,
                    body.get("message") or body.get("error") or "(无原因说明)",
                )
            # Idle Focus cooldown — only for turns that reached the Phase-2 idle
            # Focus decision (short-circuit replies never set the flag, so they
            # don't spend Focus). A proactive turn never raises the charge; it
            # decays — faster when it delivered a reply (_replied) than when
            # Phase 2 produced nothing. count_turn=False + episode-token guard
            # live inside _focus_idle_cooldown.
            if _focus_phase2_reached:
                try:
                    await mgr._focus_idle_cooldown(
                        replied=_replied, episode_token=_focus_episode_token,
                        turn_token=_focus_turn_token,
                    )
                except Exception as _focus_err:
                    logger.debug("[%s] focus idle cooldown failed: %s", lanlan_name, _focus_err)
            body.setdefault('next_schedule_fixed_mode', _next_schedule_fixed_mode)
            return JSONResponse(body, status_code=resp.status_code)

        def _proactive_preempted_json(where: str) -> dict:
            # 细粒度的 state 快照留 debug；面向排查的"本轮未发起 + 原因"由统一
            # 出口 _end_proactive 按 message 打 info（这些 dict 全部经它返回），
            # 避免同一轮 skip 打出两条重复 info。
            logger.debug(
                "[%s] proactive %s preempted by user takeover (state=%s)",
                lanlan_name, where, mgr.state.snapshot(),
            )
            return {
                "success": True,
                "action": "pass",
                "reason_code": PROACTIVE_REASON_DELIVERY_PREEMPTED,
                "message": f"proactive {where} preempted by user takeover",
            }

        print(f"[{lanlan_name}] 开始主动搭话流程（两阶段架构）...")

        # ========== 拉用户活动快照 ==========
        # 在 enabled_modes 解析之前拉一次，因为 propensity 可能需要把
        # enabled_modes 收紧到只剩 vision（restricted_screen_only 状态）。
        # 详见 docs/design/user-activity-tracker.md。
        #
        # 隐私模式：用户开了"隐私模式"开关 → 临时禁用整个 user-activity-tracker，
        # 回退到 PR #1015 之前的无限制策略。snapshot 留 None，下游所有 gating
        # 都已在 PR #1015 设计时按 "snapshot is not None" 写过 fallback：
        #   - propensity 收紧（restricted_screen_only）→ 不触发
        #   - 反思/回忆 _allow_reminiscence → 默认放开
        #   - state_section 渲染 → 输出空串
        #   - mark_unfinished_thread_used → 不计数
        # 所以这里把 snapshot 直接设 None 就够，等价于"tracker 不存在"。
        from utils.preferences import ais_privacy_mode_enabled
        try:
            privacy_mode = await ais_privacy_mode_enabled()
        except Exception as _pm_err:
            # fail-closed：读不出来按隐私开启处理。正常"用户没开隐私"是
            # ais_privacy_mode_enabled 返回 False，不进这个 except。
            logger.warning(
                f"[{lanlan_name}] privacy mode check failed, defaulting to enabled: {_pm_err}",
            )
            privacy_mode = True
        if privacy_mode:
            print(f"[{lanlan_name}] 隐私模式开启，跳过 activity tracker，按无限制策略搭话")
            activity_snapshot = None
        else:
            try:
                activity_snapshot = await mgr._activity_tracker.get_snapshot()
                print(f"[{lanlan_name}] activity snapshot: state={activity_snapshot.state} "
                      f"propensity={activity_snapshot.propensity} reasons={activity_snapshot.propensity_reasons} "
                      f"skip_prob={activity_snapshot.skip_probability:.2f} tone={activity_snapshot.tone}")
            except Exception as _act_err:
                logger.warning(f"[{lanlan_name}] activity snapshot fetch failed: {_act_err}; falling back to open propensity")
                activity_snapshot = None

        # 进 proactive_chat 后第一时间推进 mini-game invite 的"已回应"判定：
        # 即便本轮不发邀请，pending 的上一次邀请也得在用户已说话时翻成已回应，
        # 否则 cooldown 永远卡在 pending。Text path 从 activity_snapshot 反推
        # last_user_msg_at；voice fast path 在上面的 voice block 内独立调一次
        # （用 mgr.last_user_activity_time），两边对称。
        _text_last_user_msg_at: float | None = None
        if activity_snapshot is not None:
            _secs = getattr(activity_snapshot, 'seconds_since_user_msg', None)
            if _secs is not None:
                _text_last_user_msg_at = time.time() - float(_secs)
        _text_advance_outcome = _mini_game_invite_advance_response(
            lanlan_name, _text_last_user_msg_at,
        )
        # 隐式 dismiss 推 WS（同 voice fast path 对称，codex P2）
        if _text_advance_outcome and _text_advance_outcome.get('session_id'):
            await _push_mini_game_invite_resolved(
                mgr,
                session_id=_text_advance_outcome['session_id'],
                action=_text_advance_outcome.get('action', 'suppress'),
            )

        # 用户级 toggle：前端 CHAT_MODE_CONFIG 里的 ``proactiveMiniGameInviteEnabled``
        # 通过 request body 的 ``mini_game_invite_enabled`` 字段透传。缺省 True 兼容
        # 旧客户端。提到 _debug_force_invite 计算之前——把 user toggle 关同时
        # 服务端开了调试旗标的场景下，下游早退 gate（closed / skip_probability）
        # 也维持原有抑制语义；不能因为旗标开了就把 gate 一并 bypass 掉。
        # CodeRabbit Major review 指出原版只在 _maybe_deliver_mini_game_invite
        # 入口拦 user toggle，旗标已经把上游 gate 绕过 → 进 _maybe_deliver
        # 又被 toggle 拦 None → caller 走普通 source picking，封禁场景仍然漏过。
        _user_invite_toggle = bool(data.get('mini_game_invite_enabled', True))

        # 调试旗标 ``MINI_GAME_INVITE_FORCE_GAME_TYPE`` 非 None 时绕开本函数所有
        # 上游早退 gate（closed / skip_probability / restricted_screen_only），
        # 让 ``_maybe_deliver_mini_game_invite`` 能稳定接到本轮调用——契约是
        # "开启后主动搭话必定触发特定小游戏"。仅本地手测使用；生产
        # ``MINI_GAME_INVITE_ENABLED`` 总开关 + 旗标默认 None 双保险。
        # 用户 toggle 关时旗标无效（与 _maybe_deliver_mini_game_invite 入口
        # 的 toggle 检查同语义，单一事实源在前端 toggle）。
        # CodeRabbit Major 指出：这条不在 ``_maybe_deliver_mini_game_invite``
        # 内部加是因为那时已经过了上游 gate，旗标做不到"必定"。
        _debug_force_invite = (
            MINI_GAME_INVITE_FORCE_GAME_TYPE is not None
            and _user_invite_toggle
        )

        # ========== Hard short-circuit: propensity=closed ==========
        # ``private`` state pins propensity to ``closed`` (see
        # main_logic/activity/snapshot.py). Skip everything — no LLM,
        # no source fetch, no prompt assembly. The user is in a
        # password manager / banking app / etc and we promised not to
        # look. Bypassed for the unfinished_thread override is
        # deliberate: if the AI just asked a question, hanging on it
        # mid-private is rude. closed > thread.
        if (
            not _debug_force_invite
            and activity_snapshot is not None
            and activity_snapshot.propensity == 'closed'
        ):
            print(f"[{lanlan_name}] propensity=closed (state={activity_snapshot.state}), 跳过本轮 proactive")
            return await _end_proactive(JSONResponse({
                "success": True,
                "action": "pass",
                "reason_code": PROACTIVE_REASON_PASS_PRIVACY,
                "message": f"user state={activity_snapshot.state} → closed (privacy lockdown)",
            }))

        # ========== Screen-only：固定间隔 + 后端抖动 ==========
        # 用户处于 gaming / focused_work（propensity=restricted_screen_only）
        # 时，常规的前端 3-tier 退避会让搭话间隔指数级增长，跟陪伴产品
        # 命题冲突（用户最长会话段反而最安静）。改用：
        #   1. 前端 reset backoffLevel=0 并按 baseInterval 等间隔触发
        #      （由响应里的 next_schedule_fixed_mode=True 通知前端切换）
        #   2. 后端在 LLM 调用前 sleep uniform(0, 0.5 * baseInterval)，把每轮
        #      实际间隔从 base 抹成 [base, 1.5*base] 的均匀分布
        # 总效果：屏幕态平均间隔 ≈ 1.25*base，且有自然的随机抖动。
        # skip_probability（仅 immersive_horror=0.3）作为正交机制保留。
        #
        # ⚠️ 标志位 vs sleep 拆开：anti_slack_pending / work_break_pending
        # 是 focused_work 下的 must-fire 提醒（紧跟在下一段 4425+），本身
        # 时间敏感，不能被这里的随机抖动延后。但前端 fixed_mode 标志位
        # 仍然要设——否则 must-fire 走 _end_proactive 时响应里会带回
        # next_schedule_fixed_mode=False，前端误切回 tier backoff，让用户
        # 离开 must-fire 状态后又被退避机制吞掉一段时间。
        # Codex P2 + CodeRabbit Major review。
        if (
            activity_snapshot is not None
            and activity_snapshot.propensity == 'restricted_screen_only'
        ):
            _next_schedule_fixed_mode = True
            _has_must_fire = (
                activity_snapshot.anti_slack_pending is not None
                or activity_snapshot.work_break_pending is not None
            )
            if _has_must_fire:
                print(f"[{lanlan_name}] propensity=restricted_screen_only 但有 must-fire 提醒待发，跳过本轮抖动 sleep")
            else:
                try:
                    _base_interval_raw = data.get('base_interval_seconds')
                    _base_interval = float(_base_interval_raw) if _base_interval_raw is not None else 0.0
                except (TypeError, ValueError):
                    _base_interval = 0.0
                # 上限兜底：base 过大时把 0.5*base 截到 60s，避免极端配置
                # （比如 user 把 proactiveChatInterval 调到 300s）让后端
                # 单请求占连接十分钟。
                if _base_interval > 0:
                    _jitter_max = min(_base_interval * 0.5, 60.0)
                    _jitter = random.uniform(0.0, _jitter_max)
                    print(f"[{lanlan_name}] propensity=restricted_screen_only, 后端注入 {_jitter:.2f}s 间隔抖动（base={_base_interval:.1f}s）")
                    await asyncio.sleep(_jitter)

        # ========== Must-fire: break-reminder branches ==========
        # Anti-slack outranks water-break (transition trigger more
        # time-sensitive than the cumulative one). Both bypass Phase 1
        # entirely and run via _deliver_break_reminder_via_llm — see
        # the helper docstring above. ``private`` state already cleared
        # both pendings inside the tracker, so reaching here implies
        # not-private. Debug-force-invite still takes precedence so the
        # mini-game force flag keeps its "guaranteed mini-game" contract.
        if (
            not _debug_force_invite
            and activity_snapshot is not None
            and (
                activity_snapshot.anti_slack_pending is not None
                or activity_snapshot.work_break_pending is not None
            )
        ):
            try:
                _break_lang = _resolve_proactive_locale(data, mgr)
            except Exception:
                _break_lang = 'zh'

            # Resolve character_prompt up front and prepend it to every
            # break-reminder SystemMessage. Without this the model would
            # see only the env-notice template and lose its persona —
            # CodeRabbit Major review (PR #1226). Mirrors the
            # placeholder substitution the normal Phase 2 path does at
            # line ~5300 (LANLAN_NAME / MASTER_NAME).
            _break_character_prompt = lanlan_prompt_map.get(lanlan_name, '')
            if _break_character_prompt:
                _break_character_prompt = (
                    _break_character_prompt
                    .replace('{LANLAN_NAME}', lanlan_name)
                    .replace('{MASTER_NAME}', master_name_current)
                )

            def _compose_break_system_prompt(env_notice: str) -> str:
                if not _break_character_prompt:
                    return env_notice
                return f'{_break_character_prompt}\n\n{env_notice}'

            # Anti-slack first — single-behavior 'back to work' nudge.
            if activity_snapshot.anti_slack_pending is not None:
                anti_pending = activity_snapshot.anti_slack_pending
                anti_prompt = _render_anti_slack_prompt(
                    pending=anti_pending,
                    master_name=master_name_current,
                    lang=_break_lang,
                )
                delivered_text, _proactive_sid_unused = await _deliver_break_reminder_via_llm(
                    lanlan_name=lanlan_name,
                    mgr=mgr,
                    system_prompt=_compose_break_system_prompt(anti_prompt),
                    channel='anti_slack',
                    lang=_break_lang,
                )
                if delivered_text:
                    try:
                        mgr._activity_tracker.mark_anti_slack_used()
                    except Exception as _mark_err:
                        logger.warning(
                            "[%s] mark_anti_slack_used failed: %s",
                            lanlan_name, _mark_err,
                        )
                    # Mini-game cooldown counter — same contract as the
                    # normal text proactive path at ~6253: any successful
                    # proactive emission counts as one of the "10 chats
                    # since user responded" gate. No-op when no prior
                    # invite is pending. Codex/CodeRabbit Minor: PR #1226.
                    _mini_game_invite_count_post_response_chat(lanlan_name)
                    await _increment_proactive_chat_total(lanlan_name)
                    return await _end_proactive(JSONResponse({
                        "success": True,
                        "action": "chat",
                        "reason_code": PROACTIVE_REASON_CHAT_DELIVERED,
                        "message": "anti-slack reminder delivered",
                        "channel": "anti_slack",
                    }))
                # Delivery rejected (user took over / config issue).
                # Don't fall through to normal proactive — must-fire
                # semantics: leave pending armed for the next round.
                return await _end_proactive(JSONResponse({
                    "success": True,
                    "action": "pass",
                    "reason_code": PROACTIVE_REASON_DELIVERY_PREEMPTED,
                    "message": "anti-slack reminder pending but delivery skipped",
                }))

            # Water-break — 50% pivots to a rest+game-invite combo
            # (gated on mini-game cooldown / user toggle / global
            # kill switch / existence of a valid game_type). Any of
            # those gates failing falls through to the regular
            # drink/stretch nudge instead of breaking the must-fire.
            water_pending = activity_snapshot.work_break_pending
            prefs_for_break = mgr._activity_tracker._sm._prefs
            _gi_prob = prefs_for_break.work_break_game_invite_probability
            if _gi_prob is None:
                # Resolved at import time — see tracker.py defaults.
                from main_logic.activity.tracker import _WORK_BREAK_GAME_INVITE_PROBABILITY as _gi_prob_default
                _gi_prob = _gi_prob_default
            branch_game_invite = False
            chosen_game_type: str | None = None
            gi_prompt: str | None = None
            if (
                MINI_GAME_INVITE_ENABLED
                and _user_invite_toggle
                and _gi_prob > 0
            ):
                import random as _random
                if _random.random() < _gi_prob:
                    chosen_game_type = _pick_mini_game_type(lanlan_name)
                    if chosen_game_type is not None:
                        gi_prompt = _render_work_break_game_invite_prompt(
                            pending=water_pending,
                            game_type=chosen_game_type,
                            master_name=master_name_current,
                            lang=_break_lang,
                        )
                        if gi_prompt is not None:
                            branch_game_invite = True

            if branch_game_invite and chosen_game_type is not None and gi_prompt is not None:
                delivered_text, _proactive_sid_unused = await _deliver_break_reminder_via_llm(
                    lanlan_name=lanlan_name,
                    mgr=mgr,
                    system_prompt=_compose_break_system_prompt(gi_prompt),
                    channel='work_break_game_invite',
                    lang=_break_lang,
                )
                if delivered_text:
                    invite_session_id = str(uuid4())
                    _mini_game_invite_record_delivered(lanlan_name, invite_session_id)
                    _mini_game_invite_get_state(lanlan_name)['last_game_type'] = chosen_game_type
                    # Persist counter+1 + ever_delivered atomically (mini-game cooldown
                    # contract). Track success so we can fall back to the plain
                    # _increment_proactive_chat_total if persistence fails — otherwise
                    # the chat-total counter would skip this round entirely.
                    # CodeRabbit Major: don't double-count — the persistent record
                    # already does the +1, so plain counter is only the fallback.
                    _persist_ok = False
                    try:
                        await _record_invite_delivery_persistent(lanlan_name)
                        _persist_ok = True
                    except Exception as _persist_err:
                        logger.warning(
                            "[%s] record_invite_delivery_persistent failed: %s",
                            lanlan_name, _persist_err,
                        )
                    try:
                        from utils.instrument import counter as _instr_counter
                        # 与 proactive 通道共用 mini_game_invited，channel 维度区分；
                        # 不计 persist 成败——邀请 UI 已投递给用户即算一次邀请。
                        _instr_counter(
                            "mini_game_invited",
                            game_type=str(chosen_game_type)[:24],
                            channel="work_break",
                            force_first=False,
                        )
                    except Exception:
                        # 埋点 best-effort，失败不影响邀请投递
                        pass
                    options_payload = _build_mini_game_invite_options_payload(
                        invite_lang=_break_lang,
                        game_type=chosen_game_type,
                        session_id=invite_session_id,
                    )
                    try:
                        if mgr.websocket and hasattr(mgr.websocket, 'send_json'):
                            client_state = getattr(mgr.websocket, 'client_state', None)
                            if client_state is None or client_state == client_state.CONNECTED:
                                await mgr.websocket.send_json(options_payload)
                    except Exception as _ws_err:
                        logger.warning(
                            "[%s] work_break+game_invite options WS push failed: %s",
                            lanlan_name, _ws_err,
                        )
                    try:
                        mgr._activity_tracker.mark_work_break_used()
                    except Exception as _mark_err:
                        logger.warning(
                            "[%s] mark_work_break_used failed: %s",
                            lanlan_name, _mark_err,
                        )
                    if not _persist_ok:
                        # Persistence path failed → counter wasn't bumped.
                        # Fall back to the plain in-memory increment so the
                        # round still counts toward proactive_chat totals.
                        await _increment_proactive_chat_total(lanlan_name)
                    return await _end_proactive(JSONResponse({
                        "success": True,
                        "action": "chat",
                        "reason_code": PROACTIVE_REASON_CHAT_DELIVERED,
                        "message": "work-break + game-invite delivered",
                        "channel": "work_break_game_invite",
                        "game_type": chosen_game_type,
                        "invite_session_id": invite_session_id,
                    }))
                # Combo branch delivery failed → don't fall through to
                # regular drink branch (would double-charge the user's
                # attention). Pending stays armed for next round.
                return await _end_proactive(JSONResponse({
                    "success": True,
                    "action": "pass",
                    "reason_code": PROACTIVE_REASON_DELIVERY_PREEMPTED,
                    "message": "work-break + game-invite pending but delivery skipped",
                }))

            # Regular drink/stretch nudge branch
            wb_prompt, wb_seed = _render_work_break_prompt(
                pending=water_pending,
                master_name=master_name_current,
                lang=_break_lang,
            )
            delivered_text, _proactive_sid_unused = await _deliver_break_reminder_via_llm(
                lanlan_name=lanlan_name,
                mgr=mgr,
                system_prompt=_compose_break_system_prompt(wb_prompt),
                channel='work_break',
                lang=_break_lang,
            )
            if delivered_text:
                try:
                    mgr._activity_tracker.mark_work_break_used()
                except Exception as _mark_err:
                    logger.warning(
                        "[%s] mark_work_break_used failed: %s",
                        lanlan_name, _mark_err,
                    )
                # Same chats-since-response counter as anti_slack branch.
                _mini_game_invite_count_post_response_chat(lanlan_name)
                await _increment_proactive_chat_total(lanlan_name)
                return await _end_proactive(JSONResponse({
                    "success": True,
                    "action": "chat",
                    "reason_code": PROACTIVE_REASON_CHAT_DELIVERED,
                    "message": "work-break reminder delivered",
                    "channel": "work_break",
                    "seed": wb_seed,
                }))
            return await _end_proactive(JSONResponse({
                "success": True,
                "action": "pass",
                "reason_code": PROACTIVE_REASON_DELIVERY_PREEMPTED,
                "message": "work-break reminder pending but delivery skipped",
            }))

        # ========== Probabilistic skip (intensity-driven gate) ==========
        # ``skip_probability`` is rolled BEFORE we burn LLM cost.
        # Default 0 for non-gaming and varied gaming, so this only
        # kicks in for tagged competitive / immersive-horror gaming
        # — or whatever combos the user has dialed up via
        # preferences.json::skip_probability_overrides.
        #
        # The unfinished_thread guard means open threads still get
        # follow-ups even at skip=1.0: if the AI promised to come
        # back to something, we honour that promise regardless of
        # how silenced the user wanted us. The thread mechanism's
        # 2-followup hard cap already prevents harassment.
        if (
            not _debug_force_invite
            and activity_snapshot is not None
            and activity_snapshot.skip_probability > 0
            and activity_snapshot.unfinished_thread is None
        ):
            import random as _random
            if _random.random() < activity_snapshot.skip_probability:
                print(
                    f"[{lanlan_name}] skip_probability={activity_snapshot.skip_probability:.2f} "
                    f"rolled (state={activity_snapshot.state} intensity={activity_snapshot.game_intensity} "
                    f"genre={activity_snapshot.game_genre})，本轮跳过"
                )
                return await _end_proactive(JSONResponse({
                    "success": True,
                    "action": "pass",
                    "reason_code": PROACTIVE_REASON_PASS_THROTTLED,
                    "message": (
                        f"probabilistic skip: state={activity_snapshot.state} "
                        f"intensity={activity_snapshot.game_intensity} "
                        f"skip_prob={activity_snapshot.skip_probability:.2f}"
                    ),
                }))

        # ========== 解析 enabled_modes ==========
        # 兼容旧版前端：``enabled_modes`` 字段缺席 → 根据其它字段推断；显式传 ``[]``
        # 表示新版客户端"用户把所有 source toggle 都关了"，不能再走 BC fallback
        # 退化到 home/trending（否则 mini-game 邀请 toggle 单独开启的场景下 dice
        # miss 会让 home 兜底打破 toggle 契约——codex P1）。
        if 'enabled_modes' in data:
            enabled_modes = data.get('enabled_modes') or []
        else:
            content_type = data.get('content_type', None)
            screenshot_data = data.get('screenshot_data')
            if screenshot_data and isinstance(screenshot_data, str):
                enabled_modes = ['vision']
            elif data.get('use_window_search', False):
                enabled_modes = ['window']
            elif content_type == 'news':
                enabled_modes = ['news']
            elif content_type == 'video':
                enabled_modes = ['video']
            elif data.get('use_personal_dynamic', False):
                enabled_modes = ['personal']
            else:
                enabled_modes = ['home']

        # 是否有 5 分钟内未收尾话题。若有，restricted_screen_only / sources 空
        # 这两个早退分支都让步——AI 能基于 conversation history 接续旧话题，
        # 不需要任何外部素材。
        _has_unfinished_thread = (
            activity_snapshot is not None
            and activity_snapshot.unfinished_thread is not None
        )

        # restricted_screen_only：用户处于 gaming / focused_work，仅允许屏幕通道。
        # 把 enabled_modes 收紧到只剩 vision。如果前端这一轮根本没启用 vision，
        # 直接 pass —— 没东西可看，又不让聊外部，没必要继续。
        # 例外：有未收尾话题（5min 内 AI 提的问题用户还没回）→ 即使没 vision
        # 也允许跑下去，跟进上一个问题不需要外部素材。
        if (
            not _debug_force_invite
            and activity_snapshot is not None
            and activity_snapshot.propensity == 'restricted_screen_only'
        ):
            if 'vision' in enabled_modes:
                enabled_modes = ['vision']
                print(f"[{lanlan_name}] propensity=restricted_screen_only, 收紧 enabled_modes 到仅 vision")
            elif _has_unfinished_thread:
                enabled_modes = []
                print(f"[{lanlan_name}] propensity=restricted_screen_only 但有未收尾话题，允许 text-only 跟进")
            else:
                return await _end_proactive(JSONResponse({
                    "success": True,
                    "action": "pass",
                    "reason_code": PROACTIVE_REASON_PASS_RESTRICTED_SCREEN_ONLY,
                    "message": f"user state={activity_snapshot.state} restricts proactive to screen-only, but vision not enabled this round",
                }))

        print(f"[{lanlan_name}] 启用的搭话模式: {enabled_modes}")

        # ========== Mini-game 邀请短路 ==========
        # 过完 propensity / skip_probability / restricted_screen_only 这几道门后，
        # 独立掷一次 10% 骰子；命中即用静态 i18n 模板直投递邀请，跳过 Phase 1/2
        # LLM 与 source fetching。一次邀请被回应后 24h+10 chats cooldown，期间
        # 不再掷骰。activity_snapshot is None（隐私模式 / tracker 不可用）保守
        # 不发——无法判断是否在工作状态。
        try:
            invite_lang = _resolve_proactive_locale(data, mgr)
        except Exception:
            invite_lang = 'zh'
        # _user_invite_toggle 已经在上面 _debug_force_invite 计算前算过——把
        # toggle 关时旗标也连带禁用，保证早退 gate 不被绕过。
        invite_outcome = await _maybe_deliver_mini_game_invite(
            lanlan_name=lanlan_name,
            mgr=mgr,
            activity_snapshot=activity_snapshot,
            invite_lang=invite_lang,
            master_name=master_name_current,
            user_toggle_enabled=_user_invite_toggle,
        )
        if invite_outcome is not None:
            return await _end_proactive(JSONResponse(invite_outcome))

        # 用户把所有 source toggle 都关了（仅留 mini-game 邀请独立 toggle 触发本轮
        # 请求），mini-game 短路又没命中：没什么可聊。直接 pass 而不是落到下面源
        # picking 走空 list / 撞 "所有信息源获取失败" 500 分支。例外：仍然有未收尾
        # 话题 → 让 Phase 2 走 text-only 跟进路径（与 sources={} 但 thread 在的兜
        # 底语义对齐）。codex P1 指出：BC fallback 已经按 "字段缺席 vs 显式 []" 分
        # 流，这里对显式空清晰退出。
        if not enabled_modes and not _has_unfinished_thread:
            print(f"[{lanlan_name}] enabled_modes 空 + mini-game miss + 无 unfinished_thread → pass")
            return await _end_proactive(JSONResponse({
                "success": True,
                "action": "pass",
                "reason_code": PROACTIVE_REASON_PASS_SOURCE_EMPTY,
                "message": "no source modes enabled and mini-game invite did not fire",
            }))

        # 全局 source 衰减历史：进入 picking 前确保已惰性加载到内存（首次为线程池
        # IO，后续是 O(1) flag 检查）。同步 picking loop 后续直接读 dict。
        await _ensure_source_history_loaded()

        # ========== 0. 并行获取所有信息源内容（无 LLM） ==========
        screenshot_data = data.get('screenshot_data')
        has_screenshot = bool(screenshot_data) and isinstance(screenshot_data, str)
        # Avatar 位置元数据（前端截图时捕获的归一化坐标）
        avatar_position = data.get('avatar_position')
        
        async def _fetch_source(mode: str) -> tuple:
            """
            Fetch a single source; returns (mode, content_dict) or raises an exception.
            """
            if mode == 'vision':
                if not has_screenshot:
                    raise ValueError("无截图数据（screenshot_data 为空或类型不正确）")
                window_title = data.get('window_title', '')
                # ⚠️ Phase 1 不调用 vision_model 分析截图！
                # 截图将在 Phase 2 由 vision_model 直接读取原图，这里只做压缩。
                compressed_b64 = ''
                try:
                    b64_raw = screenshot_data.split(',', 1)[1] if ',' in screenshot_data else screenshot_data
                    compressed_b64 = await asyncio.to_thread(
                        decode_and_compress_screenshot_b64,
                        b64_raw,
                        COMPRESS_TARGET_HEIGHT,
                        COMPRESS_JPEG_QUALITY,
                    )
                    # 叠加 Avatar 文字注解（_fetch_source 内部 proactive_lang
                    # 尚未解析，Phase 1 使用全局语言；Phase 2 会用请求级别的 proactive_lang）
                    if avatar_position and isinstance(avatar_position, dict):
                        try:
                            from utils.screenshot_utils import overlay_avatar_annotation
                            from utils.language_utils import get_global_language_full
                            compressed_b64 = await asyncio.to_thread(
                                overlay_avatar_annotation,
                                compressed_b64, avatar_position, lanlan_name,
                                get_global_language_full(),
                            )
                        except Exception as ann_err:
                            logger.warning(f"[{lanlan_name}] Phase 1 avatar annotation failed: {ann_err}")
                    jpg_size_kb = len(compressed_b64) * 3 // 4 // 1024
                    print(f"[{lanlan_name}] Vision 通道: 截图压缩完成 {jpg_size_kb}KB (Phase 2 将直接分析)")
                except Exception as compress_err:
                    logger.warning(f"[{lanlan_name}] 截图压缩失败（Phase 2 将无法使用截图）: {compress_err}")
                return (mode, {'window_title': window_title, 'screenshot_b64': compressed_b64})
            
            elif mode == 'news':
                news_content = await fetch_news_content(limit=_PHASE1_FETCH_PER_SOURCE)
                if not news_content['success']:
                    raise ValueError(f"获取新闻失败: {news_content.get('error')}")
                formatted = format_news_content(news_content)
                _log_news_content(lanlan_name, news_content)
                # 提取链接信息
                links = _extract_links_from_raw(mode, news_content)
                return (mode, {'formatted_content': formatted, 'raw_data': news_content, 'links': links})
            
            elif mode == 'video':
                video_content = await fetch_video_content(limit=_PHASE1_FETCH_PER_SOURCE)
                if not video_content['success']:
                    raise ValueError(f"获取视频失败: {video_content.get('error')}")
                formatted = format_video_content(video_content)
                _log_video_content(lanlan_name, video_content)
                links = _extract_links_from_raw(mode, video_content)
                return (mode, {'formatted_content': formatted, 'raw_data': video_content, 'links': links})
            
            elif mode == 'window':
                window_context_content = await fetch_window_context_content(limit=5)
                if not window_context_content['success']:
                    raise ValueError(f"获取窗口上下文失败: {window_context_content.get('error')}")
                formatted = format_window_context_content(window_context_content)
                raw_title = window_context_content.get('window_title', '')
                sanitized_title = raw_title[:30] + '...' if len(raw_title) > 30 else raw_title
                print(f"[{lanlan_name}] 成功获取窗口上下文: {sanitized_title}")
                return (mode, {'formatted_content': formatted, 'raw_data': window_context_content, 'links': []})
            
            elif mode == 'home':
                trending_content = await fetch_trending_content(
                    bilibili_limit=_PHASE1_FETCH_PER_SOURCE,
                    weibo_limit=_PHASE1_FETCH_PER_SOURCE
                )
                if not trending_content['success']:
                    raise ValueError(f"获取首页推荐失败: {trending_content.get('error')}")
                formatted = format_trending_content(trending_content)
                _log_trending_content(lanlan_name, trending_content)
                links = _extract_links_from_raw(mode, trending_content)
                return (mode, {'formatted_content': formatted, 'raw_data': trending_content, 'links': links})

            elif mode == 'personal':
                personal_dynamics = await fetch_personal_dynamics(limit=_PHASE1_FETCH_PER_SOURCE)
                if not personal_dynamics['success']:
                    raise ValueError(f"获取个人动态失败: {personal_dynamics.get('error')}")
                formatted = format_personal_dynamics(personal_dynamics)
                _log_personal_dynamics(lanlan_name, personal_dynamics)
                links = _extract_links_from_raw(mode, personal_dynamics)
                return (mode, {'formatted_content': formatted, 'raw_data': personal_dynamics, 'links': links})
            
            elif mode == 'music':
                return (mode, {'placeholder': True, 'note': '关键词将在 Phase 1 开始前生成'})
            
            elif mode == 'meme':
                # meme 关键词将由合并 LLM 调用生成，此处仅占位
                return (mode, {'placeholder': True, 'note': '关键词将由合并 Phase 1 LLM 生成'})

            else:
                raise ValueError(f"未知模式: {mode}")
        
        # 并行获取所有信息源
        fetch_tasks = [
            _fetch_source(m)
            for m in enabled_modes
        ]
        fetch_results = await asyncio.gather(*fetch_tasks, return_exceptions=True)
        
        # 收集成功的信息源
        sources: dict[str, dict] = {}
        for i, result in enumerate(fetch_results):
            if isinstance(result, Exception):
                failed_mode = enabled_modes[i]
                logger.warning(f"[{lanlan_name}] 信息源 [{failed_mode}] 获取失败: {result}")
                continue
            mode, content = result
            sources[mode] = content
        
        if not sources:
            # 例外：未收尾话题模式下 enabled_modes 可能本就被清空（restricted_screen_only
            # + 无 vision），sources 必定为空但不应当 pass —— 让 Phase 2 拿对话
            # 历史 + state_section 跑 text-only [CHAT] 跟进。
            if not _has_unfinished_thread:
                return await _end_proactive(JSONResponse(
                    _proactive_pass_body(
                        PROACTIVE_REASON_ERROR_SOURCE_FETCH_FAILED,
                        success=False,
                        error="所有信息源获取失败",
                    ),
                    status_code=500,
                ))
            print(f"[{lanlan_name}] sources 为空但有未收尾话题，进入 text-only 跟进路径")

        # Phase 1 preempt check：信息源并行 fetch 完，正式进入 LLM 前先瞄一眼
        if mgr.state.is_proactive_preempted():
            return await _end_proactive(JSONResponse(_proactive_preempted_json("phase1_post_fetch")))

        print(f"[{lanlan_name}] 成功获取 {len(sources)} 个信息源: {list(sources.keys())}")

        # ========== 1. 获取记忆上下文 (New Dialog) ==========
        # new_dialog 返回格式：
        # ========以下是{name}的内心活动========
        # {内心活动/Settings}...
        # 现在时间...整理了近期发生的事情。
        # Name | Content
        # ...
        
        raw_memory_context = ""
        try:
            from utils.internal_http_client import get_internal_http_client
            _pt_client = get_internal_http_client()
            resp = await _pt_client.get(
                f"http://127.0.0.1:{MEMORY_SERVER_PORT}/new_dialog/{lanlan_name}",
                timeout=5.0,
            )
            resp.raise_for_status()  # Check for HTTP errors explicitly
            if resp.status_code == 200:
                raw_memory_context = resp.text
            else:
                logger.warning(f"[{lanlan_name}] 记忆服务返回非200状态: {resp.status_code}，使用空上下文")
        except Exception as e:
            logger.warning(f"[{lanlan_name}] 获取记忆上下文失败，使用空上下文: {e}")
        
        # 解析 new_dialog 响应：把"内心活动"与"对话历史"切开。
        # 切分逻辑（locale 无关）集中在 prompts_memory.split_inner_thoughts_and_history，
        # 以 INNER_THOUGHTS_DYNAMIC 的多语言模板为准；任一 locale 都匹配不到时返回
        # None，这里兜底为"全部当历史、内心活动留空"并打 warning（不再静默错位）。
        def _parse_new_dialog(text: str) -> tuple[str, str]:
            if not text:
                return "", ""
            from config.prompts.prompts_memory import split_inner_thoughts_and_history
            split = split_inner_thoughts_and_history(text)
            if split is None:
                logger.warning(
                    "[%s] new_dialog 未匹配到内心活动分隔句（任一 locale），"
                    "整段归入对话历史，当前内心留空",
                    lanlan_name,
                )
                return text, ""
            inner_thoughts_part, history_part = split
            return history_part, inner_thoughts_part

        memory_context, inner_thoughts = _parse_new_dialog(raw_memory_context)

        # Phase 1 preempt check：memory_server new_dialog 是 phase1 里首次大 await
        # （httpx timeout 5s）。用户在这期间打断只能等超时才有下一次 check，
        # 这里补一刀。
        if mgr.state.is_proactive_preempted():
            return await _end_proactive(JSONResponse(_proactive_preempted_json("phase1_post_memory")))

        # ========== 2. 选择语言 ==========
        # 与 mini-game 邀请短路同源：request body → mgr.user_language → 全局缓存。
        # 见 _resolve_proactive_locale 的 docstring。
        try:
            proactive_lang = _resolve_proactive_locale(data, mgr)
        except Exception:
            proactive_lang = 'zh'
        topic_hook_lang = _resolve_topic_hook_locale(data, mgr, fallback=proactive_lang)
        
        # ========== 3. 注入近期搭话记录 ==========
        proactive_chat_history_prompt = _format_recent_proactive_chats(lanlan_name, proactive_lang)

        # 趁机把 open_threads 计算起来——和下面 Phase 1 unified LLM 调用并行。
        # 缓存按用户消息序号失效；没新用户发言就 no-op 直接返回。Phase 2 读
        # snapshot 时会拿到这次的结果（如果赶上了）；赶不上就用上一次的缓存。
        try:
            mgr._activity_tracker.kickoff_open_threads_compute(lang=topic_hook_lang)
        except Exception as _ot_err:
            logger.debug(f"[{lanlan_name}] kickoff_open_threads_compute failed: {_ot_err}")

        # ========== 3.5 反思 + 回调话题（通过 memory_server API） ==========
        # 认知框架：Facts → Reflection(pending) → 主动搭话自然提及 → 用户反馈 → Persona
        #
        # 用户在 gaming / focused_work 状态下不应自然回忆——会很尬。直接跳过整段
        # （也省 reflect POST 的 15s timeout 风险）。stale_returning 反而欢迎回忆。
        followup_topics_prompt = ""
        _followup_topics = []
        _surfaced_reflection_ids = []  # 记录本次搭话提及了哪些 pending 反思
        _allow_reminiscence = (
            activity_snapshot is None
            or activity_snapshot.propensity != 'restricted_screen_only'
        )
        if not _allow_reminiscence:
            print(f"[{lanlan_name}] propensity=restricted_screen_only, 跳过反思/回忆话题获取")
        # 复用 internal_http_client 单例：proactive_chat 每次主动搭话都走此路径。
        # 仅 read：取 followup_topics 候选用于本轮 prompt 注入。
        # 历史上这一段还前置调过 POST /reflect/{name}（"自动状态迁移 + 反思合成"），
        # 已删除——合成迁到 ``_periodic_reflection_synthesis_loop`` 后端循环、
        # auto_promote 早就由 ``_periodic_auto_promote_loop`` 每 180s 跑。把
        # mutation 留在 proactive 关键路径上既拖延 ~15s response、又让整个
        # reflection 生命周期跟前端 setTimeout 强耦合（前端不开 → 永不合成）。
        if _allow_reminiscence:
            try:
                from utils.internal_http_client import get_internal_http_client
                _mem_base = f"http://127.0.0.1:{MEMORY_SERVER_PORT}"
                _mem_client = get_internal_http_client()
                _topics_resp = await _mem_client.get(
                    f"{_mem_base}/followup_topics/{lanlan_name}", timeout=5.0,
                )
                if _topics_resp.status_code == 200:
                    _followup_topics = _topics_resp.json().get('topics', [])
                    if _followup_topics:
                        try:
                            (
                                followup_topics_prompt,
                                _surfaced_reflection_ids,
                            ) = _render_followup_topic_hooks(
                                topic_hook_lang,
                                _followup_topics,
                            )
                        except Exception as _followup_prompt_err:
                            logger.debug(f"[{lanlan_name}] followup topic prompt build failed: {_followup_prompt_err}")
                        print(f"[{lanlan_name}] 回调话题候选: {len(_followup_topics)} 条")
            except Exception as e:
                logger.debug(f"[{lanlan_name}] 回调话题获取失败（不影响主流程）: {e}")

        # Phase 1 preempt check：followup GET(5s) 是一段可能拖久的 await，
        # 整段裸跑会让用户打断后继续跑完 LLM 配置和后续步骤，再到 pre-LLM
        # check 才识破。这里补一刀。
        if mgr.state.is_proactive_preempted():
            return await _end_proactive(JSONResponse(_proactive_preempted_json("phase1_post_reflect")))

        # ========== 4. 获取 LLM 配置 ==========
        # 主动搭话全链路（Phase1 筛选 / Phase2 生成 / regen）用 conversation tier
        # 而非 correction tier：correction（纠错）模型在不开思考时较难稳定遵循
        # "第一行写来源标签" 的格式，容易把人设约束块当正文吐出来；conversation
        # 是主对话主力模型，格式遵循更稳。仍保持 disable_thinking（vision+思考必超时）。
        try:
            conversation_config = _config_manager.get_model_api_config('conversation')
            conversation_model = conversation_config.get('model')
            conversation_base_url = conversation_config.get('base_url')
            conversation_api_key = conversation_config.get('api_key')
            conversation_provider_type = conversation_config.get('provider_type')

            if not conversation_model or not conversation_api_key:
                logger.error("对话模型配置缺失: model或api_key未设置")
                return await _end_proactive(JSONResponse({
                    "success": False,
                    "reason_code": PROACTIVE_REASON_ERROR_INTERNAL,
                    "error": "对话模型配置缺失",
                    "detail": "请在设置中配置对话模型的model和api_key"
                }, status_code=500))

            vision_config = _config_manager.get_model_api_config('vision')
            vision_model_name = vision_config.get('model', '')
            vision_base_url = vision_config.get('base_url', '')
            vision_api_key = vision_config.get('api_key', '')
            vision_provider_type = vision_config.get('provider_type')
            has_vision_model = bool(vision_model_name and vision_api_key)
            if not has_vision_model:
                logger.info("Vision 模型未配置，Phase 2 将退回使用对话模型")
        except Exception as e:
            logger.error(f"获取模型配置失败: {e}")
            return await _end_proactive(JSONResponse({
                "success": False,
                "reason_code": PROACTIVE_REASON_ERROR_INTERNAL,
                "error": "模型配置异常",
                "detail": str(e)
            }, status_code=500))

        async def _make_llm(temperature: float = 1.0,
                            max_completion_tokens: int = PROACTIVE_PHASE2_GENERATE_MAX_TOKENS,
                            use_vision: bool = False, disable_thinking: bool = True):
            """
            Create an LLM instance. use_vision=True uses the vision model;
            when disable_thinking=False (Focus thinking-on) the provider's
            thinking-disable extras are stripped while other auto-resolved
            extras (e.g. web_search) are preserved.
            """
            if use_vision and has_vision_model:
                m, bu, ak = vision_model_name, vision_base_url, vision_api_key
                provider_type = vision_provider_type
            else:
                m, bu, ak = conversation_model, conversation_base_url, conversation_api_key
                provider_type = conversation_provider_type
            from config import DIALOG_LLM_STREAM_TIMEOUT_SECONDS
            kw: dict = dict(
                temperature=temperature,
                max_completion_tokens=max_completion_tokens,
                streaming=True,
                timeout=DIALOG_LLM_STREAM_TIMEOUT_SECONDS,  # hang-guard for the streaming call
                provider_type=provider_type,
            )
            if not disable_thinking:
                # Focus thinking-on: strip ONLY the thinking-disable keys from
                # the provider's auto-resolved extra_body, KEEP the rest. Setting
                # extra_body=None would skip all auto-resolved extras and
                # silently drop e.g. step-2-mini's built-in web_search on focused
                # proactive Phase-2 generations (对偶 inline path
                # OmniOfflineClient._focus_stream_overrides → focus_extra_body).
                kw["extra_body"] = focus_extra_body(m)
            return await create_chat_llm_async(m, bu, ak, **kw)  # noqa: LLM_OUTPUT_BUDGET  # budget + timeout set in kw above (splat invisible to the lint).

        async def _llm_call_with_retry(
            system_prompt: str, label: str, *,
            temperature: float = 1.0,
            max_completion_tokens: int = PROACTIVE_PHASE1_UNIFIED_MAX_TOKENS,
            timeout: float = 16.0,
            use_vision: bool = False, disable_thinking: bool = True,
            image_b64: str = '',
            dynamic_context: str = '',
        ) -> str:
            """
            LLM call with retry. When image_b64 is non-empty, the screenshot is sent multimodally.
            dynamic_context: dynamic context injected into the HumanMessage so the SystemMessage stays cacheable.
            """
            begin_text = _loc(BEGIN_GENERATE, proactive_lang)
            human_text = f"{dynamic_context}\n\n{begin_text}" if dynamic_context else begin_text
            if image_b64:
                human_content = [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                    {"type": "text", "text": human_text},
                ]
            else:
                human_content = human_text
            messages = [SystemMessage(content=system_prompt), HumanMessage(content=human_content)]

            from utils.token_tracker import set_call_type
            set_call_type("proactive")
            max_retries = 3
            retry_delays = [1, 2]
            for attempt in range(max_retries):
                try:
                    # 使用 async with 确保 ChatOpenAI (AsyncOpenAI) 实例被正确关闭
                    async with (await _make_llm(temperature=temperature,
                                                max_completion_tokens=max_completion_tokens,
                                                use_vision=use_vision,
                                                disable_thinking=disable_thinking)) as llm:
                        response = await asyncio.wait_for(
                            llm.ainvoke(messages),
                            timeout=timeout
                        )
                        # [临时调试]
                        print(f"\n[PROACTIVE-DEBUG] LLM output [{label}]: {response.content[:500]}...\n")
                        return response.content.strip()
                except _PROACTIVE_LLM_RETRY_ERROR_TYPES as e:
                    if attempt < max_retries - 1:
                        logger.warning(f"[{lanlan_name}] LLM [{label}] 调用失败 (尝试 {attempt + 1}/{max_retries}): {e}")
                        await asyncio.sleep(retry_delays[attempt])
                    else:
                        logger.error(f"[{lanlan_name}] LLM [{label}] 调用失败，已达最大重试: {e}")
                        raise
            raise RuntimeError("Unexpected")
        
        # ================================================================
        # Phase 1: 合并 LLM 调用（web 筛选 + music 关键词 + meme 关键词）
        # ⚠️ 一阶段一定不要分析屏幕！截图会在二阶段由 vision_model 直接 feed in。
        # - 所有文本源合并 → 1 次 LLM 同时完成 web 筛选、music/meme 关键词生成
        # - 来源动态权重系统在 LLM 调用前剔除低权重通道
        # 总计最多 1 次 LLM 调用
        # ================================================================
        
        vision_content = sources.get('vision')  # 仅保留给 Phase 2 使用，Phase 1 不处理
        music_content = sources.get('music')
        meme_content = sources.get('meme')
        logger.debug(f"[{lanlan_name}] 主动搭话-音乐内容: type={type(music_content)}, success={music_content.get('success') if music_content else 'N/A'}")
        logger.debug(f"[{lanlan_name}] 主动搭话-表情包内容: type={type(meme_content)}, success={meme_content.get('success') if meme_content else 'N/A'}")
        
        all_web_links: list[dict] = []
        
        # 收集音乐链接（在 Phase 1 Web 筛选完成后）
        # meme 也不经过 Phase 1 LLM 筛选，直接添加话题
        web_modes = [m for m in sources if m not in ('vision', 'music', 'meme')]
        
        merged_web_content = ""
        if web_modes:
            parts = []
            seen_topic_keys: set[str] = set()
            remaining_total = _PHASE1_TOTAL_TOPIC_TARGET
            for m in web_modes:
                if remaining_total <= 0:
                    break
                src = sources[m]
                label_map = PROACTIVE_SOURCE_LABELS.get(proactive_lang, PROACTIVE_SOURCE_LABELS['en'])
                label = label_map.get(m, m)
                links = src.get('links', []) or []

                selected_links: list[dict] = []
                for link in links:
                    title = link.get('title', '')
                    url = link.get('url', '')
                    key = _source_hash(url, title)
                    if key:
                        # 跨会话衰减 skip：5h 硬窗口，之后按 web 半衰期概率瞬移到下一条
                        if key in seen_topic_keys or _should_skip_source(key):
                            continue
                        seen_topic_keys.add(key)
                    # 给 link 打上来源 mode 标记，用于细粒度 channel 记录
                    if 'mode' not in link:
                        link['mode'] = m
                    selected_links.append(link)
                    if len(selected_links) >= remaining_total:
                        break

                if selected_links:
                    all_web_links.extend(selected_links)
                    remaining_total -= len(selected_links)
                    lines = []
                    for idx, item in enumerate(selected_links, start=1):
                        from utils.tokenize import truncate_to_tokens as _ttt
                        title = item.get('title', '').strip()
                        if not title:
                            continue
                        source = item.get('source', '').strip()
                        url = item.get('url', '').strip()
                        suffix = []
                        if source:
                            suffix.append(f"来源: {source}")
                        if url:
                            suffix.append(f"URL: {url}")
                        ext = (" | " + " | ".join(suffix)) if suffix else ""
                        # 单条外部内容截到 PROACTIVE_EXTERNAL_PER_ITEM_MAX_TOKENS，
                        # 防止个别 title/url 异常长撑爆 prompt。
                        item_line = _ttt(f"{idx}. {title}{ext}", PROACTIVE_EXTERNAL_PER_ITEM_MAX_TOKENS)
                        lines.append(item_line)
                    if lines:
                        parts.append(f"--- {label} ---\n" + "\n".join(lines))
                        continue

                content_text = src.get('formatted_content', '')
                if content_text:
                    compact_lines = [ln.strip() for ln in content_text.splitlines() if ln.strip()]
                    if compact_lines:
                        fallback_lines = compact_lines[:remaining_total]
                        if fallback_lines:
                            from utils.tokenize import truncate_to_tokens as _ttt
                            fallback_lines = [
                                _ttt(ln, PROACTIVE_EXTERNAL_PER_ITEM_MAX_TOKENS)
                                for ln in fallback_lines
                            ]
                            parts.append(f"--- {label} ---\n" + "\n".join(fallback_lines))
                            remaining_total -= len(fallback_lines)
            from utils.tokenize import truncate_to_tokens as _ttt
            # 兜底总和截断：防止 20 source × 200 token = 4k 超过 2k 总预算
            merged_web_content = _ttt(
                "\n\n".join(parts), PROACTIVE_EXTERNAL_TOTAL_MAX_TOKENS
            )
        
        # Phase 1 结果收集
        phase1_topics: list[tuple[str, str]] = []  # [(channel, topic_summary), ...]
        source_links: list[dict] = []  # [{"title": ..., "url": ..., "source": ...}]
        selected_web_link = None
        selected_web_topic_key = None
        selected_music_link = None
        selected_music_topic_key = None
        selected_meme_link = None
        selected_meme_topic_key = None

        # 【加固】如果正在放歌或处于冷却期，强制清空 music 通道，彻底跳过搜歌逻辑
        if is_playing_music or music_cooldown:
            if music_content:
                reason = "音乐正在播放" if is_playing_music else "用户连续秒关，音乐冷却中"
                logger.debug(f"[{lanlan_name}]-{reason}，强制屏蔽 Phase 1 搜歌逻辑")
            music_content = None
            sources.pop('music', None)

        # ============================================================
        # 来源动态权重过滤（vision / 已屏蔽的 music 不参与权重计算）
        #
        # ``reminiscence`` 作为虚拟 channel：当本轮已经从 memory_server 取到
        # pending followup topics 时，把它放进权重计算池。和 web/news/music
        # 一样按使用频率衰减——AI 连续多次"回忆"会让 reminiscence 进入
        # suppressed 集合，本轮就跳过 followup_topics_prompt（per-reflection
        # cooldown 在 reflection.py 那侧另算，这里是 channel 级别的兜底）。
        # ============================================================
        non_vision_modes = [m for m in enabled_modes if m != 'vision' and m in sources]
        weight_candidates = list(non_vision_modes)
        if _surfaced_reflection_ids:
            weight_candidates.append('reminiscence')
        if weight_candidates:
            source_weights = _compute_source_weights(lanlan_name, weight_candidates)
            suppressed = _filter_sources_by_weight(source_weights)
            weight_str = ' '.join(f"{ch}={w:.3f}" for ch, w in source_weights.items())
            logger.debug(f"[{lanlan_name}] 来源权重: {weight_str} | 剔除: {suppressed or '无'}")

            for ch in suppressed:
                sources.pop(ch, None)
            if 'music' in suppressed:
                music_content = None
            if 'meme' in suppressed:
                meme_content = None
            if 'reminiscence' in suppressed:
                # 回忆 channel 被 throttle：只清空旧 reflection。
                # 后台深话题池走独立 one-shot 触发，不在 proactive prompt 里消费。
                if followup_topics_prompt:
                    print(f"[{lanlan_name}] reminiscence channel suppressed by weight, dropping followup section")
                _followup_topics = []
                _surfaced_reflection_ids = []
                followup_topics_prompt = ""

            # 被剔除的 web 子通道不参与 merged_web_content（sources 已弹出，
            # 但 merged_web_content 已经构建完毕，需要重新构建）
            if suppressed & set(web_modes):
                # 重新构建 merged_web_content，排除被剔除的通道
                remaining_web_modes = [m for m in web_modes if m not in suppressed]
                if remaining_web_modes:
                    # 先从 all_web_links 中移除被剔除通道的链接
                    all_web_links = [lk for lk in all_web_links if lk.get('mode') not in suppressed]
                    parts = []
                    seen_topic_keys_2: set[str] = set()
                    remaining_total_2 = _PHASE1_TOTAL_TOPIC_TARGET
                    for m in remaining_web_modes:
                        if remaining_total_2 <= 0:
                            break
                        src = sources.get(m)
                        if not src:
                            continue
                        label_map = PROACTIVE_SOURCE_LABELS.get(proactive_lang, PROACTIVE_SOURCE_LABELS['en'])
                        label = label_map.get(m, m)
                        links = src.get('links', []) or []
                        selected_links_2: list[dict] = []
                        for link in links:
                            title = link.get('title', '')
                            url = link.get('url', '')
                            key = _source_hash(url, title)
                            if key:
                                if key in seen_topic_keys_2 or _should_skip_source(key):
                                    continue
                                seen_topic_keys_2.add(key)
                            if 'mode' not in link:
                                link['mode'] = m
                            selected_links_2.append(link)
                            if len(selected_links_2) >= remaining_total_2:
                                break
                        if selected_links_2:
                            remaining_total_2 -= len(selected_links_2)
                            lines = []
                            from utils.tokenize import truncate_to_tokens as _ttt2
                            for idx, item in enumerate(selected_links_2, start=1):
                                t = item.get('title', '').strip()
                                if not t:
                                    continue
                                s = item.get('source', '').strip()
                                u = item.get('url', '').strip()
                                suffix = []
                                if s:
                                    suffix.append(f"来源: {s}")
                                if u:
                                    suffix.append(f"URL: {u}")
                                ext = (" | " + " | ".join(suffix)) if suffix else ""
                                # 同上路径，单条 cap
                                lines.append(_ttt2(
                                    f"{idx}. {t}{ext}",
                                    PROACTIVE_EXTERNAL_PER_ITEM_MAX_TOKENS,
                                ))
                            if lines:
                                parts.append(f"--- {label} ---\n" + "\n".join(lines))
                    from utils.tokenize import truncate_to_tokens as _ttt3
                    merged_web_content = _ttt3(
                        "\n\n".join(parts), PROACTIVE_EXTERNAL_TOTAL_MAX_TOKENS
                    )
                else:
                    merged_web_content = ""
                    all_web_links = []

        # ============================================================
        # 合并 Phase 1 LLM 调用：web 筛选 + music 关键词 + meme 关键词
        # 一次 LLM 调用完成所有任务，降低 RPM
        # ============================================================
        has_music_task = bool(music_content and music_content.get('placeholder'))
        has_meme_task = bool(meme_content and meme_content.get('placeholder'))
        has_web_task = bool(merged_web_content)

        # 只要有至少一个任务就发起 LLM 调用
        unified_parsed: dict = {'web': None, 'music_keyword': None, 'meme_keyword': None}
        # 先定义 enriched_memory_context 保证后续引用不报 UnboundLocalError
        enriched_memory_context = memory_context
        if followup_topics_prompt:
            enriched_memory_context = memory_context + "\n" + followup_topics_prompt

        if has_web_task or has_music_task or has_meme_task:
            # Phase 1 preempt check：拨号前最后一次检查。大头 LLM 调用即将开始，
            # 此后等待期间用户抢占只能靠流结束后的兜底识别。
            if mgr.state.is_proactive_preempted():
                return await _end_proactive(JSONResponse(_proactive_preempted_json("phase1_pre_llm")))
            try:
                from config.prompts.prompts_proactive import build_unified_phase1_prompt
                unified_prompt = build_unified_phase1_prompt(
                    proactive_lang,
                    merged_content=merged_web_content if has_web_task else None,
                    memory_context=enriched_memory_context,
                    recent_chats_section=proactive_chat_history_prompt,
                    music_ctx={'lanlan_name': lanlan_name, 'master_name': master_name_current} if has_music_task else None,
                    meme_enabled=has_meme_task,
                    lanlan_name=lanlan_name,
                    master_name=master_name_current,
                )
                unified_result_text = await _llm_call_with_retry(unified_prompt, "unified_phase1")
                print(f"[{lanlan_name}] Phase 1 合并 LLM 结果: {unified_result_text[:500]}")
                unified_parsed = _parse_unified_phase1_result(unified_result_text)
                logger.debug(f"[{lanlan_name}] Phase 1 解析: web={'有' if unified_parsed.get('web') else '无'}, "
                           f"music_kw={unified_parsed.get('music_keyword', 'N/A')}, "
                           f"meme_kw={unified_parsed.get('meme_keyword', 'N/A')}")
            except Exception as e:
                logger.warning(f"[{lanlan_name}] Phase 1 合并 LLM 调用异常: {type(e).__name__}: {e}，降级处理")
                # LLM 失败：各通道降级
                unified_parsed = {'web': None, 'music_keyword': None, 'meme_keyword': None}

        # ============================================================
        # 解析 web 结果 → 链接匹配 → 去重
        # ============================================================
        web_parsed = unified_parsed.get('web')
        if web_parsed and web_parsed.get('title'):
            matched = _lookup_link_by_title(web_parsed.get('title', ''), all_web_links)
            topic_key = _source_hash(
                matched.get('url', '') if matched else '',
                web_parsed.get('title', ''),
            )
            # matched 的链接已经在 picking 阶段过了一次 _should_skip_source，
            # 这里再 roll 等于让等效 p_skip = 1-(1-p)^2，违背单次半衰期模型。
            # 仅对未匹配（LLM 幻觉的 title-only 候选）兜底再判一次。
            needs_recheck = bool(topic_key) and matched is None
            if needs_recheck and _should_skip_source(topic_key):
                print(f"[{lanlan_name}] Phase 1 title-only 话题命中衰减，跳过: {web_parsed.get('title','')[:60]}")
            else:
                if matched:
                    selected_web_link = {
                        'title': web_parsed.get('title', matched.get('title', '')),
                        'url': matched['url'],
                        'source': web_parsed.get('source', matched.get('source', '')),
                        'mode': matched.get('mode', 'web'),  # 保留细粒度 mode
                    }
                    print(f"[{lanlan_name}] Phase 1 链接预匹配成功: {matched.get('title','')[:60]}")
                else:
                    print(f"[{lanlan_name}] Phase 1 未在 web_links 中匹配到标题: {web_parsed.get('title','')[:60]}")
                # 不论 matched 与否，都把 topic_key 留下来供 Phase 2 后落盘 ——
                # 哪怕只有 title 也参与衰减历史，避免同样的标题被反复 surface
                selected_web_topic_key = topic_key
                # 用 web_parsed 的 summary 或原始文本作为 topic
                web_topic_text = web_parsed.get('summary', web_parsed.get('title', ''))
                phase1_topics.append(('web', web_topic_text.strip()))

        # ============================================================
        # 并行后置 fetch：music + meme（使用 LLM 生成的关键词）
        # ============================================================
        music_keyword = unified_parsed.get('music_keyword')
        meme_keyword = unified_parsed.get('meme_keyword')

        async def _fetch_music_with_fallback(kw: str):
            """Search music with the LLM keyword; falls back to a random recommendation on failure."""
            try:
                raw = await fetch_music_content(keyword=kw, limit=5)
                if raw and raw.get('success'):
                    return raw
            except Exception as e:
                logger.warning(f"[{lanlan_name}] 音乐关键词 '{kw}' 搜索异常: {e}")
            logger.warning(f"[{lanlan_name}] 音乐关键词 '{kw}' 搜索失败，尝试随机推荐")
            try:
                return await fetch_music_content(keyword="", limit=5)
            except Exception:
                return None

        async def _fetch_meme_with_fallback(kw: str):
            """Search memes with the LLM keyword; falls back to random hot words on failure.

            ``effective_keyword`` marks the search term actually in effect this
            time: on a keyword hit it is kw (it describes the meme content, and
            the downstream topic carries it); on the random hot-word fallback it
            is blanked, to avoid falsely claiming "this image is about X".
            """
            try:
                raw = await asyncio.wait_for(
                    fetch_meme_content(keyword=kw, limit=_PHASE1_FETCH_PER_SOURCE),
                    timeout=12.0
                )
                if raw and raw.get('success'):
                    raw['effective_keyword'] = kw
                    return raw
            except Exception as e:
                logger.warning(f"[{lanlan_name}] 表情包关键词 '{kw}' 搜索异常: {e}")
            logger.warning(f"[{lanlan_name}] 表情包关键词 '{kw}' 搜索失败，尝试随机热词")
            try:
                raw = await asyncio.wait_for(
                    fetch_meme_content(keyword="", limit=_PHASE1_FETCH_PER_SOURCE),
                    timeout=12.0
                )
                if raw:
                    raw['effective_keyword'] = ""
                return raw
            except Exception:
                return None

        fetch_tasks_p1: list = []
        fetch_labels: list[str] = []

        if has_music_task and not unified_parsed.get('music_pass'):
            kw = music_keyword or ""
            fetch_tasks_p1.append(_fetch_music_with_fallback(kw))
            fetch_labels.append('music')
        elif has_music_task:
            print(f"[{lanlan_name}] Phase 1 音乐通道明确 PASS，跳过后置 fetch")
        if has_meme_task and not unified_parsed.get('meme_pass'):
            kw = meme_keyword or ""
            fetch_tasks_p1.append(_fetch_meme_with_fallback(kw))
            fetch_labels.append('meme')
        elif has_meme_task:
            print(f"[{lanlan_name}] Phase 1 表情包通道明确 PASS，跳过后置 fetch")

        if fetch_tasks_p1:
            # Phase 1 preempt check：unified LLM 刚回，music/meme 后置 fetch 前再瞄
            if mgr.state.is_proactive_preempted():
                return await _end_proactive(JSONResponse(_proactive_preempted_json("phase1_post_llm")))
            fetch_results_p1 = await asyncio.gather(*fetch_tasks_p1, return_exceptions=True)
            for label_p1, result_p1 in zip(fetch_labels, fetch_results_p1):
                if isinstance(result_p1, Exception):
                    logger.warning(f"[{lanlan_name}] Phase 1 后置 fetch [{label_p1}] 异常: {result_p1}")
                    continue
                if label_p1 == 'music' and result_p1 and result_p1.get('success'):
                    _log_music_content(lanlan_name, result_p1)
                    music_content = {
                        'formatted_content': _format_music_content(result_p1, proactive_lang),
                        'raw_data': result_p1,
                    }
                elif label_p1 == 'meme' and result_p1 and result_p1.get('success'):
                    meme_content = {
                        'success': True,
                        'data': result_p1.get('data', []),
                        'raw_data': result_p1,
                        'source': result_p1.get('source', '表情包'),
                        'keyword': result_p1.get('effective_keyword', ''),
                    }
                    print(f"[{lanlan_name}] 成功获取 {len(result_p1.get('data', []))} 个表情包 (来源: {result_p1.get('source', '?')})")

        # ============================================================
        # 音乐话题组装（遍历候选 → 衰减 skip → 暂存链接）
        # 与 web/meme 对偶：超取 N 条后逐条概率 skip，遇命中瞬移到下一条。
        # 全部命中则清空 music_content 让通道整体降级。
        # ============================================================
        if music_content and music_content.get('formatted_content'):
            music_topic = music_content['formatted_content']
            if music_topic:
                music_tracks = music_content.get('raw_data', {}).get('data', [])
                if music_tracks:
                    picked_track: dict | None = None
                    picked_key: str = ''
                    for candidate_track in music_tracks:
                        track_url = candidate_track.get('url', '')
                        track_name = candidate_track.get('name', '')
                        track_artist = candidate_track.get('artist', '')
                        candidate_key = _source_hash(
                            track_url, f"{track_name} - {track_artist}"
                        )
                        if candidate_key and _should_skip_source(candidate_key):
                            print(f"[{lanlan_name}]- Phase 1 音乐候选去重命中，跳过: {track_name}")
                            continue
                        picked_track = candidate_track
                        picked_key = candidate_key
                        break
                    if picked_track is None:
                        print(f"[{lanlan_name}]- Phase 1 所有音乐候选均被衰减 skip，整体清空通道")
                        music_content = None
                    else:
                        # 选中非首条时，把 raw_data['data'] 砍到 picked 起始位置并重 format —
                        # 否则 music_topic 文本仍以被 skip 掉的首条为头条，与
                        # selected_music_link 的归因脱节，下游 _append_music_recommendations
                        # 也会把已 skip 的首条作为推荐项暴露给前端。
                        picked_idx = music_tracks.index(picked_track)
                        if picked_idx > 0:
                            raw = music_content.get('raw_data') or {}
                            raw_trimmed = {**raw, 'data': music_tracks[picked_idx:]}
                            new_topic = _format_music_content(raw_trimmed, proactive_lang)
                            if new_topic:
                                music_topic = new_topic
                                music_content['formatted_content'] = music_topic
                                music_content['raw_data'] = raw_trimmed
                        track_name = picked_track.get('name', '')
                        track_artist = picked_track.get('artist', '')
                        track_url = picked_track.get('url', '')
                        track_cover = picked_track.get('cover', '')
                        logger.debug(f"[{lanlan_name}]- Phase 1 音乐话题已添加 (topic_len={len(music_topic)})")
                        print(f"[{lanlan_name}]- Phase 1 音乐话题: {music_topic[:100]}")
                        selected_music_link = {
                            'title': track_name,
                            'artist': track_artist,
                            'url': track_url,
                            'cover': track_cover,
                            'source': '音乐推荐',
                            'type': 'music'
                        }
                        selected_music_topic_key = picked_key
                        phase1_topics.append(('music', music_topic))
                else:
                    # formatted_content 非空时 _format_music_content 必已输出至少一条
                    # 曲目，所以这里实际不可达；保留为防御兜底，并与上面 picked_track
                    # is None 路径对偶：没有可播曲目就不进 active_channels，守住
                    # "music ∈ active_channels ⟺ selected_music_link 非空" 这条不变量，
                    # 避免 Phase 2 出现音乐素材却无歌可投（发了 [MUSIC] 转译不出）。
                    logger.debug(f"[{lanlan_name}] Phase 1 音乐 formatted_content 非空但无曲目数据，跳过音乐通道")
                    music_content = None

        # ============================================================
        # 表情包话题组装（遍历候选 → 去重 → 限1张）
        # ============================================================
        if meme_content and meme_content.get('success') and meme_content.get('data'):
            meme_data = meme_content.get('data', [])
            if meme_data:
                for candidate_meme in meme_data:
                    meme_title = candidate_meme.get('title', '')
                    meme_url = candidate_meme.get('url', '')
                    if not meme_url:
                        continue  # 跳过无 URL 的候选
                    meme_source = candidate_meme.get('source', '表情包')
                    meme_topic_key = _source_hash(meme_url, meme_title)
                    if meme_topic_key and _should_skip_source(meme_topic_key):
                        logger.debug(f"[{lanlan_name}]- Phase 1 表情包候选去重命中，跳过: {meme_title[:30]}")
                        continue
                    if mgr.state.is_proactive_preempted():
                        return await _end_proactive(
                            JSONResponse(_proactive_preempted_json("phase1_pre_meme_moderation"))
                        )
                    moderation = await moderate_meme_image_url(meme_url, fail_closed=False)
                    if mgr.state.is_proactive_preempted():
                        return await _end_proactive(
                            JSONResponse(_proactive_preempted_json("phase1_post_meme_moderation"))
                        )
                    if not moderation.allowed:
                        logger.info(
                            "[%s]- Phase 1 meme candidate moderation blocked: reason=%s cached=%s url_hash=%s title=%s",
                            lanlan_name,
                            moderation.reason,
                            moderation.cached,
                            moderation.url_hash,
                            meme_title[:30],
                        )
                        await _record_source_used(
                            url=meme_url,
                            kind='image',
                            title=meme_title,
                        )
                        logger.info(
                            "[%s]- 已记录被 moderation 拦截的表情包 source 衰减历史: url_hash=%s",
                            lanlan_name,
                            meme_topic_key[:16],
                        )
                        continue
                    single_meme_topic = get_meme_topic_line(
                        proactive_lang,
                        keyword=meme_content.get('keyword', ''),
                        title=meme_title,
                        source=meme_source,
                    )
                    logger.debug(f"[{lanlan_name}]- Phase 1 表情包话题已添加 (限额1张): {single_meme_topic}")
                    phase1_topics.append(('meme', single_meme_topic))
                    selected_meme_link = {
                        'title': meme_title,
                        'url': meme_url,
                        'source': meme_source,
                        'type': candidate_meme.get('type', 'meme')
                    }
                    selected_meme_topic_key = meme_topic_key
                    logger.debug(f"[{lanlan_name}] 预选表情包话题: {meme_title[:30]}")
                    break
                else:
                    logger.debug(f"[{lanlan_name}]- Phase 1 未选出可用表情包候选，跳过表情包话题")
            else:
                logger.warning(f"[{lanlan_name}] Phase 1 表情包数据为空，跳过表情包话题")
        
        if not phase1_topics and not vision_content:
            if not _has_unfinished_thread:
                print(f"[{lanlan_name}] Phase 1 所有通道均无可用话题")
                return await _end_proactive(JSONResponse({
                    "success": True,
                    "action": "pass",
                    "reason_code": PROACTIVE_REASON_PASS_MODEL_PASS,
                    "message": "所有信息源筛选后均不值得搭话"
                }))
            print(f"[{lanlan_name}] Phase 1 无话题但有未收尾话题，进入 text-only 跟进 Phase 2")

        # Phase 1 preempt check：topic assembly 完，进入 Phase 2 前最后一次瞄
        if mgr.state.is_proactive_preempted():
            return await _end_proactive(JSONResponse(_proactive_preempted_json("phase1_pre_phase2")))
        
        # 收集各通道结果
        active_channels = [ch for ch, _ in phase1_topics]
        print(f"[{lanlan_name}] Phase 1 结果: phase1_topics={phase1_topics}, vision_content={'有' if vision_content else '无'}")
        web_topic = None
        music_topic = None
        for channel, topic in phase1_topics:
            if channel == 'web':
                web_topic = topic
            elif channel == 'music':
                music_topic = topic
        if vision_content:
            active_channels.append('vision')
        primary_channel = 'vision' if vision_content else (active_channels[0] if active_channels else 'unknown')
        print(f"[{lanlan_name}] Phase 1 可用通道: {active_channels}，主通道: {primary_channel}")
        
        # ================================================================
        # Phase 2: 结合人设 + 双通道信息 → 流式生成搭话
        # ⚠️ 二阶段一定要用 vision_model，在调用前使用最新截图。
        #    只有这样才能减少 vision_model 读屏幕的延迟。
        # ⚠️ 二阶段一定不要打开思考 (disable_thinking 必须为 True)，
        #    否则 vision_model + thinking 一定会超时。
        # ⚠️ 不重试、不改写。流式拦截到异常直接 abort，失败即 pass 等下一次。
        # 流程：tokens → TTS 即时生成 → 全文完成后一次性投递文本 → abort 时中断两端
        # ================================================================
        
        # 获取角色完整人设，替换模板变量
        character_prompt = lanlan_prompt_map.get(lanlan_name, '')
        if not character_prompt:
            logger.warning(f"[{lanlan_name}] 未找到角色人设，使用空字符串")
        character_prompt = character_prompt.replace('{LANLAN_NAME}', lanlan_name).replace('{MASTER_NAME}', master_name_current)
        
        # --- 向前端请求最新截图，替换 Phase 1 时拿到的旧截图 ---
        screenshot_b64_for_phase2 = ''
        if vision_content and has_vision_model:
            fresh_b64 = await mgr.request_fresh_screenshot(timeout=3.0)
            if fresh_b64:
                # 如果 request_fresh_screenshot 走了 WebSocket 路径，screenshot_response
                # 已经在 websocket_router 中更新了 mgr._avatar_position，这里用最新的位置叠加。
                # 如果走了 pyautogui 路径，overlay 已在 request_fresh_screenshot 内部完成。
                # 为安全起见：若 WS 路径返回的 fresh_b64 尚未叠加，在此补叠。
                av_pos = getattr(mgr, '_avatar_position', None) or avatar_position
                if av_pos and isinstance(av_pos, dict):
                    try:
                        from utils.screenshot_utils import overlay_avatar_annotation
                        fresh_b64 = await asyncio.to_thread(
                            overlay_avatar_annotation,
                            fresh_b64, av_pos, lanlan_name,
                            proactive_lang,
                        )
                    except Exception as ann_err:
                        logger.warning(f"[{lanlan_name}] Phase 2 avatar annotation failed: {ann_err}")
                screenshot_b64_for_phase2 = fresh_b64
                print(f"[{lanlan_name}] Phase 2 获取到最新截图 ({len(fresh_b64)//1024}KB)")
            else:
                screenshot_b64_for_phase2 = vision_content.get('screenshot_b64', '')
                if screenshot_b64_for_phase2:
                    print(f"[{lanlan_name}] Phase 2 刷新截图失败，退回使用 Phase 1 旧截图")
        
        # 构建屏幕内容段（vision 通道）
        screen_section = ""
        if screenshot_b64_for_phase2:
            sl = get_screen_section_header(master_name_current, proactive_lang)
            sf = get_screen_section_footer(master_name_current, proactive_lang)
            vision_window = vision_content.get('window_title', '') if vision_content else ''
            window_line = _loc(SCREEN_WINDOW_TITLE, proactive_lang).format(window=vision_window) if vision_window else ""
            hint = get_screen_img_hint(master_name_current, proactive_lang)
            screen_section = f"{sl}\n{window_line}{hint}\n{sf}"
            print(f"[{lanlan_name}] Phase 2 将使用 vision 模型直接看截图")
        else:
            print(f"[{lanlan_name}] Phase 2 无截图或无 vision 模型，跳过屏幕分析")
        
        # 构建网络话题段（web 通道）
        external_section = ""
        if web_topic:
            el = _loc(EXTERNAL_TOPIC_HEADER, proactive_lang)
            ef = _loc(EXTERNAL_TOPIC_FOOTER, proactive_lang)
            external_section = f"{el}\n{web_topic}\n{ef}"
        
        music_section = ""
        # gate 钉在 selected_music_link（本轮真选中、可播的曲目）而非 music_topic：
        # 保证 Phase 2 prompt 一旦出现音乐素材 / output-format 列出 [MUSIC]，下游必有
        # 歌可投递，不会"发了 [MUSIC] 却转译不出"。selected_music_link 非空时
        # music_topic 必非空（同生于 Phase 1 选曲）。正在放歌 / 冷却期时
        # music_content / selected_music_link 已在上游清空，此分支自然不命中。
        if selected_music_link and not is_playing_music and not music_cooldown:
            # 【优化】使用独立的标识符，防止模型将音乐素材误认为普通的外部 WEB 话题
            msh = _loc(MUSIC_SECTION_HEADER, proactive_lang)
            msf = _loc(MUSIC_SECTION_FOOTER, proactive_lang)
            music_section = f"{msh}\n{music_topic}\n{msf}"
        elif is_playing_music:
            print(f"[{lanlan_name}] 正在播放音乐，已屏蔽音乐推荐素材（仅保留 playing_hint）")
            music_section = ""
        
        # 构建表情包段（meme 通道）
        meme_section = ""
        meme_topic = None
        for channel, topic in phase1_topics:
            if channel == 'meme':
                meme_topic = topic
                break
        if meme_topic:
            meh = _loc(MEME_SECTION_HEADER, proactive_lang)
            mef = _loc(MEME_SECTION_FOOTER, proactive_lang)
            meme_section = f"{meh}\n{meme_topic}\n{mef}"
        
        source_instruction, output_format_section = get_proactive_format_sections(
            has_screen=bool(screen_section),
            has_web=bool(external_section),
            has_music=bool(music_section),
            has_meme=bool(meme_section),
            lang=proactive_lang,
        )
        # 本轮是否启用"来源标签系统"：有 web/music/meme 副作用通道时，
        # get_proactive_format_sections 用 _of_header（要求第一行写 [TAG]）；三者全无
        # 时用 _of_none（明确要求纯文本、无 tag，下游靠 source_tag='CHAT' 兜底投递）。
        # 无 tag gate 只在前者生效，否则会把 _of_none 模式的合法纯文本搭话误判为
        # 格式泄漏 drop（Codex P1）。
        _expects_source_tag = bool(external_section) or bool(music_section) or bool(meme_section)
        music_playing_hint = ""
        if is_playing_music and current_track:
            track_name = current_track.get('name') or get_proactive_music_unknown_track_name(proactive_lang)
            music_playing_hint = get_proactive_music_playing_hint(track_name, master_name_current, proactive_lang)

        # 把活动快照渲染成 prompt 段。snapshot 缺失时退化为空串——decision frame
        # 里的 A) 看「用户当前状态」分支会自动走到"其它状态：所有切入点都可用"。
        #
        # 重要：渲染前重拉一次 tracker enrichment 缓存（activity_scores /
        # activity_guess / open_threads）。kickoff_open_threads_compute 是在
        # Phase 1 起点 fire-and-forget 跑的，结果会在 Phase 1 进行中陆续落到
        # 缓存里——早期捕获的 activity_snapshot 看不到这些更新。专门并行起来
        # 就是为了本轮就用。决策性字段（state / propensity / propensity_reasons /
        # unfinished_thread）仍取自早期 snapshot，避免 Phase 1 中途 state 变化
        # 导致 gating 决策（restricted_screen_only 收紧 enabled_modes 等）和最终
        # prompt 不一致。
        # Freshest enrichment for the proactive prompt — Phase 1 (source fetch +
        # memory + LLM) just elapsed, so activity scores / open threads moved on.
        # Falls back to the entry snapshot if the refresh fails / is unavailable.
        # (The idle Focus decision no longer consumes a snapshot — it is a pure
        # charge cooldown — so this block only feeds the prompt now.)
        if activity_snapshot is not None:
            from dataclasses import replace as _dc_replace
            from main_logic.activity import format_activity_state_section
            try:
                fresh_enrich = await mgr._activity_tracker.get_snapshot()
                # restricted_screen_only deliberately strips semantic open_threads
                # so gaming / focused-work prompts stay screen-only — render the
                # prompt with that filtered set.
                _filtered_open_threads = _open_threads_for_activity_state(
                    activity_snapshot,
                    fresh_enrich.open_threads,
                )
                display_snap = _dc_replace(
                    activity_snapshot,
                    activity_scores=fresh_enrich.activity_scores,
                    activity_guess=fresh_enrich.activity_guess,
                    open_threads=_filtered_open_threads,
                )
            except Exception as _enrich_err:
                logger.debug(f"[{lanlan_name}] fresh enrichment fetch failed: {_enrich_err}")
                display_snap = activity_snapshot
            state_section = format_activity_state_section(display_snap, proactive_lang)
        else:
            display_snap = None
            state_section = ''

        # 静动分离：generate_prompt 作为静态 SystemMessage（可被缓存），
        # 追加的音乐/表情包指令作为动态上下文注入 HumanMessage
        # 使用 enriched_memory_context（含回忆线索）而非原始 memory_context。
        # open_threads 保持在上方 activity state section，不混进 memory_context。
        phase2_memory_context = memory_context
        if followup_topics_prompt:
            phase2_memory_context = memory_context + "\n" + followup_topics_prompt

        generate_prompt = get_proactive_generate_prompt(
            proactive_lang, music_playing_hint,
            has_music=bool(music_section), has_meme=bool(meme_section),
            master_name=master_name_current,
        ).format(
            character_prompt=character_prompt,
            inner_thoughts=inner_thoughts,
            state_section=state_section,
            memory_context=phase2_memory_context,
            recent_chats_section=proactive_chat_history_prompt,
            screen_section=screen_section,
            external_section=external_section,
            music_section=music_section,
            meme_section=meme_section,
            master_name=master_name_current,
            source_instruction=source_instruction,
            output_format_section=output_format_section,
        )
        dynamic_context_for_phase2 = ""
        # 同 music_section：[MUSIC] tag 强制指令只在真有可播曲目时注入。
        if selected_music_link:
            dynamic_context_for_phase2 += PROACTIVE_MUSIC_TAG_INSTRUCTIONS.get(
                proactive_lang,
                PROACTIVE_MUSIC_TAG_INSTRUCTIONS.get('en', PROACTIVE_MUSIC_TAG_INSTRUCTIONS['zh']),
            )
            raw_data = music_content.get('raw_data', {}) if music_content else {}
            if raw_data.get('best_match', {}).get('status') == 'fuzzy':
                dynamic_context_for_phase2 += get_proactive_music_failsafe_hint(master_name_current, proactive_lang)

        if is_playing_music:
            dynamic_context_for_phase2 += get_proactive_music_strict_constraint(proactive_lang)
        # music_cooldown 时不再注入 strict_constraint —— 此时 music 通道已被前端/后端
        # 完全剔除，不应向模型暴露任何音乐相关指令，以免干扰其他 source 的选择。
        print(f"[{lanlan_name}] Phase 2 prompt 长度: {len(generate_prompt)}, 动态上下文: {len(dynamic_context_for_phase2)} 字符")

        # Phase 1 preempt check (final)：request_fresh_screenshot 最多 await 3s，
        # 是 prepare_proactive_delivery 之前唯一剩下的可打断窗口。若此处用户已
        # 接管，继续走 prepare 会让其内部的 `current_speech_id = uuid4()` 覆盖
        # 用户轮次的 sid —— 即使 SM 的 PROACTIVE_CLAIM 在 _preempted=True 时不
        # 回写 proactive_sid，mgr.current_speech_id 已经被物理换掉，用户的
        # 回复 TTS 会被错贴上一个陌生 sid。
        if mgr.state.is_proactive_preempted():
            return await _end_proactive(JSONResponse(_proactive_preempted_json("phase1_pre_prepare")))

        # --- 前置检查：用户是否空闲、WebSocket 是否在线、session 是否可用 ---
        if not await mgr.prepare_proactive_delivery(min_idle_secs=10.0):
            return await _end_proactive(JSONResponse({
                "success": True,
                "action": "pass",
                "reason_code": PROACTIVE_REASON_PASS_ACTIVITY_BUSY,
                "message": "主动搭话条件未满足（用户近期活跃或语音会话正在进行）"
            }))

        # 记录本轮主动搭话起始的 speech_id；abort 时若该 id 已变，说明用户已打断并接管，
        # 此时再调 handle_new_message() 会把用户正常回复的 TTS 也一起清掉。
        # prepare_proactive_delivery 已经 fire(PROACTIVE_CLAIM, sid=...)；这里把
        # 状态机翻到 PHASE2，后续 astream 循环的抢占检查基于此阶段。
        proactive_sid = mgr.current_speech_id
        await mgr.state.fire(_SE.PROACTIVE_PHASE2)

        # Path B (idle) Focus 凝神：this round is now committed to speaking
        # (PHASE2 fired). Read-only: does this proactive reply run thinking-on?
        # (= the session is already in Focus, inline-driven). A proactive turn
        # never raises the charge; the charge cooldown happens after the turn in
        # _end_proactive (it needs to know whether we actually spoke). Dominates
        # all three Phase-2 generate sites below (main stream / format-fix regen
        # / BM25 anti-repeat regen).
        _focus_phase2_thinking = mgr._focus_idle_thinking()
        # Mark that this turn reached the Phase-2 idle Focus decision and pin the
        # focus state it observed (episode id + turn count) — _end_proactive
        # applies the cooldown only for such turns, and only if still in this
        # exact episode/turn (race guard: a no-op if inline moved it since).
        _focus_phase2_reached = True
        _focus_phase2_snap = mgr.state.snapshot()
        _focus_episode_token = _focus_phase2_snap.get("focus_episode_id")
        _focus_turn_token = _focus_phase2_snap.get("focus_turn_count")

        # --- 构建 LLM + messages (static/dynamic 分离) ---
        phase2_use_vision = bool(screenshot_b64_for_phase2 and has_vision_model)
        # Vision guard: a vision model + thinking reliably times out (see the
        # Phase-2 注释 above), so Focus thinking-on is suppressed whenever this
        # round feeds a screenshot. Single source of truth for all three
        # Phase-2 generate sites.
        phase2_disable_thinking = phase2_use_vision or not _focus_phase2_thinking

        begin_text = _loc(BEGIN_GENERATE, proactive_lang)
        human_text = f"{dynamic_context_for_phase2}\n\n{begin_text}" if dynamic_context_for_phase2 else begin_text
        if phase2_use_vision:
            human_content = [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{screenshot_b64_for_phase2}"}},
                {"type": "text", "text": human_text},
            ]
        else:
            human_content = human_text
        messages = [SystemMessage(content=generate_prompt), HumanMessage(content=human_content)]

        actual_model = (vision_model_name if phase2_use_vision else conversation_model)
        print(f"\n{'='*60}\n[PROACTIVE-DEBUG] Phase 2 STREAM: model={actual_model} | vision={phase2_use_vision} | img={'yes' if phase2_use_vision else 'no'}\n{'='*60}\n{generate_prompt}\n{'='*60}\n")

        # --- 流式调用 + 在线拦截 ---
        from utils.token_tracker import set_call_type
        set_call_type("proactive")
        buffer = ""
        tag_parsed = False
        source_tag = ""
        full_text = ""
        pipe_count = 0
        aborted = False
        abort_reason_code: str | None = None
        # 滚动尾部缓冲区：保留最近 5 个字符以检测跨 chunk 的 "[PASS]"（长度 6）
        pass_probe = ""
        _PASS_PROBE_LEN = 5  # len("[PASS]") - 1

        def _abort(reason_code: str) -> None:
            nonlocal aborted, abort_reason_code
            aborted = True
            # User takeover is the most important telemetry signal. If a later
            # cleanup path also notices empty/invalid output, keep the takeover
            # reason so the final pass is classified as delivery preemption.
            if (
                abort_reason_code is None
                or reason_code == PROACTIVE_REASON_DELIVERY_PREEMPTED
            ):
                abort_reason_code = reason_code

        async def _emit_safe(text: str) -> bool:
            """Send to TTS after passing the fence/length checks. Returns True when we should abort."""
            nonlocal pipe_count, full_text
            if not text:
                return False
            # 状态机 preempt check：O(1) 读 sticky flag + sid 比较。用户抢占
            # （handle_new_message 或 text stream_text 入口）会 fire USER_INPUT，
            # 在 PHASE2 阶段 sticky 把 _preempted 翻到 True；同时 current_speech_id
            # 被轮换，proactive_sid != 新 sid 兜底覆盖竞态窗口。
            # TTS 不在流式阶段输出：先缓冲全文，等相似度/数据级硬拦截都通过后
            # 再一次性 feed。否则重复文本会在 guard 命中前已经被用户听到。
            if mgr.state.is_proactive_preempted(proactive_sid):
                print(f"[{lanlan_name}] Phase 2 检测到用户接管（state 抢占），abort")
                _abort(PROACTIVE_REASON_DELIVERY_PREEMPTED)
                return True
            for ch in text:
                if ch in ('|', '｜'):
                    pipe_count += 1
                    if pipe_count >= 2:
                        print(f"[{lanlan_name}] Phase 2 fence 触发 (pipe_count={pipe_count})，abort")
                        _abort(PROACTIVE_REASON_PASS_GENERATION_EMPTY)
                        return True
            # sync count_tokens — see PHASE2_OUTPUT_MAX_TOKENS docstring
            n_tokens = count_tokens(full_text + text)
            if n_tokens > PHASE2_OUTPUT_MAX_TOKENS:
                print(f"[{lanlan_name}] Phase 2 长度超限 ({n_tokens} > {PHASE2_OUTPUT_MAX_TOKENS} tokens)，abort")
                _abort(PROACTIVE_REASON_PASS_GENERATION_EMPTY)
                return True
            full_text += text
            return False
        
        # Focus 凝神: idle/proactive counterpart to OmniOfflineClient's inline
        # stripper — text-mode Phase-2 also streams thinking-on (disable_thinking
        # False). Strip leaked <think> CoT before it reaches TTS/UI for leak-prone
        # models (qwen3.5/3.6/3.7 hybrids). Symmetric with the inline path; None
        # (no wrapping) for clean providers or thinking-off turns → zero impact.
        _p2_strip = (
            ThinkingStreamStripper()
            if (not phase2_disable_thinking) and leaks_thinking_in_content(conversation_model)
            else None
        )
        try:
            async with asyncio.timeout(25.0):
                # 使用 async with 确保 ChatOpenAI 正确关闭
                async with (await _make_llm(temperature=1.0,
                                            max_completion_tokens=PROACTIVE_PHASE2_GENERATE_MAX_TOKENS,
                                            use_vision=phase2_use_vision,
                                            disable_thinking=phase2_disable_thinking)) as llm:
                    async for chunk in llm.astream(messages):
                        # Phase 2 preempt check：每 chunk 顶端做 O(1) 状态机读，
                        # 用户抢占立刻跳出；_emit_safe 里还有一次保险。
                        if mgr.state.is_proactive_preempted(proactive_sid):
                            print(f"[{lanlan_name}] Phase 2 astream chunk 前检测到抢占，abort")
                            _abort(PROACTIVE_REASON_DELIVERY_PREEMPTED)
                            break
                        content = chunk.content if hasattr(chunk, 'content') else ''
                        if _p2_strip is not None and content:
                            # Holds CoT until the first </think>; returns "" while
                            # buffering so the skip below drops the held chunk.
                            content = _p2_strip.feed(content)
                        if not content:
                            continue

                        if not tag_parsed:
                            buffer += content
                            # 缓冲前 ~80 字符，解析 "主动搭话" 前缀和来源标签
                            if len(buffer) < 80 and '\n' not in buffer[min(len(buffer)-1, 10):]:
                                continue
                            # 清理 "主动搭话" 前缀
                            cleaned = buffer
                            m = re.search(r'主动搭话\s*\n', cleaned)
                            if m:
                                cleaned = cleaned[m.end():]
                            # 解析 [PASS] / [CHAT] / [WEB] / [MUSIC] / [MEME]
                            # 先 lstrip：模型偶尔先吐换行/空格再吐 [CHAT]，不去前导空白
                            # 会让 ^\[ 匹配失败、source_tag 误留空被当成无 tag（Codex P2）。
                            cleaned = cleaned.lstrip()
                            tag_match = re.match(r'^\[(CHAT|WEB|PASS|MUSIC|MEME)\]\s*', cleaned, re.IGNORECASE)
                            if tag_match:
                                source_tag = tag_match.group(1).upper()
                                cleaned = cleaned[tag_match.end():]
                            else:
                                cleaned, _leak_tag = _strip_proactive_screen_tag_leak(cleaned)
                                if _leak_tag:
                                    source_tag = _leak_tag
                            tag_parsed = True

                            # 模型本该输出带括号的 [PASS]，但偶尔吐裸 PASS：tag 正则
                            # 认不出 → source_tag 空、'[PASS]' 也不在 cleaned 里。再补
                            # 一道整段哨兵判定（fullmatch，方括号可选），裸 PASS 与
                            # [PASS] 一视同仁 abort；fullmatch 不会误伤正文里的 "pass"。
                            if (source_tag == 'PASS' or '[PASS]' in cleaned.upper()
                                    or _text_is_pass_sentinel(cleaned)):
                                print(f"[{lanlan_name}] Phase 2 流式检测到 PASS，abort")
                                _abort(PROACTIVE_REASON_PASS_MODEL_PASS)
                                break
                            
                            # 缓冲中剩余的文本经由 pass_probe 逻辑输出
                            if cleaned.strip():
                                combined = pass_probe + cleaned
                                if '[PASS]' in combined.upper():
                                    print(f"[{lanlan_name}] Phase 2 流式检测到 [PASS]，abort")
                                    _abort(PROACTIVE_REASON_PASS_MODEL_PASS)
                                    break
                                safe_text = combined[:-_PASS_PROBE_LEN] if len(combined) > _PASS_PROBE_LEN else ''
                                pass_probe = combined[-_PASS_PROBE_LEN:] if len(combined) >= _PASS_PROBE_LEN else combined
                                if await _emit_safe(safe_text):
                                    break
                            continue
                        
                        # --- 在线拦截: [PASS]（含跨 chunk 检测）---
                        combined = pass_probe + content
                        if '[PASS]' in combined.upper():
                            print(f"[{lanlan_name}] Phase 2 流式检测到内嵌 [PASS]，abort")
                            _abort(PROACTIVE_REASON_PASS_MODEL_PASS)
                            break
                        # 将本次 chunk 的尾部保留到 pass_probe，可安全输出的部分为去掉尾部的前段
                        safe_text = combined[:-_PASS_PROBE_LEN] if len(combined) > _PASS_PROBE_LEN else ''
                        pass_probe = combined[-_PASS_PROBE_LEN:] if len(combined) >= _PASS_PROBE_LEN else combined
                        
                        if safe_text and await _emit_safe(safe_text):
                            break
        
        except (asyncio.TimeoutError, Exception) as e:
            logger.warning(f"[{lanlan_name}] Phase 2 流式调用异常: {type(e).__name__}: {e}")
            _abort(PROACTIVE_REASON_PASS_GENERATION_EMPTY)
        
        # --- 流结束后：flush pass_probe 残留 ---
        if pass_probe and not aborted:
            if '[PASS]' in pass_probe.upper():
                _abort(PROACTIVE_REASON_PASS_MODEL_PASS)
            else:
                await _emit_safe(pass_probe)
        pass_probe = ""

        # Focus: flush the stripper's held answer. Non-empty only when no
        # </think> ever arrived (the model didn't think this turn) — which means
        # the tag was never parsed and nothing flowed through pass_probe, so feed
        # it into `buffer` and let the unparsed-buffer block below tag-parse +
        # emit it (symmetric with the inline path's prefix_buffer flush).
        if _p2_strip is not None and not aborted:
            _p2_residual = _p2_strip.flush()
            if _p2_residual:
                buffer += _p2_residual

        # --- 流结束后 buffer 未 flush 的兜底处理 ---
        if not tag_parsed and buffer and not aborted:
            cleaned = buffer
            m = re.search(r'主动搭话\s*\n', cleaned)
            if m:
                cleaned = cleaned[m.end():]
            cleaned = cleaned.lstrip()  # 同上：去前导空白再匹配 tag（Codex P2）
            tag_match = re.match(r'^\[(CHAT|WEB|PASS|MUSIC|MEME)\]\s*', cleaned, re.IGNORECASE)
            if tag_match:
                source_tag = tag_match.group(1).upper()
                cleaned = cleaned[tag_match.end():]
            else:
                cleaned, _leak_tag = _strip_proactive_screen_tag_leak(cleaned)
                if _leak_tag:
                    source_tag = _leak_tag
            # 短 bare-PASS 回复（如整段就 "PASS"，4 字 < 80 无换行）流式期一直
            # 在 buffer 里 continue、tag_parsed 始终 False，最终落到这里兜底。
            # 同样补整段哨兵判定，裸 PASS 与 [PASS] 一视同仁 abort。
            if (source_tag == 'PASS' or '[PASS]' in cleaned.upper()
                    or _text_is_pass_sentinel(cleaned)):
                _abort(PROACTIVE_REASON_PASS_MODEL_PASS)
            elif cleaned.strip():
                await _emit_safe(cleaned)
        
        # 没有解析到合法来源标签（[CHAT]/[WEB]/[MUSIC]/[MEME]）→ 输出不符合格式。
        # 弱模型（free-model）常把人设里的 Format / 约束块当正文吐出来——线上见过
        # "No Markdown: Yes."、"* No stage directions/parentheses"、"完全不同的角度或主题"
        # 这类脚手架泄漏。合法搭话必然以 tag 起头，缺 tag 即判格式泄漏，drop 整轮，
        # 不要把脚手架念给博士听。（TTS 在本函数后段才真正投递，此处 abort 安全。）
        if not aborted and full_text.strip() and not source_tag and _expects_source_tag:
            # 没解析到合法来源标签——多半是模型把人设 Format/约束块当正文吐了出来。
            # （仅在本轮启用 tag 系统时才判泄漏；_of_none 纯文本模式无 tag 是合法的，
            #  不进此分支，留给后面的 source_tag='CHAT' 兜底正常投递。）
            # 不直接 drop，先给一次"格式纠正"regen 自救：重建 Human turn（fix 指令 +
            # 原 human_text，末尾仍是 BEGIN 触发句），ainvoke 重跑一次再解析 tag。
            # 解析到合法非 PASS tag → 用自救结果接回主流程（下游 is_duplicate / BM25
            # 照常生效）；仍无 tag / [PASS] / 空 → 才判格式泄漏 drop。preempt 时放弃。
            print(f"[{lanlan_name}] Phase 2 输出无合法来源标签，尝试格式自救 regen")
            if mgr.state.is_proactive_preempted(proactive_sid):
                _abort(PROACTIVE_REASON_DELIVERY_PREEMPTED)
            else:
                _fix_human_text = f"{render_format_fix_instruction(proactive_lang, master_name_current)}\n\n{human_text}"
                if phase2_use_vision:
                    _fix_human_content = [
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{screenshot_b64_for_phase2}"}},
                        {"type": "text", "text": _fix_human_text},
                    ]
                else:
                    _fix_human_content = _fix_human_text
                _fix_text = ""
                try:
                    async with asyncio.timeout(20.0):
                        async with (await _make_llm(
                            temperature=1.0,
                            max_completion_tokens=PROACTIVE_PHASE2_GENERATE_MAX_TOKENS,
                            use_vision=phase2_use_vision,
                            disable_thinking=phase2_disable_thinking,
                        )) as _fix_llm:
                            _fix_resp = await _fix_llm.ainvoke(
                                [messages[0], HumanMessage(content=_fix_human_content)]
                            )
                            _fix_text = (_fix_resp.content if hasattr(_fix_resp, "content") else "") or ""
                except Exception as _fix_exc:
                    logger.warning("[%s] Phase 2 格式自救 regen 失败: %s", lanlan_name, _fix_exc)
                    _fix_text = ""
                _fc = (_fix_text or "").strip()
                _fm = re.search(r"主动搭话\s*\n", _fc)
                if _fm:
                    _fc = _fc[_fm.end():]
                _fc = _fc.lstrip()
                _fix_tag = ""
                _ftm = re.match(r"^\[(CHAT|WEB|PASS|MUSIC|MEME)\]\s*", _fc, re.IGNORECASE)
                if _ftm:
                    _fix_tag = _ftm.group(1).upper()
                    _fc = _fc[_ftm.end():]
                else:
                    _fc, _leak_tag = _strip_proactive_screen_tag_leak(_fc)
                    if _leak_tag:
                        _fix_tag = _leak_tag
                if _fix_tag and _fix_tag != "PASS" and _fc.strip() and "[PASS]" not in _fc.upper():
                    source_tag = _fix_tag
                    full_text = _fc.strip()
                    print(f"[{lanlan_name}] Phase 2 格式自救成功 tag={source_tag}")
                else:
                    print(f"[{lanlan_name}] Phase 2 格式自救仍无合法 tag，drop")
                    if (
                        _fix_tag == "PASS"
                        or "[PASS]" in _fc.upper()
                        or _text_is_pass_sentinel(_fc)
                    ):
                        _abort(PROACTIVE_REASON_PASS_MODEL_PASS)
                    else:
                        _abort(PROACTIVE_REASON_PASS_GENERATION_EMPTY)

        # --- 结果处理 ---
        # buffer 是流前 ~80 字符的原始累积（含 [TAG]\n 前缀和正文头部），
        # full_text 是去标签后真正投递给 TTS / send_lanlan_response 的内容。
        # 两者拼起来打印会让正文头部"复读"一遍，看着像 bug 实际不是。
        # 调试只需要 tag + 实际投递文本即可。
        print(f"\n[PROACTIVE-DEBUG] Phase 2 STREAM output (aborted={aborted}, tag={source_tag}): {full_text[:300]}\n")
        if aborted or not full_text.strip():
            final_abort_reason_code = abort_reason_code or PROACTIVE_REASON_PASS_GENERATION_EMPTY
            # 只有当用户没接管时才调 handle_new_message 清 TTS —— 否则会把
            # 用户正常回复的 TTS 也清掉（PR #862 修的 bug）。状态机的
            # is_proactive_preempted 是权威信号，sid 比较作为最后一道兜底。
            if not mgr.state.is_proactive_preempted(proactive_sid):
                await mgr.handle_new_message()
                logger.debug(f"[{lanlan_name}] Phase 2 abort，已中断 TTS + 前端音频")
            else:
                logger.info(f"[{lanlan_name}] Phase 2 abort 但用户已接管 (state preempted)，跳过 TTS 清理避免误伤正常回复")
            return await _end_proactive(JSONResponse({
                "success": True,
                "action": "pass",
                "reason_code": final_abort_reason_code,
                "message": "Phase 2 流式输出被拦截或为空"
            }))
        
        full_text, _leak_tag = _strip_proactive_screen_tag_leak(full_text)
        if _leak_tag and not source_tag:
            source_tag = _leak_tag
        response_text = full_text.strip()
        # 剥掉模型偶尔把活动状态里的「口吻 / 回忆线索」等内部引导标签当成首行小标题
        # 念出来的泄漏（前端 realistic 模式会按换行切成单独一个气泡）。必须在下方
        # 重复度 / BM25 防复读判定**之前**剥：否则被泄漏标签做前缀的复读句会因前缀
        # 稀释相似度而绕过 dedup。这些标签纯脚手架，绝不该进 TTS / 历史。
        response_text = _strip_proactive_intent_label_leak(response_text)
        # 不要把 proactive 原文写进 logger（会进日志文件 / 遥测）；只记元数据。
        # 完整原文通过 print 给开发者本地查看。
        logger.debug(f"[{lanlan_name}] Phase 2 流式完成 (vision={phase2_use_vision}, len={len(response_text)} chars)")
        print(f"\n[PROACTIVE-DEBUG] Phase 2 STREAM output: {response_text[:200]}...\n")

        # 素材推送类 channel（MUSIC/MEME）的复读按"素材本身"去重而非台词：本轮
        # 素材（曲目 / 搜索关键词）与近期不雷同时，台词级硬拦截（字面相似度 +
        # 下面的 BM25 regen/drop）一律豁免，免得模板化 intro 被误判为复读、把自
        # 发推歌/推图压到极低频。素材雷同（反复推同一曲目 / 同一关键词）才回落
        # 到正常台词判定。一次算清，下面两道门共用。
        #
        # 归类按"真实投递 channel"而非模型原始 source_tag——gate 在
        # build_proactive_response 之前，用 Phase-1 已定的 selected_*/active_channels
        # 预测最终投递（Codex P2）：
        # - music-only 且已选中曲目 → 无论模型出 [CHAT] 还是 [MUSIC]，下面的
        #   should_try_music_fallback 都会挂上曲目，本轮等于一次音乐投递，fresh
        #   曲目不该被 CHAT 文案的字面相似度 / BM25 连带 drop/regen。
        # - 模型出 [MEME] 但没选中表情包（selected_meme_link 为空）→ 最终
        #   build_proactive_response 回退 web/vision/plain、meme 没真发出，按非豁免
        #   走正常台词判定（不能凭模型 tag 就豁免）。
        _music_only_pending = (
            'music' in active_channels and selected_music_link is not None
            and not is_playing_music and not music_cooldown
            and not any(ch in ('vision', 'web', 'meme') for ch in active_channels)
        )
        if _music_only_pending and source_tag != 'MUSIC':
            _dedup_tag = 'MUSIC'
        elif source_tag == 'MEME' and selected_meme_link is None:
            _dedup_tag = 'CHAT'
        else:
            _dedup_tag = source_tag
        _material_key = _proactive_material_key(_dedup_tag, selected_music_link, meme_content)
        _exempt_text_dedup = (
            _dedup_tag in ANTI_REPEAT_EXEMPT_SOURCE_TAGS
            and not _is_recent_proactive_material(lanlan_name, _dedup_tag, _material_key)
        )
        if _exempt_text_dedup:
            logger.info(
                "[%s] proactive text-dedup exempt: tag=%s (model_tag=%s) material=%r (fresh material, skip similarity+BM25)",
                lanlan_name, _dedup_tag, source_tag, _material_key or "(none)",
            )

        is_duplicate, similarity_score = (False, 0.0)
        if not _exempt_text_dedup:
            is_duplicate, similarity_score = _is_similar_to_recent_proactive_chat(lanlan_name, response_text)
        if is_duplicate:
            logger.info(
                "[%s] proactive repeat guard blocked Phase 2 output (similarity=%.3f threshold=%.2f)",
                lanlan_name, similarity_score, _PROACTIVE_SIMILARITY_THRESHOLD,
            )
            print(
                f"[{lanlan_name}] 主动搭话重复度过高，已拦截 "
                f"(similarity={similarity_score:.3f}, threshold={_PROACTIVE_SIMILARITY_THRESHOLD:.2f})"
            )
            if not mgr.state.is_proactive_preempted(proactive_sid):
                await mgr.handle_new_message()
            else:
                logger.info("[%s] repeat guard hit but user already took over; skip TTS cleanup", lanlan_name)
            return await _end_proactive(JSONResponse({
                "success": True,
                "action": "pass",
                "reason_code": PROACTIVE_REASON_PASS_DUPLICATE,
                "message": "主动搭话重复度过高，已拦截",
                "similarity": similarity_score,
                "threshold": _PROACTIVE_SIMILARITY_THRESHOLD,
            }))

        # ── BM25 防复读硬拦截（regen / drop）─────────────────────────
        # 上面的 ``_is_similar_to_recent_proactive_chat`` 是字面相似度，只能抓
        # "几乎一字不差的复读"。BM25 走 ngram + IDF，能命中"换种说法但还在同
        # topic 上打转"——high-IDF 的 unique topic 词在最近 5 条里反复出现就
        # 触发。命中 REGEN 阈值给 LLM 一次纠正机会（ainvoke 单 shot，注入
        # avoidance 指令）；纠正后仍 >= DROP 则放弃本次投递。
        # corpus 在 ``mgr.finish_proactive_delivery`` 里写入；首次调用 / 新角色
        # 时 corpus 为空，score_draft 直接返回 0，整段无副作用。
        # 常量 + render helper 走模块顶部 import（``ANTI_REPEAT_*`` /
        # ``PROACTIVE_PHASE2_GENERATE_MAX_TOKENS`` / ``render_regen_avoid_instruction``）；
        # 这里 try 仅包 corpus 单例与评分本身——若把常量 import 也塞进 try，
        # except 后下面的 ``>= ANTI_REPEAT_DROP_THRESHOLD`` 会 NameError（codex P1）。
        # 素材推送类 channel（推歌/推图）的开场白天生模板化、台词长一个样而素材
        # （曲目 / 搜索关键词）却不同，用台词 BM25 判复读属于天生误杀（博士连点几
        # 首后 FG 窗被音乐 intro 占满，分数爆表，后续自发推歌全被 drop → "放音乐
        # 频率极低"）。本轮素材与近期不雷同时（_exempt_text_dedup，已在上方字面
        # 相似度门一并算好）跳过整段评分 + regen/drop；录入 corpus 时也豁免（见
        # finish_proactive_delivery），免得模板化 intro 污染 FG 窗。素材雷同时
        # 回落到正常台词 BM25（台词没雷同仍可发）。
        if _exempt_text_dedup:
            _bm25_total, _bm25_terms = 0.0, {}
            _ar_corpus = None
        else:
            try:
                from memory.anti_repeat import get_anti_repeat_corpus
                _ar_corpus = get_anti_repeat_corpus()
                _bm25_total, _bm25_terms = _ar_corpus.score_draft(lanlan_name, response_text)
            except Exception as _ar_exc:  # pragma: no cover - defensive
                logger.debug("[AntiRepeat] BM25 score skipped: %s", _ar_exc)
                _bm25_total, _bm25_terms = 0.0, {}
                _ar_corpus = None

        # ANTI_REPEAT_DROP_THRESHOLD 仅在 regen 之后才生效：初稿超 DROP 也得
        # 给 LLM 一次纠正机会，跑完再用同阈值二判。之前的版本初稿 ≥ DROP
        # 直接 drop 把潜在可救的输出短路掉，与设计文档"regen then drop"相违
        # （codex P2）。代价是一次 ainvoke，比静默 drop 整轮投递有价值。
        if _bm25_total >= ANTI_REPEAT_REGEN_THRESHOLD:
            # 记下进入 regen 前的初稿 source_tag，下面在改 tag 后判定是否要撤销
            # 原 music 候选状态（CodeRabbit Major：MUSIC → CHAT regen 后，若不清
            # selected_music_link / music_content，should_try_music_fallback 仍
            # 会把刚避开的复读话题对应曲目塞回 source_links）。
            _initial_source_tag = source_tag
            avoid_terms = list(_bm25_terms.keys())[:ANTI_REPEAT_INJECT_TOP_K]
            logger.info(
                "[%s] proactive BM25 regen (score=%.2f threshold=%.2f avoid=%s)",
                lanlan_name, _bm25_total, ANTI_REPEAT_REGEN_THRESHOLD, avoid_terms,
            )
            print(
                f"[{lanlan_name}] 主动搭话 BM25 触发 regen "
                f"(score={_bm25_total:.2f} >= {ANTI_REPEAT_REGEN_THRESHOLD}, 避开={avoid_terms})"
            )
            avoid_msg = render_regen_avoid_instruction(
                avoid_terms, proactive_lang, master_name_current,
            )
            # 不再把 avoid 指令作为独立的最后一条 HumanMessage 追加在 12.5k 末尾
            # （弱模型容易把这条 meta 指令的原文/脚手架当正文吐出来）。改为**重建
            # 同一个 Human turn**：avoid 约束在前，后接原始 human_text。human_text 本身
            # = dynamic_context_for_phase2 + BEGIN 触发句，所以一来保留了音乐 tag、
            # 模糊匹配披露、"正在放歌时禁止再推歌"等运行时约束（否则 regen 可能回出被
            # 禁止的内容，Codex P1 / CodeRabbit），二来它仍以 BEGIN 句结尾，模型看到的
            # 最后一句还是中性的"请开始"而非可照抄的指令文本。System 段原样复用；vision 图保留。
            regen_human_text = f"{avoid_msg}\n\n{human_text}"
            if phase2_use_vision:
                regen_human_content = [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{screenshot_b64_for_phase2}"}},
                    {"type": "text", "text": regen_human_text},
                ]
            else:
                regen_human_content = regen_human_text
            regen_messages = [messages[0], HumanMessage(content=regen_human_content)]
            regen_text = ""
            # 进入 regen 前再读一次 sticky preempt：与上方流式循环 / Phase1 各
            # 长 await 入口保持一致——用户在初稿出来到这里之间接管的话，免去
            # 一次最长 20s 的 ainvoke 白烧 token（CodeRabbit Minor）。
            if mgr.state.is_proactive_preempted(proactive_sid):
                logger.info(
                    "[%s] proactive BM25 regen aborted: user preempted before ainvoke",
                    lanlan_name,
                )
                return await _end_proactive(JSONResponse({
                    "success": True,
                    "action": "pass",
                    "reason_code": PROACTIVE_REASON_DELIVERY_PREEMPTED,
                    "message": "BM25 regen 前用户已接管",
                }))
            try:
                async with asyncio.timeout(20.0):
                    async with (await _make_llm(
                        temperature=1.0,
                        max_completion_tokens=PROACTIVE_PHASE2_GENERATE_MAX_TOKENS,
                        use_vision=phase2_use_vision,
                        disable_thinking=phase2_disable_thinking,
                    )) as _regen_llm:
                        _regen_resp = await _regen_llm.ainvoke(regen_messages)
                        regen_text = (
                            _regen_resp.content if hasattr(_regen_resp, "content") else ""
                        ) or ""
            except Exception as _regen_exc:
                logger.warning(
                    "[%s] proactive BM25 regen LLM call failed: %s",
                    lanlan_name, _regen_exc,
                )
                regen_text = ""

            # regen 输出可能仍带 "主动搭话\n[TAG]\n" 前缀；轻量剥一下。失败就
            # 用原文（mismatch 不至于致命）。
            # ⚠️ regen 用**独立**的 ``regen_source_tag`` 解析，避免沿用初稿的
            # ``source_tag``：若初稿是 [MUSIC]、regen 返回纯文本，沿用 MUSIC 会
            # 让下面的 "MUSIC→非MUSIC clear" 不触发、music 候选继续注入 → 复读
            # 又出去（CodeRabbit Major）。规则：
            #   regen 解析出 tag → 用该 tag
            #   regen 非空但没 tag → drop（与初稿同款格式泄漏防护：弱模型常把人设
            #     Format/约束块当正文吐出来，缺 tag 一律判泄漏，不再当成 CHAT 投递）
            #   regen 空 / [PASS] → 上面 drop 分支拦掉
            _cleaned = (regen_text or "").strip()
            regen_source_tag = ""
            _m = re.search(r"主动搭话\s*\n", _cleaned)
            if _m:
                _cleaned = _cleaned[_m.end():]
            _tag_m = re.match(
                r"^\[(CHAT|WEB|PASS|MUSIC|MEME)\]\s*", _cleaned, re.IGNORECASE,
            )
            if _tag_m:
                regen_source_tag = _tag_m.group(1).upper()
                _cleaned = _cleaned[_tag_m.end():]
            else:
                _cleaned, _leak_tag = _strip_proactive_screen_tag_leak(_cleaned)
                if _leak_tag:
                    regen_source_tag = _leak_tag
            # 同初稿：把泄漏的内部引导标签从 regen 产出里剥掉，且必须在下面两道
            # regen 复读复判（score_draft / 字面相似度）**之前**剥——否则带标签前缀
            # 的复读会稀释分数绕过 drop。_cleaned 在此一次性规范化，复判与投递共用。
            _cleaned = _strip_proactive_intent_label_leak(_cleaned)
            # regen 输出 [PASS] / 空 → 等价于"模型放弃了"，drop 而不是退回原文。
            # 显式把 ``regen_source_tag == 'PASS'`` 也算 drop（前面剥过 [TAG] 前缀，
            # _cleaned 已不含字面 "[PASS]"，但 regen_source_tag 记下了是 PASS）。
            # 无 tag 是否算 drop 与初稿 gate 同款守卫：仅当本轮启用 tag 系统
            # (_expects_source_tag) 时，无 tag 才判格式泄漏 drop；_of_none 纯文本模式
            # 无 tag 是合法的，留空交给下游 source_tag='CHAT' 兜底（Codex P2）。
            if (
                regen_source_tag == "PASS"
                or (_expects_source_tag and not regen_source_tag)
                or not _cleaned.strip()
                or "[PASS]" in _cleaned.upper()
            ):
                logger.info("[%s] proactive BM25 regen returned empty/PASS/untagged, drop", lanlan_name)
                if not mgr.state.is_proactive_preempted(proactive_sid):
                    await mgr.handle_new_message()
                return await _end_proactive(JSONResponse({
                    "success": True,
                    "action": "pass",
                    "reason_code": PROACTIVE_REASON_PASS_DUPLICATE,
                    "message": "BM25 regen 失败，已 drop",
                }))

            # 再 score 一次：仍 >= DROP 则真 drop
            try:
                _regen_total, _ = _ar_corpus.score_draft(lanlan_name, _cleaned)
            except Exception:
                _regen_total = 0.0
            if _regen_total >= ANTI_REPEAT_DROP_THRESHOLD:
                logger.info(
                    "[%s] proactive BM25 regen still over drop (score=%.2f)",
                    lanlan_name, _regen_total,
                )
                if not mgr.state.is_proactive_preempted(proactive_sid):
                    await mgr.handle_new_message()
                return await _end_proactive(JSONResponse({
                    "success": True,
                    "action": "pass",
                    "reason_code": PROACTIVE_REASON_PASS_DUPLICATE,
                    "message": "BM25 regen 后仍超阈值，已 drop",
                    "bm25_score": _regen_total,
                }))
            # regen 文本也跑一次字面相似度检查——BM25 抓"换种说法但同 topic"，
            # 字面相似度抓"几乎一字不差"，两条独立信号；regen 在 BM25 上过关
            # 不代表没撞上最近原话（model 偶尔会沿用语序）。CodeRabbit Major
            # 指出。
            _regen_dup, _regen_sim = _is_similar_to_recent_proactive_chat(
                lanlan_name, _cleaned,
            )
            if _regen_dup:
                logger.info(
                    "[%s] proactive BM25 regen still literal-dup (similarity=%.3f)",
                    lanlan_name, _regen_sim,
                )
                if not mgr.state.is_proactive_preempted(proactive_sid):
                    await mgr.handle_new_message()
                return await _end_proactive(JSONResponse({
                    "success": True,
                    "action": "pass",
                    "reason_code": PROACTIVE_REASON_PASS_DUPLICATE,
                    "message": "BM25 regen 后字面相似度仍超阈值，已 drop",
                    "similarity": _regen_sim,
                    "threshold": _PROACTIVE_SIMILARITY_THRESHOLD,
                }))
            # _expects_source_tag 时 regen_source_tag 必为合法非 PASS tag；_of_none
            # 模式可能为空（合法无 tag），留空交给下游 source_tag='CHAT' 兜底。
            source_tag = regen_source_tag
            # regen 后只要最终不是 MUSIC，就清掉本轮 music 候选。
            # 之前的版本只在 _initial_source_tag == "MUSIC" 时清，但 tagless
            # 初稿（_initial 为空）+ phase1 只有 music topic 的场景下，
            # should_try_music_fallback 仍会把原曲目塞回 source_links，等于
            # 把刚 regen 避开的内容又带回去（CodeRabbit Major）。
            # 仅当 regen 显式落到 MUSIC 才保留候选（initial 即 MUSIC、regen
            # 也仍选 MUSIC 的少数情形）。
            if source_tag != "MUSIC":
                if selected_music_link is not None or music_content is not None:
                    logger.info(
                        "[%s] proactive BM25 regen final tag=%s (initial=%s); cleared music candidate",
                        lanlan_name, source_tag, _initial_source_tag or "(none)",
                    )
                selected_music_link = None
                music_content = None
            # 采用 regen 文本接着走下游 source_tag / TTS 投递（_cleaned 已在上方
            # 落定时剥过泄漏标签，复读复判与投递共用同一份干净文本）。
            response_text = _cleaned
            full_text = _cleaned

        has_music_topic = 'music' in active_channels

        # 【加固】数据级锁：如果正在播放音乐，哪怕 AI 产生了音乐标签，也强制降级/忽略
        is_music_used = has_music_topic and source_tag == 'MUSIC'
        ai_wants_music = source_tag == 'MUSIC'

        if is_playing_music and ai_wants_music:
            print(f"[{lanlan_name}] 数据级锁触发：播放中尝试推荐新歌，已强制拦截并清空曲目列表")
            is_music_used = False
            music_content = None
            source_tag = 'PASS'
            aborted = True
        elif music_cooldown and ai_wants_music:
            # 冷却期：music 通道本不应出现在上下文中，但模型仍输出了 [MUSIC] 标签。
            # 降级为普通 CHAT 而非 abort 整轮搭话，避免浪费其他 source 的有效内容。
            print(f"[{lanlan_name}] 音乐冷却期模型输出 [MUSIC]，降级为 CHAT（不中止搭话）")
            is_music_used = False
            music_content = None
            source_tag = 'CHAT'
        
        # 【加固补齐】如果触发了降级拦截（aborted），立即返回
        if aborted:
            if not mgr.state.is_proactive_preempted(proactive_sid):
                await mgr.handle_new_message()
            else:
                logger.info(f"[{lanlan_name}] 降级拦截 abort 但用户已接管 (state preempted)，跳过 TTS 清理")
            return await _end_proactive(JSONResponse({
                "success": True,
                "action": "pass",
                "reason_code": PROACTIVE_REASON_PASS_MODEL_PASS,
                "message": f"[{lanlan_name}] 播放中推荐拦截触发，动作已取消"
            }))

        # _of_none output-format 路径明确指示 AI"不带 source tag"，所以 AI 真正
        # 跟进 unfinished thread 时输出可能完全没有标签。落到这里又非 abort/empty,
        # 说明 Phase 2 实际产出了文本——按 CHAT 兜底，让下游 build_proactive_response
        # 把 primary_channel 设为 'chat'，否则 mark_unfinished_thread_used 会把这一
        # 类合法跟进当作"没用 override"漏掉，2 次配额被静默绕过。
        if not source_tag and full_text.strip():
            source_tag = 'CHAT'

        # 使用纯函数构建响应
        primary_channel, source_links = build_proactive_response(source_tag, {
            'lanlan_name': lanlan_name,
            'is_music_used': is_music_used,
            'selected_web_link': selected_web_link,
            'selected_music_link': selected_music_link,
            'selected_meme_link': selected_meme_link,
            'vision_content': vision_content
        })

        # 兜底：当最终主通道已经落到 music，或当前实际上只剩音乐通道时，
        # 【逻辑加固】如果 active_channels 里包含 meme 且 primary_channel 是 meme，不触发 fallback
        should_try_music_fallback = not is_playing_music and not music_cooldown and (
            primary_channel == 'music'
            or (has_music_topic and not any(ch in ('vision', 'web', 'meme') for ch in active_channels))
        )
        if should_try_music_fallback:
            if source_links is None:
                source_links = []
            if _append_music_recommendations(source_links, music_content) > 0:
                is_music_used = True

        if is_music_used:
            # 此处不再二次调用，因为 should_try_music_fallback 已经处理了 append
            # 或者如果 is_music_used 为 True 但 haven't appended yet, do it.
            # 实际上 supports_music_fallback 已经 append 了。
            # 为了稳妥，我们只在尚未 append 时调用。
            music_already_appended = any(link.get('source') == '音乐推荐' for link in source_links)
            if not music_already_appended:
                _append_music_recommendations(source_links, music_content)

        # anti-repeat / 素材去重按"真实投递的 channel"归类，而非模型原始 source_tag
        # （此处 primary_channel 已由 build_proactive_response 按实际 source_links 定下，
        # 比 gate 的 Phase-1 预测更准）（Codex P2）：
        # - is_music_used（含模型出 [CHAT] 但 should_try_music_fallback 追加了曲目）
        #   或 primary_channel=='music' → 实际投递音乐 → MUSIC：否则模板 intro 会被
        #   按 CHAT 录进 BM25 corpus、且曲目 key 不记，重新引入 fallback 推歌污染。
        # - 仅当 primary_channel=='meme' 且确有表情包链接（selected_meme_link 非空，
        #   build_proactive_response 此时才真 append 图）才算 MEME 投递；模型出 [MEME]
        #   但选空时它已回退别的 channel（甚至 primary 仍是 'meme' 但无链接），不能按
        #   MEME 记——否则模板文案漏录 corpus，且把没发出的关键词记成已投递，害得之后
        #   同关键词的真表情包被当复读跳过。
        # - 其余落到非豁免 CHAT（WEB/vision 同样非豁免，对 anti-repeat 等价）。
        if is_music_used or primary_channel == 'music':
            _delivered_tag = 'MUSIC'
        elif primary_channel == 'meme' and selected_meme_link is not None:
            _delivered_tag = 'MEME'
        else:
            _delivered_tag = 'CHAT'
        # 曲目优先取 selected_music_link；regen 把 tag 降级 CHAT 时它已被清空，则从已
        # 追加的 source_links（source=='音乐推荐'）里取首条。
        _delivered_music_link = selected_music_link
        if _delivered_tag == 'MUSIC' and not _delivered_music_link:
            _delivered_music_link = next(
                (l for l in (source_links or []) if isinstance(l, dict) and l.get('source') == '音乐推荐'),
                None,
            )

        # 一次性投递完整文本 + 记录历史 + TTS end + turn end
        # 传 proactive_sid：若 Phase 2 流结束到这里之间用户已打断（换了 sid），
        # finish 内部会跳过所有写入，避免 proactive 文本污染用户当前轮次。
        # action_note：把"放了什么歌 / 分享了哪条内容 / 来源"作为元数据追加到
        # AIMessage 历史，否则下一轮被反问"刚才放的什么"时 LLM 完全无从作答
        # （只看得到自己说过的话，看不到自己实际投递了什么素材）。模板里对人
        # 的称呼一律用 master_name 实名展开，不写"主人"这类物化称呼。
        action_note = build_proactive_action_note(
            primary_channel=primary_channel,
            source_links=source_links,
            language=proactive_lang,
            master_name=master_name_current,
        )
        # 只要本轮后端拿到了截图、且有可用 vision 模型（phase2_use_vision 同时
        # 蕴含 screenshot_b64_for_phase2 非空），就缓存最后这张主动搭话截图，等
        # 用户下一条 text 回复时注入——不按最终投递通道筛（哪怕这轮文案落到了
        # music/web，屏幕仍是这轮看过的画面，留着供用户追问）。截图在
        # finish_proactive_delivery 内 commit 成功后才真正落 session：新一轮主动
        # 搭话产生即覆盖/清掉旧缓存（非 vision 轮传 None 清），session 侧再用 2
        # 分钟 TTL 兜底过期。
        _stage_vision_screenshot = screenshot_b64_for_phase2 if phase2_use_vision else None
        try:
            await mgr.feed_tts_chunk(response_text, expected_speech_id=proactive_sid)
            committed = await mgr.finish_proactive_delivery(
                response_text,
                expected_speech_id=proactive_sid,
                action_note=action_note,
                source_tag=_delivered_tag,
                vision_screenshot_b64=_stage_vision_screenshot,
            )
        except Exception as exc:
            logger.warning("[%s] buffered proactive delivery failed: %s", lanlan_name, exc)
            if not mgr.state.is_proactive_preempted(proactive_sid):
                await mgr.handle_new_message()
            else:
                logger.info("[%s] buffered delivery failed after user takeover; skip TTS cleanup", lanlan_name)
            return await _end_proactive(JSONResponse({
                "success": True,
                "action": "pass",
                "reason_code": PROACTIVE_REASON_DELIVERY_FAILED,
                "message": "Phase 2 buffered delivery failed",
            }))
        if not committed:
            # Proactive 内容未真正落库（用户已接管本轮），所有下游副作用必须跳过：
            # 否则 _record_proactive_chat 会把未送达内容计入去重历史、topic usage
            # 会误记已用，前端拿到 "chat" action 会以为搭话成功。
            logger.info(
                "[%s] 主动搭话被用户接管，短路下游写入（topic/memory/response）",
                lanlan_name,
            )
            return await _end_proactive(JSONResponse({
                "success": True,
                "action": "pass",
                "reason_code": PROACTIVE_REASON_DELIVERY_PREEMPTED,
                "message": "proactive delivery skipped: user took over turn",
                "lanlan_name": lanlan_name,
                "turn_id": mgr.current_speech_id,
            }))

        # 记录主动搭话
        _record_proactive_chat(lanlan_name, response_text, primary_channel)
        # 记录本轮实际投递的"素材标识"（曲目 / 搜索关键词），供下次同 channel 的
        # 素材级去重。按"真实投递 channel"_delivered_tag/_delivered_music_link 归类
        # （含模型出 CHAT 但 fallback 追加了曲目的情形），key 为空则不记录。
        _record_proactive_material(
            lanlan_name,
            _delivered_tag,
            _proactive_material_key(_delivered_tag, _delivered_music_link, meme_content),
        )
        # Mini-game 邀请冷却 counter 推进：spec 是"被回应后再 10 次搭话才解禁"，
        # 任何 channel 的成功投递都算一次，pending 期间（responded_at=None）函数
        # 内部自然 no-op，不靠"邀请自身"提前耗 counter。
        _mini_game_invite_count_post_response_chat(lanlan_name)
        # 持久化"累计成功投递的主动搭话总数"，给 force-first 用——新用户在第 N
        # 次成功投递时强制走 mini-game 邀请，跨重启计数。
        await _increment_proactive_chat_total(lanlan_name)
        # Reminiscence usage：本轮 surfaced 了 pending reflection（不管 AI 最终
        # 用了什么标签，followup 都出现在 prompt 里）→ 记一次 reminiscence 用量。
        # 用独立 buffer (_reminiscence_usage_history) 而不是把同一条 message
        # 二次写进 _proactive_chat_history——后者还驱动 _format_recent_proactive_chats
        # 和 _is_similar_to_recent_proactive_chat，二次写会让 dedup / 相似度
        # 检查把这条 proactive 跟自己撞上、虚高 score。_compute_source_weights
        # 直接读这个独立 buffer 把 reminiscence 当一档 channel 衰减。
        if _surfaced_reflection_ids:
            _record_reminiscence_usage(lanlan_name)

        # Unfinished-thread 跟进计数：仅当 AI 本轮真的产出 [CHAT]（即没有选
        # WEB/MUSIC/MEME 这类外部素材）时才 +1。早先版本是"snapshot 里有未收尾
        # 话题就计数"，理由是想防"AI 反复忽略 override 也烧光配额"——但
        # UNFINISHED_THREAD_WINDOW_SECONDS=300 的自动过期已经兜底了 thread 的总
        # 暴露时间，再多算曝光只会让两次外部素材轮把真正的续接配额提前烧光。
        # source_tag == 'CHAT' / primary_channel == 'chat' 是 build_proactive_response
        # 后唯一可靠的 "AI 走了文本路径" 信号；无 tag 但出过文本时上游会兜底
        # 设成 CHAT。[PASS] 已在 4079 早 return，不会走到这里。
        if _has_unfinished_thread and (source_tag == 'CHAT' or primary_channel == 'chat'):
            try:
                mgr._activity_tracker.mark_unfinished_thread_used()
                print(f"[{lanlan_name}] 跟进未收尾话题：mark_used")
            except Exception as _ut_err:
                logger.warning(f"[{lanlan_name}] mark_unfinished_thread_used failed: {_ut_err}")

        # 后台长期记忆维护（通过 memory_server API）：复用 internal_http_client 单例
        try:
            from utils.internal_http_client import get_internal_http_client
            _mem_base = f"http://127.0.0.1:{MEMORY_SERVER_PORT}"
            _mem_client = get_internal_http_client()
            # 保存本次搭话实际提及的 pending 反思 ID（供下次 /process 做反馈检查）
            if _surfaced_reflection_ids:
                await _mem_client.post(
                    f"{_mem_base}/record_surfaced/{lanlan_name}",
                    json={"reflection_ids": _surfaced_reflection_ids},
                    timeout=5.0,
                )
                print(f"[{lanlan_name}] 记录 surfaced 反思: {len(_surfaced_reflection_ids)} 条")

            # 记录 persona 提及次数（疲劳跟踪） — persona 文件由 memory_server 管理
            # record_mentions 已在 memory_server 的 _run_post_turn_signals 中调用
        except Exception as e:
            logger.debug(f"[{lanlan_name}] 长期记忆后处理失败（不影响主流程）: {e}")

        # 【逻辑优化】精准的话题去重记录：仅当链接真正被加入 source_links 时才记录已使用
        def _is_link_selected(selected_link):
            if not selected_link:
                return False

            target_url = (selected_link.get('url') or '').strip()
            if target_url:
                # 存在有效 URL 时，按 URL 对比
                return any((link.get('url') or '').strip() == target_url for link in source_links if link)

            # URL 为空（如音乐降级记录），按元数据签名对比
            target_sig = (
                (selected_link.get('title') or '').strip(),
                (selected_link.get('artist') or '').strip(),
                (selected_link.get('source') or '').strip(),
            )
            return any(
                (
                    (link.get('title') or '').strip(),
                    (link.get('artist') or '').strip(),
                    (link.get('source') or '').strip(),
                ) == target_sig
                for link in source_links if link
            )

        # title-only 的 web topic（LLM 在 over-fetch 列表外编出来的标题）也写入衰减历史，
        # 否则下一轮可能再次被 surface。matched 时仍按链接是否成功登卡（_is_link_selected）
        # 把关；非 matched 时绕过链接卡片检查。
        if selected_web_topic_key and (
            selected_web_link is None or _is_link_selected(selected_web_link)
        ):
            _wl = selected_web_link or {}
            _web_title_dbg = (
                _wl.get('title', '')
                or (web_parsed.get('title', '') if web_parsed else '')
            )
            await _record_source_used(
                url=_wl.get('url', '') or '',
                kind='web',
                title=_web_title_dbg,
            )
            print(f"[{lanlan_name}] 已记录 Web source 衰减历史: {selected_web_topic_key[:16]}")

        if selected_music_topic_key and (is_music_used or _is_link_selected(selected_music_link)):
            _ml = selected_music_link or {}
            _music_title_dbg = f"{_ml.get('title', '')} - {_ml.get('artist', '')}".strip(' -')
            await _record_source_used(
                url=_ml.get('url', '') or '',
                kind='music',
                title=_music_title_dbg,
            )
            print(f"[{lanlan_name}] 已记录音乐 source 衰减历史: {selected_music_topic_key[:16]}")

        if selected_meme_topic_key and _is_link_selected(selected_meme_link):
            await _record_source_used(
                url=(selected_meme_link or {}).get('url', '') or '',
                kind='image',
                title=(selected_meme_link or {}).get('title', '') or '',
            )
            print(f"[{lanlan_name}] 已记录表情包 source 衰减历史: {selected_meme_topic_key[:16]}")

        return await _end_proactive(JSONResponse({
            "success": True,
            "action": "chat",
            "reason_code": PROACTIVE_REASON_CHAT_DELIVERED,
            "message": "主动搭话已发送",
            "lanlan_name": lanlan_name,
            "source_mode": primary_channel.lower(),
            "source_tag": source_tag or "unknown",
            "active_channels": active_channels,
            "source_links": source_links,
            "turn_id": mgr.current_speech_id
        }))

    except asyncio.TimeoutError:
        logger.error("主动搭话超时")
        await _safe_fire_proactive_done(locals())
        return JSONResponse(
            _proactive_error_body(
                PROACTIVE_REASON_ERROR_TIMEOUT,
                error="AI处理超时",
            ),
            status_code=504,
        )
    except Exception as e:
        logger.error(f"主动搭话接口异常: {e}")
        await _safe_fire_proactive_done(locals())
        return JSONResponse(
            _proactive_error_body(
                PROACTIVE_REASON_ERROR_INTERNAL,
                error="服务器内部错误",
                detail=str(e),
            ),
            status_code=500,
        )





def _apply_mini_game_invite_choice(
    lanlan_name: str, choice: str, *, source: str,
) -> dict[str, Any]:
    """Handle the three-option state transition of a mini-game invite. Returns a
    structured result shared by the endpoint / keyword matcher.

    - accept: mark responded (starts the 2h+10 chats cooldown) + return game_url
    - decline: mark responded (starts the 5h+10 chats cooldown, without opening the game)
    - later (D2): reset state (delivered_at=None, restoring both force-first and
      the normal 10%) + add ``suppressed_until = now + 5min`` so the next
      proactive does not immediately roll the dice again

    state must be already-pending (delivered_at != None and responded_at is None);
    otherwise treat it as stale and return ``action='ignored'``, letting the
    caller decide whether to inform the user."""
    state = _mini_game_invite_state.get(lanlan_name)
    if not state or state.get('delivered_at') is None:
        return {'action': 'ignored', 'reason': 'no_pending_invite'}
    if state.get('responded_at') is not None:
        return {'action': 'ignored', 'reason': 'already_responded'}

    now = time.time()
    if choice == 'accept':
        state['responded_at'] = now
        state['chats_since_response'] = 0
        state['last_response_choice'] = 'accept'
        # session_id 既进 game_url query，又作为 result 顶层字段返回——keyword 路径
        # core.py 要把它放进 mini_game_launch WS payload，前端 dedupe 才能跨路径
        # 共享 key（codex P2 review 指出：缺这个 dedupe 就失效，同 invite 多路径
        # 触发会双开窗口）。
        invite_session_id = state.get('pending_session_id') or ''
        game_type = state.get('last_game_type') or 'soccer'
        launch_game_type = game_type
        game_url = _mini_game_launch_url(launch_game_type, lanlan_name, invite_session_id)
        if not game_url:
            logger.warning(
                "[%s] accept invite but no launch URL for game_type=%r; "
                "fallback /soccer_demo", lanlan_name, game_type,
            )
            launch_game_type = "soccer"
            game_url = _mini_game_launch_url(launch_game_type, lanlan_name, invite_session_id) or "/soccer_demo"
        state['last_game_type'] = game_type
        _mini_game_invite_record_response_cooldown(state, game_type, 'accept', now)
        logger.info(
            "[%s] mini-game invite accepted via %s -> %s",
            lanlan_name, source, game_url,
        )
        return {
            'action': 'open_game',
            'game_type': launch_game_type,
            'game_url': game_url,
            'session_id': invite_session_id,
        }
    if choice == 'decline':
        # 留 session_id 给 caller 推 mini_game_invite_resolved 用——所有
        # outcome 都需要前端 dismiss prompt（codex P2）。
        decline_session_id = state.get('pending_session_id') or ''
        game_type = state.get('last_game_type') or 'soccer'
        state['responded_at'] = now
        state['chats_since_response'] = 0
        state['last_response_choice'] = 'decline'
        _mini_game_invite_record_response_cooldown(state, game_type, 'decline', now)
        logger.info(
            "[%s] mini-game invite declined via %s; cooldown started",
            lanlan_name, source,
        )
        return {'action': 'cooldown', 'session_id': decline_session_id}
    if choice == 'later':
        # D2：完全 reset 但加短期 suppression。reset 之后 force-first 仍受
        # ever_delivered（持久化）压制——已经被邀请过的用户即便 state 清掉也
        # 不会被当成新用户重邀。
        later_session_id = state.get('pending_session_id') or ''
        state['delivered_at'] = None
        state['responded_at'] = None
        state['chats_since_response'] = 0
        state['pending_session_id'] = None
        state['last_response_choice'] = None
        state['suppressed_until'] = now + MINI_GAME_INVITE_LATER_SUPPRESS_SECONDS
        logger.info(
            "[%s] mini-game invite deferred via %s; suppressed for %.0fs",
            lanlan_name, source, float(MINI_GAME_INVITE_LATER_SUPPRESS_SECONDS),
        )
        return {'action': 'suppress', 'session_id': later_session_id}
    return {'action': 'ignored', 'reason': f'unknown_choice:{choice}'}


@router.post('/mini_game/invite/respond')
async def mini_game_invite_respond(request: Request):
    """Frontend button click → three-option state transition endpoint.

    Body:
        {
          "lanlan_name": str,                   // current character (frontend gets it from the host)
          "choice": "accept" | "decline" | "later",
          "session_id": str | null,             // uuid generated by the backend at delivery;
                                                // must match state.pending_session_id,
                                                // otherwise treated as stale
        }

    Response:
        - accept: ``{success, action: 'open_game', game_type, game_url}`` — on
          receipt the frontend calls ``window.open(game_url)`` so the Electron
          main process's setWindowOpenHandler intercepts it and opens a standalone window.
        - decline: ``{success, action: 'cooldown'}``
        - later: ``{success, action: 'suppress'}``
        - expired / state mismatch: ``{success: true, action: 'expired', message}`` —
          the frontend should stop showing the option buttons (the invite has expired).
    """
    payload = await _read_json_object(request)
    # 这是个本地 mutation endpoint，会改写 invite cooldown 状态——必须走和同文件
    # 其它 browser-facing mutation endpoint 一样的 CSRF / origin 校验，否则
    # 第三方页面可对 localhost:port 盲 POST 替用户 accept / decline / later 当前
    # 邀请。CodeRabbit Major review 指出。
    validation_error = _validate_local_mutation_request(request, payload=payload)
    if validation_error is not None:
        _set_no_store_headers(validation_error)
        return validation_error
    data = payload if isinstance(payload, dict) else {}
    try:
        config_manager = get_config_manager()
        _, her_name_default, _, _, _, _, _, _, _ = await config_manager.aget_character_data()
    except Exception:
        her_name_default = ''
    lanlan_name = (data.get('lanlan_name') or her_name_default or '').strip()
    if not lanlan_name:
        return JSONResponse({"success": False, "error": "lanlan_name missing"}, status_code=400)
    choice = (data.get('choice') or '').strip().lower()
    if choice not in ('accept', 'decline', 'later'):
        return JSONResponse(
            {"success": False, "error": f"choice must be accept/decline/later, got {choice!r}"},
            status_code=400,
        )
    session_id = (data.get('session_id') or '').strip()

    # session_id 强校验：必须存在 + 必须等于 state.pending_session_id；任一失败都
    # 走 expired。原版「missing → 放过去用当前 pending」会让调用方漏传 session_id
    # 时绕过 stale-session 保护——CodeRabbit Major review 指出。
    state = _mini_game_invite_state.get(lanlan_name)
    pending_sid = state.get('pending_session_id') if state else None
    if not session_id or not pending_sid or session_id != pending_sid:
        return JSONResponse({
            "success": True,
            "action": "expired",
            "message": "invite session expired or missing; a newer invite or no pending invite exists",
        })

    result = _apply_mini_game_invite_choice(lanlan_name, choice, source='button')
    if result['action'] == 'ignored':
        return JSONResponse({
            "success": True,
            "action": "expired",
            "message": result.get('reason') or 'no pending invite',
        })
    # 推一条 mini_game_invite_resolved 给所有可能在显示 prompt 的 page（pet 主窗
    # + chat.html 多窗口同时打开），让 cross-window 一致地 dismiss 选项 UI。
    # 单窗口模式只有一个监听者也无害（idempotent）。
    #
    # ⚠️ 故意不传 game_url / game_type —— button path 由触发 page（chat.html
    # 收到 HTTP 响应后）自己 window.open；如果这里 push 的 WS 也带 game_url，
    # pet 主窗（非 follower）也会 launch，多窗口下双开窗口（codex P2 指出）。
    # WS broadcast 在 button path 里只承担 cross-window dismiss prompt 职责。
    try:
        mgr = get_session_manager().get(lanlan_name)
        if mgr is not None:
            await _push_mini_game_invite_resolved(
                mgr,
                session_id=session_id,
                action=result['action'],
                # 故意不传 game_url / game_type
            )
    except Exception as exc:
        logger.warning(
            "[%s] mini_game_invite_resolved WS push (button path) failed: %s",
            lanlan_name, exc,
        )
    return JSONResponse({"success": True, **result, "lanlan_name": lanlan_name})


async def _push_mini_game_invite_resolved(
    mgr,
    *,
    session_id: str,
    action: str,
    game_url: str | None = None,
    game_type: str | None = None,
) -> None:
    """Push the WS event so the frontend dismisses the ChoicePrompt (cleared on any outcome, consistent across windows).
    On accept the payload also carries game_url; the frontend treats
    ``action=='open_game'`` as the "launch" signal for window.open.

    Replaces the original ``mini_game_launch`` event — a single WS event covers
    both lifecycle termination (always clear the prompt) + optional game launch
    (on accept). codex P2 / CodeRabbit pointed out: the original only pushed
    ``mini_game_launch`` on accept, so after a decline / later keyword hit the
    frontend prompt never disappeared even though the state had already changed."""
    if not mgr or not session_id:
        return
    payload: dict[str, Any] = {
        'type': 'mini_game_invite_resolved',
        'session_id': session_id,
        'action': action,
    }
    if game_url:
        payload['game_url'] = game_url
    if game_type:
        payload['game_type'] = game_type
    try:
        ws = getattr(mgr, 'websocket', None)
        if ws is None or not hasattr(ws, 'send_json'):
            return
        client_state = getattr(ws, 'client_state', None)
        if client_state is not None and client_state != client_state.CONNECTED:
            return
        await ws.send_json(payload)
    except Exception as exc:
        logger.warning(
            "mini_game_invite_resolved WS push failed (session=%s, action=%s): %s",
            session_id, action, exc,
        )


# ASCII / Cyrillic keyword 用 word-boundary regex 匹配；其它（CJK / Hiragana /
# Katakana / Hangul）走 substring。Python `\b` 在 \w 边界判定，但中日韩字符也
# 算 \w——同一脚本的字符之间没有 \b，硬套 word-boundary 会把"我好啊"漏掉。
# Cyrillic 同 Latin 都是 letter-only，\b 工作良好。codex P1 指出，避免 'yes'
# 命中 'yesterday'、'no' 命中 'no idea' 等英文误命中。
_LETTER_ONLY_KW_RE = re.compile(r"^[A-Za-z0-9\s'\-Ѐ-ӿ]+$")
_KEYWORD_PATTERN_CACHE: dict[str, "re.Pattern[str]"] = {}


def _keyword_matches(keyword: str, norm_text: str) -> bool:
    """Locale-aware substring/word-boundary match.

    Keywords made of ASCII / digits / Cyrillic / spaces / apostrophes / hyphens
    go through a word-boundary regex (``\\b...\\b``); other scripts (CJK /
    Hiragana / Katakana / Hangul) use substring matching — Python's regex counts
    those characters as \\w, so adding \\b would cause misses (in "我好啊" there
    is no boundary before '好')."""  # noqa: DOCSTRING_CJK
    if not keyword or not norm_text:
        return False
    if _LETTER_ONLY_KW_RE.fullmatch(keyword):
        pattern = _KEYWORD_PATTERN_CACHE.get(keyword)
        if pattern is None:
            pattern = re.compile(r'\b' + re.escape(keyword) + r'\b', re.IGNORECASE)
            _KEYWORD_PATTERN_CACHE[keyword] = pattern
        return bool(pattern.search(norm_text))
    return keyword in norm_text


def _match_mini_game_invite_keyword(text: str) -> str | None:
    """Return accept/decline/later for a user text, or None when unmatched.

    All native locale keyword lists are scanned because users may type in a
    language different from the active UI language. ASCII and Cyrillic keywords
    use word-boundary matching to avoid substring false positives; CJK keywords
    keep substring matching.

    **Priority decline > later > accept**: a sentence with an explicit negation
    must not open a game just because it also contains an accept keyword.

    Empty text and unmatched text return None.
    """
    if not text:
        return None
    norm = text.lower().strip()
    if not norm:
        return None
    hit_accept = False
    hit_decline = False
    hit_later = False
    for lang_kw in MINI_GAME_INVITE_KEYWORDS.values():
        if not hit_accept and any(_keyword_matches(kw, norm) for kw in lang_kw.get('accept', [])):
            hit_accept = True
        if not hit_later and any(_keyword_matches(kw, norm) for kw in lang_kw.get('later', [])):
            hit_later = True
        if not hit_decline and any(_keyword_matches(kw, norm) for kw in lang_kw.get('decline', [])):
            hit_decline = True
    # decline > later > accept：negation-priority。
    if hit_decline:
        return 'decline'
    if hit_later:
        return 'later'
    if hit_accept:
        return 'accept'
    return None


def _maybe_apply_mini_game_invite_keyword(
    lanlan_name: str, text: str,
) -> dict[str, Any] | None:
    """Apply mini-game invite keywords for one user-message text entry.

    Pending invites try accept, decline, and later keywords. Without a pending
    invite this helper is a no-op: ordinary chat text must not launch mini
    games implicitly. This helper does not consume the user message; normal
    chat handling should still continue.
    """
    state = _mini_game_invite_state.get(lanlan_name)
    if not state or state.get('delivered_at') is None or state.get('responded_at') is not None:
        return None
    choice = _match_mini_game_invite_keyword(text)
    if choice is None:
        return None
    result = _apply_mini_game_invite_choice(lanlan_name, choice, source='keyword')
    if result.get('action') == 'ignored':
        return None
    return result


@router.post('/proactive/music_played_through')
async def proactive_music_played_through(request: Request):
    """Record that the user finished a recommended song.

    Completed playback is strong positive feedback for the music channel, so
    matching proactive history entries are cleared from the channel-specific
    decay calculation.
    """
    validation_error = _validate_local_mutation_request(request)
    if validation_error is not None:
        return validation_error

    try:
        data = await request.json()
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    try:
        config_manager = get_config_manager()
        _, her_name_default, _, _, _, _, _, _, _ = await config_manager.aget_character_data()
    except Exception:
        her_name_default = ''
    lanlan_name = (data.get('lanlan_name') or her_name_default or '').strip()
    if not lanlan_name:
        return JSONResponse({"success": False, "error": "lanlan_name missing"}, status_code=400)
    cleared = _clear_channel_from_proactive_history(lanlan_name, 'music')
    if cleared:
        logger.info(f"[{lanlan_name}] 音乐完整播放，重置 music 通道权重衰减（清空 {cleared} 条）")
    return JSONResponse({"success": True, "cleared": cleared, "lanlan_name": lanlan_name})


@router.post('/translate')
async def translate_text_api(request: Request):
    """
    Text translation API (used by the frontend subtitle module).

    Request format:
    {
        "text": "text to translate",
        "target_lang": "target language code ('zh', 'en', 'ja', 'ko')",
        "source_lang": "source language code (optional; auto-detected when null)"
    }

    Response format:
    {
        "success": true/false,
        "translated_text": "translated text",
        "source_lang": "detected source language code",
        "target_lang": "target language code"
    }
    """
    validation_error = _validate_local_mutation_request(request)
    if validation_error is not None:
        return validation_error

    try:
        data = await request.json()
        text = data.get('text', '').strip()
        target_lang = data.get('target_lang', 'zh')
        source_lang = data.get('source_lang')
        
        if not text:
            return {
                "success": False,
                "error": "文本不能为空",
                "translated_text": "",
                "source_lang": "unknown",
                "target_lang": target_lang
            }
        
        # 归一化目标语言代码（复用公共函数）
        target_lang_normalized = normalize_language_code(target_lang, format='short')
        
        # 检测源语言（如果未提供）
        if source_lang is None:
            detected_source_lang = detect_language(text)
        else:
            # 归一化源语言代码（复用公共函数）
            detected_source_lang = normalize_language_code(source_lang, format='short')
        
        # 如果源语言和目标语言相同，不需要翻译
        if detected_source_lang == target_lang_normalized or detected_source_lang == 'unknown':
            return {
                "success": True,
                "translated_text": text,
                "source_lang": detected_source_lang,
                "target_lang": target_lang_normalized
            }
        
        # 检查是否跳过 Google 翻译（前端传递的会话级失败标记）
        skip_google = data.get('skip_google', False)
        
        # 调用翻译服务
        try:
            translated, google_failed = await translate_text(
                text, 
                target_lang_normalized, 
                detected_source_lang,
                skip_google=skip_google
            )
            return {
                "success": True,
                "translated_text": translated,
                "source_lang": detected_source_lang,
                "target_lang": target_lang_normalized,
                "google_failed": google_failed  # 告诉前端 Google 翻译是否失败
            }
        except Exception as e:
            logger.error(f"翻译失败: {e}")
            # 翻译失败时返回原文
            return {
                "success": False,
                "error": str(e),
                "translated_text": text,
                "source_lang": detected_source_lang,
                "target_lang": target_lang_normalized
            }
            
    except Exception as e:
        logger.error(f"翻译API处理失败: {e}")
        return {
            "success": False,
            "error": str(e),
            "translated_text": "",
            "source_lang": "unknown",
            "target_lang": "zh"
        }

# Self-register the mini-game-invite keyword matcher with main_logic's
# event bus. Same rationale as plugin/core/state.py: ``main_logic.core``
# previously imported this function directly (a layering inversion);
# after the inversion was removed, the only way this hook gets attached
# is via ``register_text_user_message_hook``. Registering at module import
# time keeps the path alive for any context that loads system_router
# directly (testbench, ad-hoc scripts) without going through
# ``app/runtime_bindings.py``. ``register_text_user_message_hook`` dedupes
# on identity, so the explicit wiring in ``app/runtime_bindings.py`` is a
# no-op once we've fired here.
try:
    from main_logic.agent_event_bus import register_text_user_message_hook as _register_text_hook
    _register_text_hook(_maybe_apply_mini_game_invite_keyword)
except Exception as _exc:
    # Same discriminator pattern as plugin/core/state.py: only
    # ``ModuleNotFoundError`` whose missing module IS one of the top-level
    # targets here is a legit partial-env case (and even that is rare —
    # main_logic should always be importable when system_router loads).
    # A transitive failure or a register_* regression must be logged so
    # the silent dispatcher no-op doesn't hide a real bug. Codex P2 catch.
    _expected_absent = {"main_logic", "main_logic.agent_event_bus"}
    _is_expected_absent = (
        isinstance(_exc, ModuleNotFoundError)
        and getattr(_exc, "name", None) in _expected_absent
    )
    if not _is_expected_absent:
        logger.warning(
            "system_router: failed to self-register text_user_message_hook",
            exc_info=True,
        )
