"""Traditional-Chinese coverage for the emotion subsystem (issue #2500).

Two independent mechanisms are exercised here, and they fail differently:

* The heuristic keyword tables are flattened across *all* languages and matched as
  substrings against whatever the user typed, so a Traditional writer needs the
  Traditional forms present. No locale plumbing is involved — the table either has
  the characters or it does not.
* The two LLM prompt templates are dispatched by ``_loc``, which falls back to
  ``en`` on a missing key. Adding a ``zh-TW`` template is only half the fix: the
  call sites derive the locale from ``detect_language``, which cannot tell the two
  orthographies apart, so without ``detect_prompt_language`` the template stays
  unreachable and the tests below would pass on a template nobody ever sees.
"""
from __future__ import annotations

import pytest

from config.prompts import prompts_emotion as P
from utils.language_utils import detect_prompt_language, language_context

TRADITIONAL_ONLY = "開興歡愛難傷嗚遺喪負氣煩惱惡會這麼貼嬌並僅過驚憤靜裡閉憐別沒來"
# 有意不含 `里`：它在两种写法里都是正字（公里／里長），拿它当简体标记会把将来
# 合法的繁体词条判成违规。`裡` 只在繁体侧出现，所以只放在上面那一串里。
SIMPLIFIED_ONLY = "开兴欢爱难伤呜遗丧负气烦恼恶会这么贴娇并仅过惊愤静闭怜别没来"

def _chinese_tables():
    """Every `*_BY_LANG` table in the module that has a Chinese block.

    Discovered rather than listed: a hand-written list only covers the tables
    that existed when it was written, which is how 320 prompt dicts came to be
    missing zh-TW in the first place. Tables with no `zh` block (a Korean-only
    phenomenon, say) are not in scope and drop out on their own.
    """
    found = []
    for name in dir(P):
        if not name.endswith("_BY_LANG"):
            continue
        table = getattr(P, name)
        if isinstance(table, dict) and "zh" in table:
            found.append(name)
    return sorted(found)


FLAT_TABLES = _chinese_tables()


def _entries(block):
    """Every string in a language block, whether it is a tuple or a dict."""
    if isinstance(block, dict):
        return list(block.keys()) + [
            word for value in block.values()
            for word in (value if isinstance(value, tuple) else ())
        ]
    return list(block)


@pytest.mark.parametrize("name", FLAT_TABLES)
def test_every_chinese_table_has_a_traditional_block(name):
    """Discovered from the module, not from a hand-written list.

    A checklist here would silently stop covering a table added later — which is
    exactly how 320 of these tables came to be missing zh-TW in the first place.
    """
    table = getattr(P, name)
    assert "zh" in table, f"{name} 没有 zh block，本用例的前提不成立"
    assert "zh-TW" in table, f"{name} 缺 zh-TW，繁中输入在这张表上一个字都匹配不到"


@pytest.mark.parametrize("name", FLAT_TABLES)
def test_traditional_block_pairs_with_the_simplified_one(name):
    """Same shape, same entry count — so a later edit to one side is visibly odd."""
    table = getattr(P, name)
    zh, tw = table["zh"], table["zh-TW"]
    assert type(zh) is type(tw)
    if isinstance(zh, dict):
        assert zh.keys() == tw.keys() or len(zh) == len(tw)
        for emotion in zh:
            if isinstance(zh[emotion], tuple):
                assert len(zh[emotion]) == len(tw[emotion]), f"{name}[{emotion}] 条数不等"
    else:
        assert len(zh) == len(tw), f"{name} 两侧条数不等"


@pytest.mark.parametrize("name", FLAT_TABLES)
def test_traditional_block_is_not_a_copy_of_the_simplified_one(name):
    """Guards against a block added to satisfy the gate without being converted."""
    table = getattr(P, name)
    zh, tw = _entries(table["zh"]), _entries(table["zh-TW"])
    assert zh != tw, f"{name} 的 zh-TW 与 zh 逐字相同，等于没加"
    assert any(ch in TRADITIONAL_ONLY for entry in tw for ch in entry), (
        f"{name} 的 zh-TW 里没有任何繁体专用字"
    )


