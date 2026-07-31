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
    # A superset, not equality: the move had to preserve every token, and later
    # rounds deliberately added the English contractions plus the Spanish and
    # Portuguese blocks that were missing entirely.
    assert {
        "not", "no", "never",
        "안", "아니", "못", "않", "아니다", "아닌", "아님",
        "не", "нет", "никогда",
    } <= set(P.get_emotion_negation_words_flat())
    # `without` was in this table before the move and is deliberately gone: it is
    # a preposition that negates its own complement, so it read `I am without
    # doubt happy` as a denial of the happiness. Its Spanish and Portuguese
    # equivalents were added and removed again for the same reason.
    # See test_a_preposition_is_not_a_negation.
    assert not ({"without", "sin", "sem"} & set(P.get_emotion_negation_words_flat()))
    assert not ({"sin ", "sem ", "without "} & set(P.get_heuristic_negation_tokens_flat()))
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


@pytest.mark.parametrize("text", [
    "不要那麼難過", "不要那么难过", "不要太開心",
])
def test_imperative_negation_across_a_degree_word(text):
    """Was a documented gap until the adjacent-modal table covered it.

    The imperative negator reaches the emotion word once the degree adverb
    between them is peeled -- the same scoping the modal compounds use, so it
    costs no extra mechanism.
    """
    from main_routers.system_router.emotion import _infer_emotion_from_text

    assert _infer_emotion_from_text(text)[0] is None


def test_still_uncovered_negation_shape():
    """A noun phrase between the negation and the emotion word is still uncovered.

    It is not a degree adverb, so nothing peels it; both orthographies behave
    the same, on main as here. Pinned so closing it later is deliberate.
    """
    from main_routers.system_router.emotion import _infer_emotion_from_text

    assert _infer_emotion_from_text("我沒有必要生氣")[0] == "angry"


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


@pytest.mark.parametrize("confidence", CONFIDENCES)
@pytest.mark.parametrize("label", [
    "真的開心不起來", "其實生氣不起來", "我開心不起來", "真的开心不起来",
])
def test_postposed_negation_after_descriptive_text(label, confidence):
    """The marker negates the emotion word it follows, not the whole label.

    Requiring everything before it to look like an alias covered only the bare
    form, so any sentence-style answer came back as the emotion itself.
    """
    assert _label(label, confidence) == "neutral"


@pytest.mark.parametrize("confidence", CONFIDENCES)
@pytest.mark.parametrize("label,expected", [
    # the marker appears twice; the one that matters is the later
    ("我笑不起來，其實真的開心不起來", "neutral"),
    ("我笑不起来，其实真的开心不起来", "neutral"),
    # ...and an earlier marker must not swallow a later, un-negated emotion
    ("我笑不起來，其實很開心", "happy"),
])
def test_every_suffix_occurrence_is_examined(label, expected, confidence):
    """Stopping at the first marker read the label as the emotion it denies."""
    assert _label(label, confidence) == expected


@pytest.mark.parametrize("text", [
    "我今天開心不起來", "開心不起來", "我今天开心不起来", "生氣不起來",
])
def test_degraded_heuristic_reads_postposed_negation(text):
    """Chinese negates from behind, and the heuristic reads the same user text.

    The label parser learned this first; leaving the heuristic without it meant
    the newly added Traditional keyword scored the emotion its sentence denies.
    """
    from main_routers.system_router.emotion import _infer_emotion_from_text

    assert _infer_emotion_from_text(text)[0] is None


def test_postposed_marker_must_touch_the_keyword():
    """A marker further along belongs to some later phrase, not to this hit."""
    from main_routers.system_router.emotion import _infer_emotion_from_text

    assert _infer_emotion_from_text("我今天好開心，只是笑不出來")[0] == "happy"


@pytest.mark.parametrize("confidence", CONFIDENCES)
@pytest.mark.parametrize("label", [
    "沒有真的生氣", "不會真的開心", "我並沒有真正開心", "沒有真生氣",
])
def test_negation_reaches_across_the_really_intensifiers(label, confidence):
    """The "really" family sits between the negation and the emotion word.

    They behave like any other degree adverb there, and were simply missing from
    the table.
    """
    assert _label(label, confidence) == "neutral"


