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

"""``/daemon approve`` is dropped unless this sender just completed a task.

The command makes the upstream QwenPaw daemon actually run a pending high-risk
action, and nothing on the path ever checked that the utterance was answering a
pending approval — the repo holds no approval state at all (that state lives
only inside the upstream daemon, which the adapter reaches over a one-shot
POST).

The local approximation asks one question: **could the user have seen the
approval prompt?** The upstream reply reaches them only via
``_emit_task_result`` after the task flips to ``completed``, so that is the one
status the window accepts — see
test_statuses_that_cannot_carry_an_approval_prompt for why every other one is
excluded.
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

    @staticmethod
    def stop_trigger_tier(user_text):
        from brain.openclaw_adapter import OpenClawAdapter

        return OpenClawAdapter.stop_trigger_tier(user_text)

    @staticmethod
    def parse_typed_magic_command(user_text):
        from brain.openclaw_adapter import OpenClawAdapter

        return OpenClawAdapter.parse_typed_magic_command(user_text)

    async def run_magic_command(self, command, *, sender_id=None, role_name=None):
        self.magic_calls.append((command, sender_id, role_name))
        return {"success": True, "reply": "收到", "command": command}

    async def stop_running(self, **kwargs):
        self.stop_calls.append(kwargs)
        return {"success": True}

    current_session = "sess-1"

    def get_or_create_persistent_session_id(self, *, role_name, sender_id):
        return self.current_session

    def peek_persistent_session_id(self, *, role_name, sender_id):
        return self.current_session

    def reset_persistent_session_id(self, *, role_name, sender_id):
        self.current_session = "sess-2"
        return self.current_session


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
    session_id="sess-1",
    reply="发现 3 个重复文件，要删掉吗？",
):
    info = {
        "id": task_id,
        "type": kind,
        "status": status,
        "sender_id": sender,
        "lanlan_name": lanlan,
        "session_id": session_id,
        "start_time": _iso((ended_seconds_ago or 0) + 5),
        "params": {},
        # ⚠ Default to a **question** reply. Windows now require an actual question; checking only completed
        # tasks lets 整理完成，共移动 12 个文件 authorize later casual 同意.
        # Explicitly pass reply=... to test nonquestion replies.
        "result": {"reply": reply},
    }
    # ⚠ Give queued/running end_time too, though production never does.
    # **Isolate status filtering**: otherwise mutations adding running to allowed statuses
    # are blocked by unknown-age fail-closed, falsely passing
    # without testing status checks (mutation testing exposed it). Dedicated aging cases
    # pass ended_seconds_ago=None.
    if ended_seconds_ago is not None:
        info["end_time"] = _iso(ended_seconds_ago)
    registry[task_id] = info


def test_approve_is_dropped_with_no_task_on_record(wired):
    fake, emitted = wired

    _dispatch("/daemon approve")

    assert fake.magic_calls == [], "没有任何任务记录时不该把批准发给上游"
    # Silent: emit no task_result, so frontends never speak 收到许可！Neko 这就放手去干喵！
    assert emitted == [], "静默丢弃不该产生任何 task_result"


def test_approve_goes_through_after_a_recent_completion(wired):
    fake, emitted = wired
    _register(oc._shared.Modules.task_registry, "t-done", status="completed")

    _dispatch("/daemon approve")

    assert [call[0] for call in fake.magic_calls] == ["/daemon approve"]
    assert emitted and emitted[0].get("success") is True


@pytest.mark.parametrize("status", ["queued", "running", "cancelled", "failed"])
def test_statuses_that_cannot_carry_an_approval_prompt(wired, status):
    """⚠️ The window's test is "could the user have SEEN the prompt", not "did the
    task end" and not "is any task active".

    ``queued`` / ``running`` — the reply has not come back yet;
    ``_run_openclaw_dispatch`` only calls ``_emit_task_result`` after
    ``run_instruction`` returns and the entry flips to ``completed``. Letting an
    in-flight entry open the gate means an *unrelated* piece of active work
    authorizes a high-risk action — which is the very scenario this gate exists
    to close.

    ``failed`` — the reply text only ships on the success branch
    (``_emit_task_result(detail=reply)``); the failure branches send the fixed
    ``openclaw_failed`` / ``openclaw_dispatch_failed`` phrases and never forward
    ``reply``. So on a timeout, connection error, HTTP failure, or missing final
    reply the user cannot know anything is awaiting approval, and a later 同意
    is definitionally not answering one. Counting it would only open the door for
    a *misclassified* approval, at exactly the moment the upstream action may
    still be hanging.

    ``cancelled`` — worse: the user just killed that task, so the upstream action
    is precisely what they did not want, and ``_cancel_openclaw_tasks_for_stop``
    writes ``end_time`` even when its ``stop_running`` call failed.

    Anyone who learns of a pending approval by other means (QwenPaw's own
    console) can still type the literal ``/openclaw approve`` — explicit commands
    bypass the gate entirely.
    """  # noqa: DOCSTRING_CJK
    fake, emitted = wired
    _register(oc._shared.Modules.task_registry, f"t-{status}", status=status)

    _dispatch("/daemon approve")

    assert fake.magic_calls == [], f"status={status} 不可能承载审批提示，不该放行"
    assert emitted == []


def test_the_window_set_contains_only_statuses_this_module_writes():
    """⚠ Derive registry statuses from **write sites**, not every literal appearing in source.

    An earlier version scanned the whole module text for each status name, which
    also picked up comments, docstrings and — worse — read-only predicates like
    ``if info.get("status") not in {"queued", "running"}`` in
    _collect_active_openclaw_task_ids. ``queued`` therefore counted as "written",
    and putting it into the approval window passed this guard unnoticed.

    Here the write sites are located by AST: assignments to ``x["status"]`` plus
    the ``"status"`` key of the registry-init dict literal.
    """  # noqa: DOCSTRING_CJK
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(oc))

    def _literals(node):
        """Every string constant a status expression can evaluate to."""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return {node.value}
        if isinstance(node, ast.IfExp):  # "completed" if success else "failed"
            return _literals(node.body) | _literals(node.orelse)
        return set()

    written = set()
    for node in ast.walk(tree):
        # info["status"] = ... / _reg["status"] = ...
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Subscript)
                    and isinstance(target.slice, ast.Constant)
                    and target.slice.value == "status"
                ):
                    written |= _literals(node.value)
        # task_registry[task_id] = {..., "status": "running", ...}
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and key.value == "status":
                    written |= _literals(value)

    assert written, "没找到任何状态写入点，这条守卫的推导方式失效了"
    assert "partial" not in written, "partial 现在可达了，窗口集合要重新评估"
    assert "queued" not in written, (
        "queued 现在是被写入的状态了；它此前只出现在只读判据里，"
        "正是这条守卫上一版误收的那个"
    )
    assert oc._APPROVAL_WINDOW_STATUSES <= written, (
        f"窗口收了不会被写入的状态 → {sorted(oc._APPROVAL_WINDOW_STATUSES - written)}"
    )


def test_a_recently_completed_task_opens_the_gate(wired):
    """⚠️ ``completed`` counts on purpose — requiring "running" is backwards.

    ``run_instruction`` is a one-shot POST, so QwenPaw's "I need permission"
    surfaces as that POST's reply, and ``_run_openclaw_dispatch`` writes
    ``status=completed`` the moment the POST returns — *before*
    ``_emit_task_result`` speaks the reply. By the time the user can say 同意,
    the task is necessarily terminal. Gating on "running" would drop every
    legitimate approval and leave open only the unrelated-task case, i.e. exactly
    inverted.

    ⚠️ The window is bounded by the explicit ``end_time`` age check in
    ``_iter_approval_window_tasks`` — NOT by the registry cleanup, which the
    dispatch path never invokes. Do not "simplify" that check away; see
    test_a_stale_terminal_entry_does_not_open_the_gate.

    The other terminal statuses are excluded — see
    test_statuses_that_cannot_carry_an_approval_prompt.
    """  # noqa: DOCSTRING_CJK
    fake, _ = wired
    _register(oc._shared.Modules.task_registry, "t-done", status="completed")

    _dispatch("/daemon approve")

    assert fake.magic_calls, "刚 completed 的任务是唯一可能承载审批提示的状态"


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
    # ⚠ Register under default_sender_id. Proactive turns force nk_sender_id to default
    # (see _resolve_openclaw_sender_id comments); other senders fail earlier sender
    # filtering, falsely passing without testing proactive gating.
    _register(
        oc._shared.Modules.task_registry,
        "t-done",
        status="completed",
        sender=fake.default_sender_id,
    )

    _dispatch("/daemon approve", proactive=True, sender=fake.default_sender_id)

    assert fake.magic_calls == [], "主动搭话轮没有用户，绝不能批准"
    assert emitted == []

    # ⚠ Even literal explicit magic words fail. Proactive-always-reject and explicit-command
    # exemption are adjacent branches whose order defines this case, previously untested:
    # moving exemptions before proactive rejection leaves the whole gate file green. Proactive
    # turns have no user input, so explicit commands cannot exist; catgirl lines resembling commands get no exemption.
    _dispatch(
        "/daemon approve",
        proactive=True,
        sender=fake.default_sender_id,
        user_text="/daemon approve",
    )
    assert fake.magic_calls == [], "proactive 轮里显式豁免不得越过 proactive 阻断"

    # Sanity: same registry/sender passes on nonproactive turns,
    # proving proactive status caused rejection, not another condition.
    _dispatch("/daemon approve", proactive=False, sender=fake.default_sender_id)
    assert [c[0] for c in fake.magic_calls] == ["/daemon approve"]


@pytest.mark.parametrize("command", ["/stop", "/new", "/clear"])
def test_the_proactive_block_is_scoped_to_approve(wired, command):
    """A proactive /stop is a designed feature (see _resolve_openclaw_sender_id)."""
    fake, _ = wired

    _dispatch(command, proactive=True,
              user_text="取消这个任务" if command == "/stop" else command)

    assert [c[0] for c in fake.magic_calls] == [command]


def test_one_completion_authorizes_only_one_inferred_approval(wired):
    """⚠ One approval prompt authorizes only one inferred approval.

    Without consuming the entry, the same completion keeps the gate open for
    every 同意 / 沒問題 in the remaining TTL — and those later ones have no
    corresponding prompt, so they may approve a *different* pending action that
    showed up later in the same upstream session.
    """  # noqa: DOCSTRING_CJK
    fake, _ = wired
    _register(oc._shared.Modules.task_registry, "t-done", status="completed")

    _dispatch("/daemon approve")
    assert [c[0] for c in fake.magic_calls] == ["/daemon approve"]

    fake.magic_calls.clear()
    _dispatch("/daemon approve")
    assert fake.magic_calls == [], "同一条 completed 记录不该授权第二次"

    # Literal explicit magic words still bypass admission regardless of consumption.
    _dispatch("/daemon approve", user_text="/daemon approve")
    assert [c[0] for c in fake.magic_calls] == ["/daemon approve"]


def test_a_failed_dispatch_still_consumes_the_window(wired):
    """⚠ ``success=False`` does not mean approval was never delivered.

    ``run_instruction`` returns success=False when the POST came back fine but no
    final reply could be extracted — so the flag conflates "never sent" with
    "sent, possibly executed, unreadable". Keeping the window open on the second
    reading hands the same 同意 a free second shot at a *different* pending
    action. The two are indistinguishable here, so take the fail-closed side.

    Cost: a genuinely failed dispatch now needs the literal command typed.
    """  # noqa: DOCSTRING_CJK
    fake, _ = wired
    _register(oc._shared.Modules.task_registry, "t-done", status="completed")

    async def _fail(command, *, sender_id=None, role_name=None):
        fake.magic_calls.append((command, sender_id, role_name))
        return {"success": False, "error": "boom", "command": command}

    fake.run_magic_command = _fail
    _dispatch("/daemon approve")
    assert len(fake.magic_calls) == 1

    fake.run_magic_command = _FakeOpenClaw.run_magic_command.__get__(fake)
    _dispatch("/daemon approve")
    assert len(fake.magic_calls) == 1, "失败的那次也把窗口兑现掉了"

    # Escape hatch: explicit literal commands always bypass admission.
    _dispatch("/daemon approve", user_text="/daemon approve")
    assert len(fake.magic_calls) == 2


def test_an_inferred_approval_consumes_every_indistinguishable_window(wired):
    """⚠ `/daemon approve` lacks task ids and cannot identify which completed task supplied the prompt.

    Consuming only one leaves the other standing, so a later casual 同意
    authorizes some *other* pending action with no new prompt behind it. Since
    the records are indistinguishable at this layer, one inferred approval
    spends the whole ambiguous batch.
    """  # noqa: DOCSTRING_CJK
    fake, _ = wired
    registry = oc._shared.Modules.task_registry
    _register(registry, "t-old", status="completed", ended_seconds_ago=4.0)
    _register(registry, "t-prompt", status="completed", ended_seconds_ago=1.0)

    _dispatch("/daemon approve", task_id="magic-1")
    assert [c[0] for c in fake.magic_calls] == ["/daemon approve"]
    assert registry["t-old"][oc._APPROVAL_CONSUMED_KEY] is True
    assert registry["t-prompt"][oc._APPROVAL_CONSUMED_KEY] is True

    fake.magic_calls.clear()
    _dispatch("/daemon approve", task_id="magic-2")
    assert fake.magic_calls == [], "第二条窗口不该再授权一次"


def test_the_window_is_consumed_before_the_upstream_call(wired):
    """Consume before await; upstream exceptions cannot bypass it."""  # noqa: DOCSTRING_CJK
    fake, _ = wired
    _register(oc._shared.Modules.task_registry, "t-done", status="completed")

    async def _boom(command, *, sender_id=None, role_name=None):
        fake.magic_calls.append((command, sender_id, role_name))
        raise RuntimeError("connection reset")

    fake.run_magic_command = _boom
    _dispatch("/daemon approve", task_id="magic-1")
    assert oc._shared.Modules.task_registry["t-done"][oc._APPROVAL_CONSUMED_KEY] is True

    fake.run_magic_command = _FakeOpenClaw.run_magic_command.__get__(fake)
    fake.magic_calls.clear()
    _dispatch("/daemon approve", task_id="magic-2")
    assert fake.magic_calls == []


def test_a_completion_from_a_rotated_session_does_not_open_the_gate(wired):
    """⚠️ ``/new`` rotates the persistent session, and the old prompt belonged to
    the old one.

    ``run_magic_command("/new")`` calls ``reset_persistent_session_id``. A later
    inferred 同意 would be dispatched under the *new* session, so it cannot be
    answering the approval prompt that came out of the old one — and it could
    authorize an unrelated pending action in the replacement session.
    """  # noqa: DOCSTRING_CJK
    fake, _ = wired
    _register(
        oc._shared.Modules.task_registry,
        "t-old-session",
        status="completed",
        session_id="sess-1",
    )
    fake.reset_persistent_session_id(role_name="lan", sender_id="USER_A")
    assert fake.current_session == "sess-2"

    _dispatch("/daemon approve")

    assert fake.magic_calls == [], "旧会话的完成记录不该给新会话开闸"


def test_a_future_end_time_does_not_open_the_gate(wired):
    """⚠ Require lower bounds as well as upper bounds.

    A backward clock step (or any entry carrying a future ``end_time``) makes
    ``now - ended`` negative, which satisfies an upper-bound-only check forever —
    the "five minute" window then stays open until the clock catches up and
    another five minutes elapse.
    """  # noqa: DOCSTRING_CJK
    fake, _ = wired
    _register(
        oc._shared.Modules.task_registry,
        "t-future",
        status="completed",
        ended_seconds_ago=-3600,
    )

    _dispatch("/daemon approve")

    assert fake.magic_calls == [], "未来时间戳不该让窗口恒开"


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

    for typed in ("/daemon approve", "/approve"):
        fake.magic_calls.clear()
        _dispatch("/daemon approve", user_text=typed)
        assert [c[0] for c in fake.magic_calls] == ["/daemon approve"], typed
    assert emitted

    # ⚠ Bare words without slashes **are not** manually entered commands and get no exemption;
    # otherwise casual English approve bypasses the entire approval gate.
    for not_typed in ("approve", "daemon approve", "Approve"):
        fake.magic_calls.clear()
        _dispatch("/daemon approve", user_text=not_typed)
        assert fake.magic_calls == [], not_typed


def test_another_senders_task_does_not_open_the_gate(wired):
    """⚠️ Multi-user setups: approving under someone else's pending action is
    exactly the confused-deputy shape the gate exists to prevent."""
    fake, _ = wired
    _register(oc._shared.Modules.task_registry, "t-other", status="completed", sender="USER_B")

    _dispatch("/daemon approve", sender="USER_A")

    assert fake.magic_calls == []


def test_another_characters_task_does_not_open_the_gate(wired):
    fake, _ = wired
    _register(oc._shared.Modules.task_registry, "t-other", status="completed", lanlan="other")

    _dispatch("/daemon approve")

    assert fake.magic_calls == []


def test_a_non_openclaw_task_does_not_open_the_gate(wired):
    """A running browser/plugin task has no QwenPaw approval to grant."""
    fake, _ = wired
    _register(oc._shared.Modules.task_registry, "t-browser", status="completed", kind="browser_use")

    _dispatch("/daemon approve")

    assert fake.magic_calls == []


def test_the_approve_task_itself_never_counts_as_its_own_live_task(wired):
    """⚠️ Self-authorisation guard.

    Magic commands do not enter task_registry today (registration happens in the
    non-magic branch), so this cannot fire — but if that ever changes, the gate
    must not be satisfied by the approve dispatch itself.
    """  # noqa: DOCSTRING_CJK
    fake, _ = wired
    _register(oc._shared.Modules.task_registry, "magic-1", status="completed")

    _dispatch("/daemon approve", task_id="magic-1")

    assert fake.magic_calls == []


@pytest.mark.parametrize("command", ["/stop", "/new", "/clear"])
def test_the_gate_does_not_leak_to_the_other_magic_commands(wired, command):
    """⚠️ The gate is scoped to approve on purpose.

    /stop, /new and /clear must still dispatch with an empty registry — /stop in
    particular is how a user halts things, and gating it on "a task is running"
    would make it useless exactly when the registry is out of sync.

    ⚠️ /stop goes in with an **addressed** phrasing: that tier is the designed
    escape hatch and needs no corroboration. The ambiguous tier is covered by
    test_stop_needs_a_running_task_when_the_phrasing_is_ambiguous.
    """  # noqa: DOCSTRING_CJK
    fake, emitted = wired

    _dispatch(command, user_text="取消这个任务" if command == "/stop" else command)

    assert [call[0] for call in fake.magic_calls] == [command]
    assert emitted, f"{command} 应该照常产生 task_result"


def test_stop_retires_a_standing_approval_window(wired):
    """⚠ Cancelling tasks cannot retract approval prompts already spoken.

    ``_cancel_openclaw_tasks_for_stop`` only touches queued/running entries, and
    the window is opened by a *completed* one — two disjoint status sets. Left
    standing, a casual 同意 anywhere in the remaining TTL puts back the very
    action the user just cancelled.
    """  # noqa: DOCSTRING_CJK
    fake, _ = wired
    _register(oc._shared.Modules.task_registry, "t-done", status="completed")
    _register(
        oc._shared.Modules.task_registry,
        "t-live",
        status="running",
        ended_seconds_ago=None,
    )

    _dispatch("/stop", task_id="magic-stop", user_text="取消这个任务")
    assert [c[0] for c in fake.magic_calls] == ["/stop"]

    fake.magic_calls.clear()
    _dispatch("/daemon approve", task_id="magic-approve")
    assert fake.magic_calls == [], "/stop 之后那条 completed 不该再授权"


def test_stop_retires_the_window_with_nothing_left_to_cancel(wired):
    """⚠ Consumption must not depend on cancelling something successfully.

    The reported sequence has *nothing* queued or running — the task completed
    and is waiting on the prompt — so ``cancelled_task_ids`` comes back empty.
    Gating retirement on that list reproduces the bug exactly.
    """  # noqa: DOCSTRING_CJK
    fake, _ = wired
    _register(oc._shared.Modules.task_registry, "t-done", status="completed")

    _dispatch("/stop", task_id="magic-stop", user_text="取消这个任务")
    assert fake.stop_calls == [], "前提：这一轮没有在跑的任务可掐"
    assert oc._shared.Modules.task_registry["t-done"][oc._APPROVAL_CONSUMED_KEY] is True

    fake.magic_calls.clear()
    _dispatch("/daemon approve", task_id="magic-approve")
    assert fake.magic_calls == [], "没掐到东西也要把窗口作废"


def test_stop_retires_the_window_even_when_the_upstream_call_fails(wired):
    """Local cancellation never rolls back; user stop intent is independent of upstream call success."""  # noqa: DOCSTRING_CJK
    fake, _ = wired
    _register(oc._shared.Modules.task_registry, "t-done", status="completed")

    async def _fail(command, *, sender_id=None, role_name=None):
        fake.magic_calls.append((command, sender_id, role_name))
        return {"success": False, "error": "boom", "command": command}

    fake.run_magic_command = _fail
    _dispatch("/stop", task_id="magic-stop", user_text="取消这个任务")

    fake.run_magic_command = _FakeOpenClaw.run_magic_command.__get__(fake)
    fake.magic_calls.clear()
    _dispatch("/daemon approve", task_id="magic-approve")
    assert fake.magic_calls == [], "上游 /stop 失败也不该让旧窗口活下来"


def test_stop_only_retires_windows_it_owns(wired):
    """⚠ One user's stop request must not invalidate **another user's** pending approval prompts.

    Sender is the real boundary; character is not — one sender's characters all
    share a single upstream session, see
    test_stop_retires_windows_of_the_senders_other_characters.
    """  # noqa: DOCSTRING_CJK
    fake, _ = wired
    registry = oc._shared.Modules.task_registry
    _register(registry, "t-other-sender", status="completed", sender="USER_B")

    _dispatch("/stop", sender="USER_A", task_id="magic-stop")

    assert registry["t-other-sender"].get(oc._APPROVAL_CONSUMED_KEY) is None

    fake.magic_calls.clear()
    _dispatch("/daemon approve", sender="USER_B", task_id="magic-approve")
    assert [c[0] for c in fake.magic_calls] == ["/daemon approve"]


def test_an_explicitly_typed_approve_survives_stop(wired):
    """Invalidation narrows fail-closed; literal commands retain admission exemptions."""  # noqa: DOCSTRING_CJK
    fake, _ = wired
    _register(oc._shared.Modules.task_registry, "t-done", status="completed")

    _dispatch("/stop", task_id="magic-stop", user_text="取消这个任务")
    fake.magic_calls.clear()

    _dispatch("/daemon approve", task_id="magic-approve", user_text="/daemon approve")
    assert [c[0] for c in fake.magic_calls] == ["/daemon approve"]


@pytest.mark.parametrize("command", ["/new", "/clear", "/daemon approve"])
def test_only_stop_retires_windows(wired, command):
    """⚠ Invalidate only on /stop.

    ``/daemon approve`` spends the whole ambiguous batch through its own path;
    widening retirement to every command would make an unrelated ``/clear`` eat
    a prompt the user never answered.
    """  # noqa: DOCSTRING_CJK
    fake, _ = wired
    registry = oc._shared.Modules.task_registry
    _register(registry, "t-a", status="completed", ended_seconds_ago=1.0)
    _register(registry, "t-b", status="completed", ended_seconds_ago=2.0)

    _dispatch(command, task_id="magic-1")

    consumed = [t for t in ("t-a", "t-b") if registry[t].get(oc._APPROVAL_CONSUMED_KEY)]
    expected = 2 if command == "/daemon approve" else 0
    assert len(consumed) == expected


def test_stop_retires_every_standing_window_not_just_the_first(wired):
    """⚠ Plural results are the **entire benefit** of changing finders from one id to lists.

    Two dispatches can both complete inside the TTL and both carry a prompt.
    Retiring only the head leaves the second one standing for the rest of the
    window — the same reversal this whole change exists to stop, just one
    utterance later. Without this case, reverting the loop to the old
    single-value semantics leaves the suite green.
    """  # noqa: DOCSTRING_CJK
    fake, _ = wired
    registry = oc._shared.Modules.task_registry
    _register(registry, "t-a", status="completed", ended_seconds_ago=3.0)
    _register(registry, "t-b", status="completed", ended_seconds_ago=1.0)

    _dispatch("/stop", task_id="magic-stop", user_text="取消这个任务")

    assert registry["t-a"].get(oc._APPROVAL_CONSUMED_KEY) is True
    assert registry["t-b"].get(oc._APPROVAL_CONSUMED_KEY) is True

    fake.magic_calls.clear()
    _dispatch("/daemon approve", task_id="magic-approve")
    assert fake.magic_calls == [], "第二条窗口也必须被作废"


def test_a_proactive_stop_never_retires_the_users_window(wired):
    """⚠ If we cannot approve for users, neither can we revoke their authorization.

    A proactive turn has no user — task_executor feeds the character's own line
    back into the classifier, and both turns resolve to the same sender. Letting
    her 「停下来」 retire the window silently eats the human's next 同意: the gate
    returns without emitting anything, so nothing is spoken back either.
    """  # noqa: DOCSTRING_CJK
    fake, emitted = wired
    # ⚠ Both sides use default_sender_id, matching production: main_logic never attaches
    # sender_id to analyze messages, so _resolve_openclaw_sender_id returns empty and both user
    # and proactive turns share one sender bucket. Fixture-default USER_A mismatches proactive
    # DEFAULT_SENDER, making invalidation irrelevant and tests pass for the wrong reason.
    home = fake.default_sender_id
    _register(oc._shared.Modules.task_registry, "t-done", status="completed", sender=home)

    _dispatch("/stop", task_id="magic-stop", sender=home, proactive=True, user_text="取消这个任务")
    assert [c[0] for c in fake.magic_calls] == ["/stop"], "主动轮的 /stop 本身照常派发"
    assert oc._APPROVAL_CONSUMED_KEY not in oc._shared.Modules.task_registry["t-done"]

    fake.magic_calls.clear()
    emitted.clear()
    _dispatch("/daemon approve", task_id="magic-approve", sender=home)
    assert [c[0] for c in fake.magic_calls] == ["/daemon approve"]


def test_stop_retires_a_window_whose_end_time_is_in_the_future(wired):
    """⚠ Invalidation writes once; admission predicates recompute every time.

    A backward clock step makes ``now - ended`` negative, so the entry is not in
    the window *right now* and a same-filter retirement walks past it. Once the
    clock catches up it is back in the window — with nobody left to retire it.
    Retirement must therefore match wider than the gate does.
    """  # noqa: DOCSTRING_CJK
    fake, _ = wired
    registry = oc._shared.Modules.task_registry
    _register(registry, "t-future", status="completed", ended_seconds_ago=-30.0)
    _register(registry, "t-stale", status="completed", ended_seconds_ago=oc.TASK_REGISTRY_CLEANUP_TTL + 30)
    _register(registry, "t-noend", status="completed", ended_seconds_ago=None)

    _dispatch("/stop", task_id="magic-stop", user_text="取消这个任务")

    for task_id in ("t-future", "t-stale", "t-noend"):
        assert registry[task_id].get(oc._APPROVAL_CONSUMED_KEY) is True, task_id


def test_stop_retires_windows_of_the_senders_other_characters(wired):
    """⚠ Upstream session keys ignore characters, so /stop's scope also ignores them.

    ``_build_session_key`` opens with ``del role_name``: every character of one
    sender shares a single upstream session. A stop issued under character B
    cancels the very action character A's prompt was about, so leaving A's
    window standing lets a later 同意 put it straight back.
    """  # noqa: DOCSTRING_CJK
    fake, _ = wired
    registry = oc._shared.Modules.task_registry
    _register(registry, "t-other-char", status="completed", lanlan="miku")
    _register(registry, "t-other-sender", status="completed", sender="USER_B")

    _dispatch("/stop", task_id="magic-stop", user_text="取消这个任务")

    assert registry["t-other-char"].get(oc._APPROVAL_CONSUMED_KEY) is True
    assert registry["t-other-sender"].get(oc._APPROVAL_CONSUMED_KEY) is None, (
        "跨 sender 是真的不相干，不能一起作废"
    )


def test_stop_retires_the_window_even_when_the_upstream_call_raises(wired):
    """⚠ Most upstream failures **raise** (connection reset/timeout), rather than returning success=False.

    Retirement sits before the ``await``, so an exception cannot skip it. Only
    the return-False half used to be covered, and moving the call after the
    await left the whole file green — verified by hand-moving it, which now
    turns this case red.
    """  # noqa: DOCSTRING_CJK
    fake, _ = wired
    _register(oc._shared.Modules.task_registry, "t-done", status="completed")

    async def _boom(command, *, sender_id=None, role_name=None):
        fake.magic_calls.append((command, sender_id, role_name))
        raise RuntimeError("connection reset")

    fake.run_magic_command = _boom
    _dispatch("/stop", task_id="magic-stop", user_text="取消这个任务")
    assert oc._shared.Modules.task_registry["t-done"][oc._APPROVAL_CONSUMED_KEY] is True

    fake.run_magic_command = _FakeOpenClaw.run_magic_command.__get__(fake)
    fake.magic_calls.clear()
    _dispatch("/daemon approve", task_id="magic-approve")
    assert fake.magic_calls == [], "上游抛异常也不该让旧窗口活下来"


def test_an_explicit_approval_still_retires_the_window(wired):
    """⚠ Explicit-command exemptions bypass **admission**, not consumption.

    Typing the literal command is the user answering the prompt. Skipping the
    whole block let the window stand, so a casual 同意 later in the TTL sent a
    second approval with no prompt behind it.
    """  # noqa: DOCSTRING_CJK
    fake, _ = wired
    _register(oc._shared.Modules.task_registry, "t-done", status="completed")

    _dispatch("/daemon approve", task_id="magic-1", user_text="/daemon approve")
    assert [c[0] for c in fake.magic_calls] == ["/daemon approve"]
    assert oc._shared.Modules.task_registry["t-done"][oc._APPROVAL_CONSUMED_KEY] is True

    fake.magic_calls.clear()
    _dispatch("/daemon approve", task_id="magic-2")
    assert fake.magic_calls == [], "显式批准之后那条窗口不该还能授权一次推断批准"


def test_an_explicit_approval_is_never_gated(wired):
    """Keep consumption and admission exemptions separate: even empty registries must dispatch explicit commands."""  # noqa: DOCSTRING_CJK
    fake, _ = wired
    assert oc._shared.Modules.task_registry == {}

    _dispatch("/daemon approve", task_id="magic-1", user_text="/daemon approve")
    assert [c[0] for c in fake.magic_calls] == ["/daemon approve"]


def test_an_unknown_current_session_closes_the_gate(wired):
    """⚠ Unknown current sessions fail closed for admission.

    The filter used to read ``if current_session and ...`` — a raised or empty
    peek left it empty and skipped the session check *entirely*, so entries from
    any session opened the gate. That is the narrow/wide principle inverted:
    skipping is wider, and wider is only safe on the retirement side.
    """  # noqa: DOCSTRING_CJK
    fake, _ = wired
    _register(oc._shared.Modules.task_registry, "t-done", status="completed")

    def _boom(*, role_name, sender_id):
        raise RuntimeError("session cache unreadable")

    fake.peek_persistent_session_id = _boom
    _dispatch("/daemon approve")
    assert fake.magic_calls == [], "问不出会话时不该放行推断批准"

    fake.peek_persistent_session_id = lambda *, role_name, sender_id: ""
    _dispatch("/daemon approve")
    assert fake.magic_calls == [], "会话为空时同样不该放行"


def test_stop_still_retires_when_the_session_is_unknown(wired):
    """Conversely, invalidation consumes all this sender's windows when sessions are unknown; prefer excess over omissions."""  # noqa: DOCSTRING_CJK
    fake, _ = wired
    registry = oc._shared.Modules.task_registry
    _register(registry, "t-done", status="completed")

    def _boom(*, role_name, sender_id):
        raise RuntimeError("session cache unreadable")

    fake.peek_persistent_session_id = _boom
    _dispatch("/stop", task_id="magic-stop", user_text="取消这个任务")
    assert registry["t-done"][oc._APPROVAL_CONSUMED_KEY] is True


