from __future__ import annotations

from pathlib import Path
import re

from plugin.core.python_dependencies import (
    collect_project_python_requirements,
    find_missing_python_requirements,
    split_host_provided_requirements,
)

from .models import PluginSource
from .toml_utils import escape_string, render_toml_value, toml_bare_or_quoted_key

_DEPENDENCY_SCHEMA_VERSION = "1.0"
_PLUGIN_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def validate_source_dependency_layout(source: PluginSource) -> None:
    """Validate dependency packaging rules for a plugin source directory."""

    collect_simple_plugin_dependency_ids(source.plugin_toml, plugin_id=source.plugin_id)

    requirements_file = source.plugin_dir / "requirements.txt"
    if requirements_file.exists():
        raise ValueError(
            f"{source.plugin_id}: requirements.txt is not supported for plugin packages. "
            "Declare Python runtime dependencies in pyproject.toml [project].dependencies "
            "and vendor them under the plugin's vendor/ directory."
        )

    python_requirements = collect_project_python_requirements(source.pyproject_toml)
    external_requirements, _host_requirements = split_host_provided_requirements(python_requirements)
    if not external_requirements:
        return

    if source.package_type == "extension":
        raise ValueError(
            f"{source.plugin_id}: extension plugins cannot declare Python runtime dependencies "
            "because they run inside their host plugin process."
        )

    vendor_dir = source.plugin_dir / "vendor"
    if not vendor_dir.is_dir():
        raise ValueError(
            f"{source.plugin_id}: pyproject.toml declares Python runtime dependencies "
            f"({', '.join(external_requirements)}), but vendor/ is missing. "
            "Install those dependencies into the plugin's vendor/ directory before packaging."
        )
    if not any(path.is_file() for path in vendor_dir.rglob("*")):
        raise ValueError(
            f"{source.plugin_id}: pyproject.toml declares Python runtime dependencies "
            f"({', '.join(external_requirements)}), but vendor/ does not contain any files."
        )
    missing_requirements = find_missing_python_requirements(
        external_requirements,
        search_paths=[vendor_dir],
    )
    if missing_requirements:
        raise ValueError(
            f"{source.plugin_id}: vendor/ does not satisfy Python runtime dependencies: "
            f"{', '.join(missing_requirements)}"
        )


def collect_simple_plugin_dependency_ids(plugin_toml: dict[str, object], *, plugin_id: str) -> list[str]:
    plugin_table = plugin_toml.get("plugin")
    if not isinstance(plugin_table, dict):
        return []
    raw_dependencies = plugin_table.get("dependencies")
    if raw_dependencies is None:
        return []
    if not isinstance(raw_dependencies, list):
        raise ValueError(f"{plugin_id}: [plugin].dependencies must be a list of plugin id strings")

    result: list[str] = []
    seen: set[str] = set()
    for item in raw_dependencies:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{plugin_id}: [plugin].dependencies entries must be non-empty strings")
        dependency_id = item.strip()
        if not _PLUGIN_ID_RE.fullmatch(dependency_id):
            raise ValueError(
                f"{plugin_id}: [plugin].dependencies entry '{dependency_id}' is not a valid plugin id. "
                "Python packages belong in pyproject.toml [project].dependencies."
            )
        if dependency_id == plugin_id:
            raise ValueError(f"{plugin_id}: [plugin].dependencies must not include the plugin itself")
        if dependency_id in seen:
            continue
        seen.add(dependency_id)
        result.append(dependency_id)
    return result


def collect_advanced_plugin_dependencies(plugin_toml: dict[str, object]) -> list[dict[str, object]]:
    plugin_table = plugin_toml.get("plugin")
    if not isinstance(plugin_table, dict):
        return []
    raw = plugin_table.get("dependency")
    if raw is None:
        return []
    if isinstance(raw, dict):
        raw_entries = [raw]
    elif isinstance(raw, list):
        raw_entries = [item for item in raw if isinstance(item, dict)]
    else:
        return []
    return [_sanitize_dependency_entry(entry) for entry in raw_entries]


def write_dependency_manifest(sources: list[PluginSource], payload_dir: Path) -> Path:
    manifest_path = payload_dir / "dependencies.toml"
    lines: list[str] = [
        f'schema_version = "{_DEPENDENCY_SCHEMA_VERSION}"',
        "",
    ]

    for source in sources:
        python_requirements = collect_project_python_requirements(source.pyproject_toml)
        external_requirements, host_requirements = split_host_provided_requirements(python_requirements)
        plugin_dependencies = collect_simple_plugin_dependency_ids(
            source.plugin_toml,
            plugin_id=source.plugin_id,
        )
        advanced_dependencies = collect_advanced_plugin_dependencies(source.plugin_toml)
        vendor_dir = source.plugin_dir / "vendor"

        lines.extend(
            [
                f"[plugins.{toml_bare_or_quoted_key(source.plugin_id)}]",
                f'python_requirements = {render_toml_value(external_requirements)}',
                f'host_python_requirements = {render_toml_value(host_requirements)}',
                f'plugin_dependencies = {render_toml_value(plugin_dependencies)}',
                f'advanced_plugin_dependencies = {render_toml_value(advanced_dependencies)}',
                f'vendor_path = "plugins/{escape_string(source.plugin_id)}/vendor"',
                f"vendor_present = {render_toml_value(vendor_dir.is_dir())}",
                "",
            ]
        )

    manifest_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8", newline="\n")
    return manifest_path.resolve()


def _sanitize_dependency_entry(entry: dict[str, object]) -> dict[str, object]:
    allowed_keys = {
        "id",
        "entry",
        "custom_event",
        "providers",
        "recommended",
        "supported",
        "untested",
        "conflicts",
    }
    result: dict[str, object] = {}
    for key in sorted(allowed_keys):
        if key not in entry:
            continue
        value = entry[key]
        if isinstance(value, (str, bool)):
            result[key] = value
        elif isinstance(value, list):
            result[key] = [str(item) for item in value if item is not None]
    return result
