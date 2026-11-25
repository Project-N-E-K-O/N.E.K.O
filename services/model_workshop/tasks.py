"""One Main owner per runtime root, bounded accepted tasks and explicit cancellation."""

from __future__ import annotations

import asyncio
import os
import threading
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import portalocker

from .bundle import BundleError, BundleLimits
from .installer import Installer
from .paths import WorkshopError, WorkshopPaths, identity, regular, write_all

TERMINAL = frozenset(("completed", "failed", "cancelled"))
MAX_ACTIVE = 2
MAX_RECENT = 64


@dataclass
class _Task:
    task_id: str
    phase: str = "receiving"
    cancel: threading.Event = field(default_factory=threading.Event)
    done: threading.Event = field(default_factory=threading.Event)
    result: dict | None = None
    error: str | None = None


async def _drain_sync(call):
    """A cancelled awaiter still waits for the synchronous owner to finish its I/O."""
    future = asyncio.create_task(asyncio.to_thread(call))
    cancelled = False
    while True:
        try:
            result = await asyncio.shield(future)
            break
        except asyncio.CancelledError:
            cancelled = True
            if future.done():
                break
        except BaseException:
            if cancelled:
                raise asyncio.CancelledError from None
            raise
    if cancelled:
        # Retrieve any worker exception so cancellation cannot leak an unobserved task.
        if future.done() and not future.cancelled():
            future.exception()
        raise asyncio.CancelledError
    return result


