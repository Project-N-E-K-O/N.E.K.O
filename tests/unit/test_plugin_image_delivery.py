from __future__ import annotations

import asyncio
import base64
import warnings
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image

from plugin.sdk.shared.core.images import MAX_SOURCE_IMAGE_PIXELS


pytestmark = pytest.mark.unit


_IMAGE_URL = "http://127.0.0.1:48916/media/matrix-image"


def _inline_png_base64() -> str:
    source = BytesIO()
    Image.new("RGB", (2, 2), "blue").save(source, format="PNG")
    return base64.b64encode(source.getvalue()).decode("ascii")


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


def test_proactive_bridge_carries_one_canonical_ordered_parts_list() -> None:
    from plugin.server.messaging.proactive_bridge import ProactiveBridge

    sent: list[dict[str, object]] = []

    class _PushSocket:
        def send_json(self, payload, _flags) -> None:
            sent.append(payload)

    parts = [
        {"type": "image", "url": "http://127.0.0.1:48916/media/first"},
        {"type": "text", "text": "caption"},
        {"type": "image", "url": "http://127.0.0.1:48916/media/last"},
    ]
    ProactiveBridge()._dispatch(
        {
            "plugin_id": "ordered",
            "schema": "push_message.v2",
            "visibility": ["chat"],
            "ai_behavior": "blind",
            "parts": parts,
        },
        _PushSocket(),
    )

    proactive = next(item for item in sent if item["event_type"] == "proactive_message")
    assert proactive["parts"] == parts
    assert "media_parts" not in proactive


def test_proactive_bridge_cleans_aggregate_text_without_mutating_parts() -> None:
    from plugin.server.messaging.proactive_bridge import ProactiveBridge

    sent: list[dict[str, object]] = []

    class _PushSocket:
        def send_json(self, payload, _flags) -> None:
            sent.append(payload)

    ProactiveBridge()._dispatch(
        {
            "plugin_id": "ordered",
            "schema": "push_message.v2",
            "visibility": ["chat"],
            "ai_behavior": "blind",
            "parts": [
                {"type": "image", "url": "http://127.0.0.1:48916/media/first"},
                {"type": "text", "text": '{"message":"clean caption"}'},
                {"type": "image", "url": "http://127.0.0.1:48916/media/last"},
            ],
        },
        _PushSocket(),
    )

    proactive = next(item for item in sent if item["event_type"] == "proactive_message")
    assert proactive["text"] == "clean caption"
    assert proactive["parts"] == [
        {"type": "image", "url": "http://127.0.0.1:48916/media/first"},
        {"type": "text", "text": '{"message":"clean caption"}'},
        {"type": "image", "url": "http://127.0.0.1:48916/media/last"},
    ]


def test_proactive_bridge_bounds_aggregate_text_without_mutating_parts() -> None:
    from plugin.server.messaging.proactive_bridge import ProactiveBridge

    sent: list[dict[str, object]] = []

    class _PushSocket:
        def send_json(self, payload, _flags) -> None:
            sent.append(payload)

    raw_text = "word " * 2_000
    ProactiveBridge()._dispatch(
        {
            "plugin_id": "ordered",
            "schema": "push_message.v2",
            "visibility": ["chat"],
            "ai_behavior": "blind",
            "parts": [
                {"type": "image", "url": "http://127.0.0.1:48916/media/first"},
                {"type": "text", "text": raw_text},
                {"type": "image", "url": "http://127.0.0.1:48916/media/last"},
            ],
        },
        _PushSocket(),
    )

    proactive = next(item for item in sent if item["event_type"] == "proactive_message")
    assert proactive["parts"][1]["text"] == raw_text
    assert proactive["text"].endswith("…")
    assert len(proactive["text"]) < len(raw_text)


def test_proactive_bridge_preserves_blank_text_part_and_later_caption_order() -> None:
    from plugin.server.messaging.proactive_bridge import ProactiveBridge

    sent: list[dict[str, object]] = []

    class _PushSocket:
        def send_json(self, payload, _flags) -> None:
            sent.append(payload)

    ProactiveBridge()._dispatch(
        {
            "plugin_id": "ordered",
            "schema": "push_message.v2",
            "visibility": ["chat"],
            "ai_behavior": "blind",
            "parts": [
                {"type": "image", "url": "http://127.0.0.1:48916/media/first"},
                {"type": "text", "text": "   "},
                {"type": "image", "url": "http://127.0.0.1:48916/media/last"},
                {"type": "text", "text": "caption"},
            ],
        },
        _PushSocket(),
    )

    proactive = next(item for item in sent if item["event_type"] == "proactive_message")
    assert proactive["parts"] == [
        {"type": "image", "url": "http://127.0.0.1:48916/media/first"},
        {"type": "text", "text": "   "},
        {"type": "image", "url": "http://127.0.0.1:48916/media/last"},
        {"type": "text", "text": "caption"},
    ]
    assert proactive["text"] == "caption"


