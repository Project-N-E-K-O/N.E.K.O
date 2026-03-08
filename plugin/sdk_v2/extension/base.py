"""Extension base contracts for SDK v2."""

from __future__ import annotations

from dataclasses import dataclass, field

from plugin.sdk_v2.plugin.base import NekoPluginBase


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
