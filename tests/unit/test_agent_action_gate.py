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

import config
from brain.task_executor import DirectTaskExecutor


def _exec():
    # Pass a truthy stub for computer_use so __init__ skips constructing a real
    # ComputerUseAdapter; these tests never invoke a real adapter method (the
    # brake returns before availability checks, and the proceed paths hit only
    # the guarded ``is_available`` access which fails closed to "not available").
    return DirectTaskExecutor(computer_use=object())


# ── _deterministic_action_signal: the zero-LLM shortcuts the gate must keep ──
def test_det_signal_magic_word():
    ex = _exec()
    assert ex._deterministic_action_signal("/clear", openclaw_enabled=True, user_plugin_enabled=False) is True
    assert ex._deterministic_action_signal("stop", openclaw_enabled=True, user_plugin_enabled=False) is True
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
    monkeypatch.setattr(config, "AGENT_ACTION_GATE_ENABLED", True)
    monkeypatch.setattr(config, "AGENT_ACTION_GATE_THRESHOLD", 0.2)
    ex = _exec()
    msgs = [{"role": "user", "text": "今天天气真好心情不错"}]
    # computer_use only, action_intent below the line, no deterministic signal →
    # the gate brakes and returns None before any availability check / LLM call.
    res = asyncio.run(ex._analyze_and_execute_inner(
        messages=msgs, agent_flags={"computer_use_enabled": True}, action_intent=0.05,
    ))
    assert res is None


def test_gate_consults_deterministic_only_when_braking(monkeypatch):
    monkeypatch.setattr(config, "AGENT_ACTION_GATE_ENABLED", True)
    monkeypatch.setattr(config, "AGENT_ACTION_GATE_THRESHOLD", 0.2)
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
    monkeypatch.setattr(config, "AGENT_ACTION_GATE_ENABLED", False)
    asyncio.run(ex._analyze_and_execute_inner(messages=msgs, agent_flags=flags, action_intent=0.05))
    assert len(calls) == 0