def test_stop_retires_before_the_cancel_helper_can_raise(wired, monkeypatch):
    """⚠ Invalidate before cancellation helpers so their exceptions cannot skip it.

    ``_cancel_openclaw_tasks_for_stop`` awaits and calls ``record_completed``
    without a try, so ordering it first is free insurance — retirement depends
    on nothing the cancellation produces.
    """  # noqa: DOCSTRING_CJK
    fake, _ = wired
    registry = oc._shared.Modules.task_registry
    _register(registry, "t-done", status="completed")
    _register(registry, "t-live", status="running", ended_seconds_ago=None)

    def _boom(*a, **kw):
        raise RuntimeError("tracker exploded")

    # ⚠ Use monkeypatch, never assign then del: _task_tracker is a module singleton; manual deletion
    # also erases fixture stubs, leaving a broken shared tracker for later cases.
    # This repository has prior background-thread/singleton leaks contaminating subsequent tests.
    monkeypatch.setattr(oc._task_tracker, "record_completed", _boom)
    with pytest.raises(RuntimeError):
        _dispatch("/stop", task_id="magic-stop", user_text="取消这个任务")

    assert registry["t-done"][oc._APPROVAL_CONSUMED_KEY] is True, (
        "取消 helper 抛出之前，窗口就该已经作废掉了"
    )


