"""
插件 UI 静态文件代理路由

允许插件注入自定义前端界面，通过 iframe 嵌入到主应用中。

插件目录结构：
    my_plugin/
    ├── __init__.py
    ├── plugin.toml
    └── static/           # 静态文件目录
        ├── index.html    # 入口文件
        ├── main.js
        └── style.css

访问路径：
    GET /plugin/{plugin_id}/ui/          -> static/index.html
    GET /plugin/{plugin_id}/ui/main.js   -> static/main.js
    GET /plugin/{plugin_id}/ui/style.css -> static/style.css
"""
import mimetypes
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from fastapi.responses import FileResponse, JSONResponse

from plugin.logging_config import get_logger
from plugin.server.application.plugins.ui_query_service import PluginUiQueryService
from plugin.server.domain.errors import ServerDomainError
from plugin.server.infrastructure.error_mapping import raise_http_from_domain

router = APIRouter(tags=["plugin-ui"])
logger = get_logger("server.routes.plugin_ui")
plugin_ui_query_service = PluginUiQueryService()


class HostedUiActionRequest(BaseModel):
    args: dict[str, object] = Field(default_factory=dict)
    kind: str = "panel"
    surface_id: str = "main"

async def _get_plugin_static_dir(plugin_id: str) -> Path | None:
    """获取插件的静态文件目录
    
    只有插件显式调用 register_static_ui() 后才会返回静态目录。
    
    Args:
        plugin_id: 插件 ID
    
    Returns:
        静态文件目录路径，如果未注册或不存在则返回 None
    """
    return await plugin_ui_query_service.get_static_dir(plugin_id)


async def _get_static_ui_config(plugin_id: str) -> dict[str, object] | None:
    """获取插件的静态 UI 配置"""
    return await plugin_ui_query_service.get_static_ui_config(plugin_id)


def _get_mime_type(file_path: Path) -> str:
    """获取文件的 MIME 类型"""
    mime_type, _ = mimetypes.guess_type(str(file_path))
    if mime_type:
        return mime_type
    
    # 默认类型映射
    suffix = file_path.suffix.lower()
    mime_map = {
        ".html": "text/html",
        ".htm": "text/html",
        ".js": "application/javascript",
        ".mjs": "application/javascript",
        ".css": "text/css",
        ".json": "application/json",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".svg": "image/svg+xml",
        ".ico": "image/x-icon",
        ".woff": "font/woff",
        ".woff2": "font/woff2",
        ".ttf": "font/ttf",
        ".eot": "application/vnd.ms-fontobject",
    }
    return mime_map.get(suffix, "application/octet-stream")


@router.get("/plugin/{plugin_id}/ui")
@router.get("/plugin/{plugin_id}/ui/")
async def plugin_ui_index(plugin_id: str):
    """获取插件 UI 入口页面"""
    try:
        static_dir = await _get_plugin_static_dir(plugin_id)
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)
    
    if not static_dir:
        raise HTTPException(
            status_code=404,
            detail=f"Plugin '{plugin_id}' not found or has no static directory"
        )
    
    index_file = static_dir / "index.html"
    if not index_file.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Plugin '{plugin_id}' has no index.html in static directory"
        )
    
    return FileResponse(
        str(index_file),
        media_type="text/html; charset=utf-8",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
            "X-Frame-Options": "SAMEORIGIN",
        },
    )


@router.get("/plugin/{plugin_id}/ui/{file_path:path}")
async def plugin_ui_file(plugin_id: str, file_path: str):
    """获取插件 UI 静态文件"""
    if not file_path:
        # 重定向到 index
        return await plugin_ui_index(plugin_id)
    
    try:
        static_dir = await _get_plugin_static_dir(plugin_id)
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)
    
    if not static_dir:
        raise HTTPException(
            status_code=404,
            detail=f"Plugin '{plugin_id}' not found or has no static directory"
        )
    
    # 解析文件路径
    target_file = (static_dir / file_path).resolve()
    
    # 安全检查：确保文件在 static 目录内
    try:
        target_file.relative_to(static_dir.resolve())
    except ValueError:
        raise HTTPException(
            status_code=403,
            detail="Access denied: path traversal detected"
        )
    
    if not target_file.exists():
        raise HTTPException(
            status_code=404,
            detail=f"File not found: {file_path}"
        )
    
    if not target_file.is_file():
        raise HTTPException(
            status_code=404,
            detail=f"Not a file: {file_path}"
        )
    
    mime_type = _get_mime_type(target_file)
    
    # 获取缓存控制配置
    try:
        ui_config = await _get_static_ui_config(plugin_id)
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)
    cache_control = "public, max-age=3600"
    if ui_config is not None:
        cache_control_obj = ui_config.get("cache_control")
        if isinstance(cache_control_obj, str) and cache_control_obj:
            cache_control = cache_control_obj
    
    return FileResponse(
        str(target_file),
        media_type=mime_type,
        headers={
            "Cache-Control": cache_control,
            "X-Frame-Options": "SAMEORIGIN",
        },
    )


@router.get("/plugin/{plugin_id}/ui-info")
async def plugin_ui_info(plugin_id: str):
    """获取插件 UI 信息
    
    返回插件是否有 UI、UI 入口路径等信息。
    """
    try:
        ui_info = await plugin_ui_query_service.get_ui_info(plugin_id)
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)
    return JSONResponse(ui_info)


@router.get("/plugin/{plugin_id}/surfaces")
async def plugin_ui_surfaces(plugin_id: str):
    """获取插件统一 UI Surface 列表。

    LEGACY_STATIC_UI_COMPAT:
    Existing static UI is normalized as a mode="static" panel surface.
    """
    try:
        surfaces = await plugin_ui_query_service.get_surfaces(plugin_id)
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)
    return JSONResponse(surfaces)


@router.get("/plugin/{plugin_id}/hosted-ui/source")
async def plugin_hosted_ui_source(plugin_id: str, kind: str = "panel", id: str = "main"):
    """读取 hosted surface 源码。

    当前仅用于 hosted-tsx / markdown 的只读 source MVP。
    """
    try:
        source = await plugin_ui_query_service.get_surface_source(
            plugin_id,
            kind=kind,
            surface_id=id,
        )
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)
    return JSONResponse(source)


@router.get("/plugin/{plugin_id}/hosted-ui/context")
async def plugin_hosted_ui_context(plugin_id: str, kind: str = "panel", id: str = "main", locale: str | None = None):
    """获取 hosted surface 只读上下文。"""
    try:
        context = await plugin_ui_query_service.get_surface_context(
            plugin_id,
            kind=kind,
            surface_id=id,
            locale=locale,
        )
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)
    return JSONResponse(context)


@router.post("/plugin/{plugin_id}/hosted-ui/action/{action_id}")
async def plugin_hosted_ui_action(plugin_id: str, action_id: str, request: HostedUiActionRequest):
    """执行 hosted surface 动作；第一版复用本插件 plugin_entry。"""
    try:
        result = await plugin_ui_query_service.call_surface_action(
            plugin_id,
            action_id=action_id,
            args=request.args,
            kind=request.kind,
            surface_id=request.surface_id,
        )
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)
    return JSONResponse(result)
