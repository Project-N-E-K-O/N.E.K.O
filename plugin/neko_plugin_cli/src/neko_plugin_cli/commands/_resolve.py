"""Shared path resolution helpers for CLI commands."""

from __future__ import annotations

from pathlib import Path

from ..paths import CliDefaults


def resolve_plugin_dirs(*, plugin_names: list[str], pack_all: bool, defaults: CliDefaults) -> list[Path]:
    plugin_root = defaults.plugins_root

    if pack_all:
        plugin_dirs = sorted(
            path.parent.resolve()
            for path in plugin_root.glob("*/plugin.toml")
            if path.is_file()
        )
        if not plugin_dirs:
            raise FileNotFoundError(
                f"no plugin.toml files found under '{plugin_root}'. "
                f"Make sure plugin directories exist and each contains a plugin.toml file."
            )
        return plugin_dirs

    if not plugin_names:
        raise ValueError(
            "no plugin names provided. Specify one or more plugin directory names "
            "(e.g. 'neko-plugin pack my_plugin'), or use --all to pack every plugin "
            f"found under '{plugin_root}'."
        )

    return [resolve_plugin_dir_candidate(item, defaults=defaults) for item in plugin_names]


def resolve_plugin_dir_candidate(raw: str, *, defaults: CliDefaults) -> Path:
    candidate = Path(raw).expanduser()
    if candidate.exists():
        plugin_dir = candidate.resolve()
    else:
        plugin_dir = (defaults.plugins_root / raw).resolve()

    plugin_toml = plugin_dir / "plugin.toml"
    if not plugin_toml.is_file():
        raise FileNotFoundError(
            f"plugin.toml not found for plugin '{raw}'.\n"
            f"  looked in: {plugin_dir}\n"
            f"Make sure the plugin directory name is correct and contains a plugin.toml file. "
            f"Available plugins can be found under '{defaults.plugins_root}'."
        )
    return plugin_dir


def resolve_package_path(raw: str, *, defaults: CliDefaults) -> Path:
    candidate = Path(raw).expanduser()
    if candidate.exists():
        return candidate.resolve()

    target_candidate = (defaults.target_dir / raw).resolve()
    if target_candidate.exists():
        return target_candidate

    raise FileNotFoundError(
        f"package file not found: '{raw}'.\n"
        f"  looked in: {candidate}\n"
        f"  also tried: {target_candidate}\n"
        f"Provide a full path to the .neko-plugin or .neko-bundle file, "
        f"or a filename that exists under '{defaults.target_dir}'."
    )
