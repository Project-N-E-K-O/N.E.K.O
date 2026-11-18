"""Memory shutdown queues the final stats save before a cleanup that may stall."""

import asyncio
from concurrent.futures import Future
from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_final_save_is_queued_before_cleanup_and_survives_its_cancellation(monkeypatch):
    from app.memory_server import runtime, knowledge_routes, long_term_sync
    from utils import token_tracker

    events = []
    saved = Future()
    entered = asyncio.Event()

    def submit_save(**kwargs):
        events.append("save-submitted")
        return saved

    async def nothing():
        return None

    async def stuck_knowledge_stop():
        events.append("cleanup")
        entered.set()
        await asyncio.Event().wait()

    tracker = SimpleNamespace(submit_save=submit_save)
    monkeypatch.setattr(token_tracker.TokenTracker, "get_instance", classmethod(lambda cls: tracker))
    monkeypatch.setattr(long_term_sync, "close", nothing)
    monkeypatch.setattr(runtime, "_recent_sync_wake_task", None)
    monkeypatch.setattr(runtime, "embedding_warmup_worker", None)
    monkeypatch.setattr(runtime, "time_manager", None)
    monkeypatch.setattr(runtime, "_deferred_time_managers", [])
    monkeypatch.setattr(knowledge_routes, "stop_knowledge_runtime", stuck_knowledge_stop)

    task = asyncio.create_task(runtime.shutdown_event_handler())
    try:
        await asyncio.wait_for(entered.wait(), 2)
        # The launcher gives up on a stalled cleanup; the save is already queued.
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert events == ["save-submitted", "cleanup"]
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        if not saved.done():
            saved.set_result(None)