@pytest.mark.parametrize("name", FLAT_TABLES)
def test_traditional_block_carries_no_simplified_only_characters(name):
    table = getattr(P, name)
    offenders = [
        entry for entry in _entries(table["zh-TW"])
        if any(ch in SIMPLIFIED_ONLY for ch in entry)
    ]
    assert not offenders, f"{name} 的 zh-TW 里混进了简体字：{offenders}"


@pytest.mark.parametrize("text,emotion,word", [
    ("我今天好開心", "happy", "開心"),
    ("有點難過", "sad", "難過"),
    ("氣死我了", "angry", "氣死"),
    ("不會吧怎麼會這樣", "surprised", "不會吧"),
])
def test_traditional_text_scores_on_the_flattened_keyword_table(text, emotion, word):
    """The tables are matched against the user's text, not against a locale."""
    flat = P.get_emotion_keywords_flat()
    assert word in flat[emotion]
    assert any(kw in text for kw in flat[emotion])


@pytest.mark.parametrize("getter,flat_name,word", [
    (P.get_angry_attack_patterns_flat, "attack", None, ),
    (P.get_sad_vulnerable_patterns_flat, "vulnerable", None),
    (P.get_happy_playful_patterns_flat, "playful", None),
    (P.get_heuristic_negation_tokens_flat, "negation", None),
    (P.get_heuristic_tight_negation_tokens_flat, "tight", None),
    (P.get_heuristic_negation_blocklist_flat, "blocklist", None),
    (P.get_heuristic_contrast_conjunctions_flat, "contrast", None),
])
def test_flat_helpers_expose_the_traditional_entries(getter, flat_name, word):
    flat = getter()
    assert any(any(ch in TRADITIONAL_ONLY for ch in entry) for entry in flat), (
        f"{flat_name} 拍平后没有任何繁体条目"
    )


def test_tight_negation_set_is_unchanged_by_the_split():
    """Both orthographies of the single-character negations shared one zh block.

    Splitting them by language is a readability change only — the flattened set the
    heuristic actually matches on must come out identical, or an existing negation
    stops being recognized.
    """
    flat = set(P.get_heuristic_tight_negation_tokens_flat())
    assert {"不", "别", "別", "没", "沒", "未", "勿"} <= flat


def test_model_label_in_traditional_normalizes():
    """A model given the Traditional prompt answers in Traditional.

    Without the alias block those labels fall through to neutral, so the whole
    Traditional path would look like it works while always reporting neutral.
    """
    aliases = P.get_emotion_label_aliases_flat()
    for label, canonical in [
        ("開心", "happy"), ("難過", "sad"), ("生氣", "angry"),
        ("驚訝", "surprised"), ("平靜", "neutral"),
    ]:
        assert aliases.get(label) == canonical, f"{label} 没有归一化到 {canonical}"


PROMPT_GETTERS = [
    P.get_outward_emotion_analysis_prompt,
    P.get_master_emotion_va_prompt,
]


@pytest.mark.parametrize("getter", PROMPT_GETTERS)
def test_traditional_prompt_exists_and_is_traditional(getter):
    traditional = getter("zh-TW")
    assert traditional != getter("zh"), "zh-TW 拿到的还是简体模板"
    assert traditional != getter("en"), "zh-TW 掉回了 _loc 的 en 兜底"
    assert any(ch in TRADITIONAL_ONLY for ch in traditional)


@pytest.mark.parametrize("getter", PROMPT_GETTERS)
def test_traditional_prompt_keeps_the_machine_readable_parts_ascii(getter):
    """The response contract is parsed, so its tokens must not be translated."""
    traditional = getter("zh-TW")
    for token in ("JSON", "happy", "neutral", "confidence"):
        if token in getter("zh"):
            assert token in traditional, f"{token} 在繁中模板里被翻掉了"


@pytest.mark.parametrize("getter", PROMPT_GETTERS)
def test_shared_role_prefix_is_identical_in_every_locale(getter):
    """The opening sentence is one literal repeated across all locales.

    It is Simplified even in the en/ja/ko templates, so zh-TW follows suit rather
    than becoming the single locale that diverges.
    """
    prefix = "你是一个情感分析专家。"
    for locale in ("zh", "zh-TW", "en", "ja", "ko", "ru", "es", "pt"):
        assert getter(locale).startswith(prefix), f"{locale} 的开头不是共享前缀"


