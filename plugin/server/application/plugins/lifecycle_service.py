from __future__ import annotations

import asyncio
import inspect
import json
import math
import os
import re
import shutil
import time as time_module
import uuid
try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
    import tomli as tomllib  # type: ignore[no-redef]
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from fastapi import HTTPException

from plugin._types.exceptions import PluginError, PluginLifecycleError
from plugin.core.host import PluginProcessHost, _import_plugin_module
from plugin.core.registry import (
    _collect_plugin_python_requirements,
    _collect_plugin_python_requirement_paths,
    _check_plugin_dependency,
    _ensure_python_requirement_paths,
    _extract_entries_preview,
    _find_missing_python_requirements,
    _parse_plugin_dependencies,
    _resolve_plugin_id_conflict,
    scan_static_metadata,
)
from plugin.core.entry_points import (
    describe_plugin_entry_directory_mismatch,
    normalize_plugin_entry_point,
)
from plugin.core.state import state
from plugin.logging_config import get_logger
from plugin.server.domain import IO_RUNTIME_ERRORS, RUNTIME_ERRORS
from plugin.server.domain.errors import ServerDomainError
from plugin.server.application.plugins.registry_service import PluginRegistryService
from plugin.server.application.install_source import get_install_source_manager
from plugin.server.application.plugins.inventory_store import (
    capture_inventory_snapshot,
    get_inventory_resolution,
    get_user_installation_package_state_files,
    mark_plugin_deleted,
    remove_user_installation,
    restore_inventory_snapshot,
    select_plugin_installation,
)
from plugin.server.application.plugins.installation_selection import (
    inspect_plugin_installations,
)
from plugin.server.application.plugins.package_ownership import sha256_file
from plugin.server.application.plugins.mutation_guard import plugin_mutation_guard
from plugin.server.application.plugins.upgrade_support import (
    await_cancellation_safe,
    fsync_parent_directory,
)
from plugin.server.infrastructure.config_resolver import resolve_plugin_config_from_path
from plugin.server.infrastructure.path_safety import (
    ensure_tree_has_no_links_or_reparse_points,
    is_link_or_reparse_point,
)
from plugin.server.infrastructure.runtime_overrides import (
    RuntimeOverridePersistenceError,
    clear_runtime_override,
    get_runtime_auto_start_override,
    get_runtime_override,
    migrate_runtime_override,
    set_runtime_override,
)
from plugin.server.messaging.lifecycle_events import emit_lifecycle_event
from plugin.server.messaging.llm_tool_registry import (
    clear_plugin_tools as clear_plugin_llm_tools,
)
from plugin.settings import (
    BUILTIN_PLUGIN_CONFIG_ROOT,
    PLUGIN_CONFIG_ROOTS,
    PLUGIN_SHUTDOWN_TIMEOUT,
    PLUGIN_STARTUP_TIMEOUT,
    PLUGIN_SYNC_AUTO_START_ON_TOGGLE,
)
from plugin.utils import parse_bool_config

logger = get_logger("server.application.plugins.lifecycle")
_PLUGIN_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")
_PLUGIN_STARTUP_TIMEOUT_MAX = 300.0
plugin_registry_service = PluginRegistryService()


def _persist_user_runtime_intent(
    plugin_id: str,
    enabled: bool,
    *,
    previous_plugin_ids: tuple[str, ...] = (),
    runtime_state_changed: bool = False,
) -> None:
    try:
        auto_start = enabled if PLUGIN_SYNC_AUTO_START_ON_TOGGLE else None
        if previous_plugin_ids:
            migrate_runtime_override(
                previous_plugin_ids,
                plugin_id,
                enabled,
                auto_start=auto_start,
            )
        else:
            set_runtime_override(
                plugin_id,
                enabled,
                auto_start=auto_start,
            )
    except RuntimeOverridePersistenceError as exc:
        raise ServerDomainError(
            code="PLUGIN_RUNTIME_PREFERENCE_PERSIST_FAILED",
            message="PLUGIN_RUNTIME_PREFERENCE_PERSIST_FAILED",
            status_code=500,
            details={
                "plugin_id": plugin_id,
                "enabled": enabled,
                "auto_start": (
                    enabled if PLUGIN_SYNC_AUTO_START_ON_TOGGLE else None
                ),
                "error_type": type(exc).__name__,
                "runtime_state_changed": runtime_state_changed,
            },
            log_level="error",
        ) from exc


def _mark_preference_persistence_failure(
    response: dict[str, object],
    error: ServerDomainError,
) -> None:
    details = error.details if isinstance(error.details, dict) else {}
    response["partial_success"] = True
    response["preference_persisted"] = False
    response["preference_error"] = {
        "code": error.code,
        "error_type": str(details.get("error_type", "RuntimeOverridePersistenceError")),
    }
    response["runtime_state_changed"] = bool(details.get("runtime_state_changed", False))


async def _persist_changed_runtime_intent(
    response: dict[str, object],
    plugin_id: str,
    enabled: bool,
    *,
    previous_plugin_ids: tuple[str, ...] = (),
) -> None:
    try:
        await asyncio.to_thread(
            _persist_user_runtime_intent,
            plugin_id,
            enabled,
            previous_plugin_ids=previous_plugin_ids,
            runtime_state_changed=True,
        )
        response["preference_persisted"] = True
    except ServerDomainError as exc:
        logger.error(
            "plugin runtime state changed but user preference could not be persisted: plugin_id={}, enabled={}, err_type={}",
            plugin_id,
            enabled,
            type(exc).__name__,
        )
        _mark_preference_persistence_failure(response, exc)


@runtime_checkable
class PluginHostContract(Protocol):
    async def start(
        self,
        message_target_queue: object,
        startup_timeout: float | None = None,
        startup_failure: str = "warn",
    ) -> object: ...

    async def shutdown(self, timeout: float = PLUGIN_SHUTDOWN_TIMEOUT) -> None: ...

    def is_alive(self) -> bool: ...


@dataclass(slots=True, frozen=True)
class _ReloadOutcome:
    plugin_id: str
    success: bool
    error: str | None = None


def _normalize_mapping(raw: Mapping[object, object], *, context: str) -> dict[str, object]:
    normalized: dict[str, object] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            raise ServerDomainError(
                code="INVALID_DATA_SHAPE",
                message=f"{context} contains non-string key",
                status_code=500,
                details={"key_type": type(key).__name__},
            )
        normalized[key] = value
    return normalized


def _detail_to_message(detail: object, *, default_message: str) -> str:
    if isinstance(detail, str) and detail:
        return detail
    return default_message


def _to_domain_error(
    *,
    code: str,
    message: str,
    status_code: int,
    plugin_id: str | None,
    error_type: str,
) -> ServerDomainError:
    return ServerDomainError(
        code=code,
        message=message,
        status_code=status_code,
        details={
            "plugin_id": plugin_id or "",
            "error_type": error_type,
        },
    )


def _get_plugin_host_sync(plugin_id: str) -> object | None:
    with state.acquire_plugin_hosts_read_lock():
        return state.plugin_hosts.get(plugin_id)


def _pop_plugin_host_sync(plugin_id: str) -> object | None:
    with state.acquire_plugin_hosts_write_lock():
        popped = state.plugin_hosts.pop(plugin_id, None)
    if popped is not None:
        state.invalidate_snapshot_cache("hosts")
    return popped


def _plugin_is_running_sync(plugin_id: str) -> bool:
    with state.acquire_plugin_hosts_read_lock():
        return plugin_id in state.plugin_hosts


def _list_running_plugin_ids_sync() -> list[str]:
    with state.acquire_plugin_hosts_read_lock():
        return [plugin_id for plugin_id in state.plugin_hosts.keys()]


def _remove_event_handlers_sync(plugin_id: str) -> None:
    removed_any = False
    with state.acquire_event_handlers_write_lock():
        target_prefix_dot = f"{plugin_id}."
        target_prefix_colon = f"{plugin_id}:"
        keys_to_remove = [
            key
            for key in list(state.event_handlers.keys())
            if key.startswith(target_prefix_dot) or key.startswith(target_prefix_colon)
        ]
        for key in keys_to_remove:
            del state.event_handlers[key]
            removed_any = True
    if removed_any:
        state.invalidate_snapshot_cache("handlers")


def _get_plugin_meta_sync(plugin_id: str) -> dict[str, object] | None:
    with state.acquire_plugins_read_lock():
        raw_meta = state.plugins.get(plugin_id)
    if not isinstance(raw_meta, dict):
        return None

    normalized: dict[str, object] = {}
    for key, value in raw_meta.items():
        if isinstance(key, str):
            normalized[key] = value
    return normalized


def _set_plugin_runtime_enabled_sync(plugin_id: str, enabled: bool) -> None:
    with state.acquire_plugins_write_lock():
        raw_meta = state.plugins.get(plugin_id)
        if not isinstance(raw_meta, dict):
            return
        raw_meta["runtime_enabled"] = enabled
        state.plugins[plugin_id] = raw_meta
    state.invalidate_snapshot_cache("plugins")


def _set_plugin_runtime_metadata_sync(
    plugin_id: str,
    *,
    runtime_enabled: bool,
    runtime_auto_start: bool,
    entries_preview: list[dict[str, object]] | None = None,
    startup_state: str | None = None,
    startup_error: str | None = None,
) -> None:
    with state.acquire_plugins_write_lock():
        raw_meta = state.plugins.get(plugin_id)
        if not isinstance(raw_meta, dict):
            return
        raw_meta["runtime_enabled"] = runtime_enabled
        raw_meta["runtime_auto_start"] = runtime_auto_start
        if entries_preview is not None:
            raw_meta["entries_preview"] = entries_preview
        if startup_state is not None:
            raw_meta["runtime_startup_state"] = startup_state
        else:
            raw_meta.pop("runtime_startup_state", None)
        if startup_error:
            raw_meta["runtime_startup_error"] = startup_error
        else:
            raw_meta.pop("runtime_startup_error", None)
        raw_meta.pop("runtime_load_state", None)
        raw_meta.pop("runtime_load_error_type", None)
        raw_meta.pop("runtime_load_error_message", None)
        raw_meta.pop("runtime_load_error_phase", None)
        raw_meta.pop("runtime_load_error_time", None)
        raw_meta.pop("runtime_source_missing", None)
        state.plugins[plugin_id] = raw_meta
    state.invalidate_snapshot_cache("plugins")


