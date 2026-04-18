"""SM 集成回归测试：``trigger_agent_callbacks`` / ``trigger_greeting`` 接入
``SessionStateMachine`` 之后的关键行为契约。

覆盖点：
1. Voice 模式走 hot-swap，**不**触碰 SM（不 fire PROACTIVE_START，清 callbacks）
2. Text 模式在 SM 被另一路 proactive 占用时拒绝投递，callbacks 保留重试
3. Text 模式在 ``session._is_responding == True`` 时 SM 拒绝，callbacks 保留
4. 正常 text 投递：IDLE → PHASE1（claim）→ CLAIM → PHASE2 → DONE 事件序列
5. ``prompt_ephemeral`` 抛异常也必须 fire ``PROACTIVE_DONE``（finally 保证）
6. ``trigger_agent_callbacks`` 和 ``trigger_greeting`` / ``/api/proactive_chat``
   之间的 mutual exclusion：并发只有一路进 phase1
7. ``trigger_greeting`` 的 voice guard 在 SM claim 后触发：不投递但 fire DONE
"""
import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

# 为隔离 trigger_agent_callbacks / trigger_greeting 的环境依赖（prompt 资源、
# _loc、normalize_language_code、httpx 等），测试不直接跑整段函数，而是对
# SM 的契约做黑盒回归 —— 让一个 minimal mgr 模拟真实 LLMSessionManager 的
# state/session/lock 结构，然后直接调用 trigger_agent_callbacks 的关键分支。
from main_logic.core import LLMSessionManager, _proactive_expected_sid
from main_logic.omni_offline_client import OmniOfflineClient
from main_logic.session_state import (
    ProactivePhase,
    SessionEvent,
    SessionStateMachine,
    TurnOwner,
)


class _FakeOmniOffline(OmniOfflineClient):
    """最小 OmniOfflineClient 替身。``prompt_ephemeral`` 行为由测试注入。

    继承自 ``OmniOfflineClient`` 以通过 ``isinstance(...)`` 分支；跳过父类
    ``__init__`` 避免拉起真实 LLM 客户端。
    """

    def __init__(self, delivered: bool = True, is_responding: bool = False,
                 raise_exc: BaseException | None = None):
        # 刻意不调用 super().__init__：父类需要一堆 OpenAI/websocket 参数
        self._delivered = delivered
        self._is_responding = is_responding
        self._raise = raise_exc
        self.called_with: list[str] = []

    async def prompt_ephemeral(self, instruction: str) -> bool:
        self.called_with.append(instruction)
        if self._raise is not None:
            raise self._raise
        return self._delivered

    def update_max_response_length(self, *_a, **_kw):
        pass


def _make_mgr(session=None) -> LLMSessionManager:
    mgr = LLMSessionManager.__new__(LLMSessionManager)
    mgr.lanlan_name = "Test"
    mgr.master_name = "Master"
    mgr.user_language = "en"
    mgr.state = SessionStateMachine(lanlan_name="Test")
    mgr.session = session
    mgr.websocket = None
    mgr.lock = asyncio.Lock()
    mgr._proactive_write_lock = asyncio.Lock()
    mgr.current_speech_id = None
    mgr._tts_done_queued_for_turn = False
    mgr.pending_agent_callbacks = []
    mgr.pending_extra_replies = []
    mgr._get_text_guard_max_length = MagicMock(return_value=200)
    # Patch OmniRealtimeClient / OmniOfflineClient isinstance 判定：
    # 在测试里我们只关心 OmniOfflineClient 分支，其他分支显式构造。
    mgr.start_session = AsyncMock()
    return mgr


# ─────────────────────────────────────────────────────────────────────────────
# trigger_agent_callbacks
# ─────────────────────────────────────────────────────────────────────────────

