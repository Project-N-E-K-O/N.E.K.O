"""Control inbox admission, cancellation and ownership across event loops."""

from __future__ import annotations
import asyncio
import threading
import time
from types import SimpleNamespace
import pytest
from plugin.core import control_ingress as ingress_module
from plugin.logging_config import get_logger


async def test_expired_admission_does_not_consume_capacity():
    queue = asyncio.Queue(maxsize=1)
    ingress = ingress_module.ControlIngress(queue, lambda _: None)
    try:
        with pytest.raises(TimeoutError):
            await ingress.enqueue(
                {
                    "type": "OP",
                    "request_id": "expired",
                    "_request_deadline_monotonic": time.monotonic() - 1,
                }
            )
        assert queue.empty()
        await ingress.enqueue({"type": "OP", "request_id": "live"})
        assert ingress.take(queue.get_nowait())
    finally:
        ingress.close()


@pytest.mark.parametrize("operation", ["RPC_CANCEL", "RPC_DISCONNECT"])
async def test_cancel_covers_all_queued_duplicates_but_not_later_retry(operation):
    queue = asyncio.Queue(maxsize=4)
    ingress = ingress_module.ControlIngress(queue, lambda _: None)
    request = {"type": "OP", "request_id": "same", "_rpc_connection_id": "connection"}
    try:
        await ingress.enqueue(dict(request))
        await ingress.enqueue(dict(request))
        await ingress.enqueue({**request, "_rpc_connection_id": "other"})
        await ingress.enqueue(
            {
                **request,
                "type": operation,
                "_request_deadline_monotonic": time.monotonic() - 1,
            }
        )
        assert not ingress.take(queue.get_nowait())
        # A retry may arrive before every cancelled copy has drained.
        await ingress.enqueue(dict(request))
        assert not ingress.take(queue.get_nowait())
        assert ingress.take(queue.get_nowait())  # Other connection is untouched.
        assert ingress.take(queue.get_nowait())  # New retry is not cancelled.
    finally:
        ingress.close()


async def test_ingress_full_cancel_disconnect_and_restart_boundaries(monkeypatch):
    from plugin.core.rpc import RpcClosedError

    queue = asyncio.Queue(maxsize=1)
    controls = []
    ingress = ingress_module.ControlIngress(queue, controls.append)
    first = {"type": "OP", "request_id": "1", "_rpc_connection_id": "old"}
    await ingress.enqueue(first)
    with pytest.raises(asyncio.QueueFull):
        await ingress.enqueue(
            {"type": "OP", "request_id": "2", "_rpc_connection_id": "old"}
        )
    await ingress.enqueue(
        {"type": "RPC_CANCEL", "request_id": "1", "_rpc_connection_id": "old"}
    )
    assert not ingress.take(queue.get_nowait())
    await ingress.enqueue(first)
    assert ingress.notify_disconnect(
        {"type": "RPC_DISCONNECT", "_rpc_connection_id": "old"}
    )
    await asyncio.sleep(0)
    assert not ingress.take(queue.get_nowait())
    assert len(controls) == 2
    ingress.close()
    newer = ingress_module.ControlIngress(asyncio.Queue(maxsize=1), controls.append)
    with pytest.raises(RpcClosedError):
        await ingress.enqueue(first)
    await newer.enqueue({**first, "_rpc_connection_id": "new"})
    assert newer.take(newer.queue.get_nowait())
    newer.close()


async def test_full_ingress_returns_busy_and_retired_connection_does_not_enqueue(
    monkeypatch,
):
    from plugin.core.communication import PluginCommunicationResourceManager
    from plugin.core.state import state
    from plugin.core.rpc import RpcClosedError

    queue = asyncio.Queue(maxsize=1)
    ingress = ingress_module.ControlIngress(queue, lambda _: None)
    monkeypatch.setattr(state, "plugin_control_ingress", ingress)
    await ingress.enqueue({"type": "OP", "request_id": "existing", "from_plugin": "a"})
    replies = []
    manager = PluginCommunicationResourceManager(
        "research", SimpleNamespace(), get_logger("test.control_ingress")
    )

    async def reply(message):
        replies.append(message)

    manager.send_plugin_response = reply
    await asyncio.wait_for(
        manager._route_comm({"type": "OP", "request_id": "rejected"}), 0.5
    )
    assert replies[0]["error"]["code"] == "BUSY"
    assert replies[0]["request_id"] == "rejected"
    queue.get_nowait()
    with pytest.raises(RpcClosedError):
        await ingress.enqueue(
            {"type": "OP", "request_id": "retired"}, is_current=lambda: False
        )
    assert queue.empty()
    ingress.close()


