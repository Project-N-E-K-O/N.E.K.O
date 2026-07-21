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
"""``LLMSessionManager`` assembly.

``__init__`` is the single home of every instance attribute (locks,
caches, queues, flags); the domain mixins contribute methods only.
"""

import asyncio
import re
from collections import OrderedDict, deque
from typing import Any, Awaitable, Callable, Optional
from utils.frontend_utils import TtsStreamNormalizer, TtsBracketStripper, TtsMarkdownStripper
from main_logic.tool_calling import ToolRegistry
from main_logic.session_state import SessionStateMachine, SessionEvent
from main_logic.lifecycle_bus import LifecycleEventBus
from main_logic.proactive_delivery import ProactiveDeliveryManager
from config import MEMORY_SERVER_PORT, AVATAR_INTERACTION_DEDUPE_MAX_ITEMS
from utils.config_manager import get_config_manager
from queue import Queue
import soxr
from ._shared import logger, ContextAppendResult

from .context_append import ContextAppendMixin
from .focus import FocusMixin
from .tts_runtime import TtsRuntimeMixin
from .turn import TurnMixin
from .tool_calling import ToolCallingMixin
from .lifecycle import LifecycleMixin
from .proactive import ProactiveMixin
from .greeting import GreetingMixin
from .streaming import StreamingMixin
from .notify import NotifyMixin
from main_logic.asr_client.runtime import AsrRuntimeMixin
from main_logic.asr_client.audio import AudioDurationQueue
from main_logic.asr_client.audio import HotSwapAudioBuffer


# --- 一个带有定期上下文压缩+在线热切换的语音会话管理器 ---
class LLMSessionManager(
    ContextAppendMixin,
    FocusMixin,
    TtsRuntimeMixin,
    TurnMixin,
    ToolCallingMixin,
    LifecycleMixin,
    ProactiveMixin,
    GreetingMixin,
    AsrRuntimeMixin,
    StreamingMixin,
    NotifyMixin,
):
    # Ceiling for a missing voice_play_end before the playback gate self-heals:
    # above a normal single reply, but recovers a dropped end-signal reasonably
    # fast. Disconnect/refresh (the common cause) is already handled by session
    # teardown reset, so this only backstops the rare "connection alive but end
    # signal lost" case. Mirror of ProactiveDeliveryManager.max_play_s.
    _VOICE_PLAYBACK_STALE_S = 45.0

    def __init__(self, sync_message_queue, lanlan_name, lanlan_prompt):
        self.websocket = None
        self.sync_message_queue = sync_message_queue
        self.session = None
        self._init_asr_runtime_state()
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
        self._audio_stream_queue = AudioDurationQueue(
            capacity_us=2_000_000,
            max_frames=256,
        )
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
        self.goodbye_silent_started_monotonic: float = 0.0
        self.goodbye_silent_completed_duration: float | None = None
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
        self.hot_swap_audio_cache = HotSwapAudioBuffer(capacity_ms=8_000)
        self.hot_swap_cache_lock = asyncio.Lock()  # 保护热切换音频缓存的锁
        self.is_flushing_hot_swap_cache = False  # 是否正在推送热切换缓存（推送期间新音频继续缓存）
        
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

        # activity_guess narration 的「下游消费方」门控：narration 只喂 proactive
        # Phase 2，Phase 2 在两种情况下都没人读 → 这次昂贵的 emotion-tier 外呼纯属空烧：
        #   ① goodbye_silent（猫娘挂机静默）—— Phase 2 一进门就 bail；
        #   ② 没有在线 WebSocket（普通断连 / End Session 后）—— proactive 没法送达。
        # tracker 跨 session 长存、心跳不随 End Session 取消（break-reminder / 情境弹窗这些
        # 规则心跳要继续跑），所以必须靠这个门控在「无消费方」时跳过 LLM；否则用户关掉页面
        # 后整段挂机会一直空烧（cap 只是把间隔退避到 ~900s，不会停）。重连 / 退出静默后
        # 每签名 cache 还在，narration 在该签名退避间隔走完时恢复（conv_seq 变化或间隔已
        # 在挂起期间走完则下一 tick 立刻恢复）。
        self._activity_tracker.set_narration_suppressed_check(
            self._should_suppress_activity_narration
        )

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
