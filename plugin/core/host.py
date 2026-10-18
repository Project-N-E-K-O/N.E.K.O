from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timezone
import importlib
import importlib.machinery
import importlib.util
import inspect
import json
import math
import multiprocessing
import os
import socket
import sys
import threading
import time
import hashlib
import types
import uuid
import weakref
from pathlib import Path
from typing import Any, Dict, Iterator, Optional, Type, TYPE_CHECKING

from config import (
    MAIN_SERVER_PORT,
    MEMORY_SERVER_PORT,
    TOOL_SERVER_PORT,
    USER_PLUGIN_SERVER_PORT,
)
from plugin.logging_config import logger

from plugin._types.events import EVENT_META_ATTR
from plugin.core.entry_points import normalize_plugin_entry_point
from plugin.core.model_gateway_access import model_gateway_access
from plugin.sdk import PERSIST_ATTR
from plugin.core.state import state
from plugin.core.context import PluginContext
from plugin.core.communication import PluginCommunicationResourceManager, STARTUP_RESULT_REQ_ID

if TYPE_CHECKING:
    # Keep server API models out of the child startup import path. The SDK
    # still uses pydantic; health_check constructs this API response on demand.
    from plugin._types.models import HealthCheckResponse

from plugin._types.exceptions import (
    PluginLifecycleError,
    PluginEntryNotFoundError,
    PluginError,
)
from plugin.settings import (
    PLUGIN_TRIGGER_TIMEOUT,
    PLUGIN_SHUTDOWN_TIMEOUT,
    QUEUE_GET_TIMEOUT,
    PROCESS_SHUTDOWN_TIMEOUT,
    PROCESS_TERMINATE_TIMEOUT,
)
from plugin.sdk.shared.core.entry_runtime import prepare_entry_kwargs, resolve_entry_timeout
from plugin.sdk.shared.core.finish import normalize_structured_data
from plugin.sdk.shared.core.result_contract import model_schema_from_type
from plugin.sdk.shared.core.router import PluginRouter
from plugin.sdk.plugin.ui import UI_ACTION_META_ATTR, UI_CONTEXT_META_ATTR
from plugin.core.bus.types import dispatch_bus_change
from plugin.core.zmq_transport import (
    HostTransport, ChildTransport, CH_CMD, CH_RES, CH_STS, CH_MSG, CH_COMM, CH_RESP,
)


def _sanitize_plugin_id(raw: Any, max_len: int = 64) -> str:
    s = str(raw)
    safe = "".join(c if (c.isalnum() or c in ("-", "_")) else "_" for c in s)
    safe = safe.strip("_-")
    if not safe:
        safe = hashlib.sha256(s.encode("utf-8", errors="ignore")).hexdigest()[:16]
    if len(safe) > max_len:
        digest = hashlib.sha256(s.encode("utf-8", errors="ignore")).hexdigest()[:12]
        safe = f"{safe[:max_len - 13]}_{digest}"
    return safe


def _resolve_current_storage_layout() -> dict[str, Any]:
    from utils.config_manager import get_config_manager
    from utils.storage_layout import resolve_storage_layout

    return resolve_storage_layout(get_config_manager())


def _refresh_child_storage_layout_env(logger_obj: Any) -> None:
    try:
        from utils.storage_layout import export_storage_layout_to_env

        layout = _resolve_current_storage_layout()
        export_storage_layout_to_env(layout)
        logger_obj.debug(
            "Plugin child storage layout env refreshed: selected_root={}, anchor_root={}, source={}",
            layout.get("selected_root"),
            layout.get("anchor_root"),
            layout.get("source"),
        )
    except Exception as exc:
        logger_obj.warning(
            "Failed to refresh plugin child storage layout env; plugin data root may use fallback: err_type={}, err={}",
            type(exc).__name__,
            str(exc),
        )


_TIMEOUT_UNSET = object()


# ============================================================================
# Drop inherited host HTTP sockets after fork.
# ============================================================================

# Plugins use bare multiprocessing.Process, hence fork on POSIX; children inherit the entire
# host descriptor table, including HTTP listeners and every accepted browser connection.
# Children never read them, and TCP disconnects only when the last reference closes;
# closing host copies therefore sends no FIN. Browsers/internal httpx-pool callers retain
# idle keepalives, reuse them, and queue requests in kernel buffers with no reader,
# hanging forever. Plugin-manager pages spin and stop responding, recover after refresh,
# then hang again later.
#
# Identify sockets by local ports served by this process: listeners and accepted connections share
# those ports, while client sockets use ephemeral local ports, naturally restricting matches
# to host HTTP services without harming other sockets.
#
# Drop all four ports because topology determines the parent: source mode forks from
# agent_server, hosting agent/plugin services only; packaged defaults use merged
# topology (launcher_core.runtime._should_use_merged_mode), sharing main/memory/agent/
# plugin in one process. Children also inherit main 48911 and memory 48912 listeners/accepted
# connections; dropping only the other two leaves the main UI blackholed too.
# NEKO_* environment and config globals synchronize before imports in each process via launcher's
# _reload_runtime_config_from_env / _sync_runtime_config_globals, so these are actual
# bound ports. Add future merged HTTP services at this single change point.
#
# Do not enumerate all process listeners: libzmq TCP listeners are also ordinary AF_INET sockets
# in the descriptor table, and ZMQ must remain untouched below.
#
# Never touch ZMQ: zmq.Context.instance() retains descriptor numbers in libzmq accounting after fork.
# Closing/reusing those numbers makes zmq_ctx_term close unrelated fds. Leave AF_UNIX
# untouched too: state.plugin_response_map Manager proxies are intentionally
# inherited by children.
#
# Inherited client-direction sockets are outside this change: children may inherit host clients targeting
# these ports, but ephemeral local ports exclude them. They do not hang peers, merely delay closure
# by one keepalive cycle; leave unchanged.
_HOST_HTTP_PORTS = frozenset({
    MAIN_SERVER_PORT,
    MEMORY_SERVER_PORT,
    TOOL_SERVER_PORT,
    USER_PLUGIN_SERVER_PORT,
})
_INET_FAMILIES = frozenset({socket.AF_INET, socket.AF_INET6})

_INHERITED_HOST_SOCKETS_CLOSED = 0


def _iter_open_fds() -> Iterator[int]:
    """Current process file descriptors, excluding standard I/O.

    Fork inheritance applies only where per-process fd directories are enumerable: Linux /proc/self/fd,
    macOS/BSD fdescfs /dev/fd, both process-local numeric listings. Without either,
    enumerate nothing, typically Windows spawn where children re-execute without host descriptors.
    macOS multiprocessing also defaults to spawn, but merged packaged mode explicitly
    forks through app/main_server/__init__.py set_start_method,
    making /dev/fd required coverage rather than optional support.
    """
    for directory in ("/proc/self/fd", "/dev/fd"):
        try:
            entries = os.listdir(directory)
        except OSError:
            continue
        for entry in entries:
            try:
                fd = int(entry)
            except ValueError:
                continue
            if fd > 2:
                yield fd
        return


def _is_host_http_socket(fd: int) -> bool:
    """Determine whether fd is a host HTTP listener or one of its accepted connections.

    Caller owns fd; borrow a wrapper and detach so destruction cannot close it again.
    Read wrapper.family: CPython derives it from getsockname() sa_family for fileno-only
    construction (Modules/socketmodule.c sock_initobj), independent of SO_DOMAIN,
    absent from both Windows and Darwin headers.
    """
    try:
        probe = socket.socket(fileno=fd)
    except OSError:
        return False  # Pipes/files/epoll/signalfd are not sockets.
    try:
        # AF_UNIX getsockname() returns path strings or empty for unnamed sockets; check family before
        # extracting ports, with short-circuit and preventing string indexing there.
        return probe.family in _INET_FAMILIES and probe.getsockname()[1] in _HOST_HTTP_PORTS
    except OSError:
        return False
    finally:
        probe.detach()


def _close_inherited_host_sockets() -> None:
    """Drop host HTTP sockets via registered after_in_child hooks.

    Run immediately after fork, before child sockets/plugin code. Never raise:
    CPython reports hook failures as unraisable stderr errors but continues children
    with partially closed descriptor tables.

    Separate os.close from identification so tests can exercise identification on all platforms;
    closing bare socket fds is POSIX-specific, while hooks themselves run only
    on fork platforms anyway.
    """
    global _INHERITED_HOST_SOCKETS_CLOSED
    closed = 0
    for fd in _iter_open_fds():
        if not _is_host_http_socket(fd):
            continue
        try:
            os.close(fd)
            closed += 1
        except OSError:
            pass
    _INHERITED_HOST_SOCKETS_CLOSED = closed


def inherited_host_sockets_closed() -> int:
    """Number of host sockets dropped by the last fork, observable in child startup logs."""
    return _INHERITED_HOST_SOCKETS_CLOSED


_FORK_SOCKET_HOOK_REGISTERED = False

if hasattr(os, "register_at_fork"):
    # POSIX only; spawn platforms re-execute children and inherit no such descriptors.
    os.register_at_fork(after_in_child=_close_inherited_host_sockets)
    _FORK_SOCKET_HOOK_REGISTERED = True


# ============================================================================
# _plugin_process_runner helpers.
# ============================================================================

def _setup_plugin_logger(plugin_id: str, project_root: Path) -> Any:
    """Configure child-plugin logging via host RobustLoggerConfig under Documents/N.E.K.O/logs/.

    NOTE: never reintroduce loguru, infer paths from cwd, or create another FileHandler.
    See plugin/logging_config.py's opening warning.

    Args:
        plugin_id: Plugin ID.
        project_root: Project root for sys.path injection.

    Returns:
        PluginLoggerAdapter bound to plugin_id.
    """
    safe_pid = _sanitize_plugin_id(plugin_id)
    # Let plugin/logging_config.get_logger() derive the correct service namespace.
    os.environ["NEKO_PLUGIN_SERVICE_NAME"] = f"Plugin_{safe_pid}"

    # Children first add project roots to sys.path, or utils.logger_config imports fail below.
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    try:
        from utils.logger_config import setup_logging as _bootstrap_setup_logging
        _bootstrap_setup_logging(
            service_name=f"Plugin_{safe_pid}",
            silent=True,
            # Collect child-plugin logs under logs/plugin/, separate from PluginServer/
            # Main/Memory/Agent host logs at logs/ top level.
            log_subdir="plugin",
        )
    except Exception:
        # RobustLoggerConfig provides multilevel fallback; this is last resort, never hide root causes.
        pass

    from plugin.logging_config import get_logger
    return get_logger(f"plugin.{safe_pid}").bind(plugin_id=plugin_id)


def _setup_logging_interception(logger: Any, project_root: Path) -> None:
    """Compatibility noop: host logging already uses stdlib, requiring no interception.

    Preserve signatures for old callers; ensure project_root is on sys.path only.
    """
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))


# Parent UI-context default wait budget, matching CommManager.get_ui_context.
_UI_CONTEXT_DEFAULT_BUDGET = 5.0
# Reserve stop -> serialize -> IPC-return time so degraded actions+context_error
# arrive before parent timeout; otherwise the fallback cannot be reached.
_UI_CONTEXT_REPLY_HEADROOM = 0.75
# For tiny budgets use proportional headroom, never a fixed floor exceeding caller allowances.
_UI_CONTEXT_MIN_REPLY_SHARE = 0.5
# Budgets shorter than one IPC roundtrip are invalid like NaN/Inf and use defaults, also
# avoiding floating underflow because meaningful fractions require values far above subnormal range.
_UI_CONTEXT_MIN_SENSIBLE_BUDGET = 0.001


def _ui_context_provider_budget(requested: object) -> float:
    """Provider budget that always leaves the caller room to receive the reply.

    Invariant, for every input: the result is finite, positive, and strictly
    less than the budget it resolved to. Subtracting a fixed headroom alone
    does not get there — short budgets would be floored above the caller's,
    and budgets large enough to round the subtraction away come back
    unchanged — so both cases fall back to yielding a share of the budget.
    Budgets too small to survive that share (or to fit an IPC round trip at
    all) are nonsense input and resolve to the default instead.
    """
    try:
        budget = float(requested)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        # OverflowError is neither: an int past the float range raises it.
        budget = _UI_CONTEXT_DEFAULT_BUDGET
    # nan slips past every comparison and inf survives the subtraction, so
    # neither may reach asyncio.wait_for as a deadline.
    if not math.isfinite(budget) or budget < _UI_CONTEXT_MIN_SENSIBLE_BUDGET:
        budget = _UI_CONTEXT_DEFAULT_BUDGET
    resolved = max(budget - _UI_CONTEXT_REPLY_HEADROOM, budget * _UI_CONTEXT_MIN_REPLY_SHARE)
    if not resolved < budget:
        resolved = budget * _UI_CONTEXT_MIN_REPLY_SHARE
    return resolved


