# Copyright 2025-2026 Project N.E.K.O. Team
# SPDX-License-Identifier: Apache-2.0
"""Exercise the registered host callbacks with disposable sessions and a real ledger."""

import asyncio
import json

import pytest
import pytest_asyncio

from nekoclaw.host import call_audit
from nekoclaw.host.permissions import DENIED_BY_SETTINGS
from nekoclaw.host.task_agents import _Session
from nekoclaw import TASK_CONTROL_TOOLS
from tests.unit.nekoclaw.host_fakes import fake_model, make_host


@pytest_asyncio.fixture
async def env(tmp_path):
    host = make_host(tmp_path, task_agent_model=fake_model)
    try:
        places = []
        for title in ("sender", "recipient"):
            c, _ = await host.ledger.create(
                lanlan_name="Lan", title=title, dispatch_text=title, source="dispatch",
                status="working" if title == "sender" else "queued",
            )
            places.append(await host.ledger.give_session(c.id, kind="project", name=title))
        yield host, *places
    finally:
        await host.shutdown()


async def trace(host, commission):
    return await host.ledger.run(lambda db: call_audit.read_trace(db, commission.id, now=0))


async def test_host_actions_record_actual_destinations_and_no_content(env):
    host, (source, sender), (_, recipient) = env
    callbacks = host.task_agents._callbacks()
    secret = "test message body that must never enter host_calls"
    inputs = [
        ("session_list", {"query": secret}, "list_sessions", sender.id),
        ("session_read", {"session": recipient.id, "what": "results"}, "read_session", recipient.id),
        ("session_send", {"to": recipient.id, "message": secret}, "message_session", recipient.id),
        ("wake_schedule", {"delay_seconds": 1500, "note": secret}, "schedule_wake", sender.id),
        ("note_lesson", {"category": "feedback", "content": secret}, "note_lesson", sender.id),
    ]
    for callback, args, _, _ in inputs:
        await callbacks.call(sender.id, callback, args)
    with pytest.raises(RuntimeError):
        # A session with no work underway cannot fork a reply.
        await callbacks.call(sender.id, "session_ask", {"session": recipient.id, "question": secret})
    await host.task_agents._space_brief(source)
    rows = (await trace(host, source))[1:]
    expected = [(tool, target, "completed") for _, _, tool, target in inputs]
    expected.extend([("ask_session", recipient.id, "failed"), ("space_brief", sender.id, "completed")])
    assert [(row["target"], row["scope_ref"], row["status"]) for row in rows] == expected
    assert all(row["parent_call_id"] == source.id and row["grant_id"] for row in rows)
    assert not any(host.permissions.grant_active(row["grant_id"]) for row in rows)
    assert secret not in json.dumps(rows)


async def test_ask_session_keeps_the_run_that_scheduled_its_handoff(env, monkeypatch):
    host, (source, sender), (_, recipient) = env
    manager = host.task_agents
    spawn = manager._spawn
    admitted = []

    async def ask(sid, payload, action):
        admitted.append(sid)
        return {}

    def switch_runs_before_starting(work):
        host.permissions.end_run(source.id)
        host.permissions.current_run(source.id)
        return spawn(work)

    monkeypatch.setattr(manager, "_cb_session_ask", ask)
    monkeypatch.setattr(manager, "_spawn", switch_runs_before_starting)
    with pytest.raises(RuntimeError, match="originating run has ended"):
        await manager._callbacks().call(sender.id, "session_ask", {"session": recipient.id, "question": "status?"})
    assert admitted == []
    assert (await trace(host, source))[-1]["status"] == "denied"


async def test_deny_reads_but_do_not_ask_for_an_intrinsic_session_action(env):
    host, (source, sender), (_, recipient) = env
    callbacks = host.task_agents._callbacks()
    asked = []

    async def refuse(*args):
        asked.append(args)
        return "deny"

    host.permissions.set_asker(refuse)
    await host.permission_store.set_mode("other", "always_ask")
    await host.permission_store.set_mode("outside_read", "always_ask")
    await callbacks.call(sender.id, "session_read", {"session": recipient.id})
    await host.permission_store.set_mode("outside_read", "deny")
    for action in ("session_read", "session_ask"):
        with pytest.raises(RuntimeError, match=DENIED_BY_SETTINGS):
            await callbacks.call(sender.id, action, {"session": recipient.id})
    assert [row["status"] for row in (await trace(host, source))[1:]] == ["completed", "denied", "denied"]
    assert not asked


