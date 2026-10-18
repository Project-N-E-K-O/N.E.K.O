# -*- coding: utf-8 -*-
"""Traditional-Chinese reachability for the mini-game invite copy (issue #2500).

The invite reply *keywords* were fixed in #2651 and need no locale plumbing —
``_match_mini_game_invite_keyword`` scans every locale block at once. The invite
**copy** is the opposite shape: one invite line and three button labels, both
looked up by prompt-dict key. That makes them a *dead-code* risk rather than a
zero-hit one — ``_resolve_proactive_locale`` normalized with ``format="short"``,
and a short code has no room for a script, so ``zh-TW`` arrived as ``zh`` and any
``zh-TW`` row would have been data nothing could ever reach.

So this file pins both halves, because either one alone is silently useless:

* the tables carry a ``zh-TW`` row, and
* the locale actually gets to them with its script intact.

The second half is the one that rots quietly — a later refactor that "simplifies"
the invite call sites back to the short code leaves every test about the *table*
green while the Traditional rows go dark again. Hence the call-site walk at the
bottom, which discovers the call sites instead of listing them.
"""  # noqa: DOCSTRING_CJK
from __future__ import annotations

import ast
import inspect
import pathlib
from types import SimpleNamespace

import pytest

from config.prompts.prompts_proactive import (
    MINI_GAME_INVITE_LINES_BY_GAME,
    MINI_GAME_INVITE_OPTION_LABELS,
    normalize_mini_game_invite_locale,
)
from config.prompts.prompts_sys import _loc
from main_logic.proactive_chat import service
from main_logic.proactive_chat.mini_game_invite import (
    _build_mini_game_invite_options_payload,
    _render_mini_game_invite_line,
)

# One-to-one glyph markers: characters identical in both forms (局/不/想/玩/球...) cannot serve as markers,
# or valid entries would be misclassified as unconverted.
TRADITIONAL_ONLY = "來現遊戲會戰選個"
SIMPLIFIED_ONLY = "来现游戏会战选个"

OPTION_KEYS = ("accept", "decline", "later")
GAME_TYPES = tuple(MINI_GAME_INVITE_LINES_BY_GAME)


# -- 1. Both tables have zh-TW rows --
@pytest.mark.parametrize("game_type", GAME_TYPES)
def test_every_game_invite_line_has_a_traditional_row(game_type):
    """Automatic discovery fails here if a newly integrated minigame omits zh-TW."""  # noqa: DOCSTRING_CJK
    assert "zh-TW" in MINI_GAME_INVITE_LINES_BY_GAME[game_type], (
        f"{game_type} 缺 zh-TW 邀请文案"
    )


def test_option_labels_have_a_traditional_row():
    assert "zh-TW" in MINI_GAME_INVITE_OPTION_LABELS


@pytest.mark.parametrize("game_type", GAME_TYPES)
def test_traditional_invite_line_is_not_a_copy_of_the_simplified_one(game_type):
    table = MINI_GAME_INVITE_LINES_BY_GAME[game_type]
    assert table["zh-TW"] != table["zh"]
    assert any(ch in TRADITIONAL_ONLY for ch in table["zh-TW"])
    offenders = [ch for ch in table["zh-TW"] if ch in SIMPLIFIED_ONLY]
    assert not offenders, f"{game_type} 的 zh-TW 文案里混进简体字：{offenders}"


def test_traditional_option_labels_are_not_a_copy():
    tw = MINI_GAME_INVITE_OPTION_LABELS["zh-TW"]
    cn = MINI_GAME_INVITE_OPTION_LABELS["zh"]
    assert set(tw) == set(cn) == set(OPTION_KEYS)
    assert tw != cn
    offenders = [ch for label in tw.values() for ch in label if ch in SIMPLIFIED_ONLY]
    assert not offenders, f"zh-TW label 里混进简体字：{offenders}"


@pytest.mark.parametrize("game_type", GAME_TYPES)
def test_traditional_invite_line_keeps_the_placeholder(game_type):
    """A missing placeholder swallows master_name, producing an invitation without an address."""  # noqa: DOCSTRING_CJK
    assert "{master_name}" in MINI_GAME_INVITE_LINES_BY_GAME[game_type]["zh-TW"]


# -- 2. Locale resolution actually reaches those rows --
TRADITIONAL_SPELLINGS = ("zh-TW", "zh-tw", "zh_TW", "zh-Hant", "zh-Hant-TW", "tchinese")
SIMPLIFIED_SPELLINGS = ("zh", "zh-CN", "zh_CN", "zh-Hans", "schinese")


@pytest.mark.parametrize("spelling", TRADITIONAL_SPELLINGS)
def test_every_traditional_spelling_resolves_to_the_traditional_key(spelling):
    assert normalize_mini_game_invite_locale(spelling) == "zh-TW"


