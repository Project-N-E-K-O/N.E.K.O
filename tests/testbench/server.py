"""FastAPI application factory for the testbench server.

Later phases will register additional routers (session/persona/memory/
chat/judge/time/config/stage). The minimum P01 build only wires up the
health router and ensures the runtime data directories exist.
"""
from __future__ import annotations

import asyncio
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
from tests.testbench.logger import anon_logger, cleanup_old_logs, python_logger
from tests.testbench.pipeline import autosave as autosave_module
from tests.testbench.pipeline import boot_cleanup as boot_cleanup_module
from tests.testbench.pipeline import diagnostics_store
from tests.testbench.routers import (
    chat_router,
    config_router,
    diagnostics_router,
    external_event_router,
    health_router,
    judge_router,
    memory_router,
    persona_router,
    security_router,
    session_router,
    snapshot_router,
    stage_router,
    time_router,
)
from tests.testbench.session_store import get_session_store


def create_app() -> FastAPI:
    """Build and return the FastAPI application."""
    tb_config.ensure_code_support_dirs()
    tb_config.ensure_data_dirs()

    app = FastAPI(
        title="N.E.K.O. Testbench",
        version=tb_config.TESTBENCH_VERSION,
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

        Three sinks each get the record so no single failure loses it:

        1. ``python_logger().exception`` — full stack goes to stderr via
           Python logging, survives even if the client disconnects.
        2. ``SessionLogger`` (or anon logger if no session) — per-session
           JSONL on disk, what the Diagnostics → Logs subpage tails.
        3. :mod:`pipeline.diagnostics_store` — process-level ring buffer
           the Diagnostics → Errors subpage reads for "recent errors"
           regardless of which session was active.

        The response payload stays compact (``{error_type, message,
        trace_digest, session_state}``) so the browser toast has
        everything it needs; full trace lives on disk + ring buffer.
        """
        python_logger().exception("Unhandled exception on %s %s", request.method, request.url.path)
        store = get_session_store()
        session_state = store.get_state()
        session_id = session_state.get("session_id") if isinstance(session_state, dict) else None
        trace_digest = "\n".join(
            traceback.format_exception(type(exc), exc, exc.__traceback__)[-4:]
        )

        # Route the JSONL entry to the live session logger when one exists
        # so per-session Logs pages pick it up; else anon logger so the
        # entry still lands on disk.
        logger_target = None
        try:
            active_session = store.get()
            if active_session is not None:
                logger_target = active_session.logger
        except Exception:  # noqa: BLE001 - never crash the error handler
            logger_target = None
        (logger_target or anon_logger()).log_sync(
            "http.unhandled_exception",
            level="ERROR",
            payload={"method": request.method, "path": request.url.path},
            error=f"{type(exc).__name__}: {exc}",
        )

        try:
            diagnostics_store.record(
                source="middleware",
                type=type(exc).__name__,
                message=str(exc) or "(no message)",
                level="error",
                session_id=session_id,
                url=str(request.url.path),
                method=request.method,
                status=500,
                trace_digest=trace_digest,
                user_agent=request.headers.get("user-agent"),
                detail={"query": str(request.url.query) or None},
            )
        except Exception:  # noqa: BLE001 - ring buffer push must not re-raise
            python_logger().exception("diagnostics_store.record failed inside exception handler")

        return JSONResponse(
            status_code=500,
            content={
                "error_type": type(exc).__name__,
                "message": str(exc),
                "trace_digest": trace_digest,
                "session_state": session_state,
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
    app.include_router(judge_router.router)
    app.include_router(stage_router.router)
    app.include_router(snapshot_router.router)
    app.include_router(diagnostics_router.router)
    app.include_router(security_router.router)
    app.include_router(external_event_router.router)

    # ── Log retention background task (P19) ─────────────────────────────
    # Three triggers keep the JSONL log directory bounded:
    #   1. Startup: run once so a long-idle testbench picks up the day it
    #      missed. Sync call (no event loop at module import, but fine in
    #      the FastAPI startup hook which runs after loop is up).
    #   2. Periodic: every ``LOG_CLEANUP_INTERVAL_SECONDS`` (default 12h)
    #      re-scan in case the process runs across midnight.
    #   3. Manual: ``POST /api/diagnostics/logs/cleanup`` lets users force
    #      it from the Logs subpage.
    # Cancel the task cleanly on shutdown so uvicorn --reload doesn't leak
    # background work across reloads.
    app.state.log_cleanup_task = None

    async def _periodic_log_cleanup() -> None:
        try:
            while True:
                try:
                    result = cleanup_old_logs()
                    if result["deleted"]:
                        python_logger().info(
                            "log cleanup: removed %d file(s), freed %d bytes (retention=%d days)",
                            result["deleted"], result["bytes_freed"], result["retention_days"],
                        )
                except Exception:  # noqa: BLE001 - never crash the task loop
                    python_logger().exception("log cleanup pass failed")
                await asyncio.sleep(tb_config.LOG_CLEANUP_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            raise

    @app.on_event("startup")
    async def _startup_cleanup() -> None:
        try:
            result = cleanup_old_logs()
            if result["deleted"]:
                python_logger().info(
                    "boot log cleanup: removed %d file(s), freed %d bytes (retention=%d days)",
                    result["deleted"], result["bytes_freed"], result["retention_days"],
                )
        except Exception:  # noqa: BLE001 - never crash boot
            python_logger().exception("boot log cleanup failed")

        # P22 autosave: prune entries older than the retention window.
        # We do this synchronously in the startup hook (not in a
        # background task) because:
        #   (a) it's O(n) in the number of autosave files which is ≤ 3
        #       per session so essentially bounded in practice;
        #   (b) the UI's ``/autosaves/boot_orphans`` call happens after
        #       startup completes, so running cleanup synchronously
        #       ensures stale-but-expired entries don't flash into the
        #       "restore?" banner before disappearing on first cleanup.
        try:
            stats = autosave_module.cleanup_old_autosaves()
            if stats["deleted_entries"]:
                python_logger().info(
                    "boot autosave cleanup: removed %d entry/entries "
                    "(json=%d, tar=%d, retention=%.1fh)",
                    stats["deleted_entries"], stats["json_removed"],
                    stats["tar_removed"], stats["retention_hours"],
                )
        except Exception:  # noqa: BLE001 - never crash boot
            python_logger().exception("boot autosave cleanup failed")

        # PLAN §10 P-B (post-P22 hardening): clean up half-written
        # ``.tmp`` files from crashed atomic-writes, stale
        # ``memory.locked_<ts>`` rename-aside sidecars (> 24h), and
        # orphan SQLite sidecars (``*-journal`` / ``*-wal`` / ``*-shm``
        # whose ``.db`` is gone). Safe by construction — see the module
        # docstring for each category's rationale. Sandbox-directory
        # level orphans (P-A) are deliberately not touched here;
        # they need user triage via Diagnostics → Paths (P-D, future).
        try:
            bc_stats = boot_cleanup_module.run_boot_cleanup()
            has_activity = (
                bc_stats.get("files_removed", 0)
                or bc_stats.get("dirs_removed", 0)
                or bc_stats.get("unlink_failures", 0)
                or bc_stats.get("rmtree_failures", 0)
            )
            if has_activity:
                python_logger().info(
                    "boot temp-file cleanup: files_removed=%d, "
                    "dirs_removed=%d, unlink_failures=%d, rmtree_failures=%d",
                    bc_stats.get("files_removed", 0),
                    bc_stats.get("dirs_removed", 0),
                    bc_stats.get("unlink_failures", 0),
                    bc_stats.get("rmtree_failures", 0),
                )
        except Exception:  # noqa: BLE001 - never crash boot
            python_logger().exception("boot temp-file cleanup failed")

        app.state.log_cleanup_task = asyncio.create_task(
            _periodic_log_cleanup(), name="testbench-log-cleanup"
        )

    # Shutdown hook: release the ConfigManager singleton + sandbox so a
    # subsequent uvicorn --reload cycle doesn't leave stale paths wired in.
    @app.on_event("shutdown")
    async def _shutdown_cleanup() -> None:
        task = getattr(app.state, "log_cleanup_task", None)
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        try:
            await get_session_store().destroy(purge_sandbox=False)
        except Exception:  # noqa: BLE001 - best-effort cleanup on shutdown
            python_logger().exception("shutdown: session teardown failed")

    return app


# Module-level app instance for ``uvicorn tests.testbench.server:app`` usage.
app: FastAPI = create_app()
