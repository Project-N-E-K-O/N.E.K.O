"""
插件管理路由
"""
from typing import Optional

from fastapi import APIRouter, Query

from plugin.logging_config import get_logger
from plugin.server.application.plugins import PluginLifecycleService, PluginQueryService
from plugin.server.domain.errors import ServerDomainError
from plugin.server.infrastructure.auth import require_admin
from plugin.server.routes.error_mapping import raise_http_from_domain

router = APIRouter()
logger = get_logger("server.routes.plugins")
query_service = PluginQueryService()
lifecycle_service = PluginLifecycleService()


@router.get("/plugin/status")
async def plugin_status(plugin_id: Optional[str] = Query(default=None)) -> dict[str, object]:
    try:
        return await query_service.get_plugin_status(plugin_id)
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)


@router.get("/plugins")
async def list_plugins() -> dict[str, object]:
    try:
        return await query_service.list_plugins()
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)


@router.post("/plugin/{plugin_id}/start")
async def start_plugin_endpoint(plugin_id: str, _: str = require_admin) -> dict[str, object]:
    try:
        return await lifecycle_service.start_plugin(plugin_id)
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)


@router.post("/plugin/{plugin_id}/stop")
async def stop_plugin_endpoint(plugin_id: str, _: str = require_admin) -> dict[str, object]:
    try:
        return await lifecycle_service.stop_plugin(plugin_id)
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)


@router.post("/plugin/{plugin_id}/reload")
async def reload_plugin_endpoint(plugin_id: str, _: str = require_admin) -> dict[str, object]:
    try:
        return await lifecycle_service.reload_plugin(plugin_id)
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)


@router.post("/plugins/reload")
async def reload_all_plugins_endpoint(_: str = require_admin) -> dict[str, object]:
    """
    重载所有插件
    
    停止所有运行中的插件，然后重新加载。
    用于前端全局重载按钮。
    """
    try:
        return await lifecycle_service.reload_all_plugins()
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)


@router.post("/plugin/{ext_id}/extension/disable")
async def disable_extension_endpoint(ext_id: str, _: str = require_admin) -> dict[str, object]:
    """禁用 Extension：通知宿主进程卸载 Router"""
    try:
        return await lifecycle_service.disable_extension(ext_id)
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)


@router.post("/plugin/{ext_id}/extension/enable")
async def enable_extension_endpoint(ext_id: str, _: str = require_admin) -> dict[str, object]:
    """启用 Extension：通知宿主进程重新注入 Router"""
    try:
        return await lifecycle_service.enable_extension(ext_id)
    except ServerDomainError as error:
        raise_http_from_domain(error, logger=logger)