def test_a_completion_that_asked_nothing_does_not_open_the_gate(wired):
    """⚠ Windows require an actual question, not merely completed tasks.

    Only a reply that actually asked something can be an approval prompt. Opening
    on any completion means a plain "整理完成，共移动 12 个文件" hands the next
    casual 同意 a live approval — the task never asked for one.
    """  # noqa: DOCSTRING_CJK
    fake, _ = wired
    _register(
        oc._shared.Modules.task_registry,
        "t-done",
        status="completed",
        reply="整理完成，共移动 12 个文件。",
    )

    _dispatch("/daemon approve")
    assert fake.magic_calls == [], "没问过问题的完成不该开闸"

    # Escape hatch: explicit literal commands remain exempt.
    _dispatch("/daemon approve", user_text="/daemon approve")
    assert [c[0] for c in fake.magic_calls] == ["/daemon approve"]


@pytest.mark.parametrize(
    "reply",
    ["要删掉吗？", "Proceed?", "需要我删掉嗎", "删掉这 3 个?", "现在删除嗎"],
)
def test_replies_that_can_carry_a_prompt_open_the_gate(wired, reply):
    """Only replies with explicit question markers count as prompts; recognize markers rather than enumerating prompt forms."""  # noqa: DOCSTRING_CJK
    fake, _ = wired
    _register(oc._shared.Modules.task_registry, "t-done", status="completed", reply=reply)

    _dispatch("/daemon approve")
    assert [c[0] for c in fake.magic_calls] == ["/daemon approve"], reply


