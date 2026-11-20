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
import json
import hashlib
import shutil
import random
import threading
import time
import uuid
from collections import OrderedDict, deque
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, Protocol, Sequence, TypeVar

import numpy as np

from utils.file_utils import atomic_write_bytes, atomic_write_json
from utils.embedding_contract import EmbedDeferred

from .diagnostics import KnowledgeDiagnostics
from .models import (
    MATERIAL_TYPES,
    MAX_PACK_BYTES,
    MAX_TOTAL_PACK_BYTES,
    MAX_TOTAL_ENTRIES,
    KnowledgePack,
    KnowledgePackError,
    parse_pack,
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
    pack_file_name,
    save_registry,
    utc_now,
)
from .chunking import MAX_HEADER_CHARS, chunk_bodies
from .subscriptions import KnowledgeSubscription, Release, identity_conflict, parse_subscription
from .passive import PASSIVE_SEMANTIC_CANDIDATES, Calibration, PassiveHit, select_hits, thresholds_for
from .render import RenderCard, render_reference_block, render_turn_cards
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
from .importing.sources import SourceStore
from .importing.projection import project_pack

from config.turn_context_settings import (
    TURN_CONTEXT_ITEM_MAX_TOKENS,
    TURN_CONTEXT_QUERY_MAX_CHARS,
)


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
PASSIVE_DIAGNOSTIC_RESULTS = frozenset({"matched", "timeout", "busy", "error"})
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
# Small batches keep each burst of inference short, so memory and chat
# requests get the shared model sooner.
INDEX_BATCH_SIZE = 4
INDEX_ROUND_CHUNKS = 64
# Minimum pause after a batch; the actual pause also scales with how long the
# batch took, so that background indexing is busy at most INDEX_TARGET_DUTY of
# the time (inference may use several threads while it runs).
INDEX_BATCH_PAUSE_SECONDS = 0.5
INDEX_TARGET_DUTY = 0.25
# The measured time is wall-clock: it includes waiting for the shared model and
# a laptop sleeping mid-batch. Cap the pause so neither stalls indexing.
INDEX_MAX_BATCH_PAUSE_SECONDS = 30.0
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
    # Registry/artifact publication is the user-visible commit point. The
    # earlier SQLite gate alone does not prove an install became active.
    published: bool = False
    # Market metadata of a subscription install; None for a local import.
    subscription: KnowledgeSubscription | None = None
    kind: str = "pack_import"
    origin: str = ""
    initial_enabled: bool | None = None
    archive_sha256: str = ""
    result: dict[str, Any] | None = None
    seed_vectors: list[dict] = field(default_factory=list, repr=False, compare=False)
    # Keep the persisted format only to reject retries of retired text exports;
    # every newly admitted export is a complete v2 package.
    export_format: int = 2
    export_version: str = "1"
    # Explicitly deleted/discarded jobs cannot be revived by a worker flush.
    # Evicted history is still persisted, so absence from _jobs is not enough.
    export_deleted: bool = field(default=False, repr=False, compare=False)
    frozen_input: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)
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
            "subscription": self.subscription.to_json() if self.subscription else None,
            "kind": self.kind,
            "result": self.result,
        }


# The thread work of the query running in the current task (see _query_lease).
_QUERY_WORK: contextvars.ContextVar[list[concurrent.futures.Future[Any]] | None] = (
    contextvars.ContextVar("knowledge_query_work", default=None)
)


def _job_files(jobs: Sequence[ImportJob], *, installing: Registry | None) -> frozenset[str]:
    """File names whose bytes are counted from ``jobs`` (their ``staged_bytes``).

    Each job's staged upload. With ``installing`` (the current registry), the
    outcome the jobs lead to is counted instead of their transient files: the
    pack file each job installs (the runner writes it before indexing, which
    can take long, and the job stays active until done) and the registered
    file it replaces.
    """
    names = {f"{job.job_id}.json" for job in jobs}
    if installing is not None:
        names |= {pack_file_name(job.pack_id, job.staged_sha256) for job in jobs}
        names |= {
            record.file_name
            for job in jobs
            if (record := installing.packs.get(job.pack_id)) is not None
        }
    return frozenset(names)


def _batch_pause(inference_seconds: float) -> float:
    """Pause after an index batch: idle at least (1 - duty) / duty times as long
    as the batch computed, so indexing stays at or below INDEX_TARGET_DUTY."""
    idle_ratio = (1.0 - INDEX_TARGET_DUTY) / INDEX_TARGET_DUTY
    pause = max(INDEX_BATCH_PAUSE_SECONDS, inference_seconds * idle_ratio)
    return min(pause, INDEX_MAX_BATCH_PAUSE_SECONDS)


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


