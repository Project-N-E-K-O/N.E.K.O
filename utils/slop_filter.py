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

r"""Prompt-side slop reduction — stripping the "AI tell" from dialog history.

LLMs imitate the style of the conversation they are shown. When the cat's own
past replies are full of stock phrases — "his heart pounded wildly", "a smirk
tugged at the corner of her mouth", "a frantic rhythm drummed against his ribs"
— the model reads them as the established voice and keeps producing more of the
same. This module rewrites those clichés in the history that is *fed back to the
model*, breaking the self-imitation feedback loop.

promptOnly semantics
--------------------
The on-disk conversation history is **never** mutated. ``apply_slop_reduction``
returns a defensive copy; only that copy is sent on the wire. The user keeps
seeing the model's raw output verbatim (good for spotting which patterns recur
and tuning rules); the model sees a diversified version. Fully reversible — turn
the switch off and everything reverts.

Scope
-----
Applied at ``OmniOfflineClient._astream_with_tools`` (the text dialog path).
Only **assistant**-role turns are rewritten — that is where slop accumulates.
System instructions and user messages pass through untouched, so prompt
contracts and the user's own words are never altered. The realtime (voice) path
does not flow through here and is out of scope by construction.

Repeat and stability semantics
------------------------------
Matches are counted per rule over the original assistant history in chronological
message / text-part / match order. The first two eligible occurrences remain
verbatim; the third and later occurrences are rewritten. Replacement selection is
derived from stable rule and match coordinates rather than global randomness, so
repeating a request or appending later history cannot reshuffle older rewrites.
Fenced code, inline code, and URLs are excluded from both counting and rewriting.

Rule format (see ``config/prompts/prompts_slop.py``)
----------------------------------------------------
Each language maps to a list of rule dicts::

    {
        "id": "ZH_003",
        "name": "heart pounding",
        "find": r"...",            # a Python ``re`` pattern (NOT JS)
        "replace": ["...", ...],   # pool; one is picked deterministically per match
        "flags": 0,                # optional ``re`` flags (default 0)
    }

A pool entry may use Python backreferences (``\\1``, ``\\g<name>``) to carry
capture groups from ``find`` through to the replacement, e.g. preserving the
pronoun. Substitution uses :meth:`re.Match.expand`, so the syntax is exactly
what ``re.sub`` accepts as a template string.
"""

from __future__ import annotations

import hashlib
import os
import re
from typing import Any, Callable, Iterable, Optional

from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Main")

# Detailed before/after logging prints the cat's actual reply text, which counts
# as raw conversation content — per project policy that must go through ``print``
# (never the logger). Gated off by default; set NEKO_SLOP_DEBUG=1 to inspect.
_DEBUG_ENV = "NEKO_SLOP_DEBUG"


# ────────────────────────────────────────────────────────────────
# Compiled-pattern cache (LRU, mirrors the SillyTavern RegexProvider)
# ────────────────────────────────────────────────────────────────
class _CompiledRuleCache:
    """Compile-once cache keyed by ``(pattern, flags)``.

    A pattern that fails to compile is cached as ``None`` so a malformed rule is
    only reported once and never re-attempted. Bounded so the Phase-2 learned
    rules (which grow over a session) cannot leak memory unboundedly.
    """

    def __init__(self, max_size: int = 2000) -> None:
        self._cache: "dict[tuple[str, int], Optional[re.Pattern[str]]]" = {}
        self._max_size = max_size

    def get(self, pattern: str, flags: int) -> Optional[re.Pattern[str]]:
        key = (pattern, flags)
        if key in self._cache:
            value = self._cache.pop(key)
            self._cache[key] = value  # LRU bump
            return value
        try:
            compiled: Optional[re.Pattern[str]] = re.compile(pattern, flags)
        except (re.error, TypeError, ValueError) as exc:
            # Bad pattern from a curated or learned rule — log the pattern (it is
            # author-supplied, not conversation content) once and poison the key.
            logger.warning(
                "slop rule failed to compile, skipping: %r (%s)", pattern, exc
            )
            compiled = None
        if len(self._cache) >= self._max_size:
            # Evict the oldest entry.
            self._cache.pop(next(iter(self._cache)))
        self._cache[key] = compiled
        return compiled


