"""SM integration regressions: key contracts after ``trigger_agent_callbacks`` /``trigger_greeting``
adopt ``SessionStateMachine``.

Coverage:
1. Voice primarily uses realtime inject (conversation.item.create +response.create),
   **bypassing** SM proactive pipelines (no PROACTIVE_START); only provider
   NotImplementedError falls back to hot swap. Serialization/rejection restoration/TOCTOU races appear
   in voice_mode_* cases below.
2. Text rejects while another proactive flow owns SM, retaining callbacks for retry.
3. Text rejects while ``session._is_responding == True``, retaining callbacks.
4. Normal text delivery events: IDLE ->PHASE1 (claim) ->CLAIM ->PHASE2 ->DONE.
5. ``prompt_ephemeral`` exceptions still fire ``PROACTIVE_DONE`` via finally.
6. Mutual exclusion among ``trigger_agent_callbacks``, ``trigger_greeting``, and ``/api/proactive_chat``:
   only one concurrent contender enters phase1.
7. ``trigger_greeting`` voice guard after SM claim prevents delivery but still fires DONE.
"""
import asyncio
import os
import re
import sys
import time
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock

import pytest

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

# Isolate trigger_agent_callbacks /trigger_greeting environment dependencies (prompt resources,
# _loc, normalize_language_code, httpx, etc.) by testing SM contracts rather than entire
# functions: minimal mgr mimics LLMSessionManager's
# state/session/lock structure and invokes key trigger_agent_callbacks branches directly.
import main_logic.core as core_module
from main_logic.omni_offline_client import OmniOfflineClient
from main_logic.omni_realtime_client import (
    ImageStageResult,
    MultimodalTurnDelivery,
)
from main_logic.proactive_delivery import (
    CALLBACK_EXPIRES_AT_KEY,
    DELIVERY_ACK_FUTURE_KEY,
    DELIVERY_RETRACTED_KEY,
    SWAP_PRIME_DELIVERY_CLAIM_KEY,
)
from main_logic.session_state import (
    ProactivePhase,
    SessionEvent,
    SessionStateMachine,
    TurnOwner,
)


class _FakeOmniOffline(OmniOfflineClient):
    """Minimal OmniOfflineClient double with test-injected ``prompt_ephemeral``.

    Inherit ``OmniOfflineClient`` to pass isinstance branches, skipping superclass
    initialization to avoid real LLM clients.
    """

    def __init__(self, delivered: bool = True, is_responding: bool = False,
                 raise_exc: BaseException | None = None):
        # Deliberately skip super().__init__, requiring many OpenAI/WebSocket arguments.
        self._delivered = delivered
        self._is_responding = is_responding
        self._raise = raise_exc
        self.called_with: list[str] = []

    async def prompt_ephemeral(self, instruction: str, *, images=None, on_committed=None, on_committed_text=None) -> bool:
        self.called_with.append(instruction)
        if self._raise is not None:
            raise self._raise
        if self._delivered and on_committed:
            if on_committed_text:
                on_committed_text("shown")
            on_committed()
        return self._delivered

    def update_max_response_length(self, *_a, **_kw):
        pass


class _CapturingImageOmniOffline(_FakeOmniOffline):
    def __init__(self):
        super().__init__(delivered=True)
        self.image_batches: list[list[str]] = []

    async def prompt_ephemeral(
        self,
        instruction: str,
        *,
        images=None,
        on_committed=None,
        on_committed_text=None,
    ) -> bool:
        self.called_with.append(instruction)
        self.image_batches.append(list(images or []))
        if on_committed:
            if on_committed_text:
                on_committed_text("shown")
            on_committed()
        return True


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
    # Patch OmniRealtimeClient /OmniOfflineClient isinstance checks:
    # tests focus on OmniOfflineClient; construct other branches explicitly.
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


def test_enqueue_agent_callback_keeps_image_only_completed_callback():
    mgr = _make_mgr()
    callback = {
        "status": "completed",
        "summary": "",
        "detail": "",
        "media_images": ["image-b64"],
        "origin": "event",
    }

    core_module.LLMSessionManager.enqueue_agent_callback(mgr, callback)

    assert mgr.pending_agent_callbacks == [callback]
    assert len(mgr.pending_extra_replies) == 1
    assert (
        mgr.pending_extra_replies[0]["_callback_delivery_id"]
        == callback["_callback_delivery_id"]
    )


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
    sess.injected_events = []
    sess.inject_calls = 0
    sess.is_active_response = lambda: sess._is_responding
    sess.on_sid_rotate = AsyncMock()
    sess._client_vad_active = False
    sess._user_recent_activity_time = 0.0
    sess._ai_recent_activity_time = 0.0
    sess.get_multimodal_turn_delivery = lambda: (
        MultimodalTurnDelivery.DIRECT_ATOMIC
    )

    if inject is None:
        async def _default_inject(
            text,
            *,
            events_before_text=(),
            on_rejected=None,
        ):
            sess.inject_calls += 1
            sess.injected.append(text)
            sess.injected_events.append(events_before_text)
        sess.inject_text_and_request_response = _default_inject
    else:
        sess.inject_text_and_request_response = inject
    return sess


def _make_voice_sess_with_real_gate():
    """``_make_voice_sess`` variant that keeps the REAL ``is_active_response``.

    The stock fixture pins ``is_active_response`` to ``_is_responding`` alone,
    which silently erases the other half of the production gate — since #2345
    it is ``bool(self._is_responding or self._ensure_response_arbiter()
    .is_busy)``. Every test built on the stock fixture is structurally blind
    to arbiter-held busyness (queued tickets, a live server response, a
    pending server-VAD response). Keep using the stock fixture for SM contract
    tests; use THIS variant when the assertion is about the gate itself.

    Deleting the instance attribute re-exposes the class method, whose
    ``_ensure_response_arbiter()`` lazily builds a real arbiter on the
    ``__new__``-built double (its ``send_event`` bound method exists and is
    never called by these tests).
    """
    sess = _make_voice_sess()
    del sess.is_active_response
    return sess


async def test_voice_gate_sees_arbiter_busyness_not_just_the_responding_flag():
    """A live server-side response makes the arbiter busy while
    ``_is_responding`` is still False (the flag flips on response.created via
    the transport, which these doubles never run). The proactive gate must
    defer on arbiter busyness alone — and deliver once the lane clears, so
    the deferral is attributable to the gate rather than to any other
    precondition. Mutation check: reverting ``is_active_response`` to
    ``bool(self._is_responding)`` turns the first half red while every test
    on the stock fixture stays green — that enduring green is the fixture
    blind spot documented on ``_make_voice_sess_with_real_gate``."""
    sess = _make_voice_sess_with_real_gate()
    mgr = _make_mgr(session=sess)
    cb = {
        "_callback_delivery_id": "id-arbiter-busy",
        "status": "completed",
        "summary": "task done",
    }
    mgr.pending_agent_callbacks = [cb]

    assert sess._is_responding is False
    sess._ensure_response_arbiter().notify_response_created(
        {"type": "response.created", "response": {"id": "srv-1"}}
    )
    assert sess.is_active_response() is True

    delivered = await core_module.LLMSessionManager.trigger_agent_callbacks(mgr)

    assert delivered is False
    assert sess.inject_calls == 0, (
        "the gate must defer while the arbiter holds a live server response"
    )
    assert mgr.pending_agent_callbacks == [cb], "deferred callbacks must be retained"

    # The dual: once the lane clears the very same delivery goes through,
    # proving the deferral above was the arbiter gate and nothing else.
    sess._response_arbiter.notify_response_terminal(
        {"type": "response.done", "response": {"id": "srv-1"}}
    )
    assert sess.is_active_response() is False

    delivered = await core_module.LLMSessionManager.trigger_agent_callbacks(mgr)

    assert delivered is True
    assert sess.inject_calls == 1
    assert mgr.pending_agent_callbacks == []


async def test_voice_callback_defers_when_microphone_activity_arrived_first():
    sess = _make_voice_sess()
    sess._user_recent_activity_time = time.time()
    sess.stream_image = AsyncMock()
    mgr = _make_mgr(session=sess)
    cb = {
        "_callback_delivery_id": "id-recent-microphone",
        "status": "completed",
        "summary": "must wait for the user turn",
        "media_images": ["image-b64"],
    }
    mgr.pending_agent_callbacks = [cb]

    delivered = await core_module.LLMSessionManager.trigger_agent_callbacks(mgr)

    assert delivered is False
    sess.on_sid_rotate.assert_not_awaited()
    sess.stream_image.assert_not_awaited()
    assert sess.inject_calls == 0
    assert mgr.pending_agent_callbacks == [cb]


async def test_recent_voice_activity_arms_retry_without_turn_end_signal(monkeypatch):
    import main_logic.core.proactive as proactive_module

    # Keep setup/logging/runner delays outside the activity window. Exercise
    # the real retry scheduler, but advance its clock and timer explicitly.
    now = [1000.0]
    monkeypatch.setattr(proactive_module, "time", SimpleNamespace(time=lambda: now[0]))
    timer_loop = SimpleNamespace(call_later=MagicMock())
    controlled_asyncio = SimpleNamespace(**vars(asyncio))
    controlled_asyncio.get_running_loop = lambda: timer_loop
    monkeypatch.setattr(proactive_module, "asyncio", controlled_asyncio)
    sess = _make_voice_sess()
    sess._user_recent_activity_window = 0.02
    sess._user_recent_activity_time = now[0]
    mgr = _make_mgr(session=sess)
    cb = {
        "_callback_delivery_id": "id-recent-activity-retry",
        "status": "completed",
        "summary": "retry after the activity window",
    }
    mgr.pending_agent_callbacks = [cb]
    retry_fired = asyncio.Event()

    async def retry_trigger():
        retry_fired.set()
        return False

    mgr.trigger_agent_callbacks = retry_trigger
    mgr._fire_task = asyncio.create_task

    delivered = await core_module.LLMSessionManager.trigger_agent_callbacks(mgr)

    assert delivered is False
    assert mgr.pending_agent_callbacks == [cb]
    assert sess.inject_calls == 0
    assert not retry_fired.is_set()
    timer_loop.call_later.assert_called_once()
    delay, wake_up = timer_loop.call_later.call_args.args
    assert delay == pytest.approx(sess._user_recent_activity_window)

    now[0] += delay
    fired_tasks = []
    mgr._fire_task = lambda coro: fired_tasks.append(asyncio.create_task(coro))
    wake_up()
    assert len(fired_tasks) == 1
    await fired_tasks[0]
    assert retry_fired.is_set()


async def test_voice_nudge_waits_for_callback_inject_lock():
    sess = _make_voice_sess()
    sess._proactive_inject_awaiting_outcome = False
    sess.prompt_ephemeral = AsyncMock(return_value=True)
    mgr = _make_mgr(session=sess)
    mgr.is_active = True
    mgr.input_mode = "microphone"
    mgr.is_hot_swap_imminent = False

    await mgr._voice_proactive_inject_lock.acquire()
    task = asyncio.create_task(
        core_module.LLMSessionManager.trigger_voice_proactive_nudge(mgr)
    )
    await asyncio.sleep(0)
    sess.prompt_ephemeral.assert_not_awaited()

    mgr._voice_proactive_inject_lock.release()
    assert await task is True
    sess.prompt_ephemeral.assert_awaited_once_with(
        language="en",
        user_turn_active=mgr._independent_asr_user_turn_active,
        session_owned=sess.prompt_ephemeral.await_args.kwargs["session_owned"],
    )
    assert sess.prompt_ephemeral.await_args.kwargs["session_owned"]() is True


async def test_voice_nudge_request_locale_is_forwarded_without_mutating_manager_state():
    sess = _make_voice_sess()
    sess._proactive_inject_awaiting_outcome = False
    sess.prompt_ephemeral = AsyncMock(return_value=True)
    mgr = _make_mgr(session=sess)
    mgr.is_active = True
    mgr.input_mode = "microphone"
    mgr.is_hot_swap_imminent = False
    mgr.user_language = "en"
    mgr._user_language_explicit = False
    mgr._conversation_render_language = None

    delivered = await core_module.LLMSessionManager.trigger_voice_proactive_nudge(
        mgr,
        language="zh-TW",
    )

    assert delivered is True
    sess.prompt_ephemeral.assert_awaited_once_with(
        language="zh-TW",
        user_turn_active=mgr._independent_asr_user_turn_active,
        session_owned=sess.prompt_ephemeral.await_args.kwargs["session_owned"],
    )
    assert sess.prompt_ephemeral.await_args.kwargs["session_owned"]() is True
    assert mgr.user_language == "en"
    assert mgr._user_language_explicit is False
    assert mgr._conversation_render_language is None


# ─────────────────────────────────────────────────────────────────────────────
# trigger_agent_callbacks
# ─────────────────────────────────────────────────────────────────────────────

async def test_voice_mode_idle_injects_and_drops_paired_cbs_and_extras():
    """Idle voice calls inject_text_and_request_response and removes cb synchronously from
    **both** pending_agent_callbacks and paired pending_extra_replies;
    uncleared extras would make _finalize_turn_after_emit needlessly trigger
    _trigger_immediate_preparation_for_extra hot-swap preparation,
    then prime already-spoken content on later hot swaps, duplicating delivery.
    Fire no SM events; voice injects directly through realtime APIs."""
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
    sess.on_sid_rotate.assert_awaited_once_with()
    assert mgr.pending_agent_callbacks == []
    # Key regression: synchronously remove matching extras too.
    assert mgr.pending_extra_replies == []
    assert mgr.state.phase is ProactivePhase.IDLE
    assert events == []


