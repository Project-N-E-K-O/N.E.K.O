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
_BARE_CAPTURE = re.compile(r"\(\.\{1,\d+\}\?\)")
_GUARDED_CAPTURE = re.compile(r"\(\(\?!\\s\)\.\{1,\d+\}\?\)")


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
# 自动发现，不是手点清单：含原子空白的模板都要过等价性比较
_ATOMIZED = [t for t in _ALL if _ATOMIC_WS in t[1]]


def _ids(templates):
    return [label for label, _raw, _pat in templates]


# ── 计时：逐条模板，只计时这一条编译后的正则 ──
# ⚠️ 不计时整个 extract_directives：21 条模板的和里，单条回退会被淹掉。
# 三种输入各管一件事：
#   · 纯空白 3000：话题不许空白起头。任何一条回退到「空白里的每个位置都是起点」，
#     这里就是 0.07 秒（zh 模板 3）到 1 秒（en 模板 2）；修好之后每条 0.1 毫秒以内。
#   · ``"もうx" + 空白 300 + "y"``：ja 模板 2 捕获后的原子组。去原子化时 1.5 秒。
#   · ``"x" + 空白 1000 + "y"``：ko 模板 3 的两个原子组。去原子化时约 0.1 秒以上。
# 其余模板的原子组去掉之后代价小到计时分不出来，由下面的结构判据兜住。
_TIMING_INPUTS = {
    "spaces": " " * 3000,
    "mou-x-spaces-y": "もうx" + " " * 300 + "y",
    "x-spaces-y": "x" + " " * 1000 + "y",
}
_TIMING_LIMIT = 0.03


@pytest.mark.parametrize("label,raw,pat", _ALL, ids=_ids(_ALL))
@pytest.mark.parametrize("input_name", list(_TIMING_INPUTS))
def test_no_template_backtracks_over_long_whitespace(input_name, label, raw, pat):
    """Each template alone stays far below 30ms on long whitespace runs."""
    text = _TIMING_INPUTS[input_name]
    # 取多次里最快的一次滤掉调度噪声；修好的一侧都在毫秒以下，离 30ms 差一个数量级以上
    elapsed = fastest_run(lambda: list(pat.finditer(text)), repeat=3, stop_below=_TIMING_LIMIT)
    assert elapsed < _TIMING_LIMIT, f"{label} 在 {input_name} 上最快也要 {elapsed:.3f}s，空白回溯又回来了"


# ── 结构 ──


@pytest.mark.parametrize("label,raw,pat", _NON_ZH, ids=_ids(_NON_ZH))
def test_every_topic_capture_refuses_to_start_on_whitespace(label, raw, pat):
    """Structural: no bare ``(.{1,N}?)`` capture; each one starts with ``(?!\\s)``."""
    assert not _BARE_CAPTURE.search(raw), (label, raw)
    assert _GUARDED_CAPTURE.search(raw), (label, raw)


def test_the_zh_topic_captures_refuse_to_start_on_whitespace_too():
    """Structural, zh: template 2 guards its capture, template 3 only allows space after 我."""  # noqa: DOCSTRING_CJK
    zh = [raw for label, raw, _pat in _ALL if label.startswith("zh")]
    assert r"((?!\s)" in zh[1]
    assert zh[2].startswith("(?:我" + D._ZH_HSPACE + ")?"), zh[2][:80]


@pytest.mark.parametrize("label,raw,pat", _NON_ZH, ids=_ids(_NON_ZH))
def test_whitespace_after_the_capture_is_atomic(label, raw, pat):
    """Structural: no bare ``\\s*`` next to a group boundary after the capture group."""
    capture = _GUARDED_CAPTURE.search(raw)
    assert capture, raw
    rest = raw[capture.end():].replace(_ATOMIC_WS, "")
    # 剩下的 ``\s*`` 只允许夹在两个字面字之间（``에\s*대해`` / ``하지\s*마`` / ``듣기\s*싫``），
    # 那里没有可选组，不存在瓜分。挨着组边界的（``)\s*`` / ``)?\s*`` / ``\s*(``）就是隐患。
    assert not re.search(r"\)\??\\s\*|\\s\*\(", rest), (label, rest)


# ── 原子化不改变命中 ──

_TOKENS = (
    [" "] * 6
    + ["\t", "\n", "　", "a", "。"]
    + ["이", "가", "듣기", "싫", "말하기", "짜증나", "에", "대해", "얘기", "는", "은", "그만", "하지", "마"]
    + ["이제", "다시는", "말하지", "꺼내지", "말하지 마", "꺼내지  마세요"]
    + ["もう", "のこと", "の話", "は", "二度と", "言わないで", "嫌", "聞きたくない"]
    + ["って", "とは", "呼ばないで", "言うな"]
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
]


def _match_signature(m: re.Match[str] | None):
    return None if m is None else (m.span(), m.span(1), m.group(1))


@pytest.mark.parametrize("label,raw,pat", _ATOMIZED, ids=_ids(_ATOMIZED))
def test_atomizing_does_not_change_what_the_template_matches(label, raw, pat):
    """Same match, span and capture as the de-atomized twin, from every start position.

    Atomic ``(?>\\s*)`` is only safe while nothing that follows it can start with
    whitespace; an optional group that could would silently change matches.
    """
    # 用真实模板自己的 flags 编译对照版，不抄一份常量——模块哪天加了 flag，两边仍同一套规则
    twin = re.compile(raw.replace(_ATOMIC_WS, r"\s*"), pat.flags)
    assert twin.pattern != pat.pattern
    rng = random.Random(20260930)
    corpus = _SAMPLES + [
        "".join(rng.choice(_TOKENS) for _ in range(rng.randint(1, 14))) for _ in range(4000)
    ]
    hits = 0
    for text in corpus:
        for pos in range(len(text) + 1):
            expected = _match_signature(twin.search(text, pos))
            assert _match_signature(pat.search(text, pos)) == expected, (text, pos)
            hits += expected is not None
    # 防空转：语料里真有足够多的命中，比较才有意义（seed 固定，最少的 ja#1 是 590 次）
    assert hits > 100, (label, hits)


# ── 「不许空白起头」带来的有意的行为变化 ──
# 以前一段**全是空白**的话题会占住整段命中，term 剥成空串再丢掉，于是首尾多一个空格
# 就改了结果；zh 模板 2 的 ``关于`` temper 也能被前导空格绕过。现在补空白不改结果。


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
    """``" 关于工作别提了"`` used to also store ``关于工作``; the bare form never did."""  # noqa: DOCSTRING_CJK
    assert {term for _l, _k, term in extract_directives(" 关于工作别提了")} == {"工作"}
