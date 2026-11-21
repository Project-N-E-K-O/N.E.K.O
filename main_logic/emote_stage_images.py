"""Decode a GIF imported into the state editor into composited, timed frames."""

import io
import struct
from math import ceil, isqrt, sqrt

from PIL import Image

MAX_STAGE_GIF_BYTES = 32 * 1024 * 1024
MAX_STAGE_GIF_FRAMES = 240
MAX_STAGE_GIF_EDGE = 2048
MAX_FRAME_SHEET_PIXELS = 16 * 1024 * 1024


def _gif_frame_count(raw: bytes) -> int:
    """Bound every image descriptor before Pillow can allocate an expanded canvas."""
    if len(raw) < 13 or raw[:6] not in (b'GIF87a', b'GIF89a'):
        raise ValueError('invalid_stage_gif')
    logical_width = int.from_bytes(raw[6:8], 'little')
    logical_height = int.from_bytes(raw[8:10], 'little')
    if min(logical_width, logical_height) == 0 or max(logical_width, logical_height) > MAX_STAGE_GIF_EDGE:
        raise ValueError('invalid_stage_gif')
    offset = 13 + (3 * (1 << ((raw[10] & 7) + 1)) if raw[10] & 0x80 else 0)
    count = 0

    def skip_blocks(at):
        while at < len(raw):
            length = raw[at]
            at += 1
            if not length:
                return at
            at += length
            if at > len(raw):
                break
        raise ValueError('invalid_stage_gif')

    while offset < len(raw):
        marker = raw[offset]
        offset += 1
        if marker == 0:
            continue  # Tolerate zero padding between complete GIF blocks.
        if marker == 0x3b:
            if count:
                return count
            break
        if marker == 0x21:
            offset = skip_blocks(offset + 1)
            continue
        if marker != 0x2c or offset + 9 > len(raw):
            break
        left, top, width, height = [int.from_bytes(raw[offset + i:offset + i + 2], 'little')
                                    for i in (0, 2, 4, 6)]
        if not width or not height or max(left + width, top + height) > MAX_STAGE_GIF_EDGE:
            break
        count += 1
        if count > MAX_STAGE_GIF_FRAMES:
            break
        packed = raw[offset + 8]
        offset += 9 + (3 * (1 << ((packed & 7) + 1)) if packed & 0x80 else 0)
        offset = skip_blocks(offset + 1)  # LZW minimum code size, then image data.
    else:
        if count:
            return count  # Some encoders omit the trailer after complete frames.
    raise ValueError('invalid_stage_gif')


def decode_stage_gif(raw: bytes, edge: int) -> tuple[bytes, int, int, list[int]]:
    if not raw or len(raw) > MAX_STAGE_GIF_BYTES:
        raise ValueError("invalid_stage_gif")
    try:
        frame_count = _gif_frame_count(raw)
        with Image.open(io.BytesIO(raw), formats=["GIF"]) as source:
            if max(source.size) > MAX_STAGE_GIF_EDGE:
                raise ValueError("invalid_stage_gif")
            if not 1 <= frame_count <= MAX_STAGE_GIF_FRAMES:
                raise ValueError("invalid_stage_gif")
            columns = ceil(sqrt(frame_count))
            rows = ceil(frame_count / columns)
            cell = min(edge, max(source.size), isqrt(MAX_FRAME_SHEET_PIXELS // (columns * rows)))
            scale = cell / max(source.size)
            width, height = (max(1, round(side * scale)) for side in source.size)
            offset_x, offset_y = (cell - width) // 2, (cell - height) // 2
            sheet = Image.new("RGBA", (columns * cell, rows * cell))
            delays: list[int] = []
            for index in range(frame_count):
                source.seek(index)
                frame = source.convert("RGBA")
                if frame.size != (width, height):
                    frame = frame.resize((width, height), Image.Resampling.LANCZOS)
                sheet.paste(frame, (index % columns * cell + offset_x, index // columns * cell + offset_y))
                delays.append(max(10, int(source.info.get("duration") or 100)))
            output = io.BytesIO()
            sheet.save(output, "PNG")
            return output.getvalue(), cell, columns, delays
    except (OSError, EOFError, Image.DecompressionBombError, SyntaxError, ValueError, IndexError, struct.error) as exc:
        raise ValueError("invalid_stage_gif") from exc
