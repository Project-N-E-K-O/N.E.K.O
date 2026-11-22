"""Offline chat: images go to the chat model; the retired vision config is a fallback.

A session sends a picture to its chat model. Only when that model rejects the
image does the session move to the vision config the user filled in before
the vision slot was retired, send the turn again, and say once that the
separate vision model is deprecated.
"""
import json
from unittest.mock import AsyncMock, MagicMock

import httpx
import openai
import pytest

from tests.unit.test_offline_provider_frame_publish import _png_b64
from tests.unit.test_offline_turn_cancellation_e2e import _client, _emitted, _text
from utils import legacy_vision as lv

pytestmark = pytest.mark.unit

CHAT = {"model": "chat-x", "base_url": "http://chat.invalid/v1", "api_key": "chat-key", "is_custom": True}
VISION = {"model": "llava", "base_url": "http://127.0.0.1:11434/v1", "api_key": "", "is_custom": True}


@pytest.fixture(autouse=True)
def _reset_engagement(monkeypatch):
    lv._reset_for_tests()
    monkeypatch.setattr("utils.token_tracker.TokenTracker.get_instance", MagicMock())
    yield
    lv._reset_for_tests()


def _refusal() -> openai.BadRequestError:
    request = httpx.Request("POST", "http://chat.invalid/v1/chat/completions")
    body = {"code": 20041, "message": "The model is not a VLM (Vision Language Model). Please use text-only prompts."}
    return openai.BadRequestError(
        f"Error code: 400 - {body}", response=httpx.Response(400, request=request), body=body,
    )


def _session(vision=VISION):
    client = _client()
    client.model = CHAT["model"]
    client.base_url = CHAT["base_url"]
    client.api_key = CHAT["api_key"]
    client.provider_type = None
    choice = lv.choose_image_model(CHAT, vision)
    legacy = choice.legacy or {}
    client.vision_model = legacy.get("model", "")
    client.vision_base_url = legacy.get("base_url", "")
    client.vision_api_key = legacy.get("api_key") or None
    client.vision_provider_type = None
    client._image_choice = choice
    client._legacy_vision_switched = False
    client.switch_model = AsyncMock(return_value=True)
    client.on_status_message = AsyncMock()
    return client


def _assert_switched_to_llava(client):
    client.switch_model.assert_awaited_once()
    args, kwargs = client.switch_model.await_args
    assert args == ("llava",)
    assert kwargs["use_vision_config"] is True
    assert callable(kwargs["abandon_if"])


def _statuses(client):
    return [json.loads(call.args[0]) for call in client.on_status_message.await_args_list]


async def test_image_goes_to_the_chat_model_first():
    client = _session()
    client._pending_images = [_png_b64(4, 4)]
    client.script = [[_text("看到了。"), _text("", "stop")]]

    await client.stream_text("看看这个")

    client.switch_model.assert_not_awaited()
    assert len(client.requests) == 1
    assert _emitted(client) == ["看到了。"]


async def test_refusal_moves_to_legacy_vision_and_resends():
    notices = []
    lv.register_notice_sink(notices.append)
    client = _session()
    client._pending_images = [_png_b64(4, 4)]
    client.script = [_refusal(), [_text("看到了。"), _text("", "stop")]]

    await client.stream_text("看看这个")

    _assert_switched_to_llava(client)
    assert len(client.requests) == 2
    assert client.requests[0] == client.requests[1]
    assert _emitted(client) == ["看到了。"]
    assert client._legacy_vision_switched
    assert [json.loads(n)["code"] for n in notices] == [lv.LEGACY_VISION_FALLBACK_CODE]
    assert not any(s.get("code") == "CHAT_MODEL_IMAGE_UNSUPPORTED" for s in _statuses(client))


async def test_refusal_on_the_vision_config_is_not_retried_again():
    client = _session()
    client._pending_images = [_png_b64(4, 4)]
    client.script = [_refusal(), _refusal()]

    await client.stream_text("看看这个")

    client.switch_model.assert_awaited_once()
    assert len(client.requests) == 2
    assert {"code": "CHAT_MODEL_IMAGE_UNSUPPORTED"} in _statuses(client)


async def test_refusal_without_legacy_vision_tells_the_user():
    client = _session(vision=None)
    client._pending_images = [_png_b64(4, 4)]
    client.script = [_refusal()]

    await client.stream_text("看看这个")

    client.switch_model.assert_not_awaited()
    assert len(client.requests) == 1
    assert {"code": "CHAT_MODEL_IMAGE_UNSUPPORTED"} in _statuses(client)


async def test_engaged_fallback_switches_before_sending_images():
    lv.engage_legacy_vision(lv.choose_image_model(CHAT, VISION), "elsewhere")
    client = _session()
    client._pending_images = [_png_b64(4, 4)]
    client.script = [[_text("看到了。"), _text("", "stop")]]

    await client.stream_text("看看这个")

    client.switch_model.assert_awaited_once_with("llava", use_vision_config=True)
    assert len(client.requests) == 1


async def test_tool_images_need_no_switch_when_the_chat_model_reads_them():
    client = _session(vision=None)
    assert await client.prepare_for_tool_images() is True
    client.switch_model.assert_not_awaited()

    with_legacy = _session()
    assert await with_legacy.prepare_for_tool_images() is True
    with_legacy.switch_model.assert_not_awaited()


async def test_proactive_refusal_engages_fallback_for_the_next_turn():
    client = _session()
    client.script = [_refusal()]

    await client.prompt_ephemeral("callback with media", images=[_png_b64(4, 4)])

    assert len(client.requests) == 1
    _assert_switched_to_llava(client)
    assert lv.choose_image_model(CHAT, VISION).using_legacy


async def test_switch_dropped_when_the_session_closes_meanwhile():
    """close() during the switch: the vision client is never installed."""
    client = _session()
    client._pending_images = [_png_b64(4, 4)]
    client.script = [_refusal(), [_text("不该出现"), _text("", "stop")]]

    async def switch(model, use_vision_config=False, abandon_if=None):
        client.llm = None  # close() ran while the vision client was being built
        return not abandon_if()

    client.switch_model = AsyncMock(side_effect=switch)
    await client.stream_text("看看这个")

    assert len(client.requests) == 1
    assert not client._legacy_vision_switched
    assert _emitted(client) == []


async def test_fallback_still_happens_after_ordinary_retries(monkeypatch):
    """Two connection failures, then an image refusal: the move to the vision
    config does not need a retry of its own."""
    import asyncio
    from tests.unit.test_offline_provider_frame_publish import _connection_error

    real_sleep = asyncio.sleep

    async def no_backoff(delay, *args, **kwargs):
        return await real_sleep(0 if delay >= 1 else delay, *args, **kwargs)

    monkeypatch.setattr(asyncio, "sleep", no_backoff)
    client = _session()
    client._pending_images = [_png_b64(4, 4)]
    client.script = [_connection_error(), _connection_error(), _refusal(),
                     [_text("看到了。"), _text("", "stop")]]

    await client.stream_text("看看这个")

    assert len(client.requests) == 4
    _assert_switched_to_llava(client)
    assert _emitted(client) == ["看到了。"]
