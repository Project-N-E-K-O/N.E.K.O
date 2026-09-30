# -*- coding: utf-8 -*-
"""Whitespace backtracking guards for the ja / ko ban-topic templates.

``extract_directives`` runs synchronously on every user message. Four of its
ja / ko templates had two or more ``\\s*`` after the lazy capture group with
only optional groups between them, so one run of spaces could be split among
the capture and the quantifiers in polynomially many ways:

- ko ``(.{1,30}?)\\s*(?:이|가)?\\s*(?:듣기…)`` was cubic on a whitespace-only
  message: ``" " * 320`` took about 1.1s on its own.
- ja ``もう\\s*(.{1,40}?)\\s*(?:のこと|の話)?\\s*(?:は)?\\s*(?:嫌…)`` was worse:
  ``"もう" + " " * 320 + "x"`` took about 140s.

The whitespace quantifiers after the capture are now atomic ``(?>\\s*)``.
That must not change what the templates match; the equivalence test below
pins it against a de-atomized twin of each template.
"""  # noqa: DOCSTRING_CJK
from __future__ import annotations

import random
import re

import pytest

from config.prompts import prompts_directives as D
from tests.wall_clock import fastest_run

_ATOMIC_WS = r"(?>\s*)"
_FLAGS = re.IGNORECASE | re.UNICODE


def _template(locale: str, ordinal: int) -> tuple[str, re.Pattern[str]]:
    """The ``ordinal``-th template of ``locale`` as (raw source, compiled pattern)."""
    raws = [raw for loc, _kind, raw in D._PATTERNS_RAW if loc == locale]
    pats = [pat for loc, _kind, pat in D.DIRECTIVE_PATTERNS if loc == locale]
    assert pats[ordinal].pattern == raws[ordinal]
    return raws[ordinal], pats[ordinal]


# (locale, ordinal)：捕获组之后有多个 ``\s*``、中间只隔着可选组的那四条
_ATOMIZED = [("ja", 0), ("ja", 1), ("ko", 0), ("ko", 2)]


def test_ko_listen_template_is_not_cubic_on_whitespace():
    """ko template 3 on a whitespace-only message: ~1ms atomic, ~1s de-atomized."""
    _raw, pat = _template("ko", 2)
    text = " " * 320
    # ⚠️ 只计时这一条模板，不计时整个 extract_directives：其余模板在纯空白上本来就是
    # 二次方（每个起点线性），n=320 时合计几十毫秒，拿它们当判据测的是别人加机器负载。
    # 原子化版本本机约 1.5ms，退回 ``\s*`` 约 1.1s；0.1s 这条线两边各差近两个数量级，
    # 取多次里最快的一次滤掉调度噪声。
    elapsed = fastest_run(lambda: list(pat.finditer(text)), stop_below=0.1)
    assert elapsed < 0.1, f"ko 模板 3 在 {len(text)} 个空格上最快也要 {elapsed:.3f}s，空白瓜分回溯又回来了"


def test_ja_mou_template_does_not_blow_up_after_the_trigger():
    """ja template 2 after ``もう``: sub-millisecond atomic, ~1s de-atomized at n=100."""  # noqa: DOCSTRING_CJK
    _raw, pat = _template("ja", 1)
    # ⚠️ 尾巴的 ``x`` 是必要的：纯 ``もう`` + 空格时引擎在更早的地方就放弃了，测不出来。
    # 去原子化时这条是 n^4 量级（本机 80 → 0.33s，160 → 7.4s，320 → 142s），所以 n 只取
    # 100（约 0.9s）——回退时测试照样红，但不会一跑几分钟。
    text = "もう" + " " * 100 + "x"
    elapsed = fastest_run(lambda: list(pat.finditer(text)), stop_below=0.1)
    assert elapsed < 0.1, f"ja 模板 2 在 {text!r} 上最快也要 {elapsed:.3f}s，空白瓜分回溯又回来了"


@pytest.mark.parametrize("locale,ordinal", _ATOMIZED)
def test_whitespace_after_the_capture_is_atomic(locale, ordinal):
    """Structural: no bare ``\\s*`` next to a group boundary after the capture group."""
    raw, _pat = _template(locale, ordinal)
    capture = re.search(r"\(\.\{1,\d+\}\?\)", raw)
    assert capture, raw
    tail = raw[capture.end():]
    assert _ATOMIC_WS in tail, tail
    rest = tail.replace(_ATOMIC_WS, "")
    # 剩下的 ``\s*`` 只允许夹在两个字面字之间（``에\s*대해`` / ``하지\s*마`` / ``듣기\s*싫``），
    # 那里没有可选组，不存在瓜分。挨着组边界的（``)\s*`` / ``)?\s*`` / ``\s*(``）就是隐患。
    assert not re.search(r"\)\??\\s\*|\\s\*\(", rest), rest


_TOKENS = (
    [" "] * 6
    + ["\t", "\n", "　", "a", "。"]
    + ["이", "가", "듣기", "싫", "말하기", "짜증나", "에", "대해", "얘기", "는", "은", "그만", "하지", "마"]
    + ["もう", "のこと", "の話", "は", "二度と", "言わないで", "嫌", "聞きたくない"]
)
_SAMPLES = [
    "날씨가 듣기 싫어",
    "잔소리  짜증나",
    "그 일에 대해서는 그만 얘기해줘",
    "날씨 얘기 는  그만",
    "仕事のことはもう言わないで",
    "仕事 の話 は もう 言わないで",
    "もう天気の話は嫌だ",
    "もう 天気 の話 は 嫌",
    "もう  嫌",
]


def _match_signature(m: re.Match[str] | None):
    return None if m is None else (m.span(), m.span(1), m.group(1))


@pytest.mark.parametrize("locale,ordinal", _ATOMIZED)
def test_atomizing_does_not_change_what_the_template_matches(locale, ordinal):
    """Same match, span and capture as the de-atomized twin, from every start position.

    Atomic ``(?>\\s*)`` is only safe while nothing that follows it can start with
    whitespace; an optional group that could would silently change matches.
    """
    raw, pat = _template(locale, ordinal)
    twin = re.compile(raw.replace(_ATOMIC_WS, r"\s*"), _FLAGS)
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
    # 防空转：语料里真有足够多的命中，比较才有意义（seed 固定，最少的 ko 模板 1 是 218 次）
    assert hits > 100, hits
