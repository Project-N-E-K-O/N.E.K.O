# -*- coding: utf-8 -*-
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

"""``/daemon approve`` is dropped unless a live OpenClaw task exists.

The command makes the upstream QwenPaw daemon actually run a pending high-risk
action, and nothing on the path ever checked that the utterance was answering a
pending approval — the repo holds no approval state at all (that state lives
only inside the upstream daemon, which the adapter reaches over a one-shot
POST). With no live task there is nothing an approval could refer to, so the
dispatch is dropped.
"""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.agent_server._shared import TASK_REGISTRY_CLEANUP_TTL
from app.agent_server.channels import openclaw as oc


class _Result:
    def __init__(self, command, task_id="magic-1", user_text="没问题"):
        self.task_id = task_id
        self.task_description = "批准当前 QwenPaw 高风险动作"
        self.tool_args = {
            "instruction": command,
            "attachments": [],
            "magic_command": command,
            "original_user_text": user_text,
            "direct_reply": True,
        }


class _FakeOpenClaw:
    default_sender_id = "DEFAULT_SENDER"

    def __init__(self):
        self.magic_calls = []
        self.stop_calls = []

    @staticmethod
    def normalize_magic_command(command):
        from brain.openclaw_adapter import OpenClawAdapter

        return OpenClawAdapter.normalize_magic_command(command)

    async def run_magic_command(self, command, *, sender_id=None, role_name=None):
        self.magic_calls.append((command, sender_id, role_name))
        return {"success": True, "reply": "收到", "command": command}

    async def stop_running(self, **kwargs):
        self.stop_calls.append(kwargs)
        return {"success": True}

    def get_or_create_persistent_session_id(self, *, role_name, sender_id):
        return "sess-1"

    def reset_persistent_session_id(self, *, role_name, sender_id):
        return "sess-2"


@pytest.fixture
def wired(monkeypatch):
    """Wire the channel module against a fake adapter and an empty registry."""
    fake = _FakeOpenClaw()
    emitted = []

    async def _record_task_result(*args, **kwargs):
        emitted.append(kwargs)

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setitem(oc._shared.Modules.agent_flags, "openclaw_enabled", True)
    monkeypatch.setattr(oc._shared.Modules, "openclaw", fake)
    monkeypatch.setattr(oc._shared.Modules, "task_registry", {})
    monkeypatch.setattr(oc._shared.Modules, "task_async_handles", {})
    monkeypatch.setattr(oc, "_emit_task_result", _record_task_result)
    monkeypatch.setattr(oc, "_emit_main_event", _noop)
    monkeypatch.setattr(oc._task_tracker, "record_assigned", lambda *a, **kw: None)
    monkeypatch.setattr(oc._task_tracker, "record_completed", lambda *a, **kw: None)
    return fake, emitted


def _dispatch(
    command, *, sender="USER_A", task_id="magic-1", user_text="没问题", proactive=False
):
    messages = [{"role": "user", "content": user_text, "sender_id": sender}]
    asyncio.run(
        oc.dispatch(
            _Result(command, task_id=task_id, user_text=user_text),
            messages=messages,
            lanlan_name="lan",
            conversation_id="c",
            trigger_user_msg_sig=None,
            proactive=proactive,
        )
    )


def _iso(seconds_ago: float) -> str:
    stamp = datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)
    return stamp.isoformat().replace("+00:00", "Z")


def _register(
    registry,
    task_id,
    *,
    status,
    sender="USER_A",
    lanlan="lan",
    kind="openclaw",
    ended_seconds_ago=1.0,
):
    info = {
        "id": task_id,
        "type": kind,
        "status": status,
        "sender_id": sender,
        "lanlan_name": lanlan,
        "start_time": _iso((ended_seconds_ago or 0) + 5),
        "params": {},
    }
    if status not in {"queued", "running"} and ended_seconds_ago is not None:
        info["end_time"] = _iso(ended_seconds_ago)
    registry[task_id] = info