def test_proactive_bridge_parses_aggregate_without_mutating_split_parts() -> None:
    from plugin.server.messaging.proactive_bridge import ProactiveBridge

    sent: list[dict[str, object]] = []

    class _PushSocket:
        def send_json(self, payload, _flags) -> None:
            sent.append(payload)

    parts = [
        {"type": "image", "url": "http://127.0.0.1:48916/media/first"},
        {"type": "text", "text": "{"},
        {"type": "image", "url": "http://127.0.0.1:48916/media/middle"},
        {"type": "text", "text": '"message":"caption"}'},
        {"type": "image", "url": "http://127.0.0.1:48916/media/last"},
    ]
    ProactiveBridge()._dispatch(
        {
            "plugin_id": "ordered",
            "schema": "push_message.v2",
            "visibility": ["chat"],
            "ai_behavior": "blind",
            "parts": parts,
        },
        _PushSocket(),
    )

    proactive = next(item for item in sent if item["event_type"] == "proactive_message")
    assert proactive["parts"] == parts
    assert proactive["text"] == "caption"


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
        base64.b64encode(image_bytes).decode("ascii"),
        bypass_rate_limit=True,
        cache_latest=False,
    )


@pytest.mark.asyncio
async def test_native_realtime_read_uses_current_session_image_path(
    monkeypatch,
) -> None:
    from app import main_server
    from main_logic.omni_realtime_client import OmniRealtimeClient

    manager = _manager()
    session = OmniRealtimeClient.__new__(OmniRealtimeClient)
    session._supports_native_image = True
    session.stream_image = AsyncMock()
    manager.session = session
    encoded = base64.b64encode(b"native-read-image").decode("ascii")
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
        "text": "remember this image",
        "channel": "plugin:demo",
        "delivery_mode": "passive",
        "ai_behavior": "read",
        "visibility": [],
        "media_parts": [{"type": "image", "url": _IMAGE_URL, "mime": "image/jpeg"}],
    })

    session.stream_image.assert_awaited_once_with(
        encoded,
        bypass_rate_limit=True,
        cache_latest=False,
    )
    manager.enqueue_agent_callback.assert_called_once()
    callback = manager.enqueue_agent_callback.call_args.args[0]
    assert callback["delivery_mode"] == "passive"
    assert callback["media_images"] == []


@pytest.mark.asyncio
async def test_offline_read_uses_the_current_session_queue(monkeypatch) -> None:
    from app import main_server
    from main_logic.omni_offline_client import OmniOfflineClient

    manager = _manager()
    offline_session = OmniOfflineClient.__new__(OmniOfflineClient)
    offline_session.stream_image = AsyncMock()
    manager.session = offline_session
    encoded = base64.b64encode(b"offline-read-image").decode("ascii")
    monkeypatch.setattr(
        "app.main_server.character_runtime._get_session_manager",
        lambda _name: manager,
    )
    monkeypatch.setattr(
        "app.main_server.character_runtime._fetch_plugin_image_base64",
        AsyncMock(return_value=encoded),
    )

    for index in range(2):
        await main_server._handle_agent_event({
            "event_type": "proactive_message",
            "lanlan_name": "Test",
            "text": f"remember image {index}",
            "channel": "plugin:demo",
            "delivery_mode": "passive",
            "ai_behavior": "read",
            "visibility": [],
            "media_parts": [{
                "type": "image",
                "url": f"http://127.0.0.1:48916/media/offline-{index}",
                "mime": "image/jpeg",
            }],
        })

    assert offline_session.stream_image.await_count == 2
    assert manager.enqueue_agent_callback.call_count == 2
    assert all(
        call.args[0]["media_images"] == []
        for call in manager.enqueue_agent_callback.call_args_list
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["", "remember this image"], ids=["image-only", "text-and-image"])
async def test_read_image_is_not_queued_when_no_model_session(
    monkeypatch,
    text: str,
) -> None:
    """Read is best-effort input to the current model session only."""
    from app import main_server

    manager = _manager()
    manager.session = None
    encoded = base64.b64encode(b"deferred-read-image").decode("ascii")
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
        "text": text,
        "channel": "plugin:demo",
        "delivery_mode": "passive",
        "ai_behavior": "read",
        "visibility": [],
        "media_parts": [{"type": "image", "url": _IMAGE_URL, "mime": "image/jpeg"}],
    })

    assert manager.enqueue_agent_callback.call_count == int(bool(text))
    if text:
        assert manager.enqueue_agent_callback.call_args.args[0]["media_images"] == []


@pytest.mark.asyncio
async def test_non_native_realtime_read_uses_current_session_image_path(
    monkeypatch,
) -> None:
    from app import main_server

    manager = _manager()
    manager.session._supports_native_image = False
    encoded = base64.b64encode(b"non-native-read-image").decode("ascii")
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
        "text": "remember it",
        "channel": "plugin:demo",
        "delivery_mode": "passive",
        "ai_behavior": "read",
        "visibility": [],
        "media_parts": [{"type": "image", "url": _IMAGE_URL, "mime": "image/jpeg"}],
    })

    manager.session.stream_image.assert_awaited_once_with(
        encoded,
        bypass_rate_limit=True,
        cache_latest=False,
    )
    manager.enqueue_agent_callback.assert_called_once()
    callback = manager.enqueue_agent_callback.call_args.args[0]
    assert callback["media_images"] == []
    manager.submit_proactive_callback.assert_not_called()


@pytest.mark.asyncio
async def test_read_image_stream_failure_is_not_queued(monkeypatch) -> None:
    from app import main_server

    manager = _manager()
    manager.session.stream_image.side_effect = ConnectionError("session closed")
    encoded = base64.b64encode(b"deferred-after-stream-failure").decode("ascii")
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
        "delivery_mode": "passive",
        "ai_behavior": "read",
        "visibility": [],
        "media_parts": [{"type": "image", "url": _IMAGE_URL, "mime": "image/jpeg"}],
    })

    manager.session.stream_image.assert_awaited_once_with(
        encoded,
        bypass_rate_limit=True,
        cache_latest=False,
    )
    manager.enqueue_agent_callback.assert_not_called()


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

    with pytest.raises(ValueError, match="per-image transfer limit"):
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


