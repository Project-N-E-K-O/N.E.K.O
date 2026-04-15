"""
本文件是主逻辑文件，负责管理整个对话流程。当选择不使用TTS时，将会通过OpenAI兼容接口使用Omni模型的原生语音输出。
当选择使用TTS时，将会通过额外的TTS API去合成语音。注意，TTS API的输出是流式输出、且需要与用户输入进行交互，实现打断逻辑。
TTS部分使用了两个队列，原本只需要一个，但是阿里的TTS API回调函数只支持同步函数，所以增加了一个response queue来异步向前端发送音频数据。
"""
import asyncio
import json
import struct  # For packing audio data
import re
import time
from typing import Optional
from datetime import datetime
from websockets import exceptions as web_exceptions
from fastapi import WebSocket, WebSocketDisconnect
from utils.frontend_utils import contains_chinese, replace_blank, replace_corner_mark, remove_bracket, \
    is_only_punctuation, TtsStreamNormalizer
from utils.screenshot_utils import process_screen_data
from main_logic.omni_realtime_client import OmniRealtimeClient
from main_logic.omni_offline_client import OmniOfflineClient
from main_logic.tts_client import get_tts_worker
from utils.preferences import load_global_conversation_settings
from config import MEMORY_SERVER_PORT, TOOL_SERVER_PORT
from config.prompts_sys import (
    _loc,
    SESSION_INIT_PROMPT, SESSION_INIT_PROMPT_AGENT,
    AGENT_TASK_STATUS_RUNNING, AGENT_TASK_STATUS_QUEUED,
    AGENT_TASKS_HEADER, AGENT_TASKS_NOTICE,
    CONTEXT_SUMMARY_READY,
    SYSTEM_NOTIFICATION_TASKS_DONE,
    CONTEXT_SUMMARY_TASK_HEADER, CONTEXT_SUMMARY_TASK_FOOTER,
    AGENT_CALLBACK_NOTIFICATION,
    RESULT_PARSER_PHRASES,
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
from utils.language_utils import normalize_language_code, get_global_language
import threading
from threading import Thread
from queue import Queue
from uuid import uuid4
import numpy as np
import soxr
import httpx

# Setup logger for this module
logger = get_module_logger(__name__, "Main")

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
        self._tts_stream_normalizer = TtsStreamNormalizer()
        self._tts_norm_speech_id: Optional[str] = None
        # 流式音频重采样器（24kHz→48kHz）- 维护内部状态避免 chunk 边界不连续
        self.audio_resampler = soxr.ResampleStream(24000, 48000, 1, dtype='float32')
        self.lock = asyncio.Lock()  # 使用异步锁替代同步锁
        self.websocket_lock = None  # websocket操作的共享锁，由main_server设置
        self._bg_tasks: set = set()  # 防止 fire-and-forget 任务被 GC 回收
        self._screenshot_future: asyncio.Future | None = None
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
        # 防止 trigger_agent_callbacks 重入
        self._agent_delivery_in_progress: bool = False
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
        
        # 防止并发启动的标志
        self.is_starting_session = False

        # TTS缓存机制：确保不丢包
        self.tts_ready = False  # TTS是否完全就绪
        self.tts_pending_chunks = []  # 待处理的TTS文本chunk: [(speech_id, text), ...]
        self.tts_cache_lock = asyncio.Lock()  # 保护缓存的锁
        self._last_tts_respawn_time: float = 0.0  # 上次 respawn 时间戳，用于 12 秒冷却
        self._tts_respawn_task: Optional[asyncio.Task] = None  # 延迟重试 Task，end_session 时取消
        self._last_tts_error_code: str = ''  # 上次 TTS 错误码
        self._tts_retry_notify_count: int = 0  # TTS 重试通知计数，前3次不通知前端
        self._tts_done_queued_for_turn: bool = False  # 防止同一轮次多次排入 TTS 结束信号
        
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
        
        # 用户语言设置（由 start_session 或前端 set_user_language() 设置，初始为 None）
        self.user_language = None
        # 翻译服务（延迟初始化）
        self._translation_service = None
        
        # 防止log刷屏机制
        self.session_closed_by_server = False  # Session被服务器关闭的标志
        self.last_audio_send_error_time = 0.0  # 上次音频发送错误的时间戳
        self.audio_error_log_interval = 2.0  # 音频错误log间隔（秒）

    def _fire_task(self, coro):
        """Create a background task with GC protection (prevent Python 3.11+ from collecting it)."""
        task = asyncio.create_task(coro)
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)
        return task

    def _get_text_guard_max_length(self) -> int:
        try:
            # 优先从对话设置中读取，如果不存在则从核心配置读取
            conversation_settings = load_global_conversation_settings()
            if 'textGuardMaxLength' in conversation_settings:
                value = int(conversation_settings['textGuardMaxLength'])
            else:
                value = int(self._config_manager.get_core_config().get('TEXT_GUARD_MAX_LENGTH', 350))
            # 0 表示无限制，返回一个很大的数
            if value == 0:
                return 999999
            if value < 0:
                raise ValueError
            return value
        except Exception:
            return 350

    def _enqueue_tts_text_chunk(self, speech_id, text: str) -> None:
        """通过 stream normalizer 把一段文本 chunk 入 TTS 队列。

        调用方必须已持有 ``self.tts_cache_lock``（与现有 put 调用点一致）。
        speech_id 变化会触发 normalizer 重置；规范化后若 chunk 为空则不入队，
        避免给 TTS worker 发无意义的空串。控制信号（``__interrupt__`` /
        ``(None, None)``）请继续用 ``tts_request_queue.put`` 直接发送，
        并在合适时机调用 ``_reset_tts_stream_normalizer``。
        """
        if speech_id != self._tts_norm_speech_id:
            self._tts_stream_normalizer.reset()
            self._tts_norm_speech_id = speech_id
        normalized = self._tts_stream_normalizer.feed(text)
        if not normalized:
            return
        self.tts_request_queue.put((speech_id, normalized))

    def _reset_tts_stream_normalizer(self) -> None:
        """清空 normalizer 状态。中断 / 轮次结束 / session 重建时调用。"""
        self._tts_stream_normalizer.reset()
        self._tts_norm_speech_id = None

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

    async def handle_new_message(self):
        """处理新模型输出：清空TTS队列并通知前端"""
        # 重置音频重采样器状态（新轮次音频不应与上轮次连续）
        self.audio_resampler.clear()
        await self._clear_tts_pipeline()
        self._tts_done_queued_for_turn = False  # 新轮次重置 TTS 结束信号标记

        await self.send_user_activity()

        # 立即生成新的 speech_id，确保新回复不会使用被打断的 ID
        # 这样即使 handle_input_transcript 先于 handle_new_message 执行，
        # 新回复的 audio_chunk 也不会被错误丢弃
        async with self.lock:
            self.current_speech_id = str(uuid4())

    async def handle_text_data(self, text: str, is_first_chunk: bool = False):
        """文本回调：处理文本显示和TTS（用于文本模式）"""

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

    async def handle_proactive_complete(self):
        """Lightweight completion for proactive (agent callback) replies.

        Only flushes TTS and sends turn_end to the frontend so that the
        realistic-queue buffer is flushed.  Does NOT trigger hot-swap,
        analyze_request, or agent-callback re-delivery — those belong
        exclusively to user-initiated conversation turns.
        """
        if self.use_tts and self.tts_thread and self.tts_thread.is_alive():
            if self._tts_done_queued_for_turn:
                logger.debug("handle_proactive_complete: TTS done 已排入队列，跳过重复信号")
            else:
                try:
                    self.tts_request_queue.put((None, None))
                    self._tts_done_queued_for_turn = True
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

    async def handle_response_complete(self):
        """Qwen完成回调：用于处理Core API的响应完成事件，包含TTS和热切换逻辑"""

        if self.use_tts and self.tts_thread and self.tts_thread.is_alive():
            if self._tts_done_queued_for_turn:
                logger.debug("📨 Response complete: TTS done 已排入队列，跳过重复信号")
            else:
                logger.info("📨 Response complete (LLM 回复结束)")
                try:
                    self.tts_request_queue.put((None, None))
                    self._tts_done_queued_for_turn = True
                except Exception as e:
                    logger.warning(f"⚠️ 发送TTS结束信号失败: {e}")
        self.sync_message_queue.put({'type': 'system', 'data': 'turn end'})

        # 直接向前端发送turn end消息
        try:
            if self.websocket and hasattr(self.websocket, 'client_state') and self.websocket.client_state == self.websocket.client_state.CONNECTED:
                await self.websocket.send_json({'type': 'system', 'data': 'turn end'})
        except Exception as e:
            logger.error(f"💥 WS Send Turn End Error: {e}")

        # ── 热切换逻辑 ─────────────────────────────────────────────────────────
        # 正在切换过程中则跳过所有热切换判断
        if not self.is_hot_swap_imminent:
            try:
                # 1. 时间/轮次/上下文驱动：任一条件满足 → 开始准备新 session + 触发记忆归档
                if hasattr(self, 'is_preparing_new_session') and not self.is_preparing_new_session:
                    _elapsed = (datetime.now() - self.session_start_time).total_seconds() if self.session_start_time else 0
                    _turn_threshold_met = self._session_turn_count >= 10
                    _ctx_threshold_met = (
                        isinstance(self.session, OmniOfflineClient)
                        and sum(len(str(m.content)) for m in self.session._conversation_history[1:]) >= 10000
                    )
                    if _elapsed >= 40 or _turn_threshold_met or _ctx_threshold_met:
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
        logger.warning(f"[{self.lanlan_name}] 响应异常已丢弃 (reason={reason}, attempt={attempt}/{max_attempts}, will_retry={will_retry})")
        
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
                    "message": message or ""
                })
            except Exception as e:
                logger.warning(f"发送 response_discarded 到前端失败: {e}")

        if self.sync_message_queue:
            self.sync_message_queue.put({
                'type': 'system',
                'data': 'response_discarded_clear'
            })

        # turn end will 由 handle_response_complete 统一发送


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

    async def handle_input_transcript(self, transcript: str):
        """输入转录回调：同步转录文本到消息队列和缓存，并发送到前端显示"""
        # 更新用户活动时间戳（用于主动搭话检测）
        self.last_user_activity_time = time.time()
        # 递增轮次计数器（仅计非空转录，避免噪声/静默误触发记忆整理）
        if transcript.strip():
            self._session_turn_count += 1

        # 推送到同步消息队列
        self.sync_message_queue.put({"type": "user", "data": {"input_type": "transcript", "data": transcript.strip()}})
        
        # 只在语音模式（OmniRealtimeClient）下发送到前端显示用户转录
        # 文本模式下前端会自己显示，无需后端发送，避免重复
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

    async def send_lanlan_response(self, text: str, is_first_chunk: bool = False, turn_id: str | None = None):
        """Qwen输出转录回调: 可用于前端显示/缓存/同步。"""
        text_clean = self.emotion_pattern.sub('', text)
        effective_turn_id = turn_id or self.current_speech_id
        message = {
            "type": "gemini_response",
            "text": text_clean,
            "isNewMessage": is_first_chunk,
            "turn_id": effective_turn_id
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
        for task in tasks_to_await:
            try:
                await asyncio.wait_for(task, timeout=2.0)
            except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                pass
        
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

    def _has_custom_tts(self) -> bool:
        """判断当前会话是否使用自定义 TTS（克隆音色或自定义 TTS URL）。"""
        core_config = self._config_manager.get_core_config()
        return (bool(self.voice_id) and not self._is_free_preset_voice) or bool(
            core_config.get('ENABLE_CUSTOM_API') and core_config.get('TTS_MODEL_URL')
        )

    def _start_tts_thread(self):
        """创建并启动 TTS Worker 线程。

        根据 voice_id / core_api_type 选择 worker，解析 api_key，
        创建新的 request/response Queue 并启动 daemon 线程。
        调用前/后 tts_ready 被重置为 False，新 worker 需重新发送 __ready__。
        """
        # 重置就绪状态，新 worker 需重新握手
        self.tts_ready = False

        has_custom = self._has_custom_tts()
        tts_worker, api_key_override = get_tts_worker(
            core_api_type=self.core_api_type,
            has_custom_voice=has_custom,
            voice_id=self.voice_id or '',
        )
        tts_config = self._config_manager.get_model_api_config(
            'tts_custom' if has_custom else 'tts_default'
        )
        api_key = api_key_override or tts_config['api_key']

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
                req_queue_ref.put((None, None))
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
            if not self.tts_pending_chunks:
                return
            
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

    async def start_session(self, websocket: WebSocket, new=False, input_mode='audio'):
        # 每次 start_session 都重新获取全局语言，确保 Steam/系统语言变更能即时生效
        self.user_language = normalize_language_code(get_global_language(), format='short')
        # 重置防刷屏标志
        self.session_closed_by_server = False
        self.last_audio_send_error_time = 0.0
        # 检查是否正在启动中
        if self.is_starting_session:
            logger.warning("⚠️ Session正在启动中，忽略重复请求")
            return
        
        # 标记正在启动
        self.is_starting_session = True
        
        # 回收残留的热切换资源，防止 main + pending + new-main 叠到 >2 个 session
        await self._cleanup_pending_session_resources()
        await self._reset_preparation_state(clear_main_cache=False)
        
        _diag_start = time.time()
        logger.info(f"[语音会话诊断] 开始 start_session: input_mode={input_mode}, new={new}")
        logger.info(f"启动新session: input_mode={input_mode}, new={new}")
        self.websocket = websocket
        self.input_mode = input_mode
        
        # 立即通知前端系统正在准备（静默期开始）
        await self.send_session_preparing(input_mode)
        
        # 重新读取配置以支持热重载
        # core_api_type 从 realtime 配置获取，支持自定义 realtime API 时自动设为 'local'
        realtime_config = self._config_manager.get_model_api_config('realtime')
        self.core_api_type = realtime_config.get('api_type', '') or self._config_manager.get_core_config().get('CORE_API_TYPE', '')
        self.audio_api_key = self._config_manager.get_core_config()['AUDIO_API_KEY']

        # 每次启动会话前都清理一次无效 voice_id，避免角色配置残留旧音色导致启动异常
        try:
            cleaned_count, legacy_names = self._config_manager.cleanup_invalid_voice_ids()
            if cleaned_count > 0:
                logger.info(f"🧹 start_session 前已清理 {cleaned_count} 个无效 voice_id")
            self._enqueue_voice_migration_notice(legacy_names)
        except Exception as e:
            logger.warning(f"⚠️ start_session 清理无效 voice_id 失败，继续启动会话: {e}")

        # 重新读取角色配置以获取最新的voice_id（支持角色切换后的音色热更新）
        _, _, _, self.lanlan_basic_config, _, _, _, _, _ = self._config_manager.get_character_data()
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
            core_config = self._config_manager.get_core_config()
            tts_voice_id = core_config.get('TTS_VOICE_ID', '')
            # 过滤掉 GPT-SoVITS 禁用时的占位符（格式: __gptsovits_disabled__|...）
            if core_config.get('ENABLE_CUSTOM_API') and tts_voice_id and not tts_voice_id.startswith('__gptsovits_disabled__'):
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
        # 检查是否有自定义 TTS 配置（URL 存在即表示配置了自定义 TTS）
        core_config = self._config_manager.get_core_config()
        has_custom_tts_config = (
            core_config.get('ENABLE_CUSTOM_API') and 
            core_config.get('TTS_MODEL_URL')
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
            await self.end_session(by_server=True)
            # 等待一小段时间确保资源完全释放
            await asyncio.sleep(0.5)
            logger.info("旧session清理完成")
        
        # 如果当前不需要TTS但TTS线程仍在运行，发送停止信号
        if not self.use_tts and self.tts_thread and self.tts_thread.is_alive():
            logger.info("当前模式不需要TTS，关闭TTS线程")
            try:
                self.tts_request_queue.put((None, None))  # 通知线程退出
                self.tts_thread.join(timeout=1.0)  # 等待线程结束
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
                    try:
                        # 非阻塞检查队列
                        if not self.tts_response_queue.empty():
                            msg = self.tts_response_queue.get_nowait()
                            # 检查是否是就绪信号
                            if isinstance(msg, tuple) and len(msg) == 2 and msg[0] == "__ready__":
                                tts_ready = msg[1]
                                if tts_ready:
                                    logger.info(f"✅ TTS进程已就绪 (用时: {time.time() - start_time:.2f}秒)")
                                else:
                                    logger.error("❌ TTS进程初始化失败")
                                break
                            else:
                                # 不是就绪信号，放回队列
                                self.tts_response_queue.put(msg)
                                break
                    except: # noqa
                        pass
                    # worker 线程已死亡则无需继续等待
                    if not self.tts_thread.is_alive():
                        # 尝试取出可能的 ready(False) 信号
                        try:
                            msg = self.tts_response_queue.get_nowait()
                            if isinstance(msg, tuple) and len(msg) == 2 and msg[0] == "__ready__":
                                tts_ready = msg[1]
                        except: # noqa
                            pass
                        if not tts_ready:
                            logger.error("❌ TTS Worker 线程已退出，无法继续等待")
                        break
                    # 每约2秒输出一次诊断日志，便于定位卡在哪一阶段
                    _elapsed = time.time() - start_time
                    if _elapsed - _last_tts_log >= 2.0:
                        _last_tts_log = _elapsed
                        logger.info(f"[语音会话诊断] TTS 就绪等待中... 已等待 {_elapsed:.1f}秒 / {timeout}秒")
                    # 小睡眠避免忙等
                    await asyncio.sleep(0.05)

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

        # 定义 LLM Session 启动协程
        async def start_llm_session():
            """异步创建并连接 LLM Session.
            
            Uses connect-then-assign: a local new_session is created and connected
            first.  Only after connect() succeeds is it promoted to self.session.
            On failure the half-initialised session is closed and an exception raised.
            """
            guard_max_length = self._get_text_guard_max_length()
            _lang = normalize_language_code(self.user_language, format='short')
            initial_prompt = await self._build_initial_prompt()
            
            # 连接 Memory Server 获取记忆上下文
            _mem_start = time.time()
            logger.info(f"[语音会话诊断] 开始获取记忆上下文 (端口 {self.memory_server_port})")
            try:
                async with httpx.AsyncClient(timeout=2.0, proxy=None, trust_env=False) as client:
                    resp = await client.get(f"http://127.0.0.1:{self.memory_server_port}/new_dialog/{self.lanlan_name}")
                    if not resp.is_success:
                        raise ConnectionError(f"❌ 记忆服务返回非2xx状态 {resp.status_code}: {resp.text[:200]}")
                    initial_prompt += resp.text + _loc(CONTEXT_SUMMARY_READY, _lang).format(name=self.lanlan_name, master=self.master_name)
                logger.info(f"[语音会话诊断] 记忆上下文获取完成 (耗时: {time.time() - _mem_start:.2f}秒)")
            except httpx.ConnectError:
                raise ConnectionError(f"❌ 记忆服务未启动！请先启动记忆服务 (端口 {self.memory_server_port})")
            except httpx.TimeoutException:
                raise ConnectionError(f"❌ 记忆服务响应超时！请检查记忆服务是否正常运行 (端口 {self.memory_server_port})")
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
                nr_enabled = load_global_conversation_settings().get('noiseReductionEnabled', True)
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
            
            # Connect succeeded — promote to self.session
            self.session = new_session
            if not self.current_speech_id:
                self.current_speech_id = str(uuid4())
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

        try:
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
            if isinstance(llm_result, Exception):
                raise llm_result  # LLM Session 失败是致命的
            
            # 标记 session 激活
            if self.session:
                async with self.lock:
                    self.is_active = True
                    
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
            # 记录失败
            self.session_start_failure_count += 1
            self.session_start_last_failure_time = datetime.now()
            logger.error(f"[语音会话诊断] start_session 失败 (总耗时: {time.time() - _diag_start:.2f}秒): {e}")
            error_str = str(e)
            
            # 🔴 优先检查 Memory Server 错误（最常见的启动问题）
            is_memory_server_error = isinstance(e, ConnectionError) and any(kw in error_str.lower() for kw in ["memory server", "记忆服务"])
            
            if is_memory_server_error:
                # Memory Server 错误使用专门的日志格式
                logger.error(f"🧠 {error_str}")
                await self.send_status(json.dumps({"code": "MEMORY_SERVER_NOT_RUNNING"}))
                # Memory Server 错误不计入失败次数（因为这是配置问题而非网络问题）
                self.session_start_failure_count -= 1
                # 设置 Memory 专属冷却，避免高频重试刷日志
                self._memory_error_retry_after = time.time() + self._memory_error_cooldown_seconds
            else:
                error_message = f"Error starting session: {e}"
                logger.exception(f"💥 {error_message} (失败次数: {self.session_start_failure_count})")
                
                # 如果达到最大失败次数，发送严重警告并通知前端
                if self.session_start_failure_count >= self.session_start_max_failures:
                    critical_message = f"⛔ Session启动连续失败{self.session_start_failure_count}次，已停止自动重试。请检查网络连接和API配置，然后刷新页面重试。"
                    logger.critical(critical_message)
                    await self.send_status(json.dumps({"code": "SESSION_START_CRITICAL", "details": {"count": self.session_start_failure_count}}))
                else:
                    await self.send_status(json.dumps({"code": "SESSION_START_FAILED", "details": {"error": str(e), "count": self.session_start_failure_count}}))
                
                # 检查其他类型的连接错误
                if 'WinError 10061' in error_str or 'WinError 10054' in error_str:
                    # 检查端口号是否为memory_server端口
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
                elif 'All connection attempts failed' in error_str:
                    await self.send_status(json.dumps({"code": "LLM_CONNECTION_FAILED"}))
                else:
                    await self.send_status(json.dumps({"code": "CONNECTION_CLOSED_ABNORMAL", "details": {"error": error_str}}))
            
            # 通知前端 session 启动失败，让前端重置状态
            # 必须在 cleanup 之前发送，因为 cleanup 会清空 websocket 引用
            await self.send_session_failed(input_mode)
            
            await self.cleanup()
        
        finally:
            # 无论成功还是失败，都重置启动标志
            self.is_starting_session = False

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
        try:
            async with httpx.AsyncClient(timeout=1.5, proxy=None, trust_env=False) as client:
                resp = await client.get(f"http://127.0.0.1:{TOOL_SERVER_PORT}/tasks")
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
            self.core_api_type = realtime_config.get('api_type', '') or self._config_manager.get_core_config().get('CORE_API_TYPE', '')
            self.audio_api_key = self._config_manager.get_core_config()['AUDIO_API_KEY']
            
            # 热切换准备时同样清理无效 voice_id，防止旧版本 voice 残留进入热切换流程
            try:
                cleaned_count, legacy_names = self._config_manager.cleanup_invalid_voice_ids()
                if cleaned_count > 0:
                    logger.info(f"🧹 热切换准备: 已清理 {cleaned_count} 个无效 voice_id")
                self._enqueue_voice_migration_notice(legacy_names)
            except Exception as e:
                logger.warning(f"⚠️ 热切换准备: 清理无效 voice_id 失败，继续准备会话: {e}")

            # 重新读取角色配置以获取最新的voice_id（支持角色切换后的音色热更新）
            _, _, _, self.lanlan_basic_config, _, _, _, _, _ = self._config_manager.get_character_data()
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
                core_config = self._config_manager.get_core_config()
                tts_voice_id = core_config.get('TTS_VOICE_ID', '')
                # 过滤掉 GPT-SoVITS 禁用时的占位符（格式: __gptsovits_disabled__|...）
                if core_config.get('ENABLE_CUSTOM_API') and tts_voice_id and not tts_voice_id.startswith('__gptsovits_disabled__'):
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
                nr_enabled = load_global_conversation_settings().get('noiseReductionEnabled', True)
                if hasattr(self.pending_session, '_audio_processor') and self.pending_session._audio_processor:
                    self.pending_session._audio_processor.set_enabled(nr_enabled)
                logger.info("🔄 热切换准备: 创建语音模式 OmniRealtimeClient")
            
            initial_prompt = await self._build_initial_prompt()
            self.initial_cache_snapshot_len = len(self.message_cache_for_new_session)
            async with httpx.AsyncClient(timeout=2.0, proxy=None, trust_env=False) as client:
                resp = await client.get(f"http://127.0.0.1:{self.memory_server_port}/new_dialog/{self.lanlan_name}")
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
            logger.error(f"💥 BG Prep Stage 1: Error: {error_detail}")
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
                shot = pyautogui.screenshot()
                if shot.mode in ('RGBA', 'LA', 'P'):
                    shot = shot.convert('RGB')
                jpg_bytes = compress_screenshot(shot, target_h=COMPRESS_TARGET_HEIGHT, quality=COMPRESS_JPEG_QUALITY)
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

    async def prepare_proactive_delivery(self, min_idle_secs: float = 30.0) -> bool:
        """Phase 2 流式输出前的前置检查 + speech_id 生成。返回 True 表示可以继续。"""
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
        async with self.lock:
            self.current_speech_id = str(uuid4())
            self._tts_done_queued_for_turn = False
        return True

    async def feed_tts_chunk(self, text: str):
        """只把文本喂给 TTS 管线，不发送到前端显示。"""
        if not self.use_tts:
            return
        async with self.tts_cache_lock:
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

    async def finish_proactive_delivery(self, full_text: str):
        """流式完成后收尾：一次性投递完整文本 + 记录历史 + TTS/turn end 信号。"""
        async with self._proactive_write_lock:
            await self.send_lanlan_response(full_text, is_first_chunk=True)

            from utils.llm_client import AIMessage as _AIMsg
            if self.session and hasattr(self.session, '_conversation_history'):
                self.session._conversation_history.append(_AIMsg(content=full_text))

            if self.use_tts and self.tts_thread and self.tts_thread.is_alive() and not self._tts_done_queued_for_turn:
                try:
                    self.tts_request_queue.put((None, None))
                    self._tts_done_queued_for_turn = True
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
        logger.info("[%s] Proactive stream delivered: %.40s…", self.lanlan_name, full_text)

    async def trigger_agent_callbacks(self) -> None:
        """Proactively deliver pending agent task results via LLM rephrase.

        Design:
        - Text mode (OmniOfflineClient): calls session.prompt_ephemeral() so the
          LLM generates a styled response in the character's voice.
        - Voice mode (OmniRealtimeClient): defers to hot-swap — callbacks are
          kept in pending_extra_replies for injection via prime_context().
        - On failure or when the session is busy, restores callbacks so the next
          handle_response_complete() call will retry automatically.
        - Re-entrance guard prevents concurrent deliveries.
        """
        sess_type = type(self.session).__name__ if self.session else "None"
        logger.info(
            "[%s] trigger_agent_callbacks enter: session=%s delivery_in_progress=%s pending=%d",
            self.lanlan_name, sess_type, self._agent_delivery_in_progress, len(self.pending_agent_callbacks),
        )
        if self._agent_delivery_in_progress:
            logger.debug("[%s] trigger_agent_callbacks: skipped — delivery already in progress", self.lanlan_name)
            return
        if not self.pending_agent_callbacks:
            return

        # Build the instruction from all pending callbacks
        items: list[str] = []
        for cb in self.pending_agent_callbacks:
            status = cb.get("status", "completed")
            summary = (cb.get("summary") or "").strip()
            if not summary:
                continue
            tag = "✅" if status == "completed" else ("⚠️" if status == "partial" else "❌")
            detail = (cb.get("detail") or "").strip()
            if detail and detail != summary and len(detail) > len(summary):
                _cb_lang = normalize_language_code(getattr(self, 'user_language', '') or '', format='short') or get_global_language()
                detail_label = _loc(RESULT_PARSER_PHRASES['detail_result'], _cb_lang)
                items.append(f"{tag} {summary}\n{detail_label}{detail}")
            else:
                items.append(f"{tag} {summary}")

        if not items:
            self.pending_agent_callbacks.clear()
            self.pending_extra_replies.clear()
            return

        _lang = normalize_language_code(self.user_language, format='short')
        instruction = (
            _loc(SYSTEM_NOTIFICATION_TASKS_DONE, _lang).format(name=self.lanlan_name, master=self.master_name)
            + "\n".join(items)
        )

        callbacks_snapshot = list(self.pending_agent_callbacks)

        self._agent_delivery_in_progress = True
        try:
            if isinstance(self.session, OmniRealtimeClient):
                self.pending_agent_callbacks.clear()
                logger.debug("[%s] trigger_agent_callbacks: voice mode, deferring to hot-swap", self.lanlan_name)

            elif isinstance(self.session, OmniOfflineClient):
                if getattr(self.session, "_is_responding", False):
                    logger.debug("[%s] trigger_agent_callbacks: text session busy (_is_responding=True), re-queuing", self.lanlan_name)
                    return
                async with self._proactive_write_lock:
                    async with self.lock:
                        self.current_speech_id = str(uuid4())
                        self._tts_done_queued_for_turn = False
                    logger.debug("[%s] trigger_agent_callbacks: text session ready, calling prompt_ephemeral", self.lanlan_name)
                    # 更新字数限制（可能用户在对话期间修改了设置）
                    if hasattr(self.session, 'update_max_response_length'):
                        self.session.update_max_response_length(self._get_text_guard_max_length())
                    self.pending_agent_callbacks.clear()
                    delivered = await self.session.prompt_ephemeral(instruction)
                    logger.debug("[%s] trigger_agent_callbacks: text session prompt_ephemeral delivered=%s", self.lanlan_name, delivered)
                    if delivered:
                        self.pending_extra_replies.clear()
                    else:
                        self.pending_agent_callbacks.extend(callbacks_snapshot)

            else:
                ws = self.websocket
                if ws and hasattr(ws, 'client_state') and ws.client_state == ws.client_state.CONNECTED:
                    try:
                        await self.start_session(ws, new=False, input_mode='text')
                    except Exception as e:
                        logger.warning("[%s] trigger_agent_callbacks: auto start_session failed: %s", self.lanlan_name, e)
                if isinstance(self.session, OmniOfflineClient):
                    async with self._proactive_write_lock:
                        async with self.lock:
                            self.current_speech_id = str(uuid4())
                            self._tts_done_queued_for_turn = False
                        # 更新字数限制（可能用户在对话期间修改了设置）
                        if hasattr(self.session, 'update_max_response_length'):
                            self.session.update_max_response_length(self._get_text_guard_max_length())
                        self.pending_agent_callbacks.clear()
                        delivered = await self.session.prompt_ephemeral(instruction)
                        if delivered:
                            self.pending_extra_replies.clear()
                        else:
                            self.pending_agent_callbacks.extend(callbacks_snapshot)
                        logger.debug("[%s] trigger_agent_callbacks: auto text session, delivered=%s", self.lanlan_name, delivered)
                else:
                    logger.debug("[%s] trigger_agent_callbacks: no websocket/session, keeping for later", self.lanlan_name)

        except Exception as e:
            logger.warning("[%s] trigger_agent_callbacks error: %s", self.lanlan_name, e)
            self.pending_agent_callbacks.extend(callbacks_snapshot)
        finally:
            self._agent_delivery_in_progress = False

    async def trigger_greeting(self) -> None:
        """首次连接或切换角色时，根据距上次对话间隔触发主动搭话。

        流程：查询 memory_server 获取间隔 → 构建引导词 → 主动拉起 text session → 投递。
        """
        try:
            async with httpx.AsyncClient(timeout=2.0, proxy=None, trust_env=False) as client:
                resp = await client.get(f"http://127.0.0.1:{self.memory_server_port}/last_conversation_gap/{self.lanlan_name}")
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

        _lang = normalize_language_code(self.user_language, format='short')
        from config.prompts_proactive import get_greeting_prompt, get_time_of_day_hint
        from utils.time_format import format_elapsed as _format_elapsed
        from utils.holiday_cache import get_holiday_or_weekend_hint
        template = get_greeting_prompt(gap_seconds, _lang)
        if not template:
            return

        # 先确认投递通道可用，再消费节日预算（避免 session 拉起失败白扣次数）
        # 如果已有 text session 且空闲，直接走投递逻辑
        if isinstance(self.session, OmniOfflineClient) and not getattr(self.session, "_is_responding", False):
            pass
        else:
            # 没有 session 或不是 text session → 主动拉起
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

        # 投递通道已就绪，构建 instruction（此时才消费节日预算）
        elapsed = _format_elapsed(_lang, gap_seconds)
        time_hint = get_time_of_day_hint(_lang).format(master=self.master_name)

        try:
            holiday_hint_text = await get_holiday_or_weekend_hint(_lang, self.lanlan_name)
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

        async with self._proactive_write_lock:
            async with self.lock:
                self.current_speech_id = str(uuid4())
                self._tts_done_queued_for_turn = False
            delivered = await self.session.prompt_ephemeral(instruction)
            logger.info("[%s] trigger_greeting: delivered=%s", self.lanlan_name, delivered)

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
        """
        if not self.pending_agent_callbacks:
            return ""
        try:
            _lang = normalize_language_code(getattr(self, 'user_language', '') or '', format='short') or get_global_language()
            lines: list[str] = []
            for cb in self.pending_agent_callbacks:
                status = cb.get("status", "completed")
                summary = (cb.get("summary") or "").strip()
                detail = (cb.get("detail") or "").strip()
                if status == "completed":
                    tag = _loc(RESULT_PARSER_PHRASES['task_completed'], _lang)
                elif status == "partial":
                    tag = _loc(RESULT_PARSER_PHRASES['task_partial'], _lang)
                else:
                    tag = _loc(RESULT_PARSER_PHRASES['task_failed_tag'], _lang)
                lines.append(f"{tag} {summary}")
                if detail and detail != summary:
                    prefix = _loc(RESULT_PARSER_PHRASES['detail_prefix'], _lang)
                    lines.append(f"{prefix}{detail[:300]}")
            return "\n".join(lines)
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
            
            # 执行session切换
            # 热切换完成后，立即将缓存的音频数据发送到新session
            await self._flush_hot_swap_audio_cache()
            self.session = self.pending_session
            self.current_speech_id = str(uuid4())
            self._tts_done_queued_for_turn = False
            self.session_start_time = datetime.now()
            self._session_turn_count = 0
            
            # !!CRITICAL!! 立即清除pending_session引用，防止异常处理器误关闭新session
            # 此时self.session和self.pending_session指向同一对象（新session）
            # 如果在此之后发生异常，_cleanup_pending_session_resources()会关闭pending_session
            # 导致新session的websocket被关闭，引发 'NoneType' object has no attribute 'send' 错误
            self.pending_session = None

            # Start the main listener for the NEWLY PROMOTED self.session
            if self.session and hasattr(self.session, 'handle_messages'):
                self.message_handler_task = asyncio.create_task(self.session.handle_messages())
            
            # 验证新session的WebSocket是否仍然有效（可能在swap过程中被服务器断开）
            if isinstance(self.session, OmniRealtimeClient):
                if not self.session.ws:
                    logger.error("💥 Final Swap Sequence: 新session的WebSocket在swap后已失效，热切换失败")
                    # 不强制回滚，让系统通过现有错误处理机制自动重建session
                    # 注意：此时旧session已关闭，无法回滚

            # 关闭旧session - 必须先关闭WebSocket再取消task
            # 因为handle_messages使用 async for message in self.ws，只有关闭ws才能让循环退出
            if old_main_session:
                try:
                    # 先关闭WebSocket，让async for循环自然退出
                    await old_main_session.close()
                except Exception as e:
                    logger.error(f"💥 Final Swap Sequence: Error closing old session: {e}")
            
            # 然后取消和等待旧session的消息处理任务完成
            if old_main_message_handler_task and not old_main_message_handler_task.done():
                old_main_message_handler_task.cancel()
                try:
                    await asyncio.wait_for(old_main_message_handler_task, timeout=2.0)
                    logger.info("Final Swap Sequence: Old message handler task stopped")
                except asyncio.TimeoutError:
                    logger.warning("Final Swap Sequence: Old message handler task cancellation timeout (should not happen now)")
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    logger.error(f"💥 Final Swap Sequence: Error during old message handler cleanup: {e}")

        
            # Reset all preparation states and clear the *main* cache now that it's fully transferred
            # pending_session已在swap后立即清除，这里只需要重置其他状态
            await self._reset_preparation_state(
                clear_main_cache=True, from_final_swap=True)  # This will clear pending_*, is_preparing_new_session, etc. and self.message_cache_for_new_session
            logger.info("✅ 热切换完成")
            

        except asyncio.CancelledError:
            logger.info("Final Swap Sequence: Task cancelled.")
            # If cancelled mid-swap, state could be inconsistent. Prioritize cleaning pending.
            self.is_hot_swap_imminent = False  # Reset flag immediately
            await self._cleanup_pending_session_resources()
            await self._reset_preparation_state(clear_main_cache=True)  # Clear all state for clean restart after cancellation
            # The old main session listener might have been cancelled, needs robust restart if still active
            if self.is_active and self.session and hasattr(self.session, 'handle_messages') and (not self.message_handler_task or self.message_handler_task.done()):
                self.message_handler_task = asyncio.create_task(self.session.handle_messages())

        except Exception as e:
            logger.error(f"💥 Final Swap Sequence: Error: {e}")
            self.is_hot_swap_imminent = False  # Reset flag immediately
            await self.send_status(json.dumps({"code": "INTERNAL_UPDATE_FAILED", "details": {"error": str(e)}}))
            await self._cleanup_pending_session_resources()
            await self._reset_preparation_state(clear_main_cache=True)  # Clear all state for clean restart after error
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
                if self.is_starting_session:
                    # Session正在启动中，缓存输入数据
                    self.pending_input_data.append(message)
                    if len(self.pending_input_data) == 1:
                        logger.info("Session正在启动中，开始缓存输入数据...")
                    else:
                        logger.debug(f"继续缓存输入数据 (总计: {len(self.pending_input_data)} 条)...")
                    return
        
        # 在锁外检查是否需要创建新session（不要在锁内创建session，避免死锁）
        if not self.session_ready and not self.is_starting_session:
            if not self.session or not self.is_active:
                # Memory Server 专属冷却检查
                if self._memory_error_retry_after and time.time() < self._memory_error_retry_after:
                    time_left = int(self._memory_error_retry_after - time.time())
                    self._fire_task(self.send_status(json.dumps({
                        "code": "MEMORY_SERVER_COOLDOWN",
                        "details": {"wait_time": time_left}
                    })))
                    self.sync_message_queue.put({'type': 'system', 'data': 'turn end'})
                    if self.websocket and hasattr(self.websocket, 'client_state') and self.websocket.client_state == self.websocket.client_state.CONNECTED:
                        self._fire_task(self.websocket.send_json({'type': 'system', 'data': 'turn end'}))
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
        if self.is_starting_session:
            logger.debug("Session正在启动中，跳过...")
            return
        
        # 如果 session 不存在或不活跃，检查是否可以自动重建
        if not self.session or not self.is_active:
            # Memory Server 专属冷却检查
            if self._memory_error_retry_after and time.time() < self._memory_error_retry_after:
                time_left = int(self._memory_error_retry_after - time.time())
                self._fire_task(self.send_status(json.dumps({
                    "code": "MEMORY_SERVER_COOLDOWN",
                    "details": {"wait_time": time_left}
                })))
                self.sync_message_queue.put({'type': 'system', 'data': 'turn end'})
                if self.websocket and hasattr(self.websocket, 'client_state') and self.websocket.client_state == self.websocket.client_state.CONNECTED:
                    self._fire_task(self.websocket.send_json({'type': 'system', 'data': 'turn end'}))
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

                    # 文本模式：在发送用户输入前，将挂起的 agent 任务回调通过
                    # prompt_ephemeral 注入 — 指令不持久化，只保留 AI 回复。
                    if self.pending_agent_callbacks:
                        try:
                            ctx = self.drain_agent_callbacks_for_llm()
                            if ctx:
                                await self.session.prompt_ephemeral(
                                    _loc(AGENT_CALLBACK_NOTIFICATION, normalize_language_code(self.user_language, format='short')) + ctx,
                                )
                        except Exception as _cb_err:
                            logger.warning(f"⚠️ Agent callback injection failed: {_cb_err}")

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

    async def end_session(self, by_server=False, *, expected_session=None):  # 与Core API断开连接
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
            # 重置 is_starting_session：如果 start_session 正在执行中（比如卡在预热），
            # 前端超时后发来 end_session，必须解除这个 guard，否则用户手动重试会被
            # 静默丢弃（is_starting_session=True → return），导致"必须重启应用才能恢复"。
            # connect-then-assign 模式已保证不会出现 >2 个 session。
            self.is_starting_session = False
            
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
                logger.error(f"💥 End Session: Error during listener task cancellation: {e}")
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
        import queue as _queue_mod
        q = self.tts_response_queue
        logger.info(f"🎧 tts_response_handler started (queue id={id(q):#x})")
        while True:
            try:
                try:
                    data = q.get_nowait()
                except _queue_mod.Empty:
                    await asyncio.sleep(0.01)
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
                raise
            except Exception as e:
                logger.error(f"💥 tts_response_handler error (will retry): {e}")
                await asyncio.sleep(0.01)
