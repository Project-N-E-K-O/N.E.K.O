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
"""Shared module-level constants and helpers for the ``main_logic.core`` package.

Split out of the former single-file ``main_logic/core.py`` as a pure move (no
behavior change). The package ``__init__`` re-exports every name defined here,
so existing ``main_logic.core.<name>`` imports and test monkeypatches keep
working unchanged.
"""
import contextvars
import json
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Any

from utils.language_utils import normalize_language_code, get_global_language
from utils.logger_config import get_module_logger


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
_LIVE_VISION_STREAM_INPUT_TYPES = frozenset({"screen", "camera"})
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


# Logger for the whole package. Bound to the literal package name rather than
# ``__name__`` so every submodule keeps logging under the exact pre-split
# logger name "N.E.K.O.Main.main_logic.core" (tests and log routing key off it).
logger = get_module_logger("main_logic.core", "Main")


# 用户静默达到此阈值 → 后台 loop 主动 end_session，让下一条消息触发
# start_session(new=False) 重新拉 /new_dialog 注入新鲜时间/长间隔提示/节日
# 上下文，解决长挂机 session 上下文僵化（"猫娘还停留在前一晚"）的问题。
# 周期检查间隔故意远小于阈值（粒度 ~1 min），避免静默 30:01 时还要再等
# 一整轮。
IDLE_SESSION_RESET_THRESHOLD_SECONDS = 1800
IDLE_SESSION_RESET_CHECK_INTERVAL_SECONDS = 60

# 前端文本会话 start_session 等 session_started 的硬超时（static/app/app-buttons.js
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


_STATIC_LOCALES_DIR = Path(__file__).resolve().parents[2] / "static" / "locales"


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


# Sentinel returned by _start_session_start_llm when CAS detects a concurrent start
# already promoted its own session.  Returning a sentinel (instead of raising)
# keeps the loser out of the generic error path — that path calls cleanup()
# without an expected_session guard and would otherwise tear down the winner's
# session/websocket while also inflating session_start_failure_count.
_START_LLM_CONCURRENT_ABORTED = object()

# 强引用兜底：事件循环只弱引用 task，分离的收尸 task（lifecycle 的 listener
# 取消超时 fail-close 后等旧 listener 退出再关旧 session）若无人持有可能被
# GC 掐死在半路。add + done_callback(discard) 模式。
_ORPHAN_SESSION_REAPER_TASKS: set = set()


@dataclass(frozen=True)
class ContextAppendResult:
    appended: bool
    deduped: bool = False
    targets: tuple[str, ...] = ()
    reason: str | None = None


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
