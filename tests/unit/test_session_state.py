"""``SessionStateMachine`` 的单元测试。

覆盖点：
1. 初始态 / 正常 proactive 生命周期转移
2. USER_INPUT 在 phase1 / phase2 / committing 的 sticky preempt 行为
3. ``is_proactive_preempted`` 的零成本读（sticky flag + claim_token 兜底）
4. ``can_start_proactive`` 在各状态下的返回
5. ``PROACTIVE_DONE`` 的 owner 复位规则（被抢占时不误覆盖 USER）
6. 订阅者的派发顺序与状态一致性
7. 并发 fire 的最终态不依赖调度顺序
"""

from __future__ import annotations

import asyncio

from main_logic.session_state import (
    ProactivePhase,
    SessionEvent,
    SessionStateMachine,
    TurnOwner,
)


def _sm() -> SessionStateMachine:
    return SessionStateMachine(lanlan_name="Test")


# ─────────────────────────────────────────────────────────────────────────────
# 初始态 & 正常 proactive 生命周期
# ─────────────────────────────────────────────────────────────────────────────

async def test_initial_state_is_idle():
    sm = _sm()
    assert sm.owner is TurnOwner.NONE
    assert sm.phase is ProactivePhase.IDLE
    assert sm.proactive_sid is None
    assert sm._preempted is False
    assert sm.is_proactive_preempted() is False
    assert sm.can_start_proactive() is True


async def test_normal_proactive_lifecycle():
    sm = _sm()
    await sm.fire(SessionEvent.PROACTIVE_START)
    assert sm.phase is ProactivePhase.PHASE1
    assert sm.owner is TurnOwner.PROACTIVE

    await sm.fire(SessionEvent.PROACTIVE_CLAIM, sid="sid_1")
    assert sm.proactive_sid == "sid_1"

    await sm.fire(SessionEvent.PROACTIVE_PHASE2)
    assert sm.phase is ProactivePhase.PHASE2

    await sm.fire(SessionEvent.PROACTIVE_COMMITTING)
    assert sm.phase is ProactivePhase.COMMITTING

    await sm.fire(SessionEvent.PROACTIVE_DONE)
    assert sm.phase is ProactivePhase.IDLE
    assert sm.owner is TurnOwner.NONE
    assert sm.proactive_sid is None


# ─────────────────────────────────────────────────────────────────────────────
# USER_INPUT sticky preempt
# ─────────────────────────────────────────────────────────────────────────────

async def test_user_input_during_phase1_marks_preempted():
    sm = _sm()
    await sm.fire(SessionEvent.PROACTIVE_START)
    await sm.fire(SessionEvent.USER_INPUT, sid="user_sid")
    assert sm._preempted is True
    assert sm.owner is TurnOwner.USER
    assert sm.is_proactive_preempted() is True


async def test_user_input_during_phase2_marks_preempted():
    sm = _sm()
    await sm.fire(SessionEvent.PROACTIVE_START)
    await sm.fire(SessionEvent.PROACTIVE_CLAIM, sid="sid_2")
    await sm.fire(SessionEvent.PROACTIVE_PHASE2)
    await sm.fire(SessionEvent.USER_INPUT, sid="user_sid")
    assert sm._preempted is True
    assert sm.is_proactive_preempted(claim_token="sid_2") is True


async def test_user_input_during_committing_marks_preempted():
    """Commit 极短窗口内也得标 preempted —— 后续 _record_proactive_chat 等
    副作用仍会走 committed=False 路径跳过，但 SM 要如实反映真相。"""
    sm = _sm()
    await sm.fire(SessionEvent.PROACTIVE_START)
    await sm.fire(SessionEvent.PROACTIVE_CLAIM, sid="sid_3")
    await sm.fire(SessionEvent.PROACTIVE_PHASE2)
    await sm.fire(SessionEvent.PROACTIVE_COMMITTING)
    await sm.fire(SessionEvent.USER_INPUT, sid="user_sid")
    assert sm._preempted is True


