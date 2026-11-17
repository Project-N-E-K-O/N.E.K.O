"""Stable extraction, exclusive publication and evidence-based crash recovery."""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import threading
from contextlib import contextmanager
from pathlib import Path
from weakref import WeakValueDictionary

from .bundle import BundleError, inspect_bundle
from .formats import validate_model_bundle
from .paths import (
    WorkshopError,
    identity,
    publish_directory,
    regular,
    sync_directory,
    write_all,
    write_json,
)
from .registry import JOURNAL_SCHEMA, STATE_BYTES, Registry, make_record, task_id


class Installer:
    """Internal synchronous operations; the owning Main service schedules the worker.

    Lock order is root OS ownership, package lifecycle, registry, storage fence.
    Callers never enter/leave a synchronous storage transaction across await.
    """

    def __init__(self, config, paths, assert_owner):
        self.config = config
        self.paths = paths
        self.registry = Registry(paths)
        self._assert_owner = assert_owner
        self._locks = WeakValueDictionary()
        self._locks_guard = threading.Lock()

    @contextmanager
    def lifecycle(self, digest):
        # A strong local reference retains the lock while any thread waits/works;
        # deleted models do not leave an ever-growing process-wide lock table.
        with self._locks_guard:
            lock = self._locks.get(digest)
            if lock is None:
                lock = threading.RLock()
                self._locks[digest] = lock
        with lock:
            yield

    @contextmanager
    def mutation(self):
        self._assert_owner()
        with self.paths.transaction(self.config):
            self._assert_owner()
            yield

    def journal_path(self, attempt):
        return self.paths.checked(
            "model_workshop/transactions/" + task_id(attempt) + ".json"
        )

    def stage(self, attempt):
        return self.paths.checked("model_workshop/staging/" + task_id(attempt))

    def begin(self, attempt):
        """Persist private receive ownership before copying request-scoped input."""
        with self.mutation():
            stage = self.stage(attempt)
            stage.mkdir(mode=0o700)  # Exclusive server-generated name.
            sync_directory(stage.parent)
            journal = dict(
                schema=JOURNAL_SCHEMA,
                task_id=attempt,
                phase="receiving",
                stage_identity=identity(stage),
                record=None,
                object_identity=None,
            )
            self.save_journal(journal)
            return journal

    def save_journal(self, journal):
        write_json(self.journal_path(journal["task_id"]), journal, STATE_BYTES)

    def phase(self, journal, value):
        with self.mutation():
            journal["phase"] = value
            self.save_journal(journal)

    def cleanup(self, journal):
        """Only this journal's private stage is removable; published models are never guessed away."""
        stage = self.stage(journal["task_id"])
        if os.path.lexists(stage):
            if identity(stage) != journal["stage_identity"]:
                raise WorkshopError("model_workshop_recovery_conflict")
            # Refuse injected links/special files before recursive cleanup. On
            # POSIX shutil additionally uses its descriptor-based safe rmtree.
            for parent, dirs, files in os.walk(stage, followlinks=False):
                for name in dirs + files:
                    identity(Path(parent) / name)
            shutil.rmtree(stage)
            sync_directory(stage.parent)
        self.journal_path(journal["task_id"]).unlink(missing_ok=True)
        sync_directory(self.journal_path(journal["task_id"]).parent)

    @staticmethod
    def cancelled(event):
        if event.is_set():
            # Inspectors preserve BundleError codes; generic consumer ValueError
            # would be normalized to invalid_model_bundle by the stable context.
            raise BundleError("model_install_cancelled")

    def _extract(self, journal, admitted, event):
        """Reopen only to revalidate the full identity; publish after the stable context exits."""
        stage = self.stage(journal["task_id"])
        payload = stage / "payload"
        payload.mkdir(mode=0o700)
        record = journal["record"]
        target = record["target"]
        with inspect_bundle(stage / "input.neko-model.zip") as (bundle, archive):
            if (bundle.sha256, bundle.byte_size) != (
                admitted.sha256,
                admitted.byte_size,
            ):
                raise BundleError("model_bundle_changed")
            try:
                for item in record["files"]:
                    self.cancelled(event)
                    relative = (
                        "avatar.vrm"
                        if record["model_format"] == "vrm"
                        else item["local_path"][len(target) + 1 :]
                    )
                    output = payload / relative
                    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    digest = hashlib.sha256()
                    total = 0
                    with (
                        archive.open("model/" + item["path"]) as source,
                        regular(
                            output, os.O_RDWR | os.O_CREAT | os.O_EXCL
                        ) as destination,
                    ):
                        while chunk := source.read(1024 * 1024):
                            self.cancelled(event)
                            total += len(chunk)
                            if total > item["byte_size"]:
                                raise BundleError("bundle_file_hash_mismatch")
                            write_all(destination, chunk)
                            digest.update(chunk)
                        destination.flush()
                        os.fsync(destination.fileno())
                    if (
                        total != item["byte_size"]
                        or digest.hexdigest() != item["sha256"]
                    ):
                        raise BundleError("bundle_file_hash_mismatch")
            except OSError as exc:
                # Preserve consumer disk failures across the read-only archive
                # inspector's generic input-error normalization.
                raise BundleError("model_install_io_error") from exc
        for parent, _, _ in os.walk(payload, topdown=False):
            sync_directory(Path(parent))
        sync_directory(stage)
        return payload / "avatar.vrm" if record["model_format"] == "vrm" else payload

    def verify_resources(self, record, target: Path):
        """Require exact initial bytes/inventory only before first completed registration."""
        expected = set()
        for item in record["files"]:
            path = self.paths.checked(item["local_path"])
            expected.add(path)
            size = 0
            digest = hashlib.sha256()
            try:
                source = regular(path)
            except FileNotFoundError as exc:
                raise WorkshopError("model_workshop_recovery_conflict") from exc
            with source:
                before = os.fstat(source.fileno())
                while chunk := source.read(1024 * 1024):
                    size += len(chunk)
                    if size > item["byte_size"]:
                        raise WorkshopError("model_workshop_recovery_conflict")
                    digest.update(chunk)
                after = os.fstat(source.fileno())
            if (size, digest.hexdigest()) != (item["byte_size"], item["sha256"]) or (
                before.st_dev,
                before.st_ino,
                before.st_mtime_ns,
                before.st_ctime_ns,
            ) != (after.st_dev, after.st_ino, after.st_mtime_ns, after.st_ctime_ns):
                raise WorkshopError("model_workshop_recovery_conflict")
            if identity(path)[:2] != [after.st_dev, after.st_ino]:
                raise WorkshopError("model_workshop_recovery_conflict")
        if record["model_format"] != "vrm":
            actual = set()
            allowed_dirs = {target}
            for path in expected:
                allowed_dirs.update(
                    parent
                    for parent in path.parents
                    if parent == target or target in parent.parents
                )
            for parent, dirs, files in os.walk(target, followlinks=False):
                for name in dirs:
                    path = Path(parent) / name
                    if identity(path)[2] != stat.S_IFDIR or path not in allowed_dirs:
                        raise WorkshopError("model_workshop_recovery_conflict")
                for name in files:
                    path = Path(parent) / name
                    if identity(path)[2] != stat.S_IFREG:
                        raise WorkshopError("model_workshop_recovery_conflict")
                    actual.add(path)
            if actual != expected:
                raise WorkshopError("model_workshop_recovery_conflict")

    def _publish(self, source, target, record):
        if record["model_format"] == "vrm":
            from utils.file_utils import publish_without_replacing

            publish_without_replacing(source, target)
        else:
            publish_directory(source, target)
        sync_directory(target.parent)
        sync_directory(source.parent)

    def run(self, journal, event, progress, commit_gate):
        """Prepare outside metadata locks; cancellation stops only before commit ownership."""
        self.phase(journal, "validating")
        progress("validating")
        self.cancelled(event)
        bundle = validate_model_bundle(
            self.stage(journal["task_id"]) / "input.neko-model.zip"
        )
        self.cancelled(event)
        journal["record"] = make_record(bundle, journal["task_id"])
        self.phase(journal, "staging")
        progress("staging")
        source = self._extract(journal, bundle, event)
        record = journal["record"]
        with self.lifecycle(bundle.sha256), self.registry.lock, self.mutation():
            state = (
                self.registry.read()
            )  # Never merge a snapshot captured before this lock.
            rows, attempts = dict(state["installed"]), dict(state["attempts"])
            self.cancelled(event)
            previous = rows.get(bundle.sha256)
            if (
                previous
                and attempts.get(bundle.sha256) == previous["install_id"]
                and self.registry.status(previous) == "installed"
            ):
                self.cleanup(journal)
                return dict(previous, status="installed", already_installed=True)
            # An unresolved older publication must be recovered, never replaced by a retry.
            for other in self.registry.journals():
                if (
                    other["task_id"] != journal["task_id"]
                    and other["phase"] == "committing"
                    and (other["record"]["package_sha256"] == bundle.sha256)
                ):
                    raise WorkshopError("model_workshop_recovery_conflict")
            target = self.paths.checked(record["target"])
            if os.path.lexists(target):
                raise WorkshopError("model_workshop_install_conflict")
            self.paths.mkdir(target.parent.relative_to(self.paths.root).as_posix())
            if source.stat().st_dev != target.parent.stat().st_dev:
                raise WorkshopError("model_workshop_publication_unsupported")
            rows[bundle.sha256] = record
            attempts[bundle.sha256] = journal["task_id"]
            self.registry.check_capacity(rows, attempts)
            self.cancelled(event)
            commit_gate()  # Atomically wins/loses against a concurrent cancel request.
            # Persist the current logical attempt before recording publication intent.
            # An older completed row must not claim this reinstall if its commit fails.
            self.registry.save(state["installed"], attempts)
            journal["phase"] = "committing"
            journal["object_identity"] = identity(source)
            self.save_journal(
                journal
            )  # Durable identity evidence precedes any visible resource.
            self._publish(source, target, record)
            if identity(target) != journal["object_identity"]:
                raise WorkshopError("model_workshop_recovery_conflict")
            self.verify_resources(record, target)
            # Hash equality alone cannot claim a copy swapped in during the
            # scan: retain the publication identity through the final check.
            if identity(target) != journal["object_identity"]:
                raise WorkshopError("model_workshop_recovery_conflict")
            self.registry.save(rows, attempts)
            # Once completed, residual logs cannot revoke later local edits.
            try:
                self.cleanup(journal)
            except OSError:
                pass
            return dict(record, status="installed", already_installed=False)

    def recover(self, active=frozenset()):
        """Only the root owner recovers; each conflict preserves its log and target."""
        self._assert_owner()
        directory = self.paths.checked("model_workshop/transactions")
        names = list(directory.glob("*.json"))
        if len(names) > 1024:
            raise WorkshopError("model_workshop_state_unreadable")
        results = {}
        from .paths import read_json
        from .registry import validate_journal

        for path in names:
            if path.stem in active:
                continue  # Includes receiving/accepted-but-not-yet-enqueued reservations.
            try:
                journal = validate_journal(
                    read_json(
                        self.paths.checked("model_workshop/transactions/" + path.name),
                        STATE_BYTES,
                    ),
                    path.name,
                )
                record = journal["record"]
                digest = record["package_sha256"] if record else journal["task_id"]
                with self.lifecycle(digest), self.registry.lock, self.mutation():
                    state = (
                        self.registry.read()
                    )  # An unreadable registry never becomes empty.
                    rows, attempts = state["installed"], state["attempts"]
                    completed = rows.get(record["package_sha256"]) if record else None
                    current = attempts.get(record["package_sha256"]) if record else None
                    if completed and completed["install_id"] == journal["task_id"]:
                        # Same attempt preserves local edits; a newer attempt
                        # supersedes the old log without touching current assets.
                        self.cleanup(journal)
                        results[path.stem] = (
                            "completed"
                            if completed["install_id"] == path.stem
                            else "superseded"
                        )
                    elif (
                        record and current is not None and current != journal["task_id"]
                    ):
                        self.cleanup(journal)
                        results[path.stem] = "superseded"
                    elif journal["phase"] != "committing":
                        self.cleanup(journal)
                        results[path.stem] = "interrupted"
                    else:
                        target = self.paths.checked(record["target"])
                        if not os.path.lexists(target):
                            self.cleanup(journal)
                            results[path.stem] = "interrupted"
                            continue
                        if identity(target) != journal["object_identity"]:
                            raise WorkshopError("model_workshop_recovery_conflict")
                        self.verify_resources(record, target)
                        if identity(target) != journal["object_identity"]:
                            raise WorkshopError("model_workshop_recovery_conflict")
                        rows[record["package_sha256"]] = record
                        if current != journal["task_id"]:
                            raise WorkshopError("model_workshop_recovery_conflict")
                        self.registry.save(rows, attempts)
                        self.cleanup(journal)
                        results[path.stem] = "completed"
            except (OSError, WorkshopError) as exc:
                results[path.stem] = (
                    str(exc)
                    if isinstance(exc, WorkshopError)
                    else "model_workshop_io_error"
                )
        return results