_RULE_CACHE = _CompiledRuleCache()


# ────────────────────────────────────────────────────────────────
# Rule lookup
# ────────────────────────────────────────────────────────────────
def get_rules_for_language(lang: str) -> list[dict]:
    """Return the static slop rules for a short language code (``zh``/``en``/...).

    Unknown or empty languages return ``[]`` — we never fall back to another
    language's rules, because English clichés do not match Korean prose and
    applying them would corrupt unrelated text. Imported lazily so this engine
    has no import-time dependency on the (large) rule tables.
    """
    if not lang:
        return []
    try:
        from config.prompts.prompts_slop import SLOP_RULES
    except Exception as exc:  # pragma: no cover - config import guard
        logger.warning("slop rules unavailable: %s", exc)
        return []
    return SLOP_RULES.get(lang, [])


def _get_ruleset_config() -> tuple[int, int]:
    """Return the deterministic rule version and built-in repeat threshold."""
    try:
        from config.prompts.prompts_slop import (
            SLOP_REPEAT_THRESHOLD,
            SLOP_RULESET_VERSION,
        )

        return int(SLOP_RULESET_VERSION), max(1, int(SLOP_REPEAT_THRESHOLD))
    except Exception:
        # Keep prompt construction available even if a partially-upgraded rule
        # module is imported during development.
        return 1, 3


# ────────────────────────────────────────────────────────────────
# Core transform
# ────────────────────────────────────────────────────────────────
def _is_assistant_message(m: Any) -> bool:
    """True for the cat's own past turns, across both wire shapes the dialog
    path uses: ``BaseMessage`` objects (``type == 'ai'``) and OpenAI-style dicts
    (``role == 'assistant'``)."""
    msg_type = getattr(m, "type", None)
    if msg_type == "ai":
        return True
    if isinstance(m, dict):
        return m.get("role") == "assistant" or m.get("type") == "ai"
    return False


def _is_tool_turn(m: Any) -> bool:
    """True for an in-flight assistant turn that carries a tool call — the
    just-streamed prefix the tool loop appends before re-invoking. Rewriting it
    would make the model continue from wording that differs from what the user
    already saw/heard. Covers both wire shapes: OpenAI ``tool_calls`` key and
    Anthropic ``tool_use`` content block."""
    if isinstance(m, dict):
        if m.get("tool_calls"):
            return True
        content = m.get("content")
    else:
        if getattr(m, "tool_calls", None):
            return True
        content = getattr(m, "content", None)
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") == "tool_use":
                return True
    return False


_URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>()]+", re.IGNORECASE)


def _fenced_code_spans(text: str) -> list[tuple[int, int]]:
    """Return Markdown fenced-code spans, including an unclosed final fence."""
    spans: list[tuple[int, int]] = []
    fence_start: Optional[int] = None
    fence_char = ""
    fence_len = 0
    offset = 0
    for line in text.splitlines(keepends=True):
        stripped = line.lstrip(" \t")
        indent = len(line) - len(stripped)
        if indent <= 3:
            if fence_start is None:
                opening = re.match(r"(`{3,}|~{3,})", stripped)
                if opening:
                    marker = opening.group(1)
                    fence_start = offset
                    fence_char = marker[0]
                    fence_len = len(marker)
            else:
                closing = re.match(
                    rf"{re.escape(fence_char)}{{{fence_len},}}[ \t]*(?:\r?\n)?\Z",
                    stripped,
                )
                if closing:
                    spans.append((fence_start, offset + len(line)))
                    fence_start = None
                    fence_char = ""
                    fence_len = 0
        offset += len(line)
    if fence_start is not None:
        spans.append((fence_start, len(text)))
    return spans


