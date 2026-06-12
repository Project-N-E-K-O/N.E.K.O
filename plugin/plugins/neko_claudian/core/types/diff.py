# Ported from claudian/src/core/types/diff.ts
# Original author: Claudian contributors
# License: MIT

"""
Diff type definitions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class DiffLine:
    """A single line in a diff."""
    type: str = "equal"  # "equal" | "insert" | "delete"
    text: str = ""
    old_line_num: Optional[int] = None
    new_line_num: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "type": self.type,
            "text": self.text,
        }
        if self.old_line_num is not None:
            out["oldLineNum"] = self.old_line_num
        if self.new_line_num is not None:
            out["newLineNum"] = self.new_line_num
        return out


@dataclass
class DiffStats:
    """Statistics for a diff."""
    added: int = 0
    removed: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {"added": self.added, "removed": self.removed}


@dataclass
class StructuredPatchHunk:
    """A single hunk from the SDK's structuredPatch format."""
    old_start: int = 0
    old_lines: int = 0
    new_start: int = 0
    new_lines: int = 0
    lines: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "oldStart": self.old_start,
            "oldLines": self.old_lines,
            "newStart": self.new_start,
            "newLines": self.new_lines,
            "lines": self.lines,
        }


@dataclass
class SDKToolUseResult:
    """Shape of the SDK's toolUseResult object for Write/Edit tools."""
    structured_patch: Optional[List[StructuredPatchHunk]] = None
    file_path: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        if self.structured_patch:
            out["structuredPatch"] = [h.to_dict() for h in self.structured_patch]
        if self.file_path:
            out["filePath"] = self.file_path
        out.update(self.extra)
        return out
