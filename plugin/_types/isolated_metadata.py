"""Metadata exchanged between an isolated scanner and the plugin server."""

from dataclasses import dataclass


@dataclass(slots=True)
class IsolatedPluginMetadata:
    entries_preview: list[dict[str, object]]
    handlers: dict[str, dict[str, object]]
    entry_methods: dict[str, str]


def handler_key_belongs_to_plugin(key: str, plugin_id: str) -> bool:
    return key.startswith(f"{plugin_id}.") or key.startswith(f"{plugin_id}:")