def _inline_code_spans(
    text: str,
    fenced_spans: list[tuple[int, int]],
) -> list[tuple[int, int]]:
    """Return same-line backtick code spans outside fenced blocks.

    An unclosed backtick run protects the rest of its line. That conservative
    fallback is preferable to rewriting a technical fragment that merely has
    malformed Markdown.
    """
    spans: list[tuple[int, int]] = []
    index = 0
    fence_index = 0
    while index < len(text):
        while fence_index < len(fenced_spans) and fenced_spans[fence_index][1] <= index:
            fence_index += 1
        if (
            fence_index < len(fenced_spans)
            and fenced_spans[fence_index][0] <= index < fenced_spans[fence_index][1]
        ):
            index = fenced_spans[fence_index][1]
            continue
        if text[index] != "`":
            index += 1
            continue

        run_end = index + 1
        while run_end < len(text) and text[run_end] == "`":
            run_end += 1
        delimiter = text[index:run_end]
        newline = text.find("\n", run_end)
        line_end = len(text) if newline < 0 else newline
        closing = text.find(delimiter, run_end, line_end)
        end = line_end if closing < 0 else closing + len(delimiter)
        spans.append((index, end))
        index = max(end, run_end)
    return spans


def _protected_spans(text: str) -> list[tuple[int, int]]:
    """Return merged spans for fenced code, inline code, and URLs."""
    fenced = _fenced_code_spans(text)
    spans = fenced + _inline_code_spans(text, fenced)
    spans.extend(match.span() for match in _URL_RE.finditer(text))
    if not spans:
        return []
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
        elif end > merged[-1][1]:
            merged[-1] = (merged[-1][0], end)
    return merged


def _overlaps_spans(
    start: int,
    end: int,
    spans: Iterable[tuple[int, int]],
) -> bool:
    return any(
        start < protected_end and protected_start < end
        for protected_start, protected_end in spans
    )


def _prepare_rules(
    rules: Iterable[dict],
) -> list[tuple[int, str, tuple[str, ...], re.Pattern[str]]]:
    """Compile and validate rules without allowing one bad entry to escape."""
    prepared: list[tuple[int, str, tuple[str, ...], re.Pattern[str]]] = []
    for order, rule in enumerate(rules):
        try:
            pattern = rule.get("find")
            raw_pool = rule.get("replace")
            if not isinstance(pattern, str) or not pattern:
                continue
            if not isinstance(raw_pool, (list, tuple)):
                continue
            pool = tuple(item for item in raw_pool if isinstance(item, str))
            if not pool:
                continue
            flags = int(rule.get("flags", 0) or 0)
            compiled = _RULE_CACHE.get(pattern, flags)
            if compiled is None:
                continue
            rule_id = str(rule.get("id") or pattern)
            prepared.append((order, rule_id, pool, compiled))
        except Exception as exc:
            logger.debug(
                "slop rule %s could not be prepared: %s",
                rule.get("id") if isinstance(rule, dict) else None,
                exc,
            )
    return prepared


def _stable_pool_choice(
    pool: tuple[str, ...],
    *,
    ruleset_version: int,
    lang: str,
    rule_id: str,
    rule_order: int,
    message_index: int,
    part_index: int,
    occurrence: int,
    match: "re.Match[str]",
    original_text_fingerprint: str,
) -> str:
    """Choose a replacement without process-global or call-order state.

    The locator is prefix-stable: appending later history leaves every existing
    message index, text-part index, match span, and occurrence ordinal intact.
    """
    digest = hashlib.blake2b(digest_size=8, person=b"NEKO-slop-v1")
    components: tuple[Any, ...] = (
        ruleset_version,
        lang,
        rule_id,
        rule_order,
        message_index,
        part_index,
        occurrence,
        match.start(),
        match.end(),
        match.group(0),
        original_text_fingerprint,
    )
    for component in components:
        encoded = str(component).encode("utf-8", "surrogatepass")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    index = int.from_bytes(digest.digest(), "big") % len(pool)
    return pool[index]