def _covers(releases: frozenset[Release] | None, subscription: KnowledgeSubscription | None) -> bool:
    """Whether a removal naming ``releases`` (``None``: all) overrides an import carrying ``subscription``."""
    return releases is None or (subscription is not None and subscription.release in releases)


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
        # Passive turn context: probe vectors and backgrounds per model.
        self._calibration = Calibration()
        self._admitting: dict[str, int] = {}
        # Request order: imports and removals each take the next number when
        # the request arrives (before parsing or waiting for the lock). An
        # import gives way only to removals of its pack that arrived later
        # and cover its release (see ``_covers``): ``_removed_at`` holds the
        # latest committed one per release (``None``: every release),
        # ``_pending_removals`` those still waiting.
        self._request_seq = 0
        self._removed_at: dict[str, dict[Release | None, int]] = {}
        # Values are frozensets, replaced rather than mutated: the import's
        # cancellation check reads them from a worker thread.
        self._pending_removals: dict[str, frozenset[tuple[int, frozenset[Release] | None]]] = {}
        self._query_pool: concurrent.futures.ThreadPoolExecutor | None = None
        self._parsing = 0
        self._detached: set[asyncio.Future[Any]] = set()
        # Query and parse threads, which outlive a request that stops waiting.
        # Their completion callbacks run on worker threads, hence the lock.
        self._thread_work: set[concurrent.futures.Future[Any]] = set()
        self._thread_work_lock = threading.Lock()
        self._admission_lock = asyncio.Lock()
        self._startup_work: asyncio.Future[Any] | None = None
        self._parse_pool: concurrent.futures.ThreadPoolExecutor | None = None
        # Set (and replaced) whenever a removal finishes, committed or not.
        self._removal_settled = asyncio.Event()
        self._vectors_built_for: tuple[int, str] | None = None
        self._vector_task: asyncio.Task[Any] | None = None
        # Subscription imports between arrival and their job being known (or
        # their refusal), by arrival number: the market page must be able to
        # see an import that has not become a job yet.
        self._incoming: dict[int, KnowledgeSubscription] = {}
        # Arrival numbers whose admission runs on its own (shielded), which
        # then owns removing the ``_incoming`` entry.
        self._admission_owned: set[int] = set()
        self._sources = SourceStore(self.root)
        self._job_persist_lock = asyncio.Lock()
        # A package installed before model assets existed may seed vectors once
        # their real identity is known, without recomputing its native model.
        self._seeded_artifacts: set[tuple[str, str]] = set()
        # A replan can commit SQLite before registry persistence fails. Block
        # further capacity decisions/encoding until their counts agree again.
        self._pending_chunk_reconcile = False
        self._source_capabilities: dict[str, tuple[tuple, bool]] = {}

    # ── lifecycle ───────────────────────────────────────────────────

    async def start(self) -> None:
        """Open, reconcile and start background work; never raises."""
        try:
            async with self._write_lock:
                # Tracked so stop() can wait for it: cancelling start() stops
                # the waiting, not the thread rebuilding the index.
                self._startup_work = asyncio.ensure_future(self._thread(self._open_blocking))
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
            logger.warning("[Knowledge] startup failed: %s", type(exc).__name__)
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
        self._calibration.cancel()
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
        deadline = time.monotonic() + DETACHED_WRITE_WAIT_SECONDS
        if self._detached:
            await asyncio.wait(set(self._detached), timeout=DETACHED_WRITE_WAIT_SECONDS)
            if self._detached:
                logger.warning(
                    "[Knowledge] %d write(s) still running at shutdown", len(self._detached)
                )
        for pool in (self._query_pool, self._parse_pool):
            if pool is not None:
                pool.shutdown(wait=False, cancel_futures=True)
        # A query or parse thread whose request timed out or went away may
        # still have knowledge.db open or a pack in memory; within the same
        # bound, let it end before reporting stopped.
        with self._thread_work_lock:
            running = [future for future in self._thread_work if not future.done()]
        if running:
            await asyncio.wait(
                [asyncio.wrap_future(future) for future in running],
                timeout=max(deadline - time.monotonic(), 0.1),
            )
            still = sum(1 for future in running if not future.done())
            if still:
                logger.warning("[Knowledge] %d thread(s) still running at shutdown", still)

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
            broken, registry = self._reconcile_blocking(registry)
        except Exception as exc:
            logger.warning("[Knowledge] index reconcile failed (%s); rebuilding", type(exc).__name__)
            self._store.remove_files()
            self._store.initialize()
            broken, registry = self._reconcile_blocking(registry)
        self._registry = registry
        self._broken_packs = tuple(broken)
        self._load_jobs_blocking()
        self._clean_files_blocking(registry)

    def _load_jobs_blocking(self) -> None:
        """Reconcile durable task facts against the registry's sole commit point."""
        loaded = []
        for path in sorted((self.root / "jobs").glob("*.json")):
            if path.name.endswith(".pack.json"):
                continue
            try:
                with path.open("rb") as stream:
                    raw = stream.read(128 * 1024 + 1)
                if len(raw) > 128 * 1024:
                    continue
                data = json.loads(raw)
                if not isinstance(data, dict):
                    continue
                fields = {key: value for key, value in data.items() if key in {
                    "job_id", "pack_id", "state", "created_at", "updated_at", "entries_total", "chunks_total", "reason",
                    "cancel_requested", "arrived_at", "staged_bytes", "staged_sha256", "committed", "kind", "origin",
                    "initial_enabled", "archive_sha256", "result", "export_format", "export_version", "frozen_input",
                }}
                if data.get("subscription") is not None:
                    fields["subscription"] = parse_subscription(data["subscription"], strict=False)
                job = ImportJob(**fields)
                if path.stem != job.job_id or len(job.job_id) != 32:
                    continue
                if job.state in ACTIVE_JOB_STATES:
                    record = self._registry.packs.get(job.pack_id)
                    if (job.kind != "pack_export" and record is not None
                            and record.pack_sha256 == job.staged_sha256
                            and record.subscription == job.subscription
                            and record.archive_sha256 == job.archive_sha256):
                        job.state, job.reason, job.committed, job.published = "active", "", True, True
                    else:
                        job.state, job.reason = "failed", "process_interrupted"
                    atomic_write_json(path, self._job_record(job))
                loaded.append(job)
            except (OSError, ValueError, TypeError):
                continue
        # UUID filenames have no chronological order. Keep the newest bounded
        # task window after restart, matching normal in-process admission.
        for job in sorted(loaded, key=lambda value: (value.created_at, value.job_id)):
            self._remember_job(job)

    @staticmethod
    def _job_record(job: ImportJob) -> dict[str, Any]:
        """Freeze safe task metadata; source bodies and vector lists stay out."""
        return {**job.to_json(), "cancel_requested": job.cancel_requested, "arrived_at": job.arrived_at,
                "staged_bytes": job.staged_bytes, "staged_sha256": job.staged_sha256,
                "committed": job.committed, "origin": job.origin, "initial_enabled": job.initial_enabled,
                "archive_sha256": job.archive_sha256, "export_format": job.export_format,
                "export_version": job.export_version, "frozen_input": job.frozen_input}

    async def _persist_job(self, job: ImportJob) -> None:
        """Serialize writes so a late queued snapshot cannot replace terminal facts."""
        async with self._job_persist_lock:
            if job.export_deleted:
                return
            await self._thread(atomic_write_json, self.root / "jobs" / (job.job_id + ".json"), self._job_record(job))

    def _reconcile_blocking(self, registry: Registry) -> tuple[list[str], Registry]:
        """Make ``knowledge.db`` match the registry; return packs whose raw file is lost."""
        indexed = self._store.pack_versions()
        for pack_id in indexed.keys() - registry.packs.keys():
            self._store.delete_pack(pack_id)
        entry_counts = self._store.entry_counts()
        chunk_counts = self._store.chunk_stats(None)
        search_rows = self._store.search_row_counts()
        broken: list[str] = []
        original_registry = registry
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
                logger.warning("[Knowledge] pack %s cannot be rebuilt (%s)", pack_id, type(exc).__name__)
                self._store.delete_pack(pack_id)
                broken.append(pack_id)
                continue
            options = self._rebuild_options(record)
            chunks = count_pack_chunks(pack, fit=options.get("fit"))
            others = sum(value.chunks for key, value in registry.packs.items() if key != pack_id)
            if chunks > MAX_CHUNKS_PER_PACK or others + chunks > MAX_TOTAL_CHUNKS:
                # Preserve keyword availability within the original admitted
                # budget. A later token replan is explicitly refused before
                # native inference, rather than truncating text or overfilling.
                options.pop("fit", None)
                chunks = count_pack_chunks(pack)
            self._store.replace_pack(pack, pack_sha256=record.pack_sha256,
                                     disabled_keys=record.disabled_titles, **options)
            if chunks != record.chunks:
                registry = registry.with_pack(replace(record, chunks=chunks))
            logger.info("[Knowledge] rebuilt index of pack %s", pack_id)
        if registry != original_registry:
            save_registry(self.root, registry)
        return broken, registry

    def _rebuild_options(self, record: PackRecord) -> dict[str, Any]:
        """Recover verified package vectors without loading or invoking a model."""
        options: dict[str, Any] = {}
        fit = self._chunk_fit()
        if fit is not None:
            options["fit"] = fit
        if record.archive_sha256:
            from .archive import read_archive

            try:
                spec = getattr(self.embedder, "spec", lambda: None)()
                path = self.artifact_path(record.archive_sha256)
                checked = read_archive(path, expected_spec=spec)
                if checked["artifact_sha256"] != record.archive_sha256:
                    raise KnowledgePackError("artifact_hash_mismatch")
                if checked["seed_vectors"]:
                    options["seed_vectors"] = checked["seed_vectors"]
            except (OSError, KnowledgePackError):
                # Canonical text remains authoritative if a retained optional
                # artifact was damaged; ordinary backfill can rebuild vectors.
                pass
        return options

    def _clean_files_blocking(self, registry: Registry) -> None:
        """Drop leftovers; a file that cannot be deleted now is retried next start."""
        staging = self.root / STAGING_DIR
        leftovers = [path for path in staging.iterdir() if path.is_file()] if staging.is_dir() else []
        # Pack revisions and source history are retained user data. Only
        # disposable staging files belong to startup cleanup.
        for path in leftovers:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.warning("[Knowledge] could not delete leftover %s", path.name)

    # ── availability ────────────────────────────────────────────────

    def availability(self) -> dict[str, bool]:
        registry = self._registry
        has_usable = any(
            record.enabled and record.entries > len(record.disabled_titles)
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
        await self._thread(save_registry, self.root, registry)
        self._publish_registry(registry)
        return updated

    async def _update_record(self, pack_id: str, **changes: Any) -> PackRecord:
        return await self._locked(lambda: self._apply_record_update(pack_id, **changes))

    async def set_enabled(self, enabled: bool) -> dict[str, Any]:
        async def run() -> None:
            registry = replace(self._registry, enabled=bool(enabled))
            await self._thread(save_registry, self.root, registry)
            self._publish_registry(registry)

        await self._locked(run)
        self._index_wakeup.set()
        return {"enabled": bool(enabled)}

    async def set_pack_auto_context(self, pack_id: str, enabled: bool) -> dict[str, Any]:
        record = await self._update_record(pack_id, auto_context=bool(enabled))
        return {"pack_id": pack_id, "auto_context": record.auto_context}

    async def set_pack_enabled(self, pack_id: str, enabled: bool) -> dict[str, Any]:
        """Gate all retrieval while preserving the pack and its vector policy."""
        record = await self._update_record(pack_id, enabled=bool(enabled))
        return {"pack_id": pack_id, "enabled": record.enabled}

    async def set_pack_local_embedding(self, pack_id: str, enabled: bool) -> dict[str, Any]:
        async def run() -> PackRecord:
            record = await self._apply_record_update(pack_id, local_embedding=bool(enabled))
            if enabled:
                # Turning vectors back on is the user's retry for chunks that
                # failed to embed. The policy is already saved, so a failed
                # reset must not report the change itself as failed.
                try:
                    await self._thread(self._store.reset_attempts, pack_id)
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
            found = await self._thread(self._store.set_disabled, pack_id, title, bool(disabled))
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
                await self._thread(save_registry, self.root, registry)
            except BaseException:
                # The registry still says the old thing; so must the index.
                await self._thread(self._store.set_disabled, pack_id, title, was_disabled)
                raise
            self._publish_registry(registry)
            return {"pack_id": pack_id, "disabled": bool(disabled), "disabled_entries": len(keys)}

        return await self._locked(run)

    async def remove_pack(self, pack_id: str, *, _retain_sources: bool = False) -> dict[str, Any]:
        """Remove the pack row; only last-document removal keeps visible history."""
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

        def decide(record: PackRecord | None) -> tuple[PackRecord | None, str]:
            if record is None:
                raise KnowledgeUnavailable("not_found")
            return record, ""

        return await self._remove(pack_id, None, decide, retain_sources=_retain_sources)

    async def remove_subscription(
        self, pack_id: str, identity: tuple[str, str, str], releases: frozenset[Release]
    ) -> dict[str, Any]:
        """Unsubscribe: remove the named releases of a market package, installed or on the way.

        One request settles it all under the write lock: imports of the pack
        that arrived before this request and carry one of ``releases`` stand
        down (queued, building, or still being admitted and not yet a job),
        and the installed pack is removed if it is one of those releases.

        Safe to repeat: a pack that is not installed, or that is installed as
        a release not named here (a later subscription), is left alone and
        reported as success. A pack that is there under another identity (a
        local pack, another market package) is an error, as is any import of
        this package under another pack id.

        The lock is waited for as long as any request waits for it
        (``WRITE_LOCK_TIMEOUT_SECONDS``): a large import committing past its
        point of no return can hold it longer, and then the answer is
        ``knowledge_busy``, for the caller to repeat the same request.
        """
        self._require_ready()
        self._check_unsubscribe_target(pack_id, identity)

        def decide(record: PackRecord | None) -> tuple[PackRecord | None, str]:
            # Checked again under the lock: an import may have landed since.
            self._check_unsubscribe_target(pack_id, identity)
            if record is None:
                return None, "not_installed"
            if record.subscription is None or record.subscription.release not in releases:
                return None, "other_release"
            return record, ""

        result = await self._remove(pack_id, releases, decide)
        return {**result, "removed": not result.get("kept")}

    def _check_unsubscribe_target(self, pack_id: str, identity: tuple[str, str, str]) -> None:
        record = self._registry.packs.get(pack_id)
        if record is not None and (record.subscription is None or record.subscription.identity != identity):
            raise KnowledgeUnavailable("subscription_identity_mismatch")
        provider, package_id = identity[0], identity[1]
        claims = [
            (subscription.pack_id, subscription.identity)
            for subscription in (
                *(item.subscription for item in self._registry.packs.values()),
                *(job.subscription for job in self._jobs.values() if job.state in ACTIVE_JOB_STATES),
                *self._incoming.values(),
            )
            if subscription is not None
            and subscription.provider == provider
            and subscription.provider_package_id == package_id
        ]
        # The whole identity: a removal for an old remote id must not match
        # an import of the package that is not installed yet either.
        if any(claim != (pack_id, identity) for claim in claims):
            raise KnowledgeUnavailable("subscription_identity_mismatch")

    async def _remove(
        self,
        pack_id: str,
        releases: frozenset[Release] | None,
        decide: Callable[[PackRecord | None], tuple[PackRecord | None, str]],
        *,
        retain_sources: bool = False,
    ) -> dict[str, Any]:
        """Remove ``pack_id`` as ``decide`` rules under the lock, overriding the
        imports it covers (``releases``; ``None`` covers every import of the pack).

        The request's place in the order is taken here, before any await.
        """
        self._request_seq += 1
        my_seq = self._request_seq
        removal = (my_seq, releases)
        self._pending_removals[pack_id] = self._pending_removals.get(pack_id, frozenset()) | {removal}

        async def run() -> dict[str, Any]:
            record, kept = decide(self._registry.packs.get(pack_id))
            registry = self._registry.without_pack(pack_id) if record is not None else None
            if registry is not None:
                # Commit the history-list preference before removing the active
                # registry entry. A failure leaves that active row authoritative;
                # no post-commit sidecar error can turn removal into a failure.
                await self._thread(self._sources.set_retained, pack_id, retain_sources)
                await self._thread(save_registry, self.root, registry)
            marks = dict(self._removed_at.get(pack_id, {}))
            for key in releases if releases is not None else (None,):
                marks[key] = max(marks.get(key, 0), my_seq)
            self._removed_at[pack_id] = marks
            for job in list(self._jobs.values()):
                if (
                    job.pack_id == pack_id
                    and job.state == "queued"
                    and job.arrived_at < my_seq
                    and _covers(releases, job.subscription)
                ):
                    job.cancel_requested = True
                    self._finish_job(job, "cancelled")
                    self._unqueue(job.job_id)
                    await self._discard_staging(job.job_id)
                    with contextlib.suppress(OSError):
                        await self._persist_job(job)
            if record is None or registry is None:
                return {"pack_id": pack_id, "removed_entries": 0, "kept": kept}
            file_name = record.file_name

            def cleanup() -> None:
                # Best effort: the removal is committed by the registry write,
                # and the startup reconcile drops leftover rows and files.
                try:
                    self._store.delete_pack(pack_id)
                except Exception as exc:
                    logger.warning("[Knowledge] index cleanup of %s failed (%s)", pack_id, type(exc).__name__)
                try:
                    original = self.root / PACKS_DIR / file_name
                    if original.is_file():
                        # Pack removal changes the active registry selection;
                        # retain its immutable revision before dropping a name.
                        history = self.root / "history" / pack_id / file_name
                        history.parent.mkdir(parents=True, exist_ok=True)
                        atomic_write_bytes(history, _read_bounded(original))
                        original.unlink(missing_ok=True)
                except OSError:
                    logger.warning("[Knowledge] could not delete the file of %s", pack_id)

            self._broken_packs = tuple(p for p in self._broken_packs if p != pack_id)
            self._publish_registry(registry)
            self._vector_generation += 1
            await self._thread(cleanup)
            return {"pack_id": pack_id, "removed_entries": record.entries}

        try:
            result = await self._locked(run)
        finally:
            pending = self._pending_removals.get(pack_id, frozenset()) - {removal}
            if pending:
                self._pending_removals[pack_id] = pending
            else:
                self._pending_removals.pop(pack_id, None)
            settled, self._removal_settled = self._removal_settled, asyncio.Event()
            settled.set()
        self._schedule_vector_refresh()
        return result

    def _identity_conflict(self, pack_id: str, subscription: KnowledgeSubscription | None) -> bool:
        """Whether installing this would break the rules in ``knowledge.subscriptions``."""
        installed = {key: record.subscription for key, record in self._registry.packs.items()}
        if identity_conflict(pack_id, subscription, installed):
            return True
        # An import of another pack may already be installing this package.
        return subscription is not None and any(
            job.state in ACTIVE_JOB_STATES
            and job.pack_id != pack_id
            and job.subscription is not None
            and job.subscription.provider_package_id == subscription.provider_package_id
            for job in self._jobs.values()
        )

    def _removal_committed_over(
        self, pack_id: str, subscription: KnowledgeSubscription | None, arrived_at: int
    ) -> bool:
        """Whether a committed removal that arrived after this import covers it."""
        marks = self._removed_at.get(pack_id, {})
        latest = marks.get(None, -1)
        if subscription is not None:
            latest = max(latest, marks.get(subscription.release, -1))
        return latest > arrived_at

    def _removal_pending_over(self, job: ImportJob) -> bool:
        """Whether a removal still waiting, that arrived after this import, covers it."""
        return any(
            seq > job.arrived_at and _covers(releases, job.subscription)
            for seq, releases in self._pending_removals.get(job.pack_id, ())
        )

    def _superseded(self, job: ImportJob) -> bool:
        """Whether a removal of the job's pack overrides this import."""
        return (
            job.cancel_requested
            or self._removal_pending_over(job)
            or self._removal_committed_over(job.pack_id, job.subscription, job.arrived_at)
        )

    # ── imports ─────────────────────────────────────────────────────

    def _sources_managed(self, pack_id: str) -> bool:
        """Only local originals that still project the current pack are editable."""
        if not self._sources.has_history(pack_id):
            return False
        try:
            self._source_pack(pack_id)
            return True
        except (KnowledgePackError, OSError, ValueError):
            return False

    def _listed_sources_managed(self, pack_id: str) -> bool:
        """Cache listing hints; modifying operations still validate full source authority."""
        record = self._registry.packs.get(pack_id)
        if record is None or not self._sources.has_history(pack_id):
            return False
        folder = self.root / "sources" / pack_id
        try:
            paths = [self.root / PACKS_DIR / record.file_name, folder / "manifest.json", folder / "versions"]
            paths.extend(sorted((folder / "versions").iterdir()))
            signature = (record.pack_sha256, *(
                (str(path), info.st_mtime_ns, info.st_ctime_ns, info.st_size)
                for path in paths for info in (path.stat(),)
            ))
        except OSError:
            return False
        cached = self._source_capabilities.get(pack_id)
        if cached is not None and cached[0] == signature:
            return cached[1]
        managed = self._sources_managed(pack_id)
        self._source_capabilities[pack_id] = (signature, managed)
        return managed

    def _source_pack(self, pack_id: str) -> KnowledgePack | None:
        """Read the active source projection, never an uncommitted sidecar."""
        record = self._registry.packs.get(pack_id)
        if record is None:
            return None
        if not self._sources.has_history(pack_id):
            raise KnowledgePackError("invalid_request")
        raw = _read_bounded(self.root / PACKS_DIR / record.file_name)
        if pack_sha256(raw) != record.pack_sha256:
            raise KnowledgePackError("pack_file_mismatch")
        pack = decode_pack_bytes(raw)
        documents = self._sources.documents(pack, pack_id)
        projection = project_pack(pack_id, pack.source.name, documents)
        expected = parse_pack(json.loads(canonical_pack_bytes(projection)))
        if pack.entries != expected.entries:
            # Existing personal packs used hard windows before word-aware
            # projection. Verify that exact historical rendering too, without
            # rewriting originals or granting unrelated offline packs access.
            legacy = project_pack(pack_id, pack.source.name, documents, legacy_windows=True)
            if pack.entries != parse_pack(json.loads(canonical_pack_bytes(legacy))).entries:
                raise KnowledgePackError("invalid_request")
        return pack

    async def preview_source(self, filename: str, raw: bytes | Path, *, pack_id: str = "", doc_id: str = "", name: str = "") -> dict[str, Any]:
        """Parse an original without installing it or requesting model loading."""
        self._require_ready()
        if pack_id:
            record = self._registry.packs.get(pack_id)
            if record is None or not await self._thread(self._sources_managed, pack_id):
                raise KnowledgePackError("invalid_request")
            name = record.source.name
        value = await self._thread(self._sources.preview, filename, raw, pack_id=pack_id, doc_id=doc_id, name=name)
        return {"ok": True, "pack_id": value["pack_id"], "preview": {
            key: value[key] for key in ("preview_id", "doc_id", "sha256", "name", "content", "chars", "sections")
        }}

    async def list_sources(self, pack_id: str) -> dict[str, Any]:
        """List current source selections, retaining all immutable past versions."""
        def read():
            if not self._sources_managed(pack_id):
                return []  # Finished packages contain no editable originals.
            pack = self._source_pack(pack_id)
            documents = self._sources.documents(pack, pack_id)
            active = {row["doc_id"]: row for row in documents}
            return [{"doc_id": row["doc_id"], "name": active.get(row["doc_id"], row)["name"],
                     "current_version": active[row["doc_id"]]["version"] if row["doc_id"] in active else None,
                     "removed": row["doc_id"] not in active,
                     "versions_count": len(self._sources.versions(pack_id, row["doc_id"])),
                     "chars": active.get(row["doc_id"], row)["chars"], "updated_at": row["created_at"]}
                    for row in self._sources.all_documents(pack_id)]
        return {"ok": True, "pack_id": pack_id, "sources": await self._thread(read)}

    async def source_versions(self, pack_id: str, doc_id: str) -> dict[str, Any]:
        """Expose version metadata only; original paths never cross the API."""
        def read():
            if not self._sources_managed(pack_id):
                return []
            active = self._sources.documents(self._source_pack(pack_id), pack_id)
            current = next((row["version"] for row in active if row["doc_id"] == doc_id), None)
            return [{**{key: row[key] for key in ("version", "name", "chars", "sha256", "created_at")},
                     "current": row["version"] == current} for row in self._sources.versions(pack_id, doc_id)]
        return {"ok": True, "pack_id": pack_id, "doc_id": doc_id, "versions": await self._thread(read)}

    async def _project_sources(
        self, pack_id: str, name: str, documents: list[dict], *, enabled: bool = False,
        source_confirmation: bool | None = None,
    ) -> dict[str, Any]:
        """Send a source projection through the one existing import runner."""
        pack = await self._thread(project_pack, pack_id, name, documents)
        # v1 validation is reused before staging; no source-specific bypass of
        # entry, chunk or total pack budgets is admitted.
        canonical = await self._thread(lambda: canonical_pack_bytes(parse_pack(json.loads(canonical_pack_bytes(pack)))))
        frozen = {"documents": [{"doc_id": row["doc_id"], "version": row["version"]} for row in documents]}
        if source_confirmation is not None:
            # Consent is independent of the requested initial enabled flag;
            # another window can change that flag before this task activates.
            frozen.update(source_confirmation_required=True, enabled_confirmed=source_confirmation)
        return await self.import_pack(canonical, _options={"kind": "import_commit", "origin": "imported", "enabled": enabled,
                                                           "frozen_input": frozen})

    async def commit_source(
        self, preview_id: str, sha256: str, *, enabled: bool = False, enabled_confirmed: bool = False,
    ) -> dict[str, Any]:
        """Retain the frozen original, then enqueue a disabled-by-default pack."""
        async def run():
            value = await self._thread(self._sources.retain_preview, preview_id, sha256)
            pack_id = value["pack_id"]
            pack = await self._thread(self._source_pack, pack_id)
            documents = await self._thread(self._sources.documents, pack, pack_id)
            documents = [row for row in documents if row["doc_id"] != value["doc_id"]] + [value]
            return await self._project_sources(pack_id, value["pack_name"], documents, enabled=enabled,
                                               source_confirmation=enabled_confirmed)
        return await self._locked(run)

    async def remove_source(self, pack_id: str, doc_id: str) -> dict[str, Any]:
        """Remove an active selection without deleting its original or history."""
        async def run():
            pack = await self._thread(self._source_pack, pack_id)
            if pack is None:
                raise KnowledgePackError("source_not_found")
            documents = await self._thread(self._sources.documents, pack, pack_id)
            retained = [row for row in documents if row["doc_id"] != doc_id]
            if len(retained) == len(documents):
                raise KnowledgePackError("source_not_found")
            if not retained and (pack_id in self._admitting or any(
                    job.pack_id == pack_id and job.kind != "pack_export" and job.state in ACTIVE_JOB_STATES
                    for job in self._jobs.values())):
                # Removing the last active document removes the pack, which
                # would cancel an append of another document still waiting.
                raise KnowledgePackError("job_in_progress")
            return await self._project_sources(pack_id, pack.source.name, retained) if retained else None
        result = await self._locked(run)
        if result is not None:
            return result
        # An empty schema-v1 pack is invalid. Removing its last source uses the
        # existing pack-removal ordering while leaving every retained revision.
        await self.remove_pack(pack_id, _retain_sources=True)
        stamp = utc_now()
        job = ImportJob(uuid.uuid4().hex, pack_id, "active", stamp, stamp, kind="import_commit")
        self._remember_job(job)
        with contextlib.suppress(OSError):
            await self._persist_job(job)
        return {"ok": True, **job.to_json()}

    async def restore_source(self, pack_id: str, doc_id: str, version: int) -> dict[str, Any]:
        """Select an immutable past version through the same pack import path."""
        async def run():
            value = await self._thread(self._sources.version, pack_id, doc_id, version)
            pack = await self._thread(self._source_pack, pack_id)
            documents = await self._thread(self._sources.documents, pack, pack_id)
            documents = [row for row in documents if row["doc_id"] != doc_id] + [value]
            return await self._project_sources(pack_id, pack.source.name if pack else value["pack_name"], documents)
        return await self._locked(run)

    async def _import_archive_input(
        self, raw: bytes | Path, *, subscription: KnowledgeSubscription | None, options: dict,
        arrived_at: int, parse_work: list[concurrent.futures.Future[Any]],
    ) -> dict:
        """Validate ZIP files off-loop and keep their exact bytes for recovery."""
        from .archive import read_archive

        async def clean_input(path, running):
            # Cancellation cannot stop a queued native reader/writer. Its
            # scratch belongs to those real futures until they have ended.
            if running:
                await asyncio.gather(*(asyncio.wrap_future(future) for future in running), return_exceptions=True)
                running.clear()
            with contextlib.suppress(OSError):
                await self._thread(path.unlink, missing_ok=True)

        temporary = None
        try:
            if isinstance(raw, bytes):
                folder = self.root / STAGING_DIR
                await self._thread(folder.mkdir, parents=True, exist_ok=True)
                temporary = folder / (uuid.uuid4().hex + ".upload.zip")
                await self._parse_thread(parse_work, atomic_write_bytes, temporary, raw)
                path = temporary
            else:
                path = raw
            def magic():
                with path.open("rb") as stream:
                    return stream.read(4)
            if await self._parse_thread(parse_work, magic) != b"PK\x03\x04":
                return await self._import_parsed(
                    await self._parse_thread(parse_work, _read_bounded, path), arrived_at, parse_work, subscription, options,
                )
            spec = await self._thread(getattr(self.embedder, "spec", lambda: None))
            checked = await self._parse_thread(parse_work, lambda: read_archive(path, expected_spec=spec))
            digest = checked["artifact_sha256"]
            if subscription is not None and digest != subscription.artifact_sha256:
                return {"ok": False, "reason": "artifact_hash_mismatch"}
            if subscription is not None:
                staged = self._staged_subscription_job(subscription)
                if staged is not None:
                    return {"ok": True, **staged.to_json(), "existing": True}
            def retain():
                output = self.root / "artifacts" / (digest + ".neko-knowledge.zip")
                existed = output.exists()
                output.parent.mkdir(parents=True, exist_ok=True)
                stage = output.with_suffix(".tmp")
                with path.open("rb") as source, stage.open("wb") as target:
                    shutil.copyfileobj(source, target, 64 * 1024)
                    target.flush()
                    import os
                    os.fsync(target.fileno())
                hasher = hashlib.sha256()
                with stage.open("rb") as source:
                    for block in iter(lambda: source.read(64 * 1024), b""):
                        hasher.update(block)
                if hasher.hexdigest() != digest:
                    stage.unlink(missing_ok=True)
                    raise KnowledgePackError("artifact_hash_mismatch")
                stage.replace(output)
                return not existed
            async def retain_and_admit():
                # Export deletion uses the same mutation lock. Keep the exact
                # ZIP alive until its import job owns the recovery reference;
                # otherwise deletion could slip between retaining and admission.
                created = await self._thread(retain)
                accepted = False
                try:
                    canonical = await self._thread(lambda: canonical_pack_bytes(parse_pack(checked["pack"])))
                    options.update(archive_sha256=digest, artifact_sha256=digest, seed_vectors=checked["seed_vectors"])
                    result = await self._import_parsed(canonical, arrived_at, parse_work, subscription, options)
                    accepted = bool(result.get("ok"))
                    return result
                finally:
                    if created and not accepted:
                        # This copy never became an accepted user artifact.
                        # Preserve pre-existing files and every durable task
                        # reference; cleanup cannot change the refusal result.
                        async with self._job_persist_lock:
                            records = [self._job_record(job) for job in self._jobs.values()]
                            def cleanup():
                                if not self._archive_referenced_blocking(digest, records):
                                    (self.root / "artifacts" / (digest + ".neko-knowledge.zip")).unlink(missing_ok=True)
                            with contextlib.suppress(OSError):
                                await self._thread(cleanup)

            # Transfer before the next await, even if the owner has not yet
            # started. The original admission reservation remains visible and
            # counted until its final filesystem work and cleanup have ended.
            owned_temporary = temporary
            completion: concurrent.futures.Future[None] = concurrent.futures.Future()
            completion.set_running_or_notify_cancel()
            parse_work.append(completion)
            self._admission_owned.add(arrived_at)

            async def own_retention():
                try:
                    return await self._locked(retain_and_admit, wait=True)
                finally:
                    try:
                        if owned_temporary is not None:
                            await clean_input(owned_temporary, [])
                    finally:
                        completion.set_result(None)

            owner = asyncio.create_task(own_retention(), name="knowledge-archive-import")
            self._detached.add(owner)
            owner.add_done_callback(self._detached.discard)
            owner.add_done_callback(lambda _done: self._admission_settled(arrived_at))
            owner.add_done_callback(_consume)
            temporary = None
            return await asyncio.shield(owner)
        finally:
            if temporary is not None:
                running = [future for future in parse_work if not future.done()]
                cleanup = asyncio.create_task(clean_input(temporary, running), name="knowledge-archive-input-cleanup")
                self._detached.add(cleanup)
                cleanup.add_done_callback(self._detached.discard)
                cleanup.add_done_callback(_consume)
                if not running:
                    await asyncio.shield(cleanup)

    async def export_pack(self, pack_id: str, *, format_version: int = 2,
                          _retry_version: str | None = None) -> dict[str, Any]:
        """Queue v2 with a server-owned release version, frozen across retries."""
        self._require_ready()
        # Retired clients may still send a format selector. Refuse v1 instead
        # of silently producing a different file from the one they requested.
        if (type(format_version) is not int or format_version != 2
                or (_retry_version is not None and not isinstance(_retry_version, str))):
            raise KnowledgePackError("invalid_request")
        record = self._registry.packs.get(pack_id)
        if record is None:
            raise KnowledgePackError("not_found")
        active = [job for job in self._jobs.values() if job.state in ACTIVE_JOB_STATES]
        if any(job.pack_id == pack_id for job in active):
            raise KnowledgePackError("job_in_progress")
        if len(active) >= MAX_PENDING_IMPORTS:
            raise KnowledgePackError("knowledge_busy")
        stamp = utc_now()
        job_id = uuid.uuid4().hex
        # Reuse the existing unique task identity instead of maintaining a
        # counter that could reset after task cleanup, restore or pack import.
        # Persist this value before queueing; only retry_job supplies an old one.
        version = _retry_version if _retry_version is not None else f"auto-{job_id}"
        job = ImportJob(job_id, pack_id, "queued", stamp, stamp,
                        kind="pack_export", staged_sha256=record.pack_sha256,
                        export_version=version)
        self._request_seq += 1
        job.arrived_at = self._request_seq
        self._remember_job(job)
        async def admit():
            # The request may stop waiting while its write thread is alive.
            # One owner must finish persistence and queueing (and shutdown
            # drains it), just as for ordinary import admission.
            try:
                await self._persist_job(job)
            except OSError:
                self._jobs.pop(job.job_id, None)
                raise
            if self._stopping:
                self._finish_job(job, "failed", "knowledge_stopping")
                await self._persist_job(job)
            else:
                self._job_queue.append(job.job_id)
                self._job_queued.set()
            return {"ok": True, **job.to_json()}

        owner = asyncio.create_task(admit())
        self._detached.add(owner)
        owner.add_done_callback(self._detached.discard)
        owner.add_done_callback(_consume)
        return await asyncio.shield(owner)

    async def _export_job(self, job: ImportJob) -> None:
        """Snapshot under the mutation lock, then serialize after releasing it."""
        from .archive import export_archive

        # Durable jobs are another input boundary: an old text-export task
        # cannot bypass admission by reaching the worker or retry path.
        if type(job.export_format) is not int or job.export_format != 2:
            raise KnowledgePackError("invalid_request")

        async def snapshot():
            record = self._registry.packs.get(job.pack_id)
            if record is None or record.pack_sha256 != job.staged_sha256:
                raise KnowledgePackError("pack_changed")
            def read():
                payload = json.loads(_read_bounded(self.root / PACKS_DIR / record.file_name))
                model_id = self._current_model_id()
                spec = self._store.vector_specification(model_id, pack_id=job.pack_id)
                vectors = self._store.vectors_for_pack(job.pack_id, spec["model_id"]) if spec else []
                return payload, spec, vectors
            return await self._thread(read)

        payload, spec, vectors = await self._locked(snapshot, wait=True)
        def check():
            if self._superseded(job) or self._stopping:
                raise InterruptedError("cancelled")
        def commit_gate():
            # The archive calls this immediately before publication. Keep
            # accepting cancellation through fsync, then make that decision
            # exclusive with cancel_job rather than waiting for thread return.
            with job.gate:
                if self._superseded(job) or self._stopping:
                    return False
                job.committed = True
                return True
        async def publish():
            # The runner may stop waiting after the publication gate. Keep
            # one owner for the native result and its durable download record.
            job.result = await self._thread(export_archive, self.root / "artifacts", payload,
                                            version=job.export_version, specification=spec,
                                            vectors=vectors, created_at=job.created_at,
                                            check_cancelled=check, commit_gate=commit_gate)
            job.published = True
            if self._stopping:
                self._finish_job(job, "active")
                await self._persist_job(job)

        owner = asyncio.create_task(publish(), name="knowledge-export-publication")
        self._detached.add(owner)
        owner.add_done_callback(self._detached.discard)
        owner.add_done_callback(_consume)
        await asyncio.shield(owner)

    def artifact_path(self, artifact_id: str) -> Path:
        """Resolve only retained SHA identities; never accept an arbitrary path."""
        import re

        if not isinstance(artifact_id, str) or not re.fullmatch(r"[0-9a-f]{64}", artifact_id):
            raise KnowledgePackError("invalid_request")
        folder = self.root / "artifacts"
        # The download surface serves complete packages only. Previously
        # exported JSON files stay on disk without reviving their old route.
        path = folder / (artifact_id + ".neko-knowledge.zip")
        if path.is_file() and not path.is_symlink() and path.resolve().parent == folder.resolve():
            return path
        raise KnowledgePackError("not_found")

    async def artifact_download(self, artifact_id: str) -> tuple[Path, str]:
        """Resolve artifact bytes and their readable download metadata off-loop."""
        from .archive import archive_download_filename

        # Snapshot retained job metadata before entering a thread. The archive
        # supplies its frozen name even if today's pack has changed or vanished.
        created_at = next((job.created_at for job in self._jobs.values()
                           if job.kind == "pack_export" and isinstance(job.result, dict)
                           and job.result.get("artifact_id") == artifact_id), None)

        def read():
            path = self.artifact_path(artifact_id)
            return path, archive_download_filename(path, created_at=created_at)

        return await self._thread(read)

    async def import_pack(
        self, raw: bytes | Path, *, subscription: KnowledgeSubscription | None = None,
        _options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Import a pack; ``subscription`` marks a verified plugin-market artifact."""
        self._require_ready()
        options = dict(_options or {})
        # Parsing a large pack holds several copies of it in memory: refuse
        # before parsing when imports are already at their limit, and never
        # parse more than that many at once.
        if self.import_busy():
            # A repeat of an apply whose import is queued or building is still
            # answered with that import: the slots may be full of exactly it.
            staged = await self._staged_repeat(raw, subscription) if subscription is not None else None
            return staged or {"ok": False, "reason": "knowledge_busy"}
        self._request_seq += 1
        arrived_at = self._request_seq
        # The reservation lasts until the request is answered (including the
        # unchanged-file check, which reads from disk) or admitted, so parsed
        # copies of a pack never pile up uncounted.
        self._parsing += 1
        parse_work: list[concurrent.futures.Future[Any]] = []
        deferred = False
        if subscription is not None:
            self._incoming[arrived_at] = subscription
        try:
            # ZIP verification belongs to the original arrival and admission
            # slot. Recursing after verification would let an earlier upload
            # overtake an unsubscribe and lose bounded parse accounting.
            if isinstance(raw, Path) or raw.startswith(b"PK\x03\x04"):
                return await self._import_archive_input(
                    raw, subscription=subscription, options=options,
                    arrived_at=arrived_at, parse_work=parse_work,
                )
            return await self._import_parsed(raw, arrived_at, parse_work, subscription, options or None)
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
            if arrived_at not in self._admission_owned:
                self._incoming.pop(arrived_at, None)

    def subscription_state(self) -> dict[str, Any]:
        """What the market page reconciles against.

        ``installed``: subscribed packs (``broken`` ones are not served).
        ``imports``: subscription imports, newest first: still being admitted
        (``admitting``, not a job yet), queued or building, and the finished
        ones still remembered (``active`` / ``failed`` / ``cancelled``, with the
        reason). A failure after the plugin server handed the artifact over
        is only visible here.
        """
        self._require_ready()
        installed = [
            {
                "pack_id": record.pack_id,
                "subscription": record.subscription.to_json(),
                "broken": record.pack_id in self._broken_packs,
                "entries": record.entries,
                "installed_at": record.installed_at,
                "updated_at": record.updated_at,
            }
            for record in sorted(self._registry.packs.values(), key=lambda item: item.pack_id)
            if record.subscription is not None
        ]
        admitting = [
            {"job_id": "", "pack_id": subscription.pack_id, "state": "admitting", "reason": "",
             "subscription": subscription.to_json()}
            for _arrived, subscription in sorted(self._incoming.items(), reverse=True)
        ]
        jobs = [job.to_json() for job in reversed(self._jobs.values()) if job.subscription is not None]
        return {"installed": installed, "imports": admitting + jobs}

    def _staged_subscription_job(self, subscription: KnowledgeSubscription) -> ImportJob | None:
        """The queued or building import of exactly this subscription, unless a removal overrides it."""
        return next(
            (
                job
                for job in self._jobs.values()
                if job.state in ACTIVE_JOB_STATES
                and job.subscription == subscription
                and not self._superseded(job)
            ),
            None,
        )

    async def _staged_repeat(self, raw: bytes | Path, subscription: KnowledgeSubscription) -> dict[str, Any] | None:
        """The answer to a repeated apply of a staged import, or None if this is no such repeat."""
        if self._staged_subscription_job(subscription) is None:
            return None
        if isinstance(raw, Path):
            from utils.knowledge_package import DEFAULT_LIMITS, hash_file

            # Streamed ZIP requests retain the same repeat identity without
            # reading a full archive back into the event loop's memory.
            digest, _size = await self._thread(hash_file, raw, DEFAULT_LIMITS.max_archive_bytes)
        else:
            digest = await self._thread(pack_sha256, raw)
        if digest != subscription.artifact_sha256:
            return None
        # Checked again after the await: the import may have finished since.
        staged = self._staged_subscription_job(subscription)
        return {"ok": True, **staged.to_json(), "existing": True} if staged is not None else None

    def _admission_settled(self, arrived_at: int) -> None:
        self._admission_owned.discard(arrived_at)
        self._incoming.pop(arrived_at, None)

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
        self,
        raw: bytes,
        arrived_at: int,
        parse_work: list[concurrent.futures.Future[Any]],
        subscription: KnowledgeSubscription | None = None,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if subscription is not None:
            # The market published these exact bytes; anything else is not
            # the artifact the subscription names.
            artifact_hash = (options or {}).get("artifact_sha256") or await self._parse_thread(parse_work, pack_sha256, raw)
            if artifact_hash != subscription.artifact_sha256:
                return {"ok": False, "reason": "artifact_hash_mismatch"}
            staged = self._staged_subscription_job(subscription)
            if staged is not None:
                # A repeat of an apply already staged (its answer was lost on
                # the way): the same artifact under the same metadata, so the
                # answer is that job, not "another import is in progress".
                return {"ok": True, **staged.to_json(), "existing": True}
        try:
            pack, canonical, chunks = await self._parse_thread(parse_work, self._prepare_import, raw)
        except KnowledgePackError as exc:
            return {"ok": False, "reason": exc.reason}
        if subscription is not None:
            if pack.pack_id != subscription.pack_id:
                return {"ok": False, "reason": "package_identity_mismatch"}
            if pack.material_type != subscription.material_type:
                return {"ok": False, "reason": "material_type_mismatch"}
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
            # Same content under new market metadata (a re-published version)
            # still goes through a job, so the record gets the new metadata.
            and existing.subscription == subscription
            and existing.archive_sha256 == (options or {}).get("archive_sha256", "")
            and pack.pack_id not in self._broken_packs
            # The raw file is the source of truth; if it went missing or was
            # altered since startup, import again so it gets rewritten.
            and await self._parse_thread(parse_work, self._raw_file_intact, existing)
            # Checked after that await: the record must still be the installed
            # one and no removal may be waiting, or "unchanged, active" could
            # be answered for a pack that is (about to be) gone. Such an
            # import gets a job and the request order settles it.
            and self._registry.packs.get(pack.pack_id) == existing
            and not self._pending_removals.get(pack.pack_id)
        ):
            return {"ok": True, "pack_id": pack.pack_id, "unchanged": True, "state": "active"}
        # After the awaits above and with none before the reservation below;
        # the commit checks once more under the lock.
        if self._identity_conflict(pack.pack_id, subscription):
            return {"ok": False, "reason": "subscription_identity_mismatch"}
        # Admission is checked and reserved without an await in between, so
        # two requests for the same pack cannot both get through, and staged
        # files (written before the single runner gets to them) stay bounded.
        pending = [job for job in self._jobs.values() if job.state in ACTIVE_JOB_STATES]
        if pack.pack_id in self._admitting or any(job.pack_id == pack.pack_id for job in pending):
            return {"ok": False, "reason": "job_in_progress"}
        if len(pending) + len(self._admitting) >= MAX_PENDING_IMPORTS:
            return {"ok": False, "reason": "knowledge_busy"}
        self._admitting[pack.pack_id] = len(canonical)

        async def admit() -> dict[str, Any]:
            try:
                # Signed JSON subscriptions retry the exact published bytes;
                # canonical bytes remain the index's independent identity.
                retry_options = dict(options or {})
                if subscription is not None and not retry_options.get("archive_sha256"):
                    retry_options["retry_bytes"] = raw
                return await self._admit_import(pack, canonical, chunks, arrived_at, subscription, retry_options)
            finally:
                self._admitting.pop(pack.pack_id, None)

        # Shielded: if the request goes away mid-way, admission still runs to
        # the end, so a staging file is never written without a job (and
        # reservation) accounting for it. Tracked, so stop() waits for it.
        admission = asyncio.ensure_future(admit())
        self._detached.add(admission)
        admission.add_done_callback(self._detached.discard)
        # The admission outlives a cancelled request; until it ends (with a
        # job remembered, or a refusal) the import stays visible as incoming.
        self._admission_owned.add(arrived_at)
        admission.add_done_callback(lambda _done: self._admission_settled(arrived_at))
        return await asyncio.shield(admission)

    async def _admit_import(
        self,
        pack: KnowledgePack,
        canonical: bytes,
        chunks: int,
        arrived_at: int,
        subscription: KnowledgeSubscription | None = None,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self._pending_chunk_reconcile:
            return {"ok": False, "reason": "knowledge_busy"}
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
        # One admission at a time from the disk scan to its job being known:
        # another admission's just-written staging file would otherwise be
        # counted on disk and again as that admission's reservation.
        async with self._admission_lock:
            return await self._stage_import(pack, canonical, chunks, arrived_at, subscription, options)

    async def _stage_import(
        self,
        pack: KnowledgePack,
        canonical: bytes,
        chunks: int,
        arrived_at: int,
        subscription: KnowledgeSubscription | None = None,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        # Staged files are counted from their jobs, or from the reservation of
        # an admission that has not written one yet; taken together with the
        # set of active jobs the disk scan leaves out.
        active = [job for job in self._jobs.values() if job.state in ACTIVE_JOB_STATES]
        staged_bytes = sum(job.staged_bytes for job in active) + sum(
            size for pack_id, size in self._admitting.items() if pack_id != pack.pack_id
        )
        installed_bytes = await self._thread(
            self._installed_pack_bytes, pack.pack_id, _job_files(active, installing=self._registry)
        )
        if installed_bytes + staged_bytes + len(canonical) > MAX_TOTAL_PACK_BYTES:
            return {"ok": False, "reason": "capacity_bytes"}
        now = utc_now()
        options = options or {}
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
            subscription=subscription,
            kind=options.get("kind", "pack_import"),
            origin=options.get("origin", ""),
            initial_enabled=options.get("enabled"),
            archive_sha256=options.get("archive_sha256", ""),
            seed_vectors=options.get("seed_vectors", []),
            frozen_input=options.get("frozen_input", {}),
        )
        try:
            await self._thread(atomic_write_bytes, self._staging_path(job.job_id), canonical)
            # Retain exact retry bytes apart from disposable canonical staging.
            await self._thread(atomic_write_bytes, self.root / "jobs" / (job.job_id + ".pack.json"),
                               options.get("retry_bytes", canonical))
        except BaseException:
            # Before a job owns this input, a failed second write must not
            # leave disposable staging charged against future admission.
            await self._discard_staging(job.job_id)
            raise
        if self._stopping:
            # The runner is gone; a queued job would never run.
            await self._discard_staging(job.job_id)
            return {"ok": False, "reason": "knowledge_stopping"}
        job.arrived_at = arrived_at
        self._remember_job(job)
        try:
            await self._persist_job(job)
        except OSError:
            # An atomic record write can fail after replacement. Preserve its
            # retry input whenever that record exists; otherwise this new,
            # unreferenced copy never became retained task history.
            self._jobs.pop(job.job_id, None)
            await self._discard_staging(job.job_id)
            async with self._job_persist_lock:
                def discard_unowned_input():
                    if not (self.root / "jobs" / (job.job_id + ".json")).exists():
                        (self.root / "jobs" / (job.job_id + ".pack.json")).unlink(missing_ok=True)
                with contextlib.suppress(OSError):
                    await self._thread(discard_unowned_input)
            raise
        # A job now: listed as one, no longer as an incoming import.
        self._incoming.pop(arrived_at, None)
        if self._removal_committed_over(pack.pack_id, subscription, arrived_at):
            # The pack was removed while this import was being admitted.
            job.cancel_requested = True
            self._finish_job(job, "cancelled")
            await self._discard_staging(job.job_id)
            with contextlib.suppress(OSError):
                await self._persist_job(job)
            return {"ok": True, **job.to_json()}
        if self._stopping:
            # Shutdown can retire the runner during the initial task flush.
            # Publish a retryable terminal record instead of queueing work
            # behind a worker that has exited; keep its frozen JSON/ZIP input.
            self._finish_job(job, "failed", "knowledge_stopping")
            await self._discard_staging(job.job_id)
            with contextlib.suppress(OSError):
                await self._persist_job(job)
            return {"ok": True, **job.to_json()}
        self._job_queue.append(job.job_id)
        self._job_queued.set()
        return {"ok": True, **job.to_json()}

    async def _parse_thread(
        self,
        parse_work: list[concurrent.futures.Future[Any]],
        fn: Callable[..., _T],
        /,
        *args: Any,
    ) -> _T:
        """Run an import request's thread work; its slot stays held until it ends."""
        if self._parse_pool is None:
            self._parse_pool = concurrent.futures.ThreadPoolExecutor(
                max_workers=MAX_PENDING_IMPORTS, thread_name_prefix="knowledge-parse"
            )
        future = self._parse_pool.submit(fn, *args)
        parse_work.append(future)
        self._track_thread_work(future)
        return await asyncio.wrap_future(future)

    async def _thread(self, fn: Callable[..., _T], /, *args: Any, **kwargs: Any) -> _T:
        """``asyncio.to_thread``, tracked so that ``stop()`` waits for the thread.

        Cancelling the caller only stops the waiting: the thread runs on, and
        may hold knowledge.db open, until ``fn`` returns.
        """
        future: concurrent.futures.Future[_T] = concurrent.futures.Future()
        context = contextvars.copy_context()

        def run() -> None:
            if not future.set_running_or_notify_cancel():
                return  # cancelled before it started
            try:
                result = context.run(fn, *args, **kwargs)
            except BaseException as exc:
                future.set_exception(exc)
            else:
                future.set_result(result)

        self._track_thread_work(future)
        try:
            asyncio.get_running_loop().run_in_executor(None, run)
        except BaseException:
            future.cancel()
            raise
        return await asyncio.wrap_future(future)

    def _track_thread_work(self, future: concurrent.futures.Future[Any]) -> None:
        with self._thread_work_lock:
            self._thread_work.add(future)
        future.add_done_callback(self._untrack_thread_work)

    def _untrack_thread_work(self, future: concurrent.futures.Future[Any]) -> None:
        with self._thread_work_lock:
            self._thread_work.discard(future)

    def _prepare_import(self, raw: bytes) -> tuple[KnowledgePack, bytes, int]:
        pack = decode_pack_bytes(raw)
        return pack, canonical_pack_bytes(pack), count_pack_chunks(pack, fit=self._chunk_fit())

    def _chunk_fit(self) -> Callable[[str], bool] | None:
        """Called only in workers; token counting never loads an ONNX session."""
        count = getattr(self.embedder, "count_tokens", None)
        spec = getattr(self.embedder, "spec", lambda: None)()
        if not callable(count) or not spec:
            return None
        if count([""]) is None:
            return None  # Missing tokenizer resources must not block text import.
        return lambda text: bool((sizes := count([text])) and sizes[0] <= spec["max_length"])

    def _raw_file_intact(self, record: PackRecord) -> bool:
        try:
            raw = _read_bounded(self.root / PACKS_DIR / record.file_name)
            return pack_sha256(raw) == record.pack_sha256
        except OSError:
            return False

    def _active_job_files(self) -> frozenset[str]:
        return _job_files(
            [job for job in self._jobs.values() if job.state in ACTIVE_JOB_STATES], installing=None
        )

    def _installed_pack_bytes(self, replacing: str, job_files: frozenset[str] = frozenset()) -> int:
        """Bytes of pack files on disk, except the one ``replacing`` swaps out.

        Retained historical revisions have a separate, explicit no-deletion
        policy; the active-pack budget counts current revisions and staging.
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
                if path.name in job_files:
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
            # A finished export is the only handle to its artifact (download,
            # publish, delete); the bounded window must not orphan it.
            oldest = next(
                (job_id for job_id, item in self._jobs.items() if item.state in TERMINAL_JOB_STATES
                 and not (item.kind == "pack_export" and item.state == "active" and item.result)),
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
        await self._persist_job(job)
        return True

    def _unqueue(self, job_id: str) -> None:
        # The runner may already have taken it; then there is nothing to drop.
        with contextlib.suppress(ValueError):
            self._job_queue.remove(job_id)

    def discard_job(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if job is None or job.state not in TERMINAL_JOB_STATES:
            return False
        job.export_deleted = True
        del self._jobs[job_id]
        async def discard():
            async with self._job_persist_lock:
                await self._thread((self.root / "jobs" / (job_id + ".json")).unlink, missing_ok=True)
        task = asyncio.create_task(discard())
        self._detached.add(task)
        task.add_done_callback(self._detached.discard)
        task.add_done_callback(_consume)
        return True

    async def delete_export(self, job_id: str) -> dict[str, Any]:
        """Remove one completed local export, retaining any shared recovery ZIP."""
        import re

        if not isinstance(job_id, str) or not re.fullmatch(r"[0-9a-f]{32}", job_id):
            raise KnowledgePackError("invalid_request")

        async def run():
            job = self._jobs.get(job_id)
            if (job is None or job.kind != "pack_export" or job.state != "active"
                    or not isinstance(job.result, dict)):
                raise KnowledgePackError("invalid_request")
            artifact_id = job.result.get("artifact_id")
            if not isinstance(artifact_id, str) or not re.fullmatch(r"[0-9a-f]{64}", artifact_id):
                raise KnowledgePackError("invalid_request")

            async with self._job_persist_lock:
                records = [self._job_record(other) for other in self._jobs.values() if other.job_id != job_id]

                def remove():
                    protected = self._archive_referenced_blocking(artifact_id, records, exclude_job_id=job_id)
                    path = self.root / "artifacts" / (artifact_id + ".neko-knowledge.zip")
                    if not protected:
                        if path.is_symlink():
                            raise KnowledgePackError("invalid_request")
                        # Missing files are already cleaned. If the later task
                        # unlink fails, preserve the card and allow a retry to
                        # finish rather than report success before persistence.
                        path.unlink(missing_ok=True)
                    (self.root / "jobs" / (job_id + ".json")).unlink(missing_ok=True)
                    return protected

                retained = await self._thread(remove)
                job.export_deleted = True
                self._jobs.pop(job_id, None)
                return {"ok": True, "job_id": job_id, "artifact_retained": retained}

        return await self._locked(run)

    def _archive_referenced_blocking(self, artifact_id: str, records: list[dict], *, exclude_job_id: str = "") -> bool:
        """Honor current installs and retained task history under mutation/persist locks."""
        def references(value):
            return (value.get("archive_sha256") == artifact_id
                    or isinstance(value.get("result"), dict) and value["result"].get("artifact_id") == artifact_id)

        if (any(record.archive_sha256 == artifact_id for record in self._registry.packs.values())
                or any(references(value) for value in records)):
            return True
        # The in-memory task window may evict durable recovery references.
        for path in (self.root / "jobs").glob("*.json"):
            if path.name.endswith(".pack.json") or path.stem == exclude_job_id:
                continue
            with path.open("rb") as stream:
                raw = stream.read(128 * 1024 + 1)
            if len(raw) > 128 * 1024:
                continue
            try:
                value = json.loads(raw)
            except (ValueError, UnicodeError):
                continue
            if isinstance(value, dict) and references(value):
                return True
        return False

    async def retry_job(self, job_id: str) -> dict[str, Any]:
        """Retry the original frozen source selection, never today's document set."""
        job = self._jobs.get(job_id)
        if job is None or job.state not in {"failed", "cancelled"}:
            raise KnowledgePackError("job_not_retryable")
        if job.kind == "pack_export":
            record = self._registry.packs.get(job.pack_id)
            if record is None or record.pack_sha256 != job.staged_sha256:
                raise KnowledgePackError("pack_changed")
            return await self.export_pack(job.pack_id, format_version=job.export_format, _retry_version=job.export_version)
        options = {"kind": job.kind, "origin": job.origin, "enabled": job.initial_enabled, "frozen_input": job.frozen_input}
        if job.archive_sha256:
            path = self.artifact_path(job.archive_sha256)
            from utils.knowledge_package import DEFAULT_LIMITS, hash_file

            digest, _size = await self._thread(hash_file, path, DEFAULT_LIMITS.max_archive_bytes)
            if digest != job.archive_sha256:
                raise KnowledgePackError("artifact_hash_mismatch")
            return await self.import_pack(path, subscription=job.subscription, _options=options)
        raw = await self._thread(_read_bounded, self.root / "jobs" / (job.job_id + ".pack.json"))
        expected = job.subscription.artifact_sha256 if job.subscription is not None else job.staged_sha256
        if pack_sha256(raw) != expected:
            raise KnowledgePackError("pack_file_mismatch")
        return await self.import_pack(raw, subscription=job.subscription, _options=options)

    def _finish_job(self, job: ImportJob, state: str, reason: str = "") -> None:
        job.state = state
        job.reason = reason
        job.updated_at = utc_now()
        if state in TERMINAL_JOB_STATES:
            # Retry reads the retained archive, never this transient RAM copy.
            job.seed_vectors = []

    async def _discard_staging(self, job_id: str) -> None:
        """Best effort: a staged file that cannot go now is removed at the next start."""
        try:
            await self._thread(self._staging_path(job_id).unlink, missing_ok=True)
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
                if job.kind == "pack_export":
                    await self._export_job(job)
                    self._finish_job(job, "active")
                    continue
                for attempt in range(MAX_IMPORT_YIELDS + 1):
                    try:
                        await self._locked(lambda job=job: self._commit_job(job), wait=True)
                        break
                    except InterruptedError:
                        if (
                            self._stopping
                            or job.cancel_requested
                            or self._removal_committed_over(job.pack_id, job.subscription, job.arrived_at)
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
                self._finish_job(job, "active" if job.published else "cancelled")
            except KnowledgeUnavailable as exc:
                self._finish_job(job, "active" if job.published else "failed", "" if job.published else exc.reason)
            except KnowledgePackError as exc:
                self._finish_job(job, "active" if job.published else "failed", "" if job.published else exc.reason)
            except asyncio.CancelledError:
                # Once the commit owner has crossed its gate, cancellation
                # cannot tell whether its registry write succeeded. That
                # owner publishes the result; a restart reconciles an unknown
                # building record instead of preserving a false failure.
                if job.published or not job.committed:
                    self._finish_job(job, "active" if job.published else "failed", "" if job.published else "knowledge_stopping")
                raise
            except Exception as exc:
                # Post-publication cache or cleanup errors cannot undo the
                # successful registry commit or expose document text in logs.
                logger.warning("[Knowledge] import bookkeeping failed (%s)", type(exc).__name__)
                self._finish_job(job, "active" if job.published else "failed", "" if job.published else "operation_failed")
            finally:
                await self._discard_staging(job_id)
                try:
                    await self._persist_job(job)
                except OSError:
                    # Registry commit remains authoritative even when task
                    # bookkeeping cannot be flushed after a successful import.
                    pass

    async def _await_pending_removals(self, job: ImportJob) -> None:
        while self._removal_pending_over(job):
            await self._removal_settled.wait()

    async def _commit_job(self, job: ImportJob) -> None:
        if self._superseded(job):
            raise InterruptedError("cancelled")
        previous = self._registry.packs.get(job.pack_id)
        active_jobs = self._active_job_files()

        def commit_gate() -> bool:
            with job.gate:
                if self._superseded(job) or self._stopping:
                    return False
                job.committed = True
                return True

        def blocking() -> tuple[Registry, PackRecord]:
            self._reconcile_chunk_counts_blocking()
            raw = _read_bounded(self._staging_path(job.job_id))
            sha = pack_sha256(raw)
            if sha != job.staged_sha256:
                # The staged file changed after admission; its capacity checks
                # and chunk count were for other bytes.
                raise KnowledgeUnavailable("knowledge_error")
            pack = decode_pack_bytes(raw)
            if (job.frozen_input.get("source_confirmation_required")
                    and (previous.enabled if previous else bool(job.initial_enabled))
                    and not job.frozen_input.get("enabled_confirmed")):
                raise KnowledgePackError("source_confirmation_required")
            # Assets may become available between admission and commit. Use
            # this single fit for capacity, registry metadata and SQLite.
            fit = self._chunk_fit()
            chunks = count_pack_chunks(pack, fit=fit)
            if chunks > MAX_CHUNKS_PER_PACK:
                raise KnowledgePackError("too_many_chunks")
            # Admission checked capacity without the lock; two imports racing
            # through it must not both land, so check again under the lock.
            others = [r for r in self._registry.packs.values() if r.pack_id != pack.pack_id]
            if len(others) + 1 > MAX_PACKS:
                raise KnowledgePackError("capacity_packs")
            if sum(r.entries for r in others) + len(pack.entries) > MAX_TOTAL_ENTRIES:
                raise KnowledgePackError("capacity_entries")
            if sum(r.chunks for r in others) + chunks > MAX_TOTAL_CHUNKS:
                raise KnowledgePackError("capacity_chunks")
            if self._installed_pack_bytes(pack.pack_id, active_jobs) + len(raw) > MAX_TOTAL_PACK_BYTES:
                raise KnowledgePackError("capacity_bytes")
            # Admission checked identity without the lock too; another import
            # may have installed this pack or this market package since.
            installed = {key: r.subscription for key, r in self._registry.packs.items()}
            if identity_conflict(pack.pack_id, job.subscription, installed):
                raise KnowledgePackError("subscription_identity_mismatch")
            now = utc_now()
            keys = {entry.key for entry in pack.entries}
            record = PackRecord(
                pack_id=pack.pack_id,
                pack_sha256=sha,
                source=pack.source,
                declared_material_type=pack.material_type,
                entries=len(pack.entries),
                chunks=chunks,
                material_type_override=previous.material_type_override if previous else None,
                auto_context=previous.auto_context if previous else False,
                local_embedding=previous.local_embedding if previous else True,
                disabled_titles=tuple(
                    sorted(set(previous.disabled_titles) & keys) if previous else ()
                ),
                installed_at=previous.installed_at if previous else now,
                updated_at=now,
                subscription=job.subscription,
                # Ordinary local file replacement introduces new disclosure
                # scope. Confirmed source-version updates retain their switch.
                enabled=(False if job.kind == "pack_import" and job.initial_enabled is False
                         else previous.enabled if previous else (job.initial_enabled if job.initial_enabled is not None else True)),
                origin=previous.origin if previous else (job.origin or ("market" if job.subscription else "builtin")),
                archive_sha256=job.archive_sha256,
            )
            final_path = self.root / PACKS_DIR / record.file_name
            if previous is not None and previous.file_name != record.file_name:
                # Keep the previous immutable pack in an explicit history
                # directory before committing its replacement. History is not
                # charged as current searchable pack content.
                history = self.root / "history" / previous.pack_id / previous.file_name
                history.parent.mkdir(parents=True, exist_ok=True)
                try:
                    previous_raw = _read_bounded(self.root / PACKS_DIR / previous.file_name)
                except FileNotFoundError:
                    previous_raw = None  # A broken pack can be repaired by a new revision.
                if previous_raw is not None:
                    atomic_write_bytes(history, previous_raw)
            atomic_write_bytes(final_path, raw)
            indexed = False
            try:
                chunk_options = {}
                if fit is not None:
                    chunk_options["fit"] = fit
                if job.seed_vectors:
                    chunk_options["seed_vectors"] = job.seed_vectors
                self._store.replace_pack(
                    pack,
                    pack_sha256=sha,
                    disabled_keys=record.disabled_titles,
                    should_cancel=lambda: self._superseded(job) or self._stopping,
                    commit_gate=commit_gate,
                    **chunk_options,
                )
                indexed = True
                registry = self._registry.with_pack(record)
                save_registry(self.root, registry)
                job.chunks_total = chunks
            except BaseException:
                if indexed:
                    # The registry still describes the old version: put the
                    # index back so a failed import does not change answers.
                    self._restore_index_blocking(pack.pack_id, previous)
                if previous is None or previous.file_name != record.file_name:
                    final_path.unlink(missing_ok=True)
                raise
            # Keep every immutable raw revision. A failed cleanup must never
            # turn an already committed import into a failed task.
            if previous is not None and previous.file_name != record.file_name:
                with contextlib.suppress(OSError):
                    (self.root / PACKS_DIR / previous.file_name).unlink(missing_ok=True)
            return registry, record

        registry, _record = await self._thread(blocking)
        self._broken_packs = tuple(p for p in self._broken_packs if p != job.pack_id)
        self._publish_registry(registry)
        job.published = True
        if self._stopping:
            # The runner may already have stopped waiting for this owner.
            self._finish_job(job, "active")
            await self._persist_job(job)

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
                **self._rebuild_options(previous),
            )
        except Exception as exc:
            # Startup reconcile repairs the index from the registry anyway.
            logger.warning("[Knowledge] could not restore index after a failed import (%s)", type(exc).__name__)

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

    def _install_vectors(self, snapshot: VectorSnapshot | None, key: tuple[int, str]) -> None:
        """Use ``snapshot`` unless one loaded for a later generation is in place."""
        built = self._vectors_built_for
        if built is not None and built[1] == key[1] and built[0] > key[0]:
            return
        self._vectors = snapshot
        # Transiently unverifiable resources are not a completed empty index.
        # Keep the same generation eligible for the next scheduled refresh.
        self._vectors_built_for = key if snapshot is not None else None

    async def _refresh_vectors_now(self) -> None:
        model_id = self._current_model_id()
        if model_id is None or self._state != "ready":
            return
        key = (self._vector_generation, model_id)
        try:
            snapshot = await self._thread(self._load_vector_snapshot, model_id)
        except Exception as exc:
            # The scheduled refresh retries; lookups meanwhile skip ids that
            # no longer exist.
            logger.warning("[Knowledge] vector snapshot reload failed (%s)", type(exc).__name__)
            return
        self._install_vectors(snapshot, key)
        await self._clear_damaged_vectors(snapshot)

    def _load_vector_snapshot(self, model_id: str) -> VectorSnapshot | None:
        """Run off-loop: a profile name cannot certify a different resource set."""
        provider = getattr(self.embedder, "spec", None)
        if callable(provider):
            specification = provider()
            if specification is None:
                return None
            return self._store.load_vectors(model_id, specification=specification)
        return self._store.load_vectors(model_id)

    async def _compatible_snapshot(self, snapshot: VectorSnapshot | None) -> VectorSnapshot | None:
        """Fail closed if on-disk resources changed after the matrix was loaded."""
        provider = getattr(self.embedder, "spec", None)
        if snapshot is not None and callable(provider):
            current = await self._thread(provider)
            if current is None or current != snapshot.specification:
                return None
        return snapshot

    async def _clear_damaged_vectors(self, snapshot: VectorSnapshot | None) -> None:
        """Hand unusable stored vectors back to the indexer, under the write lock.

        Best effort: the snapshot is already in use without them, so a failed
        clear only means they are skipped again on the next load.
        """
        if snapshot is None or not snapshot.damaged:
            return
        damaged = snapshot.damaged
        try:
            await self._locked(
                lambda: self._thread(self._store.clear_vectors, damaged), wait=True
            )
        except Exception as exc:
            logger.warning("[Knowledge] could not clear damaged vectors (%s)", type(exc).__name__)
            return
        self._index_wakeup.set()

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
                snapshot = await self._thread(self._load_vector_snapshot, model_id)
            except Exception as exc:
                logger.warning("[Knowledge] vector snapshot rebuild failed (%s)", type(exc).__name__)
                return False
            self._install_vectors(snapshot, key)
            await self._clear_damaged_vectors(snapshot)
            return snapshot is not None  # Do not immediately reschedule an unavailable snapshot.

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
            # Consume only the previous round's wake-up; a busy batch can
            # schedule the next round while this one is still running.
            self._index_wakeup.clear()
            try:
                processed = await self._index_round(until=last_refresh + VECTOR_REFRESH_SECONDS)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("[Knowledge] indexer round failed (%s)", type(exc).__name__)
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
            try:
                await asyncio.wait_for(self._index_wakeup.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass  # the idle delay elapsed without a wake-up; run another round

    def _vector_pack_ids(self) -> list[str] | None:
        """Packs whose chunks may be embedded now; ``None`` when indexing is off."""
        if self._stopping or self._state != "ready" or not self._registry.enabled:
            return None
        return [record.pack_id for record in self._registry.packs.values()
                if record.local_embedding and record.pack_id not in self._broken_packs]

    async def _replan_vector_inputs(self, pack_ids: list[str]) -> None:
        """Rebuild chunks after a tokenizer becomes available, retaining text.

        Imports made with missing model assets stay keyword-searchable. Before
        any later encoding, full input counts may require smaller chunks. The
        same total/per-pack limits remain authoritative for that rebuild.
        """
        async def run():
            def rebuild():
                # A previous pack may have committed SQLite before its
                # registry flush failed. Capacity must use those real counts.
                self._reconcile_chunk_counts_blocking()
                fit = self._chunk_fit()
                if fit is None:
                    return self._registry
                planned = []
                for pack_id in pack_ids:
                    record = self._registry.packs.get(pack_id)
                    if record is None:
                        continue
                    raw = _read_bounded(self.root / PACKS_DIR / record.file_name)
                    if pack_sha256(raw) != record.pack_sha256:
                        raise KnowledgePackError("pack_file_mismatch")
                    pack = decode_pack_bytes(raw)
                    chunks = count_pack_chunks(pack, fit=fit)
                    if chunks > MAX_CHUNKS_PER_PACK:
                        raise KnowledgePackError("too_many_chunks")
                    planned.append((pack, record, chunks))
                changed = {record.pack_id: chunks for _pack, record, chunks in planned}
                if sum(changed.get(key, record.chunks) for key, record in self._registry.packs.items()) > MAX_TOTAL_CHUNKS:
                    raise KnowledgePackError("capacity_chunks")
                registry = self._registry
                for pack, record, chunks in planned:
                    self._store.replace_pack(pack, pack_sha256=record.pack_sha256,
                                             disabled_keys=record.disabled_titles, fit=fit)
                    self._pending_chunk_reconcile = True
                    registry = registry.with_pack(replace(record, chunks=chunks))
                save_registry(self.root, registry)
                self._pending_chunk_reconcile = False
                return registry

            try:
                self._publish_registry(await self._thread(rebuild))
            finally:
                # Even a failed registry flush may follow committed row-ID
                # changes. Old snapshots and previous seeding attempts cannot
                # describe those new rows; reconcile before further encoding.
                self._vectors = None
                self._vectors_built_for = None
                self._vector_generation += 1
                self._seeded_artifacts.clear()
        await self._locked(run, wait=True)

    def _reconcile_chunk_counts_blocking(self) -> None:
        """Repair a failed replan flush under the existing mutation lock."""
        if not self._pending_chunk_reconcile:
            return
        counts = self._store.chunk_stats(None)
        registry = self._registry
        for pack_id, record in registry.packs.items():
            registry = registry.with_pack(replace(record, chunks=counts.get(pack_id, {}).get("total", 0)))
        save_registry(self.root, registry)
        self._publish_registry(registry)
        self._pending_chunk_reconcile = False

    async def _seed_retained_packages(self, pack_ids: list[str], specification: dict) -> None:
        """Recover product-package vectors when assets arrive after text install."""
        from .archive import read_archive

        identity = self._store._spec_hash(specification)
        for pack_id in pack_ids:
            record = self._registry.packs.get(pack_id)
            if record is None or not record.archive_sha256:
                continue
            key = (record.archive_sha256, identity)
            if key in self._seeded_artifacts:
                continue
            try:
                checked = await self._thread(read_archive, self.artifact_path(record.archive_sha256), expected_spec=specification)
                if checked["artifact_sha256"] != record.archive_sha256:
                    continue
                async def write():
                    if self._registry.packs.get(pack_id) != record:
                        return None  # Retry the same archive against the new policy record.
                    return await self._thread(self._store.seed_pack_vectors, pack_id, checked["seed_vectors"])
                stored = await self._locked(write, wait=True)
                if stored is None:
                    continue
                if stored:
                    self._vectors_dirty = True
                self._seeded_artifacts.add(key)
            except (OSError, KnowledgePackError):
                pass  # Valid canonical text can still use ordinary backfill.

    async def _index_round(self, until: float | None = None) -> int:
        """Embed pending chunks; return how many were tried.

        The round ends early once ``until`` (when the vector snapshot is due
        for a reload) has passed, so a long backfill with slow inference still
        makes its new vectors searchable every VECTOR_REFRESH_SECONDS.
        """
        if self._pending_chunk_reconcile:
            await self._locked(lambda: self._thread(self._reconcile_chunk_counts_blocking), wait=True)
        if self._state != "ready" or not self._registry.enabled or self.embedder is None:
            return 0
        model_id = self._current_model_id()
        if model_id is None:
            return 0
        if self._index_model_id != model_id:
            # Failure counts belong to the model (and process) that produced
            # them: a new model, or a restart, retries every chunk once more.
            await self._locked(lambda: self._thread(self._store.reset_attempts), wait=True)
            self._index_model_id = model_id
        processed = 0
        deferred_packs: set[str] = set()
        while processed < INDEX_ROUND_CHUNKS and not self._stopping:
            # Re-read the switches every batch: the user may turn knowledge or
            # a pack's vectors off while a batch is embedding.
            pack_ids = self._vector_pack_ids()
            if pack_ids is None:
                break
            pack_ids = [pack_id for pack_id in pack_ids if pack_id not in deferred_packs]
            specification = await self._thread(getattr(self.embedder, "spec", lambda: None))
            if callable(getattr(self.embedder, "spec", None)) and specification is None:
                # A modern provider that cannot prove its loaded resources
                # must wait, rather than emit uncertified/truncated vectors.
                break
            if specification is not None:
                await self._seed_retained_packages(pack_ids, specification)
            rows = await self._thread(
                self._store.pending_chunks, model_id=model_id, pack_ids=pack_ids, limit=INDEX_BATCH_SIZE,
                require_spec=specification is not None,
                specification=specification,
            )
            if not rows:
                break
            counter = getattr(self.embedder, "count_tokens", None)
            if specification is not None and callable(counter):
                counts = await self._thread(counter, [text for _id, _hash, text in rows])
                if counts is None:
                    break  # Missing assets are deferred, never failed attempts.
                if any(size > specification["max_length"] for size in counts):
                    owners = await self._thread(self._store.chunk_pack_ids, [
                        row[0] for row, size in zip(rows, counts) if size > specification["max_length"]
                    ])
                    if not owners:
                        break  # Concurrent replacement removed this batch's rows.
                    for pack_id in sorted(set(owners.values())):
                        try:
                            await self._replan_vector_inputs([pack_id])
                        except (KnowledgePackError, OSError, ValueError):
                            # One missing/over-capacity source cannot starve
                            # unrelated packs. Exclude it for this round so
                            # re-reading the same rows cannot spin forever.
                            deferred_packs.add(pack_id)
                            if self._pending_chunk_reconcile:
                                return processed  # Reconcile before planning or encoding another pack.
                    continue  # Re-read the new complete inputs before inference.
            started = time.monotonic()
            try:
                compute = getattr(self.embedder, "embed_documents", self.embedder.embed_batch)
                vectors = await compute([text for _id, _hash, text in rows])
            except EmbedDeferred as deferred:
                # Scheduling pressure is not an encoding attempt. Keep the
                # same pending rows and let the next round retry them.
                await asyncio.sleep(max(0.05, deferred.retry_after_ms / 1000))
                self._index_wakeup.set()
                return processed
            after_specification = await self._thread(getattr(self.embedder, "spec", lambda: None))
            if after_specification != specification:
                break  # Changed resources cannot certify the batch just computed.
            inference_seconds = time.monotonic() - started
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
                lambda blobs=blobs, pack_ids=pack_ids: self._thread(
                    self._store.store_vectors, model_id=model_id, rows=blobs, pack_ids=pack_ids,
                    specification=specification,
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
            # Every batch that ran inference pauses, also one that stored
            # nothing: failing chunks must not push indexing past its duty.
            await asyncio.sleep(_batch_pause(inference_seconds))
            if stored == 0:
                break
            if until is not None and time.monotonic() >= until:
                break
        return processed

    # ── queries ─────────────────────────────────────────────────────

    def _current_packs(self, pack_ids: Sequence[str], registry: Registry, *, management: bool = False) -> set[str]:
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
            and (current := self._registry.packs.get(pack_id)) is not None and (management or current.enabled)
            and indexed.get(pack_id) == record.pack_sha256
        }

    def _allowed_pack_ids(self, registry: Registry, material_type: str) -> list[str]:
        wanted = MATERIAL_TYPES if material_type in ("", "auto", "all") else (material_type,)
        return [
            record.pack_id
            for record in registry.packs.values()
            if record.enabled and record.effective_material_type in wanted and record.pack_id not in self._broken_packs
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
                # Imported entry titles can contain original filenames. Keep
                # diagnostics to structural counts and opaque pack identities.
                entry_title="",
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
                    # A pack being replaced (rows newer than this snapshot)
                    # would take sample picks or candidate slots and then be
                    # dropped at rendering, hiding matches of other packs.
                    current = await self._query_thread(self._current_packs, allowed, registry)
                    allowed = [pack_id for pack_id in allowed if pack_id in current]
                    if not allowed:
                        return finish("miss")
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
                logger.warning("[Knowledge] query failed: %s", type(exc).__name__)
                return finish("error", error_type=type(exc).__name__)
        if not hits:
            return finish("miss", retrieval_mode=retrieval_mode)
        return finish("matched", hits=hits, context=context, retrieval_mode=retrieval_mode)

    async def turn_context(
        self,
        *,
        text: str,
        exclude_card_ids: Sequence[str] = (),
        budget_ms: int = DEFAULT_QUERY_BUDGET_MS,
        language: str | None = None,
    ) -> dict[str, Any]:
        """Cards a chat message brings in unasked (passive turn context).

        Only packs whose ``auto_context`` is on take part, under the global
        switch. Knowledge and corpus are chosen separately (``passive``) and
        returned as two sections of bare cards for Main to wrap:
        ``{"knowledge": {"context", "card_ids", "tokens"}, "corpus": {...}}``.
        ``exclude_card_ids`` are cards the session still carries; they are
        left out before anything is ranked. Nothing about the session is kept
        here. The result never raises.
        """
        started = time.monotonic()
        text = str(text or "").strip()[:TURN_CONTEXT_QUERY_MAX_CHARS]
        budget = min(max(int(budget_ms or DEFAULT_QUERY_BUDGET_MS), 50), MAX_QUERY_BUDGET_MS) / 1000
        excluded = {str(card_id) for card_id in exclude_card_ids or ()}

        def finish(result: str, **extra: Any) -> dict[str, Any]:
            elapsed = int((time.monotonic() - started) * 1000)
            sections = {
                kind: extra.get(kind) or {"context": "", "card_ids": [], "tokens": 0}
                for kind in MATERIAL_TYPES
            }
            hits = sum(len(section["card_ids"]) for section in sections.values())
            # Runs on every text turn: only outcomes worth a look are kept,
            # so routine misses do not push tool lookups out of the log.
            if result in PASSIVE_DIAGNOSTIC_RESULTS:
                self.diagnostics.record_query(
                    mode="passive",
                    result=result,
                    retrieval_mode=extra.get("retrieval_mode", ""),
                    hits=hits,
                    entry_title="",
                    pack_id="",
                    elapsed_ms=elapsed,
                    error_type=extra.get("error_type", ""),
                )
            return {
                "ok": True,
                "result": result,
                **sections,
                "retrieval_mode": extra.get("retrieval_mode", ""),
                "elapsed_ms": elapsed,
            }

        if self._state != "ready":
            return finish("unavailable")
        if not self._registry.enabled:
            return finish("disabled")
        if not text:
            return finish("miss")
        registry = self._registry
        allowed = [
            pack_id
            for pack_id in self._allowed_pack_ids(registry, "auto")
            if (record := registry.packs.get(pack_id)) is not None and record.auto_context
        ]
        if not allowed:
            return finish("off")
        if self._query_slots.locked():
            return finish("busy")
        async with self._query_lease():
            deadline = started + budget
            try:
                async with asyncio.timeout(max(deadline - time.monotonic(), 0.01)):
                    current = await self._query_thread(self._current_packs, allowed, registry)
                    allowed = [pack_id for pack_id in allowed if pack_id in current]
                    if not allowed:
                        return finish("miss")
                    picked, retrieval_mode = await self._passive_pick(
                        text, allowed, deadline, registry, excluded
                    )
                    if not any(picked.values()):
                        return finish("miss", retrieval_mode=retrieval_mode)
                    sections = await self._query_thread(
                        self._render_turn_sections, picked, text, language, registry
                    )
            except TimeoutError:
                return finish("timeout")
            except Exception as exc:
                logger.warning("[Knowledge] turn context failed: %s", type(exc).__name__)
                return finish("error", error_type=type(exc).__name__)
        if not any(section["card_ids"] for section in sections.values()):
            return finish("miss", retrieval_mode=retrieval_mode)
        return finish("matched", retrieval_mode=retrieval_mode, **sections)

    async def _passive_pick(
        self,
        text: str,
        allowed: list[str],
        deadline: float,
        registry: Registry,
        excluded: set[str],
    ) -> tuple[dict[str, list[PassiveHit]], str]:
        """Candidates from all three signals, judged per material type."""
        model_id = self._current_model_id()
        # Read together, with no await between: the generation names this
        # very snapshot in the calibration cache.
        snapshot = self._vectors
        snapshot_key = self._vectors_built_for
        snapshot = await self._compatible_snapshot(snapshot)
        vector_packs = [
            pack_id
            for pack_id in allowed
            if (record := registry.packs.get(pack_id)) is not None
            and record.local_embedding
            and (
                snapshot is None
                or snapshot.pack_versions is None
                or snapshot.pack_versions.get(pack_id, record.pack_sha256) == record.pack_sha256
            )
        ]
        type_packs = {
            material_type: [
                pack_id
                for pack_id in allowed
                if registry.packs[pack_id].effective_material_type == material_type
            ]
            for material_type in MATERIAL_TYPES
        }
        embed_task: asyncio.Task[Any] | None = None
        if (
            model_id is not None
            and snapshot is not None
            and snapshot.model_id == model_id
            and set(vector_packs) & set(snapshot.pack_ids)
            and len(self._query_embeddings) < MAX_QUERY_EMBEDDINGS
            and self.embedder is not None
        ):
            self._calibration.ensure_probes(model_id, self.embedder.embed_batch)
            embed_task = asyncio.create_task(getattr(self.embedder, "embed_query", self.embedder.embed)(text))
            embed_task.add_done_callback(_consume)
            self._query_embeddings.add(embed_task)
            embed_task.add_done_callback(self._query_embeddings.discard)
        # Cards the session still carries are left out before any cut, so
        # they cannot take the slots of the next-best cards. Disabled cards
        # stay out of the vector cut and the calibration background too.
        excluded_ids, disabled_ids, exact_ids, lexical_ids, named_ids = await self._query_thread(
            self._passive_lexical_search, text, allowed, registry, excluded
        )
        query_vector: np.ndarray | None = None
        if embed_task is not None:
            remaining = deadline - time.monotonic()
            done, _pending = await asyncio.wait(
                {embed_task}, timeout=max(remaining - QUERY_RENDER_RESERVE_SECONDS, 0)
            )
            if embed_task in done and embed_task.exception() is None and embed_task.result():
                blob = normalize_vector(embed_task.result())
                if blob is not None:
                    query_vector = np.frombuffer(blob, dtype="<f4")
        # Each material type gets its own candidate cut, so many close cards
        # of one type never push the other type's leader out.
        semantic_by_type: dict[str, list[SemanticMatch]] = {}
        if query_vector is not None:
            vector_excluded = excluded_ids | disabled_ids
            # One hop per type: a query cancelled or timed out between them
            # does not go on scanning the other type while holding its slot.
            for material_type, packs in type_packs.items():
                type_vector_packs = [pack_id for pack_id in vector_packs if pack_id in packs]
                if type_vector_packs:
                    semantic_by_type[material_type] = await self._query_thread(
                        self._passive_semantic_search, snapshot, query_vector,
                        type_vector_packs, vector_excluded,
                    )
        semantic = [match for matches in semantic_by_type.values() for match in matches]
        candidate_ids = list(dict.fromkeys(
            [*exact_ids, *named_ids, *lexical_ids, *(m.entry_id for m in semantic)]
        ))
        entries = await self._query_thread(self._store.fetch_entries, candidate_ids)
        allowed_set = await self._query_thread(self._current_packs, allowed, registry)
        picked: dict[str, list[PassiveHit]] = {}
        for material_type in MATERIAL_TYPES:
            type_set = {pack_id for pack_id in type_packs[material_type] if pack_id in allowed_set}
            if not type_set:
                picked[material_type] = []
                continue
            usable = {
                entry_id
                for entry_id, entry in entries.items()
                if entry.pack_id in type_set
                and not _disabled_in(entry, registry)
                and f"{entry.pack_id}/{title_key(entry.title)}" not in excluded
            }
            type_vector_packs = [pack_id for pack_id in vector_packs if pack_id in type_set]
            background = (
                self._calibration.background(
                    snapshot, snapshot_key, type_vector_packs, material_type, self._thread,
                    excluded_entry_ids=disabled_ids,
                )
                if query_vector is not None and type_vector_packs
                else None
            )
            picked[material_type] = select_hits(
                text,
                entries=entries,
                usable=usable,
                exact_ids=[entry_id for entry_id in exact_ids if entry_id in usable],
                named_ids=[entry_id for entry_id in named_ids if entry_id in usable],
                lexical_ids=[entry_id for entry_id in lexical_ids if entry_id in usable],
                semantic=[
                    match for match in semantic_by_type.get(material_type, ())
                    if match.entry_id in usable
                ],
                thresholds=thresholds_for(material_type, background),
            )
        return picked, ("hybrid" if query_vector is not None else "bm25")

    def _excluded_entry_ids(self, card_ids: set[str]) -> set[int]:
        """Entry ids of ``pack_id/title_key`` card ids."""
        titles: dict[str, list[str]] = {}
        for card_id in card_ids:
            pack_id, _, key = card_id.partition("/")
            if pack_id and key:
                titles.setdefault(pack_id, []).append(key)
        return self._store.entry_ids_by_title(titles) if titles else set()

    def _passive_lexical_search(
        self, text: str, pack_ids: list[str], registry: Registry, excluded_cards: set[str]
    ) -> tuple[set[int], set[int], list[int], list[int], list[int]]:
        """Exclusions and keyword candidates in one worker-thread hop.

        Returns the entry ids of ``excluded_cards``, the disabled entry ids of
        ``pack_ids`` (index and registry snapshot), then the exact, keyword
        and named candidates.
        """
        excluded_ids = self._excluded_entry_ids(excluded_cards)
        snapshot_disabled = self._snapshot_only_disabled(pack_ids, registry)
        disabled_ids = self._store.disabled_entry_ids(pack_ids) | snapshot_disabled
        # Disabled index rows are filtered in SQL already.
        excluded = excluded_ids | snapshot_disabled
        exact_ids, lexical_ids = self._store.lexical_candidates(
            text, pack_ids=pack_ids, limit=LEXICAL_CANDIDATES, exclude_ids=excluded,
        )
        named_ids = self._store.named_candidates(text, pack_ids=pack_ids, exclude_ids=excluded)
        return excluded_ids, disabled_ids, exact_ids, lexical_ids, named_ids

    def _passive_semantic_search(
        self,
        snapshot: VectorSnapshot | None,
        query_vector: np.ndarray,
        pack_ids: list[str],
        excluded_ids: set[int],
    ) -> list[SemanticMatch]:
        """Vector candidates of one material type, with its own cut so many
        close cards of one type never push the other type's leader out."""
        return semantic_candidates(
            snapshot,
            query_vector,
            allowed_pack_ids=pack_ids,
            exclude_entry_ids=excluded_ids,
            limit=PASSIVE_SEMANTIC_CANDIDATES,
            threshold=-1.0,
        )

    def _render_turn_sections(
        self,
        picked: dict[str, list[PassiveHit]],
        text: str,
        language: str | None,
        registry: Registry,
    ) -> dict[str, dict[str, Any]]:
        ids = [hit.entry_id for hits in picked.values() for hit in hits]
        entries = self._store.fetch_entries(ids)
        current = self._current_packs([entry.pack_id for entry in entries.values()], registry)
        sections: dict[str, dict[str, Any]] = {}
        for material_type in MATERIAL_TYPES:
            cards: list[RenderCard] = []
            card_ids: list[str] = []
            for hit in picked.get(material_type, ()):
                entry = entries.get(hit.entry_id)
                record = registry.packs.get(entry.pack_id) if entry is not None else None
                if entry is None or record is None or _disabled_in(entry, registry) or entry.pack_id not in current:
                    continue
                bodies = self._excerpt_bodies(entry, hit.semantic_chunk)
                index = best_excerpt_index(text, bodies, hit.semantic_chunk)
                excerpt = bodies[index] if index == 0 else f"... {bodies[index]}"
                cards.append(
                    RenderCard(
                        title=entry.title,
                        material_type=material_type,
                        summary=entry.summary,
                        content=excerpt,
                        source_name=record.source.name,
                        source_license=record.source.license,
                    )
                )
                card_ids.append(f"{entry.pack_id}/{title_key(entry.title)}")
            context, tokens = render_turn_cards(
                cards, language=language, max_tokens_per_card=TURN_CONTEXT_ITEM_MAX_TOKENS
            )
            sections[material_type] = {"context": context, "card_ids": card_ids, "tokens": tokens}
        return sections

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
            return await self._thread(fn, *args, **kwargs)
        if self._query_pool is None:
            self._query_pool = concurrent.futures.ThreadPoolExecutor(
                max_workers=QUERY_CONCURRENCY, thread_name_prefix="knowledge-query"
            )
        future = self._query_pool.submit(contextvars.copy_context().run, fn, *args, **kwargs)
        work.append(future)
        self._track_thread_work(future)
        return await asyncio.wrap_future(future)

    async def _sample(
        self, tag: str, allowed: list[str], limit: int, registry: Registry
    ) -> list[RankedHit]:
        rows = await self._query_thread(self._store.entries_with_tag, tag, allowed)
        # Read the versions after the rows: a pack replaced since the query's
        # version check has its new rows here, which would take picks and then
        # be dropped at rendering. Choose among unchanged packs only.
        current = await self._query_thread(self._current_packs, allowed, registry)
        # Drop entries the query's registry snapshot disables before choosing,
        # or one of them could take a slot and then be rejected at rendering.
        ids = [
            entry_id
            for entry_id, pack_id, title in rows
            if pack_id in current
            and (
                (record := registry.packs.get(pack_id)) is None
                or title_key(title) not in record.disabled_titles
            )
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
        snapshot = await self._compatible_snapshot(snapshot)
        # A pack with local vectors turned off is keyword-only, even if
        # vectors from before the switch are still in the snapshot; when no
        # eligible pack has vectors, the query is not embedded at all.
        vector_packs = [
            pack_id
            for pack_id in allowed
            if (record := registry.packs.get(pack_id)) is not None
            and record.local_embedding
            # Vectors read for another version of the pack (it was replaced
            # and the snapshot is still being reloaded) point at entry ids
            # that no longer exist: they would fill the semantic cutoff and
            # then be dropped. Until the reload the pack is keyword-only.
            and (
                snapshot is None
                or snapshot.pack_versions is None
                or snapshot.pack_versions.get(pack_id, record.pack_sha256) == record.pack_sha256
            )
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
            embed_task = asyncio.create_task(getattr(self.embedder, "embed_query", self.embedder.embed)(query))
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
        for attempt in range(2):
            if attempt:
                exact_ids, lexical_ids = await self._query_thread(
                    self._lexical_search, query, allowed, registry
                )
            semantic = (
                await self._query_thread(
                    self._semantic_search, snapshot, query_vector, vector_packs, registry
                )
                if query_vector is not None and vector_packs
                else []
            )
            candidate_ids = list(
                dict.fromkeys([*exact_ids, *lexical_ids, *(m.entry_id for m in semantic)])
            )
            entries = await self._query_thread(self._store.fetch_entries, candidate_ids)
            # Read the versions after the rows: rows of a pack replaced in
            # between then show a newer version and are left out, never
            # mislabelled.
            allowed_set = await self._query_thread(self._current_packs, allowed, registry)
            if attempt or not allowed_set or set(allowed) <= allowed_set:
                break
            # A pack was replaced while candidates were chosen: its rows (or
            # its stale vectors) may have pushed valid matches of other packs
            # below the cutoffs. Choose once more without it.
            allowed = [pack_id for pack_id in allowed if pack_id in allowed_set]
            vector_packs = [pack_id for pack_id in vector_packs if pack_id in allowed_set]
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
            bodies = self._excerpt_bodies(entry, ranked_hit.semantic_chunk)
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

    def _excerpt_bodies(self, entry: StoredEntry, semantic_chunk: int | None) -> list[str]:
        """Render the actual token-fitted chunk instead of a different char split."""
        if semantic_chunk is not None:
            inputs = self._store.entry_embedding_inputs(entry.entry_id)
            bodies = []
            for index, text in enumerate(inputs):
                header = entry.title + ("\n" + entry.summary if index == 0 and entry.summary else "")
                prefix = header[:MAX_HEADER_CHARS] + "\n"
                if not text.startswith(prefix):
                    break
                bodies.append(text[len(prefix):])
            else:
                if bodies:
                    return bodies
        return chunk_bodies(entry.content) or [entry.content]

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
        visible = await self._thread(
            self._current_packs, [pack_id] if pack_id else list(registry.packs), registry, management=True
        )
        pack_ids = sorted(visible)
        if query:
            entries = await self._thread(
                self._store.search_entries, query, pack_ids=pack_ids, limit=limit + 1, offset=offset
            )
            has_more = len(entries) > limit
            entries = entries[:limit]
            total = None
        else:
            total = await self._thread(self._store.count_entries, pack_ids=pack_ids)
            entries = await self._thread(
                self._store.list_entries, pack_ids=pack_ids, limit=limit, offset=offset
            )
            has_more = offset + len(entries) < total
        # Recheck after the read: a pack replaced in between is left out
        # rather than shown with the snapshot's (older) metadata.
        still_current = await self._thread(
            self._current_packs, sorted({entry.pack_id for entry in entries}), registry, management=True
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
        if not await self._thread(self._current_packs, [pack_id], registry, management=True):
            raise KnowledgeUnavailable("not_found")
        entry = await self._thread(self._store.get_entry, pack_id, title)
        if entry is None or not await self._thread(self._current_packs, [pack_id], registry, management=True):
            raise KnowledgeUnavailable("not_found")
        return {"entry": self._entry_payload(entry, detail=True)}

    async def list_packs(self) -> list[dict[str, Any]]:
        self._require_ready()
        model_id = self._current_model_id()
        provider = getattr(self.embedder, "spec", None)
        specification = await self._thread(provider) if callable(provider) else None
        # A profile name alone cannot certify vectors after resource changes.
        # Legacy providers have no spec API; modern unverifiable ones wait.
        if callable(provider) and specification is None:
            model_id = None
        counts, stats = await asyncio.gather(
            self._thread(self._store.entry_counts),
            self._thread(self._store.chunk_stats, model_id, specification=specification),
        )
        embedding_state = self._embedding_state()
        # Inspect local sidecar facts off-loop; imported ZIP/JSON origins alone
        # do not grant the document-management capability.
        def source_capabilities():
            self._source_capabilities = {key: value for key, value in self._source_capabilities.items() if key in self._registry.packs}
            # Bind each hint to the exact body examined in this worker. The
            # listing can observe a replacement registry after this returns.
            records = self._registry.packs
            return {key: record.pack_sha256 for key, record in records.items() if self._listed_sources_managed(key)}
        managed = await self._thread(source_capabilities)
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
                    "enabled": record.enabled,
                    "origin": record.origin,
                    "retained": False,
                    "sources_managed": managed.get(record.pack_id) == record.pack_sha256,
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
                    "subscription": record.subscription.to_json() if record.subscription else None,
                    "installed_at": record.installed_at,
                    "updated_at": record.updated_at,
                }
            )
        for retained in await self._thread(self._sources.retained_packs):
            if retained["pack_id"] in self._registry.packs:
                continue
            packs.append({"pack_id": retained["pack_id"], "source": {"name": retained["name"], "homepage": "", "license": ""},
                          "declared_material_type": "knowledge", "material_type_override": None, "effective_material_type": "knowledge",
                          "entries": 0, "disabled_entries": 0, "enabled": False, "origin": "imported", "retained": True,
                          "sources_managed": True,
                          "auto_context": False, "local_embedding": False, "chunks_total": 0, "chunks_ready": 0,
                          "chunks_failed": 0, "vector_state": "off", "broken": False, "subscription": None,
                          "installed_at": "", "updated_at": ""})
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
