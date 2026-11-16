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

"""Background jobs and the ``job_list`` / ``job_output`` / ``job_kill`` tools.

Every shell command is a job from the moment it starts. A foreground command
that finishes in time takes its job with it and the model never sees an id; one
that outlives its timeout simply stays, now visible. Result texts and the
completion notice follow dsh ``packages/jobs/tool-jobs``.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from nekoclaw.runtime.loop.tools import AgentTool
from nekoclaw.runtime.tools.base import (
    ToolCallInfo,
    ToolContext,
    ToolError,
    define_tool,
)
from nekoclaw.runtime.tools.procs import ShellProcess

JobStatus = Literal["running", "stopping", "completed", "killed", "failed"]

MAX_RUNNING_JOBS = 16
WAIT_DEFAULT_MS = 30_000
WAIT_MAX_MS = 600_000
# Largest completion notice that carries the job's unread output inline.
NOTICE_INLINE_BYTES = 2048
_LABEL_MAX_CHARS = 120
_POLL_SECONDS = 0.1
# How long a kill is given to take effect before its caller stops waiting.
KILL_SETTLE_SECONDS = 10.0


@dataclass(slots=True)
class Job:
    id: str
    kind: str
    label: str
    process: ShellProcess
    started_at: float
    status: JobStatus = "running"
    detail: str | None = None
    finished_at: float | None = None
    # Read cursors of the model, per stream.
    stdout_offset: int = 0
    stderr_offset: int = 0
    # Live waits attached to the job: a waiting call reports the settlement itself.
    waiters: int = 0
    # Still owned by the tool call that started it; not listed, no notice.
    foreground: bool = False
    # Whoever stopped the job already knows: no completion notice follows.
    notice_suppressed: bool = False
    stop_reason: str | None = None
    settled: asyncio.Event = field(default_factory=asyncio.Event)

    @property
    def active(self) -> bool:
        return self.status in ("running", "stopping")


def bounded_label(label: str) -> str:
    """First line of a job label, shortened for one-line displays."""
    text = label.lstrip()
    newline = text.find("\n")
    if newline != -1:
        text = f"{text[:newline]}…"
    if len(text) > _LABEL_MAX_CHARS:
        text = f"{text[: _LABEL_MAX_CHARS - 1]}…"
    return text


def status_line(job: Job, extra: str | None = None) -> str:
    detail = job.detail
    if extra:
        detail = extra if detail is None else f"{detail}; {extra}"
    return f"[status: {job.status}, {detail}]" if detail is not None else f"[status: {job.status}]"


def render_delta(out: str, err: str, lossy: bool, spill_paths: list[str]) -> str:
    """Combine one read of both streams the way every job read is shown."""
    separator = "\n" if out and not out.endswith("\n") else ""
    body = out + (f"{separator}[stderr]\n{err}" if err else "")
    if not lossy:
        return body
    where = ", ".join(spill_paths) if spill_paths else "(unavailable)"
    notice = f"[some output was dropped from memory; full output: {where}]"
    gap = "\n" if body and not body.endswith("\n") else ""
    return f"{body}{gap}{notice}"


@dataclass(slots=True)
class PendingNotice:
    """A completion notice queued for the model and not yet known to be read."""

    handle: object
    # Output the notice carried inline; returned by job_output if it withdraws the notice.
    held: str | None


class JobRegistry:
    """The jobs of one session."""

    def __init__(self, on_finished: Callable[[Job], None]) -> None:
        self._on_finished = on_finished
        self._jobs: dict[str, Job] = {}
        self._watchers: set[asyncio.Task[None]] = set()
        self._counter = 0
        self._closing = False
        self.pending_notices: dict[str, PendingNotice] = {}

    # -- lifecycle ------------------------------------------------------------

    def ensure_capacity(self) -> None:
        running = sum(1 for job in self._jobs.values() if job.active)
        if running >= MAX_RUNNING_JOBS:
            raise ToolError(
                f"too many jobs are running ({running}); wait for one to finish or stop one "
                "with job_kill"
            )

    def start(self, kind: str, label: str, process: ShellProcess, *, foreground: bool) -> Job:
        self._counter += 1
        job = Job(
            id=f"job-{self._counter}",
            kind=kind,
            label=label,
            process=process,
            started_at=time.time(),
            foreground=foreground,
        )
        self._jobs[job.id] = job
        watcher = asyncio.ensure_future(self._watch(job))
        self._watchers.add(watcher)
        watcher.add_done_callback(self._watchers.discard)
        return job

    async def _watch(self, job: Job) -> None:
        await job.process.wait()
        process = job.process
        if process.kill_requested or process.signal_name is not None:
            job.status = "killed"
            name = process.signal_name
            job.detail = f"signal: {name}" if name is not None else "killed before exit"
        else:
            job.status = "completed"
            job.detail = f"exit code: {process.returncode or 0}"
        job.finished_at = time.time()
        job.settled.set()
        suppressed = (
            self._closing or job.foreground or job.waiters > 0 or job.notice_suppressed
        )
        if not suppressed and job.id in self._jobs:
            self._on_finished(job)

    def get(self, job_id: str) -> Job:
        job = self._jobs.get(job_id)
        if job is None or job.foreground:
            raise ToolError(f'unknown job "{job_id}"; use job_list to see your jobs')
        return job

    def visible(self) -> list[Job]:
        return [job for job in self._jobs.values() if not job.foreground]

    def running(self) -> list[Job]:
        return [job for job in self.visible() if job.active]

    def remove(self, job: Job) -> None:
        self._jobs.pop(job.id, None)
        self.pending_notices.pop(job.id, None)

    def stop_all(self, reason: str) -> int:
        """Stop every running job without a completion notice; returns how many."""
        stopped = 0
        for job in list(self._jobs.values()):
            if job.active:
                job.notice_suppressed = True
                self.kill(job, reason)
                stopped += 1
        return stopped

    def close(self) -> None:
        """Stop every running job; no completion notices are produced afterwards."""
        self._closing = True
        self.stop_all("session closed")

    async def wait_closed(self, timeout_seconds: float = KILL_SETTLE_SECONDS) -> None:
        pending = [job.settled.wait() for job in self._jobs.values() if not job.settled.is_set()]
        if pending:
            try:
                await asyncio.wait_for(asyncio.gather(*pending), timeout_seconds)
            except asyncio.TimeoutError:
                pass

    # -- reading and control ---------------------------------------------------

    def peek(self, job: Job) -> tuple[str, tuple[int, int]]:
        """Return the unread output and the cursors that would mark it as read."""
        out, out_next, out_lossy = job.process.stdout.read_from(job.stdout_offset)
        err, err_next, err_lossy = job.process.stderr.read_from(job.stderr_offset)
        spill = [
            str(path)
            for path in (job.process.stdout.spill_path, job.process.stderr.spill_path)
            if path is not None
        ]
        return render_delta(out, err, out_lossy or err_lossy, spill), (out_next, err_next)

    def commit(self, job: Job, cursors: tuple[int, int]) -> None:
        job.stdout_offset, job.stderr_offset = cursors

    def read(self, job: Job) -> str:
        """Return the output since the previous read and mark it as read."""
        text, cursors = self.peek(job)
        self.commit(job, cursors)
        return text

    def kill(self, job: Job, reason: str | None) -> bool:
        """Ask a job to stop. Returns False when it had already finished."""
        if not job.active:
            return False
        job.status = "stopping"
        job.stop_reason = reason
        job.process.kill()
        return True

    async def wait(
        self,
        job: Job,
        timeout_seconds: float,
        call: ToolCallInfo,
        *,
        interrupted: Callable[[], bool] | None = None,
        on_tick: Callable[[], None] | None = None,
    ) -> Literal["settled", "timeout", "aborted", "interrupted"]:
        """Wait for a job to settle; the job keeps running if the wait ends first."""
        deadline = time.monotonic() + timeout_seconds
        job.waiters += 1
        try:
            while True:
                if job.settled.is_set():
                    return "settled"
                if call.cancelled():
                    return "aborted"
                if interrupted is not None and interrupted():
                    return "interrupted"
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return "timeout"
                try:
                    await asyncio.wait_for(job.settled.wait(), min(_POLL_SECONDS, remaining))
                except asyncio.TimeoutError:
                    pass
                if on_tick is not None:
                    on_tick()
        finally:
            job.waiters -= 1


def completion_notice(registry: JobRegistry, job: Job) -> tuple[str, str | None]:
    """Build the notice for a finished job.

    Returns the text and, when the unread output fitted inline, that output
    (now marked as read). Otherwise the output stays unread for ``job_output``.
    """
    head = f"background job {job.id} ({job.kind}: {bounded_label(job.label)}) finished {status_line(job)}"
    output, cursors = registry.peek(job)
    if not output:
        registry.commit(job, cursors)
        return f"{head}. No new output.", ""
    trimmed = output[:-1] if output.endswith("\n") else output
    inline = f"{head}. Output:\n{trimmed}"
    if len(inline.encode("utf-8", errors="replace")) <= NOTICE_INLINE_BYTES:
        registry.commit(job, cursors)
        return inline, output
    return f"{head}. Read its output with job_output.", None


def build_job_tools(ctx: ToolContext, schemas: dict[str, dict[str, Any]]) -> list[AgentTool]:
    registry = ctx.jobs

    def job_id_of(args: dict[str, Any]) -> str:
        job_id = str(args["job_id"]).strip()
        if not job_id:
            raise ToolError("job_id must be a non-empty string")
        return job_id

    async def job_output(args: dict[str, Any], call: ToolCallInfo) -> str:
        job = registry.get(job_id_of(args))
        released: str | None = None
        if args.get("wait") is True and job.active:
            requested = float(args.get("timeout_ms", WAIT_DEFAULT_MS))
            if requested <= 0:
                raise ToolError("timeout_ms must be a positive number")
            outcome = await registry.wait(
                job,
                min(requested, WAIT_MAX_MS) / 1000,
                call,
                interrupted=ctx.inbox.has_pending_user_steer,
            )
            if outcome == "aborted":
                raise ToolError("wait aborted")
            if outcome == "interrupted":
                released = "user steered"
        body = registry.read(job)
        notice = registry.pending_notices.pop(job.id, None)
        if notice is not None and ctx.inbox.withdraw_notice(notice.handle):
            # This result covers everything the withdrawn notice would have said.
            body = f"{notice.held or ''}{body}"
        if not body:
            body = "(no new output)"
        gap = "" if body.endswith("\n") else "\n"
        return f"{body}{gap}{status_line(job, released if job.active else None)}"

    async def job_list(args: dict[str, Any], call: ToolCallInfo) -> str:
        del args, call
        jobs = registry.visible()
        if not jobs:
            return "(no background jobs)"
        return "\n".join(
            f"{job.id} [{job.kind}] {job.status} — {bounded_label(job.label)}" for job in jobs
        )

    async def job_kill(args: dict[str, Any], call: ToolCallInfo) -> str:
        del call
        job = registry.get(job_id_of(args))
        reason = args.get("reason")
        if job.active:
            # The model's own kill is its delivery of the outcome.
            job.notice_suppressed = True
            registry.kill(job, str(reason) if reason else None)
            return f"requested cancellation of job {job.id}"
        return f"job {job.id} had already finished {status_line(job)}"

    return [
        define_tool(schemas["job_list"], job_list),
        define_tool(schemas["job_output"], job_output),
        define_tool(schemas["job_kill"], job_kill),
    ]
