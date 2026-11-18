import asyncio
import base64
import io
from types import SimpleNamespace

import pytest
from PIL import Image

from main_logic import screen_inspection as m
from main_logic.omni_offline_client import OmniOfflineClient

pytestmark = pytest.mark.unit


def fixture():
    image = Image.new("RGB", (2560, 1440), "blue")
    image.paste("red", (1280, 0, 2560, 1440))
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    manager = SimpleNamespace(
        session=object.__new__(OmniOfflineClient), current_speech_id="turn1"
    )
    calls = []

    async def send(message):
        calls.append(message)
        reply = {
            "request_id": message["request_id"],
            "success": True,
            "scope": "desktop",
            "data_url": "data:image/png;base64,"
            + base64.b64encode(buffer.getvalue()).decode(),
            "source_id": message.get("window_id", "screen:1"),
            "windows": [{"id": "window:9", "name": "Test APP"}],
            "avatar_position": {
                "centerX": 0.75,
                "centerY": 0.5,
                "width": 0.2,
                "height": 0.8,
            },
        }
        m.resolve_response(manager, manager.websocket, reply)

    manager.websocket = SimpleNamespace(send_json=send)
    return manager, calls


def test_native_pixels_retained_and_crop_uses_original():
    async def run():
        manager, calls = fixture()
        overview = await m.inspect(manager)
        out = overview["output"]
        assert out["original_size"] == [2560, 1440]
        assert out["preview_size"] == [1280, 720]
        crop = await m.inspect(
            manager,
            "region",
            {
                "snapshot_id": out["snapshot_id"],
                "target": "region",
                "region": [0.5, 0, 1, 1],
            },
        )
        assert crop["output"]["original_size"] == [1280, 1440]
        im = Image.open(io.BytesIO(base64.b64decode(crop["images"][0]["data_b64"])))
        assert im.getpixel((50, 50))[0] > 240
        assert [c["operation"] for c in calls] == ["overview", "validate"]
        avatar = await m.inspect(
            manager, "region", {"snapshot_id": out["snapshot_id"], "target": "avatar"}
        )
        assert avatar["output"]["kind"] == "region"

    asyncio.run(run())


def test_window_ids_must_come_from_snapshot():
    async def run():
        manager, calls = fixture()
        sid = (await m.inspect(manager))["output"]["snapshot_id"]
        bad = await m.inspect(
            manager, "window", {"snapshot_id": sid, "window_id": "window:other"}
        )
        assert bad["is_error"] and len(calls) == 1
        good = await m.inspect(
            manager, "window", {"snapshot_id": sid, "window_id": "window:9"}
        )
        assert good["output"]["kind"] == "window"

    asyncio.run(run())


@pytest.mark.parametrize("change", ["turn", "session", "expiry"])
def test_old_snapshot_cannot_be_read(change):
    async def run():
        manager, calls = fixture()
        sid = (await m.inspect(manager))["output"]["snapshot_id"]
        if change == "turn":
            manager.current_speech_id = "turn2"
        elif change == "session":
            manager.session = object.__new__(OmniOfflineClient)
        else:
            m._state(manager)["images"][sid]["expires"] = 0
        assert (
            await m.inspect(manager, "region", {"snapshot_id": sid, "target": "avatar"})
        )["is_error"]
        assert len(calls) == 1

    asyncio.run(run())


def test_privacy_revocation_clears_cached_pixels():
    async def run():
        manager, calls = fixture()
        sid = (await m.inspect(manager))["output"]["snapshot_id"]

        async def deny(message):
            m.resolve_response(
                manager,
                manager.websocket,
                {
                    "request_id": message["request_id"],
                    "success": False,
                    "error": "disabled",
                },
            )

        manager.websocket.send_json = deny
        result = await m.inspect(
            manager, "region", {"snapshot_id": sid, "target": "avatar"}
        )
        assert result["is_error"] and not m._state(manager)["images"]

    asyncio.run(run())


def test_wrong_connection_and_late_reply_not_accepted():
    async def run():
        manager, _ = fixture()
        future = asyncio.get_running_loop().create_future()
        manager._screen_inspection_pending = {
            "future": future,
            "websocket": manager.websocket,
            "id": "right",
        }
        m.resolve_response(manager, object(), {"request_id": "right", "success": True})
        m.resolve_response(
            manager, manager.websocket, {"request_id": "wrong", "success": True}
        )
        assert not future.done()
        future.cancel()

    asyncio.run(run())


