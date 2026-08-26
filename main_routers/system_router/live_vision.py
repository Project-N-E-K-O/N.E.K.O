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

from collections.abc import Mapping

from fastapi import Depends, Request
from fastapi.responses import Response

from main_logic.core.live_frame_permissions import (
    allows_live_frame,
    revoke_plugin_permissions,
    set_live_frame_permission,
    set_plugin_delivery_permission,
)
from main_routers.cookies_login_router import verify_local_access
from utils.plugin_host_auth import LIVE_FRAME_TOKEN_HEADER, require_plugin_host_access

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
    """Prefer an active screen share, then another active share, then idle.

    Callers usually omit ``role`` because a plugin knows which game is running
    but not which character the user is talking to. A camera share cannot serve
    a game companion's desktop request, so a later screen share must outrank it.
    """
    fallback = None
    active_fallback = None
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
            if state.get("source") == "screen":
                return mgr, state
            if active_fallback is None:
                active_fallback = (mgr, state)
        elif fallback is None:
            fallback = (mgr, state)
    if active_fallback is not None:
        return active_fallback
    return fallback if fallback is not None else (None, dict(_INACTIVE))


@router.get("/system/live-vision", dependencies=[Depends(verify_local_access)])
async def get_live_vision_state(
    request: Request,
    response: Response,
    role: str = "",
    include_frame: bool = False,
    source_name: str = "",
):
    """Report whether a screen/camera share is feeding the conversation."""
    _set_no_store_headers(response)
    token = request.headers.get(LIVE_FRAME_TOKEN_HEADER, "")

    mgr, state = _pick_sharing_manager(_candidate_managers(role))
    payload = {
        "ok": True,
        "role": str(getattr(mgr, "lanlan_name", "") or "") if mgr else "",
        "active": bool(state.get("active")),
        "source": str(state.get("source") or ""),
        "age_seconds": state.get("age_seconds"),
        "native_vision": bool(state.get("native_vision")),
    }

    # Camera shares the user's room, not a desktop. The SDK documents
    # ``include_frame`` as a desktop image, and game companions reject camera
    # frames for the same reason — report liveness/source but withhold pixels.
    if (
        include_frame
        and allows_live_frame(source_name, token)
        and payload["active"]
        and payload["source"] == "screen"
    ):
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


@router.post(
    "/system/live-vision/attachment-permission",
    dependencies=[Depends(verify_local_access), Depends(require_plugin_host_access)],
)
async def set_live_frame_attachment_permission(
    request: Request,
    response: Response,
):
    """Install a plugin's live-frame generation before acknowledging it."""
    _set_no_store_headers(response)
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    return set_live_frame_permission(
        str(payload.get("source_name") or ""),
        str(payload.get("token") or ""),
        enabled=bool(payload.get("enabled")),
        host_generation=str(payload.get("host_generation") or ""),
    )


def _retract_plugin_deliveries(source_name: str) -> None:
    try:
        managers = get_session_manager()
    except Exception as exc:
        logger.debug("plugin-delivery retract: session_manager unavailable: %s", exc)
        return
    if not isinstance(managers, Mapping):
        return
    for mgr in managers.values():
        retract = getattr(mgr, "retract_callbacks_from_source", None)
        if callable(retract):
            try:
                retract(source_name)
            except Exception as exc:
                logger.debug("plugin-delivery retract failed: %s", exc)


@router.post(
    "/system/plugin-callbacks/delivery-permission",
    dependencies=[Depends(verify_local_access), Depends(require_plugin_host_access)],
)
async def set_plugin_callback_delivery_permission(
    request: Request,
    response: Response,
):
    """Install a plugin's spoken-cue generation, then retract queued ones."""
    _set_no_store_headers(response)
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    result = set_plugin_delivery_permission(
        str(payload.get("source_name") or ""),
        str(payload.get("token") or ""),
        enabled=bool(payload.get("enabled")),
        host_generation=str(payload.get("host_generation") or ""),
    )
    source = str(result.get("source_name") or "")
    token = str(result.get("token") or "")
    if source and token and not result["enabled"] and result.get("applied"):
        _retract_plugin_deliveries(source)
    return result


@router.post(
    "/system/plugin-permissions/revoke",
    dependencies=[Depends(verify_local_access), Depends(require_plugin_host_access)],
)
async def revoke_plugin_host_permissions(
    request: Request,
    response: Response,
):
    """Drop every host capability owned by a stopped plugin source."""
    _set_no_store_headers(response)
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    host_generation = str(payload.get("host_generation") or "")
    result = revoke_plugin_permissions(
        str(payload.get("source_name") or ""),
        host_generation,
    )
    source = str(result.get("source_name") or "")
    if source and (
        not host_generation or bool(result.get("delivery_revoked"))
    ):
        _retract_plugin_deliveries(source)
    return result