@pytest.mark.parametrize("getter", PROMPT_GETTERS)
def test_other_locales_are_untouched(getter):
    """Adding a key must not perturb the seven templates that already existed."""
    for locale in ("zh", "en", "ja", "ko", "ru", "es", "pt"):
        assert getter(locale), f"{locale} 模板变空了"
    assert getter("zh") != getter("zh-TW")


@pytest.mark.parametrize("text,ui,expected", [
    ("我今天好開心", "zh-TW", "zh-TW"),
    ("我今天好开心", "zh-TW", "zh-TW"),   # UI is the only signal that separates them
    ("我今天好开心", "zh-CN", "zh"),
    ("hello there", "zh-TW", "en"),      # detection wins outside Chinese
    ("こんにちは、元気ですか", "zh-TW", "ja"),
])
def test_prompt_language_resolution(text, ui, expected):
    with language_context(ui):
        assert detect_prompt_language(text) == expected


def test_prompt_language_falls_back_without_a_ui_locale():
    """No context set: the previous short-code behavior, unchanged."""
    assert detect_prompt_language("我今天好开心") == "zh"


@pytest.mark.parametrize("resolver_path,resolver_name", [
    ("main_routers.system_router.emotion", "_resolve_emotion_prompt_language"),
    ("main_logic.activity.master_emotion", None),
])
def test_call_sites_can_reach_the_traditional_template(resolver_path, resolver_name):
    """The half of the fix that the template tests above cannot see.

    Both prompts are keyed by whatever their call site resolves. While that was
    `normalize_language_code(detect_language(text), format="short")` the zh-TW
    template was dead code, so pinning the resolvers is what keeps it reachable.
    """
    import importlib

    module = importlib.import_module(resolver_path)
    if resolver_name is not None:
        resolve = getattr(module, resolver_name)
    else:
        resolve = module.MasterEmotionTracker._resolve_lang
    with language_context("zh-TW"):
        assert resolve("我今天好開心") == "zh-TW"
    with language_context("zh-CN"):
        assert resolve("我今天好开心") == "zh"


NEGATION_TABLES = [
    "EMOTION_NEGATION_PREFIXES_BY_LANG",
    "EMOTION_NEGATION_WORDS_BY_LANG",
    "EMOTION_NEGATION_SUFFIXES_BY_LANG",
]


@pytest.mark.parametrize("name", NEGATION_TABLES)
def test_negation_tables_live_beside_the_other_language_tables(name):
    """They used to be hardcoded in the router, against its own stated convention."""
    assert hasattr(P, name)


def test_negation_prefixes_cover_traditional():
    """Adding Traditional aliases is what makes this mandatory rather than nice.

    Once a Traditional emotion word is an alias, a negated phrase built on it
    matches that alias as a substring. If the negation prefixes carry only the
    Simplified spellings, the negation is missed and the label comes back as the
    emotion itself — the opposite of what the model said, and worse than before
    the aliases existed. See test_negated_traditional_labels_do_not_invert.
    """
    prefixes = set(P.get_emotion_negation_prefixes_flat())
    for token in ("沒", "沒有", "沒那麼", "並不", "並非", "並沒有", "別", "無"):
        assert token in prefixes, f"缺繁体否定前缀 {token}"


def test_negation_flattening_preserves_the_previous_vocabulary():
    """The move must be additive; a dropped token silently un-negates a label."""
    previous_prefixes = {
        "不是", "并不", "并非", "不太", "没那么", "没有", "并没有",
        "不", "没", "無", "无", "非", "别", "別",
        "안", "아니", "못", "не", "нет", "никогда",
    }
    assert previous_prefixes <= set(P.get_emotion_negation_prefixes_flat())
    assert set(P.get_emotion_negation_words_flat()) == {
        "not", "no", "never", "without",
        "안", "아니", "못", "않", "아니다", "아닌", "아님",
        "не", "нет", "никогда",
    }
    # The Korean set is what the move had to preserve; the Chinese postposed
    # forms are a later, deliberate addition on top of it.
    korean = set(P.EMOTION_NEGATION_SUFFIXES_BY_LANG["ko"])
    assert len(korean) == 23
    assert korean <= set(P.get_emotion_negation_suffixes_flat())


