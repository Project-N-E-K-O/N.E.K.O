# -*- coding: utf-8 -*-
"""Traditional-Chinese coverage for the ban-topic regex templates (issue #2500).

``extract_directives`` is what persists "the user told me not to bring X up" into
``memory/{name}/user_directives.json`` for 3 days. Its zh templates were written
with Simplified glyphs only, so a Traditional writer's "別再提小明了" matched
nothing: not a lower score, a structural 100% miss. Only the handful of phrasings
that happen to be script-neutral ("我不想聊…") ever worked.

Three things are being pinned here, because the fix is not just "add the other
glyphs":

1. **Recall** — the Traditional forms extract the same term the Simplified ones do.
2. **The Japanese collision** — ``別`` is the *same codepoint* in Japanese, and
   ``提 / 講 / 談 / 討論`` are shared kanji, so adding Traditional glyphs drags
   Japanese input into the zh templates' range ("特別講演について話しましょう。"
   → ban_topic "演について話しましょう"). ``說`` is safe by luck alone (Japanese
   writes ``説`` U+8AAC). ``_is_japanese_sentence_match`` is what keeps this
   closed — and it has to stay narrow, because the thing being banned is very
   often *itself* Japanese ("別叫我お兄ちゃん")：a blanket "kana in the match →
   drop it" throws away exactly the preference the user just stated.
3. **The compound-noun left edge** — "他特别提到你的名字。" was *already* being
   extracted as a ban_topic before this change; merging the scripts would have
   handed Traditional users the same bug. ``_BIE_COMPOUND_LEFT`` fixes the four
   compounds that have no natural counterexample, on both scripts at once.

⚠️ The Chinese side of (3) is deliberately *not* exhaustive. "这个别提了" vs
"个别说法", "这部分别提了" vs "分别说明" are the same characters in the same
order — no lookbehind separates them, so the remaining false positives stay,
identically on both scripts. Tightening further kills the main use case.
"""  # noqa: DOCSTRING_CJK
from __future__ import annotations

import pathlib
import re
import re._constants as _sre  # Private module: inspect parse trees for structural criteria (project pins Python 3.11).
import re._parser as _sre_parser
import unicodedata

import pytest

from config.prompts import prompts_directives as D
from config.prompts.prompts_directives import extract_directives


def _zh_terms(text: str) -> set[str]:
    """Terms extracted by the **zh** templates only (ja hits are a different owner)."""
    return {term for locale, _kind, term in extract_directives(text) if locale == "zh"}


def _zh_pattern_sources() -> list[str]:
    return [raw for locale, _kind, raw in D._PATTERNS_RAW if locale == "zh"]


_REPEATS = (_sre.MAX_REPEAT, _sre.MIN_REPEAT)
_NOT_SPACE_CATEGORIES = (_sre.CATEGORY_NOT_SPACE, _sre.CATEGORY_UNI_NOT_SPACE)


def _class_is_whitespace_only(items) -> bool:
    if items and items[0][0] is _sre.NEGATE:
        # ``[^\S...]``: a negated class that excludes every non-space is a subset of \s
        return any(op is _sre.CATEGORY and av in _NOT_SPACE_CATEGORIES for op, av in items[1:])
    return all(
        (op is _sre.LITERAL and chr(av).isspace())
        or (op is _sre.RANGE and all(chr(c).isspace() for c in range(av[0], av[1] + 1)))
        or (op is _sre.CATEGORY and av in (_sre.CATEGORY_SPACE, _sre.CATEGORY_UNI_SPACE))
        for op, av in items
    )


def _never_consumes_non_whitespace(seq) -> bool:
    # Empty sequences count True: the empty branch in ``(?:\s|)*`` consumes nothing, leaving it equivalent to ``\s*`` (codex P2).
    # _consumes_something separately requires consuming at least one character.
    for op, av in seq:
        if op in (_sre.AT, _sre.ASSERT, _sre.ASSERT_NOT):
            continue
        if op is _sre.LITERAL:
            ok = chr(av).isspace()
        elif op is _sre.IN:
            ok = _class_is_whitespace_only(av)
        elif op is _sre.SUBPATTERN:
            ok = _never_consumes_non_whitespace(av[3])
        elif op is _sre.ATOMIC_GROUP:
            ok = _never_consumes_non_whitespace(av)
        elif op is _sre.BRANCH:
            ok = all(_never_consumes_non_whitespace(branch) for branch in av[1])
        elif op in _REPEATS or op is _sre.POSSESSIVE_REPEAT:
            ok = _never_consumes_non_whitespace(av[2])
        else:
            ok = False
        if not ok:
            return False
    return True


def _consumes_something(seq) -> bool:
    for op, av in seq:
        if op in (_sre.LITERAL, _sre.NOT_LITERAL, _sre.IN, _sre.ANY):
            return True
        if op is _sre.SUBPATTERN:
            sub = [av[3]]
        elif op is _sre.ATOMIC_GROUP:
            sub = [av]
        elif op is _sre.BRANCH:
            sub = av[1]
        elif op in _REPEATS or op is _sre.POSSESSIVE_REPEAT:
            sub = [av[2]] if av[1] else []
        else:
            sub = []  # lookarounds / anchors are zero-width
        if any(_consumes_something(s) for s in sub):
            return True
    return False


def _matches_only_whitespace(seq) -> bool:
    return _never_consumes_non_whitespace(seq) and _consumes_something(seq)


def _collect_whitespace_runs(seq, out: list[str]) -> list[str]:
    for op, av in seq:
        if op in (_sre.ASSERT, _sre.ASSERT_NOT, _sre.ATOMIC_GROUP, _sre.POSSESSIVE_REPEAT):
            continue  # zero-width, or already atomic
        if op in _REPEATS:
            lo, hi, body = av
            if (hi is _sre.MAXREPEAT or hi > 8) and _matches_only_whitespace(body):
                out.append(f"{op}{{{lo},{'' if hi is _sre.MAXREPEAT else hi}}}")
                continue
            _collect_whitespace_runs(body, out)
        elif op is _sre.SUBPATTERN:
            _collect_whitespace_runs(av[3], out)
        elif op is _sre.BRANCH:
            for branch in av[1]:
                _collect_whitespace_runs(branch, out)
    return out


def _splittable_whitespace_runs(head: str) -> list[str]:
    """Non-atomic whitespace repetitions in a template source, found on the parsed regex.

    Two such runs next to each other (or next to the topic's plain-char branch,
    which also eats spaces) can split one run of spaces in O(n^k) ways. An
    atomic group ``(?>...)`` or a possessive quantifier removes the choice.

    Works on the parse tree rather than the text, so any spelling of a
    repetition whose body only matches whitespace counts: ``\\s*``,
    ``(?:\\s)*``, ``[ \\t]+``, ``(?:(?>\\s))*``, ``\\s{0,50}``. Lookarounds are
    skipped (zero-width, they split nothing), and so is a run bounded at 8 or
    fewer: that is a constant factor, not an explosion (``_ZH_IDENT_GAP``).
    """
    return _collect_whitespace_runs(_sre_parser.parse(head), [])


def _zh_terms_without_japanese_guard(text: str) -> set[str]:
    """``extract_directives``'s zh loop with ``_is_japanese_sentence_match`` lifted.

    This is the premise for the Japanese corpus: a sample only proves the guard
    is doing work if the templates *would* have matched it without one.
    """
    out: set[str] = set()
    for locale, _kind, pat in D.DIRECTIVE_PATTERNS:
        if locale != "zh":
            continue
        for m in pat.finditer(text):
            term = D._trim_term(m.group(1))
            if 2 <= len(term) <= 40:
                out.add(term)
    return out


# ── 1. Structure: simplified characters require traditional twins wherever used ──
# Automatic discovery, not a checklist: adding a zh template without traditional forms fails here.
# Include only characters actually used by this module's zh templates.
SIMPLIFIED_TO_TRADITIONAL = {
    "别": "別", "说": "說", "讲": "講", "谈": "談", "讨": "討", "论": "論",
    "这": "這", "个": "個", "话": "話", "题": "題", "关": "關", "于": "於",
    "愿": "願", "懒": "懶", "没": "沒", "许": "許", "称": "稱", "为": "為",
    # Deliberately exclude 准 -> 準: traditional prohibition is already ``不准``, whereas ``不準``
    # means inaccurate; including it would classify "測量不準說明有問題" as ban_topic.
}


@pytest.mark.parametrize("simplified,traditional", sorted(SIMPLIFIED_TO_TRADITIONAL.items()))
def test_every_simplified_glyph_in_zh_templates_has_its_traditional_twin(
    simplified, traditional,
):
    """Whichever zh template uses a Simplified glyph must also carry the Traditional one."""
    seen = False
    for raw in _zh_pattern_sources():
        if simplified not in raw:
            continue
        seen = True
        assert traditional in raw, (
            f"zh 模板里有简体 {simplified!r} 却没有繁体 {traditional!r}：{raw!r}"
        )
    assert seen, f"{simplified!r} 已不在任何 zh 模板里，请从对照表删掉这一行"


def _script_twin(branch: str) -> str:
    """Replace each simplified character in a branch with its traditional twin."""  # noqa: DOCSTRING_CJK
    return "".join(SIMPLIFIED_TO_TRADITIONAL.get(ch, ch) for ch in branch)


def _alternation_groups(pattern: str) -> list[list[str]]:
    """Extract alternatives from every ``(?:a|b|c)`` / ``(?>a|b|c)`` group in a pattern.

    Include atomic groups ``(?>``: verb groups are atomic, so reading only ``(?:``
    entirely misses the template's most important parity dimension.
    """  # noqa: DOCSTRING_CJK
    groups = []
    for match in re.finditer(r"\(\?[:>]([^()\[\]]*?)\)", pattern):
        body = match.group(1)
        if "|" in body:
            groups.append(body.split("|"))
    return groups


def test_the_alternation_group_reader_finds_the_verb_groups():
    """Premise guard: unreadable alternative groups make branch-level parity checks below vacuous."""  # noqa: DOCSTRING_CJK
    groups = [g for raw in _zh_pattern_sources() for g in _alternation_groups(raw)]
    assert groups, "一个分支组都没读出来"
    flat = {b for g in groups for b in g}
    # Choose one branch unique to each of the four templates.
    for expected in ("提了", "不愿意", "懒得", "这话题"):
        assert expected in flat, expected


@pytest.mark.parametrize("template_no", range(1, 5))
def test_every_branch_has_its_script_twin_in_the_same_group(template_no):
    """Check parity **per branch**, not merely code-point presence in the whole pattern.

    Other branches containing the same traditional character can mask deletion of one traditional branch: template 3's
    ``(?:不想|不愿意|不願意|不愿|不願|…)`` has two 願 branches shielding each other. Deleting
    ``|不願意`` prevents persistence of 我不願意聊工作 while code-point assertions remain green.

    Check both directions: retaining traditional while deleting simplified must fail too, harming simplified users despite
    this PR's focus. Deleting template 2's |讲 while keeping |講 once lacked coverage.
    """  # noqa: DOCSTRING_CJK
    raw = _zh_pattern_sources()[template_no - 1]
    trad_to_simp = {v: k for k, v in SIMPLIFIED_TO_TRADITIONAL.items()}
    missing = []
    for group in _alternation_groups(raw):
        branches = set(group)
        for branch in group:
            twin = _script_twin(branch)
            if twin != branch and twin not in branches:
                missing.append((branch, twin))
            back = "".join(trad_to_simp.get(ch, ch) for ch in branch)
            if back != branch and back not in branches:
                missing.append((branch, back))
    assert not missing, f"模板 {template_no} 的这些分支缺孪生字形：{missing}"


# ── 2. Recall: extract terms from traditional imperatives ──
# Build a Cartesian product of negation × verb × object rather than hand-copying sentences.
TW_NEGATIONS = ("別", "不要", "不許", "莫", "甭", "休")
CN_NEGATIONS = ("别", "不要", "不许", "莫", "甭", "休")
TW_OBJECTS = ("工作", "前男友", "我的體重")
CN_OBJECTS = ("工作", "前男友", "我的体重")

# **Derive verbs from module constants**, never hand-copy. The handwritten version stated compounds must precede
# single-character prefixes, yet omitted 提到 / 聊起 / 講起 / 扯到 and the whole X及 family,
# missing 28 of 54 cases. Derivation automatically includes additions to _ZH_SAY_VERBS /
# _ZH_SAY_COMPOUNDS in the product.
#
# Removed the speech-verb × result-complement dimension; see _ZH_SAY_VERBS above:
# ``提／到达时间`` and ``提到／达时间`` cannot be distinguished locally; consuming complements also consumes topic initials.
ALL_VERBS = tuple(list(D._ZH_SAY_COMPOUNDS) + list(D._ZH_SAY_VERBS))
TW_VERBS = tuple(v for v in ALL_VERBS if not any(ch in "说讲谈论" for ch in v))
CN_VERBS = tuple(v for v in ALL_VERBS if not any(ch in "說講談論" for ch in v))

# Sole deliberate exclusion: ``休`` + verbs starting with ``講``. Japanese ``休講`` means canceled classes,
# unused in Chinese, and ``休``'s initial-position rule cannot block sentence-initial occurrences. See _ZH_XIU's ``(?!講)``.
def _is_excluded_pair(negation: str, verb: str) -> bool:
    return negation == "休" and verb.startswith("講")


# Derived products have an inherent blind spot: deriving tests from constants means **changing constants changes tests too**.
# Removing 谈论/談論 from _ZH_SAY_COMPOUNDS shrinks the product and stays green. Pin all three
# word-formation dimensions independently with equality assertions as closed sets, requiring corresponding edits here.
def test_verb_constants_are_pinned():
    assert D._ZH_SAY_VERBS == ("说", "說", "提", "聊", "讲", "講", "谈", "談", "扯")
    assert D._ZH_SAY_COMPOUNDS == ("讨论", "討論")
    assert not hasattr(D, "_ZH_VERB_COMPLEMENTS")
    assert D._ZH_ADDRESS_VERBS == (
        "管我叫", "称呼我为?", "稱呼我為?", "喊我", "叫我",
    )


@pytest.mark.parametrize(
    ("tw", "cn", "expected"),
    [
        ("不要談論政治。", "不要谈论政治。", ("論政治", "论政治")),
        ("別談論我的家人。", "别谈论我的家人。", ("論我的家人", "论我的家人")),
        ("我不想談論政治。", "我不想谈论政治。", ("論政治", "论政治")),
    ],
)
def test_tanlun_keeps_lun_in_the_term_in_both_scripts(tw, cn, expected):
    """Previously asserted 不要谈论政治。 yields 政治, treating 谈论 as a compound verb.
    Removed: identical splitting truncates 别谈论语考试。 to 语考试 and loses 别谈论语。
    entirely (codex P2). 論政治 adds a character but preserves the topic for the model; 语考试 is a nonword.
    Both scripts must behave identically; this test now protects script parity rather than compound verbs.
    """  # noqa: DOCSTRING_CJK
    tw_expected, cn_expected = expected
    assert _zh_terms(tw) == {tw_expected}
    assert _zh_terms(cn) == {cn_expected}


@pytest.mark.parametrize("negation", TW_NEGATIONS + CN_NEGATIONS)
def test_negation_word_is_actually_in_the_template(negation):
    """Premise guard: the cartesian product below is meaningless if the literal
    it is built from never made it into the pattern."""
    assert any(negation in raw for raw in _zh_pattern_sources()), (
        f"否定词 {negation!r} 不在任何 zh 模板里"
    )


@pytest.mark.parametrize("verb", TW_VERBS + CN_VERBS)
def test_verb_is_actually_in_the_template(verb):
    assert any(verb in raw for raw in _zh_pattern_sources()), (
        f"动词 {verb!r} 不在任何 zh 模板里"
    )


@pytest.mark.parametrize("obj", TW_OBJECTS)
@pytest.mark.parametrize("verb", TW_VERBS)
@pytest.mark.parametrize("negation", TW_NEGATIONS)
def test_traditional_imperative_extracts_the_object(negation, verb, obj):
    if _is_excluded_pair(negation, verb):
        pytest.skip(f"{negation}{verb} 是刻意排除的组合（日文 休講）")
    # Assert **equality**, not containment: containment accepts "起工作" / "到我前女友" after
    # single-character prefixes consume compound verbs, exactly why 28 missing cases escaped two rounds.
    assert _zh_terms(f"{negation}{verb}{obj}。") == {obj}, (
        f"繁中 {negation}{verb}{obj} 抽不到干净的 term"
    )


@pytest.mark.parametrize("obj", CN_OBJECTS)
@pytest.mark.parametrize("verb", CN_VERBS)
@pytest.mark.parametrize("negation", CN_NEGATIONS)
def test_simplified_imperative_still_extracts_the_object(negation, verb, obj):
    """The merge must not cost the Simplified side anything."""
    if _is_excluded_pair(negation, verb):
        pytest.skip(f"{negation}{verb} 是刻意排除的组合（日文 休講）")
    assert _zh_terms(f"{negation}{verb}{obj}。") == {obj}


# Representative sentences for all four zh templates, paired across scripts; the product above exercises only template 1.
TEMPLATE_PAIRS = [
    # (traditional sentence, simplified sentence, expected term variants)
    ("別再提小明了", "别再提小明了", ("小明", "小明")),
    ("不要再說工作的事了！", "不要再说工作的事了！", ("工作的事", "工作的事")),
    ("這件事別提了", "这件事别提了", ("這件事", "这件事")),
    # Pin all four filler branches of template 2; losing any makes the term consume filler too.
    ("工作這個別提了", "工作这个别提了", ("工作", "工作")),
    ("工作這事別提了", "工作这事别提了", ("工作", "工作")),
    ("工作這話題別提了", "工作这话题别提了", ("工作", "工作")),
    ("工作這件事別提了", "工作这件事别提了", ("工作", "工作")),
    # ``的事`` belongs to the topic, consistently across all four templates (see
    # test_no_zh_template_consumes_deshi_after_the_topic）
    ("我不想聊昨天發生的事", "我不想聊昨天发生的事", ("昨天發生的事", "昨天发生的事")),
    ("我不願再討論這件事", "我不愿再讨论这件事", ("這件事", "这件事")),
    ("懶得聊減肥", "懒得聊减肥", ("減肥", "减肥")),
    ("沒心情聊工作", "没心情聊工作", ("工作", "工作")),
    ("關於股票就別再講了", "关于股票就别再讲了", ("股票", "股票")),
    ("別叫我小胖", "别叫我小胖", ("小胖", "小胖")),
    ("別稱呼我為老師", "别称呼我为老师", ("老師", "老师")),
    ("以後別提前男友", "以后别提前男友", ("前男友", "前男友")),
    ("千萬別提我前女友", "千万别提我前女友", ("我前女友", "我前女友")),
    ("拜託別聊工作", "拜托别聊工作", ("工作", "工作")),
]


@pytest.mark.parametrize("tw,cn,expected", TEMPLATE_PAIRS)
def test_traditional_and_simplified_reach_the_same_term(tw, cn, expected):
    """Parity: both script forms of one sentence extract corresponding terms; breaking one reveals the mismatch."""  # noqa: DOCSTRING_CJK
    tw_expected, cn_expected = expected
    assert tw_expected in _zh_terms(tw), f"繁体 {tw!r} 抽不到 {tw_expected!r}"
    assert cn_expected in _zh_terms(cn), f"简体 {cn!r} 抽不到 {cn_expected!r}"


# ── 3. Avoid Japanese collisions ──
# Samples blocked only by _is_japanese_sentence_match: removing the guard truly makes every sample yield a term through zh
# templates, verified by premise assertions below. This prevents the table silently degenerating into irrelevant
# sentences while staying green. ``特別講演について`` / ``今日は休講です`` are blocked earlier by _BIE_COMPOUND_LEFT /
# 休's initial-position rule and tested separately in JAPANESE_BLOCKED_ELSEWHERE.
# Use only prefixes ``地域別 / 年齢別 / 職種別`` with productive Japanese 〜別 suffixes,
# not ``個別 / 特別``: their preceding characters already belong to _BIE_COMPOUND_LEFT and guard 1 blocks them,
# leaving the Japanese guard untested (premise tests would fail).
JAPANESE_KANA_GUARDED = [
    "地域別提案をお願いします。",
    "地域別講座の一覧。",
    "年齢別講座の案内です。",
    "職種別談話会のお知らせ",
    "地域別談話をお願いします。",
    "年齢別講座に申し込みました。",
    # Complete the closed particle set's previous omissions (codex P2): listing only のにをはがでと misses these cases.
    "地域別提案ください。",
    "地域別講座へ申込。",
    "年齢別提案から選択。",
    "地域別提案など検討。",
    "職種別講座まで案内。",
    "地域別談話でも可。",
    "年齢別提案だけ確認。",
    "地域別講座について質問。",
    # Conjunctive particles: these terms contain **no** single-character case particles; only けど / たら block them.
    "職種別提案したけど。",
    "地域別提案したら連絡。",
    # Colloquial copulas / final particles (codex P2).
    "地域別講座だね。",
    "世代別講座だよ。",
    "部門別提案かな。",
    "地域別提案だっけ。",
    "職種別講座でしょ。",
    "地域別提案かも。",
    # Predicate forms: past / obligation / passive / progressive, etc. (codex P2).
    "地域別講座だった。",
    "世代別提案だって。",
    "商品別提案すべき。",
    "地域別提案される。",
    "世代別講座している。",
    # Japanese kanji formerly mistaken for Chinese evidence: 没 (没収) / 称 (名称) are standard Japanese
    # forms, not simplified-Chinese-only variants of 沒 / 稱 (codex P2).
    "地域別提案で没になりました。",
    "地域別講座の名称を確認します。",
    "地域別提案の名称です。",
    "地域別講座は没収された。",
]
# Kana-leading 〜別 forms: terms have no particles (``スレ`` / ``案書``), beyond (2b)'s reach;
# only the criterion that Kana immediately precedes the matched span blocks them (adversarial investigation).
JAPANESE_KANA_PREFIXED = [
    "ジャンル別討論スレ",
    "カテゴリ別提案書。",
    "テーマ別討論スレッド。",
    "メーカー別提案資料",
    "タイプ別提案書。",
]
JAPANESE_BLOCKED_ELSEWHERE = [
    "今日は休講です。",
    # These two target branch-by-branch Chinese-evidence scanning: adding ``|題`` to the evidence regex or
    # Japanese kanji ``俺`` to the subject allowlist surfaces here, as mutation testing showed.
    "話題別提案について検討します。",
    "俺別提案をお願いします。",
    "俺別講座のご案内。",
    # Classification labels before 〜別 need not end in Kanji/Kana: Latin letters / half/fullwidth digits /
    # closing brackets can precede it too (codex P2).
    "A別提案をお願いします。",
    "タイプ2別提案をお願いします。",
    "「地域」別提案をお願いします。",
    "Ａ別提案をお願いします。",
    "（地域）別提案をお願いします。",
    "《地域》別提案をお願いします。",
    "特別講演について話しましょう。",
    "特別提供の商品です。",
    "特別講座に申し込んだ。",
    "特別談話を発表した。",
    "個別に提案します。",
    "部門別の説明会に出ます。",
    # Pure Kanji, no Kana: the Kana guard cannot help; only including
    # ``個`` in _BIE_COMPOUND_LEFT blocks it (codex P2).
    "個別提案書。",
    "個別提案資料。",
    "個別講座案内。",
    # Sentence-initial 休講 escapes 休's initial-position rule and needs _ZH_XIU's (?!講) (adversarial investigation).
    "休講だそうです。",
    "休講のお知らせ",
    "休講だって。",
    "休講情報。",
    "休講案内。",
    "休講、残念。",
]


@pytest.mark.parametrize("text", JAPANESE_KANA_GUARDED)
def test_the_japanese_guard_is_what_stops_this_sample(text):
    """Premise: lift the guard and the sample really does get extracted.

    Without this the corpus below could silently degrade into sentences the
    templates never matched in the first place, and stay green.
    """
    assert _zh_terms_without_japanese_guard(text), (
        f"{text!r} 没有日文守卫也不会命中，这条样本证明不了守卫在干活"
    )


@pytest.mark.parametrize(
    "text", JAPANESE_KANA_GUARDED + JAPANESE_KANA_PREFIXED + JAPANESE_BLOCKED_ELSEWHERE,
)
def test_japanese_text_is_not_extracted_by_the_zh_templates(text):
    assert _zh_terms(text) == set(), f"日文 {text!r} 被 zh 模板抓成 ban_topic"


# Known residual: **deliberately assert current incorrect behavior**. Productive Japanese 〜別 suffixes (地域別 /
# 年齢別 / 世代別 / 商品別…) accept arbitrary nouns, an open set beyond all three guards:
# _BIE_COMPOUND_LEFT includes only characters with zero counterexamples; left-Kana checks require Kana before 別 (here
# Kanji), while particle checks require particles in terms (here pure Katakana nouns).
#
# The only proposed fix combines terms beginning with the second character of the verb's compound plus Katakana
# (講座->座, 提案->案), but measurements also reject ``你別提初音ミク。``. The boundary depends on
# whether the Kanji before 別 is a noun, yet both 世代 and 你 are Kanji. Tradeoff:
# Japanese gets a junk term expiring in three days; traditional Chinese loses persisted instructions. Choose not to fix.
#
# Assert current output rather than emptiness so a future valid criterion can turn this directly into
# a regression test by changing the expectation to == set().
KNOWN_JAPANESE_RESIDUALS = [
    # Japanese noun/adjectival-noun suffixes (済み / 向け / 付き / 込み / っぽい…) form an
    # **open set**; adding them individually is endless whack-a-mole. Like the Kana-free case below, these combine 別 +
    # shared verb + Japanese compound, locally indistinguishable by rules. Document the residual honestly (codex P2).
    ("別提案済み。", {"案済み"}),
    # Pure-Kana titles + traditional bare ``別提``: single-character particles (のにをはがでとへ) have no boundaries,
    # so title-internal に / が rejects whole instructions. Adding a Kanji-stem left boundary
    # still failed ``にじさんじ`` (only rescuing ``ありがとう``), while failing
    # ten structural guards by altering the Japanese guard's central character class. Partial benefit with maximal risk: reverted.
    ("別提にじさんじ。", set()),
    ("地域別講座向け。", {"座向け"}),
    ("別提案っぽい。", {"案っぽい"}),
    # Only this **Kana-free** case remains. Japanese 別 + shared verbs + pure-Kanji compounds
    # (別提案書 / 別講座資料) are locally identical to Chinese 別提 + Kanji topic (別提工作),
    # impossible to separate by rules without rejecting ``別提工作。`` (measured).
    ("地域別提案書。", {"案書"}),
]
# Three Kana-tailed cases are **no longer residual**: 〜別 + compound noun + bare Kana noun
# is blocked by non-clause-boundary left context plus terms consisting of one Kanji followed by Kana (codex P2).
# Retain here as regression tests: once residual, they must fail immediately if behavior regresses.
FIXED_JAPANESE_KANA_TAILS = [
    "世代別講座ガイド。", "商品別提案プラン。", "部門別提案リスト",
    "地域別提案スレ", "A別講座スレ", "2別講座メモ", "β別提案スレ",
    "世代別提案まとめ", "商品別談話スレ",
]


@pytest.mark.parametrize("text", FIXED_JAPANESE_KANA_TAILS)
def test_kana_tailed_bessu_labels_are_suppressed(text):
    assert _zh_terms(text) == set(), text


