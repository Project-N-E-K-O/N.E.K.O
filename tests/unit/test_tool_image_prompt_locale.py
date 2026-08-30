# -*- coding: utf-8 -*-
"""The tool-image channel speaks one language, and it is the user's.

Every model-facing string a tool's picture drags into the conversation used to
be an inline literal in ``main_logic/omni_offline_client/_tools.py``, split
down the middle: the caption and the budget-omission warning in English, the
eviction placeholder and its recall suffix in Chinese. A Japanese session got
both, and neither was Japanese.

These tests pin the two halves of the fix:

* ``config/prompts/prompts_tool.py`` carries one row per runtime locale, and
* ``_ToolingMixin`` resolves the session locale once per injected turn and
  renders all four strings from that row.

Expected strings are written out as literals rather than re-derived through
``_loc``. Deriving them would mutate the expectation in lockstep with the code
under test, so a resolver that picked the wrong row would stay green.
"""

from __future__ import annotations

import pytest

from config import _runtime
from config.prompts.prompts_tool import (
    TOOL_IMAGE_CAPTION,
    TOOL_IMAGE_DEFAULT_CAPTION,
    TOOL_IMAGE_HISTORY_PLACEHOLDER,
    TOOL_IMAGE_OMITTED_WARNING,
    TOOL_IMAGE_RECALL_HANDLE,
    TOOL_IMAGE_RECALL_HINT,
    normalize_tool_image_locale,
)
from main_logic.omni_offline_client._tools import _ToolingMixin
from main_logic.tool_calling import ToolImage, ToolResult


# The eight runtime locales, as config/prompts/_locale.py defines them, in the
# key scheme this module's tables use ('zh' for Simplified, not 'zh-CN').
PROMPT_LOCALE_KEYS = {"zh", "zh-TW", "en", "ja", "ko", "ru", "es", "pt"}

ALL_TABLES = {
    "TOOL_IMAGE_DEFAULT_CAPTION": TOOL_IMAGE_DEFAULT_CAPTION,
    "TOOL_IMAGE_CAPTION": TOOL_IMAGE_CAPTION,
    "TOOL_IMAGE_OMITTED_WARNING": TOOL_IMAGE_OMITTED_WARNING,
    "TOOL_IMAGE_RECALL_HANDLE": TOOL_IMAGE_RECALL_HANDLE,
    "TOOL_IMAGE_RECALL_HINT": TOOL_IMAGE_RECALL_HINT,
    "TOOL_IMAGE_HISTORY_PLACEHOLDER": TOOL_IMAGE_HISTORY_PLACEHOLDER,
}

REQUIRED_FIELDS = {
    "TOOL_IMAGE_DEFAULT_CAPTION": (),
    "TOOL_IMAGE_CAPTION": ("{tool_name}", "{call_id}", "{instruction}"),
    "TOOL_IMAGE_OMITTED_WARNING": ("{count}",),
    "TOOL_IMAGE_RECALL_HANDLE": ("{shot_id}",),
    "TOOL_IMAGE_RECALL_HINT": ("{recall_hint}",),
    "TOOL_IMAGE_HISTORY_PLACEHOLDER": ("{tool_name}", "{recall_suffix}"),
}


# ------------------------------------------------------------------ the tables

def test_every_table_covers_every_runtime_locale():
    for name, table in ALL_TABLES.items():
        assert set(table) == PROMPT_LOCALE_KEYS, name


def test_every_row_keeps_the_fields_its_call_site_formats():
    """A row missing a field silently drops it from what the model reads."""
    for name, table in ALL_TABLES.items():
        for locale, template in table.items():
            for field in REQUIRED_FIELDS[name]:
                assert field in template, f"{name}[{locale}] lost {field}"


def test_no_row_is_blank():
    for name, table in ALL_TABLES.items():
        for locale, template in table.items():
            assert template.strip(), f"{name}[{locale}]"


