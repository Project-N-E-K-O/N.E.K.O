from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
import inspect
import logging
import secrets
import time
from typing import Any


DOCUMENT_JOB_TIMEOUT_SECONDS = 20 * 60.0
DOCUMENT_JOB_MERGE_RESERVED_SECONDS = 2 * 60.0
DOCUMENT_JOB_FINALIZE_RESERVED_SECONDS = 30.0
DOCUMENT_JOB_RESULT_TTL_SECONDS = 30 * 60.0

ProgressCallback = Callable[[str, int, int], Awaitable[None]]
JobRunner = Callable[..., Awaitable[dict[str, Any]]]
CompletionCallback = Callable[[dict[str, Any]], None]


_logger = logging.getLogger(__name__)


class DocumentAnalysisJobError(RuntimeError):
    def __init__(self, message: str, *, diagnostic: str) -> None:
        super().__init__(message)
        self.diagnostic = diagnostic


@dataclass(frozen=True, slots=True)
class DocumentJobBudget:
    started_monotonic: float
    chunk_deadline_monotonic: float
    merge_deadline_monotonic: float
    deadline_monotonic: float


@dataclass(slots=True)
class _Job:
    job_id: str
    analysis_mode: str
    document: dict[str, object]
    total_chunks: int
    status: str = "running"
    stage: str = "validating"
    completed_chunks: int = 0
    diagnostic: str = ""
    cancellation_source: str = ""
    result: dict[str, Any] = field(default_factory=dict)
    finished_at: float = 0.0
    task: asyncio.Task[None] | None = None

    def public_payload(self) -> dict[str, Any]:
        progress = (
            min(1.0, self.completed_chunks / self.total_chunks)
            if self.total_chunks > 0
            else 0.0
        )
        payload: dict[str, Any] = {
            "job_id": self.job_id,
            "status": self.status,
            "stage": self.stage,
            "analysis_mode": self.analysis_mode,
            "document": dict(self.document),
            "chunks": self.total_chunks,
            "completed_chunks": self.completed_chunks,
            "total_chunks": self.total_chunks,
            "progress": progress,
            "reply": "",
            "summary": "",
            "degraded": self.status in {"failed", "canceled"},
            "diagnostic": self.diagnostic,
            "cancellation_source": self.cancellation_source,
        }
        if self.status == "completed":
            payload.update(self.result)
            payload["status"] = "completed"
            payload["stage"] = "completed"
            payload["progress"] = 1.0
        return payload


