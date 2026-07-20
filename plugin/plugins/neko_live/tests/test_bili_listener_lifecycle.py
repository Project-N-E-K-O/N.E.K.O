from __future__ import annotations

import asyncio
import json
from collections import deque
from types import SimpleNamespace
from typing import Any

import pytest

from plugin.plugins.neko_live.modules.bili_live_ingest import BiliLiveIngestModule
from plugin.plugins.neko_live.modules.bili_live_ingest import danmaku_core
from plugin.plugins.neko_live.modules.bili_live_ingest.danmaku_core import (
    OPERATION_AUTH_REPLY,
    DanmakuListener,
    _pack,
)


class _Audit:
    def __init__(self) -> None:
        self.records: list[tuple[str, str, dict[str, Any]]] = []

    def record(self, event: str, message: str, **kwargs: Any) -> None:
        self.records.append((event, message, kwargs))


class _FakeListener:
    instances: list["_FakeListener"] = []

    def __init__(self, room_id: int, **kwargs: Any) -> None:
        self.room_id = room_id
        self.callbacks = dict(kwargs.get("callbacks") or {})
        self.ready = asyncio.Event()
        self.stopped = asyncio.Event()
        self.finished = asyncio.Event()
        self.__class__.instances.append(self)

    async def start(self) -> None:
        await self.finished.wait()

    async def wait_until_ready(self) -> None:
        await self.ready.wait()

    async def stop(self) -> None:
        self.stopped.set()
        self.finished.set()

    def get_connection_state(self) -> dict[str, Any]:
        return {"state": "receiving" if self.ready.is_set() else "connecting", "room_id": self.room_id}


def _module() -> BiliLiveIngestModule:
    module = BiliLiveIngestModule()
    module.ctx = SimpleNamespace(audit=_Audit(), bili_credential=None)
    module._listener_ready_timeout = 1.0
    return module


async def _wait_until(predicate: Any) -> None:
    for _ in range(20):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition did not become true")


@pytest.fixture(autouse=True)
def _fake_listener(monkeypatch: pytest.MonkeyPatch):
    _FakeListener.instances.clear()
    monkeypatch.setattr(danmaku_core, "DanmakuListener", _FakeListener)


@pytest.mark.asyncio
async def test_start_returns_only_after_auth_ready() -> None:
    module = _module()
    starting = asyncio.create_task(module.start_listening(123))
    await asyncio.sleep(0)

    assert not starting.done()
    listener = _FakeListener.instances[0]
    listener.ready.set()

    assert await starting is True
    assert module.is_listening() is True
    await module.stop_listening()


@pytest.mark.asyncio
async def test_listener_uses_rich_event_as_the_only_support_event_callback() -> None:
    module = _module()
    starting = asyncio.create_task(module.start_listening(123))
    await asyncio.sleep(0)
    listener = _FakeListener.instances[0]

    assert "on_event" in listener.callbacks
    assert "on_gift" not in listener.callbacks
    assert "on_sc" not in listener.callbacks

    listener.ready.set()
    assert await starting is True
    await module.stop_listening()


@pytest.mark.asyncio
async def test_stop_during_start_cannot_resurrect_or_orphan_listener() -> None:
    module = _module()
    starting = asyncio.create_task(module.start_listening(123))
    await asyncio.sleep(0)
    listener = _FakeListener.instances[0]

    await module.stop_listening()

    assert await starting is False
    assert listener.stopped.is_set()
    assert module.is_listening() is False
    assert module._listener is None
    assert module._listener_task is None


@pytest.mark.asyncio
async def test_second_start_owns_generation_and_stops_first_listener() -> None:
    module = _module()
    first_start = asyncio.create_task(module.start_listening(123))
    await asyncio.sleep(0)
    first = _FakeListener.instances[0]
    second_start = asyncio.create_task(module.start_listening(456))
    await _wait_until(lambda: len(_FakeListener.instances) == 2)
    second = _FakeListener.instances[1]
    second.ready.set()

    assert await first_start is False
    assert await second_start is True
    assert first.stopped.is_set()
    assert module._listener is second
    assert module.listener_state()["room_id"] == 456
    await module.stop_listening()


