from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
import shutil
from typing import Mapping, TypeVar
import uuid

from plugin.core.plugin_layout import PluginLayout
from plugin.logging_config import get_logger
from plugin.server.application.plugins.package_ownership import (
    PackageStateConflictError,
    sha256_file,
)
from plugin.server.domain.errors import ServerDomainError
from plugin.server.infrastructure.config_paths import ensure_plugin_layout_runtime_config
from plugin.server.infrastructure.path_safety import is_link_or_reparse_point

logger = get_logger("server.application.plugins.upgrade_support")
_PLUGIN_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")

_MANIFEST_ADJACENT_PROFILE_PATHS = (Path("profiles.toml"), Path("profiles"))
_CleanupResult = TypeVar("_CleanupResult")


def fsync_parent_directory(path: Path) -> None:
    """Persist a replaced directory entry on platforms with directory fsync."""

    if os.name == "nt":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path.parent, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


async def await_cancellation_safe(
    operation: Awaitable[_CleanupResult],
) -> _CleanupResult:
    """Finish cleanup even when the awaiting task is already canceled."""

    cleanup = asyncio.ensure_future(operation)
    while True:
        try:
            return await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            if cleanup.done():
                return cleanup.result()


async def _run_thread_mutation(function, /, *args, **kwargs):
    operation = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(operation)
    except asyncio.CancelledError:
        try:
            await await_cancellation_safe(operation)
        except Exception as operation_exc:
            logger.error(
                "plugin file mutation failed while cancellation was pending "
                "err_type={}",
                type(operation_exc).__name__,
            )
        raise


@dataclass(frozen=True, slots=True)
class ReplacePluginResult:
    restarted: bool
    rollback_status: str
    install_result: dict[str, object]
    backup_dir: Path


class ReplacePluginError(RuntimeError):
    def __init__(
        self,
        *,
        stage: str,
        rollback_status: str,
        cause: Exception,
        committed: bool = False,
    ) -> None:
        super().__init__(f"{stage} failed: {cause}")
        self.stage = stage
        self.rollback_status = rollback_status
        self.cause = cause
        self.committed = committed


@dataclass(frozen=True, slots=True)
class ReplacementRecoveryResult:
    recovered_operation_ids: tuple[str, ...]
    manual_recovery_operation_ids: tuple[str, ...]
    manual_recovery_plugin_ids: tuple[str, ...] = ()
    block_user_plugin_root: bool = False


class _ReplacementJournal:
    def __init__(
        self,
        *,
        path: Path,
        operation_id: str,
        plugin_id: str,
        targets: tuple[dict[str, object], ...],
    ) -> None:
        self.path = path
        self.owner_path = path.with_suffix(".owner")
        self.state: dict[str, object] = {
            "schema_version": 1,
            "operation_id": operation_id,
            "plugin_id": plugin_id,
            "phase": "precommit",
            "targets": [dict(item) for item in targets],
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
        journal_root: Path,
        targets: tuple[Path, ...],
        backups: Mapping[Path, Path],
        preexisting_targets: frozenset[Path],
    ) -> _ReplacementJournal:
        operation_id = uuid.uuid4().hex
        journal_root.mkdir(parents=True, exist_ok=True)
        records = tuple(
            {
                "target": str(target.resolve(strict=False)),
                "backup": str(backups[target].resolve(strict=False)),
                "preexisting": target in preexisting_targets,
                "moved": False,
            }
            for target in targets
        )
        return cls(
            path=journal_root / f"{operation_id}.json",
            operation_id=operation_id,
            plugin_id=plugin_id,
            targets=records,
        )

    def mark_moved(self, target: Path) -> None:
        resolved = str(target.resolve(strict=False))
        for item in self.state["targets"]:  # type: ignore[index]
            if isinstance(item, dict) and item.get("target") == resolved:
                item["moved"] = True
                self._write()
                return
        raise RuntimeError(f"replacement journal target is missing: {target.name}")

    def set_phase(self, phase: str) -> None:
        self.state["phase"] = phase
        self._write()

    def finish(self) -> None:
        self.path.unlink(missing_ok=True)
        self.owner_path.unlink(missing_ok=True)
        try:
            self.path.parent.rmdir()
        except OSError:
            pass

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
                    "kind": "replacement",
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


