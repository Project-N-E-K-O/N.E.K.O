"""
配置管理路由
"""
import asyncio
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from loguru import logger

from plugin._types.exceptions import PluginError
from plugin.server.infrastructure.error_handler import handle_plugin_error
from plugin.server.config_service import (
    load_plugin_config,
    replace_plugin_config,
    load_plugin_config_toml,
    parse_toml_to_config,
    render_config_to_toml,
    update_plugin_config_toml,
    load_plugin_base_config,
    get_plugin_profiles_state,
    get_plugin_profile_config,
    upsert_plugin_profile_config,
    delete_plugin_profile_config,
    set_plugin_active_profile,
    hot_update_plugin_config,
)
from plugin.server.infrastructure.auth import require_admin
from plugin.server.infrastructure.executor import _api_executor

router = APIRouter()


class ConfigUpdateRequest(BaseModel):
    config: dict


class ConfigTomlUpdateRequest(BaseModel):
    toml: str


class ConfigTomlParseRequest(BaseModel):
    toml: str


class ConfigTomlRenderRequest(BaseModel):
    config: dict


class ProfileConfigUpsertRequest(BaseModel):
    config: dict
    make_active: Optional[bool] = None


class HotUpdateConfigRequest(BaseModel):
    """热更新配置请求"""
    config: dict
    mode: str = "temporary"  # "temporary" | "permanent"
    profile: Optional[str] = None  # permanent 模式时使用的 profile 名称


def validate_config_updates(plugin_id: str, updates: dict) -> None:
    FORBIDDEN_FIELDS = {
        "plugin": ["id", "entry"]
    }
    
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
    
    def check_nested_forbidden(data: dict, path: str = "") -> None:
        for key, value in data.items():
            current_path = f"{path}.{key}" if path else key
            
            if current_path == "plugin.id" or current_path == "plugin.entry":
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot modify critical field '{current_path}'. This field is protected."
                )
            
            if isinstance(value, dict):
                check_nested_forbidden(value, current_path)
            elif isinstance(value, list):
                for idx, item in enumerate(value):
                    if isinstance(item, dict):
                        check_nested_forbidden(item, f"{current_path}[{idx}]")
    
    check_nested_forbidden(updates)
    
    if "plugin" in updates:
        plugin_updates = updates["plugin"]
        if isinstance(plugin_updates, dict):
            if "name" in plugin_updates:
                name = plugin_updates["name"]
                if not isinstance(name, str):
                    raise HTTPException(
                        status_code=400,
                        detail="plugin.name must be a string"
                    )
                if len(name) > 200:
                    raise HTTPException(
                        status_code=400,
                        detail="plugin.name is too long (max 200 characters)"
                    )
            
            if "version" in plugin_updates:
                version = plugin_updates["version"]
                if not isinstance(version, str):
                    raise HTTPException(
                        status_code=400,
                        detail="plugin.version must be a string"
                    )
                if len(version) > 50:
                    raise HTTPException(
                        status_code=400,
                        detail="plugin.version format is invalid (max 50 characters)"
                    )
            
            if "description" in plugin_updates:
                description = plugin_updates["description"]
                if not isinstance(description, str):
                    raise HTTPException(
                        status_code=400,
                        detail="plugin.description must be a string"
                    )
                if len(description) > 5000:
                    raise HTTPException(
                        status_code=400,
                        detail="plugin.description is too long (max 5000 characters)"
                    )
    
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
                    if "@" not in email or len(email) > 200:
                        raise HTTPException(
                            status_code=400,
                            detail="plugin.author.email format is invalid"
                        )
    
    if "plugin" in updates and isinstance(updates["plugin"], dict):
        if "sdk" in updates["plugin"]:
            sdk = updates["plugin"]["sdk"]
            if isinstance(sdk, dict):
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
                
                if "conflicts" in sdk:
                    conflicts = sdk["conflicts"]
                    if isinstance(conflicts, bool):
                        pass
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


@router.get("/plugin/{plugin_id}/config")
async def get_plugin_config_endpoint(plugin_id: str, _: str = require_admin):
    try:
        # 使用线程池执行文件 I/O，避免阻塞事件循环
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(_api_executor, load_plugin_config, plugin_id)
    except HTTPException:
        raise
    except (PluginError, ValueError, AttributeError, KeyError, OSError) as e:
        raise handle_plugin_error(e, f"Failed to get config for plugin {plugin_id}", 500) from e
    except Exception as e:
        logger.exception(f"Failed to get config for plugin {plugin_id}: Unexpected error")
        raise handle_plugin_error(e, f"Failed to get config for plugin {plugin_id}", 500) from e


