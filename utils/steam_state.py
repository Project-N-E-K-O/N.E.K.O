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

"""
Steamworks process-global handle.

The Steamworks SDK is initialized once during app startup (``app/main_server/__init__.py``)
and consumed by code at every layer of the dependency stack:

* high-level routers / brain agents (e.g. ``main_routers/workshop_router.py``,
  ``brain/browser_use_adapter.py``)
* mid-level helpers (e.g. ``utils/config_manager.py``'s GeoIP probe,
  ``utils/language_utils.py``'s Steam-language detection)

Originally the singleton lived in ``main_routers.shared_state`` together with
the per-role state. That forced low-layer modules (``utils/*``) to import from
``main_routers``, creating a layering inversion (``utils → main_routers``) that
also closed a cycle (``main_routers → utils → main_routers``).

This module owns the singleton at the lowest practical layer (``utils``, L1),
where every consumer is allowed to depend on it. ``main_routers.shared_state``
re-exports the same getter/setter as a thin proxy so the legacy public API is
unchanged. See ``scripts/check_module_layering.py`` for the enforced ordering.

The state is module-global by design: a single Steamworks SDK instance per
process is the SDK's own constraint (``SteamAPI_Init`` is process-singleton).
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable, Optional

# Module-global handle. ``None`` is a valid resting state — Steamworks may fail
# to initialize (Steam client not running, dev/CI environment without DLL),
# and every caller is expected to handle that.
_steamworks: Optional[Any] = None
_steamworks_initializer: Optional[Callable[[], Optional[Any]]] = None
_steamworks_lock = threading.RLock()
_last_init_attempt_monotonic = 0.0


def get_steamworks() -> Optional[Any]:
    """Return the process-global Steamworks instance, or ``None`` if not ready.

    May be called from any layer at any time. Returns ``None`` both before
    initialization and when Steamworks is permanently unavailable on this host;
    callers must handle ``None`` gracefully.
    """
    return _steamworks


def set_steamworks(steamworks: Optional[Any]) -> None:
    """Install the Steamworks instance. Called once during app startup.

    Pass ``None`` to clear (e.g. on shutdown / failed init).
    """
    global _steamworks
    with _steamworks_lock:
        _steamworks = steamworks


def set_steamworks_initializer(initializer: Optional[Callable[[], Optional[Any]]]) -> None:
    """Register a process-local Steamworks initializer for lazy reconnects.

    The callback is installed by ``app/main_server/__init__.py`` because only that layer
    knows how to create and wire the SDK instance. Keeping the callback here
    lets routers ask for a retry without importing ``main_server``.
    """
    global _steamworks_initializer
    with _steamworks_lock:
        _steamworks_initializer = initializer


def ensure_steamworks(*, force: bool = False, min_retry_interval_seconds: float = 5.0) -> Optional[Any]:
    """Return Steamworks, trying to initialize it when it is currently absent.

    This is intentionally separate from ``get_steamworks()`` so low-level
    helpers can keep doing cheap read-only probes. Steam-facing API endpoints
    call this function to support the common flow "start N.E.K.O first, open
    Steam later".
    """
    global _steamworks, _last_init_attempt_monotonic
    with _steamworks_lock:
        if _steamworks is not None:
            return _steamworks
        if _steamworks_initializer is None:
            return None

        now = time.monotonic()
        if (
            not force
            and _last_init_attempt_monotonic > 0
            and now - _last_init_attempt_monotonic < min_retry_interval_seconds
        ):
            return None

        _last_init_attempt_monotonic = now
        _steamworks = _steamworks_initializer()
        return _steamworks