async def test_user_input_when_idle_does_not_flip_preempted():
    sm = _sm()
    await sm.fire(SessionEvent.USER_INPUT, sid="u")
    assert sm._preempted is False
    assert sm.owner is TurnOwner.USER
    assert sm.is_proactive_preempted() is False


# ─────────────────────────────────────────────────────────────────────────────
# is_proactive_preempted 的 claim_token 兜底
# ─────────────────────────────────────────────────────────────────────────────

async def test_is_proactive_preempted_sticky_after_user_input():
    sm = _sm()
    await sm.fire(SessionEvent.PROACTIVE_START)
    await sm.fire(SessionEvent.PROACTIVE_CLAIM, sid="sid_x")
    # 即使 sid 还没动，sticky flag 已经是 True
    await sm.fire(SessionEvent.USER_INPUT, sid="u")
    # claim_token 与 proactive_sid 其实一致，但 sticky 仍然返回 True
    assert sm.is_proactive_preempted(claim_token="sid_x") is True


async def test_is_proactive_preempted_claim_token_mismatch_fallback():
    """proactive_sid 不等于 claim_token 时也应返回 True —— 防御性兜底，
    正常情况 sticky flag 已先触发，这里是双保险。"""
    sm = _sm()
    await sm.fire(SessionEvent.PROACTIVE_START)
    await sm.fire(SessionEvent.PROACTIVE_CLAIM, sid="sid_current")
    assert sm.is_proactive_preempted(claim_token="sid_stale") is True


async def test_is_proactive_preempted_claim_token_matches_not_preempted():
    sm = _sm()
    await sm.fire(SessionEvent.PROACTIVE_START)
    await sm.fire(SessionEvent.PROACTIVE_CLAIM, sid="sid_m")
    assert sm.is_proactive_preempted(claim_token="sid_m") is False


async def test_is_proactive_preempted_phase1_none_token_ok():
    """phase1 尚未 claim 时 claim_token=None，只看 sticky flag。"""
    sm = _sm()
    await sm.fire(SessionEvent.PROACTIVE_START)
    assert sm.is_proactive_preempted(claim_token=None) is False


async def test_mark_user_input_preempt_sync_flips_flag_during_active_phase():
    """``mark_user_input_preempt`` 是 sid 轮换路径在 ``self.lock`` 内同步调用的
    helper，保证 sid 写入 + preempt 翻起原子可见。活动阶段必须翻，idle 阶段不翻。
    """
    sm = _sm()
    # idle 时 no-op
    sm.mark_user_input_preempt()
    assert sm._preempted is False

    await sm.fire(SessionEvent.PROACTIVE_START)
    assert sm._preempted is False
    sm.mark_user_input_preempt()
    assert sm._preempted is True

    # PHASE2 / COMMITTING 同样覆盖
    sm._preempted = False
    await sm.fire(SessionEvent.PROACTIVE_PHASE2)
    sm.mark_user_input_preempt()
    assert sm._preempted is True

    sm._preempted = False
    await sm.fire(SessionEvent.PROACTIVE_COMMITTING)
    sm.mark_user_input_preempt()
    assert sm._preempted is True


async def test_mark_user_input_preempt_atomically_visible_before_full_fire():
    """回归 race：prepare_proactive_delivery 必须在 ``mark_user_input_preempt``
    之后、``fire(USER_INPUT)`` 之前，也能从 ``is_proactive_preempted()`` 看到 True。
    这模拟 core.py 里 sid 轮换的临界区语义。
    """
    sm = _sm()
    await sm.fire(SessionEvent.PROACTIVE_START)
    await sm.fire(SessionEvent.PROACTIVE_CLAIM, sid="claim_sid")

    # 模拟 handle_new_message 持 self.lock 写新 sid + mark 翻 flag 的片段
    sm.mark_user_input_preempt()
    # 此刻 fire(USER_INPUT) 还没调用；但并发的 prepare_proactive_delivery 在
    # 其 lock-held 复查点已能看到抢占，绝不会再误写 current_speech_id。
    assert sm.is_proactive_preempted(claim_token="claim_sid") is True


