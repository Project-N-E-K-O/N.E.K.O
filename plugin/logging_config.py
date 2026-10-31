"""Plugin-facing stdlib adapter and explicit host bootstrap.

Host entrypoints install handlers through the shared logging backend. SDK lookup
is side-effect-free. Historical bind/opt/brace calling conventions are adapted
here, so plugins keep one public interface and one persistence implementation.
"""
from __future__ import annotations

import logging
from collections.abc import Mapping
import os
import re
import threading
from enum import Enum
from pathlib import Path
from typing import Any, Optional


# ──────────────────────────────────────────────────────────────────────────
# LogLevel enum preserves legacy SDK APIs, mapping to stdlib integer levels.
# ──────────────────────────────────────────────────────────────────────────

class LogLevel(str, Enum):
    TRACE = "TRACE"
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


_LEVEL_TO_INT = {
    LogLevel.TRACE: logging.DEBUG,  # stdlib lacks TRACE; map to DEBUG.
    LogLevel.DEBUG: logging.DEBUG,
    LogLevel.INFO: logging.INFO,
    LogLevel.WARNING: logging.WARNING,
    LogLevel.ERROR: logging.ERROR,
    LogLevel.CRITICAL: logging.CRITICAL,
}


def _level_to_int(level: Any) -> int:
    if isinstance(level, int):
        return level
    if isinstance(level, LogLevel):
        return _LEVEL_TO_INT[level]
    if isinstance(level, str):
        try:
            return _LEVEL_TO_INT[LogLevel(level.upper())]
        except (KeyError, ValueError):
            mapped = logging.getLevelName(level.upper())
            return mapped if isinstance(mapped, int) else logging.INFO
    return logging.INFO


def _get_log_level() -> LogLevel:
    raw = os.getenv("NEKO_LOG_LEVEL", "INFO").upper()
    try:
        return LogLevel(raw)
    except ValueError:
        return LogLevel.INFO


# ──────────────────────────────────────────────────────────────────────────
# Legacy global constants; new code uses utils.logger_config directly.
# ──────────────────────────────────────────────────────────────────────────

LOG_LEVEL = _get_log_level()
# LOG_DIR is a compatibility stub only; never write there. RobustLoggerConfig
# automatically selects writable Documents/N.E.K.O/logs/.
LOG_DIR = Path(os.getenv("NEKO_LOG_DIR", "log"))
LOG_MAX_SIZE = os.getenv("NEKO_LOG_MAX_SIZE", "10 MB")
LOG_RETENTION = os.getenv("NEKO_LOG_RETENTION", "7 days")
LOG_COMPRESSION = os.getenv("NEKO_LOG_COMPRESSION", "gz")

# Retain legacy loguru format constants for imports, unused by stdlib.
FORMAT_CONSOLE = (
    "{asctime} | {levelname:<8} | {name:<20} | {message}"
)
FORMAT_FILE = FORMAT_CONSOLE
FORMAT_CONSOLE_SIMPLE = "{asctime} | {levelname:<8} | {message}"
FORMAT_FILE_SIMPLE = FORMAT_CONSOLE_SIMPLE


def get_plugin_format_console(plugin_id: str) -> str:
    return f"{{asctime}} | {{levelname:<8}} | [{plugin_id}] {{message}}"


def get_plugin_format_file(plugin_id: str) -> str:
    return get_plugin_format_console(plugin_id)


# ──────────────────────────────────────────────────────────────────────────
# Sensitive-information filtering.
# ──────────────────────────────────────────────────────────────────────────

REDACT_PATTERNS = [
    re.compile(r'(password|passwd|pwd)["\']?\s*[:=]\s*["\']?[^"\'\s]+', re.I),
    re.compile(r'(token|api_key|apikey|secret)["\']?\s*[:=]\s*["\']?[^"\'\s]+', re.I),
    re.compile(r'(authorization|auth)["\']?\s*[:=]\s*["\']?[^"\'\s]+', re.I),
]


