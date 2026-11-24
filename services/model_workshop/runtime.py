"""Main-owned lifecycle coordination and read-only metadata for other consumers."""

from __future__ import annotations

import os
import shutil
import stat
import threading
from itertools import islice
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote, urlsplit

import portalocker

from .paths import (
    WorkshopError,
    WorkshopPaths,
    identity,
    read_json,
    regular,
    sync_directory,
)
from .registry import MAX_INSTALLED, STATE_BYTES, Registry, validate_journal
from .tasks import WorkshopTasks

_gate = threading.RLock()
_services: dict[str, WorkshopTasks] = {}
_stopped = False
_UNCAPTURED = object()


@dataclass(frozen=True)
class Snapshot:
    """A read result, not an authorization token for a later pathname reopen."""

    paths: WorkshopPaths | None
    records: tuple[dict, ...] = ()
    blocked: frozenset[str] = frozenset()
    errors: tuple[str, ...] = ()
    pending: bool = False

    def matches(self, path, target) -> bool:
        relative = self.relative(path)
        if relative is None:
            return False
        if relative == target or relative.startswith(target + "/"):
            return True
        # macOS and Windows may resolve a differently-cased spelling to the
        # same object. Identity checks retain case-sensitive Linux semantics.
        parts = Path(relative).parts
        depth = len(Path(target).parts)
        if len(parts) < depth:
            return False
        # Owned target names are ASCII hashes. Avoid per-record filesystem
        # probes for unrelated ordinary models while retaining case aliases.
        if Path(*parts[:depth]).as_posix().casefold() != target.casefold():
            return False
        try:
            return identity(self.paths.root.joinpath(*parts[:depth])) == identity(
                self.paths.root / target
            )
        except (OSError, WorkshopError):
            return False

    def relative(self, path) -> str | None:
        if self.paths is None:
            return None
        candidate = Path(os.path.abspath(path))
        # Rebase only the configured root alias. Resolving the entire candidate
        # would follow an injected link inside a model before its safety check.
        for root in (self.paths.root, self.paths.root_alias):
            if root is None:
                continue
            try:
                relative = candidate.relative_to(root)
            except ValueError:
                continue
            if root != self.paths.root and root.resolve() != self.paths.root:
                raise WorkshopError("model_workshop_root_changed")
            return relative.as_posix()
        return None

    def record_for(self, path) -> dict | None:
        relative = self.relative(path)
        if relative is None:
            return None
        return next(
            (row for row in self.records if self.matches(path, row["target"])), None
        )

    def require(self, path) -> None:
        relative = self.relative(path)
        if relative is not None and any(
            self.matches(path, name) for name in self.blocked
        ):
            raise WorkshopError("model_workshop_unavailable")

    def identifier(self, kind: str, value: str) -> dict | None:
        """Resolve only trusted entry/root/stem identities, never a filename prefix."""
        if not isinstance(value, str):
            return None
        is_url = value.startswith("/") or "://" in value
        try:
            local = local_path(self.paths, value) if self.paths and is_url else None
        except ValueError:
            local = None
        for row in self.records:
            if row["model_format"] != kind:
                continue
            if local is not None and local in (
                self.paths.root / row["target"],
                self.paths.root / row["local_entrypoint"],
            ):
                return row
            expected = (
                Path(row["target"]).name
                if kind == "live2d"
                else Path(row["local_entrypoint"]).stem
            )
            if value == expected:
                return row
        return None


def local_path(
    paths: WorkshopPaths, value: str, *, readable_live2d=None, url_encoded=True
) -> Path | None:
    """Map existing local URL prefixes (including CFA); decode exactly once."""
    parsed = urlsplit(value)
    if (parsed.scheme or parsed.netloc) and parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
        return None
    raw = (unquote(parsed.path, errors="strict") if url_encoded else value).replace("\\", "/")
    for prefix, directory in (
        ("/user_live2d/", readable_live2d or paths.live2d),
        ("/user_live2d_local/", paths.live2d),
        ("/user_vrm/", paths.vrm),
        ("/user_mmd/", paths.mmd),
    ):
        if raw.startswith(prefix):
            candidate = Path(
                os.path.normpath(directory / raw[len(prefix) :])
            ).absolute()
            return candidate if candidate.is_relative_to(directory) else None
    # Live2D bindings also retain the historical root-relative form.
    if not parsed.scheme and not raw.startswith("/"):
        candidate = Path(os.path.normpath(paths.live2d / raw)).absolute()
        if candidate.is_relative_to(paths.live2d):
            return candidate
    return None