def _rewrite_text(
    text: str,
    prepared_rules: Iterable[tuple[int, str, tuple[str, ...], re.Pattern[str]]],
    *,
    lang: str,
    ruleset_version: int,
    repeat_threshold: int,
    message_index: int,
    part_index: int,
    occurrence_counts: dict[int, int],
    counters: dict[str, int],
) -> str:
    """Count on original text, then rewrite stable non-overlapping candidates.

    Counts are independent per rule and therefore unaffected by replacements
    from earlier rules. If rewrite spans overlap, the earlier rule in the table
    wins; accepted replacements are finally applied right-to-left to preserve
    every original match coordinate.
    """
    protected = _protected_spans(text)
    text_fingerprint = hashlib.blake2b(
        text.encode("utf-8", "surrogatepass"),
        digest_size=16,
        person=b"NEKO-slop-text",
    ).hexdigest()
    candidates: list[tuple[int, int, int, str, str]] = []
    for order, rule_id, pool, compiled in prepared_rules:
        try:
            for match in compiled.finditer(text):
                start, end = match.span()
                if start == end or _overlaps_spans(start, end, protected):
                    continue
                occurrence = occurrence_counts.get(order, 0) + 1
                occurrence_counts[order] = occurrence
                if occurrence < repeat_threshold:
                    continue
                template = _stable_pool_choice(
                    pool,
                    ruleset_version=ruleset_version,
                    lang=lang,
                    rule_id=rule_id,
                    rule_order=order,
                    message_index=message_index,
                    part_index=part_index,
                    occurrence=occurrence,
                    match=match,
                    original_text_fingerprint=text_fingerprint,
                )
                try:
                    replacement = match.expand(template)
                except Exception:
                    # Preserve the previous fail-soft contract for a bad
                    # backreference or escape in one pool entry.
                    replacement = template
                candidates.append((order, start, end, replacement, rule_id))
        except Exception as exc:
            logger.debug("slop rule %s errored at apply time: %s", rule_id, exc)

    accepted: list[tuple[int, int, int, str, str]] = []
    for candidate in sorted(candidates, key=lambda item: (item[0], item[1], item[2])):
        _, start, end, _, _ = candidate
        if any(
            start < kept_end and kept_start < end
            for _, kept_start, kept_end, _, _ in accepted
        ):
            continue
        accepted.append(candidate)
    if not accepted:
        return text

    out = text
    for _, start, end, replacement, rule_id in sorted(
        accepted,
        key=lambda item: item[1],
        reverse=True,
    ):
        out = out[:start] + replacement + out[end:]
        counters[rule_id] = counters.get(rule_id, 0) + 1
    return out


def _rewrite_content(
    content: Any,
    prepared_rules,
    *,
    lang: str,
    ruleset_version: int,
    repeat_threshold: int,
    message_index: int,
    occurrence_counts: dict[int, int],
    counters: dict[str, int],
) -> Any:
    """Rewrite plain or multimodal text while retaining non-text part identity."""
    if isinstance(content, str):
        return _rewrite_text(
            content,
            prepared_rules,
            lang=lang,
            ruleset_version=ruleset_version,
            repeat_threshold=repeat_threshold,
            message_index=message_index,
            part_index=0,
            occurrence_counts=occurrence_counts,
            counters=counters,
        )
    if isinstance(content, list):
        new_parts = []
        changed = False
        text_part_index = 0
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                new_text = _rewrite_text(
                    part["text"],
                    prepared_rules,
                    lang=lang,
                    ruleset_version=ruleset_version,
                    repeat_threshold=repeat_threshold,
                    message_index=message_index,
                    part_index=text_part_index,
                    occurrence_counts=occurrence_counts,
                    counters=counters,
                )
                text_part_index += 1
                if new_text != part["text"]:
                    new_parts.append({**part, "text": new_text})
                    changed = True
                else:
                    new_parts.append(part)
            else:
                new_parts.append(part)
        return new_parts if changed else content
    return content