def test_the_kana_tail_guard_needs_a_left_label():
    """String-initial 別提案スレ still escapes; record the residual honestly.

    〜別 is a **suffix**, so the left-boundary criterion requires a preceding label. Removing it would
    block string-initial cases too, but also reject Chinese one-Kanji + Kana topics such as 別提蘭ちゃん。
    Choose the missed classification for this case.
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms("別提案スレ") == {"案スレ"}
    assert "蘭ちゃん" in _zh_terms("別提蘭ちゃん。")


@pytest.mark.parametrize(("text", "current"), KNOWN_JAPANESE_RESIDUALS)
def test_known_japanese_residual_is_documented_not_forgotten(text, current):
    assert _zh_terms(text) == current, (
        f"{text!r} 的行为变了。变好了（== set()）就把这条从残留清单挪走；"
        f"变成别的样子说明有回归。"
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("別提初音ミク。", "初音ミク"),
        ("你別提初音ミク。", "初音ミク"),
        ("别再提初音ミク", "初音ミク"),
    ],
)
def test_the_fix_that_would_close_that_residual_must_not_break_these(text, expected):
    """The proposed fix for that residual rejects these cases too; run them first when attempting a real fix."""  # noqa: DOCSTRING_CJK
    assert expected in _zh_terms(text)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("仕事のことはもう言わないで", "仕事"),
        ("この前の話はもう言わないで", "この前"),
        ("あの人のことは言わないで", "あの人"),
    ],
)
def test_japanese_ban_topic_still_works(text, expected):
    """The guard is zh-scoped: a ja match is Japanese *by construction*, so
    running the "is this Japanese?" test on it can only ever throw it away."""
    hits = extract_directives(text)
    assert expected in {term for locale, _kind, term in hits if locale == "ja"}, hits


def test_chinese_directive_survives_a_stray_kana_elsewhere_in_the_message():
    """The guard looks at the matched span, not the whole message — a Chinese
    sentence that merely mentions something Japanese keeps its directive."""
    assert "工作" in _zh_terms("剛看完 ドラえもん。別提工作。")


# Reverse pitfall: the banned **object itself** is often a Japanese proper noun. Sentence structure is Chinese,
# with Kana only in the topic name; dropping matches containing Kana loses explicit user preferences (codex P2).
# This product's users especially often phrase requests this way ("別叫我お兄ちゃん").
CHINESE_WITH_JAPANESE_TOPIC = [
    ("别再提ドラえもん。", "ドラえもん"),            # Simplified trigger uses code points absent from Japanese.
    ("別再提ドラえもん。", "ドラえもん"),            # Traditional trigger + shared verb: rely on particle-free terms.
    ("別叫我お兄ちゃん。", "お兄ちゃん"),            # 叫我 is a combination absent from Japanese.
    ("别叫我お兄ちゃん", "お兄ちゃん"),              # No sentence-final punctuation.
    ("不想聊ドラえもん。", "ドラえもん"),            # 不想 provides Chinese evidence.
    ("不要聊ドラえもん。", "ドラえもん"),            # 不要 is itself Japanese; passes because term contains no particles.
    ("别再提君の名は。", "君の名は"),                # Title contains particles, requiring Chinese evidence to rescue the match.
    ("我不想聊初音ミク", "初音ミク"),
    # Bare だ cannot enter Japanese particle tables: real song titles contain it (だんご三兄弟). Use a **traditional**
    # trigger here; simplified 别 provides Chinese evidence and short-circuits before particle checks, leaving this dimension untested.
    ("別提だんご三兄弟。", "だんご三兄弟"),
    ("别再提だんご三兄弟。", "だんご三兄弟"),
]


@pytest.mark.parametrize(("text", "expected"), CHINESE_WITH_JAPANESE_TOPIC)
def test_chinese_directive_about_a_japanese_topic_is_kept(text, expected):
    assert expected in _zh_terms(text), f"{text!r} 的 ban 对象被日文守卫误丢"


@pytest.mark.parametrize(("text", "expected"), CHINESE_WITH_JAPANESE_TOPIC)
def test_those_samples_really_do_go_through_the_guard(text, expected):
    """Premise: these samples' **matched spans** really contain Kana and exercise guard criteria,
    rather than passing accidentally because no Kana exists.

    Apply criteria to matched spans and terms, never whole messages: the guard receives
    ``m.group(0)`` and ``term``, making Kana elsewhere irrelevant.
    剛看完 ドラえもん。別提工作。 contains Kana in the whole sentence but none in the matched span;
    whole-message assertions would claim the guard allowed it when the guard was never exercised.
    """  # noqa: DOCSTRING_CJK
    assert D._KANA_RE.search(expected), f"{expected!r} 里没有假名，这条样本不吃守卫"
    spans = [
        m.group(0)
        for locale, _kind, pat in D.DIRECTIVE_PATTERNS
        if locale == "zh"
        for m in pat.finditer(text)
    ]
    assert spans, f"{text!r} 没有 zh 命中，证明不了任何东西"
    assert any(D._KANA_RE.search(span) for span in spans), (
        f"{text!r} 的命中区间里没有假名：{spans}"
    )


# Pair every Chinese-evidence token with a payload sample: when topic names contain particles, only that token can
# rescue the whole match. Particle-free terms exercise another criterion, not this dimension.
ZH_EVIDENCE_LOAD_BEARING = [
    ("叫我", "別叫我ハルヒの妹。", "ハルヒの妹"),
    ("喊我", "別喊我ハルヒの妹。", "ハルヒの妹"),
    ("管我叫", "別管我叫ハルヒの妹。", "ハルヒの妹"),
    ("不想", "不想聊君の名は。", "君の名は"),
    ("懶得", "懶得聊君の名は。", "君の名は"),
    ("不願", "我不願聊君の名は。", "君の名は"),
    ("别", "别再提君の名は。", "君の名は"),
    ("說", "別說君の名は。", "君の名は"),
]


@pytest.mark.parametrize(("token", "text", "expected"), ZH_EVIDENCE_LOAD_BEARING)
def test_each_zh_evidence_token_is_load_bearing(token, text, expected):
    assert token in D._ZH_EVIDENCE_RE.pattern, f"{token!r} 已不在中文证据表里"
    assert D._JA_GRAMMAR_RE.search(expected), (
        f"{expected!r} 不含日文助词，这条样本走的是另一条判据，压不住中文证据这一维"
    )
    assert expected in _zh_terms(text), f"{text!r} 少了 {token!r} 这条证据就会被误丢"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("甭提あの日の記憶。", "あの日の記憶"),
        ("甭再提ドラえもん了。", "ドラえもん"),
    ],
)
def test_beng_counts_as_chinese_evidence(text, expected):
    """甭 is simplified-Chinese-only, absent from Japanese Kanji, so sentences containing it cannot be Japanese.
    Without it in the evidence table, 甭提 + Japanese proper name is suppressed entirely, unlike
    structurally identical 别提 + Japanese proper name, causing within-template asymmetry (adversarial investigation)."""  # noqa: DOCSTRING_CJK
    assert "甭" in D._ZH_EVIDENCE_RE.pattern
    assert expected in _zh_terms(text)


def _split_top_level_alternatives(pattern: str) -> list[str]:
    """Split top-level alternatives on | without entering (...) / [...] groups."""  # noqa: DOCSTRING_CJK
    parts, depth, in_class, buf, escaped = [], 0, False, [], False
    for ch in pattern:
        if escaped:
            buf.append(ch)
            escaped = False
            continue
        if ch == "\\":
            buf.append(ch)
            escaped = True
            continue
        if in_class:
            buf.append(ch)
            if ch == "]":
                in_class = False
            continue
        if ch == "[":
            in_class = True
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "|" and depth == 0:
            parts.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    parts.append("".join(buf))
    return parts


def test_the_alternative_splitter_does_not_cut_inside_groups():
    """Premise guard: verify the splitter itself, or scanning below becomes vacuous."""  # noqa: DOCSTRING_CJK
    assert _split_top_level_alternatives("a|b") == ["a", "b"]
    assert _split_top_level_alternatives("[a|b]|c") == ["[a|b]", "c"]
    assert _split_top_level_alternatives("(?:a|b)|c") == ["(?:a|b)", "c"]
    assert _split_top_level_alternatives(chr(92) + chr(124)) == [chr(92) + chr(124)]


def test_no_zh_evidence_alternative_matches_the_japanese_corpus():
    """Automatic discovery rather than character audits: **every Chinese-evidence alternative** must miss Japanese corpora.
    The table short-circuits the Japanese guard; any hit disables it entirely.
    没 (没収) and 称 (名称) slipped through this way.

    Test **every top-level regex alternative**, not just the first character class. Scanning only that class
    hides later evidence, whether another class or bare |題. Experimentally, appending
    |題 persisted 話題別提案について検討します。 as a Chinese instruction,
    while string-comparison assertions remained unchanged and green.

    This grows with Japanese corpora: new sentences automatically expose incorrectly admitted alternatives.
    """  # noqa: DOCSTRING_CJK
    corpus = (
        JAPANESE_KANA_GUARDED + JAPANESE_KANA_PREFIXED + JAPANESE_BLOCKED_ELSEWHERE
    )
    offenders = []
    for alt in _split_top_level_alternatives(D._ZH_EVIDENCE_RE.pattern):
        probe = re.compile(alt)
        for sentence in corpus:
            hit = probe.search(sentence)
            if hit:
                offenders.append((alt, sentence, hit.group(0)))
    assert not offenders, (
        f"这些中文证据分支在日文语料里命中了：{offenders}"
    )


def test_zh_evidence_charclass_is_pinned():
    """Assert equality for closed sets: each character claims that Japanese lacks this form. Before adding characters, verify
    Japanese shinjitai (别->別, 说->説, 关->関, 为->為…)."""  # noqa: DOCSTRING_CJK
    # Split into trigger characters + Chinese-only sentence-final particles, asserting equality separately. The latter is new:
    # when topics contain Japanese particles (``別提君の名は吧。``), uncaptured ``吧`` is the only
    # evidence proving a Chinese instruction (codex P2).
    assert D._ZH_EVIDENCE_CHARS == (
        "别说讲谈讨论关这话题愿懒许为甭說這關沒稱" + D._ZH_ZH_ONLY_FINAL_PARTICLES
    )
    assert D._ZH_ZH_ONLY_FINAL_PARTICLES == "啊呀嘛哦呗吧啦呢喔唷齁欸誒咧喲囉啰"
    assert D._ZH_EVIDENCE_WORDS == (
        "叫我", "喊我", "管我叫", "不想", "懶得", "不願", "没心情",
        "称呼我", "稱呼我",
    )
    # Verb + 了 entries are **derived**, so assert the derivation relationship instead of copying literals;
    # copying requires changing tests with the verb table, merely repeating implementation (the same
    # pitfall as derived-product tests). Pin literals separately below.
    assert D._ZH_VERB_LE_EVIDENCE == tuple(
        v + "了" for v in dict.fromkeys(
            D._ZH_SAY_COMPOUNDS + D._ZH_SAY_VERBS + D._ZH_PREPOSED_SAY_VERBS
        )
    )
    assert set(D._ZH_VERB_LE_EVIDENCE) == {
        "讨论了", "討論了", "说了", "說了", "提了", "聊了", "讲了", "講了",
        "谈了", "談了", "扯了", "提起了", "提及了",
    }
    # The character class must be the regex's first alternative. Scanning above does not depend on this, but
    # pinning it makes newly inserted preceding branches visible during review.
    first = _split_top_level_alternatives(D._ZH_EVIDENCE_RE.pattern)[0]
    assert first == f"[{D._ZH_EVIDENCE_CHARS}]"


def test_the_grammar_marker_set_excludes_mo():
    """も is a particle but occurs in ドラえもん. Adding it to markers rejects the Chinese-sentence/Japanese-topic
    cases above, a reverse pitfall pinned here."""  # noqa: DOCSTRING_CJK
    assert not D._JA_GRAMMAR_RE.search("ドラえもん"), (
        "助词表把 ドラえもん 判成了日文句子（多半是收了 も）"
    )
    assert not D._JA_GRAMMAR_RE.search("お兄ちゃん")


# ── 4. Compound-word left-boundary guard ──
def test_compound_left_set_is_pinned():
    """Assert closed-set equality: each character claims following 别 is never imperative. Before additions, rule out
    natural counterexamples. 个/個 became safe only after narrowing the guard to template 1;
    工作这个别提了 uses template 2 and remains unaffected."""  # noqa: DOCSTRING_CJK
    assert D._BIE_COMPOUND_LEFT == "特性区區级級个個"


@pytest.mark.parametrize("verb", ("说", "說", "提", "讲", "講", "谈", "談"))
@pytest.mark.parametrize("left", tuple("特性区區级級"))
def test_compound_noun_is_not_read_as_an_imperative(left, verb):
    """他特别提到 / 級別提升: 别 ends a compound rather than meaning do not say."""  # noqa: DOCSTRING_CJK
    bie = "別" if left in "區級" else "别"
    text = f"他{left}{bie}{verb}到你的名字。"
    assert _zh_terms(text) == set(), f"{text!r} 被误抽成 ban_topic"


@pytest.mark.parametrize(
    "text",
    [
        "他特别提到你的名字。",
        "他特別提到你的名字。",
        "老师特别讲了这道题。",
        "老師特別講了這道題。",
        "性别说明一下。",
        "性別說明一下。",
        "级别提升了。",
        "級別提升了。",
        "区别说明在文档里。",
        "區別說明在文件裡。",
    ],
)
def test_real_sentences_with_compound_bie_do_not_fire(text):
    assert _zh_terms(text) == set()


# The guard applies **only to template 1**. Templates 2/4 place captured topics before 别; a topic ending
# in guard characters (模特 / 可能性 / 等级 / 地区), applying it would swallow entire instructions (codex P2).
TOPIC_ENDING_IN_A_GUARDED_CHAR = [
    ("模特别提了。", "模特"),
    ("模特別提了。", "模特"),
    ("这种可能性别提了。", "这种可能性"),
    ("這種可能性別提了。", "這種可能性"),
    ("等级别提了。", "等级"),
    ("那个地区别提了。", "那个地区"),
    # Same for template 4.
    ("關於模特別提了", "模特"),
    ("关于模特别提了", "模特"),
    ("關於可能性別說了", "可能性"),
]


@pytest.mark.parametrize(("text", "expected"), TOPIC_ENDING_IN_A_GUARDED_CHAR)
def test_topic_ending_in_a_guarded_char_survives(text, expected):
    assert expected in _zh_terms(text), f"{text!r} 的话题被复合词守卫吃掉了"


@pytest.mark.parametrize(("text", "expected"), TOPIC_ENDING_IN_A_GUARDED_CHAR)
def test_those_topics_really_do_end_in_a_guarded_char(text, expected):
    """Premise: the topic's final character truly belongs to the guard table, or this sample cannot demonstrate guard scope."""  # noqa: DOCSTRING_CJK
    assert expected[-1] in D._BIE_COMPOUND_LEFT, expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("這個別提了。", "這個"),
        ("这个别提了。", "这个"),
        ("這部分別提了。", "這部分"),
        ("價格別提了。", "價格"),
        ("今年別提這件事了", "這件事"),
    ],
)
def test_compound_guard_did_not_eat_the_main_use_case(text, expected):
    """Deliberately exclude 个/分/格/年: natural counterexamples make tightening reject the main use case."""  # noqa: DOCSTRING_CJK
    assert expected in _zh_terms(text), f"{text!r} 期望 {expected!r}"


# ── 5. 休 denotes negation only at word starts ──
@pytest.mark.parametrize("prefix", ("退", "午", "调", "調", "补", "補", "年", "公"))
@pytest.mark.parametrize("verb", ("说", "說", "讲", "講", "提"))
def test_xiu_inside_a_compound_is_not_a_negation(prefix, verb):
    """退休讲 / 午休提前 / 調休說明: 休 is a compound's second half, not prohibitive 休提."""  # noqa: DOCSTRING_CJK
    text = f"他{prefix}休{verb}了很多年了。"
    assert _zh_terms(text) == set(), f"{text!r} 被误抽成 ban_topic"


@pytest.mark.parametrize(
    "text",
    ["午休提前结束。", "午休提前結束。", "调休说明发下来了。", "調休說明發下來了。"],
)
def test_real_sentences_with_compound_xiu_do_not_fire(text):
    assert _zh_terms(text) == set()


@pytest.mark.parametrize(
    "text",
    [
        "休提舊事。", "休提旧事。",
        # Do not use word-start rules: Python treats adjacent Han as \w, so (?<!\w) rejects all these valid
        # sentences (codex P2). Use a compound left-boundary table shaped like 别's instead.
        "你休提旧事。", "以后休提旧事。", "千万休提旧事。",
        "你休提舊事。", "以後休提舊事。",
    ],
)
def test_xiu_after_ordinary_context_is_still_a_negation(text):
    assert "旧事" in _zh_terms(text) or "舊事" in _zh_terms(text), (
        f"{text!r} 应当仍然命中：{_zh_terms(text)}"
    )


def test_xiu_compound_left_set_is_pinned():
    assert D._XIU_COMPOUND_LEFT == "退午调調补補年病公轮輪全双雙不歇罢罷特半"


# ── 5b. Taiwan's final particles must not attach to terms ──
# user_directives stores the term itself, injecting it literally into the system prompt. Attached particles
# ("工作喔") feed the model a nonexistent topic name (codex P2).
# Exclude 囉 / 啰 here: they also end 嘍囉 / 喽啰; omit them entirely like 耶 / 捏.
# See test_luoluo_is_a_word_not_a_final_particle.
TAIWANESE_FINAL_PARTICLES = (
    "喔", "唷", "齁", "欸", "誒", "咧", "喲",
)
# Rhetorical endings follow final particles ("工作了好嗎"): the regex's optional particle group accepts only one,
# leaving the rest in term for trim's loop to remove.
INTERROGATIVE_TAILS = ("好吗", "好嗎", "好不好", "可以吗", "可以嗎", "行吗", "行嗎")


@pytest.mark.parametrize("particle", TAIWANESE_FINAL_PARTICLES)
def test_taiwanese_final_particle_is_stripped_from_the_term(particle):
    assert _zh_terms(f"別再提工作{particle}") == {"工作"}


@pytest.mark.parametrize("tail", INTERROGATIVE_TAILS)
def test_interrogative_tail_is_stripped_from_the_term(tail):
    assert _zh_terms(f"別再提工作了{tail}？") == {"工作"}


@pytest.mark.parametrize("particle", TAIWANESE_FINAL_PARTICLES)
def test_particle_is_declared_in_the_regex(particle):
    assert particle in D._ZH_FINAL_PARTICLES, f"{particle} 不在 _ZH_FINAL_PARTICLES"


@pytest.mark.parametrize("particle", TAIWANESE_FINAL_PARTICLES)
def test_particle_is_also_declared_in_the_trim_table(particle):
    """Pair regex acceptance with trim removal; omitting either persists particles attached to terms."""  # noqa: DOCSTRING_CJK
    assert particle in D._TRIM_TRAIL_TOKENS_BY_LOCALE["zh"], f"{particle} 不在 zh trim 表"


@pytest.mark.parametrize("glyph", ("唄", "耶", "捏"))
def test_ambiguous_particle_glyphs_are_not_treated_as_particles(glyph):
    """Regex and trim both omit these three characters because of **tradeoff direction**, not because they lack particle usage.

    Including them cleans common phrasing (工作耶->工作) but truncates rare topics into nonwords
    (精准拿捏->精准拿, 音樂人坎耶->音樂人坎, 花の唄->花の).
    Omitting them adds one character to common phrasing while retaining full real topics the model can recognize.
    Prefer an extra character over a missing one. 唄 additionally means song in Japanese.
    """  # noqa: DOCSTRING_CJK
    assert glyph not in D._ZH_FINAL_PARTICLES
    assert glyph not in D._TRIM_TRAIL_TOKENS_BY_LOCALE["zh"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("别再提精准拿捏。", "精准拿捏"),
        ("別再提精準拿捏。", "精準拿捏"),
        ("別再提音樂人坎耶。", "音樂人坎耶"),
        ("别再提揉捏。", "揉捏"),
        ("别再提花の唄了。", "花の唄"),
    ],
)
def test_longer_topics_ending_in_an_ambiguous_glyph_survive(text, expected):
    """Minimum-length protection covers only stripping below two characters; three-plus-character topics are still truncated (codex P2)."""  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == {expected}


def test_bei_is_not_treated_as_a_particle_at_all():
    """Regex and trim both omit 唄. It is traditional 呗 but Japanese song: including it in trim
    truncates ja terms (子守唄->子守), while regex-only inclusion also truncates
    zh terms (别再提花の唄。->花の), encountered in separate rounds. 呗 is northern Chinese
    colloquial speech, not Taiwan 唄 usage, making that cost unjustified."""  # noqa: DOCSTRING_CJK
    assert "唄" not in D._ZH_FINAL_PARTICLES
    assert "唄" not in D._TRIM_TRAIL_TOKENS_BY_LOCALE["zh"]
    # Explicit tradeoff: Taiwan users genuinely writing 唄 retain one extra character, deliberately.
    assert _zh_terms("別再提工作唄") == {"工作唄"}
    # In exchange, both Japanese song-title spellings remain intact.
    assert _zh_terms("别再提花の唄。") == {"花の唄"}
    assert _zh_terms("别再提花の唄了。") == {"花の唄"}
    assert "子守唄" in {t for loc, _k, t in extract_directives(
        "子守唄のことはもう言わないで") if loc == "ja"}


def test_stacked_particles_are_all_stripped():
    assert _zh_terms("不要再說這件事了喔") == {"這件事"}


# These particles also end ordinary words (拿捏 / 坎耶 / 好咧 / 耶稣). Taiwan **does** use
# them as particles, unlike 唄, so cannot omit them entirely. Instead choose another segmentation when stripping
# as a particle makes the term too short to store (codex P2).
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("别再提拿捏。", "拿捏"),
        ("別再提拿捏。", "拿捏"),
        ("别再提坎耶。", "坎耶"),
        ("別再提坎耶。", "坎耶"),
        ("别再提好咧。", "好咧"),
        ("别再提耶稣。", "耶稣"),
        ("别再提咧嘴笑。", "咧嘴笑"),
    ],
)
def test_particle_glyph_that_is_also_a_word_ending_survives(text, expected):
    assert _zh_terms(text) == {expected}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Two-character topics ending in particles would fall below minimum and lose the instruction if stripped; retain them.
        ("别再提好咧。", {"好咧"}),
        ("別再提好咧。", {"好咧"}),
        # Control: strip particles normally on sufficiently long topics; the minimum guard does not mean never strip.
        ("别再提工作咧。", {"工作"}),
        ("別再提工作咧。", {"工作"}),
    ],
)
def test_trim_never_shortens_a_term_below_the_storable_minimum(text, expected):
    """Assert the **complete set** and actually use text.

    Originally checked only _trim_term directly without running text: broken minimum lengths would not
    fail, and even an incorrect parameter expectation (别再提工作咧。 actually yields 工作)
    stayed hidden (CodeRabbit).
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == expected
    # Directly test trim's minimum-length criterion.
    assert D._trim_term("好咧", "zh") == "好咧"
    assert D._TERM_MIN_LEN == 2


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("提了，工作别提了。", "工作"),
        ("说完了，工作别提了。", "工作"),
        ("算了，工作别提了。", "工作"),
        ("算了，工作別提了。", "工作"),
    ],
)
def test_template2_prefix_never_spans_a_sentence_boundary(text, expected):
    """Template 2 prefixes behave similarly: raising minimum to two lets lazy prefixes cross punctuation and merge
    prior-sentence tails into topics (算了，工作)."""  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == {expected}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("别提工作\n别提加班", {"加班"}),
        ("別提工作\n別提加班", {"加班"}),
        ("别提工作\r\n再说", set()),
        ("我不想聊工作\n别提加班", {"加班"}),
    ],
)
def test_object_never_spans_a_line_break(text, expected):
    """Explicitly exclude newlines in character classes. These captures originally used dot, naturally
    excluding newlines without DOTALL; negated classes lose that property and multiline terms swallow
    newlines plus **the next instruction** ("别提工作\\n别提加班" -> "工作\\n别提加班").

    Assert the **complete set**: merely iterating to check no newlines does nothing when results are empty
    (CodeRabbit).
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Template 3: optional 的事 can shorten a single-character subject below minimum (existing symmetric defect).
        ("我不想聊钱的事", "钱的事"),
        ("我不想聊錢的事", "錢的事"),
        ("我不想聊我的事", "我的事"),
        # Same for template 4.
        ("关于钱的事别提了。", "钱的事"),
        ("關於錢的事別提了。", "錢的事"),
    ],
)
def test_single_character_subject_survives_the_optional_de_shi(text, expected):
    assert expected in _zh_terms(text), f"{text!r} -> {_zh_terms(text)}"


def test_bracket_and_plain_branches_are_mutually_exclusive():
    """This is a ReDoS guard, not a style preference.

    Single-character branches also match 《, so 《a》 can match either the bracket branch as one unit or individual
    characters. Repeating that ambiguity in {2,30}? creates exponential backtracking: 别提 plus
    thirty 《a》 spans takes 1.3s on a path handling every user message (codex P1).

    Wrap the whole unit in an **atomic group**, never revisiting its chosen branch at a position. Better than excluding
    openers from single-character branches: lone " / ( remain ordinary characters for inch marks/emoticons.
    """  # noqa: DOCSTRING_CJK
    from tests.wall_clock import fastest_run

    assert D._ZH_TOPIC_CHAR.startswith("(?>"), (
        "话题单位不是原子组，括号分支与单字分支重叠 = 回溯爆炸"
    )
    for segment in ("《a》", '"a"', "(a)"):
        # Take the fastest of multiple runs: scheduling preemption can falsely fail a single sample (see tests/wall_clock.py).
        elapsed = fastest_run(
            lambda segment=segment: extract_directives("别提" + segment * 120), stop_below=1.0
        )
        assert elapsed < 1.0, f"{segment} x120 最快也要 {elapsed:.2f}s，回溯又爆了"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Paired ASCII delimiters were preserved on parent; omitting them regresses behavior (codex P2).
        ('"Everything, Everywhere"别提了。', "Everything, Everywhere"),
        ("电影(Hello, World)别提了。", "电影(Hello, World)"),
        ('别提"你好，李焕英"了。', "你好，李焕英"),
    ],
)
def test_ascii_paired_delimiters_keep_the_whole_title(text, expected):
    assert expected in _zh_terms(text), f"{text!r} -> {_zh_terms(text)}"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Unpaired ASCII delimiters must not become hard boundaries, the key advantage of atomic groups over excluding
        # openers (inch marks, :( emoticons, English apostrophes).
        ("别提这个:(", {"这个"}),
        ("别提 don't do it 了。", {"don't do it"}),
        ('别提 5" 屏幕了。', {'5" 屏幕'}),
    ],
)
def test_an_unpaired_ascii_delimiter_is_not_a_hard_boundary(text, expected):
    assert _zh_terms(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # A whole bracketed segment is **one** unit; `{2,n}` rejects standalone book titles (codex P2).
        ("《你好，李焕英》别提了。", "你好，李焕英"),
        ("「你好，李焕英」别提了。", "你好，李焕英"),
        ("《你好，李煥英》別提了。", "你好，李煥英"),
    ],
)
def test_a_standalone_quoted_title_is_a_valid_topic(text, expected):
    assert _zh_terms(text) == {expected}


def test_all_four_zh_templates_share_one_topic_char_class():
    """Independent character classes across four templates cause drift; share one constant so new templates inherit it automatically."""  # noqa: DOCSTRING_CJK
    assert r"[^，。！？；,.!?;\r\n" in D._ZH_PLAIN_CHAR
    assert D._ZH_BRACKET_PAIRS == (
        ("《", "》"), ("「", "」"), ("『", "』"), ("“", "”"), ("【", "】"),
        ("（", "）"), ("〈", "〉"), ("〔", "〕"), ("［", "］"), ("〖", "〗"),
        ('"', '"'), ("(", ")"), ("[", "]"), ("{", "}"), ("<", ">"),
    )
    # Deliberately exclude apostrophes: they occur within English words (don't / it's), making pairing meaningless.
    assert "'" not in {lo for lo, _hi in D._ZH_BRACKET_PAIRS}


def test_every_bracket_delimiter_is_also_trimmed():
    """Invariant: both ends of every bracket treated as a topic delimiter must belong to _TRIM_TRAIL.

    Omitting either persists bracketed terms (〔重要，紧急〕). Adding bracket pairs without updating trim
    fails here automatically, without relying on memory.
    """  # noqa: DOCSTRING_CJK
    missing = sorted(
        ch
        for pair in D._ZH_BRACKET_PAIRS
        for ch in pair
        if ch not in D._TRIM_TRAIL
    )
    assert not missing, f"这些括号是话题分隔符但不会被 trim 剥掉：{missing}"
    import re as _re

    for lo, hi in D._ZH_BRACKET_PAIRS:
        # ASCII delimiters are escaped in regexes, so escape them when comparing too.
        assert f"{_re.escape(lo)}(?:" in D._ZH_BRACKET_RUN, (
            f"{lo}{hi} 没进话题单位"
        )
    # Template 2's preposed topic uses NO_GUANYU (see _ZH_PLAIN_CHAR_NO_GUANYU); others use the shared variant.
    units = (D._ZH_TOPIC_CHAR, D._ZH_TOPIC_CHAR_NO_GUANYU)
    for raw in _zh_pattern_sources():
        assert any(u in raw for u in units), f"这条 zh 模板没走共用话题单位：{raw!r}"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Punctuation inside title/quotation brackets belongs to the topic, not sentence boundaries. Blanket exclusion truncates
        # terms to their latter halves ("电影《你好，李焕英》别提了。" -> "李焕英", codex P2).
        ("电影《你好，李焕英》别提了。", "电影《你好，李焕英》"),
        ("電影《你好，李煥英》別提了。", "電影《你好，李煥英》"),
        ("别提《你好，李焕英》了。", "你好，李焕英"),
        ("别提「你好，李焕英」了。", "你好，李焕英"),
        ("别提【重要，紧急】了。", "重要，紧急"),
        ("别提（重要，紧急）了。", "重要，紧急"),
        ("别提〔重要，紧急〕了。", "重要，紧急"),
    ],
)
def test_punctuation_inside_a_quoted_title_stays_in_the_topic(text, expected):
    assert expected in _zh_terms(text), f"{text!r} -> {_zh_terms(text)}"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Samples must put the **closing bracket on the next line**: without any closer, bracket branches fail
        # regardless of newline support, leaving this dimension untested.
        ("别提《书名前半\n后半》了。", set()),
        ("别提「引文前半\n后半」了。", set()),
        ("别提《没闭合的书名\n别提加班。", {"加班"}),
    ],
)
def test_a_bracket_run_must_not_cross_a_line_break(text, expected):
    """Bracket bodies permit punctuation but not newlines, or multiline title brackets swallow both lines plus
    intervening instructions into one term.

    Assert **complete sets**, never iterate results individually: empty results produce zero checks
    (CodeRabbit), this file's third occurrence of assertions weaker than claims.
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("功成名就别提了，功成名别提了。", {"功成名就", "功成名"}),
        ("别提小明，也别提小红。", {"小明", "小红"}),
        ("算了，工作别提了。", {"工作"}),
    ],
)
def test_object_never_spans_a_sentence_boundary(text, expected):
    """Raising object minimum to two lets lazy captures cross terminating punctuation for longer matches:
    功成名就别提了，功成名别提了。 once yielded 了，功成名别提. Objects need classes excluding sentence punctuation;
    topics should never cross sentences.

    Assert full sets, not iterate to confirm punctuation-free results: empty results bypass checks (the same vacuity
    CodeRabbit identified elsewhere; audited and repaired equivalent cases throughout this file).
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == expected


# Separate trim tables by locale: identical code points mean different words. 唄 is the traditional Chinese
# particle 呗, but Japanese song (子守唄 = lullaby); 了 forms Japanese 完了/
# 終了. Chinese stripping would halve Japanese words (codex P2).
JAPANESE_TERMS_ENDING_IN_A_CHINESE_PARTICLE = [
    ("終了のことはもう言わないで", "終了"),
    ("完了のことはもう言わないで", "完了"),
    # Use topics of at least three characters to exercise locale: stripping one from two-character terms violates
    # minimum length and is blocked by trim's guard, falsely appearing intact.
    ("完全終了のことはもう言わないで", "完全終了"),
]


@pytest.mark.parametrize(
    ("text", "expected"), JAPANESE_TERMS_ENDING_IN_A_CHINESE_PARTICLE,
)
def test_japanese_term_is_not_trimmed_with_chinese_particles(text, expected):
    ja_terms = {term for locale, _kind, term in extract_directives(text) if locale == "ja"}
    assert expected in ja_terms, f"{text!r} 的 ja term 被中文助词表削掉了：{ja_terms}"


@pytest.mark.parametrize(
    ("text", "expected"), JAPANESE_TERMS_ENDING_IN_A_CHINESE_PARTICLE,
)
def test_those_japanese_terms_really_end_in_a_chinese_particle(text, expected):
    """Premise: term endings truly belong to the Chinese particle table, or samples cannot prove the need for locale tables."""  # noqa: DOCSTRING_CJK
    assert expected[-1] in D._TRIM_TRAIL_TOKENS_BY_LOCALE["zh"], expected


@pytest.mark.parametrize("locale", ("zh", "ja", "ko", "en", "ru", "es", "pt"))
def test_ascii_tails_stay_global(locale):
    """ASCII endings cannot collide across language scripts, and Chinese/English mixing is common (别提 my ex please),
    so strip them for every locale."""  # noqa: DOCSTRING_CJK
    assert D._trim_term("my ex please", locale) == "my ex"


@pytest.mark.parametrize(
    ("text", "locale", "expected"),
    [
        ("stop talking about 前女友了", "en", "前女友"),
        ("don't mention 加班了", "en", "加班"),
        ("no hables de 相亲了", "es", "相亲"),
        ("не говори про 前任了", "ru", "前任"),
        ("não fale de 加班了", "pt", "加班"),
        # Mixed-language spans may themselves be Japanese, so fallback combines zh + ja rather than zh alone.
        ("stop saying 仕事ね", "en", "仕事"),
        # Include Korean in fallback too: Hangul shares no code points with Han/Kana and lacks the
        # cross-language character collisions originally requiring separate tables (codex P2).
        ("stop saying 전남친은", "en", "전남친"),
        ("don't mention 직장에", "en", "직장"),
        ("stop talking about あの人よ", "en", "あの人"),
        ("don't mention my ex 啊", "en", "my ex"),
    ],
)
def test_non_cjk_locales_fall_back_to_the_cjk_particle_lists(text, locale, expected):
    """After locale separation, en/ru/es/pt lack CJK particle tables, but mixed-language topics
    are often wholly Chinese (stop talking about 前女友了). Without fallback, 了 persists;
    stripping it predates separation, which must preserve behavior."""  # noqa: DOCSTRING_CJK
    terms = {t for loc, _k, t in extract_directives(text) if loc == locale}
    assert expected in terms, f"{text!r} -> {terms}"


# ── 5d. Do not segment verb + result complement; preserve script parity ──
# Previously asserted 別提起工作。 yields 工作 by consuming 起 as a complement. Removed:
# identical segmentation truncates 别聊起点问题。 to 点问题, also regressing simplified behavior (codex P2).
# Retaining complements in terms matches base behavior and is safer: one extra character preserves the full topic.
COMPOUND_VERB_PAIRS = [
    ("別提起工作。", "别提起工作。", "起工作"),
    ("別提及工作。", "别提及工作。", "及工作"),
    ("別講到工作。", "别讲到工作。", "到工作"),
    ("別說到工作。", "别说到工作。", "到工作"),
    ("別說起工作。", "别说起工作。", "起工作"),
    ("別談到工作。", "别谈到工作。", "到工作"),
    ("別談起工作。", "别谈起工作。", "起工作"),
    ("別聊到工作。", "别聊到工作。", "到工作"),
]


@pytest.mark.parametrize(("tw", "cn", "expected"), COMPOUND_VERB_PAIRS)
def test_verb_plus_complement_keeps_the_complement_in_both_scripts(tw, cn, expected):
    for text in (tw, cn):
        assert _zh_terms(text) == {expected}, f"{text!r} 繁简不一致"


