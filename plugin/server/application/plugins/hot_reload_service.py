"""Plugin-source hot reload: watch directory changes and reload automatically.

Use standard-library polling of Path.stat mtime_ns/size signatures without third-party
watchdog dependencies or platform filesystem notifications. Each scan:

1. Target registered plugin config directories under PLUGIN_CONFIG_ROOTS plus
   source_dir from development registrations.
2. Collect *.py/plugin.toml signatures per directory and compare with the previous round.
3. Mark changed plugins pending and reload after changes remain quiet for
   PLUGIN_HOT_RELOAD_DEBOUNCE seconds.

Reuse PluginLifecycleService.reload_plugin, stopping and restarting processes,
so automatic and manual reload share identical locking/transaction paths.

Safety differences from manual reload are confined to triggering:

- Reload running plugins or those whose last automatic reload failed startup. Retry failure only
  after another source change; explicit start/stop revokes recovery permission under operation locks.
  A user's manually stopped plugin must not restart merely because a file
  changed, confusing code edits with startup intent. Check once outside locks
  for speed, then recheck through reload_plugin(only_if_running=True) under
  operation locks, covering intervening Stop races.
- Preflight before reload reuses development manifest/entry/dependency validation and .py compilation.
  Broken syntax skips this attempt, preserving old instances until the next change. Development
  plugins already have full reload_plugin preflight; provide equivalent protection here
  for ordinary built-in/installed plugins.
- When user operations contend, wait at most one debounce window for locks;
  PluginOperationBusy defers another window rather than jumping the queue.
- Watching assumes plugins do not write *.py/plugin.toml into their own config directories
  during execution. The 2026-09 plugin/plugins audit found runtime writes only to
  JSON/media/model files, with all plugin.toml writes confined to tests, outside watched
  signatures. Future runtime source generation must use excluded
  subdirectories such as vendor to avoid reload loops.
"""

from __future__ import annotations

import asyncio
import os
import time as time_module
from dataclasses import dataclass
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
    import tomli as tomllib  # type: ignore[no-redef]

from plugin.core.state import state
from plugin.logging_config import get_logger
from plugin.utils.source_paths import is_vendor_sync_path
from plugin.server.application.plugins import development as development_store
from plugin.server.application.plugins.lifecycle_service import (
    PluginLifecycleService,
    _resolve_registered_config_path_sync,
    active_startup_timeout,
    plugin_is_running_sync,
    plugin_needs_hot_reload_recovery,
)
from plugin.server.application.plugins._metadata_scan_settings import (
    METADATA_SCAN_TIMEOUT_SECONDS as _DEFAULT_SCAN_TIMEOUT_SECONDS,
)
from plugin.server.application.plugins.operation_lock import (
    PluginOperationBusy,
    bounded_operation_wait,
)
from plugin.server.domain.errors import ServerDomainError
from plugin.server.messaging.lifecycle_events import emit_lifecycle_event
from plugin.settings import (
    PLUGIN_CONFIG_ROOTS,
    PLUGIN_HOT_RELOAD,
    PLUGIN_HOT_RELOAD_DEBOUNCE,
    PLUGIN_HOT_RELOAD_INTERVAL,
    PLUGIN_HOT_RELOAD_MIN_INTERVAL_SECONDS,
    PLUGIN_STARTUP_TIMEOUT,
    PLUGIN_SHUTDOWN_TIMEOUT,
    PROCESS_SHUTDOWN_TIMEOUT,
)
from plugin.utils.time_utils import now_iso

logger = get_logger("server.application.plugins.hot_reload")

# Minimum postdebounce check interval: pending work shrinks polling toward this value,
# avoiding whole-second polling delays between quiet completion and reload.
_MIN_TICK_SECONDS = PLUGIN_HOT_RELOAD_MIN_INTERVAL_SECONDS
# stop() watcher-exit limit. Timeout during operation-lock-shielded in-flight reload
# means its final step still runs. Server shutdown passes 0.05 s instead,
# preserving host-teardown budget while registration latches reject late hosts.
_STOP_TIMEOUT_SECONDS = 1.5
# Restart has a different budget from shutdown: a healthy in-flight reload may
# still consume the host start/stop and isolated metadata scan limits.
_RESTART_DRAIN_OVERHEAD_SECONDS = (
    PLUGIN_SHUTDOWN_TIMEOUT + PROCESS_SHUTDOWN_TIMEOUT
    + _DEFAULT_SCAN_TIMEOUT_SECONDS + 5.0
)
_RESTART_DRAIN_SECONDS = PLUGIN_STARTUP_TIMEOUT + _RESTART_DRAIN_OVERHEAD_SECONDS
# The in-flight plugin's budget is re-derived this often while draining, so the
# timeout start_plugin records once it has read the config is picked up.
_DRAIN_RECHECK_SECONDS = 1.0
_BUSY_RETRY_SECONDS = 1.0
# Share development-preflight directory exclusions; their .py files are not plugin source.
_EXCLUDED_DIR_NAMES = development_store.SOURCE_EXCLUDED_DIR_NAMES