@pytest.mark.parametrize("label,expected", [
    ("沒有生氣", "neutral"),
    ("並不開心", "neutral"),
    ("沒那麼難過", "neutral"),
    ("別生氣", "neutral"),
    ("無驚訝", "neutral"),
    ("生氣", "angry"),
    ("驚訝", "surprised"),
    ("開心", "happy"),
    # the Simplified and non-Chinese paths must be untouched by the move
    ("没有生气", "neutral"),
    ("not happy", "neutral"),
    ("happy", "happy"),
    ("슬프지 않아", "neutral"),
])
def test_negated_traditional_labels_do_not_invert(label, expected):
    from main_routers.system_router.emotion import _normalize_emotion_label

    assert _normalize_emotion_label(label) == expected


def test_session_language_wins_over_the_process_wide_one():
    """The frontend sets the session language; the global one is the OS/Steam one.

    They disagree whenever the user picks a language inside the app, and then the
    global value is wrong in both directions — Traditional sessions on a
    Simplified install and vice versa.
    """
    with language_context("zh-TW"):
        assert detect_prompt_language("我今天好开心", ui_language="zh-CN") == "zh"
    with language_context("zh-CN"):
        assert detect_prompt_language("我今天好開心", ui_language="zh-TW") == "zh-TW"


def test_session_language_is_normalized_before_comparison():
    """`user_language` is whatever the frontend sent, not a canonical code."""
    for variant in ("zh-TW", "zh_TW", "zh-Hant-TW", "zh-Hant"):
        assert detect_prompt_language("我今天好開心", ui_language=variant) == "zh-TW"


def test_absent_session_language_falls_back_to_the_global_one():
    with language_context("zh-TW"):
        assert detect_prompt_language("我今天好開心", ui_language=None) == "zh-TW"


def test_route_resolver_reads_the_session_language(monkeypatch):
    """The endpoint gets `lanlan_name` in the body and nothing else locale-ish."""
    from main_routers.system_router import emotion as R

    class _Session:
        user_language = "zh-TW"

    monkeypatch.setattr(R, "get_session_manager", lambda: {"neko": _Session()})
    with language_context("zh-CN"):
        assert R._resolve_emotion_prompt_language("我今天好開心", "neko") == "zh-TW"
        # unknown / absent name must not raise, just fall back
        assert R._resolve_emotion_prompt_language("我今天好开心", "missing") == "zh"
        assert R._resolve_emotion_prompt_language("我今天好开心", None) == "zh"


def test_master_emotion_resolver_takes_the_language_from_its_caller():
    """The tracker has no session handle, so the core passes its own value down."""
    from main_logic.activity.master_emotion import MasterEmotionTracker

    with language_context("zh-CN"):
        assert MasterEmotionTracker._resolve_lang("我今天好開心", "zh-TW") == "zh-TW"
        assert MasterEmotionTracker._resolve_lang("我今天好开心", "zh-CN") == "zh"


def test_core_passes_the_session_language_into_analyze():
    """Pinned at the call site: the plumbing is only useful if the core uses it.

    Located through the AST rather than by scanning the file, so the assertion is
    about *this* call and not about the word appearing somewhere in the module.
    """
    import ast
    import pathlib

    from main_logic.core import turn

    tree = ast.parse(pathlib.Path(turn.__file__).read_text(encoding="utf-8"))
    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "analyze"
    ]
    assert calls, "core 里找不到 master emotion 的 analyze 调用"
    assert any(
        any(kw.arg == "ui_language" for kw in call.keywords) for call in calls
    ), "core 没有把 session 语言传给 analyze"


def test_degree_adverb_table_pairs_across_scripts():
    """Same phenomenon, two orthographies — like every other table in this file."""
    table = P.EMOTION_NEGATION_DEGREE_ADVERBS_BY_LANG
    assert table["zh"] and table["zh-TW"]
    assert len(table["zh"]) == len(table["zh-TW"])
    assert not any(ch in SIMPLIFIED_ONLY for entry in table["zh-TW"] for ch in entry)