@router.get("/plugin/{plugin_id}/config/toml")
async def get_plugin_config_toml_endpoint(plugin_id: str, _: str = require_admin):
    try:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(_api_executor, load_plugin_config_toml, plugin_id)
    except HTTPException:
        raise
    except (PluginError, ValueError, AttributeError, KeyError, OSError) as e:
        raise handle_plugin_error(e, f"Failed to get TOML config for plugin {plugin_id}", 500) from e
    except Exception as e:
        logger.exception(f"Failed to get TOML config for plugin {plugin_id}: Unexpected error")
        raise handle_plugin_error(e, f"Failed to get TOML config for plugin {plugin_id}", 500) from e


@router.put("/plugin/{plugin_id}/config")
async def update_plugin_config_endpoint(plugin_id: str, payload: ConfigUpdateRequest, _: str = require_admin):
    try:
        validate_config_updates(plugin_id, payload.config)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(_api_executor, replace_plugin_config, plugin_id, payload.config)
    except HTTPException:
        raise
    except (PluginError, ValueError, AttributeError, KeyError, OSError) as e:
        raise handle_plugin_error(e, f"Failed to update config for plugin {plugin_id}", 500) from e
    except Exception as e:
        logger.exception(f"Failed to update config for plugin {plugin_id}: Unexpected error")
        raise handle_plugin_error(e, f"Failed to update config for plugin {plugin_id}", 500) from e


@router.post("/plugin/{plugin_id}/config/parse_toml")
async def parse_toml_to_config_endpoint(plugin_id: str, payload: ConfigTomlParseRequest, _: str = require_admin):
    try:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(_api_executor, parse_toml_to_config, plugin_id, payload.toml)
    except HTTPException:
        raise
    except (PluginError, ValueError, AttributeError, KeyError, OSError) as e:
        raise handle_plugin_error(e, f"Failed to parse TOML for plugin {plugin_id}", 500) from e
    except Exception as e:
        logger.exception(f"Failed to parse TOML for plugin {plugin_id}: Unexpected error")
        raise handle_plugin_error(e, f"Failed to parse TOML for plugin {plugin_id}", 500) from e


@router.post("/plugin/{plugin_id}/config/render_toml")
async def render_config_to_toml_endpoint(plugin_id: str, payload: ConfigTomlRenderRequest, _: str = require_admin):
    try:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(_api_executor, render_config_to_toml, plugin_id, payload.config)
    except HTTPException:
        raise
    except (PluginError, ValueError, AttributeError, KeyError, OSError) as e:
        raise handle_plugin_error(e, f"Failed to render TOML for plugin {plugin_id}", 500) from e
    except Exception as e:
        logger.exception(f"Failed to render TOML for plugin {plugin_id}: Unexpected error")
        raise handle_plugin_error(e, f"Failed to render TOML for plugin {plugin_id}", 500) from e


@router.put("/plugin/{plugin_id}/config/toml")
async def update_plugin_config_toml_endpoint(plugin_id: str, payload: ConfigTomlUpdateRequest, _: str = require_admin):
    try:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(_api_executor, update_plugin_config_toml, plugin_id, payload.toml)
    except HTTPException:
        raise
    except (PluginError, ValueError, AttributeError, KeyError, OSError) as e:
        raise handle_plugin_error(e, f"Failed to update TOML config for plugin {plugin_id}", 500) from e
    except Exception as e:
        logger.exception(f"Failed to update TOML config for plugin {plugin_id}: Unexpected error")
        raise handle_plugin_error(e, f"Failed to update TOML config for plugin {plugin_id}", 500) from e


@router.get("/plugin/{plugin_id}/config/base")
async def get_plugin_base_config_endpoint(plugin_id: str, _: str = require_admin):
    try:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(_api_executor, load_plugin_base_config, plugin_id)
    except HTTPException:
        raise
    except (PluginError, ValueError, AttributeError, KeyError, OSError) as e:
        raise handle_plugin_error(e, f"Failed to get base config for plugin {plugin_id}", 500) from e
    except Exception as e:
        logger.exception(f"Failed to get base config for plugin {plugin_id}: Unexpected error")
        raise handle_plugin_error(e, f"Failed to get base config for plugin {plugin_id}", 500) from e


@router.get("/plugin/{plugin_id}/config/profiles")
async def get_plugin_profiles_state_endpoint(plugin_id: str, _: str = require_admin):
    try:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(_api_executor, get_plugin_profiles_state, plugin_id)
    except HTTPException:
        raise
    except (PluginError, ValueError, AttributeError, KeyError, OSError) as e:
        raise handle_plugin_error(e, f"Failed to get profiles state for plugin {plugin_id}", 500) from e
    except Exception as e:
        logger.exception(f"Failed to get profiles state for plugin {plugin_id}: Unexpected error")
        raise handle_plugin_error(e, f"Failed to get profiles state for plugin {plugin_id}", 500) from e


