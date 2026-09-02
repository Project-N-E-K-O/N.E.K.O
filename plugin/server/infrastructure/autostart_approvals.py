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

"""Plugins that were installed but never started by the user.

A plugin's manifest declares ``plugin_runtime.auto_start`` and it defaults to
true, so a freshly installed plugin would run its own code at the next greeting
without the user ever having started it. Installing and running are different
acts, and only the second one is the user's.

The record is deliberately a list of plugins **awaiting** approval rather than a
list of approved ones. An approved-list needs a baseline — some moment where
every already-installed plugin is grandfathered in — and getting that baseline
wrong in the quiet direction (seeding it while the registry happens to be empty,
or before a refresh that failed) permanently silences a user's whole autostart
set. With a pending-list, the absence of a record means "not our business", so
the failure mode of every bug in this file is a plugin autostarting the way it
always did.

Entries are added when a plugin is newly installed and removed the first time
the user starts or enables it — and only once that start has been durably
recorded. Clearing before the runtime preference lands would grant autostart on
the strength of an intent that never persisted.
"""

from __future__ import annotations

import threading

from plugin.logging_config import get_logger

logger = get_logger("server.infrastructure.autostart_approvals")

PENDING_FILENAME = "plugin_autostart_pending.json"

_lock = threading.Lock()
_cache: set[str] | None = None


def _load_locked() -> set[str]:
    global _cache
    if _cache is not None:
        return _cache
    try:
        from utils.config_manager import get_config_manager

        raw = get_config_manager().load_json_config(PENDING_FILENAME)
    except FileNotFoundError:
        _cache = set()
        return _cache
    except Exception as exc:
        # 读不出来就当作没有待批准记录。这个方向的错误是"新装插件照常自启"，
        # 也就是这个功能出现之前的行为；反方向是"用户的插件集体不启动"。
        logger.error("failed to load {}: {}", PENDING_FILENAME, exc)
        _cache = set()
        return _cache

    pending: set[str] = set()
    if isinstance(raw, dict):
        items = raw.get("pending")
        if isinstance(items, list):
            pending = {item for item in items if isinstance(item, str) and item}
    _cache = pending
    return _cache


def _save_locked(pending: set[str]) -> None:
    try:
        from utils.config_manager import get_config_manager

        get_config_manager().save_json_config(
            PENDING_FILENAME, {"pending": sorted(pending)}
        )
    except Exception as exc:
        logger.error("failed to persist {}: {}", PENDING_FILENAME, exc)


def mark_autostart_pending(plugin_id: str) -> None:
    """Record that ``plugin_id`` was installed but never started by the user."""
    normalized = str(plugin_id or "").strip()
    if not normalized:
        return
    with _lock:
        pending = _load_locked()
        if normalized in pending:
            return
        pending.add(normalized)
        _save_locked(pending)
        logger.info(
            "plugin {} installed; it will not autostart until the user starts it",
            normalized,
        )


def clear_autostart_pending(plugin_id: str) -> None:
    """Record that the user started or enabled ``plugin_id`` themselves."""
    normalized = str(plugin_id or "").strip()
    if not normalized:
        return
    with _lock:
        pending = _load_locked()
        if normalized not in pending:
            return
        pending.discard(normalized)
        _save_locked(pending)


def is_autostart_approved(plugin_id: str) -> bool:
    """Whether ``plugin_id`` may start itself at server startup."""
    with _lock:
        return str(plugin_id or "").strip() not in _load_locked()


def _reset_cache_for_testing() -> None:
    global _cache
    with _lock:
        _cache = None
