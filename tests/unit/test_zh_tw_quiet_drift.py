"""Traditional coverage for the "quiet drift" tables (issue #2500).

Unlike the earlier batches these do not fail loudly. Nothing crashes, nothing
returns zero matches — the numbers just come out wrong for a Traditional writer:

* topic stop-chars: Traditional function words survive into the similarity
  units, so every Traditional topic shares 這/個/還/嗎 and scores as more
  similar to every other one than it should;
* question particles: a Traditional question never opens the follow-up window;
* vague markers: a Traditional "繼續處理一下" is not recognised as needing
  context, so the agent gets a task description with no referent;
* holiday hints: four tables that must be backfilled together or the locale
  selection picks zh-TW off one table and then misses on the others.
"""  # noqa: DOCSTRING_CJK
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# main_logic/topic/common.py — stop chars feeding topic similarity
# ---------------------------------------------------------------------------

# Function words that must never survive into the similarity units, paired.
STOP_CHAR_PAIRS = [("与", "與"), ("着", "著"), ("吗", "嗎"), ("还", "還"), ("这", "這")]


@pytest.mark.parametrize(("simplified", "traditional"), STOP_CHAR_PAIRS)
def test_topic_stop_chars_cover_both_scripts(simplified, traditional):
    from main_logic.topic.common import ZH_TOPIC_STOP_CHARS

    assert simplified in ZH_TOPIC_STOP_CHARS
    assert traditional in ZH_TOPIC_STOP_CHARS, (
        f"{traditional!r} 不在停用字表里，繁中话题会把它当成有意义的相似度单元"
    )


@pytest.mark.parametrize(
    ("simplified", "traditional"), [("与", "與"), ("着", "著"), ("吗", "嗎"), ("还", "還")]
)
def test_link_stop_chars_cover_both_scripts(simplified, traditional):
    """⚠️ This table deliberately excludes demonstratives (这/那/我/你…), so only
    the function-word half is asserted here — 這 belongs in the topic table but
    not this one."""  # noqa: DOCSTRING_CJK
    from main_logic.topic.common import ZH_LINK_STOP_CHARS

    assert simplified in ZH_LINK_STOP_CHARS
    assert traditional in ZH_LINK_STOP_CHARS


def test_traditional_topic_does_not_carry_function_word_units():
    """The observable symptom, not just the table contents.

    Before the backfill 「這個遊戲還在更新嗎」 produced 15 units against the
    Simplified sentence's 9 — the extra six were pure function words, and every
    other Traditional topic carried the same six, inflating pairwise similarity
    across the board.
    """  # noqa: DOCSTRING_CJK
    from main_logic.topic.common import topic_units

    simplified = topic_units("这个游戏还在更新吗")
    traditional = topic_units("這個遊戲還在更新嗎")
    assert len(traditional) == len(simplified), (
        f"繁体切出 {len(traditional)} 个单元、简体 {len(simplified)} 个：{sorted(traditional)}"
    )
    # ⚠️ 不断言 個：个/個 在**两侧都不是**停用字（既有取舍，对称），拿它做判据
    # 会把一个非 zh-TW 问题记到本批头上。
    for junk in ("這", "嗎", "還", "與", "著"):
        assert junk not in topic_units(f"聊聊{junk}這件事"), f"虚词 {junk!r} 混进了相似度单元"


# ---------------------------------------------------------------------------
# main_logic/activity/state_machine.py — open-question detection
# ---------------------------------------------------------------------------

QUESTION_PAIRS = [
    ("你今天过得还好吗", "你今天過得還好嗎"),
    ("要不要一起看电影呢", "要不要一起看電影呢"),
    ("这样好吧", "這樣好吧"),
]


@pytest.mark.parametrize(("simplified", "traditional"), QUESTION_PAIRS)
def test_open_question_detected_in_both_scripts(simplified, traditional):
    from main_logic.activity.state_machine import _text_has_open_question

    assert _text_has_open_question(simplified) is True
    assert _text_has_open_question(traditional) is True