@pytest.mark.parametrize(
    "text",
    [
        # Template 1 (别/不要 + verb).
        "别提起了。", "别提及了。", "别说起了。",
        "別提起了。", "別說到了。", "别谈及了。",
        # Pair each with template 3 (不想/懒得 + verb) too; testing only template 1 leaves deletion of template 3's
        # lookahead green while producing fake topics such as 起了 / 到了 / 及了.
        "我不想再提起了。", "我懒得再说起了。", "我不想聊到了。",
        "我不想再提及了。", "我沒心情提起了。", "我懶得再說起了。",
        # Complements are not limited to 到/起/及: Chinese result complements form an open set. Require the **count** criterion
        # that objects contain only one character. Listing just three missed every following case (codex P2 reported
        # 完; measurements also found 光/够/死/上 affected).
        "别说完了。", "別提完了。", "我不想再说完了。", "别说光了。",
        "别提够了。", "别聊死了。", "别提上了。", "别提走了。",
        # Single-character objects themselves, not complements, should likewise not extract; parent discards them by length.
        "别提钱了。", "别提A了。", "別提錢了。",
    ],
)
def test_an_objectless_directive_does_not_invent_an_object(text):
    """Only result complement + particle after a verb means no object, explicitly excluded by this module's documentation.
    Block through _ZH_OBJECTLESS_AHEAD; **never** substitute lowering object minimum to one,
    which lets lazy objects surrender topic-final characters to optional particles, turning 别提钱的事。 into
    钱 and losing the entire instruction below minimum (two codex P2 rounds, opposite directions).
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == set(), f"{text!r} 被造出了话题：{_zh_terms(text)}"


def test_the_objectless_guard_is_not_the_topic_minimum():
    """The two gates guard different ends and cannot substitute for each other; restoring minimum one fails here."""  # noqa: DOCSTRING_CJK
    assert "_ZH_OBJECTLESS_AHEAD" in D.__dict__
    # Topics now consist of first unit + remaining {min-1,max-1}, so minimum 2 becomes
    # ``{1,39}``. Derive criteria from _zh_topic itself rather than finding literal ``{1,``.
    assert D._zh_topic(2, 40).endswith("{1,39}?)"), D._zh_topic(2, 40)
    assert D._zh_topic(1, 40).endswith("{0,39}?)"), D._zh_topic(1, 40)
    for _loc, _kind, pat in D.DIRECTIVE_PATTERNS:
        if _loc == "zh":
            assert D._zh_topic(1, 40) not in pat.pattern, pat.pattern[:80]


def test_the_objectless_guard_counts_characters_not_complements():
    """Require one-character objects, never revert to enumerated complement characters, an open set."""  # noqa: DOCSTRING_CJK
    assert not hasattr(D, "_ZH_COMPLEMENT_CHARS")
    # Lookaheads must not alternate between a few hand-picked complement characters.
    assert "到|起|及" not in D._ZH_OBJECTLESS_AHEAD
    assert D._ZH_PLAIN_CHAR in D._ZH_OBJECTLESS_AHEAD


def test_the_objectless_guard_uses_only_the_parent_particle_set():
    """New Taiwan colloquial particles would classify 别再提好咧。 as 好 + 咧 and reject it,
    while parent stores 好咧. Those characters also commonly end words.
    """  # noqa: DOCSTRING_CJK
    assert D._ZH_BASE_FINAL_PARTICLES in D._ZH_OBJECTLESS_AHEAD
    for tw_only in TAIWANESE_FINAL_PARTICLES:
        assert tw_only not in D._ZH_OBJECTLESS_AHEAD, tw_only
        assert tw_only in D._ZH_FINAL_PARTICLES, tw_only
    # Behavior: pin one example in each direction.
    assert _zh_terms("别再提好咧。") == {"好咧"}
    assert _zh_terms("别再提工作喔。") == {"工作"}


def test_the_verb_alternation_is_atomic():
    assert D._ZH_VERBS_WITH_ADDRESS.startswith("(?>")
    assert D._ZH_VERBS_PLAIN.startswith("(?>")


# ── 5e. Natural 的 + demonstrative phrasing ──


# Template 2's three optional filler groups (的事 / 的+demonstrative / 就), with lazy prefixes, make regexes preferentially
# place the **topic's final character** into filler groups. Test each of three failure modes (adversarial investigation):
@pytest.mark.parametrize(
    ("text", "expected", "why"),
    [
        # (a) 就 cuts into words: template 2 consumes none, leaving _drop_filler_suffixed_terms to handle it.
        # Words ending in 就 form an **open set** (成就 / 迁就 / 功成名就 / 一蹴而就 /
        # 練就 / 鑄就…); each omission from a preceding-character denylist truncates a real topic (codex P2).
        ("他的成就别提了。", "他的成就", "就"),
        ("他的成就別提了。", "他的成就", "就"),
        ("成就别提了。", "成就", "就"),
        ("迁就别提了。", "迁就", "就"),
        ("将就别提了。", "将就", "就"),
        ("功成名就别提了。", "功成名就", "就"),
        ("功成名就別提了。", "功成名就", "就"),
        ("一蹴而就别提了。", "一蹴而就", "就"),
        ("努力练就别提了。", "努力练就", "就"),
        # (b) Optional standalone 的 cuts 目的 / 标的: prevent it by binding 的 to the demonstrative branch.
        ("目的这个别提了。", "目的", "的"),
        ("目的這個別提了。", "目的", "的"),
        ("标的这个别提了。", "标的", "的"),
        # Topics need at least three characters to exercise this: a truncated two-character topic falls below minimum,
        # prompting regex to choose a longer prefix and falsely appear intact.
        ("有目的别提了。", "有目的", "的"),
        ("有目的別提了。", "有目的", "的"),
        ("这个标的别提了。", "这个标的", "的"),
        # (c) Filler groups reduce short subjects to one character below minimum: prevent with prefix minimum 2.
        ("钱的事别提了。", "钱的事", "下限"),
        ("我的事别提了。", "我的事", "下限"),
        ("他的事别提了。", "他的事", "下限"),
        ("关于钱的事别提了。", "钱的事", "下限"),
    ],
)
def test_filler_groups_do_not_slice_the_topic(text, expected, why):
    assert _zh_terms(text) == {expected}, f"{text!r}（{why}）"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("減肥的這件事別再說了。", "減肥"),
        ("减肥的这件事别再说了。", "减肥"),
        ("搬家的這件事別提了。", "搬家"),
        ("搬家的这件事别提了。", "搬家"),
        ("關於減肥的這件事就別說了。", "減肥"),
        ("关于减肥的这件事就别说了。", "减肥"),
    ],
)
def test_possessive_before_the_demonstrative_is_consumed(text, expected):
    """Independent optional 的事 and 這件事 are insufficient: natural 減肥的這件事 separates
    的 from demonstratives, leaving dangling 的 in terms without support."""  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == {expected}


# ── 5f. 关于 within book/film titles is not a structural prefix ──
@pytest.mark.parametrize(
    "text",
    [
        "电影《关于爱》别提了。",
        "電影《關於愛》別提了。",
        "那本《关于时间的简史》别提了。",
        "那本《關於時間的簡史》別提了。",
    ],
)
def test_a_title_containing_guanyu_still_yields_its_outer_topic(text):
    """Block 关于 constructions only at the **start**: titles naturally contain 关于; rejecting entire spans
    loses whole instructions (codex P2)."""  # noqa: DOCSTRING_CJK
    terms = _zh_terms(text)
    assert terms, f"{text!r} 一条 term 都没抽到"
    assert any("关于" in t or "關於" in t for t in terms), (
        f"{text!r} 只剩书名内层：{terms}"
    )


# ── 5c. 关于 X produces only one term ──


# ── 5c-2. Postprocess filler deduplication ──
def test_filler_dedup_needs_the_shorter_term_to_actually_exist():
    """Deduplicate **terms actually extracted from the same sentence**, never guess word boundaries.

    Drop 股票就 because 股票 also appears (from template 4); retain 功成名就 because
    功成名 is never a term. Guessing filler boundaries instead gets both
    wrong, exactly what happened with the _ZH_JIU denylist.
    """  # noqa: DOCSTRING_CJK
    overlapping = [(0, 10), (0, 10)]
    assert D._drop_filler_suffixed_terms([
        ("zh", "ban_topic", "股票就"), ("zh", "ban_topic", "股票"),
    ], overlapping) == [("zh", "ban_topic", "股票")]
    assert D._drop_filler_suffixed_terms([
        ("zh", "ban_topic", "功成名就"),
    ], [(0, 10)]) == [("zh", "ban_topic", "功成名就")]
    # Fillers can stack: 前女友 + 的事 + 就.
    assert D._drop_filler_suffixed_terms([
        ("zh", "ban_topic", "前女友的事就"), ("zh", "ban_topic", "前女友"),
    ], overlapping) == [("zh", "ban_topic", "前女友")]
    # Different kinds do not interfere.
    assert len(D._drop_filler_suffixed_terms([
        ("zh", "ban_topic", "股票就"), ("zh", "other_kind", "股票"),
    ], overlapping)) == 2


def test_filler_dedup_runs_before_term_deduplication():
    """Deduplicate **after** filler filtering: filtering uses match spans to identify two segmentations of one instruction,
    while deduplication discards duplicate terms together with spans.

    In 股票别提了。关于股票就别提了。, the second 股票 shares the first's name. Deduplicating
    first loses its span, leaving only the first **nonoverlapping** span visible to filtering, so 股票就
    escapes (codex P2).
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms("股票别提了。关于股票就别提了。") == {"股票"}
    assert _zh_terms("工作别提了。关于工作就别提了。") == {"工作"}
    # Reverse case: distinct topics must not be swallowed due to this ordering.
    assert _zh_terms("股票别提了。关于加班就别提了。") == {"股票", "加班"}


def test_filler_dedup_stays_linear_on_many_directives():
    """Filler-deduplication overlap checks are O(n²), while most messages have no filler-suffixed terms.
    Filter first, then compare: pasted chat histories can contain hundreds of entries (codex P2).

    Pure performance change with no behavioral difference. Timing is unreliable (small constants and CI jitter),
    so use a **deterministic** criterion: with no filler-bearing terms, return **the identical original
    list object**, proving no pairwise comparison occurred.
    """  # noqa: DOCSTRING_CJK
    # Do **not** pin the skip-suspects-individually layer here. With bucket indexing it is only a constant factor
    # (800 entries + one filler: 24.6ms -> 74.4ms, same complexity), and pinning it requires source-text assertions
    # falsely rejecting equivalent rewrites. The timing assertion below protects complexity (CodeRabbit).
    #
    # Filtering suspects alone is insufficient: topics ending in fillers (thousands of repeated 成就别提了。)
    # make every entry suspect, and full rescanning per entry returns to O(n²), previously 1.25s for 4,000. Bucketing by start
    # limits checks to neighboring buckets because match length is bounded; timing here pins that complexity difference (codex P2).
    #
    # Limit is four seconds, not one: bucket indexing measured 0.14s locally and 1.01s on Windows CI,
    # seven times slower, falsely crossing one second. Protect **complexity differences**:
    # reverting full rescans costs 1.25s locally and about 9s at sevenfold slowdown, still rejected by four seconds.
    from tests.wall_clock import fastest_run

    elapsed = fastest_run(
        lambda: extract_directives("成就别提了。" * 4000), repeat=3, stop_below=4.0
    )
    assert elapsed < 4.0, f"4000 条以填充词结尾的话题最快也要 {elapsed:.2f}s，桶索引失效了"

    hits = [("zh", "ban_topic", f"话题{i}") for i in range(50)]
    spans = [(i * 10, i * 10 + 5) for i in range(50)]
    assert not any(
        t.endswith(f) for _l, _k, t in hits for f in D._ZH_TRAILING_FILLERS
    ), "前提：这批 term 里不该有带填充词的"
    assert D._drop_filler_suffixed_terms(hits, spans) is hits, (
        "没有走早退：即使一条填充词后缀都没有，也做了 O(n²) 的重叠比对"
    )
    # Works normally with fillers.
    assert D._drop_filler_suffixed_terms(
        [("zh", "ban_topic", "股票就"), ("zh", "ban_topic", "股票")], [(0, 9), (0, 9)],
    ) == [("zh", "ban_topic", "股票")]


def test_filler_dedup_only_touches_overlapping_matches():
    """Only two segmentations of one instruction are duplicates. Nonoverlapping spans mean independent instructions, even differing
    by one filler: 功成名就别提了，功成名别提了。 contains two (codex P2)."""  # noqa: DOCSTRING_CJK
    disjoint = [(0, 7), (8, 15)]
    assert len(D._drop_filler_suffixed_terms([
        ("zh", "ban_topic", "功成名就"), ("zh", "ban_topic", "功成名"),
    ], disjoint)) == 2
    # Without spans or with mismatched lengths, do not suppress: the safe direction.
    assert len(D._drop_filler_suffixed_terms([
        ("zh", "ban_topic", "股票就"), ("zh", "ban_topic", "股票"),
    ], None)) == 2
    assert _zh_terms("功成名就别提了，功成名别提了。") == {"功成名就", "功成名"}


def test_filler_dedup_keeps_genuinely_different_topics():
    assert _zh_terms("别提小明和小红") == {"小明和小红"}
    assert "工作" in _zh_terms("別提工作，也別提工作的事")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("關於股票就別再講了", "股票"),
        ("关于股票就别再讲了", "股票"),
        # 的事 belongs to the topic; see test_the_guanyu_template_keeps_deshi_in_the_topic.
        ("關於前女友的事就別提了", "前女友的事"),
        ("關於減肥的這件事就別說了。", "減肥"),
    ],
)
def test_guanyu_produces_exactly_one_term(text, expected):
    """Generic X + 别提 treats 关于股票就 as an entire topic, persisting it alongside dedicated-template 股票.
    The junk term also consumes an active slot and injects for three days."""  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == {expected}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("我觉得关于股票就别再讲了", "股票"),
        ("我覺得關於股票就別再講了", "股票"),
        ("其实关于工作别提了", "工作"),
    ],
)
def test_a_leading_clause_before_guanyu_still_yields_the_real_topic(text, expected):
    """Originally asserted == {expected}, suppressing extra 我觉得关于股票就; now require
    the real topic to be present instead.

    Two codex requests **conflict directly**: first temper 关于 inside topics to avoid extra
    我觉得关于股票就, then note that doing so loses 电影关于爱别提了。 and truncates
    这部关于爱的电影 (both intact on parent). Narrowing temper to the **first**
    unit restores the extra long span to parent behavior.
    Compare cost severity: an extra long term containing the real topic (G class, matching parent) is much less harmful
    than losing a real instruction (B class).
    """  # noqa: DOCSTRING_CJK
    assert expected in _zh_terms(text), _zh_terms(text)


@pytest.mark.parametrize(
    ("text", "expected"),
    [("有关工作别提了", "有关工作"), ("有關工作別提了", "有關工作")],
)
def test_guanyu_exclusion_does_not_eat_other_words_starting_with_guan(text, expected):
    """Exclude only the 关|于 split, not everything beginning 关/關."""  # noqa: DOCSTRING_CJK
    assert expected in _zh_terms(text)


# ── 6. Avoid false triggers in ordinary traditional Chinese chat ──
@pytest.mark.parametrize(
    "text",
    [
        "今天天氣真好。",
        "我們來聊聊這個專案吧",
        "剛剛那個 bug 我修好了",
        "你覺得這樣寫可以嗎",
        "我特別喜歡這首歌",
    ],
)
def test_ordinary_traditional_talk_does_not_trigger(text):
    assert _zh_terms(text) == set()


# ── 7. Do not consume topic initials 到/起/及 as verb complements ──
# All five were **measured against origin/main**: base retained initial characters that this PR once consumed
# (codex P2). 提／到达时间 and 提到／达时间 are identical strings, locally indistinguishable, so
# avoid speech-verb × result-complement products: retaining 到 preserves topics; consuming it creates nonwords.
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("别提到达时间。", "到达时间"),
        ("別提到達時間。", "到達時間"),
        ("别聊起点问题。", "起点问题"),
        ("別聊起點問題。", "起點問題"),
        ("别提及格线的事。", "及格线的事"),
        ("別提及格線的事。", "及格線的事"),
        ("别说起点站。", "起点站"),
        ("別說起點站。", "起點站"),
        ("别扯到我头上。", "到我头上"),
    ],
)
def test_a_topic_beginning_with_a_complement_character_keeps_its_first_char(
    text, expected,
):
    assert expected in _zh_terms(text)


def _verb_branches(alternation: str) -> set[str]:
    """Split ``(?>a|b|c)`` into {a, b, c}.

    Never search with f"|{x}|": the first alternative follows (?>, not |, and the last
    precedes ), not |, making both ends invisible. Adding 提到 at the table's beginning or end
    is precisely what this guard must prevent (CodeRabbit).
    """  # noqa: DOCSTRING_CJK
    body = alternation.removeprefix("(?>").removesuffix(")")
    return set(body.split("|"))


def test_the_verb_branch_splitter_sees_both_ends():
    """Premise guard: the splitter must see both first and last alternatives, or the preceding guard is vacuous."""  # noqa: DOCSTRING_CJK
    branches = _verb_branches("(?>头|中|尾)")
    assert branches == {"头", "中", "尾"}


@pytest.mark.parametrize(
    "alternation_name", ["_ZH_VERBS_PLAIN", "_ZH_VERBS_WITH_ADDRESS"],
)
def test_the_verb_table_has_no_complement_cartesian_product(alternation_name):
    """Returning complement families to verb tables consumes initial characters in the topics above."""  # noqa: DOCSTRING_CJK
    branches = _verb_branches(getattr(D, alternation_name))
    # Premise: extracted alternatives really contain known verbs, or not-in assertions below are vacuous.
    assert "提" in branches and "討論" in branches, branches
    for verb in D._ZH_SAY_VERBS:
        for complement in ("到", "起", "及", "完", "上", "光", "死", "够"):
            assert f"{verb}{complement}" not in branches, f"{verb}{complement}"


# ── 8. Strip rhetorical endings only without brackets ──
# Paired brackets are stripped before particles, so determine whether original terms contain brackets beforehand.
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # No brackets: rhetorical tone, strip it (base retained everything; this PR improves it).
        ("别再提工作好吗。", "工作"),
        ("別再提工作好嗎。", "工作"),
        ("别再提工作了好吗。", "工作"),
        ("别再提工作好不好。", "工作"),
        ("別再提工作好不好。", "工作"),
        ("别再提加班可以吗。", "加班"),
        ("別再提加班可以嗎。", "加班"),
        # If stripping leaves fewer than two characters, retain everything; trim never goes below minimum.
        ("别提行吗。", "行吗"),
        # Bracketed content is a quoted proper name, and the rhetorical phrase belongs to that name.
        ("別再提《最近你好嗎》。", "最近你好嗎"),
        ("别再提《最近你好吗》。", "最近你好吗"),
        ("别再提电影《我们好不好》。", "电影《我们好不好》"),
        ("別再提電影《我們好不好》。", "電影《我們好不好》"),
        ("别再提《你可以吗》。", "你可以吗"),
        ('别再提"我们好不好"。', "我们好不好"),
        ("別叫我《好不好》。", "好不好"),
    ],
)
def test_interrogative_tails_are_stripped_only_outside_quoted_names(text, expected):
    terms = _zh_terms(text)
    if expected is None:
        assert terms == set()
    else:
        assert expected in terms, terms


def test_the_bracket_char_set_is_derived_from_the_pairs():
    """These tables drifted four times (#2655); pin their derivation relationship here."""  # noqa: DOCSTRING_CJK
    assert D._ZH_CLOSE_FOR_OPEN == {
        lo: hi for lo, hi in D._ZH_BRACKET_PAIRS if lo != hi
    }
    assert D._ZH_SYMMETRIC_DELIMS == frozenset(
        lo for lo, hi in D._ZH_BRACKET_PAIRS if lo == hi
    )
    # Symmetric pairs do not count as unclosed openers; lone double quotes are inch marks, not unfinished quotations.
    assert '"' not in D._ZH_CLOSE_FOR_OPEN
    assert "《" in D._ZH_CLOSE_FOR_OPEN


def test_interrogative_tails_are_a_separate_table_from_the_particles():
    """Adding them to particle tables prevents treating endings differently according to brackets."""  # noqa: DOCSTRING_CJK
    assert D._TAIL_INTERROGATIVES_BY_LOCALE["zh"] == (
        "好吗", "好嗎", "好不好", "可以吗", "可以嗎", "行吗", "行嗎", "吗", "嗎",
    )
    # The zh particle table must contain only single characters, or multicharacter rhetorical endings bypass bracket criteria and strip unconditionally.
    for tok in D._TRIM_TRAIL_TOKENS_BY_LOCALE["zh"]:
        assert len(tok) == 1, tok




# ── 9. Bound bracketed spans and prevent symmetric quotes spanning sentences ──
@pytest.mark.parametrize("pair", D._ZH_BRACKET_PAIRS)
def test_bracket_bodies_are_bounded(pair):
    """Unbounded ``*`` scans to the end from every opener, yielding quadratic behavior (codex P2).

    Require **all** pairs bounded, not merely one: an `in` check passes if any of twelve pairs stays bounded,
    while unbounding only 「」 raises ``"「" * 8000`` from 0.04s to 2.5s (mutation testing).
    """  # noqa: DOCSTRING_CJK
    lo, hi = pair
    body = D._zh_bracket_body(lo, hi)
    assert f"{{0,{D._TERM_MAX_LEN}}}" in body, body
    for unbounded in ("*", "+", "{0,}"):
        assert f"){unbounded}" not in body, (body, unbounded)


@pytest.mark.parametrize("pair", D._ZH_BRACKET_PAIRS)
def test_no_bracket_pair_scans_to_the_end(pair):
    """Behavior: feeding 8,000 unmatched openers for each pair independently must remain linear.

    Ten seconds, not one: bounded behavior measures 0.14s locally and reached 7.37s on Windows CI,
    where runner stalls can be fiftyfold slower. One second falsely fails. Protect the complexity
    gap documented above: unbounding one pair raises local time from 0.04s to 2.5s
    (sixtyfold), becoming minutes on equally slow CI, still blocked by ten seconds. Exact per-pair criteria live in
    ``test_bracket_bodies_are_bounded`` (universal structural checks); this is their behavioral fallback.
    """  # noqa: DOCSTRING_CJK
    from tests.wall_clock import fastest_run

    lo, _hi = pair
    elapsed = fastest_run(lambda: extract_directives(lo * 8000), repeat=3, stop_below=10.0)
    assert elapsed < 10.0, f"{lo!r} * 8000 最快也要 {elapsed:.2f}s"


def test_unmatched_openers_stay_linear():
    from tests.wall_clock import fastest_run

    # Take the fastest of multiple runs at both input sizes: ratio criteria are sensitive to scheduling preemption; a stalled 8,000-size sample
    # can add fractions of a second beyond the +0.2 allowance.
    timings = {
        n: fastest_run(lambda n=n: extract_directives("《" * n), repeat=3) for n in (2000, 8000)
    }
    # Quadratic behavior makes fourfold input cost sixteenfold time; allow ample margin but require **far below** quadratic.
    # The ratio is authoritative and normalizes machine speed. Absolute time below is only fallback with a ten-second
    # limit: ``"《" * 8000`` once measured 7.37s under Windows CI runner stalls
    # (see test_no_bracket_pair_scans_to_the_end); one second would falsely fail there.
    assert timings[8000] < timings[2000] * 8 + 0.2, timings
    assert timings[8000] < 10.0, timings


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Two sentences each have lone inch marks; never merge them into one quotation.
        ('尺寸5"别提了。尺寸6"别提了。', {"尺寸5", "尺寸6"}),
        ('尺寸5"別提了。尺寸6"別提了。', {"尺寸5", "尺寸6"}),
        # Actual paired quotes remain intact, including commas.
        ('别提"你好，李焕英"了。', {"你好，李焕英"}),
        ('別提"你好，李煥英"了。', {"你好，李煥英"}),
        ('"Everything, Everywhere"别提了。', {"Everything, Everywhere"}),
    ],
)
def test_symmetric_ascii_quotes_do_not_span_sentences(text, expected):
    assert _zh_terms(text) == expected


def test_only_symmetric_pairs_forbid_sentence_punctuation():
    """Punctuation inside asymmetric brackets belongs to topics (《你好，李焕英》); do not tighten it too."""  # noqa: DOCSTRING_CJK
    assert _zh_terms("电影《你好，李焕英》别提了。") == {"电影《你好，李焕英》"}
    assert _zh_terms("電影《你好，李煥英》別提了。") == {"電影《你好，李煥英》"}


# ── 10. 关于 exclusion applies only to preposed topics ──
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("别再提关于公司的传闻。", "关于公司的传闻"),
        ("別再提關於公司的傳聞。", "關於公司的傳聞"),
        ("我不想聊关于钱的事", "关于钱的事"),
        ("我不想聊關於錢的事", "關於錢的事"),
    ],
)
def test_an_object_may_begin_with_guanyu(text, expected):
    """Excluding 关于 guards template 2's **preposed topics**. Moving it into shared single-character branches rejects
    verb-object structures' objects too (codex P2). Preposed topics and post-verbal objects are distinct structures.
    """  # noqa: DOCSTRING_CJK
    assert expected in _zh_terms(text), _zh_terms(text)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Real topics containing 关于 **internally** remain intact; guard only starts.
        ("这部关于爱的电影别提了。", "这部关于爱的电影"),
        ("电影关于爱别提了。", "电影关于爱"),
        ("這部關於愛的電影別提了。", "這部關於愛的電影"),
    ],
)
def test_a_topic_may_contain_guanyu_in_the_middle(text, expected):
    """The 关于 guard tempers only the **first** unit.

    Applying it to every unit truncates or loses real topics containing 关于 internally
    (codex P2). It prevents prefixes consuming sentence-initial 关于 character by character, possible only
    at the **start**.
    """  # noqa: DOCSTRING_CJK
    assert expected in _zh_terms(text), _zh_terms(text)


def test_the_guanyu_temper_applies_only_to_the_first_unit():
    """Structure: temper occurs once, never around repeated units."""  # noqa: DOCSTRING_CJK
    # Require the tempered **unit** to occur once, not literal ``(?!关于|關於)``.
    # That prefix resides in _ZH_TOPIC_CHAR_NO_GUANYU; equivalent rewrites such as swapping alternatives
    # would otherwise falsely fail despite the protected behavior being independent of spelling (coderabbit).
    body = D._zh_topic(2, 30, block_guanyu=True)
    assert body.count(D._ZH_TOPIC_CHAR_NO_GUANYU) == 1, body
    assert D._ZH_TOPIC_CHAR_NO_GUANYU != D._ZH_TOPIC_CHAR


def test_the_guanyu_guard_is_scoped_to_one_template():
    assert "关于" not in D._ZH_TOPIC_CHAR
    assert "关于" in D._ZH_TOPIC_CHAR_NO_GUANYU
    scoped = [
        pat.pattern for loc, _k, pat in D.DIRECTIVE_PATTERNS
        if loc == "zh" and "(?!关于|關於)" in pat.pattern
    ]
    assert len(scoped) == 1, len(scoped)


# ── 11. All negation families provide Chinese evidence ──
# Product negations derive from _ZH_NEG, preventing additions without corresponding evidence updates.
NEGATIONS_FOR_EVIDENCE = ("别", "別", "不要", "不许", "不許", "不准", "莫", "甭", "休")


@pytest.mark.parametrize("negation", NEGATIONS_FOR_EVIDENCE)
def test_every_negation_counts_as_chinese_evidence(negation):
    """Single-character classes miss 不准/莫/休/不要 entirely, causing titles with Japanese grammar markers
    to be rejected as Japanese (codex P2). Add **structure**, not shared Kanji: adding 准/莫/休 to
    character classes short-circuits the whole guard like 没/称.

    Forms sharing Japanese code points (别 別) use 再: string-initial 別提X
    without 再 means another X in Japanese (別提案), deliberately deferred to its guard; see
    test_a_clause_initial_bie_compound_stays_behind_the_japanese_guard.
    """  # noqa: DOCSTRING_CJK
    again = "再" if negation in D._ZH_NEG_JA_AMBIGUOUS else ""
    assert _zh_terms(f"{negation}{again}提君の名は。") == {"君の名は"}


