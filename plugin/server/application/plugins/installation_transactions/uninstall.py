"""Transactional uninstall of one installer-owned plugin candidate."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
from typing import Literal
import uuid

from plugin.core.state import state
from plugin.logging_config import get_logger
from plugin.server.application.install_source import (
    InstallSourceManager,
    get_install_source_manager,
)
from plugin.server.application.install_source.models import LockEntry
from plugin.server.application.install_source.scanner import PluginDirectoryScanner
from plugin.server.application.plugins.operation_lock import serialized_plugin_operation
from plugin.server.application.plugins.registry_service import PluginRegistryService
from plugin.server.domain.errors import ServerDomainError
from plugin.server.infrastructure.autostart_approvals import (
    clear_autostart_pending,
    is_autostart_approved,
    mark_autostart_pending,
)
from plugin.server.infrastructure.runtime_overrides import (
    RuntimeOverride,
    clear_runtime_override,
    get_runtime_override_entry,
    restore_runtime_override,
)
from plugin.settings import (
    BUILTIN_PLUGIN_CONFIG_ROOT,
    PLUGIN_CONFIG_ROOTS,
    ensure_plugin_exec_state_roots_separated,
    get_plugin_state_root,
    get_user_plugin_config_root,
    get_user_plugin_exec_root,
)

from .ownership import UninstallOwnershipError, require_uninstall_ownership

logger = get_logger("server.application.plugins.installation_transactions.uninstall")
plugin_registry_service = PluginRegistryService()

_DEFERRED_PROFILE_CLEANUP_FILENAME = "package_profile_cleanup.json"
_CODE_BACKUP_ROOT_NAME = ".uninstall-backups"
_CODE_PAYLOAD_DIR_NAME = "payload"
_CODE_COMMIT_MARKER_FILENAME = "committed.json"
_CODE_TRANSACTION_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
# ``package_id`` allows dots, so the staged name must too; the leading dot,
# the ``.deleting-<uuid4hex>`` suffix and full-name anchoring keep it exact.
_DEFERRED_PROFILE_STAGING_NAME_PATTERN = re.compile(
    r"^\.[A-Za-z0-9._-]+\.deleting-[0-9a-f]{32}$"
)

PreferenceAction = Literal["preserved", "cleared"]
FilesystemRollback = Literal["not_needed", "completed", "incomplete"]
RuntimeRestart = Literal["not_needed", "succeeded", "failed"]


@dataclass(frozen=True, slots=True)
class UninstallPluginResult:
    plugin_id: str
    plugin_dir: Path
    deleted_from_disk: bool
    deleted_profile_dir: Path | None
    restored_builtin: bool
    preference_action: PreferenceAction
    filesystem_rollback: FilesystemRollback
    runtime_restart: RuntimeRestart
    cleanup_pending: bool
    runtime_restart_error: dict[str, str] | None = None


class UninstallPluginError(RuntimeError):
    """Stable uninstall failure with compensation outcomes kept separate."""

    def __init__(
        self,
        *,
        code: str,
        message: str,
        status_code: int,
        stage: str,
        filesystem_rollback: FilesystemRollback = "not_needed",
        runtime_restart: RuntimeRestart = "not_needed",
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.stage = stage
        self.filesystem_rollback = filesystem_rollback
        self.runtime_restart = runtime_restart
        self.details = {
            **(details or {}),
            "stage": stage,
            "filesystem_rollback": filesystem_rollback,
            "runtime_restart": runtime_restart,
        }


@dataclass(frozen=True, slots=True)
class _StagedPluginCode:
    original_dir: Path
    staged_dir: Path
    transaction_id: str


@dataclass(frozen=True, slots=True)
class _CommittedPluginCodeCleanup:
    staged: _StagedPluginCode
    marker_path: Path


@dataclass(frozen=True, slots=True)
class _RuntimePreferenceSnapshot:
    entry: RuntimeOverride | None


@dataclass(frozen=True, slots=True)
class _RegistryRefreshTarget:
    runtime_plugin_id: str
    declared_plugin_id: str
    config_paths: tuple[Path, ...]


@dataclass(frozen=True, slots=True)
class _RollbackOutcome:
    filesystem_rollback: FilesystemRollback
    runtime_restart: RuntimeRestart
    preference_restored: bool
    # Whether the plugin's own source (install record and code) is back,
    # independent of the package-profile restore in filesystem_rollback.
    source_restored: bool = True


def _get_plugin_meta_sync(plugin_id: str) -> dict[str, object] | None:
    # Registration metadata stays owned by the lifecycle module; the
    # transaction resolves it lazily to avoid an import cycle.
    from plugin.server.application.plugins.lifecycle_service import (
        _get_plugin_meta_sync as get_meta,
    )

    return get_meta(plugin_id)


def _resolve_plugin_config_path_sync(
    plugin_id: str,
    plugin_meta: dict[str, object],
) -> Path | None:
    # Resolution is shared lifecycle knowledge, not a caller-supplied callback.
    from plugin.server.application.plugins.lifecycle_service import (
        _resolve_plugin_config_path_sync as resolve,
    )

    return resolve(plugin_id, plugin_meta)


def _plugin_is_running_sync(plugin_id: str) -> bool:
    from plugin.server.application.plugins.lifecycle_service import (
        _plugin_is_running_sync as is_running,
    )

    return is_running(plugin_id)


def _path_within_plugin_roots_sync(path: Path) -> bool:
    try:
        resolved_path = path.resolve()
    except Exception:
        resolved_path = path

    resolved_exec_root = get_user_plugin_exec_root().resolve(strict=False)
    resolved_builtin_root = BUILTIN_PLUGIN_CONFIG_ROOT.resolve(strict=False)
    resolved_state_root = get_plugin_state_root().resolve(strict=False)
    if resolved_exec_root == resolved_state_root:
        return False
    allowed_roots: set[Path] = set()
    if resolved_exec_root not in {resolved_builtin_root, resolved_state_root}:
        allowed_roots.add(resolved_exec_root)
    # Preserve injected/test roots while explicitly excluding both immutable
    # builtin code and the SDK-owned persistent state root.
    for root in PLUGIN_CONFIG_ROOTS:
        resolved_root = root.resolve(strict=False)
        if resolved_root not in {resolved_builtin_root, resolved_state_root}:
            allowed_roots.add(resolved_root)
    # Deletion owns one direct child installation only. In particular, the
    # builtin root and SDK state root are never acceptable lifecycle targets.
    return resolved_path.parent in allowed_roots


def _remove_runtime_metadata_sync(plugin_id: str) -> None:
    from plugin.server.application.plugins.lifecycle_service import (
        _pop_plugin_host_sync,
        _remove_event_handlers_sync,
    )

    _pop_plugin_host_sync(plugin_id)
    _remove_event_handlers_sync(plugin_id)
    removed = False
    with state.acquire_plugins_write_lock():
        if plugin_id in state.plugins:
            state.plugins.pop(plugin_id, None)
            removed = True
    if removed:
        state.invalidate_snapshot_cache("plugins")


async def _stop_plugin(plugin_id: str) -> None:
    from plugin.server.application.plugins.lifecycle_service import (
        PluginLifecycleService,
    )

    await PluginLifecycleService().stop_plugin(plugin_id)


async def _start_plugin(plugin_id: str) -> None:
    from plugin.server.application.plugins.lifecycle_service import (
        PluginLifecycleService,
    )

    await PluginLifecycleService().start_plugin(plugin_id, refresh_registry=False)


def _path_has_symlink_ancestor(path: Path) -> bool:
    """Reject a path when resolving it would traverse a symlink."""
    return any(candidate.is_symlink() for candidate in (path, *path.parents))


def _deferred_profile_cleanup_record_path_sync() -> Path:
    return (
        get_plugin_state_root().expanduser().resolve().parent
        / _DEFERRED_PROFILE_CLEANUP_FILENAME
    )


def _legacy_deferred_profile_cleanup_record_path_sync() -> Path:
    return (
        get_user_plugin_config_root().expanduser().resolve().parent
        / _DEFERRED_PROFILE_CLEANUP_FILENAME
    )


def _load_deferred_profile_cleanup_paths_sync(record_path: Path) -> list[str] | None:
    """Return the pending paths, or ``None`` when an existing record is unusable.

    Callers must not overwrite a record they could not read: the paths already
    queued in it would be dropped and their staging directories would then be
    retained forever.
    """
    try:
        raw = json.loads(record_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError, TypeError) as exc:
        logger.error(
            "uninstall: failed to read deferred profile cleanup record {}: {}",
            record_path,
            exc,
        )
        return None
    if not isinstance(raw, dict) or not isinstance(raw.get("staged_paths"), list):
        logger.error(
            "uninstall: invalid deferred profile cleanup record: {}", record_path
        )
        return None
    return [path for path in raw["staged_paths"] if isinstance(path, str) and path]


def _save_deferred_profile_cleanup_paths_sync(
    record_path: Path, paths: list[str]
) -> None:
    if not paths:
        try:
            record_path.unlink()
        except FileNotFoundError:
            pass
        return
    record_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = record_path.with_name(
        f".{record_path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    )
    try:
        temporary_path.write_text(
            json.dumps({"schema_version": 1, "staged_paths": paths}),
            encoding="utf-8",
        )
        temporary_path.replace(record_path)
    finally:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass


def _is_safe_deferred_profile_cleanup_path(path: Path) -> bool:
    return (
        path.is_absolute()
        and _DEFERRED_PROFILE_STAGING_NAME_PATTERN.fullmatch(path.name) is not None
        and not _path_has_symlink_ancestor(path)
    )


def retry_deferred_profile_cleanup_sync() -> int:
    """Finish legacy committed cleanup jobs; new uninstalls retain profile exports."""
    record_path = _deferred_profile_cleanup_record_path_sync()
    record_paths = [record_path]
    legacy_record_path = _legacy_deferred_profile_cleanup_record_path_sync()
    if legacy_record_path != record_path and legacy_record_path.exists():
        record_paths.append(legacy_record_path)

    paths: list[str] = []
    for candidate in record_paths:
        loaded = _load_deferred_profile_cleanup_paths_sync(candidate)
        if loaded is None:
            return 0
        for raw_path in loaded:
            if raw_path not in paths:
                paths.append(raw_path)
    if not paths:
        return 0

    remaining_paths: list[str] = []
    cleaned = 0
    for raw_path in paths:
        staged_path = Path(raw_path).expanduser()
        if not _is_safe_deferred_profile_cleanup_path(staged_path):
            logger.error(
                "uninstall: refusing unsafe deferred profile cleanup path: {}",
                staged_path,
            )
            remaining_paths.append(raw_path)
            continue
        try:
            shutil.rmtree(staged_path)
        except FileNotFoundError:
            cleaned += 1
        except OSError as exc:
            logger.warning(
                "uninstall: deferred profile cleanup still pending for {}: {}",
                staged_path,
                exc,
            )
            remaining_paths.append(raw_path)
        else:
            cleaned += 1
    try:
        _save_deferred_profile_cleanup_paths_sync(record_path, remaining_paths)
    except OSError as exc:
        logger.error(
            "uninstall: failed to update deferred profile cleanup record {}: {}",
            record_path,
            exc,
        )
    else:
        for legacy_path in record_paths[1:]:
            try:
                legacy_path.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                logger.warning(
                    "uninstall: failed to remove migrated cleanup record {}: {}",
                    legacy_path,
                    exc,
                )
    return cleaned


def _stage_plugin_code_sync(plugin_dir: Path) -> _StagedPluginCode:
    """Stage the code directory with a same-filesystem rename, never rmtree."""
    transaction_id = uuid.uuid4().hex
    backup_root = plugin_dir.parent / _CODE_BACKUP_ROOT_NAME
    transaction_dir = backup_root / transaction_id
    backup_root.mkdir(exist_ok=True)
    if (
        backup_root.is_symlink()
        or backup_root.resolve(strict=False).parent
        != plugin_dir.parent.resolve(strict=False)
    ):
        raise ValueError("uninstall code backup root is not a direct local directory")
    transaction_dir.mkdir(exist_ok=False)
    payload_dir = transaction_dir / _CODE_PAYLOAD_DIR_NAME
    payload_dir.mkdir()
    staged_dir = payload_dir / plugin_dir.name
    try:
        plugin_dir.replace(staged_dir)
    except BaseException:
        try:
            payload_dir.rmdir()
            transaction_dir.rmdir()
            backup_root.rmdir()
        except OSError:
            pass
        raise
    return _StagedPluginCode(plugin_dir, staged_dir, transaction_id)


def _restore_staged_plugin_code_sync(staged: _StagedPluginCode) -> None:
    # staged exists only after a successful move; missing staging means old code cannot be restored and must fail,
    # or rollback falsely records source restoration. Still clean up, then raise.
    staged_missing = not staged.staged_dir.exists()
    if not staged_missing:
        staged.staged_dir.replace(staged.original_dir)
    payload_dir = staged.staged_dir.parent
    transaction_dir = payload_dir.parent
    backup_root = transaction_dir.parent
    try:
        (transaction_dir / _CODE_COMMIT_MARKER_FILENAME).unlink()
    except FileNotFoundError:
        pass
    try:
        payload_dir.rmdir()
        transaction_dir.rmdir()
        backup_root.rmdir()
    except OSError:
        pass
    if staged_missing:
        raise FileNotFoundError(f"staged plugin code missing: {staged.original_dir.name}")


def _commit_staged_plugin_code_sync(
    staged: _StagedPluginCode,
) -> _CommittedPluginCodeCleanup:
    marker_path = staged.staged_dir.parent.parent / _CODE_COMMIT_MARKER_FILENAME
    temporary_path = marker_path.with_name(
        f".{marker_path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    )
    marker = {
        "schema_version": 1,
        "transaction_id": staged.transaction_id,
        "original_dir": str(staged.original_dir),
        "staged_dir": str(staged.staged_dir),
    }
    try:
        temporary_path.write_text(json.dumps(marker), encoding="utf-8")
        temporary_path.replace(marker_path)
    finally:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass
    return _CommittedPluginCodeCleanup(staged=staged, marker_path=marker_path)


def _load_committed_plugin_code_cleanup_sync(
    transaction_dir: Path,
    *,
    backup_root: Path,
) -> _CommittedPluginCodeCleanup | None:
    if (
        transaction_dir.parent != backup_root
        or _CODE_TRANSACTION_ID_PATTERN.fullmatch(transaction_dir.name) is None
        or _path_has_symlink_ancestor(transaction_dir)
    ):
        return None
    marker_path = transaction_dir / _CODE_COMMIT_MARKER_FILENAME
    try:
        raw = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        return None
    transaction_id = raw.get("transaction_id")
    original_dir_raw = raw.get("original_dir")
    staged_dir_raw = raw.get("staged_dir")
    if (
        transaction_id != transaction_dir.name
        or not isinstance(original_dir_raw, str)
        or not isinstance(staged_dir_raw, str)
    ):
        return None
    original_dir = Path(original_dir_raw)
    staged_dir = Path(staged_dir_raw)
    if (
        not original_dir.is_absolute()
        or not staged_dir.is_absolute()
        or original_dir.parent != backup_root.parent
        or staged_dir
        != transaction_dir / _CODE_PAYLOAD_DIR_NAME / original_dir.name
    ):
        return None
    staged = _StagedPluginCode(
        original_dir=original_dir,
        staged_dir=staged_dir,
        transaction_id=transaction_id,
    )
    return _CommittedPluginCodeCleanup(staged=staged, marker_path=marker_path)


def _finalize_committed_plugin_code_sync(
    committed: _CommittedPluginCodeCleanup,
) -> bool:
    """Remove code only from this transaction's own committed staging artifact."""
    staged = committed.staged
    backup_root = staged.original_dir.parent / _CODE_BACKUP_ROOT_NAME
    transaction_dir = backup_root / staged.transaction_id
    if (
        staged.staged_dir
        != transaction_dir / _CODE_PAYLOAD_DIR_NAME / staged.original_dir.name
        or committed.marker_path != transaction_dir / _CODE_COMMIT_MARKER_FILENAME
        or _load_committed_plugin_code_cleanup_sync(
            transaction_dir,
            backup_root=backup_root,
        )
        != committed
    ):
        raise ValueError(
            "uninstall code cleanup marker does not match staged directory"
        )
    # Keep the marker outside the recursive delete target.  ``rmtree`` may
    # partially remove a locked tree before raising; retaining the marker lets
    # startup authenticate and retry the remaining payload safely.
    try:
        shutil.rmtree(staged.staged_dir)
    except FileNotFoundError:
        pass
    try:
        staged.staged_dir.parent.rmdir()
    except FileNotFoundError:
        pass
    committed.marker_path.unlink(missing_ok=True)
    try:
        transaction_dir.rmdir()
    except FileNotFoundError:
        pass
    try:
        backup_root.rmdir()
    except OSError:
        pass
    return True