def _get_plugin_config_path(plugin_id: str) -> Path | None:
    normalized_plugin_id = plugin_id.strip()
    if not _PLUGIN_ID_PATTERN.fullmatch(normalized_plugin_id):
        return None
    canonical_plugin_id = normalized_plugin_id.casefold()
    inventory = get_inventory_resolution()
    if canonical_plugin_id in inventory.deleted_plugin_ids:
        return None

    active_user_directory = inventory.active_user_directories.get(canonical_plugin_id)
    if active_user_directory is not None and len(PLUGIN_CONFIG_ROOTS) > 1:
        roots = tuple(PLUGIN_CONFIG_ROOTS)
        active = inventory.active_installations.get(canonical_plugin_id)
        candidate_roots = roots[1:]
        if len(roots) >= 3 and active is not None:
            candidate_roots = roots[1:2] if active.installation_kind == "managed" else roots[2:]
        for root in candidate_roots:
            resolved_root = root.resolve()
            config_file = (resolved_root / active_user_directory / "plugin.toml").resolve()
            if resolved_root in config_file.parents and config_file.exists():
                return config_file

    for root in PLUGIN_CONFIG_ROOTS:
        resolved_root = root.resolve()
        config_file = (resolved_root / normalized_plugin_id / "plugin.toml").resolve()
        if resolved_root not in config_file.parents:
            continue
        if config_file.exists():
            return config_file
    return None


def _resolve_plugin_dir_sync(plugin_id: str, plugin_meta: dict[str, object] | None) -> Path | None:
    config_path = _resolve_registered_config_path_sync(plugin_meta)
    if config_path is None:
        config_path = _get_plugin_config_path(plugin_id)
    if config_path is None:
        return None
    try:
        return config_path.parent.resolve()
    except Exception:
        return config_path.parent


def _path_within_plugin_roots_sync(path: Path) -> bool:
    try:
        resolved_path = path.resolve()
    except Exception:
        resolved_path = path

    for root in PLUGIN_CONFIG_ROOTS:
        try:
            resolved_root = root.resolve()
        except Exception:
            resolved_root = root
        if resolved_path == resolved_root or resolved_root in resolved_path.parents:
            return True
    return False


def _plugin_root_kind_sync(path: Path) -> str | None:
    """Classify a plugin directory by payload ownership.

    Production roots are ordered built-in, managed payload, then legacy user
    layout.  A two-root configuration predates the managed payload root and is
    therefore classified as legacy for compatibility.
    """

    try:
        resolved_path = path.resolve()
    except Exception:
        resolved_path = path
    roots = tuple(PLUGIN_CONFIG_ROOTS)
    for index, root in enumerate(roots):
        try:
            resolved_root = root.resolve()
        except Exception:
            resolved_root = root
        if resolved_root in resolved_path.parents:
            if len(roots) > 1 and index == 0:
                return "builtin"
            if len(roots) > 2 and index == 1:
                return "managed"
            return "legacy"
    return None


def _is_writable_installation_root(root_kind: str | None) -> bool:
    # ``user`` is retained for older tests/callers which used the pre-v2 name.
    return root_kind in {"managed", "legacy", "user"}


def _remove_plugin_metadata_sync(plugin_id: str) -> bool:
    removed = False
    with state.acquire_plugins_write_lock():
        if plugin_id in state.plugins:
            state.plugins.pop(plugin_id, None)
            removed = True
    if removed:
        state.invalidate_snapshot_cache("plugins")
    return removed


def _delete_plugin_directory_sync(plugin_dir: Path) -> bool:
    if not plugin_dir.exists():
        return False
    ensure_tree_has_no_links_or_reparse_points(
        plugin_dir,
        field="plugin delete target",
    )
    backup_root = plugin_dir.parent / ".delete-backups"
    backup_dir = backup_root / plugin_dir.name
    backup_root.mkdir(parents=True, exist_ok=True)
    if backup_dir.exists():
        raise FileExistsError(backup_dir)
    plugin_dir.rename(backup_dir)
    preserved_state_names = {"config", "data", "cache"}
    try:
        state_children = tuple(
            child
            for child in backup_dir.iterdir()
            if child.name.casefold() in preserved_state_names
            and child.is_dir()
            and not is_link_or_reparse_point(child)
        )
        if state_children:
            plugin_dir.mkdir(parents=True)
            for child in state_children:
                child.rename(plugin_dir / child.name)
    except BaseException:
        if plugin_dir.exists():
            for child in tuple(plugin_dir.iterdir()):
                child.rename(backup_dir / child.name)
            plugin_dir.rmdir()
        backup_dir.rename(plugin_dir)
        try:
            backup_root.rmdir()
        except OSError:
            pass
        raise
    return True


def _delete_managed_plugin_directory_sync(
    plugin_dir: Path,
    package_state_files: dict[str, str] | None,
) -> bool:
    """Remove a managed payload while retaining only legacy runtime residue.

    SDK user state lives outside ``plugin-installations`` and is therefore not
    touched here.  For older plugins which still write beneath their installed
    ``config/data/cache`` directories, files not owned by the last package (or
    package-owned files modified locally) remain as a state-only compatibility
    directory.  Unchanged package-owned files are deleted with the payload.
    """

    if not plugin_dir.exists():
        return False
    ensure_tree_has_no_links_or_reparse_points(
        plugin_dir,
        field="managed plugin delete target",
    )
    backup_root = plugin_dir.parent / ".delete-backups"
    backup_dir = backup_root / plugin_dir.name
    backup_root.mkdir(parents=True, exist_ok=True)
    if backup_dir.exists():
        raise FileExistsError(backup_dir)
    plugin_dir.rename(backup_dir)
    moved: list[tuple[Path, Path]] = []
    try:
        for state_name in ("config", "data", "cache"):
            state_root = backup_dir / state_name
            if not state_root.is_dir():
                continue
            for source in sorted(
                (path for path in state_root.rglob("*") if path.is_file()),
                key=lambda path: path.as_posix(),
            ):
                relative = source.relative_to(backup_dir)
                relative_key = relative.as_posix()
                package_digest = (
                    package_state_files.get(relative_key)
                    if package_state_files is not None
                    else None
                )
                if package_digest is not None and sha256_file(source) == package_digest:
                    continue
                target = plugin_dir / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                source.rename(target)
                moved.append((source, target))
    except BaseException:
        for source, target in reversed(moved):
            source.parent.mkdir(parents=True, exist_ok=True)
            target.rename(source)
        if plugin_dir.exists():
            shutil.rmtree(plugin_dir)
        backup_dir.rename(plugin_dir)
        try:
            backup_root.rmdir()
        except OSError:
            pass
        raise
    return True


@dataclass(frozen=True, slots=True)
class DeleteRecoveryResult:
    recovered_operation_ids: tuple[str, ...]
    manual_recovery_operation_ids: tuple[str, ...]
    manual_recovery_plugin_ids: tuple[str, ...] = ()
    block_user_plugin_root: bool = False