def _redact_sensitive(message: str) -> str:
    for pattern in REDACT_PATTERNS:
        message = pattern.sub(r'\1=***REDACTED***', message)
    return message


# Runtime ownership is process-local. SDK lookup stays pure; legacy SDK users
# without a host runner initialize on explicit setup or on their first record.
_runtime_pid: int | None = None
_runtime_service: str | None = None
_runtime_result: Any = None
_runtime_owns_root = False
_runtime_lock = threading.RLock()
_runtime_lock_pid = os.getpid()


def _process_lock():
    global _runtime_lock, _runtime_lock_pid
    if _runtime_lock_pid != os.getpid():
        # A fork may copy a lock held by another parent thread.
        _runtime_lock = threading.RLock()
        _runtime_lock_pid = os.getpid()
    return _runtime_lock


def _bind_process_logging(base_logger: logging.Logger, previous_handlers=()) -> None:
    """Retarget inherited project sinks in a dedicated plugin process.

    Rebind the handlers rather than clearing logger objects: cached Main/Agent
    loggers may deliberately stop propagation and still need a working outlet.
    The containing application's process never calls this operation.
    """
    from config import APP_NAME
    from plugin._logging_runtime import (
        all_loggers as _all_loggers, close_unbound_handlers as _close_unbound_handlers,
        replace_handlers as _replace_handlers,
    )

    handlers = [h for h in base_logger.handlers if getattr(h, "_neko_owned", False)]
    inherited = {h for lg in _all_loggers() for h in lg.handlers
                 if getattr(h, "_neko_owned", False)
                 and getattr(h, "_neko_pid", None) != os.getpid()}
    retired = set(previous_handlers) | inherited
    for target in _all_loggers():
        selected = retired if target.name == APP_NAME or target.name.startswith(APP_NAME + ".") else inherited
        _replace_handlers(target, selected, handlers)
    shared = logging.getLogger(APP_NAME)
    shared.setLevel(base_logger.level)
    shared.propagate = False
    root = logging.getLogger()
    retired.update(h for target in (shared, root) for h in target.handlers
                   if getattr(h, "_neko_owned", False) and h not in handlers)
    for target in (shared, root):
        target.handlers = [h for h in target.handlers if not getattr(h, "_neko_owned", False)] + handlers
    root.setLevel(base_logger.level)
    root._neko_sdk_bridge = None
    _close_unbound_handlers(retired)


def _active_file_handler(service_name: str) -> logging.FileHandler | None:
    """Inspect the currently attached canonical writer, including public reconfiguration."""
    from config import APP_NAME
    from utils.logging_layout import is_service_log_file

    for handler in logging.getLogger(f"{APP_NAME}.{service_name}").handlers:
        if isinstance(handler, logging.FileHandler) and getattr(handler, "_neko_owned", False):
            path = Path(handler.baseFilename)
            if is_service_log_file(path.name, service_name) and "_error.log" not in path.name:
                return handler
    return None


def _active_log_file(service_name: str) -> Path | None:
    handler = _active_file_handler(service_name)
    return Path(handler.baseFilename) if handler is not None else None