def retry_deferred_plugin_code_cleanup_sync() -> int:
    """Retry only committed code backups under transaction-owned roots."""
    candidate_roots = {get_user_plugin_exec_root(), *PLUGIN_CONFIG_ROOTS}
    builtin_root = BUILTIN_PLUGIN_CONFIG_ROOT.resolve(strict=False)
    state_root = get_plugin_state_root().resolve(strict=False)
    cleaned = 0
    for candidate_root in candidate_roots:
        root = candidate_root.resolve(strict=False)
        if root in {builtin_root, state_root}:
            continue
        backup_root = root / _CODE_BACKUP_ROOT_NAME
        try:
            transaction_dirs = tuple(backup_root.iterdir())
        except (FileNotFoundError, NotADirectoryError):
            continue
        except OSError as exc:
            logger.warning(
                "uninstall: failed to inspect deferred code cleanup root {}: {}",
                backup_root,
                exc,
            )
            continue
        for transaction_dir in transaction_dirs:
            committed = _load_committed_plugin_code_cleanup_sync(
                transaction_dir,
                backup_root=backup_root,
            )
            if committed is None:
                logger.error(
                    "uninstall: refusing unmarked deferred code cleanup path: {}",
                    transaction_dir,
                )
                continue
            try:
                if _finalize_committed_plugin_code_sync(committed):
                    cleaned += 1
            except (OSError, ValueError) as exc:
                logger.warning(
                    "uninstall: deferred code cleanup still pending for {}: {}",
                    transaction_dir,
                    exc,
                )
    return cleaned


