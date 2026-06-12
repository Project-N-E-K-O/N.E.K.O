# Ported from claudian/src/features/chat/controllers/SelectionController.ts
# Original author: Claudian contributors
# License: MIT

"""
SelectionController — Manages editor selection context.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class EditorSelectionContext:
    """Editor selection context."""
    file_path: str = ""
    selection_text: str = ""
    start_line: int = 0
    end_line: int = 0
    language: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "filePath": self.file_path,
            "selectionText": self.selection_text,
            "startLine": self.start_line,
            "endLine": self.end_line,
            "language": self.language,
        }


class SelectionController:
    """Manages editor selection context.

    Ported from claudian/src/features/chat/controllers/SelectionController.ts
    """

    def __init__(self):
        self._current_context: Optional[EditorSelectionContext] = None

    def get_context(self) -> Optional[EditorSelectionContext]:
        """Get the current editor selection context."""
        return self._current_context

    def set_context(self, context: Optional[EditorSelectionContext]) -> None:
        """Set the editor selection context."""
        self._current_context = context
        if context:
            logger.debug(f"Selection context set: {context.file_path}:{context.start_line}-{context.end_line}")

    def clear_context(self) -> None:
        """Clear the editor selection context."""
        self._current_context = None

    def has_context(self) -> bool:
        """Check if there is a current selection context."""
        return self._current_context is not None