async def test_voice_mode_sid_rotation_rechecks_user_activity_before_media():
    sess = _make_voice_sess()

    async def _rotate_while_user_starts_speaking():
        await asyncio.sleep(0)
        sess._client_vad_active = True

    sess.on_sid_rotate = AsyncMock(
        side_effect=_rotate_while_user_starts_speaking
    )
    sess.stream_image = AsyncMock()
    mgr = _make_mgr(session=sess)
    mgr._schedule_proactive_retry = MagicMock()
    cb = {
        "_callback_delivery_id": "id-vad-during-rotation",
        "status": "completed",
        "summary": "defer this callback",
        "media_images": ["callback-image"],
    }
    mgr.pending_agent_callbacks = [cb]

    delivered = await core_module.LLMSessionManager.trigger_agent_callbacks(
        mgr
    )

    assert delivered is False
    sess.on_sid_rotate.assert_awaited_once_with()
    sess.stream_image.assert_not_awaited()
    assert sess.inject_calls == 0
    assert mgr.pending_agent_callbacks == [cb]
    assert cb.get("_voice_delivery_committed") is None
    mgr._schedule_proactive_retry.assert_called_once_with(
        mgr.proactive_manager.min_gap_s
    )


async def test_voice_mode_native_media_rechecks_user_activity_after_stream():
    sess = _make_voice_sess()
    sess._inject_rejection_handlers = {}
    sess._fatal_error_occurred = False
    sess.close = AsyncMock()
    mgr = _make_mgr(session=sess)
    mgr.end_session = AsyncMock()
    mgr._schedule_proactive_retry = MagicMock()
    retirement_tasks = []

    def _fire_retirement(coro):
        task = asyncio.create_task(coro)
        retirement_tasks.append(task)
        return task

    mgr._fire_task = _fire_retirement

    async def _stream_image(
        _image_b64,
        *,
        bypass_rate_limit=False,
        cache_latest=True,
        source=None,
        request_id=None,
        on_rejected=None,
    ):
        assert bypass_rate_limit is True
        assert cache_latest is False
        assert source == "callback"
        assert on_rejected is not None
        await asyncio.sleep(0)
        sess._client_vad_active = True
        return SimpleNamespace(accepted=True, mode="native")

    sess.stream_image = _stream_image
    cb = {
        "_callback_delivery_id": "id-vad-during-native-media",
        "status": "completed",
        "summary": "defer native visual callback",
        "media_images": ["callback-image"],
    }
    mgr.pending_agent_callbacks = [cb]

    delivered = await core_module.LLMSessionManager.trigger_agent_callbacks(mgr)
    await asyncio.gather(*retirement_tasks)

    assert delivered is False
    assert sess.inject_calls == 0
    assert sess._fatal_error_occurred is True
    sess.close.assert_awaited_once_with()
    mgr.end_session.assert_awaited_once_with(
        by_server=True,
        expected_session=sess,
    )
    assert mgr.pending_agent_callbacks == [cb]
    assert cb.get("_voice_delivery_committed") is None
    mgr._schedule_proactive_retry.assert_called_once_with(
        mgr.proactive_manager.min_gap_s
    )


async def test_external_callback_rechecks_user_activity_after_visual_analysis():
    """A user turn that wins the vision await must preempt proactive injection."""
    sess = _make_voice_sess()
    sess._inject_rejection_handlers = {}

    async def _stream_image(
        _image_b64,
        *,
        bypass_rate_limit=False,
        cache_latest=True,
        source=None,
        request_id=None,
        on_rejected=None,
    ):
        assert bypass_rate_limit is True
        assert cache_latest is False
        assert source == "callback"
        assert request_id == "id-vad-during-vision"
        assert on_rejected is not None
        await asyncio.sleep(0)
        sess._client_vad_active = True
        return SimpleNamespace(
            accepted=True,
            mode="external_description",
            description="画面里有一只猫。",
        )

    async def _expire(_event_id, _timeout):
        return None

    def _fire_task(coro):
        coro.close()

    sess.stream_image = _stream_image
    sess._expire_inject_rejection_handler = _expire
    sess._fire_task = _fire_task
    mgr = _make_mgr(session=sess)
    mgr._schedule_proactive_retry = MagicMock()
    cb = {
        "_callback_delivery_id": "id-vad-during-vision",
        "status": "completed",
        "summary": "defer this visual callback",
        "media_images": ["callback-image"],
    }
    mgr.pending_agent_callbacks = [cb]

    delivered = await core_module.LLMSessionManager.trigger_agent_callbacks(
        mgr
    )

    assert delivered is False
    assert sess.inject_calls == 0
    assert sess._inject_rejection_handlers == {}
    assert mgr.pending_agent_callbacks == [cb]
    assert cb.get("_voice_delivery_committed") is None
    mgr._schedule_proactive_retry.assert_called_once_with(
        mgr.proactive_manager.min_gap_s
    )


async def test_callback_media_analysis_rechecks_session_ownership():
    sess = _make_voice_sess()
    sess._inject_rejection_handlers = {}
    replacement = _make_voice_sess()
    mgr = _make_mgr(session=sess)
    mgr._schedule_proactive_retry = MagicMock()

    async def _stream_image(
        _image_b64,
        *,
        bypass_rate_limit=False,
        cache_latest=True,
        source=None,
        request_id=None,
        on_rejected=None,
    ):
        assert bypass_rate_limit is True
        assert cache_latest is False
        assert source == "callback"
        assert request_id == "id-session-swap-during-vision"
        assert on_rejected is not None
        await asyncio.sleep(0)
        mgr.session = replacement
        return SimpleNamespace(
            accepted=True,
            mode="external_description",
            description="画面里有一只猫。",
        )

    async def _expire(_event_id, _timeout):
        return None

    sess.stream_image = _stream_image
    sess._expire_inject_rejection_handler = _expire
    sess._fire_task = lambda coro: coro.close()
    cb = {
        "_callback_delivery_id": "id-session-swap-during-vision",
        "status": "completed",
        "summary": "defer this visual callback",
        "media_images": ["callback-image"],
    }
    mgr.pending_agent_callbacks = [cb]

    delivered = await core_module.LLMSessionManager.trigger_agent_callbacks(mgr)

    assert delivered is False
    assert sess.inject_calls == 0
    assert sess._inject_rejection_handlers == {}
    assert mgr.pending_agent_callbacks == [cb]
    assert cb.get("_voice_delivery_committed") is None
    mgr._schedule_proactive_retry.assert_called_once_with(
        mgr.proactive_manager.min_gap_s
    )


async def test_callback_external_description_prefix_has_item_id():
    sess = _make_voice_sess()
    sess._inject_rejection_handlers = {}
    sess._fire_task = lambda coro: coro.close()
    sess._expire_inject_rejection_handler = AsyncMock()
    mgr = _make_mgr(session=sess)

    async def _stream_image(
        _image_b64,
        *,
        bypass_rate_limit=False,
        cache_latest=True,
        source=None,
        request_id=None,
        on_rejected=None,
    ):
        return SimpleNamespace(
            accepted=True,
            mode="external_description",
            description="画面里有一只猫。",
        )

    sess.stream_image = _stream_image
    cb = {
        "_callback_delivery_id": "id-description-prefix-item",
        "status": "completed",
        "summary": "visual callback",
        "media_images": ["callback-image"],
    }
    mgr.pending_agent_callbacks = [cb]

    delivered = await core_module.LLMSessionManager.trigger_agent_callbacks(mgr)

    assert delivered is True
    assert len(sess.injected_events) == 1
    visual_event = sess.injected_events[0][0]
    assert visual_event["item"]["id"].startswith("neko_cbvis_")


async def test_external_callback_rechecks_independent_asr_turn_after_visual_analysis():
    """Independent-ASR onset must preempt callback injection after vision awaits."""
    sess = _make_voice_sess()
    sess._inject_rejection_handlers = {}
    mgr = _make_mgr(session=sess)
    mgr._schedule_proactive_retry = MagicMock()
    independent_turn_active = False
    mgr._independent_asr_user_turn_active = lambda: independent_turn_active

    async def _stream_image(
        _image_b64,
        *,
        bypass_rate_limit=False,
        cache_latest=True,
        source=None,
        request_id=None,
        on_rejected=None,
    ):
        nonlocal independent_turn_active
        assert bypass_rate_limit is True
        assert cache_latest is False
        assert source == "callback"
        assert request_id == "id-independent-asr-during-vision"
        assert on_rejected is not None
        await asyncio.sleep(0)
        independent_turn_active = True
        return SimpleNamespace(
            accepted=True,
            mode="external_description",
            description="画面里有一只猫。",
        )

    async def _expire(_event_id, _timeout):
        return None

    def _fire_task(coro):
        coro.close()

    sess.stream_image = _stream_image
    sess._expire_inject_rejection_handler = _expire
    sess._fire_task = _fire_task
    cb = {
        "_callback_delivery_id": "id-independent-asr-during-vision",
        "status": "completed",
        "summary": "defer this visual callback",
        "media_images": ["callback-image"],
    }
    mgr.pending_agent_callbacks = [cb]

    delivered = await core_module.LLMSessionManager.trigger_agent_callbacks(mgr)

    assert delivered is False
    assert sess.inject_calls == 0
    assert sess._inject_rejection_handlers == {}
    assert mgr.pending_agent_callbacks == [cb]
    assert cb.get("_voice_delivery_committed") is None
    mgr._schedule_proactive_retry.assert_called_once_with(
        mgr.proactive_manager.min_gap_s
    )


async def test_voice_mode_sid_rotation_failure_arms_callback_retry():
    sess = _make_voice_sess()
    sess.on_sid_rotate = AsyncMock(side_effect=RuntimeError("rotate failed"))
    sess.stream_image = AsyncMock()
    mgr = _make_mgr(session=sess)
    mgr._schedule_proactive_retry = MagicMock()
    cb = {
        "_callback_delivery_id": "id-rotation-failed",
        "status": "completed",
        "summary": "retry this callback",
        "media_images": ["callback-image"],
    }
    mgr.pending_agent_callbacks = [cb]

    delivered = await core_module.LLMSessionManager.trigger_agent_callbacks(
        mgr
    )

    assert delivered is False
    sess.on_sid_rotate.assert_awaited_once_with()
    sess.stream_image.assert_not_awaited()
    assert sess.inject_calls == 0
    assert mgr.pending_agent_callbacks == [cb]
    mgr._schedule_proactive_retry.assert_called_once_with(
        mgr.proactive_manager.min_gap_s
    )


async def test_voice_mode_inject_preserves_passive_cb_without_extra():
    """Voice inject consumes only proactive callbacks; passive stays queued
    without a hot-swap extra mirror."""
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
    proactive_extra = {
        "_callback_delivery_id": "id-proactive",
        "origin": "task_result", "summary": "ping user now",
    }
    mgr.pending_agent_callbacks = [passive_cb, proactive_cb]
    mgr.pending_extra_replies = [proactive_extra]

    await core_module.LLMSessionManager.trigger_agent_callbacks(mgr)
    await asyncio.sleep(0)

    assert len(sess.injected) == 1
    assert mgr.pending_agent_callbacks == [passive_cb]
    assert mgr.pending_extra_replies == []


async def test_voice_passive_enqueue_never_injects_or_creates_hot_swap_extra():
    sess = _make_voice_sess()
    mgr = _make_mgr(session=sess)
    passive_cb = {
        "origin": "event",
        "status": "completed",
        "summary": "context only",
        "detail": "context only",
        "delivery_mode": "passive",
        "coalesce_key": "context",
    }

    core_module.LLMSessionManager.enqueue_agent_callback(mgr, passive_cb)
    delivered = await core_module.LLMSessionManager.trigger_agent_callbacks(mgr)
    await asyncio.sleep(0)

    assert delivered is False
    assert sess.injected == []
    assert mgr.pending_agent_callbacks == [passive_cb]
    assert mgr.pending_extra_replies == []


async def test_voice_mode_busy_defers_cbs_for_retry():
    """Responding voice sessions keep callbacks queued for retry after response.done."""
    sess = _make_voice_sess(is_responding=True)
    mgr = _make_mgr(session=sess)
    original = [{"status": "completed", "summary": "deferred"}]
    mgr.pending_agent_callbacks = list(original)

    await core_module.LLMSessionManager.trigger_agent_callbacks(mgr)
    await asyncio.sleep(0)

    assert sess.injected == []  # No injection.
    assert mgr.pending_agent_callbacks == original  # Retain cb.
    assert mgr.state.phase is ProactivePhase.IDLE


async def test_voice_mode_drop_uses_id_match_not_length_alignment():
    """Successful voice injection removes exact ``_callback_delivery_id`` matches from both queues,
    even if prior ``drain_agent_callbacks_for_llm`` cleared pending_agent_callbacks,
    misaligning queue lengths; matching stale extras still need removal.
    Pin CodeRabbit r3248967092: equal lengths do not imply aligned queues."""
    sess = _make_voice_sess()
    mgr = _make_mgr(session=sess)
    # Simulate queue misalignment: old user-turn drain_agent_callbacks_for_llm
    # cleared pending_agent_callbacks, while pending_extra_replies retained both;
    # then new proactive cb arrives, pac=1/extras=3.
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
    # Key: clear new_extra; stale extras are outside this injection, retained for hot swap.
    assert mgr.pending_extra_replies == [stale_extra_a, stale_extra_b]