@pytest.mark.parametrize("abandon", ["cancel", "disconnect", "close"])
async def test_handoff_abandoned_before_owner_execution_never_enqueues(abandon):
    from plugin.core.rpc import RpcClosedError

    ready, blocked, release = (threading.Event(), threading.Event(), threading.Event())
    holder = {}

    async def owner():
        loop = asyncio.get_running_loop()
        stop = asyncio.Event()
        ingress = ingress_module.ControlIngress(
            asyncio.Queue(maxsize=1), lambda _: None
        )
        holder.update(loop=loop, stop=stop, ingress=ingress)

        def park():
            blocked.set()
            release.wait(2)

        loop.call_soon(park)
        ready.set()
        await stop.wait()
        ingress.close()

    thread = threading.Thread(
        target=lambda: asyncio.run(owner()), name="candidate-handoff-owner"
    )
    thread.start()
    assert await asyncio.to_thread(ready.wait, 2)
    assert await asyncio.to_thread(blocked.wait, 2)
    alive = [True]
    task = asyncio.create_task(
        holder["ingress"].enqueue(
            {"type": "OP", "request_id": "late"}, is_current=lambda: alive[0]
        )
    )
    try:
        await asyncio.sleep(0)
        if abandon == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        elif abandon == "disconnect":
            alive[0] = False
        else:
            holder["loop"].call_soon_threadsafe(holder["ingress"].close)
        release.set()
        if abandon in {"disconnect", "close"}:
            with pytest.raises(RpcClosedError):
                await asyncio.wait_for(task, 1)

        async def barrier():
            await asyncio.sleep(0)
            return holder["ingress"].queue.empty()

        assert await asyncio.wrap_future(
            asyncio.run_coroutine_threadsafe(barrier(), holder["loop"])
        )
    finally:
        release.set()
        holder["loop"].call_soon_threadsafe(holder["stop"].set)
        await asyncio.to_thread(thread.join, 3)
        assert not thread.is_alive()


def test_child_reference_cleanup_drops_parent_ingress():
    from plugin.core.state import state

    child = SimpleNamespace(
        plugin_control_ingress=object(),
        _plugin_comm_queue=object(),
        _plugin_comm_lock=object(),
        plugin_hosts={},
        _plugin_downlink_senders={},
    )
    type(state).clear_inherited_plugin_references(child)
    assert child.plugin_control_ingress is None
    assert child._plugin_comm_queue is None


async def test_busy_reply_has_bounded_send_budget(monkeypatch):
    from plugin.core.communication import PluginCommunicationResourceManager
    from plugin.core.state import state

    queue = asyncio.Queue(maxsize=1)
    queue.put_nowait({"type": "EXISTING"})
    ingress = ingress_module.ControlIngress(queue, lambda _: None)
    monkeypatch.setattr(state, "plugin_control_ingress", ingress)
    manager = PluginCommunicationResourceManager(
        "research", SimpleNamespace(), get_logger("test.control_ingress")
    )
    blocked_send_cancelled = asyncio.Event()

    async def blocked_send(message):
        try:
            await asyncio.Event().wait()
        finally:
            blocked_send_cancelled.set()

    manager.send_plugin_response = blocked_send
    await asyncio.wait_for(manager._route_comm({"type": "OP", "request_id": "busy"}), 1)
    assert blocked_send_cancelled.is_set()
    ingress.close()


async def test_expired_foreign_handoff_never_reaches_the_queue():
    from plugin.core.control_ingress import ControlIngress

    ready, blocked, release = (threading.Event(), threading.Event(), threading.Event())
    holder, errors = ({}, [])

    async def owner():
        loop = asyncio.get_running_loop()
        loop.set_debug(True)
        stop = asyncio.Event()
        ingress = ControlIngress(asyncio.Queue(), lambda _: None)
        holder.update(loop=loop, stop=stop, ingress=ingress)

        def park():
            blocked.set()
            assert release.wait(3)

        loop.call_soon(park)
        ready.set()
        try:
            await stop.wait()
        finally:
            ingress.close()

    def serve():
        try:
            asyncio.run(owner())
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=serve, name="review-expired-handoff")
    thread.start()
    try:
        assert await asyncio.to_thread(ready.wait, 2)
        assert await asyncio.to_thread(blocked.wait, 2)
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(
                holder["ingress"].enqueue(
                    {
                        "type": "ECHO",
                        "request_id": "expired",
                        "from_plugin": "review",
                        "_request_deadline_monotonic": time.monotonic() + 0.02,
                    }
                ),
                1,
            )
        release.set()

        async def barrier():
            await asyncio.sleep(0)
            return holder["ingress"].queue.empty()

        assert await asyncio.wrap_future(
            asyncio.run_coroutine_threadsafe(barrier(), holder["loop"])
        )
    finally:
        release.set()
        if "loop" in holder:
            holder["loop"].call_soon_threadsafe(holder["stop"].set)
        await asyncio.to_thread(thread.join, 3)
        assert not thread.is_alive() and (not errors)
