"""Knowledge-owned progressive vector index and exact cosine search."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, TypeVar

import numpy as np

from utils.local_embedding_runtime import (
    LocalEmbeddingService,
    LocalEmbeddingStatus,
    get_local_embedding_service,
    get_local_embedding_status,
)

from ._mutation_lock import mutation_lock
from .chunking import knowledge_query_embedding_text
from .catalog_overrides import (
    entry_key,
    get_catalog_override_path,
    load_disabled_entries,
)
from .limits import MAX_READY_VECTOR_CHUNKS
from .models import KnowledgeHit
from .store import KnowledgeStore, KnowledgeStoreError


logger = logging.getLogger("N.E.K.O.Knowledge.VectorIndex")
# Calibrated with input contract v1 against the local 256d int8 model:
# Recall@3=80% and unrelated-query rejection=90% on the grounded release set.
SEMANTIC_THRESHOLD = 0.57
VECTOR_CANDIDATE_LIMIT = 12
QUERY_EMBEDDING_TIMEOUT_SECONDS = 1.0
SLOW_BATCH_SECONDS = 15.0
DEFAULT_EMBEDDING_MICROBATCH_SIZE = 4
MAX_EMBEDDING_MICROBATCH_SIZE = 8
MAX_VECTOR_SNAPSHOT_BYTES = 32 * 1024 * 1024
MAX_VECTOR_SNAPSHOT_CHUNKS = 20_000

_T = TypeVar("_T")


def _remaining_timeout(
    deadline_monotonic: float | None,
    maximum: float,
) -> float:
    if deadline_monotonic is None:
        return maximum
    return max(min(deadline_monotonic - time.monotonic(), maximum), 0.0)


def _consume_task_result(task: asyncio.Task[object]) -> None:
    try:
        task.exception()
    except (asyncio.CancelledError, Exception):
        pass


async def _wait_task_until(
    task: asyncio.Task[_T],
    deadline_monotonic: float | None,
) -> tuple[bool, _T | None]:
    if task.done():
        return True, task.result()
    if deadline_monotonic is None:
        return True, await task
    remaining = deadline_monotonic - time.monotonic()
    if remaining <= 0:
        task.add_done_callback(_consume_task_result)
        return False, None
    done, _pending = await asyncio.wait({task}, timeout=remaining)
    if not done:
        task.add_done_callback(_consume_task_result)
        return False, None
    return True, task.result()


@dataclass(frozen=True, slots=True)
class EmbeddingBatchResult:
    selected: int = 0
    stored: int = 0
    failed: int = 0
    stale_writebacks: int = 0
    elapsed_ms: int = 0
    state: str = "no_work"


@dataclass(frozen=True, slots=True)
class SemanticQueryEmbedding:
    """One request-scoped query vector reusable across public-knowledge scans."""

    vector: list[float] | None
    status: LocalEmbeddingStatus
    state: str


class _KnowledgeInferenceCoordinator:
    """Serialize knowledge-owned native inference without cancelling timed-out work."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._task: asyncio.Task[object] | None = None
        self._kind = ""

    def active_kind(self) -> str:
        with self._lock:
            task = self._task
            return self._kind if task is not None and not task.done() else ""

    def _start(
        self,
        factory: Callable[[], Awaitable[_T]],
        *,
        kind: str,
    ) -> asyncio.Task[_T] | None:
        loop = asyncio.get_running_loop()
        with self._lock:
            active = self._task
            if active is not None and not active.done():
                return None
            task = loop.create_task(factory(), name=f"knowledge-embedding-{kind}")
            self._task = task
            self._kind = kind
        task.add_done_callback(self._finished)
        return task

    def _finished(self, task: asyncio.Task[object]) -> None:
        # Retrieve exceptions even when a query has already returned after its
        # soft timeout, then make the coordinator available for later work.
        try:
            task.exception()
        except (asyncio.CancelledError, Exception):
            pass
        with self._lock:
            if self._task is task:
                self._task = None
                self._kind = ""

    async def run_query(
        self,
        service: LocalEmbeddingService,
        text: str,
        *,
        timeout: float,
    ) -> tuple[list[float] | None, str]:
        task = self._start(lambda: service.embed(text), kind="query")
        if task is None:
            return None, "inference_busy"
        done, _pending = await asyncio.wait({task}, timeout=timeout)
        if not done:
            return None, "query_timeout"
        try:
            return task.result(), "ready"
        except Exception:
            return None, "query_embedding_failed"

    async def ensure_loaded(
        self,
        service: LocalEmbeddingService,
        *,
        timeout: float,
    ) -> str:
        task = self._start(service.request_load, kind="load")
        if task is None:
            return "inference_busy"
        done, _pending = await asyncio.wait({task}, timeout=timeout)
        if not done:
            return "model_load_timeout"
        try:
            return "ready" if bool(task.result()) else "not_ready"
        except Exception:
            return "embedding_unavailable"

    async def run_background(
        self,
        service: LocalEmbeddingService,
        texts: list[str],
    ) -> tuple[list[list[float] | None] | None, str, Exception | None]:
        task = self._start(lambda: service.embed_batch(texts), kind="background")
        if task is None:
            return None, "inference_busy", None
        try:
            vectors = await asyncio.shield(task)
        except asyncio.CancelledError:
            # Native inference cannot be cancelled safely. Keep the task tracked;
            # shutdown drains it before releasing the model runtime.
            raise
        except Exception as exc:
            return None, "inference_failed", exc
        return vectors, "ready", None

    async def drain(self, *, deadline_monotonic: float | None = None) -> bool:
        with self._lock:
            task = self._task
        if task is None:
            return True
        if task.done():
            return True
        if deadline_monotonic is None:
            try:
                await asyncio.shield(task)
            except (asyncio.CancelledError, Exception):
                pass
            return True

        remaining = deadline_monotonic - time.monotonic()
        if remaining <= 0:
            return False
        done, _pending = await asyncio.wait({task}, timeout=remaining)
        return bool(done)


