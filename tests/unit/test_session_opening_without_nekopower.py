# -*- coding: utf-8 -*-
"""The opening of a session with the NekoPower master switch off says she cannot act on the computer.

With the master switch off a session has no task tool and no turn of hers is checked
afterwards (``nekopower_on`` on the agent side). Before, its opening was only the
role-play sentence, so she would answer "on it" to a request to open a file and
nothing happened. The opening now adds ``SESSION_INIT_NO_NEKOPOWER``. Only the master
switch is looked at (owner, 2026-11-26): the task agent is to start with it alone.
When asked, she tells the user NekoPower is off and suggests switching it on; the
built-in tools she still has are named as staying in use (owner, 2026-11-26).

The real ``NotifyMixin`` methods run here, ``_is_agent_enabled`` included.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import memory.anti_repeat as anti_repeat_module
import memory.user_directives as user_directives_module
from config.prompts.prompts_sys import (
    SESSION_INIT_NO_NEKOPOWER,
    SESSION_INIT_PROMPT,
    SESSION_INIT_PROMPT_AGENT,
)
from config.prompts.prompts_task_tools import DISPATCH_TOOL_NAME
from main_logic import public_knowledge
from main_logic.core.notify import NotifyMixin
from main_logic.core.tool_calling import ToolCallingMixin
from main_logic.tool_calling import ToolRegistry
from utils.http import internal_client

NAME = "Lan"
PERSONA = "<persona>"
CAPABILITIES = ("computer_use_enabled", "browser_use_enabled", "user_plugin_enabled")
PROMPT_KEYS = ("zh", "zh-TW", "en", "ja", "ko", "ru", "es", "pt")
# prompts_sys key -> the locale file whose panel labels the sentence names.
LOCALE_FILES = {
    "zh": "zh-CN", "zh-TW": "zh-TW", "en": "en", "ja": "ja",
    "ko": "ko", "ru": "ru", "es": "es", "pt": "pt",
}
LOCALES_DIR = Path(__file__).resolve().parents[2] / "static" / "locales"


class _Session(NotifyMixin):
    def __init__(self, flags, user_language="zh-CN"):
        self.user_language = user_language
        self.lanlan_name = NAME
        self.lanlan_prompt = PERSONA
        self.agent_flags = dict(flags)


class _NoDirectives:
    def render_prompt_block(self, name, lang):
        return ""


class _NoRecentTopics:
    async def apreload(self, name):
        return None

    def top_recent_topics(self, name):
        return []


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    """No directive / topic blocks, no disk, and an empty brief instead of HTTP."""

    class _BriefClient:
        async def get(self, url, params=None, timeout=None):
            return SimpleNamespace(status_code=200, json=lambda: {"success": True, "lines": []})

    monkeypatch.setattr(user_directives_module, "get_user_directives_manager", _NoDirectives)
    monkeypatch.setattr(anti_repeat_module, "get_anti_repeat_corpus", _NoRecentTopics)
    monkeypatch.setattr(internal_client, "get_internal_http_client", _BriefClient)


def _flags(master, *capabilities):
    flags = {"agent_enabled": master}
    flags.update({name: name in capabilities for name in CAPABILITIES})
    return flags


async def _opening(flags):
    return await _Session(flags)._build_initial_prompt()


async def test_nekopower_off_says_she_cannot_act_on_the_computer():
    prompt = await _opening(_flags(False))
    assert prompt == SESSION_INIT_PROMPT["zh"].format(name=NAME) + SESSION_INIT_NO_NEKOPOWER["zh"] + PERSONA


@pytest.mark.parametrize("capability", CAPABILITIES)
async def test_a_capability_on_with_the_master_switch_off_is_still_off(capability):
    # A capability left on while the master switch is off does nothing either.
    prompt = await _opening(_flags(False, capability))
    assert prompt == SESSION_INIT_PROMPT["zh"].format(name=NAME) + SESSION_INIT_NO_NEKOPOWER["zh"] + PERSONA


async def test_the_master_switch_on_gives_the_opening_of_nekopower_whatever_capability_is_on():
    # Only the master switch is looked at: with it on and no capability, NekoPower is on, and
    # the opening is its own, with no note that she cannot act.
    prompt = await _opening(_flags(True))
    assert prompt == SESSION_INIT_PROMPT_AGENT["zh"].format(name=NAME) + PERSONA
    assert SESSION_INIT_NO_NEKOPOWER["zh"] not in prompt


@pytest.mark.parametrize("capability", CAPABILITIES)
async def test_nekopower_fully_on_keeps_its_own_opening(capability):
    prompt = await _opening(_flags(True, capability))
    assert prompt.startswith(SESSION_INIT_PROMPT_AGENT["zh"].format(name=NAME) + PERSONA)
    assert SESSION_INIT_NO_NEKOPOWER["zh"] not in prompt


async def test_the_sentence_is_in_the_session_language():
    prompt = await _Session(_flags(False), user_language="ja")._build_initial_prompt()
    assert prompt == SESSION_INIT_PROMPT["ja"].format(name=NAME) + SESSION_INIT_NO_NEKOPOWER["ja"] + PERSONA


def test_every_prompt_language_has_its_own_sentence():
    table = SESSION_INIT_NO_NEKOPOWER
    assert set(table) == set(PROMPT_KEYS)
    assert all(table[key].strip() for key in PROMPT_KEYS)
    # No row falls back to another language's text.
    assert len({table[key] for key in PROMPT_KEYS}) == len(PROMPT_KEYS)


# Languages whose sentences are parted by a space: the note follows the last sentence of
# SESSION_INIT_PROMPT directly, so it starts with one ("...first. NekoPower is off").
SPACED = ("en", "ko", "ru", "es", "pt")


@pytest.mark.parametrize("key", PROMPT_KEYS)
def test_the_note_is_parted_from_the_sentence_before_it_as_the_language_writes(key):
    note = SESSION_INIT_NO_NEKOPOWER[key]
    if key in SPACED:
        assert note.startswith(" ") and not note.startswith("  "), key
    else:
        assert not note[:1].isspace(), key


# What each language calls the task tools, as SESSION_INIT_PROMPT_AGENT does.
TASK_TOOLS = {
    "zh": "任务工具", "zh-TW": "任務工具", "en": "task tool", "ja": "タスクツール",
    "ko": "작업 도구", "ru": "инструмент", "es": "herramientas de tareas", "pt": "ferramentas de tarefas",
}
# She asks the user herself to authorize her to switch NekoPower on (owner, 2026-11-26).
SUGGESTS_SWITCHING_ON = {
    "zh": "主动引导对方授权你把喵能力打开", "zh-TW": "主動引導對方授權你把喵能力打開",
    "en": "on your own ask them to authorize you to switch NekoPower on",
    "ja": "にゃんパワーをオンにする許可をくれるよう自分から頼んで", "ko": "냥파워를 켤 수 있게 허락해 달라고 먼저 부탁하세요",
    "ru": "сами попросите разрешить вам включить NekoPower", "es": "pídele por tu cuenta que te autorice a activar NekoPower",
    "pt": "peça por iniciativa própria que autorizem você a ativar o NekoPower",
}
# The built-in tool every session without NekoPower has (recall_memory), as each language names it.
RECALL = {
    "zh": "回忆记忆", "zh-TW": "回想記憶", "en": "recalling memories", "ja": "記憶の呼び出し",
    "ko": "기억 떠올리기", "ru": "воспоминаниями", "es": "recordar memorias", "pt": "lembrar memórias",
}
# The knowledge base, as each language would name it: there only once a knowledge pack is
# imported (not the usual state), so the sentence leaves it to "and the like".
KNOWLEDGE_BASE = {
    "zh": "知识", "zh-TW": "知識", "en": "knowledge", "ja": "知識",
    "ko": "지식", "ru": "знаний", "es": "conocimiento", "pt": "conhecimento",
}
# Web search, as the first wording named it: Gemini Live searches on its own whatever NekoPower is.
WEB_SEARCH = {
    "zh": "上网查", "zh-TW": "上網查", "en": "online", "ja": "ネットで調べ",
    "ko": "인터넷 검색", "ru": "поискать в интернете", "es": "buscar en internet", "pt": "pesquisar na internet",
}


@pytest.mark.parametrize("key", PROMPT_KEYS)
def test_she_has_no_task_tools_by_the_name_the_nekopower_opening_uses(key):
    assert TASK_TOOLS[key] in SESSION_INIT_NO_NEKOPOWER[key], key
    assert TASK_TOOLS[key] in SESSION_INIT_PROMPT_AGENT[key], key


@pytest.mark.parametrize("key", PROMPT_KEYS)
def test_she_suggests_switching_nekopower_on(key):
    assert SUGGESTS_SWITCHING_ON[key] in SESSION_INIT_NO_NEKOPOWER[key], key


@pytest.mark.parametrize("key", PROMPT_KEYS)
def test_she_keeps_using_the_built_in_tool_every_session_has(key):
    assert RECALL[key] in SESSION_INIT_NO_NEKOPOWER[key], key


@pytest.mark.parametrize("key", PROMPT_KEYS)
def test_the_knowledge_base_is_not_named(key):
    # With no knowledge pack imported there is no such tool: named, she might claim to search it.
    assert KNOWLEDGE_BASE[key] not in SESSION_INIT_NO_NEKOPOWER[key], key


@pytest.mark.parametrize("pack_imported", (True, False))
def test_the_tool_named_is_there_without_nekopower_whatever_is_imported(monkeypatch, pack_imported):
    monkeypatch.delenv("NEKO_DISABLE_BUILTIN_TOOLS", raising=False)
    monkeypatch.setattr(public_knowledge, "tool_available", lambda: pack_imported)
    monkeypatch.setattr(public_knowledge, "add_availability_listener", lambda owner: None)
    manager = object.__new__(ToolCallingMixin)
    manager.user_language = "zh"
    manager.tool_registry = ToolRegistry()
    manager._is_agent_enabled = lambda: False
    manager._register_builtin_tools()

    assert manager.tool_registry.get("recall_memory") is not None
    # The knowledge base comes and goes with the packs: why the sentence does not name it.
    assert (manager.tool_registry.get(public_knowledge.TOOL_NAME) is not None) is pack_imported
    assert manager.tool_registry.get(DISPATCH_TOOL_NAME) is None


@pytest.mark.parametrize("key", PROMPT_KEYS)
def test_web_search_is_not_ruled_out(key):
    assert WEB_SEARCH[key] not in SESSION_INIT_NO_NEKOPOWER[key], key


# What she tells the user when asked: that NekoPower is off and she cannot do it now.
TELLS_IT_IS_OFF = {
    "zh": "告诉对方喵能力没开", "zh-TW": "告訴對方喵能力沒開",
    "en": "tell them plainly that NekoPower is off",
    "ja": "にゃんパワーがオフでいまはできないと正直に伝え",
    "ko": "냥파워가 꺼져 있어 지금은 할 수 없다고 솔직하게 말하고",
    "ru": "честно скажите, что NekoPower выключен",
    "es": "di con franqueza que NekoPower está desactivado",
    "pt": "diga com franqueza que o NekoPower está desativado",
}


@pytest.mark.parametrize("key", PROMPT_KEYS)
def test_she_tells_the_user_that_nekopower_is_off(key):
    assert TELLS_IT_IS_OFF[key] in SESSION_INIT_NO_NEKOPOWER[key], key


@pytest.mark.parametrize("key", PROMPT_KEYS)
def test_nekopower_goes_by_its_panel_name(key):
    toggles = json.loads((LOCALES_DIR / f"{LOCALE_FILES[key]}.json").read_text(encoding="utf-8"))["settings"]["toggles"]
    # Why she has no task tools, what she tells the user, and what she suggests switching on.
    assert SESSION_INIT_NO_NEKOPOWER[key].count(toggles["agentMaster"]) >= 3, key
    assert toggles["agentMaster"] in TELLS_IT_IS_OFF[key], key
    assert toggles["agentMaster"] in SUGGESTS_SWITCHING_ON[key], key
