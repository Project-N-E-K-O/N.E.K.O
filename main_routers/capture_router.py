# -*- coding: utf-8 -*-
"""
Capture Router

Cross-platform capture bridge HTTP entrypoint used by the galgame plugin's
``ElectronCaptureBackend`` when running under Linux pure-Wayland (where MSS
and PyAutoGUI cannot read other application windows).

Endpoints:

    GET  /api/capture/health       -- renderer reachable? (503 if not)
    POST /api/capture/screenshot   -- ask the connected Electron renderer
                                       to capture a window and return its
                                       data URL (data:image/jpeg;base64,...)

URL convention: routes declared WITHOUT trailing slash. See
``main_routers/characters_router.py`` docstring or
``.agent/rules/neko-guide.md`` (§"API URL 末尾不带斜杠") for rationale;
enforced by ``scripts/check_api_trailing_slash.py``.
"""

from __future__ import annotations

import asyncio
import ipaddress
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from utils.logger_config import get_module_logger

from utils import capture_bridge

router = APIRouter(prefix="/api/capture", tags=["capture"])
logger = get_module_logger(__name__, "Main")


def _is_loopback_request(request: Request) -> bool:
    client_host = request.client.host if request.client else ""
    if client_host == "localhost":
        return True
    normalized_host = str(client_host or "").removeprefix("::ffff:")
    try:
        return ipaddress.ip_address(normalized_host).is_loopback
    except ValueError:
        return False


class CaptureRequestBody(BaseModel):
    """Incoming payload from galgame plugin's ElectronCaptureBackend.

    ``target_id`` accepts both int (raw HWND on Windows / 0 on Wayland) and
    str (renderer-supplied source.id). Router normalises to str before
    handing off to the renderer because Electron ``desktopCapturer`` source
    IDs are always strings like ``"window:123456:0"``.
    """

    model_config = ConfigDict(extra="forbid")

    target_id: int | str = Field(...)
    pid: int = Field(..., gt=0)
    title: str = Field(default="", max_length=512)


@router.get("/health")
async def capture_health(request: Request):
    if not _is_loopback_request(request):
        return JSONResponse({"success": False, "error": "loopback_only"}, status_code=403)
    if not capture_bridge.has_capture_client():
        return JSONResponse(
            {"success": False, "available": False, "error": "no_renderer"},
            status_code=503,
        )
    return JSONResponse({"success": True, "available": True})


@router.post("/screenshot")
async def capture_screenshot(request: Request):
    if not _is_loopback_request(request):
        return JSONResponse({"success": False, "error": "loopback_only"}, status_code=403)

    try:
        raw = await request.json()
    except Exception:
        return JSONResponse({"success": False, "error": "invalid_json"}, status_code=400)

    try:
        body = CaptureRequestBody.model_validate(raw)
    except ValidationError as exc:
        return JSONResponse(
            {"success": False, "error": "validation_error", "detail": exc.errors()},
            status_code=422,
        )

    # Normalise target_id to string before crossing the bridge: Electron
    # desktopCapturer source.id is always a string. The plugin side may
    # pass an int hwnd (e.g. 123456 on Windows, 0 on Wayland).
    payload: dict[str, Any] = {
        "target_id": str(body.target_id),
        "pid": body.pid,
        "title": body.title,
    }

    # Cheap pre-flight: target_id length is also enforced in capture_bridge
    # but rejecting at 422 here gives the plugin a clearer signal.
    if len(payload["target_id"]) == 0 or len(payload["target_id"]) > capture_bridge.MAX_TARGET_ID_LEN:
        return JSONResponse(
            {"success": False, "error": "validation_error", "detail": "target_id length"},
            status_code=422,
        )

    if not capture_bridge.has_capture_client():
        return JSONResponse(
            {"success": False, "error": "no_renderer"},
            status_code=503,
        )

    try:
        response = await capture_bridge.request_capture_screenshot(payload)
    except capture_bridge.CaptureBridgeError as exc:
        message = str(exc)
        # ``source_not_found`` is propagated verbatim by the renderer; treat
        # it as a soft failure that the OCR tick can retry next round.
        if message == "source_not_found":
            return JSONResponse(
                {"success": False, "error": "source_not_found"},
                status_code=502,
            )
        if "timeout" in message.lower():
            return JSONResponse(
                {"success": False, "error": "renderer_timeout"},
                status_code=504,
            )
        if "no renderer" in message.lower():
            return JSONResponse(
                {"success": False, "error": "no_renderer"},
                status_code=503,
            )
        # Default: upstream bridge error.
        logger.warning("[capture_router] bridge error: %s", message)
        return JSONResponse(
            {"success": False, "error": "bridge_error", "detail": message},
            status_code=502,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        # Unexpected error path. We intentionally avoid logging payload
        # because it may contain renderer-returned image data; only the
        # exception class is logged.
        logger.error("[capture_router] unexpected error: %s", type(exc).__name__)
        return JSONResponse(
            {"success": False, "error": "internal_error"},
            status_code=500,
        )

    image = response.get("image") if isinstance(response, dict) else None
    if not isinstance(image, str) or not image:
        return JSONResponse(
            {"success": False, "error": "empty_image"},
            status_code=502,
        )

    body_out: dict[str, Any] = {
        "success": True,
        "image": image,
    }
    width = response.get("width") if isinstance(response, dict) else None
    height = response.get("height") if isinstance(response, dict) else None
    if isinstance(width, int) and width > 0:
        body_out["width"] = width
    if isinstance(height, int) and height > 0:
        body_out["height"] = height
    source_id = response.get("source_id") if isinstance(response, dict) else None
    if isinstance(source_id, str) and source_id:
        body_out["source_id"] = source_id
    return JSONResponse(body_out)
