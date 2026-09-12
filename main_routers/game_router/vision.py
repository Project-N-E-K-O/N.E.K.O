"""Bounded, stateless mini-game vision analysis. No screenshots, chat or memory writes."""
from __future__ import annotations

import asyncio
import base64
import binascii
import json
import re
from io import BytesIO

from fastapi import Request
from PIL import Image

from main_routers.system_router._shared import _validate_local_mutation_request
from utils.game_vision import MAX_DATA_URL_CHARS, analyze_game_vision, run_vision_preprocessing
from utils.game_route_state import _get_active_game_route_state

from ._shared import router

MAX_BODY_BYTES = 8 * 1024 * 1024 + 96 * 1024
MAX_ACTIVE_OPERATIONS = 4
REQUEST_TIMEOUT = 55.0
# Raw operations retain their slot until settlement even if a provider ignores
# cancellation. No wait queue, per-session registry, image cache or history.
_active_operations: set[asyncio.Task] = set()


def _failure(reason: str) -> dict:
    return {"ok": False, "reason": reason}


def _validate_payload(data: object) -> dict | None:
    allowed = {"lanlan_name", "session_id", "sdk_route_instance_id", "prompt", "image_data_url", "text", "attachments", "_csrf_token"}
    if not isinstance(data, dict) or data.keys() - allowed:
        return None
    for name, maximum in (("lanlan_name", 128), ("session_id", 128), ("sdk_route_instance_id", 128)):
        value = data.get(name)
        if not isinstance(value, str) or not value.strip() or len(value) > maximum:
            return None
    if "attachments" in data or "text" in data:
        if "prompt" in data or "image_data_url" in data:
            return None
        if not isinstance(data.get("text"), str) or not data["text"].strip() or len(data["text"]) > 16384:
            return None
        items = data.get("attachments")
        if not isinstance(items, list) or not 1 <= len(items) <= 4:
            return None
        for item in items:
            if (not isinstance(item, dict) or item.keys() - {"type", "image_data_url", "label"}
                    or item.get("type") != "image" or not isinstance(item.get("image_data_url"), str)
                    or len(item["image_data_url"]) > MAX_DATA_URL_CHARS
                    or not isinstance(item.get("label", ""), str) or len(item.get("label", "")) > 128):
                return None
        return data
    if not isinstance(data.get("prompt"), str) or not data["prompt"].strip() or len(data["prompt"]) > 4096:
        return None
    image = data.get("image_data_url")
    if not isinstance(image, str) or len(image) > 2 * 1024 * 1024 or not image.startswith("data:image/jpeg;base64,"):
        return None
    return data


def _current_route(game_type: str, data: dict, expected: dict | None = None) -> dict | None:
    state = _get_active_game_route_state(data["lanlan_name"], game_type)
    if (state is None or (expected is not None and state is not expected)
            or state.get("session_id") != data["session_id"]
            or state.get("_sdk_route_instance_id") != data["sdk_route_instance_id"]):
        return None
    return state


def _validated_image(value: str) -> str:
    """Check the legacy capture bounds; the shared service sanitizes it once."""
    try:
        raw = base64.b64decode(value.split(",", 1)[1], validate=True)
        with Image.open(BytesIO(raw)) as probe:
            if probe.format != "JPEG" or not (1 <= probe.width <= 1280 and 1 <= probe.height <= 720):
                raise ValueError("invalid_image")
            probe.verify()
        return value
    except (ValueError, OSError, binascii.Error, Image.DecompressionBombError) as exc:
        raise ValueError("invalid_image") from exc


async def _analyze(data: dict, image: str | None, current) -> str:
    return await analyze_game_vision(
        text=data["text"] if "attachments" in data else data["prompt"],
        attachments=data["attachments"] if "attachments" in data else [{"type": "image", "image_data_url": image}],
        is_current=current,
    )


async def _process(game_type: str, request: Request, progress: dict) -> dict:
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_BODY_BYTES:
            return _failure("payload_too_large")
        body.extend(chunk)
    data = _validate_payload(json.loads(body.decode("utf-8")))
    body.clear()
    if data is None:
        return _failure("invalid_payload")
    state = _current_route(game_type, data)
    if state is None:
        return _failure("route_inactive")
    current = lambda: _current_route(game_type, data, state) is not None
    progress["current"] = current
    # No competing ASGI receive while the request body is being consumed.
    progress["body_read"] = True
    image = (None if "attachments" in data else
             await run_vision_preprocessing(_validated_image, data["image_data_url"]))
    if not current():
        return _failure("route_inactive")
    text = await _analyze(data, image, current)
    if not current():
        return _failure("route_inactive")
    return {"ok": True, "text": text}


def _release(task: asyncio.Task) -> None:
    _active_operations.discard(task)
    if not task.cancelled():
        task.exception()  # retrieve late failure without logging private payloads


@router.post("/{game_type}/vision/analyze")
async def game_sdk_vision_analyze(game_type: str, request: Request):
    denied = _validate_local_mutation_request(request)
    if denied is not None:
        return denied
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,127}", game_type):
        return _failure("invalid_game")
    if len(_active_operations) >= MAX_ACTIVE_OPERATIONS:
        return _failure("busy")
    progress: dict = {}
    task = asyncio.create_task(_process(game_type, request, progress))
    _active_operations.add(task)
    task.add_done_callback(_release)
    deadline = asyncio.get_running_loop().time() + REQUEST_TIMEOUT
    try:
        while not task.done():
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return _failure("timeout")
            await asyncio.wait({task}, timeout=min(0.1, remaining))
            if progress.get("body_read"):
                if not progress["current"]():
                    return _failure("route_inactive")
                if await request.is_disconnected():
                    return _failure("cancelled")
        return task.result()
    except ValueError as exc:
        reason = str(exc)
        return _failure(reason if reason in {"invalid_image", "vision_unavailable", "vision_failed", "route_inactive", "invalid_model_response", "busy", "timeout", "unsupported_attachment"} else "invalid_payload")
    except Exception:
        return _failure("vision_failed")
    finally:
        if not task.done():
            task.cancel()
