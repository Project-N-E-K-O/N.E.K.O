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

"""Traditional Chinese fact-extraction prompts (issue #2500, batch 1).

Fact text is persisted to facts.json and indexed for BM25/embedding recall, so
these two templates were the first to backfill: a wrong-language instruction here
contaminates stored data rather than just one reply.

The call sites in memory/facts.py already pass ``get_global_language_full()``, so a
zh-TW user's language code reaches these getters intact — what used to collapse it
was a per-call-site line inside ``_localized_fact_extraction_prompt``. These tests
pin both that the templates exist and that nothing reintroduces the collapse.
"""
from __future__ import annotations

import re

import pytest

from config.prompts.prompts_memory import (
    FACT_EXTRACTION_AI_AWARE_PROMPT,
    FACT_EXTRACTION_PROMPT,
    get_fact_extraction_ai_aware_prompt,
    get_fact_extraction_prompt,
)

TABLES = {
    "basic": FACT_EXTRACTION_PROMPT,
    "ai_aware": FACT_EXTRACTION_AI_AWARE_PROMPT,
}
GETTERS = {
    "basic": get_fact_extraction_prompt,
    "ai_aware": get_fact_extraction_ai_aware_prompt,
}

# The conversation watermark stays Simplified in every locale — it is a fixed
# literal the runtime matches on, not user-facing copy. See
# docs/contributing/developer-notes.md "Prompt watermark".
WATERMARK_OPEN = "======以下为对话======"
WATERMARK_CLOSE = "======以上为对话======"

# Characters that differ between the scripts, used to tell them apart without
# depending on any particular sentence surviving a copy edit.
TRADITIONAL_MARKERS = ("擷取", "資訊", "陣列", "關於", "實")
SIMPLIFIED_MARKERS = ("提取", "信息", "数组", "关于", "实")


@pytest.mark.parametrize("name", sorted(TABLES))
def test_traditional_template_exists(name):
    assert "zh-TW" in TABLES[name], f"{name} still has no zh-TW template"


@pytest.mark.parametrize("name", sorted(TABLES))
def test_traditional_template_is_actually_traditional(name):
    """Not a copy of the Simplified one, and not English either."""
    table = TABLES[name]
    traditional = table["zh-TW"]
    assert traditional != table["zh"]
    assert traditional != table["en"]
    for marker in TRADITIONAL_MARKERS:
        assert marker in traditional, f"{name} zh-TW missing {marker!r}"


@pytest.mark.parametrize("name", sorted(TABLES))
def test_simplified_template_is_untouched(name):
    """Backfilling Traditional must not have rewritten the Simplified copy."""
    simplified = TABLES[name]["zh"]
    for marker in SIMPLIFIED_MARKERS:
        assert marker in simplified, f"{name} zh lost {marker!r}"


@pytest.mark.parametrize("name", sorted(GETTERS))
def test_full_locale_resolves_to_traditional(name):
    """A zh-TW code must reach the Traditional template, not collapse to zh.

    This is the regression this batch exists to prevent: the collapse used to live
    in ``_localized_fact_extraction_prompt`` and was invisible from the call sites,
    which have been passing the full locale all along.
    """
    resolved = GETTERS[name]("zh-TW")
    assert resolved == TABLES[name]["zh-TW"]
    assert resolved != TABLES[name]["zh"]


@pytest.mark.parametrize("name", sorted(GETTERS))
@pytest.mark.parametrize("code", ["zh-TW", "zh-Hant", "zh-HK", "tchinese", "zh_TW"])
def test_traditional_variants_all_resolve_to_traditional(name, code):
    """Every spelling the normalizer folds into zh-TW gets the Traditional text."""
    assert GETTERS[name](code) == TABLES[name]["zh-TW"], code


@pytest.mark.parametrize("name", sorted(GETTERS))
@pytest.mark.parametrize("code", ["zh", "zh-CN", "zh-Hans", "schinese"])
def test_simplified_variants_still_resolve_to_simplified(name, code):
    assert GETTERS[name](code) == TABLES[name]["zh"], code


@pytest.mark.parametrize("name", sorted(TABLES))
def test_watermark_stays_simplified_in_every_locale(name):
    """Including zh-TW: the watermark is a matched literal, not translated copy."""
    for locale, text in TABLES[name].items():
        assert WATERMARK_OPEN in text, f"{name}/{locale} lost the opening watermark"
        assert WATERMARK_CLOSE in text, f"{name}/{locale} lost the closing watermark"


@pytest.mark.parametrize("name", sorted(TABLES))
def test_traditional_template_keeps_every_placeholder(name):
    """Placeholders are filled by str.replace, so a missing one silently no-ops."""
    table = TABLES[name]
    placeholders = lambda text: set(re.findall(r"\{[A-Z_]+\}", text))
    assert placeholders(table["zh-TW"]) == placeholders(table["zh"])


@pytest.mark.parametrize("name", sorted(TABLES))
def test_machine_readable_tokens_stay_ascii(name):
    """JSON field names and enum values must not be translated.

    The extractor parses these back out, so localizing `entity` or
    `user_observation` would break parsing rather than change wording.
    """
    traditional = TABLES[name]["zh-TW"]
    for token in ("importance", "entity", "event_when", "offset", "unit",
                  "master", "neko", "relationship"):
        assert token in traditional, f"{name} zh-TW lost {token!r}"
    if name == "ai_aware":
        for token in ("source", "user_observation", "ai_disclosure"):
            assert token in traditional, f"{name} zh-TW lost {token!r}"


@pytest.mark.parametrize("name", sorted(GETTERS))
def test_other_locales_are_unaffected(name):
    for locale in ("en", "ja", "ko", "ru", "es", "pt"):
        assert GETTERS[name](locale) == TABLES[name][locale], locale


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("human_text", "ai_text", "expected"),
    [
        (
            "我喜歡貓",
            "This is a much longer English assistant response " * 20,
            [("basic", "zh-TW"), ("aware", "en")],
        ),
        (
            "This is a much longer English user statement " * 20,
            "我喜歡貓",
            [("basic", "en"), ("aware", "zh-TW")],
        ),
    ],
)
async def test_fact_extractors_detect_locale_from_persisted_role(
    monkeypatch,
    human_text,
    ai_text,
    expected,
):
    from unittest.mock import AsyncMock

    from config.prompts import prompts_memory as prompt_module
    from memory import facts
    from utils.language_utils import language_context
    from utils.llm_client import AIMessage, HumanMessage

    selected = []

    def basic_prompt(lang):
        selected.append(("basic", lang))
        return "{CONVERSATION} {LANLAN_NAME} {MASTER_NAME}"

    def aware_prompt(lang):
        selected.append(("aware", lang))
        return "{CONVERSATION} {KNOWN_POOL} {LANLAN_NAME} {MASTER_NAME}"

    class ConfigManager:
        async def aget_character_data(self):
            return (None, None, None, None, {"human": "Alice"}, None, None, None, None)

    store = object.__new__(facts.FactStore)
    store._config_manager = ConfigManager()
    store._allm_call_with_retries = AsyncMock(return_value=[])
    messages = [
        HumanMessage(content=human_text),
        AIMessage(content=ai_text),
    ]

    monkeypatch.setattr(facts, "get_fact_extraction_prompt", basic_prompt)
    monkeypatch.setattr(
        prompt_module,
        "get_fact_extraction_ai_aware_prompt",
        aware_prompt,
    )

    with language_context("zh-TW"):
        await store._allm_extract_facts("Neko", messages)
        await store._allm_extract_facts_with_known_pool("Neko", messages, [])

    assert selected == expected