@pytest.mark.asyncio
@pytest.mark.parametrize("ai_behavior", ["blind", "read", "respond"])
async def test_plugin_chat_blocks_preserve_canonical_part_order(
    monkeypatch,
    ai_behavior: str,
) -> None:
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
    parts = [
        {"type": "image", "url": "http://127.0.0.1:48916/media/first"},
        {"type": "text", "text": "between"},
        {"type": "image", "url": "http://127.0.0.1:48916/media/last"},
    ]

    await main_server._handle_agent_event({
        "event_type": "proactive_message",
        "lanlan_name": "Test",
        "text": "between",
        "channel": "plugin:ordered",
        "task_id": f"ordered-{ai_behavior}",
        "delivery_mode": {
            "blind": "silent",
            "read": "passive",
            "respond": "proactive",
        }[ai_behavior],
        "ai_behavior": ai_behavior,
        "visibility": ["chat"],
        "parts": parts,
    })

    expected = [
        {"type": "image", "url": "http://127.0.0.1:48916/media/first"},
        {"type": "text", "text": "between"},
        {"type": "image", "url": "http://127.0.0.1:48916/media/last"},
    ]
    if ai_behavior == "blind":
        assert manager.passthrough_to_chat_bubble.await_args.kwargs["blocks"] == expected
    else:
        assert manager.render_chat_blocks.await_args.args[0] == expected


def test_external_image_urls_are_not_exposed_to_chat_or_model() -> None:
    from app.main_server.character_runtime import _build_plugin_image_chat_blocks

    assert _build_plugin_image_chat_blocks([
        {"type": "image", "url": "https://example.com/media/not-local"},
        {"type": "image", "url": "http://localhost:48916/media/not-an-ip"},
        {"type": "image", "url": "http://127.0.0.1:48916/media/valid"},
    ]) == [{
        "type": "image",
        "url": "http://127.0.0.1:48916/media/valid",
    }]


def test_inline_image_is_exposed_to_chat_as_a_data_url() -> None:
    from app.main_server.character_runtime import _build_plugin_image_chat_blocks

    encoded = _inline_png_base64()

    assert _build_plugin_image_chat_blocks([{
        "type": "image",
        "binary_base64": encoded,
        "mime": "image/png",
    }]) == [{
        "type": "image",
        "url": f"data:image/png;base64,{encoded}",
    }]


@pytest.mark.asyncio
async def test_inline_image_only_chat_blind_message_is_rendered(monkeypatch) -> None:
    from app import main_server

    manager = _manager()
    encoded = _inline_png_base64()
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
        "channel": "plugin:inline",
        "task_id": "inline-image-chat",
        "delivery_mode": "silent",
        "ai_behavior": "blind",
        "visibility": ["chat"],
        "media_parts": [{
            "type": "image",
            "binary_base64": encoded,
            "mime": "image/png",
        }],
    })

    manager.passthrough_to_chat_bubble.assert_awaited_once_with(
        "",
        request_id="inline-image-chat",
        source="plugin",
        blocks=[{
            "type": "image",
            "url": f"data:image/png;base64,{encoded}",
        }],
    )


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
        manager.session.stream_image.assert_awaited_once_with(
            encoded,
            bypass_rate_limit=True,
            cache_latest=False,
        )
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
async def test_text_only_delivery_obeys_visibility_and_ai_behavior(
    monkeypatch,
    ai_behavior: str,
    delivery_mode: str,
    visibility: list[str],
    expects_submit: bool,
    expects_enqueue: bool,
    expects_chat: bool,
) -> None:
    """The image feature leaves the existing text-only delivery path unchanged."""
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
        "parts": [{"type": "text", "text": "legacy text stays text"}],
    })

    fetch_image.assert_not_awaited()
    manager.session.stream_image.assert_not_awaited()
    manager.render_chat_blocks.assert_not_awaited()
    assert manager.submit_proactive_callback.call_count == int(expects_submit)
    assert manager.enqueue_agent_callback.call_count == int(expects_enqueue)
    assert manager.passthrough_to_chat_bubble.await_count == int(
        expects_chat and ai_behavior == "blind"
    )


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


# ---------------------------------------------------------------------------
# Per-push image budgets
#
# A push carries an unbounded ``parts`` list. Without these caps one plugin can
# pin (count x 8 MiB) of decoded image bytes in the event handler — and, for
# ``respond``, keep it pinned on the queued callback until the pacing manager
# releases it — while blocking the handler on ceil(count / 4) fetch rounds.
# ---------------------------------------------------------------------------


def test_plugin_image_budget_constants_are_pinned() -> None:
    """Guard the literals the budget tests below assert against.

    The behavioral tests monkeypatch the byte budgets down so they don't have
    to allocate tens of MiB. That makes them blind to a constant change, so
    pin the shipped values here.
    """
    from app.main_server import character_runtime

    assert character_runtime._PLUGIN_IMAGE_MAX_COUNT == 8
    assert character_runtime._PLUGIN_IMAGE_TOTAL_MAX_BYTES == 8 * 1024 * 1024
    assert character_runtime._PLUGIN_CHAT_IMAGE_MAX_COUNT == 8
    assert character_runtime._PLUGIN_CHAT_INLINE_TOTAL_MAX_BYTES == 8 * 1024 * 1024


