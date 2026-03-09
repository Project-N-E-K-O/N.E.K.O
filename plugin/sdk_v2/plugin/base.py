"""Plugin-facing base facade for SDK v2.

This module keeps the plugin-author surface explicit and slightly richer than
`shared.core.base`: it preserves the shared implementation, but adds a few
plugin-oriented convenience accessors so plugin code does not need to reach into
context details for common paths/identifiers.
"""

from __future__ import annotations

from pathlib import Path

from plugin.sdk_v2.shared.constants import NEKO_PLUGIN_META_ATTR, NEKO_PLUGIN_TAG
from plugin.sdk_v2.shared.core.base import NekoPluginBase as _SharedNekoPluginBase
from plugin.sdk_v2.shared.core.base import PluginMeta as _SharedPluginMeta


class PluginMeta(_SharedPluginMeta):
    """Plugin-facing metadata model.

    This aliases the shared metadata shape while reserving the plugin facade as
    the stable import target for plugin authors.
    """


class NekoPluginBase(_SharedNekoPluginBase):
    """Plugin-facing base class with small convenience helpers."""

    @property
    def plugin_id(self) -> str:
        return str(getattr(self.ctx, "plugin_id", "plugin"))

    @property
    def config_dir(self) -> Path:
        config_path = getattr(self.ctx, "config_path", None)
        return Path(config_path).parent if config_path is not None else Path.cwd()

    def data_path(self, *parts: str) -> Path:
        base = self.config_dir / "data"
        return base.joinpath(*parts) if parts else base


__all__ = [
    "NEKO_PLUGIN_META_ATTR",
    "NEKO_PLUGIN_TAG",
    "PluginMeta",
    "NekoPluginBase",
]