def test_adverb_stripping_is_confined_to_the_label_parser():
    """The keyword heuristic has its own, separate negation machinery.

    Its tables already carry the compound negation forms, so it must not be pulled
    into this change -- the two paths answer different questions.
    """
    from main_routers.system_router import emotion as R

    assert "不怎么" in R._HEURISTIC_NEGATION_TOKENS
    assert "不怎麼" in R._HEURISTIC_NEGATION_TOKENS
    for probe in ("我今天好開心", "我不太開心", "不是很難過但還好"):
        assert R._infer_emotion_from_text(probe) is not None




def test_longest_adverb_wins_when_entries_overlap(monkeypatch):
    """A shorter entry that is the tail of a longer one must not be taken first.

    Nothing in the shipped table overlaps, so this pins the helper's contract
    rather than today's data — the ordering is the only thing keeping a future
    entry from stranding the rest of a word.
    """
    from main_routers.system_router import emotion as R

    monkeypatch.setattr(R, "_EMOTION_DEGREE_ADVERBS", ("十分", "分"))
    assert R._strip_degree_adverbs("不十分") == "不"
    monkeypatch.setattr(R, "_EMOTION_DEGREE_ADVERBS", ("分", "十分"))
    assert R._strip_degree_adverbs("不十分") == "不十"


def test_module_builds_the_adverb_list_longest_first():
    """The ordering the helper above depends on has to actually be established.

    Its own table happens to have no overlapping entries, so only this pins the
    construction; the two together are what keeps a future entry safe.
    """
    from main_routers.system_router import emotion as R

    lengths = [len(adverb) for adverb in R._EMOTION_DEGREE_ADVERBS]
    assert lengths == sorted(lengths, reverse=True), R._EMOTION_DEGREE_ADVERBS


# The endpoint calls `_normalize_emotion_label(raw_emotion, raw_confidence)`, and
# the confidence lowers the fuzzy cutoffs (0.9/0.88 -> 0.74/0.72). A test that
# omits it exercises the strict path only and can pass on a label the running
# system gets wrong, so every label below is checked in both regimes. The prompt
# itself asks for a high confidence when the emotion is clear, so 0.9 is the
# ordinary case, not the exotic one.
CONFIDENCES = [None, 0.9]


def _label(text, confidence):
    from main_routers.system_router.emotion import _normalize_emotion_label

    if confidence is None:
        return _normalize_emotion_label(text)
    return _normalize_emotion_label(text, confidence)


@pytest.mark.parametrize("confidence", CONFIDENCES)
@pytest.mark.parametrize("label", [
    "不怎麼開心", "不怎么开心", "沒有很生氣", "没有很生气",
    "不是很開心", "不很開心", "並不怎麼開心", "不是很特別開心",
    "沒有那麼驚訝", "没有那么惊讶", "沒有非常生氣",
])
def test_negation_survives_a_degree_adverb(label, confidence):
    """A negated label with a degree adverb in it must not report the emotion.

    English never had this problem -- its scan runs over the last three *tokens*,
    and a compact CJK window has no token boundaries to count, so the adverb sat
    between the negation and the alias and the endswith test simply failed.
    """
    assert _label(label, confidence) == "neutral"


@pytest.mark.parametrize("confidence", CONFIDENCES)
@pytest.mark.parametrize("label,expected", [
    # an ordinary word that happens to end in a negation character, then an
    # adverb, then the emotion: peeling must not turn its last character into a
    # negation it never was
    ("分别很开心", "happy"), ("分別很開心", "happy"),
    ("个别很生气", "angry"), ("個別很生氣", "angry"),
    ("告别很难过", "sad"), ("告別很難過", "sad"),
    ("除非很开心", "happy"), ("除非很開心", "happy"),
    # the adverb is inside a fixed phrase
    ("差不多开心", "happy"), ("差不多開心", "happy"),
    ("差不多難過", "sad"), ("差不多生氣", "angry"),
])
def test_peeling_does_not_reach_into_ordinary_words(label, expected, confidence):
    """The uncovered text has to BE a negation, not merely end with one.

    Reaching further left is only safe when what it uncovers is the label's whole
    opening; otherwise every word ending in one of the single-character negations
    reads as one.
    """
    assert _label(label, confidence) == expected


