from __future__ import annotations

from pathlib import Path
from typing import Iterable
import re

from plugin.core.python_dependencies import (
    collect_project_python_requirements,
    find_missing_python_requirements,
    split_host_provided_requirements,
)

from .models import PluginSource
from .toml_utils import escape_string, load_toml, render_toml_value, toml_bare_or_quoted_key

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
    """Write payload/dependencies.toml into the staging directory.

    This is an internal build artifact consumed only during install-time
    validation.  Plugin developers never interact with this file directly.
    """
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


def validate_payload_dependency_layout(payload_dir: Path, plugin_ids: Iterable[str]) -> None:
    """Validate dependency rules against the actual staged package payload.

    Uses the auto-generated dependencies.toml (if present) merged with each
    plugin's pyproject.toml to verify vendored packages satisfy all declared
    requirements.  This runs at build time and install time only.
    """

    payload_dir = Path(payload_dir)
    plugin_id_list = [str(item) for item in plugin_ids]
    dependency_manifest_path = payload_dir / "dependencies.toml"
    dependency_manifest = load_toml(dependency_manifest_path) if dependency_manifest_path.is_file() else None
    manifest_requirements = collect_dependency_manifest_python_requirements(
        dependency_manifest,
        plugin_id_list,
        source_name=str(dependency_manifest_path),
    )

    for plugin_id in plugin_id_list:
        plugin_dir = payload_dir / "plugins" / plugin_id
        requirements_file = plugin_dir / "requirements.txt"
        if requirements_file.exists():
            raise ValueError(
                f"plugin '{plugin_id}' package payload contains unsupported requirements.txt. "
                "Python runtime dependencies must be declared in pyproject.toml "
                "[project].dependencies and vendored under vendor/."
            )

        pyproject_path = plugin_dir / "pyproject.toml"
        pyproject_toml = load_toml(pyproject_path) if pyproject_path.is_file() else None
        python_requirements = _merge_requirement_lists(
            manifest_requirements.get(plugin_id, []),
            collect_project_python_requirements(pyproject_toml),
        )

        external_requirements, _host_requirements = split_host_provided_requirements(python_requirements)
        if not external_requirements:
            continue

        vendor_dir = plugin_dir / "vendor"
        if not vendor_dir.is_dir():
            raise ValueError(
                f"plugin '{plugin_id}' package payload declares Python runtime dependencies "
                f"({', '.join(external_requirements)}) but vendor/ is missing."
            )
        if not any(path.is_file() for path in vendor_dir.rglob("*")):
            raise ValueError(
                f"plugin '{plugin_id}' package payload declares Python runtime dependencies "
                f"({', '.join(external_requirements)}) but vendor/ does not contain any files."
            )
        missing_requirements = find_missing_python_requirements(
            external_requirements,
            search_paths=[vendor_dir],
        )
        if missing_requirements:
            raise ValueError(
                f"plugin '{plugin_id}' package payload vendor/ does not satisfy Python runtime dependencies: "
                f"{', '.join(missing_requirements)}"
            )


def collect_dependency_manifest_python_requirements(
    data: dict[str, object] | None,
    plugin_ids: Iterable[str],
    *,
    source_name: str = "payload/dependencies.toml",
) -> dict[str, list[str]]:
    if data is None:
        return {}

    plugins_table = data.get("plugins")
    if not isinstance(plugins_table, dict):
        raise ValueError(f"{source_name} must contain a [plugins] table")

    result: dict[str, list[str]] = {}
    for plugin_id in plugin_ids:
        raw_item = plugins_table.get(plugin_id)
        if raw_item is None:
            raise ValueError(f"{source_name} is missing dependency metadata for plugin '{plugin_id}'")
        if not isinstance(raw_item, dict):
            raise ValueError(f"{source_name} [plugins.{plugin_id}] must be a TOML table")
        result[plugin_id] = _read_manifest_string_list(
            raw_item,
            "python_requirements",
            source_name=source_name,
            plugin_id=plugin_id,
        )
    return result


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


def _merge_requirement_lists(*groups: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for item in group:
            requirement = str(item or "").strip()
            if not requirement:
                continue
            key = requirement.lower()
            if key in seen:
                continue
            seen.add(key)
            result.append(requirement)
    return result


def _read_manifest_string_list(
    data: dict[str, object],
    key: str,
    *,
    source_name: str,
    plugin_id: str,
) -> list[str]:
    value = data.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{source_name} [plugins.{plugin_id}].{key} must be a list of strings")

    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError(f"{source_name} [plugins.{plugin_id}].{key} entries must be strings")
        stripped = item.strip()
        if stripped:
            result.append(stripped)
    return result
