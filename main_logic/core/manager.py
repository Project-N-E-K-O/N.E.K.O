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
import time
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
from ._shared import logger, ContextAppendResult, _ReplyTurn

from .context_append import ContextAppendMixin
from .focus import FocusMixin
from .tts_runtime import TtsRuntimeMixin
from .tts_lifecycle import TtsLifecycleMixin
from .turn import TurnMixin
from .tool_calling import ToolCallingMixin
from .lifecycle import LifecycleMixin
from .session_lifecycle import SessionOwnershipMixin
from .proactive import ProactiveMixin
from .greeting import GreetingMixin
from .asr_runtime import AsrRuntimeMixin
from .streaming import StreamingMixin
from .notify import NotifyMixin
from .turn_context import TurnContextMixin
from .takeover import TakeoverMixin, TakeoverToken, HoldToken


# --- Voice session manager with periodic context compression and live hot swapping ---
class LLMSessionManager(
    ContextAppendMixin,
    FocusMixin,
    TtsRuntimeMixin,
    TtsLifecycleMixin,
    TurnMixin,
    ToolCallingMixin,
    LifecycleMixin,
    SessionOwnershipMixin,
    ProactiveMixin,
    GreetingMixin,
    AsrRuntimeMixin,
    StreamingMixin,
    NotifyMixin,
    TakeoverMixin,
    TurnContextMixin,
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
        self._init_session_lifecycle_state()
        self._init_tts_lifecycle_state()
        self._init_asr_runtime_state()
        self.last_time = None
        self.is_active = False
        self.active_session_is_idle = False
        self.current_expression = None
        self.tts_request_queue = Queue()  # TTS request (thread queue).
        self.tts_response_queue = Queue()  # TTS response (thread queue).
        self.tts_thread = None  # TTS thread.
        self._tts_runtime_key = None
        # Runtime fallback excludes only providers that failed in this session;
        # the remaining dispatcher order is never rewritten.
        # Runtime fallback excludes only providers failing this session; preserve remaining dispatch order.
        self._tts_active_provider_key: Optional[str] = None
        self._tts_excluded_provider_keys: frozenset[str] = frozenset()
        # Cross-chunk normalizer: Gemini Live output transcription inserts ASCII spaces between Chinese tokens,
        # breaking Chinese pronunciation in streaming TTS such as MiniMax / CosyVoice. Normalizer
        # removes spaces with replace_blank semantics, deferring trailing chunk spaces for correct boundaries.
        # Note: enabled only for http_sentence TTS providers, whose client-side sentence splitting needs clean text.
        # ws_bistream providers (qwen / step / cosyvoice) send fragments directly server-side;
        # pending_spaces delays + CJK boundary space removal disrupt server processing cadence.
        self._tts_stream_normalizer = TtsStreamNormalizer()
        self._tts_norm_speech_id: Optional[str] = None
        self._tts_normalize_enabled: bool = True  # Enabled by default; _start_tts_thread overrides by provider class.
        # Bracket/Markdown strippers omit bracketed narration and Markdown markers from speech.
        # Independent of _tts_stream_normalizer: CJK space normalization depends on provider
        # (off for ws_bistream), but bracket/Markdown stripping is universal TTS behavior,
        # always enabled. Order: normalizer → markdown → bracket, because
        # Markdown links ``[文本](url)`` must first become ``文本`` before bracket processing,
        # or bracket treats ``[`` ``]`` as ordinary brackets and removes link text too.
        self._tts_markdown_stripper = TtsMarkdownStripper()
        self._tts_bracket_stripper = TtsBracketStripper()
        # Streaming audio resampler (24kHz→48kHz) retains state to avoid chunk-boundary discontinuities.
        self.audio_resampler = soxr.ResampleStream(24000, 48000, 1, dtype='float32')
        self.lock = asyncio.Lock()  # Use asynchronous rather than synchronous lock.
        self.websocket_lock = None  # Shared WebSocket-operation lock, assigned by main_server.
        self._bg_tasks: set = set()  # Prevent garbage collection of fire-and-forget tasks.
        self._screenshot_future: asyncio.Future | None = None
        self._proactive_screenshot_request_id: str | None = None
        # Live browser consent can arrive before the settings POST is persisted.
        self._proactive_vision_enabled: bool | None = None
        self._proactive_vision_revocation_epoch: int = 0
        self._avatar_position: dict | None = None  # Frontend normalized Avatar coordinates {centerX, centerY, width, height}.
        # One-shot handoff slot between request_fresh_screenshot and resolve_screenshot_request.
        # Deliberately separate from _avatar_position, overwritten by every stream_data (screen-share frames,
        # proactive single-frame vision). Pairing it with Phase 2 screenshot would let intervening frames
        # replace coordinates or clear them to None. Only resolve_screenshot_request writes, and only its paired
        # request reads.
        self._pending_screenshot_avatar_position: dict | None = None
        self.current_speech_id = None
        # Per-speech playback overrides are bounded and released on audio_done,
        # interruption, or session teardown. Ordinary speech is absent and
        # therefore keeps the global speaker gain unchanged.
        self._speech_playback_gains: OrderedDict[str, float] = OrderedDict()
        # Mini-game speech preload uses an isolated, short-lived worker so its
        # synthesized chunks never enter the audible response handler. Batches
        # are serialized per character and explicitly cancelled on teardown.
        self._game_speech_preload_lock = asyncio.Lock()
        self._game_speech_preload_pending_batches = 0
        self._game_speech_preload_cancel_epoch = 0
        self._game_speech_preload_active_workers: dict[Thread, Queue] = {}
        # Mini-game audible speech is serialized by the game router through
        # one completion slot.  The slot is resolved by audio_done and cleared
        # on timeout, interruption, or teardown, so it cannot grow per request.
        self._game_speech_completion_waiter: tuple[str, asyncio.Future] | None = None
        # Delivery result for that same serialized speech.  A fixed single
        # slot is enough because the game router never permits two audible
        # game speeches to wait concurrently.  Cleared with the waiter on
        # completion, cancellation, timeout, pipeline reset, or teardown.
        self._game_speech_delivery_state: tuple[str, bool] | None = None
        # Exact SDK playback correlation for the one serialized audible game
        # speech.  Cleared with audio_done, interruption, or TTS teardown.
        self._game_speech_correlation: tuple[str, str] | None = None
        self._speech_output_total = 0  # diagnostic: chunks actually sent to frontend playback
        self._last_speech_output_time = 0.0
        self._last_speech_output_bytes = 0
        # Increment only on user/frontend cancellation of startup (end_session's not by_server +
        # reset_starting_count path), letting cross-mode restart guards distinguish user abandonment from
        # internal cleanup / in-flight startup failure. Do not reuse _audio_stream_epoch:
        # it increments in every end_session cleanup (including by_server=True in-flight failure),
        # misclassifying still-awaited audio requests as abandoned (CodeRabbit).
        self._user_session_abandon_epoch = 0
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
        # Get character-related configuration.
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
        # API configuration is now dynamically fetched via _config_manager.get_model_api_config().
        # core_api_type comes from realtime config, automatically 'local' for custom realtime APIs.
        realtime_config = self._config_manager.get_model_api_config('realtime')
        self.core_api_type = realtime_config.get('api_type', '') or self._config_manager.get_core_config().get('CORE_API_TYPE', '')
        self.memory_server_port = MEMORY_SERVER_PORT
        self.audio_api_key = self._config_manager.get_core_config()['AUDIO_API_KEY']  # For CosyVoice custom voices.
        self._apply_voice_id_for_route()
        # Note: start_session resets use_tts according to input_mode.
        self.use_tts = False
        self.generation_config = {}  # Currently unused for Qwen.
        self.message_cache_for_new_session = []
        self.next_session_context_messages: list[dict] = []
        self.is_preparing_new_session = False
        self.summary_triggered_time = None
        self.initial_cache_snapshot_len = 0
        self._primed_context_snapshot = None
        self.initial_next_session_context_snapshot_len = 0
        self.pending_session_warmed_up_event = None
        self.pending_session_final_prime_complete_event = None
        self.session_start_time = None
        # When the conversation began: set by a real session start, kept by a
        # hot swap (which only renews the provider session). Passive memory
        # recall leaves out facts written since (their turns are in context).
        self._turn_context_conversation_started_at = None
        self._session_turn_count = 0  # User-input turn count for current session.
        self.pending_connector = None
        self.pending_session = None
        # Closing a detached pending session is owned here, not by whoever
        # happened to ask for it: that caller is a background prep task the
        # reset path cancels — twice, once on entry and again when its 2s wait
        # expires — and the second cancel used to land inside the close.
        # Holding the task also keeps it from being garbage collected.
        self._pending_session_close_tasks: set = set()
        self.pending_use_tts = None
        self.is_hot_swap_imminent = False
        self.tts_handler_task = None
        self._tts_handler_response_queue = None
        # Hot-swap variables.
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
        # Structured agent-task callback queue for session-type-specific injection.
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
        # Prevent concurrent WS/sync_message_queue writes by trigger_agent_callbacks and finish_proactive_delivery.
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
        # Backend silence gate while she leaves / becomes a cat. Frontend sets True upon entering cat mode,
        # clears on return or explicit start_session; all proactive entries read it uniformly.
        self.goodbye_silent: bool = False
        self.goodbye_silent_reason: str = ""
        self.goodbye_silent_updated_at: float = 0.0
        self.goodbye_silent_started_monotonic: float = 0.0
        self.goodbye_silent_completed_duration: float | None = None
        # ── Session takeover ──────────────────────────────────────────
        # When an external controller takes over this session, local chat-LLM output
        # （text/audio delta、output transcript、response.complete、
        # new-message notifications) must be muted; first pass voice transcripts to external dispatcher,
        # and skip local chat path when handled.
        # SessionManager knows neither takeover identity nor reason, only these three attributes.
        # Only TakeoverMixin (acquire_takeover / release_takeover /
        # set_takeover_callback_sink) writes them; _takeover_token identifies current holder,
        # and releases with other tokens do nothing, preventing controllers from unmuting one another.
        # Currently consumed only by main_routers.game_router; future plugin/agent full-chat
        # takeovers share this interface.
        self._takeover_token: Optional[TakeoverToken] = None
        self._takeover_active: bool = False
        self._takeover_input_dispatcher: Optional[
            Callable[..., Awaitable[bool]]
        ] = None
        # Respond-callback sink during takeover: True means external controller accepted it,
        # bypassing proactive_manager. None preserves default queuing until takeover release.
        self._takeover_callback_sink: Optional[Callable[[dict], bool]] = None
        # Callback hold independent of takeover (TakeoverMixin.hold_callbacks): after release,
        # respond callbacks may still enter this sink rather than ordinary proactive delivery.
        self._callback_hold_sink: Optional[Callable[[dict], bool]] = None
        self._callback_hold_token: Optional[HoldToken] = None
        # Frontend-controlled Agent switches.
        self.agent_flags = {
            'agent_enabled': False,
            'computer_use_enabled': False,
            'browser_use_enabled': False,
            'user_plugin_enabled': False,
            'openclaw_enabled': False,
            'openclaw_ready': False,
        }
        
        # Mode flag: 'audio' or 'text'.
        self.input_mode = 'audio'
        # Input ownership and response backend are independent. Independent ASR
        # may keep the audio microphone route alive after the answering session
        # has been promoted from Realtime to an Offline VLM.
        self.response_backend = 'realtime'
        self._multimodal_handoff_lock = asyncio.Lock()
        
        # Create audio-mode session by default at initialization.
        self.session = None
        
        # Guard against infinite retries.
        self.session_start_failure_count = 0
        self.session_start_last_failure_time = None
        self.session_start_cooldown_seconds = 3.0  # Cooldown: 3 seconds.
        self.session_start_max_failures = 3  # Maximum consecutive failures.
        # Circuit breaker: max_failures requires explicit user start_session (refresh/retry)
        # to clear. Internal recovery paths return early meanwhile, avoiding log flooding.
        self._session_start_circuit_open = False
        self._memory_error_retry_after = 0  # Memory Server-specific cooldown timestamp.
        self._memory_error_cooldown_seconds = 10  # Memory Server cooldown.
        
        # Prevent concurrent startup (counter avoids concurrent start_session finally blocks overwriting each other).
        self._starting_session_count = 0
        self._starting_input_mode = None
        self._last_cooldown_turn_end_time = 0.0  # Cooldown-path turn_end deduplication timestamp.

        # TTS buffering prevents lost chunks.
        self.tts_ready = False  # Whether TTS is fully ready.
        self.tts_pending_chunks = []  # Pending TTS text chunks: [(speech_id, text), ...].
        # Streaming mirror speech (open_mirror_speech_stream) enqueue callbacks: {speech_id: on_enqueued(n)}.
        self._mirror_stream_callbacks: dict = {}
        # Latest opened stream's TTS-turn handover time; later streams wait in opening order before claiming.
        self._mirror_stream_tail = None
        # Speech id of last stream actually claiming a TTS turn; if intervening streams end unclaimed, next stream recognizes the same sequence.
        self._mirror_last_claimed_sid = None
        # Streaming mirror hands over TTS turn when its end marker really queues: {speech_id: callback}.
        self._mirror_stream_ends: dict = {}
        self.tts_cache_lock = asyncio.Lock()  # Lock protecting buffer.
        self._last_tts_respawn_time: float = 0.0  # Last respawn timestamp for 12-second cooldown.
        self._tts_respawn_task: Optional[asyncio.Task] = None  # Delayed retry task, cancelled by end_session.
        self._last_tts_error_code: str = ''  # Last TTS error code.
        self._tts_retry_notify_count: int = 0  # TTS retry notification count; first three do not notify frontend.
        self._tts_rate_limit_backoff_level: int = 0  # API_RATE_LIMIT scheduled-respawn doubling count, reset on readiness.
        self._tts_rate_limit_retry_at: float = 0.0  # Monotonic rate-limit backoff deadline shared by scheduled/implicit respawn.
        self._tts_quota_blocked: bool = False  # Quota exhaustion stopped scheduled retries; discard old-turn buffers on readiness recovery.
        self._tts_quota_from_server_close: bool = False  # Last quota error came from free-service close frame (only then stop scheduled retry).
        self._tts_quota_stale_speech_ids: Optional[frozenset] = None  # Old-turn speech_ids classified at first implicit retry after quota interception; None means not classified yet.
        # User-facing TTS notices must survive handler replacement during a
        # worker respawn. Entries are released by audio_done or session reset.
        self._tts_notified_error_keys: set[tuple[str, str]] = set()
        self._tts_done_queued_for_turn: bool = False  # Prevent duplicate TTS end signals within one turn.
        self._tts_done_pending_until_ready: bool = False  # If TTS not ready, enqueue end signal only after pending flush.
        # Text-idle soft flush: with realtime voice + custom TTS, transcription pauses ~1s while
        # provider response.done has not arrived; ask worker to synthesize buffered final phrase first.
        # Every queued text chunk resets timer; done enqueue / interruption / teardown cancels it.
        self._tts_soft_flush_task: Optional[asyncio.Task] = None
        self._tts_soft_flush_supported: bool = False  # Set by _start_tts_thread using provider capabilities.
        # Keep one utterance ledger so a replacement worker can replay consumed text.
        # Raw-text ledger already submitted to current worker. On configured-provider runtime failure,
        # replay this turn's text + done to replacement, avoiding a wholly silent response.
        self._tts_replay_speech_id: Optional[str] = None
        self._tts_replay_chunks: list[tuple[Optional[str], str]] = []
        # After HTTP sentence-worker completion/failure boundary reports, retain only unplayed normalized text.
        self._tts_replay_sent_chunks: list[tuple[Optional[str], str]] = []
        self._tts_replay_done: bool = False
        self._tts_replay_audio_emitted: bool = False
        self._tts_replay_sentence_audio_emitted: bool = False
        self._tts_replay_progress_supported: bool = False
        # A failed configured preset must not leak its identity into legacy clone routing.
        # After configured-preset failure, retain character settings this session but use replacement worker's default voice,
        # preventing Voice ID and custom credentials from reaching unrelated providers such as CosyVoice.
        self._tts_fallback_uses_default_voice: bool = False
        self._active_text_request_id: Optional[str] = None
        self._turn_wrap_up_owed = False
        # Typed inputs being handled (StreamingMixin._process_stream_input):
        # an owed wrap-up waits for their reply rather than running before it.
        self._reply_setup_depth = 0
        # The independent-ASR voice turn (its id) holding an owed wrap-up from
        # its speech onset until it ends (TurnMixin._voice_turn_holds_owed_wrap_up).
        self._voice_turn_wrap_up_hold: Optional[str] = None
        # Last response handed to Offline client (see _shared._ReplyTurn). Hot-swap
        # promotion uses it to carry in-flight responses across speech-id rotation.
        self._open_reply_turn: Optional[_ReplyTurn] = None
        self._magic_command_image_drop_request_ids: set[str] = set()
        self._magic_command_image_drop_request_order: deque[str] = deque()
        # (request_id, staged image) pairs for offline attachments still queued in
        # the session's _pending_images; pruned whenever a new image is recorded.
        self._request_staged_images: deque[tuple[str, object]] = deque()
        
        # Input buffering prevents loss during session initialization.
        self.session_ready = False  # Whether session is fully ready.
        self.pending_input_data = []  # Pending input data: [message_dict, ...].
        self.pending_context_appends: list[dict] = []
        self._context_append_sequence = 0
        self._context_append_request_ids: OrderedDict[tuple[Any, ...], float] = OrderedDict()
        self._context_append_inflight_results: dict[tuple[Any, ...], asyncio.Future[ContextAppendResult]] = {}
        self._require_context_append_current_delivery = False
        self.input_cache_lock = asyncio.Lock()  # Lock protecting input buffer.
        # Serialize pending-input replay with live input dispatch. The cache
        # lock cannot span awaits because attachment handoff reacquires it.
        self._pending_input_flush_active = False
        self._pending_input_flush_idle_event = None
        
        # User activity timestamp for detecting recent input during proactive checks.
        self.last_user_activity_time = None  # float timestamp or None

        # Real-user-message timestamp: refresh only for nonempty genuine user input, excluding AI echoes (actual voice
        # transcript / text), VAD noise, and microphone recordings of AI's own TTS.
        # Distinct from last_user_activity_time (unconditional top-level refresh including echoes/noise); using the latter
        # for mini-game invitation response checks would let spoken-invitation echoes
        # implicitly dismiss pending invitations and remove buttons before user interaction. Subsequent
        # 'do not want to play now' clicks expire, never starting real decline cooldown, so invitations recur.
        self.last_user_message_time = None  # float timestamp or None
        # Real-user-interaction timestamp covers actual text/voice and explicit UI responses without messages
        # (e.g. mini-game invitation buttons), used only for proactive sustained-no-response
        # windows; separate from last_user_message_time's invitation-state semantics.
        self.last_user_engagement_time = None  # float timestamp or None
        # Process-local observation start for long-window proactive feedback. Anti-repeat corpus persists across restarts,
        # but genuine-user-message timestamps do not; use manager creation time so prior-run proactive messages
        # potentially already answered are not miscounted as continuously ignored after restart.
        self.proactive_engagement_observation_started_at = time.time()

        # Background loop disconnects session when user silence ≥ IDLE_SESSION_RESET_THRESHOLD_SECONDS.
        # Start lazily at first start_session; live permanently as a per-manager
        # singleton, sleeping and polling without an active session.
        self._idle_session_reset_task: Optional[asyncio.Task] = None

        # User-activity tracker aggregates window/process/CPU/idle/voice/chat signals into structured
        # ActivitySnapshot for proactive_chat Phase 1/2's propensity-to-speak decisions.
        # See docs/design/user-activity-tracker.md.
        from main_logic.activity import FocusScorer, MasterEmotionTracker, UserActivityTracker
        from main_logic.conversation_turns import create_default_turn_dispatcher
        self._activity_tracker = UserActivityTracker(lanlan_name)
        self._turn_dispatcher = create_default_turn_dispatcher(
            lanlan_name,
            self._activity_tracker,
        )

        # Focus-mode scorer (docs/design/focus-truename-mode.md): score
        # ActivitySnapshot + user-message text in [0,1], feeding self.state's
        # hysteresis state machine to decide whether this turn enables thinking. Per-session instance retaining only
        # rolling cadence-baseline buffer. Both triggers (inline stream_text / idle
        # proactive) share one scorer for consistent behavior.
        self._focus_scorer = FocusScorer(lanlan_name)
        # Edge flag for purging historical thinking / completed tool-call residue on focus exit: arm on FOCUS
        # entry, disarm after exit cleanup (see _maybe_purge_focus_artifacts).
        self._focus_artifacts_pending = False
        # History length upon FOCUS entry; on exit clear only completed tool calls after this point, during episode.
        self._focus_artifacts_history_start: int | None = None

        # Master emotion profile (infrastructure; config MASTER_EMOTION_* docs): asynchronous valence-arousal analysis of
        # user utterances, sole authority. Focus is first consumer (_focus_scorer's
        # emotion signal reads latest result). Never reuse lanlan avatar outward-emotion pipeline.
        # Privacy-independent (input is conversation, not screen), unaffected by privacy gate.
        self._master_emotion = MasterEmotionTracker(lanlan_name)
        # Emotion snapshot for inline focus scoring: _note_user_turn refreshes before
        # starting this turn's analysis, guaranteeing a deterministic one-turn lag.
        self._focus_emotion_reading = None

        # On entering gaming/entertainment or focused work, send one-shot context signal to frontend; once per type per session,
        # frontend asks whether to enable/disable screen-sharing source for proactive chat. Backend detects entry
        # and pushes only; frontend deduplicates. Originally limited to A/B vision_chat_default_off group, now
        # merged into main for all users.
        # Screen-sharing source matters only with privacy off (vision on); privacy mode already stops tracker heartbeat
        # ticks (_activity_guess_loop's _privacy_mode_active early return), naturally preventing triggers.
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

        # Downstream-consumer gate for activity_guess narration, consumed only by proactive
        # Phase 2. Two cases have no reader, wasting an expensive emotion-tier call:
        #   ① goodbye_silent (idle catgirl silence): Phase 2 immediately exits;
        #   ② no online WebSocket (ordinary disconnection / End Session): proactive delivery impossible.
        # Tracker outlives sessions; End Session does not cancel heartbeat (break-reminder / contextual-popup
        # rules must continue), so skip LLM through this gate without consumers. Otherwise after page closure
        # idle time keeps spending (cap only backs off to ~900s, never stops). On reconnect / leaving silence,
        # per-signature cache remains; narration resumes after that signature's backoff interval
        # (immediately next tick if conv_seq changed or interval elapsed while suspended).
        self._activity_tracker.set_narration_suppressed_check(
            self._should_suppress_activity_narration
        )

        # Current AI-turn text buffer: accumulate send_lanlan_response chunks, then at turn end
        # send one conversation turn to dispatcher. Activity sink uses trailing
        # text to detect questions and trigger unfinished_thread (at most two follow-ups within 5 minutes);
        # topic sink independently consumes the same turn without activity-tracker coupling.
        self._current_ai_turn_text: str = ''
        # Current AI-turn identity, published with _current_ai_turn_text to plugin bus at turn end.
        self._current_ai_turn_id: str = ''
        # Arrival time of current AI turn's first chunk ('when she started speaking' on plugin bus).
        self._current_ai_turn_started_at: float = 0.0
        self._current_ai_turn_type: str | None = None
        self._current_ai_turn_client_owned: bool = False
        # A discard emptied that buffer after its text had already reached
        # cross_server, which stays in that assistant turn until a turn end.
        self._discarded_turn_open: bool = False
        self._recent_ai_voice_echo_text: str = ''
        self._recent_ai_voice_echo_at: float = 0.0
        self._pending_ai_voice_echo_text: str = ''
        self._pending_ai_voice_echo_chunks = deque()
        self._confirmed_ai_voice_echo_audio_speech_ids: set[str] = set()

        # Event-driven state machine centralizes turn-ownership signals, letting proactive pipeline
        # frequently check is_proactive_preempted with O(1) reads. Emission sites include
        # handle_new_message / stream_text entry / prepare_proactive_delivery /
        # finish_proactive_delivery / system_router.proactive_chat, etc.
        self.state = SessionStateMachine(lanlan_name=lanlan_name)
        # Focus mode: mirror enter/exit to the frontend as a subtle cognition
        # indicator (very faint glow badge; see react-neko-chat). Inert by default: the
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

        # User language, set by start_session or frontend set_user_language(), initially None.
        self.user_language = None
        self._user_language_explicit = False
        self._conversation_render_language = None
        self._conversation_turn_language = None
        # Translation service (lazy initialization).
        self._translation_service = None
        
        # Log-throttling mechanism.
        self.session_closed_by_server = False  # Server-closed session flag.
        self.last_audio_send_error_time = 0.0  # Last audio-send-error timestamp.
        self.audio_error_log_interval = 2.0  # Audio-error logging interval in seconds.

        self._recent_avatar_interaction_ids = deque(maxlen=AVATAR_INTERACTION_DEDUPE_MAX_ITEMS)
        self._recent_avatar_interaction_id_set = set()
        self._avatar_interaction_gate_lock = asyncio.Lock()
        self._last_avatar_interaction_at = 0
        self._last_avatar_interaction_speak_at = 0
        self.avatar_interaction_cooldown_ms = 600
        self.avatar_interaction_speak_cooldown_ms = 1500

        # ── Unified tool calling registry ─────────────────────────────
        # Exposed via public ``register_tool`` / ``unregister_tool`` methods.
        # Same-process callback / agent_bridge uses local handlers; cross-process
        # plugin / agent_server uses ``remote_dispatcher`` (main_routers/
        # tool_router.py binds HTTP forwarding at main_server startup).
        # One registry serves offline and realtime clients,
        # eliminating re-registration on session switching.
        self.tool_registry = ToolRegistry()
        # Serialization lock for synchronizing tools to active/pending sessions.
        # Prevent repeated register/unregister/clear session.update
        # from reaching wire out of order (OpenAI Realtime / GLM / Qwen / Step accept
        # streaming session.update replacement, so disorder can leave the last snapshot inconsistent
        # with final registry state).
        self._tool_sync_lock = asyncio.Lock()
        # Metadata for next turn-end event from handle_response_complete.
        # Set at special-turn entries such as handle_avatar_interaction,
        # read and cleared by handle_response_complete. More atomic than separate
        # sync_message_queue control messages: metadata shares turn-end event lifecycle,
        # preventing out-of-order messages from misclassifying avatar turns as proactive.
        self._pending_turn_meta: Optional[dict] = None

        # Built-in pseudo tools (currently recall_memory only). Register placeholder at __init__ end,
        # when user_language may be None, falling back to 'en';
        # refresh before actual session entry to align descriptions with
        # then-known user_language.
        self._register_builtin_tools()