@pytest.mark.parametrize("confidence", CONFIDENCES)
@pytest.mark.parametrize("label,expected", [
    ("不是，非常开心", "happy"),
    ("不是，有点难过", "sad"),
    ("没，太开心了", "happy"),
])
def test_peeling_stops_at_a_clause_boundary(label, expected, confidence):
    """Punctuation is stripped out of the compact text, so the window would
    otherwise reach straight through a comma into an unrelated clause.

    The keyword heuristic has had `_HEURISTIC_CLAUSE_DELIMITERS` for this all
    along; the label parser had no notion of a clause, which is why extending its
    reach needed one.
    """
    assert _label(label, confidence) == expected


@pytest.mark.parametrize("confidence", CONFIDENCES)
@pytest.mark.parametrize("label,expected", [
    # intensified but NOT negated
    ("很開心", "happy"), ("非常生氣", "angry"), ("非常開心", "happy"),
    ("非常難過", "sad"), ("非常驚訝", "surprised"),
    ("十分驚訝", "surprised"), ("超級開心", "happy"), ("最難過", "sad"),
    ("有點難過", "sad"), ("太開心", "happy"), ("更開心", "happy"),
    ("特別開心", "happy"), ("特别开心", "happy"), ("特別難過", "sad"),
    ("無比開心", "happy"), ("許多開心", "happy"), ("好多開心", "happy"),
    ("開心", "happy"), ("生氣", "angry"), ("驚訝", "surprised"), ("難過", "sad"),
])
def test_intensifiers_are_not_read_as_negations(label, expected, confidence):
    """The other direction: reaching further left must not invent a negation.

    The plain intensifier + emotion form is the one that matters most -- it is
    the most ordinary way for a model to answer, and a single leading character
    used to be enough to fuzzy-match the rest against the alias and call the
    whole thing negated.
    """
    assert _label(label, confidence) == expected


@pytest.mark.parametrize("confidence", CONFIDENCES)
@pytest.mark.parametrize("label", [
    # adjacent negation — the behaviour that was already there and must not move
    "沒有生氣", "没有生气", "並不開心", "很不開心", "不太開心", "沒那麼難過",
    "不開心", "不生氣", "没开心", "無驚訝", "別生氣",
    "not happy", "not very happy", "never happy",
    "슬프지 않아", "не злюсь", "no estoy feliz",
])
def test_adjacent_and_non_chinese_negation_is_unchanged(label, confidence):
    assert _label(label, confidence) == "neutral"


def test_stacked_degree_adverbs_are_all_peeled():
    """Adverbs stack, so one pass is not enough.

    Both have to come off before the window ends in a negation.
    """
    for confidence in CONFIDENCES:
        assert _label("不是很特別開心", confidence) == "neutral"
        assert _label("沒有非常生氣", confidence) == "neutral"


def test_peeling_needs_to_see_the_whole_opening():
    """A truncated window cannot say the negation opens the label.

    The lookback is capped at the longest negation (7 characters), so a longer
    label only ever shows its tail. Synthetic input, because a real label never
    stacks this much in front of the emotion word — but without the check the
    peeled reading would fire on whatever the cap happened to leave visible.
    """
    for confidence in CONFIDENCES:
        assert _label("真不是怎麼特別很開心", confidence) == "neutral"
        assert _label("不是怎麼特別很開心", confidence) == "neutral"


@pytest.mark.parametrize("confidence", CONFIDENCES)
@pytest.mark.parametrize("label,expected", [
    # sentence-final punctuation sits AFTER the emotion word, so it says nothing
    # about whether a negation and that word are in the same clause
    ("沒有很生氣。", "neutral"), ("不怎麼開心。", "neutral"), ("沒有很生氣！", "neutral"),
])
def test_punctuation_after_the_alias_does_not_block_peeling(label, expected, confidence):
    """The clause check has to look at the text before the match, not the whole label."""
    assert _label(label, confidence) == expected


