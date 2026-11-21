"""Validate GIF frame bounds before decoding any pixels."""
import io

import pytest
from PIL import Image

from main_logic.emote_stage_images import decode_stage_gif


def _gif():
    output = io.BytesIO()
    Image.new('RGB', (16, 16), 'red').save(output, 'GIF', save_all=True,
        append_images=[Image.new('RGB', (16, 16), 'blue')], duration=[50, 100], optimize=False)
    return output.getvalue()


def test_decodes_bounded_frames_and_preserves_their_timing():
    data, cell, columns, delays = decode_stage_gif(_gif(), 64)
    assert (cell, columns, delays) == (16, 2, [50, 100])
    with Image.open(io.BytesIO(data)) as sheet:
        assert sheet.size == (32, 16)
        assert sheet.getpixel((8, 8)) == (255, 0, 0, 255)
        assert sheet.getpixel((24, 8)) == (0, 0, 255, 255)


def test_rejects_a_later_frame_expanding_the_canvas_before_opening_pillow(monkeypatch):
    raw = bytearray(_gif())
    descriptor = raw.rfind(b'\x2c\x00\x00\x00\x00\x10\x00\x10\x00')
    assert descriptor > 13
    raw[descriptor + 5:descriptor + 7] = (8192).to_bytes(2, 'little')
    monkeypatch.setattr(Image, 'open', lambda *args, **kwargs: pytest.fail('Pillow must not decode this GIF'))
    with pytest.raises(ValueError, match='invalid_stage_gif'):
        decode_stage_gif(bytes(raw), 64)
