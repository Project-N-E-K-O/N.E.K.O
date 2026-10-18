# -*- coding: utf-8 -*-
"""Whitespace backtracking guards for the ban-topic templates.

``extract_directives`` runs synchronously on every user message, and nothing
bounds the message length. Two separate defects made long runs of whitespace
expensive:

- **Where a match may start.** A topic capture whose first character may be a
  space turns every position inside a run of spaces into a candidate start,
  and each start rescans the rest of the run: O(n^2) per template, with the
  capture bound as the constant. ``extract_directives(" " * 3000)`` took 3.6s.
  Every topic capture now refuses to start on whitespace.
- **How a run is split.** Several ja / ko templates had two or more ``\\s*``
  after the capture with only optional groups between them, so one run could be
  divided among them in polynomially many ways: the ko ``(?:이|가)?`` template
  was cubic, the ja ``もう`` template about n^4 (``"もう" + " " * 320 + "x"``
  took about 140s). Those quantifiers are now atomic ``(?>\\s*)``.

Timing only catches a regression where it is slow, so each defect also has a
structural check, and the atomic groups have an equivalence check against
their de-atomized twins.
"""  # noqa: DOCSTRING_CJK
from __future__ import annotations

import random
import re

import pytest

from config.prompts import prompts_directives as D
from config.prompts.prompts_directives import extract_directives
from tests.wall_clock import fastest_run

_ATOMIC_WS = r"(?>\s*)"
_ATOMIC_WS_PLUS = r"(?>\s+)"
_END_GUARD = r"(?<!\s)"
_BARE_CAPTURE = re.compile(r"\(\.\{1,\d+\}\?\)")
# Capture may end in ``(?<!\s)`` (en template 2: whitespace-ending captures are futile; see its comment).
_GUARDED_CAPTURE = re.compile(r"\(\(\?!\\s\)\.\{1,\d+\}\?(?:\(\?<!\\s\))?\)")


def _templates() -> list[tuple[str, str, re.Pattern[str]]]:
    """Every template as (``"ko#3"``-style label, raw source, compiled pattern)."""
    out = []
    counts: dict[str, int] = {}
    for (locale, _kind, raw), (_l, _k, pat) in zip(D._PATTERNS_RAW, D.DIRECTIVE_PATTERNS):
        assert pat.pattern == raw
        counts[locale] = counts.get(locale, 0) + 1
        out.append((f"{locale}#{counts[locale]}", raw, pat))
    return out


_ALL = _templates()
_NON_ZH = [t for t in _ALL if not t[0].startswith("zh")]
# Discover automatically, no manual list: every template with atomic whitespace (``(?>\s*)`` / ``(?>\s+)``)
# or capture-ending ``(?<!\s)`` must pass equivalence comparison: pruning must not change matches.
_ATOMIZED = [t for t in _ALL if any(u in t[1] for u in (_ATOMIC_WS, _ATOMIC_WS_PLUS, _END_GUARD))]


def _ids(templates):
    return [label for label, _raw, _pat in templates]


# ── Timing: individual compiled regexes only ──
# ⚠️ Never time all extract_directives: summed 21-template costs hide individual regressions.
# Each input tests one behavior; thresholds separate slowest fixed and fastest regressed cases,
# leaving >4× margin both sides (local fastest-of-five measurements):
#   · 3000 whitespace: topics cannot begin with whitespace. Regressing to every whitespace position as start
#     costs 75ms (zh template 3 leading whitespace) to one second (en template 2).
#   · ``"もうx" + 300 spaces + "y"``: ja template 2 postcapture atomic group; removing atomicity costs 1.5s.
#   · ``"x" + 1000 spaces + "y"``: ko template 3's two atomic groups; non-atomic costs ~0.1s.
#   Fixed versions all below 0.25ms; threshold 15ms.
#   · Trigger + long whitespace: between trigger and capture. Only one start, but forbidding whitespace-starting
#     topics removes whitespace-topic fast exit; earlier quantifiers try every split of the same whitespace:
#     es/pt template 1's ``\s+(?:de|…)?\s*`` without atomicity costs 0.33s; zh templates 3/1
#     without ``(?!\s)`` or moving it after objectless lookahead cost ~0.3s (lookahead rescans all spaces at each position).
#     Pure whitespace cannot enter this path: trigger absent, first literal already fails.
#   Slowest fixed case among these four: zh template 2 (``"no hables"`` input 14ms), starting with lazy topic;
#   each nonspace prefix character starts another 2-to-30-unit topic search, rescanning whitespace each expansion,
#   linear but high constant. Minimum two-unit topic forbids en-template-2-style trailing
#   ``(?<!\s)`` (two-unit whitespace-ending topic is valid shortest split), so threshold 60ms.
# Other templates' atomicity costs are too small to distinguish by timing; structural checks below cover them.
_TIMING_INPUTS = {
    "spaces": (" " * 3000, 0.015),
    "mou-x-spaces-y": ("もうx" + " " * 300 + "y", 0.015),
    "x-spaces-y": ("x" + " " * 1000 + "y", 0.015),
    "no-hables-spaces": ("no hables" + " " * 8000, 0.06),
    "nao-fale-spaces": ("não fale" + " " * 8000, 0.06),
    "zh-reluctance-spaces-x": ("我不想聊" + " " * 12000 + "x", 0.06),
    "zh-negation-spaces-x": ("别提" + " " * 16000 + "x", 0.06),
}
# Skip retries if first sample exceeds these seconds: far beyond scheduling noise, saving repeats of true 0.3s–2min regressions.
_NO_RETRY_ABOVE = 5.0


