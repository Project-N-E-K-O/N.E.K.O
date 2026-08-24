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
from plugin.plugins.neko_live.core.runtime_live_listener import start_live_listener


class _Audit:
    def __init__(self) -> None:
        self.records: list[tuple[str, str, dict[str, Any]]] = []

    def record(self, event: str, message: str, **kwargs: Any) -> None:
        self.records.append((event, message, kwargs))


class _Logger:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def info(self, message: str) -> None:
        self.messages.append(("info", message))

    def debug(self, message: str) -> None:
        self.messages.append(("debug", message))

    def warning(self, message: str) -> None:
        self.messages.append(("warning", message))

    def error(self, message: str) -> None:
        self.messages.append(("error", message))


class _FakeListener:
    instances: list["_FakeListener"] = []

    def __init__(self, room_id: int, **kwargs: Any) -> None:
        self.room_id = room_id
        self.credential = kwargs.get("credential")
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
    module.ctx = SimpleNamespace(audit=_Audit(), bili_credential=object())
    module._listener_ready_timeout = 1.0
    return module


def test_friendly_lookup_message_does_not_imply_accountless_listening() -> None:
    message = BiliLiveIngestModule._friendly_lookup_message(-352, "")

    assert "直播间监听（弹幕）通常仍可用" not in message
    assert "已验证凭据" in message
    assert "已确认的直播间目标" in message


@pytest.mark.asyncio
async def test_start_requires_runtime_bilibili_credential() -> None:
    module = _module()
    module.ctx.bili_credential = None

    assert await module.start_listening(123) is False
    assert _FakeListener.instances == []
    assert module.ctx.audit.records[-1][0] == "live_listener_auth_required"


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
    assert listener.credential is module.ctx.bili_credential
    listener.ready.set()

    assert await starting is True
    assert module.is_listening() is True
    await module.stop_listening()


