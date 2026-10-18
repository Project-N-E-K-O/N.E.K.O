# -*- coding: utf-8 -*-
"""Traditional-Chinese reachability for the whole proactive family (issue #2500 step 2 C2).

Step 1 backfilled a ``zh-TW`` row into every dictionary in ``prompts_proactive``.
Those rows were unreachable data until now: ``_normalize_prompt_language`` ran with
``keep_traditional=False``, and every caller resolved its locale with
``format="short"``, so Traditional was already gone twice over before a lookup
happened. This file pins the flip and — more importantly — the call sites, because
the flip alone is worthless if a caller still hands over ``zh``.

Three things are asserted, and each is useless without the others:

1. the normalizer keeps the script (``keep_traditional=True``),
2. every proactive entry point hands it a locale that still has one, and
3. **one turn renders one locale** — the invariant that made the flip have to be
   atomic. A later patch that adds a lookup bypassing the normalizer, or "cleans
   up" one call site back to the short code, would leave Traditional users reading
   half-Simplified copy. Test 3 is the one that catches it.

⚠️ There is a fourth assertion that looks redundant and is not: the Simplified
guard. The obvious way to keep the script is ``format="full"``, which yields
``zh-CN`` — and ``zh-CN`` is not a key in *any* of these tables. That mistake keeps
every Traditional test green while quietly degrading the majority Simplified path
to English on the plain ``dict.get`` lookups. See
``test_simplified_never_degrades_to_english``.
"""  # noqa: DOCSTRING_CJK
from __future__ import annotations

import ast
import inspect
import pathlib
from types import SimpleNamespace

import pytest

from config.prompts.prompts_activity import (
    WORK_BREAK_GAME_INVITE_PROMPTS_BY_GAME,
    WORK_BREAK_GENERIC_WORK_LABEL,
)
from config.prompts.prompts_proactive import (
    CAT_GREETING_REASON_AUTO,
    MEME_SECTION_HEADER,
    MUSIC_SEARCH_RESULT_TEXTS,
    NEW_CHARACTER_GREETING_PROMPT,
    PROACTIVE_MUSIC_UNKNOWN_TRACK,
    RECENT_PROACTIVE_CHANNEL_LABELS,
    RECENT_PROACTIVE_TIME_LABELS,
    SCREEN_SECTION_HEADER,
    _normalize_prompt_language,
    get_cat_greeting_reason_hint,
    get_new_character_greeting_prompt,
    get_proactive_music_unknown_track_name,
    get_screen_section_header,
    normalize_proactive_prompt_locale,
)
from config.prompts.prompts_sys import _loc
from main_logic.proactive_chat import service

TRADITIONAL_SPELLINGS = ("zh-TW", "zh-tw", "zh_TW", "zh-Hant", "zh-Hant-TW", "tchinese")
SIMPLIFIED_SPELLINGS = ("zh", "zh-CN", "zh_CN", "zh-Hans", "schinese")
OTHER_LOCALES = ("en", "ja", "ko", "ru", "es", "pt")


def _mgr(user_language):
    return SimpleNamespace(user_language=user_language)


# -- 1. Normalization preserves orthography --
@pytest.mark.parametrize("spelling", TRADITIONAL_SPELLINGS)
def test_every_traditional_spelling_survives_the_normalizer(spelling):
    assert _normalize_prompt_language(spelling) == "zh-TW"


@pytest.mark.parametrize("spelling", SIMPLIFIED_SPELLINGS)
def test_simplified_spellings_still_collapse_to_the_simplified_key(spelling):
    """``zh-CN`` must come out ``zh``: it is the *simplified* key of these tables,
    and ``zh-CN`` itself is not a key anywhere in ``config/prompts``."""
    assert _normalize_prompt_language(spelling) == "zh"


@pytest.mark.parametrize("spelling", OTHER_LOCALES)
def test_other_locales_are_untouched_by_the_flip(spelling):
    assert _normalize_prompt_language(spelling) == spelling


@pytest.mark.parametrize("spelling", ("estonian", "undefined", "", None))
def test_garbage_degrades_to_english_not_to_a_crash(spelling):
    assert _normalize_prompt_language(spelling) == "en"


def test_public_normalizer_is_the_same_function():
    """The public face must not drift from the private one — consumers that index
    the tables directly have to land on exactly the getters' key scheme."""
    for spelling in TRADITIONAL_SPELLINGS + SIMPLIFIED_SPELLINGS + OTHER_LOCALES:
        assert normalize_proactive_prompt_locale(spelling) == _normalize_prompt_language(
            spelling
        )


