# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
"""Pure predicates over the saved community-session status dictionary.

``main_routers.community_oauth.resolve_saved_oauth_status`` produces the status
dictionary (``logged_in`` / ``snapshot`` / ``rejected`` / ...). The predicates
that read it live here, dependency-free, so ``services`` can interpret a status
without importing the router. ``main_routers.community_oauth`` re-exports them,
and its attribute consumers keep resolving the names there.
"""

from __future__ import annotations

from typing import Any


def status_session_saved(status: dict[str, Any]) -> bool:
    """A saved session the cloud could not verify right now (offline, 5xx, refresh unavailable).

    Not logged in, but also not a definite logout: a snapshot the cloud already
    rejected and only local cleanup left behind does not count.
    """
    return bool(status.get("snapshot")) and not status.get("rejected")


__all__ = ["status_session_saved"]
