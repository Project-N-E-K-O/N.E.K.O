# -- coding: utf-8 --

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Optional


_DEFAULT_TOOL_NAMES = frozenset({"recall_memory"})
_SEED_OPEN_RE = re.compile(r"<\s*seed\s*:\s*tool_call\b[^>]*>|(?<!/)\bseed\s*:\s*tool_call\b", re.IGNORECASE)
_SEED_CLOSE_RE = re.compile(r"<\s*/\s*seed\s*:\s*tool_call\s*>", re.IGNORECASE)
_PARAMETER_RE = re.compile(r"<\s*parameter\b[^>]*\bname\s*=", re.IGNORECASE)
_FUNCTION_CLOSE_RE = re.compile(r"<\s*/\s*function\s*>", re.IGNORECASE)
_NAME_CLOSE_RE = re.compile(r"</\s*name\s*>", re.IGNORECASE)


@dataclass(frozen=True)
class ToolLeakFilterEvent:
    pattern: str
    chars: int
    cross_chunk: bool = False
    finalized: bool = False


class ToolLeakFilter:
    """Streaming filter for provider-emitted tool-call markup in assistant text."""

    def __init__(self, *, tool_names: set[str] | None = None, max_tail: int = 2048):
        self._tool_names = {name for name in (tool_names or _DEFAULT_TOOL_NAMES) if name}
        self._max_tail = max(128, int(max_tail))
        self._pending = ""
        self._suppressing = False
        self._suppressed_chars = 0
        self._suppression_pattern = ""
        self._cross_chunk = False
        self._in_code_fence = False
        self._fence_marker = ""
        self._fence_line_buffer = ""

    def feed(self, chunk: str) -> tuple[str, ToolLeakFilterEvent | None]:
        if not chunk:
            return "", None

        text = self._pending + str(chunk)
        had_pending = bool(self._pending)
        self._pending = ""
        output: list[str] = []
        event: ToolLeakFilterEvent | None = None

        while text:
            if self._suppressing:
                close_match = _SEED_CLOSE_RE.search(text)
                if close_match:
                    self._suppressed_chars += close_match.end()
                    text = text[close_match.end():]
                    event = self._finish_event()
                    continue
                self._suppressed_chars += len(text)
                text = ""
                break

            match = self._find_leak_start(text)
            if not match:
                keep, self._pending = self._split_safe_tail(text)
                if keep:
                    self._append_visible(output, keep)
                break

            start, _end, pattern = match
            if start:
                self._append_visible(output, text[:start])
            text = text[start:]
            if self._in_code_fence:
                self._append_visible(output, "[tool-call markup omitted]")
            self._suppressing = True
            self._suppressed_chars = 0
            self._suppression_pattern = pattern
            self._cross_chunk = had_pending

        return "".join(output), event

    def finalize(self) -> tuple[str, ToolLeakFilterEvent | None]:
        if self._suppressing:
            self._suppressed_chars += len(self._pending)
            self._pending = ""
            return "", self._finish_event(finalized=True)

        visible = self._pending
        self._pending = ""
        return visible, None

    def reset(self) -> None:
        self._pending = ""
        self._suppressing = False
        self._suppressed_chars = 0
        self._suppression_pattern = ""
        self._cross_chunk = False
        self._in_code_fence = False
        self._fence_marker = ""
        self._fence_line_buffer = ""

    def _finish_event(self, *, finalized: bool = False) -> ToolLeakFilterEvent:
        event = ToolLeakFilterEvent(
            pattern=self._suppression_pattern or "tool_call_markup",
            chars=self._suppressed_chars,
            cross_chunk=self._cross_chunk,
            finalized=finalized,
        )
        self._suppressing = False
        self._suppressed_chars = 0
        self._suppression_pattern = ""
        self._cross_chunk = False
        return event

    def _find_leak_start(self, text: str) -> Optional[tuple[int, int, str]]:
        seed = _SEED_OPEN_RE.search(text)
        if seed:
            return seed.start(), seed.end(), "seed_tool_call"

        if self._tool_names:
            lower_text = text.lower()
            for tool_name in sorted(self._tool_names, key=len, reverse=True):
                idx = lower_text.find(tool_name.lower())
                if idx < 0:
                    continue
                suffix = text[idx:]
                if _NAME_CLOSE_RE.search(suffix) and (
                    _PARAMETER_RE.search(suffix) or _FUNCTION_CLOSE_RE.search(suffix)
                ):
                    return idx, idx + len(tool_name), "structured_tool_call"
        return None

    def _split_safe_tail(self, text: str) -> tuple[str, str]:
        keep_tail = self._possible_marker_tail_len(text)
        if keep_tail <= 0:
            return text, ""
        return text[:-keep_tail], text[-keep_tail:]

    def _append_visible(self, output: list[str], text: str) -> None:
        output.append(text)
        self._track_code_fences(text)

    def _track_code_fences(self, text: str) -> None:
        if not text:
            return

        self._fence_line_buffer += text
        while "\n" in self._fence_line_buffer:
            line, self._fence_line_buffer = self._fence_line_buffer.split("\n", 1)
            self._apply_fence_line(line)

    def _apply_fence_line(self, line: str) -> None:
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            marker = stripped[:3]
            if not self._in_code_fence:
                self._in_code_fence = True
                self._fence_marker = marker
            elif marker == self._fence_marker:
                self._in_code_fence = False
                self._fence_marker = ""

    def _possible_marker_tail_len(self, text: str) -> int:
        lower = text.lower()
        best = self._seed_marker_tail_len(text)
        markers = []
        markers.extend(name.lower() + "</name><parameter name=" for name in self._tool_names)
        markers.extend(name.lower() + "</name>" for name in self._tool_names)
        for marker in markers:
            max_prefix = min(len(marker) - 1, len(lower), self._max_tail)
            for size in range(max_prefix, 0, -1):
                if lower.endswith(marker[:size]):
                    best = max(best, size)
                    break
        return best

    def _seed_marker_tail_len(self, text: str) -> int:
        min_start = max(0, len(text) - self._max_tail)
        for start in range(min_start, len(text)):
            first = text[start]
            if first != "<" and first.lower() != "s":
                continue
            tail = text[start:]
            if self._is_seed_opener_prefix(tail):
                return len(tail)
        return 0

    @classmethod
    def _is_seed_opener_prefix(cls, text: str) -> bool:
        return cls._is_bracketed_seed_opener_prefix(text) or cls._is_bare_seed_opener_prefix(text)

    @staticmethod
    def _consume_literal_prefix(text: str, pos: int, literal: str) -> tuple[bool, int, bool]:
        fragment = text[pos : pos + len(literal)].lower()
        if not literal.startswith(fragment):
            return False, pos, False
        if len(fragment) < len(literal):
            return True, len(text), True
        return True, pos + len(literal), False

    @staticmethod
    def _consume_whitespace(text: str, pos: int) -> int:
        while pos < len(text) and text[pos].isspace():
            pos += 1
        return pos

    @staticmethod
    def _is_word_char(char: str) -> bool:
        return char == "_" or char.isalnum()

    @classmethod
    def _is_bracketed_seed_opener_prefix(cls, text: str) -> bool:
        if not text or text[0] != "<":
            return False

        pos = cls._consume_whitespace(text, 1)
        if pos == len(text):
            return True

        ok, pos, partial = cls._consume_literal_prefix(text, pos, "seed")
        if not ok:
            return False
        if partial:
            return True

        pos = cls._consume_whitespace(text, pos)
        if pos == len(text):
            return True
        if text[pos] != ":":
            return False

        pos = cls._consume_whitespace(text, pos + 1)
        if pos == len(text):
            return True

        ok, pos, partial = cls._consume_literal_prefix(text, pos, "tool_call")
        if not ok:
            return False
        if partial or pos == len(text):
            return True

        return not cls._is_word_char(text[pos]) and ">" not in text[pos:]

    @classmethod
    def _is_bare_seed_opener_prefix(cls, text: str) -> bool:
        if not text or text[0].lower() != "s":
            return False

        ok, pos, partial = cls._consume_literal_prefix(text, 0, "seed")
        if not ok:
            return False
        if partial:
            return True

        pos = cls._consume_whitespace(text, pos)
        if pos == len(text):
            return True
        if text[pos] != ":":
            return False

        pos = cls._consume_whitespace(text, pos + 1)
        if pos == len(text):
            return True

        ok, _pos, partial = cls._consume_literal_prefix(text, pos, "tool_call")
        return ok and partial


def log_tool_leak_filtered(
    event: ToolLeakFilterEvent,
    *,
    provider: str | None = None,
    session: str = "OmniOfflineClient",
) -> None:
    parts = [
        "[tool-leak-filter] stripped",
        f"provider={provider or 'unknown'}",
        f"session={session}",
        f"pattern={event.pattern}",
        f"chars={event.chars}",
        f"cross_chunk={str(event.cross_chunk).lower()}",
        f"finalized={str(event.finalized).lower()}",
    ]
    print(" ".join(parts))
