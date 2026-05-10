"""Reusable FastAPI app factory for the plugin HTTP server."""
from __future__ import annotations

import asyncio
import faulthandler
import os
import signal
import threading
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import Response

from plugin.logging_config import get_logger
from utils.logger_config import get_module_logger
from plugin.server.infrastructure.exceptions import register_exception_handlers
from plugin.server.lifecycle import shutdown as lifecycle_shutdown
from plugin.server.lifecycle import startup as lifecycle_startup
from plugin.server.routes import (
    actions_router,
    config_router,
    frontend_router,
    health_router,
    llm_tools_router,
    logs_router,
    market_bridge_router,
    messages_router,
    metrics_router,
    plugin_cli_router,
    plugin_ui_router,
    plugins_router,
    runs_router,
    websocket_router,
)
from plugin.server.routes.frontend import mount_static_files

_EMBEDDED_BY_AGENT = os.getenv("NEKO_PLUGIN_HOSTED_BY_AGENT", "").strip().lower() == "true"

if _EMBEDDED_BY_AGENT:
    logger = get_module_logger(__name__, "Agent")
else:
    logger = get_logger("server.user_plugin_server")


def _can_register_faulthandler_signal() -> bool:
    return hasattr(faulthandler, "register") and hasattr(signal, "SIGUSR1")


@asynccontextmanager
async def plugin_server_lifespan(app: FastAPI) -> AsyncIterator[None]:
    _ = app

    if _can_register_faulthandler_signal():
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

    # When embedded inside agent_server, lifecycle is managed externally
    # via the user_plugin_enabled flag — do NOT auto-start here.
    if not _EMBEDDED_BY_AGENT:
        await lifecycle_startup()

    # Install-source lock subsystem (design §6.1 / §4.6). Runs after
    # lifecycle_startup so the plugin loader's filesystem state is
    # stable, but before the bridge-token write below because
    # /plugins/install-sources and the /plugins install_source field
    # injection both need the global manager to be set. Req 17.1: any
    # failure here must NOT block the remainder of lifespan startup —
    # StartupReconciler already swallows internal errors, and the outer
    # try/except here catches any issue in build_install_source_manager
    # (e.g. a weird env that prevents path resolution) so we can degrade
    # gracefully to set_global_manager(None).
    try:
        from plugin.server.application.install_source import (
            StartupReconciler,
            build_install_source_manager,
            set_global_manager,
        )
        _install_source_mgr = build_install_source_manager()
        await StartupReconciler(_install_source_mgr).run()
        set_global_manager(_install_source_mgr)
    except Exception as exc:
        logger.error(
            "InstallSourceManager init failed, subsystem will run in degraded mode: {}",
            exc,
        )
        try:
            from plugin.server.application.install_source import set_global_manager
            set_global_manager(None)
        except Exception as inner_exc:
            logger.error(
                "Failed to mark install-source subsystem as degraded: {}",
                inner_exc,
            )

    # 写入 bridge token 文件，供 Market 前端和 URI handler 使用
    try:
        from plugin.server.routes.market_bridge import write_bridge_token_file
        from pathlib import Path
        _bridge_dir = Path.home() / ".neko"
        write_bridge_token_file(_bridge_dir)
    except Exception as exc:
        logger.warning("Failed to write bridge token file: {}", exc)

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
        if not _EMBEDDED_BY_AGENT:
            await lifecycle_shutdown()


def build_plugin_server_app(title: str = "N.E.K.O User Plugin Server") -> FastAPI:
    app = FastAPI(title=title, lifespan=plugin_server_lifespan)

    # Market 域名通过 settings 配置，支持自部署
    from plugin.settings import MARKET_ORIGINS as _market_origins

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:48911",
            "http://127.0.0.1:48911",
            *_market_origins,
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
    app.include_router(actions_router)
    # galgame_plugin install routes 是可选模块：plugin import-time 副作用
    # （缺 dxcam / tesseract / textractor 等可选依赖、非 Windows 运行时等）不应让
    # 整个 plugin server 启动失败；失败时只让 install 端点返回 404。
    try:
        from plugin.plugins.galgame_plugin.install_routes import (
            router as galgame_install_router,
        )
        app.include_router(galgame_install_router)
    except ImportError as exc:
        logger.warning(
            "galgame install routes unavailable, install endpoints will be 404: "
            "err_type={}, err={}",
            type(exc).__name__,
            str(exc),
        )
    app.include_router(plugin_cli_router)
    app.include_router(llm_tools_router)
    app.include_router(market_bridge_router)
    return app
