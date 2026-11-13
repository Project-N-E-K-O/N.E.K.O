# -- coding: utf-8 --
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

"""Turn-scoped, read-only window capture and original-pixel region inspection."""

from __future__ import annotations

import asyncio
import base64
import binascii
import io
import math
import time
from functools import partial
from uuid import uuid4

from PIL import Image, UnidentifiedImageError

from config.prompts.prompts_screen_inspection import ERRORS, text
from main_logic.tool_calling import ToolDefinition
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Main")
SNAPSHOT_TTL = 120
MAX_IMAGES = 4
MAX_IMAGE_PIXELS = 25_000_000
MAX_CACHE_PIXELS = 40_000_000
MAX_DATA_URL_BYTES = 12 * 1024 * 1024
CAPTURE_TIMEOUT = 10


class InspectionError(ValueError):
    """A stable error code, localized only at the model boundary."""


def build_tools(manager):
    language = getattr(manager, "user_language", None)
    return [
        ToolDefinition(
            name="read_current_screen",
            description=text("overview", language),
            parameters={
                "type": "object",
                "properties": {
                    "snapshot_id": {"type": "string"},
                    "window_id": {"type": "string"},
                    "region": {
                        "type": "array",
                        "items": {"type": "number", "minimum": 0, "maximum": 1},
                        "minItems": 4,
                        "maxItems": 4,
                    },
                },
                "required": [],
            },
            handler=partial(read_screen, manager),
            metadata={"source": "builtin"},
        )
    ]


async def read_screen(manager, arguments=None):
    """One model-facing entry; continuation arguments are disclosed in results."""
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        return failure(manager, "invalid_request")
    # Some providers serialize every optional schema field with an empty value.
    # Ignore those placeholders while keeping non-empty combinations strict.
    arguments = {key: value for key, value in arguments.items()
                 if value is not None and value != "" and value != [] and value != {}}
    if not arguments:
        return await inspect(manager)
    keys = set(arguments)
    if keys == {"snapshot_id", "window_id"}:
        return await inspect(manager, "window", arguments)
    if keys == {"snapshot_id", "region"}:
        return await inspect(manager, "region", {**arguments, "target": "region"})
    return failure(manager, "invalid_request")


def failure(manager, code):
    if code not in ERRORS:
        code = "capture_failed"
    language = getattr(manager, "user_language", None)
    return {
        "is_error": True,
        "output": {
            "status": "unavailable",
            "reason": code,
            "message": text(code, language),
            "guidance": text(
                {"stale_snapshot": "restart", "invalid_request": "invalid_arguments"}.get(code, "unavailable"),
                language,
            ),
        },
        "images": [],
    }


def _owner(manager):
    return (manager.session, manager.current_speech_id, id(manager.websocket), manager.websocket,
            getattr(manager, "_proactive_vision_revocation_epoch", 0))


async def _check_permission(manager, owner):
    """Recheck consent after asynchronous image work, including revoke/re-enable."""
    declined = getattr(manager, "_proactive_screenshot_declined", None)
    if getattr(manager, "_proactive_vision_enabled", None) is False or (declined and await declined()):
        raise InspectionError("disabled")
    if owner != _owner(manager):
        raise InspectionError("superseded")


def _state(manager):
    owner = _owner(manager)
    state = getattr(manager, "_screen_inspection_state", None)
    if state is None or state["owner"] != owner:
        if state:
            state["images"].clear()
        state = {"owner": owner, "images": {}}
        manager._screen_inspection_state = state
    now = time.monotonic()
    for key, saved in list(state["images"].items()):
        if saved["expires"] <= now:
            del state["images"][key]
    return state


def resolve_response(manager, websocket, message):
    pending = getattr(manager, "_screen_inspection_pending", None)
    if (
        not pending
        or pending["websocket"] is not websocket
        or message.get("request_id") != pending["id"]
    ):
        return
    future = pending["future"]
    if not future.done():
        data = message.get("data_url", "")
        if not isinstance(data, str) or len(data) > MAX_DATA_URL_BYTES:
            future.set_result({"success": False, "error": "invalid_image"})
        else:
            future.set_result(message)