async def test_proactive_claim_dropped_if_preempted():
    """用户在 phase1 抢占后，即使 prepare_proactive_delivery 还是 fire 了
    CLAIM，proactive_sid 不应被填上 —— 否则 phase2 会误以为自己仍然持有 turn。"""
    sm = _sm()
    await sm.fire(SessionEvent.PROACTIVE_START)
    await sm.fire(SessionEvent.USER_INPUT, sid="u")
    await sm.fire(SessionEvent.PROACTIVE_CLAIM, sid="should_not_stick")
    assert sm.proactive_sid is None
    assert sm.is_proactive_preempted() is True


# ─────────────────────────────────────────────────────────────────────────────
# can_start_proactive
# ─────────────────────────────────────────────────────────────────────────────

async def test_can_start_proactive_false_when_phase1():
    sm = _sm()
    await sm.fire(SessionEvent.PROACTIVE_START)
    assert sm.can_start_proactive() is False


async def test_can_start_proactive_true_after_user_turn_completes():
    """Regression: owner == USER 不能永久阻塞 proactive。

    USER_INPUT 会把 owner 翻到 USER，但没有 AI_RESPONSE_END 事件将其复位
    （Stage 2 未做该项迁移）。can_start_proactive 若卡 owner == USER，用户
    发第一条消息后所有后续 proactive 都会被永久 409 掉 —— 这是 Codex review
    发现的 P1 bug。正确语义：只看 phase 和 session._is_responding。
    """
    sm = _sm()
    await sm.fire(SessionEvent.USER_INPUT, sid="u")
    # 用户发完了，AI 也不在响应（session=None），proactive 必须能起
    assert sm.can_start_proactive(session=None) is True
    # 即使传 session，只要 _is_responding=False 就能起
    assert sm.can_start_proactive(session=_FakeSession(is_responding=False)) is True


async def test_can_start_proactive_true_after_done():
    sm = _sm()
    await sm.fire(SessionEvent.PROACTIVE_START)
    await sm.fire(SessionEvent.PROACTIVE_DONE)
    assert sm.can_start_proactive() is True


class _FakeSession:
    """只暴露 `_is_responding` 的最小 session stub。"""

    def __init__(self, is_responding: bool) -> None:
        self._is_responding = is_responding


async def test_can_start_proactive_false_when_session_is_responding():
    """AI 正在为用户回复 —— 即使 SM 自己看起来 IDLE，也不能起 proactive。

    这一步把原来 router 里直读 session._is_responding 的检查收拢到 SM。
    """
    sm = _sm()
    assert sm.phase is ProactivePhase.IDLE
    session = _FakeSession(is_responding=True)
    assert sm.can_start_proactive(session=session) is False


async def test_can_start_proactive_true_with_idle_session():
    sm = _sm()
    session = _FakeSession(is_responding=False)
    assert sm.can_start_proactive(session=session) is True


async def test_can_start_proactive_none_session_falls_back_to_sm_only():
    """session=None 时只看 SM 自己的字段，向后兼容单元测试。"""
    sm = _sm()
    assert sm.can_start_proactive(session=None) is True
    await sm.fire(SessionEvent.PROACTIVE_START)
    assert sm.can_start_proactive(session=None) is False


async def test_can_start_proactive_session_without_is_responding_attr():
    """session 没有 `_is_responding` 字段时（老 session 类型）不该抛，当作 False 处理。"""
    sm = _sm()

    class _BareSession:
        pass

    assert sm.can_start_proactive(session=_BareSession()) is True


