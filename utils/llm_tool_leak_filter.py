# -- coding: utf-8 --

from __future__ import annotations

from dataclasses import dataclass
import os
import re
import string
from typing import Iterable, Optional


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

# A call written into the reply as text instead of sent as a native tool call:
#   declaration:default_api:NAME{...}   default_api:NAME{...} / default_api.NAME(...)
#   asynccall:NAME{...}                 NAME(param=...)   NAME{param: ...}
# The prefixed forms are tool-call syntax whatever NAME is; a bare NAME only
# counts when it is a registered tool and an argument follows. Each opener is a
# list of steps so a chunk ending inside one can be held back (see
# ``_call_marker_tail_len``); the call then runs to its matching bracket.
_INLINE_CALL_PATTERN = "inline_tool_call"
_ASCII_WORD_CHARS = frozenset(string.ascii_letters + string.digits + "_")
_IDENT_START_CHARS = frozenset(string.ascii_letters + "_")
_IDENT_CHARS = frozenset(string.ascii_letters + string.digits + "_-")
_QUOTE_CHARS = "'" + '"'
_CALL_CLOSERS = {"(": ")", "[": "]", "{": "}"}
_PREFIXED_CALL_OPENERS = (
    (("lit", "declaration"), ("ws",), ("char", ":"), ("ws",), ("lit", "default_api"),
     ("ws",), ("char", ":."), ("ws",), ("ident",), ("ws",), ("char", "({")),
    (("lit", "default_api"), ("ws",), ("char", ":."), ("ws",), ("ident",), ("ws",), ("char", "({")),
    (("lit", "asynccall"), ("ws",), ("char", ":"), ("ws",), ("ident",), ("ws",), ("char", "({")),
)
# Lowercase text every prefixed or seed opener contains (``strip_tool_call_leaks``),
# taken from the openers themselves so a new prefix is never skipped.
_OPENER_MARKERS = ("seed", *dict.fromkeys(steps[0][1] for steps in _PREFIXED_CALL_OPENERS))
# The last character of every inline opener: only there can one complete.
_OPENER_END_CHARS = frozenset("({=:")
# ``_consume_inline_call``: the call was given up, resume at ``_call_resume``.
_CALL_GIVEN_UP = -2
_OPENER_FAIL = ("fail", 0)
_OPENER_PARTIAL = ("partial", 0)


