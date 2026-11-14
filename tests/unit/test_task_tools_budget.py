"""What a session pays on every request for having NekoClaw switched on.

The opening sentence and the definitions of the two task tools ride on every
request of the main dialogue model. These tests hold their size: the Chinese
total to the limit the design set, the other languages to what they measure
today, and the opening sentence of a session without NekoClaw to its own.
A longer text has to raise a number here on purpose.
"""

from __future__ import annotations

import json

import pytest

from config.prompts.prompts_sys import SESSION_INIT_PROMPT, SESSION_INIT_PROMPT_AGENT
from config.prompts.prompts_task_tools import (
    DISPATCH_TOOL_DESCRIPTION,
    DISPATCH_TOOL_NAME,
    DISPATCH_TOOL_TASK_DESCRIPTION,
    QUERY_TOOL_DESCRIPTION,
    QUERY_TOOL_NAME,
    QUERY_TOOL_QUESTION_DESCRIPTION,
    TASK_TOOL_EMPTY,
    TASK_TOOL_QUERY_UNREACHABLE,
    TASK_TOOL_UNREACHABLE,
)
from main_logic.core.tool_calling import ToolCallingMixin
from main_logic.tool_calling import ToolRegistry
from utils.tokenize import count_tokens

pytestmark = pytest.mark.unit

LANGUAGES = ["zh", "zh-TW", "en", "ja", "ko", "ru", "es", "pt"]

# Opening sentence of a session with NekoClaw on, plus both tool definitions
# as they go out (compact JSON). For Chinese: the design's 221, plus the 15
# of the clause the dispatch description gained ("looking something up or
# checking a state counts; what you already know, answer yourself"), which
# the owner allowed up to 20 for and approved on 2026-11-14 after it was
# measured. The others are today's measure with two tokens to spare.
ON_LIMIT = {"zh": 236, "zh-TW": 259, "en": 236, "ja": 317, "ko": 273, "ru": 257, "es": 250, "pt": 250}
# Opening sentence of a session without NekoClaw: it carries the one sentence
# about speaking before a tool call and nothing else of this.
OFF_LIMIT = {"zh": 34, "zh-TW": 36, "en": 30, "ja": 52, "ko": 43, "ru": 40, "es": 36, "pt": 34}


def _registered_tools(lang: str) -> list[dict]:
    """The two definitions exactly as the session registers and sends them."""
    manager = object.__new__(ToolCallingMixin)
    manager.user_language = lang
    manager.tool_registry = ToolRegistry()
    manager._is_agent_enabled = lambda: True
    assert manager._register_task_tools() is True
    tools = [manager.tool_registry.get(name) for name in (DISPATCH_TOOL_NAME, QUERY_TOOL_NAME)]
    assert all(tool is not None for tool in tools)
    return [tool.to_openai_chat() for tool in tools]


def _compact(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


@pytest.mark.parametrize("lang", LANGUAGES)
def test_opening_sentence_and_both_tool_definitions_stay_within_the_limit(lang):
    opening = count_tokens(SESSION_INIT_PROMPT_AGENT[lang])
    definitions = sum(count_tokens(_compact(tool)) for tool in _registered_tools(lang))

    assert opening + definitions <= ON_LIMIT[lang], (lang, opening, definitions)


def test_the_chinese_limit_is_the_one_the_design_set():
    assert ON_LIMIT["zh"] == 221 + 15


@pytest.mark.parametrize("lang", LANGUAGES)
def test_opening_sentence_without_nekoclaw_stays_within_its_limit(lang):
    assert count_tokens(SESSION_INIT_PROMPT[lang]) <= OFF_LIMIT[lang]


@pytest.mark.parametrize("lang", LANGUAGES)
def test_both_opening_sentences_end_with_the_same_sentence_about_speaking_first(lang):
    plain, agent = SESSION_INIT_PROMPT[lang], SESSION_INIT_PROMPT_AGENT[lang]
    # The last sentence of the plain one is the last sentence of the other.
    shared = 0
    while shared < min(len(plain), len(agent)) and plain[-1 - shared] == agent[-1 - shared]:
        shared += 1
    tail = plain[len(plain) - shared :]

    assert count_tokens(tail) >= 6, (lang, tail)
    # Neither names a capability, a plugin or a tool by name.
    for text in (plain, agent):
        assert DISPATCH_TOOL_NAME not in text and QUERY_TOOL_NAME not in text
        assert text.count("{name}") == 1


@pytest.mark.parametrize(
    "table",
    [
        SESSION_INIT_PROMPT,
        SESSION_INIT_PROMPT_AGENT,
        DISPATCH_TOOL_DESCRIPTION,
        DISPATCH_TOOL_TASK_DESCRIPTION,
        QUERY_TOOL_DESCRIPTION,
        QUERY_TOOL_QUESTION_DESCRIPTION,
        TASK_TOOL_UNREACHABLE,
        TASK_TOOL_QUERY_UNREACHABLE,
        TASK_TOOL_EMPTY,
    ],
)
def test_every_text_exists_in_all_eight_languages(table):
    assert set(table) == set(LANGUAGES)
    assert all(isinstance(text, str) and text.strip() for text in table.values())
    # A translation that was never written shows up as a copy of another row.
    assert len(set(table.values())) == len(LANGUAGES)


def test_the_tools_take_one_sentence_and_no_identifier():
    dispatch, query = _registered_tools("en")

    assert dispatch["function"]["parameters"] == {
        "type": "object",
        "properties": {"task": {"type": "string", "description": DISPATCH_TOOL_TASK_DESCRIPTION["en"]}},
        "required": ["task"],
    }
    assert query["function"]["parameters"] == {
        "type": "object",
        "properties": {"question": {"type": "string", "description": QUERY_TOOL_QUESTION_DESCRIPTION["en"]}},
    }
