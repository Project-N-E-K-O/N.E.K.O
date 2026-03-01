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
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse

from plugin.core.state import state
from plugin.settings import PLUGIN_CONFIG_ROOT

router = APIRouter(tags=["plugin-ui"])


def _get_plugin_static_dir(plugin_id: str) -> Optional[Path]:
    """获取插件的静态文件目录
    
    只有插件显式调用 register_static_ui() 后才会返回静态目录。
    
    Args:
        plugin_id: 插件 ID
    
    Returns:
        静态文件目录路径，如果未注册或不存在则返回 None
    """
    # 从 state.plugins 获取插件元数据
    with state.acquire_plugins_read_lock():
        plugin_meta = state.plugins.get(plugin_id)
    
    if not plugin_meta:
        return None
    
    # 检查是否显式注册了静态 UI
    static_ui_config = plugin_meta.get("static_ui_config")
    if static_ui_config and static_ui_config.get("enabled"):
        # 使用显式注册的目录
        directory = static_ui_config.get("directory")
        if directory:
            static_dir = Path(directory)
            if static_dir.exists() and static_dir.is_dir():
                return static_dir
    
    # 未显式注册，不自动挂载
    return None


def _get_static_ui_config(plugin_id: str) -> Optional[dict]:
    """获取插件的静态 UI 配置"""
    with state.acquire_plugins_read_lock():
        plugin_meta = state.plugins.get(plugin_id)
    
    if not plugin_meta:
        return None
    
    return plugin_meta.get("static_ui_config")


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
    static_dir = _get_plugin_static_dir(plugin_id)
    
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
    
    static_dir = _get_plugin_static_dir(plugin_id)
    
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
    ui_config = _get_static_ui_config(plugin_id)
    cache_control = "public, max-age=3600"
    if ui_config:
        cache_control = ui_config.get("cache_control", cache_control)
    
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
    static_dir = _get_plugin_static_dir(plugin_id)
    
    has_ui = static_dir is not None and (static_dir / "index.html").exists()
    
    # 获取插件元数据
    with state.acquire_plugins_read_lock():
        plugin_meta = state.plugins.get(plugin_id)
    
    if not plugin_meta:
        raise HTTPException(
            status_code=404,
            detail=f"Plugin '{plugin_id}' not found"
        )
    
    # 列出静态文件（如果有）
    static_files = []
    if static_dir and static_dir.exists():
        for f in static_dir.rglob("*"):
            if f.is_file():
                rel_path = f.relative_to(static_dir)
                static_files.append(str(rel_path))
    
    # 获取静态 UI 配置
    ui_config = _get_static_ui_config(plugin_id)
    
    return JSONResponse({
        "plugin_id": plugin_id,
        "has_ui": has_ui,
        "explicitly_registered": ui_config is not None and ui_config.get("enabled", False),
        "ui_path": f"/plugin/{plugin_id}/ui/" if has_ui else None,
        "static_dir": str(static_dir) if static_dir else None,
        "static_files": static_files[:50],  # 限制返回数量
        "static_files_count": len(static_files),
    })
