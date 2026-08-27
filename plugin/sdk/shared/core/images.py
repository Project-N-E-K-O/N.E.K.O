"""Plugin-facing temporary image upload interface."""
from __future__ import annotations

import asyncio
from io import BytesIO
from typing import Any

from PIL import Image, ImageOps


MAX_IMAGE_EDGE = 2048
MAX_UPLOADED_IMAGE_BYTES = 8 * 1024 * 1024
MAX_SOURCE_IMAGE_BYTES = 32 * 1024 * 1024
MAX_SOURCE_IMAGE_PIXELS = 16 * 1024 * 1024

# Concurrent decodes, per plugin process.
#
# Every other limit here is per IMAGE: 32 MiB in, 16 MP, 8 MiB out. None of
# them bounds how many decodes run at once, and the transport's queue depth
# does not either, because the decode happens BEFORE anything is submitted. A
# plugin that fires many uploads together therefore schedules that many Pillow
# decodes into the default executor, each holding a full uncompressed bitmap --
# a 16 MP source is ~64 MB as RGBA, so a burst measured in dozens is measured
# in gigabytes (Codex P2).
#
# The realistic case is not a hostile plugin, which has easier ways to spend
# memory, but an ordinary one uploading in a loop without thinking about
# concurrency. Two lets a slow transport overlap with the next decode while
# keeping the ceiling to roughly one image's working set either side of it.
MAX_CONCURRENT_NORMALIZATIONS = 2
_normalize_slots: "asyncio.Semaphore | None" = None
_normalize_slots_loop: "asyncio.AbstractEventLoop | None" = None


def _acquire_normalize_slot() -> "asyncio.Semaphore":
    """Return this loop's decode semaphore, creating it on first use.

    Bound lazily and re-created when the running loop changes: a plugin process
    may restart its loop, and a semaphore bound to a dead loop would deadlock
    every later upload rather than merely bounding it.
    """
    global _normalize_slots, _normalize_slots_loop
    loop = asyncio.get_running_loop()
    if _normalize_slots is None or _normalize_slots_loop is not loop:
        _normalize_slots = asyncio.Semaphore(MAX_CONCURRENT_NORMALIZATIONS)
        _normalize_slots_loop = loop
    return _normalize_slots


def normalize_image_to_jpeg(data: bytes) -> bytes:
    """Decode one supported image payload and return bounded JPEG bytes."""
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("image data must be bytes or bytearray")
    if not data:
        raise ValueError("image data must not be empty")
    if len(data) > MAX_SOURCE_IMAGE_BYTES:
        raise ValueError("source image exceeds the 32 MiB decode limit")

    with Image.open(BytesIO(bytes(data))) as source:
        source.seek(0)
        width, height = source.size
        if width * height > MAX_SOURCE_IMAGE_PIXELS:
            raise ValueError("source image exceeds the 16 megapixel decode limit")
        image = ImageOps.exif_transpose(source)
        image.thumbnail((MAX_IMAGE_EDGE, MAX_IMAGE_EDGE), Image.Resampling.LANCZOS)
        if image.mode in ("RGBA", "LA") or "transparency" in image.info:
            rgba = image.convert("RGBA")
            normalized = Image.new("RGB", rgba.size, "white")
            normalized.paste(rgba, mask=rgba.getchannel("A"))
        else:
            normalized = image.convert("RGB")

        output = BytesIO()
        normalized.save(output, format="JPEG", quality=88)
        payload = output.getvalue()

    if len(payload) > MAX_UPLOADED_IMAGE_BYTES:
        raise ValueError("normalized image exceeds the 8 MiB upload limit")
    return payload


class PluginImages:
    """Prepare image bytes and return a canonical ``push_message`` part."""

    def __init__(self, host_ctx: Any) -> None:
        self._host_ctx = host_ctx

    async def upload(
        self,
        data: bytes | bytearray,
        *,
        mime: str | None = None,
        timeout: float = 3.0,
    ) -> dict[str, object]:
        """Upload one temporary image without delivering it to chat or the model.

        Lifecycle handlers cannot perform request/response media IPC because
        the plugin command loop is not servicing upload responses while those
        handlers run. Reject there before decoding or transport submission.
        """
        self._host_ctx._ensure_image_upload_available()
        handler_ctx = getattr(self._host_ctx, "handler_ctx", None)
        if isinstance(handler_ctx, str) and handler_ctx.startswith("lifecycle."):
            raise RuntimeError(
                "ctx.images.upload() is not available from lifecycle handlers; "
                "use a plugin entry, timer, message, or custom event handler"
            )
        _ = mime  # Input format is detected by Pillow; output is always JPEG.
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("image data must be bytes or bytearray")
        if not data:
            raise ValueError("image data must not be empty")
        if len(data) > MAX_SOURCE_IMAGE_BYTES:
            raise ValueError("source image exceeds the 32 MiB decode limit")
        # Bounded: the size checks above cap ONE image, not how many decode
        # at once. Held only across the decode, not the upload, so a slow
        # transport cannot stall the queue behind it.
        async with _acquire_normalize_slot():
            jpeg = await asyncio.to_thread(normalize_image_to_jpeg, bytes(data))
        result = await self._host_ctx._upload_image(
            jpeg,
            mime="image/jpeg",
            timeout=timeout,
        )
        if not isinstance(result, dict):
            raise RuntimeError("image upload returned an invalid result")
        url = result.get("url")
        if (
            result.get("type") != "image"
            or not isinstance(url, str)
            or not url.strip()
        ):
            raise RuntimeError("image upload did not return a valid image part")
        return dict(result)


__all__ = ["PluginImages"]
