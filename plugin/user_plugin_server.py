"""
User Plugin Server

HTTP 服务器主入口文件。
"""
from __future__ import annotations

import asyncio
import faulthandler
import logging
import os
import signal
import socket
import sys
import threading
from pathlib import Path
from types import FrameType
from typing import Callable, IO

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_PLUGIN_PACKAGE_ROOT = Path(__file__).resolve().parent


def _prepend_sys_path(path: Path, index: int) -> None:
    value = str(path)
    try:
        while value in sys.path:
            sys.path.remove(value)
    except Exception:
        pass
    sys.path.insert(index, value)


# Keep import resolution deterministic even when launcher/sitecustomize preloads paths.
_prepend_sys_path(_PROJECT_ROOT, 0)
_prepend_sys_path(_PLUGIN_PACKAGE_ROOT, 1)


def _parse_tcp_endpoint(endpoint: str) -> tuple[str, int] | None:
    if not isinstance(endpoint, str) or not endpoint.startswith("tcp://"):
        return None
    host_port = endpoint[6:]
    if ":" not in host_port:
        return None
    host, port_text = host_port.rsplit(":", 1)
    if not host:
        return None
    try:
        port = int(port_text)
    except (TypeError, ValueError):
        return None
    if port <= 0 or port > 65535:
        return None
    return host, port


def _is_tcp_port_available(host: str, port: int) -> bool:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        try:
            probe.close()
        except OSError:
            pass


def _find_next_available_port(host: str, start_port: int, max_tries: int = 50) -> int | None:
    for port in range(start_port, start_port + max_tries):
        if _is_tcp_port_available(host, port):
            return port
    return None


def _ensure_plugin_zmq_endpoint_available() -> None:
    endpoint = os.getenv("NEKO_PLUGIN_ZMQ_IPC_ENDPOINT", "tcp://127.0.0.1:38765")
    parsed = _parse_tcp_endpoint(endpoint)
    if parsed is None:
        return
    host, base_port = parsed
    if _is_tcp_port_available(host, base_port):
        return

    fallback_port = _find_next_available_port(host, base_port + 1, max_tries=100)
    if fallback_port is None:
        return

    fallback_endpoint = f"tcp://{host}:{fallback_port}"
    os.environ["NEKO_PLUGIN_ZMQ_IPC_ENDPOINT"] = fallback_endpoint
    try:
        print(
            (
                "[user_plugin_server] NEKO_PLUGIN_ZMQ_IPC_ENDPOINT occupied, "
                f"fallback to {fallback_endpoint}"
            ),
            file=sys.stderr,
        )
    except (OSError, ValueError, RuntimeError):
        pass


_ensure_plugin_zmq_endpoint_available()

from config import USER_PLUGIN_SERVER_PORT

# -- brace-format compat for third-party libs that mix {} and % --
def _install_logging_brace_compat() -> None:
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


_install_logging_brace_compat()

# -- Unified stdlib logger, same as agent_server / memory_server --
try:
    from utils.logger_config import setup_logging
except ModuleNotFoundError:
    import importlib.util

    _logger_config_path = _PROJECT_ROOT / "utils" / "logger_config.py"
    _spec = importlib.util.spec_from_file_location("utils.logger_config", _logger_config_path)
    if _spec is None or _spec.loader is None:
        raise ModuleNotFoundError(f"failed to load logger config from {_logger_config_path}")

    _module = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_module)
    setup_logging = getattr(_module, "setup_logging")

logger, _log_config = setup_logging(service_name="PluginServer", log_level=logging.INFO)

# -- Keep loguru console handler alive for plugin SDK internals --
# Plugin SDK components (plugin/server/*, plugin/core/*) use loguru via
# plugin.logging_config.get_logger().  configure_default_logger() gives them
# a console sink so their output isn't silently lost.  We additionally bridge
# loguru -> stdlib so those logs also land in the PluginServer log file.
from plugin.logging_config import configure_default_logger  # noqa: E402
configure_default_logger()