async def test_no_release_to_reused_session_after_audit_write(env, monkeypatch):
    host, (source, sender), (_, recipient) = env
    finish = call_audit.finish_call

    async def close_then_end(record, status):
        await finish(record, status)
        host.permissions.end_run(source.id)

    monkeypatch.setattr(call_audit, "finish_call", close_then_end)
    with pytest.raises(RuntimeError, match="could not be delivered"):
        await host.task_agents._callbacks().call(sender.id, "session_read", {"session": recipient.id})
    last = (await trace(host, source))[-1]
    assert (last["status"], last["reason"]) == ("denied", "delivery_revoked")


@pytest.mark.parametrize("fails_at", ["reservation", "permission", "capacity"])
async def test_unavailable_answer_admission_preserves_ordinary_text(env, monkeypatch, fails_at):
    from nekoclaw.host.task_agents import _Unsaid

    host, (source, sender), _ = env
    session = _Session(source.id, session_id=sender.id)
    parts = ["other work", _Unsaid("answer fixture", source.id)]

    async def unavailable(*args, **kwargs):
        raise OSError("fixture")

    if fails_at == "capacity":
        monkeypatch.setattr(call_audit, "BUDGET_BYTES", 0)
    elif fails_at == "permission":
        monkeypatch.setattr(host.permissions, "check", unavailable)
    else:
        monkeypatch.setattr(call_audit, "start_call", unavailable)
    async with host.task_agents._answer_delivery(session, parts) as render:
        assert render(parts) == "other work\n\nthe session answer could not be authorized for delivery"
    if fails_at == "permission":
        row = (await trace(host, source))[-1]
        assert (row["status"], row["reason"]) == ("failed", "delivery_unavailable")


async def test_the_runtime_receiving_an_answer_starts_its_own_calls(env):
    from nekoclaw.host.task_agents import _Unsaid

    host, (source, sender), _ = env
    session = _Session(source.id, session_id=sender.id)
    parts = [_Unsaid("answer", source.id)]
    async with call_audit.call(host.ledger, source.id, "session_action", "producer") as producer:
        async with host.task_agents._answer_delivery(session, parts) as render:
            assert render(parts) == "answer"
            async with call_audit.call(host.ledger, source.id, "session_action", "next_tool"):
                pass
        async with call_audit.call(host.ledger, source.id, "session_action", "producer_continues"):
            pass
    rows = {r["target"]: r for r in await trace(host, source)}
    assert rows["receive_answer"]["parent_call_id"] == rows["next_tool"]["parent_call_id"] == source.id
    assert rows["producer_continues"]["parent_call_id"] == producer.call_id
    assert rows["receive_answer"]["status"] == "running"
    await host.task_agents._on_taken(session, {"inputs": 1})
    await asyncio.gather(*host.task_agents._control_settlements)
    assert next(r for r in await trace(host, source) if r["target"] == "receive_answer")["status"] == "completed"


@pytest.mark.parametrize("change", ["revoke", "different_recipient"])
async def test_answer_rechecks_both_the_recipient_and_its_live_loan(env, change):
    from nekoclaw.host.task_agents import _Unsaid

    host, (source, sender), (peer, _) = env
    session = _Session(source.id, session_id=sender.id)
    parts = ["plain background", _Unsaid("private answer", peer.id if change == "different_recipient" else source.id)]
    async with host.task_agents._answer_delivery(session, parts) as render:
        if change == "revoke":
            await host.permission_store.set_mode("outside_read", "deny")
        assert render(parts) == "plain background\n\n" + DENIED_BY_SETTINGS
    last = (await trace(host, source))[-1]
    assert (last["target"], last["scope_ref"], last["status"]) == ("receive_answer", sender.id, "denied")
    assert last["grant_id"] and "private answer" not in json.dumps(last)


