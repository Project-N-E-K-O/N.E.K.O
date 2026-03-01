"""
插件管理路由
"""
import asyncio
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from loguru import logger

from plugin._types.exceptions import PluginError
from plugin.core.status import status_manager
from plugin.server.infrastructure.error_handler import handle_plugin_error
from plugin.server.services import build_plugin_list
from plugin.server.management import start_plugin, stop_plugin, reload_plugin, reload_all_plugins, disable_extension, enable_extension
from plugin.server.infrastructure.utils import now_iso
from plugin.server.infrastructure.auth import require_admin
from plugin.server.infrastructure.executor import _api_executor

router = APIRouter()


@router.get("/plugin/status")
async def plugin_status(plugin_id: Optional[str] = Query(default=None)):
    try:
        loop = asyncio.get_running_loop()
        if plugin_id:
            result = await loop.run_in_executor(_api_executor, status_manager.get_plugin_status, plugin_id)
            if isinstance(result, dict) and "time" not in result:
                result["time"] = now_iso()
            return result
        else:
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


@router.get("/plugins")
async def list_plugins():
    try:
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


@router.post("/plugin/{plugin_id}/start")
async def start_plugin_endpoint(plugin_id: str, _: str = require_admin):
    try:
        return await start_plugin(plugin_id)
    except HTTPException:
        raise
    except (PluginError, ValueError, AttributeError, OSError) as e:
        raise handle_plugin_error(e, f"Failed to start plugin {plugin_id}", 500) from e
    except Exception as e:
        logger.exception(f"Failed to start plugin {plugin_id}: Unexpected error")
        raise handle_plugin_error(e, f"Failed to start plugin {plugin_id}", 500) from e


@router.post("/plugin/{plugin_id}/stop")
async def stop_plugin_endpoint(plugin_id: str, _: str = require_admin):
    try:
        return await stop_plugin(plugin_id)
    except HTTPException:
        raise
    except (PluginError, OSError, TimeoutError) as e:
        raise handle_plugin_error(e, f"Failed to stop plugin {plugin_id}", 500) from e
    except Exception as e:
        logger.exception(f"Failed to stop plugin {plugin_id}: Unexpected error")
        raise handle_plugin_error(e, f"Failed to stop plugin {plugin_id}", 500) from e


@router.post("/plugin/{plugin_id}/reload")
async def reload_plugin_endpoint(plugin_id: str, _: str = require_admin):
    try:
        return await reload_plugin(plugin_id)
    except HTTPException:
        raise
    except (PluginError, OSError, TimeoutError) as e:
        raise handle_plugin_error(e, f"Failed to reload plugin {plugin_id}", 500) from e
    except Exception as e:
        logger.exception(f"Failed to reload plugin {plugin_id}: Unexpected error")
        raise handle_plugin_error(e, f"Failed to reload plugin {plugin_id}", 500) from e


@router.post("/plugins/reload")
async def reload_all_plugins_endpoint(_: str = require_admin):
    """
    重载所有插件
    
    停止所有运行中的插件，然后重新加载。
    用于前端全局重载按钮。
    """
    try:
        return await reload_all_plugins()
    except HTTPException:
        raise
    except (PluginError, OSError, TimeoutError) as e:
        raise handle_plugin_error(e, "Failed to reload all plugins", 500) from e
    except Exception as e:
        logger.exception("Failed to reload all plugins: Unexpected error")
        raise handle_plugin_error(e, "Failed to reload all plugins", 500) from e


@router.post("/plugin/{ext_id}/extension/disable")
async def disable_extension_endpoint(ext_id: str, _: str = require_admin):
    """禁用 Extension：通知宿主进程卸载 Router"""
    try:
        return await disable_extension(ext_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to disable extension {ext_id}")
        raise handle_plugin_error(e, f"Failed to disable extension {ext_id}", 500) from e


@router.post("/plugin/{ext_id}/extension/enable")
async def enable_extension_endpoint(ext_id: str, _: str = require_admin):
    """启用 Extension：通知宿主进程重新注入 Router"""
    try:
        return await enable_extension(ext_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to enable extension {ext_id}")
        raise handle_plugin_error(e, f"Failed to enable extension {ext_id}", 500) from e