def setup_plugin_logging(service_name: str = "PluginServer", *, own_root: bool | None = None,
                         log_level: int = logging.INFO):
    """Initialize an embedded namespace or a dedicated plugin process.

    Official entrypoints retain their historical INFO default. SDK-only scripts
    pass their legacy environment level explicitly. File setup failure degrades
    to stderr, so logging cannot prevent plugin startup; explicit setup retries.
    """
    global _runtime_pid, _runtime_service, _runtime_result, _runtime_owns_root
    from config import APP_NAME
    from utils.logger_config import (EnhancedLogger, RobustLoggerConfig, mark_project_handler, create_log_formatter,
                                     install_httpx_filter, install_message_formatting)
    from plugin._logging_runtime import install_namespace_handlers

    with _process_lock():
        if own_root is None:
            own_root = (_runtime_pid == os.getpid() and _runtime_service == service_name
                        and _runtime_owns_root)
        if (_runtime_pid == os.getpid() and _runtime_service == service_name
                and _runtime_result is not None and _runtime_result[1] is not None
                and _runtime_owns_root == own_root and _runtime_result[0].level == log_level
                and _active_log_file(service_name) == _runtime_result[1].log_file.absolute()):
            os.environ["NEKO_PLUGIN_SERVICE_NAME"] = service_name
            return _runtime_result
        previous_handlers = [h for h in logging.getLogger(f"{APP_NAME}.{service_name}").handlers
                             if getattr(h, "_neko_owned", False)]
        install_message_formatting()
        config = None
        try:
            config = RobustLoggerConfig(service_name=service_name, log_level=log_level,
                                        log_subdir="plugin" if service_name.startswith("Plugin_") else None)
            base_logger = logging.getLogger(f"{APP_NAME}.{service_name}")
            handlers = list(config.iter_handlers(delay=True))
            install_namespace_handlers(base_logger, handlers, log_level)
            if _active_log_file(service_name) != config.log_file.absolute():
                # The backend tolerates file-open failures itself. A console-only
                # result must still be marked degraded and explicitly retryable.
                raise OSError("Primary plugin log file could not be opened")
        except Exception as error:
            config = None
            base_logger = logging.getLogger(f"{APP_NAME}.{service_name}")
            handler = logging.StreamHandler()
            handler.setFormatter(create_log_formatter())
            mark_project_handler(handler)
            install_namespace_handlers(base_logger, [handler], log_level)
            base_logger.warning("Plugin file logging unavailable (%s); using stderr", type(error).__name__)
        if own_root:
            _bind_process_logging(base_logger, previous_handlers)
        else:
            from plugin._logging_runtime import ensure_legacy_root_collection
            root = logging.getLogger()
            if getattr(root, "_neko_sdk_bridge", None) in root.handlers:
                ensure_legacy_root_collection(base_logger)
        _runtime_pid, _runtime_service = os.getpid(), service_name
        _runtime_result = (EnhancedLogger(base_logger), config)
        _runtime_owns_root = own_root
        os.environ["NEKO_PLUGIN_SERVICE_NAME"] = service_name
        install_httpx_filter()
        return _runtime_result


def _ensure_sdk_logging() -> None:
    """Initialize legacy SDK persistence and its compatibility collection."""
    # The established runtime is immutable until explicit reconfiguration. Keep
    # the per-record path lock-free; a fork changes PID and takes the slow path.
    if _runtime_pid == os.getpid() and _runtime_result is not None:
        return
    from config import APP_NAME

    with _process_lock():
        if _runtime_pid == os.getpid() and _runtime_result is not None:
            return
        from plugin._logging_runtime import all_loggers, ensure_legacy_root_collection

        service = os.getenv("NEKO_PLUGIN_SERVICE_NAME") or "Plugin"
        namespace = logging.getLogger(f"{APP_NAME}.{service}")
        inherited = any(getattr(h, "_neko_owned", False)
                        and getattr(h, "_neko_pid", None) != os.getpid()
                        for lg in all_loggers() for h in lg.handlers)
        if namespace.handlers and not inherited:
            return  # Respect a logger explicitly configured by the containing app.
        result = setup_plugin_logging(service, own_root=inherited, log_level=_level_to_int(LOG_LEVEL))
        ensure_legacy_root_collection(result[0])


def ensure_plugin_log_collection():
    """Preserve historical third-party file collection at plugin app startup."""
    from plugin._logging_runtime import ensure_legacy_root_collection

    if _runtime_pid == os.getpid() and _runtime_result is not None:
        ensure_legacy_root_collection(_runtime_result[0])


def resolve_plugin_log_file(plugin_id: str) -> Path:
    """Inspect the attached writer; only cold paths infer a daily filename."""
    from plugin.logging_layout import plugin_log_service_name
    from utils.logging_layout import log_filename

    service = plugin_log_service_name(plugin_id)
    return _active_log_file(service) or (resolve_plugin_log_directory(log_subdir="plugin") / log_filename(service))