def apply_slop_reduction(
    messages: list,
    lang: str,
    *,
    rules: Optional[list[dict]] = None,
    ruleset_version: Optional[int] = None,
    repeat_threshold: Optional[int] = None,
    dry_run: bool = False,
) -> list:
    """Return a NEW message list with AI-writing clichés rewritten in the
    assistant turns. Pure: no settings/IO, no mutation of ``messages`` or its
    elements. Suitable for direct unit testing.

    Args:
        messages: the history list (``BaseMessage`` objects and/or dicts).
        lang: short language code selecting the rule set (``zh``/``en``/...).
        rules: override rule set (defaults to ``get_rules_for_language(lang)``).
        ruleset_version: stable version mixed into replacement selection.
        repeat_threshold: first occurrence ordinal eligible for replacement;
            defaults to the built-in value (3) and is not a user setting.
        dry_run: count + log what *would* change but return ``messages`` as-is.

    Returns ``messages`` unchanged (same object) when there is nothing to do —
    no rules, or no assistant turns — so the no-op path is allocation-free.
    """
    rules = rules if rules is not None else get_rules_for_language(lang)
    if not rules:
        return messages
    default_ruleset_version, default_repeat_threshold = _get_ruleset_config()
    ruleset_version = (
        default_ruleset_version if ruleset_version is None else int(ruleset_version)
    )
    repeat_threshold = max(
        1,
        default_repeat_threshold if repeat_threshold is None else int(repeat_threshold),
    )
    prepared_rules = _prepare_rules(rules)
    if not prepared_rules:
        return messages
    counters: dict[str, int] = {}
    occurrence_counts: dict[int, int] = {}

    out: list = []
    changed = False
    assistant_message_index = 0
    for m in messages:
        # Rewrite only completed plain assistant turns. System instructions and
        # the user's own words pass through; an in-flight assistant+tool_calls
        # turn (the just-shown prefix the tool loop re-feeds) is left alone so
        # the model's continuation matches what the user already saw.
        if not _is_assistant_message(m) or _is_tool_turn(m):
            out.append(m)
            continue
        message_index = assistant_message_index
        assistant_message_index += 1
        if isinstance(m, dict):
            content = m.get("content")
            new_content = _rewrite_content(
                content,
                prepared_rules,
                lang=lang,
                ruleset_version=ruleset_version,
                repeat_threshold=repeat_threshold,
                message_index=message_index,
                occurrence_counts=occurrence_counts,
                counters=counters,
            )
            if new_content != content:
                out.append({**m, "content": new_content})
                changed = True
            else:
                out.append(m)
        else:
            content = getattr(m, "content", None)
            # Count into a scratch dict and only credit it if the clone
            # succeeds, so the log never reports replacements that were dropped
            # because the message object could not be safely cloned.
            scratch: dict[str, int] = {}
            new_content = _rewrite_content(
                content,
                prepared_rules,
                lang=lang,
                ruleset_version=ruleset_version,
                repeat_threshold=repeat_threshold,
                message_index=message_index,
                occurrence_counts=occurrence_counts,
                counters=scratch,
            )
            if new_content != content:
                # Defensive copy of the message object — never mutate the one
                # that lives in the persisted history.
                clone = _clone_message(m, new_content)
                if clone is not None:
                    out.append(clone)
                    changed = True
                    for k, v in scratch.items():
                        counters[k] = counters.get(k, 0) + v
                else:
                    out.append(m)
            else:
                out.append(m)

    total = sum(counters.values())
    if total:
        # Aggregate counts carry no conversation text → safe for the logger.
        logger.info(
            "slop reduction%s: %d replacement(s) across %d rule(s) [lang=%s]",
            " (dry-run)" if dry_run else "",
            total,
            len(counters),
            lang,
        )
        if os.environ.get(_DEBUG_ENV):
            # Per-rule hit counts (rule ids only — still no raw text) for tuning.
            print(f"[SLOP] lang={lang} dry_run={dry_run} hits={counters}")

    if dry_run or not changed:
        return messages
    return out


