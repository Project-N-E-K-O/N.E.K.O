from __future__ import annotations

import asyncio
import sys
import threading

import pytest

from plugin.core.state import state
from plugin.server.domain.errors import ServerDomainError
from plugin.server.messaging import request_router as module


@pytest.fixture
async def router(monkeypatch):
    monkeypatch.setattr(state, "_plugin_comm_queue", asyncio.Queue())
    instance = module.PluginRouter()
    try:
        yield instance
    finally:
        await instance.stop()


async def test_validation_unknown_and_domain_failure_use_one_reply_contract(router):
    bad = await router._dispatcher.dispatch({"type": "X", "request_id": "1"})
    assert bad["error"]["code"] == "INVALID_REQUEST"
    unknown = await router._dispatcher.dispatch(
        {"type": "X", "request_id": "2", "from_plugin": "a"}
    )
    assert unknown["error"]["code"] == "UNKNOWN_OPERATION"


async def test_failed_request_does_not_stop_the_queue(router, monkeypatch):
    replies = asyncio.Queue()

    async def fail(ctx, request):
        raise ServerDomainError("PLUGIN_EXECUTION_FAILED", "target rejected input", 502, details={"x": 1})

    async def good(ctx, request):
        return "ok"

    router._handlers.update(FAIL=fail, GOOD=good)
    monkeypatch.setattr(state, "get_downlink_sender", lambda plugin: replies.put)
    await router.start()
    await state.plugin_comm_queue.put(
        {"type": "FAIL", "request_id": "1", "from_plugin": "a"}
    )
    await state.plugin_comm_queue.put(
        {"type": "GOOD", "request_id": "2", "from_plugin": "a"}
    )
    received = {
        reply["request_id"]: reply
        for reply in [
            await asyncio.wait_for(replies.get(), 1),
            await asyncio.wait_for(replies.get(), 1),
        ]
    }
    assert received["1"]["error"]["message"] == "target rejected input"
    assert received["1"]["error"]["details"] == {"x": 1}
    assert received["2"]["result"] == "ok"


async def test_response_delivery_failure_is_logged_without_fallback(
    router, monkeypatch, caplog
):
    async def good(ctx, request):
        return "ok"

    router._handlers["GOOD"] = good
    monkeypatch.setattr(state, "get_downlink_sender", lambda plugin: None)
    await router.start()
    await state.plugin_comm_queue.put(
        {"type": "GOOD", "request_id": "lost", "from_plugin": "a"}
    )
    for _ in range(100):
        if "RPC reply delivery failed" in caplog.text:
            break
        await asyncio.sleep(0.001)
    assert "RPC reply delivery failed" in caplog.text
    assert "lost" in caplog.text


async def test_blocked_overload_reply_does_not_stall_other_requests(
    router, monkeypatch
):
    replies = asyncio.Queue()
    blocked = asyncio.Event()

    async def good(ctx, request):
        return "ok"

    async def send(reply):
        if reply["to_plugin"] == "slow":
            await blocked.wait()
        await replies.put(reply)

    router._handlers["GOOD"] = good
    monkeypatch.setattr(router, "_send_response", send)
    await router.start()
    router._dispatcher._limit = 0
    for index in range(8):
        await state.plugin_comm_queue.put(
            {"type": "GOOD", "request_id": f"s{index}", "from_plugin": "slow"}
        )
    await state.plugin_comm_queue.put(
        {"type": "GOOD", "request_id": "fast", "from_plugin": "fast"}
    )
    reply = await asyncio.wait_for(replies.get(), 0.2)
    assert reply["request_id"] == "fast"
    assert reply["error"]["code"] == "BUSY"


async def test_healthy_downlink_answers_every_request_in_a_queued_burst(router, monkeypatch):
    replies = asyncio.Queue()

    async def good(ctx, request):
        return "ok"

    router._handlers["GOOD"] = good
    monkeypatch.setattr(state, "get_downlink_sender", lambda _: replies.put)
    for index in range(600):
        state.plugin_comm_queue.put_nowait(
            {"type": "GOOD", "request_id": str(index), "from_plugin": "healthy"}
        )
    await router.start()
    async with asyncio.timeout(2):
        received = [await replies.get() for _ in range(600)]
    assert {reply["request_id"] for reply in received} == {str(i) for i in range(600)}
    assert all(
        reply["result"] == "ok" or reply["error"]["code"] == "BUSY"
        for reply in received
    )


