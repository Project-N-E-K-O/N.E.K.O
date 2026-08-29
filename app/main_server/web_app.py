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
import hashlib
import os
import re
import secrets
import stat
from pathlib import Path
from urllib.parse import parse_qsl

import httpx
from fastapi import Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import Headers
from starlette.responses import RangeNotSatisfiable
from starlette.staticfiles import NotModifiedResponse

from ._shared import runtime

_IS_MAIN_PROCESS = runtime.is_main_process
_config_manager = runtime.config_manager
_get_app_root = runtime.get_app_root
_resolve_user_plugin_base = runtime.resolve_user_plugin_base
app = runtime.app
logger = runtime.logger
_AVATAR_TOOL_ASSET_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _has_generated_asset_version(query_string: bytes) -> bool:
    """Return whether ``v`` is a content-derived version safe to cache immutably."""
    try:
        query_params = parse_qsl(query_string.decode("ascii"), keep_blank_values=True)
    except UnicodeDecodeError:
        return False

    for key, value in query_params:
        if key != "v":
            continue
        version_tail = value.rsplit("-", 1)[-1]
        if version_tail.isascii() and version_tail.isdigit() and len(version_tail) >= 9:
            return True
    return False


def _avatar_tool_asset_digest_version(query_string: bytes) -> str | None:
    """Return the exact generated avatar-tool digest carried by the query."""
    try:
        query_params = parse_qsl(query_string.decode("ascii"), keep_blank_values=True)
    except UnicodeDecodeError:
        return None
    if not (
        len(query_params) == 1
        and query_params[0][0] == "v"
        and _AVATAR_TOOL_ASSET_DIGEST_PATTERN.fullmatch(query_params[0][1]) is not None
    ):
        return None
    return query_params[0][1]


def _read_avatar_tool_asset(
    path: Path,
    maximum_bytes: int,
) -> tuple[bytes, os.stat_result, str] | None:
    """Read one bounded asset and report the digest of exactly those bytes."""
    with path.open("rb") as stream:
        content = stream.read(maximum_bytes + 1)
        stat_result = os.fstat(stream.fileno())
    if not stat.S_ISREG(stat_result.st_mode):
        return None
    if len(content) > maximum_bytes or stat_result.st_size != len(content):
        return None
    return content, stat_result, hashlib.sha256(content).hexdigest()


class _VerifiedAssetFileResponse(FileResponse):
    """Serve the exact bytes verified for a content-addressed asset URL."""

    _MAX_RANGE_SPECS = 16

    def __init__(self, path: str, content: bytes, stat_result: os.stat_result, digest: str):
        self._verified_content = content
        super().__init__(
            path,
            stat_result=stat_result,
            headers={
                "Cache-Control": "public, max-age=31536000, immutable",
                "ETag": f'"{digest}"',
            },
        )

    @staticmethod
    def _parse_range_header(http_range: str, file_size: int) -> list[tuple[int, int]]:
        # Bound work before Starlette materializes and coalesces every range.
        # Disjoint one-byte ranges otherwise make parsing and multipart headers
        # grow independently of the managed asset-size limit.
        range_spec = http_range.split("=", 1)[-1]
        if range_spec.count(",") + 1 > _VerifiedAssetFileResponse._MAX_RANGE_SPECS:
            raise RangeNotSatisfiable(file_size)
        return FileResponse._parse_range_header(http_range, file_size)

    async def _handle_simple(self, send, send_header_only: bool) -> None:
        await send({"type": "http.response.start", "status": self.status_code, "headers": self.raw_headers})
        body = b"" if send_header_only else self._verified_content
        await send({"type": "http.response.body", "body": body, "more_body": False})

    async def _handle_single_range(
        self, send, start: int, end: int, file_size: int, send_header_only: bool
    ) -> None:
        self.headers["content-range"] = f"bytes {start}-{end - 1}/{file_size}"
        self.headers["content-length"] = str(end - start)
        await send({"type": "http.response.start", "status": 206, "headers": self.raw_headers})
        body = b"" if send_header_only else self._verified_content[start:end]
        await send({"type": "http.response.body", "body": body, "more_body": False})

    async def _handle_multiple_ranges(
        self, send, ranges: list[tuple[int, int]], file_size: int, send_header_only: bool
    ) -> None:
        boundary = secrets.token_hex(13)
        _, header = self.generate_multipart(
            ranges, boundary, file_size, self.headers["content-type"]
        )
        body = b"".join(
            header(start, end) + self._verified_content[start:end] + b"\n"
            for start, end in ranges
        ) + f"\n--{boundary}--\n".encode("latin-1")
        self.headers["content-type"] = f"multipart/byteranges; boundary={boundary}"
        self.headers["content-length"] = str(len(body))
        await send({"type": "http.response.start", "status": 206, "headers": self.raw_headers})
        if send_header_only:
            await send({"type": "http.response.body", "body": b"", "more_body": False})
            return
        await send({"type": "http.response.body", "body": body, "more_body": False})