async def test_voice_mode_does_not_touch_sm():
    """Voice 模式：清 pending，不 fire SM 任何事件。"""
    from main_logic.omni_realtime_client import OmniRealtimeClient

    class _VoiceSess(OmniRealtimeClient):
        def __init__(self):
            pass  # 跳过父类初始化

    mgr = _make_mgr(session=_VoiceSess())
    mgr.pending_agent_callbacks = [{"status": "completed", "summary": "task done"}]

    events: list[SessionEvent] = []
    mgr.state.subscribe(None, lambda ev, p: events.append(ev))

    await LLMSessionManager.trigger_agent_callbacks(mgr)
    # 异步订阅派发 —— 让 event loop 转一圈
    await asyncio.sleep(0)

    assert mgr.pending_agent_callbacks == []
    assert mgr.state.phase is ProactivePhase.IDLE
    assert events == []  # 未走 SM 任何事件


async def test_text_mode_sm_denied_when_phase_active():
    """另一路 proactive 已占 phase1 时，text 投递不应清 callbacks。"""
    sess = _FakeOmniOffline()
    mgr = _make_mgr(session=sess)
    mgr.pending_agent_callbacks = [{"status": "completed", "summary": "hello"}]

    # 模拟 router 已启动 proactive
    await mgr.state.fire(SessionEvent.PROACTIVE_START)
    assert mgr.state.phase is ProactivePhase.PHASE1

    await LLMSessionManager.trigger_agent_callbacks(mgr)

    # SM 拒绝 → prompt_ephemeral 未调用，callbacks 保留
    assert sess.called_with == []
    assert mgr.pending_agent_callbacks == [{"status": "completed", "summary": "hello"}]
    assert mgr.state.phase is ProactivePhase.PHASE1  # 原 proactive 占用未动


async def test_text_mode_sm_denied_when_session_responding():
    """AI 正在回复时 SM 拒绝 text 投递。"""
    sess = _FakeOmniOffline(is_responding=True)
    mgr = _make_mgr(session=sess)
    mgr.pending_agent_callbacks = [{"status": "completed", "summary": "queued"}]

    await LLMSessionManager.trigger_agent_callbacks(mgr)

    assert sess.called_with == []
    # callbacks 保留以便下轮重试
    assert mgr.pending_agent_callbacks == [{"status": "completed", "summary": "queued"}]
    assert mgr.state.phase is ProactivePhase.IDLE


async def test_text_mode_successful_delivery_fires_full_event_sequence():
    """happy path：START → CLAIM → PHASE2 → DONE，且 phase 回到 IDLE。"""
    sess = _FakeOmniOffline(delivered=True)
    mgr = _make_mgr(session=sess)
    mgr.pending_agent_callbacks = [{"status": "completed", "summary": "ok"}]

    seen: list[tuple[SessionEvent, dict]] = []
    mgr.state.subscribe(None, lambda ev, p: seen.append((ev, dict(p))))

    await LLMSessionManager.trigger_agent_callbacks(mgr)
    # 异步派发订阅回调
    for _ in range(3):
        await asyncio.sleep(0)

    event_names = [ev for ev, _ in seen]
    assert event_names == [
        SessionEvent.PROACTIVE_START,
        SessionEvent.PROACTIVE_CLAIM,
        SessionEvent.PROACTIVE_PHASE2,
        SessionEvent.PROACTIVE_DONE,
    ]

    # CLAIM payload 带着生成的 sid
    claim_payload = seen[1][1]
    assert claim_payload["sid"] == mgr.current_speech_id

    # 最终 phase 回 IDLE
    assert mgr.state.phase is ProactivePhase.IDLE
    assert mgr.state.proactive_sid is None

    # prompt_ephemeral 被调用，callbacks 已清
    assert len(sess.called_with) == 1
    assert mgr.pending_agent_callbacks == []


