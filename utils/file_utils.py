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

from __future__ import annotations

import asyncio
import errno
import json
import logging
import os
import re
import secrets
import threading
import time
import unicodedata
from contextlib import suppress
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

# ── LLM JSON tolerance ─────────────────────────��────────────────────────
# LLM JSON often has formatting defects (unquoted keys, trailing commas, Python literals, etc.).
# Try standard parsing, then progressively repair and retry.
_UNQUOTED_KEY_RE = re.compile(r'(?<=[{,])\s*([A-Za-z_]\w*)\s*:')
# Python literals ->JSON, using word boundaries to protect identifiers such as TrueValue /NoneType;
# bare replace silently corrupts keys containing literal substrings.
_PY_LITERAL_RE = re.compile(r'(?<!\w)(True|False|None)(?!\w)')
_PY_LITERAL_MAP = {'True': 'true', 'False': 'false', 'None': 'null'}

# Valid JSON value initials: `"` string /`{` object /`[` array /
# `-` or digits for numbers /`t` `f` `n` for true/false/null.
_VALUE_START_CHARS = frozenset('"{[-tfn0123456789')

# Unicode-category allowlist: strip only these hallucinated pollution base characters:
#   Lo: Other Letter, including CJK/Korean/Japanese/Arabic (observed sources such as 결).
#   So: Other Symbol, mainly emoji.
# Exclude Sm mathematical symbols (− U+2212 /＋ U+FF0B), Pd dashes,
# Nd (full-width ０-９, Arabic ٠, etc.), and other possible Unicode numeric prefixes;
# stripping them silently corrupts `[1,−2]` into `[1,2]`.
_POLLUTION_UNICODE_CATEGORIES = frozenset({'Lo', 'So'})

# Combining marks/format characters belong to preceding bases as grapheme clusters.
# Example: ❤️ =U+2764 (So) +U+FE0F (Mn variation selector).
#     `🧑‍💻` = U+1F9D1 (So) + U+200D (Cf ZWJ) + U+1F4BB (So)。
_GRAPHEME_EXTEND_CATEGORIES = frozenset({'Mn', 'Me', 'Mc', 'Cf'})


def _is_likely_pollution_char(c: str) -> bool:
    """Non-ASCII and in the Other Letter (CJK/etc.) or Other Symbol (emoji) category."""
    if ord(c) <= 127:
        return False
    return unicodedata.category(c) in _POLLUTION_UNICODE_CATEGORIES


_ZWJ = '‍'


def _consume_pollution_grapheme(s: str, i: int) -> int:
    """Try to consume one pollution grapheme cluster, returning the end position.

    If ``s[i]`` is a pollution base char (Lo/So), treat it together with subsequent
    combining marks and extenders like ZWJ as one cluster. If a ZWJ is directly followed
    by another pollution base, merge it into the same cluster (emoji compounds like
    ``🧑‍💻`` = PERSON + ZWJ + COMPUTER). Returns i unchanged when not pollution.
    """
    n = len(s)
    if i >= n or not _is_likely_pollution_char(s[i]):
        return i
    end = i + 1
    while True:
        # Consume combining marks/ZWJ/format characters.
        while end < n and unicodedata.category(s[end]) in _GRAPHEME_EXTEND_CATEGORIES:
            end += 1
        # Another pollution base after ZWJ joins the same cluster.
        if (
            end < n
            and end >= 2
            and s[end - 1] == _ZWJ
            and _is_likely_pollution_char(s[end])
        ):
            end += 1
            continue
        break
    return end


def _strip_stray_chars_between_tokens(s: str) -> str:
    """Strip 1–2 hallucinated grapheme clusters between `,`/`[` and the next value.

    Stateful scanner — only acts outside of quoted strings (with backslash escape
    handling). Strips only **non-ASCII Letters / emoji** (the hallucination pollution
    sources observed from LLMs); ASCII chars and Unicode numeric symbols / punctuation /
    dashes / fullwidth digits always pass through, avoiding silently corrupting
    half-legitimate value prefixes like `+5`, `.5`, `e3`, `−2` (U+2212), `＋5` (U+FF0B).
    If stripping doesn't help, let json.loads raise JSONDecodeError and take the fallback.

    Best-effort, least destruction: capped at 2 grapheme clusters, increasing from k=1;
    the first k whose lookahead hits a legal value start stops immediately — no greed.
    One cluster = 1 pollution base char + 0+ subsequent combining marks/ZWJ, so
    multi-codepoint emoji like `❤️` (U+2764+U+FE0F) or `🧑‍💻` (with ZWJ) also count as 1 cluster.
    """
    out: list[str] = []
    i = 0
    n = len(s)
    in_string = False
    escape = False
    while i < n:
        c = s[i]
        if in_string:
            out.append(c)
            if escape:
                escape = False
            elif c == '\\':
                escape = True
            elif c == '"':
                in_string = False
            i += 1
            continue
        if c == '"':
            in_string = True
            out.append(c)
            i += 1
            continue
        out.append(c)
        i += 1
        if c not in ',[':
            continue
        # Skip whitespace after separators, starting with minimum k=1 cluster.
        j = i
        while j < n and s[j].isspace():
            j += 1
        cur = j
        for _ in range(2):  # Cap at two grapheme clusters.
            nxt = _consume_pollution_grapheme(s, cur)
            if nxt == cur:
                break  # Not pollution; increasing k only worsens it.
            cur = nxt
            # Permit whitespace after pollution, common in pretty-printed output,
            # then require the next nonwhitespace character to begin a valid value.
            m = cur
            while m < n and s[m].isspace():
                m += 1
            if m < n and s[m] in _VALUE_START_CHARS:
                out.append(s[i:j])  # Preserve whitespace after separators.
                i = cur  # Skip pollution; the main loop appends subsequent whitespace normally.
                break
    return ''.join(out)


