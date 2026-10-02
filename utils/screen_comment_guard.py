"""Keep chained screen-source narration out of the request view.

When an assistant message carries a confirmed chain, the request view keeps
only its first comment, as plain prose: every source label is removed, the
text before the chain stays, and everything from the second comment on is
dropped. The cut lands on that comment's sentence end, so what remains is a
whole sentence in the character's own voice, not a truncated fragment. A
chain spread over the assistant run before the current turn is rewritten the
same way across the run. A saved transcript is never rewritten, and no user
wording restores a removed comment. This removes the sample the model would
otherwise copy from, which is what feeds the observed propagation: polluted
history in, imitated chains out.

Detection needs a labelled, multi-item chain whose comments reach
``MIN_PROSE``. Unlabelled history and comments below that threshold pass
through unchanged and silently; see the incident record's applicability
and failure-boundary section for the measured limits.

This module deliberately does **not** filter this turn's own output. The
measured propagation stops once the stimulus is removed, so an output-side
guard is a fallback, not the causal fix; adding one without a correct
boundary story would trade a visible chain for truncated legitimate text.
"""
from __future__ import annotations

import os
from copy import copy
from functools import lru_cache

import regex


MIN_PROSE = 16
# Operator kill switch, read per request: a false positive in the field can be
# stopped without a release. Not reachable from anything a user says.
SCREEN_GUARD_ENV = "NEKO_SCREEN_HISTORY_GUARD"
_USER_ROLES = {"user", "human"}
_ASSISTANT_ROLES = {"assistant", "ai"}
# Labels are written by the model, never by the app, so this list only covers
# the forms seen so far (simplified and traditional Chinese, English). Other
# variants pass through; the request view does not depend on catching them all.
_CN_LABEL = (
    r"(?:当前|當前)?(?:屏幕|螢幕)"
    r"(?:搭话|搭話|画面|畫面|观察|觀察|内容|內容|截图|截圖|显示|顯示)"
)
_EN_LABEL = r"(?:current[ \t]{1,8})?screen[ \t]{1,8}(?:comment|observation|content|display|image)"
_LABEL = rf"{_CN_LABEL}|{_EN_LABEL}"
# A bracket followed by "(" or "[" closes link text ("[屏幕截图](url)",
# "[屏幕截图][1]", and the full-width "【屏幕截图】(url)" a model may write the
# same way), and one next to a path separator is a path segment
# ("/tmp/[屏幕截图]/a.png"); neither is a label.
_NOT_A_LABEL_AFTER_BRACKET = "([/\\"
_CLOSING_BRACKET = r"[】\]](?![(\[/\\])"
_OPENING_BRACKET = r"(?<![/\\])[【\[]"
# Match only through the first separator. No unbounded whitespace lookahead.
# The lexer checks the preceding character; complete and partial matches use
# the same engine (re and regex disagree about Unicode combining characters).
# The bare English label needs a colon: "screen comment " followed by a space
# is ordinary English grammar. The bare Chinese label counts with whitespace or
# a colon, the recorded shape; a reply that itself opens two sentences with
# that shape is lexically the same and gets the same verdict (design doc,
# section 7.1.3, item 5).
#
# A slash form needs a slash on both sides ("/屏幕画面/", the shape the
# proactive tag-leak stripper also knows) and must open a phrase: after the
# start, whitespace or punctuation (``_SLASH_OPENERS``, checked by the lexer).
# "屏幕截图/照片" and "照片/屏幕截图 给我" are ordinary either-or wording.
_SLASH_OPENERS = " \t\r\n。！？!?…～.~，,、：:；;“「『‘\"'（(【[《〈"
_MARKER = regex.compile(
    rf"(?:[/／][ \t]{{0,8}}(?:{_LABEL})[ \t]{{0,8}}[/／]"
    rf"|[【\[][ \t]{{0,8}}(?:{_LABEL})[ \t]{{0,8}}{_CLOSING_BRACKET}"
    r"|(?:屏幕|螢幕)(?:搭话|搭話)[\s:：]"
    r"|screen[ \t]{1,8}comment[:：])",
    regex.IGNORECASE,
)
_NOT_AFTER_WORD = r"(?<![A-Za-z0-9_])"
# Every label in an assistant message, with the separator after it, for the
# unconditional removal in the request view. The same forms as ``_MARKER`` but
# stateless: quotes, code and think blocks are no exemption here, because only
# the request copy of the model's own replies is touched. An English label
# glued to an ASCII word stays ("screenshot", "keyboard/screen display").
_LABEL_STRIP = regex.compile(
    rf"{_OPENING_BRACKET}[ \t]{{0,8}}(?:{_LABEL})[ \t]{{0,8}}{_CLOSING_BRACKET}[ \t]*(?:[:：][ \t]*)?"
    rf"|(?:^|(?<=[{_SLASH_OPENERS}]))[/／][ \t]{{0,8}}(?:{_LABEL})[ \t]{{0,8}}[/／]"
    r"[ \t]*(?:[:：][ \t]*)?"
    r"|(?<![/\\])(?:屏幕|螢幕)(?:搭话|搭話)(?=[\s:：])[ \t]*(?:[:：][ \t]*)?"
    rf"|{_NOT_AFTER_WORD}(?<![/\\])screen[ \t]{{1,8}}comment[:：][ \t]*",
    regex.IGNORECASE,
)
# Spans no label is looked for in: a URL up to whitespace, closing punctuation
# or a sentence end ("…", "～"), and a Markdown link target "](...)". Paths in
# them may name a label ("https://host/屏幕搭话：a.png").
# Balanced parentheses, nested or not, are part of either ("a_(1)/b.png").
# A URL may also come without its scheme: protocol-relative with any host
# ("//cdn.host/", "//192.168.1.10/", "//[::1]/", "//localhost:8080?a=b") or a bare
# domain or IPv4 host with a path ("www.host.com/", "10.0.0.2:8080/"), and a
# drive path written with forward slashes ("C:/a.png") is one too. A
# Markdown reference definition's target may also be a path ("[1]: /a.png",
# "./a.png", "../a.png").
_PROTECTED = regex.compile(
    r"(?:[A-Za-z][A-Za-z0-9+.\-]*://(?:\[[0-9A-Fa-f:.]+\])?"
    r"|(?<![\w:/.@])//(?:\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9\-.]+)(?::\d+)?(?=[/?#])"
    r"|(?<![\w:/.@])[A-Za-z]:(?=/)"
    r"|(?<![\w:/.@])(?:[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}|\d{1,3}(?:\.\d{1,3}){3})(?::\d+)?(?=/)"
    r"|(?<=(?:^|\n)[ ]{0,3}\[[^\]\n]+\]:[ \t]{0,8})(?:\.{1,2})?(?=/))"
    r"(?:[^\s<>\"'()\]】）」』，。！？；、…～]|(?&paren))+"
    r"|(?<=[\]】]\()(?:[^()\s]|(?&paren))+"
    r"(?(DEFINE)(?P<paren>\((?:[^\s()]|(?&paren))*\)))"
)
# Stand in for a protected and a thinking character (Unicode private use).
_MASK = "\ue000"
_THINK_MASK = "\ue001"
# A thinking block, closed or running to the end of the text.
_THINK_BLOCK = regex.compile(
    r"<think(?:ing)?[ \t]{0,8}>.*?(?:</think(?:ing)?[ \t]{0,8}>|\Z)",
    regex.IGNORECASE | regex.DOTALL,
)
# What a message that held only a label shows when it must stay in the view.
_NOTHING_SAID = "…"
_THINK_TAG = regex.compile(r"</?think(?:ing)?[ \t]{0,8}>", regex.IGNORECASE)
_QUOTES = {"“": "”", "「": "」", "『": "』", "‘": "’", '"': '"', "'": "'"}
# Every marker form carries one of these; texts without either skip the lexer.
_LABEL_HINT = regex.compile(r"屏幕|螢幕|screen", regex.IGNORECASE)
# Always a sentence end.
_SENTENCE_ENDS = "。！？!?…～"
# A sentence end unless an ASCII letter or digit follows ("example.com",
# "1.5", "v2.0", "a~b"), or unless what follows is the next label
# ("today.screen comment:").
_SOFT_SENTENCE_ENDS = ".~"
_CLOSERS = "」』”’\"')）】》"
# What may sit between a removed label and the prose it introduces.
_LABEL_SEPARATORS = " \t:："