@pytest.mark.parametrize(
    "region",
    [
        [0, 0, float("nan"), 1],
        [-0.1, 0, 1, 1],
        [1, 0, 0, 1],
        [0, 0, 0.0001, 0.0001],
        [True, 0, 1, 1],
    ],
)
def test_invalid_region_rejected(region):
    with pytest.raises(ValueError):
        m.crop_box(region, (2560, 1440))


def test_overview_window_and_two_crops_reach_provider_messages():
    from main_logic.tool_calling import ToolImage, ToolResult

    client = object.__new__(OmniOfflineClient)
    client._tool_image_locale = lambda: "zh"
    messages, slots, frames = [], [], []
    names = ["read_current_screen"] * 4
    for i, name in enumerate(names):
        result = ToolResult(
            call_id=str(i),
            name=name,
            output={},
            images=[ToolImage(data_b64="cGl4ZWxz")],
        )
        assert client._append_tool_result_images(
            messages, result, slots=slots, bus_frames=frames
        )
        assert not result.output.get("_image_warnings")
    assert len(frames) == 4
    fifth = ToolResult(
        call_id="extra",
        name="read_current_screen",
        output={},
        images=[ToolImage(data_b64="cGl4ZWxz")],
    )
    assert (
        client._append_tool_result_images(
            messages, fifth, slots=slots, bus_frames=frames
        )
        is None
    )
    assert fifth.output["_image_warnings"]
    client._release_tool_image_slots(slots)
    assert all("image_url" not in str(m) for m in messages)


def test_tool_image_byte_budget_remains_enforced():
    from main_logic.tool_calling import ToolImage, ToolResult

    client = object.__new__(OmniOfflineClient)
    client._tool_image_locale = lambda: "zh"
    result = ToolResult(
        call_id="large",
        name="read_current_screen",
        output={},
        images=[ToolImage(data_b64="A" * (4 * 1024 * 1024 + 1))],
    )
    assert client._append_tool_result_images([], result, slots=[]) is None
    assert result.output["_image_warnings"]


@pytest.mark.parametrize("cancel_after_denial", [False, True])
async def test_screen_denial_keeps_other_tool_evidence_and_releases_on_cancel(cancel_after_denial):
    from main_logic.tool_calling import ToolCall, ToolImage, ToolResult

    client = object.__new__(OmniOfflineClient)
    client._tool_image_locale = lambda: "en"
    messages, slots, frames = [], [], []
    old_screen = {
        "role": "tool", "name": "read_current_screen", "tool_call_id": "old",
        "content": '{"title":"private-old-title"}',
    }
    messages.append(old_screen)
    slots.append((messages, 0, old_screen, client._released_screen_result_content()))
    for name, pixels in (("read_current_screen", "old-screen-pixels"), ("recall_memory", "other-pixels")):
        client._append_tool_result_images(
            messages,
            ToolResult(call_id=name, name=name, output={}, images=[ToolImage(data_b64=pixels)]),
            slots=slots, bus_frames=frames,
        )
    prior_request = list(messages)

    async def handler(call):
        if call.call_id == "cancel":
            raise asyncio.CancelledError
        if call.call_id == "denied":
            return ToolResult(
                call_id=call.call_id, name=call.name,
                output={"status": "unavailable", "reason": "disabled"}, is_error=True,
            )
        return ToolResult(
            call_id=call.call_id, name=call.name,
            output={"title": "private-new-title" if call.call_id == "screen" else "keep-result"},
            images=[ToolImage(data_b64="new-screen-pixels" if call.call_id == "screen" else "new-other-pixels")],
        )

    client.on_tool_call = handler
    calls = [
        ToolCall(call_id="screen", name="read_current_screen", arguments={}),
        ToolCall(call_id="other", name="recall_memory", arguments={}),
        ToolCall(call_id="denied", name="read_current_screen", arguments={}),
    ]
    if cancel_after_denial:
        calls.append(ToolCall(call_id="cancel", name="read_current_screen", arguments={}))
    assistant = {
        "role": "assistant", "content": "",
        "tool_calls": [{"id": call.call_id, "type": "function",
                        "function": {"name": call.name, "arguments": "{}"}} for call in calls],
    }
    run = client._run_tool_round(
        messages, assistant, calls, tool_image_slots=slots, tool_bus_frames=frames,
        generation_is_active=lambda: True, log_prefix="test",
    )
    if cancel_after_denial:
        with pytest.raises(asyncio.CancelledError):
            await run
    else:
        executed, live = await run
        assert executed == 3 and live

    saved = str(messages)
    assert "private-old-title" not in saved and "private-new-title" not in saved
    assert "old-screen-pixels" not in saved and "new-screen-pixels" not in saved
    assert "other-pixels" in saved and "keep-result" in saved and "disabled" in saved
    assert all(frame[2] == "recall_memory" for frame in frames)
    assert len(frames) == (1 if cancel_after_denial else 2)
    replies = [message["tool_call_id"] for message in messages if message.get("role") == "tool"]
    assert replies == ["old", "screen", "other", "denied"]
    assert "private-old-title" in str(prior_request) and "old-screen-pixels" in str(prior_request)