def _snapshot_runtime_preference(plugin_id: str) -> _RuntimePreferenceSnapshot:
    return _RuntimePreferenceSnapshot(
        entry=get_runtime_override_entry(plugin_id),
    )


def _restore_runtime_preference(
    plugin_id: str, snapshot: _RuntimePreferenceSnapshot
) -> None:
    restored = restore_runtime_override(
        plugin_id,
        snapshot.entry,
        expected_current=None,
    )
    if not restored:
        logger.info(
            "uninstall rollback kept a newer runtime preference plugin_id={}",
            plugin_id,
        )


def _registry_refresh_target(
    *,
    runtime_plugin_id: str,
    source_entry: LockEntry,
    plugin_meta: dict[str, object],
    config_path: Path,
) -> _RegistryRefreshTarget:
    declared_plugin_id = source_entry.plugin_id.strip()
    if not declared_plugin_id:
        declared_plugin_id = (
            PluginDirectoryScanner._load_plugin_id(config_path.parent)
            or runtime_plugin_id
        )

    config_paths = [config_path]
    shadowed_builtin_path = plugin_meta.get("shadowed_builtin_path")
    if isinstance(shadowed_builtin_path, str) and shadowed_builtin_path:
        config_paths.append(Path(shadowed_builtin_path))
    config_paths.append(
        BUILTIN_PLUGIN_CONFIG_ROOT / declared_plugin_id / config_path.name
    )
    return _RegistryRefreshTarget(
        runtime_plugin_id=runtime_plugin_id,
        declared_plugin_id=declared_plugin_id,
        config_paths=tuple(config_paths),
    )


