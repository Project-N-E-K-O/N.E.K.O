# -*- coding: utf-8 -*-
"""The opening of a session without NekoPower fully on says she cannot act on the computer.

Without NekoPower fully on (the master switch and at least one capability switch,
``NotifyMixin._is_agent_enabled``) a session has no task tool and no turn of hers is
checked afterwards (``nekopower_on`` on the agent side). Before, its opening was only
the role-play sentence, so she would answer "on it" to a request to open a file and
nothing happened. The opening now adds ``SESSION_INIT_NO_NEKOPOWER``, or, when the
master switch alone is on, ``SESSION_INIT_NEKOPOWER_NO_CAPABILITY``.

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
    SESSION_INIT_NEKOPOWER_NO_CAPABILITY,
    SESSION_INIT_NO_NEKOPOWER,
    SESSION_INIT_PROMPT,
    SESSION_INIT_PROMPT_AGENT,
)
from main_logic.core.notify import NotifyMixin
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


async def test_master_switch_alone_names_the_capability_switches():
    prompt = await _opening(_flags(True))
    assert prompt == (
        SESSION_INIT_PROMPT["zh"].format(name=NAME) + SESSION_INIT_NEKOPOWER_NO_CAPABILITY["zh"] + PERSONA
    )


@pytest.mark.parametrize("capability", CAPABILITIES)
async def test_a_capability_off_with_the_master_switch_is_still_off(capability):
    # A capability left on while the master switch is off does nothing either.
    prompt = await _opening(_flags(False, capability))
    assert SESSION_INIT_NO_NEKOPOWER["zh"] in prompt
    assert SESSION_INIT_NEKOPOWER_NO_CAPABILITY["zh"] not in prompt


@pytest.mark.parametrize("capability", CAPABILITIES)
async def test_nekopower_fully_on_keeps_its_own_opening(capability):
    prompt = await _opening(_flags(True, capability))
    assert prompt.startswith(SESSION_INIT_PROMPT_AGENT["zh"].format(name=NAME) + PERSONA)
    assert SESSION_INIT_NO_NEKOPOWER["zh"] not in prompt
    assert SESSION_INIT_NEKOPOWER_NO_CAPABILITY["zh"] not in prompt


async def test_the_sentence_is_in_the_session_language():
    prompt = await _Session(_flags(True), user_language="ja")._build_initial_prompt()
    assert SESSION_INIT_NEKOPOWER_NO_CAPABILITY["ja"] in prompt


@pytest.mark.parametrize("table", (SESSION_INIT_NO_NEKOPOWER, SESSION_INIT_NEKOPOWER_NO_CAPABILITY))
def test_every_prompt_language_has_its_own_sentence(table):
    assert set(table) == set(PROMPT_KEYS)
    assert all(table[key].strip() for key in PROMPT_KEYS)
    # No row falls back to another language's text.
    assert len({table[key] for key in PROMPT_KEYS}) == len(PROMPT_KEYS)


# What each language calls the task tools, as SESSION_INIT_PROMPT_AGENT does.
TASK_TOOLS = {
    "zh": "任务工具", "zh-TW": "任務工具", "en": "task tool", "ja": "タスクツール",
    "ko": "작업 도구", "ru": "инструмент", "es": "herramientas de tareas", "pt": "ferramentas de tarefas",
}
# The clause that ends the rule once task tools appear (NekoPower switched on mid-session).
ONCE_THEY_APPEAR = {
    "zh": "之后有了任务工具", "zh-TW": "之後有了任務工具", "en": "If task tools appear later",
    "ja": "あとでタスクツールが使えるようになったら", "ko": "나중에 작업 도구가 생기면",
    "ru": "Если инструменты задач появятся позже", "es": "Si más adelante aparecen herramientas de tareas",
    "pt": "Se mais tarde aparecerem ferramentas de tarefas",
}
# Web search, as the first wording named it: Gemini Live searches on its own whatever NekoPower is.
WEB_SEARCH = {
    "zh": "上网查", "zh-TW": "上網查", "en": "online", "ja": "ネットで調べ",
    "ko": "인터넷 검색", "ru": "поискать в интернете", "es": "buscar en internet", "pt": "pesquisar na internet",
}


@pytest.mark.parametrize("table", (SESSION_INIT_NO_NEKOPOWER, SESSION_INIT_NEKOPOWER_NO_CAPABILITY))
@pytest.mark.parametrize("key", PROMPT_KEYS)
def test_the_rule_holds_while_she_has_no_task_tools_and_ends_once_she_has(table, key):
    # Switching NekoPower on mid-session brings the task tools but leaves this opening as it
    # was: the sentence says "without task tools ..." and "once there are task tools, use them".
    assert TASK_TOOLS[key] in table[key], key
    assert TASK_TOOLS[key] in SESSION_INIT_PROMPT_AGENT[key], key
    assert ONCE_THEY_APPEAR[key] in table[key], key


@pytest.mark.parametrize("table", (SESSION_INIT_NO_NEKOPOWER, SESSION_INIT_NEKOPOWER_NO_CAPABILITY))
@pytest.mark.parametrize("key", PROMPT_KEYS)
def test_web_search_is_not_ruled_out(table, key):
    assert WEB_SEARCH[key] not in table[key], key


@pytest.mark.parametrize("key", PROMPT_KEYS)
def test_the_sentences_name_the_switches_as_the_panel_does(key):
    toggles = json.loads((LOCALES_DIR / f"{LOCALE_FILES[key]}.json").read_text(encoding="utf-8"))["settings"]["toggles"]
    assert toggles["agentMaster"] in SESSION_INIT_NO_NEKOPOWER[key]
    for label in ("agentMaster", "keyboardControl", "browserUse", "userPlugin"):
        assert toggles[label] in SESSION_INIT_NEKOPOWER_NO_CAPABILITY[key], (key, label)
