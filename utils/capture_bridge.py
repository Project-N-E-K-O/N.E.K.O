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
Capture Bridge Registry

Maintains the link between the N.E.K.O Python main server and the connected
N.E.K.O.-PC Electron renderer for the galgame OCR cross-platform capture
fallback path (Phase 5 — Electron Capture Bridge).

Pipeline:

    galgame_plugin ElectronCaptureBackend
      -> HTTP POST /api/capture/screenshot (capture_router.py)
      -> request_capture_screenshot()  [this module]
      -> WebSocket capture_bridge_request to renderer
      -> renderer's window.electronDesktopCapturer.*
      -> capture_bridge_response back via WebSocket
      -> resolve_capture_response()    [this module]
      -> data:image/jpeg;base64 returned to plugin

Privacy / safety:
  * image / base64 content is NEVER passed to logger; only ``print`` is used
    for diagnostic noise when the image payload itself is malformed.
  * pending request Futures are always cleaned up on timeout / disconnect,
    never leaked.
  * a single asyncio.Semaphore(1) serialises capture requests because OCR
    ticks are inherently sequential and concurrent captures are useless.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from typing import Any

from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Main")

# response image upper bound (base64 chars). ~10MB base64 ≈ 7.5MB raw.
MAX_IMAGE_BASE64_BYTES = 10 * 1024 * 1024

# target_id is a string after router-side normalisation (str(int hwnd)).
# Electron source.id like "window:123456:0" is also bounded; keep generous
# but bounded to avoid abuse.
MAX_TARGET_ID_LEN = 128

# Default per-request timeout when callers do not supply one.
DEFAULT_REQUEST_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class _CaptureCapabilities:
    get_sources: bool
    capture_source_as_data_url: bool
    capture_source_without_neko: bool


@dataclass
class _CaptureClient:
    lanlan_name: str
    websocket: Any
    capabilities: _CaptureCapabilities
    registered_at: float


# Registry state -----------------------------------------------------------
# Only one renderer is expected to provide capture at a time. The newest
# `mark_capture_client` wins; the previous registration (if for a different
# WebSocket) is unregistered first so its pending futures are cleaned up.
_clients: dict[str, _CaptureClient] = {}
_pending_by_client: dict[str, dict[str, asyncio.Future]] = {}
_capture_semaphore = asyncio.Semaphore(1)


def _coerce_bool(value: Any) -> bool:
    return bool(value)


def _build_capabilities(payload: dict[str, Any]) -> _CaptureCapabilities:
    caps = payload.get("capabilities") or {}
    if not isinstance(caps, dict):
        caps = {}
    return _CaptureCapabilities(
        get_sources=_coerce_bool(caps.get("getSources")),
        capture_source_as_data_url=_coerce_bool(caps.get("captureSourceAsDataUrl")),
        capture_source_without_neko=_coerce_bool(caps.get("captureSourceWithoutNeko")),
    )


def _drop_client_pendings(lanlan_name: str, *, reason: str) -> None:
    pending = _pending_by_client.pop(lanlan_name, None)
    if not pending:
        return
    for request_id, future in pending.items():
        if not future.done():
            future.set_exception(
                CaptureBridgeError(f"renderer {lanlan_name!r} {reason} (request_id={request_id})")
            )


def _drop_websocket_aliases(lanlan_name: str, websocket: Any) -> None:
    for old_name, client in list(_clients.items()):
        if old_name == lanlan_name or client.websocket is not websocket:
            continue
        _clients.pop(old_name, None)
        _drop_client_pendings(old_name, reason="was replaced by new renderer")


class CaptureBridgeError(RuntimeError):
    """Raised when a capture request cannot be fulfilled by the bridge."""


def mark_capture_client(lanlan_name: str, websocket: Any, payload: dict[str, Any]) -> None:
    """Register / refresh / unregister an Electron renderer.

    payload comes straight from ``capture_bridge_status`` WebSocket message;
    when ``payload["available"]`` is falsy, this is equivalent to
    :func:`unmark_capture_client`.
    """
    if not isinstance(lanlan_name, str) or not lanlan_name:
        return
    if not isinstance(payload, dict):
        payload = {}

    if not _coerce_bool(payload.get("available")):
        unmark_capture_client(lanlan_name, expected_websocket=websocket)
        return

    _drop_websocket_aliases(lanlan_name, websocket)

    existing = _clients.get(lanlan_name)
    if existing is not None and existing.websocket is not websocket:
        # different socket -- old registration is dead.
        _drop_client_pendings(lanlan_name, reason="was replaced by new renderer")

    registered_at = time.time()
    if existing is not None:
        registered_at = max(registered_at, existing.registered_at + 1e-6)

    _clients[lanlan_name] = _CaptureClient(
        lanlan_name=lanlan_name,
        websocket=websocket,
        capabilities=_build_capabilities(payload),
        registered_at=registered_at,
    )
    _pending_by_client.setdefault(lanlan_name, {})
    logger.info("[capture_bridge] renderer registered: lanlan_name=%s", lanlan_name)