# -- 2. Parsing delivers orthography correctly --
@pytest.mark.parametrize("source", ("request", "session", "global"))
@pytest.mark.parametrize("spelling", TRADITIONAL_SPELLINGS)
def test_prompt_format_preserves_traditional_from_every_source(source, spelling, monkeypatch):
    if source == "request":
        data, mgr = {"language": spelling}, _mgr(None)
    elif source == "session":
        data, mgr = {}, _mgr(spelling)
    else:
        data, mgr = {}, _mgr(None)
        monkeypatch.setattr(service, "get_global_language_full", lambda: spelling)

    assert service._resolve_proactive_locale(data, mgr, fmt="prompt") == "zh-TW"


@pytest.mark.parametrize("spelling", TRADITIONAL_SPELLINGS)
def test_short_format_still_collapses_it(spelling):
    """Premise guard: short codes really destroy orthography. If this passes, the tests above
    no longer prove anything; they would pass without fixing call sites."""  # noqa: DOCSTRING_CJK
    resolved = service._resolve_proactive_locale({}, _mgr(spelling))
    assert resolved == "zh", resolved


@pytest.mark.parametrize("spelling", SIMPLIFIED_SPELLINGS)
def test_prompt_format_yields_the_simplified_key_not_a_full_locale(spelling):
    """``fmt="full"`` returns ``zh-CN``, which is not a key in any prompt table."""  # noqa: DOCSTRING_CJK
    assert service._resolve_proactive_locale({}, _mgr(spelling), fmt="prompt") == "zh"


@pytest.mark.parametrize("spelling", OTHER_LOCALES)
def test_prompt_format_leaves_other_locales_alone(spelling):
    assert service._resolve_proactive_locale({}, _mgr(spelling), fmt="prompt") == spelling


def test_request_language_still_wins_over_the_session():
    resolved = service._resolve_proactive_locale({"language": "ja"}, _mgr("zh-TW"), fmt="prompt")
    assert resolved == "ja"


def test_garbage_request_language_still_falls_through_to_the_session():
    """Preserve the preexisting allowlist guard; otherwise corrupt localStorage
    short-circuits prose to English despite valid session state."""  # noqa: DOCSTRING_CJK
    resolved = service._resolve_proactive_locale(
        {"language": "estonian"}, _mgr("zh-TW"), fmt="prompt"
    )
    assert resolved == "zh-TW", resolved


# -- 3. All four paths receive zh-TW templates --
@pytest.mark.parametrize("spelling", TRADITIONAL_SPELLINGS)
def test_proactive_chat_copy_renders_traditional(spelling):
    """Proactive-chat prose: the screen-section header expands {master}, making it the easiest
    section of the Phase 2 prompt in which to overlook orthography."""  # noqa: DOCSTRING_CJK
    lang = service._resolve_proactive_locale({}, _mgr(spelling), fmt="prompt")
    assert get_screen_section_header("博士", lang) == SCREEN_SECTION_HEADER["zh-TW"].format(
        master="博士"
    )


@pytest.mark.parametrize("spelling", TRADITIONAL_SPELLINGS)
def test_break_reminder_copy_renders_traditional(spelling):
    """Rest invitations: both prompts_activity tables use bare ``dict.get``,
    so incorrect keys silently produce English rather than raising."""  # noqa: DOCSTRING_CJK
    from main_logic.proactive_chat.break_reminders import _resolve_break_reminder_label

    lang = service._resolve_proactive_locale({}, _mgr(spelling), fmt="prompt")
    assert _resolve_break_reminder_label(None, lang, WORK_BREAK_GENERIC_WORK_LABEL) == (
        WORK_BREAK_GENERIC_WORK_LABEL["zh-TW"]
    )
    for game_type, per_lang in WORK_BREAK_GAME_INVITE_PROMPTS_BY_GAME.items():
        assert per_lang.get(lang) == per_lang["zh-TW"], game_type


@pytest.mark.parametrize("spelling", TRADITIONAL_SPELLINGS)
def test_new_character_greeting_renders_traditional(spelling):
    """Icebreaker greetings."""  # noqa: DOCSTRING_CJK
    assert get_new_character_greeting_prompt(
        normalize_proactive_prompt_locale(spelling)
    ) == NEW_CHARACTER_GREETING_PROMPT["zh-TW"]


@pytest.mark.parametrize("spelling", TRADITIONAL_SPELLINGS)
def test_cat_greeting_renders_traditional(spelling):
    """Cat greetings: same family as icebreakers, sharing their mixin and short-code source."""  # noqa: DOCSTRING_CJK
    assert get_cat_greeting_reason_hint(
        True, normalize_proactive_prompt_locale(spelling)
    ) == CAT_GREETING_REASON_AUTO["zh-TW"]