def _registry_failure_matches_target(
    failure: object,
    *,
    target: _RegistryRefreshTarget,
) -> bool:
    if not isinstance(failure, dict):
        return False

    failure_config_path = failure.get("config_path")
    if isinstance(failure_config_path, str) and failure_config_path:
        try:
            resolved_failure_path = Path(failure_config_path).resolve(strict=False)
        except (OSError, RuntimeError):
            resolved_failure_path = Path(failure_config_path)
        for config_path in target.config_paths:
            try:
                resolved_target_path = config_path.resolve(strict=False)
            except (OSError, RuntimeError):
                resolved_target_path = config_path
            if resolved_failure_path == resolved_target_path:
                return True
        return False

    failure_plugin_id = failure.get("plugin_id")
    return isinstance(failure_plugin_id, str) and failure_plugin_id in {
        target.runtime_plugin_id,
        target.declared_plugin_id,
    }


async def _refresh_registry(
    target: _RegistryRefreshTarget,
) -> dict[str, object]:
    # This previously cleared metadata scan cache explicitly because cache keys saw only plugin-directory contents,
    # not shared vendor/ or site-packages dependencies. With caching removed, every refresh rereads
    # disk state, so refresh directly.
    result = await plugin_registry_service.refresh_registry()
    failed = result.get("failed")
    if isinstance(failed, list) and any(
        _registry_failure_matches_target(failure, target=target)
        for failure in failed
    ):
        raise RuntimeError("plugin registry refresh failed for uninstall target")
    return result


