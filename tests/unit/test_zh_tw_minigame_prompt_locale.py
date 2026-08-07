# -*- coding: utf-8 -*-
"""The minigame prompt chain keeps Traditional Chinese end to end (issue #2500 step 2).

Two halves that only work together, which is why they ship in one commit:

* ``prompts_minigame_common._normalize_prompt_lang`` now passes
  ``keep_traditional=True``. On its own that changes nothing — a caller handing
  over a SHORT code has already collapsed the script upstream.
* ``_run_soccer_passive_guard_ai`` now resolves the FULL locale. On its own that
  changes nothing either — the normalizer would have collapsed it right back.

The zh-TW coverage test below is not decoration. Three consumers read these
tables as ``.get(key) or table["en"]`` rather than through ``_loc``, so a table
missing a ``zh-TW`` row does not fall back to Simplified — it falls back to
ENGLISH. Widening the normalizer is only safe while that coverage holds.
"""
from __future__ import annotations

import asyncio
import importlib
import pathlib
from types import SimpleNamespace

import pytest

import utils.llm_client as llm_client_module
from config.prompts import prompts_soccer
from config.prompts.prompts_minigame_common import _normalize_prompt_lang
from main_routers.game_router import char_info as gr_char_info
from main_routers.game_router import runtime as gr_runtime

PROMPTS_DIR = pathlib.Path(__file__).resolve().parents[2] / "config" / "prompts"


def _minigame_prompt_modules() -> tuple[str, ...]:
    """Every config/prompts module on ``_normalize_prompt_lang``, found via imports.

    This was a hardcoded 4-tuple while the docstring below claimed discovery --
    exactly the checklist-gap failure it warns about. A fifth module importing the
    helper would be skipped silently by the coverage guard, and a missing zh-TW row
    there costs a Traditional user ENGLISH, not Simplified (the three
    ``.get(k) or table["en"]`` consumers).
    """
    found = ["config.prompts.prompts_minigame_common"]
    for path in sorted(PROMPTS_DIR.glob("prompts_*.py")):
        if path.stem == "prompts_minigame_common":
            continue
        src = path.read_text(encoding="utf-8")
        if "prompts_minigame_common import" in src or "from config.prompts.prompts_minigame_common" in src:
            found.append(f"config.prompts.{path.stem}")
    return tuple(found)


MINIGAME_PROMPT_MODULES = _minigame_prompt_modules()


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("zh-TW", "zh-TW"),
        ("zh-Hant", "zh-TW"),
        ("zh-HK", "zh-TW"),
        ("tchinese", "zh-TW"),
        ("zh-CN", "zh"),
        ("zh", "zh"),
        ("schinese", "zh"),
        ("en", "en"),
        ("ja", "ja"),
        (None, "zh"),
        ("", "zh"),
    ],
)
def test_minigame_normalizer_keeps_the_script(raw, expected):
    assert _normalize_prompt_lang(raw) == expected


def _chinese_tables():
    """Every locale table in the minigame modules, discovered from the modules.

    Discovered rather than listed: a hand-written list stops covering whatever is
    added next, and for the three ``.get(key) or table["en"]`` consumers the cost
    of a miss is an English prompt for a Traditional user, not a Simplified one.
    """
    found = []
    for module_name in MINIGAME_PROMPT_MODULES:
        module = importlib.import_module(module_name)
        for attr in dir(module):
            value = getattr(module, attr)
            if isinstance(value, dict) and "zh" in value and "en" in value:
                found.append((module_name, attr, value))
    return found


@pytest.mark.parametrize(
    "module_name, attr, table",
    [pytest.param(m, a, t, id=f"{m.rsplit('.', 1)[-1]}.{a}") for m, a, t in _chinese_tables()],
)
def test_every_minigame_table_carries_a_traditional_row(module_name, attr, table):
    assert "zh-TW" in table, (
        f"{module_name}.{attr} has no zh-TW row; _normalize_prompt_lang now emits "
        "'zh-TW', and the .get(key) or table['en'] consumers would answer ENGLISH"
    )