@pytest.mark.parametrize("spelling", SIMPLIFIED_SPELLINGS)
def test_simplified_spellings_still_resolve_to_the_simplified_key(spelling):
    assert normalize_mini_game_invite_locale(spelling) == "zh"


@pytest.mark.parametrize("spelling", ("en", "ja", "ko", "ru", "es", "pt"))
def test_other_locales_are_untouched(spelling):
    assert normalize_mini_game_invite_locale(spelling) == spelling


@pytest.mark.parametrize("spelling", ("estonian", "undefined", "", None))
def test_garbage_degrades_to_english_not_to_a_crash(spelling):
    assert normalize_mini_game_invite_locale(spelling) == "en"


def _expected_line(game_type: str, key: str, master: str) -> str:
    return MINI_GAME_INVITE_LINES_BY_GAME[game_type][key].format(master_name=master)


@pytest.mark.parametrize("spelling", TRADITIONAL_SPELLINGS)
@pytest.mark.parametrize("game_type", GAME_TYPES)
def test_invite_line_renders_traditional(game_type, spelling):
    """Exercise the production renderer, without normalizing before feeding ``_loc`` in the test; that would test
    ``_loc`` rather than the call site and still pass if normalization were removed there."""  # noqa: DOCSTRING_CJK
    rendered = _render_mini_game_invite_line(game_type, spelling, "博士")
    assert rendered == _expected_line(game_type, "zh-TW", "博士")


@pytest.mark.parametrize("spelling", SIMPLIFIED_SPELLINGS)
@pytest.mark.parametrize("game_type", GAME_TYPES)
def test_invite_line_still_renders_simplified(game_type, spelling):
    rendered = _render_mini_game_invite_line(game_type, spelling, "博士")
    assert rendered == _expected_line(game_type, "zh", "博士")


@pytest.mark.parametrize("game_type", GAME_TYPES)
def test_invite_line_falls_back_to_simplified_via_loc(game_type):
    """Premise guard: ``_loc`` silently falls back to simplified Chinese for missing Chinese keys,
    so missing normalization produces wrong glyphs rather than a crash, leaving no other failure signal."""  # noqa: DOCSTRING_CJK
    assert _loc(MINI_GAME_INVITE_LINES_BY_GAME[game_type], "zh-Hant") == (
        MINI_GAME_INVITE_LINES_BY_GAME[game_type]["zh"]
    )


@pytest.mark.parametrize("spelling", TRADITIONAL_SPELLINGS)
def test_option_payload_renders_traditional_labels(spelling):
    """The payload builder does an exact ``.get``; an unnormalized tag would
    have fallen through to the Simplified labels without any error."""
    payload = _build_mini_game_invite_options_payload(
        invite_lang=spelling, game_type=GAME_TYPES[0], session_id="s",
    )
    labels = {opt["choice"]: opt["label"] for opt in payload["options"]}
    assert labels == MINI_GAME_INVITE_OPTION_LABELS["zh-TW"]
    # choice is a wire-format identifier and does not vary with locale.
    assert set(labels) == set(OPTION_KEYS)


@pytest.mark.parametrize("spelling", SIMPLIFIED_SPELLINGS)
def test_option_payload_still_renders_simplified_labels(spelling):
    payload = _build_mini_game_invite_options_payload(
        invite_lang=spelling, game_type=GAME_TYPES[0], session_id="s",
    )
    labels = {opt["choice"]: opt["label"] for opt in payload["options"]}
    assert labels == MINI_GAME_INVITE_OPTION_LABELS["zh"]


# -- 3. Parser: short-code handling collapses zh-TW --
@pytest.mark.parametrize("source", ("request", "session"))
def test_short_format_collapses_traditional_and_full_keeps_it(source):
    """This is the whole reason the invite path needs its own format.

    Asserting the *collapse* as well as the preservation is deliberate: it
    documents that ``fmt="short"`` is not merely suboptimal here but destroys
    the only bit that distinguishes the two Chinese rows.
    """
    if source == "request":
        data, mgr = {"language": "zh-TW"}, SimpleNamespace(user_language=None)
    else:
        data, mgr = {}, SimpleNamespace(user_language="zh-TW")

    short = service._resolve_proactive_locale(data, mgr)
    full = service._resolve_proactive_locale(data, mgr, fmt="full")

    assert normalize_mini_game_invite_locale(short) == "zh", (
        f"短码居然留住了字形（{short!r}），这条测试的前提没了"
    )
    assert normalize_mini_game_invite_locale(full) == "zh-TW", full