async def _restore_original_runtime(
    plugin_id: str, *, was_running: bool
) -> RuntimeRestart:
    if not was_running:
        return "not_needed"
    try:
        if await asyncio.to_thread(_plugin_is_running_sync, plugin_id):
            return "succeeded"
        await _start_plugin(plugin_id)
    except Exception:
        logger.exception(
            "uninstall rollback runtime restart failed plugin_id={}", plugin_id
        )
        return "failed"
    return "succeeded"


async def _rollback_precommit(
    *,
    plugin_id: str,
    manager: InstallSourceManager,
    source_entry: LockEntry,
    source_update_attempted: bool,
    staged_code: _StagedPluginCode | None,
    preference_snapshot: _RuntimePreferenceSnapshot,
    preference_update_attempted: bool,
    was_running: bool,
    stop_attempted: bool,
    registry_target: _RegistryRefreshTarget,
) -> _RollbackOutcome:
    """Compensate in reverse stage order: source, code, preference,
    registry, then the original runtime. Each step is best effort."""
    filesystem_changed = (
        source_update_attempted or staged_code is not None
    )
    filesystem_complete = True
    source_restored = True
    if source_update_attempted:
        try:
            await asyncio.to_thread(manager.restore_entry_for_rollback, source_entry)
        except Exception:
            filesystem_complete = False
            source_restored = False
            logger.exception(
                "uninstall rollback failed to restore source entry plugin_id={}",
                plugin_id,
            )
    if staged_code is not None:
        try:
            await asyncio.to_thread(_restore_staged_plugin_code_sync, staged_code)
        except Exception:
            filesystem_complete = False
            source_restored = False
            logger.exception(
                "uninstall rollback failed to restore code plugin_id={}", plugin_id
            )
    preference_restored = True
    if preference_update_attempted:
        try:
            await asyncio.to_thread(
                _restore_runtime_preference, plugin_id, preference_snapshot
            )
        except Exception:
            preference_restored = False
            logger.exception(
                "uninstall rollback failed to restore preference plugin_id={}",
                plugin_id,
            )
    if filesystem_changed:
        try:
            await _refresh_registry(registry_target)
        except Exception:
            # A refresh failure does not undo the physical restores above;
            # the scan itself is not part of the filesystem rollback report.
            logger.exception(
                "uninstall rollback registry refresh failed plugin_id={}",
                plugin_id,
            )
    runtime_restart = await _restore_original_runtime(
        plugin_id,
        was_running=was_running and stop_attempted,
    )
    filesystem_rollback: FilesystemRollback = (
        "not_needed"
        if not filesystem_changed
        else ("completed" if filesystem_complete else "incomplete")
    )
    return _RollbackOutcome(
        filesystem_rollback=filesystem_rollback,
        runtime_restart=runtime_restart,
        preference_restored=preference_restored,
        source_restored=source_restored,
    )