class DocumentAnalysisJobManager:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._jobs: dict[str, _Job] = {}
        self._active_job_id = ""
        self._expiry_tasks: set[asyncio.Task[None]] = set()

    async def start(
        self,
        *,
        analysis_mode: str,
        document: dict[str, object],
        total_chunks: int,
        runner: JobRunner,
        on_completed: CompletionCallback | None = None,
    ) -> dict[str, Any]:
        async with self._lock:
            self._reap_locked()
            active = self._jobs.get(self._active_job_id)
            if active is not None and active.status == "running":
                raise DocumentAnalysisJobError(
                    "a document analysis job is already running",
                    diagnostic="document_job_busy",
                )
            job_id = secrets.token_urlsafe(24)
            job = _Job(
                job_id=job_id,
                analysis_mode=analysis_mode,
                document=dict(document),
                total_chunks=max(1, int(total_chunks)),
                stage="analyzing_chunks" if analysis_mode == "chunked" else "analyzing",
            )
            self._jobs[job_id] = job
            self._active_job_id = job_id
            job.task = asyncio.create_task(self._run(job, runner, on_completed))
            return job.public_payload()

    async def status(self, job_id: str) -> dict[str, Any]:
        async with self._lock:
            self._reap_locked()
            job = self._jobs.get(str(job_id or "").strip())
            if job is None:
                raise DocumentAnalysisJobError(
                    "document analysis job was not found",
                    diagnostic="document_job_not_found",
                )
            return job.public_payload()

    async def active(self) -> dict[str, Any]:
        async with self._lock:
            self._reap_locked()
            job = self._jobs.get(self._active_job_id)
            if job is None or job.status != "running":
                return {
                    "job_id": "",
                    "status": "idle",
                    "stage": "idle",
                    "degraded": False,
                    "diagnostic": "",
                    "cancellation_source": "",
                }
            return job.public_payload()

    async def cancel(self, job_id: str, *, source: str = "user") -> dict[str, Any]:
        task: asyncio.Task[None] | None = None
        async with self._lock:
            self._reap_locked()
            job = self._jobs.get(str(job_id or "").strip())
            if job is None:
                raise DocumentAnalysisJobError(
                    "document analysis job was not found",
                    diagnostic="document_job_not_found",
                )
            if job.status == "running":
                job.status = "canceled"
                job.stage = "canceled"
                job.diagnostic = "document_canceled"
                job.cancellation_source = str(source or "user")
                _logger.info(
                    "document analysis job canceled source=%s",
                    job.cancellation_source,
                )
                job.finished_at = time.monotonic()
                if self._active_job_id == job.job_id:
                    self._active_job_id = ""
                task = job.task
                if task is not None:
                    task.cancel()
            payload = job.public_payload()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        return payload

    async def shutdown(self) -> None:
        async with self._lock:
            tasks: list[asyncio.Task[None]] = []
            for job in self._jobs.values():
                if job.task is None or job.task.done():
                    continue
                job.status = "canceled"
                job.stage = "canceled"
                job.diagnostic = "document_canceled"
                job.cancellation_source = "plugin_shutdown"
                job.finished_at = time.monotonic()
                _logger.info("document analysis job canceled source=plugin_shutdown")
                tasks.append(job.task)
                job.task.cancel()
            expiry_tasks = list(self._expiry_tasks)
            for task in expiry_tasks:
                task.cancel()
            self._active_job_id = ""
        pending = [*tasks, *expiry_tasks]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        async with self._lock:
            late_expiry_tasks = list(self._expiry_tasks)
            for task in late_expiry_tasks:
                task.cancel()
            self._jobs.clear()
            self._expiry_tasks.clear()
        if late_expiry_tasks:
            await asyncio.gather(*late_expiry_tasks, return_exceptions=True)

    async def _run(
        self,
        job: _Job,
        runner: JobRunner,
        on_completed: CompletionCallback | None,
    ) -> None:
        started_monotonic = time.monotonic()
        deadline_monotonic = started_monotonic + DOCUMENT_JOB_TIMEOUT_SECONDS
        merge_deadline_monotonic = (
            deadline_monotonic - DOCUMENT_JOB_FINALIZE_RESERVED_SECONDS
        )
        budget = DocumentJobBudget(
            started_monotonic=started_monotonic,
            chunk_deadline_monotonic=(
                merge_deadline_monotonic - DOCUMENT_JOB_MERGE_RESERVED_SECONDS
            ),
            merge_deadline_monotonic=merge_deadline_monotonic,
            deadline_monotonic=deadline_monotonic,
        )

        async def update(stage: str, completed: int, total: int) -> None:
            async with self._lock:
                if job.status != "running":
                    raise asyncio.CancelledError
                job.stage = str(stage or job.stage)
                job.completed_chunks = max(0, min(int(completed), job.total_chunks))
                if total > 0:
                    job.total_chunks = int(total)

        try:
            runner_signature = inspect.signature(runner)
            if len(runner_signature.parameters) >= 2:
                runner_awaitable = runner(update, budget)
            else:
                runner_awaitable = runner(update)
            result = await asyncio.wait_for(
                runner_awaitable,
                timeout=max(0.0, deadline_monotonic - time.monotonic()),
            )
            async with self._lock:
                if job.status == "running":
                    job.result = dict(result)
                    job.status = "completed"
                    job.stage = "completed"
                    job.completed_chunks = job.total_chunks
                    job.finished_at = time.monotonic()
                    if on_completed is not None:
                        try:
                            on_completed(job.result)
                        except Exception:
                            _logger.exception(
                                "document analysis completion callback failed"
                            )
        except asyncio.CancelledError:
            async with self._lock:
                if job.status == "running":
                    job.status = "canceled"
                    job.stage = "canceled"
                    job.diagnostic = "document_canceled"
                    job.finished_at = time.monotonic()
        except asyncio.TimeoutError:
            _logger.warning("document analysis job canceled source=job_timeout")
            await self._fail(job, "timeout", cancellation_source="job_timeout")
        except Exception as exc:
            await self._fail(
                job, str(getattr(exc, "diagnostic", "") or "document_chunk_failed")
            )
        finally:
            # Drop the runner closure immediately. It may have captured the source,
            # chunks, and internal memos; terminal job records retain public data only.
            del runner
            del on_completed
            async with self._lock:
                if self._active_job_id == job.job_id:
                    self._active_job_id = ""
                job.task = None
                expiry_task = asyncio.create_task(self._expire_after_ttl(job.job_id))
                self._expiry_tasks.add(expiry_task)
                expiry_task.add_done_callback(self._expiry_tasks.discard)

    async def _fail(
        self,
        job: _Job,
        diagnostic: str,
        *,
        cancellation_source: str = "",
    ) -> None:
        async with self._lock:
            if job.status == "running":
                job.status = "failed"
                job.stage = "failed"
                job.diagnostic = diagnostic
                job.cancellation_source = cancellation_source
                job.finished_at = time.monotonic()

    async def _expire_after_ttl(self, job_id: str) -> None:
        try:
            await asyncio.sleep(DOCUMENT_JOB_RESULT_TTL_SECONDS)
            async with self._lock:
                job = self._jobs.get(job_id)
                if job is not None and job.status != "running":
                    self._jobs.pop(job_id, None)
        except asyncio.CancelledError:
            raise

    def _reap_locked(self) -> None:
        cutoff = time.monotonic() - DOCUMENT_JOB_RESULT_TTL_SECONDS
        expired = [
            job_id
            for job_id, job in self._jobs.items()
            if job.status != "running" and job.finished_at and job.finished_at < cutoff
        ]
        for job_id in expired:
            self._jobs.pop(job_id, None)


__all__ = [
    "DOCUMENT_JOB_RESULT_TTL_SECONDS",
    "DOCUMENT_JOB_FINALIZE_RESERVED_SECONDS",
    "DOCUMENT_JOB_MERGE_RESERVED_SECONDS",
    "DOCUMENT_JOB_TIMEOUT_SECONDS",
    "DocumentAnalysisJobError",
    "DocumentAnalysisJobManager",
    "DocumentJobBudget",
]