_INFERENCE_COORDINATOR = _KnowledgeInferenceCoordinator()


def knowledge_inference_state() -> str:
    return _INFERENCE_COORDINATOR.active_kind()


async def drain_knowledge_embedding_inference(
    *,
    deadline_monotonic: float | None = None,
) -> bool:
    """Observe native inference without cancelling it when shutdown expires."""
    return await _INFERENCE_COORDINATOR.drain(
        deadline_monotonic=deadline_monotonic,
    )


@dataclass(frozen=True, slots=True)
class VectorIndexSnapshot:
    revision: int
    model_id: str
    matrix: np.ndarray
    entry_rowids: np.ndarray
    chunk_indices: np.ndarray
    database_identity: tuple[int, int, int, int] | tuple[()] = ()


_CACHE: dict[str, VectorIndexSnapshot] = {}
_REJECTED_CACHE: dict[
    str, tuple[int, str, tuple[int, int, int, int] | tuple[()], str]
] = {}
_CACHE_LOCK = threading.Lock()


def _cache_key(path: Path) -> str:
    return str(path.resolve()).casefold()


def _database_identity(path: Path) -> tuple[int, int, int, int] | tuple[()]:
    try:
        stat = path.stat()
    except OSError:
        return ()
    return int(stat.st_dev), int(stat.st_ino), int(stat.st_mtime_ns), int(stat.st_size)


