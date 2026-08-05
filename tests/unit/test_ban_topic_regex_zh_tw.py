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
import unicodedata

import pytest

from config.prompts import prompts_directives as D
from config.prompts.prompts_directives import extract_directives


def _zh_terms(text: str) -> set[str]:
    """Terms extracted by the **zh** templates only (ja hits are a different owner)."""
    return {term for locale, _kind, term in extract_directives(text) if locale == "zh"}


def _zh_pattern_sources() -> list[str]:
    return [raw for locale, _kind, raw in D._PATTERNS_RAW if locale == "zh"]


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


# ── 1. 结构：简体字出现的地方必须有繁体孪生字 ─────────────────────
# 自动发现式，不是清单式：新加一条 zh 模板忘了写繁体，这里就红。
# 只列本模块 zh 模板里实际用到的字。
SIMPLIFIED_TO_TRADITIONAL = {
    "别": "別", "说": "說", "讲": "講", "谈": "談", "讨": "討", "论": "論",
    "这": "這", "个": "個", "话": "話", "题": "題", "关": "關", "于": "於",
    "愿": "願", "懒": "懶", "没": "沒", "许": "許", "称": "稱", "为": "為",
    # ⚠️ 故意不收 准 → 準：繁体"不允许"本来就写 ``不准``（已在表内），而 ``不準``
    # 是"不准确"，收进来 "測量不準說明有問題" 会被抓成 ban_topic。
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
    """把一个分支里的简体字逐个换成繁体孪生字。"""  # noqa: DOCSTRING_CJK
    return "".join(SIMPLIFIED_TO_TRADITIONAL.get(ch, ch) for ch in branch)


def _alternation_groups(pattern: str) -> list[list[str]]:
    """取出 pattern 里每个 ``(?:a|b|c)`` / ``(?>a|b|c)`` 组的分支列表。

    ⚠️ 原子组 ``(?>`` 一定要一起读：动词组正是原子的，只读 ``(?:`` 的话
    整条模板最要紧的那一维根本没进对偶检查。
    """  # noqa: DOCSTRING_CJK
    groups = []
    for match in re.finditer(r"\(\?[:>]([^()\[\]]*?)\)", pattern):
        body = match.group(1)
        if "|" in body:
            groups.append(body.split("|"))
    return groups


def test_the_alternation_group_reader_finds_the_verb_groups():
    """前提守卫：读不出分支组的话，下面那条逐分支对偶是空的。"""  # noqa: DOCSTRING_CJK
    groups = [g for raw in _zh_pattern_sources() for g in _alternation_groups(raw)]
    assert groups, "一个分支组都没读出来"
    flat = {b for g in groups for b in g}
    # 四条模板里各挑一个只出现在自己那条上的分支
    for expected in ("提了", "不愿意", "懒得", "这话题"):
        assert expected in flat, expected


@pytest.mark.parametrize("template_no", range(1, 5))
def test_every_branch_has_its_script_twin_in_the_same_group(template_no):
    """⚠️ 逐**分支**对偶，不是「整条 pattern 里有没有这个码位」。

    同一条模板里只要还有别的分支带着那个繁体字，删掉某一个繁体分支照样绿：模板 3 的
    ``(?:不想|不愿意|不願意|不愿|不願|…)`` 里两个含 ``願`` 的分支互相打掩护，删掉
    ``|不願意`` 之后 ``我不願意聊工作`` 整条不落库，而按码位查的那条断言全绿。

    ⚠️ 双向：留繁体删简体同样要红——受害的是简体用户，方向和本 PR 的主张相反，
    但一样是回归（删掉模板 2 的 ``|讲`` 保留 ``|講`` 曾经无人看守）。
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


# ── 2. 召回：繁体祈使句能抽到 term ──────────────────────────────
# 从否定词 × 动词 × 对象做笛卡尔积，而不是手抄几个句子。
TW_NEGATIONS = ("別", "不要", "不許", "莫", "甭", "休")
CN_NEGATIONS = ("别", "不要", "不许", "莫", "甭", "休")
TW_OBJECTS = ("工作", "前男友", "我的體重")
CN_OBJECTS = ("工作", "前男友", "我的体重")

# ⚠️ 动词维**从模块常量派生**，不手抄。手抄的那版在注释里写下了「复合动词必须排在
# 单字前缀之前」这条不变量，然后自己漏了 提到 / 聊起 / 講起 / 扯到 和整个 X及 族
# （54 格漏 28 格）。派生之后，往 _ZH_SAY_VERBS / _ZH_SAY_COMPOUNDS 里加字会
# 自动进笛卡尔积。
#
# ⚠️ 「言说动词 × 结果补语」的那一维已经拿掉：见 _ZH_SAY_VERBS 上方的注释，
# ``提／到达时间`` 与 ``提到／达时间`` 无法局部区分，吃掉补语会把话题首字一并吃掉。
ALL_VERBS = tuple(list(D._ZH_SAY_COMPOUNDS) + list(D._ZH_SAY_VERBS))
TW_VERBS = tuple(v for v in ALL_VERBS if not any(ch in "说讲谈论" for ch in v))
CN_VERBS = tuple(v for v in ALL_VERBS if not any(ch in "說講談論" for ch in v))

# 唯一被刻意排除的组合：``休`` + 以 ``講`` 开头的动词。``休講`` 是日文（＝停课），
# 中文没人这么写，而 ``休`` 的词首规则拦不住句首的它。见 _ZH_XIU 的 ``(?!講)``。
def _is_excluded_pair(negation: str, verb: str) -> bool:
    return negation == "休" and verb.startswith("講")


# ⚠️ 派生的笛卡尔积有个固有盲区：从常量派生的测试，**改常量的同时也改了测试**——
# 把 谈论/談論 从 _ZH_SAY_COMPOUNDS 里删掉，上面的积会跟着缩小而全绿。所以三个
# 构词维度各自用相等断言钉死（闭集），改动必须同时改这里。
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
    """⚠️ 这条曾经断言 ``不要谈论政治。`` 得到 ``政治``（把 ``谈论`` 当复合动词）。
    撤掉了：同样的切分会把 ``别谈论语考试。`` 削成 ``语考试``、``别谈论语。`` 整条
    削没（codex P2）。``論政治`` 多一个字但话题仍完整，模型对得上；``语考试`` 是非词。
    繁简两侧必须同时是这个行为——这条测试的意义已经从「复合动词」变成「繁简一致」。
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
    # ⚠️ 断言的是**相等**而不是包含：包含判据放过 "起工作" / "到我前女友" 这类
    # 复合动词被单字前缀吃掉的结果，正是那样才让 28 格漏了两轮没被发现。
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


# 四条 zh 模板各自的代表句，繁简成对——单靠上面的笛卡尔积只压到模板 1。
TEMPLATE_PAIRS = [
    # (繁体句, 简体句, 期望 term 的繁简写法)
    ("別再提小明了", "别再提小明了", ("小明", "小明")),
    ("不要再說工作的事了！", "不要再说工作的事了！", ("工作的事", "工作的事")),
    ("這件事別提了", "这件事别提了", ("這件事", "这件事")),
    # 模板 2 的四个"填充词"分支各钉一条：少了任何一个，term 就会把填充词一起吞进去
    ("工作這個別提了", "工作这个别提了", ("工作", "工作")),
    ("工作這事別提了", "工作这事别提了", ("工作", "工作")),
    ("工作這話題別提了", "工作这话题别提了", ("工作", "工作")),
    ("工作這件事別提了", "工作这件事别提了", ("工作", "工作")),
    # ⚠️ ``的事`` 归话题所有——四条模板统一口径（见
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
    """对偶性：同一句话的两种字形抽到对应的 term，一侧改坏另一侧就露馅。"""  # noqa: DOCSTRING_CJK
    tw_expected, cn_expected = expected
    assert tw_expected in _zh_terms(tw), f"繁体 {tw!r} 抽不到 {tw_expected!r}"
    assert cn_expected in _zh_terms(cn), f"简体 {cn!r} 抽不到 {cn_expected!r}"


# ── 3. 日文不碰撞 ────────────────────────────────────────────
# 只有 _is_japanese_sentence_match 拦得住的样本 —— 每一条在守卫拿掉后都真的会被 zh
# 模板抓出 term（下面的 premise 断言就是这么验的），所以这张表不会悄悄退化成一堆无关
# 句子而全绿。``特別講演について``、``今日は休講です`` 之类由 _BIE_COMPOUND_LEFT /
# 休 词首规则先挡下，放在 JAPANESE_BLOCKED_ELSEWHERE 里另测。
# ⚠️ 前缀一律用 ``地域別 / 年齢別 / 職種別``（日文能产的 ``〜別`` 后缀），不用
# ``個別 / 特別``：后两者的左界字已经在 _BIE_COMPOUND_LEFT 里，会被守卫 1 先挡下，
# 拿它们当样本压不住日文守卫（premise 测试会红）。
JAPANESE_KANA_GUARDED = [
    "地域別提案をお願いします。",
    "地域別講座の一覧。",
    "年齢別講座の案内です。",
    "職種別談話会のお知らせ",
    "地域別談話をお願いします。",
    "年齢別講座に申し込みました。",
    # 助词表按闭集补全之前漏的（codex P2）：只列 のにをはがでと 时这些都会漏出去
    "地域別提案ください。",
    "地域別講座へ申込。",
    "年齢別提案から選択。",
    "地域別提案など検討。",
    "職種別講座まで案内。",
    "地域別談話でも可。",
    "年齢別提案だけ確認。",
    "地域別講座について質問。",
    # 接续助词：这两条的 term 里**没有**单字格助词，只有 けど / たら 拦得住
    "職種別提案したけど。",
    "地域別提案したら連絡。",
    # 口语系 copula / 终助词（codex P2）
    "地域別講座だね。",
    "世代別講座だよ。",
    "部門別提案かな。",
    "地域別提案だっけ。",
    "職種別講座でしょ。",
    "地域別提案かも。",
    # 过去 / 义务 / 被动 / 进行 等谓语形式（codex P2）
    "地域別講座だった。",
    "世代別提案だって。",
    "商品別提案すべき。",
    "地域別提案される。",
    "世代別講座している。",
    # 含「曾被误当成中文证据」的日文汉字：没（没収）/ 称（名称）。它们**就是**日文
    # 标准字形，不是 沒 / 稱 的简体专用形（codex P2）。
    "地域別提案で没になりました。",
    "地域別講座の名称を確認します。",
    "地域別提案の名称です。",
    "地域別講座は没収された。",
]
# 假名开头的 ``〜別``：term 里一个助词都没有（``スレ`` / ``案書``），(2b) 够不着，
# 只有「命中区间左边紧挨着假名」这条拦得住（对抗排查）。
JAPANESE_KANA_PREFIXED = [
    "ジャンル別討論スレ",
    "カテゴリ別提案書。",
    "テーマ別討論スレッド。",
    "メーカー別提案資料",
    "タイプ別提案書。",
]
JAPANESE_BLOCKED_ELSEWHERE = [
    "今日は休講です。",
    # ⚠️ 这两条是给中文证据的逐分支扫描当靶子的：往证据正则尾部追加 ``|題``、或者
    # 往主语白名单里混进日文汉字 ``俺``，都会在这里现形（变异跑出来的）。
    "話題別提案について検討します。",
    "俺別提案をお願いします。",
    "俺別講座のご案内。",
    # ⚠️ ``〜別`` 的分类标签不止以汉字假名结尾：拉丁字母 / 数字（半角全角）/
    # 收尾括号都会出现在它前面（codex P2）
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
    # 纯汉字、一个假名都没有 —— 假名守卫够不着，只有 _BIE_COMPOUND_LEFT 收了
    # ``個`` 才挡得住（codex P2）
    "個別提案書。",
    "個別提案資料。",
    "個別講座案内。",
    # 句首的 休講 —— 休 的词首规则拦不住它，靠 _ZH_XIU 的 (?!講)（对抗排查）
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


# ⚠️ 已知残留，**故意断言当前的错误行为**：日文能产的 ``〜別`` 后缀（地域別 /
# 年齢別 / 世代別 / 商品別…）前缀是任意名词，是开集；三道守卫各自够不着——
# _BIE_COMPOUND_LEFT 只收零反例的几个字，左邻假名判据要求 別 前面是假名（这里是
# 汉字），助词判据要求 term 里有助词（这里是纯片假名名词）。
#
# 唯一想到的补法是「term 以该动词所领复合词的第二字开头 + term 含片假名」
# （講座→座、提案→案），实测会把 ``你別提初音ミク。`` 一起打死——分界线要落在
# 「別 前面那个汉字是不是名词」上，而 ``世代`` 和 ``你`` 都是汉字。代价方向：
# 日文侧是一条三天后过期的垃圾 term，繁中侧是指令根本不落库，所以选择不修。
#
# 断言写成"当前长什么样"而不是"应该是空"，是为了将来真找到判据时这里现成就是
# 回归测试——那时把它改成 == set() 即可。
KNOWN_JAPANESE_RESIDUALS = [
    # ⚠️ 日文的名词 / 形容动词后缀（済み / 向け / 付き / 込み / っぽい …）：这一维是
    # **开集**，逐个补就是打地鼠，而且和下面那条 kana-free 的是同一族——``別`` +
    # 共用动词 + 日文复合词，局部无规则可分。如实挂着，不装作已经关掉（codex P2）。
    ("別提案済み。", {"案済み"}),
    # ⚠️ 纯假名标题 + 繁体裸 ``別提``：单字助词类（のにをはがでとへ）不带左右界，
    # 标题里恰好含 ``に`` / ``が`` 就整条被吞。试过给它加「汉字词干」左界——
    # ``にじさんじ`` 这条**依然**修不好（只顺带救回 ``ありがとう``），却连带打红
    # 十条结构守卫，动的还是整个日文守卫最核心的字类。收益一半、风险最大，撤回。
    ("別提にじさんじ。", set()),
    ("地域別講座向け。", {"座向け"}),
    ("別提案っぽい。", {"案っぽい"}),
    # ⚠️ 只剩**不含假名**的这一条。日文里 ``別`` + 共用动词 + 纯汉字复合词
    # （別提案書 / 別講座資料）和中文的 ``別提 + 汉字话题``（別提工作）在局部
    # 完全同形，任何规则都分不开——真去分就会打死 ``別提工作。``（量过）。
    ("地域別提案書。", {"案書"}),
]
# 带假名尾巴的那三条**已经不是残留**了：``〜別 + 复合名词 + 光杆假名名词``
# 靠「左边不是小句边界」+「term 是一个汉字接假名」两条判据关掉了（codex P2）。
# ⚠️ 留在这里做回归：它们曾经是残留，退回去要立刻红。
FIXED_JAPANESE_KANA_TAILS = [
    "世代別講座ガイド。", "商品別提案プラン。", "部門別提案リスト",
    "地域別提案スレ", "A別講座スレ", "2別講座メモ", "β別提案スレ",
    "世代別提案まとめ", "商品別談話スレ",
]


@pytest.mark.parametrize("text", FIXED_JAPANESE_KANA_TAILS)
def test_kana_tailed_bessu_labels_are_suppressed(text):
    assert _zh_terms(text) == set(), text


def test_the_kana_tail_guard_needs_a_left_label():
    """⚠️ 串首的 ``別提案スレ`` 仍然漏——如实记着，别当成已经关掉。

    ``〜別`` 是**后缀**，左界这条判据要求它前面挂着标签词。去掉左界这条就能
    连串首一起关，代价是 ``別提蘭ちゃん。`` 这类「单字汉字 + 假名」的中文话题
    会被一起打死。这一格选了漏判。
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
    """上面那个残留的候选补法会把这几条一起打死——真要修的时候先跑这里。"""  # noqa: DOCSTRING_CJK
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


# ⚠️ 反向的坑：被 ban 的**对象本身**经常就是日文专有名词。这类句子结构是中文的，
# 假名只是话题名——「命中区间有假名就丢」会把用户明确说过的偏好扔掉（codex P2）。
# 这个产品的用户尤其容易这么说话（"別叫我お兄ちゃん"）。
CHINESE_WITH_JAPANESE_TOPIC = [
    ("别再提ドラえもん。", "ドラえもん"),            # 简体触发词 = 日文没有的码位
    ("別再提ドラえもん。", "ドラえもん"),            # 繁体触发词 + 共用动词，靠 term 无助词
    ("別叫我お兄ちゃん。", "お兄ちゃん"),            # 叫我 = 日文不会出现的组合
    ("别叫我お兄ちゃん", "お兄ちゃん"),              # 无句末标点
    ("不想聊ドラえもん。", "ドラえもん"),            # 不想 = 中文证据
    ("不要聊ドラえもん。", "ドラえもん"),            # 不要本身是日文词，靠 term 无助词过
    ("别再提君の名は。", "君の名は"),                # 标题自带助词，只能靠中文证据救
    ("我不想聊初音ミク", "初音ミク"),
    # ⚠️ 裸 だ 不能进日文助词表：真实歌名里就有（だんご三兄弟）。这条必须用**繁体**
    # 触发词——简体 ``别`` 是中文证据，守卫在查助词之前就短路了，压不住这一维。
    ("別提だんご三兄弟。", "だんご三兄弟"),
    ("别再提だんご三兄弟。", "だんご三兄弟"),
]


@pytest.mark.parametrize(("text", "expected"), CHINESE_WITH_JAPANESE_TOPIC)
def test_chinese_directive_about_a_japanese_topic_is_kept(text, expected):
    assert expected in _zh_terms(text), f"{text!r} 的 ban 对象被日文守卫误丢"


@pytest.mark.parametrize(("text", "expected"), CHINESE_WITH_JAPANESE_TOPIC)
def test_those_samples_really_do_go_through_the_guard(text, expected):
    """Premise: 这些样本**命中区间**里确实有假名，所以它们真的会走到守卫判据，
    而不是因为压根没假名才侥幸通过。

    ⚠️ 判据必须打在命中区间和 term 上，不能打在整条消息上：守卫收到的是
    ``m.group(0)`` 和 ``term``，消息里别处的假名对它毫无意义。
    ``剛看完 ドラえもん。別提工作。`` 这种句子整条有假名、命中区间却没有——
    打在整条上的话它会被当成"证明了守卫放行"，而实际上守卫根本没被考验到。
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


# 中文证据表里的每个 token 各配一条载荷样本：话题名自带助词时，只有这个 token 能
# 把整条命中救回来（term 不含助词的样本走的是另一条判据，压不住这一维）。
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
    """``甭`` 是简体独有字，日文里根本没有这个汉字，所以带它的句子不可能是日文。
    不收进证据表的话，"甭提 + 日文专名" 会被日文守卫整条抑制，而同结构的
    "别提 + 日文专名" 不会——同一模板内的行为不对称（对抗排查）。"""  # noqa: DOCSTRING_CJK
    assert "甭" in D._ZH_EVIDENCE_RE.pattern
    assert expected in _zh_terms(text)


def _split_top_level_alternatives(pattern: str) -> list[str]:
    """按 ``|`` 切顶层分支，不切进 ``(...)`` / ``[...]`` 里面。"""  # noqa: DOCSTRING_CJK
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
    """前提守卫：切分器本身要正确，否则下面那条扫描是空的。"""  # noqa: DOCSTRING_CJK
    assert _split_top_level_alternatives("a|b") == ["a", "b"]
    assert _split_top_level_alternatives("[a|b]|c") == ["[a|b]", "c"]
    assert _split_top_level_alternatives("(?:a|b)|c") == ["(?:a|b)", "c"]
    assert _split_top_level_alternatives(chr(92) + chr(124)) == [chr(92) + chr(124)]


def test_no_zh_evidence_alternative_matches_the_japanese_corpus():
    """⚠️ 自动发现，不是逐字审：中文证据的**每一条分支**都不许在日文语料里命中。
    这张表是用来 short-circuit 日文守卫的——命中一次等于把守卫整个关掉。
    ``没``（没収）和 ``称``（名称）就是这么漏进来的。

    ⚠️ 判据打在**整条正则的每个顶层分支**上，不是只打第一个字符类。只扫第一个
    字符类的话，写在它后面的证据（第二个字符类、或裸的 ``|題``）完全隐形——实测
    追加 ``|題`` 之后 ``話題別提案について検討します。`` 就会被当成中文指令存下来，
    而字符串比对那条断言一字未变、全绿。

    这条随日文语料一起长：以后往语料里加句子，收错的分支会被自动抓出来。
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
    """闭集用相等断言：每个字都是一句"日文里不存在这个字形"的主张，加字要先核对
    日文新字体（别→別 说→説 关→関 为→為…）。"""  # noqa: DOCSTRING_CJK
    # ⚠️ 拆成「触发词字」+「中文独有的句末助词」两段，各自相等断言。后者是新加的：
    # 话题本身带日文助词时（``別提君の名は吧。``），落在捕获组外的 ``吧`` 是唯一能
    # 证明这是中文指令的东西（codex P2）。
    assert D._ZH_EVIDENCE_CHARS == (
        "别说讲谈讨论关这话题愿懒许为甭說這關沒稱" + D._ZH_ZH_ONLY_FINAL_PARTICLES
    )
    assert D._ZH_ZH_ONLY_FINAL_PARTICLES == "啊呀嘛哦呗吧啦呢喔唷齁欸誒咧喲囉啰"
    assert D._ZH_EVIDENCE_WORDS == (
        "叫我", "喊我", "管我叫", "不想", "懶得", "不願", "没心情",
        "称呼我", "稱呼我",
    )
    # ⚠️ 「动词 + 了」这批是**派生**的，所以断言写成派生关系而不是抄一份字面量
    # ——抄一份的话，改动词表要顺手改测试，守卫就只是复读代码（同一个坑在
    # 「派生笛卡尔积」那类测试上栽过）。字面量那份在下面单独钉。
    assert D._ZH_VERB_LE_EVIDENCE == tuple(
        v + "了" for v in dict.fromkeys(
            D._ZH_SAY_COMPOUNDS + D._ZH_SAY_VERBS + D._ZH_PREPOSED_SAY_VERBS
        )
    )
    assert set(D._ZH_VERB_LE_EVIDENCE) == {
        "讨论了", "討論了", "说了", "說了", "提了", "聊了", "讲了", "講了",
        "谈了", "談了", "扯了", "提起了", "提及了",
    }
    # 字类必须是整条正则的第一个分支——上面那条扫描不依赖这点，但 pin 住它能让
    # 「有人往字类前面插了新分支」这件事在 review 里显形。
    first = _split_top_level_alternatives(D._ZH_EVIDENCE_RE.pattern)[0]
    assert first == f"[{D._ZH_EVIDENCE_CHARS}]"


def test_the_grammar_marker_set_excludes_mo():
    """⚠️ ``も`` 是助词，但它出现在 ``ドラえもん`` 里。把它收进标记表，上面那批
    「中文句子 + 日文话题名」的用例就会被打回去——这是个反向的坑，写死在这里。"""  # noqa: DOCSTRING_CJK
    assert not D._JA_GRAMMAR_RE.search("ドラえもん"), (
        "助词表把 ドラえもん 判成了日文句子（多半是收了 も）"
    )
    assert not D._JA_GRAMMAR_RE.search("お兄ちゃん")


# ── 4. 复合词左界守卫 ────────────────────────────────────────
def test_compound_left_set_is_pinned():
    """闭集断言用相等：这张表里每个字都是一句"该字后面的 别 一定不是祈使"的主张，
    加字要先确认没有自然反例。``个/個`` 能收进来是因为守卫收窄到了模板 1——
    "工作这个别提了" 走模板 2，不受影响。"""  # noqa: DOCSTRING_CJK
    assert D._BIE_COMPOUND_LEFT == "特性区區级級个個"


@pytest.mark.parametrize("verb", ("说", "說", "提", "讲", "講", "谈", "談"))
@pytest.mark.parametrize("left", tuple("特性区區级級"))
def test_compound_noun_is_not_read_as_an_imperative(left, verb):
    """他特别提到 / 級別提升 —— 别 是复合词词尾，不是"别说"。"""  # noqa: DOCSTRING_CJK
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


# ⚠️ 守卫**只挂在模板 1**。模板 2/4 的 ``别`` 前面是被捕获的话题本身，话题正好以
# 守卫字结尾时（模特 / 可能性 / 等级 / 地区）挂上去会把整条指令吃掉（codex P2）。
TOPIC_ENDING_IN_A_GUARDED_CHAR = [
    ("模特别提了。", "模特"),
    ("模特別提了。", "模特"),
    ("这种可能性别提了。", "这种可能性"),
    ("這種可能性別提了。", "這種可能性"),
    ("等级别提了。", "等级"),
    ("那个地区别提了。", "那个地区"),
    # 模板 4 同理
    ("關於模特別提了", "模特"),
    ("关于模特别提了", "模特"),
    ("關於可能性別說了", "可能性"),
]


@pytest.mark.parametrize(("text", "expected"), TOPIC_ENDING_IN_A_GUARDED_CHAR)
def test_topic_ending_in_a_guarded_char_survives(text, expected):
    assert expected in _zh_terms(text), f"{text!r} 的话题被复合词守卫吃掉了"


@pytest.mark.parametrize(("text", "expected"), TOPIC_ENDING_IN_A_GUARDED_CHAR)
def test_those_topics_really_do_end_in_a_guarded_char(text, expected):
    """Premise: 话题最后一个字确实在守卫表里，否则这条样本证明不了守卫的作用域。"""  # noqa: DOCSTRING_CJK
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
    """个/分/格/年 故意留在守卫之外：它们都有真实反例，收紧会把主用例打死。"""  # noqa: DOCSTRING_CJK
    assert expected in _zh_terms(text), f"{text!r} 期望 {expected!r}"


# ── 5. 休 只在词首算否定 ─────────────────────────────────────
@pytest.mark.parametrize("prefix", ("退", "午", "调", "調", "补", "補", "年", "公"))
@pytest.mark.parametrize("verb", ("说", "說", "讲", "講", "提"))
def test_xiu_inside_a_compound_is_not_a_negation(prefix, verb):
    """退休讲 / 午休提前 / 調休說明 —— 休 是复合词的后半，不是"休提"。"""  # noqa: DOCSTRING_CJK
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
        # ⚠️ 不能用词首规则：Python 把相邻汉字都算 \w，一道 (?<!\w) 会把这些正常
        # 句子全打死（codex P2）。改用和 别 同形的复合词左界表。
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


# ── 5b. 台湾句末助词不能粘在 term 上 ─────────────────────────
# 存进 user_directives 的是 term 本身，会逐字注进 system prompt。助词粘上去
# ("工作喔") 就是把一个不存在的话题名喂给模型（codex P2）。
# ⚠️ ``囉`` / ``啰`` 不在这里：它们同时是 ``嘍囉 / 喽啰`` 的末字，和 ``耶 / 捏``
# 一样整个不收，见 test_luoluo_is_a_word_not_a_final_particle。
TAIWANESE_FINAL_PARTICLES = (
    "喔", "唷", "齁", "欸", "誒", "咧", "喲",
)
# 反问尾巴跟在句末助词后面（"工作了好嗎"）：正则的可选助词组只放行一个，剩下的
# 并进 term，靠 trim 的循环剥。
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
    """放行（正则）与剥离（trim）成对：少一边 term 就带着助词存进去。"""  # noqa: DOCSTRING_CJK
    assert particle in D._TRIM_TRAIL_TOKENS_BY_LOCALE["zh"], f"{particle} 不在 zh trim 表"


@pytest.mark.parametrize("glyph", ("唄", "耶", "捏"))
def test_ambiguous_particle_glyphs_are_not_treated_as_particles(glyph):
    """⚠️ 这三个字正则和 trim 都不收，理由是**代价方向**而不是"它们不是助词"。

    收了：常见说法能拿到干净的 term（"工作耶"→"工作"），但罕见话题被腰斩成非词
    （"精准拿捏"→"精准拿"、"音樂人坎耶"→"音樂人坎"、"花の唄"→"花の"）。
    不收：常见说法多带一个字，term 里仍然完整含着真话题，模型对得上。
    宁可多一个字，不可少一个字。``唄`` 另有一层——它在日文里是"歌"。
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
    """长度下限只护住"剥完不足 2 字"的那一档，3 字以上的照样被吃（codex P2）。"""  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == {expected}


def test_bei_is_not_treated_as_a_particle_at_all():
    """⚠️ ``唄`` 正则和 trim 都不收。它是 ``呗`` 的繁体，但在日文里是"歌"：留在 trim
    表里会削掉 ja 模板的 term（``子守唄``→``子守``），只留在正则放行组里同样会削掉
    zh 模板的（``别再提花の唄。``→``花の``）——两轮各中一次。而 ``呗`` 本就是北方
    口语词、台湾并不说 ``唄``，为它承担这个代价不划算。"""  # noqa: DOCSTRING_CJK
    assert "唄" not in D._ZH_FINAL_PARTICLES
    assert "唄" not in D._TRIM_TRAIL_TOKENS_BY_LOCALE["zh"]
    # 代价说清楚：一个真写 ``唄`` 的台湾用户会多存一个字，这是刻意的取舍。
    assert _zh_terms("別再提工作唄") == {"工作唄"}
    # 换来的是日文歌名两种写法都完整
    assert _zh_terms("别再提花の唄。") == {"花の唄"}
    assert _zh_terms("别再提花の唄了。") == {"花の唄"}
    assert "子守唄" in {t for loc, _k, t in extract_directives(
        "子守唄のことはもう言わないで") if loc == "ja"}


def test_stacked_particles_are_all_stripped():
    assert _zh_terms("不要再說這件事了喔") == {"這件事"}


# ⚠️ 这些助词同时也是普通的词尾字（拿捏 / 坎耶 / 好咧 / 耶稣）。台湾**确实**在用
# 它们做语气词（不像 ``唄``），所以不能像 ``唄`` 那样整个删掉——只能保证"当成助词
# 剥掉之后 term 短到存不下"时改走另一种切法（codex P2）。
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
        # 话题只有两字、末字又是助词：剥掉就低于下限、整条指令消失，所以不剥。
        ("别再提好咧。", {"好咧"}),
        ("別再提好咧。", {"好咧"}),
        # 对照：话题够长时助词照剥，下限守卫不是"永不剥"。
        ("别再提工作咧。", {"工作"}),
        ("別再提工作咧。", {"工作"}),
    ],
)
def test_trim_never_shortens_a_term_below_the_storable_minimum(text, expected):
    """⚠️ 断言**完整集合**并且真的用上 text。

    这条原本只断言 `_trim_term` 的直接行为、根本没跑 `text`——不但下限失效时不会
    红，连我写在参数里的期望值本身是错的（`别再提工作咧。` 其实是 `工作`）都一直
    没暴露（CodeRabbit）。
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == expected
    # 直接压一下 trim 的下限判据本身
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
    """模板 2 的前缀同理：下限抬到 2 之后 lazy 前缀会跨过句读，把上一句的尾巴
    并进话题（"算了，工作"）。"""  # noqa: DOCSTRING_CJK
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
    """⚠️ 换行必须在字符类里**显式**排除。这些捕获组原本写的是 ``.``，在没有
    DOTALL 时天然不匹配换行；改成负字符类之后这个性质就没了，多行消息里 term 会把
    换行连同**下一条指令**一起吞掉（"别提工作\\n别提加班" → "工作\\n别提加班"）。

    ⚠️ 断言**完整集合**：只遍历结果检查"不含换行"的话，结果为空时是空跑
    （CodeRabbit）。
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # 模板 3：可选的 ``的事`` 会把单字主语削到长度下限之下（既有对称缺陷）
        ("我不想聊钱的事", "钱的事"),
        ("我不想聊錢的事", "錢的事"),
        ("我不想聊我的事", "我的事"),
        # 模板 4 同理
        ("关于钱的事别提了。", "钱的事"),
        ("關於錢的事別提了。", "錢的事"),
    ],
)
def test_single_character_subject_survives_the_optional_de_shi(text, expected):
    assert expected in _zh_terms(text), f"{text!r} -> {_zh_terms(text)}"


