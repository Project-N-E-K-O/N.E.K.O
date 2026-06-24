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

"""Unit tests for the opt-in proactive-analyze path.

A proactive (self-initiated, no fresh user input) turn would be dropped by the
ordinary user-turn dedupe, so it is routed through a separate throttled path
(``_handle_proactive_analyze``), bounded by the master switch, an assistant-text
fingerprint, and a per-session count cap. lanlan's own latest utterance becomes
the actionable intent for the assessment.
"""
from __future__ import annotations

import asyncio

import config
import app.agent_server as a
from brain.task_executor import DirectTaskExecutor


def _proactive_msgs(assistant_text: str):
    # A proactive window: the latest user line is a STALE prior turn, the new
    # content is lanlan's own assistant utterance.
    return [
        {"role": "user", "content": "之前那句旧闲聊"},
        {"role": "assistant", "content": assistant_text},
    ]


def _patch_dispatch(monkeypatch):
    """Replace the dispatch tail so no coroutine/loop is needed; capture calls."""
    calls = []
    monkeypatch.setattr(a, "_background_analyze_and_plan", lambda *args, **kw: ("BAP", kw))
    monkeypatch.setattr(a, "_create_tracked_task", lambda x: calls.append(x))
    return calls


def _reset_state():
    a.Modules.proactive_analyze_count.clear()
    a.Modules.last_proactive_assistant_fingerprint.clear()


# ── fingerprint / intent helpers ─────────────────────────────────────
def test_assistant_fingerprint_and_latest_text():
    m1 = _proactive_msgs("我帮你查下天气")
    m2 = _proactive_msgs("我帮你查下天气")
    m3 = _proactive_msgs("我给你推首歌")
    assert a._build_assistant_turn_fingerprint(m1) == a._build_assistant_turn_fingerprint(m2)
    assert a._build_assistant_turn_fingerprint(m1) != a._build_assistant_turn_fingerprint(m3)
    # no assistant text → None
    assert a._build_assistant_turn_fingerprint([{"role": "user", "content": "x"}]) is None
    assert a._latest_assistant_text(m1) == "我帮你查下天气"
    assert a._latest_assistant_text([{"role": "user", "content": "x"}]) == ""


# ── _handle_proactive_analyze gating ─────────────────────────────────
def test_proactive_disabled_does_not_dispatch(monkeypatch):
    _reset_state()
    calls = _patch_dispatch(monkeypatch)
    monkeypatch.setattr(config, "AGENT_PROACTIVE_ANALYZE_ENABLED", False)  # default
    a._handle_proactive_analyze({"proactive": True}, _proactive_msgs("我帮你查天气"), "lan", "lan", "c")
    assert calls == []
    assert a.Modules.proactive_analyze_count.get("lan", 0) == 0


def test_proactive_enabled_dispatches_with_assistant_intent(monkeypatch):
    _reset_state()
    calls = _patch_dispatch(monkeypatch)
    monkeypatch.setattr(config, "AGENT_PROACTIVE_ANALYZE_ENABLED", True)
    monkeypatch.setattr(config, "AGENT_PROACTIVE_ANALYZE_MAX_PER_SESSION", 2)
    a._handle_proactive_analyze({"proactive": True}, _proactive_msgs("我帮你查天气"), "lan", "lan", "c")
    assert len(calls) == 1
    # dispatched with proactive=True and no gate signal (fails open into assess)
    _, kw = calls[0]
    assert kw.get("proactive") is True and kw.get("action_intent") is None
    assert a.Modules.proactive_analyze_count["lan"] == 1


def test_proactive_no_assistant_text_skips(monkeypatch):
    _reset_state()
    calls = _patch_dispatch(monkeypatch)
    monkeypatch.setattr(config, "AGENT_PROACTIVE_ANALYZE_ENABLED", True)
    a._handle_proactive_analyze({"proactive": True}, [{"role": "user", "content": "x"}], "lan", "lan", "c")
    assert calls == []


def test_proactive_duplicate_utterance_deduped(monkeypatch):
    _reset_state()
    calls = _patch_dispatch(monkeypatch)
    monkeypatch.setattr(config, "AGENT_PROACTIVE_ANALYZE_ENABLED", True)
    monkeypatch.setattr(config, "AGENT_PROACTIVE_ANALYZE_MAX_PER_SESSION", 5)
    msgs = _proactive_msgs("我帮你查天气")
    a._handle_proactive_analyze({"proactive": True}, msgs, "lan", "lan", "c")
    a._handle_proactive_analyze({"proactive": True}, msgs, "lan", "lan", "c")  # identical → deduped
    assert len(calls) == 1
    assert a.Modules.proactive_analyze_count["lan"] == 1


def test_proactive_per_session_cap(monkeypatch):
    _reset_state()
    calls = _patch_dispatch(monkeypatch)
    monkeypatch.setattr(config, "AGENT_PROACTIVE_ANALYZE_ENABLED", True)
    monkeypatch.setattr(config, "AGENT_PROACTIVE_ANALYZE_MAX_PER_SESSION", 2)
    # distinct utterances each time so dedupe never blocks — only the cap does.
    for i in range(4):
        a._handle_proactive_analyze({"proactive": True}, _proactive_msgs(f"主动台词{i}"), "lan", "lan", "c")
    assert len(calls) == 2  # capped at 2
    assert a.Modules.proactive_analyze_count["lan"] == 2


def test_proactive_cap_is_per_lanlan(monkeypatch):
    _reset_state()
    calls = _patch_dispatch(monkeypatch)
    monkeypatch.setattr(config, "AGENT_PROACTIVE_ANALYZE_ENABLED", True)
    monkeypatch.setattr(config, "AGENT_PROACTIVE_ANALYZE_MAX_PER_SESSION", 1)
    a._handle_proactive_analyze({"proactive": True}, _proactive_msgs("A"), "lanA", "lanA", "c")
    a._handle_proactive_analyze({"proactive": True}, _proactive_msgs("B"), "lanB", "lanB", "c")
    assert len(calls) == 2  # each lanlan has its own budget
    assert a.Modules.proactive_analyze_count["lanA"] == 1
    assert a.Modules.proactive_analyze_count["lanB"] == 1


# ── greeting_check resets the per-session budget ─────────────────────
def test_greeting_check_resets_budget(monkeypatch):
    _reset_state()
    async def _noop():
        return
    monkeypatch.setattr(a, "_maybe_restore_agent_intent", _noop)
    a.Modules.proactive_analyze_count["lan"] = 2
    a.Modules.last_proactive_assistant_fingerprint["lan"] = "deadbeef"
    asyncio.run(a._on_session_event({"event_type": "agent_intent_restore_signal", "lanlan_name": "lan"}))
    assert "lan" not in a.Modules.proactive_analyze_count
    assert "lan" not in a.Modules.last_proactive_assistant_fingerprint


# ── executor uses the assistant utterance as intent on proactive ─────
def test_executor_extracts_latest_assistant_intent():
    ex = DirectTaskExecutor(computer_use=object())
    msgs = [
        {"role": "user", "content": "旧请求"},
        {"role": "assistant", "content": "我帮你查下天气吧"},
    ]
    assert ex._extract_latest_assistant_intent(msgs) == "我帮你查下天气吧"
    assert ex._extract_latest_assistant_intent([{"role": "user", "content": "x"}]) == ""
