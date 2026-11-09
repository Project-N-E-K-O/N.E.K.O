# -*- coding: utf-8 -*-
"""Session-opening ``prompts_sys`` templates reach Traditional users (issue #2500 step 2).

``NotifyMixin._build_initial_prompt`` and ``_fetch_active_agent_tasks_prompt``
looked up ``SESSION_INIT_PROMPT`` / ``SESSION_INIT_PROMPT_AGENT`` and the
``AGENT_TASKS_*`` / ``AGENT_TASK_STATUS_*`` tables with a *short* code, which
folds ``zh-TW`` into ``zh``. Every one of those tables has a ``zh-TW`` row, so
Traditional users got the Simplified one. Both lookups now go through
``normalize_sys_prompt_locale``, which keeps ``zh-TW`` and resolves every other
input to the key the short code gave before.

Every case drives the real method and compares the rendered prompt; pinning the
normalizer argument alone would not show which row was actually rendered.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import memory.anti_repeat as anti_repeat_module
import memory.user_directives as user_directives_module
from config.prompts.prompts_sys import (
    AGENT_TASK_STATUS_QUEUED,
    AGENT_TASK_STATUS_RUNNING,
    AGENT_TASKS_HEADER,
    AGENT_TASKS_NOTICE,
    SESSION_INIT_PROMPT,
    SESSION_INIT_PROMPT_AGENT,
)
from main_logic.core.notify import NotifyMixin
from utils.http import internal_client
from utils.language_utils import normalize_language_code

TRADITIONAL_SPELLINGS = ("zh-TW", "zh_TW", "zh-HK", "zh-Hant", "zh-Hant-TW", "tchinese")

# (user_language, prompts_sys key it must keep rendering)
UNCHANGED_LOCALES = (
    ("zh-CN", "zh"),
    ("zh", "zh"),
    ("zh-Hans", "zh"),
    ("schinese", "zh"),
    ("en", "en"),
    ("ja", "ja"),
    ("ko", "ko"),
    ("ru", "ru"),
    ("es", "es"),
    ("pt", "pt"),
    ("pt-BR", "pt"),
    ("fr", "en"),
    ("", "en"),
    (None, "en"),
)

SYS_TABLES = {
    "SESSION_INIT_PROMPT": SESSION_INIT_PROMPT,
    "SESSION_INIT_PROMPT_AGENT": SESSION_INIT_PROMPT_AGENT,
    "AGENT_TASKS_HEADER": AGENT_TASKS_HEADER,
    "AGENT_TASKS_NOTICE": AGENT_TASKS_NOTICE,
    "AGENT_TASK_STATUS_RUNNING": AGENT_TASK_STATUS_RUNNING,
    "AGENT_TASK_STATUS_QUEUED": AGENT_TASK_STATUS_QUEUED,
}

NAME = "Lan"
PERSONA = "<persona>"
ACTIVE_TASKS = [
    {"status": "running", "params": {"query": "open the map"}},
    {"status": "queued", "params": {"instruction": "pause the music"}},
    {"status": "completed", "params": {"query": "already finished"}},
]


class _PromptOwner(NotifyMixin):
    def __init__(self, user_language, *, agent_enabled):
        self.user_language = user_language
        self.lanlan_name = NAME
        self.lanlan_prompt = PERSONA
        self._agent_enabled = agent_enabled

    def _is_agent_enabled(self):
        return self._agent_enabled


class _NoDirectives:
    def render_prompt_block(self, name, lang):
        return ""


class _NoRecentTopics:
    async def apreload(self, name):
        return None

    def top_recent_topics(self, name):
        return []


class _TasksClient:
    async def get(self, url, timeout=None):
        return SimpleNamespace(status_code=200, json=lambda: {"tasks": ACTIVE_TASKS})


@pytest.fixture(autouse=True)
def _only_sys_prompt_inputs(monkeypatch):
    """Empty directive / topic blocks (and no disk), and a canned agent task list (no HTTP)."""
    monkeypatch.setattr(user_directives_module, "get_user_directives_manager", _NoDirectives)
    monkeypatch.setattr(anti_repeat_module, "get_anti_repeat_corpus", _NoRecentTopics)
    monkeypatch.setattr(internal_client, "get_internal_http_client", _TasksClient)


def _expected_prompt(key: str, *, agent_enabled: bool) -> str:
    if not agent_enabled:
        return SESSION_INIT_PROMPT[key].format(name=NAME) + PERSONA
    return (
        SESSION_INIT_PROMPT_AGENT[key].format(name=NAME)
        + PERSONA
        + AGENT_TASKS_HEADER[key]
        + f"  - [{AGENT_TASK_STATUS_RUNNING[key]}] open the map\n"
        + f"  - [{AGENT_TASK_STATUS_QUEUED[key]}] pause the music"
        + AGENT_TASKS_NOTICE[key]
    )


async def _render(user_language, *, agent_enabled: bool) -> str:
    return await _PromptOwner(user_language, agent_enabled=agent_enabled)._build_initial_prompt()


@pytest.mark.parametrize("table", SYS_TABLES.values(), ids=SYS_TABLES.keys())
def test_precondition_the_two_rows_differ(table):
    """Vacuous-assertion trap: identical zh / zh-TW rows would let the tests below pass on the bug."""
    assert table["zh-TW"] != table["zh"]


@pytest.mark.parametrize("agent_enabled", (False, True), ids=("plain", "agent"))
@pytest.mark.parametrize("spelling", TRADITIONAL_SPELLINGS)
async def test_traditional_users_get_the_traditional_rows(spelling, agent_enabled, capsys):
    prompt = await _render(spelling, agent_enabled=agent_enabled)

    assert prompt == _expected_prompt("zh-TW", agent_enabled=agent_enabled)
    assert "Unexpected lang code" not in capsys.readouterr().out


@pytest.mark.parametrize("agent_enabled", (False, True), ids=("plain", "agent"))
@pytest.mark.parametrize(("spelling", "key"), UNCHANGED_LOCALES)
async def test_other_locales_render_the_same_rows_as_before(spelling, key, agent_enabled, capsys):
    # The key in the table is what the old short code resolved to; pin that so
    # "unchanged" is checked against the previous behavior, not just restated.
    assert normalize_language_code(spelling, format="short") == key

    prompt = await _render(spelling, agent_enabled=agent_enabled)

    assert prompt == _expected_prompt(key, agent_enabled=agent_enabled)
    # _loc prints this when it gets a key the table does not have (e.g. a full
    # "zh-CN") and silently falls back; the rendered text alone would not show it.
    assert "Unexpected lang code" not in capsys.readouterr().out