# A send that spans several loop turns, not wall-clock time: a real sleep can
# outlast _send_busy's 0.25s timeout on a starved runner and drop the reply.
@pytest.mark.parametrize("reply_turns", [0, 8])
async def test_saturated_dispatcher_delivers_busy_replies_for_the_whole_burst(
    router, monkeypatch, reply_turns
):
    replies = asyncio.Queue()
    release = asyncio.Event()

    async def held(ctx, request):
        await release.wait()
        return "ok"

    async def send(reply):
        for _ in range(reply_turns):
            await asyncio.sleep(0)
        await replies.put(reply)

    router._handlers["HELD"] = held
    monkeypatch.setattr(state, "get_downlink_sender", lambda _: send)
    for index in range(600):
        state.plugin_comm_queue.put_nowait(
            {"type": "HELD", "request_id": str(index), "from_plugin": "healthy"}
        )
    await router.start()
    try:
        # Hang guard only; no step here waits on wall-clock time.
        async with asyncio.timeout(30):
            busy = [await replies.get() for _ in range(344)]
            assert all(reply["error"]["code"] == "BUSY" for reply in busy)
            release.set()
            completed = [await replies.get() for _ in range(256)]
    finally:
        release.set()
    assert all(reply["result"] == "ok" for reply in completed)
    assert {reply["request_id"] for reply in busy + completed} == {str(i) for i in range(600)}


@pytest.mark.parametrize("control", ["RPC_CANCEL", "RPC_DISCONNECT"])
async def test_full_overload_reply_budget_still_allows_control_messages(router, monkeypatch, control):
    entered = asyncio.Event()
    cancelled = asyncio.Event()
    blocked = asyncio.Event()

    async def held(ctx, request):
        entered.set()
        try:
            await blocked.wait()
        finally:
            cancelled.set()

    router._handlers["HELD"] = held

    async def blocked_send(reply):
        await blocked.wait()

    monkeypatch.setattr(state, "get_downlink_sender", lambda _: blocked_send)
    await router.start()
    router._dispatcher._limit = 1
    state.plugin_comm_queue.put_nowait(
        {"type": "HELD", "request_id": "active", "from_plugin": "slow"}
    )
    await asyncio.wait_for(entered.wait(), timeout=1)
    for index in range(300):
        state.plugin_comm_queue.put_nowait(
            {"type": "HELD", "request_id": str(index), "from_plugin": "slow"}
        )
    state.plugin_comm_queue.put_nowait(
        {"type": control, "request_id": "active", "from_plugin": "slow"}
    )
    await asyncio.wait_for(cancelled.wait(), timeout=1)


async def test_stop_snapshot_cannot_combine_old_loop_and_new_ingress(monkeypatch):
    from plugin.core.state import state
    from plugin.server.messaging.request_router import PluginRouter

    monkeypatch.setattr(state, "_plugin_comm_queue", asyncio.Queue())
    monkeypatch.setattr(state, "plugin_control_ingress", None)
    router = PluginRouter()
    ready, snapshot_paused, resume_snapshot = threading.Event(), threading.Event(), threading.Event()
    holder, errors = {}, []

    async def owner():
        holder.update(loop=asyncio.get_running_loop(), end=asyncio.Event())
        await router.start()
        ready.set()
        await holder["end"].wait()

    def run_owner():
        try:
            asyncio.run(owner())
        except BaseException as exc:
            errors.append(exc)

    owner_thread = threading.Thread(target=run_owner, name="review-old-router-owner")
    owner_thread.start()
    assert await asyncio.to_thread(ready.wait, 2)
    stopped = threading.Event()

    def stale_stop():
        saw_first_line, paused = False, False
        def trace(frame, event, arg):
            nonlocal saw_first_line, paused
            if event == "line" and frame.f_code is PluginRouter.stop.__code__:
                if not saw_first_line:
                    saw_first_line = True
                elif not paused:
                    paused = True
                    snapshot_paused.set()
                    assert resume_snapshot.wait(3)
            return trace
        sys.settrace(trace)
        try:
            asyncio.run(router.stop())
        except BaseException as exc:
            errors.append(exc)
        finally:
            sys.settrace(None)
            stopped.set()

    stale_thread = threading.Thread(target=stale_stop, name="review-stale-stop")
    stale_thread.start()
    current_task = None
    try:
        assert await asyncio.to_thread(snapshot_paused.wait, 2)
        await router.stop()
        await router.start()
        current_ingress, current_task = router._ingress, router._router_task
        resume_snapshot.set()
        assert await asyncio.to_thread(stopped.wait, 2)
        assert state.plugin_control_ingress is current_ingress and not current_ingress._closed, (
            "A stale stop captured an old loop and the new ingress, then closed the restarted router"
        )
        assert not current_task.done()
        assert not errors
        replies = asyncio.Queue()
        async def echo(ctx, request):
            return "alive"
        router._handlers["ECHO"] = echo
        monkeypatch.setattr(state, "get_downlink_sender", lambda _: replies.put)
        await current_ingress.enqueue({"type": "ECHO", "request_id": "after-restart", "from_plugin": "review"})
        assert (await asyncio.wait_for(replies.get(), 1))["result"] == "alive"
    finally:
        resume_snapshot.set()
        try:
            await asyncio.wait_for(router.stop(), 1)
        finally:
            if current_task:
                current_task.cancel()
                await asyncio.gather(current_task, return_exceptions=True)
            holder["loop"].call_soon_threadsafe(holder["end"].set)
            await asyncio.to_thread(stale_thread.join, 3)
            await asyncio.to_thread(owner_thread.join, 3)
            assert not owner_thread.is_alive() and not stale_thread.is_alive()
        assert not errors