class CustomStaticFiles(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if path.endswith(".js"):
            response.headers["Content-Type"] = "application/javascript"
        if _has_generated_asset_version(scope.get("query_string", b"")):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


class AvatarToolStaticFiles(CustomStaticFiles):
    """Expose generated media only; private records never enter HTTP space."""

    async def get_response(self, path, scope):
        from starlette.exceptions import HTTPException as StarletteHTTPException
        from utils.avatar_tool_store import (
            AVATAR_TOOL_LIMITS,
            is_public_avatar_tool_resource_path,
        )

        normalized_path = str(path).replace("\\", "/")
        # is_symlink / resolve / is_file 都是同步 FS 调用；存储根指向慢的或
        # 暂时不可用的网络位置时，它们会把整个事件循环卡住。
        if not await asyncio.to_thread(
            is_public_avatar_tool_resource_path, Path(self.directory), normalized_path
        ):
            raise StarletteHTTPException(status_code=404)
        requested_digest = _avatar_tool_asset_digest_version(
            scope.get("query_string", b"")
        )
        if requested_digest is None:
            # 生成的每个 URL 都恰好带一个 64 位十六进制摘要（见 _asset_url）。
            # 没有摘要、摘要畸形、或者多带了参数，都不是本应用发出的请求；放它
            # 走 StaticFiles 会绕过大小上限和内容核验，把手工改动过或同步坏掉的
            # 存储根里的任意字节直接流出去。
            raise StarletteHTTPException(status_code=404)
        try:
            full_path, stat_result = await asyncio.to_thread(
                self.lookup_path,
                normalized_path,
            )
            if stat_result is None or not stat.S_ISREG(stat_result.st_mode):
                raise StarletteHTTPException(status_code=404)
            verified = await asyncio.to_thread(
                _read_avatar_tool_asset,
                Path(full_path),
                (
                    AVATAR_TOOL_LIMITS["maxAudioBytes"]
                    if normalized_path.endswith(".mp3")
                    else AVATAR_TOOL_LIMITS["maxImageBytes"]
                ),
            )
        except OSError as exc:
            raise StarletteHTTPException(status_code=404) from exc
        if verified is None:
            raise StarletteHTTPException(status_code=404)
        content, opened_stat, actual_digest = verified
        if not secrets.compare_digest(actual_digest, requested_digest):
            # 这一层不判定完整性，只回 404。原因是这里拿到的字节和任何 record
            # 都来自两次独立读取：陈旧请求读到 revision N 的文件时，一个原子
            # PUT 可能刚发布 revision N+1，拿旧快照去比新 record 就会把一个刚
            # 更新好的健康道具隔离掉。完整性归 store 的消费点——get_detail 和
            # 互动前的权威读取都在 _STORE_LOCK 内一次性读 record 并核验，没有
            # 这个窗口。用户看到图裂之后点进详情，就会在那里被隔离。
            raise StarletteHTTPException(status_code=404)
        response = _VerifiedAssetFileResponse(
            full_path,
            content,
            opened_stat,
            requested_digest,
        )
        if self.is_not_modified(response.headers, Headers(scope=scope)):
            return NotModifiedResponse(response.headers)
        return response


# 确定 static 目录位置（使用 _get_app_root）
static_dir = os.path.join(_get_app_root(), "static")

app.mount("/static", CustomStaticFiles(directory=static_dir), name="static")

# 挂载用户文档下的live2d目录（只在主进程中执行，子进程不提供HTTP服务）
if _IS_MAIN_PROCESS:
    from utils.avatar_tool_store import AvatarToolStoreError, get_avatar_tool_store

    _config_manager.ensure_live2d_directory()
    _config_manager.ensure_vrm_directory()
    _config_manager.ensure_mmd_directory()
    _config_manager.ensure_pngtuber_directory()
    try:
        get_avatar_tool_store(_config_manager).initialize()
    except AvatarToolStoreError as exc:
        logger.warning("初始化本地 Avatar Tool 存储失败: %s", exc)
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

    user_avatar_tools_path = str(_config_manager.avatar_tools_dir)
    app.mount(
        "/user_avatar_tools",
        AvatarToolStaticFiles(directory=user_avatar_tools_path, check_dir=False),
        name="user_avatar_tools",
    )
    logger.info("已挂载本地Avatar Tool资源目录: %s", user_avatar_tools_path)

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
from main_routers.avatar_tool_router import router as avatar_tool_router  # noqa
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
from main_routers.plugin_media_router import router as plugin_media_router  # noqa
from main_routers.system_router import router as system_router  # noqa
from main_routers.tool_router import router as tool_router  # noqa
from main_routers.vrm_router import router as vrm_router  # noqa
from main_routers.vmc_router import router as vmc_router  # noqa
from main_routers.voice_identity_router import router as voice_identity_router  # noqa
from main_routers.websocket_router import router as websocket_router  # noqa
from main_routers.workshop_router import router as workshop_router  # noqa
from main_routers.cookies_login_router import router as cookies_login_router  # noqa
from main_routers.game_router import router as game_router  # noqa
from main_routers.card_drop_router import (  # noqa
    _facts_cors_headers as _card_drop_cors_headers,
    _local_mutation_origin_allowed as _card_drop_mutation_origin_allowed,
    router as card_drop_router,
)
from main_routers.community_oauth import (  # noqa
    callback_router as community_oauth_callback_router,
    router as community_oauth_router,
)
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


# ── Card-drop cross-process active-character snapshot ──────────────────────
# Community-card native delegation reads this snapshot for the current
# character identity and optional avatar/reference images.
_card_drop_active_character: dict[str, str] = {}


async def _fallback_active_character_identity() -> tuple[str, str]:
    """Use the configured active character when Pet has not posted a snapshot."""
    try:
        master_name, lanlan_name, *_rest = await _config_manager.aget_character_data()
    except Exception:
        return "", ""
    return str(lanlan_name or "").strip(), str(master_name or "").strip()


def _active_character_cors_headers(request: Request) -> dict[str, str] | None:
    """Preserve native local reads; restrict browser reads to the social origin."""
    if not (request.headers.get("origin") or "").strip():
        return {"Cache-Control": "no-store", "Pragma": "no-cache"}
    return _card_drop_cors_headers(request)


@app.post("/api/card-drop/active-character")
async def set_card_drop_active_character(request: Request, payload: dict):
    """Apply supplied fields, dropping avatar payloads that belong to a prior name."""
    if not _card_drop_mutation_origin_allowed(request):
        return JSONResponse({"detail": "origin_not_allowed"}, status_code=403)
    if not isinstance(payload, dict):
        return {"ok": True}
    if "name" in payload:
        next_name = str(payload.get("name") or "")
        if next_name != _card_drop_active_character.get("name", ""):
            for avatar_field in ("dataUrl", "characterReferenceDataUrl"):
                if avatar_field not in payload:
                    _card_drop_active_character.pop(avatar_field, None)
        _card_drop_active_character["name"] = next_name
    if "dataUrl" in payload:
        _card_drop_active_character["dataUrl"] = str(payload.get("dataUrl") or "")
    if "characterReferenceDataUrl" in payload:
        _card_drop_active_character["characterReferenceDataUrl"] = str(
            payload.get("characterReferenceDataUrl") or ""
        )
    return {"ok": True}


@app.options("/api/card-drop/active-character")
async def active_character_options(request: Request):
    """Allow only the configured community origin to read the local snapshot."""
    cors = _active_character_cors_headers(request)
    if cors is None:
        return JSONResponse({"detail": "origin_not_allowed"}, status_code=403)
    return JSONResponse({"ok": True}, headers=cors)


@app.get("/api/card-drop/active-character")
async def get_card_drop_active_character(
    request: Request, include_avatar: bool = False
):
    """Return the active name and optionally the larger avatar payloads."""
    cors = _active_character_cors_headers(request)
    if cors is None:
        return JSONResponse({"detail": "origin_not_allowed"}, status_code=403)
    name = str(_card_drop_active_character.get("name", "") or "").strip()
    master_name = ""
    # Community forge used to treat an empty live snapshot as "本体未连接" even
    # when the local ledger/credits were healthy. Fall back to the configured
    # current catgirl so ticket selection can proceed before Pet avatar sync.
    used_fallback = False
    if not name:
        name, master_name = await _fallback_active_character_identity()
        used_fallback = True
    payload: dict[str, str] = {"name": name}
    if master_name:
        payload["master_name"] = master_name
    if include_avatar and not used_fallback:
        payload["dataUrl"] = _card_drop_active_character.get("dataUrl", "")
        payload["characterReferenceDataUrl"] = _card_drop_active_character.get(
            "characterReferenceDataUrl", ""
        )
    return JSONResponse(payload, headers=cors)


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
app.include_router(plugin_media_router)
# 注意：pages_router 含 /{lanlan_name} 兜底路由，应最后挂载
app.include_router(websocket_router)
app.include_router(agent_router)
app.include_router(avatar_drop_router)
app.include_router(avatar_tool_router)
app.include_router(system_router)
app.include_router(tool_router)
app.include_router(music_router)
app.include_router(galgame_router)
app.include_router(widget_mode_router)
app.include_router(icebreaker_router)
app.include_router(game_router)
app.include_router(card_assist_router)
app.include_router(capture_router)
app.include_router(card_drop_router)  # Must precede the pages fallback router.
app.include_router(community_oauth_router)
app.include_router(community_oauth_callback_router)  # Exact /oauth/callback before pages.
# VMC Protocol OSC sender: REST control plane plus an isolated per-frame
# WebSocket data plane at /api/vmc/ws (kept off the chat/session channel).
app.include_router(vmc_router)
app.include_router(voice_identity_router)
app.include_router(
    cookies_login_router
)  # Cookies登录相关路由，放在最后以避免与其他API路由冲突
app.include_router(
    debug_router
)  # 诊断观测：/api/debug/health（轻量、零侵入，详见 debug_router.py 头注释）
app.include_router(pages_router)  # 兜底路由需最后挂载