def test_approve_is_dropped_without_a_live_openclaw_task(wired):
    fake, emitted = wired

    _dispatch("/daemon approve")

    assert fake.magic_calls == [], "没有活任务时不该把批准发给上游"
    # 静默：不 emit task_result，所以前端不会念出「收到许可！Neko 这就放手去干喵！」
    assert emitted == [], "静默丢弃不该产生任何 task_result"


def test_approve_goes_through_when_a_task_is_live(wired):
    fake, emitted = wired
    _register(oc._shared.Modules.task_registry, "t-running", status="running")

    _dispatch("/daemon approve")

    assert [call[0] for call in fake.magic_calls] == ["/daemon approve"]
    assert emitted and emitted[0].get("success") is True


@pytest.mark.parametrize(
    "status", ["queued", "running", "completed", "failed", "cancelled", "partial"]
)
def test_a_registry_entry_in_any_status_opens_the_gate(wired, status):
    """⚠️ Terminal statuses count on purpose — requiring "running" is backwards.

    ``run_instruction`` is a one-shot POST, so QwenPaw's "I need permission"
    surfaces as that POST's reply, and ``_run_openclaw_dispatch`` writes
    ``status=completed`` the moment the POST returns — *before*
    ``_emit_task_result`` speaks the reply. By the time the user can say 同意,
    the task is necessarily terminal. Gating on "running" would drop every
    legitimate approval and leave open only the unrelated-task case, i.e. exactly
    inverted. ``TASK_REGISTRY_CLEANUP_TTL`` purges terminal entries, so the
    registry itself bounds the window.
    """  # noqa: DOCSTRING_CJK
    fake, _ = wired
    _register(oc._shared.Modules.task_registry, "t-live", status=status)

    _dispatch("/daemon approve")

    assert fake.magic_calls, f"status={status} 仍在 registry 里，应该放行批准"


def test_a_stale_terminal_entry_does_not_open_the_gate(wired):
    """⚠️ Age is checked here, not assumed from the cleanup having run.

    ``_cleanup_task_registry`` is only called from capabilities.py's status
    emission paths — the ordinary analysis/dispatch path never touches it. In a
    long-lived session a terminal entry can therefore sit in the registry
    indefinitely, and "still present" would let a task from hours ago hold the
    gate open for every later everyday 同意.
    """  # noqa: DOCSTRING_CJK
    fake, _ = wired
    _register(
        oc._shared.Modules.task_registry,
        "t-stale",
        status="completed",
        ended_seconds_ago=TASK_REGISTRY_CLEANUP_TTL + 60,
    )

    _dispatch("/daemon approve")

    assert fake.magic_calls == []


def test_a_terminal_entry_without_an_end_time_fails_closed(wired):
    fake, _ = wired
    _register(
        oc._shared.Modules.task_registry,
        "t-noend",
        status="completed",
        ended_seconds_ago=None,
    )

    _dispatch("/daemon approve")

    assert fake.magic_calls == [], "判不了龄就不该放行"


def test_a_proactive_turn_can_never_authorize(wired):
    """⚠️ A proactive turn has no user at all.

    ``task_executor`` swaps the intent for the character's own latest utterance
    on proactive turns, so her everyday 「没问题」 classifies as an approval while
    the user has said nothing this turn. Approve is the one command that makes
    the upstream daemon really run a high-risk action, so a proactive turn never
    dispatches it — regardless of what the registry holds.
    """  # noqa: DOCSTRING_CJK
    fake, emitted = wired
    # ⚠️ 必须注册在 default_sender_id 名下。proactive 轮会把 nk_sender_id 强制成
    # default（见 _resolve_openclaw_sender_id 上方的说明），登记在别的 sender 名下
    # 时闸会先被 sender 过滤挡掉——那样这条测试就永远绿，验不到 proactive 这道判据。
    _register(
        oc._shared.Modules.task_registry,
        "t-running",
        status="running",
        sender=fake.default_sender_id,
    )

    _dispatch("/daemon approve", proactive=True, sender=fake.default_sender_id)

    assert fake.magic_calls == [], "主动搭话轮没有用户，绝不能批准"
    assert emitted == []

    # sanity: 同一个 registry 状态、同一个 sender，非 proactive 轮照常放行——
    # 证明上面拦住它的确实是 proactive 而不是别的条件
    _dispatch("/daemon approve", proactive=False, sender=fake.default_sender_id)
    assert [c[0] for c in fake.magic_calls] == ["/daemon approve"]


