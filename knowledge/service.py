# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""The knowledge subsystem as one object owned by the Memory Server.

Concurrency model
-----------------
* Every SQLite and file operation runs in a worker thread.
* All mutations (imports, removals, policy changes, vector writes, the startup
  reconcile) are serialized by one ``asyncio.Lock``. A mutation that cannot
  get the lock within ``WRITE_LOCK_TIMEOUT_SECONDS`` reports
  ``knowledge_busy`` instead of queueing behind a long import.
* Reads never take that lock: SQLite runs in WAL mode and the registry is an
  immutable snapshot replaced on every write.
* Queries run under a small semaphore and a hard time budget. Query embedding
  is started as its own task and abandoned (not awaited) when the budget runs
  out, so a slow model degrades a lookup to BM25 instead of stalling it.

Failure isolation: nothing here touches memory data, and every public coroutine
turns unexpected exceptions into a result value. Background loops log and
continue; a broken knowledge root leaves the subsystem ``unavailable`` while
the rest of the Memory Server carries on.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import contextvars
import logging
import random
import threading
import time
import uuid
from collections import OrderedDict, deque
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, Protocol, Sequence, TypeVar

import numpy as np

from utils.file_utils import atomic_write_bytes

from .diagnostics import KnowledgeDiagnostics
from .models import (
    MATERIAL_TYPES,
    MAX_PACK_BYTES,
    MAX_TOTAL_PACK_BYTES,
    MAX_TOTAL_ENTRIES,
    KnowledgePack,
    KnowledgePackError,
    canonical_pack_bytes,
    decode_pack_bytes,
    pack_id_is_valid,
    pack_sha256,
)
from .registry import (
    PACKS_DIR,
    KnowledgeRegistryError,
    PackRecord,
    Registry,
    load_registry,
    save_registry,
    utc_now,
)
from .chunking import chunk_bodies
from .render import RenderCard, render_reference_block
from .retrieval import (
    LEXICAL_CANDIDATES,
    RankedHit,
    SemanticMatch,
    best_excerpt_index,
    fuse,
    semantic_candidates,
)
from .store import (
    MAX_CHUNKS_PER_PACK,
    MAX_TOTAL_CHUNKS,
    KnowledgeStore,
    KnowledgeStoreError,
    StoredEntry,
    VectorSnapshot,
    count_pack_chunks,
    normalize_vector,
)
from .text import title_key


logger = logging.getLogger("N.E.K.O.Knowledge")
_T = TypeVar("_T")

DATABASE_FILE = "knowledge.db"
STAGING_DIR = ".staging"
WRITE_LOCK_TIMEOUT_SECONDS = 5.0
QUERY_CONCURRENCY = 4
DEFAULT_QUERY_BUDGET_MS = 1_500
MAX_QUERY_BUDGET_MS = 5_000
QUERY_RENDER_RESERVE_SECONDS = 0.15
# How often one import may give way to removals that then fail.
MAX_IMPORT_YIELDS = 8
# How long shutdown waits for writes whose caller was cancelled.
DETACHED_WRITE_WAIT_SECONDS = 10.0
MAX_QUERY_EMBEDDINGS = 2
MAX_QUERY_CHARS = 2_000
MAX_QUERY_LIMIT = 10
MAX_TRACKED_JOBS = 50
MAX_PENDING_IMPORTS = 3
# Packs are listed whole on the management page; keep that list small.
MAX_PACKS = 200
STATUS_SOURCES = 12
ACTIVE_JOB_STATES = frozenset({"queued", "building"})
TERMINAL_JOB_STATES = frozenset({"active", "failed", "cancelled"})

# Background embedding budget: small batches, a pause between them and a
# longer one after each round, so inference never monopolizes the shared
# EmbeddingService or the CPU that memory recall also needs.
INDEX_BATCH_SIZE = 8
INDEX_ROUND_CHUNKS = 64
INDEX_BATCH_PAUSE_SECONDS = 0.5
INDEX_ROUND_PAUSE_SECONDS = 5.0
INDEX_IDLE_SECONDS = 30.0
VECTOR_REFRESH_SECONDS = 60.0


class Embedder(Protocol):
    """What the knowledge subsystem needs from the shared EmbeddingService."""

    def state(self) -> str:
        """``ready`` / ``loading`` / ``disabled`` / ``unavailable``."""

    def model_id(self) -> str | None:
        """Id of the loaded model; vectors are only comparable within one id."""

    async def embed(self, text: str) -> list[float] | None:
        """One vector, or ``None`` when the service cannot produce it."""

    async def embed_batch(self, texts: list[str]) -> list[list[float] | None]:
        """Vectors aligned with ``texts``; ``None`` where one failed."""