def test_bracket_and_plain_branches_are_mutually_exclusive():
    """⚠️⚠️ 这是一条 ReDoS 护栏，不是风格问题。

    单字分支也能匹配 ``《``，于是 ``《a》`` 既可以被括号分支整体吃掉、也可以被单字
    分支逐字吃掉；这个歧义放进 ``{2,30}?`` 的重复里就是指数级回溯——``别提`` 加
    30 段 ``《a》`` 要跑 1.3 秒，而这条路径每条用户消息都会走（codex P1）。

    解法是把整个"单位"包进**原子组**：某个位置选了哪个分支就不再回头。比"把开括号
    排除出单字分支"更好——落单的 ``"`` / ``(``（英寸号、颜文字）仍能被当普通字吃掉。
    """  # noqa: DOCSTRING_CJK
    import time

    assert D._ZH_TOPIC_CHAR.startswith("(?>"), (
        "话题单位不是原子组，括号分支与单字分支重叠 = 回溯爆炸"
    )
    for segment in ("《a》", '"a"', "(a)"):
        started = time.perf_counter()
        extract_directives("别提" + segment * 120)
        elapsed = time.perf_counter() - started
        assert elapsed < 1.0, f"{segment} x120 跑了 {elapsed:.2f}s，回溯又爆了"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # ASCII 成对定界符：这些在 parent 上是完整的，不收就是回归（codex P2）
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
        # ⚠️ 落单的 ASCII 定界符不能变成硬边界——这正是原子组方案相对"排除开括号"
        # 的关键好处（英寸号、颜文字 :( 、英文撇号）。
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
        # 一整段括号算**一个**单位，`{2,n}` 会把独立成句的书名卡掉（codex P2）
        ("《你好，李焕英》别提了。", "你好，李焕英"),
        ("「你好，李焕英」别提了。", "你好，李焕英"),
        ("《你好，李煥英》別提了。", "你好，李煥英"),
    ],
)
def test_a_standalone_quoted_title_is_a_valid_topic(text, expected):
    assert _zh_terms(text) == {expected}


def test_all_four_zh_templates_share_one_topic_char_class():
    """四条模板各写各的字符类正是漂移的起点——共用一份常量，加一条模板也自动跟上。"""  # noqa: DOCSTRING_CJK
    assert r"[^，。！？；,.!?;\r\n" in D._ZH_PLAIN_CHAR
    assert D._ZH_BRACKET_PAIRS == (
        ("《", "》"), ("「", "」"), ("『", "』"), ("“", "”"), ("【", "】"),
        ("（", "）"), ("〈", "〉"), ("〔", "〕"), ("［", "］"), ("〖", "〗"),
        ('"', '"'), ("(", ")"), ("[", "]"), ("{", "}"), ("<", ">"),
    )
    # ⚠️ 单引号刻意不收：英文里它是词内撇号（don't / it's），配对没有意义。
    assert "'" not in {lo for lo, _hi in D._ZH_BRACKET_PAIRS}


def test_every_bracket_delimiter_is_also_trimmed():
    """不变量：凡是被当作话题分隔符的括号，两端都必须在 _TRIM_TRAIL 里。

    少一边 term 就带着括号存进去（`〔重要，紧急〕`）——新加一对括号忘了同步 trim
    表，这里就红，不用靠人记得。
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
        # ASCII 定界符在正则里是转义过的，比对时也要转义
        assert f"{_re.escape(lo)}(?:" in D._ZH_BRACKET_RUN, (
            f"{lo}{hi} 没进话题单位"
        )
    # 模板 2 的前置话题走 NO_GUANYU 变体（见 _ZH_PLAIN_CHAR_NO_GUANYU），其余走共用的。
    units = (D._ZH_TOPIC_CHAR, D._ZH_TOPIC_CHAR_NO_GUANYU)
    for raw in _zh_pattern_sources():
        assert any(u in raw for u in units), f"这条 zh 模板没走共用话题单位：{raw!r}"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # ⚠️ 书名号 / 引号里的标点属于话题本身，不是句子边界。一刀切排除句读会把
        # term 截成后半截（"电影《你好，李焕英》别提了。" → "李焕英"，codex P2）。
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
        # ⚠️ 样本必须让「闭合括号在下一行」——括号里根本没有闭合符号时，括号分支
        # 无论放不放行换行都失败，压不住这一维。
        ("别提《书名前半\n后半》了。", set()),
        ("别提「引文前半\n后半」了。", set()),
        ("别提《没闭合的书名\n别提加班。", {"加班"}),
    ],
)
def test_a_bracket_run_must_not_cross_a_line_break(text, expected):
    """括号段放行标点，但不放行换行——否则一个跨行的书名号会把两行连同中间的
    指令一起吞进 term。

    ⚠️ 断言**完整集合**而不是遍历结果逐条检查：结果为空时遍历零次，是空跑
    （CodeRabbit）。这已经是这个文件里第三次栽在"断言弱于主张"上了。
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
    """宾语下限抬到 2 之后，lazy 捕获会跳过本该收尾的句读去够更长的匹配——
    "功成名就别提了，功成名别提了。" 一度吐出 "了，功成名别提"。宾语用排除句读的
    字符类，话题本来也不该跨句子。

    ⚠️ 断言完整集合，不是"遍历结果确认不含句读"：结果为空时遍历零次（CodeRabbit
    在别处指出的同一个空跑模式，我扫了全文件把同类一起修了）。
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == expected


# ⚠️ trim 表必须按 locale 分开：同一个码位在不同语言里是不同的词。``唄`` 在中文
# 是 ``呗`` 的繁体语气词，在日文是"歌"（子守唄＝摇篮曲）；``了`` 在日文是 完了/
# 終了 的构词成分。拿中文那套去剥日文 term 会把词削掉一半（codex P2）。
JAPANESE_TERMS_ENDING_IN_A_CHINESE_PARTICLE = [
    ("終了のことはもう言わないで", "終了"),
    ("完了のことはもう言わないで", "完了"),
    # ⚠️ 三字以上的样本才压得住 locale 这一维：两字 term 被剥掉一个字就低于长度
    # 下限，trim 的下限守卫会替它挡住，看起来"没坏"。
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
    """Premise：term 结尾确实是中文助词表里的字，否则这条样本证明不了分表的必要。"""  # noqa: DOCSTRING_CJK
    assert expected[-1] in D._TRIM_TRAIL_TOKENS_BY_LOCALE["zh"], expected


@pytest.mark.parametrize("locale", ("zh", "ja", "ko", "en", "ru", "es", "pt"))
def test_ascii_tails_stay_global(locale):
    """ASCII 尾巴字形上不可能跨语言撞，中英混说又很常见（"别提 my ex please"），
    所以这些对每个 locale 都剥。"""  # noqa: DOCSTRING_CJK
    assert D._trim_term("my ex please", locale) == "my ex"


@pytest.mark.parametrize(
    ("text", "locale", "expected"),
    [
        ("stop talking about 前女友了", "en", "前女友"),
        ("don't mention 加班了", "en", "加班"),
        ("no hables de 相亲了", "es", "相亲"),
        ("не говори про 前任了", "ru", "前任"),
        ("não fale de 加班了", "pt", "加班"),
        # ⚠️ 混说的那一段也可能是**日文**，所以回落是 zh + ja 的并集，不是只有 zh
        ("stop saying 仕事ね", "en", "仕事"),
        # ⚠️ 韩语也要在回落里：谚文与汉字/假名不共码位，本来就没有当初促使分表的
        # 那种跨语言字形碰撞（codex P2）。
        ("stop saying 전남친은", "en", "전남친"),
        ("don't mention 직장에", "en", "직장"),
        ("stop talking about あの人よ", "en", "あの人"),
        ("don't mention my ex 啊", "en", "my ex"),
    ],
)
def test_non_cjk_locales_fall_back_to_the_cjk_particle_lists(text, locale, expected):
    """⚠️ 按 locale 分表之后 en/ru/es/pt 就没有 CJK 助词表了，但中英混说时 term
    往往整段是中文（"stop talking about 前女友了"），不回落就把 ``了`` 存进去——
    那是分表**之前**的既有行为，分表不该顺手改掉它。"""  # noqa: DOCSTRING_CJK
    terms = {t for loc, _k, t in extract_directives(text) if loc == locale}
    assert expected in terms, f"{text!r} -> {terms}"


# ── 5d. 「动词 + 结果补语」不切分，繁简一致 ──────────────────
# ⚠️ 这里曾经断言 ``別提起工作。`` 得到 ``工作``（把 ``起`` 当补语吃掉）。撤掉了：
# 同样的切分会把 ``别聊起点问题。`` 削成 ``点问题``（codex P2，简体也回归）。
# 补语留在 term 里是 base 的既有行为，也是安全方向——多一个字话题仍完整。
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
        # 模板 1（别/不要 + 动词）
        "别提起了。", "别提及了。", "别说起了。",
        "別提起了。", "別說到了。", "别谈及了。",
        # ⚠️ 模板 3（不想/懒得 + 动词）也要各配一条：只钉模板 1 的话，把模板 3 的
        # 前视删掉照样全绿，而它同样会产出 "起了" / "到了" / "及了" 这种假话题。
        "我不想再提起了。", "我懒得再说起了。", "我不想聊到了。",
        "我不想再提及了。", "我沒心情提起了。", "我懶得再說起了。",
        # ⚠️ 补语不是 到/起/及 这三个字：汉语的结果补语是开集，判据必须是「宾语只有
        # 一个字」这个**数量**。一度只列了三个字，下面这批全部漏网（codex P2 只报了
        # 完，实测 光/够/死/上 一样中招）。
        "别说完了。", "別提完了。", "我不想再说完了。", "别说光了。",
        "别提够了。", "别聊死了。", "别提上了。", "别提走了。",
        # 单字宾语本身（不是补语）也一样不该抽——parent 靠长度过滤丢弃它们
        "别提钱了。", "别提A了。", "別提錢了。",
    ],
)
def test_an_objectless_directive_does_not_invent_an_object(text):
    """⚠️ 动词之后只剩「结果补语 + 语气词」就是没有宾语，本模块 docstring 明确说
    不抽这种指令。靠 _ZH_OBJECTLESS_AHEAD 这道前视挡，**不能**改用把宾语下限降到 1
    来代替——下限 1 会让 lazy 宾语把话题末字让给可选助词组，``别提钱的事。`` 退化成
    ``钱`` 后撞长度下限整条消失（codex P2 两轮，方向相反）。
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == set(), f"{text!r} 被造出了话题：{_zh_terms(text)}"


def test_the_objectless_guard_is_not_the_topic_minimum():
    """两道闸各管一头，谁都不能顶替谁——把下限调回 1 这条会红。"""  # noqa: DOCSTRING_CJK
    assert "_ZH_OBJECTLESS_AHEAD" in D.__dict__
    # ⚠️ 话题体现在是「首单位 + 其余 {min-1, max-1}」，所以下限 2 在串里长成
    # ``{1,39}``。判据改成从 _zh_topic 自己派生，别再按字面找 ``{1,``。
    assert D._zh_topic(2, 40).endswith("{1,39}?)"), D._zh_topic(2, 40)
    assert D._zh_topic(1, 40).endswith("{0,39}?)"), D._zh_topic(1, 40)
    for _loc, _kind, pat in D.DIRECTIVE_PATTERNS:
        if _loc == "zh":
            assert D._zh_topic(1, 40) not in pat.pattern, pat.pattern[:80]


def test_the_objectless_guard_counts_characters_not_complements():
    """⚠️ 判据必须是「宾语只有一个字」，不能退回枚举补语字——那是开集。"""  # noqa: DOCSTRING_CJK
    assert not hasattr(D, "_ZH_COMPLEMENT_CHARS")
    # 前视里不许出现「某几个补语字的择一」这种形状
    assert "到|起|及" not in D._ZH_OBJECTLESS_AHEAD
    assert D._ZH_PLAIN_CHAR in D._ZH_OBJECTLESS_AHEAD


def test_the_objectless_guard_uses_only_the_parent_particle_set():
    """⚠️ 认上本 PR 新加的台湾口语助词，``别再提好咧。`` 会被判成「好 + 咧」整条毙掉，
    而 parent 存的是 ``好咧``。那批字同时也是常见词尾字。
    """  # noqa: DOCSTRING_CJK
    assert D._ZH_BASE_FINAL_PARTICLES in D._ZH_OBJECTLESS_AHEAD
    for tw_only in TAIWANESE_FINAL_PARTICLES:
        assert tw_only not in D._ZH_OBJECTLESS_AHEAD, tw_only
        assert tw_only in D._ZH_FINAL_PARTICLES, tw_only
    # 行为面：两个方向各钉一条
    assert _zh_terms("别再提好咧。") == {"好咧"}
    assert _zh_terms("别再提工作喔。") == {"工作"}


def test_the_verb_alternation_is_atomic():
    assert D._ZH_VERBS_WITH_ADDRESS.startswith("(?>")
    assert D._ZH_VERBS_PLAIN.startswith("(?>")


# ── 5e. 的 + 指示词 的自然说法 ───────────────────────────────