def test_stop_needs_a_running_task_when_the_phrasing_is_ambiguous(wired):
    """⚠ 停下来 in roleplay may address the catgirl herself, not background tasks.

    Whole-clause matching kills the narration cases (雨停下来了) but cannot tell
    these apart — the sentence is identical either way. So this tier asks for
    corroboration: something must actually be running.
    """  # noqa: DOCSTRING_CJK
    fake, _ = wired

    _dispatch("/stop", user_text="停下来")
    assert fake.magic_calls == [], "没有在跑的任务时，模糊说法不该派 /stop"

    _register(
        oc._shared.Modules.task_registry,
        "t-live",
        status="running",
        ended_seconds_ago=None,
    )
    _dispatch("/stop", user_text="停下来")
    assert [c[0] for c in fake.magic_calls] == ["/stop"]


@pytest.mark.parametrize(
    "text", ["取消这个任务", "停止搜索", "算了别查了", "取消這個搜尋", "/stop"]
)
def test_an_addressed_stop_never_needs_corroboration(wired, text):
    """⚠ Never seal the escape hatch shut.

    registry lies exactly when /stop matters most — a timed-out request is written
    as ``failed``, a restart empties the registry, TTL drops the entry — and the
    upstream job may well still be running. The one channel that can stop it is
    this POST, so phrasings that unambiguously address the agent, and the literal
    command, must go through with nothing on record.
    """  # noqa: DOCSTRING_CJK
    fake, _ = wired
    assert oc._shared.Modules.task_registry == {}

    _dispatch("/stop", user_text=text)
    assert [c[0] for c in fake.magic_calls] == ["/stop"], text