def _named_call_openers(tool_name: str) -> tuple:
    return (
        (("lit", tool_name), ("ws",), ("char", "("), ("ws",), ("param",), ("ws",), ("char", "=")),
        (("lit", tool_name), ("ws",), ("char", "{"), ("ws",), ("param",), ("ws",), ("char", ":")),
    )


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
        self._call_openers = _PREFIXED_CALL_OPENERS + tuple(
            opener
            for tool_name in sorted(self._tool_names, key=len, reverse=True)
            for opener in _named_call_openers(tool_name)
        )
        self._call_first_chars = frozenset(
            steps[0][1][0].lower() for steps in self._call_openers
        )
        # How far back an opener completing in recovered text can start.
        self._opener_lookback = 128 + max((len(name) for name in self._tool_names), default=0)
        # A call opener must not continue a word, including one whose end was
        # already emitted with an earlier chunk.
        self._last_visible_char = ""
        self._reset_call_state()

    def feed(self, chunk: str) -> tuple[str, ToolLeakFilterEvent | None]:
        if not chunk:
            return "", None

        text = self._pending + str(chunk)
        had_pending = bool(self._pending)
        self._pending = ""
        output: list[str] = []
        event: ToolLeakFilterEvent | None = None

        while text:
            if self._suppressing and self._suppression_pattern == _INLINE_CALL_PATTERN:
                end = self._consume_inline_call(text)
                if end == _CALL_GIVEN_UP:
                    rest = self._call_resume
                    self._suppressed_chars += len(text) - len(rest)
                    event = self._finish_event(finalized=True)
                    self._last_visible_char = ""
                    text = rest
                    continue
                if end < 0:
                    self._suppressed_chars += len(text)
                    break
                self._suppressed_chars += end
                text = text[end:]
                event = self._finish_event()
                continue

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
        recovered: list[str] = []
        first_event: ToolLeakFilterEvent | None = None
        # A loop, not recursion: every recovered stretch can hold another
        # call that never closes.
        while (
            self._suppressing
            and self._suppression_pattern == _INLINE_CALL_PATTERN
            and self._call_recovery
        ):
            # The call never closed, but an outer closer went by (inside an
            # unclosed quote, or with an unclosed opener in a value): what
            # followed the first one is the reply, not the call.
            rest = "".join(self._call_recovery)
            self._suppressed_chars -= len(rest)
            event = self._finish_event(finalized=True)
            first_event = first_event or event
            self._last_visible_char = ""
            recovered.append(_feed_in_pieces(self, rest))
        if self._suppressing:
            self._suppressed_chars += len(self._pending)
            self._pending = ""
            event = self._finish_event(finalized=True)
            return "".join(recovered), first_event or event

        recovered.append(self._pending)
        self._pending = ""
        return "".join(recovered), first_event

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
        self._last_visible_char = ""
        self._reset_call_state()

    def _reset_call_state(self) -> None:
        # Text after the first outer closer of a call still open; None until one.
        self._call_recovery: list[str] | None = None
        # Whether that closer was inside a quote never closed (else nested).
        self._call_recovery_in_quote = False
        self._call_resume = ""
        self._call_closers: list[str] = []
        self._call_opened = False
        self._call_quote = ""
        self._call_escape = False
        self._call_last = ""

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
        self._reset_call_state()
        return event

    def _consume_inline_call(self, text: str) -> int:
        """Advance through a suppressed inline call; the index just past its
        matching bracket, or -1 when ``text`` ends inside it.

        Brackets nest; a quote opens a string only where a value starts (after
        ``( [ { = : ,``), so an apostrophe inside an unquoted value such as
        ``{instruction: don't}`` never swallows the rest of the reply. An outer
        closer that does not end the call (inside a quote never closed, or
        after a same-kind opener left open in a value) marks where the reply
        may resume: ``finalize`` gives back what followed the first one, and
        reads it again for further calls. A call that starts in that text
        settles it at once: the open call is given up there
        (``_CALL_GIVEN_UP``, the text to read on in ``_call_resume``), so
        unclosed calls in a row stay linear while a well-formed long call,
        whose nested closers also set a recovery point, still runs to its
        own closer.
        """
        for index, char in enumerate(text):
            if self._call_recovery is not None:
                self._call_recovery.append(char)
                if (
                    char in _OPENER_END_CHARS
                    # A recovery point a nested closer set: a call named in a
                    # quoted value of this call is data, not a new call.
                    and (self._call_recovery_in_quote or not self._call_quote)
                    and self._opens_call_at_end(self._call_recovery)
                ):
                    self._call_resume = "".join(self._call_recovery) + text[index + 1:]
                    return _CALL_GIVEN_UP
            outer_closer = (
                self._call_recovery is None
                and bool(self._call_closers) and char == self._call_closers[0]
            )
            if self._call_quote:
                if outer_closer:
                    self._call_recovery = []
                    self._call_recovery_in_quote = True
                if self._call_escape:
                    self._call_escape = False
                elif char == "\\":
                    self._call_escape = True
                elif char == self._call_quote:
                    self._call_quote = ""
                    self._call_last = char
                continue
            if char in _CALL_CLOSERS:
                self._call_closers.append(_CALL_CLOSERS[char])
                self._call_opened = True
            elif char in self._call_closers:
                # An opener left unclosed inside a value (``(靠左}``) must not
                # keep the call open past an outer closer, or the rest of the
                # reply goes with it.
                while self._call_closers.pop() != char:
                    pass
                if not self._call_closers:
                    return index + 1
                if outer_closer:
                    self._call_recovery = []
                    self._call_recovery_in_quote = False
            elif char in _QUOTE_CHARS and self._call_opened and self._call_last in "([{=:,":
                self._call_quote = char
            if not char.isspace():
                self._call_last = char
        return -1

    def _opens_call_at_end(self, chars: list[str]) -> bool:
        """Whether an inline call opener ends with the last of ``chars``."""
        window = "".join(chars[-self._opener_lookback:])
        for start in range(len(window)):
            if start and window[start - 1] in _ASCII_WORD_CHARS:
                continue
            if window[start].lower() not in self._call_first_chars:
                continue
            for steps in self._call_openers:
                state, end = self._match_call_opener(window, start, steps)
                if state == "full" and end == len(window):
                    return True
        return False

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
            start = seed.start()
            # ``<seed:tool_call`` whose ">" is still to come matches the bare
            # form: the "<" belongs to it, not to the reply.
            lead = text[:start].rstrip()
            if lead.endswith("<"):
                start = len(lead) - 1
            best = (start, seed.end(), "seed_tool_call")
        inline = self._inline_call_start(text, stop=best[0] if best else len(text))
        if inline is not None:
            best = inline
            if best[0] == 0:
                return best

        if self._tool_names:
            lower_text = text.lower()
            for tool_name in sorted(self._tool_names, key=len, reverse=True):
                search_from = 0
                lower_tool_name = tool_name.lower()
                while True:
                    idx = lower_text.find(lower_tool_name, search_from)
                    # Only a start before the best one so far can win; past it
                    # every further occurrence is wasted work (and slicing per
                    # occurrence made a reply full of calls quadratic).
                    if idx < 0 or (best is not None and idx >= best[0]):
                        break
                    name_close = _NAME_CLOSE_RE.match(text, idx + len(tool_name))
                    if name_close is not None:
                        after_name = name_close.end()
                        if (
                            _PARAMETER_RE.search(text, after_name)
                            or _FUNCTION_CLOSE_RE.search(text, after_name)
                        ):
                            start = self._structured_tool_start(text, idx)
                            candidate = (start, idx + len(tool_name), "structured_tool_call")
                            if best is None or candidate[0] < best[0]:
                                best = candidate
                                if best[0] == 0:
                                    return best
                    search_from = idx + len(tool_name)
        return best

    def _inline_call_start(self, text: str, *, stop: int) -> Optional[tuple[int, int, str]]:
        """The first complete inline call opener starting before ``stop``."""
        for start in range(min(stop, len(text))):
            if not self._may_open_call_at(text, start):
                continue
            for steps in self._call_openers:
                state, end = self._match_call_opener(text, start, steps)
                if state == "full":
                    return start, end, _INLINE_CALL_PATTERN
        return None

    def _may_open_call_at(self, text: str, start: int) -> bool:
        before = text[start - 1] if start else self._last_visible_char
        if before in _ASCII_WORD_CHARS:
            return False
        return text[start].lower() in self._call_first_chars

    @classmethod
    def _match_call_opener(cls, text: str, pos: int, steps) -> tuple[str, int]:
        """Match ``steps`` at ``pos``: ``("full", end)``, ``("partial", 0)``
        when ``text`` ends inside the opener, or ``("fail", 0)``."""
        for step in steps:
            kind = step[0]
            if kind == "ws":
                pos = cls._consume_whitespace(text, pos)
                continue
            if pos == len(text):
                return _OPENER_PARTIAL
            if kind == "lit":
                ok, pos, partial = cls._consume_literal_prefix(text, pos, step[1])
                if not ok:
                    return _OPENER_FAIL
                if partial:
                    return _OPENER_PARTIAL
            elif kind == "char":
                if text[pos] not in step[1]:
                    return _OPENER_FAIL
                pos += 1
            elif kind == "ident":
                if text[pos] not in _IDENT_START_CHARS:
                    return _OPENER_FAIL
                while pos < len(text) and text[pos] in _IDENT_CHARS:
                    pos += 1
            elif kind == "param":
                quote = text[pos] if text[pos] in _QUOTE_CHARS else ""
                pos += len(quote)
                if pos == len(text):
                    return _OPENER_PARTIAL
                if text[pos] not in _IDENT_START_CHARS:
                    return _OPENER_FAIL
                while pos < len(text) and text[pos] in _ASCII_WORD_CHARS:
                    pos += 1
                if quote:
                    if pos == len(text):
                        return _OPENER_PARTIAL
                    if text[pos] != quote:
                        return _OPENER_FAIL
                    pos += 1
        return "full", pos

    def _call_marker_tail_len(self, text: str) -> int:
        min_start = max(0, len(text) - self._max_tail)
        for start in range(min_start, len(text)):
            if not self._may_open_call_at(text, start):
                continue
            if any(
                self._match_call_opener(text, start, steps) == _OPENER_PARTIAL
                for steps in self._call_openers
            ):
                return len(text) - start
        return 0

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
        if text:
            self._last_visible_char = text[-1]
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
        best = max(best, self._call_marker_tail_len(text))
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

        # Whitespace may separate </name> from what follows it.
        pos = cls._consume_whitespace(text, pos)
        if pos == len(text):
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


