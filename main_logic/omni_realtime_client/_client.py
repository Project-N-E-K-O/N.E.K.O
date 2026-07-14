# -- coding: utf-8 --
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

from ._shared import (
    Any,
    AudioProcessor,
    Awaitable,
    Callable,
    Dict,
    List,
    OMNI_RECENT_RESPONSES_MAX,
    OnToolCallCallback,
    Optional,
    ToolDefinition,
    TurnDetectionMode,
    _IMAGE_ANALYSIS_PENDING_DESCRIPTION,
    asyncio,
    soxr,
)



from ._tools import _ToolingMixin
from ._audio import _AudioMixin
from ._transport import _TransportMixin
from ._responses import _ResponseMixin
from ._gemini_support import _GeminiMixin


class OmniRealtimeClient(_ToolingMixin, _AudioMixin, _TransportMixin, _ResponseMixin, _GeminiMixin):
    """
    A demo client for interacting with the Omni Realtime API.

    This class provides methods to connect to the Realtime API, send text and audio data,
    handle responses, and manage the WebSocket connection.

    Attributes:
        base_url (str):
            The base URL for the Realtime API.
        api_key (str):
            The API key for authentication.
        model (str):
            Omni model to use for chat.
        voice (str):
            The voice to use for audio output.
        turn_detection_mode (TurnDetectionMode):
            The mode for turn detection.
        on_text_delta (Callable[[str, bool], Awaitable[None]]):
            Callback for text delta events.
            Takes in a string and returns an awaitable.
        on_audio_delta (Callable[[bytes], Awaitable[None]]):
            Callback for audio delta events.
            Takes in bytes and returns an awaitable.
        on_input_transcript (Callable[[str], Awaitable[None]]):
            Callback for input transcript events.
            Takes in a string and returns an awaitable.
        on_interrupt (Callable[[], Awaitable[None]]):
            Callback for user interrupt events, should be used to stop audio playback.
        on_output_transcript (Callable[[str, bool], Awaitable[None]]):
            Callback for output transcript events.
            Takes in a string and returns an awaitable.
        extra_event_handlers (Dict[str, Callable[[Dict[str, Any]], Awaitable[None]]]):
            Additional event handlers.
            Is a mapping of event names to functions that process the event payload.
    """

    def __init__(
        self,
        base_url,
        api_key: str,
        model: str = "",
        voice: str = None,
        turn_detection_mode: TurnDetectionMode = TurnDetectionMode.SERVER_VAD,
        on_text_delta: Optional[Callable[[str, bool], Awaitable[None]]] = None,
        on_audio_delta: Optional[Callable[[bytes], Awaitable[None]]] = None,
        on_new_message: Optional[Callable[[], Awaitable[None]]] = None,
        on_sid_rotate: Optional[Callable[[], Awaitable[None]]] = None,
        on_input_transcript: Optional[Callable[[str], Awaitable[None]]] = None,
        on_output_transcript: Optional[Callable[[str, bool], Awaitable[None]]] = None,
        on_connection_error: Optional[Callable[[str], Awaitable[None]]] = None,
        on_response_done: Optional[Callable[[], Awaitable[None]]] = None,
        on_silence_timeout: Optional[Callable[[], Awaitable[None]]] = None,
        on_status_message: Optional[Callable[[str], Awaitable[None]]] = None,
        on_repetition_detected: Optional[Callable[[], Awaitable[None]]] = None,
        extra_event_handlers: Optional[Dict[str, Callable[[Dict[str, Any]], Awaitable[None]]]] = None,
        api_type: Optional[str] = None,
        on_tool_call: Optional[OnToolCallCallback] = None,
        tool_definitions: Optional[List[ToolDefinition]] = None,
        livestream_mode: bool = False,
    ):
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self._model_lower = model.lower() if model else ''
        self.voice = voice
        self.ws = None
        self.instructions = None
        self.on_text_delta = on_text_delta
        self.on_audio_delta = on_audio_delta
        self.on_new_message = on_new_message
        self.on_sid_rotate = on_sid_rotate
        self.on_input_transcript = on_input_transcript
        self.on_output_transcript = on_output_transcript
        self.turn_detection_mode = turn_detection_mode
        self.on_connection_error = on_connection_error
        self.on_response_done = on_response_done
        self.on_silence_timeout = on_silence_timeout
        self.on_status_message = on_status_message
        self.on_repetition_detected = on_repetition_detected
        self.extra_event_handlers = extra_event_handlers or {}
        self._bg_tasks: set = set()  # 防止 fire-and-forget 任务被 GC 回收

        # Track current response state
        self._current_response_id = None
        self._current_item_id = None
        self._is_responding = False
        # Track printing state for input and output transcripts
        self._is_first_text_chunk = False
        self._is_first_transcript_chunk = False
        self._print_input_transcript = False
        self._output_transcript_buffer = ""
        self._modalities = ["text", "audio"]
        self._audio_in_buffer = False
        self._skip_until_next_response = False
        self._audio_delta_count = 0  # diagnostic: count audio.delta events per session
        self._audio_delta_total = 0  # monotonic diagnostic across responses
        self._last_audio_delta_time = 0.0
        self._input_audio_committed_total = 0  # diagnostic: audio buffer commits observed
        self._last_input_audio_committed_time = 0.0
        self._response_created_total = 0  # diagnostic: response.created events observed
        self._last_response_created_time = 0.0
        self._response_done_total = 0  # diagnostic: response.done events observed
        self._last_response_done_time = 0.0
        self._last_response_transcript = ""
        self._speech_started_total = 0  # diagnostic: server VAD start events observed
        self._speech_stopped_total = 0  # diagnostic: server VAD stop events observed
        # [ISSUE4c] Realtime tool-call flood guard. Unlike OmniOfflineClient
        # (max_tool_iterations=3 per turn), realtime has no per-turn tool-call
        # cap — _send_tool_result unconditionally response.create's, so a weak
        # model can chain function_call → result → function_call indefinitely
        # (observed: minecraft_task fired ~9× in 30s). We can't hot-swap the
        # tool list out (realtime API doesn't support mid-session tool changes),
        # so instead we count tool calls in a sliding time window and, once the
        # window is saturated, short-circuit with a hard STOP warning result
        # (the tool is NOT executed) so the model is told to stop calling tools
        # and just speak. Window-based (not strict per-turn) so paced autonomous
        # self-play (~1 call / 10s via the plugin keep-going nudge) is never
        # blocked — only true bursts are.
        self._recent_tool_call_times: list[float] = []
        # Track image recognition per turn
        self._image_recognized_this_turn = False
        self._image_sent_this_turn = False
        self._image_being_analyzed = False
        self._image_description = _IMAGE_ANALYSIS_PENDING_DESCRIPTION
        self._latest_image_b64 = None  # Cached latest screenshot for proactive injection
        self._proactive_image_consumed = True  # Whether the cached image has been used by a proactive nudge
        self._proactive_injecting = False  # True while prompt_ephemeral is injecting audio — suppresses mic input

        # Silence detection for auto-closing inactive sessions
        # 只在 GLM 和 free API 时启用90秒静默超时，Qwen 和 Step 放行
        self._last_speech_time = None
        self._api_type = api_type or ""
        self._livestream_mode = bool(livestream_mode)
        # 只在 GLM 和 free 时启用静默超时；livestream 模式（主播长会话）整路跳过
        self._enable_silence_timeout = (
            self._api_type.lower() in ['glm', 'free']
            and not self._livestream_mode
        )
        self._silence_timeout_seconds = 90  # 90秒无语音输入则自动关闭
        self._silence_check_task = None
        self._silence_timeout_triggered = False

        # Audio preprocessing with RNNoise for noise reduction
        # Auto-resets after 2 seconds of no speech to prevent state drift
        # Input: 48kHz from PC, 16kHz from mobile
        # Output: 16kHz for API
        self._audio_processor = AudioProcessor(
            input_sample_rate=48000,
            output_sample_rate=16000,
            noise_reduce_enabled=True,  # RNNoise noise reduction + VAD
            on_silence_reset=self._on_silence_reset  # 静音重置时发送 input_audio_buffer.clear
        )

        # ── Uplink (client→provider) sample rate ──────────────────────
        # 内部管线一律 16kHz（RNNoise 降采样到 16k、移动端原生 16k、主动
        # 注入 WAV 也是 16k）。绝大多数 Realtime API（Gemini/Qwen/GLM/Step/
        # Grok/free）都吃 16kHz PCM —— 唯独 OpenAI Realtime 的 PCM 输入
        # *只* 接受 24kHz（GA 文档：audio/pcm 的 rate 固定 24000，不能声明
        # 16000）。否则服务端会把我们的 16k 字节当 24k 解，等于喂模型 1.5×
        # 变速变调的音频，拖累 ASR 与 server VAD。
        # 因此只为 GPT 在「发送前的最后一刻」把 16k 上采到 24k；其余各家
        # _uplink_sample_rate 保持 16000，_uplink_resampler 为 None → 整条
        # 重采样彻底短路，行为与改动前完全一致。
        self._uplink_sample_rate = 24000 if 'gpt' in self._model_lower else 16000
        # 持续型流式重采样器：连续麦克风流必须维持 FIR 状态，否则每个 chunk
        # 边界都会引入伪影（与 AudioProcessor 的 downsample stream 同理）。
        # 一次性的预录 WAV（prompt_ephemeral）走整段无状态重采样，不复用它。
        self._uplink_resampler = (
            soxr.ResampleStream(16000, self._uplink_sample_rate, 1, dtype='float32', quality='HQ')
            if self._uplink_sample_rate != 16000
            else None
        )

        # 静音重置事件异步队列（RNNoise 4秒静音回调用）
        self._silence_reset_pending = False
        # 按“上次语音时间”做静音清 buffer：无 RNNoise 时也生效，与 RESET_TIMEOUT 一致
        self._silence_buffer_clear_seconds = 4.0
        self._last_silence_clear_speech_time = 0.0
        # 叠加本地音量：必须连续 2 秒本地静音才允许 clear，避免 VAD 延迟导致误清
        self._local_quiet_seconds = 2.0
        self._last_local_loud_time = 0.0

        # 重复度检测
        self._recent_responses = []  # 存储最近3轮助手回复
        self._repetition_threshold = 0.8  # 相似度阈值
        self._max_recent_responses = OMNI_RECENT_RESPONSES_MAX  # 最多存储的回复数
        self._current_response_transcript = ""  # 当前回复的转录文本

        # Backpressure control - 防止503过载错误
        self._send_semaphore = asyncio.Semaphore(25)  # 最多25个并发发送
        self._is_throttled = False  # 503检测后节流状态
        self._throttle_until = 0.0  # 节流结束时间戳
        self._throttle_duration = 2.0  # 节流持续时间（秒）
        self._server_busy_count: int = 0  # 503 过载计数，第3次起通知前端

        # Fatal error detection - 检测到致命错误后立即中断
        self._fatal_error_occurred = False  # 致命错误标志

        # Interruption state - suppress output after user interruption until next response
        self._interrupted = False  # 打断状态标志，防止重复消息块
        self._suppressed_delta_logged_resp_id = None  # 限流：每个 response 只记录一次 text.delta 被拦截的日志

        # Native image input rate limiting
        self._last_native_image_time = 0.0  # 上次原生图片输入时间戳

        # Unified VAD for image throttling (priority: server VAD > RNNoise > RMS)
        # All native-image paths use _client_vad_active to adjust send rate
        self._client_vad_active = False  # 语音活动检测（统一标志）
        self._client_vad_last_speech_time = 0.0  # 上次检测到语音的时间戳
        # Grace 从 2.0 提到 6.0：覆盖用户说话时的自然停顿（换气/思考），
        # 避免 prompt_ephemeral 在用户两句话中间的静默缝隙误触发。
        self._client_vad_grace_period = 6.0  # 语音结束后保持活跃的宽限期（秒）
        self._client_vad_threshold = 500  # RMS 能量阈值（int16 范围，fallback用）
        self._speech_detect_start = 0.0  # RNNoise 连续检测到语音的起始时间
        self._speech_sustain_threshold = 0.5  # 需持续 500ms 才算真正说话（防噪音误触）
        self._rnnoise_vad_active = False  # RNNoise VAD 是否正在运行（48kHz + denoiser ok）
        # Fudge 保护专用信号：与 _client_vad_active 解耦，记录"最近任何一帧 RNNoise
        # 判定为语音（>0.4，无需 sustain 500ms）或 server-VAD speech_started"的时刻。
        # 解决两个 _client_vad_active 覆盖不到的窗口：
        #   1. 用户说话首 500ms 还未达 sustain 阈值时
        #   2. 句子间停顿 >grace_period 时 _client_vad_active flip False 的瞬间
        # prompt_ephemeral 在此窗口内直接放弃注入。
        self._user_recent_activity_time = 0.0
        self._user_recent_activity_window = 8.0
        # 对称于 _user_recent_activity_time 的 AI 侧信号。任何一帧 AI 内容下发都打点。
        # 与 _is_responding 正交 —— _is_responding 是 response 生命周期（server 侧
        # response.created/done / Gemini turn_complete 驱动），但下列场景下 content
        # 流与之不同步：
        #   1. OpenAI response.created 到首 content chunk 之间的几百毫秒空窗
        #   2. Gemini turn_complete 早于最后几帧音频送达 → late audio
        #   3. Gemini 长回复被拆多 sub-turn，两个 sub-turn 之间 False 的瞬间
        # prompt_ephemeral 和 Gemini turn 分配分别用此信号兜底 "fudge 打断 AI 自己"
        # 和 "late audio 被当新 turn" 两个 race。不改 _is_responding 语义（它还有
        # 8 个消费者：handle_interruption / QQ 插件 / system_router 409 等），只做正交增量。
        self._ai_recent_activity_time = 0.0
        self._ai_recent_activity_window = 3.0

        # 防止log刷屏机制（当websocket关闭后）
        self._last_ws_none_warning_time = 0.0  # 上次websocket为None警告的时间戳
        self._ws_none_warning_interval = 5.0  # websocket为None警告的最小间隔（秒）

        # Image processing lock
        self._image_lock = asyncio.Lock()

        # Audio processing lock to ensure sequential processing in thread pool
        self._audio_processing_lock = asyncio.Lock()

        # Gemini Live API specific attributes
        self._is_gemini = self._api_type.lower() == 'gemini'

        # Whether this API returns server-side VAD events (speech_started/speech_stopped)
        # Gemini (direct), lanlan.app+free (Gemini proxy), 以及 livestream 模式
        # （主播自建 server_prefix 上游同样是 Gemini 系，不发 OpenAI 协议的 VAD 帧）
        # 一律按"无 server VAD"处理。否则 handle_messages 走不到 speech_stopped
        # 那条 on_new_message 路径，多轮对话 sid 不轮换，TTS 在 turn 2 起静音。
        self._has_server_vad = (
            not self._is_gemini
            and not ('lanlan.app' in (base_url or '') and 'free' in self._model_lower)
            and not bool(livestream_mode)
        )

        # free 经 Gemini 代理（OpenAI-realtime 协议，发图走 input_image_buffer.append、
        # 服务端 VAD 由代理吞掉）：lanlan.app 海外节点，或 livestream 主播自建 server_prefix。
        # 二者上游同为 Gemini 系，原生视觉与发图协议一致；lanlan.tech free 上游是
        # StepFun（无原生视觉，走 VISION_MODEL 分析通道），不在此列。
        self._is_free_proxy = 'free' in self._model_lower and (
            'lanlan.app' in (base_url or '')
            or bool(livestream_mode)
        )

        # Whether this client supports native image input
        # qwen/glm/gpt/gemini have native vision; free Gemini-proxy (lanlan.app / livestream) also does
        self._supports_native_image = (
            any(m in self._model_lower for m in ['qwen', 'glm', 'gpt'])
            or self._is_gemini
            or self._is_free_proxy
        )
        self._gemini_client = None  # genai.Client instance
        self._gemini_session = None  # Live session from SDK
        self._gemini_context_manager = None  # For proper cleanup
        self._gemini_current_transcript = ""  # Current response transcript for Gemini
        self._gemini_user_transcript = ""  # Accumulated user input transcript
        self._gemini_user_transcript_after_interrupt = False

        # ── Tool calling state ────────────────────────────────────────
        # ``_tool_definitions`` is the canonical list (ToolDefinition);
        # the wire-format snapshots are rebuilt from it on each connect/
        # update_session so callers can mutate the list at any time.
        self.on_tool_call: Optional[OnToolCallCallback] = on_tool_call
        self._tool_definitions: List[ToolDefinition] = list(tool_definitions or [])
        # Provider behaviour matrix:
        #   gpt   → flat schema, response.done has output[].type=function_call
        #   glm   → flat schema, response.function_call_arguments.done event
        #           (no call_id — synthesize from response_id+output_index)
        #   step  → nested schema, response.function_call_arguments.done event
        #   free  (lanlan.tech proxies StepFun) → same as step. lanlan.app
        #          proxies Vertex Live and is NOT plumbed yet (server side
        #          strips tools); see TODO in core.py.
        #   qwen  → no custom tool calling per Aliyun docs (only enable_search)
        #   gemini → genai SDK config.tools, response.tool_call.function_calls
        # The provider-side flags below let event handlers cheaply route.
        self._supports_tools_wire = self._api_type.lower() in ('gpt', 'glm', 'qwen', 'step', 'free', 'gemini', 'grok')
        # Per-call accumulator for OpenAI-Realtime / StepFun delta arguments
        # keyed by call_id. cleared on response.done.
        self._inflight_tool_args: Dict[str, Dict[str, Any]] = {}
        # GLM: track response_id+output_index → synthesized call_id since
        # GLM's function_call_arguments.done lacks an explicit call_id field.
        self._glm_tool_index_to_id: Dict[str, str] = {}

        # Proactive inject rejection handlers, keyed by the client-side
        # event_id we stamp on ``response.create``. When the server rejects
        # the request (e.g. ``response_already_active`` from a VAD race), it
        # emits an ``error`` event whose ``error.event_id`` echoes our id —
        # the message loop pops the matching handler and invokes it so the
        # caller (core.trigger_agent_callbacks) can re-enqueue the cb that
        # was optimistically pruned after send. Entries also self-expire to
        # avoid leaks if the server never acks.
        self._inject_rejection_handlers: Dict[str, Callable[[str], None]] = {}
        # One-shot gate for the no-event_id content fallback in
        # ``_route_inject_rejection``. True only between "a proactive inject
        # just sent its ``response.create``" and "that inject's outcome was
        # observed" (rejection fired, or a response lifecycle event arrived).
        # Without this, a no-id ``response_already_active`` from a DIFFERENT
        # ``response.create`` sender (create_response / tool-result /
        # signal_user_activity_end) could content-match a lingering — already
        # succeeded — proactive handler and wrongly re-enqueue its cb.
        self._proactive_inject_awaiting_outcome = False

    def _fire_task(self, coro):
        """Create a background task with GC protection."""
        task = asyncio.create_task(coro)
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)
        return task
