"""Memory shutdown queues the final stats save first and waits for it boundedly."""

import asyncio
from concurrent.futures import Future
from types import SimpleNamespace

import pytest


@pytest.fixture
def memory_shutdown(monkeypatch):
    from app.memory_server import runtime, knowledge_routes, long_term_sync
    from memory import embeddings
    from utils import token_tracker

    rig = SimpleNamespace(
        runtime=runtime,
        events=[],
        saved=Future(),
        long_term_close=None,
        knowledge_stop=None,
    )

    def submit_save(**kwargs):
        rig.events.append("save-submitted")
        return rig.saved

    async def long_term_close():
        rig.events.append("long-term-close")
        if rig.long_term_close is not None:
            await rig.long_term_close()

    async def knowledge_stop():
        rig.events.append("cleanup")
        if rig.knowledge_stop is not None:
            await rig.knowledge_stop()

    async def release_embedding_service():
        rig.events.append("embedding-released")

    tracker = SimpleNamespace(submit_save=submit_save)
    monkeypatch.setattr(token_tracker.TokenTracker, "get_instance", classmethod(lambda cls: tracker))
    monkeypatch.setattr(long_term_sync, "close", long_term_close)
    monkeypatch.setattr(runtime, "_recent_sync_wake_task", None)
    monkeypatch.setattr(runtime, "embedding_warmup_worker", None)
    monkeypatch.setattr(runtime, "time_manager", None)
    monkeypatch.setattr(runtime, "_deferred_time_managers", [])
    monkeypatch.setattr(knowledge_routes, "stop_knowledge_runtime", knowledge_stop)
    monkeypatch.setattr(embeddings, "release_embedding_service", release_embedding_service)
    yield rig
    if not rig.saved.done():
        rig.saved.set_result(None)


async def _stall_forever():
    await asyncio.Event().wait()


@pytest.mark.asyncio
async def test_final_save_is_queued_before_the_first_cleanup_wait(memory_shutdown):
    rig = memory_shutdown
    entered = asyncio.Event()

    async def stuck():
        # A long-term memory file commit that close() has to wait for.
        entered.set()
        await _stall_forever()

    rig.long_term_close = stuck
    task = asyncio.create_task(rig.runtime.shutdown_event_handler())
    try:
        await asyncio.wait_for(entered.wait(), 2)
        assert rig.events == ["save-submitted", "long-term-close"]
        # The launcher gives up on the stalled cleanup; the save is already queued.
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert not rig.saved.cancelled()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        if not rig.saved.done():
            rig.saved.set_result(None)
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_cancelling_the_wait_for_the_save_does_not_cancel_it(memory_shutdown):
    rig = memory_shutdown
    task = asyncio.create_task(rig.runtime.shutdown_event_handler())
    try:
        # Cleanup finishes normally; the handler is then waiting for the save.
        async with asyncio.timeout(2):
            while "embedding-released" not in rig.events:
                await asyncio.sleep(.01)
        await asyncio.sleep(.02)
        assert not task.done()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert task.cancelled(), "the caller's cancellation goes on up"
        assert rig.events == ["save-submitted", "long-term-close", "cleanup", "embedding-released"]
        assert not rig.saved.cancelled() and not rig.saved.done()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        if not rig.saved.done():
            rig.saved.set_result(None)
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_waiting_for_the_save_stops_at_the_shutdown_deadline(memory_shutdown, monkeypatch):
    rig = memory_shutdown
    monkeypatch.setattr(rig.runtime, "_SHUTDOWN_SAVE_WAIT_DEADLINE_SECONDS", .05)
    # A save that never finishes (a stuck fsync): shutdown still returns.
    await asyncio.wait_for(rig.runtime.shutdown_event_handler(), 2)
    assert rig.events == ["save-submitted", "long-term-close", "cleanup", "embedding-released"]
    assert not rig.saved.cancelled() and not rig.saved.done()
    rig.saved.set_result(None)
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_a_save_done_by_the_deadline_is_not_reported_as_timed_out(memory_shutdown, monkeypatch):
    rig = memory_shutdown
    warnings = []
    monkeypatch.setattr(rig.runtime.logger, "warning", lambda *args, **kwargs: warnings.append(args))
    monkeypatch.setattr(rig.runtime, "_SHUTDOWN_SAVE_WAIT_DEADLINE_SECONDS", 0.0)

    async def finish_save():
        # Done in the save thread while cleanup ran past the deadline.
        rig.saved.set_result(None)

    rig.knowledge_stop = finish_save
    await asyncio.wait_for(rig.runtime.shutdown_event_handler(), 2)
    assert not [w for w in warnings if "最终统计保存" in str(w[0])]


@pytest.mark.asyncio
async def test_shutdown_waits_for_a_save_that_finishes_in_time(memory_shutdown):
    rig = memory_shutdown
    task = asyncio.create_task(rig.runtime.shutdown_event_handler())
    try:
        async with asyncio.timeout(2):
            while "embedding-released" not in rig.events:
                await asyncio.sleep(.01)
        await asyncio.sleep(.02)
        assert not task.done(), "a save still under way is waited for"
        rig.saved.set_result(None)
        await asyncio.wait_for(task, 2)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
