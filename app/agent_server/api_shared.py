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
    Modules,
    PLUGIN_NAME_CACHE_TTL,
    _get_throttled_logger,
    _bump_state_revision,
    _set_capability,
    _track_background_task,
    _create_tracked_task,
    _now_iso,
)

from config import (  # noqa: F401
    USER_PLUGIN_SERVER_PORT,
    TOOL_SERVER_PORT,
)
from utils.config_manager import get_config_manager
from utils.host_origin_guard import HostOriginGuardMiddleware

from .plugin_host import (  # noqa: F401
    _plugin_name_cache_lock,
    _get_plugin_friendly_name,
    _get_plugin_display_id,
    _start_embedded_user_plugin_server,
    _stop_embedded_user_plugin_server,
    _ensure_plugin_lifecycle_started,
    _ensure_plugin_lifecycle_stopped,
    _fire_user_plugin_capability_check,
)
from .capabilities import (  # noqa: F401
    _browser_use_dependency_status,
    _close_browser_use_adapter,
    _ensure_browser_use_adapter,
    _try_refresh_computer_use_adapter,
    _llm_check_lock,
    _fire_agent_llm_connectivity_check,
    _agent_flags_snapshot,
    _collect_agent_status_snapshot,
    _emit_agent_status_update,
)
from .results import (  # noqa: F401
    _emit_main_event,
    _send_main_event,
)
from .nekoclaw_builtins import provider as builtin_provider  # noqa: F401
from .nekoclaw_mount import (  # noqa: F401
    HUD_ACTIVE,
    hud,
    hud_task,
    ledger_exists,
    mount_nekoclaw_host,
    nekoclaw_on,
    peek_host,
    ready_host,
    send_report,
    shutdown_nekoclaw_host,
    stop_everything,
)


app = FastAPI(title="N.E.K.O Tool Server")
from utils.instance_access import InstanceAccessMiddleware
app.add_middleware(InstanceAccessMiddleware)
app.add_middleware(HostOriginGuardMiddleware)
