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

Tasks are not kept here. Every task is a commission in the NekoPower host's
ledger (``nekopower/host``); ``nekopower_mount`` connects this process to it.

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
    # Generation of the keyboard-and-mouse switch: bumped by every request
    # that sets it. A request that waited for the check of the keyboard and
    # mouse writes its answer only while it is still the latest one.
    computer_use_switch_seq: int = 0
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
    # "Not ready" with no reason until worked out: for the keyboard-and-mouse
    # and the browser tools when the master switch comes on
    # (``capabilities._refresh_tool_capabilities``), for the plugins at start.
    # Nothing here asks the agent model anything.
    capability_cache: Dict[str, Dict[str, Any]] = {
        "computer_use": {"ready": False, "reason": ""},
        "browser_use": {"ready": False, "reason": ""},
        "user_plugin": {"ready": False, "reason": ""},
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


def _put_switch_off(key: str) -> None:
    """Put a capability switch off, and drop what was kept for task agents if it was the last one on.

    In one go, with nothing awaited in between: NekoPower is off the
    moment its last capability switch is (``nekopower_on``), and
    what was kept of the main dialogue goes then. Not after
    whatever the caller waits for next (a browser that is being
    closed, a plugin host that is being stopped): the switch may
    be on again by then, or the caller cancelled. Every place
    that puts one of them off does it by this.
    """
    Modules.agent_flags[key] = False
    try:
        # Resolved when called: that module imports this one.
        from .api_runtime import forget_turns_if_nekopower_is_off

        forget_turns_if_nekopower_is_off()
    except Exception:
        # Seen: with this failed, what was said before the switch went
        # off is handed to a task agent once NekoPower is on again.
        # (Whether NekoPower is off now could not be looked at, that is:
        # the dropping itself says so by itself when it fails.)
        logger.warning(
            "[NekoPower] looking whether NekoPower is off now failed as a switch went off;"
            " the messages kept for task agents are not dropped",
            exc_info=True,
        )


def _capability_reason_code(raw_reason: str) -> str:
    """Return the stable i18n code (``agent.precheck.*``) for why a capability is not ready.

    Codes are passed through; the free-text reasons of the configuration
    check (``ConfigManager.is_agent_api_ready``) and of the plugin host are
    mapped onto codes.
    """
    text = str(raw_reason or "").strip()
    if not text:
        return ""
    if text.startswith("AGENT_"):
        return text

    lower = text.lower()
    if "模型未配置" in text or "model not configured" in lower:
        return "AGENT_MODEL_NOT_CONFIGURED"
    if "api url 未配置" in lower or "url not configured" in lower:
        return "AGENT_URL_NOT_CONFIGURED"
    if "api key 未配置" in lower or "key not configured" in lower:
        return "AGENT_KEY_NOT_CONFIGURED"
    if "pyautogui" in lower and ("not installed" in lower or "未安装" in text):
        return "AGENT_PYAUTOGUI_NOT_INSTALLED"
    if "no" in lower and "browser" in lower and "found" in lower:
        return "AGENT_BROWSER_NOT_FOUND"
    if "未发现可用插件" in text or "no plugins" in lower:
        return "AGENT_NO_PLUGINS_FOUND"
    if "plugin server" in lower or "插件服务" in text or "user_plugin server responded" in lower:
        return "AGENT_PLUGIN_SERVER_ERROR"
    if "agent api check failed" in lower:
        # ``_check_agent_api_gate`` could not read the configuration.
        return "AGENT_CONFIG_CHECK_FAILED"
    # Anything else came from the configuration check failing as such
    # (``_check_agent_api_gate``): the model counts as not set up.
    return "AGENT_ENDPOINT_NOT_CONFIGURED"


# Tickets: what is written of a capability after a check that waited (for
# the configuration, the desktop, the browser search) is as new as when that
# check started, not as when it ended. Each such check takes a ticket when it
# starts (``_capability_ticket_now``) and writes with it; an answer from a
# check that started before the one last written is dropped. Writes that do
# not wait take a ticket as they write.
_capability_ticket = 0
_capability_ticket_written: Dict[str, int] = {}


def _capability_ticket_now() -> int:
    """Return a ticket for a check of a capability that starts now."""
    global _capability_ticket
    _capability_ticket += 1
    return _capability_ticket


def _set_capability(name: str, ready: bool, reason: str = "", *, started: Optional[int] = None) -> bool:
    """Write whether a capability can be used, and why not; False when the answer is older than the one written.

    ``started``: the ticket of the check the answer comes from, taken when
    that check started. Without it the write counts as of now.
    """
    ticket = started if started is not None else _capability_ticket_now()
    if ticket < _capability_ticket_written.get(name, 0):
        return False
    _capability_ticket_written[name] = ticket
    prev = Modules.capability_cache.get(name, {})
    normalized_reason = _capability_reason_code(reason)
    Modules.capability_cache[name] = {"ready": bool(ready), "reason": normalized_reason}
    if prev.get("ready") != bool(ready) or prev.get("reason", "") != normalized_reason:
        _bump_state_revision()
    return True


def _track_background_task(task: asyncio.Task) -> asyncio.Task:
    Modules._background_tasks.add(task)
    task.add_done_callback(Modules._background_tasks.discard)
    return task


def _create_tracked_task(coro: Any) -> asyncio.Task:
    return _track_background_task(asyncio.create_task(coro))
