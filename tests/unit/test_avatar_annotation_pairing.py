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
"""Avatar annotation: how a Phase 2 screenshot gets paired with its coordinates.

The rule these tests pin down is that "the frontend sent no avatar_position" is a
verdict ("do not annotate this image"), not a missing value, and must never be
back-filled with the position that arrived with the original request. The one
exception is the backend pyautogui fallback, where the frontend never saw the
image at all and therefore cannot have objected.
"""

import asyncio
import base64
from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image

from main_logic.core import LLMSessionManager
from main_logic.core._shared import FreshScreenshot
from main_logic.proactive_chat.service import _resolve_phase2_avatar_position

POS_A = {"centerX": 0.25, "centerY": 0.5, "width": 0.1, "height": 0.2}
POS_B = {"centerX": 0.75, "centerY": 0.5, "width": 0.1, "height": 0.2}


def _png_b64(width: int, height: int) -> str:
    buf = BytesIO()
    Image.new("RGB", (width, height), (10, 20, 30)).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def _fake_manager() -> SimpleNamespace:
    return SimpleNamespace(
        _screenshot_future=None,
        _pending_screenshot_avatar_position=None,
        _avatar_position=None,
        lanlan_name="桃奈",
        websocket=None,
    )


@pytest.mark.unit
def test_ws_reply_carries_its_own_avatar_position():
    """The position handed to resolve_screenshot_request rides back with the image."""
    shot = _png_b64(640, 360)

    async def _run() -> FreshScreenshot:
        fake = _fake_manager()

        async def _send_json(payload):
            assert payload == {"type": "request_screenshot"}
            LLMSessionManager.resolve_screenshot_request(fake, shot, POS_A)

        fake.websocket = SimpleNamespace(send_json=_send_json)
        return await LLMSessionManager.request_fresh_screenshot(fake, timeout=3.0)

    fresh = asyncio.run(_run())
    assert fresh.source == "websocket"
    assert fresh.avatar_position == POS_A


@pytest.mark.unit
def test_ws_reply_without_position_stays_none():
    """No position from the frontend must surface as None, not as some fallback."""
    shot = _png_b64(640, 360)

    async def _run() -> FreshScreenshot:
        fake = _fake_manager()
        # A stale coordinate from an earlier frame sits in the broadcast field;
        # it must not leak into this image's verdict.
        fake._avatar_position = POS_B

        async def _send_json(_payload):
            LLMSessionManager.resolve_screenshot_request(fake, shot, None)

        fake.websocket = SimpleNamespace(send_json=_send_json)
        return await LLMSessionManager.request_fresh_screenshot(fake, timeout=3.0)

    fresh = asyncio.run(_run())
    assert fresh.source == "websocket"
    assert fresh.avatar_position is None


@pytest.mark.unit
def test_interleaved_stream_frame_cannot_swap_the_paired_position():
    """A stream_data frame landing mid-flight must not rewrite this image's position.

    This is why the pairing uses its own one-shot slot instead of the broadcast
    ``_avatar_position`` field, which every stream_data frame overwrites.
    """
    shot = _png_b64(640, 360)

    async def _run() -> FreshScreenshot:
        fake = _fake_manager()

        async def _send_json(_payload):
            LLMSessionManager.resolve_screenshot_request(fake, shot, POS_A)
            # Exactly what a concurrent screen-share frame does to the manager
            # between resolve and the requester waking up.
            fake._avatar_position = POS_B

        fake.websocket = SimpleNamespace(send_json=_send_json)
        return await LLMSessionManager.request_fresh_screenshot(fake, timeout=3.0)

    fresh = asyncio.run(_run())
    assert fresh.avatar_position == POS_A


@pytest.mark.unit
def test_stale_slot_is_cleared_before_the_next_request():
    """A position left by an earlier request must not be reused by the next one."""
    shot = _png_b64(640, 360)

    async def _run() -> FreshScreenshot:
        fake = _fake_manager()
        # Residue from a previous round.
        fake._pending_screenshot_avatar_position = POS_B

        async def _send_json(_payload):
            LLMSessionManager.resolve_screenshot_request(fake, shot, None)

        fake.websocket = SimpleNamespace(send_json=_send_json)
        return await LLMSessionManager.request_fresh_screenshot(fake, timeout=3.0)

    fresh = asyncio.run(_run())
    assert fresh.avatar_position is None


@pytest.mark.unit
def test_backend_fallback_is_tagged_as_such():
    """No websocket at all -> pyautogui path, tagged so the caller may fall back."""

    async def _run() -> FreshScreenshot:
        fake = _fake_manager()
        fake.websocket = None
        return await LLMSessionManager.request_fresh_screenshot(fake, timeout=0.1)

    fresh = asyncio.run(_run())
    # Without a websocket the loopback guard rejects the fallback, so nothing is
    # captured; what matters is that it never claims to be a websocket answer.
    assert fresh.source != "websocket"
    assert fresh.avatar_position is None


@pytest.mark.unit
def test_websocket_verdict_wins_over_the_request_position():
    """The Phase 2 decision must not resurrect the request position on a WS answer."""
    fresh = FreshScreenshot(b64="x", source="websocket", avatar_position=None)
    assert _resolve_phase2_avatar_position(fresh, POS_B) is None

    fresh_with_pos = FreshScreenshot(b64="x", source="websocket", avatar_position=POS_A)
    assert _resolve_phase2_avatar_position(fresh_with_pos, POS_B) == POS_A


@pytest.mark.unit
def test_backend_fallback_keeps_the_request_position_as_its_only_source():
    """Tightening both branches to fresh.avatar_position would silently kill this."""
    fresh = FreshScreenshot(b64="x", source="backend_fallback", avatar_position=None)
    assert _resolve_phase2_avatar_position(fresh, POS_B) == POS_B
