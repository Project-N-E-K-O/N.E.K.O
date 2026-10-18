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
    Awaitable,
    Callable,
    DIALOG_LLM_STREAM_TIMEOUT_SECONDS,
    Dict,
    List,
    OMNI_RECENT_RESPONSES_MAX,
    OnToolCallCallback,
    Optional,
    ToolDefinition,
    _UNLIMITED_BUDGET,
    _budget_to_max_tokens,
    asyncio,
    create_chat_llm,
)

from ._genai_support import (
    _should_use_genai_sdk,
)

from ._tools import _ToolingMixin
from ._genai_support import _GenaiMixin
from ._streaming import _StreamingMixin
from ._media import _MediaMixin
from ._lifecycle import _LifecycleMixin

from utils.http.url import same_endpoint


# Use utils.http.url for the shared-endpoint test. Visual-slot credential inheritance and the config-layer rule "a managed Key may only
# configure its provider's own endpoint" ask the same question. This file used to hand-roll its own version; reviewers found six edge cases
# (trailing slash / path case / userinfo / malformed port / default port / malformed IPv6).
# The config layer later independently reimplemented it and repeated the same mistakes. Sharing one implementation is the fix.
_same_endpoint = same_endpoint


class OmniOfflineClient(_ToolingMixin, _GenaiMixin, _StreamingMixin, _MediaMixin, _LifecycleMixin):
    """
    A client for text-based chat that mimics the interface of OmniRealtimeClient.

    This class provides a compatible interface with OmniRealtimeClient but uses
    ChatOpenAI with OpenAI-compatible API instead of realtime WebSocket,
    suitable for text-only conversations.

    Attributes:
        base_url (str):
            The base URL for the OpenAI-compatible API (e.g., OPENROUTER_URL).
        api_key (str):
            The API key for authentication.
        model (str):
            Model to use for chat.
        vision_model (str):
            Model to use for vision tasks.
        vision_base_url (str):
            Optional separate base URL for vision model API.
        vision_api_key (str):
            Optional separate API key for vision model.
        llm (ChatOpenAI):
            ChatOpenAI client for streaming text generation.
        on_text_delta (Callable[[str, bool], Awaitable[None]]):
            Callback for text delta events.
        on_input_transcript (Callable[[str], Awaitable[None]]):
            Callback for input transcript events (user messages).
        on_output_transcript (Callable[[str, bool], Awaitable[None]]):
            Callback for output transcript events (assistant messages).
        on_connection_error (Callable[[str], Awaitable[None]]):
            Callback for connection errors.
        on_response_done (Callable[[], Awaitable[None]]):
            Callback when a response is complete.
    """

    # Clear conversation history during sustained high repetition (keep only system instructions). Disable it for sessions whose history is maintained by the caller:
    # clearing it would erase every utterance and also lose per-entry markers the caller attached.
    repetition_reset_enabled: bool = True

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str = "",
        vision_model: str = "",
        vision_base_url: str = "",  # Independent vision-model API URL
        vision_api_key: str = "",   # Independent vision-model API key
        provider_type: str | None = None,
        vision_provider_type: str | None = None,
        voice: str = "",  # Unused for text mode but kept for compatibility
        turn_detection_mode = None,  # Unused for text mode
        on_text_delta: Optional[Callable[[str, bool], Awaitable[None]]] = None,
        on_thinking_active: Optional[Callable[[bool], Awaitable[None]]] = None,
        on_audio_delta: Optional[Callable[[bytes], Awaitable[None]]] = None,  # Unused
        on_interrupt: Optional[Callable[[], Awaitable[None]]] = None,  # Unused
        on_input_transcript: Optional[Callable[[str], Awaitable[None]]] = None,
        on_output_transcript: Optional[Callable[[str, bool], Awaitable[None]]] = None,
        on_connection_error: Optional[Callable[[str], Awaitable[None]]] = None,
        on_response_done: Optional[Callable[[], Awaitable[None]]] = None,
        on_repetition_detected: Optional[Callable[[], Awaitable[None]]] = None,
        on_response_discarded: Optional[Callable[[str, int, int, bool, Optional[str]], Awaitable[None]]] = None,
        on_status_message: Optional[Callable[[str], Awaitable[None]]] = None,
        extra_event_handlers: Optional[Dict[str, Callable[[Dict[str, Any]], Awaitable[None]]]] = None,
        max_response_length: Optional[int] = None,
        lanlan_name: str = "",
        master_name: str = "",
        on_tool_call: Optional[OnToolCallCallback] = None,
        tool_definitions: Optional[List[ToolDefinition]] = None,
        max_tool_iterations: int = 3,
        enable_long_response_summary: bool = False,
        user_language_provider: Optional[Callable[[], Optional[str]]] = None,
    ):
        # Use base_url directly without conversion
        self.base_url = base_url
        self._user_language_provider = user_language_provider
        self.api_key = api_key if api_key and api_key != '' else None
        self.model = model
        self.vision_model = vision_model  # Store vision model for temporary switching
        # Independent vision-model configuration: URL and Key must fall back **as a pair**.
        #
        # These two lines originally checked for emptiness independently, so when "the visual slot had its own URL but no Key,"
        # the URL stayed on the visual provider's domain while the Key inherited the conversation provider's credential — sending the user's
        # paid Key to another vendor, both a routing error and credential leakage. Leaving Key empty makes the request
        # omit Authorization: correct for a local unauthenticated endpoint; a paid endpoint returns 401
        # clearly, which is more diagnosable than silently sending credentials elsewhere.
        # Compare normalized URLs for same-origin checks: when both slots point to the same provider, common trailing-slash /
        # case differences should not count as "switching providers" and disable inheritance (which would make a working config return 401).
        _vision_url = vision_base_url or base_url
        self.vision_base_url = _vision_url
        # Normalize "no credential" to None, matching normalization of self.api_key above —
        # otherwise the visual side would use '' while the main side uses None for the same state.
        if _same_endpoint(_vision_url, base_url):
            self.vision_api_key = (vision_api_key or None) or self.api_key
        else:
            self.vision_api_key = vision_api_key or None
        self.provider_type = provider_type
        self.vision_provider_type = vision_provider_type or provider_type
        self._model_switch_lock = asyncio.Lock()
        self._multimodal_submit_lock = asyncio.Lock()
        self.on_text_delta = on_text_delta
        # Called with True the first time a stream emits a reasoning / thinking
        # chunk (the text itself is filtered out before it reaches text/TTS —
        # only the "is thinking" boolean is surfaced), and with False to clear
        # when that stream ends without an external unconditional clear (see
        # _notify_reasoning_done). Drives the chat thinking-dots bubble for ANY
        # turn that actually reasons, decoupled from Focus mode.
        self.on_thinking_active = on_thinking_active
        # Reasoning-pulse ownership. A proactive prompt_ephemeral turn can
        # interleave with a user stream_text on this SAME client (core drops stale
        # proactive chunks via the expected-sid guard rather than awaiting the
        # prompt). Ownership is tracked by a single source of truth — NOT a shared
        # boolean, which _begin_reasoning_stream would reset out from under an
        # older still-running stream (Codex P2):
        #   _reasoning_stream_seq      — monotonic counter, bumped per stream entry
        #                                (stream_text / prompt_ephemeral) so each
        #                                stream has a distinct token.
        #   _reasoning_active_pulse_seq — the seq that currently owns an un-cleared
        #                                True pulse (None = bubble not lit by us).
        #                                A pulse stamps it; a clear only fires for
        #                                the owning seq. Because it is NOT reset on
        #                                stream entry, a preempted proactive turn
        #                                still clears its OWN pulse, yet cannot
        #                                clear a newer stream that already re-pulsed.
        self._reasoning_stream_seq = 0
        self._reasoning_active_pulse_seq: Optional[int] = None
        self.on_input_transcript = on_input_transcript
        self.on_output_transcript = on_output_transcript
        self.handle_connection_error = on_connection_error
        self.on_status_message = on_status_message
        self.on_response_done = on_response_done
        self.on_proactive_done: Optional[Callable[[bool], Awaitable[None]]] = None
        self.on_repetition_detected = on_repetition_detected
        self.on_response_discarded = on_response_discarded

        # Ordinary chat guard configuration (determine max_response_length first; create_chat_llm
        # uses _budget_to_max_tokens(self.max_response_length)).
        # 0 / negative values mean "unlimited" in the update_max_response_length path
        # (= _UNLIMITED_BUDGET); __init__ must use the same semantics, or if persisted config reads 0 on the first turn,
        # it would create the LLM with a 300+20 cap until the user moves the slider again.
        # Maximum automatic rerolls: 1 reroll → 2 attempts total.
        self.enable_response_guard = True
        if not isinstance(max_response_length, int):
            self.max_response_length = 300
        elif max_response_length > 0:
            self.max_response_length = max_response_length
        else:
            self.max_response_length = _UNLIMITED_BUDGET
        # If the second attempt is still too long, do not discard the whole response; fall back to truncating at the last sentence-ending punctuation.
        # Long-reply summary path switch: when enabled, do not inline-abort and truncate long but readable replies;
        self.max_response_rerolls = 1

        # let the model continue to budget+slack (at least _SUMMARY_API_BUDGET_FLOOR); TTS
        # stops at the next terminator after budget, while the small emotion-tier model uses the character's voice to
        # write the continuation summary. Nothing changes when disabled.
        # Summarize the tail into one or two sentences for TTS. The live frontend shows full text; history stores prefix+summary,
        # an intentional semantic difference. Default False preserves existing abort behavior for short game lines with max=100;
        # core explicitly enables this when creating chat clients.
        self.enable_long_response_summary = bool(enable_long_response_summary)

        # Initialize ChatOpenAI client. Set max_completion_tokens to
        # max_response_length + 20 so the API naturally stops at budget+20 tokens,
        # avoiding useless generation while leaving the fence 20 tokens of overshoot slack
        # to distinguish truncation from gibberish filtering.
        # Always use the ordinary budget here, without baking in the summary's 3000-token floor:
        # the same self.llm serves stream_text and prompt_ephemeral (proactive),
        # so a 3000-token client limit would also let unguarded proactive turns emit 3000
        # tokens. Instead temporarily raise the budget inside stream_text and restore it in finally
        # (see stream_text's start), restricting 3000 strictly to long-response streaming.
        self.llm = create_chat_llm(
            self.model, self.base_url, self.api_key,
            streaming=True, max_retries=0,
            max_completion_tokens=_budget_to_max_tokens(self.max_response_length),
            timeout=DIALOG_LLM_STREAM_TIMEOUT_SECONDS,  # hang-guard; generous so normal/long replies aren't truncated
            provider_type=self.provider_type,
        )

        # ── Tool calling state ────────────────────────────────────────
        # ``tool_definitions`` is the canonical list (ToolDefinition objects);
        # the wire-format snapshots are rebuilt from it on each request so
        # callers can mutate the list (register/unregister) between turns.
        self.on_tool_call: Optional[OnToolCallCallback] = on_tool_call
        # Fires once per LLM iteration that turns out to be a tool round —
        # BEFORE any handler runs, and also on rounds where every collected
        # call gets dropped (nameless streaming fragments) so zero handler
        # invocations happen. Buffering callers (the QQ plugin accumulates
        # deltas and sends one message at the end) hook this to discard
        # pre-tool text ("let me check…"): anchoring that hygiene on the
        # handler alone misses the zero-call rounds. Streaming callers
        # (main program) don't need it — their text is already on screen.
        self.on_tool_round_start: Optional[Callable[[], Awaitable[None]]] = None
        self._tool_definitions: List[ToolDefinition] = list(tool_definitions or [])
        self.max_tool_iterations = max(1, int(max_tool_iterations))
        self._use_genai_sdk = _should_use_genai_sdk(self.model, self.base_url)
        self._genai_client = None  # initialized lazily inside _stream_text_genai
        self._genai_tools_unsupported = False  # set True if genai path falls back at runtime
        # Set True after an OpenAI-compatible endpoint explicitly rejects tools (e.g. Ollama llava returns
        # 400 "does not support tools"). Omit tools on later session turns to avoid a guaranteed
        # failed request each turn. This is model capability state; clear only on model change / close.
        self._openai_tools_unsupported = False
        # The endpoint rejects tools only when images accompany the request ("tool use is not supported with images").
        self._openai_tools_unsupported_with_images = False

        # State management
        self._is_responding = False
        self._response_generation = 0
        self._interrupter_owned_generations: set[int] = set()
        self._active_response_generation: int | None = None
        self._completion_pending_generation: int | None = None
        # stream_text / prompt_ephemeral calls still running (see is_idle).
        self._reply_calls_in_flight = 0
        # Closes of clients switch_model replaced while a reply call was in
        # flight; the last call to return runs them (_retire_replaced_clients).
        self._retired_client_closers: list = []
        # Both sync and optional. on_response_displaced(kind): a user reply
        # began over this one without an interruption and took its close over
        # (the owner closes it). on_idle(): the last reply call returned and
        # nothing is left in progress.
        self.on_response_displaced: Optional[Callable[[str], Any]] = None
        # What on_response_displaced handed back to send before this reply's
        # first output (see _run_displaced_followup).
        self._displaced_followup = None
        self.on_idle: Optional[Callable[[], None]] = None
        self._conversation_history = []
        self._instructions = ""
        # Knowledge blocks the session's requests carry beside the history
        # (``_turn_context``); never saved in it.
        self._resident_turn_context = []
        self._stream_task = None
        self._pending_images = []  # Store pending images to send with next text
        # Separate staging for plugin-read images. Sharing _pending_images would make any eviction policy hurt users:
        # dropping oldest loses their newly staged frame, while rejecting new images lets plugins occupy all slots
        # and block user images (both were tried and rightly rejected in review). Separate quotas prevent either
        # side spending the other's budget; overflow always removes that side's own oldest image.
        # Same independently owned visual-staging pattern as _proactive_image_to_inject.
        self._pending_plugin_images = []
        # Screenshot retained after screen-based proactive delivery, to inject before the next user text reply
        # as visual context so the conversation model can see the screen just discussed. Keep separate
        # from _pending_images, the user's own next frame: sharing would steal the user's pending image;
        # see core.py proactive-media comments (Codex P2). Single image, consumed once, with TTL
        # (_proactive_image_staged_at is monotonic staging time in seconds; 0.0 means none).
        # _proactive_image_history_len records history length at staging. The screenshot is valid only for the
        # immediately following user reply. If another AI turn intervenes (greeting / agent callbacks through
        # prompt_ephemeral, bypassing finish_proactive_delivery) and grows history, it is stale
        # and discarded on injection, binding it to the latest AI turn (Codex P2).
        self._proactive_image_to_inject = None
        self._proactive_image_staged_at = 0.0
        self._proactive_image_history_len = 0

        # -- Empty-completion diagnostics (finish_reason / prompt_tokens / block_reason). --
        # Gemini may return non-stop finish_reason with empty content for SAFETY / RECITATION / MAX_TOKENS,
        # while OpenAI-compatible proxies still return HTTP 200 without exceptions,
        # leaving callers only a completed stream with zero text tokens. Record the last attempt's LLM-layer
        # finish_reason / block_reason / prompt_tokens so stream_text's all-retries-empty warning
        # and prompt_ephemeral's delivered=False fallback can report the actual
        # reason for empty output.
        self._last_finish_reason: Optional[str] = None
        self._last_block_reason: Optional[str] = None
        self._last_prompt_tokens: Optional[int] = None

        # Repetition detection.
        self._recent_responses = []  # Store the latest three assistant replies.
        self._repetition_threshold = 0.8  # Similarity threshold.
        self._max_recent_responses = OMNI_RECENT_RESPONSES_MAX  # Maximum stored replies.

        # ========== Output-prefix detection ==========
        self.lanlan_name = lanlan_name
        self.master_name = master_name
        self._prefix_buffer_size = max(len(lanlan_name), len(master_name)) + 3 if (lanlan_name or master_name) else 0
