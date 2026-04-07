from __future__ import annotations

from pathlib import Path

from .models import PluginSource
from .toml_utils import load_toml, optional_string, require_string, require_table


def load_plugin_source(plugin_dir: str | Path) -> PluginSource:
    plugin_dir = Path(plugin_dir).expanduser().resolve()
    plugin_toml_path = plugin_dir / "plugin.toml"
    if not plugin_toml_path.is_file():
        raise FileNotFoundError(f"plugin.toml not found: {plugin_toml_path}")

    plugin_toml = load_toml(plugin_toml_path)
    plugin_table = require_table(plugin_toml, "plugin", plugin_toml_path)
    plugin_id = require_string(plugin_table, "id", plugin_toml_path)
    name = optional_string(plugin_table, "name") or plugin_id
    version = optional_string(plugin_table, "version") or "0.1.0"
    package_type = optional_string(plugin_table, "type") or "plugin"

    pyproject_toml_path = plugin_dir / "pyproject.toml"
    pyproject_toml = load_toml(pyproject_toml_path) if pyproject_toml_path.is_file() else None

    return PluginSource(
        plugin_dir=plugin_dir,
        plugin_toml_path=plugin_toml_path,
        pyproject_toml_path=pyproject_toml_path if pyproject_toml_path.is_file() else None,
        plugin_id=plugin_id,
        name=name,
        version=version,
        package_type=package_type,
        plugin_toml=plugin_toml,
        pyproject_toml=pyproject_toml,
    )


def extract_runtime_config(source: PluginSource) -> dict[str, object]:
    value = source.plugin_toml.get(source.plugin_id)
    return value if isinstance(value, dict) else {}