def resolve_plugin_log_directory(*, log_subdir: str | None = None) -> Path:
    """Use the actual attached writer; cold or degraded inspection stays pure."""
    from utils.logging_layout import log_subdirectory, resolve_log_directory

    service = _service_name()
    handler = _active_file_handler(service)
    if handler is not None:
        directory = Path(handler.baseFilename).parent
        own_directory = log_subdir == "plugin" if service.startswith("Plugin_") else log_subdir is None
        if own_directory:
            return directory
        base = getattr(handler, "_neko_log_root", None)
        if base is not None:
            return Path(base) / log_subdirectory(log_subdir)
    return resolve_log_directory(log_subdir=log_subdir)


def _service_name() -> str:
    if _runtime_pid == os.getpid() and _runtime_service:
        return _runtime_service
    return os.getenv("NEKO_PLUGIN_SERVICE_NAME") or "Plugin"


# ──────────────────────────────────────────────────────────────────────────
#  PluginLoggerAdapter
#  -------------------------------------------------------------------------
# Preserve 1000+ legacy loguru-style plugin calls:
#    .info / .warning / .error / .debug / .critical / .exception
#   info("msg {}",x) -> the shared backend's formatting policy.
#   info("msg",key=value) -> stdlib extra.
#   bind(plugin_id=...) -> new adapter with extra.
#   opt(exception=True) -> temporary next-log exc_info wrapper.
#    .add / .remove / .configure → no-op
#   level(name) -> compatible LoguruLevel.
#   success/trace -> INFO/DEBUG.
#  -------------------------------------------------------------------------
# Compatibility only; new code must use stdlib styles:
#       logger.info(f"foo {x}")
#       logger.warning("bar", extra={"plugin_id": pid})
# ──────────────────────────────────────────────────────────────────────────

_LOGGER_KWARGS = {"exc_info", "stack_info", "stacklevel"}
_LOGURU_KW_BLACKLIST = {"colors", "lazy", "raw", "capture", "depth"}