def _discard_abandoned_task_result(task: asyncio.Future) -> None:
    """Retrieve an abandoned task's outcome so asyncio stops complaining about it."""
    if task.cancelled():
        return
    try:
        task.exception()
    except asyncio.CancelledError:
        pass


async def _await_within_budget(awaitable: Any, budget: float) -> tuple[bool, Any]:
    """Wait at most *budget* seconds. Returns ``(completed, result)``.

    Deliberately not ``asyncio.wait_for``: that cancels the inner task and then
    *awaits* it, so a coroutine which swallows ``CancelledError`` (or blocks in
    a ``finally``) delays — or never triggers — the timeout. The whole point of
    this budget is to answer before the caller's own deadline, so abandon the
    task rather than wait on it.
    """
    task = asyncio.ensure_future(awaitable)
    done, _pending = await asyncio.wait({task}, timeout=budget)
    if task in done:
        try:
            return True, task.result()
        except asyncio.CancelledError as exc:
            # This task exists only locally and cannot be cancelled externally, so reaching here means
            # the awaitable itself leaked CancelledError. As BaseException it bypasses
            # caller except Exception; convert to an ordinary exception so success=False returns.
            # Wrap result() only: genuine cooperative shutdown cancellation comes from await above,
            # and broad wrapping would swallow that signal too.
            raise RuntimeError("awaited task was cancelled") from exc
    task.cancel()
    task.add_done_callback(_discard_abandoned_task_result)
    return False, None


def _find_project_root(config_path: Path) -> Path:
    """
    Discover project roots upward from configuration-file paths.
    
    Args:
        config_path: Plugin configuration-file path.
    
    Returns:
        Project-root path.
    """
    cur = config_path.resolve()
    try:
        if cur.is_file():
            cur = cur.parent
    except Exception:
        pass
    
    for _ in range(10):
        try:
            candidate = cur
            # Repo root should contain both plugin/ and utils/
            if (candidate / "plugin").is_dir() and (candidate / "utils").is_dir():
                return candidate
        except Exception:
            pass
        if cur.parent == cur:
            break
        cur = cur.parent
    
    try:
        logger.debug(
            "[Plugin Process] Could not find project root via exploration from %s; using host-relative root",
            config_path,
        )
    except Exception:
        pass

    # config_path is outside the repository. User plugins live in
    # Documents/{APP_NAME}/plugins/<id>/plugin.toml; counting four parents as in old
    # plugin/plugins/<id>/ layouts yields the user's Documents folder, then inserts it
    # into child sys.path[0], letting arbitrary Documents types.py/utils/
    # precede stdlib/host modules. Trust the host's own location instead:
    # plugin/core/host.py -> plugin/core -> plugin -> repository root.
    # Validate plugin/ only: packaging compiles utils with --include-package=utils,
    # leaving no utils/ directory in dist. Requiring both would force every packaged
    # plugin process into fallback. plugin/ physically exists in both layouts,
    # including dist plugin/plugins/.
    try:
        host_relative_root = Path(__file__).resolve().parents[2]
        if (host_relative_root / "plugin").is_dir():
            return host_relative_root
    except Exception:
        pass

    # Final fallback: plugin directory itself. Prefer one fewer import root over putting
    # unrelated directories first on sys.path.
    try:
        return config_path.parent.resolve()
    except Exception:
        return config_path.parent


def _prepare_child_plugin_import_roots(logger: Any) -> None:
    """Expose Core itself without exposing shared plugin candidate roots."""

    try:
        from plugin.settings import BUILTIN_PLUGIN_CONFIG_ROOT, PLUGIN_CONFIG_ROOTS
    except Exception as exc:
        logger.debug("[Plugin Process] Failed to load plugin config roots: {}", exc)
        return

    try:
        builtin_root = BUILTIN_PLUGIN_CONFIG_ROOT.resolve()
    except Exception:
        builtin_root = BUILTIN_PLUGIN_CONFIG_ROOT

    # A child process loads only the plugin selected by its config path. Remove
    # any inherited candidate roots so sibling user/builtin plugins cannot
    # shadow standard-library or third-party top-level modules.
    for plugin_config_root in PLUGIN_CONFIG_ROOTS:
        try:
            import_root = plugin_config_root.resolve().parent
        except Exception:
            import_root = plugin_config_root.parent
        value = str(import_root)
        while value in sys.path:
            sys.path.remove(value)

    repo_root = builtin_root.parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))


def _prepare_child_current_plugin_import_root(config_path: Path, logger: Any) -> None:
    """Prepare an isolated namespace for the selected plugin."""

    try:
        plugin_root = config_path.resolve().parent.parent
    except Exception as exc:
        logger.debug("[Plugin Process] Failed to resolve current plugin import root: {}", exc)
        return

    _ensure_plugins_namespace(plugin_root, logger)


def _prepare_child_plugin_vendor_path(config_path: Path, logger: Any) -> None:
    """Add the current plugin's vendor/ directory before importing its entry."""

    vendor_dir = config_path.parent / "vendor"
    if not vendor_dir.is_dir():
        return

    value = str(vendor_dir)
    if value in sys.path:
        return
    sys.path.insert(0, value)
    logger.info("[Plugin Process] Added plugin vendor path to sys.path: {}", vendor_dir)


def _is_target_module_missing(exc: ModuleNotFoundError, module_path: str) -> bool:
    """Determine whether the missing module is the target plugin itself rather than its internal dependency."""

    missing_name = getattr(exc, "name", None)
    return bool(missing_name and (missing_name == module_path or module_path.startswith(f"{missing_name}.")))


def _ensure_plugins_namespace(plugin_root: Path, logger: Any) -> None:
    """Create a namespace without exposing sibling installations."""

    existing = sys.modules.get("plugins")
    if existing is None or getattr(existing, "__path__", None) is None:
        module = types.ModuleType("plugins")
        module.__package__ = "plugins"
        module.__path__ = []
        module.__spec__ = importlib.machinery.ModuleSpec("plugins", loader=None, is_package=True)
        if module.__spec__ is not None:
            module.__spec__.submodule_search_locations = module.__path__
        sys.modules["plugins"] = module
        logger.info("[Plugin Process] Created plugins namespace for: {}", plugin_root)
        return

    existing.__path__ = []
    spec = getattr(existing, "__spec__", None)
    if spec is None:
        spec = importlib.machinery.ModuleSpec("plugins", loader=None, is_package=True)
        existing.__spec__ = spec
    spec.submodule_search_locations = existing.__path__
    logger.debug("[Plugin Process] Cleared inherited plugins namespace search paths")

def _evict_plugin_module_tree(plugin_module_path: str) -> None:
    """Remove one synthetic plugin package without disturbing its siblings."""

    loaded_module = sys.modules.get(plugin_module_path)
    module_prefix = f"{plugin_module_path}."
    for loaded_name in tuple(sys.modules):
        if loaded_name == plugin_module_path or loaded_name.startswith(module_prefix):
            sys.modules.pop(loaded_name, None)

    parent_name, _, child_name = plugin_module_path.rpartition(".")
    parent_module = sys.modules.get(parent_name)
    if parent_module is None:
        return
    bound_child = getattr(parent_module, child_name, None)
    if bound_child is not None and (
        bound_child is loaded_module
        or getattr(bound_child, "__name__", None) == plugin_module_path
    ):
        delattr(parent_module, child_name)


def evict_cached_plugin_modules(plugin_id: str) -> None:
    """Invalidate both import aliases for one plugin after its files change."""

    if not plugin_id or "." in plugin_id:
        return
    _evict_plugin_module_tree(f"plugins.{plugin_id}")
    _evict_plugin_module_tree(f"plugin.plugins.{plugin_id}")
    importlib.invalidate_caches()


def _module_is_loaded_from_plugin_dir(module: Any, plugin_dir: Path) -> bool:
    """Return whether an imported package already represents the selected source."""

    loaded_file = getattr(module, "__file__", None)
    if isinstance(loaded_file, str) and loaded_file:
        try:
            Path(loaded_file).resolve().relative_to(plugin_dir)
            return True
        except (OSError, ValueError):
            pass
    for loaded_path in getattr(module, "__path__", ()):
        try:
            if Path(loaded_path).resolve() == plugin_dir:
                return True
        except (OSError, TypeError, ValueError):
            continue
    return False


def _evict_cached_plugin_source(module_path: str, config_path: Path, logger: Any) -> None:
    """Remove an inherited same-ID package before importing the effective source."""

    parts = module_path.split(".")
    if len(parts) >= 2 and parts[0] == "plugins":
        plugin_id = parts[1]
    elif len(parts) >= 3 and parts[:2] == ["plugin", "plugins"]:
        plugin_id = parts[2]
    else:
        return
    try:
        plugin_dir = config_path.resolve().parent
    except OSError as exc:
        logger.debug("[Plugin Process] Failed to resolve plugin directory for cache eviction: {}", exc)
        return
    if plugin_id != plugin_dir.name:
        return

    package_prefixes = (f"plugins.{plugin_id}", f"plugin.plugins.{plugin_id}")
    loaded_tree = [
        (name, loaded)
        for name, loaded in tuple(sys.modules.items())
        if any(name == prefix or name.startswith(f"{prefix}.") for prefix in package_prefixes)
    ]
    if loaded_tree and all(
        _module_is_loaded_from_plugin_dir(loaded, plugin_dir) for _name, loaded in loaded_tree
    ):
        return

    evicted = [name for name, _loaded in loaded_tree]
    for name in evicted:
        sys.modules.pop(name, None)
    if evicted:
        logger.info(
            "[Plugin Process] Evicted cached plugin package before source import: {}",
            ", ".join(package_prefixes),
        )


def _import_current_plugin_from_config(module_path: str, config_path: Path, logger: Any) -> Any | None:
    """On namespace-import failure, load plugin packages beside current plugin.toml directly."""

    parts = module_path.split(".")
    if len(parts) < 2 or parts[0] != "plugins":
        return None

    try:
        plugin_dir = config_path.resolve().parent
    except Exception as exc:
        logger.debug("[Plugin Process] Failed to resolve plugin directory for import fallback: {}", exc)
        return None

    if parts[1] != plugin_dir.name:
        logger.debug(
            "[Plugin Process] Import fallback skipped because module '{}' does not match plugin dir '{}'",
            module_path,
            plugin_dir.name,
        )
        return None

    source_file = plugin_dir / "__init__.py"

    plugin_root = plugin_dir.parent
    _ensure_plugins_namespace(plugin_root, logger)
    importlib.invalidate_caches()

    plugin_module_path = ".".join(parts[:2])
    existing = sys.modules.get(plugin_module_path)
    existing_file = getattr(existing, "__file__", None)
    existing_matches = False
    if isinstance(existing_file, str) and existing_file:
        try:
            Path(existing_file).resolve().relative_to(plugin_dir)
            existing_matches = True
        except (OSError, ValueError):
            pass
    if existing is not None and not existing_matches:
        for existing_path in getattr(existing, "__path__", ()):
            try:
                if Path(existing_path).resolve() == plugin_dir:
                    existing_matches = True
                    break
            except (OSError, TypeError, ValueError):
                continue
    if existing is not None and not existing_matches:
        _evict_plugin_module_tree(plugin_module_path)

    if plugin_module_path not in sys.modules:
        if source_file.is_file():
            spec = importlib.util.spec_from_file_location(
                plugin_module_path,
                source_file,
                submodule_search_locations=[str(plugin_dir)],
            )
        else:
            spec = importlib.machinery.ModuleSpec(
                plugin_module_path,
                loader=None,
                is_package=True,
            )
            spec.submodule_search_locations = [str(plugin_dir)]
        if spec is None:
            logger.debug("[Plugin Process] Import could not create spec for: {}", plugin_dir)
            return None

        module = importlib.util.module_from_spec(spec)
        sys.modules[plugin_module_path] = module
        setattr(sys.modules["plugins"], parts[1], module)
        legacy_parent = importlib.import_module("plugin.plugins")
        legacy_module_path = f"plugin.{plugin_module_path}"
        _evict_plugin_module_tree(legacy_module_path)
        sys.modules[legacy_module_path] = module
        setattr(legacy_parent, parts[1], module)
        try:
            if spec.loader is not None:
                spec.loader.exec_module(module)
        except Exception:
            _evict_plugin_module_tree(plugin_module_path)
            _evict_plugin_module_tree(legacy_module_path)
            raise

    if len(parts) > 2:
        try:
            return importlib.import_module(module_path)
        except ModuleNotFoundError as exc:
            if _is_target_module_missing(exc, module_path):
                return None
            raise
    return sys.modules[plugin_module_path]