@serialized_plugin_operation
async def uninstall_plugin(plugin_id: str) -> UninstallPluginResult:
    """Uninstall the exact managed user candidate with a pre-commit rollback."""

    stage = "preflight"
    try:
        await asyncio.to_thread(ensure_plugin_exec_state_roots_separated)
    except ValueError as exc:
        if getattr(exc, "code", "") == "PLUGIN_EXEC_STATE_ROOT_COLLISION":
            raise UninstallPluginError(
                code="PLUGIN_EXEC_STATE_ROOT_COLLISION",
                message=str(exc),
                status_code=409,
                stage=stage,
                details={"plugin_id": plugin_id, "error_type": type(exc).__name__},
            ) from exc
        raise

    plugin_meta = await asyncio.to_thread(_get_plugin_meta_sync, plugin_id)
    if plugin_meta is None:
        raise UninstallPluginError(
            code="PLUGIN_NOT_FOUND",
            message=f"Plugin '{plugin_id}' not found",
            status_code=404,
            stage=stage,
            details={"plugin_id": plugin_id, "error_type": "PluginNotFound"},
        )
    config_path = await asyncio.to_thread(
        _resolve_plugin_config_path_sync, plugin_id, plugin_meta
    )
    if config_path is None:
        raise UninstallPluginError(
            code="PLUGIN_CONFIG_NOT_FOUND",
            message=f"Plugin '{plugin_id}' configuration not found",
            status_code=404,
            stage=stage,
            details={"plugin_id": plugin_id, "error_type": "ConfigNotFound"},
        )
    plugin_dir = config_path.parent

    manager = get_install_source_manager()
    stage = "ownership"
    try:
        if manager is not None:
            reload_install_source = getattr(manager, "load", None)
            if callable(reload_install_source):
                await asyncio.to_thread(reload_install_source)
        source_entry = await asyncio.to_thread(
            require_uninstall_ownership,
            manager=manager,
            runtime_plugin_id=plugin_id,
            config_path=config_path,
        )
    except UninstallOwnershipError as exc:
        raise UninstallPluginError(
            code=exc.code,
            message=exc.message,
            status_code=exc.status_code,
            stage=stage,
            details={**exc.details, "error_type": type(exc).__name__},
        ) from exc
    assert manager is not None

    registry_target = await asyncio.to_thread(
        _registry_refresh_target,
        runtime_plugin_id=plugin_id,
        source_entry=source_entry,
        plugin_meta=plugin_meta,
        config_path=config_path,
    )

    # The existing path guard stays an independent second check: it must not
    # mask the ownership refusal with a generic forbidden-path error.
    if not await asyncio.to_thread(_path_within_plugin_roots_sync, plugin_dir):
        raise UninstallPluginError(
            code="PLUGIN_DELETE_FORBIDDEN_PATH",
            message=f"Plugin '{plugin_id}' path is outside managed plugin roots",
            status_code=403,
            stage="preflight",
            details={"plugin_id": plugin_id, "error_type": "ForbiddenDeletePath"},
        )

    was_running = await asyncio.to_thread(_plugin_is_running_sync, plugin_id)
    preference_snapshot = await asyncio.to_thread(
        _snapshot_runtime_preference, plugin_id
    )
    stop_attempted = False
    staged_code: _StagedPluginCode | None = None
    source_update_attempted = False
    preference_update_attempted = False
    autostart_was_pending = False
    autostart_gate_rollback: str | None = None
    try:
        stage = "stop"
        if was_running:
            stop_attempted = True
            await _stop_plugin(plugin_id)

        # Package profiles are legacy export artifacts, not runtime state.
        # Retain existing directories without staging or deleting them.
        stage = "stage_code"
        staged_code = await asyncio.to_thread(_stage_plugin_code_sync, plugin_dir)

        stage = "update_source"
        source_update_attempted = True
        await asyncio.to_thread(manager.mark_removed, directory_path=plugin_dir)

        stage = "refresh_and_preferences"
        await asyncio.to_thread(_remove_runtime_metadata_sync, plugin_id)
        await _refresh_registry(registry_target)
        restored_meta = await asyncio.to_thread(_get_plugin_meta_sync, plugin_id)
        restored_builtin = bool(
            restored_meta and restored_meta.get("effective_source") == "builtin"
        )
        # After uninstall, pending approval is necessarily stale because it referred to removed code.
        # Retaining it keeps blocking the restored built-in plugin that the user already autostarted;
        # if the plugin is removed entirely, it remains on the ID and wrongly blocks future reinstalls (codex).
        #
        # Record the old state before clearing: subsequent precommit steps can fail,
        # and _rollback_precommit restores plugin files/preferences without knowing the approval bit changed.
        # A never-started new plugin would then become approved after rollback and autostart next launch
        # (codex), so restore it in the except branch below.
        autostart_was_pending = not await asyncio.to_thread(
            is_autostart_approved, plugin_id
        )
        if not await asyncio.to_thread(clear_autostart_pending, plugin_id):
            # Failure to clear means failure overall. The old disk record keeps blocking the restored built-in plugin,
            # and same-ID reinstalls inherit it too (coderabbit). Raise into the existing precommit
            # rollback to restore the uninstall rather than leave ambiguous state.
            raise UninstallPluginError(
                code="PLUGIN_AUTOSTART_APPROVAL_PERSIST_FAILED",
                message=(
                    "uninstall could not clear the plugin's autostart approval "
                    "record; refusing to commit"
                ),
                status_code=500,
                stage=stage,
            )
        preference_action: PreferenceAction = (
            "preserved" if restored_builtin else "cleared"
        )
        if not restored_builtin:
            preference_update_attempted = True
            await asyncio.to_thread(clear_runtime_override, plugin_id)

        committed_code = await asyncio.to_thread(
            _commit_staged_plugin_code_sync,
            staged_code,
        )
    except BaseException as exc:
        rollback = await _rollback_precommit(
            plugin_id=plugin_id,
            manager=manager,
            source_entry=source_entry,
            source_update_attempted=source_update_attempted,
            staged_code=staged_code,
            preference_snapshot=preference_snapshot,
            preference_update_attempted=preference_update_attempted,
            was_running=was_running,
            stop_attempted=stop_attempted,
            registry_target=registry_target,
        )
        if not rollback.source_restored:
            # If original source cannot be restored exactly, recovery permission must not cover whatever remains on disk. Check only
            # plugin source; incomplete package-config restoration does not change which code is present.
            from plugin.server.application.plugins.lifecycle_service import revoke_hot_reload_recovery

            revoke_hot_reload_recovery(plugin_id)
        if autostart_was_pending and await asyncio.to_thread(plugin_dir.exists):
            # When rollback restores plugin files, restore pending approval too. Otherwise a plugin the user
            # never started becomes approved after a failed uninstall and automatically
            # runs next launch (codex). Apply the shared criterion: inspect disk, not intent; if code really is gone,
            # do not restore a record that would wrongly block a future plugin using this ID.
            if not await asyncio.to_thread(mark_autostart_pending, plugin_id):
                # Reraising would replace the real failure reason, so do not raise here; logging alone is also insufficient.
                # Return compensation failure with rollback results, matching preference_rollback's
                # shape (coderabbit). Its additional request to persist recoverable blocking
                # state is not implemented: the only available persistence store just failed to write.
                autostart_gate_rollback = "incomplete"
                logger.error(
                    "uninstall rollback restored plugin {} but could not restore "
                    "its pending-approval record; it may autostart without having "
                    "been started by the user",
                    plugin_id,
                )
        gate_details = (
            {} if autostart_gate_rollback is None
            else {"autostart_gate_rollback": autostart_gate_rollback}
        )
        if isinstance(exc, UninstallPluginError):
            exc.details.update(gate_details)
        if not isinstance(exc, Exception) or isinstance(exc, UninstallPluginError):
            raise
        if isinstance(exc, ServerDomainError):
            # Structured domain failures (e.g. stop_plugin) keep their stable
            # public code; compensation outcomes ride along in details.
            raise UninstallPluginError(
                code=exc.code,
                message=exc.message,
                status_code=exc.status_code,
                stage=stage,
                filesystem_rollback=rollback.filesystem_rollback,
                runtime_restart=rollback.runtime_restart,
                details={
                    **exc.details,
                    "error_type": type(exc).__name__,
                    **(
                        {}
                        if rollback.preference_restored
                        else {"preference_rollback": "incomplete"}
                    ),
                    **gate_details,
                },
            ) from exc
        raise UninstallPluginError(
            code="PLUGIN_DELETE_FAILED",
            message=f"Failed to delete plugin '{plugin_id}'",
            status_code=500,
            stage=stage,
            filesystem_rollback=rollback.filesystem_rollback,
            runtime_restart=rollback.runtime_restart,
            details={
                "plugin_id": plugin_id,
                "error_type": type(exc).__name__,
                **(
                    {}
                    if rollback.preference_restored
                    else {"preference_rollback": "incomplete"}
                ),
                **gate_details,
            },
        ) from exc

    # ---- COMMIT: source record, registry facts and preference contract are
    # consistent and the original user path is no longer valid. Failures from
    # here on are reported, never presented as a rolled-back uninstall. ----
    from plugin.server.application.plugins.lifecycle_service import revoke_hot_reload_recovery

    # Stopped plugins do not pass through stop_plugin; revoke recovery permission after commit so restored
    # same-ID built-ins or reinstalls cannot inherit it. Keep it on rollback, which restores the original source exactly.
    revoke_hot_reload_recovery(plugin_id)
    runtime_restart: RuntimeRestart = "not_needed"
    runtime_restart_error: dict[str, str] | None = None
    if was_running and restored_builtin:
        try:
            await _start_plugin(plugin_id)
            runtime_restart = "succeeded"
        except Exception as exc:
            runtime_restart = "failed"
            runtime_restart_error = {
                "code": "PLUGIN_BUILTIN_RESTORE_START_FAILED",
                "message": str(exc),
                "error_type": type(exc).__name__,
            }
            logger.error(
                "uninstall committed but builtin restart failed plugin_id={} err_type={}",
                plugin_id,
                type(exc).__name__,
            )

    cleanup_pending = False
    try:
        await asyncio.to_thread(_finalize_committed_plugin_code_sync, committed_code)
    except Exception:
        cleanup_pending = True
        logger.exception(
            "uninstall committed but code cleanup is pending plugin_id={}", plugin_id
        )
    return UninstallPluginResult(
        plugin_id=plugin_id,
        plugin_dir=plugin_dir,
        deleted_from_disk=True,
        deleted_profile_dir=None,
        restored_builtin=restored_builtin,
        preference_action=preference_action,
        filesystem_rollback="not_needed",
        runtime_restart=runtime_restart,
        cleanup_pending=cleanup_pending,
        runtime_restart_error=runtime_restart_error,
    )