# ⚠️ 模板 2 的三个可选填充组（的事 / 的+指示词 / 就）加上 lazy 前缀，会让正则优先
# 把**话题的最后一个字**塞进填充组。三种破法各配一条（对抗排查）：
@pytest.mark.parametrize(
    ("text", "expected", "why"),
    [
        # (a) 就 切进词里 —— 模板 2 一个字都不吃，交给 _drop_filler_suffixed_terms。
        # ⚠️ 以 ``就`` 结尾的词是**开集**（成就 / 迁就 / 功成名就 / 一蹴而就 /
        # 練就 / 鑄就 …），用左界字符黑名单挡漏一个就腰斩一个真实话题（codex P2）。
        ("他的成就别提了。", "他的成就", "就"),
        ("他的成就別提了。", "他的成就", "就"),
        ("成就别提了。", "成就", "就"),
        ("迁就别提了。", "迁就", "就"),
        ("将就别提了。", "将就", "就"),
        ("功成名就别提了。", "功成名就", "就"),
        ("功成名就別提了。", "功成名就", "就"),
        ("一蹴而就别提了。", "一蹴而就", "就"),
        ("努力练就别提了。", "努力练就", "就"),
        # (b) 的 单独可选会切 目的 / 标的 —— 靠把 的 绑进指示词分支挡
        ("目的这个别提了。", "目的", "的"),
        ("目的這個別提了。", "目的", "的"),
        ("标的这个别提了。", "标的", "的"),
        # ⚠️ 三字以上的话题才压得住这一维：话题只有两字时被削掉的那半撞上 2 字下限
        # 被丢弃，正则会自己改选更长的前缀，看起来"没坏"。
        ("有目的别提了。", "有目的", "的"),
        ("有目的別提了。", "有目的", "的"),
        ("这个标的别提了。", "这个标的", "的"),
        # (c) 单字主语被填充组削到 1 字、撞长度下限 —— 靠前缀下限 2 挡
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
    """``的事`` 与 ``這件事`` 各自可选还不够：``減肥的這件事`` 这种自然说法里
    ``的`` 和指示词是分开的，不放行就会留一个悬空的 ``的`` 在 term 里。"""  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == {expected}


# ── 5f. 书名 / 片名里含 关于 不能被当成构造前缀 ──────────────
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
    """挡 ``关于`` 构造只能挡**开头**：书名里带 关于 是正常的，挡整段会把整条
    指令打没（codex P2）。"""  # noqa: DOCSTRING_CJK
    terms = _zh_terms(text)
    assert terms, f"{text!r} 一条 term 都没抽到"
    assert any("关于" in t or "關於" in t for t in terms), (
        f"{text!r} 只剩书名内层：{terms}"
    )


# ── 5c. 关于 X 只产出一条 term ───────────────────────────────


# ── 5c-2. 填充词后置去重 ─────────────────────────────────────
def test_filler_dedup_needs_the_shorter_term_to_actually_exist():
    """去重比对的是**同一句话里实际抽出来的 term**，不猜词边界。

    ``股票就`` 被丢是因为 ``股票`` 也在结果里（模板 4 抽的）；``功成名就`` 留下是
    因为 ``功成名`` 从来不是一条 term。把这条判据换成"猜哪个字是填充词"就会两边
    都错——这正是 _ZH_JIU 那版黑名单的下场。
    """  # noqa: DOCSTRING_CJK
    overlapping = [(0, 10), (0, 10)]
    assert D._drop_filler_suffixed_terms([
        ("zh", "ban_topic", "股票就"), ("zh", "ban_topic", "股票"),
    ], overlapping) == [("zh", "ban_topic", "股票")]
    assert D._drop_filler_suffixed_terms([
        ("zh", "ban_topic", "功成名就"),
    ], [(0, 10)]) == [("zh", "ban_topic", "功成名就")]
    # 填充词会叠：前女友 + 的事 + 就
    assert D._drop_filler_suffixed_terms([
        ("zh", "ban_topic", "前女友的事就"), ("zh", "ban_topic", "前女友"),
    ], overlapping) == [("zh", "ban_topic", "前女友")]
    # kind 不同不互相影响
    assert len(D._drop_filler_suffixed_terms([
        ("zh", "ban_topic", "股票就"), ("zh", "other_kind", "股票"),
    ], overlapping)) == 2


def test_filler_dedup_runs_before_term_deduplication():
    """⚠️ 去重必须在填充词过滤**之后**：过滤器靠命中区间认「同一条指令的两种切法」，
    而去重会把重复 term 连同它的区间一起扔掉。

    "股票别提了。关于股票就别提了。" 里第二条指令的 ``股票`` 和第一条同名，先去重
    的话它的区间就没了，过滤器只看得到第一条那个**不重叠**的区间，``股票就``
    逃过一劫（codex P2）。
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms("股票别提了。关于股票就别提了。") == {"股票"}
    assert _zh_terms("工作别提了。关于工作就别提了。") == {"工作"}
    # 反向：不同话题不能因为这个顺序被吞掉
    assert _zh_terms("股票别提了。关于加班就别提了。") == {"股票", "加班"}


def test_filler_dedup_stays_linear_on_many_directives():
    """填充词去重的重叠比对本身是 O(n²)。绝大多数消息里一条带填充词的 term 都没有，
    所以先筛一遍再比——粘贴一大段聊天记录时 n 可以到几百（codex P2）。

    ⚠️ 这条是纯性能改动，行为上没有差异。用时限钉不可靠（常数太小、CI 上还抖），
    改成**确定性**判据：一条带填充词的 term 都没有时，函数应当**原样返回同一个
    列表对象**——那正是"没有进入逐对比对"的证据。
    """  # noqa: DOCSTRING_CJK
    # ⚠️ 这里**不**钉「逐条跳过 suspects」那一层。装上桶索引之后它只是个常数因子
    #（800 条 + 1 条填充词：24.6ms → 74.4ms，量级没变），而钉它只能靠断言源码文本，
    # 换个等价写法就误红。量级那一维由下面的时限断言负责（CodeRabbit）。
    #
    # ⚠️ 光筛 suspects 不够：话题本身就以填充词结尾时（`成就别提了。` 重复几千遍）
    # 每条都是 suspect，逐条再扫全表又变回 O(n²)——4000 条曾要 1.25 秒。按起点分桶
    # 之后只看邻桶（命中区间长度有上界），这里用时限钉住那个量级差（codex P2）。
    import time

    started = time.perf_counter()
    extract_directives("成就别提了。" * 4000)
    elapsed = time.perf_counter() - started
    assert elapsed < 1.0, f"4000 条以填充词结尾的话题跑了 {elapsed:.2f}s，桶索引失效了"

    hits = [("zh", "ban_topic", f"话题{i}") for i in range(50)]
    spans = [(i * 10, i * 10 + 5) for i in range(50)]
    assert not any(
        t.endswith(f) for _l, _k, t in hits for f in D._ZH_TRAILING_FILLERS
    ), "前提：这批 term 里不该有带填充词的"
    assert D._drop_filler_suffixed_terms(hits, spans) is hits, (
        "没有走早退：即使一条填充词后缀都没有，也做了 O(n²) 的重叠比对"
    )
    # 有填充词时照常工作
    assert D._drop_filler_suffixed_terms(
        [("zh", "ban_topic", "股票就"), ("zh", "ban_topic", "股票")], [(0, 9), (0, 9)],
    ) == [("zh", "ban_topic", "股票")]


def test_filler_dedup_only_touches_overlapping_matches():
    """⚠️ 同一条指令的两种切法才算重复。命中区间不重叠 = 两条独立指令，哪怕正好差
    一个填充词也不能丢——"功成名就别提了，功成名别提了。" 是两条（codex P2）。"""  # noqa: DOCSTRING_CJK
    disjoint = [(0, 7), (8, 15)]
    assert len(D._drop_filler_suffixed_terms([
        ("zh", "ban_topic", "功成名就"), ("zh", "ban_topic", "功成名"),
    ], disjoint)) == 2
    # spans 缺失 / 长度对不上时不做抑制——安全方向
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
        # ⚠️ `的事` 归话题所有（见 test_the_guanyu_template_keeps_deshi_in_the_topic）
        ("關於前女友的事就別提了", "前女友的事"),
        ("關於減肥的這件事就別說了。", "減肥"),
    ],
)
def test_guanyu_produces_exactly_one_term(text, expected):
    """通用的 ``X + 别提`` 模板会把 "关于股票就" 整段当话题，和专用模板的 "股票"
    一起存下来。垃圾那条同样占一个 active 名额、注入三天。"""  # noqa: DOCSTRING_CJK
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
    """⚠️ 这条原来断言的是 ``== {expected}``（多出来的 ``我觉得关于股票就`` 必须被
    压掉），现在放宽成「真话题一定在」。

    两条 codex 要求**直接打架**：早先要求 temper 掉话题里的 ``关于``（免得多产出
    ``我觉得关于股票就``），后来又指出这会让 ``电影关于爱别提了。`` 整条 0 命中、
    ``这部关于爱的电影`` 被腰斩（parent 两条都完整）。守卫收窄成只 temper**第一个**
    单位之后，多出来的那条长 span 回到了 parent 的行为。
    判据是代价量级：多一条含着真话题的长 term（G 类，和 parent 一样）比丢掉一条真
    指令（B 类）轻得多。
    """  # noqa: DOCSTRING_CJK
    assert expected in _zh_terms(text), _zh_terms(text)


@pytest.mark.parametrize(
    ("text", "expected"),
    [("有关工作别提了", "有关工作"), ("有關工作別提了", "有關工作")],
)
def test_guanyu_exclusion_does_not_eat_other_words_starting_with_guan(text, expected):
    """排除的是 ``关|于`` 这一个切点，不是"以 关/關 开头的一切"。"""  # noqa: DOCSTRING_CJK
    assert expected in _zh_terms(text)


# ── 6. 普通繁体聊天不误触发 ──────────────────────────────────
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


# ── 7. 话题首字是 到/起/及 时不被当成动词补语吃掉 ────────────
# ⚠️ 全部**对照 origin/main 实测**过：base 这五条都保留了首字，是本 PR 一度吃掉的
# （codex P2）。``提／到达时间`` 与 ``提到／达时间`` 是同一串字，局部无从分辨，所以
# 不做「言说动词 × 结果补语」的笛卡尔积——留一个 ``到`` 话题仍完整，吃一个字变非词。
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
    """把 ``(?>a|b|c)`` 拆成 {a, b, c}。

    ⚠️ 不能用 ``f"|{x}|"`` 去搜：第一个分支前面是 ``(?>`` 不是 ``|``，最后一个分支
    后面是 ``)`` 不是 ``|``，首尾两位永远搜不到——而「有人把 提到 加到表头或表尾」
    正是这条护栏要防的事（CodeRabbit）。
    """  # noqa: DOCSTRING_CJK
    body = alternation.removeprefix("(?>").removesuffix(")")
    return set(body.split("|"))


def test_the_verb_branch_splitter_sees_both_ends():
    """前提守卫：切分器本身要能看到首尾两个分支，否则上面那条护栏是空的。"""  # noqa: DOCSTRING_CJK
    branches = _verb_branches("(?>头|中|尾)")
    assert branches == {"头", "中", "尾"}


@pytest.mark.parametrize(
    "alternation_name", ["_ZH_VERBS_PLAIN", "_ZH_VERBS_WITH_ADDRESS"],
)
def test_the_verb_table_has_no_complement_cartesian_product(alternation_name):
    """补语族一旦回到动词表，上面那批话题就会被削掉首字。"""  # noqa: DOCSTRING_CJK
    branches = _verb_branches(getattr(D, alternation_name))
    # 前提：切出来的分支里确实有已知的动词，否则下面的 not in 是空断言
    assert "提" in branches and "討論" in branches, branches
    for verb in D._ZH_SAY_VERBS:
        for complement in ("到", "起", "及", "完", "上", "光", "死", "够"):
            assert f"{verb}{complement}" not in branches, f"{verb}{complement}"


# ── 8. 反问尾巴只在没有括号时才剥 ────────────────────────────
# 剥配对括号发生在剥语气词之前，所以「原 term 带不带括号」必须在剥之前判断。
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # 不带括号：反问语气，剥掉（base 会留着整串，这是本 PR 的改进）
        ("别再提工作好吗。", "工作"),
        ("別再提工作好嗎。", "工作"),
        ("别再提工作了好吗。", "工作"),
        ("别再提工作好不好。", "工作"),
        ("別再提工作好不好。", "工作"),
        ("别再提加班可以吗。", "加班"),
        ("別再提加班可以嗎。", "加班"),
        # 剥完不够两个字就整条留着（trim 从不把 term 削到下限以下）
        ("别提行吗。", "行吗"),
        # 带括号：括号里是被引用的专名，反问短语是名字的一部分
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
    """两张表漂移过四次（#2655），这里钉死派生关系。"""  # noqa: DOCSTRING_CJK
    assert D._ZH_CLOSE_FOR_OPEN == {
        lo: hi for lo, hi in D._ZH_BRACKET_PAIRS if lo != hi
    }
    assert D._ZH_SYMMETRIC_DELIMS == frozenset(
        lo for lo, hi in D._ZH_BRACKET_PAIRS if lo == hi
    )
    # 对称的那一对不算「未闭合的开括号」——落单的双引号是英寸号，不是没写完的引文
    assert '"' not in D._ZH_CLOSE_FOR_OPEN
    assert "《" in D._ZH_CLOSE_FOR_OPEN


def test_interrogative_tails_are_a_separate_table_from_the_particles():
    """混进助词表就没法按括号分别对待了。"""  # noqa: DOCSTRING_CJK
    assert D._TAIL_INTERROGATIVES_BY_LOCALE["zh"] == (
        "好吗", "好嗎", "好不好", "可以吗", "可以嗎", "行吗", "行嗎", "吗", "嗎",
    )
    # zh 那张助词表必须只剩单字，否则多字反问短语会绕过括号判据被无条件剥掉。
    for tok in D._TRIM_TRAIL_TOKENS_BY_LOCALE["zh"]:
        assert len(tok) == 1, tok




# ── 9. 括号段有界 + 对称引号不跨句 ──────────────────────────
@pytest.mark.parametrize("pair", D._ZH_BRACKET_PAIRS)
def test_bracket_bodies_are_bounded(pair):
    """无界的 ``*`` 在每个开括号处都会扫到串尾，是二次方（codex P2）。

    ⚠️ 判据是**全称**不是存在：12 对括号里只要有一对还有界，`in` 那种写法就通过，
    而只把 ``「」`` 放成无界就足以让 ``"「" * 8000`` 从 0.04s 涨到 2.5s（变异跑出来的）。
    """  # noqa: DOCSTRING_CJK
    lo, hi = pair
    body = D._zh_bracket_body(lo, hi)
    assert f"{{0,{D._TERM_MAX_LEN}}}" in body, body
    for unbounded in ("*", "+", "{0,}"):
        assert f"){unbounded}" not in body, (body, unbounded)


@pytest.mark.parametrize("pair", D._ZH_BRACKET_PAIRS)
def test_no_bracket_pair_scans_to_the_end(pair):
    """行为面：每一对括号单独喂 8000 个未配对开括号都必须是线性的。"""  # noqa: DOCSTRING_CJK
    import time

    lo, _hi = pair
    started = time.perf_counter()
    extract_directives(lo * 8000)
    elapsed = time.perf_counter() - started
    assert elapsed < 1.0, f"{lo!r} * 8000 跑了 {elapsed:.2f}s"


def test_unmatched_openers_stay_linear():
    import time

    timings = {}
    for n in (2000, 8000):
        text = "《" * n
        start = time.perf_counter()
        extract_directives(text)
        timings[n] = time.perf_counter() - start
    # 二次方的话 4 倍输入是 16 倍时间；给足余量只要求**远小于**二次方。
    assert timings[8000] < timings[2000] * 8 + 0.2, timings
    assert timings[8000] < 1.0, timings


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # 两句各带一个孤立英寸号，不能被并成一段引文
        ('尺寸5"别提了。尺寸6"别提了。', {"尺寸5", "尺寸6"}),
        ('尺寸5"別提了。尺寸6"別提了。', {"尺寸5", "尺寸6"}),
        # 真的成对引号仍然整体放行，逗号也还在
        ('别提"你好，李焕英"了。', {"你好，李焕英"}),
        ('別提"你好，李煥英"了。', {"你好，李煥英"}),
        ('"Everything, Everywhere"别提了。', {"Everything, Everywhere"}),
    ],
)
def test_symmetric_ascii_quotes_do_not_span_sentences(text, expected):
    assert _zh_terms(text) == expected


def test_only_symmetric_pairs_forbid_sentence_punctuation():
    """非对称括号里的句读属于话题（``《你好，李焕英》``），不能一起收紧。"""  # noqa: DOCSTRING_CJK
    assert _zh_terms("电影《你好，李焕英》别提了。") == {"电影《你好，李焕英》"}
    assert _zh_terms("電影《你好，李煥英》別提了。") == {"電影《你好，李煥英》"}


# ── 10. 关于 的排除只属于前置话题 ────────────────────────────
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
    """⚠️ 排除 ``关于`` 是模板 2 **前置话题**的守卫，放进共用单字分支会把动宾结构的
    宾语一起毙掉（codex P2）。前置话题与动词后宾语是两种结构。
    """  # noqa: DOCSTRING_CJK
    assert expected in _zh_terms(text), _zh_terms(text)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # 话题**内部**含 关于 的真话题必须完整——守卫只管起点
        ("这部关于爱的电影别提了。", "这部关于爱的电影"),
        ("电影关于爱别提了。", "电影关于爱"),
        ("這部關於愛的電影別提了。", "這部關於愛的電影"),
    ],
)
def test_a_topic_may_contain_guanyu_in_the_middle(text, expected):
    """⚠️ ``关于`` 守卫只 temper **第一个**单位。

    套在每个单位上的话，话题内部含 ``关于`` 的真话题会被腰斩甚至整条丢掉
    （codex P2）。这道守卫要防的是「前缀逐字吃过句首的 ``关于``」，那只可能
    发生在**起点**。
    """  # noqa: DOCSTRING_CJK
    assert expected in _zh_terms(text), _zh_terms(text)


def test_the_guanyu_temper_applies_only_to_the_first_unit():
    """结构面：temper 只能出现一次，不能套在重复单位上。"""  # noqa: DOCSTRING_CJK
    # ⚠️ 判据是「temper 过的那个**单位**只出现一次」，不是数字面串 ``(?!关于|關於)``。
    # 那个前缀住在 _ZH_TOPIC_CHAR_NO_GUANYU 里，实现侧换个等价写法（比如调两个分支
    # 的顺序）这条就会假红，而它要防的事和字面写法无关（coderabbit）。
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


# ── 11. 否定词全族都算中文证据 ───────────────────────────────
# ⚠️ 笛卡尔积从 _ZH_NEG 派生的那批否定词，防的是「加了新否定词但忘了同步证据」。
NEGATIONS_FOR_EVIDENCE = ("别", "別", "不要", "不许", "不許", "不准", "莫", "甭", "休")


@pytest.mark.parametrize("negation", NEGATIONS_FOR_EVIDENCE)
def test_every_negation_counts_as_chinese_evidence(negation):
    """⚠️ 单字类覆盖不到 不准/莫/休/不要——它们一个字都不在里面，于是含日文语法标记
    的标题被当成日文句子整条丢掉（codex P2）。补的是**结构**不是共用汉字：往字类里
    塞 准/莫/休 会像 没/称 那样把守卫整个短路掉。

    ⚠️ 和日文共用码位的那两个（``别 別``）用带 ``再`` 的形态：串首、又没有 ``再``
    的 ``別提X`` 在日文里是"另一份 X"（別提案），刻意交给日文守卫，见
    test_a_clause_initial_bie_compound_stays_behind_the_japanese_guard。
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
    """⚠️ ``別`` 在日文是后缀「按…分」，``地域別講座`` 会满足「否定 + 言说动词」。
    左界是开集（地域/年代/男女…都行），右界是闭集——日文里 ``別`` 之后成词的只有
    ``講``。宁可漏判繁体用户的一次 ban，也不能把日文句子残片存进指令表。
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


# ── 12. 以 论/論 开头的话题不被 谈论 吃掉 ────────────────────
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
    """⚠️ ``讨`` 不能单用（"别讨政治"不成话）所以 ``讨论`` 必须整体进表；``谈`` 能，
    所以 ``谈论`` 不进——进了就把以 ``论`` 开头的话题削掉首字（codex P2，与结果补语
    同一族）。这条是判据本身，加复合动词前先过一遍。
    """  # noqa: DOCSTRING_CJK
    assert D._ZH_SAY_COMPOUNDS == ("讨论", "討論")
    for compound in D._ZH_SAY_COMPOUNDS:
        assert compound[0] not in D._ZH_SAY_VERBS, compound


@pytest.mark.parametrize(
    ("text", "expected"),
    [("别讨论文格式。", "文格式"), ("別討論文格式。", "文格式")],
)
def test_the_unavoidable_taolun_overlap_is_symmetric(text, expected):
    """``讨论`` 的同类重叠没法避免（base 也这样），但繁简两侧必须一致。"""  # noqa: DOCSTRING_CJK
    assert expected in _zh_terms(text)


# ── 13. 标识符内部的 ASCII 点号/逗号不是边界 ─────────────────
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Python 3.11别提了。", "Python 3.11"),
        ("Python 3.11別提了。", "Python 3.11"),
        ("example.com别提了。", "example.com"),
        ("example.com別提了。", "example.com"),
        ("价格1,000元别提了。", "价格1,000元"),
        ("價格1,000元別提了。", "價格1,000元"),
        # ⚠️ 两侧判据必须是 Unicode 感知的：写成 [0-9A-Za-z] 只修了 ASCII 一半，
        # 下面这批全部还是断的（codex P2 第二轮）
        ("café.com别提了。", "café.com"),
        ("naïve.org别提了。", "naïve.org"),
        ("Дом.ру别提了。", "Дом.ру"),
        ("例子.测试别提了。", "例子.测试"),
        ("例子.測試別提了。", "例子.測試"),
        ("价格１,０００元别提了。", "价格１,０００元"),
        ("價格１,０００元別提了。", "價格１,０００元"),
        # ⚠️ 全角逗号也要收：中文排版的数字就是这个写法
        ("价格1，000元别提了。", "价格1，000元"),
        ("價格1，000元別提了。", "價格1，000元"),
        # ⚠️ 组合符号：Python 的 \w 不含 Mn/Mc 类，NFD 分解形和天城文照样被截断
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
    """判据是「左右都得是字母或数字」——句尾点号后面是空白或串尾，不满足。"""  # noqa: DOCSTRING_CJK
    for term in _zh_terms(text):
        assert "。" not in term and "，" not in term, term


def test_the_identifier_punct_rule_needs_both_sides():
    """⚠️ 两侧都要，而且判据必须 Unicode 感知——写死 ASCII 只修一半（codex P2 两轮）。"""  # noqa: DOCSTRING_CJK
    # ⚠️ 判据必须是**否定式**（不是空白、不是句读），不是「列出哪些字算词字符」——
    # 列举法修了三轮还在漏（ASCII → \w → 组合符号）。
    assert "0-9A-Za-z" not in D._ZH_IDENT_PUNCT
    assert r"\w" not in D._ZH_IDENT_PUNCT
    assert D._ZH_IDENT_PUNCT.startswith(r"(?<=[^\s")
    assert D._ZH_IDENT_PUNCT.endswith(r"])")
    for unit in (D._ZH_TOPIC_CHAR, D._ZH_TOPIC_CHAR_NO_GUANYU):
        assert D._ZH_IDENT_PUNCT in unit
    # 前提守卫：这条判据在非 ASCII 上真的命中
    import re as _re

    probe = _re.compile(D._ZH_IDENT_PUNCT)
    for ident in (
        "café.com", "Дом.ру", "例子.测试", "１,０００",
        unicodedata.normalize("NFD", "café.com"), "देवनागरी.com",
    ):
        assert probe.search(ident), ident
    # 而句尾的点号（后面是空白或串尾）不命中
    for tail in ("工作. ", "工作."):
        assert not probe.search(tail), tail


# ── 14. 剥填充词之后要归一化括号再跟对手比 ───────────────────
@pytest.mark.parametrize(
    "text",
    ["关于《你好，李焕英》就别提了。", "關於《你好，李煥英》就別提了。",
     "关于「我的事」就别提了。", "关于工作就别提了。"],
)
def test_a_filler_stripped_form_is_normalized_before_comparing(text):
    """⚠️ 填充词前面常常正好是一个收尾括号：剥掉 ``就`` 得到 ``你好，李焕英》``，
    多一个 ``》`` 就跟专用切法的干净 term 对不上，畸形的那条照样存三天（codex P2）。
    """  # noqa: DOCSTRING_CJK
    terms = _zh_terms(text)
    assert len(terms) == 1, terms
    for term in terms:
        assert not term.endswith("就"), term
        assert not any(term.endswith(hi) for _lo, hi in D._ZH_BRACKET_PAIRS), term


def test_normalizing_does_not_merge_two_separate_directives():
    """⚠️ 归一化只在**重叠**的命中之间比，两条独立指令差一个 ``就`` 不能被吞掉。"""  # noqa: DOCSTRING_CJK
    assert _zh_terms("功成名就别提了，功成名别提了。") == {"功成名就", "功成名"}
    assert _zh_terms("他的成就别提了。") == {"他的成就"}


# ── 15. 主语打头的指令仍算中文证据 ───────────────────────────
# ⚠️ 日文的 ``〜別`` 后缀问题只存在于**单字**否定词；多字的 不要/不许/不許/不准
# 不可能是日文名词后缀，给它们套左界纯属误伤。
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
    """⚠️ 只收日文里根本没有的汉字。``我 / 他 / 請`` 刻意不收——它们是日文汉字，
    收了 ``他別提案をお願いします。`` 这类句子就会被放行进来（实测过）。

    ⚠️ 主断言是**相等**：白名单是开放可加的，而「不许出现的日文汉字」是开集，
    手抄一份黑名单挡不住没写进去的那些（``俺`` 是北方口语主语、同时是日文常用汉字，
    加进来 ``俺別提案をお願いします。`` 就会被当成中文指令存下来——变异跑出来的）。
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


# ── 16. 对称引号不吞掉一整条逗号分隔的指令 ───────────────────
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('尺寸5"别提了，尺寸6"别提了。', {"尺寸5", "尺寸6"}),
        ('尺寸5"別提了，尺寸6"別提了。', {"尺寸5", "尺寸6"}),
        # 逗号本身仍然放行——真作品名带逗号的不少
        ('"Everything, Everywhere"别提了。', {"Everything, Everywhere"}),
        ('别提"你好，李焕英"了。', {"你好，李焕英"}),
        # 带否定词但不带标点的引文走单字分支，照样完整
        ('别提"再别康桥"了。', {"再别康桥"}),
        ('别提"我不是药神"了。', {"我不是药神"}),
    ],
)
def test_a_symmetric_quote_run_cannot_swallow_a_whole_directive(text, expected):
    assert _zh_terms(text) == expected


def test_only_symmetric_pairs_temper_the_negation():
    """非对称括号不会被误当收尾，不需要 temper——``电影(Hello, World)`` 要保住。"""  # noqa: DOCSTRING_CJK
    # ⚠️ 判据变了：temper 现在**每一对**都上。对称引号是最早的那一格（孤立的 ``"``
    # 很常见），ASCII 非对称那几对是后来量出来的同一族（``别再提价格<预算.别再提
    # 收入>目标.`` 被并成一条，codex P2）。全角非对称不会被误当收尾，但多一道
    # 零宽前视不改变它们的行为，统一上比留个例外更不容易漂。
    temper = f"(?!{D._ZH_DIRECTIVE_AHEAD})"
    assert D._ZH_BRACKET_RUN.count(temper) >= len(D._ZH_BRACKET_PAIRS)
    assert _zh_terms("电影(Hello, World)别提了。") == {"电影(Hello, World)"}


# ── 17. 反问尾巴落在引号之外时该剥 ───────────────────────────
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # 尾巴在收尾括号**之后** = 句子级语气，剥
        ("別再提電影《你好》好嗎。", "電影《你好》"),
        ("别再提电影《你好》好吗。", "电影《你好》"),
        ("别再提《你的名字》好吗。", "你的名字"),
        ("別再提《你的名字》好嗎。", "你的名字"),
        ("别再提《你的名字》好不好。", "你的名字"),
        ('别再提"你的名字"好吗。', "你的名字"),
        # 尾巴在括号**里面** = 名字的一部分，不剥
        ("别再提《最近你好吗》。", "最近你好吗"),
        ("別再提《最近你好嗎》。", "最近你好嗎"),
        ("别再提电影《我们好不好》。", "电影《我们好不好》"),
        ("别再提《你可以吗》。", "你可以吗"),
        # ⚠️ 前缀以**开括号**结束 = 尾巴仍在书名里，不能剥（判据必须是收尾括号，
        # 换成「任意括号字符」这三条就会被削成 电影 / 剧集，标题整个丢掉）
        ("别再提电影《好不好》。", "电影《好不好》"),
        ("別再提電影《好不好》。", "電影《好不好》"),
        ("别再提剧集《可以吗》。", "剧集《可以吗》"),
        # ⚠️ 引号和尾巴之间隔着普通修饰词也一样该剥——代理判据「前缀正好以收尾
        # 括号结尾」在这三行会判错（codex P2）
        ("我不想再聊電影《你好》續集好嗎。", "電影《你好》續集"),
        ("我不想再聊电影《你好》续集好吗。", "电影《你好》续集"),
        ("别再提電影《你好》續集好嗎。", "電影《你好》續集"),
        # ⚠️ 叠加的尾巴：剥掉外层之后，内层的下标必须把已剥的字数算进去，
        # 否则 ``好吗`` 会被当成引号外的（变异跑出来的）
        ("别再提《最近你好吗》好不好。", "最近你好吗"),
        ("別再提《最近你好嗎》好不好。", "最近你好嗎"),
        ("别再提「最近你好吗」好不好。", "最近你好吗"),
        # ⚠️ 多段括号要看**最后**一段的收尾，不是第一段（变异跑出来的）
        ("别再提《甲》《乙》好吗。", "甲》《乙"),
        ("别再提《甲》和《乙》好吗。", "甲》和《乙"),
        ("别再提电影《甲》《乙》好吗。", "电影《甲》《乙》"),
        # 一个括号都没有 = 无条件可剥
        ("别再提工作好吗。", "工作"),
        ("別再提工作好嗎。", "工作"),
    ],
)
def test_an_interrogative_outside_the_quotes_is_still_a_tail(text, expected):
    """⚠️ 判据不能只看「原 term 有没有括号」：剥配对括号发生在剥语气词之前，等轮到
    语气词时括号已经没了。要看的是**剥完之后前缀是不是以收尾括号结束**（codex P2，
    与「不要腰斩《最近你好嗎》」那条方向相反，两条得同时成立）。
    """  # noqa: DOCSTRING_CJK
    assert expected in _zh_terms(text), _zh_terms(text)


def test_the_interrogative_gate_uses_closing_delimiters():
    closers = {hi for _lo, hi in D._ZH_BRACKET_PAIRS}
    # 对称的一对里开合同字，所以收尾集必然是括号字符集的真子集或相等
    assert closers <= frozenset(ch for pair in D._ZH_BRACKET_PAIRS for ch in pair)
    assert "》" in closers and "《" not in closers


# ── 18. 引号里的单字语气词也是名字的一部分 ───────────────────
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("别再提《想见你喔》。", "想见你喔"),
        ("別再提《想見你喔》。", "想見你喔"),
        ("别再提《就是爱唷》。", "就是爱唷"),
        ("別再提《就是愛唷》。", "就是愛唷"),
        ("别再提《你好啦》。", "你好啦"),
        # 引号外的同一个字仍然是语气词，照剥
        ("别再提工作喔。", "工作"),
        ("別再提工作喔。", "工作"),
        ("别再提工作啦。", "工作"),
    ],
)
def test_single_char_particles_inside_a_quoted_name_survive(text, expected):
    """⚠️ 引号判据对**所有** CJK 尾词生效，不只多字反问短语（codex P2）。"""  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == {expected}, _zh_terms(text)


# ── 19. 谚文助词对每个 locale 都适用 ─────────────────────────
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
    """⚠️ 分表是为了拆开 CJK 之间的同码位歧义（``唄`` 中文语气词 / 日文"歌"），而
    谚文与汉字、假名都不共码位，不可能撞——所以每个 locale 都该带上它（codex P2）。
    """  # noqa: DOCSTRING_CJK
    assert expected in _zh_terms(text), _zh_terms(text)


def test_the_hangul_table_is_not_script_ambiguous():
    """判据是「谚文与 CJK 不共码位」，这条钉住它，别哪天往 ko 表里塞汉字。"""  # noqa: DOCSTRING_CJK
    for tok in D._TRIM_TRAIL_TOKENS_BY_LOCALE["ko"]:
        for ch in tok:
            assert "\uac00" <= ch <= "\ud7a3" or "\u1100" <= ch <= "\u11ff", tok


# ── 20. 日文守卫只拿一个字符，不复制整段前缀 ─────────────────
def test_the_japanese_guard_gets_one_character_not_the_whole_prefix(monkeypatch):
    """⚠️ 用**实参长度**断言，不用计时：计时的阈值只能拿变异后自己的基线来算，而
    复制整段前缀在两个规模上同时变慢，比值反而看不出来（变异跑出来的）。守卫只读
    ``before[-1:]``，切整段等于每条命中复制一次全文——几万条指令时是二次方。
    """  # noqa: DOCSTRING_CJK
    seen = []
    real = D._is_japanese_sentence_match

    def spy(span, term, before="", **kwargs):
        seen.append(len(before))
        return real(span, term, before, **kwargs)

    monkeypatch.setattr(D, "_is_japanese_sentence_match", spy)
    extract_directives("工作别提了。" * 50 + "别提加班。")
    assert seen, "日文守卫根本没被调用，这条测试是空的"
    # ⚠️ 上限是**常量**不是字面量 1：主语跨空格那条需要几个字符的左文
    # （_ZH_SUBJECT_ACROSS_SPACE），但仍然必须是 O(1)，不能是整段前缀。
    assert max(seen) <= D._ZH_SUBJECT_LEFT_MAX, max(seen)
    assert D._ZH_SUBJECT_LEFT_MAX <= 8


def test_the_quoted_span_end_marks_where_the_quotes_stop():
    """⚠️ 判据是位置不是形状：``電影《你好》續集好嗎`` 的收尾括号在中间，代理判据
    「前缀正好以收尾括号结尾」在这里判错（codex P2）。
    """  # noqa: DOCSTRING_CJK
    end = D._zh_quoted_span_end
    # 没有括号 → 任何尾巴都在引号外
    assert end("工作好吗") == 0
    # 完整一段括号 → 越过它的部分在引号外，中间隔多少修饰词都一样
    assert end("《最近你好嗎》") == 7
    assert end("電影《你好》續集好嗎") == 6
    assert end('"你的名字"好吗') == 6
    # 只有收尾括号（开括号已被前一步剥掉）→ 后面的在引号外
    assert end("你的名字》好吗") == 0
    # 没闭合的开括号 → 一直延伸到末尾，里面的一切都算引号内
    assert end("电影《我们好不好") == 8
    assert end("电影《好不好") == 6
    # 多段括号取**最后**一段的收尾
    assert end("《甲》《乙》好吗") == 6
    assert end("《甲》《乙好吗》") == 8


def test_the_quoted_span_end_is_derived_from_the_bracket_run():
    """别另开一张括号表——同一件事维护两份必然漂移（#2655）。"""  # noqa: DOCSTRING_CJK
    assert D._ZH_CLOSE_FOR_OPEN == {
        lo: hi for lo, hi in D._ZH_BRACKET_PAIRS if lo != hi
    }


