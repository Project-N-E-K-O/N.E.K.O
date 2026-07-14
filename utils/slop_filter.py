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

Rule format (see ``config/prompts/prompts_slop.py``)
----------------------------------------------------
Each language maps to a list of rule dicts::

    {
        "id": "ZH_003",
        "name": "heart pounding",
        "find": r"...",            # a Python ``re`` pattern (NOT JS)
        "replace": ["...", ...],   # pool; one is picked at random per match
        "flags": 0,                # optional ``re`` flags (default 0)
    }

A pool entry may use Python backreferences (``\\1``, ``\\g<name>``) to carry
capture groups from ``find`` through to the replacement, e.g. preserving the
pronoun. Substitution uses :meth:`re.Match.expand`, so the syntax is exactly
what ``re.sub`` accepts as a template string.
"""
from __future__ import annotations

import os
import random
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
        except re.error as exc:
            # Bad pattern from a curated or learned rule — log the pattern (it is
            # author-supplied, not conversation content) once and poison the key.
            logger.warning("slop rule failed to compile, skipping: %r (%s)", pattern, exc)
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


def _rewrite_text(
    text: str,
    rules: Iterable[dict],
    rng: random.Random,
    counters: dict[str, int],
) -> str:
    """Apply every rule to ``text`` in order. Each rule is isolated in its own
    try/except so one malformed rule can never break the dialog turn."""
    out = text
    for rule in rules:
        pattern = rule.get("find")
        pool = rule.get("replace")
        if not pattern or not pool:
            continue
        compiled = _RULE_CACHE.get(pattern, int(rule.get("flags", 0) or 0))
        if compiled is None:
            continue

        def _replacer(match: "re.Match[str]") -> str:
            template = rng.choice(pool)
            try:
                return match.expand(template)
            except (re.error, IndexError):
                # Backref to a group the pattern didn't capture, or a stray
                # escape in the pool entry — fall back to the literal template.
                return template

        try:
            new_out, n = compiled.subn(_replacer, out)
        except Exception as exc:
            logger.debug("slop rule %s errored at apply time: %s", rule.get("id"), exc)
            continue
        if n:
            counters[rule.get("id") or pattern] = counters.get(rule.get("id") or pattern, 0) + n
            out = new_out
    return out


def _rewrite_content(content: Any, rules, rng, counters) -> Any:
    """Rewrite a message's ``content`` field, handling both a plain string and
    the multimodal list-of-parts shape (only ``text`` parts are touched)."""
    if isinstance(content, str):
        return _rewrite_text(content, rules, rng, counters)
    if isinstance(content, list):
        new_parts = []
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                new_parts.append({**part, "text": _rewrite_text(part["text"], rules, rng, counters)})
            else:
                new_parts.append(part)
        return new_parts
    return content


def apply_slop_reduction(
    messages: list,
    lang: str,
    *,
    rules: Optional[list[dict]] = None,
    rng: Optional[random.Random] = None,
    dry_run: bool = False,
) -> list:
    """Return a NEW message list with AI-writing clichés rewritten in the
    assistant turns. Pure: no settings/IO, no mutation of ``messages`` or its
    elements. Suitable for direct unit testing.

    Args:
        messages: the history list (``BaseMessage`` objects and/or dicts).
        lang: short language code selecting the rule set (``zh``/``en``/...).
        rules: override rule set (defaults to ``get_rules_for_language(lang)``).
        rng: inject a seeded ``random.Random`` for deterministic tests.
        dry_run: count + log what *would* change but return ``messages`` as-is.

    Returns ``messages`` unchanged (same object) when there is nothing to do —
    no rules, or no assistant turns — so the no-op path is allocation-free.
    """
    rules = rules if rules is not None else get_rules_for_language(lang)
    if not rules:
        return messages
    rng = rng or random
    counters: dict[str, int] = {}

    out: list = []
    changed = False
    for m in messages:
        # Rewrite only completed plain assistant turns. System instructions and
        # the user's own words pass through; an in-flight assistant+tool_calls
        # turn (the just-shown prefix the tool loop re-feeds) is left alone so
        # the model's continuation matches what the user already saw.
        if not _is_assistant_message(m) or _is_tool_turn(m):
            out.append(m)
            continue
        if isinstance(m, dict):
            content = m.get("content")
            new_content = _rewrite_content(content, rules, rng, counters)
            if new_content is not content:
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
            new_content = _rewrite_content(content, rules, rng, scratch)
            if new_content is not content:
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