# -- 4. All prose within one turn shares one locale --
# This is why C1 centralization exists. Individual path tests may pass while users see a mixed-language
# turn. Any future lookup bypassing normalization fails here.
def _one_turn_renderings(lang: str) -> dict[str, str]:
    """Fragments actually assembled in a proactive-chat turn's prompt, covering three lookup styles:
    getters / ``_loc`` / bare ``dict.get``."""  # noqa: DOCSTRING_CJK
    return {
        "screen_header": get_screen_section_header("博士", lang),
        "meme_header": _loc(MEME_SECTION_HEADER, lang),
        "music_unknown_track": get_proactive_music_unknown_track_name(lang),
        "music_search_title": MUSIC_SEARCH_RESULT_TEXTS.get(
            lang, MUSIC_SEARCH_RESULT_TEXTS["en"]
        )["title"],
        "recent_time_label": RECENT_PROACTIVE_TIME_LABELS.get(
            lang, RECENT_PROACTIVE_TIME_LABELS["en"]
        )["m"],
        "recent_channel_label": RECENT_PROACTIVE_CHANNEL_LABELS.get(
            lang, RECENT_PROACTIVE_CHANNEL_LABELS["en"]
        )["vision"],
    }


def _expected_for_key(key: str, row: str) -> str:
    return {
        "screen_header": SCREEN_SECTION_HEADER[row].format(master="博士"),
        "meme_header": MEME_SECTION_HEADER[row],
        "music_unknown_track": PROACTIVE_MUSIC_UNKNOWN_TRACK[row],
        "music_search_title": MUSIC_SEARCH_RESULT_TEXTS[row]["title"],
        "recent_time_label": RECENT_PROACTIVE_TIME_LABELS[row]["m"],
        "recent_channel_label": RECENT_PROACTIVE_CHANNEL_LABELS[row]["vision"],
    }[key]


@pytest.mark.parametrize("spelling", TRADITIONAL_SPELLINGS)
def test_one_turn_is_entirely_traditional(spelling):
    lang = service._resolve_proactive_locale({}, _mgr(spelling), fmt="prompt")
    rendered = _one_turn_renderings(lang)
    # Assert exact values, not merely presence of traditional characters; containment cannot detect half the templates falling back to simplified.
    assert rendered == {k: _expected_for_key(k, "zh-TW") for k in rendered}


@pytest.mark.parametrize("spelling", SIMPLIFIED_SPELLINGS)
def test_one_turn_is_entirely_simplified(spelling):
    lang = service._resolve_proactive_locale({}, _mgr(spelling), fmt="prompt")
    rendered = _one_turn_renderings(lang)
    assert rendered == {k: _expected_for_key(k, "zh") for k in rendered}


def test_simplified_never_degrades_to_english():
    """Specifically guard against ``fmt="full"``.

    ``full`` yields ``zh-CN``, absent from all these tables, so bare ``dict.get``
    falls directly to ``en``. Traditional-Chinese tests pass while simplified-Chinese users see English.
    """  # noqa: DOCSTRING_CJK
    lang = service._resolve_proactive_locale({}, _mgr("zh-CN"), fmt="prompt")
    english = {k: _expected_for_key(k, "en") for k in _one_turn_renderings(lang)}
    rendered = _one_turn_renderings(lang)
    offenders = [k for k, v in rendered.items() if v == english[k]]
    assert not offenders, f"简中被降级成英文的片段：{offenders}"


def test_full_format_would_have_broken_simplified():
    """Premise guard: prove the previous test is meaningful and ``fmt="full"`` really breaks simplified Chinese.

    If ``full`` ever returns ``zh`` or tables gain ``zh-CN`` rows, fail here to
    prompt rewriting the previous rationale rather than letting it become a tautology.
    """  # noqa: DOCSTRING_CJK
    full = service._resolve_proactive_locale({}, _mgr("zh-CN"), fmt="full")
    assert full == "zh-CN"
    assert MUSIC_SEARCH_RESULT_TEXTS.get(full) is None
    assert RECENT_PROACTIVE_TIME_LABELS.get(full) is None


# -- 5. Discover call sites automatically rather than listing them --
# Above only establishes independently correct normalizers/tables; call sites deliver zh-TW to users.
# Removing ``fmt="prompt"`` leaves many section 1/3/4 tests green because tables do not change. Walk
# service.py's AST and require explicit fmt at every call site.
def _callee_name(node: ast.Call) -> str:
    """Call name, treating attribute calls and bare names equally.

    Checking only ``ast.Name`` misses ``service._resolve_proactive_locale(...)`` /
    ``prompts_proactive.normalize_proactive_prompt_locale(...)``, both ``ast.Attribute``;
    legitimate refactoring would falsely fail the guard or, worse, hide a real call site.
    """  # noqa: DOCSTRING_CJK
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _service_tree() -> ast.Module:
    path = pathlib.Path(inspect.getsourcefile(service))
    return ast.parse(path.read_text(encoding="utf-8"))


