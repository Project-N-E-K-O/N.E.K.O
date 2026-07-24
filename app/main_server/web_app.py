# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Mount static assets and register main-server routers and local endpoints."""

import asyncio
import os
import secrets

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from ._shared import runtime

_IS_MAIN_PROCESS = runtime.is_main_process
_config_manager = runtime.config_manager
_get_app_root = runtime.get_app_root
_resolve_user_plugin_base = runtime.resolve_user_plugin_base
app = runtime.app
logger = runtime.logger


class CustomStaticFiles(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if path.endswith(".js"):
            response.headers["Content-Type"] = "application/javascript"
        return response


# 确定 static 目录位置（使用 _get_app_root）
static_dir = os.path.join(_get_app_root(), "static")

app.mount("/static", CustomStaticFiles(directory=static_dir), name="static")

# 挂载用户文档下的live2d目录（只在主进程中执行，子进程不提供HTTP服务）
if _IS_MAIN_PROCESS:
    _config_manager.ensure_live2d_directory()
    _config_manager.ensure_vrm_directory()
    _config_manager.ensure_mmd_directory()
    _config_manager.ensure_pngtuber_directory()
    _config_manager.ensure_chara_directory()

    # CFA (反勒索防护) 感知挂载：
    # 优先从原始 Documents 目录（可读）提供模型文件，
    # 可写回退目录（AppData）作为辅助挂载供新导入的模型使用
    _readable_live2d = _config_manager.readable_live2d_dir
    _serve_live2d_path = (
        str(_readable_live2d) if _readable_live2d else str(_config_manager.live2d_dir)
    )

    if os.path.exists(_serve_live2d_path):
        app.mount(
            "/user_live2d",
            CustomStaticFiles(directory=_serve_live2d_path),
            name="user_live2d",
        )
        logger.info(f"已挂载用户Live2D目录: {_serve_live2d_path}")

    # CFA 场景：可写回退目录额外挂载，供新导入的模型使用
    if _readable_live2d and str(_config_manager.live2d_dir) != _serve_live2d_path:
        _writable_live2d_path = str(_config_manager.live2d_dir)
        if os.path.exists(_writable_live2d_path):
            app.mount(
                "/user_live2d_local",
                CustomStaticFiles(directory=_writable_live2d_path),
                name="user_live2d_local",
            )
            logger.info(f"已挂载本地Live2D目录(CFA回退): {_writable_live2d_path}")
            if _config_manager.is_windows_cfa_fallback_active:
                logger.info(
                    "检测到 Windows CFA 读写分离模式：Live2D 读取目录=%s，写入目录=%s",
                    _serve_live2d_path,
                    _writable_live2d_path,
                )

    # 挂载VRM动画目录（static/vrm/animation） 必须第一个挂载
    vrm_animation_path = str(_config_manager.vrm_animation_dir)
    if os.path.exists(vrm_animation_path):
        app.mount(
            "/user_vrm/animation",
            CustomStaticFiles(directory=vrm_animation_path),
            name="user_vrm_animation",
        )
        logger.info(f"已挂载VRM动画目录: {vrm_animation_path}")

    # 挂载VRM模型目录（用户文档目录）
    user_vrm_path = str(_config_manager.vrm_dir)
    if os.path.exists(user_vrm_path):
        app.mount(
            "/user_vrm", CustomStaticFiles(directory=user_vrm_path), name="user_vrm"
        )
        logger.info(f"已挂载VRM目录: {user_vrm_path}")

    # 挂载项目目录下的static/vrm（作为备用，如果文件在项目目录中）
    project_vrm_path = os.path.join(static_dir, "vrm")
    if os.path.exists(project_vrm_path) and os.path.isdir(project_vrm_path):
        logger.info(f"项目VRM目录存在: {project_vrm_path} (可通过 /static/vrm/ 访问)")

    # 挂载MMD动画目录（必须在MMD模型目录之前挂载）
    mmd_animation_path = str(_config_manager.mmd_animation_dir)
    if os.path.exists(mmd_animation_path):
        app.mount(
            "/user_mmd/animation",
            CustomStaticFiles(directory=mmd_animation_path),
            name="user_mmd_animation",
        )
        logger.info(f"已挂载MMD动画目录: {mmd_animation_path}")

    # 挂载MMD模型目录（用户文档目录）
    user_mmd_path = str(_config_manager.mmd_dir)
    if os.path.exists(user_mmd_path):
        app.mount(
            "/user_mmd", CustomStaticFiles(directory=user_mmd_path), name="user_mmd"
        )
        logger.info(f"已挂载MMD目录: {user_mmd_path}")

    user_pngtuber_path = str(_config_manager.pngtuber_dir)
    if os.path.exists(user_pngtuber_path):
        app.mount(
            "/user_pngtuber",
            CustomStaticFiles(directory=user_pngtuber_path),
            name="user_pngtuber",
        )
        logger.info(f"已挂载PNGTuber目录: {user_pngtuber_path}")

    # 挂载项目目录下的static/mmd（作为备用）
    project_mmd_path = os.path.join(static_dir, "mmd")
    if os.path.exists(project_mmd_path) and os.path.isdir(project_mmd_path):
        logger.info(f"项目MMD目录存在: {project_mmd_path} (可通过 /static/mmd/ 访问)")

    # 挂载用户mod路径
    user_mod_path = _config_manager.get_workshop_path()
    if os.path.exists(user_mod_path) and os.path.isdir(user_mod_path):
        app.mount(
            "/user_mods", CustomStaticFiles(directory=user_mod_path), name="user_mods"
        )
        logger.info(f"已挂载用户mod路径: {user_mod_path}")

# --- 初始化共享状态并挂载路由 ---
# 显式从各子模块导入 router，避免与包级模块导出产生同名遮蔽。
from main_routers.agent_router import router as agent_router  # noqa
from main_routers.avatar_drop_router import router as avatar_drop_router  # noqa
from main_routers.card_assist_router import router as card_assist_router  # noqa
from main_routers.capture_router import router as capture_router  # noqa
from main_routers.characters_router import router as characters_router  # noqa
from main_routers.cloudsave_router import router as cloudsave_router  # noqa
from main_routers.config_router import router as config_router  # noqa
from main_routers.proactive_router import router as proactive_router  # noqa
from main_routers.galgame_router import router as galgame_router  # noqa
from main_routers.widget_mode_router import router as widget_mode_router  # noqa
from main_routers.icebreaker_router import router as icebreaker_router  # noqa
from main_routers.jukebox_router import router as jukebox_router  # noqa
from main_routers.live2d_router import router as live2d_router  # noqa
from main_routers.memory_router import router as memory_router  # noqa
from main_routers.mmd_router import router as mmd_router  # noqa
from main_routers.music_router import router as music_router  # noqa
from main_routers.pages_router import router as pages_router  # noqa
from main_routers.pngtuber_router import router as pngtuber_router  # noqa
from main_routers.storage_location_router import router as storage_location_router  # noqa
from main_routers.system_router import router as system_router  # noqa
from main_routers.tool_router import router as tool_router  # noqa
from main_routers.vrm_router import router as vrm_router  # noqa
from main_routers.websocket_router import router as websocket_router  # noqa
from main_routers.workshop_router import router as workshop_router  # noqa
from main_routers.cookies_login_router import router as cookies_login_router  # noqa
from main_routers.game_router import router as game_router  # noqa
from main_routers.debug_router import (
    router as debug_router,
    start_watchdog as _start_debug_health_watchdog,
)  # noqa
from main_routers.shared_state import init_shared_state, set_steamworks_initializer  # noqa


# ── 健康检查 / 指纹端点 ──────────────────────────────────────────
@app.get("/health")
async def health():
    """Return a health response carrying the N.E.K.O signature so the launcher/frontend
    can distinguish this service from a random process squatting on the port."""
    from utils.port_utils import build_health_response
    from config import INSTANCE_ID

    return build_health_response("main", instance_id=INSTANCE_ID)


@app.post("/api/beacon/shutdown")
async def beacon_shutdown():
    """Beacon endpoint: used for graceful server shutdown"""
    try:
        # 从 app.state 获取配置
        current_config = runtime.get_start_config()
        # 仅当服务由 --open-browser 模式启动时才响应 beacon
        if current_config["browser_mode_enabled"]:
            logger.info("收到beacon信号，准备关闭服务器...")
            # 调度服务器关闭任务
            asyncio.create_task(runtime.shutdown_server_async())
            return {"success": True, "message": "服务器关闭信号已接收"}
    except Exception as e:
        logger.error(f"Beacon处理错误: {e}")
        return {"success": False, "error": str(e)}


def _runtime_shutdown_has_target() -> bool:
    current_config = runtime.get_start_config()
    if callable(current_config.get("request_runtime_shutdown")):
        return True
    if current_config.get("server") is not None:
        return True

    launcher_pid_raw = os.environ.get("NEKO_LAUNCHER_PID", "").strip()
    if os.name != "nt" and launcher_pid_raw:
        try:
            launcher_pid = int(launcher_pid_raw)
        except ValueError:
            return False
        return launcher_pid > 0 and launcher_pid != os.getpid()

    return False


@app.post("/api/runtime/shutdown")
async def runtime_shutdown(request: Request):
    """Request an authenticated application-level shutdown from the owning desktop app."""
    configured_token = os.environ.get("NEKO_RUNTIME_SHUTDOWN_TOKEN", "").strip()
    if not configured_token:
        return JSONResponse(
            {"success": False, "error": "runtime shutdown is not enabled"},
            status_code=503,
        )

    provided_token = request.headers.get("x-neko-runtime-shutdown-token", "").strip()
    if not provided_token or not secrets.compare_digest(
        configured_token, provided_token
    ):
        return JSONResponse(
            {"success": False, "error": "invalid runtime shutdown token"},
            status_code=403,
        )

    from config import INSTANCE_ID

    provided_instance = request.headers.get("x-neko-instance-id", "").strip()
    if provided_instance and not secrets.compare_digest(
        str(INSTANCE_ID), provided_instance
    ):
        return JSONResponse(
            {"success": False, "error": "runtime instance mismatch"},
            status_code=409,
        )

    if not _runtime_shutdown_has_target():
        return JSONResponse(
            {"success": False, "error": "runtime shutdown target is unavailable"},
            status_code=503,
        )

    shutdown = runtime.request_application_shutdown_async
    if not callable(shutdown):
        return JSONResponse(
            {"success": False, "error": "runtime shutdown bridge is unavailable"},
            status_code=503,
        )

    asyncio.create_task(shutdown(reason="desktop_owner_exit"))
    return JSONResponse(
        {
            "success": True,
            "message": "runtime shutdown accepted",
            "instance_id": str(INSTANCE_ID),
        },
        status_code=202,
    )


@app.api_route(
    "/market/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
)
@app.api_route("/market", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
async def proxy_user_plugin_market_bridge(request: Request, path: str = ""):
    """Proxy plugin-manager Market bridge calls to the user plugin server.

    Vite dev proxies /market to USER_PLUGIN_SERVER_PORT. The packaged UI is
    served by the main server, so it needs the same same-origin bridge here.
    """

    target = f"{_resolve_user_plugin_base()}/market"
    if path:
        target = f"{target}/{path}"
    if request.url.query:
        target = f"{target}?{request.url.query}"

    hop_by_hop = {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
        "host",
        "content-length",
    }
    # Request-side filter additionally drops Accept-Encoding so the upstream
    # is asked for an *uncompressed* response. We can't safely forward the
    # client's Accept-Encoding because httpx auto-decompresses on
    # ``upstream.content`` access — which would leave the response body
    # decompressed but the upstream's ``Content-Encoding: gzip`` header
    # intact, and the browser would double-decompress
    # (ERR_CONTENT_DECODING_FAILED). See bugfix.md §1.1 / §2.1.
    #
    # CC-1 LOCK (PR #1480 review-fix Phase 3): do **NOT** add ``authorization``
    # to ``hop_by_hop_request``. The /market/oauth/* endpoints will (post
    # 2.3.1 / 2.3.2) accept the bridge token via ``Authorization: Bearer``,
    # and that header MUST survive this proxy. Stripping it would silently
    # break Market login.
    hop_by_hop_request = hop_by_hop | {"accept-encoding"}
    headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in hop_by_hop_request
    }

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=3.0), proxy=None, trust_env=False
        ) as client:
            upstream = await client.request(
                request.method,
                target,
                content=await request.body(),
                headers=headers,
            )
    except httpx.HTTPError as exc:
        logger.warning("Market bridge proxy failed: target=%s error=%s", target, exc)
        return JSONResponse(
            status_code=502,
            content={"detail": "Market bridge unavailable", "error": str(exc)},
        )

    # Response-side filter additionally drops Content-Encoding so the body
    # bytes (already decompressed by httpx when we read ``upstream.content``)
    # and the response headers stay consistent. ``Content-Length`` is also
    # dropped because httpx may have changed the byte count during
    # decompression; FastAPI / Starlette will recompute it from the body.
    hop_by_hop_response = hop_by_hop | {"content-encoding", "content-length"}
    response_headers = {
        key: value
        for key, value in upstream.headers.items()
        if key.lower() not in hop_by_hop_response
    }
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=response_headers,
        media_type=upstream.headers.get("content-type"),
    )


# 挂载全部路由
app.include_router(config_router)
app.include_router(proactive_router)
app.include_router(characters_router)
app.include_router(live2d_router)
app.include_router(vrm_router)
app.include_router(mmd_router)
app.include_router(pngtuber_router)
app.include_router(jukebox_router)
app.include_router(workshop_router)
app.include_router(memory_router)
app.include_router(cloudsave_router)
app.include_router(storage_location_router)
# 注意：pages_router 含 /{lanlan_name} 兜底路由，应最后挂载
app.include_router(websocket_router)
app.include_router(agent_router)
app.include_router(avatar_drop_router)
app.include_router(system_router)
app.include_router(tool_router)
app.include_router(music_router)
app.include_router(galgame_router)
app.include_router(widget_mode_router)
app.include_router(icebreaker_router)
app.include_router(game_router)
app.include_router(card_assist_router)
app.include_router(capture_router)
app.include_router(
    cookies_login_router
)  # Cookies登录相关路由，放在最后以避免与其他API路由冲突
app.include_router(
    debug_router
)  # 诊断观测：/api/debug/health（轻量、零侵入，详见 debug_router.py 头注释）
app.include_router(pages_router)  # 兜底路由需最后挂载
