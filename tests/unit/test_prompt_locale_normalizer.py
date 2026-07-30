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
        got = fn(raw)
        assert got == expected, (
            f"{fn.__module__}.{fn.__name__}: {raw!r} -> {got!r}, expected {expected!r}"
        )


def test_none_takes_module_default():
    """None is empty input, so each module returns its own default."""
    assert _normalize_prompt_language(None) == "en"
    assert _normalize_prompt_lang(None) == "zh"
    assert _normalize_lang(None) == "en"
    assert _normalize_memory_prompt_lang(None) == "en"
    assert normalize_badminton_prompt_locale(None) == "zh-CN"


def test_keep_traditional_false_collapses_to_simplified():
    """keep_traditional=False must route Traditional Chinese to `simplified`.

    This is what protects Traditional Chinese users in modules whose prompt
    dicts have no 'zh-TW' template: resolving to zh-TW there would miss the key
    and drop to the English fallback in `_loc`.
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


def _locale_predicate_functions():
    """Yield (file, function) for prompt functions doing their own locale sniffing.

    Discovered from the AST rather than listed, so a newly hand-rolled
    normalizer is caught without editing this test.
    """
    locale_prefixes = {"zh", "en", "ja", "ko", "ru", "es", "pt"}
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
                    and inner.args[0].value.lower().rstrip("-") in locale_prefixes
                ):
                    hits.append((path.name, node.name, inner.args[0].value))
                    break
    return hits


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
