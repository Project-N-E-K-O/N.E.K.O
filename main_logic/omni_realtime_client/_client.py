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
    ToolCall,
    ToolDefinition,
    TurnDetectionMode,
    VisualDeliveryMode,
    _IMAGE_ANALYSIS_PENDING_DESCRIPTION,
    asyncio,
    realtime_wire_trace_enabled,
    response_arbiter_fail_open_enabled,
    soxr,
)



from ._shared import canonical_realtime_dialect
from ._shared import logger as _transport_logger
from ._wire_trace import RealtimeWireTrace
from ._tools import _ToolingMixin
from ._audio import _AudioMixin
from ._transport import _TransportMixin
from ._responses import _ResponseMixin
from ._response_arbiter import RealtimeResponseArbiter
from ._protocol_capabilities import resolve_realtime_protocol_capabilities
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
        on_audio_done (Callable[[], Awaitable[None]]):
            Callback for the provider closing this response's audio stream.
            Awaited strictly after the last audio delta of that response has
            been awaited, so downstream consumers can treat it as the
            authoritative end of playback for the current speech id.
            Only wired on the OpenAI-schema websocket loop; see
            ``_gemini_support`` for why Gemini deliberately never fires it.
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
        get_host_turn_id (Callable[[], str | None]):
            Reads the host's own notion of "which turn is live" — its speech id.
            Read-only and synchronous: ownership of the turn stays with the
            host, this side only samples it at a turn start and compares at the
            end (see ``_notify_turn_finished``). Left unset, the end-of-turn
            hooks behave exactly as they did before it existed.
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
        on_audio_done: Optional[Callable[[], Awaitable[None]]] = None,
        on_new_message: Optional[Callable[[], Awaitable[None]]] = None,
        on_sid_rotate: Optional[Callable[[], Awaitable[None]]] = None,
        on_input_transcript: Optional[Callable[[str], Awaitable[None]]] = None,
        on_input_transcript_with_route: Optional[Callable[..., Awaitable[None]]] = None,
        get_input_route_identity: Optional[Callable[[], "tuple[str, str, str] | None"]] = None,
        on_output_transcript: Optional[Callable[[str, bool], Awaitable[None]]] = None,
        on_connection_error: Optional[Callable[[str], Awaitable[None]]] = None,
        on_response_done: Optional[Callable[[], Awaitable[None]]] = None,
        on_silence_timeout: Optional[Callable[[], Awaitable[None]]] = None,
        on_status_message: Optional[Callable[[str], Awaitable[None]]] = None,
        on_repetition_detected: Optional[Callable[[], Awaitable[None]]] = None,
        get_host_turn_id: Optional[Callable[[], "str | None"]] = None,
        # Which character this session belongs to. Optional because the field
        # is only used to attribute frame copies -- a caller that does not pass
        # it gets the previous behaviour (unattributed) rather than a crash.
        lanlan_name: Optional[str] = None,
        extra_event_handlers: Optional[Dict[str, Callable[[Dict[str, Any]], Awaitable[None]]]] = None,
        api_type: Optional[str] = None,
        on_tool_call: Optional[OnToolCallCallback] = None,
        tool_definitions: Optional[List[ToolDefinition]] = None,
        livestream_mode: bool = False,
        noise_reduction_enabled: bool = True,
        turn_admission_lock: Optional[asyncio.Lock] = None,
    ):
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self._model_lower = model.lower() if model else ''
        _base_url_lower = (base_url or '').lower()
        _known_lanlan_free_route = (
            'lanlan.tech' in _base_url_lower
            or 'lanlan.app' in _base_url_lower
            or bool(livestream_mode)
        )
        _effective_api_type = api_type or ""
        if (
            not _effective_api_type
            and 'free' in self._model_lower
            and _known_lanlan_free_route
        ):
            _effective_api_type = "free"
        self.voice = voice
        self.ws = None
        self.instructions = None
        self.on_text_delta = on_text_delta
        self.on_audio_delta = on_audio_delta
        self.on_audio_done = on_audio_done
        self.on_new_message = on_new_message
        self.on_sid_rotate = on_sid_rotate
        self.on_input_transcript = on_input_transcript
        self.on_input_transcript_with_route = on_input_transcript_with_route
        self.get_input_route_identity = get_input_route_identity
        # One client-side utterance slot plus at most eight server item ids.
        # Provider finals consume their entry; reconnect/close clears all.
        self._input_route_identity_captured = False
        self._input_route_identity = None
        self._input_route_identity_by_item: dict[str, tuple[str, str, str] | None] = {}
        # Route owning the most recent audio frame seen before an onset, used
        # when the local onset gate never armed a snapshot. Last-write-wins, so
        # it cannot carry a stale owner into a later utterance.
        self._input_route_identity_stream_armed = False
        self._input_route_identity_stream_owner = None
        self.on_output_transcript = on_output_transcript
        self.turn_detection_mode = turn_detection_mode
        self.on_connection_error = on_connection_error
        self.on_response_done = on_response_done
        self.on_silence_timeout = on_silence_timeout
        self.on_status_message = on_status_message
        self.on_repetition_detected = on_repetition_detected
        self.get_host_turn_id = get_host_turn_id
        self.lanlan_name = lanlan_name
        self.extra_event_handlers = extra_event_handlers or {}
        self._bg_tasks: set = set()  # Retain fire-and-forget tasks against garbage collection
        # Tool handlers have narrower ownership than generic background work:
        # their results belong to one connection and one user-turn scope.
        self._tool_scope_generation = 0
        # Some OpenAI-compatible proxies omit speech_started/stopped and only
        # report the completed input transcript. Track whether server VAD has
        # already advanced this input's tool scope so the transcript can fill
        # that gap without advancing a normal provider's turn twice.
        #
        # Two markers, because the providers this covers are inconsistent.
        # ``_raw_speech_started_scoped_item_ids`` is the real one: the
        # utterance ids a ``speech_started`` already scoped, so a transcript
        # is matched against ITS OWN utterance rather than against "some
        # utterance was scoped at some point". The bool is the fallback for a
        # proxy that sends neither event an ``item_id``; it is all the
        # pre-identity shape ever had, and on its own it survives an utterance
        # whose transcript never arrives -- the next turn's transcript then
        # reads as already scoped and a stale tool result can cross into it.
        self._raw_speech_started_scope_pending_transcript = False
        self._raw_speech_started_scoped_item_ids: List[str] = []
        self._tool_tasks: set[asyncio.Task] = set()
        # Tool tasks that can no longer produce a usable result, whatever
        # retired them. Recorded rather than re-derived: see
        # ``_retired_tool_tasks``.
        self._retired_tool_task_set: set[asyncio.Task] = set()
        self._tool_tasks_by_call_id: Dict[str, set[asyncio.Task]] = {}
        self._cancelled_tool_call_ids: set[tuple[int, int, str]] = set()
        # Teardown owns the socket it detached, so a cancelled caller cannot
        # strand it. Both close paths run as one task per connection and every
        # caller awaits it through a shield: cancelling the caller stops the
        # waiting, never the closing. An in-flight retry re-awaits the same
        # task; a completed failure keeps its detached transport for a fresh
        # close attempt instead of finding ``self.ws`` already None and
        # returning happy.
        # Reset by connect(), because the client object outlives a connection.
        self._close_task = None
        self._failed_transport_close_task = None
        self._gemini_close_task = None
        self._gemini_close_retry_contexts: dict[int, tuple[Any, Any]] = {}
        # A provider close can fail after the socket has been detached from
        # ``self.ws``. Keep that physical owner until a later close retry
        # confirms release; connect() must not discard this uncertainty.
        self._retired_websockets: list[Any] = []
        # A replacement can reset the connection-wide close latch while the
        # retired Gemini context is still exiting. Keep a separate latch per
        # context so every path joins the same one-shot ``__aexit__`` call.
        self._gemini_context_close_tasks: dict[int, tuple[Any, asyncio.Task]] = {}
        # Bumped when a replacement connection attaches. A teardown that
        # outlived its caller compares it after every await: the socket it
        # detached is still its own to close, but client-wide state (silence
        # scalars, the shared audio processor, the Gemini session) belongs to
        # whoever attached last.
        self._connection_generation = 0
        # ``(generation, reason)`` armed when a local component aborts the
        # attached transport out from under the receive loop, so the loop can
        # still request manager recovery for a socket it no longer owns.
        # Cleared by an ordinary close() (the manager already knows) and by a
        # replacement attach (a successor never inherits this).
        self._local_failure_recovery: tuple[int, str] | None = None

        # Track current response state
        self._current_response_id = None
        self._current_item_id = None
        self._is_responding = False
        # Advanced once per turn START, by every writer that begins one. A
        # fail-open release captures it and stops the moment it changes: an
        # abandoned turn's end-of-turn hooks must not land on whatever turn is
        # live by the time the host gets around to them. Nothing that ENDS a
        # turn touches it — "this turn ended" is not "a new turn started", and
        # an interrupted response's own terminal must still be able to finish
        # the turn it belongs to.
        self._turn_epoch = 0
        # The value ``_turn_epoch`` had when the response ``_current_response_id``
        # names began. A release must compare against THIS, not against the
        # epoch it happens to read on entry: a barge-in advances ``_turn_epoch``
        # at ``speech_stopped`` without clearing the tracked response id, so a
        # release starting after that barge-in would otherwise adopt the
        # successor's epoch as its own baseline and find itself trivially
        # current.
        self._current_turn_epoch = 0
        # The host's speech id as it was when this turn began, sampled at the
        # same points that record the epoch. The epoch counts turn starts this
        # transport OBSERVES; the host starts turns of its own that never reach
        # here (``handle_new_message`` off a text input or independent ASR), so
        # on those the epoch is unchanged and only the speech id moves. #2612.
        self._current_turn_host_id: str | None = None
        self._realtime_protocol_capabilities = (
            resolve_realtime_protocol_capabilities(
                _effective_api_type,
                base_url,
                livestream_mode=bool(livestream_mode),
            )
        )
        # Read once per construction, like the fail-open hatch: a change needs
        # a restart. Off leaves the transport and the arbiter untouched.
        self._wire_trace_enabled = realtime_wire_trace_enabled()
        self._wire_trace = (
            RealtimeWireTrace(_transport_logger) if self._wire_trace_enabled else None
        )
        self._response_arbiter = RealtimeResponseArbiter(
            self.send_event,
            abort_transport=self._abort_failed_transport,
            fail_open=response_arbiter_fail_open_enabled(),
            on_stuck_release=self._on_arbiter_stuck_release,
            protocol_capabilities=self._realtime_protocol_capabilities,
            trace=self._wire_trace_enabled,
            # Stamp arbiter records with the wire records' client tag and
            # live connection generation so an offline reader can key both
            # to the same connection.
            trace_tag=(
                self._wire_trace.client_tag if self._wire_trace is not None else None
            ),
            trace_generation=(
                (lambda: getattr(self, "_connection_generation", None))
                if self._wire_trace_enabled
                else None
            ),
        )
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
        # Raised by a fail-open release, lowered by the next response.created.
        # Inside that window an id-less event cannot be told apart from the
        # successor's, and a tool call is the one kind whose leak has side
        # effects rather than merely wrong words. Never set on the default
        # fail-closed path, which has no release.
        self._idless_quarantine = False
        # Latched the first time this connection sees a response.created.
        # Until then the stale-event filter has no identity to compare
        # against, and an id-bearing terminal cannot belong to anyone but
        # the turn in progress — the free Gemini-proxy route never
        # announces at all, and treating its terminal as stale skipped
        # every turn's finalization, including the speech-id rotation it
        # depends on. Reset per connect(): ids are connection-scoped.
        self._announces_responses = False
        self._last_response_created_time = 0.0
        self._response_done_total = 0  # diagnostic: response.done events observed
        # Response ids whose token usage has already been booked, so a
        # repeated response.done cannot count the same turn twice.
        self._usage_recorded_ids: list[str] = []
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
        self._latest_image_generation = 0  # Distinguishes identical consecutive frames
        self._latest_image_captured_at = 0.0
        self._latest_image_source = "unknown"
        self._latest_image_request_id = None
        self._proactive_image_consumed = True  # Whether the cached image has been used by a proactive nudge
        # Native is the backwards-compatible session default. Independent ASR
        # explicitly switches this to EXTERNAL_DESCRIPTION through the neutral
        # session API; provider capability remains a separate concern.
        self._visual_delivery_mode = VisualDeliveryMode.NATIVE
        self._raw_visual_delivery_blocked = False
        self._visual_delivery_epoch = 0
        # Callback-owned native media and user turns share this boundary. Core
        # passes its voice-proactive lock so the whole callback media+text
        # transaction is mutually exclusive with both server-VAD and external
        # ASR turn admission. Standalone clients keep an equivalent local lock.
        self._turn_admission_lock = turn_admission_lock or asyncio.Lock()

        # Silence detection for auto-closing inactive sessions
        # Enable the 90-second silence timeout only for GLM/free APIs; exempt Qwen and Step
        self._last_speech_time = None
        self._api_type = api_type or ""
        self._livestream_mode = bool(livestream_mode)
        # Use inferred api_type: an empty type inferred as free from a free-model name + lanlan routing
        # also counts as free, matching other free checks in this class.
        # Livestream mode with long streamer sessions bypasses this entire path.
        self._enable_silence_timeout = (
            _effective_api_type.lower() in ['glm', 'free']
            and not self._livestream_mode
        )
        self._silence_timeout_seconds = 90  # Automatically close after 90 seconds without voice input
        self._silence_check_task = None
        self._silence_timeout_triggered = False

        # Audio preprocessing with RNNoise for noise reduction
        # Auto-resets after 2 seconds of no speech to prevent state drift
        # Input: 48kHz from PC, 16kHz from mobile
        # Output: 16kHz for API
        self._noise_reduction_enabled = noise_reduction_enabled
        self._audio_processor = self._create_audio_processor()

        # ── Uplink (client→provider) sample rate ──────────────────────
        # Internal pipelines consistently use 16kHz: RNNoise downsamples to 16k, and mobile audio is natively 16k.
        # Most Realtime APIs (Gemini/Qwen/GLM/Step/
        # Grok/free) accept 16kHz PCM, but OpenAI Realtime PCM input
        # accepts *only* 24kHz: GA documentation fixes audio/pcm rate at 24000, disallowing
        # 16000. Otherwise the server decodes our 16k bytes as 24k, feeding the model audio sped/pitched up by 1.5×,
        # degrading ASR and server VAD.
        # Therefore upsample 16k→24k only for GPT at the final moment before sending; other providers retain
        # _uplink_sample_rate=16000 and _uplink_resampler=None, bypassing
        # resampling completely and preserving prior behavior.
        self._uplink_sample_rate = 24000 if 'gpt' in self._model_lower else 16000
        # Stateful streaming resampler: continuous microphone streams must retain FIR state, or every chunk
        # boundary introduces artifacts, just like AudioProcessor's downsample stream.
        self._uplink_resampler = (
            soxr.ResampleStream(16000, self._uplink_sample_rate, 1, dtype='float32', quality='HQ')
            if self._uplink_sample_rate != 16000
            else None
        )

        # Async silence-reset event queue, used by RNNoise's 4-second-silence callback
        self._silence_reset_pending = False
        # Clear buffers by last-speech time even without RNNoise, matching RESET_TIMEOUT
        self._silence_buffer_clear_seconds = 4.0
        self._last_silence_clear_speech_time = 0.0
        # Also require 2 continuous seconds of local silence before clear, preventing erroneous clearing from delayed VAD
        self._local_quiet_seconds = 2.0
        self._last_local_loud_time = 0.0

        # Repetition detection
        self._recent_responses = []  # Store the last 3 assistant replies
        self._repetition_threshold = 0.8  # Similarity threshold
        self._max_recent_responses = OMNI_RECENT_RESPONSES_MAX  # Maximum stored replies
        self._current_response_transcript = ""  # Current response transcript

        # Backpressure control prevents 503 overload errors
        self._send_semaphore = asyncio.Semaphore(25)  # At most 25 concurrent sends
        self._is_throttled = False  # Throttle state after detecting 503
        self._throttle_until = 0.0  # Throttle-end timestamp
        self._throttle_duration = 2.0  # Throttle duration in seconds
        self._server_busy_count: int = 0  # 503 overload count; notify frontend from the third occurrence onward

        # Fatal error detection immediately interrupts on fatal errors
        self._fatal_error_occurred = False  # Fatal-error flag

        # Interruption state - suppress output after user interruption until next response
        self._interrupted = False  # Interruption flag prevents duplicate message chunks
        self._suppressed_delta_logged_resp_id = None  # Rate limiting: log intercepted text.delta only once per response

        # Native image input rate limiting
        self._last_native_image_time = 0.0  # Timestamp of the last native image input

        # Unified VAD for image throttling (priority: server VAD > RNNoise > RMS)
        # All native-image paths use _client_vad_active to adjust send rate
        self._client_vad_active = False  # Voice activity detection, unified flag
        self._client_vad_last_speech_time = 0.0  # Last detected-speech timestamp
        # Increase grace from 2.0 to 6.0 to cover natural pauses for breathing/thinking,
        # preventing prompt_ephemeral from falsely triggering between the user's sentences.
        self._client_vad_grace_period = 6.0  # Post-speech activity grace period in seconds
        self._client_vad_threshold = 500  # RMS energy threshold in int16 range, used for fallback
        self._speech_detect_start = 0.0  # Start of sustained RNNoise speech detection
        self._speech_sustain_threshold = 0.5  # Require 500ms of speech to avoid noise-triggered false positives
        self._rnnoise_vad_active = False  # Whether RNNoise VAD is running (48kHz + working denoiser)
        # Proactive-chat protection separate from _client_vad_active: timestamp the most recent RNNoise frame
        # classed as speech (>0.4 without 500ms sustain) or server-VAD speech_started.
        # Covers two windows missed by _client_vad_active:
        #   1. The first 500ms before user speech reaches the sustain threshold
        #   2. The instant _client_vad_active becomes False after a between-sentence pause >grace_period
        # prompt_ephemeral abandons injection during this window.
        self._user_recent_activity_time = 0.0
        self._user_recent_activity_window = 8.0
        # AI-side counterpart to _user_recent_activity_time; timestamp every delivered AI-content frame.
        # Independent of _is_responding, which tracks response lifecycle from server
        # response.created/done or Gemini turn_complete; content streams
        # can be out of sync in these cases:
        #   1. Hundreds of milliseconds between OpenAI response.created and the first content chunk
        #   2. Gemini turn_complete preceding the final audio frames, producing late audio
        #   3. Long Gemini replies split into sub-turns, briefly False between sub-turns
        # prompt_ephemeral and Gemini turn allocation use this signal to guard proactive text interrupting the AI itself
        # and late audio mistaken for a new turn. Preserve _is_responding semantics because it still has
        # eight consumers (handle_interruption / QQ plugin / system_router 409, etc.); add only an independent signal.
        self._ai_recent_activity_time = 0.0
        self._ai_recent_activity_window = 3.0

        # Prevent log spam after WebSocket closure
        self._last_ws_none_warning_time = 0.0  # Timestamp of the last websocket-is-None warning
        self._ws_none_warning_interval = 5.0  # Minimum interval between websocket-is-None warnings, in seconds

        # Image processing lock
        self._image_lock = asyncio.Lock()

        # Audio processing lock to ensure sequential processing in thread pool
        self._audio_processing_lock = asyncio.Lock()

        # Gemini Live API specific attributes
        self._is_gemini = self._api_type.lower() == 'gemini'
        # ``api_type`` is the provider-ownership signal. The model name is
        # user-configurable, so an arbitrary local model such as
        # ``freeform-realtime`` must not inherit Lanlan's image wire protocol.
        # Keep a narrow compatibility fallback only for old callers that omit
        # api_type while targeting a known Lanlan/livestream route.
        self._is_free_provider = _effective_api_type.lower() == 'free'

        # Only the international/livestream free routes proxy Gemini. This flag
        # remains about VAD/lifecycle behaviour, not image capability: every
        # Lanlan free route now accepts native input_image_buffer events.
        self._is_free_proxy = self._is_free_provider and (
            'lanlan.app' in _base_url_lower
            or bool(livestream_mode)
        )

        # Whether this API returns server-side VAD events (speech_started/speech_stopped)
        # Gemini direct, lanlan.app+free Gemini proxy, and livestream mode
        # (streamers' self-hosted server_prefix upstreams also use Gemini and send no OpenAI VAD frames)
        # all count as lacking server VAD. Otherwise handle_messages never reaches speech_stopped's
        # on_new_message path, sid never rotates across turns, and TTS goes silent from turn 2.
        self._has_server_vad = (
            not self._is_gemini
            and not self._is_free_proxy
            and not bool(livestream_mode)
        )

        # Whether this client supports native image input
        # qwen/glm/gpt/gemini and every Lanlan free route have native vision.
        # Standard StepFun is the sole realtime provider that still needs the
        # external VISION_MODEL description path.
        self._supports_native_image = (
            any(m in self._model_lower for m in ['qwen', 'glm', 'gpt'])
            or self._is_gemini
            or self._is_free_provider
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
        # Internal, synchronous provenance observer. Acceptance is recorded
        # before execution is scheduled, even when the provider terminal wins
        # the event loop race against the actual tool task.
        self.on_tool_call_accepted: Optional[Callable[[ToolCall], None]] = None
        self._tool_definitions: List[ToolDefinition] = list(tool_definitions or [])
        # Provider behaviour matrix:
        #   gpt   → flat schema, response.done has output[].type=function_call
        #   glm   → flat schema, response.function_call_arguments.done event
        #           (no call_id — synthesize from response_id+output_index)
        #   step  → nested schema, response.function_call_arguments.done event
        #   free  (lanlan.tech proxies StepFun) → same as step. lanlan.app
        #          proxies Vertex Live; it forwards the session.update tools
        #          list and emits response.function_call_arguments.* events
        #          (observed 2026-09-07 minecraft_task, 2026-09-12 recall_memory).
        #   qwen  → no custom tool calling per Aliyun docs (only enable_search)
        #   gemini → genai SDK config.tools, response.tool_call.function_calls
        # The provider-side flags below let event handlers cheaply route.
        # Share apply_tools_to_session's dialect vocabulary: this previously compared api_type directly,
        # but 'gpt' was never valid (the real value is 'openai'), and 'qwen_intl'
        # never matched 'qwen'. The field has no readers currently, but retaining a wrong criterion invites future misuse.
        self._supports_tools_wire = canonical_realtime_dialect(self._api_type) in (
            'gpt', 'glm', 'qwen', 'step', 'free', 'gemini', 'grok'
        )
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
        # ``session.updated`` is the ordered Provider acknowledgement used by
        # hot-swap passive-media delivery barriers. Each waiter names the exact
        # instructions snapshot sent after its media writes, so an older
        # connection-setup acknowledgement cannot settle a later handoff.
        self._session_update_ack_waiters: list[tuple[str, asyncio.Future]] = []
        # One-shot gate for the no-event_id content fallback in
        # ``_route_inject_rejection``. True only between "a proactive inject
        # just sent its ``response.create``" and "that inject's outcome was
        # observed" on its exact arbiter ticket.
        # Without this, a no-id ``response_already_active`` from a DIFFERENT
        # ``response.create`` sender (create_response / tool-result /
        # signal_user_activity_end) could content-match a lingering — already
        # succeeded — proactive handler and wrongly re-enqueue its cb.
        self._proactive_inject_awaiting_outcome = False
        self._proactive_inject_outcome_token: Optional[str] = None
        self._gemini_proactive_outcome: Optional[tuple] = None
        # The task currently parked inside Gemini's proactive SDK send. A new
        # independent-ASR turn cancels and joins it before opening its own turn,
        # closing the post-activity-check race where the stale inject could land
        # while user speech was already being captured.
        self._gemini_proactive_submit_task: Optional[asyncio.Task] = None
        # Identify the SDK session owning the in-flight send and narrow guards accordingly, rather than asking only whether
        # any send exists: after replacement attach, old sends stuck on **retired** sessions
        # do not compete with injection on the new session's transport.
        self._gemini_proactive_submit_session: object | None = None
        self._gemini_proactive_quarantine_task: Optional[asyncio.Task] = None
        # External-ASR Gemini sends have the same accepted-before-cancellation
        # ambiguity as proactive sends. Keep the submit identity after its
        # visual record is abandoned so a successor turn can join quarantine
        # and retire the owning SDK session before reconnecting.
        self._gemini_external_submit_task: Optional[asyncio.Task] = None
        # ``send_client_content`` returning only proves that Gemini accepted the
        # turn; the response still owns this session until its terminal event.
        # Keep a distinct token because the submit coroutine itself may already
        # be finished when the next external-ASR utterance starts.
        self._gemini_external_outcome_token: Optional[object] = None
        self._gemini_cancelled_terminal_pending = False
        self._gemini_cancelled_terminal_deadline: Optional[float] = None
        self._gemini_cancelled_terminal_awaiting_delivery = False
        self._gemini_cancelled_terminal_id: Optional[object] = None
        self._gemini_external_quarantine_task: Optional[asyncio.Task] = None
        self._gemini_proactive_outcome_owner: Optional[tuple] = None

    def _create_audio_processor(self) -> AudioProcessor:
        """Create session-owned audio state, including native RNNoise state."""
        return AudioProcessor(
            input_sample_rate=48000,
            output_sample_rate=16000,
            noise_reduce_enabled=self._noise_reduction_enabled,
            on_silence_reset=self._on_silence_reset,
        )

    def _fire_task(self, coro):
        """Create a background task with GC protection."""
        task = asyncio.create_task(coro)
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)
        return task

    async def _cancel_frame_copies(self) -> None:
        """End every in-flight frame copy. The dual of the offline client's.

        ``_bg_tasks`` is never cancelled on close, so without this a copy
        parked in the cross-loop handoff outlives the session holding its
        base64, and publishes a frame from a retired session if the bridge
        later recovers. Cancel then collect -- a cancelled task has not
        stopped until it has been awaited.
        """
        # Latch before draining, the same way the offline client does. Draining
        # alone races: an image send already awaiting the provider (Gemini's
        # send_realtime_input, a queued WebSocket event) resolves after the
        # drain, fires a fresh copy, and that copy outlives the closed session
        # with nothing left to collect it.
        self._frame_copies_closed = True
        tasks = getattr(self, "_frame_copy_tasks", None)
        if not tasks:
            return
        # Snapshot: the done-callbacks discard from the live sets as they end.
        draining = list(tasks)
        for task in draining:
            task.cancel()
        await asyncio.gather(*draining, return_exceptions=True)
        tasks.clear()

    def _fire_frame_copy(self, coro):
        """``_fire_task`` for frame copies only, and bounded.

        The ceiling is counted in its OWN set, not in ``_bg_tasks``: that set
        holds quarantine and lifecycle work whose whole job is to finish, and
        capping it would drop exactly the tasks that must not be dropped. A
        frame copy is the only thing here that is both optional and
        multi-megabyte, so it is the only thing that gets one.

        The task itself is still created through ``_fire_task``, so it stays
        registered for teardown like every other background task, and the
        single seam tests use to observe or refuse one keeps working.
        """
        from main_logic.agent_event_bus import spawn_bounded_frame_copy

        if getattr(self, "_frame_copies_closed", False):
            # close() has begun. A copy started now describes a session that is
            # gone, and the drain has already run -- nothing would collect it.
            coro.close()
            return None
        tasks = getattr(self, "_frame_copy_tasks", None)
        if tasks is None:
            tasks = set()
            self._frame_copy_tasks = tasks
        return spawn_bounded_frame_copy(
            coro, tasks, label="realtime bus copy", spawn=self._fire_task,
        )

    def _ensure_turn_admission_lock(self) -> asyncio.Lock:
        """Return the shared callback/user-turn boundary, lazily for doubles."""

        lock = getattr(self, "_turn_admission_lock", None)
        if lock is None:
            lock = asyncio.Lock()
            self._turn_admission_lock = lock
        return lock