# ─────────────────────────────────────────────────────────────────────────────
# PROACTIVE_DONE 对 owner 的处理
# ─────────────────────────────────────────────────────────────────────────────

async def test_proactive_done_preserves_user_ownership_after_preempt():
    """抢占路径下，proactive 最后 fire DONE 时 owner 已经是 USER，
    DONE 不该把 owner 改成 NONE —— 用户仍然持有 turn。"""
    sm = _sm()
    await sm.fire(SessionEvent.PROACTIVE_START)
    await sm.fire(SessionEvent.USER_INPUT, sid="u")
    assert sm.owner is TurnOwner.USER
    await sm.fire(SessionEvent.PROACTIVE_DONE)
    assert sm.owner is TurnOwner.USER
    # 但 phase 和 sticky flag 确实复位
    assert sm.phase is ProactivePhase.IDLE
    assert sm._preempted is False


async def test_proactive_done_flips_owner_to_none_on_clean_exit():
    sm = _sm()
    await sm.fire(SessionEvent.PROACTIVE_START)
    await sm.fire(SessionEvent.PROACTIVE_CLAIM, sid="c")
    await sm.fire(SessionEvent.PROACTIVE_PHASE2)
    await sm.fire(SessionEvent.PROACTIVE_COMMITTING)
    await sm.fire(SessionEvent.PROACTIVE_DONE)
    assert sm.owner is TurnOwner.NONE


async def test_second_proactive_after_done_starts_fresh():
    """一轮抢占结束后，下一轮 proactive 应重新归 IDLE → PHASE1，sticky 清零。"""
    sm = _sm()
    await sm.fire(SessionEvent.PROACTIVE_START)
    await sm.fire(SessionEvent.USER_INPUT, sid="u1")
    await sm.fire(SessionEvent.PROACTIVE_DONE)
    # USER_INPUT 让 owner 变 USER；下一轮 START 要能重新 claim
    await sm.fire(SessionEvent.PROACTIVE_START)
    assert sm._preempted is False
    assert sm.phase is ProactivePhase.PHASE1
    assert sm.owner is TurnOwner.PROACTIVE
    assert sm.is_proactive_preempted() is False


# ─────────────────────────────────────────────────────────────────────────────
# 订阅
# ─────────────────────────────────────────────────────────────────────────────

async def test_subscriber_sees_post_apply_state():
    """订阅者在回调触发时观察到的状态必然是"事件 apply 后"的状态。"""
    sm = _sm()
    observed: list[tuple[ProactivePhase, TurnOwner]] = []

    def cb(event, payload):
        observed.append((sm.phase, sm.owner))

    sm.subscribe(SessionEvent.PROACTIVE_START, cb)
    await sm.fire(SessionEvent.PROACTIVE_START)
    # 订阅者回调是异步 schedule，让一次事件循环
    await asyncio.sleep(0)
    assert observed == [(ProactivePhase.PHASE1, TurnOwner.PROACTIVE)]


async def test_subscriber_exception_does_not_break_event_flow():
    sm = _sm()

    def bad(event, payload):
        raise RuntimeError("subscriber should not break caller")

    sm.subscribe(SessionEvent.PROACTIVE_START, bad)
    # fire 不应该抛
    await sm.fire(SessionEvent.PROACTIVE_START)
    assert sm.phase is ProactivePhase.PHASE1


async def test_wildcard_subscriber_gets_all_events():
    sm = _sm()
    received: list[SessionEvent] = []

    def cb(event, payload):
        received.append(event)

    sm.subscribe(None, cb)
    await sm.fire(SessionEvent.PROACTIVE_START)
    await sm.fire(SessionEvent.USER_INPUT, sid="u")
    await asyncio.sleep(0)
    assert SessionEvent.PROACTIVE_START in received
    assert SessionEvent.USER_INPUT in received