@pytest.mark.asyncio
async def test_terminal_listener_task_clears_module_references() -> None:
    module = _module()
    starting = asyncio.create_task(module.start_listening(123))
    await asyncio.sleep(0)
    listener = _FakeListener.instances[0]
    listener.ready.set()
    assert await starting is True

    listener.finished.set()
    await _wait_until(lambda: module._listener is None)

    assert module.is_listening() is False
    assert module._listener is None
    assert module._listener_task is None
    ended = [item for item in module.ctx.audit.records if item[0] == "live_listener_task_ended"]
    assert len(ended) == 1
    assert ended[0][2]["level"] == "warning"
    assert ended[0][2]["detail"]["generation"] > 0


@pytest.mark.asyncio
async def test_auth_reply_unblocks_listener_ready_wait() -> None:
    listener = DanmakuListener(room_id=123)
    ready = asyncio.create_task(listener.wait_until_ready())
    await listener._process_packet(_pack(OPERATION_AUTH_REPLY, json.dumps({"code": 0}).encode()))
    await asyncio.wait_for(ready, timeout=0.1)

    assert listener.get_connection_state()["state"] == "receiving"
    assert listener.get_connection_state()["reconnect_count"] == 0


@pytest.mark.asyncio
async def test_successful_authentication_resets_retry_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    listener = DanmakuListener(room_id=123)
    attempts = 0

    async def connect_once() -> None:
        nonlocal attempts
        attempts += 1
        listener._authenticated_in_attempt = attempts == 10

    async def no_wait(_awaitable: Any, timeout: float) -> None:
        _awaitable.close()
        raise asyncio.TimeoutError

    monkeypatch.setattr(listener, "_connect_once", connect_once)
    monkeypatch.setattr(asyncio, "wait_for", no_wait)

    await listener.start()

    assert attempts == 20


@pytest.mark.asyncio
async def test_listener_start_propagates_cancellation_and_cleans_state(monkeypatch: pytest.MonkeyPatch) -> None:
    listener = DanmakuListener(room_id=123)

    async def cancelled() -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(listener, "_connect_once", cancelled)

    with pytest.raises(asyncio.CancelledError):
        await listener.start()
    assert listener.running is False
    assert listener.get_connection_state()["state"] == "disconnected"


def test_stale_or_paused_provider_event_is_dropped_before_event_bus() -> None:
    published: list[Any] = []
    module = BiliLiveIngestModule()
    router = SimpleNamespace(
        platform="douyin",
        provider_for=lambda _platform: module,
        configured_room_ref=lambda: "123",
    )
    module.ctx = SimpleNamespace(
        _accepting_live_events=True,
        _stopping=False,
        live_provider=router,
        event_bus=SimpleNamespace(publish=lambda *args: published.append(args)),
        audit=_Audit(),
    )
    module._room_id = 123

    module._on_live_event("DANMU_MSG", {"uid": 1, "text": "late"})
    router.platform = "bilibili"
    router.configured_room_ref = lambda: "456"
    module._on_live_event("DANMU_MSG", {"uid": 1, "text": "wrong room"})
    router.configured_room_ref = lambda: "123"
    module.ctx._stopping = True
    module._on_live_event("DANMU_MSG", {"uid": 1, "text": "paused"})

    assert published == []


def test_support_event_records_privacy_safe_ingest_stages() -> None:
    published: list[Any] = []
    timeline: deque[dict[str, Any]] = deque(maxlen=16)
    module = BiliLiveIngestModule()
    module.ctx = SimpleNamespace(
        _stopping=False,
        event_bus=SimpleNamespace(publish=lambda *args: published.append(args)),
        audit=_Audit(),
        runtime_timeline=timeline,
        _timeline_salt=b"test-timeline-salt",
    )
    module._room_id = 123

    module._on_live_event(
        "SEND_GIFT",
        {"uid": 42, "nickname": "viewer", "gift_name": "小鱼干", "gift_count": 1},
    )

    assert len(published) == 1
    assert [item["stage"] for item in timeline] == [
        "ingest",
        "event_bus",
    ]
    assert [item["status"] for item in timeline] == ["received", "published"]
    assert timeline[0]["trace_id"] == timeline[1]["trace_id"]
    assert timeline[0]["uid"].startswith("viewer_")
    assert "nickname" not in timeline[0]

    module._on_live_event(
        "SEND_GIFT",
        {"uid": 42, "nickname": "viewer", "gift_name": "小鱼干", "gift_count": 1},
    )

    assert len(published) == 1
    assert timeline[-1]["stage"] == "ingest"
    assert timeline[-1]["status"] == "dropped"
    assert timeline[-1]["reason"] == "ingest.duplicate_support_event"