async def _request(manager, operation, **kwargs):
    pending = getattr(manager, "_screen_inspection_pending", None)
    if pending and not pending["future"].done():
        raise InspectionError("busy")
    websocket = manager.websocket
    if not websocket:
        raise InspectionError("disconnected")
    owner = _owner(manager)
    pending = {
        "id": uuid4().hex,
        "websocket": websocket,
        "future": asyncio.get_running_loop().create_future(),
    }
    manager._screen_inspection_pending = pending
    try:
        async with asyncio.timeout(CAPTURE_TIMEOUT):
            await websocket.send_json(
                {
                    "type": "request_screen_inspection",
                    "request_id": pending["id"],
                    "operation": operation,
                    **kwargs,
                }
            )
            reply = await pending["future"]
        if owner != _owner(manager) or manager.websocket is not websocket:
            raise InspectionError("superseded")
        if reply.get("success") is not True:
            _state(manager)["images"].clear()
            raise InspectionError(
                reply.get("error") if reply.get("error") in ERRORS else "capture_failed"
            )
        if not isinstance(reply.get("scope"), str) or (
            "scope" in kwargs and reply["scope"] != kwargs["scope"]
        ):
            _state(manager)["images"].clear()
            raise InspectionError("scope_changed")
        if operation == "window" and reply.get("source_id") != kwargs["window_id"]:
            raise InspectionError("window_unavailable")
        return reply
    finally:
        if not pending["future"].done():
            pending["future"].cancel()
        if getattr(manager, "_screen_inspection_pending", None) is pending:
            manager._screen_inspection_pending = None


def decode_image(data_url):
    if (
        not isinstance(data_url, str)
        or len(data_url) > MAX_DATA_URL_BYTES
        or not data_url.startswith(
            ("data:image/png;base64,", "data:image/jpeg;base64,")
        )
    ):
        raise InspectionError("invalid_image")
    try:
        raw = base64.b64decode(data_url.split(",", 1)[1], validate=True)
        with Image.open(io.BytesIO(raw)) as image:
            if image.width * image.height > MAX_IMAGE_PIXELS or min(image.size) < 8:
                raise InspectionError("invalid_image")
            # Native window thumbnails can be transparent. White preserves light/dark
            # clothing instead of turning every transparent pixel black during RGB conversion.
            if image.mode in ("RGBA", "LA") or "transparency" in image.info:
                rgba = image.convert("RGBA")
                background = Image.new("RGBA", image.size, "white")
                background.alpha_composite(rgba)
                return background.convert("RGB")
            return image.convert("RGB")
    except (
        UnidentifiedImageError,
        OSError,
        binascii.Error,
        Image.DecompressionBombError,
    ) as exc:
        raise InspectionError("invalid_image") from exc


def crop_box(region, size):
    if (
        not isinstance(region, (tuple, list))
        or len(region) != 4
        or any(
            isinstance(v, bool)
            or not isinstance(v, (int, float))
            or not math.isfinite(v)
            or not 0 <= v <= 1
            for v in region
        )
    ):
        raise InspectionError("invalid_region")
    left, top, right, bottom = region
    if left >= right or top >= bottom:
        raise InspectionError("invalid_region")
    w, h = size
    box = (int(left * w), int(top * h), math.ceil(right * w), math.ceil(bottom * h))
    if box[2] - box[0] < 8 or box[3] - box[1] < 8:
        raise InspectionError("invalid_region")
    return box


def _avatar_region(position):
    try:
        x, y, w, h = (
            float(position[k]) for k in ("centerX", "centerY", "width", "height")
        )
        if (
            not all(math.isfinite(v) for v in (x, y, w, h))
            or not 0 <= x <= 1
            or not 0 <= y <= 1
            or not 0 < w <= 1
            or not 0 < h <= 1
        ):
            return None
        return [
            max(0, x - w * 0.58),
            max(0, y - h * 0.55),
            min(1, x + w * 0.58),
            min(1, y + h * 0.55),
        ]
    except (TypeError, KeyError, ValueError):
        return None


def _encode_preview(image, kind):
    preview = image.copy()
    preview.thumbnail((1280, 720), Image.Resampling.LANCZOS)
    if kind == "region" and preview.height < 640:
        factor = min(640 / preview.height, 1280 / preview.width, 4)
        if factor > 1:
            preview = preview.resize(
                (round(preview.width * factor), round(preview.height * factor)),
                Image.Resampling.LANCZOS,
            )
    buffer = io.BytesIO()
    preview.save(buffer, "JPEG", quality=90)
    return base64.b64encode(buffer.getvalue()).decode(), list(preview.size)


