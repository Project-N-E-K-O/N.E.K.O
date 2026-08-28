from __future__ import annotations

import asyncio
import threading
import time

import pytest

from knowledge import indexer
from knowledge.vector_index import _KnowledgeInferenceCoordinator


@pytest.mark.asyncio
async def test_indexer_lifecycle_is_idempotent_and_wakeable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    started = asyncio.Event()
    awakened = asyncio.Event()
    cleanup_order: list[str] = []

    async def fake_run(_root, wake_event: asyncio.Event) -> None:
        started.set()
        await wake_event.wait()
        awakened.set()
        await asyncio.Event().wait()

    async def fake_release() -> None:
        cleanup_order.append("release")

    async def fake_drain(*, deadline_monotonic=None) -> bool:
        assert deadline_monotonic is not None
        cleanup_order.append("drain")
        return True

    monkeypatch.setattr(indexer, "_run_indexer", fake_run)
    monkeypatch.setattr(
        "utils.local_embedding_runtime.release_local_embedding_service",
        fake_release,
    )
    monkeypatch.setattr(
        "knowledge.vector_index.drain_knowledge_embedding_inference",
        fake_drain,
    )

    assert indexer.start_knowledge_indexer(tmp_path) is True
    assert indexer.start_knowledge_indexer(tmp_path) is False
    await asyncio.wait_for(started.wait(), timeout=1.0)

    indexer.notify_knowledge_index_changed()
    await asyncio.wait_for(awakened.wait(), timeout=1.0)
    assert await indexer.stop_knowledge_indexer() is True
    assert cleanup_order == ["drain", "release"]


