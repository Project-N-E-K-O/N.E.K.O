"""
本文件是主逻辑文件，负责管理整个对话流程。当选择不使用TTS时，将会通过OpenAI兼容接口使用Omni模型的原生语音输出。
当选择使用TTS时，将会通过额外的TTS API去合成语音。注意，TTS API的输出是流式输出、且需要与用户输入进行交互，实现打断逻辑。
TTS部分使用了两个队列，原本只需要一个，但是阿里的TTS API回调函数只支持同步函数，所以增加了一个response queue来异步向前端发送音频数据。
"""
import asyncio
import contextvars
import json
import struct  # For packing audio data
import re
import time
from collections import deque
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional


# Sentinel for `send_lanlan_response(request_id=...)` so we can tell apart
# "caller didn't pass it (use shared field as fallback)" from "caller
# explicitly passed None to mean 'no request id'". A normal default of
# None collapses both into the same code path and would let recovery /
# proactive paths accidentally bind their messages to a newer request_id.
_REQUEST_ID_UNSET: Any = object()
from datetime import datetime
from websockets import exceptions as web_exceptions
from fastapi import WebSocket, WebSocketDisconnect
from utils.frontend_utils import contains_chinese, replace_blank, replace_corner_mark, remove_bracket, \
    is_only_punctuation, TtsStreamNormalizer, TtsBracketStripper, TtsMarkdownStripper
from utils.screenshot_utils import process_screen_data, overlay_avatar_annotation
from main_logic.omni_realtime_client import OmniRealtimeClient
from main_logic.omni_offline_client import OmniOfflineClient
from main_logic.tts_client import get_tts_worker, dummy_tts_worker, TTS_PROVIDER_REGISTRY
from utils.llm_client import AIMessage
from main_logic.session_state import SessionStateMachine, SessionEvent
from utils.preferences import load_global_conversation_settings, aload_global_conversation_settings
from config import (
    MEMORY_SERVER_PORT,
    TOOL_SERVER_PORT,
    SESSION_ARCHIVE_TRIGGER_TOKENS,
    SESSION_TURN_THRESHOLD,
    AVATAR_INTERACTION_DEDUPE_MAX_ITEMS,
)
from config.prompts_sys import (
    _loc,
    SESSION_INIT_PROMPT, SESSION_INIT_PROMPT_AGENT,
    AGENT_TASK_STATUS_RUNNING, AGENT_TASK_STATUS_QUEUED,
    AGENT_TASKS_HEADER, AGENT_TASKS_NOTICE,
    CONTEXT_SUMMARY_READY,
    SYSTEM_NOTIFICATION_PROACTIVE,
    SYSTEM_NOTIFICATION_PASSIVE,
    SOURCE_DESCRIPTORS,
    TASK_STATUS_PHRASES,
    TASK_ACTION_PHRASES,
    CONTEXT_SUMMARY_TASK_HEADER, CONTEXT_SUMMARY_TASK_FOOTER,
    RESULT_PARSER_PHRASES,
)


# 内部 item 渲染时的视觉标记。状态信息已在外层 SYSTEM_NOTIFICATION_PROACTIVE
# 表达，emoji 仅作快速视觉识别用。
_STATUS_EMOJI = {
    "completed": "✅",
    "partial": "⚠️",
    "failed": "❌",
    "cancelled": "🚫",
}


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


def _render_callback_inner_item(cb: dict, lang: str) -> str:
    """Render one callback as a single inline string for the LLM prompt.

    Returns ``""`` when there is genuinely nothing to convey (both summary
    and detail empty); the caller can then drop the line and rely on the
    outer header alone to express that something happened.
    """
    summary = (cb.get("summary") or "").strip()
    detail = (cb.get("detail") or "").strip()
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

    Groups by ``(delivery_mode/passive flag, status, source_kind, source_name)``
    so each group can pick the right outer template (PROACTIVE vs PASSIVE)
    and slot in the right status/action phrases.
    """
    if not callbacks:
        return ""
    from collections import OrderedDict

    grouped: "OrderedDict[tuple, list]" = OrderedDict()
    for cb in callbacks:
        # passive=True call = drain path; treat all as passive regardless
        # of per-callback delivery_mode.
        cb_passive = passive or (cb.get("delivery_mode") == "passive")
        key = (
            cb_passive,
            cb.get("status") or "completed",
            cb.get("source_kind") or "unknown",
            (cb.get("source_name") or ""),
        )
        grouped.setdefault(key, []).append(cb)

    parts: list[str] = []
    for (cb_passive, status, _src_kind, _src_name), cbs in grouped.items():
        source_text = _format_callback_source(cbs[0], lang)
        if cb_passive:
            header = _loc(SYSTEM_NOTIFICATION_PASSIVE, lang).format(source=source_text)
        else:
            status_phrase = _loc(
                TASK_STATUS_PHRASES.get(status) or TASK_STATUS_PHRASES["completed"],
                lang,
            )
            action_phrase = _loc(
                TASK_ACTION_PHRASES.get(status) or TASK_ACTION_PHRASES["completed"],
                lang,
            )
            header = _loc(SYSTEM_NOTIFICATION_PROACTIVE, lang).format(
                source=source_text,
                status_phrase=status_phrase,
                action_phrase=action_phrase,
                name=lanlan_name,
                master=master_name,
            )
        items = [_render_callback_inner_item(cb, lang) for cb in cbs]
        items = [s for s in items if s]
        if items:
            parts.append(header + "\n".join(items))
        else:
            # No item text — outer header alone (e.g. "task X failed") still
            # tells the AI that something happened. Strip trailing newline so
            # the joined output is clean.
            parts.append(header.rstrip())
    return "\n\n".join(parts)
from config.prompts_avatar_interaction import (
    _normalize_avatar_interaction_payload,
    _build_avatar_interaction_instruction,
    _build_avatar_interaction_memory_meta,
)
# Historical imports kept here (commented) for easy rollback:
# from config import USER_PLUGIN_SERVER_PORT
# from config.prompts_sys import (
#     SESSION_INIT_PROMPT_AGENT_DYNAMIC,
#     AGENT_CAPABILITY_COMPUTER_USE, AGENT_CAPABILITY_BROWSER_USE,
#     AGENT_CAPABILITY_USER_PLUGIN_USE, AGENT_CAPABILITY_GENERIC, AGENT_CAPABILITY_SEPARATOR,
#     AGENT_PLUGINS_HEADER, AGENT_PLUGINS_COUNT,
# )
from utils.config_manager import get_config_manager, get_reserved
from utils.logger_config import get_module_logger
from utils.api_config_loader import get_free_voices
from utils.language_utils import normalize_language_code, get_global_language, get_global_language_full
import threading
from threading import Thread
from queue import Queue, Empty
from uuid import uuid4
import numpy as np
import soxr
import httpx
from main_logic.agent_event_bus import publish_analyze_request_reliably

# Setup logger for this module
logger = get_module_logger(__name__, "Main")

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
NO_RETRY_TTS_CODES = {'API_ARREARS', 'API_KEY_REJECTED'}
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
    """将一条醒目通知放入缓冲池，等待前端拉取。
    
    可传入字符串（自动包装为 {"message": ...}）或结构化字典
    （建议包含 "code"、"message"、"message_en"、"details" 字段）。
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
    """返回缓冲池快照和当前游标（供 GET /pending-notices 使用）。

    返回 (notices_without_internal_fields, cursor)；cursor 是本次快照中最大的
    _nid，调用方将其传给 drain_prominent_notices(cursor) 即可精确删除已展示项。
    """
    with _prominent_notice_lock:
        items = list(_prominent_notice_queue)
    cursor = items[-1]["_nid"] if items else 0
    public = [{k: v for k, v in it.items() if k != "_nid"} for it in items]
    return public, cursor