class KnowledgeUnavailable(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(slots=True)
class ImportJob:
    job_id: str
    pack_id: str
    state: str
    created_at: str
    updated_at: str
    entries_total: int = 0
    chunks_total: int = 0
    reason: str = ""
    cancel_requested: bool = False
    arrived_at: int = 0
    staged_bytes: int = 0
    staged_sha256: str = ""
    # Set once the index write commits; from then on the import is no longer
    # cancellable. The lock makes "cancel" and "commit" exclusive.
    committed: bool = False
    gate: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def to_json(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "pack_id": self.pack_id,
            "state": self.state,
            "reason": self.reason,
            "entries_total": self.entries_total,
            "chunks_total": self.chunks_total,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


# The thread work of the query running in the current task (see _query_lease).
_QUERY_WORK: contextvars.ContextVar[list[concurrent.futures.Future[Any]] | None] = (
    contextvars.ContextVar("knowledge_query_work", default=None)
)


def _disabled_in(entry: StoredEntry, registry: Registry) -> bool:
    """Disabled per the index or per the query's registry snapshot.

    A toggle writes the index before the registry; whichever says "disabled"
    wins, so re-enabling never shows an entry before the registry agrees.
    """
    record = registry.packs.get(entry.pack_id)
    return entry.disabled or (record is not None and title_key(entry.title) in record.disabled_titles)


def _read_bounded(path: Path) -> bytes:
    """Read a raw pack, never more than the pack limit (+1 to detect overflow).

    A damaged or replaced file of any size must not be loaded whole into the
    shared Memory Server; an oversized one simply fails the hash check.
    """
    with path.open("rb") as handle:
        return handle.read(MAX_PACK_BYTES + 1)


def _consume(task: asyncio.Task[Any]) -> None:
    if not task.cancelled():
        task.exception()


class KnowledgeService:
    def __init__(self, root: Path, *, embedder: Embedder | None = None) -> None:
        self.root = Path(root)
        self.embedder = embedder
        self.diagnostics = KnowledgeDiagnostics()
        self._store = KnowledgeStore(self.root / DATABASE_FILE)
        self._registry = Registry()
        self._state = "starting"
        self._error_code = ""
        self._broken_packs: tuple[str, ...] = ()
        self._write_lock = asyncio.Lock()
        self._query_slots = asyncio.Semaphore(QUERY_CONCURRENCY)
        self._jobs: OrderedDict[str, ImportJob] = OrderedDict()
        # Queued job ids in order; a cancelled job leaves it at once, so the
        # backlog never holds more than the active jobs.
        self._job_queue: deque[str] = deque()
        self._job_queued = asyncio.Event()
        self._index_wakeup = asyncio.Event()
        self._stopping = False
        self._tasks: list[asyncio.Task[Any]] = []
        self._vectors: VectorSnapshot | None = None
        self._vector_generation = 0
        self._vectors_dirty = False
        self._index_model_id: str | None = None
        self._query_embeddings: set[asyncio.Task[Any]] = set()
        self._admitting: dict[str, int] = {}
        # Request order: imports and removals each take the next number when
        # the request arrives (before parsing or waiting for the lock). An
        # import gives way only to removals of its pack that arrived later:
        # ``_removed_at`` holds the latest committed one, ``_pending_removals``
        # those still waiting.
        self._request_seq = 0
        self._removed_at: dict[str, int] = {}
        # Values are frozensets, replaced rather than mutated: the import's
        # cancellation check reads them from a worker thread.
        self._pending_removals: dict[str, frozenset[int]] = {}
        self._query_pool: concurrent.futures.ThreadPoolExecutor | None = None
        self._parsing = 0
        self._detached: set[asyncio.Task[Any]] = set()
        self._startup_work: asyncio.Future[Any] | None = None
        self._parse_pool: concurrent.futures.ThreadPoolExecutor | None = None
        # Set (and replaced) whenever a removal finishes, committed or not.
        self._removal_settled = asyncio.Event()
        self._vectors_built_for: tuple[int, str] | None = None
        self._vector_task: asyncio.Task[Any] | None = None

    # ── lifecycle ───────────────────────────────────────────────────

    async def start(self) -> None:
        """Open, reconcile and start background work; never raises."""
        try:
            async with self._write_lock:
                # Tracked so stop() can wait for it: cancelling start() stops
                # the waiting, not the thread rebuilding the index.
                self._startup_work = asyncio.ensure_future(asyncio.to_thread(self._open_blocking))
                await asyncio.shield(self._startup_work)
            self._state = "ready"
            logger.info(
                "[Knowledge] ready: packs=%d enabled=%s", len(self._registry.packs), self._registry.enabled
            )
        except KnowledgeRegistryError as exc:
            self._state, self._error_code = "unavailable", "registry_invalid"
            logger.warning("[Knowledge] registry.json is invalid (%s); knowledge disabled", exc)
            return
        except Exception as exc:
            self._state, self._error_code = "unavailable", type(exc).__name__
            logger.warning("[Knowledge] startup failed: %s", type(exc).__name__, exc_info=True)
            return
        self._tasks = [
            asyncio.create_task(self._job_loop(), name="knowledge-import-jobs"),
            asyncio.create_task(self._index_loop(), name="knowledge-indexer"),
        ]
        self._schedule_vector_refresh()

    async def stop(self) -> None:
        self._stopping = True
        startup = self._startup_work
        if startup is not None and not startup.done():
            await asyncio.wait({startup}, timeout=DETACHED_WRITE_WAIT_SECONDS)
        self._index_wakeup.set()
        tasks = [task for task in (*self._tasks, self._vector_task) if task is not None]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.wait(tasks, timeout=2.0)
        # Cancelling a caller leaves its mutation running (see _locked); its
        # file, index and registry writes should end before we report stopped.
        # The wait is bounded: a write stuck on the disk must not hang the
        # Memory Server's shutdown. Each write is atomic on its own, and the
        # next start reconciles the index with the registry.
        if self._detached:
            await asyncio.wait(set(self._detached), timeout=DETACHED_WRITE_WAIT_SECONDS)
            if self._detached:
                logger.warning(
                    "[Knowledge] %d write(s) still running at shutdown", len(self._detached)
                )
        for pool in (self._query_pool, self._parse_pool):
            if pool is not None:
                pool.shutdown(wait=False, cancel_futures=True)

    def _open_blocking(self) -> None:
        (self.root / PACKS_DIR).mkdir(parents=True, exist_ok=True)
        registry = load_registry(self.root)
        try:
            self._store.initialize()
        except KnowledgeStoreError as exc:
            logger.warning("[Knowledge] rebuilding knowledge.db (%s)", exc)
            self._store.remove_files()
            self._store.initialize()
        try:
            broken = self._reconcile_blocking(registry)
        except Exception as exc:
            logger.warning("[Knowledge] index reconcile failed (%s); rebuilding", type(exc).__name__)
            self._store.remove_files()
            self._store.initialize()
            broken = self._reconcile_blocking(registry)
        self._registry = registry
        self._broken_packs = tuple(broken)
        self._clean_files_blocking(registry)

    def _reconcile_blocking(self, registry: Registry) -> list[str]:
        """Make ``knowledge.db`` match the registry; return packs whose raw file is lost."""
        indexed = self._store.pack_versions()
        for pack_id in indexed.keys() - registry.packs.keys():
            self._store.delete_pack(pack_id)
        entry_counts = self._store.entry_counts()
        chunk_counts = self._store.chunk_stats(None)
        search_rows = self._store.search_row_counts()
        broken: list[str] = []
        for pack_id, record in registry.packs.items():
            # The raw file is the source of truth even when the index is
            # current: a pack whose file is gone or altered cannot be rebuilt
            # and is not served.
            try:
                raw = _read_bounded(self.root / PACKS_DIR / record.file_name)
                if pack_sha256(raw) != record.pack_sha256:
                    raise KnowledgePackError("pack_file_mismatch")
                if (
                    indexed.get(pack_id) == record.pack_sha256
                    # The version row alone does not prove the rows are all
                    # there: a damaged database can keep it and lose others.
                    and entry_counts.get(pack_id, (0, 0))[0] == record.entries
                    and chunk_counts.get(pack_id, {}).get("total", 0) == record.chunks
                    # The rows lookups actually read: one full-text row per
                    # entry and every exact-match surface written at import.
                    and search_rows.get(pack_id, (0, 0, -1))[0] == record.entries
                    and search_rows.get(pack_id, (0, 0, -1))[1] == search_rows.get(pack_id, (0, 0, -1))[2]
                ):
                    # Disabled flags are written to the index and then the
                    # registry; the registry wins if the process died in between.
                    self._store.sync_disabled(pack_id, record.disabled_titles)
                    continue
                pack = decode_pack_bytes(raw)
            except (OSError, KnowledgePackError) as exc:
                logger.warning("[Knowledge] pack %s cannot be rebuilt: %s", pack_id, exc)
                self._store.delete_pack(pack_id)
                broken.append(pack_id)
                continue
            self._store.replace_pack(
                pack, pack_sha256=record.pack_sha256, disabled_keys=record.disabled_titles
            )
            logger.info("[Knowledge] rebuilt index of pack %s", pack_id)
        return broken

    def _clean_files_blocking(self, registry: Registry) -> None:
        """Drop leftovers; a file that cannot be deleted now is retried next start."""
        staging = self.root / STAGING_DIR
        referenced = {record.file_name for record in registry.packs.values()}
        leftovers = [path for path in staging.iterdir() if path.is_file()] if staging.is_dir() else []
        leftovers += [
            path
            for path in (self.root / PACKS_DIR).iterdir()
            if path.is_file()
            and path.suffix in (".json", ".tmp")
            and path.name not in referenced
        ]
        for path in leftovers:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.warning("[Knowledge] could not delete leftover %s", path.name)

    # ── availability ────────────────────────────────────────────────

    def availability(self) -> dict[str, bool]:
        registry = self._registry
        has_usable = any(
            record.entries > len(record.disabled_titles)
            and record.pack_id not in self._broken_packs
            for record in registry.packs.values()
        )
        ready = self._state == "ready"
        return {
            "ready": ready,
            "enabled": registry.enabled,
            "tool_available": ready and registry.enabled and has_usable,
        }

    def _publish_registry(self, registry: Registry) -> None:
        self._registry = registry

    # ── guarded writes ──────────────────────────────────────────────

    def _require_ready(self) -> None:
        if self._state != "ready":
            raise KnowledgeUnavailable(
                "knowledge_starting" if self._state == "starting" else "knowledge_unavailable"
            )

    async def _locked(
        self, func: Callable[[], Awaitable[_T]], *, wait: bool = False
    ) -> _T:
        """Run ``func`` under the write lock.

        Requests give up with ``knowledge_busy`` after a short wait; background
        work (``wait=True``) queues for as long as it takes.
        """
        self._require_ready()
        if wait:
            await self._write_lock.acquire()
        else:
            try:
                await asyncio.wait_for(self._write_lock.acquire(), WRITE_LOCK_TIMEOUT_SECONDS)
            except asyncio.TimeoutError as exc:
                raise KnowledgeUnavailable("knowledge_busy") from exc
        # The mutation runs as its own task: a cancelled caller stops waiting,
        # but thread work it started cannot be stopped, so the lock is only
        # released once the mutation has really finished.
        task = asyncio.ensure_future(func())
        try:
            result = await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.done():
                self._write_lock.release()
            else:
                task.add_done_callback(_consume)
                task.add_done_callback(lambda _task: self._write_lock.release())
                # Still writing: shutdown must wait for it (see stop()).
                self._detached.add(task)
                task.add_done_callback(self._detached.discard)
            raise
        except BaseException:
            self._write_lock.release()
            raise
        self._write_lock.release()
        return result

    async def _apply_record_update(self, pack_id: str, **changes: Any) -> PackRecord:
        """Persist and publish a policy change; the caller holds the write lock."""
        record = self._registry.packs.get(pack_id)
        if record is None:
            raise KnowledgeUnavailable("not_found")
        updated = replace(record, updated_at=utc_now(), **changes)
        registry = self._registry.with_pack(updated)
        await asyncio.to_thread(save_registry, self.root, registry)
        self._publish_registry(registry)
        return updated

    async def _update_record(self, pack_id: str, **changes: Any) -> PackRecord:
        return await self._locked(lambda: self._apply_record_update(pack_id, **changes))

    async def set_enabled(self, enabled: bool) -> dict[str, Any]:
        async def run() -> None:
            registry = replace(self._registry, enabled=bool(enabled))
            await asyncio.to_thread(save_registry, self.root, registry)
            self._publish_registry(registry)

        await self._locked(run)
        self._index_wakeup.set()
        return {"enabled": bool(enabled)}

    async def set_pack_auto_context(self, pack_id: str, enabled: bool) -> dict[str, Any]:
        record = await self._update_record(pack_id, auto_context=bool(enabled))
        return {"pack_id": pack_id, "auto_context": record.auto_context}

    async def set_pack_local_embedding(self, pack_id: str, enabled: bool) -> dict[str, Any]:
        async def run() -> PackRecord:
            record = await self._apply_record_update(pack_id, local_embedding=bool(enabled))
            if enabled:
                # Turning vectors back on is the user's retry for chunks that
                # failed to embed. The policy is already saved, so a failed
                # reset must not report the change itself as failed.
                try:
                    await asyncio.to_thread(self._store.reset_attempts, pack_id)
                except Exception:
                    logger.warning("[Knowledge] could not reset embed attempts of %s", pack_id)
            return record

        record = await self._locked(run)
        self._vector_generation += 1
        self._schedule_vector_refresh()
        self._index_wakeup.set()
        return {"pack_id": pack_id, "local_embedding": record.local_embedding}

    async def set_pack_material_type(self, pack_id: str, material_type: str | None) -> dict[str, Any]:
        if material_type is not None and material_type not in MATERIAL_TYPES:
            raise KnowledgeUnavailable("invalid_request")
        record = await self._update_record(pack_id, material_type_override=material_type)
        return {
            "pack_id": pack_id,
            "material_type_override": record.material_type_override,
            "effective_material_type": record.effective_material_type,
        }

    async def set_entry_disabled(self, pack_id: str, title: str, disabled: bool) -> dict[str, Any]:
        async def run() -> dict[str, Any]:
            record = self._registry.packs.get(pack_id)
            if record is None:
                raise KnowledgeUnavailable("not_found")
            key = title_key(title)
            was_disabled = key in record.disabled_titles
            found = await asyncio.to_thread(self._store.set_disabled, pack_id, title, bool(disabled))
            if not found:
                raise KnowledgeUnavailable("not_found")
            keys = set(record.disabled_titles)
            if disabled:
                keys.add(key)
            else:
                keys.discard(key)
            updated = replace(record, disabled_titles=tuple(sorted(keys)), updated_at=utc_now())
            registry = self._registry.with_pack(updated)
            try:
                await asyncio.to_thread(save_registry, self.root, registry)
            except BaseException:
                # The registry still says the old thing; so must the index.
                await asyncio.to_thread(self._store.set_disabled, pack_id, title, was_disabled)
                raise
            self._publish_registry(registry)
            return {"pack_id": pack_id, "disabled": bool(disabled), "disabled_entries": len(keys)}

        return await self._locked(run)

    async def remove_pack(self, pack_id: str) -> dict[str, Any]:
        # Nothing is marked up front, so a failed removal has nothing to undo.
        # While this removal is pending, earlier imports of the pack yield (one
        # may be holding the lock this removal waits for); once it succeeds,
        # every import that arrived before it stands down. Imports that
        # arrived after it are left alone: they are the newer request.
        if pack_id not in self._registry.packs:
            # Not installed (a first import may still be building): there is
            # nothing this removal could commit, so it must not make that
            # import yield either.
            raise KnowledgeUnavailable("not_found")
        self._request_seq += 1
        my_seq = self._request_seq
        self._pending_removals[pack_id] = self._pending_removals.get(pack_id, frozenset()) | {my_seq}

        async def run() -> dict[str, Any]:
            record = self._registry.packs.get(pack_id)
            if record is None:
                raise KnowledgeUnavailable("not_found")
            registry = self._registry.without_pack(pack_id)

            def cleanup() -> None:
                # Best effort: the removal is committed by the registry write,
                # and the startup reconcile drops leftover rows and files.
                try:
                    self._store.delete_pack(pack_id)
                except Exception:
                    logger.warning("[Knowledge] index cleanup of %s failed", pack_id, exc_info=True)
                try:
                    (self.root / PACKS_DIR / record.file_name).unlink(missing_ok=True)
                except OSError:
                    logger.warning("[Knowledge] could not delete the file of %s", pack_id)

            await asyncio.to_thread(save_registry, self.root, registry)
            self._removed_at[pack_id] = max(self._removed_at.get(pack_id, 0), my_seq)
            for job in list(self._jobs.values()):
                if job.pack_id == pack_id and job.state == "queued" and job.arrived_at < my_seq:
                    job.cancel_requested = True
                    self._finish_job(job, "cancelled")
                    self._unqueue(job.job_id)
                    await self._discard_staging(job.job_id)
            self._broken_packs = tuple(p for p in self._broken_packs if p != pack_id)
            self._publish_registry(registry)
            self._vector_generation += 1
            await asyncio.to_thread(cleanup)
            return {"pack_id": pack_id, "removed_entries": record.entries}

        try:
            result = await self._locked(run)
        finally:
            pending = self._pending_removals.get(pack_id, frozenset()) - {my_seq}
            if pending:
                self._pending_removals[pack_id] = pending
            else:
                self._pending_removals.pop(pack_id, None)
            settled, self._removal_settled = self._removal_settled, asyncio.Event()
            settled.set()
        self._schedule_vector_refresh()
        return result

    def _superseded(self, job: ImportJob) -> bool:
        """Whether a removal of the job's pack overrides this import."""
        return (
            job.cancel_requested
            or any(seq > job.arrived_at for seq in self._pending_removals.get(job.pack_id, ()))
            or self._removed_at.get(job.pack_id, -1) > job.arrived_at
        )

    # ── imports ─────────────────────────────────────────────────────

    async def import_pack(self, raw: bytes) -> dict[str, Any]:
        self._require_ready()
        # Parsing a large pack holds several copies of it in memory: refuse
        # before parsing when imports are already at their limit, and never
        # parse more than that many at once.
        if self.import_busy():
            return {"ok": False, "reason": "knowledge_busy"}
        self._request_seq += 1
        arrived_at = self._request_seq
        # The reservation lasts until the request is answered (including the
        # unchanged-file check, which reads from disk) or admitted, so parsed
        # copies of a pack never pile up uncounted.
        self._parsing += 1
        parse_work: list[concurrent.futures.Future[Any]] = []
        deferred = False
        try:
            return await self._import_parsed(raw, arrived_at, parse_work)
        except asyncio.CancelledError:
            # A cancelled request stops waiting, but its parse thread runs on
            # with the pack in memory: the slot is freed when that ends.
            running = [future for future in parse_work if not future.done()]
            if running:
                deferred = True
                loop = asyncio.get_running_loop()
                running[0].add_done_callback(
                    lambda _future: loop.call_soon_threadsafe(self._release_parse_slot)
                )
            raise
        finally:
            if not deferred:
                self._parsing -= 1

    def import_busy(self) -> bool:
        """Whether a new import would be refused for lack of room right now."""
        pending = sum(1 for job in self._jobs.values() if job.state in ACTIVE_JOB_STATES)
        return (
            pending + len(self._admitting) >= MAX_PENDING_IMPORTS
            or self._parsing >= MAX_PENDING_IMPORTS
        )

    def _release_parse_slot(self) -> None:
        self._parsing -= 1

    async def _import_parsed(
        self, raw: bytes, arrived_at: int, parse_work: list[concurrent.futures.Future[Any]]
    ) -> dict[str, Any]:
        if self._parse_pool is None:
            self._parse_pool = concurrent.futures.ThreadPoolExecutor(
                max_workers=MAX_PENDING_IMPORTS, thread_name_prefix="knowledge-parse"
            )
        future = self._parse_pool.submit(self._prepare_import, raw)
        parse_work.append(future)
        try:
            pack, canonical, chunks = await asyncio.wrap_future(future)
        except KnowledgePackError as exc:
            return {"ok": False, "reason": exc.reason}
        if len(canonical) > MAX_PACK_BYTES:
            # Normalization fills in omitted fields and can grow the file; the
            # staged canonical form is what gets read back, so it is what counts.
            return {"ok": False, "reason": "pack_too_large"}
        sha = pack_sha256(canonical)
        registry = self._registry
        existing = registry.packs.get(pack.pack_id)
        if (
            existing is not None
            and existing.pack_sha256 == sha
            and pack.pack_id not in self._broken_packs
            # The raw file is the source of truth; if it went missing or was
            # altered since startup, import again so it gets rewritten.
            and await asyncio.to_thread(self._raw_file_intact, existing)
            # Checked after that await: the record must still be the installed
            # one and no removal may be waiting, or "unchanged, active" could
            # be answered for a pack that is (about to be) gone. Such an
            # import gets a job and the request order settles it.
            and self._registry.packs.get(pack.pack_id) == existing
            and not self._pending_removals.get(pack.pack_id)
        ):
            return {"ok": True, "pack_id": pack.pack_id, "unchanged": True, "state": "active"}
        # Admission is checked and reserved without an await in between, so
        # two requests for the same pack cannot both get through, and staged
        # files (written before the single runner gets to them) stay bounded.
        pending = [job for job in self._jobs.values() if job.state in ACTIVE_JOB_STATES]
        if pack.pack_id in self._admitting or any(job.pack_id == pack.pack_id for job in pending):
            return {"ok": False, "reason": "job_in_progress"}
        if len(pending) + len(self._admitting) >= MAX_PENDING_IMPORTS:
            return {"ok": False, "reason": "knowledge_busy"}
        staged_bytes = sum(job.staged_bytes for job in pending) + sum(self._admitting.values())
        self._admitting[pack.pack_id] = len(canonical)

        async def admit() -> dict[str, Any]:
            try:
                return await self._admit_import(pack, canonical, chunks, staged_bytes, arrived_at)
            finally:
                self._admitting.pop(pack.pack_id, None)

        # Shielded: if the request goes away mid-way, admission still runs to
        # the end, so a staging file is never written without a job (and
        # reservation) accounting for it.
        return await asyncio.shield(asyncio.ensure_future(admit()))

    async def _admit_import(
        self,
        pack: KnowledgePack,
        canonical: bytes,
        chunks: int,
        staged_bytes: int,
        arrived_at: int,
    ) -> dict[str, Any]:
        registry = self._registry
        others = [record for record in registry.packs.values() if record.pack_id != pack.pack_id]
        if len(others) + 1 > MAX_PACKS:
            return {"ok": False, "reason": "capacity_packs"}
        if sum(record.entries for record in others) + len(pack.entries) > MAX_TOTAL_ENTRIES:
            return {"ok": False, "reason": "capacity_entries"}
        if sum(record.chunks for record in others) + chunks > MAX_TOTAL_CHUNKS:
            return {"ok": False, "reason": "capacity_chunks"}
        if chunks > MAX_CHUNKS_PER_PACK:
            return {"ok": False, "reason": "too_many_chunks"}
        installed_bytes = await asyncio.to_thread(
            self._installed_pack_bytes, pack.pack_id, self._active_job_ids()
        )
        if installed_bytes + staged_bytes + len(canonical) > MAX_TOTAL_PACK_BYTES:
            return {"ok": False, "reason": "capacity_bytes"}
        now = utc_now()
        job = ImportJob(
            job_id=uuid.uuid4().hex,
            pack_id=pack.pack_id,
            state="queued",
            created_at=now,
            updated_at=now,
            entries_total=len(pack.entries),
            chunks_total=chunks,
            staged_bytes=len(canonical),
            staged_sha256=pack_sha256(canonical),
        )
        await asyncio.to_thread(atomic_write_bytes, self._staging_path(job.job_id), canonical)
        self._remember_job(job)
        job.arrived_at = arrived_at
        if self._removed_at.get(pack.pack_id, -1) > arrived_at:
            # The pack was removed while this import was being admitted.
            job.cancel_requested = True
            self._finish_job(job, "cancelled")
            await self._discard_staging(job.job_id)
            return {"ok": True, **job.to_json()}
        self._job_queue.append(job.job_id)
        self._job_queued.set()
        return {"ok": True, **job.to_json()}

    @staticmethod
    def _prepare_import(raw: bytes) -> tuple[KnowledgePack, bytes, int]:
        pack = decode_pack_bytes(raw)
        return pack, canonical_pack_bytes(pack), count_pack_chunks(pack)

    def _raw_file_intact(self, record: PackRecord) -> bool:
        try:
            raw = _read_bounded(self.root / PACKS_DIR / record.file_name)
            return pack_sha256(raw) == record.pack_sha256
        except OSError:
            return False

    def _active_job_ids(self) -> frozenset[str]:
        return frozenset(job.job_id for job in self._jobs.values() if job.state in ACTIVE_JOB_STATES)

    def _installed_pack_bytes(self, replacing: str, active_jobs: frozenset[str] = frozenset()) -> int:
        """Bytes of pack files on disk, except the one ``replacing`` swaps out.

        Every file counts, not just registered ones: an old version or a
        staged upload whose deletion failed still takes disk space until a
        later cleanup. Staged files of active jobs are left out here; the
        callers count those from the jobs themselves.
        """
        record = self._registry.packs.get(replacing)
        skip = record.file_name if record is not None else None
        total = 0
        for directory in (PACKS_DIR, STAGING_DIR):
            try:
                paths = list((self.root / directory).iterdir())
            except OSError:
                continue
            for path in paths:
                # .tmp: an atomic write that could neither finish nor clean up.
                if path.suffix not in (".json", ".tmp"):
                    continue
                if directory == PACKS_DIR and path.name == skip:
                    continue
                if directory == STAGING_DIR and path.stem in active_jobs:
                    continue
                try:
                    total += path.stat().st_size
                except OSError:
                    continue
        return total

    def _staging_path(self, job_id: str) -> Path:
        return self.root / STAGING_DIR / f"{job_id}.json"

    def _remember_job(self, job: ImportJob) -> None:
        self._jobs[job.job_id] = job
        while len(self._jobs) > MAX_TRACKED_JOBS:
            oldest = next(
                (job_id for job_id, item in self._jobs.items() if item.state in TERMINAL_JOB_STATES),
                None,
            )
            if oldest is None:
                break
            del self._jobs[oldest]

    def list_jobs(self) -> list[dict[str, Any]]:
        return [job.to_json() for job in reversed(self._jobs.values())]

    async def cancel_job(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if job is None or job.state not in ACTIVE_JOB_STATES:
            return False
        with job.gate:
            if job.committed:
                # Past the commit point: the import is landing and will be
                # reported active; removing the pack is the way back.
                return False
            job.cancel_requested = True
        if job.state == "queued":
            self._finish_job(job, "cancelled")
            self._unqueue(job_id)
            # A cancelled job no longer counts toward the staging limits, so
            # its file must go now, not when the runner reaches the job.
            await self._discard_staging(job_id)
        return True

    def _unqueue(self, job_id: str) -> None:
        # The runner may already have taken it; then there is nothing to drop.
        with contextlib.suppress(ValueError):
            self._job_queue.remove(job_id)

    def discard_job(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if job is None or job.state not in TERMINAL_JOB_STATES:
            return False
        del self._jobs[job_id]
        return True

    def _finish_job(self, job: ImportJob, state: str, reason: str = "") -> None:
        job.state = state
        job.reason = reason
        job.updated_at = utc_now()

    async def _discard_staging(self, job_id: str) -> None:
        """Best effort: a staged file that cannot go now is removed at the next start."""
        try:
            await asyncio.to_thread(self._staging_path(job_id).unlink, missing_ok=True)
        except OSError:
            logger.warning("[Knowledge] could not delete staged file of job %s", job_id)

    async def _job_loop(self) -> None:
        while not self._stopping:
            while not self._job_queue:
                self._job_queued.clear()
                await self._job_queued.wait()
            job_id = self._job_queue.popleft()
            job = self._jobs.get(job_id)
            if job is None or job.state != "queued":
                await self._discard_staging(job_id)
                continue
            job.state, job.updated_at = "building", utc_now()
            try:
                for attempt in range(MAX_IMPORT_YIELDS + 1):
                    try:
                        await self._locked(lambda job=job: self._commit_job(job), wait=True)
                        break
                    except InterruptedError:
                        if (
                            self._stopping
                            or job.cancel_requested
                            or self._removed_at.get(job.pack_id, -1) > job.arrived_at
                            # Never spin the single runner on one job.
                            or attempt == MAX_IMPORT_YIELDS
                        ):
                            raise
                    # It only gave way to a removal that has not committed:
                    # wait for that removal's outcome. If it fails, this
                    # import goes ahead; if it commits, the import is dropped.
                    await self._await_pending_removals(job)
                # The replaced rows got new entry ids; reload the vector
                # snapshot before reporting "active", or vector lookups would
                # still point at the old ids until a background refresh.
                self._vector_generation += 1
                await self._refresh_vectors_now()
                self._finish_job(job, "active")
                self._index_wakeup.set()
                self._schedule_vector_refresh()
            except InterruptedError:
                self._finish_job(job, "cancelled")
            except KnowledgeUnavailable as exc:
                self._finish_job(job, "failed", exc.reason)
            except KnowledgePackError as exc:
                self._finish_job(job, "failed", exc.reason)
            except asyncio.CancelledError:
                self._finish_job(job, "failed", "knowledge_stopping")
                raise
            except Exception as exc:
                logger.warning("[Knowledge] import of %s failed: %s", job.pack_id, exc, exc_info=True)
                self._finish_job(job, "failed", type(exc).__name__)
            finally:
                await self._discard_staging(job_id)

    async def _await_pending_removals(self, job: ImportJob) -> None:
        while any(seq > job.arrived_at for seq in self._pending_removals.get(job.pack_id, ())):
            await self._removal_settled.wait()

    async def _commit_job(self, job: ImportJob) -> None:
        if self._superseded(job):
            raise InterruptedError("cancelled")
        previous = self._registry.packs.get(job.pack_id)
        active_jobs = self._active_job_ids()

        def commit_gate() -> bool:
            with job.gate:
                if self._superseded(job) or self._stopping:
                    return False
                job.committed = True
                return True

        def blocking() -> tuple[Registry, PackRecord]:
            raw = _read_bounded(self._staging_path(job.job_id))
            sha = pack_sha256(raw)
            if sha != job.staged_sha256:
                # The staged file changed after admission; its capacity checks
                # and chunk count were for other bytes.
                raise KnowledgeUnavailable("knowledge_error")
            pack = decode_pack_bytes(raw)
            # Admission checked capacity without the lock; two imports racing
            # through it must not both land, so check again under the lock.
            others = [r for r in self._registry.packs.values() if r.pack_id != pack.pack_id]
            if len(others) + 1 > MAX_PACKS:
                raise KnowledgePackError("capacity_packs")
            if sum(r.entries for r in others) + len(pack.entries) > MAX_TOTAL_ENTRIES:
                raise KnowledgePackError("capacity_entries")
            if sum(r.chunks for r in others) + job.chunks_total > MAX_TOTAL_CHUNKS:
                raise KnowledgePackError("capacity_chunks")
            if self._installed_pack_bytes(pack.pack_id, active_jobs) + len(raw) > MAX_TOTAL_PACK_BYTES:
                raise KnowledgePackError("capacity_bytes")
            now = utc_now()
            keys = {entry.key for entry in pack.entries}
            record = PackRecord(
                pack_id=pack.pack_id,
                pack_sha256=sha,
                source=pack.source,
                declared_material_type=pack.material_type,
                entries=len(pack.entries),
                chunks=job.chunks_total,
                material_type_override=previous.material_type_override if previous else None,
                auto_context=previous.auto_context if previous else False,
                local_embedding=previous.local_embedding if previous else True,
                disabled_titles=tuple(
                    sorted(set(previous.disabled_titles) & keys) if previous else ()
                ),
                installed_at=previous.installed_at if previous else now,
                updated_at=now,
            )
            final_path = self.root / PACKS_DIR / record.file_name
            atomic_write_bytes(final_path, raw)
            indexed = False
            try:
                self._store.replace_pack(
                    pack,
                    pack_sha256=sha,
                    disabled_keys=record.disabled_titles,
                    should_cancel=lambda: self._superseded(job) or self._stopping,
                    commit_gate=commit_gate,
                )
                indexed = True
                registry = self._registry.with_pack(record)
                save_registry(self.root, registry)
            except BaseException:
                if indexed:
                    # The registry still describes the old version: put the
                    # index back so a failed import does not change answers.
                    self._restore_index_blocking(pack.pack_id, previous)
                if previous is None or previous.file_name != record.file_name:
                    final_path.unlink(missing_ok=True)
                raise
            if previous is not None and previous.file_name != record.file_name:
                # The new version is committed; a leftover old file is only
                # clutter and is removed by the startup cleanup.
                try:
                    (self.root / PACKS_DIR / previous.file_name).unlink(missing_ok=True)
                except OSError:
                    logger.warning("[Knowledge] could not delete the old file of %s", pack.pack_id)
            return registry, record

        registry, _record = await asyncio.to_thread(blocking)
        self._broken_packs = tuple(p for p in self._broken_packs if p != job.pack_id)
        self._publish_registry(registry)

    def _restore_index_blocking(self, pack_id: str, previous: PackRecord | None) -> None:
        try:
            if previous is None:
                self._store.delete_pack(pack_id)
                return
            raw = _read_bounded(self.root / PACKS_DIR / previous.file_name)
            if pack_sha256(raw) != previous.pack_sha256:
                # The old file was altered: rows built from it must not carry
                # the registered version. Leave the pack without rows (not
                # served) until the startup reconcile marks it broken.
                logger.warning("[Knowledge] old file of %s changed; not restoring its index", pack_id)
                self._store.delete_pack(pack_id)
                return
            self._store.replace_pack(
                decode_pack_bytes(raw),
                pack_sha256=previous.pack_sha256,
                disabled_keys=previous.disabled_titles,
            )
        except Exception:
            # Startup reconcile repairs the index from the registry anyway.
            logger.warning("[Knowledge] could not restore index after a failed import", exc_info=True)

    # ── vectors ─────────────────────────────────────────────────────

    def _embedding_state(self) -> str:
        if self.embedder is None:
            return "unavailable"
        try:
            return self.embedder.state()
        except Exception:
            return "unavailable"

    def _current_model_id(self) -> str | None:
        if self._embedding_state() != "ready":
            return None
        try:
            return self.embedder.model_id() if self.embedder is not None else None
        except Exception:
            return None

    def _install_vectors(self, snapshot: VectorSnapshot, key: tuple[int, str]) -> None:
        """Use ``snapshot`` unless one loaded for a later generation is in place."""
        built = self._vectors_built_for
        if built is not None and built[1] == key[1] and built[0] > key[0]:
            return
        self._vectors = snapshot
        self._vectors_built_for = key

    async def _refresh_vectors_now(self) -> None:
        model_id = self._current_model_id()
        if model_id is None or self._state != "ready":
            return
        key = (self._vector_generation, model_id)
        try:
            snapshot = await asyncio.to_thread(self._store.load_vectors, model_id)
        except Exception:
            # The scheduled refresh retries; lookups meanwhile skip ids that
            # no longer exist.
            logger.warning("[Knowledge] vector snapshot reload failed", exc_info=True)
            return
        self._install_vectors(snapshot, key)

    def _schedule_vector_refresh(self) -> None:
        model_id = self._current_model_id()
        if model_id is None or self._state != "ready":
            return
        key = (self._vector_generation, model_id)
        if self._vectors_built_for == key:
            return
        if self._vector_task is not None and not self._vector_task.done():
            return

        async def rebuild() -> bool:
            try:
                snapshot = await asyncio.to_thread(self._store.load_vectors, model_id)
            except Exception:
                logger.warning("[Knowledge] vector snapshot rebuild failed", exc_info=True)
                return False
            self._install_vectors(snapshot, key)
            return True

        def rebuilt(task: asyncio.Task[bool]) -> None:
            # A change that arrived while this rebuild ran was skipped above.
            # Re-check once the task is done (from inside it, it still counts
            # as running); a failed rebuild waits for the indexer instead of
            # retrying in a tight loop.
            if task.cancelled() or task.exception() is not None or not task.result():
                return
            if not self._stopping:
                self._schedule_vector_refresh()

        self._vector_task = asyncio.create_task(rebuild(), name="knowledge-vector-snapshot")
        self._vector_task.add_done_callback(rebuilt)

    async def _index_loop(self) -> None:
        last_refresh = time.monotonic()
        while not self._stopping:
            try:
                processed = await self._index_round()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("[Knowledge] indexer round failed", exc_info=True)
                processed = 0
            # New vectors become searchable once the backlog drains, or at
            # least every VECTOR_REFRESH_SECONDS while a long backfill runs;
            # reloading the matrix after every batch would cost more than the
            # batch itself.
            if processed == 0 or time.monotonic() - last_refresh >= VECTOR_REFRESH_SECONDS:
                if self._vectors_dirty:
                    self._vectors_dirty = False
                    self._vector_generation += 1
                self._schedule_vector_refresh()
                last_refresh = time.monotonic()
            delay = INDEX_ROUND_PAUSE_SECONDS if processed else INDEX_IDLE_SECONDS
            self._index_wakeup.clear()
            try:
                await asyncio.wait_for(self._index_wakeup.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass  # the idle delay elapsed without a wake-up; run another round

    def _vector_pack_ids(self) -> list[str] | None:
        """Packs whose chunks may be embedded now; ``None`` when indexing is off."""
        if self._stopping or self._state != "ready" or not self._registry.enabled:
            return None
        return [record.pack_id for record in self._registry.packs.values() if record.local_embedding]

    async def _index_round(self) -> int:
        if self._state != "ready" or not self._registry.enabled or self.embedder is None:
            return 0
        model_id = self._current_model_id()
        if model_id is None:
            return 0
        if self._index_model_id != model_id:
            # Failure counts belong to the model (and process) that produced
            # them: a new model, or a restart, retries every chunk once more.
            await self._locked(lambda: asyncio.to_thread(self._store.reset_attempts), wait=True)
            self._index_model_id = model_id
        processed = 0
        while processed < INDEX_ROUND_CHUNKS and not self._stopping:
            # Re-read the switches every batch: the user may turn knowledge or
            # a pack's vectors off while a batch is embedding.
            pack_ids = self._vector_pack_ids()
            if pack_ids is None:
                break
            rows = await asyncio.to_thread(
                self._store.pending_chunks, model_id=model_id, pack_ids=pack_ids, limit=INDEX_BATCH_SIZE
            )
            if not rows:
                break
            started = time.monotonic()
            vectors = await self.embedder.embed_batch([text for _id, _hash, text in rows])
            if self._current_model_id() != model_id:
                break
            pack_ids = self._vector_pack_ids()
            if pack_ids is None:
                break
            blobs = [
                (chunk_id, text_hash, normalize_vector(vector) if vector is not None else None)
                for (chunk_id, text_hash, _text), vector in zip(rows, vectors)
            ]
            stored, failed = await self._locked(
                lambda blobs=blobs, pack_ids=pack_ids: asyncio.to_thread(
                    self._store.store_vectors, model_id=model_id, rows=blobs, pack_ids=pack_ids
                ),
                wait=True,
            )
            if stored:
                self._vectors_dirty = True
            self.diagnostics.record_index_batch(
                model_id=model_id,
                selected=len(rows),
                stored=stored,
                failed=failed,
                elapsed_ms=int((time.monotonic() - started) * 1000),
            )
            processed += len(rows)
            if stored == 0:
                break
            await asyncio.sleep(INDEX_BATCH_PAUSE_SECONDS)
        return processed

    # ── queries ─────────────────────────────────────────────────────

    def _current_packs(self, pack_ids: Sequence[str], registry: Registry) -> set[str]:
        """Packs whose indexed rows are the version ``registry`` describes.

        An import writes the rows before the registry, and queries do not take
        the write lock, so a query may see a newer (or rolled-back) version of
        a pack than its registry snapshot; such rows are not served.
        """
        indexed = self._store.pack_versions()
        return {
            pack_id
            for pack_id in pack_ids
            if (record := registry.packs.get(pack_id)) is not None
            and indexed.get(pack_id) == record.pack_sha256
        }

    def _allowed_pack_ids(self, registry: Registry, material_type: str) -> list[str]:
        wanted = MATERIAL_TYPES if material_type in ("", "auto", "all") else (material_type,)
        return [
            record.pack_id
            for record in registry.packs.values()
            if record.effective_material_type in wanted and record.pack_id not in self._broken_packs
        ]

    async def query(
        self,
        *,
        query: str,
        mode: str = "lookup",
        material_type: str = "auto",
        limit: int = 3,
        budget_ms: int = DEFAULT_QUERY_BUDGET_MS,
        language: str | None = None,
    ) -> dict[str, Any]:
        """Look up or sample entries; the result never raises.

        ``result`` is one of matched / miss / timeout / busy / error / disabled
        / unavailable, and ``context`` is the fenced reference block (empty
        unless matched).
        """
        started = time.monotonic()
        mode = mode if mode in ("lookup", "sample") else "lookup"
        query = str(query or "").strip()[:MAX_QUERY_CHARS]
        limit = min(max(int(limit or 1), 1), MAX_QUERY_LIMIT)
        budget = min(max(int(budget_ms or DEFAULT_QUERY_BUDGET_MS), 50), MAX_QUERY_BUDGET_MS) / 1000

        def finish(result: str, **extra: Any) -> dict[str, Any]:
            elapsed = int((time.monotonic() - started) * 1000)
            hits = extra.get("hits") or []
            self.diagnostics.record_query(
                mode=mode,
                result=result,
                retrieval_mode=extra.get("retrieval_mode", ""),
                hits=len(hits),
                entry_title=hits[0]["title"] if hits else "",
                pack_id=hits[0]["pack_id"] if hits else "",
                elapsed_ms=elapsed,
                error_type=extra.get("error_type", ""),
            )
            return {
                "ok": True,
                "result": result,
                "context": extra.get("context", ""),
                "hits": hits,
                "card_ids": [f"{hit['pack_id']}/{title_key(hit['title'])}" for hit in hits],
                "retrieval_mode": extra.get("retrieval_mode", ""),
                "elapsed_ms": elapsed,
            }

        if self._state != "ready":
            return finish("unavailable")
        if not self._registry.enabled:
            return finish("disabled")
        if not query:
            return finish("miss")
        # One registry snapshot decides eligibility, vector policy and the
        # labels in the rendered cards, even if a policy changes mid-query.
        registry = self._registry
        allowed = self._allowed_pack_ids(registry, str(material_type or "auto"))
        if not allowed:
            return finish("miss")
        if self._query_slots.locked():
            return finish("busy")
        async with self._query_lease():
            deadline = started + budget
            try:
                async with asyncio.timeout(max(deadline - time.monotonic(), 0.01)):
                    if mode == "sample":
                        ranked = await self._sample(query, allowed, limit, registry)
                        retrieval_mode = "sample"
                    else:
                        ranked, retrieval_mode = await self._lookup(
                            query, allowed, limit, deadline, registry
                        )
                    if not ranked:
                        return finish("miss", retrieval_mode=retrieval_mode)
                    excerpt_query = query if mode == "lookup" else ""
                    hits, context = await self._query_thread(
                        self._render, ranked, excerpt_query, language, registry
                    )
            except TimeoutError:
                return finish("timeout")
            except Exception as exc:
                logger.warning("[Knowledge] query failed: %s", type(exc).__name__, exc_info=True)
                return finish("error", error_type=type(exc).__name__)
        if not hits:
            return finish("miss", retrieval_mode=retrieval_mode)
        return finish("matched", hits=hits, context=context, retrieval_mode=retrieval_mode)

    @contextlib.asynccontextmanager
    async def _query_lease(self) -> AsyncIterator[None]:
        """Hold a query slot until the query and all its thread work are done.

        A timeout only stops waiting for a thread; the work itself runs on.
        Releasing the slot only when that work ends keeps the number of
        running scans at ``QUERY_CONCURRENCY``, whatever the budgets.
        """
        await self._query_slots.acquire()
        work: list[concurrent.futures.Future[Any]] = []
        token = _QUERY_WORK.set(work)
        try:
            yield
        finally:
            _QUERY_WORK.reset(token)
            running = [future for future in work if not future.done()]
            if not running:
                self._query_slots.release()
            else:
                loop = asyncio.get_running_loop()
                left = [len(running)]

                def one_done(_future: concurrent.futures.Future[Any]) -> None:
                    left[0] -= 1
                    if left[0] == 0:
                        self._query_slots.release()

                for future in running:
                    future.add_done_callback(
                        lambda f: loop.call_soon_threadsafe(one_done, f)
                    )

    async def _query_thread(self, fn: Callable[..., _T], /, *args: Any, **kwargs: Any) -> _T:
        """Run query work in a thread that the query's slot stays tied to."""
        work = _QUERY_WORK.get()
        if work is None:
            return await asyncio.to_thread(fn, *args, **kwargs)
        if self._query_pool is None:
            self._query_pool = concurrent.futures.ThreadPoolExecutor(
                max_workers=QUERY_CONCURRENCY, thread_name_prefix="knowledge-query"
            )
        future = self._query_pool.submit(contextvars.copy_context().run, fn, *args, **kwargs)
        work.append(future)
        return await asyncio.wrap_future(future)

    async def _sample(
        self, tag: str, allowed: list[str], limit: int, registry: Registry
    ) -> list[RankedHit]:
        rows = await self._query_thread(self._store.entries_with_tag, tag, allowed)
        # Drop entries the query's registry snapshot disables before choosing,
        # or one of them could take a slot and then be rejected at rendering.
        ids = [
            entry_id
            for entry_id, pack_id, title in rows
            if (record := registry.packs.get(pack_id)) is None
            or title_key(title) not in record.disabled_titles
        ]
        return [
            RankedHit(entry_id=i, score=0.0, exact=False, lexical_rank=None, semantic_score=None)
            for i in random.sample(ids, min(limit, len(ids)))
        ]

    async def _lookup(
        self, query: str, allowed: list[str], limit: int, deadline: float, registry: Registry
    ) -> tuple[list[RankedHit], str]:
        embed_task: asyncio.Task[Any] | None = None
        model_id = self._current_model_id()
        snapshot = self._vectors
        # A pack with local vectors turned off is keyword-only, even if
        # vectors from before the switch are still in the snapshot; when no
        # eligible pack has vectors, the query is not embedded at all.
        vector_packs = [
            pack_id
            for pack_id in allowed
            if (record := registry.packs.get(pack_id)) is not None and record.local_embedding
        ]
        if (
            model_id is not None
            and snapshot is not None
            and snapshot.model_id == model_id
            and set(vector_packs) & set(snapshot.pack_ids)
            # A lookup that ran out of budget leaves its embedding running.
            # Cap those, or slow inference piles up on the shared model.
            and len(self._query_embeddings) < MAX_QUERY_EMBEDDINGS
        ):
            embed_task = asyncio.create_task(self.embedder.embed(query))
            embed_task.add_done_callback(_consume)
            self._query_embeddings.add(embed_task)
            embed_task.add_done_callback(self._query_embeddings.discard)
        exact_ids, lexical_ids = await self._query_thread(
            self._lexical_search, query, allowed, registry
        )
        query_vector: np.ndarray | None = None
        if embed_task is not None:
            remaining = deadline - time.monotonic()
            # Leave room for fetching and rendering the cards; a late vector
            # is dropped and the lookup goes on with BM25 alone.
            done, _pending = await asyncio.wait(
                {embed_task}, timeout=max(remaining - QUERY_RENDER_RESERVE_SECONDS, 0)
            )
            if embed_task in done and embed_task.exception() is None and embed_task.result():
                blob = normalize_vector(embed_task.result())
                if blob is not None:
                    query_vector = np.frombuffer(blob, dtype="<f4")
        semantic = (
            await self._query_thread(
                self._semantic_search, snapshot, query_vector, vector_packs, registry
            )
            if query_vector is not None
            else []
        )
        candidate_ids = list(dict.fromkeys([*exact_ids, *lexical_ids, *(m.entry_id for m in semantic)]))
        entries = await self._query_thread(self._store.fetch_entries, candidate_ids)
        # Read the versions after the rows: rows of a pack replaced in between
        # then show a newer version and are left out, never mislabelled.
        allowed_set = await self._query_thread(self._current_packs, allowed, registry)
        usable = {
            entry_id
            for entry_id, entry in entries.items()
            if not _disabled_in(entry, registry) and entry.pack_id in allowed_set
        }
        # Coverage scoring scans entry text: keep it off the event loop.
        ranked = await self._query_thread(
            fuse,
            query,
            exact_ids=exact_ids,
            lexical_ids=lexical_ids,
            semantic=semantic,
            entries=entries,
            usable=usable,
            limit=limit,
        )
        return ranked, ("hybrid" if query_vector is not None else "bm25")

    def _snapshot_only_disabled(self, pack_ids: Sequence[str], registry: Registry) -> set[int]:
        """Entries the registry snapshot disables but the index has enabled.

        A re-enable writes the index first, so for a moment the snapshot a
        query holds can still disable entries the index already serves.
        Disabled index rows are filtered in SQL anyway; these few are not.
        """
        return self._store.entry_ids_by_title(
            {
                pack_id: record.disabled_titles
                for pack_id in pack_ids
                if (record := registry.packs.get(pack_id)) is not None and record.disabled_titles
            },
            enabled_only=True,
        )

    def _lexical_search(
        self, query: str, pack_ids: list[str], registry: Registry
    ) -> tuple[list[int], list[int]]:
        return self._store.lexical_candidates(
            query,
            pack_ids=pack_ids,
            limit=LEXICAL_CANDIDATES,
            exclude_ids=self._snapshot_only_disabled(pack_ids, registry),
        )

    def _semantic_search(
        self,
        snapshot: VectorSnapshot | None,
        query_vector: np.ndarray,
        pack_ids: list[str],
        registry: Registry,
    ) -> list[SemanticMatch]:
        # Disabled now in the index, or in the query's registry snapshot (a
        # re-enable writes the index first): neither may take a slot.
        excluded = self._store.disabled_entry_ids(pack_ids) | self._snapshot_only_disabled(
            pack_ids, registry
        )
        return semantic_candidates(
            snapshot, query_vector, allowed_pack_ids=pack_ids, exclude_entry_ids=excluded
        )

    def _render(
        self, ranked: list[RankedHit], query: str, language: str | None, registry: Registry
    ) -> tuple[list[dict[str, Any]], str]:
        entries = self._store.fetch_entries([hit.entry_id for hit in ranked])
        current = self._current_packs([entry.pack_id for entry in entries.values()], registry)
        cards: list[RenderCard] = []
        hits: list[dict[str, Any]] = []
        for ranked_hit in ranked:
            entry = entries.get(ranked_hit.entry_id)
            record = registry.packs.get(entry.pack_id) if entry is not None else None
            if entry is None or record is None or _disabled_in(entry, registry) or entry.pack_id not in current:
                continue
            # Show the passage that matched, not just the start of the entry.
            bodies = chunk_bodies(entry.content) or [entry.content]
            index = best_excerpt_index(query, bodies, ranked_hit.semantic_chunk)
            excerpt = bodies[index] if index == 0 else f"... {bodies[index]}"
            cards.append(
                RenderCard(
                    title=entry.title,
                    material_type=record.effective_material_type,
                    summary=entry.summary,
                    content=excerpt,
                    source_name=record.source.name,
                    source_license=record.source.license,
                )
            )
            hits.append(
                {
                    "pack_id": entry.pack_id,
                    "title": entry.title,
                    "material_type": record.effective_material_type,
                }
            )
        return hits, render_reference_block(cards, language=language)

    # ── management reads ────────────────────────────────────────────

    def _entry_payload(self, entry: StoredEntry, *, detail: bool) -> dict[str, Any]:
        record = self._registry.packs.get(entry.pack_id)
        preview = " ".join(entry.content.split())
        payload = {
            "pack_id": entry.pack_id,
            "title": entry.title,
            "terms": entry.terms,
            "tags": entry.tags,
            "summary": entry.summary,
            "content_preview": preview[:180] + ("..." if len(preview) > 180 else ""),
            "material_type": record.effective_material_type if record else "knowledge",
            "source": {
                "name": record.source.name if record else entry.pack_id,
                "homepage": record.source.homepage if record else "",
                "license": record.source.license if record else "",
            },
            "disabled": entry.disabled,
        }
        if detail:
            payload["content"] = entry.content
        return payload

    async def list_entries(
        self, *, query: str = "", pack_id: str = "", limit: int = 50, offset: int = 0
    ) -> dict[str, Any]:
        self._require_ready()
        if pack_id and not pack_id_is_valid(pack_id):
            raise KnowledgeUnavailable("invalid_request")
        query = query.strip()[:200]
        # Only registered packs whose rows are current: leftovers of a removal
        # whose cleanup failed (or of an import in flight) stay hidden.
        registry = self._registry
        visible = await asyncio.to_thread(
            self._current_packs, [pack_id] if pack_id else list(registry.packs), registry
        )
        pack_ids = sorted(visible)
        if query:
            entries = await asyncio.to_thread(
                self._store.search_entries, query, pack_ids=pack_ids, limit=limit + 1, offset=offset
            )
            has_more = len(entries) > limit
            entries = entries[:limit]
            total = None
        else:
            total = await asyncio.to_thread(self._store.count_entries, pack_ids=pack_ids)
            entries = await asyncio.to_thread(
                self._store.list_entries, pack_ids=pack_ids, limit=limit, offset=offset
            )
            has_more = offset + len(entries) < total
        # Recheck after the read: a pack replaced in between is left out
        # rather than shown with the snapshot's (older) metadata.
        still_current = await asyncio.to_thread(
            self._current_packs, sorted({entry.pack_id for entry in entries}), registry
        )
        entries = [entry for entry in entries if entry.pack_id in still_current]
        return {
            "total": total,
            "offset": offset,
            "limit": limit,
            "has_more": has_more,
            "items": [self._entry_payload(entry, detail=False) for entry in entries],
        }

    async def get_entry(self, pack_id: str, title: str) -> dict[str, Any]:
        self._require_ready()
        if not pack_id_is_valid(pack_id):
            raise KnowledgeUnavailable("invalid_request")
        registry = self._registry
        if not await asyncio.to_thread(self._current_packs, [pack_id], registry):
            raise KnowledgeUnavailable("not_found")
        entry = await asyncio.to_thread(self._store.get_entry, pack_id, title)
        if entry is None or not await asyncio.to_thread(self._current_packs, [pack_id], registry):
            raise KnowledgeUnavailable("not_found")
        return {"entry": self._entry_payload(entry, detail=True)}

    async def list_packs(self) -> list[dict[str, Any]]:
        self._require_ready()
        model_id = self._current_model_id()
        counts, stats = await asyncio.gather(
            asyncio.to_thread(self._store.entry_counts),
            asyncio.to_thread(self._store.chunk_stats, model_id),
        )
        embedding_state = self._embedding_state()
        packs: list[dict[str, Any]] = []
        for record in sorted(self._registry.packs.values(), key=lambda item: item.pack_id):
            entries, disabled = counts.get(record.pack_id, (0, 0))
            chunk = stats.get(record.pack_id, {"total": 0, "ready": 0, "failed": 0})
            packs.append(
                {
                    "pack_id": record.pack_id,
                    "source": {
                        "name": record.source.name,
                        "homepage": record.source.homepage,
                        "license": record.source.license,
                    },
                    "declared_material_type": record.declared_material_type,
                    "material_type_override": record.material_type_override,
                    "effective_material_type": record.effective_material_type,
                    "entries": entries,
                    "disabled_entries": disabled,
                    "auto_context": record.auto_context,
                    "local_embedding": record.local_embedding,
                    "chunks_total": chunk["total"],
                    "chunks_ready": chunk["ready"],
                    "chunks_failed": chunk["failed"],
                    "vector_state": self._vector_state(
                        record, chunk, embedding_state, indexing=self._registry.enabled
                    ),
                    "broken": record.pack_id in self._broken_packs,
                    "installed_at": record.installed_at,
                    "updated_at": record.updated_at,
                }
            )
        return packs

    @staticmethod
    def _vector_state(
        record: PackRecord, chunk: dict[str, int], embedding_state: str, *, indexing: bool = True
    ) -> str:
        total, ready = chunk["total"], chunk["ready"]
        if total <= 0:
            return "none"
        # Policy first: vectors kept from before the switch are not used.
        if not record.local_embedding:
            return "off"
        if ready >= total:
            return "complete"
        if not indexing:
            # Knowledge is switched off, so nothing is being computed.
            return "paused"
        if embedding_state != "ready":
            return "waiting"
        if ready + chunk["failed"] >= total:
            return "partial"
        return "building"

    async def status(self) -> dict[str, Any]:
        base: dict[str, Any] = {
            "state": self._state,
            "error_code": self._error_code,
            "embedding": {"state": self._embedding_state(), "model_id": self._current_model_id()},
            **self.availability(),
        }
        if self._state != "ready":
            return base
        packs = await self.list_packs()
        by_type = {kind: [p for p in packs if p["effective_material_type"] == kind] for kind in MATERIAL_TYPES}
        # Packs with local vectors off are keyword-only and never get more
        # vectors; counting them would keep the overview below 100% forever.
        vector_packs = [p for p in packs if p["local_embedding"]]
        chunks_total = sum(p["chunks_total"] for p in vector_packs)
        chunks_ready = sum(p["chunks_ready"] for p in vector_packs)
        base.update(
            {
                "packs": len(packs),
                "entries": sum(p["entries"] for p in packs),
                "disabled_entries": sum(p["disabled_entries"] for p in packs),
                "knowledge_packs": len(by_type["knowledge"]),
                "corpus_packs": len(by_type["corpus"]),
                "knowledge_entries": sum(p["entries"] for p in by_type["knowledge"]),
                "corpus_entries": sum(p["entries"] for p in by_type["corpus"]),
                "chunks_total": chunks_total,
                "chunks_ready": chunks_ready,
                "chunks_failed": sum(p["chunks_failed"] for p in vector_packs),
                "indexed_percent": round(chunks_ready * 100 / chunks_total, 1) if chunks_total else 0.0,
                "broken_packs": list(self._broken_packs),
                # The largest packs only: this is an overview, not a pack list.
                "sources": [
                    {"pack_id": p["pack_id"], "name": p["source"]["name"], "entries": p["entries"]}
                    for p in sorted(packs, key=lambda p: -p["entries"])[:STATUS_SOURCES]
                ],
            }
        )
        return base