async def test_concurrent_recovery_does_not_stop_a_replacement_router(monkeypatch):
    import ast
    import inspect
    import textwrap

    from plugin.core.state import state
    from plugin.server.messaging.request_router import PluginRouter

    monkeypatch.setattr(state, "_plugin_comm_queue", asyncio.Queue())
    monkeypatch.setattr(state, "plugin_control_ingress", None)
    router = PluginRouter()
    await router.start()
    # A completed receive task is the condition that enters start's recovery path.
    router._router_task.cancel()
    await asyncio.gather(router._router_task, return_exceptions=True)
    paused, resume, restarted = threading.Event(), threading.Event(), threading.Event()
    worker, errors = {}, []
    entered, cancelled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def held(ctx, request):
        entered.set()
        try:
            await release.wait()
            return "ok"
        except asyncio.CancelledError:
            cancelled.set()
            raise

    router._handlers["HELD"] = held
    replies = asyncio.Queue()
    monkeypatch.setattr(state, "get_downlink_sender", lambda _: replies.put)

    async def recover():
        worker.update(loop=asyncio.get_running_loop(), end=asyncio.Event())
        await router.start()
        restarted.set()
        await worker["end"].wait()

    source, source_line = inspect.getsourcelines(PluginRouter.start)
    tree = ast.parse(textwrap.dedent("".join(source)))
    # Pause after the failed generation is selected, immediately before cleanup.
    cleanup_line = source_line + min(node.lineno for node in ast.walk(tree) if isinstance(node, ast.Await)) - 1

    def run_recovery():
        did_pause = False
        def trace(frame, event, arg):
            nonlocal did_pause
            if event == "line" and frame.f_code is PluginRouter.start.__code__ and frame.f_lineno == cleanup_line and not did_pause:
                did_pause = True
                paused.set()
                assert resume.wait(3)
            return trace
        sys.settrace(trace)
        try:
            asyncio.run(recover())
        except BaseException as exc:
            errors.append(exc)
            restarted.set()
        finally:
            sys.settrace(None)

    thread = threading.Thread(target=run_recovery, name="review-concurrent-router-recovery")
    thread.start()
    try:
        assert await asyncio.to_thread(paused.wait, 2)
        await router.start()
        replacement = router._ingress
        await replacement.enqueue({"type": "HELD", "request_id": "replacement-call", "from_plugin": "review"})
        await asyncio.wait_for(entered.wait(), 1)
        resume.set()
        assert await asyncio.to_thread(restarted.wait, 2)
        assert not errors
        assert state.plugin_control_ingress is replacement and not replacement._closed, (
            "A delayed start called stop() on a healthy replacement router"
        )
        assert not cancelled.is_set(), "Recovery cancelled an unrelated request on the replacement"
        release.set()
        assert (await asyncio.wait_for(replies.get(), 1))["result"] == "ok"
    finally:
        resume.set()
        release.set()
        try:
            await asyncio.wait_for(router.stop(), 2)
        finally:
            if "loop" in worker:
                worker["loop"].call_soon_threadsafe(worker["end"].set)
            await asyncio.to_thread(thread.join, 3)
            assert not thread.is_alive()


