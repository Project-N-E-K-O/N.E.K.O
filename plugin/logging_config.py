"""Plugin-facing stdlib adapter and explicit host bootstrap.

Host entrypoints install handlers through the shared logging backend. SDK lookup
is side-effect-free. Historical bind/opt/brace calling conventions are adapted
here, so plugins keep one public interface and one persistence implementation.
"""
from __future__ import annotations

import logging
import os
import re
import threading
from enum import Enum
from pathlib import Path
from typing import Any, Optional


# ──────────────────────────────────────────────────────────────────────────
# Brace compatibility enables loguru-style logger.info("msg {}",x) in stdlib.
# Moved from plugin/user_plugin_server.py to share across processes.
# ──────────────────────────────────────────────────────────────────────────

def _install_logging_brace_compat() -> None:
    """Patch stdlib LogRecord.getMessage to support brace formatting without errors.

    Default percent formatting raises TypeError for logger.info("msg {}",x);
    fall back to str.format(*args), supporting both styles.
    """
    if getattr(logging, "_neko_brace_compat_installed", False):
        return

    original_get_message = logging.LogRecord.getMessage

    def _compat_get_message(self: logging.LogRecord) -> str:
        try:
            return original_get_message(self)
        except TypeError:
            msg = str(self.msg)
            args = self.args
            if not args or "%" in msg or "{" not in msg or "}" not in msg:
                raise
            try:
                if isinstance(args, dict):
                    return msg.format(**args)
                if not isinstance(args, tuple):
                    args = (args,)
                return msg.format(*args)
            except Exception:
                return f"{msg} | args={self.args!r}"

    setattr(logging.LogRecord, "getMessage", _compat_get_message)
    setattr(logging, "_neko_brace_compat_installed", True)




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
FORMAT_FILE = (
    "{asctime} | {levelname:<8} | {name:<20} | {message}"
)
FORMAT_CONSOLE_SIMPLE = "{asctime} | {levelname:<8} | {message}"
FORMAT_FILE_SIMPLE = "{asctime} | {levelname:<8} | {message}"


def get_plugin_format_console(plugin_id: str) -> str:
    return f"{{asctime}} | {{levelname:<8}} | [{plugin_id}] {{message}}"


def get_plugin_format_file(plugin_id: str) -> str:
    return f"{{asctime}} | {{levelname:<8}} | [{plugin_id}] {{message}}"


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


# Explicit bootstrap belongs to process/service entrypoints. Merely importing
# the SDK or requesting an adapter never creates files or installs handlers.
_runtime_pid: int | None = None
_runtime_service: str | None = None
_runtime_result: Any = None
_runtime_owns_root = False


def setup_plugin_logging(service_name: str = "PluginServer", *, own_root: bool = False):
    """Install plugin-owned sinks once for this process and service.

    Embedded services own only their namespace. Dedicated servers and plugin
    subprocesses also capture ordinary stdlib records through root. A PID check
    makes an inherited fork state unusable until the child's explicit bootstrap.
    """
    global _runtime_pid, _runtime_service, _runtime_result, _runtime_owns_root
    from utils.logger_config import install_httpx_filter, setup_logging as bootstrap

    if (_runtime_pid == os.getpid() and _runtime_service == service_name
            and _runtime_result is not None and _runtime_owns_root == own_root
            and any(getattr(h, "_neko_owned", False)
                    for h in _runtime_result[0].handlers)):
        return _runtime_result
    _install_logging_brace_compat()
    result = bootstrap(
        service_name=service_name, log_level=_level_to_int(LOG_LEVEL), silent=True,
        log_subdir="plugin" if service_name.startswith("Plugin_") else None,
        capture_root=own_root, shared_parent=own_root,
    )
    _runtime_pid, _runtime_service, _runtime_result = os.getpid(), service_name, result
    _runtime_owns_root = own_root
    # Preserve the public environment convention for external SDK consumers;
    # internal routing uses the explicit runtime identity above.
    os.environ["NEKO_PLUGIN_SERVICE_NAME"] = service_name
    install_httpx_filter()
    return result


def _service_name() -> str:
    if _runtime_pid == os.getpid() and _runtime_service:
        return _runtime_service
    return os.getenv("NEKO_PLUGIN_SERVICE_NAME") or "PluginServer"


# ──────────────────────────────────────────────────────────────────────────
#  PluginLoggerAdapter
#  -------------------------------------------------------------------------
# Preserve 1000+ legacy loguru-style plugin calls:
#    .info / .warning / .error / .debug / .critical / .exception
#   info("msg {}",x) -> internal format.
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


def _format_brace_msg(msg: Any, args: tuple) -> tuple[str, tuple]:
    """If msg contains braces and args exist, try format(*args); otherwise return unchanged."""
    if not args:
        return msg, args
    text = msg if isinstance(msg, str) else str(msg)
    if "{" not in text or "}" not in text:
        return msg, args
    # Prefer stdlib percent formatting, falling back to format on failure.
    try:
        text % args
        return msg, args
    except (TypeError, ValueError):
        pass
    try:
        formatted = text.format(*args)
        return formatted, ()
    except Exception:
        return msg, args


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
        msg, args = _format_brace_msg(msg, args)

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

        self._resolve_logger().log(level, msg, *args, **std_kwargs)

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
    if level is not None:
        get_logger(component)._resolve_logger().setLevel(_level_to_int(level))


def get_logger(component: str) -> PluginLoggerAdapter:
    """Get component-namespaced loggers.

    Resolve the current explicit host identity when used, so module-level
    adapters follow later child bootstrap without freezing the parent namespace.
    """
    safe = component.strip(".") or "plugin"
    return PluginLoggerAdapter(safe)


def configure_default_logger(level: str = "INFO") -> None:
    """Compatibility no-op.

    Main RobustLoggerConfig already sets up logging at entrypoints such as user_plugin_server;
    retain signatures for legacy imports without configuring sinks again.
    """
    return None


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