def test_an_unrecognized_stop_phrasing_still_needs_corroboration(wired):
    """⚠ Reverse the criterion: unclassified tiers **no longer** bypass checks, precisely the entry point for LLM misclassification.

    An earlier revision let ``tier is None`` through as an escape hatch. But
    ``stop_trigger_tier`` runs on the raw text independently of who classified
    it, so 停下来 is tiered ambiguous no matter what; None means only "no table
    knows this phrasing", which is precisely the LLM-misclassification path this
    guard exists to contain. Leaving it open left a door built specifically to
    bypass the guard.

    The escape hatch is the **addressed tier plus the literal command** — both
    still dispatch with nothing on record.
    """  # noqa: DOCSTRING_CJK
    from brain.openclaw_adapter import OpenClawAdapter

    fake, _ = wired
    assert oc._shared.Modules.task_registry == {}
    assert OpenClawAdapter.stop_trigger_tier("请把这个停一停") is None

    _dispatch("/stop", task_id="m1", user_text="请把这个停一停")
    assert fake.magic_calls == [], "词表不认识的说法，没有佐证时不该派发"

    # Corroboration allows admission.
    _register(oc._shared.Modules.task_registry, "t-live", status="running",
              ended_seconds_ago=None)
    _dispatch("/stop", task_id="m2", user_text="请把这个停一停")
    assert [c[0] for c in fake.magic_calls] == ["/stop"]

    # Unambiguous tiers and literal commands always bypass checks.
    fake.magic_calls.clear()
    oc._shared.Modules.task_registry.clear()
    for text in ("取消这个任务", "/stop"):
        fake.magic_calls.clear()
        _dispatch("/stop", task_id="m3", user_text=text)
        assert [c[0] for c in fake.magic_calls] == ["/stop"], text