def test_the_coverage_check_actually_found_tables():
    """A zero-table sweep would make the parametrized test vacuously green."""
    assert len(_chinese_tables()) >= 20


def test_the_module_discovery_finds_every_minigame_consumer():
    """The module list is discovered from imports, so a fifth module cannot slip past.

    Pinned by name as well as by count: discovery returning a short list is the
    same silent-hole failure as the hand-written tuple this replaced.
    """
    assert set(MINIGAME_PROMPT_MODULES) == {
        "config.prompts.prompts_minigame_common",
        "config.prompts.prompts_soccer",
        "config.prompts.prompts_badminton",
        "config.prompts.prompts_minigame_route",
    }


def test_soccer_prompts_differ_by_script():
    traditional = prompts_soccer.get_soccer_passive_guard_system_prompt("zh-TW")
    simplified = prompts_soccer.get_soccer_passive_guard_system_prompt("zh-CN")
    assert traditional == prompts_soccer.SOCCER_PASSIVE_GUARD_SYSTEM_PROMPTS["zh-TW"]
    assert simplified == prompts_soccer.SOCCER_PASSIVE_GUARD_SYSTEM_PROMPTS["zh"]
    assert traditional != simplified


# ── 端到端：soccer 被动守卫端点 ────────────────────────────────────────────


class _FakeResult:
    def __init__(self, content):
        self.content = content


class _FakeLLM:
    def __init__(self, sink):
        self._sink = sink

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def ainvoke(self, messages):
        self._sink.append(messages)
        return _FakeResult('{"classification":"ordinary","confidence":0.5}')


def _run_passive_guard(monkeypatch, *, session_language, request_body=None):
    """Drive the real ``_run_soccer_passive_guard_ai`` and capture its prompts."""
    sink: list = []

    manager = SimpleNamespace(user_language=session_language, _user_language_explicit=True)
    monkeypatch.setattr(gr_char_info, "get_session_manager", lambda: {"Lan": manager})
    monkeypatch.setattr(gr_char_info, "get_global_language_full", lambda: "en")
    monkeypatch.setattr(gr_runtime, "_find_game_route_state_for_session", lambda *a, **k: None)
    monkeypatch.setattr(
        gr_runtime,
        "_get_game_route_summary_llm_info",
        lambda *a, **k: {
            "model": "m",
            "base_url": "http://localhost",
            "api_key": "k",
            "provider_type": "openai",
            "user_language": "en",
            "user_language_full": "en",
        },
    )

    async def _fake_create(*args, **kwargs):
        return _FakeLLM(sink)

    monkeypatch.setattr(llm_client_module, "create_chat_llm_async", _fake_create)

    data = dict(request_body or {})
    data.setdefault("session_id", "s1")
    data.setdefault("stage", 9)
    asyncio.run(gr_runtime._run_soccer_passive_guard_ai(data, "Lan"))
    assert sink, "LLM 没被调用"
    return sink[0][0].content


def test_passive_guard_prompt_is_traditional_for_a_traditional_session(monkeypatch):
    system_text = _run_passive_guard(monkeypatch, session_language="zh-TW")
    assert system_text == prompts_soccer.SOCCER_PASSIVE_GUARD_SYSTEM_PROMPTS["zh-TW"]
    assert system_text != prompts_soccer.SOCCER_PASSIVE_GUARD_SYSTEM_PROMPTS["zh"]


def test_passive_guard_prompt_stays_simplified_for_a_simplified_session(monkeypatch):
    system_text = _run_passive_guard(monkeypatch, session_language="zh-CN")
    assert system_text == prompts_soccer.SOCCER_PASSIVE_GUARD_SYSTEM_PROMPTS["zh"]


def test_passive_guard_request_body_locale_wins_over_the_session(monkeypatch):
    """The request body's i18n truth is the top priority, same as every other
    soccer endpoint."""
    system_text = _run_passive_guard(
        monkeypatch,
        session_language="zh-CN",
        request_body={"i18n_language": "zh-TW"},
    )
    assert system_text == prompts_soccer.SOCCER_PASSIVE_GUARD_SYSTEM_PROMPTS["zh-TW"]
