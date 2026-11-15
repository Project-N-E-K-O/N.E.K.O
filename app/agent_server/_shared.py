# -*- coding: utf-8 -*-
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

"""Shared runtime state for the ``app.agent_server`` package.

Owns the service logger (``setup_logging`` runs here before any other
project import so import-time failures are persisted), the ``Modules``
singleton state bag, cross-module numeric constants, and the state primitives
(``_bump_state_revision`` / ``_set_capability`` / background-task bookkeeping).

Tasks are not kept here. Every task is a commission in the NekoClaw host's
ledger (``nekoclaw/host``); ``nekoclaw_mount`` connects this process to it.

Split out of the former monolithic ``app/agent_server.py``. ``Modules`` is
mutable shared state with this module as its single owner: sibling modules
must do ``from . import _shared`` and access attributes as
``_shared.Modules.<attr>`` — never snapshot mutable globals via from-import.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional, ClassVar

from fastapi import FastAPI

from utils.logger_config import setup_logging, ThrottledLogger

# Configure logging as early as possible so import-time failures are persisted.
logger, log_config = setup_logging(service_name="Agent", log_level=logging.INFO)

from main_logic.agent_event_bus import AgentServerEventBridge


class Modules:
    # ``model_probe.AgentModelProbe``: whether the agent model can be reached.
    # The keyboard-and-mouse and browser switches only stay on while it can.
    model_probe: Any = None
    user_plugin_app: FastAPI | None = None
    user_plugin_http_server: Any = None
    user_plugin_http_task: Any = None  # threading.Thread (imported after class def)
    _plugin_server_loop: Any = None
    plugin_lifecycle_started: bool = False
    _plugin_lifecycle_lock: Optional[asyncio.Lock] = None
    # Monotonic user-plugin intent generation.  Enable/disable work runs in
    # background tasks, so an older readiness check must not overwrite a newer
    # toggle (or restart the lifecycle after the master switch was turned off).
    user_plugin_lifecycle_seq: int = 0
    # The master switch. The name is the one the persisted intent file and the
    # status snapshot use; nothing is "analyzed" any more.
    analyzer_enabled: bool = False
    analyzer_profile: Dict[str, Any] = {}
    # Monotonic intent generation of the browser switch. A background close
    # may finish after a quick re-enable, but must not overwrite the newer
    # ready capability state.
    browser_use_lifecycle_seq: int = 0
    # Agent feature flags (controlled by UI)
    agent_flags: Dict[str, Any] = {
        "computer_use_enabled": False,
        "browser_use_enabled": False,
        "user_plugin_enabled": False,
    }
    # Notification queue for frontend (one-time messages)
    notification: Optional[str] = None
    # Use the shared rate-limited logger at the business-logic layer.
    throttled_logger: "ThrottledLogger" = None  # Lazy initialization.
    agent_bridge: AgentServerEventBridge | None = None
    state_revision: int = 0
    capability_cache: Dict[str, Dict[str, Any]] = {
        "computer_use": {"ready": False, "reason": "AGENT_PRECHECK_PENDING"},
        "browser_use": {"ready": False, "reason": "AGENT_PRECHECK_PENDING"},
        "user_plugin": {"ready": False, "reason": "AGENT_PRECHECK_PENDING"},
    }
    _background_tasks: ClassVar[set] = set()
    _persistent_tasks: ClassVar[set] = set()


PLUGIN_NAME_CACHE_TTL: float = 30.0  # Cache for 30 seconds.


def _now_iso() -> str:
    # datetime.utcnow() is deprecated since Python 3.12; the tz-aware form
    # yields the exact same string ("+00:00" swapped back to the legacy "Z").
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _get_throttled_logger() -> ThrottledLogger:
    throttled = Modules.throttled_logger
    if throttled is None:
        throttled = ThrottledLogger(logger, interval=30.0)
        Modules.throttled_logger = throttled
    return throttled


def _bump_state_revision() -> int:
    Modules.state_revision += 1
    return Modules.state_revision


def _set_capability(name: str, ready: bool, reason: str = "") -> None:
    def _normalize_precheck_reason(raw_reason: str) -> str:
        text = str(raw_reason or "").strip()
        if not text:
            return ""
        if text.startswith("AGENT_"):
            return text

        lower = text.lower()
        # Normalize legacy Chinese/English free-text reasons into stable i18n codes.
        if "未检查" in text or "not checked" in lower or "pending" in lower:
            return "AGENT_PRECHECK_PENDING"
        if "模型未配置" in text or "model not configured" in lower:
            return "AGENT_MODEL_NOT_CONFIGURED"
        if "api url 未配置" in lower or "url not configured" in lower:
            return "AGENT_URL_NOT_CONFIGURED"
        if "api key 未配置" in lower or "key not configured" in lower:
            return "AGENT_KEY_NOT_CONFIGURED"
        if "endpoint not configured" in lower or "api 未配置" in lower:
            return "AGENT_ENDPOINT_NOT_CONFIGURED"
        if "pyautogui" in lower and ("not installed" in lower or "未安装" in text):
            return "AGENT_PYAUTOGUI_NOT_INSTALLED"
        if "no" in lower and "browser" in lower and "found" in lower:
            return "AGENT_BROWSER_NOT_FOUND"
        if "not initialized" in lower or "初始化失败" in text:
            return "AGENT_NOT_INITIALIZED"
        if "未发现可用插件" in text or "no plugins" in lower:
            return "AGENT_NO_PLUGINS_FOUND"
        if "plugin server" in lower or "插件服务" in text or "user_plugin server responded" in lower:
            return "AGENT_PLUGIN_SERVER_ERROR"
        if "unreachable" in lower or "连接失败" in text or "connectivity" in lower:
            return "AGENT_LLM_UNREACHABLE"
        return "AGENT_LLM_UNREACHABLE"

    prev = Modules.capability_cache.get(name, {})
    normalized_reason = _normalize_precheck_reason(reason)
    Modules.capability_cache[name] = {"ready": bool(ready), "reason": normalized_reason}
    if prev.get("ready") != bool(ready) or prev.get("reason", "") != normalized_reason:
        _bump_state_revision()


def _track_background_task(task: asyncio.Task) -> asyncio.Task:
    Modules._background_tasks.add(task)
    task.add_done_callback(Modules._background_tasks.discard)
    return task


def _create_tracked_task(coro: Any) -> asyncio.Task:
    return _track_background_task(asyncio.create_task(coro))
