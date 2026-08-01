# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Contract tests for the shared prompt-locale normalizer (issue #2500).

config/prompts used to carry six hand-rolled locale normalizers that disagreed
on empty-input defaults, Steam aliases, whitespace, and whether the Traditional
Chinese branch survived. They now all delegate to
``config.prompts._locale.normalize_prompt_locale``.

The table below is asserted through each module's own normalizer, not through
the shared function, so a module silently changing which keyword arguments it
passes fails here.
"""

import ast
import pathlib

import pytest

from config.prompts._locale import NEKO_CORE_LOCALES, normalize_prompt_locale
from config.prompts.prompts_avatar_interaction import _avatar_interaction_locale
from config.prompts.prompts_badminton import normalize_badminton_prompt_locale
from config.prompts.prompts_chara import _normalize_lang
from config.prompts.prompts_memory import _normalize_memory_prompt_lang
from config.prompts.prompts_minigame_common import _normalize_prompt_lang
from config.prompts.prompts_proactive import _normalize_prompt_language
from config.prompts.prompts_sys import _loc

PROMPTS_DIR = pathlib.Path(__file__).resolve().parents[2] / "config" / "prompts"

# The six module normalizers collapse to four distinct behaviors. chara, memory
# and avatar_interaction share one column: same default, same simplified key,
# both keeping zh-TW.
COLUMNS = ("proactive", "minigame", "traditional_aware", "badminton")

MODULE_NORMALIZERS = {
    "proactive": _normalize_prompt_language,
    "minigame": _normalize_prompt_lang,
    "traditional_aware": _normalize_lang,
    "badminton": normalize_badminton_prompt_locale,
}

# Extra members of the traditional_aware column, asserted to agree with it.
TRADITIONAL_AWARE_PEERS = (
    _normalize_memory_prompt_lang,
    _avatar_interaction_locale,
)

# _avatar_interaction_locale resolves empty input through
# resolve_global_language() instead of its own default, so on these inputs it
# answers whatever the app's language is and cannot be held to the column.
# test_avatar_empty_input_falls_back_to_global_language pins that path instead.
AVATAR_RESOLVER_INPUTS = {"", None}

# input -> (proactive, minigame, traditional_aware, badminton)
EXPECTED = {
    # The eight runtime locales.
    "en": ("en", "en", "en", "en"),
    "ja": ("ja", "ja", "ja", "ja"),
    "ko": ("ko", "ko", "ko", "ko"),
    "zh-CN": ("zh", "zh", "zh", "zh-CN"),
    "zh-TW": ("zh", "zh", "zh-TW", "zh-TW"),
    "ru": ("ru", "ru", "ru", "ru"),
    "pt": ("pt", "pt", "pt", "pt"),
    "es": ("es", "es", "es", "es"),
    # Short Chinese and its spellings.
    "zh": ("zh", "zh", "zh", "zh-CN"),
    "zh-Hant": ("zh", "zh", "zh-TW", "zh-TW"),
    "zh-Hans": ("zh", "zh", "zh", "zh-CN"),
    "zh-HK": ("zh", "zh", "zh-TW", "zh-TW"),
    "zh-hant-TW": ("zh", "zh", "zh-TW", "zh-TW"),
    # Case, underscore and surrounding whitespace must not change the answer.
    "ZH-TW": ("zh", "zh", "zh-TW", "zh-TW"),
    "zh_TW": ("zh", "zh", "zh-TW", "zh-TW"),
    "  zh-TW  ": ("zh", "zh", "zh-TW", "zh-TW"),
    "zh-tw": ("zh", "zh", "zh-TW", "zh-TW"),
    # Region subtags.
    "en-US": ("en", "en", "en", "en"),
    "ja-JP": ("ja", "ja", "ja", "ja"),
    "ko-KR": ("ko", "ko", "ko", "ko"),
    "ru-RU": ("ru", "ru", "ru", "ru"),
    "es-MX": ("es", "es", "es", "es"),
    "pt-BR": ("pt", "pt", "pt", "pt"),
    "en_US": ("en", "en", "en", "en"),
    # Steam store language codes. Every module resolves these now; before the
    # collapse only minigame and badminton did, and the rest fell to English.
    "schinese": ("zh", "zh", "zh", "zh-CN"),
    "tchinese": ("zh", "zh", "zh-TW", "zh-TW"),
    "english": ("en", "en", "en", "en"),
    "japanese": ("ja", "ja", "ja", "ja"),
    "koreana": ("ko", "ko", "ko", "ko"),
    "korean": ("ko", "ko", "ko", "ko"),
    "russian": ("ru", "ru", "ru", "ru"),
    "spanish": ("es", "es", "es", "es"),
    "latam": ("es", "es", "es", "es"),
    "portuguese": ("pt", "pt", "pt", "pt"),
    "brazilian": ("pt", "pt", "pt", "pt"),
    "TChinese": ("zh", "zh", "zh-TW", "zh-TW"),
    # Empty input takes the per-module default; the minigame and badminton
    # modules intentionally default to Chinese rather than English.
    "": ("en", "zh", "en", "zh-CN"),
    "   ": ("en", "zh", "en", "zh-CN"),
    # Unrecognized *non-empty* input is a different case from empty: it always
    # resolves to English, never to the module default.
    "xx": ("en", "en", "en", "en"),
    "klingon": ("en", "en", "en", "en"),
    "-zh": ("en", "en", "en", "en"),
    "fr": ("en", "en", "en", "en"),
    # "esperanto" must not be read as Spanish: matching is exact or
    # "<locale>-" prefixed, never a bare startswith.
    "esperanto": ("en", "en", "en", "en"),
    # Known wart, pinned so a change is deliberate: a tag merely beginning with
    # "zh" still reads as Chinese. Harmless while the runtime locale set is
    # NEKO_CORE_LOCALES, none of which collide.
    "zh-": ("zh", "zh", "zh", "zh-CN"),
}


@pytest.mark.parametrize("raw", list(EXPECTED))
def test_module_normalizers_match_table(raw):
    """Each module's own normalizer resolves the table's expected key."""
    for column, expected in zip(COLUMNS, EXPECTED[raw]):
        got = MODULE_NORMALIZERS[column](raw)
        assert got == expected, (
            f"{column} normalizer: {raw!r} -> {got!r}, expected {expected!r}"
        )


@pytest.mark.parametrize("raw", list(EXPECTED))
def test_traditional_aware_column_members_agree(raw):
    """memory and avatar_interaction stay in lockstep with the chara column."""
    expected = MODULE_NORMALIZERS["traditional_aware"](raw)
    for fn in TRADITIONAL_AWARE_PEERS:
        if fn is _avatar_interaction_locale and raw in AVATAR_RESOLVER_INPUTS:
            continue
        got = fn(raw)
        assert got == expected, (
            f"{fn.__module__}.{fn.__name__}: {raw!r} -> {got!r}, expected {expected!r}"
        )


@pytest.fixture
def global_language(monkeypatch):
    """Bind config._runtime's global-language resolver for one test."""
    from config import _runtime

    def _set(value):
        monkeypatch.setattr(
            _runtime, "_global_language_resolver", lambda: value, raising=False
        )

    return _set


