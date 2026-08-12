from __future__ import annotations

import asyncio
import base64
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock

import pytest
from PIL import Image


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
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["", "remember this image"], ids=["image-only", "text-and-image"])
async def test_read_image_waits_in_callback_when_no_model_session(
    monkeypatch,
    text: str,
) -> None:
    """A passive image survives until the next model session is available."""
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

    manager.enqueue_agent_callback.assert_called_once()
    callback = manager.enqueue_agent_callback.call_args.args[0]
    assert callback["delivery_mode"] == "passive"
    assert callback["media_images"] == [encoded]
    manager.submit_proactive_callback.assert_not_called()


@pytest.mark.asyncio
async def test_read_image_stream_failure_keeps_image_for_next_turn(monkeypatch) -> None:
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

    manager.enqueue_agent_callback.assert_called_once()
    callback = manager.enqueue_agent_callback.call_args.args[0]
    assert callback["media_images"] == [encoded]


@pytest.mark.asyncio
async def test_inline_png_is_normalized_to_jpeg_before_model_injection(monkeypatch) -> None:
    from app import main_server

    source = BytesIO()
    Image.new("RGBA", (2, 2), (255, 0, 0, 128)).save(source, format="PNG")
    encoded = base64.b64encode(source.getvalue()).decode("ascii")
    manager = _manager()
    monkeypatch.setattr(
        "app.main_server.character_runtime._get_session_manager",
        lambda _name: manager,
    )

    await main_server._handle_agent_event({
        "event_type": "proactive_message",
        "lanlan_name": "Test",
        "text": "remember it",
        "channel": "plugin:inline",
        "delivery_mode": "passive",
        "ai_behavior": "read",
        "visibility": [],
        "parts": [{"type": "image", "binary_base64": encoded, "mime": "image/png"}],
    })

    manager.session.stream_image.assert_awaited_once()
    model_bytes = base64.b64decode(
        manager.session.stream_image.await_args.args[0],
        validate=True,
    )
    with Image.open(BytesIO(model_bytes)) as model_image:
        assert model_image.format == "JPEG"


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


@pytest.mark.asyncio
async def test_external_image_urls_are_not_exposed_to_chat_or_model() -> None:
    from app.main_server.character_runtime import _plugin_image_chat_blocks

    assert await _plugin_image_chat_blocks([
        {"type": "image", "url": "https://example.com/media/not-local"},
        {"type": "image", "url": "http://localhost:48916/media/not-an-ip"},
        {"type": "image", "url": "http://127.0.0.1:48916/media/valid"},
    ]) == [{
        "type": "image",
        "url": "http://127.0.0.1:48916/media/valid",
    }]


@pytest.mark.asyncio
async def test_inline_image_is_exposed_to_chat_as_a_data_url() -> None:
    from app.main_server.character_runtime import _plugin_image_chat_blocks

    encoded = base64.b64encode(b"inline-image").decode("ascii")

    assert await _plugin_image_chat_blocks([{
        "type": "image",
        "binary_base64": encoded,
        "mime": "image/png",
    }]) == [{
        "type": "image",
        "url": f"data:image/png;base64,{encoded}",
    }]


@pytest.mark.asyncio
async def test_model_image_fetches_have_count_and_aggregate_limits(monkeypatch) -> None:
    from app import main_server

    manager = _manager()
    encoded = base64.b64encode(b"four").decode("ascii")
    fetch_image = AsyncMock(return_value=encoded)
    monkeypatch.setattr(
        "app.main_server.character_runtime._get_session_manager",
        lambda _name: manager,
    )
    monkeypatch.setattr(
        "app.main_server.character_runtime._fetch_plugin_image_base64",
        fetch_image,
    )
    monkeypatch.setattr(
        "app.main_server.character_runtime._PLUGIN_MODEL_IMAGE_AGGREGATE_MAX_BYTES",
        10,
    )

    await main_server._handle_agent_event({
        "event_type": "proactive_message",
        "lanlan_name": "Test",
        "text": "",
        "channel": "plugin:demo",
        "delivery_mode": "passive",
        "ai_behavior": "read",
        "visibility": [],
        "media_parts": [
            {
                "type": "image",
                "url": f"http://127.0.0.1:48916/media/image-{index}",
                "mime": "image/jpeg",
            }
            for index in range(12)
        ],
    })

    from app.main_server import character_runtime

    assert fetch_image.await_count == character_runtime._PLUGIN_MODEL_IMAGE_MAX_COUNT
    assert manager.session.stream_image.await_count == 2
    assert [call.args[0] for call in manager.session.stream_image.await_args_list] == [
        encoded,
        encoded,
    ]


@pytest.mark.asyncio
async def test_inline_image_only_chat_blind_message_is_rendered(monkeypatch) -> None:
    from app import main_server

    manager = _manager()
    encoded = base64.b64encode(b"inline-image").decode("ascii")
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