async def test_async_subscriber_coroutine_is_scheduled():
    sm = _sm()
    fired = asyncio.Event()

    async def cb(event, payload):
        fired.set()

    sm.subscribe(SessionEvent.PROACTIVE_START, cb)
    await sm.fire(SessionEvent.PROACTIVE_START)
    await asyncio.wait_for(fired.wait(), timeout=1.0)


async def test_async_subscriber_returning_task_is_also_awaited():
    """订阅者若返回已创建的 Task（而非 coroutine），派发路径也要能接管它，
    异常/取消才不会绕过 ``_swallow_subscriber_exc`` 泄漏到事件循环。
    """
    sm = _sm()
    inner_fired = asyncio.Event()

    async def inner():
        inner_fired.set()

    def cb(event, payload):
        # 返回 Task 而非 coroutine；早先的 iscoroutine 分支会把它漏过去
        return asyncio.create_task(inner())

    sm.subscribe(SessionEvent.PROACTIVE_START, cb)
    await sm.fire(SessionEvent.PROACTIVE_START)
    await asyncio.wait_for(inner_fired.wait(), timeout=1.0)


async def test_async_subscriber_returning_task_with_exception_is_swallowed():
    """返回 Task 的订阅者，其任务抛异常不应产生 "Task exception was never
    retrieved" warning（由 done-callback 静默吞下）。
    """
    sm = _sm()

    async def inner_raises():
        raise RuntimeError("boom")

    def cb(event, payload):
        return asyncio.create_task(inner_raises())

    sm.subscribe(SessionEvent.PROACTIVE_START, cb)
    await sm.fire(SessionEvent.PROACTIVE_START)
    # 让 inner_raises 任务运行并被 done-callback 吞掉
    await asyncio.sleep(0)
    await asyncio.sleep(0)


# ─────────────────────────────────────────────────────────────────────────────
# 并发
# ─────────────────────────────────────────────────────────────────────────────

async def test_concurrent_user_input_and_proactive_start_converges():
    """USER_INPUT 和 PROACTIVE_START 并发 fire：无论谁先，终态都自洽。

    - USER_INPUT 先：owner=USER，PROACTIVE_START 后 owner 被改成 PROACTIVE，
      phase=PHASE1，此时 is_proactive_preempted=False（因为 sticky 在 START
      时被清零 —— 这是故意的：START 表示一轮新 proactive，上一轮遗留的
      sticky flag 不该影响新轮次）。
    - PROACTIVE_START 先：phase=PHASE1，USER_INPUT 后 _preempted=True、
      owner=USER，sticky 翻起。
    这两条路径终态不同是意料之中的 —— 我们只验证各自都"自洽"。
    """
    # Case A: USER_INPUT 先
    sm_a = _sm()
    await sm_a.fire(SessionEvent.USER_INPUT, sid="u")
    await sm_a.fire(SessionEvent.PROACTIVE_START)
    assert sm_a.phase is ProactivePhase.PHASE1
    assert sm_a.owner is TurnOwner.PROACTIVE
    assert sm_a._preempted is False

    # Case B: PROACTIVE_START 先
    sm_b = _sm()
    await sm_b.fire(SessionEvent.PROACTIVE_START)
    await sm_b.fire(SessionEvent.USER_INPUT, sid="u")
    assert sm_b.phase is ProactivePhase.PHASE1  # phase 自己不动，由 DONE 清
    assert sm_b.owner is TurnOwner.USER
    assert sm_b._preempted is True


async def test_try_start_proactive_atomic_only_one_winner():
    """并发 try_start_proactive：只有一路拿到 turn 所有权，其余都返回 False。

    这是"检查 + 占坑"合成原子操作的核心保证：若分裂为 can_start_proactive +
    fire(PROACTIVE_START) 两步，两个请求都能在 IDLE 时通过 can_start，进而各自
    fire 进 PHASE1，claim/commit 互相踩 turn。
    """
    sm = _sm()

    results = await asyncio.gather(
        sm.try_start_proactive(),
        sm.try_start_proactive(),
        sm.try_start_proactive(),
    )
    assert results.count(True) == 1
    assert results.count(False) == 2
    assert sm.phase is ProactivePhase.PHASE1
    assert sm.owner is TurnOwner.PROACTIVE


