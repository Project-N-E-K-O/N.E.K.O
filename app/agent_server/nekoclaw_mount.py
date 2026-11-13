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

"""Ties the NekoClaw host (``nekoclaw/host``) to this process.

``mount_nekoclaw_host`` runs during startup and must stay free: it tells the
host how to read this process's switches and subscribes it to plugin lifecycle
events. It does not build the host, open its ledger, start a runtime or
connect to anything; that happens on the first dispatch.

Nothing calls the host yet. The main dialogue's dispatch and query tools, the
commission board and the report sender are connected in a later change; until
then the analyzer path in this package keeps working exactly as before.
"""

from typing import Any, Callable, Optional

from . import _shared
from ._shared import logger

_unsubscribe: Optional[Callable[[], Any]] = None


def _master_enabled() -> bool:
    return bool(_shared.Modules.analyzer_enabled)


def _plugins_enabled() -> bool:
    return bool((_shared.Modules.agent_flags or {}).get("user_plugin_enabled", False))


def mount_nekoclaw_host(change_token: Optional[Callable[[], Any]] = None) -> None:
    """Register this process with the NekoClaw host. Cheap; safe to call twice."""
    global _unsubscribe
    try:
        from nekoclaw.host import mount

        mount.configure(
            enabled=_master_enabled,
            plugins_enabled=_plugins_enabled,
            change_token=change_token,
        )
        if _unsubscribe is None:
            # A plugin that starts, stops, reloads, is installed or removed
            # changes the capability list. The callback only sets a flag, so
            # it is fine that it runs on the plugin server's thread.
            from plugin.core.state import state as plugin_state

            _unsubscribe = plugin_state.bus_change_hub.subscribe("lifecycle", mount.notify_plugins_changed)
    except Exception as e:
        # The host is not in use yet; failing to register it must not affect startup.
        logger.warning("[Agent] NekoClaw host registration failed: %s", e)


async def shutdown_nekoclaw_host() -> None:
    """Stop the NekoClaw host if anything ever started it."""
    global _unsubscribe
    unsubscribe, _unsubscribe = _unsubscribe, None
    if unsubscribe is not None:
        try:
            unsubscribe()
        except Exception as e:
            logger.debug("[Agent] NekoClaw lifecycle unsubscribe failed: %s", e)
    try:
        from nekoclaw.host import mount

        await mount.shutdown_host()
    except Exception as e:
        logger.warning("[Agent] NekoClaw host shutdown error: %s", e)