async def _publish(
    manager, image, reply, *, kind, parent_id=None, region=None, captured_at=None
):
    owner = _owner(manager)
    encoded, preview_size = await asyncio.to_thread(_encode_preview, image, kind)
    await _check_permission(manager, owner)
    images = _state(manager)["images"]
    pixels = image.width * image.height
    while images and (
        len(images) >= MAX_IMAGES
        or pixels
        + sum(entry["image"].width * entry["image"].height for entry in images.values())
        > MAX_CACHE_PIXELS
    ):
        del images[next(iter(images))]
    snapshot_id = uuid4().hex[:16]
    avatar = (
        _avatar_region(reply.get("avatar_position")) if kind == "overview" else None
    )
    windows = [
        {"window_id": w["id"], "title": str(w.get("name", ""))[:200]}
        for w in (reply.get("windows") or [])[:80]
        if isinstance(w, dict)
        and isinstance(w.get("id"), str)
        and w["id"].startswith("window:")
    ]
    captured_at = captured_at or time.time()
    images[snapshot_id] = {
        "image": image,
        "expires": time.monotonic() + SNAPSHOT_TTL,
        "scope": reply["scope"],
        "windows": windows,
        "avatar": avatar,
        "source_id": reply.get("source_id"),
        "title": str(reply.get("title", ""))[:200],
        "captured_at": captured_at,
    }
    # Release pixels even if the user makes no subsequent inspection call.
    asyncio.get_running_loop().call_later(SNAPSHOT_TTL, images.pop, snapshot_id, None)
    output = {
        "status": "captured",
        "snapshot_id": snapshot_id,
        "kind": kind,
        "original_size": list(image.size),
        "preview_size": preview_size,
        "source_id": reply.get("source_id"),
        "title": str(reply.get("title", ""))[:200],
        "capture_scope": reply["scope"],
        "captured_at": captured_at,
        "windows": windows,
        "avatar_region": avatar,
        "parent_snapshot_id": parent_id,
        "region": region,
        "message": text("evidence", getattr(manager, "user_language", None)),
    }
    language = getattr(manager, "user_language", None)
    output["next_steps"] = [text("region", language)]
    if windows:
        output["next_steps"].insert(0, text("window", language))
    logger.info(
        "[screen-inspection] %s snapshot=%s source=%s original=%s preview=%s parent=%s",
        kind,
        snapshot_id,
        reply.get("source_id"),
        image.size,
        preview_size,
        parent_id,
    )
    return {
        "output": output,
        "images": [
            {
                "data_b64": encoded,
                "mime": "image/jpeg",
                "vision_prompt": (
                    f"snapshot_id={snapshot_id}; kind={kind}; source_id={output['source_id']}; "
                    f"parent_snapshot_id={parent_id}."
                ),
            }
        ],
    }


async def inspect(manager, operation="overview", arguments=None):
    from main_logic.omni_offline_client import OmniOfflineClient

    if not isinstance(manager.session, OmniOfflineClient):
        return failure(manager, "unsupported_session")
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        return failure(manager, "invalid_request")
    try:
        state = _state(manager)
        owner = _owner(manager)
        await _check_permission(manager, owner)
        if operation == "overview":
            reply = await _request(manager, "overview")
            image = await asyncio.to_thread(decode_image, reply.get("data_url"))
            if owner != _owner(manager):
                raise InspectionError("superseded")
            return await _publish(manager, image, reply, kind="overview")
        snapshot_id = arguments.get("snapshot_id")
        saved = (
            state["images"].get(snapshot_id) if isinstance(snapshot_id, str) else None
        )
        if not saved:
            return failure(manager, "stale_snapshot")
        if operation == "window":
            window_id = arguments.get("window_id")
            if window_id not in [w["window_id"] for w in saved["windows"]]:
                return failure(manager, "window_not_allowed")
            reply = await _request(
                manager, "window", scope=saved["scope"], window_id=window_id
            )
            image = await asyncio.to_thread(decode_image, reply.get("data_url"))
            if owner != _owner(manager):
                raise InspectionError("superseded")
            return await _publish(
                manager,
                image,
                reply,
                kind="window",
                parent_id=snapshot_id,
            )
        if operation != "region":
            return failure(manager, "invalid_request")
        target = arguments.get("target")
        if target not in ("avatar", "region"):
            return failure(manager, "invalid_request")
        region = saved["avatar"] if target == "avatar" else arguments.get("region")
        if target == "avatar" and not region:
            return failure(manager, "no_avatar_hint")
        box = crop_box(region, saved["image"].size)
        # Cached pixels are still subject to the live privacy and shared-source scope.
        await _request(manager, "validate", scope=saved["scope"])
        if _state(manager)["images"].get(snapshot_id) is not saved:
            return failure(manager, "stale_snapshot")
        cropped = await asyncio.to_thread(saved["image"].crop, box)
        if owner != _owner(manager):
            raise InspectionError("superseded")
        reply = {
            "scope": saved["scope"],
            "source_id": saved["source_id"],
            "title": saved["title"],
        }
        return await _publish(
            manager,
            cropped,
            reply,
            kind="region",
            parent_id=snapshot_id,
            region=region,
            captured_at=saved["captured_at"],
        )
    except asyncio.TimeoutError:
        return failure(manager, "timeout")
    except InspectionError as exc:
        return failure(manager, str(exc))
    except Exception as exc:
        logger.warning("[screen-inspection] failed: %s", type(exc).__name__)
        return failure(manager, "capture_failed")
