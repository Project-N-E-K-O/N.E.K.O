from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import shutil

from plugin.logging_config import get_logger
from plugin.server.domain.errors import ServerDomainError

logger = get_logger("server.application.plugins.upgrade_support")


@dataclass(frozen=True, slots=True)
class SafeUpgradeResult:
    operation: str
    restarted: bool
    rollback_status: str
    install_result: dict[str, object]
    backup_dir: Path


class SafeUpgradeError(RuntimeError):
    def __init__(self, *, stage: str, rollback_status: str, cause: Exception) -> None:
        super().__init__(f"{stage} failed: {cause}")
        self.stage = stage
        self.rollback_status = rollback_status


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


async def stop_plugin_for_upgrade(plugin_id: str) -> None:
    if not plugin_id:
        return
    from plugin.server.application.plugins.lifecycle_service import PluginLifecycleService

    try:
        await PluginLifecycleService().stop_plugin(plugin_id)
    except ServerDomainError as exc:
        if getattr(exc, "code", None) == "PLUGIN_NOT_RUNNING":
            return
        raise


async def start_plugin_after_upgrade(plugin_id: str, *, strict: bool) -> bool:
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


def backup_path_for(target_dir: Path, *, backup_root: Path | None = None) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S_%f")
    root = backup_root or target_dir.parent / ".upgrade-backups"
    return root / f"{target_dir.name}.bak.{timestamp}"


async def restore_directory(backup_dir: Path, target_dir: Path) -> None:
    if not backup_dir.exists():
        return
    await remove_directory(target_dir)
    await asyncio.to_thread(backup_dir.rename, target_dir)


async def remove_directory(target_dir: Path) -> None:
    if not target_dir.exists():
        return
    await asyncio.to_thread(shutil.rmtree, target_dir)


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
) -> bool:
    restored = True
    for target in reversed(targets):
        try:
            await remove_directory(target)
            backup = backups.get(target)
            if backup is not None:
                await restore_directory(backup, target)
        except Exception as exc:
            restored = False
            logger.error(
                "plugin upgrade target rollback failed target={} err_type={}",
                target.name,
                type(exc).__name__,
            )
    return restored


async def perform_safe_upgrade(
    *,
    plan: object,
    target_dir: Path,
    install_new: Callable[[], Awaitable[dict[str, object]]],
    validate_new: Callable[[], Awaitable[None]],
    is_running: Callable[[str], Awaitable[bool]],
    stop: Callable[[str], Awaitable[None]],
    start: Callable[[str], Awaitable[None]],
    cleanup_backup: Callable[[Path], Awaitable[None]],
    additional_targets: tuple[Path, ...] = (),
) -> SafeUpgradeResult:
    if getattr(plan, "action", "") != "upgrade":
        raise ValueError("safe upgrade requires an upgrade install plan")
    plugin_id = str(getattr(plan, "plugin_id", ""))
    if not plugin_id:
        raise ValueError("safe upgrade requires a plugin id")
    if not target_dir.is_dir():
        raise FileNotFoundError(f"installed plugin directory is missing: {target_dir.name}")

    was_running = await is_running(plugin_id)
    if was_running:
        await stop(plugin_id)

    targets = (target_dir, *additional_targets)
    backups: dict[Path, Path] = {}
    backup_dir = backup_path_for(target_dir)
    try:
        for target in targets:
            if not target.exists():
                continue
            if not target.is_dir():
                raise NotADirectoryError(target)
            backup = backup_dir if target == target_dir else backup_path_for(target)
            await asyncio.to_thread(backup.parent.mkdir, parents=True, exist_ok=True)
            await asyncio.to_thread(target.rename, backup)
            backups[target] = backup
    except Exception as exc:
        recovered = await _rollback_targets(targets=targets, backups=backups)
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
        raise SafeUpgradeError(
            stage="backup",
            rollback_status="completed" if recovered else "incomplete",
            cause=exc,
        ) from exc
    stage = "install"
    try:
        install_result = await install_new()
        stage = "validate"
        await validate_new()
        if was_running:
            stage = "restart"
            await start(plugin_id)
        stage = "cleanup"
        for backup in backups.values():
            try:
                await cleanup_backup(backup)
            except Exception as exc:  # cleanup must not roll back a valid upgrade
                logger.warning(
                    "plugin backup cleanup failed plugin_id={} err_type={}",
                    plugin_id,
                    type(exc).__name__,
                )
        return SafeUpgradeResult(
            operation="upgrade",
            restarted=was_running,
            rollback_status="not_needed",
            install_result=install_result,
            backup_dir=backup_dir,
        )
    except Exception as exc:
        restored = await _rollback_targets(targets=targets, backups=backups)
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
        raise SafeUpgradeError(
            stage=stage,
            rollback_status="completed" if restored else "incomplete",
            cause=exc,
        ) from exc