@pytest.mark.parametrize("confidence", CONFIDENCES)
@pytest.mark.parametrize("label,expected", [
    # a label naming two emotions: the named one outranks `neutral`
    ("中性但開心", "happy"), ("普通但開心", "happy"),
    ("平靜但生氣", "angry"), ("中性但難過", "sad"),
    # two named emotions: earliest in the text wins, deterministically
    ("開心但有點難過", "happy"),
])
def test_a_label_naming_two_emotions_is_resolved_by_the_text(label, expected, confidence):
    """Returning on the first alias found made the answer depend on dict order.

    Two labels of the same shape came back with different emotions, and nothing
    about the text decided which — only which alias the iteration reached first.
    """
    assert _label(label, confidence) == expected


def test_single_emotion_labels_are_unchanged_by_the_ranking():
    """The rule only arbitrates between matches; one match still wins outright."""
    for label, expected in [
        ("開心", "happy"), ("生氣", "angry"), ("平靜", "neutral"),
        ("neutral", "neutral"), ("happy", "happy"),
    ]:
        for confidence in CONFIDENCES:
            assert _label(label, confidence) == expected


def test_same_position_ties_prefer_the_longer_alias(monkeypatch):
    """The tie-break that keeps the ranking total rather than merely partial.

    No two aliases in the shipped table are a prefix of each other with different
    canonicals, so only an injected pair separates this from "whatever sorted
    first" — and that is exactly the non-determinism the ranking replaced.
    """
    from main_routers.system_router import emotion as R

    # The injected canonical sorts BEFORE the real one, so a rank that falls back
    # to comparing canonicals would pick it; only the length keeps them ordered.
    monkeypatch.setitem(R._EMOTION_COMPACT_ALIAS_LOOKUP, "難", "angry")
    monkeypatch.setitem(R._EMOTION_NORMALIZED_ALIAS_LOOKUP, "難", "angry")
    assert R._normalize_emotion_label("他難過") == "sad"


@pytest.mark.parametrize("confidence", CONFIDENCES)
@pytest.mark.parametrize("label,expected", [
    # denies one emotion and asserts another: the assertion is the answer
    ("我難過不起來但很開心", "happy"),
    ("難過不起來但很開心", "happy"),
    ("我笑不起來，其實很開心", "happy"),
    # nothing follows the marker, so the denial IS the answer
    ("開心不起來", "neutral"), ("難過不起來", "neutral"),
    ("我笑不起來，其實真的開心不起來", "neutral"),
])
def test_postposed_negation_does_not_veto_a_later_emotion(label, expected, confidence):
    """A denied emotion earlier must not outrank an asserted one later.

    The suffix branch answers for the *whole* label, so firing it whenever the
    head reads as an emotion reported the denial as the result.
    """
    assert _label(label, confidence) == expected


@pytest.mark.parametrize("confidence", CONFIDENCES)
@pytest.mark.parametrize("label", ["슬프지 않아", "기쁘지 않아"])
def test_korean_whole_label_negation_is_unchanged(label, confidence):
    """Korean attaches its negation to the verb ending, so it really is whole-label.

    That branch matches the head fuzzily rather than by alias, which is why the
    two mechanisms have to coexist.
    """
    assert _label(label, confidence) == "neutral"


@pytest.mark.parametrize("text", [
    "不會真的開心", "不再那麼難過", "不算很開心", "未必開心", "不至於難過",
])
def test_degraded_heuristic_reads_adjacent_modal_negation(text):
    """Modal compounds only negate the word they sit against.

    They were tried in the wide 14-character lookback first, where they swallowed
    an unrelated predicate in the same clause; this table is consulted only after
    the degree adverbs are peeled off, so it has to be adjacent.
    """
    from main_routers.system_router.emotion import _infer_emotion_from_text

    assert _infer_emotion_from_text(text)[0] is None


@pytest.mark.parametrize("text,expected", [
    # the trap the wide lookback fell into: a modal negating a DIFFERENT predicate
    ("我不会唱歌也很开心", "happy"),
    ("他不会来所以我很难过", "sad"),
])
def test_a_modal_negating_another_predicate_is_left_alone(text, expected):
    from main_routers.system_router.emotion import _infer_emotion_from_text

    assert _infer_emotion_from_text(text)[0] == expected