def test_child_coordinates_and_source_are_relative_to_parent():
    async def run():
        manager, _ = fixture()
        out = (await m.inspect(manager))["output"]
        first = await m.inspect(
            manager,
            "region",
            {
                "snapshot_id": out["snapshot_id"],
                "target": "region",
                "region": [0.5, 0, 1, 1],
            },
        )
        second = await m.inspect(
            manager,
            "region",
            {
                "snapshot_id": first["output"]["snapshot_id"],
                "target": "region",
                "region": [0, 0, 0.5, 0.5],
            },
        )
        assert second["output"]["original_size"] == [640, 720]
        assert second["output"]["parent_snapshot_id"] == first["output"]["snapshot_id"]
        assert second["output"]["captured_at"] == out["captured_at"]
        assert second["output"]["source_id"] == out["source_id"]
        image = m._state(manager)["images"][second["output"]["snapshot_id"]]["image"]
        assert image.getpixel((1, 1)) == (255, 0, 0)

    asyncio.run(run())


@pytest.mark.parametrize("what", ["turn", "socket", "session"])
def test_replaced_owner_during_capture_does_not_publish(what):
    async def run():
        manager, _ = fixture()
        original = manager.websocket.send_json

        async def replace(request):
            await original(request)
            if what == "turn":
                manager.current_speech_id = "next"
            elif what == "socket":
                manager.websocket = object()
            else:
                manager.session = object.__new__(OmniOfflineClient)

        manager.websocket.send_json = replace
        result = await m.inspect(manager)
        assert result["output"]["reason"] == "superseded"
        assert result["images"] == []
        assert manager._screen_inspection_pending is None

    asyncio.run(run())


def test_cancel_and_timeout_release_request_slot(monkeypatch):
    async def run():
        manager, _ = fixture()
        started = asyncio.Event()

        async def no_reply(request):
            started.set()

        manager.websocket.send_json = no_reply
        task = asyncio.create_task(m.inspect(manager))
        await started.wait()
        assert (await m.inspect(manager))["output"]["reason"] == "busy"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert manager._screen_inspection_pending is None
        monkeypatch.setattr(m, "CAPTURE_TIMEOUT", 0.001)
        assert (await m.inspect(manager))["output"]["reason"] == "timeout"
        assert manager._screen_inspection_pending is None

    asyncio.run(run())


@pytest.mark.parametrize("language", ["en", "zh-CN", "ja"])
def test_localized_tools_and_errors(language):
    manager, _ = fixture()
    manager.user_language = language
    tools = m.build_tools(manager)
    assert {t.name for t in tools} == {"read_current_screen"}
    assert all(t.description for t in tools)
    result = m.failure(manager, "disabled")
    expected = {"en": "disabled", "zh-CN": "关闭", "ja": "無効"}[language]
    assert expected in result["output"]["message"]
    assert result["images"] == []


def test_transparent_window_pixels_composite_on_white():
    image = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
    image.putpixel((0, 0), (255, 0, 0, 255))
    b = io.BytesIO()
    image.save(b, "PNG")
    result = m.decode_image(
        "data:image/png;base64," + base64.b64encode(b.getvalue()).decode()
    )
    assert result.getpixel((1, 1)) == (255, 255, 255)
    assert result.getpixel((0, 0)) == (255, 0, 0)


@pytest.mark.parametrize(
    "data",
    [
        None,
        "",
        "data:image/png;base64,!!!",
        "https://example.org/x.png",
        "data:text/plain;base64,YWJj",
    ],
)
def test_invalid_images_rejected(data):
    with pytest.raises(m.InspectionError):
        m.decode_image(data)


