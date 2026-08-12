from __future__ import annotations

import asyncio
import base64
from unittest.mock import AsyncMock, MagicMock

import pytest


pytestmark = pytest.mark.unit


_IMAGE_URL = "http://127.0.0.1:48916/media/matrix-image"


class _StreamingResponse:
    def __init__(
        self,
        chunks: list[bytes],
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._chunks = chunks
        self.headers = headers or {"content-type": "image/jpeg"}
        self.chunks_read = 0

    async def __aenter__(self) -> "_StreamingResponse":
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    async def aiter_bytes(self):
        for chunk in self._chunks:
            self.chunks_read += 1
            yield chunk


def _manager() -> MagicMock:
    manager = MagicMock()
    manager.session = MagicMock()
    manager.session.stream_image = AsyncMock()
    manager.enqueue_agent_callback = MagicMock()
    manager.submit_proactive_callback = MagicMock()
    manager.passthrough_to_chat_bubble = AsyncMock(return_value=True)
    manager.render_chat_blocks = AsyncMock(return_value=True)
    manager.handle_proactive_complete = AsyncMock()
    manager.websocket = None
    manager._pending_agent_callback_task = None
    return manager


@pytest.mark.asyncio
async def test_plugin_image_url_is_fetched_asynchronously_for_model_context(monkeypatch) -> None:
    from app import main_server

    manager = _manager()
    image_bytes = b"jpeg-from-plugin-image-store"
    response = _StreamingResponse([image_bytes])
    client = MagicMock()
    client.stream.return_value = response

    monkeypatch.setattr(
        "app.main_server.character_runtime._get_session_manager",
        lambda _name: manager,
    )
    monkeypatch.setattr(
        "app.main_server.character_runtime.get_internal_http_client",
        lambda: client,
        raising=False,
    )
    await main_server._handle_agent_event({
        "event_type": "proactive_message",
        "lanlan_name": "Test",
        "text": "remember this image",
        "channel": "plugin:demo",
        "delivery_mode": "passive",
        "ai_behavior": "read",
        "visibility": [],
        "media_parts": [{
            "type": "image",
            "url": "http://127.0.0.1:49889/media/example",
            "mime": "image/jpeg",
        }],
    })

    client.stream.assert_called_once_with(
        "GET",
        "http://127.0.0.1:49889/media/example",
        timeout=2.0,
        follow_redirects=False,
    )
    manager.session.stream_image.assert_awaited_once_with(
        base64.b64encode(image_bytes).decode("ascii")
    )


@pytest.mark.asyncio
async def test_plugin_image_fetch_stops_at_the_model_input_byte_limit(monkeypatch) -> None:
    from app.main_server.character_runtime import _fetch_plugin_image_base64

    one_megabyte = b"x" * (1024 * 1024)
    response = _StreamingResponse([one_megabyte] * 12)
    client = MagicMock()
    client.stream.return_value = response
    monkeypatch.setattr(
        "app.main_server.character_runtime.get_internal_http_client",
        lambda: client,
    )

    with pytest.raises(ValueError, match="8 MiB model input limit"):
        await _fetch_plugin_image_base64(_IMAGE_URL)

    assert response.chunks_read == 9


@pytest.mark.asyncio
async def test_chat_blind_plugin_image_is_forwarded_as_structured_blocks(monkeypatch) -> None:
    from app import main_server

    manager = _manager()
    monkeypatch.setattr(
        "app.main_server.character_runtime._get_session_manager",
        lambda _name: manager,
    )
    monkeypatch.setattr(
        "app.main_server.character_runtime._is_websocket_connected",
        lambda _ws: False,
    )

    await main_server._handle_agent_event({
        "event_type": "proactive_message",
        "lanlan_name": "Test",
        "text": "look at this",
        "channel": "plugin:demo",
        "task_id": "image-chat-1",
        "delivery_mode": "silent",
        "ai_behavior": "blind",
        "visibility": ["chat"],
        "media_parts": [{
            "type": "image",
            "url": "http://127.0.0.1:48916/media/example",
            "mime": "image/jpeg",
        }],
    })

    manager.passthrough_to_chat_bubble.assert_awaited_once()
    call = manager.passthrough_to_chat_bubble.await_args
    assert call.kwargs["blocks"] == [
        {"type": "text", "text": "look at this"},
        {
            "type": "image",
            "url": "http://127.0.0.1:48916/media/example",
        },
    ]


def test_external_image_urls_are_not_exposed_to_chat_or_model() -> None:
    from app.main_server.character_runtime import _plugin_image_chat_blocks

    assert _plugin_image_chat_blocks([
        {"type": "image", "url": "https://example.com/media/not-local"},
        {"type": "image", "url": "http://localhost:48916/media/not-an-ip"},
        {"type": "image", "url": "http://127.0.0.1:48916/media/valid"},
    ]) == [{
        "type": "image",
        "url": "http://127.0.0.1:48916/media/valid",
    }]


@pytest.mark.asyncio
async def test_image_only_chat_blind_message_still_opens_a_structured_bubble(monkeypatch) -> None:
    from app import main_server

    manager = _manager()
    monkeypatch.setattr(
        "app.main_server.character_runtime._get_session_manager",
        lambda _name: manager,
    )
    monkeypatch.setattr(
        "app.main_server.character_runtime._is_websocket_connected",
        lambda _ws: False,
    )

    await main_server._handle_agent_event({
        "event_type": "proactive_message",
        "lanlan_name": "Test",
        "text": "",
        "channel": "plugin:demo",
        "task_id": "image-only-chat",
        "delivery_mode": "silent",
        "ai_behavior": "blind",
        "visibility": ["chat"],
        "media_parts": [{
            "type": "image",
            "url": "http://127.0.0.1:48916/media/image-only",
            "mime": "image/jpeg",
        }],
    })

    manager.passthrough_to_chat_bubble.assert_awaited_once_with(
        "",
        request_id="image-only-chat",
        source="plugin",
        blocks=[{
            "type": "image",
            "url": "http://127.0.0.1:48916/media/image-only",
        }],
    )


@pytest.mark.asyncio
async def test_chat_visible_respond_image_is_rendered_and_sent_to_model(monkeypatch) -> None:
    from app import main_server

    manager = _manager()
    encoded = base64.b64encode(b"model-image").decode("ascii")
    monkeypatch.setattr(
        "app.main_server.character_runtime._get_session_manager",
        lambda _name: manager,
    )
    monkeypatch.setattr(
        "app.main_server.character_runtime._fetch_plugin_image_base64",
        AsyncMock(return_value=encoded),
    )

    await main_server._handle_agent_event({
        "event_type": "proactive_message",
        "lanlan_name": "Test",
        "text": "describe it",
        "channel": "plugin:demo",
        "task_id": "image-both",
        "delivery_mode": "proactive",
        "ai_behavior": "respond",
        "visibility": ["chat"],
        "media_parts": [{
            "type": "image",
            "url": "http://127.0.0.1:48916/media/both",
            "mime": "image/jpeg",
        }],
    })

    manager.render_chat_blocks.assert_awaited_once_with(
        [
            {"type": "text", "text": "describe it"},
            {
                "type": "image",
                "url": "http://127.0.0.1:48916/media/both",
            },
        ],
        request_id="image-both",
        source="plugin",
    )
    callback = manager.submit_proactive_callback.call_args.args[0]
    assert callback["media_images"] == [encoded]


@pytest.mark.asyncio
async def test_image_only_respond_still_triggers_an_immediate_model_turn(monkeypatch) -> None:
    from app import main_server

    manager = _manager()
    encoded = base64.b64encode(b"image-only-model-input").decode("ascii")
    monkeypatch.setattr(
        "app.main_server.character_runtime._get_session_manager",
        lambda _name: manager,
    )
    monkeypatch.setattr(
        "app.main_server.character_runtime._fetch_plugin_image_base64",
        AsyncMock(return_value=encoded),
    )

    await main_server._handle_agent_event({
        "event_type": "proactive_message",
        "lanlan_name": "Test",
        "text": "",
        "channel": "plugin:demo",
        "task_id": "image-only-respond",
        "delivery_mode": "proactive",
        "ai_behavior": "respond",
        "visibility": [],
        "media_parts": [{
            "type": "image",
            "url": "http://127.0.0.1:48916/media/image-only-respond",
            "mime": "image/jpeg",
        }],
    })

    manager.session.stream_image.assert_not_awaited()
    callback = manager.submit_proactive_callback.call_args.args[0]
    assert callback["media_images"] == [encoded]


@pytest.mark.asyncio
async def test_multiple_plugin_image_urls_are_fetched_concurrently(monkeypatch) -> None:
    from app import main_server

    manager = _manager()
    active_fetches = 0
    max_active_fetches = 0

    async def _fetch(url: str) -> str:
        nonlocal active_fetches, max_active_fetches
        active_fetches += 1
        max_active_fetches = max(max_active_fetches, active_fetches)
        await asyncio.sleep(0)
        active_fetches -= 1
        return base64.b64encode(url.encode()).decode("ascii")

    monkeypatch.setattr(
        "app.main_server.character_runtime._get_session_manager",
        lambda _name: manager,
    )
    monkeypatch.setattr(
        "app.main_server.character_runtime._fetch_plugin_image_base64",
        _fetch,
    )

    await main_server._handle_agent_event({
        "event_type": "proactive_message",
        "lanlan_name": "Test",
        "text": "remember both",
        "channel": "plugin:demo",
        "delivery_mode": "passive",
        "ai_behavior": "read",
        "visibility": [],
        "media_parts": [
            {"type": "image", "url": "http://127.0.0.1:48916/media/one"},
            {"type": "image", "url": "http://127.0.0.1:48916/media/two"},
        ],
    })

    assert max_active_fetches == 2
    assert manager.session.stream_image.await_count == 2


@pytest.mark.parametrize("with_text", [False, True], ids=["image-only", "text-and-image"])
@pytest.mark.parametrize(
    ("visibility", "ai_behavior", "shows_original", "model_reads", "responds_now"),
    [
        ([], "blind", False, False, False),
        (["chat"], "blind", True, False, False),
        ([], "read", False, True, False),
        (["chat"], "read", True, True, False),
        ([], "respond", False, True, True),
        (["chat"], "respond", True, True, True),
    ],
)
@pytest.mark.asyncio
async def test_image_delivery_obeys_visibility_and_ai_behavior(
    monkeypatch,
    with_text: bool,
    visibility: list[str],
    ai_behavior: str,
    shows_original: bool,
    model_reads: bool,
    responds_now: bool,
) -> None:
    """The two public delivery axes stay orthogonal for both payload shapes."""
    from app import main_server

    manager = _manager()
    encoded = base64.b64encode(b"matrix-image").decode("ascii")
    monkeypatch.setattr(
        "app.main_server.character_runtime._get_session_manager",
        lambda _name: manager,
    )
    monkeypatch.setattr(
        "app.main_server.character_runtime._fetch_plugin_image_base64",
        AsyncMock(return_value=encoded),
    )

    await main_server._handle_agent_event({
        "event_type": "proactive_message",
        "lanlan_name": "Test",
        "text": "describe this" if with_text else "",
        "channel": "plugin:matrix",
        "task_id": f"matrix-{ai_behavior}-{with_text}",
        "delivery_mode": {
            "blind": "silent",
            "read": "passive",
            "respond": "proactive",
        }[ai_behavior],
        "ai_behavior": ai_behavior,
        "visibility": visibility,
        "media_parts": [{"type": "image", "url": _IMAGE_URL, "mime": "image/jpeg"}],
    })

    assert manager.render_chat_blocks.await_count == int(
        shows_original and ai_behavior != "blind"
    )
    assert manager.passthrough_to_chat_bubble.await_count == int(
        shows_original and ai_behavior == "blind"
    )
    if shows_original and ai_behavior != "blind":
        expected_blocks = []
        if with_text:
            expected_blocks.append({"type": "text", "text": "describe this"})
        expected_blocks.append({
            "type": "image",
            "url": _IMAGE_URL,
        })
        assert manager.render_chat_blocks.await_args.args[0] == expected_blocks

    if ai_behavior == "read" and model_reads:
        manager.session.stream_image.assert_awaited_once_with(encoded)
    else:
        manager.session.stream_image.assert_not_awaited()

    assert manager.submit_proactive_callback.call_count == int(responds_now)
    if responds_now:
        callback = manager.submit_proactive_callback.call_args.args[0]
        assert callback["media_images"] == [encoded]
    if ai_behavior == "blind":
        manager.enqueue_agent_callback.assert_not_called()
    elif ai_behavior == "read":
        assert manager.enqueue_agent_callback.call_count == int(with_text)


@pytest.mark.parametrize(
    ("ai_behavior", "delivery_mode", "visibility", "expects_submit", "expects_enqueue", "expects_chat"),
    [
        ("blind", "silent", [], False, False, False),
        ("blind", "silent", ["chat"], False, False, True),
        ("read", "passive", [], False, True, False),
        ("read", "passive", ["chat"], False, True, False),
        ("respond", "proactive", [], True, False, False),
        ("respond", "proactive", ["chat"], True, False, False),
    ],
)
@pytest.mark.asyncio
async def test_text_only_delivery_keeps_its_pre_image_behavior(
    monkeypatch,
    ai_behavior: str,
    delivery_mode: str,
    visibility: list[str],
    expects_submit: bool,
    expects_enqueue: bool,
    expects_chat: bool,
) -> None:
    """Adding image support must not redirect or duplicate existing text events."""
    from app import main_server

    manager = _manager()
    fetch_image = AsyncMock()
    monkeypatch.setattr(
        "app.main_server.character_runtime._get_session_manager",
        lambda _name: manager,
    )
    monkeypatch.setattr(
        "app.main_server.character_runtime._fetch_plugin_image_base64",
        fetch_image,
    )

    await main_server._handle_agent_event({
        "event_type": "proactive_message",
        "lanlan_name": "Test",
        "text": "legacy text stays text",
        "channel": "plugin:matrix",
        "delivery_mode": delivery_mode,
        "ai_behavior": ai_behavior,
        "visibility": visibility,
        "media_parts": [],
    })

    fetch_image.assert_not_awaited()
    manager.session.stream_image.assert_not_awaited()
    manager.render_chat_blocks.assert_not_awaited()
    assert manager.submit_proactive_callback.call_count == int(expects_submit)
    assert manager.enqueue_agent_callback.call_count == int(expects_enqueue)
    assert manager.passthrough_to_chat_bubble.await_count == int(expects_chat)


@pytest.mark.asyncio
async def test_failed_plugin_image_does_not_block_text_delivery(monkeypatch) -> None:
    """A broken temporary URL drops only the image, never the text callback."""
    from app import main_server

    manager = _manager()
    monkeypatch.setattr(
        "app.main_server.character_runtime._get_session_manager",
        lambda _name: manager,
    )
    monkeypatch.setattr(
        "app.main_server.character_runtime._fetch_plugin_image_base64",
        AsyncMock(side_effect=TimeoutError("local media read timed out")),
    )

    await main_server._handle_agent_event({
        "event_type": "proactive_message",
        "lanlan_name": "Test",
        "text": "the text must survive",
        "channel": "plugin:failure",
        "delivery_mode": "proactive",
        "ai_behavior": "respond",
        "visibility": [],
        "media_parts": [{"type": "image", "url": _IMAGE_URL, "mime": "image/jpeg"}],
    })

    callback = manager.submit_proactive_callback.call_args.args[0]
    assert callback["summary"] == "the text must survive"
    assert callback["media_images"] == []