def _remove_path_sync(path: Path) -> None:
    if path.is_dir() and not is_link_or_reparse_point(path):
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def _journal_target_records(
    raw: object,
    *,
    allowed_roots: tuple[Path, ...] | None,
    plugin_id: str,
) -> tuple[dict[str, object], ...]:
    if not isinstance(raw, list) or not raw:
        raise ValueError("replacement journal targets are missing")
    resolved_allowed = (
        tuple(root.resolve(strict=False) for root in allowed_roots)
        if allowed_roots is not None
        else None
    )
    records: list[dict[str, object]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("replacement journal target is invalid")
        target_raw = item.get("target")
        backup_raw = item.get("backup")
        if not isinstance(target_raw, str) or not isinstance(backup_raw, str):
            raise ValueError("replacement journal paths are invalid")
        target = Path(target_raw).resolve(strict=False)
        backup = Path(backup_raw).resolve(strict=False)
        if resolved_allowed is not None and not any(
            target == root or root in target.parents for root in resolved_allowed
        ):
            raise ValueError("replacement journal target is outside managed roots")
        if backup.parent.name != ".upgrade-backups":
            raise ValueError("replacement journal backup root is invalid")
        if backup.parent.parent != target.parent:
            raise ValueError("replacement journal target and backup roots differ")
        if not backup.name.startswith(f"{target.name}.bak."):
            raise ValueError("replacement journal backup name is invalid")
        records.append(
            {
                "target": target,
                "backup": backup,
                "preexisting": item.get("preexisting") is True,
                "moved": item.get("moved") is True,
            }
        )
    first_target = records[0]["target"]
    if not isinstance(first_target, Path) or first_target.name.casefold() != plugin_id.casefold():
        raise ValueError("replacement journal plugin target identity is invalid")
    return tuple(records)


def _load_replacement_owner(
    journal_path: Path,
) -> tuple[str, str]:
    owner_path = journal_path.with_suffix(".owner")
    owner = json.loads(owner_path.read_text(encoding="utf-8"))
    if not isinstance(owner, dict) or owner.get("schema_version") != 1:
        raise ValueError("replacement journal owner marker is invalid")
    operation_id = owner.get("operation_id")
    plugin_id = owner.get("plugin_id")
    if (
        owner.get("kind") != "replacement"
        or not isinstance(operation_id, str)
        or operation_id != journal_path.stem
        or not isinstance(plugin_id, str)
        or not _PLUGIN_ID_PATTERN.fullmatch(plugin_id)
    ):
        raise ValueError("replacement journal owner identity is invalid")
    return operation_id, plugin_id


def recover_incomplete_plugin_replacements(
    *,
    journal_root: Path,
    allowed_roots: tuple[Path, ...] | None = None,
) -> ReplacementRecoveryResult:
    """Recover only replacement journals whose on-disk facts are unambiguous."""

    recovered: list[str] = []
    manual: list[str] = []
    manual_plugin_ids: list[str] = []
    block_user_plugin_root = False
    if not journal_root.is_dir():
        return ReplacementRecoveryResult((), ())
    for journal_path in sorted(journal_root.glob("*.json")):
        operation_id = journal_path.stem
        plugin_id: object = None
        try:
            owner_operation_id, owner_plugin_id = _load_replacement_owner(journal_path)
            operation_id = owner_operation_id
            plugin_id = owner_plugin_id
            state = json.loads(journal_path.read_text(encoding="utf-8"))
            if not isinstance(state, dict) or state.get("schema_version") != 1:
                manual.append(operation_id)
                if isinstance(plugin_id, str) and plugin_id:
                    manual_plugin_ids.append(plugin_id.casefold())
                continue
            raw_operation_id = state.get("operation_id")
            state_plugin_id = state.get("plugin_id")
            if (
                raw_operation_id != owner_operation_id
                or state_plugin_id != owner_plugin_id
            ):
                raise ValueError("replacement journal identity does not match owner")
            operation_id = raw_operation_id
            phase = state.get("phase")
            records = _journal_target_records(
                state.get("targets"),
                allowed_roots=allowed_roots,
                plugin_id=owner_plugin_id,
            )
            if phase == "commit_started":
                manual.append(operation_id)
                if isinstance(plugin_id, str) and plugin_id:
                    manual_plugin_ids.append(plugin_id.casefold())
                continue
            if phase == "committed":
                if any(
                    not item["target"].exists()  # type: ignore[union-attr]
                    for item in records
                    if item["preexisting"]
                ):
                    manual.append(operation_id)
                    if isinstance(plugin_id, str) and plugin_id:
                        manual_plugin_ids.append(plugin_id.casefold())
                    continue
                for item in records:
                    _remove_path_sync(item["backup"])  # type: ignore[arg-type]
                journal_path.unlink(missing_ok=True)
                journal_path.with_suffix(".owner").unlink(missing_ok=True)
                recovered.append(operation_id)
                continue
            if phase != "precommit":
                manual.append(operation_id)
                if isinstance(plugin_id, str) and plugin_id:
                    manual_plugin_ids.append(plugin_id.casefold())
                continue

            ambiguous = False
            for item in records:
                target = item["target"]
                backup = item["backup"]
                assert isinstance(target, Path) and isinstance(backup, Path)
                target_exists = target.exists()
                backup_exists = backup.exists()
                if item["preexisting"]:
                    if not target_exists and not backup_exists:
                        ambiguous = True
                    elif target_exists and backup_exists:
                        ambiguous = True
                elif backup_exists:
                    ambiguous = True
                elif target_exists:
                    ambiguous = True
            if ambiguous:
                manual.append(operation_id)
                if isinstance(plugin_id, str) and plugin_id:
                    manual_plugin_ids.append(plugin_id.casefold())
                continue

            for item in records:
                target = item["target"]
                backup = item["backup"]
                assert isinstance(target, Path) and isinstance(backup, Path)
                if item["preexisting"] and backup.exists():
                    _remove_path_sync(target)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    backup.rename(target)
                elif not item["preexisting"] and target.exists():
                    raise RuntimeError("new replacement target requires manual recovery")
            journal_path.unlink(missing_ok=True)
            journal_path.with_suffix(".owner").unlink(missing_ok=True)
            recovered.append(operation_id)
        except Exception as exc:
            logger.error(
                "replacement journal recovery requires manual action "
                "operation_id={} err_type={}",
                operation_id,
                type(exc).__name__,
            )
            manual.append(operation_id)
            if isinstance(plugin_id, str) and plugin_id:
                manual_plugin_ids.append(plugin_id.casefold())
            else:
                block_user_plugin_root = True
    return ReplacementRecoveryResult(
        tuple(recovered),
        tuple(dict.fromkeys(manual)),
        tuple(dict.fromkeys(manual_plugin_ids)),
        block_user_plugin_root,
    )


async def plugin_is_running(plugin_id: str) -> bool:
    if not plugin_id:
        return False
    try:
        from plugin.server.application.plugins.lifecycle_service import _plugin_is_running_sync

        return await asyncio.to_thread(_plugin_is_running_sync, plugin_id)
    except Exception as exc:  # pragma: no cover - defensive host-registry boundary
        logger.warning(
            "lifecycle running-state probe failed plugin_id={} err_type={}",
            plugin_id,
            type(exc).__name__,
        )
        raise


async def stop_plugin_for_replace(plugin_id: str) -> None:
    if not plugin_id:
        return
    from plugin.server.application.plugins.lifecycle_service import PluginLifecycleService

    try:
        await PluginLifecycleService().stop_plugin(plugin_id)
    except ServerDomainError as exc:
        if getattr(exc, "code", None) == "PLUGIN_NOT_RUNNING":
            return
        raise


async def start_plugin_after_replace(plugin_id: str, *, strict: bool) -> bool:
    if not plugin_id:
        return False
    from plugin.server.application.plugins.lifecycle_service import PluginLifecycleService

    try:
        await PluginLifecycleService().start_plugin(plugin_id)
        return True
    except Exception as exc:
        logger.error(
            "lifecycle restart failed plugin_id={} err_type={}",
            plugin_id,
            type(exc).__name__,
        )
        if strict:
            raise
        return False


# Market keeps the established names until its Day 3 adapter switches to the
# shared replace transaction.
stop_plugin_for_upgrade = stop_plugin_for_replace
start_plugin_after_upgrade = start_plugin_after_replace


def backup_path_for(target_dir: Path, *, backup_root: Path | None = None) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S_%f")
    root = backup_root or target_dir.parent / ".upgrade-backups"
    return root / f"{target_dir.name}.bak.{timestamp}"


async def restore_directory(backup_dir: Path, target_dir: Path) -> None:
    if not backup_dir.exists():
        return
    await remove_directory(target_dir)
    await _run_thread_mutation(backup_dir.rename, target_dir)


async def remove_directory(target_dir: Path) -> None:
    if not target_dir.exists():
        return
    await _run_thread_mutation(shutil.rmtree, target_dir)


async def merge_directory_contents(source_dir: Path, target_dir: Path) -> None:
    if not source_dir.exists():
        return
    if not source_dir.is_dir():
        raise NotADirectoryError(source_dir)
    await _run_thread_mutation(target_dir.mkdir, parents=True, exist_ok=True)
    await _run_thread_mutation(
        shutil.copytree,
        source_dir,
        target_dir,
        dirs_exist_ok=True,
    )


async def _restore_manifest_adjacent_profiles(backup_dir: Path, target_dir: Path) -> None:
    for relative_path in _MANIFEST_ADJACENT_PROFILE_PATHS:
        source = backup_dir / relative_path
        if is_link_or_reparse_point(source):
            raise OSError(f"linked paths are not supported for profile paths: {source}")
        if not source.exists():
            continue
        target = target_dir / relative_path
        if source.is_dir():
            await merge_directory_contents(source, target)
            continue
        if not source.is_file():
            raise OSError(f"unsupported profile path: {source}")
        await _run_thread_mutation(target.parent.mkdir, parents=True, exist_ok=True)
        await _run_thread_mutation(shutil.copy2, source, target)


def _merge_preserved_state_sync(
    source: Path,
    target: Path,
    *,
    root: Path,
    previous_package_state_files: Mapping[str, str] | None,
    incoming_package_state_files: Mapping[str, str],
) -> None:
    """Copy old runtime state into a replacement without overwriting package files."""

    if is_link_or_reparse_point(source) or is_link_or_reparse_point(target):
        relative = source.relative_to(root).as_posix()
        raise OSError(f"links are not supported for plugin state paths: {relative}")
    if source.is_dir():
        if target.exists() and not target.is_dir():
            relative = source.relative_to(root).as_posix()
            raise PackageStateConflictError(relative)
        children = tuple(source.iterdir())
        if not children and not target.exists():
            target.mkdir(parents=True, exist_ok=True)
        for child in children:
            _merge_preserved_state_sync(
                child,
                target / child.name,
                root=root,
                previous_package_state_files=previous_package_state_files,
                incoming_package_state_files=incoming_package_state_files,
            )
        return
    if not source.is_file():
        relative = source.relative_to(root).as_posix()
        raise OSError(f"unsupported plugin state path: {relative}")
    relative = source.relative_to(root).as_posix()
    source_digest = sha256_file(source)
    previous_digest = (
        previous_package_state_files.get(relative)
        if previous_package_state_files is not None
        else None
    )
    incoming_digest = incoming_package_state_files.get(relative)
    if previous_digest is not None and source_digest == previous_digest:
        return
    if target.exists() or incoming_digest is not None:
        if previous_package_state_files is None and incoming_digest == source_digest:
            return
        raise PackageStateConflictError(relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target, follow_symlinks=False)


async def _restore_collocated_runtime_state(
    layout: PluginLayout,
    backup_dir: Path,
    target_dir: Path,
    *,
    previous_package_state_files: Mapping[str, str] | None,
    incoming_package_state_files: Mapping[str, str],
) -> None:
    installed_dir = layout.installed_dir.resolve(strict=False)
    relative_paths: list[Path] = []
    for state_path in (layout.config_path.parent, layout.data_dir, layout.cache_dir):
        try:
            relative_path = state_path.resolve(strict=False).relative_to(installed_dir)
        except ValueError:
            continue
        if relative_path != Path(".") and relative_path not in relative_paths:
            relative_paths.append(relative_path)

    for relative_path in relative_paths:
        source = backup_dir / relative_path
        if not source.exists() and not is_link_or_reparse_point(source):
            continue
        operation = asyncio.create_task(
            asyncio.to_thread(
                _merge_preserved_state_sync,
                source,
                target_dir / relative_path,
                root=backup_dir,
                previous_package_state_files=previous_package_state_files,
                incoming_package_state_files=incoming_package_state_files,
            )
        )
        try:
            await asyncio.shield(operation)
        except asyncio.CancelledError:
            try:
                await await_cancellation_safe(operation)
            except Exception as copy_exc:
                logger.error(
                    "plugin state preservation failed while cancellation was pending "
                    "plugin_id={} path={} err_type={}",
                    layout.plugin_id,
                    relative_path.as_posix(),
                    type(copy_exc).__name__,
                )
            raise


async def run_rollback(
    *,
    plugin_id: str,
    target_dir: Path,
    backup_dir: Path,
    restart: bool,
    start: Callable[[str], Awaitable[None]],
) -> bool:
    restored = True
    try:
        await restore_directory(backup_dir, target_dir)
    except Exception as exc:
        restored = False
        logger.error(
            "plugin directory rollback failed plugin_id={} err_type={}",
            plugin_id,
            type(exc).__name__,
        )
    if restart:
        try:
            await start(plugin_id)
        except Exception as exc:
            restored = False
            logger.error(
                "plugin rollback restart failed plugin_id={} err_type={}",
                plugin_id,
                type(exc).__name__,
            )
    return restored


async def _rollback_targets(
    *,
    targets: tuple[Path, ...],
    backups: dict[Path, Path],
    preexisting_targets: frozenset[Path],
    remove_created_targets: bool,
) -> bool:
    restored = True
    for target in reversed(targets):
        backup = backups.get(target)
        if backup is None:
            if remove_created_targets and target not in preexisting_targets:
                try:
                    await remove_directory(target)
                except Exception as exc:
                    restored = False
                    logger.error(
                        "plugin replacement created-target cleanup failed target={} err_type={}",
                        target.name,
                        type(exc).__name__,
                    )
            continue
        if not backup.exists():
            restored = False
            logger.error(
                "plugin replacement target rollback skipped because backup is missing "
                "target={}",
                target.name,
            )
            continue
        try:
            await remove_directory(target)
            await restore_directory(backup, target)
        except Exception as exc:
            restored = False
            logger.error(
                "plugin replacement target rollback failed target={} err_type={}",
                target.name,
                type(exc).__name__,
            )
    return restored


def _notify_rollback_start(callback: Callable[[], None] | None) -> None:
    if callback is None:
        return
    try:
        callback()
    except Exception as exc:
        logger.warning(
            "plugin replacement rollback observer failed err_type={}",
            type(exc).__name__,
        )


async def replace_plugin(
    *,
    layout: PluginLayout,
    install_new: Callable[[], Awaitable[dict[str, object]]],
    validate_new: Callable[[], Awaitable[None]],
    is_running: Callable[[str], Awaitable[bool]],
    stop: Callable[[str], Awaitable[None]],
    start: Callable[[str], Awaitable[None]],
    cleanup_backup: Callable[[Path], Awaitable[None]],
    additional_targets: tuple[Path, ...] = (),
    preserve_targets: tuple[Path, ...] = (),
    previous_package_state_files: Mapping[str, str] | None = None,
    incoming_package_state_files: Mapping[str, str] | None = None,
    on_rollback_start: Callable[[], None] | None = None,
    validate_backup: Callable[[Path], Awaitable[None]] | None = None,
    commit: Callable[[dict[str, object]], Awaitable[dict[str, object]]] | None = None,
) -> ReplacePluginResult:
    plugin_id = layout.plugin_id
    target_dir = layout.installed_dir
    if not plugin_id:
        raise ValueError("plugin replacement requires a plugin id")
    if not target_dir.is_dir():
        raise FileNotFoundError(f"installed plugin directory is missing: {target_dir.name}")
    targets = (target_dir, *additional_targets)
    if any(target not in targets for target in preserve_targets):
        raise ValueError("preserve targets must also be replacement targets")

    await _run_thread_mutation(
        ensure_plugin_layout_runtime_config,
        layout,
    )
    was_running = await is_running(plugin_id)
    if was_running:
        stop_operation = asyncio.create_task(stop(plugin_id))
        try:
            await asyncio.shield(stop_operation)
        except asyncio.CancelledError:

            async def finish_stop_and_restart_after_cancellation() -> None:
                try:
                    await stop_operation
                except Exception as stop_exc:
                    logger.error(
                        "plugin stop failed while cancellation was pending "
                        "plugin_id={} err_type={}",
                        plugin_id,
                        type(stop_exc).__name__,
                    )
                try:
                    await start(plugin_id)
                except Exception as restart_exc:
                    logger.error(
                        "plugin restart after canceled stop failed "
                        "plugin_id={} err_type={}",
                        plugin_id,
                        type(restart_exc).__name__,
                    )

            await await_cancellation_safe(finish_stop_and_restart_after_cancellation())
            raise

    preexisting_targets = frozenset(target for target in targets if target.exists())
    backups: dict[Path, Path] = {}
    backup_dir = backup_path_for(target_dir)
    planned_backups = {
        target: (
            backup_dir
            if target == target_dir
            else backup_path_for(target)
        )
        for target in targets
    }
    try:
        journal = _ReplacementJournal.create(
            plugin_id=plugin_id,
            journal_root=backup_dir.parent / ".transactions",
            targets=targets,
            backups=planned_backups,
            preexisting_targets=preexisting_targets,
        )
    except Exception:
        if was_running:
            await start(plugin_id)
        raise
    try:
        for target in targets:
            if not target.exists():
                continue
            if not target.is_dir():
                raise NotADirectoryError(target)
            backup = planned_backups[target]
            mkdir_operation = asyncio.create_task(
                asyncio.to_thread(backup.parent.mkdir, parents=True, exist_ok=True)
            )
            try:
                await asyncio.shield(mkdir_operation)
            except asyncio.CancelledError:
                try:
                    await await_cancellation_safe(mkdir_operation)
                except Exception as mkdir_exc:
                    logger.error(
                        "plugin backup directory creation failed while cancellation "
                        "was pending target={} err_type={}",
                        target.name,
                        type(mkdir_exc).__name__,
                    )
                raise

            rename_operation = asyncio.create_task(asyncio.to_thread(target.rename, backup))
            try:
                await asyncio.shield(rename_operation)
            except asyncio.CancelledError:
                rename_error: Exception | None = None
                try:
                    await await_cancellation_safe(rename_operation)
                except Exception as rename_exc:
                    rename_error = rename_exc

                target_exists = target.exists()
                backup_exists = backup.exists()
                if backup_exists:
                    backups[target] = backup
                if rename_error is not None:
                    logger.error(
                        "plugin backup rename failed while cancellation was pending "
                        "target={} err_type={} target_exists={} backup_exists={}",
                        target.name,
                        type(rename_error).__name__,
                        target_exists,
                        backup_exists,
                    )
                if not target_exists and not backup_exists:
                    logger.error(
                        "plugin backup rename cancellation left no observable source "
                        "or backup target={}",
                        target.name,
                    )
                raise
            backups[target] = backup
            journal.mark_moved(target)
    except asyncio.CancelledError:
        _notify_rollback_start(on_rollback_start)

        async def rollback_canceled_backup() -> bool:
            recovered = await _rollback_targets(
                targets=targets,
                backups=backups,
                preexisting_targets=preexisting_targets,
                remove_created_targets=False,
            )
            missing_targets = [
                target.name for target in preexisting_targets if not target.exists()
            ]
            if missing_targets:
                recovered = False
                logger.error(
                    "plugin canceled backup rollback left targets missing "
                    "plugin_id={} targets={}",
                    plugin_id,
                    ",".join(sorted(missing_targets)),
                )
            if was_running:
                try:
                    await start(plugin_id)
                except Exception as restart_exc:
                    recovered = False
                    logger.error(
                        "plugin restart after canceled backup failed "
                        "plugin_id={} err_type={}",
                        plugin_id,
                        type(restart_exc).__name__,
                    )
            return recovered

        recovered = await await_cancellation_safe(rollback_canceled_backup())
        if recovered:
            journal.finish()
        if not recovered:
            logger.error(
                "plugin canceled backup rollback incomplete plugin_id={}",
                plugin_id,
            )
        raise
    except Exception as exc:
        _notify_rollback_start(on_rollback_start)
        recovered = await _rollback_targets(
            targets=targets,
            backups=backups,
            preexisting_targets=preexisting_targets,
            remove_created_targets=False,
        )
        if was_running:
            try:
                await start(plugin_id)
            except Exception as restart_exc:
                recovered = False
                logger.error(
                    "plugin restart after backup failure failed plugin_id={} err_type={}",
                    plugin_id,
                    type(restart_exc).__name__,
                )
        if recovered:
            journal.finish()
        raise ReplacePluginError(
            stage="backup",
            rollback_status="completed" if recovered else "incomplete",
            cause=exc,
        ) from exc
    stage = "confirm"
    committed = False
    try:
        if validate_backup is not None:
            await validate_backup(backup_dir)
        stage = "install"
        install_result = await install_new()
        stage = "validate"
        await validate_new()
        stage = "preserve"
        await _restore_collocated_runtime_state(
            layout,
            backup_dir,
            target_dir,
            previous_package_state_files=previous_package_state_files,
            incoming_package_state_files=incoming_package_state_files or {},
        )
        for target in preserve_targets:
            backup = backups.get(target)
            if backup is not None:
                await merge_directory_contents(backup, target)
        await _restore_manifest_adjacent_profiles(backup_dir, target_dir)
        if was_running:
            stage = "restart"
            await start(plugin_id)
        if commit is not None:
            stage = "commit"
            journal.set_phase("commit_started")
            install_result = await commit(install_result)
        journal.set_phase("committed")
        committed = True
        stage = "cleanup"
        cleanup_cancellation: asyncio.CancelledError | None = None
        cleanup_complete = True
        for backup in backups.values():
            cleanup_operation = asyncio.create_task(cleanup_backup(backup))
            try:
                await asyncio.shield(cleanup_operation)
            except asyncio.CancelledError as exc:
                cleanup_cancellation = cleanup_cancellation or exc
                try:
                    await await_cancellation_safe(cleanup_operation)
                except Exception as cleanup_exc:
                    cleanup_complete = False
                    logger.warning(
                        "plugin backup cleanup failed while cancellation was pending "
                        "plugin_id={} err_type={}",
                        plugin_id,
                        type(cleanup_exc).__name__,
                    )
            except Exception as exc:  # cleanup must not roll back a valid replacement
                cleanup_complete = False
                logger.warning(
                    "plugin backup cleanup failed plugin_id={} err_type={}",
                    plugin_id,
                    type(exc).__name__,
                )
        if cleanup_complete and not any(backup.exists() for backup in backups.values()):
            journal.finish()
        if cleanup_cancellation is not None:
            setattr(cleanup_cancellation, "replacement_committed", True)
            raise cleanup_cancellation
        return ReplacePluginResult(
            restarted=was_running,
            rollback_status="not_needed",
            install_result=install_result,
            backup_dir=backup_dir,
        )
    except asyncio.CancelledError as cancel_exc:
        if committed:
            setattr(cancel_exc, "replacement_committed", True)
            raise
        _notify_rollback_start(on_rollback_start)

        async def rollback_canceled_replacement() -> bool:
            restored = await _rollback_targets(
                targets=targets,
                backups=backups,
                preexisting_targets=preexisting_targets,
                remove_created_targets=True,
            )
            if was_running:
                try:
                    await start(plugin_id)
                except Exception as restart_exc:
                    restored = False
                    logger.error(
                        "plugin canceled replacement restart failed "
                        "plugin_id={} err_type={}",
                        plugin_id,
                        type(restart_exc).__name__,
                    )
            return restored

        restored = await await_cancellation_safe(rollback_canceled_replacement())
        if restored:
            journal.finish()
        if not restored:
            logger.error(
                "plugin canceled replacement rollback incomplete plugin_id={} stage={}",
                plugin_id,
                stage,
            )
        raise
    except Exception as exc:
        if committed:
            persistence_errors: list[str] = []
            try:
                await _run_thread_mutation(journal.ensure_persisted)
            except Exception as persistence_exc:
                persistence_errors.append(type(persistence_exc).__name__)
            logger.error(
                "plugin replacement committed cleanup incomplete plugin_id={} "
                "err_type={} journal_persistence_errors={}",
                plugin_id,
                type(exc).__name__,
                ",".join(persistence_errors),
            )
            if isinstance(exc, ReplacePluginError) and exc.committed:
                raise
            raise ReplacePluginError(
                stage="cleanup",
                rollback_status="not_needed",
                cause=exc,
                committed=True,
            ) from exc
        _notify_rollback_start(on_rollback_start)
        restored = await _rollback_targets(
            targets=targets,
            backups=backups,
            preexisting_targets=preexisting_targets,
            remove_created_targets=True,
        )
        if was_running:
            try:
                await start(plugin_id)
            except Exception as restart_exc:
                restored = False
                logger.error(
                    "plugin rollback restart failed plugin_id={} err_type={}",
                    plugin_id,
                    type(restart_exc).__name__,
                )
        if restored:
            journal.finish()
        raise ReplacePluginError(
            stage=stage,
            rollback_status="completed" if restored else "incomplete",
            cause=exc,
        ) from exc