try:
    from loguru import logger as _loguru_logger

    class _LoguruToStdlib(logging.Handler):
        """Forward loguru records into the stdlib PluginServer file handler."""
        def emit(self, record: logging.LogRecord) -> None:
            pass  # unused; we add loguru sink below

    def _loguru_sink(message) -> None:
        record = message.record
        lvl_name = record["level"].name
        stdlib_lvl = getattr(logging, lvl_name, logging.INFO)
        component = record["extra"].get("component", "plugin")
        logger._logger.log(stdlib_lvl, "[%s] %s", component, record["message"])

    _loguru_logger.add(_loguru_sink, level="INFO", format="{message}")
except Exception:
    pass

# -- uvicorn logging bridge --
def _configure_uvicorn_logging_bridge() -> None:
    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uv_logger = logging.getLogger(logger_name)
        uv_logger.handlers.clear()
        uv_logger.propagate = True


_configure_uvicorn_logging_bridge()

# Must run before any event loop gets created on Windows.
def _configure_windows_event_loop_policy() -> None:
    if sys.platform != "win32":
        return
    policy_cls = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
    if policy_cls is None:
        return
    try:
        asyncio.set_event_loop_policy(policy_cls())
    except (RuntimeError, ValueError, TypeError, AttributeError):
        try:
            print("[user_plugin_server] failed to set WindowsSelectorEventLoopPolicy", file=sys.stderr)
        except (OSError, RuntimeError, ValueError):
            pass


def _disable_windows_plugin_zmq_when_tornado_missing() -> None:
    if sys.platform != "win32":
        return
    try:
        import tornado  # type: ignore  # noqa: F401
        return
    except Exception:
        pass
    os.environ["NEKO_PLUGIN_ZMQ_IPC_ENABLED"] = "false"
    try:
        print(
            "[user_plugin_server] tornado not found on Windows; disable plugin ZeroMQ IPC",
            file=sys.stderr,
        )
    except (OSError, RuntimeError, ValueError):
        pass


_configure_windows_event_loop_policy()
_disable_windows_plugin_zmq_when_tornado_missing()

from plugin.server.http_app import build_plugin_server_app  # noqa: E402


app = build_plugin_server_app()


def _can_register_faulthandler_signal() -> bool:
    return hasattr(faulthandler, "register") and hasattr(signal, "SIGUSR1")


def _enable_fault_handler_dump_file() -> IO[str] | None:
    dump_path = Path(__file__).resolve().parent / "log" / "server" / "faulthandler_dump.log"
    try:
        dump_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning("failed to create faulthandler dump directory: %s (%s)", dump_path.parent, exc)

    try:
        dump_file = dump_path.open("a", encoding="utf-8")
    except OSError as exc:
        logger.warning("failed to open faulthandler dump file: %s (%s)", dump_path, exc)
        return None

    try:
        faulthandler.enable(file=dump_file)
        if _can_register_faulthandler_signal():
            faulthandler.register(signal.SIGUSR1, all_threads=True, file=dump_file)
        return dump_file
    except (RuntimeError, OSError, AttributeError, ValueError) as exc:
        logger.warning("failed to enable faulthandler dump file: %s (%s)", dump_path, exc)
        try:
            dump_file.close()
        except OSError:
            pass
        return None


def _enable_fault_handler_fallback() -> None:
    try:
        faulthandler.enable()
        if _can_register_faulthandler_signal():
            faulthandler.register(signal.SIGUSR1, all_threads=True)
    except (RuntimeError, OSError, AttributeError, ValueError) as exc:
        logger.warning("failed to enable fallback faulthandler: %s", exc)


def _find_available_port(host: str, start_port: int, max_tries: int = 50) -> int:
    for port in range(start_port, start_port + max_tries):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((host, port))
            return port
        except OSError:
            continue
        finally:
            try:
                sock.close()
            except OSError:
                pass
    return start_port