@pytest.mark.parametrize(
    "text",
    [
        # ⚠️ 麼 is deliberately NOT a particle: the check looks at the last
        # character, and 什麼/怎麼 are extremely common non-final uses.
        "我不知道該說什麼",
        "今天天氣很好",
        "今天天气很好",
    ],
)
def test_statements_are_not_treated_as_questions(text):
    from main_logic.activity.state_machine import _text_has_open_question

    assert _text_has_open_question(text) is False


def test_the_simplified_me_particle_is_a_known_pre_existing_false_positive():
    """Recorded, not fixed, and deliberately asymmetric.

    「我不知道该说什么」 ends in 么, which *is* in the particle table, so the
    Simplified side reports an open question here and always has. The
    Traditional equivalent ends in 麼, which is not in the table — so adding 麼
    "for symmetry" would import the bug rather than remove it.

    Fixing it means dropping 么 from the table, which changes Simplified
    behaviour; the function's own docstring says false positives are tolerable,
    so that is a separate call and not part of a zh-TW batch.
    """  # noqa: DOCSTRING_CJK
    from main_logic.activity.state_machine import _text_has_open_question

    assert _text_has_open_question("我不知道该说什么") is True   # 既有假阳性
    assert _text_has_open_question("我不知道該說什麼") is False  # 繁体侧正确


def test_the_particle_table_does_not_include_the_trailing_me_character():
    """Pins the exclusion, so a future "let's be thorough" edit goes red."""
    from main_logic.activity.state_machine import _CN_QUESTION_PARTICLES

    assert "嗎" in _CN_QUESTION_PARTICLES
    assert "麼" not in _CN_QUESTION_PARTICLES, (
        "收了「麼」会把「我不知道該說什麼」误判成问句"
    )


# ---------------------------------------------------------------------------
# brain/task_executor.py — vague-reference detection
# ---------------------------------------------------------------------------

VAGUE_PAIRS = [
    ("继续处理一下", "繼續處理一下"),
    ("帮我弄一下", "幫我弄一下"),
    ("刚才那个", "剛才那個"),
    ("打开这个", "打開這個"),
    ("继续那个", "繼續那個"),
    ("上一条", "上一條"),
    ("接着做", "接著做"),
    ("发给他", "發給他"),
]


def _needs_context(latest: str) -> bool:
    """Whether the executor decides to splice earlier turns in as context.

    Driven through the real entry point rather than the marker tuple, so the
    length-threshold fallback (short CJK turns are vague regardless) is part of
    what is being asserted — a marker-only check would pass on短 inputs that
    never consult the table.
    """  # noqa: DOCSTRING_CJK
    from brain.task_executor import DirectTaskExecutor

    history = [
        {"role": "user", "content": "帮我把上周的会议纪要整理成邮件"},
        {"role": "assistant", "content": "好的"},
        {"role": "user", "content": latest},
    ]
    # Bound method on the class, but its body never touches ``self`` — calling
    # it unbound avoids constructing an executor (which would want config/IO).
    return DirectTaskExecutor._normalize_user_intent(None, latest, history) != latest


@pytest.mark.parametrize(("simplified", "traditional"), VAGUE_PAIRS)
def test_vague_references_are_recognised_in_both_scripts(simplified, traditional):
    assert _needs_context(simplified) == _needs_context(traditional)


# Simplified -> Traditional for exactly the characters this table uses.
_VAGUE_CHAR_MAP = str.maketrans({
    "这": "這", "个": "個", "继": "繼", "续": "續", "处": "處", "帮": "幫",
    "刚": "剛", "条": "條", "发": "發", "给": "給", "开": "開", "着": "著",
    "她": "她", "它": "它",
})