@dataclass(slots=True)
class _WatchTarget:
    plugin_id: str
    root: Path
    is_development: bool


def _signature_sync(root: Path) -> dict[str, tuple[int, int]]:
    """Collect ``{relative_path: (mtime_ns, size)}`` for watched files.

    Watch only *.py/plugin.toml: resource changes do not invalidate already-loaded
    data, and restarting a whole process for one image is not worthwhile.
    """
    signature: dict[str, tuple[int, int]] = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [
            name
            for name in dirnames
            if name not in _EXCLUDED_DIR_NAMES
            # `neko-plugin sync` writes thousands of third-party files into
            # its staging/backup trees at the plugin root; they are not source.
            and not (Path(dirpath) == root and is_vendor_sync_path(Path(name)))
        ]
        for name in filenames:
            if name != "plugin.toml" and not name.lower().endswith(".py"):
                continue
            path = Path(dirpath) / name
            try:
                stat = path.stat()
            except OSError:
                # Deleted between walk and stat: ignore this round;
                # absence in the next signature naturally triggers a diff.
                continue
            signature[path.relative_to(root).as_posix()] = (
                stat.st_mtime_ns,
                stat.st_size,
            )
    return signature


def _preflight_compile_sync(root: Path) -> str | None:
    """Validate a plugin source tree without importing it.

    Return an error message or None for success. Before stopping a healthy process, verify
    manifest/entry/dependencies and compile plugin-owned .py files; broken edits must not
    kill the running old instance.
    """
    manifest_path = root / "plugin.toml"
    try:
        manifest = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
        manifest_id = manifest.get("plugin", {}).get("id")
    except (
        OSError, UnicodeDecodeError, tomllib.TOMLDecodeError, AttributeError,
    ) as exc:
        return f"plugin.toml: {exc}"
    # Reuse discovery validation (entry, dependencies) and the plugin-owned
    # ``.py`` compile pass without importing the plugin in the server process.
    # Manifest IDs may differ from conflict-suffixed runtime IDs.
    from plugin.server.application.plugins.development_service import (
        _preflight_source_sync,
    )

    try:
        _preflight_source_sync(root.resolve(), manifest_id)
    except ServerDomainError as exc:
        return exc.message
    except Exception as exc:
        # Any rejection keeps the running instance; an unexpected error type
        # must not escape and leave the plugin pending for an immediate retry.
        return f"{type(exc).__name__}: {exc}"
    return None