def screen_guard_enabled() -> bool:
    """On unless the operator switch ``NEKO_SCREEN_HISTORY_GUARD`` says off.

    Kept as a function because providers thread its result as a per-call
    override, and because turning the guard off is not something any user
    wording is allowed to request.
    """
    raw = os.environ.get(SCREEN_GUARD_ENV, "").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _role_and_content(message):
    if isinstance(message, dict):
        return message.get("role"), message.get("content")
    return getattr(message, "type", None), getattr(message, "content", None)


def _text_of(content) -> str | None:
    """The text a message body shows the model, or ``None`` when it has none.

    Memory restores assistant messages with list content
    (``[{"type": "text", "text": ...}]``), so a string-only check would let
    every restored chain through. Text parts are joined with newlines, the
    way the renderers join them.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            part.get("text")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
            and isinstance(part.get("text"), str)
        ]
        return "\n".join(parts) if parts else None
    return None


def _is_tool_image_turn(messages, index) -> bool:
    """Whether ``messages[index]`` is a user turn the tool loop injected.

    Tool results that carry pictures are followed by ``{"role": "user"}``
    dicts (see ``_append_tool_result_images``). They are not the user
    speaking, and counting one as "the last user turn" would move the
    assistant run off the real one on every request after such a tool.
    A real user turn in the saved transcript is a message object.
    """
    for back in range(index, -1, -1):
        message = messages[back]
        role, _content = _role_and_content(message)
        if back < index and role == "tool":
            return True
        if not (isinstance(message, dict) and role in _USER_ROLES):
            return False
    return False


def _assistant_tail_run(messages, *, trailing_turn: bool = False) -> tuple[int, int]:
    """Half-open range of the consecutive assistant messages that answer the
    last user turn; ``(0, 0)`` when there is no such run.

    The run must sit immediately before the last user message *and* be
    preceded by a user message. Both halves are load-bearing and were measured
    separately:

    * ``[u, a, a, ask]`` and ``[u, a×7, ask]`` chain (5/5 and 2/2), so a run
      before the current turn does propagate.
    * ``[a×7, u]`` with no preceding user does **not** chain (0/3).
      This positional condition does not identify message origin: independent
      proactive deliveries can also occur between two user turns and then
      satisfy the same rule. Internal proactive source markers split that run
      during projection; unmarked legacy deliveries remain ambiguous.
    * ``[a×7, u1, ask]`` does not chain either (0/2): an intervening user turn
      ends it.
    * A non-assistant message ends the run, which is why the measured
      tool-boundary case does not chain (0/3).

    Image turns injected by the tool loop are not user turns and are skipped
    when looking for the last one.

    ``trailing_turn`` treats the end of ``messages`` as followed by a user
    turn. History restored into a new session's prompt is exactly that: the
    next thing the model sees is the user speaking, so the run at the end is
    the one that answers "the current turn".
    """
    # A restored history that already ends in a user line has its own last
    # user turn; only one that ends elsewhere is followed by the next turn.
    if trailing_turn and not (
        messages and _role_and_content(messages[-1])[0] in _USER_ROLES
    ):
        last_user = len(messages)
    else:
        last_user = next(
            (
                index for index in range(len(messages) - 1, -1, -1)
                if _role_and_content(messages[index])[0] in _USER_ROLES
                and not _is_tool_image_turn(messages, index)
            ),
            -1,
        )
    if last_user <= 1:
        return 0, 0
    start = last_user
    while start > 0:
        role, _content = _role_and_content(messages[start - 1])
        if role not in _ASSISTANT_ROLES:
            break
        start -= 1
    if start == 0 or start == last_user:
        return 0, 0
    # Require an actual preceding user; position alone does not prove origin.
    preceding_role, _content = _role_and_content(messages[start - 1])
    if preceding_role not in _USER_ROLES:
        return 0, 0
    return start, last_user


def _is_independent_delivery(message) -> bool:
    metadata = (message.get("additional_kwargs", {}) if isinstance(message, dict)
                else getattr(message, "additional_kwargs", {}))
    return isinstance(metadata, dict) and metadata.get("dialog_source") == "proactive"


def _has_tool_calls(message) -> bool:
    if isinstance(message, dict):
        return bool(message.get("tool_calls"))
    return bool(getattr(message, "tool_calls", None))


def _role_is_assistant(message) -> bool:
    return message is not None and _role_and_content(message)[0] in _ASSISTANT_ROLES


def _with_content(message, content):
    if isinstance(message, dict):
        return {**message, "content": content}
    message = copy(message)
    message.content = content
    return message


def _is_text_part(part) -> bool:
    return (isinstance(part, dict) and part.get("type") == "text"
            and isinstance(part.get("text"), str))


def _non_text_parts(message) -> list:
    _, content = _role_and_content(message)
    if not isinstance(content, list):
        return []
    return [part for part in content if not _is_text_part(part)]


def _split_over(texts, text):
    """``text`` split back over the text parts it came from, or ``None``.

    A rewrite removes labels and may cut the joined text at a sentence end,
    so it is a prefix of the parts with their labels removed. Anything else
    (``None``) falls back to one text slot.
    """
    remaining, pieces = text, []
    for original in texts:
        # The joining newline, and any whitespace a part starts or ends with
        # (a removed label keeps the text's own indentation).
        remaining = remaining.lstrip()
        label_free = _strip_labels(original)
        core = label_free.strip()
        # The part keeps its own indentation and trailing whitespace.
        lead = label_free[:len(label_free) - len(label_free.lstrip())]
        if not core:
            # Spacing between images, or a part that held only a label.
            pieces.append(label_free)
        elif not remaining:
            pieces.append("")
        elif remaining.startswith(core):
            pieces.append(label_free)
            remaining = remaining[len(core):]
        elif core.startswith(remaining):
            pieces.append(lead + remaining)
            remaining = ""
        else:
            return None
    return pieces if not remaining.strip() else None


def _with_text(message, text):
    """``message`` showing ``text``; non-text parts of list content stay.

    Text keeps its slots among images: each text part gets its share of the
    rewrite, or, when the rewrite cannot be split back, the first text slot
    takes all of it.
    """
    if not _non_text_parts(message):
        return _with_content(message, text)
    _, content = _role_and_content(message)
    texts = [part["text"] for part in content if _is_text_part(part)]
    pieces = _split_over(texts, text) if text else [""] * len(texts)
    if pieces is None:
        pieces = [text] + [""] * (len(texts) - 1)
    pieces = iter(pieces)
    parts = []
    for part in content:
        if not _is_text_part(part):
            parts.append(part)
        else:
            piece = next(pieces)
            if piece:
                parts.append({**part, "text": piece})
    if text and not texts:
        parts.insert(0, {"type": "text", "text": text})
    return _with_content(message, parts)


def screen_history_rewrites(messages, *, trailing_turn: bool = False,
                            hits: dict | None = None) -> dict:
    """Map each assistant message the request view rewrites to its new text.

    A value of ``None`` means the message has nothing left to say (it lies
    wholly past a chain's cut, or was only a label). Indices not in the
    result are untouched.

    Chains are cut first (``_chain_rewrites``). Then every remaining
    assistant text loses its source labels, chain or not, because a single
    labelled comment is still a sample of the format (``_strip_labels``).

    ``trailing_turn`` is for restored history: the end of ``messages`` is
    treated as followed by the next user turn (see ``_assistant_tail_run``).
    ``hits``, when given, receives the count of rewritten messages per
    category: ``"message"`` for a chain inside one message, ``"run"`` for one
    spread over the assistant run, and ``"label"`` for labels removed outside
    a chain's cut.
    """
    rewrites = _chain_rewrites(messages, trailing_turn=trailing_turn, hits=hits)
    for index, message in enumerate(messages):
        role, content = _role_and_content(message)
        original = _text_of(content)
        if role not in _ASSISTANT_ROLES or original is None:
            continue
        current = rewrites.get(index, original)
        if current is None:
            continue
        stripped = _strip_labels(current)
        if stripped != current:
            # Only the label goes; indentation and other whitespace around it
            # can be Markdown structure (code blocks, line breaks).
            rewrites[index] = stripped if stripped.strip() else None
            if hits is not None:
                hits["label"] = hits.get("label", 0) + 1
    return rewrites


def _strip_labels(text: str) -> str:
    """``text`` without any source label (see ``_LABEL_STRIP``).

    URLs and Markdown link targets (``_PROTECTED``) are left as they are.
    """
    if not _LABEL_HINT.search(text):
        return text
    kept, position = [], 0
    for match in _PROTECTED.finditer(text):
        kept.append(_LABEL_STRIP.sub("", text[position:match.start()]))
        kept.append(match.group())
        position = match.end()
    kept.append(_LABEL_STRIP.sub("", text[position:]))
    return "".join(kept)


def _masked(text: str) -> tuple[str, str]:
    """``text`` with ``_PROTECTED`` spans and thinking hidden from the lexer.

    A protected character becomes ``_MASK`` (still prose for a comment's
    length), a thinking character ``_THINK_MASK`` (not prose: hidden
    reasoning never completes a visible comment). Offsets hold; the second
    value holds the hidden characters in order for ``_unmasked``.
    """
    if _MASK in text or _THINK_MASK in text:
        return text, ""
    spans = sorted(
        [(m.start(), m.end(), _MASK) for m in _PROTECTED.finditer(text)]
        + [(m.start(), m.end(), _THINK_MASK) for m in _THINK_BLOCK.finditer(text)]
    )
    masked, hidden, position = [], [], 0
    for start, end, mask in spans:
        if start < position:
            continue
        masked.append(text[position:start])
        masked.append(mask * (end - start))
        hidden.append(text[start:end])
        position = end
    if not hidden:
        return text, ""
    masked.append(text[position:])
    return "".join(masked), "".join(hidden)


def _unmasked(text: str, hidden: str) -> str:
    """Put hidden characters back. A rewrite only drops markers (never
    masked) and cuts the end, so masks keep their order from the start."""
    if not hidden:
        return text
    characters = iter(hidden)
    return "".join(
        next(characters) if char in (_MASK, _THINK_MASK) else char for char in text
    )


def _chain_rewrites(messages, *, trailing_turn: bool = False,
                    hits: dict | None = None) -> dict:
    """The chain cuts of ``screen_history_rewrites``, before label removal.

    Every assistant message is judged alone first. Then the consecutive
    assistant run immediately before the last user turn is judged as one
    sequence over the original texts, because a chain spread one comment per
    message is invisible to a per-message check; where it finds a chain, its
    result replaces the per-message one for every message of the run (a
    chain that starts inside one message and continues in the next is cut
    once, at its second comment). Internally marked proactive deliveries
    split the run and are checked individually. Unmarked legacy deliveries
    remain positionally ambiguous.
    """
    rewrites: dict = {}
    originals: dict = {}
    for index, message in enumerate(messages):
        role, content = _role_and_content(message)
        text = _text_of(content)
        if role not in _ASSISTANT_ROLES or text is None:
            continue
        originals[index] = text
        rewritten = _dechained((text,))
        if rewritten is not None:
            rewrites[index] = rewritten[0]
            if hits is not None:
                hits["message"] = hits.get("message", 0) + 1
    tail_start, tail_end = _assistant_tail_run(messages, trailing_turn=trailing_turn)
    segment_start = tail_start
    for boundary in range(tail_start, tail_end + 1):
        if boundary < tail_end and not _is_independent_delivery(messages[boundary]):
            continue
        indices = [index for index in range(segment_start, boundary) if index in originals]
        segment_start = boundary + 1
        if len(indices) < 2:
            continue
        rewritten = _dechained(tuple(originals[index] for index in indices))
        if rewritten is None:
            continue
        # Messages before the cut carry no chain of their own (the cut is the
        # first chain), so the run's result never undoes a per-message one.
        for index, text in zip(indices, rewritten):
            if text != rewrites.get(index, originals[index]):
                rewrites[index] = text
                if hits is not None:
                    hits["run"] = hits.get("run", 0) + 1
    return rewrites


def project_screen_history(messages, *, guard_enabled: bool | None = None,
                           hits: dict | None = None, trailing_turn: bool = False):
    """Return a request-only view; keep saved transcripts and tool metadata.

    A message carrying a confirmed chain keeps its text before the chain and
    the first comment of it, with every source label removed, closed at that
    comment's sentence end. The rest of the chain is dropped. A message left
    with nothing is dropped from the view, unless it carries ``tool_calls``,
    in which case it stays with empty content so tool-result pairing holds,
    or unless no assistant turn is kept on either side of it, in which case
    it stays as ``_NOTHING_SAID`` so user and assistant turns still
    alternate.
    Non-text parts of list content (images and the like) are kept.
    Role, ``tool_calls`` and every other key are preserved. Only the copy is
    touched; ``messages`` is never mutated, and ``messages`` itself is
    returned when nothing matched.

    Every source label is removed from assistant texts, chain or not.
    Unlabelled history is left byte-for-byte alone, silently.
    See ``screen_history_rewrites`` for ``trailing_turn`` and ``hits``.
    """
    if guard_enabled is None:
        guard_enabled = screen_guard_enabled()
    if not guard_enabled:
        return messages
    rewrites = screen_history_rewrites(messages, trailing_turn=trailing_turn, hits=hits)
    if not rewrites:
        return messages
    projected = []
    for index, message in enumerate(messages):
        if index not in rewrites:
            projected.append(message)
        elif rewrites[index] is not None:
            projected.append(_with_text(message, rewrites[index]))
        elif _has_tool_calls(message) or _non_text_parts(message):
            projected.append(_with_text(message, ""))
        elif not (_role_is_assistant(projected[-1] if projected else None)
                  or (_role_is_assistant(messages[index + 1] if index + 1 < len(messages) else None)
                      and rewrites.get(index + 1, "") is not None)):
            # Leaving it out would put two user turns side by side, which
            # some chat templates reject; an empty text part is rejected by
            # others. A lone label becomes a neutral ellipsis.
            projected.append(_with_text(message, _NOTHING_SAID))
    return projected


def strip_screen_labels(messages, *, guard_enabled: bool | None = None):
    """Remove source labels from assistant texts and cut nothing else.

    For text the user has already seen this turn (the tool loop's assistant
    turns): the model keeps every comment it streamed, so it does not say one
    again, but not the labelled format. Returns ``messages`` itself when no
    label is found, or when the operator switch turns the guard off.
    """
    if guard_enabled is None:
        guard_enabled = screen_guard_enabled()
    if not guard_enabled:
        return messages
    projected = None
    for index, message in enumerate(messages):
        role, content = _role_and_content(message)
        if role not in _ASSISTANT_ROLES:
            continue
        text = _text_of(content)
        if text is None:
            continue
        stripped = _strip_labels(text)
        if stripped == text:
            continue
        if projected is None:
            projected = list(messages)
        projected[index] = _with_text(message, stripped if stripped.strip() else "")
    return messages if projected is None else projected


class _ScreenLexer:
    """Incremental source-marker lexer; emitted text is never retained.

    Quotes/escapes, code delimiter lengths and line starts are state, not a
    regex over the accumulated reply. Tokens held for lookahead are bounded by
    the marker/tag grammar. Delimiter runs are counted, not buffered.
    """

    def __init__(self):
        self.position = 0
        self.previous = ""
        self.indent = 0
        self.line_prefix = True
        self.pending = ""
        self.pending_kind = ""
        self.pending_after_word = False
        # A complete bracket marker waits one character: "(" makes it
        # Markdown link text.
        self.pending_closed = False
        self.quote = ""
        self.escaped = False
        self.thinking = False
        self.blockquote = False
        self.indented_code = False
        self.code = ""
        self.code_length = 0
        self.fence = False
        self.fence_tail = False
        self.run = ""
        self.run_length = 0
        self.run_opening = False
        self.run_at_start = False

    def _emit(self, text, marker=False):
        start = self.position
        self.position += len(text)
        for char in text:
            if char == "\n":
                self.indent, self.line_prefix = 0, True
            elif char in " \t\r" and self.line_prefix:
                self.indent = min(4, self.indent + (4 if char == "\t" else 1))
            else:
                self.indent, self.line_prefix = 4, False
        self.previous = text[-1:]
        return text, marker, start

    def _end_run(self):
        if self.run_opening:
            if self.run == "`" or self.run_length >= 3:
                self.code = self.run
                self.code_length = self.run_length
                self.fence = self.run_at_start and self.run_length >= 3
        elif self.fence:
            self.fence_tail = self.run_length >= self.code_length
        elif self.run_length == self.code_length:
            self.code = ""
        self.run = ""

    def _accept(self, char):
        if self.pending_closed:
            held, self.pending, self.pending_closed = self.pending, "", False
            if char not in _NOT_A_LABEL_AFTER_BRACKET:
                return [self._emit(held, True), *self._accept(char)]
            result = [self._emit(held[0])]
            for rest in held[1:] + char:
                result.extend(self._accept(rest))
            return result
        if self.pending:
            candidate = self.pending + char
            pattern = _MARKER if self.pending_kind == "marker" else _THINK_TAG
            match = pattern.fullmatch(candidate, partial=True)
            if (match is not None and not match.partial and self.pending_kind == "marker"
                    and self.pending_after_word and _has_ascii_letter(candidate)):
                # An English label glued to an ASCII word is part of that word
                # or phrase ("screenshot", "prescreen", "keyboard/screen
                # display"). Rescan from the next character.
                match = None
            if match is not None:
                self.pending = candidate if match.partial else ""
                if match.partial:
                    return []
                if self.pending_kind == "tag":
                    self.thinking = not candidate.startswith("</")
                elif candidate.endswith(("]", "】")):
                    self.pending, self.pending_closed = candidate, True
                    return []
                return [self._emit(candidate, self.pending_kind == "marker")]
            self.pending = ""
            result = [self._emit(candidate[0])]
            for rest in candidate[1:]:
                result.extend(self._accept(rest))
            return result

        if self.run:
            if char == self.run:
                self.run_length += 1
                return [self._emit(char)]
            self._end_run()
        if self.fence_tail:
            if char == "\n":
                self.code = ""
                self.fence_tail = False
                return [self._emit(char)]
            if char in " \t\r":
                return [self._emit(char)]
            self.fence_tail = False
        if self.code:
            if char == self.code and (not self.fence or self.indent <= 3):
                self.run, self.run_length = char, 1
                self.run_opening = False
            return [self._emit(char)]
        if self.blockquote:
            # An unprefixed continuation of the same paragraph is still a
            # Markdown quote. Fail open until a blank line ends that paragraph.
            if char == "\n" and self.line_prefix:
                self.blockquote = False
            return [self._emit(char)]
        if self.indented_code or (self.line_prefix and self.indent >= 4):
            self.indented_code = char != "\n"
            return [self._emit(char)]
        if self.escaped:
            self.escaped = False
            return [self._emit(char)]
        if self.quote:
            if char == "\\":
                self.escaped = True
            elif char == self.quote:
                self.quote = ""
            return [self._emit(char)]
        if char == "<":
            self.pending, self.pending_kind = char, "tag"
            return []
        if self.thinking:
            return [self._emit(char)]
        if char == "\\":
            self.escaped = True
        elif char == ">" and self.indent <= 3:
            self.blockquote = True
        elif char in "`~" and (char == "`" or self.indent <= 3):
            self.run, self.run_length = char, 1
            self.run_opening, self.run_at_start = True, self.indent <= 3
        elif char in _QUOTES:
            # ASCII apostrophes/inch marks attached to words/numbers are not
            # quote openers. A real quote opened earlier still closes normally.
            attached = self.previous.isalnum() or self.previous == "_"
            if not ((char == '"' and self.previous.isdigit()) or (char == "'" and attached)):
                self.quote = _QUOTES[char]
        elif char in "/／" and self.previous and self.previous not in _SLASH_OPENERS:
            pass
        elif char in "【[屏螢sS" and self.previous in ("/", "\\"):
            # A path segment ("/tmp/屏幕搭话：a.png", "C:\\tmp\\[屏幕画面]\\",
            # "/tmp/screen comment:a.png").
            pass
        elif char in "/／屏螢当當【[sScC":
            # An ASCII word character before a marker blocks it only when the
            # label is English (checked once the marker is complete). A
            # Chinese label counts after any character, so "…喵屏幕搭话 …",
            # "…233屏幕搭话 …" and "…QwQ屏幕搭话 …" are markers.
            self.pending, self.pending_kind = char, "marker"
            self.pending_after_word = _is_ascii_word_char(self.previous)
            return []
        return [self._emit(char)]

    def feed(self, text):
        for char in text:
            yield from self._accept(char)

    def finalize(self):
        pending, self.pending = self.pending, ""
        closed, self.pending_closed = self.pending_closed, False
        if pending:
            yield self._emit(pending, closed)
        if self.run:
            self._end_run()


def _sentence_ends(text: str, offset: int = 0, glued: set | None = None) -> set:
    """Positions (plus ``offset``) of the characters in ``text`` that end a sentence.

    ``glued``, when given, receives the positions of soft ends that only an
    ASCII letter or digit follows; the tracker counts those when the next
    character turns out to start a label.
    """
    ends = set()
    for index, char in enumerate(text):
        if char in _SENTENCE_ENDS:
            ends.add(offset + index)
        elif char in _SOFT_SENTENCE_ENDS:
            following = text[index + 1:index + 2]
            if not (following.isascii() and following.isalnum()):
                ends.add(offset + index)
            elif glued is not None:
                glued.add(offset + index)
    return ends


class _ChainTracker:
    """Follow labelled comments and report the first chain.

    A chain is a complete comment (at least ``MIN_PROSE`` characters, then a
    sentence end) followed later by another complete one. Short comments in
    between do not break it; the cut is the label right after the first
    complete comment, so they are cut together with the rest.
    """

    def __init__(self, ends, glued=frozenset()):
        self.ends = ends
        self.glued = glued
        self.start = None
        self.first = None
        self.cut = None
        self.length = 0
        self.complete = False

    def accept(self, text, marker, start):
        if marker:
            if self.length >= MIN_PROSE and start - 1 in self.glued:
                self.complete = True
            # The label itself may be what completes the second comment.
            if self.complete and self.first is not None:
                return self.first, self.cut
            if self.complete and self.first is None:
                self.first, self.cut = self.start, start
            self.start, self.length, self.complete = start, 0, False
        elif self.start is not None:
            for offset, char in enumerate(text):
                if char == _THINK_MASK:
                    continue
                if self.length or not char.isspace():
                    self.length = min(MIN_PROSE, self.length + 1)
                if self.length >= MIN_PROSE and start + offset in self.ends:
                    self.complete = True
            if self.complete and self.first is not None:
                return self.first, self.cut
        return None


def _tokens_across(texts):
    """Yield ``(index, text, marker, start)`` over ``texts`` as one stream.

    Each text gets a fresh lexer: its lexical state (an open quote or
    ``<think>``, a fence, a quote block, the word before a marker) ends with
    the text, so a reply ending in a CJK letter or an unclosed quote cannot
    hide the next one's label. Positions are offset by the preceding lengths,
    so one tracker can follow a chain across the boundary.
    """
    offset = 0
    for index, text in enumerate(texts):
        lexer = _ScreenLexer()
        for tokens in (lexer.feed(text), lexer.finalize()):
            for token_text, marker, start in tokens:
                yield index, token_text, marker, start + offset
        offset += len(text)


def _find_chain(texts):
    """Locate the first chain over ``texts``.

    Returns ``(first, cut, cut_index, tokens)``: where the first complete
    comment's label starts, where the label after it starts (global
    offsets), the index of the text holding ``cut``, and each text's tokens
    up to the point the chain was confirmed. ``None`` when there is no chain.
    """
    ends: set = set()
    glued: set = set()
    starts = []
    offset = 0
    for text in texts:
        starts.append(offset)
        ends |= _sentence_ends(text, offset, glued)
        offset += len(text)
    tracker = _ChainTracker(ends, glued)
    tokens: list[list] = [[] for _ in texts]
    for index, token_text, marker, start in _tokens_across(texts):
        tokens[index].append((token_text, marker, start))
        found = tracker.accept(token_text, marker, start)
        if found is not None:
            first, cut = found
            cut_index = max(i for i, begin in enumerate(starts) if begin <= cut)
            return first, cut, cut_index, tokens
    return None


def _through_last_sentence(text: str) -> str:
    """``text`` up to its last sentence end and the closing marks after it."""
    ends = _sentence_ends(text)
    if not ends:
        return ""
    end = max(ends) + 1
    while end < len(text) and text[end] in _CLOSERS:
        end += 1
    return text[:end]


def _unlabelled(tokens, cut) -> str:
    """The text of ``tokens`` before ``cut``, with every label removed.

    The separator after a removed label (spaces, one colon) goes with it, so
    a label written with a space before its full-width colon, or
    "here. screen comment: the", leaves neither a stray colon nor a double
    space.
    """
    kept = []
    after_label = colon_seen = False
    for token_text, marker, start in tokens:
        if start >= cut:
            break
        if marker:
            after_label, colon_seen = True, False
            continue
        for char in token_text:
            if after_label and char in _LABEL_SEPARATORS:
                if char in ":：":
                    if colon_seen:
                        after_label = False
                        kept.append(char)
                    colon_seen = True
                continue
            after_label = False
            kept.append(char)
    return "".join(kept)


def _dechain(texts) -> tuple | None:
    """Rewrite a chain over ``texts`` down to its first comment.

    Returns one entry per text: the text itself when untouched, its rewrite,
    or ``None`` when nothing of it is left. Returns ``None`` when ``texts``
    carry no chain.

    Before the cut every source label is removed and the rest is kept as is;
    the text the cut falls in is then closed at its last sentence end, which
    the first comment is guaranteed to have (that is what made it complete).
    Texts past the cut are dropped.
    """
    if not any(_LABEL_HINT.search(text) for text in texts):
        return None
    found = _find_chain(texts)
    if found is None:
        return None
    _first, cut, cut_index, tokens = found
    rewritten = []
    for index, text in enumerate(texts):
        if index > cut_index:
            rewritten.append(None)
            continue
        if index < cut_index and not any(marker for _text, marker, _start in tokens[index]):
            rewritten.append(text)
            continue
        kept = _unlabelled(tokens[index], cut)
        # Only emptiness is judged on stripped text; the rest keeps its
        # whitespace (blank lines, indentation) as is.
        rewritten.append(kept if kept.strip() else None)
    # The first comment closes at its last sentence end, which may lie in an
    # earlier text than the cut; texts after it hold only a dangling fragment.
    for index in range(cut_index, -1, -1):
        kept = _through_last_sentence(rewritten[index] or "")
        if kept.strip():
            rewritten[index] = kept
            break
        rewritten[index] = None
    return tuple(rewritten)


# Most history never mentions a screen; skip the pure-Python lexer for it, and
# cache only texts that do, so ordinary chat cannot evict the ones that matter.
# The run is re-judged on every provider call over the same texts, so it is
# cached the same way.
_cached_dechain = lru_cache(maxsize=1024)(_dechain)


def _dechained(texts: tuple) -> tuple | None:
    if not any(_LABEL_HINT.search(text) for text in texts):
        return None
    pairs = [_masked(text) for text in texts]
    masked = tuple(text for text, _hidden in pairs)
    result = _cached_dechain(masked)
    if result is None or masked == texts:
        return result
    return tuple(
        None if text is None else _unmasked(text, hidden)
        for text, (_masked_text, hidden) in zip(result, pairs)
    )


def _is_ascii_word_char(char: str) -> bool:
    return char.isascii() and (char.isalnum() or char == "_")


def _has_ascii_letter(text: str) -> bool:
    """Whether a marker carries an English label; Chinese labels have none."""
    return any(char.isascii() and char.isalpha() for char in text)


def screen_chain_start(text: str) -> int | None:
    """Where the first chain in ``text`` starts (its first comment's label)."""
    found = _find_chain([_masked(text)[0]])
    return None if found is None else found[0]