@pytest.mark.parametrize(
    "text",
    [
        "地域別講座の名称を確認します。",
        "性別講座について話しました。",
        "年代別講座の名称。",
        "休講のお知らせを確認します。",
    ],
)
def test_japanese_betsu_suffix_before_kou_is_not_chinese_evidence(text):
    """Japanese 別 means by-category, so 地域別講座 satisfies negation + speech verb.
    Left context is open (地域/年代/男女…), but right context is closed: only 講 forms
    such Japanese words after 別. Prefer missing a traditional user's ban over persisting Japanese fragments as directives.
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == set()


def test_the_negation_evidence_is_derived_from_the_verb_tables():
    sources = (
        D._ZH_NEG_VERB_EVIDENCE,
        D._ZH_MULTI_NEG_EVIDENCE,
        D._ZH_SUBJECT_BEFORE_NEG,
    )
    for verb in D._ZH_SAY_VERBS + D._ZH_SAY_COMPOUNDS:
        for source in sources:
            assert verb in source, (verb, source)
    for negation in NEGATIONS_FOR_EVIDENCE:
        assert any(negation in source for source in sources), negation


# ── 12. 谈论 must not consume topics beginning 论/論 ──
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("别谈论语考试。", "论语考试"),
        ("別談論語考試。", "論語考試"),
        ("别谈论语。", "论语"),
        ("別談論語。", "論語"),
        ("别谈论文格式。", "论文格式"),
        ("別談論文格式。", "論文格式"),
        ("别谈论政治。", "论政治"),
        ("別談論政治。", "論政治"),
    ],
)
def test_a_topic_beginning_with_lun_survives(text, expected):
    assert _zh_terms(text) == {expected}, _zh_terms(text)


def test_only_verbs_that_cannot_stand_alone_are_compounds():
    """讨 cannot stand alone (别讨政治 is unnatural), so include whole 讨论; 谈 can,
    so exclude 谈论, which consumes topic initials 论 (codex P2, the same family as result
    complements). This is the criterion itself; check before adding compound verbs.
    """  # noqa: DOCSTRING_CJK
    assert D._ZH_SAY_COMPOUNDS == ("讨论", "討論")
    for compound in D._ZH_SAY_COMPOUNDS:
        assert compound[0] not in D._ZH_SAY_VERBS, compound


@pytest.mark.parametrize(
    ("text", "expected"),
    [("别讨论文格式。", "文格式"), ("別討論文格式。", "文格式")],
)
def test_the_unavoidable_taolun_overlap_is_symmetric(text, expected):
    """Equivalent 讨论 overlap is unavoidable, as on base, but both scripts must match."""  # noqa: DOCSTRING_CJK
    assert expected in _zh_terms(text)


# ── 13. ASCII periods/commas inside identifiers are not boundaries ──
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Python 3.11别提了。", "Python 3.11"),
        ("Python 3.11別提了。", "Python 3.11"),
        ("example.com别提了。", "example.com"),
        ("example.com別提了。", "example.com"),
        ("价格1,000元别提了。", "价格1,000元"),
        ("價格1,000元別提了。", "價格1,000元"),
        # Both-side criteria must be Unicode-aware: [0-9A-Za-z] fixes only ASCII,
        # leaving all following cases broken (codex P2 second round).
        ("café.com别提了。", "café.com"),
        ("naïve.org别提了。", "naïve.org"),
        ("Дом.ру别提了。", "Дом.ру"),
        ("例子.测试别提了。", "例子.测试"),
        ("例子.測試別提了。", "例子.測試"),
        ("价格１,０００元别提了。", "价格１,０００元"),
        ("價格１,０００元別提了。", "價格１,０００元"),
        # Include fullwidth commas too: Chinese typesetting uses them in numbers.
        ("价格1，000元别提了。", "价格1，000元"),
        ("價格1，000元別提了。", "價格1，000元"),
        # Combining marks: Python \w excludes Mn/Mc, still truncating NFD forms and Devanagari.
        (unicodedata.normalize("NFD", "café.com") + "别提了。",
         unicodedata.normalize("NFD", "café.com")),
        ("देवनागरी.com别提了。", "देवनागरी.com"),
        ("Ωμέγα.gr别提了。", "Ωμέγα.gr"),
    ],
)
def test_identifier_internal_punctuation_is_not_a_topic_boundary(text, expected):
    assert expected in _zh_terms(text), _zh_terms(text)


@pytest.mark.parametrize(
    "text",
    ["工作别提了。", "功成名就别提了，功成名别提了。", "别提工作，别提加班。"],
)
def test_sentence_final_punctuation_is_still_a_boundary(text):
    """Require letters or digits on both sides; sentence-final periods followed by whitespace/end fail that criterion."""  # noqa: DOCSTRING_CJK
    for term in _zh_terms(text):
        assert "。" not in term and "，" not in term, term


def test_the_identifier_punct_rule_needs_both_sides():
    """Require both sides and Unicode awareness; hardcoded ASCII fixes only half (two codex P2 rounds)."""  # noqa: DOCSTRING_CJK
    # Use a **negative** criterion (not whitespace or sentence punctuation), rather than enumerating word characters;
    # enumeration still missed cases after three rounds (ASCII -> \w -> combining marks).
    assert "0-9A-Za-z" not in D._ZH_IDENT_PUNCT
    assert r"\w" not in D._ZH_IDENT_PUNCT
    assert D._ZH_IDENT_PUNCT.startswith(r"(?<=[^\s")
    assert D._ZH_IDENT_PUNCT.endswith(r"])")
    for unit in (D._ZH_TOPIC_CHAR, D._ZH_TOPIC_CHAR_NO_GUANYU):
        assert D._ZH_IDENT_PUNCT in unit
    # Premise guard: this criterion truly matches non-ASCII cases.
    import re as _re

    probe = _re.compile(D._ZH_IDENT_PUNCT)
    for ident in (
        "café.com", "Дом.ру", "例子.测试", "１,０００",
        unicodedata.normalize("NFD", "café.com"), "देवनागरी.com",
    ):
        assert probe.search(ident), ident
    # Sentence-final periods followed by whitespace/end do not match.
    for tail in ("工作. ", "工作."):
        assert not probe.search(tail), tail


# ── 14. Normalize brackets after filler stripping before comparing competitors ──
@pytest.mark.parametrize(
    "text",
    ["关于《你好，李焕英》就别提了。", "關於《你好，李煥英》就別提了。",
     "关于「我的事」就别提了。", "关于工作就别提了。"],
)
def test_a_filler_stripped_form_is_normalized_before_comparing(text):
    """Fillers often follow closing brackets: stripping 就 yields 你好，李焕英》, whose extra
    》 prevents matching the clean dedicated term, persisting malformed output for three days (codex P2).
    """  # noqa: DOCSTRING_CJK
    terms = _zh_terms(text)
    assert len(terms) == 1, terms
    for term in terms:
        assert not term.endswith("就"), term
        assert not any(term.endswith(hi) for _lo, hi in D._ZH_BRACKET_PAIRS), term


def test_normalizing_does_not_merge_two_separate_directives():
    """Compare normalized terms only for **overlapping** matches; independent instructions differing by 就 must survive."""  # noqa: DOCSTRING_CJK
    assert _zh_terms("功成名就别提了，功成名别提了。") == {"功成名就", "功成名"}
    assert _zh_terms("他的成就别提了。") == {"他的成就"}


# ── 15. Subject-leading instructions still provide Chinese evidence ──
# Japanese 〜別 ambiguity affects only **single-character** negations. Multicharacter 不要/不许/不許/不准
# cannot be Japanese noun suffixes; left-boundary restrictions only falsely reject them.
ZH_ONLY_SUBJECTS = ("你", "妳", "您", "咱", "请", "們")


@pytest.mark.parametrize("subject", ZH_ONLY_SUBJECTS)
@pytest.mark.parametrize("negation", ["别", "別"])
def test_a_subject_before_a_one_char_negation_keeps_the_evidence(subject, negation):
    text = f"{subject}{negation}提君の名は。"
    assert _zh_terms(text) == {"君の名は"}, text


@pytest.mark.parametrize("subject", ["我", "你", "他", "她", "咱", "您"])
@pytest.mark.parametrize("negation", ["不要", "不许", "不許", "不准"])
def test_multi_char_negations_need_no_left_boundary(subject, negation):
    text = f"{subject}{negation}提君の名は。"
    assert _zh_terms(text) == {"君の名は"}, text


def test_the_subject_allowlist_holds_no_japanese_kanji():
    """Include only Kanji wholly absent from Japanese. Deliberately exclude 我 / 他 / 請, which occur in Japanese;
    including them admits 他別提案をお願いします。 (measured).

    The primary assertion is **equality**: allowlists permit additions, whereas forbidden Japanese Kanji are an open set
    no copied denylist can exhaust. 俺 is both a northern Chinese colloquial subject and common Japanese Kanji;
    adding it persists 俺別提案をお願いします。 as a Chinese instruction (mutation testing).
    """  # noqa: DOCSTRING_CJK
    assert D._ZH_SUBJECT_CHARS == "你妳您咱请們"
    assert tuple(D._ZH_SUBJECT_CHARS) == ZH_ONLY_SUBJECTS
    for subject in ZH_ONLY_SUBJECTS:
        assert subject in D._ZH_SUBJECT_BEFORE_NEG, subject
    for kanji in ("我", "他", "她", "請", "貴"):
        assert kanji not in D._ZH_SUBJECT_BEFORE_NEG, kanji


@pytest.mark.parametrize(
    "text",
    ["他別提案をお願いします。", "我々は地域別提案を検討。", "貴社別提案の件。"],
)
def test_japanese_kanji_subjects_do_not_unlock_the_evidence(text):
    assert _zh_terms(text) == set()


# ── 16. Symmetric quotes must not swallow entire comma-separated instructions ──
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('尺寸5"别提了，尺寸6"别提了。', {"尺寸5", "尺寸6"}),
        ('尺寸5"別提了，尺寸6"別提了。', {"尺寸5", "尺寸6"}),
        # Commas remain allowed: many real work titles contain them.
        ('"Everything, Everywhere"别提了。', {"Everything, Everywhere"}),
        ('别提"你好，李焕英"了。', {"你好，李焕英"}),
        # Quotes with negation but no punctuation use single-character branches and remain intact.
        ('别提"再别康桥"了。', {"再别康桥"}),
        ('别提"我不是药神"了。', {"我不是药神"}),
    ],
)
def test_a_symmetric_quote_run_cannot_swallow_a_whole_directive(text, expected):
    assert _zh_terms(text) == expected


def test_only_symmetric_pairs_temper_the_negation():
    """Asymmetric brackets cannot be mistaken for closers and need no temper; preserve 电影(Hello, World)."""  # noqa: DOCSTRING_CJK
    # Criterion changed: now temper **every pair**. Symmetric quotes were first because lone ``"``
    # is common; ASCII asymmetric pairs later proved equivalent (``别再提价格<预算.别再提
    # 收入>目标.`` merged as one, codex P2). Fullwidth asymmetric pairs are not mistaken for closers, but another
    # zero-width lookahead leaves behavior unchanged; uniform use drifts less than exceptions.
    temper = f"(?!{D._ZH_DIRECTIVE_AHEAD})"
    assert D._ZH_BRACKET_RUN.count(temper) >= len(D._ZH_BRACKET_PAIRS)
    assert _zh_terms("电影(Hello, World)别提了。") == {"电影(Hello, World)"}


# ── 17. Strip rhetorical endings outside quotes ──
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Ending **after** a closing bracket is sentence-level tone: strip.
        ("別再提電影《你好》好嗎。", "電影《你好》"),
        ("别再提电影《你好》好吗。", "电影《你好》"),
        ("别再提《你的名字》好吗。", "你的名字"),
        ("別再提《你的名字》好嗎。", "你的名字"),
        ("别再提《你的名字》好不好。", "你的名字"),
        ('别再提"你的名字"好吗。', "你的名字"),
        # Ending **inside** brackets belongs to the name: retain.
        ("别再提《最近你好吗》。", "最近你好吗"),
        ("別再提《最近你好嗎》。", "最近你好嗎"),
        ("别再提电影《我们好不好》。", "电影《我们好不好》"),
        ("别再提《你可以吗》。", "你可以吗"),
        # Prefix ends with an **opener**: ending remains inside the title and must stay. Require closing brackets,
        # not any bracket character, which would truncate these three to 电影 / 剧集 and lose titles entirely.
        ("别再提电影《好不好》。", "电影《好不好》"),
        ("別再提電影《好不好》。", "電影《好不好》"),
        ("别再提剧集《可以吗》。", "剧集《可以吗》"),
        # Still strip when ordinary modifiers separate quotes and endings: requiring the prefix to end exactly with
        # a closing bracket misclassifies these three rows (codex P2).
        ("我不想再聊電影《你好》續集好嗎。", "電影《你好》續集"),
        ("我不想再聊电影《你好》续集好吗。", "电影《你好》续集"),
        ("别再提電影《你好》續集好嗎。", "電影《你好》續集"),
        # Stacked endings: after removing the outer one, inner indices must account for removed characters,
        # or 好吗 appears outside quotes (found by mutation testing).
        ("别再提《最近你好吗》好不好。", "最近你好吗"),
        ("別再提《最近你好嗎》好不好。", "最近你好嗎"),
        ("别再提「最近你好吗」好不好。", "最近你好吗"),
        # With multiple bracketed spans, inspect the **last** closing boundary, not the first (mutation testing).
        ("别再提《甲》《乙》好吗。", "甲》《乙"),
        ("别再提《甲》和《乙》好吗。", "甲》和《乙"),
        ("别再提电影《甲》《乙》好吗。", "电影《甲》《乙》"),
        # No brackets: unconditionally strippable.
        ("别再提工作好吗。", "工作"),
        ("別再提工作好嗎。", "工作"),
    ],
)
def test_an_interrogative_outside_the_quotes_is_still_a_tail(text, expected):
    """Do not check only original terms for brackets: paired-bracket stripping precedes particles, so brackets
    are gone by then. Check **whether the stripped prefix ends in a closing bracket** (codex P2),
    opposite to preserving 《最近你好嗎》 from truncation; both must hold.
    """  # noqa: DOCSTRING_CJK
    assert expected in _zh_terms(text), _zh_terms(text)


def test_the_interrogative_gate_uses_closing_delimiters():
    closers = {hi for _lo, hi in D._ZH_BRACKET_PAIRS}
    # Symmetric pairs use identical open/close characters, so closing-character sets must be subsets of, or equal to, all bracket characters.
    assert closers <= frozenset(ch for pair in D._ZH_BRACKET_PAIRS for ch in pair)
    assert "》" in closers and "《" not in closers


# ── 18. Single-character particles inside quotes belong to names too ──
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("别再提《想见你喔》。", "想见你喔"),
        ("別再提《想見你喔》。", "想見你喔"),
        ("别再提《就是爱唷》。", "就是爱唷"),
        ("別再提《就是愛唷》。", "就是愛唷"),
        ("别再提《你好啦》。", "你好啦"),
        # The same character outside quotes remains a particle and is stripped.
        ("别再提工作喔。", "工作"),
        ("別再提工作喔。", "工作"),
        ("别再提工作啦。", "工作"),
    ],
)
def test_single_char_particles_inside_a_quoted_name_survive(text, expected):
    """Quotation criteria apply to **all** CJK endings, not just multicharacter rhetorical phrases (codex P2)."""  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == {expected}, _zh_terms(text)


# ── 19. Hangul particles apply across all locales ──
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("别再提전남친은。", "전남친"),
        ("別再提전남친은。", "전남친"),
        ("别再提직장에。", "직장"),
        ("我不想聊남자친구를", "남자친구"),
    ],
)
def test_hangul_particles_are_trimmed_from_chinese_matches(text, expected):
    """Locale tables separate same-code-point CJK ambiguity (唄 as Chinese particle / Japanese song), whereas
    Hangul shares no code points with Han/Kana and cannot collide. Include it for every locale (codex P2).
    """  # noqa: DOCSTRING_CJK
    assert expected in _zh_terms(text), _zh_terms(text)


def test_the_hangul_table_is_not_script_ambiguous():
    """Pin that Hangul shares no code points with CJK, preventing future Han additions to ko tables."""  # noqa: DOCSTRING_CJK
    for tok in D._TRIM_TRAIL_TOKENS_BY_LOCALE["ko"]:
        for ch in tok:
            assert "\uac00" <= ch <= "\ud7a3" or "\u1100" <= ch <= "\u11ff", tok


# ── 20. Japanese guards read one character, never copy whole prefixes ──
def test_the_japanese_guard_gets_one_character_not_the_whole_prefix(monkeypatch):
    """Assert **argument lengths**, not timing: thresholds based on mutated baselines slow both sizes when
    copying whole prefixes, hiding it in ratios (mutation testing). The guard reads only
    ``before[-1:]``; slicing everything copies full text per match, quadratic for tens of thousands of instructions.
    """  # noqa: DOCSTRING_CJK
    seen = []
    real = D._is_japanese_sentence_match

    def spy(span, term, before="", **kwargs):
        seen.append(len(before))
        return real(span, term, before, **kwargs)

    monkeypatch.setattr(D, "_is_japanese_sentence_match", spy)
    extract_directives("工作别提了。" * 50 + "别提加班。")
    assert seen, "日文守卫根本没被调用，这条测试是空的"
    # Bound is a **constant**, not literal 1: subjects across whitespace require several preceding characters
    # (_ZH_SUBJECT_ACROSS_SPACE), but still O(1), never the entire prefix.
    assert max(seen) <= D._ZH_SUBJECT_LEFT_MAX, max(seen)
    assert D._ZH_SUBJECT_LEFT_MAX <= 8


def test_the_quoted_span_end_marks_where_the_quotes_stop():
    """Criteria concern position, not shape: 電影《你好》續集好嗎 has an internal closer, making the proxy
    prefix-ends-with-closer criterion wrong (codex P2).
    """  # noqa: DOCSTRING_CJK
    end = D._zh_quoted_span_end
    # No brackets: every ending is outside quotes.
    assert end("工作好吗") == 0
    # Complete bracketed span: following content lies outside quotes regardless of intervening modifiers.
    assert end("《最近你好嗎》") == 7
    assert end("電影《你好》續集好嗎") == 6
    assert end('"你的名字"好吗') == 6
    # Closing bracket only (opener stripped earlier): following content is outside quotes.
    assert end("你的名字》好吗") == 0
    # Unclosed opener extends through the end; all following content remains inside quotes.
    assert end("电影《我们好不好") == 8
    assert end("电影《好不好") == 6
    # Multiple bracketed spans: use the **last** closer.
    assert end("《甲》《乙》好吗") == 6
    assert end("《甲》《乙好吗》") == 8


def test_the_quoted_span_end_is_derived_from_the_bracket_run():
    """Do not create another bracket table; duplicate maintenance inevitably drifts (#2655)."""  # noqa: DOCSTRING_CJK
    assert D._ZH_CLOSE_FOR_OPEN == {
        lo: hi for lo, hi in D._ZH_BRACKET_PAIRS if lo != hi
    }


# ── 21. Every branch of every template must actually drive extraction ──
# Script-pair checks miss **identical** forms across scripts (聊 / 提及 / 不想). Deleting template 2's
# |聊 loses both scripts' instructions while structural assertions stay green (mutation testing). Use behavior-driven checks:
# read each template's alternatives and insert each into that template's sentence pattern; all must extract the same topic.
def _capture_start(raw: str) -> int:
    """Start of this template's first **capturing** group, skipping forms such as (?: / (?=."""  # noqa: DOCSTRING_CJK
    for pos, char in enumerate(raw):
        if char != "(" or (pos and raw[pos - 1] == chr(92)):
            continue
        if raw[pos : pos + 2] == "(?":
            continue
        return pos
    raise AssertionError(f"这条模板没有捕获组：{raw!r}")


def _group_containing(raw: str, needle: str) -> list[str]:
    """Extract this template's alternative group **containing needle**.

    First remove bracket-body temper (negative lookahead for negation + optional 再 + speech verb): **zero-width**
    lookaheads drive no extraction but contain their own verb alternatives, which needle searches would otherwise find.
    """  # noqa: DOCSTRING_CJK
    raw = raw.replace(f"(?!{D._ZH_DIRECTIVE_AHEAD})", "")
    for group in _alternation_groups(raw):
        if needle in group:
            return group
    raise AssertionError(f"这条模板里没有含 {needle!r} 的分支组：{raw!r}")


# **Pin branch tables as literals**, never derive from patterns: deleting pattern branches would
# also remove their test cases, shrinking tests while remaining green, the derived-test blind spot
# previously seen here. Pinned tables make deletion fail and additions require updates plus paired-script cases.
#
# First column is the **1-based template number**, matching comments naming templates 2/3/4, not a list
# index. This file otherwise uses 1-based references; mixing conventions misrepresents coverage (CodeRabbit).
# (template number, locating alternative for the branch group, pinned alternatives, sentence pattern, expected topic)
BRANCH_DRIVE_CASES = [
    (2, "提了", ("提了", "提起", "提及", "说", "說", "提", "聊", "讲", "講"),
     "工作别{branch}。", "工作"),
    (2, "提了", ("提了", "提起", "提及", "说", "說", "提", "聊", "讲", "講"),
     "工作別{branch}。", "工作"),
    (3, "聊", ("讨论", "討論", "说", "說", "提", "聊", "讲", "講", "谈", "談", "扯"),
     "我不想{branch}工作。", "工作"),
    (3, "不想", ("不想", "不愿意", "不願意", "不愿", "不願", "懒得", "懶得",
                 "没心情", "沒心情"),
     "我{branch}聊工作。", "工作"),
    # Template 4 now shares trigger source _ZH_PREPOSED_SAY_VERBS with template 2; formerly hardcoded,
    # it failed to accept 關於工作就別提起了。 (codex P2).
    (4, "提", ("提起", "提及", "说", "說", "提", "聊", "讲", "講"),
     "关于工作就别{branch}了。", "工作"),
    (4, "提", ("提起", "提及", "说", "說", "提", "聊", "讲", "講"),
     "關於工作就別{branch}了。", "工作"),
]


@pytest.mark.parametrize(
    ("template_no", "anchor", "pinned", "skeleton", "expected"), BRANCH_DRIVE_CASES,
)
def test_every_branch_in_the_group_actually_drives_extraction(
    template_no, anchor, pinned, skeleton, expected,
):
    raw = _zh_pattern_sources()[template_no - 1]
    # Premise guard: pinned tables must **equal** implementation alternatives, or tests drive outdated tables.
    assert tuple(_group_containing(raw, anchor)) == pinned, (
        f"模板 {template_no} 的分支组变了，请同步这里并给新分支补繁简对照"
    )
    for branch in pinned:
        text = skeleton.format(branch=branch)
        assert expected in _zh_terms(text), f"{text!r} -> {_zh_terms(text)}"


def test_negation_constants_are_pinned():
    """Closed sets require equality. Adding _ZH_NEG negations without matching Japanese-guard evidence leaves Chinese working
    but rejects new-negation + Japanese-name instructions while older equivalent forms survive, causing within-template
    asymmetry. All three evidence regexes now derive from these two tables, synchronizing additions; this assertion
    makes vocabulary additions pause for review.
    """  # noqa: DOCSTRING_CJK
    assert D._ZH_NEG_SINGLES == ("别", "別", "莫", "休", "甭")
    assert D._ZH_NEG_MULTIS == ("不要", "不许", "不許", "不准")
    # All three evidence regexes must truly derive from the two tables above.
    for neg in D._ZH_NEG_SINGLES:
        assert neg in D._ZH_NEG_VERB_EVIDENCE, neg
        assert neg in D._ZH_SUBJECT_BEFORE_NEG, neg
    for neg in D._ZH_NEG_MULTIS:
        assert neg in D._ZH_MULTI_NEG_EVIDENCE, neg
    # _ZH_NEG itself must also be assembled from that same table.
    for neg in D._ZH_NEG_MULTIS:
        assert neg in D._ZH_NEG, neg


def test_the_branch_drive_cases_cover_every_template_but_the_first():
    """Section 2's product covers template 1 verbs/negations; the preceding test covers 2/3/4."""  # noqa: DOCSTRING_CJK
    covered = {case[0] for case in BRANCH_DRIVE_CASES}
    assert covered == {2, 3, 4}
    assert len(_zh_pattern_sources()) == 4


# ── 22. Pin shared constants on every template using them ──
# These guards/branches apply to **multiple templates**, but previously only template 1 or 2
# had samples. Removing copies from other templates regressed behavior while tests passed, found through individual mutations.
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # (a) Standalone title-bracket topics: _zh_topic's leading bracket branch is shared by all four;
        #     template 3 (不想/懒得 + verb + object) previously had no samples.
        ("我不想聊《你好，李焕英》", "你好，李焕英"),
        ("我不想聊「加班的事」", "加班的事"),
        ("懶得聊《你好，李煥英》", "你好，李煥英"),
        ("我沒心情談《你好，李煥英》", "你好，李煥英"),
    ],
)
def test_template_three_also_takes_a_quoted_topic(text, expected):
    assert expected in _zh_terms(text), _zh_terms(text)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # (b) `的?+demonstrative` fillers appear in templates 2 and 4; only template 2 previously had samples.
        ("关于减肥这话题就别说了。", "减肥"),
        ("關於減肥這話題就別說了。", "減肥"),
        ("关于工作这事就别提了。", "工作"),
        ("關於工作這事就別提了。", "工作"),
        ("关于减肥这个就别说了。", "减肥"),
        ("關於減肥的這個就別說了。", "減肥"),
        ("关于工作这件事就别提了。", "工作"),
        ("關於工作這件事就別提了。", "工作"),
    ],
)
def test_template_four_consumes_the_demonstrative_filler(text, expected):
    assert _zh_terms(text) == {expected}, _zh_terms(text)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # This test originally asserted the **opposite**: I added (?:的事)? to template 2 and claimed
        # 工作的事别提了。 should yield 工作. Base stores 工作的事, correctly:
        # template 2 lacks anchors such as 关于, so 的事 belongs to the topic. Truncating 我们的事别提了。
        # to 我们 tells the model to avoid the users themselves rather than that matter (codex P2).
        ("工作的事别提了。", "工作的事"),
        ("工作的事別提了。", "工作的事"),
        ("前女友的事别提了。", "前女友的事"),
        ("前女友的事別提了。", "前女友的事"),
        ("我们的事别提了。", "我们的事"),
        ("我們的事別提了。", "我們的事"),
        # Postposed forms already retain it; align both sides.
        ("别提我们的事。", "我们的事"),
        ("別提我們的事。", "我們的事"),
    ],
)
def test_the_preposed_template_keeps_deshi_in_the_topic(text, expected):
    assert _zh_terms(text) == {expected}, _zh_terms(text)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Template 4 also **does not consume** 的事; this was initially reversed. 的事 expresses possession plus nominalization,
        # potentially part of names. Worse, truncated terms make
        # _drop_filler_suffixed_terms delete template 2's **correct** term as
        # truncated-term + filler, leaving only truncation (codex P2).
        ("关于工作的事就别提了。", "工作的事"),
        ("關於工作的事就別提了。", "工作的事"),
        ("关于我们的事别提了。", "我们的事"),
        ("關於我們的事別提了。", "我們的事"),
        ("关于我前女友的事就别提了。", "我前女友的事"),
    ],
)
def test_the_guanyu_template_keeps_deshi_in_the_topic(text, expected):
    assert _zh_terms(text) == {expected}, _zh_terms(text)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Retain demonstrative groups: unambiguously filler; the topic is 减肥.
        ("关于减肥这话题就别说了。", "减肥"),
        ("關於減肥這話題就別說了。", "減肥"),
        ("关于工作这件事就别提了。", "工作"),
        ("關於工作這件事就別提了。", "工作"),
        # Retain (?:就)? too: removing it degrades these two cases into junk containing 就.
        ("关于股票就别提了。", "股票"),
        ("关于工作就别提了。", "工作"),
        ("關於股票就別提了。", "股票"),
    ],
)
def test_the_guanyu_template_still_consumes_unambiguous_fillers(text, expected):
    """The criterion is **ambiguity**, not filler position: demonstratives and final 就 are unambiguous,
    的事 is ambiguous. All share a template, but remove only the ambiguous one (measured both options;
    removing 就 too turns these three clean results into junk).
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == {expected}, _zh_terms(text)


def test_the_preposed_topic_unit_excludes_newlines_too():
    """Template 2's preposed topics use the **different** _ZH_PLAIN_CHAR_NO_GUANYU constant,
    previously untouched by assertions. Removing newline exclusion swallowed subsequent instructions across lines,
    while every newline sample exercised templates 1/3 only (mutation testing).
    """  # noqa: DOCSTRING_CJK
    assert D._ZH_PLAIN_CHAR_NO_GUANYU.endswith(D._ZH_PLAIN_CHAR)
    assert D._ZH_PLAIN_CHAR_NO_GUANYU == r"(?!关于|關於)" + D._ZH_PLAIN_CHAR
    # Behavior: preposed topics cannot cross lines.
    for text in ("工作\n股票别提了。", "工作\r\n股票別提了。"):
        for term in _zh_terms(text):
            assert "\n" not in term and "\r" not in term, (text, term)


# ── 23. Particle tables without Han apply across all locales ──
def test_script_disjoint_families_are_discovered_not_listed():
    """Automatic discovery, not manual lists: locale separation addresses shared **Han** ambiguity (唄 as Chinese
    particle/Japanese song; 了 in 完了/終了). Kana/Hangul do not share Han code points,
    safely applying across locales. Future purely Kana/Hangul tables enter this set automatically.
    """  # noqa: DOCSTRING_CJK
    assert set(D._SCRIPT_DISJOINT_FAMILIES) == {"ja", "ko"}
    for fam in D._SCRIPT_DISJOINT_FAMILIES:
        for tok in D._TRIM_TRAIL_TOKENS_BY_LOCALE[fam]:
            assert not D._HAN_RE.search(tok), (fam, tok)
    # Conversely zh tables contain Han, so must remain behind locale gates.
    assert "zh" not in D._SCRIPT_DISJOINT_FAMILIES
    assert any(D._HAN_RE.search(t) for t in D._TRIM_TRAIL_TOKENS_BY_LOCALE["zh"])


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Locale describes the matched template, not the topic's language.
        ("别再提仕事ね。", "仕事"),
        ("別再提仕事ね。", "仕事"),
        ("不要说元彼って。", "元彼"),
        ("不要說元彼って。", "元彼"),
        ("别再提전남친은。", "전남친"),
        ("別再提전남친은。", "전남친"),
    ],
)
def test_a_chinese_match_still_trims_kana_and_hangul_tails(text, expected):
    assert _zh_terms(text) == {expected}, _zh_terms(text)


@pytest.mark.parametrize(
    "text",
    ["地域別講座だね。", "世代別講座だよ。", "世代別提案だって。", "職種別講座でしょ。"],
)
def test_trimming_the_kana_tail_does_not_blind_the_japanese_guard(text):
    """The guard must inspect **untrimmed** captures: once Kana particle tables apply across locales,
    trim removes だね, so Japanese grammar markers disappear before guard inspection, wrongly classifying
    the sentence as Chinese (encountered while adding Kana fallback).
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == set()


# ── 24. Traditional plural subjects ──
@pytest.mark.parametrize("subject", ["我們", "你們", "咱們", "您們"])
def test_a_plural_traditional_subject_keeps_the_evidence(subject):
    """們 is a traditional plural suffix, with neither 們 nor 们 used in Japanese; following 別
    therefore cannot be Japanese 〜別. Excluding it rejects traditional instructions while simplified
    我们别… survives via 们 evidence elsewhere (codex P2).
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(f"{subject}別再提君の名は。") == {"君の名は"}


# ── 25. Apply the same trim after filler stripping before comparison ──
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("關於전남친은就別提了。", "전남친"),
        ("关于전남친은就别提了。", "전남친"),
        ("關於仕事ね就別提了。", "仕事"),
        ("关于股票就别提了。", "股票"),
    ],
)
def test_the_shortened_form_is_trimmed_before_comparing(text, expected):
    """The competitor term was trimmed before persistence, while intermediate forms retain particles: stripping only punctuation
    leaves 전남친은 unequal to 전남친 and persists malformed output for three days (codex P2).
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == {expected}, _zh_terms(text)


# ── 26. Lone symmetric quotes are not unfinished quotations ──
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Inch marks: lone " is ordinary text; following 好吗 remains sentence-level tone.
        ('别再提5"屏幕好吗。', '5"屏幕'),
        ('別再提5"螢幕好嗎。', '5"螢幕'),
        ('别再提27"顯示器好不好。', '27"顯示器'),
        # Lone asymmetric openers truly mark unfinished quotations, leaving endings inside.
        ("别再提电影《我们好不好》。", "电影《我们好不好》"),
        ("别再提电影《好不好》。", "电影《好不好》"),
    ],
)
def test_a_standalone_symmetric_quote_is_not_an_unclosed_opener(text, expected):
    """_zh_bracket_body already treats lone ASCII quotes as inch marks and :( as ordinary text,
    not hard boundaries. Reinterpreting them as quotation starts in unclosed-opener checks contradicts
    that decision, treating identical characters differently within one module (codex P2).
    """  # noqa: DOCSTRING_CJK
    assert expected in _zh_terms(text), _zh_terms(text)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("别提工作.别提加班.", {"工作", "加班"}),
        ("別提工作.別提加班.", {"工作", "加班"}),
        ("别提工作,别提加班.", {"工作", "加班"}),
        ("別提工作,別提加班.", {"工作", "加班"}),
    ],
)
def test_relaxing_to_word_chars_does_not_merge_two_directives(text, expected):
    """After widening to \\w, Han may surround ASCII punctuation, but instructions remain separable:
    lazy objects and existing ASCII-dot terminators try shorter matches first. Only when shorter
    matches fail does dot enter topics. Pin that premise here.
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == expected, _zh_terms(text)


# ── 27. Left-boundary checks only for the two forms ambiguous with Japanese 〜別 ──
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("我莫再提君の名は。", "君の名は"),
        ("她莫再提地域の話。", "地域の話"),
        ("我休再提君の名は。", "君の名は"),
        ("他甭再提君の名は。", "君の名は"),
    ],
)
def test_only_bie_needs_the_han_left_boundary(text, expected):
    """Japanese 〜別 suffix ambiguity is exclusive to 别/別, not 莫 / 休 / 甭, which are not
    Japanese noun suffixes. Applying it to the whole family blocks ordinary Chinese subjects too (codex P2).
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == {expected}, _zh_terms(text)


def test_the_ambiguous_negations_are_a_pinned_subset():
    assert D._ZH_NEG_JA_AMBIGUOUS == ("别", "別")
    assert set(D._ZH_NEG_JA_AMBIGUOUS) <= set(D._ZH_NEG_SINGLES)
    # All others use the no-left-boundary branch, while 休 still excludes 講.
    assert D._ZH_NEG_UNAMBIGUOUS == ("莫", "休(?!講)", "甭")