def _looks_like_structural_close(s: str, pos: int) -> bool:
    """Whether an in-string ``"`` at index ``pos-1`` is a real string terminator.

    Look at the first non-whitespace char from ``pos``:
      - ``:`` / ``}`` / ``]`` / end-of-input → strong structural signal → close.
      - ``,`` → ambiguous (commas appear in prose). Only a real close when the token
        *after* the comma looks like a valid next key/value head (``_VALUE_START_CHARS``,
        which already includes ``"``); otherwise the ``"`` is content.
      - anything else → content quote (not a close).
    """
    n = len(s)
    j = pos
    while j < n and s[j].isspace():
        j += 1
    if j >= n:
        return True  # EOF validly closes the final string.
    c = s[j]
    if c in ':}]':
        return True
    if c == ',':
        k = j + 1
        while k < n and s[k].isspace():
            k += 1
        # Commas are separators only before valid next key/value initials; otherwise prefer string content.
        return k < n and s[k] in _VALUE_START_CHARS
    return False


def _escape_inner_quotes(s: str) -> str:
    """Escape unescaped double-quotes that appear *inside* JSON string values.

    Some fast LLMs (notably Qwen) write Chinese prose with literal English double
    quotes around quoted speech / terms and forget to escape them, e.g.
    ``{"content": "他对我说"晚安"然后走了"}`` —— ``json.loads`` then treats the first
    inner ``"`` as the string terminator and chokes.

    Scan char by char. Inside a string, when an unescaped ``"`` is hit, decide whether
    it terminates the string (``_looks_like_structural_close``) or is stray content; if
    content, rewrite it to ``\\"``. The bias is deliberately toward *escaping*: a wrong
    "this is content" guess merely fails to parse and json.loads raises with full
    context (no regression vs. today), whereas closing too eagerly would yield
    valid-but-wrong JSON —— the silent corruption this module avoids everywhere else.
    This is the last, most aggressive transform in the fallback pipeline; it only runs
    after every cheaper repair has failed.

    Known best-effort limitations (consciously accepted —— this repair targets the
    common Qwen single-/multi-field prose case and trades a little adversarial-corner
    safety for that coverage; only ever runs on input that already fails strict parse):
      1. Comma boundary is fundamentally ambiguous. A legitimate multi-field object like
         ``{"summary": "他说"晚安"了", "reason": "..."}`` is structurally *identical* to
         an adversarial ``{"content": "User wrote "x", "y": "z""}``. Repairing the first
         (needs the comma to close) necessarily mis-splits the second. There is no local
         signal to tell them apart, so the latter is silently mis-repaired.
      2. Adjacent quoted tokens with a missing separator (``["x" "y"]``, ``{"a" "b": 1}``)
         get merged into one string rather than left to fail —— missing *commas/colons*
         are out of scope for an inner-quote repair, but this transform incidentally
         "fixes" them wrongly.
      3. Earlier text transforms (Python-literal / ``{{}}``) run *before* this one
         (they must, and this one must run after quote-normalization + unquoted-key, or
         its lookahead misreads bare keys), so a stray ``True`` / ``{{x}}`` *inside* an
         unescaped-inner-quote string can be rewritten before the string is made whole,
         e.g. ``{"a":"he said "True" today"}`` → ``...said "true"...``.
    """  # noqa: DOCSTRING_CJK
    out: list[str] = []
    i = 0
    n = len(s)
    in_string = False
    escape = False
    while i < n:
        c = s[i]
        if not in_string:
            out.append(c)
            if c == '"':
                in_string = True
            i += 1
            continue
        if escape:
            out.append(c)
            escape = False
            i += 1
            continue
        if c == '\\':
            out.append(c)
            escape = True
            i += 1
            continue
        if c != '"':
            out.append(c)
            i += 1
            continue
        # Unescaped inner double quote: closing or content?
        if _looks_like_structural_close(s, i + 1):
            out.append(c)
            in_string = False
        else:
            out.append('\\"')
        i += 1
    return ''.join(out)