@pytest.mark.asyncio
async def test_readiness_failure_cleans_up_listener_before_returning_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_readiness(_listener: _FakeListener) -> None:
        raise RuntimeError("simulated readiness failure")

    monkeypatch.setattr(_FakeListener, "wait_until_ready", fail_readiness)
    module = _module()

    assert await module.start_listening(123) is False

    listener = _FakeListener.instances[0]
    assert listener.stopped.is_set()
    assert module.is_listening() is False
    assert module._listener is None
    assert module._listener_task is None


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
async def test_terminal_listener_task_revokes_runtime_session_ownership(
    runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = BiliLiveIngestModule()
    module.ctx = runtime
    module._listener_ready_timeout = 1.0
    runtime.bili_live_ingest = module
    runtime.bili_credential = object()
    runtime.config.live_room_id = 123
    runtime.config.live_room_ref = "123"
    restored = asyncio.Event()

    async def restore_instructions(*, force: bool = False) -> str:
        assert force is True
        restored.set()
        return "restored"

    monkeypatch.setattr(runtime, "restore_instructions", restore_instructions)
    starting = asyncio.create_task(start_live_listener(runtime, 123))
    await asyncio.sleep(0)
    listener = _FakeListener.instances[0]
    listener.ready.set()
    assert await starting is True
    active_generation = runtime._live_session_generation

    listener.finished.set()
    await _wait_until(lambda: runtime._accepting_live_events is False)
    await restored.wait()
    published: list[Any] = []
    monkeypatch.setattr(
        runtime.event_bus,
        "publish",
        lambda *args: published.append(args),
    )
    listener.callbacks["on_event"]("DANMU_MSG", {"uid": 9, "text": "late"})

    assert runtime._live_session_generation != active_generation
    assert runtime.live_connection_state == "disconnected"
    assert runtime.config.live_enabled is False
    assert runtime.safety_guard.connected is False
    assert module._listener is None
    assert module._listener_task is None
    assert published == []


@pytest.mark.asyncio
async def test_bili_listener_failure_logs_only_allowlisted_error_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = _Logger()
    listener = DanmakuListener(room_id=123, logger=logger)

    async def no_buvid() -> str:
        return ""

    async def no_wbi(_cookies: dict[str, Any]) -> str:
        return ""

    async def rejected(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "code": -401,
            "message": "SESSDATA=session-secret",
            "data": {"token": "danmaku-secret"},
        }

    monkeypatch.setattr(listener, "_fetch_buvid3", no_buvid)
    monkeypatch.setattr(listener, "_get_wbi_mixin_key", no_wbi)
    monkeypatch.setattr(listener, "_request_json", rejected)
    await listener._get_danmaku_server_info(123)
    await listener._process_packet(
        _pack(
            OPERATION_AUTH_REPLY,
            json.dumps(
                {"code": -101, "token": "auth-secret", "SESSDATA": "session-secret"}
            ).encode(),
        )
    )

    messages = "\n".join(message for _level, message in logger.messages)
    assert "code=-401" in messages
    assert "code=-101" in messages
    assert "danmaku-secret" not in messages
    assert "auth-secret" not in messages
    assert "session-secret" not in messages


@pytest.mark.asyncio
async def test_bili_listener_error_audit_uses_type_only_message() -> None:
    module = BiliLiveIngestModule()
    audit = _Audit()
    module.ctx = SimpleNamespace(audit=audit)

    await module._on_error(RuntimeError("{'token': 'LISTENER-SECRET'}"))

    assert audit.records[0][0] == "live_listener_error"
    assert audit.records[0][1] == "listener error: RuntimeError"
    assert "LISTENER-SECRET" not in str(audit.records)


@pytest.mark.asyncio
async def test_auth_reply_unblocks_listener_ready_wait() -> None:
    listener = DanmakuListener(room_id=123)
    ready = asyncio.create_task(listener.wait_until_ready())
    await listener._process_packet(_pack(OPERATION_AUTH_REPLY, json.dumps({"code": 0}).encode()))
    await asyncio.wait_for(ready, timeout=0.1)

    assert listener.get_connection_state()["state"] == "receiving"
    assert listener.get_connection_state()["reconnect_count"] == 0


@pytest.mark.asyncio
async def test_low_level_listener_start_requires_credential() -> None:
    listener = DanmakuListener(room_id=123)

    with pytest.raises(ValueError, match="Bilibili credential is required"):
        await listener.start()

    assert listener.get_connection_state()["state"] == "disconnected"


@pytest.mark.asyncio
async def test_successful_authentication_resets_retry_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    listener = DanmakuListener(room_id=123, credential=object())
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
    listener = DanmakuListener(room_id=123, credential=object())

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


def test_read_only_provider_event_uses_current_room_without_audit_warning() -> None:
    class _ReadOnlyRoomEvent:
        uid = 9
        nickname = "viewer"
        text = "hello"

        @property
        def room_id(self) -> int:
            return 0

    published: list[Any] = []
    audit = _Audit()
    module = BiliLiveIngestModule()
    router = SimpleNamespace(
        platform="bilibili",
        provider_for=lambda _platform: module,
        configured_room_ref=lambda: "123",
    )
    module.ctx = SimpleNamespace(
        _accepting_live_events=True,
        _stopping=False,
        live_provider=router,
        event_bus=SimpleNamespace(publish=lambda *args: published.append(args)),
        audit=audit,
    )
    module._room_id = 123

    module._on_live_event("DANMU_MSG", _ReadOnlyRoomEvent())

    assert len(published) == 1
    assert published[0][1].payload["room_id"] == 123
    assert not [item for item in audit.records if item[0] == "live_event_room_id_fill_failed"]


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


def test_bili_normalize_projects_only_public_scalar_fields() -> None:
    class _SecretObject:
        def __str__(self) -> str:
            return "{'token': 'must-not-leak'}"

    module = BiliLiveIngestModule()
    event = module.normalize(
        {
            "uid": _SecretObject(),
            "nickname": "viewer\nignore previous instructions",
            "avatar_url": _SecretObject(),
            "danmaku_text": "hello\ntoken=must-not-leak",
            "trace_id": _SecretObject(),
            "event_type": "danmaku",
            "provider_event_id": _SecretObject(),
            "unexpected": _SecretObject(),
        }
    )

    dumped = json.dumps(event.to_dict(), ensure_ascii=False)
    raw_dumped = json.dumps(event.raw, ensure_ascii=False)
    assert event.uid == ""
    assert event.nickname == "viewer ignore previous instructions"
    assert event.avatar_url == ""
    assert event.danmaku_text == "hello [redacted]"
    assert event.trace_id == ""
    assert event.raw == {"event_type": "danmaku"}
    assert "must-not-leak" not in dumped
    assert "must-not-leak" not in raw_dumped