def _resolver_calls() -> list[ast.Call]:
    return [
        node
        for node in ast.walk(_service_tree())
        if isinstance(node, ast.Call)
        and _callee_name(node) == "_resolve_proactive_locale"
    ]


def test_the_call_site_walk_finds_something():
    """Vacuous-assertion trap: incorrect AST matching makes the following test pass without checking anything."""  # noqa: DOCSTRING_CJK
    assert len(_resolver_calls()) >= 4


def test_no_call_site_relies_on_the_short_default():
    """The short-code default remains part of the function contract, but no call site should use it now:
    proactive-family consumers all use prompt dictionaries."""  # noqa: DOCSTRING_CJK
    offenders = [
        node.lineno
        for node in _resolver_calls()
        if not any(kw.arg == "fmt" for kw in node.keywords)
    ]
    assert not offenders, f"这些调用点还在吃短码默认值：service.py:{offenders}"


GREETING_ENTRYPOINTS = ("trigger_cat_greeting", "trigger_new_character_greeting")


def _greeting_tree() -> ast.Module:
    from main_logic.core import greeting as greeting_mod

    return ast.parse(
        pathlib.Path(inspect.getsourcefile(greeting_mod)).read_text(encoding="utf-8")
    )


def _named_function(tree: ast.Module, name: str):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def _greeting_scope(name: str) -> list:
    """Entry function plus locally called helpers, following one level.

    Walking only the entry body leaves a gap: moving locale resolution into a private helper
    such as ``_resolve_greeting_lang()`` hides short codes from the guard. Following one level
    closes that gap and prevents the required-normalizer check falsely failing after simple extraction refactors.
    """  # noqa: DOCSTRING_CJK
    tree = _greeting_tree()
    entry = _named_function(tree, name)
    assert entry is not None, f"greeting.py 里找不到 {name}——入口改名了，这条守卫已失效"

    scope = [entry]
    for node in ast.walk(entry):
        if not isinstance(node, ast.Call):
            continue
        helper = _named_function(tree, _callee_name(node))
        if helper is not None and helper is not entry:
            scope.append(helper)
    return scope


def _calls_in(scope: list) -> list[ast.Call]:
    return [node for func in scope for node in ast.walk(func) if isinstance(node, ast.Call)]


@pytest.mark.parametrize("name", GREETING_ENTRYPOINTS)
def test_the_greeting_scope_walk_finds_something(name):
    """Vacuous-assertion trap: incorrect scope resolution makes the following two tests pass without checking anything."""  # noqa: DOCSTRING_CJK
    assert _calls_in(_greeting_scope(name)), f"{name} 作用域里一个调用都没扫到"


@pytest.mark.parametrize("name", GREETING_ENTRYPOINTS)
def test_greeting_entrypoints_do_not_shorten_the_locale(name):
    """The two greeting tests above supply normalized locales and test getters, not call sites:
    changing greeting.py back to ``format='short'`` leaves both green. Check the call site here."""  # noqa: DOCSTRING_CJK
    offenders = [
        node.lineno
        for node in _calls_in(_greeting_scope(name))
        if _callee_name(node) == "normalize_language_code"
        and any(
            kw.arg == "format" and getattr(kw.value, "value", None) == "short"
            for kw in node.keywords
        )
    ]
    assert not offenders, f"{name} 还在把 locale 砍成短码：greeting.py:{offenders}"


@pytest.mark.parametrize("name", GREETING_ENTRYPOINTS)
def test_greeting_entrypoints_normalize_through_the_prompt_normalizer(name):
    callees = {_callee_name(node) for node in _calls_in(_greeting_scope(name))}
    assert "normalize_proactive_prompt_locale" in callees, (
        f"{name} 没走 prompt key 归一化器，zh-TW 行取不到"
    )


def test_prompt_dict_consumers_ask_for_the_prompt_format():
    """``_break_lang`` / ``proactive_lang`` are the two main paths feeding prompt tables."""  # noqa: DOCSTRING_CJK
    wanted = {"_break_lang", "proactive_lang"}
    seen: dict[str, str] = {}
    for node in ast.walk(_service_tree()):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        call = node.value
        if _callee_name(call) != "_resolve_proactive_locale":
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id in wanted:
                fmt = next((kw.value for kw in call.keywords if kw.arg == "fmt"), None)
                seen[target.id] = getattr(fmt, "value", None)
    assert seen == {name: "prompt" for name in wanted}, seen