def _insert_missing_structural_commas(s: str) -> str:
    """Insert a missing comma between two adjacent JSON containers.

    LLMs frequently drop the separator between array / object elements, e.g.
    ``[{"role": "user", ...} {"role": "ai", ...}]`` — ``json.loads`` then raises
    ``Expecting ',' delimiter`` at the second ``{``. This was the single most
    common unhandled failure observed in the memory-review (``correction`` model)
    path, whose output is exactly an array of ``{role, content}`` objects.

    Stateful scan (string + backslash aware so braces inside string values are
    ignored). Whenever a structural close ``}`` / ``]`` is immediately followed —
    only whitespace between — by a structural open ``{`` / ``[`` outside any
    string, insert a ``,`` between them.

    Deliberately scoped to the **close → open** boundary only: ``}{`` / ``}[`` /
    ``]{`` / ``][`` are never valid JSON, so inserting a comma there cannot
    silently corrupt a legitimate document (on valid input this is a no-op). The
    riskier siblings — a missing comma after a *string* value (``"a" "b"``, where
    the leading ``"`` might be an inner content quote rather than a real
    terminator) — are left to fail loudly, consistent with this module's bias
    against silent corruption.
    """
    out: list[str] = []
    i = 0
    n = len(s)
    in_string = False
    escape = False
    while i < n:
        c = s[i]
        out.append(c)
        if in_string:
            if escape:
                escape = False
            elif c == '\\':
                escape = True
            elif c == '"':
                in_string = False
            i += 1
            continue
        if c == '"':
            in_string = True
            i += 1
            continue
        if c in '}]':
            # Look ahead past whitespace: another container opener ({ or [) implies a missing comma.
            j = i + 1
            while j < n and s[j].isspace():
                j += 1
            if j < n and s[j] in '{[':
                out.append(',')
        i += 1
    return ''.join(out)


def _try_json_loads(s: str) -> tuple[Any, bool]:
    try:
        return json.loads(s), True
    except json.JSONDecodeError:
        return None, False


def _apply_outside_strings(s: str, transform: Callable[[str], str]) -> str:
    """Run ``transform`` only on text outside of quoted strings.

    Both ``'...'`` and ``"..."`` are recognized as string boundaries (LLMs often emit
    Python-repr style mixed quotes). Backslash inside strings escapes the next char.
    Inside-string content is preserved bytewise — protects e.g. the literal value
    ``"True"`` from the Python-literal substitution step.
    """
    out: list[str] = []
    buf: list[str] = []  # outside-string segment buffer
    quote: str | None = None
    escape = False

    def _flush_outside() -> None:
        if buf:
            out.append(transform(''.join(buf)))
            buf.clear()

    for c in s:
        if escape:
            out.append(c)
            escape = False
            continue
        if quote is not None:
            out.append(c)
            if c == '\\':
                escape = True
            elif c == quote:
                quote = None
        else:
            if c in ('"', "'"):
                _flush_outside()
                out.append(c)
                quote = c
            else:
                buf.append(c)
    _flush_outside()
    return ''.join(out)


def _normalize_quotes(s: str) -> str:
    """Convert single-quoted strings to double-quoted; preserve inside content.

    Segment-aware: one scan slices by ``'`` / ``"`` boundaries, rewriting only ``'...'``
    segments into ``"..."``, unescaping inner ``\\'`` and escaping bare ``"``. Segments
    that are already double-quoted strings are left byte-for-byte untouched.
    """
    out: list[str] = []
    current: list[str] = []
    quote: str | None = None
    escape = False
    for c in s:
        if escape:
            current.append(c)
            escape = False
            continue
        if quote is not None:
            if c == '\\':
                current.append(c)
                escape = True
            elif c == quote:
                # String ends.
                if quote == "'":
                    inner = ''.join(current)
                    # Decode \' to ', retain \\, then escape bare double quotes for the target double-quoted string.
                    inner = re.sub(r"\\'", "'", inner)
                    inner = re.sub(r'(?<!\\)"', r'\\"', inner)
                    out.append('"' + inner + '"')
                else:
                    out.append('"' + ''.join(current) + '"')
                current = []
                quote = None
            else:
                current.append(c)
        else:
            if c in ('"', "'"):
                quote = c
                current = []
            else:
                out.append(c)
    if quote is not None:
        # Unclosed: emit unchanged, letting json.loads raise.
        out.append(quote)
        out.append(''.join(current))
    return ''.join(out)


# Failure fingerprint: one or more literal newline escapes, a --- divider line, then more literal
# newline escapes. Replace only this overescaped divider region
# with canonical `\n\n---\n\n`, preserving all literal escapes elsewhere
# in the string, including legitimate ``C:\new_folder``, regexes, and code;
# their literal ``\n``/``\t`` remain untouched.
_OVERESCAPED_DIVIDER_RE = re.compile(
    r'(?:\\r\\n|\\r|\\n)+[ \t]*-{3,}[ \t]*(?:\\r\\n|\\r|\\n)+'
)