async def test_voice_mode_server_rejection_re_enqueues_cb():
    """Voice ``response.create`` server rejection (``response_already_active`` under VAD overtaking)
    arrives asynchronously through errors after ``inject_text_and_request_response``
    returned. Registered on_rejected must restore optimistically removed callbacks
    into pending_agent_callbacks so _finalize_turn_after_emit retries
    after the next response.done. Pin Codex r3249012424."""
    captured_rejection: list = []

    sess = _make_voice_sess()

    async def _inject(text, *, on_rejected=None):
        sess.inject_calls += 1
        sess.injected.append(text)
        # Do not fire here; simulate inject returned before server processing.
        captured_rejection.append(on_rejected)
    sess.inject_text_and_request_response = _inject

    mgr = _make_mgr(session=sess)
    cb = {"_callback_delivery_id": "id-race", "status": "completed", "summary": "race-cb"}
    extra = {"_callback_delivery_id": "id-race", "origin": "task_result", "summary": "race-cb"}
    mgr.pending_agent_callbacks = [cb]
    mgr.pending_extra_replies = [extra]

    await core_module.LLMSessionManager.trigger_agent_callbacks(mgr)
    await asyncio.sleep(0)

    # Optimistic removal empties both queues.
    assert mgr.pending_agent_callbacks == []
    assert mgr.pending_extra_replies == []
    assert len(captured_rejection) == 1
    assert captured_rejection[0] is not None

    # Simulate asynchronous server response_already_active.
    captured_rejection[0]("response_already_active")

    # on_rejected restores cb to pending_agent_callbacks for next-trigger retry.
    assert len(mgr.pending_agent_callbacks) == 1
    assert mgr.pending_agent_callbacks[0]["_callback_delivery_id"] == "id-race"
    # Restore paired extras too, or later hot-swap fallback loses supplementary content.
    assert len(mgr.pending_extra_replies) == 1
    assert mgr.pending_extra_replies[0]["_callback_delivery_id"] == "id-race"
    # Handler does not immediately re-fire (Codex P1): response_already_active may see
    # stale is_active_response=False; immediate re-fire creates inject/reject loops.
    # Delegate retry to active response.done ->
    # _finalize_turn_after_emit. No immediate scheduling here.
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

    async def _stream_then_retract(
        callbacks,
        session,
        *,
        on_rejected=None,
        events_before_text=None,
        on_session_unsafe=None,
        on_native_prefix_committed=None,
    ):
        assert on_rejected is not None
        assert events_before_text == []
        assert on_session_unsafe is not None
        assert on_native_prefix_committed is not None
        cb[DELIVERY_RETRACTED_KEY] = True
        return True
    mgr._stream_cb_media = _stream_then_retract

    delivered = await core_module.LLMSessionManager.trigger_agent_callbacks(mgr)

    assert delivered is False
    assert sess.injected == []
    assert mgr.pending_agent_callbacks == []
    assert mgr.pending_extra_replies == []


async def test_voice_mode_drops_callback_expired_during_media_before_inject():
    """A callback expiring during media streaming never reaches text inject."""
    sess = _make_voice_sess()
    mgr = _make_mgr(session=sess)
    future = asyncio.get_running_loop().create_future()
    cb = {
        "_callback_delivery_id": "id-expired-during-media",
        "status": "completed",
        "summary": "stale event",
        CALLBACK_EXPIRES_AT_KEY: time.monotonic() + 60,
        DELIVERY_ACK_FUTURE_KEY: future,
    }
    extra = {
        "_callback_delivery_id": "id-expired-during-media",
        "origin": "event",
        "summary": "stale event",
        CALLBACK_EXPIRES_AT_KEY: cb[CALLBACK_EXPIRES_AT_KEY],
    }
    mgr.pending_agent_callbacks = [cb]
    mgr.pending_extra_replies = [extra]

    async def _stream_then_expire(*_args, **_kwargs):
        cb[CALLBACK_EXPIRES_AT_KEY] = time.monotonic() - 1
        return True

    mgr._stream_cb_media = _stream_then_expire

    delivered = await core_module.LLMSessionManager.trigger_agent_callbacks(mgr)

    assert delivered is False
    assert sess.injected == []
    assert mgr.pending_agent_callbacks == []
    assert mgr.pending_extra_replies == []
    assert future.done() and future.result() is False
    assert cb.get("_voice_delivery_committed") is None
    mgr.proactive_manager.release_inflight_noop.assert_called_once()


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


async def test_trigger_releases_inflight_when_callback_expires_during_claim():
    sess = _FakeOmniOffline(delivered=True)
    mgr = _make_mgr(session=sess)
    future = asyncio.get_running_loop().create_future()
    callback = {
        "_callback_delivery_id": "id-expired-during-trigger-claim",
        "status": "completed",
        "summary": "stale event",
        CALLBACK_EXPIRES_AT_KEY: time.monotonic() + 60,
        DELIVERY_ACK_FUTURE_KEY: future,
    }
    mgr.pending_agent_callbacks = [callback]
    original_try_start = mgr.state.try_start_proactive

    async def _try_start_and_expire(*args, **kwargs):
        claimed = await original_try_start(*args, **kwargs)
        callback[CALLBACK_EXPIRES_AT_KEY] = time.monotonic() - 1
        return claimed

    mgr.state.try_start_proactive = _try_start_and_expire

    delivered = await core_module.LLMSessionManager.trigger_agent_callbacks(mgr)

    assert delivered is False
    assert sess.called_with == []
    assert mgr.pending_agent_callbacks == []
    assert future.done() and future.result() is False
    mgr.proactive_manager.release_inflight_noop.assert_called_once()


async def test_text_mode_resolves_delivery_ack_after_committed_output_before_completion_flush():
    class _FlushCancellingSess(_FakeOmniOffline):
        async def prompt_ephemeral(self, instruction: str, *, images=None, on_committed=None, on_committed_text=None) -> bool:
            self.called_with.append(instruction)
            assert not future.done()
            assert on_committed is not None
            if on_committed_text:
                on_committed_text("shown")
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
        async def prompt_ephemeral(self, instruction: str, *, images=None, on_committed=None, on_committed_text=None) -> bool:
            self.called_with.append(instruction)
            assert on_committed is not None
            if on_committed_text:
                on_committed_text("shown")
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
        async def prompt_ephemeral(self, instruction: str, *, images=None, on_committed=None, on_committed_text=None) -> bool:
            self.called_with.append(instruction)
            if on_committed:
                if on_committed_text:
                    on_committed_text("shown")
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
    await asyncio.sleep(0)

    assert delivered is True
    assert mgr.pending_agent_callbacks == [late_cb]
    assert mgr.pending_extra_replies == [late_extra]
    assert mgr._fired_tasks == []


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
    # Neither retracted nor delivered: proves the prunes below are targeted
    # rather than a blanket clear of the queue.
    survivor_extra = {"_callback_delivery_id": "id-survivor-drain", "origin": "task_result", "summary": "pending"}
    mgr.pending_agent_callbacks = [retracted_cb, active_cb]
    mgr.pending_extra_replies = [retracted_extra, active_extra, survivor_extra]

    rendered = core_module.LLMSessionManager.drain_agent_callbacks_for_llm(mgr)

    assert "shown" in rendered
    assert "cancelled" not in rendered
    assert mgr.pending_agent_callbacks == []
    # Both halves go: the retracted pair is purged, and active_cb was rendered
    # so its voice mirror is delivered too. Leaving the mirror behind lets the
    # next hot swap re-prime it, re-announcing what the model just said — the
    # same paired prune trigger_agent_callbacks already does on the voice path.
    assert mgr.pending_extra_replies == [survivor_extra]


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


async def test_passive_image_is_staged_before_text_drain_can_prune_callback():
    session = _FakeOmniOffline(delivered=True)
    session.stream_image = AsyncMock(return_value=None)
    mgr = _make_mgr(session=session)
    cb = {
        "_callback_delivery_id": "id-passive-image-text",
        "status": "completed",
        "summary": "camera event",
        "delivery_mode": "passive",
        "origin": "event",
        "media_images": ["image-b64"],
    }
    mgr.pending_agent_callbacks = [cb]

    # Production stages before draining: staging attaches images to
    # system_prefix_images, drain renders text, both sent with one user message.
    outcome = await core_module.LLMSessionManager._stage_passive_callback_media(
        mgr,
        [cb],
        session,
    )
    rendered = core_module.LLMSessionManager.drain_agent_callbacks_for_llm(mgr)

    # Offline images use stream_text prefixes, never stream_image.
    session.stream_image.assert_not_awaited()
    assert outcome["system_prefix_images"] == ["image-b64"]
    assert "camera event" in rendered
    assert mgr.pending_agent_callbacks == []


async def test_text_passive_media_stages_only_the_selected_prompt_snapshot():
    session = _FakeOmniOffline(delivered=True)
    session.stream_image = AsyncMock(return_value=None)
    mgr = _make_mgr(session=session)
    retracted = {
        "_callback_delivery_id": "id-retracted-media",
        "status": "completed",
        "summary": "must not render",
        "delivery_mode": "passive",
        "media_images": ["retracted-image"],
        DELIVERY_RETRACTED_KEY: True,
    }
    selected = {
        "_callback_delivery_id": "id-selected-media",
        "status": "completed",
        "summary": "selected callback",
        # Missing delivery_mode defaults proactive; image-bearing proactive cues STOP awaiting
        # their path. Explicitly mark this passive text-path case.
        "delivery_mode": "passive",
        "media_images": ["selected-image"],
    }
    mgr.pending_agent_callbacks = [retracted, selected]

    snapshot = core_module.LLMSessionManager._claim_agent_callbacks_for_llm(mgr)
    await core_module.LLMSessionManager._stage_passive_callback_media(
        mgr,
        snapshot,
        session,
    )
    rendered = core_module.LLMSessionManager.drain_agent_callbacks_for_llm(
        mgr,
        snapshot,
    )

    assert snapshot == [selected]
    session.stream_image.assert_not_awaited()
    assert "selected callback" in rendered
    assert "must not render" not in rendered
    assert mgr.pending_agent_callbacks == []


def test_passive_media_identity_is_stable_and_distinct_per_session():
    mgr = _make_mgr()
    first_session = SimpleNamespace()
    replacement_session = SimpleNamespace()
    first_identity = core_module.LLMSessionManager._session_media_identity(
        first_session
    )
    replacement_identity = (
        core_module.LLMSessionManager._session_media_identity(
            replacement_session
        )
    )
    cb = {
        "media_images": ["image-b64"],
        "_passive_media_session_id": first_identity,
        "_passive_media_staged_count": 1,
    }

    assert first_identity == core_module.LLMSessionManager._session_media_identity(
        first_session
    )
    assert first_identity != replacement_identity
    assert core_module.LLMSessionManager._callback_media_ready_for_session(
        mgr,
        cb,
        first_session,
    )
    assert not core_module.LLMSessionManager._callback_media_ready_for_session(
        mgr,
        cb,
        replacement_session,
    )


async def test_transient_native_passive_image_rejection_keeps_callback_for_retry():
    session = _make_voice_sess()
    session.stream_image = AsyncMock(
        return_value=ImageStageResult(
            accepted=False,
            mode="native",
            rejection_reason="raw_visual_delivery_blocked",
        )
    )
    mgr = _make_mgr(session=session)
    cb = {
        "_callback_delivery_id": "id-passive-image-retry",
        "status": "completed",
        "summary": "camera event",
        "delivery_mode": "passive",
        "origin": "event",
        "media_images": ["image-b64"],
    }
    mgr.pending_agent_callbacks = [cb]

    await core_module.LLMSessionManager._stage_passive_callback_media(
        mgr,
        [cb],
        session,
    )
    rendered = core_module.LLMSessionManager.drain_agent_callbacks_for_llm(mgr)

    # Maintainer criterion ①B: **transient** failures merit one more try; a network blip should not lose images.
    # Send no text this round either, preserving order instead of skipping to later cues.
    assert rendered == ""
    assert mgr.pending_agent_callbacks == [cb]
    assert cb["media_images"] == ["image-b64"]

    # Retain **only once**; if attachment fails again, best-effort sends text without images,
    # never withholding indefinitely.
    await core_module.LLMSessionManager._stage_passive_callback_media(
        mgr,
        [cb],
        session,
    )
    rendered_after_retry = core_module.LLMSessionManager.drain_agent_callbacks_for_llm(mgr)

    assert "camera event" in rendered_after_retry
    assert mgr.pending_agent_callbacks == []


async def test_offline_passive_media_uses_call_local_images_without_touching_queue():
    session = _FakeOmniOffline(delivered=True)
    session._pending_images = ["user-image"]
    session.stream_image = AsyncMock()
    mgr = _make_mgr(session=session)
    cb = {
        "_callback_delivery_id": "id-passive-partial-rollback",
        "status": "completed",
        "summary": "two callback images",
        "delivery_mode": "passive",
        "origin": "event",
        "media_images": ["callback-image-1", "callback-image-2"],
    }
    mgr.pending_agent_callbacks = [cb]

    outcome = await core_module.LLMSessionManager._stage_passive_callback_media(
        mgr,
        [cb],
        session,
    )
    rendered = core_module.LLMSessionManager.drain_agent_callbacks_for_llm(mgr)

    assert session._pending_images == ["user-image"]
    session.stream_image.assert_not_awaited()
    assert outcome["system_prefix_images"] == [
        "callback-image-1",
        "callback-image-2",
    ]
    assert cb["_passive_media_staged_count"] == 2
    assert "two callback images" in rendered
    assert mgr.pending_agent_callbacks == []


async def test_partial_native_passive_media_staging_requires_session_retirement():
    session = _make_voice_sess()
    session._is_gemini = False
    session._supports_native_image = True
    session._visual_delivery_mode = "native"

    async def stream_image(image_b64, **_kwargs):
        if image_b64 == "callback-image-2":
            raise RuntimeError("transient second-image failure")
        return ImageStageResult(accepted=True, mode="native")

    session.stream_image = AsyncMock(side_effect=stream_image)
    mgr = _make_mgr(session=session)
    cb = {
        "_callback_delivery_id": "id-native-partial-retire",
        "status": "completed",
        "summary": "two native callback images",
        "delivery_mode": "passive",
        "origin": "event",
        "media_images": ["callback-image-1", "callback-image-2"],
    }
    mgr.pending_agent_callbacks = [cb]

    outcome = await core_module.LLMSessionManager._stage_passive_callback_media(
        mgr,
        [cb],
        session,
    )

    assert outcome["safe_to_continue"] is False
    assert outcome["native_prefix_committed"] is True
    assert outcome["native_rejection_pending"] is True
    assert cb["_passive_media_staged_count"] == 1
    # Maintainer criterion: always deliver text, images best-effort; failed attachment means no image this round,
    # never a withheld notification stuck forever when both paths fail.
    assert core_module.LLMSessionManager.drain_agent_callbacks_for_llm(mgr) != ""


