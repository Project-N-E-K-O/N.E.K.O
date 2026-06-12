# Ported from claudian/src/core/storage/WorkspaceFileAdapter.ts
# Original author: Claudian contributors
# License: MIT

"""
Workspace file adapter — File operations in workspace directory.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


class WorkspaceFileAdapter:
    """File adapter for workspace directory.

    Ported from WorkspaceFileAdapter.ts
    """

    def __init__(self, workspace_path: Path):
        self._workspace_path = workspace_path

    @property
    def workspace_path(self) -> Path:
        return self._workspace_path

    def get_path(self, *parts: str) -> Path:
        """Get a path relative to workspace."""
        return self._workspace_path.joinpath(*parts)

    async def read_json(self, relative_path: str) -> Optional[Any]:
        """Read a JSON file from workspace."""
        path = self._workspace_path / relative_path
        if not path.exists():
            return None

        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to read {path}: {e}")
            return None

    async def write_json(self, data: Any, relative_path: str) -> bool:
        """Write a JSON file to workspace."""
        path = self._workspace_path / relative_path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            return True
        except Exception as e:
            logger.error(f"Failed to write {path}: {e}")
            return False

    async def read_text(self, relative_path: str) -> Optional[str]:
        """Read a text file from workspace."""
        path = self._workspace_path / relative_path
        if not path.exists():
            return None

        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            logger.error(f"Failed to read {path}: {e}")
            return None

    async def exists(self, relative_path: str) -> bool:
        """Check if a file exists in workspace."""
        return (self._workspace_path / relative_path).exists()