@pytest.mark.parametrize("confidence", CONFIDENCES)
@pytest.mark.parametrize("label,expected", [
    # leading punctuation is dropped from one index space but not the other
    ("......sad但開心", "sad"),
    ("sad但開心", "sad"),
    ("開心但sad", "happy"),
])
def test_mixed_script_aliases_are_ranked_in_one_space(label, expected, confidence):
    """An ASCII match indexes the normalized text, a CJK one the compact text.

    Comparing them directly meant the punctuation compact_text drops could order
    a later alias ahead of an earlier one.
    """
    assert _label(label, confidence) == expected


@pytest.mark.parametrize("confidence", CONFIDENCES)
@pytest.mark.parametrize("label", [
    # a negator separated from the emotion by something that is NOT a degree
    # adverb, so peeling cannot reach it — the whole phrase goes in the table
    "沒什麼好開心的", "沒什麼可開心的", "没什么好开心的", "有什麼好開心的",
    "不要生氣", "不要這麼開心", "我不覺得開心", "我沒覺得難過",
    "我沒有在生氣", "我沒有到很難過", "我沒有覺得很開心",
    # `超級` / `一直` really are degree/aspect adverbs, so those go in that table
    "我沒有超級難過", "我沒有一直很難過",
])
def test_negation_separated_by_a_non_adverb(label, confidence):
    """One of the most ordinary ways Chinese denies an emotion.

    What sits between the negator and the emotion word is not a degree adverb,
    so peeling cannot reach it. Returning the denied emotion is the worst answer
    available under any reading.
    """
    assert _label(label, confidence) == "neutral"


def test_hao_is_not_treated_as_a_degree_adverb():
    """The obvious shortcut for the above, which does not work.

    Peeling that character off "in a bad mood" uncovers a trailing negation, so
    "in a bad mood, sad" would read as negated. It is both an intensifier and
    half of a negation, which is why these go in the negation table as whole
    phrases instead.
    """
    for confidence in CONFIDENCES:
        assert _label("心情不好難過", confidence) == "sad"
        assert _label("心情不好难过", confidence) == "sad"


@pytest.mark.parametrize("confidence", CONFIDENCES)
@pytest.mark.parametrize("label", [
    "興奮しない", "興奮していない", "憤怒しない", "傷心ではない",
])
def test_japanese_postposed_negation(label, confidence):
    """Japanese negates with a trailing kana, and that table had no `ja` block.

    It went unnoticed until this PR added Traditional aliases that are the same
    Han characters Japanese uses: those words became reachable and landed
    straight in the hole.
    """
    assert _label(label, confidence) == "neutral"


@pytest.mark.parametrize("confidence", CONFIDENCES)
@pytest.mark.parametrize("label", [
    "não estou feliz", "não estou triste", "nunca feliz", "jamás feliz",
    "isn't happy", "wasn't sad", "aren't sad",
    # the contraction has to survive tokenizing for the three-token lookback to
    # see it — `don't` split into `don` + `t` was in no table at all
    "don't be sad", "can't be happy",
])
def test_portuguese_spanish_and_contracted_english_negation(label, confidence):
    """Portuguese had no negation words at all; Spanish borrowed English's `no`.

    The English contractions were split by the tokenizer into `isn` + `t`, so
    neither half was ever a negation.
    """
    assert _label(label, confidence) == "neutral"


# --- 否定识别的三处反转（来自主动扫描，逐条对 origin/main 复核过） ---


@pytest.mark.parametrize("confidence", CONFIDENCES)
@pytest.mark.parametrize("label, expected", [
    ("個別難過", "sad"),
    ("个别难过", "sad"),
    ("區別開心", "happy"),
    ("区别开心", "happy"),
    ("分別很開心", "happy"),
    ("特別開心", "happy"),
    ("差別很大很開心", "happy"),
    ("送別難過", "sad"),
])
def test_a_negation_syllable_inside_a_word_is_not_a_negation(label, expected, confidence):
    """The imperative negator is also the second half of a dozen common words.

    The adjacency test could not tell them apart, so a label built out of one of
    those words came back as the denial of the emotion it was asserting -- the
    worst answer available. The heuristic side already kept a blocklist for
    exactly this; both sides read it now.
    """
    assert _label(label, confidence) == expected


