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
    ("我不想聊昨天發生的事", "我不想聊昨天发生的事", ("昨天發生", "昨天发生")),
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
    ("世代別講座ガイド。", {"座ガイド"}),
    ("商品別提案プラン。", {"案プラン"}),
    ("部門別提案リスト", {"案リスト"}),
    ("地域別提案書。", {"案書"}),
]


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
    """Premise: 这些样本命中区间里确实有假名，所以它们真的会走到守卫判据，
    而不是因为压根没假名才侥幸通过。"""  # noqa: DOCSTRING_CJK
    assert D._KANA_RE.search(text), f"{text!r} 没有假名，证明不了守卫放行"


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


def test_no_zh_evidence_glyph_appears_in_the_japanese_corpus():
    """⚠️ 自动发现，不是逐字审：中文证据表里的字**一旦出现在日文语料里**，就说明
    它不是"日文里不存在"的字，而这张表是用来 short-circuit 日文守卫的——收错一个字
    等于把守卫整个关掉。``没``（没収）和 ``称``（名称）就是这么漏进来的。

    这条随日文语料一起长：以后往语料里加句子，收错的字会被自动抓出来。
    """  # noqa: DOCSTRING_CJK
    charclass = D._ZH_EVIDENCE_RE.pattern.split("]", 1)[0].lstrip("[")
    corpus = "".join(
        JAPANESE_KANA_GUARDED + JAPANESE_KANA_PREFIXED + JAPANESE_BLOCKED_ELSEWHERE
    )
    offenders = sorted({ch for ch in charclass if ch in corpus})
    assert not offenders, (
        f"这些字既在中文证据表里、又出现在日文语料里：{offenders}"
    )


def test_zh_evidence_charclass_is_pinned():
    """闭集用相等断言：每个字都是一句"日文里不存在这个字形"的主张，加字要先核对
    日文新字体（别→別 说→説 关→関 为→為…）。"""  # noqa: DOCSTRING_CJK
    charclass = D._ZH_EVIDENCE_RE.pattern.split("]", 1)[0].lstrip("[")
    assert charclass == "别说讲谈讨论关这话题愿懒许为甭說這關沒稱"


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
TAIWANESE_FINAL_PARTICLES = (
    "喔", "囉", "啰", "唷", "齁", "欸", "誒", "咧", "喲",
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
        ("电影(Hello, World)别提了。", "电影(Hello, World"),
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
        ('"', '"'), ("(", ")"),
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
        ("电影《你好，李焕英》别提了。", "电影《你好，李焕英"),
        ("電影《你好，李煥英》別提了。", "電影《你好，李煥英"),
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
    assert D._ZH_COMPLEMENT_CHARS == ("到", "起", "及")
    for _loc, _kind, pat in D.DIRECTIVE_PATTERNS:
        if _loc == "zh":
            assert "{1," not in pat.pattern, pat.pattern[:80]


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
    import inspect

    source = inspect.getsource(D._drop_filler_suffixed_terms)
    # 第二层：即使有一条带填充词的 term（早退不触发），也只让**它**去做重叠比对，
    # 而不是全部 n 条。实测 800 条指令 + 1 条填充词：24.6ms → 74.4ms（3x）。
    # 差距太小、不适合用时限钉，所以这里钉结构。
    assert "index not in suspect_set" in source, (
        "逐条跳过没了：有一条填充词 term 时，全部命中都会做 O(n) 的重叠比对"
    )
    # ⚠️ 光筛 suspects 不够：话题本身就以填充词结尾时（`成就别提了。` 重复几千遍）
    # 每条都是 suspect，逐条再扫全表又变回 O(n²)——4000 条曾要 1.25 秒。按起点分桶
    # 之后只看邻桶（命中区间长度有上界），这里用时限钉住那个量级差（codex P2）。
    import time

    started = time.perf_counter()
    extract_directives("成就别提了。" * 4000)
    elapsed = time.perf_counter() - started
    assert elapsed < 1.0, f"4000 条以填充词结尾的话题跑了 {elapsed:.2f}s，桶索引失效了"
    assert "_span_buckets" in inspect.getsource(D._drop_filler_suffixed_terms)

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
        ("關於前女友的事就別提了", "前女友"),
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
        # ``关于`` 出现在句中时，通用模板的前缀不能逐字吃过它——否则多产出一条
        # ``我觉得关于股票就``（codex P2）。单字分支 temper 掉裸的 关于，括号分支
        # 排在它前面，所以书名里的 关于 仍然整体放行。
        ("我觉得关于股票就别再讲了", "股票"),
        ("我覺得關於股票就別再講了", "股票"),
        ("其实关于工作别提了", "工作"),
    ],
)
def test_a_leading_clause_before_guanyu_does_not_add_a_bogus_term(text, expected):
    assert _zh_terms(text) == {expected}


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


def test_the_verb_table_has_no_complement_cartesian_product():
    """补语族一旦回到动词表，上面那批话题就会被削掉首字。"""  # noqa: DOCSTRING_CJK
    verbs = D._ZH_VERBS_PLAIN
    for verb in D._ZH_SAY_VERBS:
        for complement in ("到", "起", "及"):
            assert f"|{verb}{complement}|" not in verbs, f"{verb}{complement}"


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
        ("别再提电影《我们好不好》。", "电影《我们好不好"),
        ("別再提電影《我們好不好》。", "電影《我們好不好"),
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
    assert D._ZH_BRACKET_CHARS == frozenset(
        ch for pair in D._ZH_BRACKET_PAIRS for ch in pair
    )


