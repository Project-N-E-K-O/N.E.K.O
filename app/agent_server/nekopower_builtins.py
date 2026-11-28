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

"""The keyboard-and-mouse and browser tools of the NekoPower gateway, as this process offers them.

The tools themselves are in ``nekopower/builtin``: two groups of small tools a
task agent calls one step at a time from its own loop. This module tells
them what only this process knows, and tells the rest of the application
what they are doing:

* whether a group is available: the master switch and the group's own switch
  are on and its last readiness check passed (``_state``);
* which commission is using a group right now (``occupant``), for the
  banner and for stopping it;
* when a commission begins and stops using a group (``on_builtin_activity``):
  the main server is sent a ``builtin_activity`` event, which is what the
  banner over the desktop or the browser window is driven by (design
  section 11.8), and the use is counted once.

Nothing else calls the tools: they are not channels the task brain routes to.
"""

from typing import Any, Dict, Optional

from nekopower.builtin.provider import BuiltinToolProvider
from nekopower.host.capabilities import BUILTIN_GROUP_BROWSER, BUILTIN_GROUP_KEYBOARD_MOUSE

from . import _shared
from ._shared import logger

# group -> (the switch that turns it on, the name its readiness is cached under)
_FLAG_OF = {
    BUILTIN_GROUP_KEYBOARD_MOUSE: ("computer_use_enabled", "computer_use"),
    BUILTIN_GROUP_BROWSER: ("browser_use_enabled", "browser_use"),
}
_COUNTED_AS = {BUILTIN_GROUP_KEYBOARD_MOUSE: "cua", BUILTIN_GROUP_BROWSER: "browser"}

ACTIVITY_EVENT = "builtin_activity"


def _state(group: str) -> tuple[bool, str]:
    """Return whether a group is switched on and ready right now, and if not, why. Does not block."""
    names = _FLAG_OF.get(group)
    if names is None:
        return False, "unknown"
    flag, capability = names
    modules = _shared.Modules
    if not modules.analyzer_enabled or not (modules.agent_flags or {}).get(flag, False):
        return False, "switched off"
    cached = (modules.capability_cache or {}).get(capability) or {}
    if not cached.get("ready"):
        return False, f"not ready ({cached.get('reason') or 'unknown'})"
    return True, ""


# group -> active team commission id(s) occupying it right now.
# Exclusive groups hold one id; shared groups (browser) may hold several concurrently.
_in_use: Dict[str, Any] = {}


def _as_list(raw: Any) -> list[str]:
    if isinstance(raw, str):
        return [raw] if raw else []
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, str) and item]
    return []


def _on_browser_window(window: Optional[Dict[str, Any]]) -> None:
    """The window of the browser being driven is known now, or has moved: the banner is told again.

    The browser is only started by a commission's first call, after the
    ``started`` event went out, so that event cannot say where the window is.
    """
    del window
    for commission_id in occupants(BUILTIN_GROUP_BROWSER):
        _send_activity(BUILTIN_GROUP_BROWSER, "started", commission_id)


def _own_ports() -> set[int]:
    """Return the ports this application's own services listen on, on this computer.

    The browser the browser tools drive is kept away from them: none is of
    any use to a task agent, and some answer a request from this computer
    without asking who sent it.
    """
    from config import (
        COMMENTER_SERVER_PORT,
        MAIN_SERVER_PORT,
        MEMORY_SERVER_PORT,
        MONITOR_SERVER_PORT,
        TOOL_SERVER_PORT,
        USER_PLUGIN_SERVER_PORT,
    )

    ports = {
        MAIN_SERVER_PORT,
        MEMORY_SERVER_PORT,
        MONITOR_SERVER_PORT,
        COMMENTER_SERVER_PORT,
        TOOL_SERVER_PORT,
        USER_PLUGIN_SERVER_PORT,
    }
    try:
        from nekopower.host import mount

        # The capability gateway picks its port when it starts to listen.
        port = getattr(getattr(mount.peek_host(), "gateway", None), "port", None)
        if isinstance(port, int):
            ports.add(port)
    except Exception as exc:
        logger.debug("[Builtin] the gateway's port is not known: %s", exc)
    return ports


# One per process; the browser it starts and the keys it holds are this process's.
provider = BuiltinToolProvider(state=_state, on_browser_window=_on_browser_window, own_ports=_own_ports)


def occupants(group: str) -> list[str]:
    """Return every commission occupying a group right now."""
    return _as_list(_in_use.get(group))


def occupant(group: str) -> Optional[str]:
    """Return the first commission that occupies a group right now, or None."""
    active = occupants(group)
    return active[0] if active else None


def _count(group: str) -> None:
    try:
        from utils.instrument import counter

        counter("agent_invoked", agent_type=_COUNTED_AS.get(group, group))
    except Exception:
        pass  # Counting is best effort and must not affect the work.


def activity_payload(group: str, state: str, commission_id: str) -> Dict[str, Any]:
    """Return the ``builtin_activity`` event for the main server.

    ``window`` says where the browser window being driven is, when that is
    known; for the keyboard and mouse the whole desktop is meant and it is
    None. A ``started`` event may come more than once for one occupation:
    again when the window is first known, and when it has moved.
    """
    window = provider.browser_window() if group == BUILTIN_GROUP_BROWSER and state == "started" else None
    return {
        "group": group,
        "state": state,
        "commission_id": commission_id,
        "window": window,
    }


def on_builtin_activity(group: str, state: str, commission_id: str) -> None:
    """Told by the host when a commission begins (``started``) or stops (``ended``) occupying a group.

    Runs on the event loop and must not block: the event is sent from a task.
    """
    active = _as_list(_in_use.get(group))
    if state == "started":
        if group == BUILTIN_GROUP_BROWSER:
            if commission_id not in active:
                active.append(commission_id)
            _in_use[group] = active
        else:
            _in_use[group] = [commission_id]
        _count(group)
        _send_activity(group, state, commission_id)
        return
    was_latest = bool(active and active[-1] == commission_id)
    active = [item for item in active if item != commission_id]
    if active:
        _in_use[group] = active
    else:
        _in_use.pop(group, None)
    _send_activity(group, state, commission_id)
    if group == BUILTIN_GROUP_BROWSER and was_latest and active:
        # Re-announce a remaining active occupant so single-slot consumers
        # (such as the desktop overlay banner) keep tracking the browser.
        _send_activity(group, "started", active[-1])


def _send_activity(group: str, state: str, commission_id: str) -> None:
    """Send one ``builtin_activity`` event to the main server from a task of its own; never raises."""
    try:
        from .nekopower_mount import hud
        from .results import _emit_main_event

        task = hud.get(commission_id) or {}
        _shared._create_tracked_task(
            _emit_main_event(ACTIVITY_EVENT, task.get("lanlan_name"), activity=activity_payload(group, state, commission_id))
        )
    except Exception as exc:
        # The banner is a display; the work does not depend on it.
        logger.debug("[Builtin] activity event not sent: %s", exc)