@pytest.mark.parametrize("confidence", CONFIDENCES)
@pytest.mark.parametrize("label", ["別難過", "別太開心", "不特別開心", "别难过"])
def test_the_bare_imperative_negation_still_negates(label, confidence):
    """The other half of the above: removing the blocklist word must not remove
    a negation that was really there."""
    assert _label(label, confidence) == "neutral"


@pytest.mark.parametrize("confidence", CONFIDENCES)
@pytest.mark.parametrize("label, expected", [
    ("難過哭不出來", "sad"),
    ("难过哭不出来", "sad"),
    ("傷心哭不出來", "sad"),
    ("難過到笑不出來", "sad"),
])
def test_a_postposed_marker_denies_the_word_it_sits_against(label, expected, confidence):
    """"So sad I can't even cry" is sad, and the marker is about the crying.

    The whole-label veto fuzzy-matched the entire run before the marker as one
    misspelt emotion word, which at the confidence the endpoint passes scored
    high enough to answer neutral. Only reachable at high confidence, which is
    why the parameterisation over confidences is not decorative.
    """
    assert _label(label, confidence) == expected


@pytest.mark.parametrize("confidence", CONFIDENCES)
@pytest.mark.parametrize("label", [
    "開心不起來", "我難過不起來", "我笑不起來，其實真的開心不起來", "开心不起来",
])
def test_a_postposed_marker_still_vetoes_the_word_it_does_sit_against(label, confidence):
    assert _label(label, confidence) == "neutral"


@pytest.mark.parametrize("text", [
    "sino que estoy feliz",
    "estoy muy bueno y feliz",
    "casino night, I am happy",
    "not only happy",
])
def test_a_latin_word_that_ends_in_a_negation_is_not_a_negation(text):
    """The Latin entries pad themselves with a space to fake a word boundary.

    That only guards one side, so every Spanish or Portuguese word ending in
    those two letters -- sino, bueno, uno -- silently swallowed the writer's
    emotion. Real boundaries on the Latin entries; the CJK ones stay on the
    substring path, where there are no boundaries to find.
    """
    from main_routers.system_router.emotion import _infer_emotion_from_text

    assert _infer_emotion_from_text(text)[0] is not None


@pytest.mark.parametrize("text", [
    "no estoy feliz", "nunca feliz", "nao estou feliz", "I am not happy",
    "cannot be happy", "I don't feel happy", "not angry at all",
])
def test_latin_negations_still_negate(text):
    from main_routers.system_router.emotion import _infer_emotion_from_text

    assert _infer_emotion_from_text(text)[0] is None


# --- 否定的作用域：小句边界与日语丁宁体 ---


@pytest.mark.parametrize("confidence", CONFIDENCES)
@pytest.mark.parametrize("label", [
    "興奮していません", "憤怒していません", "傷心していません",
    "興奮しておりません", "興奮してません", "興奮しません",
    "興奮していない", "興奮してない", "興奮しない",
    "興奮ではありません", "興奮じゃありません", "嬉しくありません",
])
def test_japanese_polite_negation_is_recognised(label, confidence):
    """The polite forms put three or four kana between the word and the ending.

    The per-match test anchors the marker to the end of the alias, so a table
    holding only the tail matched nothing at all -- the label came back as the
    emotion it was denying. Composed forms go in whole.
    """
    assert _label(label, confidence) == "neutral"


@pytest.mark.parametrize("confidence", CONFIDENCES)
@pytest.mark.parametrize("label, expected", [
    ("não triste, feliz", "happy"),
    ("não triste mas feliz", "happy"),
    ("no triste, feliz", "happy"),
    ("no triste pero feliz", "happy"),
    ("not sad, happy", "happy"),
    ("not sad but happy", "happy"),
])
def test_a_negation_does_not_reach_past_a_clause_break(label, expected, confidence):
    """These name the emotion they assert, right after the one they deny.

    Three branches each read the label as one run and answered neutral, so the
    asserted half was thrown away. Punctuation and a contrast conjunction mark
    the same boundary and both have to stop it.
    """
    assert _label(label, confidence) == expected


