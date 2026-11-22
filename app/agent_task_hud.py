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

# Two locks: ``_lock`` only guards what is held in memory and is never held
# across disk I/O, so the status snapshot (built on the event loop) never waits
# for a write; ``_write_lock`` puts writes one after another and is held while
# the file is written.
_lock = threading.Lock()
_write_lock = threading.Lock()
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
        if _loaded:
            return _visible
    read = _load_from_disk()  # not under ``_lock``
    with _lock:
        if not _loaded:
            _visible = read
            _loaded = True
        return _visible


def set_visible(value: bool, *, only_if_unset: bool = False) -> bool:
    """Keep ``value``; return whether it changed. Writes go one after another.

    The file is written first and what is held only changes once it is: a
    write that fails raises, leaves the setting as it was, and the same value
    asked for again is tried again. While the file is being written,
    ``get_visible`` answers the setting as it was. Blocking: call it off the
    event loop.

    ``only_if_unset`` is for a main page handing in what its own storage said
    before the setting lived here: it is kept only while nothing has been set
    here yet, checked under the write lock, so a choice the user made in the
    meantime is never overwritten by it.
    """
    global _loaded, _visible
    with _write_lock:
        current = get_visible()
        if only_if_unset and current is not None:
            return False
        if current is value:
            return False
        from utils.config_manager import get_config_manager

        get_config_manager().save_json_config(FILENAME, {"visible": value})
        with _lock:
            _visible = value
            _loaded = True
        return True


def reset_cache_for_testing() -> None:
    """Forget what was read; for tests that swap the backing store."""
    global _loaded, _visible
    with _write_lock, _lock:
        _loaded = False
        _visible = None