class PluginLoggerAdapter:
    """Public logger adapter.

    Adapters created before host bootstrap resolve the explicit current service
    when used, so module-level SDK loggers follow child-process initialization.
    """

    __slots__ = ("_component", "_extra", "_opt_exc_info")

    def __init__(
        self,
        component: str,
        extra: Optional[dict] = None,
        opt_exc_info: Any = None,
    ):
        self._component = component
        self._extra: dict = dict(extra) if extra else {}
        self._opt_exc_info = opt_exc_info  # Set only by opt(exception=...), effective once.

    # -- Lazy underlying stdlib logger resolution --
    def _resolve_logger(self) -> logging.Logger:
        try:
            from config import APP_NAME as _app_name
        except Exception:
            _app_name = "N.E.K.O"
        service_name = _service_name()
        return logging.getLogger(f"{_app_name}.{service_name}.{self._component}")

    # -- Internal dispatch --
    def _log(self, level: int, msg: Any, args: tuple, kwargs: dict) -> None:
        _ensure_sdk_logging()
        resolved_logger = self._resolve_logger()
        if not resolved_logger.isEnabledFor(level):
            self._opt_exc_info = None
            return
        # Leave standard percent records intact for filters and handler error
        # handling. Only legacy brace calls need adapter-side interpolation.
        if args and isinstance(msg, str) and "{" in msg and "}" in msg:
            legacy_braces = "%" not in msg
            if not legacy_braces:
                percent_args = args[0] if len(args) == 1 and isinstance(args[0], Mapping) and args[0] else args
                try:
                    msg % percent_args
                except (TypeError, ValueError):
                    legacy_braces = True
                except Exception:
                    pass  # Keep conversion failures inside standard handlers.
            if legacy_braces:
                from utils.logger_config import format_log_message

                try:
                    msg, args = format_log_message(msg, args), ()
                except Exception:
                    pass  # Standard handlers own formatting failures.

        std_kwargs: dict[str, Any] = {}
        for key in list(kwargs):
            if key in _LOGGER_KWARGS:
                std_kwargs[key] = kwargs.pop(key)
            elif key in _LOGURU_KW_BLACKLIST:
                kwargs.pop(key)  # Silently discard loguru-only keywords.

        # Attach one-shot opt(exception=...).
        if self._opt_exc_info is not None and "exc_info" not in std_kwargs:
            std_kwargs["exc_info"] = self._opt_exc_info
            self._opt_exc_info = None

        # Merge bound/call extras, supporting both extra={...} and logger.info("msg",k=v).
        merged_extra = dict(self._extra)
        user_extra = kwargs.pop("extra", None)
        if isinstance(user_extra, dict):
            merged_extra.update(user_extra)
        if kwargs:
            # Remaining kwargs become loguru-style extra fields.
            merged_extra.update(kwargs)

        if merged_extra:
            std_kwargs["extra"] = merged_extra

        # Skip adapter frames for caller filename/lineno.
        std_kwargs.setdefault("stacklevel", 2)

        resolved_logger.log(level, msg, *args, **std_kwargs)

    # -- Standard methods --
    def trace(self, msg: Any, *args, **kwargs) -> None:
        self._log(logging.DEBUG, msg, args, kwargs)

    def debug(self, msg: Any, *args, **kwargs) -> None:
        self._log(logging.DEBUG, msg, args, kwargs)

    def info(self, msg: Any, *args, **kwargs) -> None:
        self._log(logging.INFO, msg, args, kwargs)

    def success(self, msg: Any, *args, **kwargs) -> None:
        self._log(logging.INFO, msg, args, kwargs)

    def warning(self, msg: Any, *args, **kwargs) -> None:
        self._log(logging.WARNING, msg, args, kwargs)

    warn = warning

    def error(self, msg: Any, *args, **kwargs) -> None:
        self._log(logging.ERROR, msg, args, kwargs)

    def critical(self, msg: Any, *args, **kwargs) -> None:
        self._log(logging.CRITICAL, msg, args, kwargs)

    def exception(self, msg: Any, *args, **kwargs) -> None:
        kwargs.setdefault("exc_info", True)
        self._log(logging.ERROR, msg, args, kwargs)

    def log(self, level: Any, msg: Any, *args, **kwargs) -> None:
        self._log(_level_to_int(level), msg, args, kwargs)

    # -- Loguru compatibility --
    def bind(self, **extra: Any) -> "PluginLoggerAdapter":
        merged = dict(self._extra)
        merged.update(extra)
        return PluginLoggerAdapter(self._component, extra=merged)

    def opt(self, *, exception: Any = None, depth: int = 0, lazy: bool = False,
            colors: bool = False, raw: bool = False, capture: bool = True) -> "PluginLoggerAdapter":
        # depth/lazy/colors/raw/capture have no stdlib effect; retain compatible signatures.
        # exception=True enables next-log exc_info; exception tuples pass through directly.
        return PluginLoggerAdapter(
            self._component,
            extra=self._extra,
            opt_exc_info=True if exception is True else exception if exception else None,
        )

    def add(self, *args, **kwargs) -> int:  # noqa: ARG002 — Loguru API compatibility.
        # Legacy logger.add sinks migrated to stdlib handlers;
        # this is a no-op returning a fake loguru-style sink ID.
        return 0

    def remove(self, *args, **kwargs) -> None:  # noqa: ARG002 — Loguru API compatibility.
        return None

    def configure(self, *args, **kwargs) -> None:  # noqa: ARG002 — Loguru API compatibility.
        return None

    def level(self, name: str) -> Any:
        no = _level_to_int(name)
        return type("Level", (), {"name": name, "no": no})

    def patch(self, *args, **kwargs) -> "PluginLoggerAdapter":  # noqa: ARG002
        return self

    def contextualize(self, *args, **kwargs):  # noqa: ARG002
        # Return no-op context managers.
        from contextlib import nullcontext
        return nullcontext()

    @property
    def extra(self) -> dict:
        return dict(self._extra)


# ──────────────────────────────────────────────────────────────────────────
# Public API.
# ──────────────────────────────────────────────────────────────────────────

