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

"""Event emission toward main_server.

Everything here goes through ``Modules.agent_bridge`` (ZeroMQ); there is
deliberately no HTTP fallback. How a task ended is not emitted from here any
more: the NekoClaw host stores each report in its outbox first and
``nekoclaw_mount`` sends it, so a report that could not be delivered is tried
again instead of being lost.
"""

from typing import Any, Dict, Optional

from . import _shared
from ._shared import logger


async def _send_main_event(event: Dict[str, Any]) -> bool:
    """Hand one event to the bridge. Returns whether it was taken."""
    bridge = _shared.Modules.agent_bridge
    if bridge is None:
        return False
    try:
        return bool(await bridge.emit_to_main(event))
    except Exception as e:
        logger.warning("[Agent] event not sent: type=%s error=%s", event.get("event_type"), e)
        return False


async def _emit_main_event(event_type: str, lanlan_name: Optional[str], **payload) -> None:
    event = {"event_type": event_type, "lanlan_name": lanlan_name, **payload}
    if _shared.Modules.agent_bridge is None:
        logger.debug("[Agent] _emit_main_event skipped: no agent_bridge, type=%s", event_type)
        return
    if not await _send_main_event(event):
        logger.debug("[Agent] _emit_main_event not sent: type=%s lanlan=%s", event_type, lanlan_name)