async def test_first_native_passive_media_exception_requires_session_retirement():
    session = _make_voice_sess()
    session._is_gemini = False
    session._supports_native_image = True
    session._visual_delivery_mode = "native"
    session.stream_image = AsyncMock(
        side_effect=RuntimeError("ambiguous first-image write failure")
    )
    mgr = _make_mgr(session=session)
    cb = {
        "_callback_delivery_id": "id-native-first-retire",
        "status": "completed",
        "summary": "one native callback image",
        "delivery_mode": "passive",
        "origin": "event",
        "media_images": ["callback-image-1"],
    }
    mgr.pending_agent_callbacks = [cb]

    outcome = await core_module.LLMSessionManager._stage_passive_callback_media(
        mgr,
        [cb],
        session,
    )

    assert outcome["safe_to_continue"] is False
    assert outcome["native_prefix_committed"] is False
    assert cb["_passive_media_staged_count"] == 0
    # Maintainer criterion: always deliver text, images best-effort; failed attachment means no image this round,
    # never a withheld notification stuck forever when both paths fail.
    assert core_module.LLMSessionManager.drain_agent_callbacks_for_llm(mgr) != ""


async def test_partial_gemini_passive_media_staging_requires_session_retirement():
    session = _make_voice_sess()
    session._is_gemini = True
    session._supports_native_image = True
    session._visual_delivery_mode = "native"

    async def stream_image(image_b64, **_kwargs):
        if image_b64 == "callback-image-2":
            raise RuntimeError("second Gemini image failed")
        return ImageStageResult(accepted=True, mode="native")

    session.stream_image = AsyncMock(side_effect=stream_image)
    mgr = _make_mgr(session=session)
    cb = {
        "_callback_delivery_id": "id-gemini-partial-retire",
        "status": "completed",
        "summary": "two Gemini callback images",
        "delivery_mode": "passive",
        "origin": "event",
        "media_images": ["callback-image-1", "callback-image-2"],
    }
    mgr.pending_agent_callbacks = [cb]

    outcome = await core_module.LLMSessionManager._stage_passive_callback_media(
        mgr,
        [cb],
        session,
    )

    assert outcome["safe_to_continue"] is False
    assert outcome["native_prefix_committed"] is True
    assert cb["_passive_media_staged_count"] == 1
    # Maintainer criterion: always deliver text, images best-effort; failed attachment means no image this round,
    # never a withheld notification stuck forever when both paths fail.
    assert core_module.LLMSessionManager.drain_agent_callbacks_for_llm(mgr) != ""


async def test_passive_native_async_rejection_invalidates_live_stage_outcome():
    session = _make_voice_sess()
    session._is_gemini = False
    rejection_handlers = []

    async def stream_image(_image_b64, *, on_rejected=None, **_kwargs):
        rejection_handlers.append(on_rejected)
        return ImageStageResult(accepted=True, mode="native")

    session.stream_image = AsyncMock(side_effect=stream_image)
    mgr = _make_mgr(session=session)
    cb = {
        "_callback_delivery_id": "id-native-async-reject",
        "status": "completed",
        "summary": "native callback image",
        "delivery_mode": "passive",
        "origin": "event",
        "media_images": ["callback-image"],
    }

    outcome = await core_module.LLMSessionManager._stage_passive_callback_media(
        mgr,
        [cb],
        session,
    )
    assert outcome["safe_to_continue"] is True
    assert outcome["native_prefix_committed"] is True
    assert outcome["native_rejection_pending"] is True

    rejection_handlers[0]("provider rejected callback image")

    assert outcome["rejected"] is True
    assert outcome["safe_to_continue"] is False
    assert outcome["rejection_observed"].is_set()
    assert cb["_passive_media_staged_count"] == 0


async def test_passive_handoff_required_never_calls_stream_or_annotation():
    session = _make_voice_sess()
    session.get_multimodal_turn_delivery = MagicMock(
        return_value=MultimodalTurnDelivery.HANDOFF_REQUIRED
    )
    session.stream_image = AsyncMock()
    session._analyze_image_with_vision_model = AsyncMock()
    mgr = _make_mgr(session=session)
    cb = {
        "_callback_delivery_id": "id-passive-image-handoff",
        "status": "completed",
        "summary": "camera event",
        "delivery_mode": "passive",
        "origin": "event",
        "media_images": ["image-b64"],
    }
    mgr.pending_agent_callbacks = [cb]

    outcome = await core_module.LLMSessionManager._stage_passive_callback_media(
        mgr,
        [cb],
        session,
    )

    assert outcome["safe_to_continue"] is True
    session.stream_image.assert_not_awaited()
    session._analyze_image_with_vision_model.assert_not_awaited()
    assert cb["media_images"] == ["image-b64"]
    # Maintainer criterion: always deliver text, images best-effort; failed attachment means no image this round,
    # never a withheld notification stuck forever when both paths fail.
    assert "camera event" in core_module.LLMSessionManager.drain_agent_callbacks_for_llm(mgr)
    assert mgr.pending_agent_callbacks == []


async def test_passive_image_description_never_rides_hot_swap_prime():
    session = _make_voice_sess()
    session.stream_image = AsyncMock(
        return_value=ImageStageResult(
            accepted=True,
            mode="external_description",
            description="桌上有一只白杯子",
        )
    )
    mgr = _make_mgr(session=session)
    mgr.pending_session = session
    cb = {
        "_callback_delivery_id": "id-passive-image-swap",
        "status": "completed",
        "summary": "camera event",
        "detail": "",
        "delivery_mode": "passive",
        "origin": "event",
        "source_kind": "unknown",
        "media_images": ["image-b64"],
    }
    mgr.pending_agent_callbacks = [cb]

    await core_module.LLMSessionManager._stage_passive_callback_media(
        mgr,
        [cb],
        session,
    )
    selected, rendered = (
        core_module.LLMSessionManager._select_passive_callbacks_for_swap_prime(mgr)
    )

    assert selected == []
    assert rendered == ""
    assert cb["detail"] == ""
    assert cb["media_images"] == ["image-b64"]
    assert mgr.pending_agent_callbacks == [cb]


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
    # Untouched by either prune: neither retracted nor delivered.
    survivor_extra = {
        "_callback_delivery_id": "id-survivor-gate",
        "origin": "task_result",
        "summary": "still pending",
    }
    mgr.pending_agent_callbacks = [topic_cb, normal_cb]
    mgr.pending_extra_replies = [topic_extra, normal_extra, survivor_extra]

    rendered = core_module.LLMSessionManager.drain_agent_callbacks_for_llm(mgr)

    assert "regular callback" in rendered
    assert "stale deep topic" not in rendered
    assert topic_future.done()
    assert topic_future.result() is False
    assert normal_future.done()
    assert normal_future.result() is True
    assert mgr.pending_agent_callbacks == []
    # normal_cb was delivered, so its voice mirror leaves with it; the topic
    # pair is cleared by the gate. See the paired-prune note in
    # test_drain_agent_callbacks_purges_retracted_callbacks_and_extras.
    assert mgr.pending_extra_replies == [survivor_extra]


async def test_voice_mode_reject_during_await_not_pruned():
    """TOCTOU regression (Codex P1): handle_messages can dispatch on_rejected while inject
    still awaits, before optimistic pruning, with cb already queued. Old handlers
    skipped re-addition as present, then success pruning by delivered_ids
    silently lost it. Set _rejected in handlers; after awaiting,
    rejected injections skip pruning and retain callbacks for retry."""
    sess = _make_voice_sess()  # is_active_response()→_is_responding，init False

    async def _inject(text, *, on_rejected=None):
        sess.inject_calls += 1
        # Simulate VAD overtaking: server already has active response when rejection arrives,
        # during await and before pruning.
        sess._is_responding = True
        if on_rejected is not None:
            on_rejected("response_already_active")
        # inject returns normally; rejection is asynchronous, not an exception.
    sess.inject_text_and_request_response = _inject

    mgr = _make_mgr(session=sess)
    cb = {"_callback_delivery_id": "id-toctou", "status": "completed", "summary": "keep me"}
    extra = {"_callback_delivery_id": "id-toctou", "origin": "task_result", "summary": "keep me"}
    mgr.pending_agent_callbacks = [cb]
    mgr.pending_extra_replies = [extra]

    await core_module.LLMSessionManager.trigger_agent_callbacks(mgr)
    await asyncio.sleep(0)

    # Key: retain cb in both queues for retry, without pruning.
    assert sess.inject_calls == 1
    assert len(mgr.pending_agent_callbacks) == 1
    assert mgr.pending_agent_callbacks[0]["_callback_delivery_id"] == "id-toctou"
    assert len(mgr.pending_extra_replies) == 1
    assert mgr.pending_extra_replies[0]["_callback_delivery_id"] == "id-toctou"
    # While busy (is_active_response=True), never immediately re-fire; retry through
    # _finalize_turn_after_emit after response.done, avoiding response_already_active loops.
    assert mgr._fired_tasks == []


async def test_voice_mode_concurrent_triggers_inject_once():
    """Concurrency regression (Codex P1): two simultaneous voice trigger tasks must
    serialize check-and-claim via _voice_proactive_inject_lock; only one
    injects, while the next lock-holder refilters empty queues and sends nothing."""
    release = asyncio.Event()
    entered_inject = asyncio.Event()

    sess = _make_voice_sess()

    async def _inject(text, *, on_rejected=None):
        sess.inject_calls += 1
        # Mark first inject's actual locked critical-section entry, then block it so the second
        # task deterministically competes for the lock, without guessing fixed tick counts.
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
    # Await first locked entry, then schedule the second task to block on that lock before
    # release, guaranteeing two tasks really compete for one snapshot.
    await asyncio.wait_for(entered_inject.wait(), timeout=5)
    await asyncio.sleep(0)
    release.set()
    # Local timeout fails quickly if _voice_proactive_inject_lock regresses to indefinite waiting,
    # rather than hanging until global CI timeout.
    await asyncio.wait_for(asyncio.gather(t1, t2), timeout=5)

    # Key: one injection, no duplicate speech.
    assert sess.inject_calls == 1
    assert mgr.pending_agent_callbacks == []
    assert mgr.pending_extra_replies == []


async def test_voice_mode_not_implemented_falls_back_to_hot_swap():
    """Defensive voice fallback: provider NotImplementedError drops proactive cb and
    retains pending_extra_replies for existing hot-swap injection.
    All current providers support manual inject (Qwen conversation.item.create,
    Gemini send_client_content), making this branch unreachable today;
    future-provider fallback is tested using an explicit NotImplementedError
    fake session."""
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
    # Exact equality, not truthiness: pin fallback preserving pending_extra_replies without duplicates.
    assert mgr.pending_extra_replies == original_extras  # hot-swap channel preserved


async def test_inject_gemini_routes_through_send_client_content():
    """Symmetry regression: Gemini also supports inject_text_and_request_response via
    send_client_content(turn_complete=True), mirroring create_response ->
    _create_response_gemini without NotImplementedError. Invoke real
    OmniRealtimeClient.inject_text_and_request_response directly, bypassing SM,
    verifying user-turn text injection and turn_complete=True response triggering."""
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

    # google.genai may be absent in tests; skip only then (SDK-equipped CI runs it).
    # Use importorskip, not broad except Exception: only ImportError skips;
    # real SDK errors propagate instead of disguising regressions as skips.
    import pytest
    pytest.importorskip("google.genai")

    await OmniRealtimeClient.inject_text_and_request_response(sess, "（系统通知）任务完成了。")

    assert sent.get("turn_complete") is True
    assert sent.get("turns") is not None


async def test_inject_gemini_missing_session_raises():
    """Unavailable Gemini sessions must raise during inject so callers retain cb,
    never silently succeed."""
    from main_logic.omni_realtime_client import OmniRealtimeClient

    sess = OmniRealtimeClient.__new__(OmniRealtimeClient)
    sess._fatal_error_occurred = False
    sess._is_gemini = True
    sess._gemini_session = None

    import pytest
    with pytest.raises(RuntimeError):
        await OmniRealtimeClient.inject_text_and_request_response(sess, "x")


async def test_route_inject_rejection_id_match():
    """Exact path: error echoes our stamped client event_id, matching/firing the corresponding handler."""
    from main_logic.omni_realtime_client import OmniRealtimeClient

    fired = []
    sess = OmniRealtimeClient.__new__(OmniRealtimeClient)
    sess._inject_rejection_handlers = {"event_inject_resp_x": lambda msg: fired.append(msg)}
    sess._route_inject_rejection("event_inject_resp_x", "response_already_active")
    assert fired == ["response_already_active"]
    assert sess._inject_rejection_handlers == {}  # popped


async def test_route_inject_rejection_content_fallback_no_id():
    """Fire only the current outcome handler for a no-ID response conflict."""
    from main_logic.omni_realtime_client import OmniRealtimeClient

    fired = []
    sess = OmniRealtimeClient.__new__(OmniRealtimeClient)
    sess._inject_rejection_handlers = {"k1": lambda msg: fired.append(msg)}
    sess._proactive_inject_awaiting_outcome = True  # inject just sent, awaiting outcome.
    sess._proactive_inject_outcome_token = "k1"
    # Missing err_event_id, but message indicates response conflict.
    sess._route_inject_rejection(None, "Conversation already has an active response")
    assert len(fired) == 1
    assert sess._inject_rejection_handlers == {}
    assert sess._proactive_inject_awaiting_outcome is False  # Window consumed.


async def test_route_inject_rejection_no_id_excludes_old_image_handler():
    """Do not fire an old image handler for a later no-ID response conflict."""
    from main_logic.omni_realtime_client import OmniRealtimeClient

    fired = []
    sess = OmniRealtimeClient.__new__(OmniRealtimeClient)
    sess._inject_rejection_handlers = {
        "event_old_callback_image": lambda msg: fired.append(("old", msg)),
        "event_current_response": lambda msg: fired.append(("current", msg)),
    }
    sess._proactive_inject_awaiting_outcome = True
    sess._proactive_inject_outcome_token = "event_current_response"

    sess._route_inject_rejection(None, "response_already_active")

    assert fired == [("current", "response_already_active")]
    assert "event_old_callback_image" in sess._inject_rejection_handlers
    assert "event_current_response" not in sess._inject_rejection_handlers


async def test_route_inject_rejection_no_id_but_not_awaiting_does_not_fire():
    """Ignore a no-ID response conflict when no proactive outcome is pending."""
    from main_logic.omni_realtime_client import OmniRealtimeClient

    fired = []
    sess = OmniRealtimeClient.__new__(OmniRealtimeClient)
    sess._inject_rejection_handlers = {"k1": lambda msg: fired.append(msg)}
    sess._proactive_inject_awaiting_outcome = False  # No waiting injection.
    sess._route_inject_rejection(None, "response_already_active")
    assert fired == []
    assert "k1" in sess._inject_rejection_handlers  # Unchanged.


