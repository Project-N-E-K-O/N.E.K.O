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
    }
    # ⚠️ 连 queued / running 也带上 end_time，明知生产里它们不会有。
    # 目的是**隔离状态判据**：不带的话，把 running 塞进窗口集合的变异会被
    # 「判不了龄 → fail-closed」那条挡掉，测试照样绿——为错误的理由而绿，
    # 状态过滤根本没被验到（变异验证抓出来的）。想测判龄有专门的用例，
    # 走 ended_seconds_ago=None。
    if ended_seconds_ago is not None:
        info["end_time"] = _iso(ended_seconds_ago)
    registry[task_id] = info


def test_approve_is_dropped_with_no_task_on_record(wired):
    fake, emitted = wired

    _dispatch("/daemon approve")

    assert fake.magic_calls == [], "没有任何任务记录时不该把批准发给上游"
    # 静默：不 emit task_result，所以前端不会念出「收到许可！Neko 这就放手去干喵！」
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
    """⚠️ 「写进 registry 的状态」必须从**写入点**推导，不是从源码里出现过的字面量。

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
    ``_has_recent_openclaw_task`` — NOT by the registry cleanup, which the
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
    # ⚠️ 必须注册在 default_sender_id 名下。proactive 轮会把 nk_sender_id 强制成
    # default（见 _resolve_openclaw_sender_id 上方的说明），登记在别的 sender 名下
    # 时闸会先被 sender 过滤挡掉——那样这条测试就永远绿，验不到 proactive 这道判据。
    _register(
        oc._shared.Modules.task_registry,
        "t-done",
        status="completed",
        sender=fake.default_sender_id,
    )

    _dispatch("/daemon approve", proactive=True, sender=fake.default_sender_id)

    assert fake.magic_calls == [], "主动搭话轮没有用户，绝不能批准"
    assert emitted == []

    # ⚠️ 显式敲字面 magic word **也**不行。「proactive 一律不放行」和「显式命令一律
    # 豁免闸」是两条相邻分支，谁在前面决定了这一格的行为，而这个顺序此前没有任何测试
    # 钉住：把豁免提到 proactive 之前，整个 gate 文件照样全绿。主动搭话轮里根本没有
    # 用户输入，"显式"这个概念不成立——猫娘自己那句台词不该因为长得像命令就被豁免。
    _dispatch(
        "/daemon approve",
        proactive=True,
        sender=fake.default_sender_id,
        user_text="/daemon approve",
    )
    assert fake.magic_calls == [], "proactive 轮里显式豁免不得越过 proactive 阻断"

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


def test_one_completion_authorizes_only_one_inferred_approval(wired):
    """⚠️ 一次审批提示只授权一次推断批准。

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

    # 显式敲字面 magic word 仍然豁免闸，不受兑现影响
    _dispatch("/daemon approve", user_text="/daemon approve")
    assert [c[0] for c in fake.magic_calls] == ["/daemon approve"]


def test_a_failed_dispatch_does_not_consume_the_window(wired):
    """派单失败时不兑现——那次批准没送出去，用户重说一遍应该还能用。"""  # noqa: DOCSTRING_CJK
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
    assert len(fake.magic_calls) == 2, "失败的那次不该把窗口兑现掉"


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
    """⚠️ 上界之外还要下界。

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

    for typed in ("/daemon approve", "daemon approve", "/approve", "approve"):
        fake.magic_calls.clear()
        _dispatch("/daemon approve", user_text=typed)
        assert [c[0] for c in fake.magic_calls] == ["/daemon approve"], typed
    assert emitted


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
    """  # noqa: DOCSTRING_CJK
    fake, emitted = wired

    _dispatch(command)

    assert [call[0] for call in fake.magic_calls] == [command]
    assert emitted, f"{command} 应该照常产生 task_result"


