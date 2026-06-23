# -- coding: utf-8 --

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Optional


_DEFAULT_TOOL_NAMES = frozenset({"recall_memory"})
_SEED_OPEN_RE = re.compile(r"<\s*seed\s*:\s*tool_call\b[^>]*>|(?<!/)\bseed\s*:\s*tool_call\b", re.IGNORECASE)
_SEED_CLOSE_RE = re.compile(r"<\s*/\s*seed\s*:\s*tool_call\s*>", re.IGNORECASE)
_PARAMETER_RE = re.compile(r"<\s*parameter\b[^>]*\bname\s*=", re.IGNORECASE)
_PARAMETER_CLOSE_RE = re.compile(r"<\s*/\s*parameter\s*>", re.IGNORECASE)
_PARAMETER_BOUNDARY_RE = re.compile(
    r"<\s*parameter\b[^>]*\bname\s*=[^>]*>|<\s*/\s*parameter\s*>",
    re.IGNORECASE,
)
_FUNCTION_CLOSE_RE = re.compile(r"<\s*/\s*function\s*>", re.IGNORECASE)
_FUNCTION_NAME_OPEN_TAIL_RE = re.compile(r"<\s*function\b[^>]*>\s*<\s*name\b[^>]*>\s*$", re.IGNORECASE)
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
        self._structured_tail = ""
        self._structured_tail_depth = 0
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
                close_match = self._suppression_close_match(text)
                if close_match:
                    self._track_suppressed_structure(text[: close_match.end()])
                    self._suppressed_chars += close_match.end()
                    text = text[close_match.end():]
                    event = self._finish_event()
                    continue
                keep_tail = self._suppression_close_tail_len(text)
                if keep_tail > 0:
                    consumed = text[:-keep_tail]
                    self._track_suppressed_structure(consumed)
                    self._suppressed_chars += len(consumed)
                    self._pending = text[-keep_tail:]
                else:
                    self._track_suppressed_structure(text)
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
            self._structured_tail = ""
            self._structured_tail_depth = 0

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
        self._structured_tail = ""
        self._structured_tail_depth = 0
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
        self._structured_tail = ""
        self._structured_tail_depth = 0
        return event

    def _suppression_close_match(self, text: str) -> re.Match[str] | None:
        if self._suppression_pattern != "structured_tool_call":
            return self._seed_close_match(text)

        seed_close = _SEED_CLOSE_RE.search(text)
        function_close = self._structured_function_close_match(text)
        if function_close is None:
            if seed_close is not None and self._structured_seed_close_can_finish(text, seed_close):
                return seed_close
            return None

        after_function = self._consume_whitespace(text, function_close.end())
        trailing_seed_close = _SEED_CLOSE_RE.match(text, after_function)
        if trailing_seed_close is not None:
            return trailing_seed_close

        trailing = text[after_function:]
        if not trailing:
            return None
        if self._is_seed_close_prefix(trailing):
            return None

        return function_close

    def _track_suppressed_structure(self, text: str) -> None:
        if not text:
            return

        base_depth = self._structured_tail_depth
        tracked = self._structured_tail + text
        trim = max(0, len(tracked) - self._max_tail)
        self._structured_tail_depth = self._parameter_depth_after(tracked[:trim], base_depth)
        self._structured_tail = tracked[-self._max_tail:]

    def _structured_seed_close_can_finish(self, text: str, seed_close: re.Match[str]) -> bool:
        return self._suppressed_parameter_depth_after(text[: seed_close.start()]) == 0

    def _seed_close_match(self, text: str) -> re.Match[str] | None:
        for seed_close in _SEED_CLOSE_RE.finditer(text):
            if self._suppressed_parameter_depth_after(text[: seed_close.start()]) == 0:
                return seed_close
        return None

    def _structured_function_close_match(self, text: str) -> re.Match[str] | None:
        for function_close in _FUNCTION_CLOSE_RE.finditer(text):
            if self._structured_function_close_can_finish(text, function_close):
                return function_close
        return None

    def _structured_function_close_can_finish(self, text: str, function_close: re.Match[str]) -> bool:
        return self._suppressed_parameter_depth_after(text[: function_close.start()]) == 0

    def _suppressed_parameter_depth_after(self, text: str) -> int:
        return self._parameter_depth_after(self._structured_tail + text, self._structured_tail_depth)

    @staticmethod
    def _parameter_depth_after(text: str, depth: int = 0) -> int:
        for match in _PARAMETER_BOUNDARY_RE.finditer(text):
            tag = match.group(0)
            if re.match(r"<\s*/", tag):
                depth = max(0, depth - 1)
            elif not re.search(r"/\s*>$", tag):
                depth += 1
        return depth

    def _suppression_close_tail_len(self, text: str) -> int:
        if self._suppression_pattern == "structured_tool_call":
            function_tail = self._pending_function_close_tail_len(text)
            if function_tail > 0:
                return function_tail

        min_start = max(0, len(text) - self._max_tail)
        for start in range(min_start, len(text)):
            tail = text[start:]
            if self._is_seed_close_prefix(tail):
                return len(tail)
            if self._suppression_pattern == "structured_tool_call" and self._is_function_close_prefix(tail):
                return len(tail)
            if self._suppression_pattern == "structured_tool_call" and _FUNCTION_CLOSE_RE.fullmatch(tail):
                return len(tail)
        return 0

    def _pending_function_close_tail_len(self, text: str) -> int:
        tail_len = 0
        for match in _FUNCTION_CLOSE_RE.finditer(text):
            after_function = self._consume_whitespace(text, match.end())
            trailing = text[after_function:]
            if not trailing or self._is_seed_close_prefix(trailing):
                tail_len = len(text) - match.start()
        return tail_len

    def _find_leak_start(self, text: str) -> Optional[tuple[int, int, str]]:
        seed = _SEED_OPEN_RE.search(text)
        best: Optional[tuple[int, int, str]] = None
        if seed:
            best = (seed.start(), seed.end(), "seed_tool_call")

        if self._tool_names:
            lower_text = text.lower()
            for tool_name in sorted(self._tool_names, key=len, reverse=True):
                search_from = 0
                lower_tool_name = tool_name.lower()
                while True:
                    idx = lower_text.find(lower_tool_name, search_from)
                    if idx < 0:
                        break
                    suffix = text[idx + len(tool_name):]
                    name_close = _NAME_CLOSE_RE.match(suffix)
                    if name_close is not None:
                        structured_suffix = suffix[name_close.end():]
                        if _PARAMETER_RE.search(structured_suffix) or _FUNCTION_CLOSE_RE.search(structured_suffix):
                            start = self._structured_tool_start(text, idx)
                            candidate = (start, idx + len(tool_name), "structured_tool_call")
                            if best is None or candidate[0] < best[0]:
                                best = candidate
                                if best[0] == 0:
                                    return best
                    search_from = idx + len(tool_name)
        return best

    @staticmethod
    def _structured_tool_start(text: str, tool_name_start: int) -> int:
        opener = _FUNCTION_NAME_OPEN_TAIL_RE.search(text[:tool_name_start])
        if opener:
            return opener.start()
        return tool_name_start

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
        best = self._seed_marker_tail_len(text)
        return max(best, self._structured_marker_tail_len(text))

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
        literal_lower = literal.lower()
        if not literal_lower.startswith(fragment):
            return False, pos, False
        if len(fragment) < len(literal_lower):
            return True, len(text), True
        return True, pos + len(literal_lower), False

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

    @classmethod
    def _is_seed_close_prefix(cls, text: str) -> bool:
        return cls._is_xml_close_prefix(text, "seed", ":", "tool_call")

    @classmethod
    def _is_function_close_prefix(cls, text: str) -> bool:
        return cls._is_xml_close_prefix(text, "function")

    @classmethod
    def _is_xml_close_prefix(
        cls,
        text: str,
        first_literal: str,
        separator: str | None = None,
        second_literal: str | None = None,
    ) -> bool:
        if not text or text[0] != "<":
            return False

        pos = cls._consume_whitespace(text, 1)
        if pos == len(text):
            return True
        if text[pos] != "/":
            return False

        pos = cls._consume_whitespace(text, pos + 1)
        if pos == len(text):
            return True

        ok, pos, partial = cls._consume_literal_prefix(text, pos, first_literal)
        if not ok:
            return False
        if partial:
            return True

        if separator is not None:
            pos = cls._consume_whitespace(text, pos)
            if pos == len(text):
                return True
            if text[pos] != separator:
                return False

            pos = cls._consume_whitespace(text, pos + 1)
            if pos == len(text):
                return True

            assert second_literal is not None
            ok, pos, partial = cls._consume_literal_prefix(text, pos, second_literal)
            if not ok:
                return False
            if partial:
                return True

        pos = cls._consume_whitespace(text, pos)
        if pos == len(text):
            return True
        return text[pos] == ">" and pos == len(text) - 1

    def _structured_marker_tail_len(self, text: str) -> int:
        min_start = max(0, len(text) - self._max_tail)
        tool_names = sorted(self._tool_names, key=len, reverse=True)
        for start in range(min_start, len(text)):
            tail = text[start:]
            if any(self._is_structured_tool_prefix(tail, tool_name) for tool_name in tool_names):
                return len(tail)
        return 0

    @classmethod
    def _is_structured_tool_prefix(cls, text: str, tool_name: str) -> bool:
        if not text:
            return False

        ok, pos, partial = cls._consume_function_name_open_prefix(text, 0)
        if ok:
            if partial or pos == len(text):
                return True
        else:
            pos = 0

        ok, pos, partial = cls._consume_literal_prefix(text, pos, tool_name)
        if not ok:
            return False
        if partial or pos == len(text):
            return True

        ok, pos, partial = cls._consume_name_close_prefix(text, pos)
        if not ok:
            return False
        if partial or pos == len(text):
            return True

        ok, _pos, partial = cls._consume_parameter_open_prefix(text, pos)
        if ok and partial:
            return True
        return cls._is_function_close_prefix(text[pos:])

    @classmethod
    def _consume_function_name_open_prefix(cls, text: str, pos: int) -> tuple[bool, int, bool]:
        ok, pos, partial = cls._consume_xml_open_prefix(text, pos, "function")
        if not ok or partial:
            return ok, pos, partial

        pos = cls._consume_whitespace(text, pos)
        if pos == len(text):
            return True, pos, True
        return cls._consume_xml_open_prefix(text, pos, "name")

    @classmethod
    def _consume_xml_open_prefix(cls, text: str, pos: int, literal: str) -> tuple[bool, int, bool]:
        if pos == len(text):
            return True, pos, True
        if text[pos] != "<":
            return False, pos, False

        pos = cls._consume_whitespace(text, pos + 1)
        if pos == len(text):
            return True, pos, True

        ok, pos, partial = cls._consume_literal_prefix(text, pos, literal)
        if not ok or partial:
            return ok, pos, partial

        if pos < len(text) and cls._is_word_char(text[pos]):
            return False, pos, False

        while pos < len(text):
            if text[pos] == ">":
                return True, pos + 1, False
            pos += 1
        return True, pos, True

    @classmethod
    def _consume_name_close_prefix(cls, text: str, pos: int) -> tuple[bool, int, bool]:
        if pos == len(text):
            return True, pos, True
        if text[pos] != "<":
            return False, pos, False

        pos += 1
        if pos == len(text):
            return True, pos, True
        if text[pos] != "/":
            return False, pos, False

        pos = cls._consume_whitespace(text, pos + 1)
        if pos == len(text):
            return True, pos, True

        ok, pos, partial = cls._consume_literal_prefix(text, pos, "name")
        if not ok or partial:
            return ok, pos, partial

        pos = cls._consume_whitespace(text, pos)
        if pos == len(text):
            return True, pos, True
        if text[pos] != ">":
            return False, pos, False
        return True, pos + 1, False

    @classmethod
    def _consume_parameter_open_prefix(cls, text: str, pos: int) -> tuple[bool, int, bool]:
        if pos == len(text):
            return True, pos, True
        if text[pos] != "<":
            return False, pos, False

        pos = cls._consume_whitespace(text, pos + 1)
        if pos == len(text):
            return True, pos, True

        ok, pos, partial = cls._consume_literal_prefix(text, pos, "parameter")
        if not ok or partial:
            return ok, pos, partial

        if pos < len(text) and cls._is_word_char(text[pos]):
            return False, pos, False

        while pos < len(text):
            if text[pos] == ">":
                return False, pos, False
            if text[pos].lower() == "n":
                ok, name_pos, name_partial = cls._consume_literal_prefix(text, pos, "name")
                if ok:
                    if name_partial:
                        return True, name_pos, True
                    after_name = cls._consume_whitespace(text, name_pos)
                    if after_name == len(text):
                        return True, after_name, True
                    if text[after_name] == "=":
                        return True, after_name + 1, False
                    if not cls._is_word_char(text[after_name]):
                        return False, after_name, False
            pos += 1

        return True, pos, True


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