def test_cache_count_and_pixel_budgets(monkeypatch):
    async def run():
        manager, _ = fixture()
        monkeypatch.setattr(m, "MAX_CACHE_PIXELS", 2560 * 1440 * 2)
        snapshots = [
            (await m.inspect(manager))["output"]["snapshot_id"] for _ in range(5)
        ]
        assert list(m._state(manager)["images"]) == snapshots[-2:]
        monkeypatch.setattr(m, "MAX_CACHE_PIXELS", 2560 * 1440 * 10)
        snapshots += [
            (await m.inspect(manager))["output"]["snapshot_id"] for _ in range(5)
        ]
        assert list(m._state(manager)["images"]) == snapshots[-4:]

    asyncio.run(run())


def test_unrelated_tools_keep_two_image_budget():
    from main_logic.tool_calling import ToolImage, ToolResult

    client = object.__new__(OmniOfflineClient)
    client._tool_image_locale = lambda: "en"
    messages, slots = [], []
    for i in range(3):
        result = ToolResult(
            call_id=str(i),
            name="other_tool",
            output={},
            images=[ToolImage(data_b64="cGl4ZWxz")],
        )
        value = client._append_tool_result_images(messages, result, slots=slots)
        assert (value is not None) == (i < 2)
    assert result.output["_image_warnings"]


@pytest.mark.parametrize(
    "field,value", [("source_id", "window:other"), ("scope", "changed")]
)
def test_wrong_source_response_rejected(field, value):
    async def run():
        manager, _ = fixture()
        sid = (await m.inspect(manager))["output"]["snapshot_id"]

        async def wrong(request):
            reply = {
                "request_id": request["request_id"],
                "success": True,
                "scope": "desktop",
                "source_id": "window:9",
            }
            reply[field] = value
            m.resolve_response(manager, manager.websocket, reply)

        manager.websocket.send_json = wrong
        result = await m.inspect(
            manager, "window", {"snapshot_id": sid, "window_id": "window:9"}
        )
        assert result["is_error"] and result["images"] == []

    asyncio.run(run())


@pytest.mark.parametrize("helper", ["decode_image", "_encode_preview"])
def test_image_workers_do_not_publish_into_a_replacement_turn(helper, monkeypatch):
    import threading

    started, release = threading.Event(), threading.Event()
    original = getattr(m, helper)

    def delayed(*args):
        started.set()
        assert release.wait(2)
        return original(*args)

    monkeypatch.setattr(m, helper, delayed)

    async def run():
        manager, _ = fixture()
        task = asyncio.create_task(m.inspect(manager))
        assert await asyncio.to_thread(started.wait, 2)
        manager.current_speech_id = "replacement"
        release.set()
        result = await task
        assert result["output"]["reason"] == "superseded"
        assert result["images"] == []
        assert not m._state(manager)["images"]

    try:
        asyncio.run(run())
    finally:
        release.set()


def test_expiry_during_permission_validation_rejects_crop():
    async def run():
        manager, _ = fixture()
        sid = (await m.inspect(manager))["output"]["snapshot_id"]
        original = manager.websocket.send_json

        async def expire(message):
            if message["operation"] == "validate":
                m._state(manager)["images"][sid]["expires"] = 0
            await original(message)

        manager.websocket.send_json = expire
        result = await m.inspect(
            manager, "region", {"snapshot_id": sid, "target": "avatar"}
        )
        assert result["output"]["reason"] == "stale_snapshot"

    asyncio.run(run())


@pytest.mark.parametrize('phase', ['decode_image', '_encode_preview'])
@pytest.mark.parametrize('change', ['connection', 'revoke', 'revoke_enable'])
async def test_image_processing_rechecks_connection_and_permission(phase, change, monkeypatch):
    manager, _ = fixture()
    original = m.asyncio.to_thread

    async def intercept(fn, *args, **kwargs):
        value = await original(fn, *args, **kwargs)
        if fn.__name__ == phase:
            if change == 'connection':
                manager.websocket = SimpleNamespace(send_json=manager.websocket.send_json)
            else:
                manager._proactive_vision_revocation_epoch = 1
                manager._proactive_vision_enabled = change == 'revoke_enable'
        return value

    monkeypatch.setattr(m.asyncio, 'to_thread', intercept)
    result = await m.read_screen(manager)
    assert result['is_error'] and not result['images']
    assert result['output']['reason'] in ('disabled', 'superseded')
    assert not m._state(manager)['images']


async def test_cached_pixels_do_not_cross_connections():
    manager, calls = fixture()
    first = await m.read_screen(manager)
    manager.websocket = SimpleNamespace(send_json=manager.websocket.send_json)
    result = await m.read_screen(manager, {'snapshot_id': first['output']['snapshot_id'], 'region': [0, 0, 1, 1]})
    assert result['output']['reason'] == 'stale_snapshot'
    assert len(calls) == 1