def _load_snapshot(
    store: KnowledgeStore, status: LocalEmbeddingStatus
) -> VectorIndexSnapshot:
    revision = store.chunks_revision()
    key = _cache_key(store.database_path)
    identity = _database_identity(store.database_path)
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if (
            cached is not None
            and cached.revision == revision
            and cached.model_id == status.model_id
            and cached.database_identity == identity
        ):
            return cached
        rejected = _REJECTED_CACHE.get(key)
        if rejected is not None and rejected[:3] == (
            revision,
            status.model_id,
            identity,
        ):
            raise MemoryError(rejected[3])

    revision, rows, truncated = store.load_ready_chunk_vectors(
        model_id=status.model_id,
        limit=MAX_VECTOR_SNAPSHOT_CHUNKS,
    )

    entry_rowids: list[int] = []
    chunk_indices: list[int] = []
    vectors: list[np.ndarray] = []
    for row in rows:
        dimensions = int(row.get("dimensions") or 0)
        raw = row.get("embedding")
        if (
            dimensions != status.dimensions
            or not isinstance(raw, bytes)
            or len(raw) != dimensions * 2
        ):
            continue
        vector = np.frombuffer(raw, dtype="<f2").astype(np.float32)
        norm = float(np.linalg.norm(vector))
        if norm <= 0 or not np.isfinite(vector).all():
            continue
        vectors.append(vector / norm)
        entry_rowids.append(int(row["entry_rowid"]))
        chunk_indices.append(int(row["chunk_index"]))
    matrix = (
        np.stack(vectors).astype(np.float32, copy=False)
        if vectors
        else np.empty((0, status.dimensions), dtype=np.float32)
    )
    snapshot = VectorIndexSnapshot(
        revision,
        status.model_id,
        matrix,
        np.asarray(entry_rowids, dtype=np.int64),
        np.asarray(chunk_indices, dtype=np.int32),
        identity,
    )
    if truncated or matrix.nbytes > MAX_VECTOR_SNAPSHOT_BYTES:
        reason = "knowledge vector snapshot exceeds the local budget"
        with _CACHE_LOCK:
            _REJECTED_CACHE[key] = (revision, status.model_id, identity, reason)
        raise MemoryError(reason)
    with _CACHE_LOCK:
        _CACHE[key] = snapshot
        _REJECTED_CACHE.pop(key, None)
    return snapshot


def _score_snapshot(
    snapshot: VectorIndexSnapshot,
    query_vector: list[float],
    *,
    store: KnowledgeStore,
    limit: int,
    allowed_source_tags: tuple[str, ...] | None,
) -> list[KnowledgeHit]:
    if snapshot.matrix.size == 0:
        return []
    query = np.asarray(query_vector, dtype=np.float32).ravel()
    if query.size != snapshot.matrix.shape[1] or not np.isfinite(query).all():
        return []
    norm = float(np.linalg.norm(query))
    if norm <= 0:
        return []
    scores = snapshot.matrix @ (query / norm)
    disabled = load_disabled_entries(get_catalog_override_path(store.database_path))
    try:
        disabled_rowids = store.entry_rowids_for_keys(
            disabled,
            strict=bool(disabled),
        )
    except KnowledgeStoreError:
        return []
    if allowed_source_tags is None:
        eligible_indices = np.arange(len(scores), dtype=np.int64)
    else:
        eligible_rowids = store.entry_rowids_for_source_tags(allowed_source_tags)
        if not eligible_rowids:
            return []
        eligible_indices = np.flatnonzero(
            np.isin(snapshot.entry_rowids, tuple(eligible_rowids))
        )
        if not len(eligible_indices):
            return []
    if disabled_rowids:
        eligible_indices = eligible_indices[
            ~np.isin(snapshot.entry_rowids[eligible_indices], tuple(disabled_rowids))
        ]
        if not len(eligible_indices):
            return []
    best_chunk_by_rowid: dict[int, int] = {}
    for raw_index in eligible_indices:
        index = int(raw_index)
        score = float(scores[index])
        if score < SEMANTIC_THRESHOLD:
            continue
        rowid = int(snapshot.entry_rowids[index])
        previous = best_chunk_by_rowid.get(rowid)
        if previous is None or (score, -int(snapshot.chunk_indices[index])) > (
            float(scores[previous]),
            -int(snapshot.chunk_indices[previous]),
        ):
            best_chunk_by_rowid[rowid] = index
    if not best_chunk_by_rowid:
        return []
    unique_indices = sorted(
        best_chunk_by_rowid.values(),
        key=lambda index: (
            -float(scores[index]),
            int(snapshot.entry_rowids[index]),
            int(snapshot.chunk_indices[index]),
        ),
    )
    candidate_count = min(
        len(unique_indices),
        max(int(limit) * 8, 64),
    )
    candidate_indices = unique_indices[:candidate_count]
    rowids = [int(snapshot.entry_rowids[int(index)]) for index in candidate_indices]
    entries = store.load_entries_by_rowids_at_chunks_revision(
        rowids,
        expected_revision=snapshot.revision,
    )
    if entries is None:
        return []
    best: dict[tuple[str, str], KnowledgeHit] = {}
    for index in candidate_indices:
        score = float(scores[index])
        entry = entries.get(int(snapshot.entry_rowids[int(index)]))
        if entry is None:
            continue
        if (
            allowed_source_tags is not None
            and entry.source_tag not in allowed_source_tags
        ):
            continue
        key = entry_key(entry)
        if key in disabled:
            continue
        candidate = KnowledgeHit(
            entry=entry,
            score=score,
            retrieval_modes=("semantic",),
            semantic_score=score,
            best_chunk_index=int(snapshot.chunk_indices[int(index)]),
        )
        previous = best.get(key)
        if previous is None or score > float(previous.semantic_score or 0.0):
            best[key] = candidate
        if len(best) >= limit:
            break
    return sorted(
        best.values(),
        key=lambda hit: (-hit.score, hit.entry.title, hit.entry.source_tag),
    )[:limit]


