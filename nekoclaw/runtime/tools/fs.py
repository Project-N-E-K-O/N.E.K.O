# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""The ``read``, ``write`` and ``edit`` tools.

Result and error texts follow dsh ``packages/fs/tool-fs`` and
``packages/fs/fs-local`` so both runtimes read the same to the model. Two
things go beyond dsh: a file keeps its encoding and byte-order mark as well as
its newline style, and ``edit`` falls back to relaxed line matching (see
``seek.py``) when the literal text is not found.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nekoclaw.runtime.tools import seek, textio
from nekoclaw.runtime.tools.base import (
    ToolCallInfo,
    ToolContext,
    ToolError,
    as_int,
    define_tool,
)
from nekoclaw.runtime.loop.tools import AgentTool

READ_LIMIT = 2000
READ_MAX_LINE_LENGTH = 2000
READ_MAX_BYTES = 50 * 1024
# Whole-file reads are held in memory; larger files are refused rather than streamed.
FILE_MAX_BYTES = 64 * 1024 * 1024


# ---------------------------------------------------------------------------
# read
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ReadWindow:
    offset: int
    lines: list[tuple[int, str]]
    total_lines: int
    truncated_by_bytes: bool


def build_read_window(
    lines: list[str],
    *,
    offset: int,
    limit: int,
    display: str,
    max_line_length: int = READ_MAX_LINE_LENGTH,
    max_bytes: int = READ_MAX_BYTES,
) -> ReadWindow:
    """Select the lines one ``read`` call returns."""
    selected: list[tuple[int, str]] = []
    used = 0
    truncated = False
    for number, raw in enumerate(lines, start=1):
        if number < offset:
            continue
        if len(selected) >= limit:
            break
        text = raw
        if len(text) > max_line_length:
            text = f"{text[:max_line_length]}... (line truncated to {max_line_length} chars)"
        size = len(text.encode("utf-8", errors="replace")) + (1 if selected else 0)
        if used + size > max_bytes:
            truncated = True
            break
        used += size
        selected.append((number, text))
    total = len(lines)
    if not truncated and offset > total and not (total == 0 and offset == 1):
        raise ToolError(f'offset {offset} is out of range for "{display}" ({total} lines)')
    return ReadWindow(offset=offset, lines=selected, total_lines=total, truncated_by_bytes=truncated)


def format_read_output(display: str, window: ReadWindow) -> str:
    end_line = window.lines[-1][0] if window.lines else max(0, window.offset - 1)
    if window.truncated_by_bytes:
        footer = (
            f"(Output capped. Showing lines {window.offset}-{end_line}. "
            f"Use offset={end_line + 1} to continue.)"
        )
    elif end_line < window.total_lines:
        footer = (
            f"(Showing lines {window.offset}-{end_line} of {window.total_lines}. "
            f"Use offset={end_line + 1} to continue.)"
        )
    else:
        footer = f"(End of file - total {window.total_lines} lines)"
    if window.lines:
        numbered = "\n".join(f"{number}: {text}" for number, text in window.lines)
        body = f"{numbered}\n\n{footer}"
    else:
        body = footer
    return f"<path>{display}</path>\n<type>file</type>\n<content>\n{body}\n</content>"


def read_file(path: Path, *, offset: int, limit: int) -> str:
    """Blocking implementation of ``read``."""
    display = str(path)
    raw = textio.read_bytes(path, verb="read", display=display, max_bytes=FILE_MAX_BYTES)
    text, _fmt = textio.decode_text(raw, verb="read", display=display)
    window = build_read_window(
        textio.split_lines(text), offset=offset, limit=limit, display=display
    )
    return format_read_output(display, window)


# ---------------------------------------------------------------------------
# write
# ---------------------------------------------------------------------------


def write_file(path: Path, content: str) -> str:
    """Blocking implementation of ``write``."""
    display = str(path)
    if path.is_dir():
        raise ToolError(f'cannot write "{display}": not a regular file')
    existed = path.exists()
    # A new file, or one that is not readable as text (binary, unknown
    # encoding, too large), gets plain UTF-8 exactly as the model wrote it.
    data = content.encode("utf-8", errors="replace")
    if existed:
        try:
            previous = textio.read_bytes(
                path, verb="write", display=display, max_bytes=FILE_MAX_BYTES
            )
            _old_text, fmt = textio.decode_text(previous, verb="write", display=display)
        except ToolError:
            pass
        else:
            # An existing text file keeps its encoding and line endings.
            data = textio.encode_text(content, fmt, display=display)
    textio.write_bytes_atomic(path, data, display=display)
    outcome = "Updated file" if existed else "Created file"
    return f"<path>{display}</path>\n<type>file</type>\n<content>\n{outcome}\n</content>"


# ---------------------------------------------------------------------------
# edit
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EditOutcome:
    content: str
    replacements: int
    # None for a literal match, otherwise the name of the relaxed level used.
    relaxed_level: str | None