def _clone_message(m: Any, new_content: Any) -> Any:
    """Best-effort shallow clone of a message object with a replaced ``content``.

    Handles the project's ``BaseMessage`` dataclasses (and anything else with a
    ``type`` attribute and a ``content`` constructor arg). Returns ``None`` if it
    cannot safely clone, so the caller keeps the original (rewrite skipped for
    that turn rather than risking a corrupted object)."""
    try:
        cls = type(m)
        clone = cls.__new__(cls)
        # Copy instance state, then override content. dataclasses keep __dict__.
        if hasattr(m, "__dict__"):
            clone.__dict__.update(m.__dict__)
            clone.content = new_content
            return clone
    except Exception:
        # Best-effort: any construction/copy failure means "cannot safely
        # clone" — fall through to ``return None`` so the caller keeps the
        # original message untouched rather than risk a corrupted object.
        pass
    return None


# ────────────────────────────────────────────────────────────────
# Dialog-path integration helper (settings gate + lang resolution)
# ────────────────────────────────────────────────────────────────
def _resolve_short_lang(raw: Optional[str]) -> str:
    if not raw:
        return ""
    try:
        from utils.language_utils import normalize_language_code

        return normalize_language_code(raw, format="short") or ""
    except Exception:
        return ""


def _is_traditional_chinese(raw: Optional[str]) -> bool:
    """True for Traditional variants (zh-TW / zh-Hant / zh-HK …), which
    ``normalize_language_code`` collapses to a full code of ``zh-TW``. The shared
    ``zh`` rule set is Simplified, so these are skipped (see
    ``resolve_dialog_slop_lang``)."""
    if not raw:
        return False
    try:
        from utils.language_utils import normalize_language_code

        return (normalize_language_code(raw, format="full") or "") == "zh-TW"
    except Exception:
        return False


def is_slop_filter_enabled() -> bool:
    """Read the user's master switch (conversation settings → ``slopFilterEnabled``).

    Defaults to ``True`` when unset. Never raises — a settings read failure
    leaves the feature on (its rewrites are reversible and low-risk)."""
    try:
        from utils.preferences import load_global_conversation_settings

        return bool(load_global_conversation_settings().get("slopFilterEnabled", True))
    except Exception:
        return True


def resolve_dialog_slop_lang(
    user_language_provider: Optional[Callable[[], Optional[str]]],
) -> Optional[str]:
    """Resolve the short language code to use for slop reduction on THIS dialog
    turn, or ``None`` to skip.

    Returns ``None`` when the master switch is off, no language could be
    resolved, or that language has no rule set. The dialog entry points call
    this once per turn and, when it returns a code, arm the ``_dialog_slop_lang``
    context var so ``ChatOpenAI._params`` rewrites the assistant history on the
    wire. Never raises.
    """
    try:
        if not is_slop_filter_enabled():
            return None
        raw_lang = user_language_provider() if user_language_provider else None
        # The shared 'zh' rule set is written in Simplified Chinese. Feeding
        # Simplified rewrites into a Traditional (zh-TW / Hant) conversation
        # would nudge the model's script, so skip until a Traditional set exists.
        if _is_traditional_chinese(raw_lang):
            return None
        lang = _resolve_short_lang(raw_lang)
        if not lang or not get_rules_for_language(lang):
            return None
        return lang
    except Exception as exc:
        logger.debug("slop lang resolution skipped (non-fatal): %s", exc)
        return None
