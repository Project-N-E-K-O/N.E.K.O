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


# ── assistant-turn fingerprint ───────────────────────────────────────
def test_assistant_fingerprint():
    m1 = _proactive_msgs("我帮你查下天气")
    m2 = _proactive_msgs("我帮你查下天气")
    m3 = _proactive_msgs("我给你推首歌")
    assert a._build_assistant_turn_fingerprint(m1) == a._build_assistant_turn_fingerprint(m2)
    assert a._build_assistant_turn_fingerprint(m1) != a._build_assistant_turn_fingerprint(m3)
    # no assistant text → None (also the "no assistant utterance" skip signal)
    assert a._build_assistant_turn_fingerprint([{"role": "user", "content": "x"}]) is None
    # role match is case-insensitive (consistent with the executor's intent extractor)
    assert a._build_assistant_turn_fingerprint([{"role": "Assistant", "content": "hi"}]) is not None
    # keyed to the LATEST assistant utterance only → history-independent: the same
    # trailing line with different prior assistant records still dedupes (so a
    # re-send after the window grew can't slip past the gate and burn a slot).
    only = [{"role": "assistant", "content": "我帮你查下天气"}]
    with_history = [
        {"role": "assistant", "content": "更早的一句"},
        {"role": "user", "content": "中间的用户话"},
        {"role": "assistant", "content": "我帮你查下天气"},
    ]
    assert a._build_assistant_turn_fingerprint(only) == a._build_assistant_turn_fingerprint(with_history)


# ── _handle_proactive_analyze gating ─────────────────────────────────
def test_proactive_disabled_does_not_dispatch(monkeypatch):
    _reset_state()
    calls = _patch_dispatch(monkeypatch)
    monkeypatch.setattr(a, "AGENT_PROACTIVE_ANALYZE_ENABLED", False)  # default
    a._handle_proactive_analyze(_proactive_msgs("我帮你查天气"), "lan", "lan", "c")
    assert calls == []
    assert a.Modules.proactive_analyze_count.get("lan", 0) == 0


def test_proactive_enabled_dispatches_with_assistant_intent(monkeypatch):
    _reset_state()
    calls = _patch_dispatch(monkeypatch)
    monkeypatch.setattr(a, "AGENT_PROACTIVE_ANALYZE_ENABLED", True)
    monkeypatch.setattr(a, "AGENT_PROACTIVE_ANALYZE_MAX_PER_SESSION", 2)
    a._handle_proactive_analyze(_proactive_msgs("我帮你查天气"), "lan", "lan", "c")
    assert len(calls) == 1
    # dispatched with proactive=True and no gate signal (fails open into assess)
    _, kw = calls[0]
    assert kw.get("proactive") is True and kw.get("external_intent") is None
    assert a.Modules.proactive_analyze_count["lan"] == 1


def test_proactive_no_assistant_text_skips(monkeypatch):
    _reset_state()
    calls = _patch_dispatch(monkeypatch)
    monkeypatch.setattr(a, "AGENT_PROACTIVE_ANALYZE_ENABLED", True)
    a._handle_proactive_analyze([{"role": "user", "content": "x"}], "lan", "lan", "c")
    assert calls == []


def test_proactive_duplicate_utterance_deduped(monkeypatch):
    _reset_state()
    calls = _patch_dispatch(monkeypatch)
    monkeypatch.setattr(a, "AGENT_PROACTIVE_ANALYZE_ENABLED", True)
    monkeypatch.setattr(a, "AGENT_PROACTIVE_ANALYZE_MAX_PER_SESSION", 5)
    msgs = _proactive_msgs("我帮你查天气")
    a._handle_proactive_analyze(msgs, "lan", "lan", "c")
    a._handle_proactive_analyze(msgs, "lan", "lan", "c")  # identical → deduped
    assert len(calls) == 1
    assert a.Modules.proactive_analyze_count["lan"] == 1


def test_proactive_per_session_cap(monkeypatch):
    _reset_state()
    calls = _patch_dispatch(monkeypatch)
    monkeypatch.setattr(a, "AGENT_PROACTIVE_ANALYZE_ENABLED", True)
    monkeypatch.setattr(a, "AGENT_PROACTIVE_ANALYZE_MAX_PER_SESSION", 2)
    # distinct utterances each time so dedupe never blocks — only the cap does.
    for i in range(4):
        a._handle_proactive_analyze(_proactive_msgs(f"主动台词{i}"), "lan", "lan", "c")
    assert len(calls) == 2  # capped at 2
    assert a.Modules.proactive_analyze_count["lan"] == 2


