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
This is the main logic file, responsible for managing the entire conversation flow. When TTS is not selected, the Omni model's native speech output is used via the OpenAI-compatible interface.
When TTS is selected, speech is synthesized through an extra TTS API. Note that the TTS API output is streamed and must interact with user input to implement interruption logic.
The TTS part uses two queues; one would normally suffice, but Aliyun's TTS API callbacks only support synchronous functions, so a response queue was added to asynchronously send audio data to the frontend.
"""
import asyncio
import contextvars
import json
import os
import struct  # For packing audio data
import re
import time
from collections import OrderedDict, deque
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Optional
from datetime import datetime
from websockets import exceptions as web_exceptions
from fastapi import WebSocket, WebSocketDisconnect
from utils.frontend_utils import contains_chinese, replace_blank, replace_corner_mark, remove_bracket, \
    is_only_punctuation, TtsStreamNormalizer, TtsBracketStripper, TtsMarkdownStripper
from utils.screenshot_utils import process_screen_data, overlay_avatar_annotation
from main_logic.omni_realtime_client import OmniRealtimeClient
from main_logic.omni_offline_client import OmniOfflineClient, _is_safety_violation_signal
from main_logic.tts_client import (
    get_tts_worker,
    dummy_tts_worker,
    TTS_PROVIDER_REGISTRY,
    VLLM_OMNI_DEFAULT_BASE_URL,
    VLLM_OMNI_DEFAULT_MODEL,
)
from utils.gptsovits_config import is_gsv_disabled_voice_id
from main_logic.tool_calling import (
    ToolCall,
    ToolDefinition,
    ToolRegistry,
    ToolResult,
)
from utils.llm_client import AIMessage, HumanMessage
from main_logic.session_state import SessionStateMachine, SessionEvent, ProactivePhase, CognitionMode, TurnOwner
from main_logic.lifecycle_bus import LifecycleEventBus
from main_logic.proactive_delivery import (
    DELIVERY_RETRACTED_KEY,
    ProactiveDeliveryManager,
    resolve_callback_delivery_ack,
)
from main_logic.agent_event_bus import (
    dispatch_text_user_message,
    dispatch_user_utterance,
    publish_analyze_request_reliably,
    publish_voice_transcript_observed_best_effort,
)
from utils.preferences import load_global_conversation_settings, aload_global_conversation_settings
from config import (
    MEMORY_SERVER_PORT,
    TOOL_SERVER_PORT,
    SESSION_ARCHIVE_TRIGGER_TOKENS,
    SESSION_TURN_THRESHOLD,
    AVATAR_INTERACTION_DEDUPE_MAX_ITEMS,
    HIDE_DIRTY_VOICE_TRANSCRIPTS,
    ANTI_REPEAT_EXEMPT_SOURCE_TAGS,
)
# FOCUS_MODE_ENABLED is read live with a function-local ``from config import
# FOCUS_MODE_ENABLED`` at each gate (re-imported per call → picks up a runtime
# toggle / test monkeypatch), consistent with how the SM/scorer read the other
# knobs at call time. Single import style keeps the module clean.
from config.prompts.prompts_sys import (
    _loc,
    SESSION_INIT_PROMPT, SESSION_INIT_PROMPT_AGENT,
    AGENT_TASK_STATUS_RUNNING, AGENT_TASK_STATUS_QUEUED,
    AGENT_TASKS_HEADER, AGENT_TASKS_NOTICE,
    CONTEXT_SUMMARY_READY,
    SYSTEM_NOTIFICATION_TASK_ACTIVE,
    SYSTEM_NOTIFICATION_TASK_PASSIVE,
    SYSTEM_NOTIFICATION_EVENT_ACTIVE,
    SYSTEM_NOTIFICATION_EVENT_PASSIVE,
    SOURCE_DESCRIPTORS,
    TASK_STATUS_PHRASES,
    TASK_ACTION_PHRASES,
    CONTEXT_SUMMARY_TASK_HEADER, CONTEXT_SUMMARY_TASK_FOOTER,
    CONTEXT_SUMMARY_EVENT_HEADER, CONTEXT_SUMMARY_EVENT_FOOTER,
    RESULT_PARSER_PHRASES,
)
from config.prompts.prompts_memory import (
    RECALL_MEMORY_TOOL_DESCRIPTION,
    RECALL_MEMORY_TOOL_QUERY_DESCRIPTION,
    RECALL_MEMORY_TOOL_TIME_DESCRIPTION,
    RECALL_MEMORY_TOOL_NO_RESULT,
    RECALL_MEMORY_TOOL_NO_RESULT_LOOSEN,
    RECALL_MEMORY_TOOL_FILLER,
    RECALL_MEMORY_TOOL_FOUND_HEADER,
)

# Sentinel for `send_lanlan_response(request_id=...)` so we can tell apart
# "caller didn't pass it (use shared field as fallback)" from "caller
# explicitly passed None to mean 'no request id'". A normal default of
# None collapses both into the same code path and would let recovery /
# proactive paths accidentally bind their messages to a newer request_id.
_REQUEST_ID_UNSET: Any = object()
_MAGIC_COMMAND_IMAGE_DROP_REQUEST_MAX = 64
_VOICE_PROACTIVE_ACK_GRACE_S = 0.05
_TEXT_SESSION_INPUT_TYPES = frozenset({"text", "avatar_drop_image", "user_image"})
_IMAGE_INPUT_TYPES = frozenset({"screen", "camera", "avatar_drop_image", "user_image"})
_CONTEXT_APPEND_DEDUP_TTL_SECONDS = 120.0
_CONTEXT_APPEND_DEDUP_MAX_ENTRIES = 256
_CONTEXT_APPEND_READY_FLUSH_MAX_PASSES = 8
_CONTEXT_APPEND_DEFAULT_MAX_TOKENS = 1000
_CONTEXT_APPEND_SOURCE_MAX_TOKENS = {
    "game.icebreaker": 500,
    "game.scripted": 1000,
    "game.realtime_context": 1000,
    "game.postgame": 1500,
    "proactive.context": 1000,
    "proactive.callback": 1000,
    "topic.hook": 1000,
    "topic.material": 1000,
    "realtime.prime": 1000,
}
_CONTEXT_APPEND_BARE_PRIME_SOURCES = frozenset({
    "game.realtime_context",
    "game.postgame",
})

# recall 占位语音用的合成 worker-sid 后缀。仅用于在 TTS worker 层把 filler 切成
# 一段独立 utterance（见 _emit_recall_filler_tts）；``send_speech`` 在发往前端前会
# 把它剥掉、归一回本轮 turn sid。否则在「把 request-id 透传进音频事件」的 provider
# （如 minimax 的 ("__audio__", sid, ...) 路径）下，filler 音频会带着合成 sid 到前端，
# 用户打断时前端按 turn sid 匹配不到 filler chunk，barge-in 取消不掉 filler。
_RECALL_FILLER_SID_SUFFIX = "::recall-filler"


# 内部 item 渲染时的视觉标记。状态信息已在外层 SYSTEM_NOTIFICATION_TASK_ACTIVE
# 表达，emoji 仅作快速视觉识别用。
_STATUS_EMOJI = {
    "completed": "✅",
    "partial": "⚠️",
    "blocked": "⚠️",
    "failed": "❌",
    "cancelled": "🚫",
}

_VOICE_ECHO_LOOKBACK_SECONDS = 20.0
_VOICE_ECHO_LOOKBACK_CHARS = 1200
_VOICE_ECHO_MIN_NORMALIZED_CHARS = 6
_VOICE_ECHO_MIN_WINDOW_CHARS = 10
_VOICE_ECHO_SIMILARITY_THRESHOLD = 0.88
_VOICE_ECHO_NORMALIZE_RE = re.compile(r"[\W_]+", re.UNICODE)


def _normalize_voice_echo_text(text: str) -> str:
    return _VOICE_ECHO_NORMALIZE_RE.sub("", str(text or "").casefold())


def _looks_like_recent_ai_echo(transcript: str, recent_ai_text: str) -> bool:
    """Return True when STT text is probably the assistant's own recent audio.

    This intentionally requires a close text match. Voice barge-in during AI
    playback should keep flowing unless it resembles the AI text that was just
    rendered/spoken.
    """
    transcript_norm = _normalize_voice_echo_text(transcript)
    if len(transcript_norm) < _VOICE_ECHO_MIN_NORMALIZED_CHARS:
        return False
    recent_norm = _normalize_voice_echo_text(recent_ai_text)
    if len(recent_norm) < _VOICE_ECHO_MIN_NORMALIZED_CHARS:
        return False
    if len(transcript_norm) > len(recent_norm):
        return SequenceMatcher(None, transcript_norm, recent_norm).ratio() >= _VOICE_ECHO_SIMILARITY_THRESHOLD
    if len(transcript_norm) < _VOICE_ECHO_MIN_WINDOW_CHARS:
        return False
    if transcript_norm in recent_norm:
        return True

    window_len = len(transcript_norm)
    step = max(1, window_len // 3)
    best = 0.0
    last_start = len(recent_norm) - window_len
    starts = list(range(0, last_start + 1, step))
    if not starts or starts[-1] != last_start:
        starts.append(last_start)
    for start in starts:
        candidate = recent_norm[start:start + window_len]
        best = max(best, SequenceMatcher(None, transcript_norm, candidate).ratio())
        if best >= _VOICE_ECHO_SIMILARITY_THRESHOLD:
            return True
    return False


def _format_callback_source(cb: dict, lang: str) -> str:
    """Render an agent_task_callback's source as user-facing text in ``lang``.

    Reads ``cb["source_kind"]`` (one of SOURCE_DESCRIPTORS keys) and
    ``cb["source_name"]`` (free-form string used as ``{name}`` slot). Falls
    back to the ``unknown`` descriptor for missing/unrecognized kinds.
    """
    kind = (cb.get("source_kind") or "unknown").strip()
    descriptor = SOURCE_DESCRIPTORS.get(kind) or SOURCE_DESCRIPTORS["unknown"]
    name = (cb.get("source_name") or "").strip()
    return _loc(descriptor, lang).format(name=name)


def apply_role_placeholders(
    text: str,
    *,
    lanlan_name: str = "",
    master_name: str = "",
) -> str:
    """Substitute ``{MASTER_NAME}`` / ``{LANLAN_NAME}`` placeholders in
    plugin-supplied text at the LLM-injection boundary.

    Plugin authors don't know which ``LLMSessionManager`` (and therefore which
    ``master_name`` / ``lanlan_name`` pair) the text will route to — that's a
    host-side visibility decision. So the canonical contract is:

        plugin writes ``"Report to {MASTER_NAME}…"`` →
        host expands at the injection site, per session.

    Uses ``str.replace`` rather than ``str.format`` so that other braces in
    the text (JSON fragments, code snippets, user content containing stray
    ``{``) don't raise ``KeyError``. Empty names short-circuit — the
    placeholder is left in place rather than replaced with ``""``, on the
    theory that the literal token is less misleading than an empty hole.

    This is the SINGLE source of truth for the placeholder contract. New
    plugin-text injection sites should funnel through this helper.
    """
    if not text:
        return text
    if isinstance(master_name, str) and master_name:
        text = text.replace("{MASTER_NAME}", master_name)
    if isinstance(lanlan_name, str) and lanlan_name:
        text = text.replace("{LANLAN_NAME}", lanlan_name)
    return text


def _render_callback_inner_item(
    cb: dict,
    lang: str,
    *,
    lanlan_name: str = "",
    master_name: str = "",
) -> str:
    """Render one callback as a single inline string for the LLM prompt.

    Returns ``""`` when there is genuinely nothing to convey (both summary
    and detail empty); the caller can then drop the line and rely on the
    outer header alone to express that something happened.

    Plugin-supplied ``summary``/``detail`` may contain ``{MASTER_NAME}`` /
    ``{LANLAN_NAME}`` placeholders; see :func:`apply_role_placeholders`.
    """
    summary = apply_role_placeholders(
        (cb.get("summary") or "").strip(),
        lanlan_name=lanlan_name, master_name=master_name,
    )
    detail = apply_role_placeholders(
        (cb.get("detail") or "").strip(),
        lanlan_name=lanlan_name, master_name=master_name,
    )
    text = summary or detail
    if not text:
        return ""
    status = cb.get("status") or "completed"
    emoji = _STATUS_EMOJI.get(status, "•")
    line = f"{emoji} {text}"
    if summary and detail and detail != summary and len(detail) > len(summary):
        label = _loc(RESULT_PARSER_PHRASES["detail_result"], lang)
        line += f"\n{label}{detail}"
    return line


def _build_callback_instruction(
    callbacks,
    *,
    lang: str,
    lanlan_name: str,
    master_name: str,
    passive: bool = False,
) -> str:
    """Render a list of agent_task_callbacks into the LLM injection string.

    Each callback carries an ``origin`` tag stamped by the host at the
    EventBus → callback boundary:
      - ``"task_result"`` — real task completion (agent_server._emit_task_result),
        e.g. Computer Use / Browser Use / plugin entry / MCP tool result.
      - ``"event"`` — plugin push_message stream (proactive_bridge),
        e.g. danmaku / gift / external notification.

    Plugin authors cannot set ``origin``; it is derived structurally from
    which SDK method they called (``finish()`` vs ``push_message()``) by
    way of the event_type the upstream producer emitted.

    Two axes (origin × passive) pick one of four outer templates:

    +--------------+----------------------+-----------------------------+
    | origin       | active (proactive)   | passive                     |
    +==============+======================+=============================+
    | task_result  | TASK_ACTIVE          | TASK_PASSIVE                |
    |              | ("done, report it")  | ("task result")             |
    +--------------+----------------------+-----------------------------+
    | event        | EVENT_ACTIVE         | EVENT_PASSIVE               |
    |              | ("new msg, respond") | ("message")                 |
    +--------------+----------------------+-----------------------------+

    Unknown origin defaults to ``"event"`` + warning. Rationale: rather
    have the AI naturally react than fabricate "I completed a task".

    Callbacks are grouped by (passive, origin, status, source) so each
    group can pick the right outer template and (for task_result+active)
    slot in the right status/action phrases. Event templates ignore
    status/action — the concept doesn't apply to passive event streams.
    """
    if not callbacks:
        return ""
    from collections import OrderedDict

    grouped: "OrderedDict[tuple, list]" = OrderedDict()
    for cb in callbacks:
        # passive=True call = drain path; treat all as passive regardless
        # of per-callback delivery_mode.
        cb_passive = passive or (cb.get("delivery_mode") == "passive")
        origin = cb.get("origin")
        if origin not in ("task_result", "event"):
            if origin:
                logger.warning(
                    "[callback_instruction] unknown origin=%r, falling back to 'event'; "
                    "source=%s/%s",
                    origin, cb.get("source_kind"), cb.get("source_name"),
                )
            origin = "event"
        key = (
            cb_passive,
            origin,
            cb.get("status") or "completed",
            cb.get("source_kind") or "unknown",
            (cb.get("source_name") or ""),
        )
        grouped.setdefault(key, []).append(cb)

    parts: list[str] = []
    for (cb_passive, origin, status, _src_kind, _src_name), cbs in grouped.items():
        source_text = _format_callback_source(cbs[0], lang)
        if origin == "task_result":
            if cb_passive:
                header = _loc(SYSTEM_NOTIFICATION_TASK_PASSIVE, lang).format(source=source_text)
            else:
                status_phrase = _loc(
                    TASK_STATUS_PHRASES.get(status) or TASK_STATUS_PHRASES["completed"],
                    lang,
                )
                action_phrase = _loc(
                    TASK_ACTION_PHRASES.get(status) or TASK_ACTION_PHRASES["completed"],
                    lang,
                )
                header = _loc(SYSTEM_NOTIFICATION_TASK_ACTIVE, lang).format(
                    source=source_text,
                    status_phrase=status_phrase,
                    action_phrase=action_phrase,
                    name=lanlan_name,
                    master=master_name,
                )
        else:  # origin == "event"
            if cb_passive:
                header = _loc(SYSTEM_NOTIFICATION_EVENT_PASSIVE, lang).format(source=source_text)
            else:
                header = _loc(SYSTEM_NOTIFICATION_EVENT_ACTIVE, lang).format(
                    source=source_text,
                    name=lanlan_name,
                    master=master_name,
                )
        items = [
            _render_callback_inner_item(
                cb, lang, lanlan_name=lanlan_name, master_name=master_name,
            )
            for cb in cbs
        ]
        items = [s for s in items if s]
        if items:
            parts.append(header + "\n".join(items))
        else:
            # No item text — outer header alone (e.g. "task X failed") still
            # tells the AI that something happened. Strip trailing newline so
            # the joined output is clean.
            parts.append(header.rstrip())
    rendered = "\n\n".join(parts)
    # Total input budget: many callbacks accumulating must not blow up the turn.
    from utils.tokenize import truncate_to_tokens
    from config import AGENT_CALLBACK_TOTAL_MAX_TOKENS
    return truncate_to_tokens(rendered, AGENT_CALLBACK_TOTAL_MAX_TOKENS)


def _format_voice_swap_item(
    entry: dict,
    lang: str,
    *,
    lanlan_name: str = "",
    master_name: str = "",
) -> str:
    """Render a single voice-mode pending_extra_replies entry to a bulleted
    line for the hot-swap injection.

    Priority: ``summary`` → ``detail`` → synthesized "{status_phrase} from
    {source}[: error_message]" placeholder. The placeholder path matters for
    failure callbacks whose body is empty — without it, header information
    like "execution failed / from plugin X / Connection refused" would be
    silently dropped (the voice-mode equivalent of the header-only branch in
    ``_build_callback_instruction``).

    Plugin-supplied ``summary``/``detail`` may contain ``{MASTER_NAME}`` /
    ``{LANLAN_NAME}`` placeholders; see :func:`apply_role_placeholders`. The
    synthesized placeholder fallback uses host-side localized phrases so it
    needs no role substitution.

    Returns ``""`` when the entry is genuinely empty (no body, no error, and
    a benign ``completed`` status) — caller filters those out.
    """
    summary = apply_role_placeholders(
        (entry.get("summary") or "").strip(),
        lanlan_name=lanlan_name, master_name=master_name,
    )
    detail = apply_role_placeholders(
        (entry.get("detail") or "").strip(),
        lanlan_name=lanlan_name, master_name=master_name,
    )
    text = summary or detail
    status = entry.get("status") or "completed"
    emoji = _STATUS_EMOJI.get(status, "•")

    if text:
        return f"- {emoji} {text}"

    # No body text — synthesize from header info so the failure status
    # doesn't disappear silently.
    error_message = (entry.get("error_message") or "").strip()
    source_name = (entry.get("source_name") or "").strip()
    if not error_message and not source_name and status == "completed":
        # Truly nothing to convey; drop. (enqueue_agent_callback already
        # filters these out, but be defensive against legacy entries.)
        return ""

    source_text = _format_callback_source(entry, lang)
    status_phrase = _loc(
        TASK_STATUS_PHRASES.get(status) or TASK_STATUS_PHRASES["completed"],
        lang,
    )
    line = f"- {emoji} {source_text} {status_phrase}"
    if error_message:
        line += f"：{error_message}"
    return line


def _render_pending_extra_replies_by_origin(
    entries,
    *,
    lang: str,
    lanlan_name: str,
    master_name: str,
) -> str:
    """Render voice-mode ``pending_extra_replies`` into the hot-swap injection
    string, grouped by ``origin``.

    Each entry should be a structured dict with at least ``origin``;
    ``summary``/``detail``/``status``/``source_kind``/``source_name``/
    ``error_message`` are consumed by :func:`_format_voice_swap_item`. Legacy
    plain-string entries (pre-migration code paths) are tolerated and
    treated as ``origin="event"`` event-stream content — the safer default,
    since the "report the result of a previously executed task" framing on
    what may actually be a push event is the bug this refactor fixes.

    Returns a single string suitable for appending to ``final_prime_text``.
    Order: task block first (if any), then event block — matches the original
    single-block placement where everything followed the cache dump.
    """
    if not entries:
        return ""

    task_entries: list[dict] = []
    event_entries: list[dict] = []
    for entry in entries:
        if isinstance(entry, dict):
            normalized = dict(entry)
            origin = normalized.get("origin")
            if origin not in ("task_result", "event"):
                normalized["origin"] = "event"  # fail-safe
                origin = "event"
            if origin == "task_result":
                task_entries.append(normalized)
            else:
                event_entries.append(normalized)
        elif isinstance(entry, str):
            stripped = entry.strip()
            if stripped:
                event_entries.append({
                    "origin": "event",
                    "summary": stripped,
                    "detail": "",
                    "status": "completed",
                    "source_kind": "unknown",
                    "source_name": "",
                    "error_message": "",
                })

    blocks: list[str] = []
    if task_entries:
        items = [
            _format_voice_swap_item(e, lang, lanlan_name=lanlan_name, master_name=master_name)
            for e in task_entries
        ]
        items = [s for s in items if s]
        if items:
            blocks.append(
                _loc(CONTEXT_SUMMARY_TASK_HEADER, lang).format(name=lanlan_name, master=master_name)
                + "\n".join(items)
                + _loc(CONTEXT_SUMMARY_TASK_FOOTER, lang)
            )
    if event_entries:
        items = [
            _format_voice_swap_item(e, lang, lanlan_name=lanlan_name, master_name=master_name)
            for e in event_entries
        ]
        items = [s for s in items if s]
        if items:
            blocks.append(
                _loc(CONTEXT_SUMMARY_EVENT_HEADER, lang).format(name=lanlan_name, master=master_name)
                + "\n".join(items)
                + _loc(CONTEXT_SUMMARY_EVENT_FOOTER, lang)
            )
    rendered = "".join(blocks)
    # Total input budget for the voice hot-swap injection (mirror of the
    # text-mode cap in _build_callback_instruction). Backstop only — callers
    # should pre-select within budget via _select_callbacks_within_token_budget
    # so whole callbacks are never silently dropped after a successful ack.
    from utils.tokenize import truncate_to_tokens
    from config import AGENT_CALLBACK_TOTAL_MAX_TOKENS
    return truncate_to_tokens(rendered, AGENT_CALLBACK_TOTAL_MAX_TOKENS)


def _select_callbacks_within_token_budget(callbacks, total_budget):
    """Greedily take the oldest prefix of ``callbacks`` whose cumulative
    summary/detail token count stays within ``total_budget``.

    Returns ``(selected, deferred)``. Always selects at least one item so the
    queue makes forward progress (each item is already per-item capped at
    enqueue). The point: a caller that acks + clears must ack/clear only the
    *selected* items and re-queue ``deferred`` for the next turn — otherwise
    callbacks beyond the cap would be acked as delivered but never reach the
    model (see PR review)."""
    from utils.tokenize import count_tokens
    # Per-item overhead for the emoji/bullet, the per-group outer header, and the
    # template wrapper that the renderer adds around the body. Over-counting is
    # the SAFE direction: we select fewer, so the rendered instruction stays
    # under budget and the builder's backstop truncation never cuts an already
    # selected (and acked) callback.
    _ITEM_OVERHEAD_TOKENS = 48
    selected: list = []
    used = 0
    for i, cb in enumerate(callbacks):
        if isinstance(cb, dict):
            # Count every field the renderer may emit — body line (summary or
            # detail) plus the error/source fallback line — not just summary.
            t = (
                count_tokens(cb.get("summary") or "")
                + count_tokens(cb.get("detail") or "")
                + count_tokens(cb.get("error_message") or "")
                + count_tokens(cb.get("source_name") or "")
                + _ITEM_OVERHEAD_TOKENS
            )
        else:
            t = count_tokens(str(cb)) + _ITEM_OVERHEAD_TOKENS
        if selected and used + t > total_budget:
            return selected, list(callbacks[i:])
        selected.append(cb)
        used += t
    return selected, []


from config.prompts.prompts_avatar_interaction import (
    _normalize_avatar_interaction_payload,
    _build_avatar_interaction_instruction,
    _build_avatar_interaction_memory_meta,
)
# Historical imports kept here (commented) for easy rollback:
# from config import USER_PLUGIN_SERVER_PORT
# from config.prompts.prompts_sys import (
#     SESSION_INIT_PROMPT_AGENT_DYNAMIC,
#     AGENT_CAPABILITY_COMPUTER_USE, AGENT_CAPABILITY_BROWSER_USE,
#     AGENT_CAPABILITY_USER_PLUGIN_USE, AGENT_CAPABILITY_GENERIC, AGENT_CAPABILITY_SEPARATOR,
#     AGENT_PLUGINS_HEADER, AGENT_PLUGINS_COUNT,
# )
from utils.config_manager import _as_bool, get_config_manager, get_reserved
from utils.logger_config import get_module_logger
from utils.native_voice_registry import (
    is_free_preset_voice_id,
    resolve_native_voice_for_routing,
)
from utils.api_config_loader import (
    get_livestream_config,
    is_livestream_active,
)
from utils.language_utils import normalize_language_code, get_global_language, get_global_language_full, is_supported_language_code
import threading
from threading import Thread
from queue import Queue, Empty
from uuid import uuid4
import numpy as np
import soxr
import httpx

# Setup logger for this module
logger = get_module_logger(__name__, "Main")

# 用户静默达到此阈值 → 后台 loop 主动 end_session，让下一条消息触发
# start_session(new=False) 重新拉 /new_dialog 注入新鲜时间/长间隔提示/节日
# 上下文，解决长挂机 session 上下文僵化（"猫娘还停留在前一晚"）的问题。
# 周期检查间隔故意远小于阈值（粒度 ~1 min），避免静默 30:01 时还要再等
# 一整轮。
IDLE_SESSION_RESET_THRESHOLD_SECONDS = 1800
IDLE_SESSION_RESET_CHECK_INTERVAL_SECONDS = 60

# 前端文本会话 start_session 等 session_started 的硬超时（static/app-buttons.js
# 的 setTimeout(..., 15000)）。start_session 去重路径等 in-flight 启动落定后给
# 本请求补发 ack 时，等待上限绑到这个值：超过前端这个超时再补发 session_started
# 已无意义（前端早已 reject 并发 end_session），故以它为有意义窗口的天然上界。
FRONTEND_START_SESSION_TIMEOUT_SECONDS = 15.0

# 跨模式重启时「等 in-flight 落定」的等待上限。必须明显短于前端超时：等完之后
# 还要花几秒真正起目标模式会话（含最坏 ~12s 的 TTS 就绪等待），若把大半个 15s 都
# 耗在等待上，重启发出的 session_started 会晚于前端 deadline，前端照样超时、甚至
# reset 后才收到 ack 起孤儿会话（Codex P2）。取前端超时的一半，给重启留 ~7.5s 余量；
# in-flight 没在这窗口内落定就放弃（回落 baseline：前端超时、无孤儿）。in-flight
# （text）正常 1~3s 落定，远在窗口内。注：TTS 冷启动叠加 in-flight 贴线落定的双重
# 最坏情形仍可能溢出 15s，此时由 start_session 末尾的连接/放弃校验与重启侧守卫兜底。
CROSS_MODE_RESTART_WAIT_SECONDS = FRONTEND_START_SESSION_TIMEOUT_SECONDS / 2

# 主动搭话（proactive）调用 prompt_ephemeral 时设置的 sid 期望值。
# 目的：prompt_ephemeral 内部通过 on_text_delta=handle_text_data 回调 enqueue TTS，
# 中间可能被用户输入抢占（user stream_text 清 queue + 换 current_speech_id）。
# handle_text_data / handle_output_transcript 检查此 contextvar：若已设置且与
# current_speech_id 不符，说明本路径生成的 chunk 已不属于当前轮次，必须丢弃
# 以免 proactive 文本被错打上用户新 sid 混进用户回复 TTS。
# contextvar 是 per-task 隔离，不会泄漏到用户 stream_text 所在的独立任务。
_proactive_expected_sid: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    '_proactive_expected_sid', default=None,
)

# TTS 错误码：不可恢复，禁止 respawn（欠费 / API Key 无效）
NO_RETRY_TTS_CODES = {'API_ARREARS', 'API_KEY_REJECTED', 'TTS_CONFIG_INVALID'}
# TTS 错误码：立即上报前端，不受"第3次才通知"门槛限制（含配额——仍允许重试）
IMMEDIATE_REPORT_TTS_CODES = NO_RETRY_TTS_CODES | {'API_QUOTA_TIME'}


# ---------------------------------------------------------------------------
# 重要通知缓冲池
# 任何模块随时可以调用 enqueue_prominent_notice() 往池里推消息；
# 前端通过 GET /api/pending-notices 拉取（返回通知列表和游标），
# 用户全部确认后通过 POST /api/pending-notices/ack?cursor=N 只删除已展示的通知，
# 避免 peek→ack 两次 HTTP 往返之间新入队的通知被静默清空（TOCTOU）。
# ---------------------------------------------------------------------------
_prominent_notice_queue: list[dict] = []
_prominent_notice_lock = threading.Lock()
_prominent_notice_seq: int = 0  # 单调递增，每条通知入队时分配
_STATIC_LOCALES_DIR = Path(__file__).resolve().parents[1] / "static" / "locales"


@lru_cache(maxsize=16)
def _load_locale_messages(locale_code: str) -> dict:
    try:
        with (_STATIC_LOCALES_DIR / f"{locale_code}.json").open("r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _get_chat_locale_text(language: str | None, key: str, fallback: str) -> str:
    raw_lang = language or get_global_language()
    try:
        lang_full = normalize_language_code(raw_lang, format='full')
    except Exception:
        lang_full = raw_lang or 'en'
    try:
        lang_short = normalize_language_code(raw_lang, format='short')
    except Exception:
        lang_short = 'en'

    candidates: list[str] = []
    for candidate in (lang_full, lang_short, 'en', 'zh-CN'):
        if candidate and candidate not in candidates:
            candidates.append(candidate)

    for locale_code in candidates:
        cursor = _load_locale_messages(locale_code)
        for part in ('chat', key):
            if not isinstance(cursor, dict):
                cursor = None
                break
            cursor = cursor.get(part)
        if isinstance(cursor, str) and cursor.strip():
            return cursor
    return fallback


def enqueue_prominent_notice(notice: "str | dict"):
    """Put a prominent notice into the buffer pool, awaiting frontend pickup.
    
    Accepts a string (automatically wrapped as {"message": ...}) or a structured
    dict (recommended fields: "code", "message", "message_en", "details").
    """
    global _prominent_notice_seq
    if isinstance(notice, str):
        item: dict = {"message": notice}
    else:
        item = dict(notice)
    with _prominent_notice_lock:
        _prominent_notice_seq += 1
        item["_nid"] = _prominent_notice_seq
        _prominent_notice_queue.append(item)


def peek_prominent_notices() -> tuple[list[dict], int]:
    """Return a snapshot of the buffer pool and the current cursor (for GET /pending-notices).

    Returns (notices_without_internal_fields, cursor); cursor is the largest _nid in
    this snapshot, and passing it to drain_prominent_notices(cursor) deletes exactly
    the displayed items.
    """
    with _prominent_notice_lock:
        items = list(_prominent_notice_queue)
    cursor = items[-1]["_nid"] if items else 0
    public = [{k: v for k, v in it.items() if k != "_nid"} for it in items]
    return public, cursor


def drain_prominent_notices(up_to_cursor: int) -> list[dict]:
    """Delete notices with _nid <= up_to_cursor, keeping items enqueued afterwards.

    Returns the list of deleted notices. Passing 0 or a negative number deletes nothing.
    """
    if up_to_cursor <= 0:
        return []
    with _prominent_notice_lock:
        remaining = [it for it in _prominent_notice_queue if it.get("_nid", 0) > up_to_cursor]
        drained = [it for it in _prominent_notice_queue if it.get("_nid", 0) <= up_to_cursor]
        _prominent_notice_queue.clear()
        _prominent_notice_queue.extend(remaining)
    return drained


# ---------------------------------------------------------------------------
# CosyVoice 旧版音色通知去重（模块级，startup 和 LLMSessionManager 共享）
# ---------------------------------------------------------------------------
_notified_legacy_voices: set[str] = set()


def enqueue_voice_migration_notice(legacy_names: list) -> None:
    """Push the legacy CosyVoice voice notice after dedup. Called by both the main_server
    startup path and LLMSessionManager, avoiding duplicate popups for the same character."""
    global _notified_legacy_voices
    if not legacy_names:
        return
    new_names = sorted(set(legacy_names) - _notified_legacy_voices)
    if not new_names:
        return
    _notified_legacy_voices.update(new_names)
    enqueue_prominent_notice({
        "code": "notice.voiceMigration.legacyDetected",
        "message": "检测到旧版 CosyVoice 音色可能已失效，建议重新克隆语音。",
        "message_en": "Legacy CosyVoice voices detected that may no longer work. Consider re-cloning your voices.",
        "details": {"voices": new_names},
    })


# Sentinel returned by start_llm_session when CAS detects a concurrent start
# already promoted its own session.  Returning a sentinel (instead of raising)
# keeps the loser out of the generic error path — that path calls cleanup()
# without an expected_session guard and would otherwise tear down the winner's
# session/websocket while also inflating session_start_failure_count.
_START_LLM_CONCURRENT_ABORTED = object()


@dataclass(frozen=True)
class ContextAppendResult:
    appended: bool
    deduped: bool = False
    targets: tuple[str, ...] = ()
    reason: str | None = None


# --- 一个带有定期上下文压缩+在线热切换的语音会话管理器 ---
def _purge_closed_tool_calls(history: list, *, start: int = 0) -> int:
    """Remove every CLOSED tool-call pair from the conversation history: an
    assistant message (role=assistant, carrying tool_calls) plus the tool-result
    messages immediately following it whose tool_call_id matches. Any
    reasoning_content (the thinking model's chain, parked on that assistant
    message for provider replay) is dropped together with it — deleting the
    whole pair, NOT just the field, since a thinking endpoint rejects a
    tool_calls turn whose reasoning_content went missing on replay.

    Only assistant messages at index >= ``start`` are considered, so a Focus
    exit scopes the purge to the episode's history suffix (recorded when Focus
    was entered) and leaves closed tool calls from regular turns BEFORE Focus
    began intact. ``start`` is clamped to [0, len].

    "Closed" = every tool_call id on the assistant message is answered by a
    following contiguous tool message. Unclosed calls (a call with no result —
    an interrupted / in-flight turn) are kept so live state is never corrupted.
    Plain Human / AI / System (BaseMessage) entries are never touched. Returns
    the number of messages deleted.
    """
    if not history:
        return 0
    n = len(history)
    start = max(0, min(int(start or 0), n))
    remove: set[int] = set()
    for i in range(start, n):
        msg = history[i]
        if not (isinstance(msg, dict) and msg.get("role") == "assistant"):
            continue
        tool_calls = msg.get("tool_calls") or []
        if not tool_calls:
            continue
        call_ids = {tc.get("id") for tc in tool_calls if tc.get("id")}
        if not call_ids:
            continue
        # execute 路径原子追加 assistant + 紧随其后的各 tool result，所以闭合的
        # 结果消息是「连续」的；遇到非 tool 消息即停止该 assistant 的结果收集。
        result_idx: list[int] = []
        covered: set = set()
        for j in range(i + 1, n):
            rj = history[j]
            if isinstance(rj, dict) and rj.get("role") == "tool":
                result_idx.append(j)
                covered.add(rj.get("tool_call_id"))
            else:
                break
        if call_ids <= covered:  # 每个 call 都有结果 → 已闭合，整对删
            remove.add(i)
            remove.update(result_idx)
    for idx in sorted(remove, reverse=True):
        del history[idx]
    return len(remove)


class LLMSessionManager:
    def __init__(self, sync_message_queue, lanlan_name, lanlan_prompt):
        self.websocket = None
        self.sync_message_queue = sync_message_queue
        self.session = None
        self.last_time = None
        self.is_active = False
        self.active_session_is_idle = False
        self.current_expression = None
        self.tts_request_queue = Queue()  # TTS request (线程队列)
        self.tts_response_queue = Queue()  # TTS response (线程队列)
        self.tts_thread = None  # TTS线程
        self._tts_runtime_key = None
        # 跨 chunk 规范化器：Gemini Live 输出转录会在中文 token 之间插入 ASCII
        # 空格，让 MiniMax / CosyVoice 等 streaming TTS 把中文读断。normalizer
        # 按 replace_blank 的语义剔除空格，同时延后处理 chunk 尾部空格以保证边界正确。
        # 注意：仅对 http_sentence 类 TTS provider 启用（它们做客户端切句，需要干净文本）。
        # ws_bistream 类 provider（qwen / step / cosyvoice）直接把文本碎片发给服务端，
        # normalizer 的 pending_spaces 延迟投递 + CJK 边界空格删除会干扰服务端处理节奏。
        self._tts_stream_normalizer = TtsStreamNormalizer()
        self._tts_norm_speech_id: Optional[str] = None
        self._tts_normalize_enabled: bool = True  # 默认启用，_start_tts_thread 按 provider 类别覆盖
        # 括号 / markdown 剥离器：朗读时不读括号内的旁白与 markdown 标记。
        # 与 _tts_stream_normalizer 解耦——CJK 空格规范化是 provider 相关的
        # （ws_bistream provider 关），但括号/markdown 剥离是 TTS 通用需求，
        # 始终启用。两者串接顺序：normalizer → markdown → bracket，因为
        # markdown 链接 ``[文本](url)`` 必须先剥成 ``文本`` 再交给 bracket，
        # 否则 ``[`` ``]`` 会被 bracket 当成普通括号把链接文本一起吞掉。
        self._tts_markdown_stripper = TtsMarkdownStripper()
        self._tts_bracket_stripper = TtsBracketStripper()
        # 流式音频重采样器（24kHz→48kHz）- 维护内部状态避免 chunk 边界不连续
        self.audio_resampler = soxr.ResampleStream(24000, 48000, 1, dtype='float32')
        self.lock = asyncio.Lock()  # 使用异步锁替代同步锁
        self.websocket_lock = None  # websocket操作的共享锁，由main_server设置
        self._bg_tasks: set = set()  # 防止 fire-and-forget 任务被 GC 回收
        self._screenshot_future: asyncio.Future | None = None
        self._avatar_position: dict | None = None  # 前端传来的 Avatar 归一化坐标 {centerX, centerY, width, height}
        self.current_speech_id = None
        self._speech_output_total = 0  # diagnostic: chunks actually sent to frontend playback
        self._last_speech_output_time = 0.0
        self._last_speech_output_bytes = 0
        self._audio_stream_queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=300)
        self._audio_stream_worker_task: Optional[asyncio.Task] = None
        self._audio_stream_dropped_total = 0
        self._audio_stream_epoch = 0
        # 只在「用户/前端主动结束启动」时递增（end_session 的 not by_server +
        # reset_starting_count 路径），用于跨模式重启守卫区分"用户已放弃"与
        # "内部 cleanup / in-flight 启动失败"。不能复用 _audio_stream_epoch——
        # 它在所有 end_session cleanup（含 by_server=True 的 in-flight 失败收口）
        # 里都会涨，会把用户仍在等待的 audio 请求误判为已放弃（CodeRabbit）。
        self._user_session_abandon_epoch = 0
        self._last_audio_stream_backlog_log_time = 0.0
        self.emoji_pattern = re.compile(r'[^\w\u4e00-\u9fff\s>][^\w\u4e00-\u9fff\s]{2,}[^\w\u4e00-\u9fff\s<]', flags=re.UNICODE)
        self.emoji_pattern2 = re.compile("["
        u"\U0001F600-\U0001F64F"  # emoticons
        u"\U0001F300-\U0001F5FF"  # symbols & pictographs
        u"\U0001F680-\U0001F6FF"  # transport & map symbols
        u"\U0001F1E0-\U0001F1FF"  # flags (iOS)
                           "]+", flags=re.UNICODE)
        self.emotion_pattern = re.compile('<(.*?)>')

        self.lanlan_prompt = lanlan_prompt
        self.lanlan_name = lanlan_name
        # 获取角色相关配置
        self._config_manager = get_config_manager()

        (
            self.master_name,
            self.her_name,
            self.master_basic_config,
            self.lanlan_basic_config,
            self.name_mapping,
            self.lanlan_prompt_map,
            self.time_store,
            self.setting_store,
            self.recent_log
        ) = self._config_manager.get_character_data()
        # API配置现在通过 _config_manager.get_model_api_config() 动态获取
        # core_api_type 从 realtime 配置获取，支持自定义 realtime API 时自动设为 'local'
        realtime_config = self._config_manager.get_model_api_config('realtime')
        self.core_api_type = realtime_config.get('api_type', '') or self._config_manager.get_core_config().get('CORE_API_TYPE', '')
        self.memory_server_port = MEMORY_SERVER_PORT
        self.audio_api_key = self._config_manager.get_core_config()['AUDIO_API_KEY']  # 用于CosyVoice自定义音色
        self._apply_voice_id_for_route()
        # 注意：use_tts 会在 start_session 中根据 input_mode 重新设置
        self.use_tts = False
        self.generation_config = {}  # Qwen暂时不用
        self.message_cache_for_new_session = []
        self.next_session_context_messages: list[dict] = []
        self.is_preparing_new_session = False
        self.summary_triggered_time = None
        self.initial_cache_snapshot_len = 0
        self.initial_next_session_context_snapshot_len = 0
        self.pending_session_warmed_up_event = None
        self.pending_session_final_prime_complete_event = None
        self.session_start_time = None
        self._session_turn_count = 0  # 当前 session 的用户输入轮次计数
        self.pending_connector = None
        self.pending_session = None
        self.pending_use_tts = None
        self.is_hot_swap_imminent = False
        self.tts_handler_task = None
        # 热切换相关变量
        self.background_preparation_task = None
        self.final_swap_task = None
        self.receive_task = None
        self.message_handler_task = None
        # Voice-mode-only callback queue, drained on hot-swap via
        # ``_perform_final_swap_sequence`` into ``prime_context`` for the new
        # session. Each element is a dict ``{"origin": "task_result"|"event",
        # "text": str}`` — swap-time rendering groups by origin so event-stream
        # pushes (push_message) get the EVENT hot-swap wrapper, not the TASK
        # one. Kept independent from ``pending_agent_callbacks``: the two are
        # consumed at different lifecycle points (text mode = next stream_text,
        # voice mode = next hot-swap) and must not share state.
        self.pending_extra_replies: list[dict] = []
        # 结构化 agent 任务回调队列（用于按会话类型注入）
        self.pending_agent_callbacks: list[dict] = []
        # ── Proactive delivery front stage ───────────────────────────────
        # Generic, plugin-agnostic pacing/ordering for proactive cues
        # (push_message ai_behavior="respond" + agent task results). The
        # manager OWNS waiting cues and decides which/when to hand one off
        # into enqueue_agent_callback + trigger_agent_callbacks below; it
        # does not replace pending_agent_callbacks (which stays the
        # race-tested delivery buffer). ``_voice_playback_active`` is set by
        # the FRONTEND-reported voice_play_start/end signals so the voice
        # inject gate keys off ACTUAL audio playback completion rather than
        # the realtime API's response.done (generation, not playback).
        self.lifecycle_bus = LifecycleEventBus(name=self.lanlan_name)
        self._voice_playback_active = False
        # When playback started (monotonic). Used to time-bound the gate so a
        # missing voice_play_end (frontend disconnect/refresh mid-playback)
        # can't wedge proactive delivery forever — see _is_voice_playing().
        self._voice_playback_started_ts = 0.0
        self.proactive_manager = ProactiveDeliveryManager(
            deliver=self._deliver_proactive_batch,
            name=self.lanlan_name,
            can_release=self._can_release_proactive,
        )
        self.lifecycle_bus.subscribe("voice_play_start", self.proactive_manager.on_playback_start)
        self.lifecycle_bus.subscribe("voice_play_end", self.proactive_manager.on_playback_end)
        self.lifecycle_bus.subscribe("text_start", self.proactive_manager.on_text_start)
        self.lifecycle_bus.subscribe("text_end", self.proactive_manager.on_text_end)
        # 防止 trigger_agent_callbacks 和 finish_proactive_delivery 并发写 WS/sync_message_queue
        self._proactive_write_lock = asyncio.Lock()
        # Serializes the voice-mode proactive inject path. trigger_agent_callbacks
        # is fired via asyncio.create_task from multiple sites (EventBus per-
        # callback scheduling, _finalize_turn_after_emit, start_session), so two
        # tasks can race: both pass the (phase / is_active_response) gate before
        # either sends, then both inject the SAME snapshot → duplicate
        # conversation.item.create and a response_already_active on the second
        # response.create. The voice branch holds this lock across
        # gate-check → render → inject → prune and re-filters the queue inside,
        # making check-and-claim atomic. (Text mode uses the SM's
        # try_start_proactive claim instead; voice deliberately bypasses the SM.)
        self._voice_proactive_inject_lock = asyncio.Lock()
        # 请她离开/变猫期间的后端静默闸门。前端会在进入猫态时置 True，
        # 回来或显式 start_session 时清掉；所有主动搭话入口统一读取它。
        self.goodbye_silent: bool = False
        self.goodbye_silent_reason: str = ""
        self.goodbye_silent_updated_at: float = 0.0
        # ── Session takeover ──────────────────────────────────────────
        # 当某个外部 controller 接管这个 session 时，本地 chat LLM 的输出
        # （text/audio delta、output transcript、response.complete、
        # new-message 通知）都要静音；语音转写也要先丢给外部 dispatcher
        # 处理，处理过的不再走本地 chat 路径。
        # SessionManager 不知道 takeover 是谁、为什么——只认这两个 flag。
        # 当前唯一消费者：main_routers.game_router；未来 plugin/agent 想完
        # 全接管 chat 的场景也走同一套接口。
        self._takeover_active: bool = False
        self._takeover_input_dispatcher: Optional[
            Callable[..., Awaitable[bool]]
        ] = None
        # 由前端控制的Agent相关开关
        self.agent_flags = {
            'agent_enabled': False,
            'computer_use_enabled': False,
            'browser_use_enabled': False,
            'user_plugin_enabled': False,
            'openclaw_enabled': False,
            'openclaw_ready': False,
            'openfang_enabled': False,
        }
        
        # 模式标志: 'audio' 或 'text'
        self.input_mode = 'audio'
        
        # 初始化时创建audio模式的session（默认）
        self.session = None
        
        # 防止无限重试的保护机制
        self.session_start_failure_count = 0
        self.session_start_last_failure_time = None
        self.session_start_cooldown_seconds = 3.0  # 冷却时间：3秒
        self.session_start_max_failures = 3  # 最大连续失败次数
        # 熔断：达到 max_failures 后必须等用户显式触发 start_session（刷新页面/点重试）
        # 才会清。中间任何内部 recovery 路径都被早退拦截，避免日志被刷屏。
        self._session_start_circuit_open = False
        self._memory_error_retry_after = 0  # Memory Server 专属冷却时间戳
        self._memory_error_cooldown_seconds = 10  # Memory Server 冷却时间
        
        # 防止并发启动的标志（使用计数器避免并发 start_session 的 finally 互相覆盖）
        self._starting_session_count = 0
        self._starting_input_mode = None
        self._last_cooldown_turn_end_time = 0.0  # 冷却路径 turn_end 去重时间戳

        # TTS缓存机制：确保不丢包
        self.tts_ready = False  # TTS是否完全就绪
        self.tts_pending_chunks = []  # 待处理的TTS文本chunk: [(speech_id, text), ...]
        self.tts_cache_lock = asyncio.Lock()  # 保护缓存的锁
        self._last_tts_respawn_time: float = 0.0  # 上次 respawn 时间戳，用于 12 秒冷却
        self._tts_respawn_task: Optional[asyncio.Task] = None  # 延迟重试 Task，end_session 时取消
        self._last_tts_error_code: str = ''  # 上次 TTS 错误码
        self._tts_retry_notify_count: int = 0  # TTS 重试通知计数，前3次不通知前端
        self._tts_done_queued_for_turn: bool = False  # 防止同一轮次多次排入 TTS 结束信号
        self._tts_done_pending_until_ready: bool = False  # TTS未就绪时延迟到 flush 后再排入结束信号
        self._active_text_request_id: Optional[str] = None
        self._magic_command_image_drop_request_ids: set[str] = set()
        self._magic_command_image_drop_request_order: deque[str] = deque()
        
        # 输入数据缓存机制：确保session初始化期间的输入不丢失
        self.session_ready = False  # Session是否完全就绪
        self.pending_input_data = []  # 待处理的输入数据: [message_dict, ...]
        self.pending_context_appends: list[dict] = []
        self._context_append_sequence = 0
        self._context_append_request_ids: OrderedDict[tuple[Any, ...], float] = OrderedDict()
        self._context_append_inflight_results: dict[tuple[Any, ...], asyncio.Future[ContextAppendResult]] = {}
        self._require_context_append_current_delivery = False
        self.input_cache_lock = asyncio.Lock()  # 保护输入缓存的锁
        
        # 热切换音频缓存机制：确保热切换期间的用户输入语音不丢失
        self.hot_swap_audio_cache = []  # 热切换期间缓存的音频数据: [bytes, ...]
        self.hot_swap_cache_lock = asyncio.Lock()  # 保护热切换音频缓存的锁
        self.is_flushing_hot_swap_cache = False  # 是否正在推送热切换缓存（推送期间新音频继续缓存）
        self.HOT_SWAP_FLUSH_CHUNK_MULTIPLIER = 5  # 热切换后发送的chunk大小倍数(节流)
        
        # 用户活动时间戳：用于主动搭话检测最近是否有用户输入
        self.last_user_activity_time = None  # float timestamp or None

        # 用户「真实消息」时间戳：仅在非空、非 AI 回声的真用户输入时刷新（语音
        # 真转录 / 文本输入），不含 VAD 空噪声、麦克风录回 AI 自己 TTS 的回声。
        # 区别于 last_user_activity_time（顶部无条件刷新，含回声/空噪声）——后者拿
        # 来判 mini-game 邀请「用户是否已回应」会被 AI 念邀请台词的回声污染，导致
        # 隐式 dismiss 在用户还没点按钮前就把 pending 邀请清掉、按钮撤走，用户随后
        # 点「现在不想玩」落到 expired、真正的 decline 冷却起不来、邀请反复重来。
        self.last_user_message_time = None  # float timestamp or None

        # 用户静默 ≥ IDLE_SESSION_RESET_THRESHOLD_SECONDS 时主动断 session 的
        # 后台 loop。lazily 在首次 start_session 时启动，永久存活（per-manager
        # 单例），无 active session 时 sleep 后继续轮询。
        self._idle_session_reset_task: Optional[asyncio.Task] = None

        # 用户活动 tracker：把窗口/进程/CPU/idle/语音/对话信号聚合成结构化
        # ActivitySnapshot，供 proactive_chat Phase 1/2 决策搭话倾向。
        # 详见 docs/design/user-activity-tracker.md。
        from main_logic.activity import FocusScorer, MasterEmotionTracker, UserActivityTracker
        from main_logic.conversation_turns import create_default_turn_dispatcher
        self._activity_tracker = UserActivityTracker(lanlan_name)
        self._turn_dispatcher = create_default_turn_dispatcher(
            lanlan_name,
            self._activity_tracker,
        )

        # Focus mode 凝神 scorer（docs/design/focus-truename-mode.md）：把
        # ActivitySnapshot + 用户消息文本评成一个 [0,1] 分，喂给 self.state
        # 的迟滞状态机决定这一轮是否「升档」开思考。per-session 实例，仅持有
        # cadence 基线滚动 buffer。两条触发路径（inline stream_text / idle
        # proactive）共用同一个 scorer，保证行为不分裂。
        self._focus_scorer = FocusScorer(lanlan_name)
        # 凝神退出时清理历史 thinking/已闭合 tool call 残留的边沿标志：进入 FOCUS
        # 时 arm，退出并清理后 disarm（见 _maybe_purge_focus_artifacts）。
        self._focus_artifacts_pending = False
        # 进入 FOCUS 那刻的历史长度；退出只清这之后（episode 期间）的闭合 tool call。
        self._focus_artifacts_history_start: int | None = None

        # Master 情绪画像（基建，docs 见 config MASTER_EMOTION_*）：异步分析「用户
        # 说的话」的 valence-arousal，单一权威源。凝神是第一个消费者（_focus_scorer
        # 的 emotion 信号读它的最近读数）。绝不复用 lanlan 头像 outward-emotion 管线。
        # privacy-independent（输入是对话不是屏幕），不受隐私门控。
        self._master_emotion = MasterEmotionTracker(lanlan_name)
        # 凝神 inline 评分用的 emotion 读数快照：每个 user turn 在 _note_user_turn 里
        # 于「本轮 analyze 启动前」刷新，保证 emotion 信号确定性滞后一拍。
        self._focus_emotion_reading = None

        # 进入游戏/娱乐 或 进入专注工作时，给前端推一次性情境信号——前端（每会话每类
        # 一次）据此弹窗问要不要开/关主动搭话里的屏幕分享来源。后端只检测「进入」那一刻
        # 并推送，去重在前端。原本只对 A/B 实验组 vision_chat_default_off 生效，现该机制
        # 已合并进 main，对所有用户开放。
        # 屏幕分享来源只在隐私关（vision 开）时才有意义；隐私开时 tracker 心跳本就不
        # tick（见 _activity_guess_loop 的 _privacy_mode_active 早退），自然不会触发。
        async def _push_activity_context_prompt(context: str) -> None:
            ws = self.websocket
            if not (
                ws
                and hasattr(ws, 'client_state')
                and ws.client_state == ws.client_state.CONNECTED
            ):
                return
            try:
                await ws.send_json({
                    'type': 'activity_context_prompt',
                    'context': context,
                })
            except Exception as e:
                logger.debug(
                    '[%s] activity_context_prompt WS send failed: %s',
                    self.lanlan_name, e,
                )
        self._activity_tracker.set_context_prompt_callback(_push_activity_context_prompt)

        # activity_guess narration 的「下游消费方」门控：会话 goodbye_silent（猫娘挂机
        # 静默）时，proactive Phase 2 一进门就 bail，narration 没人读。把 is_goodbye_silent
        # 注入 tracker，让 20s 心跳在静默期跳过昂贵的 emotion-tier LLM 外呼（规则心跳照跑）。
        # tracker 跨 session 长存、心跳不随 End Session 取消，否则挂机后会一直空烧 LLM。
        self._activity_tracker.set_narration_suppressed_check(self.is_goodbye_silent)

        # AI 当前轮文本 buffer：每个 send_lanlan_response chunk 累加，turn end
        # 时作为一个 conversation turn 发给 dispatcher。activity sink 用末尾
        # 文本判断是否问问号 → 触发 unfinished_thread 机制（5 分钟内允许至多 2
        # 次跟进）；topic sink 独立消费同一 turn，不和 activity tracker 耦合。
        self._current_ai_turn_text: str = ''
        self._recent_ai_voice_echo_text: str = ''
        self._recent_ai_voice_echo_at: float = 0.0
        self._pending_ai_voice_echo_text: str = ''
        self._pending_ai_voice_echo_chunks = deque()
        self._confirmed_ai_voice_echo_audio_speech_ids: set[str] = set()

        # 事件驱动状态机：收口 "谁占用当前 turn" 的所有信号，供 proactive 流水线
        # 零成本（O(1) 读）频繁询问 is_proactive_preempted。事件发射点分布在
        # handle_new_message / stream_text 入口 / prepare_proactive_delivery /
        # finish_proactive_delivery / system_router.proactive_chat 等处。
        self.state = SessionStateMachine(lanlan_name=lanlan_name)
        # Focus 凝神: mirror enter/exit to the frontend as a subtle cognition
        # indicator (极隐微光 badge — see react-neko-chat). Inert by default: the
        # SM only enters Focus when FOCUS_MODE_ENABLED. The badge needs only
        # on/off; memory consumes FOCUS_EXIT's episode payload on a separate
        # (not-yet-wired) path.
        #   Two layers keep the badge honest: (1) FOCUS_ENTER/EXIT subscriptions
        # give immediate updates on the normal hysteresis path; (2) a per-turn
        # reconcile (_reconcile_focus_indicator) catches Focus states dropped
        # WITHOUT an event — clear_focus (history wipe) and the master-switch /
        # privacy self-clear in update_focus — so the badge can't get stuck on.
        # _push_focus_indicator is idempotent on this cached state so the two
        # layers never double-fire.
        self._focus_indicator_active = False
        self.state.subscribe(SessionEvent.FOCUS_ENTER, self._on_focus_transition)
        self.state.subscribe(SessionEvent.FOCUS_EXIT, self._on_focus_transition)

        # 用户语言设置（由 start_session 或前端 set_user_language() 设置，初始为 None）
        self.user_language = None
        self._conversation_turn_language = None
        # 翻译服务（延迟初始化）
        self._translation_service = None
        
        # 防止log刷屏机制
        self.session_closed_by_server = False  # Session被服务器关闭的标志
        self.last_audio_send_error_time = 0.0  # 上次音频发送错误的时间戳
        self.audio_error_log_interval = 2.0  # 音频错误log间隔（秒）

        self._recent_avatar_interaction_ids = deque(maxlen=AVATAR_INTERACTION_DEDUPE_MAX_ITEMS)
        self._recent_avatar_interaction_id_set = set()
        self._last_avatar_interaction_at = 0
        self._last_avatar_interaction_speak_at = 0
        self.avatar_interaction_cooldown_ms = 600
        self.avatar_interaction_speak_cooldown_ms = 1500

        # ── Unified tool calling registry ─────────────────────────────
        # 通过 ``register_tool`` / ``unregister_tool`` 公共方法对外开放。
        # 同进程内的 callback / agent_bridge 走 local handler，跨进程的
        # plugin / agent_server 走 ``remote_dispatcher``（由 main_routers/
        # tool_router.py 在 main_server 启动时绑定 HTTP 转发器）。
        # 同一份 registry 同时给 offline 和 realtime client 使用，所以
        # 切换会话时不需要重新注册。
        self.tool_registry = ToolRegistry()
        # 同步推送 tools 到 active/pending session 时的串行化锁。
        # 防止连续多次 register/unregister/clear 触发的 session.update
        # 在 wire 上乱序（OpenAI Realtime / GLM / Qwen / Step 都接受
        # session.update 流式覆盖，乱序可能让最后一份快照不对应 registry
        # 的最终状态）。
        self._tool_sync_lock = asyncio.Lock()
        # 下一次 handle_response_complete 发出的 turn end 要携带的 meta。
        # 在 handle_avatar_interaction 等需要标记特殊轮次的入口里设置，
        # 由 handle_response_complete 读取并清空。比独立的
        # sync_message_queue 控制消息更原子：meta 与 turn end 事件
        # 同生共死，不会因为两条消息的时序错乱而把 avatar 轮当成 proactive。
        self._pending_turn_meta: Optional[dict] = None

        # 内置 pseudo 工具（目前只有 recall_memory）。在 __init__ 末尾注册
        # 一份占位，此时 user_language 还可能是 None → 短码兜底回退 'en'；
        # 真正进 session 前会再 refresh 一次，把 description 对齐到当时
        # 已知的 user_language。
        self._register_builtin_tools()

    def _fire_task(self, coro):
        """Create a background task with GC protection (prevent Python 3.11+ from collecting it)."""
        task = asyncio.create_task(coro)
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)
        return task

    def _context_append_request_key(self, payload: Mapping[str, Any]) -> tuple[Any, ...] | None:
        request_id = str(payload.get("request_id") or "").strip()
        if not request_id:
            return None
        source = str(payload.get("source") or "").strip()
        lifetime = str(payload.get("lifetime") or "current_session").strip().lower()
        if lifetime == "current_session":
            if payload.get("_dedup_pending_ready"):
                return (source, request_id, lifetime, "pending_ready")
            session_id = payload.get("_dedup_session_id")
            if session_id is None:
                session_id = id(getattr(self, "session", None))
            return (source, request_id, lifetime, session_id)
        return (source, request_id, lifetime)

    def _context_append_durable_cache_key(self, payload: Mapping[str, Any]) -> tuple[Any, ...] | None:
        request_id = str(payload.get("request_id") or "").strip()
        lifetime = str(payload.get("lifetime") or "").strip().lower()
        if not request_id or lifetime not in {"next_session", "session_family"}:
            return None
        source = str(payload.get("source") or "").strip()
        return (source, request_id, lifetime)

    def _context_append_durable_cache_seen(self, payload: Mapping[str, Any]) -> bool:
        key = self._context_append_durable_cache_key(payload)
        if key is None:
            return False
        seen = getattr(self, "_context_append_durable_cache_keys", None)
        if not isinstance(seen, OrderedDict):
            return False
        now = time.time()
        cutoff = now - _CONTEXT_APPEND_DEDUP_TTL_SECONDS
        while seen:
            oldest_key = next(iter(seen))
            if seen[oldest_key] >= cutoff:
                break
            seen.pop(oldest_key, None)
            entries = getattr(self, "_context_append_durable_cache_entries", None)
            if isinstance(entries, dict):
                entries.pop(oldest_key, None)
        return key in seen

    def _context_append_durable_cache_contains(self, payload: Mapping[str, Any]) -> bool:
        key = self._context_append_durable_cache_key(payload)
        if key is None:
            return False
        entries = getattr(self, "_context_append_durable_cache_entries", None)
        expected_entry = None
        if isinstance(entries, dict):
            expected_entry = entries.get(key)
        if expected_entry is None:
            expected_entry = self._context_payload_cache_key(payload)
        cache = getattr(self, "next_session_context_messages", None)
        if not isinstance(cache, list):
            return False
        return expected_entry in {
            (str(entry.get("role") or ""), str(entry.get("text") or ""))
            for entry in cache
            if isinstance(entry, Mapping)
        }

    def _remember_context_append_durable_cache(self, payload: Mapping[str, Any]) -> None:
        key = self._context_append_durable_cache_key(payload)
        if key is None:
            return
        seen = getattr(self, "_context_append_durable_cache_keys", None)
        if not isinstance(seen, OrderedDict):
            seen = OrderedDict()
            self._context_append_durable_cache_keys = seen
        entries = getattr(self, "_context_append_durable_cache_entries", None)
        if not isinstance(entries, dict):
            entries = {}
            self._context_append_durable_cache_entries = entries
        entries[key] = self._context_payload_cache_key(payload)
        seen[key] = time.time()
        seen.move_to_end(key)
        while len(seen) > _CONTEXT_APPEND_DEDUP_MAX_ENTRIES:
            stale_key, _ = seen.popitem(last=False)
            entries.pop(stale_key, None)

    def _forget_context_append_durable_cache(self, payload: Mapping[str, Any]) -> None:
        key = self._context_append_durable_cache_key(payload)
        if key is None:
            return
        seen = getattr(self, "_context_append_durable_cache_keys", None)
        if isinstance(seen, OrderedDict):
            seen.pop(key, None)
        entries = getattr(self, "_context_append_durable_cache_entries", None)
        if isinstance(entries, dict):
            entries.pop(key, None)

    def _promote_context_append_request_id_to_current_session(self, payload: dict) -> None:
        if (
            str(payload.get("lifetime") or "").strip().lower() != "current_session"
            or not payload.get("_dedup_pending_ready")
            or not payload.get("request_id")
        ):
            return
        seen = getattr(self, "_context_append_request_ids", None)
        if not isinstance(seen, OrderedDict):
            return
        old_key = self._context_append_request_key(payload)
        if old_key is None:
            return
        timestamp = seen.pop(old_key, None)
        payload.pop("_dedup_pending_ready", None)
        payload["_dedup_session_id"] = id(getattr(self, "session", None))
        new_key = self._context_append_request_key(payload)
        if timestamp is not None and new_key is not None:
            seen[new_key] = timestamp
            self._context_append_request_ids = OrderedDict(
                sorted(seen.items(), key=lambda item: item[1])
            )

    def _remember_context_append_request_id(self, payload: Mapping[str, Any]) -> None:
        key = self._context_append_request_key(payload)
        if key is None:
            return
        seen = getattr(self, "_context_append_request_ids", None)
        if not isinstance(seen, OrderedDict):
            seen = OrderedDict()
            self._context_append_request_ids = seen
        now = time.time()
        cutoff = now - _CONTEXT_APPEND_DEDUP_TTL_SECONDS
        while seen:
            oldest_key = next(iter(seen))
            if seen[oldest_key] >= cutoff:
                break
            seen.pop(oldest_key, None)
        seen[key] = now
        seen.move_to_end(key)
        while len(seen) > _CONTEXT_APPEND_DEDUP_MAX_ENTRIES:
            seen.popitem(last=False)

    def _forget_context_append_request_id(self, payload: Mapping[str, Any]) -> None:
        key = self._context_append_request_key(payload)
        if key is None:
            return
        seen = getattr(self, "_context_append_request_ids", None)
        if isinstance(seen, OrderedDict):
            seen.pop(key, None)

    def _context_append_request_seen(self, payload: Mapping[str, Any]) -> bool:
        key = self._context_append_request_key(payload)
        if key is None:
            return False
        seen = getattr(self, "_context_append_request_ids", None)
        if not isinstance(seen, OrderedDict):
            return False
        now = time.time()
        cutoff = now - _CONTEXT_APPEND_DEDUP_TTL_SECONDS
        while seen:
            oldest_key = next(iter(seen))
            if seen[oldest_key] >= cutoff:
                break
            seen.pop(oldest_key, None)
        return key in seen

    def _normalize_context_append(
        self,
        *,
        source: str,
        role: str,
        text: str,
        audience: str,
        timing: str,
        lifetime: str,
        request_id: str | None,
        ordering_key: str | None,
        metadata: Mapping[str, Any] | None,
    ) -> dict | None:
        normalized_source = str(source or "").strip()
        normalized_role = str(role or "").strip().lower()
        normalized_audience = str(audience or "").strip().lower()
        normalized_timing = str(timing or "").strip().lower()
        normalized_lifetime = str(lifetime or "").strip().lower()
        if (
            not normalized_source
            or normalized_role not in {"assistant", "user", "system"}
            or normalized_audience not in {"model", "user_and_model"}
            or normalized_timing not in {"now", "when_ready"}
            or normalized_lifetime not in {"current_session", "next_session", "session_family"}
        ):
            return None
        content = self._normalize_context_text_for_source(normalized_source, text)
        if not content:
            return None
        safe_metadata = dict(metadata or {}) if isinstance(metadata, Mapping) else {}
        return {
            "source": normalized_source,
            "role": normalized_role,
            "text": content,
            "audience": normalized_audience,
            "timing": normalized_timing,
            "lifetime": normalized_lifetime,
            "request_id": str(request_id or "").strip(),
            "ordering_key": str(ordering_key or "").strip(),
            "metadata": safe_metadata,
        }

    def _normalize_context_text_for_source(self, source: str, text: Any) -> str:
        content = str(text or "").strip()
        if not content:
            return ""
        max_tokens = _CONTEXT_APPEND_SOURCE_MAX_TOKENS.get(
            str(source or "").strip(),
            _CONTEXT_APPEND_DEFAULT_MAX_TOKENS,
        )
        try:
            from utils.tokenize import truncate_to_tokens
            return truncate_to_tokens(content[: max(max_tokens * 8, max_tokens)], max_tokens).strip()
        except Exception:
            return content[: max(max_tokens * 8, max_tokens)].strip()

    def _append_context_to_new_session_cache(self, role: str, text: str) -> bool:
        cache = getattr(self, "next_session_context_messages", None)
        if not isinstance(cache, list):
            cache = []
            self.next_session_context_messages = cache
        if role == "user":
            speaker = getattr(self, "master_name", "user")
        elif role == "assistant":
            speaker = getattr(self, "lanlan_name", "assistant")
        else:
            speaker = "system"
        cache.append({"role": speaker, "text": text})
        return True

    def _context_payload_cache_key(self, payload: Mapping[str, Any]) -> tuple[str, str]:
        role = str(payload.get("role") or "").strip().lower()
        if role == "user":
            speaker = getattr(self, "master_name", "user")
        elif role == "assistant":
            speaker = getattr(self, "lanlan_name", "assistant")
        else:
            speaker = "system"
        return (speaker, str(payload.get("text") or ""))

    def _mark_pending_context_appends_delivered_in_start_prompt(
        self,
        snapshot: list[dict],
        *,
        owner: object | None = None,
    ) -> None:
        pending = getattr(self, "pending_context_appends", None)
        if not isinstance(pending, list) or not pending or not snapshot:
            return
        available: dict[tuple[str, str], int] = {}
        for entry in snapshot:
            if not isinstance(entry, Mapping):
                continue
            key = (str(entry.get("role") or ""), str(entry.get("text") or ""))
            available[key] = available.get(key, 0) + 1
        for payload in pending:
            if (
                not isinstance(payload, dict)
                or not payload.get("_durable_cached")
                or payload.get("_delivered_in_start_prompt")
            ):
                continue
            key = self._context_payload_cache_key(payload)
            count = available.get(key, 0)
            if count <= 0:
                continue
            payload["_delivered_in_start_prompt"] = True
            payload["_delivered_in_start_prompt_owner"] = owner
            available[key] = count - 1

    def _clear_pending_context_start_prompt_marks(self, *, owner: object | None = None) -> None:
        pending = getattr(self, "pending_context_appends", None)
        if not isinstance(pending, list):
            return
        for payload in pending:
            if isinstance(payload, dict):
                if owner is not None and payload.get("_delivered_in_start_prompt_owner") is not owner:
                    continue
                payload.pop("_delivered_in_start_prompt", None)
                payload.pop("_delivered_in_start_prompt_owner", None)

    def _snapshot_next_session_context_messages(self) -> list[dict]:
        cache = getattr(self, "next_session_context_messages", None)
        if not isinstance(cache, list) or not cache:
            return []
        return list(cache)

    def _consume_next_session_context_messages(self, count: int) -> None:
        if count <= 0:
            return
        cache = getattr(self, "next_session_context_messages", None)
        if isinstance(cache, list):
            del cache[:count]

    async def _prime_late_next_session_context_after_swap(
        self,
        start_index: int,
        end_index: int | None = None,
    ) -> int:
        consumed_count = max(0, start_index)
        session = getattr(self, "session", None)
        prime_context = getattr(session, "prime_context", None)
        if not callable(prime_context):
            return consumed_count

        snapshot = self._snapshot_next_session_context_messages()
        stop_index = len(snapshot) if end_index is None else max(consumed_count, min(end_index, len(snapshot)))
        late_context = snapshot[consumed_count:stop_index]
        if not late_context:
            return consumed_count
        try:
            await prime_context(self._convert_cache_to_str(late_context), skipped=True)
        except Exception as exc:
            logger.warning(
                "[%s] final-swap late next-session context prime failed: %s",
                self.lanlan_name,
                exc,
            )
            return consumed_count
        consumed_count += len(late_context)

        return consumed_count

    async def _append_context_to_targets(self, payload: dict) -> ContextAppendResult:
        role = payload["role"]
        content = payload["text"]
        audience = payload["audience"]
        lifetime = payload["lifetime"]
        targets: list[str] = []
        if payload.get("_delivered_in_start_prompt") and lifetime in {"next_session", "session_family"}:
            return ContextAppendResult(appended=True, targets=("start_prompt",))
        if lifetime in {"next_session", "session_family"}:
            durable_cache_remembered = (
                payload.get("_durable_cached")
                or self._context_append_durable_cache_seen(payload)
            )
            # 去重记账本（_context_append_durable_cache_*）与真缓存
            # （next_session_context_messages）是两套独立结构：前者记“这条已写过”，
            # 后者存实际内容，而后者会被 session-swap 的 _consume_next_session_context_messages
            # 异步消费/清空。下面用 remembered（记账本）与 present（真缓存）双重核对决定是否
            # 重写，本身能兜住失步、不丢上下文；但两者一旦失步是静默的，故在此显式自检并告警，
            # 把“隐蔽失步”变成日志里可观测的信号（见 _consume_next_session_context_messages
            # 不同步清记账本的设计债）。
            durable_cache_present = self._context_append_durable_cache_contains(payload)
            if durable_cache_remembered and not durable_cache_present:
                # 记账本说写过、内容却没了：通常是 swap 消费了缓存而记账本（TTL 内）未清。
                # 当前靠下方 present 核对兜底重写、不会丢；但若后续有人移除该核对、只信记账本，
                # 这条上下文就会被误判“已写过”而静默丢失。出现此日志即代表两者已失步。
                logger.warning(
                    "[%s] durable context cache desync: dedup record present but content "
                    "missing from next-session cache; re-appending (source=%s request_id=%s)",
                    self.lanlan_name,
                    payload.get("source"),
                    payload.get("request_id"),
                )
            elif durable_cache_present and not durable_cache_remembered:
                # 内容在、记账本却没记：这条会被当作首次写而重复入库，下个 session 可能看到两遍。
                logger.warning(
                    "[%s] durable context cache desync: content present but dedup record "
                    "missing; may duplicate in next session (source=%s request_id=%s)",
                    self.lanlan_name,
                    payload.get("source"),
                    payload.get("request_id"),
                )
            if durable_cache_remembered and durable_cache_present:
                targets.append("new_session_cache")
            elif self._append_context_to_new_session_cache(role, content):
                payload["_durable_cached"] = True
                self._remember_context_append_durable_cache(payload)
                targets.append("new_session_cache")
        session = getattr(self, "session", None)
        history = getattr(session, "_conversation_history", None)
        wrote_active_history = False
        current_session_required = lifetime in {"current_session", "session_family"}
        current_session_delivered = False
        current_session_failed_reason: str | None = None
        if lifetime in {"current_session", "session_family"} and isinstance(history, list):
            if role == "assistant":
                message = AIMessage(content=content)
            elif role == "user":
                message = HumanMessage(content=content)
            else:
                message = HumanMessage(content=f"system: {content}")
            history.append(message)
            targets.append("active_history")
            wrote_active_history = True
            current_session_delivered = True

        if lifetime in {"current_session", "session_family"} and not wrote_active_history:
            prime_context = getattr(session, "prime_context", None)
            if callable(prime_context):
                try:
                    source = str(payload.get("source") or "")
                    prime_text = content if source in _CONTEXT_APPEND_BARE_PRIME_SOURCES else f"{role}: {content}"
                    await prime_context(prime_text, skipped=(audience == "model"))
                    targets.append("realtime_prime")
                    current_session_delivered = True
                except Exception as exc:
                    current_session_failed_reason = "realtime_prime_failed"
                    logger.warning("[%s] context append realtime_prime failed: %s", self.lanlan_name, exc)
            else:
                current_session_failed_reason = "no_current_session_target"

        current_session_delivery_required = (
            current_session_required
            and (
                not bool(getattr(self, "is_preparing_new_session", False))
                or bool(getattr(self, "_require_context_append_current_delivery", False))
            )
        )
        if current_session_delivery_required and not current_session_delivered:
            return ContextAppendResult(
                appended=False,
                targets=tuple(targets),
                reason=current_session_failed_reason or "current_session_target_unavailable",
            )

        if not targets:
            return ContextAppendResult(appended=False, reason="no_context_target")
        return ContextAppendResult(appended=True, targets=tuple(targets))

    async def append_context(
        self,
        *,
        source: str,
        role: str,
        text: str,
        audience: str = "model",
        timing: str = "now",
        lifetime: str = "current_session",
        request_id: str | None = None,
        ordering_key: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> ContextAppendResult:
        payload = self._normalize_context_append(
            source=source,
            role=role,
            text=text,
            audience=audience,
            timing=timing,
            lifetime=lifetime,
            request_id=request_id,
            ordering_key=ordering_key,
            metadata=metadata,
        )
        if payload is None:
            return ContextAppendResult(appended=False, reason="invalid_context")
        pending_needed = (
            payload["timing"] == "when_ready"
            and (
                not bool(getattr(self, "session_ready", False))
                or getattr(self, "session", None) is None
            )
        )
        if pending_needed and payload["lifetime"] == "current_session":
            payload["_dedup_pending_ready"] = True
        request_key = self._context_append_request_key(payload)
        if self._context_append_request_seen(payload):
            inflight = getattr(self, "_context_append_inflight_results", None)
            if isinstance(inflight, dict) and request_key in inflight:
                original_result = await asyncio.shield(inflight[request_key])
                if original_result.appended:
                    return ContextAppendResult(
                        appended=False,
                        deduped=True,
                        targets=original_result.targets,
                        reason="duplicate_request_id",
                    )
                return original_result
            return ContextAppendResult(appended=False, deduped=True, reason="duplicate_request_id")
        reserved_request_id = bool(payload["request_id"])
        inflight_result: asyncio.Future[ContextAppendResult] | None = None
        if reserved_request_id:
            self._remember_context_append_request_id(payload)
            if request_key is not None:
                inflight = getattr(self, "_context_append_inflight_results", None)
                if not isinstance(inflight, dict):
                    inflight = {}
                    self._context_append_inflight_results = inflight
                inflight_result = asyncio.get_running_loop().create_future()
                inflight[request_key] = inflight_result

        if pending_needed:
            if payload["lifetime"] in {"next_session", "session_family"}:
                payload["_durable_cached"] = self._append_context_to_new_session_cache(
                    payload["role"],
                    payload["text"],
                )
                if payload["_durable_cached"]:
                    self._remember_context_append_durable_cache(payload)
            pending = getattr(self, "pending_context_appends", None)
            if not isinstance(pending, list):
                pending = []
                self.pending_context_appends = pending
            sequence = int(getattr(self, "_context_append_sequence", 0))
            self._context_append_sequence = sequence + 1
            payload["_sequence"] = sequence
            payload["_pending_ready"] = True
            pending.append(payload)
            result = ContextAppendResult(appended=True, targets=("pending_ready",))
            if inflight_result is not None and not inflight_result.done():
                inflight_result.set_result(result)
            if request_key is not None:
                inflight = getattr(self, "_context_append_inflight_results", None)
                if isinstance(inflight, dict):
                    inflight.pop(request_key, None)
            return result

        try:
            result = await self._append_context_to_targets(payload)
        except asyncio.CancelledError:
            if reserved_request_id:
                self._forget_context_append_request_id(payload)
            if inflight_result is not None and not inflight_result.done():
                inflight_result.set_result(ContextAppendResult(
                    appended=False,
                    reason="context_inject_cancelled",
                ))
            raise
        except Exception:
            if reserved_request_id:
                self._forget_context_append_request_id(payload)
            if inflight_result is not None and not inflight_result.done():
                inflight_result.set_result(ContextAppendResult(
                    appended=False,
                    reason="context_inject_failed",
                ))
            raise
        else:
            if not result.appended and reserved_request_id:
                self._forget_context_append_request_id(payload)
            if inflight_result is not None and not inflight_result.done():
                inflight_result.set_result(result)
        finally:
            if request_key is not None:
                inflight = getattr(self, "_context_append_inflight_results", None)
                if isinstance(inflight, dict):
                    inflight.pop(request_key, None)
        return result

    async def _flush_pending_context_appends(self) -> int:
        pending = getattr(self, "pending_context_appends", None)
        if not isinstance(pending, list) or not pending:
            return 0
        self.pending_context_appends = []
        pending.sort(key=lambda payload: (
            payload.get("ordering_key") or f"~{int(payload.get('_sequence', 0)):020d}",
            int(payload.get("_sequence", 0)),
        ))
        retry: list[dict] = []
        flushed = 0
        for index, payload in enumerate(pending):
            try:
                result = await self._append_context_to_targets(payload)
                if not result.appended:
                    retry.append(payload)
                else:
                    self._promote_context_append_request_id_to_current_session(payload)
                    flushed += 1
            except asyncio.CancelledError:
                retry.append(payload)
                retry.extend(pending[index + 1:])
                if retry:
                    self.pending_context_appends = retry + self.pending_context_appends
                raise
            except Exception as exc:
                retry.append(payload)
                logger.warning("[%s] context append flush failed: %s", self.lanlan_name, exc)
        if retry:
            self.pending_context_appends = retry + self.pending_context_appends
        return flushed

    async def _drain_pending_context_appends_before_ready(self) -> None:
        for _ in range(_CONTEXT_APPEND_READY_FLUSH_MAX_PASSES):
            pending = getattr(self, "pending_context_appends", None)
            if not isinstance(pending, list) or not pending:
                return
            before_ids = {id(payload) for payload in pending}
            flushed = await self._flush_pending_context_appends()
            pending = getattr(self, "pending_context_appends", None)
            if not isinstance(pending, list) or not pending:
                return
            after_ids = {id(payload) for payload in pending}
            if flushed <= 0 and after_ids <= before_ids:
                return
        pending = getattr(self, "pending_context_appends", None)
        if isinstance(pending, list) and pending:
            logger.warning(
                "[%s] context append ready drain left %d pending item(s)",
                self.lanlan_name,
                len(pending),
            )

    def _clear_pending_context_appends(self, *, release_durable_cached: bool = False) -> None:
        pending = getattr(self, "pending_context_appends", None)
        if isinstance(pending, list):
            stale_payloads = list(pending)
            pending.clear()
        else:
            stale_payloads = []
            self.pending_context_appends = []
        for payload in stale_payloads:
            if (
                isinstance(payload, dict)
                and payload.get("request_id")
                and (release_durable_cached or not payload.get("_durable_cached"))
            ):
                self._forget_context_append_request_id(payload)
                if release_durable_cached:
                    self._forget_context_append_durable_cache(payload)

    def is_goodbye_silent(self) -> bool:
        """Whether cat-mode silence after being asked to leave is in effect."""
        return bool(getattr(self, "goodbye_silent", False))

    def set_goodbye_silent(self, active: bool, reason: str = "") -> None:
        """Sync the frontend cat-mode silence state, and park queued proactive callbacks in the persistent queue."""
        active = bool(active)
        reason = str(reason or "")[:64]
        was_active = self.is_goodbye_silent()
        self.goodbye_silent = active
        self.goodbye_silent_reason = reason
        self.goodbye_silent_updated_at = time.time()
        if active:
            self._park_proactive_for_goodbye()
        if was_active != active:
            logger.info("[%s] goodbye_silent=%s reason=%s", self.lanlan_name, active, reason or "-")

    def _park_proactive_for_goodbye(self) -> None:
        """While cat-mode silent, move the manager's pending-release callbacks into the persistent queue, so nothing is dropped or released on timeout during the silence."""
        try:
            leftover = self.proactive_manager.drain_pending()
            for callback in leftover:
                self.enqueue_agent_callback(callback)
            self.proactive_manager.reset_gate()
            if leftover:
                logger.info("[%s] goodbye_silent parked proactive callbacks n=%d", self.lanlan_name, len(leftover))
        except Exception:
            logger.exception("[%s] goodbye_silent proactive park failed", self.lanlan_name)

    def _ensure_audio_stream_worker(self):
        if self._audio_stream_worker_task and not self._audio_stream_worker_task.done():
            return
        self._audio_stream_worker_task = self._fire_task(self._audio_stream_worker_loop())

    def _clear_audio_stream_queue(self, reason: str):
        dropped = 0
        while True:
            try:
                self._audio_stream_queue.get_nowait()
                self._audio_stream_queue.task_done()
                dropped += 1
            except asyncio.QueueEmpty:
                break
        if dropped:
            self._audio_stream_dropped_total += dropped
            logger.info(
                "[%s] audio stream queue cleared reason=%s dropped=%d total_dropped=%d",
                self.lanlan_name, reason, dropped, self._audio_stream_dropped_total,
            )

    def _cancel_audio_stream_worker(self, reason: str):
        task = self._audio_stream_worker_task
        if not task:
            return
        if task.done():
            self._audio_stream_worker_task = None
            return
        if task is asyncio.current_task():
            return
        task.cancel()
        self._audio_stream_worker_task = None
        logger.debug("[%s] audio stream worker cancelled reason=%s", self.lanlan_name, reason)

    async def _enqueue_audio_stream_data(self, message: dict):
        self._ensure_audio_stream_worker()
        if self._audio_stream_queue.full():
            try:
                self._audio_stream_queue.get_nowait()
                self._audio_stream_queue.task_done()
                self._audio_stream_dropped_total += 1
            except asyncio.QueueEmpty:
                pass
        await self._audio_stream_queue.put(message)
        qsize = self._audio_stream_queue.qsize()
        now = time.time()
        if qsize >= 250 and now - self._last_audio_stream_backlog_log_time >= 2.0:
            self._last_audio_stream_backlog_log_time = now
            logger.warning(
                "[%s] audio stream queue backlog qsize=%d max=%d total_dropped=%d",
                self.lanlan_name,
                qsize,
                self._audio_stream_queue.maxsize,
                self._audio_stream_dropped_total,
            )

    async def _audio_stream_worker_loop(self):
        while True:
            while not self.session_ready and self._starting_session_count > 0:
                await asyncio.sleep(0.02)
            message = await self._audio_stream_queue.get()
            try:
                await self._stream_data_now(message)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("[%s] audio stream worker error: %s", self.lanlan_name, exc)
            finally:
                self._audio_stream_queue.task_done()

    def _emit_cooldown_turn_end_if_needed(self):
        """Deduplicated turn_end emission during cooldown, at most once per second. Returns True when currently cooling down."""
        if not self._memory_error_retry_after or time.time() >= self._memory_error_retry_after:
            return False
        now = time.time()
        if now - self._last_cooldown_turn_end_time >= 1.0:
            self._last_cooldown_turn_end_time = now
            time_left = int(self._memory_error_retry_after - now)
            self._fire_task(self.send_status(json.dumps({
                "code": "MEMORY_SERVER_COOLDOWN",
                "details": {"wait_time": time_left}
            })))
            self.sync_message_queue.put({'type': 'system', 'data': 'turn end'})
            if self.websocket and hasattr(self.websocket, 'client_state') and self.websocket.client_state == self.websocket.client_state.CONNECTED:
                self._fire_task(self.websocket.send_json({'type': 'system', 'data': 'turn end'}))
        return True

    def _get_text_guard_max_length(self) -> int:
        """Read the user-configured reply token cap.
        Unit: tiktoken (o200k_base) tokens. 0 = unlimited (returns 999999).
        Default 300 tokens ≈ 400 CJK characters / ~1200 English characters.
        """
        try:
            # 优先从对话设置中读取，如果不存在则从核心配置读取
            conversation_settings = load_global_conversation_settings()
            if 'textGuardMaxLength' in conversation_settings:
                value = int(conversation_settings['textGuardMaxLength'])
            else:
                value = int(self._config_manager.get_core_config().get('TEXT_GUARD_MAX_LENGTH', 300))
            # 0 / 负数都表示"无限制"，与 OmniOfflineClient.__init__ /
            # update_max_response_length 的语义统一。原本 < 0 会 raise 然后
            # fallback 到 300，存量配置带 -1 的会被静默降级。
            if value <= 0:
                return 999999
            return value
        except Exception:
            return 300

    def _enqueue_tts_text_chunk(self, speech_id, text: str) -> None:
        """Enqueue a text chunk into the TTS queue; http_sentence-class providers go through the normalizer.

        The caller must already hold ``self.tts_cache_lock`` (consistent with the
        existing put call sites). For ws_bistream-class providers (qwen / step /
        cosyvoice), text fragments are sent straight to the server, skipping the
        normalizer to avoid pending_spaces latency and CJK-boundary space removal
        disturbing the server's synthesis cadence. Control signals
        (``__interrupt__`` interrupt / ``(None, None)`` end-of-utterance flush /
        ``("__shutdown__", None)`` worker exit) should still be sent directly via
        ``tts_request_queue.put``, calling ``_reset_tts_stream_normalizer`` at the
        appropriate moment.
        """
        # speech_id 切换时重置所有 stripper 状态（pending 内容属于上一轮，丢弃）
        if speech_id != self._tts_norm_speech_id:
            self._tts_stream_normalizer.reset()
            self._tts_markdown_stripper.reset()
            self._tts_bracket_stripper.reset()
            self._tts_norm_speech_id = speech_id

        if self._tts_normalize_enabled:
            text = self._tts_stream_normalizer.feed(text)
            if not text:
                return
        # markdown → bracket 顺序固定：链接先剥成文本再交给 bracket
        text = self._tts_markdown_stripper.feed(text)
        if not text:
            return
        text = self._tts_bracket_stripper.feed(text)
        if not text:
            return
        self.tts_request_queue.put((speech_id, text))
        self._remember_pending_ai_voice_echo(speech_id, text)

    def _reset_tts_stream_normalizer(self) -> None:
        """Clear all TTS text stripper state. Called on interrupt / turn end / session rebuild."""
        self._tts_stream_normalizer.reset()
        self._tts_markdown_stripper.reset()
        self._tts_bracket_stripper.reset()
        self._tts_norm_speech_id = None

    def _request_tts_done_locked(self) -> str:
        """Request that a TTS end signal be enqueued for the current turn.

        The caller must already hold ``self.tts_cache_lock``. If text is still
        pending or the worker isn't ready yet, only the deferred state is recorded,
        and `_flush_tts_pending_chunks()` re-sends it after ready, so that
        `(None, None)` never enters the queue before the text chunks.
        """
        if self._tts_done_queued_for_turn:
            return "already"

        worker_alive = bool(self.tts_thread and self.tts_thread.is_alive())
        if not worker_alive:
            return "no_worker"

        if not self.tts_ready or self.tts_pending_chunks:
            self._tts_done_pending_until_ready = True
            return "deferred"

        # 把 markdown/bracket stripper 的 pending 兜底 emit：链 markdown.flush()
        # → bracket.feed(...) → bracket.flush() 顺序，与 _enqueue_tts_text_chunk
        # 的串接顺序一致。markdown.flush 把残留的孤立 marker 字符删掉再 emit；
        # bracket.feed 处理任何残留括号字符；bracket.flush 直接 reset 不读
        # 未闭合的括号内容。normalizer.flush 永远返回 ""，省略调用。
        flushed = self._tts_markdown_stripper.flush()
        if flushed:
            flushed = self._tts_bracket_stripper.feed(flushed)
        self._tts_bracket_stripper.flush()
        if flushed and self._tts_norm_speech_id is not None:
            self.tts_request_queue.put((self._tts_norm_speech_id, flushed))
            self._remember_pending_ai_voice_echo(self._tts_norm_speech_id, flushed)

        self.tts_request_queue.put((None, None))
        self._tts_done_queued_for_turn = True
        self._tts_done_pending_until_ready = False
        return "queued"

    async def _request_tts_done_for_turn(
        self,
        source: str,
        expected_speech_id: str | None = None,
    ) -> str:
        """Thread-safely request the TTS end signal for the current turn.

        ``expected_speech_id`` is an optional sid check: callers holding a snapshot
        of this turn's sid pass it in, and the function only sends done after
        confirming inside the lock that ``self.current_speech_id`` still equals the
        snapshot. In recovery / proactive scenarios where the user starts a new
        turn between awaits, the old turn's done signal would otherwise terminate
        the new turn's TTS outright (first sentence clipped / whole turn silent).
        Omitting it keeps the original behavior: always send done."""
        if not self.use_tts:
            return "disabled"

        async with self.tts_cache_lock:
            if expected_speech_id is not None and self.current_speech_id != expected_speech_id:
                logger.debug(
                    "%s: stale TTS done skipped (expected=%s current=%s)",
                    source, expected_speech_id, self.current_speech_id,
                )
                return "stale"
            status = self._request_tts_done_locked()

        if status == "already":
            logger.debug("%s: TTS done 已排入队列，跳过重复信号", source)
        elif status == "deferred":
            logger.debug("%s: TTS 未就绪或仍有 pending chunk，延迟排入 done 信号", source)

        return status

    async def _emit_recall_filler_tts(self, text: str, turn_sid: str) -> bool:
        """Synthesize and play the recall filler line immediately as an **independent worker utterance**.

        Key design — enqueue with a *worker-only* filler sid distinct from this
        turn's sid, then send a ``(None, None)`` flush:

        - the TTS worker treats the filler as a complete utterance and commits
          synthesis immediately (filling the retrieval gap);
        - when the main text later enqueues with the real turn sid, the worker sees
          ``current_speech_id != sid``, automatically opens a new utterance and
          resets ``text_done_sent``. If the filler reused the same turn sid, the
          worker's ``sid is None`` branch would only set ``text_done_sent=True``
          without switching sids, and the main text would be dropped wholesale at
          the ``if text_done_sent: discard residual text`` check (= main text
          silent). The separate sid exists precisely to bypass that worker
          behavior.

        Note: the worker-internal sid is only for utterance segmentation; audio
        sent to the frontend still carries core's ``self.current_speech_id``
        (= the turn sid), so the frontend sees one continuous turn of audio with no
        frontend changes needed.

        When not ready, simply give up on the immediate filler (return False) —
        do **not** degrade into "stuff it into pending to flush with the main
        text"; that was exactly the old "filler glued in front of the main text"
        bug.
        """
        if not self.use_tts:
            return False
        async with self.tts_cache_lock:
            if self.current_speech_id != turn_sid:
                return False
            if not (self.tts_ready and self.tts_thread and self.tts_thread.is_alive()):
                return False
            # 切到 filler 的 worker-sid 之前，先处理本轮 turn_sid 可能还在管线里的
            # pre-tool 正文（provider 先吐 content 再进 tool_calls 时会有，见
            # _astream_openai_with_tools 的 streamed_text_buffer）。直接 _enqueue
            # filler_sid 会让 _enqueue_tts_text_chunk 因 sid 变化 reset stripper、丢掉
            # turn_sid 仍 pending 的文本，且 worker 换连接也会丢 server 端缓冲，造成同轮
            # 正文缺字（Codex P2）。所以先把 stripper pending flush 出去、并用 (None,None)
            # 把 turn_sid utterance commit 掉（worker 发 text.done 后才换 sid，不丢内容）。
            # 仅当本轮确有 turn_sid 文本入过队（_tts_norm_speech_id == turn_sid）才触发；
            # 模型首动作即调 recall（无 pre-tool 文本）时跳过，行为不变。
            if self._tts_norm_speech_id == turn_sid:
                pre_tool = self._tts_markdown_stripper.flush()
                if pre_tool:
                    pre_tool = self._tts_bracket_stripper.feed(pre_tool)
                self._tts_bracket_stripper.flush()
                if pre_tool:
                    self.tts_request_queue.put((turn_sid, pre_tool))
                    self._remember_pending_ai_voice_echo(turn_sid, pre_tool)
                # 直接放 (None,None)，不走 _request_tts_done_locked，故不置
                # _tts_done_queued_for_turn——正文/收尾仍各自正常 flush。
                self.tts_request_queue.put((None, None))
            filler_sid = f"{turn_sid}{_RECALL_FILLER_SID_SUFFIX}"
            self._enqueue_tts_text_chunk(filler_sid, text)
            # flush 这段独立 utterance。用 filler_sid 而非 turn sid，所以**不**触碰
            # 本轮 _tts_done_queued_for_turn——正文之后仍按正常 turn-end 流程 flush。
            self.tts_request_queue.put((None, None))
            return True

    def _remember_avatar_interaction_id(self, interaction_id: str) -> None:
        if interaction_id in self._recent_avatar_interaction_id_set:
            return
        if self._recent_avatar_interaction_ids.maxlen and len(self._recent_avatar_interaction_ids) >= self._recent_avatar_interaction_ids.maxlen:
            oldest_id = self._recent_avatar_interaction_ids[0]
            self._recent_avatar_interaction_id_set.discard(oldest_id)
        self._recent_avatar_interaction_ids.append(interaction_id)
        self._recent_avatar_interaction_id_set.add(interaction_id)

    def _has_connected_websocket(self) -> bool:
        websocket = self.websocket
        if not websocket or not hasattr(websocket, 'client_state'):
            return False
        try:
            return websocket.client_state == websocket.client_state.CONNECTED
        except Exception:
            return False

    def _can_preserve_tts_ready_for_session_start(self) -> bool:
        """A live, previously-ready TTS worker will not emit __ready__ again."""
        current_key = self._build_tts_runtime_key()
        worker_key = getattr(self, "_tts_runtime_key", None)
        return bool(
            self.tts_ready
            and self.tts_thread is not None
            and self.tts_thread.is_alive()
            and current_key == worker_key
        )

    @staticmethod
    def resolve_tts_api_key(provider_key: str | None, api_key_override: str | None, tts_config: dict) -> str:
        if provider_key == 'vllm_omni':
            return api_key_override or ''
        return api_key_override or tts_config.get('api_key', '')

    @staticmethod
    def _is_vllm_omni_tts_enabled(core_config: dict) -> bool:
        return _as_bool(core_config.get('ENABLE_CUSTOM_API'), False) and (
            str(core_config.get('ttsModelProvider') or '').strip() == 'vllm_omni'
        )

    @classmethod
    def _resolve_vllm_omni_runtime_config(cls, core_config: dict) -> tuple[str, str, str]:
        if not cls._is_vllm_omni_tts_enabled(core_config):
            return ('', '', '')
        return (
            str(core_config.get('ttsModelUrl') or '').strip()
            or VLLM_OMNI_DEFAULT_BASE_URL,
            str(core_config.get('ttsModelId') or '').strip()
            or VLLM_OMNI_DEFAULT_MODEL,
            str(core_config.get('ttsVoiceId') or '').strip()
            or 'default',
        )

    def _build_tts_runtime_key(self) -> tuple:
        """Return the effective TTS worker identity for ready-state reuse."""
        try:
            core_config = self._config_manager.get_core_config()
            if core_config.get('DISABLE_TTS', False):
                return ("disabled",)
            has_custom = self._has_custom_tts()
            _, api_key_override, provider_key = get_tts_worker(
                core_api_type=self.core_api_type,
                has_custom_voice=has_custom,
                voice_id=self.voice_id or '',
            )
            tts_config = self._config_manager.get_model_api_config(
                'tts_custom' if has_custom else 'tts_default'
            )
            api_key = self.resolve_tts_api_key(provider_key, api_key_override, tts_config)
            return (
                provider_key,
                self.core_api_type,
                self.voice_id or '',
                bool(getattr(self, "_is_free_preset_voice", False)),
                bool(has_custom),
                tts_config.get('base_url', ''),
                tts_config.get('model', ''),
                self._resolve_vllm_omni_runtime_config(core_config),
                api_key,
            )
        except Exception:
            return (
                "fallback",
                getattr(self, "core_api_type", ""),
                getattr(self, "voice_id", ""),
                bool(getattr(self, "_is_free_preset_voice", False)),
            )

    async def _clear_tts_pipeline(self):
        """Clear the TTS request/response queues and pending caches, stopping the current synthesis.

        Gate is on worker liveness, not ``self.use_tts``: mirror channel
        (e.g. ``mirror_assistant_speech``) feeds the project TTS pipeline
        regardless of ``use_tts``, so a Realtime native voice session
        (``use_tts=False``) can still have a live worker that needs
        interrupting on ``interrupt_audio``.
        """
        if self.tts_thread and self.tts_thread.is_alive():
            while not self.tts_response_queue.empty():
                try:
                    self.tts_response_queue.get_nowait()
                except: # noqa
                    break
            try:
                self.tts_request_queue.put(("__interrupt__", None))
            except Exception as e:
                logger.warning(f"⚠️ 发送TTS中断信号失败: {e}")
            self._reset_tts_stream_normalizer()
            # 等待 TTS worker 处理 __interrupt__ 并 mute 回调（worker 轮询间隔 ~10ms）
            # 然后再次清空响应队列，确保旧 synthesizer 泄漏的音频全部丢弃
            await asyncio.sleep(0.02)
            while not self.tts_response_queue.empty():
                try:
                    self.tts_response_queue.get_nowait()
                except: # noqa
                    break
        async with self.tts_cache_lock:
            self.tts_pending_chunks.clear()
            self._tts_done_pending_until_ready = False
            # Drop only queued-but-unconfirmed TTS text. Already-confirmed
            # audio may still be echoed by STT shortly after an interrupt.
            self._discard_pending_ai_voice_echo()

    @property
    def is_tts_pipeline_ready(self) -> bool:
        """Light health check: TTS worker thread alive and ready, no orchestration."""
        return bool(
            self.tts_thread
            and self.tts_thread.is_alive()
            and self.tts_ready
        )

    async def ensure_tts_pipeline_alive(self) -> None:
        """Light TTS startup helper: spawn worker + handler task if not alive.

        Does NOT wait for ``__ready__`` — callers that need confirmed-ready
        must poll :attr:`is_tts_pipeline_ready` themselves.  Callers that
        only need ``tts_pending_chunks`` to eventually drain do not need
        to wait at all (the handler picks up pending chunks once
        ``tts_ready`` flips).
        """
        if not (self.tts_thread and self.tts_thread.is_alive()):
            self._start_tts_thread()
        if self.tts_handler_task is None or self.tts_handler_task.done():
            self.tts_handler_task = asyncio.create_task(self.tts_response_handler())

    async def _apply_pending_tts_route_after_swap(self) -> None:
        """Apply pending TTS route and reconcile worker state after hot-swap."""
        if self.pending_use_tts is None:
            return
        self.use_tts = self.pending_use_tts
        if self.use_tts:
            await self.ensure_tts_pipeline_alive()

    async def handle_new_message(self):
        """Handle new model output: clear the TTS queue and notify the frontend"""
        if self._takeover_active:
            logger.info("[%s] session takeover active: suppressing ordinary realtime new-message handling", self.lanlan_name)
            return

        # 重置音频重采样器状态（新轮次音频不应与上轮次连续）
        self.audio_resampler.clear()
        await self._clear_tts_pipeline()
        self._tts_done_queued_for_turn = False  # 新轮次重置 TTS 结束信号标记
        self._tts_done_pending_until_ready = False
        # 新一轮开始：清空上一轮 AI 文本累加器（即使上轮 turn end 已清过，
        # proactive abort 等异常路径可能漏清，新轮次起点重置最稳）
        self._current_ai_turn_text = ''

        await self.send_user_activity()

        # 立即生成新的 speech_id，确保新回复不会使用被打断的 ID
        # 这样即使 handle_input_transcript 先于 handle_new_message 执行，
        # 新回复的 audio_chunk 也不会被错误丢弃
        async with self.lock:
            self.current_speech_id = str(uuid4())
            new_sid = self.current_speech_id
            # 必须在 self.lock 内同步翻 _preempted 标记，使新 sid + preempt 对
            # 同样在 self.lock 内复查 is_proactive_preempted 的 prepare_proactive_delivery
            # 原子可见；否则 proactive 会插到 lock 释放 ~ fire() 之间把 user sid
            # 再覆盖成 proactive sid。完整 USER_INPUT 事件仍在锁外 fire，以更新
            # owner/user_sid 并派发订阅者。
            self.state.mark_user_input_preempt()
        await self.state.fire(SessionEvent.USER_INPUT, sid=new_sid)

    async def rotate_speech_id_for_response_done(self):
        """Lightweight sid rotation for realtime providers without server VAD.

        Triggered at OmniRealtimeClient's response.done event (the pass-through of
        Gemini's turn_complete) when ``_has_server_vad=False`` (lanlan.app+free /
        livestream). Without server VAD, ``speech_stopped`` never fires, so
        the canonical ``handle_new_message`` rotation path stays dormant and
        every turn ends up reusing the initial session sid — TTS upstream
        silently drops text after the first ``tts.response.done`` closes
        that sid, and turn 2+ goes silent.

        Why not reuse ``handle_new_message``: that helper rotates sid AND
        clears the TTS pipeline AND fires USER_INPUT. Both side effects are
        correct at ``speech_stopped`` (user just spoke, AI hasn't started,
        leftover TTS belongs to an interrupted prior turn). At
        ``response.done`` they're wrong — leftover TTS is the trailing audio
        of the AI turn that just ended; clearing it would clip the last
        few syllables. ``USER_INPUT`` mischaracterizes the trigger (no user
        input actually happened — this is end-of-AI-turn, not start-of-user).
        Resetting ``audio_resampler`` is safe because the next turn's audio
        is a fresh stream — keeping stale soxr state would only risk a
        boundary artefact at turn 2's first frame.
        """
        if self._takeover_active:
            return
        self.audio_resampler.clear()
        # 必须重置这两个 flag，否则下一轮 ``_request_tts_done_locked`` 会因
        # ``_tts_done_queued_for_turn=True`` 直接 early-return，下一轮的 TTS
        # flush sentinel 永远不入队，server 拿不到 ``tts.flush`` 句尾音频
        # 可能挂在 buffer 里、长句 utterance 不会 finalize。``handle_new_message``
        # 在 speech_stopped 路径也是这样 reset 的（[core.py:1214](main_logic/core.py:1214)），
        # 这里和它对偶。
        self._tts_done_queued_for_turn = False
        self._tts_done_pending_until_ready = False
        async with self.lock:
            self.current_speech_id = str(uuid4())

    async def _focus_inline_decision(self, user_text: str) -> bool:
        """Path A (inline) Focus gate: score the just-arrived user message and
        return whether THIS reply should run thinking-on.

        Scores via the shared ``FocusScorer`` (keyword + cadence + open-thread
        signals), advances ``self.state``'s hysteresis (``update_focus``), and
        returns ``mode is FOCUS``. An explicit topic switch forces an immediate
        exit. Best-effort: any failure degrades to regular (thinking-off) and
        never blocks the user reply. Returns False fast when the master switch
        is off (skips the snapshot cost).
        """
        # Reconcile the badge first — catches a Focus state cleared since the
        # last turn without a FOCUS_EXIT event (clear_focus / master-switch
        # self-clear), even on the early-return path when the switch is now off.
        await self._reconcile_focus_indicator()
        from config import FOCUS_MODE_ENABLED  # live read (re-imported per call)
        if not FOCUS_MODE_ENABLED:
            # Flag flipped off → clear ALL focus residue unconditionally, not
            # just when mode==FOCUS. The leaky accumulator can sit in REGULAR
            # with charge just under the enter bar; if we only cleared on FOCUS,
            # that frozen charge would survive the disabled window and let an
            # unrelated mild cue enter on stale evidence once re-enabled.
            # update_focus self-clears when the (config) switch is off (idempotent).
            await self.state.update_focus(0.0)
            # Reconcile AGAIN after the self-clear: if the switch was flipped off
            # mid-episode, the clear above is silent (no FOCUS_EXIT), so clear the
            # badge this turn rather than waiting for the next one.
            await self._reconcile_focus_indicator()
            return False
        # Per-user master switch (对话设置 → focusCognitionEnabled): the user can
        # turn 凝神 off entirely while the global config flag stays on. Defaults on
        # when unset. The master emotion read is independent (its own pipeline) and
        # is NOT affected by this gate.
        try:
            focus_user_enabled = bool(
                (await aload_global_conversation_settings()).get('focusCognitionEnabled', True)
            )
        except Exception:
            focus_user_enabled = True
        if not focus_user_enabled:
            # Hard-clear (not update_focus(0.0)): with the config flag still on,
            # update_focus only DECAYS by retention, leaving residual charge and a
            # FOCUS mode lingering for a turn or two — so the badge/glow wouldn't
            # drop until the charge bled below exit. clear_focus zeroes everything
            # silently (no FOCUS_EXIT) so 凝神 turns off at once when the user asks.
            await self.state.clear_focus()
            await self._reconcile_focus_indicator()
            return False
        if not (user_text and user_text.strip()):
            return False
        try:
            from config.prompts.prompts_focus import detect_topic_switch
            # Focus scores the user's MESSAGE, not the screen: the inline
            # signals (vulnerability keywords + reply cadence) read user_text
            # and the scorer's own cadence buffer — never the activity snapshot
            # (silence / open_thread are idle-only). So Focus is
            # privacy-independent BY CONSTRUCTION and must NOT be gated on
            # privacy mode: understanding the user's emotional state from what
            # they typed is core to an AI companion. Privacy mode governs only
            # SCREEN / app-state visibility (see docs/contributing/
            # developer-notes.md rule 6). Hence no snapshot fetch here.
            # emotion 信号读 master 情绪画像的最近读数（异步算、滞后一拍，与
            # cadence 用历史一脉相承）。画像关 / 还没算过时 latest 为 None，
            # FocusScorer 让 emotion 信号自动退出加权、退回 keyword+cadence。
            # 读 _note_user_turn 在「本轮 analyze 启动前」存的快照，保证 emotion
            # 信号确定性地滞后一拍——当前 turn 不消费它自己即将算出的读数（否则快
            # tier / 中途 await 让 fire-and-forget analyze 抢先更新 latest，同一条
            # 消息会时而读旧读数时而读自己的，违反 scorer 的 lag-one-turn 契约）。
            emotion_reading = getattr(self, '_focus_emotion_reading', None)
            scored = self._focus_scorer.score(
                user_text=user_text, emotion_reading=emotion_reading,
            )
            topic_changed = detect_topic_switch(user_text)
            mode = await self.state.update_focus(
                scored.score, topic_changed=topic_changed,
            )
            if mode is CognitionMode.FOCUS:
                if not self._focus_artifacts_pending:
                    # 首次进入本 episode：记下历史长度，退出时只清这之后（episode
                    # 期间）产生的闭合 tool call，不动 Focus 之前的普通工具调用。
                    hist = getattr(
                        getattr(self, "session", None), "_conversation_history", None
                    )
                    self._focus_artifacts_history_start = (
                        len(hist) if isinstance(hist, list) else None
                    )
                # arm：本 episode 的 thinking/tool 残留，退出时要清（见下）。
                self._focus_artifacts_pending = True
            # Log every turn (incl. REGULAR) so tuning can watch the charge
            # accumulate toward FOCUS_CHARGE_ENTER, not just the entry moment.
            logger.info(
                "[%s] 凝神 inline: score=%.2f charge=%s mode=%s signals=%s",
                self.lanlan_name, scored.score,
                self.state.snapshot().get("focus_charge"), mode.value, scored.signals,
            )
            # Stream the post-turn charge for the frontend edge glow.
            await self._push_focus_charge(self.state.snapshot().get("focus_charge"))
            # 退出边沿同步清理（此刻在 stream_text 之前、无并发）：inline 自然退出
            # （decayed / hard_cap / topic_switch）都在此命中并清；FOCUS 维持时是
            # no-op（仍在 episode 内）。silent early-return 路径由 reconcile 兜底。
            await self._maybe_purge_focus_artifacts()
            return mode is CognitionMode.FOCUS
        except Exception as e:
            logger.warning("[%s] focus inline decision failed (degrading to regular): %s",
                           self.lanlan_name, e)
            # Don't leave a stale FOCUS episode if score / update_focus raised
            # mid-episode — degrade cleanly to regular.
            try:
                if self.state.mode is CognitionMode.FOCUS:
                    await self.state.update_focus(0.0, topic_changed=True)
            except Exception as _exit_err:
                logger.debug("[%s] focus inline fail-exit also failed: %s",
                             self.lanlan_name, _exit_err)
            return False

    def _focus_idle_thinking(self) -> bool:
        """Path B (idle) — does THIS proactive reply run thinking-on?

        Read-only: returns whether the session is currently in Focus. A
        proactive turn never raises the charge, so there is nothing to score
        here. The charge decay happens AFTER the turn, in
        ``_focus_idle_cooldown`` (it needs to know whether the turn actually
        spoke). Returns False when the master switch is off. Privacy-independent
        (no snapshot, no screen signals).
        """
        from config import FOCUS_MODE_ENABLED  # live read
        if not FOCUS_MODE_ENABLED:
            return False
        # Honor the per-user master switch here too (defense in depth): if the
        # user turned 凝神 off mid-episode, a proactive turn must not keep running
        # thinking-on before the next inline turn clears the episode.
        try:
            if not bool(load_global_conversation_settings().get('focusCognitionEnabled', True)):
                return False
        except Exception:
            # Fail-open: a settings-read hiccup must not silently disable an
            # active Focus episode — fall through to the live state.mode below.
            pass
        return self.state.mode is CognitionMode.FOCUS

    async def _on_focus_transition(self, event: SessionEvent, payload: dict) -> None:
        """SM subscriber for FOCUS_ENTER / FOCUS_EXIT — immediate badge update on
        the normal hysteresis path. Delegates to the idempotent push.

        NB: history artifact arm/purge is deliberately NOT done here.
        ``_dispatch_subscribers`` fires async callbacks fire-and-forget
        (``ensure_future``), so a FOCUS_EXIT-driven purge could race the reply
        stream. Arming + purging happen SYNCHRONOUSLY on the inline decision path
        and the per-turn reconcile instead (see ``_maybe_purge_focus_artifacts``)."""
        await self._push_focus_indicator(event is SessionEvent.FOCUS_ENTER)

    async def _maybe_purge_focus_artifacts(self) -> None:
        """On the edge where Focus mode turns OFF, wipe the thinking + closed
        tool-call traces the just-ended episode left in history, so they can't
        bias the REGULAR reply that follows (or a fresh session).

        Called SYNCHRONOUSLY from two places (NOT the async FOCUS_EXIT event,
        which fires fire-and-forget and could race the stream):
          - the inline decision, right after update_focus and BEFORE stream_text
            — catches inline exits (decayed / hard_cap / topic_switch);
          - the per-turn _reconcile_focus_indicator — catches the silent exits
            (master switch / per-user setting / privacy self-clear / clear_focus)
            and a proactive-cooldown exit on the next inline turn.

        Idempotent: runs once per episode, only when Focus was actually entered
        (``_focus_artifacts_pending``) AND the mode has dropped back to REGULAR.
        All call sites sit OUTSIDE the stream boundary, so there is no concurrent
        history mutation. Only text sessions (OmniOfflineClient) keep history.
        """
        if not self._focus_artifacts_pending:
            return
        if self.state.mode is CognitionMode.FOCUS:
            return  # 仍在 focus，残留还在用，不能清
        self._focus_artifacts_pending = False
        start = self._focus_artifacts_history_start or 0
        self._focus_artifacts_history_start = None
        sess = getattr(self, "session", None)
        if not isinstance(sess, OmniOfflineClient):
            return
        history = getattr(sess, "_conversation_history", None)
        if not history:
            return
        try:
            removed = _purge_closed_tool_calls(history, start=start)
            if removed:
                logger.info(
                    "[%s] 凝神退出：从历史清除 thinking/已闭合 tool call 残留 %d 条",
                    self.lanlan_name, removed,
                )
        except Exception as e:
            logger.warning("[%s] 凝神退出历史清理失败(忽略): %s", self.lanlan_name, e)

    async def _reconcile_focus_indicator(self) -> None:
        """Catch Focus states dropped WITHOUT a FOCUS_EXIT event (clear_focus
        history-wipe, master-switch / privacy self-clear in update_focus) so the
        badge AND the charge glow can't get stuck on. Called once per turn. The
        charge push reads the (now-cleared → 0) snapshot, so a silent exit that
        only reconciles the binary state still tells the glow to fade out. Also
        the catch-all purge point for silent Focus exits (no FOCUS_EXIT event)."""
        await self._push_focus_indicator(self.state.mode is CognitionMode.FOCUS)
        await self._push_focus_charge()
        await self._maybe_purge_focus_artifacts()

    async def resync_focus_for_new_window(self) -> None:
        """Re-emit ALL focus signals to a freshly-connected window (greeting_check):
        the charge glow, the binary focus_state, AND the transient thinking pulse.
        ``force=True`` bypasses the idempotent cache so a window opened mid-FOCUS
        (or mid-thinking) gets the current indicator even though no enter/exit /
        thinking transition fires for it."""
        await self._push_focus_charge()
        await self._push_focus_indicator(self.state.mode is CognitionMode.FOCUS, force=True)
        await self._push_focus_thinking(getattr(self, "_focus_thinking_active", False), force=True)

    async def _push_focus_indicator(self, active: bool, *, force: bool = False) -> None:
        """Mirror the cognition indicator (focus_state) to the frontend (drives
        the screen-reader status node; the visible glow is charge-driven). Idempotent
        on the cached state so the event path and the per-turn reconcile never
        double-fire — except ``force=True`` (a new window re-sync) re-pushes even
        when unchanged. Ephemeral UI state: pushed live over the websocket and
        mirrored to the sync queue for cross-server, but never persisted to history.
        Best-effort: a ws failure must never disturb the caller."""
        # getattr default guards bypass-__init__ constructions (bare test mgrs,
        # cross-server / unpickled managers) — they simply have no badge to sync.
        if not force and active == getattr(self, "_focus_indicator_active", False):
            return
        self._focus_indicator_active = active
        msg = {"type": "focus_state", "active": active}
        try:
            self.sync_message_queue.put({"type": "json", "data": msg})
        except Exception as e:
            logger.debug("[%s] focus_state sync-queue push failed: %s", self.lanlan_name, e)
        try:
            ws = self.websocket
            if ws and hasattr(ws, 'client_state') and ws.client_state == ws.client_state.CONNECTED:
                if self.websocket_lock:
                    async with self.websocket_lock:
                        await ws.send_json(msg)
                else:
                    await ws.send_json(msg)
        except Exception as e:
            logger.debug("[%s] focus_state ws push failed: %s", self.lanlan_name, e)

    async def _push_focus_charge(self, charge: Optional[float] = None) -> None:
        """Stream the live Focus charge (0..1) to the frontend so the edge glow
        can scale continuously: onset at FOCUS_CHARGE_EXIT, the non-linear jump +
        breathing at FOCUS_CHARGE_ENTER, peak toward FOCUS_CHARGE_CAP. Carries the
        wall-clock stamp so the frontend extrapolates the same time decay between
        pushes for a smooth fade (no per-second server spam). Pushed on every
        charge change AND on (re)connect so a freshly-opened window (e.g. the
        separate /chat_full window) lands on the correct brightness immediately.
        Ephemeral, ws + sync-queue only, never persisted. Best-effort.

        ``at_ms`` is the charge's LAST-CHANGE wall-clock (not "now"), so the
        frontend extrapolates the time decay from the right moment — a reconnect
        after a long gap must not replay a stale un-decayed charge as if current.
        When Focus is disabled we push 0 (not skip) so a lit glow can't linger."""
        from config import FOCUS_MODE_ENABLED  # live read
        try:
            snap = self.state.snapshot()
        except Exception:
            snap = {}
        if not FOCUS_MODE_ENABLED:
            charge, at = 0.0, 0.0
        else:
            if charge is None:
                try:
                    charge = float(snap.get("focus_charge") or 0.0)
                except Exception:
                    charge = 0.0
            at = snap.get("focus_charge_at") or 0.0
        at_ms = int(at * 1000) if at and at > 0 else int(time.time() * 1000)
        msg = {"type": "focus_charge", "charge": round(max(0.0, float(charge)), 4),
               "at_ms": at_ms}
        try:
            self.sync_message_queue.put({"type": "json", "data": msg})
        except Exception as e:
            logger.debug("[%s] focus_charge sync-queue push failed: %s", self.lanlan_name, e)
        try:
            ws = self.websocket
            if ws and hasattr(ws, 'client_state') and ws.client_state == ws.client_state.CONNECTED:
                if self.websocket_lock:
                    async with self.websocket_lock:
                        await ws.send_json(msg)
                else:
                    await ws.send_json(msg)
        except Exception as e:
            logger.debug("[%s] focus_charge ws push failed: %s", self.lanlan_name, e)

    async def _push_focus_thinking(self, active: bool, *, force: bool = False) -> None:
        """Pulse a transient "model is thinking" signal to the frontend so the
        chat history can show a thinking-dots bubble while a Focus turn runs
        thinking-on but hasn't emitted any visible content yet. Pushed True right
        before such a turn streams, cleared (False) the moment the first visible
        chunk lands (send_lanlan_response) or the turn ends. Idempotent on the
        cached state so per-chunk callers can clear blindly without spamming —
        except ``force=True`` (a new-window re-sync) re-pushes even when
        unchanged, mirroring _push_focus_indicator so a window opened mid-thinking
        lands on the current bubble. Ephemeral: ws + sync-queue only, never
        persisted. Best-effort — a ws failure must never disturb the caller.

        getattr default guards bypass-__init__ constructions (bare test mgrs,
        cross-server / unpickled managers) — they simply have no bubble to sync."""
        if not force and active == getattr(self, "_focus_thinking_active", False):
            return
        self._focus_thinking_active = active
        msg = {"type": "focus_thinking", "active": active}
        try:
            self.sync_message_queue.put({"type": "json", "data": msg})
        except Exception as e:
            logger.debug("[%s] focus_thinking sync-queue push failed: %s", self.lanlan_name, e)
        try:
            ws = self.websocket
            if ws and hasattr(ws, 'client_state') and ws.client_state == ws.client_state.CONNECTED:
                if self.websocket_lock:
                    async with self.websocket_lock:
                        await ws.send_json(msg)
                else:
                    await ws.send_json(msg)
        except Exception as e:
            logger.debug("[%s] focus_thinking ws push failed: %s", self.lanlan_name, e)

    async def handle_thinking_active(self, active: bool = True) -> None:
        """Session callback: the model started (active=True) or finished
        (active=False) emitting reasoning/thinking chunks for the current stream
        (the text is filtered out upstream; only this boolean pulse reaches us).
        Drives the chat thinking-dots bubble for ANY reasoning turn — decoupled
        from the Focus inline decision. A Focus turn pre-pulses the bubble before
        streaming (still works, idempotent); a non-Focus turn whose provider
        reasons internally pulses here on its first reasoning chunk. The bubble
        is cleared on the first visible token (send_lanlan_response), when the
        text turn ends (the unconditional finally in the text path), or — for a
        proactive/greeting/avatar turn that reasons but commits no visible text —
        by the active=False clear from prompt_ephemeral's finally. Best-effort —
        idempotent via ``_push_focus_thinking``'s cached state."""
        await self._push_focus_thinking(active)

    def _make_thinking_active_callback(self, session_ref):
        """Bind ``handle_thinking_active`` to ONE specific OmniOfflineClient so a
        reasoning pulse only drives the bubble while that client is the live
        session. The thinking bubble is a single per-window surface; a pulse from
        a NON-current client — a pending hot-swap session, or a just-demoted old
        session still draining a stream after the swap — must not light or clear
        the current window (CodeRabbit). The live session always matches, so its
        pulses/clears pass through unchanged; everything else is a silent no-op.
        getattr default tolerates call-time teardown where self.session is None."""
        async def _on_thinking_active(active: bool) -> None:
            if session_ref is getattr(self, "session", None):
                await self.handle_thinking_active(active)
        return _on_thinking_active

    async def _focus_idle_cooldown(
        self, *, replied: bool, episode_token, turn_token=None,
    ) -> None:
        """Path B (idle) Focus COOLDOWN: decay the charge once, after a Phase-2
        proactive turn finishes. A proactive turn NEVER raises the charge —
        entering and sustaining Focus is driven solely by the inline path (the
        user's own messages). This only lets an active episode cool down.

        Decay rate by whether the turn actually spoke, via two config knobs:
          * ``replied=True`` (a Phase-2 proactive reply was delivered) →
            ``FOCUS_IDLE_REPLIED_RETENTION``.
          * ``replied=False`` (Phase-2 reached but produced no reply — empty /
            aborted) → ``FOCUS_IDLE_SILENT_RETENTION``.
        Currently both are tuned to the same value (0.8), so speaking and silence
        cool the episode at one gentle rate; the split is kept for future
        re-tuning (invariant: replied <= silent). Focus persistence is driven by
        how often a proactive turn fires, not raw time.

        ``episode_token`` / ``turn_token`` pin the decay to the exact focus state
        this proactive turn observed when it made its thinking decision — the
        episode id and the turn count at Phase 2. The decay is SKIPPED unless the
        SM is STILL in that same episode AND no inline turn has landed since:
          * ``not replied`` AND the user already took over (``owner is USER``) →
            the user spoke during an UNDELIVERED proactive turn and aborted it
            before it said anything. The inline path marks USER_INPUT
            (owner→USER) the moment they speak, but its focus update lands LATER
            (after mini-game / agent-callback handling), so the episode + turn
            token still match here. This aborted proactive tick must not decay
            the charge before the user's own message is scored — that
            (user-driven) episode is the inline path's to update. owner stays
            USER through PROACTIVE_DONE (which only clears a PROACTIVE owner), so
            it is still observable at this point. A turn that DID reply
            (``replied=True``) genuinely spent the episode and still takes the
            replied retention even if the user fired back fast enough to flip the
            owner first; once the inline update actually lands the turn-token
            guard below takes over.
          * ``episode_token is None`` → the turn observed REGULAR (no active
            episode). There is nothing to cool, and a proactive tick must not
            erode the pre-entry accumulator the inline path is building toward
            ENTER — entering Focus is the inline path's job alone.
          * episode id changed → the inline path exited and/or entered a new
            episode while this proactive request was finishing.
          * turn count changed → the inline path recharged THIS same episode (a
            user message landed mid-flight). A stale proactive tick must not
            decay that fresh, user-driven charge.

        Decays with ``count_turn=False`` so a proactive tick never consumes a
        hard-cap turn slot (that bounds inline turns). Pure charge cooldown:
        privacy-independent, no snapshot. Best-effort; never blocks the exit.
        """
        # Idle-path counterpart to the inline reconcile — keeps the badge honest
        # on proactive-only stretches (idempotent).
        await self._reconcile_focus_indicator()
        from config import (  # live read
            FOCUS_MODE_ENABLED,
            FOCUS_IDLE_REPLIED_RETENTION,
            FOCUS_IDLE_SILENT_RETENTION,
        )
        try:
            if not FOCUS_MODE_ENABLED:
                # Master switch off → update_focus self-clears any residue.
                await self.state.update_focus(0.0)
                # Same-turn badge clear on a mid-episode switch-off (symmetric
                # with the inline path); the self-clear emits no FOCUS_EXIT.
                await self._reconcile_focus_indicator()
                return
            # User took over an UNDELIVERED turn: the user spoke during the
            # proactive request (USER_INPUT flipped owner→USER) and aborted it
            # before it said anything, but their inline focus update has not
            # landed yet, so the episode/turn token below would still match.
            # Hand the charge to the imminent inline turn instead of decaying it
            # with this aborted proactive tick.
            #   Gated on ``not replied``: a turn that DID commit a reply
            # (``replied=True``) genuinely spent the episode and must still take
            # the replied retention even if the user fired back fast enough to
            # flip the owner before this cooldown ran — owner==USER alone would
            # wrongly let quick replies after a successful proactive chat skip
            # their decay. (Once the inline focus update actually lands, the
            # episode/turn-token guard below takes over.)
            if not replied and self.state.owner is TurnOwner.USER:
                logger.debug(
                    "[%s] focus idle cooldown skipped: user took over an undelivered turn",
                    self.lanlan_name,
                )
                return
            # Only cool an episode this turn actually observed — never the
            # REGULAR pre-entry accumulator (entering Focus is inline-only).
            if episode_token is None:
                logger.debug(
                    "[%s] focus idle cooldown skipped: no active episode observed",
                    self.lanlan_name,
                )
                return
            # Race guard: skip if the focus state moved since this turn observed
            # it — a different episode (inline exited / re-entered) or a fresh
            # inline turn that recharged this same episode (turn count bumped).
            snap = self.state.snapshot()
            current_episode = snap.get("focus_episode_id")
            current_turn = snap.get("focus_turn_count")
            if current_episode != episode_token or (
                turn_token is not None and current_turn != turn_token
            ):
                logger.debug(
                    "[%s] focus idle cooldown skipped: focus state changed "
                    "(episode %s→%s, turn %s→%s)",
                    self.lanlan_name, episode_token, current_episode,
                    turn_token, current_turn,
                )
                return
            retention = (
                FOCUS_IDLE_REPLIED_RETENTION if replied
                else FOCUS_IDLE_SILENT_RETENTION
            )
            # score=0 + retention<1 ⇒ charge can only decay (never cross the
            # enter bar from REGULAR), so this can't ENTER Focus — only cool an
            # inline-driven episode toward the exit bar. count_turn=False keeps
            # it off the hard-cap turn budget.
            mode = await self.state.update_focus(
                0.0, retention_override=retention, count_turn=False,
            )
            logger.info(
                "[%s] 凝神 idle(cooldown replied=%s): charge=%s mode=%s",
                self.lanlan_name, replied,
                self.state.snapshot().get("focus_charge"), mode.value,
            )
            await self._push_focus_charge(self.state.snapshot().get("focus_charge"))
            # 若这次 cooldown 把 Focus 衰减出去(→REGULAR)，立即清 episode 残留：
            # proactive/greeting 的 prompt_ephemeral 会在下个 inline turn 之前就从
            # _conversation_history 构建、且不走 reconcile，必须赶在它前面清掉，否则
            # 会把刚结束的 Focus tool-call/reasoning 残留带进随后的 REGULAR 轮。
            await self._maybe_purge_focus_artifacts()
        except Exception as e:
            logger.warning("[%s] focus idle cooldown failed (degrading to regular): %s",
                           self.lanlan_name, e)
            try:
                if self.state.mode is CognitionMode.FOCUS:
                    await self.state.update_focus(0.0, topic_changed=True)
            except Exception as _exit_err:
                logger.debug("[%s] focus idle fail-exit also failed: %s",
                             self.lanlan_name, _exit_err)

    async def handle_text_data(
        self,
        text: str,
        is_first_chunk: bool = False,
        *,
        ui_enabled: bool = True,
        tts_enabled: bool = True,
    ):
        """Text callback: handles text display and TTS (for text mode).

        The ``ui_enabled`` / ``tts_enabled`` split is used by OmniOfflineClient's
        long-reply summary path: the tail text after the cutover goes to UI only
        (keeping the frontend "show full text"), while the condensed version from
        the summary LLM goes to TTS only (keeping TTS from reading the whole
        tail). Both flags off is also consistent — going to neither UI nor TTS
        equals discarding the segment, so return immediately.
        """
        if self._takeover_active:
            logger.info("[%s] session takeover active: dropping ordinary realtime text chunk len=%d", self.lanlan_name, len(text or ""))
            return

        if not ui_enabled and not tts_enabled:
            return

        # 主动搭话 race guard：prompt_ephemeral 路径会设置 _proactive_expected_sid
        # contextvar。若其与 current_speech_id 不一致，说明用户已在 proactive
        # 生成期间打断并换了 sid，本 chunk 属于已被作废的 proactive 轮次，必须
        # 整体丢弃（含前端显示和 TTS），避免污染用户当前轮次。user stream_text
        # 在自己的 task 里 contextvar 为 None，不受影响。
        expected_sid = _proactive_expected_sid.get()
        if expected_sid is not None and expected_sid != self.current_speech_id:
            logger.debug(
                "handle_text_data drop: expected_sid=%s current_sid=%s len=%d",
                expected_sid, self.current_speech_id, len(text),
            )
            return

        # 如果是新消息的第一个chunk，清空TTS队列和缓存以打断之前的语音。
        # summary epilogue 触发的 TTS-only 注入 is_first_chunk=False，不会
        # 误清掉本轮已经播放/排队的 prefix 音频。
        #
        # 注意：这里**不**为 recall 占位语音（filler）开例外。filler 走独立 worker
        # sid 并在检索期间就立即 flush + 经 tts_response_handler 发往前端，正文首
        # chunk 到达时 filler 早已送达，pending / response_queue 里不再有它，清理碰
        # 不到。反过来，这个首包清理在某些路径（如 no-server-VAD 的 response.done
        # 只 rotate sid、不清 TTS）是下一个唯一的打断点，若为 filler 跳过会让上一轮
        # 残留音频漏清、与新轮重叠，破坏 barge-in（Codex P1）。故保持无条件清理。
        if is_first_chunk and self.use_tts and tts_enabled:
            async with self.tts_cache_lock:
                self.tts_pending_chunks.clear()
                self._discard_pending_ai_voice_echo()

            if self.tts_thread and self.tts_thread.is_alive():
                # 清空响应队列中待发送的音频数据
                while not self.tts_response_queue.empty():
                    try:
                        self.tts_response_queue.get_nowait()
                    except: # noqa
                        break

        # 文本模式下，无论是否使用TTS，都要发送文本到前端显示
        if ui_enabled:
            await self.send_lanlan_response(
                text,
                is_first_chunk,
                remember_voice_echo=not self.use_tts,
            )

        # 如果配置了TTS，将文本发送到TTS队列或缓存
        if self.use_tts and tts_enabled:
            async with self.tts_cache_lock:
                # 检查TTS是否就绪
                if self.tts_ready and self.tts_thread and self.tts_thread.is_alive():
                    # TTS已就绪，直接发送
                    try:
                        self._enqueue_tts_text_chunk(self.current_speech_id, text)
                    except Exception as e:
                        logger.warning(f"⚠️ 发送TTS请求失败: {e}")
                else:
                    # TTS未就绪，先缓存（规范化延迟到 _flush_tts_pending_chunks）
                    self.tts_pending_chunks.append((self.current_speech_id, text))
                    if len(self.tts_pending_chunks) == 1:
                        logger.info("TTS未就绪，开始缓存文本chunk...")
                    # 仅在回复首 chunk 尝试拉起，避免每个 chunk 都重试
                    if is_first_chunk and self.tts_thread and not self.tts_thread.is_alive():
                        self._respawn_tts_worker()

    def _set_conversation_turn_language(self, language: str | None) -> None:
        dispatcher = getattr(self, '_turn_dispatcher', None)
        if dispatcher is not None:
            dispatcher.set_language(language)

    def _note_user_turn(self, *, text: str | None = None, now: float | None = None) -> None:
        # Master 情绪画像：异步分析用户这轮说的话（节流 + 开关都在 tracker 内部）。
        # 语音转写 / 文本输入两条路径的对偶 chokepoint。fire-and-forget、best-effort，
        # 绝不阻塞 turn 记录、不让分析异常冒泡。
        _me = getattr(self, '_master_emotion', None)
        if _me is not None:
            # 先 snapshot「本轮 analyze 启动前」的读数，供 _focus_inline_decision 用，
            # 保证 emotion 信号滞后一拍（当前 turn 不消费下面这次 analyze 的结果）。
            # 读 .latest 已含 TTL / 开关 gate。
            self._focus_emotion_reading = _me.latest
        if text and text.strip() and _me is not None:
            try:
                self._fire_task(_me.analyze(text, now=now))
            except Exception as _me_err:
                logger.debug("[%s] master emotion fire failed: %s", self.lanlan_name, _me_err)

        dispatcher = getattr(self, '_turn_dispatcher', None)
        if dispatcher is not None:
            dispatcher.note_user_message(text=text, now=now)
            return
        if now is None:
            self._activity_tracker.on_user_message(text=text)
        else:
            self._activity_tracker.on_user_message(text=text, now=now)

    def _note_ai_turn(self, *, text: str | None = None, now: float | None = None) -> None:
        dispatcher = getattr(self, '_turn_dispatcher', None)
        if dispatcher is not None:
            dispatcher.note_ai_message(text=text, now=now)
            return
        if now is None:
            self._activity_tracker.on_ai_message(text=text)
        else:
            self._activity_tracker.on_ai_message(text=text, now=now)

    def _flush_ai_turn_text_to_tracker(self) -> None:
        """Flush the per-turn AI text buffer into conversation turn sinks.

        Called from each AI-turn-end exit point — there are three:
          - ``_emit_turn_end`` for regular replies (and truncate-recovery)
          - ``handle_proactive_complete`` for the agent direct-reply path
          - ``finish_proactive_delivery`` for /api/proactive_chat success

        The activity sink runs the question heuristic over the text and
        (when text is non-empty) bumps ``_conv_seq`` for open_threads cache
        invalidation. Other sinks, such as background topic collection, see
        the same turn without living inside ``UserActivityTracker``.
        """
        self._note_ai_turn(text=self._current_ai_turn_text or None)
        self._current_ai_turn_text = ''

    async def handle_proactive_complete(self, content_committed: bool = True):
        """Lightweight completion for proactive (agent callback) replies.

        Only flushes TTS and sends turn_end to the frontend so that the
        realistic-queue buffer is flushed.  Does NOT trigger hot-swap,
        analyze_request, or agent-callback re-delivery — those belong
        exclusively to user-initiated conversation turns.
        """
        if not content_committed:
            logger.debug("[%s] handle_proactive_complete: no content committed, skipping completion flush", self.lanlan_name)
            return
        # Activity tracker flush：proactive 也算 AI 在说话。和 _emit_turn_end
        # 对称，让 seconds_since_ai_msg 不分主动/被动。proactive 文本同样走过
        # send_lanlan_response（finish_proactive_delivery 内部会调），所以
        # _current_ai_turn_text 已经累加好。
        self._flush_ai_turn_text_to_tracker()
        if self.use_tts and self.tts_thread and self.tts_thread.is_alive():
            try:
                await self._request_tts_done_for_turn("handle_proactive_complete")
            except Exception as e:
                logger.warning(f"⚠️ 发送TTS结束信号失败 (proactive): {e}")
        if self.sync_message_queue:
            self.sync_message_queue.put({'type': 'system', 'data': 'turn end agent_callback'})
        try:
            if self.websocket and hasattr(self.websocket, 'client_state') and self.websocket.client_state == self.websocket.client_state.CONNECTED:
                await self.websocket.send_json({'type': 'system', 'data': 'turn end agent_callback'})
                logger.debug("[%s] handle_proactive_complete: turn_end (agent_callback) sent to frontend", self.lanlan_name)
            else:
                logger.warning("[%s] handle_proactive_complete: websocket not connected, turn_end NOT sent", self.lanlan_name)
        except Exception as e:
            logger.warning("[%s] handle_proactive_complete: WS send turn_end error: %s", self.lanlan_name, e)

    async def _emit_turn_end(self, active_request_id) -> None:
        """Send the turn end signal to both sync_message_queue and the WebSocket,
        passing ``_pending_turn_meta`` through both channels before clearing it.
        Shared by two paths:
        - ``handle_response_complete`` normal completion
        - ``handle_response_discarded``'s truncate-recovery / too-long-final
        Unified semantics: sync queue and WS carry the same meta, avoiding one
        having meta while the other doesn't."""
        turn_end_msg: dict = {'type': 'system', 'data': 'turn end'}
        pending_meta = self._pending_turn_meta
        if pending_meta:
            turn_end_msg['meta'] = pending_meta
            self._pending_turn_meta = None
        if active_request_id:
            turn_end_msg['request_id'] = active_request_id
        self.sync_message_queue.put(turn_end_msg)
        try:
            if self.websocket and hasattr(self.websocket, 'client_state') and self.websocket.client_state == self.websocket.client_state.CONNECTED:
                ws_msg = {
                    'type': 'system',
                    'data': 'turn end',
                    'request_id': active_request_id,
                }
                if 'meta' in turn_end_msg:
                    ws_msg['meta'] = turn_end_msg['meta']
                await self.websocket.send_json(ws_msg)
        except Exception as e:
            logger.error(f"💥 WS Send Turn End Error: {e}")
        # Activity tracker flush：AI 刚结束一轮（普通完成 + truncate-recovery 都
        # 走这里）。text 用于 unfinished_thread 检测——tracker 跑问号启发式决定
        # 要不要开 5min 跟进窗口；为 None 时不开窗，但仍更新 seconds_since_ai_msg。
        self._flush_ai_turn_text_to_tracker()

    async def _emit_agent_callback_turn_end(self, active_request_id) -> None:
        turn_end_msg: dict = {'type': 'system', 'data': 'turn end agent_callback'}
        if active_request_id:
            turn_end_msg['request_id'] = active_request_id
        self.sync_message_queue.put(turn_end_msg)
        try:
            if self.websocket and hasattr(self.websocket, 'client_state') and self.websocket.client_state == self.websocket.client_state.CONNECTED:
                await self.websocket.send_json(turn_end_msg)
        except Exception as e:
            logger.error(f"💥 WS Send Agent Callback Turn End Error: {e}")

    def _mark_magic_command_image_drop_request(self, request_id: object) -> None:
        request_id_str = str(request_id or "")
        if not request_id_str or request_id_str in self._magic_command_image_drop_request_ids:
            return
        self._magic_command_image_drop_request_ids.add(request_id_str)
        self._magic_command_image_drop_request_order.append(request_id_str)
        while len(self._magic_command_image_drop_request_order) > _MAGIC_COMMAND_IMAGE_DROP_REQUEST_MAX:
            stale_request_id = self._magic_command_image_drop_request_order.popleft()
            self._magic_command_image_drop_request_ids.discard(stale_request_id)

    def _should_drop_magic_command_image(self, request_id: object) -> bool:
        request_id_str = str(request_id or "")
        return bool(request_id_str and request_id_str in self._magic_command_image_drop_request_ids)

    async def handle_response_complete(self):
        """Qwen completion callback: handles the Core API's response-complete event, including TTS and hot-swap logic"""
        if self._takeover_active:
            logger.info("[%s] session takeover active: dropping ordinary realtime response completion", self.lanlan_name)
            await self._clear_tts_pipeline()
            self._pending_turn_meta = None
            self._current_ai_turn_text = ""
            self._active_text_request_id = None
            return

        active_request_id = self._active_text_request_id

        if self.use_tts and self.tts_thread and self.tts_thread.is_alive():
            logger.info("📨 Response complete (LLM 回复结束)")
            try:
                await self._request_tts_done_for_turn("handle_response_complete")
            except Exception as e:
                logger.warning(f"⚠️ 发送TTS结束信号失败: {e}")
        try:
            await self._emit_turn_end(active_request_id)
        finally:
            # Compare-and-clear：仅在共享字段仍是本轮快照时才清空，避免
            # 抹掉用户在 turn end 发出前提交的新轮 request_id。
            if self._active_text_request_id == active_request_id:
                self._active_text_request_id = None

        await self._finalize_turn_after_emit()

    async def _finalize_turn_after_emit(self) -> None:
        """Unified wrap-up after turn end: renew/prewarm decision + agent callback delivery.

        Shared by ``handle_response_complete`` and the recovery / too-long-final
        branches of ``handle_response_discarded``, so that consecutive
        RESPONSE_LENGTH_TRUNCATED / RESPONSE_TOO_LONG runs don't skip session
        archiving/prewarming and fall into the "context grows → keeps truncating
        and recovering" loop.
        """
        # ── 热切换逻辑 ─────────────────────────────────────────────────────────
        # 正在切换过程中则跳过所有热切换判断
        if not self.is_hot_swap_imminent:
            try:
                # 1. 轮次 / 上下文 token 任一满足 → 准备新 session + 记忆归档。
                #    （已删除 elapsed >= 40s 的纯时间触发：长时间发呆不应强制
                #     归档 cache，由 turn / token 真实驱动。）
                if hasattr(self, 'is_preparing_new_session') and not self.is_preparing_new_session:
                    _turn_threshold_met = self._session_turn_count >= SESSION_TURN_THRESHOLD
                    # Session 历史 token 总量阈值。turn-end 后的冷路径，
                    # sync count_tokens 即可（10 条消息合计 < 50ms）。
                    # m.content 在多模态消息下是 list[dict]（含 image_url base64）；
                    # 直接 str() 会把 base64 一起算进 budget，带图对话会被过早判定。
                    # 这里只统计可见文本部分。
                    if isinstance(self.session, OmniOfflineClient):
                        from utils.tokenize import count_tokens as _ct

                        def _budget_text(message) -> str:
                            content = getattr(message, "content", "")
                            if isinstance(content, str):
                                return content
                            if isinstance(content, list):
                                return "\n".join(
                                    str(part.get("text") or "").strip()
                                    for part in content
                                    if isinstance(part, dict)
                                    and str(part.get("type") or "") in {"text", "input_text", "output_text"}
                                )
                            return ""

                        _ctx_total = sum(
                            _ct(_budget_text(m))
                            for m in self.session._conversation_history[1:]
                        )
                        _ctx_threshold_met = _ctx_total >= SESSION_ARCHIVE_TRIGGER_TOKENS
                    else:
                        _ctx_threshold_met = False
                    if _turn_threshold_met or _ctx_threshold_met:
                        logger.info(f"[{self.lanlan_name}] Main Listener: Uptime threshold met. Marking for new session preparation.")
                        self.is_preparing_new_session = True
                        self.summary_triggered_time = datetime.now()
                        self.message_cache_for_new_session = []
                        self.initial_cache_snapshot_len = 0
                        self.initial_next_session_context_snapshot_len = 0
                        self.sync_message_queue.put({'type': 'system', 'data': 'renew session'})

                # 2. agent 任务结果即时触发（无需等待 40s）：有挂起的额外提示 → 立刻启动预热
                has_extra = bool(getattr(self, 'pending_extra_replies', None))
                if has_extra and not self.is_preparing_new_session:
                    await self._trigger_immediate_preparation_for_extra()

                # 3. 后台预热（10s 延迟，适用于定时触发路径；
                #    即时路径由 _trigger_immediate_preparation_for_extra 在内部直接启动，不走这里）
                if self.is_preparing_new_session and \
                        self.summary_triggered_time and \
                        (datetime.now() - self.summary_triggered_time).total_seconds() >= 10 and \
                        (not self.background_preparation_task or self.background_preparation_task.done()) and \
                        not (self.pending_session_warmed_up_event and self.pending_session_warmed_up_event.is_set()):
                    logger.info(f"[{self.lanlan_name}] Main Listener: Conditions met to start BACKGROUND PREPARATION of pending session.")
                    self.pending_session_warmed_up_event = asyncio.Event()
                    self.background_preparation_task = asyncio.create_task(self._background_prepare_pending_session())

                # 4. 后台预热完成 + 当前轮次结束 → 执行最终热切换
                elif self.pending_session_warmed_up_event and \
                        self.pending_session_warmed_up_event.is_set() and \
                        not self.is_hot_swap_imminent and \
                        (not self.final_swap_task or self.final_swap_task.done()):
                    logger.info(
                        "Main Listener: OLD session completed a turn & PENDING session is warmed up. Triggering FINAL SWAP sequence.")
                    self.is_hot_swap_imminent = True
                    self.pending_session_final_prime_complete_event = asyncio.Event()
                    self.final_swap_task = asyncio.create_task(
                        self._perform_final_swap_sequence()
                    )
            except Exception as e:
                logger.error(f"💥 Hot-swap preparation error: {e}")

        # After each turn: deliver any queued agent task callbacks via LLM rephrase
        if self.pending_agent_callbacks:
            self._fire_task(self.trigger_agent_callbacks())

    async def handle_response_discarded(self, reason: str, attempt: int, max_attempts: int, will_retry: bool, message: Optional[str] = None):
        """
        Handle the response-discarded notification: clear the TTS pipeline + frontend output, sending turn end if necessary
        """
        # 快照本轮的 request_id，函数末尾只在仍等于快照时才清空——
        # 防止用户在本轮 turn end 发出前就提交下一条文本时，新轮的
        # request_id 被旧 discard 回调误抹掉（前端 rollback / clearPending
        # rollback 会跨轮串掉）。
        active_request_id = self._active_text_request_id
        logger.warning(f"[{self.lanlan_name}] 响应异常已丢弃 (reason={reason}, attempt={attempt}/{max_attempts}, will_retry={will_retry})")

        # 检测是否为 RESPONSE_TOO_LONG 最终丢弃 / RESPONSE_LENGTH_TRUNCATED 截断恢复
        _is_too_long_final = False
        _truncated_text = None  # 非 None 表示进入 reroll 耗尽后的"截断到句末"恢复路径
        if not will_retry and message:
            try:
                parsed = json.loads(message) if isinstance(message, str) else message
                if isinstance(parsed, dict):
                    if parsed.get('code') == 'RESPONSE_TOO_LONG':
                        _is_too_long_final = True
                    elif parsed.get('code') == 'RESPONSE_LENGTH_TRUNCATED':
                        candidate = parsed.get('text')
                        if isinstance(candidate, str) and candidate.strip():
                            _truncated_text = candidate
            except Exception as _parse_err:
                # message 可能含 RESPONSE_LENGTH_TRUNCATED.text（截断后的 AI 原文），
                # 不写进 logger；只记元数据，原文走 print 兜底。
                logger.debug(
                    f"[{self.lanlan_name}] response_discarded JSON 解析失败: {_parse_err} (msg_len={len(message or '')})"
                )
                print(f"[response_discarded parse_err] raw: {message!r}")

        await self._clear_tts_pipeline()

        if self.websocket and hasattr(self.websocket, 'client_state') and \
                self.websocket.client_state == self.websocket.client_state.CONNECTED:
            try:
                await self.websocket.send_json({
                    "type": "response_discarded",
                    "reason": reason,
                    "attempt": attempt,
                    "max_attempts": max_attempts,
                    "will_retry": will_retry,
                    "message": message or "",
                    # 透传函数开头的 snapshot，避免新轮覆盖后串轮
                    "request_id": active_request_id,
                })
            except Exception as e:
                logger.warning(f"发送 response_discarded 到前端失败: {e}")

        # RESPONSE_TOO_LONG 最终丢弃时：发送可爱回复 + 用角色 TTS 音色念出来。
        # RESPONSE_LENGTH_TRUNCATED：reroll 耗尽后回退到最后句末标点截断的恢复路径，
        # 把截断后的文本当作正常回复重新喂给前端 + TTS（用户输入不回滚）。
        #
        # 这里要复用 handle_response_complete 的"turn 收尾"语义：
        #   - 消费 _pending_turn_meta：把它挂到 turn_end，再清空，避免漏挂或
        #     被下一轮 turn 误消费。
        #   - 尊重 ephemeral 语义：avatar_interaction 由 prompt_ephemeral
        #     (persist_response=False) 触发，本来不该写 _conversation_history；
        #     truncate-recovery / too-long-final 走到这里时不能强行 append。
        if _is_too_long_final or _truncated_text is not None:
            try:
                if _truncated_text is not None:
                    body_text = _truncated_text
                else:
                    body_text = _get_chat_locale_text(
                        self.user_language,
                        'responseTooLong',
                        "Response too long and was discarded; your input has been restored.",
                    )

                # 冻结本轮 recovery 用的 turn/speech id snapshot——后面所有
                # send_lanlan_response / feed_tts_chunk 都用这个本地变量，
                # 不再回读共享字段；否则用户在 response_discarded 发出后立刻
                # 提交下一条文本时，新轮会改写 self.current_speech_id，截断
                # 恢复出来的正文 + 音频会带着新轮的 turn_id 发出去，前端
                # （app-websocket.js assistant turn 生命周期是按 turn_id 建的）
                # 会把恢复内容和新轮串到一起。
                if self.use_tts:
                    async with self.lock:
                        recovery_turn_id = str(uuid4())
                        self.current_speech_id = recovery_turn_id
                        self._tts_done_queued_for_turn = False
                        self._tts_done_pending_until_ready = False
                else:
                    recovery_turn_id = self.current_speech_id

                # 发送文本到前端显示。显式传 active_request_id snapshot，
                # 避免 send_lanlan_response 内部回读共享字段时拿到新轮 id
                # 串掉前端 rollback 绑定。
                await self.send_lanlan_response(
                    body_text,
                    is_first_chunk=True,
                    turn_id=recovery_turn_id,
                    request_id=active_request_id,
                )

                # 仅当本轮**不是** ephemeral（即非 avatar_interaction 等
                # persist_response=False 的路径）时才写历史。avatar_interaction
                # 触发 RESPONSE_TOO_LONG/TRUNCATED 时本就该和 ephemeral 一致地
                # 不留下 AIMessage 痕迹。
                pending_meta = self._pending_turn_meta
                is_ephemeral = bool(pending_meta) and pending_meta.get("kind") == "avatar_interaction"
                if not is_ephemeral and self.session and hasattr(self.session, '_conversation_history'):
                    self.session._conversation_history.append(AIMessage(content=body_text))

                # 喂给 TTS 管线用角色音色念。recovery 路径下两次 await
                # 之间用户可能开新轮（ self.current_speech_id 被改），所以
                # done 信号也要带 expected_speech_id 校验，否则旧 recovery
                # 的 done 会结束新轮的 TTS（首句被截 / 整轮静音）。
                if self.use_tts:
                    await self.feed_tts_chunk(body_text, expected_speech_id=recovery_turn_id)
                    await self._request_tts_done_for_turn(
                        "handle_response_discarded:length_truncated"
                        if _truncated_text is not None
                        else "handle_response_discarded:too_long_final",
                        expected_speech_id=recovery_turn_id,
                    )

                # turn end —— 复用 _emit_turn_end helper（同 handle_response_complete
                # 走同一套语义；sync queue 和 WS 都带相同 meta）。
                # 注：上面读 pending_meta 已经触发 is_ephemeral 判定，但这里
                # _emit_turn_end 自己会再读一次 _pending_turn_meta 做透传 + 清空，
                # 二者读的是同一个值，幂等。
                await self._emit_turn_end(active_request_id)
            except Exception as e:
                logger.warning(f"⚠️ {'RESPONSE_LENGTH_TRUNCATED' if _truncated_text is not None else 'RESPONSE_TOO_LONG'} 回复发送失败: {e}")
            finally:
                # Compare-and-clear：见函数顶部 active_request_id 快照说明。
                if self._active_text_request_id == active_request_id:
                    self._active_text_request_id = None

        if self.sync_message_queue:
            self.sync_message_queue.put({
                'type': 'system',
                'data': 'response_discarded_clear'
            })

        if not will_retry and not _is_too_long_final and _truncated_text is None:
            # Compare-and-clear：仅当共享字段仍是本轮快照时才清空。
            if self._active_text_request_id == active_request_id:
                self._active_text_request_id = None

        # Recovery / too-long-final 路径相当于"这一轮 LLM 已完成"——必须
        # 跑跟 handle_response_complete 同款的 turn 后置流程（renew/prewarm
        # 判断 + agent callback 投递），否则连续多轮走 RESPONSE_LENGTH_TRUNCATED
        # / RESPONSE_TOO_LONG 时 session 不归档/不预热，会卡进"上下文越来越
        # 大→一直截断恢复"的死循环。普通 will_retry / RESPONSE_INVALID 路径
        # 还会重试同轮，不算 turn 真正结束，跳过 finalize。
        if _is_too_long_final or _truncated_text is not None:
            await self._finalize_turn_after_emit()


    async def handle_audio_data(self, audio_data: bytes):
        """Qwen audio callback: push audio to the WebSocket frontend"""
        if self._takeover_active:
            logger.info("[%s] session takeover active: dropping ordinary realtime audio bytes=%d", self.lanlan_name, len(audio_data or b""))
            return
        if not self.use_tts:
            if self.websocket and hasattr(self.websocket, 'client_state') and self.websocket.client_state == self.websocket.client_state.CONNECTED:
                # 这里假设audio_data为PCM16字节流，使用流式重采样器处理
                audio = np.frombuffer(audio_data, dtype=np.int16)
                audio_float = audio.astype(np.float32) / 32768.0
                # 使用流式重采样器（维护内部状态，避免 chunk 边界不连续）
                resampled_float = self.audio_resampler.resample_chunk(audio_float)
                audio = (resampled_float * 32767.0).clip(-32768, 32767).astype(np.int16)
                await self.send_speech(audio.tobytes())
            else:
                pass  # websocket未连接时忽略

    def _publish_user_utterance_to_plugin_bus(
        self, text: Optional[str], *, is_voice_source: bool
    ) -> None:
        """Publish one verbatim user utterance to the plugin bus's user-context bucket.

        Plugins read it via ``ctx.bus.memory.get(bucket_id=...)``. Written to two
        buckets at once: ``"default"`` (matching the protocols.py doc example,
        globally readable) and ``self.lanlan_name`` (character-scoped) — but if the
        two names collide it is written only once, so the same utterance isn't
        consumed twice.

        Why: before this, the whole ``state.add_user_context_event`` chain was dead
        infrastructure — server, handler, and plugin SDK were all in place, but
        nothing ever wrote, so plugins always read empty. This is the first
        gateway where verbatim user input enters the system (voice transcription +
        text input), making it the most faithful place to publish "the user's
        actual words".
        """
        if not isinstance(text, str):
            return
        cleaned = text.strip()
        if not cleaned:
            return
        event = {
            "type": "user_message",
            "content": cleaned,
            "lanlan": self.lanlan_name,
            "is_voice": bool(is_voice_source),
            "source": "main_logic.core",
        }
        # dict.fromkeys 保留顺序的同时去重：lanlan_name == "default" 或为空
        # 时不会重复写入 default bucket。
        for bucket in dict.fromkeys(("default", self.lanlan_name)):
            if not isinstance(bucket, str) or not bucket:
                continue
            # dispatch_user_utterance fans out to every sink (plugin runtime
            # registers ``plugin.core.state.add_user_context_event`` at app
            # startup via app/runtime_bindings.py). Per-sink errors are
            # swallowed inside the dispatcher.
            dispatch_user_utterance(bucket, event)

    def _clean_frontend_memory_text(self, value: Any) -> str:
        if not isinstance(value, str):
            return ""
        cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]+", "", value)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if not cleaned:
            return ""
        return cleaned[:500]

    async def _broadcast_voice_transcript_observed(self, transcript: str) -> None:
        """Best-effort fan-out of voice transcripts to plugins.

        Plugins are observers here, not arbiters for the current user turn.
        Main must never wait for or apply plugin-produced actions from this
        path.
        """
        session_snapshot = self.session
        try:
            await publish_voice_transcript_observed_best_effort(
                self.lanlan_name,
                transcript,
                metadata={
                    "session_type": type(session_snapshot).__name__ if session_snapshot else "",
                    "voice_source": True,
                },
            )
        except Exception as exc:
            logger.debug("[%s] voice transcript observer broadcast failed: %s", self.lanlan_name, exc)

    def _reset_voice_echo_suppression_cache(self) -> None:
        self._recent_ai_voice_echo_text = ''
        self._recent_ai_voice_echo_at = 0.0
        self._pending_ai_voice_echo_text = ''
        pending_chunks = getattr(self, "_pending_ai_voice_echo_chunks", None)
        if pending_chunks is None:
            self._pending_ai_voice_echo_chunks = deque()
        else:
            pending_chunks.clear()
        confirmed_speech_ids = getattr(self, "_confirmed_ai_voice_echo_audio_speech_ids", None)
        if confirmed_speech_ids is None:
            self._confirmed_ai_voice_echo_audio_speech_ids = set()
        else:
            confirmed_speech_ids.clear()

    def _remember_recent_ai_voice_echo(self, text: str) -> None:
        if not text:
            return
        recent_echo_text = (getattr(self, "_recent_ai_voice_echo_text", "") or "") + text
        self._recent_ai_voice_echo_text = recent_echo_text[-_VOICE_ECHO_LOOKBACK_CHARS:]
        self._recent_ai_voice_echo_at = time.time()

    @staticmethod
    def _pending_ai_voice_echo_item_speech_id(item) -> str | None:
        if isinstance(item, tuple) and len(item) == 2:
            return item[0]
        return None

    @staticmethod
    def _pending_ai_voice_echo_item_text(item) -> str:
        if isinstance(item, tuple) and len(item) == 2:
            return item[1]
        return item

    def _remember_pending_ai_voice_echo(self, speech_id: str | None, text: str) -> None:
        if not text:
            return
        pending_chunks = getattr(self, "_pending_ai_voice_echo_chunks", None)
        if pending_chunks is None:
            pending_chunks = deque()
            self._pending_ai_voice_echo_chunks = pending_chunks
        pending_chunks.append((speech_id, text))
        self._sync_pending_ai_voice_echo_text()

    def _sync_pending_ai_voice_echo_text(self) -> None:
        pending_chunks = getattr(self, "_pending_ai_voice_echo_chunks", None)
        if pending_chunks is None:
            pending_chunks = deque()
            pending_echo_text = getattr(self, "_pending_ai_voice_echo_text", "") or ""
            if pending_echo_text:
                pending_chunks.append((None, pending_echo_text))
            self._pending_ai_voice_echo_chunks = pending_chunks

        pending_echo_text = "".join(
            self._pending_ai_voice_echo_item_text(chunk)
            for chunk in pending_chunks
        )
        excess = max(0, len(pending_echo_text) - _VOICE_ECHO_LOOKBACK_CHARS)
        while pending_chunks:
            first_text = self._pending_ai_voice_echo_item_text(pending_chunks[0])
            if not first_text:
                pending_chunks.popleft()
                continue
            if excess < len(first_text):
                break
            excess -= len(first_text)
            pending_chunks.popleft()
        if pending_chunks and excess > 0:
            first_chunk = pending_chunks[0]
            first_speech_id = self._pending_ai_voice_echo_item_speech_id(first_chunk)
            first_text = self._pending_ai_voice_echo_item_text(first_chunk)
            pending_chunks[0] = (first_speech_id, first_text[excess:])

        self._pending_ai_voice_echo_text = "".join(
            self._pending_ai_voice_echo_item_text(chunk)
            for chunk in pending_chunks
        )

    def _confirm_pending_ai_voice_echo(self, speech_id: str | None = None) -> None:
        if speech_id is None:
            return

        confirmed_speech_ids = getattr(self, "_confirmed_ai_voice_echo_audio_speech_ids", None)
        if confirmed_speech_ids is None:
            confirmed_speech_ids = set()
            self._confirmed_ai_voice_echo_audio_speech_ids = confirmed_speech_ids
        # Without chunk-level playback confirmation, one speech id can only safely promote one chunk.
        if speech_id in confirmed_speech_ids:
            return

        pending_chunks = getattr(self, "_pending_ai_voice_echo_chunks", None)
        if pending_chunks is None:
            pending_echo_text = getattr(self, "_pending_ai_voice_echo_text", "") or ""
            pending_chunks = deque()
            if pending_echo_text:
                pending_chunks.append((None, pending_echo_text))
            self._pending_ai_voice_echo_chunks = pending_chunks
            self._sync_pending_ai_voice_echo_text()
            return

        if not pending_chunks:
            self._pending_ai_voice_echo_text = ''
            return

        pending_speech_id = self._pending_ai_voice_echo_item_speech_id(pending_chunks[0])
        if pending_speech_id != speech_id:
            return

        pending_echo_text = self._pending_ai_voice_echo_item_text(pending_chunks.popleft())
        self._sync_pending_ai_voice_echo_text()
        confirmed_speech_ids.add(speech_id)
        self._remember_recent_ai_voice_echo(pending_echo_text)

    def _discard_pending_ai_voice_echo(self) -> None:
        self._pending_ai_voice_echo_text = ''
        pending_chunks = getattr(self, "_pending_ai_voice_echo_chunks", None)
        if pending_chunks is not None:
            pending_chunks.clear()
        confirmed_speech_ids = getattr(self, "_confirmed_ai_voice_echo_audio_speech_ids", None)
        if confirmed_speech_ids is not None:
            confirmed_speech_ids.clear()

    def _should_suppress_dirty_voice_transcript(self, transcript_text: str) -> bool:
        if not HIDE_DIRTY_VOICE_TRANSCRIPTS:
            return False
        recent_ai_at = float(getattr(self, "_recent_ai_voice_echo_at", 0.0) or 0.0)
        if recent_ai_at <= 0 or (time.time() - recent_ai_at) > _VOICE_ECHO_LOOKBACK_SECONDS:
            return False
        recent_ai_text = getattr(self, "_recent_ai_voice_echo_text", "") or ""
        return _looks_like_recent_ai_echo(transcript_text, recent_ai_text)

    async def _dispatch_mini_game_invite_keyword(self, user_text: str) -> None:
        """Scan the user's words once for mini-game invite accept/decline/later keywords; on a
        hit, trigger the corresponding state transition + push ``mini_game_invite_resolved``
        so the frontend dismisses the ChoicePrompt (on accept it doubles as the launch
        signal carrying game_url).

        Shared by the text input path (``_process_stream_data_internal``) and the voice
        transcription path (``handle_input_transcript``) — voice users can't click the
        ChoicePrompt's three buttons, they can only speak; a spoken "not now" must
        trigger the real decline cooldown just like typing / clicking. Otherwise a
        spoken refusal neither counts as decline nor escapes being treated by the next
        proactive tick's ``_mini_game_invite_advance_response`` as an implicit
        dismiss = 'later' (only a 5min suppress), and the invite keeps coming back.
        **Does not consume the message**: the normal chat pipeline still responds to it.

        main_routers' keyword matcher is registered as a hook on the bus
        (see app/runtime_bindings.py). Dispatcher swallows per-hook errors;
        if no hook is bound (e.g. entrypoint without main_routers), result
        is None.
        """
        outcome = dispatch_text_user_message(self.lanlan_name, user_text or '')
        # 推一条 mini_game_invite_resolved 给前端：accept 时兼当 launch 信号
        # （带 game_url），decline/later 时让 ChoicePrompt UI 清掉不让按钮挂着——
        # codex P2 指出，原版只对 accept 推，decline/later 命中后前端 prompt 不
        # 消失，用户后续点按钮会被 endpoint 当 expired，state 早变了。
        if not (outcome and outcome.get('action')):
            return
        try:
            if self.websocket and hasattr(self.websocket, 'send_json'):
                ws_state = getattr(self.websocket, 'client_state', None)
                if ws_state is None or ws_state == ws_state.CONNECTED:
                    payload = {
                        'type': 'mini_game_invite_resolved',
                        'session_id': outcome.get('session_id') or '',
                        'action': outcome['action'],
                    }
                    if outcome.get('game_url'):
                        payload['game_url'] = outcome['game_url']
                    if outcome.get('game_type'):
                        payload['game_type'] = outcome['game_type']
                    await self.websocket.send_json(payload)
        except Exception as _push_err:
            logger.warning(
                f"[{self.lanlan_name}] mini_game_invite_resolved "
                f"WS push failed: {_push_err}",
            )

    async def handle_text_input_transcript(self, transcript: str):
        """Reuse transcript queue/cache plumbing for text-mode sessions."""
        await self.handle_input_transcript(transcript, is_voice_source=False)

    @staticmethod
    def _normalize_explicit_openclaw_magic_command(text: str) -> Optional[str]:
        raw = str(text or "").strip()
        if not raw:
            return None
        lowered = " ".join(raw.lower().split())
        prefix = None
        for candidate in ("/openclaw ", "/qwenpaw "):
            if lowered.startswith(candidate):
                prefix = candidate
                break
        if prefix is None:
            return None
        command = lowered[len(prefix):].strip()
        if command in {"/clear", "clear"}:
            return "/clear"
        if command in {"/new", "new"}:
            return "/new"
        if command in {"/stop", "stop"}:
            return "/stop"
        if command in {"/daemon approve", "daemon approve", "/approve", "approve"}:
            return "/daemon approve"
        return None

    def _clear_text_pending_images(self) -> None:
        if not isinstance(self.session, OmniOfflineClient):
            return
        pending_images = getattr(self.session, "_pending_images", None)
        if hasattr(pending_images, "clear"):
            pending_images.clear()

    async def _publish_openclaw_magic_command(self, command: str) -> None:
        try:
            sent = await publish_analyze_request_reliably(
                lanlan_name=self.lanlan_name,
                trigger="text_openclaw_magic_command",
                messages=[{"role": "user", "content": command}],
                ack_timeout_s=0.8,
                retries=1,
                conversation_id=uuid4().hex,
            )
        except Exception as exc:
            logger.warning("[%s] openclaw magic command publish failed: %s", self.lanlan_name, exc)
            await self.send_status(json.dumps({
                "code": "OPENCLAW_COMMAND_DISPATCH_FAILED",
                "details": {"command": command},
            }))
            return
        if not sent:
            logger.warning("[%s] openclaw magic command publish failed: no ack", self.lanlan_name)
            await self.send_status(json.dumps({
                "code": "OPENCLAW_COMMAND_DISPATCH_FAILED",
                "details": {"command": command},
            }))

    async def handle_input_transcript(self, transcript: str, *, is_voice_source: bool = True):
        """Sync transcript text into queues/cache and push it to the frontend.

        ``is_voice_source`` defaults to True for the realtime-client
        callbacks (genuine VAD-captured speech). Text-mode call sites
        that reuse this function for non-voice transcript display/cache paths
        pass False so that:
          - voice_rms is NOT marked (no fake voice_engaged state)
          - on_user_message is skipped here (the text-mode entry has
            already called it directly with the input data — calling
            twice would double-bump _conv_seq and add the text to the
            buffer twice)
        """
        transcript_text = transcript.strip()
        record_transcript_text = transcript_text
        voice_rms_recorded = False

        # 更新用户活动时间戳（用于主动搭话检测）。先捕获「转写到达时刻」局部变量，
        # 下面 last_user_message_time 复用同一时刻——若 takeover dispatcher 注册，
        # 这条转写会先 await 它再走到下面的真消息块；用 await 之后的 time.time() 会
        # 把时间戳推迟，万一 await 期间投递了 invite，invite 之前说的话会被记成 >
        # delivered_at、被下个 tick 误判成 invite 之后的回应（codex P2）。
        _transcript_arrival_ts = time.time()
        self.last_user_activity_time = _transcript_arrival_ts
        if (
            is_voice_source
            and transcript_text
            and self._takeover_input_dispatcher is not None
        ):
            # takeover 路由优先于 echo suppression；否则接管流程里用户说出
            # 与 AI 近期播报相同的口令时，会被当成脏回声提前吞掉。
            self._activity_tracker.on_voice_rms()
            voice_rms_recorded = True
            try:
                handled = await self._takeover_input_dispatcher(
                    self.lanlan_name,
                    transcript_text,
                    request_id=f"realtime-stt-{uuid4()}",
                )
                logger.info(
                    "[%s] session takeover dispatcher: realtime STT transcript routed handled=%s len=%d",
                    self.lanlan_name, handled, len(transcript_text),
                )
                if handled:
                    if isinstance(self.session, OmniRealtimeClient):
                        try:
                            await self.session.cancel_response()
                            logger.info("[%s] session takeover: cancelled ordinary realtime response after STT transcript", self.lanlan_name)
                        except Exception as cancel_exc:
                            logger.debug("[%s] session takeover: realtime response cancel skipped/failed: %s", self.lanlan_name, cancel_exc)
                    return
            except Exception as exc:
                logger.warning("[%s] session takeover dispatcher failed: %s", self.lanlan_name, exc)

        if (
            is_voice_source
            and transcript_text
            and self._should_suppress_dirty_voice_transcript(transcript_text)
        ):
            logger.info(
                "[%s] suppressed likely AI echo voice transcript len=%d",
                self.lanlan_name, len(transcript_text),
            )
            return

        if is_voice_source and not voice_rms_recorded:
            # transcript 到达 → VAD 在窗口内捕捉到声音，标记 voice RMS 活跃；
            # 即使转录为空（VAD 误触发或转录失败）也算一次"用户在发声"，
            # 维持 voice_engaged 状态。
            self._activity_tracker.on_voice_rms()

        if is_voice_source and record_transcript_text:
            self._fire_task(self._broadcast_voice_transcript_observed(record_transcript_text))

        if is_voice_source:
            # 仅非空转录才算"用户消息"：on_user_message 会清掉 unfinished_thread、
            # bump _conv_seq（让 open_threads 缓存失效）、把文本进 buffer 给
            # emotion-tier LLM 用——空 transcript 这些副作用都不该触发。
            if record_transcript_text:
                self._note_user_turn(text=transcript)
                # 真实用户语音消息（已过 echo 抑制 + 非空）才刷「真消息」时间戳，
                # 给 mini-game 邀请隐式 dismiss 用，避免回声/空噪声误判用户已回应。
                # 用顶部捕获的到达时刻而非此处 time.time()：takeover dispatcher 的
                # await 不会把它推迟到 await 之后（codex P2）。
                self.last_user_message_time = _transcript_arrival_ts
                self._session_turn_count += 1
                # Telemetry：D1 漏斗——本进程首条用户消息（语音路径）。
                try:
                    from utils.token_tracker import TokenTracker as _TT
                    _tt = _TT.get_instance()
                    _tt.note_first_user_message("voice")
                    # 每条用户消息：user_message_sent counter（轮数 + voice/text 占比）
                    # + 累加 per-session 轮数（session_end emit session_turn_count）。
                    # 只在此真语音消息点调，避开非语音复用路径，杜绝双计。
                    _tt.note_user_message("voice")
                except Exception:
                    # 埋点 best-effort，绝不阻塞语音转录消息处理（同文本路径）。
                    pass
                # 与 on_user_message 对偶：把"用户原话"推到插件总线 user-context
                # bucket。文本路径在 _process_stream_data_internal 已自行调用，
                # 这里只覆盖语音路径，避免非语音复用路径重复发布。
                self._publish_user_utterance_to_plugin_bus(transcript, is_voice_source=True)

                # Mini-game 邀请关键词兜底：与文本路径
                # （_process_stream_data_internal）对偶。语音用户没法点
                # ChoicePrompt 三按钮，只能说话——口头"现在不想玩"必须和打字 /
                # 点按钮一样触发真正的 decline 冷却，否则邀请会按 5min 隐式
                # dismiss 反复重来。详见 _dispatch_mini_game_invite_keyword。
                await self._dispatch_mini_game_invite_keyword(transcript)
        else:
            # Non-voice reuse of this method.
            # Skip activity-tracker hooks entirely — the text-mode entry
            # at `_process_stream_data_internal` has already recorded the
            # user message. We still need the queue/cache plumbing below
            # to work normally, so just bypass the tracker block.
            if record_transcript_text:
                self._session_turn_count += 1

        # 推送到同步消息队列
        user_message = {"input_type": "transcript", "data": record_transcript_text}
        if not is_voice_source and self._active_text_request_id:
            user_message["request_id"] = self._active_text_request_id
        self.sync_message_queue.put({"type": "user", "data": user_message})
        
        # 只在语音模式（OmniRealtimeClient）下发送到前端显示用户转录
        # 文本模式下前端会自己显示，无需后端发送，避免重复
        # [DIAG] 切换猫娘后对话框空白问题：记录是否触发、session 类型、ws 状态
        _ws_connected_dbg = bool(
            self.websocket
            and hasattr(self.websocket, 'client_state')
            and self.websocket.client_state == self.websocket.client_state.CONNECTED
        )
        logger.info(
            "[%s] voice user_transcript session=%s ws_connected=%s len=%d",
            self.lanlan_name, type(self.session).__name__, _ws_connected_dbg, len(record_transcript_text),
        )
        if isinstance(self.session, OmniRealtimeClient):
            if self.websocket and hasattr(self.websocket, 'client_state') and self.websocket.client_state == self.websocket.client_state.CONNECTED:
                try:
                    message = {
                        "type": "user_transcript",
                        "text": transcript.strip()
                    }
                    await self.websocket.send_json(message)
                except Exception as e:
                    logger.error(f"⚠️ 发送用户转录到前端失败: {e}")
        
        # 缓存到session cache
        if hasattr(self, 'is_preparing_new_session') and self.is_preparing_new_session:
            if not hasattr(self, 'message_cache_for_new_session'):
                self.message_cache_for_new_session = []
            if len(self.message_cache_for_new_session) == 0 or self.message_cache_for_new_session[-1]['role'] == self.lanlan_name:
                self.message_cache_for_new_session.append({"role": self.master_name, "text": record_transcript_text})
            elif self.message_cache_for_new_session[-1]['role'] == self.master_name:
                self.message_cache_for_new_session[-1]['text'] += record_transcript_text
        # 注意: 这里不能修改 current_speech_id.
        # speech_id 仅应在“模型新回复开始”时更新 (handle_new_message / 文本模式 stream 入口),
        # 否则会导致前端把同一轮 AI 语音误判为新轮次, 出现首包被重置/吞掉的问题.

    async def handle_output_transcript(self, text: str, is_first_chunk: bool = False):
        """Output transcription callback: handles text display and TTS (for voice mode)"""
        if self._takeover_active:
            logger.info("[%s] session takeover active: dropping ordinary realtime output transcript len=%d", self.lanlan_name, len(text or ""))
            return

        # 同 handle_text_data：proactive 路径设置的 sid 期望值若与 current 不符，
        # 丢弃本 chunk，避免 proactive 文本被错插进用户新轮次。
        expected_sid = _proactive_expected_sid.get()
        if expected_sid is not None and expected_sid != self.current_speech_id:
            logger.debug(
                "handle_output_transcript drop: expected_sid=%s current_sid=%s len=%d",
                expected_sid, self.current_speech_id, len(text),
            )
            return
        # 无论是否使用TTS，都要发送文本到前端显示
        await self.send_lanlan_response(
            text,
            is_first_chunk,
            remember_voice_echo=not self.use_tts,
        )
        
        # 如果配置了TTS，将文本发送到TTS队列或缓存
        if self.use_tts:
            async with self.tts_cache_lock:
                # 检查TTS是否就绪
                if self.tts_ready and self.tts_thread and self.tts_thread.is_alive():
                    # TTS已就绪，直接发送
                    try:
                        self._enqueue_tts_text_chunk(self.current_speech_id, text)
                    except Exception as e:
                        logger.warning(f"⚠️ 发送TTS请求失败: {e}")
                else:
                    # TTS未就绪，先缓存（规范化延迟到 _flush_tts_pending_chunks）
                    self.tts_pending_chunks.append((self.current_speech_id, text))
                    if len(self.tts_pending_chunks) == 1:
                        logger.info("TTS未就绪，开始缓存文本chunk...")
                    # 仅在回复首 chunk 尝试拉起，避免每个 chunk 都重试
                    if is_first_chunk and self.tts_thread and not self.tts_thread.is_alive():
                        self._respawn_tts_worker()

    async def send_lanlan_response(
        self,
        text: str,
        is_first_chunk: bool = False,
        turn_id: str | None = None,
        *,
        metadata: dict | None = None,
        request_id: Any = _REQUEST_ID_UNSET,
        track_ai_turn: bool = True,
        cache_for_new_session: bool = True,
        remember_voice_echo: bool = False,
    ):
        """Qwen output transcription callback: usable for frontend display/cache/sync.

        ``request_id`` is tri-state:
          - not passed (i.e. the default ``_REQUEST_ID_UNSET``) → falls back to the
            shared field ``self._active_text_request_id``, preserving the behavior
            of existing LLM streaming call sites
          - explicitly passing ``None`` → genuinely "frozen to empty"; proactive /
            no-request_id scenarios need the frontend to know this message is
            bound to no user request
          - explicitly passing a str → cross-turn safety: discard / recovery must
            use the ``active_request_id`` snapshotted at the start of the
            function, so that after a new turn has written the shared field, a
            re-read doesn't pick up the wrong id and make the frontend roll back
            the wrong turn
        The default sentinel is the module-level ``_REQUEST_ID_UNSET = object()``
        to distinguish "not passed" from "explicit None", unlike a plain
        ``request_id is None`` check.
        """
        text_clean = self.emotion_pattern.sub('', text)
        # 累加到当前轮 AI 文本 buffer，turn end 时一并交给 activity tracker 做
        # unfinished_thread 检测。emotion_pattern 已剥掉表情标签，但保留 <expr>
        # 等可能的 markup——tracker 自己会做二次 strip。
        if track_ai_turn:
            self._current_ai_turn_text += text_clean
            if remember_voice_echo:
                self._remember_recent_ai_voice_echo(text_clean)
        effective_turn_id = turn_id or self.current_speech_id
        effective_request_id = (
            self._active_text_request_id
            if request_id is _REQUEST_ID_UNSET
            else request_id
        )
        message = {
            "type": "gemini_response",
            "text": text_clean,
            "isNewMessage": is_first_chunk,
            "turn_id": effective_turn_id,
            "request_id": effective_request_id,
        }
        if metadata:
            message["metadata"] = metadata

        # 无论 WS 发送成功与否，始终将消息写入 sync_message_queue 和 message_cache，
        # 确保 cross_server 历史组装不因 WS 断连而丢失 assistant 内容。
        if is_first_chunk:
            logger.debug("[%s] send_lanlan_response: first chunk (len=%d)", self.lanlan_name, len(text_clean))
            # First visible content of the turn → the model has finished its
            # (hidden) 凝神 thinking, so drop the thinking-dots bubble. Idempotent,
            # so this is a no-op on regular / proactive turns that never lit it.
            await self._push_focus_thinking(False)
        self.sync_message_queue.put({"type": "json", "data": message})
        if cache_for_new_session and hasattr(self, 'is_preparing_new_session') and self.is_preparing_new_session:
            if not hasattr(self, 'message_cache_for_new_session'):
                self.message_cache_for_new_session = []
            # 注意：缓存使用原始文本，不翻译（用于记忆等内部处理）
            if len(self.message_cache_for_new_session) == 0 or self.message_cache_for_new_session[-1]['role']==self.master_name:
                self.message_cache_for_new_session.append(
                    {"role": self.lanlan_name, "text": text_clean})
            elif self.message_cache_for_new_session[-1]['role'] == self.lanlan_name:
                self.message_cache_for_new_session[-1]['text'] += text_clean

        # WS 发送（可能失败，但 sync/cache 已保存）
        # [DIAG] 切换猫娘后对话框空白问题：仅首 chunk 记录，避免流式刷屏
        if is_first_chunk:
            logger.info(
                "[%s] send_lanlan_response first=%s len=%d ws_state=%s",
                self.lanlan_name, is_first_chunk, len(text_clean),
                getattr(self.websocket, 'client_state', None),
            )
        try:
            async def _do_send():
                if self.websocket and hasattr(self.websocket, 'client_state') and self.websocket.client_state == self.websocket.client_state.CONNECTED:
                    await self.websocket.send_json(message)
                    return True
                return False

            if self.websocket_lock:
                async with self.websocket_lock:
                    ws_ok = await _do_send()
            else:
                ws_ok = await _do_send()
            return ws_ok

        except WebSocketDisconnect:
            logger.info("Frontend disconnected.")
            return False
        except Exception as e:
            logger.error(f"💥 WS Send Lanlan Response Error: {e}")
            return False

    # ------------------------------------------------------------------
    # Mirror channel (chat-bubble passthrough that enters context as
    # AIMessage; user-side inputs intentionally do NOT enter chat history
    # as UserMessage — see ``main_logic.mirror_meta``).
    # ------------------------------------------------------------------

    async def mirror_user_input(
        self,
        text: str,
        *,
        metadata: dict,
        request_id: str | None = None,
        input_type: str | None = None,
        send_to_frontend: bool = False,
    ) -> None:
        """Record an external-controller user input into the sync stream.

        The text is logged for monitor/display purposes but does not
        flush into ``chat_history`` as a UserMessage (cross_server skips
        ``input_type`` values listed in ``mirror_meta.MIRROR_USER_INPUT_TYPES``).
        Use this when an external controller (e.g. a game route) has
        captured what the user said but the ordinary chat LLM should not
        see it.
        """
        from main_logic.mirror_meta import MIRROR_USER_TEXT_INPUT_TYPE

        clean = str(text or "").strip()
        if not clean:
            return
        resolved_input_type = input_type or MIRROR_USER_TEXT_INPUT_TYPE
        source = str(metadata.get("source") or "mirror") if isinstance(metadata, dict) else "mirror"
        self.last_user_activity_time = time.time()
        self.sync_message_queue.put({
            "type": "user",
            "data": {
                "input_type": resolved_input_type,
                "data": clean,
                "source": source,
                "metadata": metadata if isinstance(metadata, dict) else {},
                "request_id": request_id or "",
            },
        })
        if (
            send_to_frontend
            and self.websocket
            and hasattr(self.websocket, "client_state")
            and self.websocket.client_state == self.websocket.client_state.CONNECTED
        ):
            try:
                await self.websocket.send_json({
                    "type": "user_transcript",
                    "text": clean,
                    "source": source,
                    "request_id": request_id,
                })
            except Exception as e:
                logger.error(f"⚠️ mirror_user_input frontend dispatch failed: {e}")

    async def mirror_assistant_output(
        self,
        text: str,
        *,
        metadata: dict,
        request_id: str | None = None,
        turn_id: str | None = None,
        finalize_turn: bool = False,
    ) -> dict:
        """Push an external-controller assistant line into the chat bubble.

        Reuses the ordinary :meth:`send_lanlan_response` path with
        ``track_ai_turn=False`` and ``cache_for_new_session=False`` so
        the line shows on frontend + sync stream as an AIMessage but
        doesn't pollute activity-tracker / hot-swap caches.
        """
        clean = str(text or "").strip()
        if not clean:
            return {"ok": False, "reason": "missing_line", "mirrored": False}

        effective_turn_id = turn_id or request_id or str(uuid4())
        await self.send_lanlan_response(
            clean,
            is_first_chunk=True,
            turn_id=effective_turn_id,
            metadata=metadata,
            request_id=request_id,
            track_ai_turn=False,
            cache_for_new_session=False,
        )
        if finalize_turn:
            await self.emit_mirror_turn_end(
                metadata=metadata,
                request_id=request_id,
                log_context="mirror assistant",
            )
        return {
            "ok": True,
            "mirrored": True,
            "turn_id": effective_turn_id,
            "request_id": request_id or "",
            "metadata": metadata if isinstance(metadata, dict) else {},
            "turn_finalized": bool(finalize_turn),
        }

    async def passthrough_to_chat_bubble(
        self,
        text: str,
        *,
        request_id: str | None = None,
        turn_id: str | None = None,
        source: str = "passthrough",
    ) -> bool:
        """Render external text verbatim into the chat bubble WITHOUT
        entering chat-LLM context.

        Distinct from :meth:`mirror_assistant_output`: that writes to
        ``sync_message_queue`` (so cross_server may add an AIMessage to
        chat history). ``passthrough_to_chat_bubble`` skips
        ``sync_message_queue`` entirely — frontend sees the bubble, but
        the chat LLM never sees it in the next turn.

        Use case: plugin / agent_server pushes verbatim with
        ``visibility=["chat"] + ai_behavior="blind"`` — operator wants
        the user to read it but the LLM should remain ignorant.

        This is a generic SessionManager capability; it does not assume
        any particular consumer.

        Returns ``True`` iff a ``gemini_response`` frame was actually
        handed to ``send_json`` without raising. ``False`` covers every
        no-op path: empty/whitespace text, websocket missing or
        disconnected, and ``send_json`` failures swallowed below. Callers
        that open an assistant-turn lifecycle on the frontend (e.g.
        ``main_server`` chat-blind) MUST gate their turn-end emit on this
        flag — a swallowed send means the frontend never opened a turn,
        so emitting turn-end would close a lifecycle that never started.
        """
        # Why: caller passes raw_text deliberately (PR #1128 0ac9e8881).
        # We empty-check on the stripped form but forward the ORIGINAL so
        # leading/trailing whitespace, newlines, and indentation render
        # exactly as the plugin authored them.
        raw = str(text or "")
        if not raw or not raw.strip():
            return False
        effective_turn_id = turn_id or request_id or str(uuid4())
        message = {
            "type": "gemini_response",
            "text": raw,
            "isNewMessage": True,
            "turn_id": effective_turn_id,
            "request_id": request_id,
            "metadata": {"source": source, "passthrough": True},
        }
        if not (
            self.websocket
            and hasattr(self.websocket, "client_state")
            and self.websocket.client_state == self.websocket.client_state.CONNECTED
        ):
            return False
        try:
            await self.websocket.send_json(message)
        except Exception as e:
            logger.warning(
                "[%s] passthrough_to_chat_bubble WS send failed: %s",
                self.lanlan_name, e,
            )
            return False
        return True

    async def emit_mirror_turn_end(
        self,
        *,
        metadata: dict,
        request_id: str | None = None,
        log_context: str = "",
    ) -> None:
        """Emit a turn-end carrying mirror metadata (cross_server uses
        the metadata to decide whether to fold the turn into ordinary
        chat memory or skip it)."""
        turn_end_msg = {
            "type": "system",
            "data": "turn end",
            "request_id": request_id,
            "meta": metadata if isinstance(metadata, dict) else {},
        }
        self.sync_message_queue.put(turn_end_msg)
        try:
            if (
                self.websocket
                and hasattr(self.websocket, "client_state")
                and self.websocket.client_state == self.websocket.client_state.CONNECTED
            ):
                await self.websocket.send_json(turn_end_msg)
        except Exception as e:
            logger.warning("[%s] %s turn_end send failed: %s", self.lanlan_name, log_context or "mirror", e)

    async def mirror_assistant_speech(
        self,
        line: str,
        *,
        metadata: dict,
        request_id: str | None = None,
        mirror_text: bool = True,
        emit_turn_end_after: bool = True,
        interrupt_audio: bool = False,
    ) -> dict:
        """Mirror an assistant line + play it through the project TTS pipeline.

        Combines :meth:`mirror_assistant_output` with TTS chunk
        enqueue.  TTS pipeline is started lazily via
        :meth:`ensure_tts_pipeline_alive`; if the worker isn't ready
        yet, the chunk is buffered in ``tts_pending_chunks`` and the
        handler picks it up when ``__ready__`` arrives.
        """
        clean = str(line or "").strip()
        if not clean:
            return {"ok": False, "reason": "missing_line", "audio_sent": False}

        interrupted_speech_id = None
        if interrupt_audio:
            async with self.lock:
                interrupted_speech_id = self.current_speech_id
            self.audio_resampler.clear()
            # Mirror channel feeds the project TTS pipeline regardless of
            # ``self.use_tts``, so always clear it on interrupt — the inner
            # liveness gate inside ``_clear_tts_pipeline`` makes this safe
            # when no worker is actually running.
            await self._clear_tts_pipeline()
            # Realtime native voice: also tell the provider to stop generating
            # so further audio.delta / output_audio.delta won't keep streaming
            # past the interruption point.  Local takeover guards drop these
            # at handler level too, but cancelling on the wire avoids wasted
            # tokens and stale audio still in the wire buffer.
            if isinstance(self.session, OmniRealtimeClient):
                try:
                    await self.session.cancel_response()
                except Exception as cancel_exc:
                    logger.debug(
                        "[%s] mirror_assistant_speech: realtime cancel_response skipped/failed: %s",
                        self.lanlan_name, cancel_exc,
                    )
            await self.send_user_activity(interrupted_speech_id)

        async with self.lock:
            self.current_speech_id = str(uuid4())
            self._tts_done_queued_for_turn = False
            self._tts_done_pending_until_ready = False
            turn_id = self.current_speech_id
            self.state.mark_user_input_preempt()
        await self.state.fire(SessionEvent.USER_INPUT, sid=turn_id)

        if mirror_text:
            await self.send_lanlan_response(
                clean,
                is_first_chunk=True,
                turn_id=turn_id,
                metadata=metadata,
                request_id=request_id,
                track_ai_turn=False,
                cache_for_new_session=False,
            )

        await self.ensure_tts_pipeline_alive()
        audio_queued = False
        if self.tts_thread and self.tts_thread.is_alive():
            async with self.tts_cache_lock:
                if self.tts_ready:
                    self._enqueue_tts_text_chunk(turn_id, clean)
                else:
                    self.tts_pending_chunks.append((turn_id, clean))
                status = self._request_tts_done_locked()
                audio_queued = status in {"queued", "deferred", "already"}
        if emit_turn_end_after:
            await self.emit_mirror_turn_end(
                metadata=metadata,
                request_id=request_id,
                log_context="mirror speech",
            )

        return {
            "ok": True,
            "method": "project_tts",
            "speech_id": turn_id,
            "audio_sent": audio_queued,
            "audio_queued": audio_queued,
            "turn_end_emitted": bool(emit_turn_end_after),
            "interrupt_audio": bool(interrupt_audio),
            "voice_source": {
                "provider": "project_tts",
                "method": "project_tts",
                "use_existing_send_speech": True,
            },
        }


    async def handle_silence_timeout(self, *, expected_session=None):
        """Handle voice-input silence timeout: automatically close the session while keeping the Live2D display"""
        try:
            if expected_session is not None:
                if expected_session is self.pending_session:
                    logger.info("⏭️ handle_silence_timeout: expected_session is pending_session, delegating to pending teardown")
                    await self._teardown_pending_session_from_lifecycle_callback(expected_session)
                    return
                if expected_session is not self.session:
                    logger.info("⏭️ handle_silence_timeout: expected_session stale, skipping")
                    return
            logger.warning(f"[{self.lanlan_name}] 检测到长时间无语音输入，自动关闭session")
            
            # 清空热切换音频缓存的最后4秒数据（静默期间的音频主要是噪音）
            async with self.hot_swap_cache_lock:
                # Re-check: a hot-swap could have completed while we waited for the lock.
                if expected_session is not None and expected_session is not self.session and expected_session is not self.pending_session:
                    logger.info("⏭️ handle_silence_timeout: expected_session stale after acquiring cache lock, skipping")
                    return
                if self.hot_swap_audio_cache:
                    SILENCE_DURATION_BYTES = 120000
                    total_bytes = sum(len(chunk) for chunk in self.hot_swap_audio_cache)
                    
                    if total_bytes > SILENCE_DURATION_BYTES:
                        bytes_to_remove = SILENCE_DURATION_BYTES
                        removed_bytes = 0
                        
                        while bytes_to_remove > 0 and self.hot_swap_audio_cache:
                            last_chunk = self.hot_swap_audio_cache[-1]
                            chunk_size = len(last_chunk)
                            
                            if chunk_size <= bytes_to_remove:
                                self.hot_swap_audio_cache.pop()
                                bytes_to_remove -= chunk_size
                                removed_bytes += chunk_size
                            else:
                                keep_size = chunk_size - bytes_to_remove
                                self.hot_swap_audio_cache[-1] = last_chunk[:keep_size]
                                removed_bytes += bytes_to_remove
                                bytes_to_remove = 0
                        
                        logger.info(f"🗑️ 静默超时：已清空音频缓存的最后 {removed_bytes} 字节（约{removed_bytes/32000:.1f}秒）")
                    else:
                        logger.info(f"🗑️ 静默超时：缓存总量不足4秒，全部清空（{total_bytes} 字节）")
                        self.hot_swap_audio_cache.clear()
            
            # Re-check before websocket side-effects
            if expected_session is not None and expected_session is not self.session and expected_session is not self.pending_session:
                logger.info("⏭️ handle_silence_timeout: expected_session stale before WS send, skipping")
                return
            
            if self.websocket and hasattr(self.websocket, 'client_state') and self.websocket.client_state == self.websocket.client_state.CONNECTED:
                session_for_reason = expected_session or self.session or self.pending_session
                timeout_api_type = str(
                    getattr(session_for_reason, "_api_type", "") or getattr(self, "core_api_type", "") or ""
                ).lower()
                timeout_model = str(
                    getattr(session_for_reason, "_model_lower", "")
                    or getattr(session_for_reason, "model", "")
                    or ""
                ).lower()
                is_free_timeout = timeout_api_type == "free" or "free" in timeout_model
                timeout_reason_code = (
                    "free_api_silence_timeout" if is_free_timeout else "silence_timeout"
                )
                if is_free_timeout:
                    await self.send_status(json.dumps({"code": "FREE_API_AUTO_CLOSE_VOICE"}))
                await self.websocket.send_json({
                    "type": "auto_close_mic",
                    "reason_code": timeout_reason_code,
                    "api_type": timeout_api_type,
                    "message": f"{self.lanlan_name}检测到长时间无语音输入，已自动关闭麦克风"
                })
            
            await self.end_session(by_server=True, expected_session=expected_session)
            
        except Exception as e:
            logger.error(f"处理静默超时时出错: {e}")
    
    async def handle_connection_error(self, message=None, *, expected_session=None):
        async with self.lock:
            is_pending = False
            if expected_session is not None:
                if expected_session is self.pending_session:
                    is_pending = True
                elif expected_session is not self.session:
                    logger.info("⏭️ handle_connection_error: expected_session stale (not current session), skipping")
                    return
            # Only flag the manager-level flag for main session errors (or unguarded calls).
            # A pending_session failure must not misclassify the main session as closed.
            if not is_pending:
                self.session_closed_by_server = True
        
        if is_pending:
            logger.info("⏭️ handle_connection_error: expected_session is pending_session, delegating to pending teardown")
            await self._teardown_pending_session_from_lifecycle_callback(expected_session, message)
            return
        
        if message:
            message_text = str(message)
            message_text_lower = message_text.lower()

            # Pre-classified structured errors from omni_realtime_client (JSON with "code")
            # Forward them directly so the frontend sees the original code.
            try:
                _parsed = json.loads(message_text) if message_text.startswith('{') else None
            except (json.JSONDecodeError, TypeError):
                _parsed = None
            if _parsed and isinstance(_parsed, dict) and _parsed.get('code'):
                await self.send_status(message_text)
            elif '欠费' in message_text_lower or 'standing' in message_text_lower:
                await self.send_status(json.dumps({"code": "API_ARREARS"}))
            elif 'quota' in message_text_lower or 'time limit' in message_text_lower:
                await self.send_status(json.dumps({"code": "API_QUOTA_TIME"}))
            elif '429' in message_text_lower or 'too many' in message_text_lower:
                await self.send_status(json.dumps({"code": "API_RATE_LIMIT"}))
            elif ('401' in message_text_lower or 'unauthorized' in message_text_lower
                    or 'authentication' in message_text_lower
                    or 'incorrect api key' in message_text_lower
                    or 'invalid_api_key' in message_text_lower
                    or ('invalid' in message_text_lower and 'key' in message_text_lower)):
                await self.send_status(json.dumps({"code": "API_KEY_REJECTED"}))
            elif _is_safety_violation_signal(message_text_lower):
                await self.send_status(json.dumps({"code": "API_POLICY_VIOLATION", "details": {"msg": message_text}}))
            elif '1008' in message_text_lower:
                await self.send_status(json.dumps({"code": "API_1008_FALLBACK", "details": {"msg": message_text}}))
            else:
                await self.send_status(json.dumps({"code": "API_UNKNOWN_ERROR", "details": {"msg": message_text}}))
        logger.info("💥 Session closed by API Server.")
        await self.disconnected_by_server(expected_session=expected_session)
    
    async def handle_repetition_detected(self):
        """Handle the repetition-detection callback: reset Focus state, notify the frontend"""
        try:
            logger.warning(f"[{self.lanlan_name}] 检测到高重复度对话")

            # Repetition recovery wiped _conversation_history — the Focus
            # accumulator charge / mode and the cadence baseline are evidence
            # from the now-erased conversation, so clear them too (对偶
            # _init_renew_status 的会话级清场). clear_focus emits no FOCUS_EXIT:
            # a degenerate looping episode is not a coherent episode to
            # synthesize. Best-effort — never block the frontend notice.
            try:
                await self.state.clear_focus()
                self._focus_scorer.reset()
                self._master_emotion.reset()
            except Exception as _focus_err:
                logger.debug(f"[{self.lanlan_name}] focus reset on repetition failed: {_focus_err}")

            # 向前端发送重复警告消息（使用 i18n key）
            if self.websocket and hasattr(self.websocket, 'client_state') and self.websocket.client_state == self.websocket.client_state.CONNECTED:
                await self.websocket.send_json({
                    "type": "repetition_warning",
                    "name": self.lanlan_name  # 前端会用这个名字填充 i18n 模板
                })
            
        except Exception as e:
            logger.error(f"处理重复度检测时出错: {e}")

    # ------------------------------------------------------------------
    # Tool calling — public API for agent_server / plugins
    # ------------------------------------------------------------------

    def register_tool(self, tool: ToolDefinition, *, replace: bool = True) -> None:
        """Register a tool with the unified registry.

        - ``tool.handler`` is an in-process callable (recommended) — same-process
          agent_bridge / built-in features take this path.
        - When ``tool.handler is None``, calls are routed to ``ToolRegistry``'s
          ``remote_dispatcher``, used for cross-process plugins / agent_server.
          The latter is attached by main_server at startup (HTTP forwarding to
          the corresponding plugin).

        ⚠️ This is the **synchronous** entry: it only updates registry state;
        session sync runs fire-and-forget via ``_fire_task``. If the caller needs
        to wait until "the tool is genuinely live on the wire" before returning,
        use ``await register_tool_and_sync(...)`` instead (the HTTP
        /api/tools/register endpoint already uses that path automatically).
        """
        self.tool_registry.register(tool, replace=replace)
        self._fire_task(self._sync_tools_to_active_session())

    async def register_tool_and_sync(self, tool: ToolDefinition, *, replace: bool = True) -> None:
        """The awaitable version of ``register_tool``: registers, then waits for the session sync push to finish.

        For remote entries like HTTP `/api/tools/register` — by the time the
        caller gets the response, the tools on the active/pending sessions are
        already up to date, with no "returned ok but the next model call still
        can't see the tool" window. Serialization is guaranteed by
        ``_tool_sync_lock``: multiple concurrent registers can't put the wire's
        session.update out of order.

        ⚠️ ``raise_on_failure=True``: if the session.update genuinely fails on the
        wire, propagate the exception upward, so HTTP /api/tools doesn't return a
        false ok=true.
        """
        self.tool_registry.register(tool, replace=replace)
        await self._sync_tools_to_active_session(raise_on_failure=True)

    def unregister_tool(self, name: str) -> bool:
        existed = self.tool_registry.unregister(name)
        if existed:
            self._fire_task(self._sync_tools_to_active_session())
        return existed

    async def unregister_tool_and_sync(self, name: str) -> bool:
        existed = self.tool_registry.unregister(name)
        if existed:
            await self._sync_tools_to_active_session(raise_on_failure=True)
        return existed

    def list_tools(self) -> list[str]:
        return self.tool_registry.names()

    def clear_tools(self, *, source: str | None = None) -> int:
        n = self.tool_registry.clear(source=source)
        if n > 0:
            self._fire_task(self._sync_tools_to_active_session())
        return n

    async def clear_tools_and_sync(self, *, source: str | None = None) -> int:
        n = self.tool_registry.clear(source=source)
        if n > 0:
            await self._sync_tools_to_active_session(raise_on_failure=True)
        return n

    async def _on_tool_call(self, call: ToolCall) -> ToolResult:
        """Bridge invoked by both clients when the model emits a tool
        call. Just forwards to the registry; the registry is process-
        global and outlives any single session.
        """
        return await self.tool_registry.execute(call)

    # ------------------------------------------------------------------
    # 内置 pseudo 工具：recall_memory
    # ------------------------------------------------------------------
    # 机制层占位：先让 offline / realtime 两条路径都能 register、把
    # description / parameters 推到 wire、收到模型的 tool call、回 result。
    # handler 当前固定返回"没有找到相关记忆"，等真实记忆检索接好后只
    # 替换 ``_handle_recall_memory_call`` 即可，不动注册 / 同步链路。

    def _register_builtin_tools(self) -> None:
        """Re-register the built-in tools, with description / parameter docs in the current
        ``user_language``. Calls ``tool_registry.register(replace=True)`` directly
        rather than the public ``register_tool``, to avoid firing unnecessary
        ``_sync_tools_to_active_session`` on hot paths like __init__ /
        start_session — this method's callers decide whether to sync.

        Kill-switch: set ``NEKO_DISABLE_BUILTIN_TOOLS=1`` to make this method
        return early without writing any builtin into the registry. Intended for
        A/B debugging of "suspected tool-schema-induced voice stream stutter /
        StepFun-proxy compatibility issues" — flip the switch → restart → the same
        frontend code runs in a "no builtin tools at all" state, comparing which
        baseline (with vs. without) misbehaves. Effective when the value is
        ``1`` / ``true`` / ``yes``.
        """
        if os.environ.get("NEKO_DISABLE_BUILTIN_TOOLS", "").strip().lower() in ("1", "true", "yes"):
            logger.info(
                "[builtin tools] NEKO_DISABLE_BUILTIN_TOOLS set — skipping recall_memory registration"
            )
            return
        _lang = normalize_language_code(self.user_language, format='short') or 'en'
        recall_tool = ToolDefinition(
            name="recall_memory",
            description=_loc(RECALL_MEMORY_TOOL_DESCRIPTION, _lang),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": _loc(RECALL_MEMORY_TOOL_QUERY_DESCRIPTION, _lang),
                    },
                    "time": {
                        "type": "string",
                        "description": _loc(RECALL_MEMORY_TOOL_TIME_DESCRIPTION, _lang),
                    },
                },
                # query / time 至少给一个：只给 time 就按时间回溯（不依赖
                # 内容），只给 query 就语义检索。两者都空时 handler 早退回
                # "没有找到相关记忆"。故 required 留空，靠 handler 兜底。
                "required": [],
            },
            handler=self._handle_recall_memory_call,
            metadata={"source": "builtin"},
        )
        self.tool_registry.register(recall_tool, replace=True)

    async def _handle_recall_memory_call(self, arguments: dict) -> str:
        """Handler for ``recall_memory`` — calls memory_server's
        ``/query_memory/{lanlan_name}`` over HTTP to run hybrid BM25 + cosine
        recall, formats the results as markdown bullets and returns them to the
        model.

        Logging is split into two tiers (privacy):
        - **INFO**: only reports name / mode / session / lang / hit count /
          elapsed ms. *No raw query text / no raw recalled text*.
          INFO persists to ``D:/Documents/N.E.K.O/logs/N.E.K.O_Main_<date>.log``
          and may be bundled and shipped out; raw memory text (containing user
          privacy) must not appear there.
        - **DEBUG**: only here do the raw query / recalled id list / full args
          land. DEBUG is hidden from the console by default and only goes to the
          project-dir _debug_ file, never shipped out.

        Failure fallback: if HTTP dies at any stage → return "no relevant
        memories found" as an empty result, letting the model continue the
        conversation flow. Never raise to the upstream wire, or one failed tool
        call would stall the model's whole turn.
        """
        _lang = normalize_language_code(self.user_language, format='short') or 'en'
        args_dict = arguments if isinstance(arguments, dict) else {}
        query = ""
        raw_query = args_dict.get("query")
        if isinstance(raw_query, str):
            query = raw_query.strip()
        time_arg = ""
        raw_time = args_dict.get("time")
        if isinstance(raw_time, str):
            time_arg = raw_time.strip()
        session_kind = type(self.session).__name__ if self.session is not None else "no-session"

        # 空入参早退：模型偶尔会用空 args 调一下"探探工具是否可用"，省一次
        # HTTP。但只要带了 time（按时间回溯，不依赖 query），就不算空入参。
        if not query and not time_arg:
            logger.info(
                "[recall_memory] called by name=%s mode=%s session=%s lang=%s "
                "→ empty query, no fetch",
                self.lanlan_name, self.input_mode, session_kind, _lang,
            )
            logger.debug("[recall_memory] empty-query args=%s", args_dict)
            return _loc(RECALL_MEMORY_TOOL_NO_RESULT, _lang)

        # 本轮首次真正发起回忆检索时，立刻喂一段"让我回忆一下"占位语音给 TTS，
        # 填补 hybrid_recall + 可能的多轮工具调用造成的空窗，避免猫娘那边长时间
        # 沉默。用 current_speech_id 去重，保证一轮只播一次（模型一轮里可能连调
        # 好几次 recall）。只进 TTS，不进前端气泡 / 不进历史；voice 模式下
        # feed_tts_chunk 因 use_tts=False 自动 no-op。
        cur_sid = self.current_speech_id
        if cur_sid and self.use_tts and getattr(self, "_recall_filler_spoken_sid", None) != cur_sid:
            try:
                # 关键：这一轮的 TTS worker 通常在正文首个 chunk 才懒启动，而
                # recall 发生在正文之前——若此时 worker 没起，filler 只会进
                # tts_pending_chunks，等正文来了 worker ready 才一起 flush，导致
                # 占位语音被粘在正文前一起播、失去"填补空窗"的意义。所以这里
                # 主动把管线（worker 线程 + response handler 任务）拉起来，让 worker
                # 在检索这几秒内就绪，filler 一就绪即被 handler flush 合成播放。
                # 但 NO_RETRY_TTS_CODES（API_ARREARS / API_KEY_REJECTED 等不可恢复态）下
                # 不要拉起：ensure_tts_pipeline_alive 直接调 _start_tts_thread，会绕过
                # _respawn_tts_worker 的 no-retry 闸，等于同轮每次 recall 都重启一次注定
                # 失败的 worker。此时跳过，直接走下面的早退放弃 filler。
                if getattr(self, "_last_tts_error_code", None) not in NO_RETRY_TTS_CODES:
                    await self.ensure_tts_pipeline_alive()
                # 冷启动有界等待：ensure_tts_pipeline_alive 只拉起 worker/handler，
                # 不等 __ready__。首轮 recall 紧接着 emit 时 tts_ready 可能还是 False，
                # 导致 _emit_recall_filler_tts 直接返回 False、首轮空窗依旧。TTS 通常
                # ~0.1s 就绪，这里给 ~1s 有界等待；超时则优雅放弃 filler（不阻塞回忆
                # 检索主流程），由后续 recall 调用或正文兜底。
                # 提前退出：sid 变化（用户打断）、worker 没起来/已挂、或已进入
                # NO_RETRY_TTS_CODES 这类不可恢复错误时，TTS 不可能再 ready，别白等
                # 满 1s——否则 TTS 确定失败时同轮每次 recall 都会吃满这段延迟。
                if not self.tts_ready:
                    for _ in range(20):
                        if (
                            self.current_speech_id != cur_sid
                            or not (self.tts_thread and self.tts_thread.is_alive())
                            or getattr(self, "_last_tts_error_code", None) in NO_RETRY_TTS_CODES
                        ):
                            break
                        await asyncio.sleep(0.05)
                        if self.tts_ready:
                            break
                # 用独立 worker-sid 把 filler 作为一段完整 utterance 立即合成出声，
                # 既能在检索空窗里马上播，又不会让正文（同 turn sid）被 worker 当成
                # "text_done 之后的残余文本"丢弃。详见 _emit_recall_filler_tts。
                _filler_ok = await self._emit_recall_filler_tts(
                    _loc(RECALL_MEMORY_TOOL_FILLER, _lang), cur_sid,
                )
                # 仅在真正入队成功后才标记"本轮已播过"：否则（worker 未 ready 等
                # 返回 False）会误判已预热，本轮后续 recall 不再补发 filler，且
                # handle_text_data 的 barge-in 守卫也会按"已预热"误跳过。
                if _filler_ok:
                    self._recall_filler_spoken_sid = cur_sid
                logger.debug(
                    "[recall_memory] filler TTS emitted=%s (sid=%s tts_ready=%s)",
                    _filler_ok, cur_sid, self.tts_ready,
                )
            except Exception as _filler_err:
                logger.debug("[recall_memory] filler TTS skipped: %s", _filler_err)

        # POST 到 memory_server。query 始终原样下传，不能因为带了 time 就清空
        # —— 下游路由：query + time → hybrid_recall(query, time_window=...) 做
        # "语义 + 时间"联合检索（窗口内按 query 排序，语义匹配保留）；只有 time
        # → 纯时间邻近回溯；time 解析失败还要靠 query 回落语义检索。
        post_body = {"query": query}
        if time_arg:
            post_body["time"] = time_arg
        result_payload: dict = {}
        recall_request_ok = False  # 仅当 memory server 真正成功返回时才置真
        try:
            from utils.internal_http_client import get_internal_http_client
            client = get_internal_http_client()
            resp = await client.post(
                f"http://127.0.0.1:{self.memory_server_port}/query_memory/{self.lanlan_name}",
                json=post_body,
                timeout=5.0,
            )
            if not resp.is_success:
                # WARNING 只带 status + body 长度（非敏感元数据）；body 原文
                # 含跨进程边界返回的字符串，可能夹带 query 回显 / 错误细节
                # 等含上下文内容，按 PR #1384 立的隐私分层规矩落 DEBUG。
                body_text = resp.text or ""
                logger.warning(
                    "[recall_memory] memory_server returned status=%s body_len=%d",
                    resp.status_code, len(body_text),
                )
                logger.debug(
                    "[recall_memory] non-success response body=%r",
                    body_text[:500],
                )
            else:
                result_payload = resp.json()
                recall_request_ok = True
        except Exception as exc:
            logger.warning(
                "[recall_memory] memory_server call failed (%s: %s); "
                "returning empty result",
                type(exc).__name__, exc,
            )

        results = result_payload.get("results") if isinstance(result_payload, dict) else None
        results = results if isinstance(results, list) else []
        elapsed_ms = result_payload.get("elapsed_ms", 0) if isinstance(result_payload, dict) else 0

        # INFO 只记 has_time（布尔），不落 time_arg 原值——time_arg 是用户
        # 原始输入，按本函数 docstring 立的隐私分层规矩（INFO 可能被打包外送）
        # 原文只进下面的 DEBUG。
        logger.info(
            "[recall_memory] called by name=%s mode=%s session=%s lang=%s "
            "has_time=%s → hits=%d elapsed=%.0fms",
            self.lanlan_name, self.input_mode, session_kind, _lang,
            bool(time_arg), len(results), elapsed_ms,
        )
        logger.debug(
            "[recall_memory] args=%s query=%r time=%r ids=%s",
            args_dict, query, time_arg,
            [r.get("id") for r in results],
        )

        if not results:
            # 同时带了 query 和 time 却 0 命中：八成是两个过滤条件叠加太窄
            # （时间窗口里没有语义匹配的条目）。别直接报"没有记忆"让模型放弃，
            # 提示它放宽——只留 time 或只留 query 再查一次。
            # 仅在请求**真正成功返回**时才给放宽提示：non-2xx / 异常也会落到
            # results=[]，那是 memory server 临时故障，不该误导模型"换条件重试"
            # 白烧刚收紧的工具迭代预算。
            if recall_request_ok and query and time_arg:
                return _loc(RECALL_MEMORY_TOOL_NO_RESULT_LOOSEN, _lang).format(query=query)
            return _loc(RECALL_MEMORY_TOOL_NO_RESULT, _lang)

        # 渲染：首行 i18n 总览 + 每条 markdown bullet
        # 格式: ``1. [tier/entity] text  (2026-05-01, 23 天前)``
        # tier/entity 是英文 enum 不翻译；text 是原始记忆原文不翻译
        # （按用户拍板）。时间锚点优先取事件真正发生时间 event_end_at →
        # event_start_at → created_at（与 persona 过时 block / temporal
        # _past_anchor 同口径），让模型看到的是"事件什么时候发生"而不是
        # "记忆什么时候写下"；再附一个本地化相对标签（X 天/周/月前）。
        from memory.temporal import (
            time_since_label as _time_label,
            _parse_iso_safe,
            to_naive_local,
        )
        lines = [_loc(RECALL_MEMORY_TOOL_FOUND_HEADER, _lang).format(n=len(results))]
        for i, r in enumerate(results, start=1):
            tier = r.get("tier") or "?"
            entity = r.get("entity") or "-"
            # str() coerce 防 malformed memory entry：facts/reflections.json
            # 走 JSON 序列化往返，理论上 text / 时间字段应是 str，但 manual
            # edit / 老格式残留 / 迁移 bug 都可能让它们变 list / int 等
            # truthy non-string（时间戳尤其常见，老数据可能存 epoch int）。
            # codex review (2 轮): 不 coerce → .strip() / [:10] crash → 整条
            # tool call 翻 is_error，模型反而不能正常走。
            text = str(r.get("text") or "").strip()
            # 锚点取 event_end_at → event_start_at → created_at 里**第一个能
            # 解析出来**的（不是第一个 truthy 的）：manual edit / 迁移可能让
            # 高优先级字段是个非空但解析不了的脏值，按 truthiness 选会卡住、
            # 把本可用的低优先级字段挡掉，渲染出乱码日期（Codex）。
            # _parse_iso_safe 对 None / int / list 等都安全返回 None。
            # date_part 和 rel 都从同一个归一后的 datetime 出，口径一致。
            anchor_dt = None
            for _cand in (
                r.get("event_end_at"),
                r.get("event_start_at"),
                r.get("created_at"),
            ):
                anchor_dt = to_naive_local(_parse_iso_safe(_cand))
                if anchor_dt is not None:
                    break
            date_part = anchor_dt.strftime("%Y-%m-%d") if anchor_dt else ""
            rel = _time_label(anchor_dt.isoformat(), lang=_lang) if anchor_dt else ""
            if date_part and rel:
                time_suffix = f"  ({date_part}, {rel})"
            elif date_part:
                time_suffix = f"  ({date_part})"
            else:
                time_suffix = ""
            lines.append(f"{i}. [{tier}/{entity}] {text}{time_suffix}")
        return "\n".join(lines)

    async def _sync_tools_to_active_session(self, *, raise_on_failure: bool = False) -> None:
        """Sync the registry's current state to all active clients.

        Covers:
        - ``self.session``: the currently active main session
        - ``self.pending_session``: the session prewarming during hot-swap (the
          window where the new catgirl is built but not yet formally swapped).
          Without syncing it, tools registered via register_tool before
          pending_session takes over would be lost after the hot-swap completes.

        ``apply_tools_to_session`` is only meaningful for ``OmniRealtimeClient``
        instances with a live ws connection; offline clients just rely on
        ``set_tools`` picking up the new snapshot at the next ``stream_text``.

        ⚠️ Serialization: ``_tool_sync_lock`` guarantees that concurrent calls
        push session.update one by one in call order. Otherwise the wire events
        from back-to-back ``register_tool / unregister_tool / clear_tools`` could
        arrive out of order, and the last snapshot might not match the registry's
        final state.
        """
        async with self._tool_sync_lock:
            # registry 在 lock 内才读，确保拿到的是 lock 持有期间的真实快照
            # （而不是入队时的旧值）。
            defs = self.tool_registry.all()
            targets = []
            if self.session is not None:
                targets.append(self.session)
            if self.pending_session is not None and self.pending_session is not self.session:
                targets.append(self.pending_session)
            if not targets:
                return
            errors: list[str] = []
            for sess in targets:
                role = "pending" if sess is self.pending_session else "active"
                try:
                    if hasattr(sess, "set_tools"):
                        sess.set_tools(defs)
                    if hasattr(sess, "set_tool_call_handler"):
                        sess.set_tool_call_handler(self._on_tool_call)
                    if isinstance(sess, OmniRealtimeClient) and sess.ws is not None:
                        await sess.apply_tools_to_session()
                except Exception as e:
                    err_text = f"{role}: {type(e).__name__}: {e}"
                    logger.warning("⚠️ Tool sync to %s session failed: %s", role, e)
                    errors.append(err_text)
            if errors and raise_on_failure:
                # 给 ``*_and_sync`` 调用方一个明确信号：wire 上没真生效，
                # 让 HTTP /api/tools 不要回 ok=true 假成功。
                raise RuntimeError("tool sync failed: " + "; ".join(errors))

    def _bind_session_lifecycle_callbacks(self, session):
        """Bind lifecycle callbacks with closure-captured session reference.
        
        Ensures that even if self.session is replaced later, the callbacks
        still carry a reference to the session they were bound to,
        enabling the expected_session guard to detect stale callbacks.
        """
        async def on_connection_error(message=None, session_ref=session):
            await self.handle_connection_error(message, expected_session=session_ref)
        
        # OmniRealtimeClient stores as .on_connection_error
        if isinstance(session, OmniRealtimeClient):
            session.on_connection_error = on_connection_error
        # OmniOfflineClient stores as .handle_connection_error
        elif isinstance(session, OmniOfflineClient):
            session.handle_connection_error = on_connection_error
        
        if hasattr(session, 'on_silence_timeout'):
            async def on_silence_timeout(session_ref=session):
                await self.handle_silence_timeout(expected_session=session_ref)
            session.on_silence_timeout = on_silence_timeout

    async def _teardown_pending_session_from_lifecycle_callback(self, expected_session, message=None):
        """Handle lifecycle callback (connection_error / silence_timeout) fired
        by a pending_session that has NOT yet been promoted to self.session.
        
        This avoids routing through the main session cleanup flow which would
        incorrectly kill the active main session.
        """
        if message:
            message_text = str(message)
            logger.warning(f"💥 Pending session lifecycle error: {message_text}")
        else:
            logger.warning("💥 Pending session lifecycle event (silence/disconnect)")
        
        if expected_session is self.pending_session:
            await self._cleanup_pending_session_resources()
            await self._reset_preparation_state(clear_main_cache=True)
        else:
            # pending_session already swapped or cleaned by someone else
            logger.info("⏭️ _teardown_pending: expected_session no longer matches pending_session, skipping")

    async def _reset_preparation_state(self, clear_main_cache=False, from_final_swap=False):
        """[Hot-swap related] Helper to reset flags and pending components related to new session prep.
        
        async because we await cancelled tasks to guarantee they have exited
        before clearing references — prevents >2 concurrent OmniRealtimeClient.
        """
        self.is_preparing_new_session = False
        self._require_context_append_current_delivery = False
        self.summary_triggered_time = None
        self.initial_cache_snapshot_len = 0
        
        # Snapshot task refs, cancel, await completion, THEN clear.
        # This ensures CancelledError handlers (e.g. _cleanup_pending_session_resources)
        # finish before we drop references, preventing races with newly created tasks.
        bg_task_ref = self.background_preparation_task
        swap_task_ref = self.final_swap_task if not from_final_swap else None
        
        tasks_to_await = []
        if bg_task_ref and not bg_task_ref.done():
            bg_task_ref.cancel()
            tasks_to_await.append(bg_task_ref)
        if swap_task_ref and not swap_task_ref.done():
            swap_task_ref.cancel()
            tasks_to_await.append(swap_task_ref)
        # 并行 wait：bg 和 swap task 已 cancel，串行最坏 4s 墙钟，gather 后 2s 封顶
        if tasks_to_await:
            async def _wait_one(t):
                try:
                    await asyncio.wait_for(t, timeout=2.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    # 清理路径：cancel 后的任务必然抛这两者之一，吞掉即可
                    pass
                except Exception as e:
                    # 非预期异常不应阻塞准备状态重置，记 debug 方便排障
                    logger.debug(f"_wait_one: ignored unexpected exception: {e}")
            await asyncio.gather(*(_wait_one(t) for t in tasks_to_await), return_exceptions=True)
        
        if self.background_preparation_task is bg_task_ref:
            self.background_preparation_task = None
        if not from_final_swap and self.final_swap_task is swap_task_ref:
            self.final_swap_task = None
        self.pending_session_warmed_up_event = None
        self.pending_session_final_prime_complete_event = None
        self.pending_use_tts = None

        if clear_main_cache:
            self.message_cache_for_new_session = []
            self.initial_next_session_context_snapshot_len = 0

    async def _cleanup_pending_session_resources(self):
        """[Hot-swap related] Safely cleans up ONLY PENDING connector and session if they exist AND are not the current main session."""
        # Stop any listener specifically for the pending session (if different from main listener structure)
        # The _listen_for_pending_session_response tasks are short-lived and managed by their callers.
        if self.pending_session:
            try:
                logger.info("🧹 清理pending_session资源...")
                await self.pending_session.close()
                logger.info("✅ Pending session已关闭")
            except Exception as e:
                logger.error(f"💥 清理pending_session时出错: {e}")
            finally:
                self.pending_session = None  # 即使close失败也要清除引用

    async def _init_renew_status(self):
        await self._reset_preparation_state(True)
        self.session_start_time = None
        await self._cleanup_pending_session_resources()  # close()后再置None，避免泄漏
        self.is_hot_swap_imminent = False
        # 状态机是 per-manager 的，跨 start_session/end_session 复用同一实例。
        # 若上一轮 proactive 在 PHASE1/PHASE2 中途 WS 断开、PROACTIVE_DONE 来不及
        # fire，phase/_preempted 会泄漏到新会话，堵死 can_start_proactive。
        # teardown 必须用 force=True：默认 reset() 会在活动 phase 上 no-op（保护
        # auto-start 不被误清），但 end_session 语义就是整轮收尾，必须强制清场。
        await self.state.reset(force=True)
        # 对偶 SM.reset 清 focus 态：scorer 的 cadence 基线也按会话隔离，新会话
        # 不继承上一会话的消息长度基线。master 情绪画像同样按会话隔离。
        self._focus_scorer.reset()
        self._master_emotion.reset()

    def _realtime_base_url(self) -> str:
        """Read the realtime route's base_url, for the native voice routing host remap
        (overseas free free→free_intl). Returns an empty string when unreadable, treated as non-lanlan.app."""
        try:
            return str((self._config_manager.get_model_api_config('realtime') or {}).get('base_url') or '')
        except Exception:
            return ''

    def _has_custom_tts(self) -> bool:
        """Decide whether the current session uses custom TTS (a cloned voice or a custom TTS URL)."""
        core_config = self._config_manager.get_core_config()
        _, uses_provider_native_voice = resolve_native_voice_for_routing(
            self.core_api_type,
            self.voice_id,
            self._config_manager.voice_id_exists_in_any_storage,
            realtime_base_url=self._realtime_base_url(),
        )
        if uses_provider_native_voice:
            return False
        gsv_voice_id = str(core_config.get('TTS_VOICE_ID') or '')
        gsv_enabled = (
            _as_bool(core_config.get('GPTSOVITS_ENABLED'), False)
            and not is_gsv_disabled_voice_id(gsv_voice_id)
        )
        if gsv_enabled:
            return True
        # 克隆音色始终走 custom 路径。
        if bool(self.voice_id) and not self._is_free_preset_voice:
            return True
        return False

    def _start_tts_thread(self):
        """Create and start the TTS worker thread.

        Selects the worker by voice_id / core_api_type, resolves the api_key,
        creates fresh request/response Queues and starts the daemon thread.
        tts_ready is reset to False around the call; the new worker must send
        __ready__ again.
        """
        # 重置就绪状态，新 worker 需重新握手
        self.tts_ready = False
        self._tts_runtime_key = None

        # 检查是否禁用了 TTS
        core_config = self._config_manager.get_core_config()
        if core_config.get('DISABLE_TTS', False):
            logger.info("TTS 已被用户禁用, 使用 dummy worker")
            tts_worker = dummy_tts_worker
            api_key_override = None
            provider_key = None
            api_key = ''
        else:
            has_custom = self._has_custom_tts()
            tts_worker, api_key_override, provider_key = get_tts_worker(
                core_api_type=self.core_api_type,
                has_custom_voice=has_custom,
                voice_id=self.voice_id or '',
            )
            tts_config = self._config_manager.get_model_api_config(
                'tts_custom' if has_custom else 'tts_default'
            )
            api_key = self.resolve_tts_api_key(provider_key, api_key_override, tts_config)

        # 根据实际选中的 TTS provider 类别决定是否启用流式文本规范化。
        # ws_bistream 类（qwen / step / cosyvoice）直接把文本碎片发给服务端处理，
        # normalizer 的 pending_spaces 延迟投递和 CJK 边界空格删除会干扰送达节奏。
        # http_sentence 类（cogtts / gemini / openai / minimax）做客户端句子分割，
        # 需要干净的文本，normalizer 在此有意义。
        # 注意：'free' 不在 registry 中 → meta 为 None → 走 fallthrough 启用 normalizer，
        # 因为 free 国外模式走 Gemini 后端，需要 CJK 空格清理。
        meta = TTS_PROVIDER_REGISTRY.get(provider_key) if provider_key else None
        self._tts_normalize_enabled = not meta or meta.category != "ws_bistream"

        self.tts_request_queue = Queue()
        self.tts_response_queue = Queue()

        self.tts_thread = Thread(
            target=tts_worker,
            args=(self.tts_request_queue, self.tts_response_queue, api_key, self.voice_id),
            daemon=True,
        )
        self._tts_runtime_key = self._build_tts_runtime_key()
        self.tts_thread.start()

    def _reset_tts_retry_state(self):
        """Cancel pending TTS respawn task and clear error/cooldown state.

        Safe to call whether or not a session is active.  When called from
        within an ``async with self.lock`` block the cancellation of
        ``_tts_respawn_task`` is race-free; outside the lock the worst case
        is a harmless double-cancel.
        """
        if self._tts_respawn_task and not self._tts_respawn_task.done():
            self._tts_respawn_task.cancel()
            self._tts_respawn_task = None
        self._last_tts_error_code = ''
        self._last_tts_respawn_time = 0.0
        self._tts_retry_notify_count = 0
        self._tts_done_queued_for_turn = False
        self._tts_done_pending_until_ready = False

    async def _teardown_tts_runtime(self, handler_task_ref, thread_ref,
                                     req_queue_ref, resp_queue_ref):
        """Tear down TTS handler task, worker thread, and drain queues.

        Operates only on the snapshot references passed in to prevent
        accidentally killing resources that have been recreated by a
        concurrent start_session.
        """
        if handler_task_ref and not handler_task_ref.done():
            handler_task_ref.cancel()
            try:
                await asyncio.wait_for(handler_task_ref, timeout=2.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            if self.tts_handler_task is handler_task_ref:
                self.tts_handler_task = None

        if thread_ref and thread_ref.is_alive():
            try:
                # 使用独立的 shutdown sentinel；(None, None) 在 worker 里是
                # "本轮 utterance 结束、flush 缓冲区"，并不会让 worker 退出。
                req_queue_ref.put(("__shutdown__", None))
                await asyncio.to_thread(thread_ref.join, 2.0)
            except Exception as e:
                logger.error(f"💥 关闭TTS线程时出错: {e}")

            if thread_ref.is_alive():
                logger.warning("⚠️ TTS worker 未在超时内退出，清除引用以允许重建")
                if self.tts_thread is thread_ref:
                    self.tts_thread = None
            else:
                if self.tts_thread is thread_ref:
                    self.tts_thread = None
                # 仅在线程确实已停止后才安全地清空队列
                try:
                    while not req_queue_ref.empty():
                        req_queue_ref.get_nowait()
                except Exception:
                    pass
                try:
                    while not resp_queue_ref.empty():
                        resp_queue_ref.get_nowait()
                except Exception:
                    pass

        # 只在被拆除的 runtime 仍是当前 runtime 时才清全局 TTS 状态，
        # 避免新 session 已创建新队列/worker 后被旧 teardown 误重置
        if resp_queue_ref is self.tts_response_queue:
            async with self.tts_cache_lock:
                self.tts_ready = False
                self.tts_pending_chunks.clear()

    def _respawn_tts_worker(self):
        """Respawn the TTS worker when its thread is detected dead, without blocking for readiness.

        Once the new worker is ready it sends the __ready__ signal through
        response_queue; tts_response_handler receives it and calls
        _flush_tts_pending_chunks to flush the cache.

        Rate limit: at most one respawn per 12 seconds, avoiding a reconnect
        storm when the service is completely down.
        """
        if self.tts_thread and self.tts_thread.is_alive():
            return

        # 如果上次错误属于不应自动重试的类型，直接跳过 respawn
        if self._last_tts_error_code in NO_RETRY_TTS_CODES:
            logger.warning(f"⚠️ _respawn_tts_worker: 上次错误为 {self._last_tts_error_code}，跳过自动重试")
            return

        import time
        now = time.monotonic()
        if now - self._last_tts_respawn_time < 12.0:
            return  # 冷却中，保留待执行的延迟任务和错误码状态

        # 通过冷却检查后，取消可能仍在等待的延迟重试任务，既然已经在直接 respawn 了
        if self._tts_respawn_task and not self._tts_respawn_task.done():
            self._tts_respawn_task.cancel()
            self._tts_respawn_task = None
        self._last_tts_respawn_time = now

        logger.info("🔄 TTS Worker 已死亡，尝试重新拉起...")
        self._start_tts_thread()

        # 重新启动 tts_response_handler 以监听新队列
        if self.tts_handler_task and not self.tts_handler_task.done():
            self.tts_handler_task.cancel()
        self.tts_handler_task = asyncio.create_task(self.tts_response_handler())

        logger.info("🔄 TTS Worker 已重新拉起，等待运行时就绪信号...")

    async def _flush_tts_pending_chunks(self):
        """Send the cached TTS text chunks to the TTS queue"""
        async with self.tts_cache_lock:
            if self.tts_pending_chunks:
                chunk_count = len(self.tts_pending_chunks)
                logger.info(f"TTS就绪，开始处理缓存的 {chunk_count} 个文本chunk...")

                if self.tts_thread and self.tts_thread.is_alive():
                    for speech_id, text in self.tts_pending_chunks:
                        try:
                            self._enqueue_tts_text_chunk(speech_id, text)
                        except Exception as e:
                            logger.error(f"💥 发送缓存的TTS请求失败: {e}")
                            break

                # 清空缓存
                self.tts_pending_chunks.clear()

            if self._tts_done_pending_until_ready:
                status = self._request_tts_done_locked()
                if status == "queued":
                    logger.debug("_flush_tts_pending_chunks: pending 文本已刷出，补发 TTS done 信号")
    
    async def _flush_pending_input_data(self):
        """Send the cached input data to the session"""
        async with self.input_cache_lock:
            if not self.pending_input_data:
                return

            if self.session and self.is_active:
                # 缓存阶段（_stream_data_now）不知道 session 最终是 voice 还是
                # text。如果最终启好的是 voice session，缓存里的 text 输入若
                # 直接 flush 进 _process_stream_data_internal，会触发 4977-4995
                # 的"硬撕 voice → 重建 text"自动切换路径，把刚 ready 的 voice
                # session 撕成 CHARACTER_LEFT / "角色离开"——这是用户在切音色
                # 后开语音、麦启动期打字的典型 race。这里只防御 text → voice
                # 这一条不对偶的路径；screen / camera 等 vision 输入会在
                # _process_stream_data_internal 里路由到
                # OmniRealtimeClient.stream_image（5262-5278），是 voice session
                # 的合法路径，不能误丢。audio 在 _stream_data_now 缓存阶段已经
                # 直接 return 不缓存，pending_input_data 不会出现 audio。
                is_voice_session = isinstance(self.session, OmniRealtimeClient)
                dropped_text_for_voice = 0
                for message in self.pending_input_data:
                    msg_input_type = message.get("input_type")
                    try:
                        # 重新调用stream_data处理缓存的数据
                        # 注意：这里直接处理，不再缓存（因为session_ready已设为True）
                        if msg_input_type == "audio":
                            await self._enqueue_audio_stream_data(message)
                        else:
                            if is_voice_session and msg_input_type in _TEXT_SESSION_INPUT_TYPES:
                                dropped_text_for_voice += 1
                                continue
                            await self._process_stream_data_internal(message)
                    except Exception as e:
                        logger.error(f"💥 发送缓存的输入数据失败: {e}")
                        break
                if dropped_text_for_voice:
                    logger.info(
                        "[%s] _flush_pending_input_data: dropped %d cached text "
                        "message(s) because final session is voice mode",
                        self.lanlan_name, dropped_text_for_voice,
                    )

            # 清空缓存
            self.pending_input_data.clear()
    
    async def _flush_hot_swap_audio_cache(self):
        """After hot-swap completes, push cached audio data to the new session in a loop until the cache is stably empty"""
        # 设置标志，让新的音频继续缓存而不是直接发送
        self.is_flushing_hot_swap_cache = True
        
        try:
            # 检查session是否可用
            if not self.session or not self.is_active:
                logger.warning("⚠️ 热切换音频缓存刷新时session不可用，丢弃缓存")
                async with self.hot_swap_cache_lock:
                    self.hot_swap_audio_cache.clear()
                return
            
            # 检查session类型
            if not isinstance(self.session, OmniRealtimeClient):
                logger.debug("热切换音频缓存仅适用于语音模式，当前session类型不匹配，跳过flush")
                async with self.hot_swap_cache_lock:
                    self.hot_swap_audio_cache.clear()
                return
            
            max_iterations = 20  # 最多迭代20次，防止无限循环
            iteration = 0
            total_chunks_sent = 0
            
            logger.info("🔄 开始循环推送热切换音频缓存...")
            
            while iteration < max_iterations:
                # 检查并取出当前缓存
                async with self.hot_swap_cache_lock:
                    cache_len = len(self.hot_swap_audio_cache)
                    
                    if cache_len == 0:
                        break
                    else:
                        audio_chunks = self.hot_swap_audio_cache.copy()
                        self.hot_swap_audio_cache.clear()
                
                # 如果有缓存，合并并发送
                if cache_len > 0:
                    logger.info(f"🔄 推送第{iteration+1}批音频缓存: {cache_len} 个chunk")
                    
                    # 合并小chunk成大chunk（节流）
                    combined_audio = b''.join(audio_chunks)
                    
                    # 计算每个大chunk的大小（16kHz，约10ms = 160 samples = 320 bytes）
                    original_chunk_size = 320  # 16kHz: 160 samples × 2 bytes
                    large_chunk_size = original_chunk_size * self.HOT_SWAP_FLUSH_CHUNK_MULTIPLIER
                    
                    # 分批发送
                    for i in range(0, len(combined_audio), large_chunk_size):
                        chunk = combined_audio[i:i + large_chunk_size]
                        try:
                            await self.session.stream_audio(chunk)
                            await asyncio.sleep(0.025)
                            total_chunks_sent += 1
                        except Exception as e:
                            logger.error(f"💥 推送音频缓存失败: {e}")
                            return  # 推送失败，放弃
                
                iteration += 1
                
            if iteration >= max_iterations:
                logger.warning(f"⚠️ 达到最大迭代次数({max_iterations})，停止推送")
            
            logger.info(f"✅ 热切换音频缓存推送完成，共推送约 {total_chunks_sent} 个大chunk，迭代 {iteration} 次")
            
        finally:
            # 无论如何都要清除flag，恢复正常音频输入
            self.is_flushing_hot_swap_cache = False

    
    def _resolve_session_use_tts(
        self,
        input_mode: str,
        realtime_config: dict,
        core_config_snapshot: dict,
        *,
        log_prefix: str = "",
    ) -> bool:
        """Resolve whether this session should use the external TTS pipeline."""
        has_custom_tts_config = (
            bool(core_config_snapshot.get('GPTSOVITS_ENABLED'))
            and not is_gsv_disabled_voice_id(core_config_snapshot.get('TTS_VOICE_ID', ''))
        )

        if input_mode == 'text':
            return True
        # Livestream 上游是 free 路 Gemini 系，服务端始终承担原生 TTS。客户端
        # 角色卡的 voice_id 不论是不是 free preset，都不应该再开外部 TTS——
        # 否则文本会被客户端按整句喂给 tts_proxy，丢掉服务端 Gemini → core_proxy
        # → CV3 那条真 bistream 路径的首音频延迟优势。
        #
        # PR #1369 在原 free-preset gate 第三个条件里 OR 了 livestream-active，
        # 但前两个 AND（_is_free_preset_voice / core_api_type='free'）没拆，
        # 导致 livestream + 非 free preset 音色（克隆 / 空 voice_id / 主播
        # 自定义未识别为 preset）仍会 fallback 到外部 TTS。这里独立早退兜底。
        # _is_livestream_active 内部已经 gate 了 core_api_type='free'。
        if self._is_livestream_active():
            logger.info(f"{log_prefix}🎙️ livestream 模式：使用服务端原生语音，跳过外部 TTS")
            return False
        if self._is_vllm_omni_tts_enabled(core_config_snapshot):
            logger.info(f"{log_prefix}🔊 语音模式：检测到 vLLM-Omni TTS provider，将使用外部 TTS")
            return True
        base_url = realtime_config.get('base_url', '')
        _, uses_provider_native_voice = resolve_native_voice_for_routing(
            self.core_api_type,
            self.voice_id,
            self._config_manager.voice_id_exists_in_any_storage,
            realtime_base_url=base_url,
        )
        if uses_provider_native_voice:
            logger.info(f"{log_prefix}🔊 {self.core_api_type} 原生音色 '{self.voice_id}' 将直接传入 RealtimeClient")
            return False
        if (
            self._is_free_preset_voice
            and self.core_api_type == 'free'
            and 'lanlan.tech' in realtime_config.get('base_url', '')
        ):
            logger.info(f"{log_prefix}🆓 免费预设音色 '{self.voice_id}' 将直接传入 session config，不启动外部 TTS")
            return False
        if self.voice_id or has_custom_tts_config:
            if has_custom_tts_config and not self.voice_id:
                logger.info(f"{log_prefix}🔊 语音模式：检测到自定义TTS配置，将使用自定义TTS覆盖原生语音")
            return True
        return False

    def _get_voice_id(self) -> str:
        raw = get_reserved(
            self.lanlan_basic_config[self.lanlan_name],
            'voice_id',
            default='',
            legacy_keys=('voice_id',),
        )
        # 声音来源统一架构惰性迁移：characters.json 里 voice 可能是旧扁平串，也可能是
        # 用户设音色后迁成的结构对象 {source,provider,ref}。read_legacy_voice_id 把两形态
        # 统一读成 dispatch/route gating 一直消费的 legacy 前缀串（顺带 strip 收口空白），
        # 下游 literal 比较 / is_free_preset_voice_id 等无需感知存储形态。
        from utils.voice_config import read_legacy_voice_id
        return read_legacy_voice_id(raw)

    def _apply_voice_id_for_route(self) -> None:
        """Resolve the character card's voice_id into self.voice_id /
        self._is_free_preset_voice according to the current route.

        Shared by __init__ / start_session / _background_prepare_pending_session:
        reads _get_voice_id() → corrects the pairing between free presets and
        core_api_type. Centralized here to prevent rule drift.

        Historically this also suppressed voice delivery via "overseas lanlan.app
        hard-overrides to Leda"; now overseas free uniformly goes through
        www.lanlan.app with voice pass-through (full Gemini set + yui, claimed by
        the free_intl provider), no more suppression — stale StepFun/free preset
        voices won't hit the free_intl catalog under the overseas route and
        naturally fall through, no pre-clearing needed.

        An empty voice_id stays empty: under overseas free, the "empty → default
        voice" mapping is left to the server (www.lanlan.app); the client no
        longer injects a fallback voice.
        """
        raw_voice_id = self._get_voice_id()
        self.voice_id = raw_voice_id
        self._is_free_preset_voice = is_free_preset_voice_id(raw_voice_id)
        # free preset 选了但当前非 free 模式 → 不下发，避免把 preset id 透给别的 provider。
        if self._is_free_preset_voice and self.core_api_type != 'free':
            self.voice_id = ''
            self._is_free_preset_voice = False

    def _is_livestream_active(self) -> bool:
        """Livestream is a sub-mode on top of core_api_type='free'; both must hold simultaneously."""
        return self.core_api_type == 'free' and is_livestream_active()

    def _resolve_realtime_voice(self, realtime_config: dict):
        """Decide the voice that OmniRealtimeClient passes to the server/provider.

        Priority:
        1. core_api_type has a registered native voice provider and voice_id hits
           its catalog (Gemini Puck / Chinese male, etc.) → normalized and consumed
           directly by the provider client.
        2. livestream sub-mode enabled with a configured voice_id → use the
           livestream voice_id (bypassing the free_voices preset gate; the derived
           base_url no longer contains lanlan.tech)
        3. otherwise keep the original logic: deliver only when the character's
           voice is a free preset, core_api_type='free' and base_url still points
           at the lanlan.tech domain, to avoid leaking preset ids to non-lanlan
           services. Overseas free (free + *.lanlan.app) yui / Gemini voices are
           remapped via free_intl by resolve_native_voice_for_routing and hit
           step 1 directly.
        """
        base_url = realtime_config.get('base_url', '')
        voice_name, uses_provider_native_voice = resolve_native_voice_for_routing(
            self.core_api_type,
            self.voice_id,
            self._config_manager.voice_id_exists_in_any_storage,
            realtime_base_url=base_url,
        )
        if uses_provider_native_voice:
            return voice_name
        if self._is_livestream_active():
            ls_voice = get_livestream_config().get('voice_id', '')
            if ls_voice:
                return ls_voice
        base_url = realtime_config.get('base_url', '') or ''
        if (self._is_free_preset_voice
                and self.core_api_type == 'free'
                and 'lanlan.tech' in base_url):
            return self.voice_id
        return None

    def _resolve_realtime_free_voice(self, realtime_config: dict):
        """Backward-compatible wrapper for older callers/tests."""
        return self._resolve_realtime_voice(realtime_config)

    def _enqueue_voice_migration_notice(self, legacy_names: list) -> None:
        """Push the voice migration notice into the buffer pool, delegating to the module-level function for unified dedup."""
        enqueue_voice_migration_notice(legacy_names)

    def normalize_text(self, text): # 对文本进行基本预处理
        text = text.strip()
        text = text.replace("\n", "")
        if contains_chinese(text):
            text = replace_blank(text)
            text = replace_corner_mark(text)
            text = text.replace(".", "。")
            text = text.replace(" - ", "，")
            text = remove_bracket(text)
            text = re.sub(r'[，、]+$', '。', text)
        else:
            text = remove_bracket(text)
        text = self.emoji_pattern2.sub('', text)
        text = self.emoji_pattern.sub('', text)
        if is_only_punctuation(text) and text not in ['<', '>']:
            return ""
        return text

    async def _handle_session_start_exception(self, e: BaseException, input_mode: str, diag_start: float) -> None:
        """Unified handling of session start failure: log, send the status code, send_session_failed, cleanup.

        Used by start_session's outer except, covering both the prelude
        (_cleanup_pending_session_resources / end_session etc.) and the gather
        block, so the frontend doesn't get stuck on preparing.
        """
        self.session_start_failure_count += 1
        self.session_start_last_failure_time = datetime.now()
        logger.error(f"[语音会话诊断] start_session 失败 (总耗时: {time.time() - diag_start:.2f}秒): {e}")
        # Telemetry：语音会话启动失败 —— 语音优先桌宠，voice 在用户开口前就坏掉
        # = 静默 D1 流失（现在完全看不到）。reason 用异常类名（低基数 enum）。
        # **仅 audio 模式计**：本收口对 text/audio 两种 start_session 都用，text
        # 启动失败不该误标成 voice_setup_failed 污染该信号。best-effort 不阻塞收口。
        if input_mode == 'audio':
            try:
                from utils.instrument import counter as _instr_counter
                _instr_counter("voice_setup_failed", reason=type(e).__name__[:32])
            except Exception:
                pass  # 埋点 best-effort：instrument 不可用也不能挡失败收口流程
        error_str = str(e)

        is_memory_server_error = isinstance(e, ConnectionError) and any(
            kw in error_str.lower() for kw in ["memory server", "记忆服务"]
        )

        if is_memory_server_error:
            logger.error(f"🧠 {error_str}")
            await self.send_status(json.dumps({"code": "MEMORY_SERVER_NOT_RUNNING"}))
            # Memory Server 错误不计入失败次数（这是配置问题而非网络问题）
            self.session_start_failure_count -= 1
            self._memory_error_retry_after = time.time() + self._memory_error_cooldown_seconds
        else:
            error_message = f"Error starting session: {e}"
            logger.exception(f"💥 {error_message} (失败次数: {self.session_start_failure_count})")

            if self.session_start_failure_count >= self.session_start_max_failures:
                # 仅在熔断"刚跳闸"时打 CRITICAL + 推 status；之后的失败由
                # start_session 早退拦截（理论上不会再走到这里），CRITICAL 只发一次。
                if not self._session_start_circuit_open:
                    self._session_start_circuit_open = True
                    critical_message = f"⛔ Session启动连续失败{self.session_start_failure_count}次，已停止自动重试。请检查网络连接和API配置，然后刷新页面重试。"
                    logger.critical(critical_message)
                    await self.send_status(json.dumps({"code": "SESSION_START_CRITICAL", "details": {"count": self.session_start_failure_count}}))
            else:
                await self.send_status(json.dumps({"code": "SESSION_START_FAILED", "details": {"error": str(e), "count": self.session_start_failure_count}}))

            if 'WinError 10061' in error_str or 'WinError 10054' in error_str:
                if str(self.memory_server_port) in error_str or '48912' in error_str:
                    await self.send_status(json.dumps({"code": "MEMORY_SERVER_CRASHED", "details": {"port": self.memory_server_port}}))
                else:
                    await self.send_status(json.dumps({"code": "CONNECTION_REFUSED"}))
            elif ('401' in error_str or 'unauthorized' in error_str.lower()
                    or 'authentication' in error_str.lower()
                    or 'incorrect api key' in error_str.lower()
                    or 'invalid_api_key' in error_str.lower()
                    or ('invalid' in error_str.lower() and 'key' in error_str.lower())):
                await self.send_status(json.dumps({"code": "API_KEY_REJECTED"}))
            elif '429' in error_str:
                await self.send_status(json.dumps({"code": "API_RATE_LIMIT_SESSION"}))
            elif 'HTTP 503' in error_str:
                await self.send_status(json.dumps({"code": "UPSTREAM_SERVER_BUSY"}))
            elif 'All connection attempts failed' in error_str:
                await self.send_status(json.dumps({"code": "LLM_CONNECTION_FAILED"}))
            else:
                await self.send_status(json.dumps({"code": "CONNECTION_CLOSED_ABNORMAL", "details": {"error": error_str}}))

        # 必须在 cleanup 之前发送，因为 cleanup 会清空 websocket 引用
        await self.send_session_failed(input_mode)
        # reset_starting_count=False：本函数从失败的 start_session 的 except 里调用，
        # 那次 start_session 的 finally 才是 _starting_session_count guard 的唯一所有者
        # 并会在最后递减它。若让这里的 cleanup 提前把 count 清 0，会开出一个"失败任务
        # 尚未完全收尾、但 count 已 0"的窗口，等待中的跨模式重启会据此重入，随后被
        # 失败任务残余的 cleanup（清 websocket）和 finally（减 guard）clobber（Codex P2）。
        await self.cleanup(reset_starting_count=False)
        # 但 reset_starting_count=False 会让 end_session 的 inactive-early 路径跳过
        # pending_input_data.clear()（那块与 guard 释放耦合），导致本次失败启动期间缓存的
        # 输入残留、被下次成功启动的 _flush_pending_input_data() 误注入（Codex P2）。
        # 这里显式补清本次失败 start 自己的输入：此刻 count 仍被本次 finally 持有(>0)，
        # 没有并发 start 穿过、缓存里只可能是本次失败 start 的输入，清理安全。
        # 不走 end_session 的 gating 改动，rebuild 路径(同样 reset_starting_count=False 但
        # 需要保留输入回放)语义不受影响。
        async with self.input_cache_lock:
            self.session_ready = False
            self.pending_input_data.clear()
            self._clear_pending_context_appends()

    @property
    def is_starting(self) -> bool:
        """The window where the start_session coroutine is running but is_active isn't True yet.
        Externals (e.g. the catgirl-switch path) use this to decide whether to keep
        the current manager instance, avoiding replacing a manager mid-initialization
        and leaking an orphan session.
        """
        return self._starting_session_count > 0

    @property
    def starting_input_mode(self):
        """Return the target mode being started, avoiding reads of an input_mode that hasn't finished switching."""
        if self._starting_session_count <= 0:
            return None
        return self._starting_input_mode

    def reset_session_start_circuit(self) -> None:
        """Clear the circuit breaker + failure counter + memory cooldown. Only for
        websocket_router upon receiving an explicit user start_session action — that is
        equivalent to "the user saw CRITICAL, chose to retry, and declares the config
        fixed". So _memory_error_retry_after is cleared along the way; otherwise the
        user would still wait an extra 10 seconds after starting the memory server.
        Internal recovery paths must never call this, or the circuit breaker becomes
        meaningless."""
        if (self._session_start_circuit_open
                or self.session_start_failure_count
                or self._memory_error_retry_after):
            logger.info(f"🔄 重置 session 启动熔断 (之前失败 {self.session_start_failure_count} 次)")
        self._session_start_circuit_open = False
        self.session_start_failure_count = 0
        self.session_start_last_failure_time = None
        self._memory_error_retry_after = 0

    def shutdown(self) -> None:
        """Manager-level shutdown — cancels the idle reset background task. Caller:
        main_server's ``_init_character_resources``, before replacing the old
        manager with a new one.

        Why needed: ``_idle_session_reset_task`` is a bound-method coroutine
        holding a strong reference to ``self`` — after a config hot-reload creates
        a new LLMSessionManager to replace the old one, the old manager should be
        GC'd, but the leftover task wakes every 60s (even though it only takes the
        ``is_active==False`` early-exit branch), extending the old manager's
        lifetime indefinitely; N copies accumulate after repeated reloads.
        """
        task = self._idle_session_reset_task
        if task is not None and not task.done():
            task.cancel()
        self._idle_session_reset_task = None

    def _ensure_idle_session_reset_loop(self) -> None:
        """Lazily start the idle reset background task. Idempotent, safe to call repeatedly."""
        if self._idle_session_reset_task is not None and not self._idle_session_reset_task.done():
            return
        try:
            self._idle_session_reset_task = asyncio.create_task(self._idle_session_reset_loop())
        except RuntimeError:
            # 极端情况：没有 running event loop（不该发生于 start_session 路径）
            logger.debug("[%s] _ensure_idle_session_reset_loop: no running loop, skip", self.lanlan_name)

    async def _idle_session_reset_loop(self) -> None:
        """Periodically check the user's silence duration; past the threshold, proactively
        end_session so the next message triggers fresh /new_dialog context injection.
        Guards: responding / takeover / session starting / no activity timestamp →
        skip this round, re-evaluate next round.
        """
        while True:
            try:
                await asyncio.sleep(IDLE_SESSION_RESET_CHECK_INTERVAL_SECONDS)
                if not self.is_active or self.session is None:
                    continue
                if self._starting_session_count > 0:
                    continue
                if self._takeover_active:
                    continue
                if getattr(self.session, '_is_responding', False):
                    continue
                last_activity = self.last_user_activity_time
                if last_activity is None:
                    continue
                idle_seconds = time.time() - last_activity
                if idle_seconds < IDLE_SESSION_RESET_THRESHOLD_SECONDS:
                    continue
                # 快照当前 session：传给 end_session 的 expected_session 守卫，
                # 在 end_session 内部多个 await 期间若用户触发新一轮 start_session
                # 把 self.session 换掉了，end_session 会早退而不会误清新 session
                # 或 _starting_session_count guard（参见 end_session 6011-6013 注释）。
                session_snapshot = self.session
                logger.info(
                    "[%s] idle_session_reset: 用户静默 %.0fs ≥ %ds，主动关闭 session 让下一条消息刷新上下文",
                    self.lanlan_name, idle_seconds, IDLE_SESSION_RESET_THRESHOLD_SECONDS,
                )
                try:
                    # by_server=True：抑制末尾的 CHARACTER_LEFT 状态推送，把本路径
                    # 与用户主动离开的语义区分开。reset_starting_count=False：
                    # expected_session 早退已经把 race 兜住了，再叠一层保险防止
                    # await 期间挤进来的新 start_session guard 被清零。
                    await self.end_session(
                        by_server=True,
                        expected_session=session_snapshot,
                        reset_starting_count=False,
                    )
                except Exception as e:
                    logger.warning("[%s] idle_session_reset: end_session 失败: %s", self.lanlan_name, e)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("[%s] idle_session_reset 单轮异常: %s", self.lanlan_name, e)

    async def _maybe_kick_activity_loop_for_context_prompt(self) -> None:
        """Start the activity tracker's background heartbeat.

        Context-prompt detection (entering gaming/entertainment / entering focused
        work) hangs off the tracker's 20s heartbeat, and the heartbeat lazy-starts
        only on the first get_snapshot; get_snapshot in turn is only called by
        paths where proactive chat is on. Proactive chat defaults to off at first
        start, so without an explicit kick a user who hasn't enabled proactive chat
        would never detect entering a game and the prompt would never show. Here we
        kick once when the session comes up.

        The context prompt used to be gated to the vision_chat_default_off A/B group;
        it's now merged into main and open to everyone. OS signal collection is still
        gated on the user having *explicitly* allowed autonomous vision (privacy mode
        off), but the topic candidate heartbeat is privacy-independent and should
        run even when vision is disabled.
        """
        try:
            self._activity_tracker.ensure_activity_guess_loop_started()
            # 只有当 proactiveVisionEnabled 已被显式落盘为 True 才 kick：get_snapshot 会起
            # SystemSignalCollector 采集窗口/进程信号，且绕过隐私模式（loop 只跳过 LLM、
            # collector 仍在采）。不能用 is_privacy_mode_active()——它在 proactiveVisionEnabled
            # 缺失时 fail-open 成「隐私关」，于是首启 settings 尚未同步的窗口里，会把 UI 默认
            # 隐私开（海外首启 proactiveVisionEnabled 默认 false）的用户误判成可采集，启动一次
            # session 就采了窗口/进程（Codex P1）。这里改读原始落盘值，缺失/False 一律不 kick，
            # 等下一次 session（settings 已同步、用户确为 vision 开）再拉起；隐私开的用户本就是
            # no-op（屏幕分享来源开不了），不 kick 无损。
            from utils.preferences import aload_global_conversation_settings
            settings = await aload_global_conversation_settings()
            if settings.get('proactiveVisionEnabled') is not True:
                return
            # 清情境弹窗基线：tracker 跨 session 长存，若用户上个 session 结束时就在
            # 游戏/工作、这个 session 仍在同一状态，不清就检测不到「进入」、本会话漏弹。
            self._activity_tracker.reset_context_prompt_baseline()
            await self._activity_tracker.get_snapshot()
        except Exception as e:
            logger.debug("[%s] 活动心跳 kick 失败: %s", self.lanlan_name, e)

    async def start_session(self, websocket: WebSocket, new=False, input_mode='audio',
                            *, user_initiated=False, _allow_cross_mode_restart=True):
        # user_initiated：True 仅由 websocket_router 的 start_session action 传入，
        # 标记"用户显式点击启动"。跨模式撞车时只有用户显式请求才会等 in-flight
        # 落定后改起目标模式；后台 proactive / greeting 的 auto-start 跨模式撞车
        # 仍走静默 return（保持原行为，避免后台 text 启动反过来顶掉用户的语音会话）。
        # _allow_cross_mode_restart：跨模式重启重入时置 False，把递归深度封到 1，
        # 二次并发撞车回落静默 return 而非无界递归。
        # 之前每次 start_session 都无脑用 get_global_language() 覆盖 user_language，
        # 想"语言变更即时生效"，但实际效果是把 ws greeting_check 已经推上来的
        # 前端 i18n 真值（例如 Steam=zh / 系统=en 时正确的 'zh-CN'）一律打回错的
        # 全局缓存值（race 失败时的 'en'），让游戏 / proactive / memory 的 prompt
        # 全部回退英文。改为：仅在 user_language 还没被设过时才 seed 一次，已经
        # 有 session 真值就保留——全局缓存晚到的更新由 refresh_global_language
        # 路径独立处理（见 main_routers/config_router.py:steam_language 端点）。
        topic_language_seed = None
        if not getattr(self, 'user_language', None):
            topic_language_seed = normalize_language_code(get_global_language_full(), format='full')
            self.user_language = normalize_language_code(topic_language_seed, format='short')
            self._conversation_turn_language = topic_language_seed
        self._set_conversation_turn_language(
            self._conversation_turn_language
            or topic_language_seed
            or self.user_language
        )
        # 重置防刷屏标志
        self.session_closed_by_server = False
        self.last_audio_send_error_time = 0.0
        # 熔断早退：达到失败上限后，所有内部 recovery 路径在此返回，
        # 避免 stream_data / _process_stream_data_internal 每个音频包都触发
        # 一次连接尝试导致日志被刷屏。用户显式 retry（websocket_router 的
        # start_session action）会在那边先调 reset_session_start_circuit() 清掉。
        if self._session_start_circuit_open:
            logger.debug("Session启动熔断已跳闸，忽略本次启动请求（等用户刷新/重试）")
            return
        # 检查是否正在启动中
        if self._starting_session_count > 0:
            # 另一路 start_session（典型是 greeting 的 auto-start）已在飞。早期实现
            # 直接静默 return，但前端的 start_session 在 await 一个 session_started
            # ack——若它撞在这里被去重，ack 永远不来，前端 15s 后超时并卡死（用户
            # 在 greeting 出现前抢发消息触发的竞态：greeting 先把 in-flight 占住，
            # 而它完成时发的 ack 又早于前端开始 await，前端两头落空）。
            #
            # 仅对**同模式**的去重请求补发 ack：in-flight 启的是它自己的模式，
            # 跨模式（如 greeting 拉 text、另一路同刻请求 audio）若复用 in-flight 的
            # session_started(text)，前端会按 text 切 UI、收口 promise，而用户要的
            # audio 会话根本没起（CodeRabbit）。
            if (self._starting_input_mode or input_mode) == input_mode:
                logger.warning("⚠️ Session正在启动中，等 in-flight 启动落定后给本请求补发 session_started")
                # 等 in-flight 那次启动**自己落定**（_starting_session_count 归 0）。
                # 不拿 session_ready 当谓词：它可能还残留上一个 session 的 True
                # （in-flight start 要过几个 await 才把它重置），那样循环会被直接
                # 跳过、在 in-flight 还没真正起好时就误发 started 假阳性（Codex P1）。
                # 等待上限绑前端的 start_session 超时：超过它再补发 ack 已无意义
                # （前端早已 reject + end_session），故以它为窗口上界兼防挂安全阀。
                _waited = 0.0
                while self._starting_session_count > 0 and _waited < FRONTEND_START_SESSION_TIMEOUT_SECONDS:
                    await asyncio.sleep(0.05)
                    _waited += 0.05
                # 仅当 in-flight 真正落定（count 归 0、即循环是「落定退出」而非
                # 「超时退出」）且会话确实活跃时才补发 session_started（与
                # in-flight 自身发的那条幂等，前端 resolver 一次性）。若是超时退出
                # （count 仍 >0、in-flight 没结束），self.session/is_active 在 restart
                # 流程里可能是上一个 session 残留的 True，补发会是假阳性（Codex P1），
                # 故一律不发。也**不**发 session_failed——in-flight 可能仍在跑/或其
                # 失败路径已通知前端，过早发 failed 会被前端当终态打断本会成功的启动。
                if self._starting_session_count == 0 and self.session and self.is_active:
                    await self.send_session_started(input_mode)
            elif user_initiated and _allow_cross_mode_restart:
                # 跨模式撞车，且这是用户显式启动：典型是 proactive（主动搭话 /
                # greeting）自起的 text 会话还在飞，而用户此刻点了"开始语音对话"
                # （audio）。早期实现静默 return，但用户的 audio 请求是显式意图：
                # 静默丢弃会让前端干等 15s ack 超时，且超时时发的 end_session 还会把
                # 正在建立的 proactive text 会话一并撕掉（proactive 语音也播不出）。
                # 改为：等 in-flight 那次启动落定（_starting_session_count 归 0）后，
                # 递归重入起一个本模式的新会话——它会按 5588 行的旧 session 清理逻辑
                # 替换掉刚建好的旧模式会话。不复用 in-flight 的 ack（跨模式复用会按
                # 错模式切 UI，见上）。
                logger.warning("⚠️ Session正在启动中（跨模式），等 in-flight 落定后改起 %s 会话", input_mode)
                # 快照"用户放弃"计数：仅在前端/用户主动 end_session 时递增（见
                # end_session 顶部）。in-flight 真正落定时 count 由其自身 finally 归 0、
                # abandon epoch 不变；而前端 15s 超时发的 end_session 会把 count 清 0
                # 且 abandon epoch +1。只在「count 归 0 且 abandon epoch 未变」时重启——
                # 区分"真落定"与"用户已放弃 + 被 end_session 清零"，避免在 UI 已 reject
                # 后凭空起孤儿会话（Codex P2）。关键：不能用 _audio_stream_epoch——它在
                # in-flight 启动失败的 by_server cleanup 里也会涨，会把用户仍在等待的
                # audio 误判成放弃、回到 15s 干等（CodeRabbit）。
                _abandon_epoch = self._user_session_abandon_epoch
                _waited = 0.0
                while self._starting_session_count > 0 and _waited < CROSS_MODE_RESTART_WAIT_SECONDS:
                    await asyncio.sleep(0.05)
                    _waited += 0.05
                # 重启前的连接校验，两个条件都要满足：
                #   1) 本请求那把 ws（param）仍连接。in-flight 失败时
                #      _handle_session_start_exception→cleanup() 会把 self.websocket 清成
                #      None，但浏览器连接其实还开着——这种必须能重启，否则用户 audio 干等
                #      15s（Codex P2）。判据用 param ws 的连接态，不看 self.websocket：浏览器
                #      真刷新/断连时这把 param ws 会变 DISCONNECTED。
                #   2) self.websocket 仍是这把或已被清空（None）。若已被换成另一条新连接，
                #      说明发生了重连，别用旧 ws 重启去和新连接打架（Codex P2 stale ws）。
                try:
                    _param_ws_connected = (
                        websocket is not None
                        and hasattr(websocket, 'client_state')
                        and websocket.client_state == websocket.client_state.CONNECTED
                    )
                except Exception:  # noqa: BLE001
                    _param_ws_connected = False
                _self_ws_ok = self.websocket is websocket or self.websocket is None
                if self._starting_session_count != 0:
                    logger.warning("⚠️ 跨模式等待 in-flight 启动超时（%.1fs，留余量给重启），放弃改起 %s", _waited, input_mode)
                elif self._user_session_abandon_epoch != _abandon_epoch:
                    logger.warning("⚠️ 跨模式等待期间用户主动结束了启动（已放弃），不再改起 %s", input_mode)
                elif not (_param_ws_connected and _self_ws_ok):
                    logger.warning("⚠️ 跨模式等待期间 websocket 已断连/被新连接替换，不再改起 %s", input_mode)
                else:
                    # in-flight 干净落定且连接仍在：递归重入起目标模式。
                    # 先清熔断：用户显式请求按 websocket_router 语义本应清，但当时
                    # _starting_session_count>0 让它没清；若 in-flight 失败把熔断跳了闸，
                    # 递归会在熔断检查（5427）处静默 return、不发 session_failed → 前端干等
                    # 15s。这里替它清，让重启真正起来或走正常失败上报（Codex P2）。
                    # 重入禁用跨模式重启（_allow_cross_mode_restart=False）把递归深度封到 1，
                    # 二次并发撞车回落静默 return 而非无界递归（greptile P2）。guard 检查
                    # （5431）前无 await，count==0 的判定到重入是原子的。
                    self.reset_session_start_circuit()
                    await self.start_session(websocket, new, input_mode,
                                             user_initiated=True, _allow_cross_mode_restart=False)
            else:
                logger.warning("⚠️ Session正在启动中（跨模式重复请求），忽略")
            return

        # 标记正在启动（使用计数器，避免并发 start_session 的 finally 互相覆盖）
        self._starting_session_count += 1
        self._starting_input_mode = input_mode
        # 干净的播放门控：清掉上一会话可能残留的 playback flag / manager 队列
        # （前端中途断线/刷新导致 voice_play_end 丢失时尤为重要）。放在熔断早退
        # 与 "正在启动中" 去重早退 *之后*——那些早退不会真正起新 session，提前
        # reset 会误清掉仍在播放的旧会话门控（Codex P1）。这里已确定要起新会话。
        self._reset_proactive_gate()
        # 首次 start_session 起算，让 idle reset loop 永久存活
        self._ensure_idle_session_reset_loop()
        # rebase idle 计时基准：last_user_activity_time 是 manager 状态、跨 session 持久。
        # idle-reset 触发 end_session 后用户再开新 session 时，如不重置就会继承超过
        # 阈值的旧时间戳，下一轮 sweep 立刻把新 session 当成 30 min idle 再关一次。
        # 同步刷新 proactive 路径 10s 抑制窗口（prepare_proactive_delivery），避免
        # session 刚起来就被立刻触发主动搭话。
        self.last_user_activity_time = time.time()
        # CAS 落败早退标志：True 时禁止 finally 递减 guard，
        # 防止赢家初始化期间第三个协程穿过 guard 浪费 LLM 连接。
        _llm_concurrent_aborted = False
        _diag_start = time.time()
        # 预创建的 /new_dialog 任务：若 start_llm_session 之前就抛异常，
        # finally 会负责 cancel + await，避免孤儿 task 残留连接。
        _new_dialog_task = None

        try:
            # 回收残留的热切换资源，防止 main + pending + new-main 叠到 >2 个 session
            await self._cleanup_pending_session_resources()
            await self._reset_preparation_state(clear_main_cache=False)
        
            logger.info(f"[语音会话诊断] 开始 start_session: input_mode={input_mode}, new={new}")
            logger.info(f"启动新session: input_mode={input_mode}, new={new}")
            self.websocket = websocket
            self.input_mode = input_mode
            self._reset_voice_echo_suppression_cache()

            # 拉起活动 tracker 心跳，让进游戏/娱乐/工作的情境弹窗检测得到（详见
            # _maybe_kick_activity_loop_for_context_prompt）。fire-and-forget，不阻塞会话
            # 启动；仅在用户已显式开启 vision（隐私关）时才 kick，否则直接早退、零成本。
            self._fire_task(self._maybe_kick_activity_loop_for_context_prompt())
        
            # 立即通知前端系统正在准备（静默期开始）
            await self.send_session_preparing(input_mode)
        
            # 重新读取配置以支持热重载
            # core_api_type 从 realtime 配置获取，支持自定义 realtime API 时自动设为 'local'
            realtime_config = self._config_manager.get_model_api_config('realtime')
            # 合并两次同步 IO：core_config 一次 read 即可，avoid 双倍 json.load
            core_config_snapshot = await self._config_manager.aget_core_config()
            self.core_api_type = realtime_config.get('api_type', '') or core_config_snapshot.get('CORE_API_TYPE', '')
            self.audio_api_key = core_config_snapshot['AUDIO_API_KEY']

            # 每次启动会话前都清理一次无效 voice_id，避免角色配置残留旧音色导致启动异常
            try:
                cleaned_count, legacy_names = await asyncio.to_thread(self._config_manager.cleanup_invalid_voice_ids)
                if cleaned_count > 0:
                    logger.info(f"🧹 start_session 前已清理 {cleaned_count} 个无效 voice_id")
                self._enqueue_voice_migration_notice(legacy_names)
            except Exception as e:
                logger.warning(f"⚠️ start_session 清理无效 voice_id 失败，继续启动会话: {e}")

            # 重新读取角色配置以获取最新的voice_id（支持角色切换后的音色热更新）
            _, _, _, self.lanlan_basic_config, _, _, _, _, _ = await self._config_manager.aget_character_data()
            old_voice_id = self.voice_id
            self._apply_voice_id_for_route()

            # 如果角色没有设置 voice_id，尝试使用自定义API配置的 TTS_VOICE_ID 作为回退
            if not self.voice_id:
                # core_config 在单次 start_session 内不会变（改它走 save_core_api → end_session），复用顶部 snapshot
                tts_voice_id = core_config_snapshot.get('TTS_VOICE_ID', '')
                # 过滤掉 GPT-SoVITS 禁用时的占位符（格式: __gptsovits_disabled__|...）
                if (
                    tts_voice_id
                    and not is_gsv_disabled_voice_id(tts_voice_id)
                    and (
                        _as_bool(core_config_snapshot.get('ENABLE_CUSTOM_API'), False)
                        or core_config_snapshot.get('GPTSOVITS_ENABLED')
                    )
                ):
                    self.voice_id = tts_voice_id
                    logger.info(f"🔄 使用自定义TTS回退音色: '{self.voice_id}'")
                    self._is_free_preset_voice = False
        
            if old_voice_id != self.voice_id:
                logger.info(f"🔄 voice_id已更新: '{old_voice_id}' -> '{self.voice_id}'")
            if self._is_free_preset_voice:
                logger.info(f"🆓 当前使用免费预设音色: '{self.voice_id}'")
        
            # 日志输出模型配置（直接从配置读取，避免创建不必要的实例变量）
            _realtime_model = realtime_config.get('model', '')
            _conversation_model = self._config_manager.get_model_api_config('conversation').get('model', '')
            _vision_model = self._config_manager.get_model_api_config('vision').get('model', '')
            logger.info(f"📌 已重新加载配置: core_api={self.core_api_type}, realtime_model={_realtime_model}, text_model={_conversation_model}, vision_model={_vision_model}, voice_id={self.voice_id}")
            logger.info(f"[语音会话诊断] 配置加载完成 (耗时: {time.time() - _diag_start:.2f}秒)")
        
            # 重置 TTS 缓存状态。若 TTS worker 已经存活且此前确认 ready，
            # 这里只清空待播文本，不要把 ready 状态抹掉；存活 worker 不会
            # 因为新 text session 再发一次 __ready__，否则赛后一次性文本会
            # 永远停在 pending chunks 里。
            preserve_tts_ready = self._can_preserve_tts_ready_for_session_start()
            async with self.tts_cache_lock:
                self.tts_ready = preserve_tts_ready
                self.tts_pending_chunks.clear()
        
            # 重置输入缓存状态
            async with self.input_cache_lock:
                self.session_ready = False
                # 注意：不清空 pending_input_data，因为可能已有数据在缓存中
        
            self.use_tts = self._resolve_session_use_tts(
                input_mode,
                realtime_config,
                core_config_snapshot,
            )
        
            async with self.lock:
                if self.is_active:
                    logger.warning("检测到活跃的旧session，正在清理...")
                    # 释放锁后清理，避免死锁
        
            # 如果检测到旧 session，先清理
            if self.is_active:
                # reset_starting_count=False：保留自己递增的 guard，防止 end_session 里
                # 的 _starting_session_count=0 让并发第二次 start_session 穿过，产生孤儿 session。
                await self.end_session(by_server=True, reset_starting_count=False)
                # 等待一小段时间确保资源完全释放
                await asyncio.sleep(0.5)
                logger.info("旧session清理完成")
        
            # 如果当前不需要TTS但TTS线程仍在运行，发送停止信号
            if not self.use_tts and self.tts_thread and self.tts_thread.is_alive():
                logger.info("当前模式不需要TTS，关闭TTS线程")
                try:
                    self.tts_request_queue.put(("__shutdown__", None))  # 通知线程退出
                    await asyncio.to_thread(self.tts_thread.join, 1.0)  # 等待线程结束
                except Exception as e:
                    logger.error(f"关闭TTS线程时出错: {e}")
                finally:
                    self.tts_thread = None

            # 定义 TTS 启动协程（如果需要）
            async def start_tts_if_needed():
                """Asynchronously start the TTS process and wait for readiness"""
                if not self.use_tts:
                    return True

                # 启动TTS线程
                tts_ready = False
                if self.tts_thread is None or not self.tts_thread.is_alive():
                    self._start_tts_thread()

                    # 等待TTS进程发送就绪信号（最多等待12秒）
                    has_custom_tts = self._has_custom_tts()
                    tts_type = "free-preset-TTS" if self._is_free_preset_voice else ("custom-TTS" if has_custom_tts else f"{self.core_api_type}-default-TTS")
                    logger.info(f"🎤 TTS进程已启动，等待就绪... (使用: {tts_type})")
                    logger.info("[语音会话诊断] 开始等待 TTS 就绪信号 (超时: 12秒)")
                    start_time = time.time()
                    timeout = 12.0  # 最多等待12秒
                    _last_tts_log = 0.0
                    while time.time() - start_time < timeout:
                        # worker 线程已死亡则无需继续等待
                        if not self.tts_thread.is_alive():
                            # 抽干此刻队列：__ready__ 用于决定本次等待结果，
                            # 其他消息（如承载 NO_RETRY 错误码的 __error__）放回队列，
                            # 让稍后启动的 tts_response_handler 处理，避免错误码丢失。
                            _requeue: list = []
                            while True:
                                try:
                                    msg = self.tts_response_queue.get_nowait()
                                except Empty:
                                    break
                                if isinstance(msg, tuple) and len(msg) == 2 and msg[0] == "__ready__":
                                    tts_ready = msg[1]
                                else:
                                    _requeue.append(msg)
                            for _m in _requeue:
                                self.tts_response_queue.put(_m)
                            if not tts_ready:
                                logger.error("❌ TTS Worker 线程已退出，无法继续等待")
                            break
                        remaining = timeout - (time.time() - start_time)
                        # 单次阻塞窗口封顶 2 秒，保证 worker 死亡探测与诊断日志能及时触发
                        poll_window = min(remaining, 2.0)
                        if poll_window <= 0:
                            break
                        try:
                            msg = await asyncio.to_thread(
                                self.tts_response_queue.get, True, poll_window
                            )
                        except Empty:
                            # 每约2秒输出一次诊断日志，便于定位卡在哪一阶段
                            _elapsed = time.time() - start_time
                            if _elapsed - _last_tts_log >= 2.0:
                                _last_tts_log = _elapsed
                                logger.info(f"[语音会话诊断] TTS 就绪等待中... 已等待 {_elapsed:.1f}秒 / {timeout}秒")
                            continue
                        if isinstance(msg, tuple) and len(msg) == 2 and msg[0] == "__ready__":
                            tts_ready = msg[1]
                            if tts_ready:
                                logger.info(f"✅ TTS进程已就绪 (用时: {time.time() - start_time:.2f}秒)")
                            else:
                                logger.error("❌ TTS进程初始化失败")
                            break
                        else:
                            # 不是就绪信号，放回队列后退出（与旧行为一致）
                            self.tts_response_queue.put(msg)
                            break

                    if not tts_ready:
                        if time.time() - start_time >= timeout:
                            logger.warning(f"⚠️ TTS进程就绪信号超时 ({timeout}秒)，继续执行...")
                            logger.warning(f"[语音会话诊断] TTS 在 {timeout} 秒内未就绪，可能为 TTS 服务慢或网络问题")
                        else:
                            logger.error("❌ TTS进程初始化失败，但继续执行...")
                else:
                    # TTS线程已存活，复用现有线程；保留上次的就绪状态（避免失败的 worker 被误标为就绪）
                    tts_ready = self.tts_ready
                    logger.info(f"🎤 TTS线程已在运行，复用现有线程 (ready={tts_ready})")
            
                # 确保旧的 TTS handler task 已经停止
                if self.tts_handler_task and not self.tts_handler_task.done():
                    logger.info("🎧 Cancelling old tts_handler_task...")
                    self.tts_handler_task.cancel()
                    try:
                        await asyncio.wait_for(self.tts_handler_task, timeout=1.0)
                    except (asyncio.CancelledError, asyncio.TimeoutError):
                        pass
            
                # 启动新的 TTS handler task
                logger.info(f"🎧 Creating tts_handler_task (response_queue id={id(self.tts_response_queue):#x})")
                self.tts_handler_task = asyncio.create_task(self.tts_response_handler())
            
                # 仅在确认为就绪时才标记可发送，避免“假就绪”导致静默
                async with self.tts_cache_lock:
                    self.tts_ready = bool(tts_ready)

                # 处理在TTS启动期间可能已经缓存的文本chunk
                if tts_ready:
                    await self._flush_tts_pending_chunks()
                else:
                    logger.warning("⚠️ TTS未就绪，当前回复将继续缓存，等待后续就绪信号")
                return True

            # —— 提前发起 /new_dialog，避免被 TTS worker 线程的 dashscope
            # import 抢 GIL 拖慢。在 gather 之前就 create_task，让 httpx 先
            # 和 server 建好连接、收到响应；gather 时 start_llm_session 只
            # await 现成的结果即可。
            _dlg_lanlan = self.lanlan_name
            _dlg_port = self.memory_server_port

            async def _fetch_new_dialog():
                """Independent task: fetch the /new_dialog response. Kicked off before the gather,
                deliberately avoiding the GIL contention window during TTS worker startup."""
                from utils.internal_http_client import get_internal_http_client
                _mem_client = get_internal_http_client()
                try:
                    resp = await _mem_client.get(
                        f"http://127.0.0.1:{_dlg_port}/new_dialog/{_dlg_lanlan}",
                        timeout=5.0,
                    )
                except httpx.ConnectError:
                    raise ConnectionError(f"❌ 记忆服务未启动！请先启动记忆服务 (端口 {_dlg_port})")
                except httpx.TimeoutException:
                    raise ConnectionError(f"❌ 记忆服务响应超时！请检查记忆服务是否正常运行 (端口 {_dlg_port})")
                except Exception as e:
                    raise ConnectionError(f"❌ 记忆服务连接失败: {e} (端口 {_dlg_port})")
                if not resp.is_success:
                    raise ConnectionError(f"❌ 记忆服务返回非2xx状态 {resp.status_code}: {resp.text[:200]}")
                return resp.text

            logger.info(f"[语音会话诊断] 开始获取记忆上下文 (端口 {self.memory_server_port})")
            _mem_start = time.time()
            _new_dialog_task = asyncio.create_task(_fetch_new_dialog())
            llm_next_context_count_to_consume_after_start = 0

            # 定义 LLM Session 启动协程
            async def start_llm_session():
                """Asynchronously create and connect the LLM Session.

                Uses connect-then-assign: a local new_session is created and connected
                first.  Only after connect() succeeds is it promoted to self.session.
                On failure the half-initialised session is closed and an exception raised.
                """
                nonlocal llm_next_context_count_to_consume_after_start
                # 强 CAS 语义：只允许在 self.session 为 None（start_session 已清场）
                # 或已经是自己的 new_session 时赋值。任何其他状态都视为并发落败，
                # 必须关闭本次 new_session，避免覆盖赢家造成孤儿。
                #
                # 反例：若仅对比"入口快照"，当赢家已把 self.session 置为 B、
                # 落败者 A 早退后 guard 被 finally 放开，第三者 C 会把入口快照
                # 记作 B，随后 CAS 通过 B==B 的自反检查覆盖 B，产生新的孤儿。
                guard_max_length = self._get_text_guard_max_length()
                _lang = normalize_language_code(self.user_language, format='short')
                initial_prompt = await self._build_initial_prompt()
                next_session_context_messages = self._snapshot_next_session_context_messages()
                start_prompt_context_owner = object()
                self._mark_pending_context_appends_delivered_in_start_prompt(
                    next_session_context_messages,
                    owner=start_prompt_context_owner,
                )

                # 等待上面预先发出的 /new_dialog 完成
                try:
                    _nd_text = await _new_dialog_task
                    initial_prompt += (
                        _nd_text
                        + self._convert_cache_to_str(next_session_context_messages)
                        + _loc(CONTEXT_SUMMARY_READY, _lang).format(name=self.lanlan_name, master=self.master_name)
                    )
                    logger.info(f"[语音会话诊断] 记忆上下文获取完成 (耗时: {time.time() - _mem_start:.2f}秒)")
                except ConnectionError:
                    raise
                except Exception as e:
                    raise ConnectionError(f"❌ 记忆服务连接失败: {e} (端口 {self.memory_server_port})")
            
                logger.info(f"🤖 开始创建 LLM Session (input_mode={input_mode})")
                logger.info("[语音会话诊断] 开始创建 LLM 连接 (realtime/text)...")
                _llm_create_start = time.time()
            
                # Create into a LOCAL variable — not self.session yet
                new_session = None
                # 在抓快照前先把内置工具的 description 对齐到当前
                # user_language —— __init__ 时 user_language 可能还是 None
                # 走的英文占位，这里 user_language 已经定型了，重新注册
                # 一份覆盖 registry 里的旧描述，再被下面的 snapshot 读走。
                self._register_builtin_tools()
                # Snapshot the registry once per session create so the
                # tools list seen by the wire matches what the registry
                # held at connect time. ``set_tools`` keeps it live for
                # later mutations.
                _initial_tool_defs = self.tool_registry.all()
                if input_mode == 'text':
                    conversation_config = self._config_manager.get_model_api_config('conversation')
                    vision_config = self._config_manager.get_model_api_config('vision')
                    new_session = OmniOfflineClient(
                        base_url=conversation_config['base_url'],
                        api_key=conversation_config['api_key'],
                        model=conversation_config['model'],
                        vision_model=vision_config['model'],
                        vision_base_url=vision_config['base_url'],
                        vision_api_key=vision_config['api_key'],
                        provider_type=conversation_config.get('provider_type'),
                        vision_provider_type=vision_config.get('provider_type'),
                        on_text_delta=self.handle_text_data,
                        # on_thinking_active bound below via a session-scoped
                        # closure so only the LIVE session drives the bubble.
                        on_input_transcript=self.handle_text_input_transcript,
                        on_output_transcript=self.handle_output_transcript,
                        on_connection_error=self.handle_connection_error,
                        on_response_done=self.handle_response_complete,
                        on_repetition_detected=self.handle_repetition_detected,
                        on_response_discarded=self.handle_response_discarded,
                        on_status_message=self.send_status,
                        max_response_length=guard_max_length,
                        lanlan_name=self.lanlan_name,
                        master_name=self.master_name,
                        on_tool_call=self._on_tool_call,
                        tool_definitions=_initial_tool_defs,
                        # 长回复 summary 必须有"真的会发声的 TTS"才有意义：summary
                        # 文本是 `tts_enabled=True, ui_enabled=False` 注入的，若 TTS
                        # 实际不发声它会被 handle_text_data 静默丢掉，但 history 仍被
                        # 重写成 prefix+summary —— 静音会话会"live 看到全文、reload 看
                        # 不到尾巴"，是隐性内容丢失。注意 `_resolve_session_use_tts` 对
                        # text mode 永远返回 True；真正的"发声"还要 DISABLE_TTS=False，
                        # 否则 tts_worker 会被换成 dummy_tts_worker。
                        enable_long_response_summary=(
                            self.use_tts
                            and not core_config_snapshot.get('DISABLE_TTS', False)
                        ),
                    )
                    new_session.on_proactive_done = self.handle_proactive_complete
                    new_session.on_thinking_active = self._make_thinking_active_callback(new_session)
                else:
                    realtime_config = self._config_manager.get_model_api_config('realtime')
                    new_session = OmniRealtimeClient(
                        base_url=realtime_config.get('base_url', ''),
                        api_key=realtime_config['api_key'],
                        model=realtime_config['model'],
                        voice=self._resolve_realtime_voice(realtime_config),
                        on_text_delta=self.handle_text_data,
                        on_audio_delta=self.handle_audio_data,
                        on_new_message=self.handle_new_message,
                        on_sid_rotate=self.rotate_speech_id_for_response_done,
                        on_input_transcript=self.handle_input_transcript,
                        on_output_transcript=self.handle_output_transcript,
                        on_connection_error=self.handle_connection_error,
                        on_response_done=self.handle_response_complete,
                        on_silence_timeout=self.handle_silence_timeout,
                        on_status_message=self.send_status,
                        on_repetition_detected=self.handle_repetition_detected,
                        api_type=self.core_api_type,
                        on_tool_call=self._on_tool_call,
                        tool_definitions=_initial_tool_defs,
                        livestream_mode=self._is_livestream_active(),
                    )
                    # Apply user's noise reduction preference to the AudioProcessor
                    nr_enabled = (await aload_global_conversation_settings()).get('noiseReductionEnabled', True)
                    if hasattr(new_session, '_audio_processor') and new_session._audio_processor:
                        new_session._audio_processor.set_enabled(nr_enabled)

                # Bind guarded callbacks BEFORE connect — connect() can invoke
                # on_connection_error during the handshake, and without the guard
                # it would run the raw unbound handler and potentially kill the
                # current active session.
                self._bind_session_lifecycle_callbacks(new_session)

                try:
                    await new_session.connect(initial_prompt, native_audio=not self.use_tts)
                except Exception:
                    try:
                        await new_session.close()
                    except Exception:
                        pass
                    raise

                # 强 CAS 提升：仅在 self.session 为 None（已被 end_session 清场）
                # 或已经是自己时才赋值，确保不会覆盖任何已就位的赢家 session。
                concurrent_winner = False
                async with self.lock:
                    if self.session is None or self.session is new_session:
                        self.session = new_session
                        if not self.current_speech_id:
                            self.current_speech_id = str(uuid4())
                        llm_next_context_count_to_consume_after_start = len(next_session_context_messages)
                    else:
                        concurrent_winner = True

                if concurrent_winner:
                    self._clear_pending_context_start_prompt_marks(owner=start_prompt_context_owner)
                    logger.warning("⚠️ start_llm_session: 检测到并发 start_session 已抢先建立 session，关闭本次 new_session 避免孤儿泄漏")
                    try:
                        await new_session.close()
                    except Exception as _close_err:
                        logger.error(f"💥 关闭并发落败的 new_session 失败: {_close_err}")
                    # 返回哨兵（而非 raise）以绕开 start_session 的通用 except：后者会调
                    # cleanup()（无 expected_session 守卫），反过来拆掉赢家的 session/ws，
                    # 还会 +1 session_start_failure_count 并向前端发 SESSION_START_FAILED。
                    return _START_LLM_CONCURRENT_ABORTED

                # 关 race 的最后一道闸：构造时拍了一次 registry 快照塞进 client，
                # 但 connect() 期间若有 register_tool / unregister_tool 发生，前面
                # 那次异步 _sync_tools_to_active_session 可能找不到 self.session
                # （它当时还是 None / 旧 session）。这里 self.session 已就位，
                # 重新 sync 一次，让 wire 上的 tools 与 registry 保持最终一致。
                try:
                    await self._sync_tools_to_active_session()
                except Exception as _sync_err:
                    logger.warning("⚠️ start_llm_session: post-connect tool sync failed: %s", _sync_err)

                logger.info("✅ LLM Session 已连接")
                logger.info(f"[语音会话诊断] LLM 连接并 connect 完成 (耗时: {time.time() - _llm_create_start:.2f}秒)")
                print(initial_prompt)  #只在控制台显示，不输出到日志文件
                return True
        
            # 重置状态
            if new:
                self.message_cache_for_new_session = []
                self.next_session_context_messages = []
                self.last_time = None
                self.is_preparing_new_session = False
                self.summary_triggered_time = None
                self.initial_cache_snapshot_len = 0
                self.initial_next_session_context_snapshot_len = 0
                # 清空输入缓存（新对话时不需要保留旧的输入）
                async with self.input_cache_lock:
                    self.pending_input_data.clear()
                    self._clear_pending_context_appends(release_durable_cached=True)

            # 并行启动 TTS 和 LLM Session
            logger.info("🚀 并行启动 TTS 和 LLM Session...")
            start_parallel_time = time.time()
            
            tts_result, llm_result = await asyncio.gather(
                start_tts_if_needed(),
                start_llm_session(),
                return_exceptions=True
            )
            
            logger.info(f"⚡ 并行启动完成 (总用时: {time.time() - start_parallel_time:.2f}秒)")
            tts_status = '异常' if isinstance(tts_result, Exception) else ('跳过(原生语音)' if not self.use_tts else 'OK')
            logger.info(f"[语音会话诊断] 并行启动结果: TTS={tts_status}, LLM={'异常' if isinstance(llm_result, Exception) else 'OK'}")
            # 检查是否有错误
            if isinstance(tts_result, Exception):
                logger.error(f"TTS 启动失败: {tts_result}")
            # 并发落败分支：赢家已持有 self.session / message_handler_task，
            # 我们不能继续走 "if self.session" 分支（会覆盖 handler task、重复
            # send_session_started），也不能 raise（会误触发 cleanup 杀掉赢家）。
            # 同时设置 _llm_concurrent_aborted=True 让 finally 跳过 guard 递减：
            # 赢家尚未完成初始化，必须保持 guard 以阻止第三个协程穿过。
            if llm_result is _START_LLM_CONCURRENT_ABORTED:
                logger.info("[语音会话诊断] start_session 因并发 CAS 落败早退，保持 guard 关闭")
                _llm_concurrent_aborted = True
                return
            if isinstance(llm_result, Exception):
                raise llm_result  # LLM Session 失败是致命的
            
            # 标记 session 激活
            if self.session:
                async with self.lock:
                    self.is_active = True

                # Activity tracker：voice_engaged state 的硬前置就是 voice mode flag。
                # 文本模式置 False 让 voice_engaged 永不触发；语音模式打开后由
                # handle_input_transcript 的 on_voice_rms() 维持 8s 活跃窗口。
                self._activity_tracker.on_voice_mode(input_mode == 'audio')

                self.session_start_time = datetime.now()
                self._session_turn_count = 0

                # 启动消息处理任务
                self.message_handler_task = asyncio.create_task(self.session.handle_messages())
                
                # 启动成功，重置失败计数器和熔断
                self.session_start_failure_count = 0
                self.session_start_last_failure_time = None
                self._memory_error_retry_after = 0
                self._session_start_circuit_open = False
                if self.is_goodbye_silent():
                    self.set_goodbye_silent(False)

                logger.info(f"[语音会话诊断] 即将通知前端 session_started (start_session 总耗时: {time.time() - _diag_start:.2f}秒)")
                # 通知前端 session 已成功启动
                await self.send_session_started(input_mode)

                # 在 queued context 写入 session 前保持输入闸门关闭；否则第一条
                # 缓存/并发用户输入可能抢在上下文前面进入模型。
                async with self.input_cache_lock:
                    await self._drain_pending_context_appends_before_ready()
                    self.session_ready = True

                # 处理在session启动期间可能已经缓存的输入数据
                await self._flush_pending_input_data()
                self._consume_next_session_context_messages(
                    llm_next_context_count_to_consume_after_start
                )
                llm_next_context_count_to_consume_after_start = 0

                # WebSocket 重连后，投递因断线积压的 agent 任务回调
                if self.pending_agent_callbacks:
                    self._fire_task(self.trigger_agent_callbacks())

            else:
                raise Exception("Session not initialized")
        
        except Exception as e:
            # prelude（_cleanup_pending_session_resources / end_session / asyncio.sleep 等）
            # 与 gather 块的错误统一走这里收口：send_session_failed + cleanup，避免前端卡在 preparing。
            # 注意：except Exception 不会捕获 CancelledError，shutdown 路径保持原语义。
            await self._handle_session_start_exception(e, input_mode, _diag_start)
        finally:
            # 例外：CAS 落败早退时不递减——赢家还在初始化，若此时放开 guard，
            # 第三个协程会穿过并再次把入口快照当作"赢家"进而覆盖掉真正的赢家。
            # 赢家完成（成功或异常）后会通过自己的 finally 或 cleanup 清理 guard。
            if not _llm_concurrent_aborted:
                self._starting_session_count = max(0, self._starting_session_count - 1)
                if self._starting_session_count == 0:
                    self._starting_input_mode = None
            # 保险：若 /new_dialog 预取任务早期异常后仍在跑（gather 没来得及
            # await 它就异常退出），这里统一 cancel + await，避免 "Task exception
            # was never retrieved" warning 和连接池泄漏。
            if _new_dialog_task is not None and not _new_dialog_task.done():
                _new_dialog_task.cancel()
                try:
                    await _new_dialog_task
                except (asyncio.CancelledError, Exception):
                    pass

    async def send_user_activity(self, interrupted_speech_id: Optional[str] = None):
        """Send the user-activity signal, attaching the interrupted speech_id for precise interruption control"""
        try:
            if self.websocket and hasattr(self.websocket, 'client_state') and self.websocket.client_state == self.websocket.client_state.CONNECTED:
                if interrupted_speech_id is None:
                    interrupted_speech_id = self.current_speech_id
                message = {
                    "type": "user_activity",
                    "interrupted_speech_id": interrupted_speech_id  # 告诉前端应丢弃哪个 speech_id
                }
                await self.websocket.send_json(message)
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.error(f"💥 WS Send User Activity Error: {e}")

    def _convert_cache_to_str(self, cache):
        """[Hot-swap related] Convert the cache to a string"""
        res = ""
        for i in cache:
            res += f"{i['role']} | {i['text']}\n"
        return res

    async def _build_initial_prompt(self) -> str:
        """Build the system prompt and inject active task summary when agent is enabled."""
        _lang = normalize_language_code(self.user_language, format='short')
        if self._is_agent_enabled():
            # Keep the current wrapper structure but revert prompt semantics:
            # do not distinguish browser/computer/plugin in the initial capability text.
            # Historical dynamic capability block kept for rollback:
            # capability_parts = []
            # if self.agent_flags.get('computer_use_enabled'):
            #     capability_parts.append(_loc(AGENT_CAPABILITY_COMPUTER_USE, _lang))
            # if self.agent_flags.get('browser_use_enabled'):
            #     capability_parts.append(_loc(AGENT_CAPABILITY_BROWSER_USE, _lang))
            # if self.agent_flags.get('user_plugin_enabled'):
            #     capability_parts.append(_loc(AGENT_CAPABILITY_USER_PLUGIN_USE, _lang))
            # caps_text = (
            #     _loc(AGENT_CAPABILITY_SEPARATOR, _lang).join(capability_parts)
            #     if capability_parts else _loc(AGENT_CAPABILITY_GENERIC, _lang)
            # )
            # prompt = _loc(SESSION_INIT_PROMPT_AGENT_DYNAMIC, _lang).format(
            #     name=self.lanlan_name,
            #     capabilities=caps_text,
            # ) + self.lanlan_prompt
            prompt = _loc(SESSION_INIT_PROMPT_AGENT, _lang).format(name=self.lanlan_name) + self.lanlan_prompt
        else:
            prompt = _loc(SESSION_INIT_PROMPT, _lang).format(name=self.lanlan_name) + self.lanlan_prompt
        if self._is_agent_enabled():
            # Plugin summary (with plugin ids) is intentionally disabled to avoid
            # exposing implementation identifiers in the general agent prompt.
            # Keep method call removed here for deterministic prompt content.
            # Historical prompt merge kept for rollback:
            # plugin_prompt, active_tasks_prompt = await asyncio.gather(
            #     self._fetch_plugin_summary_prompt(),
            #     self._fetch_active_agent_tasks_prompt(),
            # )
            # prompt += plugin_prompt
            active_tasks_prompt = await self._fetch_active_agent_tasks_prompt()
            prompt += active_tasks_prompt

        # 记录 / 查询 key：lanlan_name 为空时落到 "default" 与 sink 端对齐
        # （sink 在 lanlan 字段空 / "default" 时把 directive 写到 "default"
        # bucket；这里读取也得用同一 key，否则用户的 ban-topic 永远进不来
        # system prompt，codex P2）。
        _directives_key = self.lanlan_name or "default"

        # ── 用户显式 ban-topic 注入 ─────────────────────────────────
        # 用户在过去 3 天里说过的 "别再提 X / stop saying X" 类指令，本轮 LLM
        # 在 context 里已经看过；下一次 session 重启时原话已被 compress_history
        # 抹掉，需要把活跃 term 拼成 system prompt 一段重新提醒模型避开。
        # 抽取与落盘走 ``memory.user_directives`` 的 user_utterance sink；
        # 这里只读。空时 render_prompt_block 返回 ""，对 prompt 长度无影响。
        try:
            from memory.user_directives import get_user_directives_manager
            prompt += get_user_directives_manager().render_prompt_block(
                _directives_key, _lang,
            )
        except Exception as _exc:  # pragma: no cover - defensive
            logger.debug(
                "[UserDirectives] prompt injection skipped: %s", _exc,
            )

        # ── 防复读 soft hint 注入 ──────────────────────────────────
        # 把最近高 BM25 rank 的 topic 词列出来，提示模型"已经聊过这些"。这是
        # 对**所有路径**生效的软约束（与 user ban list 不同：那个是用户明确
        # 说过别提，必须强约束）。proactive 还会在 system_router Phase 2 出口
        # 被 BM25 总分阈值二次拦截（regen / drop），常规 reply 只靠这段 prompt
        # 软约束。空 corpus / 新角色第一轮 → render 返回 ""，无副作用。
        try:
            from memory.anti_repeat import get_anti_repeat_corpus
            from config.prompts.prompts_directives import render_recent_topics_block
            topics = get_anti_repeat_corpus().top_recent_topics(_directives_key)
            prompt += render_recent_topics_block(topics, _lang)
        except Exception as _exc:  # pragma: no cover - defensive
            logger.debug(
                "[AntiRepeat] soft hint injection skipped: %s", _exc,
            )

        return prompt

    def _is_agent_enabled(self):
        try:
            gate_ok, _ = self._config_manager.is_agent_api_ready()
        except Exception:
            gate_ok = False
        return gate_ok and self.agent_flags['agent_enabled'] and (
            self.agent_flags['computer_use_enabled']
            or self.agent_flags.get('browser_use_enabled', False)
            or self.agent_flags.get('user_plugin_enabled', False)
            or self.agent_flags.get('openclaw_enabled', False)
            or self.agent_flags.get('openfang_enabled', False)
        )

    async def _fetch_plugin_summary_prompt(self) -> str:
        """Plugin prompt segment is intentionally disabled for chat prompt minimalism."""
        # This hook is kept for compatibility with older call sites.
        # Disabled by product decision: do not include plugin IDs in agent prompt.
        # Historical implementation kept for rollback:
        # if not (self._is_agent_enabled() and self.agent_flags.get('user_plugin_enabled')):
        #     return ""
        # _lang = normalize_language_code(self.user_language, format='short')
        # header = _loc(AGENT_PLUGINS_HEADER, _lang)
        # count_tmpl = _loc(AGENT_PLUGINS_COUNT, _lang)
        # try:
        #     async with httpx.AsyncClient(timeout=httpx.Timeout(2.0, connect=1.0), proxy=None, trust_env=False) as client:
        #         r = await client.get(f"http://127.0.0.1:{USER_PLUGIN_SERVER_PORT}/plugins")
        #         if r.status_code != 200:
        #             return ""
        #         data = r.json()
        #         plugins = data.get("plugins", []) if isinstance(data, dict) else []
        #         if not plugins:
        #             return ""
        #         if len(plugins) <= 5:
        #             lines = []
        #             for p in plugins:
        #                 if not isinstance(p, dict):
        #                     continue
        #                 pid = p.get("id", "")
        #                 if pid:
        #                     lines.append(f"  - {pid}")
        #             if lines:
        #                 return header + "\n".join(lines) + "\n"
        #         else:
        #             return count_tmpl.format(count=len(plugins))
        # except Exception as e:
        #     logger.debug(f"获取插件摘要失败，已忽略: {e}")
        return ""

    async def _fetch_active_agent_tasks_prompt(self) -> str:
        """Query agent server for active tasks and return a prompt snippet."""
        if not self._is_agent_enabled():
            return ""
        # 复用 internal_http_client 单例：agent mode session init 走此路径，
        # TOOL_SERVER_PORT 也是 127.0.0.1 内部服务
        try:
            from utils.internal_http_client import get_internal_http_client
            client = get_internal_http_client()
            resp = await client.get(
                f"http://127.0.0.1:{TOOL_SERVER_PORT}/tasks", timeout=1.5,
            )
            if resp.status_code != 200:
                return ""
            data = resp.json()
            tasks = data.get("tasks", [])
            active = [t for t in tasks if t.get("status") in ("running", "queued")]
            if not active:
                return ""
            _lang = normalize_language_code(self.user_language, format='short')
            lines = []
            for t in active:
                params = t.get("params") or {}
                desc = params.get("query") or params.get("instruction") or t.get("original_query") or t.get("id", "")[:8]
                status = _loc(AGENT_TASK_STATUS_RUNNING, _lang) if t.get("status") == "running" else _loc(AGENT_TASK_STATUS_QUEUED, _lang)
                lines.append(f"  - [{status}] {desc}")
            if len(lines) > 0:
                return (
                    _loc(AGENT_TASKS_HEADER, _lang)
                    + "\n".join(lines)
                    + _loc(AGENT_TASKS_NOTICE, _lang)
                )
            else:
                return ""
        except Exception:
            return ""

    async def _background_prepare_pending_session(self):
        """[Hot-swap related] Prewarm the pending session in the background"""

        # 确保旧的 pending session 已释放，防止泄漏到第 3 个实例
        if self.pending_session:
            logger.info("🧹 BG Prep: 清理残留的 pending session 后再创建新的")
            await self._cleanup_pending_session_resources()

        # 2. Create PENDING session components (as before, store in self.pending_connector, self.pending_session)
        try:
            # 重新读取配置以支持热重载
            # core_api_type 从 realtime 配置获取，支持自定义 realtime API 时自动设为 'local'
            realtime_config = self._config_manager.get_model_api_config('realtime')
            # 合并两次同步 IO：core_config 一次 read 即可
            core_config_snapshot = await self._config_manager.aget_core_config()
            self.core_api_type = realtime_config.get('api_type', '') or core_config_snapshot.get('CORE_API_TYPE', '')
            self.audio_api_key = core_config_snapshot['AUDIO_API_KEY']

            # 热切换准备时同样清理无效 voice_id，防止旧版本 voice 残留进入热切换流程
            try:
                cleaned_count, legacy_names = await asyncio.to_thread(self._config_manager.cleanup_invalid_voice_ids)
                if cleaned_count > 0:
                    logger.info(f"🧹 热切换准备: 已清理 {cleaned_count} 个无效 voice_id")
                self._enqueue_voice_migration_notice(legacy_names)
            except Exception as e:
                logger.warning(f"⚠️ 热切换准备: 清理无效 voice_id 失败，继续准备会话: {e}")

            # 重新读取角色配置以获取最新的voice_id（支持角色切换后的音色热更新）
            _, _, _, self.lanlan_basic_config, _, _, _, _, _ = await self._config_manager.aget_character_data()
            old_voice_id = self.voice_id
            self._apply_voice_id_for_route()

            # 如果角色没有设置 voice_id，尝试使用自定义API配置的 TTS_VOICE_ID 作为回退
            if not self.voice_id:
                # 复用本次热切换准备顶部的 snapshot（save_core_api 会 end_session 才能改 core_config）
                tts_voice_id = core_config_snapshot.get('TTS_VOICE_ID', '')
                # 过滤掉 GPT-SoVITS 禁用时的占位符（格式: __gptsovits_disabled__|...）
                if (
                    tts_voice_id
                    and not is_gsv_disabled_voice_id(tts_voice_id)
                    and (
                        _as_bool(core_config_snapshot.get('ENABLE_CUSTOM_API'), False)
                        or core_config_snapshot.get('GPTSOVITS_ENABLED')
                    )
                ):
                    self.voice_id = tts_voice_id
                    logger.info(f"🔄 热切换准备: 使用自定义TTS回退音色: '{self.voice_id}'")
                    self._is_free_preset_voice = False
            
            if old_voice_id != self.voice_id:
                logger.info(f"🔄 热切换准备: voice_id已更新: '{old_voice_id}' -> '{self.voice_id}'")

            self.pending_use_tts = self._resolve_session_use_tts(
                self.input_mode,
                realtime_config,
                core_config_snapshot,
                log_prefix="热切换准备: ",
            )
            
            # 根据input_mode创建对应类型的pending session
            # 复用 main session 的 ToolRegistry 状态（registry 是 manager 级，
            # 跨 session 持久），保证热切换前后工具集合保持一致。
            # 热切换可能跨语言（用户切了 user_language 后再热切换猫娘），
            # 抓快照前 refresh 一下内置工具的 description。
            self._register_builtin_tools()
            _pending_tool_defs = self.tool_registry.all()
            if self.input_mode == 'text':
                # 文本模式：使用 OmniOfflineClient
                conversation_config = self._config_manager.get_model_api_config('conversation')
                vision_config = self._config_manager.get_model_api_config('vision')
                guard_max_length = self._get_text_guard_max_length()
                self.pending_session = OmniOfflineClient(
                    base_url=conversation_config['base_url'],
                    api_key=conversation_config['api_key'],
                    model=conversation_config['model'],
                    vision_model=vision_config['model'],
                    vision_base_url=vision_config['base_url'],
                    vision_api_key=vision_config['api_key'],
                    on_text_delta=self.handle_text_data,
                    # on_thinking_active bound below via a session-scoped closure:
                    # the pending session must NOT light the current window's
                    # bubble while it warms up / before the hot-swap promotes it.
                    on_input_transcript=self.handle_text_input_transcript,
                    on_output_transcript=self.handle_output_transcript,
                    on_connection_error=self.handle_connection_error,
                    on_response_done=self.handle_response_complete,
                    on_repetition_detected=self.handle_repetition_detected,
                    on_response_discarded=self.handle_response_discarded,
                    on_status_message=self.send_status,
                    max_response_length=guard_max_length,
                    lanlan_name=self.lanlan_name,
                    master_name=self.master_name,
                    on_tool_call=self._on_tool_call,
                    tool_definitions=_pending_tool_defs,
                    # 与上方对偶：长回复 summary 必须有"真的会发声的 TTS"才有意义
                    # （理由见 main session 构造点的注释）。pending_use_tts 是热切换
                    # 准备时已 resolve 的下一轮 use_tts；DISABLE_TTS 仍需独立检查
                    # 因为它会把 worker 换成 dummy_tts_worker。
                    enable_long_response_summary=(
                        self.pending_use_tts
                        and not core_config_snapshot.get('DISABLE_TTS', False)
                    ),
                )
                self.pending_session.on_proactive_done = self.handle_proactive_complete
                self.pending_session.on_thinking_active = self._make_thinking_active_callback(self.pending_session)
                logger.info("🔄 热切换准备: 创建文本模式 OmniOfflineClient")
            else:
                # 语音模式：使用 OmniRealtimeClient
                realtime_config = self._config_manager.get_model_api_config('realtime')
                self.pending_session = OmniRealtimeClient(
                    base_url=realtime_config.get('base_url', ''),
                    api_key=realtime_config['api_key'],
                    model=realtime_config['model'],
                    voice=self._resolve_realtime_voice(realtime_config),
                    on_text_delta=self.handle_text_data,
                    on_audio_delta=self.handle_audio_data,
                    on_new_message=self.handle_new_message,
                    on_sid_rotate=self.rotate_speech_id_for_response_done,
                    on_input_transcript=self.handle_input_transcript,
                    on_output_transcript=self.handle_output_transcript,
                    on_connection_error=self.handle_connection_error,
                    on_response_done=self.handle_response_complete,
                    on_silence_timeout=self.handle_silence_timeout,
                    on_status_message=self.send_status,
                    on_repetition_detected=self.handle_repetition_detected,
                    api_type=self.core_api_type,
                    on_tool_call=self._on_tool_call,
                    tool_definitions=_pending_tool_defs,
                    livestream_mode=self._is_livestream_active(),
                )
                # Apply user's noise reduction preference to the AudioProcessor
                nr_enabled = (await aload_global_conversation_settings()).get('noiseReductionEnabled', True)
                if hasattr(self.pending_session, '_audio_processor') and self.pending_session._audio_processor:
                    self.pending_session._audio_processor.set_enabled(nr_enabled)
                logger.info("🔄 热切换准备: 创建语音模式 OmniRealtimeClient")
            
            initial_prompt = await self._build_initial_prompt()
            next_session_context_messages = list(getattr(self, "next_session_context_messages", []) or [])
            self.initial_next_session_context_snapshot_len = len(next_session_context_messages)
            self.initial_cache_snapshot_len = len(self.message_cache_for_new_session)
            from utils.internal_http_client import get_internal_http_client
            _hs_client = get_internal_http_client()
            try:
                resp = await _hs_client.get(
                    f"http://127.0.0.1:{self.memory_server_port}/new_dialog/{self.lanlan_name}",
                    timeout=5.0,
                )
            except httpx.ConnectError:
                raise ConnectionError(f"❌ 记忆服务未启动！请先启动记忆服务 (端口 {self.memory_server_port})")
            except httpx.TimeoutException:
                raise ConnectionError(f"❌ 记忆服务响应超时！请检查记忆服务是否正常运行 (端口 {self.memory_server_port})")
            if not resp.is_success:
                raise ConnectionError(f"❌ 记忆服务热切换时返回非2xx状态 {resp.status_code}: {resp.text[:200]}")
            initial_prompt += (
                resp.text
                + self._convert_cache_to_str(next_session_context_messages)
                + self._convert_cache_to_str(self.message_cache_for_new_session)
            )
            print(initial_prompt)
            self._bind_session_lifecycle_callbacks(self.pending_session)
            await self.pending_session.connect(initial_prompt, native_audio=not self.pending_use_tts)

            # 同主 session 路径：热切换的 pending_session 也要在 connect 后
            # 补一次 sync，覆盖 connect 期间发生的 register/unregister race。
            try:
                await self._sync_tools_to_active_session()
            except Exception as _sync_err:
                logger.warning("⚠️ pending_session post-connect tool sync failed: %s", _sync_err)

            if self.pending_session_warmed_up_event:
                self.pending_session_warmed_up_event.set()

        except asyncio.CancelledError:
            logger.error("💥 BG Prep Stage 1: Task cancelled.")
            await self._cleanup_pending_session_resources()
            # Do not set warmed_up_event here if cancelled.
        except Exception as e:
            # 记录HTTP详细错误信息（如503等）
            error_detail = str(e)
            if hasattr(e, 'status_code'):
                error_detail = f"HTTP {e.status_code}: {e}"
            if hasattr(e, 'body'):
                error_detail += f" | Body: {e.body}"
            logger.error(f"💥 BG Prep Stage 1: Error: {error_detail}", exc_info=True)
            await self._cleanup_pending_session_resources()
            # Do not set warmed_up_event on error.
        finally:
            # Ensure this task variable is cleared so it's known to be done
            if self.background_preparation_task and self.background_preparation_task.done():
                self.background_preparation_task = None

    async def _trigger_immediate_preparation_for_extra(self):
        """When extra prompts need injecting and preparation hasn't started yet, start preparing immediately and schedule the renew logic."""
        try:
            if not self.is_preparing_new_session:
                logger.info("Extra Reply: Triggering preparation due to pending extra reply.")
                self.is_preparing_new_session = True
                self.summary_triggered_time = datetime.now()
                self.message_cache_for_new_session = []
                self.initial_cache_snapshot_len = 0
                self.initial_next_session_context_snapshot_len = 0
                # 立即启动后台预热，不等待10秒
                self.pending_session_warmed_up_event = asyncio.Event()
                if not self.background_preparation_task or self.background_preparation_task.done():
                    self.background_preparation_task = asyncio.create_task(self._background_prepare_pending_session())
        except Exception as e:
            logger.error(f"💥 Extra Reply: preparation trigger error: {e}")

    # 供主服务调用，更新Agent模式相关开关
    def update_agent_flags(self, flags: dict):
        try:
            for k in [
                'agent_enabled',
                'computer_use_enabled',
                'browser_use_enabled',
                'user_plugin_enabled',
                'openclaw_enabled',
                'openclaw_ready',
                'openfang_enabled',
            ]:
                if k in flags and isinstance(flags[k], bool):
                    self.agent_flags[k] = flags[k]
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Voice-chat proactive audio nudge (dedicated path)
    # ------------------------------------------------------------------

    async def trigger_voice_proactive_nudge(self) -> bool:
        """Inject a pre-recorded audio prompt to nudge the voice model into speaking.

        This is the **only** caller of ``OmniRealtimeClient.prompt_ephemeral``
        for the voice-chat proactive feature.  It is completely independent of
        ``trigger_agent_callbacks`` (which handles agent task results).

        Returns True if the audio was fully injected, False if skipped.
        """
        if not self.is_active or not isinstance(self.session, OmniRealtimeClient):
            return False
        if self.is_goodbye_silent():
            logger.info("[%s] voice proactive nudge skipped: goodbye silent", self.lanlan_name)
            return False
        if self._takeover_active:
            logger.info("[%s] voice proactive nudge skipped: session takeover active", self.lanlan_name)
            return False
        if self.is_hot_swap_imminent:
            logger.info("[%s] voice proactive nudge skipped: hot-swap imminent", self.lanlan_name)
            return False
        _lang = normalize_language_code(self.user_language, format='short') or 'en'
        delivered = await self.session.prompt_ephemeral(language=_lang)
        if delivered:
            logger.info("[%s] voice proactive nudge delivered (%s)", self.lanlan_name, _lang)
        else:
            logger.info("[%s] voice proactive nudge skipped (guard)", self.lanlan_name)
        return delivered

    # ------------------------------------------------------------------
    # Proactive streaming helpers (Phase 2 流式 TTS + 完整文本投递)
    # ------------------------------------------------------------------

    async def request_fresh_screenshot(self, timeout: float = 3.0) -> str:
        """Request the latest screenshot from the frontend over WebSocket, falling back to backend pyautogui on failure.

        Both paths normalize the screenshot down to 720p/JPEG-80 and return the
        normalized base64 (without prefix), so a native-resolution frontend image
        never goes straight to the vision LLM and trips the proxy's 413.
        """
        # 策略1: 前端 WebSocket 截图
        if self.websocket:
            try:
                loop = asyncio.get_running_loop()
                self._screenshot_future = loop.create_future()
                await self.websocket.send_json({"type": "request_screenshot"})
                b64 = await asyncio.wait_for(self._screenshot_future, timeout=timeout)
                if b64:
                    # 前端有的截图路径（如 Electron 主进程直捕 captureSourceAsDataUrl）
                    # 返回原生分辨率，未走 720p 缩放，base64 可达 ~1.4MB，直接发给
                    # vision LLM 会被代理 nginx 以 413 Request Entity Too Large 拒掉。
                    # 这里和下方 pyautogui 兜底分支对称，统一压到 720p/JPEG-80 再返回
                    # （avatar 注解会在其上二次编码，不影响）。压缩失败则退回原图。
                    try:
                        from utils.screenshot_utils import (
                            decode_and_compress_screenshot_b64,
                            COMPRESS_TARGET_HEIGHT, COMPRESS_JPEG_QUALITY,
                        )
                        b64 = await asyncio.to_thread(
                            decode_and_compress_screenshot_b64,
                            b64, COMPRESS_TARGET_HEIGHT, COMPRESS_JPEG_QUALITY,
                        )
                    except Exception as comp_err:
                        logger.warning(
                            "[%s] request_fresh_screenshot WS compress failed, using raw: %s",
                            self.lanlan_name, comp_err,
                        )
                    return b64
            except (asyncio.TimeoutError, Exception) as e:
                logger.warning("[%s] request_fresh_screenshot WS failed: %s", self.lanlan_name, e)
            finally:
                self._screenshot_future = None

        # 策略2: 后端 pyautogui 兜底（仅限本机连接，远程服务器截图无意义）
        is_local = False
        try:
            ws = self.websocket
            if ws and hasattr(ws, 'client') and ws.client:
                is_local = ws.client.host in ('127.0.0.1', '::1', 'localhost')
        except Exception:
            pass
        if is_local:
            try:
                import pyautogui
                from utils.screenshot_utils import compress_screenshot, COMPRESS_TARGET_HEIGHT, COMPRESS_JPEG_QUALITY
                import base64 as b64mod
                def _capture_and_compress() -> bytes:
                    shot = pyautogui.screenshot()
                    if shot.mode in ('RGBA', 'LA', 'P'):
                        shot = shot.convert('RGB')
                    return compress_screenshot(
                        shot,
                        target_h=COMPRESS_TARGET_HEIGHT,
                        quality=COMPRESS_JPEG_QUALITY,
                    )

                jpg_bytes = await asyncio.to_thread(_capture_and_compress)
                b64_str = b64mod.b64encode(jpg_bytes).decode('utf-8')
                logger.info("[%s] request_fresh_screenshot: 后端 pyautogui 兜底成功 (%dKB)", self.lanlan_name, len(jpg_bytes) // 1024)
                return b64_str
            except Exception as e2:
                logger.warning("[%s] request_fresh_screenshot backend fallback failed: %s", self.lanlan_name, e2)

        return ''

    def resolve_screenshot_request(self, b64: str):
        """Called by the WebSocket router to hand the frontend's returned screenshot to the waiting future."""
        if self._screenshot_future and not self._screenshot_future.done():
            self._screenshot_future.set_result(b64)

    async def prepare_proactive_delivery(self, min_idle_secs: float = 10.0) -> bool:
        """Pre-checks before Phase 2 streaming + speech_id generation. Returns True if it's OK to proceed."""
        if self.is_goodbye_silent():
            logger.info("[%s] prepare_proactive_delivery: goodbye silent", self.lanlan_name)
            return False
        # 早期抢占检查：在任何 await / sid 改写前快速短路，防止用户刚在入口之后
        # 抢占而后续 self.current_speech_id 写入覆盖用户的 user_sid。默认 reset()
        # 对活动 phase no-op（保护 auto-start 期间偶发并发 reset），但 end_session
        # 走 force=True 强制清场——这里短路不依赖 reset() 的语义差异，单纯是为
        # 了更早放弃已被抢占的 proactive 轮次。
        if self.state.is_proactive_preempted():
            logger.info("[%s] prepare_proactive_delivery: preempted before claim", self.lanlan_name)
            return False
        if self.last_user_activity_time is not None:
            if time.time() - self.last_user_activity_time < min_idle_secs:
                logger.info("[%s] prepare_proactive_delivery: user active recently", self.lanlan_name)
                return False
        if self.is_active and isinstance(self.session, OmniRealtimeClient):
            logger.info("[%s] prepare_proactive_delivery: voice session active", self.lanlan_name)
            return False
        if not self.websocket:
            return False
        try:
            if (hasattr(self.websocket, 'client_state')
                    and self.websocket.client_state != self.websocket.client_state.CONNECTED):
                return False
        except Exception:
            pass
        if not self.session or not hasattr(self.session, '_conversation_history'):
            try:
                await self.start_session(self.websocket, new=False, input_mode='text')
            except Exception as e:
                logger.warning("[%s] prepare_proactive_delivery: session start failed: %s", self.lanlan_name, e)
                return False
            if not self.session or not hasattr(self.session, '_conversation_history'):
                return False
            # auto-start 期间耗时 await；再次确认 proactive 未被用户抢占
            if self.state.is_proactive_preempted():
                logger.info("[%s] prepare_proactive_delivery: preempted during auto-start", self.lanlan_name)
                return False
        async with self.lock:
            # lock 内二次复查：USER_INPUT 在 self.lock 内 rotate sid，sticky preempt
            # flag 先于 sid mutation 翻起；此处若已被抢占则不写 current_speech_id。
            if self.state.is_proactive_preempted():
                logger.info("[%s] prepare_proactive_delivery: preempted in claim lock", self.lanlan_name)
                return False
            self.current_speech_id = str(uuid4())
            self._tts_done_queued_for_turn = False
            self._tts_done_pending_until_ready = False
            claim_sid = self.current_speech_id
        # 状态机：正式 claim turn。订阅者（诊断、frontend sync 等）在此之后
        # 观察到 proactive_sid 已与 current_speech_id 一致。
        await self.state.fire(SessionEvent.PROACTIVE_CLAIM, sid=claim_sid)
        return True

    async def feed_tts_chunk(self, text: str, expected_speech_id: str | None = None):
        """Feed text to the TTS pipeline only, without sending it to the frontend display.

        expected_speech_id: if not None and it doesn't match the current
        current_speech_id (meaning the caller's turn has been taken over by another
        path, e.g. the user interrupted during proactive streaming), drop this
        chunk and return. The check happens inside the lock to stay atomic with
        the enqueue, so proactive text can't be mislabeled with the new turn's
        speech_id and flow into the user's normal reply audio.
        """
        if not self.use_tts:
            return
        async with self.tts_cache_lock:
            if expected_speech_id is not None and self.current_speech_id != expected_speech_id:
                logger.debug(
                    "feed_tts_chunk drop: expected_sid=%s current_sid=%s len=%d",
                    expected_speech_id, self.current_speech_id, len(text),
                )
                return
            if self.tts_ready and self.tts_thread and self.tts_thread.is_alive():
                try:
                    self._enqueue_tts_text_chunk(self.current_speech_id, text)
                except Exception as e:
                    logger.warning(f"⚠️ feed_tts_chunk 失败: {e}")
            else:
                self.tts_pending_chunks.append((self.current_speech_id, text))
                # Worker 已死亡则尝试拉起（受 12 秒冷却限制，不会风暴重连）
                if self.tts_thread and not self.tts_thread.is_alive():
                    self._respawn_tts_worker()

    async def finish_proactive_delivery(
        self,
        full_text: str,
        expected_speech_id: str | None = None,
        action_note: str | None = None,
        source_tag: str | None = None,
    ) -> bool:
        """Wrap-up after streaming completes: deliver the full text in one shot + record history + TTS/turn end signals.

        expected_speech_id: if not None and it no longer matches
        current_speech_id after entering _proactive_write_lock, the user
        interrupted and took over the turn between the end of the Phase 2 stream
        and finish (stream_text cleared the queue + rotated the sid). In that case
        the frontend/history/TTS end signals must all be skipped, or the proactive
        text bubble would appear after the user's reply, history would be
        polluted, and TTS done would wrongly terminate the user's in-progress
        reply.

        action_note: optional; when non-empty it is appended to the tail of that
        AIMessage's content in _conversation_history (history-only — never enters
        send_lanlan_response or TTS). Used to leave "what song was actually
        played / what content was shared / where it came from" as metadata for
        the LLM to see next turn, so when the user asks "what was that song just
        now" the AI isn't clueless — remembering only what it said but not what
        it did. Construction logic in
        ``config.prompts.prompts_proactive.build_proactive_action_note``.

        Returns True when genuinely persisted, False when skipped due to a sid
        change. The caller uses this to short-circuit downstream side effects
        (_record_proactive_chat / topic usage / surfaced reflection etc.), so
        undelivered content is never recorded as "delivered".
        """
        async with self._proactive_write_lock:
            if expected_speech_id is not None and self.current_speech_id != expected_speech_id:
                logger.info(
                    "[%s] finish_proactive_delivery skip: sid changed (expected=%s current=%s)，用户已接管本轮",
                    self.lanlan_name, expected_speech_id, self.current_speech_id,
                )
                return False
            # 冻结 commit 用的 turn_id：current_speech_id 由 self.lock 保护，不在
            # _proactive_write_lock 范围内，下面 send_lanlan_response 之前若用户经
            # handle_new_message/stream_text 抢占完成 sid 轮换，再让 send_lanlan_response
            # 默认从 self.current_speech_id 取值会把这条 proactive 气泡打到用户新
            # turn 上、前端分组串掉。expected_speech_id 在 phase2 已经一路传到这里
            # 并且刚校验过，作为冻结快照最稳。
            commit_sid = expected_speech_id or self.current_speech_id
            # 状态机：进入 COMMITTING 阶段；期间若用户抢占仍会 sticky 到 _preempted，
            # 但本处 lock 内 sid 已校验过，commit 本身安全。
            await self.state.fire(SessionEvent.PROACTIVE_COMMITTING)
            await self.send_lanlan_response(full_text, is_first_chunk=True, turn_id=commit_sid)

            # Flush per-turn AI-text buffer to activity tracker. The regular
            # /api/proactive_chat path doesn't call handle_proactive_complete
            # (only the agent-direct-reply path in main_server.py does), so
            # without this the buffer would carry the proactive text forward
            # and contaminate the next user-initiated turn's AI message.
            self._flush_ai_turn_text_to_tracker()

            if self.session and hasattr(self.session, '_conversation_history'):
                # action_note 只进历史，不进 send_lanlan_response（前端不展示）
                # 也不进 TTS。空 full_text + 非空 note 的场景目前不会发生
                # （proactive 不允许空文本），但写法上仍然兜底拼接。
                history_text = full_text
                if action_note:
                    note = action_note.strip()
                    if note:
                        history_text = f"{full_text}\n{note}" if full_text else note
                self.session._conversation_history.append(AIMessage(content=history_text))
                # 防复读 corpus：只录"被说出口的"那段（full_text），action_note 是
                # LLM 给自己的元数据备忘，不算复读对象。素材推送类 channel（推歌）
                # 的台词天生模板化，录进 corpus 会污染 FG 窗、漂移其它 channel 的
                # 复读基线，故按 ANTI_REPEAT_EXEMPT_SOURCE_TAGS 豁免（与出口的
                # BM25 评分豁免对偶）。
                if source_tag not in ANTI_REPEAT_EXEMPT_SOURCE_TAGS:
                    try:
                        from memory.anti_repeat import get_anti_repeat_corpus
                        get_anti_repeat_corpus().record_output(
                            self.lanlan_name, full_text, is_proactive=True,
                        )
                    except Exception as _exc:  # pragma: no cover
                        logger.debug("[AntiRepeat] record proactive skipped: %s", _exc)

            if self.use_tts and self.tts_thread and self.tts_thread.is_alive() and not self._tts_done_queued_for_turn:
                try:
                    await self._request_tts_done_for_turn("finish_proactive_delivery")
                except Exception:
                    pass

            self.sync_message_queue.put({'type': 'system', 'data': 'turn end'})
            try:
                if (self.websocket
                        and hasattr(self.websocket, 'client_state')
                        and self.websocket.client_state == self.websocket.client_state.CONNECTED):
                    await self.websocket.send_json({'type': 'system', 'data': 'turn end'})
            except Exception:
                pass
        # proactive 原文不写 logger（隐私）；本地 print 兜底
        logger.info("[%s] Proactive stream delivered (text_len=%d)", self.lanlan_name, len(full_text or ""))
        print(f"[{self.lanlan_name}] Proactive stream delivered: {(full_text or '')[:40]}…")
        return True

    async def handle_avatar_interaction(self, payload: dict) -> dict:
        raw_interaction_id = str(payload.get("interaction_id") or payload.get("interactionId") or "").strip() if isinstance(payload, dict) else ""
        raw = _normalize_avatar_interaction_payload(payload)
        if not raw:
            logger.debug("[%s] handle_avatar_interaction: ignored invalid payload", self.lanlan_name)
            await self.send_avatar_interaction_ack(raw_interaction_id, False, "invalid_payload")
            return {"accepted": False, "reason": "invalid_payload"}

        interaction_id = raw["interaction_id"]
        now_ms = int(time.time() * 1000)

        if interaction_id in self._recent_avatar_interaction_id_set:
            logger.debug("[%s] handle_avatar_interaction: duplicate interaction_id=%s", self.lanlan_name, interaction_id)
            await self.send_avatar_interaction_ack(interaction_id, False, "duplicate")
            return {"accepted": False, "reason": "duplicate", "interaction_id": interaction_id}

        if now_ms - self._last_avatar_interaction_at < self.avatar_interaction_cooldown_ms:
            logger.debug("[%s] handle_avatar_interaction: cooldown skip interaction_id=%s", self.lanlan_name, interaction_id)
            self._remember_avatar_interaction_id(interaction_id)
            await self.send_avatar_interaction_ack(interaction_id, False, "cooldown")
            return {"accepted": False, "reason": "cooldown", "interaction_id": interaction_id}

        self._remember_avatar_interaction_id(interaction_id)
        self._last_avatar_interaction_at = now_ms

        if self.is_active and isinstance(self.session, OmniRealtimeClient):
            logger.debug("[%s] handle_avatar_interaction: voice session active, skipping", self.lanlan_name)
            await self.send_avatar_interaction_ack(interaction_id, False, "voice_session_active")
            return {"accepted": False, "reason": "voice_session_active", "interaction_id": interaction_id}

        if not (self.is_active and isinstance(self.session, OmniOfflineClient)):
            if not self._has_connected_websocket():
                logger.warning("[%s] handle_avatar_interaction: no connected websocket, skipping", self.lanlan_name)
                await self.send_avatar_interaction_ack(interaction_id, False, "no_websocket")
                return {"accepted": False, "reason": "no_websocket", "interaction_id": interaction_id}
            try:
                logger.info("[%s] handle_avatar_interaction: auto-starting text session", self.lanlan_name)
                await self.start_session(self.websocket, new=False, input_mode='text')
            except Exception as e:
                logger.warning("[%s] handle_avatar_interaction: auto start_session failed: %s", self.lanlan_name, e)
                await self.send_avatar_interaction_ack(interaction_id, False, "session_start_failed")
                return {"accepted": False, "reason": "session_start_failed", "interaction_id": interaction_id}

        if not (self.is_active and isinstance(self.session, OmniOfflineClient)):
            logger.warning("[%s] handle_avatar_interaction: session is not text mode after start, skipping", self.lanlan_name)
            await self.send_avatar_interaction_ack(interaction_id, False, "not_text_session")
            return {"accepted": False, "reason": "not_text_session", "interaction_id": interaction_id}

        instruction = _build_avatar_interaction_instruction(
            getattr(self, "user_language", None),
            self.lanlan_name,
            self.master_name,
            raw,
        )
        memory_meta = _build_avatar_interaction_memory_meta(
            getattr(self, "user_language", None),
            raw,
            self.master_name,
        )
        memory_note = memory_meta["memory_note"]
        delivered = False

        async with self._proactive_write_lock:
            if not (self.is_active and isinstance(self.session, OmniOfflineClient)):
                await self.send_avatar_interaction_ack(interaction_id, False, "session_changed")
                return {"accepted": False, "reason": "session_changed", "interaction_id": interaction_id}
            if getattr(self.session, "_is_responding", False):
                logger.debug("[%s] handle_avatar_interaction: text session busy, skipping", self.lanlan_name)
                await self.send_avatar_interaction_ack(interaction_id, False, "busy")
                return {"accepted": False, "reason": "busy", "interaction_id": interaction_id}
            speak_now_ms = int(time.time() * 1000)
            if speak_now_ms - self._last_avatar_interaction_speak_at < self.avatar_interaction_speak_cooldown_ms:
                logger.debug("[%s] handle_avatar_interaction: speak cooldown skip interaction_id=%s", self.lanlan_name, interaction_id)
                await self.send_avatar_interaction_ack(interaction_id, False, "speak_cooldown")
                return {"accepted": False, "reason": "speak_cooldown", "interaction_id": interaction_id}

            async with self.lock:
                self.current_speech_id = str(uuid4())
                self._tts_done_queued_for_turn = False

            if hasattr(self.session, 'update_max_response_length'):
                self.session.update_max_response_length(self._get_text_guard_max_length())

            # 后端打标：把 avatar interaction 元数据挂在 session manager 上，
            # 等 prompt_ephemeral 触发 handle_response_complete 时随 turn end
            # 原子地下发。不再走独立的 sync_message_queue 控制消息，避免
            # meta 与 turn end 两条消息时序错乱导致本轮被误判成 proactive。
            self._pending_turn_meta = {
                "kind": "avatar_interaction",
                "interaction_id": interaction_id,
                "memory_note": memory_note,
                "memory_dedupe_key": memory_meta["memory_dedupe_key"],
                "memory_dedupe_rank": memory_meta["memory_dedupe_rank"],
            }

            current_turn_id = self.current_speech_id
            # 主动搭话 race guard：prompt_ephemeral 运行期间若用户发起新输入
            # 会换 current_speech_id + 清 TTS queue，本路径产生的 text delta
            # 必须靠 _proactive_expected_sid 在 handle_text_data/handle_output_transcript
            # 里判同，不一致就丢。和 trigger_agent_callbacks 走同一套保护。
            _sid_token = _proactive_expected_sid.set(current_turn_id)
            try:
                try:
                    delivered = await self.session.prompt_ephemeral(
                        instruction,
                        completion_mode="response",
                        persist_response=False,
                    )
                except Exception as e:
                    logger.exception(
                        "[%s] handle_avatar_interaction: prompt_ephemeral failed interaction_id=%s: %s",
                        self.lanlan_name,
                        interaction_id,
                        e,
                    )
                    # prompt_ephemeral 抛错时 handle_response_complete 不会被触发，
                    # 必须主动清掉 meta，避免泄漏到下一轮。
                    self._pending_turn_meta = None
                    await self.send_avatar_interaction_ack(interaction_id, False, "error")
                    return {"accepted": False, "reason": "error", "interaction_id": interaction_id}
            finally:
                _proactive_expected_sid.reset(_sid_token)

            # Prompt 跑完后若 current_speech_id 已换（用户中途接管），
            # 本轮 avatar 响应算未送达：meta 不该挂到用户的新 turn end 上，
            # ack 也要汇报 interrupted 而非 delivered。
            interrupted = self.current_speech_id != current_turn_id
            accepted = bool(delivered) and not interrupted
            if interrupted:
                self._pending_turn_meta = None
            if accepted:
                self._last_avatar_interaction_speak_at = int(time.time() * 1000)
            ack_reason = "delivered" if accepted else ("interrupted" if interrupted else "empty_response")
            await self.send_avatar_interaction_ack(
                interaction_id,
                accepted,
                ack_reason,
                turn_id=current_turn_id if accepted else "",
            )

        # 未 accepted 时 handle_response_complete 不一定被触发（或者触发在用户
        # 的新 turn 上已被 interrupted 分支清空），留下的 meta 可能被下一轮
        # turn end 误消费；在这里兜底清掉。accepted=True 时 meta 已被
        # handle_response_complete 消费，这里是幂等 no-op。
        if not accepted:
            self._pending_turn_meta = None

        if accepted:
            logger.info(
                "[%s] handle_avatar_interaction: delivered interaction_id=%s tool=%s action=%s",
                self.lanlan_name,
                interaction_id,
                raw["tool_id"],
                raw["action_id"],
            )
            return {"accepted": True, "interaction_id": interaction_id}

        logger.debug(
            "[%s] handle_avatar_interaction: not accepted interaction_id=%s reason=%s",
            self.lanlan_name, interaction_id, ack_reason,
        )
        return {"accepted": False, "reason": ack_reason, "interaction_id": interaction_id}

    def _purge_retracted_agent_callbacks(self) -> None:
        retracted_ids = {
            cb.get("_callback_delivery_id")
            for cb in self.pending_agent_callbacks
            if cb.get(DELIVERY_RETRACTED_KEY) and cb.get("_callback_delivery_id")
        }
        has_retracted = any(
            cb.get(DELIVERY_RETRACTED_KEY)
            for cb in self.pending_agent_callbacks
        )
        if not has_retracted:
            return
        self.pending_agent_callbacks = [
            cb for cb in self.pending_agent_callbacks
            if not cb.get(DELIVERY_RETRACTED_KEY)
        ]
        if retracted_ids:
            self.pending_extra_replies = [
                extra for extra in self.pending_extra_replies
                if extra.get("_callback_delivery_id") not in retracted_ids
            ]

    def _purge_retracted_agent_callback_extras(self, callbacks: list) -> None:
        retracted_ids = {
            cb.get("_callback_delivery_id")
            for cb in callbacks
            if cb.get(DELIVERY_RETRACTED_KEY) and cb.get("_callback_delivery_id")
        }
        if retracted_ids:
            self.pending_extra_replies = [
                extra for extra in self.pending_extra_replies
                if extra.get("_callback_delivery_id") not in retracted_ids
            ]

    async def trigger_agent_callbacks(self) -> bool:
        """Proactively deliver pending agent task results via LLM rephrase.

        Design:
        - Text mode (OmniOfflineClient): claims proactive turn via
          ``state.try_start_proactive()`` then calls ``prompt_ephemeral()`` so
          the LLM generates a styled response in the character's voice.
        - Voice mode (OmniRealtimeClient): defers to hot-swap — callbacks are
          kept in pending_extra_replies for injection via prime_context();
          does not participate in the SM state machine (hot-swap has its own
          independent lifecycle).
        - On failure or when the session is busy, restores callbacks so the next
          handle_response_complete() call will retry automatically.
        - Re-entrancy and the "AI is replying" mutual exclusion are handled by
          the SM's atomic claim; also mutually exclusive with
          ``/api/proactive_chat`` / ``trigger_greeting``.
        """
        def _active_proactive_callbacks(callbacks: list) -> list:
            return [
                cb for cb in callbacks
                if cb.get("delivery_mode") != "passive"
                and not cb.get(DELIVERY_RETRACTED_KEY)
            ]

        sess_type = type(self.session).__name__ if self.session else "None"
        logger.info(
            "[%s] trigger_agent_callbacks enter: session=%s phase=%s pending=%d",
            self.lanlan_name, sess_type, self.state.phase.value, len(self.pending_agent_callbacks),
        )
        if not self.pending_agent_callbacks:
            return False
        self._purge_retracted_agent_callbacks()
        if not self.pending_agent_callbacks:
            return False
        if self.is_goodbye_silent():
            logger.info(
                "[%s] trigger_agent_callbacks deferred: goodbye silent, keeping %d callback(s)",
                self.lanlan_name, len(self.pending_agent_callbacks),
            )
            return False
        # 与 handle_text_data / handle_response_complete 等输出 handler 对偶：
        # takeover 期间普通 chat LLM 输出会被静音，所以现在派发会被吞掉、callback
        # 内容白丢。把入口卡住，callback 留在队列里等 takeover 释放。
        if self._takeover_active:
            logger.info(
                "[%s] trigger_agent_callbacks deferred: session takeover active, keeping %d callback(s) for next attempt",
                self.lanlan_name, len(self.pending_agent_callbacks),
            )
            return False

        # Hard delivery contract: trigger_agent_callbacks ONLY consumes
        # proactive callbacks. Passive ones must remain in the queue and
        # surface only at the next user turn via drain_agent_callbacks_for_llm.
        # Without this filter, a passive callback enqueued earlier would get
        # piggy-backed onto any later proactive trigger — silently breaking
        # ``delivery="passive"``'s "don't interrupt" promise.
        proactive_cbs = _active_proactive_callbacks(self.pending_agent_callbacks)
        if not proactive_cbs:
            logger.debug(
                "[%s] trigger_agent_callbacks: queue has only passive callbacks (n=%d); deferring to next user turn",
                self.lanlan_name, len(self.pending_agent_callbacks),
            )
            return False

        # Voice mode：直接 conversation.item.create(role=user) + response.create，
        # 让 LLM 立即用本角色嗓音主动回应 proactive callback，不等用户开口。
        #
        # Gate：realtime API 同一时刻只允许一个 active response。如果 user 正在
        # 说话（server-VAD 触发 → 自动 response.create）或上一个 response 还
        # 没结束（含 prompt_ephemeral 走的 fudge response），client 再发
        # response.create 会被 reject。phase != IDLE 时说明 text-mode proactive
        # 流水线在跑，也跳。两条都不满足时 callbacks 留在队列，等
        # _finalize_turn_after_emit 在 response.done 之后重新调用本函数重试。
        if isinstance(self.session, OmniRealtimeClient):
            # Serialize the whole check-and-claim against concurrent trigger
            # tasks (see ``_voice_proactive_inject_lock``). Hold the lock across
            # gate → render → inject → prune; a second task blocks here and,
            # once it acquires, re-filters the (now-pruned) queue and finds
            # nothing left to send.
            async with self._voice_proactive_inject_lock:
                # Read the session INSIDE the lock — start_session / end_session
                # / hot-swap may have swapped or torn it down while we waited
                # for the lock. Re-check the type; if it's no longer a voice
                # session, bail (a text-mode path / no session shouldn't be
                # driven from this branch). Using the lock-time instance for
                # gate + inject avoids injecting into a closing old session.
                voice_sess = self.session
                if not isinstance(voice_sess, OmniRealtimeClient):
                    return False
                # Re-filter inside the lock: a concurrent task may have already
                # injected+pruned these cbs while we waited on the lock.
                self._purge_retracted_agent_callbacks()
                proactive_cbs = _active_proactive_callbacks(self.pending_agent_callbacks)
                if not proactive_cbs:
                    return False
                # Playback-aware gate: ``_voice_playback_active`` is True
                # between the FRONTEND's voice_play_start and voice_play_end,
                # i.e. while buffered audio is still AUDIBLY playing — which
                # outlasts the realtime API's response.done (generation end).
                # Injecting then makes her interrupt herself, so defer; the
                # voice_play_end signal re-fires this and the manager releases
                # the next cue only once she has truly stopped talking.
                if (
                    self.state.phase is not ProactivePhase.IDLE
                    or voice_sess.is_active_response()
                    or self._is_voice_playing()
                ):
                    logger.debug(
                        "[%s] trigger_agent_callbacks: voice session busy (phase=%s, active_response=%s, playback=%s); deferring proactive (n=%d)",
                        self.lanlan_name,
                        self.state.phase.value,
                        voice_sess.is_active_response(),
                        self._voice_playback_active,
                        len(proactive_cbs),
                    )
                    return False

                _lang = normalize_language_code(self.user_language, format='short')
                voice_snapshot = [
                    cb for cb in proactive_cbs
                    if not cb.get(DELIVERY_RETRACTED_KEY)
                ]
                if not voice_snapshot:
                    return False
                # NOTE: the callback instruction is built AFTER the media-stream
                # gate + retraction re-filter below (right before inject), so it
                # reflects the final delivered set. Don't build it here — that
                # copy would be stale the moment a cb retracts during streaming.
                # Snapshot the paired extras entries NOW (before prune) so the
                # rejection handler can restore BOTH queues if the server
                # rejects asynchronously.
                delivered_ids = {
                    cb.get("_callback_delivery_id")
                    for cb in voice_snapshot
                    if cb.get("_callback_delivery_id")
                }
                voice_extra_snapshot = [
                    extra for extra in self.pending_extra_replies
                    if extra.get("_callback_delivery_id") in delivered_ids
                ]

                # Server-side rejection of ``response.create`` (e.g.
                # ``response_already_active`` from a VAD race winning between
                # our gate check and our send) is delivered asynchronously as
                # an ``error`` event, not via this call's return value or an
                # exception — and ``handle_messages`` can dispatch it WHILE we
                # are still awaiting ``inject_text_and_request_response`` (i.e.
                # BEFORE the optimistic prune below runs). The handler must
                # survive both orderings:
                #   (a) reject fires DURING the await (cb still in the queue):
                #       set ``_rejected`` so the post-await code SKIPS the prune
                #       — otherwise the success prune would delete a cb the
                #       server refused. Do NOT re-add here (it's still present).
                #   (b) reject fires AFTER the trigger returned + pruned (cb
                #       gone): re-add to BOTH queues by id (dedup-guarded) and,
                #       if idle, re-fire trigger so it doesn't wait for the next
                #       unrelated response.done.
                # The dedup-by-presence check distinguishes the two: present →
                # case (a) (skip re-add, rely on skip-prune); absent → case (b).
                lanlan_name_snapshot = self.lanlan_name
                _reject_state = {"rejected": False}

                def _on_voice_inject_rejected(
                    error_msg: str,
                    _snapshot=voice_snapshot,
                    _extra_snapshot=voice_extra_snapshot,
                    _lanlan=lanlan_name_snapshot,
                    _state=_reject_state,
                ) -> None:
                    _state["rejected"] = True
                    logger.warning(
                        "[%s] voice proactive inject rejected by server: %s; re-enqueuing %d cb(s) for retry",
                        _lanlan, error_msg, len(_snapshot),
                    )
                    # Restore BOTH queues in lockstep — only entries whose
                    # delivery_id is not already present. Present means the
                    # optimistic prune hasn't run yet (case a): leave them and
                    # let the post-await ``_rejected`` check skip the prune.
                    existing_cb_ids = {
                        cb.get("_callback_delivery_id")
                        for cb in self.pending_agent_callbacks
                        if cb.get("_callback_delivery_id")
                    }
                    # Object-identity fallback, symmetric with the success-path
                    # prune: an unstamped cb (no _callback_delivery_id, e.g. a
                    # future caller bypassing enqueue_agent_callback) would
                    # otherwise fail the id-based dedup and get re-appended even
                    # when it's still in the queue (case a) — then skip-prune
                    # keeps both copies → double-delivery on retry. Dedup such
                    # entries by Python id().
                    existing_cb_obj_ids = {id(cb) for cb in self.pending_agent_callbacks}
                    existing_extra_ids = {
                        extra.get("_callback_delivery_id")
                        for extra in self.pending_extra_replies
                        if extra.get("_callback_delivery_id")
                    }
                    for cb in _snapshot:
                        cb_id = cb.get("_callback_delivery_id")
                        if (cb_id and cb_id in existing_cb_ids) or (
                            not cb_id and id(cb) in existing_cb_obj_ids
                        ):
                            continue
                        self.pending_agent_callbacks.append(cb)
                        if cb_id:
                            existing_cb_ids.add(cb_id)
                        else:
                            existing_cb_obj_ids.add(id(cb))
                    for extra in _extra_snapshot:
                        extra_id = extra.get("_callback_delivery_id")
                        if extra_id and extra_id in existing_extra_ids:
                            continue
                        self.pending_extra_replies.append(extra)
                    # Do NOT immediately re-fire trigger here. The dominant
                    # rejection is ``response_already_active``, which by
                    # definition means an active response exists — but the
                    # client may not have processed its ``response.created``
                    # yet, so ``is_active_response()`` reads a STALE False. A
                    # re-fire on that stale state would re-inject → re-reject →
                    # tight loop until state flips (Codex P1). Instead rely on
                    # the retry guaranteed by that active response's
                    # ``response.done`` → ``handle_response_complete`` →
                    # ``_finalize_turn_after_emit`` (which re-calls this when
                    # ``pending_agent_callbacks`` is non-empty). The cb is kept
                    # queued above, so the retry is not lost — just deferred to
                    # the loop-free turn-end hook.

                # Stream any images carried by these cues into the (guaranteed)
                # voice session right before inject, so the proactive response
                # sees the matching visual context (Codex P2).
                if not await self._stream_cb_media(voice_snapshot, voice_sess):
                    # A media stream failed — DEFER the whole inject so this cb
                    # retries WITH its image rather than being delivered
                    # text-only and pruned (which would lose the retained
                    # media). cbs are still in pending_agent_callbacks (not yet
                    # pruned). The manager already emptied its queue and its
                    # inflight timeout only pumps manager-queued items, and no
                    # response.create fired (so no response.done / voice_play_end
                    # to re-drive trigger) — so re-arm a delayed retry here,
                    # otherwise a transient media/WS failure leaves the cue
                    # waiting for an unrelated user turn (Codex P2).
                    logger.info(
                        "[%s] trigger_agent_callbacks: proactive media stream failed; deferring voice inject (%d cb kept, retry armed)",
                        self.lanlan_name, len(voice_snapshot),
                    )
                    self._schedule_proactive_retry(self.proactive_manager.min_gap_s)
                    return False
                voice_snapshot[:] = [
                    cb for cb in voice_snapshot
                    if not cb.get(DELIVERY_RETRACTED_KEY)
                ]
                self._purge_retracted_agent_callbacks()
                if not voice_snapshot:
                    logger.info(
                        "[%s] trigger_agent_callbacks: voice proactive callbacks retracted before inject",
                        self.lanlan_name,
                    )
                    return False
                instruction = _build_callback_instruction(
                    voice_snapshot,
                    lang=_lang,
                    lanlan_name=self.lanlan_name,
                    master_name=self.master_name,
                    passive=False,
                )
                delivered_ids = {
                    cb.get("_callback_delivery_id")
                    for cb in voice_snapshot
                    if cb.get("_callback_delivery_id")
                }
                voice_extra_snapshot[:] = [
                    extra for extra in voice_extra_snapshot
                    if extra.get("_callback_delivery_id") in delivered_ids
                ]
                try:
                    await voice_sess.inject_text_and_request_response(
                        instruction, on_rejected=_on_voice_inject_rejected
                    )
                except NotImplementedError:
                    # Defensive fallback. As of now every realtime provider
                    # (OpenAI / GLM / Step / free / GPT / Qwen / Grok via
                    # conversation.item.create, Gemini via send_client_content)
                    # supports manual inject, so this branch is unreachable in
                    # practice — kept so a hypothetical future provider that
                    # raises NotImplementedError degrades to hot-swap instead of
                    # losing the cb. Drop the proactive cbs so they don't loop
                    # forever, but keep ``pending_extra_replies`` populated for
                    # the next user-turn prime_context() drain.
                    voice_ids = {id(cb) for cb in voice_snapshot}
                    self.pending_agent_callbacks = [
                        cb for cb in self.pending_agent_callbacks
                        if id(cb) not in voice_ids
                    ]
                    logger.info(
                        "[%s] trigger_agent_callbacks: voice provider does not support manual inject; falling back to hot-swap (n=%d)",
                        self.lanlan_name, len(voice_snapshot),
                    )
                    return False
                except Exception as exc:
                    # WS error / fatal / response_already_active race — keep cbs
                    # in the queue so the next phase-idle hook retries them.
                    logger.warning(
                        "[%s] trigger_agent_callbacks: voice proactive inject failed: %s; keeping cbs for retry",
                        self.lanlan_name, exc,
                    )
                    return False

                # If the server rejected asynchronously DURING the await above
                # (case a — ``_on_voice_inject_rejected`` already fired while
                # the cbs were still in the queue), the cbs were intentionally
                # left in place. Pruning now would delete a cb the server
                # refused → silent loss. Skip the prune; the cbs stay queued and
                # are retried via _finalize_turn_after_emit (or the re-fire the
                # handler scheduled). The active response that caused the
                # rejection will fire response.done and trigger the retry.
                if _reject_state["rejected"]:
                    logger.info(
                        "[%s] trigger_agent_callbacks: voice proactive inject rejected during await; keeping %d cb(s) queued for retry",
                        self.lanlan_name, len(voice_snapshot),
                    )
                    return False

                # Inject succeeded. Drop the cbs we delivered from BOTH queues:
                # ``pending_agent_callbacks`` (text-mode drain + proactive
                # trigger) AND the matching ``pending_extra_replies`` entries
                # (voice hot-swap prime channel). Leaving the extras intact would
                # have two concrete bad consequences:
                #   1. ``_finalize_turn_after_emit`` gates immediate session
                #      preparation on ``bool(pending_extra_replies)`` — stale
                #      entries trigger needless background hot-swap prep.
                #   2. The eventual hot-swap re-primes the new session with cbs
                #      the AI already spoke about, producing duplicate
                #      announcements.
                # Match by the stable ``_callback_delivery_id`` stamped on both
                # entries by ``enqueue_agent_callback``. Length-based alignment
                # would be unsafe — ``drain_agent_callbacks_for_llm`` clears
                # ``pending_agent_callbacks`` while leaving
                # ``pending_extra_replies`` intact, so the queues legitimately
                # drift apart across user turns.
                # Object-identity fallback for pending_agent_callbacks: defense
                # in depth against any future code path that appends a cb
                # without going through ``enqueue_agent_callback`` (the only
                # stamper of ``_callback_delivery_id``). extras dicts are fresh
                # objects so there is no id() link — extras rely on the
                # delivery_id contract.
                voice_obj_ids = {id(cb) for cb in voice_snapshot}
                self.pending_agent_callbacks = [
                    cb for cb in self.pending_agent_callbacks
                    if cb.get("_callback_delivery_id") not in delivered_ids
                    and id(cb) not in voice_obj_ids
                ]
                self.pending_extra_replies = [
                    extra for extra in self.pending_extra_replies
                    if extra.get("_callback_delivery_id") not in delivered_ids
                ]
                logger.info(
                    "[%s] trigger_agent_callbacks: voice proactive inject sent (n=%d)",
                    self.lanlan_name, len(voice_snapshot),
                )
                def _resolve_voice_ack_after_rejection_window(
                    _snapshot=tuple(voice_snapshot),
                    _state=_reject_state,
                ) -> None:
                    if _state["rejected"]:
                        return
                    for cb in _snapshot:
                        if cb.get(DELIVERY_RETRACTED_KEY):
                            continue
                        resolve_callback_delivery_ack(cb, True)

                try:
                    loop = asyncio.get_running_loop()
                    loop.call_later(
                        _VOICE_PROACTIVE_ACK_GRACE_S,
                        _resolve_voice_ack_after_rejection_window,
                    )
                except RuntimeError:
                    _resolve_voice_ack_after_rejection_window()
                return True

        callbacks_snapshot = list(proactive_cbs)

        # 原子 check-and-claim：若另一路 proactive（router/greeting）在跑或 AI
        # 正在为用户回复，SM 拒绝本次投递，callbacks 留在 pending 下轮重试。
        claim_session = self.session if isinstance(self.session, OmniOfflineClient) else None
        if not await self.state.try_start_proactive(session=claim_session):
            logger.debug(
                "[%s] trigger_agent_callbacks: SM denied claim (phase=%s), re-queuing",
                self.lanlan_name, self.state.phase.value,
            )
            return False

        callbacks_snapshot = [
            cb for cb in callbacks_snapshot
            if not cb.get(DELIVERY_RETRACTED_KEY)
        ]
        self._purge_retracted_agent_callbacks()
        if not callbacks_snapshot:
            await self.state.fire(SessionEvent.PROACTIVE_DONE)
            return False

        # Drop only the snapshot cbs from the queue once we have the SM
        # claim — keep both pre-existing passive cbs and any callbacks
        # that another task enqueued during the ``await try_start_proactive``
        # window (``enqueue_agent_callback`` is sync + lock-free, so this race
        # window is real). Filtering by ``delivery_mode == "passive"`` would
        # wipe such fresh proactive cbs since ``callbacks_snapshot`` only
        # restores pre-claim entries on exception. preempt / not-delivered /
        # exception 路径靠 ``extend(callbacks_snapshot)`` 把本次 snapshot
        # 放回队列，保证投递失败不会丢消息。
        snapshot_ids = {id(cb) for cb in callbacks_snapshot}
        self.pending_agent_callbacks = [
            cb for cb in self.pending_agent_callbacks
            if id(cb) not in snapshot_ids
        ]

        delivered = False
        try:
            if isinstance(self.session, OmniOfflineClient):
                delivered = await self._deliver_agent_callbacks_text(callbacks_snapshot)
            else:
                ws = self.websocket
                if ws and hasattr(ws, 'client_state') and ws.client_state == ws.client_state.CONNECTED:
                    try:
                        await self.start_session(ws, new=False, input_mode='text')
                    except Exception as e:
                        logger.warning("[%s] trigger_agent_callbacks: auto start_session failed: %s", self.lanlan_name, e)
                if isinstance(self.session, OmniOfflineClient):
                    delivered = await self._deliver_agent_callbacks_text(callbacks_snapshot)
                    logger.debug("[%s] trigger_agent_callbacks: auto text session delivered", self.lanlan_name)
                else:
                    logger.debug("[%s] trigger_agent_callbacks: no websocket/session, re-queueing for later", self.lanlan_name)
                    self.pending_agent_callbacks.extend(callbacks_snapshot)
                    callbacks_snapshot[:] = []
        except Exception as e:
            logger.warning("[%s] trigger_agent_callbacks error: %s", self.lanlan_name, e)
            self.pending_agent_callbacks.extend(callbacks_snapshot)
        finally:
            await self.state.fire(SessionEvent.PROACTIVE_DONE)
        if delivered:
            for cb in callbacks_snapshot:
                resolve_callback_delivery_ack(cb, True)
        return delivered

    async def _deliver_agent_callbacks_text(self, callbacks_snapshot: list) -> bool:
        """Execute prompt_ephemeral on an OmniOfflineClient session inside the
        proactive write lock. Caller holds the SM proactive claim (PHASE1).

        Returns True iff genuinely delivered. Returns False when the user preempts
        between the claim and the lock (``mark_user_input_preempt`` flipped
        ``_preempted`` inside ``self.lock`` and ``current_speech_id`` has already
        rotated to the new user sid) — in that case we must not overwrite.
        """
        async with self._proactive_write_lock:
            async with self.lock:
                # Delivery-point topic re-gate (1/2 — cheap early-out before the
                # sid claim). A topic hook can pass the release gate, get copied
                # into callbacks_snapshot + removed from pending_agent_callbacks,
                # then this trigger parks on try_start_proactive /
                # _proactive_write_lock while the user starts a new turn, opens a
                # voice session, or otherwise closes the callback-specific gate.
                # That in-flight snapshot is in neither queue, so queue sweeps
                # cannot reach it and the release gate's check has gone stale.
                # Drop topic hooks with ack False so TopicHookPool retries later;
                # the retracted filter below removes them + their extras. A SECOND
                # identical re-gate runs right before prompt_ephemeral to catch a
                # gate closure that lands during the CLAIM/PHASE2 awaits in between.
                if self._retract_unavailable_topic_hook_snapshots(callbacks_snapshot):
                    logger.info("[%s] trigger_agent_callbacks: topic hook dropped before claim — delivery gate closed mid-delivery", self.lanlan_name)
                self._purge_retracted_agent_callback_extras(callbacks_snapshot)
                active_callbacks = [
                    cb for cb in callbacks_snapshot
                    if not cb.get(DELIVERY_RETRACTED_KEY)
                ]
                if not active_callbacks:
                    logger.info("[%s] trigger_agent_callbacks: text proactive callbacks retracted before prompt", self.lanlan_name)
                    # Nothing will emit text_start/text_end to free the manager's
                    # inflight slot, so release it now (mirrors
                    # _deliver_proactive_batch's no-op release) — else the next
                    # cue stalls until the inflight timeout.
                    self.proactive_manager.release_inflight_noop()
                    return False
                callbacks_snapshot[:] = active_callbacks
                # sticky preempt 复查：与 prepare_proactive_delivery 同样，在持有
                # self.lock 的临界区内判定。USER_INPUT 路径在本锁段内翻 flag 和
                # 写 user sid 是原子的，如果此处 preempt==True 说明用户已抢到
                # 本轮 turn，必须放弃本次 proactive（否则会把用户刚写好的 sid
                # 再覆盖成 proactive sid，污染 TTS/chunk 分发）。
                if self.state.is_proactive_preempted():
                    logger.info("[%s] trigger_agent_callbacks: preempted before sid claim, skipping", self.lanlan_name)
                    self.pending_agent_callbacks.extend(active_callbacks)
                    return False
                self.current_speech_id = str(uuid4())
                self._tts_done_queued_for_turn = False
                self._tts_done_pending_until_ready = False
                proactive_sid = self.current_speech_id
            # SM：发射 CLAIM（把 proactive_sid 写入 state，供诊断/订阅者观察）
            # 随后立刻 PHASE2，因 prompt_ephemeral 没有可分离的 phase1/phase2 边界
            await self.state.fire(SessionEvent.PROACTIVE_CLAIM, sid=proactive_sid)
            await self.state.fire(SessionEvent.PROACTIVE_PHASE2)
            logger.debug("[%s] trigger_agent_callbacks: text session ready, calling prompt_ephemeral", self.lanlan_name)
            # 更新字数限制（可能用户在对话期间修改了设置）
            if hasattr(self.session, 'update_max_response_length'):
                self.session.update_max_response_length(self._get_text_guard_max_length())
            # NOTE: queue mutation moved to caller (trigger_agent_callbacks
            # extracts the proactive subset before claim). Do NOT clear
            # pending_agent_callbacks here — passive cbs would also get wiped.
            # per-task contextvar：prompt_ephemeral 回调链里 handle_text_data
            # 识别本路径 chunk 并在 sid 被用户抢走后丢弃
            # Collect proactive images carried ON the callbacks and pass them
            # EXPLICITLY to prompt_ephemeral — separate from the user's
            # _pending_images staging queue (which holds the user's next
            # screen/camera frame). Sharing that queue would steal the user's
            # pending image into this proactive turn and rob the user's next
            # message of its visual context (Codex P2). Media stays on the cb
            # until the cb is delivered & pruned, so a failed retry re-collects
            # and re-passes it (preserve-until-success). NOTE: we do NOT call
            # _stream_cb_media for text mode (that's the voice path, which uses
            # the realtime session's persistent conversation.item).
            # Delivery-point topic re-gate (2/2 — authoritative, immediately
            # before prompt_ephemeral). CLAIM/PHASE2 were just awaited above, so
            # the user may have switched to audio or sent a fresh turn since the
            # pre-claim check; re-drop topic hooks here so a stale hook cannot
            # still prompt the old text session.
            if self._retract_unavailable_topic_hook_snapshots(active_callbacks):
                logger.info("[%s] trigger_agent_callbacks: topic hook dropped at prompt — delivery gate closed mid-delivery", self.lanlan_name)
            self._purge_retracted_agent_callback_extras(active_callbacks)
            active_callbacks = [
                cb for cb in active_callbacks
                if not cb.get(DELIVERY_RETRACTED_KEY)
            ]
            callbacks_snapshot[:] = active_callbacks
            if not active_callbacks:
                logger.info("[%s] trigger_agent_callbacks: text proactive callbacks retracted before prompt", self.lanlan_name)
                # Free the inflight slot — text_start/text_end below won't run.
                self.proactive_manager.release_inflight_noop()
                return False
            async with self.lock:
                preempted_before_prompt = (
                    self.state.is_proactive_preempted()
                    or self.current_speech_id != proactive_sid
                )
            if preempted_before_prompt:
                logger.info("[%s] trigger_agent_callbacks: preempted before prompt, re-queueing", self.lanlan_name)
                self.pending_agent_callbacks.extend(active_callbacks)
                callbacks_snapshot[:] = []
                self.proactive_manager.release_inflight_noop()
                return False
            _proactive_images: list = []
            for _cb in active_callbacks:
                if isinstance(_cb, dict):
                    _proactive_images.extend(_cb.get("media_images") or [])
            _lang = normalize_language_code(self.user_language, format='short')
            instruction = _build_callback_instruction(
                active_callbacks,
                lang=_lang,
                lanlan_name=self.lanlan_name,
                master_name=self.master_name,
                passive=False,
            )
            ack_resolved = False

            def _resolve_text_delivery_ack(delivered: bool) -> None:
                nonlocal ack_resolved
                if ack_resolved:
                    return
                ack_resolved = True
                for cb in active_callbacks:
                    resolve_callback_delivery_ack(cb, delivered)

            _sid_token = _proactive_expected_sid.set(proactive_sid)
            # Text-mode playback boundary for the pacing manager: no frontend
            # audio signal arrives for text delivery, so bracket prompt_ephemeral
            # with text_start/text_end. text_end clears the manager's in-flight
            # slot + applies min-gap before the next proactive cue.
            try:
                self.lifecycle_bus.emit("text_start")
            except Exception:
                # A lifecycle signal must never break delivery. The bus
                # already isolates per-handler failures (logger.exception);
                # this guard only covers an emit() that itself somehow raises.
                logger.debug("[%s] lifecycle_bus emit(text_start) failed", self.lanlan_name)
            try:
                try:
                    delivered = await self.session.prompt_ephemeral(
                        instruction,
                        images=_proactive_images or None,
                        on_committed=lambda: _resolve_text_delivery_ack(True),
                    )
                except Exception as exc:
                    if ack_resolved:
                        logger.warning(
                            "[%s] trigger_agent_callbacks: prompt_ephemeral failed after committed output; treating callback delivery as complete: %s",
                            self.lanlan_name,
                            exc,
                        )
                        delivered = True
                    else:
                        raise
            finally:
                _proactive_expected_sid.reset(_sid_token)
                try:
                    self.lifecycle_bus.emit("text_end")
                except Exception:
                    # Same rationale as text_start; never let signalling break
                    # the delivery path's finally cleanup.
                    logger.debug("[%s] lifecycle_bus emit(text_end) failed", self.lanlan_name)
            logger.debug("[%s] trigger_agent_callbacks: prompt_ephemeral delivered=%s", self.lanlan_name, delivered)
            if delivered or ack_resolved:
                _resolve_text_delivery_ack(True)
                delivered_ids = {
                    cb.get("_callback_delivery_id")
                    for cb in active_callbacks
                    if cb.get("_callback_delivery_id")
                }
                if delivered_ids:
                    self.pending_extra_replies = [
                        extra for extra in self.pending_extra_replies
                        if extra.get("_callback_delivery_id") not in delivered_ids
                    ]
                return True
            else:
                _resolve_text_delivery_ack(False)
                self.pending_agent_callbacks.extend(active_callbacks)
                return False

    def _is_voice_session_active_or_starting(self) -> bool:
        """Returns True while a voice session is starting or already active, to keep greetings from disturbing the voice stream."""
        if self._starting_session_count > 0 and (self._starting_input_mode or self.input_mode) == 'audio':
            return True
        if self.is_active and self.input_mode == 'audio':
            return True
        return False

    def _voice_delivery_blocked(self) -> bool:
        """True whenever a deep-topic hook could still reach the voice path, so
        topic delivery must defer. The union of two predicates, each covering a
        transition window the other misses:
          - ``isinstance(self.session, OmniRealtimeClient)``: the live session is
            realtime. This still holds during an audio→text switch, where
            ``start_session`` flips the input-mode flags to text while the old
            voice session lingers in ``self.session`` for several awaited
            teardown steps — and ``trigger_agent_callbacks`` would still take its
            ``isinstance``-gated voice branch and inject into that old session.
          - ``_is_voice_session_active_or_starting()``: a voice session is active
            or starting, covering the text→audio startup window before the
            realtime client is installed in ``self.session``.
        Using the union keeps the gate aligned with the exact condition under
        which the voice branch fires."""
        return (
            isinstance(self.session, OmniRealtimeClient)
            or self._is_voice_session_active_or_starting()
        )

    async def trigger_greeting(self) -> None:
        """On first connect or character switch, trigger a proactive greeting based on the gap since the last conversation.

        Flow: query memory_server for the gap → build the guiding prompt → proactively start a text session → deliver.
        """
        if self.is_goodbye_silent():
            logger.info("[%s] trigger_greeting: goodbye silent, skipping", self.lanlan_name)
            return
        # ── 守卫：语音 session 正在启动 / 已活跃时，跳过 greeting ──
        if self._is_voice_session_active_or_starting():
            logger.info("[%s] trigger_greeting: voice session active/starting, skipping", self.lanlan_name)
            return
        # ── 守卫：takeover 期间跳过 greeting ──
        # 与 trigger_voice_proactive_nudge / trigger_agent_callbacks 对偶。
        # takeover 时 ordinary chat 输出在 handler 层会被静音，跑 greeting
        # 只会白消耗节日 budget + 写一份永远到不了用户的 LLM 回复。
        if self._takeover_active:
            logger.info("[%s] trigger_greeting: session takeover active, skipping", self.lanlan_name)
            return

        # 复用 internal_http_client 单例：session 启动路径，避开 AsyncClient 构造开销
        # （Windows idle 157ms，事件循环压力下可达 1.1s，详见 utils/internal_http_client.py）
        try:
            from utils.internal_http_client import get_internal_http_client
            _mem_client = get_internal_http_client()
            resp = await _mem_client.get(
                f"http://127.0.0.1:{self.memory_server_port}/last_conversation_gap/{self.lanlan_name}",
                timeout=2.0,
            )
            if not resp.is_success:
                logger.warning("[%s] trigger_greeting: memory server returned %s", self.lanlan_name, resp.status_code)
                return
            gap_seconds = resp.json().get("gap_seconds", -1)
        except Exception as e:
            logger.warning("[%s] trigger_greeting: failed to query gap: %s", self.lanlan_name, e)
            return

        if gap_seconds < 900:  # < 15分钟，不触发
            logger.debug("[%s] trigger_greeting: gap %.0fs < 15min, skipping", self.lanlan_name, gap_seconds)
            return

        # ── await 归来后再检查一次：memory 查询期间用户可能已点了麦克风 ──
        if self._is_voice_session_active_or_starting():
            logger.info("[%s] trigger_greeting: voice session appeared during gap query, skipping", self.lanlan_name)
            return

        _lang = normalize_language_code(self.user_language, format='short')
        from config.prompts.prompts_proactive import get_greeting_prompt, get_time_of_day_hint
        from utils.time_format import format_elapsed as _format_elapsed
        from utils.holiday_cache import preview_holiday_or_weekend_hint, commit_holiday_or_weekend_hint
        template = get_greeting_prompt(gap_seconds, _lang)
        if not template:
            return

        # 先确认投递通道可用，再消费节日预算（避免 session 拉起失败白扣次数）
        # 如果已有 text session 且空闲，直接走投递逻辑
        if isinstance(self.session, OmniOfflineClient) and not getattr(self.session, "_is_responding", False):
            pass
        else:
            # 没有 session 或不是 text session → 主动拉起
            # ── 拉起前再次检查：避免与即将到来的语音 session 竞争 ──
            if self._is_voice_session_active_or_starting():
                logger.info("[%s] trigger_greeting: voice session appeared before text session auto-start, skipping", self.lanlan_name)
                return
            ws = self.websocket
            if not ws or not hasattr(ws, 'client_state') or ws.client_state != ws.client_state.CONNECTED:
                logger.warning("[%s] trigger_greeting: no connected websocket, aborting", self.lanlan_name)
                return
            try:
                logger.info("[%s] trigger_greeting: auto-starting text session", self.lanlan_name)
                await self.start_session(ws, new=False, input_mode='text')
            except Exception as e:
                logger.warning("[%s] trigger_greeting: auto start_session failed: %s", self.lanlan_name, e)
                return

        if not isinstance(self.session, OmniOfflineClient):
            logger.warning("[%s] trigger_greeting: session is not text mode after start, aborting", self.lanlan_name)
            return

        # 投递通道已就绪，构建 instruction（节日预算仅 preview，不消费）
        elapsed = _format_elapsed(_lang, gap_seconds)
        time_hint = get_time_of_day_hint(_lang).format(master=self.master_name)

        _holiday_token = None
        try:
            holiday_hint_text, _holiday_token = await preview_holiday_or_weekend_hint(_lang, self.lanlan_name)
        except Exception as e:
            logger.debug("[%s] trigger_greeting: holiday hint failed: %s", self.lanlan_name, e)
            holiday_hint_text = None
        holiday_hint = (holiday_hint_text + '\n') if holiday_hint_text else ''

        instruction = template.format(
            elapsed=elapsed, name=self.lanlan_name, master=self.master_name,
            time_hint=time_hint, holiday_hint=holiday_hint,
        )
        print(f"[trigger_greeting] instruction:\n{instruction}")
        logger.info("[%s] trigger_greeting: gap=%.0fs elapsed=%s, delivering", self.lanlan_name, gap_seconds, elapsed)

        # ── 投递前最终检查：构建 instruction 期间（holiday hint 等 await）语音可能已接管 ──
        if self._is_voice_session_active_or_starting():
            logger.info("[%s] trigger_greeting: voice session took over before delivery, skipping", self.lanlan_name)
            return

        # 原子 SM claim：与 trigger_agent_callbacks / /api/proactive_chat 互斥
        # 并拦截"AI 正在为用户回复"（session._is_responding）的场景
        if not await self.state.try_start_proactive(session=self.session):
            logger.info(
                "[%s] trigger_greeting: SM denied claim (phase=%s), skipping",
                self.lanlan_name, self.state.phase.value,
            )
            return

        try:
            async with self._proactive_write_lock:
                # 持锁后仍需检查：_proactive_write_lock 等待期间语音可能已启动
                if self._is_voice_session_active_or_starting():
                    logger.info("[%s] trigger_greeting: voice session took over while waiting for write lock, skipping", self.lanlan_name)
                    return
                async with self.lock:
                    # sticky preempt 复查：USER_INPUT 路径在本锁段内翻 flag 和写
                    # user sid 是原子的；若 preempt==True 说明用户已抢到本轮 turn，
                    # 不能再覆盖 current_speech_id 成 proactive sid。
                    if self.state.is_proactive_preempted():
                        logger.info("[%s] trigger_greeting: preempted before sid claim, skipping", self.lanlan_name)
                        return
                    self.current_speech_id = str(uuid4())
                    self._tts_done_queued_for_turn = False
                    self._tts_done_pending_until_ready = False
                    proactive_sid = self.current_speech_id
                await self.state.fire(SessionEvent.PROACTIVE_CLAIM, sid=proactive_sid)
                await self.state.fire(SessionEvent.PROACTIVE_PHASE2)
                _sid_token = _proactive_expected_sid.set(proactive_sid)
                try:
                    # 防御 stale session: 4429 start_session 之后到这里又过了
                    # 多次 await（holiday hint / try_start_proactive /
                    # _proactive_write_lock / self.lock / state.fire ×2），
                    # 期间 cleanup / disconnected_by_server / 切音色重建路径
                    # 都可能把 self.session 置 None 或换为 OmniRealtimeClient。
                    # 直接 self.session.prompt_ephemeral 会触发 AttributeError
                    # 把 trigger_greeting task 整个挂掉（参考切音色后并发
                    # session 重建期间 trigger_greeting 撞 self.session=None
                    # 的崩溃 trace）。先快照本地引用 + 类型校验，stale 时
                    # 静默 skip，外层 finally 会 fire PROACTIVE_DONE 让 SM
                    # 不卡在 PHASE2 / CLAIM。
                    session_ref = self.session
                    if not isinstance(session_ref, OmniOfflineClient):
                        logger.info(
                            "[%s] trigger_greeting: session swapped/nullified "
                            "before prompt_ephemeral (now=%s), skipping",
                            self.lanlan_name, type(session_ref).__name__,
                        )
                        return
                    delivered = await session_ref.prompt_ephemeral(instruction)
                finally:
                    _proactive_expected_sid.reset(_sid_token)
                logger.info("[%s] trigger_greeting: delivered=%s", self.lanlan_name, delivered)
                # 投递成功后才真正消费节日/周末预算
                # commit 内部会 atomic_write_json 消费预算文件，offload 以免阻塞事件循环
                if delivered and _holiday_token is not None:
                    await asyncio.to_thread(commit_holiday_or_weekend_hint, self.lanlan_name, _holiday_token)
        finally:
            await self.state.fire(SessionEvent.PROACTIVE_DONE)

    async def trigger_cat_greeting(self, duration_seconds: float, tier: str, was_auto: bool) -> None:
        """When transforming back from cat form to catgirl (asking her back), trigger one dedicated greeting based on "behavior (tier) × time spent as a cat".

        Dual of trigger_greeting, but with independent timing: it doesn't query
        last_conversation_gap, instead using the cat-dwell duration measured and
        passed in by the frontend (the datetime gap is "since the last
        conversation", this is "how long she stayed a cat" — two clocks that don't
        interfere). Flow: pick the behavior/duration tier → build the guiding
        prompt → proactively start a text session → deliver.
        """
        if self.is_goodbye_silent():
            logger.info("[%s] trigger_cat_greeting: goodbye silent, skipping", self.lanlan_name)
            return
        # ── 守卫：语音 session 正在启动 / 已活跃时，跳过（与 trigger_greeting 对偶）──
        if self._is_voice_session_active_or_starting():
            logger.info("[%s] trigger_cat_greeting: voice session active/starting, skipping", self.lanlan_name)
            return
        if self._takeover_active:
            logger.info("[%s] trigger_cat_greeting: session takeover active, skipping", self.lanlan_name)
            return

        # tier → 行为：cat1=清醒 / cat2=打盹 / cat3=熟睡。
        behavior = {"cat1": "awake", "cat2": "nap", "cat3": "sleep"}.get(str(tier or "").strip().lower(), "awake")

        _lang = normalize_language_code(self.user_language, format='short')
        from config.prompts.prompts_proactive import (
            get_cat_greeting_prompt, get_cat_greeting_reason_hint, get_time_of_day_hint,
        )
        from utils.time_format import format_elapsed as _format_elapsed
        # < 3min 静默由 get_cat_greeting_prompt 内部判定，返回 None 时不触发。
        template = get_cat_greeting_prompt(behavior, duration_seconds, _lang)
        if not template:
            logger.debug("[%s] trigger_cat_greeting: duration %.0fs below threshold, skipping", self.lanlan_name, duration_seconds)
            return

        # 投递通道：已有空闲 text session 则直接用，否则主动拉起（与 trigger_greeting 对偶）
        if isinstance(self.session, OmniOfflineClient) and not getattr(self.session, "_is_responding", False):
            pass
        else:
            if self._is_voice_session_active_or_starting():
                logger.info("[%s] trigger_cat_greeting: voice session appeared before text session auto-start, skipping", self.lanlan_name)
                return
            ws = self.websocket
            if not ws or not hasattr(ws, 'client_state') or ws.client_state != ws.client_state.CONNECTED:
                logger.warning("[%s] trigger_cat_greeting: no connected websocket, aborting", self.lanlan_name)
                return
            try:
                logger.info("[%s] trigger_cat_greeting: auto-starting text session", self.lanlan_name)
                await self.start_session(ws, new=False, input_mode='text')
            except Exception as e:
                logger.warning("[%s] trigger_cat_greeting: auto start_session failed: %s", self.lanlan_name, e)
                return

        if not isinstance(self.session, OmniOfflineClient):
            logger.warning("[%s] trigger_cat_greeting: session is not text mode after start, aborting", self.lanlan_name)
            return

        # 与 time_hint 一样，reason_hint 先 format 好 {master} 再注入主模板。
        reason_hint = get_cat_greeting_reason_hint(was_auto, _lang).format(master=self.master_name)
        elapsed = _format_elapsed(_lang, duration_seconds)
        time_hint = get_time_of_day_hint(_lang).format(master=self.master_name)

        instruction = template.format(
            reason_hint=reason_hint, elapsed=elapsed, name=self.lanlan_name,
            master=self.master_name, time_hint=time_hint,
        )
        print(f"[trigger_cat_greeting] instruction:\n{instruction}")
        logger.info("[%s] trigger_cat_greeting: behavior=%s duration=%.0fs was_auto=%s elapsed=%s, delivering",
                    self.lanlan_name, behavior, duration_seconds, was_auto, elapsed)

        # ── 投递前最终检查：构建 instruction 期间语音可能已接管 ──
        if self._is_voice_session_active_or_starting():
            logger.info("[%s] trigger_cat_greeting: voice session took over before delivery, skipping", self.lanlan_name)
            return

        # 原子 SM claim：与 trigger_greeting / trigger_agent_callbacks / proactive_chat 互斥
        if not await self.state.try_start_proactive(session=self.session):
            logger.info(
                "[%s] trigger_cat_greeting: SM denied claim (phase=%s), skipping",
                self.lanlan_name, self.state.phase.value,
            )
            return

        try:
            async with self._proactive_write_lock:
                if self._is_voice_session_active_or_starting():
                    logger.info("[%s] trigger_cat_greeting: voice session took over while waiting for write lock, skipping", self.lanlan_name)
                    return
                async with self.lock:
                    if self.state.is_proactive_preempted():
                        logger.info("[%s] trigger_cat_greeting: preempted before sid claim, skipping", self.lanlan_name)
                        return
                    self.current_speech_id = str(uuid4())
                    self._tts_done_queued_for_turn = False
                    self._tts_done_pending_until_ready = False
                    proactive_sid = self.current_speech_id
                await self.state.fire(SessionEvent.PROACTIVE_CLAIM, sid=proactive_sid)
                await self.state.fire(SessionEvent.PROACTIVE_PHASE2)
                _sid_token = _proactive_expected_sid.set(proactive_sid)
                try:
                    # stale session 防御：与 trigger_greeting 同款快照 + 类型校验。
                    session_ref = self.session
                    if not isinstance(session_ref, OmniOfflineClient):
                        logger.info(
                            "[%s] trigger_cat_greeting: session swapped/nullified "
                            "before prompt_ephemeral (now=%s), skipping",
                            self.lanlan_name, type(session_ref).__name__,
                        )
                        return
                    delivered = await session_ref.prompt_ephemeral(instruction)
                finally:
                    _proactive_expected_sid.reset(_sid_token)
                logger.info("[%s] trigger_cat_greeting: delivered=%s", self.lanlan_name, delivered)
        finally:
            await self.state.fire(SessionEvent.PROACTIVE_DONE)

    async def trigger_new_character_greeting(self) -> None:
        from config.prompts.prompts_proactive import get_new_character_greeting_prompt
        from utils.new_character_greeting_state import has_pending, remove_pending

        config_manager = get_config_manager()
        if not await has_pending(config_manager, self.lanlan_name):
            logger.debug("[%s] trigger_new_character_greeting: no pending intent", self.lanlan_name)
            return

        if self.is_goodbye_silent():
            logger.info("[%s] trigger_new_character_greeting: goodbye silent, skipping", self.lanlan_name)
            return

        if self._is_voice_session_active_or_starting():
            logger.info("[%s] trigger_new_character_greeting: voice session active/starting, skipping", self.lanlan_name)
            return

        _lang = normalize_language_code(getattr(self, 'user_language', '') or '', format='short') or get_global_language()
        template = get_new_character_greeting_prompt(_lang)

        if self._is_voice_session_active_or_starting():
            logger.info("[%s] trigger_new_character_greeting: voice session appeared before text session check, skipping", self.lanlan_name)
            return

        if not (self.is_active and isinstance(self.session, OmniOfflineClient)):
            if self._is_voice_session_active_or_starting():
                logger.info("[%s] trigger_new_character_greeting: voice session appeared before text session auto-start, skipping", self.lanlan_name)
                return
            if not self._has_connected_websocket():
                logger.warning("[%s] trigger_new_character_greeting: no connected websocket, aborting", self.lanlan_name)
                return
            try:
                logger.info("[%s] trigger_new_character_greeting: auto-starting text session", self.lanlan_name)
                await self.start_session(self.websocket, new=False, input_mode='text')
            except Exception as e:
                logger.warning("[%s] trigger_new_character_greeting: auto start_session failed: %s", self.lanlan_name, e)
                return

        if not isinstance(self.session, OmniOfflineClient):
            logger.warning("[%s] trigger_new_character_greeting: session is not text mode after start, aborting", self.lanlan_name)
            return

        if not await has_pending(config_manager, self.lanlan_name):
            logger.debug("[%s] trigger_new_character_greeting: pending intent already consumed", self.lanlan_name)
            return

        instruction = template.format(name=self.lanlan_name, master=self.master_name)
        print(f"[trigger_new_character_greeting] instruction:\n{instruction}")
        logger.info("[%s] trigger_new_character_greeting: delivering", self.lanlan_name)

        if self._is_voice_session_active_or_starting():
            logger.info("[%s] trigger_new_character_greeting: voice session took over before delivery, skipping", self.lanlan_name)
            return

        if not await self.state.try_start_proactive(session=self.session):
            logger.info(
                "[%s] trigger_new_character_greeting: SM denied claim (phase=%s), skipping",
                self.lanlan_name, self.state.phase.value,
            )
            return

        delivered = False
        proactive_sid = None
        history_len = None
        appended_snapshot = None
        try:
            async with self._proactive_write_lock:
                if self._is_voice_session_active_or_starting():
                    logger.info("[%s] trigger_new_character_greeting: voice session took over while waiting for write lock, skipping", self.lanlan_name)
                    return
                async with self.lock:
                    if self.state.is_proactive_preempted():
                        logger.info("[%s] trigger_new_character_greeting: preempted before sid claim, skipping", self.lanlan_name)
                        return
                    self.current_speech_id = str(uuid4())
                    self._tts_done_queued_for_turn = False
                    self._tts_done_pending_until_ready = False
                    proactive_sid = self.current_speech_id
                await self.state.fire(SessionEvent.PROACTIVE_CLAIM, sid=proactive_sid)
                await self.state.fire(SessionEvent.PROACTIVE_PHASE2)
                history = getattr(self.session, "_conversation_history", None)
                if isinstance(history, list):
                    history_len = len(history)
                _sid_token = _proactive_expected_sid.set(proactive_sid)
                try:
                    delivered = await self.session.prompt_ephemeral(instruction)
                finally:
                    _proactive_expected_sid.reset(_sid_token)
                if history_len is not None and isinstance(history, list) and len(history) > history_len:
                    appended_snapshot = list(history[history_len:])
                logger.info("[%s] trigger_new_character_greeting: delivered=%s", self.lanlan_name, delivered)
        finally:
            try:
                interrupted = bool(proactive_sid) and self.current_speech_id != proactive_sid
                if (not delivered or interrupted) and history_len is not None:
                    history = getattr(self.session, "_conversation_history", None)
                    if isinstance(history, list) and appended_snapshot:
                        suffix_len = len(appended_snapshot)
                        if suffix_len <= len(history) and history[-suffix_len:] == appended_snapshot:
                            del history[-suffix_len:]
                if delivered and not interrupted:
                    try:
                        await remove_pending(config_manager, self.lanlan_name)
                    except Exception as exc:
                        logger.warning("[%s] trigger_new_character_greeting: remove pending failed: %s", self.lanlan_name, exc)
            finally:
                await self.state.fire(SessionEvent.PROACTIVE_DONE)

    def topic_hook_delivery_allowed(self) -> bool:
        """Whether a background deep-topic hook may interrupt right now.

        Deep topic hooks are brand-new text openers — the most intrusive,
        "better none than forced" kind of proactive content. They must honour
        the same activity gate as ``/api/proactive_chat``: delivery never
        surfaces when the user's propensity is ``closed`` or
        ``restricted_screen_only`` (gaming / focused_work). Unlike the
        proactive reminiscence path there is NO open-thread exception — a
        fresh deep topic is not a follow-up to something already on the table,
        so it shouldn't borrow that escape hatch.

        Voice sessions never receive deep topic hooks. A topic hook is a
        text-mode opener; injecting one mid voice conversation would cut across
        a live spoken exchange, which is exactly the "forced" intrusion this
        feature avoids. Gate on ``_voice_delivery_blocked()`` — the union of "the
        live session is realtime" and "a voice session is active/starting" — so
        the gate matches the exact condition under which
        ``trigger_agent_callbacks`` takes its voice branch, including both the
        text→audio startup window (realtime client not yet installed) and the
        audio→text teardown window (old realtime client still in ``self.session``).
        Returning False here defers rather than drops — the process-global
        per-character ``TopicHookPool`` keeps the material pending and retries
        it once the user is back in a text session, so a voice-heavy user still
        gets the hook later instead of losing it. This is the chokepoint both
        delivery gates consult (``_topic_activity_gate_open`` at submit,
        ``_deliver_proactive_batch`` at release); the session-start drain /
        already-pending / extras-only paths are closed separately in
        ``_reset_proactive_gate`` + ``_drop_pending_topic_hooks_for_voice``.

        Privacy mode is deliberately NOT checked here and no longer gates the
        deep-topic chain upstream either. Store/candidate/prepare/delivery all
        proceed independently from that toggle; this method only answers
        whether a prepared hook may interrupt the current activity context.
        Activity snapshot lookup remains fail-open when no snapshot is
        available, mirroring the proactive path's "snapshot None ⇒ open
        propensity" default.
        """
        if self._voice_delivery_blocked():
            return False
        tracker = getattr(self, '_activity_tracker', None)
        if tracker is None:
            return True
        try:
            snap = tracker.get_snapshot_sync()
        except Exception:
            return True
        propensity = getattr(snap, 'propensity', None)
        if propensity in ('closed', 'restricted_screen_only'):
            return False
        if getattr(snap, 'unfinished_thread', None) is not None:
            logger.info(
                "[%s] topic hook delivery skipped: unfinished thread is still open",
                self.lanlan_name,
            )
            return False
        return True

    def current_topic_language(self) -> Optional[str]:
        """Live full-locale topic language, for re-resolving at delivery time.

        A topic hook captures its language when it is scheduled; if the
        session language changes while the material is pending delivery, that
        captured value goes stale. Topic delivery re-resolves from here so the
        hook renders in the current locale (preserving zh-TW etc.). Returns
        None when no dispatcher is available so the caller keeps the captured
        language.
        """
        dispatcher = getattr(self, '_turn_dispatcher', None)
        getter = getattr(dispatcher, 'current_language', None)
        if not callable(getter):
            return None
        try:
            return getter()
        except Exception:
            return None

    def submit_proactive_callback(
        self,
        callback: dict,
        *,
        priority: int = 0,
        coalesce_key: Optional[str] = None,
    ) -> None:
        """Hand a proactive (ai_behavior="respond") cue to the delivery
        manager, which paces/orders/coalesces it before release.

        Replaces the EventBus's old "enqueue + immediately fire trigger"
        for proactive cues. Passive/silent cues do NOT come here — they keep
        their existing direct enqueue-only path so ``delivery="passive"``'s
        "don't interrupt" promise is unchanged.
        """
        if self.is_goodbye_silent():
            self.enqueue_agent_callback(callback)
            logger.debug(
                "[%s] goodbye_silent queued proactive callback for later delivery",
                self.lanlan_name,
            )
            return
        self.proactive_manager.submit(callback, priority=priority, coalesce_key=coalesce_key)

    async def _deliver_proactive_batch(self, callbacks: list) -> None:
        """Release hook invoked by ProactiveDeliveryManager when the gate is
        open. Enqueues the WHOLE batch then fires ONE trigger — trigger drains
        all pending proactive callbacks into a single LLM turn, restoring the
        legacy "several near-simultaneous cues batched into one turn"
        behaviour (the manager only governs WHEN the batch is released, not
        how many cues per turn)."""
        callbacks = [cb for cb in callbacks if not cb.get(DELIVERY_RETRACTED_KEY)]
        # Topic hooks re-validate the delivery gate at RELEASE: the submit-time
        # check in trigger_topic_hook_once can go stale while the manager paces
        # the cue (min-gap / playback). If the user has since moved into a
        # restricted activity OR a voice session has taken over (topic hooks are
        # text-mode openers, never injected mid voice — see
        # topic_hook_delivery_allowed), drop the topic hook (ack=False) so
        # TopicHookPool retries later instead of opening a fresh deep topic at
        # the wrong moment. Other channels are unaffected.
        if callbacks:
            kept = []
            for cb in callbacks:
                if cb.get("channel") == "topic_hook" and not self._topic_hook_release_allowed(cb):
                    resolve_callback_delivery_ack(cb, False)
                    logger.info(
                        "[%s] topic hook held at release: delivery gate restricts interruption",
                        self.lanlan_name,
                    )
                else:
                    kept.append(cb)
            callbacks = kept
        if not callbacks:
            # This release delivered nothing (everything retracted or dropped
            # at the gate), so no playback/text lifecycle signal will arrive to
            # clear the manager's inflight slot. Free it now so the next cue
            # isn't held behind a phantom in-flight delivery for the timeout.
            self.proactive_manager.release_inflight_noop()
            return
        for callback in callbacks:
            self.enqueue_agent_callback(callback)
        # NOTE: images carried by these cues (push_message media_parts for
        # ai_behavior="respond") are streamed at the ACTUAL delivery point
        # inside trigger_agent_callbacks via _stream_cb_media — NOT here. That
        # binds streaming to a guaranteed session and covers every delivery
        # path (manager release / reconnect redelivery / turn-end retry),
        # instead of streaming into a possibly-None / about-to-be-swapped
        # session at release time (Codex P2).
        await self.trigger_agent_callbacks()

    async def _stream_cb_media(self, callbacks: list, session) -> bool:
        """Stream images carried by proactive callbacks (push_message
        media_parts with ai_behavior="respond") into ``session`` right before
        delivery, so the proactive response sees matching visual context.

        Returns False if ANY image failed to stream — the caller must then
        DEFER the whole delivery (don't inject/prompt the text), so the cb
        retries WITH its media next time. Delivering text-only here and then
        pruning the cb would drop the retained media for good (Codex P2).
        Returns True when every image streamed (or there were none / no
        session).

        Bound to the delivery point (not the manager-release point) so it
        covers every path that actually delivers a cb — manager release,
        reconnect redelivery, turn-end retry.

        VOICE path only: OmniRealtimeClient.stream_image() persists the image
        as a conversation.item the immediately-following proactive
        response.create sees (same-turn), and the realtime conversation is an
        accumulating log (not a single-consume queue), so adding a proactive
        image can't steal a user's pending frame. TEXT mode does NOT go through
        here — its proactive images are passed explicitly to prompt_ephemeral()
        (separate from the user's _pending_images staging queue); see
        _deliver_agent_callbacks_text.

        Media is LEFT on the cb (NOT popped) until the cb is delivered &
        pruned, so a deferred / failed-and-retried cb re-streams it instead of
        losing the visual context. On a PARTIAL stream failure the FULL set is
        kept (not just the tail): a stream failure usually means the session is
        closing, so the retry lands on a new session that has none of the
        earlier images — re-streaming everything is correct (Codex P2)."""
        si = getattr(session, "stream_image", None)
        if si is None:
            return True
        all_ok = True
        for cb in callbacks:
            if not isinstance(cb, dict):
                continue
            images = cb.get("media_images")
            if not images:
                continue
            streamed = 0
            for b64 in images:
                try:
                    # Deliberate cue image: bypass the native-vision frame-rate
                    # throttle so it isn't silently dropped behind a recent
                    # high-frequency screen/camera frame (Codex P2).
                    await si(b64, bypass_rate_limit=True)
                    streamed += 1
                except Exception as e:
                    # Keep the FULL media set (do NOT trim already-streamed
                    # ones): a voice stream_image failure almost always means
                    # the session is closing, so the retry runs on a NEW session
                    # whose conversation has none of the earlier images —
                    # trimming would permanently drop them. Re-streaming the
                    # whole set on the (likely new) session is correct; the only
                    # downside is duplicate items if the SAME session is retried,
                    # which is rare in a failure path and harmless (Codex P2 —
                    # overrides the earlier tail-trim). media_images is left
                    # untouched (already the full set). Signal the caller to
                    # DEFER (don't send text-only and prune the media away).
                    logger.warning(
                        "[%s] proactive media stream_image failed (streamed %d/%d); keeping FULL set for retry: %s",
                        self.lanlan_name, streamed, len(images), e,
                    )
                    all_ok = False
                    break
            # All streamed: keep media_images on the cb until it's delivered+
            # pruned (preserve-until-success) so an inject/prompt failure retry
            # re-streams it. Successful delivery removes the cb (and its media).
        return all_ok

    def on_voice_playback_signal(self, *, playing: bool, **meta) -> None:
        """Handle a FRONTEND-reported audio playback boundary.

        ``playing=True`` (voice_play_start) → real audio started; close the
        voice inject gate. ``playing=False`` (voice_play_end) → the browser's
        audio queue fully drained (she actually stopped talking) → open the
        gate and let the manager release the next cue. Re-fires
        ``trigger_agent_callbacks`` on end so a cue deferred mid-playback is
        delivered promptly rather than waiting for the next response.done.
        """
        self._voice_playback_active = bool(playing)
        if playing:
            self._voice_playback_started_ts = time.monotonic()
        try:
            self.lifecycle_bus.emit(
                "voice_play_start" if playing else "voice_play_end", **meta
            )
        except Exception:
            logger.exception("[%s] lifecycle_bus emit failed", self.lanlan_name)
        if not playing and self.pending_agent_callbacks:
            # A cue deferred while she was speaking (gate busy at release) can
            # now go out — but honor the manager's min-gap so this retry doesn't
            # start the next proactive turn with ZERO gap right at audio end.
            # Parity with the manager's own post-playback pump, which also
            # waits min_gap (Codex P2). trigger_agent_callbacks re-gates itself,
            # so a fire that lands while she's speaking again just defers.
            try:
                delay = max(0.0, float(self.proactive_manager.min_gap_s))
            except Exception:
                delay = 0.0
            if delay <= 0.0:
                self._fire_task(self.trigger_agent_callbacks())
            else:
                try:
                    asyncio.get_running_loop().call_later(
                        delay, lambda: self._fire_task(self.trigger_agent_callbacks())
                    )
                except RuntimeError:
                    self._fire_task(self.trigger_agent_callbacks())

    # Ceiling for a missing voice_play_end before the playback gate self-heals:
    # above a normal single reply, but recovers a dropped end-signal reasonably
    # fast. Disconnect/refresh (the common cause) is already handled by session
    # teardown reset, so this only backstops the rare "connection alive but end
    # signal lost" case. Mirror of ProactiveDeliveryManager.max_play_s.
    _VOICE_PLAYBACK_STALE_S = 45.0

    def _is_voice_playing(self) -> bool:
        """Time-bounded read of the playback gate. Auto-clears a stuck
        ``_voice_playback_active`` when no voice_play_end has arrived within
        ``_VOICE_PLAYBACK_STALE_S`` (frontend disconnect/refresh mid-playback),
        so the voice inject gate can never wedge proactive delivery forever."""
        if not self._voice_playback_active:
            return False
        if time.monotonic() - self._voice_playback_started_ts > self._VOICE_PLAYBACK_STALE_S:
            logger.warning(
                "[%s] voice playback gate watchdog: no voice_play_end after %.0fs; clearing stuck flag",
                self.lanlan_name, self._VOICE_PLAYBACK_STALE_S,
            )
            self._voice_playback_active = False
            return False
        return True

    def _schedule_proactive_retry(self, delay: float) -> None:
        """Schedule a delayed ``trigger_agent_callbacks`` so a cb left in
        pending_agent_callbacks (e.g. the voice media-stream deferral path) is
        retried even when nothing else would drive it — the manager has already
        emptied its queue and its inflight timeout only pumps manager-queued
        items, and a media failure before response.create means no
        response.done / voice_play_end arrives to re-fire trigger."""
        try:
            asyncio.get_running_loop().call_later(
                max(0.0, float(delay)),
                lambda: self._fire_task(self.trigger_agent_callbacks()),
            )
        except RuntimeError:
            self._fire_task(self.trigger_agent_callbacks())

    def _can_release_proactive(self) -> bool:
        """Manager-release gate, mirroring the defer conditions in
        ``trigger_agent_callbacks`` so cues stay UNDER manager ordering
        (coalescing/priority) until they can actually be delivered — rather
        than being released into the inner trigger, deferred, and parked in
        ``pending_agent_callbacks`` outside the manager (Codex P2).

        Returns False while: audio is playing (frontend gate), the SM is not
        IDLE (another proactive/greeting turn owns it), or the session is still
        GENERATING a response (_is_responding — covers BOTH the realtime
        response.created→voice_play_start window the playback gate can't see,
        AND an active offline/text user response where try_start_proactive
        would deny the claim)."""
        if self.is_goodbye_silent():
            return False
        # Time-bounded read (NOT the raw _voice_playback_active flag): if the
        # frontend dropped voice_play_end, _is_voice_playing() self-heals after
        # the 30s watchdog, so a stuck flag can't make can_release return False
        # forever and wedge the queue in an endless busy-recheck (Codex P1).
        if self._is_voice_playing():
            return False
        try:
            if self.state.phase is not ProactivePhase.IDLE:
                return False
        except Exception:
            # State unavailable → treat as IDLE and fall through to the rest of
            # the gate; never block delivery on a phase-read hiccup.
            logger.debug("[%s] _can_release_proactive: state.phase unavailable; treating as IDLE", self.lanlan_name)
        sess = self.session
        # Both realtime AND offline sessions expose _is_responding (set while
        # generating a response — user OR proactive); realtime's
        # is_active_response() is just a read of it. Releasing while True would
        # have trigger deny/defer the claim (voice: is_active_response gate;
        # text: try_start_proactive denies during _is_responding) and park the
        # cue in pending_agent_callbacks outside the manager (Codex P2).
        try:
            if sess is not None and getattr(sess, "_is_responding", False):
                return False
        except Exception:
            # Read hiccup → treat as not-responding rather than wedging the queue.
            logger.debug("[%s] _can_release_proactive: _is_responding check failed; treating as not-responding", self.lanlan_name)
        return True

    def _reset_proactive_gate(self) -> None:
        """Reset the playback gate on session lifecycle boundaries (session
        start / end / character switch) so a dropped voice_play_end can't
        carry stale playback state into the next session and wedge delivery.

        Proactive cues are generally important, so cues still queued in the
        manager are NOT dropped: they're moved into pending_agent_callbacks,
        which persists across teardown and is redelivered by the reconnect /
        next-turn path. Only the gate/single-flight state is cleared."""
        self._voice_playback_active = False
        self._voice_playback_started_ts = 0.0
        # end_session may run against a partially constructed manager (e.g.
        # teardown after an early start_session failure), so read the manager
        # defensively like the other teardown helpers on this path.
        manager = getattr(self, "proactive_manager", None)
        if manager is None:
            return
        try:
            leftover = manager.drain_pending()
            for cb in leftover:
                # Hand back to the persistent queue so the reconnect path
                # (websocket_router) / next trigger redelivers rather than
                # losing it.
                self.enqueue_agent_callback(cb)
            manager.reset_gate()
            # Deep topic hooks are text-mode openers and must never be spoken in
            # voice. start_session sets the audio starting flags BEFORE calling
            # us, so when entering / within a voice session this is the one
            # boundary where we sweep EVERY queued topic hook out of
            # pending_agent_callbacks (and the paired pending_extra_replies):
            # both the cbs just drained from the manager AND any released earlier
            # by _deliver_proactive_batch into the pending queue but left
            # deferred (SM busy / media-stream fail / no text session). Both the
            # voice branch of trigger_agent_callbacks (re-fired by start_session)
            # and the hot-swap prime path inject those two queues WITHOUT
            # re-consulting topic_hook_delivery_allowed, so neither delivery gate
            # covers this. Resolve ack False so TopicHookPool defers and retries
            # on a text session.
            if self._voice_delivery_blocked():
                self._drop_pending_topic_hooks_for_voice()
        except Exception:
            # getattr fallback: the except path must never raise itself
            # (a second AttributeError here would abort end_session teardown).
            logger.exception("[%s] proactive_manager reset/drain failed", getattr(self, "lanlan_name", "?"))

    def _drop_pending_topic_hooks_for_voice(self) -> None:
        """Drop every queued deep-topic hook when entering / within a voice
        session, across BOTH delivery queues.

        1. ``pending_agent_callbacks``: hooks here still carry their callback, so
           resolve each one's delivery ack False (``TopicHookPool`` defers +
           retries on a text session) and retract it, letting
           ``_purge_retracted_agent_callbacks`` sweep it and its paired
           ``pending_extra_replies`` entry by ``_callback_delivery_id``.
        2. ``pending_extra_replies`` orphans: ``drain_agent_callbacks_for_llm``
           clears ``pending_agent_callbacks`` on a text user turn but leaves the
           paired extras behind, so a topic hook can survive as an extras-only
           entry (callback already delivered + acked in text) and be rendered by
           the hot-swap ``prime_context`` path. Those have no callback left to
           ack/retract — just drop them. They are identified by
           ``source_kind == "topic"`` (stamped by ``build_topic_hook_callback``
           and copied onto the extra by ``enqueue_agent_callback``).

        See ``_reset_proactive_gate`` for why this is needed beyond the submit /
        release gates."""
        pending = getattr(self, "pending_agent_callbacks", None) or []
        hooks = [
            cb for cb in pending
            if isinstance(cb, dict) and cb.get("channel") == "topic_hook"
        ]
        for cb in hooks:
            resolve_callback_delivery_ack(cb, False)
            cb[DELIVERY_RETRACTED_KEY] = True
        if hooks:
            self._purge_retracted_agent_callbacks()
        # Sweep extras-only topic hooks (callback side already gone).
        extras = getattr(self, "pending_extra_replies", None)
        dropped_extras = 0
        if isinstance(extras, list):
            kept = [
                extra for extra in extras
                if not (isinstance(extra, dict) and extra.get("source_kind") == "topic")
            ]
            dropped_extras = len(extras) - len(kept)
            if dropped_extras:
                self.pending_extra_replies = kept
        if hooks or dropped_extras:
            logger.info(
                "[%s] dropped %d queued + %d extras-only topic hook(s) at voice start: deferred for a text session",
                self.lanlan_name, len(hooks), dropped_extras,
            )

    def _topic_hook_release_allowed(self, callback: dict) -> bool:
        if callback.get("channel") != "topic_hook":
            return True
        if not self.topic_hook_delivery_allowed():
            return False
        release_available = callback.get("_topic_release_available")
        if not callable(release_available):
            return True
        try:
            return bool(release_available())
        except Exception as exc:
            logger.warning(
                "[%s] topic hook release predicate failed closed: %s",
                self.lanlan_name,
                exc,
            )
            return False

    def _retract_unavailable_topic_hook_snapshots(self, callbacks: list) -> int:
        """Retract in-flight topic hooks whose release-time gate closed."""
        n = 0
        for cb in callbacks:
            if (
                isinstance(cb, dict)
                and cb.get("channel") == "topic_hook"
                and not cb.get(DELIVERY_RETRACTED_KEY)
                and not self._topic_hook_release_allowed(cb)
            ):
                resolve_callback_delivery_ack(cb, False)
                cb[DELIVERY_RETRACTED_KEY] = True
                n += 1
        return n

    def _retract_topic_hook_snapshots(self, callbacks: list) -> int:
        """Mark in-flight topic-hook snapshot entries retracted + ack False so the
        text delivery path drops them and ``TopicHookPool`` retries on a text
        session. This is the voice-specific subset of the broader topic release
        gate: a snapshot held by an in-flight ``trigger_agent_callbacks`` is in
        neither pending queue, so the voice-start sweep can't reach it. Returns
        the number retracted."""
        n = 0
        for cb in callbacks:
            if (
                isinstance(cb, dict)
                and cb.get("channel") == "topic_hook"
                and not cb.get(DELIVERY_RETRACTED_KEY)
            ):
                resolve_callback_delivery_ack(cb, False)
                cb[DELIVERY_RETRACTED_KEY] = True
                n += 1
        return n

    def enqueue_agent_callback(self, callback: dict) -> None:
        """Enqueue a structured agent task callback for LLM injection.

        Text mode: drained before the next stream_text call and injected via
        prompt_ephemeral(), OR proactively via trigger_agent_callbacks().
        Voice mode: also appended to pending_extra_replies for hot-swap
        injection via prime_context().

        Voice queue element shape is structured (not flat text) so the
        hot-swap renderer can:
          1. Pick TASK vs EVENT wrapper from ``origin``.
          2. Recover status / source phrasing when both ``summary`` and
             ``detail`` are empty — typical for failure callbacks where
             the meaning lives in the header (e.g. ``status="failed"`` +
             ``error_message="Connection refused"``).

        ``summary`` and ``detail`` are normalized **independently** (strip
        each, then prefer summary → detail), so a blank-whitespace
        ``summary`` doesn't shadow a real ``detail`` via the legacy
        ``summary or detail`` chain.

        The two queues stay independent (text-mode drain and voice-mode
        hot-swap fire at different lifecycle points).
        """
        try:
            from config import (
                AGENT_CALLBACK_QUEUE_MAX_ITEMS,
            )
            context_source = "topic.hook" if callback.get("channel") == "topic_hook" else "proactive.callback"
            # Per-item input budget: summary/detail flow into the LLM verbatim.
            # Reuse the same source-policy normalizer as append_context() so
            # proactive/topic callbacks do not grow a parallel budget path.
            summary_raw = str(callback.get("summary") or "").strip()
            detail_raw = str(callback.get("detail") or "").strip()
            summary = self._normalize_context_text_for_source(context_source, summary_raw)
            # summary/detail frequently carry the SAME body (proactive_bridge
            # sets both to the aggregated text) — reuse the encode, don't do it
            # twice.
            detail = summary if detail_raw == summary_raw else self._normalize_context_text_for_source(
                context_source,
                detail_raw,
            )
            # Write the capped text back so the text-mode drain (which reads the
            # callback dict directly) injects the truncated body too.
            callback["summary"] = summary
            callback["detail"] = detail
            error_message = str(callback.get("error_message") or "").strip()
            source_name = str(callback.get("source_name") or "").strip()
            status = callback.get("status") or "completed"
            origin = callback.get("origin")
            if origin not in ("task_result", "event"):
                # Fail-safe: missing/unknown origin defaults to event so the
                # hot-swap renderer does not fabricate "我完成了任务" for what
                # may actually be an external event push.
                origin = "event"
            # Skip enqueue (BOTH queues) only when there is *truly* nothing
            # to convey: no body text, no error context, no identifiable
            # source, and a benign completed status. Anything else
            # (failed/cancelled/blocked, an error message, or a named source)
            # carries meaning even with empty summary/detail and must survive
            # into the hot-swap output.
            #
            # The two queues must filter consistently — otherwise text mode
            # (which drains pending_agent_callbacks) would inject a garbage
            # header-only block for callbacks the voice mode already
            # discarded.
            if not summary and not detail and not error_message and not source_name and status == "completed":
                return
            # Stable delivery id so the voice inject success path can
            # precisely drop the matching extras entry from
            # ``pending_extra_replies``. Length-based alignment is unsafe:
            # ``drain_agent_callbacks_for_llm`` clears
            # ``pending_agent_callbacks`` while leaving
            # ``pending_extra_replies`` intact, so the queues legitimately
            # drift apart across user turns.
            delivery_id = callback.setdefault("_callback_delivery_id", uuid4().hex)
            self.pending_agent_callbacks.append(callback)
            self.pending_extra_replies.append({
                "_callback_delivery_id": delivery_id,
                "origin": origin,
                "summary": summary,
                "detail": detail,
                "status": status,
                "context_source": context_source,
                "source_kind": callback.get("source_kind") or "unknown",
                "source_name": source_name,
                "error_message": error_message,
            })
            # Flood guard: a runaway plugin event stream must not grow either
            # queue without bound. Keep the most recent N (newest = most
            # relevant); drop-oldest.
            if len(self.pending_agent_callbacks) > AGENT_CALLBACK_QUEUE_MAX_ITEMS:
                overflow = len(self.pending_agent_callbacks) - AGENT_CALLBACK_QUEUE_MAX_ITEMS
                dropped = self.pending_agent_callbacks[:overflow]
                dropped_ids = {
                    _cb.get("_callback_delivery_id")
                    for _cb in dropped
                    if isinstance(_cb, dict) and _cb.get("_callback_delivery_id")
                }
                self.pending_agent_callbacks = self.pending_agent_callbacks[overflow:]
                # Resolve any delivery-ack future on a dropped callback NOW, so a
                # waiter (e.g. topic-hook delivery) unblocks immediately instead
                # of stalling until its timeout.
                for _cb in dropped:
                    resolve_callback_delivery_ack(_cb, False)
                # Drop the matching voice-queue mirrors by delivery_id (the two
                # queues drift, so positional trimming is unreliable) — otherwise
                # a callback acked False here could still be injected via hot-swap.
                if dropped_ids:
                    self.pending_extra_replies = [
                        _extra for _extra in self.pending_extra_replies
                        if _extra.get("_callback_delivery_id") not in dropped_ids
                    ]
            if len(self.pending_extra_replies) > AGENT_CALLBACK_QUEUE_MAX_ITEMS:
                self.pending_extra_replies = self.pending_extra_replies[-AGENT_CALLBACK_QUEUE_MAX_ITEMS:]
        except Exception:
            pass

    def drain_agent_callbacks_for_llm(self) -> str:
        """Drain pending_agent_callbacks and format as a system context string.

        Clears pending_agent_callbacks (NOT pending_extra_replies, which is
        consumed separately by the voice-mode hot-swap path).
        Returns an empty string if there are no callbacks.

        Renders with the same grouped/source-aware logic as
        :meth:`trigger_agent_callbacks` but in passive mode — so the resulting
        string already includes its own outer header (PASSIVE for delivery
        ``"passive"`` callbacks, PROACTIVE for any "proactive" ones that
        ended up here because the SM denied the claim earlier). The caller
        therefore should NOT prepend an additional notification template.
        """
        self._purge_retracted_agent_callbacks()
        if not self.pending_agent_callbacks:
            return ""
        candidate_callbacks = list(self.pending_agent_callbacks)
        if self._retract_unavailable_topic_hook_snapshots(candidate_callbacks):
            logger.info(
                "[%s] drain_agent_callbacks_for_llm: topic hook dropped before passive drain — delivery gate closed",
                self.lanlan_name,
            )
        self._purge_retracted_agent_callback_extras(candidate_callbacks)
        self._purge_retracted_agent_callbacks()
        active_callbacks = [
            cb for cb in candidate_callbacks
            if not cb.get(DELIVERY_RETRACTED_KEY)
        ]
        if not active_callbacks:
            return ""
        from config import AGENT_CALLBACK_TOTAL_MAX_TOKENS
        # Budget-aware selection: render (and ack) only the callbacks that fit
        # the total budget this turn; defer the rest to the next drain instead
        # of acking them as delivered while their text falls off the cap.
        callbacks_snapshot, deferred = _select_callbacks_within_token_budget(
            active_callbacks, AGENT_CALLBACK_TOTAL_MAX_TOKENS
        )
        delivered_to_prompt = False
        try:
            _lang = normalize_language_code(getattr(self, 'user_language', '') or '', format='short') or get_global_language()
            rendered = _build_callback_instruction(
                callbacks_snapshot,
                lang=_lang,
                lanlan_name=getattr(self, "lanlan_name", "") or "",
                master_name=getattr(self, "master_name", "") or "",
                passive=False,
            )
            delivered_to_prompt = True
            return rendered
        finally:
            if delivered_to_prompt:
                for cb in callbacks_snapshot:
                    resolve_callback_delivery_ack(cb, True)
            # Keep deferred (over-budget) callbacks for the next turn; only the
            # rendered+acked ones leave the queue.
            self.pending_agent_callbacks = deferred

    async def _perform_final_swap_sequence(self):
        """[Hot-swap related] Perform the final swap sequence"""
        logger.info("Final Swap Sequence: Starting...")
        if not self.pending_session:
            logger.error("💥 Final Swap Sequence: Pending session not found. Aborting swap.")
            await self._reset_preparation_state(clear_main_cache=True)  # Reset all flags and cache for clean restart
            self.is_hot_swap_imminent = False
            return
        
        # 检查pending_session的websocket是否有效
        if isinstance(self.pending_session, OmniRealtimeClient):
            if not hasattr(self.pending_session, 'ws') or not self.pending_session.ws:
                logger.error("💥 Final Swap Sequence: Pending session的WebSocket已关闭，放弃swap操作")
                await self._cleanup_pending_session_resources()
                await self._reset_preparation_state(clear_main_cache=True)
                self.is_hot_swap_imminent = False
                return
            
            # 检查是否发生致命错误
            if hasattr(self.pending_session, '_fatal_error_occurred') and self.pending_session._fatal_error_occurred:
                logger.error("💥 Final Swap Sequence: Pending session已发生致命错误，放弃swap操作")
                await self._cleanup_pending_session_resources()
                await self._reset_preparation_state(clear_main_cache=True)
                self.is_hot_swap_imminent = False
                return

        try:
            new_session = None  # 提前初始化，确保 except 块安全访问（实际赋值在 PERFORM ACTUAL HOT SWAP 段）
            old_listener_cancel_timed_out = False  # 旧 listener 取消超时标志，供 except 块做 fail-close 决策
            next_session_context_messages = getattr(self, "next_session_context_messages", []) or []
            incremental_next_session_context = next_session_context_messages[
                self.initial_next_session_context_snapshot_len:
            ]
            incremental_cache = (
                list(incremental_next_session_context)
                + self.message_cache_for_new_session[self.initial_cache_snapshot_len:]
            )
            # 1. Send incremental cache (or a heartbeat) to PENDING session for its *second* ignored response
            if incremental_cache:
                final_prime_text = self._convert_cache_to_str(incremental_cache)
            else:  # Ensure session cycles a turn even if no incremental cache
                final_prime_text = ""  # Initialize to empty string to prevent NameError
                logger.debug(f"🔄 No incremental cache found. 缓存长度: {len(self.message_cache_for_new_session)}, 快照长度: {self.initial_cache_snapshot_len}")

            # 若存在需要植入的额外提示，则指示模型忽略上一条消息，并在下一次响应中统一向用户补充这些提示
            if self.pending_extra_replies and len(self.pending_extra_replies) > 0:
                _lang = normalize_language_code(self.user_language, format='short')
                from config import AGENT_CALLBACK_TOTAL_MAX_TOKENS
                # Budget-aware selection (mirror of the text-mode drain): render
                # only what fits, keep the rest for the next hot-swap rather than
                # dropping it after clearing the queue.
                _selected, _deferred = _select_callbacks_within_token_budget(
                    list(self.pending_extra_replies), AGENT_CALLBACK_TOTAL_MAX_TOKENS
                )
                final_prime_text += _render_pending_extra_replies_by_origin(
                    _selected,
                    lang=_lang,
                    lanlan_name=self.lanlan_name,
                    master_name=self.master_name,
                )
                try:
                    await self.pending_session.prime_context(final_prime_text, skipped=False)
                except (web_exceptions.ConnectionClosed, AttributeError) as e:
                    # pending_session 连接已关闭或websocket为None，放弃整个 swap 操作
                    logger.error(f"💥 Final Swap Sequence: pending_session不可用，放弃swap操作: {e}")
                    await self._cleanup_pending_session_resources()
                    await self._reset_preparation_state(clear_main_cache=True)
                    self.is_hot_swap_imminent = False
                    return
                # 仅在成功注入后才移除已选条目；失败时保留整队列等下一轮 hot-swap
                # （否则 _selected 既没进模型又丢了）。over-budget 的 _deferred 留到下一轮。
                self.pending_extra_replies = _deferred
            else:
                _lang = normalize_language_code(self.user_language, format='short')
                final_prime_text += _loc(CONTEXT_SUMMARY_READY, _lang).format(name=self.lanlan_name, master=self.master_name)
                try:
                    await self.pending_session.prime_context(final_prime_text, skipped=True)
                except (web_exceptions.ConnectionClosed, AttributeError) as e:
                    # pending_session 连接已关闭或websocket为None，放弃整个 swap 操作
                    logger.error(f"💥 Final Swap Sequence: pending_session不可用，放弃swap操作: {e}")
                    await self._cleanup_pending_session_resources()
                    await self._reset_preparation_state(clear_main_cache=True)
                    self.is_hot_swap_imminent = False
                    return

            print(final_prime_text) #只在控制台显示，不输出到日志文件

            # 2. Start temporary listener for PENDING session's *second* ignored response
            if self.pending_session_final_prime_complete_event:
                self.pending_session_final_prime_complete_event.set()

            # --- PERFORM ACTUAL HOT SWAP ---
            logger.info("Final Swap Sequence: Starting actual session swap...")
            old_main_session = self.session
            old_main_message_handler_task = self.message_handler_task
            # 立即用局部变量持有新 session，并清空 self.pending_session。
            # 必须在任何 await 之前完成：后续 cancel/close 的 await 若触发
            # CancelledError，异常处理器会调 _cleanup_pending_session_resources()，
            # 它检查 self.pending_session；若不提前清零，会把新 session 的 ws 关掉。
            new_session = self.pending_session
            self.pending_session = None

            # ── 步骤 1：先停旧 listener ────────────────────────────────────────────
            # 必须在 old_main_session.close() 之前完成：ws.close() 内部执行关闭握手
            # （等待服务端 CLOSE 帧），本质上是一次 recv()。若旧 task 仍在
            # async for 的 recv() 中，就会产生
            # "cannot call recv while another coroutine is already running recv" 并发冲突。
            if old_main_message_handler_task and not old_main_message_handler_task.done():
                old_main_message_handler_task.cancel()
                try:
                    await asyncio.wait_for(old_main_message_handler_task, timeout=2.0)
                    logger.info("Final Swap Sequence: Old message handler task stopped")
                except asyncio.TimeoutError:
                    # 旧 task 仍占着 recv()，继续往下 close() 会重演并发 recv 冲突。
                    # 关闭 new_session 防止 ws 泄漏，标记超时后中止 swap。
                    old_listener_cancel_timed_out = True
                    logger.error("Final Swap Sequence: 旧 listener 取消超时，中止热切换")
                    try:
                        await new_session.close()
                    except Exception as _e:
                        logger.debug(f"Final Swap Sequence: 超时中止时关闭 new_session 失败（可忽略）: {_e}")
                    raise RuntimeError("旧 listener 取消超时，热切换中止")
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    logger.warning(f"Final Swap Sequence: Old task exited with error: {e}")

            # ── 步骤 2：旧 task 已停，安全关闭旧 session ─────────────────────────
            if old_main_session:
                try:
                    await old_main_session.close()
                except Exception as e:
                    logger.error(f"💥 Final Swap Sequence: Error closing old session: {e}")

            # ── 步骤 3：promote 新 session ────────────────────────────────────────
            # 旧 listener 已停、旧 session 已关，现在切换 self.session；
            # 此后旧 task 的任何回调若再执行也已看不到旧 ws。
            self.session = new_session
            self._require_context_append_current_delivery = True
            next_context_count_at_promote = len(self._snapshot_next_session_context_messages())
            await self._apply_pending_tts_route_after_swap()
            self.current_speech_id = str(uuid4())
            self._tts_done_queued_for_turn = False
            self._tts_done_pending_until_ready = False
            self.session_start_time = datetime.now()
            self._session_turn_count = 0

            # promote 之后立刻把 registry 最新状态推过去 —— swap 序列里
            # ``self.pending_session → 局部 new_session → self.session``
            # 跨了几个 await，期间 register_tool 触发的 _sync 可能既赶不上
            # pending_session（已被挪走置 None）也赶不上 self.session
            # （还没赋值），导致 promote 后新 session 缺了那次注册的工具。
            try:
                await self._sync_tools_to_active_session()
            except Exception as _sync_err:
                logger.warning("⚠️ final swap post-promote tool sync failed: %s", _sync_err)

            # 验证新session的WebSocket是否仍然有效（可能在swap过程中被服务器断开）
            if isinstance(self.session, OmniRealtimeClient) and not self.session.ws:
                # 旧session已关闭无法回滚，抛出异常让 except 块走重建流程
                raise RuntimeError("新session的WebSocket在swap后已失效，热切换失败")

            transferred_next_context_count = (
                self.initial_next_session_context_snapshot_len
                + len(incremental_next_session_context)
            )
            consumed_next_context_count = await self._prime_late_next_session_context_after_swap(
                transferred_next_context_count,
                next_context_count_at_promote,
            )

            # ── 步骤 4：启动新 listener ───────────────────────────────────────────
            if self.session and hasattr(self.session, 'handle_messages'):
                self.message_handler_task = asyncio.create_task(self.session.handle_messages())

            # ── 步骤 5：flush 热切换音频缓存到新 session ─────────────────────────
            # 必须在 promote 之后调用：_flush_hot_swap_audio_cache 使用 self.session
            # 发送音频，此时 self.session 已是新 session，音频会正确发往新会话。
            await self._flush_hot_swap_audio_cache()
            self._consume_next_session_context_messages(consumed_next_context_count)

            # Reset all preparation states and clear the *main* cache now that it's fully transferred
            # pending_session已在swap后立即清除，这里只需要重置其他状态
            await self._reset_preparation_state(
                clear_main_cache=True, from_final_swap=True)  # This will clear pending_*, is_preparing_new_session, etc. and self.message_cache_for_new_session
            logger.info("✅ 热切换完成")
            

        except asyncio.CancelledError:
            logger.info("Final Swap Sequence: Task cancelled.")
            self.is_hot_swap_imminent = False
            # new_session 在 self.pending_session = None 后由局部变量持有。
            # 若 swap 在 promote 之前被取消，_cleanup_pending_session_resources 不再持有它，
            # 必须在此手动关闭，防止 ws 泄漏。
            if new_session is not None and new_session is not self.session:
                try:
                    await new_session.close()
                except Exception as _e:
                    logger.debug(f"Final Swap Sequence: CancelledError 路径关闭 new_session 失败（可忽略）: {_e}")
            await self._cleanup_pending_session_resources()
            await self._reset_preparation_state(clear_main_cache=True)
            if self.is_active and self.session and hasattr(self.session, 'handle_messages') and (not self.message_handler_task or self.message_handler_task.done()):
                self.message_handler_task = asyncio.create_task(self.session.handle_messages())

        except Exception as e:
            logger.error(f"💥 Final Swap Sequence: Error: {e}")
            self.is_hot_swap_imminent = False
            await self.send_status(json.dumps({"code": "INTERNAL_UPDATE_FAILED", "details": {"error": str(e)}}))
            # 同上：new_session 若未完成 promote，需手动关闭防 ws 泄漏。
            if new_session is not None and new_session is not self.session:
                try:
                    await new_session.close()
                except Exception as _e:
                    logger.debug(f"Final Swap Sequence: 异常路径关闭 new_session 失败（可忽略）: {_e}")
            await self._cleanup_pending_session_resources()
            await self._reset_preparation_state(clear_main_cache=True)
            if old_listener_cancel_timed_out:
                # 旧 listener 取消超时：旧 task 可能在本函数返回后才真正退出，
                # 此时无法安全判断 task.done() 并补建 listener，会留下"活跃但无监听"状态。
                # 直接 fail-close：清除会话状态让前端重连，优于让后续输入陷入僵局。
                self.session = None
                self.message_handler_task = None
                self.is_active = False
                return
            # 若 self.session 的 ws 已失效（promote 后 ws invalid），清除会话状态，
            # 防止 is_active=True + ws=None 让后续输入进入坏会话。
            if self.session and isinstance(self.session, OmniRealtimeClient) and not self.session.ws:
                self.session = None
                self.is_active = False
            if self.is_active and self.session and hasattr(self.session, 'handle_messages') and (not self.message_handler_task or self.message_handler_task.done()):
                self.message_handler_task = asyncio.create_task(self.session.handle_messages())
        finally:
            self.is_hot_swap_imminent = False  # Always reset this flag
            if self.final_swap_task and self.final_swap_task.done():
                self.final_swap_task = None

    async def disconnected_by_server(self, *, expected_session=None):
        if expected_session is not None and expected_session is not self.session:
            logger.info("⏭️ disconnected_by_server: expected_session stale, skipping")
            return
        await self.send_status(json.dumps({"code": "CHARACTER_DISCONNECTED", "details": {"name": self.lanlan_name}}))
        await self.send_session_ended_by_server()
        self.sync_message_queue.put({'type': 'system', 'data': 'API server disconnected'})
        await self.cleanup(expected_session=expected_session)
    
    async def stream_data(self, message: dict):  # 向Core API发送Media数据
        if message.get("input_type") == "audio":
            await self._enqueue_audio_stream_data(message)
            return
        await self._stream_data_now(message)

    async def _stream_data_now(self, message: dict):
        input_type = message.get("input_type")
        # 检查session是否就绪
        async with self.input_cache_lock:
            if not self.session_ready:
                # 检查是否正在启动session - 只有在启动过程中才缓存
                if self._starting_session_count > 0:
                    if input_type == "audio":
                        return
                    # Session正在启动中，缓存输入数据
                    self.pending_input_data.append(message)
                    if len(self.pending_input_data) == 1:
                        logger.info("Session正在启动中，开始缓存输入数据...")
                    else:
                        logger.debug(f"继续缓存输入数据 (总计: {len(self.pending_input_data)} 条)...")
                    return

        # 在锁外检查是否需要创建新session（不要在锁内创建session，避免死锁）
        if not self.session_ready and self._starting_session_count == 0:
            if not self.session or not self.is_active:
                # Memory Server 专属冷却检查
                if self._emit_cooldown_turn_end_if_needed():
                    return
                # 熔断早退：start_session 内部也会拦，但这里再加一层省掉
                # 每个音频包的"自动创建 session" info 日志，避免日志洪水。
                if self._session_start_circuit_open:
                    return
                logger.info(f"Session未就绪且不存在，根据输入类型 {input_type} 自动创建 session")
                # 根据输入类型确定模式
                mode = 'text' if input_type in _TEXT_SESSION_INPUT_TYPES else 'audio'
                await self.start_session(self.websocket, new=False, input_mode=mode)

                # 检查启动是否成功
                if not self.session or not self.is_active:
                    logger.warning("⚠️ Session启动失败，放弃本次数据流")
                    return
        
        # Session已就绪，直接处理
        await self._process_stream_data_internal(message)
    
    async def _process_stream_data_internal(self, message: dict):
        """Internal method: the actual stream_data processing logic"""
        data = message.get("data")
        input_type = message.get("input_type")
        # 检查session是否发生致命错误（如1011错误、Response timeout）
        if self.session and isinstance(self.session, OmniRealtimeClient):
            if hasattr(self.session, '_fatal_error_occurred') and self.session._fatal_error_occurred:
                logger.warning("⚠️ Session已发生致命错误，忽略新的输入数据")
                return
        
        # 如果正在启动session，这不应该发生（因为stream_data已经检查过了）
        if self._starting_session_count > 0:
            logger.debug("Session正在启动中，跳过...")
            return

        # 如果 session 不存在或不活跃，检查是否可以自动重建
        if not self.session or not self.is_active:
            # Memory Server 专属冷却检查
            if self._emit_cooldown_turn_end_if_needed():
                return
            # 失败上限保护：start_session 内部熔断会早退，这里再加一层是为了
            # 不让 stream 路径每个包都打"Session 不存在"info 日志，省日志开销。
            if self._session_start_circuit_open:
                return

            logger.info(f"Session 不存在或未激活，根据输入类型 {input_type} 自动创建 session")
            # 检查WebSocket状态
            ws_exists = self.websocket is not None
            if ws_exists:
                has_state = hasattr(self.websocket, 'client_state')
                if has_state:
                    logger.info(f"  └─ WebSocket状态: exists=True, state={self.websocket.client_state}")
                    # 进一步检查连接状态
                    if self.websocket.client_state != self.websocket.client_state.CONNECTED:
                        logger.error(f"  └─ WebSocket未连接，状态: {self.websocket.client_state}")
                        self.sync_message_queue.put({'type': 'system', 'data': 'websocket disconnected'})
                        return
                else:
                    logger.warning("  └─ WebSocket状态: exists=True, 但没有client_state属性!")
            else:
                logger.error("  └─ WebSocket状态: exists=False! 连接可能已断开，请刷新页面")
                # 通过sync_message_queue发送错误提示
                self.sync_message_queue.put({'type': 'system', 'data': 'websocket disconnected'})
                return
            
            # 根据输入类型确定模式
            mode = 'text' if input_type in _TEXT_SESSION_INPUT_TYPES else 'audio'
            await self.start_session(self.websocket, new=False, input_mode=mode)
            
            # 检查启动是否成功
            if not self.session or not self.is_active:
                logger.warning("⚠️ Session启动失败，放弃本次数据流")
                return
        
        try:
            if input_type == 'text':
                # 文本模式：检查 session 类型是否正确
                if not isinstance(self.session, OmniOfflineClient):
                    # 检查是否允许重建session
                    if self.session_start_failure_count >= self.session_start_max_failures:
                        logger.error("💥 Session类型不匹配，但失败次数过多，已停止自动重建")
                        return
                    
                    logger.info(f"文本模式需要 OmniOfflineClient，但当前是 {type(self.session).__name__}. 自动重建 session。")
                    # 占用 _starting_session_count guard 跨过 end_session 窗口期。
                    # 默认 end_session(reset_starting_count=True) 会把 guard 清零；
                    # 它内部又有多个 await 拆 session，期间另一条 _stream_data_now
                    # （比如 audio worker 拉到下一包）看到 session=None / count=0 会
                    # 从 4941-4953 的 auto-create 分支抢跑 start_session(audio)，
                    # 等本路径走到 await self.start_session(text) 时命中 2776 的
                    # "Session正在启动中" guard 被静默忽略，重建静默失败
                    # （ERROR "💥 文本模式Session重建失败"）。
                    #
                    # 同时把 session_ready 提前置 False，与 start_session 2867-2868
                    # 的初始化对偶：rebuild 期间若 session_ready 仍是 True，并发
                    # _stream_data_now 跳过 4926-4938 的 cache 分支（条件为
                    # not session_ready），落到 _process_stream_data_internal 后
                    # 命中 4975 的 count>0 早退被 silent drop——用户在 rebuild
                    # 窗口内打的字直接丢失。提前置 False 让 cache 路径接住，
                    # rebuild 完成后 _flush_pending_input_data 会 flush 出去。
                    async with self.input_cache_lock:
                        self.session_ready = False
                    self._starting_session_count += 1
                    self._starting_input_mode = 'text'
                    try:
                        if self.session:
                            await self.end_session(reset_starting_count=False)
                    finally:
                        self._starting_session_count = max(0, self._starting_session_count - 1)
                        if self._starting_session_count == 0:
                            self._starting_input_mode = None
                    # 释放 guard 与下面的 start_session 之间禁止 await，否则窗口
                    # 重新打开。start_session 入口的 +=1 (2781) 之前都是同步代码，
                    # 函数调用本身不让出控制权，安全。
                    await self.start_session(self.websocket, new=False, input_mode='text')

                    # 检查重建是否成功
                    if not self.session or not self.is_active or not isinstance(self.session, OmniOfflineClient):
                        logger.error("💥 文本模式Session重建失败，放弃本次数据流")
                        return
                
                # 文本模式：直接发送文本
                if isinstance(data, str):
                    memory_text = self._clean_frontend_memory_text(message.get("memory_text"))
                    record_data = memory_text or data
                    # 更新用户活动时间戳（与 handle_input_transcript / _record_external_user_input
                    # 对偶）。idle reset loop 依赖该字段判断静默时长，文本路径不补的话
                    # 纯文本会话永远满足"静默 ≥ 30 min"被误重置。
                    self.last_user_activity_time = time.time()
                    # 「真消息」时间戳：strip 后非空才刷，与语音路径
                    # `if transcript_text:` 对偶——空白输入不算真实回应，否则会误
                    # 推进 mini-game 邀请隐式 dismiss 判定（CodeRabbit）。注意
                    # last_user_activity_time 仍无条件刷（服务 idle reset，语义是
                    # 「有没有发请求」，与「是不是真消息」不同）。
                    if record_data.strip():
                        self.last_user_message_time = time.time()

                    # 更新字数限制（可能用户在对话期间修改了设置）
                    if hasattr(self.session, 'update_max_response_length'):
                        self.session.update_max_response_length(self._get_text_guard_max_length())

                    # 先打断当前正在播放的语音（旧speech_id），避免误打断新回复
                    async with self.lock:
                        interrupted_speech_id = self.current_speech_id

                    self.audio_resampler.clear()
                    await self._clear_tts_pipeline()
                    await self.send_user_activity(interrupted_speech_id)

                    # 再为本次新回复生成新的speech_id（用于TTS和lipsync）
                    async with self.lock:
                        self.current_speech_id = str(uuid4())
                        self._tts_done_queued_for_turn = False
                        self._tts_done_pending_until_ready = False
                        new_user_sid = self.current_speech_id
                        # 与 handle_new_message 同理：sid 写入的同一锁段内同步翻
                        # _preempted，避免 prepare_proactive_delivery 插到 lock
                        # 释放 ~ fire() 之间再覆盖新 user sid。
                        self.state.mark_user_input_preempt()
                    # 状态机：文本模式 stream_text 入口同样需要发射 USER_INPUT。
                    # handle_new_message 只在语音模式走到，这里是文本模式的对偶。
                    await self.state.fire(SessionEvent.USER_INPUT, sid=new_user_sid)
                    # Activity tracker：文本模式真实用户输入。故意不在 handle_new_message
                    # 里挂——后者也被 proactive abort 流程调用做清理（见
                    # main_routers/system_router.py），那不算用户活动。
                    # text 进 buffer 给 emotion-tier 用。
                    self._note_user_turn(text=record_data)
                    # Telemetry：D1 漏斗——本进程首条用户消息（lazy import 防循环）。
                    try:
                        from utils.token_tracker import TokenTracker as _TT
                        _tt = _TT.get_instance()
                        _tt.note_first_user_message("text")
                        # 每条用户消息：user_message_sent counter + 累加 per-session 轮数。
                        # 此处是文本侧 on_user_message 唯一入口，每条真实消息恰好一次。
                        _tt.note_user_message("text")
                    except Exception:
                        # 埋点 best-effort，绝不阻塞用户消息处理；note_first_user_message
                        # 自身幂等，丢一次也不影响 D1 漏斗统计。
                        pass
                    # 与 on_user_message 对偶：把"用户原话"推到插件总线 user-context
                    # bucket。语音路径在 handle_input_transcript 里发布，这里只覆盖
                    # 文本路径，避免与语音入口重复发布。
                    self._publish_user_utterance_to_plugin_bus(
                        record_data,
                        is_voice_source=False,
                    )

                    # Mini-game 邀请的关键词文本兜底（PR #1141 follow-up E2）。
                    # 用户在 pending 邀请期间自己打字（没点 ChoicePrompt 三按钮）
                    # → 扫关键词命中就触发对应 state 转换。与语音转写路径
                    # （handle_input_transcript）共用同一方法，逻辑见
                    # _dispatch_mini_game_invite_keyword。
                    await self._dispatch_mini_game_invite_keyword(
                        record_data,
                    )

                    openclaw_magic_command = self._normalize_explicit_openclaw_magic_command(data)
                    if (
                        openclaw_magic_command
                        and self._is_agent_enabled()
                        and self.agent_flags.get("openclaw_enabled", False)
                        and self.agent_flags.get("openclaw_ready", False)
                    ):
                        self._session_turn_count += 1
                        self._clear_text_pending_images()
                        self._mark_magic_command_image_drop_request(message.get("request_id"))
                        await self.mirror_user_input(
                            data,
                            metadata={
                                "source": "openclaw",
                                "kind": "magic_command",
                                "command": openclaw_magic_command,
                            },
                            request_id=message.get("request_id"),
                        )
                        await self._emit_agent_callback_turn_end(message.get("request_id"))
                        self._fire_task(self._publish_openclaw_magic_command(openclaw_magic_command))
                        logger.info("[%s] text input sent explicit openclaw magic command", self.lanlan_name)
                        return

                    # 文本模式：把挂起的 agent 任务回调**就地拼到本轮 user
                    # message 的 content 前缀**——LLM 把它当作"用户当前发声那
                    # 一刻附带的额外上下文"，在同一轮回答里自然提及，不再起
                    # 独立 turn（issue #1033）。drain 出来的字符串已含
                    # ``======[系统通知] 来自xxx的xxx======`` watermark，LLM
                    # 看得出来是 system notice 而不是用户原话。
                    #
                    # 与 voice mode 的对偶：``prime_context(skipped=False)`` 在
                    # GPT/GLM/Step 上同样走 ``create_response`` 把 callback
                    # 注入成 user role 消息，offline 这边 inline 进 user
                    # content 跟那条路径语义一致——callback 文本随 user message
                    # 进 transcript 持久化（issue 旧注释里担忧的"持久化污染"作
                    # 废，passive callback 跟用户输入一起留在 history 让 AI
                    # 后续仍能 reference）。
                    #
                    # best-effort 注入：drain 的 ``finally clear`` 是 PR #1032
                    # 的设计决定（passive=单次软通知），即便 drain 或 stream_text
                    # 失败也不回填——延续到这条路径仍是这样，不在 caller 加
                    # snapshot 回滚。
                    _agent_cb_ctx = ""
                    if self.pending_agent_callbacks:
                        try:
                            _agent_cb_ctx = self.drain_agent_callbacks_for_llm() or ""
                        except Exception as _cb_err:
                            logger.warning(f"⚠️ Agent callback drain failed: {_cb_err}")
                            _agent_cb_ctx = ""

                    self._active_text_request_id = message.get("request_id")
                    # Path A (inline) Focus 凝神：score this user message and, if
                    # over the bar, run THIS reply thinking-on. Scored on
                    # ``record_data`` (= memory_text or data) — the user-VISIBLE
                    # text that also feeds the activity tracker / cadence baseline
                    # and history replacement. Scoring raw ``data`` instead would
                    # read a hidden scaffold prompt (e.g. avatar-drop file
                    # contents) the user never typed, mismatching the cadence
                    # signal and entering Focus on evidence the user didn't author.
                    _focus_thinking = await self._focus_inline_decision(record_data)
                    input_transcript_callback = None
                    if memory_text:
                        async def input_transcript_callback(_transcript: str, *, _memory_text: str = memory_text) -> None:
                            await self.handle_input_transcript(_memory_text, is_voice_source=False)

                    stream_text_kwargs = {
                        "system_prefix": _agent_cb_ctx or None,
                        "thinking_on": _focus_thinking,
                    }
                    if input_transcript_callback:
                        stream_text_kwargs["input_transcript_callback"] = input_transcript_callback
                    if memory_text:
                        stream_text_kwargs["history_replacement_text"] = memory_text
                    if _focus_thinking:
                        # 凝神 turn runs thinking-on: pre-pulse the frontend so the
                        # bubble shows up the instant the turn starts (immediate
                        # feedback), before any reasoning chunk arrives. Idempotent
                        # and harmless — a non-Focus turn instead pulses lazily from
                        # OmniOfflineClient.on_thinking_active on its first reasoning
                        # chunk (handle_thinking_active). Either way the bubble clears
                        # on the first visible token (send_lanlan_response) or in the
                        # unconditional finally below.
                        await self._push_focus_thinking(True)
                    try:
                        await self.session.stream_text(data, **stream_text_kwargs)
                    finally:
                        # Clear unconditionally: a non-Focus turn may have pulsed the
                        # bubble True via the reasoning callback, so gating the clear
                        # on _focus_thinking would leave it stuck on tool-only / empty
                        # / error turns. _push_focus_thinking is idempotent, so a no-op
                        # clear when nothing pulsed costs nothing.
                        await self._push_focus_thinking(False)
                else:
                    logger.error(f"💥 Stream: Invalid text data type: {type(data)}")
                return
            
            # Audio输入：只有OmniRealtimeClient能处理
            if input_type == 'audio':
                # 检查 session 类型
                if not isinstance(self.session, OmniRealtimeClient):
                    # 检查是否允许重建session
                    if self.session_start_failure_count >= self.session_start_max_failures:
                        logger.error("💥 Session类型不匹配，但失败次数过多，已停止自动重建")
                        return
                    
                    logger.info(f"语音模式需要 OmniRealtimeClient，但当前是 {type(self.session).__name__}. 自动重建 session。")
                    # 与上面 text 重建路径对偶：先置 session_ready=False 让 cache
                    # 路径接住窗口期内的输入，再占用 guard 跨过 end_session，防止
                    # 并发 _stream_data_now 抢跑 start_session(text) 造成本路径
                    # 命中 2776 guard 静默失败（ERROR "💥 语音模式Session重建失败"）
                    # 或落到 _process_stream_data_internal 4975 早退被 silent drop。
                    async with self.input_cache_lock:
                        self.session_ready = False
                    self._starting_session_count += 1
                    self._starting_input_mode = 'audio'
                    try:
                        if self.session:
                            await self.end_session(reset_starting_count=False)
                    finally:
                        self._starting_session_count = max(0, self._starting_session_count - 1)
                        if self._starting_session_count == 0:
                            self._starting_input_mode = None
                    await self.start_session(self.websocket, new=False, input_mode='audio')

                    # 检查重建是否成功
                    if not self.session or not self.is_active or not isinstance(self.session, OmniRealtimeClient):
                        logger.error("💥 语音模式Session重建失败，放弃本次数据流")
                        return
                
                # 检查WebSocket连接
                session_ref = self.session
                audio_epoch = self._audio_stream_epoch
                if not hasattr(session_ref, 'ws') or not session_ref.ws:
                    logger.error("💥 Stream: Session websocket not available")
                    return
                try:
                    if isinstance(data, list):
                        audio_bytes = struct.pack(f'<{len(data)}h', *data)
                        
                        # 🔧 音频预处理：RNNoise降噪 + 降采样到16kHz（在缓存之前）
                        # 检查是否为48kHz输入（480 samples = 960 bytes per 10ms chunk）
                        num_samples = len(audio_bytes) // 2
                        is_48khz = (num_samples == 480)
                        
                        processed_audio = audio_bytes  # 默认使用原始音频
                        if is_48khz and isinstance(session_ref, OmniRealtimeClient):
                            # 使用session的AudioProcessor处理音频
                            if hasattr(session_ref, '_audio_processor') and session_ref._audio_processor:
                                try:
                                    # Use async wrapper to avoid blocking main loop
                                    if hasattr(session_ref, 'process_audio_chunk_async'):
                                        processed_audio = await session_ref.process_audio_chunk_async(audio_bytes)
                                    else:
                                        # Fallback (should not happen if client updated)
                                        processed_audio = session_ref._audio_processor.process_chunk(audio_bytes)
                                        
                                    # RNNoise可能返回空字节（缓冲中），跳过
                                    if len(processed_audio) == 0:
                                        return
                                except Exception as e:
                                    logger.error(f"💥 音频预处理失败: {e}")
                                    return
                        if (
                            self.session is not session_ref
                            or not self.is_active
                            or self._audio_stream_epoch != audio_epoch
                        ):
                            return
                        
                        # 热切换期间或推送缓存期间，缓存处理后的音频（16kHz，已降噪）
                        if self.is_hot_swap_imminent or self.is_flushing_hot_swap_cache:
                            async with self.hot_swap_cache_lock:
                                self.hot_swap_audio_cache.append(processed_audio)
                                if len(self.hot_swap_audio_cache) == 1:
                                    logger.info("🔄 热切换进行中，开始缓存处理后的音频（16kHz）...")
                            return
                        
                        # 检查session是否被服务器关闭（防刷屏）
                        if self.session_closed_by_server:
                            return  # 静默拒绝，不记录log
                        
                        # 再次检查session状态（防止在处理过程中session被关闭）
                        if not session_ref or not hasattr(session_ref, 'ws') or not session_ref.ws:
                            # 限流log：2秒内只记录一次
                            current_time = asyncio.get_event_loop().time()
                            if current_time - self.last_audio_send_error_time > self.audio_error_log_interval:
                                logger.warning("⚠️ Session已关闭，跳过音频数据发送")
                                self.last_audio_send_error_time = current_time
                            return
                        
                        # 检查致命错误状态
                        if hasattr(session_ref, '_fatal_error_occurred') and session_ref._fatal_error_occurred:
                            current_time = asyncio.get_event_loop().time()
                            if current_time - self.last_audio_send_error_time > self.audio_error_log_interval:
                                logger.warning("⚠️ Session已发生致命错误，跳过音频数据发送")
                                self.last_audio_send_error_time = current_time
                            return
                        
                        # 发送音频到session（stream_audio会检测是否48kHz，16kHz不会再处理）
                        await session_ref.stream_audio(processed_audio)
                    else:
                        logger.error(f"💥 Stream: Invalid audio data type: {type(data)}")
                        return

                except struct.error as se:
                    logger.error(f"💥 Stream: Struct packing error (audio): {se}")
                    return
                except web_exceptions.ConnectionClosedOK:
                    self.session_closed_by_server = True  # 标记连接已关闭
                    return
                except AttributeError as ae:
                    # 捕获 'NoneType' object has no attribute 'send' 等错误
                    self.session_closed_by_server = True
                    current_time = asyncio.get_event_loop().time()
                    if current_time - self.last_audio_send_error_time > self.audio_error_log_interval:
                        logger.error(f"💥 Stream: Session已关闭或不可用: {ae}")
                        self.last_audio_send_error_time = current_time
                    return
                except Exception as e:
                    # 检测连接关闭错误
                    error_str = str(e)
                    if 'no close frame' in error_str or 'Connection closed' in error_str:
                        self.session_closed_by_server = True
                    
                    # 限流log
                    current_time = asyncio.get_event_loop().time()
                    if current_time - self.last_audio_send_error_time > self.audio_error_log_interval:
                        logger.error(f"💥 Stream: Error processing audio data: {e}")
                        self.last_audio_send_error_time = current_time
                    return

            elif input_type in _IMAGE_INPUT_TYPES:
                try:
                    if self._should_drop_magic_command_image(message.get("request_id")):
                        return
                    # 使用统一的图像工具处理数据（只验证，不缩放）
                    image_b64 = await process_screen_data(data)

                    if image_b64:
                        # 叠加 Avatar 文字注解（仅当本条消息携带了位置元数据时）
                        # 不回退到 self._avatar_position：前端未附带位置说明该截图不应叠加
                        # （如窗口截图、手机相机等场景）
                        av_pos = message.get('avatar_position') if input_type in {"screen", "camera"} else None
                        if av_pos and isinstance(av_pos, dict):
                            try:
                                image_b64 = await asyncio.to_thread(
                                    overlay_avatar_annotation,
                                    image_b64, av_pos, self.lanlan_name,
                                    get_global_language_full(),
                                )
                            except Exception as ann_err:
                                logger.warning("[%s] avatar annotation failed, sending original: %s",
                                               self.lanlan_name, ann_err)

                        # 如果是文本模式（OmniOfflineClient），只存储图片，不立即发送
                        if isinstance(self.session, OmniOfflineClient):
                            # 只添加到待发送队列，等待与文本一起发送
                            await self.session.stream_image(image_b64)
                            image_data = (
                                ""
                                if input_type in {"avatar_drop_image", "user_image"}
                                else f"data:image/jpeg;base64,{image_b64}"
                            )
                            image_message = {
                                "input_type": input_type,
                                "data": image_data,
                                "has_image": True,
                                "mime_type": "image/jpeg",
                            }
                            if message.get("request_id"):
                                image_message["request_id"] = message.get("request_id")
                            self.sync_message_queue.put({
                                "type": "user",
                                "data": image_message,
                            })

                        # 如果是语音模式（OmniRealtimeClient），检查是否支持视觉并直接发送
                        elif isinstance(self.session, OmniRealtimeClient):
                            # 检查WebSocket连接
                            if not hasattr(self.session, 'ws') or not self.session.ws:
                                logger.error("💥 Stream: Session websocket not available")
                                return

                            # 语音模式直接发送图片
                            await self.session.stream_image(image_b64)
                    else:
                        logger.error("💥 Stream: 图像数据验证失败")
                        return
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.error(f"💥 Stream: Error processing image data: {e}")
                    return

        except web_exceptions.ConnectionClosedError as e:
            logger.error(f"💥 Stream: Error sending data to session: {e}")
            if '1011' in str(e):
                await self.send_status(json.dumps({"code": "ERROR_1011_MIC_CHECK"}))
            if '1007' in str(e):
                await self.send_status(json.dumps({"code": "ERROR_1007_ARREARS"}))
            await self.disconnected_by_server()
            return
        except Exception as e:
            error_message = f"Stream: Error sending data to session: {e}"
            logger.error(f"💥 {error_message}")
            await self.send_status(json.dumps({"code": "API_UNKNOWN_ERROR", "details": {"msg": error_message}}))

    async def end_session(self, by_server=False, *, expected_session=None, reset_starting_count=True):  # 与Core API断开连接
        # 「用户/前端主动结束启动」信号：只有前端发来的 end_session / pause_session
        # （by_server=False 且 reset_starting_count=True，见 websocket_router）才计。
        # 内部 recovery（reset_starting_count=False）与各类 by_server=True cleanup
        # 不算，避免把 in-flight 启动失败误判成"用户放弃"而误杀跨模式重启
        # （见 start_session 跨模式分支的 _user_session_abandon_epoch 守卫）。放在所有
        # 早退之前，确保 in-flight（尚未 active）期间前端 end_session 也能计上。
        if not by_server and reset_starting_count:
            self._user_session_abandon_epoch += 1
        # Pre-check: no-side-effect guard before _init_renew_status which mutates
        # pending/prewarm state.  A stale callback must not nuke preparation state.
        _inactive_early = False
        async with self.lock:
            if not self.is_active:
                # Stale-session guard: 即使未激活，也要确认不是过期回调，
                # 否则会误清理新 session 正在创建的 TTS 资源
                if expected_session is not None and expected_session is not self.session:
                    logger.info("⏭️ end_session: expected_session stale (inactive-early), skipping")
                    return
                # 即使会话未完全激活（如 start_session 失败），也要清理
                # 可能残留的 TTS 重试状态，防止污染下一次会话
                self._reset_tts_retry_state()
                self._audio_stream_epoch += 1
                self._clear_audio_stream_queue("end_session_inactive")
                self._cancel_audio_stream_worker("end_session_inactive")
                self._reset_voice_echo_suppression_cache()
                _inactive_early = True
                # start_tts_if_needed 可能已启动 TTS 线程/handler，
                # 但 is_active 尚未置 True 就失败了——快照引用以便释放锁后清理
                _orphan_tts_handler = self.tts_handler_task
                _orphan_tts_thread = self.tts_thread
                _orphan_tts_rq = self.tts_request_queue
                _orphan_tts_rsq = self.tts_response_queue
            elif expected_session is not None and expected_session is not self.session:
                logger.info("⏭️ end_session: expected_session stale (pre-check), skipping")
                return
            else:
                # 尽早取消 TTS 延迟重试任务并清理错误码（持锁状态下），
                # 防止 _init_renew_status 期间 respawn task 触发无效重试
                self._reset_tts_retry_state()

        # Clear the playback gate + manager queue on genuine teardown. Placed
        # AFTER the stale-session guards above (which `return` early) so a stale/
        # duplicate end_session callback can't reset the CURRENT live session's
        # gate or drop its queued cues (Codex P1).
        self._reset_proactive_gate()

        if _inactive_early:
            if reset_starting_count:
                # 前端启动超时会在 session 尚未 active 时发送 end_session。
                # 旧输入缓存必须在释放 start_session guard 之前清掉；释放后
                # 新一轮启动可能已经开始缓存用户消息，旧收尾不能再碰它们。
                async with self.input_cache_lock:
                    self.session_ready = False
                    self.pending_input_data.clear()
                    self._clear_pending_context_appends()
                async with self.lock:
                    if expected_session is None or expected_session is self.session:
                        self._starting_session_count = 0
                        self._starting_input_mode = None
            # start_tts_if_needed 可能已启动 TTS 但 is_active 未置 True（如 LLM 启动失败），
            # 必须清理这些孤儿资源，否则线程/task 会泄漏
            await self._teardown_tts_runtime(
                _orphan_tts_handler, _orphan_tts_thread,
                _orphan_tts_rq, _orphan_tts_rsq)
            return

        await self._init_renew_status()

        async with self.lock:
            # Re-check after await: another task may have deactivated or swapped session.
            if not self.is_active:
                self._audio_stream_epoch += 1
                self._clear_audio_stream_queue("end_session_post_init_inactive")
                self._cancel_audio_stream_worker("end_session_post_init_inactive")
                self._reset_voice_echo_suppression_cache()
                return
            if expected_session is not None and expected_session is not self.session:
                logger.info("⏭️ end_session: expected_session stale (post-init), skipping")
                return
            self.is_active = False
            # 重置 _starting_session_count：如果 start_session 正在执行中（比如卡在预热），
            # 前端超时后发来 end_session，必须解除这个 guard，否则用户手动重试会被
            # 静默丢弃（_starting_session_count>0 → return），导致"必须重启应用才能恢复"。
            # 但 start_session 内部自己调 end_session 清理旧 session 时必须传
            # reset_starting_count=False，否则 guard 被清零后并发的第二次 start_session
            # 会穿过，产生孤儿 OmniRealtimeClient（silence_check_task/ws 泄漏）。
            if reset_starting_count:
                self._starting_session_count = 0
                self._starting_input_mode = None
            self._audio_stream_epoch += 1
            self._clear_audio_stream_queue("end_session")
            self._cancel_audio_stream_worker("end_session")
            self._reset_voice_echo_suppression_cache()

            # Activity tracker：session 关闭，voice_engaged 不再可能触发。
            self._activity_tracker.on_voice_mode(False)

            # Snapshot all mutable resource refs while holding the lock,
            # then operate only on locals to prevent killing newly created resources.
            main_session_ref = self.session
            message_handler_task_ref = self.message_handler_task
            tts_handler_task_ref = self.tts_handler_task
            tts_thread_ref = self.tts_thread
            tts_request_queue_ref = self.tts_request_queue
            tts_response_queue_ref = self.tts_response_queue

        logger.info("End Session: Starting cleanup...")
        self.sync_message_queue.put({'type': 'system', 'data': 'session end'})

        if message_handler_task_ref:
            message_handler_task_ref.cancel()
            try:
                await asyncio.wait_for(message_handler_task_ref, timeout=3.0)
            except asyncio.CancelledError:
                pass
            except asyncio.TimeoutError:
                logger.warning("End Session: Warning: Listener task cancellation timeout.")
            except Exception as e:
                # 任务可能已因并发 recv() 冲突等原因提前退出，此处只是发现既成事实
                logger.warning(f"End Session: Listener task had prior error: {e}")
            if self.message_handler_task is message_handler_task_ref:
                self.message_handler_task = None

        if main_session_ref:
            try:
                logger.info("End Session: Closing connection...")
                await main_session_ref.close()
                logger.info("End Session: Qwen connection closed.")
            except Exception as e:
                logger.error(f"💥 End Session: Error during cleanup: {e}")
            finally:
                if self.session is main_session_ref:
                    self.session = None

        await self._teardown_tts_runtime(
            tts_handler_task_ref, tts_thread_ref,
            tts_request_queue_ref, tts_response_queue_ref)
        # handler 可能在锁释放到 task 取消之间重新引入了过期错误码——
        # 在活跃会话拆除路径（is_active 已置 False）补充一次清理。
        # 但仅当 TTS 资源尚未被新会话替换时才重置，避免擦除新会话的状态。
        tts_replaced_by_new_session = (
            (self.tts_handler_task is not None and self.tts_handler_task is not tts_handler_task_ref) or
            (self.tts_thread is not None and self.tts_thread is not tts_thread_ref)
        )
        if not tts_replaced_by_new_session:
            self._reset_tts_retry_state()
        
        # 重置输入缓存状态
        async with self.input_cache_lock:
            self.session_ready = False
            self.pending_input_data.clear()
            self._clear_pending_context_appends()

        self.last_time = None
        if not by_server:
            await self.send_status(json.dumps({"code": "CHARACTER_LEFT", "details": {"name": self.lanlan_name}}))
            logger.info("End Session: Resources cleaned up.")

    async def cleanup(self, expected_websocket=None, *, expected_session=None, reset_starting_count=True):
        """
        Clean up session resources.

        Args:
            expected_websocket: optional, the expected websocket instance.
                               If provided and it doesn't match the current websocket, skip cleanup.
                               Prevents an old connection from wrongly cleaning up a new connection's resources (race protection).
            expected_session: optional, the expected session instance.
                             Session-level guard from lifecycle callbacks, passed through to end_session.
            reset_starting_count: forwarded to end_session. Pass False when the
                             caller is itself a start_session that owns the
                             _starting_session_count guard and will decrement it
                             in its own finally — letting cleanup reset it to 0
                             early opens a premature-0 window where a concurrent
                             start (e.g. the cross-mode restart wait) sees the
                             guard freed before the failing start has fully
                             unwound, then gets its websocket/guard clobbered by
                             the still-running teardown (Codex P2). Same rationale
                             as the in-start old-session cleanup at line ~5610.
        """
        if expected_websocket is not None and self.websocket is not None:
            if self.websocket != expected_websocket:
                logger.info("⏭️ cleanup 跳过：当前 websocket 已被新连接替换")
                return

        await self.end_session(by_server=True, expected_session=expected_session,
                               reset_starting_count=reset_starting_count)
        # 清理websocket引用，防止保留失效的连接
        # 使用共享锁保护websocket操作，防止与initialize_character_data()中的restore竞争
        if self.websocket_lock:
            async with self.websocket_lock:
                # 再次检查：只有当 websocket 仍是我们期望的那个时才清理
                if expected_websocket is None or self.websocket == expected_websocket:
                    self.websocket = None
        else:
            # 如果没有设置websocket_lock（旧代码路径），直接清理
            if expected_websocket is None or self.websocket == expected_websocket:
                self.websocket = None

    def _get_translation_service(self):
        """Get the translation service instance (lazily initialized)"""
        if self._translation_service is None:
            from utils.language_utils import get_translation_service
            self._translation_service = get_translation_service(self._config_manager)
        return self._translation_service
    
    def set_user_language(self, language: str):
        """
        Set the user language (reuses normalize_language_code for normalization)
        
        Supported normalization rules:
        - 'zh', 'zh-CN', 'zh-TW' and anything starting with 'zh' → 'zh-CN'
        - 'en', 'en-US', 'en-GB' and anything starting with 'en' → 'en'
        - 'ja', 'ja-JP' and anything starting with 'ja' → 'ja'
        - other languages unsupported for now, stays at the default 'zh-CN'
        """
        if not language:
            logger.warning(f"语言参数为空，保持当前语言: {self.user_language}")
            return

        # 校验原始输入：``normalize_language_code`` 对未识别值会默认回退 ``'en'``，
        # 外部来源（ws ``message['language']`` 携带的 corrupted ``localStorage``、
        # 第三方客户端发的 ``'undefined'`` / ``'null'`` / ``'estonian'`` 等 garbage）
        # 会被静默归一成 ``'en'``，覆盖正确的 session locale。先用公共白名单挡掉。
        if not is_supported_language_code(language):
            logger.warning(
                f"语言参数不支持: {language!r}，保持当前语言: {self.user_language}"
            )
            return

        # 使用公共函数进行语言代码归一化
        normalized_lang = normalize_language_code(language, format='full')

        self.user_language = normalized_lang
        self._conversation_turn_language = normalized_lang
        self._set_conversation_turn_language(normalized_lang)
        if normalized_lang != language:
            logger.info(f"用户语言已归一化: {language} → {normalized_lang}")
        else:
            logger.info(f"用户语言已设置为: {normalized_lang}")

        # 文本模式下无需额外同步改写提示语言（已移除 rewrite 逻辑）

        # 内置工具的 description / 参数说明是按 user_language 渲染的，
        # 这里换语言后重新注册一份覆盖 registry 旧描述，并 fire-and-forget
        # 推到当前 active / pending session 的 wire 上（OmniRealtimeClient
        # 支持 session.update 携带新 tools；OmniOfflineClient 下次 stream_text
        # 自动用最新 _tool_definitions）。
        self._register_builtin_tools()
        self._fire_task(self._sync_tools_to_active_session())
    
    async def send_status(self, message: str):
        """Send a status message to the frontend. message should be a JSON string {"code": "XXX", "details": {...}}, translated by the frontend via i18next."""
        try:
            if self.websocket and hasattr(self.websocket, 'client_state') and self.websocket.client_state == self.websocket.client_state.CONNECTED:
                data = json.dumps({"type": "status", "message": message})
                await self.websocket.send_text(data)

                # 同步到同步服务器
                self.sync_message_queue.put({'type': 'json', 'data': {"type": "status", "message": message}})
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.error(f"💥 WS Send Status Error: {e}")
    
    async def send_session_preparing(self, input_mode: str): # 通知前端session正在准备（静默期）
        try:
            if self.websocket and hasattr(self.websocket, 'client_state') and self.websocket.client_state == self.websocket.client_state.CONNECTED:
                data = json.dumps({"type": "session_preparing", "input_mode": input_mode})
                await self.websocket.send_text(data)
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.error(f"💥 WS Send Session Preparing Error: {e}")
    
    async def send_session_started(self, input_mode: str): # 通知前端session已启动
        try:
            if self.websocket and hasattr(self.websocket, 'client_state') and self.websocket.client_state == self.websocket.client_state.CONNECTED:
                data = json.dumps({"type": "session_started", "input_mode": input_mode})
                await self.websocket.send_text(data)
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.error(f"💥 WS Send Session Started Error: {e}")
    
    async def send_session_failed(self, input_mode: str): # 通知前端session启动失败
        """Notify the frontend that session start failed, so it hides the preparing banner and resets state"""
        try:
            if self.websocket and hasattr(self.websocket, 'client_state') and self.websocket.client_state == self.websocket.client_state.CONNECTED:
                data = json.dumps({"type": "session_failed", "input_mode": input_mode})
                await self.websocket.send_text(data)
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.error(f"💥 WS Send Session Failed Error: {e}")

    async def send_avatar_interaction_ack(self, interaction_id: str, accepted: bool, reason: str = '', turn_id: str = ''):
        """Acknowledge to the frontend the delivery result of an avatar-tap interaction, enabling retry and state wrap-up on the frontend."""
        if not interaction_id:
            return
        try:
            if self.websocket and hasattr(self.websocket, 'client_state') and self.websocket.client_state == self.websocket.client_state.CONNECTED:
                await self.websocket.send_json({
                    "type": "avatar_interaction_ack",
                    "interaction_id": interaction_id,
                    "accepted": bool(accepted),
                    "reason": str(reason or ''),
                    "turn_id": str(turn_id or ''),
                })
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.error(f"💥 WS Send Avatar Interaction Ack Error: {e}")

    async def send_session_ended_by_server(self): # 通知前端session已被服务器终止
        """Notify the frontend that the session was terminated server-side (e.g. API disconnect), so it resets the session state"""
        try:
            if self.websocket and hasattr(self.websocket, 'client_state') and self.websocket.client_state == self.websocket.client_state.CONNECTED:
                data = json.dumps({"type": "session_ended_by_server", "input_mode": self.input_mode})
                await self.websocket.send_text(data)
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.error(f"💥 WS Send Session Ended By Server Error: {e}")

    async def send_speech(self, tts_audio, speech_id: Optional[str] = None):
        """Send speech data to the frontend, sending the speech_id header first for precise interruption control"""
        try:
            if self.websocket and hasattr(self.websocket, 'client_state') and self.websocket.client_state == self.websocket.client_state.CONNECTED:
                effective_speech_id = speech_id if speech_id is not None else self.current_speech_id
                # recall 占位语音在 worker 层用合成 sid 切分 utterance；对前端必须归一回
                # turn sid，否则透传 request-id 的 provider 下，filler 音频带着合成 sid，
                # 打断时前端按 turn sid 匹配不到 → barge-in 取消不掉 filler。
                if isinstance(effective_speech_id, str) and effective_speech_id.endswith(_RECALL_FILLER_SID_SUFFIX):
                    effective_speech_id = effective_speech_id[: -len(_RECALL_FILLER_SID_SUFFIX)]
                await self.websocket.send_json({
                    "type": "audio_chunk",
                    "speech_id": effective_speech_id
                })
                await self.websocket.send_bytes(tts_audio)
                logger.debug(f"🔊 send_speech OK: {len(tts_audio)} bytes, speech_id={effective_speech_id}")
                self._speech_output_total += 1
                self._last_speech_output_time = time.time()
                self._last_speech_output_bytes = len(tts_audio)
                self.sync_message_queue.put({"type": "binary", "data": tts_audio})
                return True
            else:
                ws_state = getattr(self.websocket, 'client_state', None) if self.websocket else None
                logger.warning(f"⚠️ send_speech skipped: ws={self.websocket is not None}, state={ws_state}")
                return False
        except WebSocketDisconnect:
            logger.warning("⚠️ send_speech: WebSocket disconnected")
            return False
        except Exception as e:
            logger.error(f"💥 WS Send Response Error: {e}")
            return False

    async def tts_response_handler(self):
        q = self.tts_response_queue
        logger.info(f"🎧 tts_response_handler started (queue id={id(q):#x})")
        while True:
            try:
                # 阻塞 get 挂在线程池里，无消息时主 event loop 完全沉默；
                # 取消时 except CancelledError 分支会 push 哨兵唤醒线程池里那个
                # 仍在 q.get() 上的线程，避免线程泄漏。
                data = await asyncio.to_thread(q.get)

                # 处理 cancel 时为唤醒泄漏线程而 push 的哨兵。同一个 handler 实例
                # 不会在 cancel 之后继续运行（CancelledError 已 raise），所以这里
                # 只是为了在 handler 被替换后，新 handler（绑同一 queue）若意外
                # 读到旧 handler 留下的哨兵也能正确忽略。
                if isinstance(data, tuple) and len(data) == 2 and data[0] == "__handler_exit__":
                    continue

                if isinstance(data, tuple) and len(data) == 2:
                    if data[0] == "__ready__":
                        ready_flag = bool(data[1])
                        async with self.tts_cache_lock:
                            self.tts_ready = ready_flag
                        if ready_flag:
                            self._last_tts_error_code = ''
                            self._tts_retry_notify_count = 0
                            logger.info("✅ 收到TTS运行时就绪信号，开始刷新缓存文本")
                            await self._flush_tts_pending_chunks()
                        else:
                            # 复用 __error__ 分支记录的 code 判断是否重试
                            _last_code = self._last_tts_error_code
                            if _last_code in NO_RETRY_TTS_CODES:
                                logger.warning(f"⚠️ TTS 未就绪且上次错误为 {_last_code}，跳过自动重试")
                                # 取消可能仍在等待的延迟重试任务，避免绕过 no-retry 策略
                                if self._tts_respawn_task and not self._tts_respawn_task.done():
                                    self._tts_respawn_task.cancel()
                                    self._tts_respawn_task = None
                                # TTS 不会恢复，清空无用的缓存文本，避免白白占用内存
                                async with self.tts_cache_lock:
                                    self.tts_pending_chunks.clear()
                            else:
                                logger.warning("⚠️ 收到TTS未就绪信号，13秒后尝试重新拉起Worker")
                                # 取消之前的延迟重试任务（如有）
                                if self._tts_respawn_task and not self._tts_respawn_task.done():
                                    self._tts_respawn_task.cancel()
                                    self._tts_respawn_task = None
                                # 捕获当前会话身份与 TTS 标志，防止跨会话的错误 respawn
                                _expected_session = self.session
                                _expected_use_tts = self.use_tts
                                async def _delayed_respawn(_expected_session=_expected_session,
                                                           _expected_use_tts=_expected_use_tts):
                                    await asyncio.sleep(13)
                                    if not self.is_active or self.tts_ready:
                                        return
                                    if self.session is not _expected_session or self.use_tts != _expected_use_tts:
                                        logger.info("🔄 TTS 延迟重试：会话已变更，跳过 respawn")
                                        return
                                    logger.info("🔄 TTS 延迟重试：尝试重新拉起 Worker...")
                                    self._respawn_tts_worker()
                                self._tts_respawn_task = asyncio.ensure_future(_delayed_respawn())
                        continue
                    elif data[0] == "__warning__":
                        # TTS worker 发来的提示性消息（如水印检测），直接转发前端
                        self._fire_task(self.send_status(data[1]))
                        continue
                    elif data[0] == "__reconnecting__":
                        self._tts_retry_notify_count += 1
                        logger.info(f"🌊 TTS 正在自动重连 (retry {self._tts_retry_notify_count})")
                        if self._tts_retry_notify_count >= 3:
                            user_msg = json.dumps({"code": "TTS_RECONNECTING", "level": "info"})
                            self._fire_task(self.send_status(user_msg))
                        continue
                    elif data[0] == "__error__":
                        error_msg = data[1]
                        error_msg_text = str(error_msg)
                        logger.error(f"TTS Worker Error: {error_msg}")

                        # 优先尝试从结构化 JSON 中提取明确的 code 字段
                        _known_codes = {
                            'API_ARREARS', 'API_QUOTA_TIME', 'API_KEY_REJECTED',
                            'API_RATE_LIMIT', 'API_POLICY_VIOLATION',
                            'API_1008_FALLBACK', 'TTS_CONNECTION_FAILED',
                            'UPSTREAM_SERVER_BUSY', 'TTS_CONFIG_INVALID',
                        }
                        _parsed_code = None
                        _keyword_target = error_msg_text  # 非 JSON 错误时回退使用
                        try:
                            _parsed = json.loads(error_msg_text)
                            if isinstance(_parsed, dict):
                                # 结构化错误：关键词匹配只看 data.message，避免元数据误判
                                _keyword_target = ""
                                # 先检查顶层 code
                                _candidate = _parsed.get('code', '')
                                if isinstance(_candidate, str) and _candidate in _known_codes:
                                    _parsed_code = _candidate
                                # 再检查 data.code（TTS 事件结构）
                                if not _parsed_code:
                                    _data = _parsed.get('data', {})
                                    if isinstance(_data, dict):
                                        _candidate = _data.get('code', '')
                                        if isinstance(_candidate, str) and _candidate in _known_codes:
                                            _parsed_code = _candidate
                                        # 关键词匹配仅针对 message 字段
                                        _keyword_target = str(_data.get('message', '') or "")
                        except (json.JSONDecodeError, TypeError):
                            # JSON parsing may fail for free-form error strings from
                            # tts.response.error events; this is expected and harmless —
                            # the keyword-based fallback below will handle classification.
                            pass

                        if _parsed_code:
                            user_msg = json.dumps({"code": _parsed_code, "details": {"msg": error_msg_text}})
                            self._last_tts_error_code = _parsed_code
                        else:
                            # 回退到关键词匹配（仅匹配 message 字段，不匹配 UUID/时间戳等元数据）
                            error_msg_lower = _keyword_target.lower()
                            if '欠费' in error_msg_lower or 'standing' in error_msg_lower:
                                user_msg = json.dumps({"code": "API_ARREARS"})
                                self._last_tts_error_code = 'API_ARREARS'
                            elif 'quota' in error_msg_lower or 'time limit' in error_msg_lower:
                                user_msg = json.dumps({"code": "API_QUOTA_TIME"})
                                self._last_tts_error_code = 'API_QUOTA_TIME'
                            elif '429' in error_msg_lower or 'too many' in error_msg_lower:
                                user_msg = json.dumps({"code": "API_RATE_LIMIT"})
                                self._last_tts_error_code = 'API_RATE_LIMIT'
                            elif _is_safety_violation_signal(error_msg_lower):
                                user_msg = json.dumps({"code": "API_POLICY_VIOLATION", "details": {"msg": error_msg_text}})
                                self._last_tts_error_code = 'API_POLICY_VIOLATION'
                            elif '1008' in error_msg_lower:
                                user_msg = json.dumps({"code": "API_1008_FALLBACK", "details": {"msg": error_msg_text}})
                                self._last_tts_error_code = 'API_1008_FALLBACK'
                            elif ('401' in error_msg_lower or 'unauthorized' in error_msg_lower
                                    or 'authentication' in error_msg_lower
                                    or 'incorrect api key' in error_msg_lower
                                    or 'invalid_api_key' in error_msg_lower
                                    or ('invalid' in error_msg_lower and 'key' in error_msg_lower)):
                                user_msg = json.dumps({"code": "API_KEY_REJECTED", "details": {"msg": error_msg_text}})
                                self._last_tts_error_code = 'API_KEY_REJECTED'
                            else:
                                user_msg = json.dumps({"code": "TTS_CONNECTION_FAILED", "details": {"msg": error_msg_text}})
                                self._last_tts_error_code = 'TTS_CONNECTION_FAILED'
                        # Telemetry：TTS 失败。code 是已归一化的低基数枚举
                        # （API_ARREARS / API_KEY_REJECTED / TTS_CONNECTION_FAILED ...）。
                        # 首日听不到语音是核心体验断裂，D1 流失重要信号。
                        try:
                            from utils.instrument import counter as _instr_counter
                            # before_first_loop：TTS 在用户体验到核心 loop 前就坏 =
                            # 首次体验障碍（开了口但没听到回复）。低基数 true/false/unknown。
                            try:
                                from utils.token_tracker import TokenTracker as _TT
                                _bfl = "false" if _TT.get_instance().has_completed_core_loop() else "true"
                            except Exception:
                                _bfl = "unknown"
                            _instr_counter("tts_error", code=str(self._last_tts_error_code or "unknown")[:32], before_first_loop=_bfl)
                        except Exception:
                            # 埋点 best-effort，绝不影响 TTS 错误的重试/上报主流程。
                            pass
                        # 可重试的错误：前2次静默重试，第3次失败时上报前端
                        if self._last_tts_error_code not in IMMEDIATE_REPORT_TTS_CODES:
                            self._tts_retry_notify_count += 1
                            if self._tts_retry_notify_count < 3:
                                logger.info(f"TTS 错误重试 {self._tts_retry_notify_count}/3，暂不通知前端")
                                continue
                        self._fire_task(self.send_status(user_msg))
                        continue
                elif isinstance(data, tuple) and len(data) == 3 and data[0] == "__audio__":
                    _, speech_id, audio_payload = data
                    if await self.send_speech(audio_payload, speech_id=speech_id):
                        self._confirm_pending_ai_voice_echo(speech_id)
                        # Telemetry：音频成功投递 = 用户听到了角色的声音。配合
                        # note_core_loop_completed 的"用户已开口"前置，构成 D1
                        # 核心 loop 完成信号（每进程一次，内部幂等）。
                        try:
                            from utils.token_tracker import TokenTracker as _TT
                            _TT.get_instance().note_core_loop_completed()
                        except Exception:
                            # 埋点 best-effort，绝不影响音频投递主流程；
                            # note_core_loop_completed 自身幂等。
                            pass
                    else:
                        self._discard_pending_ai_voice_echo()
                    continue

                size = len(data) if isinstance(data, (bytes, bytearray)) else f"type={type(data).__name__}"
                logger.debug(f"🎧 handler dequeued audio: {size}, qsize≈{q.qsize()}")
                await self.send_speech(data)
                self._discard_pending_ai_voice_echo()
            except asyncio.CancelledError:
                logger.info("🎧 tts_response_handler cancelled")
                # asyncio.to_thread 取消后，线程池里那个 thread 仍阻塞在 q.get()。
                # push 哨兵唤醒它返回，避免线程泄漏（线程持有 queue ref，整个 queue
                # 也会被一起留住）。put_nowait 失败不影响主流程。
                try:
                    q.put_nowait(("__handler_exit__", None))
                except Exception:
                    pass
                raise
            except Exception as e:
                logger.error(f"💥 tts_response_handler error (will retry): {e}")
                await asyncio.sleep(0.01)
