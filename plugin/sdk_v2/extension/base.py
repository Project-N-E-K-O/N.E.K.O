"""Extension base contracts for SDK v2.

This is the developer-facing extension facade. It keeps extension-specific
metadata and capability boundaries local to the extension surface while relying
only on the shared layer beneath it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from plugin.sdk_v2.shared.core.base import NekoPluginBase


@dataclass(slots=True)
class ExtensionMeta:
    id: str
    name: str
    version: str = "0.0.0"
    description: str = ""
    capabilities: list[str] = field(default_factory=list)


class NekoExtensionBase(NekoPluginBase):
    """Narrower plugin contract for extension flavor."""


__all__ = ["ExtensionMeta", "NekoExtensionBase"]