if __name__ == "__main__":
    import uvicorn

    host = "127.0.0.1"
    base_port = int(os.getenv("NEKO_USER_PLUGIN_SERVER_PORT", str(USER_PLUGIN_SERVER_PORT)))

    dump_file = _enable_fault_handler_dump_file()
    if dump_file is None:
        _enable_fault_handler_fallback()

    selected_port = _find_available_port(host, base_port)
    os.environ["NEKO_USER_PLUGIN_SERVER_PORT"] = str(selected_port)
    if selected_port != base_port:
        logger.warning("User plugin server port %s is unavailable, switched to %s", base_port, selected_port)
    else:
        logger.info("User plugin server starting on %s:%s", host, selected_port)

    sigint_count = 0
    sigint_lock = threading.Lock()
    force_exit_timer: threading.Timer | None = None

    config = uvicorn.Config(
        app,
        host=host,
        port=selected_port,
        log_config=None,
        backlog=4096,
        timeout_keep_alive=30,
    )
    server = uvicorn.Server(config)

    def _start_force_exit_watchdog(timeout_s: float) -> None:
        global force_exit_timer
        if force_exit_timer is not None:
            return

        def _kill() -> None:
            os._exit(130)

        timer = threading.Timer(float(timeout_s), _kill)
        timer.daemon = True
        timer.start()

        force_exit_timer = timer

    def _sigint_handler(_signum: int, _frame: FrameType | None) -> None:
        global sigint_count
        with sigint_lock:
            sigint_count += 1
            current_count = sigint_count

        if current_count >= 2:
            os._exit(130)

        server.should_exit = True
        server.force_exit = True
        _start_force_exit_watchdog(timeout_s=2.0)

    old_sigint: int | Callable[[int, FrameType | None], object] | None = None
    try:
        old_sigint = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, _sigint_handler)
        signal.signal(signal.SIGTERM, _sigint_handler)
        if hasattr(signal, "SIGQUIT"):
            signal.signal(signal.SIGQUIT, _sigint_handler)
    except (ValueError, OSError, RuntimeError) as exc:
        old_sigint = None
        logger.warning("failed to register shutdown signals: %s", exc)

    server.install_signal_handlers = lambda: None

    cleanup_old_sigint: int | Callable[[int, FrameType | None], object] | None = None
    try:
        server.run()
    finally:
        try:
            cleanup_old_sigint = signal.getsignal(signal.SIGINT)

            def _force_quit(_signum: int, _frame: FrameType | None) -> None:
                os._exit(130)

            signal.signal(signal.SIGINT, _force_quit)
        except (ValueError, OSError, RuntimeError):
            cleanup_old_sigint = None

        try:
            import psutil
        except ImportError:
            psutil = None

        if psutil is not None:
            try:
                parent = psutil.Process(os.getpid())
                children = parent.children(recursive=True)
                for child in children:
                    try:
                        child.terminate()
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass

                _, alive = psutil.wait_procs(children, timeout=0.5)
                for process in alive:
                    try:
                        process.kill()
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
            except KeyboardInterrupt:
                pass
            except (psutil.Error, OSError, RuntimeError, ValueError) as exc:
                logger.warning("failed to cleanup child processes: %s", exc)
        elif hasattr(os, "killpg"):
            try:
                os.killpg(os.getpgrp(), signal.SIGKILL)
            except OSError:
                pass

        if force_exit_timer is not None:
            force_exit_timer.cancel()

        if cleanup_old_sigint is not None:
            try:
                signal.signal(signal.SIGINT, cleanup_old_sigint)
            except (ValueError, OSError, RuntimeError):
                pass

        if old_sigint is not None:
            try:
                signal.signal(signal.SIGINT, old_sigint)
            except (ValueError, OSError, RuntimeError):
                pass

        if dump_file is not None:
            try:
                dump_file.close()
            except OSError:
                pass