async def test_route_inject_rejection_nonmatching_id_does_not_fire_fallback():
    """Codex P1: a client event_id matching no pending handlers means another
    response.create (create_response /tool-result continuation /
    signal_user_activity_end, all timestamp-stamped by send_event setdefault)
    was rejected, not our injection. Even response_already_active-like text or
    waiting injections **must not** fire handlers when ids are present but unmatched,
    or accepted callbacks restore and duplicate speech. Content fallback requires no client id at all."""
    from main_logic.omni_realtime_client import OmniRealtimeClient

    fired = []
    sess = OmniRealtimeClient.__new__(OmniRealtimeClient)
    sess._inject_rejection_handlers = {"event_inject_resp_ours": lambda msg: fired.append(msg)}
    sess._proactive_inject_awaiting_outcome = True  # Even waiting injections cannot be triggered by unrelated ids.
    # Another request's event_id with response-conflict text.
    sess._route_inject_rejection("event_create_response_other", "response_already_active")
    assert fired == []
    assert "event_inject_resp_ours" in sess._inject_rejection_handlers  # Unchanged.


async def test_route_inject_rejection_unrelated_error_does_not_fire():
    """No matching id and no response-conflict error (e.g. 503/quota): do not fire,
    avoiding unrelated errors restoring callbacks and duplicating delivery."""
    from main_logic.omni_realtime_client import OmniRealtimeClient

    fired = []
    sess = OmniRealtimeClient.__new__(OmniRealtimeClient)
    sess._inject_rejection_handlers = {"k1": lambda msg: fired.append(msg)}
    sess._proactive_inject_awaiting_outcome = True
    sess._route_inject_rejection(None, "503 service overloaded, try again later")
    assert fired == []
    assert sess._inject_rejection_handlers == {"k1": sess._inject_rejection_handlers["k1"]}


async def test_voice_mode_inject_exception_keeps_cbs_for_retry():
    """Voice injection exceptions other than NotImplementedError retain cb for retry."""
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

    # Prove inject called once, reaching injection exceptions rather than earlier guard returns.
    assert sess.inject_calls == 1
    assert mgr.pending_agent_callbacks == original


async def test_voice_mode_callback_image_rejection_before_inject_keeps_cb():
    """A native callback image rejection that arrives during stream_image must
    keep the callback queued and prevent a text-only response from starting."""
    sess = _make_voice_sess()

    async def _reject_image(
        _image_b64,
        *,
        bypass_rate_limit=False,
        cache_latest=True,
        on_rejected=None,
    ):
        assert bypass_rate_limit is True
        assert cache_latest is False
        assert on_rejected is not None
        on_rejected("callback image rejected")

    sess.stream_image = _reject_image
    mgr = _make_mgr(session=sess)
    cb = {
        "_callback_delivery_id": "id-image-rejected",
        "status": "completed",
        "summary": "inspect this image",
        "media_images": ["image-b64"],
    }
    extra = {
        "_callback_delivery_id": "id-image-rejected",
        "summary": "inspect this image",
    }
    mgr.pending_agent_callbacks = [cb]
    mgr.pending_extra_replies = [extra]
    mgr._schedule_proactive_retry = MagicMock()

    delivered = await core_module.LLMSessionManager.trigger_agent_callbacks(mgr)

    assert delivered is False
    assert sess.inject_calls == 0
    assert mgr.pending_agent_callbacks == [cb]
    assert mgr.pending_extra_replies == [extra]
    mgr._schedule_proactive_retry.assert_called_once_with(
        mgr.proactive_manager.min_gap_s
    )


async def test_partial_proactive_native_rejection_retires_active_session():
    """A raw callback prefix cannot survive without its paired callback text."""
    sess = _make_voice_sess()
    sess._is_gemini = False
    sess._supports_native_image = True
    sess._visual_delivery_mode = "native"
    sess._fatal_error_occurred = False
    sess.close = AsyncMock()

    async def _stream_image(image_b64, **_kwargs):
        if image_b64 == "callback-image-1":
            return ImageStageResult(accepted=True, mode="native")
        sess._visual_delivery_mode = "external_description"
        return ImageStageResult(
            accepted=False,
            mode="external_description",
        )

    sess.stream_image = AsyncMock(side_effect=_stream_image)
    mgr = _make_mgr(session=sess)
    mgr.end_session = AsyncMock()
    retirement_tasks = []

    def _fire_retirement(coro):
        task = asyncio.create_task(coro)
        retirement_tasks.append(task)
        return task

    mgr._fire_task = _fire_retirement
    mgr._schedule_proactive_retry = MagicMock()
    cb = {
        "_callback_delivery_id": "id-proactive-native-partial-reject",
        "status": "completed",
        "summary": "two native callback images",
        "media_images": ["callback-image-1", "callback-image-2"],
    }
    mgr.pending_agent_callbacks = [cb]

    delivered = await core_module.LLMSessionManager.trigger_agent_callbacks(mgr)
    await asyncio.gather(*retirement_tasks)

    assert delivered is False
    assert sess.inject_calls == 0
    assert sess._fatal_error_occurred is True
    sess.close.assert_awaited_once_with()
    mgr.end_session.assert_awaited_once_with(
        by_server=True,
        expected_session=sess,
    )
    assert mgr.pending_agent_callbacks == [cb]
    mgr._schedule_proactive_retry.assert_called_once_with(
        mgr.proactive_manager.min_gap_s
    )


async def test_voice_mode_drops_permanently_oversized_image_and_delivers_text():
    from main_logic.omni_realtime_client import ImageStageResult

    sess = _make_voice_sess()
    streamed = []

    async def _stream_image(
        image_b64,
        *,
        bypass_rate_limit=False,
        cache_latest=True,
        on_rejected=None,
    ):
        assert bypass_rate_limit is True
        assert cache_latest is False
        assert on_rejected is not None
        streamed.append(image_b64)
        if image_b64 == "oversized-image":
            return ImageStageResult(
                accepted=False,
                mode="external_description",
                rejection_reason="payload_too_large",
            )
        return ImageStageResult(accepted=True, mode="native")

    sess.stream_image = _stream_image
    mgr = _make_mgr(session=sess)
    cb = {
        "_callback_delivery_id": "id-oversized-image",
        "status": "completed",
        "summary": "deliver despite oversized media",
        "media_images": ["valid-prefix", "oversized-image", "valid-tail"],
    }
    mgr.pending_agent_callbacks = [cb]

    delivered = await core_module.LLMSessionManager.trigger_agent_callbacks(mgr)

    assert delivered is True
    assert streamed == ["valid-prefix", "oversized-image", "valid-tail"]
    assert sess.inject_calls == 1
    assert cb["media_images"] == ["valid-prefix", "valid-tail"]
    assert mgr.pending_agent_callbacks == []


@pytest.mark.parametrize(
    "rejection_reason",
    ["analysis_empty", "invalid_payload"],
)
async def test_voice_mode_drops_terminally_rejected_image_and_delivers_text(
    rejection_reason,
):
    from main_logic.omni_realtime_client import ImageStageResult

    sess = _make_voice_sess()

    async def _stream_image(
        _image_b64,
        *,
        bypass_rate_limit=False,
        cache_latest=True,
        on_rejected=None,
    ):
        assert bypass_rate_limit is True
        assert cache_latest is False
        assert on_rejected is not None
        return ImageStageResult(
            accepted=False,
            mode="external_description",
            rejection_reason=rejection_reason,
        )

    sess.stream_image = _stream_image
    mgr = _make_mgr(session=sess)
    cb = {
        "_callback_delivery_id": f"id-{rejection_reason}",
        "status": "completed",
        "summary": "deliver text despite unusable media",
        "media_images": ["unanalyzable-image"],
    }
    mgr.pending_agent_callbacks = [cb]
    mgr._schedule_proactive_retry = MagicMock()

    delivered = await core_module.LLMSessionManager.trigger_agent_callbacks(mgr)

    assert delivered is True
    assert "media_images" not in cb
    assert sess.inject_calls == 1
    assert mgr.pending_agent_callbacks == []
    mgr._schedule_proactive_retry.assert_not_called()


async def test_standard_step_callback_image_description_shares_inject_ticket():
    sess = _make_voice_sess()
    sess._supports_native_image = False
    sess._inject_rejection_handlers = {}
    expired = []

    async def _stream_image(
        _image_b64,
        *,
        bypass_rate_limit=False,
        cache_latest=True,
        on_rejected=None,
    ):
        assert bypass_rate_limit is True
        assert cache_latest is False
        assert on_rejected is not None
        return "画面里有一只猫。"

    async def _expire(event_id, _timeout):
        expired.append(event_id)

    def _fire_task(coro):
        asyncio.create_task(coro)

    sess.stream_image = _stream_image
    sess._expire_inject_rejection_handler = _expire
    sess._fire_task = _fire_task
    mgr = _make_mgr(session=sess)
    cb = {
        "_callback_delivery_id": "id-step-image",
        "status": "completed",
        "summary": "inspect this image",
        "media_images": ["image-b64"],
    }
    mgr.pending_agent_callbacks = [cb]

    delivered = await core_module.LLMSessionManager.trigger_agent_callbacks(mgr)
    await asyncio.sleep(0)

    assert delivered is True
    assert len(sess.injected_events) == 1
    assert len(sess.injected_events[0]) == 1
    description_event = sess.injected_events[0][0]
    assert description_event["item"]["content"] == [{
        "type": "input_text",
        "text": "[实时屏幕截图或相机画面]: 画面里有一只猫。",
    }]
    assert description_event["event_id"] in sess._inject_rejection_handlers
    assert expired == [description_event["event_id"]]


async def test_external_visual_callback_uses_description_even_for_native_capable_provider():
    """Delivery mode, not provider capability, decides whether raw callback media is legal."""
    sess = _make_voice_sess()
    sess._supports_native_image = True
    sess._inject_rejection_handlers = {}
    expired = []

    async def _stream_image(
        _image_b64,
        *,
        bypass_rate_limit=False,
        cache_latest=True,
        source=None,
        request_id=None,
        on_rejected=None,
    ):
        assert bypass_rate_limit is True
        assert cache_latest is False
        assert source == "callback"
        assert request_id == "id-external-image"
        assert on_rejected is not None
        return SimpleNamespace(
            accepted=True,
            mode="external_description",
            generation=23,
            description="画面里有一只猫。",
        )

    async def _expire(event_id, _timeout):
        expired.append(event_id)

    def _fire_task(coro):
        asyncio.create_task(coro)

    sess.stream_image = _stream_image
    sess._expire_inject_rejection_handler = _expire
    sess._fire_task = _fire_task
    mgr = _make_mgr(session=sess)
    cb = {
        "_callback_delivery_id": "id-external-image",
        "status": "completed",
        "summary": "inspect this image",
        "media_images": ["image-b64"],
    }
    mgr.pending_agent_callbacks = [cb]

    delivered = await core_module.LLMSessionManager.trigger_agent_callbacks(mgr)
    await asyncio.sleep(0)

    assert delivered is True
    assert len(sess.injected_events) == 1
    description_event = sess.injected_events[0][0]
    description_text = description_event["item"]["content"][0]["text"]
    assert description_text.startswith("[系统视觉感知结果，不是用户陈述]")
    assert "当前画面" in description_text
    assert "画面里有一只猫。" in description_text
    assert expired == [description_event["event_id"]]


async def test_external_visual_callback_analysis_failure_defers_text_delivery():
    """A false structured staging result cannot be reported as callback success."""
    sess = _make_voice_sess()
    staged = []

    async def _stream_image(
        _image_b64,
        *,
        bypass_rate_limit=False,
        cache_latest=True,
        source=None,
        request_id=None,
        on_rejected=None,
    ):
        staged.append((source, request_id))
        return SimpleNamespace(
            accepted=False,
            mode="external_description",
            generation=24,
            description=None,
        )

    sess.stream_image = _stream_image
    mgr = _make_mgr(session=sess)
    cb = {
        "_callback_delivery_id": "id-external-failed",
        "status": "completed",
        "summary": "inspect this image",
        "media_images": ["image-b64"],
    }
    mgr.pending_agent_callbacks = [cb]
    mgr._schedule_proactive_retry = MagicMock()

    delivered = await core_module.LLMSessionManager.trigger_agent_callbacks(mgr)

    assert delivered is False
    assert staged == [("callback", "id-external-failed")]
    assert sess.inject_calls == 0
    assert mgr.pending_agent_callbacks == [cb]
    mgr._schedule_proactive_retry.assert_called_once_with(
        mgr.proactive_manager.min_gap_s
    )


async def test_voice_mode_callback_image_rejection_after_inject_rearms_retry():
    sess = _make_voice_sess()
    image_rejections = []

    async def _stream_image(
        _image_b64,
        *,
        bypass_rate_limit=False,
        cache_latest=True,
        on_rejected=None,
    ):
        assert bypass_rate_limit is True
        assert cache_latest is False
        assert on_rejected is not None
        image_rejections.append(on_rejected)

    sess.stream_image = _stream_image
    mgr = _make_mgr(session=sess)
    cb = {
        "_callback_delivery_id": "id-image-late-rejected",
        "status": "completed",
        "summary": "inspect this image",
        "media_images": ["image-b64-1", "image-b64-2"],
    }
    extra = {
        "_callback_delivery_id": "id-image-late-rejected",
        "summary": "inspect this image",
    }
    mgr.pending_agent_callbacks = [cb]
    mgr.pending_extra_replies = [extra]
    mgr._schedule_proactive_retry = MagicMock()

    delivered = await core_module.LLMSessionManager.trigger_agent_callbacks(mgr)

    assert delivered is True
    assert mgr.pending_agent_callbacks == []
    assert mgr.pending_extra_replies == []
    assert len(image_rejections) == 2

    image_rejections[0]("callback image rejected")
    image_rejections[1]("second callback image rejected")

    assert mgr.pending_agent_callbacks == [cb]
    assert mgr.pending_extra_replies == [extra]
    mgr._schedule_proactive_retry.assert_called_once_with(
        mgr.proactive_manager.min_gap_s
    )


