"""Progressive background indexing owned by the public-knowledge domain."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Mapping
from pathlib import Path


logger = logging.getLogger("N.E.K.O.Knowledge.Indexer")

STARTUP_DELAY_SECONDS = 45.0
INITIALIZATION_RETRY_SECONDS = 5.0
MAX_INITIALIZATION_RETRY_SECONDS = 60.0
BACKLOG_DELAY_SECONDS = 30.0
IDLE_DELAY_SECONDS = 60.0
EMBEDDING_BATCH_SIZE = 4
MAX_CHUNKS_PER_ROUND = 8
SHUTDOWN_TIMEOUT_SECONDS = 2.0
INDEXER_CANCEL_GRACE_SECONDS = 0.25
_BLOCKED_STATES = frozenset(
    (
        "inference_busy",
        "embedding_unavailable",
        "not_ready",
        "disabled",
        "capacity_reached",
        "store_unavailable",
    )
)

_STATE_LOCK = threading.Lock()
_TASK: asyncio.Task[None] | None = None
_WAKE_EVENT: asyncio.Event | None = None
_EVENT_LOOP: asyncio.AbstractEventLoop | None = None


def _consume_task_result(task: asyncio.Task[object]) -> None:
    try:
        task.exception()
    except (asyncio.CancelledError, Exception):
        pass


def _rss_bytes() -> int | None:
    """Return aggregate process RSS without making diagnostics a dependency."""
    try:
        import psutil

        return int(psutil.Process().memory_info().rss)
    except Exception:
        return None


def _backfill_store(
    store: object,
    *,
    limit: int,
    embedding_policy_by_source: Mapping[str, str],
) -> int:
    from ._mutation_lock import mutation_lock

    with mutation_lock(store.database_path):
        return int(
            store.backfill_missing_chunks(
                limit=limit,
                embedding_policy_by_source=embedding_policy_by_source,
            )
        )


def _has_pending_pack_jobs(jobs: tuple[Mapping[str, object], ...]) -> bool:
    from .pack_jobs import DEGRADED_STATE, TERMINAL_STATES

    non_pending_states = TERMINAL_STATES | {DEGRADED_STATE}
    return any(job.get("state") not in non_pending_states for job in jobs)


async def _list_pack_jobs_off_loop(service: object) -> tuple[Mapping[str, object], ...]:
    return await asyncio.to_thread(service.list_pack_jobs)


async def _wait_for_wake(event: asyncio.Event, timeout: float) -> None:
    try:
        await asyncio.wait_for(event.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        pass
    finally:
        event.clear()


async def _run_indexer(knowledge_root: Path, wake_event: asyncio.Event) -> None:
    """Backfill chunks, then infer vectors outside every SQLite transaction."""
    await asyncio.sleep(STARTUP_DELAY_SECONDS)

    from .diagnostics import record_knowledge_index_batch
    from .packs import installed_source_embedding_policies
    from .store import KnowledgeStore
    from .pack_jobs import MAX_READY_VECTOR_CHUNKS, process_pack_jobs
    from .service import KnowledgeService
    from .vector_index import index_embedding_batch, reconcile_embedding_models

    retry_seconds = INITIALIZATION_RETRY_SECONDS
    while True:
        try:
            service = KnowledgeService.from_root(knowledge_root)
            break
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "Knowledge background indexing could not initialize (%s); "
                "retrying in %.1fs while BM25 remains available",
                type(exc).__name__,
                retry_seconds,
            )
            await _wait_for_wake(wake_event, retry_seconds)
            retry_seconds = min(
                retry_seconds * 2,
                MAX_INITIALIZATION_RETRY_SECONDS,
            )
    memory_baseline = _rss_bytes()
    memory_delta_reported = False

    while True:
        database_path = service.database_path()
        stores = (
            [KnowledgeStore(database_path)] if database_path.is_file() else []
        )
        backlog = False
        try:
            remaining = MAX_CHUNKS_PER_ROUND
            embedding_activity = False
            embedding_policies = installed_source_embedding_policies(database_path)
            # Derive at most one legacy entry per round. This is deterministic
            # SQLite work and does not load the ONNX model.
            for store in stores:
                derived_entries = await asyncio.to_thread(
                    _backfill_store,
                    store,
                    limit=1,
                    embedding_policy_by_source=embedding_policies,
                )
                if derived_entries:
                    backlog = True
                    await asyncio.sleep(0)
                    break

            # A model change must release old local vectors from the global
            # ready budget before deciding whether any replacement work fits.
            await reconcile_embedding_models(stores)
            statuses = await asyncio.gather(
                *(asyncio.to_thread(store.chunk_status) for store in stores)
            )
            ready_vectors = sum(
                int(status.get("chunks_ready", 0)) for status in statuses
            )
            blocked = False
            job_touched = False
            while remaining > 0:
                job_result = await process_pack_jobs(
                    service,
                    batch_size=min(EMBEDDING_BATCH_SIZE, remaining),
                    ready_vector_chunks=ready_vectors,
                )
                job_state = str(job_result.get("state") or "")
                selected = int(job_result.get("selected", 0))
                embedding_activity = embedding_activity or selected > 0
                remaining = max(remaining - selected, 0)
                job_touched = job_touched or job_state != "no_work"
                blocked = blocked or job_state in _BLOCKED_STATES
                if selected == 0 or job_state not in {"ready", "slow_batch"}:
                    break
                await asyncio.sleep(0)

            pending_jobs = _has_pending_pack_jobs(
                await _list_pack_jobs_off_loop(service)
            )
            backlog = backlog or (pending_jobs and not blocked)

            if (
                not job_touched
                and not pending_jobs
                and ready_vectors < MAX_READY_VECTOR_CHUNKS
            ):
                for store in stores:
                    if remaining <= 0:
                        break
                    available = MAX_READY_VECTOR_CHUNKS - ready_vectors
                    if available <= 0:
                        break
                    result = await index_embedding_batch(
                        store,
                        batch_size=min(EMBEDDING_BATCH_SIZE, remaining, available),
                        load_model=True,
                    )
                    record_knowledge_index_batch(
                        selected=result.selected,
                        stored=result.stored,
                        failed=result.failed,
                        stale_writebacks=result.stale_writebacks,
                        elapsed_ms=result.elapsed_ms,
                        state=result.state,
                    )
                    remaining -= result.selected
                    embedding_activity = embedding_activity or result.selected > 0
                    ready_vectors += result.stored
                    backlog = backlog or result.selected > 0
                    if result.state in _BLOCKED_STATES:
                        blocked = True
                        backlog = False
                        break
                    if result.stored:
                        await asyncio.sleep(0)

            statuses = await asyncio.gather(
                *(asyncio.to_thread(store.chunk_status) for store in stores)
            )
            local_work = await asyncio.gather(
                *(asyncio.to_thread(store.has_embedding_work) for store in stores)
            )
            if ready_vectors >= MAX_READY_VECTOR_CHUNKS:
                backlog = False

            if not stores and not pending_jobs:
                backlog = False

            if embedding_activity and not memory_delta_reported:
                current_rss = _rss_bytes()
                if memory_baseline is not None and current_rss is not None:
                    logger.info(
                        "Knowledge embedding runtime RSS delta after first index round: %.1f MiB",
                        (current_rss - memory_baseline) / (1024 * 1024),
                    )
                memory_delta_reported = True

            if (
                any(
                    int(status.get("entries_missing_chunks", 0)) > 0
                    for status in statuses
                )
                or any(local_work)
            ) and ready_vectors < MAX_READY_VECTOR_CHUNKS:
                if not blocked and not pending_jobs:
                    backlog = True
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Indexing is best-effort: BM25 remains the complete fallback path.
            logger.warning(
                "Knowledge background indexing round failed (%s); BM25 remains available",
                type(exc).__name__,
            )
            backlog = False

        await _wait_for_wake(
            wake_event,
            BACKLOG_DELAY_SECONDS if backlog else IDLE_DELAY_SECONDS,
        )


def start_knowledge_indexer(knowledge_root: str | Path) -> bool:
    """Start the one process-local coordinator; repeated calls are harmless."""
    global _TASK, _WAKE_EVENT, _EVENT_LOOP

    loop = asyncio.get_running_loop()
    with _STATE_LOCK:
        if _TASK is not None and not _TASK.done():
            return False
        wake_event = asyncio.Event()
        _WAKE_EVENT = wake_event
        _EVENT_LOOP = loop
        _TASK = loop.create_task(
            _run_indexer(Path(knowledge_root), wake_event),
            name="knowledge-vector-indexer",
        )
    return True


def notify_knowledge_index_changed() -> None:
    """Wake the coordinator safely from async or synchronous mutation threads."""
    with _STATE_LOCK:
        loop = _EVENT_LOOP
        event = _WAKE_EVENT
    if loop is None or event is None or loop.is_closed():
        return
    try:
        loop.call_soon_threadsafe(event.set)
    except RuntimeError:
        return


def request_knowledge_indexer_stop() -> asyncio.Task[None] | None:
    """Detach and cancel the coordinator without waiting for native work."""
    global _TASK, _WAKE_EVENT, _EVENT_LOOP

    with _STATE_LOCK:
        task = _TASK
        _TASK = None
        _WAKE_EVENT = None
        _EVENT_LOOP = None
    if task is not None and not task.done():
        task.cancel()
    return task


async def finish_knowledge_indexer_stop(
    task: asyncio.Task[None] | None,
    *,
    deadline_monotonic: float,
) -> bool:
    """Best-effort cleanup bounded by one absolute shutdown deadline."""
    if task is not None and not task.done():
        remaining = max(deadline_monotonic - time.monotonic(), 0.0)
        cancel_grace = min(remaining, INDEXER_CANCEL_GRACE_SECONDS)
        done, _pending = await asyncio.wait({task}, timeout=cancel_grace)
        if not done:
            task.add_done_callback(_consume_task_result)
            logger.warning(
                "Knowledge indexer cancellation exceeded %.2fs; "
                "embedding release skipped",
                cancel_grace,
            )
            return False
    if task is not None:
        _consume_task_result(task)

    # This model instance belongs to main_server's knowledge runtime.  The
    # separately running memory-server process has its own lifecycle.
    try:
        from .vector_index import drain_knowledge_embedding_inference
        from utils.local_embedding_runtime import release_local_embedding_service

        drained = await drain_knowledge_embedding_inference(
            deadline_monotonic=deadline_monotonic,
        )
        if not drained:
            logger.warning(
                "Knowledge embedding inference exceeded its shutdown budget; "
                "model release skipped",
            )
            return False

        remaining = deadline_monotonic - time.monotonic()
        if remaining <= 0:
            logger.warning(
                "Knowledge shutdown budget expired before model release; "
                "model release skipped",
            )
            return False
        release_task = asyncio.create_task(
            release_local_embedding_service(),
            name="knowledge-embedding-release",
        )
        done, _pending = await asyncio.wait({release_task}, timeout=remaining)
        if not done:
            release_task.add_done_callback(_consume_task_result)
            logger.warning(
                "Knowledge embedding model release exceeded its shutdown budget; "
                "continuing process cleanup",
            )
            return False
        release_task.result()
        return True
    except Exception as exc:
        logger.debug("Knowledge embedding runtime cleanup failed: %s", exc)
        return False


async def stop_knowledge_indexer(
    *,
    timeout_seconds: float = SHUTDOWN_TIMEOUT_SECONDS,
) -> bool:
    """Cancel the coordinator and bound all process-local model cleanup."""
    task = request_knowledge_indexer_stop()
    return await finish_knowledge_indexer_stop(
        task,
        deadline_monotonic=time.monotonic() + max(timeout_seconds, 0.0),
    )
