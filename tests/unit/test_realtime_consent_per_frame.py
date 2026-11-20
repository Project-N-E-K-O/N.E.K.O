"""Realtime direct submit: a screen-consent revocation inside the provider
drops only the live frames and keeps the screenshots the user attached
(issue #3807). Losing frame ownership still drops every frame."""

import asyncio
import base64
import io
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from main_logic.omni_realtime_client import _responses as responses_module
from main_logic.omni_realtime_client import OmniRealtimeClient, TurnDetectionMode

_REAL_FIT_IMAGES = responses_module.fit_images_to_turn_budget


def _jpeg_b64(color: tuple[int, int, int]) -> str:
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), color).save(buffer, format="JPEG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


LIVE = _jpeg_b64((255, 0, 0))
USER = _jpeg_b64((0, 0, 255))


def _make_client(api_type: str, model: str) -> OmniRealtimeClient:
    return OmniRealtimeClient(
        base_url="wss://test.example.invalid/realtime",
        api_key="test-key",
        model=model,
        api_type=api_type,
        turn_detection_mode=TurnDetectionMode.SERVER_VAD,
    )


async def _submit_gemini(images, request_ids, *, fit, owned=True, consent=True):
    """Submit through Gemini; ``fit`` stands in for the compression ladder and
    flips the predicates inside it, the window issue #3807 is about."""
    client = _make_client("gemini", "gemini-2.5-flash-native-audio")
    session = AsyncMock()
    client._gemini_session = session
    client.ws = session
    client.handle_interruption = AsyncMock()
    client._analyze_image_with_vision_model = AsyncMock()
    state = {"owned": True, "consent": True}

    async def _fit(staged, _budget):
        state["owned"], state["consent"] = owned, consent
        kept = fit(list(staged))
        return kept, {
            "original_count": len(staged),
            "final_count": len(kept),
            "sampled": False,
            "compressed": True,
            "dropped": len(staged) - len(kept),
            "user_visible": len(kept) != len(staged),
        }

    responses_module.fit_images_to_turn_budget = _fit
    try:
        await client.prepare_external_voice_turn(turn_id="turn-1")
        await client.submit_multimodal_turn(
            "看一下这张图",
            images,
            turn_id="turn-1",
            visual_still_owned=lambda: state["owned"],
            screen_consent_holds=lambda: state["consent"],
            image_request_ids=request_ids,
        )
    finally:
        responses_module.fit_images_to_turn_budget = _REAL_FIT_IMAGES
    session.send_client_content.assert_awaited_once()
    parts = session.send_client_content.await_args.kwargs["turns"][0].parts
    sent = [bytes(p.inline_data.data) for p in parts if p.inline_data is not None]
    assert any(p.text == "看一下这张图" for p in parts)
    await client.close()
    return sent


@pytest.mark.asyncio
async def test_gemini_revocation_during_compression_keeps_the_user_screenshot():
    sent = await _submit_gemini(
        (LIVE, USER), (None, "req-1"), fit=lambda imgs: imgs, consent=False,
    )
    assert sent == [base64.b64decode(USER)]


@pytest.mark.asyncio
async def test_gemini_revocation_with_only_live_frames_sends_text_only():
    sent = await _submit_gemini(
        (LIVE, LIVE), (None, None), fit=lambda imgs: imgs, consent=False,
    )
    assert sent == []


@pytest.mark.asyncio
async def test_gemini_without_request_ids_treats_every_frame_as_live():
    sent = await _submit_gemini(
        (LIVE, USER), (), fit=lambda imgs: imgs, consent=False,
    )
    assert sent == []


@pytest.mark.asyncio
async def test_gemini_lost_ownership_still_drops_the_user_screenshot():
    sent = await _submit_gemini(
        (LIVE, USER), (None, "req-1"), fit=lambda imgs: imgs, owned=False,
    )
    assert sent == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "images,request_ids,expected",
    [
        # The ladder dropped the oldest (live) frame: the survivor is the
        # user's screenshot and stays.
        ((LIVE, USER), (None, "req-1"), [USER]),
        # The ladder dropped the oldest (the user's) frame: the survivor is
        # live and goes.
        ((USER, LIVE), ("req-1", None), []),
    ],
)
async def test_gemini_flags_follow_the_frames_the_ladder_kept(
    images, request_ids, expected,
):
    sent = await _submit_gemini(
        images, request_ids, fit=lambda imgs: imgs[1:], consent=False,
    )
    assert sent == [base64.b64decode(image) for image in expected]


@pytest.mark.asyncio
async def test_gemini_consent_held_sends_every_frame():
    sent = await _submit_gemini(
        (LIVE, USER), (None, "req-1"), fit=lambda imgs: imgs,
    )
    assert sent == [base64.b64decode(LIVE), base64.b64decode(USER)]


async def _submit_websocket(images, request_ids, *, owned=True, consent=True):
    """Submit through the WebSocket branch; the predicates flip while the
    ticket waits in the arbiter, right before its pre_commit hook."""
    client = _make_client("openai", "gpt-4o-realtime-preview")
    client.ws = AsyncMock()
    client.handle_interruption = AsyncMock()
    state = {"owned": True, "consent": True}
    captured: list = []

    async def _fake_enqueue(**kwargs):
        event = kwargs["events_before_response"][0]
        state["owned"], state["consent"] = owned, consent
        kwargs["pre_commit"](event)
        captured.append(event)
        loop = asyncio.get_running_loop()
        sent, done = loop.create_future(), loop.create_future()
        sent.set_result(None)
        done.set_result(None)
        return SimpleNamespace(sent=sent, done=done)

    client._ensure_response_arbiter = lambda: SimpleNamespace(
        enqueue=_fake_enqueue,
        resume_dispatch=lambda: None,
        pause_dispatch=lambda owner=None: None,
        cancel_ticket=AsyncMock(),
        begin_turn_preparation=lambda owner=None: None,
        end_turn_preparation=lambda _token=None: None,
        has_live_response=True,
        allow_ticket_while_paused=lambda ticket: None,
        cancel_current=AsyncMock(),
    )
    await client.prepare_external_voice_turn(turn_id="turn-1")
    await client.submit_multimodal_turn(
        "看一下这张图",
        images,
        turn_id="turn-1",
        visual_still_owned=lambda: state["owned"],
        screen_consent_holds=lambda: state["consent"],
        image_request_ids=request_ids,
    )
    content = captured[0]["item"]["content"]
    assert any(part.get("text") == "看一下这张图" for part in content)
    return [
        part["image_url"].split(",", 1)[1]
        for part in content
        if part.get("type") == "input_image"
    ]


@pytest.mark.asyncio
async def test_websocket_revocation_in_the_arbiter_keeps_the_user_screenshot():
    sent = await _submit_websocket((LIVE, USER), (None, "req-1"), consent=False)
    assert sent == [USER]


@pytest.mark.asyncio
async def test_websocket_without_request_ids_treats_every_frame_as_live():
    sent = await _submit_websocket((LIVE, USER), (), consent=False)
    assert sent == []


@pytest.mark.asyncio
async def test_websocket_lost_ownership_still_drops_the_user_screenshot():
    sent = await _submit_websocket((LIVE, USER), (None, "req-1"), owned=False)
    assert sent == []


@pytest.mark.asyncio
async def test_websocket_consent_held_sends_every_frame():
    sent = await _submit_websocket((LIVE, USER), (None, "req-1"))
    assert sent == [LIVE, USER]