def test_stop_retires_a_standing_approval_window(wired):
    """⚠️ 掐任务掐不掉已经问出口的那句审批提示。

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

    _dispatch("/stop", task_id="magic-stop")
    assert [c[0] for c in fake.magic_calls] == ["/stop"]

    fake.magic_calls.clear()
    _dispatch("/daemon approve", task_id="magic-approve")
    assert fake.magic_calls == [], "/stop 之后那条 completed 不该再授权"


def test_stop_retires_the_window_with_nothing_left_to_cancel(wired):
    """⚠️ 兑现不能挂在「掐到了东西」上。

    The reported sequence has *nothing* queued or running — the task completed
    and is waiting on the prompt — so ``cancelled_task_ids`` comes back empty.
    Gating retirement on that list reproduces the bug exactly.
    """  # noqa: DOCSTRING_CJK
    fake, _ = wired
    _register(oc._shared.Modules.task_registry, "t-done", status="completed")

    _dispatch("/stop", task_id="magic-stop")
    assert fake.stop_calls == [], "前提：这一轮没有在跑的任务可掐"
    assert oc._shared.Modules.task_registry["t-done"][oc._APPROVAL_CONSUMED_KEY] is True

    fake.magic_calls.clear()
    _dispatch("/daemon approve", task_id="magic-approve")
    assert fake.magic_calls == [], "没掐到东西也要把窗口作废"


def test_stop_retires_the_window_even_when_the_upstream_call_fails(wired):
    """本地取消不回滚，「用户说了停」也跟上游那趟调用的成败无关。"""  # noqa: DOCSTRING_CJK
    fake, _ = wired
    _register(oc._shared.Modules.task_registry, "t-done", status="completed")

    async def _fail(command, *, sender_id=None, role_name=None):
        fake.magic_calls.append((command, sender_id, role_name))
        return {"success": False, "error": "boom", "command": command}

    fake.run_magic_command = _fail
    _dispatch("/stop", task_id="magic-stop")

    fake.run_magic_command = _FakeOpenClaw.run_magic_command.__get__(fake)
    fake.magic_calls.clear()
    _dispatch("/daemon approve", task_id="magic-approve")
    assert fake.magic_calls == [], "上游 /stop 失败也不该让旧窗口活下来"


def test_stop_only_retires_windows_it_owns(wired):
    """⚠️ 一个用户的「停下来」不该作废**另一个用户**的待批准提示。

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
    """作废是 fail-closed 收窄，逃生口还在：直接敲字面命令一律豁免闸。"""  # noqa: DOCSTRING_CJK
    fake, _ = wired
    _register(oc._shared.Modules.task_registry, "t-done", status="completed")

    _dispatch("/stop", task_id="magic-stop")
    fake.magic_calls.clear()

    _dispatch("/daemon approve", task_id="magic-approve", user_text="/daemon approve")
    assert [c[0] for c in fake.magic_calls] == ["/daemon approve"]


@pytest.mark.parametrize("command", ["/new", "/clear", "/daemon approve"])
def test_only_stop_retires_windows(wired, command):
    """⚠️ 作废只挂在 /stop 上。

    ``/daemon approve`` consumes exactly one entry through its own success path;
    widening retirement to every command would make an unrelated ``/clear`` eat
    a prompt the user never answered.
    """  # noqa: DOCSTRING_CJK
    fake, _ = wired
    registry = oc._shared.Modules.task_registry
    _register(registry, "t-a", status="completed", ended_seconds_ago=1.0)
    _register(registry, "t-b", status="completed", ended_seconds_ago=2.0)

    _dispatch(command, task_id="magic-1")

    consumed = [t for t in ("t-a", "t-b") if registry[t].get(oc._APPROVAL_CONSUMED_KEY)]
    expected = 1 if command == "/daemon approve" else 0
    assert len(consumed) == expected


