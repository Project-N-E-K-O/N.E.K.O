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
   writes ``説`` U+8AAC). The kana guard is what keeps this closed.
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


def _zh_terms_without_kana_guard(text: str) -> set[str]:
    """``extract_directives``'s zh loop with the kana guard lifted.

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
TW_VERBS = ("說", "提", "聊", "講", "談", "討論")
CN_NEGATIONS = ("别", "不要", "不许", "莫", "甭", "休")
CN_VERBS = ("说", "提", "聊", "讲", "谈", "讨论")
TW_OBJECTS = ("工作", "前男友", "我的體重")
CN_OBJECTS = ("工作", "前男友", "我的体重")


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
    assert obj in _zh_terms(f"{negation}{verb}{obj}。"), (
        f"繁中 {negation}{verb}{obj} 抽不到 term"
    )


@pytest.mark.parametrize("obj", CN_OBJECTS)
@pytest.mark.parametrize("verb", CN_VERBS)
@pytest.mark.parametrize("negation", CN_NEGATIONS)
def test_simplified_imperative_still_extracts_the_object(negation, verb, obj):
    """The merge must not cost the Simplified side anything."""
    assert obj in _zh_terms(f"{negation}{verb}{obj}。")


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
    """对偶性：同一句话的两种字形抽到对应的 term，一侧改坏另一侧就露馅。"""
    tw_expected, cn_expected = expected
    assert tw_expected in _zh_terms(tw), f"繁体 {tw!r} 抽不到 {tw_expected!r}"
    assert cn_expected in _zh_terms(cn), f"简体 {cn!r} 抽不到 {cn_expected!r}"


# ── 3. 日文不碰撞 ────────────────────────────────────────────
# 只有假名守卫拦得住的样本 —— 每一条在守卫拿掉后都真的会被 zh 模板抓出 term
# （下面的 premise 断言就是这么验的），所以这张表不会悄悄退化成一堆无关句子而全绿。
# ``特別講演について``、``今日は休講です`` 之类由 _BIE_COMPOUND_LEFT / 休 词首规则
# 先挡下，放在 JAPANESE_BLOCKED_ELSEWHERE 里另测。
JAPANESE_KANA_GUARDED = [
    "個別提案をお願いします。",
    "地域別講座の一覧。",
    "休講のお知らせ",
    "年齢別講座の案内です。",
    "職種別談話会のお知らせ",
    "個別談話をお願いします。",
    "休講だそうです。",
    "個別講座に申し込みました。",
]
JAPANESE_BLOCKED_ELSEWHERE = [
    "今日は休講です。",
    "特別講演について話しましょう。",
    "特別提供の商品です。",
    "特別講座に申し込んだ。",
    "特別談話を発表した。",
    "個別に提案します。",
    "部門別の説明会に出ます。",
]


@pytest.mark.parametrize("text", JAPANESE_KANA_GUARDED)
def test_kana_guard_is_what_stops_this_sample(text):
    """Premise: lift the guard and the sample really does get extracted.

    Without this the corpus below could silently degrade into sentences the
    templates never matched in the first place, and stay green.
    """
    assert _zh_terms_without_kana_guard(text), (
        f"{text!r} 没有假名守卫也不会命中，这条样本证明不了守卫在干活"
    )


@pytest.mark.parametrize(
    "text", JAPANESE_KANA_GUARDED + JAPANESE_BLOCKED_ELSEWHERE,
)
def test_japanese_text_is_not_extracted_by_the_zh_templates(text):
    assert _zh_terms(text) == set(), f"日文 {text!r} 被 zh 模板抓成 ban_topic"


def test_japanese_ban_topic_still_works():
    """The kana guard is zh-scoped; wiring it to every locale would silence ja
    entirely, since every ja template *requires* kana."""
    hits = extract_directives("仕事のことはもう言わないで")
    assert any(locale == "ja" for locale, _kind, _term in hits), hits


def test_chinese_directive_survives_a_stray_kana_elsewhere_in_the_message():
    """The guard looks at the matched span, not the whole message — a Chinese
    sentence that merely mentions something Japanese keeps its directive."""
    assert "工作" in _zh_terms("剛看完 ドラえもん。別提工作。")


# ── 4. 复合词左界守卫 ────────────────────────────────────────
def test_compound_left_set_is_pinned():
    """闭集断言用相等：这张表里每个字都是一句"该字后面的 别 一定不是祈使"的主张，
    加字要先确认没有自然反例（"这个别提了" 就是 个 的反例）。"""  # noqa: DOCSTRING_CJK
    assert D._BIE_COMPOUND_LEFT == "特性区區级級"


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


@pytest.mark.parametrize("text", ["休提舊事。", "休提旧事。"])
def test_xiu_at_a_word_start_is_still_a_negation(text):
    """守卫是词首规则，不是把 休 从表里删掉。"""  # noqa: DOCSTRING_CJK
    assert _zh_terms(text), f"{text!r} 应当仍然命中"


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
