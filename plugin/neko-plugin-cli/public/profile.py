from __future__ import annotations

from pathlib import Path

from .models import PluginSource
from .plugin_source import extract_runtime_config
from .toml_utils import dump_mapping, escape_string, toml_bare_or_quoted_key, toml_bool


def write_default_profile(source: PluginSource, profiles_dir: Path) -> list[Path]:
    # Keep the first profile pass lightweight and deterministic so future
    # bundle/profile features can extend it without changing pack flow shape.
    profile_path = profiles_dir / "default.toml"
    lines: list[str] = [
        'name = "default"',
        f'enabled_plugins = ["{escape_string(source.plugin_id)}"]',
        "",
        f"[plugin.{toml_bare_or_quoted_key(source.plugin_id)}]",
        "enabled = true",
    ]

    plugin_runtime = source.plugin_toml.get("plugin_runtime")
    if isinstance(plugin_runtime, dict):
        auto_start = plugin_runtime.get("auto_start")
        if isinstance(auto_start, bool):
            lines.append(f"auto_start = {toml_bool(auto_start)}")

    runtime_config = extract_runtime_config(source)
    if runtime_config:
        lines.extend(
            [
                "",
                f"[plugin.{toml_bare_or_quoted_key(source.plugin_id)}.{toml_bare_or_quoted_key(source.plugin_id)}]",
            ]
        )
        lines.extend(dump_mapping(runtime_config))

    profile_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return [profile_path.resolve()]