@pytest.mark.parametrize("lang", ("en", "ja", "zh-CN"))
def test_full_format_does_not_disturb_other_locales(lang):
    mgr = SimpleNamespace(user_language=None)
    resolved = service._resolve_proactive_locale({"language": lang}, mgr, fmt="full")
    expected = normalize_mini_game_invite_locale(lang)
    assert normalize_mini_game_invite_locale(resolved) == expected


def test_request_language_still_wins_over_the_session():
    mgr = SimpleNamespace(user_language="zh-TW")
    resolved = service._resolve_proactive_locale({"language": "ja"}, mgr, fmt="full")
    assert normalize_mini_game_invite_locale(resolved) == "ja"


def test_render_language_guides_proactive_copy_without_becoming_declared_locale():
    mgr = SimpleNamespace(user_language="en", _user_language_explicit=False)
    data = {"render_language": "ja"}

    assert service._resolve_proactive_locale(data, mgr, fmt="full") == "ja"
    assert service._resolve_declared_topic_hook_locale(data, mgr) is None


def test_garbage_request_language_still_falls_through_to_the_session():
    """The supported-language whitelist predates this change and must survive it:
    without it a corrupted localStorage short-circuits the copy to English."""
    mgr = SimpleNamespace(user_language="zh-TW")
    resolved = service._resolve_proactive_locale(
        {"language": "estonian"}, mgr, fmt="full",
    )
    assert normalize_mini_game_invite_locale(resolved) == "zh-TW", resolved


# -- 4. Call sites supplying locale to invitations --
# The above checks establish only that tables and parsers work independently; call sites actually deliver zh-TW to users.
# Reverting call sites to short codes leaves all those checks green. Walk service.py's
# AST to discover every source of invite_lang automatically, rather than listing them, so a future third call site
# is checked too.
INVITE_CALLEES = {
    "_build_mini_game_invite_options_payload",
    "run_mini_game_invite_short_circuit",
}


def _service_tree() -> ast.Module:
    path = pathlib.Path(inspect.getsourcefile(service))
    return ast.parse(path.read_text(encoding="utf-8"))


def _callee_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _invite_lang_arg_names() -> dict[str, set[str]]:
    """callee -> the variable names handed to its ``invite_lang=``."""
    found: dict[str, set[str]] = {}
    for node in ast.walk(_service_tree()):
        if not isinstance(node, ast.Call):
            continue
        name = _callee_name(node)
        if name not in INVITE_CALLEES:
            continue
        for kw in node.keywords:
            if kw.arg != "invite_lang":
                continue
            assert isinstance(kw.value, ast.Name), (
                f"{name} 的 invite_lang 不是个变量（{ast.dump(kw.value)}），"
                "下面的赋值追踪跟不过去，请更新这条测试"
            )
            found.setdefault(name, set()).add(kw.value.id)
    return found


def test_both_invite_call_sites_are_found():
    """Premise guard: if no call sites are found, the test below is vacuous."""  # noqa: DOCSTRING_CJK
    found = _invite_lang_arg_names()
    assert set(found) == INVITE_CALLEES, f"只找到 {sorted(found)}"


def test_every_invite_locale_is_resolved_with_the_full_format():
    """Each variable feeding ``invite_lang`` must come from a full-format resolve.

    A bare string assignment (the ``except`` fallback) is fine — it carries no
    script to lose. What must not appear is ``_resolve_proactive_locale`` called
    without ``fmt="full"``.
    """
    tree = _service_tree()
    wanted = {n for names in _invite_lang_arg_names().values() for n in names}

    resolves: dict[str, list[ast.Call]] = {name: [] for name in wanted}
    others: dict[str, list[ast.AST]] = {name: [] for name in wanted}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not isinstance(target, ast.Name) or target.id not in wanted:
                continue
            value = node.value
            if isinstance(value, ast.Call) and _callee_name(value) == "_resolve_proactive_locale":
                resolves[target.id].append(value)
            else:
                others[target.id].append(value)

    for name in sorted(wanted):
        assert resolves[name], f"{name} 从来没有由 _resolve_proactive_locale 赋值过"
        for call in resolves[name]:
            fmt = next((kw.value for kw in call.keywords if kw.arg == "fmt"), None)
            assert isinstance(fmt, ast.Constant) and fmt.value == "full", (
                f"{name} 由 _resolve_proactive_locale 赋值时没有传 fmt=\"full\"，"
                "zh-TW 会在这里被折成 zh，两张表的 zh-TW 行就成了死数据"
            )
        for value in others[name]:
            assert isinstance(value, ast.Constant) and isinstance(value.value, str), (
                f"{name} 还有一处非字符串常量的赋值（{ast.dump(value)}），"
                "它可能带进一个被折过的短码"
            )