# ── 28. Quoted Chinese is not evidence for the Japanese guard ──
@pytest.mark.parametrize(
    "text",
    [
        "世代別講座で中国映画「這就是愛」について話します。",
        "世代別講座で中国映画「这就是爱」について話します。",
        "地域別提案で映画『這就是愛』について話します。",
        # Replace quoted spans with **equal-length** blanks; deletion joins characters across quotes, fabricating
        # multicharacter evidence (叫+我 / 不+想 / 懶+得 / 不+願 / 喊+我) and wrongly admitting Japanese sentences.
        "世代別講座で叫「這就是愛」我について話します。",
        "地域別提案は不「這」想について話します。",
        "地域別提案で懶「愛」得について話します。",
        "世代別講座で不「愛」願について話します。",
        "地域別講座は喊「愛」我だそうです。",
    ],
)
def test_a_quoted_chinese_title_does_not_disable_the_japanese_guard(text):
    """Japanese sentences naturally quote Chinese movie titles. Quoted 這 short-circuits the entire guard,
    persisting Japanese fragments as three-day directives (codex P2). Blank paired quotes before evidence searches.
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == set()


def test_blanking_the_quotes_keeps_evidence_outside_them():
    """Conversely evidence **outside** quotes still counts, or entire Chinese instruction families fail."""  # noqa: DOCSTRING_CJK
    assert _zh_terms("别再提《想見你喔》。") == {"想見你喔"}
    assert _zh_terms("別叫我「お兄ちゃん」。") == {"お兄ちゃん"}
    assert _zh_terms("别提《我很好吗》这件事。") == {"《我很好吗》这件事"}


# ── 29. Bare interrogative particles ──
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("別再提工作嗎？", "工作"),
        ("别再提工作吗？", "工作"),
        ("不要再說工作了嗎？", "工作"),
        ("不要再说工作了吗？", "工作"),
        # The same quoted characters remain protected.
        ("别再提《你可以吗》。", "你可以吗"),
        ("別再提《你可以嗎》。", "你可以嗎"),
        # Retain the whole term if stripping leaves fewer than two characters.
        ("别提行吗。", "行吗"),
        ("別提行嗎。", "行嗎"),
    ],
)
def test_a_bare_question_particle_is_a_tail_too(text, expected):
    assert _zh_terms(text) == {expected}, _zh_terms(text)


def test_a_whitespace_only_message_does_not_blow_up():
    """A whitespace-only message must not explode template 2 (codex P1).

    The preposed topic's plain-char branch also matches spaces, so the topic and
    every whitespace run after it can split one run of spaces any way they like.
    ``" " * 60`` once took 0.42s on a path that runs synchronously per message.
    """
    from tests.wall_clock import fastest_run

    extract_directives(" ")  # Warm up: exclude initial regex compilation from timing (CodeRabbit).
    # Time **template 2 alone** for the primary criterion. extract_directives sums 21 templates; when written,
    # pure-whitespace cost was dominated by the Korean template ``(.{1,30}?)\s*(?:이|가)?\s*…``'s cubic behavior
    # (about 7ms for 60 spaces versus template 2's 0.7ms). Even removing **all** template-2 whitespace atomicity cost only
    # 21ms for 60 spaces, still passing ``timings[60] < timings[30] * 25 + 0.02``. Korean was later
    # fixed (see test_directive_regex_whitespace.py), but whole timing still measures other templates.
    # Input must **begin with content**, never pure whitespace: topic capture now forbids whitespace-leading matches (see
    # _PATTERNS_RAW's beginning), so every pure-whitespace starting point fails immediately even without atomicity;
    # measured ``" " * 240`` stays below 0.1ms either way. Beginning with 工作 creates one starting point,
    # distributing all following whitespace: atomic 0.6ms versus non-atomic 2.2s. The 0.25s threshold leaves
    # roughly 400x and 9x margins respectively; do not loosen it. Take the fastest run to filter scheduling preemption.
    pat = [p for locale, _kind, p in D.DIRECTIVE_PATTERNS if locale == "zh"][1]
    text = "工作" + " " * 1500
    elapsed = fastest_run(lambda: list(pat.finditer(text)), stop_below=0.25)
    assert elapsed < 0.25, f"模板 2 在 {len(text)} 个字符上最快也要 {elapsed:.3f}s，空白瓜分回溯又回来了"
    # Keep only a very loose end-to-end ceiling: this path runs synchronously for every user message.
    assert fastest_run(lambda: extract_directives(" " * 60), stop_below=0.5) < 0.5


def test_the_preposed_template_spacing_is_atomic():
    """Structure: every \\s* **before verbs** in template 2 must be atomic.

    Post-verbal ``\\s*(?:了)?`` must **not** be atomic: following terminators include \\s,
    so atomicity consumes spaces that should terminate (工作别提 然后… would stop matching).
    """  # noqa: DOCSTRING_CJK
    raw = _zh_pattern_sources()[1]
    # Remove zero-width temper first here too: its whitespace belongs to the **criterion**, permitting spaces between negation
    # and verbs, rather than quantifiers participating in partitioning.
    raw = raw.replace(f"(?!{D._ZH_DIRECTIVE_AHEAD})", "")
    head = raw.split("(?:提了|")[0]
    # Require **every whitespace-matching quantifier** inside atomic groups, rather than counting (?>\s*) occurrences.
    # Pause-separator whitespace now uses horizontal-only classes, so count assertions drift. Remove both
    # unit forms first and check remaining omissions to enforce the actual invariant.
    # Inspect **parse trees**, not literals: recognizing only \s* / _ZH_HSPACE lets
    # _ZH_HSPACE_ONE + "+", ``(?:\s)*``, and ``[ \t]*`` bypass both structural and timing checks.
    assert not _splittable_whitespace_runs(head), _splittable_whitespace_runs(head)
    units = (r"(?>\s*)", f"(?>{D._ZH_HSPACE})")
    for unit in units:
        assert unit + unit not in head, unit
    # Template 2 now has no cross-line whitespace **before triggers**: topic/filler surroundings and
    # trigger interiors all use horizontal classes, since instructions cannot cross lines. Pin count against wholesale removal:
    # two _ZH_PAUSE_THEN_JIU occurrences (three each) + one after topic + one after filler + two inside trigger.
    assert head.count(r"(?>\s*)") == 0, head.count(r"(?>\s*)")
    atomic_h = f"(?>{D._ZH_HSPACE})"
    assert head.count(atomic_h) == 2 * D._ZH_PAUSE_THEN_JIU.count(atomic_h) + 4


# ── 30. Nested quotes / verb-object pauses / ASCII square brackets ──
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # The outer opener precedes the complete inner quotation: scanning only after the last complete quote misses it,
        # treating 好吗 as sentence tone and truncating even the inner 」 (codex P2).
        ("别再提电影《续集「你好」好吗。", "电影《续集「你好」好吗"),
        ("別再提電影《續集「你好」好嗎。", "電影《續集「你好」好嗎"),
        ("别再提《甲「乙」好吗。", "甲「乙」好吗"),
    ],
)
def test_an_unmatched_outer_opener_before_a_nested_run_still_counts(text, expected):
    assert _zh_terms(text) == {expected}, _zh_terms(text)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Typing and ASR both create pauses between verbs/objects; parent consumes them through .{1,40}? and trims afterward.
        ("别再提，工作。", "工作"),
        ("別再提，工作。", "工作"),
        ("我不想聊，工作。", "工作"),
        ("我不想聊，工作", "工作"),
        ("不要说，加班的事。", "加班的事"),
        ("别叫我，“笨蛋”。", "笨蛋"),
        ("别再提、工作。", "工作"),
        ("别再提：工作。", "工作"),
        ("别再提,工作。", "工作"),
        # Enumeration commas / colons **must** enter separator tables. Initially thought unnecessary because topic character classes
        # permit them as ordinary content, later trimmed, passing the three cases above. But short topics
        # with **ambiguous final characters** break this: topic becomes ：好, punctuation satisfying two-unit
        # minimum while optional particles consume 咧; trim leaves one character and discards it (codex P2).
        ("别提：好咧。", "好咧"),
        ("别提、好咧。", "好咧"),
        ("别提，好咧。", "好咧"),
        ("別提：好咧。", "好咧"),
    ],
)
def test_a_separator_between_verb_and_object_is_consumed(text, expected):
    """Separators must precede objectless lookaheads: the latter treat punctuation immediately after verbs as no
    object, rejecting these entire instructions unless separators are consumed first; implementation once reversed this order.
    """  # noqa: DOCSTRING_CJK
    assert expected in _zh_terms(text), _zh_terms(text)


@pytest.mark.parametrize(
    "text", ["别提，。", "别提。", "别提，", "别提起了。", "别说完了。"],
)
def test_a_separator_alone_is_still_objectless(text):
    """Separators must not bypass objectless criteria."""  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == set()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("[Hello, World]别提了。", "Hello, World"),
        ("[Hello, World]別提了。", "Hello, World"),
        ("别提[Hello, World]了。", "Hello, World"),
        ("別提[重要，緊急]了。", "重要，緊急"),
    ],
)
def test_ascii_square_brackets_are_a_paired_delimiter(text, expected):
    """_TRIM_TRAIL already strips them as outer delimiters, but missing pair-table entries make internal commas
    hard boundaries (codex P2).
    """  # noqa: DOCSTRING_CJK
    assert expected in _zh_terms(text), _zh_terms(text)


# ── 31. Evidence comes only from instructions / minimum protects only ambiguous endings ──
@pytest.mark.parametrize(
    "text",
    [
        # Payload Chinese never counts as evidence, with or without quotes (two codex P2 rounds).
        "世代別講座で中国映画這就是愛について話します。",
        "地域別提案で中国映画这就是爱について話します。",
        "世代別講座で中国映画「這就是愛」について話します。",
    ],
)
def test_chinese_in_the_payload_is_not_directive_evidence(text):
    assert _zh_terms(text) == set()


@pytest.mark.parametrize(
    "text",
    [
        # Conversely, evidence in the **instruction** still counts normally.
        "地域別提案をお願いします。",
        "テーマ別討論スレッド。",
        "個別提案をお願いします。",
        "休講だそうです。",
    ],
)
def test_blanking_the_payload_does_not_blind_the_kana_conditions(text):
    """Only **evidence** inspects blanked instruction portions; Kana/Japanese-grammar criteria still inspect full matches.
    Passing blanked strings removes Kana too, wrongly treating these sentences as Chinese because Kana is absent.
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == set()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("我没心情聊君の名は。", "君の名は"),
        ("我沒心情聊君の名は。", "君の名は"),
        ("没心情提君の名は。", "君の名は"),
        ("沒心情提君の名は。", "君の名は"),
    ],
)
def test_meixinqing_is_structural_chinese_evidence(text, expected):
    """没 is standard Japanese Kanji (没収), excluded from character classes, but the three-character phrase is Chinese-only.
    Omitting it rejects simplified forms while traditional 沒心情 survives accidentally because 沒 belongs to evidence,
    causing within-template asymmetry (codex P2).
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == {expected}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Unambiguous endings (ASCII / Kana / Hangul) strip even below minimum; discard by length afterward.
        ("stop talking about X porfa", set()),
        ("別再提錢please。", set()),
        ("别再提钱please。", set()),
        # Protect ambiguous Chinese endings: retaining them is better than losing the whole instruction below minimum.
        ("别再提好咧。", {"好咧"}),
        ("别再提拿捏。", {"拿捏"}),
        ("别提行吗。", {"行吗"}),
    ],
)
def test_the_length_floor_only_protects_ambiguous_tails(text, expected):
    """耶 / 捏 / 咧 also end ordinary words, while ASCII please / porfa, Kana, and Hangul
    cannot form Chinese words. Applying minimum protection to the latter stores 別再提錢please。 as 錢please,
    whereas parent strips then discards by length (codex P2).
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == expected, _zh_terms(text)


def test_the_ambiguity_criterion_is_the_presence_of_han():
    """Require tokens containing Han, not a manually enumerated list."""  # noqa: DOCSTRING_CJK
    for tok in D._TRIM_TRAIL_TOKENS_ANY:
        assert not D._HAN_RE.search(tok), tok
    for fam in D._SCRIPT_DISJOINT_FAMILIES:
        for tok in D._TRIM_TRAIL_TOKENS_BY_LOCALE[fam]:
            assert not D._HAN_RE.search(tok), (fam, tok)
    assert all(D._HAN_RE.search(t) for t in D._TRIM_TRAIL_TOKENS_BY_LOCALE["zh"])


def test_the_full_width_comma_is_only_identifier_punctuation_between_digits():
    """Fullwidth commas commonly separate Chinese clauses; treating them unconditionally as internal punctuation makes preposed topics cross clauses.
    Their genuine identifier role is thousands grouping. Exclude fullwidth periods entirely: no identifiers use them.
    """  # noqa: DOCSTRING_CJK
    import re as _re

    probe = _re.compile(D._ZH_IDENT_PUNCT)
    assert probe.search("价格1，000元")
    assert not probe.search("算了，工作")
    assert not probe.search("工作。加班")
    assert _zh_terms("别提工作。别提加班。") == {"工作", "加班"}
    assert _zh_terms("算了，别提工作。") == {"工作"}
    assert _zh_terms("算了,别提工作。") == {"工作"}


# ── 32. Same-type nested brackets / naming verbs / template-4 whitespace ──
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Same-type nesting: regex pairs outer openers with **inner** closers; depth scanning is required.
        ("别再提《电影《你好吗》续集好吗》。", "电影《你好吗》续集好吗"),
        ("別再提《電影《你好嗎》續集好嗎》。", "電影《你好嗎》續集好嗎"),
        ("别再提《电影《你好》续集好吗》。", "电影《你好》续集好吗"),
        ("别再提「甲「乙好吗」丙好吗」。", "甲「乙好吗」丙好吗"),
    ],
)
def test_nested_same_type_delimiters_track_depth(text, expected):
    assert _zh_terms(text) == {expected}, _zh_terms(text)


def test_the_quoted_span_scanner_is_depth_aware():
    end = D._zh_quoted_span_end
    assert end("《电影《你好吗》续集好吗》") == 13
    assert end("《甲》《乙》好吗") == 6
    assert end("电影《我们好不好") == 8
    # Lone symmetric quotes are inch marks, not unclosed quotation.
    assert end('5"屏幕好吗') == 0
    assert end('"甲"好吗') == 3


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("不要称呼我「君の名は」。", "君の名は"),
        ("不准称呼我《君の名は》。", "君の名は"),
        ("莫称呼我“君の名は”。", "君の名は"),
        ("不要稱呼我「君の名は」。", "君の名は"),
        ("不准稱呼我《君の名は》。", "君の名は"),
    ],
)
def test_address_verbs_are_chinese_structural_evidence(text, expected):
    """Template 1 also includes _ZH_ADDRESS_VERBS, while evidence previously contained only 叫我 / 喊我 / 管我叫.
    称 is standard Japanese (名称), excluded from character classes, but whole 称呼我 is Chinese-only;
    omitting it loses these instructions while 别称呼我… survives accidentally through 别 evidence
    (codex P2, same asymmetry family as 甭 / 没心情).
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == {expected}, _zh_terms(text)


def test_every_address_verb_has_evidence_coverage():
    """Automatic discovery: adding _ZH_ADDRESS_VERBS verbs without matching evidence fails here."""  # noqa: DOCSTRING_CJK
    for verb in D._ZH_ADDRESS_VERBS:
        stem = verb.replace("?", "").replace("为", "").replace("為", "")
        assert any(word in stem or stem in word for word in D._ZH_EVIDENCE_WORDS), stem


def test_the_guanyu_template_spacing_is_atomic_too():
    """Template 4 needs every whitespace run before its verb atomic, like template 2.

    An earlier round atomized only template 2 and missed this one, and
    ``"关于" + " " * 80`` then took 3 seconds on the per-message path.
    """  # noqa: DOCSTRING_CJK
    from tests.wall_clock import fastest_run

    # ── Structure, the deterministic primary criterion, matching test_the_preposed_template_spacing_is_atomic ──
    raw = _zh_pattern_sources()[3]
    # **Assert split anchors exist**. Previously hardcoded (?:说|說|提|聊|讲|講) disappeared when triggers shared
    # _ZH_PREPOSED_SAY_VERBS; split no longer worked and head became the whole template.
    # Already-horizontal whitespace meant no \s*, making the assertion always true and structural coverage vacuous.
    verbs = "(?:" + "|".join(D._ZH_PREPOSED_SAY_VERBS) + ")"
    # Include occurrence counts in messages: more than one also causes vacuity by truncating head with split()[0], not only missing anchors.
    assert raw.count(verbs) == 1, (
        f"模板 4 里触发词组出现 {raw.count(verbs)} 次（应为 1），结构判据会空转"
    )
    head = raw.split(verbs)[0]
    # Whitespace inside zero-width temper belongs to criteria, not partitioning; remove it first.
    head = head.replace(f"(?!{D._ZH_DIRECTIVE_AHEAD})", "")
    # This is the sole defense when **one** whitespace unit loses atomicity: individually reverting any template-4
    # atomic unit still processes 关于 + 80 spaces in milliseconds, beyond timing detection. Inspect parse trees
    # (including ``(?:\s)*``, ``[ \t]+``, ``\s{0,50}``), never literal spelling.
    assert not _splittable_whitespace_runs(head), _splittable_whitespace_runs(head)

    # ── Behavior: time template 4 alone ──
    # Never time all extract_directives: the Korean ``(.{1,30}?)\s*(?:이|가)?\s*…`` template
    # then had cubic whitespace behavior, dominating 关于 + 80 spaces (around 20ms locally,
    # template 4 about 2µs). Its ratio measured Korean plus load; CI/xdist scheduling
    # preemption produced false failures such as {40: 0.003, 80: 0.236}. Korean was fixed and overall
    # whitespace behavior is linear, but timing all 21 templates still hides template-4
    # regressions among others, so the reasoning remains valid.
    # Removing all template-4 leading-whitespace atomicity measured fastest 0.96s for 80 spaces locally (n^5; 20->40 rose
    # 24x), versus atomic 2µs. The 0.1s threshold offers ample margin **only on the fast side** (about 50,000x),
    # versus only **about 10x** on the slow side. Never raise to 1s even on slow runners, or full reversion escapes.
    # Take the fastest of multiple runs to filter scheduling noise.
    pat = [p for locale, _kind, p in D.DIRECTIVE_PATTERNS if locale == "zh"][3]
    assert pat.pattern == raw
    text = "关于" + " " * 80
    elapsed = fastest_run(lambda: list(pat.finditer(text)), stop_below=0.1)
    assert elapsed < 0.1, f"模板 4 在 {text!r} 上最快也要 {elapsed:.3f}s，空白瓜分回溯又回来了"


# ── 33. Bracket bodies recognize one level of same-type nesting ──
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Regex closes at the first same-type closer, leaving later commas unprotected.
        ("《电影《你好》续集，第二章》别提了。", "电影《你好》续集，第二章"),
        ("《電影《你好》續集，第二章》別提了。", "電影《你好》續集，第二章"),
        ("《甲《乙》丙，丁》别提了。", "甲《乙》丙，丁"),
        ("别提《电影《你好》续集，第二章》。", "电影《你好》续集，第二章"),
        ("别提「甲「乙」丙，丁」。", "甲「乙」丙，丁"),
    ],
)
def test_a_bracket_run_recognizes_one_level_of_same_type_nesting(text, expected):
    """_zh_quoted_span_end depth scanning governs **ending stripping**, not matching itself.
    Bracket regexes also need nesting, or 《甲《乙》丙，丁》别提了。 disappears entirely (codex P2).
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == {expected}, _zh_terms(text)


def test_the_nested_branch_only_applies_to_asymmetric_pairs():
    """Symmetric nesting is meaningless ("a"b" cannot distinguish open/close), so add only for asymmetric pairs."""  # noqa: DOCSTRING_CJK
    for lo, hi in D._ZH_BRACKET_PAIRS:
        body = D._zh_bracket_body(lo, hi)
        if lo == hi:
            # Remove temper first: it already contains | for negation/verb alternatives,
            # so literal | searches mistake it for nesting alternatives.
            plain = body.replace(f"(?!{D._ZH_DIRECTIVE_AHEAD})", "")
            assert "|" not in plain.split("]", 1)[-1], (lo, body)
        else:
            import re as _re

            # Nested branches now also use temper (``lo(?!指令)[^…]``), rather than bare character classes.
            assert f"|{_re.escape(lo)}(?:(?!" in body, (lo, body)


def test_nesting_does_not_regress_the_bounded_scan():
    """Nested branches remain bounded; mutually exclusive branches introduce no ambiguous backtracking."""  # noqa: DOCSTRING_CJK
    from tests.wall_clock import fastest_run

    extract_directives(" ")  # Warm up.
    # ``"《" * 8000`` uses the same input and ten-second threshold as test_no_bracket_pair_scans_to_the_end;
    # Windows CI measured 7.37s. Fastest sampling filters occasional preemption, not an entire
    # slow runner, so one second falsely fails there (codex P2). This is only fallback; precise unbounded-bracket
    # criteria live in test_bracket_bodies_are_bounded (structure) and
    # test_unmatched_openers_stay_linear (ratios). This test's unique criterion is the nested case below.
    unmatched = fastest_run(lambda: extract_directives("《" * 8000), repeat=3, stop_below=10.0)
    nested = fastest_run(lambda: extract_directives("别提" + "《《a》" * 40), stop_below=0.2)
    assert unmatched < 10.0, unmatched
    assert nested < 0.2, nested


# ── 34. Four trim boundaries, all from one codex P2 round ──
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # If the ending itself is the whole topic, never strip, which would lose the entire instruction.
        ("stop saying please", "please"),
        ("stop saying porfa", "porfa"),
        ("please is off limits", "please"),
    ],
)
def test_a_tail_token_that_is_the_whole_topic_is_kept(text, expected):
    terms = {t for _loc, _kind, t in extract_directives(text)}
    assert expected in terms, terms


def test_a_tail_token_as_a_real_suffix_is_still_stripped():
    """Conversely strip the same token as a **suffix**, then discard by length if too short."""  # noqa: DOCSTRING_CJK
    assert not extract_directives("stop saying X porfa")
    assert not extract_directives("別再提錢please。")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Chinese rhetorical endings **do not follow fallback**: en/es/ru/pt syntax already delimits objects.
        ("stop saying 你好吗", "你好吗"),
        ("你好吗 is off limits", "你好吗"),
        ("no menciones 你好吗", "你好吗"),
        # Topics must be long enough to distinguish this; short cases are protected by the minimum-rejected ending also blocking its suffixes.
        ("stop saying 工作好吗", "工作好吗"),
        ("no menciones 工作好嗎", "工作好嗎"),
        ("工作好吗 is off limits", "工作好吗"),
    ],
)
def test_chinese_interrogatives_do_not_leak_into_other_templates(text, expected):
    terms = {t for _loc, _kind, t in extract_directives(text)}
    assert expected in terms, terms


def test_the_particle_fallback_still_applies_to_other_templates():
    """Conversely particles **need** fallback because mixed-language terms are often wholly Chinese."""  # noqa: DOCSTRING_CJK
    terms = {t for _loc, _kind, t in extract_directives("stop saying 前女友了")}
    assert "前女友" in terms, terms


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Once minimum length blocks a long ending, its **suffixes** must not strip either.
        ("别提钱好吗？", "钱好吗"),
        ("別提錢好嗎？", "錢好嗎"),
        ("别提猫可以吗？", "猫可以吗"),
        ("别提行吗。", "行吗"),
    ],
)
def test_a_floor_blocked_tail_also_blocks_its_suffixes(text, expected):
    """好吗 is blocked by minimum length because only 钱 remains; subsequent 吗 strips it into nonword 钱好."""  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == {expected}, _zh_terms(text)


def test_tail_tokens_are_tried_longest_first():
    """Short tokens often suffix longer ones; stripping short first bypasses the longer-token decision."""  # noqa: DOCSTRING_CJK
    import config.prompts.prompts_directives as _d
    import inspect

    src = inspect.getsource(_d._trim_term)
    assert "key=len, reverse=True" in src


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Lone symmetric quotes must not hide **following** brackets.
        ('别再提5"屏幕《你好吗》。', '5"屏幕《你好吗》'),
        ('別再提5"螢幕《你好嗎》。', '5"螢幕《你好嗎》'),
        # Paired quotes remain quotations.
        ('别再提"你的名字"好吗。', "你的名字"),
    ],
)
def test_an_unpaired_symmetric_quote_does_not_mask_later_delimiters(text, expected):
    assert _zh_terms(text) == {expected}, _zh_terms(text)


def test_the_quoted_span_scanner_ignores_odd_symmetric_delimiters():
    end = D._zh_quoted_span_end
    assert end('5"屏幕《你好吗》') == 9      # Ignore lone quotes while tracking brackets normally.
    assert end('5"屏幕好吗') == 0            # Only lone quotes means no quotation.
    assert end('"甲"好吗') == 3              # Actual pairs still count.


# Removed the enumerated Japanese-label left-boundary guard: negative criteria eliminated that table,
# replaced by test_the_clause_start_boundary_needs_no_enumeration and
# test_the_left_boundary_is_a_negated_class_not_an_enumeration.


# ── 35. Pause punctuation between preposed topics and triggers ──
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("工作，别提了。", "工作"),
        ("工作，別提了。", "工作"),
        ("工作、别提了。", "工作"),
        ("工作,别提了。", "工作"),
        ("功成名就，别提了。", "功成名就"),
        ("关于工作，就别提了。", "工作"),
        ("關於工作，就別提了。", "工作"),
    ],
)
def test_a_pause_before_the_trigger_is_consumed(text, expected):
    """Pause punctuation appears on **both sides**: post-verbal objects were fixed previously, but preposed topics
    were missed. Topic classes exclude ， while templates 2/4 allowed only whitespace, yielding zero matches (codex P2).
    Both sides use the same constant.
    """  # noqa: DOCSTRING_CJK
    assert expected in _zh_terms(text), _zh_terms(text)


def test_the_separator_is_shared_by_both_sides():
    """Both sides share one constant; independently maintaining the same thing inevitably drifts (#2655)."""  # noqa: DOCSTRING_CJK
    sources = _zh_pattern_sources()
    # Two template-2 sites now use _ZH_PAUSE_THEN_JIU, sharing punctuation and post-pause 就.
    # Count is four, not three: optional pause after 就 is **exactly** _ZH_TOPIC_SEPARATOR,
    # also present in template 2's string. These constants should share a source, making overlap expected.
    assert sum(D._ZH_TOPIC_SEPARATOR in raw for raw in sources) == 4
    assert sum(D._ZH_PAUSE_THEN_JIU in raw for raw in sources) == 1
    assert D._ZH_TOPIC_SEPARATOR in D._ZH_PAUSE_THEN_JIU
    # Both constants must derive from the **same** string, not independent copies; adding semicolons
    # exposed the issue when only one copy changed.
    klass = f"[{D._ZH_PAUSE_CHARS}]"
    # _ZH_PAUSE_THEN_JIU has **two** punctuation classes, before and after 就
    # (``工作，这件事，就，别提了。``).
    assert D._ZH_PAUSE_THEN_JIU.count(klass) == 2
    assert D._ZH_TOPIC_SEPARATOR.count(klass) == 1
    # Include clause punctuation but deliberately exclude sentence terminators 。！？, preventing cross-sentence instruction binding.
    assert set(D._ZH_PAUSE_CHARS) == set("，、：；,:;")
    assert not set(D._ZH_PAUSE_CHARS) & set("。！？.!?")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Conversely pause punctuation must not merge two independent instructions.
        ("工作，别提加班。", {"加班"}),
        ("别提工作，别提加班。", {"工作", "加班"}),
    ],
)
def test_the_pause_does_not_merge_two_directives(text, expected):
    assert _zh_terms(text) == expected, _zh_terms(text)


# ── 36. Template 3 also preserves 的事 / ASCII endings ignore case ──
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("我沒心情聊我們的事。", "我們的事"),
        ("我不願意聊我們的事。", "我們的事"),
        ("我懶得聊我們的事。", "我們的事"),
        ("我不想聊我们的事", "我们的事"),
        ("我不想再提工作的事了。", "工作的事"),
    ],
)
def test_the_reluctance_template_keeps_deshi_too(text, expected):
    """All four templates now agree: 的事 is possession plus nominalization and can belong to names.
    Templates 1/2/4 removed it separately; template 3's copy was found last (codex P2).
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == {expected}, _zh_terms(text)


def test_no_zh_template_consumes_deshi_after_the_topic():
    """Automatic discovery: any template reintroducing 的事 as an optional suffix fails here."""  # noqa: DOCSTRING_CJK
    for index, raw in enumerate(_zh_pattern_sources()):
        assert "(?:的事)?" not in raw, index
        assert "了|的事" not in raw, index


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("别再提工作PLEASE。", "工作"),
        ("別再提工作 PLEASE。", "工作"),
        ("我不願意說工作 PLEASE。", "工作"),
        ("别再提工作please。", "工作"),
        ("別再提工作 please。", "工作"),
    ],
)
def test_ascii_tails_are_matched_case_insensitively(text, expected):
    """Templates compile with IGNORECASE, so matching could succeed while ending stripping fails (codex P2)."""  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == {expected}, _zh_terms(text)


@pytest.mark.parametrize(
    ("text", "expected"),
    [("stop saying Please", "Please"), ("stop saying PORFA", "PORFA")],
)
def test_case_insensitive_matching_does_not_change_the_stored_casing(text, expected):
    """Lowercase comparison decides stripping only; preserve original term case."""  # noqa: DOCSTRING_CJK
    terms = {t for _loc, _kind, t in extract_directives(text)}
    assert expected in terms, terms


# ── 37. ASCII {} <> / post-filler pauses / negative left-boundary criteria ──
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("{Hello, World}别提了。", "Hello, World"),
        ("<Hello, World>别提了。", "Hello, World"),
        ("{Hello, World}別提了。", "Hello, World"),
        ("别提{Hello, World}了。", "Hello, World"),
    ],
)
def test_all_ascii_delimiters_that_trim_strips_are_also_paired(text, expected):
    assert expected in _zh_terms(text), _zh_terms(text)


def test_every_trimmed_delimiter_pair_is_in_the_bracket_table():
    """Automatic discovery: every ASCII bracket stripped by _TRIM_TRAIL must appear in the pair table;
    disagreement makes internal punctuation hard boundaries (codex P2 separately reported [], {}, <>).
    """  # noqa: DOCSTRING_CJK
    paired = {ch for pair in D._ZH_BRACKET_PAIRS for ch in pair}
    for opener, closer in (("(", ")"), ("[", "]"), ("{", "}"), ("<", ">")):
        if opener in D._TRIM_TRAIL and closer in D._TRIM_TRAIL:
            assert opener in paired and closer in paired, (opener, closer)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("工作，这件事，别提了。", "工作"),
        ("工作，這件事，別提了。", "工作"),
        ("关于工作，这件事，就别提了。", "工作"),
        ("關於工作，這件事，就別提了。", "工作"),
    ],
)
def test_a_pause_after_the_filler_is_consumed_too(text, expected):
    """Pause punctuation can appear **multiple times**: 工作，这件事，别提了。 places it on both filler sides.
    Accepting only the first restarts matching after the first comma and stores 这件事 (codex P2).
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == {expected}, _zh_terms(text)


