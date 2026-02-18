"""
User Plugin Server

HTTP 服务器主入口文件。
"""
from __future__ import annotations

from contextlib import asynccontextmanager

import asyncio
import logging
import os
import sys
from pathlib import Path

from loguru import logger as logger
from plugin.logging_config import configure_default_logger

# 配置默认 logger 格式（统一所有模块的日志格式）
configure_default_logger()

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from config import USER_PLUGIN_SERVER_PORT

try:
    from utils.logger_config import setup_logging
except ModuleNotFoundError:
    import importlib.util

    _logger_config_path = _PROJECT_ROOT / "utils" / "logger_config.py"
    _spec = importlib.util.spec_from_file_location("utils.logger_config", _logger_config_path)
    if _spec is None or _spec.loader is None:
        raise
    _mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    setup_logging = getattr(_mod, "setup_logging")
server_logger, server_log_config = setup_logging(service_name="PluginServer", log_level="INFO", silent=True)

try:
    for _ln in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        _lg = logging.getLogger(_ln)
        try:
            _lg.handlers.clear()
        except Exception:
            pass
        _lg.propagate = True
except Exception:
    pass

try:
    _server_log_path = server_log_config.get_log_file_path()
    if isinstance(_server_log_path, str) and _server_log_path:
        logger.add(
            _server_log_path,
            rotation="10 MB",
            retention="30 days",
            enqueue=True,
            encoding="utf-8",
        )
except Exception:
    pass


class _LoguruInterceptHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = logger.level(record.levelname).name
        except Exception:
            level = record.levelno

        logger.opt(exception=record.exc_info).log(level, record.getMessage())


try:
    logging.root.handlers.clear()
    logging.root.addHandler(_LoguruInterceptHandler())
    logging.root.setLevel(logging.INFO)
    for _ln in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        _lg = logging.getLogger(_ln)
        _lg.handlers.clear()
        _lg.propagate = True
except Exception:
    pass

from plugin.server.infrastructure.exceptions import register_exception_handlers
from plugin.server.lifecycle import startup, shutdown
from plugin.server.routes import (
    health_router,
    plugins_router,
    runs_router,
    messages_router,
    metrics_router,
    config_router,
    logs_router,
    frontend_router,
    websocket_router,
    plugin_ui_router,
)
from plugin.server.routes.frontend import mount_static_files


