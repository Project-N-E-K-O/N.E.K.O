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
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from types import FrameType
from typing import IO

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import Response

from config import USER_PLUGIN_SERVER_PORT
from plugin.logging_config import configure_default_logger, get_logger

configure_default_logger()
logger = get_logger("server.user_plugin_server")

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

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

server_logger, server_log_config = setup_logging(service_name="PluginServer", log_level="INFO", silent=True)
_ = server_logger


class _LoguruInterceptHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            level: str | int = logger.level(record.levelname).name
        except (ValueError, AttributeError, TypeError):
            level = record.levelno

        logger.opt(exception=record.exc_info).log(level, record.getMessage())


def _configure_uvicorn_logging_bridge() -> None:
    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uv_logger = logging.getLogger(logger_name)
        uv_logger.handlers.clear()
        uv_logger.propagate = True


def _configure_server_log_sink() -> None:
    log_path_obj = server_log_config.get_log_file_path()
    if not isinstance(log_path_obj, str):
        return

    log_path = log_path_obj.strip()
    if not log_path:
        return

    try:
        logger.add(
            log_path,
            rotation="10 MB",
            retention="30 days",
            enqueue=True,
            encoding="utf-8",
        )
    except (TypeError, ValueError, OSError) as exc:
        logger.warning(
            "failed to configure server file sink: path={}, err_type={}, err={}",
            log_path,
            type(exc).__name__,
            str(exc),
        )


def _configure_python_logging_root() -> None:
    logging.root.handlers.clear()
    logging.root.addHandler(_LoguruInterceptHandler())
    logging.root.setLevel(logging.INFO)
    _configure_uvicorn_logging_bridge()


_configure_uvicorn_logging_bridge()
_configure_server_log_sink()
_configure_python_logging_root()

from plugin.server.infrastructure.exceptions import register_exception_handlers  # noqa: E402
from plugin.server.lifecycle import shutdown, startup  # noqa: E402
from plugin.server.routes import (  # noqa: E402
    config_router,
    frontend_router,
    health_router,
    logs_router,
    messages_router,
    metrics_router,
    plugin_ui_router,
    plugins_router,
    runs_router,
    websocket_router,
)
from plugin.server.routes.frontend import mount_static_files  # noqa: E402


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    _ = app

    try:
        faulthandler.register(signal.SIGUSR1, all_threads=True)
    except (RuntimeError, OSError, AttributeError, ValueError) as exc:
        logger.debug(
            "failed to register faulthandler SIGUSR1: err_type={}, err={}",
            type(exc).__name__,
            str(exc),
        )

    stop_event = threading.Event()
    last_heartbeat: dict[str, float] = {"t": time.monotonic()}

    async def _heartbeat() -> None:
        while not stop_event.is_set():
            last_heartbeat["t"] = time.monotonic()
            await asyncio.sleep(0.5)

    def _watchdog() -> None:
        threshold = 8.0
        while not stop_event.is_set():
            now = time.monotonic()
            elapsed = now - last_heartbeat["t"]
            if elapsed > threshold:
                logger.error(
                    "Event loop appears blocked (no heartbeat for {:.1f}s); dumping all thread tracebacks",
                    elapsed,
                )
                try:
                    faulthandler.dump_traceback(all_threads=True)
                except (RuntimeError, OSError, ValueError, AttributeError) as exc:
                    logger.warning(
                        "failed to dump traceback: err_type={}, err={}",
                        type(exc).__name__,
                        str(exc),
                    )
                last_heartbeat["t"] = now
            time.sleep(1.0)

    watchdog_thread = threading.Thread(target=_watchdog, daemon=True, name="event-loop-watchdog")
    watchdog_thread.start()

    heartbeat_task = asyncio.create_task(_heartbeat(), name="server-heartbeat")
    await startup()
    try:
        yield
    finally:
        stop_event.set()
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            logger.debug("heartbeat task cancelled")
        except RuntimeError as exc:
            logger.warning(
                "heartbeat task failed while stopping: err_type={}, err={}",
                type(exc).__name__,
                str(exc),
            )
        await shutdown()


app = FastAPI(title="N.E.K.O User Plugin Server", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:48911",
        "http://127.0.0.1:48911",
    ],
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_exception_handlers(app)
mount_static_files(app)


