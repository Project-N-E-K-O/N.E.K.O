"""One bounded worker and durable task facts, with explicit cancellation gates."""

from __future__ import annotations

import asyncio
from copy import deepcopy
import threading
from typing import Awaitable, Callable

from utils.file_utils import atomic_write_json

from .models import LibraryError, new_id, now


async def run_sync(function: Callable, *args):
    """Keep ownership until a cancelled await's real worker thread has exited."""
    worker = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        # Waiting cancellation does not stop a synchronous parser or SQLite writer.
        # Do not free its input or concurrency slot while it can still write.
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if worker.done() and not worker.cancelled():
            worker.exception()
        raise


class JobContext:
    """Represent one runner's cancellation and irreversible commit boundary."""

    def __init__(self, record: dict):
        self.record = record
        self.lock = threading.Lock()
        self.cancelled = threading.Event()
        self.committed = False
        # A cancelled preview may reclaim its original only after the actual
        # worker exits; cancellation itself does not close a native parser.
        self.finished = asyncio.Event()

    def check_cancelled(self) -> None:
        """Fail before using or publishing an invalidated task result."""
        if self.cancelled.is_set() and not self.committed:
            raise LibraryError("job_cancelled")

    def commit_gate(self) -> None:
        """Call under the target write lock immediately before its atomic commit."""
        with self.lock:
            self.check_cancelled()
            self.committed = True

    async def run_sync(self, function: Callable, *args):
        """Offload bounded work while retaining this job's actual worker ownership."""
        self.check_cancelled()
        result = await run_sync(function, *args)
        self.check_cancelled()
        return result