def snapshot(config=None, *, paths=None) -> Snapshot:
    """Read bounded state without creating directories, acquiring a writer or recovering."""
    if paths is None:
        root = getattr(config, "app_docs_dir", None)
        if root is None or not os.path.lexists(Path(root) / "model_workshop"):
            return Snapshot(None)
        paths = WorkshopPaths.capture(config)
    if not os.path.lexists(paths.private):
        return Snapshot(paths)
    registry = Registry(paths)
    errors = []

    def read_state():
        try:
            return registry.read()
        except (OSError, WorkshopError):
            errors.append("model_workshop_state_unreadable")
            return dict(installed={}, attempts={})

    for _ in range(3):
        # A later stable read supersedes transient rename/removal failures.
        errors.clear()
        before = read_state()
        journals = []
        try:
            directory = paths.checked("model_workshop/transactions")
            names = list(islice(directory.glob("*.json"), MAX_INSTALLED + 1))
            if len(names) > MAX_INSTALLED:
                raise WorkshopError("model_workshop_state_unreadable")
            for path in names:
                try:
                    data = read_json(
                        paths.checked("model_workshop/transactions/" + path.name),
                        STATE_BYTES,
                    )
                    if data is not None:
                        journals.append(validate_journal(data, path.name))
                except (OSError, WorkshopError):
                    errors.append("model_workshop_state_unreadable")
        except (OSError, WorkshopError):
            errors.append("model_workshop_state_unreadable")
        after = read_state()
        if before == after and not errors:
            break
    else:
        errors.append("model_workshop_state_busy")
    rows, attempts = after["installed"], after["attempts"]
    records = dict(rows)
    blocked = set()
    pending = False
    for journal in journals:
        row = journal["record"]
        completed = rows.get(row["package_sha256"]) if row else None
        if completed and completed["install_id"] == journal["task_id"]:
            continue
        if row and attempts.get(row["package_sha256"]) not in (
            None,
            journal["task_id"],
        ):
            continue  # A newer logical attempt supersedes this old journal.
        pending = True
        if row and journal["phase"] == "committing":
            records[row["package_sha256"]] = row
            blocked.add(row["target"])
    return Snapshot(
        paths, tuple(records.values()), frozenset(blocked), tuple(set(errors)), pending
    )


def get_service(config) -> WorkshopTasks:
    """Only Main calls this, after its startup gate. Monitor uses snapshot only."""
    paths = WorkshopPaths.capture(config)
    key = str(paths.root)
    with _gate:
        if _stopped:
            raise WorkshopError("model_workshop_closed")
        service = _services.get(key)
        if service is None:
            service = WorkshopTasks(config)
            service.start()
            _services[key] = service
        elif service.paths != paths:
            raise WorkshopError("model_workshop_root_changed")
        return service


def snapshot_for_mutation(config) -> Snapshot:
    """Unknown ownership must never authorize the ordinary write/delete fallback."""
    state = snapshot(config)
    if state.errors:
        raise WorkshopError("model_workshop_unavailable")
    return state


@contextmanager
def _migration_scope(source, target, *, quiesce):
    """Keep Main admissions frozen through one storage transaction, never recover."""
    from utils.model_workshop_storage import (
        ModelWorkshopStorageError,
        assert_model_workshop_stopped,
        inspect_model_workshop_metadata,
    )
    from utils.storage.migration import StorageMigrationError, require_paired_model_workshop_roots

    # get_service starts under _gate before taking the storage fence. A
    # nonblocking acquire avoids reversing that order during final rechecks.
    if not _gate.acquire(blocking=False):
        raise WorkshopError("model_workshop_busy")
    try:
        with ExitStack() as stack:
            try:
                if _stopped:
                    raise WorkshopError("model_workshop_busy")
                roots = tuple(dict.fromkeys(Path(root).resolve() for root in (source, target)))
                owners = []
                for root in roots:
                    service = _services.get(str(root))
                    if service is not None:
                        stack.enter_context(service.migration_idle())
                        owners.append((str(root), service))
                    evidence = inspect_model_workshop_metadata(root)
                    if evidence is not None:
                        # Main validates full rows/journals as well as the low
                        # layer's envelope; no metadata is repaired by preflight.
                        config = SimpleNamespace(app_docs_dir=root, **{
                            name + "_dir": root / name for name in ("live2d", "vrm", "mmd")
                        })
                        state = snapshot(config)
                        if state.errors:
                            raise WorkshopError("model_workshop_state_unreadable")
                        if state.pending:
                            raise WorkshopError("model_workshop_pending")
                    if service is None:
                        assert_model_workshop_stopped(root)
                require_paired_model_workshop_roots(roots[0], roots[-1], reuse_target=False)
                if quiesce:
                    # Validate both roots before releasing either idle owner.
                    # New callers stay behind _gate through commit/rollback.
                    for key, service in owners:
                        service.close()
                        _services.pop(key)
            except (ModelWorkshopStorageError, StorageMigrationError) as exc:
                raise WorkshopError(getattr(exc, "error_code", str(exc))) from exc
            except (OSError, ValueError, portalocker.exceptions.LockException) as exc:
                if isinstance(exc, WorkshopError):
                    raise
                raise WorkshopError("model_workshop_state_unreadable") from exc
            # Do not translate write/rollback failures from inside the caller's
            # transaction into a harmless preflight rejection.
            yield
    finally:
        _gate.release()