@pytest.mark.parametrize("confidence", CONFIDENCES)
@pytest.mark.parametrize("label", [
    "não estou feliz", "no estoy feliz", "not happy", "não triste", "not sad.",
    "nunca feliz", "jamas feliz",
])
def test_a_negation_inside_one_clause_still_negates(label, confidence):
    """The other half: scoping must not cost the ordinary single-clause case."""
    assert _label(label, confidence) == "neutral"


@pytest.mark.parametrize("confidence", CONFIDENCES)
def test_a_postposed_veto_still_crosses_a_clause_break(confidence):
    """The postposed loop is scoped by what follows the marker, not by clauses.

    That is deliberate and sharper: this label has a comma and its last clause
    is still a denial, so a blanket clause guard there would answer sad.
    """
    assert _label("我笑不起來，其實真的開心不起來", confidence) == "neutral"
    assert _label("我難過不起來但很開心", confidence) == "happy"


# --- 两条管线各自的作用域缺口（来自一轮 45 agent 的扫描 + Codex 四条 P2） ---


def _heur(text):
    from main_routers.system_router.emotion import _infer_emotion_from_text

    return _infer_emotion_from_text(text)[0]


@pytest.mark.parametrize("text", [
    "這幅畫充滿生氣", "这幅画充满生气", "生氣勃勃", "生氣蓬勃", "了無生氣", "死氣沉沉",
])
def test_a_keyword_that_does_not_mean_the_emotion_it_spells(text):
    """The Traditional word for anger is also the word for vitality.

    No negation is involved, so none of the negation machinery can catch it --
    the keyword simply means something else. Degraded fallback is the only
    emotion source when the model is unavailable, so answering angry here drives
    a real reaction.
    """
    assert _heur(text) is None


@pytest.mark.parametrize("text", ["我很生氣", "他生氣了", "好生氣", "他有生氣"])
def test_real_anger_survives_the_false_friend_list(text):
    """The last one uses the Taiwanese perfective marker: it is real anger.

    That is why the vitality list deliberately omits that construction, at the
    cost of the literary reading. Pinned so the trade-off is not silently
    reversed.
    """
    # 台湾华语 `有 + 动词` 是完成体（`他有生氣` ＝ 他生气了），不是「有生机」。
    assert _heur(text) == "angry"


@pytest.mark.parametrize("text", ["有什麼好開心的", "有什么好开心的", "這有什麼好開心的", "有什麼好難過的"])
def test_an_overlapping_keyword_keeps_the_modal_negation(text):
    """The rhetorical negation has to survive the longer keyword winning.

    The tables hold both the short emotion word and a longer one starting one
    character earlier. The short match was suppressed correctly; the long one
    left a shorter window, degree-adverb stripping then took the window apart,
    and the phrase scored positive -- the opposite of what it says.
    """
    assert _heur(text) is None


@pytest.mark.parametrize("text", ["好開心", "好高興", "我今天好開心"])
def test_the_longer_keyword_still_scores_on_its_own(text):
    assert _heur(text) == "happy"


def test_an_emphatic_negation_does_not_reach_the_next_clause():
    """The emphatic form only denies the predicate it precedes.

    It sat in the 14-character wide-lookback table, so it cancelled the emotion
    asserted later in the same sentence and the whole line came back empty. It
    belongs in the adjacency-scoped table, which is where the other modal
    compounds already live.
    """
    assert _heur("我並不討厭而且覺得太棒") == "happy"
    assert _heur("我并不讨厌而且觉得太棒") == "happy"
    assert _heur("我並不開心") is None


@pytest.mark.parametrize("confidence", CONFIDENCES)
@pytest.mark.parametrize("label", ["sadじゃないけどhappy", "sadではないがhappy"])
def test_a_mixed_script_label_honours_the_postposed_negation(label, confidence):
    """A label can deny an ASCII alias with a Japanese suffix.

    The compact branch checked postposed markers; the ASCII branch did not, so
    the denied half stayed in the running and won the ranking for being earlier.
    """
    assert _label(label, confidence) == "happy"


