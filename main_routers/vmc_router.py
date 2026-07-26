# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
# Licensed under the Apache License, Version 2.0

"""REST control plane and dedicated WebSocket data plane for VMC output.

Routes use no trailing slash:

* ``GET /api/vmc/status``
* ``POST /api/vmc/enable|disable|t_pose``
* ``WS /api/vmc/ws`` (CSRF-authenticated VMC frames only)
"""

from __future__ import annotations

import asyncio
import json
import math
import secrets
import threading
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from config import AUTOSTART_ALLOWED_ORIGINS, AUTOSTART_CSRF_TOKEN
from main_logic.vmc_sender import get_vmc_sender
from main_routers.system_router import _validate_local_mutation_request
from utils.logger_config import get_module_logger

router = APIRouter(prefix="/api/vmc", tags=["vmc"])
logger = get_module_logger(__name__, "Main")

_MAX_WS_MESSAGE_BYTES = 256 * 1024
_WS_AUTH_TIMEOUT_SECONDS = 5.0
_WS_PUBLISHER_IDLE_TIMEOUT_SECONDS = 10.0
_PUBLISHER_DISCONNECT_GRACE_SECONDS = 2.0
_PUBLISHER_BUSY_CLOSE_CODE = 4429
_active_vmc_publisher: WebSocket | None = None
_active_vmc_publisher_guard = threading.Lock()
_publisher_generation = 0
_pending_terminal_task: asyncio.Task[None] | None = None


def _claim_active_vmc_publisher(websocket: WebSocket) -> int | None:
    """Atomically grant the single process-wide VMC publishing lease."""
    global _active_vmc_publisher, _publisher_generation
    with _active_vmc_publisher_guard:
        if _active_vmc_publisher is not None:
            return None
        _publisher_generation += 1
        _active_vmc_publisher = websocket
        return _publisher_generation


def _release_active_vmc_publisher(websocket: WebSocket) -> bool:
    global _active_vmc_publisher
    with _active_vmc_publisher_guard:
        if _active_vmc_publisher is websocket:
            _active_vmc_publisher = None
            return True
        return False


def _cancel_pending_terminal_task() -> None:
    global _pending_terminal_task
    task = _pending_terminal_task
    _pending_terminal_task = None
    if task is not None and not task.done():
        task.cancel()


async def _send_terminal_after_disconnect(
    sender: Any,
    publisher_generation: int,
) -> None:
    """Retire a publisher only if no authenticated successor takes over."""
    global _pending_terminal_task
    try:
        await asyncio.sleep(_PUBLISHER_DISCONNECT_GRACE_SECONDS)
        with _active_vmc_publisher_guard:
            if (
                _active_vmc_publisher is not None
                or publisher_generation != _publisher_generation
            ):
                return
        await asyncio.to_thread(
            sender.send_terminal_state,
            publisher_generation=publisher_generation,
        )
    except asyncio.CancelledError:
        return
    except Exception as exc:
        logger.warning("Failed to send delayed VMC terminal state: %s", exc)
    finally:
        current_task = asyncio.current_task()
        if _pending_terminal_task is current_task:
            _pending_terminal_task = None


def _schedule_terminal_after_disconnect(
    sender: Any,
    publisher_generation: int,
) -> None:
    global _pending_terminal_task
    _cancel_pending_terminal_task()
    _pending_terminal_task = asyncio.create_task(
        _send_terminal_after_disconnect(sender, publisher_generation)
    )