def check_migration_ready(source, target):
    """Read-only preflight; idle Main ownership is retained until actual scheduling."""
    with _migration_scope(source, target, quiesce=False):
        pass


@contextmanager
def prepare_migration(source, target):
    """Quiesce before taking root_state, then exclude new work through rollback."""
    with _migration_scope(source, target, quiesce=True):
        yield


def maintain(config):
    """Main's explicit lazy maintenance; absence and read failures do not disable ordinary models."""
    root = getattr(config, "app_docs_dir", None)
    if root is None or not os.path.lexists(Path(root) / "model_workshop"):
        return {}
    from utils.cloudsave_runtime import MaintenanceModeError

    try:
        return get_service(config).recover_if_changed()
    except (
        WorkshopError,
        OSError,
        MaintenanceModeError,
        portalocker.exceptions.LockException,
    ):
        return {}  # Availability still isolates every trusted unfinished path.


def require_url(config, value: str, *, for_binding: bool = False) -> None:
    state = snapshot(config)
    if state.paths is not None:
        if for_binding:
            try:
                parsed = urlsplit(value)
                raw = unquote(parsed.path, errors="strict").replace("\\", "/")
            except ValueError as exc:
                raise WorkshopError("model_workshop_unsafe_path") from exc
            local_origin = not (parsed.scheme or parsed.netloc) or parsed.hostname in ("localhost", "127.0.0.1", "::1")
            if local_origin and ".." in raw.split("/"):
                raise WorkshopError("model_workshop_unsafe_path")
        path = local_path(
            state.paths,
            value,
            readable_live2d=getattr(config, "readable_live2d_dir", None),
        )
        if for_binding and path is None:
            # Failed safe mapping is not proof of an external source. Browsers
            # normalize encoded dot segments before fetching a local URL.
            parsed = urlsplit(value)
            raw = unquote(parsed.path, errors="strict").replace("\\", "/")
            local_origin = not (parsed.scheme or parsed.netloc) or parsed.hostname in ("localhost", "127.0.0.1", "::1")
            relative = not parsed.scheme and not parsed.netloc and not raw.startswith("/")
            if local_origin and (relative or raw.startswith((
                "/user_live2d/", "/user_live2d_local/", "/user_vrm/", "/user_mmd/",
            ))):
                raise WorkshopError("model_workshop_unsafe_path")
        if path is not None:
            # A new local binding needs known ownership. Existing reads remain
            # tolerant of damaged metadata; unrelated builtin/Steam paths keep
            # their original behavior, including a separate readable CFA root.
            if for_binding and state.errors and state.relative(path) is not None:
                raise WorkshopError("model_workshop_unavailable")
            state.require(path)


def require_identifier(config, kind, value):
    state = snapshot(config)
    row = state.identifier(kind, value)
    if row is not None:
        state.require(state.paths.root / row["local_entrypoint"])
    return row


def remove_managed_model(config, captured, auxiliary=None):
    """Coordinate the existing delete adapter, including its synchronous auxiliary cleanup."""
    service = get_service(config)
    installer = service.installer
    digest = captured["package_sha256"]
    with service.activity(), installer.lifecycle(digest), installer.registry.lock, installer.mutation():
        state = installer.registry.read()
        row = state["installed"].get(digest)
        if (
            row is None
            or row["install_id"] != captured["install_id"]
            or state["attempts"].get(digest) != row["install_id"]
        ):
            raise WorkshopError("model_workshop_install_conflict")
        snapshot(config).require(service.paths.root / row["local_entrypoint"])
        target = service.paths.checked(row["target"])
        try:
            if os.path.lexists(target):
                if identity(target)[2] == stat.S_IFDIR:
                    # Refuse injected links before removing the one owned model tree.
                    for parent, dirs, files in os.walk(target, followlinks=False):
                        for name in dirs + files:
                            identity(Path(parent) / name)
                    shutil.rmtree(target)
                else:
                    target.unlink()
                sync_directory(target.parent)
            if auxiliary is not None and auxiliary() is False:
                raise OSError("associated configuration cleanup incomplete")
        except OSError as exc:
            # Keep the completed record: status reports actual missing/partial
            # resources, and an old delete cannot remove a later reinstall.
            raise WorkshopError("model_workshop_delete_partial") from exc
        return dict(success=True, deleted=True)


def close_all():
    """Called during Main shutdown, from a worker; ownership outlives all real I/O."""
    with _gate:
        global _stopped
        _stopped = True
        services = list(_services.values())
        _services.clear()
        for service in services:
            service.close()


