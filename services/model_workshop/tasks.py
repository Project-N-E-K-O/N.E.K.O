"""One Main owner per runtime root, bounded accepted tasks and explicit cancellation."""

from __future__ import annotations

import asyncio
import os
import threading
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from itertools import islice

import anyio
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
    try:
        return await asyncio.shield(future)
    except asyncio.CancelledError:
        # AnyIO reissues cancellation at every checkpoint outside a shield.
        # Keep waiting for the real thread without busy-spinning on disconnect.
        with anyio.CancelScope(shield=True):
            while not future.done():
                try:
                    await asyncio.wait({future})
                except asyncio.CancelledError:
                    continue
        # Retrieve any worker exception so cancellation cannot leak an unobserved task.
        if not future.cancelled():
            future.exception()
        raise


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
        self._uploads = 0
        self._operations = 0
        self._writer = None
        self._executor = None
        self._closing = False
        self.installer = Installer(config, self.paths, self._assert_owner)
        self.recovery = {}
        self._recovery_version = None

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
                self._recovery_version = self._recovery_metadata_version()
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
            task = self._task(attempt)
            return dict(
                task_id=task.task_id,
                phase=task.phase,
                result=task.result,
                error=task.error,
            )

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
                    pass
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
            event = self._task(attempt).done
        if not event.wait(timeout):
            raise TimeoutError("model_workshop_task_pending")
        return self.status(attempt)

    def cancel(self, attempt):
        with self._condition:
            task = self._task(attempt)
            if task.phase not in TERMINAL and task.phase != "committing":
                task.cancel.set()
        return self.wait(attempt)

    def begin_upload(self):
        """Reserve an HTTP parser without retaining a thread lock across await."""
        with self._admission, self._condition:
            if self._closing:
                raise WorkshopError("model_workshop_closed")
            self._assert_owner()
            if self._uploads >= MAX_ACTIVE:
                raise WorkshopError("model_workshop_busy")
            self._uploads += 1

    def finish_upload(self):
        """Release even after shutdown; the HTTP owner drains and closes its spool."""
        with self._condition:
            self._uploads -= 1

    @contextmanager
    def activity(self):
        """Retain ownership through legacy mutations/recovery, including their cleanup."""
        with self._admission:
            with self._condition:
                if self._closing:
                    raise WorkshopError("model_workshop_closed")
                self._assert_owner()
                self._operations += 1
            try:
                yield
            finally:
                with self._condition:
                    self._operations -= 1

    @contextmanager
    def migration_idle(self):
        """Freeze admissions only if immediately idle; never wait on a storage writer."""
        if not self._admission.acquire(blocking=False):
            raise WorkshopError("model_workshop_busy")
        try:
            if not self._condition.acquire(blocking=False):
                raise WorkshopError("model_workshop_busy")
            try:
                if self._closing or self._operations or self._receivers or self._uploads or any(
                    not task.done.is_set() for task in self._tasks.values()
                ):
                    raise WorkshopError("model_workshop_busy")
                self._assert_owner()
            finally:
                self._condition.release()
            # Keep admission frozen until the caller finishes its checkpoint
            # transaction (including rollback), so an old reference cannot write.
            yield
        finally:
            self._admission.release()

    def recover(self):
        with self.activity():
            with self._condition:
                if self._closing:
                    raise WorkshopError("model_workshop_closed")
                active = frozenset(
                    key for key, task in self._tasks.items() if not task.done.is_set()
                )
            self.recovery = self.installer.recover(active)
            metadata, tasks = self._recovery_metadata_version()
            # Recovery skipped these tasks. If one settled during the scan,
            # retain the skipped state so the next maintenance retries its log.
            self._recovery_version = (
                metadata, tuple((key, False if key in active else done) for key, done in tasks)
            )
            return self.recovery

    def _recovery_metadata_version(self):
        """A bounded metadata witness, never a hash of model resources per request."""
        from .registry import MAX_INSTALLED

        directory = self.paths.checked("model_workshop/transactions")
        logs = list(islice(directory.glob("*.json"), MAX_INSTALLED + 1))
        if len(logs) > MAX_INSTALLED:
            raise WorkshopError("model_workshop_state_unreadable")
        tokens = []
        for path in sorted(logs) + [self.paths.checked("model_workshop/installed.json")]:
            try:
                info = path.lstat()
            except FileNotFoundError:
                tokens.append((path.name, None))
            else:
                tokens.append((path.name, info.st_dev, info.st_ino, info.st_size,
                               info.st_mtime_ns, info.st_ctime_ns))
        with self._condition:
            # A skipped active journal must be retried if its worker later
            # fails without changing that journal. File mtimes alone miss this.
            tasks = tuple((key, task.done.is_set()) for key, task in self._tasks.items())
        return tuple(tokens), tasks

    def recover_if_changed(self):
        """Retry changed state once; unchanged conflicts remain visible but are not rehashed."""
        with self.activity():
            if self._closing:
                raise WorkshopError("model_workshop_closed")
            if self._recovery_metadata_version() != self._recovery_version:
                return self.recover()
            return self.recovery

    def close(self):
        """Wait for actual receivers/workers, then unlock; retained lock inodes are never unlinked."""
        with self._admission:
            self._close_owned()

    def _close_owned(self):
        """Admission is closed while recovery, receivers and workers finish."""
        with self._condition:
            self._closing = True
            # HTTP parsers own only temporary spools; late receive() calls are
            # rejected below. An unfinished client body must not retain root
            # ownership indefinitely during shutdown. Migration rejects it
            # earlier through _uploads instead of closing an active parser.
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
