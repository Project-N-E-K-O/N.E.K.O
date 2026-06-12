# Ported from claudian/src/core/providers/commands/ProviderCommandEntry.ts
# Original author: Claudian contributors
# License: MIT

"""
Provider command entry.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ProviderCommandEntry:
    """A provider command entry."""
    id: str = ""
    name: str = ""
    description: str = ""
    content: str = ""
    source: str = "provider"  # "builtin" | "user" | "provider" | "sdk"
    hidden: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "content": self.content,
            "source": self.source,
            "hidden": self.hidden,
        }