async def semantic_search(
    store: KnowledgeStore,
    query: str,
    *,
    limit: int = VECTOR_CANDIDATE_LIMIT,
    allowed_source_tags: tuple[str, ...] | None = None,
) -> tuple[list[KnowledgeHit], str]:
    prepared = await prepare_semantic_query(query, stores=(store,))
    return await semantic_search_prepared(
        store,
        prepared,
        limit=limit,
        allowed_source_tags=allowed_source_tags,
    )


async def prepare_semantic_query(
    query: str,
    *,
    stores: tuple[KnowledgeStore, ...],
    load_model: bool = True,
    deadline_monotonic: float | None = None,
) -> SemanticQueryEmbedding:
    """Encode one query at most once for the requested public-knowledge scan."""
    empty_status = LocalEmbeddingStatus(state="not_ready")
    if not str(query or "").strip():
        return SemanticQueryEmbedding(None, empty_status, "empty_query")
    if knowledge_inference_state():
        return SemanticQueryEmbedding(None, empty_status, "inference_busy")
    try:
        status = get_local_embedding_status()
    except Exception:
        return SemanticQueryEmbedding(None, empty_status, "status_unavailable")
    if status.state == "disabled":
        return SemanticQueryEmbedding(None, status, "disabled")
    if status.state == "not_ready":
        statuses = await asyncio.gather(
            *(asyncio.to_thread(store.chunk_status) for store in stores)
        )
        if not any(int(value.get("chunks_ready", 0)) > 0 for value in statuses):
            return SemanticQueryEmbedding(None, status, "index_not_ready")
        if not load_model:
            return SemanticQueryEmbedding(None, status, "model_not_ready")
    service = get_local_embedding_service()
    if status.state == "not_ready":
        timeout = _remaining_timeout(
            deadline_monotonic,
            QUERY_EMBEDDING_TIMEOUT_SECONDS,
        )
        if timeout <= 0:
            return SemanticQueryEmbedding(None, status, "budget_exhausted")
        load_state = await _INFERENCE_COORDINATOR.ensure_loaded(
            service,
            timeout=timeout,
        )
        if load_state != "ready":
            return SemanticQueryEmbedding(None, status, load_state)
        status = get_local_embedding_status()
    if not status.ready:
        return SemanticQueryEmbedding(None, status, status.state)
    timeout = _remaining_timeout(
        deadline_monotonic,
        QUERY_EMBEDDING_TIMEOUT_SECONDS,
    )
    if timeout <= 0:
        return SemanticQueryEmbedding(None, status, "budget_exhausted")
    vector, query_state = await _INFERENCE_COORDINATOR.run_query(
        service,
        knowledge_query_embedding_text(query),
        timeout=timeout,
    )
    if query_state != "ready":
        return SemanticQueryEmbedding(None, status, query_state)
    if vector is None:
        return SemanticQueryEmbedding(None, status, "query_embedding_unavailable")
    return SemanticQueryEmbedding(vector, status, "ready")


async def semantic_search_prepared(
    store: KnowledgeStore,
    prepared: SemanticQueryEmbedding,
    *,
    limit: int = VECTOR_CANDIDATE_LIMIT,
    allowed_source_tags: tuple[str, ...] | None = None,
    deadline_monotonic: float | None = None,
) -> tuple[list[KnowledgeHit], str]:
    """Scan the public-knowledge index with an encoded request-scoped query."""
    if prepared.state != "ready" or prepared.vector is None:
        return [], prepared.state
    try:
        snapshot_task = asyncio.create_task(
            asyncio.to_thread(_load_snapshot, store, prepared.status)
        )
        completed, snapshot = await _wait_task_until(
            snapshot_task,
            deadline_monotonic,
        )
        if not completed or snapshot is None:
            return [], "snapshot_timeout"
        score_task = asyncio.create_task(
            asyncio.to_thread(
                _score_snapshot,
                snapshot,
                prepared.vector,
                store=store,
                limit=limit,
                allowed_source_tags=allowed_source_tags,
            )
        )
        completed, hits = await _wait_task_until(score_task, deadline_monotonic)
        if not completed or hits is None:
            return [], "score_timeout"
    except Exception:
        return [], "invalid_response"
    return hits, "ready"


