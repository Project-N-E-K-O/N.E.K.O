from __future__ import annotations

import asyncio
import inspect
import math
import re
import time as time_module
try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
    import tomli as tomllib  # type: ignore[no-redef]
from collections.abc import Mapping, Sequence
from functools import partial
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from fastapi import HTTPException

from plugin._types.exceptions import PluginError, PluginLifecycleError
from plugin._types.isolated_metadata import (
    IsolatedPluginMetadata,
    handler_key_belongs_to_plugin as _handler_key_belongs_to_plugin,
)
from plugin.server.application.plugins import development as development_store
from plugin.core.registry import (
    _collect_plugin_python_requirements,
    _collect_plugin_python_requirement_paths,
    _check_plugin_dependency,
    _find_missing_python_requirements,
    _effective_entries,
    _overlay_entry_declaration,
    _parse_plugin_dependencies,
    _resolve_plugin_id_conflict,
)
from plugin.core.entry_points import (
    describe_plugin_entry_directory_mismatch,
    normalize_plugin_entry_point,
)
from plugin.core.state import state
from plugin.logging_config import get_logger
from plugin.server.domain import IO_RUNTIME_ERRORS, RUNTIME_ERRORS
from plugin.server.domain.errors import ServerDomainError
from plugin.server.application.plugins.operation_lock import (
    bounded_operation_wait,
    _HeldPluginOperationLock,
    _operation_lock_is_held_by_current_task,
    PluginOperationBusy,
    plugin_operation_lock,
    serialized_plugin_operation,
)
from plugin.server.application.plugins.registry_service import (
    PluginRegistryService,
    config_overrides_packaged_entries,
)
from plugin.server.application.plugins.installation_transactions import (
    UninstallOwnershipError,
    UninstallPluginError,
    require_uninstall_ownership,
    retry_deferred_plugin_code_cleanup_sync,
    retry_deferred_profile_cleanup_sync,
    uninstall_plugin,
)
from plugin.server.application.plugins._env_budgets import env_seconds
from plugin.server.application.plugins._metadata_scan_settings import (
    METADATA_SCAN_TIMEOUT_SECONDS as _DEFAULT_METADATA_SCAN_TIMEOUT,
)
from plugin.server.infrastructure.packaged_metadata import (
    SourceTreeSnapshot,
    entries_config_digest,
    packaged_metadata_needs_rebuild,
    read_packaged_metadata,
    refresh_stale_packaged_metadata,
    snapshot_packaged_metadata_rebuild_tree,
    stale_packaged_schema_version,
    write_local_packaged_metadata,
)
from plugin.server.application.install_source import (
    InstallSourceError,
    get_install_source_manager,
)
from plugin.server.infrastructure.config_resolver import resolve_plugin_config_from_path
from plugin.server.infrastructure.runtime_overrides import (
    RuntimeOverridePersistenceError,
    get_runtime_auto_start_override,
    get_runtime_override,
    get_runtime_override_entry,
    migrate_runtime_override,
    restore_runtime_override,
    set_runtime_auto_start_override,
    set_runtime_override,
)
from plugin.server.messaging.lifecycle_events import emit_lifecycle_event
from plugin.settings import (
    BUILTIN_PLUGIN_CONFIG_ROOT,
    PLUGIN_AUTOSTART_CONCURRENCY,
    PLUGIN_CONFIG_ROOTS,
    PLUGIN_SHUTDOWN_TIMEOUT,
    PLUGIN_STARTUP_TIMEOUT,
    PLUGIN_SYNC_AUTO_START_ON_TOGGLE,
)
from plugin.server.infrastructure.autostart_approvals import (
    clear_autostart_pending,
    is_autostart_approved,
)
from plugin.utils import parse_bool_config
from plugin.utils.asyncio_utils import await_cancellation_safe

logger = get_logger("server.application.plugins.lifecycle")


def create_plugin_host(
    *, plugin_id: str, entry_point: str, config_path: Path, source_only: bool = False
) -> PluginHostContract:
    from plugin.core.host import PluginProcessHost

    return PluginProcessHost(
        plugin_id, entry_point, config_path, source_only=source_only
    )


def scan_plugin_metadata_isolated(
    *,
    plugin_id: str,
    module_path: str,
    class_name: str,
    config_path: Path,
    conf: Mapping[str, object],
    pdata: Mapping[str, object],
    python_requirement_paths: list[Path],
    timeout: float,
    source_only: bool = False,
) -> IsolatedPluginMetadata:
    from plugin.server.application.plugins.metadata_scanner import (
        scan_plugin_metadata_isolated as scan,
    )

    return scan(
        plugin_id=plugin_id,
        module_path=module_path,
        class_name=class_name,
        config_path=config_path,
        conf=conf,
        pdata=pdata,
        python_requirement_paths=python_requirement_paths,
        timeout=timeout,
        source_only=source_only,
    )


def install_isolated_plugin_metadata(
    plugin_id: str, metadata: IsolatedPluginMetadata
) -> None:
    from plugin.server.application.plugins.metadata_scanner import (
        install_isolated_plugin_metadata as install,
    )

    install(plugin_id, metadata)


async def clear_plugin_llm_tools(
    plugin_id: str, *, timeout: float | None = None
) -> dict[str, object]:
    from plugin.server.messaging.llm_tool_registry import clear_plugin_tools

    return await clear_plugin_tools(plugin_id, timeout=timeout)


_PLUGIN_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")
_PLUGIN_STARTUP_TIMEOUT_MAX = 300.0
# Minimum time a step receives after compression by the whole-pass budget.
#
# Without a lower bound, an exhausted budget yields zero or negative time, immediately declaring startup failed
# rather than giving one hurried attempt. Plugins reaching startup were just stopped by us, so failure
# leaves them stopped; shutdown would similarly degenerate into killing the process without requesting cleanup first.
#
# Startup and shutdown have separate lower bounds. They once shared one value, so raising startup's minimum also
# raised shutdown's worst-case wall time, coupling two unrelated concerns in one change.
#
# Startup gets 3.0 rather than 1.0: the minimum promises one real attempt, and one second cannot buy one.
# Spawning a subprocess and importing the framework alone measured 0.74s locally (fastapi accounts for 0.38s), before
# any plugin imports. An inevitably insufficient window marks healthy plugins as startup failures on
# an over-budget pass. Only the already pathological over-budget path pays: eight plugins cost up to 16 extra seconds;
# a normal pass never reaches this case.
_MIN_CLAMPED_START_TIMEOUT = 3.0
# Keep shutdown at 1.0: this step spawns no process and merely lets the plugin acknowledge cleanup.
# Time allotted to clearing the remote tool table without budget constraints (it has finer internal timeouts too).
_CLEAR_TOOLS_BUDGET_SECONDS = 2.0
# Keep a small allowance even when the budget is exhausted. This differs from the startup-attempt minimum;
# _MIN_CLAMPED_START_TIMEOUT），
# it merely lets this idempotent remote clear be sent. Skipping leaves permanent ghost tools, whereas
# this small allowance costs time only when main_server actually hangs.
_MIN_TOOL_CLEANUP_TIMEOUT = 0.25