def test_model_image_budget_matches_the_documented_plugin_contract() -> None:
    """PLUGIN_DEVELOPMENT_GUIDE.md advertises 8 images / 8 MiB to authors.

    The guide is the contract plugin authors code against, so a silent drift
    between it and the enforced budget is the actual defect this guards.
    """
    from pathlib import Path

    from app.main_server import character_runtime

    guide = Path(__file__).resolve().parents[2] / "plugin" / "PLUGIN_DEVELOPMENT_GUIDE.md"
    text = guide.read_text(encoding="utf-8")
    assert "单条消息最多向模型注入 8 张、合计" in text
    assert "8 MiB 图片" in text
    assert character_runtime._PLUGIN_IMAGE_MAX_COUNT == 8
    assert character_runtime._PLUGIN_IMAGE_TOTAL_MAX_BYTES == 8 * 1024 * 1024


@pytest.mark.asyncio
async def test_model_path_caps_image_count_per_push(monkeypatch) -> None:
    """Images past the count cap never reach the model or the fetcher."""
    from app import main_server
    from app.main_server import character_runtime

    manager = _manager()

    async def _fake_fetch(url: str) -> str:
        return "b64-" + url.rsplit("/", 1)[-1]

    fetch = AsyncMock(side_effect=_fake_fetch)
    monkeypatch.setattr(
        "app.main_server.character_runtime._get_session_manager",
        lambda _name: manager,
    )
    monkeypatch.setattr(
        "app.main_server.character_runtime._fetch_plugin_image_base64",
        fetch,
    )

    over_cap = character_runtime._PLUGIN_IMAGE_MAX_COUNT + 4
    await main_server._handle_agent_event({
        "event_type": "proactive_message",
        "lanlan_name": "Test",
        "text": "many images",
        "channel": "plugin:flood",
        "delivery_mode": "passive",
        "ai_behavior": "read",
        "visibility": [],
        "media_parts": [
            {
                "type": "image",
                "url": "http://127.0.0.1:48916/media/img%d" % i,
                "mime": "image/jpeg",
            }
            for i in range(over_cap)
        ],
    })

    assert manager.session.stream_image.await_count == 8
    # The cap short-circuits BEFORE the fetch, so the dropped tail costs no
    # HTTP round trips (this is what bounds handler latency, not just memory).
    assert fetch.await_count == 8
    streamed = [call.args[0] for call in manager.session.stream_image.await_args_list]
    assert streamed == ["b64-img%d" % i for i in range(8)]


@pytest.mark.asyncio
async def test_model_path_caps_total_image_bytes_per_push(monkeypatch) -> None:
    """Once the per-push byte budget is spent, later images are dropped."""
    from app import main_server
    from app.main_server import character_runtime

    manager = _manager()
    # Pool sized from the MEASURED normalized fixture: room for one, not two.
    # Hardcoding a figure would drift the moment the fixture or codec changes.
    _one = character_runtime._approx_decoded_bytes(
        character_runtime._normalize_inline_image_to_jpeg_base64(
            _expands_under_jpeg_base64()
        )
    )
    monkeypatch.setattr(
        "app.main_server.character_runtime._PLUGIN_IMAGE_TOTAL_MAX_BYTES",
        int(_one * 1.5),
    )
    # A REAL png, not a synthetic base64 blob: inline model images are
    # re-encoded to jpeg, so undecodable bytes would be dropped as garbage
    # rather than by the budget this test is about.
    expanding_b64 = _expands_under_jpeg_base64()
    monkeypatch.setattr(
        "app.main_server.character_runtime._get_session_manager",
        lambda _name: manager,
    )
    # Inline parts draw from the pool inside the real resolver; mocking the
    # fetcher would bypass the very bound under test.

    await main_server._handle_agent_event({
        "event_type": "proactive_message",
        "lanlan_name": "Test",
        "text": "heavy images",
        "channel": "plugin:heavy",
        "delivery_mode": "passive",
        "ai_behavior": "read",
        "visibility": [],
        "media_parts": [
            {"type": "image", "binary_base64": expanding_b64, "mime": "image/jpeg"}
            for _ in range(3)
        ],
    })

    assert manager.session.stream_image.await_count == 1


@pytest.mark.asyncio
async def test_respond_callback_media_images_obey_the_byte_budget(monkeypatch) -> None:
    """The budget also bounds what rides the queued proactive callback."""
    from app import main_server
    from app.main_server import character_runtime

    manager = _manager()
    # Pool sized from the MEASURED normalized fixture: room for one, not two.
    # Hardcoding a figure would drift the moment the fixture or codec changes.
    _one = character_runtime._approx_decoded_bytes(
        character_runtime._normalize_inline_image_to_jpeg_base64(
            _expands_under_jpeg_base64()
        )
    )
    monkeypatch.setattr(
        "app.main_server.character_runtime._PLUGIN_IMAGE_TOTAL_MAX_BYTES",
        int(_one * 1.5),
    )
    # A REAL png, not a synthetic base64 blob: inline model images are
    # re-encoded to jpeg, so undecodable bytes would be dropped as garbage
    # rather than by the budget this test is about.
    expanding_b64 = _expands_under_jpeg_base64()
    monkeypatch.setattr(
        "app.main_server.character_runtime._get_session_manager",
        lambda _name: manager,
    )
    # Inline parts draw from the pool inside the real resolver; mocking the
    # fetcher would bypass the very bound under test.

    await main_server._handle_agent_event({
        "event_type": "proactive_message",
        "lanlan_name": "Test",
        "text": "heavy respond",
        "channel": "plugin:heavy",
        "delivery_mode": "proactive",
        "ai_behavior": "respond",
        "visibility": [],
        "media_parts": [
            {"type": "image", "binary_base64": expanding_b64, "mime": "image/jpeg"}
            for _ in range(3)
        ],
    })

    manager.submit_proactive_callback.assert_called_once()
    callback = manager.submit_proactive_callback.call_args.args[0]
    assert callback["summary"] == "heavy respond"
    assert len(callback["media_images"]) == 1
    # Normalized, not passed through: every model client declares jpeg for
    # callback images, so the inline png must be re-encoded to match.
    assert callback["media_images"][0] != expanding_b64
    assert base64.b64decode(callback["media_images"][0])[:3] == bytes.fromhex("ffd8ff")