def test_the_addressed_tier_is_what_carries_the_addressed_cases(wired):
    """⚠ That fallback masks deleted unambiguous tiers, so test the vocabulary directly here.

    Without this, removing _STOP_ADDRESSED entirely leaves every dispatch test
    green: those phrasings would simply fall through the same None-tier path.
    """  # noqa: DOCSTRING_CJK
    from brain.openclaw_adapter import OpenClawAdapter

    for text in ("取消这个任务", "停止搜索", "算了别查了", "取消這個搜尋"):
        assert OpenClawAdapter.stop_trigger_tier(text) == "addressed", text


@pytest.mark.parametrize(
    "reply",
    [
        "已确认配置无误。", "確認完成", "已检查是否有重复文件，没有发现。",
        "整理完成，共移动 12 个文件。", "确认执行完毕", "是否存在的检查已跑完",
    ],
)
def test_a_declarative_confirmation_is_not_a_prompt(wired, reply):
    """⚠ Markers match **substrings**, so include only terms absent from declarative sentences.

    ``确认`` / ``確認`` / ``是否`` all read naturally in a completion summary —
    "已确认配置无误", "已检查是否有重复" — so accepting them as prompt markers
    reopened the window on replies that asked nothing at all, and the next casual
    同意 went straight upstream.
    """  # noqa: DOCSTRING_CJK
    fake, _ = wired
    _register(oc._shared.Modules.task_registry, "t-done", status="completed", reply=reply)

    _dispatch("/daemon approve")
    assert fake.magic_calls == [], reply


