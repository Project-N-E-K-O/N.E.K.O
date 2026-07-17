from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path

import pytest

from plugin.plugins.neko_live.modules.douyin_live_ingest.bridge_backend import (
    DouyinBridgeBackend,
    default_douyin_bridge_backend,
)
from plugin.plugins.neko_live.modules.douyin_live_ingest.embedded_bridge import DouyinEmbeddedBridgeSupervisor
from plugin.plugins.neko_live.modules.live_bridge.process_supervisor import BridgeProcessSupervisor
from plugin.plugins.neko_live.modules.live_bridge.transport import (
    LiveBridgeEvent,
    LiveBridgeStartRequest,
    LiveBridgeState,
    LiveBridgeTransport,
    local_bridge_url,
)


class _Adapter:
    adapter_id = "test_bridge"

    def __init__(self, base_url: str = "ws://127.0.0.1:1088/ws") -> None:
        self.base_url = base_url

    def bridge_url(self, room_ref: str) -> str:
        return local_bridge_url(self.base_url, room_ref)

    def map_message(self, message, *, room_ref: str) -> list[dict]:
        if not isinstance(message, dict):
            return []
        return [{"event_type": "chat", "room_ref": room_ref, "text": message.get("text", "")}]


class _FakeWebSocket:
    def __init__(self, messages: list[str | bytes]) -> None:
        self.messages = list(messages)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.messages:
            raise StopAsyncIteration
        return self.messages.pop(0)


class _FakeConnect:
    def __init__(self, ws: _FakeWebSocket) -> None:
        self.ws = ws
        self.url = ""
        self.kwargs: dict[str, object] = {}

    def __call__(self, url: str, **kwargs):
        self.url = url
        self.kwargs = kwargs
        return self

    async def __aenter__(self):
        return self.ws

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class _FailingConnect:
    def __call__(self, url: str, **kwargs):
        return self

    async def __aenter__(self):
        raise OSError("cookie=must-not-leak token=secret")

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class _FakeProcess:
    def __init__(self) -> None:
        self.returncode = None
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode


def test_local_bridge_url_appends_quoted_room_ref_without_query_or_userinfo():
    url = local_bridge_url("ws://127.0.0.1:1088/ws?token=must-not-leak", "room 42")

    assert url == "ws://127.0.0.1:1088/ws/room%2042"
    assert local_bridge_url("ws://user:pass@127.0.0.1:1088/ws", "room-42") == ""


def test_default_douyin_bridge_backend_is_the_only_bundled_binary_spec():
    backend = default_douyin_bridge_backend()

    assert backend.backend_id == "douyinlive"
    assert backend.executable_path.name == "douyinLive.exe"
    assert backend.args_factory(18088) == ["--port", "18088", "--log-level", "warn"]


def test_douyin_embedded_bridge_supervisor_accepts_replaceable_backend(tmp_path: Path):
    backend = DouyinBridgeBackend(
        backend_id="replacement",
        executable_path=tmp_path / "replacement.exe",
        args_factory=lambda port: ["serve", "--listen", str(port)],
    )
    supervisor = DouyinEmbeddedBridgeSupervisor(backend=backend)

    assert supervisor.backend_id == "replacement"


@pytest.mark.asyncio
async def test_live_bridge_transport_receives_json_from_local_websocket():
    ws = _FakeWebSocket([json.dumps({"text": "hello"})])
    connect = _FakeConnect(ws)
    events: list[LiveBridgeEvent] = []
    states: list[LiveBridgeState] = []
    transport = LiveBridgeTransport(connect_factory=connect)

    state = await transport.start(
        LiveBridgeStartRequest(
            room_ref="room-42",
            adapter=_Adapter(),
            emit=events.append,
            on_state=states.append,
        )
    )
    await asyncio.wait_for(transport._task, timeout=1.0)  # noqa: SLF001

    assert state.safe_state() == "connected"
    assert connect.url == "ws://127.0.0.1:1088/ws/room-42"
    assert connect.kwargs["proxy"] is None
    assert events[0].payload == {"event_type": "chat", "room_ref": "room-42", "text": "hello"}
    assert [item.safe_state() for item in states] == ["connecting", "connected", "receiving", "disconnected"]