def _import_plugin_module(module_path: str, config_path: Path | None, logger: Any, *, source_only: bool = False) -> Any:
    """Import plugins with current-directory fallback if user-plugin namespaces fail.

    Without config_path, e.g. runtime extensions lacking plugin.toml paths, skip fallback
    and use ordinary import_module semantics.
    """

    if source_only:
        from plugin.core.source_imports import install_source_imports

        if config_path is None:
            raise ValueError("Source-only plugin imports require a manifest path")
        canonical = module_path.removeprefix("plugin.")
        parts = canonical.split(".")
        directory = config_path.resolve().parent
        if len(parts) < 2 or parts[0] != "plugins" or parts[1] != directory.name:
            raise ValueError("Source-only plugin module must match its source directory")
        package = ".".join(parts[:2])
        _evict_plugin_module_tree(package)
        _evict_plugin_module_tree(f"plugin.{package}")
        _ensure_plugins_namespace(directory.parent, logger)
        install_source_imports(package, directory)
        legacy_parent = importlib.import_module("plugin.plugins")
        spec = importlib.util.find_spec(package)
        if spec is None:
            raise ModuleNotFoundError(f"No source package named '{package}'", name=package)
        root_module = importlib.util.module_from_spec(spec)
        sys.modules[package] = root_module
        sys.modules[f"plugin.{package}"] = root_module
        setattr(sys.modules["plugins"], parts[1], root_module)
        setattr(legacy_parent, parts[1], root_module)
        try:
            if spec.loader is not None:
                spec.loader.exec_module(root_module)
            return importlib.import_module(canonical)
        except BaseException:
            _evict_plugin_module_tree(package)
            _evict_plugin_module_tree(f"plugin.{package}")
            raise

    if config_path is not None and (
        module_path.startswith("plugins.") or module_path.startswith("plugin.plugins.")
    ):
        _evict_cached_plugin_source(module_path, config_path, logger)
    if config_path is not None and module_path.startswith("plugins."):
        configured_module = _import_current_plugin_from_config(
            module_path,
            config_path,
            logger,
        )
        if configured_module is not None:
            return configured_module

    try:
        return importlib.import_module(module_path)
    except ModuleNotFoundError as exc:
        if config_path is None or not _is_target_module_missing(exc, module_path):
            raise
        fallback_module = _import_current_plugin_from_config(module_path, config_path, logger)
        if fallback_module is None:
            raise
        return fallback_module


async def _handle_config_update_command(
    msg: dict,
    ctx: Any,
    events_by_type: dict,
    plugin_id: str,
    res_sender: Any,
    logger: Any,
) -> None:
    """Handle CONFIG_UPDATE for hot configuration changes.

    ``res_sender`` must expose ``.put(obj, timeout=...)`` — either a
    :class:`ChannelSender` or legacy ``mp.Queue``.
    """
    req_id = msg.get("req_id", "unknown")
    new_config = msg.get("config", {})
    mode = msg.get("mode", "temporary")  # temporary | permanent
    profile_name = msg.get("profile")
    
    ret_payload = {"req_id": req_id, "success": False, "data": None, "error": None}
    
    try:
        logger.info(
            "[Plugin Process] Received CONFIG_UPDATE: plugin_id={}, mode={}, req_id={}",
            plugin_id, mode, req_id,
        )
        
        # Deep-copy old configuration for callbacks, avoiding shared nested references.
        old_config = {}
        if hasattr(ctx, '_effective_config'):
            old_config = copy.deepcopy(ctx._effective_config) if ctx._effective_config else {}
        
        # Update process-local configuration cache.
        if hasattr(ctx, '_effective_config') and ctx._effective_config is not None:
            # Deep-merge configuration.
            _deep_merge(ctx._effective_config, new_config)
            logger.debug("[Plugin Process] Config cache updated")
        else:
            ctx._effective_config = new_config

        refresh_runtime_config = getattr(ctx, "_refresh_instance_runtime_config", None)
        if callable(refresh_runtime_config) and isinstance(ctx._effective_config, dict):
            refresh_runtime_config(ctx._effective_config)
        
        # Trigger config_change lifecycle callbacks when present.
        lifecycle_events = events_by_type.get("lifecycle", {})
        config_change_handler = lifecycle_events.get("config_change")
        
        if config_change_handler:
            logger.debug("[Plugin Process] Triggering config_change lifecycle event")
            try:
                with ctx._handler_scope("lifecycle.config_change"):
                    result = config_change_handler(
                        old_config=old_config,
                        new_config=ctx._effective_config,
                        mode=mode,
                    )
                    if inspect.isawaitable(result):
                        await result
                logger.info("[Plugin Process] config_change handler executed successfully")
            except Exception as e:
                logger.exception("[Plugin Process] config_change handler failed")
                # Restore prechange configuration.
                ctx._effective_config = old_config
                if callable(refresh_runtime_config) and isinstance(old_config, dict):
                    refresh_runtime_config(old_config)
                logger.debug("[Plugin Process] Config rolled back after handler failure")
                ret_payload["error"] = f"config_change handler failed: {e}"
                res_sender.put(ret_payload, timeout=10.0)
                return
        
        if mode == "permanent":
            logger.warning(
                "[Plugin Process] CONFIG_UPDATE permanent mode requested but persistence is not implemented: plugin_id={}, profile={} req_id={}",
                plugin_id, profile_name, req_id,
            )
            ret_payload["success"] = False
            ret_payload["error"] = "permanent mode persistence is not implemented"
            ret_payload["data"] = {
                "mode": mode,
                "config_applied": True,
                "handler_called": config_change_handler is not None,
                "permanent_not_implemented": True,
            }
            try:
                res_sender.put(ret_payload, timeout=10.0)
            except Exception:
                logger.exception("[Plugin Process] Failed to send CONFIG_UPDATE response")
            return

        ret_payload["success"] = True
        ret_payload["data"] = {
            "mode": mode,
            "config_applied": True,
            "handler_called": config_change_handler is not None,
        }
        
        logger.info("[Plugin Process] CONFIG_UPDATE completed successfully, mode={}", mode)
        
    except Exception as e:
        logger.exception("[Plugin Process] CONFIG_UPDATE failed")
        ret_payload["error"] = str(e)
    
    try:
        res_sender.put(ret_payload, timeout=10.0)
    except Exception:
        logger.exception("[Plugin Process] Failed to send CONFIG_UPDATE response")


def _deep_merge(base: dict, updates: dict) -> None:
    """
    Deep-merge updates into base.
    
    Args:
        base: Base dictionary, mutated in place.
        updates: Updates dictionary.
    """
    for key, value in updates.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


async def _run_with_model_client_cleanup(ctx: PluginContext, awaitable: Any) -> Any:
    """Release this loop's HTTP client before an asyncio.run loop closes."""
    try:
        return await awaitable
    finally:
        models = getattr(ctx, "_models", None)
        if models is not None:
            try:
                await models.aclose()
            except Exception as exc:
                # Cleanup must not replace a plugin result or hide its error.
                logger.warning("[Plugin Process] Model client cleanup failed: {}", type(exc).__name__)