def test_an_ambiguous_stop_is_corroborated_by_a_standing_approval_window(wired):
    """⚠ Approval prompts arrive precisely when **no tasks run**: completed tasks open windows.

    Requiring only a queued/running task made the guard return before the
    ``/stop`` block, so a 「停下来」 that *rejects* the prompt was dropped **and**
    left the window standing for a later inferred 同意 to approve the very thing
    the user just refused.
    """  # noqa: DOCSTRING_CJK
    fake, _ = wired
    registry = oc._shared.Modules.task_registry
    _register(registry, "t-done", status="completed")
    assert oc._collect_active_openclaw_task_ids() == [], "前提：没有在跑的任务"

    _dispatch("/stop", task_id="magic-stop", user_text="停下来")
    assert [c[0] for c in fake.magic_calls] == ["/stop"], "拒绝提示的 /stop 必须发出去"
    assert registry["t-done"][oc._APPROVAL_CONSUMED_KEY] is True, "而且要把窗口作废掉"

    fake.magic_calls.clear()
    _dispatch("/daemon approve", task_id="magic-approve")
    assert fake.magic_calls == [], "被拒绝过的提示不该还能授权"


def test_a_stale_completion_does_not_corroborate_an_ambiguous_stop(wired):
    """⚠ Corroboration **opens** gates and needs narrow criteria; broad invalidation filters would disable tier guards.

    Terminal entries can sit in the registry indefinitely on this path (cleanup
    only runs from the capabilities route), so a single hours-old, non-prompt
    completion would otherwise corroborate every later 停下来 forever.
    """  # noqa: DOCSTRING_CJK
    fake, _ = wired
    registry = oc._shared.Modules.task_registry

    # Too old.
    _register(registry, "t-stale", status="completed",
              ended_seconds_ago=oc.TASK_REGISTRY_CLEANUP_TTL + 60)
    _dispatch("/stop", task_id="m1", user_text="停下来")
    assert fake.magic_calls == [], "超龄的完成记录不该给模糊说法当佐证"

    # Asked no question.
    registry.clear()
    _register(registry, "t-plain", status="completed", reply="整理完成，共移动 12 个文件。")
    _dispatch("/stop", task_id="m2", user_text="停下来")
    assert fake.magic_calls == [], "没问过问题的完成记录同样不算佐证"

    # Already consumed.
    registry.clear()
    _register(registry, "t-used", status="completed")
    registry["t-used"][oc._APPROVAL_CONSUMED_KEY] = True
    _dispatch("/stop", task_id="m3", user_text="停下来")
    assert fake.magic_calls == [], "已兑现的窗口不该复活成佐证"


def test_a_running_task_under_another_character_corroborates(wired):
    """⚠ Upstream session keys recognize only sender (_build_session_key starts with del role_name).

    A job running under another character of the same sender is exactly what a
    「停下来」 can be referring to, and the `/stop` POST lands on that same shared
    upstream session anyway.
    """  # noqa: DOCSTRING_CJK
    fake, _ = wired
    _register(
        oc._shared.Modules.task_registry,
        "t-other-char",
        status="running",
        lanlan="miku",
        ended_seconds_ago=None,
    )

    _dispatch("/stop", task_id="m1", user_text="停下来")
    assert [c[0] for c in fake.magic_calls] == ["/stop"]

    # Other senders still do not count.
    fake.magic_calls.clear()
    oc._shared.Modules.task_registry.clear()
    _register(
        oc._shared.Modules.task_registry,
        "t-other-sender",
        status="running",
        sender="USER_B",
        ended_seconds_ago=None,
    )
    _dispatch("/stop", task_id="m2", user_text="停下来")
    assert fake.magic_calls == [], "别人的在跑任务不能给我的模糊说法当佐证"