@pytest.mark.parametrize("label,raw,pat", _ALL, ids=_ids(_ALL))
@pytest.mark.parametrize("input_name", list(_TIMING_INPUTS))
def test_no_template_backtracks_over_long_whitespace(input_name, label, raw, pat):
    """Each template alone stays well below its input's limit on long whitespace runs."""
    text, limit = _TIMING_INPUTS[input_name]
    # Take fastest sample to filter scheduling noise. Retry unless first exceeds _NO_RETRY_ABOVE: some regressions
    # take two minutes (ko template 3 non-atomic trigger input 130s); tripling retries turns definite
    # failure into CI timeout. Use absolute seconds far above scheduler noise, not threshold multiples:
    # 10 × 15ms = 150ms, reachable by one shared-runner preemption (tests/wall_clock.py documents
    # hundreds of ms), falsely failing fixed regexes on one bad sample (Greptile P1).
    elapsed = fastest_run(lambda: list(pat.finditer(text)), repeat=1)
    if limit <= elapsed < _NO_RETRY_ABOVE:
        elapsed = min(elapsed, fastest_run(lambda: list(pat.finditer(text)), repeat=2, stop_below=limit))
    assert elapsed < limit, f"{label} 在 {input_name} 上最快也要 {elapsed:.3f}s（线 {limit}s），空白回溯又回来了"


# ── Structure ──


@pytest.mark.parametrize("label,raw,pat", _NON_ZH, ids=_ids(_NON_ZH))
def test_every_topic_capture_refuses_to_start_on_whitespace(label, raw, pat):
    """Structural: no bare ``(.{1,N}?)`` capture; each one starts with ``(?!\\s)``."""
    assert not _BARE_CAPTURE.search(raw), (label, raw)
    assert _GUARDED_CAPTURE.search(raw), (label, raw)


def test_the_zh_topic_captures_refuse_to_start_on_whitespace_too():
    """Structural, zh: template 2 guards its capture; template 3 only allows space after 我.

    The objectless-lookahead guards (templates 1 and 3) are discovered by the test below.
    """  # noqa: DOCSTRING_CJK
    zh = [raw for label, raw, _pat in _ALL if label.startswith("zh")]
    assert r"((?!\s)" in zh[1]
    assert zh[2].startswith("(?:我" + D._ZH_HSPACE + ")?"), zh[2][:80]


@pytest.mark.parametrize("label,raw,pat", [t for t in _ALL if t[0].startswith("zh")], ids=_ids([t for t in _ALL if t[0].startswith("zh")]))
def test_every_zh_objectless_lookahead_is_guarded(label, raw, pat):
    """Structural, auto-discovered: each ``_ZH_OBJECTLESS_AHEAD`` is preceded by ``(?!\\s)``.

    The lookahead rescans the whole run of spaces at every position the verb's
    whitespace gives back; zh template 1 had the same shape as template 3 and
    was missed when only template 3 was listed by index.
    """
    count = raw.count(D._ZH_OBJECTLESS_AHEAD)
    assert raw.count(r"(?!\s)" + D._ZH_OBJECTLESS_AHEAD) == count, (label, count)


# Two bare whitespace quantifiers separated by optional group: ``\s+(?:de|sobre)?\s*``. Absent group lets same whitespace
# split n ways between quantifiers, all tried on later failure.
_WS_SANDWICH = re.compile(r"\\s[*+](?:\(\?:[^()]*\)\?)+\\s[*+]")


@pytest.mark.parametrize("label,raw,pat", _NON_ZH, ids=_ids(_NON_ZH))
def test_no_two_bare_whitespace_quantifiers_around_an_optional_group(label, raw, pat):
    """Structural: anywhere in the template, not only after the capture (es/pt had it before)."""
    assert not _WS_SANDWICH.search(raw), (label, _WS_SANDWICH.search(raw).group(0))


