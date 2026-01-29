"""
配置管理路由
"""
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from loguru import logger

from plugin.api.exceptions import PluginError
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
)
from plugin.server.infrastructure.auth import require_admin

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
        return load_plugin_config(plugin_id)
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
        return load_plugin_config_toml(plugin_id)
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
        return replace_plugin_config(plugin_id, payload.config)
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
        return parse_toml_to_config(plugin_id, payload.toml)
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
        return render_config_to_toml(plugin_id, payload.config)
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
        return update_plugin_config_toml(plugin_id, payload.toml)
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
        return load_plugin_base_config(plugin_id)
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
        return get_plugin_profiles_state(plugin_id)
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
        return get_plugin_profile_config(plugin_id, profile_name)
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


@router.delete("/plugin/{plugin_id}/config/profiles/{profile_name}")
async def delete_plugin_profile_config_endpoint(plugin_id: str, profile_name: str, _: str = require_admin):
    try:
        return delete_plugin_profile_config(plugin_id, profile_name)
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
        return set_plugin_active_profile(plugin_id, profile_name)
    except HTTPException:
        raise
    except (PluginError, ValueError, AttributeError, KeyError, OSError) as e:
        raise handle_plugin_error(e, f"Failed to set active profile '{profile_name}' for plugin {plugin_id}", 500) from e
    except Exception as e:
        logger.exception(f"Failed to set active profile '{profile_name}' for plugin {plugin_id}: Unexpected error")
        raise handle_plugin_error(e, f"Failed to set active profile '{profile_name}' for plugin {plugin_id}", 500) from e
