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
    names = [
        "read_current_screen",
        "read_screen_window",
        "zoom_screen_region",
        "zoom_screen_region",
    ]
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
        name="zoom_screen_region",
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
    assert {t.name for t in tools} == {
        "read_current_screen",
        "read_screen_window",
        "zoom_screen_region",
    }
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
