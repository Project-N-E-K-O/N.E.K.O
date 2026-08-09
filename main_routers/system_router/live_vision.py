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

"""Read-only probe for the live screen/camera share.

Plugins run in their own processes and cannot see ``window.appState`` or the
session objects, but some of them need to know whether the user is already
sharing a screen with the character -- a plugin that would otherwise take its
own screenshot can stand down and reuse the frames the host is already
carrying. This endpoint is the one place that answer is published.

The payload can contain a picture of the user's desktop, so the route is
gated on ``verify_local_access`` and the frame itself is opt-in.
"""

from fastapi import Depends, Request
from fastapi.responses import Response

from main_routers.cookies_login_router import verify_local_access

from ..shared_state import get_session_manager
from ._shared import _set_no_store_headers, logger, router

_INACTIVE = {
    "active": False,
    "source": "",
    "age_seconds": None,
    "native_vision": False,
}


def _candidate_managers(role: str) -> list:
    """Managers worth asking, most specific request first."""
    try:
        session_manager = get_session_manager()
    except Exception as exc:
        # Not initialized yet (early startup, or a bare unit-test import).
        # Nothing is sharing anything, which is the honest answer.
        logger.debug("live-vision probe: session_manager unavailable: %s", exc)
        return []
    if role:
        mgr = session_manager.get(role)
        return [mgr] if mgr is not None else []
    return [m for m in session_manager.values() if m is not None]


def _pick_sharing_manager(managers: list) -> tuple:
    """Return the first manager with an active share, else the first one.

    Callers usually omit ``role`` because a plugin knows which game is running
    but not which character the user is talking to. Preferring the manager that
    is actually receiving frames makes the roleless query mean "is anyone
    sharing", which is the question being asked.
    """
    fallback = None
    for mgr in managers:
        snapshot_fn = getattr(mgr, "live_vision_snapshot", None)
        if not callable(snapshot_fn):
            continue
        try:
            state = snapshot_fn()
        except Exception as exc:
            logger.debug("live-vision probe: snapshot failed: %s", exc)
            continue
        if state.get("active"):
            return mgr, state
        if fallback is None:
            fallback = (mgr, state)
    return fallback if fallback is not None else (None, dict(_INACTIVE))


@router.get("/system/live-vision", dependencies=[Depends(verify_local_access)])
async def get_live_vision_state(
    request: Request,
    response: Response,
    role: str = "",
    include_frame: bool = False,
):
    """Report whether a screen/camera share is feeding the conversation."""
    del request  # consumed by the verify_local_access dependency
    _set_no_store_headers(response)

    mgr, state = _pick_sharing_manager(_candidate_managers(role))
    payload = {
        "ok": True,
        "role": str(getattr(mgr, "lanlan_name", "") or "") if mgr else "",
        "active": bool(state.get("active")),
        "source": str(state.get("source") or ""),
        "age_seconds": state.get("age_seconds"),
        "native_vision": bool(state.get("native_vision")),
    }

    if include_frame and payload["active"]:
        frame_fn = getattr(mgr, "live_vision_frame_b64", None)
        frame = ""
        if callable(frame_fn):
            try:
                frame = frame_fn()
            except Exception as exc:
                logger.debug("live-vision probe: frame read failed: %s", exc)
        if frame:
            payload["frame_b64"] = frame
            payload["frame_mime"] = "image/jpeg"

    return payload
