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

"""The knowledge runtime and transactional coordinator owned by Memory Server.

QueryEngine, ExportManager and VectorIndexer contain their domain workflows.
This service remains the single owner of mutable state, admission, worker
lifetimes and the registry/SQLite transaction boundary; collaborators never
copy those resources or create independent locks.

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

Patch contract: domain mixins (``_service_packs``, ``_service_jobs``,
``_service_vectors``) resolve patchable module-level symbols (e.g.
``KnowledgeStore``, ``MAX_*``, ``STATUS_SOURCES``, ``WRITE_LOCK_TIMEOUT_SECONDS``,
``_read_bounded``, ``atomic_write_bytes``, ``atomic_write_json``,
``canonical_pack_bytes``, ``logger``, ``save_registry``, ``shutil``, ``utc_now``)
late-bound via a function-local ``from . import service as _svc`` so tests can
monkeypatch ``services.knowledge.service.<attr>`` directly and each mixin can
still be imported standalone without circular-import errors.
"""
# ruff: noqa: F401

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import contextvars
import logging
import inspect
import json
import hashlib
import shutil
import threading
import time
import uuid
from collections import OrderedDict, deque
from dataclasses import replace
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, Coroutine, Mapping, Protocol, Sequence, TypeVar

from utils.file_utils import atomic_write_bytes, atomic_write_json

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
from .subscriptions import KnowledgeSubscription, Release, identity_conflict, parse_subscription
from .passive import Calibration
from .store import (
    MAX_CHUNKS_PER_PACK,
    MAX_TOTAL_CHUNKS,
    KnowledgeStore,
    KnowledgeStoreError,
    StoredEntry,
    VectorSnapshot,
    count_pack_chunks,
)
from .text import title_key
from .importing.sources import SourceStore
from .importing.projection import project_pack
from .importing.commit_policy import build_import_record
from .querying import DEFAULT_QUERY_BUDGET_MS, QueryEngine
from .exports import ExportManager
from .indexing import VectorIndexer, _ReplanInputRefusal, _ReplanLimitRefusal
from .pack_files import read_pack_bytes as _read_bounded, retain_revision as _retain_revision
from .jobs import ACTIVE_JOB_STATES, TERMINAL_JOB_STATES, ImportJob, consume_task_result as _consume


logger = logging.getLogger("N.E.K.O.Knowledge")
_T = TypeVar("_T")

DATABASE_FILE = "knowledge.db"
STAGING_DIR = ".staging"
WRITE_LOCK_TIMEOUT_SECONDS = 5.0
QUERY_CONCURRENCY = 4
# How often one import may give way to removals that then fail.
MAX_IMPORT_YIELDS = 8
# How long shutdown waits for writes whose caller was cancelled.
DETACHED_WRITE_WAIT_SECONDS = 10.0
MAX_TRACKED_JOBS = 50
# One bound for every durable task-record reader. Records hold only bounded
# metadata; a source selection is frozen in the job's own ``.pack.json``.
MAX_JOB_RECORD_BYTES = 128 * 1024
MAX_PENDING_IMPORTS = 3
# Packs are listed whole on the management page; keep that list small.
MAX_PACKS = 200
STATUS_SOURCES = 12


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


# The thread work of the query running in the current task (see _query_lease).
_QUERY_WORK: contextvars.ContextVar[list[concurrent.futures.Future[Any]] | None] = (
    contextvars.ContextVar("knowledge_query_work", default=None)
)


class _SelectionChanged(Exception):
    """The active source selection changed before a last-source removal."""


def _budgeted_plan(pack: KnowledgePack, options: dict[str, Any], others: int) -> tuple[dict[str, Any], int] | None:
    """Pick the rebuild plan that fits the chunk budgets, preferring vectors.

    Try the token plan with verified archive rows, then without the token
    plan, then the local (legacy) layout. A token plan that cannot fit an
    entry's header counts as not fitting. A rebuild must never grow a pack
    past the per-pack or total limits: when no plan fits (e.g. a pack admitted
    with its archive layout whose retained ZIP is now gone), return None and
    let the caller refuse the rebuild before writing anything.
    """
    for dropped in ((), ("fit",), ("fit", "seed_vectors")):
        trial = {key: value for key, value in options.items() if key not in dropped}
        try:
            chunks = count_pack_chunks(pack, fit=trial.get("fit"), seed_vectors=trial.get("seed_vectors", ()))
        except ValueError as exc:
            # A pack admitted before tokenizer assets existed may have a header
            # that alone exceeds the token budget. That rejects only the token
            # plan; it is not index corruption, so try the plans without fit.
            if trial.get("fit") is None or str(exc) != "embedding_input_too_large":
                raise
            continue
        if chunks <= MAX_CHUNKS_PER_PACK and others + chunks <= MAX_TOTAL_CHUNKS:
            return trial, chunks
    return None


