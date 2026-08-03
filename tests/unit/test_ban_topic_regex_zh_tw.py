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
    """对偶性：同一句话的两种字形抽到对应的 term，一侧改坏另一侧就露馅。"""  # noqa: DOCSTRING_CJK
    tw_expected, cn_expected = expected
    assert tw_expected in _zh_terms(tw), f"繁体 {tw!r} 抽不到 {tw_expected!r}"
    assert cn_expected in _zh_terms(cn), f"简体 {cn!r} 抽不到 {cn_expected!r}"


# ── 3. 日文不碰撞 ────────────────────────────────────────────
# 只有 _is_japanese_sentence_match 拦得住的样本 —— 每一条在守卫拿掉后都真的会被 zh
# 模板抓出 term（下面的 premise 断言就是这么验的），所以这张表不会悄悄退化成一堆无关
# 句子而全绿。``特別講演について``、``今日は休講です`` 之类由 _BIE_COMPOUND_LEFT /
# 休 词首规则先挡下，放在 JAPANESE_BLOCKED_ELSEWHERE 里另测。
JAPANESE_KANA_GUARDED = [
    "個別提案をお願いします。",
    "地域別講座の一覧。",
    "休講のお知らせ",
    "年齢別講座の案内です。",
    "職種別談話会のお知らせ",
    "個別談話をお願いします。",
    "休講だそうです。",
    "個別講座に申し込みました。",
    # 助词表按闭集补全之前漏的（codex P2）：只列 のにをはがでと 时这些都会漏出去
    "個別提案ください。",
    "地域別講座へ申込。",
    "個別提案から選択。",
    "個別提案など検討。",
    "地域別講座まで案内。",
    "個別談話でも可。",
    "個別提案だけ確認。",
    "地域別講座について質問。",
    # 接续助词：这两条的 term 里**没有**单字格助词，只有 けど / たら 拦得住
    "個別提案したけど。",
    "個別提案したら連絡。",
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
def test_the_japanese_guard_is_what_stops_this_sample(text):
    """Premise: lift the guard and the sample really does get extracted.

    Without this the corpus below could silently degrade into sentences the
    templates never matched in the first place, and stay green.
    """
    assert _zh_terms_without_japanese_guard(text), (
        f"{text!r} 没有日文守卫也不会命中，这条样本证明不了守卫在干活"
    )


@pytest.mark.parametrize(
    "text", JAPANESE_KANA_GUARDED + JAPANESE_BLOCKED_ELSEWHERE,
)
def test_japanese_text_is_not_extracted_by_the_zh_templates(text):
    assert _zh_terms(text) == set(), f"日文 {text!r} 被 zh 模板抓成 ban_topic"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("仕事のことはもう言わないで", "仕事"),
        # ⚠️ term 自带助词的 ja 命中才压得住 zh_family 这道作用域：守卫的判据是
        # "没有中文证据 + term 含助词"，对一条 ja 命中永远成立，套上去就把 ja 自己
        # 否掉了。term 不含假名的样本（"仕事"）证明不了这一点。
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


@pytest.mark.parametrize("text", ["休提舊事。", "休提旧事。"])
def test_xiu_at_a_word_start_is_still_a_negation(text):
    """守卫是词首规则，不是把 休 从表里删掉。"""  # noqa: DOCSTRING_CJK
    assert _zh_terms(text), f"{text!r} 应当仍然命中"


# ── 5b. 台湾句末助词不能粘在 term 上 ─────────────────────────
# 存进 user_directives 的是 term 本身，会逐字注进 system prompt。助词粘上去
# ("工作喔") 就是把一个不存在的话题名喂给模型（codex P2）。
TAIWANESE_FINAL_PARTICLES = ("喔", "囉", "啰", "唄", "唷", "齁")


@pytest.mark.parametrize("particle", TAIWANESE_FINAL_PARTICLES)
def test_taiwanese_final_particle_is_stripped_from_the_term(particle):
    assert _zh_terms(f"別再提工作{particle}") == {"工作"}


@pytest.mark.parametrize("particle", TAIWANESE_FINAL_PARTICLES)
def test_particle_is_declared_in_both_places(particle):
    """放行（正则）与剥离（trim）必须成对：少一边 term 就带着助词存进去。"""  # noqa: DOCSTRING_CJK
    assert particle in D._TRIM_TRAIL_TOKENS, f"{particle} 不在 _TRIM_TRAIL_TOKENS"
    assert particle in D._ZH_FINAL_PARTICLES, f"{particle} 不在 _ZH_FINAL_PARTICLES"


def test_stacked_particles_are_all_stripped():
    assert _zh_terms("不要再說這件事了喔") == {"這件事"}


# ── 5c. 关于 X 只产出一条 term ───────────────────────────────
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("關於股票就別再講了", "股票"),
        ("关于股票就别再讲了", "股票"),
        ("關於前女友的事就別提了", "前女友"),
        ("关于前女友的事就别提了", "前女友"),
        ("關於股票別再講了", "股票"),
    ],
)
def test_guanyu_produces_exactly_one_term(text, expected):
    """通用的 ``X + 别提`` 模板会把 "关于股票就" 整段当话题，和专用模板的 "股票"
    一起存下来。垃圾那条同样占一个 active 名额、注入三天。"""  # noqa: DOCSTRING_CJK
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
