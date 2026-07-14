# -*- coding: utf-8 -*-
# ruff: noqa: F401 - this module intentionally owns the facade import surface
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

"""Shared import surface and FastAPI application for the agent server.

This module owns the single application object and the dependencies re-exported
by the package facade. Runtime and route modules import this surface explicitly.
"""

import sys
import os
# Three levels up from this file (app/agent_server/__init__.py); the former
# monolith computed two levels up from app/agent_server.py.
_repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Always insert at position 0 so project-root ``utils/`` (and ``config/``,
# etc.) are found *before* ``plugin/`` which may contain identically-named
# sub-packages.  The check ``not in`` is deliberately removed: ``_repo_root``
# may already exist later in sys.path (e.g. via .venv site-packages), but
# that position loses to ``plugin/`` which is inserted at index 1 by
# ``_start_embedded_user_plugin_server`` (plugin_host.py).
if sys.path[0:1] != [_repo_root]:
    sys.path.insert(0, _repo_root)

# Wire DI bindings explicitly — direct script invocation
# (``python -m app.agent_server``) doesn't run app/__init__.py.
# Idempotent under launcher's ``from app import agent_server`` path too.
from app.runtime_bindings import install_runtime_bindings as _install_runtime_bindings
_install_runtime_bindings()

import mimetypes
import json
mimetypes.add_type("application/javascript", ".js")
import asyncio
import uuid
import time
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone
import httpx

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

# ``_shared`` calls setup_logging() before any config/brain import so
# import-time failures are persisted — same order as the old monolith.
from ._shared import (  # noqa: F401
    logger,
    log_config,
    setup_logging,
    ThrottledLogger,
    AgentServerEventBridge,
    ComputerUseAdapter,
    BrowserUseAdapter,
    OpenClawAdapter,
    OpenFangAdapter,
    TaskDeduper,
    DirectTaskExecutor,
    get_session_manager,
    parse_computer_use_result,
    parse_browser_use_result,
    parse_plugin_result,
    _rp_phrase,
    _rp_lang,
    Modules,
    PLUGIN_NAME_CACHE_TTL,
    TASK_REGISTRY_CLEANUP_TTL,
    DEFERRED_TASK_TIMEOUT,
    OPENCLAW_ENABLE_CHECK_ATTEMPTS,
    OPENCLAW_ENABLE_CHECK_INTERVAL,
    _get_throttled_logger,
    _bump_state_revision,
    _set_capability,
    _track_background_task,
    _create_tracked_task,
)

from config import (  # noqa: F401  (tail entries keep facade parity with the old monolith namespace)
    USER_PLUGIN_SERVER_PORT,
    OPENFANG_BASE_URL,
    TASK_ERROR_MAX_TOKENS,
    EXCEPTION_TEXT_MAX_CHARS,
    USER_NOTIFICATION_REASON_MAX_CHARS,
    USER_NOTIFICATION_ERROR_MAX_CHARS,
    TOOL_SERVER_PORT,
    TASK_DETAIL_MAX_TOKENS,
    AGENT_HISTORY_TURNS,
    ERROR_MESSAGE_MAX_CHARS,
    TASK_TRACKER_DETAIL_MAX_CHARS,
    TASK_TRACKER_INJECT_DETAIL_MAX_CHARS,
    AGENT_PROACTIVE_ANALYZE_ENABLED,
    AGENT_PROACTIVE_ANALYZE_MAX_PER_SESSION,
)
from utils.config_manager import get_config_manager
from utils.tokenize import truncate_to_tokens as _tt

from .tracker import (  # noqa: F401
    TASK_TRACKER_MAX_RECORDS,
    TASK_TRACKER_TTL,
    AgentTaskTracker,
    _task_tracker,
    _normalize_lanlan_key,
    _user_message_sender_id,
    _user_message_payload_text,
    _build_user_turn_fingerprint,
    _build_assistant_turn_fingerprint,
    _build_analyze_event_fingerprint,
    _user_message_signature,
    _last_user_message_signature,
    REDACTED_USER_TURN_MARKER,
    _redact_cancelled_user_turns,
)
from .registry import (  # noqa: F401
    _LEGACY_CORRECTION_PUBLIC_KEYS,
    _now_iso,
    _cleanup_task_registry,
    _collect_existing_task_descriptions,
    _is_duplicate_task,
    _spawn_task,
    _set_internal_correction_context,
    _get_internal_correction_context,
    _tracker_desc_for_task_info,
    _public_task_info,
    _spawn_background_cancel,
)
from .plugin_host import (  # noqa: F401
    _plugin_name_cache_lock,
    _bind_deferred_task,
    _get_plugin_friendly_name,
    _get_plugin_display_id,
    _start_embedded_user_plugin_server,
    _stop_embedded_user_plugin_server,
    _ensure_plugin_lifecycle_started,
    _ensure_plugin_lifecycle_stopped,
    _fire_user_plugin_capability_check,
)
from .capabilities import (  # noqa: F401
    _rewire_computer_use_dependents,
    _try_refresh_computer_use_adapter,
    _llm_check_lock,
    _fire_agent_llm_connectivity_check,
    _agent_flags_snapshot,
    _collect_agent_status_snapshot,
    _emit_agent_status_update,
)
from .results import (  # noqa: F401
    _emit_task_result,
    _emit_main_event,
)
from . import channels
from .channels.computer_use import (  # noqa: F401
    _run_computer_use_task,
    _computer_use_scheduler_loop,
)
from .channels.openclaw import (  # noqa: F401
    _default_openclaw_task_description,
    _resolve_openclaw_sender_id,
    _collect_active_openclaw_task_ids,
    _cancel_openclaw_tasks_for_stop,
    _openclaw_pending,
    _cancel_openclaw_enable_probe,
    _openclaw_first_reason,
    _openclaw_reason_code,
    _openclaw_reason_text,
    _openclaw_notification,
    _start_openclaw_enable_probe,
    _run_openclaw_enable_probe,
)
from .channels.openfang import (  # noqa: F401
    _patch_openai_response,
    _patch_usage,
    _patch_malformed_tool_calls,
    _extract_tool_intent_as_text,
)
from .channels.user_plugin import (  # noqa: F401
    _plugin_terminal_status,
    _resolve_delivery_mode,
    _lookup_llm_result_fields,
    _is_reply_suppressed,
)


app = FastAPI(title="N.E.K.O Tool Server")