def _embedding_work_status(store: KnowledgeStore) -> dict[str, object]:
    return store.chunk_status()


def _mark_store_model_vectors_stale(
    store: KnowledgeStore,
    *,
    model_id: str,
) -> int:
    with mutation_lock(store.database_path):
        return int(store.mark_incompatible_models_stale(model_id))


async def reconcile_embedding_models(
    stores: tuple[KnowledgeStore, ...] | list[KnowledgeStore],
) -> int:
    """Stale old local-model vectors before the global ready-vector budget is read."""
    try:
        status = get_local_embedding_status()
    except Exception:
        return 0
    model_id = str(status.model_id or "").strip()
    if not model_id or not stores:
        return 0
    stale_counts = await asyncio.gather(
        *(
            asyncio.to_thread(
                _mark_store_model_vectors_stale,
                store,
                model_id=model_id,
            )
            for store in stores
        )
    )
    return sum(stale_counts)


def _select_embedding_chunks(
    store: KnowledgeStore,
    *,
    model_id: str,
    limit: int,
) -> tuple[dict[str, object], ...]:
    with mutation_lock(store.database_path):
        store.mark_incompatible_models_stale(model_id)
    return store.pending_embedding_chunks(model_id=model_id, limit=limit)


def _mark_embedding_chunks_failed(
    store: KnowledgeStore,
    chunks: tuple[dict[str, object], ...],
    error_code: str,
) -> None:
    with mutation_lock(store.database_path):
        for chunk in chunks:
            store.mark_chunk_embedding_failed(
                chunk_id=str(chunk["chunk_id"]),
                content_hash=str(chunk["content_hash"]),
                error_code=error_code,
            )


def _store_embedding_vectors(
    store: KnowledgeStore,
    chunks: tuple[dict[str, object], ...],
    vectors: list[object] | tuple[object, ...],
    *,
    model_id: str,
    dimensions: int,
) -> tuple[int, int, int, int]:
    failed = 0
    capacity_deferred = 0
    prepared: list[dict[str, object]] = []
    with mutation_lock(store.database_path):
        status = store.chunk_status(strict=True)
        remaining_capacity = max(
            MAX_READY_VECTOR_CHUNKS - int(status["chunks_ready"]),
            0,
        )
        for index, chunk in enumerate(chunks):
            vector = vectors[index] if index < len(vectors) else None
            if vector is None:
                store.mark_chunk_embedding_failed(
                    chunk_id=str(chunk["chunk_id"]),
                    content_hash=str(chunk["content_hash"]),
                    error_code="empty_embedding",
                )
                failed += 1
                continue
            try:
                array = np.asarray(vector, dtype=np.float32).ravel()
            except (TypeError, ValueError):
                array = np.empty(0, dtype=np.float32)
            if array.size != dimensions or not np.isfinite(array).all():
                store.mark_chunk_embedding_failed(
                    chunk_id=str(chunk["chunk_id"]),
                    content_hash=str(chunk["content_hash"]),
                    error_code="invalid_embedding",
                )
                failed += 1
                continue
            with np.errstate(over="ignore", under="ignore", invalid="ignore"):
                stored_array = array.astype("<f2")
            stored_array_float32 = stored_array.astype(np.float32)
            if (
                not np.isfinite(stored_array_float32).all()
                or float(np.linalg.norm(stored_array_float32)) <= 0
            ):
                store.mark_chunk_embedding_failed(
                    chunk_id=str(chunk["chunk_id"]),
                    content_hash=str(chunk["content_hash"]),
                    error_code="invalid_embedding",
                )
                failed += 1
                continue
            if remaining_capacity <= 0:
                capacity_deferred += 1
                continue
            prepared.append(
                {
                    "chunk_id": str(chunk["chunk_id"]),
                    "content_hash": str(chunk["content_hash"]),
                    "model_id": model_id,
                    "dimensions": dimensions,
                    "embedding": stored_array.tobytes(),
                }
            )
            remaining_capacity -= 1
        stored = store.store_chunk_embedding_batch(prepared)
        stale_writebacks = len(prepared) - stored
    return stored, failed, stale_writebacks, capacity_deferred