@pytest.mark.parametrize("confidence", CONFIDENCES)
@pytest.mark.parametrize("label, expected", [("nice happy", "happy"), ("nice sad", "sad")])
def test_a_two_letter_latin_negation_is_not_a_syllable(label, expected, confidence):
    """Only reachable at the confidence the endpoint actually passes.

    A two-letter Spanish negation at the head of the label left a remainder that
    fuzzy-matches the emotion word once the cutoff drops, so an ordinary label
    came back neutral. Short Latin negations now get the rule single CJK
    characters already had: what follows must be an alias outright.
    """
    assert _label(label, confidence) == expected


@pytest.mark.parametrize("confidence", CONFIDENCES)
@pytest.mark.parametrize("label", [
    "幸せではなかった", "びっくりしなかった", "興奮していなかった", "嬉しいわけじゃない",
    "幸せじゃなくて", "意外でもない", "興奮せず", "腹が立つことはない", "可愛くありませんでした",
])
def test_japanese_negation_beyond_the_polite_present(label, confidence):
    """Past, te-form, particle-infixed and nominalised negation.

    All still sit directly against the alias, so they are table entries rather
    than new analysis. Each one was returning the emotion it denies.
    """
    assert _label(label, confidence) == "neutral"


@pytest.mark.parametrize("confidence", CONFIDENCES)
@pytest.mark.parametrize("label", [
    "開心不了", "开心不了", "開心才怪", "開心個屁", "生氣不了", "難過不了",
])
def test_chinese_postposed_colloquial_negation(label, confidence):
    assert _label(label, confidence) == "neutral"


@pytest.mark.parametrize("confidence", CONFIDENCES)
@pytest.mark.parametrize("label", [
    "無法開心", "无法开心", "難以開心", "沒辦法開心", "我無法難過", "無法生氣",
])
def test_chinese_inability_is_a_negation(label, confidence):
    """The single character was already in the table but cannot reach past the
    second half of the compound, because it requires the remainder to be an
    alias outright."""
    assert _label(label, confidence) == "neutral"


@pytest.mark.parametrize("text", [
    "no estoy feliz", "ni triste ni feliz", "tampoco estoy feliz",
    "neither sad nor happy", "ninguno feliz", "nada feliz",
    "no estoy triste ni enojado", "nem triste nem feliz",
    # `sin` / `sem` 曾经在这份清单里，后来整条撤出两张表 ——
    # 见 test_a_preposition_is_not_a_negation
])
def test_the_two_negation_tables_agree(text):
    """One table feeds the label parser, the other the heuristic — same words.

    They drifted: several negations existed only on the label side, so the same
    sentence came back neutral from one pipeline and named the emotion it denies
    from the other. This asserts both at once so a one-sided addition fails.
    """
    from main_routers.system_router.emotion import _normalize_emotion_label

    assert _normalize_emotion_label(text, 0.9) == "neutral"
    assert _heur(text) is None


@pytest.mark.parametrize("text", [
    "estoy feliz", "sino que estoy feliz", "casino night, I am happy", "I am happy",
])
def test_the_wider_negation_vocabulary_does_not_swallow_assertions(text):
    assert _heur(text) == "happy"


# --- 上一轮补的否定词自己带来的三处反转 ---


@pytest.mark.parametrize("text, expected", [
    ("senão fico feliz", "happy"),
    ("senão me sinto feliz", "happy"),
    ("não fico feliz", None),
    ("nunca feliz", None),
])
def test_an_accented_latin_negation_is_a_word_not_a_substring(text, expected):
    """The Latin/CJK split was `str.isascii()`, which is not about script.

    An accented Latin negation fell to the substring path and matched inside a
    longer word, so the sentence lost its emotion entirely. The split is by
    writing system now: only scripts with no word boundaries to find stay on
    substring.
    """
    assert _heur(text) == expected


