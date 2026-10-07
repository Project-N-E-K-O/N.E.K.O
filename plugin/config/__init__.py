"""Public plugin configuration helpers, loaded on first use."""

from importlib import import_module

_EXPORTS = {
    "load_plugin_config": ".service",
    "replace_plugin_config": ".service",
    "update_plugin_config": ".service",
    "load_plugin_config_toml": ".service",
    "parse_toml_to_config": ".service",
    "render_config_to_toml": ".service",
    "update_plugin_config_toml": ".service",
    "load_plugin_base_config": ".service",
    "get_plugin_profiles_state": ".service",
    "get_plugin_profile_config": ".service",
    "upsert_plugin_profile_config": ".service",
    "delete_plugin_profile_config": ".service",
    "set_plugin_active_profile": ".service",
    "hot_update_plugin_config": ".service",
    "deep_merge": ".service",
    "validate_plugin_config": ".schema",
    "ConfigValidationError": ".schema",
}
_SUBMODULES = frozenset({"service", "schema"})


def __getattr__(name: str):
    if name in _SUBMODULES:
        value = import_module(f".{name}", __name__)
    else:
        module = _EXPORTS.get(name)
        if module is None:
            raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
        value = getattr(import_module(module, __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))


__all__ = [*_EXPORTS, "service", "schema"]