def _plugin_process_runner(
    plugin_id: str,
    entry_point: str,
    config_path: Path,
    downlink_endpoint: str,
    uplink_endpoint: str,
    uplink_token: str = "",
    stop_event: Any | None = None,
    startup_options: dict[str, object] | None = None,
    message_uplink_endpoint: str = "",
    image_uplink_endpoint: str | None = None,
    *,
    model_gateway_options: dict[str, str] | None = None,
) -> None:
    """Separate-process runner communicating with hosts through ZMQ."""
    uplink_token = str(uplink_token or "").strip()
    if not uplink_token:
        raise ValueError("Plugin child process requires an uplink token")
    state.mark_plugin_child_process()

    # Store the process-level stop event.
    process_stop_event = stop_event
    startup_failure_policy = str((startup_options or {}).get("startup_failure") or "warn").strip().lower()
    
    # Initialize project-root discovery and logging.
    project_root = _find_project_root(config_path)
    logger = _setup_plugin_logger(plugin_id, project_root)
    
    # Configure logging interception.
    try:
        _setup_logging_interception(logger, project_root)
    except Exception as e:
        logger.warning("[Plugin Process] Failed to setup logging interception: {}", e)

    # Fork inherited the host descriptor table; hooks already dropped HTTP sockets
    # (see _close_inherited_host_sockets above). Leave one observable trace here.
    released_sockets = inherited_host_sockets_closed()
    if released_sockets:
        logger.info(
            "[Plugin Process] released {} host socket(s) inherited over fork",
            released_sockets,
        )
    
    # ── ZMQ child-side transport ─────────────────────────────────
    child_transport_kwargs: dict[str, object] = {}
    if message_uplink_endpoint:
        child_transport_kwargs["message_uplink_endpoint"] = (
            message_uplink_endpoint
        )
    if image_uplink_endpoint:
        child_transport_kwargs["image_uplink_endpoint"] = image_uplink_endpoint
    child_transport = ChildTransport(
        downlink_endpoint,
        uplink_endpoint,
        uplink_token,
        **child_transport_kwargs,
    )
    res_sender = child_transport.channel_sender(CH_RES)
    status_sender = child_transport.channel_sender(CH_STS)
    message_sender = child_transport.channel_sender(CH_MSG)
    comm_sender = child_transport.channel_sender(CH_COMM)

    try:
        _prepare_child_plugin_import_roots(logger)
        _prepare_child_current_plugin_import_root(config_path, logger)
        _prepare_child_plugin_vendor_path(config_path, logger)
        try:
            from plugin.settings import BUILTIN_PLUGIN_CONFIG_ROOT
            entry_point = normalize_plugin_entry_point(
                entry_point,
                config_path=config_path,
                builtin_plugin_root=BUILTIN_PLUGIN_CONFIG_ROOT,
            )
        except Exception as e:
            logger.debug("[Plugin Process] Failed to normalize entry point: {}", e)

        if str(project_root) not in sys.path:
            sys.path.insert(0, str(project_root))
            logger.info("[Plugin Process] Added project root to sys.path: {}", project_root)
        
        logger.info("[Plugin Process] Starting plugin '{}' from {}", plugin_id, entry_point)
        
        module_path, class_name = entry_point.split(":", 1)
        logger.debug("[Plugin Process] Importing module: {}", module_path)
        mod = _import_plugin_module(
            module_path, config_path, logger,
            source_only=(startup_options or {}).get("source_only") is True,
        )
        cls = getattr(mod, class_name)
        logger.debug("[Plugin Process] Class loaded: {}", cls.__name__)

        ctx = PluginContext(
            plugin_id=plugin_id,
            logger=logger,
            config_path=config_path,
            status_queue=status_sender,
            message_queue=message_sender,
            _plugin_comm_queue=comm_sender,
            _zmq_ipc_client=None,
            _cmd_queue=None,
            _res_queue=None,
            _response_queue=None,
            _response_pending={},
            _image_transport=child_transport,
            _entry_map=None,
            _instance=None,
            _model_gateway_base_url=(model_gateway_options or {}).get("base_url", ""),
            _model_gateway_token=(model_gateway_options or {}).get("token", ""),
        )
        # The context owns this instance's credential from now on.
        if model_gateway_options is not None:
            model_gateway_options.clear()
        # Cleared until the downlink loop starts reading. Uploads launched by
        # timer / custom-event handlers before that point would have no reader
        # for their reply, so they wait here instead of timing out (Codex).
        ctx._downlink_ready = threading.Event()

        try:
            from plugin.settings import PLUGIN_ZMQ_IPC_ENABLED, PLUGIN_ZMQ_IPC_ENDPOINT

            enabled = os.getenv("NEKO_PLUGIN_ZMQ_IPC_ENABLED")
            if enabled is None:
                use_zmq = bool(PLUGIN_ZMQ_IPC_ENABLED)
            else:
                use_zmq = str(enabled).lower() in ("true", "1", "yes", "on")

            if use_zmq:
                from plugin.utils.zeromq_ipc import ZmqIpcClient
                endpoint = os.getenv("NEKO_PLUGIN_ZMQ_IPC_ENDPOINT", PLUGIN_ZMQ_IPC_ENDPOINT)
                ctx._zmq_ipc_client = ZmqIpcClient(plugin_id=plugin_id, endpoint=endpoint)
                try:
                    logger.info("[Plugin Process] ZeroMQ IPC enabled: {}", endpoint)
                except Exception:
                    pass
        except Exception:
            try:
                logger.warning("[Plugin Process] ZeroMQ IPC enabled but client init failed")
            except Exception:
                pass
            pass

        # Routers are composition units within ordinary plugins, never standalone plugin entry points.
        if isinstance(cls, type) and issubclass(cls, PluginRouter):
            logger.error(
                "[Plugin Process] Entry class '{}' is a PluginRouter subclass, not a NekoPluginBase. "
                "Mount the router from a normal plugin with include_router(). "
                "/ 入口类是 PluginRouter；请在普通插件中用 include_router() 挂载。"
                "/ エントリークラスは PluginRouter です。通常プラグインから include_router() でマウントしてください。"
                "Aborting process for plugin '{}'.",
                cls.__name__, plugin_id,
            )
            status_sender.put_nowait({
                "type": "plugin_status",
                "plugin_id": plugin_id,
                "status": "error",
                "error": f"Plugin '{plugin_id}' entry is a PluginRouter, not a NekoPluginBase. "
                         "Mount it from a normal plugin with include_router(). "
                         "/ 请在普通插件中用 include_router() 挂载。"
                         "/ 通常プラグインから include_router() でマウントしてください。",
            })
            try:
                ctx.close()
            except Exception as e:
                logger.debug("[Plugin Process] Context close failed after invalid router entry: {}", e)
            try:
                child_transport.close()
            except Exception:
                pass
            return

        startup_config_fingerprint: str | None = None
        effective_cfg: dict[str, object] = {}
        try:
            from plugin.server.infrastructure.config_resolver import resolve_plugin_config_from_path

            resolved_config = resolve_plugin_config_from_path(
                plugin_id,
                config_path=Path(config_path),
            )
            resolved_effective_cfg = resolved_config["effective_config"]
            if not isinstance(resolved_effective_cfg, dict):
                raise TypeError("Startup effective config must be a mapping")
            effective_cfg = resolved_effective_cfg
            ctx._set_effective_config_cache(effective_cfg)
            # Keep the applied identity in the child, where the effective
            # configuration is actually resolved immediately before startup.
            # The parent must not hash its earlier pre-spawn snapshot.
            from plugin.server.infrastructure.config_fingerprint import fingerprint_config

            startup_config_fingerprint = fingerprint_config(effective_cfg)
        except Exception as e:
            logger.debug("[Plugin Process] Could not resolve startup config: {}", type(e).__name__)

        instance = cls(ctx)

        if startup_config_fingerprint is None:
            # Retain the SDK read fallback when child-side resolution fails.
            # Only a successful read can establish the loaded identity.
            try:
                fallback_cfg = asyncio.run(instance.config.dump(timeout=3.0))
                if not isinstance(fallback_cfg, dict):
                    raise TypeError("Startup effective config must be a mapping")
                effective_cfg = fallback_cfg
                ctx._set_effective_config_cache(effective_cfg)
                from plugin.server.infrastructure.config_fingerprint import fingerprint_config

                startup_config_fingerprint = fingerprint_config(effective_cfg)
            except Exception as e:
                logger.debug("[Plugin Process] Could not read startup config through SDK: {}", type(e).__name__)

        # Configuration overrides instance defaults, preserving old checkpoint fallback order.
        freezable_keys = getattr(instance, "__freezable__", []) or []
        persist_mode = getattr(instance, "__persist_mode__", None)
        if persist_mode is None:
            persist_mode = getattr(instance, "__freeze_mode__", "off")
        try:
            # New [plugin_state] settings.
            state_cfg = effective_cfg.get("plugin_state", {})
            if isinstance(state_cfg, dict):
                cfg_persist_mode = state_cfg.get("persist_mode")
                if cfg_persist_mode in ("auto", "manual", "off"):
                    persist_mode = cfg_persist_mode
                    logger.debug("[Plugin Process] persist_mode from effective config: {}", persist_mode)
            # Backward-compatible old [plugin_checkpoint] settings.
            if persist_mode == "off":
                checkpoint_cfg = effective_cfg.get("plugin_checkpoint", {})
                if isinstance(checkpoint_cfg, dict):
                    cfg_freeze_mode = checkpoint_cfg.get("freeze_mode")
                    if cfg_freeze_mode in ("auto", "manual", "off"):
                        persist_mode = cfg_freeze_mode
                        logger.debug("[Plugin Process] persist_mode from legacy plugin_checkpoint config: {}", persist_mode)
        except Exception as e:
            logger.debug("[Plugin Process] Could not read plugin_state from effective config: {}", e)

        # Mark restored frozen state for unfreeze lifecycle events.
        ctx._restored_from_freeze = False
        
        if freezable_keys:
            logger.debug("[Plugin Process] Freezable attributes: {}, mode: {}", freezable_keys, persist_mode)
            # Attempt restoring saved state if present.
            state_persistence = getattr(instance, "_state_persistence", None) or getattr(instance, "_freeze_checkpoint", None)
            if state_persistence:
                try:
                    def _unwrap_state_result(op_name: str, result_obj: object) -> object:
                        is_ok = getattr(result_obj, "is_ok", None)
                        if callable(is_ok):
                            if is_ok():
                                return getattr(result_obj, "value", None)
                            error_obj = getattr(result_obj, "error", None)
                            logger.warning(
                                "[Plugin Process] State persistence {} failed: {}",
                                op_name,
                                error_obj,
                            )
                            raise RuntimeError(f"state persistence {op_name} failed: {error_obj}")
                        return result_obj

                    has_saved_state_result = asyncio.run(state_persistence.has_saved_state())
                    has_saved_state = _unwrap_state_result("has_saved_state", has_saved_state_result)
                    if bool(has_saved_state):
                        logger.debug("[Plugin Process] Restoring saved state...")
                        load_result_obj = asyncio.run(state_persistence.load(instance))
                        load_result = _unwrap_state_result("load", load_result_obj)
                        if bool(load_result):
                            clear_result_obj = asyncio.run(state_persistence.clear())  # Clear after restoration.
                            clear_result = _unwrap_state_result("clear", clear_result_obj)
                            if not bool(clear_result):
                                raise RuntimeError("state persistence clear returned falsy result")
                            ctx._restored_from_freeze = True  # Mark restored from freeze.
                except Exception as e:
                    logger.warning("[Plugin Process] Failed to restore saved state: {}", e)
        
        def _should_persist(method) -> bool:
            """Determine whether state should persist."""
            if not freezable_keys or persist_mode == "off":
                return False
            # Check method-level persist settings.
            method_persist = getattr(method, PERSIST_ATTR, None)
            if method_persist is not None:
                return method_persist  # Explicit method setting.
            # Follow class-level settings.
            return persist_mode == "auto"

        entry_map: Dict[str, Any] = {}
        entry_meta_map: Dict[str, Any] = {}  # Store EventMeta for custom settings such as timeout.
        events_by_type: Dict[str, Dict[str, Any]] = {}
        ui_context_map: Dict[str, Any] = {}

        def _get_ui_context_meta(member: Any) -> dict[str, Any] | None:
            candidates: list[Any] = [member]
            if hasattr(member, "__func__"):
                candidates.append(member.__func__)
            current = getattr(member, "__wrapped__", None)
            seen: set[int] = set()
            while current is not None and id(current) not in seen:
                seen.add(id(current))
                candidates.append(current)
                if hasattr(current, "__func__"):
                    candidates.append(current.__func__)
                current = getattr(current, "__wrapped__", None)
            for candidate in candidates:
                meta = getattr(candidate, UI_CONTEXT_META_ATTR, None)
                if isinstance(meta, dict):
                    return dict(meta)
            return None

        def _rebuild_ui_context_map() -> None:
            ui_context_map.clear()
            for _name, member in inspect.getmembers(instance, predicate=callable):
                meta = _get_ui_context_meta(member)
                if not meta:
                    continue
                context_id = str(meta.get("id") or "main").strip() or "main"
                ui_context_map[context_id] = member

        def _serialize_ui_context_result(result: Any) -> Dict[str, Any]:
            schema: Dict[str, Any] | None = None
            data: Any
            model_dump = getattr(result, "model_dump", None)
            if callable(model_dump):
                # normalize_structured_data already handles model_dump mode fallback for handwritten
                # implementations lacking mode, recursively flattening Mapping/dataclass/tuple
                # into primitives. Do not duplicate it.
                data = normalize_structured_data(result)
                schema = model_schema_from_type(type(result))
            elif isinstance(result, (dict, list)):
                data = normalize_structured_data(result)
            else:
                data = {"value": normalize_structured_data(result)}
            # Normalize to JSON-safe values in children: results first cross pickle to parents, then
            # FastAPI responses. Locks/sockets/async generators prevent the entire response,
            # including action whitelists, from sending, leaving parents waiting until timeout.
            # Custom classes unavailable to parent imports also fail pickle.loads. Failing here
            # becomes ordinary context_error at callers, symmetric with provider exceptions.
            data = json.loads(json.dumps(data, default=str, ensure_ascii=False))
            return {"state": data, "state_schema": schema}

        def _get_ui_action_meta(member: Any) -> dict[str, Any] | None:
            candidates: list[Any] = [member]
            if hasattr(member, "__func__"):
                candidates.append(member.__func__)
            current = getattr(member, "__wrapped__", None)
            seen: set[int] = set()
            while current is not None and id(current) not in seen:
                seen.add(id(current))
                candidates.append(current)
                if hasattr(current, "__func__"):
                    candidates.append(current.__func__)
                current = getattr(current, "__wrapped__", None)
            for candidate in candidates:
                meta = getattr(candidate, UI_ACTION_META_ATTR, None)
                if isinstance(meta, dict):
                    return dict(meta)
            return None

        def _collect_ui_actions() -> list[dict[str, Any]]:
            actions: list[dict[str, Any]] = []
            for entry_id, handler in entry_map.items():
                # Per-entry tolerance: invalid metadata drops only that action,
                # not the entire authorization whitelist, which makes every panel button return 403.
                try:
                    ui_meta = _get_ui_action_meta(handler)
                    if not ui_meta:
                        continue
                    entry_meta = entry_meta_map.get(entry_id)
                    action_id = str(ui_meta.get("id") or entry_id)
                    raw_schema = getattr(entry_meta, "input_schema", None)
                    actions.append({
                        "id": action_id,
                        "entry_id": entry_id,
                        "label": ui_meta.get("label") or getattr(entry_meta, "name", entry_id),
                        "description": getattr(entry_meta, "description", ""),
                        # Plugin-written input_schema may be nonmapping, e.g. strings.
                        "input_schema": dict(raw_schema) if isinstance(raw_schema, dict) else {},
                        "icon": ui_meta.get("icon"),
                        "tone": ui_meta.get("tone") or "default",
                        "group": ui_meta.get("group"),
                        "order": int(ui_meta.get("order") or 0),
                        "confirm": ui_meta.get("confirm") or False,
                        "refresh_context": bool(ui_meta.get("refresh_context", True)),
                    })
                except Exception:
                    logger.exception("Skipping malformed UI action metadata for entry '{}'", entry_id)
            actions.sort(key=lambda item: (str(item.get("group") or ""), int(item.get("order") or 0), str(item.get("label") or item.get("id"))))
            # Plugin-written label/confirm can be arbitrary objects. Responses cross pickle then HTTP;
            # flattening here ensures whitelist responses remain transferable.
            # Normalize first: confirm declares Mapping, and passing nondict Mapping directly to
            # json.dumps would let default=str turn it into
            # a useless string.
            return json.loads(
                json.dumps(normalize_structured_data(actions), default=str, ensure_ascii=False)
            )

        def _rebuild_entry_map() -> None:
            """Rebuild entry_map and events_by_type."""
            collected = instance.collect_entries(wrap_with_hooks=True)
            entry_map.clear()
            entry_meta_map.clear()
            events_by_type.clear()
            for eid, eh in collected.items():
                entry_map[eid] = eh.handler
                entry_meta_map[eid] = eh.meta
                etype = getattr(eh.meta, "event_type", "plugin_entry")
                events_by_type.setdefault(etype, {})
                events_by_type[etype][eid] = eh.handler
            ctx._entry_map = entry_map

        # Prefer collect_entries(), supporting Hook wrappers.
        if hasattr(instance, "collect_entries") and callable(instance.collect_entries):
            try:
                collected = instance.collect_entries(wrap_with_hooks=True)
                for eid, event_handler in collected.items():
                    entry_map[eid] = event_handler.handler
                    entry_meta_map[eid] = event_handler.meta
                    etype = getattr(event_handler.meta, "event_type", "plugin_entry")
                    events_by_type.setdefault(etype, {})
                    events_by_type[etype][eid] = event_handler.handler
                logger.info("Plugin entries collected via collect_entries(): {}", list(entry_map.keys()))
            except Exception as e:
                logger.warning("Failed to collect entries via collect_entries(): {}, falling back to scan", e)
                entry_map.clear()
                entry_meta_map.clear()
                events_by_type.clear()
        
        # Fall back to method scanning if collect_entries is missing or fails.
        if not entry_map:
            for name, member in inspect.getmembers(instance, predicate=callable):
                if name.startswith("_") and not hasattr(member, EVENT_META_ATTR):
                    continue
                event_meta = getattr(member, EVENT_META_ATTR, None)
                if not event_meta:
                    wrapped = getattr(member, "__wrapped__", None)
                    if wrapped is not None:
                        event_meta = getattr(wrapped, EVENT_META_ATTR, None)

                if event_meta:
                    eid = getattr(event_meta, "id", name)
                    entry_map[eid] = member
                    entry_meta_map[eid] = event_meta  # Store EventMeta for custom settings.
                    etype = getattr(event_meta, "event_type", "plugin_entry")
                    events_by_type.setdefault(etype, {})
                    events_by_type[etype][eid] = member
                else:
                    entry_map[name] = member
            logger.info("Plugin instance created. Mapped entries: {}", list(entry_map.keys()))
        
        ctx._entry_map = entry_map
        ctx._instance = instance
        _rebuild_ui_context_map()
        if ui_context_map:
            logger.info("Plugin UI contexts collected: {}", list(ui_context_map.keys()))

        # asyncio.Queue fed from the downlink for plugin-to-plugin responses
        _response_inbox: asyncio.Queue = asyncio.Queue()
        ctx._response_queue = _response_inbox
        _startup_pending_downlink: list[tuple[str, dict]] = []

        async def _route_response(msg: Any) -> None:
            dispatch_direct = getattr(ctx, "_dispatch_direct_response", None)
            if callable(dispatch_direct):
                try:
                    if dispatch_direct(msg):
                        return
                except Exception:
                    logger.exception("Failed to dispatch SDK-owned response")
            await _response_inbox.put(msg)

        async def _startup_downlink_pump(stop_event: asyncio.Event) -> None:
            poll_ms = int(QUEUE_GET_TIMEOUT * 1000)
            while not stop_event.is_set():
                result = await child_transport.recv_downlink(timeout_ms=poll_ms)
                if result is None:
                    continue
                ch, msg = result
                if ch == CH_RESP:
                    await _response_inbox.put(msg)
                    continue
                if ch == CH_CMD and isinstance(msg, dict) and msg.get("type") == "STOP":
                    stop_event.set()
                    break
                if isinstance(msg, dict):
                    _startup_pending_downlink.append((ch, msg))

        async def _run_startup_with_downlink(startup_callable: Any) -> None:
            stop_event = asyncio.Event()
            pump_task = asyncio.create_task(_startup_downlink_pump(stop_event), name="startup-downlink-pump")
            try:
                await startup_callable()
            finally:
                stop_event.set()
                pump_task.cancel()
                try:
                    await pump_task
                except asyncio.CancelledError:
                    pass

        # Lifecycle: startup.
        lifecycle_events = events_by_type.get("lifecycle", {})

        def _send_startup_result(startup_error: str | None) -> None:
            try:
                startup_success = startup_error is None
                startup_data: dict[str, Any] = {
                    "status": "ready" if startup_success else "failed",
                }
                if startup_config_fingerprint:
                    startup_data["config_fingerprint"] = startup_config_fingerprint
                if startup_error is not None:
                    startup_data["startup_error"] = startup_error
                res_sender.put(
                    {
                        "req_id": STARTUP_RESULT_REQ_ID,
                        "success": startup_success,
                        "data": startup_data,
                        "error": startup_error,
                    },
                    timeout=10.0,
                )
            except Exception:
                logger.exception("[Plugin Process] Failed to send startup result")

        startup_fn = lifecycle_events.get("startup")
        startup_error: str | None = None
        if startup_fn:
            try:
                with ctx._handler_scope("lifecycle.startup"):
                    asyncio.run(_run_with_model_client_cleanup(ctx, _run_startup_with_downlink(startup_fn)))
            except (KeyboardInterrupt, SystemExit):
                # Propagate system-level interruptions directly.
                raise
            except Exception as e:
                error_msg = f"Error in lifecycle.startup: {str(e)}"
                logger.exception(error_msg)
                startup_error = error_msg
                # Record errors without aborting process startup.
                # Fatal startup failures may raise PluginLifecycleError here.
        
        # Lifecycle: unfreeze when restored from frozen state.
        # Determine frozen restoration by checking restored state.
        _restored_from_freeze = False
        if freezable_keys:
            state_persistence = getattr(instance, "_state_persistence", None) or getattr(instance, "_freeze_checkpoint", None)
            # Check ctx restoration flags set by restore logic.
            _restored_from_freeze = getattr(ctx, "_restored_from_freeze", False)
        
        unfreeze_fn = lifecycle_events.get("unfreeze")
        if unfreeze_fn and _restored_from_freeze:
            try:
                logger.info("[Plugin Process] Executing unfreeze lifecycle (restored from frozen state)...")
                with ctx._handler_scope("lifecycle.unfreeze"):
                    asyncio.run(_run_with_model_client_cleanup(ctx, unfreeze_fn()))
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception as e:
                error_msg = f"Error in lifecycle.unfreeze: {str(e)}"
                logger.exception(error_msg)

        _send_startup_result(startup_error)
        if startup_error is not None and startup_failure_policy == "fail":
            logger.info("[Plugin Process] Startup failed with policy='fail'; skipping auto-start work")
            try:
                ctx.close()
            except Exception as e:
                logger.debug("[Plugin Process] Context close failed after startup failure: {}", e)
            try:
                child_transport.close()
            except Exception:
                pass
            return

        # Scheduled tasks: timer auto_start interval.
        def _run_timer_interval(fn, interval_seconds: int, fn_name: str, stop_event: threading.Event):
            while not stop_event.is_set():
                try:
                    with ctx._handler_scope(f"timer.{fn_name}"):
                        asyncio.run(_run_with_model_client_cleanup(ctx, fn()))
                except (KeyboardInterrupt, SystemExit):
                    # System-level interruptions stop scheduled tasks.
                    logger.info("Timer '{}' interrupted, stopping", fn_name)
                    break
                except Exception:
                    logger.exception("Timer '{}' failed", fn_name)
                    # Individual scheduled-task failures must not break the loop.
                stop_event.wait(interval_seconds)

        timer_events = events_by_type.get("timer", {})
        timer_stop_events: list[threading.Event] = []
        for eid, fn in timer_events.items():
            meta = getattr(fn, EVENT_META_ATTR, None)
            if not meta or not getattr(meta, "auto_start", False):
                continue
            mode = getattr(meta, "extra", {}).get("mode")
            if mode == "interval":
                seconds = getattr(meta, "extra", {}).get("seconds", 0)
                if seconds > 0:
                    timer_stop_event = threading.Event()
                    timer_stop_events.append(timer_stop_event)
                    t = threading.Thread(
                        target=_run_timer_interval,
                        args=(fn, seconds, eid, timer_stop_event),
                        daemon=True,
                    )
                    t.start()
                    logger.info("Started timer '{}' every {}s", eid, seconds)

        # Handle custom events with automatic startup.
        def _run_custom_event_auto(fn, fn_name: str, event_type: str):
            """Run autostart custom events."""
            try:
                with ctx._handler_scope(f"{event_type}.{fn_name}"):
                    asyncio.run(_run_with_model_client_cleanup(ctx, fn()))
            except (KeyboardInterrupt, SystemExit):
                logger.info("Custom event '{}' (type: {}) interrupted", fn_name, event_type)
            except Exception:
                logger.exception("Custom event '{}' (type: {}) failed", fn_name, event_type)

        # Scan every custom event type.
        for event_type, events in events_by_type.items():
            if event_type in ("plugin_entry", "lifecycle", "message", "timer"):
                continue  # Skip standard types.
            
            # Custom event type.
            logger.info("Found custom event type: {} with {} handlers", event_type, len(events))
            for eid, fn in events.items():
                meta = getattr(fn, EVENT_META_ATTR, None)
                if not meta:
                    continue
                
                # Process autostart custom events.
                if getattr(meta, "auto_start", False):
                    trigger_method = getattr(meta, "extra", {}).get("trigger_method", "auto")
                    if trigger_method == "auto":
                        # Start in a separate thread.
                        t = threading.Thread(
                            target=_run_custom_event_auto,
                            args=(fn, eid, event_type),
                            daemon=True,
                        )
                        t.start()
                        logger.info("Started auto custom event '{}' (type: {})", eid, event_type)

        # ────────────────────────────────────────────────────
        #  Async command loop  (replaces the old sync while-loop)
        # ────────────────────────────────────────────────────

        _STALL_THRESHOLD = 5.0

        async def _run_with_watchdog(coro, label: str, timeout_seconds):
            """Run *coro* with a stall-detection watchdog and timeout."""
            task = asyncio.ensure_future(coro)

            async def _watchdog():
                while not task.done():
                    t0 = time.monotonic()
                    await asyncio.sleep(1.0)
                    wall = time.monotonic() - t0
                    if wall > _STALL_THRESHOLD and not task.done():
                        logger.warning(
                            "[Plugin Process] Event-loop stall: sleep(1s) took {:.1f}s "
                            "in '{}'. Likely blocking I/O in async code.",
                            wall, label,
                        )

            wd = asyncio.create_task(_watchdog())
            try:
                return await asyncio.wait_for(task, timeout=timeout_seconds)
            finally:
                wd.cancel()

        def _resolve_timeout(entry_id: str, requested_timeout: Any = _TIMEOUT_UNSET):
            # _TIMEOUT_UNSET distinguishes absent entry timeouts from explicit timeout<=0 -> None.
            entry_timeout = resolve_entry_timeout(entry_meta_map.get(entry_id), _TIMEOUT_UNSET)
            entry_has_timeout = entry_timeout is not _TIMEOUT_UNSET

            if requested_timeout is not _TIMEOUT_UNSET:
                if requested_timeout is None:
                    # Caller None delegates to entry settings.
                    return entry_timeout if entry_has_timeout else None
                try:
                    explicit_timeout = float(requested_timeout)
                except (TypeError, ValueError):
                    pass
                else:
                    if explicit_timeout <= 0:
                        return entry_timeout if entry_has_timeout else None
                    # Explicit entry timeout None disables limits; respect it.
                    if entry_has_timeout and entry_timeout is None:
                        return None
                    # Positive entry timeout uses the smaller limit.
                    if entry_has_timeout:
                        return min(explicit_timeout, entry_timeout)
                    return explicit_timeout

            if entry_has_timeout:
                return entry_timeout
            return PLUGIN_TRIGGER_TIMEOUT

        async def _save_plugin_state(reason: str) -> None:
            sp = getattr(instance, "_state_persistence", None) or getattr(instance, "_freeze_checkpoint", None)
            if not sp:
                return

            save_method = getattr(sp, "save", None)
            if not callable(save_method):
                return

            try:
                save_result = save_method(instance)
            except TypeError:
                save_result = save_method(instance, freezable_keys, reason=reason)

            if inspect.isawaitable(save_result):
                await save_result

        # run_id → asyncio.Task – used by CANCEL_RUN to propagate cancellation
        _run_tasks: Dict[str, asyncio.Task] = {}

        async def _handle_trigger(msg: dict):
            entry_id = msg.get("entry_id")
            args = msg.get("args", {})
            req_id = msg.get("req_id", "unknown")

            if not entry_id or not isinstance(args, dict):
                res_sender.put(
                    {"req_id": req_id, "success": False, "data": None,
                     "error": "Invalid TRIGGER payload: 'entry_id' required, 'args' must be dict"},
                    timeout=10.0,
                )
                return

            logger.info("[Plugin Process] TRIGGER entry='{}' req_id={}", entry_id, req_id)

            method = (
                entry_map.get(entry_id)
                or getattr(instance, entry_id, None)
                or getattr(instance, f"entry_{entry_id}", None)
            )
            if method is None and hasattr(instance, "collect_entries") and callable(instance.collect_entries):
                try:
                    _rebuild_entry_map()
                    method = (
                        entry_map.get(entry_id)
                        or getattr(instance, entry_id, None)
                        or getattr(instance, f"entry_{entry_id}", None)
                    )
                    if method is not None:
                        logger.info(
                            "[Plugin Process] Rebuilt dynamic entry map and resolved entry='{}' req_id={}",
                            entry_id,
                            req_id,
                        )
                except Exception as e:
                    logger.warning(
                        "Failed to rebuild entry map while resolving '{}' req_id={}: {}",
                        entry_id,
                        req_id,
                        e,
                    )
            ret = {"req_id": req_id, "success": False, "data": None, "error": None}

            run_id = None
            lanlan_name = None
            try:
                ctx_obj = args.get("_ctx") if isinstance(args, dict) else None
                if isinstance(ctx_obj, dict):
                    run_id = ctx_obj.get("run_id")
                    lanlan_name = ctx_obj.get("lanlan_name")
                    if lanlan_name:
                        ctx._current_lanlan = str(lanlan_name)
            except Exception:
                pass

            try:
                if not method:
                    raise PluginEntryNotFoundError(plugin_id, entry_id)

                if not (asyncio.iscoroutinefunction(method) or inspect.iscoroutinefunction(method)):
                    ret["error"] = f"Entry '{entry_id}' must be 'async def'. Sync entries are not supported."
                    return

                requested_timeout = msg["timeout"] if "timeout" in msg else _TIMEOUT_UNSET
                timeout_seconds = _resolve_timeout(entry_id, requested_timeout)
                call_args = prepare_entry_kwargs(
                    plugin_id=plugin_id,
                    entry_id=entry_id,
                    handler=method,
                    meta=entry_meta_map.get(entry_id),
                    args=args,
                )

                with ctx._handler_scope(f"plugin_entry.{entry_id}"), ctx._run_scope(run_id), ctx._lanlan_scope(lanlan_name):
                    result = await _run_with_watchdog(
                        method(**call_args), entry_id, timeout_seconds,
                    )

                if hasattr(result, "is_ok") and callable(result.is_ok):
                    if result.is_ok():
                        ret["success"] = True
                        ret["data"] = result.value
                    else:
                        ret["success"] = False
                        err = result.error
                        ret["error"] = str(err) if err is not None else "Unknown error"
                else:
                    ret["success"] = True
                    ret["data"] = result

                if _should_persist(method):
                    try:
                        await _save_plugin_state("auto")
                    except Exception:
                        pass

            except asyncio.CancelledError:
                ret["error"] = "Execution cancelled"
            except asyncio.TimeoutError:
                requested_timeout = msg["timeout"] if "timeout" in msg else _TIMEOUT_UNSET
                resolved_timeout = _resolve_timeout(entry_id, requested_timeout)
                logger.error("Entry '{}' timed out after {}s", entry_id, resolved_timeout)
                ret["error"] = f"Execution timed out after {resolved_timeout}s"
            except PluginError as e:
                logger.warning("Plugin error executing '{}': {}", entry_id, e)
                ret["error"] = str(e)
            except Exception as e:
                logger.exception("Unexpected error executing '{}'", entry_id)
                ret["error"] = f"Unexpected error: {e}"
            finally:
                if run_id:
                    _run_tasks.pop(run_id, None)
                try:
                    res_sender.put(ret, timeout=10.0)
                except Exception:
                    logger.exception("Failed to send response for req_id={}", req_id)

        async def _handle_trigger_custom(msg: dict):
            event_type = msg.get("event_type")
            event_id = msg.get("event_id")
            args = msg.get("args", {})
            req_id = msg.get("req_id", "unknown")

            logger.info("[Plugin Process] TRIGGER_CUSTOM {}.{} req_id={}", event_type, event_id, req_id)

            lanlan_name = None
            try:
                ctx_obj = args.get("_ctx") if isinstance(args, dict) else None
                if isinstance(ctx_obj, dict):
                    lanlan_name = ctx_obj.get("lanlan_name")
                    if lanlan_name:
                        ctx._current_lanlan = str(lanlan_name)
            except Exception:
                pass

            custom_events = events_by_type.get(event_type, {})
            method = custom_events.get(event_id)
            ret = {"req_id": req_id, "success": False, "data": None, "error": None}

            try:
                if not method:
                    ret["error"] = f"Custom event '{event_type}.{event_id}' not found"
                    return

                if not asyncio.iscoroutinefunction(method):
                    ret["error"] = f"Custom event '{event_type}.{event_id}' must be 'async def'."
                    return

                with ctx._handler_scope(f"{event_type}.{event_id}"), ctx._lanlan_scope(lanlan_name):
                    result = await _run_with_watchdog(
                        method(**args),
                        f"{event_type}.{event_id}",
                        PLUGIN_TRIGGER_TIMEOUT,
                    )

                if hasattr(result, "is_ok") and callable(result.is_ok):
                    if result.is_ok():
                        ret["success"] = True
                        ret["data"] = result.value
                    else:
                        ret["success"] = False
                        err = result.error
                        ret["error"] = str(err) if err is not None else "Unknown error"
                else:
                    ret["success"] = True
                    ret["data"] = result

            except asyncio.CancelledError:
                ret["error"] = "Execution cancelled"
            except asyncio.TimeoutError:
                logger.error("Custom event {}.{} timed out", event_type, event_id)
                ret["error"] = f"Custom event timed out after {PLUGIN_TRIGGER_TIMEOUT}s"
            except Exception as e:
                logger.exception("Error executing custom event {}.{}", event_type, event_id)
                ret["error"] = str(e)
            finally:
                try:
                    res_sender.put(ret, timeout=10.0)
                except Exception:
                    logger.exception("Failed to send response for req_id={}", req_id)

        async def _handle_ui_context(msg: dict):
            req_id = msg.get("req_id", "unknown")
            context_id = str(msg.get("context_id") or "main")
            ret = {"req_id": req_id, "success": False, "data": None, "error": None}
            # Bind outside try so fallback/finally can use it after any later failure.
            actions: list[dict[str, Any]] = []
            context_payload: Dict[str, Any] = {"state": {}, "state_schema": None}
            context_error: str | None = None
            try:
                # Derive actions only from @ui.action decorator metadata, never invoking plugin providers.
                # Keep independent of plugin-written @ui.context: missing/failed/timed-out providers
                # must not hide api.call authorization whitelists, or every panel button
                # returns 500 unrelated to the actual cause.
                actions = _collect_ui_actions()
                if context_id == "__chat_card__":
                    # Card buttons need the UI-action list, not a panel's state provider.
                    ret.update(success=True, data={"state": {}, "actions": actions})
                    return
                provider_budget = _ui_context_provider_budget(msg.get("timeout"))

                provider = ui_context_map.get(context_id)
                if provider is None:
                    # rebuild uses inspect.getmembers on plugin instances, potentially triggering
                    # failing __getattr__/properties. Treat failure as provider absence,
                    # preserving already computed whitelists.
                    try:
                        _rebuild_ui_context_map()
                    except Exception:
                        logger.exception("Failed to rebuild UI context map for '{}'", context_id)
                    provider = ui_context_map.get(context_id)

                if provider is None:
                    context_error = f"UI context '{context_id}' not found"
                    logger.warning(
                        "UI context '{}' not found; serving actions without state", context_id,
                    )
                else:
                    try:
                        result = provider()
                        completed = True
                        if inspect.isawaitable(result):
                            completed, result = await _await_within_budget(result, provider_budget)
                        if completed:
                            context_payload = _serialize_ui_context_result(result)
                        else:
                            logger.warning(
                                "UI context '{}' timed out after {}s", context_id, provider_budget,
                            )
                            context_error = (
                                f"UI context '{context_id}' timed out after {provider_budget}s"
                            )
                    except Exception as e:
                        logger.exception("Failed to execute UI context '{}'", context_id)
                        context_error = str(e) or type(e).__name__

                ret["success"] = True
                ret["data"] = {
                    **context_payload,
                    "actions": actions,
                    "context_error": context_error,
                }
            except Exception as e:
                logger.exception("Failed to build UI context '{}'", context_id)
                reason = str(e) or type(e).__name__
                if actions:
                    # Keep computed whitelists so panels remain usable; report causes as warnings.
                    ret["success"] = True
                    ret["data"] = {
                        "state": {},
                        "state_schema": None,
                        "actions": actions,
                        "context_error": reason,
                    }
                else:
                    # Fail only when no actions exist; sending an empty whitelist makes every
                    # button return 403, harder to diagnose than 500.
                    ret["error"] = reason
            finally:
                try:
                    res_sender.put(ret, timeout=10.0)
                except Exception:
                    logger.exception("Failed to send UI context response for req_id={}", req_id)

        async def _async_command_loop():
            poll_ms = int(QUEUE_GET_TIMEOUT * 1000)
            on_command_loop_start = getattr(instance, "_on_command_loop_start", None)
            if callable(on_command_loop_start):
                try:
                    with ctx._handler_scope("lifecycle.command_loop_start"):
                        result = on_command_loop_start()
                        if inspect.isawaitable(result):
                            await result
                except Exception:
                    logger.exception("[Plugin Process] _on_command_loop_start failed")

            # Downlink is live from here: everything below reads replies. Set
            # AFTER _on_command_loop_start, because that hook can await for an
            # arbitrary time and uploads launched during it would still have no
            # reader. Timer and custom-event threads started earlier block on
            # this instead of sending into a void (Codex).
            try:
                ctx._downlink_ready.set()
            except Exception:
                logger.exception("[Plugin Process] failed to mark downlink ready")

            while True:
                try:
                    if process_stop_event is not None and process_stop_event.is_set():
                        break
                except Exception:
                    pass

                try:
                    if _startup_pending_downlink:
                        result = _startup_pending_downlink.pop(0)
                    else:
                        result = await child_transport.recv_downlink(timeout_ms=poll_ms)
                except asyncio.CancelledError:
                    logger.info("[Plugin Process] Command loop cancelled, shutting down")
                    break
                if result is None:
                    continue
                ch, msg = result

                # Plugin-to-plugin responses arrive on the downlink tagged CH_RESP
                if ch == CH_RESP:
                    await _route_response(msg)
                    continue

                if ch != CH_CMD or not isinstance(msg, dict):
                    continue
                msg_type = msg.get("type")
                if not msg_type:
                    continue

                # ── STOP ──
                if msg_type == "STOP":
                    break

                # ── CANCEL_RUN ──
                if msg_type == "CANCEL_RUN":
                    run_id = msg.get("run_id")
                    task = _run_tasks.get(run_id) if run_id else None
                    if task and not task.done():
                        task.cancel()
                        logger.info("[Plugin Process] Cancel sent for run_id={}", run_id)
                    continue

                # ── FREEZE ──
                if msg_type == "FREEZE":
                    req_id = msg.get("req_id", "unknown")
                    logger.info("[Plugin Process] FREEZE req_id={}", req_id)
                    ctx._image_uploads_blocked = True
                    ret = {"req_id": req_id, "success": False, "data": None, "error": None}
                    try:
                        freeze_fn = lifecycle_events.get("freeze")
                        if freeze_fn:
                            with ctx._handler_scope("lifecycle.freeze"):
                                await freeze_fn()
                        if freezable_keys:
                            await _save_plugin_state("freeze")
                        ret["success"] = True
                        ret["data"] = {"frozen": True, "freezable_keys": freezable_keys}
                    except Exception as e:
                        logger.exception("[Plugin Process] Freeze failed")
                        ret["error"] = str(e)
                    res_sender.put(ret, timeout=10.0)
                    if ret["success"]:
                        break
                    ctx._image_uploads_blocked = False
                    continue

                # ── BUS_CHANGE ──
                if msg_type == "BUS_CHANGE":
                    try:
                        dispatch_bus_change(
                            sub_id=str(msg.get("sub_id") or ""),
                            bus=str(msg.get("bus") or ""),
                            op=str(msg.get("op") or ""),
                            delta=msg.get("delta") if isinstance(msg.get("delta"), dict) else None,
                        )
                    except Exception as e:
                        logger.debug("Failed to dispatch bus change: {}", e)
                    continue

                # ── CONFIG_UPDATE ──
                if msg_type == "CONFIG_UPDATE":
                    # The loop awaits this handler INLINE, so it cannot route
                    # anyone else's IMAGE_UPLOAD_RESULT until it returns. A
                    # timer or custom-event upload running concurrently would
                    # otherwise send into a loop that is not reading and could
                    # only time out (Codex P2) -- the same failure as the
                    # startup window, from the same cause.
                    #
                    # So the flag means "responses are being routed right now",
                    # not "the loop started once". Cleared for the duration and
                    # restored in a finally, so a handler that raises cannot
                    # leave uploads blocked for the rest of the process.
                    ctx._downlink_ready.clear()
                    try:
                        await _handle_config_update_command(
                            msg=msg, ctx=ctx, events_by_type=events_by_type,
                            plugin_id=plugin_id, res_sender=res_sender,
                            logger=logger,
                        )
                    finally:
                        ctx._downlink_ready.set()
                    continue

                # ── TRIGGER_CUSTOM ──
                if msg_type == "TRIGGER_CUSTOM":
                    req_id = str(msg.get("req_id") or uuid.uuid4())
                    task_key = f"custom:{req_id}"
                    task = asyncio.create_task(_handle_trigger_custom(msg))
                    _run_tasks[task_key] = task
                    task.add_done_callback(lambda _t, key=task_key: _run_tasks.pop(key, None))
                    continue

                # ── UI_CONTEXT ──
                if msg_type == "UI_CONTEXT":
                    req_id = str(msg.get("req_id") or uuid.uuid4())
                    task_key = f"ui_context:{req_id}"
                    task = asyncio.create_task(_handle_ui_context(msg))
                    _run_tasks[task_key] = task
                    task.add_done_callback(lambda _t, key=task_key: _run_tasks.pop(key, None))
                    continue

                # ── TRIGGER ──
                if msg_type == "TRIGGER":
                    run_id = None
                    try:
                        ctx_obj = msg.get("args", {}).get("_ctx")
                        if isinstance(ctx_obj, dict):
                            run_id = ctx_obj.get("run_id")
                    except Exception:
                        pass
                    task = asyncio.create_task(_handle_trigger(msg))
                    # Register every trigger so shutdown cancels it before the
                    # model clients close; only run_id keys are cancellable by CANCEL_RUN.
                    task_key = run_id if run_id else f"trigger:{msg.get('req_id') or uuid.uuid4()}"
                    _run_tasks[task_key] = task
                    task.add_done_callback(
                        lambda _t, key=task_key: _run_tasks.pop(key, None) if _run_tasks.get(key) is _t else None
                    )
                    continue

        async def _command_loop_with_cleanup():
            try:
                await _async_command_loop()
            finally:
                # Also clean up when transport/handler dispatch raises. Close
                # model clients only after their request tasks have unwound.
                tasks = list(_run_tasks.values())
                for task in tasks:
                    if not task.done():
                        task.cancel()
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
                    _run_tasks.clear()

        asyncio.run(_run_with_model_client_cleanup(ctx, _command_loop_with_cleanup()))

        # Best-effort shutdown lifecycle and stop all scheduled tasks.
        try:
            for ev in timer_stop_events:
                try:
                    ev.set()
                except Exception:
                    pass
        except Exception:
            pass

        shutdown_fn = lifecycle_events.get("shutdown")
        if shutdown_fn:
            try:
                with ctx._handler_scope("lifecycle.shutdown"):
                    result = shutdown_fn()
                    if asyncio.iscoroutine(result):
                        asyncio.run(_run_with_model_client_cleanup(ctx, result))
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception as e:
                logger.exception("Error in lifecycle.shutdown: {}", e)

        try:
            ctx.close()
        except Exception as e:
            logger.debug("[Plugin Process] Context close failed during shutdown: {}", e)

        child_transport.close()

    except (KeyboardInterrupt, SystemExit):
        logger.info("Plugin process {} interrupted", plugin_id)
        try:
            for ev in timer_stop_events:
                try:
                    ev.set()
                except Exception:
                    pass
        except Exception:
            pass
        try:
            shutdown_fn = lifecycle_events.get("shutdown")
            if shutdown_fn:
                with ctx._handler_scope("lifecycle.shutdown"):
                    result = shutdown_fn()
                    if asyncio.iscoroutine(result):
                        asyncio.run(_run_with_model_client_cleanup(ctx, result))
        except BaseException:
            pass
        try:
            ctx.close()
        except Exception:
            pass
        try:
            child_transport.close()
        except Exception:
            pass
    except Exception as e:
        # Process crash: record details.
        logger.exception("Plugin process {} crashed", plugin_id)
        # Try reporting errors through the result queue.
        try:
            res_sender.put({
                "req_id": "CRASH",
                "success": False,
                "data": None,
                "error": f"Process crashed: {str(e)}"
            }, timeout=10.0)
            res_sender.put({
                "req_id": STARTUP_RESULT_REQ_ID,
                "success": False,
                "data": None,
                "error": f"Process crashed: {str(e)}"
            }, timeout=10.0)
        except Exception:
            pass  # If queues also failed, abandon reporting.
        raise  # Reraise so the process exits.


