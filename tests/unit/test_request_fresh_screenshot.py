"""Regression tests for LLMSessionManager.request_fresh_screenshot.

Regression protection: some frontend capture paths (Electron main-process captureSourceAsDataUrl) return
native-resolution images whose base64 reaches about 1.4 MB. Returning these unchanged through WebSocket makes Phase 2
send them to the vision LLM, where proxy nginx rejects them with 413 Request Entity Too Large.
Both request_fresh_screenshot return paths must normalize to 720p/JPEG-80.
"""
import asyncio
import base64
from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image

from main_logic.core import LLMSessionManager


def _png_b64(width: int, height: int) -> str:
    """Generate a PNG at the specified dimensions and return plain base64 without a data: prefix."""
    img = Image.new("RGB", (width, height))
    # Add a gradient resembling a real screenshot; solid-color JPEGs compress almost to zero.
    px = img.load()
    for y in range(0, height, 4):
        for x in range(0, width, 4):
            px[x, y] = ((x * 255) // width, (y * 255) // height, 128)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


@pytest.mark.unit
def test_ws_screenshot_is_downscaled_to_720p():
    """Normalize native-resolution WebSocket screenshots to 720p JPEG."""
    big_b64 = _png_b64(2560, 1440)  # Simulate a native 1440p image captured directly by Electron's main process.

    async def _run() -> str:
        fake = SimpleNamespace(_screenshot_future=None, lanlan_name="桃奈")

        async def _send_json(payload):
            # send_json is awaited after create_future and before wait_for;
            # resolve the future directly here to simulate the frontend returning a screenshot.
            assert payload == {"type": "request_screenshot"}
            fake._screenshot_future.set_result(big_b64)

        fake.websocket = SimpleNamespace(send_json=_send_json)
        return (await LLMSessionManager.request_fresh_screenshot(fake, timeout=3.0)).b64

    out_b64 = asyncio.run(_run())

    assert out_b64, "应返回非空 base64"
    out_img = Image.open(BytesIO(base64.b64decode(out_b64)))
    # Must be normalized to 720p JPEG rather than retaining the original 1440p PNG.
    assert out_img.format == "JPEG"
    assert out_img.height == 720
    # Bytes should be well below nginx's default 1 MB body limit, the trigger for 413.
    assert len(base64.b64decode(out_b64)) < 1024 * 1024


@pytest.mark.unit
def test_ws_screenshot_already_small_stays_under_limit():
    """Small images already within 720p must return normally without enlargement."""
    small_b64 = _png_b64(1280, 720)

    async def _run() -> str:
        fake = SimpleNamespace(_screenshot_future=None, lanlan_name="桃奈")

        async def _send_json(payload):
            fake._screenshot_future.set_result(small_b64)

        fake.websocket = SimpleNamespace(send_json=_send_json)
        return (await LLMSessionManager.request_fresh_screenshot(fake, timeout=3.0)).b64

    out_b64 = asyncio.run(_run())

    assert out_b64
    out_img = Image.open(BytesIO(base64.b64decode(out_b64)))
    assert out_img.format == "JPEG"
    assert out_img.height <= 720
    assert len(base64.b64decode(out_b64)) < 1024 * 1024