async def test_voice_mode_callback_image_rejection_after_ack_is_ignored():
    sess = _make_voice_sess()
    image_rejections = []

    async def _stream_image(
        _image_b64,
        *,
        bypass_rate_limit=False,
        cache_latest=True,
        on_rejected=None,
    ):
        assert bypass_rate_limit is True
        assert cache_latest is False
        image_rejections.append(on_rejected)

    sess.stream_image = _stream_image
    mgr = _make_mgr(session=sess)
    future = asyncio.get_running_loop().create_future()
    cb = {
        "_callback_delivery_id": "id-image-after-ack",
        "status": "completed",
        "summary": "inspect acknowledged image",
        "media_images": ["image-b64"],
        DELIVERY_ACK_FUTURE_KEY: future,
    }
    extra = {
        "_callback_delivery_id": "id-image-after-ack",
        "summary": "inspect acknowledged image",
    }
    mgr.pending_agent_callbacks = [cb]
    mgr.pending_extra_replies = [extra]
    mgr._schedule_proactive_retry = MagicMock()

    delivered = await core_module.LLMSessionManager.trigger_agent_callbacks(mgr)
    await asyncio.sleep(core_module._VOICE_PROACTIVE_ACK_GRACE_S + 0.02)

    assert delivered is True
    assert future.result() is True
    assert mgr.pending_agent_callbacks == []
    assert mgr.pending_extra_replies == []
    image_rejections[0]("late callback image rejection")
    assert mgr.pending_agent_callbacks == []
    assert mgr.pending_extra_replies == []
    mgr._schedule_proactive_retry.assert_not_called()


async def test_voice_mode_rejected_media_does_not_restore_superseded_callback():
    sess = _make_voice_sess()
    image_rejections = []

    async def _stream_image(
        _image_b64,
        *,
        bypass_rate_limit=False,
        cache_latest=True,
        on_rejected=None,
    ):
        assert bypass_rate_limit is True
        assert cache_latest is False
        image_rejections.append(on_rejected)

    sess.stream_image = _stream_image
    mgr = _make_mgr(session=sess)
    future = asyncio.get_running_loop().create_future()
    old_cb = {
        "_callback_delivery_id": "id-old-weather",
        "status": "completed",
        "summary": "old weather",
        "media_images": ["old-weather-image"],
        "coalesce_key": "weather",
        "_coalesce_submit_seq": 1,
        DELIVERY_ACK_FUTURE_KEY: future,
    }
    old_extra = {
        "_callback_delivery_id": "id-old-weather",
        "summary": "old weather",
        "coalesce_key": "weather",
        "_coalesce_submit_seq": 1,
    }
    mgr.pending_agent_callbacks = [old_cb]
    mgr.pending_extra_replies = [old_extra]
    mgr._schedule_proactive_retry = MagicMock()

    assert await core_module.LLMSessionManager.trigger_agent_callbacks(mgr) is True
    mgr._coalesce_latest = {"weather": 2}
    image_rejections[0]("superseded callback image rejected")

    assert old_cb[DELIVERY_RETRACTED_KEY] is True
    assert future.result() is False
    assert mgr.pending_agent_callbacks == []
    assert mgr.pending_extra_replies == []
    mgr._schedule_proactive_retry.assert_not_called()


async def test_voice_mode_coalescing_queues_newer_cue_after_media_commit():
    sess = _make_voice_sess()
    stream_started = asyncio.Event()
    release_stream = asyncio.Event()
    streamed = []

    async def _stream_image(
        image_b64,
        *,
        bypass_rate_limit=False,
        cache_latest=True,
        on_rejected=None,
    ):
        assert bypass_rate_limit is True
        assert cache_latest is False
        assert on_rejected is not None
        streamed.append(image_b64)
        stream_started.set()
        await release_stream.wait()

    sess.stream_image = _stream_image
    mgr = _make_mgr(session=sess)
    old_cb = {
        "_callback_delivery_id": "id-old-weather",
        "status": "completed",
        "summary": "old weather",
        "media_images": ["old-weather-image"],
        "coalesce_key": "weather",
        "_coalesce_submit_seq": 1,
    }
    old_extra = {
        "_callback_delivery_id": "id-old-weather",
        "summary": "old weather",
        "coalesce_key": "weather",
        "_coalesce_submit_seq": 1,
    }
    mgr.pending_agent_callbacks = [old_cb]
    mgr.pending_extra_replies = [old_extra]
    mgr._coalesce_latest = {"weather": 1}

    delivery = asyncio.create_task(
        core_module.LLMSessionManager.trigger_agent_callbacks(mgr)
    )
    await stream_started.wait()

    newer_cb = {
        "status": "completed",
        "summary": "new weather",
        "coalesce_key": "weather",
        "_coalesce_submit_seq": 2,
    }
    core_module.LLMSessionManager.enqueue_agent_callback(mgr, newer_cb)
    assert old_cb.get(DELIVERY_RETRACTED_KEY) is not True

    release_stream.set()
    assert await delivery is True

    assert streamed == ["old-weather-image"]
    assert len(sess.injected) == 1
    assert "old weather" in sess.injected[0]
    assert "new weather" not in sess.injected[0]
    assert old_cb.get("_voice_delivery_committed") is None
    assert mgr.pending_agent_callbacks == [newer_cb]
    assert mgr.pending_extra_replies[0]["summary"] == "new weather"


async def test_voice_mode_build_failure_clears_media_commit_marker(monkeypatch):
    import pytest
    import main_logic.core.proactive as proactive_module

    sess = _make_voice_sess()

    async def _stream_image(
        _image_b64,
        *,
        bypass_rate_limit=False,
        cache_latest=True,
        on_rejected=None,
    ):
        assert bypass_rate_limit is True
        assert cache_latest is False
        assert on_rejected is not None

    sess.stream_image = _stream_image
    mgr = _make_mgr(session=sess)
    old_cb = {
        "_callback_delivery_id": "id-old-weather",
        "status": "completed",
        "summary": "old weather",
        "media_images": ["old-weather-image"],
        "coalesce_key": "weather",
        "_coalesce_submit_seq": 1,
    }
    mgr.pending_agent_callbacks = [old_cb]
    mgr.pending_extra_replies = [{
        "_callback_delivery_id": "id-old-weather",
        "summary": "old weather",
        "coalesce_key": "weather",
        "_coalesce_submit_seq": 1,
    }]
    mgr._coalesce_latest = {"weather": 1}
    monkeypatch.setattr(
        proactive_module,
        "_build_callback_instruction",
        MagicMock(side_effect=RuntimeError("instruction build failed")),
    )

    with pytest.raises(RuntimeError, match="instruction build failed"):
        await core_module.LLMSessionManager.trigger_agent_callbacks(mgr)

    assert old_cb.get("_voice_delivery_committed") is None
    core_module.LLMSessionManager.enqueue_agent_callback(
        mgr,
        {
            "status": "completed",
            "summary": "new weather",
            "coalesce_key": "weather",
            "_coalesce_submit_seq": 2,
        },
    )
    assert old_cb[DELIVERY_RETRACTED_KEY] is True
    assert [cb["summary"] for cb in mgr.pending_agent_callbacks] == [
        "new weather"
    ]


async def test_voice_mode_unstamped_cb_still_pruned_via_object_id_fallback():
    """Prune unstamped callbacks through the object-ID fallback after delivery."""
    sess = _make_voice_sess()
    mgr = _make_mgr(session=sess)
    # Construct cb without _callback_delivery_id, simulating enqueue_agent_callback bypass.
    unstamped_cb = {"status": "completed", "summary": "unstamped"}
    mgr.pending_agent_callbacks = [unstamped_cb]
    # Empty extras are valid without enqueue-created paired entries.
    mgr.pending_extra_replies = []

    await core_module.LLMSessionManager.trigger_agent_callbacks(mgr)
    await asyncio.sleep(0)

    assert len(sess.injected) == 1
    # Key: unstamped cb removed via id() fallback too, preventing redelivery next retry.
    assert mgr.pending_agent_callbacks == []


async def test_send_event_preserves_caller_stamped_event_id():
    """``send_event`` preserves explicit caller ``event_id``, never overwriting with timestamps.
    ``inject_text_and_request_response`` on_rejected depends on server error.event_id
    echoing caller-registered IDs to match
    _inject_rejection_handlers. Pin Codex r3249069126."""
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
    """Text delivery keeps callbacks when another proactive path owns phase1."""
    sess = _FakeOmniOffline()
    mgr = _make_mgr(session=sess)
    mgr.pending_agent_callbacks = [{"status": "completed", "summary": "hello"}]

    # Simulate router-owned proactive work.
    await mgr.state.fire(SessionEvent.PROACTIVE_START)
    assert mgr.state.phase is ProactivePhase.PHASE1

    await core_module.LLMSessionManager.trigger_agent_callbacks(mgr)

    # SM rejects without prompt_ephemeral; retain callbacks.
    assert sess.called_with == []
    assert mgr.pending_agent_callbacks == [{"status": "completed", "summary": "hello"}]
    assert mgr.state.phase is ProactivePhase.PHASE1  # Existing proactive ownership unchanged.


async def test_text_mode_sm_denied_when_session_responding():
    """SM rejects text while AI responds."""
    sess = _FakeOmniOffline(is_responding=True)
    mgr = _make_mgr(session=sess)
    mgr.pending_agent_callbacks = [{"status": "completed", "summary": "queued"}]

    await core_module.LLMSessionManager.trigger_agent_callbacks(mgr)

    assert sess.called_with == []
    # Retain callbacks for next-round retry.
    assert mgr.pending_agent_callbacks == [{"status": "completed", "summary": "queued"}]
    assert mgr.state.phase is ProactivePhase.IDLE