def strip_tool_call_leaks(text: str, *, tool_names: Iterable[str] | None = None) -> str:
    """``text`` without the tool-call markup a ``ToolLeakFilter`` would hide.

    For finished text (a reply handed to memory, a memory rendered back into a
    prompt). Without ``tool_names`` only markup that is tool-call syntax
    whatever the tool (``seed:tool_call``, ``default_api:``, ``asynccall:``,
    the built-in default names) is removed: a bare ``name(param=...)`` only
    counts for a registered tool.
    """
    names = set(tool_names or ())
    if not text or not _may_hold_tool_call(text, names):
        return text
    leak_filter = ToolLeakFilter(tool_names=names)
    visible = _feed_in_pieces(leak_filter, text)
    tail, _event = leak_filter.finalize()
    return visible + tail


# Finished text is fed like a stream, a piece at a time: each ``feed`` scans
# what it is given, so one call over a long text full of leaks would rescan
# the remainder after every one of them.
_FINISHED_TEXT_PIECE = 256


def _feed_in_pieces(leak_filter: ToolLeakFilter, text: str) -> str:
    return "".join(
        leak_filter.feed(text[start:start + _FINISHED_TEXT_PIECE])[0]
        for start in range(0, len(text), _FINISHED_TEXT_PIECE)
    )