# Shutdown latch: ServerLifecycleService._shutdown_internal sets it before touching any plugin host;
# startup() resets it initially. Once set, start_plugin rejects new plugins: an in-flight reload protected
# by asyncio.shield could otherwise register a new host after the host snapshot/clear, leaving an orphan
# subprocess nobody stops. Follow _delivery_path_shutting_down: set on shutdown, reset on startup.
_operations_shutting_down = False
_hot_reload_failed: set[str] = set()


def plugin_needs_hot_reload_recovery(plugin_id: str) -> bool:
    """Recovery permission, revoked by any explicit start/stop under the lock."""
    return plugin_id in _hot_reload_failed


# plugin_id (as passed to start_plugin) -> startup timeout that call granted,
# recorded once the effective config has been read. The hot-reload watcher sizes
# its restart drain from it instead of reading config files itself.
_active_startup_timeouts: dict[str, float] = {}


def active_startup_timeout(plugin_id: str) -> float | None:
    """Startup timeout granted by an in-progress start of this plugin, if any."""
    return _active_startup_timeouts.get(plugin_id)


def revoke_hot_reload_recovery(plugin_id: str) -> None:
    """Drop the recovery permission when the plugin's source is replaced.

    Uninstallation, overwrite installation, and removing/rebinding development links may bypass stop_plugin (never
    called when the plugin is stopped), leaving permission attached to the ID for different source installed later
    (a restored built-in plugin or reinstalled package) to inherit, starting a plugin the user never started merely after editing a file.
    All callers hold the operation lock.
    """
    _hot_reload_failed.discard(plugin_id)


def _resolve_python_requirements(
    conf: Any,
    config_path: Path,
    plugin_id: str,
) -> tuple[list[str], list[Path], list[str]]:
    """Read a plugin's declared Python deps and check them against its vendor dir.

    All three steps are disk I/O: read pyproject.toml, list each dist-info under vendor/, then read each
    METADATA. Combining them lets callers offload everything with one to_thread call; see start_plugin.
    """
    requirements = _collect_plugin_python_requirements(conf, config_path, logger, plugin_id)
    paths = _collect_plugin_python_requirement_paths(config_path)
    missing = _find_missing_python_requirements(requirements, search_paths=paths)
    return requirements, paths, missing


_MIN_CLAMPED_STOP_TIMEOUT = 1.0


def _read_packaged_isolated_metadata(
    config_path: Path,
    plugin_id: str,
    *,
    conf: object = None,
    pdata: object = None,
) -> IsolatedPluginMetadata | None:
    """Reuse the packaging-time metadata instead of importing the plugin again.

    Starting a plugin already imports it once — inside the plugin process. The
    metadata worker was a *second* import of the same code, for a result the
    package already carries (codex). When the package has valid metadata, read
    it; otherwise fall back to the worker so a hand-dropped or dev-mode plugin
    still gets its entries.

    ⚠️ Handler keys embed the plugin id (``"<pid>.<entry>"``), and the id a
    plugin runs under is not always the one its manifest declares — an id
    conflict renames it. ``install_isolated_plugin_metadata`` drops every key
    that does not belong to the runtime id, so handing it packaged keys minted
    under a different id registers *nothing*: the plugin starts, reports
    success, and exposes no entries at all (coderabbit). Fall back to the
    worker in that case, since it mints keys under the id we pass it.

    An empty ``handlers`` mapping is an answer, not a gap: a background-only
    plugin registers no entries, and the current schema always writes the key.
    Treating empty as "no metadata" sent exactly those plugins back through the worker —
    one import for the scan, one for the host, so any module-level side effect
    (writing state, sending a notification, launching a helper) happened twice
    (codex). An artifact written under an older schema is refused by the reader
    and takes the worker path; ``start_plugin`` then rewrites it from that scan
    (``_refresh_scanned_packaged_metadata``), so the cost is one import, not one
    per start. Schema 3 never shipped in a release, so no in-memory migration.

    Returns ``None`` when there is no usable metadata at all.
    """
    packaged = read_packaged_metadata(Path(config_path).parent)
    if packaged is None:
        return None
    if config_overrides_packaged_entries(conf, pdata, packaged):
        # Packaging reads plugin.toml in the staging directory and cannot see user runtime configuration or the active
        # profile. If effective configuration changes entries, packaged handlers no longer describe the entries
        # this machine should register (codex). Fall back to a real scan for such plugins.
        return None
    if not packaged.built_in_this_environment:
        # These results were imported on another machine. Plugins may conditionally register entries based on sys.platform or
        # Python version, so packaged handlers describe the packaging machine's capabilities,
        # rather than this machine's (codex). Display-only entries can tolerate that, but the entries registered in
        # state.event_handlers are authoritative: incorrect metadata makes the model invoke an entry absent on this
        # machine. Fall back to a real scan, costing exactly what the pre-PR behavior did.
        logger.info(
            "packaged metadata was produced in a different environment; "
            "rescanning so the registered entries match this machine: "
            "plugin_id={}",
            plugin_id,
        )
        return None
    if not all(
        _handler_key_belongs_to_plugin(key, plugin_id) for key in packaged.handlers
    ):
        logger.info(
            "packaged handler keys were minted under a different plugin id; "
            "rescanning so they match the runtime id: plugin_id={}",
            plugin_id,
        )
        return None
    return IsolatedPluginMetadata(
        entries_preview=_overlay_entry_declaration(
            packaged.entries,
            dict(conf) if isinstance(conf, Mapping) else {},
            dict(pdata) if isinstance(pdata, Mapping) else {},
        ),
        handlers=dict(packaged.handlers),
        entry_methods=dict(packaged.entry_methods),
    )


