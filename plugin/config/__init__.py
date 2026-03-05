"""
Plugin Config 模块

提供插件配置的读取、校验和更新功能。
"""
from plugin.config.service import (
    load_plugin_config,
    replace_plugin_config,
    update_plugin_config,
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
    deep_merge,
)
from plugin.config.schema import validate_plugin_config, ConfigValidationError