def filter_catalog(config, items, *, url_encoded=True):
    """Keep existing option identity/source fields; add only trusted local display metadata."""
    state = snapshot(config)
    if state.paths is None:
        return items
    result = []
    for item in items:
        value = item.get("url") or item.get("path")
        try:
            path = (
                local_path(
                    state.paths, value, url_encoded=url_encoded,
                    readable_live2d=getattr(config, "readable_live2d_dir", None),
                )
                if isinstance(value, str) else None
            )
        except ValueError:
            continue  # One malformed URL cannot suppress unrelated models.
        if path is not None:
            try:
                state.require(path)
            except WorkshopError:
                continue
            row = state.record_for(path)
            if row is not None:
                item = dict(
                    item,
                    display_name=row["display_name"],
                    model_workshop={
                        key: row[key]
                        for key in (
                            "package_sha256",
                            "install_id",
                            "display_name",
                            "attribution",
                            "license",
                        )
                    },
                )
        result.append(item)
    return result


def write_managed_json(
    config, path, data, *, kind=None, name=None, indent=2, captured=_UNCAPTURED
):
    """Serialize existing local edits with managed install/delete; ordinary writers stay unchanged."""
    state = snapshot_for_mutation(config)
    current = state.identifier(kind, name) if kind else state.record_for(path)
    if captured is not _UNCAPTURED:
        # A read/modify/write request captures its attempt before awaiting the
        # read. Never apply its old contents to a replacement installed meanwhile.
        if (captured is None) != (current is None) or (
            captured and captured["install_id"] != current["install_id"]
        ):
            raise WorkshopError("model_workshop_install_conflict")
    captured = current
    if captured is None:
        return False
    service = get_service(config)
    installer = service.installer
    digest = captured["package_sha256"]
    with service.activity(), installer.lifecycle(digest), installer.registry.lock, installer.mutation():
        current = installer.registry.read()
        row = current["installed"].get(digest)
        if (
            row is None
            or row["install_id"] != captured["install_id"]
            or current["attempts"].get(digest) != row["install_id"]
        ):
            raise WorkshopError("model_workshop_install_conflict")
        snapshot(config).require(service.paths.root / row["target"])
        if installer.registry.status(row) != "installed":
            raise WorkshopError("model_workshop_unavailable")
        from utils.file_utils import atomic_write_json

        # The captured root may have an alternate spelling, but managed writes
        # always use a checked canonical path under that same owned root.
        relative = state.relative(path)
        if relative is None:
            # VRM emotion maps already live in project static/vrm/configs,
            # outside app_docs_dir. Permit only that exact existing target,
            # under the same installation/fence checks, never an arbitrary path.
            if kind != "vrm":
                raise WorkshopError("model_workshop_unsafe_path")
            config_dir = Path(config.project_root).absolute() / "static/vrm/configs"
            expected = config_dir / (Path(row["local_entrypoint"]).stem + "_emotion.json")
            if Path(path).absolute() != expected or expected.resolve().parent != config_dir.resolve():
                raise WorkshopError("model_workshop_unsafe_path")
            if os.path.lexists(expected):
                identity(expected)
            checked = expected
        else:
            checked = service.paths.checked(relative)
        atomic_write_json(checked, data, ensure_ascii=False, indent=indent)
        sync_directory(checked.parent)
    return True


def reject_managed_upload(config, path):
    """Old raw-upload entry points must not replace a managed model through a second writer."""
    if snapshot_for_mutation(config).record_for(path) is not None:
        raise WorkshopError("model_workshop_install_conflict")


def write_managed_blob(config, path, data):
    """Preserve existing motion/expression imports without resurrecting a deleted model."""
    state = snapshot_for_mutation(config)
    captured = state.record_for(path)
    if captured is None:
        return False
    service = get_service(config)
    installer = service.installer
    digest = captured["package_sha256"]
    with service.activity(), installer.lifecycle(digest), installer.registry.lock, installer.mutation():
        current = installer.registry.read()
        row = current["installed"].get(digest)
        if (
            row is None
            or row["install_id"] != captured["install_id"]
            or current["attempts"].get(digest) != row["install_id"]
        ):
            raise WorkshopError("model_workshop_install_conflict")
        snapshot(config).require(service.paths.root / row["target"])
        if installer.registry.status(row) != "installed":
            raise WorkshopError("model_workshop_unavailable")
        relative = state.relative(path)
        if relative is None:
            raise WorkshopError("model_workshop_unsafe_path")
        checked = service.paths.checked(relative)
        service.paths.mkdir(Path(relative).parent.as_posix())
        with regular(checked, os.O_WRONLY | os.O_CREAT | os.O_EXCL) as handle:
            from .paths import write_all

            write_all(handle, data)
            os.fsync(handle.fileno())
        sync_directory(checked.parent)
    return True