async def _vmc_frame_worker(
    queue: asyncio.Queue[dict[str, Any]],
    sender: Any,
    websocket: WebSocket,
    publisher_generation: int | None = None,
) -> None:
    """Send frames serially, keeping only the newest pending normal frame."""
    while True:
        item = await queue.get()
        sent = False
        try:
            try:
                if publisher_generation is None:
                    send_task = asyncio.create_task(
                        asyncio.to_thread(
                            sender.send_frame,
                            item["payload"],
                            force=item["force"],
                        )
                    )
                else:
                    send_task = asyncio.create_task(
                        asyncio.to_thread(
                            sender.send_frame,
                            item["payload"],
                            force=item["force"],
                            publisher_generation=publisher_generation,
                        )
                    )
                try:
                    sent = await asyncio.shield(send_task)
                except asyncio.CancelledError:
                    # Cancelling an asyncio.to_thread await does not stop its
                    # underlying thread. Drain it before the worker exits so a
                    # delayed terminal state cannot be overtaken by this frame.
                    try:
                        sent = await send_task
                    except Exception as exc:
                        logger.warning(
                            "Unexpected VMC frame worker error during shutdown: %s",
                            exc,
                        )
                    raise
            except Exception as exc:
                # A faulty frame or an unexpected sender implementation must
                # not permanently strand the bounded queue without a worker.
                # Report this frame as failed and continue with the newest one.
                logger.warning("Unexpected VMC frame worker error: %s", exc)
            if item["require_ack"]:
                try:
                    await websocket.send_json(
                        {
                            "type": "frame_ack",
                            "sequence": item["sequence"],
                            "sent": sent,
                        }
                    )
                except Exception:
                    return
        finally:
            completion = item.get("completion")
            if completion is not None and not completion.done():
                completion.set_result(sent)
            queue.task_done()


def _validate_endpoint(
    host: Any,
    port: Any,
    send_rate_hz: Any,
) -> tuple[str | None, int | None, int | None]:
    out_host: str | None = None
    if host is not None:
        if not isinstance(host, str):
            raise ValueError("host must be a string")
        candidate = host.strip()
        if not (
            candidate
            and len(candidate) <= 255
            and all(
                0x21 <= ord(char) <= 0x7E
                and (char.isalnum() or char in ".-_")
                for char in candidate
            )
        ):
            raise ValueError("host must be an ASCII hostname or IPv4 address")
        out_host = candidate

    out_port: int | None = None
    if port is not None:
        if (
            not isinstance(port, int)
            or isinstance(port, bool)
            or not 1 <= port <= 65535
        ):
            raise ValueError("port must be an integer between 1 and 65535")
        out_port = port

    out_rate: int | None = None
    if send_rate_hz is not None:
        if (
            not isinstance(send_rate_hz, int)
            or isinstance(send_rate_hz, bool)
            or not 1 <= send_rate_hz <= 120
        ):
            raise ValueError("send_rate_hz must be an integer between 1 and 120")
        out_rate = send_rate_hz
    return out_host, out_port, out_rate


async def _read_json_body(request: Request) -> dict[str, Any]:
    raw_body = await request.body()
    if not raw_body.strip():
        return {}
    try:
        body = json.loads(raw_body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("request body must contain valid JSON") from exc
    if not isinstance(body, dict):
        raise ValueError("request body must be a JSON object")
    return body


def _invalid_json_body_response(exc: ValueError) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={
            "success": False,
            "error_code": "invalid_json_body",
            "error": str(exc),
        },
    )


def _websocket_has_allowed_origin(websocket: WebSocket) -> bool:
    """Require a browser Origin from the server host or configured local hosts."""
    raw_origin = websocket.headers.get("origin", "")
    try:
        parsed_origin = urlsplit(raw_origin)
    except ValueError:
        return False
    if parsed_origin.scheme not in {"http", "https"} or not parsed_origin.hostname:
        return False

    request_host = websocket.url.hostname
    if request_host and parsed_origin.hostname.lower() == request_host.lower():
        return True

    origin_host = parsed_origin.hostname.lower()
    for allowed_origin in AUTOSTART_ALLOWED_ORIGINS:
        try:
            allowed_host = urlsplit(allowed_origin).hostname
        except (TypeError, ValueError):
            continue
        if allowed_host and allowed_host.lower() == origin_host:
            return True
    return False


def _valid_websocket_auth(message: Any) -> bool:
    if not isinstance(message, dict) or message.get("type") != "auth":
        return False
    token = message.get("csrf_token")
    return bool(
        isinstance(token, str)
        and token
        and AUTOSTART_CSRF_TOKEN
        and secrets.compare_digest(token, AUTOSTART_CSRF_TOKEN)
    )


@router.get("/status")
async def get_vmc_status():
    try:
        sender = get_vmc_sender()
        await sender.ensure_config_loaded()
        return JSONResponse(content={"success": True, **sender.status()})
    except Exception as exc:
        logger.error("get_vmc_status failed: %s", exc)
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(exc)},
        )