class _DeleteJournal:
    def __init__(
        self,
        *,
        path: Path,
        operation_id: str,
        plugin_id: str,
        plugin_dir: Path,
        backup_dir: Path,
        rollback_snapshot: Path,
        phase: str,
    ) -> None:
        self.path = path
        self.owner_path = path.with_suffix(".owner")
        self.state: dict[str, object] = {
            "schema_version": 1,
            "operation_id": operation_id,
            "plugin_id": plugin_id,
            "phase": phase,
            "plugin_dir": str(plugin_dir.resolve(strict=False)),
            "backup_dir": str(backup_dir.resolve(strict=False)),
            "rollback_snapshot": str(rollback_snapshot.resolve(strict=False)),
        }
        try:
            self._write_owner()
            self._write()
        except BaseException:
            self.path.unlink(missing_ok=True)
            self.owner_path.unlink(missing_ok=True)
            raise

    @classmethod
    def create(
        cls,
        *,
        plugin_id: str,
        plugin_dir: Path,
        rollback_snapshot: Path,
        phase: str = "snapshot_pending",
    ) -> _DeleteJournal:
        operation_id = uuid.uuid4().hex
        backup_root = plugin_dir.parent / ".delete-backups"
        journal_root = backup_root / ".transactions"
        journal_root.mkdir(parents=True, exist_ok=True)
        return cls(
            path=journal_root / f"{operation_id}.json",
            operation_id=operation_id,
            plugin_id=plugin_id,
            plugin_dir=plugin_dir,
            backup_dir=backup_root / plugin_dir.name,
            rollback_snapshot=rollback_snapshot,
            phase=phase,
        )

    def set_phase(self, phase: str) -> None:
        self.state["phase"] = phase
        self._write()

    def finish(self) -> None:
        self.path.unlink(missing_ok=True)
        self.owner_path.unlink(missing_ok=True)
        try:
            self.path.parent.rmdir()
            self.path.parent.parent.rmdir()
        except OSError:
            pass

    def ensure_persisted(self) -> None:
        if not self.owner_path.exists():
            self._write_owner()
        if not self.path.exists():
            self._write()

    def _write(self) -> None:
        payload = (
            json.dumps(self.state, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            fsync_parent_directory(self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def _write_owner(self) -> None:
        payload = (
            json.dumps(
                {
                    "schema_version": 1,
                    "operation_id": self.state["operation_id"],
                    "plugin_id": self.state["plugin_id"],
                    "kind": "deletion",
                },
                ensure_ascii=True,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
        with self.owner_path.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        fsync_parent_directory(self.owner_path)


def _load_delete_owner(journal_path: Path) -> tuple[str, str]:
    owner = json.loads(journal_path.with_suffix(".owner").read_text(encoding="utf-8"))
    if not isinstance(owner, dict) or owner.get("schema_version") != 1:
        raise ValueError("delete journal owner marker is invalid")
    operation_id = owner.get("operation_id")
    plugin_id = owner.get("plugin_id")
    if (
        owner.get("kind") != "deletion"
        or operation_id != journal_path.stem
        or not isinstance(plugin_id, str)
        or not _PLUGIN_ID_PATTERN.fullmatch(plugin_id)
    ):
        raise ValueError("delete journal owner identity is invalid")
    return operation_id, plugin_id


def recover_incomplete_plugin_deletions(
    *,
    journal_root: Path,
    user_root: Path,
) -> DeleteRecoveryResult:
    recovered: list[str] = []
    manual: list[str] = []
    manual_plugin_ids: list[str] = []
    block_user_plugin_root = False
    if not journal_root.is_dir():
        return DeleteRecoveryResult((), ())
    resolved_user_root = user_root.resolve(strict=False)
    for journal_path in sorted(journal_root.glob("*.json")):
        operation_id = journal_path.stem
        plugin_id: str | None = None
        try:
            operation_id, plugin_id = _load_delete_owner(journal_path)
            state = json.loads(journal_path.read_text(encoding="utf-8"))
            if not isinstance(state, dict) or state.get("schema_version") != 1:
                manual.append(operation_id)
                manual_plugin_ids.append(plugin_id.casefold())
                continue
            if (
                state.get("operation_id") != operation_id
                or state.get("plugin_id") != plugin_id
            ):
                raise ValueError("delete journal identity does not match owner")
            plugin_dir = Path(str(state.get("plugin_dir", ""))).resolve(strict=False)
            backup_dir = Path(str(state.get("backup_dir", ""))).resolve(strict=False)
            rollback_snapshot = Path(
                str(state.get("rollback_snapshot", ""))
            ).resolve(strict=False)
            backup_root = resolved_user_root / ".delete-backups"
            if (
                plugin_dir.parent != resolved_user_root
                or plugin_dir.name.casefold() != plugin_id.casefold()
                or backup_dir != backup_root / plugin_dir.name
                or rollback_snapshot.parent != backup_root
                or not rollback_snapshot.name.startswith(f"{plugin_dir.name}.rollback.")
            ):
                raise ValueError("delete journal paths are invalid")
            phase = state.get("phase")
            if phase == "snapshot_pending":
                backup_exists = backup_dir.exists() or is_link_or_reparse_point(backup_dir)
                plugin_exists = plugin_dir.exists() or is_link_or_reparse_point(plugin_dir)
                snapshot_exists = rollback_snapshot.exists() or is_link_or_reparse_point(
                    rollback_snapshot
                )
                if (
                    backup_exists
                    or not plugin_exists
                    or not plugin_dir.is_dir()
                    or is_link_or_reparse_point(plugin_dir)
                    or (
                        snapshot_exists
                        and (
                            not rollback_snapshot.is_dir()
                            or is_link_or_reparse_point(rollback_snapshot)
                        )
                    )
                ):
                    manual.append(operation_id)
                    manual_plugin_ids.append(plugin_id.casefold())
                    continue
                if snapshot_exists:
                    shutil.rmtree(rollback_snapshot)
            elif phase == "precommit":
                snapshot_ready = (
                    rollback_snapshot.is_dir()
                    and not is_link_or_reparse_point(rollback_snapshot)
                )
                backup_exists = backup_dir.exists() or is_link_or_reparse_point(backup_dir)
                plugin_exists = plugin_dir.exists() or is_link_or_reparse_point(plugin_dir)
                if not snapshot_ready:
                    manual.append(operation_id)
                    manual_plugin_ids.append(plugin_id.casefold())
                    continue
                if backup_exists:
                    if (
                        not backup_dir.is_dir()
                        or is_link_or_reparse_point(backup_dir)
                        or (
                            plugin_exists
                            and (
                                not plugin_dir.is_dir()
                                or is_link_or_reparse_point(plugin_dir)
                            )
                        )
                    ):
                        manual.append(operation_id)
                        manual_plugin_ids.append(plugin_id.casefold())
                        continue
                    _restore_delete_rollback_snapshot_sync(
                        plugin_dir,
                        rollback_snapshot,
                    )
                elif plugin_exists:
                    if not plugin_dir.is_dir() or is_link_or_reparse_point(plugin_dir):
                        manual.append(operation_id)
                        manual_plugin_ids.append(plugin_id.casefold())
                        continue
                    shutil.rmtree(rollback_snapshot)
                else:
                    manual.append(operation_id)
                    manual_plugin_ids.append(plugin_id.casefold())
                    continue
            elif phase == "committed":
                _finalize_delete_transaction_sync(
                    plugin_dir,
                    rollback_snapshot,
                )
            else:
                manual.append(operation_id)
                manual_plugin_ids.append(plugin_id.casefold())
                continue
            journal_path.unlink(missing_ok=True)
            journal_path.with_suffix(".owner").unlink(missing_ok=True)
            recovered.append(operation_id)
        except Exception as exc:
            logger.error(
                "delete journal recovery requires manual action "
                "operation_id={} err_type={}",
                operation_id,
                type(exc).__name__,
            )
            manual.append(operation_id)
            if plugin_id is None:
                block_user_plugin_root = True
            else:
                manual_plugin_ids.append(plugin_id.casefold())
    return DeleteRecoveryResult(
        tuple(recovered),
        tuple(dict.fromkeys(manual)),
        tuple(dict.fromkeys(manual_plugin_ids)),
        block_user_plugin_root,
    )


def _delete_rollback_snapshot_path(plugin_dir: Path) -> Path:
    backup_root = plugin_dir.parent / ".delete-backups"
    return backup_root / f"{plugin_dir.name}.rollback.{uuid.uuid4().hex}"


def _capture_delete_rollback_snapshot_sync(
    plugin_dir: Path,
    snapshot: Path | None = None,
) -> Path:
    ensure_tree_has_no_links_or_reparse_points(
        plugin_dir,
        field="plugin delete snapshot",
    )
    backup_root = plugin_dir.parent / ".delete-backups"
    backup_root.mkdir(parents=True, exist_ok=True)
    snapshot = snapshot or _delete_rollback_snapshot_path(plugin_dir)
    if snapshot.parent != backup_root:
        raise ValueError("plugin delete snapshot must stay inside the backup root")
    try:
        shutil.copytree(plugin_dir, snapshot, symlinks=True)
        return snapshot
    except BaseException:
        shutil.rmtree(snapshot, ignore_errors=True)
        raise


def _restore_delete_rollback_snapshot_sync(
    plugin_dir: Path,
    rollback_snapshot: Path | None,
    *,
    delete_started: bool = False,
) -> None:
    if rollback_snapshot is None or not rollback_snapshot.is_dir():
        raise FileNotFoundError("plugin delete rollback snapshot is missing")
    transaction_backup = plugin_dir.parent / ".delete-backups" / plugin_dir.name
    if not (transaction_backup.exists() or is_link_or_reparse_point(transaction_backup)):
        if (
            not delete_started
            and plugin_dir.is_dir()
            and not is_link_or_reparse_point(plugin_dir)
        ):
            shutil.rmtree(rollback_snapshot)
            return
        if not delete_started:
            raise FileNotFoundError("plugin delete transaction backup is missing")
    for path in (plugin_dir, transaction_backup):
        if path.is_dir() and not is_link_or_reparse_point(path):
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
    rollback_snapshot.rename(plugin_dir)
    try:
        transaction_backup.parent.rmdir()
    except OSError:
        pass


def _finalize_delete_transaction_sync(
    plugin_dir: Path,
    rollback_snapshot: Path | None,
) -> None:
    backup_root = plugin_dir.parent / ".delete-backups"
    for path in (backup_root / plugin_dir.name, rollback_snapshot):
        if path is None:
            continue
        if path.is_dir() and not is_link_or_reparse_point(path):
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
    try:
        backup_root.rmdir()
    except OSError:
        pass


def _builtin_plugin_exists_sync(plugin_id: str) -> bool:
    roots = tuple(PLUGIN_CONFIG_ROOTS)
    if len(roots) < 2:
        return False
    builtin_root = roots[0]
    if not builtin_root.is_dir():
        return False
    canonical_id = plugin_id.casefold()
    for manifest_path in builtin_root.glob("*/plugin.toml"):
        try:
            with manifest_path.open("rb") as handle:
                raw = tomllib.load(handle)
        except (OSError, tomllib.TOMLDecodeError):
            continue
        plugin_table = raw.get("plugin")
        manifest_id = plugin_table.get("id") if isinstance(plugin_table, dict) else None
        if isinstance(manifest_id, str) and manifest_id.strip().casefold() == canonical_id:
            return True
    return False


def _register_or_replace_host_sync(plugin_id: str, host: PluginHostContract) -> int:
    with state.acquire_plugin_hosts_write_lock():
        if plugin_id in state.plugin_hosts:
            existing_host = state.plugin_hosts.get(plugin_id)
            if existing_host is not None and existing_host is not host:
                logger.warning("Plugin {} already exists in plugin_hosts, replacing host", plugin_id)
        state.plugin_hosts[plugin_id] = host
        current_count = len(state.plugin_hosts)
    state.invalidate_snapshot_cache("hosts")
    return current_count


def _read_plugin_config_sync(config_path: Path) -> dict[str, object]:
    with config_path.open("rb") as file_obj:
        raw_conf = tomllib.load(file_obj)
    if not isinstance(raw_conf, Mapping):
        raise ValueError("plugin config root must be an object")
    return _normalize_mapping(raw_conf, context=f"plugin_config[{config_path}]")


def _resolve_registered_config_path_sync(plugin_meta: dict[str, object] | None) -> Path | None:
    if not isinstance(plugin_meta, dict):
        return None

    config_path_obj = plugin_meta.get("config_path")
    if not isinstance(config_path_obj, str) or not config_path_obj:
        return None

    try:
        return Path(config_path_obj).resolve()
    except Exception:
        return Path(config_path_obj)


def _registered_load_failure_error(plugin_id: str, plugin_meta: dict[str, object] | None) -> ServerDomainError | None:
    if not isinstance(plugin_meta, dict) or plugin_meta.get("runtime_load_state") != "failed":
        return None

    error_type_obj = plugin_meta.get("runtime_load_error_type")
    error_message_obj = plugin_meta.get("runtime_load_error_message")
    error_phase_obj = plugin_meta.get("runtime_load_error_phase")
    error_type = str(error_type_obj or "PluginLoadFailed")
    if error_type not in {"PluginEntryDirectoryMismatch", "SyntaxError"}:
        return None

    error_message = str(error_message_obj or "Plugin failed to load during registry refresh")
    error_phase = str(error_phase_obj or "unknown")
    code = "PLUGIN_ENTRY_DIRECTORY_MISMATCH" if error_type == "PluginEntryDirectoryMismatch" else "PLUGIN_LOAD_FAILED"
    return _to_domain_error(
        code=code,
        message=(
            f"Plugin '{plugin_id}' cannot be started because its entry failed during "
            f"registry phase '{error_phase}': {error_type}: {error_message}"
        ),
        status_code=400,
        plugin_id=plugin_id,
        error_type=error_type,
    )


async def _cleanup_started_host(plugin_id: str, host: PluginHostContract) -> None:
    removed = await asyncio.to_thread(_pop_plugin_host_sync, plugin_id)
    target_host = host
    if isinstance(removed, PluginHostContract):
        target_host = removed

    try:
        await target_host.shutdown(timeout=1.0)
    except PluginError as exc:
        logger.warning(
            "cleanup shutdown failed with PluginError: plugin_id={}, err_type={}, err={}",
            plugin_id,
            type(exc).__name__,
            str(exc),
        )
    except RUNTIME_ERRORS as exc:
        logger.warning(
            "cleanup shutdown failed: plugin_id={}, err_type={}, err={}",
            plugin_id,
            type(exc).__name__,
            str(exc),
        )


def _emit_lifecycle_event(
    *,
    event_type: str,
    plugin_id: str | None = None,
    data: Mapping[str, object] | None = None,
) -> None:
    event: dict[str, object] = {
        "type": event_type,
    }
    if plugin_id is not None:
        event["plugin_id"] = plugin_id
    if data is not None:
        event["data"] = dict(data)
    emit_lifecycle_event(event)


def _normalize_runtime_timeout(
    raw_value: object,
    *,
    plugin_id: str,
    setting_label: str = "[plugin_runtime].timeout",
) -> float:
    message = (
        f"Plugin '{plugin_id}' {setting_label} must be a number "
        f"in range 0 < timeout <= {_PLUGIN_STARTUP_TIMEOUT_MAX:g}"
    )
    if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
        raise _to_domain_error(
            code="INVALID_PLUGIN_CONFIG",
            message=message,
            status_code=400,
            plugin_id=plugin_id,
            error_type="InvalidStartupTimeout",
        )
    timeout = float(raw_value)
    if not math.isfinite(timeout) or timeout <= 0 or timeout > _PLUGIN_STARTUP_TIMEOUT_MAX:
        raise _to_domain_error(
            code="INVALID_PLUGIN_CONFIG",
            message=message,
            status_code=400,
            plugin_id=plugin_id,
            error_type="InvalidStartupTimeout",
        )
    return timeout


def _normalize_startup_failure_policy(raw_value: object, *, plugin_id: str) -> str:
    if raw_value is None:
        return "warn"
    policy = str(raw_value).strip().lower()
    if policy in {"warn", "fail", "ignore"}:
        return policy
    raise _to_domain_error(
        code="INVALID_PLUGIN_CONFIG",
        message=f"Plugin '{plugin_id}' [plugin_runtime].startup_failure must be one of: warn, fail, ignore",
        status_code=400,
        plugin_id=plugin_id,
        error_type="InvalidStartupFailurePolicy",
    )


def _start_method_accepts_kwarg(start_method: object, name: str) -> bool:
    try:
        signature = inspect.signature(start_method)
    except (TypeError, ValueError):
        return False
    return (
        name in signature.parameters
        or any(param.kind is inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values())
    )


def _extract_startup_error(start_result: object) -> str | None:
    if not isinstance(start_result, Mapping):
        return None
    raw_error = start_result.get("startup_error")
    if isinstance(raw_error, str) and raw_error:
        return raw_error
    data = start_result.get("data")
    if isinstance(data, Mapping):
        raw_error = data.get("startup_error")
        if isinstance(raw_error, str) and raw_error:
            return raw_error
    return None


def _is_startup_timeout_error(exc: PluginLifecycleError) -> bool:
    reason = str(getattr(exc, "reason", "") or "").lower()
    return getattr(exc, "event_type", None) == "startup" and bool(
        re.fullmatch(r"startup timed out after \d+(?:\.\d+)?(?:e[+-]?\d+)?s", reason)
    )


def _startup_timeout_domain_error(
    *,
    plugin_id: str,
    startup_timeout: float,
) -> ServerDomainError:
    return _to_domain_error(
        code="PLUGIN_START_TIMEOUT",
        message=f"Plugin '{plugin_id}' startup timed out after {startup_timeout}s",
        status_code=504,
        plugin_id=plugin_id,
        error_type="StartupTimeout",
    )


async def _start_host_with_timeout(
    *,
    plugin_id: str,
    host_obj: PluginHostContract,
    message_target_queue: object,
    startup_timeout: float | None,
    startup_failure: str,
) -> object:
    start_method = host_obj.start
    kwargs: dict[str, object] = {"message_target_queue": message_target_queue}
    if _start_method_accepts_kwarg(start_method, "startup_failure"):
        kwargs["startup_failure"] = startup_failure
    if startup_timeout is not None and _start_method_accepts_kwarg(start_method, "startup_timeout"):
        kwargs["startup_timeout"] = startup_timeout
        try:
            return await start_method(**kwargs)
        except PluginLifecycleError as exc:
            if _is_startup_timeout_error(exc):
                raise _startup_timeout_domain_error(
                    plugin_id=plugin_id,
                    startup_timeout=startup_timeout,
                ) from exc
            raise

    start_coro = start_method(**kwargs)
    if startup_timeout is None:
        return await start_coro

    try:
        return await asyncio.wait_for(start_coro, timeout=startup_timeout)
    except asyncio.TimeoutError as exc:
        raise _startup_timeout_domain_error(
            plugin_id=plugin_id,
            startup_timeout=startup_timeout,
        ) from exc
    except PluginLifecycleError as exc:
        if _is_startup_timeout_error(exc):
            raise _startup_timeout_domain_error(
                plugin_id=plugin_id,
                startup_timeout=startup_timeout,
            ) from exc
        raise


class PluginLifecycleService:
    async def start_plugin(
        self,
        plugin_id: str,
        restore_state: bool = False,
        *,
        refresh_registry: bool = True,
        persist_user_intent: bool = False,
    ) -> dict[str, object]:
        start_time = time_module.perf_counter()
        original_plugin_id = plugin_id
        current_plugin_id = plugin_id
        resolved_plugin_ids = [plugin_id]

        existing_host_obj = await asyncio.to_thread(_get_plugin_host_sync, current_plugin_id)
        if isinstance(existing_host_obj, PluginHostContract):
            if existing_host_obj.is_alive():
                if persist_user_intent:
                    await asyncio.to_thread(
                        _persist_user_runtime_intent,
                        current_plugin_id,
                        True,
                        runtime_state_changed=False,
                    )
                _emit_lifecycle_event(event_type="plugin_start_skipped", plugin_id=current_plugin_id)
                return {
                    "success": True,
                    "plugin_id": current_plugin_id,
                    "message": "Plugin is already running",
                }
            # Stale host (process dead) — remove so re-start can proceed
            await asyncio.to_thread(_pop_plugin_host_sync, current_plugin_id)
            logger.info("removed stale host for plugin_id={} (process no longer alive)", current_plugin_id)

        if state.is_plugin_frozen(current_plugin_id) and not restore_state:
            raise _to_domain_error(
                code="PLUGIN_FROZEN",
                message=f"Plugin '{current_plugin_id}' is frozen. Use unfreeze_plugin to restore it.",
                status_code=409,
                plugin_id=current_plugin_id,
                error_type="PluginFrozen",
            )

        if refresh_registry:
            try:
                refresh_payload = await plugin_registry_service.refresh_plugin(current_plugin_id)
                refreshed_plugin_id = refresh_payload.get("plugin_id")
                if isinstance(refreshed_plugin_id, str) and refreshed_plugin_id:
                    if refreshed_plugin_id != current_plugin_id:
                        resolved_plugin_ids.append(refreshed_plugin_id)
                    current_plugin_id = refreshed_plugin_id
            except ServerDomainError as exc:
                if exc.code == "PLUGIN_CONFIG_NOT_FOUND":
                    logger.warning(
                        "registry refresh skipped for plugin_id={} because config lookup disagreed with lifecycle path resolution",
                        current_plugin_id,
                    )
                else:
                    raise _to_domain_error(
                        code=exc.code,
                        message=exc.message,
                        status_code=exc.status_code,
                        plugin_id=current_plugin_id,
                        error_type=str(exc.details.get("error_type", "RegistryRefreshFailed")) if isinstance(exc.details, dict) else "RegistryRefreshFailed",
                    ) from exc

        registered_meta = await asyncio.to_thread(_get_plugin_meta_sync, current_plugin_id)
        config_path = await asyncio.to_thread(_resolve_registered_config_path_sync, registered_meta)
        if config_path is None:
            config_path = _get_plugin_config_path(current_plugin_id)
        if config_path is None:
            raise _to_domain_error(
                code="PLUGIN_CONFIG_NOT_FOUND",
                message=f"Plugin '{current_plugin_id}' configuration not found",
                status_code=404,
                plugin_id=current_plugin_id,
                error_type="ConfigNotFound",
            )
        registered_load_error = _registered_load_failure_error(current_plugin_id, registered_meta)
        if registered_load_error is not None:
            raise registered_load_error

        host_obj: PluginHostContract | None = None
        registered_plugin_id: str | None = None

        try:
            conf = await asyncio.to_thread(_read_plugin_config_sync, config_path)
            logger.info(
                "start_plugin config loaded: plugin_id={}, elapsed={:.3f}s",
                current_plugin_id,
                time_module.perf_counter() - start_time,
            )

            try:
                resolved_conf = await asyncio.to_thread(
                    resolve_plugin_config_from_path,
                    str(current_plugin_id),
                    config_path=config_path,
                    base_config=conf,
                    include_effective_config=True,
                    validate_schema=True,
                )
                warnings_obj = resolved_conf.get("warnings")
                if isinstance(warnings_obj, list):
                    for warning in warnings_obj:
                        if isinstance(warning, Mapping):
                            logger.warning(
                                "Plugin config warning [{}] field={} msg={}",
                                warning.get("code"),
                                warning.get("field"),
                                warning.get("message"),
                            )
                conf = resolved_conf.get("effective_config")
            except HTTPException as exc:
                raise _to_domain_error(
                    code="PLUGIN_CONFIG_PROFILE_FAILED",
                    message=_detail_to_message(exc.detail, default_message="Failed to resolve plugin config"),
                    status_code=exc.status_code,
                    plugin_id=current_plugin_id,
                    error_type="HTTPException",
                ) from exc
            except IO_RUNTIME_ERRORS as exc:
                logger.warning(
                    "resolve plugin config failed: plugin_id={}, err_type={}, err={}",
                    current_plugin_id,
                    type(exc).__name__,
                    str(exc),
                )
            if not isinstance(conf, Mapping):
                raise _to_domain_error(
                    code="INVALID_PLUGIN_CONFIG",
                    message=f"Plugin '{current_plugin_id}' config is invalid after profile overlay",
                    status_code=500,
                    plugin_id=current_plugin_id,
                    error_type="InvalidConfigAfterProfile",
                )
            conf = _normalize_mapping(conf, context=f"plugin_config[{current_plugin_id}]")

            plugin_obj = conf.get("plugin")
            if not isinstance(plugin_obj, Mapping):
                raise _to_domain_error(
                    code="INVALID_PLUGIN_CONFIG",
                    message=f"Plugin '{current_plugin_id}' has invalid [plugin] section",
                    status_code=400,
                    plugin_id=current_plugin_id,
                    error_type="InvalidPluginSection",
                )
            pdata = _normalize_mapping(plugin_obj, context=f"plugin_config[{current_plugin_id}].plugin")

            runtime_obj = conf.get("plugin_runtime")
            enabled_value = True
            auto_start_value = True
            startup_timeout_value: float | None = _normalize_runtime_timeout(
                PLUGIN_STARTUP_TIMEOUT,
                plugin_id=current_plugin_id,
                setting_label="PLUGIN_STARTUP_TIMEOUT",
            )
            startup_failure_policy = "warn"
            if isinstance(runtime_obj, Mapping):
                runtime_cfg = _normalize_mapping(runtime_obj, context=f"plugin_config[{current_plugin_id}].plugin_runtime")
                enabled_value = parse_bool_config(runtime_cfg.get("enabled"), default=True)
                auto_start_value = parse_bool_config(runtime_cfg.get("auto_start"), default=True)
                if "timeout" in runtime_cfg:
                    startup_timeout_value = _normalize_runtime_timeout(
                        runtime_cfg.get("timeout"),
                        plugin_id=current_plugin_id,
                    )
                if "startup_failure" in runtime_cfg:
                    startup_failure_policy = _normalize_startup_failure_policy(
                        runtime_cfg.get("startup_failure"),
                        plugin_id=current_plugin_id,
                    )
            enabled_override = await asyncio.to_thread(
                get_runtime_override,
                current_plugin_id,
            )
            if enabled_override is not None:
                enabled_value = enabled_override
            auto_start_override = await asyncio.to_thread(
                get_runtime_auto_start_override,
                current_plugin_id,
            )
            if auto_start_override is not None:
                auto_start_value = auto_start_override
            if persist_user_intent:
                # An explicit start request is the new enabled intent. Apply it
                # in-memory for this attempt, but do not persist until startup
                # succeeds; otherwise validation/start failures become durable
                # auto-start preferences.
                enabled_value = True
                if PLUGIN_SYNC_AUTO_START_ON_TOGGLE:
                    auto_start_value = True
            if not enabled_value:
                raise _to_domain_error(
                    code="PLUGIN_DISABLED",
                    message=f"Plugin '{current_plugin_id}' is disabled by plugin_runtime.enabled and cannot be started",
                    status_code=400,
                    plugin_id=current_plugin_id,
                    error_type="PluginDisabled",
                )

            entry_obj = pdata.get("entry")
            if not isinstance(entry_obj, str) or ":" not in entry_obj:
                raise _to_domain_error(
                    code="INVALID_PLUGIN_ENTRY",
                    message=f"Invalid entry point for plugin '{current_plugin_id}'",
                    status_code=400,
                    plugin_id=current_plugin_id,
                    error_type="InvalidEntryPoint",
                )
            entry = normalize_plugin_entry_point(
                entry_obj,
                config_path=config_path,
                builtin_plugin_root=BUILTIN_PLUGIN_CONFIG_ROOT,
            )
            entry_mismatch = describe_plugin_entry_directory_mismatch(entry, config_path=config_path)
            if entry_mismatch:
                raise _to_domain_error(
                    code="PLUGIN_ENTRY_DIRECTORY_MISMATCH",
                    message=entry_mismatch,
                    status_code=400,
                    plugin_id=current_plugin_id,
                    error_type="PluginEntryDirectoryMismatch",
                )

            resolved_id = _resolve_plugin_id_conflict(
                current_plugin_id,
                logger,
                config_path=config_path,
                entry_point=entry,
                plugin_data=pdata,
                purpose="load",
            )
            if resolved_id is None:
                raise _to_domain_error(
                    code="PLUGIN_ALREADY_LOADED",
                    message=f"Plugin '{current_plugin_id}' is already loaded (duplicate detected)",
                    status_code=409,
                    plugin_id=current_plugin_id,
                    error_type="DuplicatePlugin",
                )
            current_plugin_id = resolved_id
            python_requirements = _collect_plugin_python_requirements(
                conf,
                config_path,
                logger,
                current_plugin_id,
            )
            python_requirement_paths = _collect_plugin_python_requirement_paths(config_path)
            unsatisfied_python_requirements = _find_missing_python_requirements(
                python_requirements,
                search_paths=python_requirement_paths,
            )
            if unsatisfied_python_requirements:
                raise _to_domain_error(
                    code="PLUGIN_PYTHON_DEPENDENCIES_MISSING",
                    message=(
                        f"Plugin '{current_plugin_id}' has unsatisfied Python dependencies: "
                        f"{unsatisfied_python_requirements}. Install compatible packages into the plugin vendor/ directory."
                    ),
                    status_code=400,
                    plugin_id=current_plugin_id,
                    error_type="MissingPythonDependencies",
                )

            _emit_lifecycle_event(event_type="plugin_start_requested", plugin_id=current_plugin_id)
            created_host = await asyncio.to_thread(
                PluginProcessHost,
                plugin_id=current_plugin_id,
                entry_point=entry,
                config_path=config_path,
            )
            if not isinstance(created_host, PluginHostContract):
                raise _to_domain_error(
                    code="INVALID_HOST_OBJECT",
                    message=f"Plugin '{current_plugin_id}' host object is invalid",
                    status_code=500,
                    plugin_id=current_plugin_id,
                    error_type=type(created_host).__name__,
                )
            host_obj = created_host

            dependencies = _parse_plugin_dependencies(conf, logger, current_plugin_id)
            for dep in dependencies:
                satisfied, error_message = _check_plugin_dependency(dep, logger, current_plugin_id)
                if not satisfied:
                    raise _to_domain_error(
                        code="PLUGIN_DEPENDENCY_CHECK_FAILED",
                        message=f"Plugin dependency check failed for plugin '{current_plugin_id}': {error_message}",
                        status_code=400,
                        plugin_id=current_plugin_id,
                        error_type="DependencyCheckFailed",
                    )

            startup_result = await _start_host_with_timeout(
                plugin_id=current_plugin_id,
                host_obj=host_obj,
                message_target_queue=state.message_queue,
                startup_timeout=startup_timeout_value,
                startup_failure=startup_failure_policy,
            )
            startup_error = _extract_startup_error(startup_result)
            startup_degraded = bool(startup_error) and startup_failure_policy == "warn"

            process_obj = getattr(created_host, "process", None)
            if process_obj is not None and hasattr(process_obj, "is_alive"):
                if not process_obj.is_alive():
                    exitcode_obj = getattr(process_obj, "exitcode", None)
                    exitcode_text = str(exitcode_obj) if exitcode_obj is not None else "unknown"
                    raise _to_domain_error(
                        code="PLUGIN_PROCESS_DIED_IMMEDIATELY",
                        message=(
                            f"Plugin '{current_plugin_id}' process died immediately after startup "
                            f"(exitcode: {exitcode_text})"
                        ),
                        status_code=500,
                        plugin_id=current_plugin_id,
                        error_type="ProcessDiedImmediately",
                    )

            # Mirror the startup loader: ensure the plugin's vendor/ entries
            # are on sys.path before we import its entry module here, so a
            # plugin whose top-level imports use vendored packages doesn't
            # fail this parent-process metadata scan even though the child
            # process would import it just fine.
            _ensure_python_requirement_paths(
                python_requirement_paths,
                logger,
                current_plugin_id,
            )
            module_path, class_name = entry.split(":", 1)
            module_obj = await asyncio.to_thread(_import_plugin_module, module_path, config_path, logger)
            cls_obj = getattr(module_obj, class_name)
            if not isinstance(cls_obj, type):
                raise _to_domain_error(
                    code="INVALID_PLUGIN_CLASS",
                    message=f"Plugin '{current_plugin_id}' entry class '{class_name}' is invalid",
                    status_code=500,
                    plugin_id=current_plugin_id,
                    error_type="InvalidPluginClass",
                )

            await asyncio.to_thread(scan_static_metadata, current_plugin_id, cls_obj, conf, pdata)
            entries_preview = await asyncio.to_thread(
                _extract_entries_preview,
                current_plugin_id,
                cls_obj,
                conf,
                pdata,
            )
            await asyncio.to_thread(
                _set_plugin_runtime_metadata_sync,
                current_plugin_id,
                runtime_enabled=True,
                runtime_auto_start=auto_start_value,
                entries_preview=entries_preview,
                startup_state="degraded" if startup_degraded else "ready",
                startup_error=startup_error if startup_degraded else None,
            )

            await asyncio.to_thread(_register_or_replace_host_sync, current_plugin_id, host_obj)
            registered_plugin_id = current_plugin_id

            _emit_lifecycle_event(event_type="plugin_started", plugin_id=current_plugin_id)
            response: dict[str, object] = {
                "success": True,
                "plugin_id": current_plugin_id,
                "message": "Plugin started successfully",
            }
            if startup_degraded:
                response["startup_degraded"] = True
                response["startup_error"] = startup_error
                response["message"] = "Plugin started with startup warning"
            if current_plugin_id != original_plugin_id:
                response["original_plugin_id"] = original_plugin_id
                if startup_degraded:
                    response["message"] = (
                        f"Plugin started with startup warning (renamed from '{original_plugin_id}' to "
                        f"'{current_plugin_id}' due to ID conflict)"
                    )
                else:
                    response["message"] = (
                        f"Plugin started successfully (renamed from '{original_plugin_id}' to "
                        f"'{current_plugin_id}' due to ID conflict)"
                    )
            if persist_user_intent:
                stale_plugin_ids = tuple(
                    plugin_id
                    for plugin_id in resolved_plugin_ids
                    if plugin_id != current_plugin_id
                )
                await _persist_changed_runtime_intent(
                    response,
                    current_plugin_id,
                    True,
                    previous_plugin_ids=stale_plugin_ids,
                )
            return response
        except ServerDomainError:
            if host_obj is not None:
                cleanup_plugin_id = registered_plugin_id if registered_plugin_id is not None else current_plugin_id
                await _cleanup_started_host(cleanup_plugin_id, host_obj)
            raise
        except HTTPException as exc:
            if host_obj is not None:
                cleanup_plugin_id = registered_plugin_id if registered_plugin_id is not None else current_plugin_id
                await _cleanup_started_host(cleanup_plugin_id, host_obj)
            raise _to_domain_error(
                code="PLUGIN_START_FAILED",
                message=_detail_to_message(exc.detail, default_message="start_plugin failed"),
                status_code=exc.status_code,
                plugin_id=current_plugin_id,
                error_type="HTTPException",
            ) from exc
        except PluginError as exc:
            if host_obj is not None:
                cleanup_plugin_id = registered_plugin_id if registered_plugin_id is not None else current_plugin_id
                await _cleanup_started_host(cleanup_plugin_id, host_obj)
            raise _to_domain_error(
                code="PLUGIN_START_FAILED",
                message=str(exc),
                status_code=500,
                plugin_id=current_plugin_id,
                error_type=type(exc).__name__,
            ) from exc
        except (ImportError, ModuleNotFoundError) as exc:
            if host_obj is not None:
                cleanup_plugin_id = registered_plugin_id if registered_plugin_id is not None else current_plugin_id
                await _cleanup_started_host(cleanup_plugin_id, host_obj)
            raise _to_domain_error(
                code="PLUGIN_IMPORT_FAILED",
                message=f"Failed to import plugin '{current_plugin_id}' module",
                status_code=500,
                plugin_id=current_plugin_id,
                error_type=type(exc).__name__,
            ) from exc
        except RUNTIME_ERRORS as exc:
            if host_obj is not None:
                cleanup_plugin_id = registered_plugin_id if registered_plugin_id is not None else current_plugin_id
                await _cleanup_started_host(cleanup_plugin_id, host_obj)
            raise _to_domain_error(
                code="PLUGIN_START_FAILED",
                message="start_plugin failed",
                status_code=500,
                plugin_id=current_plugin_id,
                error_type=type(exc).__name__,
            ) from exc

    async def stop_plugin(
        self,
        plugin_id: str,
        *,
        persist_user_intent: bool = False,
    ) -> dict[str, object]:
        host_obj = await asyncio.to_thread(_get_plugin_host_sync, plugin_id)
        if host_obj is None:
            raise _to_domain_error(
                code="PLUGIN_NOT_RUNNING",
                message=f"Plugin '{plugin_id}' is not running",
                status_code=404,
                plugin_id=plugin_id,
                error_type="PluginNotRunning",
            )

        if not isinstance(host_obj, PluginHostContract):
            raise _to_domain_error(
                code="INVALID_HOST_OBJECT",
                message=f"Plugin '{plugin_id}' host object is invalid",
                status_code=500,
                plugin_id=plugin_id,
                error_type=type(host_obj).__name__,
            )

        try:
            _emit_lifecycle_event(event_type="plugin_stop_requested", plugin_id=plugin_id)
            await host_obj.shutdown(timeout=PLUGIN_SHUTDOWN_TIMEOUT)
            await asyncio.to_thread(_pop_plugin_host_sync, plugin_id)
            await asyncio.to_thread(_remove_event_handlers_sync, plugin_id)
            # Clear any LLM tools the plugin had registered with
            # ``main_server``. Best-effort: a transient HTTP failure
            # here shouldn't block the rest of plugin teardown — the
            # registration helper logs the error itself. Without this
            # call, a stopped plugin's tools would linger in
            # main_server's registry until process restart, and the
            # model could still pick them only to hit a 404 on
            # dispatch.
            try:
                await clear_plugin_llm_tools(plugin_id)
            except Exception as exc:
                logger.debug(
                    "clear_plugin_llm_tools failed (best-effort): plugin_id={}, err_type={}, err={}",
                    plugin_id,
                    type(exc).__name__,
                    str(exc),
                )
            _emit_lifecycle_event(event_type="plugin_stopped", plugin_id=plugin_id)
            response: dict[str, object] = {
                "success": True,
                "plugin_id": plugin_id,
                "message": "Plugin stopped successfully",
            }
            if persist_user_intent:
                await _persist_changed_runtime_intent(
                    response,
                    plugin_id,
                    False,
                )
            return response
        except PluginError as exc:
            logger.error(
                "stop_plugin failed with PluginError: plugin_id={}, err_type={}, err={}",
                plugin_id,
                type(exc).__name__,
                str(exc),
            )
            raise _to_domain_error(
                code="PLUGIN_STOP_FAILED",
                message=str(exc),
                status_code=500,
                plugin_id=plugin_id,
                error_type=type(exc).__name__,
            ) from exc
        except RUNTIME_ERRORS as exc:
            logger.error(
                "stop_plugin failed: plugin_id={}, err_type={}, err={}",
                plugin_id,
                type(exc).__name__,
                str(exc),
            )
            raise _to_domain_error(
                code="PLUGIN_STOP_FAILED",
                message="stop_plugin failed",
                status_code=500,
                plugin_id=plugin_id,
                error_type=type(exc).__name__,
            ) from exc

    async def reload_plugin(self, plugin_id: str) -> dict[str, object]:
        _emit_lifecycle_event(event_type="plugin_reload_requested", plugin_id=plugin_id)

        is_running = await asyncio.to_thread(_plugin_is_running_sync, plugin_id)
        if is_running:
            try:
                await self.stop_plugin(plugin_id)
            except ServerDomainError as error:
                if error.status_code != 404:
                    raise

        result = await self.start_plugin(plugin_id)
        _emit_lifecycle_event(event_type="plugin_reloaded", plugin_id=plugin_id)
        return result

    async def reload_all_plugins(self) -> dict[str, object]:
        start_time = time_module.perf_counter()
        _emit_lifecycle_event(event_type="plugins_reload_all_requested")

        try:
            await plugin_registry_service.refresh_registry()
        except ServerDomainError as exc:
            raise _to_domain_error(
                code=exc.code,
                message=exc.message,
                status_code=exc.status_code,
                plugin_id=None,
                error_type="RegistryRefreshFailed",
            ) from exc

        running_plugin_ids = await asyncio.to_thread(_list_running_plugin_ids_sync)
        if not running_plugin_ids:
            return {
                "success": True,
                "reloaded": [],
                "failed": [],
                "skipped": [],
                "message": "No running plugins to reload",
            }

        stop_tasks = [self._safe_stop_for_reload(plugin_id) for plugin_id in running_plugin_ids]
        stop_outcomes = await asyncio.gather(*stop_tasks)

        plugins_to_start: list[str] = []
        failed: list[dict[str, object]] = []
        for outcome in stop_outcomes:
            if outcome.success:
                plugins_to_start.append(outcome.plugin_id)
                continue
            failed.append({"plugin_id": outcome.plugin_id, "error": outcome.error or "Stop failed"})

        reloaded: list[str] = []
        ordered_plugin_ids = await plugin_registry_service.order_plugin_ids(plugins_to_start)
        for plugin_id in ordered_plugin_ids:
            outcome = await self._safe_start_for_reload(plugin_id)
            if outcome.success:
                reloaded.append(outcome.plugin_id)
                continue
            failed.append({"plugin_id": outcome.plugin_id, "error": outcome.error or "Start failed"})

        elapsed = time_module.perf_counter() - start_time
        success = len(failed) == 0
        message: str
        if success:
            message = f"Successfully reloaded {len(reloaded)} plugins (took {elapsed:.3f}s)"
        else:
            message = f"Reloaded {len(reloaded)} plugins, {len(failed)} failed (took {elapsed:.3f}s)"

        _emit_lifecycle_event(
            event_type="plugins_reload_all_completed",
            data={
                "reloaded_count": len(reloaded),
                "failed_count": len(failed),
                "duration_seconds": round(elapsed, 3),
            },
        )

        return {
            "success": success,
            "reloaded": reloaded,
            "failed": failed,
            "skipped": [],
            "message": message,
        }

    async def switch_plugin_installation(
        self,
        plugin_id: str,
        *,
        selection_id: str,
        expected_generation: int,
        _mutation_guarded: bool = False,
    ) -> dict[str, object]:
        """Switch the selected code installation without moving user state."""

        if not _mutation_guarded:
            async with plugin_mutation_guard():
                return await self.switch_plugin_installation(
                    plugin_id,
                    selection_id=selection_id,
                    expected_generation=expected_generation,
                    _mutation_guarded=True,
                )

        try:
            before = await asyncio.to_thread(inspect_plugin_installations, plugin_id)
        except Exception as exc:
            raise _to_domain_error(
                code="PLUGIN_INSTALLATIONS_UNAVAILABLE",
                message="plugin installations could not be inspected",
                status_code=409,
                plugin_id=plugin_id,
                error_type=type(exc).__name__,
            ) from exc
        if before.generation != expected_generation:
            raise _to_domain_error(
                code="PLUGIN_INSTALLATION_SELECTION_CHANGED",
                message="available plugin installations changed; refresh and try again",
                status_code=409,
                plugin_id=plugin_id,
                error_type="InventoryGenerationChanged",
            )
        target = next(
            (candidate for candidate in before.candidates if candidate.selection_id == selection_id),
            None,
        )
        if target is None or not target.selectable:
            raise _to_domain_error(
                code="PLUGIN_INSTALLATION_NOT_SELECTABLE",
                message="requested plugin installation cannot be selected",
                status_code=409,
                plugin_id=plugin_id,
                error_type="InstallationNotSelectable",
            )
        if target.active:
            return {
                "success": True,
                "changed": False,
                "plugin_id": before.plugin_id,
                "active_selection_id": selection_id,
                "generation": before.generation,
                "restarted": False,
            }

        snapshot = await asyncio.to_thread(capture_inventory_snapshot)
        was_running = await asyncio.to_thread(_plugin_is_running_sync, plugin_id)
        inventory_changed = False

        async def _run_sync_mutation(function, /, *args, **kwargs):
            operation = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
            try:
                return await asyncio.shield(operation)
            except asyncio.CancelledError:
                try:
                    await await_cancellation_safe(operation)
                except Exception as operation_exc:
                    logger.error(
                        "plugin installation selection mutation failed while "
                        "cancellation was pending plugin_id={} err_type={}",
                        plugin_id,
                        type(operation_exc).__name__,
                    )
                raise

        async def _rollback_selection() -> list[str]:
            errors: list[str] = []
            try:
                if inventory_changed and await asyncio.to_thread(_plugin_is_running_sync, plugin_id):
                    await self.stop_plugin(plugin_id)
            except Exception as exc:
                errors.append(f"stop:{type(exc).__name__}")
            try:
                if inventory_changed:
                    await _run_sync_mutation(restore_inventory_snapshot, snapshot)
            except Exception as exc:
                errors.append(f"inventory:{type(exc).__name__}")
            try:
                if inventory_changed:
                    await plugin_registry_service.refresh_plugin(
                        plugin_id,
                        _mutation_guarded=True,
                        _recover_incomplete=False,
                    )
            except Exception as exc:
                errors.append(f"registry:{type(exc).__name__}")
            try:
                if was_running and not await asyncio.to_thread(_plugin_is_running_sync, plugin_id):
                    await self.start_plugin(
                        plugin_id,
                        refresh_registry=False,
                        persist_user_intent=False,
                    )
            except Exception as exc:
                errors.append(f"restart:{type(exc).__name__}")
            return errors

        try:
            if was_running:
                await self.stop_plugin(plugin_id)
            # Once the inventory worker starts, rollback from the exact snapshot
            # even if cancellation arrives before the worker reports its result.
            # The process-wide/cross-process guard prevents another mutation from
            # being overwritten by this operation-local restore.
            inventory_changed = True
            inventory_changed = bool(
                await _run_sync_mutation(
                    select_plugin_installation,
                    plugin_id,
                    installation_key=target.installation_key,
                    expected_generation=expected_generation,
                )
            )
            await plugin_registry_service.refresh_plugin(
                plugin_id,
                _mutation_guarded=True,
                _recover_incomplete=False,
            )
            after = await asyncio.to_thread(inspect_plugin_installations, plugin_id)
            if after.active_selection_id != selection_id:
                raise RuntimeError("selected installation was not projected")
            if was_running:
                await self.start_plugin(
                    plugin_id,
                    refresh_registry=False,
                    persist_user_intent=False,
                )
            return {
                "success": True,
                "changed": True,
                "plugin_id": after.plugin_id,
                "active_selection_id": after.active_selection_id,
                "generation": after.generation,
                "restarted": was_running,
            }
        except asyncio.CancelledError:
            await await_cancellation_safe(asyncio.create_task(_rollback_selection()))
            raise
        except Exception as exc:
            rollback_errors = await await_cancellation_safe(
                asyncio.create_task(_rollback_selection())
            )
            raise ServerDomainError(
                code=(
                    "PLUGIN_INSTALLATION_SWITCH_RECOVERY_INCOMPLETE"
                    if rollback_errors
                    else "PLUGIN_INSTALLATION_SWITCH_FAILED"
                ),
                message="plugin installation switch failed",
                status_code=500,
                details={
                    "plugin_id": plugin_id,
                    "error_type": type(exc).__name__,
                    "recovery_errors": rollback_errors,
                },
            ) from exc

    async def delete_plugin(
        self,
        plugin_id: str,
        *,
        _mutation_guarded: bool = False,
    ) -> dict[str, object]:
        if not _mutation_guarded:
            async with plugin_mutation_guard():
                return await self.delete_plugin(
                    plugin_id,
                    _mutation_guarded=True,
                )

        plugin_meta = await asyncio.to_thread(_get_plugin_meta_sync, plugin_id)
        if plugin_meta is None:
            raise _to_domain_error(
                code="PLUGIN_NOT_FOUND",
                message=f"Plugin '{plugin_id}' not found",
                status_code=404,
                plugin_id=plugin_id,
                error_type="PluginNotFound",
            )

        plugin_dir = await asyncio.to_thread(_resolve_plugin_dir_sync, plugin_id, plugin_meta)
        if plugin_dir is None:
            raise _to_domain_error(
                code="PLUGIN_CONFIG_NOT_FOUND",
                message=f"Plugin '{plugin_id}' configuration not found",
                status_code=404,
                plugin_id=plugin_id,
                error_type="ConfigNotFound",
            )

        path_allowed = await asyncio.to_thread(_path_within_plugin_roots_sync, plugin_dir)
        if not path_allowed:
            raise _to_domain_error(
                code="PLUGIN_DELETE_FORBIDDEN_PATH",
                message=f"Plugin '{plugin_id}' path is outside managed plugin roots",
                status_code=403,
                plugin_id=plugin_id,
                error_type="ForbiddenDeletePath",
            )
        root_kind = await asyncio.to_thread(_plugin_root_kind_sync, plugin_dir)
        if root_kind is None:
            raise _to_domain_error(
                code="PLUGIN_DELETE_FORBIDDEN_PATH",
                message=f"Plugin '{plugin_id}' path is outside managed plugin roots",
                status_code=403,
                plugin_id=plugin_id,
                error_type="ForbiddenDeletePath",
            )

        writable_installation = _is_writable_installation_root(root_kind)
        fallback_to_builtin = (
            writable_installation
            and await asyncio.to_thread(_builtin_plugin_exists_sync, plugin_id)
        )
        package_state_files: dict[str, str] | None = None
        if root_kind == "managed":
            package_state_files = await asyncio.to_thread(
                get_user_installation_package_state_files,
                plugin_id,
                directory_name=plugin_dir.name,
            )
        source_manager = get_install_source_manager()
        source_snapshot = source_manager.snapshot() if source_manager is not None else None
        inventory_snapshot = await asyncio.to_thread(capture_inventory_snapshot)
        is_running = await asyncio.to_thread(_plugin_is_running_sync, plugin_id)
        runtime_override = await asyncio.to_thread(get_runtime_override, plugin_id)
        runtime_auto_start = await asyncio.to_thread(
            get_runtime_auto_start_override,
            plugin_id,
        )
        deleted_from_disk = False
        rollback_snapshot: Path | None = None
        delete_journal: _DeleteJournal | None = None
        committed = False
        committed_cleanup_errors: list[str] = []
        delete_started = False
        fallback_runtime_started = False
        fallback_runtime_error: str | None = None

        async def run_sync_mutation(function, /, *args, **kwargs):
            operation = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
            try:
                return await asyncio.shield(operation)
            except asyncio.CancelledError:
                try:
                    await await_cancellation_safe(operation)
                except Exception as operation_exc:
                    logger.error(
                        "plugin delete mutation failed while cancellation was pending "
                        "plugin_id={} err_type={}",
                        plugin_id,
                        type(operation_exc).__name__,
                    )
                raise

        async def recover_delete() -> tuple[bool, bool, list[str]]:
            inventory_restored = False
            runtime_restarted = False
            recovery_errors: list[str] = []
            if writable_installation:
                try:
                    await run_sync_mutation(
                        _restore_delete_rollback_snapshot_sync,
                        plugin_dir,
                        rollback_snapshot,
                        delete_started=delete_started,
                    )
                except BaseException as recovery_exc:
                    recovery_errors.append(
                        f"filesystem_restore:{type(recovery_exc).__name__}"
                    )
            if source_manager is not None and source_snapshot is not None:
                try:
                    await run_sync_mutation(
                        source_manager.restore_snapshot_for_rollback,
                        source_snapshot,
                    )
                except BaseException as recovery_exc:
                    recovery_errors.append(
                        f"source_restore:{type(recovery_exc).__name__}"
                    )
            try:
                await run_sync_mutation(
                    restore_inventory_snapshot,
                    inventory_snapshot,
                )
                inventory_restored = True
            except BaseException as recovery_exc:
                recovery_errors.append(
                    f"inventory_restore:{type(recovery_exc).__name__}"
                )
            try:
                if runtime_override is None:
                    await run_sync_mutation(clear_runtime_override, plugin_id)
                else:
                    await run_sync_mutation(
                        set_runtime_override,
                        plugin_id,
                        runtime_override,
                        auto_start=runtime_auto_start,
                    )
            except BaseException as recovery_exc:
                recovery_errors.append(
                    f"runtime_override_restore:{type(recovery_exc).__name__}"
                )
            try:
                await plugin_registry_service.refresh_registry(
                    _mutation_guarded=True,
                )
            except BaseException as recovery_exc:
                recovery_errors.append(
                    f"registry_restore:{type(recovery_exc).__name__}"
                )
            if is_running and plugin_dir.exists():
                try:
                    await self.start_plugin(plugin_id, refresh_registry=False)
                    runtime_restarted = True
                except BaseException as recovery_exc:
                    recovery_errors.append(
                        f"runtime_restart:{type(recovery_exc).__name__}"
                    )
            if delete_journal is not None and not recovery_errors:
                try:
                    await run_sync_mutation(delete_journal.finish)
                except BaseException as recovery_exc:
                    recovery_errors.append(
                        f"journal_cleanup:{type(recovery_exc).__name__}"
                    )
            return inventory_restored, runtime_restarted, recovery_errors

        try:
            if is_running:
                await self.stop_plugin(plugin_id)
            if writable_installation:
                rollback_snapshot = _delete_rollback_snapshot_path(plugin_dir)
                delete_journal = await run_sync_mutation(
                    _DeleteJournal.create,
                    plugin_id=plugin_id,
                    plugin_dir=plugin_dir,
                    rollback_snapshot=rollback_snapshot,
                )
                snapshot_operation = asyncio.create_task(
                    asyncio.to_thread(
                        _capture_delete_rollback_snapshot_sync,
                        plugin_dir,
                        rollback_snapshot,
                    )
                )
                try:
                    rollback_snapshot = await asyncio.shield(snapshot_operation)
                except asyncio.CancelledError:
                    rollback_snapshot = await await_cancellation_safe(snapshot_operation)
                    raise
                await run_sync_mutation(delete_journal.set_phase, "precommit")
                delete_started = True
                if root_kind == "managed":
                    delete_operation = asyncio.create_task(
                        asyncio.to_thread(
                            _delete_managed_plugin_directory_sync,
                            plugin_dir,
                            package_state_files,
                        )
                    )
                else:
                    delete_operation = asyncio.create_task(
                        asyncio.to_thread(_delete_plugin_directory_sync, plugin_dir)
                    )
                try:
                    deleted_from_disk = await asyncio.shield(delete_operation)
                except asyncio.CancelledError:
                    try:
                        deleted_from_disk = await await_cancellation_safe(delete_operation)
                    except Exception as operation_exc:
                        logger.error(
                            "plugin delete worker failed while cancellation was pending "
                            "plugin_id={} err_type={}",
                            plugin_id,
                            type(operation_exc).__name__,
                        )
                    raise
                await run_sync_mutation(delete_journal.set_phase, "commit_started")
                await run_sync_mutation(remove_user_installation, plugin_id)
                if source_manager is not None:
                    await run_sync_mutation(
                        source_manager.mark_removed,
                        directory_path=plugin_dir,
                        reason="user_overlay_removed",
                    )
            else:
                await run_sync_mutation(mark_plugin_deleted, plugin_id)
            await run_sync_mutation(_pop_plugin_host_sync, plugin_id)
            await run_sync_mutation(_remove_event_handlers_sync, plugin_id)
            await run_sync_mutation(_remove_plugin_metadata_sync, plugin_id)
            await run_sync_mutation(clear_runtime_override, plugin_id)
            await plugin_registry_service.refresh_registry()
            if is_running and fallback_to_builtin:
                try:
                    await self.start_plugin(plugin_id, refresh_registry=False)
                    fallback_runtime_started = True
                except Exception as fallback_exc:
                    fallback_runtime_error = type(fallback_exc).__name__
                    raise
            if delete_journal is not None:
                await run_sync_mutation(delete_journal.set_phase, "committed")
            committed = True
            if writable_installation:
                cleanup_operation = asyncio.create_task(
                    asyncio.to_thread(
                        _finalize_delete_transaction_sync,
                        plugin_dir,
                        rollback_snapshot,
                    )
                )
                try:
                    await asyncio.shield(cleanup_operation)
                except asyncio.CancelledError as cancel_exc:
                    cleanup_errors: list[str] = []
                    try:
                        await await_cancellation_safe(cleanup_operation)
                    except Exception as cleanup_exc:
                        cleanup_errors.append(
                            f"backup_cleanup:{type(cleanup_exc).__name__}"
                        )
                    if delete_journal is not None and not cleanup_errors:
                        try:
                            await await_cancellation_safe(
                                asyncio.to_thread(delete_journal.finish)
                            )
                        except Exception as journal_exc:
                            cleanup_errors.append(
                                f"journal_finish:{type(journal_exc).__name__}"
                            )
                            try:
                                await await_cancellation_safe(
                                    asyncio.to_thread(delete_journal.ensure_persisted)
                                )
                            except Exception as preserve_exc:
                                cleanup_errors.append(
                                    f"journal_preserve:{type(preserve_exc).__name__}"
                                )
                    if cleanup_errors:
                        setattr(
                            cancel_exc,
                            "cleanup_code",
                            "PLUGIN_DELETE_COMMITTED_CLEANUP_INCOMPLETE",
                        )
                        setattr(cancel_exc, "cleanup_errors", tuple(cleanup_errors))
                        logger.error(
                            "plugin delete committed cleanup incomplete code={} "
                            "plugin_id={} errors={}",
                            "PLUGIN_DELETE_COMMITTED_CLEANUP_INCOMPLETE",
                            plugin_id,
                            ",".join(cleanup_errors),
                        )
                    raise
                except Exception as cleanup_exc:
                    committed_cleanup_errors.append(
                        f"backup_cleanup:{type(cleanup_exc).__name__}"
                    )
                    logger.warning(
                        "plugin delete backup cleanup failed plugin_id={} err_type={}",
                        plugin_id,
                        type(cleanup_exc).__name__,
                    )
                else:
                    if delete_journal is not None:
                        try:
                            await run_sync_mutation(delete_journal.finish)
                        except Exception as journal_exc:
                            committed_cleanup_errors.append(
                                f"journal_finish:{type(journal_exc).__name__}"
                            )
            if committed_cleanup_errors:
                raise ServerDomainError(
                    code="PLUGIN_DELETE_COMMITTED_CLEANUP_INCOMPLETE",
                    message=(
                        f"Plugin '{plugin_id}' was deleted, but transaction cleanup "
                        "did not finish"
                    ),
                    status_code=500,
                    details={
                        "plugin_id": plugin_id,
                        "committed": True,
                        "cleanup_errors": list(committed_cleanup_errors),
                    },
                )
        except asyncio.CancelledError:
            if committed:
                raise
            inventory_restored, runtime_restarted, recovery_errors = (
                await await_cancellation_safe(recover_delete())
            )
            logger.warning(
                "delete_plugin canceled after cleanup: plugin_id={}, "
                "inventory_restored={}, runtime_restarted={}, recovery_errors={}",
                plugin_id,
                inventory_restored,
                runtime_restarted,
                recovery_errors,
            )
            raise
        except Exception as exc:
            if committed:
                if delete_journal is not None:
                    try:
                        await await_cancellation_safe(
                            asyncio.to_thread(delete_journal.ensure_persisted)
                        )
                    except Exception as preserve_exc:
                        committed_cleanup_errors.append(
                            f"journal_preserve:{type(preserve_exc).__name__}"
                        )
                if (
                    isinstance(exc, ServerDomainError)
                    and exc.code == "PLUGIN_DELETE_COMMITTED_CLEANUP_INCOMPLETE"
                    and not any(
                        item.startswith("journal_preserve:")
                        for item in committed_cleanup_errors
                    )
                ):
                    raise
                logger.error(
                    "plugin delete committed cleanup incomplete code={} "
                    "plugin_id={} errors={}",
                    "PLUGIN_DELETE_COMMITTED_CLEANUP_INCOMPLETE",
                    plugin_id,
                    ",".join(committed_cleanup_errors),
                )
                raise ServerDomainError(
                    code="PLUGIN_DELETE_COMMITTED_CLEANUP_INCOMPLETE",
                    message=(
                        f"Plugin '{plugin_id}' was deleted, but transaction cleanup "
                        "did not finish"
                    ),
                    status_code=500,
                    details={
                        "plugin_id": plugin_id,
                        "committed": True,
                        "cleanup_errors": list(committed_cleanup_errors),
                    },
                ) from exc
            recovery_operation = asyncio.create_task(recover_delete())
            try:
                inventory_restored, runtime_restarted, recovery_errors = (
                    await asyncio.shield(recovery_operation)
                )
            except asyncio.CancelledError:
                inventory_restored, runtime_restarted, recovery_errors = (
                    await await_cancellation_safe(recovery_operation)
                )
                logger.error(
                    "delete_plugin failure recovery completed after cancellation: "
                    "plugin_id={}, original_err_type={}, recovery_errors={}",
                    plugin_id,
                    type(exc).__name__,
                    recovery_errors,
                )
                raise
            logger.error(
                "delete_plugin failed: plugin_id={}, root_kind={}, err_type={}, "
                "inventory_restored={}, runtime_restarted={}, recovery_errors={}",
                plugin_id,
                root_kind,
                type(exc).__name__,
                inventory_restored,
                runtime_restarted,
                recovery_errors,
            )
            raise ServerDomainError(
                code="PLUGIN_DELETE_FAILED",
                message=f"Failed to delete plugin '{plugin_id}'",
                status_code=500,
                details={
                    "plugin_id": plugin_id,
                    "error_type": type(exc).__name__,
                    "inventory_restored": inventory_restored,
                    "runtime_restarted": runtime_restarted,
                    "deletion_marker_retained": root_kind == "builtin",
                    "recovery_errors": recovery_errors,
                },
            ) from exc

        _emit_lifecycle_event(
            event_type="plugin_deleted",
            plugin_id=plugin_id,
            data={
                "plugin_dir": str(plugin_dir),
                "deleted_from_disk": deleted_from_disk,
                "builtin_preserved": root_kind == "builtin",
                "user_data_preserved": True,
                "deletion_scope": (
                    "user_overlay" if writable_installation else "logical_plugin"
                ),
                "installation_kind": root_kind,
                "compatibility_state_preserved": (
                    root_kind == "managed" and plugin_dir.exists()
                ),
                "fallback_to_builtin": fallback_to_builtin,
                "fallback_runtime_started": fallback_runtime_started,
                "fallback_runtime_error": fallback_runtime_error,
            },
        )
        response: dict[str, object] = {
            "success": True,
            "plugin_id": plugin_id,
            "plugin_dir": str(plugin_dir),
            "deleted_from_disk": deleted_from_disk,
            "builtin_preserved": root_kind == "builtin",
            "user_data_preserved": True,
            "deletion_scope": (
                "user_overlay" if writable_installation else "logical_plugin"
            ),
            "installation_kind": root_kind,
            "compatibility_state_preserved": (
                root_kind == "managed" and plugin_dir.exists()
            ),
            "fallback_to_builtin": fallback_to_builtin,
            "fallback_runtime_started": fallback_runtime_started,
            "fallback_runtime_error": fallback_runtime_error,
            "message": "Plugin deleted successfully",
        }
        return response

    async def _safe_stop_for_reload(self, plugin_id: str) -> _ReloadOutcome:
        try:
            await self.stop_plugin(plugin_id)
            return _ReloadOutcome(plugin_id=plugin_id, success=True)
        except ServerDomainError as error:
            if error.status_code == 404:
                return _ReloadOutcome(plugin_id=plugin_id, success=True)
            return _ReloadOutcome(plugin_id=plugin_id, success=False, error=error.message)

    async def _safe_start_for_reload(self, plugin_id: str) -> _ReloadOutcome:
        try:
            await self.start_plugin(plugin_id, refresh_registry=False)
            return _ReloadOutcome(plugin_id=plugin_id, success=True)
        except ServerDomainError as error:
            return _ReloadOutcome(plugin_id=plugin_id, success=False, error=error.message)