def test_proactive_cap_is_per_lanlan(monkeypatch):
    _reset_state()
    calls = _patch_dispatch(monkeypatch)
    monkeypatch.setattr(a, "AGENT_PROACTIVE_ANALYZE_ENABLED", True)
    monkeypatch.setattr(a, "AGENT_PROACTIVE_ANALYZE_MAX_PER_SESSION", 1)
    a._handle_proactive_analyze(_proactive_msgs("A"), "lanA", "lanA", "c")
    a._handle_proactive_analyze(_proactive_msgs("B"), "lanB", "lanB", "c")
    assert len(calls) == 2  # each lanlan has its own budget
    assert a.Modules.proactive_analyze_count["lanA"] == 1
    assert a.Modules.proactive_analyze_count["lanB"] == 1


# ── greeting_check resets the budget only on a genuine new session ───
def test_genuine_new_session_resets_budget(monkeypatch):
    _reset_state()
    async def _noop():
        return
    monkeypatch.setattr(a, "_maybe_restore_agent_intent", _noop)
    a.Modules.proactive_analyze_count["lan"] = 2
    a.Modules.last_proactive_assistant_fingerprint["lan"] = "deadbeef"
    asyncio.run(a._on_session_event({
        "event_type": "agent_intent_restore_signal", "lanlan_name": "lan", "new_session": True,
    }))
    assert "lan" not in a.Modules.proactive_analyze_count
    assert "lan" not in a.Modules.last_proactive_assistant_fingerprint


def test_refresh_does_not_reset_budget(monkeypatch):
    # A refresh/reconnect (new_session falsey) must NOT reset the budget — else a
    # user could refresh to farm a fresh cap mid-conversation.
    _reset_state()
    async def _noop():
        return
    monkeypatch.setattr(a, "_maybe_restore_agent_intent", _noop)
    a.Modules.proactive_analyze_count["lan"] = 2
    asyncio.run(a._on_session_event({
        "event_type": "agent_intent_restore_signal", "lanlan_name": "lan",  # no new_session
    }))
    assert a.Modules.proactive_analyze_count.get("lan") == 2


# ── cancel redaction preserves the proactive utterance ──────────────
def test_redact_preserves_proactive_utterance(monkeypatch):
    user_msg = {"role": "user", "content": "取消了的旧请求"}
    msgs = [
        user_msg,
        {"role": "assistant", "content": "旧任务回复"},
        {"role": "assistant", "content": "我帮你查下天气"},  # proactive utterance (trailing)
    ]
    sig = a._user_message_signature(user_msg)
    monkeypatch.setattr(a._task_tracker, "get_cancelled_user_sigs", lambda ln: {sig})

    def _has_proactive(out):
        return any(m.get("role") == "assistant" and "天气" in str(m.get("content", "")) for m in out)

    # default: the trailing proactive assistant is dropped with the cancelled user
    assert not _has_proactive(a._redact_cancelled_user_turns(msgs, "lan"))
    # preserve_trailing_assistant: the proactive utterance survives
    assert _has_proactive(a._redact_cancelled_user_turns(msgs, "lan", preserve_trailing_assistant=True))


def test_redact_cancelled_user_without_reply_before_proactive(monkeypatch):
    # Codex P2: a cancelled user turn that produced NO assistant reply, followed
    # by a proactive utterance. The trailing proactive assistant must not count as
    # that user's "first-time" reply (trailing_assistant_count==1 bypass), else the
    # cancelled request leaks into the proactive assessment. It must be redacted
    # while the proactive utterance is still preserved.
    user_msg = {"role": "user", "content": "取消了的旧请求（没产出回复）"}
    msgs = [
        user_msg,
        {"role": "assistant", "content": "我帮你查下天气"},  # proactive utterance (only trailing assistant)
    ]
    sig = a._user_message_signature(user_msg)
    monkeypatch.setattr(a._task_tracker, "get_cancelled_user_sigs", lambda ln: {sig})

    out = a._redact_cancelled_user_turns(msgs, "lan", preserve_trailing_assistant=True)
    # cancelled user text redacted (replaced by the system marker), not leaked
    assert not any(m.get("role") == "user" and "取消了的旧请求" in str(m.get("content", "")) for m in out)
    assert any(m.get("role") == "system" and m.get("content") == a.REDACTED_USER_TURN_MARKER for m in out)
    # proactive utterance still survives for the downstream assessment
    assert any(m.get("role") == "assistant" and "天气" in str(m.get("content", "")) for m in out)


