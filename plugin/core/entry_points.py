from __future__ import annotations

from pathlib import Path


def _is_same_or_within(path: Path, base: Path) -> bool:
    try:
        resolved = path.resolve()
        resolved_base = base.resolve()
        return resolved == resolved_base or resolved.is_relative_to(resolved_base)
    except Exception:
        return False


def normalize_plugin_entry_point(
    entry_point: str,
    *,
    config_path: Path,
    builtin_plugin_root: Path,
) -> str:
    """Normalize canonical manifest entries for user-installed plugins.

    Plugin manifests use ``plugin.plugins.<plugin_id>:Class`` as the canonical
    source-tree entry path. Market packages are installed under the user plugin
    root as ``plugins/<plugin_id>`` at runtime, so entries found outside the
    built-in root are rewritten to the user-root import namespace.
    """

    if ":" not in entry_point:
        return entry_point

    module_path, class_name = entry_point.split(":", 1)
    canonical_prefix = "plugin.plugins."
    if not module_path.startswith(canonical_prefix):
        return entry_point

    plugin_dir = config_path.parent
    if _is_same_or_within(plugin_dir, builtin_plugin_root):
        return entry_point

    suffix = module_path[len(canonical_prefix):]
    if not suffix:
        return entry_point
    return f"plugins.{suffix}:{class_name}"


def describe_plugin_entry_directory_mismatch(entry_point: str, *, config_path: Path) -> str | None:
    """Return an error message when a ``plugins.<name>`` entry targets another directory.

    User plugins are loaded from ``<plugin-root>/<directory>/plugin.toml``.  If
    an entry points at ``plugins.some_id`` but the package directory is not
    ``some_id``, the normal namespace import cannot resolve the plugin package.
    Treat that as a packaging/config error instead of silently loading a
    different directory.
    """

    if ":" not in entry_point:
        return None

    module_path, _class_name = entry_point.split(":", 1)
    parts = module_path.split(".")
    if len(parts) < 2 or parts[0] != "plugins":
        return None

    entry_package = parts[1]
    plugin_dir_name = config_path.parent.name
    if entry_package == plugin_dir_name:
        return None

    return (
        f"Plugin entry '{entry_point}' targets package 'plugins.{entry_package}', "
        f"but plugin.toml is located in directory '{plugin_dir_name}'. "
        f"Rename the directory to '{entry_package}' or update [plugin].entry to match the directory."
    )