@pytest.mark.parametrize(
    "text",
    [
        "モデルβ別提案をお願いします。",
        "モデルＸ別提案をお願いします。",
        "ΑΒΓ別提案をお願いします。",
        "Модель別提案をお願いします。",
    ],
)
def test_the_clause_start_boundary_needs_no_enumeration(text):
    """This dimension failed three times: blocking Han missed Katakana, adding Kana missed Latin/digits/closers,
    then β escaped. Character sets are open; enumeration always misses something.

    Use a **negative criterion**: Chinese instruction negations always **begin clauses**, so left neighbors must be start, whitespace, or
    clause punctuation. Other scripts' letters, digits, and closers indicate 別 attaches after a word.
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == set(), _zh_terms(text)


def test_the_left_boundary_is_a_clause_start_class_not_an_enumeration():
    """Structure: require left neighbors that begin clauses, never enumerate Japanese-label characters.

    Left boundaries now apply **only to ambiguous verbs + 再** (提 / 講 / 談 / 討論). Verbs
    absent from Japanese disambiguate themselves and bypass left boundaries.
    """  # noqa: DOCSTRING_CJK
    assert not hasattr(D, "_ZH_JA_LABEL_TAIL")
    assert "一-鿿" not in D._ZH_CLAUSE_START_LEFT
    assert (
        f"(?<![^{D._ZH_CLAUSE_START_LEFT}{D._ZH_POLITE_BEFORE_NEG}])"
        in D._ZH_NEG_VERB_EVIDENCE
    )
    # Honorifics / subjects pass **only this branch**, where 再 disambiguates; never leak elsewhere.
    assert D._ZH_POLITE_BEFORE_NEG == D._ZH_SUBJECT_CHARS + "請"
    assert "請" not in D._ZH_SUBJECT_CHARS
    # Conversely preceding characters that can begin clauses still pass normally.
    # Distinguishing this table's size requires both premises below; missing either makes it untestable:
    # 1. Topic contains **Japanese grammar** (君の名は). Pure Chinese topics already fail the guard's third criterion,
    #    so the guard never activates and shrinking its left-boundary table changes nothing.
    # 2. Negation uses **traditional 別**. Simplified 别 itself belongs to _ZH_EVIDENCE_CHARS and is rescued
    #    earlier by single-character evidence. Traditional 別 shares Japanese code points and cannot enter that table,
    #    leaving structural evidence as its sole protection, precisely the users this PR serves.
    # 3. Use an **ambiguous** verb (提), or another branch bypasses this left boundary entirely.
    for text in ("算了，別再提君の名は。", "算了。別再提君の名は。", "工作、別再提君の名は。"):
        assert _zh_terms(text) == {"君の名は"}, text
    assert _zh_terms("算了，别提工作。") == {"工作"}
    assert _zh_terms("别再提工作。") == {"工作"}


# ── 38. Pauses after 关于 / 就 after pauses / comma parity / 嘍囉 / openers / lone ASCII ──
#
# Read this round's six interdependent changes together: first four concern preposed topics + pauses,
# last two bracket tables.


@pytest.mark.parametrize(
    "text",
    [
        "关于，工作，就别提了。",
        "關於，工作，就別提了。",
        "关于、工作就别提了。",
        "关于：工作就别提了。",
        "关于 ，工作就别提了。",
        "关于，工作，这件事，就别提了。",
    ],
)
def test_a_pause_right_after_the_topic_introducer_is_consumed(text):
    """Topic introducers also precede pauses: 关于，工作，就别提了。 (codex P2)."""  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == {"工作"}, _zh_terms(text)


@pytest.mark.parametrize("pause", ["，", "、", "：", ",", ":"])
def test_a_pause_before_jiu_lets_the_preposed_template_eat_it(pause):
    """Consume 就 after pauses: 工作，就别提了。 already matches on parent."""  # noqa: DOCSTRING_CJK
    assert _zh_terms(f"工作{pause}就别提了。") == {"工作"}
    assert _zh_terms(f"工作{pause}这件事{pause}就别提了。") == {"工作"}


@pytest.mark.parametrize("term", ["成就", "迁就", "功成名就", "将就", "迁就"])
def test_without_a_pause_no_jiu_is_eaten(term):
    """Without pauses, consume nothing: template 2 covers all X别提了, including words ending 就."""  # noqa: DOCSTRING_CJK
    assert _zh_terms(f"{term}别提了。") == {term}


def test_the_jiu_slot_lives_inside_the_pause_branch():
    """Structure: 就 must remain inside pause branches, never independently optional.

    Independent optionality consumes without pauses, immediately truncating 成就别提了。 to 成.
    This assertion is stronger than examples above: moving (?:就)? outside parentheses fails
    even when no existing example happens to cover the changed behavior.
    """  # noqa: DOCSTRING_CJK
    # Removing pause-punctuation branches must make 就 wholly unreachable, reducing this pattern to empty matching.
    assert D._ZH_PAUSE_THEN_JIU.startswith("(?:[")
    assert D._ZH_PAUSE_THEN_JIU.endswith(")?")
    inner = D._ZH_PAUSE_THEN_JIU[len("(?:") : -len(")?")]
    assert inner.startswith(f"[{D._ZH_PAUSE_CHARS}]"), inner
    # 就 follows punctuation classes, so consuming a pause is mandatory before it becomes reachable.
    assert inner.index("就") > inner.index("]")


def test_the_preposed_topic_never_spans_a_full_width_comma():
    """Deliberately exclude ， from preposed-topic classes, even at the cost of storing discourse adverbs when structure is unclear.

    工作，还是别提了。 stores 还是, with 就是 / 那就 / 最好 / 反正 /
    以后 / 咱们 forming an open family no enumeration exhausts. Ugly, but admitting ， contradicts
    test_template2_prefix_never_spans_a_sentence_boundary:
    算了，工作别提了。 would store 算了，工作. Both have identical X，Y别提了 structure,
    differing only lexically in which half is the topic. No structural criterion separates them; retain the previously chosen right half.
    This test records that decision to prevent repeated reversals.
    """  # noqa: DOCSTRING_CJK
    excluded = set(re.findall(r"\[\^([^\]]+)\]", D._ZH_PLAIN_CHAR)[0])
    assert {"，", ","} <= excluded
    assert not hasattr(D, "_ZH_PLAIN_CHAR_PAUSE_OK")
    assert _zh_terms("算了，工作别提了。") == {"工作"}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("別再提小嘍囉。", "小嘍囉"),
        ("别再提小喽啰。", "小喽啰"),
        ("我沒心情聊小嘍囉。", "小嘍囉"),
        ("我没心情聊小喽啰。", "小喽啰"),
        ("關於小嘍囉就別提了。", "小嘍囉"),
    ],
)
def test_luoluo_is_a_word_not_a_final_particle(text, expected):
    """囉 / 啰 also end words (嘍囉 / 喽啰); unconditional stripping creates nonword 小嘍.

    Parent simplified behavior already preserved 小喽啰, regressed by this PR (codex P2).
    Same tradeoff as 耶 / 捏: prefer an extra character over a missing one.
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == {expected}, _zh_terms(text)


def test_the_ambiguous_tail_glyphs_are_absent_from_both_tables():
    """Structure: both tables must omit 囉 / 啰; leaving either still truncates."""  # noqa: DOCSTRING_CJK
    for glyph in ("囉", "啰"):
        assert glyph not in D._TRIM_TRAIL_TOKENS_BY_LOCALE["zh"], glyph
        assert glyph not in D._ZH_FINAL_PARTICLES, glyph


def test_every_allowed_final_particle_can_also_be_trimmed():
    """Guard duplicate handwritten lists against drift: every regex-accepted particle must belong to trim tables.

    The converse is unnecessary: larger trim tables can remove extra characters captured into terms. But
    regex-only particles remain in terms forever.
    """  # noqa: DOCSTRING_CJK
    allowed = set(D._ZH_FINAL_PARTICLES.rsplit("[", 1)[1].split("]")[0])
    assert len(allowed) > 5, allowed
    # Inspect the **regex itself**, not its constant, or tests merely repeat derivation. Pin both
    # together here so changing only regex or constant also fails.
    assert allowed == set(D._ZH_FINAL_PARTICLE_CHARS)
    trimmable = set(D._TRIM_TRAIL_TOKENS_BY_LOCALE["zh"])
    assert allowed <= trimmable, allowed - trimmable


def test_zh_only_particles_are_derived_from_the_regex_particles():
    """Separate handwritten copies once let regexes consume 啊 齁 欸 誒 absent from evidence, losing entire traditional
    instructions (codex P2). Exclude 了 separately because it is common Japanese Kanji (終了 / 了解)."""  # noqa: DOCSTRING_CJK
    assert set(D._ZH_ZH_ONLY_FINAL_PARTICLES) == (
        set(D._ZH_FINAL_PARTICLE_CHARS) - {"了"}
    ) | {"囉", "啰"}
    assert "了" not in D._ZH_EVIDENCE_CHARS
    # Regex accepts but evidence rejects means that particle loses entire traditional instructions; permit none.
    for char in D._ZH_FINAL_PARTICLE_CHARS:
        if char == "了":
            continue
        assert "君の名は" in _zh_terms(f"別提君の名は{char}。"), char


@pytest.mark.parametrize(
    ("opener", "closer"), sorted(D._ZH_CLOSE_FOR_OPEN.items())
)
def test_an_opening_delimiter_can_start_a_clause(opener, closer):
    """Recognize traditional instructions inside quotes too: 「別再提君の名は。」 (codex P2).

    Japanese 〜別 suffixes cannot immediately follow **opening** brackets; admitting openers does not
    dismantle the guard because matching **closers** remain blocked (next test).
    Probe with 別再提 (**ambiguous** verb + 再), the only branch using left boundaries now;
    switching to Japanese-absent verbs like 別聊 takes another branch and tests nothing here.
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(f"{opener}別再提君の名は。{closer}") == {"君の名は"}


@pytest.mark.parametrize(
    ("opener", "closer"), sorted(D._ZH_CLOSE_FOR_OPEN.items())
)
def test_a_closing_delimiter_still_blocks_the_japanese_label(opener, closer):
    """**Closing** brackets block Japanese labels such as 「地域」別提案; permit no misses."""  # noqa: DOCSTRING_CJK
    assert _zh_terms(f"{opener}地域{closer}別再提案をお願いします。") == set()
    assert _zh_terms(f"{opener}地域{closer}別提案をお願いします。") == set()


def test_symmetric_delimiters_are_not_treated_as_clause_starters():
    """Symmetric quotes serve both roles; admitting them dismantles the guard ("地域"別提案)."""  # noqa: DOCSTRING_CJK
    for delim in D._ZH_SYMMETRIC_DELIMS:
        assert delim not in D._ZH_CLAUSE_START_LEFT, delim
        assert _zh_terms(f"{delim}地域{delim}別再提案をお願いします。") == set(), delim
    for closer in set(D._ZH_CLOSE_FOR_OPEN.values()):
        assert closer not in D._ZH_CLAUSE_START_LEFT, closer


def test_the_clause_start_boundary_lists_every_asymmetric_opener():
    """Structure: admit the entire opening-bracket set, not selected entries; discover automatically."""  # noqa: DOCSTRING_CJK
    for opener in D._ZH_CLOSE_FOR_OPEN:
        assert opener in D._ZH_CLAUSE_START_LEFT, opener


@pytest.mark.parametrize(
    "opener", sorted(o for o in D._ZH_CLOSE_FOR_OPEN if o.isascii())
)
def test_an_unmatched_ascii_opener_is_an_operator_not_a_title(opener):
    """Lone ASCII openers are comparison signs/code fragments, not unfinished title brackets.

    Otherwise 好吗 in 别再提价格<预算好吗？ can never strip (codex P2).
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(f"别再提价格{opener}预算好吗？") == {f"价格{opener}预算"}


@pytest.mark.parametrize(
    "opener", sorted(o for o in D._ZH_CLOSE_FOR_OPEN if not o.isascii())
)
def test_an_unmatched_fullwidth_opener_is_still_a_truncated_title(opener):
    """Chinese fullwidth openers introduce quotations; lone ones imply truncated titles, protecting particles from stripping."""  # noqa: DOCSTRING_CJK
    assert _zh_terms(f"别再提电影{opener}好不好") == {f"电影{opener}好不好"}


# ── 39. String-initial 別 compounds / lone ASCII brackets hiding later quotations ──


@pytest.mark.parametrize(
    "text",
    [
        "別提案をお願いします。",
        "別提案について説明します。",
        "別談話でも可。",
        "別提案。",
    ],
)
def test_a_clause_initial_bie_compound_stays_behind_the_japanese_guard(text):
    """Japanese 別 is also a **prefix** (別提案 = another proposal), often sentence-initial.

    Empty initial lookbehind makes structural evidence short-circuit the Japanese guard, persisting
    案をお願いします (codex P2). Like X，Y别提了, no structural criterion separates
    Chinese 別提〈Japanese title〉 from Japanese 別提案… at string start. Choose by consequences:
    persisting Japanese fragments as forbidden topics is much worse than missing one trigger.
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == set(), _zh_terms(text)


@pytest.mark.parametrize(
    "text",
    [
        # Ambiguous verbs (提 / 講 / 談 / 討論) rely on 再 for disambiguation.
        "別再提君の名は。",
        "「別再提君の名は。」",
        "算了，別再提君の名は。",
        "我們別再提君の名は。",
        # Verbs absent from Japanese (聊 / 扯 / 說) disambiguate themselves in any position.
        "別聊君の名は。",
        "別扯君の名は。",
        "別說君の名は。",
        "請別扯君の名は。",
        "算了，別聊君の名は。",
        # Chinese topics prevent the Japanese guard from activating at all.
        "別提工作。",
        "別再提工作。",
    ],
)
def test_the_traditional_directive_still_works_where_it_is_unambiguous(text):
    """Narrow that cost to ambiguous verbs **without 再**.

    再 disambiguates: 別再提 is invalid Japanese, which needs 別の再提案. Verbs also disambiguate:
    聊 / 扯 / 說 cannot form Japanese 別X compounds (Japanese writes 説 for 說,
    and has no 扯), so require neither left boundaries nor 再 for them.
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text), text


def test_the_ambiguous_verb_branch_needs_both_the_boundary_and_zai():
    """Both ambiguous-verb conditions are essential, each blocking a Japanese family.

    Left boundaries block 地域別再提案 (〜別 **suffix**); 再 blocks 別提案 (別
    **prefix**). Both occur genuinely in this file's ja corpus.
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms("地域別再提案をお願いします。") == set()
    assert _zh_terms("地域別提案をお願いします。") == set()
    assert _zh_terms("今回は、別提案をお願いします。") == set()
    assert _zh_terms("「別提案をお願いします。」") == set()
    # Structure: ambiguous verbs appear only in branches with both left boundaries **and** 再.
    flat = D._ZH_NEG_VERB_EVIDENCE.replace(r"\s*", "").replace(D._ZH_HSPACE, "")
    shared = "|".join(D._ZH_SAY_VERBS_JA_SHARED)
    assert flat.count(shared) == 1, flat
    head = flat.split(shared)[0]
    assert head.endswith("(?:%s)再(?:" % "|".join(D._ZH_NEG_JA_AMBIGUOUS)), head
    assert head[head.rindex("(?<!"):].startswith(
        f"(?<![^{D._ZH_CLAUSE_START_LEFT}{D._ZH_POLITE_BEFORE_NEG}])"
    ), head


@pytest.mark.parametrize(
    "opener", sorted(o for o in D._ZH_CLOSE_FOR_OPEN if o.isascii())
)
def test_an_unmatched_ascii_opener_does_not_mask_a_later_quote(opener):
    """Lone ASCII openers at the stack bottom prevent tracking **later** valid quotation closers.

    In 别再提价格<预算《你好吗》。, 《你好吗》 closes uselessly; 好吗 strips as sentence
    tone and truncates the title to 《你 (parent intact, codex P2). Rescan lone
    ASCII openers as **ordinary characters**, rather than clearing the stack afterward when positions are already lost.
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(f"别再提价格{opener}预算《你好吗》。") == {
        f"价格{opener}预算《你好吗》"
    }
    # Still strip endings outside quotations.
    assert _zh_terms(f"别再提价格{opener}预算《你好吗》好吗？") == {
        f"价格{opener}预算《你好吗》"
    }


@pytest.mark.parametrize(
    ("lone", "opener", "closer"),
    [
        (lone, opener, closer)
        for lone in sorted(o for o in D._ZH_CLOSE_FOR_OPEN if o.isascii())
        for opener, closer in sorted(D._ZH_CLOSE_FOR_OPEN.items())
        if opener.isascii() and opener != lone
    ],
)
def test_the_rescan_only_ignores_the_unmatched_positions(lone, opener, closer):
    """Rescanning ignores only **unpaired indices**; paired ASCII brackets still mark quoted spans.

    This is the sole case distinguishing the shortcut of ignoring all ASCII openers during rescan:
    require both an unmatched opener triggering rescan and a pair protecting its content; the paired span
    must end in an ambiguous token to expose misclassification as sentence tone.
    """  # noqa: DOCSTRING_CJK
    text = f"别再提价格{lone}预算{opener}你好吗{closer}。"
    assert _zh_terms(text) == {f"价格{lone}预算{opener}你好吗{closer}"}, _zh_terms(text)


def test_the_quoted_span_end_boundaries():
    """Three direct index assertions for _zh_quoted_span_end."""  # noqa: DOCSTRING_CJK
    assert D._zh_quoted_span_end("价格<预算《你好吗》") == len("价格<预算《你好吗》")
    assert D._zh_quoted_span_end("《你好吗》好吗") == len("《你好吗》")
    # Lone fullwidth openers still extend through the end.
    assert D._zh_quoted_span_end("电影《好不好") == len("电影《好不好")


# ── 40. Deliberately permit sentence punctuation inside symmetric quotes ──


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Preserve real titles containing periods/commas; parent truncated both, fixed by this PR.
        ('别再提"Everything. Everywhere"。', "Everything. Everywhere"),
        ('别再提"Everything, Everywhere"。', "Everything, Everywhere"),
        ('别再提"你好。世界"好吗？', "你好。世界"),
        ('我不想聊"工作。加班"。', "工作。加班"),
        # Excluding punctuation in both preposed-topic templates produces nonwords here.
        ('"工作。加班"别提了。', "工作。加班"),
        ('关于"工作。加班"就别提了。', "工作。加班"),
    ],
)
def test_sentence_punctuation_stays_inside_symmetric_quotes(text, expected):
    """Symmetric-quote classes deliberately **permit** sentence punctuation, a measured choice rather than omission.

    Exclusion prevents lone inch marks pairing across sentences, but truncates real period-containing titles
    and produces nonwords under templates 2/4 (加班 / 加班"就, both worse than parent).
    Same tradeoff as 耶 / 捏 / 囉: retain extra spans rather than consume characters into nonwords.
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == {expected}, _zh_terms(text)


def test_the_symmetric_quote_guard_is_the_temper_not_a_punctuation_class():
    """Structure: use (?![别別]) temper, never exclude sentence punctuation from classes.

    Also pin against dead code in symmetric branches: banned += punctuation once appeared
    **after** unit assembly, looking like a guard but only modifying a dead variable (coderabbit).
    """  # noqa: DOCSTRING_CJK
    body = D._zh_bracket_body('"', '"')
    # Temper requires a **complete instruction** (negation + optional 再 + speech verb), not merely
    # a negation character. Characters are not the criterion: ordinary words often begin 不 / 莫 / 休 / 别
    # (不可思议 / 莫名其妙 / 休闲 / 告别版, codex P2).
    assert f"(?!{D._ZH_DIRECTIVE_AHEAD})" in body
    for punct in "。！？；":
        assert punct not in body, punct
    # Temper really does prevent two instructions from merging.
    assert _zh_terms('尺寸5"别提了。尺寸6"别提了。') == {"尺寸5", "尺寸6"}
    assert _zh_terms('尺寸5"别提了，尺寸6"别提了。') == {"尺寸5", "尺寸6"}


# ── 41. Comma + space / verb-level ambiguity / strip brackets only around whole terms ──


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Hello, World别提了。", "Hello, World"),
        ("Hello, World別提了。", "Hello, World"),
        ("关于Hello, World就别提了。", "Hello, World"),
        ("關於Hello, World就別提了。", "Hello, World"),
        # Spaceless forms already worked; this fixes their asymmetry with spaced forms.
        ("Hello,World别提了。", "Hello,World"),
    ],
)
def test_a_comma_followed_by_a_space_is_still_word_internal(text, expected):
    """Hello, World is the most common spelling, rejected by an unchanged no-whitespace right lookahead.

    Matching restarts after the comma: Hello, World别提了。 stores only World,
    关于Hello, World就别提了。 stores nonword World就 (both intact on parent;
    codex P2). Hello,World already works; fix this asymmetry.
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == {expected}, _zh_terms(text)


def test_the_full_width_comma_decision_is_untouched():
    """The preceding test does **not** overturn preposed topics never crossing clauses; that governs fullwidth ，."""  # noqa: DOCSTRING_CJK
    assert _zh_terms("算了，工作别提了。") == {"工作"}
    assert _zh_terms("说完了，工作别提了。") == {"工作"}
    # The tighter thousands-separator rule remains unchanged too.
    assert _zh_terms("价格1，000元别提了。") == {"价格1，000元"}
    # Bounded, not \s*: template 2/4 whitespace guards search for literal \s*, which must stay unambiguous.
    assert r"\s*" not in D._ZH_IDENT_PUNCT


@pytest.mark.parametrize(
    "text",
    [
        "別提案をお願いします。",
        "今回は、別提案をお願いします。",
        "「別提案をお願いします。」",
        "（別提案をお願いします。）",
        "今回は 別提案をお願いします。",
        "別談話でも可。",
        "今回は、別談話でも可。",
    ],
)
def test_a_japanese_bie_prefix_compound_is_suppressed_anywhere(text):
    """Ambiguity belongs to **verbs**, not positions.

    Three positional rounds blocked one case and missed the next: Han left-neighbor checks missed Kana/β;
    clause-start checks missed string start; string-start 再 requirements missed clause boundaries
    (今回は、別提案… / 「別提案…」, codex P2). Position is not the criterion.
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == set(), _zh_terms(text)


@pytest.mark.parametrize("verb", sorted(D._ZH_ZH_ONLY_VERBS))
def test_a_verb_japanese_does_not_have_needs_no_disambiguation(verb):
    """Verbs unable to form Japanese 別X compounds need neither left boundaries nor 再.

    Otherwise 別聊君の名は。 / 請別扯君の名は。 yield zero matches while equivalent simplified
    wording survives through 别 in _ZH_EVIDENCE_CHARS (codex P2).
    請 cannot enter subject allowlists because it is Japanese Kanji, leaving verb disambiguation essential.
    **Derive** from _ZH_ZH_ONLY_VERBS, never manually list: the manual version omitted compound
    讨论 (coderabbit). Future verb additions automatically gain coverage here.
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(f"別{verb}君の名は。") == {"君の名は"}
    assert _zh_terms(f"請別{verb}君の名は。") == {"君の名は"}


def test_the_shared_verb_table_is_pinned_and_partitions_the_verbs():
    """Structure: the shared-verb table is closed and must be a proper subset of all speech verbs.

    Adding inappropriate characters immediately loses corresponding traditional phrases; omitting appropriate ones
    admits corresponding Japanese compounds. Assert **equality**, not containment.
    """  # noqa: DOCSTRING_CJK
    assert D._ZH_SAY_VERBS_JA_SHARED == ("提", "講", "談", "討論")
    everything = set(D._ZH_SAY_COMPOUNDS + D._ZH_SAY_VERBS)
    assert set(D._ZH_SAY_VERBS_JA_SHARED) < everything
    assert set(D._ZH_ZH_ONLY_VERBS) == everything - set(D._ZH_SAY_VERBS_JA_SHARED)
    # Shared tables must contain no simplified forms: Japanese does not use simplified Chinese characters.
    for verb in D._ZH_SAY_VERBS_JA_SHARED:
        assert verb not in ("说", "讲", "谈", "讨论"), verb


@pytest.mark.parametrize(
    ("opener", "closer"), sorted(D._ZH_CLOSE_FOR_OPEN.items())
)
def test_a_closer_survives_when_the_title_is_only_a_suffix(opener, closer):
    """Strip brackets only when **truly enclosing the entire term**.

    Unconditional strip-character inclusion removes closing brackets when titles merely suffix terms:
    别再提电影〈你好〉。 becomes 电影〈你好 (parent intact; 〈〉〔〕［］〖〗
    were newly added to _TRIM_TRAIL in this PR, codex P2).
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(f"别再提电影{opener}你好{closer}。") == {
        f"电影{opener}你好{closer}"
    }


@pytest.mark.parametrize(
    ("opener", "closer"), sorted(D._ZH_CLOSE_FOR_OPEN.items())
)
def test_a_pair_wrapping_the_whole_term_is_still_peeled(opener, closer):
    """Conversely strip brackets normally when one pair encloses the entire term."""  # noqa: DOCSTRING_CJK
    assert _zh_terms(f"别再提{opener}你好{closer}。") == {"你好"}


@pytest.mark.parametrize(
    ("opener", "closer"), sorted(D._ZH_CLOSE_FOR_OPEN.items())
)
def test_a_lone_closer_is_still_stripped(opener, closer):
    """Second converse: still strip lone closers without matching openers in the term."""  # noqa: DOCSTRING_CJK
    assert _zh_terms(f"别再提你好{closer}。") == {"你好"}


@pytest.mark.parametrize(
    "opener", sorted(o for o in D._ZH_CLOSE_FOR_OPEN if o.isascii())
)
def test_a_lone_opener_of_the_same_type_still_shields_the_inner_run(opener):
    """Only **same-shaped** unmatched and paired openers distinguish rescan scope.

    别再提<预算<你好吗>。 has unmatched outer < and paired inner <…>. Ignoring
    all ASCII openers instead of only unmatched indices removes inner quotation protection,
    stripping 好吗 as sentence tone and storing 预算<你.
    Previous pairwise unmatched × paired combinations excluded identical shapes, missing this case.
    """  # noqa: DOCSTRING_CJK
    closer = D._ZH_CLOSE_FOR_OPEN[opener]
    text = f"别再提{opener}预算{opener}你好吗{closer}。"
    assert _zh_terms(text) == {f"预算{opener}你好吗"}, _zh_terms(text)


def test_symmetric_quotes_need_no_dedicated_peel():
    """Behavior: generic strip removes symmetric quotes at both ends; no dedicated branch needed.

    Deliberately no assertion against reintroducing that dead branch: it has no behavior,
    which makes its mutation equivalent, so no behavioral assertion can distinguish it.
    Source-text checks also fail in either direction: equivalent ``s[0] == s[-1] == '"'`` remains green,
    while mentioning constants in implementation comments falsely fails (coderabbit). Put the criterion in _strip_trail's
    comment, where it belongs.
    """  # noqa: DOCSTRING_CJK
    for delim in D._ZH_SYMMETRIC_DELIMS:
        assert delim in D._TRIM_TRAIL, delim
    assert _zh_terms('别再提"Everything, Everywhere"。') == {"Everything, Everywhere"}
    assert _zh_terms('别再提"你好吗"。') == {"你好吗"}


def test_the_counterpart_table_covers_every_asymmetric_pair():
    """Structure: _ZH_COUNTERPART must include both directions, discovered from bracket tables."""  # noqa: DOCSTRING_CJK
    for opener, closer in D._ZH_CLOSE_FOR_OPEN.items():
        assert D._ZH_COUNTERPART[opener] == closer
        assert D._ZH_COUNTERPART[closer] == opener
    # Deliberately exclude symmetric pairs from this table: one shape serves both ends, preventing detection of an absent counterpart.
    for delim in D._ZH_SYMMETRIC_DELIMS:
        assert delim not in D._ZH_COUNTERPART, delim


# ── 42. Semicolons as pauses / subjects across spaces ──


@pytest.mark.parametrize("pause", ["；", ";"])
def test_a_semicolon_is_a_pause_like_any_other(pause):
    """Semicolons were excluded from topics as terminators but absent from pause tables.

    Thus 别再提；工作。 / 工作；别提了。 had zero matches while parent stored
    工作 (codex P2). Cover all four templates.
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(f"别再提{pause}工作。") == {"工作"}
    assert _zh_terms(f"別再提{pause}工作。") == {"工作"}
    assert _zh_terms(f"工作{pause}别提了。") == {"工作"}
    assert _zh_terms(f"關於工作{pause}就別提了。") == {"工作"}
    assert _zh_terms(f"我不想聊{pause}工作。") == {"工作"}


def test_sentence_terminators_are_not_pauses():
    """Conversely deliberately exclude 。！？ from pauses, preventing cross-**sentence** instruction binding."""  # noqa: DOCSTRING_CJK
    assert not set(D._ZH_PAUSE_CHARS) & set("。！？.!?")
    # Residue from preceding sentences must not become topics.
    assert _zh_terms("算了。别提工作。") == {"工作"}


@pytest.mark.parametrize("subject", list("你妳您咱请們"))
@pytest.mark.parametrize("gap", [" ", "  ", "\t"])
def test_an_allowlisted_subject_survives_intervening_whitespace(subject, gap):
    """Spaces may separate subjects/negations, while the guard checked only the immediately preceding character.

    你 別提君の名は。 had zero matches while simplified equivalents survived through 别 in
    _ZH_EVIDENCE_CHARS, another script asymmetry (codex P2).
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(f"{subject}{gap}別提君の名は。") == {"君の名は"}


@pytest.mark.parametrize("subject", ["我", "他", "請"])
def test_a_japanese_kanji_subject_still_does_not_open_the_guard(subject):
    """Wider left context must **not** broaden subject allowlists.

    我 / 他 / 請 are Japanese Kanji; admitting them persists sentences such as 他別提案をお願いします。
    as Chinese. Include only forms wholly absent from Japanese.
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(f"{subject} 別提君の名は。") == set()
    assert _zh_terms(f"{subject}別提君の名は。") == set()


@pytest.mark.parametrize(
    "text",
    [
        "地域別提案をお願いします。",
        "カテゴリ別提案書。",
        "世代別講座で話します。",
        "テーマ別討論スレ",
        "個別提案をお願いします。",
    ],
)
def test_widening_the_left_context_does_not_leak_evidence(text):
    """Wider left context serves **only** subject evidence; character-class evidence still checks one adjacent character.

    Widening the latter lets Chinese from preceding sentences short-circuit the entire Japanese guard.
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == set(), _zh_terms(text)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Prior-sentence Chinese evidence falls in widened left context but is **not** adjacent to the Japanese sentence.
        ("这话题。地域別提案をお願いします。", set()),
        ("別提这个。世代別講座で話します。", {"这个"}),
    ],
)
def test_evidence_still_reads_only_the_adjacent_character(text, expected):
    """The sole form distinguishing widened-left-context character evidence.

    **Cross a sentence boundary**: adjacent evidence works either way; evidence beyond _ZH_SUBJECT_LEFT_MAX
    also reveals nothing. Place it inside the widened window but nonadjacent.
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == expected, _zh_terms(text)


# ── 43. Periods are not internal punctuation / Japanese existential predicates ──


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # ``. `` ends English sentences; never consume the entire preceding sentence as a topic.
        ("That was bad. Work别提了。", "Work"),
        ("I am sad. Work别提了。", "Work"),
        # Commas still permit one space.
        ("Hello, World别提了。", "Hello, World"),
        ("关于Hello, World就别提了。", "Hello, World"),
        # Periods without whitespace remain internal punctuation.
        ("版本v1.2别提了。", "版本v1.2"),
    ],
)
def test_only_the_comma_tolerates_a_following_space(text, expected):
    """Allow whitespace for commas, never periods.

    Same reasoning as excluding 。！？ from _ZH_PAUSE_CHARS this round: they end **sentences**.
    Initially applying one shortcut to all [.,] manufactured an inconsistency
    (coderabbit).
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == {expected}, _zh_terms(text)


def test_the_two_identifier_punctuations_have_different_lookaheads():
    """Structure: express these two symbols separately, never merge them back into one class."""  # noqa: DOCSTRING_CJK
    # Space criteria now derive from _ZH_HSPACE_ONE (including pasted NBSP) as a **bounded run**,
    # not one character: pasted titles often contain two spaces (Dr.  Who). Comma whitespace is optional,
    # hence {0,n}; period branches require whitespace, hence {1,n}, otherwise v1.2's general criterion applies.
    # Optional branches must use {0,n}, never append ? after {1,n}, which makes a **lazy quantifier**.
    space = D._ZH_IDENT_GAP_OPT
    assert D._ZH_IDENT_GAP_OPT == D._ZH_HSPACE_ONE + "{0,8}"
    assert D._ZH_IDENT_GAP == D._ZH_HSPACE_ONE + "{1,8}"
    assert D._ZH_IDENT_PUNCT.count(space) == 1
    branches = [b for b in D._ZH_IDENT_PUNCT.split("|") if b.startswith("(?<=")]
    # Classify branches by the **character matched**, not punctuation present in character classes,
    # which already contain commas/periods. Inspect the segment **between** lookbehind and lookahead.
    def _matched(branch: str) -> str:
        return branch.split("])", 1)[1].split("(?=", 1)[0]

    dot = [b for b in branches if _matched(b) == chr(92) + "."]
    comma = [b for b in branches if _matched(b) == ","]
    assert len(dot) == 1 and len(comma) == 1, branches
    assert space not in dot[0], dot[0]
    # Dot here is the **general** v1.2 branch, forbidding surrounding whitespace; spaced abbreviation
    # branches start (?<![A-Za-z] and are excluded by branches filtering above.
    assert D._ZH_IDENT_GAP in D._ZH_ABBREV_PERIOD
    assert space in comma[0], comma[0]


@pytest.mark.parametrize(
    "text",
    [
        "別提案あり。",
        "別提案なし。",
        "今回は、別提案あり。",
        "「別提案あり。」",
        "別提案書あり。",
        "別講座あり。",
        "別談話なし。",
    ],
)
def test_japanese_existence_predicates_are_grammar_evidence(text):
    """Telegraphic Japanese may contain only Kana あり / なし, beyond all predicate-table entries.

    案あり then persists as a three-day Chinese ban (codex P2). Unlike unresolved 別提案書。,
    which is locally identical, this has a clean criterion: pure-Kana あり/なし
    cannot occur in Chinese.
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == set(), _zh_terms(text)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # なし prefixes なしくずし; without an end-of-word anchor it swallows the whole instruction.
        # This is the **only** form distinguishing that anchor, found by mutation testing:
        #   · Simplified 别… provides single-character evidence, bypassing the guard;
        #   · 別再提… / 別聊… provides structural evidence, also bypassing it;
        #   · ありがとう already contains が / と in the single-character particle class, yielding Japanese either way.
        # Only traditional + shared verb + no 再 + topic beginning なし without single-character particles
        # reaches the predicate table.
        ("別提なしくずし。", "なしくずし"),
        ("别再提なしくずし。", "なしくずし"),
        ("别再提ありがとう。", "ありがとう"),
        ("別再提ありがとう。", "ありがとう"),
        # Do not reject cases already rescued by parent.
        ("别再提ドラえもん。", "ドラえもん"),
        ("別叫我「お兄ちゃん」。", "お兄ちゃん"),
    ],
)
def test_the_existence_predicates_are_anchored_at_the_end(text, expected):
    """Anchor existential predicates at word ends: Japanese predicate usage is always sentence-final, preserving recall,
    while unanchored forms reject proper nouns beginning with the same Kana.
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == {expected}, _zh_terms(text)


# ── 44. Subjects never cross lines / rescan after stripping / predicate left boundaries / bare だ ──


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
def test_subject_evidence_does_not_leak_across_lines(newline):
    """\\s consumes newlines too; an allowlisted character at the previous line's end can disable the next line's guard.

    "你\\n別提案をお願いします。" persists 案をお願いします (codex P2).
    Subjects and their predicates cannot cross lines, so accept horizontal whitespace only.
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(f"你{newline}別提案をお願いします。") == set()