async def test_try_start_proactive_refuses_when_session_is_responding():
    """AI 正在为用户回复时（session._is_responding=True），即使 SM phase=IDLE，
    try_start_proactive 也必须返回 False（与 can_start_proactive 语义对齐）。
    """
    sm = _sm()

    class _Resp:
        _is_responding = True

    ok = await sm.try_start_proactive(session=_Resp())
    assert ok is False
    assert sm.phase is ProactivePhase.IDLE
    assert sm.owner is TurnOwner.NONE


async def test_try_start_proactive_dispatches_subscribers():
    """try_start_proactive 抢到所有权时也要派发 PROACTIVE_START 订阅者，
    与直接 fire(PROACTIVE_START) 行为一致。
    """
    sm = _sm()
    hits: list[SessionEvent] = []

    def cb(event, payload):
        hits.append(event)

    sm.subscribe(SessionEvent.PROACTIVE_START, cb)
    ok = await sm.try_start_proactive()
    assert ok is True
    await asyncio.sleep(0)
    assert hits == [SessionEvent.PROACTIVE_START]


async def test_try_start_proactive_false_does_not_dispatch():
    """try_start_proactive 败者（第二名）不应误触发 PROACTIVE_START 订阅者，
    否则订阅者会以为有两轮 proactive 在跑。
    """
    sm = _sm()
    await sm.fire(SessionEvent.PROACTIVE_START)  # 先占坑
    hits: list[SessionEvent] = []

    def cb(event, payload):
        hits.append(event)

    sm.subscribe(SessionEvent.PROACTIVE_START, cb)
    ok = await sm.try_start_proactive()
    assert ok is False
    await asyncio.sleep(0)
    assert hits == []


async def test_swallow_subscriber_cancelled_error():
    """异步订阅者被取消时，done-callback 必须吞掉 CancelledError，
    否则 "Task exception was never retrieved" warning 会刷屏。
    """
    from main_logic.session_state import _swallow_subscriber_exc

    async def never_returns():
        await asyncio.sleep(10)

    task = asyncio.create_task(never_returns())
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        # 故意吞：这里只是把 task 驱动到 done 态供下面 _swallow_subscriber_exc
        # 调用，CancelledError 本身就是测试期望的结果。
        pass
    _swallow_subscriber_exc(task)


async def test_fire_is_serialized_by_write_lock():
    """并发 fire 应被 write_lock 串行化，内部状态永远一致。"""
    sm = _sm()

    async def burst():
        await sm.fire(SessionEvent.PROACTIVE_START)
        await sm.fire(SessionEvent.PROACTIVE_CLAIM, sid="s")
        await sm.fire(SessionEvent.PROACTIVE_PHASE2)
        await sm.fire(SessionEvent.PROACTIVE_COMMITTING)
        await sm.fire(SessionEvent.PROACTIVE_DONE)

    await asyncio.gather(burst(), burst(), burst())
    # 三轮完整生命周期之后终态必然是 IDLE
    assert sm.phase is ProactivePhase.IDLE


# ─────────────────────────────────────────────────────────────────────────────
# USER_ACTIVITY（静默信号，不轮换 sid/owner）
# ─────────────────────────────────────────────────────────────────────────────

async def test_user_activity_updates_timestamp_without_owner_flip():
    sm = _sm()
    await sm.fire(SessionEvent.PROACTIVE_START)
    before_activity = sm.last_user_activity
    await sm.fire(SessionEvent.USER_ACTIVITY)
    assert sm.last_user_activity > before_activity
    # owner 未被改动
    assert sm.owner is TurnOwner.PROACTIVE
    # sticky 未被翻（USER_ACTIVITY 不表示抢占）
    assert sm._preempted is False