@pytest.mark.parametrize("confidence", CONFIDENCES)
@pytest.mark.parametrize("label,expected", [
    ("我沒有很生氣", "neutral"), ("其實沒有很生氣", "neutral"),
    ("我不是很開心", "neutral"), ("我並不怎麼開心", "neutral"),
])
def test_negation_may_follow_descriptive_text(label, expected, confidence):
    """A model that answers in a sentence still put the negation in it.

    Requiring the negation to open the label was too strict; requiring instead
    that it be at least two characters is what keeps the ordinary-word case out,
    since there the coincidence is a single character. See
    test_peeling_does_not_reach_into_ordinary_words.
    """
    assert _label(label, confidence) == expected


@pytest.mark.parametrize("text", ["😀😀😀", "123 456", "!!!", "", "   "])
def test_undetectable_text_falls_back_to_the_caller_default(text):
    """`detect_language` says 'unknown', and normalizing that lands on 'en'.

    That is a guess wearing a detection's clothes — the parameter the caller
    passed for exactly this case is the honest answer.
    """
    assert detect_prompt_language(text) == "zh"
    assert detect_prompt_language(text, default="en") == "en"


def test_table_discovery_actually_finds_them():
    """The discovery above is only useful if it finds something.

    A typo in the suffix would make every table-shaped test below vacuous —
    parametrized over an empty list, reported as passing.
    """
    assert len(FLAT_TABLES) >= 9, FLAT_TABLES
    assert "EMOTION_KEYWORDS_BY_LANG" in FLAT_TABLES
    assert "EMOTION_NEGATION_PREFIXES_BY_LANG" in FLAT_TABLES


def test_no_language_table_has_a_duplicate_key():
    """A repeated key in a dict literal is silent — the later one simply wins.

    It happened while writing this PR: a second `zh-TW` entry appended to the
    heuristic blocklist quietly discarded the first, and ruff does not flag it.
    """
    import ast
    import collections
    import pathlib

    tree = ast.parse(pathlib.Path(P.__file__).read_text(encoding="utf-8"))
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        keys = [k.value for k in node.keys if isinstance(k, ast.Constant)]
        repeated = [k for k, n in collections.Counter(keys).items() if n > 1]
        if repeated:
            offenders.append((node.lineno, repeated))
    assert not offenders, offenders


@pytest.mark.parametrize("confidence", CONFIDENCES)
@pytest.mark.parametrize("label", [
    "不會開心", "不会开心", "不算開心", "不算开心",
    "未必驚訝", "未必惊讶", "不再生氣", "不再生气",
    "不至於難過", "不至于难过", "談不上開心", "算不上生氣",
])
def test_compound_negations_are_recognized(label, confidence):
    """These open with a character that is a negation only in combination.

    The single character alone is not followed by an alias, so nothing matches
    until the whole compound form is in the negation table.
    """
    assert _label(label, confidence) == "neutral"


@pytest.mark.parametrize("text,expected", [
    # the keyword heuristic runs on the user's own words, where these are common
    ("我今天特別開心", "happy"), ("我今天特别开心", "happy"),
    ("個別的時候很生氣", "angry"), ("差不多開心", "happy"),
    ("告別了很難過", "sad"), ("分別的時候好難過", "sad"),
])
def test_ordinary_words_do_not_negate_the_keyword_heuristic(text, expected):
    """These used to score zero.

    Each opens with an ordinary word whose last character is a single-character
    negation, sitting right against the emotion word — which is exactly the span
    the tight negation lookback covers. The blocklist is the mechanism that was
    already there for the "not only" family; this is the same idea applied to
    ordinary words that merely end in one of those characters.
    """
    from main_routers.system_router.emotion import _infer_emotion_from_text

    emotion, score = _infer_emotion_from_text(text)
    assert emotion == expected, (text, emotion, score)


@pytest.mark.parametrize("text", [
    "我今天不開心", "我今天不太開心", "別生氣了", "不要生氣", "我不是很開心",
])
def test_real_negation_still_suppresses_the_keyword_heuristic(text):
    from main_routers.system_router.emotion import _infer_emotion_from_text

    assert _infer_emotion_from_text(text)[0] is None