# ── 21. 每条模板的每个分支都要真的驱动一次抽取 ───────────────
# ⚠️ 字形对偶抓不到繁简**同形**的分支（``聊`` / ``提及`` / ``不想``）——删掉模板 2 的
# ``|聊``，两侧一起丢指令而所有结构断言全绿（变异跑出来的）。这里改成行为驱动：
# 从模板自己的分支组里读出分支，逐个塞进该模板的句型，必须都能抽到同一个话题。
def _capture_start(raw: str) -> int:
    """这条模板里第一个**捕获**组的起点（跳过 ``(?:`` / ``(?=`` 这类）。"""  # noqa: DOCSTRING_CJK
    for pos, char in enumerate(raw):
        if char != "(" or (pos and raw[pos - 1] == chr(92)):
            continue
        if raw[pos : pos + 2] == "(?":
            continue
        return pos
    raise AssertionError(f"这条模板没有捕获组：{raw!r}")


def _group_containing(raw: str, needle: str) -> list[str]:
    """取出这条模板里**含有 needle** 的那个分支组。

    ⚠️ 先摘掉括号体里那道 temper（``(?!否定词 + 再? + 言说动词)``）：它是**零宽**
    前视，不驱动任何提取，但它自带一份言说动词交替，不摘的话按 needle 找到的是它。
    """  # noqa: DOCSTRING_CJK
    raw = raw.replace(f"(?!{D._ZH_DIRECTIVE_AHEAD})", "")
    for group in _alternation_groups(raw):
        if needle in group:
            return group
    raise AssertionError(f"这条模板里没有含 {needle!r} 的分支组：{raw!r}")


# ⚠️ 分支表**钉死成字面量**，不从 pattern 里读。从 pattern 读的话「删掉一个分支」
# 同时也删掉了驱动它的那条用例，测试跟着缩水、照样全绿——本仓库栽过的 derived-test
# 盲区。钉死之后删分支必然红，加分支必须同时改这里（顺便被迫补一条繁简对照）。
#
# ⚠️ 第一列是**模板序号（1-based，跟注释里说的「模板 2/3/4」对得上）**，不是列表
# 下标——本文件别处一律用 1-based 指代模板，混用会让人误读覆盖范围（CodeRabbit）。
# (模板序号, 分支组的定位分支, 钉死的分支表, 句型, 期望话题)
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
    # ⚠️ 模板 4 的触发词表现在和模板 2 同源（_ZH_PREPOSED_SAY_VERBS）——原先它
    # 写死着，``關於工作就別提起了。`` 走不进来（codex P2）。
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
    # 前提守卫：钉死的表必须和实现里那组分支**相等**，否则驱动的是一张过时的表
    assert tuple(_group_containing(raw, anchor)) == pinned, (
        f"模板 {template_no} 的分支组变了，请同步这里并给新分支补繁简对照"
    )
    for branch in pinned:
        text = skeleton.format(branch=branch)
        assert expected in _zh_terms(text), f"{text!r} -> {_zh_terms(text)}"


def test_negation_constants_are_pinned():
    """⚠️ 闭集用相等断言。往 _ZH_NEG 加否定词而忘了同步日文守卫的证据，中文侧照常
    工作、但「新否定词 + 日文专名」会被整条吞掉，而同结构的旧否定词不会——同一模板内
    的行为不对称。现在三条证据正则都从这两张表派生，加词自动同步；这条断言负责让
    「加词」这个动作停下来过一次 review。
    """  # noqa: DOCSTRING_CJK
    assert D._ZH_NEG_SINGLES == ("别", "別", "莫", "休", "甭")
    assert D._ZH_NEG_MULTIS == ("不要", "不许", "不許", "不准")
    # 三条证据正则必须真的从上面两张表派生
    for neg in D._ZH_NEG_SINGLES:
        assert neg in D._ZH_NEG_VERB_EVIDENCE, neg
        assert neg in D._ZH_SUBJECT_BEFORE_NEG, neg
    for neg in D._ZH_NEG_MULTIS:
        assert neg in D._ZH_MULTI_NEG_EVIDENCE, neg
    # 而 _ZH_NEG 自己也从同一张表拼出来
    for neg in D._ZH_NEG_MULTIS:
        assert neg in D._ZH_NEG, neg


def test_the_branch_drive_cases_cover_every_template_but_the_first():
    """模板 1 的动词/否定词维已有第 2 节的笛卡尔积；2/3/4 靠上面这条。"""  # noqa: DOCSTRING_CJK
    covered = {case[0] for case in BRANCH_DRIVE_CASES}
    assert covered == {2, 3, 4}
    assert len(_zh_pattern_sources()) == 4


# ── 22. 共用常量装在哪几条模板上，就要在哪几条上钉住 ─────────
# ⚠️ 下面这批守卫/分支都**同时装在多条模板**上，而原先只有模板 1（或只有模板 2）
# 有样本。删掉其他模板上的那一份，行为回归而测试全绿——变异逐条跑出来的。
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # (a) 独立成句的书名号话题：_zh_topic 的前导括号分支，四条模板共用，
        #     模板 3（不想/懒得 + 动词 + 宾语）原先一条样本都没有
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
        # (b) `的?+指示词` 填充组同时装在模板 2 和模板 4 上，原先只有模板 2 有样本
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
        # ⚠️ 这条测试原先的方向是**反的**：我给模板 2 加过 `(?:的事)?`，还补了断言
        # 说 `工作的事别提了。` 该得 `工作`。但 base 存的是 `工作的事`，而且那才对——
        # 模板 2 没有 `关于` 那样的锚，`的事` 就是话题本身的一部分。`我们的事别提了。`
        # 被削成 `我们` 意味着让模型回避用户本人而不是那件事（codex P2）。
        ("工作的事别提了。", "工作的事"),
        ("工作的事別提了。", "工作的事"),
        ("前女友的事别提了。", "前女友的事"),
        ("前女友的事別提了。", "前女友的事"),
        ("我们的事别提了。", "我们的事"),
        ("我們的事別提了。", "我們的事"),
        # 后置形态本来就保留，两侧对齐
        ("别提我们的事。", "我们的事"),
        ("別提我們的事。", "我們的事"),
    ],
)
def test_the_preposed_template_keeps_deshi_in_the_topic(text, expected):
    assert _zh_terms(text) == {expected}, _zh_terms(text)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # ⚠️ 模板 4 也**不吃** `的事`——这条一开始写反了。`的事` 是领属加名物化，
        # 可以是名字本身的一部分；更糟的是那个被截短的 term 会让
        # `_drop_filler_suffixed_terms` 把模板 2 抽到的**正确** term 当成
        # 「它 + 一个填充词」删掉，于是只剩截短的那个（codex P2）。
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
        # 指示词那组**保留**：它们无歧义地是填充（话题就是 `减肥`）
        ("关于减肥这话题就别说了。", "减肥"),
        ("關於減肥這話題就別說了。", "減肥"),
        ("关于工作这件事就别提了。", "工作"),
        ("關於工作這件事就別提了。", "工作"),
        # `(?:就)?` 也保留：删掉它这两条会退成带 `就` 的垃圾
        ("关于股票就别提了。", "股票"),
        ("关于工作就别提了。", "工作"),
        ("關於股票就別提了。", "股票"),
    ],
)
def test_the_guanyu_template_still_consumes_unambiguous_fillers(text, expected):
    """⚠️ 判据是**有没有歧义**，不是「是不是填充位」：指示词和句末 `就` 无歧义，
    `的事` 有——三者都装在同一条模板上，但只撤掉有歧义的那个（实测比较过两个方案，
    连 `就` 一起撤会把这三条干净结果变成垃圾）。
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == {expected}, _zh_terms(text)


def test_the_preposed_topic_unit_excludes_newlines_too():
    """⚠️ 模板 2 的前置话题走的是 _ZH_PLAIN_CHAR_NO_GUANYU 这个**另一个**常量，
    原先全文件没有一条断言碰过它的内容——把换行排除丢掉，term 会跨行吞掉下一条指令，
    而所有换行样本都只走模板 1 和模板 3（变异跑出来的）。
    """  # noqa: DOCSTRING_CJK
    assert D._ZH_PLAIN_CHAR_NO_GUANYU.endswith(D._ZH_PLAIN_CHAR)
    assert D._ZH_PLAIN_CHAR_NO_GUANYU == r"(?!关于|關於)" + D._ZH_PLAIN_CHAR
    # 行为面：前置话题不许跨行
    for text in ("工作\n股票别提了。", "工作\r\n股票別提了。"):
        for term in _zh_terms(text):
            assert "\n" not in term and "\r" not in term, (text, term)


# ── 23. 不含汉字的助词表对所有 locale 生效 ───────────────────
def test_script_disjoint_families_are_discovered_not_listed():
    """⚠️ 自动发现，不是手点名单：分表要拆的是**汉字**上的同码位歧义（``唄`` 中文
    语气词 / 日文「歌」，``了`` 是 完了/終了 的构词成分），假名和谚文与汉字不共码位，
    任何 locale 带上它们都不会撞。以后加一张新的纯假名 / 谚文表会自动进这个集合。
    """  # noqa: DOCSTRING_CJK
    assert set(D._SCRIPT_DISJOINT_FAMILIES) == {"ja", "ko"}
    for fam in D._SCRIPT_DISJOINT_FAMILIES:
        for tok in D._TRIM_TRAIL_TOKENS_BY_LOCALE[fam]:
            assert not D._HAN_RE.search(tok), (fam, tok)
    # 反向：zh 那张表含汉字，所以必须留在 locale 门里
    assert "zh" not in D._SCRIPT_DISJOINT_FAMILIES
    assert any(D._HAN_RE.search(t) for t in D._TRIM_TRAIL_TOKENS_BY_LOCALE["zh"])


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # locale 说的是哪条模板命中，不是话题本身什么语言
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
    """⚠️ 守卫必须看**未 trim** 的捕获：假名助词表对所有 locale 生效之后，
    ``だね`` 会在 trim 里被剥掉，等守卫拿到 term 时日文语法标记已经没了，整句反被
    判成中文（补假名回落时踩到的）。
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == set()


# ── 24. 繁体复数主语 ─────────────────────────────────────────
@pytest.mark.parametrize("subject", ["我們", "你們", "咱們", "您們"])
def test_a_plural_traditional_subject_keeps_the_evidence(subject):
    """``們`` 是繁体复数后缀，日文既不用 ``們`` 也不用 ``们``，所以它后面的 ``別``
    不可能是日文的 ``〜別`` 后缀。不收的话繁中用户这句会被整条丢掉，而简体
    ``我们别…`` 因为 ``们`` 在别处有证据而正常（codex P2）。
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(f"{subject}別再提君の名は。") == {"君の名は"}


# ── 25. 剥填充词后要走同一套 trim 再比 ───────────────────────
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
    """⚠️ 对手那条是落库前 trim 过的，中间形态还带着助词——只剥标点的话
    ``전남친은`` 跟 ``전남친`` 对不上，畸形的那条照样存三天（codex P2）。
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == {expected}, _zh_terms(text)


# ── 26. 落单的对称引号不是「没写完的引文」 ───────────────────
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # 英寸号：落单的 " 是普通字，后面的 好吗 仍然是句子级语气
        ('别再提5"屏幕好吗。', '5"屏幕'),
        ('別再提5"螢幕好嗎。', '5"螢幕'),
        ('别再提27"顯示器好不好。', '27"顯示器'),
        # 非对称的开括号落单时确实是没写完的引文，尾巴仍在里面
        ("别再提电影《我们好不好》。", "电影《我们好不好》"),
        ("别再提电影《好不好》。", "电影《好不好》"),
    ],
)
def test_a_standalone_symmetric_quote_is_not_an_unclosed_opener(text, expected):
    """⚠️ _zh_bracket_body 已经决定过一次：落单的 ASCII 双引号是英寸号 / 颜文字
    ``:(`` 这类普通字符，不该当硬边界。未闭合开括号那条判据再把它当引文起点就是
    自相矛盾——同一个字符在同一个模块里两种待遇（codex P2）。
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
    """⚠️ 放宽到 ``\w`` 之后 ASCII 句读两侧都可能是汉字，但两条指令仍然分得开：
    宾语是 lazy 的，而终结符分支里本来就有 ASCII ``.``——引擎先试短的那条。只有短的
    凑不出合法匹配时才会把 ``.`` 吃进话题。这条钉住那个前提。
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == expected, _zh_terms(text)


# ── 27. 左界只给有日文 〜別 歧义的那两个字形 ─────────────────
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
    """⚠️ 日文的 ``〜別`` 后缀歧义是 ``别/別`` 独有的，``莫 / 休 / 甭`` 都不是日文的
    名词后缀。套给全族的话正常的中文主语会把它们一起挡掉（codex P2）。
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == {expected}, _zh_terms(text)


def test_the_ambiguous_negations_are_a_pinned_subset():
    assert D._ZH_NEG_JA_AMBIGUOUS == ("别", "別")
    assert set(D._ZH_NEG_JA_AMBIGUOUS) <= set(D._ZH_NEG_SINGLES)
    # 其余的都进无左界那一支，且 休 仍然排掉 講
    assert D._ZH_NEG_UNAMBIGUOUS == ("莫", "休(?!講)", "甭")


# ── 28. 引号里的中文不算日文守卫的证据 ───────────────────────
@pytest.mark.parametrize(
    "text",
    [
        "世代別講座で中国映画「這就是愛」について話します。",
        "世代別講座で中国映画「这就是爱」について話します。",
        "地域別提案で映画『這就是愛』について話します。",
        # ⚠️ 挖空必须**等长**替换：直接删掉的话引文两侧的字会被拼到一起，凭空造出
        # 多字证据（叫+我 / 不+想 / 懶+得 / 不+願 / 喊+我），日文句子照样被放行进来。
        "世代別講座で叫「這就是愛」我について話します。",
        "地域別提案は不「這」想について話します。",
        "地域別提案で懶「愛」得について話します。",
        "世代別講座で不「愛」願について話します。",
        "地域別講座は喊「愛」我だそうです。",
    ],
)
def test_a_quoted_chinese_title_does_not_disable_the_japanese_guard(text):
    """⚠️ 日文句子引用一个中文片名是正常的。引号里的 ``這`` 把整条守卫短路掉，
    日文句子的残片就进了指令表、注三天（codex P2）。挖空配对引文再搜证据。
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == set()


def test_blanking_the_quotes_keeps_evidence_outside_them():
    """反向：引号**外面**的证据照旧算数，否则整族中文指令会被打死。"""  # noqa: DOCSTRING_CJK
    assert _zh_terms("别再提《想見你喔》。") == {"想見你喔"}
    assert _zh_terms("別叫我「お兄ちゃん」。") == {"お兄ちゃん"}
    assert _zh_terms("别提《我很好吗》这件事。") == {"《我很好吗》这件事"}


# ── 29. 裸的疑问语气词 ───────────────────────────────────────
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("別再提工作嗎？", "工作"),
        ("别再提工作吗？", "工作"),
        ("不要再說工作了嗎？", "工作"),
        ("不要再说工作了吗？", "工作"),
        # ⚠️ 引号里的同一个字仍受保护
        ("别再提《你可以吗》。", "你可以吗"),
        ("別再提《你可以嗎》。", "你可以嗎"),
        # 剥完不够两个字就整条留着
        ("别提行吗。", "行吗"),
        ("別提行嗎。", "行嗎"),
    ],
)
def test_a_bare_question_particle_is_a_tail_too(text, expected):
    assert _zh_terms(text) == {expected}, _zh_terms(text)


def test_a_whitespace_only_message_does_not_blow_up():
    """⚠️ 前置话题的单字分支也匹配空格，于是话题和后面每个 ``\s*`` 能任意瓜分同一串
    空白，把「在哪切」变成组合爆炸——``" " * 60`` 一度要 0.42 秒，而这条路径是每条
    用户消息同步跑的，发一条纯空白消息就能卡住（codex P1）。

    ⚠️ 用**增长倍率**而不是绝对秒数：这个形状本身在 parent 上就是三次方（60/120/240
    实测 0.006/0.05/0.43），本 PR 要守的是「不比 parent 更差」，不是把它变成线性。
    """  # noqa: DOCSTRING_CJK
    import time

    # 预热：别把首次正则编译算进 timings[30]，那会让倍率虚低、判据失灵（CodeRabbit）
    extract_directives(" ")
    timings = {}
    for n in (30, 60):
        started = time.perf_counter()
        extract_directives(" " * n)
        timings[n] = time.perf_counter() - started
    # ⚠️ 主判据是**倍率**。绝对秒数只当一道很松的天花板——共享 CI runner 上负载不可控，
    # 卡得紧会偶发变红（CodeRabbit）。组合爆炸时这里是 0.4 秒往上。
    assert timings[60] < 0.5, timings
    # 组合爆炸时 60 是 30 的几十倍；三次方是 8 倍左右，给足余量取 25
    assert timings[60] < timings[30] * 25 + 0.02, timings


def test_the_preposed_template_spacing_is_atomic():
    """结构面：模板 2 里**动词之前**的每个 ``\s*`` 都必须原子化。

    ⚠️ 动词**之后**那个 ``\s*(?:了)?`` 不能原子化——它后面的终结符字符类里含 ``\s``，
    原子化会把本该当终结符的那个空格吃掉（``工作别提 然后…`` 会从命中变成不命中）。
    """  # noqa: DOCSTRING_CJK
    raw = _zh_pattern_sources()[1]
    # ⚠️ 同样先摘掉那道零宽 temper：它里面的空白是**判据的一部分**（否定词和动词
    # 之间允许空格），不是会参与瓜分的量词。
    raw = raw.replace(f"(?!{D._ZH_DIRECTIVE_AHEAD})", "")
    head = raw.split("(?:提了|")[0]
    # ⚠️ 判据是「**任何**会匹配空白的量词都得包在原子组里」，不是「数出几个 (?>\s*)」。
    # 停顿分隔符里的空白已经收窄成横向空白类（不跨行），数量断言会跟着漂——把两种
    # 单位都摘掉之后再看有没有漏网的，才是真正的不变量。
    units = (r"(?>\s*)", f"(?>{D._ZH_HSPACE})")
    rest = head
    for unit in units:
        rest = rest.replace(unit, "")
    assert r"\s*" not in rest, rest
    assert D._ZH_HSPACE not in rest, rest
    for unit in units:
        assert unit + unit not in head, unit
    # ⚠️ 模板 2 里**触发词之前**已经一个跨行空白都不剩了：话题两侧、填充词两侧、
    # 触发词内部全部收窄成横向（一条指令不跨行）。数量钉一下防止某个单位被整段删掉：
    # 两处 _ZH_PAUSE_THEN_JIU（各 3 个）+ 话题后 1 + 填充词后 1 + 触发词里 2。
    assert head.count(r"(?>\s*)") == 0, head.count(r"(?>\s*)")
    atomic_h = f"(?>{D._ZH_HSPACE})"
    assert head.count(atomic_h) == 2 * D._ZH_PAUSE_THEN_JIU.count(atomic_h) + 4


# ── 30. 嵌套引号 / 动宾停顿 / ASCII 方括号 ───────────────────
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # ⚠️ 外层开括号在内层完整引文**之前**：只扫「最后一段完整引文之后」会漏掉它，
        # ``好吗`` 被当成句子级语气剥掉、连内层的 ``」`` 一起削（codex P2）
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
        # 打字和 ASR 都会在动宾之间产生停顿标点；parent 靠 ``.{1,40}?`` 吃进去再 trim 掉
        ("别再提，工作。", "工作"),
        ("別再提，工作。", "工作"),
        ("我不想聊，工作。", "工作"),
        ("我不想聊，工作", "工作"),
        ("不要说，加班的事。", "加班的事"),
        ("别叫我，“笨蛋”。", "笨蛋"),
        ("别再提、工作。", "工作"),
        ("别再提：工作。", "工作"),
        ("别再提,工作。", "工作"),
        # ⚠️ 顿号 / 冒号**必须**进分隔符表。一度以为不用——它们不在话题字符类的排除
        # 表里，会被当普通字吃进话题再由 trim 剥掉，上面三条靠这条也能过。但话题很短
        # 且以**有歧义的尾字**结尾时就不成立：话题变成 ``：好``（标点凑满了两个单位的
        # 下限），``咧`` 被可选助词组吃掉，trim 完只剩一个字被丢弃（codex P2）。
        ("别提：好咧。", "好咧"),
        ("别提、好咧。", "好咧"),
        ("别提，好咧。", "好咧"),
        ("別提：好咧。", "好咧"),
    ],
)
def test_a_separator_between_verb_and_object_is_consumed(text, expected):
    """⚠️ 分隔符必须排在无宾语前视**之前**：那道前视认「动词之后直接是句读」＝没有
    宾语，没先吃掉分隔符的话这批指令会被它整条否掉（实现顺序写反过一次）。
    """  # noqa: DOCSTRING_CJK
    assert expected in _zh_terms(text), _zh_terms(text)


@pytest.mark.parametrize(
    "text", ["别提，。", "别提。", "别提，", "别提起了。", "别说完了。"],
)
def test_a_separator_alone_is_still_objectless(text):
    """分隔符不能把无宾语判据绕过去。"""  # noqa: DOCSTRING_CJK
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
    """``_TRIM_TRAIL`` 本来就把它们当两端分隔符剥，却没进配对表，于是内部的逗号
    变成硬边界（codex P2）。
    """  # noqa: DOCSTRING_CJK
    assert expected in _zh_terms(text), _zh_terms(text)