def test_the_two_negation_paths_partition_by_script():
    """Structural: no entry may sit on the wrong side of the split.

    Asserted on the derived tuples rather than on behaviour, because a single
    misrouted entry only shows up on the one word that happens to contain it.
    """
    from main_routers.system_router.emotion import (
        _HEURISTIC_CJK_NEGATION_TOKENS, _HEURISTIC_WORD_NEGATIONS, _UNBOUNDED_SCRIPT_RE,
    )

    assert not any(_UNBOUNDED_SCRIPT_RE.search(t) for t in _HEURISTIC_WORD_NEGATIONS)
    assert all(_UNBOUNDED_SCRIPT_RE.search(t) for t in _HEURISTIC_CJK_NEGATION_TOKENS)
    latin = {t.strip() for t in _HEURISTIC_WORD_NEGATIONS}
    assert {"não", "jamás", "ningún", "не"} <= latin, "带重音的拉丁词/西里尔词落错边"


@pytest.mark.parametrize("confidence", CONFIDENCES)
@pytest.mark.parametrize("label, expected", [
    ("sin duda triste", "sad"),
    ("sin duda enojado", "angry"),
    ("sem dúvida triste", "sad"),
    ("sin ninguna duda triste", "sad"),
])
def test_the_fixed_phrase_does_not_veto_the_whole_label(label, expected, confidence):
    """The other whole-label branch reads a compact string, not tokens.

    Only reachable at the confidence the endpoint passes: with the phrase left
    in, the remainder glued together scores close enough to the emotion word to
    veto it. One strip now feeds both branches.
    """
    assert _label(label, confidence) == expected


@pytest.mark.parametrize("confidence", CONFIDENCES)
@pytest.mark.parametrize("label", ["sin duda feliz", "sem dúvida feliz", "sin duda estoy feliz"])
def test_a_fixed_phrase_containing_a_negation_is_not_a_negation(label, confidence):
    """"Without doubt" is emphatic agreement, the same family as "no doubt".

    The blocklist that already held that family was read by the heuristic and by
    the compact path, but not by either of the label parser's ASCII paths.
    """
    assert _label(label, confidence) == "happy"
    assert _heur(label) == "happy"


@pytest.mark.parametrize("confidence", CONFIDENCES)
@pytest.mark.parametrize("label", [
    "sin miedo y feliz", "sem medo e feliz", "sin miedo feliz", "sin estar feliz",
    "I am without doubt happy", "without doubt happy", "without being happy",
])
def test_a_preposition_is_not_a_negation(label, confidence):
    """`without` governs its own complement, and nothing here can find it.

    `sin miedo y feliz` denies the fear, not the happiness, and the last one
    genuinely does deny it -- telling them apart needs the complement, which this
    module does not parse. Fixed-phrase exemptions cannot help either: the
    complement is open-ended. So these two are out of the tables entirely, which
    is also what main did. Losing the last case is the price; reading an
    assertion as its own denial is not one we take.
    """
    assert _label(label, confidence) == "happy"
    assert _heur(label) == "happy"


@pytest.mark.parametrize("confidence", CONFIDENCES)
@pytest.mark.parametrize("label, expected", [
    ("nadando feliz", "happy"),
    ("nadie feliz", "happy"),
    ("nice happy", "happy"),
    ("nada feliz", "neutral"),
    ("ninguno feliz", "neutral"),
    ("no feliz", "neutral"),
    ("isn't happy", "neutral"),
])
def test_a_latin_negation_prefix_must_be_the_whole_first_word(label, expected, confidence):
    """Compacting hides word boundaries, and the fuzzy cutoff does the rest.

    `nadando` opens with a negation and the remainder scores close enough to the
    emotion word at the confidence the endpoint passes. The rule is the same one
    single CJK characters get, generalised: in a script that has word boundaries,
    the negation has to *be* the first word rather than merely open it.
    """
    assert _label(label, confidence) == expected


@pytest.mark.parametrize("text, expected", [
    ("这个结果难以置信令人开心", "happy"),
    ("這個結果難以置信令人開心", "happy"),
    ("这件事没办法解决但我很开心", "happy"),
    ("难以开心", None),
    ("無法開心", None),
    ("並無生氣", None),
])
def test_an_inability_negation_does_not_reach_the_next_predicate(text, expected):
    """Same shape as the emphatic negation: it modifies what follows it.

    In the wide table it cancelled an emotion asserted later in the sentence.
    These belong in the adjacency-scoped table, which still catches them when
    they really do sit against the emotion word.
    """
    assert _heur(text) == expected