def setup_logging(
    component: str = "main",  # noqa: ARG001 — Legacy signature; main logging owns persistence, components only name namespaces.
    level: Optional[Any] = None,  # noqa: ARG001 — Same rationale.
    force: bool = False,  # noqa: ARG001 — Same rationale.
) -> None:
    """Compatibility entrypoint; host startup owns persistence configuration.

    Component/force historically did not select independent sinks. Retain that
    contract without allowing SDK calls to take ownership of host handlers.
    """
    _ensure_sdk_logging()


def get_logger(component: str) -> PluginLoggerAdapter:
    """Get component-namespaced loggers.

    Resolve the current explicit host identity when used, so module-level
    adapters follow later child bootstrap without freezing the parent namespace.
    """
    safe = component.strip(".") or "plugin"
    return PluginLoggerAdapter(safe)


def configure_default_logger(level: str = "INFO") -> None:
    """Legacy explicit initialization; the level argument remains ignored."""
    _ensure_sdk_logging()


def intercept_standard_logging() -> None:
    """Compatibility no-op.

    Main logging already uses stdlib and needs no interception.
    """
    return None


def format_log_text(value: Any, max_len: Optional[int] = None, wrap: Optional[int] = None) -> str:
    """Format log text with truncation/wrapping.

    Args:
        value: Value to format.
        max_len: Maximum length from NEKO_PLUGIN_LOG_CONTENT_MAX, default 200.
        wrap: Width from NEKO_PLUGIN_LOG_WRAP, default 0 for no wrapping.
    """
    s = "" if value is None else str(value)

    if max_len is None:
        try:
            max_len = int(os.getenv("NEKO_PLUGIN_LOG_CONTENT_MAX", "200"))
        except (ValueError, TypeError):
            max_len = 200
    if max_len <= 0:
        max_len = 200

    truncated = False
    if len(s) > max_len:
        s = s[:max_len]
        truncated = True

    if wrap is None:
        try:
            wrap = int(os.getenv("NEKO_PLUGIN_LOG_WRAP", "0"))
        except (ValueError, TypeError):
            wrap = 0

    if wrap and wrap > 0:
        s = "\n".join(s[i:i + wrap] for i in range(0, len(s), wrap))

    if truncated:
        s = s + "...(truncated)"

    return s


_INFO_ONCE_KEYS: set[tuple[str, ...]] = set()
_INFO_ONCE_LOCK = threading.Lock()


def info_once(logger: PluginLoggerAdapter, key: tuple[str, ...], msg: Any, *args: Any) -> None:
    """Log at INFO the first time ``key`` is seen in this process, DEBUG afterwards.

    For notices that every plugin rescan (startup, each Market install or
    upgrade) would otherwise repeat verbatim: the first occurrence stays
    visible, the repeats remain available at DEBUG.
    """
    with _INFO_ONCE_LOCK:
        first = key not in _INFO_ONCE_KEYS
        if first:
            _INFO_ONCE_KEYS.add(key)
    if first:
        logger.info(msg, *args)
    else:
        logger.debug(msg, *args)


# Module default logger supports from plugin.logging_config import logger.
logger: PluginLoggerAdapter = get_logger("plugin")


# ──────────────────────────────────────────────────────────────────────────
# Exports.
# ──────────────────────────────────────────────────────────────────────────

__all__ = [
    "LogLevel",
    "LOG_LEVEL",
    "LOG_DIR",
    "LOG_MAX_SIZE",
    "LOG_RETENTION",
    "LOG_COMPRESSION",
    "logger",
    "get_logger",
    "setup_logging",
    "configure_default_logger",
    "intercept_standard_logging",
    "format_log_text",
    "info_once",
    "PluginLoggerAdapter",
    # Legacy format constants for compatibility imports, unused by stdlib.
    "FORMAT_CONSOLE",
    "FORMAT_FILE",
    "FORMAT_CONSOLE_SIMPLE",
    "FORMAT_FILE_SIMPLE",
    "get_plugin_format_console",
    "get_plugin_format_file",
]