def _count_occurrences(content: str, needle: str) -> int:
    count = 0
    index = 0
    while True:
        found = content.find(needle, index)
        if found == -1:
            return count
        count += 1
        index = found + len(needle)


def _ambiguous(count: int, display: str) -> ToolError:
    return ToolError(
        f'old_string matched {count} times in "{display}"; provide a more specific old_string '
        "or set replace_all to true"
    )


def apply_edit(
    content: str,
    old_string: str,
    new_string: str,
    *,
    replace_all: bool,
    display: str,
) -> EditOutcome:
    """Replace ``old_string`` in LF-normalized ``content``.

    The literal text is tried first. When it does not occur, the text is
    matched as a block of whole lines with the relaxed levels of ``seek``.
    """
    old = textio.normalize_newlines(old_string)
    new = textio.normalize_newlines(new_string)
    if not old:
        raise ToolError("old_string must be a non-empty string")

    literal = _count_occurrences(content, old)
    if literal > 1 and not replace_all:
        raise _ambiguous(literal, display)
    if literal >= 1:
        return EditOutcome(content.replace(old, new), literal, None)

    ends_with_newline = old.endswith("\n")
    pattern = (old[:-1] if ends_with_newline else old).split("\n")
    lines = content.split("\n")
    found = seek.find_block(lines, pattern)
    if found is None:
        raise ToolError(f'old_string was not found in "{display}"')
    level, starts = found
    if len(starts) > 1 and not replace_all:
        raise _ambiguous(len(starts), display)

    offsets = [0]
    for line in lines[:-1]:
        offsets.append(offsets[-1] + len(line) + 1)
    result = content
    for start in reversed(starts):
        last = start + len(pattern) - 1
        begin = offsets[start]
        end = offsets[last] + len(lines[last])
        if ends_with_newline and last < len(lines) - 1:
            end += 1
        result = result[:begin] + new + result[end:]
    return EditOutcome(result, len(starts), level)


def format_edit_output(display: str, *, replace_all: bool, relaxed_level: str | None) -> str:
    text = (
        f"The file {display} has been updated. All occurrences were successfully replaced."
        if replace_all
        else f"The file {display} has been updated successfully."
    )
    if relaxed_level is not None:
        text += (
            " old_string did not match exactly; it was matched "
            f"{seek.LEVEL_DESCRIPTIONS[relaxed_level]}. The matched lines were replaced with "
            "new_string as written, so read the file again if whitespace or punctuation matters."
        )
    return text


def edit_file(path: Path, old_string: str, new_string: str, *, replace_all: bool) -> str:
    """Blocking implementation of ``edit``."""
    display = str(path)
    raw = textio.read_bytes(path, verb="edit", display=display, max_bytes=FILE_MAX_BYTES)
    content, fmt = textio.decode_text(raw, verb="edit", display=display)
    outcome = apply_edit(
        content, old_string, new_string, replace_all=replace_all, display=display
    )
    textio.write_bytes_atomic(
        path, textio.encode_text(outcome.content, fmt, display=display), display=display
    )
    return format_edit_output(
        display, replace_all=replace_all, relaxed_level=outcome.relaxed_level
    )


# ---------------------------------------------------------------------------
# Tool wiring
# ---------------------------------------------------------------------------


def _require_path(args: dict[str, Any]) -> str:
    raw = str(args["file_path"])
    if not raw.strip():
        raise ToolError("file_path must be a non-empty string")
    return raw


def build_fs_tools(ctx: ToolContext, schemas: dict[str, dict[str, Any]]) -> list[AgentTool]:
    async def read(args: dict[str, Any], call: ToolCallInfo) -> str:
        del call
        path = ctx.resolve(_require_path(args))
        offset = as_int(args.get("offset", 1))
        limit = as_int(args.get("limit", READ_LIMIT))
        if offset < 1:
            raise ToolError("offset must be greater than or equal to 1")
        if limit < 1:
            raise ToolError("limit must be greater than or equal to 1")
        if limit > READ_LIMIT:
            raise ToolError(f"limit must be less than or equal to {READ_LIMIT}")
        return await asyncio.to_thread(read_file, path, offset=offset, limit=limit)

    async def write(args: dict[str, Any], call: ToolCallInfo) -> str:
        del call
        path = ctx.resolve(_require_path(args))
        return await asyncio.to_thread(write_file, path, str(args["content"]))

    async def edit(args: dict[str, Any], call: ToolCallInfo) -> str:
        del call
        path = ctx.resolve(_require_path(args))
        old_string = str(args["old_string"])
        new_string = str(args["new_string"])
        if not old_string:
            raise ToolError("old_string must be a non-empty string")
        if old_string == new_string:
            raise ToolError("old_string and new_string must differ")
        return await asyncio.to_thread(
            edit_file,
            path,
            old_string,
            new_string,
            replace_all=bool(args.get("replace_all", False)),
        )

    return [
        define_tool(schemas["read"], read, parallel=True),
        define_tool(schemas["write"], write),
        define_tool(schemas["edit"], edit),
    ]
