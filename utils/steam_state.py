# -*- coding: utf-8 -*-
"""
Steamworks process-global handle.

The Steamworks SDK is initialized once during app startup (``app/main_server.py``)
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

from typing import Any, Optional

# Module-global handle. ``None`` is a valid resting state — Steamworks may fail
# to initialize (Steam client not running, dev/CI environment without DLL),
# and every caller is expected to handle that.
_steamworks: Optional[Any] = None


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
    _steamworks = steamworks