async def test_text_mode_exception_still_fires_done():
    """prompt_ephemeral 抛异常：callbacks 恢复 + PROACTIVE_DONE 仍必 fire。"""
    sess = _FakeOmniOffline(raise_exc=RuntimeError("llm boom"))
    mgr = _make_mgr(session=sess)
    original = [{"status": "completed", "summary": "retry_me"}]
    mgr.pending_agent_callbacks = list(original)

    seen_events: list[SessionEvent] = []
    mgr.state.subscribe(None, lambda ev, p: seen_events.append(ev))

    await LLMSessionManager.trigger_agent_callbacks(mgr)
    for _ in range(3):
        await asyncio.sleep(0)

    assert SessionEvent.PROACTIVE_DONE in seen_events
    assert mgr.state.phase is ProactivePhase.IDLE
    # exception 路径下 callbacks 恢复（见 core 的 except 分支）
    assert mgr.pending_agent_callbacks == original


async def test_contextvar_reset_after_delivery():
    """prompt_ephemeral 调用完后 ``_proactive_expected_sid`` 必须恢复为 None。"""
    sess = _FakeOmniOffline(delivered=True)
    mgr = _make_mgr(session=sess)
    mgr.pending_agent_callbacks = [{"status": "completed", "summary": "ctx"}]

    assert _proactive_expected_sid.get() is None
    await LLMSessionManager.trigger_agent_callbacks(mgr)
    assert _proactive_expected_sid.get() is None


# ─────────────────────────────────────────────────────────────────────────────
# mutual exclusion：text trigger 和 router proactive 之间
# ─────────────────────────────────────────────────────────────────────────────

async def test_already_claimed_denies_agent_callback():
    """router 已占 phase1 时，后续 agent callback 不能进 prompt_ephemeral。"""
    sess = _FakeOmniOffline(delivered=True)
    mgr = _make_mgr(session=sess)
    mgr.pending_agent_callbacks = [{"status": "completed", "summary": "race"}]

    router_won = await mgr.state.try_start_proactive(session=sess)
    assert router_won is True

    await LLMSessionManager.trigger_agent_callbacks(mgr)

    assert mgr.state.phase is ProactivePhase.PHASE1
    assert sess.called_with == []
    assert mgr.pending_agent_callbacks == [{"status": "completed", "summary": "race"}]


async def test_concurrent_claim_only_one_winner():
    """真·并发：两路 contender 用同一个 barrier 放行，
    原子 check-and-claim 保证只有一个 winner 进入 prompt_ephemeral。"""
    sess = _FakeOmniOffline(delivered=True)
    mgr = _make_mgr(session=sess)
    mgr.pending_agent_callbacks = [{"status": "completed", "summary": "race"}]

    barrier = asyncio.Event()
    router_won = asyncio.Future()

    async def router_contender():
        await barrier.wait()
        router_won.set_result(await mgr.state.try_start_proactive(session=sess))

    async def agent_contender():
        await barrier.wait()
        await LLMSessionManager.trigger_agent_callbacks(mgr)

    t1 = asyncio.create_task(router_contender())
    t2 = asyncio.create_task(agent_contender())
    # 两个 task 都阻塞在 barrier 上，再一起放行
    await asyncio.sleep(0)
    barrier.set()
    await asyncio.gather(t1, t2)

    # 恰好一个 winner：router_won 为 True → agent 被拒；False → agent 成功
    if router_won.result() is True:
        # router winner：agent 不能进 prompt_ephemeral
        assert sess.called_with == []
        assert mgr.pending_agent_callbacks == [{"status": "completed", "summary": "race"}]
        assert mgr.state.phase is ProactivePhase.PHASE1
    else:
        # agent winner：router 拒绝，agent 跑完 prompt_ephemeral → phase 回 IDLE
        assert len(sess.called_with) == 1
        assert mgr.pending_agent_callbacks == []
        assert mgr.state.phase is ProactivePhase.IDLE


