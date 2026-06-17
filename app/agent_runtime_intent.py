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

"""User-level persistence for agent runtime toggles.

The agent server keeps six in-memory switches:

    analyzer_enabled         — master gate (the "cat paw" master switch)
    computer_use_enabled     — keyboard/mouse control sub flag
    browser_use_enabled      — browser control sub flag
    user_plugin_enabled      — user plugin sub flag
    openclaw_enabled         — OpenClaw sub flag
    openfang_enabled         — OpenFang sub flag

Historically all six were re-zeroed on every process start, so the user had
to re-toggle every switch after restart. This module persists the user's
**intent** (last explicit toggle) under the config dir as
``agent_runtime_intent.json``, and a restore path at first ``greeting_check``
replays it through the existing ``set_agent_enabled`` / ``set_agent_flags``
codepaths (so capability checks still gate actual activation).

**Key semantics** (deliberately mirroring plugin runtime overrides):

* Only entries the user explicitly toggled are stored. Absence means
  "use the in-process default" (currently False).
* ``capability auto-disable`` (e.g. LLM probe demote) does NOT write here —
  intent survives transient LLM failures so the next restart still tries.
* The restore path WILL clear intent back to ``False`` after a hard 15s
  retry window or on a permanent failure code, so the user sees a clear
  "auto-disabled" notification instead of repeatedly retrying a dead key.
* Toggling the master gate off does NOT touch sub-flag intent — the master
  is a runtime gate, not a clear-all command. (See the matching fix in
  ``set_agent_enabled`` and the gate-fail branch of ``set_agent_flags``.)

The escape hatch ``NEKO_DISABLE_AGENT_AUTO_RESTORE=1`` is consumed by the
restore path itself, not this module — this module always reads/writes
faithfully so a misbehaving restore can be debugged by inspecting the JSON.
"""

from __future__ import annotations

import threading
from typing import Mapping, Optional

from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Agent")

INTENT_FILENAME = "agent_runtime_intent.json"

# Whitelisted keys. Anything else in the JSON is silently dropped so an old /
# malformed file can't blow up restore. Keep this in sync with the keys used
# in ``set_agent_enabled`` and ``set_agent_flags``.
INTENT_KEYS: frozenset[str] = frozenset({
    "analyzer_enabled",
    "computer_use_enabled",
    "browser_use_enabled",
    "user_plugin_enabled",
    "openclaw_enabled",
    "openfang_enabled",
})

_cache_lock = threading.Lock()
_cache: Optional[dict[str, bool]] = None


def _coerce_intent(raw: object) -> dict[str, bool]:
    if not isinstance(raw, Mapping):
        return {}
    result: dict[str, bool] = {}
    for key, value in raw.items():
        if isinstance(key, str) and key in INTENT_KEYS and isinstance(value, bool):
            result[key] = value
    return result


def _load_from_disk() -> dict[str, bool]:
    try:
        from utils.config_manager import get_config_manager

        cm = get_config_manager()
        raw = cm.load_json_config(INTENT_FILENAME, default_value={})
    except FileNotFoundError:
        return {}
    except Exception as exc:
        logger.warning(
            "Failed to load agent runtime intent from %s: %s",
            INTENT_FILENAME,
            exc,
        )
        return {}
    return _coerce_intent(raw)


def _save_to_disk(intent: dict[str, bool]) -> None:
    try:
        from utils.config_manager import get_config_manager

        cm = get_config_manager()
        cm.save_json_config(INTENT_FILENAME, dict(intent))
    except Exception as exc:
        logger.warning(
            "Failed to persist agent runtime intent to %s: %s",
            INTENT_FILENAME,
            exc,
        )


def load_intent() -> dict[str, bool]:
    """Return a snapshot of the persisted intent; loads on first access."""
    global _cache
    with _cache_lock:
        if _cache is None:
            _cache = _load_from_disk()
        return dict(_cache)


def get_intent(key: str) -> Optional[bool]:
    """Return the persisted intent for ``key`` or ``None`` if never set."""
    if not key or key not in INTENT_KEYS:
        return None
    return load_intent().get(key)


def set_intent(key: str, value: bool) -> None:
    """Persist ``value`` as the user's intent for ``key``.

    No-op if ``key`` is not in :data:`INTENT_KEYS` — restore relies on the
    schema being closed so unknown junk doesn't break parsing on next boot.

    The disk write happens while ``_cache_lock`` is still held so that two
    concurrent toggles can't race and overwrite each other with stale
    snapshots.
    """
    if not key or key not in INTENT_KEYS:
        return
    global _cache
    with _cache_lock:
        if _cache is None:
            _cache = _load_from_disk()
        if _cache.get(key) == value:
            return
        _cache[key] = value
        _save_to_disk(dict(_cache))


def clear_intent(key: str) -> None:
    """Remove the intent for ``key`` (e.g. after a permanent restore failure)."""
    if not key or key not in INTENT_KEYS:
        return
    global _cache
    with _cache_lock:
        if _cache is None:
            _cache = _load_from_disk()
        if key not in _cache:
            return
        _cache.pop(key, None)
        _save_to_disk(dict(_cache))


def reset_cache_for_testing() -> None:
    """Drop the in-memory cache; intended for tests that swap the backing store."""
    global _cache
    with _cache_lock:
        _cache = None