_PLUGIN_HOSTS: weakref.WeakSet[PluginHost] = weakref.WeakSet()
_FORKING_HOST = threading.local()


def _scrub_inherited_host_credentials() -> None:
    current = getattr(_FORKING_HOST, "host", None)
    for host in tuple(_PLUGIN_HOSTS):
        try:
            host.clear_inherited_credentials(keep_launch_options=host is current)
        except Exception:
            pass
    _PLUGIN_HOSTS.clear()
    try:
        state.clear_inherited_plugin_references()
    except Exception:
        pass


_HOST_CREDENTIAL_FORK_HOOK_REGISTERED = False
if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_scrub_inherited_host_credentials)
    _HOST_CREDENTIAL_FORK_HOOK_REGISTERED = True


class PluginHost:
    """
    Plugin-process host.
    
    Manages the complete plugin-process lifecycle:
    - Startup, shutdown, and monitoring directly.
    - IPC through PluginCommunicationResourceManager.
    """

    def __init__(self, plugin_id: str, entry_point: str, config_path: Path, *, source_only: bool = False):
        _PLUGIN_HOSTS.add(self)
        self.plugin_id = plugin_id
        self.entry_point = entry_point
        self.config_path = config_path
        # Set only after the child reports a successful startup handshake and
        # the parent confirms that the process is still alive.  Keeping this
        # on the host makes the applied identity belong to the process owner,
        # rather than to a caller's best-effort reload result.
        self.applied_config_fingerprint: str | None = None
        self.applied_config_loaded_at: str | None = None
        self.logger = logger.bind(plugin_id=plugin_id, host=True)

        # ZMQ transport: 4 PUSH/PULL socket channels replace 5 mp.Queues.
        # Only the three child->host uplinks carry tokens; downlinks carry no credentials.
        self.transport = HostTransport()

        self._process_stop_event: Any = multiprocessing.Event()
        self._startup_options: dict[str, object] = {"startup_failure": "warn"}
        self._model_gateway_token = ""
        self._model_gateway_starting = False
        # Mutated only for launch; no credentials enter environment/config/logs.
        self._model_gateway_options: dict[str, str] = {}
        if source_only:
            self._startup_options["source_only"] = True

        # Shared response notification primitives must be initialized before
        # forking, otherwise each child creates its own Manager proxies.
        try:
            _ = state.plugin_response_map
        except Exception as e:
            logger.warning(
                "Failed to pre-initialize plugin_response_map for plugin {}: {}",
                plugin_id, e
            )
        try:
            _ = state.plugin_response_notify_event
        except Exception as e:
            logger.warning(
                "Failed to pre-initialize plugin_response_notify_event for plugin {}: {}",
                plugin_id, e
            )

        # POSIX fork inherits all host fds; _close_inherited_host_sockets
        # drops HTTP sockets inside children, described above.
        self.process = multiprocessing.Process(
            target=_plugin_process_runner,
            args=(
                plugin_id,
                entry_point,
                config_path,
                self.transport.downlink_endpoint,
                self.transport.uplink_endpoint,
                getattr(self.transport, "uplink_token", ""),
                self._process_stop_event,
                self._startup_options,
                getattr(self.transport, "message_uplink_endpoint", ""),
            ),
            kwargs={
                "image_uplink_endpoint": getattr(
                    self.transport,
                    "image_uplink_endpoint",
                    None,
                ),
                "model_gateway_options": self._model_gateway_options,
            },
            # Plugin code may spawn subprocesses/Managers; daemon process would forbid that.
            daemon=False,
        )

        self.comm_manager = PluginCommunicationResourceManager(
            plugin_id=plugin_id,
            transport=self.transport,
        )
    
    def clear_inherited_credentials(self, *, keep_launch_options: bool = False) -> None:
        """Erase copied host secrets while retaining this child's launch arguments."""
        self._model_gateway_token = ""
        options = getattr(self, "_model_gateway_options", None)
        if not keep_launch_options and options is not None:
            options.clear()

    def _start_process(self) -> None:
        # The fork hook must retain this child's own launch options while
        # clearing credentials of every other host, including in-flight starts.
        _FORKING_HOST.host = self
        try:
            self.process.start()
        finally:
            del _FORKING_HOST.host

    async def start(
        self,
        message_target_queue=None,
        startup_timeout: float | None = None,
        startup_failure: str = "warn",
    ) -> Any:
        """
        Start background tasks from async contexts.
        
        Args:
            message_target_queue: Main-process queue receiving plugin pushes.
        """
        # Register this plugin's downlink sender so plugin_router can
        # route plugin-to-plugin responses back to the child process.
        state.register_downlink_sender(self.plugin_id, self.comm_manager.send_plugin_response)

        await self.comm_manager.start(message_target_queue=message_target_queue)
        should_wait_for_startup = startup_timeout is not None and startup_timeout > 0
        startup_failure_policy = str(startup_failure or "warn").strip().lower()
        self._startup_options["startup_failure"] = startup_failure_policy

        if self.process.is_alive():
            self.logger.debug(
                "Plugin {} process already running (pid: {})",
                self.plugin_id,
                self.process.pid,
            )
            return

        if should_wait_for_startup:
            await self.comm_manager.prepare_startup_wait()

        start_task: asyncio.Task | None = None
        try:
            from config.network import resolve_user_plugin_base

            self._model_gateway_starting = True
            self._model_gateway_token = model_gateway_access.issue(
                self.plugin_id, self._model_gateway_is_alive,
            )
            self._model_gateway_options.update({
                "base_url": f"{resolve_user_plugin_base()}/api/models/v1",
                "token": self._model_gateway_token,
            })
            _refresh_child_storage_layout_env(self.logger)
            start_task = asyncio.create_task(asyncio.to_thread(self._start_process))
            await asyncio.shield(start_task)
        except asyncio.CancelledError:
            self._revoke_model_gateway_access()
            # Canceling to_thread does not stop spawn or its argument pickling.
            # Keep launch options and transport intact until the worker exits,
            # then stop any child it created. Repeated caller cancellation must
            # not cancel this cleanup or clear arguments underneath the worker.
            cleanup_task = asyncio.create_task(self._finish_cancelled_start(start_task))
            while not cleanup_task.done():
                try:
                    await asyncio.shield(cleanup_task)
                except asyncio.CancelledError:
                    continue
            cleanup_task.result()
            raise
        except BaseException:
            self._revoke_model_gateway_access()
            self.logger.error(
                "Plugin {} process failed to start, shutting down comm_manager",
                self.plugin_id,
            )
            state.remove_downlink_sender(self.plugin_id)
            try:
                self.transport.close()
            except Exception:
                pass
            await self.comm_manager.shutdown(timeout=PLUGIN_SHUTDOWN_TIMEOUT)
            raise
        finally:
            self._model_gateway_starting = False
            # multiprocessing has copied these arguments into the child. Do not
            # leave launch credentials for future forked sibling plugins.
            self._model_gateway_options.clear()
        self.logger.info("Plugin {} process started (pid: {})", self.plugin_id, self.process.pid)

        # Validate process state.
        if not self.process.is_alive():
            self._revoke_model_gateway_access()
            exitcode = self.process.exitcode
            self.logger.error(
                "Plugin {} process is not alive after startup (exitcode: {})",
                self.plugin_id,
                exitcode,
            )
            state.remove_downlink_sender(self.plugin_id)
            try:
                self.transport.close()
            except Exception:
                pass
            await self.comm_manager.shutdown(timeout=PLUGIN_SHUTDOWN_TIMEOUT)
            raise PluginLifecycleError(
                self.plugin_id,
                "startup",
                f"process exited immediately (exitcode={exitcode})",
            )
        else:
            self.logger.info(
                "Plugin {} process is alive and running (pid: {})",
                self.plugin_id,
                self.process.pid,
            )

        if should_wait_for_startup:
            try:
                startup_result = await self.comm_manager.wait_for_startup(
                    timeout=float(startup_timeout),
                    allow_startup_error=startup_failure_policy != "fail",
                )
                if isinstance(startup_result, dict) and startup_result.get("startup_error"):
                    self.logger.warning(
                        "Plugin {} startup completed with warning: {}",
                        self.plugin_id,
                        startup_result["startup_error"],
                    )

                # The child owns the effective configuration it actually
                # loaded.  Record it only after the handshake succeeds and a
                # second liveness check passes. A tolerated startup warning
                # does not change which configuration the child loaded.
                # Timeouts and dead processes must not advance this identity.
                if (
                    isinstance(startup_result, dict)
                    and (
                        startup_result.get("status") == "ready"
                        or (
                            startup_failure_policy != "fail"
                            and startup_result.get("status") == "failed"
                            and startup_result.get("startup_error")
                        )
                    )
                    and self.process.is_alive()
                ):
                    fingerprint = startup_result.get("config_fingerprint")
                    if isinstance(fingerprint, str) and fingerprint:
                        self.applied_config_fingerprint = fingerprint
                        self.applied_config_loaded_at = datetime.now(timezone.utc).isoformat()
                return startup_result
            except asyncio.CancelledError:
                await self._abort_startup_after_failure(timeout=PLUGIN_SHUTDOWN_TIMEOUT)
                raise
            except TimeoutError as exc:
                self.logger.error(
                    "Plugin {} startup timed out after {}s",
                    self.plugin_id,
                    startup_timeout,
                )
                await self._abort_startup_after_failure(timeout=min(float(startup_timeout), PLUGIN_SHUTDOWN_TIMEOUT))
                raise PluginLifecycleError(
                    self.plugin_id,
                    "startup",
                    f"startup timed out after {startup_timeout}s",
                ) from exc
            except Exception as exc:
                self.logger.error(
                    "Plugin {} startup failed: {}",
                    self.plugin_id,
                    exc,
                )
                await self._abort_startup_after_failure(timeout=PLUGIN_SHUTDOWN_TIMEOUT)
                raise PluginLifecycleError(
                    self.plugin_id,
                    "startup",
                    str(exc),
                ) from exc

    async def _finish_cancelled_start(self, start_task: asyncio.Task | None) -> None:
        if start_task is not None:
            try:
                await start_task
            except Exception:
                # The original caller remains cancelled whether spawn finishes
                # successfully or fails. Both paths still require teardown.
                pass
        await self._abort_startup_after_failure(timeout=PLUGIN_SHUTDOWN_TIMEOUT)

    async def _abort_startup_after_failure(self, timeout: float) -> None:
        self._revoke_model_gateway_access()
        try:
            if getattr(self, "_process_stop_event", None) is not None:
                self._process_stop_event.set()
        except Exception:
            pass
        state.remove_downlink_sender(self.plugin_id)
        try:
            await self.comm_manager.send_stop_command()
        except Exception:
            pass
        try:
            await self.comm_manager.shutdown(timeout=timeout)
        except Exception:
            pass
        await asyncio.to_thread(self._shutdown_process, timeout)
        try:
            self.transport.close()
        except Exception:
            pass
    
    async def shutdown(self, timeout: float = PLUGIN_SHUTDOWN_TIMEOUT) -> None:
        """
        Gracefully shut down plugins.
        
        Close sequentially:
        1. Send stop.
        2. Close communication resources.
        3. Close the process.
        """
        self.logger.info(f"Shutting down plugin {self.plugin_id}")
        self._revoke_model_gateway_access()

        # Set out-of-band stop event first so the child can exit promptly.
        try:
            if getattr(self, "_process_stop_event", None) is not None:
                self._process_stop_event.set()
        except Exception:
            pass
        
        # 1. Send stop.
        await self.comm_manager.send_stop_command()

        # 2. Close communication resources.
        await self.comm_manager.shutdown(timeout=timeout)

        # 3. Unregister downlink sender & close ZMQ transport
        state.remove_downlink_sender(self.plugin_id)
        self.transport.close()

        # 4. Close the process.
        success = await asyncio.to_thread(self._shutdown_process, timeout)
        
        if success:
            self.logger.info(f"Plugin {self.plugin_id} shutdown successfully")
        else:
            self.logger.warning(f"Plugin {self.plugin_id} shutdown with issues")
    
    def shutdown_sync(self, timeout: float = PLUGIN_SHUTDOWN_TIMEOUT) -> None:
        """
        Synchronous shutdown for nonasync contexts.
        
        Does not await async tasks; prefer shutdown().
        """
        self._revoke_model_gateway_access()
        try:
            if getattr(self, "_process_stop_event", None) is not None:
                self._process_stop_event.set()
        except Exception:
            pass

        # Best-effort notify the communication manager to stop, even without waiting.
        if getattr(self, "comm_manager", None) is not None:
            try:
                _ev = getattr(self.comm_manager, "_shutdown_event", None)
                if _ev is not None:
                    _ev.set()
            except Exception:
                pass

        state.remove_downlink_sender(self.plugin_id)
        self.transport.close()

        self._shutdown_process(timeout=timeout)
    
    async def trigger(self, entry_id: str, args: dict, timeout: float | None = PLUGIN_TRIGGER_TIMEOUT) -> Any:
        """
        Trigger a plugin entry.
        
        Args:
            entry_id: Entry ID.
            args: Argument dictionary.
            timeout: Timeout.
        
        Returns:
            Plugin result.
        """
        self.logger.debug(
            "[PluginHost] Trigger called: plugin_id={}, entry_id={}",
            self.plugin_id,
            entry_id,
        )
        # Parameter values may contain schema-declared sensitive data. Keep only
        # structural diagnostics here; execution still receives the original args.
        self.logger.debug(
            "[PluginHost] Args: type={}, keys={}",
            type(args),
            list(args.keys()) if isinstance(args, dict) else "N/A",
        )
        # Send TRIGGER and await child results.
        # Delegate to the communication-resource manager.
        return await self.comm_manager.trigger(entry_id, args, timeout)

    async def cancel_run(self, run_id: str) -> None:
        """Propagate a run cancellation to the child process.

        Fire-and-forget: the child process will set the cancel_event for
        the given *run_id*, causing the running entry to be cancelled if it
        supports cancellation (async entries).
        """
        await self.comm_manager.send_cancel_run(run_id)
    
    async def trigger_custom_event(
        self,
        event_type: str,
        event_id: str,
        args: dict,
        timeout: float = PLUGIN_TRIGGER_TIMEOUT
    ) -> Any:
        """
        Trigger custom events.
        
        Args:
            event_type: Custom type, e.g. \"file_change\" / \"user_action\".
            event_id: Event ID
            args: Argument dictionary.
            timeout: Timeout.
        
        Returns:
            Event-handler result.
        
        Raises:
            PluginError: Missing event or execution failure.
        """
        self.logger.info(
            "[PluginHost] Trigger custom event: plugin_id={}, event_type={}, event_id={}",
            self.plugin_id,
            event_type,
            event_id,
        )
        return await self.comm_manager.trigger_custom_event(event_type, event_id, args, timeout)

    async def push_bus_change(self, *, sub_id: str, bus: str, op: str, delta: Dict[str, Any] | None = None) -> None:
        await self.comm_manager.push_bus_change(sub_id=sub_id, bus=bus, op=op, delta=delta)

    async def send_config_update(
        self,
        config: Dict[str, Any],
        mode: str = "temporary",
        profile: str | None = None,
        timeout: float = 10.0
    ) -> Dict[str, Any]:
        """
        Send CONFIG_UPDATE to children for hot updates.
        
        Args:
            config: Complete or partial new configuration.
            mode: "temporary" | "permanent"
            profile: Profile name in permanent mode.
            timeout: Timeout.
        
        Returns:
            {
                "success": bool,
                "config_applied": bool,
                "handler_called": bool,
            }
        """
        req_id = str(uuid.uuid4())
        cmd = {
            "type": "CONFIG_UPDATE",
            "req_id": req_id,
            "config": config,
            "mode": mode,
            "profile": profile,
        }
        return await self.comm_manager._send_command_and_wait(req_id, cmd, timeout, "CONFIG_UPDATE")

    async def get_ui_context(self, context_id: str = "main", timeout: float = 5.0) -> Any:
        return await self.comm_manager.get_ui_context(context_id=context_id, timeout=timeout)

    def is_alive(self) -> bool:
        """Check process liveness."""
        alive = self.process.is_alive() and self.process.exitcode is None
        if not alive and not getattr(self, "_model_gateway_starting", False):
            self._revoke_model_gateway_access()
        return alive

    def _model_gateway_is_alive(self) -> bool:
        # A spawned child can make its startup request just before start()
        # returns and multiprocessing publishes the parent-side process handle.
        return self._model_gateway_starting or (
            self.process.is_alive() and self.process.exitcode is None
        )

    def _revoke_model_gateway_access(self) -> None:
        token = getattr(self, "_model_gateway_token", "")
        self._model_gateway_token = ""
        if token:
            model_gateway_access.revoke(token)
    
    def health_check(self) -> HealthCheckResponse:
        """Perform health checks and return detailed status."""
        from plugin._types.models import HealthCheckResponse

        alive = self.is_alive()
        exitcode = self.process.exitcode
        pid = self.process.pid if self.process.is_alive() else None
        
        if alive:
            status = "running"
        elif exitcode is None:
            status = "not_started"
        elif exitcode == 0:
            status = "stopped"
        else:
            status = "crashed"
        
        return HealthCheckResponse(
            alive=alive,
            exitcode=exitcode,
            pid=pid,
            status=status,
            communication={
                "pending_requests": len(self.comm_manager._pending_futures),
                "consumer_running": (
                    self.comm_manager._uplink_consumer_task is not None
                    and not self.comm_manager._uplink_consumer_task.done()
                ),
            },
        )
    
    async def freeze(self, timeout: float = PLUGIN_TRIGGER_TIMEOUT) -> Dict[str, Any]:
        """
        Freeze plugins by saving state, then stopping processes.
        
        Args:
            timeout: Timeout.
        
        Returns:
            Freeze result with frozen status and freezable_keys.
        """
        self.logger.info(f"[PluginHost] Freezing plugin {self.plugin_id}")
        
        # Send FREEZE and await results.
        result = await self.comm_manager.send_freeze_command(timeout=timeout)
        
        if result.get("success"):
            self._revoke_model_gateway_access()
            await asyncio.to_thread(self._shutdown_process, timeout)
            await self.comm_manager.shutdown(timeout=timeout)
            state.remove_downlink_sender(self.plugin_id)
            self.transport.close()
            self.logger.info(f"[PluginHost] Plugin {self.plugin_id} frozen successfully")
        else:
            self.logger.error(f"[PluginHost] Plugin {self.plugin_id} freeze failed: {result.get('error')}")
        
        return result
    
    def _shutdown_process(self, timeout: float = PROCESS_SHUTDOWN_TIMEOUT) -> bool:
        """
        Gracefully close the process.
        
        Args:
            timeout: Process-exit wait in seconds.
        
        Returns:
            True on successful closure, False on timeout/error.
        """
        self._revoke_model_gateway_access()
        if not self.process.is_alive():
            self.logger.info(f"Plugin {self.plugin_id} process already stopped")
            return True
        
        try:
            # Attempt graceful closure first: process reads STOP from its queue and exits.
            self.process.join(timeout=timeout)
            
            if self.process.is_alive():
                self.logger.warning(
                    f"Plugin {self.plugin_id} didn't stop gracefully within {timeout}s, terminating"
                )
                self.process.terminate()
                self.process.join(timeout=PROCESS_TERMINATE_TIMEOUT)
                
                if self.process.is_alive():
                    self.logger.error(f"Plugin {self.plugin_id} failed to terminate, killing")
                    self.process.kill()
                    self.process.join(timeout=PROCESS_TERMINATE_TIMEOUT)
                    return False
            
            self.logger.info(f"Plugin {self.plugin_id} process shutdown successfully")
            return True
            
        except Exception:
            self.logger.exception("Error while shutting down plugin {}", self.plugin_id)
            return False


# Backwards-compatible alias
PluginProcessHost = PluginHost