async def test_send_is_inside_capture_deadline(monkeypatch):
    manager, _ = fixture()
    async def stalled(message):
        await asyncio.Future()
    manager.websocket.send_json = stalled
    monkeypatch.setattr(m, 'CAPTURE_TIMEOUT', .01)
    result = await asyncio.wait_for(m.read_screen(manager), .5)
    assert result['output']['reason'] == 'timeout'
    assert manager._screen_inspection_pending is None


@pytest.mark.parametrize('stale', ['session', 'reply'])
async def test_stale_tool_invocation_does_not_capture(stale):
    from main_logic.core.tool_calling import ToolCallingMixin
    from main_logic.tool_calling import ToolCall, ToolRegistry
    manager, calls = fixture()
    manager.tool_registry = ToolRegistry()
    manager.tool_registry.register(m.build_tools(manager)[0])
    manager.session._active_reply_owner = object()
    call = ToolCall(call_id='old', name='read_current_screen', arguments={},
                    reply_owner=object() if stale == 'reply' else None)
    result = await ToolCallingMixin._on_tool_call(manager, call,
        invoking_session=object() if stale == 'session' else manager.session)
    assert result.is_error and result.output['reason'] == 'superseded'
    assert not calls


@pytest.mark.parametrize("stale", ["session", "reply"])
async def test_tool_invocation_rechecks_owner_after_thinking_bubble(stale):
    from unittest.mock import AsyncMock

    from main_logic.core.tool_calling import ToolCallingMixin
    from main_logic.tool_calling import ToolCall, ToolRegistry

    manager, calls = fixture()
    manager.tool_registry = ToolRegistry()
    manager.tool_registry.register(m.build_tools(manager)[0])
    manager._only_looks_up = lambda name: True
    manager._route_tool_images = AsyncMock()
    session = manager.session
    owner = session._active_reply_owner = object()
    bubble_started, release_bubble = asyncio.Event(), asyncio.Event()

    async def light_bubble(active):
        assert active is True
        bubble_started.set()
        await release_bubble.wait()

    manager._push_focus_thinking = light_bubble
    call = ToolCall(
        call_id="interrupted", name="read_current_screen", arguments={}, reply_owner=owner,
    )
    task = asyncio.create_task(ToolCallingMixin._on_tool_call(
        manager, call, invoking_session=session,
    ))
    try:
        await asyncio.wait_for(bubble_started.wait(), 1)
        if stale == "session":
            manager.session = object.__new__(OmniOfflineClient)
            # Keep the same reply owner so the session check is independently tested.
            manager.session._active_reply_owner = owner
        else:
            session._active_reply_owner = object()
        release_bubble.set()
        result = await asyncio.wait_for(task, 1)
        assert result.is_error and result.output["reason"] == "superseded"
        assert result.images == []
        assert not calls
        manager._route_tool_images.assert_not_awaited()
        assert not getattr(manager, "_screen_inspection_state", None)
    finally:
        release_bubble.set()
        if not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task


@pytest.mark.parametrize("empty", [None, "", [], {}])
async def test_optional_empty_screen_fields_do_not_block_capture(empty):
    manager, calls = fixture()
    first = await m.read_screen(manager, {
        "snapshot_id": empty, "window_id": empty, "region": empty,
    })
    assert not first.get("is_error", False)
    assert first["output"]["status"] == "captured"
    assert first["output"]["kind"] == "overview"
    assert len(first["images"]) == 1
    sid = first["output"]["snapshot_id"]
    window = await m.read_screen(manager, {
        "snapshot_id": sid, "window_id": "window:9", "region": empty,
    })
    assert window["output"]["kind"] == "window"
    crop = await m.read_screen(manager, {
        "snapshot_id": sid, "window_id": empty, "region": [0, 0, 1, 1],
    })
    assert crop["output"]["kind"] == "region"
    assert [call["operation"] for call in calls] == ["overview", "window", "validate"]


async def test_nonempty_conflicting_screen_targets_remain_invalid():
    manager, calls = fixture()
    first = await m.read_screen(manager)
    sid = first["output"]["snapshot_id"]
    result = await m.read_screen(manager, {
        "snapshot_id": sid, "window_id": "window:9", "region": [0, 0, 1, 1],
    })
    assert result["output"]["reason"] == "invalid_request"
    assert len(calls) == 1