def test_avatar_empty_input_falls_back_to_global_language(global_language):
    """Empty input reaches resolve_global_language(), not the module default.

    This is why avatar_interaction sits out the column assertion on empty
    input: with a resolver bound it answers the app's language, so asserting
    chara's "en" here would pass or fail depending on the host's locale and on
    whether an earlier test happened to bind a resolver.
    """
    global_language("zh-TW")
    assert _avatar_interaction_locale("") == "zh-TW"
    assert _avatar_interaction_locale(None) == "zh-TW"
    # Whitespace does NOT take that path: "   " is truthy, so it never reaches
    # the resolver and strips to empty, landing on the module default instead.
    assert _avatar_interaction_locale("   ") == "en"
    # chara has no resolver fallback at all.
    assert _normalize_lang("") == "en"


def test_avatar_matches_column_when_resolver_is_english(global_language):
    """With an English resolver the column assertion holds on empty input too."""
    global_language("en")
    for raw in AVATAR_RESOLVER_INPUTS:
        assert _avatar_interaction_locale(raw) == _normalize_lang(raw)


def test_none_takes_module_default():
    """None is empty input, so each module returns its own default."""
    assert _normalize_prompt_language(None) == "en"
    assert _normalize_prompt_lang(None) == "zh"
    assert _normalize_lang(None) == "en"
    assert _normalize_memory_prompt_lang(None) == "en"
    assert normalize_badminton_prompt_locale(None) == "zh-CN"


def test_keep_traditional_false_collapses_to_simplified():
    """keep_traditional=False must route Traditional Chinese to `simplified`.

    This keeps modules without Traditional templates on their declared
    Simplified key. `_loc` provides the same Chinese-family behavior as a
    secondary missing-key safety net.
    """
    for raw in ("zh-TW", "zh-Hant", "zh-HK", "tchinese"):
        assert normalize_prompt_locale(raw, keep_traditional=False) == "zh"
        assert normalize_prompt_locale(raw, keep_traditional=True) == "zh-TW"
        assert (
            normalize_prompt_locale(
                raw, simplified="zh-CN", keep_traditional=False
            )
            == "zh-CN"
        )