class PluginHotReloadService:
    """Watch plugin source directories and reload running plugins on change.

    Reuse one instance across start/stop; restarting rebuilds signature baselines,
    avoiding false reloads for changes made while stopped.
    """

    def __init__(self, lifecycle_service: PluginLifecycleService | None = None) -> None:
        self._lifecycle_service = lifecycle_service or PluginLifecycleService()
        self._task: asyncio.Task[None] | None = None
        self._stop_event: asyncio.Event | None = None
        self._restart_requested = False
        # Current reload target, whose startup timeout determines restart-drain budget.
        self._inflight_target: _WatchTarget | None = None
        # plugin_id -> last observed signature; None means directory invisible this round.
        self._signatures: dict[str, dict[str, tuple[int, int]]] = {}
        # plugin_id -> monotonic debounce deadline.
        self._pending: dict[str, float] = {}

    # ---------- lifecycle ----------

    def start(self) -> bool:
        """Start the watcher task on the running loop. Idempotent.

        Return whether it actually started. Disabled PLUGIN_HOT_RELOAD is a no-op, allowing
        startup() to call unconditionally without duplicating configuration checks.
        """
        if not PLUGIN_HOT_RELOAD:
            logger.debug(
                "plugin hot-reload disabled (set NEKO_PLUGIN_HOT_RELOAD=true to enable)"
            )
            return False
        if self._task is not None and not self._task.done():
            if self._stop_event is not None and self._stop_event.is_set():
                # Defer the replacement until the old task has finished touching
                # shared signatures; never run two generations concurrently.
                self._restart_requested = True
            return True
        # On restart, old pending/signatures describe the previous process generation's disk;
        # retaining them would cause a false reload.
        self._signatures.clear()
        self._pending.clear()
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._run(), name="plugin-hot-reload-watcher")
        self._task.add_done_callback(self._watcher_done)
        logger.info(
            "plugin hot-reload watcher started (interval={}s, debounce={}s)",
            PLUGIN_HOT_RELOAD_INTERVAL,
            PLUGIN_HOT_RELOAD_DEBOUNCE,
        )
        return True

    async def stop(self, timeout: float = _STOP_TIMEOUT_SECONDS) -> None:
        """Stop the watcher. Safe to call when not running."""
        self._restart_requested = False
        event = self._stop_event
        if event is not None:
            event.set()
        task = self._task
        if task is None:
            return
        task.cancel()
        # Do not await the task directly, which propagates CancelledError. We need
        # to await exit, report timeout, and continue shutdown.
        done, _pending_tasks = await asyncio.wait({task}, timeout=timeout)
        if not done:
            # In-flight reload still running after timeout: retain the task reference or
            # start() misses the live watcher and creates another, allowing both
            # to write _signatures briefly. The orphan's stop_event is set and it exits
            # after its current step; meanwhile start() reserves a postexit replacement.
            self._task = task
            logger.warning(
                "plugin hot-reload watcher did not stop within {}s; "
                "an in-flight reload will finish on its own",
                timeout,
            )
        else:
            if self._task is task:
                self._task = None
            if not task.cancelled():
                exc = task.exception()
                if exc is not None:
                    logger.warning(
                        "plugin hot-reload watcher exited with error: {}", exc
                    )

    def _watcher_done(self, task: asyncio.Task[None]) -> None:
        if self._task is not task:
            return
        self._task = None
        if not task.cancelled():
            task.exception()  # Retrieve errors even after a timed-out stop.
        if self._restart_requested:
            self._restart_requested = False
            self.start()

    async def wait_for_stopped(self, timeout: float | None = None) -> None:
        """Drain a stopping generation before reopening the server host gate.

        Shutdown need not spend its host cleanup budget on an in-flight reload,
        but startup must not let that old transaction join the new server run.
        asyncio.wait observes task completion without propagating its cancellation.
        Cancelling startup itself still propagates and leaves the gate closed.
        A stalled transaction fails startup promptly instead of hanging it;
        reopening the gate while it still lives would admit an old-generation host.
        The default budget follows the in-flight plugin's own startup timeout
        (``[plugin_runtime].timeout`` may raise it above the global setting),
        re-derived every recheck interval.
        """
        task = self._task
        if task is None or self._stop_event is None or not self._stop_event.is_set():
            return
        if timeout is not None:
            done, _pending_tasks = await asyncio.wait({task}, timeout=timeout)
            if not done:
                self._raise_drain_timeout()
            return
        started = time_module.monotonic()
        # Sticky across the drain: start_plugin drops its record when the start
        # ends, just before the reload finishes, and the budget must not shrink
        # back under a reload that legitimately ran long.
        granted_seen: float | None = None
        while True:
            granted = self._inflight_granted_timeout()
            if granted is not None:
                granted_seen = granted if granted_seen is None else max(granted_seen, granted)
            remaining = started + self._restart_drain_budget(granted_seen) - time_module.monotonic()
            if remaining <= 0:
                self._raise_drain_timeout()
            done, _pending_tasks = await asyncio.wait(
                {task}, timeout=min(remaining, _DRAIN_RECHECK_SECONDS)
            )
            if done:
                return

    @staticmethod
    def _raise_drain_timeout() -> None:
        raise RuntimeError(
            "Previous plugin hot reload is still running; "
            "retry server startup after it finishes"
        )

    def _inflight_granted_timeout(self) -> float | None:
        target = self._inflight_target
        return None if target is None else active_startup_timeout(target.plugin_id)

    @staticmethod
    def _restart_drain_budget(granted: float | None) -> float:
        """Drain budget, from in-memory state only.

        Until start_plugin records the timeout it granted, the default budget
        applies: the steps before that record (stopping the old process, the
        bounded lock wait, reading the config) take seconds, so a reload still
        short of it after the default budget is stalled. Once recorded, the
        plugin's own timeout extends the budget. No file is read here: a drain
        must never block on, or leak threads into, a stalled filesystem.
        """
        if granted is None:
            return _RESTART_DRAIN_SECONDS
        return max(_RESTART_DRAIN_SECONDS, _RESTART_DRAIN_OVERHEAD_SECONDS + granted)

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    # ---------- watcher loop ----------

    async def _run(self) -> None:
        # Capture locally instead of assert: _run starts only through start()'s create_task,
        # after setting the event, but python -O strips assertions,
        # so defensive checks cannot depend on them.
        stop_event = self._stop_event
        if stop_event is None:
            logger.warning("plugin hot-reload watcher started without a stop event")
            return
        while not stop_event.is_set():
            try:
                await self._tick(stop_event)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # One disk glitch must not kill the watcher; partial signature updates are fine,
                # since next round compares from the new state.
                logger.warning(
                    "plugin hot-reload tick failed: err_type={}, err={}",
                    type(exc).__name__,
                    exc,
                )
            if not stop_event.is_set():
                await asyncio.sleep(self._next_sleep_seconds())

    def _next_sleep_seconds(self) -> float:
        interval = PLUGIN_HOT_RELOAD_INTERVAL
        if not self._pending:
            return interval
        nearest = min(self._pending.values())
        remaining = nearest - time_module.monotonic()
        return max(_MIN_TICK_SECONDS, min(interval, remaining))

    async def _tick(self, stop_event: asyncio.Event) -> None:
        def collect_signatures():
            return [
                (target, _signature_sync(target.root))
                for target in self._collect_targets_sync()
            ]

        scanned = await asyncio.to_thread(collect_signatures)
        targets = [target for target, _signature in scanned]

        now = time_module.monotonic()
        visible_ids: set[str] = set()
        for target, signature in scanned:
            visible_ids.add(target.plugin_id)
            previous = self._signatures.get(target.plugin_id)
            self._signatures[target.plugin_id] = signature
            if previous is None:
                # First baseline or newly visible directory: record without triggering reload.
                continue
            if signature != previous:
                self._pending[target.plugin_id] = now + PLUGIN_HOT_RELOAD_DEBOUNCE

        # Removed/unbound target: clear its state.
        for plugin_id in list(self._signatures.keys() - visible_ids):
            self._signatures.pop(plugin_id, None)
            self._pending.pop(plugin_id, None)

        due = [
            plugin_id
            for plugin_id, deadline in self._pending.items()
            if deadline <= time_module.monotonic()
        ]
        target_by_id = {target.plugin_id: target for target in targets}
        for plugin_id in due:
            if stop_event.is_set():
                break
            target = target_by_id.get(plugin_id)
            if target is None:
                self._pending.pop(plugin_id, None)
                continue
            try:
                await self._reload_target(target)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Escaping here would keep the overdue entry pending and retry it
                # at the minimum tick; drop it until the next source change.
                self._pending.pop(plugin_id, None)
                logger.warning(
                    "hot-reload attempt aborted: plugin_id={}, err_type={}, err={}",
                    plugin_id,
                    type(exc).__name__,
                    exc,
                )

    def _collect_targets_sync(self) -> list[_WatchTarget]:
        """Resolve watchable directories: dev source dirs + registered configs."""
        targets: dict[str, _WatchTarget] = {}
        try:
            for snapshot in development_store.list_registration_records_sync():
                if snapshot.source_dir.is_dir():
                    targets[snapshot.plugin_id] = _WatchTarget(
                        plugin_id=snapshot.plugin_id,
                        root=snapshot.source_dir,
                        is_development=True,
                    )
        except Exception as exc:
            logger.debug(
                "failed to list development registrations for hot-reload: err={}", exc
            )
        try:
            with state.acquire_plugins_read_lock():
                registered_plugins = [
                    (plugin_id, dict(meta) if isinstance(meta, dict) else None)
                    for plugin_id, meta in state.plugins.items()
                    if isinstance(plugin_id, str)
                ]
        except Exception as exc:
            logger.debug("failed to read plugin registry for hot-reload: err={}", exc)
            registered_plugins = []
        for plugin_id, meta in registered_plugins:
            if plugin_id in targets:
                continue
            config_path = _resolve_registered_config_path_sync(meta)
            candidate = (
                config_path.parent
                if config_path is not None and config_path.is_file()
                else None
            )
            if candidate is None:
                for base in PLUGIN_CONFIG_ROOTS:
                    fallback = Path(base) / plugin_id
                    if (fallback / "plugin.toml").is_file():
                        candidate = fallback
                        break
            if candidate is not None:
                targets[plugin_id] = _WatchTarget(plugin_id, candidate, False)
        return [
            target for target in targets.values()
            if plugin_is_running_sync(target.plugin_id)
            or plugin_needs_hot_reload_recovery(target.plugin_id)
        ]

    # ---------- reload execution ----------

    async def _reload_target(self, target: _WatchTarget) -> None:
        plugin_id = target.plugin_id
        is_running = await asyncio.to_thread(plugin_is_running_sync, plugin_id)
        if not is_running and not plugin_needs_hot_reload_recovery(plugin_id):
            # Never autostart stopped plugins. This unlocked fast path is followed by
            # reload_plugin(only_if_running=True)'s locked recheck, covering
            # a user Stop between checks. A later manual start refreshes
            # registry entries from disk itself, preserving new code.
            logger.debug(
                "hot-reload skipped (plugin not running): plugin_id={}", plugin_id
            )
            self._pending.pop(plugin_id, None)
            self._emit_event(
                "plugin_hot_reload_skipped", plugin_id, reason="not_running"
            )
            return

        if not target.is_development:
            # Development reload already has full internal preflight; add syntax checks here
            # for ordinary plugins so broken edits cannot kill healthy processes.
            error = await asyncio.to_thread(_preflight_compile_sync, target.root)
            if error is not None:
                logger.warning(
                    "hot-reload skipped (source failed preflight, keeping the "
                    "running instance): plugin_id={}, error={}",
                    plugin_id,
                    error,
                )
                self._emit_event(
                    "plugin_hot_reload_skipped", plugin_id, reason="preflight_failed"
                )
                self._pending.pop(plugin_id, None)
                return

        self._inflight_target = target
        logger.info("hot-reload triggered: plugin_id={}", plugin_id)
        self._emit_event("plugin_hot_reload_triggered", plugin_id)
        try:
            # Give lock waiting a deadline; without a budget, reload_plugin's
            # serialized_plugin_operation waits forever, never raising busy and making
            # the except below unreachable. Use one debounce window, matching deferral.
            with bounded_operation_wait(PLUGIN_HOT_RELOAD_DEBOUNCE):
                result = await self._lifecycle_service.reload_plugin(
                    plugin_id, only_if_running=True
                )
            if result.get("skipped"):
                self._pending.pop(plugin_id, None)
                self._emit_event(
                    "plugin_hot_reload_skipped", plugin_id, reason="not_running"
                )
                return
            logger.info("hot-reload completed: plugin_id={}", plugin_id)
        except PluginOperationBusy:
            # User operation holds the lock: defer one debounce window without errors.
            self._pending[plugin_id] = (
                time_module.monotonic()
                + max(PLUGIN_HOT_RELOAD_DEBOUNCE, _BUSY_RETRY_SECONDS)
            )
            logger.debug(
                "hot-reload deferred (operation busy): plugin_id={}", plugin_id
            )
        except ServerDomainError as exc:
            # reload_plugin preflight/startup failures abandon this attempt until
            # another file change; synchronized signatures prevent automatic retriggering.
            logger.warning(
                "hot-reload failed: plugin_id={}, code={}, message={}",
                plugin_id,
                exc.code,
                exc.message,
            )
            self._pending.pop(plugin_id, None)
            self._emit_event("plugin_hot_reload_failed", plugin_id, reason=exc.code)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(
                "hot-reload raised unexpectedly: plugin_id={}, err_type={}, err={}",
                plugin_id,
                type(exc).__name__,
                exc,
            )
            self._pending.pop(plugin_id, None)
            self._emit_event(
                "plugin_hot_reload_failed", plugin_id, reason=type(exc).__name__
            )
        else:
            self._pending.pop(plugin_id, None)
        finally:
            self._inflight_target = None

    @staticmethod
    def _emit_event(event_type: str, plugin_id: str, reason: str | None = None) -> None:
        payload: dict[str, object] = {
            "type": event_type,
            "plugin_id": plugin_id,
            "time": now_iso(),
        }
        if reason is not None:
            # Frontends distinguish preflight rejection/domain errors/unexpected exceptions through this,
            # without correlating log timestamps.
            payload["reason"] = reason
        try:
            emit_lifecycle_event(payload)
        except Exception as exc:
            logger.debug("failed to emit {} event: {}", event_type, exc)


# Module singleton, matching lifecycle.py's other services.
hot_reload_service = PluginHotReloadService()