@pytest.mark.parametrize("text, expected", [
    ("我並非討厭而且覺得太棒", "happy"),
    ("我并非讨厌而且觉得太棒", "happy"),
    ("並非開心", None),
    ("并非开心", None),
])
def test_the_other_emphatic_negation_is_predicate_scoped_too(text, expected):
    """The sibling of the one moved last round, left behind in the wide table.

    Same shape, same fix: it denies the predicate it precedes, so in a
    14-character window it cancelled an emotion asserted later in the sentence.
    """
    assert _heur(text) == expected


@pytest.mark.parametrize("confidence", CONFIDENCES)
@pytest.mark.parametrize("text, label_expected, heuristic_expected", [
    ("nada feliz", "neutral", None),
    ("nada triste", "neutral", None),
    ("nada más feliz", "happy", "happy"),
    ("no hay nada más feliz que un niño jugando", "happy", "happy"),
])
def test_the_degree_negation_is_told_apart_from_the_superlative(
    text, label_expected, heuristic_expected, confidence
):
    """The same four letters are an adverb in one reading and a noun in another.

    Adjacent to the adjective it means "not at all"; one word further away the
    sentence is a superlative comparison asserting exactly that emotion. Both
    pipelines tell them apart by requiring adjacency, which is the only signal
    available without parsing.
    """
    assert _label(text, confidence) == label_expected
    assert _heur(text) == heuristic_expected


@pytest.mark.parametrize("text", [
    "我沒有很開心", "沒有真的生氣", "其實沒有那麼難過", "我没有很开心", "並沒有很開心",
])
def test_the_bare_negation_survives_a_degree_adverb(text):
    """The tight lookback is two characters, so a degree word fills it entirely.

    With one in between, the window holds only the tail of the negation and the
    head of the adverb, and the sentence scored as the emotion it denies. The
    adjacency table peels the adverb first, which is exactly what this needs.
    """
    assert _heur(text) is None


@pytest.mark.parametrize("text, expected", [
    ("una jornada feliz", "happy"),
    ("uma jornada feliz", "happy"),
    ("nada feliz", None),
    ("la nada feliz", None),
])
def test_a_latin_modal_negation_has_to_end_a_word(text, expected):
    """Third time this shape has appeared, second time I introduced it.

    A Latin entry compared with `endswith` against raw text matches the tail of
    any longer word -- here the last four letters of the Spanish for "workday".
    """
    assert _heur(text) == expected


def test_every_latin_modal_negation_is_boundary_checked():
    """Auto-discovered from the table, because the list keeps growing.

    Each Latin entry must fail when glued to the end of a word and pass when it
    stands alone. Written against the table rather than against a few sentences
    so an entry added later is covered without anyone remembering to.
    """
    from main_routers.system_router.emotion import (
        _HEURISTIC_MODAL_NEGATIONS, _UNBOUNDED_SCRIPT_RE, _modal_ends_window,
    )

    latin = [m for m in _HEURISTIC_MODAL_NEGATIONS if not _UNBOUNDED_SCRIPT_RE.search(m)]
    assert latin, "本用例的前提是表里有拉丁词条"
    for modal in latin:
        assert _modal_ends_window(modal, modal), modal
        assert _modal_ends_window("xyz " + modal, modal), modal
        assert not _modal_ends_window("xyz" + modal, modal), f"{modal} 会命中长词的词尾"


@pytest.mark.parametrize("text", [
    "這是一個困難過程", "經歷困難過後終於完成", "这是一个困难过程", "艱難過程", "災難過後",
])
def test_a_keyword_formed_across_a_compound_boundary_is_not_one(text):
    """Neither half is an emotion word; the keyword only exists in the seam.

    Substring matching has no notion of a word boundary, so the left-hand word
    has to be removed before scoring.
    """
    # 具体例子：`困難` + `過程` 的接缝里浮出一个 `難過`。
    assert _heur(text) is None


@pytest.mark.parametrize("text", ["我很難過", "困難重重我好難過", "好難過喔"])
def test_the_real_emotion_word_still_scores(text):
    assert _heur(text) == "sad"
