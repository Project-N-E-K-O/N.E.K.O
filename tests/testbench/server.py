"""FastAPI application factory for the testbench server.

Later phases will register additional routers (session/persona/memory/
chat/judge/time/config/stage). The minimum P01 build only wires up the
health router and ensures the runtime data directories exist.
"""
from __future__ import annotations

import traceback

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates


class _NoCacheStaticFiles(StaticFiles):
    """StaticFiles with ``Cache-Control: no-cache, must-revalidate``.

    踩点 (P12 多次): 开发期改完 JS / CSS 文件, 浏览器 (尤其 Edge/Chrome 的 ES
    module loader) 会命中强缓存直接用旧副本, 导致 "代码已经合并、手动刷新多
    次、仍然看不到新 UI" 的假 bug — 测试人员还会以为是后端数据没对. 调试一
    圈绕一圈最后只是 Ctrl+Shift+R 清缓存. testbench 是开发/测试工具, 静态资
    源体积小且改得频繁, 统一强制走 "revalidate" — 浏览器每次请求都带上
    If-Modified-Since/ETag, 未变返回 304 (几乎不费流量), 变了立即拉新版本.
    这样就彻底杜绝 "UI 代码已更新但用户看不到" 的假象.
    """

    def file_response(self, *args, **kwargs):  # noqa: ANN001, ANN202
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response

from tests.testbench import config as tb_config
from tests.testbench.logger import anon_logger, python_logger
from tests.testbench.routers import (
    chat_router,
    config_router,
    health_router,
    memory_router,
    persona_router,
    session_router,
    time_router,
)
from tests.testbench.session_store import get_session_store


def create_app() -> FastAPI:
    """Build and return the FastAPI application."""
    tb_config.ensure_code_support_dirs()
    tb_config.ensure_data_dirs()

    app = FastAPI(
        title="N.E.K.O. Testbench",
        version="0.1.0",
        docs_url="/api/docs",
        redoc_url=None,
    )

    # Static assets + Jinja templates ------------------------------------
    app.mount(
        "/static",
        _NoCacheStaticFiles(directory=str(tb_config.STATIC_DIR), check_dir=False),
        name="testbench-static",
    )
    templates = Jinja2Templates(directory=str(tb_config.TEMPLATES_DIR))

    @app.get("/", response_class=HTMLResponse, name="index")
    async def index(request: Request) -> HTMLResponse:
        """Serve the single-page UI shell. The shell renders empty until
        JavaScript boots and hydrates each workspace.
        """
        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "app_name": "N.E.K.O. Testbench",
                "default_port": tb_config.DEFAULT_PORT,
            },
        )

    # Global exception handler -------------------------------------------
    @app.exception_handler(Exception)
    async def _unhandled_exception_handler(request: Request, exc: Exception):
        """Turn uncaught exceptions into structured JSON.

        A full stack trace goes to stderr (through :mod:`logging`); a
        short digest rides the HTTP response so the browser can show a
        friendly toast. P19 will extend this by also pushing the record
        into the Diagnostics → Errors list.
        """
        python_logger().exception("Unhandled exception on %s %s", request.method, request.url.path)
        anon_logger().log_sync(
            "http.unhandled_exception",
            level="ERROR",
            payload={"method": request.method, "path": request.url.path},
            error=f"{type(exc).__name__}: {exc}",
        )
        store = get_session_store()
        return JSONResponse(
            status_code=500,
            content={
                "error_type": type(exc).__name__,
                "message": str(exc),
                "trace_digest": "\n".join(traceback.format_exception(type(exc), exc, exc.__traceback__)[-4:]),
                "session_state": store.get_state(),
            },
        )

    # Routers -------------------------------------------------------------
    app.include_router(health_router.router)
    app.include_router(session_router.router)
    app.include_router(config_router.router)
    app.include_router(persona_router.router)
    app.include_router(time_router.router)
    app.include_router(memory_router.router)
    app.include_router(chat_router.router)

    # Shutdown hook: release the ConfigManager singleton + sandbox so a
    # subsequent uvicorn --reload cycle doesn't leave stale paths wired in.
    @app.on_event("shutdown")
    async def _shutdown_cleanup() -> None:
        try:
            await get_session_store().destroy(purge_sandbox=False)
        except Exception:  # noqa: BLE001 - best-effort cleanup on shutdown
            python_logger().exception("shutdown: session teardown failed")

    return app


# Module-level app instance for ``uvicorn tests.testbench.server:app`` usage.
app: FastAPI = create_app()