async def test_native_controls_reserve_and_settle_only_their_own_call(env, monkeypatch):
    host, (source, sender), _ = env
    manager = host.task_agents
    manager._sessions[source.id] = session = _Session(source.id, session_id=sender.id)
    manager._held_by[sender.id] = source.id
    callbacks = manager._callbacks()
    try:
        for name in sorted(TASK_CONTROL_TOOLS):
            assert await callbacks.call(sender.id, "task_control_begin", {"tool": name, "call_id": name}) == {"allow": True}
        assert not (await callbacks.call(sender.id, "task_control_begin", {"tool": "other", "call_id": "x"}))["allow"]
        for name in ("get_goal", "todo_write"):
            await manager._handle({
                "type": "tool_end", "session_id": sender.id, "tool_call_id": name,
                "tool_name": name, "is_error": name == "todo_write", "text": "private local state",
            })
        manager._run_over(source.id)
        await asyncio.gather(*manager._control_settlements)
        rows = (await trace(host, source))[1:]
        assert {r["target"]: r["status"] for r in rows} == {
            "get_goal": "completed", "todo_write": "failed", "create_goal": "cancelled", "update_goal": "cancelled",
        }
        assert all(r["grant_id"] for r in rows)
        assert "private local state" not in json.dumps(rows)
        assert not (await callbacks.call(sender.id, "task_control_begin", {
            "tool": "create_goal", "call_id": "peer", "refused": True,
        }))["allow"]
        assert (await trace(host, source))[-1]["status"] == "denied"
        monkeypatch.setattr(call_audit, "BUDGET_BYTES", call_audit.RECORD_BYTES)
        assert not (await callbacks.call(sender.id, "task_control_begin", {"tool": "todo_write", "call_id": "new"}))["allow"]
        with pytest.raises(call_audit.AuditFull):
            await callbacks.call(sender.id, "wake_schedule", {"note": "never queued", "delay_seconds": 2000})
        assert not await host.ledger.wakes_pending(session_id=sender.id)
        assert not session.control_calls
    finally:
        manager._sessions.pop(source.id)
        manager._held_by.pop(sender.id)


async def test_failed_send_restores_the_idle_run_before_writing_its_receipt(env, monkeypatch):
    from types import SimpleNamespace
    from nekoclaw.host.task_agents import _Unsaid

    host, (source, sender), _ = env
    manager = host.task_agents
    session = manager._sessions[source.id] = _Session(source.id, session_id=sender.id, run_ended=True)
    finish = call_audit.finish_call
    observed = []

    async def refused(*args, **kwargs):
        raise OSError("refused")

    async def observe(record, status):
        if status == "failed":
            observed.append(session.run_ended)
        await finish(record, status)

    monkeypatch.setattr(call_audit, "finish_call", observe)
    with pytest.raises(OSError, match="refused"):
        await manager._send_text(SimpleNamespace(steer=refused), session, "steer", _Unsaid("answer", source.id))
    assert observed == [True]


async def test_a_blocked_refusal_write_does_not_hold_the_runtime_event_pump(env, monkeypatch):
    from nekoclaw.host.task_agents import _Unsaid

    host, (source, sender), _ = env
    manager = host.task_agents
    session = manager._sessions[source.id] = _Session(source.id, session_id=sender.id)
    manager._held_by[sender.id] = source.id
    parts = [_Unsaid("answer", source.id)]
    async with manager._answer_delivery(session, parts) as render:
        assert render(parts) == "answer"
    session.sent = 1
    session.numbered[1] = 1
    writing, release = asyncio.Event(), asyncio.Event()
    finish = call_audit.finish_call

    async def blocked(record, status):
        writing.set()
        await release.wait()
        await finish(record, status)

    monkeypatch.setattr(call_audit, "finish_call", blocked)
    event = {"type": "error", "session_id": sender.id, "command": "steer", "serial": 1}
    handling = asyncio.create_task(manager._handle(event))
    try:
        await asyncio.wait_for(writing.wait(), timeout=5)
        done, _ = await asyncio.wait([handling], timeout=0.2)
        assert done
    finally:
        release.set()
        await handling
        await asyncio.gather(*manager._control_settlements)
    assert (await trace(host, source))[-1]["status"] == "failed"


