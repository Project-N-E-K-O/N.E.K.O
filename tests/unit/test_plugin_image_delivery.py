from __future__ import annotations

import asyncio
import base64
import io
import warnings
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image

from plugin.sdk.shared.core.images import MAX_SOURCE_IMAGE_PIXELS


pytestmark = pytest.mark.unit


_IMAGE_URL = "http://127.0.0.1:48916/media/matrix-image"


def _browser_url(url: str) -> str:
    """What a chat block should carry for a given minted media URL.

    Chat blocks are same-origin paths so the picture loads when the browser is
    not on the host (Docker, another device). The absolute loopback form stays
    on the part itself, because the main server fetches THAT in-process.
    """
    from urllib.parse import urlsplit

    return urlsplit(url).path


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
        source="plugin",
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
        source="plugin",
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
async def test_non_native_realtime_read_images_are_skipped_before_fetch(
    monkeypatch,
) -> None:
    """``read`` is not supported on a realtime provider without native vision.

    Such a session answers ``stream_image(cache_latest=False)`` by RETURNING a
    VISION_MODEL description rather than putting anything in the conversation,
    and ``read`` owns no delivery ticket to hang that description on (which is
    what ``_stream_cb_media`` builds for ``respond``). Reaching the inject site
    therefore costs a fetch, a decode and a paid vision call for a string that
    is dropped on the floor, so the host bails out before any of it.

    The predecessor of this test asserted that ``stream_image`` WAS awaited and
    called itself ``..._uses_current_session_image_path``. It could not have
    caught the gap: ``manager.session`` is a MagicMock, so the real early-return
    in ``_transport.stream_image`` never ran, and the host did not read
    ``_supports_native_image`` at all. Deleting the flag from that test changed
    nothing about its outcome -- the assertion below is the one that makes the
    flag load-bearing.
    """
    from app import main_server

    manager = _manager()
    manager.session._supports_native_image = False
    encoded = base64.b64encode(b"non-native-read-image").decode("ascii")
    fetch = AsyncMock(return_value=encoded)
    monkeypatch.setattr(
        "app.main_server.character_runtime._get_session_manager",
        lambda _name: manager,
    )
    monkeypatch.setattr(
        "app.main_server.character_runtime._fetch_plugin_image_base64",
        fetch,
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

    # Skipped, not merely un-injected: the short circuit sits above the fetch,
    # so a background plugin cannot repeat the whole cost per push.
    fetch.assert_not_awaited()
    manager.session.stream_image.assert_not_awaited()
    # The text half of the cue still delivers -- only the media is dropped.
    manager.enqueue_agent_callback.assert_called_once()
    callback = manager.enqueue_agent_callback.call_args.args[0]
    assert callback["media_images"] == []
    manager.submit_proactive_callback.assert_not_called()


@pytest.mark.asyncio
async def test_native_realtime_read_still_injects_into_current_session(
    monkeypatch,
) -> None:
    """Dual of the test above: the skip is keyed on the flag, not on ``read``.

    Without this, flipping the host's condition to drop every ``read`` image
    would leave the suite green.
    """
    from app import main_server

    manager = _manager()
    manager.session._supports_native_image = True
    encoded = base64.b64encode(b"native-read-image").decode("ascii")
    fetch = AsyncMock(return_value=encoded)
    monkeypatch.setattr(
        "app.main_server.character_runtime._get_session_manager",
        lambda _name: manager,
    )
    monkeypatch.setattr(
        "app.main_server.character_runtime._fetch_plugin_image_base64",
        fetch,
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

    fetch.assert_awaited_once()
    manager.session.stream_image.assert_awaited_once_with(
        encoded,
        bypass_rate_limit=True,
        cache_latest=False,
        source="plugin",
    )
    manager.enqueue_agent_callback.assert_called_once()
    assert manager.enqueue_agent_callback.call_args.args[0]["media_images"] == []
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
        source="plugin",
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

    manager.render_chat_blocks.assert_awaited_once()
    call = manager.render_chat_blocks.await_args
    assert call.args[0] == [
        {"type": "text", "text": "look at this"},
        {
            "type": "image",
            "url": "/media/example",
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

    # Chat blocks carry the same-origin path; the parts fed in above keep the
    # absolute loopback form, which is what the model fetch uses.
    expected = [
        {"type": "image", "url": "/media/first"},
        {"type": "text", "text": "between"},
        {"type": "image", "url": "/media/last"},
    ]
    if ai_behavior == "blind":
        assert manager.render_chat_blocks.await_args.args[0] == expected
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
        "url": "/media/valid",
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

    # System message, not an assistant bubble: blind content never reaches the
    # model, so an assistant-looking bubble would be something she has no
    # memory of having produced.
    manager.render_chat_blocks.assert_awaited_once_with(
        [{
            "type": "image",
            "url": f"data:image/png;base64,{encoded}",
        }],
        request_id="inline-image-chat",
        source="plugin",
        source_name="inline",
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

    manager.render_chat_blocks.assert_awaited_once_with(
        [{
            "type": "image",
            "url": "/media/image-only",
        }],
        request_id="image-only-chat",
        source="plugin",
        # Derived from the channel (plugin:demo) so the bubble names its origin.
        source_name="demo",
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
                "url": "/media/both",
            },
        ],
        request_id="image-both",
        source="plugin",
        source_name="demo",
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

    # One path for every ai_behavior: chat visibility decides, identity does
    # not. Nothing renders as the assistant any more.
    assert manager.render_chat_blocks.await_count == int(shows_original)
    manager.passthrough_to_chat_bubble.assert_not_awaited()
    if shows_original:
        expected_blocks = []
        if with_text:
            expected_blocks.append({"type": "text", "text": "describe this"})
        expected_blocks.append({
            "type": "image",
            "url": _browser_url(_IMAGE_URL),
        })
        assert manager.render_chat_blocks.await_args.args[0] == expected_blocks

    if ai_behavior == "read" and model_reads:
        manager.session.stream_image.assert_awaited_once_with(
            encoded,
            bypass_rate_limit=True,
            cache_latest=False,
            source="plugin",
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
        # read/respond + chat now render too — as a system message, alongside
        # the model injection, not instead of it.
        ("read", "passive", ["chat"], False, True, True),
        ("respond", "proactive", [], True, False, False),
        ("respond", "proactive", ["chat"], True, False, True),
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
    """Text-only pushes render as a system message whenever chat is visible.

    Previously only ``blind`` reached chat, wearing the assistant's identity.
    Every ai_behavior now renders through the same system path, so the matrix
    checks ``visibility`` alone rather than ``visibility and blind``.
    """
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
    assert manager.submit_proactive_callback.call_count == int(expects_submit)
    assert manager.enqueue_agent_callback.call_count == int(expects_enqueue)
    # Chat visibility alone decides rendering now, and it never wears the
    # assistant's identity.
    assert manager.render_chat_blocks.await_count == int(expects_chat)
    manager.passthrough_to_chat_bubble.assert_not_awaited()


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


def test_staged_quotas_match_the_documented_plugin_contract() -> None:
    """The guide now states the per-TURN staging quotas, not just per push.

    The 8 above is the per-push ceiling and stays true. What was never written
    down is that text-mode `read` images are staged rather than sent, so one
    turn can carry several pushes' worth -- and the maintainer chose to keep
    the resulting nine rather than trim across sources.
    """
    from pathlib import Path

    from main_logic import proactive_delivery as pd

    guide = Path(__file__).resolve().parents[2] / "plugin" / "PLUGIN_DEVELOPMENT_GUIDE.md"
    text = guide.read_text(encoding="utf-8")

    # The figures the guide prints, tied to the constants that enforce them, so
    # changing a quota without the doc (or the reverse) fails here.
    assert f"| 用户自己的截图 / 摄像头帧 | {pd.USER_PENDING_IMAGE_MAX_COUNT} |" in text
    assert f"| 插件 `read` 图片 | {pd.PLUGIN_PENDING_IMAGE_MAX_COUNT} |" in text
    user_mib = pd.USER_PENDING_IMAGE_MAX_BYTES // (1024 * 1024)
    plugin_mib = pd.PLUGIN_PENDING_IMAGE_MAX_BYTES // (1024 * 1024)
    assert f"| {pd.USER_PENDING_IMAGE_MAX_COUNT} | {user_mib} MiB |" in text
    assert f"| {pd.PLUGIN_PENDING_IMAGE_MAX_COUNT} | {plugin_mib} MiB |" in text

    # The stated turn total must be what the quotas actually add up to
    # (both lanes plus the single proactive slot).
    turn_total = (
        pd.USER_PENDING_IMAGE_MAX_COUNT + pd.PLUGIN_PENDING_IMAGE_MAX_COUNT + 1
    )
    assert f"一个回合最多可能带 {turn_total} 张图" in text


def test_the_guide_does_not_promise_ordering_the_model_path_cannot_keep() -> None:
    """parts are documented as ordered; only chat rendering honours that.

    The model path filters images out of the ordered list and aggregates text,
    so interleaved caption/image pairs do not survive. Rather than leave the
    promise standing, the guide now scopes it.
    """
    from pathlib import Path

    guide = Path(__file__).resolve().parents[2] / "plugin" / "PLUGIN_DEVELOPMENT_GUIDE.md"
    text = guide.read_text(encoding="utf-8")
    assert "**进模型的那条路不保序**" in text


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
        character_runtime._normalize_inline_image_to_model_profile(
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
        character_runtime._normalize_inline_image_to_model_profile(
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
    """A GIF with genuinely distinct frames.

    Each frame gets its own colour: identical frames are deduped by the encoder,
    which silently produced a ONE-frame file and made the bound look unreachable.
    """
    source = BytesIO()
    imgs = [
        Image.new("RGB", (size, size), (i % 256, (i * 7) % 256, (i * 13) % 256))
        for i in range(frames)
    ]
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


def test_animation_frame_count_is_bounded_independently_of_pixels() -> None:
    """Thousands of 1x1 frames multiply to nothing but still cost per frame.

    The cumulative-pixel bound cannot see them: 400 frames of one pixel is 400
    pixels. Each frame is still a decode and an animation tick.
    """
    from app.main_server import character_runtime

    tiny_but_many = _animated_gif_base64(frames=400, size=2)
    # 400 frames of 2x2 is 1600 pixels total and ~15 KiB on the wire: neither
    # the cumulative-pixel bound nor the byte budget can see it.
    assert 400 * 2 * 2 < MAX_SOURCE_IMAGE_PIXELS, "pixels alone must not catch it"
    assert 400 > character_runtime._PLUGIN_CHAT_MAX_ANIMATION_FRAMES

    assert character_runtime._inline_image_data_url_mime(tiny_but_many) is None


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
    # expanded jpeg twice. Measured through the resolver's OWN normalizer, so
    # the figure follows the model profile instead of being a second guess at
    # what the resolver retains.
    expanding = _expands_under_jpeg_base64()
    normalized = character_runtime._normalize_inline_image_to_model_profile(expanding)
    grown = character_runtime._approx_decoded_bytes(normalized)
    # The premise, asserted rather than assumed: if a codec change ever made
    # the jpeg SMALLER than the png, charging the source would over-count and
    # this test would pass while proving the opposite of its name.
    assert grown > character_runtime._approx_decoded_bytes(expanding)
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
    jpeg, which spends bytes per block regardless. Measured ~4 KiB png against
    ~15 KiB jpeg. Callers assert the growth as a precondition, so a codec change
    fails loudly instead of quietly making the test prove nothing.

    Sized to the MODEL PROFILE, and derived from those constants rather than
    hardcoded. The model resolver now bounds every copy it returns to that
    profile, so a bigger fixture (this was 2000x2000) comes back DOWNSCALED --
    and the byte-budget tests below, which are about accounting and not about
    resolution, would silently be measuring the resize instead of the jpeg
    expansion they claim to measure. At exactly the profile the resize is a
    no-op and the claim stays true.
    """
    from utils.screenshot_utils import COMPRESS_TARGET_HEIGHT, MODEL_IMAGE_MAX_WIDTH

    source = BytesIO()
    Image.new(
        "RGB", (MODEL_IMAGE_MAX_WIDTH, COMPRESS_TARGET_HEIGHT), "white"
    ).save(source, format="PNG")
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


# ---------------------------------------------------------------------------
# Per-source staged-image quotas (user 5 / plugin 3)
#
# The maintainer's requirement is mutual non-interference: a plugin burst must
# never cost the user a frame, and the reverse must hold too. A single shared
# cap cannot express that -- both eviction policies available under one cap let
# one source damage the other, which is why both were rejected during review.
# ---------------------------------------------------------------------------


def _fresh_offline_client():
    from main_logic.omni_offline_client._client import OmniOfflineClient

    client = OmniOfflineClient.__new__(OmniOfflineClient)
    client._pending_images = []
    client._pending_plugin_images = []
    return client


def test_quota_numbers_are_the_agreed_ones():
    """Pin the numbers themselves, not just the behavior derived from them.

    Every other test here reads the constants, so raising a cap would silently
    move the goalposts and keep passing. This is the one assertion that fails
    if the agreed 5 / 3 changes.
    """
    from main_logic import proactive_delivery as pd

    assert pd.USER_PENDING_IMAGE_MAX_COUNT == 5
    assert pd.PLUGIN_PENDING_IMAGE_MAX_COUNT == 3


@pytest.mark.asyncio
async def test_plugin_burst_never_evicts_user_frames():
    from main_logic import proactive_delivery as pd

    client = _fresh_offline_client()
    for name in ("user-a", "user-b"):
        await client.stream_image(name)

    for i in range(pd.PLUGIN_PENDING_IMAGE_MAX_COUNT * 4):
        await client.stream_image(f"plugin-{i}", source="plugin")

    # The user's frames are untouched — not merely present, but exactly as
    # staged and in order.
    assert client._pending_images == ["user-a", "user-b"]
    # The plugin kept only its own newest, inside its own quota.
    assert client._pending_plugin_images == ["plugin-9", "plugin-10", "plugin-11"]


@pytest.mark.asyncio
async def test_user_burst_never_evicts_plugin_frames():
    """The dual. A quota that only protects one direction is not isolation."""
    from main_logic import proactive_delivery as pd

    client = _fresh_offline_client()
    for i in range(pd.PLUGIN_PENDING_IMAGE_MAX_COUNT):
        await client.stream_image(f"plugin-{i}", source="plugin")
    staged_by_plugin = list(client._pending_plugin_images)

    for i in range(pd.USER_PENDING_IMAGE_MAX_COUNT * 3):
        await client.stream_image(f"user-{i}")

    assert client._pending_plugin_images == staged_by_plugin
    assert len(client._pending_images) == pd.USER_PENDING_IMAGE_MAX_COUNT
    # Trimming took the user's own oldest, keeping the newest run.
    assert client._pending_images[-1] == "user-14"


@pytest.mark.asyncio
async def test_staged_images_keep_the_list_identity():
    """turn.py holds a reference and clears in place, so rebinding would leak.

    A quota implemented as `self._pending_images = queue[-cap:]` would pass
    every count assertion above and still break the magic-command choke point,
    which would go on clearing a list nobody reads any more.
    """
    client = _fresh_offline_client()
    user_list = client._pending_images
    plugin_list = client._pending_plugin_images

    for i in range(12):
        await client.stream_image(f"user-{i}")
        await client.stream_image(f"plugin-{i}", source="plugin")

    assert client._pending_images is user_list
    assert client._pending_plugin_images is plugin_list


@pytest.mark.asyncio
async def test_unknown_source_is_charged_to_the_user_quota():
    """Anything that is not explicitly "plugin" is the user's own frame.

    Fail-safe direction: an unrecognised label must not silently create a
    third, unbounded queue.
    """
    from main_logic import proactive_delivery as pd

    client = _fresh_offline_client()
    for i in range(pd.USER_PENDING_IMAGE_MAX_COUNT * 2):
        await client.stream_image(f"x-{i}", source="something-else")

    assert len(client._pending_images) == pd.USER_PENDING_IMAGE_MAX_COUNT
    assert client._pending_plugin_images == []


@pytest.mark.asyncio
async def test_plugin_queue_is_created_on_instances_built_without_init():
    """Tests and legacy callers build the client via __new__.

    The proactive slot is read defensively for the same reason; the plugin
    queue must not become the one attribute that raises there.
    """
    from main_logic.omni_offline_client._client import OmniOfflineClient

    client = OmniOfflineClient.__new__(OmniOfflineClient)
    client._pending_images = []
    assert not hasattr(client, "_pending_plugin_images")

    await client.stream_image("late", source="plugin")
    assert client._pending_plugin_images == ["late"]


def _animated_gif_bytes(frame_count: int, size=(2, 2)) -> bytes:
    """A GIF whose frames genuinely differ.

    Identical frames get merged by the encoder, which silently produces a
    1-frame file and makes an "animation" test test nothing. Every caller here
    asserts the realised frame count before using the fixture.
    """
    from PIL import Image as _Image

    frames = []
    for i in range(frame_count):
        frame = _Image.new("P", size)
        frame.putpalette([(i * 7) % 256, (i * 11) % 256, (i * 13) % 256] * 256)
        frame.putpixel((0, 0), i % 256)
        frames.append(frame)
    buf = io.BytesIO()
    frames[0].save(
        buf, format="GIF", save_all=True, append_images=frames[1:],
        duration=1, disposal=2,
    )
    return buf.getvalue()


def test_over_ceiling_animation_is_refused_without_walking_it():
    """The ceiling must bound VALIDATION work, not just renderer work.

    Reading ``n_frames`` on the FULL payload decodes the whole animation before
    the ceiling can reject it, so a GIF with thousands of 2x2 frames -- which
    stays well inside the wire budget -- costs a full walk on the event loop
    just to be refused.

    The bounded header prefix is exempt: it is 64 KiB by construction and
    cannot walk an animation, and the function deliberately reads n_frames
    there to avoid promoting an animation to a still.
    """
    from PIL import Image as _Image

    from app.main_server import character_runtime as cr

    ceiling = cr._PLUGIN_CHAT_MAX_ANIMATION_FRAMES
    raw = _animated_gif_bytes(ceiling * 8)
    with _Image.open(io.BytesIO(raw)) as probe:
        assert getattr(probe, "n_frames", 1) == ceiling * 8, "fixture collapsed"

    full_payload_len = len(raw)
    seeks = []
    walked_full_payload = []
    real_open = cr.Image.open

    class _CountingImage:
        def __init__(self, inner, payload_len):
            self._inner = inner
            self._payload_len = payload_len

        def __enter__(self):
            self._inner.__enter__()
            return self

        def __exit__(self, *exc):
            return self._inner.__exit__(*exc)

        def seek(self, frame):
            if self._payload_len == full_payload_len:
                seeks.append(frame)
            return self._inner.seek(frame)

        def __getattr__(self, name):
            # RECORD, never raise: the caller wraps this in
            # `except Exception: return None`, so a probe that raises is
            # swallowed and the test passes for the wrong reason.
            if name == "n_frames" and self._payload_len == full_payload_len:
                walked_full_payload.append(name)
            return getattr(self._inner, name)

    def _tracking_open(stream, *a, **k):
        payload_len = len(stream.getvalue()) if hasattr(stream, "getvalue") else -1
        return _CountingImage(real_open(stream, *a, **k), payload_len)

    with patch.object(cr.Image, "open", _tracking_open):
        assert cr._inline_image_data_url_mime(base64.b64encode(raw).decode()) is None

    assert not walked_full_payload, (
        "n_frames on the full payload decodes the whole animation to count it"
    )
    # Bounded by the ceiling, not by the animation's own length.
    assert seeks, "the full payload was never counted at all"
    assert len(seeks) <= ceiling + 2, (
        f"walked {len(seeks)} frames for a {ceiling} ceiling"
    )


def test_animation_within_the_ceiling_is_still_accepted():
    """The bound must reject too-long animations, not animation itself."""
    from app.main_server import character_runtime as cr

    raw = _animated_gif_bytes(5)
    assert cr._inline_image_data_url_mime(base64.b64encode(raw).decode()) == "image/gif"


def _b64_of_decoded_size(decoded_bytes: int) -> str:
    """A base64 string whose approx decoded size is decoded_bytes."""
    return "A" * ((decoded_bytes + 2) // 3 * 4)


def test_byte_ceilings_are_the_agreed_ones():
    from main_logic import proactive_delivery as pd

    assert pd.PLUGIN_PENDING_IMAGE_MAX_BYTES == pd.CALLBACK_IMAGE_MAX_TOTAL_BYTES
    assert pd.USER_PENDING_IMAGE_MAX_BYTES == 2 * pd.CALLBACK_IMAGE_MAX_TOTAL_BYTES


@pytest.mark.asyncio
async def test_plugin_byte_ceiling_trims_inside_its_own_quota():
    """Three images can sit inside the COUNT quota and still be ~24 MiB.

    The trim must come out of the plugin's own lane -- this is why bytes could
    be bounded at all without reopening the cross-source eviction problem.
    """
    from main_logic import proactive_delivery as pd

    client = _fresh_offline_client()
    await client.stream_image("user-frame")

    near_budget = pd.PLUGIN_PENDING_IMAGE_MAX_BYTES * 3 // 4
    for i in range(pd.PLUGIN_PENDING_IMAGE_MAX_COUNT):
        await client.stream_image(_b64_of_decoded_size(near_budget), source="plugin")

    staged = sum(
        pd.approx_base64_decoded_bytes(i) for i in client._pending_plugin_images
    )
    assert staged <= pd.PLUGIN_PENDING_IMAGE_MAX_BYTES
    assert len(client._pending_plugin_images) < pd.PLUGIN_PENDING_IMAGE_MAX_COUNT
    # The user's frame is untouched: the byte trim stayed in the plugin's lane.
    assert client._pending_images == ["user-frame"]


@pytest.mark.asyncio
async def test_a_lone_oversized_image_is_kept():
    """The byte ceiling bounds ACCUMULATION, not a single image.

    One frame that is over already passed its own per-image limit upstream;
    dropping the only image would be a silent loss with nothing to show for it.
    """
    from main_logic import proactive_delivery as pd

    client = _fresh_offline_client()
    huge = _b64_of_decoded_size(pd.PLUGIN_PENDING_IMAGE_MAX_BYTES * 2)
    await client.stream_image(huge, source="plugin")

    assert client._pending_plugin_images == [huge]


@pytest.mark.asyncio
async def test_upload_timeout_is_clamped():
    """An unbounded upload timeout is an unbounded shutdown delay.

    The child transport holds its image lock for the whole upload and shutdown
    waits on that lock, so `timeout=3600` could wedge a STOP for an hour.
    """
    from plugin.sdk.shared.core import images as images_mod

    seen = []

    class _Ctx:
        handler_ctx = "timer.x"

        def _ensure_image_upload_available(self):
            return None

        async def _upload_image(self, data, *, mime, deadline=None, timeout):
            seen.append(timeout)
            return {"type": "image", "url": "http://127.0.0.1:1/media/x"}

    uploader = images_mod.PluginImages(_Ctx())
    with patch.object(images_mod, "normalize_image_to_jpeg", lambda *a, **k: b"jpeg"):
        from PIL import Image as _Image
        buf = io.BytesIO()
        _Image.new("RGB", (4, 4), "red").save(buf, format="PNG")
        await uploader.upload(buf.getvalue(), timeout=3600.0)

    assert seen == [images_mod.MAX_UPLOAD_TIMEOUT_SECONDS]


def test_the_two_consumers_resolve_a_dual_source_part_differently():
    """Documents WHY dual-source parts are refused rather than ordered.

    If this ever stops being true the rejection could be replaced by a shared
    precedence -- but while it holds, a part carrying both sources shows the
    user one image and gives the character another.
    """
    from app.main_server import character_runtime as cr
    import inspect

    model_src = inspect.getsource(cr._resolve_plugin_model_image)
    chat_src = inspect.getsource(cr._build_plugin_chat_blocks)
    # Model path reads the inline bytes first; chat path reads the url first.
    assert model_src.index("binary_base64") < model_src.index('part.get("url")')
    assert chat_src.index('part.get("url")') < chat_src.index("binary_base64")


def test_dual_source_image_parts_are_dropped_for_both_consumers():
    from app.main_server import character_runtime as cr

    url_only = {"type": "image", "url": "http://127.0.0.1:9/media/a"}
    inline_only = {"type": "image", "binary_base64": "AAAA", "mime": "image/png"}
    conflicting = {
        "type": "image",
        "url": "http://127.0.0.1:9/media/b",
        "binary_base64": "BBBB",
        "mime": "image/png",
    }
    text = {"type": "text", "text": "hello"}

    kept = cr._drop_conflicting_image_parts([text, url_only, conflicting, inline_only])

    assert conflicting not in kept
    # Everything unambiguous survives, in order.
    assert kept == [text, url_only, inline_only]


def test_a_url_or_inline_part_alone_is_never_treated_as_conflicting():
    """The rejection must not fire on the ordinary single-source shapes."""
    from app.main_server import character_runtime as cr

    assert not cr._image_part_payloads_conflict(
        {"type": "image", "url": "http://127.0.0.1:9/media/a"}
    )
    assert not cr._image_part_payloads_conflict(
        {"type": "image", "binary_base64": "AAAA"}
    )
    # Empty / whitespace counts as absent, not as a second source.
    assert not cr._image_part_payloads_conflict(
        {"type": "image", "url": "http://127.0.0.1:9/media/a", "binary_base64": "  "}
    )
    # Non-image parts are never affected.
    assert not cr._image_part_payloads_conflict(
        {"type": "audio", "url": "u", "binary_base64": "b"}
    )
    assert cr._image_part_payloads_conflict(
        {"type": "image", "url": "u", "binary_base64": "b"}
    )


@pytest.mark.asyncio
async def test_a_dual_source_part_reaches_neither_the_model_nor_the_chat(
    monkeypatch,
) -> None:
    """Pins the CALL SITE, not just the predicate.

    The unit tests above prove _drop_conflicting_image_parts works. They stay
    green if the call at the event boundary is deleted, which is the failure
    that would actually ship -- so this drives the real handler and asserts
    both consumers came away empty.
    """
    from app import main_server

    manager = _manager()
    # A REAL png: with fake bytes the inline branch fails to decode anyway, so
    # the assertion below would pass even with the sanitization removed.
    from PIL import Image as _Image
    _buf = io.BytesIO()
    _Image.new("RGB", (4, 4), "green").save(_buf, format="PNG")
    encoded = base64.b64encode(_buf.getvalue()).decode("ascii")
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
        "text": "which image is this",
        "channel": "plugin:demo",
        "delivery_mode": "passive",
        "ai_behavior": "read",
        "visibility": ["chat"],
        "parts": [
            {"type": "text", "text": "which image is this"},
            {
                "type": "image",
                "url": _IMAGE_URL,
                "binary_base64": encoded,
                "mime": "image/jpeg",
            },
        ],
    })

    # The model was given no image at all, rather than one of the two.
    manager.session.stream_image.assert_not_awaited()
    # And the chat bubble rendered no image either -- the point is that the
    # two never disagree, so dropping it from one side only would be worse.
    for call in manager.render_chat_blocks.await_args_list:
        for block in call.args[0]:
            assert block.get("type") != "image", "chat kept a part the model lost"


@pytest.mark.asyncio
async def test_the_legacy_media_parts_path_drops_dual_source_too(
    monkeypatch,
) -> None:
    """Legacy pushes carry `media_parts` instead of `parts`.

    That branch bypasses the ordered list entirely, so sanitizing only the v2
    path would leave the older shape as an open door to the same divergence.
    """
    from app import main_server

    manager = _manager()
    # A REAL png: with fake bytes the inline branch fails to decode anyway, so
    # the assertion below would pass even with the sanitization removed.
    from PIL import Image as _Image
    _buf = io.BytesIO()
    _Image.new("RGB", (4, 4), "green").save(_buf, format="PNG")
    encoded = base64.b64encode(_buf.getvalue()).decode("ascii")
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
        "text": "legacy shape",
        "channel": "plugin:demo",
        "delivery_mode": "passive",
        "ai_behavior": "read",
        "visibility": [],
        "media_parts": [
            {
                "type": "image",
                "url": _IMAGE_URL,
                "binary_base64": encoded,
                "mime": "image/jpeg",
            }
        ],
    })

    manager.session.stream_image.assert_not_awaited()


def test_chat_gets_a_same_origin_path_while_the_model_keeps_the_absolute_url():
    """The two URLs are deliberately different, and that is the whole fix.

    127.0.0.1 is correct for the main server, which fetches in-process on the
    same host. It is wrong for the browser whenever the browser is elsewhere --
    Docker, or another device -- because it then means the viewer's own
    machine. Rewriting BOTH would break the model fetch; rewriting neither is
    the bug.
    """
    from app.main_server import character_runtime as cr

    part = {"type": "image", "url": _IMAGE_URL, "mime": "image/jpeg"}
    blocks = cr._build_plugin_image_chat_blocks([part])

    assert blocks == [{"type": "image", "url": "/media/matrix-image"}]
    # The part itself is untouched: _fetch_plugin_image_base64 still receives
    # the absolute address, and _is_local_plugin_media_url still accepts it.
    assert part["url"] == _IMAGE_URL
    assert cr._is_local_plugin_media_url(part["url"])


def test_the_rewritten_chat_url_is_a_path_not_a_host():
    """A same-origin URL must carry no scheme and no authority.

    If any of those survive, the browser goes back to contacting 127.0.0.1 and
    the Docker case is broken again while every equality assertion still reads
    plausibly.
    """
    from urllib.parse import urlsplit

    from app.main_server import character_runtime as cr

    rewritten = cr._browser_media_url(_IMAGE_URL)
    parsed = urlsplit(rewritten)
    assert parsed.scheme == ""
    assert parsed.netloc == ""
    assert rewritten.startswith("/media/")


def test_the_main_server_serves_the_media_path_it_now_hands_out():
    """The rewrite is only safe because this server answers that path.

    Emitting /media/<id> without the route would 404 in the desktop build,
    which has no proxy in front of it -- turning a Docker-only defect into an
    everywhere defect.
    """
    from app.main_server.web_app import app

    paths = {getattr(route, "path", None) for route in app.routes}
    assert "/media/{image_id}" in paths

@pytest.mark.asyncio
async def test_media_route_bounds_a_trickling_upstream(monkeypatch):
    """httpx's timeout bounds one read, not the transfer.

    An upstream sending a few bytes just inside the idle timeout satisfies it
    forever, holding the connection and the task. The idle timeout is kept --
    it catches a stalled peer fast -- but a total deadline is what catches a
    slow one at all.
    """
    import asyncio as _asyncio
    import time as _time

    from main_routers import plugin_media_router as pmr

    monkeypatch.setattr(pmr, "_TOTAL_DEADLINE_S", 0.3)

    class _Trickle:
        status_code = 200
        headers = {"content-type": "image/jpeg"}

        def raise_for_status(self):
            return None

        async def aiter_bytes(self):
            while True:
                await _asyncio.sleep(0.05)
                yield b"x"

    class _Stream:
        async def __aenter__(self):
            return _Trickle()

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(
        pmr, "get_internal_http_client",
        lambda: type("C", (), {"stream": lambda self, *a, **k: _Stream()})(),
    )
    monkeypatch.setattr(pmr, "resolve_user_plugin_base", lambda: "http://127.0.0.1:1")

    started = _time.monotonic()
    with pytest.raises(Exception) as raised:
        # Outer guard: without the route's own deadline the trickle never ends,
        # and a guard test that hangs forever is a CI hazard rather than a
        # guard. This makes that regression FAIL instead of stalling.
        await _asyncio.wait_for(pmr.get_plugin_media("abc123"), timeout=3.0)
    elapsed = _time.monotonic() - started

    assert elapsed < 3.0, f"a trickling upstream held the request for {elapsed:.1f}s"
    assert getattr(raised.value, "status_code", None) == 504


def test_media_route_refuses_ids_it_did_not_mint():
    """The route must not become a general proxy for any spellable path."""
    from main_routers import plugin_media_router as pmr

    assert pmr._IMAGE_ID_PATTERN.match("a1B2-c3_d4")
    for bad in ("../secret", "a/b", "", "x" * 200, "a b", "a.png"):
        assert not pmr._IMAGE_ID_PATTERN.match(bad), bad


@pytest.mark.asyncio
async def test_media_proxy_follows_the_port_that_minted_the_url(monkeypatch):
    """The proxy must resolve the plugin origin the way the MINTER does.

    plugin/core/communication.py reads NEKO_USER_PLUGIN_SERVER_PORT before
    falling back to the configured base, because the plugin server rewrites
    that variable when its preferred port is busy. A proxy resolving any other
    way sends the request to the port that did not mint the id -- so the image
    is missing precisely in the fallback case the variable exists to handle.
    """
    from main_routers import plugin_media_router as pmr

    requested: list[str] = []

    class _Resp:
        status_code = 200
        headers = {"content-type": "image/jpeg"}

        def raise_for_status(self):
            return None

        async def aiter_bytes(self):
            yield b"jpegbytes"

    class _Stream:
        def __init__(self, url):
            requested.append(url)

        async def __aenter__(self):
            return _Resp()

        async def __aexit__(self, *exc):
            return False

    class _Client:
        def stream(self, _method, url, **_kw):
            return _Stream(url)

    monkeypatch.setattr(pmr, "get_internal_http_client", lambda: _Client())
    monkeypatch.setenv("NEKO_USER_PLUGIN_SERVER_PORT", "49999")

    # Deliberately NOT stubbed: the point is that the router reads the same
    # rule the minter does, so this exercises the real resolver.

    response = await pmr.get_plugin_media("abc123")

    assert requested == ["http://127.0.0.1:49999/media/abc123"]
    assert response.body == b"jpegbytes"

@pytest.mark.asyncio
async def test_model_fetch_bounds_a_trickling_media_endpoint(monkeypatch):
    """The dual of the same bound on the browser-facing /media route.

    httpx's timeout bounds one read, not the transfer, so an endpoint sending
    a few bytes just inside each interval holds a connection and a slot in the
    bounded fetch pool indefinitely. Both paths read from the same store; a
    defect fixed on one side belongs on the other.
    """
    import asyncio as _asyncio
    import time as _time

    from app.main_server import character_runtime as cr

    monkeypatch.setattr(cr, "_PLUGIN_IMAGE_FETCH_TOTAL_DEADLINE_S", 0.3)

    class _Trickle:
        headers = {"content-type": "image/jpeg"}

        def raise_for_status(self):
            return None

        async def aiter_bytes(self):
            while True:
                await _asyncio.sleep(0.05)
                yield b"x"

    class _Stream:
        async def __aenter__(self):
            return _Trickle()

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(
        cr, "get_internal_http_client",
        lambda: type("C", (), {"stream": lambda self, *a, **k: _Stream()})(),
    )

    started = _time.monotonic()
    with pytest.raises((TimeoutError, _asyncio.TimeoutError)):
        # Outer bound so a regression fails instead of hanging the run.
        await _asyncio.wait_for(cr._fetch_plugin_image_base64(_IMAGE_URL), timeout=3.0)
    elapsed = _time.monotonic() - started

    assert elapsed < 2.0, f"a trickling endpoint held the fetch for {elapsed:.1f}s"


@pytest.mark.asyncio
async def test_httpx_timeouts_are_reported_as_timeouts(monkeypatch):
    """httpx.TimeoutException is NOT a builtin TimeoutError.

    Its MRO is TransportError -> RequestError -> HTTPError -> Exception, so a
    connect/read/pool timeout would land in the generic branch and report 502,
    reporting a slow upstream with the same code as a broken one.
    """
    import httpx

    from main_routers import plugin_media_router as pmr

    assert not issubclass(httpx.TimeoutException, TimeoutError)

    class _Stream:
        async def __aenter__(self):
            raise httpx.ReadTimeout("slow")

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(
        pmr, "get_internal_http_client",
        lambda: type("C", (), {"stream": lambda self, *a, **k: _Stream()})(),
    )
    monkeypatch.setattr(pmr, "resolve_user_plugin_base", lambda: "http://127.0.0.1:1")

    with pytest.raises(Exception) as raised:
        await pmr.get_plugin_media("abc123")
    assert getattr(raised.value, "status_code", None) == 504


# ---------------------------------------------------------------------------
# Per-request ceiling across sources
#
# The per-source staging quotas (user 5/16 MiB, plugin 3/8 MiB, plus the
# proactive screenshot) are independent BY DESIGN so neither source can spend
# the other's budget. They all land on one HumanMessage though, so their sum
# is what the provider is asked to accept -- several times the per-request
# ceiling, which rejects the whole request rather than dropping images.
# ---------------------------------------------------------------------------


def _b64_of_size(decoded_bytes: int, filler: str) -> str:
    """A base64 string whose approx decoded size is ``decoded_bytes``."""
    return filler * ((decoded_bytes * 4 // 3) // len(filler))


def test_turn_image_budget_trims_oldest_across_sources() -> None:
    from main_logic.proactive_delivery import (
        TURN_ATTACHED_IMAGE_MAX_TOTAL_BYTES,
        trim_images_to_turn_budget,
    )

    half = TURN_ATTACHED_IMAGE_MAX_TOTAL_BYTES // 2
    # Attachment order is chronological: proactive screenshot, plugin frame,
    # then the user's own frame. Three halves overflow; the user's frame is
    # what the message is about, so it must be the survivor.
    proactive = _b64_of_size(half, "p")
    plugin = _b64_of_size(half, "g")
    user = _b64_of_size(half, "u")

    kept, dropped = trim_images_to_turn_budget([proactive, plugin, user])

    assert dropped == 1
    assert kept == [plugin, user]
    # Trimming is a prefix take, which is what lets the caller conclude the
    # proactive screenshot survives iff nothing was dropped.
    assert kept == [proactive, plugin, user][dropped:]


def test_turn_image_budget_keeps_a_lone_oversized_image() -> None:
    """Bounds ACCUMULATION, not one image.

    A single frame already passed its own per-image limit upstream. Dropping
    it would leave a message whose visual content silently vanished, which is
    worse than letting the provider judge one oversized attachment.
    """
    from main_logic.proactive_delivery import (
        TURN_ATTACHED_IMAGE_MAX_TOTAL_BYTES,
        trim_images_to_turn_budget,
    )

    lone = _b64_of_size(TURN_ATTACHED_IMAGE_MAX_TOTAL_BYTES * 2, "x")

    kept, dropped = trim_images_to_turn_budget([lone])

    assert kept == [lone]
    assert dropped == 0


def test_turn_image_budget_leaves_a_fitting_turn_untouched() -> None:
    from main_logic.proactive_delivery import (
        TURN_ATTACHED_IMAGE_MAX_TOTAL_BYTES,
        trim_images_to_turn_budget,
    )

    third = TURN_ATTACHED_IMAGE_MAX_TOTAL_BYTES // 4
    images = [_b64_of_size(third, c) for c in "abc"]

    kept, dropped = trim_images_to_turn_budget(images)

    assert kept == images
    assert dropped == 0


def test_per_source_quotas_alone_can_exceed_the_request_ceiling() -> None:
    """The reason the ceiling exists at all.

    If someone later "simplifies" by deleting the ceiling and trusting the
    per-source quotas, this is the arithmetic that makes that wrong. Asserted
    against the constants themselves so raising a quota re-breaks it.
    """
    from main_logic.proactive_delivery import (
        PLUGIN_PENDING_IMAGE_MAX_BYTES,
        TURN_ATTACHED_IMAGE_MAX_TOTAL_BYTES,
        USER_PENDING_IMAGE_MAX_BYTES,
    )

    assert (
        USER_PENDING_IMAGE_MAX_BYTES + PLUGIN_PENDING_IMAGE_MAX_BYTES
        > TURN_ATTACHED_IMAGE_MAX_TOTAL_BYTES
    )


# ---------------------------------------------------------------------------
# HUD toasts render text and nothing else.
#
# The gate above the delivery block widened from `if text:` to
# `if text or deferred_callback_images:` so image-only pushes could reach the
# LLM channel. The HUD branch shares that gate but has no image sink, so an
# image-only push started emitting an agent_notification with text "".
# ---------------------------------------------------------------------------


def _notifications(manager) -> list:
    return [
        call.args[0]
        for call in manager.websocket.send_json.call_args_list
        if isinstance(call.args[0], dict)
        and call.args[0].get("type") == "agent_notification"
    ]


async def _push_image_only_with_hud(monkeypatch, *, text: str):
    from app import main_server

    manager = _manager()
    manager.websocket = MagicMock()
    manager.websocket.send_json = AsyncMock()
    monkeypatch.setattr(
        "app.main_server.character_runtime._get_session_manager",
        lambda _name: manager,
    )
    monkeypatch.setattr(
        "app.main_server.character_runtime._is_websocket_connected",
        lambda _ws: True,
    )
    monkeypatch.setattr(
        "app.main_server.character_runtime._fetch_plugin_image_base64",
        AsyncMock(return_value=base64.b64encode(b"img").decode("ascii")),
    )

    await main_server._handle_agent_event({
        "event_type": "proactive_message",
        "lanlan_name": "Test",
        "text": text,
        "channel": "plugin:demo",
        "delivery_mode": "proactive",
        "ai_behavior": "respond",
        "visibility": ["hud"],
        "media_parts": [{"type": "image", "url": _IMAGE_URL, "mime": "image/jpeg"}],
    })
    return manager


@pytest.mark.asyncio
async def test_image_only_push_does_not_emit_an_empty_hud_toast(monkeypatch) -> None:
    manager = await _push_image_only_with_hud(monkeypatch, text="")

    assert _notifications(manager) == [], "HUD has no image sink; an empty toast carries nothing"
    # The image is NOT lost -- it rides the proactive callback, which is the
    # whole reason the shared gate was widened. Suppressing the toast must not
    # suppress delivery.
    manager.submit_proactive_callback.assert_called_once()
    assert manager.submit_proactive_callback.call_args.args[0]["media_images"]


@pytest.mark.asyncio
async def test_push_with_text_and_images_still_emits_its_hud_toast(monkeypatch) -> None:
    """Dual: the fix must key on empty text, not on "this push carried images"."""
    manager = await _push_image_only_with_hud(monkeypatch, text="有人送了礼物")

    notifs = _notifications(manager)
    assert len(notifs) == 1
    assert notifs[0]["text"] == "有人送了礼物"
    manager.submit_proactive_callback.assert_called_once()


@pytest.mark.asyncio
async def test_offline_turn_with_images_reaches_history_without_crashing():
    """Drive a real image-bearing turn all the way to the history append.

    Every other test here stops at the pending-image queues. Nothing covered
    the assembly that reads those queues, builds the HumanMessage and logs what
    it attached -- so a NameError in that stretch shipped green. This is the
    end-to-end floor: an ordinary Offline turn carrying an image must reach
    ``_conversation_history`` and must not raise on the way.
    """
    import time

    from main_logic.omni_offline_client._client import OmniOfflineClient

    client = OmniOfflineClient.__new__(OmniOfflineClient)
    client._pending_images = ["user-frame"]
    client._pending_plugin_images = ["plugin-frame"]
    client._conversation_history = []
    client._proactive_image_to_inject = "proactive-frame"
    client._proactive_image_staged_at = time.monotonic()
    client._proactive_image_history_len = 0
    client.vision_model = None
    client.model = "test-model"
    client._begin_reasoning_stream = lambda: None
    client.on_response_discarded = None
    client.on_status_message = None
    client.on_input_transcript = None
    client.on_text_delta = None
    client.on_thinking_active = None
    client.on_output_transcript = None
    client.on_response_done = None
    client.on_repetition_detected = None
    client._is_responding = False
    client._skip_next_response = False

    committed = {}

    async def fake_stream(*_args, **_kwargs):
        committed["history_len"] = len(client._conversation_history)
        return

    # 只跑到「装配完、进 history」为止：真正的 provider 调用在这之后。
    client._stream_from_llm = AsyncMock(side_effect=fake_stream)

    await client.stream_text("这是什么", on_turn_committed=lambda: committed.setdefault("marked", True))

    assert committed.get("marked") is True
    assert len(client._conversation_history) == 1
    content = client._conversation_history[0].content
    attached = [
        part["image_url"]["url"]
        for part in content
        if isinstance(part, dict) and part.get("type") == "image_url"
    ]
    # 三个来源的图都到齐了，并且没有在装配途中抛异常。
    assert any("proactive-frame" in url for url in attached)
    assert any("plugin-frame" in url for url in attached)
    assert any("user-frame" in url for url in attached)
    # 消费完就清空，与用户列表同一窗口。
    assert client._pending_images == []
    assert client._pending_plugin_images == []


@pytest.mark.asyncio
async def test_cancelled_budget_fitting_gives_both_image_queues_back():
    """Ownership was taken before an await; a death there must give it back.

    The user's attachments are dequeued atomically and the plugin list is
    cleared at read time, so between that point and the history append nothing
    else holds those bytes. Budget fitting can suspend there for threaded
    compression -- a teardown landing on it would otherwise erase both sets
    from every future turn.
    """
    import time

    from main_logic.omni_offline_client._client import OmniOfflineClient

    client = OmniOfflineClient.__new__(OmniOfflineClient)
    client._pending_images = ["user-frame-a", "user-frame-b"]
    client._pending_plugin_images = ["plugin-frame"]
    client._conversation_history = []
    client._proactive_image_to_inject = None
    client._proactive_image_staged_at = 0.0
    client._proactive_image_history_len = 0
    client.vision_model = None
    client.model = "test-model"
    client._begin_reasoning_stream = lambda: None
    for hook in (
        "on_response_discarded", "on_status_message", "on_input_transcript",
        "on_text_delta", "on_thinking_active", "on_output_transcript",
        "on_response_done", "on_repetition_detected",
    ):
        setattr(client, hook, None)
    client._is_responding = False
    client._skip_next_response = False

    async def cancelled_fit(*_args, **_kwargs):
        raise asyncio.CancelledError()

    with patch(
        "main_logic.omni_offline_client._streaming.fit_images_to_turn_budget",
        cancelled_fit,
    ):
        with pytest.raises(asyncio.CancelledError):
            await client.stream_text("看看这个")

    # 两条队列都完整还了回来，顺序也没乱。
    assert client._pending_images == ["user-frame-a", "user-frame-b"]
    assert client._pending_plugin_images == ["plugin-frame"]
    # 这一轮什么都没提交。
    assert client._conversation_history == []


# ---------------------------------------------------------------------------
# The numbers PLUGIN_DEVELOPMENT_GUIDE.md hands plugin authors.
#
# Every ceiling below had zero tests: the inline payload cap, the four upload
# ceilings, and the double-carry that makes the inline cap mean roughly half of
# what it says. A plugin author reads those figures out of the guide and sizes
# their screenshots against them, so a constant raised without touching the
# prose is not a doc nit -- it is a contract the host stopped honouring while
# still advertising it. Pin both halves and match them against each other.
# ---------------------------------------------------------------------------


def _guide_text() -> str:
    from pathlib import Path

    guide = (
        Path(__file__).resolve().parents[2] / "plugin" / "PLUGIN_DEVELOPMENT_GUIDE.md"
    )
    return guide.read_text(encoding="utf-8")


def test_message_plane_payload_ceiling_is_pinned_and_documented() -> None:
    """The inline push cap is 512 KiB, and the guide must print that number.

    The guide used to say "256 KB", which reads as 256000 and is not what the
    ingest server measures against. Pin the shipped default and require the
    guide to carry the exact byte count, so moving the cap without touching the
    prose fails here rather than in a plugin author's head.

    512 rather than 256 because the constant budgets ENCODED bytes: an inline
    image travels base64 at 4/3 of its raw size, so the 256 KiB picture the
    guide tells authors they may push needs 341 KiB of envelope to fit. This
    literal is also what keeps the derived figure in the sibling test below
    honest -- without it, moving the constant would move the assertion with it.
    """
    from plugin.settings import MESSAGE_PLANE_PAYLOAD_MAX_BYTES

    assert MESSAGE_PLANE_PAYLOAD_MAX_BYTES == 512 * 1024

    text = _guide_text()
    assert (
        f"`MESSAGE_PLANE_PAYLOAD_MAX_BYTES` = {MESSAGE_PLANE_PAYLOAD_MAX_BYTES}"
        in text
    )
    # KiB, spelled out, because the whole point of the correction is that the
    # unit was wrong and not just the formatting.
    assert f"（{MESSAGE_PLANE_PAYLOAD_MAX_BYTES // 1024} **KiB**" in text


def test_every_push_reason_the_sdk_can_return_is_documented() -> None:
    """The guide's `reason` list must be the code's, derived and not retyped.

    ``payload_too_large`` shipped while the guide's list still named three
    values, which is the failure mode a hand-written list always has: nobody
    edits prose when they add a branch. So derive the set from the function
    itself. A checklist copied out of today's code is the same bug one release
    later; the literal below is only a tripwire that makes "a reason moved"
    fail loudly enough that someone goes and reads the prose.

    Two shapes carry a reason out of ``push_message``: the ``"reason"`` value
    of a returned dict, and the ``primary_failure_reason`` local that feeds
    those returns (the conditional form hides two constants inside one
    expression, which a regex over ``"reason": "..."`` would miss entirely --
    that is why this walks the AST).

    Mutation A: delete `payload_too_large` from the guide's reason list -- red
    on the coverage loop.
    Mutation B: rename the constant in the SDK, e.g. ``payload_too_large`` ->
    ``payload_oversize``, without touching the guide -- red on both.
    """
    import ast
    import inspect
    import textwrap

    from plugin.core.context import PluginContext

    tree = ast.parse(textwrap.dedent(inspect.getsource(PluginContext.push_message)))

    def _string_constants(node) -> set:
        return {
            sub.value
            for sub in ast.walk(node)
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str)
        }

    reasons: set = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and key.value == "reason":
                    reasons |= _string_constants(value)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Name)
                    and target.id == "primary_failure_reason"
                ):
                    reasons |= _string_constants(node.value)

    assert reasons == {
        "backpressure",
        "transport_error",
        "transport_unavailable",
        "payload_too_large",
    }, "a reason was added, removed or renamed -- fix the guide, then this literal"

    text = _guide_text()
    for reason in sorted(reasons):
        assert f"`{reason}`" in text, f"push_message can return {reason}, undocumented"


def test_image_upload_ceilings_are_pinned_and_documented() -> None:
    """The four upload ceilings, pinned as literals and matched to the guide.

    These are the limits that turn into a raised exception in a plugin's face
    (32 MiB source, 16 MP source, 8 MiB normalized output) plus the silent
    downscale edge. All four were undocumented and untested; both gaps are
    closed here in one place so neither side can drift alone.
    """
    from plugin.sdk.shared.core.images import (
        MAX_IMAGE_EDGE,
        MAX_SOURCE_IMAGE_BYTES,
        MAX_SOURCE_IMAGE_PIXELS,
        MAX_UPLOADED_IMAGE_BYTES,
    )

    assert MAX_IMAGE_EDGE == 2048
    assert MAX_UPLOADED_IMAGE_BYTES == 8 * 1024 * 1024
    assert MAX_SOURCE_IMAGE_BYTES == 32 * 1024 * 1024
    assert MAX_SOURCE_IMAGE_PIXELS == 16 * 1024 * 1024

    text = _guide_text()
    assert f"`MAX_IMAGE_EDGE` = {MAX_IMAGE_EDGE}" in text
    assert f"`MAX_UPLOADED_IMAGE_BYTES` = {MAX_UPLOADED_IMAGE_BYTES}" in text
    assert f"`MAX_SOURCE_IMAGE_BYTES` = {MAX_SOURCE_IMAGE_BYTES}" in text
    assert f"`MAX_SOURCE_IMAGE_PIXELS` = {MAX_SOURCE_IMAGE_PIXELS}" in text


def _push_one_inline_image(monkeypatch, raw_len: int):
    """Push one inline image through the real writer; return (result, payload).

    ``payload`` is None when the push was refused. No ZMQ endpoint in a unit
    test, so this falls through to the legacy host queue -- which hands back
    the very same ``_build_wire_payload`` envelope the ingest server would have
    measured, which is the whole point of measuring here rather than
    hand-rolling a dict.
    """
    import logging
    import queue
    from pathlib import Path

    from plugin.core import context as context_module
    from plugin.core.context import PluginContext

    monkeypatch.setattr(context_module, "zmq", None)

    message_queue: "queue.Queue" = queue.Queue()
    ctx = PluginContext(
        plugin_id="demo_plugin",
        config_path=Path("plugin.toml"),
        logger=logging.getLogger("test"),
        status_queue=queue.Queue(),
        message_queue=message_queue,
    )
    result = ctx.push_message(
        visibility=["chat"],
        ai_behavior="respond",
        parts=[
            {"type": "text", "text": "look"},
            {"type": "image", "data": bytes(raw_len), "mime": "image/png"},
        ],
    )
    if not result["submitted"]:
        assert message_queue.empty(), "a refused push must not reach the transport"
        return result, None
    return result, message_queue.get_nowait()


def test_an_inline_image_rides_the_wire_envelope_once(monkeypatch) -> None:
    """One inline image is carried base64, and NOT a second time raw.

    This test used to assert the opposite, and the guide used to print ~110 KiB
    as the effective ceiling because of it: the envelope carried the picture
    twice -- base64 in ``parts[].binary_base64`` (+33%) and the same bytes raw
    in the legacy ``binary_data`` field -- costing ~2.34x the source, so an
    image at half the advertised cap already did not fit.

    Both halves of the fix are pinned here, because either one alone leaves the
    guide lying. Dropping the duplicate takes the envelope from ~2.34x to ~4/3,
    and raising the cap to 512 KiB is what makes 4/3 of a documented 256 KiB
    image actually fit. The measured figures the guide now prints are asserted
    against the real writer rather than restated.

    Mutation A: restore the raw ``binary_data`` duplicate in
    ``_build_wire_payload`` -- the ratio and the acceptance case both fail.
    Mutation B: put ``MESSAGE_PLANE_PAYLOAD_MAX_BYTES`` back to 256*1024 --
    the acceptance case and the bracketed ceiling both fail.
    """
    import ormsgpack

    from plugin.settings import MESSAGE_PLANE_PAYLOAD_MAX_BYTES

    raw_len = 256 * 1024
    result, payload = _push_one_inline_image(monkeypatch, raw_len)
    assert result["submitted"] is True

    assert base64.b64decode(payload["parts"][1]["binary_base64"]) == bytes(raw_len)
    # The legacy field is the thing that used to double the bill. It is only
    # populated now for the one shape where it is the sole carrier (caller
    # passed binary_data= BESIDE parts=), which is not this shape.
    assert payload["binary_data"] is None

    packed = len(ormsgpack.packb(payload))
    # base64 is 4/3; the envelope adds a few hundred bytes on top of that and
    # nothing else. The upper bound is what would fail if the duplicate came
    # back -- 2.34x is nowhere near 1.34x.
    assert 4 / 3 <= packed / raw_len < 1.34
    # The acceptance case the guide promises in so many words: a 256 KiB inline
    # image fits the shipped default. Under either half of the old world it did
    # not.
    assert packed <= MESSAGE_PLANE_PAYLOAD_MAX_BYTES

    text = _guide_text()
    # The effective raw-bytes ceiling, derived from the constant the sibling
    # test pins to a literal, so the two cannot drift apart quietly.
    effective_kib = MESSAGE_PLANE_PAYLOAD_MAX_BYTES * 3 // 4 // 1024
    assert effective_kib == 384
    assert f"**约 {effective_kib} KiB**" in text
    # The two concrete measurements the guide quotes, checked against the
    # writer instead of trusted. A doc number nobody measures is how the
    # 110 KiB line survived a rewrite of the thing it described.
    assert f"{packed / 1024:.1f} KiB" == "341.8 KiB"
    assert "341.8 KiB" in text
    assert "383.6 KiB" in text
    # Bracket that last figure rather than bisect for it: at 383 KiB the packed
    # envelope is still under the cap and at 385 KiB it is over, which is only
    # true if the cap AND the single carry are both in place.
    #
    # The under case is measured on the ENVELOPE, because a push that succeeds
    # hands the wire payload back and the byte count is the thing the guide
    # quotes.
    #
    # The over case is measured on push_message()'s VERDICT instead, and the
    # reason it changed is worth recording. It used to read the envelope too,
    # justified by "the payload_too_large gate sits on the ZMQ branch and this
    # fixture has no ZMQ, so a verdict would only test which transport happened
    # to be available". That justification died when the gate was added to the
    # legacy queue exit as well (CodeRabbit found it missing there): the guard
    # is now on all three exits, so an oversized push is refused everywhere and
    # there is no envelope left to read back -- packb(None) is one byte, which
    # is exactly how this surfaced.
    #
    # Asserting the refusal is also the stronger claim. The bracket exists to
    # pin the ceiling the guide prints, and what a plugin author actually meets
    # at 385 KiB is a rejection, not a byte count they cannot observe.
    _, under = _push_one_inline_image(monkeypatch, 383 * 1024)
    assert len(ormsgpack.packb(under)) <= MESSAGE_PLANE_PAYLOAD_MAX_BYTES
    over_result, over = _push_one_inline_image(monkeypatch, 385 * 1024)
    assert over is None
    assert over_result == {
        "ok": False,
        "submitted": False,
        "reason": "payload_too_large",
    }
    # 110 KiB is allowed to survive, but only as history. Banning the number
    # would be the wrong pin -- the paragraph explaining why the effective
    # ceiling used to sit below the cap is exactly what makes an author trust
    # the new figure instead of re-deriving it. So pin the FRAMING: it appears
    # once, in a paragraph that says it used to be that way and names the
    # 2.34x double carry as the reason.
    assert text.count("110 KiB") == 1
    # Blockquote paragraphs are separated by a bare "> " line, not a blank one.
    history = next(p for p in text.split("\n>\n") if "110 KiB" in p)
    assert "曾经" in history and "2.34" in history
    assert "384 KiB" not in history, (
        "the current ceiling must not be stated inside the historical paragraph"
    )


# ---------------------------------------------------------------------------
# The proactive text turn (prompt_ephemeral) and the model/display fork
#
# Two separate holes, one theme: an image reached a model at whatever
# resolution it happened to arrive with.
#
#   * prompt_ephemeral attached every entry of ``images`` verbatim as a
#     data: URL. It was the only image-bearing model call in the repo with no
#     budget ladder at all -- the same provider, the same per-request ceiling
#     as a user turn, but nothing between the plugin's bytes and the wire.
#   * _resolve_plugin_model_image re-encoded INLINE bytes and passed URL bytes
#     through untouched. The SDK bounds an upload's long edge at 2048, so the
#     measured plugin frame is 2048x1536 / ~49 KiB: far under any byte budget,
#     and therefore never trimmed by one.
# ---------------------------------------------------------------------------


def _gradient_rgb(width: int, height: int) -> Image.Image:
    """A real, non-degenerate picture of exactly this size.

    Three different ramps, not flat colour: a flat image compresses to almost
    nothing at ANY size, so a byte-budget assertion built on one would be
    measuring rounding noise rather than the picture. Built from
    ``linear_gradient`` + one resize so a 2048x1536 fixture costs microseconds
    instead of two million python-level pixel writes.
    """
    ramp = Image.linear_gradient("L").resize((width, height))
    return Image.merge(
        "RGB", (ramp, ramp.rotate(180), ramp.transpose(Image.FLIP_LEFT_RIGHT))
    )


def _jpeg_b64(width: int, height: int) -> str:
    buffer = BytesIO()
    _gradient_rgb(width, height).save(buffer, format="JPEG", quality=92)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _png_b64(width: int, height: int) -> str:
    """The same picture as a png, for the inline (re-encoded) branch."""
    buffer = BytesIO()
    _gradient_rgb(width, height).save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _image_size(b64: str) -> tuple[int, int]:
    with Image.open(BytesIO(base64.b64decode(b64))) as image:
        return image.size


def _image_format(b64: str) -> str:
    with Image.open(BytesIO(base64.b64decode(b64))) as image:
        return str(image.format or "").upper()


def _offline_client_for_ephemeral():
    """The minimum needed to drive prompt_ephemeral to its finally block.

    Modelled on the harness in test_proactive_vision_screenshot_staging.py.
    ``vision_model`` is left empty so the model switch stays out of the path --
    these tests are about what gets ATTACHED, not about which model receives it.
    """
    from types import SimpleNamespace
    from unittest.mock import AsyncMock as _AsyncMock, MagicMock as _MagicMock
    from main_logic.omni_offline_client._client import OmniOfflineClient
    from utils.llm_client import SystemMessage

    client = OmniOfflineClient.__new__(OmniOfflineClient)
    client._conversation_history = [SystemMessage(content="sys")]
    client._pending_images = []
    client._pending_plugin_images = []
    client._proactive_image_to_inject = None
    client._proactive_image_staged_at = 0.0
    client._proactive_image_history_len = 0
    client.model = "m"
    client.vision_model = ""
    client._is_responding = False
    client._prefix_buffer_size = 0
    client.master_name = "M"
    client.lanlan_name = "L"
    client.on_text_delta = _AsyncMock()
    client.on_status_message = _AsyncMock()
    client.on_response_done = _AsyncMock()
    client.on_proactive_done = _AsyncMock()
    client._begin_reasoning_stream = _MagicMock(return_value=1)
    client._notify_reasoning_done = _AsyncMock()

    sent: list = []

    async def _fake_stream(messages):
        sent.append(messages)
        yield SimpleNamespace(content="看到了喵~")

    client._astream_visible_with_tools = _fake_stream
    return client, sent


def _attached_images(messages: list) -> list[str]:
    """The base64 payloads of every image block on the ephemeral message."""
    content = messages[-1].content
    if not isinstance(content, list):
        return []
    return [
        block["image_url"]["url"].split("base64,", 1)[1]
        for block in content
        if block.get("type") == "image_url"
    ]


@pytest.mark.asyncio
async def test_proactive_prompt_ephemeral_images_go_through_the_budget_ladder(
    monkeypatch,
) -> None:
    """The proactive turn now obeys the same ladder as a user turn.

    Mutation: replace the ``fit_images_to_turn_budget`` call in
    ``prompt_ephemeral`` with ``_budget_images = images`` (what the code did
    before). Every assertion below fails -- five 1600x1200 frames reach the
    model untouched and the user is told nothing.
    """
    from main_logic.omni_offline_client import _lifecycle
    from utils.screenshot_utils import COMPRESS_TARGET_HEIGHT, MODEL_IMAGE_MAX_WIDTH

    client, sent = _offline_client_for_ephemeral()
    # The LAST frame has a different aspect ratio on purpose. Every frame comes
    # out of rung 0 at 720 high, but only this one comes out 1280 wide, so the
    # survivor's shape says WHICH input survived -- and the ladder's rule is
    # that the newest frame, the one the instruction is about, is the one it
    # must never drop. With five identical fixtures that would be unobservable.
    oversized = [_jpeg_b64(1600, 1200) for _ in range(4)] + [_jpeg_b64(1600, 900)]
    # Budget squeezed to roughly one normalized frame and a half, so the ladder
    # has to walk all the way down: sample five to three, re-compress, then
    # drop from the front. Derived from the MEASURED normalized size, because a
    # hardcoded figure drifts the moment the profile or the codec moves.
    from utils.screenshot_utils import normalize_image_for_model

    one_normalized = len(base64.b64decode(normalize_image_for_model(oversized[0])))
    monkeypatch.setattr(
        _lifecycle, "TURN_ATTACHED_IMAGE_MAX_TOTAL_BYTES", int(one_normalized * 1.5)
    )

    committed = await client.prompt_ephemeral("======主动搭话======", images=oversized)

    assert committed is True
    attached = _attached_images(sent[-1])
    # Something was actually dropped, so the ladder ran to its last rung.
    assert 0 < len(attached) < len(oversized)
    # And what survived is at the model profile, not at 1600x1200.
    for payload in attached:
        width, height = _image_size(payload)
        assert width <= MODEL_IMAGE_MAX_WIDTH
        assert height <= COMPRESS_TARGET_HEIGHT
    # The last frame is the one the instruction is about; the ladder keeps it.
    # 1280x720 is reachable only from the 1600x900 input (the 1600x1200 ones
    # normalize to 960x720), so this identifies the survivor rather than merely
    # re-checking the bound above.
    assert _image_size(attached[-1]) == (MODEL_IMAGE_MAX_WIDTH, COMPRESS_TARGET_HEIGHT)


@pytest.mark.asyncio
async def test_proactive_prompt_ephemeral_toasts_only_when_frames_left_the_turn(
    monkeypatch,
) -> None:
    """The gate is "a frame is gone", not "the ladder did something".

    Both directions, because a one-sided assertion here is worth little:
    a turn that silently loses a frame is as bad as one that toasts about
    routine housekeeping. The loud half uses three frames on purpose -- that is
    ``TURN_IMAGE_SAMPLE_KEEP``, so the sample rung cannot fire and this stays a
    test of the drop rung specifically; the sampling half of the same gate is
    covered in test_proactive_delivery.py where the ladder itself is under
    test.

    Mutation A: gate on ``if _budget_notice`` instead of
    ``_budget_notice.get("user_visible")`` -- the quiet case fails.
    Mutation B: drop the ``on_status_message`` call entirely -- the loud case
    fails.
    """
    from main_logic.omni_offline_client import _lifecycle
    from utils.screenshot_utils import COMPRESS_TARGET_HEIGHT, MODEL_IMAGE_MAX_WIDTH
    from utils.screenshot_utils import normalize_image_for_model

    # ── Quiet: normalization alone. One oversized frame, budget untouched, so
    #    rung 0 rewrites it and nothing is lost from the reader's point of view.
    client, sent = _offline_client_for_ephemeral()
    only = _jpeg_b64(1600, 1200)

    await client.prompt_ephemeral("======主动搭话======", images=[only])

    attached = _attached_images(sent[-1])
    assert len(attached) == 1
    width, height = _image_size(attached[0])
    # It really was rewritten -- otherwise "no toast" would prove nothing.
    assert (width, height) != (1600, 1200)
    assert width <= MODEL_IMAGE_MAX_WIDTH and height <= COMPRESS_TARGET_HEIGHT
    client.on_status_message.assert_not_awaited()

    # ── Loud: whole frames dropped. Same call, budget squeezed.
    client, sent = _offline_client_for_ephemeral()
    frames = [_jpeg_b64(1600, 1200) for _ in range(3)]
    one_normalized = len(base64.b64decode(normalize_image_for_model(frames[0])))
    monkeypatch.setattr(
        _lifecycle, "TURN_ATTACHED_IMAGE_MAX_TOTAL_BYTES", int(one_normalized * 1.5)
    )

    await client.prompt_ephemeral("======主动搭话======", images=frames)

    client.on_status_message.assert_awaited_once()
    import json as _json

    payload = _json.loads(client.on_status_message.await_args.args[0])
    assert payload["code"] == "TURN_IMAGES_TRIMMED"
    assert payload["details"]["dropped"] > 0
    assert payload["details"]["user_visible"] is True
    # The frontend interpolates these two into the toast copy, so an empty
    # notice would render "images trimmed from {{original_count}}".
    assert payload["details"]["original_count"] == 3
    assert payload["details"]["final_count"] == len(_attached_images(sent[-1]))


@pytest.mark.asyncio
async def test_one_inline_image_reaches_chat_full_size_and_the_model_at_720p(
    monkeypatch,
) -> None:
    """The fork, on the inline branch: same part, two deliberately different copies.

    Mutation A (model side): put ``_normalize_inline_image_to_jpeg_base64``
    back in ``_resolve_plugin_model_image`` -- the model image comes back
    2048x1536 and the size assertion fails.
    Mutation B (chat side): make ``_build_plugin_chat_blocks`` re-encode the
    inline payload through the model normalizer -- the chat block is no longer
    the plugin's own bytes and the identity assertion fails.
    """
    from app import main_server
    from utils.screenshot_utils import COMPRESS_TARGET_HEIGHT, MODEL_IMAGE_MAX_WIDTH

    manager = _manager()
    # 2048x1536 is the MEASURED shape of an SDK-normalized plugin frame: the
    # SDK bounds the long edge at MAX_IMAGE_EDGE=2048, and nothing downstream
    # bounded the other one.
    original = _png_b64(2048, 1536)
    monkeypatch.setattr(
        "app.main_server.character_runtime._get_session_manager",
        lambda _name: manager,
    )

    await main_server._handle_agent_event({
        "event_type": "proactive_message",
        "lanlan_name": "Test",
        "text": "",
        "channel": "plugin:demo",
        "delivery_mode": "passive",
        "ai_behavior": "read",
        "visibility": ["chat"],
        "parts": [
            {"type": "image", "binary_base64": original, "mime": "image/png"}
        ],
    })

    # ── Chat: the plugin's own bytes, byte for byte, at the uploaded size.
    manager.render_chat_blocks.assert_awaited_once()
    blocks = manager.render_chat_blocks.await_args.args[0]
    images = [block for block in blocks if block["type"] == "image"]
    assert len(images) == 1
    assert images[0]["url"] == f"data:image/png;base64,{original}"
    assert _image_size(images[0]["url"].split("base64,", 1)[1]) == (2048, 1536)

    # ── Model: jpeg, inside the profile.
    manager.session.stream_image.assert_awaited_once()
    model_b64 = manager.session.stream_image.await_args.args[0]
    assert _image_format(model_b64) == "JPEG"
    width, height = _image_size(model_b64)
    assert width <= MODEL_IMAGE_MAX_WIDTH
    assert height <= COMPRESS_TARGET_HEIGHT
    # Stated as an asymmetry rather than as two independent facts: the point is
    # that ONE part produced two different resolutions on purpose.
    assert (width, height) != (2048, 1536)


@pytest.mark.asyncio
async def test_one_url_image_reaches_chat_by_reference_and_the_model_at_720p(
    monkeypatch,
) -> None:
    """The same fork on the URL branch, which is where the hole actually was.

    The inline branch always re-encoded; the URL branch returned the fetched
    bytes verbatim, and those bytes are the measured 2048x1536 / ~49 KiB frame
    that no byte budget ever objects to.

    Mutation A (model side): drop the ``normalize_image_for_model`` call after
    ``_fetch_plugin_image_base64`` -- the model gets 2048x1536 back.
    Mutation B (chat side): render the fetched bytes inline instead of the
    ``/media/<id>`` path -- the chat block stops being a reference and the URL
    assertion fails.
    """
    from app import main_server
    from utils.screenshot_utils import COMPRESS_TARGET_HEIGHT, MODEL_IMAGE_MAX_WIDTH

    manager = _manager()
    fetched = _jpeg_b64(2048, 1536)
    monkeypatch.setattr(
        "app.main_server.character_runtime._get_session_manager",
        lambda _name: manager,
    )
    # Mocked at the FETCH, deliberately: the resolver -- not the transfer -- is
    # what has to own the profile guarantee, so bypassing the transfer must not
    # bypass the bound.
    monkeypatch.setattr(
        "app.main_server.character_runtime._fetch_plugin_image_base64",
        AsyncMock(return_value=fetched),
    )

    await main_server._handle_agent_event({
        "event_type": "proactive_message",
        "lanlan_name": "Test",
        "text": "",
        "channel": "plugin:demo",
        "delivery_mode": "passive",
        "ai_behavior": "read",
        "visibility": ["chat"],
        "parts": [{"type": "image", "url": _IMAGE_URL, "mime": "image/jpeg"}],
    })

    # ── Chat: a reference the browser fetches itself, so the reader gets the
    #    uploaded resolution and those bytes never touch a model request.
    manager.render_chat_blocks.assert_awaited_once()
    blocks = manager.render_chat_blocks.await_args.args[0]
    assert [block for block in blocks if block["type"] == "image"] == [
        {"type": "image", "url": _browser_url(_IMAGE_URL)}
    ]

    # ── Model: the same picture, bounded.
    manager.session.stream_image.assert_awaited_once()
    model_b64 = manager.session.stream_image.await_args.args[0]
    assert model_b64 != fetched, "the URL branch used to pass bytes through"
    assert _image_format(model_b64) == "JPEG"
    width, height = _image_size(model_b64)
    assert width <= MODEL_IMAGE_MAX_WIDTH
    assert height <= COMPRESS_TARGET_HEIGHT


@pytest.mark.asyncio
async def test_routine_normalization_is_logged_below_warning(monkeypatch) -> None:
    """Log level follows the same "did anything actually go missing" question.

    rung 0 is unconditional, so an ordinary attached photo produces a notice on
    essentially every turn that carries one. Emitting all of them at WARNING
    makes "the picture got smaller" and "whole frames never reached the model"
    indistinguishable in the log, and the second one is what someone greps for
    at 2am (Codex).

    The module logger is swapped rather than using caplog: this project's
    loggers do not propagate into the stdlib root handler, so caplog collects
    nothing here and the test would pass by observing an empty list.

    Both directions asserted -- a one-sided version would pass just as happily
    against a branch that logged everything at info, burying the case that
    matters.
    """
    from main_logic.omni_offline_client import _lifecycle
    from utils.screenshot_utils import normalize_image_for_model

    class _Recorder:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        def _make(self, level):
            def _log(message, *args, **kwargs):
                self.calls.append((level, str(message)))
            return _log

        def __getattr__(self, name):
            return self._make(name)

    def _budget_levels(rec):
        return [lvl for lvl, msg in rec.calls if "images fitted for the" in msg]

    # Quiet: one oversized frame, budget untouched. Normalization only.
    recorder = _Recorder()
    monkeypatch.setattr(_lifecycle, "logger", recorder)
    client, _sent = _offline_client_for_ephemeral()
    await client.prompt_ephemeral("======主动搭话======", images=[_jpeg_b64(1600, 1200)])

    assert _budget_levels(recorder) == ["info"], (
        f"routine normalization should log once at info, got {_budget_levels(recorder)}"
    )

    # Loud: whole frames dropped.
    recorder = _Recorder()
    monkeypatch.setattr(_lifecycle, "logger", recorder)
    frames = [_jpeg_b64(1600, 1200) for _ in range(3)]
    one = len(base64.b64decode(normalize_image_for_model(frames[0])))
    monkeypatch.setattr(_lifecycle, "TURN_ATTACHED_IMAGE_MAX_TOTAL_BYTES", int(one * 1.5))
    client, _sent = _offline_client_for_ephemeral()
    await client.prompt_ephemeral("======主动搭话======", images=frames)

    assert _budget_levels(recorder) == ["warning"], (
        f"losing whole frames must stay at warning, got {_budget_levels(recorder)}"
    )


def test_every_budget_notice_log_picks_its_level_from_user_visible() -> None:
    """Both notice call sites must share the level rule, not just the tested one.

    The behavioural test above drives prompt_ephemeral (_lifecycle.py). The
    other call site is stream_text (_streaming.py), whose fixture is far
    heavier, and a mutation that reverts ONLY that one would otherwise sail
    through green -- verified: flipping _streaming.py back to an unconditional
    logger.warning failed nothing.

    So this pins the shape at both sites. It is deliberately a source
    assertion and deliberately narrow: it does not claim the logging works,
    only that neither site has drifted back to a hardcoded level while the
    other kept the rule.
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    sites = [
        root / "main_logic" / "omni_offline_client" / "_streaming.py",
        root / "main_logic" / "omni_offline_client" / "_lifecycle.py",
    ]
    for path in sites:
        text = path.read_text(encoding="utf-8")
        assert "images fitted for the" in text, f"{path.name}: notice log vanished"
        # The level must be chosen from the notice, never hardcoded at the call.
        assert re.search(
            r"_budget_log\s*=\s*\(\s*logger\.warning\s*if\s*_budget_notice\.get\(\s*[\"']user_visible[\"']\s*\)\s*else\s*logger\.info\s*\)",
            text,
        ), f"{path.name}: budget notice no longer picks its level from user_visible"
        assert not re.search(
            r"logger\.warning\(\s*\n\s*[\"']((prompt_ephemeral )?[Tt]urn )?[Ii]mages fitted", text
        ), f"{path.name}: budget notice is hardcoded to warning again"


@pytest.mark.asyncio
async def test_trim_notice_waits_for_the_proactive_turn_to_actually_speak(
    monkeypatch,
) -> None:
    """No visible output means no trim notice, however much was trimmed.

    The notice used to fire as soon as the ladder ran, i.e. before the LLM was
    even called. A proactive turn can then be cancelled, exhaust its retries, or
    come back empty -- all of which prompt_ephemeral deliberately keeps silent,
    because the user never asked for this turn and never knew it was coming.
    The result was a lone "images were adjusted" toast attached to nothing: no
    reply on screen, no way to make sense of it. Worse than saying nothing
    (Codex).

    Staging it until the first emitted text is what makes the earlier rationale
    true again -- that rationale rests on "this turn happened", which is only
    established at that moment.

    The empty stream here stands in for the whole family: cancelled, retried to
    exhaustion, or answered with nothing. They differ in how they end, not in
    what the user sees, which is nothing.
    """
    from main_logic.omni_offline_client import _lifecycle
    from utils.screenshot_utils import normalize_image_for_model

    client, _sent = _offline_client_for_ephemeral()

    async def _silent_stream(messages):
        # 流正常结束却一个可见字符都没有：emitted_any 保持 False。
        return
        yield  # pragma: no cover - makes this an async generator

    client._astream_visible_with_tools = _silent_stream

    frames = [_jpeg_b64(1600, 1200) for _ in range(3)]
    one = len(base64.b64decode(normalize_image_for_model(frames[0])))
    monkeypatch.setattr(
        _lifecycle, "TURN_ATTACHED_IMAGE_MAX_TOTAL_BYTES", int(one * 1.5)
    )

    await client.prompt_ephemeral("======主动搭话======", images=frames)

    # The ladder definitely dropped frames -- otherwise this proves nothing.
    trims = [
        call for call in client.on_status_message.await_args_list
        if "TURN_IMAGES_TRIMMED" in str(call)
    ]
    assert trims == [], (
        "a turn that never spoke must not report what it trimmed: "
        f"{trims}"
    )


@pytest.mark.asyncio
async def test_trim_notice_fires_once_across_a_multi_chunk_stream(monkeypatch) -> None:
    """Staging must clear on send, or every chunk re-fires the toast.

    The send sits inside the per-chunk emit branch, which is the only place
    that knows the turn has spoken. That branch runs once per delta, so the
    pending slot has to be cleared as it fires. The single-chunk fixture used
    by the sibling tests cannot see the difference -- verified: removing the
    clear failed nothing until this test existed.

    Ten chunks rather than two so an off-by-one in the clearing (say, clearing
    on the second delta) still shows up as a count, not as a coin flip.
    """
    from types import SimpleNamespace

    from main_logic.omni_offline_client import _lifecycle
    from utils.screenshot_utils import normalize_image_for_model

    client, _sent = _offline_client_for_ephemeral()

    async def _chatty_stream(messages):
        for index in range(10):
            yield SimpleNamespace(content=f"第{index}段喵~")

    client._astream_visible_with_tools = _chatty_stream

    frames = [_jpeg_b64(1600, 1200) for _ in range(3)]
    one = len(base64.b64decode(normalize_image_for_model(frames[0])))
    monkeypatch.setattr(
        _lifecycle, "TURN_ATTACHED_IMAGE_MAX_TOTAL_BYTES", int(one * 1.5)
    )

    await client.prompt_ephemeral("======主动搭话======", images=frames)

    trims = [
        call for call in client.on_status_message.await_args_list
        if "TURN_IMAGES_TRIMMED" in str(call)
    ]
    assert len(trims) == 1, (
        f"the trim notice must be sent exactly once per turn, got {len(trims)}"
    )
    # And it did arrive -- a clear that fired too early would also give len 0.
    assert client.on_text_delta.await_count == 10


@pytest.mark.asyncio
async def test_text_only_proactive_turn_completes_without_touching_image_state() -> None:
    """A proactive turn with no images at all must still finish cleanly.

    The staged-notice slot is read unconditionally at the first emitted delta,
    but it was being bound inside ``if images:``. A text-only proactive turn --
    by far the common case -- therefore raised UnboundLocalError at that read.

    The failure mode is worse than a crash in isolation: on_text_delta has
    ALREADY delivered the first chunk to the user by then, so the reader sees a
    reply while prompt_ephemeral falls into its failure branch and returns
    False. The proactive scheduler then treats a turn the user plainly saw as
    one that never happened.

    Both halves are asserted for that reason -- returning True is not enough if
    the text never arrived, and delivered text is not enough if the turn is
    still reported as failed.
    """
    from types import SimpleNamespace

    client, sent = _offline_client_for_ephemeral()

    async def _talky(messages):
        # 替掉夹具的 stream 就丢了它对 sent 的追加，这里补上，
        # 否则「LLM 被调用了」这条断言测的是夹具自己。
        sent.append(messages)
        yield SimpleNamespace(content="今天也想你了喵~")

    client._astream_visible_with_tools = _talky

    result = await client.prompt_ephemeral("======主动搭话======")

    assert client.on_text_delta.await_count == 1, "the turn must actually speak"
    assert result is not False, (
        "a text-only proactive turn delivered its text but was reported failed"
    )
    # No image state was involved, so no trim notice may appear either.
    trims = [
        call for call in client.on_status_message.await_args_list
        if "TURN_IMAGES_TRIMMED" in str(call)
    ]
    assert trims == []
    assert sent, "the LLM should have been called"


@pytest.mark.asyncio
async def test_trim_notice_survives_a_reply_shorter_than_the_prefix_buffer(
    monkeypatch,
) -> None:
    """A short reply is delivered by the flush path, which must notice too.

    ``_prefix_buffer_size`` holds back the opening characters so a leading
    "MasterName:" can be stripped before anything reaches the screen. A reply
    shorter than that threshold therefore never satisfies the per-chunk emit
    branch at all -- the whole thing is delivered by the end-of-stream flush.

    The staged notice originally lived inline on the chunk branch only, so
    those turns committed normally and silently dropped TURN_IMAGES_TRIMMED
    (Codex). Every fixture in this file sets ``_prefix_buffer_size = 0``, which
    is why 451 passing tests never went near it.
    """
    from types import SimpleNamespace

    from main_logic.omni_offline_client import _lifecycle
    from utils.screenshot_utils import normalize_image_for_model

    client, _sent = _offline_client_for_ephemeral()
    # Big enough that the whole reply below stays buffered to the very end.
    client._prefix_buffer_size = 64

    async def _short_reply(messages):
        yield SimpleNamespace(content="喵~")

    client._astream_visible_with_tools = _short_reply

    frames = [_jpeg_b64(1600, 1200) for _ in range(3)]
    one = len(base64.b64decode(normalize_image_for_model(frames[0])))
    monkeypatch.setattr(
        _lifecycle, "TURN_ATTACHED_IMAGE_MAX_TOTAL_BYTES", int(one * 1.5)
    )

    await client.prompt_ephemeral("======主动搭话======", images=frames)

    # The reply really did go out through the flush, not the chunk branch.
    assert client.on_text_delta.await_count == 1
    trims = [
        call for call in client.on_status_message.await_args_list
        if "TURN_IMAGES_TRIMMED" in str(call)
    ]
    assert len(trims) == 1, (
        f"a short reply still lost whole frames and must say so, got {len(trims)}"
    )