@pytest.mark.parametrize(
    "locale",
    ("zh", "zh-CN", "zh-TW", "zh-HK", "zh-Hant", "schinese", "tchinese"),
)
def test_loc_missing_chinese_variant_falls_back_to_simplified(locale):
    templates = {"zh": "simplified", "en": "english"}

    assert _loc(templates, locale) == "simplified"


def test_loc_missing_chinese_variant_supports_full_simplified_key():
    templates = {"zh-CN": "simplified", "en": "english"}

    assert _loc(templates, "zh-TW") == "simplified"


@pytest.mark.parametrize("locale", ("fr", "klingon", "esperanto"))
def test_loc_missing_non_chinese_or_unknown_locale_falls_back_to_english(locale):
    templates = {"zh": "simplified", "en": "english"}

    assert _loc(templates, locale) == "english"


def test_loc_prefers_an_exact_traditional_template():
    templates = {
        "zh": "simplified",
        "zh-TW": "traditional",
        "en": "english",
    }

    assert _loc(templates, "zh-TW") == "traditional"


def test_default_only_applies_to_empty_input():
    """`default` covers empty input; garbage still resolves to English."""
    assert normalize_prompt_locale("", default="zh") == "zh"
    assert normalize_prompt_locale(None, default="zh") == "zh"
    assert normalize_prompt_locale("   ", default="zh") == "zh"
    assert normalize_prompt_locale("klingon", default="zh") == "en"


def test_every_core_locale_round_trips():
    """Each of the eight runtime locales resolves to itself under full keys."""
    for locale in NEKO_CORE_LOCALES:
        got = normalize_prompt_locale(
            locale, simplified="zh-CN", keep_traditional=True
        )
        assert got == locale, f"{locale!r} -> {got!r}"


LOCALE_PREFIXES = {"zh", "en", "ja", "ko", "ru", "es", "pt"}


def _is_locale_prefix_literal(value: str) -> bool:
    """Whether a startswith() literal looks like locale prefix-matching.

    Keyed on the *primary* subtag so a region-qualified literal like "zh-tw"
    counts — that form is how prompts_icebreaker._prompt_lang_from_data slipped
    past an earlier, narrower version of this guard.
    """
    return value.lower().split("-")[0].strip() in LOCALE_PREFIXES


def _locale_predicate_functions():
    """Yield (file, function) for prompt functions doing their own locale sniffing.

    Discovered from the AST rather than listed, so a newly hand-rolled
    normalizer is caught without editing this test.

    Only startswith is inspected, deliberately. Normalizing a locale means
    prefix-matching an arbitrary input; branching on an already-normalized
    locale uses equality (e.g.
    prompts_avatar_interaction._avatar_interaction_prompt_actor's
    ``locale == "ko"`` for Korean subject particles, or the per-call-site zh-TW
    collapse in prompts_memory). Those are legitimate and stay out.
    """
    hits = []
    for path in sorted(PROMPTS_DIR.glob("*.py")):
        if path.name == "_locale.py":
            continue  # the one place allowed to sniff locale strings
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for inner in ast.walk(node):
                if (
                    isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Attribute)
                    and inner.func.attr == "startswith"
                    and inner.args
                    and isinstance(inner.args[0], ast.Constant)
                    and isinstance(inner.args[0].value, str)
                    and _is_locale_prefix_literal(inner.args[0].value)
                ):
                    hits.append((path.name, node.name, inner.args[0].value))
                    break
    return hits


def test_guard_matches_region_qualified_locale_literals():
    """The guard must not narrow back to bare primary subtags.

    prompts_icebreaker._prompt_lang_from_data matched with
    startswith("zh-tw") / ("zh-hk") / ("zh-hant") and went unnoticed while this
    predicate only accepted "zh".
    """
    for literal in ("zh", "zh-tw", "zh-TW", "zh-hk", "zh-hant", "en", "pt-br"):
        assert _is_locale_prefix_literal(literal), literal
    for literal in ("hello", "http", "prompt", "topic_state", ""):
        assert not _is_locale_prefix_literal(literal), literal


def test_no_hand_rolled_locale_normalizers_return():
    """Only config/prompts/_locale.py may sniff locale strings directly.

    The six normalizers this module replaced all matched locales with
    `startswith("zh")`-style checks, which is how "esperanto" came to read as
    Spanish and why Steam codes fell to English in four of them. Route new
    locale decisions through normalize_prompt_locale instead.
    """
    hits = _locale_predicate_functions()
    assert hits == [], (
        "hand-rolled locale matching found outside config/prompts/_locale.py: "
        + ", ".join(f"{f}:{fn} startswith({v!r})" for f, fn, v in hits)
    )