async def index_embedding_batch(
    store: KnowledgeStore,
    *,
    batch_size: int = DEFAULT_EMBEDDING_MICROBATCH_SIZE,
    load_model: bool = False,
) -> EmbeddingBatchResult:
    safe_batch_size = max(1, min(int(batch_size), MAX_EMBEDDING_MICROBATCH_SIZE))
    service = None
    if load_model:
        try:
            service = get_local_embedding_service()
            if not service.is_available() and not service.is_disabled():
                load_state = await _INFERENCE_COORDINATOR.ensure_loaded(
                    service,
                    timeout=QUERY_EMBEDDING_TIMEOUT_SECONDS,
                )
                if load_state != "ready":
                    return EmbeddingBatchResult(state=load_state)
        except Exception:
            return EmbeddingBatchResult(state="embedding_unavailable")
    work = await asyncio.to_thread(_embedding_work_status, store)
    if not any(
        int(work.get(key, 0)) > 0
        for key in (
            "entries_missing_chunks",
            "chunks_pending",
            "chunks_stale",
            "chunks_failed_retryable_now",
        )
    ):
        return EmbeddingBatchResult(state="no_work")
    if service is None:
        try:
            service = get_local_embedding_service()
        except Exception:
            return EmbeddingBatchResult(state="embedding_unavailable")
    try:
        status = get_local_embedding_status()
    except Exception:
        return EmbeddingBatchResult(state="embedding_unavailable")
    if not status.ready:
        return EmbeddingBatchResult(state=status.state)
    chunks = await asyncio.to_thread(
        _select_embedding_chunks,
        store,
        model_id=status.model_id,
        limit=safe_batch_size,
    )
    if not chunks:
        return EmbeddingBatchResult(state="no_work")
    texts = [str(chunk["text"]) for chunk in chunks]
    started = time.perf_counter()
    (
        vectors,
        inference_state,
        inference_error,
    ) = await _INFERENCE_COORDINATOR.run_background(
        service,
        texts,
    )
    elapsed_ms = round((time.perf_counter() - started) * 1000)
    if inference_state == "inference_busy":
        return EmbeddingBatchResult(elapsed_ms=elapsed_ms, state=inference_state)
    if inference_error is not None:
        await asyncio.to_thread(
            _mark_embedding_chunks_failed,
            store,
            chunks,
            type(inference_error).__name__,
        )
        return EmbeddingBatchResult(
            selected=len(chunks),
            failed=len(chunks),
            elapsed_ms=elapsed_ms,
            state="failed",
        )

    if not isinstance(vectors, (list, tuple)):
        await asyncio.to_thread(
            _mark_embedding_chunks_failed,
            store,
            chunks,
            "invalid_response",
        )
        return EmbeddingBatchResult(
            selected=len(chunks),
            failed=len(chunks),
            elapsed_ms=elapsed_ms,
            state="failed",
        )

    try:
        stored, failed, stale_writebacks, capacity_deferred = await asyncio.to_thread(
            _store_embedding_vectors,
            store,
            chunks,
            vectors,
            model_id=status.model_id,
            dimensions=status.dimensions,
        )
    except KnowledgeStoreError:
        return EmbeddingBatchResult(
            selected=len(chunks),
            elapsed_ms=elapsed_ms,
            state="store_unavailable",
        )
    state = "failed" if failed else "ready"
    if not failed and capacity_deferred:
        state = "capacity_reached"
    if state == "ready" and elapsed_ms > round(SLOW_BATCH_SECONDS * 1000):
        state = "slow_batch"
        logger.warning(
            "Knowledge embedding microbatch was slow: selected=%d elapsed_ms=%d",
            len(chunks),
            elapsed_ms,
        )
    return EmbeddingBatchResult(
        selected=len(chunks),
        stored=stored,
        failed=failed,
        stale_writebacks=stale_writebacks,
        elapsed_ms=elapsed_ms,
        state=state,
    )
