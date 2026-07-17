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
project import so import-time failures are persisted), the guarded brain
adapter imports, the ``Modules`` singleton state bag, cross-module numeric
constants, and the state primitives (``_bump_state_revision`` /
``_set_capability`` / background-task bookkeeping).

Split out of the former monolithic ``app/agent_server.py``. ``Modules`` is
mutable shared state with this module as its single owner: sibling modules
must do ``from . import _shared`` and access attributes as
``_shared.Modules.<attr>`` — never snapshot mutable globals via from-import.
"""

import asyncio
import logging
from typing import Dict, Any, Optional, ClassVar

from fastapi import FastAPI

from utils.logger_config import setup_logging, ThrottledLogger

# Configure logging as early as possible so import-time failures are persisted.
logger, log_config = setup_logging(service_name="Agent", log_level=logging.INFO)

from main_logic.agent_event_bus import AgentServerEventBridge
try:
    from brain.computer_use import ComputerUseAdapter
    from brain.browser_use_adapter import BrowserUseAdapter
    from brain.openclaw_adapter import OpenClawAdapter
    from brain.openfang_adapter import OpenFangAdapter
    from brain.deduper import TaskDeduper
    from brain.task_executor import DirectTaskExecutor
    from brain.agent_session import get_session_manager  # noqa: F401  (re-exported via the package facade)
    from utils.result_parser import (  # noqa: F401  (re-exported via the package facade)
        parse_computer_use_result,
        parse_browser_use_result,
        parse_plugin_result,
        _phrase as _rp_phrase,
        _get_lang as _rp_lang,
    )
except Exception as e:
    logger.exception(f"[Agent] Module import failed during startup: {e}")
    raise


class Modules:
    computer_use: ComputerUseAdapter | None = None
    browser_use: BrowserUseAdapter | None = None
    openclaw: OpenClawAdapter | None = None
    openfang: OpenFangAdapter | None = None
    deduper: TaskDeduper | None = None
    task_executor: DirectTaskExecutor | None = None
    user_plugin_app: FastAPI | None = None
    user_plugin_http_server: Any = None
    user_plugin_http_task: Any = None  # threading.Thread (imported after class def)
    _plugin_server_loop: Any = None
    plugin_lifecycle_started: bool = False
    _plugin_lifecycle_lock: Optional[asyncio.Lock] = None
    # Task tracking
    task_registry: Dict[str, Dict[str, Any]] = {}
    executor_reset_needed: bool = False
    analyzer_enabled: bool = False
    analyzer_profile: Dict[str, Any] = {}
    # Computer-use exclusivity and scheduling
    computer_use_queue: Optional[asyncio.Queue] = None
    computer_use_running: bool = False
    active_computer_use_task_id: Optional[str] = None
    active_computer_use_async_task: Optional[asyncio.Task] = None
    # Browser-use task tracking
    active_browser_use_task_id: Optional[str] = None
    active_browser_use_bg_task: Optional[asyncio.Task] = None
    # Browser-use exclusivity: the adapter is a singleton whose cancel flag
    # and browser session are shared state, so dispatches must not overlap
    # (mirrors computer-use exclusivity, implemented as a lock instead of a
    # scheduler loop). Lazily created on the running event loop.
    browser_use_dispatch_lock: Optional[asyncio.Lock] = None
    # BrowserUse imports a sizeable dependency graph and may own a Chromium
    # subprocess.  Keep construction/teardown serialized so on-demand callers
    # cannot create duplicate adapters or race a disable/shutdown close.
    browser_use_init_lock: Optional[asyncio.Lock] = None
    # Monotonic intent generation. A background close may finish after a quick
    # re-enable, but must not overwrite the newer ready capability state.
    browser_use_lifecycle_seq: int = 0
    # OpenClaw/QwenPaw is an external service. Enabling keeps the user's intent
    # while a bounded background probe waits for the external health endpoint.
    openclaw_enable_task: Optional[asyncio.Task] = None
    openclaw_enable_seq: int = 0
    # Agent feature flags (controlled by UI)
    agent_flags: Dict[str, Any] = {
        "computer_use_enabled": False,
        "browser_use_enabled": False,
        "user_plugin_enabled": False,
        "openclaw_enabled": False,
        "openfang_enabled": False,
    }
    # Notification queue for frontend (one-time messages)
    notification: Optional[str] = None
    # 使用统一的速率限制日志记录器（业务逻辑层面）
    throttled_logger: "ThrottledLogger" = None  # 延迟初始化
    agent_bridge: AgentServerEventBridge | None = None
    state_revision: int = 0
    # Serialize analysis+dispatch to prevent duplicate tasks from concurrent analyze_request events
    analyze_lock: Optional[asyncio.Lock] = None
    # Per-lanlan fingerprint of latest user-turn payload already consumed by analyzer
    last_user_turn_fingerprint: ClassVar[Dict[str, str]] = {}
    # Proactive-analyze throttle state (opt-in feature, see AGENT_PROACTIVE_ANALYZE_*).
    # Per-lanlan count of proactive analyses run this session (reset on greeting_check)
    # and the last proactive assistant-turn fingerprint already consumed (dedupe).
    proactive_analyze_count: ClassVar[Dict[str, int]] = {}
    last_proactive_assistant_fingerprint: ClassVar[Dict[str, str]] = {}
    capability_cache: Dict[str, Dict[str, Any]] = {
        "computer_use": {"ready": False, "reason": "AGENT_PRECHECK_PENDING"},
        "browser_use": {"ready": False, "reason": "AGENT_PRECHECK_PENDING"},
        "user_plugin": {"ready": False, "reason": "AGENT_PRECHECK_PENDING"},
        "openclaw": {"ready": False, "reason": "AGENT_PRECHECK_PENDING"},
        "openfang": {"ready": False, "reason": "AGENT_PRECHECK_PENDING"},
    }
    _background_tasks: ClassVar[set] = set()
    _persistent_tasks: ClassVar[set] = set()
    # Cancellable background task handles by logical task_id
    task_async_handles: ClassVar[Dict[str, asyncio.Task]] = {}


PLUGIN_NAME_CACHE_TTL: float = 30.0  # 缓存 30 秒
TASK_REGISTRY_CLEANUP_TTL: float = 300.0  # 已完成任务保留 5 分钟
DEFERRED_TASK_TIMEOUT: float = 3600.0  # deferred 任务超时 1 小时
OPENCLAW_ENABLE_CHECK_ATTEMPTS: int = 24
OPENCLAW_ENABLE_CHECK_INTERVAL: float = 1.0


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
        if name == "openclaw":
            # Late import: channels.openclaw imports _shared at module load,
            # while this branch only runs at call time — the function-local
            # import breaks the would-be cycle without changing behavior.
            from .channels.openclaw import _openclaw_reason_code
            return _openclaw_reason_code(text)

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
        if "browser-use" in lower and ("not installed" in lower or "未安装" in text):
            return "AGENT_BROWSER_USE_NOT_INSTALLED"
        if "not initialized" in lower or "初始化失败" in text:
            return "AGENT_NOT_INITIALIZED"
        if "未发现可用插件" in text or "no plugins" in lower:
            return "AGENT_NO_PLUGINS_FOUND"
        if "plugin server" in lower or "插件服务" in text or "user_plugin server responded" in lower:
            return "AGENT_PLUGIN_SERVER_ERROR"
        if "openfang" in lower or "daemon" in lower:
            return "AGENT_OPENFANG_DAEMON_UNREACHABLE"
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