@pytest.mark.parametrize("command", ["/stop", "/new", "/clear"])
def test_the_proactive_block_is_scoped_to_approve(wired, command):
    """A proactive /stop is a designed feature (see _resolve_openclaw_sender_id)."""
    fake, _ = wired

    _dispatch(command, proactive=True)

    assert [c[0] for c in fake.magic_calls] == [command]


def test_an_empty_registry_still_closes_the_gate(wired):
    """The case the gate exists for: chatting with no agent activity at all."""
    fake, emitted = wired

    _dispatch("/daemon approve")

    assert fake.magic_calls == []
    assert emitted == []


def test_an_explicitly_typed_magic_word_is_never_gated(wired):
    """⚠️ The gate filters free-text inference only.

    Typing ``/openclaw approve`` routes through core/turn.py's explicit branch,
    which returns before the normal reply path — ``_emit_task_result`` is the
    only user-visible output left on it. Dropping that silently means the user
    typed an unambiguous command, lost their attached images and a turn, and got
    nothing back, so they just retype it.
    """  # noqa: DOCSTRING_CJK
    fake, emitted = wired
    assert oc._shared.Modules.task_registry == {}

    for typed in ("/daemon approve", "daemon approve", "/approve", "approve"):
        fake.magic_calls.clear()
        _dispatch("/daemon approve", user_text=typed)
        assert [c[0] for c in fake.magic_calls] == ["/daemon approve"], typed
    assert emitted


def test_another_senders_task_does_not_open_the_gate(wired):
    """⚠️ Multi-user setups: approving under someone else's pending action is
    exactly the confused-deputy shape the gate exists to prevent."""
    fake, _ = wired
    _register(oc._shared.Modules.task_registry, "t-other", status="running", sender="USER_B")

    _dispatch("/daemon approve", sender="USER_A")

    assert fake.magic_calls == []


def test_another_characters_task_does_not_open_the_gate(wired):
    fake, _ = wired
    _register(oc._shared.Modules.task_registry, "t-other", status="running", lanlan="other")

    _dispatch("/daemon approve")

    assert fake.magic_calls == []


def test_a_non_openclaw_task_does_not_open_the_gate(wired):
    """A running browser/plugin task has no QwenPaw approval to grant."""
    fake, _ = wired
    _register(oc._shared.Modules.task_registry, "t-browser", status="running", kind="browser_use")

    _dispatch("/daemon approve")

    assert fake.magic_calls == []


def test_the_approve_task_itself_never_counts_as_its_own_live_task(wired):
    """⚠️ Self-authorisation guard.

    Magic commands do not enter task_registry today (registration happens in the
    non-magic branch), so this cannot fire — but if that ever changes, the gate
    must not be satisfied by the approve dispatch itself.
    """  # noqa: DOCSTRING_CJK
    fake, _ = wired
    _register(oc._shared.Modules.task_registry, "magic-1", status="running")

    _dispatch("/daemon approve", task_id="magic-1")

    assert fake.magic_calls == []


@pytest.mark.parametrize("command", ["/stop", "/new", "/clear"])
def test_the_gate_does_not_leak_to_the_other_magic_commands(wired, command):
    """⚠️ The gate is scoped to approve on purpose.

    /stop, /new and /clear must still dispatch with an empty registry — /stop in
    particular is how a user halts things, and gating it on "a task is running"
    would make it useless exactly when the registry is out of sync.
    """  # noqa: DOCSTRING_CJK
    fake, emitted = wired

    _dispatch(command)

    assert [call[0] for call in fake.magic_calls] == [command]
    assert emitted, f"{command} 应该照常产生 task_result"