def _may_hold_tool_call(text: str, tool_names: set[str]) -> bool:
    """Whether ``text`` contains what every tool-call opener starts with.

    The filter scans every position against every opener; the finished-text
    helpers run on the event loop, and almost no reply holds any of these.
    """
    lowered = text.lower()
    return any(marker in lowered for marker in _OPENER_MARKERS) or any(
        name.lower() in lowered for name in (tool_names or _DEFAULT_TOOL_NAMES)
    )


def strip_tool_call_leaks_from_parts(
    texts: list[str], *, tool_names: Iterable[str] | None = None,
) -> list[str]:
    """``strip_tool_call_leaks`` over the text parts of one message.

    The parts are read as one stream, so a call split across two parts is
    still found. Each part keeps what it showed: text held back at a part's
    end goes back to that part once the next one shows it was no call, and
    the last part takes the rest. Parts with nothing cut come back as they
    were.
    """
    names = set(tool_names or ())
    if not _may_hold_tool_call("".join(texts), names):
        return list(texts)
    leak_filter = ToolLeakFilter(tool_names=names)
    cleaned: list[str] = []
    for text in texts:
        held = leak_filter._pending
        shown = _feed_in_pieces(leak_filter, text)
        if cleaned and held:
            # What the previous part held back comes out first, as far as it
            # was not the start of a call.
            given_back = len(os.path.commonprefix([held, shown]))
            cleaned[-1] += shown[:given_back]
            shown = shown[given_back:]
        cleaned.append(shown)
    tail, _event = leak_filter.finalize()
    if cleaned:
        cleaned[-1] += tail
    if "".join(cleaned) == "".join(texts):
        return list(texts)
    return cleaned


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