def test_redact_cancelled_one_reply_turn_before_proactive(monkeypatch):
    # Codex P2 follow-up: a cancelled user turn that DID produce its own reply,
    # then a proactive turn. The first-time bypass (trailing_assistant_count==1)
    # must NOT fire on a proactive analyze pass — the trigger is lanlan's own
    # utterance, not a user re-issue — so the cancelled user AND its reply are
    # redacted, while the proactive utterance is preserved.
    user_msg = {"role": "user", "content": "取消了的旧请求"}
    msgs = [
        user_msg,
        {"role": "assistant", "content": "旧任务回复"},   # its own reply R
        {"role": "assistant", "content": "我帮你查下天气"},  # proactive utterance A
    ]
    sig = a._user_message_signature(user_msg)
    monkeypatch.setattr(a._task_tracker, "get_cancelled_user_sigs", lambda ln: {sig})

    out = a._redact_cancelled_user_turns(msgs, "lan", preserve_trailing_assistant=True)
    # neither the cancelled user text nor its reply leak into the assessment
    assert not any(m.get("role") == "user" and "取消了的旧请求" in str(m.get("content", "")) for m in out)
    assert not any(m.get("role") == "assistant" and "旧任务回复" in str(m.get("content", "")) for m in out)
    assert any(m.get("role") == "system" and m.get("content") == a.REDACTED_USER_TURN_MARKER for m in out)
    # proactive utterance survives
    assert any(m.get("role") == "assistant" and "天气" in str(m.get("content", "")) for m in out)
    # sanity: the non-proactive default still applies the first-time bypass
    # (single trailing assistant → user kept), i.e. behavior only changes on proactive
    single = [user_msg, {"role": "assistant", "content": "刚刚的回复"}]
    kept = a._redact_cancelled_user_turns(single, "lan")
    assert any(m.get("role") == "user" and "取消了的旧请求" in str(m.get("content", "")) for m in kept)


# ── proactive OpenClaw dispatch forces the default sender ────────────
def test_proactive_openclaw_dispatch_uses_default_sender(monkeypatch):
    # A proactive OpenClaw task has no triggering user; the latest user in the
    # window is a stale prior turn. Its sender_id must NOT be used — otherwise a
    # self-initiated action (or proactive /stop) runs under that user's
    # persistent OpenClaw session in multi-user setups. Proactive → default sender.
    from app.agent_server.channels import openclaw as oc

    captured = {}

    class _FakeOpenClaw:
        default_sender_id = "DEFAULT_SENDER"

        def normalize_magic_command(self, x):
            return None  # non-magic path

        def get_or_create_persistent_session_id(self, *, role_name, sender_id):
            captured["sender_id"] = sender_id
            return "sess-1"

        async def run_instruction(self, *a, **kw):
            return {"success": True, "reply": ""}

    monkeypatch.setitem(oc._shared.Modules.agent_flags, "openclaw_enabled", True)
    monkeypatch.setattr(oc._shared.Modules, "openclaw", _FakeOpenClaw())

    async def _noop_emit(*a, **kw):
        return None
    monkeypatch.setattr(oc, "_emit_main_event", _noop_emit)
    monkeypatch.setattr(oc, "_emit_task_result", _noop_emit)
    monkeypatch.setattr(oc._task_tracker, "record_assigned", lambda *a, **kw: None)

    class _Result:
        task_id = "t1"
        task_description = "查天气"
        tool_args = {"instruction": "查一下天气"}

    # messages carry a stale user with an explicit sender_id
    msgs = [
        {"role": "user", "content": "旧请求", "sender_id": "USER_A"},
        {"role": "assistant", "content": "我帮你查下天气"},
    ]

    async def _drive():
        await oc.dispatch(
            _Result(), messages=msgs, lanlan_name="lan",
            conversation_id="c", trigger_user_msg_sig=None, proactive=True,
        )
        await asyncio.sleep(0.02)  # let the spawned run_instruction task settle

    asyncio.run(_drive())
    assert captured.get("sender_id") == "DEFAULT_SENDER"

    # sanity: a non-proactive dispatch DOES resolve the stale user's sender
    captured.clear()

    async def _drive_user():
        await oc.dispatch(
            _Result(), messages=msgs, lanlan_name="lan",
            conversation_id="c", trigger_user_msg_sig=None, proactive=False,
        )
        await asyncio.sleep(0.02)

    asyncio.run(_drive_user())
    assert captured.get("sender_id") == "USER_A"


# ── executor uses the assistant utterance as LATEST_USER_REQUEST on proactive ─
def test_executor_format_messages_proactive_intent():
    ex = DirectTaskExecutor(computer_use=object())
    msgs = [
        {"role": "user", "content": "旧请求"},
        {"role": "assistant", "content": "我帮你查下天气吧"},
    ]
    # proactive: LATEST_USER_REQUEST is lanlan's own utterance, not the old user line
    conv_p = ex._format_messages(msgs, proactive=True)
    assert "LATEST_USER_REQUEST: 我帮你查下天气吧" in conv_p
    assert ex._extract_latest_user_intent(conv_p) == "我帮你查下天气吧"
    # ordinary turn: LATEST_USER_REQUEST is the user line
    conv_u = ex._format_messages(msgs, proactive=False)
    assert "LATEST_USER_REQUEST: 旧请求" in conv_u
