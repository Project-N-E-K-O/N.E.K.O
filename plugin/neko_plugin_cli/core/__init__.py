"""Core packaging library — platform-independent, no hardcoded paths."""

from importlib import import_module

# The type facade eagerly re-exports its lightweight version/policy modules.
# Finish that parent before lazy CLI helpers can import different type children
# concurrently and invert their child/parent module-lock order. API models and
# all packaging operations remain lazy; no additional application lock is held.
import_module("plugin._types")

_EXPORTS = {
    "BuildResult": (".models", "BuildResult"),
    "analyze_bundle_plugins": (".bundle_analysis", "analyze_bundle_plugins"),
    "inspect_package": (".inspect", "inspect_package"),
    "build_bundle": (".build", "build_bundle"),
    "build_plugin": (".build", "build_plugin"),
    "install_package": (".install", "install_package"),
}
_SUBMODULES = frozenset({
    "archive_utils", "build", "build_rules", "bundle_analysis", "dependencies",
    "inspect", "install", "metadata_probe", "models", "normalize",
    "plugin_source", "profile", "toml_utils",
})


def __getattr__(name: str):
    if name in _SUBMODULES:
        value = import_module(f".{name}", __name__)
    else:
        export = _EXPORTS.get(name)
        if export is None:
            raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
        module, attr = export
        value = getattr(import_module(module, __name__), attr)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__) | _SUBMODULES)


__all__ = [
    "BuildResult",
    "analyze_bundle_plugins",
    "inspect_package",
    "build_bundle",
    "build_plugin",
    "install_package",
]
