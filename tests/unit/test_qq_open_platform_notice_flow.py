"""Notice handling in the QQ Open Platform connector: sinks and reception state.

Two families of defect lived here.

Sinks: ``_dispatch_event`` awaited every listener inline, inside the one
``_receive_loop`` task. A listener that waits on a slow request therefore stopped the
connector from reading the next message, and a listener that never returned stopped it
indefinitely -- catching the exception cannot help when nothing is raised.

Reception state: ``receives_all_messages`` and ``needs_attention`` were one-way
latches. The first full-group push set them and nothing cleared them -- not the group
turning push off, not the bot being removed, not a reconnect -- so the connector
advertised a mode the platform had taken away, for the life of the process.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from utils.connection.qq import open_platform as mod
from utils.connection.qq.open_platform import QQOpenPlatformConnection


class _Log:
    def __init__(self):
        self.warnings: list[str] = []
        self.infos: list[str] = []

    def warning(self, msg):
        self.warnings.append(str(msg))

    def info(self, msg):
        self.infos.append(str(msg))


def _conn():
    conn = QQOpenPlatformConnection(app_id="1", client_secret="2")
    conn.logger = _Log()
    return conn


def run(coro):
    return asyncio.run(coro)


def _prove_full_push(conn, group_id="G1"):
    run(conn._dispatch_event("GROUP_MESSAGE_CREATE", {
        "id": "m-" + group_id, "group_openid": group_id, "content": "hi",
        "author": {"member_openid": "MA"}}, "e0"))


# ---- sinks ---------------------------------------------------------------

def test_a_hanging_sink_does_not_stop_the_next_notice_from_being_handled():
    conn = _conn()
    started = asyncio.Event()
    release = asyncio.Event()
    seen: list[str] = []

    async def slow_sink(event_type, payload):
        if event_type == "GROUP_ADD_ROBOT":
            started.set()
            await release.wait()              # never returns unless released
        seen.append(event_type)

    conn.add_event_sink(slow_sink)

    async def go():
        await conn._dispatch_event("GROUP_ADD_ROBOT", {"group_openid": "G1"}, "e1")
        await asyncio.sleep(0)                 # let the detached task start
        assert started.is_set()
        await conn._dispatch_event("FRIEND_ADD", {"user_openid": "U1"}, "e2")
        await asyncio.sleep(0)             # the second detached task needs one turn
        return list(seen)

    # Dispatch returned while the first listener was still blocked, so the second
    # notice reached the same sink instead of queueing behind it. Under the old inline
    # await this deadlocked: the receive loop was waiting on release.
    assert run(go()) == ["FRIEND_ADD"]
    release.set()


def test_sink_timeout_is_reported_and_the_notice_still_drains(monkeypatch):
    conn = _conn()
    monkeypatch.setattr(mod, "_NOTICE_SINK_TIMEOUT_SECONDS", 0.01)
    reached: list[str] = []

    async def sleeper(event_type, payload):
        await asyncio.sleep(5)
        reached.append(event_type)

    conn.add_event_sink(sleeper)

    async def go():
        await conn._dispatch_event("GROUP_ADD_ROBOT", {"group_openid": "G1"}, "e1")
        await asyncio.sleep(0.08)
        return list(conn._sink_tasks)

    tasks = run(go())
    assert reached == []                        # gave up rather than waited
    assert any("timeout" in line or "超时" in line for line in conn.logger.warnings)
    assert not tasks                            # a finished task leaves the set


def test_too_many_outstanding_sinks_drop_the_notice_instead_of_piling_tasks(monkeypatch):
    conn = _conn()
    monkeypatch.setattr(mod, "_NOTICE_SINK_MAX_TASKS", 2)
    gate = asyncio.Event()

    async def blocker(event_type, payload):
        await gate.wait()

    conn.add_event_sink(blocker)

    async def go():
        for index in range(5):
            await conn._dispatch_event(
                "GROUP_ADD_ROBOT", {"group_openid": "G" + str(index)}, "e" + str(index))
        return len(conn._sink_tasks)

    outstanding = run(go())
    assert outstanding == 2                     # bounded, not five
    assert conn.logger.warnings                 # and the drop was said out loud
    gate.set()


def test_disconnect_cancels_outstanding_sink_tasks():
    conn = _conn()
    gate = asyncio.Event()

    async def blocker(event_type, payload):
        await gate.wait()

    conn.add_event_sink(blocker)

    async def go():
        await conn._dispatch_event("GROUP_ADD_ROBOT", {"group_openid": "G1"}, "e1")
        await asyncio.sleep(0)
        tasks = list(conn._sink_tasks)
        await conn.disconnect()
        await asyncio.sleep(0)             # let the cancellation land
        return tasks

    tasks = run(go())
    assert len(tasks) == 1
    assert tasks[0].cancelled() or tasks[0].done()


def test_a_sync_sink_that_raises_is_logged_and_the_transport_survives():
    conn = _conn()
    calls: list[str] = []

    def bad_sink(event_type, payload):
        calls.append(event_type)
        raise RuntimeError("consumer exploded")

    conn.add_event_sink(bad_sink)
    run(conn._dispatch_event("FRIEND_DEL", {"user_openid": "U1"}, "e1"))
    assert calls == ["FRIEND_DEL"]
    assert any("事件监听器异常" in line for line in conn.logger.warnings)


# ---- reception state -----------------------------------------------------

def test_proactive_message_notice_does_not_change_the_reception_mode():
    # GROUP_MSG_REJECT / GROUP_MSG_RECEIVE are the admin allowing or blocking the
    # bot's *proactive* sends ("接受/拒绝机器人主动消息"). They say nothing about
    # whether the group pushes every message, so they must not touch the reception
    # state: mapping them onto recv_msg_setting made an admin enabling proactive
    # push look like full-message delivery, and a refusal erased the proof that a
    # real GROUP_MESSAGE_CREATE had already arrived.
    conn = _conn()
    _prove_full_push(conn, "G1")
    assert conn.group_receives_all_messages("G1") is True

    run(conn._dispatch_event("GROUP_MSG_REJECT", {"group_openid": "G1"}, "e2"))
    assert conn.group_receives_all_messages("G1") is True
    assert conn.group_recv_msg_setting("G1") == ""
    assert conn.group_proactive_allowed("G1") is False

    run(conn._dispatch_event("GROUP_MSG_RECEIVE", {"group_openid": "G1"}, "e3"))
    assert conn.group_proactive_allowed("G1") is True
    assert conn.group_receives_all_messages("G1") is True


def test_proactive_state_is_per_group_and_unknown_until_a_notice_arrives():
    conn = _conn()
    assert conn.group_proactive_allowed("G1") is None
    assert conn.group_proactive_allowed("") is None
    run(conn._dispatch_event("GROUP_MSG_REJECT", {"group_openid": "G1"}, "e1"))
    run(conn._dispatch_event("GROUP_MSG_RECEIVE", {"group_openid": "G2"}, "e2"))
    assert conn.group_proactive_allowed("G1") is False
    assert conn.group_proactive_allowed("G2") is True


def test_being_removed_from_a_group_forgets_its_proactive_state():
    conn = _conn()
    run(conn._dispatch_event("GROUP_MSG_RECEIVE", {"group_openid": "G1"}, "e1"))
    run(conn._dispatch_event("GROUP_DEL_ROBOT", {"group_openid": "G1"}, "e2"))
    assert conn.group_proactive_allowed("G1") is None


def test_being_removed_from_a_group_forgets_what_that_group_proved():
    conn = _conn()
    _prove_full_push(conn, "G1")
    conn._learn_bot_openid("G1", "BOTID1")
    conn._bot_member_role_by_group["G1"] = "admin"
    run(conn._dispatch_event("GROUP_DEL_ROBOT", {"group_openid": "G1"}, "e5"))
    assert conn.group_receives_all_messages("G1") is False
    assert conn._remembered_bot_openid("G1") == ""
    assert conn.group_bot_member_role("G1") == ""
    assert "G1" not in conn._seen_groups


def test_identify_relearns_the_mode_that_a_resume_keeps():
    # A brand-new session can find the privilege withdrawn while the bot was away; a
    # resumed session never lost it, so only Identify may clear the proof.
    conn = _conn()
    _prove_full_push(conn, "G1")

    class _WS:
        def __init__(self):
            self.sent: list[dict] = []

        async def send(self, raw):
            self.sent.append(json.loads(raw))

        async def recv(self):
            await asyncio.sleep(0)
            return json.dumps({
                "op": 0, "t": "READY", "s": 1,
                "d": {"session_id": "S1", "user": {"id": "u1"}}})

        async def close(self):
            return None

    ws = _WS()
    conn._ws = ws
    conn._access_token = "t"
    conn._token_expires_at = float("inf")

    run(conn._handshake(is_reconnect=False))
    assert conn._all_messages_observed_groups == set()


def test_a_successful_resume_keeps_the_mode_it_already_proved():
    conn = _conn()
    _prove_full_push(conn, "G1")

    class _WS:
        def __init__(self):
            self.frames = [
                json.dumps({"op": 10, "d": {"heartbeat_interval": 30000,
                                            "session_id": "S0"}}),
                json.dumps({"op": 0, "t": "RESUMED", "s": 5, "d": {}}),
            ]

        async def send(self, raw):
            return None

        async def recv(self):
            await asyncio.sleep(0)
            return self.frames.pop(0) if self.frames else json.dumps(
                {"op": 0, "t": "READY", "s": 6, "d": {"session_id": "S0",
                                                      "user": {"id": "u1"}}})

        async def close(self):
            return None

    conn._ws = _WS()
    conn._access_token = "t"
    conn._token_expires_at = float("inf")
    conn._session_id = "S0"
    conn._last_seq = 4

    run(conn._handshake(is_reconnect=True))
    assert conn._all_messages_observed_groups == {"G1"}

# ---- attribution window: the case in-place merging cannot reach ----------------
#
# A waiting consumer sits on queue.get(), so the full-push copy leaves the queue
# before its AT frame arrives. _merge_pending_duplicate then finds nothing to
# upgrade and the summon is delivered as chatter anyway -- which is the common
# case, not the rare one. The hold covers it: wait one short window for the AT
# frame, then deliver.

def test_idle_consumer_still_gets_the_summon_from_the_late_at_frame():
    import asyncio

    monkey_loop = asyncio.new_event_loop()
    conn = QQOpenPlatformConnection.__new__(QQOpenPlatformConnection)
    conn._self_id = ""
    conn.logger = None
    conn._message_queue = asyncio.Queue(maxsize=8)
    full = {
        "id": "summon-1", "group_openid": "G1", "content": "喂 <@GGGGGGGGGGGGGGGG>",
        "author": {"member_openid": "MA"},
    }
    at = dict(full, mentions=[{"member_openid": "GGGGGGGGGGGGGGGG", "is_you": True}])

    async def go():
        delivered = []

        async def consumer():
            while True:
                msg = await conn.receive_message(timeout=0.05)
                if msg:
                    delivered.append(msg)

        drive = asyncio.ensure_future(go_frames())
        watch = asyncio.ensure_future(consumer())
        await asyncio.wait_for(drive, timeout=2)
        await asyncio.sleep(0.7)              # past the attribution window, so the
        watch.cancel()                        # held copy has flushed and dropped
        try:
            await watch                        # reaped, not left pending on close
        except asyncio.CancelledError:
            pass
        return delivered

    async def go_frames():
        await conn._dispatch_event("GROUP_MESSAGE_CREATE", full, "e1")
        await asyncio.sleep(0.02)             # the consumer takes this one first
        await conn._dispatch_event("GROUP_AT_MESSAGE_CREATE", at, "e2")

    delivered = monkey_loop.run_until_complete(go())
    monkey_loop.close()

    assert len(delivered) == 1                # one delivery, not two, not zero
    assert delivered[0]["is_at_bot"] is True
    assert delivered[0]["mentions_other_user"] is False


def test_held_copy_is_still_delivered_when_no_at_frame_comes(monkeypatch):
    import asyncio

    monkeypatch.setattr(mod, "_AT_MERGE_WINDOW_SECONDS", 0.01)
    conn = _conn()
    payload = {
        "id": "lonely-1", "group_openid": "G5", "content": "看 <@HHHHHHHHHHHHHHHH>",
        "author": {"member_openid": "MA"},
    }

    async def go():
        await conn._dispatch_event("GROUP_MESSAGE_CREATE", payload, "e1")
        assert conn._message_queue.empty()     # held, not dropped
        await asyncio.sleep(0.05)
        return conn._message_queue.get_nowait()

    msg = asyncio.run(go())
    assert msg["message_id"] == "lonely-1"
    assert msg["is_at_bot"] is False


def test_hold_is_flushed_on_disconnect_rather_than_dropped():
    import asyncio

    conn = _conn()
    payload = {
        "id": "held-1", "group_openid": "G6", "content": "hi <@IIIIIIIIIIIIIIII>",
        "author": {"member_openid": "MA"},
    }

    async def go():
        await conn._dispatch_event("GROUP_MESSAGE_CREATE", payload, "e1")
        assert "held-1" in conn._attribution_hold
        await conn.disconnect()
        return conn._message_queue.qsize(), conn._attribution_hold

    size, pending = asyncio.run(go())
    assert pending == {}                       # nothing left holding
    assert size == 1                           # and nothing lost


def test_merge_upgrades_the_ownership_flag_too():
    # The merge is a queue-level rule, reached when the full copy is already queued:
    # hold saturated, or a frame the hold rule does not catch. So drive it at the queue
    # level, which is where it lives, instead of through _dispatch_event.
    conn = _conn()
    full = {"message_id": "sum-1", "is_at_bot": False, "mentions_other_user": True,
            "mentioned_user_ids": ["HHHHHHHHHHHHHHHH"], "content": "x"}
    at = {"message_id": "sum-1", "is_at_bot": True, "mentions_other_user": False,
          "mentioned_user_ids": ["JJJJJJJJJJJJJJJJ"], "content": "x"}
    assert conn._enqueue_inbound(full) is None
    # "merged", not "duplicate": the second frame changed what the queued copy says.
    assert conn._enqueue_inbound(at) == "merged"
    queued = conn._message_queue.get_nowait()
    assert queued["is_at_bot"] is True
    assert queued["mentions_other_user"] is False


def test_quoted_reply_is_not_evicted_as_chatter():
    import asyncio

    conn = QQOpenPlatformConnection.__new__(QQOpenPlatformConnection)
    conn._self_id = ""
    conn.logger = None
    conn._message_queue = asyncio.Queue(maxsize=2)
    reply = {"message_id": "r1", "is_at_bot": False, "is_reply_to_bot": True}
    assert conn._enqueue_inbound(reply) is None
    assert conn._enqueue_inbound({"message_id": "c1", "is_at_bot": False}) is None
    # Queue full now. The oldest unaddressed copy (c1) is sacrificed and the newcomer
    # fits; the direct reply r1 stays. Under the old predicate r1 and c1 looked the
    # same -- both "not @bot" -- so the reply to the bot was the first thing a burst
    # could throw away.
    assert conn._enqueue_inbound({"message_id": "c2", "is_at_bot": False}) is None
    queued = [conn._message_queue.get_nowait()["message_id"] for _ in range(2)]
    assert "r1" in queued
    assert "c1" not in queued
    assert "c2" in queued

def test_coroutine_sink_is_not_called_before_its_task_runs():
    # Calling the sink first and wrapping the coroutine afterwards left an un-awaited
    # coroutine object behind whenever disconnect() cancelled the task before its first
    # step: a "coroutine ... was never awaited" warning on every shutdown under
    # traffic, and the notice gone without a log. The guard is that dispatch returns
    # with the sink NOT yet called and exactly one task owning the call. Whether the
    # task then runs is covered by the timeout, bound and hanging-sink cases above.
    conn = _conn()
    called = []

    async def sink(event_type, payload):
        called.append(event_type)

    conn.add_event_sink(sink)

    async def go():
        await conn._dispatch_event("FRIEND_ADD", {"user_openid": "U1"}, "e1")
        return list(called), len(conn._sink_tasks)

    called_after_dispatch, tasks = run(go())
    assert called_after_dispatch == []
    assert tasks == 1

def test_full_queue_reject_does_not_leak_a_sink_coroutine(recwarn):
    # Greptile r4575301768: a synchronous sink that returns a coroutine, rejected by the
    # task cap, used to close only the wrapper. The real coroutine stayed alive
    # unawaited and emitted a RuntimeWarning per dropped notification.
    conn = _conn()
    release = asyncio.Event()

    def sync_sink_returning_coroutine(event_type, payload):
        async def body():
            await release.wait()
        return body()

    conn.add_event_sink(sync_sink_returning_coroutine)

    async def go():
        for i in range(40):
            await conn._dispatch_event("FRIEND_ADD", {"user_openid": "U" + str(i)}, "e")
        return len(conn._sink_tasks)

    pending = run(go())
    assert pending == _max_tasks()

    async def drain():
        # Let the accepted tasks finish inside the same loop, so the shutdown stays
        # quiet: this test asserts the absence of a leak, not the presence of noise.
        release.set()
        for task in list(conn._sink_tasks):
            await task

    run(drain())
    leaked = [str(w.message) for w in recwarn.list if "never awaited" in str(w.message)]
    assert leaked == []


def _max_tasks():
    from utils.connection.qq import open_platform as op
    return op._NOTICE_SINK_MAX_TASKS


def test_disconnect_before_first_step_leaks_nothing(recwarn):
    # CodeRabbit r4575321815 / r4576525885, wehos r4576541311, greptile r4576493238:
    # this test used to define go() and never run it, so it asserted nothing and the
    # regression it guards would have passed anyway. It now dispatches, disconnects
    # without yielding, and checks both the task state and the warning list.
    conn = _conn()

    async def sink(event_type, payload):
        await asyncio.sleep(5)

    conn.add_event_sink(sink)

    async def go():
        await conn._dispatch_event("FRIEND_ADD", {"user_openid": "U1"}, "e1")
        created = list(conn._sink_tasks)
        await conn.disconnect()
        await asyncio.sleep(0)                 # deliver the cancellation
        return created

    async def checked():
        got = await go()
        # Checked before the loop can close: a closing loop cancels pending tasks and
        # would make this pass without the disconnect actually doing anything.
        return bool(got) and all(x.cancelled() or x.done() for x in got)

    loop = asyncio.new_event_loop()
    try:
        ok = loop.run_until_complete(checked())
    finally:
        loop.close()
    assert ok, "the sink task was never scheduled or never cancelled by disconnect()"
    leaked = [str(w.message) for w in recwarn.list if "never awaited" in str(w.message)]
    assert leaked == []


def test_future_returning_sink_is_timed_out_not_parked_forever(monkeypatch):
    # greptile r4576493234 / CodeRabbit r4576525889 / wehos r4576541138: a plain
    # callable that returns an awaitable (``lambda et, p: gate.wait()`` gives a Future,
    # not a coroutine) used to be scheduled without any deadline. Hung listeners then
    # kept their slots for the life of the connection.
    monkeypatch.setattr(mod, "_NOTICE_SINK_TIMEOUT_SECONDS", 0.05)
    conn = _conn()
    gate = asyncio.Event()

    conn.add_event_sink(lambda event_type, payload: gate.wait())

    async def go():
        await conn._dispatch_event("FRIEND_ADD", {"user_openid": "U1"}, "e1")
        tasks = list(conn._sink_tasks)
        await asyncio.sleep(0.2)               # five times the deadline
        # Decided here, while the loop is still open: closing the loop cancels pending
        # tasks on its own, and that would hide a missing deadline.
        return (bool(tasks), all(t.cancelled() or t.done() for t in tasks),
                set(conn._sink_tasks), list(conn.logger.warnings))

    scheduled, settled, remaining, warnings = run(go())
    assert scheduled, "no task was scheduled, so the deadline had nothing to bound"
    assert settled, "the awaitable outlived its deadline: no timeout was applied"
    assert remaining == set()                  # the slot came back
    assert any("超时" in line for line in warnings)


def test_saturation_does_not_starve_a_synchronous_sink(monkeypatch):
    # wehos r4576541138, problem 2: the slot check used to sit in front of the branch,
    # so a saturated pool also skipped listeners that never create a task at all.
    monkeypatch.setattr(mod, "_NOTICE_SINK_MAX_TASKS", 2)
    conn = _conn()
    gate = asyncio.Event()
    calls = []

    conn.add_event_sink(lambda event_type, payload: gate.wait())     # hangs, takes slots
    conn.add_event_sink(lambda event_type, payload: calls.append(event_type))

    async def go():
        for i in range(6):
            await conn._dispatch_event("FRIEND_ADD", {"user_openid": "U" + str(i)}, "e")
        return len(conn._sink_tasks), list(calls)

    tasks, seen = run(go())
    assert tasks == 2                          # pool capped
    assert len(seen) == 6                      # the sync listener still saw everything

    async def drain():
        # Release and await the held tasks inside one open loop, so the shutdown of the
        # next one is not filled with "Task was destroyed but it is pending".
        gate.set()
        await asyncio.gather(*list(conn._sink_tasks), return_exceptions=True)

    run(drain())
    assert conn._sink_tasks == set()


def test_repeated_unattributed_copy_keeps_one_timer():
    # CodeRabbit r4576525899: re-registering the same message id left an orphan timer
    # that neither the AT frame nor disconnect() could cancel, and whose completion
    # callback discarded the live registration instead of its own.
    conn = _conn()
    payload = {
        "id": "m-twice", "group_openid": "G12", "content": "喂 <@KKKKKKKKKKKKKKKK>",
        "author": {"member_openid": "H"},
    }

    async def go():
        await conn._dispatch_event("GROUP_MESSAGE_CREATE", payload, "e1")
        await asyncio.sleep(0)
        await conn._dispatch_event("GROUP_MESSAGE_CREATE", payload, "e2")
        await asyncio.sleep(0)
        return len(conn._attribution_hold_tasks), dict(conn._attribution_hold)

    tasks, holds = run(go())
    assert tasks == 1
    assert list(holds) == ["m-twice"]

    async def take_over():
        await conn._dispatch_event("GROUP_AT_MESSAGE_CREATE", dict(payload, content="喂"), "e3")
        await asyncio.sleep(0)
        return dict(conn._attribution_hold_tasks)

    assert run(take_over()) == {}              # the one timer is the one that cancels
