from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
import shutil
from typing import Any

from plugin.core.state import state
from plugin.logging_config import get_logger

logger = get_logger("server.application.plugins.source_switch")

AsyncNoArg = Callable[[], Awaitable[Any]]
AsyncPluginAction = Callable[[str], Awaitable[Any]]


@dataclass(frozen=True, slots=True)
class SourceSwitchRequest:
    plugin_id: str
    staged_plugin_dir: Path
    target_plugin_dir: Path
    confirmation_token: str
    staged_profile_dir: Path | None = None
    target_profile_dir: Path | None = None


@dataclass(frozen=True, slots=True)
class SourceSwitchResult:
    plugin_id: str
    code: str
    effective_source: str
    restarted: bool


class SourceSwitchError(RuntimeError):
    def __init__(
        self,
        *,
        code: str,
        stage: str,
        cause: Exception,
        rollback_code: str | None = None,
    ) -> None:
        super().__init__(f"{stage} failed: {cause}")
        self.code = code
        self.stage = stage
        self.cause = cause
        self.rollback_code = rollback_code

    def as_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "code": self.code,
            "stage": self.stage,
            "error_type": type(self.cause).__name__,
        }
        if self.rollback_code:
            payload["rollback_code"] = self.rollback_code
            payload["running"] = False
            payload["restored"] = self.rollback_code == "override_rollback_completed"
        return payload


def _validate_switch_paths_sync(request: SourceSwitchRequest) -> None:
    pairs = [(request.staged_plugin_dir, request.target_plugin_dir, "plugin")]
    if (request.staged_profile_dir is None) != (request.target_profile_dir is None):
        raise ValueError("profile staging and target paths must be provided together")
    if request.staged_profile_dir is not None and request.target_profile_dir is not None:
        pairs.append((request.staged_profile_dir, request.target_profile_dir, "profile"))

    for staging, target, label in pairs:
        if staging.is_symlink() or target.is_symlink():
            raise OSError(f"{label} source switch paths must not be symbolic links")
        if not staging.is_dir():
            raise FileNotFoundError(f"staged {label} directory is missing: {staging}")
        if target.exists():
            raise FileExistsError(f"target {label} directory already exists: {target}")
        if staging.resolve(strict=False).parent != target.resolve(strict=False).parent:
            raise ValueError(f"staged {label} directory must be a sibling of its target")
        if not staging.name.startswith("."):
            raise ValueError(f"staged {label} directory must be hidden")