def test_interrogative_tails_are_a_separate_table_from_the_particles():
    """混进助词表就没法按括号分别对待了。"""  # noqa: DOCSTRING_CJK
    assert D._TAIL_INTERROGATIVES_BY_LOCALE["zh"] == (
        "好吗", "好嗎", "好不好", "可以吗", "可以嗎", "行吗", "行嗎",
    )
    # zh 那张助词表必须只剩单字，否则多字反问短语会绕过括号判据被无条件剥掉。
    for tok in D._TRIM_TRAIL_TOKENS_BY_LOCALE["zh"]:
        assert len(tok) == 1, tok




# ── 9. 括号段有界 + 对称引号不跨句 ──────────────────────────
def test_bracket_bodies_are_bounded():
    """无界的 ``*`` 在每个开括号处都会扫到串尾，是二次方（codex P2）。"""  # noqa: DOCSTRING_CJK
    assert "]*" not in D._ZH_BRACKET_RUN, D._ZH_BRACKET_RUN
    assert f"{{0,{D._TERM_MAX_LEN}}}" in D._ZH_BRACKET_RUN


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
    assert _zh_terms("电影《你好，李焕英》别提了。") == {"电影《你好，李焕英"}
    assert _zh_terms("電影《你好，李煥英》別提了。") == {"電影《你好，李煥英"}


# ── 10. 关于 的排除只属于前置话题 ────────────────────────────
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("别再提关于公司的传闻。", "关于公司的传闻"),
        ("別再提關於公司的傳聞。", "關於公司的傳聞"),
        ("我不想聊关于钱的事", "关于钱"),
        ("我不想聊關於錢的事", "關於錢"),
    ],
)
def test_an_object_may_begin_with_guanyu(text, expected):
    """⚠️ 排除 ``关于`` 是模板 2 **前置话题**的守卫，放进共用单字分支会把动宾结构的
    宾语一起毙掉（codex P2）。前置话题与动词后宾语是两种结构。
    """  # noqa: DOCSTRING_CJK
    assert expected in _zh_terms(text), _zh_terms(text)


@pytest.mark.parametrize(
    "text",
    ["我觉得关于股票就别再讲了", "我覺得關於股票就別再講了", "其实关于工作别提了"],
)
def test_the_preposed_guard_still_suppresses_the_junk_prefix(text):
    terms = _zh_terms(text)
    assert not any("关于" in t or "關於" in t for t in terms), terms


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
    """  # noqa: DOCSTRING_CJK
    assert _zh_terms(f"{negation}提君の名は。") == {"君の名は"}


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
    assert D._ZH_IDENT_PUNCT.startswith("(?<=[0-9A-Za-z])")
    assert D._ZH_IDENT_PUNCT.endswith("(?=[0-9A-Za-z])")
    for unit in (D._ZH_TOPIC_CHAR, D._ZH_TOPIC_CHAR_NO_GUANYU):
        assert D._ZH_IDENT_PUNCT in unit


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
ZH_ONLY_SUBJECTS = ("你", "妳", "您", "咱", "请")


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
    """  # noqa: DOCSTRING_CJK
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
    assert "(?![别別])" in D._ZH_BRACKET_RUN
    assert D._ZH_BRACKET_RUN.count("(?![别別])") == sum(
        1 for lo, hi in D._ZH_BRACKET_PAIRS if lo == hi
    )
    assert _zh_terms("电影(Hello, World)别提了。") == {"电影(Hello, World"}


# ── 17. 反问尾巴落在引号之外时该剥 ───────────────────────────
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # 尾巴在收尾括号**之后** = 句子级语气，剥
        ("別再提電影《你好》好嗎。", "電影《你好"),
        ("别再提电影《你好》好吗。", "电影《你好"),
        ("别再提《你的名字》好吗。", "你的名字"),
        ("別再提《你的名字》好嗎。", "你的名字"),
        ("别再提《你的名字》好不好。", "你的名字"),
        ('别再提"你的名字"好吗。', "你的名字"),
        # 尾巴在括号**里面** = 名字的一部分，不剥
        ("别再提《最近你好吗》。", "最近你好吗"),
        ("別再提《最近你好嗎》。", "最近你好嗎"),
        ("别再提电影《我们好不好》。", "电影《我们好不好"),
        ("别再提《你可以吗》。", "你可以吗"),
        # ⚠️ 前缀以**开括号**结束 = 尾巴仍在书名里，不能剥（判据必须是收尾括号，
        # 换成「任意括号字符」这三条就会被削成 电影 / 剧集，标题整个丢掉）
        ("别再提电影《好不好》。", "电影《好不好"),
        ("別再提電影《好不好》。", "電影《好不好"),
        ("别再提剧集《可以吗》。", "剧集《可以吗"),
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
    assert closers <= D._ZH_BRACKET_CHARS
    assert "》" in closers and "《" not in closers