def test_a_cross_character_prompt_window_corroborates_an_ambiguous_stop(wired):
    """⚠ All three corroboration paths must agree on characters, or leave a particularly dangerous gap.

    Active tasks and retirement both treat one sender's characters as a single
    upstream session (`_build_session_key` opens with ``del role_name``). Leaving
    only the prompt-window side role-scoped means a prompt raised under character
    A, rejected with 「停下来」 after switching to B, returns before the ``/stop``
    block — the stop is dropped *and* A's window survives for a later inferred
    approval.
    """  # noqa: DOCSTRING_CJK
    fake, _ = wired
    registry = oc._shared.Modules.task_registry
    _register(registry, "t-other-char", status="completed", lanlan="miku")
    assert oc._collect_active_openclaw_task_ids() == [], "前提：没有在跑的任务"

    _dispatch("/stop", task_id="m1", user_text="停下来")
    assert [c[0] for c in fake.magic_calls] == ["/stop"], "跨角色的提示也算佐证"
    assert registry["t-other-char"][oc._APPROVAL_CONSUMED_KEY] is True

    fake.magic_calls.clear()
    _dispatch("/daemon approve", task_id="m2")
    assert fake.magic_calls == [], "被拒绝过的跨角色提示不该还能授权"


def test_cross_sender_prompts_never_corroborate(wired):
    """Widen only to sender scope: others' pending prompts cannot corroborate my ambiguous input."""  # noqa: DOCSTRING_CJK
    fake, _ = wired
    _register(
        oc._shared.Modules.task_registry, "t-other-sender",
        status="completed", sender="USER_B",
    )

    _dispatch("/stop", task_id="m1", user_text="停下来")
    assert fake.magic_calls == []


def test_the_retirement_set_is_always_a_superset_of_the_gate_set(wired):
    """⚠⚠ Overall filter invariant, dimension by dimension: invalidation ⊇admission.

    Open the gate and you authorize an upstream action; retire and you only mark
    a record spent. So every dimension the gate narrows on — age, session,
    character, prompt marker — must be *wider or equal* on the retirement side.
    Anything the gate can still see but retirement cannot is a window nobody can
    ever close, which is exactly the shape of the last four defects here.

    A property check rather than a checklist: the registry below spans every
    dimension at once, so a new filter added to one side and not the other turns
    this red without anyone remembering to extend a list.
    """  # noqa: DOCSTRING_CJK
    fake, _ = wired
    registry = oc._shared.Modules.task_registry

    _register(registry, "fresh", status="completed")
    _register(registry, "stale", status="completed",
              ended_seconds_ago=TASK_REGISTRY_CLEANUP_TTL + 60)
    _register(registry, "future", status="completed", ended_seconds_ago=-30.0)
    _register(registry, "no-end", status="completed", ended_seconds_ago=None)
    _register(registry, "other-char", status="completed", lanlan="miku")
    _register(registry, "other-session", status="completed", session_id="sess-old")
    _register(registry, "no-prompt", status="completed", reply="整理完成。")
    _register(registry, "running", status="running", ended_seconds_ago=None)
    _register(registry, "failed", status="failed")
    _register(registry, "cancelled", status="cancelled")

    def _gate(**kw):
        return set(oc._iter_approval_window_tasks(
            sender_id="USER_A", exclude_task_id=None, **kw
        ))

    narrow = _gate(lanlan_name="lan")                      # approve admission.
    narrow_any_role = _gate(lanlan_name=None)              # /stop corroboration.
    wide = _gate(lanlan_name=None, age_bounded=False,
                 match_lanlan=False, require_session=False)  # Invalidate.

    assert narrow <= narrow_any_role <= wide, (
        f"作废必须 ⊇ 开闸：narrow={sorted(narrow)} "
        f"any_role={sorted(narrow_any_role)} wide={sorted(wide)}"
    )
    # Each dimension must distinguish at least one side, or the containment assertion above is vacuous.
    assert "stale" not in narrow and "stale" in wide, "判龄这一维没起作用"
    assert "other-session" not in narrow and "other-session" in wide, "session 维没起作用"
    assert "other-char" not in narrow and "other-char" in narrow_any_role, "角色维没起作用"
    assert "no-prompt" not in narrow and "no-prompt" in wide, "疑问标记维没起作用"
    assert "future" not in narrow and "future" in wide, "判龄**下界**没起作用"
    assert "no-end" not in narrow and "no-end" in wide, "缺 end_time 的 fail-closed 没起作用"
    # Noncompleted statuses are invisible on both sides.
    for task_id in ("running", "failed", "cancelled"):
        assert task_id not in wide, task_id


def test_stop_cancels_the_cross_character_task_it_used_as_corroboration(wired):
    """⚠ Admitted tasks must also cancel, or UI keeps displaying work users just stopped.

    The upstream `/stop` POST lands on the shared session and stops that job
    anyway, so leaving the other character's registry entry as ``running`` is the
    local state telling a lie.
    """  # noqa: DOCSTRING_CJK
    fake, _ = wired
    registry = oc._shared.Modules.task_registry
    _register(registry, "t-other-char", status="running", lanlan="miku",
              ended_seconds_ago=None)

    _dispatch("/stop", task_id="m1", user_text="停下来")

    assert [c[0] for c in fake.magic_calls] == ["/stop"]
    assert registry["t-other-char"]["status"] == "cancelled", (
        "拿它当佐证放行了，就必须把它掐掉"
    )


def test_another_senders_running_task_is_never_cancelled(wired):
    """Widen only to sender scope: my /stop cannot cancel others' tasks."""  # noqa: DOCSTRING_CJK
    fake, _ = wired
    registry = oc._shared.Modules.task_registry
    _register(registry, "t-mine", status="running", ended_seconds_ago=None)
    _register(registry, "t-theirs", status="running", sender="USER_B",
              ended_seconds_ago=None)

    _dispatch("/stop", task_id="m1", user_text="停下来")

    assert registry["t-mine"]["status"] == "cancelled"
    assert registry["t-theirs"]["status"] == "running", "跨 sender 不该被掐"


@pytest.mark.parametrize("reply", ["整理了「要不要删除备份」这个讨论。", "记录了要不要保留的争论"])
def test_an_embedded_interrogative_phrase_is_not_a_prompt(wired, reply):
    """⚠ 要不要 also occurs declaratively; substring markers would wrongly open approval windows too."""  # noqa: DOCSTRING_CJK
    fake, _ = wired
    _register(oc._shared.Modules.task_registry, "t-done", status="completed", reply=reply)

    _dispatch("/daemon approve")
    assert fake.magic_calls == [], reply