def drain_prominent_notices(up_to_cursor: int) -> list[dict]:
    """删除 _nid ≤ up_to_cursor 的通知，保留之后新入队的项目。

    返回被删除的通知列表。传入 0 或负数时不删除任何条目。
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
    """去重后推送旧版 CosyVoice 音色通知。供 main_server 启动路径和
    LLMSessionManager 共同调用，避免重复弹出相同角色通知。"""
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


# --- 一个带有定期上下文压缩+在线热切换的语音会话管理器 ---
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
        raw_voice_id = self._get_voice_id()
        if self._should_block_free_preset_voice(raw_voice_id, realtime_config.get('base_url', '')):
            self.voice_id = ''
            self._is_free_preset_voice = False
        else:
            self.voice_id = raw_voice_id
            self._is_free_preset_voice = self._is_preset_voice_id(self.voice_id)
        if self._is_free_preset_voice and self.core_api_type != 'free':
            self.voice_id = ''
            self._is_free_preset_voice = False
        # 注意：use_tts 会在 start_session 中根据 input_mode 重新设置
        self.use_tts = False
        self.generation_config = {}  # Qwen暂时不用
        self.message_cache_for_new_session = []
        self.is_preparing_new_session = False
        self.summary_triggered_time = None
        self.initial_cache_snapshot_len = 0
        self.pending_session_warmed_up_event = None
        self.pending_session_final_prime_complete_event = None
        self.session_start_time = None
        self._session_turn_count = 0  # 当前 session 的用户输入轮次计数
        self.pending_connector = None
        self.pending_session = None
        self.is_hot_swap_imminent = False
        self.tts_handler_task = None
        # 热切换相关变量
        self.background_preparation_task = None
        self.final_swap_task = None
        self.receive_task = None
        self.message_handler_task = None
        # 任务完成后的额外回复队列（将在下一次切换时统一汇报，语音模式使用）
        self.pending_extra_replies = []
        # 结构化 agent 任务回调队列（用于按会话类型注入）
        self.pending_agent_callbacks: list[dict] = []
        # 防止 trigger_agent_callbacks 和 finish_proactive_delivery 并发写 WS/sync_message_queue
        self._proactive_write_lock = asyncio.Lock()
        # 由前端控制的Agent相关开关
        self.agent_flags = {
            'agent_enabled': False,
            'computer_use_enabled': False,
            'browser_use_enabled': False,
            'user_plugin_enabled': False,
            'openclaw_enabled': False,
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
        self._memory_error_retry_after = 0  # Memory Server 专属冷却时间戳
        self._memory_error_cooldown_seconds = 10  # Memory Server 冷却时间
        
        # 防止并发启动的标志（使用计数器避免并发 start_session 的 finally 互相覆盖）
        self._starting_session_count = 0
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
        
        # 输入数据缓存机制：确保session初始化期间的输入不丢失
        self.session_ready = False  # Session是否完全就绪
        self.pending_input_data = []  # 待处理的输入数据: [message_dict, ...]
        self.input_cache_lock = asyncio.Lock()  # 保护输入缓存的锁
        
        # 热切换音频缓存机制：确保热切换期间的用户输入语音不丢失
        self.hot_swap_audio_cache = []  # 热切换期间缓存的音频数据: [bytes, ...]
        self.hot_swap_cache_lock = asyncio.Lock()  # 保护热切换音频缓存的锁
        self.is_flushing_hot_swap_cache = False  # 是否正在推送热切换缓存（推送期间新音频继续缓存）
        self.HOT_SWAP_FLUSH_CHUNK_MULTIPLIER = 5  # 热切换后发送的chunk大小倍数(节流)
        
        # 用户活动时间戳：用于主动搭话检测最近是否有用户输入
        self.last_user_activity_time = None  # float timestamp or None

        # 用户活动 tracker：把窗口/进程/CPU/idle/语音/对话信号聚合成结构化
        # ActivitySnapshot，供 proactive_chat Phase 1/2 决策搭话倾向。
        # 详见 docs/design/user-activity-tracker.md。
        from main_logic.activity import UserActivityTracker
        self._activity_tracker = UserActivityTracker(lanlan_name)

        # AI 当前轮文本 buffer：每个 send_lanlan_response chunk 累加，turn end
        # 时连同 on_ai_message 一起喂给 tracker。后者用末尾文本判断是否问问号
        # → 触发 unfinished_thread 机制（5 分钟内允许至多 2 次跟进）。
        self._current_ai_turn_text: str = ''

        # 事件驱动状态机：收口 "谁占用当前 turn" 的所有信号，供 proactive 流水线
        # 零成本（O(1) 读）频繁询问 is_proactive_preempted。事件发射点分布在
        # handle_new_message / stream_text 入口 / prepare_proactive_delivery /
        # finish_proactive_delivery / system_router.proactive_chat 等处。
        self.state = SessionStateMachine(lanlan_name=lanlan_name)
        
        # 用户语言设置（由 start_session 或前端 set_user_language() 设置，初始为 None）
        self.user_language = None
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
        # 下一次 handle_response_complete 发出的 turn end 要携带的 meta。
        # 在 handle_avatar_interaction 等需要标记特殊轮次的入口里设置，
        # 由 handle_response_complete 读取并清空。比独立的
        # sync_message_queue 控制消息更原子：meta 与 turn end 事件
        # 同生共死，不会因为两条消息的时序错乱而把 avatar 轮当成 proactive。
        self._pending_turn_meta: Optional[dict] = None

    def _fire_task(self, coro):
        """Create a background task with GC protection (prevent Python 3.11+ from collecting it)."""
        task = asyncio.create_task(coro)
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)
        return task

    def _emit_cooldown_turn_end_if_needed(self):
        """冷却期间去重发送 turn_end，每秒最多一次。返回 True 表示当前处于冷却中。"""
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
        """读取用户设置的回复 token 上限。
        单位：tiktoken (o200k_base) tokens。0 = 无限制（返回 999999）。
        默认 300 tokens ≈ 400 CJK 字 / ~1200 英文字符。
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
        """把一段文本 chunk 入 TTS 队列，http_sentence 类 provider 走 normalizer。

        调用方必须已持有 ``self.tts_cache_lock``（与现有 put 调用点一致）。
        对于 ws_bistream 类 provider（qwen / step / cosyvoice），文本碎片直接
        发给服务端处理，跳过 normalizer 以避免 pending_spaces 延迟和 CJK 边界
        空格删除干扰服务端合成节奏。控制信号（``__interrupt__`` 打断 /
        ``(None, None)`` 本轮 utterance 结束-flush / ``("__shutdown__", None)``
        worker 退出）请继续用 ``tts_request_queue.put`` 直接发送，并在合适
        时机调用 ``_reset_tts_stream_normalizer``。
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

    def _reset_tts_stream_normalizer(self) -> None:
        """清空所有 TTS 文本 stripper 状态。中断 / 轮次结束 / session 重建时调用。"""
        self._tts_stream_normalizer.reset()
        self._tts_markdown_stripper.reset()
        self._tts_bracket_stripper.reset()
        self._tts_norm_speech_id = None

    def _request_tts_done_locked(self) -> str:
        """请求为当前轮次排入 TTS 结束信号。

        调用方必须已持有 ``self.tts_cache_lock``。若文本仍在 pending 或 worker
        尚未 ready，则只记录 deferred 状态，待 `_flush_tts_pending_chunks()`
        在 ready 后统一补发，避免 `(None, None)` 早于文本 chunk 入队。
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

        self.tts_request_queue.put((None, None))
        self._tts_done_queued_for_turn = True
        self._tts_done_pending_until_ready = False
        return "queued"

    async def _request_tts_done_for_turn(
        self,
        source: str,
        expected_speech_id: str | None = None,
    ) -> str:
        """线程安全地为当前轮次请求 TTS 结束信号。

        ``expected_speech_id`` 可选 sid 校验：调用方持有本轮 sid 快照
        时传入，函数会在锁内确认 ``self.current_speech_id`` 仍等于该
        快照才发 done。recovery / proactive 等 await 之间用户开新轮的
        场景，旧轮的 done 信号否则会直接结束新轮的 TTS（首句被截 / 整轮
        静音）。不传则保持原行为：始终发 done。"""
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

    async def _clear_tts_pipeline(self):
        """清空 TTS 请求/响应队列和待处理缓存，停止当前合成。"""
        if self.use_tts and self.tts_thread and self.tts_thread.is_alive():
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

    async def handle_new_message(self):
        """处理新模型输出：清空TTS队列并通知前端"""
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

    async def handle_text_data(self, text: str, is_first_chunk: bool = False):
        """文本回调：处理文本显示和TTS（用于文本模式）"""

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

        # 如果是新消息的第一个chunk，清空TTS队列和缓存以打断之前的语音
        if is_first_chunk and self.use_tts:
            async with self.tts_cache_lock:
                self.tts_pending_chunks.clear()

            if self.tts_thread and self.tts_thread.is_alive():
                # 清空响应队列中待发送的音频数据
                while not self.tts_response_queue.empty():
                    try:
                        self.tts_response_queue.get_nowait()
                    except: # noqa
                        break

        # 文本模式下，无论是否使用TTS，都要发送文本到前端显示
        await self.send_lanlan_response(text, is_first_chunk)
        
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

    def _flush_ai_turn_text_to_tracker(self) -> None:
        """Flush the per-turn AI text buffer into the activity tracker.

        Called from each AI-turn-end exit point — there are three:
          - ``_emit_turn_end`` for regular replies (and truncate-recovery)
          - ``handle_proactive_complete`` for the agent direct-reply path
          - ``finish_proactive_delivery`` for /api/proactive_chat success

        The tracker runs the question heuristic over the text and (when
        text is non-empty) bumps ``_conv_seq`` for open_threads cache
        invalidation. Empty / None text is fine — it just updates the
        timestamp without opening an unfinished_thread or invalidating
        the cache.
        """
        self._activity_tracker.on_ai_message(text=self._current_ai_turn_text or None)
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
        """同时把 turn end 信号下发给 sync_message_queue 和 WebSocket，
        并把 ``_pending_turn_meta`` 透传到两条通道后清空。两条路径共用：
        - ``handle_response_complete`` 正常完成
        - ``handle_response_discarded`` 的 truncate-recovery / too-long-final
        语义统一：sync queue 和 WS 都带相同 meta，避免一边有 meta 一边没。"""
        turn_end_msg: dict = {'type': 'system', 'data': 'turn end'}
        pending_meta = self._pending_turn_meta
        if pending_meta:
            turn_end_msg['meta'] = pending_meta
            self._pending_turn_meta = None
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

    async def handle_response_complete(self):
        """Qwen完成回调：用于处理Core API的响应完成事件，包含TTS和热切换逻辑"""
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
        """Turn end 之后的统一收尾：renew/prewarm 判断 + agent callback 投递。

        被 ``handle_response_complete`` 和 ``handle_response_discarded`` 的
        recovery / too-long-final 分支共用，避免连续走 RESPONSE_LENGTH_TRUNCATED
        / RESPONSE_TOO_LONG 时 session 不归档/不预热而陷入"上下文越来越大→
        一直截断恢复"循环。
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
        处理响应被丢弃的通知：清空 TTS 管线 + 前端输出，必要时发送 turn end
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
        """Qwen音频回调：推送音频到WebSocket前端"""
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

    async def handle_input_transcript(self, transcript: str, *, is_voice_source: bool = True):
        """输入转录回调：同步转录文本到消息队列和缓存，并发送到前端显示

        ``is_voice_source`` defaults to True for the realtime-client
        callbacks (genuine VAD-captured speech). Text-mode call sites
        that reuse this function for non-voice paths (e.g. openclaw
        handoff at ``_dispatch_openclaw_handoff``) pass False so that:
          - voice_rms is NOT marked (no fake voice_engaged state)
          - on_user_message is skipped here (the text-mode entry has
            already called it directly with the input data — calling
            twice would double-bump _conv_seq and add the text to the
            buffer twice)
        """
        # 更新用户活动时间戳（用于主动搭话检测）
        self.last_user_activity_time = time.time()
        if is_voice_source:
            # transcript 到达 → VAD 在窗口内捕捉到声音，标记 voice RMS 活跃；
            # 即使转录为空（VAD 误触发或转录失败）也算一次"用户在发声"，
            # 维持 voice_engaged 状态。
            self._activity_tracker.on_voice_rms()
            # 仅非空转录才算"用户消息"：on_user_message 会清掉 unfinished_thread、
            # bump _conv_seq（让 open_threads 缓存失效）、把文本进 buffer 给
            # emotion-tier LLM 用——空 transcript 这些副作用都不该触发。
            if transcript.strip():
                self._activity_tracker.on_user_message(text=transcript)
                self._session_turn_count += 1
        else:
            # Non-voice reuse of this method (e.g. openclaw text handoff).
            # Skip activity-tracker hooks entirely — the text-mode entry
            # at `_process_stream_data_internal` has already recorded the
            # user message. We still need the queue/cache plumbing below
            # to work normally, so just bypass the tracker block.
            if transcript.strip():
                self._session_turn_count += 1

        # 推送到同步消息队列
        self.sync_message_queue.put({"type": "user", "data": {"input_type": "transcript", "data": transcript.strip()}})
        
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
            self.lanlan_name, type(self.session).__name__, _ws_connected_dbg, len(transcript.strip()),
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
                self.message_cache_for_new_session.append({"role": self.master_name, "text": transcript.strip()})
            elif self.message_cache_for_new_session[-1]['role'] == self.master_name:
                self.message_cache_for_new_session[-1]['text'] += transcript.strip()
        # 注意: 这里不能修改 current_speech_id.
        # speech_id 仅应在“模型新回复开始”时更新 (handle_new_message / 文本模式 stream 入口),
        # 否则会导致前端把同一轮 AI 语音误判为新轮次, 出现首包被重置/吞掉的问题.

    async def handle_output_transcript(self, text: str, is_first_chunk: bool = False):
        """输出转录回调：处理文本显示和TTS（用于语音模式）"""
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
        await self.send_lanlan_response(text, is_first_chunk)
        
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
        request_id: Any = _REQUEST_ID_UNSET,
    ):
        """Qwen输出转录回调: 可用于前端显示/缓存/同步。

        ``request_id`` 三态：
          - 不传（即默认 ``_REQUEST_ID_UNSET``）→ fallback 到共享字段
            ``self._active_text_request_id``，保留现有 LLM 流式 callsite 行为
          - 显式传 ``None`` → 真"冻结为空"，proactive / 无 request_id 的
            场景需要让前端知道这条消息不绑定任何用户请求
          - 显式传 str → 跨轮安全：discard / recovery 必须用函数开头快照
            的 ``active_request_id``，避免新轮已经写入共享字段后回读到
            错的 id 导致前端 rollback 串轮
        默认 sentinel 用 module-level ``_REQUEST_ID_UNSET = object()`` 区分
        "未传"和"显式 None"，与单纯 ``request_id is None`` 检测不同。
        """
        text_clean = self.emotion_pattern.sub('', text)
        # 累加到当前轮 AI 文本 buffer，turn end 时一并交给 activity tracker 做
        # unfinished_thread 检测。emotion_pattern 已剥掉表情标签，但保留 <expr>
        # 等可能的 markup——tracker 自己会做二次 strip。
        self._current_ai_turn_text += text_clean
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

        # 无论 WS 发送成功与否，始终将消息写入 sync_message_queue 和 message_cache，
        # 确保 cross_server 历史组装不因 WS 断连而丢失 assistant 内容。
        if is_first_chunk:
            logger.debug("[%s] send_lanlan_response: first chunk (len=%d)", self.lanlan_name, len(text_clean))
        self.sync_message_queue.put({"type": "json", "data": message})
        if hasattr(self, 'is_preparing_new_session') and self.is_preparing_new_session:
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
        
    async def handle_silence_timeout(self, *, expected_session=None):
        """处理语音输入静默超时：自动关闭session但保持live2d显示"""
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
                await self.websocket.send_json({
                    "type": "auto_close_mic",
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
                    or ('invalid' in message_text_lower and 'key' in message_text_lower)):
                await self.send_status(json.dumps({"code": "API_KEY_REJECTED"}))
            elif 'policy violation' in message_text_lower:
                await self.send_status(json.dumps({"code": "API_POLICY_VIOLATION", "details": {"msg": message_text}}))
            elif '1008' in message_text_lower:
                await self.send_status(json.dumps({"code": "API_1008_FALLBACK", "details": {"msg": message_text}}))
            else:
                await self.send_status(json.dumps({"code": "API_UNKNOWN_ERROR", "details": {"msg": message_text}}))
        logger.info("💥 Session closed by API Server.")
        await self.disconnected_by_server(expected_session=expected_session)
    
    async def handle_repetition_detected(self):
        """处理重复度检测回调：通知前端"""
        try:
            logger.warning(f"[{self.lanlan_name}] 检测到高重复度对话")
            
            # 向前端发送重复警告消息（使用 i18n key）
            if self.websocket and hasattr(self.websocket, 'client_state') and self.websocket.client_state == self.websocket.client_state.CONNECTED:
                await self.websocket.send_json({
                    "type": "repetition_warning",
                    "name": self.lanlan_name  # 前端会用这个名字填充 i18n 模板
                })
            
        except Exception as e:
            logger.error(f"处理重复度检测时出错: {e}")

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
        """[热切换相关] Helper to reset flags and pending components related to new session prep.
        
        async because we await cancelled tasks to guarantee they have exited
        before clearing references — prevents >2 concurrent OmniRealtimeClient.
        """
        self.is_preparing_new_session = False
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

        if clear_main_cache:
            self.message_cache_for_new_session = []

    async def _cleanup_pending_session_resources(self):
        """[热切换相关] Safely cleans up ONLY PENDING connector and session if they exist AND are not the current main session."""
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

    def _has_custom_tts(self) -> bool:
        """判断当前会话是否使用自定义 TTS（克隆音色或自定义 TTS URL）。"""
        core_config = self._config_manager.get_core_config()
        # 克隆音色始终走 custom 路径；
        # ENABLE_CUSTOM_API + TTS_MODEL_URL 仅在 gptsovitsEnabled 开启时才视为 custom，
        # 否则 caller 会用 tts_custom credentials 启动 default worker，导致鉴权失败。
        if bool(self.voice_id) and not self._is_free_preset_voice:
            return True
        return bool(
            core_config.get('ENABLE_CUSTOM_API')
            and core_config.get('TTS_MODEL_URL')
            and core_config.get('GPTSOVITS_ENABLED')
        )

    def _start_tts_thread(self):
        """创建并启动 TTS Worker 线程。

        根据 voice_id / core_api_type 选择 worker，解析 api_key，
        创建新的 request/response Queue 并启动 daemon 线程。
        调用前/后 tts_ready 被重置为 False，新 worker 需重新发送 __ready__。
        """
        # 重置就绪状态，新 worker 需重新握手
        self.tts_ready = False

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
            api_key = api_key_override or tts_config['api_key']

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
        """检测 TTS Worker 线程已死亡时重新拉起，不阻塞等待就绪。

        新 Worker 就绪后会通过 response_queue 发送 __ready__ 信号，
        由 tts_response_handler 接收并调用 _flush_tts_pending_chunks 刷出缓存。

        限流：12 秒内最多拉起一次，避免服务彻底不可用时风暴式重连。
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
        """将缓存的TTS文本chunk发送到TTS队列"""
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
        """将缓存的输入数据发送到session"""
        async with self.input_cache_lock:
            if not self.pending_input_data:
                return
            
            if self.session and self.is_active:
                for message in self.pending_input_data:
                    try:
                        # 重新调用stream_data处理缓存的数据
                        # 注意：这里直接处理，不再缓存（因为session_ready已设为True）
                        await self._process_stream_data_internal(message)
                    except Exception as e:
                        logger.error(f"💥 发送缓存的输入数据失败: {e}")
                        break
            
            # 清空缓存
            self.pending_input_data.clear()
    
    async def _flush_hot_swap_audio_cache(self):
        """热切换完成后，循环推送缓存的音频数据到新session，直到缓存稳定为空"""
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

    
    def _is_preset_voice_id(self, voice_id: str) -> bool:
        """判断 voice_id 是否属于免费 preset 列表。"""
        if not voice_id:
            return False
        return voice_id in set(get_free_voices().values())

    def _should_block_free_preset_voice(self, voice_id: str, realtime_base_url: str) -> bool:
        """lanlan.app/free 下仅屏蔽 preset 音色，不影响 custom 音色。"""
        return bool(
            self.core_api_type == "free"
            and "lanlan.app" in (realtime_base_url or "")
            and self._is_preset_voice_id(voice_id)
        )

    def _get_voice_id(self) -> str:
        return get_reserved(
            self.lanlan_basic_config[self.lanlan_name],
            'voice_id',
            default='',
            legacy_keys=('voice_id',),
        )

    def _enqueue_voice_migration_notice(self, legacy_names: list) -> None:
        """将语音迁移通知推入缓冲池，委托模块级函数统一去重。"""
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
        """统一的 session 启动失败收口：打日志、发状态码、send_session_failed、cleanup。

        供 start_session 的外层 except 使用，覆盖 prelude（_cleanup_pending_session_resources /
        end_session 等）和 gather 块两条路径，避免前端卡在 preparing。
        """
        self.session_start_failure_count += 1
        self.session_start_last_failure_time = datetime.now()
        logger.error(f"[语音会话诊断] start_session 失败 (总耗时: {time.time() - diag_start:.2f}秒): {e}")
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
        await self.cleanup()

    @property
    def is_starting(self) -> bool:
        """start_session 协程正在运行但 is_active 尚未置 True 的窗口。
        外部（如切猫娘路径）据此判断是否应保留当前 manager 实例，
        避免替换掉一个正在初始化的 manager 造成孤儿 session 泄漏。
        """
        return self._starting_session_count > 0

    async def start_session(self, websocket: WebSocket, new=False, input_mode='audio'):
        # 每次 start_session 都重新获取全局语言，确保 Steam/系统语言变更能即时生效
        self.user_language = normalize_language_code(get_global_language(), format='short')
        # 重置防刷屏标志
        self.session_closed_by_server = False
        self.last_audio_send_error_time = 0.0
        # 检查是否正在启动中
        if self._starting_session_count > 0:
            logger.warning("⚠️ Session正在启动中，忽略重复请求")
            return

        # 标记正在启动（使用计数器，避免并发 start_session 的 finally 互相覆盖）
        self._starting_session_count += 1
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
            raw_voice_id = self._get_voice_id()
            block_free_preset = self._should_block_free_preset_voice(raw_voice_id, realtime_config.get('base_url', ''))
            if block_free_preset:
                self.voice_id = ''
                self._is_free_preset_voice = False
            else:
                self.voice_id = raw_voice_id
                self._is_free_preset_voice = self._is_preset_voice_id(self.voice_id)
            if self._is_free_preset_voice and self.core_api_type != 'free':
                self.voice_id = ''
                self._is_free_preset_voice = False
        
            # 如果角色没有设置 voice_id，尝试使用自定义API配置的 TTS_VOICE_ID 作为回退
            if not self.voice_id:
                # core_config 在单次 start_session 内不会变（改它走 save_core_api → end_session），复用顶部 snapshot
                tts_voice_id = core_config_snapshot.get('TTS_VOICE_ID', '')
                # 过滤掉 GPT-SoVITS 禁用时的占位符（格式: __gptsovits_disabled__|...）
                if core_config_snapshot.get('ENABLE_CUSTOM_API') and tts_voice_id and not tts_voice_id.startswith('__gptsovits_disabled__'):
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
        
            # 重置TTS缓存状态
            async with self.tts_cache_lock:
                self.tts_ready = False
                self.tts_pending_chunks.clear()
        
            # 重置输入缓存状态
            async with self.input_cache_lock:
                self.session_ready = False
                # 注意：不清空 pending_input_data，因为可能已有数据在缓存中
        
            # 根据 input_mode 设置 use_tts
            # 检查是否有自定义 TTS 配置（URL 存在即表示配置了自定义 TTS）—— 复用顶部 snapshot
            has_custom_tts_config = (
                core_config_snapshot.get('ENABLE_CUSTOM_API') and
                core_config_snapshot.get('TTS_MODEL_URL')
            )
        
            if input_mode == 'text':
                # 文本模式总是需要 TTS（使用默认或自定义音色）
                self.use_tts = True
            elif self._is_free_preset_voice and self.core_api_type == 'free' and 'lanlan.tech' in realtime_config.get('base_url', ''):
                # 免费预设音色直接传入 realtime session config 的 voice 字段，不需要外部 TTS
                self.use_tts = False
                logger.info(f"🆓 免费预设音色 '{self.voice_id}' 将直接传入 session config，不启动外部 TTS")
            elif self.voice_id or has_custom_tts_config:
                # 语音模式下：有自定义音色 或 配置了自定义TTS时，使用外部TTS
                self.use_tts = True
                if has_custom_tts_config and not self.voice_id:
                    logger.info("🔊 语音模式：检测到自定义TTS配置，将使用自定义TTS覆盖原生语音")
            else:
                # 语音模式下无自定义音色且无自定义TTS配置，使用 realtime API 原生语音
                self.use_tts = False
        
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
                """异步启动 TTS 进程并等待就绪"""
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
                """独立任务：取 /new_dialog 响应。在 gather 之前就 kick off，
                主动避开 TTS worker 启动时的 GIL 争用窗口。"""
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

            # 定义 LLM Session 启动协程
            async def start_llm_session():
                """异步创建并连接 LLM Session.

                Uses connect-then-assign: a local new_session is created and connected
                first.  Only after connect() succeeds is it promoted to self.session.
                On failure the half-initialised session is closed and an exception raised.
                """
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

                # 等待上面预先发出的 /new_dialog 完成
                try:
                    _nd_text = await _new_dialog_task
                    initial_prompt += _nd_text + _loc(CONTEXT_SUMMARY_READY, _lang).format(name=self.lanlan_name, master=self.master_name)
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
                        on_text_delta=self.handle_text_data,
                        on_input_transcript=self.handle_input_transcript,
                        on_output_transcript=self.handle_output_transcript,
                        on_connection_error=self.handle_connection_error,
                        on_response_done=self.handle_response_complete,
                        on_repetition_detected=self.handle_repetition_detected,
                        on_response_discarded=self.handle_response_discarded,
                        on_status_message=self.send_status,
                        max_response_length=guard_max_length,
                        lanlan_name=self.lanlan_name,
                        master_name=self.master_name
                    )
                    new_session.on_proactive_done = self.handle_proactive_complete
                else:
                    realtime_config = self._config_manager.get_model_api_config('realtime')
                    new_session = OmniRealtimeClient(
                        base_url=realtime_config.get('base_url', ''),
                        api_key=realtime_config['api_key'],
                        model=realtime_config['model'],
                        voice=self.voice_id if self._is_free_preset_voice and self.core_api_type == 'free' 
                            and 'lanlan.tech' in realtime_config.get('base_url', '') else None,
                        on_text_delta=self.handle_text_data,
                        on_audio_delta=self.handle_audio_data,
                        on_new_message=self.handle_new_message,
                        on_input_transcript=self.handle_input_transcript,
                        on_output_transcript=self.handle_output_transcript,
                        on_connection_error=self.handle_connection_error,
                        on_response_done=self.handle_response_complete,
                        on_silence_timeout=self.handle_silence_timeout,
                        on_status_message=self.send_status,
                        on_repetition_detected=self.handle_repetition_detected,
                        api_type=self.core_api_type
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
                    else:
                        concurrent_winner = True

                if concurrent_winner:
                    logger.warning("⚠️ start_llm_session: 检测到并发 start_session 已抢先建立 session，关闭本次 new_session 避免孤儿泄漏")
                    try:
                        await new_session.close()
                    except Exception as _close_err:
                        logger.error(f"💥 关闭并发落败的 new_session 失败: {_close_err}")
                    # 返回哨兵（而非 raise）以绕开 start_session 的通用 except：后者会调
                    # cleanup()（无 expected_session 守卫），反过来拆掉赢家的 session/ws，
                    # 还会 +1 session_start_failure_count 并向前端发 SESSION_START_FAILED。
                    return _START_LLM_CONCURRENT_ABORTED

                logger.info("✅ LLM Session 已连接")
                logger.info(f"[语音会话诊断] LLM 连接并 connect 完成 (耗时: {time.time() - _llm_create_start:.2f}秒)")
                print(initial_prompt)  #只在控制台显示，不输出到日志文件
                return True
        
            # 重置状态
            if new:
                self.message_cache_for_new_session = []
                self.last_time = None
                self.is_preparing_new_session = False
                self.summary_triggered_time = None
                self.initial_cache_snapshot_len = 0
                # 清空输入缓存（新对话时不需要保留旧的输入）
                async with self.input_cache_lock:
                    self.pending_input_data.clear()

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
                
                # 启动成功，重置失败计数器
                self.session_start_failure_count = 0
                self.session_start_last_failure_time = None
                self._memory_error_retry_after = 0

                logger.info(f"[语音会话诊断] 即将通知前端 session_started (start_session 总耗时: {time.time() - _diag_start:.2f}秒)")
                # 通知前端 session 已成功启动
                await self.send_session_started(input_mode)

                # 标记session为就绪状态并处理可能已缓存的输入数据
                async with self.input_cache_lock:
                    self.session_ready = True

                # 处理在session启动期间可能已经缓存的输入数据
                await self._flush_pending_input_data()

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
        """发送用户活动信号，附带被打断的 speech_id 用于精确打断控制"""
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
        """[热切换相关] 将cache转换为字符串"""
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
        """[热切换相关] 后台预热pending session"""

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
            raw_voice_id = self._get_voice_id()
            block_free_preset = self._should_block_free_preset_voice(raw_voice_id, realtime_config.get('base_url', ''))
            if block_free_preset:
                self.voice_id = ''
                self._is_free_preset_voice = False
            else:
                self.voice_id = raw_voice_id
                self._is_free_preset_voice = self._is_preset_voice_id(self.voice_id)
            if self._is_free_preset_voice and self.core_api_type != 'free':
                self.voice_id = ''
                self._is_free_preset_voice = False
            
            # 如果角色没有设置 voice_id，尝试使用自定义API配置的 TTS_VOICE_ID 作为回退
            if not self.voice_id:
                # 复用本次热切换准备顶部的 snapshot（save_core_api 会 end_session 才能改 core_config）
                tts_voice_id = core_config_snapshot.get('TTS_VOICE_ID', '')
                # 过滤掉 GPT-SoVITS 禁用时的占位符（格式: __gptsovits_disabled__|...）
                if core_config_snapshot.get('ENABLE_CUSTOM_API') and tts_voice_id and not tts_voice_id.startswith('__gptsovits_disabled__'):
                    self.voice_id = tts_voice_id
                    logger.info(f"🔄 热切换准备: 使用自定义TTS回退音色: '{self.voice_id}'")
                    self._is_free_preset_voice = False
            
            if old_voice_id != self.voice_id:
                logger.info(f"🔄 热切换准备: voice_id已更新: '{old_voice_id}' -> '{self.voice_id}'")
            
            # 根据input_mode创建对应类型的pending session
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
                    on_input_transcript=self.handle_input_transcript,
                    on_output_transcript=self.handle_output_transcript,
                    on_connection_error=self.handle_connection_error,
                    on_response_done=self.handle_response_complete,
                    on_repetition_detected=self.handle_repetition_detected,
                    on_response_discarded=self.handle_response_discarded,
                    on_status_message=self.send_status,
                    max_response_length=guard_max_length,
                    lanlan_name=self.lanlan_name,
                    master_name=self.master_name
                )
                self.pending_session.on_proactive_done = self.handle_proactive_complete
                logger.info("🔄 热切换准备: 创建文本模式 OmniOfflineClient")
            else:
                # 语音模式：使用 OmniRealtimeClient
                realtime_config = self._config_manager.get_model_api_config('realtime')
                self.pending_session = OmniRealtimeClient(
                    base_url=realtime_config.get('base_url', ''),
                    api_key=realtime_config['api_key'],
                    model=realtime_config['model'],
                    voice=self.voice_id if self._is_free_preset_voice and self.core_api_type == 'free'
                        and 'lanlan.tech' in realtime_config.get('base_url', '') else None,
                    on_text_delta=self.handle_text_data,
                    on_audio_delta=self.handle_audio_data,
                    on_new_message=self.handle_new_message,
                    on_input_transcript=self.handle_input_transcript,
                    on_output_transcript=self.handle_output_transcript,
                    on_connection_error=self.handle_connection_error,
                    on_response_done=self.handle_response_complete,
                    on_silence_timeout=self.handle_silence_timeout,
                    on_status_message=self.send_status,
                    on_repetition_detected=self.handle_repetition_detected,
                    api_type=self.core_api_type
                )
                # Apply user's noise reduction preference to the AudioProcessor
                nr_enabled = (await aload_global_conversation_settings()).get('noiseReductionEnabled', True)
                if hasattr(self.pending_session, '_audio_processor') and self.pending_session._audio_processor:
                    self.pending_session._audio_processor.set_enabled(nr_enabled)
                logger.info("🔄 热切换准备: 创建语音模式 OmniRealtimeClient")
            
            initial_prompt = await self._build_initial_prompt()
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
            initial_prompt += resp.text + self._convert_cache_to_str(self.message_cache_for_new_session)
            print(initial_prompt)
            self._bind_session_lifecycle_callbacks(self.pending_session)
            await self.pending_session.connect(initial_prompt, native_audio = not self.use_tts)

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
        """当需要注入额外提示时，如果当前未进入准备流程，立即开始准备并安排renew逻辑。"""
        try:
            if not self.is_preparing_new_session:
                logger.info("Extra Reply: Triggering preparation due to pending extra reply.")
                self.is_preparing_new_session = True
                self.summary_triggered_time = datetime.now()
                self.message_cache_for_new_session = []
                self.initial_cache_snapshot_len = 0
                # 立即启动后台预热，不等待10秒
                self.pending_session_warmed_up_event = asyncio.Event()
                if not self.background_preparation_task or self.background_preparation_task.done():
                    self.background_preparation_task = asyncio.create_task(self._background_prepare_pending_session())
        except Exception as e:
            logger.error(f"💥 Extra Reply: preparation trigger error: {e}")

    # 供主服务调用，更新Agent模式相关开关
    def update_agent_flags(self, flags: dict):
        try:
            for k in ['agent_enabled', 'computer_use_enabled', 'browser_use_enabled', 'user_plugin_enabled', 'openclaw_enabled', 'openfang_enabled']:
                if k in flags and isinstance(flags[k], bool):
                    self.agent_flags[k] = flags[k]
        except Exception:
            pass

    @staticmethod
    def _extract_openclaw_history_entry(message_obj) -> Optional[dict]:
        role_name = type(message_obj).__name__
        if role_name == "HumanMessage":
            role = "user"
        elif role_name == "AIMessage":
            role = "assistant"
        else:
            return None

        raw_content = getattr(message_obj, "content", None)
        text_parts: list[str] = []
        attachments: list[dict] = []

        if isinstance(raw_content, str):
            if raw_content.strip():
                text_parts.append(raw_content.strip())
        elif isinstance(raw_content, list):
            for item in raw_content:
                if not isinstance(item, dict):
                    continue
                item_type = str(item.get("type") or "").strip()
                if item_type in {"text", "input_text", "output_text"}:
                    text = str(item.get("text") or "").strip()
                    if text:
                        text_parts.append(text)
                elif item_type == "image_url":
                    image_url = item.get("image_url")
                    if isinstance(image_url, dict):
                        url = str(image_url.get("url") or "").strip()
                    else:
                        url = str(item.get("url") or "").strip()
                    if url:
                        attachments.append({"type": "image_url", "url": url})

        if not text_parts and not attachments:
            return None

        entry = {
            "role": role,
            "content": "\n".join(text_parts).strip(),
        }
        if attachments:
            entry["attachments"] = attachments
        return entry

    def _build_openclaw_handoff_messages(self, user_text: str) -> list[dict]:
        messages: list[dict] = []
        history = getattr(self.session, "_conversation_history", None)
        if isinstance(history, list):
            for item in history[-6:]:
                entry = self._extract_openclaw_history_entry(item)
                if entry:
                    messages.append(entry)

        attachments: list[dict] = []
        pending_images = getattr(self.session, "_pending_images", None)
        if isinstance(pending_images, list):
            for image_b64 in pending_images:
                image_b64 = str(image_b64 or "").strip()
                if image_b64:
                    attachments.append({
                        "type": "image_url",
                        "url": f"data:image/jpeg;base64,{image_b64}",
                    })

        current = {"role": "user", "content": str(user_text or "").strip()}
        if attachments:
            current["attachments"] = attachments
        if current["content"] or attachments:
            messages.append(current)
        return messages[-6:]

    def _fallback_should_handoff_to_openclaw(self, user_text: str) -> bool:
        pending_images = getattr(self.session, "_pending_images", None)
        if isinstance(pending_images, list) and any(str(item or "").strip() for item in pending_images):
            return True

        text = str(user_text or "").strip().lower()
        if not text:
            return False

        strong_keywords = (
            "帮我查", "查下", "查一下", "查一查", "找下", "找一下", "搜一下", "搜索",
            "打开", "浏览", "查看", "整理", "下载", "截图", "图片", "照片",
            "文件", "文件夹", "桌面", "代码", "报错", "修复", "天气", "新闻",
            "search", "find", "look up", "browse", "open ", "openclaw", "qwenpaw",
        )
        return any(token in text for token in strong_keywords)

    async def _should_handoff_text_to_openclaw(self, user_text: str) -> tuple[bool, list[dict]]:
        if not (
            self._is_agent_enabled()
            and self.agent_flags.get("openclaw_enabled", False)
            and isinstance(self.session, OmniOfflineClient)
        ):
            return False, []

        messages = self._build_openclaw_handoff_messages(user_text)
        if not messages:
            return False, []

        payload = {
            "lanlan_name": self.lanlan_name,
            "messages": messages,
            "conversation_id": uuid4().hex,
            "lang": normalize_language_code(self.user_language, format='short') or "zh",
        }

        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(12.0, connect=2.0),
                proxy=None,
                trust_env=False,
            ) as client:
                resp = await client.post(
                    f"http://127.0.0.1:{TOOL_SERVER_PORT}/openclaw/preflight",
                    json=payload,
                )
                if resp.status_code != 200:
                    logger.debug(
                        "[%s] openclaw preflight rejected: status=%s",
                        self.lanlan_name,
                        resp.status_code,
                    )
                    return self._fallback_should_handoff_to_openclaw(user_text), messages
                data = resp.json() if resp.content else {}
                return bool(data.get("should_handoff")), messages
        except Exception as e:
            logger.debug("[%s] openclaw preflight failed: %s", self.lanlan_name, e)
            return self._fallback_should_handoff_to_openclaw(user_text), messages

    async def _dispatch_openclaw_handoff(self, user_text: str, messages: list[dict]) -> bool:
        if not messages:
            return False

        try:
            sent = await publish_analyze_request_reliably(
                lanlan_name=self.lanlan_name,
                trigger="text_preflight_openclaw",
                messages=messages,
                ack_timeout_s=0.8,
                retries=1,
                conversation_id=uuid4().hex,
            )
        except Exception as e:
            logger.info("[%s] openclaw handoff publish failed: %s", self.lanlan_name, e)
            return False

        if not sent:
            return False

        # Text mode → voice tracker hooks would lie. Pass is_voice_source=False
        # so on_voice_rms / on_user_message aren't fired again (text-mode entry
        # already called on_user_message directly with this same data).
        await self.handle_input_transcript(user_text, is_voice_source=False)
        pending_images = getattr(self.session, "_pending_images", None)
        if isinstance(pending_images, list):
            pending_images.clear()
        return True

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
        if self.is_hot_swap_imminent:
            logger.info("[%s] voice proactive nudge skipped: hot-swap imminent", self.lanlan_name)
            return False
        _lang = normalize_language_code(self.user_language, format='short') or 'zh'
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
        """通过 WebSocket 向前端请求最新截图，失败时用后端 pyautogui 兜底。返回 base64（不含前缀）。"""
        # 策略1: 前端 WebSocket 截图
        if self.websocket:
            try:
                loop = asyncio.get_running_loop()
                self._screenshot_future = loop.create_future()
                await self.websocket.send_json({"type": "request_screenshot"})
                b64 = await asyncio.wait_for(self._screenshot_future, timeout=timeout)
                if b64:
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
        """由 WebSocket router 调用，将前端回传的截图交给等待中的 future。"""
        if self._screenshot_future and not self._screenshot_future.done():
            self._screenshot_future.set_result(b64)

    async def prepare_proactive_delivery(self, min_idle_secs: float = 10.0) -> bool:
        """Phase 2 流式输出前的前置检查 + speech_id 生成。返回 True 表示可以继续。"""
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
        """只把文本喂给 TTS 管线，不发送到前端显示。

        expected_speech_id: 若不为 None 且与当前 current_speech_id 不匹配（说明
        调用者所属轮次已被其他路径接管，例如主动搭话流式期间用户打断），丢弃本
        chunk 并返回。lock 内判定以保证与 enqueue 原子，避免 proactive 文本被错
        打上新轮次的 speech_id 流入用户正常回复音频。
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

    async def finish_proactive_delivery(self, full_text: str, expected_speech_id: str | None = None) -> bool:
        """流式完成后收尾：一次性投递完整文本 + 记录历史 + TTS/turn end 信号。

        expected_speech_id: 若不为 None 且在进入 _proactive_write_lock 后与当前
        current_speech_id 不符，说明 Phase 2 流结束到 finish 之间用户已打断并
        接管本轮（stream_text 清了 queue + 换了 sid）。此时前端/history/TTS
        结束信号都必须跳过，否则 proactive 文本气泡会插在用户回复后面、
        history 被污染、TTS done 会误结束用户正在进行的回复。

        返回 True 表示真正落库，False 表示因 sid 变化被跳过。调用方据此短路
        下游副作用（_record_proactive_chat / topic usage / surfaced reflection 等），
        避免把未送达的内容记成"已送达"。
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
                self.session._conversation_history.append(AIMessage(content=full_text))

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
        memory_meta = _build_avatar_interaction_memory_meta(getattr(self, "user_language", None), raw)
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

    async def trigger_agent_callbacks(self) -> None:
        """Proactively deliver pending agent task results via LLM rephrase.

        Design:
        - Text mode (OmniOfflineClient): claims proactive turn via
          ``state.try_start_proactive()`` then calls ``prompt_ephemeral()`` so
          the LLM generates a styled response in the character's voice.
        - Voice mode (OmniRealtimeClient): defers to hot-swap — callbacks are
          kept in pending_extra_replies for injection via prime_context()；
          不参与 SM 状态机（hot-swap 有独立生命周期）。
        - On failure or when the session is busy, restores callbacks so the next
          handle_response_complete() call will retry automatically.
        - 重入与"AI 正在回复"互斥由 SM 的原子 claim 承担；同时与
          ``/api/proactive_chat`` / ``trigger_greeting`` 互为 mutual exclusion。
        """
        sess_type = type(self.session).__name__ if self.session else "None"
        logger.info(
            "[%s] trigger_agent_callbacks enter: session=%s phase=%s pending=%d",
            self.lanlan_name, sess_type, self.state.phase.value, len(self.pending_agent_callbacks),
        )
        if not self.pending_agent_callbacks:
            return

        # Hard delivery contract: trigger_agent_callbacks ONLY consumes
        # proactive callbacks. Passive ones must remain in the queue and
        # surface only at the next user turn via drain_agent_callbacks_for_llm.
        # Without this filter, a passive callback enqueued earlier would get
        # piggy-backed onto any later proactive trigger — silently breaking
        # ``delivery="passive"``'s "don't interrupt" promise.
        proactive_cbs = [
            cb for cb in self.pending_agent_callbacks
            if cb.get("delivery_mode") != "passive"
        ]
        if not proactive_cbs:
            logger.debug(
                "[%s] trigger_agent_callbacks: queue has only passive callbacks (n=%d); deferring to next user turn",
                self.lanlan_name, len(self.pending_agent_callbacks),
            )
            return

        # Voice mode 走 hot-swap，不进 SM proactive 流水线。Drop only the
        # proactive cbs from the queue; passive cbs stay for the next drain.
        if isinstance(self.session, OmniRealtimeClient):
            self.pending_agent_callbacks = [
                cb for cb in self.pending_agent_callbacks
                if cb.get("delivery_mode") == "passive"
            ]
            logger.debug("[%s] trigger_agent_callbacks: voice mode, deferring to hot-swap", self.lanlan_name)
            return

        _lang = normalize_language_code(self.user_language, format='short')
        # Render via _build_callback_instruction on the proactive subset only.
        # Note: this never returns "" while ``proactive_cbs`` is non-empty —
        # the renderer always emits at least the per-group outer header even
        # for callbacks with empty summary/detail. So no empty-instruction
        # early-return is needed (and the previous version incorrectly cleared
        # ``pending_extra_replies`` along the way, which is voice-hot-swap
        # state belonging to a different consumer).
        instruction = _build_callback_instruction(
            proactive_cbs,
            lang=_lang,
            lanlan_name=self.lanlan_name,
            master_name=self.master_name,
            passive=False,
        )
        callbacks_snapshot = list(proactive_cbs)

        # 原子 check-and-claim：若另一路 proactive（router/greeting）在跑或 AI
        # 正在为用户回复，SM 拒绝本次投递，callbacks 留在 pending 下轮重试。
        claim_session = self.session if isinstance(self.session, OmniOfflineClient) else None
        if not await self.state.try_start_proactive(session=claim_session):
            logger.debug(
                "[%s] trigger_agent_callbacks: SM denied claim (phase=%s), re-queuing",
                self.lanlan_name, self.state.phase.value,
            )
            return

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

        try:
            if isinstance(self.session, OmniOfflineClient):
                await self._deliver_agent_callbacks_text(instruction, callbacks_snapshot)
            else:
                ws = self.websocket
                if ws and hasattr(ws, 'client_state') and ws.client_state == ws.client_state.CONNECTED:
                    try:
                        await self.start_session(ws, new=False, input_mode='text')
                    except Exception as e:
                        logger.warning("[%s] trigger_agent_callbacks: auto start_session failed: %s", self.lanlan_name, e)
                if isinstance(self.session, OmniOfflineClient):
                    await self._deliver_agent_callbacks_text(instruction, callbacks_snapshot)
                    logger.debug("[%s] trigger_agent_callbacks: auto text session delivered", self.lanlan_name)
                else:
                    logger.debug("[%s] trigger_agent_callbacks: no websocket/session, keeping for later", self.lanlan_name)
        except Exception as e:
            logger.warning("[%s] trigger_agent_callbacks error: %s", self.lanlan_name, e)
            self.pending_agent_callbacks.extend(callbacks_snapshot)
        finally:
            await self.state.fire(SessionEvent.PROACTIVE_DONE)

    async def _deliver_agent_callbacks_text(self, instruction: str, callbacks_snapshot: list) -> None:
        """Execute prompt_ephemeral on an OmniOfflineClient session inside the
        proactive write lock. Caller holds the SM proactive claim (PHASE1).

        返回 True 当且仅当真正投递。返回 False 的情况：claim 到 lock 之间用户
        抢占（``mark_user_input_preempt`` 在 ``self.lock`` 内翻起 ``_preempted``
        且已轮换 ``current_speech_id`` 到新 user sid），此时不能再覆盖。
        """
        async with self._proactive_write_lock:
            async with self.lock:
                # sticky preempt 复查：与 prepare_proactive_delivery 同样，在持有
                # self.lock 的临界区内判定。USER_INPUT 路径在本锁段内翻 flag 和
                # 写 user sid 是原子的，如果此处 preempt==True 说明用户已抢到
                # 本轮 turn，必须放弃本次 proactive（否则会把用户刚写好的 sid
                # 再覆盖成 proactive sid，污染 TTS/chunk 分发）。
                if self.state.is_proactive_preempted():
                    logger.info("[%s] trigger_agent_callbacks: preempted before sid claim, skipping", self.lanlan_name)
                    self.pending_agent_callbacks.extend(callbacks_snapshot)
                    return
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
            _sid_token = _proactive_expected_sid.set(proactive_sid)
            try:
                delivered = await self.session.prompt_ephemeral(instruction)
            finally:
                _proactive_expected_sid.reset(_sid_token)
            logger.debug("[%s] trigger_agent_callbacks: prompt_ephemeral delivered=%s", self.lanlan_name, delivered)
            if delivered:
                # pending_extra_replies parallels pending_agent_callbacks but
                # is voice-mode-only state. Wiping it on text delivery is the
                # pre-existing behavior — voice hot-swap that races in after
                # text-mode delivery would have nothing to inject anyway.
                self.pending_extra_replies.clear()
            else:
                self.pending_agent_callbacks.extend(callbacks_snapshot)

    def _is_voice_session_active_or_starting(self) -> bool:
        """语音 session 正在启动或已经活跃时返回 True，用于阻止 greeting 干扰语音流。"""
        if self._starting_session_count > 0 and self.input_mode == 'audio':
            return True
        if self.is_active and self.input_mode == 'audio':
            return True
        return False

    async def trigger_greeting(self) -> None:
        """首次连接或切换角色时，根据距上次对话间隔触发主动搭话。

        流程：查询 memory_server 获取间隔 → 构建引导词 → 主动拉起 text session → 投递。
        """
        # ── 守卫：语音 session 正在启动 / 已活跃时，跳过 greeting ──
        if self._is_voice_session_active_or_starting():
            logger.info("[%s] trigger_greeting: voice session active/starting, skipping", self.lanlan_name)
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
        from config.prompts_proactive import get_greeting_prompt, get_time_of_day_hint
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
                    delivered = await self.session.prompt_ephemeral(instruction)
                finally:
                    _proactive_expected_sid.reset(_sid_token)
                logger.info("[%s] trigger_greeting: delivered=%s", self.lanlan_name, delivered)
                # 投递成功后才真正消费节日/周末预算
                # commit 内部会 atomic_write_json 消费预算文件，offload 以免阻塞事件循环
                if delivered and _holiday_token is not None:
                    await asyncio.to_thread(commit_holiday_or_weekend_hint, self.lanlan_name, _holiday_token)
        finally:
            await self.state.fire(SessionEvent.PROACTIVE_DONE)

    def enqueue_agent_callback(self, callback: dict) -> None:
        """Enqueue a structured agent task callback for LLM injection.

        Text mode: drained before the next stream_text call and injected via
        prompt_ephemeral(), OR proactively via trigger_agent_callbacks().
        Voice mode: also appended to pending_extra_replies for hot-swap
        injection via prime_context().
        """
        try:
            self.pending_agent_callbacks.append(callback)
            text = (callback.get("summary") or callback.get("detail") or "").strip()
            if text:
                self.pending_extra_replies.append(text)
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
        if not self.pending_agent_callbacks:
            return ""
        try:
            _lang = normalize_language_code(getattr(self, 'user_language', '') or '', format='short') or get_global_language()
            return _build_callback_instruction(
                self.pending_agent_callbacks,
                lang=_lang,
                lanlan_name=getattr(self, "lanlan_name", "") or "",
                master_name=getattr(self, "master_name", "") or "",
                passive=False,
            )
        finally:
            self.pending_agent_callbacks.clear()

    async def _perform_final_swap_sequence(self):
        """[热切换相关] 执行最终的swap序列"""
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
            incremental_cache = self.message_cache_for_new_session[self.initial_cache_snapshot_len:]
            # 1. Send incremental cache (or a heartbeat) to PENDING session for its *second* ignored response
            if incremental_cache:
                final_prime_text = self._convert_cache_to_str(incremental_cache)
            else:  # Ensure session cycles a turn even if no incremental cache
                final_prime_text = ""  # Initialize to empty string to prevent NameError
                logger.debug(f"🔄 No incremental cache found. 缓存长度: {len(self.message_cache_for_new_session)}, 快照长度: {self.initial_cache_snapshot_len}")

            # 若存在需要植入的额外提示，则指示模型忽略上一条消息，并在下一次响应中统一向用户补充这些提示
            if self.pending_extra_replies and len(self.pending_extra_replies) > 0:
                try:
                    items = "\n".join([f"- {txt}" for txt in self.pending_extra_replies if isinstance(txt, str) and txt.strip()])
                except Exception:
                    items = ""
                _lang = normalize_language_code(self.user_language, format='short')
                final_prime_text += (
                    _loc(CONTEXT_SUMMARY_TASK_HEADER, _lang).format(name=self.lanlan_name, master=self.master_name)
                    + items
                    + _loc(CONTEXT_SUMMARY_TASK_FOOTER, _lang)
                )
                # 清空队列，避免重复注入
                self.pending_extra_replies.clear()
                try:
                    await self.pending_session.prime_context(final_prime_text, skipped=False)
                except (web_exceptions.ConnectionClosed, AttributeError) as e:
                    # pending_session 连接已关闭或websocket为None，放弃整个 swap 操作
                    logger.error(f"💥 Final Swap Sequence: pending_session不可用，放弃swap操作: {e}")
                    await self._cleanup_pending_session_resources()
                    await self._reset_preparation_state(clear_main_cache=True)
                    self.is_hot_swap_imminent = False
                    return
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
            self.current_speech_id = str(uuid4())
            self._tts_done_queued_for_turn = False
            self._tts_done_pending_until_ready = False
            self.session_start_time = datetime.now()
            self._session_turn_count = 0

            # 验证新session的WebSocket是否仍然有效（可能在swap过程中被服务器断开）
            if isinstance(self.session, OmniRealtimeClient) and not self.session.ws:
                # 旧session已关闭无法回滚，抛出异常让 except 块走重建流程
                raise RuntimeError("新session的WebSocket在swap后已失效，热切换失败")

            # ── 步骤 4：启动新 listener ───────────────────────────────────────────
            if self.session and hasattr(self.session, 'handle_messages'):
                self.message_handler_task = asyncio.create_task(self.session.handle_messages())

            # ── 步骤 5：flush 热切换音频缓存到新 session ─────────────────────────
            # 必须在 promote 之后调用：_flush_hot_swap_audio_cache 使用 self.session
            # 发送音频，此时 self.session 已是新 session，音频会正确发往新会话。
            await self._flush_hot_swap_audio_cache()

        
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
        input_type = message.get("input_type")
        
        # 检查session是否就绪
        async with self.input_cache_lock:
            if not self.session_ready:
                # 检查是否正在启动session - 只有在启动过程中才缓存
                if self._starting_session_count > 0:
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
                logger.info(f"Session未就绪且不存在，根据输入类型 {input_type} 自动创建 session")
                # 根据输入类型确定模式
                mode = 'text' if input_type == 'text' else 'audio'
                await self.start_session(self.websocket, new=False, input_mode=mode)
                
                # 检查启动是否成功
                if not self.session or not self.is_active:
                    logger.warning("⚠️ Session启动失败，放弃本次数据流")
                    return
        
        # Session已就绪，直接处理
        await self._process_stream_data_internal(message)
    
    async def _process_stream_data_internal(self, message: dict):
        """内部方法：实际处理stream_data的逻辑"""
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
            # 检查失败计数器和冷却时间
            if self.session_start_failure_count >= self.session_start_max_failures:
                # 达到最大失败次数，检查是否已过冷却期
                if self.session_start_last_failure_time:
                    time_since_last_failure = (datetime.now() - self.session_start_last_failure_time).total_seconds()
                    if time_since_last_failure < self.session_start_cooldown_seconds:
                        # 仍在冷却期内，不重试
                        logger.warning(f"Session启动失败过多，冷却中... (剩余 {self.session_start_cooldown_seconds - time_since_last_failure:.1f}秒)")
                        return
                    else:
                        self.session_start_failure_count = 0
                        self.session_start_last_failure_time = None
            
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
            mode = 'text' if input_type == 'text' else 'audio'
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
                    # 先关闭旧 session
                    if self.session:
                        await self.end_session()
                    # 再创建新的文本模式 session
                    await self.start_session(self.websocket, new=False, input_mode='text')
                    
                    # 检查重建是否成功
                    if not self.session or not self.is_active or not isinstance(self.session, OmniOfflineClient):
                        logger.error("💥 文本模式Session重建失败，放弃本次数据流")
                        return
                
                # 文本模式：直接发送文本
                if isinstance(data, str):
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
                    self._activity_tracker.on_user_message(text=data if isinstance(data, str) else None)

                    should_handoff, openclaw_messages = await self._should_handoff_text_to_openclaw(data)
                    if should_handoff:
                        handed_off = await self._dispatch_openclaw_handoff(data, openclaw_messages)
                        if handed_off:
                            logger.info("[%s] text input handed off to openclaw, skipping local LLM reply", self.lanlan_name)
                            return
                        logger.info("[%s] openclaw handoff fallback: publish failed, continue local LLM reply", self.lanlan_name)

                    # 文本模式：在发送用户输入前，将挂起的 agent 任务回调通过
                    # prompt_ephemeral 注入 — 指令不持久化，只保留 AI 回复。
                    if self.pending_agent_callbacks:
                        try:
                            ctx = self.drain_agent_callbacks_for_llm()
                            if ctx:
                                # ``ctx`` already includes its own grouped
                                # SYSTEM_NOTIFICATION_PROACTIVE / PASSIVE outer
                                # headers per (status, source). No extra wrap.
                                await self.session.prompt_ephemeral(ctx)
                                # prompt_ephemeral 通过 on_proactive_done → handle_proactive_complete
                                # 发送 (None, None) 并置 _tts_done_queued_for_turn = True。
                                # 对于 qwen-tts 的 server_commit 模式，需要为主回复生成新的
                                # speech_id（触发 qwen worker 重建连接、重置 buffer_committed），
                                # 并重置 done flag 允许 handle_response_complete 正常发送。
                                async with self.lock:
                                    self.current_speech_id = str(uuid4())
                                    self._tts_done_queued_for_turn = False
                                    self._tts_done_pending_until_ready = False
                        except Exception as _cb_err:
                            logger.warning(f"⚠️ Agent callback injection failed: {_cb_err}")

                    self._active_text_request_id = message.get("request_id")
                    await self.session.stream_text(data)
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
                    # 先关闭旧 session
                    if self.session:
                        await self.end_session()
                    # 再创建新的语音模式 session
                    await self.start_session(self.websocket, new=False, input_mode='audio')
                    
                    # 检查重建是否成功
                    if not self.session or not self.is_active or not isinstance(self.session, OmniRealtimeClient):
                        logger.error("💥 语音模式Session重建失败，放弃本次数据流")
                        return
                
                # 检查WebSocket连接
                if not hasattr(self.session, 'ws') or not self.session.ws:
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
                        if is_48khz and isinstance(self.session, OmniRealtimeClient):
                            # 使用session的AudioProcessor处理音频
                            if hasattr(self.session, '_audio_processor') and self.session._audio_processor:
                                try:
                                    # Use async wrapper to avoid blocking main loop
                                    if hasattr(self.session, 'process_audio_chunk_async'):
                                        processed_audio = await self.session.process_audio_chunk_async(audio_bytes)
                                    else:
                                        # Fallback (should not happen if client updated)
                                        processed_audio = self.session._audio_processor.process_chunk(audio_bytes)
                                        
                                    # RNNoise可能返回空字节（缓冲中），跳过
                                    if len(processed_audio) == 0:
                                        return
                                except Exception as e:
                                    logger.error(f"💥 音频预处理失败: {e}")
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
                        if not self.session or not hasattr(self.session, 'ws') or not self.session.ws:
                            # 限流log：2秒内只记录一次
                            current_time = asyncio.get_event_loop().time()
                            if current_time - self.last_audio_send_error_time > self.audio_error_log_interval:
                                logger.warning("⚠️ Session已关闭，跳过音频数据发送")
                                self.last_audio_send_error_time = current_time
                            return
                        
                        # 检查致命错误状态
                        if hasattr(self.session, '_fatal_error_occurred') and self.session._fatal_error_occurred:
                            current_time = asyncio.get_event_loop().time()
                            if current_time - self.last_audio_send_error_time > self.audio_error_log_interval:
                                logger.warning("⚠️ Session已发生致命错误，跳过音频数据发送")
                                self.last_audio_send_error_time = current_time
                            return
                        
                        # 发送音频到session（stream_audio会检测是否48kHz，16kHz不会再处理）
                        await self.session.stream_audio(processed_audio)
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

            elif input_type in ['screen', 'camera']:
                try:
                    # 使用统一的屏幕分享工具处理数据（只验证，不缩放）
                    image_b64 = await process_screen_data(data)

                    if image_b64:
                        # 叠加 Avatar 文字注解（仅当本条消息携带了位置元数据时）
                        # 不回退到 self._avatar_position：前端未附带位置说明该截图不应叠加
                        # （如窗口截图、手机相机等场景）
                        av_pos = message.get('avatar_position')
                        if av_pos and isinstance(av_pos, dict):
                            try:
                                from utils.language_utils import get_global_language_full
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

                        # 如果是语音模式（OmniRealtimeClient），检查是否支持视觉并直接发送
                        elif isinstance(self.session, OmniRealtimeClient):
                            # 检查WebSocket连接
                            if not hasattr(self.session, 'ws') or not self.session.ws:
                                logger.error("💥 Stream: Session websocket not available")
                                return

                            # 语音模式直接发送图片
                            await self.session.stream_image(image_b64)
                    else:
                        logger.error("💥 Stream: 屏幕数据验证失败")
                        return
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.error(f"💥 Stream: Error processing screen data: {e}")
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

        if _inactive_early:
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

        self.last_time = None
        if not by_server:
            await self.send_status(json.dumps({"code": "CHARACTER_LEFT", "details": {"name": self.lanlan_name}}))
            logger.info("End Session: Resources cleaned up.")

    async def cleanup(self, expected_websocket=None, *, expected_session=None):
        """
        清理 session 资源。
        
        Args:
            expected_websocket: 可选，期望的 websocket 实例。
                               如果提供且与当前 websocket 不匹配，跳过 cleanup。
                               用于防止旧连接误清理新连接的资源（竞态条件保护）。
            expected_session: 可选，期望的 session 实例。
                             来自生命周期回调的会话级守卫，传递给 end_session。
        """
        if expected_websocket is not None and self.websocket is not None:
            if self.websocket != expected_websocket:
                logger.info("⏭️ cleanup 跳过：当前 websocket 已被新连接替换")
                return
        
        await self.end_session(by_server=True, expected_session=expected_session)
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
        """获取翻译服务实例（延迟初始化）"""
        if self._translation_service is None:
            from utils.language_utils import get_translation_service
            self._translation_service = get_translation_service(self._config_manager)
        return self._translation_service
    
    def set_user_language(self, language: str):
        """
        设置用户语言（复用 normalize_language_code 进行归一化）
        
        支持的归一化规则：
        - 'zh', 'zh-CN', 'zh-TW' 等以 'zh' 开头的 → 'zh-CN'
        - 'en', 'en-US', 'en-GB' 等以 'en' 开头的 → 'en'
        - 'ja', 'ja-JP' 等以 'ja' 开头的 → 'ja'
        - 其他语言暂不支持，保持默认 'zh-CN'
        """
        if not language:
            logger.warning(f"语言参数为空，保持当前语言: {self.user_language}")
            return

        # 使用公共函数进行语言代码归一化
        normalized_lang = normalize_language_code(language, format='full')

        self.user_language = normalized_lang
        if normalized_lang != language:
            logger.info(f"用户语言已归一化: {language} → {normalized_lang}")
        else:
            logger.info(f"用户语言已设置为: {normalized_lang}")

        # 文本模式下无需额外同步改写提示语言（已移除 rewrite 逻辑）
    
    async def send_status(self, message: str):
        """发送状态消息到前端。message 应为 JSON 字符串 {"code": "XXX", "details": {...}}，前端通过 i18next 翻译。"""
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
        """通知前端 session 启动失败，让前端隐藏 preparing banner 并重置状态"""
        try:
            if self.websocket and hasattr(self.websocket, 'client_state') and self.websocket.client_state == self.websocket.client_state.CONNECTED:
                data = json.dumps({"type": "session_failed", "input_mode": input_mode})
                await self.websocket.send_text(data)
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.error(f"💥 WS Send Session Failed Error: {e}")

    async def send_avatar_interaction_ack(self, interaction_id: str, accepted: bool, reason: str = '', turn_id: str = ''):
        """向前端确认点触互动的投递结果，便于前端做续发与状态收口。"""
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
        """通知前端 session 已被服务器端终止（如API断连），让前端重置会话状态"""
        try:
            if self.websocket and hasattr(self.websocket, 'client_state') and self.websocket.client_state == self.websocket.client_state.CONNECTED:
                data = json.dumps({"type": "session_ended_by_server", "input_mode": self.input_mode})
                await self.websocket.send_text(data)
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.error(f"💥 WS Send Session Ended By Server Error: {e}")

    async def send_speech(self, tts_audio, speech_id: Optional[str] = None):
        """发送语音数据到前端，先发送 speech_id 头信息用于精确打断控制"""
        try:
            if self.websocket and hasattr(self.websocket, 'client_state') and self.websocket.client_state == self.websocket.client_state.CONNECTED:
                effective_speech_id = speech_id if speech_id is not None else self.current_speech_id
                await self.websocket.send_json({
                    "type": "audio_chunk",
                    "speech_id": effective_speech_id
                })
                await self.websocket.send_bytes(tts_audio)
                logger.debug(f"🔊 send_speech OK: {len(tts_audio)} bytes, speech_id={effective_speech_id}")
                self.sync_message_queue.put({"type": "binary", "data": tts_audio})
            else:
                ws_state = getattr(self.websocket, 'client_state', None) if self.websocket else None
                logger.warning(f"⚠️ send_speech skipped: ws={self.websocket is not None}, state={ws_state}")
        except WebSocketDisconnect:
            logger.warning("⚠️ send_speech: WebSocket disconnected")
        except Exception as e:
            logger.error(f"💥 WS Send Response Error: {e}")

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
                            'UPSTREAM_SERVER_BUSY',
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
                            elif 'policy violation' in error_msg_lower:
                                user_msg = json.dumps({"code": "API_POLICY_VIOLATION", "details": {"msg": error_msg_text}})
                                self._last_tts_error_code = 'API_POLICY_VIOLATION'
                            elif '1008' in error_msg_lower:
                                user_msg = json.dumps({"code": "API_1008_FALLBACK", "details": {"msg": error_msg_text}})
                                self._last_tts_error_code = 'API_1008_FALLBACK'
                            elif ('401' in error_msg_lower or 'unauthorized' in error_msg_lower
                                    or 'authentication' in error_msg_lower
                                    or ('invalid' in error_msg_lower and 'key' in error_msg_lower)):
                                user_msg = json.dumps({"code": "API_KEY_REJECTED", "details": {"msg": error_msg_text}})
                                self._last_tts_error_code = 'API_KEY_REJECTED'
                            else:
                                user_msg = json.dumps({"code": "TTS_CONNECTION_FAILED", "details": {"msg": error_msg_text}})
                                self._last_tts_error_code = 'TTS_CONNECTION_FAILED'
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
                    await self.send_speech(audio_payload, speech_id=speech_id)
                    continue

                size = len(data) if isinstance(data, (bytes, bytearray)) else f"type={type(data).__name__}"
                logger.debug(f"🎧 handler dequeued audio: {size}, qsize≈{q.qsize()}")
                await self.send_speech(data)
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