def test_push_byte_pool_refuses_an_overdraw() -> None:
    from app.main_server.character_runtime import _PushImageByteBudget

    budget = _PushImageByteBudget(100)
    assert budget.draw(60) is True
    assert budget.draw(60) is False, "must refuse rather than go negative"
    assert budget.remaining == 40
    assert budget.draw(40) is True
    assert budget.remaining == 0


def _animated_gif_base64(frames: int, size: int = 600) -> str:
    source = BytesIO()
    imgs = [Image.new("P", (size, size), i % 256) for i in range(frames)]
    imgs[0].save(source, format="GIF", save_all=True, append_images=imgs[1:])
    return base64.b64encode(source.getvalue()).decode("ascii")


def test_animated_inline_images_are_bounded_by_cumulative_frames() -> None:
    """Many small frames stay under every single-frame check but sum up.

    Each frame here is well inside the pixel ceiling and the whole animation is
    far inside the wire budget; only the cumulative bound catches it.
    """
    from app.main_server import character_runtime

    many = _animated_gif_base64(frames=60)
    assert character_runtime._approx_decoded_bytes(many) < (
        character_runtime._PLUGIN_CHAT_INLINE_TOTAL_MAX_BYTES
    )
    assert 600 * 600 < MAX_SOURCE_IMAGE_PIXELS  # one frame passes on its own
    assert 60 * 600 * 600 > MAX_SOURCE_IMAGE_PIXELS  # the sum does not

    assert character_runtime._inline_image_data_url_mime(many) is None


def test_short_animations_still_render() -> None:
    """The cumulative bound must not reject every animation."""
    from app.main_server import character_runtime

    few = _animated_gif_base64(frames=3, size=200)

    assert character_runtime._inline_image_data_url_mime(few) == "image/gif"


@pytest.mark.asyncio
async def test_inline_model_images_are_normalized_to_jpeg() -> None:
    """The resolver's job is to return what will actually be retained."""
    from app.main_server.character_runtime import _resolve_plugin_model_image

    out = await _resolve_plugin_model_image(
        {"type": "image", "binary_base64": _inline_png_base64(), "mime": "image/png"}
    )

    assert base64.b64decode(out)[:3] == bytes.fromhex("ffd8ff")


@pytest.mark.asyncio
async def test_budget_is_charged_on_the_retained_bytes(monkeypatch) -> None:
    """Charging the source under-counts when jpeg expands a compressible png.

    split_callbacks_by_image_budget always admits the head callback, so an
    under-charged batch would reach the model whole.
    """
    from app import main_server
    from app.main_server import character_runtime

    manager = _manager()
    monkeypatch.setattr(
        "app.main_server.character_runtime._get_session_manager",
        lambda _name: manager,
    )
    # Pool that fits the compressible source several times over but not the
    # expanded jpeg twice.
    expanding = _expands_under_jpeg_base64()
    normalized = character_runtime._normalize_inline_image_to_jpeg_base64(expanding)
    grown = character_runtime._approx_decoded_bytes(normalized)
    monkeypatch.setattr(
        character_runtime, "_PLUGIN_IMAGE_TOTAL_MAX_BYTES", int(grown * 1.5)
    )

    await main_server._handle_agent_event({
        "event_type": "proactive_message",
        "lanlan_name": "Test",
        "text": "two expanding images",
        "channel": "plugin:demo",
        "delivery_mode": "proactive",
        "ai_behavior": "respond",
        "visibility": [],
        "media_parts": [
            {"type": "image", "binary_base64": expanding, "mime": "image/png"}
            for _ in range(2)
        ],
    })

    callback = manager.submit_proactive_callback.call_args.args[0]
    # Only one fits once the GROWN size is what is charged; charging the
    # source would have admitted both.
    assert len(callback["media_images"]) == 1
    assert character_runtime._approx_decoded_bytes(
        callback["media_images"][0]
    ) == grown


