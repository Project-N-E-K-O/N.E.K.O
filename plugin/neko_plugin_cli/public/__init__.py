"""Public surface for plugin package helpers."""

from __future__ import annotations

from importlib import import_module

_EXPORTS = {
    "BuildResult": ("..core", "BuildResult"),
    "analyze_bundle_plugins": ("..core", "analyze_bundle_plugins"),
    "inspect_package": ("..core", "inspect_package"),
    "build_bundle": ("..core", "build_bundle"),
    "build_plugin": ("..core", "build_plugin"),
    "install_package": ("..core", "install_package"),
    "PackResult": (".models", "PackResult"),
    "UnpackResult": (".models", "UnpackResult"),
    "UnpackedPlugin": (".models", "UnpackedPlugin"),
    "pack_plugin": (".pack", "pack_plugin"),
    "pack_bundle": (".pack", "pack_bundle"),
    "unpack_package": (".unpack", "unpack_package"),
}
_SUBMODULES = frozenset({
    "archive_utils", "models", "pack", "pack_rules", "plugin_source", "profile",
    "toml_utils", "unpack",
})


def __getattr__(name: str):
    if name in _SUBMODULES:
        value = import_module(f".{name}", __name__)
    else:
        export = _EXPORTS.get(name)
        if export is None:
            raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
        module, attribute = export
        value = getattr(import_module(module, __name__), attribute)
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
    # Legacy aliases
    "PackResult",
    "UnpackResult",
    "UnpackedPlugin",
    "pack_plugin",
    "pack_bundle",
    "unpack_package",
]