def unmark_capture_client(lanlan_name: str, *, expected_websocket: Any | None = None) -> None:
    """Unregister an Electron renderer and resolve its pending futures."""
    if not isinstance(lanlan_name, str) or not lanlan_name:
        return
    existing = _clients.get(lanlan_name)
    if (
        expected_websocket is not None
        and existing is not None
        and existing.websocket is not expected_websocket
    ):
        return
    had_client = _clients.pop(lanlan_name, None) is not None
    _drop_client_pendings(lanlan_name, reason="disconnected")
    if had_client:
        logger.info("[capture_bridge] renderer unregistered: lanlan_name=%s", lanlan_name)


def has_capture_client() -> bool:
    """True iff at least one Electron renderer is registered and ready."""
    return bool(_clients)


def _pick_client() -> _CaptureClient | None:
    if not _clients:
        return None
    # newest registration wins (single-renderer assumption)
    return max(_clients.values(), key=lambda c: c.registered_at)


def _validate_target_id(target_id: Any) -> str:
    if isinstance(target_id, str):
        normalized = target_id
    elif isinstance(target_id, int) and not isinstance(target_id, bool):
        normalized = str(target_id)
    else:
        raise CaptureBridgeError("target_id must be int or str")
    if not normalized:
        raise CaptureBridgeError("target_id must be non-empty")
    if len(normalized) > MAX_TARGET_ID_LEN:
        raise CaptureBridgeError("target_id length exceeds limit")
    return normalized


def _validate_pid(pid: Any) -> int:
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        raise CaptureBridgeError("pid must be positive integer")
    return pid


def _validate_title(title: Any) -> str:
    if title is None:
        return ""
    if not isinstance(title, str):
        raise CaptureBridgeError("title must be string")
    # title is a matching hint only; keep it bounded to prevent abuse.
    return title[:256]


async def request_capture_screenshot(
    payload: dict[str, Any],
    *,
    timeout: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Send ``capture_bridge_request`` to the renderer and await response.

    Returns the renderer payload on success. Raises :class:`CaptureBridgeError`
    on missing renderer / timeout / oversized image / renderer-reported failure.
    """
    target_id = _validate_target_id(payload.get("target_id"))
    pid = _validate_pid(payload.get("pid"))
    title = _validate_title(payload.get("title"))

    async with _capture_semaphore:
        client = _pick_client()
        if client is None:
            raise CaptureBridgeError("no renderer available")

        request_id = uuid.uuid4().hex
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        _pending_by_client.setdefault(client.lanlan_name, {})[request_id] = future

        request_payload: dict[str, Any] = {
            "type": "capture_bridge_request",
            "request_id": request_id,
            "target_id": target_id,
            "pid": pid,
            "title": title,
        }

        try:
            await client.websocket.send_text(_dumps(request_payload))
        except Exception as exc:
            _pending_by_client.get(client.lanlan_name, {}).pop(request_id, None)
            raise CaptureBridgeError(f"failed to send request: {exc}") from exc

        try:
            response = await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise CaptureBridgeError("renderer response timeout") from exc
        finally:
            _pending_by_client.get(client.lanlan_name, {}).pop(request_id, None)

        return _validate_response_payload(response)


def resolve_capture_response(lanlan_name: str, payload: dict[str, Any]) -> None:
    """Hand a renderer response back to the pending Future, if any."""
    if not isinstance(payload, dict):
        return
    request_id = payload.get("request_id")
    if not isinstance(request_id, str) or not request_id:
        return
    pending = _pending_by_client.get(lanlan_name)
    if not pending:
        return
    future = pending.pop(request_id, None)
    if future is None or future.done():
        return
    future.set_result(payload)


# Helpers ------------------------------------------------------------------

def _dumps(payload: dict[str, Any]) -> str:
    import json
    return json.dumps(payload, ensure_ascii=False)


def _validate_response_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise CaptureBridgeError("renderer returned malformed payload")
    if not payload.get("success"):
        error = payload.get("error") or "renderer_capture_failed"
        if not isinstance(error, str):
            error = "renderer_capture_failed"
        # source_not_found is a normal soft-fail signal; surface it verbatim
        raise CaptureBridgeError(error)
    image = payload.get("image")
    if not isinstance(image, str) or not image:
        # Don't log the image; just print a redacted hint.
        print("[capture_bridge] renderer success without image payload")
        raise CaptureBridgeError("renderer returned empty image")
    if len(image) > MAX_IMAGE_BASE64_BYTES:
        # Don't log the image content itself.
        print("[capture_bridge] renderer image exceeds size limit; rejecting")
        raise CaptureBridgeError("image exceeds size limit")
    return payload


# Test helpers -------------------------------------------------------------

def _reset_for_tests() -> None:
    """Wipe registry state. Tests only."""
    global _capture_semaphore
    _clients.clear()
    for pendings in list(_pending_by_client.values()):
        for fut in pendings.values():
            if not fut.done():
                fut.cancel()
    _pending_by_client.clear()
    _capture_semaphore = asyncio.Semaphore(1)


def _snapshot_for_tests() -> dict[str, Any]:
    return {
        "clients": list(_clients.keys()),
        "pending_counts": {k: len(v) for k, v in _pending_by_client.items()},
    }
