"""Plugin-facing temporary image upload interface."""
from __future__ import annotations

import asyncio
import threading
from io import BytesIO
from typing import Any

from PIL import Image, ImageOps


MAX_IMAGE_EDGE = 2048
MAX_UPLOADED_IMAGE_BYTES = 8 * 1024 * 1024
MAX_SOURCE_IMAGE_BYTES = 32 * 1024 * 1024
MAX_SOURCE_IMAGE_PIXELS = 16 * 1024 * 1024

# MAX_CONCURRENT_NORMALIZATIONS (declared below, after the timeout constant):
# concurrent decodes, PROCESS-wide.
#
# Every other limit here is per IMAGE: 32 MiB in, 16 MP, 8 MiB out. None bounds
# how many decode at once, and the transport queue does not either, because the
# decode happens before anything is submitted. A 16 MP source is ~64 MB as
# RGBA, so a burst is measured in gigabytes (Codex).
#
# A threading gate rather than an asyncio one, for two reasons a per-loop
# semaphore got wrong:
#
#   * The plugin host calls asyncio.run() per handler (host.py startup, timers,
#     custom events, the command loop), so a loop-bound semaphore hands each
#     handler its own pair of slots and bounds nothing process-wide (Codex).
#   * asyncio.to_thread cannot cancel a running thread. With the gate held by
#     the awaiting coroutine, a cancelled caller released it while its thread
#     still held the bitmap; held by the THREAD, cancellation cannot free it
#     early (CodeRabbit).
#
# Known cost: waiters occupy default-executor threads while blocked. A burst of
# eight parks six of them, which is survivable against a pool of ~16-36 and
# strictly better than letting all eight decode at once.

# Upper bound on the caller-selected upload timeout.
#
# The child transport holds its image lock for the whole upload, and plugin
# shutdown waits on that lock, so an unbounded timeout is an unbounded
# shutdown delay -- `upload(timeout=3600)` could wedge a STOP for an hour
# (Codex P2). The transport now bounds its own shutdown wait as well; this is
# the other half, so a plugin cannot park its own handler that long either.
MAX_UPLOAD_TIMEOUT_SECONDS = 30.0

# Rationale in the block at the top of this module -- it is up there with the
# other per-image limits it contrasts itself against, not adjacent to its own
# constant, so read it there before changing this number.
MAX_CONCURRENT_NORMALIZATIONS = 2
_normalize_gate = threading.Semaphore(MAX_CONCURRENT_NORMALIZATIONS)


def normalize_image_to_jpeg(
    data: bytes, *, slot_timeout: float | None = None
) -> bytes:
    """Decode one supported image payload and return bounded JPEG bytes.

    ``slot_timeout`` bounds only the wait for a decode slot, never the decode
    itself. ``None`` waits indefinitely, which is what the host-side caller in
    ``character_runtime`` wants.

    ANIMATION IS SILENTLY REDUCED TO FRAME 0. ``source.seek(0)`` below selects
    the first frame and the single ``save`` writes only that one, so a
    multi-frame GIF / WEBP / APNG comes back as a one-frame JPEG -- no
    exception, no log line, and nothing in the returned bytes distinguishes it
    from a still that was always a still. That follows from the output format
    (JPEG has nowhere to put a second frame) rather than being a policy choice
    here, but it means a plugin author who uploads a GIF gets no signal that
    the motion was dropped; a caller that wants to tell them has to inspect
    ``n_frames`` before handing the bytes over.
    """
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("image data must be bytes or bytearray")
    if not data:
        raise ValueError("image data must not be empty")
    if len(data) > MAX_SOURCE_IMAGE_BYTES:
        raise ValueError("source image exceeds the 32 MiB decode limit")

    # Held across the decode only; the size checks above are cheap and must not
    # queue behind other images.
    #
    # Only the QUEUEING is bounded. A caller that exhausts its budget waiting for
    # a slot has started nothing, so giving up strands no thread and leaves no
    # slot half-held. Bounding the DECODE was requested by both reviewers and is
    # declined: asyncio.to_thread cannot cancel a running thread, so a deadline
    # around it returns the caller while the worker keeps both the bitmap and the
    # slot -- removing the only backpressure here while leaving the work in place.
    if not _normalize_gate.acquire(timeout=slot_timeout):
        raise TimeoutError("timed out waiting for an image decode slot")
    try:
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

    finally:
        _normalize_gate.release()

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
        timeout = min(max(0.0, float(timeout)), MAX_UPLOAD_TIMEOUT_SECONDS)
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("image data must be bytes or bytearray")
        if not data:
            raise ValueError("image data must not be empty")
        if len(data) > MAX_SOURCE_IMAGE_BYTES:
            raise ValueError("source image exceeds the 32 MiB decode limit")
        # Bounded: the size checks above cap ONE image, not how many decode
        # at once. Held only across the decode, not the upload, so a slow
        # transport cannot stall the queue behind it.
        # One deadline for the whole operation, started BEFORE the decode. The
        # decode can queue behind the process-wide gate, and the transport legs
        # used to restart the clock, so `timeout=3` could take well past six
        # seconds and overrun the caller's own deadline (Codex).
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        # The decode can QUEUE behind the process-wide gate, and that wait used
        # to sit outside every budget, so a saturated gate blew the advertised
        # total before the transport was even reached (Codex, CodeRabbit).
        jpeg = await asyncio.to_thread(
            normalize_image_to_jpeg,
            bytes(data),
            slot_timeout=max(0.0, deadline - loop.time()),
        )
        result = await self._host_ctx._upload_image(
            jpeg,
            mime="image/jpeg",
            deadline=deadline,
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