def test_every_simplified_marker_has_a_traditional_sibling():
    """Auto-discovered from the table, so deleting any single Traditional entry
    goes red.

    ⚠️ Hand-written sentences could not do this: 「繼續處理一下」 contains 一下,
    which is spelled the same in both scripts, so it keeps matching even with
    every Traditional entry removed. My first version of this test passed the
    mutation for exactly that reason.
    """  # noqa: DOCSTRING_CJK
    from brain.task_executor import VAGUE_REFERENCE_MARKERS

    present = set(VAGUE_REFERENCE_MARKERS)
    missing = []
    converted_any = False
    for marker in VAGUE_REFERENCE_MARKERS:
        if not any("一" <= ch <= "鿿" for ch in marker):
            continue  # latin / kana / hangul row
        traditional = marker.translate(_VAGUE_CHAR_MAP)
        if traditional == marker:
            continue  # identical in both scripts
        converted_any = True
        if traditional not in present:
            missing.append((marker, traditional))
    assert converted_any, "字符映射没转出任何东西，用例已失效"
    assert not missing, f"缺繁体对应条目：{missing}"


@pytest.mark.parametrize("marker", ["上一條", "接著做", "發給他", "繼續做", "剛才那個"])
def test_traditional_only_markers_are_reachable(marker):
    """Each of these is Traditional-only *and* free of any script-neutral
    marker, so it is genuinely pinned by the table rather than by 一下/那個."""  # noqa: DOCSTRING_CJK
    # Padded past the 3-char length fallback with text carrying no marker.
    assert _needs_context(f"請你{marker}謝謝") is True


# ---------------------------------------------------------------------------
# prompts_proactive holiday hints — four tables, one shared locale key
# ---------------------------------------------------------------------------

HOLIDAY_TABLES = ["HOLIDAY_HINT_TODAY", "HOLIDAY_HINT_SOON", "HOLIDAY_HINT_WEEK", "WEEKEND_HINT"]


@pytest.mark.parametrize("table_name", HOLIDAY_TABLES)
def test_every_holiday_table_has_the_traditional_key(table_name):
    """⚠️ Half-filling these is worse than not filling them.

    ``_holiday_hint_language_key`` derives the locale key from
    ``HOLIDAY_HINT_TODAY`` alone and then indexes the other three with it. Add
    zh-TW to TODAY only and a Traditional user gets a Traditional "today"
    line and English for "soon"/"this week"/"weekend".
    """  # noqa: DOCSTRING_CJK
    from config.prompts import prompts_proactive

    table = getattr(prompts_proactive, table_name)
    assert "zh-TW" in table, f"{table_name} 缺 zh-TW，会让繁中用户在这一句上掉英文"
    assert table["zh-TW"] != table["zh"], f"{table_name} 的 zh-TW 是 zh 的拷贝"


def test_all_four_holiday_tables_resolve_together_for_traditional():
    """Drives the real key selection, which is where the trap lives."""
    from config.prompts import prompts_proactive as P
    from utils.holiday_cache import _holiday_hint_language_key

    key = _holiday_hint_language_key("zh-TW", P.HOLIDAY_HINT_TODAY)
    assert key == "zh-TW"
    for table_name in HOLIDAY_TABLES:
        table = getattr(P, table_name)
        assert key in table, f"{table_name} 取不到 {key}，会 fallback 到英文"
        leaked = sorted({ch for ch in "这周节过松别样" if ch in table[key]})
        assert not leaked, f"{table_name} 的 zh-TW 里混进了简体字：{leaked}"


def test_simplified_holiday_hints_are_unchanged():
    from config.prompts import prompts_proactive as P
    from utils.holiday_cache import _holiday_hint_language_key

    for lang in ("zh", "zh-CN"):
        assert _holiday_hint_language_key(lang, P.HOLIDAY_HINT_TODAY) == "zh"
    assert P.WEEKEND_HINT["zh"] == "今天是周末，好好放松吧。"