def _metadata_rebuild_manifest(
    config_path: Path,
    plugin_id: str | None,
    conf: object,
    pdata: object,
) -> dict[str, object] | None:
    """Return the manifest only when scanned metadata may describe this package."""
    try:
        manifest = tomllib.loads(Path(config_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    manifest_pdata = (
        manifest.get("plugin") if isinstance(manifest.get("plugin"), dict) else {}
    )
    if plugin_id is not None and str(manifest_pdata.get("id") or "") != plugin_id:
        logger.info(
            "packaged metadata rebuild skipped: runtime id differs from manifest id; "
            "plugin_id={}, path={}",
            plugin_id, config_path,
        )
        return None
    if conf is not None and entries_config_digest(conf, pdata) != entries_config_digest(
        manifest, manifest_pdata
    ):
        logger.info(
            "packaged metadata rebuild skipped: effective configuration overrides entries; "
            "plugin_id={}, path={}",
            plugin_id, config_path,
        )
        return None
    return manifest


def _snapshot_package_tree_for_rebuild(
    config_path: Path,
    *,
    plugin_id: str | None = None,
    conf: object = None,
    pdata: object = None,
) -> SourceTreeSnapshot | None:
    """Fingerprint eligible packages before scanning, without hashing overrides."""
    plugin_dir = Path(config_path).parent
    if not packaged_metadata_needs_rebuild(plugin_dir):
        return None
    if _metadata_rebuild_manifest(config_path, plugin_id, conf, pdata) is None:
        return None
    return snapshot_packaged_metadata_rebuild_tree(plugin_dir)


def _refresh_scanned_packaged_metadata(
    config_path: Path,
    plugin_id: str,
    scanned: IsolatedPluginMetadata,
    *,
    before_scan: SourceTreeSnapshot | None,
    conf: object,
    pdata: object,
) -> None:
    """Turn the scan a refused package forced into the next start's fast path.

    Two refusal reasons have distinct persistence locations (see dispatch at the function's end): outdated schema rewrites packaged metadata in place;
    ``build_env`` differing from this machine writes the host runtime cache without changing distribution bytes.
    After either successful write, the next startup hits directly without scanning.

    Only when the effective ``entries`` table is the manifest's own: the file
    describes the package, and an active profile or runtime override that
    rewrote the table would otherwise be frozen into it (the digest gate in
    ``_read_packaged_isolated_metadata`` would then treat that machine's
    overrides as the packaged baseline).
    """
    if before_scan is None:
        return
    plugin_dir = Path(config_path).parent
    # Revalidate after the scan as the manifest may have changed in the meantime.
    manifest = _metadata_rebuild_manifest(config_path, plugin_id, conf, pdata)
    if manifest is None:
        return
    manifest_pdata = manifest.get("plugin") if isinstance(manifest.get("plugin"), dict) else {}
    write_kwargs = dict(
        before_scan=before_scan,
        entries=scanned.entries_preview,
        handlers=scanned.handlers,
        entry_methods=scanned.entry_methods,
        conf=manifest,
        pdata=manifest_pdata,
    )
    if stale_packaged_schema_version(plugin_dir) is not None:
        refresh_stale_packaged_metadata(plugin_dir, **write_kwargs)
    else:
        write_local_packaged_metadata(plugin_dir, **write_kwargs)


def _clamp_step_timeout(
    configured: float,
    budget: float | None,
    *,
    floor: float,
) -> float:
    """Fit one step of a stop or a start inside what is left of a round budget.

    Never *widens*: a plugin that declared a 0.5 s timeout of its own keeps it
    however generous the budget is, and however low the floor is. The floor
    raises a squeezed budget, it does not raise the configured value.

    One helper for both phases on purpose. The stop side had no floor at all,
    so a spent budget handed ``shutdown_timeout≈0`` down and every remaining
    plugin was killed outright instead of being asked to shut down.
    """
    if budget is None:
        return configured
    return min(configured, max(floor, budget))


def _remaining_step_budget(deadline: float | None) -> float | None:
    """Seconds left before ``deadline``, or ``None`` when the step is unbounded.

    A start is several sequential expensive steps, not one. Handing the whole
    call a single duration bounds only the step it is applied to and lets every
    other step run past the round's wall clock; recomputing against an absolute
    deadline is what makes the budget cover the call rather than one line of it
    (CodeRabbit).
    """
    return None if deadline is None else deadline - time_module.monotonic()
plugin_registry_service = PluginRegistryService()
def _persist_user_runtime_intent(
    plugin_id: str,
    enabled: bool,
    *,
    previous_plugin_ids: tuple[str, ...] = (),
    runtime_state_changed: bool = False,
) -> bool:
    if not enabled and not PLUGIN_SYNC_AUTO_START_ON_TOGGLE:
        # A manual stop is temporary. Persisting enabled=false would make the
        # autostart selection skip the plugin at the next launch even though
        # its auto-start switch still says on; whether it runs at launch is
        # the switch's job alone.
        return False
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

    # Manual start/reload persists runtime intent only, even in legacy sync
    # mode. Only the independent auto-start switch grants approval.
    return True


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
        persisted = await asyncio.to_thread(
            _persist_user_runtime_intent,
            plugin_id,
            enabled,
            previous_plugin_ids=previous_plugin_ids,
            runtime_state_changed=True,
        )
        response["preference_persisted"] = persisted
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


# Public alias: cross-module callers such as hot_reload_service should use the name without an underscore
# to avoid external dependencies on private symbols.
plugin_is_running_sync = _plugin_is_running_sync


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


def _set_plugin_runtime_auto_start_sync(
    plugin_id: str, auto_start: bool, *, restore_enabled: bool = False,
) -> None:
    with state.acquire_plugins_write_lock():
        raw_meta = state.plugins.get(plugin_id)
        if not isinstance(raw_meta, dict):
            return
        raw_meta["runtime_auto_start"] = auto_start
        if restore_enabled:
            raw_meta["runtime_enabled"] = True
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

    for root in PLUGIN_CONFIG_ROOTS:
        resolved_root = root.resolve()
        config_file = (resolved_root / normalized_plugin_id / "plugin.toml").resolve()
        if resolved_root not in config_file.parents:
            continue
        if config_file.exists():
            return config_file
    return None


def _resolve_plugin_config_path_sync(
    plugin_id: str,
    plugin_meta: dict[str, object] | None,
) -> Path | None:
    config_path = _resolve_registered_config_path_sync(plugin_meta)
    if config_path is None:
        config_path = _get_plugin_config_path(plugin_id)
    if config_path is None:
        return None
    try:
        return config_path.resolve()
    except Exception:
        return config_path


def _register_or_replace_host_sync(plugin_id: str, host: PluginHostContract) -> int:
    rejected_by_shutdown_latch = False
    with state.acquire_plugin_hosts_write_lock():
        if _operations_shutting_down:
            # Authoritative latch checkpoint. The start_plugin entry check only fails fast, saving
            # one spawn; it cannot close the window between that check passing and registration when shutdown sets the latch.
            # Check and registration share a write-lock critical section; _shutdown_hosts takes its snapshot
            # under the same lock for reading and Phase 4 clears under it for writing. A host registered after shutdown
            # sets the latch is either captured before the snapshot (and shut down normally) or rejected here,
            # leaving no orphan process. After this raises, start_plugin's except
            # ServerDomainError branch invokes _cleanup_started_host to collect the already spawned
            # subprocess.
            rejected_by_shutdown_latch = True
        else:
            if plugin_id in state.plugin_hosts:
                existing_host = state.plugin_hosts.get(plugin_id)
                if existing_host is not None and existing_host is not host:
                    logger.warning("Plugin {} already exists in plugin_hosts, replacing host", plugin_id)
            state.plugin_hosts[plugin_id] = host
            current_count = len(state.plugin_hosts)
    if rejected_by_shutdown_latch:
        # Raise outside the lock: ServerDomainError is a frozen dataclass rejecting attribute assignment;
        # raising inside a generator context manager's with block lets gen.throw's
        # __traceback__ assignment turn it into TypeError (see operation_lock's explanation of
        # why bounded_operation_wait is a class rather than @contextmanager).
        # The check itself remains inside the write-lock critical section, preserving exclusion against snapshot/clear.
        raise _to_domain_error(
            code="PLUGIN_OPERATION_SHUTTING_DOWN",
            message="Server is shutting down; plugin start rejected",
            status_code=409,
            plugin_id=plugin_id,
            error_type="ServerShuttingDown",
        )
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


# Wall-clock budget for the reload-all stop phase.
#
# Each plugin stop separately acquires the cross-process lock (see reload_all_plugins below:
# this part cannot truly run in parallel), so duration scales linearly with plugin count while the frontend waits only 30s.
# Stop on budget exhaustion, report already stopped plugins normally, and leave the rest in place, avoiding a request
# timeout while operations continue taking effect in the background.
# Env: NEKO_PLUGIN_RELOAD_ALL_BUDGET

_RELOAD_ALL_BUDGET_SECONDS = env_seconds("NEKO_PLUGIN_RELOAD_ALL_BUDGET", 20.0)


class PluginLifecycleService:
    @serialized_plugin_operation
    async def start_plugin(
        self,
        plugin_id: str,
        restore_state: bool = False,
        *,
        refresh_registry: bool = True,
        persist_user_intent: bool = False,
        start_deadline: float | None = None,
    ) -> dict[str, object]:
        return await self._start_plugin_under_lock(
            plugin_id,
            restore_state,
            refresh_registry=refresh_registry,
            persist_user_intent=persist_user_intent,
            start_deadline=start_deadline,
        )

    async def _start_plugin_under_lock(
        self,
        plugin_id: str,
        restore_state: bool = False,
        *,
        refresh_registry: bool = True,
        persist_user_intent: bool = False,
        start_deadline: float | None = None,
        operation_scope: _HeldPluginOperationLock | None = None,
    ) -> dict[str, object]:
        """Start under a caller-owned operation lock without reacquiring it.

        Batch tasks borrow their parent's serialization scope. This method and
        its callees must not acquire the operation lock: its reentrancy is tied
        to the owning asyncio task, and batch children are different tasks.
        """
        if operation_scope is not None:
            operation_scope.require_active()
        elif not _operation_lock_is_held_by_current_task():
            raise RuntimeError("Plugin startup requires the operation lock")
        _hot_reload_failed.discard(plugin_id)
        if _operations_shutting_down:
            # Shutdown has begun: starting plugins now could place them after the host snapshot, creating orphan processes
            # nobody stops. This check fails fast to save a spawn; the authoritative checkpoint is inside
            # _register_or_replace_host_sync's registration critical section, closing the window when the latch is set
            # after this check but before registration (see _operations_shutting_down).
            raise _to_domain_error(
                code="PLUGIN_OPERATION_SHUTTING_DOWN",
                message="Server is shutting down; plugin start rejected",
                status_code=409,
                plugin_id=plugin_id,
                error_type="ServerShuttingDown",
            )
        start_time = time_module.perf_counter()
        original_plugin_id = plugin_id
        current_plugin_id = plugin_id
        resolved_plugin_ids = [plugin_id]
        development_snapshot = await asyncio.to_thread(development_store.registration_for_plugin_sync, plugin_id)
        if development_snapshot is not None:
            await asyncio.to_thread(development_store.validate_development_snapshot_sync, development_snapshot)

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
                    # Machine-readable so callers can tell this apart from a real start:
                    # no process was (re)created and the saved config was not re-read.
                    "already_running": True,
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
            if startup_timeout_value is not None:
                _active_startup_timeouts[original_plugin_id] = startup_timeout_value
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
            # This is real disk I/O that previously ran directly on the event-loop thread. Cold-reading a vendor directory
            # with 200 distributions measured 0.31s locally on Windows; 600 took 0.97s
            # (warm reads: 45ms / 184ms), during which the entire server responds to no requests
            # (Greptile). Move it into a thread, as every surrounding step already does (host creation,
            # metadata scanning, runtime metadata persistence).
            #
            # Do not wrap this in a timeout. This is a prerequisite gate, not a compressible step: after timeout,
            # blindly starting leads to an immediate missing-dependency crash at the higher cost of a full subprocess
            # spawn, while declaring missing dependencies incorrectly hard-fails healthy plugins, the worst outcome.
            # Its duration already counts toward the budget: _remaining_step_budget is read only **after**
            # this step, so every subsequent step's limit already deducts its cost.
            (
                python_requirements,
                python_requirement_paths,
                unsatisfied_python_requirements,
            ) = await asyncio.to_thread(
                _resolve_python_requirements,
                conf,
                config_path,
                current_plugin_id,
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
                create_plugin_host,
                plugin_id=current_plugin_id,
                entry_point=entry,
                config_path=config_path,
                **({"source_only": True} if development_snapshot is not None else {}),
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

            # Obtain metadata **before** starting the host: read packaged metadata directly when present;
            # otherwise spawn an isolated worker to import it.
            #
            # Ordering is essential. After host startup, this import would run concurrently with the plugin process's
            # own import. Plugins acquiring file locks, binding ports, or starting singletons at module level fail on
            # the second import, causing lifecycle cleanup to kill a healthy host and report startup
            # failure; plugins without direct conflicts still execute import-time side effects twice (codex).
            # Before this PR the path was safe because refresh_plugin scanned before host
            # startup. Since refresh no longer scans, restore that ordering here.
            #
            # Narrow the limit to the remaining budget: scanning has its own 10s ceiling, so constraining only host startup lets
            # one cold scan exceed the entire reload's wall-clock budget (CodeRabbit).
            module_path, class_name = entry.split(":", 1)
            isolated_metadata = None if development_snapshot is not None else await asyncio.to_thread(
                partial(
                    _read_packaged_isolated_metadata,
                    config_path,
                    current_plugin_id,
                    conf=conf,
                    pdata=pdata,
                )
            )
            if isolated_metadata is None:
                # Calculate the budget only after reading packaged metadata. That read may hash an entire modified
                # tree before falling back; a limit computed beforehand is stale, letting the worker receive
                # almost 10s and making the whole reload exceed the promised wall-clock duration (codex).
                # Apply the same criterion as recalculating startup_timeout_value below.
                scan_timeout = _clamp_step_timeout(
                    _DEFAULT_METADATA_SCAN_TIMEOUT,
                    _remaining_step_budget(start_deadline),
                    floor=_MIN_CLAMPED_START_TIMEOUT,
                )
                # If packaged metadata has an outdated schema **or build_env differs from this machine**, the scan
                # learns what the packager should have written: persist it so the next startup uses the fast path. Capture the fingerprint
                # before import and compare afterward, rejecting tree changes caused by import just as the packager does;
                # cases. reload_all has an overall budget, so exclude optional optimization there; automatic application startup has
                # no deadline, so perform it there instead.
                before_scan = (
                    await asyncio.to_thread(
                        _snapshot_package_tree_for_rebuild,
                        config_path,
                        plugin_id=current_plugin_id,
                        conf=conf,
                        pdata=pdata,
                    )
                    if start_deadline is None and development_snapshot is None
                    else None
                )
                isolated_metadata = await asyncio.to_thread(
                    scan_plugin_metadata_isolated,
                    plugin_id=current_plugin_id,
                    module_path=module_path,
                    class_name=class_name,
                    config_path=config_path,
                    conf=conf,
                    pdata=pdata,
                    python_requirement_paths=python_requirement_paths,
                    timeout=scan_timeout,
                    **({"source_only": True} if development_snapshot is not None else {}),
                )
                await asyncio.to_thread(
                    _refresh_scanned_packaged_metadata,
                    config_path,
                    current_plugin_id,
                    isolated_metadata,
                    before_scan=before_scan,
                    conf=conf,
                    pdata=pdata,
                )

            if start_deadline is not None and startup_timeout_value is not None:
                # reload-all passes this pass's deadline inward. Checking only once **before** startup is
                # insufficient: startup beginning just before the deadline could still wait until its own
                # startup timeout, exceeding the promised wall-clock duration while the frontend has already
                # given up and plugin state is still changing (codex / CodeRabbit / Greptile).
                #
                # Pass the limit inward rather than wrapping asyncio.wait_for: start_plugin has
                # @serialized_plugin_operation, whose wrapper shields cancellation once it acquires the lock;
                # an outer timeout would only misreport a real result as timed out (see the stop-side explanation).
                #
                # Compute this **after** metadata retrieval. Metadata now precedes host startup and may
                # itself consume a scan_timeout; a ceiling calculated beforehand is stale by the time
                # _start_host_with_timeout actually runs, adding one scan per plugin to the startup phase's
                # wall-clock duration, precisely what this clamp is meant to prevent
                # (coderabbit). _remaining_step_budget uses an absolute deadline, so recalculating here
                # is correct.
                startup_timeout_value = _clamp_step_timeout(
                    startup_timeout_value,
                    _remaining_step_budget(start_deadline),
                    floor=_MIN_CLAMPED_START_TIMEOUT,
                )

            if development_snapshot is not None:
                await asyncio.to_thread(development_store.validate_development_snapshot_sync, development_snapshot)
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

            await asyncio.to_thread(
                install_isolated_plugin_metadata,
                current_plugin_id,
                isolated_metadata,
            )
            entries_preview = isolated_metadata.entries_preview
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
        finally:
            _active_startup_timeouts.pop(original_plugin_id, None)

    async def start_plugins_batch(
        self,
        independent_plugin_ids: Sequence[str],
        ordered_plugin_ids: Sequence[str] = (),
        *,
        concurrency: int | None = None,
        refresh_registry: bool = False,
    ) -> dict[str, object]:
        """Start independent plugins concurrently, then dependents in order.

        Each concurrent wave excludes external mutations until its active
        starts finish, then yields the operation lock to queued callers. Child
        tasks use the undecorated implementation inside that scope. Serial
        starts use the public entry point's cancellation-safe per-plugin lock.
        """
        limit = (
            PLUGIN_AUTOSTART_CONCURRENCY if concurrency is None else int(concurrency)
        )
        limit = max(1, limit)
        started: list[str] = []
        failed: list[str] = []

        async def _start_one(
            plugin_id: str, *, operation_scope: _HeldPluginOperationLock | None = None
        ) -> None:
            try:
                if operation_scope is not None:
                    # Drain every active start before releasing the wave lock.
                    # A spawned host must register or finish its cleanup.
                    operation = asyncio.create_task(
                        self._start_plugin_under_lock(
                            plugin_id,
                            refresh_registry=refresh_registry,
                            operation_scope=operation_scope,
                        )
                    )
                    await await_cancellation_safe(operation)
                else:
                    # This primitive also cancels an unstarted lock waiter.
                    # Shielding it again would prevent that cancellation.
                    await self.start_plugin(
                        plugin_id, refresh_registry=refresh_registry
                    )
            except Exception as error:
                failed.append(plugin_id)
                logger.error(
                    "failed to autostart plugin at startup: plugin_id={}, err_type={}, err={}",
                    plugin_id,
                    type(error).__name__,
                    str(error),
                )
            else:
                started.append(plugin_id)
                logger.debug("autostart plugin started: plugin_id={}", plugin_id)

        independent = list(dict.fromkeys(
            str(plugin_id) for plugin_id in independent_plugin_ids if plugin_id
        ))
        independent_set = set(independent)
        ordered = list(dict.fromkeys(
            str(plugin_id) for plugin_id in ordered_plugin_ids
            if plugin_id and str(plugin_id) not in independent_set
        ))

        if limit <= 1 or len(independent) <= 1:
            for plugin_id in independent:
                await _start_one(plugin_id)
        else:
            # Limit the lock scope to one wave. With a single lock around all
            # queued starts, management requests can exhaust their wait budget
            # even when each individual plugin starts within its own timeout.
            for offset in range(0, len(independent), limit):
                wave = independent[offset : offset + limit]
                try:
                    async with plugin_operation_lock.hold() as operation_scope:
                        # Collect unexpected task errors only after every sibling
                        # has finished; otherwise the wave lock releases too early.
                        outcomes = await asyncio.gather(
                            *(
                                _start_one(plugin_id, operation_scope=operation_scope)
                                for plugin_id in wave
                            ),
                            return_exceptions=True,
                        )
                        for plugin_id, outcome in zip(wave, outcomes):
                            if isinstance(outcome, BaseException):
                                # A startup failure was already recorded by
                                # _start_one; retain any unexpected task failure.
                                if plugin_id not in failed and plugin_id not in started:
                                    failed.append(plugin_id)
                                    logger.error(
                                        "autostart task failed unexpectedly: plugin_id={}, err_type={}, err={}",
                                        plugin_id,
                                        type(outcome).__name__,
                                        str(outcome),
                                    )
                except Exception as error:
                    # A failed lock acquisition must not abort server startup.
                    for plugin_id in wave:
                        if plugin_id not in started and plugin_id not in failed:
                            failed.append(plugin_id)
                    logger.error(
                        "autostart wave failed: plugin_ids={}, err_type={}, err={}",
                        wave, type(error).__name__, str(error),
                    )

        # Providers have completed; dependents keep their existing order and
        # yield the operation lock after each plugin, as serial autostart did.
        for plugin_id in ordered:
            await _start_one(plugin_id)

        logger.info(
            "autostart batch finished: started={}, failed={}, concurrency_limit={}",
            len(started),
            len(failed),
            limit,
        )
        return {"started": started, "failed": failed}

    @serialized_plugin_operation
    async def stop_plugin(
        self,
        plugin_id: str,
        *,
        persist_user_intent: bool = False,
        stop_deadline: float | None = None,
    ) -> dict[str, object]:
        _hot_reload_failed.discard(plugin_id)
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
            # Calculate remaining budget here rather than in the caller. This body runs only
            # **after** @serialized_plugin_operation acquires the lock, so only the current remaining
            # time is accurate. Calculating outside would let shutdown wait 19s for a lock yet retain a ceiling
            # based on 20s, exceeding the stop phase's promised duration (codex). This follows the startup-side
            # start_deadline pattern.
            await host_obj.shutdown(
                timeout=(
                    PLUGIN_SHUTDOWN_TIMEOUT
                    if stop_deadline is None
                    else _clamp_step_timeout(
                        PLUGIN_SHUTDOWN_TIMEOUT,
                        _remaining_step_budget(stop_deadline),
                        floor=_MIN_CLAMPED_STOP_TIMEOUT,
                    )
                )
            )
            if (
                await asyncio.to_thread(development_store.registration_for_plugin_sync, plugin_id) is not None
                and host_obj.is_alive()
            ):
                raise _to_domain_error(code="PLUGIN_STOP_FAILED",
                    message="Development plugin process is still running; association was retained",
                    status_code=409, plugin_id=plugin_id, error_type="PluginStillRunning")
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
                # This step also runs under the lock and within the stop-phase budget. Its independent 2s timeout
                # could otherwise let a stuck main_server add another two seconds beyond the shutdown budget
                # while holding the lock (codex). Narrow it to the remaining budget.
                #
                # Do **not** use _clamp_step_timeout: that lower bound is for startup, because
                # a plugin we stopped must get one real attempt. Here remote cleanup is best-effort;
                # forcing another 1s after budget exhaustion adds a second of lock occupancy per plugin.
                # （CodeRabbit）。
                #
                # Nor may we simply skip when the budget runs out; my previous version did that incorrectly.
                # This is the repository's only cleanup of remote tool registrations, with no reconciliation or retry fallback:
                # skipping removes the host while main_server still advertises its tools to the model. Selecting one
                # only reports that the plugin is not running, and this never heals itself (Greptile).
                #
                # Give a tiny lower bound so the request can at least be sent. This is **strictly better** than skipping:
                # failure merely returns to the skipped state (this POST clears a whole source idempotently,
                # so resending is harmless), while success removes ghost tools. Normally this local
                # POST returns within milliseconds, so the lower bound never applies.
                cleanup_budget = _remaining_step_budget(stop_deadline)
                cleanup_result = await clear_plugin_llm_tools(
                    plugin_id,
                    timeout=(
                        None
                        if cleanup_budget is None
                        else min(
                            _CLEAR_TOOLS_BUDGET_SECONDS,
                            max(_MIN_TOOL_CLEANUP_TIMEOUT, cleanup_budget),
                        )
                    ),
                )
                if isinstance(cleanup_result, dict) and not cleanup_result.get("ok"):
                    # Raise to warning. Cleanup is best-effort, but failure leaves the model seeing an unusable
                    # tool with no retry mechanism; debug level would leave effectively no trace.
                    logger.warning(
                        "plugin stopped but its LLM tools may still be advertised: "
                        "plugin_id={}, reason={}",
                        plugin_id,
                        cleanup_result.get("error") or cleanup_result.get("status_code"),
                    )
            except Exception as exc:
                # Use the same wording and level as ok=False above: both paths have identical consequences;
                # tools may remain on main_server with no retry mechanism.
                #
                # This exception path is particularly subtle: clear_plugin_tools catches only httpx.HTTPError and
                # asyncio.TimeoutError internally. A response claiming JSON content-type with malformed body
                # makes resp.json() raise ValueError all the way here (CodeRabbit). Keeping it at
                # debug would likewise make ghost tools on this path untraceable.
                logger.warning(
                    "plugin stopped but its LLM tools may still be advertised: "
                    "plugin_id={}, err_type={}, err={}",
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

    @serialized_plugin_operation
    async def set_plugin_auto_start(
        self, plugin_id: str, auto_start: bool
    ) -> dict[str, object]:
        """Persist the user's auto-start preference without touching the process.

        The running host is left as it is. Turning auto-start on is the user
        asking for the plugin to run at launch, so it also lifts what would
        otherwise still block that: a persisted ``enabled=false`` left by an
        earlier stop, and the pending approval of a freshly installed plugin.
        """
        meta = await asyncio.to_thread(_get_plugin_meta_sync, plugin_id)
        if meta is None:
            raise _to_domain_error(
                code="PLUGIN_NOT_FOUND",
                message=f"Plugin '{plugin_id}' not found",
                status_code=404,
                plugin_id=plugin_id,
                error_type="PluginNotFound",
            )
        restore_enabled = auto_start and (
            meta.get("runtime_enabled") is False
            or await asyncio.to_thread(get_runtime_override, plugin_id) is False
        )
        try:
            was_pending = auto_start and not await asyncio.to_thread(
                is_autostart_approved, plugin_id, strict=True
            )
        except OSError as exc:
            raise ServerDomainError(
                code="PLUGIN_AUTOSTART_APPROVAL_PERSIST_FAILED",
                message="PLUGIN_AUTOSTART_APPROVAL_PERSIST_FAILED",
                status_code=500,
                details={
                    "plugin_id": plugin_id,
                    "auto_start": auto_start,
                    "error_type": type(exc).__name__,
                    "runtime_state_changed": False,
                },
                log_level="error",
            ) from exc
        # The pending approval is the gate that keeps unapproved code from
        # running at launch, so it is lifted only after the preference is
        # durable. Every failure below therefore leaves the gate in place.
        previous_override = await asyncio.to_thread(get_runtime_override_entry, plugin_id)
        try:
            if restore_enabled:
                await asyncio.to_thread(
                    set_runtime_override, plugin_id, True, auto_start=True
                )
            else:
                await asyncio.to_thread(
                    set_runtime_auto_start_override, plugin_id, auto_start
                )
        except RuntimeOverridePersistenceError as exc:
            raise ServerDomainError(
                code="PLUGIN_RUNTIME_PREFERENCE_PERSIST_FAILED",
                message="PLUGIN_RUNTIME_PREFERENCE_PERSIST_FAILED",
                status_code=500,
                details={
                    "plugin_id": plugin_id,
                    "auto_start": auto_start,
                    "error_type": type(exc).__name__,
                    "runtime_state_changed": False,
                },
                log_level="error",
            ) from exc
        if was_pending and not await asyncio.to_thread(clear_autostart_pending, plugin_id):
            # Undo the preference so the failed request changes nothing. If the
            # undo fails too, the plugin is still pending and cannot autostart.
            written_override = await asyncio.to_thread(get_runtime_override_entry, plugin_id)
            try:
                rolled_back = await asyncio.to_thread(
                    restore_runtime_override,
                    plugin_id,
                    previous_override,
                    expected_current=written_override,
                )
            except (RuntimeOverridePersistenceError, OSError):
                rolled_back = False
            if not rolled_back:
                logger.error(
                    "Failed to roll back auto-start preference for {}; it stays pending approval",
                    plugin_id,
                )
            raise ServerDomainError(
                code="PLUGIN_AUTOSTART_APPROVAL_PERSIST_FAILED",
                message="PLUGIN_AUTOSTART_APPROVAL_PERSIST_FAILED",
                status_code=500,
                details={
                    "plugin_id": plugin_id,
                    "auto_start": auto_start,
                    "error_type": "AutostartApprovalPersistenceError",
                    "runtime_state_changed": False,
                },
                log_level="error",
            )
        await asyncio.to_thread(
            _set_plugin_runtime_auto_start_sync, plugin_id, auto_start,
            restore_enabled=restore_enabled,
        )
        return {
            "success": True,
            "plugin_id": plugin_id,
            "auto_start": auto_start,
            "message": "Plugin auto-start preference updated",
        }

    @serialized_plugin_operation
    async def reload_plugin(
        self,
        plugin_id: str,
        *,
        only_if_running: bool = False,
    ) -> dict[str, object]:
        """Restart a plugin (stop + start). When ``only_if_running`` is set, a
        plugin that is no longer running is left stopped instead of started.

        ``only_if_running`` serves the hot-reload watcher: it checked running outside the lock,
        but a user may have stopped the plugin before this recheck. Without the parameter,
        reload's start phase would restart it and persist autostart intent, silently turning a code change into
        a change to the user's startup intent.
        """
        _emit_lifecycle_event(event_type="plugin_reload_requested", plugin_id=plugin_id)
        if not only_if_running:
            _hot_reload_failed.discard(plugin_id)

        development_snapshot = await asyncio.to_thread(development_store.registration_for_plugin_sync, plugin_id)
        if development_snapshot is not None:
            from plugin.server.application.plugins.development_service import preflight_development_sync
            await asyncio.to_thread(preflight_development_sync, development_snapshot)

        is_running = await asyncio.to_thread(_plugin_is_running_sync, plugin_id)
        if is_running:
            try:
                await self.stop_plugin(plugin_id)
            except ServerDomainError as error:
                if error.status_code != 404:
                    raise
        elif only_if_running and not plugin_needs_hot_reload_recovery(plugin_id):
            # Rechecking finds the plugin already stopped (the user clicked Stop after the watcher's unlocked check):
            # do not start it or persist autostart intent. Return skipped so the caller knows this pass did nothing.
            _emit_lifecycle_event(event_type="plugin_reload_skipped", plugin_id=plugin_id)
            return {
                "success": True,
                "plugin_id": plugin_id,
                "skipped": True,
                "message": "Plugin not running",
            }

        # Manual reload persists runtime intent like Start; neither action
        # approves a pending plugin for future automatic launches.
        try:
            if development_snapshot is not None:
                await asyncio.to_thread(development_store.validate_development_snapshot_sync, development_snapshot)
            result = await self.start_plugin(plugin_id, persist_user_intent=not only_if_running)
        except BaseException:
            # start_plugin already dropped the permission on entry; restore it
            # even when cancellation lands after the stop half has run.
            if only_if_running and not _operations_shutting_down:
                _hot_reload_failed.add(plugin_id)
            raise
        _hot_reload_failed.discard(plugin_id)
        _emit_lifecycle_event(event_type="plugin_reloaded", plugin_id=plugin_id)
        return result

    @serialized_plugin_operation
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

        # Sequential, not gather.
        #
        # This previously used asyncio.gather, but bought no concurrency: each
        # _safe_stop_for_reload's inner stop_plugin itself has
        # @serialized_plugin_operation, whose lock recognizes reentrancy by asyncio.Task
        # (_OPERATION_OWNER stores the task object). gather creates a new Task for each coroutine;
        # a child's current_task necessarily differs from the lock owner's, so reentrancy fails and all N stops
        # queue strictly. Task-based ownership is intentional, preventing unrelated tasks from borrowing another's lock;
        # changing reentrancy to achieve parallelism is therefore unsafe.
        #
        # A sequential loop makes the code reflect its actual behavior and also allows intermediate
        # budget checks that gather cannot provide.
        stop_outcomes = []
        development_snapshots: dict[str, development_store.DevelopmentSnapshot] = {}
        skipped_over_budget: list[str] = []
        stop_deadline = time_module.monotonic() + _RELOAD_ALL_BUDGET_SECONDS
        for index, plugin_id in enumerate(running_plugin_ids):
            if time_module.monotonic() > stop_deadline:
                # Record remaining plugins in skipped before returning; they must not be absent from both successes and failures,
                # leaving the caller with a result missing plugins and no explanation
                # of their disappearance.
                skipped_over_budget = list(running_plugin_ids[index:])
                logger.warning(
                    "reload_all stop phase over budget after {}s, {} plugin(s) skipped",
                    _RELOAD_ALL_BUDGET_SECONDS,
                    len(skipped_over_budget),
                )
                break
            try:
                snapshot = await asyncio.to_thread(development_store.registration_for_plugin_sync, plugin_id)
                if snapshot is not None:
                    from plugin.server.application.plugins.development_service import preflight_development_sync
                    await asyncio.to_thread(preflight_development_sync, snapshot)
                    development_snapshots[plugin_id] = snapshot
            except ServerDomainError as exc:
                # Keep the last working development instance when edits are
                # invalid, just like the single-plugin reload path.
                stop_outcomes.append(_ReloadOutcome(plugin_id=plugin_id, success=False, error=exc.message))
                continue
            except Exception as exc:
                # An unexpected preflight error (e.g. a symlink loop making
                # Path.resolve raise RuntimeError) must fail only this plugin:
                # escaping would abort the batch after earlier plugins were
                # stopped, and the start phase would never bring them back.
                logger.error(
                    "reload_all preflight raised unexpectedly: plugin_id={}, err_type={}, err={}",
                    plugin_id,
                    type(exc).__name__,
                    exc,
                )
                stop_outcomes.append(
                    _ReloadOutcome(plugin_id=plugin_id, success=False, error=f"{type(exc).__name__}: {exc}")
                )
                continue
            # This stop must also respect the remaining budget: checking only before starting lets a slow shutdown
            # (or an increased NEKO_PLUGIN_SHUTDOWN_TIMEOUT) push the entire phase past
            # its promised wall-clock ceiling (codex).
            #
            # Do not wrap the outside in asyncio.wait_for. stop_plugin has
            # @serialized_plugin_operation, whose wrapper shields cancellation after acquiring the lock,
            # waits for the inner operation, then raises CancelledError (operation_lock's shield loop).
            # The request would still block for the entire shutdown, then report a **successful** stop as timed out,
            # excluding the plugin from restarts and leaving it stopped (codex).
            #
            # Pass the budget inward: bounded_operation_wait controls lock waiting,
            # shutdown_timeout controls actual shutdown, and both finish within budget while returning
            # the actual result.
            # Calculate lock wait and shutdown budgets separately using the time remaining **now**, without sharing a snapshot.
            # Otherwise a lock acquisition consuming all remaining time would give shutdown another full remaining allowance,
            # doubling the pass budget: the same error as assigning separate deadlines to nested locks, in a different place
            # (this adversarial review pass).
            remaining = max(0.0, stop_deadline - time_module.monotonic())
            with bounded_operation_wait(remaining):
                stop_outcomes.append(
                    await self._safe_stop_for_reload(
                        plugin_id, stop_deadline=stop_deadline,
                        development_snapshot=development_snapshots.get(plugin_id),
                    )
                )

        plugins_to_start: list[str] = []
        failed: list[dict[str, object]] = []
        for outcome in stop_outcomes:
            if outcome.success:
                plugins_to_start.append(outcome.plugin_id)
                continue
            failed.append({"plugin_id": outcome.plugin_id, "error": outcome.error or "Stop failed"})

        # Also mark skipped: the existing contract means not attempted, precisely the case for these plugins.
        # Only adding failed prevents callers from distinguishing stop failure from never being attempted.
        for plugin_id in skipped_over_budget:
            failed.append(
                {
                    "plugin_id": plugin_id,
                    "error": (
                        "skipped: reload exceeded its "
                        f"{_RELOAD_ALL_BUDGET_SECONDS:g}s budget"
                    ),
                }
            )

        reloaded: list[str] = []
        ordered_plugin_ids = await plugin_registry_service.order_plugin_ids(plugins_to_start)
        # Startup has its **own** budget, independent of the stop phase's remainder.
        #
        # Startup does need a ceiling: start_plugin is usually much slower than stop (configuration reads, subprocess
        # spawning, metadata scans), so constraining only stopping still lets reload exceed the frontend's 30s
        # (CodeRabbit). But the phases cannot share one budget: plugins reaching here were **already stopped
        # by us**, and each stop owes a start attempt. With a shared budget, one slow shutdown can exhaust
        # everything, quietly turning reload into stop: all plugins are offline while the caller sees
        # only an over-budget line. Accept another phase budget rather than leaving the user's plugins
        # stopped.
        #
        # This loop also **does not** exit when the budget runs out, intentionally differing from the stop phase.
        # Skipping during stopping is safe: unattempted plugins remain running. Skipping during startup
        # permanently leaves a plugin we just stopped offline: autostart runs only once at server
        # startup, with no periodic reconciliation to recover it. Users would have to start it manually or restart
        # the entire server (Greptile). Every stopped plugin must therefore receive one startup attempt;
        # after budget exhaustion each gets the minimum allowance. Whether it suffices is separate; never trying is unacceptable.
        #
        # Explicit cost: startup's wall-clock ceiling is thus budget + remaining plugin count x minimum,
        # rather than a hard budget. Healthy paths never reach it; measured startup is fast and leaves budget unused.
        start_deadline = time_module.monotonic() + _RELOAD_ALL_BUDGET_SECONDS
        for plugin_id in ordered_plugin_ids:
            snapshot = development_snapshots.get(plugin_id)
            if snapshot is not None:
                try:
                    await asyncio.to_thread(development_store.validate_development_snapshot_sync, snapshot)
                except ServerDomainError as exc:
                    failed.append({"plugin_id": plugin_id, "error": exc.message})
                    continue
            # Startup likewise bounds both lock waiting and startup itself by remaining budget, symmetrically with stop
            # above; otherwise only one of the two phases would be constrained.
            #
            # Lock waiting uses the same minimum as step timeout and cannot reach zero: after budget exhaustion,
            # bounded_operation_wait(0.0) means no waiting. If another plugin operation holds the process
            # lock, start_plugin immediately raises PluginOperationBusy, leaving a plugin we just
            # stopped offline indefinitely (CodeRabbit). Removing the over-budget break was meant to prevent
            # this; zero waiting would reintroduce it through another path.
            #
            # The stop side intentionally needs no such minimum: if it cannot acquire the lock, the plugin remains running.
            remaining = max(
                _MIN_CLAMPED_START_TIMEOUT, start_deadline - time_module.monotonic()
            )
            with bounded_operation_wait(remaining):
                outcome = await self._safe_start_for_reload(
                    plugin_id, start_deadline=start_deadline
                )
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
            "skipped": list(skipped_over_budget),
            "message": message,
        }

    @serialized_plugin_operation
    async def delete_plugin(self, plugin_id: str) -> dict[str, object]:
        """Invoke the uninstall transaction and preserve the public response."""
        if await asyncio.to_thread(development_store.registration_for_plugin_sync, plugin_id) is not None:
            raise ServerDomainError(code="DEVELOPMENT_REMOVE_ASSOCIATION_REQUIRED",
                message="Use Remove association for a development plugin; its source and data are retained", status_code=409)
        try:
            result = await uninstall_plugin(plugin_id)
        except UninstallPluginError as exc:
            raise ServerDomainError(
                code=exc.code,
                message=exc.message,
                status_code=exc.status_code,
                details=dict(exc.details),
            ) from exc

        restored_builtin_started = (
            result.restored_builtin and result.runtime_restart == "succeeded"
        )

        _emit_lifecycle_event(
            event_type="plugin_deleted",
            plugin_id=plugin_id,
            data={
                "plugin_dir": str(result.plugin_dir),
                "deleted_from_disk": result.deleted_from_disk,
                "deleted_profile_dir": (
                    str(result.deleted_profile_dir)
                    if result.deleted_profile_dir
                    else None
                ),
                "restored_builtin": result.restored_builtin,
                "restored_builtin_started": restored_builtin_started,
                "restored_builtin_restart_error": result.runtime_restart_error,
                "preference_action": result.preference_action,
                "filesystem_rollback": result.filesystem_rollback,
                "runtime_restart": result.runtime_restart,
                "cleanup_pending": result.cleanup_pending,
            },
        )
        response: dict[str, object] = {
            "success": True,
            "plugin_id": plugin_id,
            "plugin_dir": str(result.plugin_dir),
            "deleted_from_disk": result.deleted_from_disk,
            "restored_builtin": result.restored_builtin,
            "restored_builtin_started": restored_builtin_started,
            "restored_builtin_restart_error": result.runtime_restart_error,
            "preference_action": result.preference_action,
            "filesystem_rollback": result.filesystem_rollback,
            "runtime_restart": result.runtime_restart,
            "cleanup_pending": result.cleanup_pending,
            "message": "Plugin deleted successfully",
        }
        return response

    async def retry_deferred_profile_cleanup(self) -> int:
        """Retry persisted uninstall cleanup jobs during server startup."""
        cleaned_profiles = await asyncio.to_thread(
            retry_deferred_profile_cleanup_sync
        )
        await asyncio.to_thread(retry_deferred_plugin_code_cleanup_sync)
        return cleaned_profiles

    async def _safe_stop_for_reload(
        self, plugin_id: str, *, stop_deadline: float | None = None,
        development_snapshot: development_store.DevelopmentSnapshot | None = None,
    ) -> _ReloadOutcome:
        try:
            if development_snapshot is not None:
                await asyncio.to_thread(development_store.validate_development_snapshot_sync, development_snapshot)
            await self.stop_plugin(plugin_id, stop_deadline=stop_deadline)
            return _ReloadOutcome(plugin_id=plugin_id, success=True)
        except PluginOperationBusy as error:
            return _ReloadOutcome(plugin_id=plugin_id, success=False, error=str(error))
        except ServerDomainError as error:
            if error.status_code == 404:
                return _ReloadOutcome(plugin_id=plugin_id, success=True)
            return _ReloadOutcome(plugin_id=plugin_id, success=False, error=error.message)

    async def _safe_start_for_reload(
        self, plugin_id: str, *, start_deadline: float | None = None
    ) -> _ReloadOutcome:
        try:
            await self.start_plugin(
                plugin_id,
                refresh_registry=False,
                start_deadline=start_deadline,
            )
            return _ReloadOutcome(plugin_id=plugin_id, success=True)
        except PluginOperationBusy as error:
            return _ReloadOutcome(plugin_id=plugin_id, success=False, error=str(error))
        except ServerDomainError as error:
            return _ReloadOutcome(plugin_id=plugin_id, success=False, error=error.message)
