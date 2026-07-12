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

Package layout (split from the former single-file ``main_logic/core.py``; the
import path ``main_logic.core`` is unchanged):

- ``_shared``: module-level constants, the package logger, and small pure
  helpers shared across the package.
- ``callback_render``: pure rendering helpers for agent-task callbacks and
  voice-swap injection strings.
- ``notices``: the prominent-notice buffer pool (single owner of the queue
  state).
- ``manager``: ``LLMSessionManager`` -- ``__init__`` (the single home of
  every instance attribute) assembled from the domain mixin modules
  (``context_append``, ``focus``, ``tts_runtime``, ``turn``,
  ``tool_calling``, ``lifecycle``, ``proactive``, ``greeting``,
  ``streaming``, ``notify``), which hold methods only.
- ``__init__``: re-exports of every top-level name of the old module so
  existing imports and test monkeypatches (``main_logic.core.<attr>``) keep
  working unchanged -- mixin methods read the test-patchable symbols late
  through this module's namespace (see the re-export block below), which is
  exactly what tests patch.
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
    RECALL_MEMORY_TOOL_FOUND_HEADER,
)


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


# Re-exports from the package submodules. Everything the old single-file
# module defined at top level stays importable as ``main_logic.core.<name>``,
# so every import in this file is intentional even though the class body no
# longer lives here (ruff F401 is not enforced on this facade).
#
# Rebind/monkeypatch semantics: ``LLMSessionManager`` methods live in the
# mixin modules, whose functions resolve globals against their OWN module
# dict -- rebinding a facade attribute does not reach them by default. Every
# test-patched target on this facade (``monkeypatch.setattr(
# "main_logic.core.<attr>", ...)`` and the ``setattr(core_module, ...)``
# form): CROSS_MODE_RESTART_WAIT_SECONDS, HIDE_DIRTY_VOICE_TRANSCRIPTS,
# _CONTEXT_APPEND_DEFAULT_MAX_TOKENS, load/aload_global_conversation_settings,
# dispatch_text_user_message, is_livestream_active, process_screen_data,
# get_tts_worker, publish_analyze_request_reliably and
# publish_voice_transcript_observed_best_effort therefore keeps working
# through a different mechanism: the mixins do not from-import those symbols
# but read them late via ``_core_facade.<attr>`` at every call site. When
# adding a NEW patch target on this facade, route its mixin read points
# through ``_core_facade`` the same way -- a bare-name read in a mixin would
# snapshot the import and silently miss the patch. For symbols moved into
# other submodules (e.g. ``_loc`` inside ``callback_render``), patch that
# submodule directly -- same contract as ``main_routers/system_router``
# (#2148).
#
# State-carrying objects (the notice queue/lock, the
# ``_proactive_expected_sid`` ContextVar, ``_notified_legacy_voices``) are
# re-exported by reference; their single owner is the defining submodule.
# ``notices._prominent_notice_seq`` is intentionally NOT re-exported: the
# owner rebinds that int on every enqueue, so a facade snapshot would go
# stale immediately (no external reader exists).
from ._shared import (  # noqa: F401
    _REQUEST_ID_UNSET,
    _MAGIC_COMMAND_IMAGE_DROP_REQUEST_MAX,
    _VOICE_PROACTIVE_ACK_GRACE_S,
    _TEXT_SESSION_INPUT_TYPES,
    _IMAGE_INPUT_TYPES,
    _LIVE_VISION_STREAM_INPUT_TYPES,
    _CONTEXT_APPEND_DEDUP_TTL_SECONDS,
    _CONTEXT_APPEND_DEDUP_MAX_ENTRIES,
    _CONTEXT_APPEND_READY_FLUSH_MAX_PASSES,
    _CONTEXT_APPEND_DEFAULT_MAX_TOKENS,
    _CONTEXT_APPEND_SOURCE_MAX_TOKENS,
    _CONTEXT_APPEND_BARE_PRIME_SOURCES,
    _VOICE_ECHO_LOOKBACK_SECONDS,
    _VOICE_ECHO_LOOKBACK_CHARS,
    _VOICE_ECHO_MIN_NORMALIZED_CHARS,
    _VOICE_ECHO_MIN_WINDOW_CHARS,
    _VOICE_ECHO_SIMILARITY_THRESHOLD,
    _VOICE_ECHO_NORMALIZE_RE,
    _normalize_voice_echo_text,
    _looks_like_recent_ai_echo,
    logger,
    IDLE_SESSION_RESET_THRESHOLD_SECONDS,
    IDLE_SESSION_RESET_CHECK_INTERVAL_SECONDS,
    FRONTEND_START_SESSION_TIMEOUT_SECONDS,
    CROSS_MODE_RESTART_WAIT_SECONDS,
    _proactive_expected_sid,
    NO_RETRY_TTS_CODES,
    IMMEDIATE_REPORT_TTS_CODES,
    _STATIC_LOCALES_DIR,
    _load_locale_messages,
    _get_chat_locale_text,
    _START_LLM_CONCURRENT_ABORTED,
    ContextAppendResult,
    _purge_closed_tool_calls,
)
from .callback_render import (  # noqa: F401
    _STATUS_EMOJI,
    _format_callback_source,
    apply_role_placeholders,
    _render_callback_inner_item,
    _build_callback_instruction,
    _format_voice_swap_item,
    _render_pending_extra_replies_by_origin,
    _select_callbacks_within_token_budget,
)
from .notices import (  # noqa: F401
    _prominent_notice_queue,
    _prominent_notice_lock,
    enqueue_prominent_notice,
    peek_prominent_notices,
    drain_prominent_notices,
    _notified_legacy_voices,
    enqueue_voice_migration_notice,
)



# The class itself is assembled in ``manager`` from the domain mixin
# modules. Keep this import at the very bottom: it (indirectly) imports the
# mixin modules, which bind this partially-initialized facade module as
# ``_core_facade`` for late-binding reads of the test-patchable symbols
# re-exported above.
from .manager import LLMSessionManager  # noqa: F401
