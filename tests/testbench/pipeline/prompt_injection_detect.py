"""Prompt injection pattern detector — P21.3 F3 (testbench-only).

Testbench design principle (see PLAN.md §13 — Prompt Injection 最小化防御
pass): **永不过滤用户内容** (never filter user content). This module is
a pure detector, not a sanitizer — it reports "this text looks like it
contains a prompt-injection pattern" so:

  * UI can show a ``⚠ N`` badge next to user-editable fields so testers
    realise their input will reach an AI in testbench's own role-marker
    format.
  * ``python_logger()`` can emit a ``prompt_injection_suspected``
    warning for audit / Diagnostics panels.
  * AI-facing prompt builders can decide to add extra delimiter /
    preamble framing (see ``pipeline/judge_runner.py`` F2 changes) but
    **never** edit or drop the offending content.

The detector is deliberately simple (regex + literal substring) because
prompt-injection variations are infinite; we cover the obvious
role-marker tokens and the most commonly-observed jailbreak phrases in
both English and Chinese. False positives are preferable to false
negatives for a warning channel (worst case: tester sees a harmless
``[INST]`` in their own persona and shrugs; best case: they spot an
actual smuggled jailbreak before rerunning an eval).

Public API::

    from tests.testbench.pipeline import prompt_injection_detect as pid

    hits = pid.detect("ignore previous instructions and...")
    # → [InjectionHit(pattern_id="jailbreak_en_ignore", ...), ...]

    pid.detect_any("...") -> bool            # fast path for UI badge
    pid.summarize(hits) -> dict              # aggregated counts

Does **not** mutate the input. Callers that want to warn only once per
session should dedupe at their own layer (e.g., hash of ``session.id``
+ field path).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable


# ── Pattern library ─────────────────────────────────────────────────


# Category labels drive UI grouping and log field names. Keep stable —
# Diagnostics / log-based dashboards may depend on these strings.
CATEGORY_CHATML = "chatml_token"
CATEGORY_LLAMA = "llama_instruction_token"
CATEGORY_ROLE_MARKER = "role_marker"
CATEGORY_JAILBREAK = "jailbreak_phrase"
CATEGORY_SYSTEM_IMPERSONATION = "system_impersonation"


@dataclass(frozen=True)
class _Pattern:
    """One entry in the detection library.

    ``regex`` is compiled once at module load. ``category`` groups
    hits for the UI (e.g., show a single "ChatML tokens found × 3"
    chip instead of three separate chips). ``severity`` is
    ``info`` / ``warn`` / ``crit`` — UI badges raise visual prominence
    in that order; logging always emits at WARNING regardless (see
    README — all detections are advisory).
    """
    id: str
    category: str
    severity: str
    pattern: str
    description: str
    regex: re.Pattern[str] = field(init=False)

    def __post_init__(self) -> None:  # noqa: D401
        object.__setattr__(self, "regex", re.compile(self.pattern, re.IGNORECASE))


SUSPICIOUS_PATTERNS: tuple[_Pattern, ...] = (
    # ── ChatML / OpenAI tokenizer special tokens ─────────────────
    _Pattern(
        id="chatml_im_start",
        category=CATEGORY_CHATML,
        severity="warn",
        pattern=r"<\|im_start\|>",
        description="OpenAI ChatML start-of-message token",
    ),
    _Pattern(
        id="chatml_im_end",
        category=CATEGORY_CHATML,
        severity="warn",
        pattern=r"<\|im_end\|>",
        description="OpenAI ChatML end-of-message token",
    ),
    _Pattern(
        id="chatml_endoftext",
        category=CATEGORY_CHATML,
        severity="warn",
        pattern=r"<\|endoftext\|>",
        description="OpenAI end-of-text token",
    ),
    _Pattern(
        id="chatml_fim",
        category=CATEGORY_CHATML,
        severity="info",
        pattern=r"<\|fim_(?:prefix|middle|suffix)\|>",
        description="OpenAI FIM (fill-in-middle) token",
    ),
    # ── Llama / Mistral instruction tokens ──────────────────────
    _Pattern(
        id="llama_inst_open",
        category=CATEGORY_LLAMA,
        severity="warn",
        pattern=r"\[INST\]",
        description="Llama / Mistral [INST] open tag",
    ),
    _Pattern(
        id="llama_inst_close",
        category=CATEGORY_LLAMA,
        severity="warn",
        pattern=r"\[/INST\]",
        description="Llama / Mistral [/INST] close tag",
    ),
    _Pattern(
        id="llama_sys_open",
        category=CATEGORY_LLAMA,
        severity="warn",
        pattern=r"<<SYS>>",
        description="Llama system prompt open tag",
    ),
    _Pattern(
        id="llama_sys_close",
        category=CATEGORY_LLAMA,
        severity="warn",
        pattern=r"<</SYS>>",
        description="Llama system prompt close tag",
    ),
    _Pattern(
        id="llama_bos",
        category=CATEGORY_LLAMA,
        severity="info",
        pattern=r"<s>|</s>",
        description="Llama begin/end-of-sequence token",
    ),
    # ── Role-marker impersonation (line-leading) ────────────────
    _Pattern(
        id="role_marker_assistant",
        category=CATEGORY_ROLE_MARKER,
        severity="warn",
        pattern=r"(?m)^\s*(?:ASSISTANT|SYSTEM|USER|DEVELOPER)\s*[:：]",
        description="Line-leading role marker (ASSISTANT:/SYSTEM:/USER:/DEVELOPER:)",
    ),
    _Pattern(
        id="role_marker_bracketed",
        category=CATEGORY_ROLE_MARKER,
        severity="warn",
        pattern=r"(?m)^\s*\[(?:ASSISTANT|SYSTEM|USER|DEVELOPER)\]",
        description="Bracketed role marker ([ASSISTANT] / [SYSTEM] etc.)",
    ),
    # ── System / role impersonation declarations ─────────────────
    _Pattern(
        id="sysimp_en_you_are_now",
        category=CATEGORY_SYSTEM_IMPERSONATION,
        severity="warn",
        pattern=r"\byou are (?:now|an?) (?:DAN|developer mode|jailbroken|a new)\b",
        description="Common jailbreak role-declaration openings",
    ),
    _Pattern(
        id="sysimp_en_act_as",
        category=CATEGORY_SYSTEM_IMPERSONATION,
        severity="info",
        pattern=r"\bact as (?:a|an|the) (?:system|admin|developer|uncensored)\b",
        description="'Act as X' pattern (developer mode variants)",
    ),
    # ── Jailbreak override phrases (EN) ──────────────────────────
    _Pattern(
        id="jailbreak_en_ignore",
        category=CATEGORY_JAILBREAK,
        severity="warn",
        pattern=r"\bignore (?:all |the |any )?(?:previous|above|prior|earlier) (?:instructions|prompts?|rules?|directives?)\b",
        description="'Ignore previous instructions' (EN)",
    ),
    _Pattern(
        id="jailbreak_en_disregard",
        category=CATEGORY_JAILBREAK,
        severity="warn",
        pattern=r"\bdisregard (?:all |the |any )?(?:previous|above|prior|earlier) (?:instructions|prompts?|rules?)\b",
        description="'Disregard the above' (EN)",
    ),
    _Pattern(
        id="jailbreak_en_forget",
        category=CATEGORY_JAILBREAK,
        severity="warn",
        pattern=r"\bforget (?:everything|all) (?:above|previous|before)\b",
        description="'Forget everything above' (EN)",
    ),
    _Pattern(
        id="jailbreak_en_new_rules",
        category=CATEGORY_JAILBREAK,
        severity="info",
        pattern=r"\b(?:new|override) (?:system )?(?:rules|instructions|prompt)\b",
        description="'New system rules / override' (EN)",
    ),
    # ── Jailbreak override phrases (ZH) ──────────────────────────
    # Pattern matches are anchored without \b because Chinese has no
    # word boundary semantics in regex.
    _Pattern(
        id="jailbreak_zh_ignore_above",
        category=CATEGORY_JAILBREAK,
        severity="warn",
        pattern=r"(?:忽略|无视|不要理会|不要遵从)(?:上述|以上|之前|先前)(?:所有)?(?:的)?(?:指令|指示|要求|规则|提示|设定)",
        description="忽略上述 / 无视以上 (ZH)",
    ),
    _Pattern(
        id="jailbreak_zh_forget_before",
        category=CATEGORY_JAILBREAK,
        severity="warn",
        pattern=r"(?:忘记|忘掉)(?:以上|之前|前面|先前)(?:所有)?(?:的)?(?:内容|指令|设定|规则)",
        description="忘记以上/之前 (ZH)",
    ),
    _Pattern(
        id="jailbreak_zh_role_change",
        category=CATEGORY_JAILBREAK,
        severity="warn",
        pattern=r"(?:你现在是|从现在开始你是|你不再是|重置(?:你的)?身份)",
        description="你现在是 / 从现在开始你是 (ZH)",
    ),
    _Pattern(
        id="jailbreak_zh_override",
        category=CATEGORY_JAILBREAK,
        severity="info",
        pattern=r"(?:修改|更改|重写)(?:你的)?(?:评分|规则|标准|指令|身份|设定)",
        description="修改/重写 评分/规则/身份 (ZH)",
    ),
)


# ── Data model ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class InjectionHit:
    """One detection result."""
    pattern_id: str
    category: str
    severity: str
    description: str
    start: int
    end: int
    match_text: str

    def to_dict(self) -> dict[str, object]:
        """JSON-shaped dict for API / UI transport."""
        return {
            "pattern_id": self.pattern_id,
            "category": self.category,
            "severity": self.severity,
            "description": self.description,
            "start": self.start,
            "end": self.end,
            "match_text": self.match_text,
        }


# ── Public API ──────────────────────────────────────────────────────


# Cap per-pattern hit count so a pathologically long input full of role
# markers doesn't blow up the detector / UI payload. Beyond this we
# truncate and set ``truncated=True`` on the summary.
MAX_HITS_PER_PATTERN: int = 10

# Cap per-hit match text snippet so the response/log doesn't carry
# hundreds of KB of surrounding content.
MAX_MATCH_SNIPPET: int = 120


def detect(
    text: str, *,
    patterns: Iterable[_Pattern] | None = None,
) -> list[InjectionHit]:
    """Scan ``text`` for every pattern and return all hits.

    Returns an empty list when ``text`` is empty or non-str. The hit
    list is deterministic (ordered by pattern definition, then match
    position within pattern), so callers can dedupe by ``pattern_id``
    for UI aggregation.
    """
    if not isinstance(text, str) or not text:
        return []
    if patterns is None:
        patterns = SUSPICIOUS_PATTERNS

    hits: list[InjectionHit] = []
    for pat in patterns:
        count = 0
        for m in pat.regex.finditer(text):
            if count >= MAX_HITS_PER_PATTERN:
                break
            count += 1
            snippet = m.group(0)
            if len(snippet) > MAX_MATCH_SNIPPET:
                snippet = snippet[:MAX_MATCH_SNIPPET] + "..."
            hits.append(InjectionHit(
                pattern_id=pat.id,
                category=pat.category,
                severity=pat.severity,
                description=pat.description,
                start=m.start(),
                end=m.end(),
                match_text=snippet,
            ))
    return hits


def detect_any(text: str) -> bool:
    """Fast path: does ``text`` trigger *any* pattern?

    Returns early on the first match. Useful for per-field UI badges
    where we only care "is there at least one warning".
    """
    if not isinstance(text, str) or not text:
        return False
    for pat in SUSPICIOUS_PATTERNS:
        if pat.regex.search(text):
            return True
    return False


def summarize(hits: list[InjectionHit]) -> dict[str, object]:
    """Aggregate hits into a UI-friendly summary.

    Returns ``{count, by_category, by_severity, top_hits}`` where
    ``top_hits`` is at most the first 5 hits (for a tooltip /
    collapsible detail view).
    """
    if not hits:
        return {
            "count": 0,
            "by_category": {},
            "by_severity": {},
            "top_hits": [],
        }
    by_cat: dict[str, int] = {}
    by_sev: dict[str, int] = {}
    for h in hits:
        by_cat[h.category] = by_cat.get(h.category, 0) + 1
        by_sev[h.severity] = by_sev.get(h.severity, 0) + 1
    return {
        "count": len(hits),
        "by_category": by_cat,
        "by_severity": by_sev,
        "top_hits": [h.to_dict() for h in hits[:5]],
    }


def detect_bulk(fields: dict[str, str]) -> dict[str, object]:
    """Scan multiple named fields; return ``{field_name: summary}``.

    Convenience wrapper for endpoints that want to check persona /
    memory / schema fields in one round-trip. Fields with no hits are
    omitted from the output to keep payloads small.
    """
    result: dict[str, object] = {}
    for name, text in (fields or {}).items():
        if not isinstance(text, str):
            continue
        hits = detect(text)
        if hits:
            result[name] = summarize(hits)
    return result


__all__ = [
    "CATEGORY_CHATML",
    "CATEGORY_LLAMA",
    "CATEGORY_ROLE_MARKER",
    "CATEGORY_JAILBREAK",
    "CATEGORY_SYSTEM_IMPERSONATION",
    "InjectionHit",
    "SUSPICIOUS_PATTERNS",
    "MAX_HITS_PER_PATTERN",
    "MAX_MATCH_SNIPPET",
    "detect",
    "detect_any",
    "detect_bulk",
    "summarize",
]