@router.get("/plugin/{plugin_id}/config/profiles/{profile_name}")
async def get_plugin_profile_config_endpoint(plugin_id: str, profile_name: str, _: str = require_admin):
    try:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(_api_executor, get_plugin_profile_config, plugin_id, profile_name)
    except HTTPException:
        raise
    except (PluginError, ValueError, AttributeError, KeyError, OSError) as e:
        raise handle_plugin_error(e, f"Failed to get profile '{profile_name}' for plugin {plugin_id}", 500) from e
    except Exception as e:
        logger.exception(f"Failed to get profile '{profile_name}' for plugin {plugin_id}: Unexpected error")
        raise handle_plugin_error(e, f"Failed to get profile '{profile_name}' for plugin {plugin_id}", 500) from e


@router.put("/plugin/{plugin_id}/config/profiles/{profile_name}")
async def upsert_plugin_profile_config_endpoint(
    plugin_id: str,
    profile_name: str,
    payload: ProfileConfigUpsertRequest,
    _: str = require_admin,
):
    try:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            _api_executor,
            lambda: upsert_plugin_profile_config(
                plugin_id=plugin_id,
                profile_name=profile_name,
                config=payload.config,
                make_active=payload.make_active,
            )
        )
    except HTTPException:
        raise
    except (PluginError, ValueError, AttributeError, KeyError, OSError) as e:
        raise handle_plugin_error(e, f"Failed to upsert profile '{profile_name}' for plugin {plugin_id}", 500) from e
    except Exception as e:
        logger.exception(f"Failed to upsert profile '{profile_name}' for plugin {plugin_id}: Unexpected error")
        raise handle_plugin_error(e, f"Failed to upsert profile '{profile_name}' for plugin {plugin_id}", 500) from e


@router.delete("/plugin/{plugin_id}/config/profiles/{profile_name}")
async def delete_plugin_profile_config_endpoint(plugin_id: str, profile_name: str, _: str = require_admin):
    try:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(_api_executor, delete_plugin_profile_config, plugin_id, profile_name)
    except HTTPException:
        raise
    except (PluginError, ValueError, AttributeError, KeyError, OSError) as e:
        raise handle_plugin_error(e, f"Failed to delete profile '{profile_name}' for plugin {plugin_id}", 500) from e
    except Exception as e:
        logger.exception(f"Failed to delete profile '{profile_name}' for plugin {plugin_id}: Unexpected error")
        raise handle_plugin_error(e, f"Failed to delete profile '{profile_name}' for plugin {plugin_id}", 500) from e


@router.post("/plugin/{plugin_id}/config/profiles/{profile_name}/activate")
async def set_plugin_active_profile_endpoint(plugin_id: str, profile_name: str, _: str = require_admin):
    try:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(_api_executor, set_plugin_active_profile, plugin_id, profile_name)
    except HTTPException:
        raise
    except (PluginError, ValueError, AttributeError, KeyError, OSError) as e:
        raise handle_plugin_error(e, f"Failed to set active profile '{profile_name}' for plugin {plugin_id}", 500) from e
    except Exception as e:
        logger.exception(f"Failed to set active profile '{profile_name}' for plugin {plugin_id}: Unexpected error")
        raise handle_plugin_error(e, f"Failed to set active profile '{profile_name}' for plugin {plugin_id}", 500) from e


@router.post("/plugin/{plugin_id}/config/hot-update")
async def hot_update_plugin_config_endpoint(plugin_id: str, payload: HotUpdateConfigRequest, _: str = require_admin):
    """
    热更新插件配置（不需要重启插件）。
    
    支持两种模式：
    - temporary: 临时更新，只修改插件进程内缓存，不写入文件。插件重启后配置会恢复。
    - permanent: 永久更新，写入 profile 文件，并通知插件进程更新缓存。
    
    请求体：
    - config: 要更新的配置部分（会与现有配置深度合并）
    - mode: "temporary" | "permanent"
    - profile: profile 名称（permanent 模式时使用，None 表示使用当前激活的 profile）
    """
    try:
        # 验证配置更新
        validate_config_updates(plugin_id, payload.config)
        
        # hot_update_plugin_config 是异步函数，直接 await
        # 这样可以在同一个事件循环中执行，确保 _pending_futures 机制正常工作
        return await hot_update_plugin_config(
            plugin_id=plugin_id,
            updates=payload.config,
            mode=payload.mode,
            profile=payload.profile,
        )
    except HTTPException:
        raise
    except (PluginError, ValueError, AttributeError, KeyError, OSError) as e:
        raise handle_plugin_error(e, f"Failed to hot-update config for plugin {plugin_id}", 500) from e
    except Exception as e:
        logger.exception(f"Failed to hot-update config for plugin {plugin_id}: Unexpected error")
        raise handle_plugin_error(e, f"Failed to hot-update config for plugin {plugin_id}", 500) from e