@pytest.mark.asyncio
async def test_live_bridge_transport_isolates_observer_callback_failures():
    ws = _FakeWebSocket([json.dumps({"text": "hello"})])
    transport = LiveBridgeTransport(connect_factory=_FakeConnect(ws))

    def broken_observer(_value):
        raise RuntimeError("observer failed")

    state = await transport.start(
        LiveBridgeStartRequest(
            room_ref="room-42",
            adapter=_Adapter(),
            emit=broken_observer,
            on_state=broken_observer,
        )
    )
    await asyncio.wait_for(transport._task, timeout=1.0)  # noqa: SLF001

    assert state.safe_state() == "connected"


@pytest.mark.asyncio
async def test_live_bridge_transport_rejects_non_local_websocket_before_connecting():
    called = False

    def fail_if_called(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("non-local bridge URL must not be opened")

    transport = LiveBridgeTransport(connect_factory=fail_if_called)

    state = await transport.start(LiveBridgeStartRequest(room_ref="room-42", adapter=_Adapter("ws://example.com/ws")))

    assert called is False
    assert state.safe_state() == "unsupported"
    assert state.safe_error() == "external bridge URL must be local websocket"


@pytest.mark.asyncio
async def test_live_bridge_transport_open_failure_is_sanitized():
    transport = LiveBridgeTransport(connect_factory=_FailingConnect())

    state = await transport.start(LiveBridgeStartRequest(room_ref="room-42", adapter=_Adapter()))
    dumped = json.dumps(state.__dict__ if hasattr(state, "__dict__") else {"error": state.safe_error()})

    assert state.safe_state() == "disconnected"
    assert state.safe_error() == "external bridge failed: OSError"
    assert "must-not-leak" not in dumped
    assert "secret" not in dumped


@pytest.mark.asyncio
async def test_live_bridge_transport_resets_retry_budget_after_successful_reconnect():
    sockets = [
        _FakeWebSocket([json.dumps({"text": "first"})]),
        _FakeWebSocket([json.dumps({"text": "second"})]),
        _FakeWebSocket([json.dumps({"text": "third"})]),
    ]

    class _SequenceConnect(_FakeConnect):
        async def __aenter__(self):
            return sockets.pop(0)

    connect = _SequenceConnect(_FakeWebSocket([]))
    events: list[LiveBridgeEvent] = []
    states: list[LiveBridgeState] = []
    transport = LiveBridgeTransport(
        connect_factory=connect,
        reconnect_attempts=1,
        reconnect_sleep=lambda _delay: asyncio.sleep(0),
    )

    started = await transport.start(
        LiveBridgeStartRequest(
            room_ref="room-42",
            adapter=_Adapter(),
            emit=events.append,
            on_state=states.append,
        )
    )
    await asyncio.wait_for(transport._task, timeout=1.0)  # noqa: SLF001

    assert started.safe_state() == "connected"
    assert [event.payload["text"] for event in events] == ["first", "second", "third"]
    assert any(state.safe_state() == "reconnecting" for state in states)


@pytest.mark.asyncio
async def test_bridge_process_supervisor_starts_and_stops_bundled_executable(tmp_path: Path):
    executable = tmp_path / "douyinLive.exe"
    executable.write_bytes(b"fake exe")
    calls: list[dict[str, object]] = []
    cleaned: list[Path] = []
    fake_process = _FakeProcess()

    def process_factory(args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        return fake_process

    supervisor = BridgeProcessSupervisor(
        executable_path=executable,
        args_factory=lambda port: ["--port", str(port), "--log-level", "warn"],
        process_factory=process_factory,
        port_factory=lambda: 18088,
        port_waiter=lambda port, timeout: port == 18088,
        stale_process_cleaner=cleaned.append,
    )

    state = await supervisor.start()
    stopped = await supervisor.stop()

    assert state.ok is True
    assert cleaned == [executable]
    assert state.base_url == "ws://127.0.0.1:18088/ws"
    assert calls[0]["args"] == [str(executable), "--port", "18088", "--log-level", "warn"]
    assert calls[0]["kwargs"]["cwd"] == str(tmp_path)
    assert fake_process.terminated is True
    assert fake_process.killed is False
    assert stopped.port == 18088


@pytest.mark.asyncio
async def test_bridge_process_supervisor_ignores_stale_cleanup_failures(tmp_path: Path):
    executable = tmp_path / "douyinLive.exe"
    executable.write_bytes(b"fake exe")
    fake_process = _FakeProcess()

    def fail_cleanup(path: Path) -> None:
        raise OSError("cleanup failed")

    supervisor = BridgeProcessSupervisor(
        executable_path=executable,
        args_factory=lambda port: ["--port", str(port)],
        process_factory=lambda args, **kwargs: fake_process,
        port_factory=lambda: 18089,
        port_waiter=lambda port, timeout: port == 18089,
        stale_process_cleaner=fail_cleanup,
    )

    state = await supervisor.start()

    assert state.ok is True
    assert state.base_url == "ws://127.0.0.1:18089/ws"


@pytest.mark.asyncio
async def test_bridge_process_supervisor_serializes_concurrent_starts(tmp_path: Path):
    executable = tmp_path / "douyinLive.exe"
    executable.write_bytes(b"fake exe")
    entered_cleanup = threading.Event()
    release_cleanup = threading.Event()
    cleanup_calls = 0
    processes: list[_FakeProcess] = []

    def stale_cleaner(path: Path) -> None:
        nonlocal cleanup_calls
        cleanup_calls += 1
        entered_cleanup.set()
        assert release_cleanup.wait(timeout=2.0)

    def process_factory(args, **kwargs):
        process = _FakeProcess()
        processes.append(process)
        return process

    supervisor = BridgeProcessSupervisor(
        executable_path=executable,
        args_factory=lambda port: ["--port", str(port)],
        process_factory=process_factory,
        port_factory=lambda: 18090,
        port_waiter=lambda port, timeout: True,
        stale_process_cleaner=stale_cleaner,
    )

    first = asyncio.create_task(supervisor.start())
    assert await asyncio.to_thread(entered_cleanup.wait, 1.0)
    second = asyncio.create_task(supervisor.start())
    release_cleanup.set()
    first_state, second_state = await asyncio.gather(first, second)

    assert cleanup_calls == 1
    assert len(processes) == 1
    assert first_state == second_state
    assert first_state.port == 18090


@pytest.mark.asyncio
async def test_bridge_process_supervisor_stop_waits_for_start_lifecycle(tmp_path: Path):
    executable = tmp_path / "douyinLive.exe"
    executable.write_bytes(b"fake exe")
    entered_cleanup = threading.Event()
    release_cleanup = threading.Event()
    fake_process = _FakeProcess()

    def stale_cleaner(path: Path) -> None:
        entered_cleanup.set()
        assert release_cleanup.wait(timeout=2.0)

    supervisor = BridgeProcessSupervisor(
        executable_path=executable,
        args_factory=lambda port: ["--port", str(port)],
        process_factory=lambda args, **kwargs: fake_process,
        port_factory=lambda: 18091,
        port_waiter=lambda port, timeout: True,
        stale_process_cleaner=stale_cleaner,
    )

    starting = asyncio.create_task(supervisor.start())
    assert await asyncio.to_thread(entered_cleanup.wait, 1.0)
    stopping = asyncio.create_task(supervisor.stop())
    release_cleanup.set()

    assert (await starting).ok is True
    assert (await stopping).port == 18091
    assert fake_process.terminated is True


@pytest.mark.asyncio
async def test_bridge_process_supervisor_cleans_failed_start_without_lock_reentry(tmp_path: Path):
    executable = tmp_path / "douyinLive.exe"
    executable.write_bytes(b"fake exe")
    fake_process = _FakeProcess()
    supervisor = BridgeProcessSupervisor(
        executable_path=executable,
        args_factory=lambda port: ["--port", str(port)],
        process_factory=lambda args, **kwargs: fake_process,
        port_factory=lambda: 18092,
        port_waiter=lambda port, timeout: False,
    )

    state = await asyncio.wait_for(supervisor.start(), timeout=1.0)

    assert state.ok is False
    assert state.last_error == "bundled bridge did not open localhost port"
    assert fake_process.terminated is True


@pytest.mark.asyncio
async def test_bridge_process_supervisor_reports_missing_executable(tmp_path: Path):
    supervisor = BridgeProcessSupervisor(
        executable_path=tmp_path / "missing.exe",
        args_factory=lambda port: ["--port", str(port)],
    )

    state = await supervisor.start()

    assert state.ok is False
    assert state.last_error == "bundled bridge executable is missing"