async def test_retired_generation_cleanup_ignores_its_closed_owner_loop(monkeypatch):
    from plugin.core.state import state
    from plugin.server.messaging.request_router import PluginRouter

    monkeypatch.setattr(state, "_plugin_comm_queue", asyncio.Queue())
    monkeypatch.setattr(state, "plugin_control_ingress", None)
    router = PluginRouter()
    ready = threading.Event()
    holder, errors = {}, []

    async def old_run():
        holder.update(loop=asyncio.get_running_loop(), stop=asyncio.Event())
        await router.start()
        holder["old"] = router._ingress
        ready.set()
        await holder["stop"].wait()
        await router.stop()

    def run_owner():
        try:
            asyncio.run(old_run())
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=run_owner, name="review-retired-router-owner")
    thread.start()
    try:
        assert await asyncio.to_thread(ready.wait, 2)
        holder["loop"].call_soon_threadsafe(holder["stop"].set)
        await asyncio.to_thread(thread.join, 3)
        assert not thread.is_alive() and not errors
        assert holder["old"].loop.is_closed()
        await router.start()
        current = router._ingress
        await router._stop_ingress(holder["old"])
        assert state.plugin_control_ingress is current and not current._closed
        assert not router._router_task.done()
    finally:
        try:
            await asyncio.wait_for(router.stop(), 2)
        finally:
            if thread.is_alive() and "loop" in holder:
                holder["loop"].call_soon_threadsafe(holder["stop"].set)
            await asyncio.to_thread(thread.join, 3)
            assert not thread.is_alive()


async def test_cancelled_stop_waiter_leaves_cleanup_owned_and_restart_usable(monkeypatch):
    from plugin.core.state import state
    from plugin.server.messaging.request_router import PluginRouter
    monkeypatch.setattr(state, '_plugin_comm_queue', asyncio.Queue())
    monkeypatch.setattr(state, 'plugin_control_ingress', None)
    entered, cleaning, release = (asyncio.Event(), asyncio.Event(), asyncio.Event())
    router = PluginRouter()
    replies = asyncio.Queue()
    monkeypatch.setattr(state, 'get_downlink_sender', lambda _: replies.put)

    async def held(ctx, request):
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cleaning.set()
            await release.wait()
            raise

    async def echo(ctx, request):
        return 'alive'
    router._handlers.update(HELD=held, ECHO=echo)
    waiter = None
    try:
        await router.start()
        await router._ingress.enqueue({'type': 'HELD', 'request_id': 'held', 'from_plugin': 'review'})
        await asyncio.wait_for(entered.wait(), 1)
        waiter = asyncio.create_task(router.stop())
        await asyncio.wait_for(cleaning.wait(), 1)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert not router._stop_task.done()
        release.set()
        await asyncio.wait_for(asyncio.shield(router._stop_task), 1)
        assert router._ingress is None and state.plugin_control_ingress is None
        await router.start()
        await router._ingress.enqueue({'type': 'ECHO', 'request_id': 'next', 'from_plugin': 'review'})
        assert (await asyncio.wait_for(replies.get(), 1))['result'] == 'alive'
    finally:
        release.set()
        if waiter:
            await asyncio.gather(waiter, return_exceptions=True)
        await asyncio.wait_for(router.stop(), 2)


async def test_fault_recovery_cancels_only_old_requests_and_reopens_delivery(monkeypatch):
    from plugin.core.state import state
    from plugin.server.messaging.request_router import PluginRouter
    monkeypatch.setattr(state, '_plugin_comm_queue', asyncio.Queue())
    monkeypatch.setattr(state, 'plugin_control_ingress', None)
    entered, cancelled = (asyncio.Event(), asyncio.Event())
    router = PluginRouter()
    replies = asyncio.Queue()
    monkeypatch.setattr(state, 'get_downlink_sender', lambda _: replies.put)

    async def held(ctx, request):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async def echo(ctx, request):
        return 'new'
    router._handlers.update(HELD=held, ECHO=echo)
    try:
        await router.start()
        old = router._ingress
        await old.enqueue({'type': 'HELD', 'request_id': 'old', 'from_plugin': 'review'})
        await asyncio.wait_for(entered.wait(), 1)
        router._router_task.cancel()
        await asyncio.gather(router._router_task, return_exceptions=True)
        await router.start()
        assert cancelled.is_set() and old._closed
        assert router._ingress is not old
        await router._ingress.enqueue({'type': 'ECHO', 'request_id': 'new', 'from_plugin': 'review'})
        reply = await asyncio.wait_for(replies.get(), 1)
        assert reply['request_id'] == 'new' and reply['result'] == 'new'
    finally:
        await asyncio.wait_for(router.stop(), 2)