@app.middleware("http")
async def _frontend_cache_headers(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    response = await call_next(request)
    path = request.url.path

    if path.startswith("/ui/assets/"):
        response.headers.setdefault("Cache-Control", "public, max-age=31536000, immutable")
        return response

    if path in {"/ui", "/ui/"} or (path.startswith("/ui/") and path.endswith(".html")):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response

    return response


app.include_router(health_router)
app.include_router(plugins_router)
app.include_router(runs_router)
app.include_router(messages_router)
app.include_router(metrics_router)
app.include_router(config_router)
app.include_router(logs_router)
app.include_router(frontend_router)
app.include_router(websocket_router)
app.include_router(plugin_ui_router)


def _enable_fault_handler_dump_file() -> IO[str] | None:
    dump_path = Path(__file__).resolve().parent / "log" / "server" / "faulthandler_dump.log"
    try:
        dump_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning(
            "failed to create faulthandler dump directory: path={}, err_type={}, err={}",
            str(dump_path.parent),
            type(exc).__name__,
            str(exc),
        )

    try:
        dump_file = dump_path.open("a", encoding="utf-8")
    except OSError as exc:
        logger.warning(
            "failed to open faulthandler dump file: path={}, err_type={}, err={}",
            str(dump_path),
            type(exc).__name__,
            str(exc),
        )
        return None

    try:
        faulthandler.enable(file=dump_file)
        faulthandler.register(signal.SIGUSR1, all_threads=True, file=dump_file)
        return dump_file
    except (RuntimeError, OSError, AttributeError, ValueError) as exc:
        logger.warning(
            "failed to enable faulthandler dump file: path={}, err_type={}, err={}",
            str(dump_path),
            type(exc).__name__,
            str(exc),
        )
        try:
            dump_file.close()
        except OSError:
            logger.debug("failed to close faulthandler dump file")
        return None


def _enable_fault_handler_fallback() -> None:
    try:
        faulthandler.enable()
        faulthandler.register(signal.SIGUSR1, all_threads=True)
    except (RuntimeError, OSError, AttributeError, ValueError) as exc:
        logger.warning(
            "failed to enable fallback faulthandler: err_type={}, err={}",
            type(exc).__name__,
            str(exc),
        )


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
                logger.debug("failed to close probing socket")
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
        logger.warning(
            "User plugin server port {} is unavailable, switched to {}",
            base_port,
            selected_port,
        )
    else:
        logger.info("User plugin server starting on {}:{}", host, selected_port)

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
        signal.signal(signal.SIGQUIT, _sigint_handler)
    except (ValueError, OSError, RuntimeError) as exc:
        old_sigint = None
        logger.warning(
            "failed to register shutdown signals: err_type={}, err={}",
            type(exc).__name__,
            str(exc),
        )

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
        except (ValueError, OSError, RuntimeError) as exc:
            cleanup_old_sigint = None
            logger.debug(
                "failed to override SIGINT during cleanup: err_type={}, err={}",
                type(exc).__name__,
                str(exc),
            )

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
                        logger.debug("failed to terminate child process: pid={}", child.pid)

                _, alive = psutil.wait_procs(children, timeout=0.5)
                for process in alive:
                    try:
                        process.kill()
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        logger.debug("failed to kill child process: pid={}", process.pid)
            except KeyboardInterrupt:
                logger.debug("cleanup interrupted by keyboard interrupt")
            except (psutil.Error, OSError, RuntimeError, ValueError) as exc:
                logger.warning(
                    "failed to cleanup child processes: err_type={}, err={}",
                    type(exc).__name__,
                    str(exc),
                )
        elif hasattr(os, "killpg"):
            try:
                os.killpg(os.getpgrp(), signal.SIGKILL)
            except OSError as exc:
                logger.debug(
                    "failed to kill process group: err_type={}, err={}",
                    type(exc).__name__,
                    str(exc),
                )

        if force_exit_timer is not None:
            force_exit_timer.cancel()

        if cleanup_old_sigint is not None:
            try:
                signal.signal(signal.SIGINT, cleanup_old_sigint)
            except (ValueError, OSError, RuntimeError) as exc:
                logger.debug(
                    "failed to restore cleanup SIGINT handler: err_type={}, err={}",
                    type(exc).__name__,
                    str(exc),
                )

        if old_sigint is not None:
            try:
                signal.signal(signal.SIGINT, old_sigint)
            except (ValueError, OSError, RuntimeError) as exc:
                logger.debug(
                    "failed to restore original SIGINT handler: err_type={}, err={}",
                    type(exc).__name__,
                    str(exc),
                )

        if dump_file is not None:
            try:
                dump_file.close()
            except OSError:
                logger.debug("failed to close faulthandler dump file during cleanup")
