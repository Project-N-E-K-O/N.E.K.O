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

"""The ``read_image`` tool; envelope and errors follow dsh ``tool-fs/src/read-image.ts``.

The tool is offered only when the session's model accepts images. A large
image is downscaled before it is handed over, and the result states the factor
that maps coordinates on the smaller picture back to the original file.
"""

from __future__ import annotations

import asyncio
import base64
import io
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nekoclaw.runtime.loop.messages import ImageContent, TextContent
from nekoclaw.runtime.loop.tools import AgentTool, AgentToolResult
from nekoclaw.runtime.tools import textio
from nekoclaw.runtime.tools.base import ToolCallInfo, ToolContext, ToolError, define_tool

MAX_SOURCE_BYTES = 50 * 1024 * 1024
MAX_SOURCE_PIXELS = 64_000_000
# What reaches the model: at most this many pixels and bytes.
NORMALIZED_MAX_PIXELS = 2048 * 2048
NORMALIZED_MAX_BYTES = 4 * 1024 * 1024
_SUPPORTED = "PNG/JPEG/WebP/GIF"

_EXTENSIONS = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}
_PIL_FORMATS = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp", "GIF": "image/gif"}


@dataclass(frozen=True, slots=True)
class LoadedImage:
    media_type: str
    width: int
    height: int
    data: bytes
    original_size: tuple[int, int] | None


def sniff_media_type(data: bytes) -> str | None:
    """Identify a supported image from its file signature."""
    if data.startswith(bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])):
        return "image/png"
    if data.startswith(bytes([0xFF, 0xD8, 0xFF])):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return None


def load_image(path: Path) -> LoadedImage:
    """Blocking part of ``read_image``: read, check and normalize one image."""
    from PIL import Image, UnidentifiedImageError

    display = str(path)
    extension = path.suffix.lower()
    declared = _EXTENSIONS.get(extension)
    if extension and declared is None:
        raise ToolError(
            f'cannot read "{display}": the {extension} extension does not declare a supported '
            f"image format; read_image accepts {_SUPPORTED} files, including extension-less "
            "files in those formats"
        )
    raw = textio.read_bytes(path, verb="read", display=display, max_bytes=MAX_SOURCE_BYTES)
    signature = sniff_media_type(raw)
    if declared is None:
        if signature is None:
            raise ToolError(
                f'cannot read "{display}": the file content is not a supported image format; '
                f"read_image accepts {_SUPPORTED}"
            )
        declared = signature
    elif signature is not None and signature != declared:
        raise ToolError(
            f'cannot read "{display}": the {extension} extension declares {declared}, but the '
            "bytes use a different image format; rename the file to match its actual format if "
            f"it is {_SUPPORTED}, or convert it to one of those formats"
        )
    try:
        image = Image.open(io.BytesIO(raw))
        image.load()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ToolError(
            f'cannot read "{display}": the bytes do not decode as a supported {_SUPPORTED} '
            "image; the file may be truncated or corrupt"
        ) from exc
    if _PIL_FORMATS.get(image.format or "") != declared:
        raise ToolError(
            f'cannot read "{display}": the bytes do not decode as a supported {_SUPPORTED} '
            "image; the file may be truncated or corrupt"
        )
    width, height = image.size
    if width * height > MAX_SOURCE_PIXELS:
        raise ToolError(
            f'cannot read "{display}": the image exceeds the {MAX_SOURCE_PIXELS}-pixel '
            "decoded-size limit; downscale the image and read the smaller copy"
        )

    if width * height <= NORMALIZED_MAX_PIXELS and len(raw) <= NORMALIZED_MAX_BYTES:
        return LoadedImage(declared, width, height, raw, None)

    # Animated images are reduced to their first frame once they need resizing.
    frame = image.convert("RGBA" if "A" in image.getbands() or image.mode == "P" else "RGB")
    scale = min(1.0, (NORMALIZED_MAX_PIXELS / (width * height)) ** 0.5)
    for _attempt in range(6):
        # Rounded down so the product never exceeds the pixel budget.
        new_size = (max(1, int(width * scale)), max(1, int(height * scale)))
        resized = (
            frame
            if new_size == frame.size
            else frame.resize(new_size, Image.Resampling.LANCZOS)
        )
        buffer = io.BytesIO()
        if declared == "image/jpeg":
            resized.convert("RGB").save(buffer, format="JPEG", quality=85)
            media_type = "image/jpeg"
        else:
            resized.save(buffer, format="PNG", optimize=True)
            media_type = "image/png"
        data = buffer.getvalue()
        if len(data) <= NORMALIZED_MAX_BYTES:
            original = (width, height) if new_size != (width, height) else None
            return LoadedImage(media_type, new_size[0], new_size[1], data, original)
        scale *= 0.7
    raise ToolError(
        f'cannot read "{display}": the image cannot be stored within the byte limits; downscale '
        "the image and read the smaller copy"
    )


def format_image_output(display: str, image: LoadedImage) -> str:
    scaled = ""
    if image.original_size is not None:
        x = f"{image.original_size[0] / image.width:.2f}"
        y = f"{image.original_size[1] / image.height:.2f}"
        advice = (
            f"multiply coordinates by {x}"
            if x == y
            else f"multiply x coordinates by {x} and y coordinates by {y}"
        )
        scaled = (
            f" (downscaled from {image.original_size[0]}x{image.original_size[1]} px; {advice} "
            "to locate features in the original file)"
        )
    return (
        f"<path>{display}</path>\n<type>image</type>\n<content>\n"
        f"{image.media_type} image, {image.width}x{image.height} px, {len(image.data)} bytes{scaled}\n"
        "</content>"
    )


def build_image_tool(ctx: ToolContext, schemas: dict[str, dict[str, Any]]) -> list[AgentTool]:
    if not ctx.supports_images:
        return []

    async def read_image(args: dict[str, Any], call: ToolCallInfo) -> AgentToolResult:
        del call
        raw = str(args["file_path"])
        if not raw.strip():
            raise ToolError("file_path must be a non-empty string")
        path = ctx.resolve(raw)
        image = await asyncio.to_thread(load_image, path)
        return AgentToolResult(
            content=[
                TextContent(text=format_image_output(str(path), image)),
                ImageContent(
                    data=base64.b64encode(image.data).decode("ascii"), mime_type=image.media_type
                ),
            ]
        )

    return [define_tool(schemas["read_image"], read_image, parallel=True)]
