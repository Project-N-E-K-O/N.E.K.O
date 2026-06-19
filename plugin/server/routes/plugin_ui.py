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
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from plugin.logging_config import get_logger
from plugin.server.application.plugins.ui_query_service import PluginUiQueryService
from plugin.server.domain.errors import ServerDomainError
from plugin.server.infrastructure.error_mapping import raise_http_from_domain

router = APIRouter(tags=["plugin-ui"])
logger = get_logger("server.routes.plugin_ui")
plugin_ui_query_service = PluginUiQueryService()

_I18N_LOCALE_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_\-]{0,15}$")
# (mtime, payload) keyed by absolute file path. Bundles are typically <100KB
# and only change when the plugin author edits a translation file, so we keep
# the parsed bytes in process memory and revalidate via mtime on each hit.
_I18N_BUNDLE_CACHE: dict[Path, tuple[float, bytes]] = {}


class HostedUiActionRequest(BaseModel):
    args: dict[str, object] = Field(default_factory=dict)
    kind: str = "panel"
    surface_id: str = "main"
    locale: str | None = None


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


@router.get("/plugin/{plugin_id}/ui-api/locale")
async def plugin_ui_api_locale(plugin_id: str) -> JSONResponse:
    """返回当前生效的全局 UI 语言。

    通用接口，所有静态 UI 插件均可调用：i18n.js 在 init() 时取一次以决定
    要 fetch 哪份翻译 bundle。返回完整 locale（如 `zh-TW`、`en-US`），交给
    前端的 _localeCandidates 自行 fallback。
    """
    try:
        from utils.language_utils import get_global_language_full

        locale = str(get_global_language_full())
    except Exception:
        locale = "en"
    return JSONResponse(
        {"locale": locale},
        headers={"Cache-Control": "no-cache"},
    )


@router.get("/plugin/{plugin_id}/ui-api/i18n/{locale}.json")
async def plugin_ui_api_i18n_bundle(plugin_id: str, locale: str) -> Response:
    """从插件根目录 `i18n/<locale>.json` 提供翻译 bundle。

    通用接口，与 `register_static_ui()` 解耦：只要插件目录下有 `i18n/`
    文件夹即可。i18n.js 通常按 `_localeCandidates` 顺序尝试多个 locale，
    fallback 命中前每个都会发一次 HTTP，因此这里：
      - 用 `_I18N_BUNDLE_CACHE`（按文件 mtime）避免重复读盘；
      - 给 200 响应加 `max-age=300`，让 iframe 之间走浏览器缓存；
      - locale 用正则白名单挡 path-traversal。
    """
    if not _I18N_LOCALE_PATTERN.match(locale):
        raise HTTPException(status_code=404, detail=f"Invalid locale: {locale!r}")

    try:
        plugin_meta = await plugin_ui_query_service.get_plugin_meta(plugin_id)
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)

    if plugin_meta is None:
        raise HTTPException(status_code=404, detail=f"Plugin '{plugin_id}' not found")

    config_path_obj = plugin_meta.get("config_path")
    if not isinstance(config_path_obj, str) or not config_path_obj:
        raise HTTPException(status_code=404, detail=f"Plugin '{plugin_id}' has no config_path")

    try:
        plugin_dir = Path(config_path_obj).parent.resolve()
    except Exception:
        raise HTTPException(status_code=404, detail=f"Plugin '{plugin_id}' config_path invalid")

    bundle_file = (plugin_dir / "i18n" / f"{locale}.json").resolve()
    try:
        bundle_file.relative_to(plugin_dir)
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied: path traversal detected")

    if not bundle_file.is_file():
        raise HTTPException(status_code=404, detail=f"Locale bundle '{locale}' not found")

    try:
        mtime = bundle_file.stat().st_mtime
    except OSError:
        raise HTTPException(status_code=404, detail=f"Locale bundle '{locale}' not readable")

    cached = _I18N_BUNDLE_CACHE.get(bundle_file)
    if cached is None or cached[0] != mtime:
        try:
            payload = bundle_file.read_bytes()
        except OSError:
            raise HTTPException(status_code=500, detail=f"Failed to read locale bundle '{locale}'")
        cached = (mtime, payload)
        _I18N_BUNDLE_CACHE[bundle_file] = cached

    return Response(
        content=cached[1],
        media_type="application/json; charset=utf-8",
        headers={
            "Cache-Control": "public, max-age=300",
            "ETag": f'W/"{plugin_id}-{locale}-{int(mtime)}"',
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
async def plugin_ui_surfaces(plugin_id: str, locale: str | None = None):
    """获取插件统一 UI Surface 列表。

    LEGACY_STATIC_UI_COMPAT:
    Existing static UI is normalized as a mode="static" panel surface.
    """
    try:
        surfaces = await plugin_ui_query_service.get_surfaces(plugin_id, locale=locale)
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)
    return JSONResponse(surfaces)


@router.get("/plugin/{plugin_id}/hosted-ui/source")
async def plugin_hosted_ui_source(
    plugin_id: str,
    kind: str = "panel",
    id: str = "main",
    locale: str | None = None,
):
    """读取 hosted surface 源码。

    用于 hosted-tsx / markdown 的只读 source MVP。`locale` 参数让 markdown
    教程按当前 UI 语言挑同名的 `<entry>.<locale>.md` 兄弟文件，命中失败
    时回退到默认（不带 locale 后缀）的 entry 文件。
    """
    try:
        source = await plugin_ui_query_service.get_surface_source(
            plugin_id,
            kind=kind,
            surface_id=id,
            locale=locale,
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
            locale=request.locale,
        )
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)
    return JSONResponse(result)