# ── 31. 证据只看指令部分 / 长度下限只保护有歧义的尾巴 ────────
@pytest.mark.parametrize(
    "text",
    [
        # ⚠️ 载荷里的中文不算证据——加不加引号都一样（codex P2 两轮）
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
        # 反向：证据在**指令部分**时照旧算数
        "地域別提案をお願いします。",
        "テーマ別討論スレッド。",
        "個別提案をお願いします。",
        "休講だそうです。",
    ],
)
def test_blanking_the_payload_does_not_blind_the_kana_conditions(text):
    """⚠️ 只有**证据**看挖空后的指令部分；假名和日文语法那两条判据仍看完整命中区间。
    传挖空后的串进去会把假名一起挖掉，这批句子会因为「没有假名」被判成中文。
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
    """⚠️ ``没`` 是日文标准字形（没収），不能进字类；但三个字连在一起是中文独有的。
    不收的话简体侧整条被吞，而繁体 ``沒心情`` 因为 ``沒`` 在字类里侥幸活着——同一
    模板内的行为不对称（codex P2）。
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == {expected}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # 无歧义的尾巴（ASCII / 假名 / 谚文）剥到低于下限也要剥，剥完按长度丢弃
        ("stop talking about X porfa", set()),
        ("別再提錢please。", set()),
        ("别再提钱please。", set()),
        # 有歧义的中文尾字仍然保护：剥到存不下等于整条被丢，不如留着
        ("别再提好咧。", {"好咧"}),
        ("别再提拿捏。", {"拿捏"}),
        ("别提行吗。", {"行吗"}),
    ],
)
def test_the_length_floor_only_protects_ambiguous_tails(text, expected):
    """⚠️ ``耶 / 捏 / 咧`` 同时也是常见词尾字，而 ASCII 的 please / porfa、假名、谚文
    不可能是中文词的一部分。对后者套下限的话 ``別再提錢please。`` 会存成 ``錢please``，
    而 parent 是剥掉之后按长度丢弃（codex P2）。
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == expected, _zh_terms(text)


def test_the_ambiguity_criterion_is_the_presence_of_han():
    """判据是「token 里含汉字」，不是手点名单。"""  # noqa: DOCSTRING_CJK
    for tok in D._TRIM_TRAIL_TOKENS_ANY:
        assert not D._HAN_RE.search(tok), tok
    for fam in D._SCRIPT_DISJOINT_FAMILIES:
        for tok in D._TRIM_TRAIL_TOKENS_BY_LOCALE[fam]:
            assert not D._HAN_RE.search(tok), (fam, tok)
    assert all(D._HAN_RE.search(t) for t in D._TRIM_TRAIL_TOKENS_BY_LOCALE["zh"])


def test_the_full_width_comma_is_only_identifier_punctuation_between_digits():
    """⚠️ 全角逗号是中文最常见的分句符，无条件当词内字符会让前置话题跨小句；
    而它真正的标识符用途就是千分位。⚠️ 全角句号完全不收——没有标识符用它。
    """  # noqa: DOCSTRING_CJK
    import re as _re

    probe = _re.compile(D._ZH_IDENT_PUNCT)
    assert probe.search("价格1，000元")
    assert not probe.search("算了，工作")
    assert not probe.search("工作。加班")
    assert _zh_terms("别提工作。别提加班。") == {"工作", "加班"}
    assert _zh_terms("算了，别提工作。") == {"工作"}
    assert _zh_terms("算了,别提工作。") == {"工作"}


# ── 32. 同种括号嵌套 / 称呼类动词 / 模板 4 的空白 ────────────
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # ⚠️ 同种括号嵌套：正则会把外层开括号跟**内层**收尾配成一对，按深度扫才对
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
    # 落单的对称引号不算未闭合——它是英寸号
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
    """⚠️ 模板 1 也收 _ZH_ADDRESS_VERBS，但证据表里原先只有 叫我 / 喊我 / 管我叫。
    ``称`` 是日文标准字形（名称）不能进字类，但 ``称呼我`` 三个字连在一起是中文独有的
    ——不收的话这批指令整条被吞，而 ``别称呼我…`` 因为 ``别`` 在字类里侥幸活着
    （codex P2，跟 甭 / 没心情 是同一族不对称）。
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == {expected}, _zh_terms(text)


def test_every_address_verb_has_evidence_coverage():
    """自动发现：往 _ZH_ADDRESS_VERBS 加动词而忘了同步证据，这里会红。"""  # noqa: DOCSTRING_CJK
    for verb in D._ZH_ADDRESS_VERBS:
        stem = verb.replace("?", "").replace("为", "").replace("為", "")
        assert any(word in stem or stem in word for word in D._ZH_EVIDENCE_WORDS), stem


def test_the_guanyu_template_spacing_is_atomic_too():
    """⚠️ 上一轮只原子化了模板 2、漏了模板 4，``"关于" + " " * 80`` 要 3 秒。"""  # noqa: DOCSTRING_CJK
    import time

    raw = _zh_pattern_sources()[3]
    head = raw.split("(?:说|說|提|聊|讲|講)")[0]
    assert r"\s*" not in head.replace(r"(?>\s*)", ""), head

    extract_directives(" ")  # 预热
    timings = {}
    for n in (40, 80):
        started = time.perf_counter()
        extract_directives("关于" + " " * n)
        timings[n] = time.perf_counter() - started
    assert timings[80] < 0.5, timings
    assert timings[80] < timings[40] * 25 + 0.02, timings


# ── 33. 括号段本身也要认一层同种嵌套 ─────────────────────────
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # ⚠️ 正则只会在第一个同种收尾处闭合，后面的逗号就不再受保护
        ("《电影《你好》续集，第二章》别提了。", "电影《你好》续集，第二章"),
        ("《電影《你好》續集，第二章》別提了。", "電影《你好》續集，第二章"),
        ("《甲《乙》丙，丁》别提了。", "甲《乙》丙，丁"),
        ("别提《电影《你好》续集，第二章》。", "电影《你好》续集，第二章"),
        ("别提「甲「乙」丙，丁」。", "甲「乙」丙，丁"),
    ],
)
def test_a_bracket_run_recognizes_one_level_of_same_type_nesting(text, expected):
    """⚠️ ``_zh_quoted_span_end`` 的深度扫描管的是**剥尾巴**，管不到匹配本身。
    括号段正则自己也得认嵌套，否则 ``《甲《乙》丙，丁》别提了。`` 整条消失（codex P2）。
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == {expected}, _zh_terms(text)


def test_the_nested_branch_only_applies_to_asymmetric_pairs():
    """对称的一对里嵌套没有意义（``"a"b"`` 无法区分开合），只给非对称的加。"""  # noqa: DOCSTRING_CJK
    for lo, hi in D._ZH_BRACKET_PAIRS:
        body = D._zh_bracket_body(lo, hi)
        if lo == hi:
            # ⚠️ 摘掉 temper 再看：它自己就带 ``|``（否定词 / 动词的交替），
            # 按字面找 ``|`` 会把它误当成嵌套分支。
            plain = body.replace(f"(?!{D._ZH_DIRECTIVE_AHEAD})", "")
            assert "|" not in plain.split("]", 1)[-1], (lo, body)
        else:
            import re as _re

            assert f"|{_re.escape(lo)}[^" in body, (lo, body)


def test_nesting_does_not_regress_the_bounded_scan():
    """嵌套分支仍然有界——两个分支互斥，不会引进歧义回溯。"""  # noqa: DOCSTRING_CJK
    import time

    extract_directives(" ")  # 预热
    started = time.perf_counter()
    extract_directives("《" * 8000)
    unmatched = time.perf_counter() - started
    started = time.perf_counter()
    extract_directives("别提" + "《《a》" * 40)
    nested = time.perf_counter() - started
    assert unmatched < 1.0, unmatched
    assert nested < 0.2, nested


# ── 34. trim 的四个边界（全部是同一轮 codex P2） ─────────────
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # 尾词本身就是整条话题 → 一律不剥（剥了整条指令就没了）
        ("stop saying please", "please"),
        ("stop saying porfa", "porfa"),
        ("please is off limits", "please"),
    ],
)
def test_a_tail_token_that_is_the_whole_topic_is_kept(text, expected):
    terms = {t for _loc, _kind, t in extract_directives(text)}
    assert expected in terms, terms


def test_a_tail_token_as_a_real_suffix_is_still_stripped():
    """反向：同一个词做**后缀**时照剥，剥完不够长再按长度丢弃。"""  # noqa: DOCSTRING_CJK
    assert not extract_directives("stop saying X porfa")
    assert not extract_directives("別再提錢please。")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # ⚠️ 中文反问尾巴**不跟着 fallback 走**：en/es/ru/pt 的句法已经框定了宾语
        ("stop saying 你好吗", "你好吗"),
        ("你好吗 is off limits", "你好吗"),
        ("no menciones 你好吗", "你好吗"),
        # ⚠️ 话题够长时才判别得出来：短的会被「下限挡下的尾巴连后缀一起拦」那条兜住
        ("stop saying 工作好吗", "工作好吗"),
        ("no menciones 工作好嗎", "工作好嗎"),
        ("工作好吗 is off limits", "工作好吗"),
    ],
)
def test_chinese_interrogatives_do_not_leak_into_other_templates(text, expected):
    terms = {t for _loc, _kind, t in extract_directives(text)}
    assert expected in terms, terms


def test_the_particle_fallback_still_applies_to_other_templates():
    """反向：助词那批**要** fallback——中英混说的 term 常整段是中文。"""  # noqa: DOCSTRING_CJK
    terms = {t for _loc, _kind, t in extract_directives("stop saying 前女友了")}
    assert "前女友" in terms, terms


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # ⚠️ 长尾巴被下限挡下之后，它的**后缀**也不许再剥
        ("别提钱好吗？", "钱好吗"),
        ("別提錢好嗎？", "錢好嗎"),
        ("别提猫可以吗？", "猫可以吗"),
        ("别提行吗。", "行吗"),
    ],
)
def test_a_floor_blocked_tail_also_blocks_its_suffixes(text, expected):
    """``好吗`` 因为只剩 ``钱`` 被下限挡下，紧接着 ``吗`` 又把它削成非词 ``钱好``。"""  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == {expected}, _zh_terms(text)


def test_tail_tokens_are_tried_longest_first():
    """短 token 往往是长 token 的后缀，先剥短的会把长的那次判断绕过去。"""  # noqa: DOCSTRING_CJK
    import config.prompts.prompts_directives as _d
    import inspect

    src = inspect.getsource(_d._trim_term)
    assert "key=len, reverse=True" in src


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # ⚠️ 落单的对称引号不能遮蔽**后面**的括号
        ('别再提5"屏幕《你好吗》。', '5"屏幕《你好吗》'),
        ('別再提5"螢幕《你好嗎》。', '5"螢幕《你好嗎》'),
        # 成对的仍然当引文
        ('别再提"你的名字"好吗。', "你的名字"),
    ],
)
def test_an_unpaired_symmetric_quote_does_not_mask_later_delimiters(text, expected):
    assert _zh_terms(text) == {expected}, _zh_terms(text)


def test_the_quoted_span_scanner_ignores_odd_symmetric_delimiters():
    end = D._zh_quoted_span_end
    assert end('5"屏幕《你好吗》') == 9      # 落单引号忽略，括号照常记
    assert end('5"屏幕好吗') == 0            # 只有落单引号 = 没有引文
    assert end('"甲"好吗') == 3              # 成对的仍然算


# ⚠️「日文标签左界枚举」那条护栏已删：判据改成否定式之后不再有那张表，
# 由 test_the_clause_start_boundary_needs_no_enumeration 和
# test_the_left_boundary_is_a_negated_class_not_an_enumeration 接管。


# ── 35. 前置话题和触发词之间的停顿标点 ───────────────────────
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
    """⚠️ 停顿标点在**两侧**都会出现：动词后宾语那一侧上一轮补过，前置话题这一侧
    漏了——话题字符类排掉了 ``，``，而模板 2/4 原先只允许空白，整条 0 命中（codex P2）。
    两侧用同一个常量。
    """  # noqa: DOCSTRING_CJK
    assert expected in _zh_terms(text), _zh_terms(text)


def test_the_separator_is_shared_by_both_sides():
    """两侧共用同一个常量，别各写各的——同一件事维护两份必然漂移（#2655）。"""  # noqa: DOCSTRING_CJK
    sources = _zh_pattern_sources()
    # 模板 2 的两处换成了 _ZH_PAUSE_THEN_JIU（同一张标点表 + 停顿后的 ``就``）。
    # ⚠️ 计数是 4 不是 3：``就`` 之后那个可选停顿的写法正好**就是** _ZH_TOPIC_SEPARATOR，
    # 所以模板 2 的串里也含着它。这两个常量本来就该是同源的，计数重合是正常的。
    assert sum(D._ZH_TOPIC_SEPARATOR in raw for raw in sources) == 4
    assert sum(D._ZH_PAUSE_THEN_JIU in raw for raw in sources) == 1
    assert D._ZH_TOPIC_SEPARATOR in D._ZH_PAUSE_THEN_JIU
    # ⚠️ 两个常量必须从**同一个**字符串派生，不是各抄一份——加分号那轮就是因为
    # 只改一处才发现的。
    klass = f"[{D._ZH_PAUSE_CHARS}]"
    # ⚠️ _ZH_PAUSE_THEN_JIU 里有**两处**标点类：``就`` 前面一处、后面一处
    # （``工作，这件事，就，别提了。``）。
    assert D._ZH_PAUSE_THEN_JIU.count(klass) == 2
    assert D._ZH_TOPIC_SEPARATOR.count(klass) == 1
    # 分句标点收，句子终结符（。！？）刻意不收：收了指令就能跨句绑定。
    assert set(D._ZH_PAUSE_CHARS) == set("，、：；,:;")
    assert not set(D._ZH_PAUSE_CHARS) & set("。！？.!?")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # 反向：停顿标点不能把两条独立指令并成一条
        ("工作，别提加班。", {"加班"}),
        ("别提工作，别提加班。", {"工作", "加班"}),
    ],
)
def test_the_pause_does_not_merge_two_directives(text, expected):
    assert _zh_terms(text) == expected, _zh_terms(text)


# ── 36. 模板 3 也不吃 的事 / ASCII 尾词忽略大小写 ────────────
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
    """⚠️ 四条模板现在口径一致：`的事` 是领属加名物化，可以是名字本身的一部分。
    模板 1/2/4 各撤过一次，模板 3 这份最后才被发现（codex P2）。
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == {expected}, _zh_terms(text)


def test_no_zh_template_consumes_deshi_after_the_topic():
    """自动发现：以后哪条模板又把 `的事` 加回可选后缀，这里会红。"""  # noqa: DOCSTRING_CJK
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
    """⚠️ 模板本身是 IGNORECASE 编译的，能命中却剥不掉尾巴（codex P2）。"""  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == {expected}, _zh_terms(text)


@pytest.mark.parametrize(
    ("text", "expected"),
    [("stop saying Please", "Please"), ("stop saying PORFA", "PORFA")],
)
def test_case_insensitive_matching_does_not_change_the_stored_casing(text, expected):
    """比 lower 只用来判断要不要剥，term 本身的大小写原样保留。"""  # noqa: DOCSTRING_CJK
    terms = {t for _loc, _kind, t in extract_directives(text)}
    assert expected in terms, terms


# ── 37. ASCII {} <> / 填充词后的停顿 / 左界改否定式 ──────────
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
    """自动发现：``_TRIM_TRAIL`` 当分隔符剥的 ASCII 括号，配对表里必须都有——
    两处不一致就会让内部标点变成硬边界（codex P2 报了 ``[]``、``{}``、``<>`` 三轮）。
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
    """⚠️ 停顿标点会出现**不止一次**：``工作，这件事，别提了。`` 里填充词两侧各一个。
    只收前面那个的话，正则会从第一个逗号之后重新起匹配、存下 ``这件事``（codex P2）。
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
    """⚠️ 这一维栽过三次：先只挡汉字、漏片假名；补假名后漏拉丁字母 / 数字 / 收尾括号；
    补上之后又漏 ``β``。字符集是开的，枚举永远差一格。

    换成**否定式**——中文指令的否定词总是**起一个小句**，左邻只可能是串首、空白、或
    分句标点；别的（任何文字的字母、数字、收尾括号）都说明 ``別`` 挂在一个词后面。
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == set(), _zh_terms(text)


def test_the_left_boundary_is_a_clause_start_class_not_an_enumeration():
    """结构面：判据必须是「左邻属于分句起点字符」，不能退回枚举日文标签字符。

    ⚠️ 左界现在**只管有歧义动词 + ``再``** 那一支（提 / 講 / 談 / 討論）。日文里
    没有的动词自己就消歧，不走左界。
    """  # noqa: DOCSTRING_CJK
    assert not hasattr(D, "_ZH_JA_LABEL_TAIL")
    assert "一-鿿" not in D._ZH_CLAUSE_START_LEFT
    assert (
        f"(?<![^{D._ZH_CLAUSE_START_LEFT}{D._ZH_POLITE_BEFORE_NEG}])"
        in D._ZH_NEG_VERB_EVIDENCE
    )
    # ⚠️ 敬语 / 主语只在**这一支**放行（``再`` 已经消歧），别渗到别处。
    assert D._ZH_POLITE_BEFORE_NEG == D._ZH_SUBJECT_CHARS + "請"
    assert "請" not in D._ZH_SUBJECT_CHARS
    # 反向：能起小句的那些左邻照常放行。
    # ⚠️ 判别得出这张表大小的用例有两个前提，缺一个就测不出来：
    # 1. 话题要**带日文语法**（``君の名は``）。话题是纯中文时守卫的第三条判据本来就
    #    不成立，整个守卫都不会启动，缩小左界表也看不出差异。
    # 2. 否定词要用**繁体 別**。简体 ``别`` 本身就在 _ZH_EVIDENCE_CHARS 里，单字证据
    #    先一步救下它；繁体 ``別`` 和日文共用码位、不能进那张字表，所以只剩结构证据
    #    这一条命——正是这个 PR 服务的那批用户。
    # ⚠️ 3. 动词要用**有歧义**的（提），否则走的是另一支、根本碰不到左界。
    for text in ("算了，別再提君の名は。", "算了。別再提君の名は。", "工作、別再提君の名は。"):
        assert _zh_terms(text) == {"君の名は"}, text
    assert _zh_terms("算了，别提工作。") == {"工作"}
    assert _zh_terms("别再提工作。") == {"工作"}


# ── 38. 关于后的停顿 / 停顿后的就 / 逗号对偶 / 嘍囉 / 开括号 / ASCII 落单 ──
#
# 这一轮的六条互相咬合，放在一起读：前四条都是「前置话题 + 停顿」这一族，
# 后两条是括号表这一族。


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
    """话题引导词之后也会停顿：``关于，工作，就别提了。``（codex P2）。"""  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == {"工作"}, _zh_terms(text)


@pytest.mark.parametrize("pause", ["，", "、", "：", ",", ":"])
def test_a_pause_before_jiu_lets_the_preposed_template_eat_it(pause):
    """停顿之后的 ``就`` 要吃掉：``工作，就别提了。`` 在 parent 上是有命中的。"""  # noqa: DOCSTRING_CJK
    assert _zh_terms(f"工作{pause}就别提了。") == {"工作"}
    assert _zh_terms(f"工作{pause}这件事{pause}就别提了。") == {"工作"}


@pytest.mark.parametrize("term", ["成就", "迁就", "功成名就", "将就", "迁就"])
def test_without_a_pause_no_jiu_is_eaten(term):
    """没有停顿时一个字都不吃——模板 2 覆盖全部 "X别提了"，就尾词都住这里。"""  # noqa: DOCSTRING_CJK
    assert _zh_terms(f"{term}别提了。") == {term}


def test_the_jiu_slot_lives_inside_the_pause_branch():
    """结构面：``就`` 必须关在停顿分支里，不能是独立可选项。

    独立可选＝没有停顿时也能吃，``成就别提了。`` 立刻被削成 ``成``。
    这条断言比上面的例子更强：它挡住"把 ``(?:就)?`` 挪出括号"这种改法，
    哪怕改完恰好没有现成用例覆盖到。
    """  # noqa: DOCSTRING_CJK
    # 去掉停顿标点分支之后，``就`` 应当整个不可达（即这个模式退化成空匹配）。
    assert D._ZH_PAUSE_THEN_JIU.startswith("(?:[")
    assert D._ZH_PAUSE_THEN_JIU.endswith(")?")
    inner = D._ZH_PAUSE_THEN_JIU[len("(?:") : -len(")?")]
    assert inner.startswith(f"[{D._ZH_PAUSE_CHARS}]"), inner
    # ``就`` 出现在标点字符类**之后**，也就是必须先吃掉一个停顿才轮得到它。
    assert inner.index("就") > inner.index("]")


def test_the_preposed_topic_never_spans_a_full_width_comma():
    """``，`` 刻意**不**进前置话题字符类，哪怕代价是识别不出结构时存下语篇副词。

    ``工作，还是别提了。`` 存的是 ``还是``（同族还有 就是 / 那就 / 最好 / 反正 /
    以后 / 咱们，是开集，枚举不干净），确实难看。但放开 ``，`` 就等于推翻
    test_template2_prefix_never_spans_a_sentence_boundary 钉着的相反方向——
    ``算了，工作别提了。`` 会存成 ``算了，工作``。两句结构完全同形（``X，Y别提了``），
    差别纯粹是词汇性的（哪一半是话题），没有结构判据能分开；上一轮已经定了取右半边。
    这条测试把「不要再来回翻」写进代码。
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
    """``囉`` / ``啰`` 同时是词尾字（嘍囉 / 喽啰），无条件剥会造出非词 ``小嘍``。

    简体那一侧 parent 本来是对的（``小喽啰``），是本 PR 拉坏的（codex P2）。
    判据和同一段注释里的 ``耶`` / ``捏`` 一样：宁可多一个字，不可少一个字。
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == {expected}, _zh_terms(text)


def test_the_ambiguous_tail_glyphs_are_absent_from_both_tables():
    """结构面：两张表里都不能有 ``囉`` / ``啰``——少删一张就还是会削。"""  # noqa: DOCSTRING_CJK
    for glyph in ("囉", "啰"):
        assert glyph not in D._TRIM_TRAIL_TOKENS_BY_LOCALE["zh"], glyph
        assert glyph not in D._ZH_FINAL_PARTICLES, glyph


def test_every_allowed_final_particle_can_also_be_trimmed():
    """两份手抄清单的漂移守卫：正则放行的助词必须都在 trim 表里。

    反过来不要求（trim 表更大没关系，多出来的会被吃进 term 再剥掉）。但
    正则有、trim 没有＝那个助词永远留在 term 里。
    """  # noqa: DOCSTRING_CJK
    allowed = set(D._ZH_FINAL_PARTICLES.rsplit("[", 1)[1].split("]")[0])
    assert len(allowed) > 5, allowed
    # 判据读的是**正则本身**，不是那个常量——不然只是把派生关系复读一遍。这一行
    # 再把两者钉在一起，改了常量却没改正则（或反过来）同样红。
    assert allowed == set(D._ZH_FINAL_PARTICLE_CHARS)
    trimmable = set(D._TRIM_TRAIL_TOKENS_BY_LOCALE["zh"])
    assert allowed <= trimmable, allowed - trimmable


def test_zh_only_particles_are_derived_from_the_regex_particles():
    """两处一度是各抄一份的手写清单，正则吃 ``啊 齁 欸 誒``、证据表却没有，繁中
    整条 0 命中（codex P2）。⚠️ ``了`` 单独排除：它是日文常用汉字（終了 / 了解）。"""  # noqa: DOCSTRING_CJK
    assert set(D._ZH_ZH_ONLY_FINAL_PARTICLES) == (
        set(D._ZH_FINAL_PARTICLE_CHARS) - {"了"}
    ) | {"囉", "啰"}
    assert "了" not in D._ZH_EVIDENCE_CHARS
    # 「正则吃得掉、证据却不认」= 那个助词一进来繁中就整条丢——一个都不许有。
    for char in D._ZH_FINAL_PARTICLE_CHARS:
        if char == "了":
            continue
        assert "君の名は" in _zh_terms(f"別提君の名は{char}。"), char


@pytest.mark.parametrize(
    ("opener", "closer"), sorted(D._ZH_CLOSE_FOR_OPEN.items())
)
def test_an_opening_delimiter_can_start_a_clause(opener, closer):
    """繁中指令写在引号里也要认：``「別再提君の名は。」``（codex P2）。

    日文的 ``〜別`` 后缀不可能紧跟在**开**括号后面，所以放行开括号不会
    把守卫拆掉——同一组括号的**收**那一半仍然挡着（见下一条）。
    ⚠️ 探针必须用 ``別再提``（**有歧义**动词 + ``再``）：左界现在只管这一支，
    换成 ``別聊`` 那种日文里没有的动词会走另一支、根本碰不到左界，测了个寂寞。
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(f"{opener}別再提君の名は。{closer}") == {"君の名は"}


@pytest.mark.parametrize(
    ("opener", "closer"), sorted(D._ZH_CLOSE_FOR_OPEN.items())
)
def test_a_closing_delimiter_still_blocks_the_japanese_label(opener, closer):
    """``「地域」別提案`` 这类日文标签靠**收**括号挡住，一条都不能漏。"""  # noqa: DOCSTRING_CJK
    assert _zh_terms(f"{opener}地域{closer}別再提案をお願いします。") == set()
    assert _zh_terms(f"{opener}地域{closer}別提案をお願いします。") == set()


def test_symmetric_delimiters_are_not_treated_as_clause_starters():
    """对称引号同一个字形两用，放行就等于把守卫拆了（``"地域"別提案``）。"""  # noqa: DOCSTRING_CJK
    for delim in D._ZH_SYMMETRIC_DELIMS:
        assert delim not in D._ZH_CLAUSE_START_LEFT, delim
        assert _zh_terms(f"{delim}地域{delim}別再提案をお願いします。") == set(), delim
    for closer in set(D._ZH_CLOSE_FOR_OPEN.values()):
        assert closer not in D._ZH_CLAUSE_START_LEFT, closer


def test_the_clause_start_boundary_lists_every_asymmetric_opener():
    """结构面：开括号那一半必须整表放行，不能只点几个（自动发现）。"""  # noqa: DOCSTRING_CJK
    for opener in D._ZH_CLOSE_FOR_OPEN:
        assert opener in D._ZH_CLAUSE_START_LEFT, opener


@pytest.mark.parametrize(
    "opener", sorted(o for o in D._ZH_CLOSE_FOR_OPEN if o.isascii())
)
def test_an_unmatched_ascii_opener_is_an_operator_not_a_title(opener):
    """落单的 ASCII 开括号是比较号 / 代码片段，不是没写完的书名号。

    不这么判的话 ``别再提价格<预算好吗？`` 里的 ``好吗`` 永远剥不掉（codex P2）。
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(f"别再提价格{opener}预算好吗？") == {f"价格{opener}预算"}


@pytest.mark.parametrize(
    "opener", sorted(o for o in D._ZH_CLOSE_FOR_OPEN if not o.isascii())
)
def test_an_unmatched_fullwidth_opener_is_still_a_truncated_title(opener):
    """全角开括号在中文里只用来引起引文，落单＝标题被截断，语气词不能剥。"""  # noqa: DOCSTRING_CJK
    assert _zh_terms(f"别再提电影{opener}好不好") == {f"电影{opener}好不好"}