@pytest.mark.asyncio
async def test_budget_survivors_follow_part_order_not_completion_order(
    monkeypatch,
) -> None:
    """Which images survive must not depend on network timing.

    Drawing from a shared pool inside the concurrent fetches let a fast later
    image starve a slow earlier one, so the set reaching the model varied run
    to run and broke the ordered-parts contract (Codex P2).
    """
    from app import main_server
    from app.main_server import character_runtime

    manager = _manager()
    big = "A" * (6 * 1024 * 1024 * 4 // 3)  # 6 MiB decoded, two will not fit

    async def _fetch(url: str) -> str:
        # The LATER part returns immediately; the earlier one dawdles. If
        # completion order decided, the second would win.
        if url.endswith("first"):
            await asyncio.sleep(0.05)
        return big

    monkeypatch.setattr(
        "app.main_server.character_runtime._get_session_manager",
        lambda _name: manager,
    )
    monkeypatch.setattr(
        "app.main_server.character_runtime._fetch_plugin_image_base64", _fetch
    )

    await main_server._handle_agent_event({
        "event_type": "proactive_message",
        "lanlan_name": "Test",
        "text": "ordered",
        "channel": "plugin:demo",
        "delivery_mode": "proactive",
        "ai_behavior": "respond",
        "visibility": [],
        "parts": [
            {"type": "image", "url": "http://127.0.0.1:48916/media/first"},
            {"type": "image", "url": "http://127.0.0.1:48916/media/second"},
        ],
    })

    callback = manager.submit_proactive_callback.call_args.args[0]
    assert len(callback["media_images"]) == 1, (
        "two 6 MiB images cannot both fit an 8 MiB push budget"
    )
    # Both fetches return identical bytes, so identity cannot distinguish them;
    # what this pins is that exactly the FIRST part was kept and the budget was
    # not consumed by whichever finished first.
    assert character_runtime._approx_decoded_bytes(
        callback["media_images"][0]
    ) == character_runtime._approx_decoded_bytes(big)


@pytest.mark.asyncio
async def test_read_images_are_not_resolved_without_a_session(monkeypatch) -> None:
    """Work that is guaranteed to be discarded must not be done at all.

    A read push with no session drops its images at the inject site anyway;
    fetching and decoding them first burns network, CPU and handler latency,
    and a background plugin can repeat that indefinitely.
    """
    from app import main_server

    manager = _manager()
    manager.session = None
    fetched: list[str] = []

    async def _fetch(url: str) -> str:
        fetched.append(url)
        return base64.b64encode(b"never-used").decode("ascii")

    monkeypatch.setattr(
        "app.main_server.character_runtime._get_session_manager",
        lambda _name: manager,
    )
    monkeypatch.setattr(
        "app.main_server.character_runtime._fetch_plugin_image_base64", _fetch
    )

    await main_server._handle_agent_event({
        "event_type": "proactive_message",
        "lanlan_name": "Test",
        "text": "look at this",
        "channel": "plugin:demo",
        "delivery_mode": "passive",
        "ai_behavior": "read",
        "visibility": [],
        "media_parts": [
            {
                "type": "image",
                "url": "http://127.0.0.1:48916/media/i%d" % i,
                "mime": "image/jpeg",
            }
            for i in range(4)
        ],
    })

    assert fetched == [], "no session means these are dropped regardless"


@pytest.mark.asyncio
async def test_respond_images_still_resolve_without_a_session(monkeypatch) -> None:
    """The skip is read-specific: respond images ride the callback."""
    from app import main_server

    manager = _manager()
    manager.session = None
    fetched: list[str] = []

    async def _fetch(url: str) -> str:
        fetched.append(url)
        return base64.b64encode(b"carried-on-the-callback").decode("ascii")

    monkeypatch.setattr(
        "app.main_server.character_runtime._get_session_manager",
        lambda _name: manager,
    )
    monkeypatch.setattr(
        "app.main_server.character_runtime._fetch_plugin_image_base64", _fetch
    )

    await main_server._handle_agent_event({
        "event_type": "proactive_message",
        "lanlan_name": "Test",
        "text": "react to this",
        "channel": "plugin:demo",
        "delivery_mode": "proactive",
        "ai_behavior": "respond",
        "visibility": [],
        "media_parts": [
            {"type": "image", "url": _IMAGE_URL, "mime": "image/jpeg"},
        ],
    })

    assert len(fetched) == 1
    callback = manager.submit_proactive_callback.call_args.args[0]
    assert len(callback["media_images"]) == 1


@pytest.mark.asyncio
async def test_chat_render_failure_does_not_cancel_model_delivery(monkeypatch) -> None:
    """A broken display must not take the model path down with it.

    render_chat_blocks runs before the callback is built, so an exception there
    would skip delivery entirely — losing the deferred images AND the text for
    a purely cosmetic failure.
    """
    from app import main_server

    manager = _manager()
    manager.render_chat_blocks = AsyncMock(side_effect=RuntimeError("socket died"))
    monkeypatch.setattr(
        "app.main_server.character_runtime._get_session_manager",
        lambda _name: manager,
    )
    monkeypatch.setattr(
        "app.main_server.character_runtime._fetch_plugin_image_base64",
        AsyncMock(return_value=base64.b64encode(b"still-delivered").decode("ascii")),
    )

    await main_server._handle_agent_event({
        "event_type": "proactive_message",
        "lanlan_name": "Test",
        "text": "text must survive a render failure",
        "channel": "plugin:demo",
        "delivery_mode": "proactive",
        "ai_behavior": "respond",
        "visibility": ["chat"],
        "media_parts": [{"type": "image", "url": _IMAGE_URL, "mime": "image/jpeg"}],
    })

    manager.render_chat_blocks.assert_awaited()
    manager.submit_proactive_callback.assert_called_once()
    callback = manager.submit_proactive_callback.call_args.args[0]
    assert callback["summary"] == "text must survive a render failure"
    assert len(callback["media_images"]) == 1


@pytest.mark.asyncio
async def test_offline_pending_images_are_bounded_across_pushes() -> None:
    """Passive read images bypass every per-push and per-turn budget.

    They go straight into the offline session's pending list, and the next
    stream_text attaches that whole accumulated list to ONE user message — so
    two individually valid pushes could exceed the one-turn provider budget.
    """
    from main_logic.omni_offline_client import OmniOfflineClient
    from main_logic.proactive_delivery import CALLBACK_IMAGE_MAX_COUNT

    session = OmniOfflineClient.__new__(OmniOfflineClient)
    session._pending_images = []

    # Two full pushes' worth, one image at a time, as read injection does.
    for i in range(CALLBACK_IMAGE_MAX_COUNT * 2):
        await session.stream_image("img-%d" % i, bypass_rate_limit=True)

    assert len(session._pending_images) == CALLBACK_IMAGE_MAX_COUNT
    # Drop-OLDEST: the newest frames are the ones still describing what the
    # plugin is talking about.
    assert session._pending_images[-1] == "img-%d" % (CALLBACK_IMAGE_MAX_COUNT * 2 - 1)
    assert "img-0" not in session._pending_images


@pytest.mark.asyncio
async def test_offline_pending_images_are_bounded_by_bytes_too() -> None:
    """A few large frames must not slip past a count-only bound."""
    from main_logic.omni_offline_client import OmniOfflineClient
    from main_logic.proactive_delivery import (
        CALLBACK_IMAGE_MAX_TOTAL_BYTES,
        approx_base64_decoded_bytes,
    )

    session = OmniOfflineClient.__new__(OmniOfflineClient)
    session._pending_images = []
    chunky = "A" * (5 * 1024 * 1024 * 4 // 3)  # ~5 MiB decoded each

    for _ in range(4):
        await session.stream_image(chunky, bypass_rate_limit=True)

    total = sum(approx_base64_decoded_bytes(i) for i in session._pending_images)
    assert total <= CALLBACK_IMAGE_MAX_TOTAL_BYTES
    assert len(session._pending_images) >= 1, "never trim to empty"


def test_chat_blocks_cap_image_count_and_keep_text_in_order() -> None:
    """Over-cap images drop out; the text mix keeps its canonical order."""
    from app.main_server.character_runtime import (
        _PLUGIN_CHAT_IMAGE_MAX_COUNT,
        _build_ordered_plugin_chat_blocks,
    )

    over_cap = _PLUGIN_CHAT_IMAGE_MAX_COUNT + 4
    parts: list[dict[str, str]] = []
    for i in range(over_cap):
        parts.append(
            {"type": "image", "url": "http://127.0.0.1:48916/media/img%d" % i}
        )
        parts.append({"type": "text", "text": "caption %d" % i})

    blocks = _build_ordered_plugin_chat_blocks(parts)

    images = [b for b in blocks if b["type"] == "image"]
    texts = [b["text"] for b in blocks if b["type"] == "text"]
    assert len(images) == 8
    assert images[0]["url"].endswith("/media/img0")
    assert images[-1]["url"].endswith("/media/img7")
    # Text is never truncated by the image cap, and the surviving mix stays in
    # canonical order rather than losing its tail.
    assert texts == ["caption %d" % i for i in range(over_cap)]
    assert blocks[0]["type"] == "image"
    assert blocks[1] == {"type": "text", "text": "caption 0"}


def _noise_png_base64(target_bytes: int) -> str:
    """A REAL png of roughly ``target_bytes``, with a modest pixel count.

    Random noise does not compress, so the payload tracks the raw pixel bytes.
    Must be a decodable image: the inline path now probes dimensions, so a
    ``"A" * n`` stand-in would be rejected as unreadable rather than by budget.
    """
    import os

    width = 1000
    height = max(1, target_bytes // 3 // width)
    source = BytesIO()
    Image.frombytes(
        "RGB", (width, height), os.urandom(width * height * 3)
    ).save(source, format="PNG")
    return base64.b64encode(source.getvalue()).decode("ascii")


def _expands_under_jpeg_base64() -> str:
    """A png that provably GROWS when normalized to jpeg.

    Flat colour is the extreme of png-compressible and the worst case for
    jpeg, which spends bytes per block regardless. Measured ~16 KiB png against
    ~63 KiB jpeg. Callers assert the growth as a precondition, so a codec change
    fails loudly instead of quietly making the test prove nothing.
    """
    source = BytesIO()
    Image.new("RGB", (2000, 2000), "white").save(source, format="PNG")
    return base64.b64encode(source.getvalue()).decode("ascii")


def _bomb_png_base64(width: int = 12000, height: int = 12000) -> str:
    """A decompression bomb: ~40 KiB on the wire, ~0.58 GB decoded as RGBA.

    12000x12000 = 144 MP sits UNDER Pillow's 178 MP hard error on purpose, so
    this exercises the host's pixel check rather than Pillow's built-in guard.

    Built in 1-bit mode: the fixture only needs the DIMENSIONS to be huge, and
    "1" holds the pixel buffer to 18 MB where "RGB" would allocate 432 MB and
    risk an OOM kill on a memory-capped CI runner.
    """
    source = BytesIO()
    with warnings.catch_warnings():
        # Pillow warns (not errors) above 89 MP; tripping its warning is the
        # point of the fixture, not a problem to surface in test output.
        warnings.simplefilter("ignore", Image.DecompressionBombWarning)
        Image.new("1", (width, height), 1).save(source, format="PNG")
    return base64.b64encode(source.getvalue()).decode("ascii")


def test_chat_blocks_cap_inline_data_url_bytes(monkeypatch) -> None:
    """Inline base64 rides the WebSocket frame, so it gets a byte budget too."""
    from app.main_server import character_runtime

    expanding_b64 = _expands_under_jpeg_base64()
    # Chat keeps the original bytes, so the pool is sized from those.
    monkeypatch.setattr(
        character_runtime,
        "_PLUGIN_CHAT_INLINE_TOTAL_MAX_BYTES",
        int(character_runtime._approx_decoded_bytes(expanding_b64) * 1.5),
    )
    parts = [
        {"type": "image", "binary_base64": expanding_b64, "mime": "image/png"}
        for _ in range(3)
    ]

    blocks = character_runtime._build_ordered_plugin_chat_blocks(parts)

    assert len([b for b in blocks if b["type"] == "image"]) == 1


# ---------------------------------------------------------------------------
# Decompression bombs
#
# Bytes and pixels are independent axes. A single-colour 12000x12000 PNG is
# ~40 KiB on the wire — it sails through any byte budget — while the renderer
# pays ~0.58 GB to decode it as RGBA. Only a pixel check catches this class.
# ---------------------------------------------------------------------------


def test_decompression_bomb_is_invisible_to_the_byte_budget() -> None:
    """Establishes WHY the pixel check is not redundant with the byte cap."""
    from app.main_server import character_runtime

    bomb = _bomb_png_base64()

    assert character_runtime._approx_decoded_bytes(bomb) < (
        character_runtime._PLUGIN_CHAT_INLINE_TOTAL_MAX_BYTES
    )
    assert 12000 * 12000 > MAX_SOURCE_IMAGE_PIXELS


def test_chat_blocks_drop_decompression_bombs() -> None:
    """A bomb never becomes a data: URL the renderer has to decode."""
    from app.main_server.character_runtime import _build_ordered_plugin_chat_blocks

    blocks = _build_ordered_plugin_chat_blocks([
        {"type": "image", "binary_base64": _bomb_png_base64(), "mime": "image/png"},
        {"type": "text", "text": "surrounding caption"},
    ])

    assert [b["type"] for b in blocks] == ["text"]


def test_chat_blocks_keep_ordinary_inline_images() -> None:
    """The pixel check must not reject legitimate images."""
    from app.main_server.character_runtime import _build_ordered_plugin_chat_blocks

    blocks = _build_ordered_plugin_chat_blocks([
        {"type": "image", "binary_base64": _inline_png_base64(), "mime": "image/png"},
    ])

    assert len(blocks) == 1
    assert blocks[0]["url"].startswith("data:image/png;base64,")


def test_declared_mime_cannot_inject_into_the_data_url() -> None:
    """A data: URL's media type ends at the FIRST comma.

    So a part may declare `image/svg+xml,<svg ...>` — which still satisfies a
    startswith("image/") test — and the browser would render that markup as the
    payload, never looking at the bytes the pixel probe just validated. The
    MIME therefore has to come from the parsed bytes, not from the part.
    """
    from app.main_server.character_runtime import _build_ordered_plugin_chat_blocks

    png = _inline_png_base64()
    blocks = _build_ordered_plugin_chat_blocks([{
        "type": "image",
        "binary_base64": png,
        "mime": "image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg'/>#",
    }])

    assert len(blocks) == 1
    url = blocks[0]["url"]
    assert url == "data:image/png;base64," + png
    # The whole point: nothing the part declared survives into the URL.
    assert "svg" not in url
    assert url.count(",") == 1


def test_inline_mime_is_the_detected_format_not_the_declared_one() -> None:
    """A wrong-but-harmless declaration is corrected, not trusted."""
    from app.main_server.character_runtime import _build_ordered_plugin_chat_blocks

    png = _inline_png_base64()
    blocks = _build_ordered_plugin_chat_blocks([
        {"type": "image", "binary_base64": png, "mime": "image/jpeg"},
    ])

    assert blocks[0]["url"].startswith("data:image/png;base64,")


def test_chat_blocks_drop_unreadable_inline_payloads() -> None:
    """Bytes this host cannot inspect are not handed to the renderer."""
    from app.main_server.character_runtime import _build_ordered_plugin_chat_blocks

    blocks = _build_ordered_plugin_chat_blocks([
        {"type": "image", "binary_base64": "bm90LWFuLWltYWdl", "mime": "image/png"},
    ])

    assert blocks == []


def test_pixel_probe_reads_dimensions_from_a_bounded_prefix() -> None:
    """The probe must not scale with payload size.

    A full base64 decode of an 8 MiB payload costs ~14 ms; reading the header
    off a bounded prefix is ~0.1 ms regardless of size. Guard the prefix bound
    so nobody "simplifies" it into a full decode.
    """
    from app.main_server import character_runtime

    assert character_runtime._PLUGIN_CHAT_HEADER_PREFIX_B64_CHARS == 64 * 1024
    big = _noise_png_base64(4 * 1024 * 1024)
    assert len(big) > character_runtime._PLUGIN_CHAT_HEADER_PREFIX_B64_CHARS
    # Truncating everything past the prefix still yields a verdict, which is
    # only possible if the probe never needed the tail.
    head = big[: character_runtime._PLUGIN_CHAT_HEADER_PREFIX_B64_CHARS]
    assert character_runtime._inline_image_data_url_mime(head) == "image/png"
    assert character_runtime._inline_image_data_url_mime(big) == "image/png"


def test_chat_blocks_url_images_cost_no_inline_budget() -> None:
    """URL-backed blocks are a frontend fetch, not WebSocket payload."""
    from app.main_server.character_runtime import _build_ordered_plugin_chat_blocks

    parts = [
        {"type": "image", "url": "http://127.0.0.1:48916/media/img%d" % i}
        for i in range(8)
    ]

    blocks = _build_ordered_plugin_chat_blocks(parts)

    assert len(blocks) == 8
