# Ported from claudian/src/core/types/plugins.ts
# Original author: Claudian contributors
# License: MIT

"""
Plugin type definitions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


PluginScope = Literal["user", "project"]


@dataclass
class PluginInfo:
    """Plugin information."""
    id: str = ""
    name: str = ""
    enabled: bool = True
    scope: str = "user"  # "user" | "project"
    install_path: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "enabled": self.enabled,
            "scope": self.scope,
            "installPath": self.install_path,
        }