# ── 39. 串首的 別 复合词 / 落单 ASCII 括号遮蔽后面的引文 ─────


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
    """日文的 ``別`` 也能当**前缀**（別提案 ＝ 另一份提案），而这类句子常从它开头。

    串首的 lookbehind 是空真，于是结构证据抢在日文守卫之前短路，存下
    ``案をお願いします``（codex P2）。这一维和 ``X，Y别提了`` 一样没有结构判据——
    中文的 ``別提〈日文标题〉`` 和日文的 ``別提案…`` 在串首完全同形。按代价方向选边：
    存下一段日文残片当禁忌话题比少触发一次坏得多。
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == set(), _zh_terms(text)


@pytest.mark.parametrize(
    "text",
    [
        # 有歧义动词（提 / 講 / 談 / 討論）：靠 ``再`` 消歧
        "別再提君の名は。",
        "「別再提君の名は。」",
        "算了，別再提君の名は。",
        "我們別再提君の名は。",
        # 日文里没有的动词（聊 / 扯 / 說）：动词自己消歧，位置不限
        "別聊君の名は。",
        "別扯君の名は。",
        "別說君の名は。",
        "請別扯君の名は。",
        "算了，別聊君の名は。",
        # 话题本身是中文：日文守卫根本不启动
        "別提工作。",
        "別再提工作。",
    ],
)
def test_the_traditional_directive_still_works_where_it_is_unambiguous(text):
    """选边的代价要收窄到「有歧义动词 + 没有 ``再``」那一格。

    ``再`` 消歧：``別再提`` 在日文里不成立（要写 ``別の再提案``）。动词也消歧：
    ``聊 / 扯 / 說`` 在日文里组不出 ``別X`` 复合名词（``說`` 日文写 ``説``，
    ``扯`` 日文根本没这个字），所以它们不设左界、也不要求 ``再``。
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text), text


def test_the_ambiguous_verb_branch_needs_both_the_boundary_and_zai():
    """⚠️ 有歧义动词那一支两个条件缺一不可，各自打掉一族日文。

    左界挡 ``地域別再提案``（``〜別`` **后缀**）；``再`` 挡 ``別提案``（``別``
    **前缀**）。两族都真实存在于本文件自带的 ja 语料里。
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms("地域別再提案をお願いします。") == set()
    assert _zh_terms("地域別提案をお願いします。") == set()
    assert _zh_terms("今回は、別提案をお願いします。") == set()
    assert _zh_terms("「別提案をお願いします。」") == set()
    # 结构面：有歧义动词只出现在带左界**且**带 ``再`` 的那一支里。
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
    """落单的 ASCII 开括号压在栈底，会让**后面**每段合法引文都记不上收尾位置。

    ``别再提价格<预算《你好吗》。`` 里 ``《你好吗》`` 白闭合，``好吗`` 被当句子级
    语气剥掉、书名腰斩成 ``《你``（parent 是完整的，codex P2）。所以要把落单的
    ASCII 开括号当**普通字符重扫一遍**，而不是扫完再丢掉栈——那时位置已经没了。
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(f"别再提价格{opener}预算《你好吗》。") == {
        f"价格{opener}预算《你好吗》"
    }
    # 引文之外的尾巴照旧要剥掉。
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
    """重扫只忽略**落单**的那几个下标，同句里配对的 ASCII 括号仍然算引文段。

    ⚠️ 这条是「重扫时把所有 ASCII 开括号一起忽略」那种偷懒改法的唯一判别用例：
    要同时有一个落单的（触发重扫）和一个配对的（内容必须受保护），而且配对那段
    要以有歧义的尾词结尾，被误判成句子级语气才看得出来。
    """  # noqa: DOCSTRING_CJK
    text = f"别再提价格{lone}预算{opener}你好吗{closer}。"
    assert _zh_terms(text) == {f"价格{lone}预算{opener}你好吗{closer}"}, _zh_terms(text)


def test_the_quoted_span_end_boundaries():
    """直接打 _zh_quoted_span_end 的三条下标断言。"""  # noqa: DOCSTRING_CJK
    assert D._zh_quoted_span_end("价格<预算《你好吗》") == len("价格<预算《你好吗》")
    assert D._zh_quoted_span_end("《你好吗》好吗") == len("《你好吗》")
    # 全角落单仍然一路延伸到末尾。
    assert D._zh_quoted_span_end("电影《好不好") == len("电影《好不好")


# ── 40. 对称引号里的句读：刻意不排除 ─────────────────────────


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # 带句点 / 逗号的真作品名要完整（parent 两条都被腰斩，本 PR 修好的）
        ('别再提"Everything. Everywhere"。', "Everything. Everywhere"),
        ('别再提"Everything, Everywhere"。', "Everything, Everywhere"),
        ('别再提"你好。世界"好吗？', "你好。世界"),
        ('我不想聊"工作。加班"。', "工作。加班"),
        # 前置话题那两条模板：排掉句读就会在这里产出非词
        ('"工作。加班"别提了。', "工作。加班"),
        ('关于"工作。加班"就别提了。', "工作。加班"),
    ],
)
def test_sentence_punctuation_stays_inside_symmetric_quotes(text, expected):
    """对称引号的字符类**不**排除句读，是量过之后的选择，不是遗漏。

    排掉句读能让两个落单英寸号不跨句配对，但代价是腰斩带句点的真作品名，
    并在模板 2/4 上产出非词（``加班`` / ``加班"就``，两条都比 parent 还差）。
    代价方向和 ``耶 / 捏 / 囉`` 那批一致：宁可多带一段，不可吃字造非词。
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == {expected}, _zh_terms(text)


def test_the_symmetric_quote_guard_is_the_temper_not_a_punctuation_class():
    """结构面：护栏是 ``(?![别別])`` temper，不是从字符类里排句读。

    ⚠️ 这条同时钉住「别再往对称分支里塞死代码」：曾经有一行 ``banned += 句读``
    排在 ``unit`` 组装**之后**，看着像实现了护栏、实际是死变量（coderabbit 报的）。
    """  # noqa: DOCSTRING_CJK
    body = D._zh_bracket_body('"', '"')
    # ⚠️ temper 的判据是**一条完整指令**（否定词 + 可选 再 + 言说动词），不是
    # 「出现了否定词字符」。字符这一维根本不是判据：普通词里以 不 / 莫 / 休 / 别
    # 开头的太多了（不可思议 / 莫名其妙 / 休闲 / 告别版，codex P2）。
    assert f"(?!{D._ZH_DIRECTIVE_AHEAD})" in body
    for punct in "。！？；":
        assert punct not in body, punct
    # 而 temper 确实挡住了两条指令被并成一条。
    assert _zh_terms('尺寸5"别提了。尺寸6"别提了。') == {"尺寸5", "尺寸6"}
    assert _zh_terms('尺寸5"别提了，尺寸6"别提了。') == {"尺寸5", "尺寸6"}


# ── 41. 逗号+空格 / 動詞級歧义 / 括号只在包住整条时才剥 ──────


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Hello, World别提了。", "Hello, World"),
        ("Hello, World別提了。", "Hello, World"),
        ("关于Hello, World就别提了。", "Hello, World"),
        ("關於Hello, World就別提了。", "Hello, World"),
        # 没有空格的写法本来就是好的——这条补的正是它俩之间的不对称
        ("Hello,World别提了。", "Hello,World"),
    ],
)
def test_a_comma_followed_by_a_space_is_still_word_internal(text, expected):
    """``Hello, World`` 是最常见的写法，右侧前视原样要求「不是空白」就吃不进去。

    匹配从逗号之后重起，``Hello, World别提了。`` 只存下 ``World``、
    ``关于Hello, World就别提了。`` 存下非词 ``World就``（parent 两条都完整；
    codex P2）。同一句写成 ``Hello,World`` 反而是好的——补的是这处不对称。
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == {expected}, _zh_terms(text)


def test_the_full_width_comma_decision_is_untouched():
    """⚠️ 上一条**不是**在推翻「前置话题不跨小句」——那条管的是全角 ``，``。"""  # noqa: DOCSTRING_CJK
    assert _zh_terms("算了，工作别提了。") == {"工作"}
    assert _zh_terms("说完了，工作别提了。") == {"工作"}
    # 千分位那条更紧的规则也没动
    assert _zh_terms("价格1，000元别提了。") == {"价格1，000元"}
    # 有界而不是 \s*：模板 2/4 的空白护栏按字面找 \s*，不能被搅浑
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
    """歧义是**动词**的属性，不是位置的属性。

    按位置切过三轮，每挡一格就漏下一格：只挡「左邻是汉字」→ 漏假名 / β；
    改成「左邻要能起小句」→ 漏串首；串首单独要求 ``再`` → 漏分句边界之后
    （``今回は、別提案…`` / ``「別提案…」``，codex P2）。位置根本不是判据。
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == set(), _zh_terms(text)


@pytest.mark.parametrize("verb", sorted(D._ZH_ZH_ONLY_VERBS))
def test_a_verb_japanese_does_not_have_needs_no_disambiguation(verb):
    """日文里组不出 ``別X`` 的动词：不设左界、也不要求 ``再``。

    否则 ``別聊君の名は。`` / ``請別扯君の名は。`` 整条 0 命中，而同一句简体
    因为 ``别`` 在 _ZH_EVIDENCE_CHARS 里有单字证据就是好的（codex P2）。
    ⚠️ ``請`` 进不了主语白名单（它是日文汉字），所以这一族只能靠动词消歧。
    ⚠️ 从 _ZH_ZH_ONLY_VERBS **派生**，不是手点清单：手点那版漏了复合动词
    ``讨论``（coderabbit）。以后往动词表里加字，这条自动跟着覆盖。
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(f"別{verb}君の名は。") == {"君の名は"}
    assert _zh_terms(f"請別{verb}君の名は。") == {"君の名は"}


def test_the_shared_verb_table_is_pinned_and_partitions_the_verbs():
    """结构面：共用动词表是闭集，且必须是全部言说动词的一个真子集。

    ⚠️ 往里加一个不该加的字，对应的繁中说法立刻整条丢掉；漏掉一个该加的，
    对应的日文复合词就漏进来。所以用**相等**断言，不是包含。
    """  # noqa: DOCSTRING_CJK
    assert D._ZH_SAY_VERBS_JA_SHARED == ("提", "講", "談", "討論")
    everything = set(D._ZH_SAY_COMPOUNDS + D._ZH_SAY_VERBS)
    assert set(D._ZH_SAY_VERBS_JA_SHARED) < everything
    assert set(D._ZH_ZH_ONLY_VERBS) == everything - set(D._ZH_SAY_VERBS_JA_SHARED)
    # 简体字形一个都不该在共用表里：日文不用简化字。
    for verb in D._ZH_SAY_VERBS_JA_SHARED:
        assert verb not in ("说", "讲", "谈", "讨论"), verb


@pytest.mark.parametrize(
    ("opener", "closer"), sorted(D._ZH_CLOSE_FOR_OPEN.items())
)
def test_a_closer_survives_when_the_title_is_only_a_suffix(opener, closer):
    """括号只在**真的包住整条 term** 时才剥。

    无条件放进 strip 字符集的话，标题只是 term 的一个后缀时收尾括号会被削掉——
    ``别再提电影〈你好〉。`` 存成 ``电影〈你好``（parent 完整；``〈〉〔〕［］〖〗``
    四对是本 PR 新加进 _TRIM_TRAIL 的，codex P2）。
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(f"别再提电影{opener}你好{closer}。") == {
        f"电影{opener}你好{closer}"
    }


@pytest.mark.parametrize(
    ("opener", "closer"), sorted(D._ZH_CLOSE_FOR_OPEN.items())
)
def test_a_pair_wrapping_the_whole_term_is_still_peeled(opener, closer):
    """反向：整条被一对括号包住时，照旧连括号一起剥掉。"""  # noqa: DOCSTRING_CJK
    assert _zh_terms(f"别再提{opener}你好{closer}。") == {"你好"}


@pytest.mark.parametrize(
    ("opener", "closer"), sorted(D._ZH_CLOSE_FOR_OPEN.items())
)
def test_a_lone_closer_is_still_stripped(opener, closer):
    """反向之二：落单的收尾括号（term 里没有对应开括号）照旧剥掉。"""  # noqa: DOCSTRING_CJK
    assert _zh_terms(f"别再提你好{closer}。") == {"你好"}


@pytest.mark.parametrize(
    "opener", sorted(o for o in D._ZH_CLOSE_FOR_OPEN if o.isascii())
)
def test_a_lone_opener_of_the_same_type_still_shields_the_inner_run(opener):
    """⚠️ 落单的开括号和配对的那个是**同一个字形**时才判别得出重扫的作用域。

    ``别再提<预算<你好吗>。`` ——外层 ``<`` 落单、内层 ``<…>`` 配对。重扫要是
    把所有 ASCII 开括号一起忽略（而不是只忽略落单的那几个下标），内层这段就
    不再算引文，``好吗`` 被当句子级语气剥掉、存成 ``预算<你``。
    上一轮我用「落单 × 配对」两两组合写这条，但把同型那一格排除了，于是漏掉。
    """  # noqa: DOCSTRING_CJK
    closer = D._ZH_CLOSE_FOR_OPEN[opener]
    text = f"别再提{opener}预算{opener}你好吗{closer}。"
    assert _zh_terms(text) == {f"预算{opener}你好吗"}, _zh_terms(text)


def test_symmetric_quotes_need_no_dedicated_peel():
    """行为面：对称引号靠通用 strip 就能两端一起剥，不需要专用分支。

    ⚠️ 这里**没有**「别再写那支死代码」的断言，是想清楚之后不写的：那支分支
    压根没有行为（正是它对应的变异被判为等价的原因），所以不存在能钉住它的行为
    断言；而用源码文本去钉两个方向都不成立——换个写法（``s[0] == s[-1] == '"'``）
    照样绿，实现侧注释里提一句常量名反而红（coderabbit）。判据写在 _strip_trail
    的注释里，那才是它该待的地方。
    """  # noqa: DOCSTRING_CJK
    for delim in D._ZH_SYMMETRIC_DELIMS:
        assert delim in D._TRIM_TRAIL, delim
    assert _zh_terms('别再提"Everything, Everywhere"。') == {"Everything, Everywhere"}
    assert _zh_terms('别再提"你好吗"。') == {"你好吗"}


def test_the_counterpart_table_covers_every_asymmetric_pair():
    """结构面：``_ZH_COUNTERPART`` 两个方向都要有，按括号表自动发现。"""  # noqa: DOCSTRING_CJK
    for opener, closer in D._ZH_CLOSE_FOR_OPEN.items():
        assert D._ZH_COUNTERPART[opener] == closer
        assert D._ZH_COUNTERPART[closer] == opener
    # 对称的那几个刻意不进这张表：同一个字形两用，判不出「另一半在不在」。
    for delim in D._ZH_SYMMETRIC_DELIMS:
        assert delim not in D._ZH_COUNTERPART, delim


# ── 42. 分号当停顿 / 主语跨空格 ──────────────────────────────


@pytest.mark.parametrize("pause", ["；", ";"])
def test_a_semicolon_is_a_pause_like_any_other(pause):
    """分号在话题字符类里被排除（本模块把它当终结符），停顿表里却没收。

    于是 ``别再提；工作。`` / ``工作；别提了。`` 整条 0 命中——parent 存的是
    ``工作``（codex P2）。四条模板都要覆盖到。
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(f"别再提{pause}工作。") == {"工作"}
    assert _zh_terms(f"別再提{pause}工作。") == {"工作"}
    assert _zh_terms(f"工作{pause}别提了。") == {"工作"}
    assert _zh_terms(f"關於工作{pause}就別提了。") == {"工作"}
    assert _zh_terms(f"我不想聊{pause}工作。") == {"工作"}


def test_sentence_terminators_are_not_pauses():
    """反向：``。！？`` 刻意不进停顿表——收了指令就能跨**句**绑定。"""  # noqa: DOCSTRING_CJK
    assert not set(D._ZH_PAUSE_CHARS) & set("。！？.!?")
    # 前一句的残余不该被当成话题
    assert _zh_terms("算了。别提工作。") == {"工作"}


@pytest.mark.parametrize("subject", list("你妳您咱请們"))
@pytest.mark.parametrize("gap", [" ", "  ", "\t"])
def test_an_allowlisted_subject_survives_intervening_whitespace(subject, gap):
    """主语和否定词之间会有空格，而守卫原来只看紧邻的一个字符。

    ``你 別提君の名は。`` 整条 0 命中，同一句简体因为 ``别`` 在
    _ZH_EVIDENCE_CHARS 里有单字证据照样好用——又一处繁简不对称（codex P2）。
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(f"{subject}{gap}別提君の名は。") == {"君の名は"}


@pytest.mark.parametrize("subject", ["我", "他", "請"])
def test_a_japanese_kanji_subject_still_does_not_open_the_guard(subject):
    """⚠️ 加宽左文**不能**顺带放宽主语白名单。

    ``我 / 他 / 請`` 都是日文汉字，收了它们 ``他別提案をお願いします。`` 这类
    句子就会被当成中文存下来。白名单只收日文里根本没有的字形。
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
    """⚠️ 加宽的左文**只**给主语那条用；字类证据仍然只看紧邻一个字符。

    加宽了的话前一句话里的中文字会漏进来，把整个日文守卫短路掉。
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == set(), _zh_terms(text)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # 前一句的中文证据字落在加宽后的左文里、但**不**紧邻日文那句
        ("这话题。地域別提案をお願いします。", set()),
        ("別提这个。世代別講座で話します。", {"这个"}),
    ],
)
def test_evidence_still_reads_only_the_adjacent_character(text, expected):
    """判别「字类证据吃加宽后的左文」这条变异的唯一形态。

    ⚠️ 要**跨一句**：证据字紧邻时两种写法都成立，看不出差异；证据字离得太远
    （超过 _ZH_SUBJECT_LEFT_MAX）也看不出。必须恰好落在加宽窗口里、又不紧邻。
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == expected, _zh_terms(text)


# ── 43. 句号不是词内标点 / 日文存在谓语 ──────────────────────


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # ``. `` 是英文句界，前面整句话不该被吃进话题
        ("That was bad. Work别提了。", "Work"),
        ("I am sad. Work别提了。", "Work"),
        # 逗号那一格照旧要放行一个空格
        ("Hello, World别提了。", "Hello, World"),
        ("关于Hello, World就别提了。", "Hello, World"),
        # 不带空白的句号照旧是词内标点
        ("版本v1.2别提了。", "版本v1.2"),
    ],
)
def test_only_the_comma_tolerates_a_following_space(text, expected):
    """空白只给逗号，句号不给。

    ⚠️ 这跟同一轮里把 ``。！？`` 挡在 _ZH_PAUSE_CHARS 外面是同一条理由——它们
    结束的是**句子**。一开始图省事套在 ``[.,]`` 整个字符类上，是自己制造的不一致
    （coderabbit）。
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == {expected}, _zh_terms(text)


def test_the_two_identifier_punctuations_have_different_lookaheads():
    """结构面：两个符号必须分开写，别再合并回一个字符类。"""  # noqa: DOCSTRING_CJK
    # ⚠️ 空格判据现在从 _ZH_HSPACE_ONE 派生（NBSP 等粘贴空白也要认），而且是**有界的
    # 一串**而不是一个：粘来的标题常带两个空格（``Dr.  Who``）。逗号那支的空白可选，
    # 所以是 {0,n}；句点那支必须有空白（不然就是 ``v1.2`` 那条通用判据管的），是 {1,n}。
    # ⚠️ 可选那支必须写成 ``{0,n}``，不能在 ``{1,n}`` 后面加 ``?``——那是**惰性量词**。
    space = D._ZH_IDENT_GAP_OPT
    assert D._ZH_IDENT_GAP_OPT == D._ZH_HSPACE_ONE + "{0,8}"
    assert D._ZH_IDENT_GAP == D._ZH_HSPACE_ONE + "{1,8}"
    assert D._ZH_IDENT_PUNCT.count(space) == 1
    branches = [b for b in D._ZH_IDENT_PUNCT.split("|") if b.startswith("(?<=")]
    # ⚠️ 判据用「这一支匹配的是哪个字符」，别拿字符类里的标点当特征——字符类里
    # 本来就含逗号和句号。取 lookbehind 和 lookahead **之间**那一段。
    def _matched(branch: str) -> str:
        return branch.split("])", 1)[1].split("(?=", 1)[0]

    dot = [b for b in branches if _matched(b) == chr(92) + "."]
    comma = [b for b in branches if _matched(b) == ","]
    assert len(dot) == 1 and len(comma) == 1, branches
    assert space not in dot[0], dot[0]
    # ⚠️ 这里的 dot 是**通用**那支（``v1.2``），它两侧都不许有空白；带空白的缩写
    # 那几支以 ``(?<![A-Za-z]`` 开头，上面的 branches 过滤把它们排掉了。
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
    """日文电报体整句只有 ``あり`` / ``なし`` 两个假名，谓语表里每一条都够不着。

    于是 ``案あり`` 被当成中文 ban 存三天（codex P2）。这一条和 ``別提案書。``
    那条不同——那条无解（局部同形），这条有干净的判据：``あり/なし`` 是纯假名，
    中文里根本不存在。
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == set(), _zh_terms(text)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # ``なし`` 是 ``なしくずし`` 的**前缀**，不锚在词尾就会把它整条吞掉。
        # ⚠️ 这一条是这条锚**唯一**判别得出来的形态（变异跑出来的）：
        #   · 简体 ``别…`` 有单字证据，守卫根本不启动；
        #   · ``別再提…`` / ``別聊…`` 有结构证据，同样不启动；
        #   · ``ありがとう`` 里的 ``が`` ``と`` 本来就在单字助词类里，锚不锚都被判日文。
        # 只有「繁体 + 共用动词 + 无再 + 话题以 なし 开头且不含单字助词」这一格
        # 才落到谓语表这一步。
        ("別提なしくずし。", "なしくずし"),
        ("别再提なしくずし。", "なしくずし"),
        ("别再提ありがとう。", "ありがとう"),
        ("別再提ありがとう。", "ありがとう"),
        # parent 就救回来的那几个别再打回去
        ("别再提ドラえもん。", "ドラえもん"),
        ("別叫我「お兄ちゃん」。", "お兄ちゃん"),
    ],
)
def test_the_existence_predicates_are_anchored_at_the_end(text, expected):
    """⚠️ 存在谓语必须锚在词尾：日文里它们做谓语时永远在句末，锚定不损失召回，
    不锚就会打死以同样假名开头的专有名词。
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == {expected}, _zh_terms(text)


# ── 44. 主语不跨行 / 剥尾巴后重扫 / 谓语左界 / 裸 だ ────────


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
def test_subject_evidence_does_not_leak_across_lines(newline):
    """``\s`` 连换行一起吃，上一行末尾恰好是白名单里的字就能关掉下一行的守卫。

    ``你\n別提案をお願いします。`` 存下 ``案をお願いします``（codex P2）。
    主语和它管的谓语不可能跨行，所以只收横向空白。
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(f"你{newline}別提案をお願いします。") == set()


@pytest.mark.parametrize("gap", [" ", "\t", "　", "  "])
def test_horizontal_gaps_still_carry_the_subject(gap):
    """反向：同一行里的横向空白照旧要认（半角 / Tab / 全角空格）。"""  # noqa: DOCSTRING_CJK
    assert _zh_terms(f"你{gap}別提君の名は。") == {"君の名は"}


def test_the_subject_gap_is_horizontal_only():
    """结构面：这条不许再用 ``\s``。"""  # noqa: DOCSTRING_CJK
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
    """剥掉一个尾词会**露出**更长的那个，而这一轮的游标已经走过它了。

    于是接着匹配到的是它的后缀：``呢`` → 露出 ``好吗`` → 却剥了 ``吗``，存下非词
    ``工作好``（parent 是 ``工作好吗``；codex P2）。改成剥完就 break、从头重扫。
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == {expected}, _zh_terms(text)


def test_the_length_floor_still_wins_over_the_restart():
    """⚠️ 重扫不能把长度下限那道闸顶掉：短话题 + 有歧义尾巴仍然整个留着。"""  # noqa: DOCSTRING_CJK
    assert _zh_terms("别提钱好吗？") == {"钱好吗"}
    assert _zh_terms("别提猫可以吗？") == {"猫可以吗"}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # 谓语用法：前面是汉语词干
        ("別提案なし。", set()),
        ("別提案あり。", set()),
        ("別提案書あり。", set()),
        # 词汇用法：前面是假名，或者整个词以它开头
        ("別提おもてなし。", {"おもてなし"}),
        ("别提おもてなし。", {"おもてなし"}),
        ("別再提おもてなし。", {"おもてなし"}),
        ("別提なしくずし。", {"なしくずし"}),
        # ⚠️ 右锚在补了左界之后**仍然有用**：汉字接着 なし、但 なし 不在句末时，
        # 它是词的一部分而不是谓语。这条是构造用例（不是自然日文），目的就是把
        # 「谓语＝句末」这个判据钉死——去掉右锚它会被整条吞掉（变异跑出来的）。
        ("別提案なしくずし。", {"案なしくずし"}),
    ],
)
def test_the_existence_predicate_needs_a_kanji_stem(text, expected):
    """``なし`` 两侧都要卡死，缺一边就误伤。

    右边锚句末挡住 ``ありがとう``；左边要求汉字挡住 ``おもてなし``——只锚右边的话
    繁中 ``別提おもてなし。`` 整条 0 命中，而同一句简体是好的（codex P2）。
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == expected, _zh_terms(text)


@pytest.mark.parametrize(
    "text", ["別提案だ。", "今回は、別提案だ。", "「別講座だ。」", "別談話だ。"]
)
def test_a_bare_sentence_final_copula_is_grammar_evidence(text):
    """裸 ``だ`` 在句末是日文系动词，不收的话 ``別提案だ。`` 存下 ``案だ``。"""  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == set(), _zh_terms(text)


