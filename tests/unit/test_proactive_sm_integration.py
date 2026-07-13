"""SM 集成回归测试：``trigger_agent_callbacks`` / ``trigger_greeting`` 接入
``SessionStateMachine`` 之后的关键行为契约。

覆盖点：
1. Voice 模式主路径是 realtime inject（conversation.item.create + response.create），
   **不**进 SM proactive 流水线（不 fire PROACTIVE_START）；只有 provider 抛
   NotImplementedError 才回退 hot-swap。serialize / reject 回补 / TOCTOU 等竞态见
   下面 voice_mode_* 用例。
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
import re
import sys
from unittest.mock import AsyncMock, MagicMock

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

# 为隔离 trigger_agent_callbacks / trigger_greeting 的环境依赖（prompt 资源、
# _loc、normalize_language_code、httpx 等），测试不直接跑整段函数，而是对
# SM 的契约做黑盒回归 —— 让一个 minimal mgr 模拟真实 LLMSessionManager 的
# state/session/lock 结构，然后直接调用 trigger_agent_callbacks 的关键分支。
import main_logic.core as core_module
from main_logic.omni_offline_client import OmniOfflineClient
from main_logic.proactive_delivery import DELIVERY_ACK_FUTURE_KEY, DELIVERY_RETRACTED_KEY
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

    async def prompt_ephemeral(self, instruction: str, *, images=None, on_committed=None) -> bool:
        self.called_with.append(instruction)
        if self._raise is not None:
            raise self._raise
        if self._delivered and on_committed:
            on_committed()
        return self._delivered

    def update_max_response_length(self, *_a, **_kw):
        pass


def _make_mgr(session=None) -> core_module.LLMSessionManager:
    mgr = core_module.LLMSessionManager.__new__(core_module.LLMSessionManager)
    mgr.lanlan_name = "Test"
    mgr.master_name = "Master"
    mgr.user_language = "en"
    mgr.state = SessionStateMachine(lanlan_name="Test")
    mgr.session = session
    mgr.websocket = None
    mgr.lock = asyncio.Lock()
    mgr._proactive_write_lock = asyncio.Lock()
    mgr._voice_proactive_inject_lock = asyncio.Lock()
    # Record _fire_task calls (e.g. the rejection-path re-trigger) and close
    # the coroutine so it doesn't run recursively / warn "never awaited".
    mgr._fired_tasks = []

    def _fake_fire(coro):
        mgr._fired_tasks.append(coro)
        # Close the coroutine so it doesn't run / warn "never awaited". Only
        # the cleanup-related errors are expected here; anything else should
        # surface rather than be silently swallowed.
        try:
            coro.close()
        except (RuntimeError, AttributeError):
            # Coroutine already closed/started or not a coroutine — there is
            # nothing to clean up; intentionally ignored in this test shim.
            pass
    mgr._fire_task = _fake_fire
    mgr.current_speech_id = None
    mgr._tts_done_queued_for_turn = False
    mgr.pending_agent_callbacks = []
    mgr.pending_extra_replies = []
    # Mirror the production __init__ playback-gate flag (the double is built
    # via __new__, so __init__ never ran). Default False = gate open.
    mgr._voice_playback_active = False
    # Mirror the voice-session predicate inputs (_is_voice_session_active_or_starting):
    # default = a non-active text session, i.e. voice gate open.
    mgr.is_active = False
    mgr.input_mode = 'text'
    mgr._starting_session_count = 0
    mgr._starting_input_mode = None
    mgr._takeover_active = False
    mgr._takeover_input_dispatcher = None
    mgr.goodbye_silent = False
    mgr.goodbye_silent_reason = ""
    mgr.goodbye_silent_updated_at = 0.0
    mgr.proactive_manager = MagicMock()
    mgr.proactive_manager.min_gap_s = 0.0
    mgr._get_text_guard_max_length = MagicMock(return_value=200)
    # Patch OmniRealtimeClient / OmniOfflineClient isinstance 判定：
    # 在测试里我们只关心 OmniOfflineClient 分支，其他分支显式构造。
    mgr.start_session = AsyncMock()
    return mgr


def test_enqueue_agent_callback_uses_generic_context_source_budget(monkeypatch):
    mgr = _make_mgr()
    monkeypatch.setitem(core_module._CONTEXT_APPEND_SOURCE_MAX_TOKENS, "topic.hook", 2)
    monkeypatch.setitem(core_module._CONTEXT_APPEND_SOURCE_MAX_TOKENS, "proactive.callback", 4)

    def fake_truncate(text, max_tokens, *args, **kwargs):
        return f"{max_tokens}:{text}"

    monkeypatch.setattr("utils.tokenize.truncate_to_tokens", fake_truncate)

    core_module.LLMSessionManager.enqueue_agent_callback(mgr, {
        "channel": "topic_hook",
        "status": "completed",
        "summary": "topic summary",
        "detail": "topic detail",
        "origin": "event",
        "source_kind": "topic",
        "source_name": "deep_topic_hook",
    })
    core_module.LLMSessionManager.enqueue_agent_callback(mgr, {
        "status": "completed",
        "summary": "proactive summary",
        "detail": "proactive detail",
        "origin": "event",
        "source_kind": "unknown",
        "source_name": "push",
    })

    assert mgr.pending_agent_callbacks[0]["summary"] == "2:topic summary"
    assert mgr.pending_agent_callbacks[0]["detail"] == "2:topic detail"
    assert mgr.pending_extra_replies[0]["context_source"] == "topic.hook"
    assert mgr.pending_agent_callbacks[1]["summary"] == "4:proactive summary"
    assert mgr.pending_agent_callbacks[1]["detail"] == "4:proactive detail"
    assert mgr.pending_extra_replies[1]["context_source"] == "proactive.callback"


def _make_voice_sess(*, is_responding=False, inject=None):
    """Build an ``OmniRealtimeClient`` test double via ``__new__`` (NOT a
    subclass).

    The code under test branches on ``isinstance(session, OmniRealtimeClient)``,
    so the fake must be a real instance — but the heavy ``__init__`` (real
    base_url / api_key / model / WebSocket plumbing) is irrelevant to these SM
    contract tests. ``__new__`` yields an instance without running ``__init__``;
    and because there's no ``__init__``-overriding subclass, CodeQL's
    "missing super().__init__" check has nothing to flag.

    Behaviour is attached as instance attributes (invoked WITHOUT ``self`` —
    instance-attribute callables aren't bound methods — which matches how the
    production code calls ``voice_sess.is_active_response()`` /
    ``voice_sess.inject_text_and_request_response(text, on_rejected=...)``):
      - ``is_active_response()`` → current ``_is_responding``.
      - ``inject_text_and_request_response`` → ``inject`` if given, else a
        default that bumps ``inject_calls`` and records ``injected``.
    Tests needing bespoke inject behaviour can reassign
    ``sess.inject_text_and_request_response`` after construction (the closure
    can capture ``sess``).
    """
    from main_logic.omni_realtime_client import OmniRealtimeClient

    sess = OmniRealtimeClient.__new__(OmniRealtimeClient)
    sess._is_responding = is_responding
    sess.injected = []
    sess.inject_calls = 0
    sess.is_active_response = lambda: sess._is_responding

    if inject is None:
        async def _default_inject(text, *, on_rejected=None):
            sess.inject_calls += 1
            sess.injected.append(text)
        sess.inject_text_and_request_response = _default_inject
    else:
        sess.inject_text_and_request_response = inject
    return sess


# ─────────────────────────────────────────────────────────────────────────────
# trigger_agent_callbacks
# ─────────────────────────────────────────────────────────────────────────────

async def test_voice_mode_idle_injects_and_drops_paired_cbs_and_extras():
    """Voice 模式（idle）：调 inject_text_and_request_response，并把 cb 从
    pending_agent_callbacks **和** 配对的 pending_extra_replies 同步剔除——
    后者必须清，否则 _finalize_turn_after_emit 看到 pending_extra_replies 非空
    会触发 _trigger_immediate_preparation_for_extra 的无谓 hot-swap 准备，并在
    下次 hot-swap 时 prime 出已经播过的内容造成重复投递。
    不 fire SM 任何事件（voice 走 realtime API 直接 inject，不进 SM 流水线）。"""
    sess = _make_voice_sess()
    mgr = _make_mgr(session=sess)
    cb = {"_callback_delivery_id": "id-task-done", "status": "completed", "summary": "task done"}
    extra = {"_callback_delivery_id": "id-task-done", "origin": "task_result", "summary": "task done", "status": "completed"}
    mgr.pending_agent_callbacks = [cb]
    mgr.pending_extra_replies = [extra]

    events: list[SessionEvent] = []
    mgr.state.subscribe(None, lambda ev, p: events.append(ev))

    await core_module.LLMSessionManager.trigger_agent_callbacks(mgr)
    await asyncio.sleep(0)

    assert len(sess.injected) == 1
    assert mgr.pending_agent_callbacks == []
    # 关键回归：matching extras 同步剔除
    assert mgr.pending_extra_replies == []
    assert mgr.state.phase is ProactivePhase.IDLE
    assert events == []


async def test_voice_mode_inject_preserves_passive_cb_and_its_extra():
    """Voice 模式：inject 只删 proactive cb 配对的 extras 项，
    passive cb 及其 extras 必须原封不动留下 —— 它们要走 user-turn drain。"""
    sess = _make_voice_sess()
    mgr = _make_mgr(session=sess)
    passive_cb = {
        "_callback_delivery_id": "id-passive",
        "status": "completed", "summary": "passive note", "delivery_mode": "passive",
    }
    proactive_cb = {
        "_callback_delivery_id": "id-proactive",
        "status": "completed", "summary": "ping user now",
    }
    passive_extra = {
        "_callback_delivery_id": "id-passive",
        "origin": "event", "summary": "passive note",
    }
    proactive_extra = {
        "_callback_delivery_id": "id-proactive",
        "origin": "task_result", "summary": "ping user now",
    }
    # enqueue_agent_callback stamps both queues with the same _callback_delivery_id
    mgr.pending_agent_callbacks = [passive_cb, proactive_cb]
    mgr.pending_extra_replies = [passive_extra, proactive_extra]

    await core_module.LLMSessionManager.trigger_agent_callbacks(mgr)
    await asyncio.sleep(0)

    assert len(sess.injected) == 1
    assert mgr.pending_agent_callbacks == [passive_cb]
    assert mgr.pending_extra_replies == [passive_extra]


async def test_voice_mode_busy_defers_cbs_for_retry():
    """Voice 模式（session 正在回复）：cb 留在队列等下次 response.done 后重试。"""
    sess = _make_voice_sess(is_responding=True)
    mgr = _make_mgr(session=sess)
    original = [{"status": "completed", "summary": "deferred"}]
    mgr.pending_agent_callbacks = list(original)

    await core_module.LLMSessionManager.trigger_agent_callbacks(mgr)
    await asyncio.sleep(0)

    assert sess.injected == []  # 没 inject
    assert mgr.pending_agent_callbacks == original  # cb 保留
    assert mgr.state.phase is ProactivePhase.IDLE


async def test_voice_mode_drop_uses_id_match_not_length_alignment():
    """Voice 模式：成功 inject 后按 ``_callback_delivery_id`` 精确剔除两队列里
    匹配的项 —— 即使 ``drain_agent_callbacks_for_llm`` 先前清空了
    pending_agent_callbacks 把两队列长度搞错位，extras 里 stale 项也必须被清。
    锁死 CodeRabbit r3248967092：长度相等 != 队列对齐这条不变式。"""
    sess = _make_voice_sess()
    mgr = _make_mgr(session=sess)
    # 模拟"队列错位"：旧的两条 cb 因为 user turn 走了 drain_agent_callbacks_for_llm
    # 清空 pending_agent_callbacks，但 pending_extra_replies 仍然保留它们；
    # 然后一条新的 proactive cb 进来 —— pac=1, extras=3。
    stale_extra_a = {"_callback_delivery_id": "stale-A", "origin": "task_result", "summary": "old A"}
    stale_extra_b = {"_callback_delivery_id": "stale-B", "origin": "task_result", "summary": "old B"}
    new_cb = {"_callback_delivery_id": "new", "status": "completed", "summary": "fresh"}
    new_extra = {"_callback_delivery_id": "new", "origin": "task_result", "summary": "fresh"}
    mgr.pending_agent_callbacks = [new_cb]
    mgr.pending_extra_replies = [stale_extra_a, stale_extra_b, new_extra]

    await core_module.LLMSessionManager.trigger_agent_callbacks(mgr)
    await asyncio.sleep(0)

    assert len(sess.injected) == 1
    assert mgr.pending_agent_callbacks == []
    # 关键：new_extra 必须被清，stale 的两条不归本次 inject 管，保留给 hot-swap
    assert mgr.pending_extra_replies == [stale_extra_a, stale_extra_b]


async def test_voice_mode_server_rejection_re_enqueues_cb():
    """Voice 模式：``response.create`` 被 server 拒（``response_already_active``
    等 VAD 抢跑场景）通过 error 事件异步回来，``inject_text_and_request_response``
    本身已经 return 了。我们注册的 ``on_rejected`` 回调必须把那条已乐观剔除
    的 cb 重新塞回 ``pending_agent_callbacks``，让 ``_finalize_turn_after_emit``
    在下一次 response.done 后的 retry 把它捡起来。锁死 Codex r3249012424。"""
    captured_rejection: list = []

    sess = _make_voice_sess()

    async def _inject(text, *, on_rejected=None):
        sess.inject_calls += 1
        sess.injected.append(text)
        # 不在这里 fire；测试模拟 inject 已返回，但 cb 尚未在 server 端被处理
        captured_rejection.append(on_rejected)
    sess.inject_text_and_request_response = _inject

    mgr = _make_mgr(session=sess)
    cb = {"_callback_delivery_id": "id-race", "status": "completed", "summary": "race-cb"}
    extra = {"_callback_delivery_id": "id-race", "origin": "task_result", "summary": "race-cb"}
    mgr.pending_agent_callbacks = [cb]
    mgr.pending_extra_replies = [extra]

    await core_module.LLMSessionManager.trigger_agent_callbacks(mgr)
    await asyncio.sleep(0)

    # 乐观剔除：两条队列都空
    assert mgr.pending_agent_callbacks == []
    assert mgr.pending_extra_replies == []
    assert len(captured_rejection) == 1
    assert captured_rejection[0] is not None

    # 模拟 server 异步抛回 response_already_active
    captured_rejection[0]("response_already_active")

    # cb 被 on_rejected 回到 pending_agent_callbacks，等下次 trigger 重投
    assert len(mgr.pending_agent_callbacks) == 1
    assert mgr.pending_agent_callbacks[0]["_callback_delivery_id"] == "id-race"
    # 配对的 extras 也必须被回补（否则后续 hot-swap fallback 会丢补报内容）
    assert len(mgr.pending_extra_replies) == 1
    assert mgr.pending_extra_replies[0]["_callback_delivery_id"] == "id-race"
    # handler 不立即 re-fire trigger（Codex P1）：response_already_active 时
    # is_active_response() 可能读到 stale False，立即 re-fire 会 re-inject→
    # re-reject 死循环。retry 交给那个 active response 的 response.done →
    # _finalize_turn_after_emit。所以这里不应有立即调度。
    assert mgr._fired_tasks == []


async def test_voice_mode_late_rejection_keeps_delivery_ack_pending():
    captured_rejection: list = []

    sess = _make_voice_sess()

    async def _inject(text, *, on_rejected=None):
        sess.inject_calls += 1
        sess.injected.append(text)
        captured_rejection.append(on_rejected)
    sess.inject_text_and_request_response = _inject

    mgr = _make_mgr(session=sess)
    future = asyncio.get_running_loop().create_future()
    cb = {
        "_callback_delivery_id": "id-ack-race",
        "status": "completed",
        "summary": "ack race",
        DELIVERY_ACK_FUTURE_KEY: future,
    }
    extra = {"_callback_delivery_id": "id-ack-race", "origin": "task_result", "summary": "ack race"}
    mgr.pending_agent_callbacks = [cb]
    mgr.pending_extra_replies = [extra]

    await core_module.LLMSessionManager.trigger_agent_callbacks(mgr)
    assert not future.done()

    captured_rejection[0]("response_already_active")
    await asyncio.sleep(core_module._VOICE_PROACTIVE_ACK_GRACE_S + 0.02)

    assert not future.done()
    assert mgr.pending_agent_callbacks == [cb]
    assert mgr.pending_extra_replies == [extra]


async def test_voice_mode_success_resolves_delivery_ack_after_rejection_window():
    sess = _make_voice_sess()
    mgr = _make_mgr(session=sess)
    future = asyncio.get_running_loop().create_future()
    cb = {
        "_callback_delivery_id": "id-ack-ok",
        "status": "completed",
        "summary": "ack ok",
        DELIVERY_ACK_FUTURE_KEY: future,
    }
    extra = {"_callback_delivery_id": "id-ack-ok", "origin": "task_result", "summary": "ack ok"}
    mgr.pending_agent_callbacks = [cb]
    mgr.pending_extra_replies = [extra]

    await core_module.LLMSessionManager.trigger_agent_callbacks(mgr)
    assert not future.done()

    await asyncio.sleep(core_module._VOICE_PROACTIVE_ACK_GRACE_S + 0.02)

    assert future.done()
    assert future.result() is True


async def test_voice_mode_rechecks_retracted_callbacks_before_inject():
    sess = _make_voice_sess()
    mgr = _make_mgr(session=sess)
    cb = {"_callback_delivery_id": "id-retracted", "status": "completed", "summary": "cancelled"}
    mgr.pending_agent_callbacks = [cb]
    mgr.pending_extra_replies = [
        {"_callback_delivery_id": "id-retracted", "origin": "task_result", "summary": "cancelled"}
    ]

    async def _stream_then_retract(callbacks, session):
        cb[DELIVERY_RETRACTED_KEY] = True
        return True
    mgr._stream_cb_media = _stream_then_retract

    delivered = await core_module.LLMSessionManager.trigger_agent_callbacks(mgr)

    assert delivered is False
    assert sess.injected == []
    assert mgr.pending_agent_callbacks == []
    assert mgr.pending_extra_replies == []


async def test_text_mode_acks_only_callbacks_that_reach_prompt():
    sess = _FakeOmniOffline(delivered=True)
    mgr = _make_mgr(session=sess)
    dropped_future = asyncio.get_running_loop().create_future()
    active_future = asyncio.get_running_loop().create_future()
    dropped_cb = {
        "_callback_delivery_id": "id-dropped",
        "status": "completed",
        "summary": "cancelled",
        DELIVERY_ACK_FUTURE_KEY: dropped_future,
    }
    active_cb = {
        "_callback_delivery_id": "id-active",
        "status": "completed",
        "summary": "shown",
        DELIVERY_ACK_FUTURE_KEY: active_future,
    }
    mgr.pending_agent_callbacks = [dropped_cb, active_cb]

    async def _deliver(callbacks_snapshot):
        dropped_cb[DELIVERY_RETRACTED_KEY] = True
        callbacks_snapshot[:] = [active_cb]
        return True
    mgr._deliver_agent_callbacks_text = _deliver

    delivered = await core_module.LLMSessionManager.trigger_agent_callbacks(mgr)

    assert delivered is True
    assert not dropped_future.done()
    assert active_future.done()
    assert active_future.result() is True


async def test_text_mode_resolves_delivery_ack_after_committed_output_before_completion_flush():
    class _FlushCancellingSess(_FakeOmniOffline):
        async def prompt_ephemeral(self, instruction: str, *, images=None, on_committed=None) -> bool:
            self.called_with.append(instruction)
            assert not future.done()
            assert on_committed is not None
            on_committed()
            assert future.done()
            assert future.result() is True
            return True

    sess = _FlushCancellingSess(delivered=True)
    mgr = _make_mgr(session=sess)
    future = asyncio.get_running_loop().create_future()
    cb = {
        "_callback_delivery_id": "id-text-ack-before-flush",
        "status": "completed",
        "summary": "shown before flush",
        DELIVERY_ACK_FUTURE_KEY: future,
    }
    mgr.pending_agent_callbacks = [cb]

    delivered = await core_module.LLMSessionManager.trigger_agent_callbacks(mgr)

    assert delivered is True
    assert future.done()
    assert future.result() is True


async def test_text_mode_resolves_delivery_ack_false_when_prompt_has_no_committed_output():
    sess = _FakeOmniOffline(delivered=False)
    mgr = _make_mgr(session=sess)
    future = asyncio.get_running_loop().create_future()
    cb = {
        "_callback_delivery_id": "id-text-no-output",
        "status": "completed",
        "summary": "no visible output",
        DELIVERY_ACK_FUTURE_KEY: future,
    }
    mgr.pending_agent_callbacks = [cb]

    delivered = await core_module.LLMSessionManager.trigger_agent_callbacks(mgr)

    assert delivered is False
    assert future.done()
    assert future.result() is False
    assert mgr.pending_agent_callbacks == [cb]


async def test_text_mode_requeues_callbacks_when_no_session_or_websocket_after_claim():
    mgr = _make_mgr(session=None)
    cb = {"_callback_delivery_id": "id-no-session", "status": "completed", "summary": "keep queued"}
    mgr.pending_agent_callbacks = [cb]

    delivered = await core_module.LLMSessionManager.trigger_agent_callbacks(mgr)

    assert delivered is False
    assert mgr.pending_agent_callbacks == [cb]
    assert mgr.state.phase is ProactivePhase.IDLE


async def test_text_mode_retraction_after_claim_purges_paired_extra():
    cb = {"_callback_delivery_id": "id-retracted-text", "status": "completed", "summary": "cancelled"}
    extra = {"_callback_delivery_id": "id-retracted-text", "origin": "task_result", "summary": "cancelled"}

    class _RetractingSess(_FakeOmniOffline):
        def update_max_response_length(self, *_a, **_kw):
            cb[DELIVERY_RETRACTED_KEY] = True

    sess = _RetractingSess(delivered=True)
    mgr = _make_mgr(session=sess)
    mgr.pending_agent_callbacks = [cb]
    mgr.pending_extra_replies = [extra]

    delivered = await core_module.LLMSessionManager.trigger_agent_callbacks(mgr)

    assert delivered is False
    assert sess.called_with == []
    assert mgr.pending_agent_callbacks == []
    assert mgr.pending_extra_replies == []


async def test_text_mode_committed_then_flush_exception_does_not_requeue_callback():
    class _CommittedThenFailSess(_FakeOmniOffline):
        async def prompt_ephemeral(self, instruction: str, *, images=None, on_committed=None) -> bool:
            self.called_with.append(instruction)
            assert on_committed is not None
            on_committed()
            raise RuntimeError("completion flush failed after committed output")

    sess = _CommittedThenFailSess(delivered=True)
    mgr = _make_mgr(session=sess)
    future = asyncio.get_running_loop().create_future()
    cb = {
        "_callback_delivery_id": "id-committed-then-fail",
        "status": "completed",
        "summary": "already shown",
        DELIVERY_ACK_FUTURE_KEY: future,
    }
    mgr.pending_agent_callbacks = [cb]

    delivered = await core_module.LLMSessionManager.trigger_agent_callbacks(mgr)

    assert delivered is True
    assert future.done()
    assert future.result() is True
    assert mgr.pending_agent_callbacks == []
    assert mgr.state.phase is ProactivePhase.IDLE


async def test_text_mode_success_keeps_late_extra_replies():
    class _QueueingSess(_FakeOmniOffline):
        async def prompt_ephemeral(self, instruction: str, *, images=None, on_committed=None) -> bool:
            self.called_with.append(instruction)
            if on_committed:
                on_committed()
            mgr.pending_agent_callbacks.append(late_cb)
            mgr.pending_extra_replies.append(late_extra)
            return True

    sess = _QueueingSess(delivered=True)
    mgr = _make_mgr(session=sess)
    initial_cb = {"_callback_delivery_id": "id-initial", "status": "completed", "summary": "initial"}
    initial_extra = {"_callback_delivery_id": "id-initial", "origin": "task_result", "summary": "initial"}
    late_cb = {"_callback_delivery_id": "id-late", "status": "completed", "summary": "late"}
    late_extra = {"_callback_delivery_id": "id-late", "origin": "task_result", "summary": "late"}
    mgr.pending_agent_callbacks = [initial_cb]
    mgr.pending_extra_replies = [initial_extra]

    delivered = await core_module.LLMSessionManager.trigger_agent_callbacks(mgr)

    assert delivered is True
    assert mgr.pending_agent_callbacks == [late_cb]
    assert mgr.pending_extra_replies == [late_extra]


def test_drain_agent_callbacks_purges_retracted_callbacks_and_extras():
    mgr = _make_mgr(session=_FakeOmniOffline(delivered=True))
    retracted_cb = {
        "_callback_delivery_id": "id-retracted-drain",
        "status": "completed",
        "summary": "cancelled",
        DELIVERY_RETRACTED_KEY: True,
    }
    active_cb = {"_callback_delivery_id": "id-active-drain", "status": "completed", "summary": "shown"}
    retracted_extra = {
        "_callback_delivery_id": "id-retracted-drain",
        "origin": "task_result",
        "summary": "cancelled",
    }
    active_extra = {"_callback_delivery_id": "id-active-drain", "origin": "task_result", "summary": "shown"}
    mgr.pending_agent_callbacks = [retracted_cb, active_cb]
    mgr.pending_extra_replies = [retracted_extra, active_extra]

    rendered = core_module.LLMSessionManager.drain_agent_callbacks_for_llm(mgr)

    assert "shown" in rendered
    assert "cancelled" not in rendered
    assert mgr.pending_agent_callbacks == []
    assert mgr.pending_extra_replies == [active_extra]


async def test_drain_agent_callbacks_resolves_delivery_ack():
    mgr = _make_mgr(session=_FakeOmniOffline(delivered=True))
    future = asyncio.get_running_loop().create_future()
    cb = {
        "_callback_delivery_id": "id-drain-ack",
        "status": "completed",
        "summary": "shown",
        DELIVERY_ACK_FUTURE_KEY: future,
    }
    mgr.pending_agent_callbacks = [cb]

    rendered = core_module.LLMSessionManager.drain_agent_callbacks_for_llm(mgr)

    assert "shown" in rendered
    assert future.done()
    assert future.result() is True
    assert mgr.pending_agent_callbacks == []


async def test_drain_agent_callbacks_rechecks_topic_release_gate():
    mgr = _make_mgr(session=_FakeOmniOffline(delivered=True))
    mgr.topic_hook_delivery_allowed = lambda: True
    topic_future = asyncio.get_running_loop().create_future()
    normal_future = asyncio.get_running_loop().create_future()
    topic_cb = {
        "_callback_delivery_id": "id-topic-drain",
        "channel": "topic_hook",
        "source_kind": "topic",
        "status": "completed",
        "summary": "stale deep topic",
        "_topic_release_available": lambda: False,
        DELIVERY_ACK_FUTURE_KEY: topic_future,
    }
    normal_cb = {
        "_callback_delivery_id": "id-normal-drain",
        "status": "completed",
        "summary": "regular callback",
        DELIVERY_ACK_FUTURE_KEY: normal_future,
    }
    topic_extra = {
        "_callback_delivery_id": "id-topic-drain",
        "origin": "event",
        "summary": "stale deep topic",
    }
    normal_extra = {
        "_callback_delivery_id": "id-normal-drain",
        "origin": "task_result",
        "summary": "regular callback",
    }
    mgr.pending_agent_callbacks = [topic_cb, normal_cb]
    mgr.pending_extra_replies = [topic_extra, normal_extra]

    rendered = core_module.LLMSessionManager.drain_agent_callbacks_for_llm(mgr)

    assert "regular callback" in rendered
    assert "stale deep topic" not in rendered
    assert topic_future.done()
    assert topic_future.result() is False
    assert normal_future.done()
    assert normal_future.result() is True
    assert mgr.pending_agent_callbacks == []
    assert mgr.pending_extra_replies == [normal_extra]


async def test_voice_mode_reject_during_await_not_pruned():
    """TOCTOU 回归（Codex P1）：error 事件可能在 inject 仍 await 期间由
    handle_messages 派发 on_rejected——此时 cb 还在队列里（乐观 prune 还没跑）。
    旧逻辑 handler 按"已存在"跳过 re-add，随后 success 路径又按 delivered_ids
    把它 prune 掉 → 静默丢失。修法：handler 置 _rejected 标志，await 返回后
    若 rejected 则跳过 prune，cb 留在队列等重试。"""
    sess = _make_voice_sess()  # is_active_response()→_is_responding，init False

    async def _inject(text, *, on_rejected=None):
        sess.inject_calls += 1
        # 模拟 VAD 抢跑：reject 到达时 server 已经有 active response（busy），
        # 且发生在 await 期间（prune 之前）。
        sess._is_responding = True
        if on_rejected is not None:
            on_rejected("response_already_active")
        # inject 本身正常返回（拒绝是异步事件，不是异常）
    sess.inject_text_and_request_response = _inject

    mgr = _make_mgr(session=sess)
    cb = {"_callback_delivery_id": "id-toctou", "status": "completed", "summary": "keep me"}
    extra = {"_callback_delivery_id": "id-toctou", "origin": "task_result", "summary": "keep me"}
    mgr.pending_agent_callbacks = [cb]
    mgr.pending_extra_replies = [extra]

    await core_module.LLMSessionManager.trigger_agent_callbacks(mgr)
    await asyncio.sleep(0)

    # 关键：cb 没被 prune 掉，两队列都还在，等下次 retry
    assert sess.inject_calls == 1
    assert len(mgr.pending_agent_callbacks) == 1
    assert mgr.pending_agent_callbacks[0]["_callback_delivery_id"] == "id-toctou"
    assert len(mgr.pending_extra_replies) == 1
    assert mgr.pending_extra_replies[0]["_callback_delivery_id"] == "id-toctou"
    # busy（is_active_response True）时 handler 不应立即 re-fire —— 留给
    # response.done 后的 _finalize_turn_after_emit 重试，避免 response_already_active 死循环。
    assert mgr._fired_tasks == []


async def test_voice_mode_concurrent_triggers_inject_once():
    """并发回归（Codex P1）：两个 trigger_agent_callbacks task 同时进 voice
    分支，_voice_proactive_inject_lock 必须把 check-and-claim 串起来——只有
    一个真正 inject，另一个拿到锁后重新过滤发现队列已空、不再重复 inject。"""
    release = asyncio.Event()
    entered_inject = asyncio.Event()

    sess = _make_voice_sess()

    async def _inject(text, *, on_rejected=None):
        sess.inject_calls += 1
        # 标记第一个 inject 真正进入临界区（持锁中），再卡住，给第二个 task
        # 确定性地去抢锁——不靠固定 tick 数赌时序。
        entered_inject.set()
        await release.wait()
    sess.inject_text_and_request_response = _inject
    mgr = _make_mgr(session=sess)
    cb = {"_callback_delivery_id": "id-concurrent", "status": "completed", "summary": "once"}
    extra = {"_callback_delivery_id": "id-concurrent", "origin": "task_result", "summary": "once"}
    mgr.pending_agent_callbacks = [cb]
    mgr.pending_extra_replies = [extra]

    t1 = asyncio.create_task(core_module.LLMSessionManager.trigger_agent_callbacks(mgr))
    t2 = asyncio.create_task(core_module.LLMSessionManager.trigger_agent_callbacks(mgr))
    # 等第一个 inject 真的进入持锁段，再给第二个 task 一个调度点去阻塞在锁上，
    # 然后才放行——确保「两个 task 竞争同一 snapshot」这个场景真的发生。
    await asyncio.wait_for(entered_inject.wait(), timeout=5)
    await asyncio.sleep(0)
    release.set()
    # 本地超时：若 _voice_proactive_inject_lock 以后回归成死等，这里快速失败
    # 而不是挂到 CI 全局超时。
    await asyncio.wait_for(asyncio.gather(t1, t2), timeout=5)

    # 关键：只 inject 一次，没有重复播报
    assert sess.inject_calls == 1
    assert mgr.pending_agent_callbacks == []
    assert mgr.pending_extra_replies == []


async def test_voice_mode_not_implemented_falls_back_to_hot_swap():
    """Voice 模式 defensive fallback：若某 provider 抛 NotImplementedError，
    drop proactive cb，走现有 hot-swap 路径（pending_extra_replies 保留供下一
    hot-swap 注入）。注：现役 provider 全部支持 manual inject（含 Qwen 走
    conversation.item.create、Gemini 走 send_client_content），此分支实际已
    unreachable，仅为未来 provider 兜底——用一个显式抛 NotImplementedError 的
    假 session 验证兜底逻辑仍正确。"""
    sess = _make_voice_sess()

    async def _inject(text, *, on_rejected=None):
        raise NotImplementedError("test provider: no manual inject")
    sess.inject_text_and_request_response = _inject

    mgr = _make_mgr(session=sess)
    mgr.pending_agent_callbacks = [{"status": "completed", "summary": "hot-swap fallback"}]
    original_extras = [{"summary": "hot-swap fallback"}]
    mgr.pending_extra_replies = list(original_extras)

    await core_module.LLMSessionManager.trigger_agent_callbacks(mgr)
    await asyncio.sleep(0)

    assert mgr.pending_agent_callbacks == []  # proactive cb dropped
    # 精确相等而非 truthy：锁死 fallback 不会误改/重复 pending_extra_replies
    assert mgr.pending_extra_replies == original_extras  # hot-swap channel preserved


async def test_inject_gemini_routes_through_send_client_content():
    """对偶性回归：inject_text_and_request_response 对 Gemini 也支持（走
    send_client_content(turn_complete=True)），与 create_response →
    _create_response_gemini 对称，不再抛 NotImplementedError。直接调真实
    OmniRealtimeClient.inject_text_and_request_response（绕过 SM），验证它
    把文本作为 user turn 注入并 turn_complete=True 触发响应。"""
    from main_logic.omni_realtime_client import OmniRealtimeClient

    sent = {}

    class _FakeGeminiSession:
        async def send_client_content(self, *, turns, turn_complete):
            sent["turns"] = turns
            sent["turn_complete"] = turn_complete

    sess = OmniRealtimeClient.__new__(OmniRealtimeClient)
    sess._fatal_error_occurred = False
    sess._is_gemini = True
    sess._gemini_session = _FakeGeminiSession()

    # google.genai 在测试环境不一定装了 —— 缺则跳过（CI 装了 SDK 会跑）。
    # 用 importorskip 而非 try/except Exception：只在 ImportError 时 skip，
    # SDK 真实运行时错误仍会冒出来，不会被误吞成 skip 掩盖回归。
    import pytest
    pytest.importorskip("google.genai")

    await OmniRealtimeClient.inject_text_and_request_response(sess, "（系统通知）任务完成了。")

    assert sent.get("turn_complete") is True
    assert sent.get("turns") is not None


async def test_inject_gemini_missing_session_raises():
    """Gemini session 不可用时 inject 必须 raise（让 caller 保留 cb），
    不能静默成功。"""
    from main_logic.omni_realtime_client import OmniRealtimeClient

    sess = OmniRealtimeClient.__new__(OmniRealtimeClient)
    sess._fatal_error_occurred = False
    sess._is_gemini = True
    sess._gemini_session = None

    import pytest
    with pytest.raises(RuntimeError):
        await OmniRealtimeClient.inject_text_and_request_response(sess, "x")


async def test_sweep_inject_rejection_handlers_clears_dict():
    """``response.done`` lifecycle sweep 清空 inject rejection handler 字典
    （取代固定 3s TTL 作为主清理）。锁死 Codex P2：late reject 不该因 TTL 过期
    丢失——主清理改成 response.done 触发的 sweep，TTL 只是 hang 兜底。"""
    from main_logic.omni_realtime_client import OmniRealtimeClient

    sess = OmniRealtimeClient.__new__(OmniRealtimeClient)
    sess._inject_rejection_handlers = {
        "event_inject_item_x": lambda msg: None,
        "event_inject_resp_x": lambda msg: None,
    }
    sess._sweep_inject_rejection_handlers()
    assert sess._inject_rejection_handlers == {}
    # 空字典再 sweep 不报错（idempotent）
    sess._sweep_inject_rejection_handlers()
    assert sess._inject_rejection_handlers == {}


async def test_route_inject_rejection_id_match():
    """精确路径：error 携带我们 stamp 的 client event_id → 命中并 fire 对应 handler。"""
    from main_logic.omni_realtime_client import OmniRealtimeClient

    fired = []
    sess = OmniRealtimeClient.__new__(OmniRealtimeClient)
    sess._inject_rejection_handlers = {"event_inject_resp_x": lambda msg: fired.append(msg)}
    sess._route_inject_rejection("event_inject_resp_x", "response_already_active")
    assert fired == ["response_already_active"]
    assert sess._inject_rejection_handlers == {}  # popped


async def test_route_inject_rejection_content_fallback_no_id():
    """fallback 路径（Codex P1）：provider 拒绝 response.create 但 error 不带
    client event_id。proactive inject 正等待 outcome（flag True）且内容像
    response-conflict 时 fire 所有 pending handler，避免静默丢失。"""
    from main_logic.omni_realtime_client import OmniRealtimeClient

    fired = []
    sess = OmniRealtimeClient.__new__(OmniRealtimeClient)
    sess._inject_rejection_handlers = {"k1": lambda msg: fired.append(msg)}
    sess._proactive_inject_awaiting_outcome = True  # inject 刚发出，正等 outcome
    # err_event_id 缺失，但消息是 response-conflict
    sess._route_inject_rejection(None, "Conversation already has an active response")
    assert len(fired) == 1
    assert sess._inject_rejection_handlers == {}
    assert sess._proactive_inject_awaiting_outcome is False  # 窗口已消费


async def test_route_inject_rejection_no_id_but_not_awaiting_does_not_fire():
    """CodeRabbit Major：无 id 的 response-conflict，但当前没有 proactive inject
    在等 outcome（flag False，例如 handler 是上一次成功 inject 的残留，或这条
    冲突来自 create_response / tool-result / signal_user_activity_end 等别的
    response.create 发送方）→ 绝不能 fire，否则把已接受的 cb 误回补造成重复。"""
    from main_logic.omni_realtime_client import OmniRealtimeClient

    fired = []
    sess = OmniRealtimeClient.__new__(OmniRealtimeClient)
    sess._inject_rejection_handlers = {"k1": lambda msg: fired.append(msg)}
    sess._proactive_inject_awaiting_outcome = False  # 没有 inject 在等
    sess._route_inject_rejection(None, "response_already_active")
    assert fired == []
    assert "k1" in sess._inject_rejection_handlers  # 未动


async def test_route_inject_rejection_nonmatching_id_does_not_fire_fallback():
    """Codex P1：error 带了 client event_id 但不匹配我们任何 pending handler
    → 说明这是别的 response.create（create_response / tool-result 续传 /
    signal_user_activity_end，都被 send_event setdefault 打了时间戳 id）的拒绝，
    不是我们的 inject。即使消息文本像 response_already_active、即使有 inject 在
    等 outcome，也**不能** fire（id present 只精确匹配），否则把模型其实已接受的
    cb 误回补造成重复播报。content fallback 只在完全没有 client id 时才走。"""
    from main_logic.omni_realtime_client import OmniRealtimeClient

    fired = []
    sess = OmniRealtimeClient.__new__(OmniRealtimeClient)
    sess._inject_rejection_handlers = {"event_inject_resp_ours": lambda msg: fired.append(msg)}
    sess._proactive_inject_awaiting_outcome = True  # 即便在等，也不能被别人的 id 触发
    # 别的请求的 event_id + response-conflict 文本
    sess._route_inject_rejection("event_create_response_other", "response_already_active")
    assert fired == []
    assert "event_inject_resp_ours" in sess._inject_rejection_handlers  # 未动


async def test_route_inject_rejection_unrelated_error_does_not_fire():
    """无 id 匹配 + 错误不像 response-conflict（如 503/quota）→ 不应 fire，
    避免把无关错误误当成 inject 拒绝、错误回补造成重复投递。"""
    from main_logic.omni_realtime_client import OmniRealtimeClient

    fired = []
    sess = OmniRealtimeClient.__new__(OmniRealtimeClient)
    sess._inject_rejection_handlers = {"k1": lambda msg: fired.append(msg)}
    sess._proactive_inject_awaiting_outcome = True
    sess._route_inject_rejection(None, "503 service overloaded, try again later")
    assert fired == []
    assert sess._inject_rejection_handlers == {"k1": sess._inject_rejection_handlers["k1"]}


async def test_voice_mode_inject_exception_keeps_cbs_for_retry():
    """Voice 模式（inject 抛非 NotImplementedError）：cb 留在队列等重试。"""
    sess = _make_voice_sess()

    async def _inject(text, *, on_rejected=None):
        sess.inject_calls += 1
        raise RuntimeError("ws boom")
    sess.inject_text_and_request_response = _inject

    mgr = _make_mgr(session=sess)
    original = [{"status": "completed", "summary": "retry on ws err"}]
    mgr.pending_agent_callbacks = list(original)

    await core_module.LLMSessionManager.trigger_agent_callbacks(mgr)
    await asyncio.sleep(0)

    # inject 确实被调用过一次（证明走的是 inject-异常分支，而非更早的 guard 早退）
    assert sess.inject_calls == 1
    assert mgr.pending_agent_callbacks == original


async def test_voice_mode_unstamped_cb_still_pruned_via_object_id_fallback():
    """Defense in depth：production 路径都过 ``enqueue_agent_callback`` 标
    ``_callback_delivery_id``，但 voice 成功 inject 的 pac 清理还有一条
    object ``id()`` 兜底，确保任何未来直接 append 没标 id 的 cb 也不会被
    后续 retry 重复投递。锁死 Codex r3249183511。"""
    sess = _make_voice_sess()
    mgr = _make_mgr(session=sess)
    # 故意构造没有 _callback_delivery_id 的 cb（模拟绕过 enqueue_agent_callback 的入口）
    unstamped_cb = {"status": "completed", "summary": "unstamped"}
    mgr.pending_agent_callbacks = [unstamped_cb]
    # 这里 extras 用空，因为没走 enqueue → 没配对 entries 是合理状态
    mgr.pending_extra_replies = []

    await core_module.LLMSessionManager.trigger_agent_callbacks(mgr)
    await asyncio.sleep(0)

    assert len(sess.injected) == 1
    # 关键：unstamped cb 也被通过 id() 兜底剔除，下次 retry 不会重投
    assert mgr.pending_agent_callbacks == []


async def test_send_event_preserves_caller_stamped_event_id():
    """``send_event`` 必须保留 caller 显式标的 ``event_id``，不能拿时间戳覆盖。
    这是 ``inject_text_and_request_response`` 的 ``on_rejected`` 路径能工作的
    前提：server ``error.event_id`` 必须能 echo 回 caller 注册的 id 才会命中
    ``_inject_rejection_handlers``。锁死 Codex r3249069126。"""
    from main_logic.omni_realtime_client import OmniRealtimeClient

    class _CapturingWS:
        def __init__(self):
            self.sent: list[str] = []

        async def send(self, payload: str) -> None:
            self.sent.append(payload)

    sess = OmniRealtimeClient.__new__(OmniRealtimeClient)
    sess._fatal_error_occurred = False
    sess._is_throttled = False
    sess._throttle_until = 0.0
    sess._is_gemini = False
    sess._send_semaphore = asyncio.Semaphore(25)
    sess.on_connection_error = None
    sess._bg_tasks = set()
    ws = _CapturingWS()
    sess.ws = ws

    explicit_id = "event_inject_test_preserve_me"
    await OmniRealtimeClient.send_event(
        sess, {"type": "response.create", "event_id": explicit_id}
    )

    assert len(ws.sent) == 1
    import json as _json
    payload = _json.loads(ws.sent[0])
    assert payload["event_id"] == explicit_id


async def test_text_mode_sm_denied_when_phase_active():
    """另一路 proactive 已占 phase1 时，text 投递不应清 callbacks。"""
    sess = _FakeOmniOffline()
    mgr = _make_mgr(session=sess)
    mgr.pending_agent_callbacks = [{"status": "completed", "summary": "hello"}]

    # 模拟 router 已启动 proactive
    await mgr.state.fire(SessionEvent.PROACTIVE_START)
    assert mgr.state.phase is ProactivePhase.PHASE1

    await core_module.LLMSessionManager.trigger_agent_callbacks(mgr)

    # SM 拒绝 → prompt_ephemeral 未调用，callbacks 保留
    assert sess.called_with == []
    assert mgr.pending_agent_callbacks == [{"status": "completed", "summary": "hello"}]
    assert mgr.state.phase is ProactivePhase.PHASE1  # 原 proactive 占用未动


async def test_text_mode_sm_denied_when_session_responding():
    """AI 正在回复时 SM 拒绝 text 投递。"""
    sess = _FakeOmniOffline(is_responding=True)
    mgr = _make_mgr(session=sess)
    mgr.pending_agent_callbacks = [{"status": "completed", "summary": "queued"}]

    await core_module.LLMSessionManager.trigger_agent_callbacks(mgr)

    assert sess.called_with == []
    # callbacks 保留以便下轮重试
    assert mgr.pending_agent_callbacks == [{"status": "completed", "summary": "queued"}]
    assert mgr.state.phase is ProactivePhase.IDLE


async def test_goodbye_silent_defers_agent_callbacks_and_keeps_queue():
    """猫态静默时，主动回调不能绕过前端定时器直接投递。"""
    sess = _FakeOmniOffline(delivered=True)
    mgr = _make_mgr(session=sess)
    mgr.goodbye_silent = True
    cb = {"status": "completed", "summary": "queued"}
    mgr.pending_agent_callbacks = [cb]

    await core_module.LLMSessionManager.trigger_agent_callbacks(mgr)

    assert sess.called_with == []
    assert mgr.pending_agent_callbacks == [cb]
    assert mgr.state.phase is ProactivePhase.IDLE


def test_goodbye_silent_blocks_manager_release():
    """猫态静默时，manager 中的 proactive cue 必须继续等待。"""
    mgr = _make_mgr(session=_FakeOmniOffline(delivered=True))
    mgr.goodbye_silent = True

    assert core_module.LLMSessionManager._can_release_proactive(mgr) is False


def test_submit_proactive_callback_persists_when_goodbye_silent():
    """goodbye_silent persists new callbacks outside the manager TTL queue."""
    mgr = _make_mgr(session=_FakeOmniOffline(delivered=True))
    mgr.goodbye_silent = True
    cb = {"status": "completed", "summary": "queued"}

    core_module.LLMSessionManager.submit_proactive_callback(
        mgr,
        cb,
        priority=7,
        coalesce_key="same-source",
    )

    mgr.proactive_manager.submit.assert_not_called()
    assert mgr.pending_agent_callbacks == [cb]
    assert cb["_callback_delivery_id"]
    assert mgr.pending_extra_replies == [
        {
            "_callback_delivery_id": cb["_callback_delivery_id"],
            "origin": "event",
            "summary": "queued",
            "detail": "",
            "status": "completed",
            "context_source": "proactive.callback",
            "source_kind": "unknown",
            "source_name": "",
            "error_message": "",
        }
    ]


def _read_core_package_source() -> str:
    """Concatenated source of the ``main_logic.core`` package (the equivalent
    of reading the former single-file ``main_logic/core.py``)."""
    package_dir = os.path.join(os.path.dirname(__file__), "../../main_logic/core")
    parts = []
    for name in sorted(os.listdir(package_dir)):
        if name.endswith(".py"):
            with open(os.path.join(package_dir, name), encoding="utf-8") as fh:
                parts.append(fh.read())
    return "\n".join(parts)


def test_start_session_success_path_clears_goodbye_silent_gate():
    """Static guard for the successful start_session branch unblocking proactive."""
    normalized_source = re.sub(r"\s+", " ", _read_core_package_source())
    success_marker = "self._session_start_circuit_open = False"
    clear_marker = "if self.is_goodbye_silent(): self.set_goodbye_silent(False)"
    notify_marker = "await self.send_session_started(input_mode)"

    clear_pos = normalized_source.index(clear_marker)
    success_pos = normalized_source.rindex(success_marker, 0, clear_pos)
    notify_pos = normalized_source.index(notify_marker, clear_pos)

    assert success_pos < clear_pos < notify_pos


def test_start_session_seeds_topic_hooks_with_full_global_locale():
    """Topic hooks must keep zh-TW when start_session falls back to global language."""
    normalized_source = re.sub(r"\s+", " ", _read_core_package_source())

    assert "topic_language_seed = normalize_language_code(get_global_language_full(), format='full')" in normalized_source
    assert "self.user_language = normalize_language_code(topic_language_seed, format='short')" in normalized_source
    assert "self._conversation_turn_language = topic_language_seed" in normalized_source
    assert "self._conversation_turn_language or topic_language_seed or self.user_language" in normalized_source
    assert "self._conversation_turn_language = normalized_lang" in normalized_source


async def test_submit_proactive_callback_does_not_fail_ack_when_goodbye_silent():
    mgr = _make_mgr(session=_FakeOmniOffline(delivered=True))
    mgr.goodbye_silent = True
    future = asyncio.get_running_loop().create_future()
    cb = {"status": "completed", "summary": "queued", DELIVERY_ACK_FUTURE_KEY: future}

    core_module.LLMSessionManager.submit_proactive_callback(mgr, cb)

    assert mgr.pending_agent_callbacks == [cb]
    assert not future.done()


async def test_deliver_proactive_batch_does_not_fail_ack_when_inner_trigger_defers():
    mgr = _make_mgr(session=_FakeOmniOffline(delivered=True))
    future = asyncio.get_running_loop().create_future()
    cb = {"status": "completed", "summary": "queued", DELIVERY_ACK_FUTURE_KEY: future}
    mgr.trigger_agent_callbacks = AsyncMock(return_value=False)

    await core_module.LLMSessionManager._deliver_proactive_batch(mgr, [cb])

    assert mgr.pending_agent_callbacks == [cb]
    assert not future.done()


async def test_deliver_proactive_batch_leaves_ack_to_delivery_path():
    mgr = _make_mgr(session=_FakeOmniOffline(delivered=True))
    future = asyncio.get_running_loop().create_future()
    cb = {"status": "completed", "summary": "queued", DELIVERY_ACK_FUTURE_KEY: future}
    mgr.trigger_agent_callbacks = AsyncMock(return_value=True)

    await core_module.LLMSessionManager._deliver_proactive_batch(mgr, [cb])

    assert not future.done()


def test_topic_hook_delivery_blocked_in_active_voice_session():
    """Topic hooks are text-mode openers; an active voice session must never
    receive one. topic_hook_delivery_allowed returns False while a voice session
    is active, closing both the submit gate and the release gate."""
    mgr = _make_mgr(session=_make_voice_sess())
    mgr.is_active = True
    mgr.input_mode = 'audio'
    assert core_module.LLMSessionManager.topic_hook_delivery_allowed(mgr) is False


def test_topic_hook_delivery_blocked_during_audio_startup_window():
    """Codex P2: during a text→audio switch, start_session sets the audio
    starting flags while the old OmniOfflineClient is still in self.session. The
    gate must defer on the starting state, not on isinstance(session, Realtime)
    — otherwise a hook slips through the startup window into the dying text
    session."""
    mgr = _make_mgr(session=_FakeOmniOffline(delivered=True))
    mgr._starting_session_count = 1
    mgr._starting_input_mode = 'audio'
    assert core_module.LLMSessionManager.topic_hook_delivery_allowed(mgr) is False


def test_topic_hook_delivery_blocked_during_audio_to_text_teardown():
    """Codex P2: during an audio→text switch, start_session flips the input-mode
    flags to text while the old OmniRealtimeClient lingers in self.session, and
    trigger_agent_callbacks would still take its isinstance-gated voice branch.
    The gate must stay blocked on the live realtime session even though
    _is_voice_session_active_or_starting() has already gone False."""
    mgr = _make_mgr(session=_make_voice_sess())
    mgr.input_mode = 'text'
    mgr._starting_input_mode = 'text'
    mgr.is_active = True
    # _is_voice_session_active_or_starting() is False here, but the live session
    # is still realtime → the union predicate must still block.
    assert core_module.LLMSessionManager._is_voice_session_active_or_starting(mgr) is False
    assert core_module.LLMSessionManager.topic_hook_delivery_allowed(mgr) is False


def test_topic_hook_delivery_allowed_in_text_session():
    """Non-regression: a text session still allows topic-hook delivery
    (fail-open when no activity snapshot is available)."""
    mgr = _make_mgr(session=_FakeOmniOffline(delivered=True))
    assert core_module.LLMSessionManager.topic_hook_delivery_allowed(mgr) is True


def test_topic_hook_delivery_blocked_when_unfinished_thread_open():
    mgr = _make_mgr(session=_FakeOmniOffline(delivered=True))
    mgr._activity_tracker = MagicMock()
    mgr._activity_tracker.get_snapshot_sync.return_value = MagicMock(
        propensity="open",
        unfinished_thread=object(),
    )

    assert core_module.LLMSessionManager.topic_hook_delivery_allowed(mgr) is False


def test_topic_hook_delivery_does_not_recheck_privacy_preference(monkeypatch):
    """Delivery only gates voice/activity; privacy is outside deep-topic flow."""
    def _raise_privacy_error():
        raise AssertionError("delivery gate must not read the privacy preference")

    monkeypatch.setattr("utils.preferences.is_privacy_mode_enabled", _raise_privacy_error)
    monkeypatch.setattr(
        "main_logic.core.is_privacy_mode_enabled",
        _raise_privacy_error,
        raising=False,
    )
    mgr = _make_mgr(session=_FakeOmniOffline(delivered=True))
    assert core_module.LLMSessionManager.topic_hook_delivery_allowed(mgr) is True


async def test_deliver_proactive_batch_drops_topic_hook_in_voice():
    """Release gate: in a voice session the topic_hook is dropped (ack=False, left
    for TopicHookPool to retry), while non-topic_hook cues pass through. The voice
    gate reuses topic_hook_delivery_allowed."""
    mgr = _make_mgr(session=_make_voice_sess())
    mgr.is_active = True
    mgr.input_mode = 'audio'
    hook_future = asyncio.get_running_loop().create_future()
    topic_cb = {
        "status": "completed", "summary": "deep topic",
        "channel": "topic_hook", DELIVERY_ACK_FUTURE_KEY: hook_future,
    }
    other_cb = {"status": "completed", "summary": "task done", "channel": "agent_task"}
    mgr.enqueue_agent_callback = MagicMock()
    mgr.trigger_agent_callbacks = AsyncMock(return_value=True)

    await core_module.LLMSessionManager._deliver_proactive_batch(mgr, [topic_cb, other_cb])

    # topic hook held at the gate: ack resolved False so TopicHookPool retries.
    assert hook_future.done() and hook_future.result() is False
    # 非 topic_hook cue 照常进入投递路径。
    mgr.enqueue_agent_callback.assert_called_once_with(other_cb)


async def test_deliver_proactive_batch_drops_topic_hook_when_release_predicate_closes():
    mgr = _make_mgr(session=_FakeOmniOffline(delivered=True))
    hook_future = asyncio.get_running_loop().create_future()
    topic_cb = {
        "status": "completed",
        "summary": "deep topic",
        "channel": "topic_hook",
        "_topic_release_available": lambda: False,
        DELIVERY_ACK_FUTURE_KEY: hook_future,
    }
    other_cb = {"status": "completed", "summary": "task done", "channel": "agent_task"}
    mgr.enqueue_agent_callback = MagicMock()
    mgr.trigger_agent_callbacks = AsyncMock(return_value=True)

    await core_module.LLMSessionManager._deliver_proactive_batch(mgr, [topic_cb, other_cb])

    assert hook_future.done() and hook_future.result() is False
    mgr.enqueue_agent_callback.assert_called_once_with(other_cb)


async def test_drop_pending_topic_hooks_for_voice_sweeps_both_queues():
    """The voice-start sweep removes topic hooks from pending_agent_callbacks AND
    the paired pending_extra_replies (hot-swap prime channel), resolves ack False
    for the pool to retry, and leaves non-topic cues + their extras intact."""
    mgr = _make_mgr(session=_make_voice_sess())
    fut = asyncio.get_running_loop().create_future()
    topic_cb = {
        "_callback_delivery_id": "th1", "channel": "topic_hook",
        "status": "completed", "summary": "deep topic", DELIVERY_ACK_FUTURE_KEY: fut,
    }
    other_cb = {"_callback_delivery_id": "a1", "channel": "agent_task", "status": "completed", "summary": "task"}
    topic_extra = {"_callback_delivery_id": "th1", "origin": "event", "summary": "deep topic", "source_kind": "topic"}
    other_extra = {"_callback_delivery_id": "a1", "origin": "task_result", "summary": "task", "source_kind": "agent"}
    mgr.pending_agent_callbacks = [topic_cb, other_cb]
    mgr.pending_extra_replies = [topic_extra, other_extra]

    core_module.LLMSessionManager._drop_pending_topic_hooks_for_voice(mgr)

    assert mgr.pending_agent_callbacks == [other_cb]
    assert mgr.pending_extra_replies == [other_extra]
    assert fut.done() and fut.result() is False


async def test_drop_pending_topic_hooks_for_voice_sweeps_extras_only():
    """Codex P2: a topic hook can be consumed from pending_agent_callbacks by a
    text turn (drain_agent_callbacks_for_llm) while its paired extra is left in
    pending_extra_replies. Such an extras-only hook (source_kind=='topic', no
    callback left) must still be swept so the hot-swap prime_context path can't
    re-introduce the topic in voice."""
    mgr = _make_mgr(session=_make_voice_sess())
    topic_extra = {"_callback_delivery_id": "th1", "origin": "event", "summary": "deep topic", "source_kind": "topic"}
    other_extra = {"_callback_delivery_id": "a1", "origin": "task_result", "summary": "task", "source_kind": "agent"}
    mgr.pending_agent_callbacks = []  # callback already drained + delivered in text
    mgr.pending_extra_replies = [topic_extra, other_extra]

    core_module.LLMSessionManager._drop_pending_topic_hooks_for_voice(mgr)

    assert mgr.pending_extra_replies == [other_extra]


async def test_reset_proactive_gate_sweeps_already_pending_topic_hook_on_voice_start():
    """Codex P2 follow-up: a topic hook released into pending_agent_callbacks but
    left deferred is no longer in manager.drain_pending(), so a leftover-only
    filter misses it. The voice-start reset sweeps the pending queue itself."""
    mgr = _make_mgr(session=_make_voice_sess())
    # start_session sets these BEFORE calling _reset_proactive_gate.
    mgr._starting_session_count = 1
    mgr._starting_input_mode = 'audio'
    fut = asyncio.get_running_loop().create_future()
    topic_cb = {
        "_callback_delivery_id": "th1", "channel": "topic_hook",
        "status": "completed", "summary": "deep topic", DELIVERY_ACK_FUTURE_KEY: fut,
    }
    mgr.pending_agent_callbacks = [topic_cb]  # already released + deferred, NOT in manager
    mgr.proactive_manager.drain_pending = MagicMock(return_value=[])
    mgr.proactive_manager.reset_gate = MagicMock()

    core_module.LLMSessionManager._reset_proactive_gate(mgr)

    assert mgr.pending_agent_callbacks == []
    assert fut.done() and fut.result() is False


def test_reset_proactive_gate_keeps_topic_hook_on_text_start():
    """Non-regression: a text-mode reset (no voice start) leaves a queued topic
    hook in pending_agent_callbacks untouched — only voice starts drop it."""
    mgr = _make_mgr(session=_FakeOmniOffline(delivered=True))  # text, not voice-starting
    topic_cb = {"_callback_delivery_id": "th1", "channel": "topic_hook", "status": "completed", "summary": "deep topic"}
    mgr.pending_agent_callbacks = [topic_cb]
    mgr.proactive_manager.drain_pending = MagicMock(return_value=[])
    mgr.proactive_manager.reset_gate = MagicMock()

    core_module.LLMSessionManager._reset_proactive_gate(mgr)

    assert mgr.pending_agent_callbacks == [topic_cb]


async def test_deliver_agent_callbacks_text_drops_topic_hook_when_voice_took_over():
    """Codex P2: an in-flight topic-hook snapshot (already removed from
    pending_agent_callbacks) is unreachable by the voice-start sweep. The text
    delivery path re-gates at the actual prompt: if voice has taken over while
    this trigger parked on the SM claim / write lock, the topic hook is dropped
    (ack False for the pool to retry) and prompt_ephemeral is never called."""
    sess = _FakeOmniOffline(delivered=True)
    mgr = _make_mgr(session=sess)
    # audio start began while this trigger was parked — session still offline.
    mgr._starting_session_count = 1
    mgr._starting_input_mode = 'audio'
    fut = asyncio.get_running_loop().create_future()
    topic_cb = {
        "_callback_delivery_id": "th1", "channel": "topic_hook",
        "status": "completed", "summary": "deep topic", DELIVERY_ACK_FUTURE_KEY: fut,
    }
    snapshot = [topic_cb]

    delivered = await core_module.LLMSessionManager._deliver_agent_callbacks_text(mgr, snapshot)

    assert delivered is False
    assert fut.done() and fut.result() is False
    assert sess.called_with == []  # prompt_ephemeral never fired
    # Empty batch must free the inflight slot or the next cue stalls to timeout.
    mgr.proactive_manager.release_inflight_noop.assert_called_once()


async def test_deliver_agent_callbacks_text_drops_topic_hook_when_voice_takes_over_during_awaits():
    """Codex/CodeRabbit P2: the re-gate must also run AFTER the CLAIM/PHASE2
    awaits, immediately before prompt_ephemeral — a takeover landing during those
    awaits would otherwise slip through. Simulate voice going blocked only at the
    second (prompt-adjacent) check."""
    sess = _FakeOmniOffline(delivered=True)
    mgr = _make_mgr(session=sess)
    mgr._voice_delivery_blocked = MagicMock(side_effect=[False, True])
    fut = asyncio.get_running_loop().create_future()
    topic_cb = {
        "_callback_delivery_id": "th1", "channel": "topic_hook",
        "status": "completed", "summary": "deep topic", DELIVERY_ACK_FUTURE_KEY: fut,
    }

    delivered = await core_module.LLMSessionManager._deliver_agent_callbacks_text(mgr, [topic_cb])

    assert delivered is False
    assert fut.done() and fut.result() is False
    assert sess.called_with == []  # dropped at the prompt-adjacent re-gate
    assert mgr._voice_delivery_blocked.call_count == 2
    mgr.proactive_manager.release_inflight_noop.assert_called_once()


async def test_deliver_agent_callbacks_text_requeues_when_preempted_before_prompt():
    """A fresh user turn can arrive during the CLAIM/PHASE2 awaits. The final
    prompt-adjacent check must requeue the callback and avoid prompt_ephemeral
    once the proactive sid has gone stale."""
    sess = _FakeOmniOffline(delivered=True)
    mgr = _make_mgr(session=sess)
    cb = {"_callback_delivery_id": "id-stale-sid", "status": "completed", "summary": "stale"}

    original_fire = mgr.state.fire

    async def _fire_and_rotate_sid(event, **payload):
        await original_fire(event, **payload)
        if event is SessionEvent.PROACTIVE_PHASE2:
            async with mgr.lock:
                mgr.current_speech_id = "user-fresh-sid"
                mgr.state.mark_user_input_preempt()

    mgr.state.fire = _fire_and_rotate_sid

    delivered = await core_module.LLMSessionManager._deliver_agent_callbacks_text(mgr, [cb])

    assert delivered is False
    assert sess.called_with == []
    assert mgr.pending_agent_callbacks == [cb]
    mgr.proactive_manager.release_inflight_noop.assert_called_once()


async def test_deliver_agent_callbacks_text_keeps_topic_hook_in_plain_text_session():
    """Non-regression: with no voice active/starting, the text path delivers the
    topic hook normally (prompt_ephemeral fires, returns True)."""
    sess = _FakeOmniOffline(delivered=True)
    mgr = _make_mgr(session=sess)
    topic_cb = {
        "_callback_delivery_id": "th1", "channel": "topic_hook",
        "status": "completed", "summary": "deep topic",
    }

    delivered = await core_module.LLMSessionManager._deliver_agent_callbacks_text(mgr, [topic_cb])

    assert delivered is True
    assert len(sess.called_with) == 1


async def test_text_mode_successful_delivery_fires_full_event_sequence():
    """happy path：START → CLAIM → PHASE2 → DONE，且 phase 回到 IDLE。"""
    sess = _FakeOmniOffline(delivered=True)
    mgr = _make_mgr(session=sess)
    mgr.pending_agent_callbacks = [{"status": "completed", "summary": "ok"}]

    seen: list[tuple[SessionEvent, dict]] = []
    mgr.state.subscribe(None, lambda ev, p: seen.append((ev, dict(p))))

    await core_module.LLMSessionManager.trigger_agent_callbacks(mgr)
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

    await core_module.LLMSessionManager.trigger_agent_callbacks(mgr)
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

    assert core_module._proactive_expected_sid.get() is None
    await core_module.LLMSessionManager.trigger_agent_callbacks(mgr)
    assert core_module._proactive_expected_sid.get() is None


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

    await core_module.LLMSessionManager.trigger_agent_callbacks(mgr)

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
        await core_module.LLMSessionManager.trigger_agent_callbacks(mgr)

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

        async def prompt_ephemeral(self, instruction, *, images=None, on_committed=None):
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
    await core_module.LLMSessionManager._deliver_agent_callbacks_text(mgr, callbacks_snapshot)

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

        async def prompt_ephemeral(self, instruction, *, images=None, on_committed=None):
            # 模拟 LLM 耗时，期间 user input 抢占
            await sess_wait.wait()
            return True

        def update_max_response_length(self, *_a, **_kw):
            pass

    mgr = _make_mgr(session=_SlowSess())
    mgr.pending_agent_callbacks = [{"status": "completed", "summary": "slow"}]

    # 同时跑 agent callback delivery + 异步注入 USER_INPUT
    task = asyncio.create_task(core_module.LLMSessionManager.trigger_agent_callbacks(mgr))

    # 等 state 进入 phase1
    for _ in range(20):
        await asyncio.sleep(0.01)
        if mgr.state.phase in (ProactivePhase.PHASE1, ProactivePhase.PHASE2):
            break
    assert mgr.state.phase in (ProactivePhase.PHASE1, ProactivePhase.PHASE2), (
        "等待 trigger_agent_callbacks 进入 proactive phase 超时"
    )

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


async def test_cat_greeting_episode_scene_is_request_local_and_keeps_existing_guards(monkeypatch):
    """A return episode changes only this ephemeral cat-greeting instruction."""
    from config.prompts import prompts_proactive
    from config.prompts.prompts_proactive import (
        get_cat_greeting_episode_scene,
        get_cat_greeting_prompt,
        get_cat_greeting_reason_hint,
    )

    # This is the real failure shape: a completed CAT1 social response can
    # return while the visual tier is CAT2. Its scene must not be overwritten
    # by the old CAT2 "you dozed" factual template.
    episode = {"kind": "activity", "highlight": "social_ping"}
    def _unexpected_time_of_day_hint(_lang):
        raise AssertionError("cat return must not consume the general time-of-day hint")

    monkeypatch.setattr(
        prompts_proactive,
        "get_time_of_day_hint",
        _unexpected_time_of_day_hint,
    )
    session = _FakeOmniOffline(delivered=True)
    mgr = _make_mgr(session=session)

    await core_module.LLMSessionManager.trigger_cat_greeting(
        mgr, 300, "cat2", False, episode=episode,
    )

    assert len(session.called_with) == 1
    instruction = session.called_with[0]
    assert get_cat_greeting_episode_scene(episode, "en") in instruction
    assert "You gave a soft little response as a cat." in instruction
    assert "The true cat-form episode was:" in instruction
    # A valid episode is the actual cat-form scene, so the old cat2-only
    # factual statement must not survive and contradict it.
    assert "dozed for 5 minutes" not in instruction
    assert "called you back" in instruction
    assert "lunch" not in instruction.lower()
    for raw_value in ("cat1_play_yarn", "requestId", "runId", "appetite", "raw text"):
        assert raw_value not in instruction
    assert mgr.state.phase is ProactivePhase.IDLE

    # A short return remains silent even when it carries a valid episode.
    short_session = _FakeOmniOffline(delivered=True)
    short_mgr = _make_mgr(session=short_session)
    await core_module.LLMSessionManager.trigger_cat_greeting(
        short_mgr, 179, "cat1", False, episode=episode,
    )
    assert short_session.called_with == []

    # No trustworthy chapter means the exact existing greeting path remains.
    no_episode_session = _FakeOmniOffline(delivered=True)
    no_episode_mgr = _make_mgr(session=no_episode_session)
    await core_module.LLMSessionManager.trigger_cat_greeting(
        no_episode_mgr, 300, "cat1", False,
    )
    assert len(no_episode_session.called_with) == 1
    legacy = get_cat_greeting_prompt("awake", 300, "en")
    assert legacy is not None
    assert no_episode_session.called_with[0] == legacy.format(
        reason_hint=get_cat_greeting_reason_hint(False, "en").format(master="Master"),
        elapsed="5 minutes",
        name="Test",
        master="Master",
        time_hint="",
    )
    invalid_episode_session = _FakeOmniOffline(delivered=True)
    invalid_episode_mgr = _make_mgr(session=invalid_episode_session)
    await core_module.LLMSessionManager.trigger_cat_greeting(
        invalid_episode_mgr, 300, "cat1", False,
        episode={"kind": "not-a-real-episode"},
    )
    assert invalid_episode_session.called_with == no_episode_session.called_with

    silent_session = _FakeOmniOffline(delivered=True)
    silent_mgr = _make_mgr(session=silent_session)
    silent_mgr.goodbye_silent = True
    await core_module.LLMSessionManager.trigger_cat_greeting(
        silent_mgr, 300, "cat1", False, episode=episode,
    )
    assert silent_session.called_with == []

    takeover_session = _FakeOmniOffline(delivered=True)
    takeover_mgr = _make_mgr(session=takeover_session)
    takeover_mgr._takeover_active = True
    await core_module.LLMSessionManager.trigger_cat_greeting(
        takeover_mgr, 300, "cat1", False, episode=episode,
    )
    assert takeover_session.called_with == []

    voice_session = _FakeOmniOffline(delivered=True)
    voice_mgr = _make_mgr(session=voice_session)
    voice_mgr.is_active = True
    voice_mgr.input_mode = "audio"
    await core_module.LLMSessionManager.trigger_cat_greeting(
        voice_mgr, 300, "cat1", False, episode=episode,
    )
    assert voice_session.called_with == []

    busy_session = _FakeOmniOffline(delivered=True)
    busy_mgr = _make_mgr(session=busy_session)
    assert await busy_mgr.state.try_start_proactive(session=busy_session) is True
    await core_module.LLMSessionManager.trigger_cat_greeting(
        busy_mgr, 300, "cat1", False, episode=episode,
    )
    assert busy_session.called_with == []
    await busy_mgr.state.fire(SessionEvent.PROACTIVE_DONE)

    # The factual scene has no user text of its own, but its template still
    # formats master/time fields. Empty and braced names remain safe.
    for master_name in ("", "A{B}"):
        name_session = _FakeOmniOffline(delivered=True)
        name_mgr = _make_mgr(session=name_session)
        name_mgr.master_name = master_name
        await core_module.LLMSessionManager.trigger_cat_greeting(
            name_mgr, 300, "cat1", False, episode=episode,
        )
        assert len(name_session.called_with) == 1
        assert get_cat_greeting_episode_scene(episode, "en") in name_session.called_with[0]
        if master_name:
            assert master_name in name_session.called_with[0]