@pytest.mark.parametrize("gap", [" ", "\t", "　", "  "])
def test_horizontal_gaps_still_carry_the_subject(gap):
    """Conversely preserve horizontal whitespace on one line (ASCII / Tab / fullwidth spaces)."""  # noqa: DOCSTRING_CJK
    assert _zh_terms(f"你{gap}別提君の名は。") == {"君の名は"}


def test_the_subject_gap_is_horizontal_only():
    """Structure: never use \\s here again."""  # noqa: DOCSTRING_CJK
    assert r"\s" not in D._ZH_SUBJECT_GAP
    assert D._ZH_SUBJECT_GAP in D._ZH_SUBJECT_ACROSS_SPACE.pattern


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("我不想聊工作好吗呢？", "工作"),
        ("我没心情说工作可以吗呢？", "工作"),
        ("别再提工作好吗呢？", "工作"),
        ("我不想聊工作了好吗。", "工作"),
    ],
)
def test_stripping_a_tail_restarts_the_longest_first_scan(text, expected):
    """Stripping one ending **exposes** a longer one already passed by this iteration's cursor.

    Its suffix then matches instead: 呢 exposes 好吗, but 吗 strips, persisting nonword
    工作好 (parent 工作好吗; codex P2). Break after stripping and rescan from the beginning.
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == {expected}, _zh_terms(text)


def test_the_length_floor_still_wins_over_the_restart():
    """Rescanning must preserve minimum-length protection: retain short topics + ambiguous endings intact."""  # noqa: DOCSTRING_CJK
    assert _zh_terms("别提钱好吗？") == {"钱好吗"}
    assert _zh_terms("别提猫可以吗？") == {"猫可以吗"}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Predicate use: preceded by a Chinese stem.
        ("別提案なし。", set()),
        ("別提案あり。", set()),
        ("別提案書あり。", set()),
        # Lexical use: preceded by Kana or beginning the whole word.
        ("別提おもてなし。", {"おもてなし"}),
        ("别提おもてなし。", {"おもてなし"}),
        ("別再提おもてなし。", {"おもてなし"}),
        ("別提なしくずし。", {"なしくずし"}),
        # The right anchor **still matters** after adding left boundaries: Kanji followed by なし away from sentence end
        # is part of a word, not a predicate. This constructed, non-natural Japanese example pins
        # predicate = sentence-final; removing the right anchor swallows everything (mutation testing).
        ("別提案なしくずし。", {"案なしくずし"}),
    ],
)
def test_the_existence_predicate_needs_a_kanji_stem(text, expected):
    """Constrain both sides of なし, or false rejections follow.

    Right sentence-end anchors protect ありがとう; left Kanji requirements protect おもてなし. Right-only anchoring
    loses traditional 別提おもてなし。 while simplified equivalents work (codex P2).
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == expected, _zh_terms(text)


@pytest.mark.parametrize(
    "text", ["別提案だ。", "今回は、別提案だ。", "「別講座だ。」", "別談話だ。"]
)
def test_a_bare_sentence_final_copula_is_grammar_evidence(text):
    """Bare sentence-final だ is a Japanese copula; omitting it persists 別提案だ。 as 案だ."""  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == set(), _zh_terms(text)


@pytest.mark.parametrize(
    "text", ["別提だんご三兄弟。", "别再提だんご三兄弟。", "別再提だんご三兄弟。"]
)
def test_the_bare_copula_is_anchored_so_titles_survive(text):
    """Colloquial copulas above included only multicharacter forms because of だんご三兄弟.

    Sentence-end anchoring removes that concern (だ in だんご precedes ん), so bare
    だ is acceptable only with the anchor; removing it rejects these cases.
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == {"だんご三兄弟"}


# ── 45. ASCII endings inside quotes / closers after predicates / pauses cannot cross lines /
#        ASCII operators cannot cross sentences / surplus closers ──


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("別再提《Never Please》。", "Never Please"),
        ("别再提《Never Please》。", "Never Please"),
        ("别再提《Never please》。", "Never please"),
        ("别再提《Never Porfa》。", "Never Porfa"),
        ("别再提《Please》。", "Please"),
    ],
)
def test_ascii_cleanup_words_inside_quotes_are_protected(text, expected):
    """English work titles can end in please, while quotation gates previously covered only CJK endings.

    《Never Please》 was truncated to Never (parent intact); case-insensitive stripping also
    affects Please / PLEASE (codex P2).
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == {expected}, _zh_terms(text)


@pytest.mark.parametrize(
    "text", ["别再提工作 please。", "別再提工作 PLEASE。", "别再提工作PLEASE。"]
)
def test_ascii_cleanup_words_outside_quotes_are_still_stripped(text):
    """Conversely gating protects only **inside quotations**; still strip outside."""  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == {"工作"}, _zh_terms(text)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Never Please is off limits.", "Never Please"),
        ("Never Please别提了。", "Never Please"),
        ("no menciones Never Please", "Never Please"),
        ("别再提Never Please。", "Never Please"),
        ("别再提工作 Please。", "工作 Please"),
    ],
)
def test_a_title_cased_ascii_word_is_part_of_the_name(text, expected):
    """Unconditional case-insensitivity consumes **initial-capital** title words.

    Never Please is off limits. persists Never instead of parent's intact title (reverse codex P2:
    case-insensitivity was also requested by codex; narrow it to all-uppercase here).
    Initial capitals signal name components in English, while uppercase emphasizes politeness words.
    Same tradeoff as CJK: prefer an extra word over losing half a name.
    """  # noqa: DOCSTRING_CJK
    # Extract **all locales**: this family includes en/es template sentences that _zh_terms filters out,
    # making assertions vacuous set() == set(), encountered while writing this test.
    got = {term for _locale, _kind, term in extract_directives(text)}
    assert got == {expected}, got


@pytest.mark.parametrize(
    ("opener", "closer"), sorted(D._ZH_CLOSE_FOR_OPEN.items())
)
@pytest.mark.parametrize("predicate", ["あり", "なし", "だ"])
def test_a_closing_delimiter_ends_a_japanese_sentence(opener, closer, predicate):
    """Whole Japanese sentences inside quotes/brackets remain sentences, with closers counting as sentence ends.

    Punctuation-only checks miss 「別提案あり」 / （別提案なし） / 「別提案だ」
    (codex P2).
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(f"{opener}別提案{predicate}{closer}") == set()


@pytest.mark.parametrize("pause", ["，", "；", "、", ",", ";"])
@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_a_pause_does_not_reach_across_a_line(pause, newline):
    """Only horizontal whitespace after pauses: "别再提，\\n工作正常。" stores the **next line** as an object.

    Parent misses entirely (codex P2). Same criterion as subject spacing: instructions never cross lines.
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(f"别再提{pause}{newline}工作正常。") == set()


@pytest.mark.parametrize("gap", ["", " ", "\t", "　"])
def test_a_pause_still_reaches_across_horizontal_space(gap):
    """Conversely preserve same-line horizontal whitespace."""  # noqa: DOCSTRING_CJK
    assert _zh_terms(f"别再提，{gap}工作正常。") == {"工作正常"}


@pytest.mark.parametrize(
    ("opener", "closer"),
    sorted((lo, hi) for lo, hi in D._ZH_CLOSE_FOR_OPEN.items() if lo.isascii()),
)
def test_ascii_operators_do_not_pair_across_a_sentence(opener, closer):
    """Unrelated instructions each containing ASCII operators must not form one quotation.

    别再提价格<预算。别再提收入>目标。 merged as one (parent has two;
    codex P2). Fullwidth brackets and symmetric quotes omit this rule; see _zh_bracket_body.
    """  # noqa: DOCSTRING_CJK
    text = f"别再提价格{opener}预算。别再提收入{closer}目标。"
    assert _zh_terms(text) == {f"价格{opener}预算", f"收入{closer}目标"}, _zh_terms(text)


def test_full_width_brackets_may_still_span_a_sentence():
    """Conversely fullwidth brackets introduce quotations only, still permitting internal sentence punctuation in real titles."""  # noqa: DOCSTRING_CJK
    assert _zh_terms("别再提电影《你好。世界》。") == {"电影《你好。世界》"}
    assert _zh_terms('别再提"Everything. Everywhere"。') == {"Everything. Everywhere"}


@pytest.mark.parametrize(
    ("opener", "closer"), sorted(D._ZH_CLOSE_FOR_OPEN.items())
)
def test_a_surplus_closer_is_stripped_by_pairing_order(opener, closer):
    """Testing whether a counterpart occurs anywhere is too coarse: earlier closers may already have paired that opener.

    In 别再提电影《你好》续集》。 the final 》 is surplus and parent strips it
    (codex P2). Scan **pairing order** instead.
    """  # noqa: DOCSTRING_CJK
    text = f"别再提电影{opener}你好{closer}续集{closer}。"
    assert _zh_terms(text) == {f"电影{opener}你好{closer}续集"}, _zh_terms(text)


def test_the_unmatched_scan_reports_positions_not_characters():
    """Structure: position matters; identical characters can be paired at one position and unmatched at another."""  # noqa: DOCSTRING_CJK
    assert D._zh_unmatched_delims("电影《你好》续集》") == {8}
    assert D._zh_unmatched_delims("电影《你好》续集") == set()
    assert D._zh_unmatched_delims("电影《你好") == {2}
    assert D._zh_unmatched_delims("你好》") == {2}
    # Pair by **type**: 《 and ） do not pair; both remain unmatched.
    assert D._zh_unmatched_delims("电影《你好）") == {2, 5}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("别再提电影《你好）。", "电影《你好"),
        ("别再提《你好）。", "你好"),
    ],
)
def test_mismatched_bracket_types_do_not_pair(text, expected):
    """Popping whenever stacks are nonempty treats ） in 《你好） as paired, retaining it in
    terms. Distinguishing examples require a **type-mismatched** pair (mutation testing).
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == {expected}, _zh_terms(text)


# ── 46. Pauses after 就 / honorifics in 再 branch / bare だ also requires Kanji stems ──


@pytest.mark.parametrize("pause", ["，", "、", "；", ",", ";"])
def test_a_pause_after_jiu_is_consumed_too(pause):
    """Pauses may also follow 就 (工作，这件事，就，别提了。).

    Accepting only the preceding pause restarts matching after the first comma, storing filler 这件事;
    parent stores 工作，这件事，就, at least containing the real topic (codex P2).
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(f"工作{pause}这件事{pause}就{pause}别提了。") == {"工作"}
    assert _zh_terms(f"工作{pause}就{pause}别提了。") == {"工作"}


def test_a_pause_after_jiu_does_not_eat_lexical_jiu():
    """Conversely consume no 就 without pauses, preserving 成就-family words."""  # noqa: DOCSTRING_CJK
    for term in ("成就", "迁就", "功成名就"):
        assert _zh_terms(f"{term}别提了。") == {term}


@pytest.mark.parametrize("polite", list("你妳您咱请們請"))
def test_a_polite_marker_is_accepted_before_the_zai_branch(polite):
    """再 already disambiguates; left boundaries only block suffixes such as 地域別再提案.

    Subjects/honorifics cannot precede that suffix. Excluding them loses 請別再提君の名は。 entirely,
    while simplified equivalents work (codex P2).
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(f"{polite}別再提君の名は。") == {"君の名は"}


@pytest.mark.parametrize(
    "text",
    [
        # Honorifics pass only in the branch **with 再**; without it defer to the Japanese guard.
        "請別提案をお願いします。",
        "請別提君の名は。",
        # Noun + 別 forms (〜別 suffixes) remain entirely blocked.
        "地域別再提案をお願いします。",
        "個別再提案をお願いします。",
        "世代別再講座で話します。",
        # Japanese Kanji subjects must not pass.
        "我別再提君の名は。",
        "他別再提案をお願いします。",
    ],
)
def test_the_polite_allowance_does_not_open_the_guard(text):
    """Allow 請 only in the 再 branch, **never** _ZH_SUBJECT_CHARS.

    Adding it there opens a hole for 請別提案をお願いします。 without 再.
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == set(), _zh_terms(text)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Lexical use: Kana followed by だ.
        ("別提ただ。", "ただ"),
        ("別提まだ。", "まだ"),
        ("别提ただ。", "ただ"),
        ("別再提ただ。", "ただ"),
        ("你別提ただ。", "ただ"),
    ],
)
def test_a_bare_copula_needs_a_kanji_stem(text, expected):
    """Bare だ also requires a **Kanji stem**, matching あり|なし.

    Japanese predicates follow Kanji stems (案だ / 座だ / 話だ); after Kana, だ is part
    of words. Right-only anchoring loses traditional 別提ただ。 while simplified equivalents work (codex P2).
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == {expected}, _zh_terms(text)


@pytest.mark.parametrize(
    "text",
    [
        "别再提工作 PLEASE好吗？",
        "别再提工作PLEASE好吗？",
        "别再提工作 PLEASE了好吗。",
        "我不想聊工作 PLEASE可以吗？",
    ],
)
def test_the_tail_slice_comes_from_the_current_string(text):
    """Slice endings from **current** s, never original term.

    Distinguishing examples must expose ASCII endings **after other stripping**: remove rhetorical 好吗 first,
    bringing PLEASE to the end. Direct 工作 PLEASE。 already gets its ending separated by regex,
    making original-string slicing equally correct and the test ineffective (mutation testing).
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == {"工作"}, _zh_terms(text)


@pytest.mark.parametrize("text", ["別提案だ。", "「別講座だ。」", "別談話だ。"])
def test_a_kanji_stem_copula_is_still_japanese(text):
    """Conversely retain blocking for every Kanji-stem case."""  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == set(), _zh_terms(text)


# ── 47. Rescan after guard rejection / する・した / triggers never cross lines ──


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("地域別提案をお願いします 别再提工作。", "工作"),
        ("カテゴリ別提案書 别再提加班。", "加班"),
        ("テーマ別討論スレ 别再提出差。", "出差"),
        ("地域別提案をお願いします。别再提工作。", "工作"),
    ],
)
def test_a_guarded_match_does_not_swallow_a_later_directive(text, expected):
    """After Japanese guards reject a match, finditer has already consumed its entire span.

    Embedded **real instructions** become unreachable, producing zero matches where parent found the later instruction
    (codex P2). Advance only one character from rejected starts and rescan.
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == {expected}, _zh_terms(text)


def test_the_rescan_does_not_re_extract_accepted_matches():
    """Conversely successful matches still skip their whole span to prevent repeated extraction of one instruction."""  # noqa: DOCSTRING_CJK
    assert _zh_terms("别提工作。别提加班。") == {"工作", "加班"}
    # Extract each instruction once; duplicates appear here as multiple same-named terms before deduplication.
    raw = [t for _l, _k, t in extract_directives("别再提工作。")]
    assert raw == ["工作"], raw


@pytest.mark.parametrize(
    "text",
    ["別提案した。", "今回は、別提案した。", "別提案する。", "「別講座した。」"],
)
def test_plain_and_past_sahen_predicates_are_grammar_evidence(text):
    """Included します / しない / しよう but omitted common する / した.

    別提案した。 persisted 案した (codex P2). Reuse both boundaries from あり|なし|だ.
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == set(), _zh_terms(text)


@pytest.mark.parametrize(("text", "expected"), [("別提あした。", "あした")])
def test_the_sahen_predicates_need_a_kanji_stem_too(text, expected):
    """Conversely Kana word あした is not a predicate; require Kanji left boundaries consistently."""  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == {expected}, _zh_terms(text)


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
@pytest.mark.parametrize("split", ["請別再{nl}提", "別再{nl}提", "別{nl}再提"])
def test_a_trigger_does_not_span_a_line_break(newline, split):
    """Whitespace **inside triggers** must be horizontal too: negation↔再↔verb cannot cross lines.

    "請別再\\n提案をお願いします。" combines prior-line 別再 with the next Japanese verb,
    persisting 案をお願いします (codex P2).
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(split.format(nl=newline) + "案をお願いします。") == set()


@pytest.mark.parametrize("gap", [" ", "  ", "\t", "　", ""])
def test_a_trigger_still_tolerates_horizontal_gaps(gap):
    """Conversely preserve same-line horizontal whitespace."""  # noqa: DOCSTRING_CJK
    assert _zh_terms(f"别{gap}再{gap}提{gap}工作。") == {"工作"}
    assert _zh_terms(f"不要{gap}再{gap}提工作。") == {"工作"}


# ── 48. Verbs/objects never cross lines / semicolons inside ASCII brackets ──


@pytest.mark.parametrize(
    "text",
    [
        "別再提\n案をお願いします。",
        "請別再提\n案をお願いします。",
        "別再提\r\n案をお願いします。",
        "我不想聊\n案をお願いします。",
    ],
)
def test_a_verb_object_gap_does_not_span_a_line(text):
    """After a complete trigger, newlines can attach the next line as an object.

    別再提 followed on the next line by 案をお願いします。 persists 案をお願いします; structural
    evidence 別再提 then short-circuits the Japanese guard (codex P2). Apply the same no-cross-line criterion as trigger interiors,
    subjects, and post-pause spacing.
    Cost: 别再提 followed by 工作。 also loses parent's match, an inevitable consequence of applying
    one criterion consistently; narrowing only trigger interiors previously exposed this next gap.
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == set(), _zh_terms(text)


@pytest.mark.parametrize("gap", [" ", "  ", "\t", "　", ""])
def test_a_verb_object_gap_still_tolerates_horizontal_space(gap):
    """Conversely preserve same-line horizontal whitespace."""  # noqa: DOCSTRING_CJK
    assert _zh_terms(f"别再提{gap}工作。") == {"工作"}
    assert _zh_terms(f"我不想聊{gap}工作。") == {"工作"}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("代码{foo;bar}别提了。", "代码{foo;bar}"),
        ("关于代码{foo;bar}就别提了。", "代码{foo;bar}"),
        ("别再提代码{foo;bar}。", "代码{foo;bar}"),
        ("别再提代码[foo;bar]。", "代码[foo;bar]"),
        ("别再提代码(foo;bar)。", "代码(foo;bar)"),
    ],
)
def test_a_semicolon_inside_a_closed_ascii_pair_is_content(text, expected):
    """Semicolons are valid content in **closed** ASCII code spans.

    Excluding ；; to prevent cross-sentence pairing truncated 代码{foo;bar}别提了。
    to bar (parent 代码{foo;bar; codex P2). 。！？ suffice for that cross-sentence
    family.
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == {expected}, _zh_terms(text)


# The counterexample blocking pairing across **periods** remains in section 45,
# test_ascii_operators_do_not_pair_across_a_sentence. Repeating it verbatim here creates
# two identical tests needing synchronized edits, exactly the drift this file repeatedly prevents (coderabbit).


def test_the_ascii_bracket_exclusion_keeps_semicolons():
    """Structure: ASCII bracket bodies exclude sentence-ending punctuation, not semicolons."""  # noqa: DOCSTRING_CJK
    body = D._zh_bracket_body("{", "}")
    assert "。" in body and "！" in body, body
    assert "；" not in body, body
    assert ";" not in body, body


# ── 49. Unicode horizontal whitespace / サ変 endings / abbreviation periods / preposed topics never cross lines ──


@pytest.mark.parametrize("space", ["\u00a0", "\u202f", "\u2009", "\u3000", " ", "\t"])
def test_any_non_newline_whitespace_is_a_horizontal_gap(space):
    """Require **any whitespace except line breaks**, not selected whitespace characters.

    Enumeration excludes NBSP / U+202F / U+2009 pasted from webpages/mobile input: 别<NBSP>再<NBSP>提工作。
    loses all matches, unlike parent's \\s*
    (codex P2).
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(f"别{space}再{space}提工作。") == {"工作"}
    assert _zh_terms(f"工作{space}别提了。") == {"工作"}


def test_the_horizontal_class_is_a_negated_newline_class():
    """Structure: use negative criteria, never return to whitespace enumeration."""  # noqa: DOCSTRING_CJK
    # Exclude **all** line separators, not only CR/LF: Python \s also recognizes U+2028 /
    # U+2029 / U+0085 / \v / \f and U+001C–U+001F (codex P2).
    assert D._ZH_HSPACE == D._ZH_HSPACE_ONE + "*"
    assert D._ZH_HSPACE_ONE == r"[^\S" + D._ZH_LINE_SEP + r"]"
    for sep in "\r\n\v\f\x85\u2028\u2029":
        assert not re.fullmatch(D._ZH_HSPACE_ONE, sep), repr(sep)
    for space in " \t\u00a0\u202f\u2009\u3000":
        assert re.fullmatch(D._ZH_HSPACE_ONE, space), repr(space)


@pytest.mark.parametrize(
    "text",
    [
        "別提案して。",
        "今回は、別提案したい。",
        "「別講座しろ。」",
        "別提案せよ。",
        "別提案する。",
        "別提案した。",
    ],
)
def test_common_sahen_endings_are_grammar_evidence(text):
    """Common サ変 endings are sentence-final predicates (two codex P2 rounds: する/した, then して/したい/しろ)."""  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == set(), _zh_terms(text)


@pytest.mark.parametrize(("text", "expected"), [("別提そして。", "そして")])
def test_the_sahen_endings_still_need_a_kanji_stem(text, expected):
    """Conversely そ in そして is Kana and fails the left boundary."""  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == {expected}, _zh_terms(text)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Dr. Who别提了。", "Dr. Who"),
        ("关于Dr. Who就别提了。", "Dr. Who"),
        ("Mr. Robot别提了。", "Mr. Robot"),
        ("Mrs. Doubtfire别提了。", "Mrs. Doubtfire"),
        ("U.S. Army别提了。", "U.S. Army"),
        ("关于U.S. Army就别提了。", "U.S. Army"),
    ],
)
def test_an_abbreviation_period_stays_inside_the_topic(text, expected):
    """Dr. Who / U.S. Army allow spaces after abbreviation periods, which are not sentence boundaries (codex P2)."""  # noqa: DOCSTRING_CJK
    got = {term for _locale, _kind, term in extract_directives(text)}
    assert got == {expected}, got


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("That was bad. Work别提了。", "Work"),
        ("I am sad. Work别提了。", "Work"),
        ("ABad. Work别提了。", "Work"),
    ],
)
def test_a_real_sentence_period_is_still_a_boundary(text, expected):
    """Conversely real sentence boundaries still split (coderabbit). Require **abbreviation shape**:
    initial uppercase, 1–3 letters total; bad. starts lowercase, while ABad. has preceding attached letters.
    """  # noqa: DOCSTRING_CJK
    got = {term for _locale, _kind, term in extract_directives(text)}
    assert got == {expected}, got


def test_the_abbreviation_rule_survives_ignorecase():
    """Whole templates use IGNORECASE, so bare [A-Z] matches lowercase too.

    Without (?-i:...), criteria become any period followed by space + letter,
    swallowing That was bad. Work as a topic again (caught in self-testing).
    """  # noqa: DOCSTRING_CJK
    assert "(?-i:[A-Z])" in D._ZH_IDENT_PUNCT
    assert D._ZH_IDENT_PUNCT.count("(?-i:") >= 6


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
@pytest.mark.parametrize(
    "text", ["工作正常{nl}別提了。", "關於工作{nl}別提了。", "工作正常{nl}别提了。"]
)
def test_a_preposed_topic_does_not_come_from_the_previous_line(text, newline):
    """The previous line becomes a preposed topic (codex P2).

    Fifth/sixth no-cross-line sites: trigger interiors / subjects / post-pause /
    verb-object / post-topic / post-filler.
    Cost: 工作正常 followed by 别提了。 matched parent but no longer does.
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text.format(nl=newline)) == set()


def test_no_cross_line_gap_remains_anywhere_in_a_zh_template():
    """Structure: forbid cross-line whitespace **anywhere** in all four templates, automatically rather than by checklist.

    Originally scanned only **before captures**, claiming later whitespace could not pull content into terms.
    Codex disproved that: final-particle \\s* crosses newlines after complete triggers to reach
    next-line particles, persisting the **previous line** (別再提工作 followed by 吧。).
    Post-capture whitespace does not enter terms but determines whether matches **succeed**.

    Sole exemption: \\s*$ anchored to end, with no following content to reach.
    """  # noqa: DOCSTRING_CJK
    for index, raw in enumerate(_zh_pattern_sources()):
        # Order matters: remove lookaheads before horizontal whitespace; reversing changes constants inside
        # lookaheads and prevents their removal, encountered while writing this test.
        # Exempt _ZH_OBJECTLESS_AHEAD: a **zero-width negative lookahead** only rejects more,
        # never consumes additional content. Criteria govern whitespace that actually consumes.
        body = raw.replace(D._ZH_OBJECTLESS_AHEAD, "")
        # Likewise topic-leading (?!\s) forbids initial whitespace without consuming anything.
        body = body.replace(r"(?!\s)", "")
        body = body.replace(D._ZH_HSPACE, "")
        body = body.replace(chr(92) + "s*$", "")
        # \s inside negated classes **excludes** whitespace, the opposite direction and outside this rule.
        body = re.sub(r"\[\^(?:\\.|[^\]])*\]", "", body)
        assert chr(92) + "s" not in body, (index, body[:200])


# ── 50. Unicode line separators / post-punctuation spaces / subject spacing / Japanese final particles /
#        Chinese particles as evidence / terminators preserve newlines / ?! inside ASCII brackets ──


@pytest.mark.parametrize("sep", ["\u2028", "\u2029", "\u0085", "\v", "\f"])
@pytest.mark.parametrize(
    "text", ["別再提{s}案をお願いします。", "工作正常{s}別提了。", "别再提{s}工作。"]
)
def test_every_unicode_line_separator_blocks_a_match(sep, text):
    """Python ``\\s`` also recognizes U+2028 / U+2029 / U+0085 / vertical tab / form feed.

    Excluding only CR/LF still permits crossing visual lines (codex P2). Extract criteria into _ZH_LINE_SEP,
    shared by bracket bodies, topic classes, and horizontal whitespace.
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text.format(s=sep)) == set()


def test_the_line_separator_table_is_the_single_source():
    """Structure: all three derive from _ZH_LINE_SEP, never maintain separate CR/LF copies."""  # noqa: DOCSTRING_CJK
    assert D._ZH_LINE_SEP in D._ZH_PLAIN_CHAR
    assert D._ZH_LINE_SEP in D._ZH_HSPACE_ONE
    assert D._ZH_LINE_SEP in D._zh_bracket_body("《", "》")


@pytest.mark.parametrize("space", ["\u00a0", "\u202f", "\u2009", "\u3000", " ", "\t"])
def test_unicode_spaces_after_internal_punctuation(space):
    """Recognize pasted webpage forms such as Dr.<NBSP>Who too (codex P2)."""  # noqa: DOCSTRING_CJK
    got = {t for _l, _k, t in extract_directives(f"Dr.{space}Who别提了。")}
    assert got == {f"Dr.{space}Who"}, got
    assert _zh_terms(f"关于Hello,{space}World就别提了。") == {f"Hello,{space}World"}


@pytest.mark.parametrize("space", ["\u00a0", "\u202f", "\u2009", "\u3000", " ", "\t"])
def test_unicode_spaces_between_subject_and_negation(space):
    """Subject spacing uses the same horizontal whitespace (codex P2)."""  # noqa: DOCSTRING_CJK
    assert _zh_terms(f"你{space}別提君の名は。") == {"君の名は"}


@pytest.mark.parametrize(
    "text",
    ["別提案か？", "今回は、別提案も。", "「別講座じゃん。」", "別提案ね。", "別提案よ。"],
)
def test_sentence_final_japanese_particles_are_grammar_evidence(text):
    """Japanese final particles also provide predicate evidence (codex P2)."""  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == set(), _zh_terms(text)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("别再提ドラえもん。", "ドラえもん"),
        ("別再提ドラえもん。", "ドラえもん"),
        ("別叫我「お兄ちゃん」。", "お兄ちゃん"),
    ],
)
def test_the_final_particles_do_not_kill_titles(text, expected):
    """も was excluded from single-character particles precisely because of ドラえもん.

    Kanji-stem + sentence-end boundaries make it safe: も in ドラえもん follows
    Kana and is not sentence-final.
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == {expected}, _zh_terms(text)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # To distinguish whether final particles require Kanji stems, use words with **Kana followed by final particles**
        # on the actual guard path (traditional + shared verb + no 再).
        # ドラえもん does not end in a final particle and cannot test it (mutation testing).
        ("別提すいか。", "すいか"),
        ("別提ドラえもんか。", "ドラえもんか"),
        ("別提あかさ。", "あかさ"),
    ],
)
def test_the_final_particles_need_a_kanji_stem(text, expected):
    """Final particles also require Kanji-stem left boundaries, avoiding rejection of Kana-ending Japanese proper names."""  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == {expected}, _zh_terms(text)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("別提君の名は吧。", "君の名は"),
        ("君の名は別提了。", "君の名は"),
        ("别提君の名は吧。", "君の名は"),
        ("君の名は别提了。", "君の名は"),
    ],
)
def test_chinese_only_final_particles_count_as_evidence(text, expected):
    """When topics contain Japanese particles, uncaptured 吧 / 提了 are the only Chinese evidence.

    Omitting them loses traditional instructions while simplified equivalents survive through 别 evidence (codex P2).
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == {expected}, _zh_terms(text)


@pytest.mark.parametrize(
    "text",
    [
        "地域別提案をお願いします。",
        "カテゴリ別提案書。",
        "世代別講座で話します。",
        "テーマ別討論スレ",
        "個別提案をお願いします。",
        "別提案をお願いします。",
    ],
)
def test_the_new_evidence_chars_do_not_open_the_japanese_guard(text):
    """Evidence-character additions easily short-circuit guards, as 没 / 称 did in two rounds.

    These sentence-final particles are Chinese-only and absent from Japanese; recheck the whole ja corpus.
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == set(), _zh_terms(text)


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\u2028"])
def test_a_trigger_terminator_does_not_eat_a_newline(newline):
    """With triggers at line ends, newlines count as instruction termination, binding the previous line as topic.

    工作正常別提 followed by 下一句。 persists 工作正常 (codex P2).
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(f"工作正常別提{newline}下一句。") == set()
    assert _zh_terms(f"工作正常别提{newline}下一句。") == set()