async def test_shutdown_attempts_each_unused_copy_after_a_cleanup_failure(env, monkeypatch):
    host, (_, sender), (_, recipient) = env
    manager = host.task_agents
    manager._unused_ask_copies = {s.id: s for s in (sender, recipient)}
    attempted = []

    async def discard(copy):
        attempted.append(copy.id)
        if copy.id == sender.id:
            raise OSError("fixture write failure")

    monkeypatch.setattr(manager, "_discard_copy", discard)
    await manager.shutdown()
    assert attempted == [sender.id, recipient.id]


async def test_session_read_loan_survives_prompt_mode_changes_and_never_revives_after_deny(env):
    from nekoclaw.host.permissions import GrantScope, SessionActionScope

    host, (source, _), _ = env
    await host.permission_store.set_mode("outside_read", "allow")
    peer = SessionActionScope("host:session", "read_session", source.id)
    ordinary = GrantScope("test", "file")
    async def borrow(scope):
        decision = await host.permissions.check(source.id, "outside_read", scope=scope, hold_once=True)
        assert decision.allow and decision.grant_id
        return decision.grant_id

    session_loan, ordinary_loan = await borrow(peer), await borrow(ordinary)
    for mode in ("first_ask", "always_ask"):
        await host.permission_store.set_mode("outside_read", mode)
        assert host.permissions.grant_active(session_loan)
        assert not host.permissions.grant_active(ordinary_loan)
    await host.permission_store.set_mode("outside_read", "deny")
    assert not host.permissions.grant_active(session_loan)
    await host.permission_store.set_mode("outside_read", "allow")
    assert not host.permissions.grant_active(session_loan)
    assert host.permissions.grant_active(await borrow(peer))


async def test_unknown_answer_authority_is_not_reported_as_a_user_setting(env, monkeypatch):
    from nekoclaw.host.permissions import DENIED_NO_ANSWER, PermissionDecision
    from nekoclaw.host.task_agents import _Unsaid

    host, (source, sender), _ = env
    session = _Session(source.id, session_id=sender.id)
    parts = [_Unsaid("private", source.id)]

    async def unknown(*args, **kwargs):
        return PermissionDecision(False, DENIED_NO_ANSWER)

    monkeypatch.setattr(host.permissions, "check", unknown)
    async with host.task_agents._answer_delivery(session, parts) as render:
        assert render(parts) == "the session answer could not be authorized for delivery"
    assert (await trace(host, source))[-1]["status"] == "denied"


async def test_shutdown_drains_control_results_before_cancelling_unfinished_calls(env):
    host, (source, sender), _ = env
    manager = host.task_agents
    manager._sessions[source.id] = _Session(source.id, session_id=sender.id)
    manager._held_by[sender.id] = source.id
    for tool in ("todo_write", "get_goal", "update_goal"):
        assert (await manager._callbacks().call(sender.id, "task_control_begin", {"tool": tool, "call_id": tool}))["allow"]
    for tool, failed in (("todo_write", False), ("get_goal", True)):
        manager._events.put_nowait({"type": "tool_end", "session_id": sender.id, "tool_call_id": tool, "is_error": failed})
    manager._owe_what_is_left()
    manager._discard(source.id)
    await asyncio.gather(*manager._control_settlements)
    assert {r["target"]: r["status"] for r in (await trace(host, source))[1:]} == {
        "todo_write": "completed", "get_goal": "failed", "update_goal": "cancelled",
    }


async def test_shutdown_waits_for_outstanding_control_cancellation(env, monkeypatch):
    host, (source, sender), _ = env
    manager = host.task_agents
    manager._sessions[source.id] = _Session(source.id, session_id=sender.id)
    manager._held_by[sender.id] = source.id
    assert await manager._callbacks().call(sender.id, "task_control_begin", {
        "tool": "todo_write", "call_id": "interrupted",
    }) == {"allow": True}

    writing, release = asyncio.Event(), asyncio.Event()
    finish = call_audit.finish_call

    async def blocked_write(record, status):
        writing.set()
        await release.wait()
        await finish(record, status)

    monkeypatch.setattr(call_audit, "finish_call", blocked_write)
    closing = asyncio.create_task(manager.shutdown())
    try:
        await asyncio.wait_for(writing.wait(), timeout=5)
        done, _ = await asyncio.wait([closing], timeout=0.2)
        assert not done, "shutdown returned while the cancellation still needed the ledger"
    finally:
        release.set()
        await closing
    assert (await trace(host, source))[-1]["status"] == "cancelled"
