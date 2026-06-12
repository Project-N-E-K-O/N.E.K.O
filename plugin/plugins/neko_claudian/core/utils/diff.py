# Ported from claudian/src/utils/diff.ts
# Original author: Claudian contributors
# License: MIT

"""
Diff utilities.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class DiffLine:
    """A single diff line."""
    type: str = "equal"  # "equal" | "insert" | "delete"
    text: str = ""
    old_line_num: Optional[int] = None
    new_line_num: Optional[int] = None


@dataclass
class DiffStats:
    """Diff statistics."""
    added: int = 0
    removed: int = 0


@dataclass
class ToolDiffData:
    """Diff data for a tool call."""
    file_path: str = ""
    diff_lines: List[DiffLine] = field(default_factory=list)
    stats: DiffStats = field(default_factory=DiffStats)


def extract_diff_data(
    tool_use_result: Any,
    tool_call: Dict[str, Any],
) -> Optional[ToolDiffData]:
    """Extract diff data from tool use result."""
    if not isinstance(tool_use_result, dict):
        return None

    structured_patch = tool_use_result.get("structuredPatch")
    if not structured_patch:
        return None

    file_path = tool_use_result.get("filePath", "")
    diff_lines = []
    stats = DiffStats()

    for hunk in structured_patch:
        if not isinstance(hunk, dict):
            continue

        old_start = hunk.get("oldStart", 0)
        new_start = hunk.get("newStart", 0)
        lines = hunk.get("lines", [])

        old_line = old_start
        new_line = new_start

        for line in lines:
            if line.startswith("+"):
                diff_lines.append(DiffLine(
                    type="insert",
                    text=line[1:],
                    new_line_num=new_line,
                ))
                stats.added += 1
                new_line += 1
            elif line.startswith("-"):
                diff_lines.append(DiffLine(
                    type="delete",
                    text=line[1:],
                    old_line_num=old_line,
                ))
                stats.removed += 1
                old_line += 1
            else:
                diff_lines.append(DiffLine(
                    type="equal",
                    text=line,
                    old_line_num=old_line,
                    new_line_num=new_line,
                ))
                old_line += 1
                new_line += 1

    return ToolDiffData(
        file_path=file_path,
        diff_lines=diff_lines,
        stats=stats,
    )
