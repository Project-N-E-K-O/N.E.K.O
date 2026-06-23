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

"""Unit tests for the cheap action-intent pre-gate in DirectTaskExecutor.

The gate skips the expensive turn-end assessment when the input-time
master-emotion call confidently read the turn as NON-action — but only when the
zero-LLM deterministic shortcuts (openclaw magic word, plugin keyword) also find
nothing, and never when action_intent is None (no usable signal).
"""
from __future__ import annotations

import asyncio

import brain.task_executor as te

# Single import style for brain.task_executor: reference both the class
# (``te.DirectTaskExecutor``) and the gate knobs (``te.AGENT_ACTION_GATE_*``)
# through the module. The knobs are module-level names imported into
# brain.task_executor (the repo's `from config import (...)` convention), so
# they are patched on that module here, not on the `config` module.


def _exec():
    # Pass a truthy stub for computer_use so __init__ skips constructing a real
    # ComputerUseAdapter; these tests never invoke a real adapter method (the
    # brake returns before availability checks, and the proceed paths hit only
    # the guarded ``is_available`` access which fails closed to "not available").
    return te.DirectTaskExecutor(computer_use=object())


class _SpyCU:
    """computer_use stub that counts is_available() calls, to prove the gate
    brakes BEFORE the availability probe (not a None that fell through later)."""

    def __init__(self):
        self.is_available_calls = 0

    def is_available(self):
        self.is_available_calls += 1
        return {"ready": True}


# ── _deterministic_action_signal: the zero-LLM shortcuts the gate must keep ──
def test_det_signal_magic_word():
    ex = _exec()
    assert ex._deterministic_action_signal("/clear", openclaw_enabled=True, user_plugin_enabled=False) is True
    assert ex._deterministic_action_signal("stop", openclaw_enabled=True, user_plugin_enabled=False) is True
    # natural-language magic commands (zero-LLM rule classifier) count too
    assert ex._deterministic_action_signal("取消这个任务", openclaw_enabled=True, user_plugin_enabled=False) is True
    assert ex._deterministic_action_signal("换个话题吧", openclaw_enabled=True, user_plugin_enabled=False) is True
    assert ex._deterministic_action_signal("随便聊聊", openclaw_enabled=True, user_plugin_enabled=False) is False
    # magic word is ignored when openclaw is off
    assert ex._deterministic_action_signal("/clear", openclaw_enabled=False, user_plugin_enabled=False) is False


def test_det_signal_plugin_keyword():
    ex = _exec()
    ex.plugin_list = [{"id": "p1", "keywords": ["天气", "weather"]}]
    assert ex._deterministic_action_signal("帮我查下天气", openclaw_enabled=False, user_plugin_enabled=True) is True
    assert ex._deterministic_action_signal("check the weather", openclaw_enabled=False, user_plugin_enabled=True) is True
    assert ex._deterministic_action_signal("今天好开心", openclaw_enabled=False, user_plugin_enabled=True) is False
    # keyword shortcut ignored when user_plugin is off
    assert ex._deterministic_action_signal("帮我查下天气", openclaw_enabled=False, user_plugin_enabled=False) is False


def test_det_signal_failopen_keywordless_dispatchable_plugin():
    ex = _exec()
    # A dispatchable plugin with no top-level keywords (e.g. music_pusher exposes
    # an agent entry but defines no keywords) can only be selected by the LLM
    # assessment; the keyword shortcut can't represent it, so the gate must fail
    # open even on chat text — never brake a turn that plugin could handle.
    ex.plugin_list = [{"id": "music_pusher"}]  # no keywords, default visible entry
    assert ex._deterministic_action_signal("今天好无聊", openclaw_enabled=False, user_plugin_enabled=True) is True
    # When ALL active plugins DO have keywords and none match → brakeable (False).
    ex.plugin_list = [{"id": "weather", "keywords": ["天气预报"]}]
    assert ex._deterministic_action_signal("今天好无聊", openclaw_enabled=False, user_plugin_enabled=True) is False
    # A passive keywordless plugin never dispatches → it must NOT force fail-open.
    ex.plugin_list = [{"id": "bg", "passive": True}]
    assert ex._deterministic_action_signal("今天好无聊", openclaw_enabled=False, user_plugin_enabled=True) is False


def test_det_signal_failopen_when_plugins_unloaded():
    ex = _exec()
    ex.plugin_list = []  # not loaded yet
    # user_plugin on but the cached list is empty → cannot run the keyword
    # shortcut → must NOT let the gate skip it → fail open (True).
    assert ex._deterministic_action_signal("今天好开心", openclaw_enabled=False, user_plugin_enabled=True) is True


def test_det_signal_none_when_no_shortcuts():
    ex = _exec()
    assert ex._deterministic_action_signal("今天好开心", openclaw_enabled=False, user_plugin_enabled=False) is False
    # empty / whitespace text is never an action signal
    assert ex._deterministic_action_signal("   ", openclaw_enabled=True, user_plugin_enabled=True) is False


# ── the gate control flow inside _analyze_and_execute_inner ──
def test_gate_brakes_on_confident_chat(monkeypatch):
    monkeypatch.setattr(te, "AGENT_ACTION_GATE_ENABLED", True)
    monkeypatch.setattr(te, "AGENT_ACTION_GATE_THRESHOLD", 0.2)
    cu = _SpyCU()
    ex = te.DirectTaskExecutor(computer_use=cu)
    msgs = [{"role": "user", "text": "今天天气真好心情不错"}]
    # computer_use only, action_intent below the line, no deterministic signal →
    # the gate brakes and returns None before any availability check / LLM call.
    res = asyncio.run(ex._analyze_and_execute_inner(
        messages=msgs, agent_flags={"computer_use_enabled": True}, action_intent=0.05,
    ))
    assert res is None
    # Proven to brake AT THE GATE: the availability probe was never reached, so
    # no assessment / channel work happened — not a None that fell through later.
    assert cu.is_available_calls == 0


def test_gate_consults_deterministic_only_when_braking(monkeypatch):
    monkeypatch.setattr(te, "AGENT_ACTION_GATE_ENABLED", True)
    monkeypatch.setattr(te, "AGENT_ACTION_GATE_THRESHOLD", 0.2)
    ex = _exec()
    calls = []
    monkeypatch.setattr(ex, "_deterministic_action_signal", lambda *a, **k: calls.append(1) or False)
    msgs = [{"role": "user", "text": "随便聊聊"}]
    flags = {"computer_use_enabled": True}

    # low action_intent → gate consults the deterministic shortcuts, then brakes.
    res = asyncio.run(ex._analyze_and_execute_inner(messages=msgs, agent_flags=flags, action_intent=0.05))
    assert res is None and len(calls) == 1

    # high action_intent (>= threshold) → gate skipped, shortcuts NOT consulted.
    calls.clear()
    asyncio.run(ex._analyze_and_execute_inner(messages=msgs, agent_flags=flags, action_intent=0.9))
    assert len(calls) == 0

    # None action_intent (no usable signal) → gate skipped entirely (fail open).
    asyncio.run(ex._analyze_and_execute_inner(messages=msgs, agent_flags=flags, action_intent=None))
    assert len(calls) == 0

    # gate disabled by config → not consulted even on a low action_intent.
    monkeypatch.setattr(te, "AGENT_ACTION_GATE_ENABLED", False)
    asyncio.run(ex._analyze_and_execute_inner(messages=msgs, agent_flags=flags, action_intent=0.05))
    assert len(calls) == 0
