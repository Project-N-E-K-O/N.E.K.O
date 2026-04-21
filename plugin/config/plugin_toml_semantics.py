from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, TypedDict


class PluginConfigWarning(TypedDict):
    code: str
    field: str | None
    message: str
    severity: str
    source: str


_VALID_BOOL_TEXTS = {"1", "0", "true", "false", "yes", "no", "on", "off"}


def _warning(*, code: str, field: str | None, message: str) -> PluginConfigWarning:
    return {
        "code": code,
        "field": field,
        "message": message,
        "severity": "warning",
        "source": "semantic",
    }


def _is_non_empty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def collect_plugin_toml_semantic_warnings(
    conf: object,
    *,
    toml_path: Path,
) -> list[PluginConfigWarning]:
    warnings: list[PluginConfigWarning] = []

    if not isinstance(conf, Mapping):
        warnings.append(
            _warning(
                code="PLUGIN_CONFIG_ROOT_NOT_OBJECT",
                field=None,
                message=f"Plugin config {toml_path} should parse to a table at top level",
            )
        )
        return warnings

    plugin_section_obj = conf.get("plugin")
    if not isinstance(plugin_section_obj, Mapping):
        warnings.append(
            _warning(
                code="PLUGIN_SECTION_MISSING",
                field="plugin",
                message=f"Plugin config {toml_path} should contain a [plugin] table",
            )
        )
        return warnings

    raw_plugin_id = plugin_section_obj.get("id")
    plugin_id = str(raw_plugin_id).strip() if _is_non_empty_string(raw_plugin_id) else toml_path.parent.name

    directory_name = toml_path.parent.name.strip()
    if plugin_id and directory_name and plugin_id != directory_name:
        warnings.append(
            _warning(
                code="PLUGIN_DIRECTORY_ID_MISMATCH",
                field="plugin.id",
                message=(
                    f"Plugin {plugin_id} directory name '{directory_name}' does not match declared plugin.id "
                    f"'{plugin_id}'. Runtime loading can still work, but config/profile lookup and tooling may "
                    f"assume plugin root '{plugin_id}/plugin.toml'."
                ),
            )
        )

    entry = plugin_section_obj.get("entry")
    if isinstance(entry, str) and entry.strip() != entry:
        warnings.append(
            _warning(
                code="PLUGIN_ENTRY_HAS_WHITESPACE",
                field="plugin.entry",
                message=f"Plugin {plugin_id}: [plugin].entry in {toml_path} contains leading/trailing whitespace",
            )
        )

    keywords = plugin_section_obj.get("keywords")
    if keywords is not None:
        if not isinstance(keywords, list):
            warnings.append(
                _warning(
                    code="PLUGIN_KEYWORDS_NOT_LIST",
                    field="plugin.keywords",
                    message=f"Plugin {plugin_id}: [plugin].keywords should be a string list in {toml_path}",
                )
            )
        elif any(not _is_non_empty_string(item) for item in keywords):
            warnings.append(
                _warning(
                    code="PLUGIN_KEYWORDS_INVALID_ITEM",
                    field="plugin.keywords",
                    message=(
                        f"Plugin {plugin_id}: [plugin].keywords should contain only non-empty strings in {toml_path}"
                    ),
                )
            )

    passive = plugin_section_obj.get("passive")
    if isinstance(passive, str) and passive.strip().lower() in _VALID_BOOL_TEXTS:
        warnings.append(
            _warning(
                code="PLUGIN_PASSIVE_STRING_BOOLEAN",
                field="plugin.passive",
                message=(
                    f"Plugin {plugin_id}: [plugin].passive in {toml_path} uses string boolean '{passive}'; "
                    "prefer true/false"
                ),
            )
        )
    elif passive is not None and not isinstance(passive, bool):
        warnings.append(
            _warning(
                code="PLUGIN_PASSIVE_NON_BOOLEAN",
                field="plugin.passive",
                message=f"Plugin {plugin_id}: [plugin].passive should be a boolean in {toml_path}",
            )
        )

    runtime_section = conf.get("plugin_runtime")
    if isinstance(runtime_section, Mapping):
        for field_name in ("enabled", "auto_start"):
            value = runtime_section.get(field_name)
            if isinstance(value, str) and value.strip().lower() in _VALID_BOOL_TEXTS:
                warnings.append(
                    _warning(
                        code="PLUGIN_RUNTIME_STRING_BOOLEAN",
                        field=f"plugin_runtime.{field_name}",
                        message=(
                            f"Plugin {plugin_id}: [plugin_runtime].{field_name} in {toml_path} uses string boolean "
                            f"'{value}'; prefer true/false"
                        ),
                    )
                )

    return warnings


def warn_plugin_toml_semantic_issues(conf: object, *, toml_path: Path, logger: Any) -> None:
    for warning in collect_plugin_toml_semantic_warnings(conf, toml_path=toml_path):
        logger.warning(
            "Plugin config warning [{}] field={} msg={}",
            warning["code"],
            warning["field"],
            warning["message"],
        )


__all__ = ["PluginConfigWarning", "warn_plugin_toml_semantic_issues", "collect_plugin_toml_semantic_warnings"]