@pytest.mark.asyncio
async def test_indexer_shutdown_abandons_cancellation_resistant_task(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    started = asyncio.Event()
    release_task = asyncio.Event()
    released_model = False

    async def stubborn_run(_root, _wake_event: asyncio.Event) -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await release_task.wait()

    async def fake_release() -> None:
        nonlocal released_model
        released_model = True

    monkeypatch.setattr(indexer, "_run_indexer", stubborn_run)
    monkeypatch.setattr(indexer, "INDEXER_CANCEL_GRACE_SECONDS", 0.01)
    monkeypatch.setattr(
        "utils.local_embedding_runtime.release_local_embedding_service",
        fake_release,
    )

    assert indexer.start_knowledge_indexer(tmp_path) is True
    task = indexer._TASK
    assert task is not None
    await asyncio.wait_for(started.wait(), timeout=1.0)

    before = time.monotonic()
    assert await indexer.stop_knowledge_indexer(timeout_seconds=0.02) is False
    assert time.monotonic() - before < 0.5
    assert released_model is False
    assert not task.done()

    release_task.set()
    await asyncio.wait_for(task, timeout=1.0)


@pytest.mark.asyncio
async def test_indexer_shutdown_skips_release_when_inference_does_not_drain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    released_model = False

    async def fake_drain(*, deadline_monotonic=None) -> bool:
        assert deadline_monotonic is not None
        await asyncio.sleep(0)
        return False

    async def fake_release() -> None:
        nonlocal released_model
        released_model = True

    monkeypatch.setattr(
        "knowledge.vector_index.drain_knowledge_embedding_inference",
        fake_drain,
    )
    monkeypatch.setattr(
        "utils.local_embedding_runtime.release_local_embedding_service",
        fake_release,
    )

    assert await indexer.stop_knowledge_indexer(timeout_seconds=0.02) is False
    assert released_model is False


@pytest.mark.asyncio
async def test_indexer_shutdown_abandons_stuck_model_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release_started = asyncio.Event()
    allow_release = asyncio.Event()

    async def fake_drain(*, deadline_monotonic=None) -> bool:
        return True

    async def stuck_release() -> None:
        release_started.set()
        await allow_release.wait()

    monkeypatch.setattr(
        "knowledge.vector_index.drain_knowledge_embedding_inference",
        fake_drain,
    )
    monkeypatch.setattr(
        "utils.local_embedding_runtime.release_local_embedding_service",
        stuck_release,
    )

    before = time.monotonic()
    assert await indexer.stop_knowledge_indexer(timeout_seconds=0.02) is False
    assert time.monotonic() - before < 0.5
    assert release_started.is_set()

    allow_release.set()
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_inference_drain_timeout_keeps_native_task_running() -> None:
    coordinator = _KnowledgeInferenceCoordinator()
    inference_started = asyncio.Event()
    allow_inference = asyncio.Event()

    async def native_inference() -> object:
        inference_started.set()
        await allow_inference.wait()
        return object()

    task = coordinator._start(native_inference, kind="background")
    assert task is not None
    await asyncio.wait_for(inference_started.wait(), timeout=1.0)

    drained = await coordinator.drain(
        deadline_monotonic=time.monotonic() + 0.01,
    )

    assert drained is False
    assert not task.cancelled()
    assert coordinator.active_kind() == "background"

    allow_inference.set()
    await asyncio.wait_for(task, timeout=1.0)
    await asyncio.sleep(0)
    assert coordinator.active_kind() == ""


def test_indexer_work_limits_are_bounded() -> None:
    assert indexer.STARTUP_DELAY_SECONDS == 45.0
    assert indexer.INITIALIZATION_RETRY_SECONDS == 5.0
    assert indexer.MAX_INITIALIZATION_RETRY_SECONDS == 60.0
    assert indexer.BACKLOG_DELAY_SECONDS == 30.0
    assert indexer.EMBEDDING_BATCH_SIZE == 4
    assert indexer.MAX_CHUNKS_PER_ROUND == 8


def test_degraded_job_is_not_pending_for_unrelated_index_work() -> None:
    assert indexer._has_pending_pack_jobs(({"state": "degraded"},)) is False
    assert indexer._has_pending_pack_jobs(({"state": "queued"},)) is True


@pytest.mark.asyncio
async def test_post_processing_job_enumeration_runs_off_the_event_loop() -> None:
    event_loop_thread = threading.get_ident()
    call_threads: list[int] = []

    class FakeService:
        def list_pack_jobs(self):
            call_threads.append(threading.get_ident())
            return ({"state": "queued"},)

    jobs = await indexer._list_pack_jobs_off_loop(FakeService())

    assert jobs == ({"state": "queued"},)
    assert call_threads and call_threads[0] != event_loop_thread


@pytest.mark.asyncio
async def test_indexer_initialization_failure_is_retrieved(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(indexer, "STARTUP_DELAY_SECONDS", 0.0)
    attempted = asyncio.Event()

    def fail_to_open(_root):
        attempted.set()
        raise ValueError("legacy migration conflict")

    monkeypatch.setattr(
        "knowledge.service.KnowledgeService.from_root",
        fail_to_open,
    )

    task = asyncio.create_task(indexer._run_indexer(tmp_path, asyncio.Event()))
    await asyncio.wait_for(attempted.wait(), timeout=1.0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_indexer_retries_initialization_after_wake(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(indexer, "STARTUP_DELAY_SECONDS", 0.0)
    monkeypatch.setattr(indexer, "INITIALIZATION_RETRY_SECONDS", 60.0)
    first_failure = asyncio.Event()
    recovered = asyncio.Event()
    attempts = 0

    class FakeService:
        def database_path(self):
            return tmp_path / "knowledge.db"

    def flaky_open(_root):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            first_failure.set()
            raise OSError("store temporarily unavailable")
        return FakeService()

    async def observe_recovery(*_args, **_kwargs):
        recovered.set()
        raise asyncio.CancelledError

    monkeypatch.setattr(
        "knowledge.service.KnowledgeService.from_root",
        flaky_open,
    )
    monkeypatch.setattr(
        "knowledge.pack_jobs.process_pack_jobs",
        observe_recovery,
    )
    wake = asyncio.Event()
    task = asyncio.create_task(indexer._run_indexer(tmp_path, wake))
    await asyncio.wait_for(first_failure.wait(), timeout=1.0)
    wake.set()
    await asyncio.wait_for(recovered.wait(), timeout=1.0)

    with pytest.raises(asyncio.CancelledError):
        await task
    assert attempts == 2