class JobManager:
    """Persist tasks without pretending to resume opaque runners after restart."""

    def __init__(self, store):
        self.store = store
        self.records: dict[str, dict] = {}
        self.contexts: dict[str, JobContext] = {}
        self.queue: asyncio.Queue = asyncio.Queue()
        self.worker: asyncio.Task | None = None
        self.closed = False

    def _save(self, record: dict) -> None:
        with self.store.guard():
            atomic_write_json(self.store.root / "jobs" / f"{record['job_id']}.json", record)

    async def start(self) -> None:
        """Recover visible facts; interrupted work needs an explicit retry of frozen inputs."""
        def load():
            records = {}
            for path in (self.store.root / "jobs").glob("*.json"):
                record = self.store.read(path)
                previous = deepcopy(record)
                preview_id = record["frozen_input"].get("preview_id") if record["kind"] in {"preview", "index"} else None
                record.setdefault("preview_id", preview_id)
                record.setdefault("input_discarded", False)
                if preview_id and (not self.store.preview_path(preview_id).is_file()
                                   or not (self.store.root / "staging/previews" / f"{preview_id}.original").is_file()):
                    # Recover an older consumed preview, or a crash after staging
                    # removal but before all related job flags were persisted.
                    record["input_discarded"] = True
                if record["state"] in {"queued", "running"}:
                    record.update(state="interrupted", reason="process_interrupted", updated_at=now())
                if record != previous:
                    self._save(record)
                records[record["job_id"]] = record
            return records
        self.records = await run_sync(load)
        self.worker = asyncio.create_task(self._loop(), name="personal-library-jobs")

    @staticmethod
    def public(record: dict) -> dict:
        """Hide frozen internal task inputs from the browser-facing status object."""
        result = deepcopy({key: value for key, value in record.items() if key != "frozen_input"})
        # Only the opaque input identity is public, never frozen bodies or paths.
        result["preview_id"] = record["frozen_input"].get("preview_id") if record["kind"] in {"preview", "index"} else None
        result["input_discarded"] = bool(record.get("input_discarded", False))
        return result

    async def mark_preview_discarded(self, preview_id: str) -> None:
        """Persist input consumption for every attempt sharing this preview."""
        # Other task kinds can be admitted while a save is offloaded. Input
        # admission is locked by the service; unrelated records need no marking.
        for record in list(self.records.values()):
            if record["kind"] in {"preview", "index"} and record["frozen_input"].get("preview_id") == preview_id:
                record.update(input_discarded=True, updated_at=now())
                # Always save, including a second discard after an earlier disk
                # failure. An in-memory flag alone is not a durable completion.
                await run_sync(self._save, record)

    async def submit(self, kind: str, library_id: str | None, runner: Callable[[JobContext], Awaitable[dict]], frozen_input: dict) -> dict:
        """Admit work before enqueueing; the single worker is the concurrency bound."""
        if self.closed:
            raise LibraryError("service_stopping")
        if sum(value["state"] in {"queued", "running"} for value in self.records.values()) >= self.store.limits.max_pending_jobs:
            raise LibraryError("jobs_busy")
        record = {"job_id": new_id(), "kind": kind, "library_id": library_id,
                  "document_id": frozen_input.get("document_id"), "state": "queued", "phase": kind,
                  "preview_id": frozen_input.get("preview_id") if kind in {"preview", "index"} else None,
                  "input_discarded": False,
                  "reason": "", "result": None, "cancel_requested": False,
                  "created_at": now(), "updated_at": now(), "frozen_input": deepcopy(frozen_input)}
        # Register the reservation before awaiting disk IO so simultaneous submissions
        # cannot each observe a free slot and exceed the configured bound.
        self.records[record["job_id"]] = record
        context = JobContext(record)
        self.contexts[record["job_id"]] = context
        try:
            await run_sync(self._save, record)
        except BaseException:
            self.records.pop(record["job_id"], None)
            self.contexts.pop(record["job_id"], None)
            raise
        self.queue.put_nowait((record, context, runner))
        return self.public(record)

    async def _loop(self) -> None:
        """Run one job to its real terminal state before releasing its slot."""
        while True:
            item = await self.queue.get()
            if item is None:
                self.queue.task_done()
                return
            record, context, runner = item
            try:
                context.check_cancelled()
                if record.get("input_discarded"):
                    # A prior index can consume an input shared with an already
                    # queued duplicate; it must not reopen the reclaimed file.
                    raise LibraryError("preview_input_discarded")
                record.update(state="running", updated_at=now())
                await run_sync(self._save, record)
                result = await runner(context)
                context.check_cancelled()
                record.update(state="completed", result=result, reason="")
            except LibraryError as exc:
                record.update(state="cancelled" if exc.code == "job_cancelled" else "failed", reason=exc.code)
            except Exception:
                # Persist a stable code, never an exception containing document text.
                record.update(state="failed", reason="operation_failed")
            finally:
                record["updated_at"] = now()
                try:
                    await run_sync(self._save, record)
                except Exception:
                    # Disk errors and a changed storage/write guard can prevent saving
                    # terminal facts. The active manifest stays authoritative; none of
                    # these failures may kill the worker and strand its owned queue.
                    pass
                finally:
                    self.contexts.pop(record["job_id"], None)
                    context.finished.set()
                    self.queue.task_done()

    async def cancel(self, job_id: str) -> dict:
        """Record an application-level request; never cancel a parser thread."""
        record = self.records.get(job_id)
        if record is None:
            raise LibraryError("job_not_found")
        context = self.contexts.get(job_id)
        if context:
            with context.lock:
                if context.committed:
                    raise LibraryError("job_already_committed")
                context.cancelled.set()
                record["cancel_requested"] = True
            await run_sync(self._save, record)
        return self.public(record)

    async def invalidate(self, library_id: str, document_id: str | None = None) -> None:
        """Prevent a late job from reviving a deleted target."""
        for identifier, context in list(self.contexts.items()):
            record = context.record
            # A package build owns a frozen selection of several documents.
            # Deleting any selected target invalidates that uncommitted build
            # just as deleting one preview/index target does.
            selected = record.get("frozen_input", {}).get("document_ids", [])
            if record["library_id"] == library_id and (
                document_id is None or record["document_id"] == document_id or document_id in selected
            ):
                try:
                    await self.cancel(identifier)
                except LibraryError as exc:
                    if exc.code != "job_already_committed":
                        raise

    async def close(self) -> None:
        """Stop admission, invalidate uncommitted jobs, and await actual worker exit."""
        self.closed = True
        for identifier in list(self.contexts):
            try:
                await self.cancel(identifier)
            except LibraryError:
                pass  # A committed job owns its final result and finishes normally.
        await self.queue.join()
        self.queue.put_nowait(None)
        if self.worker:
            await self.worker