@pytest.mark.parametrize("confidence", CONFIDENCES)
@pytest.mark.parametrize("label,expected", [
    # punctuation BEFORE a valid negation: the negation and the emotion word are
    # still in the same clause, so the reach must be truncated, not abandoned
    ("嗯，我沒有很生氣", "neutral"), ("我想想，我沒有很生氣", "neutral"),
    # ...while punctuation BETWEEN them still cuts
    ("不是，非常开心", "happy"),
])
def test_clause_cut_keeps_the_part_that_shares_the_clause(label, expected, confidence):
    """Rejecting the whole label on any punctuation threw away valid readings."""
    assert _label(label, confidence) == expected


@pytest.mark.parametrize("confidence", CONFIDENCES)
@pytest.mark.parametrize("label,expected", [
    ("我特別開心", "happy"), ("感覺特別開心", "happy"), ("其實特別難過", "sad"),
    ("我今天特别开心", "happy"),
])
def test_an_intensifier_against_the_alias_is_not_a_negation(label, expected, confidence):
    """Whatever the window ends with belongs to the adverb, not to a negation.

    The earlier version only handled this when the adverb was the label's whole
    opening, so a sentence-style answer still read the last character of the
    adverb as a negation.
    """
    assert _label(label, confidence) == expected


@pytest.mark.parametrize("text", [
    # a real negation sitting in front of a blocklisted intensifier
    "別特別開心", "别特别生气", "不要特別開心",
    # a compound already in the wide table before this PR
    "不算很開心",
])
def test_degraded_heuristic_keeps_these_negated(text):
    """Blanking a blocklisted phrase with spaces pushed a real negation out of
    the fixed-width tight lookback; removing it keeps the negation adjacent."""
    from main_routers.system_router.emotion import _infer_emotion_from_text

    assert _infer_emotion_from_text(text)[0] is None


@pytest.mark.parametrize("text,expected", [
    # the wide lookback fires on the whole 14-character span, so a modal negation
    # would swallow an unrelated predicate in the same punctuation-free clause
    ("我不会唱歌也很开心", "happy"),
    ("他不会来所以我很难过", "sad"),
])
def test_modal_negations_stay_out_of_the_wide_lookback(text, expected):
    """They belong to the label parser's table, which tests adjacency instead.

    Adding them here suppressed the emotion of a different clause entirely — the
    two tables look alike but admit words on different terms.
    """
    from main_routers.system_router.emotion import _infer_emotion_from_text

    assert _infer_emotion_from_text(text)[0] == expected


@pytest.mark.parametrize("text,expected", [
    # a negation separated from the keyword by a degree word: the single
    # character falls outside the two-character tight window
    ("不要那麼難過", "sad"), ("不要那么难过", "sad"),
    ("不要太開心", "happy"), ("我沒有必要生氣", "angry"),
])
def test_documented_gap_negation_across_a_degree_word(text, expected):
    """Pinned so a change here is deliberate, not accidental.

    Both orthographies behave the same, on main as here — closing it means
    carrying a negation across intervening words in the heuristic, which is a
    different mechanism from either table and out of scope for a backfill.
    """
    from main_routers.system_router.emotion import _infer_emotion_from_text

    assert _infer_emotion_from_text(text)[0] == expected


@pytest.mark.parametrize("confidence", CONFIDENCES)
@pytest.mark.parametrize("label", [
    "我不太开心", "其实不怎么开心", "我沒那麼難過", "我不怎麼開心",
])
def test_a_negation_may_reach_back_past_the_intensifiers(label, confidence):
    """These end with a negation that itself spans the degree adverb.

    Peeling first and testing second lost them; testing first and never peeling
    would read the last character of an ordinary intensifier as a negation. The
    length of the match against the length of what was peeled separates the two.
    """
    assert _label(label, confidence) == "neutral"


@pytest.mark.parametrize("confidence", CONFIDENCES)
@pytest.mark.parametrize("label", [
    "開心不起來", "高興不起來", "生氣不起來", "开心不起来",
])
def test_postposed_negation_after_the_emotion_word(label, confidence):
    """Chinese can negate from behind, and the suffix table was Korean-only.

    That branch requires everything before the marker to look like an alias on
    its own, so it cannot reach into an unrelated part of the label.
    """
    assert _label(label, confidence) == "neutral"