def test_a_horizontal_space_is_still_a_terminator():
    """Conversely same-line spaces still terminate (工作别提 然后…)."""  # noqa: DOCSTRING_CJK
    assert "工作" in _zh_terms("工作别提 然后…")
    assert _zh_terms("工作正常别提了。") == {"工作正常"}


@pytest.mark.parametrize(
    ("opener", "closer"),
    sorted((lo, hi) for lo, hi in D._ZH_CLOSE_FOR_OPEN.items() if lo.isascii()),
)
@pytest.mark.parametrize("mark", ["?", "!", ".", ";"])
def test_ascii_punctuation_inside_a_closed_pair_is_content(opener, closer, mark):
    """ASCII ? ! . ; inside **closed** title/code spans are content.

    Excluding ASCII sentence punctuation to block cross-sentence pairing lost 电影(Who?)别提了。 entirely
    (parent retained 电影(Who; codex P2). That cross-sentence family uses fullwidth 。.
    """  # noqa: DOCSTRING_CJK
    text = f"电影{opener}Who{mark}{closer}别提了。"
    assert _zh_terms(text) == {f"电影{opener}Who{mark}{closer}"}, _zh_terms(text)


# ── 51. ASCII brackets pairing across instructions / particle-evidence parity / preposed-trigger evidence /
#        Kanji stems include consumed verbs ──


@pytest.mark.parametrize(
    ("opener", "closer"),
    sorted((lo, hi) for lo, hi in D._ZH_CLOSE_FOR_OPEN.items() if lo.isascii()),
)
@pytest.mark.parametrize("sep", ["。", ".", "，", ",", ";", "；"])
def test_an_ascii_pair_never_spans_two_directives(opener, closer, sep):
    """Unrelated instructions with individual ASCII brackets must not form one quotation.

    Allowing ASCII punctuation merged 别再提价格<预算.别再提收入>目标. and lost
    the whole second instruction (codex P2). Like symmetric quotes, temper negations:
    separators form an open set (periods/commas/semicolons), while negation is the cause.
    """  # noqa: DOCSTRING_CJK
    text = f"别再提价格{opener}预算{sep}别再提收入{closer}目标."
    assert _zh_terms(text) == {f"价格{opener}预算", f"收入{closer}目标"}, text


@pytest.mark.parametrize(
    ("opener", "closer"),
    sorted((lo, hi) for lo, hi in D._ZH_CLOSE_FOR_OPEN.items() if lo.isascii()),
)
def test_tempering_the_negation_still_keeps_closed_titles(opener, closer):
    """Conversely closed titles **without negation** remain intact, bounding the preceding criterion's cost."""  # noqa: DOCSTRING_CJK
    text = f"别再提标题{opener}Who?{closer}。"
    assert _zh_terms(text) == {f"标题{opener}Who?{closer}"}, text
    # Unpunctuated 告别版 uses the single-character branch and remains intact despite containing negation.
    assert _zh_terms(f"别再提{opener}告别版{closer}。") == {"告别版"}


@pytest.mark.parametrize("verb", sorted(D._ZH_PREPOSED_SAY_VERBS))
def test_every_preposed_trigger_form_is_also_chinese_evidence(verb):
    """Every trigger form accepted by preposed-topic templates must provide Chinese evidence when followed by 了.

    Discover automatically, never manually list: accepted forms absent from evidence lose whole traditional instructions
    when topics have Japanese particles. 君の名は別提起了。 was lost this way (codex P2).
    """  # noqa: DOCSTRING_CJK
    assert D._ZH_EVIDENCE_RE.search(verb + "了"), verb
    assert "君の名は" in _zh_terms(f"君の名は別{verb}了。"), verb
    assert "君の名は" in _zh_terms(f"君の名は别{verb}了。"), verb


def test_the_preposed_trigger_list_is_not_hardcoded_in_the_template():
    """Structure: never hardcode template trigger tables again; one hardcoded copy left two forms missing from evidence."""  # noqa: DOCSTRING_CJK
    # Previously asserted only **one** template derived from constants, missing template 4's hardcoded table:
    # 關於工作就別提起了。 missed the dedicated template, fell back to generic, and stored 就
    # too (codex P2). Typical checklist omission: require **every** template with
    # 别/別 triggers to derive its vocabulary.
    # Criteria: templates with triggers **after** capture groups (preposed topics) share this table.
    # Again remove zero-width temper first: its own [别別] would otherwise make template 3 (我不想聊X)
    # appear preposed too, as the first version incorrectly did.
    stripped = [
        raw.replace(f"(?!{D._ZH_DIRECTIVE_AHEAD})", "")
        for raw in _zh_pattern_sources()
    ]
    preposed = [
        raw for raw in stripped
        if "[别別]" in raw and raw.index("[别別]") > _capture_start(raw)
    ]
    assert len(preposed) == 2, [r[:80] for r in preposed]
    for raw in preposed:
        assert "|".join(D._ZH_PREPOSED_SAY_VERBS) in raw, raw[:160]
    # Detect hardcoding through **source files** only: compiled template strings necessarily resemble literals,
    # so asserting against them tests nothing (this test's first version did that and failed immediately).
    source = pathlib.Path(D.__file__).read_text(encoding="utf-8")
    assert "提起|提及" not in source, "触发词表又被写死进模板了"
    assert source.count("_ZH_PREPOSED_SAY_VERBS") >= 4


@pytest.mark.parametrize(
    "text",
    ["別討論あり。", "地域別討論なし。", "商品別討論した。", "別討論する。",
     "カテゴリ別討論して。", "地域別討論か？", "別討論も。", "別談話だ。"],
)
def test_a_consumed_compound_verb_still_supplies_the_han_stem(text):
    """Entire 討論 enters triggers, leaving Kana-only terms and defeating all four Kanji-stem criteria.

    別討論あり。 persists あり, while identical structure 別提案あり。 with term
    案あり is blocked (codex P2). Restore the consumed Kanji stem before checking.
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == set(), text


@pytest.mark.parametrize(
    ("text", "expected"),
    [("別討論這個吧。", "這個"), ("别再提ありがとう。", "ありがとう"),
     ("別提おもてなし。", "おもてなし"), ("別提ただ。", "ただ"),
     ("別提まだ。", "まだ"), ("別提そして。", "そして"),
     ("別提すいか。", "すいか"), ("別提あした。", "あした")],
)
def test_the_reattached_stem_does_not_hurt_kana_initial_topics(text, expected):
    """Conversely restore **one** Kanji only; all four criteria need Kanji immediately followed by Kana,
    so ドラえもん cases with preceding Kana remain protected."""  # noqa: DOCSTRING_CJK
    assert expected in _zh_terms(text), text


def test_the_stem_is_taken_from_inside_the_match_not_guessed():
    """Structure: take the character immediately before capture as stem, appending only if Han."""  # noqa: DOCSTRING_CJK
    assert not D._is_japanese_sentence_match("別討論あり", "あり", "", stem="")
    assert D._is_japanese_sentence_match("別討論あり", "あり", "", stem="論")
    # Latin / Kana must not attach as stems, which would nullify the entire left-boundary criterion.
    assert not D._HAN_RE.match("A")
    assert not D._HAN_RE.match("ス")


# ── 52. Pin Japanese grammar entries individually after new guards obscure old criteria ──


def _ja_grammar_alternatives() -> tuple[list[str], list[str]]:
    """Split _JA_GRAMMAR_RE top-level alternatives into bare literals and forms with surrounding boundaries."""  # noqa: DOCSTRING_CJK
    pattern = D._JA_GRAMMAR_RE.pattern
    alts, depth, cur, i = [], 0, "", 0
    while i < len(pattern):
        char = pattern[i]
        if char == chr(92):
            cur += pattern[i:i + 2]
            i += 2
            continue
        if char == "[":
            end = pattern.index("]", i + 1)
            cur += pattern[i:end + 1]
            i = end + 1
            continue
        depth += (char == "(") - (char == ")")
        if char == "|" and depth == 0:
            alts.append(cur)
            cur = ""
            i += 1
            continue
        cur += char
        i += 1
    alts.append(cur)
    plain: list[str] = []
    for alt in alts:
        if "(?<=" in alt or "(?=" in alt:
            continue
        if alt.startswith("[") and alt.endswith("]"):
            plain.extend(alt[1:-1])
        else:
            plain.append(alt)
    # Characters excluded by topic classes **cannot** occur in terms; branches containing them are dead for zh templates.
    # Derive this automatically, never hardcode a list.
    banned = set("，。！？；,.!?;")
    reachable = [lit for lit in plain if not (set(lit) & banned)]
    unreachable = [lit for lit in plain if set(lit) & banned]
    return reachable, unreachable


_JA_REACHABLE, _JA_UNREACHABLE = _ja_grammar_alternatives()


def test_the_reachable_grammar_markers_are_actually_reachable():
    """Automatic discovery: every bare literal is either reachable or explicitly dead, never a third state.

    Require **equality**, never lower bounds such as len(...) > 45. Cases below derive
    parameters from this table, so removing entries removes tests without failure; lower bounds
    miss it too (removing seven leaves fifty). Equality alone pins actual contents, shown by mutations
    deleting entire rows while tests stayed green.
    """  # noqa: DOCSTRING_CJK
    assert sorted(set(_JA_REACHABLE)) == [
        "かな", "かも", "から", "が", "ください", "けど", "された", "される", "しか", "している",
        "しない", "します", "しよう", "じゃない", "すべき", "そうです", "たら", "だけ", "だっけ",
        "だった", "だって", "だな", "だね", "だよ", "だろう", "てある", "ている", "ておく", "で",
        "である", "できる", "でした", "でしょ", "です", "でも", "と", "という", "ながら", "など",
        "に", "について", "に関して", "の", "ので", "は", "ばかり", "へ", "ました", "ましょう",
        "ます", "ません", "まで", "みたい", "より", "らしい", "を", "下さい", "出来た", "出来ない",
        "出来る",
    ]
    # Pin dead entries **with equality too**: another means someone added an unreachable
    # marker. ？ in そう？ terminates topics and can never occur inside terms.
    # Pin dead entries with equality too: any addition signals an unreachable marker.
    # Former そう？ embedded punctuation in a marker, but sentence punctuation always lies outside capture
    # groups as the instruction terminator. This guard detected unreachability; it became そう +
    # Kanji stem + sentence-end anchor (codex P2). No dead entries should remain now.
    assert _JA_UNREACHABLE == [], _JA_UNREACHABLE


@pytest.mark.parametrize("marker", sorted(set(_JA_REACHABLE)))
def test_every_japanese_grammar_marker_is_pinned_on_its_own(marker):
    """Pin Japanese grammar entries individually: **string-initial** scaffolding disables the new guard, leaving only the table.

    This fills a coverage hole: the new 〜別 + one Kanji + Kana criterion intercepted all prior
    grammar-table samples, making deletion of the whole table pass (mutation testing).
    String start has no left neighbor, failing the new left-boundary criterion, leaving grammar tables alone responsible.
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(f"別提案{marker}。") == set(), marker


def test_the_string_start_frame_really_disables_the_kana_tail_guard():
    """Conversely pin those cases' **premise**: string start truly cannot reach the new criterion.

    If that fails, e.g. future removal of its left boundary, both criteria would block
    together and deleting the table would remain green, silently restoring the hole. Assert the criterion directly.
    """  # noqa: DOCSTRING_CJK
    # Same term shape (one Kanji + Kana), differing only in preceding label words:
    #   · String start: new criterion fails and スレ has no grammar marker, so allow;
    #   · With a label: the new criterion itself blocks it.
    # Cases above use the first path, therefore pin only the grammar table.
    assert not D._is_japanese_sentence_match("別提案スレ", "案スレ", "")
    assert D._is_japanese_sentence_match("地域別提案スレ", "案スレ", "地域")
    # At string start, classifying 案です as Japanese can only come from the grammar table.
    assert D._is_japanese_sentence_match("別提案です", "案です", "")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # **Kana** initial: criterion fails, protected since this guard's first round.
        ("動畫別提ドラえもん。", "ドラえもん"),
        ("動漫別提お兄ちゃん。", "お兄ちゃん"),
        # **Two or more Kanji** initials: criterion fails, since Japanese compound nouns lose only one character.
        ("遊戲別提初音ミク。", "初音ミク"),
        ("動畫別提美少女戰士セーラームーン。", "美少女戰士セーラームーン"),
        ("動漫別提哆啦A梦。", "哆啦A梦"),
    ],
)
def test_the_kana_tail_guard_only_fires_on_one_han_char(text, expected):
    """The shape **exactly one Kanji followed by Kana** is the criterion's entirety; deleting both conditions rejects
    these cases (mutation testing: measured elsewhere but not initially included as tests).

    All have preceding labels (動畫 / 遊戲 / 動漫), satisfying left boundaries; only
    term-shape criteria preserve them.
    """  # noqa: DOCSTRING_CJK
    assert expected in _zh_terms(text), text


def test_the_stem_is_never_kana_or_latin_by_construction():
    """`_HAN_RE.match(stem)` is a **structural invariant**, not a behavioral criterion.

    Stems come immediately before captures, necessarily verb-final Han, pause punctuation, or whitespace;
    none of four templates puts Kana/Latin there. Removing the gate is therefore an **equivalent
    mutation** with no distinguishing input; assert the invariant itself directly.
    """  # noqa: DOCSTRING_CJK
    seen = set()
    for text in (
        "別提案あり。", "別討論あり。", "别再提，君の名は。", "别再提 君の名は。",
        "关于君の名は就别提了。", "我不想聊君の名は。", "別提ドラえもん。",
        "地域別提案スレ", "别叫我 John Smith。", "別再提Dr. Who。",
    ):
        for locale, _kind, pattern in D.DIRECTIVE_PATTERNS:
            if locale != "zh":
                continue
            for match in pattern.finditer(text):
                lo = match.start(1) - match.start()
                if lo:
                    seen.add(match.group(0)[lo - 1])
    assert seen, "一条都没扫到，用例或取法不对"
    assert not any(D._KANA_RE.match(c) for c in seen), sorted(seen)
    assert not any(c.isascii() and c.isalnum() for c in seen), sorted(seen)


# ── 53. Bounded abbreviation letters / particles and terminators never cross lines / サ変 with final particles / final 了 as evidence ──


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Prof. X别提了。", "Prof. X"),      # Four letters; previously no matches at all.
        ("Prof. X別提了。", "Prof. X"),
        ("Capt. America别提了。", "Capt. America"),
        ("Assoc. Prof别提了。", "Assoc. Prof"),   # Five letters.
        ("Dr. Who别提了。", "Dr. Who"),           # Two; regression guard.
        ("Mrs. Smith别提了。", "Mrs. Smith"),     # 3
        ("关于Capt. America就别提了。", "Capt. America"),
    ],
)
def test_longer_abbreviations_keep_their_period(text, expected):
    """Previously **hardcoded three branches** (1–3 letters), excluding four-letter Prof.:
    Prof. X别提了。 changes parent's Prof. X to **zero matches**, truncating to one-character X
    rejected by minimum length, also regressing simplified Chinese (codex P2)."""  # noqa: DOCSTRING_CJK
    assert expected in _zh_terms(text), (text, _zh_terms(text))


def test_the_abbreviation_branches_are_generated_not_hardcoded():
    """Structure: derive alternatives from _ZH_ABBREV_MAX_LETTERS rather than hand-copying."""  # noqa: DOCSTRING_CJK
    assert D._ZH_ABBREV_PERIOD.count("|(?<![A-Za-z]") == D._ZH_ABBREV_MAX_LETTERS
    assert D._ZH_ABBREV_PERIOD in D._ZH_IDENT_PUNCT
    # Real sentence boundaries must remain blocked: this is the criterion's **opposite** boundary, which raising limits must preserve.
    assert _zh_terms("That was bad. Work别提了。") == {"Work"}


@pytest.mark.parametrize("sep", ["\n", "\r\n", "\u2028", "\u0085"])
@pytest.mark.parametrize(
    "text",
    [
        "別再提工作{s}吧。",        # Final-particle case, eighth position.
        "别再提工作{s}吧。",
        "工作正常別提 {s}下一句。",  # Preposed-topic terminator + trailing whitespace, ninth position.
        "工作正常别提 {s}下一句。",
        "我不想聊工作{s}了。",       # Template 5's 了, tenth position, found by the guard itself.
    ],
)
def test_no_zh_template_binds_across_a_line(sep, text):
    """No-cross-line cases 8/9/10.

    The tenth was not reported by codex: expanding structural guards from before captures to **whole templates**
    discovered it automatically. Prior cases arrived one at a time; this guard actually closes the family.
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text.format(s=sep)) == set(), text.format(s=sep)


@pytest.mark.parametrize(
    ("text", "expected"),
    [("別再提工作 吧。", "工作"), ("別再提工作吧。", "工作"),
     ("别再提工作\u00a0吧。", "工作"), ("工作别提 然后我们聊别的。", "工作"),
     ("我不想聊工作 了。", "工作")],
)
def test_a_same_line_gap_still_works(text, expected):
    """Conversely allow same-line whitespace including NBSP; narrow only the newline dimension."""  # noqa: DOCSTRING_CJK
    assert expected in _zh_terms(text), text


@pytest.mark.parametrize(
    "text",
    ["別提案してね。", "今回は、別講座してね。", "別提案するな。", "別提案したね。",
     "別提案しろよ。", "別提案してか。", "別提案するかも。"],
)
def test_sahen_predicates_may_carry_a_trailing_particle(text):
    """サ変 endings may take another final particle: 別提案してね。 persists 案して (codex P2).

    Require **closed set × closed set**: サ変 endings × existing final particles, both read from shared
    constants rather than inserting more standalone words.
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == set(), text


@pytest.mark.parametrize(
    ("text", "expected"),
    [("別提そしてね。", "そして"), ("別提そして。", "そして"),
     ("別提ドラえもん。", "ドラえもん"), ("別提すいか。", "すいか")],
)
def test_the_sahen_branch_still_needs_a_han_stem(text, expected):
    """Conversely し in そして follows Kana, failing left boundaries and remaining protected."""  # noqa: DOCSTRING_CJK
    assert expected in _zh_terms(text), text


def test_a_sentence_final_le_is_chinese_evidence():
    """別提君の名は了。 survives only through final 了; simplified equivalents always worked via 别."""  # noqa: DOCSTRING_CJK
    assert "君の名は" in _zh_terms("別提君の名は了。")
    assert "君の名は" in _zh_terms("别提君の名は了。")


@pytest.mark.parametrize(
    "text", ["地域別提案は終了。", "別提案の受付は終了。", "世代別講座は終了。",
             "地域別講座について終了。"],
)
def test_the_le_evidence_needs_a_non_han_on_its_left(text):
    """Right-only sentence-end anchoring opens a much larger hole than it fixes.

    了 in 終了 / 完了 / 修了 belongs to words. The initial right-only implementation
    failed seven of eight Japanese cases (地域別提案は終了。 persisted nonword 案は終), rescuing only
    one Chinese case; reverted and redesigned.
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == set(), text


# ── 54. Derived negation characters / bounded identifier whitespace runs / dead そう branch ──


@pytest.mark.parametrize("neg", sorted(D._ZH_NEG_SINGLES + D._ZH_NEG_MULTIS))
@pytest.mark.parametrize(
    ("opener", "closer"),
    sorted((lo, hi) for lo, hi in D._ZH_CLOSE_FOR_OPEN.items() if lo.isascii()),
)
def test_every_negation_is_tempered_inside_ascii_brackets(neg, opener, closer):
    """Automatic discovery: **every** negation must prevent pairing two instructions into one quotation.

    Hardcoding only 别別 still merged 不要提… / 不许提… / 甭提…
    (codex P2, all three simplified regressions). Derive from negation tables so additions synchronize automatically.
    """  # noqa: DOCSTRING_CJK
    text = f"{neg}提价格{opener}预算.{neg}提收入{closer}目标."
    assert _zh_terms(text) == {f"价格{opener}预算", f"收入{closer}目标"}, text


def test_the_tempered_chars_are_derived_from_the_negation_tables():
    """Structure: never hand-copy again."""  # noqa: DOCSTRING_CJK
    assert D._ZH_NEG_FIRST_CHARS == "".join(
        dict.fromkeys(n[0] for n in D._ZH_NEG_SINGLES + D._ZH_NEG_MULTIS)
    )
    assert set(D._ZH_NEG_FIRST_CHARS) == set("别別莫休甭不")


@pytest.mark.parametrize("gap", ["  ", "   ", " \u00a0", "\u3000\u3000"])
@pytest.mark.parametrize(
    ("text", "expected"),
    [("Dr.{g}Who别提了。", "Dr.{g}Who"), ("Dr.{g}Who別提了。", "Dr.{g}Who"),
     ("Prof.{g}X别提了。", "Prof.{g}X"),
     ("关于Hello,{g}World就别提了。", "Hello,{g}World")],
)
def test_repeated_spaces_after_identifier_punctuation(gap, text, expected):
    """Previously recognized **one** space, claiming one sufficed; measurements disproved it.

    Titles pasted from webpages/PDFs often contain two spaces: Dr.  Who别提了。 reduces to Who,
    while Prof.  X别提了。 disappears entirely (codex P2, all three simplified regressions).
    """  # noqa: DOCSTRING_CJK
    assert expected.format(g=gap) in _zh_terms(text.format(g=gap)), text.format(g=gap)


def test_the_optional_gap_is_a_range_not_a_lazy_quantifier():
    """{1,8}? is a **lazy quantifier**, not optional {1,8}.

    Adding ? after _ZH_IDENT_GAP breaks all unspaced Hello,World forms,
    caught immediately after editing. Optional branches must explicitly use {0,8}.
    """  # noqa: DOCSTRING_CJK
    assert D._ZH_IDENT_GAP_OPT.endswith("{0,8}")
    assert "{1,8}?" not in D._ZH_IDENT_PUNCT
    assert "Hello,World" in _zh_terms("Hello,World别提了。")


@pytest.mark.parametrize("mark", ["？", "?", "。", "！"])
def test_sentence_final_sou_is_guarded(mark):
    """The そう？ branch embedded **punctuation** in its marker, but sentence punctuation always lies outside captures
    as instruction termination, making it dead: 別提案そう？ still persists
    案そう. The guard detected unreachability before codex supplied the correct fix."""  # noqa: DOCSTRING_CJK
    assert _zh_terms(f"別提案そう{mark}") == set(), mark


def test_the_sou_branch_still_needs_a_han_stem():
    """Conversely そ in ドラえもんそう follows Kana, failing left boundaries."""  # noqa: DOCSTRING_CJK
    assert "ドラえもんそう" in _zh_terms("別提ドラえもんそう？")


# ── 55. Temper requires complete instructions / Kana endings after two-character verbs ──


@pytest.mark.parametrize(
    ("opener", "closer"),
    sorted((lo, hi) for lo, hi in D._ZH_CLOSE_FOR_OPEN.items() if lo.isascii()),
)
@pytest.mark.parametrize("word", ["不可思议", "莫名其妙", "休闲", "告别版", "不错"])
def test_ordinary_words_starting_with_a_negation_stay_inside_brackets(
    opener, closer, word,
):
    """Previous temper banned negations' **first characters**, overcorrecting.

    Ordinary words often start 不 / 莫 / 休 / 别: 电影(不可思议; 2020)别提了。
    falls back to 2020 (codex P2, simplified regression). Characters are not the criterion; **negation followed
    by speech verbs** actually proves brackets span two instructions.
    """  # noqa: DOCSTRING_CJK
    text = f"电影{opener}{word}; 2020{closer}别提了。"
    assert _zh_terms(text) == {f"电影{opener}{word}; 2020{closer}"}, text


@pytest.mark.parametrize("neg", sorted(D._ZH_NEG_SINGLES + D._ZH_NEG_MULTIS))
def test_the_symmetric_quote_run_tempers_every_negation_too(neg):
    """Symmetric quotes previously blocked only 别別; other negations still merged instructions
    (不要提价格"预算.不要提收入"目标.; codex P2, simplified regression). Both derive from one source."""  # noqa: DOCSTRING_CJK
    text = f'{neg}提价格"预算.{neg}提收入"目标.'
    assert _zh_terms(text) == {'价格"预算', '收入"目标'}, text


def test_the_temper_is_a_full_directive_not_a_character_class():
    """Structure: require full instructions, with both sites using one constant."""  # noqa: DOCSTRING_CJK
    for neg in D._ZH_NEG_SINGLES + D._ZH_NEG_MULTIS:
        assert neg in D._ZH_DIRECTIVE_AHEAD, neg
    for verb in D._ZH_SAY_COMPOUNDS + D._ZH_SAY_VERBS:
        assert verb in D._ZH_DIRECTIVE_AHEAD, verb
    assert "(?![别別])" not in D._ZH_BRACKET_RUN


@pytest.mark.parametrize(
    "text", ["地域別討論スレ。", "A別討論ページ。", "カテゴリ別討論メモ。"],
)
def test_a_compound_verb_label_tail_is_suppressed(text):
    """討論 is a **two-character** shared compound, entirely consumed by triggers, leaving Kana-leading terms that
    never meet the one-Kanji-plus-Kana shape (codex P2). Restore the stem before checking."""  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == set(), text


@pytest.mark.parametrize(
    ("text", "expected"),
    [("動畫別提ドラえもん。", "ドラえもん"), ("遊戲別提初音ミク。", "初音ミク"),
     ("動漫別提お兄ちゃん。", "お兄ちゃん")],
)
def test_only_compound_verbs_reattach_the_stem(text, expected):
    """Conversely restore stems **only** for two-character verbs. Doing so for 提 / 講 / 談 creates
    one-Kanji-plus-Kana 提ドラえもん, rejecting everything this guard protected since its first round
    (tried and measured the regression)."""  # noqa: DOCSTRING_CJK
    assert expected in _zh_terms(text), text


def test_the_compound_verb_table_is_derived():
    """Structure: derive two-character shared verbs from _ZH_SAY_VERBS_JA_SHARED."""  # noqa: DOCSTRING_CJK
    assert D._ZH_COMPOUND_JA_SHARED_VERBS == tuple(
        v for v in D._ZH_SAY_VERBS_JA_SHARED if len(v) > 1
    )
    assert D._ZH_COMPOUND_JA_SHARED_VERBS == ("討論",)


# ── 56. Temper grammar matches templates / 关于-template triggers share one source ──


@pytest.mark.parametrize(
    ("opener", "closer"),
    sorted((lo, hi) for lo, hi in D._ZH_CLOSE_FOR_OPEN.items() if lo.isascii()),
)
@pytest.mark.parametrize("addr", sorted(D._ZH_ADDRESS_VERBS))
def test_address_directives_still_split_through_brackets(opener, closer, addr):
    """Temper omitted naming verbs, merging two 别叫我… instructions (codex P2, simplified regression)."""  # noqa: DOCSTRING_CJK
    verb = addr.replace("?", "")
    text = f"别{verb}价格{opener}预算.别{verb}收入{closer}目标."
    assert _zh_terms(text) == {f"价格{opener}预算", f"收入{closer}目标"}, text


# Deliberately exclude 分别: 分 is absent from _BIE_COMPOUND_LEFT, the **template's own**
# preceding-character table; parent also treats 分别讨论 as an instruction. This round aligns temper with templates,
# rather than changing template criteria.
@pytest.mark.parametrize("word", ["个别讨论", "告别演说", "休息"])
def test_compound_words_are_not_mistaken_for_a_directive(word):
    """Temper negations must use the **guarded** version.

    Bare 别 treats compounds like 个别讨论 as instructions, preventing brackets protecting internal punctuation;
    电影(个别讨论; 2020)别提了。 falls back to 2020 (codex P2, simplified regression).
    """  # noqa: DOCSTRING_CJK
    text = f"电影({word}; 2020)别提了。"
    assert _zh_terms(text) == {f"电影({word}; 2020)"}, text


def test_the_temper_grammar_matches_the_templates():
    """Structure: temper uses guarded negation tables and complete verb tables, including naming verbs."""  # noqa: DOCSTRING_CJK
    for neg in D._ZH_NEG_SINGLES_GUARDED + D._ZH_NEG_MULTIS:
        assert neg in D._ZH_DIRECTIVE_AHEAD, neg
    assert D._ZH_VERBS_WITH_ADDRESS in D._ZH_DIRECTIVE_AHEAD
    # Never allow bare single-character negations directly, which causes compound misclassification.
    assert '"|".join(_ZH_NEG_SINGLES ' not in D._ZH_DIRECTIVE_AHEAD


@pytest.mark.parametrize("verb", sorted(D._ZH_PREPOSED_SAY_VERBS))
@pytest.mark.parametrize(
    ("prefix", "expected"),
    [("關於工作就別", "工作"), ("关于工作就别", "工作"),
     ("工作別", "工作"), ("工作别", "工作")],
)
def test_the_guanyu_template_shares_the_preposed_trigger_table(verb, prefix, expected):
    """The dedicated 关于 template still hardcoded triggers, making 關於工作就別提起了。
    fall back to generic and persist filler 就 too (codex P2, also simplified)."""  # noqa: DOCSTRING_CJK
    text = f"{prefix}{verb}了。"
    assert expected in _zh_terms(text), (text, _zh_terms(text))


# ── 57. Temper includes reluctance / nested branches tempered too / complete character variants ──


@pytest.mark.parametrize(
    ("opener", "closer"),
    sorted((lo, hi) for lo, hi in D._ZH_CLOSE_FOR_OPEN.items() if lo.isascii()),
)
@pytest.mark.parametrize("head", sorted(D._ZH_RELUCTANCE))
def test_reluctance_directives_also_split_bracket_runs(opener, closer, head):
    """Temper recognized only negation + verb, omitting 我不想 / 沒心情 / 懶得 templates;
    别提价格<预算.我不想聊收入>目标. merged as one (codex P2, simplified regression)."""  # noqa: DOCSTRING_CJK
    text = f"别提价格{opener}预算.{head}聊收入{closer}目标."
    assert f"价格{opener}预算" in _zh_terms(text), (text, _zh_terms(text))
    assert f"价格{opener}预算.{head}聊收入{closer}目标" not in _zh_terms(text)


@pytest.mark.parametrize(
    ("opener", "closer"),
    sorted((lo, hi) for lo, hi in D._ZH_CLOSE_FOR_OPEN.items() if lo.isascii()),
)
def test_the_nested_branch_is_tempered_too(opener, closer):
    """Nesting is **another path**; adding lookaheads only to outer single-character branches is ineffective.

    The engine takes nested branches and consumes <预算.别提收入> as one nested level,
    merging 别提价格<<预算.别提收入>目标>. again (codex P2, simplified regression).
    Same site and pitfall as excluding punctuation from nested branches, encountered twice.
    """  # noqa: DOCSTRING_CJK
    text = f"别提价格{opener}{opener}预算.别提收入{closer}目标{closer}."
    assert _zh_terms(text) == {f"价格{opener}{opener}预算", f"收入{closer}目标"}, text


@pytest.mark.parametrize(
    "text",
    ["別提案下さい。", "地域別講座下さい。", "別提案出来る。", "別提案出来ない。",
     "別討論出来た。"],
)
def test_kanji_spellings_of_listed_markers_are_covered(text):
    """Kanji spellings of ください / できる are also common; Kana-only entries miss half.

    This does not broaden criteria, merely completes orthography of **existing entries**, leaving which words
    identify Japanese sentences unchanged.
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == set(), text


@pytest.mark.parametrize(
    ("text", "expected"),
    [("别再提提出来的事。", "提出来的事"), ("別再提出来高。", "出来高"),
     ("别再提下册。", "下册"), ("别再提出租车。", "出租车")],
)
def test_only_inflected_kanji_forms_are_added(text, expected):
    """Add only inflected forms **with okurigana**. Bare 出来 / 下 would wrongly reject Japanese noun
    出来高 and Chinese 提出来 / 下册 / 出租车."""  # noqa: DOCSTRING_CJK
    assert expected in _zh_terms(text), (text, _zh_terms(text))