def _count_admitted_chunks(pack: KnowledgePack, *, fit, seed_vectors: Sequence[Mapping] = ()) -> int:
    """Chunk count for admission and commit, rejecting an unfittable header.

    An entry whose title/summary header alone exceeds the token budget can
    never be split to fit; report it as the stable ``invalid_entry`` reason
    instead of a generic failure.
    """
    try:
        return count_pack_chunks(pack, fit=fit, seed_vectors=seed_vectors)
    except ValueError as exc:
        if fit is not None and str(exc) == "embedding_input_too_large":
            raise KnowledgePackError("invalid_entry") from None
        raise


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


def _covers(releases: frozenset[Release] | None, subscription: KnowledgeSubscription | None) -> bool:
    """Whether a removal naming ``releases`` (``None``: all) overrides an import carrying ``subscription``."""
    return releases is None or (subscription is not None and subscription.release in releases)

from ._service_packs import _KnowledgePacksMixin
from ._service_jobs import _KnowledgeJobsMixin
from ._service_vectors import _KnowledgeVectorsMixin


class KnowledgeService(
    _KnowledgePacksMixin,
    _KnowledgeJobsMixin,
    _KnowledgeVectorsMixin,
):
    def __init__(self, root: Path, *, embedder: Embedder | None = None) -> None:
        self.root = Path(root)
        self.embedder = embedder
        self.diagnostics = KnowledgeDiagnostics()
        self._store = KnowledgeStore(self.root / DATABASE_FILE)
        self._registry = Registry()
        # Policy ordering is process-local, separate from content identity and
        # wall-clock timestamps. A new instance cannot reuse an old sequence.
        self._policy_epoch = uuid.uuid4().hex
        self._registry_snapshot = (self._registry, 0)
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
        # (archive, spec) -> file identity of a retained ZIP that failed to
        # seed; re-read only after the file itself changes.
        self._seed_failures: dict[tuple[str, str], tuple[int, ...] | None] = {}
        # A replan can commit SQLite before registry persistence fails. Block
        # further capacity decisions/encoding until their counts agree again.
        self._pending_chunk_reconcile = False
        # pack_id -> ((pack digest, spec digest, other packs' chunks), reason)
        # of a token replan refused by chunk limits. Its over-long rows count
        # as failed until the inputs that reason depends on change; then they
        # are reopened and the replan is tried once more.
        self._refused_replans: dict[str, tuple[tuple[str, str, int], str]] = {}
        self._source_capabilities: dict[str, tuple[tuple, bool]] = {}
        # Queries borrow this owner so snapshot replacement and shutdown still
        # share the original leases, thread tracking and model state.
        self._queries = QueryEngine(self)
        # Export publication uses this same job queue, write lock and shutdown
        # ownership; the manager owns orchestration, not a second runtime.
        self._exports = ExportManager(self)
        # The indexer schedules against this owner; transactional replanning
        # and capacity reconciliation keep using the service's single lock.
        self._indexer = VectorIndexer(self)

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
            asyncio.create_task(self._indexer._index_loop(), name="knowledge-indexer"),
        ]
        self._indexer._schedule_vector_refresh()

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
        except (OSError, KnowledgeRegistryError):
            # Failure to persist registry facts is not SQLite corruption. The
            # existing startup error path keeps the library unavailable until
            # it can retry without deleting retained rows or vectors.
            raise
        except Exception as exc:
            logger.warning("[Knowledge] index reconcile failed (%s); rebuilding", type(exc).__name__)
            self._store.remove_files()
            self._store.initialize()
            broken, registry = self._reconcile_blocking(registry)
        self._publish_registry(registry)
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
                    raw = stream.read(MAX_JOB_RECORD_BYTES + 1)
                if len(raw) > MAX_JOB_RECORD_BYTES:
                    # The UUID filename is the only diagnostic; never log content.
                    logger.warning("[Knowledge] skipped oversized job record job=%s", path.stem)
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
                if not isinstance(job.created_at, str) or not isinstance(job.updated_at, str):
                    # The restart order below sorts by these; one damaged
                    # record must be skipped, not fail the whole start.
                    continue
                if job.state in ACTIVE_JOB_STATES:
                    record = self._registry.packs.get(job.pack_id)
                    # A pack the startup reconcile found broken was not
                    # repaired by this task, whatever its registry digest says.
                    if (job.kind != "pack_export" and record is not None
                            and job.pack_id not in self._broken_packs
                            and record.pack_sha256 == job.staged_sha256
                            and record.subscription == job.subscription
                            and record.archive_sha256 == job.archive_sha256):
                        job.state, job.reason, job.committed, job.published = "active", "", True, True
                    else:
                        job.state, job.reason = "failed", "process_interrupted"
                    try:
                        atomic_write_json(path, self._job_record(job))
                    except OSError:
                        # A valid task stays visible and retryable even when
                        # its reconciled status cannot be written back yet.
                        logger.warning("[Knowledge] job reconcile writeback failed job=%s", job.job_id)
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
        """Match the registry; return packs whose complete index cannot be rebuilt."""
        indexed = self._store.pack_versions()
        for pack_id in indexed.keys() - registry.packs.keys():
            self._store.delete_pack(pack_id)
        entry_counts = self._store.entry_counts()
        chunk_counts = self._store.chunk_stats(None)
        search_rows = self._store.search_row_counts()
        broken: list[str] = []
        original_registry = registry
        # Reserve every pack's footprint (registered or physically kept rows,
        # whichever is larger) before planning any rebuild, so the iteration
        # order cannot let an earlier rebuild use capacity a later refused
        # pack still occupies. A rebuilt pack then reserves its actual plan.
        reserved = {key: max(value.chunks, chunk_counts.get(key, {}).get("total", 0))
                    for key, value in registry.packs.items()}
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
                reserved[pack_id] = record.chunks  # Rows dropped; the registry count still stands.
                continue
            others = sum(value for key, value in reserved.items() if key != pack_id)
            plan = _budgeted_plan(pack, self._rebuild_options(record, "startup"), others)
            if plan is None:
                # A capacity refusal is not SQLite corruption: keep the old
                # rows/record and use the existing reimport repair path. Do not
                # let it reach _open_blocking's database replacement fallback.
                broken.append(pack_id)
                # The kept rows (e.g. an unfinished replacement) may outnumber
                # the registered count. Count whichever is larger, so capacity
                # is never underestimated while the pack waits for repair.
                if reserved[pack_id] > record.chunks:
                    registry = registry.with_pack(replace(record, chunks=reserved[pack_id]))
                logger.warning("[Knowledge] rebuild of pack %s exceeds the chunk budget", pack_id)
                continue
            options, chunks = plan
            chunk_total = self._store.replace_pack(pack, pack_sha256=record.pack_sha256,
                                                   disabled_keys=record.disabled_titles, **options)
            # Heal both counts after rebuilding. Wrappers without a return
            # value retain the verified token/seed plan, not a default layout.
            if isinstance(chunk_total, int) and not isinstance(chunk_total, bool):
                chunks = chunk_total
            actual_entries = len(pack.entries)
            reserved[pack_id] = chunks
            if actual_entries != record.entries or chunks != record.chunks:
                registry = registry.with_pack(replace(record, entries=actual_entries, chunks=chunks))
            logger.info("[Knowledge] rebuilt index of pack %s", pack_id)
        if registry != original_registry:
            try:
                save_registry(self.root, registry)
            except (OSError, KnowledgeRegistryError) as exc:
                # Broken packs can reserve more than their rows show: never
                # admit work until those reservations are durably repaired.
                if broken:
                    raise
                # A fully rebuilt index is usable with corrected in-memory
                # counts; a later startup can retry the metadata writeback.
                logger.warning("[Knowledge] could not persist reconciled registry (%s)", type(exc).__name__)
        return broken, registry

    def _rebuild_options(self, record: PackRecord, scenario: str) -> dict[str, Any]:
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
                started = time.monotonic()
                try:
                    checked = read_archive(path, expected_spec=spec)
                finally:
                    # Time actual validation on both success and failure;
                    # diagnostics must not include private paths or contents.
                    logger.info("[Knowledge] %s archive validation pack=%s elapsed_ms=%.1f",
                                scenario, record.pack_id, (time.monotonic() - started) * 1000)
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
        # Publish the immutable registry and sequence as one reference so a
        # listing cannot combine a worker's new policy with the old counter.
        self._registry_snapshot = (registry, self._registry_snapshot[1] + 1)

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