async def test_goodbye_silent_defers_agent_callbacks_and_keeps_queue():
    """Silent cat mode prevents proactive callbacks bypassing frontend timers for direct delivery."""
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
    """Manager proactive cues must keep waiting in silent cat mode."""
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
    # goodbye_silent bypasses the manager, so the coalesce_key arg is carried
    # onto the callback dict (plus a submission seq) for the enqueue path.
    assert cb["coalesce_key"] == "same-source"
    assert isinstance(cb["_coalesce_submit_seq"], int)
    assert mgr.pending_extra_replies == [
        {
            "_callback_delivery_id": cb["_callback_delivery_id"],
                "coalesce_key": "same-source",
                "_coalesce_submit_seq": cb["_coalesce_submit_seq"],
                CALLBACK_EXPIRES_AT_KEY: None,
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
    # The ack now names the start it answers (#2539), so match the call opener
    # rather than the whole call: the args are not what this guard is about.
    notify_marker = "await self.send_session_started(input_mode"

    clear_pos = normalized_source.index(clear_marker)
    success_pos = normalized_source.rindex(success_marker, 0, clear_pos)
    notify_pos = normalized_source.index(notify_marker, clear_pos)

    assert success_pos < clear_pos < notify_pos


def test_start_session_seeds_topic_hooks_with_full_global_locale():
    """Topic hooks must keep zh-TW when start_session falls back to global language."""
    normalized_source = re.sub(r"\s+", " ", _read_core_package_source())

    assert "topic_language_seed = normalize_language_code(get_global_language_full(), format='full')" in normalized_source
    assert "self.user_language = topic_language_seed" in normalized_source
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


@pytest.mark.parametrize("transition", ["text", "voice_start", "voice_active", "voice_teardown"])
async def test_drained_callback_restore_rechecks_current_voice_gate(transition):
    mgr = _make_mgr(session=_FakeOmniOffline(delivered=True))
    mgr._normalize_context_text_for_source = lambda _source, text: text
    for kind, summary in [("topic", "topic restore notice"), ("agent", "ordinary restore notice")]:
        callback = {
            "origin": "event", "source_kind": kind,
            "summary": summary, "delivery_mode": "proactive",
        }
        if kind == "topic":
            callback["channel"] = "topic_hook"
        mgr.enqueue_agent_callback(callback)
    callbacks = list(mgr.pending_agent_callbacks)
    extras = list(mgr.pending_extra_replies)
    assert len(callbacks) == len(extras) == 2
    rendered = mgr.drain_agent_callbacks_for_llm()
    assert "topic restore notice" in rendered
    assert not mgr.pending_agent_callbacks
    assert not mgr.pending_extra_replies

    if transition == "voice_start":
        mgr._starting_session_count = 1
        mgr._starting_input_mode = "audio"
    elif transition == "voice_active":
        mgr.is_active = True
        mgr.input_mode = "audio"
    elif transition == "voice_teardown":
        mgr.session = _make_voice_sess()
        mgr.is_active = True
        mgr.input_mode = "text"
    if transition != "text":
        # The start sweep runs while this text turn owns the drained snapshot.
        mgr._drop_pending_topic_hooks_for_voice()
        assert not callbacks[0].get(DELIVERY_RETRACTED_KEY)

    mgr._requeue_undelivered_callbacks(callbacks, extras)
    expected_callbacks = callbacks if transition == "text" else callbacks[1:]
    expected_extras = extras if transition == "text" else extras[1:]
    assert mgr.pending_agent_callbacks == expected_callbacks
    assert mgr.pending_extra_replies == expected_extras
    assert bool(callbacks[0].get(DELIVERY_RETRACTED_KEY)) == (transition != "text")
    assert "ordinary restore notice" in mgr.drain_agent_callbacks_for_llm()


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
    # Non-topic_hook cues enter normal delivery.
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


async def test_deliver_agent_callbacks_text_drops_callback_expired_during_claim():
    """An out-of-queue snapshot is rechecked after the proactive claim awaits."""
    sess = _FakeOmniOffline(delivered=True)
    mgr = _make_mgr(session=sess)
    future = asyncio.get_running_loop().create_future()
    cb = {
        "_callback_delivery_id": "id-expired-during-claim",
        "status": "completed",
        "summary": "stale event",
        CALLBACK_EXPIRES_AT_KEY: time.monotonic() + 60,
        DELIVERY_ACK_FUTURE_KEY: future,
    }
    extra = {
        "_callback_delivery_id": "id-expired-during-claim",
        "origin": "event",
        "summary": "stale event",
        CALLBACK_EXPIRES_AT_KEY: cb[CALLBACK_EXPIRES_AT_KEY],
    }
    mgr.pending_extra_replies = [extra]
    original_fire = mgr.state.fire

    async def _fire_and_expire(event, **payload):
        await original_fire(event, **payload)
        if event is SessionEvent.PROACTIVE_PHASE2:
            cb[CALLBACK_EXPIRES_AT_KEY] = time.monotonic() - 1

    mgr.state.fire = _fire_and_expire

    snapshot = [cb]
    delivered = await core_module.LLMSessionManager._deliver_agent_callbacks_text(
        mgr, snapshot
    )

    assert delivered is False
    assert sess.called_with == []
    assert snapshot == []
    assert mgr.pending_extra_replies == []
    assert future.done() and future.result() is False
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
    """Happy path: START ->CLAIM ->PHASE2 ->DONE, returning IDLE."""
    sess = _FakeOmniOffline(delivered=True)
    mgr = _make_mgr(session=sess)
    mgr.pending_agent_callbacks = [{"status": "completed", "summary": "ok"}]

    seen: list[tuple[SessionEvent, dict]] = []
    mgr.state.subscribe(None, lambda ev, p: seen.append((ev, dict(p))))

    await core_module.LLMSessionManager.trigger_agent_callbacks(mgr)
    # Dispatch subscriber callbacks asynchronously.
    for _ in range(3):
        await asyncio.sleep(0)

    event_names = [ev for ev, _ in seen]
    assert event_names == [
        SessionEvent.PROACTIVE_START,
        SessionEvent.PROACTIVE_CLAIM,
        SessionEvent.PROACTIVE_PHASE2,
        SessionEvent.PROACTIVE_DONE,
    ]

    # CLAIM payload includes generated sid.
    claim_payload = seen[1][1]
    assert claim_payload["sid"] == mgr.current_speech_id

    # Final phase returns IDLE.
    assert mgr.state.phase is ProactivePhase.IDLE
    assert mgr.state.proactive_sid is None

    # prompt_ephemeral called, callbacks cleared.
    assert len(sess.called_with) == 1
    assert mgr.pending_agent_callbacks == []


async def test_text_mode_exception_still_fires_done():
    """prompt_ephemeral exceptions restore callbacks and still fire PROACTIVE_DONE."""
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
    # Exception path restores callbacks (core except branch).
    assert mgr.pending_agent_callbacks == original


async def test_contextvar_reset_after_delivery():
    """Restore _proactive_expected_sid=None after prompt_ephemeral."""
    sess = _FakeOmniOffline(delivered=True)
    mgr = _make_mgr(session=sess)
    mgr.pending_agent_callbacks = [{"status": "completed", "summary": "ctx"}]

    assert core_module._proactive_expected_sid.get() is None
    await core_module.LLMSessionManager.trigger_agent_callbacks(mgr)
    assert core_module._proactive_expected_sid.get() is None


# ─────────────────────────────────────────────────────────────────────────────
# Mutual exclusion between text triggers and router proactive work.
# ─────────────────────────────────────────────────────────────────────────────

async def test_already_claimed_denies_agent_callback():
    """Router-owned phase1 blocks subsequent agent callback prompt_ephemeral."""
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
    """Actual concurrency: one barrier releases both contenders;
    atomic check-and-claim admits exactly one winner to prompt_ephemeral."""
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
    # Both tasks block at one barrier, then release together.
    await asyncio.sleep(0)
    barrier.set()
    await asyncio.gather(t1, t2)

    # Exactly one winner: router_won=True rejects agent; False succeeds agent.
    if router_won.result() is True:
        # Router wins: agent cannot enter prompt_ephemeral.
        assert sess.called_with == []
        assert mgr.pending_agent_callbacks == [{"status": "completed", "summary": "race"}]
        assert mgr.state.phase is ProactivePhase.PHASE1
    else:
        # Agent wins: router rejected; agent finishes prompt_ephemeral and returns IDLE.
        assert len(sess.called_with) == 1
        assert mgr.pending_agent_callbacks == []
        assert mgr.state.phase is ProactivePhase.IDLE


async def test_user_input_between_claim_and_lock_is_detected():
    """CodeRabbit regression: between try_start_proactive=True and acquiring self.lock,
    USER_INPUT may mark_user_input_preempt and rotate user sid.
    _deliver_agent_callbacks_text must recheck sticky preempt while locked,
    never overwriting the newly assigned user sid with proactive sid."""
    sess_wait = asyncio.Event()

    class _SlowSess(OmniOfflineClient):
        _is_responding = False

        def __init__(self):
            pass

        async def prompt_ephemeral(self, instruction, *, images=None, on_committed=None, on_committed_text=None):
            await sess_wait.wait()
            return True

        def update_max_response_length(self, *_a, **_kw):
            pass

    sess = _SlowSess()
    mgr = _make_mgr(session=sess)
    mgr.pending_agent_callbacks = [{"status": "completed", "summary": "slow"}]

    # SM claim succeeds, phase ->PHASE1.
    assert await mgr.state.try_start_proactive(session=sess) is True

    # Simulate user takeover after claim before _deliver: atomically preempt/rotate sid under self.lock.
    async with mgr.lock:
        pre_user_sid = "user_fresh_sid"
        mgr.current_speech_id = pre_user_sid
        mgr.state.mark_user_input_preempt()
    await mgr.state.fire(SessionEvent.USER_INPUT, sid=pre_user_sid)

    # Directly call _deliver_agent_callbacks_text, bypassing trigger_agent_callbacks
    # claim already manually simulated alongside user takeover.
    callbacks_snapshot = [{"status": "completed", "summary": "slow"}]
    await core_module.LLMSessionManager._deliver_agent_callbacks_text(mgr, callbacks_snapshot)

    # Key: current_speech_id remains user sid, never overwritten by proactive.
    assert mgr.current_speech_id == pre_user_sid
    # prompt_ephemeral uncalled.
    sess_wait.set()  # Defensive: accidental invocation cannot block forever.
    # Give one tick to prove prompt_ephemeral never ran.
    await asyncio.sleep(0)
    # Restore callbacks_snapshot to pending on bail.
    assert {"status": "completed", "summary": "slow"} in mgr.pending_agent_callbacks


# ─────────────────────────────────────────────────────────────────────────────
# user_input sticky preemption remains active.
# ─────────────────────────────────────────────────────────────────────────────

async def test_user_input_during_agent_delivery_sets_preempted():
    """USER_INPUT during text delivery sets sticky _preempted, visible even after phase reset."""
    sess_wait = asyncio.Event()

    class _SlowSess(OmniOfflineClient):
        _is_responding = False

        def __init__(self):
            pass  # Skip superclass initialization.

        async def prompt_ephemeral(self, instruction, *, images=None, on_committed=None, on_committed_text=None):
            # Simulate slow LLM with intervening user takeover.
            await sess_wait.wait()
            return True

        def update_max_response_length(self, *_a, **_kw):
            pass

    mgr = _make_mgr(session=_SlowSess())
    mgr.pending_agent_callbacks = [{"status": "completed", "summary": "slow"}]

    # Run callback delivery and asynchronous USER_INPUT concurrently.
    task = asyncio.create_task(core_module.LLMSessionManager.trigger_agent_callbacks(mgr))

    # Await phase1.
    for _ in range(20):
        await asyncio.sleep(0.01)
        if mgr.state.phase in (ProactivePhase.PHASE1, ProactivePhase.PHASE2):
            break
    assert mgr.state.phase in (ProactivePhase.PHASE1, ProactivePhase.PHASE2), (
        "等待 trigger_agent_callbacks 进入 proactive phase 超时"
    )

    await mgr.state.fire(SessionEvent.USER_INPUT, sid="user_new_sid")
    # Sticky flag should have flipped.
    assert mgr.state._preempted is True
    assert mgr.state.owner is TurnOwner.USER

    # Release prompt_ephemeral.
    sess_wait.set()
    await task

    # PROACTIVE_DONE returns phase IDLE and clears _preempted,
    # but retains USER ownership when preempted; DONE never overrides it.
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

    # The established silence is uniform: neither a completed episode nor a
    # verified runner start can bypass it.
    short_without_start_session = _FakeOmniOffline(delivered=True)
    short_without_start_mgr = _make_mgr(session=short_without_start_session)
    await core_module.LLMSessionManager.trigger_cat_greeting(
        short_without_start_mgr, 179, "cat1", False, episode=episode,
    )
    assert short_without_start_session.called_with == []

    short_scene_session = _FakeOmniOffline(delivered=True)
    short_scene_mgr = _make_mgr(session=short_scene_session)
    await core_module.LLMSessionManager.trigger_cat_greeting(
        short_scene_mgr,
        10,
        "cat2",
        False,
        episode=episode,
    )
    assert short_scene_session.called_with == []

    # A start without strict done is subject to the same minimum dwell time.
    short_neutral_session = _FakeOmniOffline(delivered=True)
    short_neutral_mgr = _make_mgr(session=short_neutral_session)
    await core_module.LLMSessionManager.trigger_cat_greeting(
        short_neutral_mgr,
        10,
        "cat3",
        False,
    )
    assert short_neutral_session.called_with == []

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


# ---------------------------------------------------------------------------
# Per-turn image budget at the two real consumption points
#
# Both paths flatten EVERY pending callback into one model turn. Batches build
# up whenever the proactive claim is denied (user mid-conversation) and release
# together, so a per-push cap alone does not bound the request. An over-limit
# request also re-queues its whole snapshot on failure — it would retry forever
# and wedge every later cue behind it.
# ---------------------------------------------------------------------------


def _budget_cb(name: str, image_count: int) -> dict:
    return {
        "_callback_delivery_id": "id-%s" % name,
        "status": "completed",
        "summary": "cue %s" % name,
        "media_images": ["%s-img%d" % (name, i) for i in range(image_count)],
    }


async def test_text_mode_bounds_images_across_one_proactive_turn():
    from main_logic.proactive_delivery import CALLBACK_IMAGE_MAX_COUNT

    assert CALLBACK_IMAGE_MAX_COUNT == 8
    sess = _CapturingImageOmniOffline()
    mgr = _make_mgr(session=sess)
    cbs = [_budget_cb("a", 4), _budget_cb("b", 4), _budget_cb("c", 4)]
    mgr.pending_agent_callbacks = list(cbs)

    delivered = await core_module.LLMSessionManager.trigger_agent_callbacks(mgr)

    assert delivered is True
    assert len(sess.image_batches) == 1
    assert sess.image_batches[0] == [
        "a-img0", "a-img1", "a-img2", "a-img3",
        "b-img0", "b-img1", "b-img2", "b-img3",
    ]
    # The third cue is DEFERRED, not dropped: it goes back on the queue for the
    # next turn rather than silently losing its images.
    assert mgr.pending_agent_callbacks == [cbs[2]]


async def test_text_mode_under_budget_batch_is_untouched():
    """The bound must not disturb ordinary multi-cue batches."""
    sess = _CapturingImageOmniOffline()
    mgr = _make_mgr(session=sess)
    cbs = [_budget_cb("a", 1), _budget_cb("b", 2)]
    mgr.pending_agent_callbacks = list(cbs)

    delivered = await core_module.LLMSessionManager.trigger_agent_callbacks(mgr)

    assert delivered is True
    assert sess.image_batches == [["a-img0", "b-img0", "b-img1"]]
    assert mgr.pending_agent_callbacks == []


async def test_voice_mode_bounds_images_across_one_proactive_turn():
    sess = _make_voice_sess()
    streamed: list[str] = []

    async def _stream_image(
        image_b64,
        *,
        bypass_rate_limit=False,
        cache_latest=True,
        on_rejected=None,
    ):
        streamed.append(image_b64)

    sess.stream_image = _stream_image
    mgr = _make_mgr(session=sess)
    cbs = [_budget_cb("a", 4), _budget_cb("b", 4), _budget_cb("c", 4)]
    mgr.pending_agent_callbacks = list(cbs)
    mgr.pending_extra_replies = []

    delivered = await core_module.LLMSessionManager.trigger_agent_callbacks(mgr)

    assert delivered is True
    assert streamed == [
        "a-img0", "a-img1", "a-img2", "a-img3",
        "b-img0", "b-img1", "b-img2", "b-img3",
    ]
    # Voice prunes pending only after a successful inject, so the deferred cue
    # is re-queued by construction — it was simply never taken.
    assert mgr.pending_agent_callbacks == [cbs[2]]


def test_text_drain_stops_at_a_held_media_callback():
    """Holding one back must hold back everything behind it.

    The drain has no channel for media_images, so a proactive media callback
    waits for the proactive path. Skipping past it to drain a LATER cue would
    put the later cue in front of the earlier one — the model would hear them
    out of the order they were queued in.
    """
    mgr = _make_mgr(session=_FakeOmniOffline(delivered=True))
    image_cb = _budget_cb("with-image", 1)
    later_text = {
        "_callback_delivery_id": "id-later",
        "status": "completed",
        "summary": "queued after the image",
    }
    mgr.pending_agent_callbacks = [image_cb, later_text]

    rendered = core_module.LLMSessionManager.drain_agent_callbacks_for_llm(mgr)

    assert rendered == ""
    # Neither is consumed: the suffix waits with the cue it queued behind.
    assert mgr.pending_agent_callbacks == [image_cb, later_text]
    assert mgr.pending_agent_callbacks[0]["media_images"] == ["with-image-img0"]


def test_text_drain_takes_the_prefix_before_a_held_media_callback():
    """Stopping is not the same as refusing: earlier text still drains."""
    mgr = _make_mgr(session=_FakeOmniOffline(delivered=True))
    earlier_text = {
        "_callback_delivery_id": "id-earlier",
        "status": "completed",
        "summary": "queued before the image",
    }
    image_cb = _budget_cb("with-image", 1)
    mgr.pending_agent_callbacks = [earlier_text, image_cb]

    rendered = core_module.LLMSessionManager.drain_agent_callbacks_for_llm(mgr)

    assert "queued before the image" in rendered
    assert mgr.pending_agent_callbacks == [image_cb]


def test_text_drain_returns_empty_when_every_callback_carries_media():
    """No text to render must not mean the images get dropped either."""
    mgr = _make_mgr(session=_FakeOmniOffline(delivered=True))
    only_image = _budget_cb("only-image", 2)
    mgr.pending_agent_callbacks = [only_image]

    rendered = core_module.LLMSessionManager.drain_agent_callbacks_for_llm(mgr)

    assert rendered == ""
    assert mgr.pending_agent_callbacks == [only_image]


def test_passive_media_callbacks_deliver_text_with_images_best_effort():
    """Text always goes out; images ride along only if they were staged.

    Holding a passive cue back until its images attach would strand it whenever
    neither path can take it. Dropping it entirely would lose the notice. So the
    text is delivered either way, and the images are best-effort: attached when
    ``_stage_passive_callback_media`` already claimed them for this session,
    skipped (with a warning) when it did not.

    A PROACTIVE media cue is different and still stops the queue -- it is
    waiting for the proactive path, which can carry its images.
    """
    mgr = _make_mgr(session=_FakeOmniOffline(delivered=True))
    passive_cb = _budget_cb("passive-image", 1)
    passive_cb["delivery_mode"] = "passive"
    proactive_cb = _budget_cb("proactive-image", 1)
    mgr.pending_agent_callbacks = [passive_cb, proactive_cb]

    rendered = core_module.LLMSessionManager.drain_agent_callbacks_for_llm(mgr)

    # Image unattached, but text delivered and cue removed from queue.
    assert "cue passive-image" in rendered
    # Image-bearing proactive cue still awaits its own path.
    assert mgr.pending_agent_callbacks == [proactive_cb]

async def test_deferred_image_overflow_is_re_driven_on_the_text_path():
    """A deferred tail must not wait for an unrelated event to wake it.

    The manager's queue is already empty by this point and the text path has no
    response.done to re-fire trigger, so without an armed retry the ninth cue
    of a nine-image batch sits in pending indefinitely.
    """
    sess = _CapturingImageOmniOffline()
    mgr = _make_mgr(session=sess)
    mgr._schedule_proactive_retry = MagicMock()
    cbs = [_budget_cb("c%d" % i, 1) for i in range(9)]
    mgr.pending_agent_callbacks = list(cbs)

    delivered = await core_module.LLMSessionManager.trigger_agent_callbacks(mgr)

    assert delivered is True
    assert len(sess.image_batches[0]) == 8
    assert [cb["summary"] for cb in mgr.pending_agent_callbacks] == ["cue c8"]
    mgr._schedule_proactive_retry.assert_called_once_with(
        mgr.proactive_manager.min_gap_s
    )


async def test_no_retry_is_armed_when_nothing_was_deferred():
    """The retry is for a deferred tail only — not every proactive turn."""
    sess = _CapturingImageOmniOffline()
    mgr = _make_mgr(session=sess)
    mgr._schedule_proactive_retry = MagicMock()
    mgr.pending_agent_callbacks = [_budget_cb("a", 2)]

    delivered = await core_module.LLMSessionManager.trigger_agent_callbacks(mgr)

    assert delivered is True
    assert mgr.pending_agent_callbacks == []
    mgr._schedule_proactive_retry.assert_not_called()


async def test_image_overflow_keeps_fifo_order_when_delivery_fails():
    """The deferred tail must not overtake the prefix it was split from.

    The exception path re-queues the delivered prefix, so re-queuing the
    overflow at split time would leave [C, A, B] and the next turn would speak
    the cues out of order.
    """
    sess = _FakeOmniOffline(raise_exc=RuntimeError("provider rejected the request"))
    mgr = _make_mgr(session=sess)
    cbs = [_budget_cb("a", 4), _budget_cb("b", 4), _budget_cb("c", 4)]
    mgr.pending_agent_callbacks = list(cbs)

    delivered = await core_module.LLMSessionManager.trigger_agent_callbacks(mgr)

    assert delivered is False
    assert [cb["summary"] for cb in mgr.pending_agent_callbacks] == [
        "cue a", "cue b", "cue c",
    ]


async def test_preemption_during_the_hint_cancel_aborts_before_prompting():
    """The cancel write is a yield point, so it is also a preempt window.

    Re-filtering callbacks after it is not enough: a stale CALLBACK and a stale
    TURN are different things. Without the re-check the user can take the
    session during that await and still receive an unrelated proactive prompt.
    """
    sess = _CapturingImageOmniOffline()
    mgr = _make_mgr(session=sess)
    mgr.topic_hook_delivery_allowed = lambda: True
    mgr.send_topic_hint = AsyncMock(return_value=True)

    async def _cancel(**_kw):
        # The user grabs the session while the cancel write is in flight.
        mgr.current_speech_id = "user-took-over"

    mgr.send_cancel_topic_hint = AsyncMock(side_effect=_cancel)
    heavy = _budget_cb("heavy", 8)
    topic = _budget_cb("topic", 1)
    topic["channel"] = "topic_hook"
    topic["source_kind"] = "topic"
    mgr.pending_agent_callbacks = [heavy, topic]

    delivered = await core_module.LLMSessionManager.trigger_agent_callbacks(mgr)

    assert delivered is False
    assert sess.image_batches == [], "no prompt may go out after preemption"
    # Nothing is lost: the whole batch goes back for a later turn.
    assert {cb["summary"] for cb in mgr.pending_agent_callbacks} == {
        "cue heavy", "cue topic",
    }


async def test_topic_hint_is_retracted_when_its_hook_lands_in_image_overflow():
    """A teaser must never outlive the opener it promised.

    The hint fires from the pre-split batch, so a topic hook pushed into the
    overflow would leave "she has something to bring up" on screen while the
    turn prompts only unrelated cues and the topic slips to a later turn.
    """
    sess = _CapturingImageOmniOffline()
    mgr = _make_mgr(session=sess)
    mgr.topic_hook_delivery_allowed = lambda: True
    mgr.send_topic_hint = AsyncMock(return_value=True)
    mgr.send_cancel_topic_hint = AsyncMock()
    heavy = _budget_cb("heavy", 8)
    topic = _budget_cb("topic", 1)
    topic["channel"] = "topic_hook"
    topic["source_kind"] = "topic"
    mgr.pending_agent_callbacks = [heavy, topic]

    delivered = await core_module.LLMSessionManager.trigger_agent_callbacks(mgr)

    assert delivered is True
    mgr.send_topic_hint.assert_awaited_once()
    mgr.send_cancel_topic_hint.assert_awaited_once()
    assert [cb["summary"] for cb in mgr.pending_agent_callbacks] == ["cue topic"]
    assert sess.image_batches == [["heavy-img%d" % i for i in range(8)]]


async def test_budget_deferred_callbacks_keep_their_images_for_the_next_turn():
    """Overflowing the turn's image budget is not a staging failure.

    A deferred callback never got its attempt, so treating it like unstaged
    media -- render the text, drop the images, dequeue -- destroys exactly what
    the deferral was protecting. It stays queued whole, and it does not spend
    the transient-failure retry allowance either.
    """
    from main_logic.proactive_delivery import (
        PASSIVE_MEDIA_BUDGET_DEFERRED_KEY,
        PASSIVE_MEDIA_RETRY_KEY,
    )

    session = _FakeOmniOffline(delivered=True)
    session.stream_image = AsyncMock(return_value=None)
    mgr = _make_mgr(session=session)
    callbacks = [
        {
            "_callback_delivery_id": f"id-budget-{i}",
            "status": "completed",
            "summary": f"camera event {i}",
            "delivery_mode": "passive",
            "origin": "event",
            "media_images": [f"img-{i}-a", f"img-{i}-b", f"img-{i}-c"],
        }
        for i in range(5)
    ]
    mgr.pending_agent_callbacks = list(callbacks)

    await core_module.LLMSessionManager._stage_passive_callback_media(
        mgr,
        callbacks,
        session,
    )
    rendered = core_module.LLMSessionManager.drain_agent_callbacks_for_llm(
        mgr,
        callbacks,
    )

    deferred = [
        cb for cb in callbacks if cb.get(PASSIVE_MEDIA_BUDGET_DEFERRED_KEY)
    ]
    assert deferred, "预算应当挡下一部分，否则这条用例没测到东西"

    for cb in deferred:
        # Text unconsumed.
        assert cb["summary"] not in rendered
        # Still queued, image intact.
        assert cb in mgr.pending_agent_callbacks
        assert len(cb["media_images"]) == 3
        # No transient-retry budget spent because no attempt occurred.
        assert PASSIVE_MEDIA_RETRY_KEY not in cb


async def test_budget_deferral_holds_text_only_callbacks_behind_it_too():
    """The split is strict FIFO, so text-only cues get deferred as well.

    Checking the deferral marker only when a callback carries media lets a
    text-only cue queued BEHIND an over-budget one jump ahead of it, which is
    exactly the ordering the strict-FIFO split exists to preserve. A deferred
    queue made up only of text cues would be consumed outright.
    """
    from main_logic.proactive_delivery import PASSIVE_MEDIA_BUDGET_DEFERRED_KEY

    session = _FakeOmniOffline(delivered=True)
    session.stream_image = AsyncMock(return_value=None)
    mgr = _make_mgr(session=session)
    # Head consumes image budget, followed by text then another image.
    heavy = {
        "_callback_delivery_id": "id-heavy",
        "status": "completed",
        "summary": "heavy media cue",
        "delivery_mode": "passive",
        "origin": "event",
        "media_images": [f"heavy-{i}" for i in range(8)],
    }
    text_only = {
        "_callback_delivery_id": "id-text",
        "status": "completed",
        "summary": "text cue behind it",
        "delivery_mode": "passive",
        "origin": "event",
    }
    trailing = {
        "_callback_delivery_id": "id-trailing",
        "status": "completed",
        "summary": "trailing media cue",
        "delivery_mode": "passive",
        "origin": "event",
        "media_images": ["trailing-a"],
    }
    # Order matters: text defers only **after an earlier entry overflowed**,
    # defining strict FIFO. Make trailing overflow first, then text_only.
    callbacks = [heavy, trailing, text_only]
    mgr.pending_agent_callbacks = list(callbacks)

    await core_module.LLMSessionManager._stage_passive_callback_media(
        mgr,
        callbacks,
        session,
    )
    assert text_only.get(PASSIVE_MEDIA_BUDGET_DEFERRED_KEY) is True

    # Drain **only the text** entry: CodeRabbit's second case consumes text-only
    # overflow incorrectly. Draining the full sequence cannot distinguish it,
    # because preceding image cues STOP first, masking differences.
    rendered = core_module.LLMSessionManager.drain_agent_callbacks_for_llm(
        mgr,
        [text_only],
    )

    # Budget deferred it; do not consume this round.
    assert "text cue behind it" not in rendered
    assert text_only in mgr.pending_agent_callbacks
    assert trailing in mgr.pending_agent_callbacks


async def test_staging_stops_where_the_drain_stops():
    """Never stage an image the drain will not deliver the text for.

    The drain halts at a media-bearing PROACTIVE cue (that one waits for the
    proactive path). Staging past it still attaches its image to
    ``system_prefix_images``, and the passive cue in front of it renders text --
    so the Offline turn carries the proactive cue's image with somebody else's
    words, while the cue itself stays queued and sends the same image again next
    time.
    """
    session = _FakeOmniOffline(delivered=True)
    session.stream_image = AsyncMock(return_value=None)
    mgr = _make_mgr(session=session)
    passive = {
        "_callback_delivery_id": "id-passive-first",
        "status": "completed",
        "summary": "passive cue",
        "delivery_mode": "passive",
        "origin": "event",
        "media_images": ["passive-img"],
    }
    proactive_media = {
        "_callback_delivery_id": "id-proactive-media",
        "status": "completed",
        "summary": "proactive cue",
        "delivery_mode": "proactive",
        "origin": "event",
        "media_images": ["proactive-img"],
    }
    callbacks = [passive, proactive_media]
    mgr.pending_agent_callbacks = list(callbacks)

    outcome = await core_module.LLMSessionManager._stage_passive_callback_media(
        mgr,
        callbacks,
        session,
    )
    rendered = core_module.LLMSessionManager.drain_agent_callbacks_for_llm(
        mgr,
        callbacks,
    )

    # Only passive images attached; proactive images never leaked separately from text.
    assert outcome["system_prefix_images"] == ["passive-img"]
    assert "proactive-img" not in outcome["system_prefix_images"]
    assert "passive cue" in rendered
    assert "proactive cue" not in rendered
    # Still queued for proactive delivery.
    assert proactive_media in mgr.pending_agent_callbacks


class _ToolOnlyOmniOffline(_FakeOmniOffline):
    """Delivered by a tool call alone: committed, but no text said."""

    async def prompt_ephemeral(self, instruction, *, images=None, on_committed=None, on_committed_text=None):
        self.called_with.append(instruction)
        if on_committed:
            on_committed()
        return True


@pytest.mark.parametrize("said_something", [False, True])
async def test_a_topic_teaser_is_retracted_when_only_a_tool_answered_it(said_something):
    """The callback counts as delivered either way (its tool already ran, so
    it is never requeued), but with nothing said nothing fills the teaser."""
    sess = _FakeOmniOffline(delivered=True) if said_something else _ToolOnlyOmniOffline()
    mgr = _make_mgr(session=sess)
    mgr.topic_hook_delivery_allowed = lambda: True
    mgr.send_topic_hint = AsyncMock(return_value=True)
    mgr.send_cancel_topic_hint = AsyncMock()
    topic = {"_callback_delivery_id": "id-topic", "status": "completed", "summary": "cue topic",
             "channel": "topic_hook", "source_kind": "topic"}
    mgr.pending_agent_callbacks = [topic]

    assert await core_module.LLMSessionManager.trigger_agent_callbacks(mgr) is True
    mgr.send_topic_hint.assert_awaited_once()
    assert mgr.send_cancel_topic_hint.await_count == (0 if said_something else 1)
    assert mgr.pending_agent_callbacks == []