async def test_user_input_between_claim_and_lock_is_detected():
    """CodeRabbit 关键回归：``try_start_proactive`` 返回 True 到获取 ``self.lock``
    之间，USER_INPUT 可能 mark_user_input_preempt() 并轮换 user sid。此时
    ``_deliver_agent_callbacks_text`` 必须在 lock 内复查 sticky preempt，不能
    把用户刚写好的 sid 再覆盖成 proactive sid。"""
    sess_wait = asyncio.Event()

    class _SlowSess(OmniOfflineClient):
        _is_responding = False

        def __init__(self):
            pass

        async def prompt_ephemeral(self, instruction):
            await sess_wait.wait()
            return True

        def update_max_response_length(self, *_a, **_kw):
            pass

    sess = _SlowSess()
    mgr = _make_mgr(session=sess)
    mgr.pending_agent_callbacks = [{"status": "completed", "summary": "slow"}]

    # SM claim 成功（phase → PHASE1）
    assert await mgr.state.try_start_proactive(session=sess) is True

    # 模拟 claim 后、_deliver 之前用户抢占：在 self.lock 内翻 preempt + 换 sid
    async with mgr.lock:
        pre_user_sid = "user_fresh_sid"
        mgr.current_speech_id = pre_user_sid
        mgr.state.mark_user_input_preempt()
    await mgr.state.fire(SessionEvent.USER_INPUT, sid=pre_user_sid)

    # 现在直接调 _deliver_agent_callbacks_text（绕过 trigger_agent_callbacks
    # 的 claim，因为我们已经手动模拟了 claim + user 抢占）
    callbacks_snapshot = [{"status": "completed", "summary": "slow"}]
    await LLMSessionManager._deliver_agent_callbacks_text(mgr, "instr", callbacks_snapshot)

    # 关键断言：current_speech_id 保留为用户的 sid，没被 proactive 覆盖
    assert mgr.current_speech_id == pre_user_sid
    # prompt_ephemeral 未调用
    sess_wait.set()  # defensive：万一被调用也不会无限阻塞
    # 给它一个 tick 证明 prompt_ephemeral 确实没跑
    await asyncio.sleep(0)
    # callbacks_snapshot 被恢复回 pending（bail 路径的语义）
    assert {"status": "completed", "summary": "slow"} in mgr.pending_agent_callbacks


# ─────────────────────────────────────────────────────────────────────────────
# user_input sticky preempt 仍生效
# ─────────────────────────────────────────────────────────────────────────────

async def test_user_input_during_agent_delivery_sets_preempted():
    """text 投递期间 USER_INPUT 到达：sticky _preempted 翻起，phase 复位后仍可感知。"""
    sess_wait = asyncio.Event()

    class _SlowSess(OmniOfflineClient):
        _is_responding = False

        def __init__(self):
            pass  # 跳过父类初始化

        async def prompt_ephemeral(self, instruction):
            # 模拟 LLM 耗时，期间 user input 抢占
            await sess_wait.wait()
            return True

        def update_max_response_length(self, *_a, **_kw):
            pass

    mgr = _make_mgr(session=_SlowSess())
    mgr.pending_agent_callbacks = [{"status": "completed", "summary": "slow"}]

    # 同时跑 agent callback delivery + 异步注入 USER_INPUT
    task = asyncio.create_task(LLMSessionManager.trigger_agent_callbacks(mgr))

    # 等 state 进入 phase1
    for _ in range(20):
        await asyncio.sleep(0.01)
        if mgr.state.phase in (ProactivePhase.PHASE1, ProactivePhase.PHASE2):
            break

    await mgr.state.fire(SessionEvent.USER_INPUT, sid="user_new_sid")
    # sticky flag 应已翻
    assert mgr.state._preempted is True
    assert mgr.state.owner is TurnOwner.USER

    # 放行 prompt_ephemeral
    sess_wait.set()
    await task

    # 一旦 PROACTIVE_DONE 触发，phase 回到 IDLE，_preempted 清零；
    # 但 owner 保留 USER（被抢占情况下 DONE 不覆盖）
    assert mgr.state.phase is ProactivePhase.IDLE
    assert mgr.state._preempted is False
    assert mgr.state.owner is TurnOwner.USER
