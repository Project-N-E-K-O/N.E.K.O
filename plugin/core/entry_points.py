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
    """Normalize legacy built-in-style entries for user-installed plugins.

    Market packages are installed under the user plugin root as
    ``plugins/<plugin_id>``. Older ``init-repo`` templates wrote entries as
    ``plugin.plugins.<plugin_id>:Class``, which only works for in-repo built-in
    plugins. When such a package is found outside the built-in root, rewrite it
    to the user-root import namespace.
    """

    if ":" not in entry_point:
        return entry_point

    module_path, class_name = entry_point.split(":", 1)
    legacy_prefix = "plugin.plugins."
    if not module_path.startswith(legacy_prefix):
        return entry_point

    plugin_dir = config_path.parent
    if _is_same_or_within(plugin_dir, builtin_plugin_root):
        return entry_point

    suffix = module_path[len(legacy_prefix):]
    if not suffix:
        return entry_point
    return f"plugins.{suffix}:{class_name}"