def test_traditional_chinese_is_not_a_copy_of_simplified():
    """The two Chinese rows are what issue #2500 is about: a zh-TW session
    reading Simplified glyphs is the regression this key exists to prevent.
    ``TOOL_IMAGE_RECALL_HINT`` is exempt -- it is a bare separator plus the
    tool's own text, with no Chinese words to convert."""
    for name, table in ALL_TABLES.items():
        if name == "TOOL_IMAGE_RECALL_HINT":
            continue
        assert table["zh"] != table["zh-TW"], name


# -------------------------------------------------------------- the normalizer

@pytest.fixture
def global_language(monkeypatch):
    """Bind config._runtime's global-language resolver for one test."""
    def _set(value):
        monkeypatch.setattr(
            _runtime, "_global_language_resolver", lambda: value, raising=False
        )
    return _set


@pytest.mark.parametrize(
    "given, expected",
    [
        ("zh-CN", "zh"),
        ("zh", "zh"),
        ("zh-TW", "zh-TW"),
        ("zh-HK", "zh-TW"),
        ("tchinese", "zh-TW"),   # Steam store code
        ("schinese", "zh"),
        ("ja-JP", "ja"),
        ("pt-BR", "pt"),
        ("en", "en"),
        ("klingon", "en"),
    ],
)
def test_normalizer_maps_a_session_locale_to_a_table_key(given, expected):
    assert normalize_tool_image_locale(given) == expected


@pytest.mark.parametrize("empty", [None, "", "   "])
def test_empty_session_locale_falls_back_to_the_global_language(
    empty, global_language
):
    """The offline client can inject a tool image before the manager has
    seeded ``user_language``; the UI language is a better guess than English."""
    global_language("zh-TW")
    assert normalize_tool_image_locale(empty) == "zh-TW"


def test_unbound_global_resolver_still_resolves(global_language):
    global_language(None)
    assert normalize_tool_image_locale(None) == "en"


# ----------------------------------------------------------------- the wiring

class _Client(_ToolingMixin):
    """Just enough of OmniOfflineClient to inject one tool-image turn."""

    def __init__(self, language):
        self._user_language_provider = lambda: language


def _result(*, images, output=None) -> ToolResult:
    return ToolResult(
        call_id="call_1",
        name="demo_tool",
        output={"ok": True} if output is None else output,
        images=list(images),
    )


def _inject(client, result):
    messages: list = []
    client._append_tool_result_images(messages, result)
    return messages


# The rendered strings below are literals on purpose (see module docstring).
# Each is the row this locale must select, formatted with the fixture values.
CAPTIONS = {
    "zh": "工具 demo_tool 返回的图片（call_id=call_1）：看小地图",
    "zh-TW": "工具 demo_tool 傳回的圖片（call_id=call_1）：看小地图",
    "ja": "ツール demo_tool の画像（call_id=call_1）：看小地图",
    "en": "Tool image from demo_tool (call_id=call_1): 看小地图",
}

DEFAULT_CAPTIONS = {
    "zh": "工具 demo_tool 返回的图片（call_id=call_1）：（工具返回的画面）",
    "zh-TW": "工具 demo_tool 傳回的圖片（call_id=call_1）：（工具傳回的畫面）",
    "ja": "ツール demo_tool の画像（call_id=call_1）：（ツールが返した画像）",
    "en": (
        "Tool image from demo_tool (call_id=call_1): "
        "(image returned by the tool)"
    ),
}

OMISSION_WARNINGS = {
    "zh": "因本轮共享图片预算已用尽，已省略 1 张工具图片",
    "zh-TW": "因本輪共用圖片預算已用盡，已省略 1 張工具圖片",
    "ja": "このターンの共有画像上限に達したため、ツール画像 1 枚を省略しました",
    "en": (
        "1 tool image(s) omitted because the shared turn image budget "
        "was exhausted"
    ),
}

PLACEHOLDERS = {
    "zh": "[工具 demo_tool 返回的画面已从上下文移除；图片只在产生它的那一轮可见]",
    "zh-TW": "[工具 demo_tool 傳回的畫面已從上下文移除；圖片僅在產生它的該輪可見]",
    "ja": (
        "[ツール demo_tool が返した画像はコンテキストから削除されました。"
        "この画像は生成されたターンでのみ表示されます]"
    ),
    "en": (
        "[Image returned by tool demo_tool was removed from context; "
        "the image was visible only in the turn that produced it]"
    ),
}