def test_stop_retires_every_standing_window_not_just_the_first(wired):
    """⚠️ 复数是 finder 从「返回单个 id」改成「返回列表」的**全部**收益。

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

    _dispatch("/stop", task_id="magic-stop")

    assert registry["t-a"].get(oc._APPROVAL_CONSUMED_KEY) is True
    assert registry["t-b"].get(oc._APPROVAL_CONSUMED_KEY) is True

    fake.magic_calls.clear()
    _dispatch("/daemon approve", task_id="magic-approve")
    assert fake.magic_calls == [], "第二条窗口也必须被作废"


def test_a_proactive_stop_never_retires_the_users_window(wired):
    """⚠️ 不能替用户批准，就同样不能替用户撤销授权。

    A proactive turn has no user — task_executor feeds the character's own line
    back into the classifier, and both turns resolve to the same sender. Letting
    her 「停下来」 retire the window silently eats the human's next 同意: the gate
    returns without emitting anything, so nothing is spoken back either.
    """  # noqa: DOCSTRING_CJK
    fake, emitted = wired
    # ⚠️ 两侧都用 default_sender_id，这才是生产形状：main_logic 从不往 analyze
    # messages 上挂 sender_id，所以 _resolve_openclaw_sender_id 返回 ""，用户轮和
    # 主动轮**落在同一个 sender 桶**。用夹具默认的 USER_A 会让主动轮的 sender
    # (DEFAULT_SENDER) 跟窗口对不上，于是作废与否都不影响断言——测试为错误的理由而绿。
    home = fake.default_sender_id
    _register(oc._shared.Modules.task_registry, "t-done", status="completed", sender=home)

    _dispatch("/stop", task_id="magic-stop", sender=home, proactive=True)
    assert [c[0] for c in fake.magic_calls] == ["/stop"], "主动轮的 /stop 本身照常派发"
    assert oc._APPROVAL_CONSUMED_KEY not in oc._shared.Modules.task_registry["t-done"]

    fake.magic_calls.clear()
    emitted.clear()
    _dispatch("/daemon approve", task_id="magic-approve", sender=home)
    assert [c[0] for c in fake.magic_calls] == ["/daemon approve"]


def test_stop_retires_a_window_whose_end_time_is_in_the_future(wired):
    """⚠️ 作废是一次性的写，开闸却是每次重算的谓词。

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

    _dispatch("/stop", task_id="magic-stop")

    for task_id in ("t-future", "t-stale", "t-noend"):
        assert registry[task_id].get(oc._APPROVAL_CONSUMED_KEY) is True, task_id


def test_stop_retires_windows_of_the_senders_other_characters(wired):
    """⚠️ 上游会话键忽略角色，所以 /stop 的影响半径也忽略角色。

    ``_build_session_key`` opens with ``del role_name``: every character of one
    sender shares a single upstream session. A stop issued under character B
    cancels the very action character A's prompt was about, so leaving A's
    window standing lets a later 同意 put it straight back.
    """  # noqa: DOCSTRING_CJK
    fake, _ = wired
    registry = oc._shared.Modules.task_registry
    _register(registry, "t-other-char", status="completed", lanlan="miku")
    _register(registry, "t-other-sender", status="completed", sender="USER_B")

    _dispatch("/stop", task_id="magic-stop")

    assert registry["t-other-char"].get(oc._APPROVAL_CONSUMED_KEY) is True
    assert registry["t-other-sender"].get(oc._APPROVAL_CONSUMED_KEY) is None, (
        "跨 sender 是真的不相干，不能一起作废"
    )


def test_stop_retires_the_window_even_when_the_upstream_call_raises(wired):
    """⚠️ 上游最常见的失败是**抛异常**（连接重置 / 超时），不是返回 success=False。

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
    _dispatch("/stop", task_id="magic-stop")
    assert oc._shared.Modules.task_registry["t-done"][oc._APPROVAL_CONSUMED_KEY] is True

    fake.run_magic_command = _FakeOpenClaw.run_magic_command.__get__(fake)
    fake.magic_calls.clear()
    _dispatch("/daemon approve", task_id="magic-approve")
    assert fake.magic_calls == [], "上游抛异常也不该让旧窗口活下来"
