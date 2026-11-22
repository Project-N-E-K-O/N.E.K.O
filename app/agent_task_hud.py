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

"""Whether the main page shows the task HUD, kept for the user.

The switch used to live only in the main page's browser storage, set from the
small panel of the hammer button. The hammer now opens the NekoPower page,
which is served from another port and cannot read that storage, so the agent
process keeps the setting under the config dir as ``agent_task_hud.json``
and hands it out in its status snapshot; the main page mirrors it into its
own storage.

``None`` means the user never set it here: the main page keeps what its own
storage says (shown, unless it was switched off there before) and hands that
in once. Kept apart from ``agent_runtime_intent.json`` on purpose: what is
there is replayed into the capability switches at start.
"""

from __future__ import annotations

import threading
from typing import Optional

from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Agent")

FILENAME = "agent_task_hud.json"

_lock = threading.Lock()
_loaded = False
_visible: Optional[bool] = None


def _load_from_disk() -> Optional[bool]:
    try:
        from utils.config_manager import get_config_manager

        raw = get_config_manager().load_json_config(FILENAME, default_value={})
    except FileNotFoundError:
        return None
    except Exception as exc:
        logger.warning("Failed to load the task HUD setting from %s: %s", FILENAME, exc)
        return None
    value = raw.get("visible") if isinstance(raw, dict) else None
    return value if isinstance(value, bool) else None


def get_visible() -> Optional[bool]:
    """Whether the user wants the task HUD shown, or ``None`` if never set here.

    The first call reads the file; the agent process makes it off the event
    loop at start (``api_runtime``), after which this only reads memory.
    """
    global _loaded, _visible
    with _lock:
        if not _loaded:
            _visible = _load_from_disk()
            _loaded = True
        return _visible


def set_visible(value: bool) -> bool:
    """Keep ``value``; return whether it changed. Written while the lock is held.

    The file is written first and what is held only changes once it is: a
    write that fails raises, leaves the setting as it was, and the same value
    asked for again is tried again. Blocking: call it off the event loop.
    """
    global _loaded, _visible
    with _lock:
        if not _loaded:
            _visible = _load_from_disk()
            _loaded = True
        if _visible is value:
            return False
        from utils.config_manager import get_config_manager

        get_config_manager().save_json_config(FILENAME, {"visible": value})
        _visible = value
        return True


def reset_cache_for_testing() -> None:
    """Forget what was read; for tests that swap the backing store."""
    global _loaded, _visible
    with _lock:
        _loaded = False
        _visible = None
