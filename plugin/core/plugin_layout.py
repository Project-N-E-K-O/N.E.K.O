from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from plugin.sdk.shared.core.base_runtime import resolve_runtime_data_root
from plugin.utils.path_resolution import PathResolutionCache, canonical_read_path


@dataclass(frozen=True, slots=True)
class PluginLayout:
    plugin_id: str
    installed_dir: Path
    manifest_path: Path
    config_path: Path
    data_dir: Path
    cache_dir: Path

    @property
    def vendor_dir(self) -> Path:
        return self.installed_dir / "vendor"


def resolve_plugin_layout(
    plugin_id: str,
    installed_dir: Path,
    *,
    storage_root: Path | None = None,
    read_cache: PathResolutionCache | None = None,
) -> PluginLayout:
    if re.fullmatch(r"[A-Za-z0-9_-]+", plugin_id) is None:
        raise ValueError("plugin_id must contain only letters, numbers, underscores, or hyphens")
    normalized_installed_dir = canonical_read_path(Path(installed_dir), cache=read_cache)
    effective_root = storage_root
    if effective_root is None:
        if read_cache is not None:
            if read_cache.runtime_data_root is None:
                read_cache.runtime_data_root = resolve_runtime_data_root()
            effective_root = read_cache.runtime_data_root
        else:
            effective_root = resolve_runtime_data_root()
    normalized_storage_root = canonical_read_path(Path(effective_root), cache=read_cache)
    state_dir = normalized_storage_root / "plugins" / plugin_id
    return PluginLayout(
        plugin_id=plugin_id,
        installed_dir=normalized_installed_dir,
        manifest_path=normalized_installed_dir / "plugin.toml",
        config_path=state_dir / "config" / "plugin.toml",
        data_dir=state_dir / "data",
        cache_dir=state_dir / "cache",
    )


__all__ = ["PluginLayout", "resolve_plugin_layout"]
