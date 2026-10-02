from __future__ import annotations

import asyncio
import time

import pytest

from plugin.core import communication, zmq_transport
from plugin.core.communication import PluginCommunicationResourceManager
from plugin.core.zmq_transport import CH_RES

# Far above anything shutdown may take: a consumer that waits out its poll
# makes the elapsed-time assertions below fail by an order of magnitude.
_HUGE_POLL_S = 30.0
# Before the fix three consumers each used up the 0.5s graceful window
# (>= 1.5s total); this bound leaves room for a loaded runner.
_FAST_SHUTDOWN_S = 1.0


async def _wait_until(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached before deadline")
        await asyncio.sleep(0.001)


class _Logger:
    def debug(self, *args, **kwargs):
        return None

    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None

    def error(self, *args, **kwargs):
        return None

    def exception(self, *args, **kwargs):
        return None


class _ParkedTransport:
    """Every blocking poll parks until cancelled; a zero-timeout read returns backlog."""

    def __init__(self) -> None:
        self.backlog: dict[str, list] = {"recv": [], "recv_message": [], "recv_image": []}
        self.parked: dict[str, asyncio.Event] = {k: asyncio.Event() for k in self.backlog}
        self.drain_reads: list[str] = []

    async def _poll(self, name: str, timeout_ms):
        if timeout_ms == 0:
            self.drain_reads.append(name)
            queued = self.backlog[name]
            return queued.pop(0) if queued else None
        self.parked[name].set()
        await asyncio.Event().wait()

    async def recv(self, timeout_ms=None):
        return await self._poll("recv", timeout_ms)

    async def recv_message(self, timeout_ms=None):
        return await self._poll("recv_message", timeout_ms)

    async def recv_image(self, timeout_ms=None):
        return await self._poll("recv_image", timeout_ms)

    async def send_command(self, msg):
        return None


async def _start_parked(monkeypatch) -> tuple[PluginCommunicationResourceManager, _ParkedTransport]:
    monkeypatch.setattr(communication, "QUEUE_GET_TIMEOUT", _HUGE_POLL_S)
    transport = _ParkedTransport()
    manager = PluginCommunicationResourceManager(
        plugin_id="demo",
        transport=transport,  # type: ignore[arg-type]
        logger=_Logger(),
    )
    await manager.start()
    for parked in transport.parked.values():
        await asyncio.wait_for(parked.wait(), timeout=2.0)
    return manager, transport


@pytest.mark.plugin_unit
async def test_shutdown_wakes_parked_consumers_instead_of_waiting_out_the_poll(monkeypatch) -> None:
    manager, transport = await _start_parked(monkeypatch)
    consumers = [
        manager._uplink_consumer_task,
        manager._message_consumer_task,
        manager._image_consumer_task,
    ]

    started = time.perf_counter()
    await manager.shutdown(timeout=5.0)
    elapsed = time.perf_counter() - started

    assert elapsed < _FAST_SHUTDOWN_S
    assert all(task is not None and task.done() for task in consumers)
    # Each consumer took the final non-blocking read before exiting.
    assert sorted(transport.drain_reads) == ["recv", "recv_image", "recv_message"]


@pytest.mark.plugin_unit
async def test_a_result_already_queued_at_shutdown_still_resolves_its_request(monkeypatch) -> None:
    manager, transport = await _start_parked(monkeypatch)
    future = asyncio.get_running_loop().create_future()
    manager._pending_futures["req-stop"] = future
    result = {"req_id": "req-stop", "success": True, "data": {"stopped": True}}
    transport.backlog["recv"].append((CH_RES, result))

    await manager.shutdown(timeout=5.0)

    assert future.done() and not future.cancelled()
    assert future.result() == result


@pytest.mark.plugin_unit
async def test_a_request_with_no_result_is_cancelled_so_its_caller_does_not_hang(monkeypatch) -> None:
    manager, _transport = await _start_parked(monkeypatch)
    caller = asyncio.create_task(
        manager._send_command_and_wait_local("req-lost", {"type": "TRIGGER"}, None, "demo")
    )
    await _wait_until(lambda: "req-lost" in manager._pending_futures)

    await manager.shutdown(timeout=5.0)

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(caller, timeout=2.0)
    assert manager._pending_futures == {}


@pytest.mark.plugin_unit
async def test_a_cancel_that_is_not_the_shutdown_wakeup_still_stops_the_consumer(monkeypatch) -> None:
    manager, transport = await _start_parked(monkeypatch)
    task = manager._uplink_consumer_task
    assert task is not None

    # The consumer loop turns CancelledError into `break`, so the task ends
    # normally; what matters is that it stopped without the shutdown drain.
    task.cancel()
    await asyncio.wait_for(task, timeout=2.0)

    assert task.done()
    assert "recv" not in transport.drain_reads
    await manager.shutdown(timeout=5.0)


@pytest.mark.plugin_unit
async def test_zero_timeout_shutdown_skips_the_final_read(monkeypatch) -> None:
    manager, transport = await _start_parked(monkeypatch)
    transport.backlog["recv_image"].append(({"name": "late.png"}, b"x"))
    consumers = [
        manager._uplink_consumer_task,
        manager._message_consumer_task,
        manager._image_consumer_task,
    ]

    # wait_for(timeout=0) cancels again before the woken consumer resumes;
    # that second cancel must win over the wake-up's final read.
    await manager.shutdown(timeout=0)

    assert all(task is not None and task.done() for task in consumers)
    assert transport.drain_reads == []


@pytest.mark.plugin_unit
async def test_shutdown_over_real_sockets_does_not_wait_for_the_poll(monkeypatch) -> None:
    monkeypatch.setattr(communication, "QUEUE_GET_TIMEOUT", _HUGE_POLL_S)
    host = zmq_transport.HostTransport()
    manager = PluginCommunicationResourceManager(
        plugin_id="demo",
        transport=host,
        logger=_Logger(),
    )
    try:
        await manager.start()
        consumers = [
            manager._uplink_consumer_task,
            manager._message_consumer_task,
            manager._image_consumer_task,
        ]
        assert all(task is not None for task in consumers)
        await _wait_until(lambda: all(task in manager._polling_tasks for task in consumers))

        started = time.perf_counter()
        await manager.shutdown(timeout=5.0)
        elapsed = time.perf_counter() - started

        assert elapsed < _FAST_SHUTDOWN_S
        assert all(task is not None and task.done() for task in consumers)
    finally:
        host.close()
