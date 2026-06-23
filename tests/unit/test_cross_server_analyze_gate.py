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

"""Unit tests for the external_intent pre-gate hint at the cross_server publish
boundary — specifically the proactive-turn fail-open.

On a proactive turn (lanlan spoke with no fresh user input) master-emotion did
NOT re-run, so ``.latest`` still holds the PREVIOUS user turn's reading and the
"latest user message" in ``messages`` is that same stale message. Because the
freshness match keys on text identity, the stale reading would match and brake
the proactive turn's analyzer on a signal that never saw the proactive content.
``had_user_input=False`` must therefore skip the gate hint entirely so the agent
fails open and assesses the turn.
"""
from __future__ import annotations

import asyncio

import main_logic.cross_server as cs
import main_logic.activity.master_emotion as me


def _patch_publish(monkeypatch):
    """Stub publish_analyze_request_reliably; capture the external_intent kwarg."""
    captured = {}

    async def fake_publish(*, lanlan_name, trigger, messages, ack_timeout_s,
                           retries, conversation_id=None, external_intent=None):
        captured["external_intent"] = external_intent
        return True

    monkeypatch.setattr(cs, "publish_analyze_request_reliably", fake_publish)
    return captured


def _patch_gate(monkeypatch, value):
    """Stub gate_signal_for; count calls so we can prove it is NOT consulted on a
    proactive turn."""
    calls = {"n": 0}

    def fake_gate(lanlan_name, user_text):
        calls["n"] += 1
        return value

    monkeypatch.setattr(me, "gate_signal_for", fake_gate)
    return calls


def test_user_turn_consults_gate(monkeypatch):
    captured = _patch_publish(monkeypatch)
    calls = _patch_gate(monkeypatch, 0.05)
    msgs = [{"role": "user", "content": "今天天气真好心情不错"}]
    ok = asyncio.run(cs._publish_analyze_request_with_fallback(
        "lan", "turn_end", msgs, conversation_id="c", had_user_input=True,
    ))
    assert ok is True
    # Real user turn → gate consulted, its value rides to the publish.
    assert calls["n"] == 1
    assert captured["external_intent"] == 0.05


def test_proactive_turn_fails_open(monkeypatch):
    captured = _patch_publish(monkeypatch)
    calls = _patch_gate(monkeypatch, 0.05)
    # A proactive turn's recent window still contains the PREVIOUS user message,
    # which would match the stale reading and brake. had_user_input=False must
    # skip the gate entirely → external_intent None → agent fails open.
    msgs = [
        {"role": "user", "content": "之前那句闲聊"},
        {"role": "assistant", "content": "我突然想帮你查个东西"},
    ]
    ok = asyncio.run(cs._publish_analyze_request_with_fallback(
        "lan", "turn_end", msgs, conversation_id="c", had_user_input=False,
    ))
    assert ok is True
    assert calls["n"] == 0                       # gate never consulted
    assert captured["external_intent"] is None   # fail open


def test_user_turn_with_attachment_fails_open(monkeypatch):
    # Existing behavior preserved: a user turn whose latest message carries an
    # attachment skips the text-only gate hint (the actionable intent may live in
    # the image), so the agent fails open rather than dropping an image task.
    captured = _patch_publish(monkeypatch)
    calls = _patch_gate(monkeypatch, 0.05)
    msgs = [{"role": "user", "content": "", "attachments": [{"type": "image_url", "url": "x"}]}]
    ok = asyncio.run(cs._publish_analyze_request_with_fallback(
        "lan", "turn_end", msgs, conversation_id="c", had_user_input=True,
    ))
    assert ok is True
    assert calls["n"] == 0
    assert captured["external_intent"] is None


def test_default_had_user_input_is_true(monkeypatch):
    # Callers that omit had_user_input get the user-turn path (gate consulted),
    # so the proactive fail-open is strictly opt-in and never silently suppresses
    # a real user turn's signal.
    captured = _patch_publish(monkeypatch)
    calls = _patch_gate(monkeypatch, 0.9)
    msgs = [{"role": "user", "content": "帮我打开浏览器"}]
    ok = asyncio.run(cs._publish_analyze_request_with_fallback(
        "lan", "turn_end", msgs, conversation_id="c",
    ))
    assert ok is True
    assert calls["n"] == 1
    assert captured["external_intent"] == 0.9
