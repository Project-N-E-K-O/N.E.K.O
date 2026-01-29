"""
User Plugin Server

HTTP 服务器主文件，定义所有路由端点。
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Optional

import asyncio
import logging
import os
import sys
from pathlib import Path

from loguru import logger as logger

# 移除默认handler并配置简洁格式
logger.remove()
logger.add(
    sys.stdout,
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
    level="INFO",
    colorize=True,
)

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from concurrent.futures import ThreadPoolExecutor
from fastapi import FastAPI, HTTPException, Request, Query, Body, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from config import USER_PLUGIN_SERVER_PORT

# 配置服务器日志
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

        # 不使用depth参数，避免显示模块路径信息
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

from plugin.core.state import state
from plugin.api.models import PluginPushMessageResponse
from plugin.runtime.registry import get_plugins as registry_get_plugins
from plugin.runtime.status import status_manager
from plugin.server.exceptions import register_exception_handlers
from plugin.server.error_handler import handle_plugin_error, safe_execute
from plugin.server.services import (
    build_plugin_list,
    trigger_plugin,
    get_messages_from_queue,
)
from plugin.server.lifecycle import startup, shutdown
from plugin.server.utils import now_iso
from plugin.server.management import start_plugin, stop_plugin, reload_plugin
from plugin.server.logs import get_plugin_logs, get_plugin_log_files, log_stream_endpoint
from plugin.server.config_service import load_plugin_config, update_plugin_config, replace_plugin_config
from plugin.server.metrics_service import metrics_collector
from plugin.server.auth import require_admin
from plugin.settings import MESSAGE_QUEUE_DEFAULT_MAX_COUNT
from plugin.api.exceptions import PluginError
from plugin.api.models import RunCreateRequest, RunCreateResponse

from plugin.server.runs import (
    RunCancelRequest,
    RunRecord,
    ExportListResponse,
    create_run,
    get_run,
    cancel_run,
    list_export_for_run,
)

from plugin.server.ws_run import ws_run_endpoint
from plugin.server.ws_run import issue_run_token
from plugin.server.blob_store import blob_store
from plugin.server.ws_admin import ws_admin_endpoint

# 创建专用线程池，避免默认线程池饥饿导致所有请求阻塞
# 默认线程池通常只有8-12个线程，高并发时会导致连心跳和静态文件也超时
_api_executor = ThreadPoolExecutor(
    max_workers=max(16, (os.cpu_count() or 1) * 4),
    thread_name_prefix="api-worker"
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
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
# 使用 loguru logger（支持 "{}" 风格参数化日志）

# 配置 CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",  # Vite 开发服务器
        "http://127.0.0.1:5173",
        "http://localhost:48911",  # 主服务器（如果需要）
        "http://127.0.0.1:48911",
    ],
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册异常处理中间件
register_exception_handlers(app)


def _get_frontend_root_dir() -> Path:
    plugin_root = Path(__file__).resolve().parent
    exported = plugin_root / "frontend" / "exported"
    return exported


_FRONTEND_ROOT_DIR = _get_frontend_root_dir()
app.mount(
    "/ui/assets",
    StaticFiles(directory=str(_FRONTEND_ROOT_DIR / "assets"), check_dir=False),
    name="frontend-assets",
)


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


# ========== 基础路由 ==========

@app.get("/health")
async def health():
    """健康检查"""
    return {"status": "ok", "time": now_iso()}


@app.get("/available")
async def available():
    """返回可用性和基本统计"""
    loop = asyncio.get_running_loop()
    
    def _get_count():
        with state.plugins_lock:
            return len(state.plugins)
    
    plugins_count = await loop.run_in_executor(_api_executor, _get_count)
    return {
        "status": "ok",
        "available": True,
        "plugins_count": plugins_count,
        "time": now_iso()
    }


@app.get("/server/info")
async def server_info(_: str = require_admin):
    """
    返回服务器信息，包括SDK版本
    
    - 需要管理员验证码（Bearer Token）
    """
    from plugin.sdk.version import SDK_VERSION
    
    loop = asyncio.get_running_loop()
    
    def _get_info():
        with state.plugins_lock:
            plugins_count = len(state.plugins)
            registered_plugins = list(state.plugins.keys())
        
        with state.plugin_hosts_lock:
            running_plugins_count = len(state.plugin_hosts)
            running_plugins = list(state.plugin_hosts.keys())
            # 检查每个运行插件的进程状态
        running_plugins_status = {}
        for pid in running_plugins:
            host = state.plugin_hosts.get(pid)
            if host:
                # 不调用 is_alive()，因为可能阻塞事件循环
                # 如果 host 存在于 plugin_hosts 中，就认为它是 running 的
                running_plugins_status[pid] = {
                    "alive": True,  # 在 plugin_hosts 中即表示 running
                    "pid": host.process.pid if hasattr(host, 'process') and host.process else None
                }
        
        return {
            "plugins_count": plugins_count,
            "registered_plugins": registered_plugins,
            "running_plugins_count": running_plugins_count,
            "running_plugins": running_plugins,
            "running_plugins_status": running_plugins_status,
        }
    
    info = await loop.run_in_executor(_api_executor, _get_info)
    info["sdk_version"] = SDK_VERSION
    info["time"] = now_iso()
    return info


@app.get("/plugin/status")
async def plugin_status(plugin_id: Optional[str] = Query(default=None)):
    """
    查询插件运行状态：
    - GET /plugin/status                -> 所有插件状态
    - GET /plugin/status?plugin_id=xxx  -> 指定插件状态
    """
    try:
        loop = asyncio.get_running_loop()
        if plugin_id:
            # status_manager.get_plugin_status 可能有锁竞争，放到线程池执行
            result = await loop.run_in_executor(_api_executor, status_manager.get_plugin_status, plugin_id)
            # 兼容字段：部分调用方可能依赖 time 字段
            if isinstance(result, dict) and "time" not in result:
                result["time"] = now_iso()
            return result
        else:
            # status_manager.get_plugin_status 可能有锁竞争，放到线程池执行
            plugins_status = await loop.run_in_executor(_api_executor, status_manager.get_plugin_status)
            return {
                "plugins": plugins_status,
                "time": now_iso(),
            }
    except HTTPException:
        raise
    except (PluginError, ValueError, AttributeError, KeyError) as e:
        raise handle_plugin_error(e, "Failed to get plugin status", 500) from e
    except Exception as e:
        logger.exception("Failed to get plugin status: Unexpected error")
        raise handle_plugin_error(e, "Failed to get plugin status", 500) from e


# ========== 插件管理路由 ==========

@app.get("/plugins")
async def list_plugins():
    """
    返回已知插件列表
    
    统一返回结构：
    {
        "plugins": [ ... ],
        "message": "..."
    }
    """
    try:
        # build_plugin_list 可能有锁竞争，放到线程池执行
        loop = asyncio.get_running_loop()
        plugins = await loop.run_in_executor(_api_executor, build_plugin_list)
        
        if plugins:
            return {"plugins": plugins, "message": ""}
        else:
            logger.info("No plugins registered.")
            return {
                "plugins": [],
                "message": "no plugins registered"
            }
    except HTTPException:
        raise
    except (PluginError, ValueError, AttributeError, KeyError) as e:
        raise handle_plugin_error(e, "Failed to list plugins", 500) from e
    except Exception as e:
        logger.exception("Failed to list plugins: Unexpected error")
        raise handle_plugin_error(e, "Failed to list plugins", 500) from e


# ========== Run Protocol (new primary invocation API) ==========

@app.post("/runs", response_model=RunCreateResponse)
async def runs_create(payload: RunCreateRequest, request: Request):
    try:
        client_host = request.client.host if request.client else None
        base = await create_run(payload, client_host=client_host)
        token, exp = issue_run_token(run_id=base.run_id, perm="read")
        return RunCreateResponse(run_id=base.run_id, status=base.status, run_token=token, expires_at=exp)
    except Exception as e:
        server_logger.error(f"Error creating run: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.websocket("/ws/run")
async def ws_run(websocket: WebSocket):
    await ws_run_endpoint(websocket)


@app.websocket("/ws/admin")
async def ws_admin(websocket: WebSocket):
    await ws_admin_endpoint(websocket)


@app.get("/runs/{run_id}", response_model=RunRecord)
async def runs_get(run_id: str):
    try:
        rec = get_run(run_id)
        if rec is None:
            raise HTTPException(status_code=404, detail="run not found")
        return rec
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to get run")
        raise handle_plugin_error(e, "Failed to get run", 500) from e


@app.post("/runs/{run_id}/uploads")
async def runs_create_upload(run_id: str, request: Request):
    try:
        rec = get_run(run_id)
        if rec is None:
            raise HTTPException(status_code=404, detail="run not found")

        body = None
        try:
            body = await request.json()
        except Exception:
            body = None
        filename = None
        mime = None
        max_bytes = None
        if isinstance(body, dict):
            filename = body.get("filename")
            mime = body.get("mime")
            max_bytes = body.get("max_bytes")

        sess = blob_store.create_upload(run_id=run_id, filename=filename, mime=mime, max_bytes=max_bytes)
        base = str(request.base_url).rstrip("/")
        upload_url = f"{base}/uploads/{sess.upload_id}"
        blob_url = f"{base}/runs/{run_id}/blobs/{sess.blob_id}"
        return {"upload_id": sess.upload_id, "blob_id": sess.blob_id, "upload_url": upload_url, "blob_url": blob_url}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to create upload")
        raise handle_plugin_error(e, "Failed to create upload", 500) from e


@app.put("/uploads/{upload_id}")
async def uploads_put(upload_id: str, request: Request):
    sess = blob_store.get_upload(upload_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="upload not found")

    rec = get_run(sess.run_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="run not found")
    if rec.status not in ("running", "cancel_requested"):
        raise HTTPException(status_code=409, detail="run not running")

    try:
        total = 0
        with sess.tmp_path.open("wb") as f:
            async for chunk in request.stream():
                if not chunk:
                    continue
                if not isinstance(chunk, (bytes, bytearray)):
                    continue
                total += len(chunk)
                if total > int(sess.max_bytes):
                    raise HTTPException(status_code=413, detail="upload too large")
                f.write(chunk)
        blob_store.finalize_upload(upload_id)
        return {"ok": True, "upload_id": sess.upload_id, "blob_id": sess.blob_id, "size": total}
    except HTTPException:
        try:
            if sess.tmp_path.exists():
                sess.tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise
    except Exception as e:
        try:
            if sess.tmp_path.exists():
                sess.tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        logger.exception("Failed to upload blob")
        raise handle_plugin_error(e, "Failed to upload blob", 500) from e


@app.get("/runs/{run_id}/blobs/{blob_id}")
async def runs_get_blob(run_id: str, blob_id: str):
    try:
        p = blob_store.get_blob_path(run_id=run_id, blob_id=blob_id)
        if p is None:
            raise HTTPException(status_code=404, detail="blob not found")
        return FileResponse(str(p), filename=f"{blob_id}.bin")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to download blob")
        raise handle_plugin_error(e, "Failed to download blob", 500) from e


@app.post("/runs/{run_id}/cancel", response_model=RunRecord)
async def runs_cancel(run_id: str, payload: RunCancelRequest = Body(default=RunCancelRequest())):
    try:
        rec = cancel_run(run_id, reason=payload.reason)
        if rec is None:
            raise HTTPException(status_code=404, detail="run not found")
        return rec
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to cancel run")
        raise handle_plugin_error(e, "Failed to cancel run", 500) from e


@app.get("/runs/{run_id}/export", response_model=ExportListResponse)
async def runs_export(
    run_id: str,
    after: Optional[str] = Query(default=None),
    limit: int = Query(default=200, ge=1, le=2000),
):
    try:
        rec = get_run(run_id)
        if rec is None:
            raise HTTPException(status_code=404, detail="run not found")
        return list_export_for_run(run_id=run_id, after=after, limit=int(limit))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to list export items")
        raise handle_plugin_error(e, "Failed to list export items", 500) from e


# ========== 消息路由 ==========

@app.get("/plugin/messages")
async def get_plugin_messages(
    plugin_id: Optional[str] = Query(default=None),
    max_count: int = Query(default=MESSAGE_QUEUE_DEFAULT_MAX_COUNT, ge=1, le=1000),
    priority_min: Optional[int] = Query(default=None, description="最低优先级（包含）"),
):
    """
    获取插件推送的消息队列
    
    - GET /plugin/messages                    -> 获取所有插件的消息
    - GET /plugin/messages?plugin_id=xxx       -> 获取指定插件的消息
    - GET /plugin/messages?max_count=50        -> 限制返回数量
    - GET /plugin/messages?priority_min=5      -> 只返回优先级>=5的消息
    """
    try:
        messages = await asyncio.to_thread(
            get_messages_from_queue,
            plugin_id=plugin_id,
            max_count=max_count,
            priority_min=priority_min,
        )
        
        return {
            "messages": messages,
            "count": len(messages),
            "time": now_iso(),
        }
    except HTTPException:
        raise
    except (PluginError, ValueError, AttributeError) as e:
        raise handle_plugin_error(e, "Failed to get plugin messages", 500) from e
    except Exception as e:
        logger.exception("Failed to get plugin messages: Unexpected error")
        raise handle_plugin_error(e, "Failed to get plugin messages", 500) from e


@app.get("/ui", response_class=HTMLResponse)
@app.get("/ui/", response_class=HTMLResponse)
async def frontend_index():
    index_file = _FRONTEND_ROOT_DIR / "index.html"
    if not index_file.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Frontend index not found: {index_file}. Please export frontend first.",
        )
    return FileResponse(
        str(index_file),
        media_type="text/html; charset=utf-8",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@app.get("/ui/{full_path:path}")
async def frontend_file(full_path: str):
    # SPA history fallback: allow browser refresh on routes like /ui/plugins/{id}
    # Note: assets are served via the mounted StaticFiles app at /ui/assets
    if full_path.startswith("assets/"):
        raise HTTPException(status_code=404, detail="Not found")

    candidate = (_FRONTEND_ROOT_DIR / full_path).resolve()
    try:
        candidate.relative_to(_FRONTEND_ROOT_DIR.resolve())
    except Exception:
        raise HTTPException(status_code=404, detail="Not found")

    if candidate.is_file():
        if candidate.suffix.lower() == ".html":
            return FileResponse(
                str(candidate),
                headers={
                    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                    "Pragma": "no-cache",
                    "Expires": "0",
                },
            )
        return FileResponse(str(candidate))

    index_file = _FRONTEND_ROOT_DIR / "index.html"
    if not index_file.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Frontend index not found: {index_file}. Please export frontend first.",
        )
    return FileResponse(
        str(index_file),
        media_type="text/html; charset=utf-8",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


# ========== 插件管理路由（扩展） ==========

@app.post("/plugin/{plugin_id}/start")
async def start_plugin_endpoint(plugin_id: str, _: str = require_admin):
    """
    启动插件
    
    - POST /plugin/{plugin_id}/start
    - 需要管理员验证码（Bearer Token）
    """
    try:
        return await start_plugin(plugin_id)
    except HTTPException:
        raise
    except (PluginError, ValueError, AttributeError, OSError) as e:
        raise handle_plugin_error(e, f"Failed to start plugin {plugin_id}", 500) from e
    except Exception as e:
        logger.exception(f"Failed to start plugin {plugin_id}: Unexpected error")
        raise handle_plugin_error(e, f"Failed to start plugin {plugin_id}", 500) from e


@app.post("/plugin/{plugin_id}/stop")
async def stop_plugin_endpoint(plugin_id: str, _: str = require_admin):
    """
    停止插件
    
    - POST /plugin/{plugin_id}/stop
    - 需要管理员验证码（Bearer Token）
    """
    try:
        return await stop_plugin(plugin_id)
    except HTTPException:
        raise
    except (PluginError, OSError, TimeoutError) as e:
        raise handle_plugin_error(e, f"Failed to stop plugin {plugin_id}", 500) from e
    except Exception as e:
        logger.exception(f"Failed to stop plugin {plugin_id}: Unexpected error")
        raise handle_plugin_error(e, f"Failed to stop plugin {plugin_id}", 500) from e


@app.post("/plugin/{plugin_id}/reload")
async def reload_plugin_endpoint(plugin_id: str, _: str = require_admin):
    """
    重载插件
    
    - POST /plugin/{plugin_id}/reload
    - 需要管理员验证码（Bearer Token）
    """
    try:
        return await reload_plugin(plugin_id)
    except HTTPException:
        raise
    except (PluginError, OSError, TimeoutError) as e:
        raise handle_plugin_error(e, f"Failed to reload plugin {plugin_id}", 500) from e
    except Exception as e:
        logger.exception(f"Failed to reload plugin {plugin_id}: Unexpected error")
        raise handle_plugin_error(e, f"Failed to reload plugin {plugin_id}", 500) from e


# ========== 性能监控路由 ==========

@app.get("/plugin/metrics")
async def get_all_plugin_metrics(_: str = require_admin):
    """
    获取所有插件的性能指标
    
    - GET /plugin/metrics
    - 需要管理员验证码（Bearer Token）
    """
    try:
        metrics = metrics_collector.get_current_metrics()
        
        # 确保 metrics 是列表
        if not isinstance(metrics, list):
            logger.warning(f"get_current_metrics returned non-list: {type(metrics)}")
            metrics = []
        
        # 确保每个 metric 都是字典
        safe_metrics = []
        for m in metrics:
            if isinstance(m, dict):
                safe_metrics.append(m)
            else:
                logger.warning(f"Invalid metric format: {type(m)}")
        
        # 计算全局性能指标（metrics 是字典列表）
        total_cpu = sum(float(m.get("cpu_percent", 0.0)) for m in safe_metrics)
        total_memory_mb = sum(float(m.get("memory_mb", 0.0)) for m in safe_metrics)
        total_memory_percent = sum(float(m.get("memory_percent", 0.0)) for m in safe_metrics)
        total_threads = sum(int(m.get("num_threads", 0)) for m in safe_metrics)
        
        return {
            "metrics": safe_metrics,
            "count": len(safe_metrics),
            "global": {
                "total_cpu_percent": round(total_cpu, 2),
                "total_memory_mb": round(total_memory_mb, 2),
                "total_memory_percent": round(total_memory_percent, 2),
                "total_threads": total_threads,
                "active_plugins": len([m for m in safe_metrics if m.get("pid") is not None])
            },
            "time": now_iso()
        }
    except (PluginError, ValueError, AttributeError) as e:
        logger.warning(f"Failed to get plugin metrics: {e}")
        # 返回空结果而不是抛出异常，避免前端显示错误
        return {
            "metrics": [],
            "count": 0,
            "global": {
                "total_cpu_percent": 0.0,
                "total_memory_mb": 0.0,
                "total_memory_percent": 0.0,
                "total_threads": 0,
                "active_plugins": 0
            },
            "time": now_iso()
        }
    except Exception:
        logger.exception("Failed to get plugin metrics: Unexpected error")
        # 返回空结果而不是抛出异常，避免前端显示错误
        return {
            "metrics": [],
            "count": 0,
            "global": {
                "total_cpu_percent": 0.0,
                "total_memory_mb": 0.0,
                "total_memory_percent": 0.0,
                "total_threads": 0,
                "active_plugins": 0
            },
            "time": now_iso()
        }


@app.get("/plugin/metrics/{plugin_id}")
async def get_plugin_metrics(plugin_id: str, _: str = require_admin):
    """
    获取指定插件的性能指标
    
    - GET /plugin/metrics/{plugin_id}
    - 需要管理员验证码（Bearer Token）
    
    如果插件正在运行但没有指标数据（比如刚启动），返回 200 但 metrics 为 null。
    如果插件不存在，返回 404。
    """
    try:
        loop = asyncio.get_running_loop()
        
        def _check_plugin():
            # 检查插件是否已注册（在 state.plugins 中）
            with state.plugins_lock:
                plugin_registered = plugin_id in state.plugins
            
            # 检查插件是否正在运行（在 state.plugin_hosts 中）
            with state.plugin_hosts_lock:
                plugin_running = plugin_id in state.plugin_hosts
                if plugin_running:
                    host = state.plugin_hosts[plugin_id]
                    # 检查进程状态
                    # 不调用 process.is_alive()，因为可能阻塞事件循环
                    # 如果 host 存在且有 process，就认为它是 alive 的
                    process_alive = hasattr(host, "process") and host.process is not None
                    if process_alive:
                        logger.debug(
                            f"Plugin {plugin_id} is running (pid: {host.process.pid if hasattr(host.process, 'pid') else 'unknown'})"
                        )
                    else:
                        logger.debug(f"Plugin {plugin_id} host has no process object")
                else:
                    host = None
                    process_alive = False
                    # 调试：列出所有正在运行的插件
                    all_running_plugins = list(state.plugin_hosts.keys())
                    return plugin_registered, plugin_running, host, process_alive, all_running_plugins
                return plugin_registered, plugin_running, host, process_alive, None
        
        plugin_registered, plugin_running, host, process_alive, all_running_plugins = await loop.run_in_executor(_api_executor, _check_plugin)
        
        if all_running_plugins is not None:
            logger.info(
                f"Plugin {plugin_id} is registered but not in plugin_hosts. "
                f"Currently tracked plugins in plugin_hosts: {all_running_plugins}. "
                f"Plugin may need to be started manually via /plugin/{plugin_id}/start"
            )
        
        # 如果插件未注册，返回 404
        if not plugin_registered:
            raise HTTPException(
                status_code=404,
                detail=f"Plugin '{plugin_id}' not found"
            )
        
        # 获取指标数据
        metrics = metrics_collector.get_current_metrics(plugin_id)
        
        if not metrics:
            # 插件已注册但没有指标数据
            # 检查进程状态以提供更详细的信息
            if not plugin_running:
                message = "Plugin is registered but not running (start the plugin to collect metrics)"
            elif not process_alive:
                message = "Plugin process is not alive (may have crashed or stopped)"
            else:
                message = "Plugin is running but no metrics available yet (may be collecting, check collector status)"
            
            logger.debug(
                f"Plugin {plugin_id} registered but no metrics: registered={plugin_registered}, "
                f"running={plugin_running}, process_alive={process_alive}, has_host={host is not None}"
            )
            
            return {
                "plugin_id": plugin_id,
                "metrics": None,
                "message": message,
                "plugin_running": plugin_running,
                "process_alive": process_alive,
                "time": now_iso()
            }
        
        return {
            "plugin_id": plugin_id,
            "metrics": metrics[0],
            "time": now_iso()
        }
    except HTTPException:
        raise
    except (PluginError, ValueError, AttributeError, KeyError) as e:
        raise handle_plugin_error(e, f"Failed to get metrics for plugin {plugin_id}", 500) from e
    except Exception as e:
        logger.exception(f"Failed to get metrics for plugin {plugin_id}: Unexpected error")
        raise handle_plugin_error(e, f"Failed to get metrics for plugin {plugin_id}", 500) from e


@app.get("/plugin/metrics/{plugin_id}/history")
async def get_plugin_metrics_history(
    plugin_id: str,
    limit: int = Query(default=100, ge=1, le=1000),
    start_time: Optional[str] = Query(default=None),
    end_time: Optional[str] = Query(default=None),
    _: str = require_admin
):
    """
    获取插件性能指标历史
    
    - GET /plugin/metrics/{plugin_id}/history?limit=100
    - 需要管理员验证码（Bearer Token）
    """
    try:
        history = metrics_collector.get_metrics_history(
            plugin_id=plugin_id,
            limit=limit,
            start_time=start_time,
            end_time=end_time
        )
        return {
            "plugin_id": plugin_id,
            "history": history,
            "count": len(history),
            "time": now_iso()
        }
    except HTTPException:
        raise
    except (PluginError, ValueError, AttributeError, KeyError) as e:
        raise handle_plugin_error(e, f"Failed to get metrics history for plugin {plugin_id}", 500) from e
    except Exception as e:
        logger.exception(f"Failed to get metrics history for plugin {plugin_id}: Unexpected error")
        raise handle_plugin_error(e, f"Failed to get metrics history for plugin {plugin_id}", 500) from e


# ========== 配置管理路由 ==========

@app.get("/plugin/{plugin_id}/config")
async def get_plugin_config_endpoint(plugin_id: str, _: str = require_admin):
    """
    获取插件配置
    
    - GET /plugin/{plugin_id}/config
    - 需要管理员验证码（Bearer Token）
    """
    try:
        return load_plugin_config(plugin_id)
    except HTTPException:
        raise
    except (PluginError, ValueError, AttributeError, KeyError, OSError) as e:
        raise handle_plugin_error(e, f"Failed to get config for plugin {plugin_id}", 500) from e
    except Exception as e:
        logger.exception(f"Failed to get config for plugin {plugin_id}: Unexpected error")
        raise handle_plugin_error(e, f"Failed to get config for plugin {plugin_id}", 500) from e


@app.get("/plugin/{plugin_id}/config/toml")
async def get_plugin_config_toml_endpoint(plugin_id: str, _: str = require_admin):
    """获取插件配置（TOML 原文）

    - GET /plugin/{plugin_id}/config/toml
    - 需要管理员验证码（Bearer Token）
    """
    try:
        from plugin.server.config_service import load_plugin_config_toml

        return load_plugin_config_toml(plugin_id)
    except HTTPException:
        raise
    except (PluginError, ValueError, AttributeError, KeyError, OSError) as e:
        raise handle_plugin_error(e, f"Failed to get TOML config for plugin {plugin_id}", 500) from e
    except Exception as e:
        logger.exception(f"Failed to get TOML config for plugin {plugin_id}: Unexpected error")
        raise handle_plugin_error(e, f"Failed to get TOML config for plugin {plugin_id}", 500) from e


class ConfigUpdateRequest(BaseModel):
    """配置更新请求"""
    config: dict


class ConfigTomlUpdateRequest(BaseModel):
    """TOML 配置更新请求"""
    toml: str


class ConfigTomlParseRequest(BaseModel):
    """TOML 解析请求（不落盘）"""
    toml: str


class ConfigTomlRenderRequest(BaseModel):
    """TOML 渲染请求（不落盘）"""
    config: dict


class ProfileConfigUpsertRequest(BaseModel):
    """Profile 配置创建/更新请求"""

    config: dict
    make_active: Optional[bool] = None


def validate_config_updates(plugin_id: str, updates: dict) -> None:
    """
    验证配置更新的安全性
    
    禁止修改关键字段，验证字段类型和格式，防止注入攻击。
    
    Args:
        plugin_id: 插件ID
        updates: 要更新的配置部分
    
    Raises:
        HTTPException: 如果配置更新不安全或无效
    """
    # 定义禁止修改的关键字段
    FORBIDDEN_FIELDS = {
        "plugin": ["id", "entry"]  # 插件ID和入口点不允许修改
    }
    
    # 检查是否尝试修改禁止的字段
    for section, forbidden_keys in FORBIDDEN_FIELDS.items():
        if section in updates:
            section_updates = updates[section]
            if isinstance(section_updates, dict):
                for key in forbidden_keys:
                    if key in section_updates:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Cannot modify critical field '{section}.{key}'. This field is protected."
                        )
    
    # 验证 plugin.id 如果存在（防止通过嵌套结构修改）
    def check_nested_forbidden(data: dict, path: str = "") -> None:
        """递归检查嵌套字典中的禁止字段"""
        for key, value in data.items():
            current_path = f"{path}.{key}" if path else key
            
            # 检查 plugin.id 和 plugin.entry
            if current_path == "plugin.id" or current_path == "plugin.entry":
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot modify critical field '{current_path}'. This field is protected."
                )
            
            # 递归检查嵌套字典
            if isinstance(value, dict):
                check_nested_forbidden(value, current_path)
            elif isinstance(value, list):
                # 检查列表中的字典（如 plugin.dependency）
                for idx, item in enumerate(value):
                    if isinstance(item, dict):
                        check_nested_forbidden(item, f"{current_path}[{idx}]")
    
    check_nested_forbidden(updates)
    
    # 验证字段类型和格式
    if "plugin" in updates:
        plugin_updates = updates["plugin"]
        if isinstance(plugin_updates, dict):
            # 验证 name（如果存在）
            if "name" in plugin_updates:
                name = plugin_updates["name"]
                if not isinstance(name, str):
                    raise HTTPException(
                        status_code=400,
                        detail="plugin.name must be a string"
                    )
                if len(name) > 200:  # 防止过长的名称
                    raise HTTPException(
                        status_code=400,
                        detail="plugin.name is too long (max 200 characters)"
                    )
            
            # 验证 version（如果存在）
            if "version" in plugin_updates:
                version = plugin_updates["version"]
                if not isinstance(version, str):
                    raise HTTPException(
                        status_code=400,
                        detail="plugin.version must be a string"
                    )
                # 基本版本格式验证（语义化版本）
                if len(version) > 50:
                    raise HTTPException(
                        status_code=400,
                        detail="plugin.version format is invalid (max 50 characters)"
                    )
            
            # 验证 description（如果存在）
            if "description" in plugin_updates:
                description = plugin_updates["description"]
                if not isinstance(description, str):
                    raise HTTPException(
                        status_code=400,
                        detail="plugin.description must be a string"
                    )
                if len(description) > 5000:  # 防止过长的描述
                    raise HTTPException(
                        status_code=400,
                        detail="plugin.description is too long (max 5000 characters)"
                    )
    
    # 验证 plugin.author（如果存在）
    if "plugin" in updates and isinstance(updates["plugin"], dict):
        if "author" in updates["plugin"]:
            author = updates["plugin"]["author"]
            if isinstance(author, dict):
                if "name" in author and not isinstance(author["name"], str):
                    raise HTTPException(
                        status_code=400,
                        detail="plugin.author.name must be a string"
                    )
                if "email" in author:
                    email = author["email"]
                    if not isinstance(email, str):
                        raise HTTPException(
                            status_code=400,
                            detail="plugin.author.email must be a string"
                        )
                    # 基本邮箱格式验证
                    if "@" not in email or len(email) > 200:
                        raise HTTPException(
                            status_code=400,
                            detail="plugin.author.email format is invalid"
                        )
    
    # 验证 plugin.sdk（如果存在）
    if "plugin" in updates and isinstance(updates["plugin"], dict):
        if "sdk" in updates["plugin"]:
            sdk = updates["plugin"]["sdk"]
            if isinstance(sdk, dict):
                # 验证版本范围字段（应该是字符串或字符串列表）
                for key in ["recommended", "supported", "untested"]:
                    if key in sdk:
                        value = sdk[key]
                        if not isinstance(value, str):
                            raise HTTPException(
                                status_code=400,
                                detail=f"plugin.sdk.{key} must be a string"
                            )
                        if len(value) > 200:
                            raise HTTPException(
                                status_code=400,
                                detail=f"plugin.sdk.{key} is too long (max 200 characters)"
                            )
                
                # 验证 conflicts（应该是字符串列表或布尔值）
                if "conflicts" in sdk:
                    conflicts = sdk["conflicts"]
                    if isinstance(conflicts, bool):
                        pass  # 允许布尔值
                    elif isinstance(conflicts, list):
                        for item in conflicts:
                            if not isinstance(item, str):
                                raise HTTPException(
                                    status_code=400,
                                    detail="plugin.sdk.conflicts must be a list of strings or a boolean"
                                )
                            if len(item) > 200:
                                raise HTTPException(
                                    status_code=400,
                                    detail="plugin.sdk.conflicts items are too long (max 200 characters)"
                                )
                    else:
                        raise HTTPException(
                            status_code=400,
                            detail="plugin.sdk.conflicts must be a list of strings or a boolean"
                        )
    
    # 验证 plugin.dependency（如果存在）
    if "plugin" in updates and isinstance(updates["plugin"], dict):
        if "dependency" in updates["plugin"]:
            dependencies = updates["plugin"]["dependency"]
            if not isinstance(dependencies, list):
                raise HTTPException(
                    status_code=400,
                    detail="plugin.dependency must be a list"
                )
            for dep in dependencies:
                if not isinstance(dep, dict):
                    raise HTTPException(
                        status_code=400,
                        detail="plugin.dependency items must be dictionaries"
                    )
                # 验证依赖字段的类型
                for key in ["id", "entry", "custom_event"]:
                    if key in dep and not isinstance(dep[key], str):
                        raise HTTPException(
                            status_code=400,
                            detail=f"plugin.dependency.{key} must be a string"
                        )
                if "providers" in dep:
                    if not isinstance(dep["providers"], list):
                        raise HTTPException(
                            status_code=400,
                            detail="plugin.dependency.providers must be a list"
                        )
                    for provider in dep["providers"]:
                        if not isinstance(provider, str):
                            raise HTTPException(
                                status_code=400,
                                detail="plugin.dependency.providers items must be strings"
                            )


@app.put("/plugin/{plugin_id}/config")
async def update_plugin_config_endpoint(plugin_id: str, payload: ConfigUpdateRequest, _: str = require_admin):
    """
    更新插件配置
    
    - PUT /plugin/{plugin_id}/config
    - 需要管理员验证码（Bearer Token）
    - 禁止修改关键字段（plugin.id, plugin.entry）
    - 验证字段类型和格式
    """
    try:
        # 验证配置更新的安全性
        validate_config_updates(plugin_id, payload.config)
        
        # 执行更新
        return replace_plugin_config(plugin_id, payload.config)
    except HTTPException:
        raise
    except (PluginError, ValueError, AttributeError, KeyError, OSError) as e:
        raise handle_plugin_error(e, f"Failed to update config for plugin {plugin_id}", 500) from e
    except Exception as e:
        logger.exception(f"Failed to update config for plugin {plugin_id}: Unexpected error")
        raise handle_plugin_error(e, f"Failed to update config for plugin {plugin_id}", 500) from e


@app.post("/plugin/{plugin_id}/config/parse_toml")
async def parse_toml_to_config_endpoint(plugin_id: str, payload: ConfigTomlParseRequest, _: str = require_admin):
    """解析 TOML 原文为配置对象（不落盘）。

    - POST /plugin/{plugin_id}/config/parse_toml
    - 需要管理员验证码（Bearer Token）
    - 禁止修改关键字段（plugin.id, plugin.entry）
    """
    try:
        from plugin.server.config_service import parse_toml_to_config

        return parse_toml_to_config(plugin_id, payload.toml)
    except HTTPException:
        raise
    except (PluginError, ValueError, AttributeError, KeyError, OSError) as e:
        raise handle_plugin_error(e, f"Failed to parse TOML for plugin {plugin_id}", 500) from e
    except Exception as e:
        logger.exception(f"Failed to parse TOML for plugin {plugin_id}: Unexpected error")
        raise handle_plugin_error(e, f"Failed to parse TOML for plugin {plugin_id}", 500) from e


@app.post("/plugin/{plugin_id}/config/render_toml")
async def render_config_to_toml_endpoint(plugin_id: str, payload: ConfigTomlRenderRequest, _: str = require_admin):
    """渲染配置对象为 TOML 原文（不落盘）。

    - POST /plugin/{plugin_id}/config/render_toml
    - 需要管理员验证码（Bearer Token）
    - 禁止修改关键字段（plugin.id, plugin.entry）
    """
    try:
        from plugin.server.config_service import render_config_to_toml

        return render_config_to_toml(plugin_id, payload.config)
    except HTTPException:
        raise
    except (PluginError, ValueError, AttributeError, KeyError, OSError) as e:
        raise handle_plugin_error(e, f"Failed to render TOML for plugin {plugin_id}", 500) from e
    except Exception as e:
        logger.exception(f"Failed to render TOML for plugin {plugin_id}: Unexpected error")
        raise handle_plugin_error(e, f"Failed to render TOML for plugin {plugin_id}", 500) from e


@app.put("/plugin/{plugin_id}/config/toml")
async def update_plugin_config_toml_endpoint(plugin_id: str, payload: ConfigTomlUpdateRequest, _: str = require_admin):
    """更新插件配置（TOML 原文覆盖写入）

    - PUT /plugin/{plugin_id}/config/toml
    - 需要管理员验证码（Bearer Token）
    - 后端会解析 TOML，禁止修改关键字段（plugin.id, plugin.entry）
    """
    try:
        from plugin.server.config_service import update_plugin_config_toml

        return update_plugin_config_toml(plugin_id, payload.toml)
    except HTTPException:
        raise
    except (PluginError, ValueError, AttributeError, KeyError, OSError) as e:
        raise handle_plugin_error(e, f"Failed to update TOML config for plugin {plugin_id}", 500) from e
    except Exception as e:
        logger.exception(f"Failed to update TOML config for plugin {plugin_id}: Unexpected error")
        raise handle_plugin_error(e, f"Failed to update TOML config for plugin {plugin_id}", 500) from e


@app.get("/plugin/{plugin_id}/config/base")
async def get_plugin_base_config_endpoint(plugin_id: str, _: str = require_admin):
    """获取插件基础配置（直接来自 plugin.toml，不包含 profile 叠加）。

    - GET /plugin/{plugin_id}/config/base
    - 需要管理员验证码（Bearer Token）
    """

    try:
        from plugin.server.config_service import load_plugin_base_config

        return load_plugin_base_config(plugin_id)
    except HTTPException:
        raise
    except (PluginError, ValueError, AttributeError, KeyError, OSError) as e:
        raise handle_plugin_error(e, f"Failed to get base config for plugin {plugin_id}", 500) from e
    except Exception as e:
        logger.exception(f"Failed to get base config for plugin {plugin_id}: Unexpected error")
        raise handle_plugin_error(e, f"Failed to get base config for plugin {plugin_id}", 500) from e


@app.get("/plugin/{plugin_id}/config/profiles")
async def get_plugin_profiles_state_endpoint(plugin_id: str, _: str = require_admin):
    """获取插件 profile 配置的整体状态（profiles.toml + 文件存在性）。

    - GET /plugin/{plugin_id}/config/profiles
    - 需要管理员验证码（Bearer Token）
    """

    try:
        from plugin.server.config_service import get_plugin_profiles_state

        return get_plugin_profiles_state(plugin_id)
    except HTTPException:
        raise
    except (PluginError, ValueError, AttributeError, KeyError, OSError) as e:
        raise handle_plugin_error(e, f"Failed to get profiles state for plugin {plugin_id}", 500) from e
    except Exception as e:
        logger.exception(f"Failed to get profiles state for plugin {plugin_id}: Unexpected error")
        raise handle_plugin_error(e, f"Failed to get profiles state for plugin {plugin_id}", 500) from e


@app.get("/plugin/{plugin_id}/config/profiles/{profile_name}")
async def get_plugin_profile_config_endpoint(plugin_id: str, profile_name: str, _: str = require_admin):
    """获取指定 profile 的配置内容（单个 profile TOML 解析结果）。

    - GET /plugin/{plugin_id}/config/profiles/{profile_name}
    - 需要管理员验证码（Bearer Token）
    """

    try:
        from plugin.server.config_service import get_plugin_profile_config

        return get_plugin_profile_config(plugin_id, profile_name)
    except HTTPException:
        raise
    except (PluginError, ValueError, AttributeError, KeyError, OSError) as e:
        raise handle_plugin_error(e, f"Failed to get profile '{profile_name}' for plugin {plugin_id}", 500) from e
    except Exception as e:
        logger.exception(f"Failed to get profile '{profile_name}' for plugin {plugin_id}: Unexpected error")
        raise handle_plugin_error(e, f"Failed to get profile '{profile_name}' for plugin {plugin_id}", 500) from e


@app.put("/plugin/{plugin_id}/config/profiles/{profile_name}")
async def upsert_plugin_profile_config_endpoint(
    plugin_id: str,
    profile_name: str,
    payload: ProfileConfigUpsertRequest,
    _: str = require_admin,
):
    """创建或更新指定 profile 配置，并自动维护 profiles.toml。

    - PUT /plugin/{plugin_id}/config/profiles/{profile_name}
    - 需要管理员验证码（Bearer Token）
    - 禁止在 profile 中定义顶层 [plugin] 段
    """

    try:
        from plugin.server.config_service import upsert_plugin_profile_config

        return upsert_plugin_profile_config(
            plugin_id=plugin_id,
            profile_name=profile_name,
            config=payload.config,
            make_active=payload.make_active,
        )
    except HTTPException:
        raise
    except (PluginError, ValueError, AttributeError, KeyError, OSError) as e:
        raise handle_plugin_error(e, f"Failed to upsert profile '{profile_name}' for plugin {plugin_id}", 500) from e
    except Exception as e:
        logger.exception(f"Failed to upsert profile '{profile_name}' for plugin {plugin_id}: Unexpected error")
        raise handle_plugin_error(e, f"Failed to upsert profile '{profile_name}' for plugin {plugin_id}", 500) from e


@app.delete("/plugin/{plugin_id}/config/profiles/{profile_name}")
async def delete_plugin_profile_config_endpoint(plugin_id: str, profile_name: str, _: str = require_admin):
    """删除指定 profile 的配置映射（不会强制删除 profile 文件本身）。

    - DELETE /plugin/{plugin_id}/config/profiles/{profile_name}
    - 需要管理员验证码（Bearer Token）
    """

    try:
        from plugin.server.config_service import delete_plugin_profile_config

        return delete_plugin_profile_config(plugin_id, profile_name)
    except HTTPException:
        raise
    except (PluginError, ValueError, AttributeError, KeyError, OSError) as e:
        raise handle_plugin_error(e, f"Failed to delete profile '{profile_name}' for plugin {plugin_id}", 500) from e
    except Exception as e:
        logger.exception(f"Failed to delete profile '{profile_name}' for plugin {plugin_id}: Unexpected error")
        raise handle_plugin_error(e, f"Failed to delete profile '{profile_name}' for plugin {plugin_id}", 500) from e


@app.post("/plugin/{plugin_id}/config/profiles/{profile_name}/activate")
async def set_plugin_active_profile_endpoint(plugin_id: str, profile_name: str, _: str = require_admin):
    """设置当前激活的 profile（更新 profiles.toml 中的 active 字段）。

    - POST /plugin/{plugin_id}/config/profiles/{profile_name}/activate
    - 需要管理员验证码（Bearer Token）
    """

    try:
        from plugin.server.config_service import set_plugin_active_profile

        return set_plugin_active_profile(plugin_id, profile_name)
    except HTTPException:
        raise
    except (PluginError, ValueError, AttributeError, KeyError, OSError) as e:
        raise handle_plugin_error(e, f"Failed to set active profile '{profile_name}' for plugin {plugin_id}", 500) from e
    except Exception as e:
        logger.exception(f"Failed to set active profile '{profile_name}' for plugin {plugin_id}: Unexpected error")
        raise handle_plugin_error(e, f"Failed to set active profile '{profile_name}' for plugin {plugin_id}", 500) from e


# ========== 日志路由 ==========

@app.get("/plugin/{plugin_id}/logs")
async def get_plugin_logs_endpoint(
    plugin_id: str,
    lines: int = Query(default=100, ge=1, le=10000),
    level: Optional[str] = Query(default=None, description="日志级别: DEBUG, INFO, WARNING, ERROR"),
    start_time: Optional[str] = Query(default=None),
    end_time: Optional[str] = Query(default=None),
    search: Optional[str] = Query(default=None, description="关键词搜索"),
    _: str = require_admin
):
    """
    获取插件日志或服务器日志
    
    - GET /plugin/{plugin_id}/logs?lines=100&level=INFO&search=error
    - GET /plugin/_server/logs - 获取服务器日志
    - 需要管理员验证码（Bearer Token）
    """
    try:
        result = get_plugin_logs(
            plugin_id=plugin_id,
            lines=lines,
            level=level,
            start_time=start_time,
            end_time=end_time,
            search=search
        )
        # 如果返回结果中包含错误信息，记录但不抛出异常（返回空日志列表）
        if "error" in result:
            logger.warning(f"Error getting logs for {plugin_id}: {result.get('error')}")
        return result
    except (PluginError, ValueError, AttributeError, OSError) as e:
        logger.warning(f"Failed to get logs for plugin {plugin_id}: {e}")
        # 返回空结果而不是抛出异常，避免前端显示错误
        return {
            "plugin_id": plugin_id,
            "logs": [],
            "total_lines": 0,
            "returned_lines": 0,
            "error": "Failed to retrieve logs"
        }
    except Exception:
        logger.exception(f"Failed to get logs for plugin {plugin_id}: Unexpected error type")
        # 返回空结果而不是抛出异常，避免前端显示错误
        return {
            "plugin_id": plugin_id,
            "logs": [],
            "total_lines": 0,
            "returned_lines": 0,
            "error": "Failed to retrieve logs"
        }


@app.get("/plugin/{plugin_id}/logs/files")
async def get_plugin_log_files_endpoint(plugin_id: str, _: str = require_admin):
    """
    获取插件日志文件列表或服务器日志文件列表
    
    - GET /plugin/{plugin_id}/logs/files
    - GET /plugin/_server/logs/files - 获取服务器日志文件列表
    - 需要管理员验证码（Bearer Token）
    """
    try:
        files = get_plugin_log_files(plugin_id)
        return {
            "plugin_id": plugin_id,
            "log_files": files,
            "count": len(files),
            "time": now_iso()
        }
    except HTTPException:
        raise
    except (PluginError, ValueError, AttributeError, OSError) as e:
        raise handle_plugin_error(e, f"Failed to get log files for plugin {plugin_id}", 500) from e
    except Exception as e:
        logger.exception(f"Failed to get log files for plugin {plugin_id}: Unexpected error type")
        raise handle_plugin_error(e, f"Failed to get log files for plugin {plugin_id}", 500) from e


@app.websocket("/ws/logs/{plugin_id}")
async def websocket_log_stream(websocket: WebSocket, plugin_id: str):
    """
    WebSocket 端点：实时推送日志流
    
    - WS /ws/logs/{plugin_id} - 实时接收插件日志
    - WS /ws/logs/_server - 实时接收服务器日志
    - 注意：WebSocket 认证需要在连接时通过查询参数传递验证码
    """
    # WebSocket 认证通过查询参数实现
    code = websocket.query_params.get("code", "").upper()
    from plugin.server.auth import get_admin_code
    admin_code = get_admin_code()
    
    if not admin_code or code != admin_code:
        await websocket.close(code=1008, reason="Authentication required")
        return
    
    await log_stream_endpoint(websocket, plugin_id)


# ========== 主程序入口 ==========

if __name__ == "__main__":
    import uvicorn
    import os
    import signal
    import socket
    import threading
    import faulthandler
    from pathlib import Path
    
    host = "127.0.0.1"  # 默认只暴露本机
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
        # Best-effort: fall back to default stderr behavior.
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
    
    # Two-stage SIGINT:
    # - First Ctrl-C: request graceful shutdown and start a watchdog timer.
    # - Second Ctrl-C (or watchdog timeout): force exit.
    _sigint_count = 0
    _sigint_lock = threading.Lock()
    _force_exit_timer: threading.Timer | None = None

    config = uvicorn.Config(app, host=host, port=selected_port, log_config=None)
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
            # Ask uvicorn to exit.
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

    # Disable uvicorn's internal signal handlers so our two-stage logic takes effect.
    try:
        server.install_signal_handlers = lambda: None  # type: ignore[assignment]
    except Exception:
        pass

    try:
        server.run()
    finally:
        # 强制清理所有子进程
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
            # 尝试使用 psutil 清理子进程（更安全）
            import psutil
            parent = psutil.Process(os.getpid())
            children = parent.children(recursive=True)
            for child in children:
                try:
                    child.terminate()
                except psutil.NoSuchProcess:
                    pass
            
            # Keep this short: we prefer quick exit on Ctrl-C.
            _, alive = psutil.wait_procs(children, timeout=0.5)
            for p in alive:
                try:
                    p.kill()
                except psutil.NoSuchProcess:
                    pass
        except KeyboardInterrupt:
            # Best-effort exit if interrupted.
            pass
        except ImportError:
            # 如果没有 psutil，尝试使用进程组清理（Linux/Mac）
            if hasattr(os, 'killpg'):
                try:
                    os.killpg(os.getpgrp(), signal.SIGKILL)
                except Exception:
                    pass
        except Exception:
            pass

        # Best-effort cancel watchdog timer.
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