@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio
    import faulthandler
    import signal
    import threading
    import time

    try:
        faulthandler.register(signal.SIGUSR1, all_threads=True)
    except Exception:
        pass

    stop_event = threading.Event()
    last_heartbeat = {"t": time.monotonic()}

    async def _heartbeat():
        while not stop_event.is_set():
            last_heartbeat["t"] = time.monotonic()
            await asyncio.sleep(0.5)

    def _watchdog():
        threshold = 8.0
        while not stop_event.is_set():
            now = time.monotonic()
            dt = now - last_heartbeat["t"]
            if dt > threshold:
                try:
                    logger.error(
                        "Event loop appears blocked (no heartbeat for {:.1f}s); dumping all thread tracebacks",
                        dt,
                    )
                except Exception:
                    pass
                try:
                    faulthandler.dump_traceback(all_threads=True)
                except Exception:
                    pass
                last_heartbeat["t"] = now
            time.sleep(1.0)

    watchdog_thread = threading.Thread(target=_watchdog, daemon=True, name="event-loop-watchdog")
    watchdog_thread.start()

    heartbeat_task = asyncio.create_task(_heartbeat())
    await startup()
    yield
    stop_event.set()
    heartbeat_task.cancel()
    try:
        await heartbeat_task
    except asyncio.CancelledError:
        pass
    except Exception:
        pass
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
async def _frontend_cache_headers(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path

    if path.startswith("/ui/assets/"):
        response.headers.setdefault("Cache-Control", "public, max-age=31536000, immutable")
        return response

    if path == "/ui" or path == "/ui/" or (path.startswith("/ui/") and path.endswith(".html")):
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


if __name__ == "__main__":
    import uvicorn
    import os
    import signal
    import socket
    import threading
    import faulthandler
    from pathlib import Path
    
    host = "127.0.0.1"
    base_port = int(os.getenv("NEKO_USER_PLUGIN_SERVER_PORT", str(USER_PLUGIN_SERVER_PORT)))

    try:
        _dump_path = Path(__file__).resolve().parent / "log" / "server" / "faulthandler_dump.log"
        try:
            _dump_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        _dump_f = open(_dump_path, "a", encoding="utf-8")
        faulthandler.enable(file=_dump_f)
        faulthandler.register(signal.SIGUSR1, all_threads=True, file=_dump_f)
    except Exception:
        try:
            faulthandler.enable()
            faulthandler.register(signal.SIGUSR1, all_threads=True)
        except Exception:
            pass
    
    def _find_available_port(start_port: int, max_tries: int = 50) -> int:
        for p in range(start_port, start_port + max_tries):
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind((host, p))
                return p
            except OSError:
                continue
            finally:
                try:
                    s.close()
                except Exception:
                    pass
        return start_port
    
    selected_port = _find_available_port(base_port)
    os.environ["NEKO_USER_PLUGIN_SERVER_PORT"] = str(selected_port)
    if selected_port != base_port:
        logger.warning(
            "User plugin server port {} is unavailable, switched to {}",
            base_port,
            selected_port,
        )
    else:
        logger.info("User plugin server starting on {}:{}", host, selected_port)
    
    _sigint_count = 0
    _sigint_lock = threading.Lock()
    _force_exit_timer: threading.Timer | None = None

    # 增加 backlog 和 limit_concurrency 以避免连接排队
    # backlog: TCP 连接队列大小（默认 2048）
    # limit_concurrency: 最大并发连接数（默认无限制）
    config = uvicorn.Config(
        app,
        host=host,
        port=selected_port,
        log_config=None,
        backlog=4096,  # 增加 TCP backlog
        timeout_keep_alive=30,  # Keep-alive 超时
    )
    server = uvicorn.Server(config)

    def _start_force_exit_watchdog(timeout_s: float) -> None:
        global _force_exit_timer
        try:
            if _force_exit_timer is not None:
                return
            def _kill() -> None:
                try:
                    os._exit(130)
                except Exception:
                    raise SystemExit(130)
            t = threading.Timer(float(timeout_s), _kill)
            t.daemon = True
            _force_exit_timer = t
            t.start()
        except Exception:
            pass

    def _sigint_handler(_signum: int, _frame: object | None) -> None:
        global _sigint_count
        with _sigint_lock:
            _sigint_count += 1
            n = _sigint_count
        if n >= 2:
            try:
                os._exit(130)
            except Exception:
                raise SystemExit(130)
        try:
            server.should_exit = True
            server.force_exit = True
        except Exception:
            pass
        _start_force_exit_watchdog(timeout_s=2.0)

    _old_sigint = None
    try:
        _old_sigint = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, _sigint_handler)
        try:
            signal.signal(signal.SIGTERM, _sigint_handler)
        except Exception:
            pass
        try:
            signal.signal(signal.SIGQUIT, _sigint_handler)
        except Exception:
            pass
    except Exception:
        _old_sigint = None

    try:
        server.install_signal_handlers = lambda: None  # type: ignore[assignment]
    except Exception:
        pass

    try:
        server.run()
    finally:
        _cleanup_old_sigint = None
        try:
            _cleanup_old_sigint = signal.getsignal(signal.SIGINT)

            def _force_quit(*_args: object) -> None:
                try:
                    os._exit(130)
                except Exception:
                    raise SystemExit(130)

            signal.signal(signal.SIGINT, _force_quit)
        except Exception:
            _cleanup_old_sigint = None
        try:
            import psutil
            parent = psutil.Process(os.getpid())
            children = parent.children(recursive=True)
            for child in children:
                try:
                    child.terminate()
                except psutil.NoSuchProcess:
                    pass
            
            _, alive = psutil.wait_procs(children, timeout=0.5)
            for p in alive:
                try:
                    p.kill()
                except psutil.NoSuchProcess:
                    pass
        except KeyboardInterrupt:
            pass
        except ImportError:
            if hasattr(os, 'killpg'):
                try:
                    os.killpg(os.getpgrp(), signal.SIGKILL)
                except Exception:
                    pass
        except Exception:
            pass

        try:
            if _force_exit_timer is not None:
                _force_exit_timer.cancel()
        except Exception:
            pass

        try:
            if _cleanup_old_sigint is not None:
                signal.signal(signal.SIGINT, _cleanup_old_sigint)
        except Exception:
            pass