class WorkshopTasks:
    """Blocking service API intended for Main's worker threads, never an import-time singleton.

    start() must follow the existing storage startup gate. Ownership is held until
    close() has drained receivers and workers. No HTTP, renderer or character API
    is registered here; N3 must wire read isolation/deletion before exposing this.
    """

    def __init__(self, config):
        self.config = config
        self.paths = WorkshopPaths.capture(config)
        self._condition = threading.Condition(threading.RLock())
        # Recovery and reservation/log creation share this gate. A snapshot of
        # active tasks alone cannot exclude a receiver accepted just afterward.
        self._admission = threading.RLock()
        self._tasks = OrderedDict()
        self._receivers = 0
        self._writer = None
        self._executor = None
        self._closing = False
        self.installer = Installer(config, self.paths, self._assert_owner)
        self.recovery = {}

    def _assert_owner(self):
        if self._writer is None or self._writer.closed:
            raise WorkshopError("model_workshop_not_started")
        info = os.fstat(self._writer.fileno())
        if info.st_nlink != 1 or identity(
            self.paths.checked("model_workshop/locks/writer.lock")
        )[:2] != [info.st_dev, info.st_ino]:
            raise WorkshopError("model_workshop_identity_unavailable")

    def start(self):
        """Acquire a safe OS lock lazily; neither PID nor a lock file's existence proves ownership."""
        with self._condition:
            if self._writer is not None:
                self._assert_owner()
                return
            if self._closing:
                raise WorkshopError("model_workshop_closed")
            with self.paths.transaction(self.config):
                for directory in (
                    "model_workshop/locks",
                    "model_workshop/staging",
                    "model_workshop/transactions",
                ):
                    self.paths.mkdir(directory)
                path = self.paths.checked("model_workshop/locks/writer.lock")
                try:
                    handle = regular(path, os.O_RDWR | os.O_CREAT | os.O_EXCL)
                except FileExistsError:
                    handle = regular(path, os.O_RDWR)
                try:
                    if os.fstat(handle.fileno()).st_nlink != 1:
                        raise WorkshopError("model_workshop_unsafe_path")
                    portalocker.lock(handle, portalocker.LOCK_EX | portalocker.LOCK_NB)
                except BaseException as exc:
                    handle.close()
                    if isinstance(exc, portalocker.exceptions.AlreadyLocked):
                        raise WorkshopError("model_workshop_busy") from exc
                    raise
                self._writer = handle
            try:
                self.recovery = self.installer.recover()
                # An unreadable registry blocks mutation, even with no pending journals.
                self.installer.registry.load()
                self._executor = ThreadPoolExecutor(
                    max_workers=1, thread_name_prefix="model-workshop"
                )
            except BaseException:
                portalocker.unlock(self._writer)
                self._writer.close()
                self._writer = None
                raise

    def _task(self, attempt):
        task = self._tasks.get(attempt)
        if task is None:
            raise WorkshopError("model_workshop_task_not_found")
        return task

    def status(self, attempt):
        with self._condition:
            return self._snapshot(self._task(attempt))

    @staticmethod
    def _snapshot(task):
        return dict(task_id=task.task_id, phase=task.phase, result=task.result, error=task.error)

    def _progress(self, task, value):
        with self._condition:
            task.phase = value

    def _commit_gate(self, task):
        # The same mutex linearizes cancel versus commit. Once committed to
        # publication, cancellation waits and reports the real terminal result.
        with self._condition:
            self.installer.cancelled(task.cancel)
            task.phase = "committing"

    def receive(self, source):
        """Copy a bounded synchronous stream before returning its durable accepted task ID."""
        journal = None
        with self._admission:
            with self._condition:
                self._assert_owner()
                if self._closing or self._executor is None:
                    raise WorkshopError("model_workshop_closed")
                if (
                    sum(task.phase not in TERMINAL for task in self._tasks.values())
                    >= MAX_ACTIVE
                ):
                    raise WorkshopError("model_workshop_busy")
                while len(self._tasks) >= MAX_RECENT:
                    expired = next(
                        (
                            key
                            for key, task in self._tasks.items()
                            if task.done.is_set()
                        ),
                        None,
                    )
                    if expired is None:
                        raise WorkshopError("model_workshop_busy")
                    del self._tasks[expired]
                task = _Task(uuid.uuid4().hex)
                self._tasks[task.task_id] = task
                self._receivers += (
                    1  # close() cannot release root ownership during this copy.
                )
            try:
                # Reject unreadable ownership metadata before consuming an
                # upload or creating another private receive transaction.
                with self.installer.registry.lock:
                    self.installer.registry.read()
                journal = self.installer.begin(task.task_id)
            except BaseException:
                with self._condition:
                    self._receivers -= 1
                    task.phase = "failed"
                    task.done.set()
                    self._condition.notify_all()
                raise
        try:
            path = self.installer.stage(task.task_id) / "input.neko-model.zip"
            size = 0
            with regular(path, os.O_RDWR | os.O_CREAT | os.O_EXCL) as output:
                while chunk := source.read(1024 * 1024):
                    self.installer.cancelled(task.cancel)
                    if not isinstance(chunk, bytes) or len(chunk) > 1024 * 1024:
                        raise WorkshopError("invalid_model_bundle")
                    size += len(chunk)
                    if size > BundleLimits().archive_bytes:
                        raise WorkshopError("model_bundle_too_large")
                    write_all(output, chunk)
                output.flush()
                os.fsync(output.fileno())
            if not size:
                raise WorkshopError("invalid_model_bundle")
            self.installer.cancelled(task.cancel)
            self.installer.phase(journal, "queued")
            with self._condition:
                self.installer.cancelled(task.cancel)
                task.phase = "queued"
                self._executor.submit(self._work, task, journal)
            return task.task_id
        except BaseException:
            if journal is not None:
                try:
                    with self.installer.mutation():
                        self.installer.cleanup_private_stage(journal)
                except (OSError, WorkshopError):
                    pass  # Keep recovery evidence; never replace the original receive failure.
            with self._condition:
                task.phase = "cancelled" if task.cancel.is_set() else "failed"
                task.error = (
                    "model_install_cancelled"
                    if task.cancel.is_set()
                    else "model_install_receive_failed"
                )
                task.done.set()
            raise
        finally:
            with self._condition:
                self._receivers -= 1
                self._condition.notify_all()

    def _work(self, task, journal):
        try:
            result = self.installer.run(
                journal,
                task.cancel,
                lambda phase: self._progress(task, phase),
                lambda: self._commit_gate(task),
            )
            with self._condition:
                task.result = result
                task.phase = "completed"
        except Exception as exc:
            # Only unpublished attempts can be discarded. A visible target or
            # uncertain registry keeps its journal for explicit recovery.
            if journal["phase"] != "committing":
                try:
                    with self.installer.mutation():
                        self.installer.cleanup_private_stage(journal)
                except (OSError, WorkshopError):
                    pass  # Keep the original task failure and its recovery evidence.
            with self._condition:
                task.error = (
                    str(exc)
                    if isinstance(exc, (WorkshopError, BundleError))
                    else "model_install_io_error"
                )
                task.phase = (
                    "cancelled" if task.error == "model_install_cancelled" else "failed"
                )
        finally:
            task.done.set()

    def wait(self, attempt, timeout=None):
        """Used by shutdown/tests, not by task polling HTTP handlers."""
        with self._condition:
            task = self._task(attempt)
        return self._wait(task, timeout)

    def _wait(self, task, timeout=None):
        if not task.done.wait(timeout):
            raise TimeoutError("model_workshop_task_pending")
        with self._condition:
            return self._snapshot(task)

    def cancel(self, attempt):
        with self._condition:
            task = self._task(attempt)
            if task.phase not in TERMINAL and task.phase != "committing":
                task.cancel.set()
        return self._wait(task)

    def recover(self):
        with self._admission:
            with self._condition:
                if self._closing:
                    raise WorkshopError("model_workshop_closed")
                active = frozenset(
                    key for key, task in self._tasks.items() if not task.done.is_set()
                )
            return self.installer.recover(active)

    def close(self):
        """Wait for actual receivers/workers, then unlock; retained lock inodes are never unlinked."""
        with self._admission:
            self._close_owned()

    def _close_owned(self):
        """Admission is closed while recovery, receivers and workers finish."""
        with self._condition:
            self._closing = True
            for task in self._tasks.values():
                if task.phase not in TERMINAL and task.phase != "committing":
                    task.cancel.set()
            while self._receivers:
                self._condition.wait()
            executor = self._executor
        if executor is not None:
            executor.shutdown(wait=True)
        with self._condition:
            self._executor = None
            if self._writer is not None:
                portalocker.unlock(self._writer)
                self._writer.close()
                self._writer = None

    async def acancel(self, attempt):
        return await _drain_sync(lambda: self.cancel(attempt))

    async def aclose(self):
        return await _drain_sync(self.close)