RECALL_SUFFIXES = {
    "zh": "；句柄 shot_7；稍后请调用截图召回工具。",
    "zh-TW": "；控制代碼 shot_7；稍后请调用截图召回工具。",
    "ja": "；ハンドル shot_7；稍后请调用截图召回工具。",
    "en": "; handle shot_7; 稍后请调用截图召回工具。",
}

LOCALES = ["zh", "zh-TW", "ja", "en"]


@pytest.mark.parametrize("locale", LOCALES)
def test_caption_is_written_in_the_session_locale(locale):
    client = _Client(locale)
    messages = _inject(
        client,
        _result(images=[ToolImage(data_b64="IMGDATA", vision_prompt="看小地图")]),
    )
    assert messages[-1]["content"][1]["text"] == CAPTIONS[locale]


@pytest.mark.parametrize("locale", LOCALES)
def test_the_stand_in_caption_is_written_in_the_session_locale(locale):
    """A tool that named no vision prompt still gets a text part, because
    several providers reject a bare image part."""
    client = _Client(locale)
    messages = _inject(
        client, _result(images=[ToolImage(data_b64="IMGDATA", vision_prompt="")]),
    )
    assert messages[-1]["content"][1]["text"] == DEFAULT_CAPTIONS[locale]


@pytest.mark.parametrize("locale", LOCALES)
def test_the_budget_omission_warning_is_written_in_the_session_locale(locale):
    client = _Client(locale)
    result = _result(images=[
        ToolImage(data_b64="A"), ToolImage(data_b64="B"), ToolImage(data_b64="C"),
    ])
    _inject(client, result)
    assert result.output["_image_warnings"] == [OMISSION_WARNINGS[locale]]


@pytest.mark.parametrize("locale", LOCALES)
def test_the_eviction_placeholder_is_written_in_the_session_locale(locale):
    client = _Client(locale)
    messages = _inject(client, _result(images=[ToolImage(data_b64="IMGDATA")]))
    client._release_tool_image_slots()
    assert messages[-1]["content"] == PLACEHOLDERS[locale]


@pytest.mark.parametrize("locale", LOCALES)
def test_the_recall_suffix_is_written_in_the_session_locale(locale):
    """The handle keeps the model's route back to the frame open. The hint is
    the tool's own text and rides through untranslated -- only the separator
    and the word for the handle are ours."""
    client = _Client(locale)
    messages = _inject(client, _result(
        images=[ToolImage(data_b64="IMGDATA")],
        output={"shot_id": "shot_7", "recall_hint": "稍后请调用截图召回工具。"},
    ))
    client._release_tool_image_slots()
    expected = PLACEHOLDERS[locale][:-1] + RECALL_SUFFIXES[locale] + "]"
    assert messages[-1]["content"] == expected


def test_one_turn_never_mixes_two_languages():
    """The bug this module exists for: an English caption next to a Chinese
    placeholder in the same tool call."""
    client = _Client("ja")
    result = _result(
        images=[ToolImage(data_b64="A"), ToolImage(data_b64="B"),
                ToolImage(data_b64="C")],
        output={"shot_id": "shot_7"},
    )
    messages = _inject(client, result)
    caption = messages[-1]["content"][1]["text"]
    warning = result.output["_image_warnings"][0]
    client._release_tool_image_slots()
    placeholder = messages[-1]["content"]

    for rendered in (caption, warning, placeholder):
        assert "Tool image from" not in rendered
        assert "omitted because" not in rendered
        assert "removed from context" not in rendered
        assert "工具" not in rendered
        assert "画面" not in rendered


def test_a_provider_that_raises_does_not_break_the_tool_loop(global_language):
    """A tool call that otherwise succeeded must not die on a locale lookup."""
    global_language("ja")

    def _boom():
        raise RuntimeError("session gone")

    client = _Client(None)
    client._user_language_provider = _boom
    messages = _inject(client, _result(images=[ToolImage(data_b64="IMGDATA")]))
    assert messages[-1]["content"][1]["text"] == DEFAULT_CAPTIONS["ja"]