@pytest.mark.parametrize("label,raw,pat", _NON_ZH, ids=_ids(_NON_ZH))
def test_whitespace_after_the_capture_is_atomic(label, raw, pat):
    """Structural: no bare ``\\s*`` next to a group boundary after the capture group."""
    capture = _GUARDED_CAPTURE.search(raw)
    assert capture, raw
    rest = raw[capture.end():].replace(_ATOMIC_WS, "")
    # Remaining ``\s*`` only between literals (``에\s*대해`` / ``하지\s*마`` / ``듣기\s*싫``),
    # with no optional groups/split ambiguity. Group-adjacent forms (``)\s*`` / ``)?\s*`` / ``\s*(``) are risky.
    assert not re.search(r"\)\??\\s\*|\\s\*\(", rest), (label, rest)


# ── Atomicity preserves matches ──

_TOKENS = (
    [" "] * 6
    + ["\t", "\n", "　", "a", "。"]
    + ["이", "가", "듣기", "싫", "말하기", "짜증나", "에", "대해", "얘기", "는", "은", "그만", "하지", "마"]
    + ["이제", "다시는", "말하지", "꺼내지", "말하지 마", "꺼내지  마세요"]
    + ["もう", "のこと", "の話", "は", "二度と", "言わないで", "嫌", "聞きたくない"]
    + ["って", "とは", "呼ばないで", "言うな"]
    + ["no hables", "deja de hablar", "de", "sobre", "acerca de", "fútbol", "más", "por favor", ","]
    + ["não fale", "deixa de falar", "a respeito de", "trabalho", "mais", "hoje"]
    + ["work", "my ex", "is", "is off limits", "is off the table", "is a no-go topic", "off-limits"]
)
_SAMPLES = [
    "날씨가 듣기 싫어",
    "잔소리  짜증나",
    "그 일에 대해서는 그만 얘기해줘",
    "날씨 얘기 는  그만",
    "이제  그 사람  말하지  마",
    "仕事のことはもう言わないで",
    "仕事 の話 は もう 言わないで",
    "もう天気の話は嫌だ",
    "もう 天気 の話 は 嫌",
    "お兄ちゃん  って  呼ばないで",
    "no hables de fútbol, por favor.",
    "no hables  sobre  fútbol",
    "no hables de",
    "não fale de trabalho hoje.",
    "my ex   is off limits.",
    "work is  a forbidden topic",
]


def _match_signature(m: re.Match[str] | None):
    return None if m is None else (m.span(), m.span(1), m.group(1))


@pytest.mark.parametrize("label,raw,pat", _ATOMIZED, ids=_ids(_ATOMIZED))
def test_atomizing_does_not_change_what_the_template_matches(label, raw, pat):
    """Same match, span and capture as the de-atomized twin, from every start position.

    Atomic ``(?>\\s*)`` is only safe while nothing that follows it can start with
    whitespace; an optional group that could would silently change matches.
    """
    # Compile comparison with actual template flags, not copied constants; future flags remain identical on both sides.
    twin = re.compile(
        raw.replace(_ATOMIC_WS, r"\s*").replace(_ATOMIC_WS_PLUS, r"\s+").replace(_END_GUARD, ""),
        pat.flags,
    )
    assert twin.pattern != pat.pattern
    rng = random.Random(20260930)
    corpus = _SAMPLES + [
        "".join(rng.choice(_TOKENS) for _ in range(rng.randint(1, 14))) for _ in range(3000)
    ]
    hits = 0
    for text in corpus:
        for pos in range(len(text) + 1):
            expected = _match_signature(twin.search(text, pos))
            assert _match_signature(pat.search(text, pos)) == expected, (text, pos)
            hits += expected is not None
    # Prevent vacuous tests: enough real matches required (fixed seed, lowest ja#1 has 325 matches).
    assert hits > 100, (label, hits)


# ── Intentional behavior change from forbidding whitespace starts ──
# Previously all-whitespace topics consumed matches, then stripped empty and discarded, so added surrounding spaces
# changed results; zh template 2's ``关于`` temper could be bypassed with leading space. Added spaces no longer change results.


@pytest.mark.parametrize("text", [
    "짜증나 듣기 싫어",
    "no hables de",
    "não fale de",
    "关于工作别提了",
    "工作别提了",
    "그 일에 대해서는 그만 얘기해줘",
    "もう天気の話は嫌だ",
    "stop talking about work please.",
])
@pytest.mark.parametrize("pad", [" {}", "{} ", "　{}\t"])
def test_padding_a_message_with_whitespace_does_not_change_the_result(text, pad):
    """Leading / trailing whitespace yields exactly what the bare message yields."""
    assert extract_directives(pad.format(text)) == extract_directives(text)


def test_a_leading_space_no_longer_slips_guanyu_past_the_preposed_template():
    """``\" 关于工作别提了\"`` used to also store ``关于工作``; the bare form never did."""  # noqa: DOCSTRING_CJK
    assert {term for _l, _k, term in extract_directives(" 关于工作别提了")} == {"工作"}