# ─────────────────────────────────────────────────────────────────────────────
# snapshot
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# reset() —— teardown hook，防止跨 session 状态泄漏
# ─────────────────────────────────────────────────────────────────────────────

async def test_reset_clears_state_after_proactive_done():
    """PROACTIVE_DONE fire 后，reset 应把所有 per-session 字段回零。"""
    sm = _sm()
    await sm.fire(SessionEvent.PROACTIVE_START)
    await sm.fire(SessionEvent.PROACTIVE_CLAIM, sid="sid_x")
    await sm.fire(SessionEvent.USER_INPUT, sid="u")
    await sm.fire(SessionEvent.PROACTIVE_DONE)

    # 此刻 owner=USER（被抢占后的正常状态），phase=IDLE
    assert sm.phase is ProactivePhase.IDLE
    assert sm.owner is TurnOwner.USER

    await sm.reset()

    assert sm.phase is ProactivePhase.IDLE
    assert sm.owner is TurnOwner.NONE
    assert sm.proactive_sid is None
    assert sm.user_sid is None
    assert sm._preempted is False
    assert sm.can_start_proactive() is True


async def test_reset_is_noop_during_active_proactive_phase():
    """reset 在 PROACTIVE_ACTIVE 阶段必须 no-op：若 ``prepare_proactive_delivery``
    的 auto-start 子路径通过 ``start_session → end_session → _init_renew_status``
    间接触发 reset，不能把当前正在跑的 proactive 状态（phase/_preempted）一并吹掉，
    否则后续 phase1/phase2 的抢占检测会失效。
    """
    sm = _sm()
    await sm.fire(SessionEvent.PROACTIVE_START)
    await sm.fire(SessionEvent.PROACTIVE_CLAIM, sid="live_sid")
    await sm.fire(SessionEvent.USER_INPUT, sid="preempt_sid")
    assert sm._preempted is True

    await sm.reset()

    # 活动阶段 reset 不生效，phase/_preempted/sids 保持不变
    assert sm.phase is ProactivePhase.PHASE1
    assert sm._preempted is True
    assert sm.proactive_sid == "live_sid"
    assert sm.user_sid == "preempt_sid"


async def test_reset_preserves_subscribers():
    """reset 是 teardown hook，不该把应用层注册的订阅钩子吹掉。"""
    sm = _sm()
    hits: list[SessionEvent] = []

    def cb(event, payload):
        hits.append(event)

    sm.subscribe(SessionEvent.PROACTIVE_START, cb)
    await sm.fire(SessionEvent.PROACTIVE_START)
    await sm.reset()
    await sm.fire(SessionEvent.PROACTIVE_START)
    await asyncio.sleep(0)
    # 两次 START 都触发了订阅者
    assert hits == [SessionEvent.PROACTIVE_START, SessionEvent.PROACTIVE_START]


async def test_reset_preserves_last_user_activity():
    """reset 不该把 last_user_activity 吹回 0：跨 session 的活跃度追踪还要用。"""
    sm = _sm()
    await sm.fire(SessionEvent.USER_INPUT, sid="u")
    activity = sm.last_user_activity
    assert activity > 0
    await sm.reset()
    assert sm.last_user_activity == activity


async def test_snapshot_fields():
    sm = _sm()
    await sm.fire(SessionEvent.PROACTIVE_START)
    await sm.fire(SessionEvent.PROACTIVE_CLAIM, sid="sid_snap")
    snap = sm.snapshot()
    assert snap["lanlan_name"] == "Test"
    assert snap["owner"] == TurnOwner.PROACTIVE.value
    assert snap["phase"] == ProactivePhase.PHASE1.value
    assert snap["proactive_sid"] == "sid_snap"
    assert snap["preempted"] is False