@router.post("/enable")
async def enable_vmc(request: Request):
    try:
        body = await _read_json_body(request)
    except ValueError as exc:
        return _invalid_json_body_response(exc)
    validation_error = _validate_local_mutation_request(request, payload=body)
    if validation_error is not None:
        return validation_error
    try:
        host, port, rate = _validate_endpoint(
            body.get("host"), body.get("port"), body.get("send_rate_hz")
        )
    except ValueError as exc:
        return JSONResponse(
            status_code=422,
            content={
                "success": False,
                "error_code": "invalid_vmc_endpoint",
                "error": str(exc),
            },
        )

    try:
        status = await get_vmc_sender().enable(
            host=host,
            port=port,
            send_rate_hz=rate,
        )
        return JSONResponse(content={"success": True, **status})
    except ImportError as exc:
        logger.error("VMC enable failed (missing dependency): %s", exc)
        return JSONResponse(
            status_code=503,
            content={
                "success": False,
                "error": "python-osc not installed; run `uv sync` to enable VMC",
            },
        )
    except Exception as exc:
        logger.error("VMC enable failed: %s", exc, exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(exc)},
        )


@router.post("/disable")
async def disable_vmc(request: Request):
    validation_error = _validate_local_mutation_request(request)
    if validation_error is not None:
        return validation_error
    try:
        status = await get_vmc_sender().disable()
        return JSONResponse(content={"success": True, **status})
    except Exception as exc:
        logger.error("VMC disable failed: %s", exc)
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(exc)},
        )


@router.post("/t_pose")
async def request_t_pose(request: Request):
    try:
        body = await _read_json_body(request)
    except ValueError as exc:
        return _invalid_json_body_response(exc)
    validation_error = _validate_local_mutation_request(request, payload=body)
    if validation_error is not None:
        return validation_error
    duration = body.get("duration_sec")
    duration_value: float | None = None
    invalid_duration = (
        duration is not None
        and (
            isinstance(duration, bool)
            or not isinstance(duration, (int, float))
        )
    )
    if duration is not None and not invalid_duration:
        try:
            duration_value = float(duration)
            invalid_duration = (
                not math.isfinite(duration_value) or duration_value <= 0
            )
        except (OverflowError, TypeError, ValueError):
            invalid_duration = True
    if invalid_duration:
        return JSONResponse(
            status_code=422,
            content={
                "success": False,
                "error_code": "invalid_vmc_t_pose",
                "error": "duration_sec must be a positive finite number",
            },
        )
    try:
        sender = get_vmc_sender()
        await sender.ensure_config_loaded()
        generation = sender.request_t_pose(duration_sec=duration_value)
        status = sender.status()
        return JSONResponse(
            content={
                "success": True,
                "t_pose_requested": True,
                "t_pose_duration_sec": status["t_pose_duration_sec"],
                "t_pose_generation": generation,
            }
        )
    except Exception as exc:
        logger.error("VMC T-Pose request failed: %s", exc)
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(exc)},
        )