def _promote_directory_sync(staging: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    staging.rename(target)


async def _promote_directory(staging: Path, target: Path) -> None:
    """Finish the atomic rename before propagating task cancellation."""

    promotion = asyncio.create_task(
        asyncio.to_thread(_promote_directory_sync, staging, target)
    )
    try:
        await asyncio.shield(promotion)
    except asyncio.CancelledError:
        if not promotion.done():
            try:
                await promotion
            except BaseException:
                # The original cancellation remains the transaction's cause;
                # rollback derives ownership from the settled filesystem.
                pass
        raise


def _remove_owned_directory_sync(path: Path) -> None:
    if not path.exists():
        return
    if path.is_symlink():
        raise OSError(f"refusing to remove symbolic-link source switch path: {path}")
    if not path.is_dir():
        raise NotADirectoryError(path)
    shutil.rmtree(path)


def _evict_plugin_modules_sync(plugin_id: str) -> None:
    from plugin.core.host import evict_cached_plugin_modules

    evict_cached_plugin_modules(plugin_id)


def _effective_config_path_sync(plugin_id: str) -> Path | None:
    with state.acquire_plugins_read_lock():
        raw_meta = state.plugins.get(plugin_id)
        config_path_obj = raw_meta.get("config_path") if isinstance(raw_meta, dict) else None
    if not isinstance(config_path_obj, str) or not config_path_obj:
        return None
    return Path(config_path_obj).resolve(strict=False)


def _runtime_load_failure_sync(plugin_id: str) -> tuple[str, str] | None:
    with state.acquire_plugins_read_lock():
        raw_meta = state.plugins.get(plugin_id)
        if not isinstance(raw_meta, dict) or raw_meta.get("runtime_load_state") != "failed":
            return None
        error_type = str(raw_meta.get("runtime_load_error_type") or "unknown")
        error_phase = str(raw_meta.get("runtime_load_error_phase") or "unknown")
    return error_type, error_phase


def _validate_rebuilt_plan(plan: Mapping[str, object], request: SourceSwitchRequest) -> None:
    if (
        plan.get("action") != "override_builtin"
        or plan.get("plugin_id") != request.plugin_id
        or plan.get("current_source") != "builtin"
        or plan.get("target_source") != "market"
        or plan.get("confirmation_token") != request.confirmation_token
    ):
        raise SourceSwitchError(
            code="override_source_changed",
            stage="rebuild_plan",
            cause=RuntimeError("builtin override plan changed before promotion"),
        )


async def _rollback_switch(
    *,
    request: SourceSwitchRequest,
    plugin_promoted: bool,
    profile_promoted: bool,
    lock_may_have_changed: bool,
    lock_snapshot: object,
    was_running: bool,
    restore_lock: Callable[[object], Awaitable[Any]],
    clear_user_source: AsyncNoArg,
    refresh_registry: AsyncNoArg,
    is_running: Callable[[str], Awaitable[bool]],
    stop: AsyncPluginAction,
    start: AsyncPluginAction,
) -> str:
    complete = True
    try:
        if await is_running(request.plugin_id):
            await stop(request.plugin_id)
    except Exception as exc:
        complete = False
        logger.error(
            "override rollback could not stop user source plugin_id={} err_type={}",
            request.plugin_id,
            type(exc).__name__,
        )

    cleanup_paths: list[Path] = []
    if profile_promoted and request.target_profile_dir is not None:
        cleanup_paths.append(request.target_profile_dir)
    if plugin_promoted:
        cleanup_paths.append(request.target_plugin_dir)
    for path in cleanup_paths:
        try:
            await asyncio.to_thread(_remove_owned_directory_sync, path)
        except Exception as exc:
            complete = False
            logger.error(
                "override rollback cleanup failed plugin_id={} target={} err_type={}",
                request.plugin_id,
                path,
                type(exc).__name__,
            )

    if plugin_promoted:
        try:
            await asyncio.to_thread(
                _evict_plugin_modules_sync,
                request.plugin_id,
            )
        except Exception as exc:
            complete = False
            logger.error(
                "override rollback cache invalidation failed plugin_id={} err_type={}",
                request.plugin_id,
                type(exc).__name__,
            )

    metadata_operations: list[tuple[Callable[..., Awaitable[Any]], tuple[object, ...]]] = []
    if lock_may_have_changed:
        metadata_operations.extend(
            (
                (restore_lock, (lock_snapshot,)),
                (clear_user_source, ()),
            )
        )
    metadata_operations.append((refresh_registry, ()))
    for operation, args in metadata_operations:
        try:
            await operation(*args)
        except Exception as exc:
            complete = False
            logger.error(
                "override rollback metadata restore failed plugin_id={} err_type={}",
                request.plugin_id,
                type(exc).__name__,
            )

    if was_running:
        try:
            await start(request.plugin_id)
        except Exception as exc:
            complete = False
            logger.error(
                "override rollback builtin restart failed plugin_id={} err_type={}",
                request.plugin_id,
                type(exc).__name__,
            )
    return "override_rollback_completed" if complete else "override_rollback_incomplete"


async def switch_builtin_source(
    request: SourceSwitchRequest,
    *,
    rebuild_plan: Callable[[], Awaitable[Mapping[str, object]]],
    read_lock_snapshot: AsyncNoArg,
    commit_lock: AsyncNoArg,
    restore_lock: Callable[[object], Awaitable[Any]],
    clear_user_source: AsyncNoArg,
    refresh_registry: AsyncNoArg,
    validate_promoted_source: AsyncNoArg,
    is_running: Callable[[str], Awaitable[bool]],
    stop: AsyncPluginAction,
    start: AsyncPluginAction,
) -> SourceSwitchResult:
    """Atomically switch a running builtin plugin to its Market override.

    Package download, SHA verification, extraction and manifest inspection are
    deliberately caller-owned. This transaction begins with a validated hidden
    staging directory and never reads, copies, moves or removes plugin state.
    """
    if not request.plugin_id or not request.confirmation_token:
        raise ValueError("source switch requires plugin id and confirmation token")
    if "." in request.plugin_id:
        raise ValueError("source switch plugin id must not contain dots")
    await asyncio.to_thread(_validate_switch_paths_sync, request)
    rebuilt_plan = await rebuild_plan()
    _validate_rebuilt_plan(rebuilt_plan, request)

    lock_snapshot = await read_lock_snapshot()
    was_running = await is_running(request.plugin_id)
    plugin_promoted = False
    profile_promoted = False
    lock_may_have_changed = False
    stage = "stop_builtin"
    try:
        if was_running:
            await stop(request.plugin_id)

        stage = "promote_plugin"
        try:
            await _promote_directory(
                request.staged_plugin_dir,
                request.target_plugin_dir,
            )
        finally:
            plugin_promoted = (
                request.target_plugin_dir.is_dir()
                and not request.staged_plugin_dir.exists()
            )
        if request.staged_profile_dir is not None and request.target_profile_dir is not None:
            stage = "promote_profile"
            try:
                await _promote_directory(
                    request.staged_profile_dir,
                    request.target_profile_dir,
                )
            finally:
                profile_promoted = (
                    request.target_profile_dir.is_dir()
                    and not request.staged_profile_dir.exists()
                )

        stage = "write_lock"
        # A callback may persist the new row and then be cancelled before it
        # returns, so rollback must treat the write as attempted.
        lock_may_have_changed = True
        await commit_lock()
        stage = "refresh_registry"
        await refresh_registry()
        effective_path = await asyncio.to_thread(_effective_config_path_sync, request.plugin_id)
        expected_path = (request.target_plugin_dir / "plugin.toml").resolve(strict=False)
        if effective_path != expected_path:
            raise RuntimeError("registry did not select the promoted user source")
        load_failure = await asyncio.to_thread(
            _runtime_load_failure_sync,
            request.plugin_id,
        )
        if load_failure is not None:
            error_type, error_phase = load_failure
            raise RuntimeError(
                "registry could not load the promoted user source "
                f"({error_type} during {error_phase})"
            )
        stage = "validate_promoted_source"
        await validate_promoted_source()

        if was_running:
            stage = "start_market"
            await start(request.plugin_id)
        return SourceSwitchResult(
            plugin_id=request.plugin_id,
            code="override_completed",
            effective_source="market",
            restarted=was_running,
        )
    except BaseException as exc:
        rollback_code = await _rollback_switch(
            request=request,
            plugin_promoted=plugin_promoted,
            profile_promoted=profile_promoted,
            lock_may_have_changed=lock_may_have_changed,
            lock_snapshot=lock_snapshot,
            was_running=was_running,
            restore_lock=restore_lock,
            clear_user_source=clear_user_source,
            refresh_registry=refresh_registry,
            is_running=is_running,
            stop=stop,
            start=start,
        )
        if isinstance(exc, asyncio.CancelledError):
            raise
        if not isinstance(exc, Exception):
            raise
        primary_code = "override_start_failed" if stage == "start_market" else rollback_code
        raise SourceSwitchError(
            code=primary_code,
            stage=stage,
            cause=exc,
            rollback_code=rollback_code,
        ) from exc
