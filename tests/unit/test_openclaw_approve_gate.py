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
        # ⚠️ 默认带一句**问句**回复。窗口现在要求那条 reply 真的问过问题——只判「有任务
        # 跑完过」的话，一次「整理完成，共移动 12 个文件」也会给随后随口的「同意」开闸。
        # 想测「回复不是问句」走 reply=... 显式传。
        "result": {"reply": reply},
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


def test_a_failed_dispatch_still_consumes_the_window(wired):
    """⚠️ ``success=False`` 不等于「那次批准没送出去」。

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

    # 逃生口：显式敲字面命令一律豁免闸
    _dispatch("/daemon approve", user_text="/daemon approve")
    assert len(fake.magic_calls) == 2


def test_an_inferred_approval_consumes_every_indistinguishable_window(wired):
    """⚠️ `/daemon approve` 不带 task id，分不出是哪条 completed 带出了那句提示。

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
    """兑现排在 await 之前，上游抛异常不能把它跳过。"""  # noqa: DOCSTRING_CJK
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

    for typed in ("/daemon approve", "/approve"):
        fake.magic_calls.clear()
        _dispatch("/daemon approve", user_text=typed)
        assert [c[0] for c in fake.magic_calls] == ["/daemon approve"], typed
    assert emitted

    # ⚠️ 不带斜杠的裸词**不算**亲手打的命令，所以拿不到这条豁免——否则一句英文闲聊
    # 里的 "approve" 就能绕开整道审批闸。
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


def test_an_explicit_approval_still_retires_the_window(wired):
    """⚠️ 显式命令豁免的是**准入判定**，不是兑现。

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
    """兑现归兑现，准入豁免不能跟着一起没了：registry 空着也必须照发。"""  # noqa: DOCSTRING_CJK
    fake, _ = wired
    assert oc._shared.Modules.task_registry == {}

    _dispatch("/daemon approve", task_id="magic-1", user_text="/daemon approve")
    assert [c[0] for c in fake.magic_calls] == ["/daemon approve"]


def test_an_unknown_current_session_closes_the_gate(wired):
    """⚠️ 问不出当前会话时开闸侧必须 fail-closed。

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
    """作废侧相反：问不出会话就把这个 sender 名下的窗口全兑现掉，宁可多不可漏。"""  # noqa: DOCSTRING_CJK
    fake, _ = wired
    registry = oc._shared.Modules.task_registry
    _register(registry, "t-done", status="completed")

    def _boom(*, role_name, sender_id):
        raise RuntimeError("session cache unreadable")

    fake.peek_persistent_session_id = _boom
    _dispatch("/stop", task_id="magic-stop")
    assert registry["t-done"][oc._APPROVAL_CONSUMED_KEY] is True


def test_stop_retires_before_the_cancel_helper_can_raise(wired, monkeypatch):
    """⚠️ 作废排在取消 helper 之前，那个 helper 抛出不能把作废跳过。

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

    # ⚠️ 用 monkeypatch，别直接赋值再 del：`_task_tracker` 是模块级单例，手工 del 会把
    # 上面 fixture 用 monkeypatch 装上去的桩一起抹掉，之后所有用例共用一个被弄坏的
    # tracker——这个仓库的 pytest 有后台线程/单例泄漏污染后续用例的前科。
    monkeypatch.setattr(oc._task_tracker, "record_completed", _boom)
    with pytest.raises(RuntimeError):
        _dispatch("/stop", task_id="magic-stop")

    assert registry["t-done"][oc._APPROVAL_CONSUMED_KEY] is True, (
        "取消 helper 抛出之前，窗口就该已经作废掉了"
    )


def test_a_completion_that_asked_nothing_does_not_open_the_gate(wired):
    """⚠️ 窗口判的应该是「问过问题」，不是「跑完过任务」。

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

    # 逃生口：显式敲字面命令仍然豁免
    _dispatch("/daemon approve", user_text="/daemon approve")
    assert [c[0] for c in fake.magic_calls] == ["/daemon approve"]


@pytest.mark.parametrize(
    "reply",
    ["要删掉吗？", "Proceed?", "需要我删掉嗎", "要不要我继续", "删掉这 3 个?"],
)
def test_replies_that_can_carry_a_prompt_open_the_gate(wired, reply):
    """带明确疑问标记的回复才算提示。判据只认标记，不枚举「提示长什么样」。"""  # noqa: DOCSTRING_CJK
    fake, _ = wired
    _register(oc._shared.Modules.task_registry, "t-done", status="completed", reply=reply)

    _dispatch("/daemon approve")
    assert [c[0] for c in fake.magic_calls] == ["/daemon approve"], reply


def test_stop_needs_a_running_task_when_the_phrasing_is_ambiguous(wired):
    """⚠️ 「停下来」在角色扮演里可能是对猫娘本人说的，不是要掐后台任务。

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
    """⚠️ 逃生阀不能焊死。

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