@router.websocket("/ws")
async def vmc_websocket(websocket: WebSocket):
    """Receive VMC frames on a channel isolated from chat/session traffic."""
    if not _websocket_has_allowed_origin(websocket):
        await websocket.close(code=4403, reason="origin rejected")
        return

    await websocket.accept()
    frame_worker: asyncio.Task[None] | None = None
    publisher_claimed = False
    publisher_generation: int | None = None
    publisher_terminal_sent = False
    sender: Any = None
    try:
        raw_auth = await asyncio.wait_for(
            websocket.receive_text(),
            timeout=_WS_AUTH_TIMEOUT_SECONDS,
        )
        if len(raw_auth.encode("utf-8")) > _MAX_WS_MESSAGE_BYTES:
            await websocket.close(code=4409, reason="message too large")
            return
        try:
            auth = json.loads(raw_auth)
        except json.JSONDecodeError:
            auth = None
        if not _valid_websocket_auth(auth):
            await websocket.close(code=4403, reason="authentication failed")
            return

        publisher_generation = _claim_active_vmc_publisher(websocket)
        if publisher_generation is None:
            await websocket.close(
                code=_PUBLISHER_BUSY_CLOSE_CODE,
                reason="another VMC publisher is active",
            )
            return
        publisher_claimed = True

        sender = get_vmc_sender()
        sender.set_publisher_generation(publisher_generation)
        _cancel_pending_terminal_task()
        await sender.ensure_config_loaded()
        frame_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1)
        frame_worker = asyncio.create_task(
            _vmc_frame_worker(
                frame_queue,
                sender,
                websocket,
                publisher_generation,
            )
        )
        await websocket.send_json({"type": "ready"})
        loop = asyncio.get_running_loop()
        last_valid_frame_ts = loop.time()

        while True:
            remaining_idle = (
                _WS_PUBLISHER_IDLE_TIMEOUT_SECONDS
                - (loop.time() - last_valid_frame_ts)
            )
            if remaining_idle <= 0:
                await websocket.close(code=4428, reason="VMC publisher idle")
                return
            raw_message = await asyncio.wait_for(
                websocket.receive_text(),
                timeout=remaining_idle,
            )
            if len(raw_message.encode("utf-8")) > _MAX_WS_MESSAGE_BYTES:
                await websocket.close(code=4409, reason="message too large")
                return
            try:
                message = json.loads(raw_message)
            except json.JSONDecodeError:
                continue
            if not isinstance(message, dict):
                continue
            message_type = message.get("type")
            if message_type not in {"frame", "release"}:
                continue
            payload = message.get("payload")
            if not isinstance(payload, dict):
                continue
            sequence = message.get("sequence")
            if (
                not isinstance(sequence, int)
                or isinstance(sequence, bool)
                or sequence < 0
            ):
                continue
            last_valid_frame_ts = loop.time()

            if message_type == "release":
                # Drop a not-yet-started normal frame, then serialize release
                # behind any frame already in flight. This prevents an older
                # pose from racing past the zero-value release frame.
                if frame_queue.full():
                    try:
                        frame_queue.get_nowait()
                        frame_queue.task_done()
                    except asyncio.QueueEmpty:
                        pass
                release_completion: asyncio.Future[bool] = loop.create_future()
                frame_queue.put_nowait(
                    {
                        "payload": payload,
                        "sequence": sequence,
                        "require_ack": True,
                        "force": True,
                        "completion": release_completion,
                    }
                )
                release_sent = await release_completion
                if release_sent and payload.get("source_released") is True:
                    publisher_terminal_sent = True
                continue

            # A valid normal frame after a source-release starts a new active
            # source on the same socket. Its later disconnect needs a fresh
            # terminal state even if the previous source ended cleanly.
            publisher_terminal_sent = False
            require_ack = message.get("require_ack") is True
            # At 60 Hz, never let UDP work hold up WebSocket reads. Keep one
            # in-flight frame and at most one newest pending frame.
            if frame_queue.full():
                try:
                    frame_queue.get_nowait()
                    frame_queue.task_done()
                except asyncio.QueueEmpty:
                    pass
            frame_queue.put_nowait(
                {
                    "payload": payload,
                    "sequence": sequence,
                    "require_ack": require_ack,
                    "force": False,
                    "completion": None,
                }
            )
    except WebSocketDisconnect as exc:
        logger.debug("VMC WebSocket disconnected (code=%s)", exc.code)
        return
    except asyncio.TimeoutError:
        if publisher_claimed:
            logger.debug("VMC publisher lease expired after frame inactivity")
            try:
                await websocket.close(code=4428, reason="VMC publisher idle")
            except Exception:
                pass
        else:
            logger.debug("VMC WebSocket authentication timed out")
        return
    except Exception as exc:
        logger.debug("VMC WebSocket closed after error: %s", exc)
        try:
            await websocket.close(code=1011)
        except Exception:
            pass
    finally:
        if frame_worker is not None:
            frame_worker.cancel()
            try:
                await frame_worker
            except asyncio.CancelledError:
                pass
        if publisher_claimed:
            released = _release_active_vmc_publisher(websocket)
            if (
                released
                and not publisher_terminal_sent
                and sender is not None
                and publisher_generation is not None
            ):
                _schedule_terminal_after_disconnect(
                    sender,
                    publisher_generation,
                )
