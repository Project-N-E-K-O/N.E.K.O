from __future__ import annotations

import base64
import logging
import queue
from pathlib import Path

import pytest

from plugin.server.application.messages import query_service as module
from plugin.server.domain.errors import ServerDomainError
from plugin.settings import MESSAGE_QUEUE_MAX


@pytest.mark.plugin_unit
def test_query_messages_sync_clamps_max_count_to_configured_max(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def _fake_refresh(*, limit: int, timeout: float, ttl_seconds: float, force: bool) -> None:
        captured["limit"] = limit

    monkeypatch.setattr(module.state, "refresh_messages_cache_from_message_plane", _fake_refresh)
    monkeypatch.setattr(module.state, "iter_message_records_reverse", lambda: iter([]))

    module._query_messages_sync(plugin_id=None, max_count=MESSAGE_QUEUE_MAX + 9999, priority_min=None)

    assert captured["limit"] == MESSAGE_QUEUE_MAX


@pytest.mark.plugin_unit
def test_query_messages_sync_filters_by_plugin_and_priority(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        module.state,
        "refresh_messages_cache_from_message_plane",
        lambda **kwargs: None,
    )

    records = [
        {"plugin_id": "a", "priority": 1, "content": "low", "time": "2026-01-01T00:00:00Z"},
        {"plugin_id": "b", "priority": 9, "content": "other", "time": "2026-01-01T00:00:01Z"},
        {
            "plugin_id": "a",
            "priority": 8,
            "content": "ok",
            "message_type": "binary",
            "binary_data": b"abc",
            "metadata": {"k": "v"},
            "time": "2026-01-01T00:00:02Z",
            "message_id": 123,
        },
    ]
    monkeypatch.setattr(module.state, "iter_message_records_reverse", lambda: iter(records))

    payload = module._query_messages_sync(plugin_id="a", max_count=10, priority_min=5)

    assert len(payload) == 1
    item = payload[0]
    assert item["plugin_id"] == "a"
    assert item["priority"] == 8
    assert item["binary_data"] == "YWJj"
    assert item["metadata"] == {"k": "v"}
    assert item["message_id"] == "123"


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_get_plugin_messages_wraps_runtime_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    service = module.MessageQueryService()

    def _boom(*, plugin_id: str | None, max_count: int, priority_min: int | None):
        raise KeyError("x")

    monkeypatch.setattr(module, "_query_messages_sync", _boom)

    with pytest.raises(ServerDomainError) as exc_info:
        await service.get_plugin_messages(plugin_id="demo", max_count=5, priority_min=None)

    assert exc_info.value.code == "MESSAGE_QUERY_FAILED"


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_get_plugin_messages_keeps_serialized_binary_data(monkeypatch: pytest.MonkeyPatch) -> None:
    service = module.MessageQueryService()
    expected = [
        {
            "plugin_id": "a",
            "source": "",
            "description": "",
            "priority": 1,
            "message_type": "binary",
            "content": "ok",
            "binary_data": "YWJj",
            "binary_url": "",
            "metadata": {},
            "timestamp": "2026-01-01T00:00:00Z",
            "message_id": "m1",
        }
    ]

    def _fake_query(*, plugin_id: str | None, max_count: int, priority_min: int | None):
        return expected

    monkeypatch.setattr(module, "_query_messages_sync", _fake_query)

    payload = await service.get_plugin_messages(plugin_id="a", max_count=1, priority_min=None)
    assert payload["messages"][0]["binary_data"] == "YWJj"


# ---------------------------------------------------------------------------
# The inline-image response shape, after the wire stopped carrying a duplicate
#
# push_message used to attach a raw copy of every inline image to the envelope
# as ``binary_data``, purely so this endpoint could re-encode it. That copy is
# what made one image cost ~2.34x its size on a 256 KiB-capped plane. It is
# gone; the HTTP field is not, because the plugin-manager frontend reads it. So
# the value is rebuilt from the canonical part, and the shape it produces has to
# be indistinguishable from what the duplicate produced.
# ---------------------------------------------------------------------------


class _CaptureQueue:
    def __init__(self) -> None:
        self.items: list[dict[str, object]] = []

    def put_nowait(self, payload: dict[str, object]) -> None:
        self.items.append(payload)


def _pushed_inline_image_record(
    monkeypatch: pytest.MonkeyPatch, raw: bytes
) -> dict[str, object]:
    """One real _build_wire_payload envelope carrying an inline image.

    Built by the actual writer rather than by hand: this test exists to pin the
    contract BETWEEN push_message and this serializer, and a hand-rolled record
    would keep passing after the writer changed shape.
    """
    from plugin.core import context as context_module
    from plugin.core.context import PluginContext

    monkeypatch.setattr(context_module, "zmq", None)
    capture = _CaptureQueue()
    ctx = PluginContext(
        plugin_id="demo",
        config_path=Path("plugin.toml"),
        logger=logging.getLogger("test"),  # type: ignore[arg-type]
        status_queue=queue.Queue(),
        message_queue=capture,
    )
    result = ctx.push_message(
        source="demo",
        visibility=["chat"],
        ai_behavior="respond",
        parts=[
            {"type": "text", "text": "look"},
            {"type": "image", "data": raw, "mime": "image/png"},
        ],
    )
    assert result == {"submitted": True}
    return capture.items[0]


@pytest.mark.plugin_unit
def test_inline_image_push_still_serializes_binary_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A v2 push with an inline image still answers with the base64 image."""
    raw = b"\x89PNG" + bytes(range(256)) * 4
    record = _pushed_inline_image_record(monkeypatch, raw)

    # The premise: the wire no longer carries the raw duplicate this endpoint
    # used to read. If that ever comes back, this test stops proving anything.
    assert record["binary_data"] is None

    serialized = module._serialize_message(record)

    assert serialized["binary_data"] == base64.b64encode(raw).decode("ascii")
    assert set(serialized) == {
        "plugin_id",
        "source",
        "description",
        "priority",
        "message_type",
        "content",
        "binary_data",
        "binary_url",
        "metadata",
        "timestamp",
        "message_id",
    }
    assert serialized["plugin_id"] == "demo"
    assert serialized["content"] == "look"
    assert serialized["binary_url"] == ""
    assert serialized["message_type"] == "proactive_notification"


@pytest.mark.plugin_unit
def test_serialized_shape_is_identical_with_and_without_the_legacy_duplicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A/B on the exact regression risk: old record vs new record, same output.

    The old envelope is the new one plus the raw ``binary_data`` copy, so
    serializing both and comparing whole dicts is a direct before/after on the
    HTTP response -- not just on the one field that changed.
    """
    raw = b"\x89PNG" + bytes(range(256)) * 4
    new_record = _pushed_inline_image_record(monkeypatch, raw)
    old_record = dict(new_record)
    old_record["binary_data"] = raw

    assert module._serialize_message(new_record) == module._serialize_message(old_record)


@pytest.mark.plugin_unit
def test_binary_data_still_wins_over_the_canonical_part() -> None:
    """The legacy field stays authoritative when it is the ONLY carrier.

    push_message keeps emitting it for a caller that passed ``binary_data=``
    next to an explicit ``parts=`` list; those bytes ride in no part, so reading
    the part first would silently answer with the wrong picture.
    """
    record = {
        "plugin_id": "demo",
        "binary_data": b"unreferenced-by-any-part",
        "parts": [
            {
                "type": "image",
                "binary_base64": base64.b64encode(b"a different picture").decode("ascii"),
                "mime": "image/png",
            }
        ],
        "time": "2026-01-01T00:00:00Z",
        "message_id": "m1",
    }

    serialized = module._serialize_message(record)

    assert serialized["binary_data"] == base64.b64encode(b"unreferenced-by-any-part").decode("ascii")


@pytest.mark.plugin_unit
def test_url_only_image_part_serializes_no_binary_data() -> None:
    """An offloaded image has no inline bytes, and must not grow any."""
    record = {
        "plugin_id": "demo",
        "parts": [{"type": "image", "url": "http://host/i.png", "mime": "image/png"}],
        "binary_url": "http://host/i.png",
        "time": "2026-01-01T00:00:00Z",
        "message_id": "m1",
    }

    serialized = module._serialize_message(record)

    assert serialized["binary_data"] is None
    assert serialized["binary_url"] == "http://host/i.png"
