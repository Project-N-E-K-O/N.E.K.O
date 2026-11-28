# -*- coding: utf-8 -*-
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
"""Import admission, staging, background job lifecycle, and commit/rollback methods."""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import contextvars
import json
import uuid
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from .models import KnowledgePack, KnowledgePackError, decode_pack_bytes, pack_sha256
from .registry import PACKS_DIR, PackRecord, Registry
from .subscriptions import KnowledgeSubscription, identity_conflict
from .importing.commit_policy import build_import_record
from .pack_files import retain_revision as _retain_revision
from .jobs import ACTIVE_JOB_STATES, TERMINAL_JOB_STATES, ImportJob, consume_task_result as _consume
from typing import TypeVar


_T = TypeVar("_T")

class _KnowledgeJobsMixin:
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

    def _reserve_arrival(self) -> int:
        """Reserve a mutation's order before its first asynchronous wait."""
        self._request_seq += 1
        return self._request_seq

    def _admission_settled(self, arrived_at: int) -> None:
        self._admission_owned.discard(arrived_at)
        self._incoming.pop(arrived_at, None)

    def _admission_count(self) -> int:
        """Count queued work and reservations without counting an import twice."""
        active = [job for job in self._jobs.values() if job.state in ACTIVE_JOB_STATES]
        # Staging remembers the import before its reservation's finally runs.
        # Only that import represents the reservation; an export does not.
        registered_imports = {job.pack_id for job in active if job.kind != "pack_export"}
        return len(active) + sum(pack_id not in registered_imports for pack_id in self._admitting)

    def _job_capacity_reached(self) -> bool:
        """Use one admission budget for imports and exports, including reservations."""
        from . import service as _svc
        return self._admission_count() >= _svc.MAX_PENDING_IMPORTS

    def _export_admission_record(self, pack_id: str) -> PackRecord:
        """Check export admission against the same jobs and import reservations."""
        record = self._registry.packs.get(pack_id)
        if record is None:
            raise KnowledgePackError("not_found")
        active = [job for job in self._jobs.values() if job.state in ACTIVE_JOB_STATES]
        if pack_id in self._admitting or any(job.pack_id == pack_id for job in active):
            raise KnowledgePackError("job_in_progress")
        if self._job_capacity_reached():
            raise KnowledgePackError("knowledge_busy")
        return record

    async def _admit_export(self, job: ImportJob) -> dict[str, Any]:
        """Reserve before yielding, then own the first durable flush and queueing.

        ExportManager validates its format and builds the frozen job identity.
        This owner retains all queue/accounting writes, including a cancelled
        first flush and shutdown retiring the import/export runner.
        """
        job.arrived_at = self._reserve_arrival()
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
            elif job.state == "queued":
                # Explicit cancellation may finish while the first durable
                # record is being written; do not revive its queue entry.
                self._job_queue.append(job.job_id)
                self._job_queued.set()
            return {"ok": True, **job.to_json()}

        owner = asyncio.create_task(admit())
        self._own_background_task(owner)
        return await asyncio.shield(owner)

    def import_busy(self) -> bool:
        """Whether a new import would be refused for lack of room right now."""
        from . import service as _svc
        return (
            self._job_capacity_reached()
            or self._parsing >= _svc.MAX_PENDING_IMPORTS
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
        from . import service as _svc
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
            # Admission must count the same validated archive inputs that the
            # commit will install, rather than count a fresh local split.
            prepare_options = {"seed_vectors": options["seed_vectors"]} if options and options.get("seed_vectors") else {}
            pack, canonical, chunks = await self._parse_thread(parse_work, self._prepare_import, raw, **prepare_options)
        except KnowledgePackError as exc:
            return {"ok": False, "reason": exc.reason}
        if subscription is not None:
            if pack.pack_id != subscription.pack_id:
                return {"ok": False, "reason": "package_identity_mismatch"}
            if pack.material_type != subscription.material_type:
                return {"ok": False, "reason": "material_type_mismatch"}
        if len(canonical) > _svc.MAX_PACK_BYTES:
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
        if self._job_capacity_reached():
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
        from . import service as _svc
        if self._pending_chunk_reconcile:
            return {"ok": False, "reason": "knowledge_busy"}
        registry = self._registry
        others = [record for record in registry.packs.values() if record.pack_id != pack.pack_id]
        if len(others) + 1 > _svc.MAX_PACKS:
            return {"ok": False, "reason": "capacity_packs"}
        if sum(record.entries for record in others) + len(pack.entries) > _svc.MAX_TOTAL_ENTRIES:
            return {"ok": False, "reason": "capacity_entries"}
        if sum(record.chunks for record in others) + chunks > _svc.MAX_TOTAL_CHUNKS:
            return {"ok": False, "reason": "capacity_chunks"}
        if chunks > _svc.MAX_CHUNKS_PER_PACK:
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
        from . import service as _svc
        active = [job for job in self._jobs.values() if job.state in ACTIVE_JOB_STATES]
        staged_bytes = sum(job.staged_bytes for job in active) + sum(
            size for pack_id, size in self._admitting.items() if pack_id != pack.pack_id
        )
        installed_bytes = await self._thread(
            self._installed_pack_bytes, pack.pack_id, _svc._job_files(active, installing=self._registry)
        )
        if installed_bytes + staged_bytes + len(canonical) > _svc.MAX_TOTAL_PACK_BYTES:
            return {"ok": False, "reason": "capacity_bytes"}
        now = _svc.utc_now()
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
            await self._thread(_svc.atomic_write_bytes, self._staging_path(job.job_id), canonical)
            # Retain exact retry bytes apart from disposable canonical staging.
            await self._thread(_svc.atomic_write_bytes, self.root / "jobs" / (job.job_id + ".pack.json"),
                               options.get("retry_bytes", canonical))
        except BaseException:
            # Before a job owns this input, a failed second write must not
            # leave disposable staging charged against future admission.
            await self._discard_staging(job.job_id)
            raise
        if self._stopping:
            # The runner is gone; a queued job would never run. No task record
            # exists yet, so nothing else could reclaim its frozen retry input.
            await self._discard_staging(job.job_id)
            with contextlib.suppress(OSError):
                await self._thread((self.root / "jobs" / (job.job_id + ".pack.json")).unlink, missing_ok=True)
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
        if job.state == "queued":
            # Explicit cancellation can finish during the initial record write;
            # its terminal ID must not outlive admission in the runner queue.
            self._job_queue.append(job.job_id)
            self._job_queued.set()
        return {"ok": True, **job.to_json()}

    async def _parse_thread(
        self,
        parse_work: list[concurrent.futures.Future[Any]],
        fn: Callable[..., _T],
        /,
        *args: Any,
        **kwargs: Any,
    ) -> _T:
        """Run an import request's thread work; its slot stays held until it ends."""
        from . import service as _svc
        if self._parse_pool is None:
            self._parse_pool = concurrent.futures.ThreadPoolExecutor(
                max_workers=_svc.MAX_PENDING_IMPORTS, thread_name_prefix="knowledge-parse"
            )
        # Archive admission passes its verified seed plan as keyword input.
        future = self._parse_pool.submit(fn, *args, **kwargs)
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

    def _own_background_task(self, task: asyncio.Task[_T]) -> asyncio.Task[_T]:
        """Retain an already-started owner until shutdown can observe its result."""
        self._detached.add(task)
        task.add_done_callback(self._detached.discard)
        task.add_done_callback(_consume)
        return task

    def _track_thread_work(self, future: concurrent.futures.Future[Any]) -> None:
        with self._thread_work_lock:
            self._thread_work.add(future)
        future.add_done_callback(self._untrack_thread_work)

    def _untrack_thread_work(self, future: concurrent.futures.Future[Any]) -> None:
        with self._thread_work_lock:
            self._thread_work.discard(future)

    def _prepare_import(self, raw: bytes, *, seed_vectors: Sequence[Mapping] = ()) -> tuple[KnowledgePack, bytes, int]:
        from . import service as _svc
        pack = decode_pack_bytes(raw)
        return pack, _svc.canonical_pack_bytes(pack), _svc._count_admitted_chunks(pack, fit=self._chunk_fit(), seed_vectors=seed_vectors)

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
        from . import service as _svc
        try:
            raw = _svc._read_bounded(self.root / PACKS_DIR / record.file_name)
            return pack_sha256(raw) == record.pack_sha256
        except OSError:
            return False

    def _active_job_files(self) -> frozenset[str]:
        from . import service as _svc
        return _svc._job_files(
            [job for job in self._jobs.values() if job.state in ACTIVE_JOB_STATES], installing=None
        )

    def _installed_pack_bytes(self, replacing: str, job_files: frozenset[str] = frozenset()) -> int:
        """Bytes of current pack revisions and staging, except ``replacing``'s.

        Retained historical revisions have a separate, explicit no-deletion
        policy; the active-pack budget counts current revisions and staging.
        An unregistered file left in ``packs/`` (an old revision whose move
        failed, or a write interrupted before the registry commit) is kept but
        no longer startup-cleaned, so it must not consume the budget forever.
        """
        from . import service as _svc
        record = self._registry.packs.get(replacing)
        skip = record.file_name if record is not None else None
        current = {item.file_name for item in self._registry.packs.values()}
        total = 0
        for directory in (PACKS_DIR, _svc.STAGING_DIR):
            try:
                paths = list((self.root / directory).iterdir())
            except OSError:
                continue
            for path in paths:
                # .tmp: an atomic write that could neither finish nor clean up.
                if path.suffix not in (".json", ".tmp"):
                    continue
                if directory == PACKS_DIR and (path.name == skip or path.name not in current):
                    continue
                if path.name in job_files:
                    continue
                try:
                    total += path.stat().st_size
                except OSError:
                    continue
        return total

    def _staging_path(self, job_id: str) -> Path:
        from . import service as _svc
        return self.root / _svc.STAGING_DIR / f"{job_id}.json"

    @staticmethod
    def _holds_artifact(job: ImportJob) -> bool:
        """A finished export is the only handle to its artifact (download,
        publish, delete); it stays tracked until the user deletes it."""
        return job.kind == "pack_export" and job.state == "active" and bool(job.result)

    def _remember_job(self, job: ImportJob) -> None:
        from . import service as _svc
        self._jobs[job.job_id] = job
        # Imports and exports keep separate bounded windows from admission
        # on, so a queued export can never evict a retryable failed import.
        # Finished exports holding an artifact are never evicted.
        for is_export in (False, True):
            def windowed(item: ImportJob) -> bool:
                return (item.kind == "pack_export") is is_export and not self._holds_artifact(item)

            while sum(windowed(item) for item in self._jobs.values()) > _svc.MAX_TRACKED_JOBS:
                oldest = next(
                    (job_id for job_id, item in self._jobs.items()
                     if windowed(item) and item.state in TERMINAL_JOB_STATES),
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
        # The cancellation has taken effect in memory; a failed record write
        # must not turn it into a server error. A record left queued on disk
        # is reconciled as interrupted on the next start.
        with contextlib.suppress(OSError):
            await self._persist_job(job)
        return True

    def _unqueue(self, job_id: str) -> None:
        # The runner may already have taken it; then there is nothing to drop.
        with contextlib.suppress(ValueError):
            self._job_queue.remove(job_id)

    async def discard_job(self, job_id: str) -> bool:
        async def discard() -> bool:
            async with self._job_persist_lock:
                job = self._jobs.get(job_id)
                if job is None or job.state not in TERMINAL_JOB_STATES:
                    return False
                if self._holds_artifact(job):
                    # Only delete_export may remove a finished export with its ZIP.
                    return False
                # Discarding is an explicit user deletion; the frozen retry
                # input belongs only to this job. Delete it first so a failed
                # unlink leaves the card and its durable record available.
                await self._thread((self.root / "jobs" / (job_id + ".pack.json")).unlink, missing_ok=True)
                # If this second step fails, the card stays visible but JSON
                # retry input may already be gone. Retrying discard can finish;
                # late flushes share this lock and cannot revive a removed job.
                await self._thread((self.root / "jobs" / (job_id + ".json")).unlink, missing_ok=True)
                job.export_deleted = True
                self._jobs.pop(job_id, None)
                return True
        task = asyncio.create_task(discard())
        self._own_background_task(task)
        # A disconnected caller cannot interrupt the durable deletion owner.
        return await asyncio.shield(task)

    async def delete_export(self, job_id: str) -> dict[str, Any]:
        """Delegate export work while retaining the shared runtime owner."""
        return await self._exports.delete_export(job_id)

    def _archive_referenced_blocking(self, artifact_id: str, records: list[dict], *, exclude_job_id: str = "") -> bool:
        """Honor current installs and retained task history under mutation/persist locks."""
        from . import service as _svc
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
                raw = stream.read(_svc.MAX_JOB_RECORD_BYTES + 1)
            if len(raw) > _svc.MAX_JOB_RECORD_BYTES:
                _svc.logger.warning("[Knowledge] skipped oversized job record job=%s", path.stem)
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
        from . import service as _svc
        job = self._jobs.get(job_id)
        if job is None or job.state not in {"failed", "cancelled"}:
            raise KnowledgePackError("job_not_retryable")
        if job.kind == "pack_export":
            record = self._registry.packs.get(job.pack_id)
            if record is None or record.pack_sha256 != job.staged_sha256:
                raise KnowledgePackError("pack_changed")
            return await self.export_pack(job.pack_id, format_version=job.export_format, _retry_version=job.export_version)
        # A new retry has its own arrival, reserved before frozen-input I/O so
        # a later pack removal can supersede both JSON and ZIP retries.
        arrived_at = self._reserve_arrival()
        options = {"kind": job.kind, "origin": job.origin, "enabled": job.initial_enabled, "frozen_input": job.frozen_input}
        if job.archive_sha256:
            path = self.artifact_path(job.archive_sha256)
            from utils.knowledge_package import DEFAULT_LIMITS, hash_file

            digest, _size = await self._thread(hash_file, path, DEFAULT_LIMITS.max_archive_bytes)
            if digest != job.archive_sha256:
                raise KnowledgePackError("artifact_hash_mismatch")
            return await self.import_pack(path, subscription=job.subscription, _options=options, _arrived_at=arrived_at)
        try:
            raw = await self._thread(_svc._read_bounded, self.root / "jobs" / (job.job_id + ".pack.json"))
        except FileNotFoundError as exc:
            # A concurrent discard removed this job together with its input.
            raise KnowledgePackError("job_not_retryable") from exc
        expected = job.subscription.artifact_sha256 if job.subscription is not None else job.staged_sha256
        if pack_sha256(raw) != expected:
            raise KnowledgePackError("pack_file_mismatch")
        return await self.import_pack(raw, subscription=job.subscription, _options=options, _arrived_at=arrived_at)

    def _finish_job(self, job: ImportJob, state: str, reason: str = "") -> None:
        from . import service as _svc
        job.state = state
        job.reason = reason
        job.updated_at = _svc.utc_now()
        if state in TERMINAL_JOB_STATES:
            # Retry reads the retained archive, never this transient RAM copy.
            job.seed_vectors = []

    async def _discard_staging(self, job_id: str) -> None:
        """Best effort: a staged file that cannot go now is removed at the next start."""
        from . import service as _svc
        try:
            await self._thread(self._staging_path(job_id).unlink, missing_ok=True)
        except OSError:
            _svc.logger.warning("[Knowledge] could not delete staged file of job %s", job_id)

    async def _job_loop(self) -> None:
        from . import service as _svc
        while not self._stopping:
            while not self._job_queue:
                self._job_queued.clear()
                await self._job_queued.wait()
            job_id = self._job_queue.popleft()
            job = self._jobs.get(job_id)
            if job is None or job.state != "queued":
                await self._discard_staging(job_id)
                continue
            job.state, job.updated_at = "building", _svc.utc_now()
            try:
                if job.kind == "pack_export":
                    await self._exports.run(job)
                    self._finish_job(job, "active")
                    continue
                for attempt in range(_svc.MAX_IMPORT_YIELDS + 1):
                    try:
                        await self._locked(lambda job=job: self._commit_job(job), wait=True)
                        break
                    except InterruptedError:
                        if (
                            self._stopping
                            or job.cancel_requested
                            or self._removal_committed_over(job.pack_id, job.subscription, job.arrived_at)
                            # Never spin the single runner on one job.
                            or attempt == _svc.MAX_IMPORT_YIELDS
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
                await self._indexer._refresh_vectors_now()
                self._finish_job(job, "active")
                self._index_wakeup.set()
                self._indexer._schedule_vector_refresh()
            except InterruptedError:
                self._finish_job(job, "active" if job.published else "cancelled")
            except _svc.KnowledgeUnavailable as exc:
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
                _svc.logger.warning("[Knowledge] import bookkeeping failed (%s)", type(exc).__name__)
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

    def _check_commit_sources(
        self, pack: KnowledgePack, sha: str, previous: PackRecord | None, job: ImportJob,
    ) -> None:
        """Validate frozen source consent under the caller's existing write lock.

        Source authority and selected versions are read here before capacity
        planning or any file/index mutation. This keeps retries bound to the
        acknowledged source body, including repairs of the same digest.
        """
        if (job.kind == "import_commit" and previous is not None and sha != previous.pack_sha256
                and not self._sources_managed(job.pack_id)):
            # A source task (including a retry of its frozen input) may
            # only replace a pack that is still the projection of its local
            # sources; an unrelated pack that took the id since is not its
            # target, even though the old source history is still on disk.
            # Input with the registered digest is that same pack: it may
            # rewrite a missing or unreadable active file.
            raise KnowledgePackError("pack_changed")
        enabled_on_commit = previous.enabled if previous else (job.initial_enabled if job.initial_enabled is not None else True)
        if (job.frozen_input.get("source_confirmation_required")
                and enabled_on_commit
                and not job.frozen_input.get("enabled_confirmed")):
            raise KnowledgePackError("source_confirmation_required")
        if (job.kind == "import_commit" and enabled_on_commit
                and (previous is None or sha != previous.pack_sha256)):
            # A whole-pack retry can resurrect other sources removed or
            # changed after this task was confirmed. Permit only its one
            # acknowledged version beyond today's verified active selection.
            current = self._source_pack(job.pack_id)
            selected = {(row["doc_id"], row["version"]) for row in self._source_documents(pack, job.pack_id)}
            active = {(row["doc_id"], row["version"]) for row in self._sources.documents(current, job.pack_id)}
            target = job.frozen_input.get("source_target")
            allowed = set()
            if (job.frozen_input.get("enabled_confirmed") is True and isinstance(target, dict)
                    and isinstance(target.get("doc_id"), str) and type(target.get("version")) is int):
                allowed.add((target["doc_id"], target["version"]))
            # Missing scope in old records grants no new disclosure;
            # users can still retry that exact input while the pack is off.
            if not selected - active <= allowed:
                raise KnowledgePackError("source_confirmation_required")

    def _check_commit_capacity(
        self, pack: KnowledgePack, chunks: int, raw_size: int, active_jobs: frozenset[str],
    ) -> None:
        """Recheck the runtime's live budgets inside the locked commit worker.

        Keep each refusal before the following check: the disk usage scan
        must still run only after pack/entry/chunk limits pass. Constants stay
        in this module so admission and commit use the same runtime limits.
        """
        from . import service as _svc
        if chunks > _svc.MAX_CHUNKS_PER_PACK:
            raise KnowledgePackError("too_many_chunks")
        # Admission checked capacity without the lock; two imports racing
        # through it must not both land, so check again under the lock.
        others = [r for r in self._registry.packs.values() if r.pack_id != pack.pack_id]
        if len(others) + 1 > _svc.MAX_PACKS:
            raise KnowledgePackError("capacity_packs")
        if sum(r.entries for r in others) + len(pack.entries) > _svc.MAX_TOTAL_ENTRIES:
            raise KnowledgePackError("capacity_entries")
        if sum(r.chunks for r in others) + chunks > _svc.MAX_TOTAL_CHUNKS:
            raise KnowledgePackError("capacity_chunks")
        if self._installed_pack_bytes(pack.pack_id, active_jobs) + raw_size > _svc.MAX_TOTAL_PACK_BYTES:
            raise KnowledgePackError("capacity_bytes")

    async def _commit_job(self, job: ImportJob) -> None:
        from . import service as _svc
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
            raw = _svc._read_bounded(self._staging_path(job.job_id))
            sha = pack_sha256(raw)
            if sha != job.staged_sha256:
                # The staged file changed after admission; its capacity checks
                # and chunk count were for other bytes.
                raise _svc.KnowledgeUnavailable("knowledge_error")
            pack = decode_pack_bytes(raw)
            self._check_commit_sources(pack, sha, previous, job)
            # Assets may become available between admission and commit. Use
            # this single fit for capacity, registry metadata and SQLite.
            fit = self._chunk_fit()
            chunks = _svc._count_admitted_chunks(pack, fit=fit, seed_vectors=job.seed_vectors)
            self._check_commit_capacity(pack, chunks, len(raw), active_jobs)
            # Admission checked identity without the lock too; another import
            # may have installed this pack or this market package since.
            installed = {key: r.subscription for key, r in self._registry.packs.items()}
            if identity_conflict(pack.pack_id, job.subscription, installed):
                raise KnowledgePackError("subscription_identity_mismatch")
            record = build_import_record(
                pack, previous, job, sha=sha, chunks=chunks, now=_svc.utc_now(),
            )
            final_path = self.root / PACKS_DIR / record.file_name
            if previous is not None and previous.file_name != record.file_name:
                # Keep the previous immutable pack in an explicit history
                # directory before committing its replacement. History is not
                # charged as current searchable pack content.
                history = self.root / "history" / previous.pack_id / previous.file_name
                try:
                    previous_raw = _svc._read_bounded(self.root / PACKS_DIR / previous.file_name)
                except FileNotFoundError:
                    previous_raw = None  # A broken pack can be repaired by a new revision.
                if previous_raw is not None:
                    _retain_revision(history, previous_raw, previous.pack_sha256)
            _svc.atomic_write_bytes(final_path, raw)
            indexed = False
            try:
                chunk_options = {}
                if fit is not None:
                    chunk_options["fit"] = fit
                if job.seed_vectors:
                    chunk_options["seed_vectors"] = job.seed_vectors
                actual_chunks = self._store.replace_pack(
                    pack,
                    pack_sha256=sha,
                    disabled_keys=record.disabled_titles,
                    should_cancel=lambda: self._superseded(job) or self._stopping,
                    commit_gate=commit_gate,
                    **chunk_options,
                )
                indexed = True
                # A tokenizer changed during planning must not publish a
                # registry count that differs from the committed index.
                if actual_chunks != chunks:
                    raise _svc.KnowledgeUnavailable("knowledge_error")
                registry = self._registry.with_pack(record)
                _svc.save_registry(self.root, registry)
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
        from . import service as _svc
        try:
            if previous is None:
                self._store.delete_pack(pack_id)
                return
            raw = _svc._read_bounded(self.root / PACKS_DIR / previous.file_name)
            if pack_sha256(raw) != previous.pack_sha256:
                # The old file was altered: rows built from it must not carry
                # the registered version. Leave the pack without rows (not
                # served) until the startup reconcile marks it broken.
                _svc.logger.warning("[Knowledge] old file of %s changed; not restoring its index", pack_id)
                self._store.delete_pack(pack_id)
                return
            pack = decode_pack_bytes(raw)
            # Same rule as the startup reconcile.
            others = sum(item.chunks for key, item in self._registry.packs.items() if key != pack_id)
            plan = _svc._budgeted_plan(pack, self._rebuild_options(previous, "rollback"), others)
            if plan is None:
                current = self._registry.packs.get(pack_id)
                if (current is not None and self._store.pack_versions().get(pack_id) != current.pack_sha256
                        and pack_id not in self._broken_packs):
                    # A failed replacement's rows are not the registered pack;
                    # flag the existing repair route instead of reporting an
                    # unchanged import as if it had restored the old version.
                    self._broken_packs = (*self._broken_packs, pack_id)
                # SQLite may already contain the failed replacement's counts.
                # Keep them gated until the ordinary coordinator repairs them.
                self._pending_chunk_reconcile = True
                _svc.logger.warning("[Knowledge] restore of pack %s exceeds the chunk budget", pack_id)
                return
            options, _chunks = plan
            restored = self._store.replace_pack(
                pack,
                pack_sha256=previous.pack_sha256,
                disabled_keys=previous.disabled_titles,
                **options,
            )
            current = self._registry.packs.get(pack_id)
            if current is None or restored != current.chunks:
                # Compare with the registry as it is now (a queued commit may
                # have reconciled it since): repair it before any further
                # admission or encoding relies on it.
                self._pending_chunk_reconcile = True
        except Exception as exc:
            current = self._registry.packs.get(pack_id)
            try:
                matches_registry = (current is not None
                                    and self._store.pack_versions().get(pack_id) == current.pack_sha256)
            except Exception:
                # A failed version probe cannot prove the old index intact,
                # and must not replace the original rollback diagnostic.
                matches_registry = False
            if not matches_registry:
                if current is not None and pack_id not in self._broken_packs:
                    self._broken_packs = (*self._broken_packs, pack_id)
            # Even an intact registered version may have changed row counts
            # before the failure; repair counters without needlessly marking
            # that usable index broken or disabling the unchanged fast path.
            self._pending_chunk_reconcile = True
            _svc.logger.warning("[Knowledge] could not restore index after a failed import (%s)", type(exc).__name__)
