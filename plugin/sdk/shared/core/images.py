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