def test_an_unclassified_stop_phrasing_falls_through_to_dispatch(wired):
    """⚠️ 分不出档的说法走**兜底放行**，这是独立于两档词表的一条判据。

    ``stop`` used to sit in the addressed-tier parametrization, where it was
    green for the wrong reason: it is not a literal command (no slash) and it is
    in neither tier table, so it rides the ``tier is None`` fallback. Deleting
    the entire addressed tier would have left that case passing.

    The fallback itself is deliberate — the LLM leg can produce ``/stop`` from
    phrasings no table knows, and welding those shut would close the escape
    hatch that exists precisely because the registry lies when it matters most.
    """  # noqa: DOCSTRING_CJK
    from brain.openclaw_adapter import OpenClawAdapter

    fake, _ = wired
    assert oc._shared.Modules.task_registry == {}

    # 前提：既不是字面命令，也不在任何一档词表里
    assert OpenClawAdapter.parse_typed_magic_command("stop") is None
    assert OpenClawAdapter.stop_trigger_tier("stop") is None
    assert OpenClawAdapter.stop_trigger_tier("请把这个停一停") is None

    for text in ("stop", "请把这个停一停"):
        fake.magic_calls.clear()
        _dispatch("/stop", user_text=text)
        assert [c[0] for c in fake.magic_calls] == ["/stop"], text


def test_the_addressed_tier_is_what_carries_the_addressed_cases(wired):
    """⚠️ 上面那条兜底会掩盖「明确档被删掉」——所以这里直接打词表。

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
    """⚠️ 标记是**子串**匹配，所以只能收在陈述句里不会出现的词。

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
    """⚠️ 审批提示出口时**恰恰没有在跑的任务**——窗口是 completed 开的。

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
    """⚠️ 佐证是**开闸**决策，得用窄判据——用作废那套宽过滤会把分档守卫废掉。

    Terminal entries can sit in the registry indefinitely on this path (cleanup
    only runs from the capabilities route), so a single hours-old, non-prompt
    completion would otherwise corroborate every later 停下来 forever.
    """  # noqa: DOCSTRING_CJK
    fake, _ = wired
    registry = oc._shared.Modules.task_registry

    # 超龄
    _register(registry, "t-stale", status="completed",
              ended_seconds_ago=oc.TASK_REGISTRY_CLEANUP_TTL + 60)
    _dispatch("/stop", task_id="m1", user_text="停下来")
    assert fake.magic_calls == [], "超龄的完成记录不该给模糊说法当佐证"

    # 没问过问题
    registry.clear()
    _register(registry, "t-plain", status="completed", reply="整理完成，共移动 12 个文件。")
    _dispatch("/stop", task_id="m2", user_text="停下来")
    assert fake.magic_calls == [], "没问过问题的完成记录同样不算佐证"

    # 已经兑现过
    registry.clear()
    _register(registry, "t-used", status="completed")
    registry["t-used"][oc._APPROVAL_CONSUMED_KEY] = True
    _dispatch("/stop", task_id="m3", user_text="停下来")
    assert fake.magic_calls == [], "已兑现的窗口不该复活成佐证"


def test_a_running_task_under_another_character_corroborates(wired):
    """⚠️ 上游会话键只认 sender（`_build_session_key` 第一行 `del role_name`）。

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

    # 但跨 sender 仍然不算
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
    """⚠️ 佐证的三条路径必须在「角色」这一维上一致，否则留下的口子最难看。

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
    """放宽只到 sender 一层：别人的待批准提示不能给我的模糊说法当佐证。"""  # noqa: DOCSTRING_CJK
    fake, _ = wired
    _register(
        oc._shared.Modules.task_registry, "t-other-sender",
        status="completed", sender="USER_B",
    )

    _dispatch("/stop", task_id="m1", user_text="停下来")
    assert fake.magic_calls == []


def test_the_retirement_set_is_always_a_superset_of_the_gate_set(wired):
    """⚠️⚠️ 这是这套过滤器的**总不变量**：作废 ⊇ 开闸，逐维成立。

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

    narrow = _gate(lanlan_name="lan")                      # approve 准入
    narrow_any_role = _gate(lanlan_name=None)              # /stop 佐证
    wide = _gate(lanlan_name=None, age_bounded=False,
                 match_lanlan=False, require_session=False)  # 作废

    assert narrow <= narrow_any_role <= wide, (
        f"作废必须 ⊇ 开闸：narrow={sorted(narrow)} "
        f"any_role={sorted(narrow_any_role)} wide={sorted(wide)}"
    )
    # 每一维都得真的被某一侧区分开，否则上面的包含关系是空转
    assert "stale" not in narrow and "stale" in wide, "判龄这一维没起作用"
    assert "other-session" not in narrow and "other-session" in wide, "session 维没起作用"
    assert "other-char" not in narrow and "other-char" in narrow_any_role, "角色维没起作用"
    assert "no-prompt" not in narrow and "no-prompt" in wide, "疑问标记维没起作用"
    # 非 completed 状态两侧都看不见
    for task_id in ("running", "failed", "cancelled"):
        assert task_id not in wide, task_id