@pytest.mark.parametrize(
    "text", ["別提だんご三兄弟。", "别再提だんご三兄弟。", "別再提だんご三兄弟。"]
)
def test_the_bare_copula_is_anchored_so_titles_survive(text):
    """⚠️ 上面那批口语 copula 只收多字形式，正是因为 ``だんご三兄弟``。

    锚在句末之后那条顾虑不再成立（``だんご`` 的 ``だ`` 后面是 ``ん``），所以裸
    ``だ`` 可以收——但锚必须在，去掉就把这几条打回去。
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == {"だんご三兄弟"}


# ── 45. 引号里的 ASCII 尾词 / 谓语后收括号 / 停顿不跨行 /
#        ASCII 运算符不跨句 / 多余收括号 ─────────────────────


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
    """英文作品名会以 ``please`` 结尾，引号门控原先只管 CJK 尾词。

    ``《Never Please》`` 被削成 ``Never``（parent 是完整的）；忽略大小写之后
    ``Please`` / ``PLEASE`` 一起中招（codex P2）。
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == {expected}, _zh_terms(text)


@pytest.mark.parametrize(
    "text", ["别再提工作 please。", "別再提工作 PLEASE。", "别再提工作PLEASE。"]
)
def test_ascii_cleanup_words_outside_quotes_are_still_stripped(text):
    """反向：门控只管**引号之内**，引号外的照旧剥掉。"""  # noqa: DOCSTRING_CJK
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
    """⚠️ 无条件忽略大小写会把**首字母大写**的题名词削掉。

    ``Never Please is off limits.`` 存成 ``Never``，parent 是完整的（codex P2
    反向——忽略大小写本身也是 codex 要求的，这里是把判据收窄到全大写）。
    首字母大写在英文里正是「这是名字的一部分」的信号；全大写是对同一个客套词的强调。
    代价方向和 CJK 那批一致：宁可多一个词，不可吃掉名字的一半。
    """  # noqa: DOCSTRING_CJK
    # ⚠️ 用**全 locale** 提取：这一族里有 en / es 模板的句子，_zh_terms 会把它们滤掉，
    # 断言就成了 set() == set() 的空断言（写这条时踩到的）。
    got = {term for _locale, _kind, term in extract_directives(text)}
    assert got == {expected}, got


@pytest.mark.parametrize(
    ("opener", "closer"), sorted(D._ZH_CLOSE_FOR_OPEN.items())
)
@pytest.mark.parametrize("predicate", ["あり", "なし", "だ"])
def test_a_closing_delimiter_ends_a_japanese_sentence(opener, closer, predicate):
    """引号 / 括号里的整句日文同样是句子，收尾括号也算句末。

    只认句读的话 ``「別提案あり」`` / ``（別提案なし）`` / ``「別提案だ」`` 全漏
    （codex P2）。
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(f"{opener}別提案{predicate}{closer}") == set()


@pytest.mark.parametrize("pause", ["，", "；", "、", ",", ";"])
@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_a_pause_does_not_reach_across_a_line(pause, newline):
    """停顿之后只收横向空白：``别再提，\n工作正常。`` 会把**下一行**存成宾语。

    parent 整条不命中（codex P2）。和主语间隔那条同一个理由：一条指令不跨行。
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(f"别再提{pause}{newline}工作正常。") == set()


@pytest.mark.parametrize("gap", ["", " ", "\t", "　"])
def test_a_pause_still_reaches_across_horizontal_space(gap):
    """反向：同一行里的横向空白照旧收。"""  # noqa: DOCSTRING_CJK
    assert _zh_terms(f"别再提，{gap}工作正常。") == {"工作正常"}


@pytest.mark.parametrize(
    ("opener", "closer"),
    sorted((lo, hi) for lo, hi in D._ZH_CLOSE_FOR_OPEN.items() if lo.isascii()),
)
def test_ascii_operators_do_not_pair_across_a_sentence(opener, closer):
    """两条互不相干的指令各带一个 ASCII 运算符，不该被当成一整段引文。

    ``别再提价格<预算。别再提收入>目标。`` 被并成一条（parent 是分开的两条，
    codex P2）。全角括号和对称 ``"`` 不设这条，理由见 _zh_bracket_body。
    """  # noqa: DOCSTRING_CJK
    text = f"别再提价格{opener}预算。别再提收入{closer}目标。"
    assert _zh_terms(text) == {f"价格{opener}预算", f"收入{closer}目标"}, _zh_terms(text)


def test_full_width_brackets_may_still_span_a_sentence():
    """反向：全角括号只用来引起引文，跨句读照旧允许（真作品名里有句号）。"""  # noqa: DOCSTRING_CJK
    assert _zh_terms("别再提电影《你好。世界》。") == {"电影《你好。世界》"}
    assert _zh_terms('别再提"Everything. Everywhere"。') == {"Everything. Everywhere"}


@pytest.mark.parametrize(
    ("opener", "closer"), sorted(D._ZH_CLOSE_FOR_OPEN.items())
)
def test_a_surplus_closer_is_stripped_by_pairing_order(opener, closer):
    """「另一半在不在串里」太粗：那个开括号可能早就被前面的收括号配掉了。

    ``别再提电影《你好》续集》。`` 里末尾那个 ``》`` 是多余的，parent 会剥掉
    （codex P2）。判据要按**配对顺序**扫。
    """  # noqa: DOCSTRING_CJK
    text = f"别再提电影{opener}你好{closer}续集{closer}。"
    assert _zh_terms(text) == {f"电影{opener}你好{closer}续集"}, _zh_terms(text)


def test_the_unmatched_scan_reports_positions_not_characters():
    """结构面：判据是**位置**，同一个字符可以一边配上一边落单。"""  # noqa: DOCSTRING_CJK
    assert D._zh_unmatched_delims("电影《你好》续集》") == {8}
    assert D._zh_unmatched_delims("电影《你好》续集") == set()
    assert D._zh_unmatched_delims("电影《你好") == {2}
    assert D._zh_unmatched_delims("你好》") == {2}
    # ⚠️ 配对要看**类型**：``《`` 和 ``）`` 不是一对，两个都算落单。
    assert D._zh_unmatched_delims("电影《你好）") == {2, 5}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("别再提电影《你好）。", "电影《你好"),
        ("别再提《你好）。", "你好"),
    ],
)
def test_mismatched_bracket_types_do_not_pair(text, expected):
    """⚠️ 只看「栈非空」就弹的话，``《你好）`` 里那个 ``）`` 会被当成配上了、留在
    term 里。判别用例必须是**类型不匹配**的一对（变异跑出来的）。
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == {expected}, _zh_terms(text)


# ── 46. 就 后的停顿 / 敬语在 再 支放行 / 裸 だ 也要汉字词干 ──


@pytest.mark.parametrize("pause", ["，", "、", "；", ",", ";"])
def test_a_pause_after_jiu_is_consumed_too(pause):
    """``就`` **之后**也会再停顿一次（``工作，这件事，就，别提了。``）。

    只收前面那个的话，正则从第一个逗号之后重新起匹配、存下填充词 ``这件事``——
    parent 存的是 ``工作，这件事，就``，里面至少含着真话题（codex P2）。
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(f"工作{pause}这件事{pause}就{pause}别提了。") == {"工作"}
    assert _zh_terms(f"工作{pause}就{pause}别提了。") == {"工作"}


def test_a_pause_after_jiu_does_not_eat_lexical_jiu():
    """反向：没有停顿时 ``就`` 一个字都不吃（``成就`` 那一族）。"""  # noqa: DOCSTRING_CJK
    for term in ("成就", "迁就", "功成名就"):
        assert _zh_terms(f"{term}别提了。") == {term}


@pytest.mark.parametrize("polite", list("你妳您咱请們請"))
def test_a_polite_marker_is_accepted_before_the_zai_branch(polite):
    """``再`` 已经把歧义解掉了，左界只是为了挡 ``地域別再提案`` 那种后缀。

    主语 / 敬语后面不可能是那个后缀。不放行的话 ``請別再提君の名は。`` 整条
    0 命中，而同句简体是好的（codex P2）。
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(f"{polite}別再提君の名は。") == {"君の名は"}


@pytest.mark.parametrize(
    "text",
    [
        # ⚠️ 敬语只在**带 再** 那一支放行：没有 再 就还是交给日文守卫
        "請別提案をお願いします。",
        "請別提君の名は。",
        # 名词 + 別 那一族（〜別 后缀）照旧全挡
        "地域別再提案をお願いします。",
        "個別再提案をお願いします。",
        "世代別再講座で話します。",
        # 日文汉字主语不许放行
        "我別再提君の名は。",
        "他別再提案をお願いします。",
    ],
)
def test_the_polite_allowance_does_not_open_the_guard(text):
    """⚠️ ``請`` 只在 ``再`` 那一支放行，**不能**进 _ZH_SUBJECT_CHARS。

    进了那张表就等于给无 ``再`` 的 ``請別提案をお願いします。`` 开洞。
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == set(), _zh_terms(text)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # 词汇用法：假名接着 だ
        ("別提ただ。", "ただ"),
        ("別提まだ。", "まだ"),
        ("别提ただ。", "ただ"),
        ("別再提ただ。", "ただ"),
        ("你別提ただ。", "ただ"),
    ],
)
def test_a_bare_copula_needs_a_kanji_stem(text, expected):
    """裸 ``だ`` 也要**汉字词干**，和 ``あり|なし`` 同一条判据。

    日文复合句里 ``だ`` 前面是汉语词干（案だ / 座だ / 話だ）；假名接着它就是词的
    一部分——只锚右边的话繁中 ``別提ただ。`` 整条 0 命中，而同句简体是好的（codex P2）。
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
    """尾巴切片必须从**当前**的 ``s`` 上取，不是原始 term。

    ⚠️ 判别用例要让 ASCII 尾词**剥完别的之后才露出来**：先剥反问短语 ``好吗``，
    ``PLEASE`` 才走到词尾。直接写 ``工作 PLEASE。`` 的话正则早就把尾巴分好了，
    拿原始串切也一样对，测了个寂寞（变异跑出来的）。
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == {"工作"}, _zh_terms(text)


@pytest.mark.parametrize("text", ["別提案だ。", "「別講座だ。」", "別談話だ。"])
def test_a_kanji_stem_copula_is_still_japanese(text):
    """反向：汉字词干那一族照旧全挡。"""  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == set(), _zh_terms(text)


# ── 47. 守卫否掉后重扫 / する・した / 触发词不跨行 ──────────


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
    """日文守卫否掉一条命中之后，那整段区间已经被 finditer 消费掉了。

    藏在里面的**真指令**再也扫不到——整条 0 命中，而 parent 还能抓到后面那句
    （codex P2）。所以被否掉的那条只把游标推进一个字符，从起点之后重扫。
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == {expected}, _zh_terms(text)


def test_the_rescan_does_not_re_extract_accepted_matches():
    """反向：正常命中照旧整段跳过，不然同一条指令会被反复抽出来。"""  # noqa: DOCSTRING_CJK
    assert _zh_terms("别提工作。别提加班。") == {"工作", "加班"}
    # 同一条指令只抽一次——重复会在这里变成多条同名 term（去重前）
    raw = [t for _l, _k, t in extract_directives("别再提工作。")]
    assert raw == ["工作"], raw


@pytest.mark.parametrize(
    "text",
    ["別提案した。", "今回は、別提案した。", "別提案する。", "「別講座した。」"],
)
def test_plain_and_past_sahen_predicates_are_grammar_evidence(text):
    """``します`` / ``しない`` / ``しよう`` 都收了，却漏了最常用的 ``する`` / ``した``。

    ``別提案した。`` 存下 ``案した``（codex P2）。左右两界照抄 ``あり|なし|だ``。
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == set(), _zh_terms(text)


@pytest.mark.parametrize(("text", "expected"), [("別提あした。", "あした")])
def test_the_sahen_predicates_need_a_kanji_stem_too(text, expected):
    """反向：假名词 ``あした`` 不该被当成谓语（左界要汉字，和同族一致）。"""  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == {expected}, _zh_terms(text)


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
@pytest.mark.parametrize("split", ["請別再{nl}提", "別再{nl}提", "別{nl}再提"])
def test_a_trigger_does_not_span_a_line_break(newline, split):
    """触发词**内部**的空白也要横向：否定词↔再↔动词 不可能跨行。

    ``請別再\n提案をお願いします。`` 会把上一行的 ``別再`` 和下一行日文的动词
    接起来，存下 ``案をお願いします``（codex P2）。
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(split.format(nl=newline) + "案をお願いします。") == set()


@pytest.mark.parametrize("gap", [" ", "  ", "\t", "　", ""])
def test_a_trigger_still_tolerates_horizontal_gaps(gap):
    """反向：同一行里的横向空白照旧收。"""  # noqa: DOCSTRING_CJK
    assert _zh_terms(f"别{gap}再{gap}提{gap}工作。") == {"工作"}
    assert _zh_terms(f"不要{gap}再{gap}提工作。") == {"工作"}


# ── 48. 动宾不跨行 / ASCII 括号里的分号 ─────────────────────


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
    """触发词齐了但换了行的话，下一行会被当成宾语接上来。

    ``別再提`` 换行 ``案をお願いします。`` 存下 ``案をお願いします``，而 ``別再提``
    这个结构证据又把日文守卫短路掉了（codex P2）。和触发词内部、主语间隔、停顿
    之后同一条判据：一条指令不跨行。
    ⚠️ 代价：``别再提`` 换行 ``工作。`` 也跟着 0 命中（parent 有）。这是把同一条
    判据贯彻到底的必然结果——上一轮只收窄了触发词内部，就被喂了这一处。
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == set(), _zh_terms(text)


@pytest.mark.parametrize("gap", [" ", "  ", "\t", "　", ""])
def test_a_verb_object_gap_still_tolerates_horizontal_space(gap):
    """反向：同一行里的横向空白照旧收。"""  # noqa: DOCSTRING_CJK
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
    """分号在**闭合**的 ASCII 代码段里是合法内容。

    上一轮为了挡跨句配对把 ``；;`` 一起排掉了，结果 ``代码{foo;bar}别提了。``
    被截成 ``bar``（parent 是 ``代码{foo;bar``；codex P2）。跨句合并那一族靠
    ``。！？`` 就够。
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == {expected}, _zh_terms(text)


# 跨**句号**配对照旧挡着的反向用例，见第 45 节
# test_ascii_operators_do_not_pair_across_a_sentence——这里写第二份逐字相同的会
# 变成两处要一起改的同体测试，正是本文件反复防的那种漂移（coderabbit）。


def test_the_ascii_bracket_exclusion_keeps_semicolons():
    """结构面：ASCII 括号体排的是句末标点，不含分号。"""  # noqa: DOCSTRING_CJK
    body = D._zh_bracket_body("{", "}")
    assert "。" in body and "！" in body, body
    assert "；" not in body, body
    assert ";" not in body, body


# ── 49. Unicode 横向空白 / サ変词尾 / 缩写句点 / 前置话题不跨行 ──


@pytest.mark.parametrize("space", ["\u00a0", "\u202f", "\u2009", "\u3000", " ", "\t"])
def test_any_non_newline_whitespace_is_a_horizontal_gap(space):
    """判据是「**除换行外的任何空白**」，不是手点几个空白字符。

    手点的话 NBSP / U+202F / U+2009 这些从网页、手机输入法粘进来的空白全被挡在
    外面——``别<NBSP>再<NBSP>提工作。`` 整条 0 命中，而 parent 的 ``\s*`` 是认的
    （codex P2）。
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(f"别{space}再{space}提工作。") == {"工作"}
    assert _zh_terms(f"工作{space}别提了。") == {"工作"}


def test_the_horizontal_class_is_a_negated_newline_class():
    """结构面：写成否定式，别退回枚举空白字符。"""  # noqa: DOCSTRING_CJK
    # ⚠️ 排掉的是**所有**行分隔符，不只 CR/LF——Python 的 ``\s`` 还认 U+2028 /
    # U+2029 / U+0085 / \v / \f 和 U+001C~U+001F（codex P2）。
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
    """サ変动词的常用词尾都是句末谓语（codex P2 两轮：先 する/した，后 して/したい/しろ）。"""  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == set(), _zh_terms(text)


@pytest.mark.parametrize(("text", "expected"), [("別提そして。", "そして")])
def test_the_sahen_endings_still_need_a_kanji_stem(text, expected):
    """反向：``そして`` 的 ``そ`` 是假名，够不着左界。"""  # noqa: DOCSTRING_CJK
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
    """``Dr. Who`` / ``U.S. Army`` 的句点后面跟得了空格，不是句界（codex P2）。"""  # noqa: DOCSTRING_CJK
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
    """反向：真句界照旧断开（coderabbit 那条）。判据是**缩写词的形状**——
    词首大写、总共 1~3 个字母；``bad.`` 词首小写、``ABad.`` 前面还连着字母。
    """  # noqa: DOCSTRING_CJK
    got = {term for _locale, _kind, term in extract_directives(text)}
    assert got == {expected}, got


def test_the_abbreviation_rule_survives_ignorecase():
    """⚠️ 模板整个是 IGNORECASE 编译的，裸 ``[A-Z]`` 连小写一起匹配。

    不写 ``(?-i:...)`` 的话判据会退化成「任何句点后跟空格加字母」，
    ``That was bad. Work`` 又被整段吃进话题（自测抓到的）。
    """  # noqa: DOCSTRING_CJK
    assert "(?-i:[A-Z])" in D._ZH_IDENT_PUNCT
    assert D._ZH_IDENT_PUNCT.count("(?-i:") >= 6


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
@pytest.mark.parametrize(
    "text", ["工作正常{nl}別提了。", "關於工作{nl}別提了。", "工作正常{nl}别提了。"]
)
def test_a_preposed_topic_does_not_come_from_the_previous_line(text, newline):
    """上一行会被当成前置话题接下来（codex P2）。

    这是「一条指令不跨行」的第五、六处（触发词内部 / 主语间隔 / 停顿之后 /
    动宾之间 / 话题之后 / 填充词之后）。
    ⚠️ 代价：``工作正常`` 换行 ``别提了。`` 在 parent 上是有命中的，现在没有了。
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text.format(nl=newline)) == set()


def test_no_cross_line_gap_remains_anywhere_in_a_zh_template():
    """结构面：四条模板里**任何位置**都不许再有跨行空白（自动发现，不是手点清单）。

    ⚠️ 这条原先只扫捕获组**之前**，注释还写着「捕获之后的拉不进 term」——那句
    话是错的，被 codex 直接证伪：句末助词那个 ``\s*`` 让触发词齐了之后能跨过换行
    去够下一行的助词，于是**上一行**被整条存下来（``別再提工作`` 换行 ``吧。``）。
    捕获之后的空白拉不进 term，但它决定这条匹配能不能**成立**。

    唯一豁免的是锚在 ``$`` 上的那个 ``\s*$``：它后面只有串尾，什么都够不着。
    """  # noqa: DOCSTRING_CJK
    for index, raw in enumerate(_zh_pattern_sources()):
        # ⚠️ 顺序要紧：先摘前视，再摘横向空白——反过来的话前视里的常量已经被改过，
        # 就摘不掉了（写这条时踩到的）。
        # ⚠️ _ZH_OBJECTLESS_AHEAD 豁免：它是**零宽负前视**，只会「多挡一些」，
        # 拉不进任何内容。判据管的是会 consume 的那些空白。
        body = raw.replace(D._ZH_OBJECTLESS_AHEAD, "")
        body = body.replace(D._ZH_HSPACE, "")
        body = body.replace(chr(92) + "s*$", "")
        # 取反的字符类里出现 ``\s`` 是在**排除**空白，方向相反，不在此列。
        body = re.sub(r"\[\^(?:\\.|[^\]])*\]", "", body)
        assert chr(92) + "s" not in body, (index, body[:200])


# ── 50. Unicode 行分隔符 / 标点后空格 / 主语间隔 / 日文终助词 /
#        中文助词当证据 / 终结符不吃换行 / ASCII 括号里的 ?! ──


@pytest.mark.parametrize("sep", ["\u2028", "\u2029", "\u0085", "\v", "\f"])
@pytest.mark.parametrize(
    "text", ["別再提{s}案をお願いします。", "工作正常{s}別提了。", "别再提{s}工作。"]
)
def test_every_unicode_line_separator_blocks_a_match(sep, text):
    """Python 的 ``\s`` 还认 U+2028 / U+2029 / U+0085 / ``\v`` / ``\f``。

    只排 CR/LF 的话这些照样跨两个视觉行（codex P2）。判据抽成 _ZH_LINE_SEP，
    括号体、话题字符类、横向空白三处都从它取。
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text.format(s=sep)) == set()


def test_the_line_separator_table_is_the_single_source():
    """结构面：三处都从 _ZH_LINE_SEP 派生，别再各写一份 ``\r\n``。"""  # noqa: DOCSTRING_CJK
    assert D._ZH_LINE_SEP in D._ZH_PLAIN_CHAR
    assert D._ZH_LINE_SEP in D._ZH_HSPACE_ONE
    assert D._ZH_LINE_SEP in D._zh_bracket_body("《", "》")


@pytest.mark.parametrize("space", ["\u00a0", "\u202f", "\u2009", "\u3000", " ", "\t"])
def test_unicode_spaces_after_internal_punctuation(space):
    """``Dr.<NBSP>Who`` 这类从网页粘来的写法同样要认（codex P2）。"""  # noqa: DOCSTRING_CJK
    got = {t for _l, _k, t in extract_directives(f"Dr.{space}Who别提了。")}
    assert got == {f"Dr.{space}Who"}, got
    assert _zh_terms(f"关于Hello,{space}World就别提了。") == {f"Hello,{space}World"}


@pytest.mark.parametrize("space", ["\u00a0", "\u202f", "\u2009", "\u3000", " ", "\t"])
def test_unicode_spaces_between_subject_and_negation(space):
    """主语间隔也用同一套横向空白（codex P2）。"""  # noqa: DOCSTRING_CJK
    assert _zh_terms(f"你{space}別提君の名は。") == {"君の名は"}


@pytest.mark.parametrize(
    "text",
    ["別提案か？", "今回は、別提案も。", "「別講座じゃん。」", "別提案ね。", "別提案よ。"],
)
def test_sentence_final_japanese_particles_are_grammar_evidence(text):
    """句末终助词也是日文谓语证据（codex P2）。"""  # noqa: DOCSTRING_CJK
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
    """⚠️ ``も`` 当初被排除在单字助词类外正是因为 ``ドラえもん``。

    加上「汉字词干 + 句末」两道界之后就安全了：``ドラえもん`` 的 ``も`` 前面是
    假名、也不在句末。
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == {expected}, _zh_terms(text)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # ⚠️ 判别「终助词要不要汉字词干」的用例必须是**假名接着终助词**的词，而且
        # 那个词要落在守卫真会跑的那条路上（繁体 + 共用动词 + 无 ``再``）。
        # ``ドラえもん`` 本身不以终助词结尾，测不出来（变异跑出来的）。
        ("別提すいか。", "すいか"),
        ("別提ドラえもんか。", "ドラえもんか"),
        ("別提あかさ。", "あかさ"),
    ],
)
def test_the_final_particles_need_a_kanji_stem(text, expected):
    """句末终助词也要「汉字词干」这道左界，否则以假名结尾的日文专名被整条吞掉。"""  # noqa: DOCSTRING_CJK
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
    """话题本身带日文助词时，落在捕获组外的 ``吧`` / ``提了`` 是唯一的中文证据。

    不收的话繁中整条 0 命中，同句简体因为 ``别`` 有单字证据而正常（codex P2）。
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
    """⚠️ 往证据字类里加字最容易短路守卫（``没`` / ``称`` 踩过两轮）。

    这批是中文独有的句末助词，日文根本不用——整份 ja 语料复测。
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == set(), _zh_terms(text)


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\u2028"])
def test_a_trigger_terminator_does_not_eat_a_newline(newline):
    """触发词落在行末时，换行本身被当成「这条指令说完了」，上一行被绑成话题。

    ``工作正常別提`` 换行 ``下一句。`` 存下 ``工作正常``（codex P2）。
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(f"工作正常別提{newline}下一句。") == set()
    assert _zh_terms(f"工作正常别提{newline}下一句。") == set()


def test_a_horizontal_space_is_still_a_terminator():
    """反向：同一行里的空格照旧算终结（``工作别提 然后…``）。"""  # noqa: DOCSTRING_CJK
    assert "工作" in _zh_terms("工作别提 然后…")
    assert _zh_terms("工作正常别提了。") == {"工作正常"}


@pytest.mark.parametrize(
    ("opener", "closer"),
    sorted((lo, hi) for lo, hi in D._ZH_CLOSE_FOR_OPEN.items() if lo.isascii()),
)
@pytest.mark.parametrize("mark", ["?", "!", ".", ";"])
def test_ascii_punctuation_inside_a_closed_pair_is_content(opener, closer, mark):
    """ASCII 的 ``? ! . ;`` 在**闭合**的标题 / 代码段里是内容。

    上一轮为挡跨句配对把 ASCII 句末标点也排掉了，``电影(Who?)别提了。`` 整条
    0 命中（parent 还留着 ``电影(Who``；codex P2）。跨句那族用的是全角 ``。``。
    """  # noqa: DOCSTRING_CJK
    text = f"电影{opener}Who{mark}{closer}别提了。"
    assert _zh_terms(text) == {f"电影{opener}Who{mark}{closer}"}, _zh_terms(text)


# ── 51. ASCII 括号跨指令配对 / 助词与证据同步 / 前置触发词证据 /
#        汉字词干接上被吃掉的动词 ───────────────────────────────


@pytest.mark.parametrize(
    ("opener", "closer"),
    sorted((lo, hi) for lo, hi in D._ZH_CLOSE_FOR_OPEN.items() if lo.isascii()),
)
@pytest.mark.parametrize("sep", ["。", ".", "，", ",", ";", "；"])
def test_an_ascii_pair_never_spans_two_directives(opener, closer, sep):
    """两条互不相干的指令各带一个 ASCII 括号，不许被当成一整段引文。

    放开 ASCII 句读之后 ``别再提价格<预算.别再提收入>目标.`` 被并成一条，
    第二条指令整个丢掉（codex P2）。判据和对称引号那支一样是 temper 掉否定词
    ——分隔的标点是开集（句号 / 逗号 / 分号都行），否定词才是病因。
    """  # noqa: DOCSTRING_CJK
    text = f"别再提价格{opener}预算{sep}别再提收入{closer}目标."
    assert _zh_terms(text) == {f"价格{opener}预算", f"收入{closer}目标"}, text


