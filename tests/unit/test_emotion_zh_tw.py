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

TRADITIONAL_ONLY = "開興歡愛難傷嗚遺喪負氣煩惱惡會這麼貼嬌並僅過驚憤靜裡閉憐別沒"
# 有意不含 `里`：它在两种写法里都是正字（公里／里長），拿它当简体标记会把将来
# 合法的繁体词条判成违规。`裡` 只在繁体侧出现，所以只放在上面那一串里。
SIMPLIFIED_ONLY = "开兴欢爱难伤呜遗丧负气烦恼恶会这么贴娇并仅过惊愤静闭怜别没"

FLAT_TABLES = [
    "EMOTION_KEYWORDS_BY_LANG",
    "ANGRY_ATTACK_PATTERNS_BY_LANG",
    "SAD_VULNERABLE_PATTERNS_BY_LANG",
    "HAPPY_PLAYFUL_PATTERNS_BY_LANG",
    "HEURISTIC_NEGATION_TOKENS_BY_LANG",
    "HEURISTIC_TIGHT_NEGATION_TOKENS_BY_LANG",
    "HEURISTIC_NEGATION_BLOCKLIST_BY_LANG",
    "HEURISTIC_CONTRAST_CONJUNCTIONS_BY_LANG",
    "EMOTION_LABEL_ALIASES_BY_LANG",
]


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

    Once `生氣` is an alias, `沒有生氣` matches it as a substring. If the negation
    prefixes only carry Simplified `没有`, the negation is missed and the label
    comes back `angry` — the opposite of what the model said, and worse than
    before the aliases existed.
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
    assert len(set(P.get_emotion_negation_suffixes_flat())) == 23


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