def _normalize_overescaped_newlines(obj: Any) -> Any:
    """When the LLM escapes ``\\n`` once more in the JSON source, the parsed string holds a
    literal backslash-n (2 chars) instead of a real newline. This replaces only the
    **over-escaped ``---`` divider regions** with a canonical ``\\n\\n---\\n\\n`` —
    literal escapes elsewhere in the same string (Windows paths, regex, code snippets,
    tool args, etc.) are left untouched.

    Trade-off: if the body / older segments contain further literal paragraph dividers,
    this function leaves them alone — keeping literals is safer than silently rewriting
    legitimate data; at worst the UI shows a few literal ``\\n``.
    """
    if isinstance(obj, str):
        return _OVERESCAPED_DIVIDER_RE.sub('\n\n---\n\n', obj)
    if isinstance(obj, dict):
        return {k: _normalize_overescaped_newlines(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_normalize_overescaped_newlines(v) for v in obj]
    return obj


def robust_json_loads(raw: str) -> Any:
    """json.loads with fallback for common LLM JSON quirks.

    If the raw input parses directly, the original result is returned unconditionally.
    Otherwise patch step by step along the fallback pipeline — try parsing right after
    each transform and stop as soon as it parses, so later steps (especially the
    scanner) never touch the text unnecessarily.

    All "pure text replacement" transforms (Python literals, `{{}}`, trailing commas,
    unquoted keys) are wrapped by ``_apply_outside_strings`` and only apply outside
    strings, avoiding silently corrupting string values (like ``"True"`` / ``"x,]"``).

    After a successful parse, a ``_normalize_overescaped_newlines`` post-pass also runs:
    when a string value carries the "over-escaped ``---`` divider fingerprint" — i.e. 1+
    literal newline-ish escapes (``\\n`` / ``\\r\\n`` / ``\\r``) hugging a ``---`` line —
    that region is replaced with a canonical ``\\n\\n---\\n\\n``. Literal escapes
    elsewhere in the same string (Windows paths, regex, code snippets, etc.) are left
    untouched.

    Handles: unquoted keys, trailing commas, ``{{ }}``, Python ``True/False/None``,
    single-quoted strings (including mixed-quote scenarios), stray hallucinated
    chars between structural tokens (e.g. ``,결{`` → ``,{``), missing commas
    between adjacent containers (e.g. ``}{`` → ``},{``), unescaped English
    double-quotes inside string values (e.g. ``"他说"晚安"走了"``), and over-escaped
    ``---`` memo dividers in string values.
    """  # noqa: DOCSTRING_CJK
    parsed, ok = _try_json_loads(raw)
    if ok:
        return _normalize_overescaped_newlines(parsed)

    transforms = (
        # {{ }} ->{ } from prompt-template imitation; segment-aware.
        lambda s: _apply_outside_strings(
            s, lambda t: t.replace("{{", "{").replace("}}", "}"),
        ),
        # Python literals ->JSON, segment-aware to preserve quoted True, plus
        # word boundaries protecting identifiers such as TrueValue/NoneType.
        lambda s: _apply_outside_strings(
            s,
            lambda t: _PY_LITERAL_RE.sub(lambda m: _PY_LITERAL_MAP[m.group(1)], t),
        ),
        # Trailing commas, segment-aware.
        lambda s: _apply_outside_strings(s, lambda t: re.sub(r',\s*([}\]])', r'\1', t)),
        # Unquoted keys: {key: "v"} ->{"key": "v"}, segment-aware.
        lambda s: _apply_outside_strings(s, lambda t: _UNQUOTED_KEY_RE.sub(r' "\1":', t)),
        # Single ->double quotes, inherently segment-aware.
        _normalize_quotes,
        # Remove hallucinated pollution between structural tokens such as `,결{`, respecting double quotes.
        _strip_stray_chars_between_tokens,
        # Escape unescaped English double quotes inside values, common with Qwen.
        _escape_inner_quotes,
        # Missing container-element commas `}{`->`},{`, string-aware; only unambiguous structural close->open.
        # Run **after** _escape_inner_quotes: unescaped inner quotes flip string parity,
        # misclassifying literal `}{` as structure and silently inserting commas. Escape inner quotes
        # first for stable string boundaries and trustworthy close->open decisions (Codex P2).
        _insert_missing_structural_commas,
    )
    s = raw
    for transform in transforms:
        s = transform(s)
        parsed, ok = _try_json_loads(s)
        if ok:
            return _normalize_overescaped_newlines(parsed)
    return _normalize_overescaped_newlines(json.loads(s))  # Raise final errors with full context.


# Sweep crash-leftover tmp files. Hard kills between mkstemp and os.replace (taskkill/power/OOM)
# leave unread but accumulating files in user config/memory directories.
#
# Throttle **per directory**, never once per lifetime. Sweepers periodically recheck;
# a single scan misses files leaked by this write itself (scan precedes mkstemp),
# files too young at scanning, and directories with transient scan failures until process exit.
# Last-scan timestamps plus minimum intervals cover all three next window, bounding leftover
# lifetime to age threshold +interval without rollback/CAS/retry-count patches.
# Track directories, not targets: otherwise every new target rescans its directory (archive_shards
# contains hundreds of independent targets, as do cloudsave bindings/<character>.json).
# Targets written once and never touched again would otherwise retain leftovers forever.
#
# Directory sweeps cannot identify ownership by target names, while shape+mtime cannot prove it:
# matching old files placed by other programs/plugins/users would be permanently deleted.
# Embed an ownership marker in our tmp names and clean only marked files: provable ownership, not probability.
# install_source's plugins.lock.json.<pid>.<uuid>.tmp uses the same approach. Cost: older
# unmarked leftovers remain forever; prefer incomplete cleanup to deleting unproven ownership.
#
# Age thresholds prevent accidental deletion but cannot prove no active owner (writers may freeze after mkstemp).
# Two fallbacks: Windows writers retain open handles, rejecting unlink with
# PermissionError winerror=32; POSIX unlink succeeds, but later
# os.replace raises FileNotFoundError, an honest failure rather than silent corruption.
_TMP_OWNER_TAG = "nkatmp"
# Random segments use [^.]+, not [a-z0-9_]+; ownership markers make length/charset irrelevant.
# Hardcoded charsets reintroduce dependencies on tempfile._RandomNameSequence details; future CPython
# uppercase letters would silently make our tmp files unrecognizable.
_STALE_TMP_RE = re.compile(rf"^\.{_TMP_OWNER_TAG}[^.]+\.tmp$")
_STALE_TMP_MIN_AGE_S = 86400.0
_STALE_TMP_SWEEP_INTERVAL_S = 3600.0
# Bookkeeping cap: cloudsave export creates fresh staging/per-character directories each time;
# permanent tracking grows unbounded. Evicting costs at most another scan, so discard
# the oldest half at capacity.
_STALE_TMP_MEMO_MAX = 512
_swept_tmp_dirs: dict[str, float] = {}
_swept_tmp_dirs_lock = threading.Lock()
# **Exclude** target basenames from tmp names. Diagnostic intent was redundant:
# os.replace OSError already includes filename/filename2 as '<tmp>' ->'<target>'.
# Benefits: constant ~19-byte names, strictly shorter than old .<basename>.<8>.tmp,
# eliminate ENAMETOOLONG without probing per-filesystem NAME_MAX (fixed byte caps
# still fail on 143-byte eCryptfs limits), and simpler regexes.


def _sweep_stale_tmp_if_due(target_path: Path) -> None:
    """Best-effort removal of abandoned temp files in this target's directory. Never fatal."""
    parent = str(target_path.parent)
    now = time.monotonic()
    with _swept_tmp_dirs_lock:
        last = _swept_tmp_dirs.get(parent)
        if last is not None and now - last < _STALE_TMP_SWEEP_INTERVAL_S:
            return
        # Record before scanning: among concurrent first writes only one scans. Failed scans still
        # record time, retrying naturally next interval without separate retry counters.
        _swept_tmp_dirs[parent] = now
        if len(_swept_tmp_dirs) > _STALE_TMP_MEMO_MAX:
            oldest = sorted(_swept_tmp_dirs, key=_swept_tmp_dirs.__getitem__)
            for stale in oldest[: _STALE_TMP_MEMO_MAX // 2]:
                _swept_tmp_dirs.pop(stale, None)

    try:
        entries = list(os.scandir(parent))
    except OSError:
        return

    cutoff = time.time() - _STALE_TMP_MIN_AGE_S
    for entry in entries:
        if not _STALE_TMP_RE.match(entry.name):
            continue
        with suppress(OSError):
            if entry.stat().st_mtime < cutoff:
                os.unlink(entry.path)


def _reset_tmp_sweep_state_after_fork() -> None:
    """Re-create the sweep lock in a forked child and drop inherited bookkeeping."""
    # app/main_server/__init__.py:56 forks, copying only the calling thread. A lock held
    # by another thread becomes permanently locked in children, deadlocking every disk write.
    # Clear bookkeeping too: new child processes should rescan.
    global _swept_tmp_dirs_lock
    _swept_tmp_dirs_lock = threading.Lock()
    _swept_tmp_dirs.clear()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_tmp_sweep_state_after_fork)


# Windows os.replace fails if another handle opens the target, PermissionError
# winerror 5 ACCESS_DENIED or 32 SHARING_VIOLATION. Antivirus/Explorer previews are
# not required: our own to_thread writes race other threads/tests opening the same
# file for reading. POSIX rename ignores readers; this Windows-only window is
# almost always milliseconds, disappearing when the handle closes.
#
# Retry with backoff, without masking genuine errors, for four reasons:
#   * Criteria use OS error codes, never guessed text. POSIX OSError lacks winerror,
#     so getattr returns None and this always raises immediately outside Windows.
#   * Never retry other codes: full disks, long paths, cross-volume operations, directory targets raise immediately.
#   * Final attempt is outside the loop, propagating the actual os.replace exception
#     with filename/filename2, never a wrapped retry-failed error.
#   * Fixed bound: five backoffs totaling 155ms. Permanent occupation (read-only/long-held handles)
#     still raises exactly as before, only 155ms later, trading that delay for avoiding
#     most occasional millisecond-window failures.
#
# Never back off on event loops. These repository-wide write primitives have over twenty
# synchronous atomic_write_* calls in async defs, including per-turn memory/anti_repeat.py
# and memory/user_directives.py sharing audio loops. Sleeping 155ms cuts audio.
# More than timing: scripts/check_async_blocking.py explicitly lists
# time.sleep in RISKY_ATTR_PAIRS, forbidden on async-reachable paths. Its current silence
# reflects documented depth-one limits (sleep occurs at depth two through
# atomic_write_text ->_replace_with_busy_retry), not compliance.
#
# With a running event loop, raise on the first busy error: callers get **exactly pre-change
# behavior**, without regression. Writes causing these flakes run entirely in to_thread
# workers without running loops and retain full backoff benefits.
#
# Cost: those twenty-plus loop-thread call sites still lack protection and lose writes
# on busy targets, typically silently behind except Exception: logger.warning.
# Primitives must not compensate by secretly sleeping on loops; migrate callers to
# existing atomic_write_json_async, already used at 77 sites, as separate work.
_REPLACE_BUSY_WINERRORS = frozenset({5, 32})
_REPLACE_RETRY_BACKOFF_S = (0.005, 0.01, 0.02, 0.04, 0.08)


def running_on_event_loop() -> bool:
    """Whether this thread is currently inside a running asyncio event loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def _with_busy_retry(attempt) -> None:
    """Run ``attempt`` under the shared Windows "target is busy" backoff.

    One policy, two entry points. The replacing and no-replace publishes
    had the same loop written out twice -- same error set, same event-loop
    rule, same delays -- so changing the policy meant changing both and
    noticing that it had to be changed in both.

    Anything that is not a BUSY error propagates on the first attempt,
    which is already the whole rule for the no-replace publish: a
    destination that won the race raises FileExistsError, whose winerror
    is 183 on Windows and absent on POSIX, so it is never in the busy set.
    A "terminal exceptions" parameter was written here and taken back
    out after checking what it did: nothing, on either platform.
    """
    for delay in _REPLACE_RETRY_BACKOFF_S:
        try:
            attempt()
            return
        except OSError as exc:
            if getattr(exc, "winerror", None) not in _REPLACE_BUSY_WINERRORS:
                raise
            # Check running-loop status only after an actual busy error; happy paths add
            # no instructions.
            if running_on_event_loop():
                raise
        time.sleep(delay)
    # One last go, so the caller sees the real error rather than a
    # silent give-up.
    attempt()


def _replace_with_busy_retry(temp_path: str, target_path: Path) -> None:
    """Replace the target, briefly retrying Windows' "target is busy" errors."""
    _with_busy_retry(lambda: os.replace(temp_path, target_path))


def _publish_once(source: str, target_path: Path) -> None:
    """One no-replace attempt, by whichever primitive the platform has."""
    if os.name == "nt":
        # os.rename ALREADY refuses an existing destination on Windows,
        # which is exactly the semantics wanted here.
        os.rename(source, target_path)
        return
    # POSIX rename replaces, so claim the name with a hard link -- which
    # raises FileExistsError instead -- and drop the staged name after.
    os.link(source, target_path)
    try:
        os.unlink(source)
    except OSError:
        # The destination is published either way; the staged name is
        # inside a workspace that comes down with it.
        pass


def publish_without_replacing(
    source: str | os.PathLike[str], target_path: Path
) -> None:
    """Move a file onto a name that must NOT already exist.

    :func:`replace_with_busy_retry` overwrites, and a destination can
    appear between a caller's last check and the call -- another
    application process, or a cloud import writing back the managed file
    it is restoring. Where an existing entry is authoritative, losing
    that race has to mean abandoning the source: FileExistsError is
    raised and nothing is overwritten.

    A hard link would serve on Windows too, and must not be used there:
    the source then shares an inode with the published file, so clearing
    the read-only attribute to remove the source changes the mode of the
    published one. Measured -- a 0o444 seed published as 0o666.

    Same Windows busy window and same backoff as the replacing form --
    literally the same, through :func:`_with_busy_retry`, rather than a
    second copy of the error set and the delays.
    """
    source = str(source)
    # FileExistsError is the destination winning: an answer, not a
    # failure to retry around -- and it needs no special case, because
    # it is not a busy error and the backoff only retries those.
    _with_busy_retry(lambda: _publish_once(source, target_path))


def replace_with_busy_retry(temp_path: str | os.PathLike[str], target_path: Path) -> None:
    """Replace ``target_path``, briefly retrying Windows' "target is busy".

    The public name for :func:`_replace_with_busy_retry`. The migration
    path publishes through it, and a private name would have made the
    retry semantics a convention that a rename in here could break
    silently rather than a contract.
    """
    _replace_with_busy_retry(str(temp_path), target_path)


def unlink_tolerating_replace(path: str | os.PathLike[str], *, missing_ok: bool = False) -> None:
    """Delete a file using the bounded Windows busy retry policy."""
    _with_busy_retry(lambda: Path(path).unlink(missing_ok=missing_ok))


# Temporary-creation attempt cap. Names use 32 random bits, token_hex(4)/eight hex characters,
# pinned by length contracts below. Collision probability in 2^32 names is negligible,
# so a small cap suffices, specifically eliminating infinite retry possibilities.
_TMP_CREATE_MAX_TRIES = 32

# Match tempfile.mkstemp: add O_BINARY on Windows to prevent CRT newline
# conversion; Python TextIOWrapper handles encoding, while underlying handles stay binary.
_TMP_OPEN_FLAGS = os.O_CREAT | os.O_EXCL | os.O_RDWR | getattr(os, "O_BINARY", 0)


def _create_exclusive_temp_file(target_dir: Path) -> tuple[int, str]:
    """Create a temp file exclusively in the target directory; return (fd, absolute path).

    Deliberately avoids ``tempfile.mkstemp``. On Windows it has a branch that,
    when file creation raises PermissionError, retries under a fresh random
    name as long as the directory "looks writable" (os.access reads static
    permission bits). That loop's bound on Windows is TMP_MAX == 2147483647,
    i.e. no bound at all.

    Sandboxes and security software reliably produce exactly the combination
    "static permissions say writable, creation is actually denied", so that
    branch retries forever: no exception, no return, the caller hangs
    permanently. This is why this project froze on disk writes in restricted
    environments.

    FileExistsError and Windows PermissionError on a confirmed existing
    candidate name are retried within the fixed bound. Other creation errors
    are raised immediately, so access denial cannot trigger an endless wait.
    """
    target_dir = Path(target_dir).absolute()
    for _ in range(_TMP_CREATE_MAX_TRIES):
        # Random segments contain no dots, matching _STALE_TMP_RE for owned leftover cleanup.
        # Fixed eight characters, token_hex(4), keep complete names 19 bytes, matching
        # tempfile.mkstemp's eight-character randomness and guaranteeing constant names strictly shorter
        # than old .<basename>.<8>.tmp. test_temp_name_is_a_short_constant_shape
        # pins it, preventing previously writable long targets failing ENAMETOOLONG.
        temp_path = target_dir / f".{_TMP_OWNER_TAG}{secrets.token_hex(4)}.tmp"
        try:
            # O_CREAT|O_EXCL requires prior absence, preventing collisions and symlink
            # takeover. 0o600 matches mkstemp default permissions.
            fd = os.open(temp_path, _TMP_OPEN_FLAGS, 0o600)
        except FileExistsError:
            # Rare random collisions retry another name, the only retryable case.
            continue
        except OSError as exc:
            # Windows directory collisions may return EACCES; allow bounded retry only after proving candidate existence.
            if isinstance(exc, PermissionError) and os.name == "nt" and os.path.lexists(temp_path):
                continue
            # Deleted directories/full disks/long paths and other failures raise immediately.
            # Core fix: no retry here, eliminating infinite loops.
            logger.debug(
                "[file_utils] 临时文件创建失败，本次写入放弃: dir=%s errno=%s error=%s",
                target_dir,
                getattr(exc, "errno", None),
                exc,
            )
            raise
        return fd, str(temp_path)

    # Thirty-two consecutive random collisions are practically impossible; retain this branch so the function on every
    # path explicitly returns or raises, never silently returning None and failing caller unpacking.
    logger.error(
        "[file_utils] 连续 %s 次都无法在目标目录创建唯一临时文件: dir=%s",
        _TMP_CREATE_MAX_TRIES,
        target_dir,
    )
    raise FileExistsError(
        errno.EEXIST,
        f"连续 {_TMP_CREATE_MAX_TRIES} 次都无法创建唯一临时文件",
        str(target_dir),
    )


def move_aside(path: str | os.PathLike[str], suffix: str) -> Path | None:
    """Rename ``path`` to ``<name>.<suffix>`` (``<name>.<n>.<suffix>`` when taken).

    For a damaged state file that must not be overwritten in place: its bytes
    stay on disk for inspection and the original name is free again. Returns
    the new path, or None when the file could not be moved.
    """
    path = Path(path)
    target = path.with_name(f"{path.name}.{suffix}")
    n = 1
    while target.exists():
        target = path.with_name(f"{path.name}.{n}.{suffix}")
        n += 1
    try:
        os.replace(path, target)
    except OSError as exc:
        logger.warning("cannot move %s aside: %s", path.name, exc)
        return None
    return target


def atomic_write_text(path: str | os.PathLike[str], content: str, *, encoding: str = "utf-8") -> None:
    """Atomically replace a text file in the same directory."""
    target_path = Path(path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    _sweep_stale_tmp_if_due(target_path)

    # Custom exclusive creation without tempfile.mkstemp raises immediately rather than retrying forever.
    fd, temp_path = _create_exclusive_temp_file(target_path.parent)

    try:
        with os.fdopen(fd, "w", encoding=encoding) as temp_file:
            temp_file.write(content)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        _replace_with_busy_retry(temp_path, target_path)
    except BaseException:
        # Catch BaseException: Ctrl-C/SystemExit during write/fsync is common; Exception alone
        # leaves tmp files until the next sweep window plus 24h.
        # Mirror install_source/manager.py _atomic_write's BaseException handling.
        # Cleanup failures must not replace original errors. Swallowing only FileNotFoundError
        # fails when antivirus/Explorer handles make os.replace raise PermissionError,
        # then os.remove fails for the same reason, exposing remove's exception while the real
        # cause retreats to __context__ and tmp still remains.
        # Undeletable files become leftovers; this write's preceding sweep cannot catch them,
        # but the next directory window does, one reason for periodic rather than once-only scanning.
        with suppress(OSError):
            os.remove(temp_path)
        raise


def atomic_write_bytes(path: str | os.PathLike[str], content: bytes) -> None:
    """Atomically replace a binary file in the same directory."""
    target_path = Path(path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    _sweep_stale_tmp_if_due(target_path)

    # Custom exclusive creation without tempfile.mkstemp raises immediately rather than retrying forever.
    fd, temp_path = _create_exclusive_temp_file(target_path.parent)

    try:
        with os.fdopen(fd, "wb") as temp_file:
            temp_file.write(content)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        _replace_with_busy_retry(temp_path, target_path)
    except BaseException:
        with suppress(OSError):
            os.remove(temp_path)
        raise


def atomic_write_json(
    path: str | os.PathLike[str],
    data: Any,
    *,
    encoding: str = "utf-8",
    ensure_ascii: bool = False,
    indent: int | None = 2,
    **json_kwargs: Any,
) -> None:
    """Serialize JSON and atomically replace the destination file."""
    content = json.dumps(
        data,
        ensure_ascii=ensure_ascii,
        indent=indent,
        **json_kwargs,
    )
    atomic_write_text(path, content, encoding=encoding)


def read_json_tolerating_replace(
    path: str | os.PathLike[str],
    *,
    encoding: str = "utf-8",
) -> Any:
    """``read_json`` that rides out a concurrent ``os.replace`` on Windows.

    The write side already backs off when the target is busy; this is the same
    window seen from the reader. Without it a caller that swallows exceptions
    turns a transient sharing violation into "the file is unreadable" and falls
    back to defaults, which is far worse than waiting a few milliseconds.

    Only the Windows "target is busy" codes are retried, the budget is the same
    bounded one as the write side, and the final attempt re-raises the real
    error rather than a wrapped one.
    """
    for delay in _REPLACE_RETRY_BACKOFF_S:
        try:
            return read_json(path, encoding=encoding)
        except OSError as exc:
            if getattr(exc, "winerror", None) not in _REPLACE_BUSY_WINERRORS:
                raise
            # Same rule as writes: never sleep on event loops. Synchronous reads such as
            # get_workshop_path() also run in async handlers; loop callers get first-attempt
            # failure and decide fallback (see ConfigManager.load_workshop_config's
            # last-known-good behavior).
            if running_on_event_loop():
                raise
        time.sleep(delay)
    return read_json(path, encoding=encoding)


def read_bytes_tolerating_replace(path: str | os.PathLike[str]) -> bytes:
    """Read one byte snapshot while tolerating a concurrent Windows replace."""

    target_path = Path(path)
    for delay in _REPLACE_RETRY_BACKOFF_S:
        try:
            return target_path.read_bytes()
        except OSError as exc:
            if getattr(exc, "winerror", None) not in _REPLACE_BUSY_WINERRORS:
                raise
            if running_on_event_loop():
                raise
        time.sleep(delay)
    return target_path.read_bytes()


def read_json(path: str | os.PathLike[str], *, encoding: str = "utf-8") -> Any:
    with open(path, "r", encoding=encoding) as f:
        return json.load(f)


async def atomic_write_text_async(
    path: str | os.PathLike[str],
    content: str,
    *,
    encoding: str = "utf-8",
) -> None:
    await asyncio.to_thread(atomic_write_text, path, content, encoding=encoding)


async def atomic_write_json_async(
    path: str | os.PathLike[str],
    data: Any,
    *,
    encoding: str = "utf-8",
    ensure_ascii: bool = False,
    indent: int | None = 2,
    **json_kwargs: Any,
) -> None:
    await asyncio.to_thread(
        atomic_write_json,
        path,
        data,
        encoding=encoding,
        ensure_ascii=ensure_ascii,
        indent=indent,
        **json_kwargs,
    )


async def read_json_async(
    path: str | os.PathLike[str],
    *,
    encoding: str = "utf-8",
) -> Any:
    return await asyncio.to_thread(read_json, path, encoding=encoding)