@pytest.mark.parametrize(
    ("opener", "closer"),
    sorted((lo, hi) for lo, hi in D._ZH_CLOSE_FOR_OPEN.items() if lo.isascii()),
)
def test_tempering_the_negation_still_keeps_closed_titles(opener, closer):
    """反向：体里**没有**否定词的闭合标题照旧完整（上一条判据的代价上界）。"""  # noqa: DOCSTRING_CJK
    text = f"别再提标题{opener}Who?{closer}。"
    assert _zh_terms(text) == {f"标题{opener}Who?{closer}"}, text
    # 不带标点的 ``告别版`` 走单字分支，带否定词也照旧完整。
    assert _zh_terms(f"别再提{opener}告别版{closer}。") == {"告别版"}


@pytest.mark.parametrize("verb", sorted(D._ZH_PREPOSED_SAY_VERBS))
def test_every_preposed_trigger_form_is_also_chinese_evidence(verb):
    """前置话题模板放行的每个触发词形式，加上 ``了`` 都必须是中文证据。

    ⚠️ 自动发现，不是手点清单：这里放行、证据表却不认，就意味着话题本身带日文
    助词时繁中整条 0 命中——``君の名は別提起了。`` 就是这么丢的（codex P2）。
    """  # noqa: DOCSTRING_CJK
    assert D._ZH_EVIDENCE_RE.search(verb + "了"), verb
    assert "君の名は" in _zh_terms(f"君の名は別{verb}了。"), verb
    assert "君の名は" in _zh_terms(f"君の名は别{verb}了。"), verb


def test_the_preposed_trigger_list_is_not_hardcoded_in_the_template():
    """结构面：模板里不许再写死触发词表——写死过一次，证据那侧就漏了两个形式。"""  # noqa: DOCSTRING_CJK
    # ⚠️ 原先只断言「有**一条**模板从常量派生」——于是模板 4 那份写死的表就漏掉了：
    # ``關於工作就別提起了。`` 走不进专用模板、退回通用的，把 ``就`` 一起存下来
    # （codex P2）。清单式漏项的典型形态，改成「**每一条**带 ``别/別`` 触发词的
    # 模板都必须派生」。
    # 判据：触发词落在捕获组**之后**的那些模板（前置话题式），它们共用这张表。
    # ⚠️ 又要先摘掉那道零宽 temper：它自带 ``[别別]``，不摘的话模板 3（``我不想聊X``）
    # 也会被算成前置话题式（第一版就是这么错的）。
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
    # ⚠️ 判「有没有写死」只能读**源文件**：编译出来的模板串必然长得跟字面量一样，
    # 拿它去断言等于什么都没测（这条测试第一版就是这么写的，一跑就红）。
    source = pathlib.Path(D.__file__).read_text(encoding="utf-8")
    assert "提起|提及" not in source, "触发词表又被写死进模板了"
    assert source.count("_ZH_PREPOSED_SAY_VERBS") >= 4


@pytest.mark.parametrize(
    "text",
    ["別討論あり。", "地域別討論なし。", "商品別討論した。", "別討論する。",
     "カテゴリ別討論して。", "地域別討論か？", "別討論も。", "別談話だ。"],
)
def test_a_consumed_compound_verb_still_supplies_the_han_stem(text):
    """``討論`` 整个进了触发词，term 只剩假名，汉字词干那四条判据全落空。

    ``別討論あり。`` 存下 ``あり``，而结构一样的 ``別提案あり。``（term 是
    ``案あり``）是拦住的（codex P2）。守卫现在把被吃掉的那个汉字接回去。
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
    """反向：接回来的只有**一个**汉字，而四条判据要的都是「汉字紧跟假名」，
    所以 ``ドラえもん`` 一族（假名前面还是假名）照旧保留。"""  # noqa: DOCSTRING_CJK
    assert expected in _zh_terms(text), text


def test_the_stem_is_taken_from_inside_the_match_not_guessed():
    """结构面：词干取自捕获组左邻的那个字符，且只在它是汉字时才接。"""  # noqa: DOCSTRING_CJK
    assert not D._is_japanese_sentence_match("別討論あり", "あり", "", stem="")
    assert D._is_japanese_sentence_match("別討論あり", "あり", "", stem="論")
    # 拉丁 / 假名不许当词干接进去——接了就等于把判据的左界整个作废。
    assert not D._HAN_RE.match("A")
    assert not D._HAN_RE.match("ス")


# ── 52. 日文语法表逐条钉住（新守卫盖住旧判据之后补的） ──────────


def _ja_grammar_alternatives() -> tuple[list[str], list[str]]:
    """把 _JA_GRAMMAR_RE 拆成顶层分支，分出「裸字面量」和「带左右界的」。"""  # noqa: DOCSTRING_CJK
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
    # 话题字符类排掉的字符**不可能**出现在 term 里，含它的分支对 zh 模板是死的。
    # 判据自动推导，不写死清单。
    banned = set("，。！？；,.!?;")
    reachable = [lit for lit in plain if not (set(lit) & banned)]
    unreachable = [lit for lit in plain if set(lit) & banned]
    return reachable, unreachable


_JA_REACHABLE, _JA_UNREACHABLE = _ja_grammar_alternatives()


def test_the_reachable_grammar_markers_are_actually_reachable():
    """自动发现：表里每个裸字面量都要么可达、要么被判定为死条目，没有第三种。

    ⚠️ 这里必须是**相等**断言，不能写成 ``len(...) > 45`` 那种下界。下面那批用例
    是从这张表**派生**出来的参数——摘掉一条表项就等于少一个用例，永远不红；下界
    也挡不住（摘掉七条还剩五十）。这条相等断言才是真正钉住表内容的东西（变异跑
    出来的：整行整行删表，测试全绿）。
    """  # noqa: DOCSTRING_CJK
    assert sorted(set(_JA_REACHABLE)) == [
        "かな", "かも", "から", "が", "ください", "けど", "された", "される",
        "しか", "している", "しない", "します", "しよう", "じゃない", "すべき",
        "そうです", "たら", "だけ", "だっけ", "だった", "だって", "だな", "だね",
        "だよ", "だろう", "てある", "ている", "ておく", "で", "である", "できる",
        "でした", "でしょ", "です", "でも", "と", "という", "ながら", "など",
        "に", "について", "に関して", "の", "ので", "は", "ばかり", "へ",
        "ました", "ましょう", "ます", "ません", "まで", "みたい", "より",
        "らしい", "を",
    ]
    # ⚠️ 死条目**也**用相等断言钉住：再多一条就说明有人往表里加了永远打不中的
    # 标记。``そう？`` 的 ``？`` 是话题终结符，term 里不可能出现它。
    # ⚠️ 死条目也用相等断言钉住：再多一条就说明有人往表里加了永远打不中的标记。
    # 曾经有一条 ``そう？``——把**标点**写进了标记里，而句读永远落在捕获组之外
    # （它就是这条指令的终结符）。这条守卫先认出它不可达，随后改成了 ``そう`` +
    # 汉字词干 + 句末锚（codex P2）。现在一条都不该有。
    assert _JA_UNREACHABLE == [], _JA_UNREACHABLE


@pytest.mark.parametrize("marker", sorted(set(_JA_REACHABLE)))
def test_every_japanese_grammar_marker_is_pinned_on_its_own(marker):
    """逐条钉住日文语法表——**串首**的框架把新守卫关掉，只剩这张表在干活。

    ⚠️ 这一批是补覆盖洞的：``〜別 + 一个汉字 + 假名`` 那条新判据一上，原先钉这
    张表的用例全被它先拦下了，于是「把表整个删掉」不再见红（变异跑出来的）。
    串首没有左邻字符，新判据的左界这一条不成立，语法表是唯一还在拦的东西。
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(f"別提案{marker}。") == set(), marker


def test_the_string_start_frame_really_disables_the_kana_tail_guard():
    """反向钉住上面那批用例的**前提**：串首确实走不到新判据。

    前提要是塌了（比如以后给新判据去掉左界），上面那批就会变成两条判据一起
    拦——表删掉照样绿，覆盖洞悄悄回来。这里直接断言判据本身。
    """  # noqa: DOCSTRING_CJK
    # 同一个 term 形状（一个汉字 + 假名），只差左邻有没有标签词：
    #   · 串首 → 新判据不成立，``スレ`` 又没有语法标记 → 放行
    #   · 有标签词 → 新判据自己就拦下来了
    # 上面那批用例走的是第一行这条路，所以它们钉的确实只有语法表。
    assert not D._is_japanese_sentence_match("別提案スレ", "案スレ", "")
    assert D._is_japanese_sentence_match("地域別提案スレ", "案スレ", "地域")
    # 而串首的 ``案です`` 被判成日文，只可能是语法表干的。
    assert D._is_japanese_sentence_match("別提案です", "案です", "")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # 词头是**假名** → 判据不成立。这是这道守卫从第一轮起就在保的东西。
        ("動畫別提ドラえもん。", "ドラえもん"),
        ("動漫別提お兄ちゃん。", "お兄ちゃん"),
        # 词头是**两个以上**汉字 → 判据不成立。日文那一侧的复合名词只吃掉一个字。
        ("遊戲別提初音ミク。", "初音ミク"),
        ("動畫別提美少女戰士セーラームーン。", "美少女戰士セーラームーン"),
        ("動漫別提哆啦A梦。", "哆啦A梦"),
    ],
)
def test_the_kana_tail_guard_only_fires_on_one_han_char(text, expected):
    """⚠️ 「恰好一个汉字接假名」这个形状是判据的**全部**——两条都拿掉就会打死
    这一批（变异跑出来的：我在别处量过这几句，却没写进测试）。

    左邻都有标签词（動畫 / 遊戲 / 動漫），所以左界那条是成立的；把它们保下来的
    只有 term 形状这一条。
    """  # noqa: DOCSTRING_CJK
    assert expected in _zh_terms(text), text


def test_the_stem_is_never_kana_or_latin_by_construction():
    """`_HAN_RE.match(stem)` 那道闸是**结构不变量**，不是行为判据。

    词干取的是捕获组左邻那个字符，而它只可能是动词末字（汉字）、停顿标点或空白
    ——四条模板里没有一条会让假名 / 拉丁落在那个位置。所以「把闸拿掉」是**等价
    变异**：找不到能把它测红的输入。这里直接钉住不变量本身。
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


# ── 53. 缩写字母数有界 / 助词与终结符不跨行 / サ変挂终助词 / 句末了当证据 ──


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Prof. X别提了。", "Prof. X"),      # 4 个字母，原先整条 0 命中
        ("Prof. X別提了。", "Prof. X"),
        ("Capt. America别提了。", "Capt. America"),
        ("Assoc. Prof别提了。", "Assoc. Prof"),   # 5 个字母
        ("Dr. Who别提了。", "Dr. Who"),           # 2，回归守卫
        ("Mrs. Smith别提了。", "Mrs. Smith"),     # 3
        ("关于Capt. America就别提了。", "Capt. America"),
    ],
)
def test_longer_abbreviations_keep_their_period(text, expected):
    """⚠️ 原先是**写死三条**（1~3 个字母），四个字母的 ``Prof.`` 落在外面：
    ``Prof. X别提了。`` 从 parent 的 ``Prof. X`` 变成**整条 0 命中**（截成 ``X``
    一个字，撞长度下限被丢弃）——简体也回归（codex P2）。"""  # noqa: DOCSTRING_CJK
    assert expected in _zh_terms(text), (text, _zh_terms(text))


def test_the_abbreviation_branches_are_generated_not_hardcoded():
    """结构面：分支从 _ZH_ABBREV_MAX_LETTERS 生成，别再手抄。"""  # noqa: DOCSTRING_CJK
    assert D._ZH_ABBREV_PERIOD.count("|(?<![A-Za-z]") == D._ZH_ABBREV_MAX_LETTERS
    assert D._ZH_ABBREV_PERIOD in D._ZH_IDENT_PUNCT
    # 真句界照旧要挡住——这是这条判据的**反向**边界，抬上限不能把它抬没了。
    assert _zh_terms("That was bad. Work别提了。") == {"Work"}


@pytest.mark.parametrize("sep", ["\n", "\r\n", "\u2028", "\u0085"])
@pytest.mark.parametrize(
    "text",
    [
        "別再提工作{s}吧。",        # 句末助词那一格（第八格）
        "别再提工作{s}吧。",
        "工作正常別提 {s}下一句。",  # 前置话题终结符 + 尾随空格（第九格）
        "工作正常别提 {s}下一句。",
        "我不想聊工作{s}了。",       # 模板 5 的 了（第十格，守卫自己抓出来的）
    ],
)
def test_no_zh_template_binds_across_a_line(sep, text):
    """「一条指令不跨行」的第 8/9/10 格。

    ⚠️ 第十格不是 codex 报的——把结构守卫从「捕获组之前」放宽到**整条模板**之后
    自己冒出来的。前九格是被一格一格喂过来的，这条守卫才是真正封住这一族的东西。
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text.format(s=sep)) == set(), text.format(s=sep)


@pytest.mark.parametrize(
    ("text", "expected"),
    [("別再提工作 吧。", "工作"), ("別再提工作吧。", "工作"),
     ("别再提工作\u00a0吧。", "工作"), ("工作别提 然后我们聊别的。", "工作"),
     ("我不想聊工作 了。", "工作")],
)
def test_a_same_line_gap_still_works(text, expected):
    """反向：同一行里的空白（含 NBSP）照旧放行——收窄的只是换行那一维。"""  # noqa: DOCSTRING_CJK
    assert expected in _zh_terms(text), text


@pytest.mark.parametrize(
    "text",
    ["別提案してね。", "今回は、別講座してね。", "別提案するな。", "別提案したね。",
     "別提案しろよ。", "別提案してか。", "別提案するかも。"],
)
def test_sahen_predicates_may_carry_a_trailing_particle(text):
    """サ変词尾后面还能再挂一个终助词：``別提案してね。`` 存下 ``案して``（codex P2）。

    判据是**闭集 × 闭集**——サ変词尾那批 × 已经收好的句末终助词那批，两处从同一个
    常量取，不是往表里再塞几个词。
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == set(), text


@pytest.mark.parametrize(
    ("text", "expected"),
    [("別提そしてね。", "そして"), ("別提そして。", "そして"),
     ("別提ドラえもん。", "ドラえもん"), ("別提すいか。", "すいか")],
)
def test_the_sahen_branch_still_needs_a_han_stem(text, expected):
    """反向：``そして`` 的 ``し`` 前面是假名，左界不成立，照旧保留。"""  # noqa: DOCSTRING_CJK
    assert expected in _zh_terms(text), text


def test_a_sentence_final_le_is_chinese_evidence():
    """``別提君の名は了。`` 靠句末的 ``了`` 才活下来，同句简体有 ``别`` 一直是好的。"""  # noqa: DOCSTRING_CJK
    assert "君の名は" in _zh_terms("別提君の名は了。")
    assert "君の名は" in _zh_terms("别提君の名は了。")


@pytest.mark.parametrize(
    "text", ["地域別提案は終了。", "別提案の受付は終了。", "世代別講座は終了。",
             "地域別講座について終了。"],
)
def test_the_le_evidence_needs_a_non_han_on_its_left(text):
    """⚠️ 只锚右边（句末）会捅出一个比它救回来的大得多的洞。

    ``終了 / 完了 / 修了`` 里的 ``了`` 是词的一部分。先做的正是只锚右边那版，量下来
    8 条日文里错 7 条（``地域別提案は終了。`` 存下非词 ``案は終``），换回来的只有
    1 条中文，撤了重做。
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == set(), text


# ── 54. 否定词字符从表派生 / 标识符空白有界成串 / そう 的死分支 ────


@pytest.mark.parametrize("neg", sorted(D._ZH_NEG_SINGLES + D._ZH_NEG_MULTIS))
@pytest.mark.parametrize(
    ("opener", "closer"),
    sorted((lo, hi) for lo, hi in D._ZH_CLOSE_FOR_OPEN.items() if lo.isascii()),
)
def test_every_negation_is_tempered_inside_ascii_brackets(neg, opener, closer):
    """⚠️ 自动发现：**每个**否定词都不许把两条指令配成一段引文。

    先写死成 ``别別`` 两个字，于是 ``不要提…`` / ``不许提…`` / ``甭提…`` 照样被并成
    一条（codex P2，三条都是简体回归）。现在从否定词表派生，加一个否定词自动跟上。
    """  # noqa: DOCSTRING_CJK
    text = f"{neg}提价格{opener}预算.{neg}提收入{closer}目标."
    assert _zh_terms(text) == {f"价格{opener}预算", f"收入{closer}目标"}, text


def test_the_tempered_chars_are_derived_from_the_negation_tables():
    """结构面：别再手抄。"""  # noqa: DOCSTRING_CJK
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
    """⚠️ 原先只认**一个**空格，注释还写着「一个就够了」——量下来不够。

    从网页 / PDF 粘来的标题常带两个空格：``Dr.  Who别提了。`` 退回 ``Who``、
    ``Prof.  X别提了。`` **整条丢**（codex P2，三条都是简体回归）。
    """  # noqa: DOCSTRING_CJK
    assert expected.format(g=gap) in _zh_terms(text.format(g=gap)), text.format(g=gap)


def test_the_optional_gap_is_a_range_not_a_lazy_quantifier():
    """⚠️ ``{1,8}?`` 是**惰性量词**，不是「零或一个 {1,8}」。

    在 _ZH_IDENT_GAP 后面加 ``?`` 会让 ``Hello,World``（不带空格）整族失配——
    改完一跑就抓到了。可选那支必须自己写成 ``{0,8}``。
    """  # noqa: DOCSTRING_CJK
    assert D._ZH_IDENT_GAP_OPT.endswith("{0,8}")
    assert "{1,8}?" not in D._ZH_IDENT_PUNCT
    assert "Hello,World" in _zh_terms("Hello,World别提了。")


@pytest.mark.parametrize("mark", ["？", "?", "。", "！"])
def test_sentence_final_sou_is_guarded(mark):
    """``そう？`` 那条分支把**标点**写进了标记里，而句读永远落在捕获组之外
    （它就是这条指令的终结符），所以那条分支是死的：``別提案そう？`` 照样存下
    ``案そう``（守卫先认出不可达，codex 随后给出正解）。"""  # noqa: DOCSTRING_CJK
    assert _zh_terms(f"別提案そう{mark}") == set(), mark


def test_the_sou_branch_still_needs_a_han_stem():
    """反向：``ドラえもんそう`` 的 ``そ`` 前面是假名，左界不成立。"""  # noqa: DOCSTRING_CJK
    assert "ドラえもんそう" in _zh_terms("別提ドラえもんそう？")


# ── 55. temper 判据改成「一条完整指令」/ 双字动词的假名尾巴 ────


@pytest.mark.parametrize(
    ("opener", "closer"),
    sorted((lo, hi) for lo, hi in D._ZH_CLOSE_FOR_OPEN.items() if lo.isascii()),
)
@pytest.mark.parametrize("word", ["不可思议", "莫名其妙", "休闲", "告别版", "不错"])
def test_ordinary_words_starting_with_a_negation_stay_inside_brackets(
    opener, closer, word,
):
    """⚠️ 上一轮把 temper 写成「禁否定词**首字**」，矫枉过正。

    普通词里以 ``不 / 莫 / 休 / 别`` 开头的太多了：``电影(不可思议; 2020)别提了。``
    退回 ``2020``（codex P2，简体回归）。字符这一维根本不是判据——真正说明「括号跨了
    两条指令」的是**否定词后面跟着言说动词**。
    """  # noqa: DOCSTRING_CJK
    text = f"电影{opener}{word}; 2020{closer}别提了。"
    assert _zh_terms(text) == {f"电影{opener}{word}; 2020{closer}"}, text


@pytest.mark.parametrize("neg", sorted(D._ZH_NEG_SINGLES + D._ZH_NEG_MULTIS))
def test_the_symmetric_quote_run_tempers_every_negation_too(neg):
    """对称引号那支原先只挡 ``别別``，其余否定词照样把两条指令并成一条
    （``不要提价格"预算.不要提收入"目标.``；codex P2，简体回归）。两处同源。"""  # noqa: DOCSTRING_CJK
    text = f'{neg}提价格"预算.{neg}提收入"目标.'
    assert _zh_terms(text) == {'价格"预算', '收入"目标'}, text


def test_the_temper_is_a_full_directive_not_a_character_class():
    """结构面：判据是完整指令，两处从同一个常量取。"""  # noqa: DOCSTRING_CJK
    for neg in D._ZH_NEG_SINGLES + D._ZH_NEG_MULTIS:
        assert neg in D._ZH_DIRECTIVE_AHEAD, neg
    for verb in D._ZH_SAY_COMPOUNDS + D._ZH_SAY_VERBS:
        assert verb in D._ZH_DIRECTIVE_AHEAD, verb
    assert "(?![别別])" not in D._ZH_BRACKET_RUN


@pytest.mark.parametrize(
    "text", ["地域別討論スレ。", "A別討論ページ。", "カテゴリ別討論メモ。"],
)
def test_a_compound_verb_label_tail_is_suppressed(text):
    """``討論`` 是**双字**共用复合词，整个进了触发词，term 直接以假名开头，
    「一个汉字接假名」那条形状判据永远不成立（codex P2）。词干接回来再判。"""  # noqa: DOCSTRING_CJK
    assert _zh_terms(text) == set(), text


@pytest.mark.parametrize(
    ("text", "expected"),
    [("動畫別提ドラえもん。", "ドラえもん"), ("遊戲別提初音ミク。", "初音ミク"),
     ("動漫別提お兄ちゃん。", "お兄ちゃん")],
)
def test_only_compound_verbs_reattach_the_stem(text, expected):
    """⚠️ 反向：**只**在双字动词时接词干。单字动词（提 / 講 / 談）也接的话
    ``提ドラえもん`` 就成了「一个汉字接假名」，把这道守卫从第一轮起就在保的东西
    全打死（试过，实测回归）。"""  # noqa: DOCSTRING_CJK
    assert expected in _zh_terms(text), text


def test_the_compound_verb_table_is_derived():
    """结构面：双字共用动词从 _ZH_SAY_VERBS_JA_SHARED 派生。"""  # noqa: DOCSTRING_CJK
    assert D._ZH_COMPOUND_JA_SHARED_VERBS == tuple(
        v for v in D._ZH_SAY_VERBS_JA_SHARED if len(v) > 1
    )
    assert D._ZH_COMPOUND_JA_SHARED_VERBS == ("討論",)


# ── 56. temper 的语法要和模板一致 / 关于模板的触发词表同源 ────


@pytest.mark.parametrize(
    ("opener", "closer"),
    sorted((lo, hi) for lo, hi in D._ZH_CLOSE_FOR_OPEN.items() if lo.isascii()),
)
@pytest.mark.parametrize("addr", sorted(D._ZH_ADDRESS_VERBS))
def test_address_directives_still_split_through_brackets(opener, closer, addr):
    """temper 漏掉称呼类动词，两条 ``别叫我…`` 指令被并成一条（codex P2，简体回归）。"""  # noqa: DOCSTRING_CJK
    verb = addr.replace("?", "")
    text = f"别{verb}价格{opener}预算.别{verb}收入{closer}目标."
    assert _zh_terms(text) == {f"价格{opener}预算", f"收入{closer}目标"}, text


# ⚠️ ``分别`` 刻意不在这里：``分`` 不在 _BIE_COMPOUND_LEFT 里，那是**模板自己**的
# 左邻表，parent 上同样把 ``分别讨论`` 当指令。本轮改的是 temper 要和模板一致，
# 不是去改模板的判据。
@pytest.mark.parametrize("word", ["个别讨论", "告别演说", "休息"])
def test_compound_words_are_not_mistaken_for_a_directive(word):
    """⚠️ temper 里的否定词必须是**加了护**的那份。

    裸 ``别`` 会把 ``个别讨论`` 这类复合词看成一条指令，括号就护不住内部标点，
    ``电影(个别讨论; 2020)别提了。`` 整条退回 ``2020``（codex P2，简体回归）。
    """  # noqa: DOCSTRING_CJK
    text = f"电影({word}; 2020)别提了。"
    assert _zh_terms(text) == {f"电影({word}; 2020)"}, text


def test_the_temper_grammar_matches_the_templates():
    """结构面：temper 用的是加了护的否定词表和完整动词表（含称呼类）。"""  # noqa: DOCSTRING_CJK
    for neg in D._ZH_NEG_SINGLES_GUARDED + D._ZH_NEG_MULTIS:
        assert neg in D._ZH_DIRECTIVE_AHEAD, neg
    assert D._ZH_VERBS_WITH_ADDRESS in D._ZH_DIRECTIVE_AHEAD
    # 裸的单字否定词不许直接出现——那正是复合词被误判的原因。
    assert '"|".join(_ZH_NEG_SINGLES ' not in D._ZH_DIRECTIVE_AHEAD


@pytest.mark.parametrize("verb", sorted(D._ZH_PREPOSED_SAY_VERBS))
@pytest.mark.parametrize(
    ("prefix", "expected"),
    [("關於工作就別", "工作"), ("关于工作就别", "工作"),
     ("工作別", "工作"), ("工作别", "工作")],
)
def test_the_guanyu_template_shares_the_preposed_trigger_table(verb, prefix, expected):
    """``关于`` 那条专用模板的触发词表原先还写死着，于是 ``關於工作就別提起了。``
    退回通用模板、把填充词 ``就`` 一起存下来（codex P2，简体同样）。"""  # noqa: DOCSTRING_CJK
    text = f"{prefix}{verb}了。"
    assert expected in _zh_terms(text), (text, _zh_terms(text))
