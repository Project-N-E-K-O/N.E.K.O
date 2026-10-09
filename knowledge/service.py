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
import logging
import random
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol, Sequence, TypeVar

import numpy as np

from utils.file_utils import atomic_write_bytes

from .diagnostics import KnowledgeDiagnostics
from .models import (
    MATERIAL_TYPES,
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
from .render import RenderCard, render_reference_block
from .retrieval import LEXICAL_CANDIDATES, fuse, semantic_candidates
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
MAX_QUERY_EMBEDDINGS = 2
MAX_QUERY_CHARS = 2_000
MAX_QUERY_LIMIT = 10
MAX_TRACKED_JOBS = 50
MAX_PENDING_IMPORTS = 3
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
    staged_bytes: int = 0

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
        self._job_queue: asyncio.Queue[str] = asyncio.Queue()
        self._index_wakeup = asyncio.Event()
        self._stopping = False
        self._tasks: list[asyncio.Task[Any]] = []
        self._vectors: VectorSnapshot | None = None
        self._vector_generation = 0
        self._vectors_dirty = False
        self._index_model_id: str | None = None
        self._query_embeddings: set[asyncio.Task[Any]] = set()
        self._admitting: dict[str, int] = {}
        self._vectors_built_for: tuple[int, str] | None = None
        self._vector_task: asyncio.Task[Any] | None = None
        self._availability_listeners: list[Callable[[], None]] = []

    # ── lifecycle ───────────────────────────────────────────────────

    async def start(self) -> None:
        """Open, reconcile and start background work; never raises."""
        try:
            async with self._write_lock:
                await asyncio.to_thread(self._open_blocking)
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
        self._index_wakeup.set()
        tasks = [task for task in (*self._tasks, self._vector_task) if task is not None]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.wait(tasks, timeout=2.0)

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
        broken: list[str] = []
        for pack_id, record in registry.packs.items():
            # The raw file is the source of truth even when the index is
            # current: a pack whose file is gone or altered cannot be rebuilt
            # and is not served.
            try:
                raw = (self.root / PACKS_DIR / record.file_name).read_bytes()
                if pack_sha256(raw) != record.pack_sha256:
                    raise KnowledgePackError("pack_file_mismatch")
                if indexed.get(pack_id) == record.pack_sha256:
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
        staging = self.root / STAGING_DIR
        if staging.is_dir():
            for path in staging.iterdir():
                if path.is_file():
                    path.unlink(missing_ok=True)
        referenced = {record.file_name for record in registry.packs.values()}
        for path in (self.root / PACKS_DIR).iterdir():
            if path.is_file() and path.suffix == ".json" and path.name not in referenced:
                path.unlink(missing_ok=True)

    # ── availability ────────────────────────────────────────────────

    def add_availability_listener(self, listener: Callable[[], None]) -> None:
        self._availability_listeners.append(listener)

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
        for listener in list(self._availability_listeners):
            try:
                listener()
            except Exception:
                logger.debug("[Knowledge] availability listener failed", exc_info=True)

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
        try:
            return await func()
        finally:
            self._write_lock.release()

    async def _update_record(self, pack_id: str, **changes: Any) -> PackRecord:
        async def run() -> PackRecord:
            record = self._registry.packs.get(pack_id)
            if record is None:
                raise KnowledgeUnavailable("not_found")
            updated = replace(record, updated_at=utc_now(), **changes)
            registry = self._registry.with_pack(updated)
            await asyncio.to_thread(save_registry, self.root, registry)
            self._publish_registry(registry)
            return updated

        return await self._locked(run)

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
        record = await self._update_record(pack_id, local_embedding=bool(enabled))
        if enabled:
            # Turning vectors back on is the user's retry for chunks that
            # previously failed to embed.
            await self._locked(lambda: asyncio.to_thread(self._store.reset_attempts, pack_id))
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
        for job in self._jobs.values():
            if job.pack_id == pack_id and job.state in ACTIVE_JOB_STATES:
                job.cancel_requested = True

        async def run() -> dict[str, Any]:
            record = self._registry.packs.get(pack_id)
            if record is None:
                raise KnowledgeUnavailable("not_found")
            registry = self._registry.without_pack(pack_id)

            def blocking() -> None:
                # Registry first: if the process dies after this, the startup
                # reconcile drops the orphaned rows and file.
                save_registry(self.root, registry)
                self._store.delete_pack(pack_id)
                (self.root / PACKS_DIR / record.file_name).unlink(missing_ok=True)

            await asyncio.to_thread(blocking)
            self._broken_packs = tuple(p for p in self._broken_packs if p != pack_id)
            self._publish_registry(registry)
            self._vector_generation += 1
            return {"pack_id": pack_id, "removed_entries": record.entries}

        result = await self._locked(run)
        self._schedule_vector_refresh()
        return result

    # ── imports ─────────────────────────────────────────────────────

    async def import_pack(self, raw: bytes) -> dict[str, Any]:
        self._require_ready()
        try:
            pack, canonical, chunks = await asyncio.to_thread(self._prepare_import, raw)
        except KnowledgePackError as exc:
            return {"ok": False, "reason": exc.reason}
        sha = pack_sha256(canonical)
        registry = self._registry
        existing = registry.packs.get(pack.pack_id)
        if existing is not None and existing.pack_sha256 == sha and pack.pack_id not in self._broken_packs:
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
        try:
            return await self._admit_import(pack, canonical, chunks, staged_bytes)
        finally:
            self._admitting.pop(pack.pack_id, None)

    async def _admit_import(
        self, pack: KnowledgePack, canonical: bytes, chunks: int, staged_bytes: int
    ) -> dict[str, Any]:
        registry = self._registry
        others = [record for record in registry.packs.values() if record.pack_id != pack.pack_id]
        if sum(record.entries for record in others) + len(pack.entries) > MAX_TOTAL_ENTRIES:
            return {"ok": False, "reason": "capacity_entries"}
        if sum(record.chunks for record in others) + chunks > MAX_TOTAL_CHUNKS:
            return {"ok": False, "reason": "capacity_chunks"}
        if chunks > MAX_CHUNKS_PER_PACK:
            return {"ok": False, "reason": "too_many_chunks"}
        installed_bytes = await asyncio.to_thread(self._installed_pack_bytes, others)
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
        )
        await asyncio.to_thread(atomic_write_bytes, self._staging_path(job.job_id), canonical)
        self._remember_job(job)
        self._job_queue.put_nowait(job.job_id)
        return {"ok": True, **job.to_json()}

    @staticmethod
    def _prepare_import(raw: bytes) -> tuple[KnowledgePack, bytes, int]:
        pack = decode_pack_bytes(raw)
        return pack, canonical_pack_bytes(pack), count_pack_chunks(pack)

    def _installed_pack_bytes(self, records: Sequence[PackRecord]) -> int:
        total = 0
        for record in records:
            try:
                total += (self.root / PACKS_DIR / record.file_name).stat().st_size
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

    def cancel_job(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if job is None or job.state not in ACTIVE_JOB_STATES:
            return False
        job.cancel_requested = True
        if job.state == "queued":
            self._finish_job(job, "cancelled")
        return True

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

    async def _job_loop(self) -> None:
        while not self._stopping:
            job_id = await self._job_queue.get()
            job = self._jobs.get(job_id)
            if job is None or job.state != "queued":
                await asyncio.to_thread(self._staging_path(job_id).unlink, missing_ok=True)
                continue
            job.state, job.updated_at = "building", utc_now()
            try:
                await self._locked(lambda job=job: self._commit_job(job), wait=True)
                self._finish_job(job, "active")
                self._vector_generation += 1
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
                await asyncio.to_thread(self._staging_path(job_id).unlink, missing_ok=True)

    async def _commit_job(self, job: ImportJob) -> None:
        if job.cancel_requested:
            raise InterruptedError("cancelled")
        previous = self._registry.packs.get(job.pack_id)

        def blocking() -> tuple[Registry, PackRecord]:
            raw = self._staging_path(job.job_id).read_bytes()
            pack = decode_pack_bytes(raw)
            sha = pack_sha256(raw)
            # Admission checked capacity without the lock; two imports racing
            # through it must not both land, so check again under the lock.
            others = [r for r in self._registry.packs.values() if r.pack_id != pack.pack_id]
            if sum(r.entries for r in others) + len(pack.entries) > MAX_TOTAL_ENTRIES:
                raise KnowledgePackError("capacity_entries")
            if sum(r.chunks for r in others) + job.chunks_total > MAX_TOTAL_CHUNKS:
                raise KnowledgePackError("capacity_chunks")
            if self._installed_pack_bytes(others) + len(raw) > MAX_TOTAL_PACK_BYTES:
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
                    should_cancel=lambda: job.cancel_requested or self._stopping,
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
            raw = (self.root / PACKS_DIR / previous.file_name).read_bytes()
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

    def _schedule_vector_refresh(self) -> None:
        model_id = self._current_model_id()
        if model_id is None or self._state != "ready":
            return
        key = (self._vector_generation, model_id)
        if self._vectors_built_for == key:
            return
        if self._vector_task is not None and not self._vector_task.done():
            return

        async def rebuild() -> None:
            try:
                snapshot = await asyncio.to_thread(self._store.load_vectors, model_id)
                self._vectors = snapshot
                self._vectors_built_for = key
            except Exception:
                logger.warning("[Knowledge] vector snapshot rebuild failed", exc_info=True)
                return
            # A change that arrived while this rebuild ran was skipped above;
            # pick it up now instead of waiting for the indexer's idle round.
            if not self._stopping:
                self._schedule_vector_refresh()

        self._vector_task = asyncio.create_task(rebuild(), name="knowledge-vector-snapshot")
        self._vector_task.add_done_callback(_consume)

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
        pack_ids = [
            record.pack_id for record in self._registry.packs.values() if record.local_embedding
        ]
        processed = 0
        while processed < INDEX_ROUND_CHUNKS and not self._stopping:
            rows = await asyncio.to_thread(
                self._store.pending_chunks, model_id=model_id, pack_ids=pack_ids, limit=INDEX_BATCH_SIZE
            )
            if not rows:
                break
            started = time.monotonic()
            vectors = await self.embedder.embed_batch([text for _id, _hash, text in rows])
            if self._current_model_id() != model_id:
                break
            blobs = [
                (chunk_id, text_hash, normalize_vector(vector) if vector is not None else None)
                for (chunk_id, text_hash, _text), vector in zip(rows, vectors)
            ]
            stored, failed = await self._locked(
                lambda blobs=blobs: asyncio.to_thread(
                    self._store.store_vectors, model_id=model_id, rows=blobs
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

    def _allowed_pack_ids(self, material_type: str) -> list[str]:
        wanted = MATERIAL_TYPES if material_type in ("", "auto", "all") else (material_type,)
        return [
            record.pack_id
            for record in self._registry.packs.values()
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
        allowed = self._allowed_pack_ids(str(material_type or "auto"))
        if not allowed:
            return finish("miss")
        if self._query_slots.locked():
            return finish("busy")
        async with self._query_slots:
            deadline = started + budget
            try:
                async with asyncio.timeout(max(deadline - time.monotonic(), 0.01)):
                    if mode == "sample":
                        ranked_ids = await self._sample(query, allowed, limit)
                        retrieval_mode = "sample"
                    else:
                        ranked_ids, retrieval_mode = await self._lookup(query, allowed, limit, deadline)
                    if not ranked_ids:
                        return finish("miss", retrieval_mode=retrieval_mode)
                    hits, context = await asyncio.to_thread(self._render, ranked_ids, language)
            except TimeoutError:
                return finish("timeout")
            except Exception as exc:
                logger.warning("[Knowledge] query failed: %s", type(exc).__name__, exc_info=True)
                return finish("error", error_type=type(exc).__name__)
        if not hits:
            return finish("miss", retrieval_mode=retrieval_mode)
        return finish("matched", hits=hits, context=context, retrieval_mode=retrieval_mode)

    async def _sample(self, tag: str, allowed: list[str], limit: int) -> list[int]:
        ids = await asyncio.to_thread(self._store.entry_ids_with_tag, tag, allowed)
        return random.sample(ids, min(limit, len(ids)))

    async def _lookup(
        self, query: str, allowed: list[str], limit: int, deadline: float
    ) -> tuple[list[int], str]:
        embed_task: asyncio.Task[Any] | None = None
        model_id = self._current_model_id()
        snapshot = self._vectors
        if (
            model_id is not None
            and snapshot is not None
            and snapshot.model_id == model_id
            # A lookup that ran out of budget leaves its embedding running.
            # Cap those, or slow inference piles up on the shared model.
            and len(self._query_embeddings) < MAX_QUERY_EMBEDDINGS
        ):
            embed_task = asyncio.create_task(self.embedder.embed(query))
            embed_task.add_done_callback(_consume)
            self._query_embeddings.add(embed_task)
            embed_task.add_done_callback(self._query_embeddings.discard)
        exact_ids, lexical_ids = await asyncio.to_thread(
            self._store.lexical_candidates, query, pack_ids=allowed, limit=LEXICAL_CANDIDATES
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
        # A pack with local vectors turned off is keyword-only, even if
        # vectors from before the switch are still in the snapshot.
        vector_packs = [
            pack_id
            for pack_id in allowed
            if (record := self._registry.packs.get(pack_id)) is not None and record.local_embedding
        ]
        semantic = semantic_candidates(snapshot, query_vector, allowed_pack_ids=vector_packs)
        candidate_ids = list(dict.fromkeys([*exact_ids, *lexical_ids, *(i for i, _ in semantic)]))
        entries = await asyncio.to_thread(self._store.fetch_entries, candidate_ids)
        allowed_set = set(allowed)
        usable = {
            entry_id
            for entry_id, entry in entries.items()
            if not entry.disabled and entry.pack_id in allowed_set
        }
        ranked = fuse(
            query,
            exact_ids=exact_ids,
            lexical_ids=lexical_ids,
            semantic=semantic,
            entries=entries,
            usable=usable,
            limit=limit,
        )
        return [hit.entry_id for hit in ranked], ("hybrid" if query_vector is not None else "bm25")

    def _render(self, entry_ids: list[int], language: str | None) -> tuple[list[dict[str, Any]], str]:
        entries = self._store.fetch_entries(entry_ids)
        registry = self._registry
        cards: list[RenderCard] = []
        hits: list[dict[str, Any]] = []
        for entry_id in entry_ids:
            entry = entries.get(entry_id)
            record = registry.packs.get(entry.pack_id) if entry is not None else None
            if entry is None or record is None or entry.disabled:
                continue
            cards.append(
                RenderCard(
                    title=entry.title,
                    material_type=record.effective_material_type,
                    summary=entry.summary,
                    content=entry.content,
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
        if query:
            entries = await asyncio.to_thread(
                self._store.search_entries, query, pack_id=pack_id, limit=limit + 1, offset=offset
            )
            has_more = len(entries) > limit
            entries = entries[:limit]
            total = None
        else:
            total = await asyncio.to_thread(self._store.count_entries, pack_id=pack_id)
            entries = await asyncio.to_thread(
                self._store.list_entries, pack_id=pack_id, limit=limit, offset=offset
            )
            has_more = offset + len(entries) < total
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
        entry = await asyncio.to_thread(self._store.get_entry, pack_id, title)
        if entry is None:
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
                    "vector_state": self._vector_state(record, chunk, embedding_state),
                    "broken": record.pack_id in self._broken_packs,
                    "installed_at": record.installed_at,
                    "updated_at": record.updated_at,
                }
            )
        return packs

    @staticmethod
    def _vector_state(record: PackRecord, chunk: dict[str, int], embedding_state: str) -> str:
        total, ready = chunk["total"], chunk["ready"]
        if total <= 0:
            return "none"
        # Policy first: vectors kept from before the switch are not used.
        if not record.local_embedding:
            return "off"
        if ready >= total:
            return "complete"
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
        chunks_total = sum(p["chunks_total"] for p in packs)
        chunks_ready = sum(p["chunks_ready"] for p in packs)
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
                "chunks_failed": sum(p["chunks_failed"] for p in packs),
                "indexed_percent": round(chunks_ready * 100 / chunks_total, 1) if chunks_total else 0.0,
                "broken_packs": list(self._broken_packs),
                "sources": [
                    {"pack_id": p["pack_id"], "name": p["source"]["name"], "entries": p["entries"]}
                    for p in packs
                ],
            }
        )
        return base
