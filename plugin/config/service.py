"""
插件配置服务

提供插件配置的读取和更新功能。
"""
from pathlib import Path
from typing import Any, Dict, Optional

from plugin.logging_config import logger

from fastapi import HTTPException


def get_plugin_config_path(plugin_id: str) -> Path:
    from plugin.server.infrastructure.config_paths import get_plugin_config_path as _impl

    return _impl(plugin_id)


def load_plugin_config(plugin_id: str, *, validate: bool = True) -> Dict[str, Any]:
    from plugin.server.infrastructure.config_queries import load_plugin_config as _impl

    return _impl(plugin_id, validate=validate)


def load_plugin_base_config(plugin_id: str) -> Dict[str, Any]:
    from plugin.server.infrastructure.config_queries import load_plugin_base_config as _impl

    return _impl(plugin_id)


def load_plugin_config_toml(plugin_id: str) -> Dict[str, Any]:
    from plugin.server.infrastructure.config_queries import load_plugin_config_toml as _impl

    return _impl(plugin_id)


def deep_merge(base: Dict[str, Any], updates: Dict[str, Any]) -> Dict[str, Any]:
    from plugin.server.infrastructure.config_merge import deep_merge as _impl

    return _impl(base, updates)


def validate_config_strict(config_data: Dict[str, Any], plugin_id: str) -> None:
    """
    严格验证插件配置（验证失败会抛出异常）
    
    Args:
        config_data: 配置数据字典
        plugin_id: 插件ID
    
    Raises:
        HTTPException: 验证失败时抛出 400 错误
    """
    try:
        from plugin.config.schema import validate_plugin_config, ConfigValidationError
    except ImportError as ie:
        raise HTTPException(
            status_code=500,
            detail="配置验证模块不可用"
        ) from ie
    
    try:
        validate_plugin_config(config_data)
    except ConfigValidationError as e:
        raise HTTPException(
            status_code=400,
            detail=f"配置验证失败: {e.message}" + (f" (字段: {e.field})" if e.field else "")
        ) from e
    except Exception as e:
        logger.warning(
            "Plugin {}: strict schema validation error: {}",
            plugin_id, str(e)
        )
        raise HTTPException(
            status_code=400,
            detail=f"配置验证错误: {str(e)}"
        ) from e


def _apply_user_config_profiles(
    *, plugin_id: str, base_config: Dict[str, Any], config_path: Path
) -> Dict[str, Any]:
    from plugin.server.infrastructure.config_profiles import apply_user_config_profiles as _impl

    return _impl(
        plugin_id=plugin_id,
        base_config=base_config,
        config_path=config_path,
    )


def get_plugin_profiles_state(plugin_id: str) -> Dict[str, Any]:
    from plugin.server.infrastructure.config_profiles import get_profiles_state as _impl

    return _impl(
        plugin_id=plugin_id,
        config_path=get_plugin_config_path(plugin_id),
    )


def get_plugin_profile_config(plugin_id: str, profile_name: str) -> Dict[str, Any]:
    from plugin.server.infrastructure.config_profiles import get_profile_config as _impl

    return _impl(
        plugin_id=plugin_id,
        profile_name=profile_name,
        config_path=get_plugin_config_path(plugin_id),
    )


def upsert_plugin_profile_config(
    plugin_id: str,
    profile_name: str,
    config: Dict[str, Any],
    make_active: Optional[bool] = None,
) -> Dict[str, Any]:
    from plugin.server.infrastructure.config_profiles_write import upsert_profile_config as _impl

    return _impl(
        plugin_id=plugin_id,
        profile_name=profile_name,
        config=config,
        make_active=make_active,
    )


def delete_plugin_profile_config(plugin_id: str, profile_name: str) -> Dict[str, Any]:
    from plugin.server.infrastructure.config_profiles_write import delete_profile_config as _impl

    return _impl(
        plugin_id=plugin_id,
        profile_name=profile_name,
    )


def set_plugin_active_profile(plugin_id: str, profile_name: str) -> Dict[str, Any]:
    from plugin.server.infrastructure.config_profiles_write import set_active_profile as _impl

    return _impl(
        plugin_id=plugin_id,
        profile_name=profile_name,
    )


def replace_plugin_config(plugin_id: str, new_config: Dict[str, Any]) -> Dict[str, Any]:
    from plugin.server.infrastructure.config_updates import replace_plugin_config as _impl

    return _impl(plugin_id, new_config)


def update_plugin_config(plugin_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
    from plugin.server.infrastructure.config_updates import update_plugin_config as _impl

    return _impl(plugin_id, updates)


def update_plugin_config_toml(plugin_id: str, toml_text: str) -> Dict[str, Any]:
    from plugin.server.infrastructure.config_updates import update_plugin_config_toml as _impl

    return _impl(plugin_id, toml_text)


def parse_toml_to_config(plugin_id: str, toml_text: str) -> Dict[str, Any]:
    from plugin.server.infrastructure.config_queries import parse_toml_to_config as _impl

    return _impl(plugin_id, toml_text)


def render_config_to_toml(plugin_id: str, config: Dict[str, Any]) -> Dict[str, Any]:
    from plugin.server.infrastructure.config_queries import render_config_to_toml as _impl

    return _impl(plugin_id, config)


async def hot_update_plugin_config(
    plugin_id: str,
    updates: Dict[str, Any],
    mode: str = "temporary",
    profile: Optional[str] = None,
    timeout: float = 10.0,
) -> Dict[str, Any]:
    from plugin.server.application.config.hot_update_service import hot_update_plugin_config as _impl
    from plugin.server.domain.errors import ServerDomainError

    try:
        return await _impl(
            plugin_id=plugin_id,
            updates=updates,
            mode=mode,
            profile=profile,
            timeout=timeout,
        )
    except ServerDomainError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
