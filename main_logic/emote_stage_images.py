"""Decode a GIF imported into the state editor into composited, timed frames."""

import io
from math import ceil, isqrt, sqrt

from PIL import Image

MAX_STAGE_GIF_BYTES = 32 * 1024 * 1024
MAX_STAGE_GIF_FRAMES = 240
MAX_STAGE_GIF_EDGE = 2048
MAX_FRAME_SHEET_PIXELS = 16 * 1024 * 1024


def decode_stage_gif(raw: bytes, edge: int) -> tuple[bytes, int, int, list[int]]:
    if not raw or len(raw) > MAX_STAGE_GIF_BYTES:
        raise ValueError("invalid_stage_gif")
    try:
        with Image.open(io.BytesIO(raw), formats=["GIF"]) as source:
            if max(source.size) > MAX_STAGE_GIF_EDGE:
                raise ValueError("invalid_stage_gif")
            frame_count = source.n_frames
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
    except (OSError, EOFError, Image.DecompressionBombError, SyntaxError, ValueError) as exc:
        raise ValueError("invalid_stage_gif") from exc
