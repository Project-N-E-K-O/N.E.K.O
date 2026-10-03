"""Qwen local activity must retain its runtime owner across hint delivery."""

import asyncio
import json

import pytest

import main_logic.asr_client.runtime as runtime_module
from main_logic.asr_client._infra import AsrSessionConfig, _RealtimeAsrSessionImpl
from main_logic.asr_client.lifecycle import (
    VoiceInputLifecycleController,
    VoiceLifecycleState,
    VoiceRouteMode,
)
from main_logic.asr_client.provider_policy import resolve_provider_policy
from main_logic.asr_client.workers import qwen
from main_logic.voice_turn.contracts import SpeechActivityEvent
from tests.support.asr_fakes import _Runtime, _selection
from tests.support.core_asr_harness import _ReadyDetector
from tests.unit.test_asr_workers import _FakeConnector, _FakeWebSocket
from main_logic.asr_client.endpointing.detector_runtime import DetectorFeedResult
from main_logic.voice_turn.audio_input import ProcessedVoiceFrame

pytestmark = [pytest.mark.asyncio, pytest.mark.runtime]


@pytest.fixture
def qwen_sessions(monkeypatch):
    policy = resolve_provider_policy("qwen", "provider")

    async def on_send(ws, payload):
        event_type = json.loads(payload)["type"]
        if event_type == "session.update":
            await ws.server_send({"type": "session.updated"})
        elif event_type == "session.finish":
            await ws.server_send({"type": "session.finished"})

    sockets = (_FakeWebSocket(on_send=on_send), _FakeWebSocket(on_send=on_send))
    monkeypatch.setattr(qwen.websockets, "connect", _FakeConnector(*sockets))

    async def callback(*_args):
        pass

    def make_session(**callbacks):
        return _RealtimeAsrSessionImpl(
            worker_fn=qwen.qwen_asr_worker,
            api_key="test",
            config=AsrSessionConfig(endpointing_mode="provider"),
            on_input_transcript=callbacks.get("on_input_transcript", callback),
            on_connection_error=callbacks.get("on_connection_error", callback),
            on_turn_endpointed=callbacks.get("on_turn_endpointed"),
            provider_policy=policy,
        )

    return make_session, sockets, policy


def _install_runtime_session(runtime, session, policy):
    runtime._asr_session = session
    runtime._asr_provider = "qwen"
    runtime._asr_lifecycle = VoiceInputLifecycleController(
        provider_policy=policy,
        shadow_mode=False,
    )
    runtime._asr_lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
    runtime._asr_detector = _ReadyDetector()
    runtime._set_microphone_route("independent")
    runtime._asr_runtime._asr_current_ingress_token = runtime._capture_ingress_token()


def _observe_hint_entry(monkeypatch, session, *, reject_after_return=False):
    entered = asyncio.Event()
    original = session.signal_local_activity

    async def observed_hint(*, speech_active):
        # Synchronous observation only: retain the actual lock/queue/wait path.
        entered.set()
        await original(speech_active=speech_active)
        if reject_after_return:
            raise RuntimeError("controlled local activity rejection")

    monkeypatch.setattr(session, "signal_local_activity", observed_hint)
    return entered


@pytest.mark.parametrize("cancel_source", ["owner", "resume", "abort"])
async def test_cancel_inflight_fifo_pause_does_not_deliver_late_hint(
    monkeypatch, qwen_sessions, cancel_source
):
    make_session, _, policy = qwen_sessions
    session = make_session()
    await session.connect()
    runtime = _Runtime()
    component = runtime._asr_runtime
    _install_runtime_session(runtime, session, policy)
    await component._handle_independent_asr_activity(
        SpeechActivityEvent.SPEECH_STARTED, component._asr_session_epoch
    )
    token = component._capture_turn_token(component._asr_lifecycle)
    assert component._asr_audio_dispatcher.activate(token, session, b"")
    await component._asr_audio_dispatcher.wait_idle()
    entered = asyncio.Event()
    delivered = []
    original = session.signal_local_activity

    async def observe(*, speech_active):
        if not speech_active:
            entered.set()
        await original(speech_active=speech_active)
        delivered.append(speech_active)

    monkeypatch.setattr(session, "signal_local_activity", observe)
    await session._operation_lock.acquire()
    locked = True
    pause = asyncio.create_task(component._handle_independent_asr_activity(
        SpeechActivityEvent.CANDIDATE_PAUSE, component._asr_session_epoch
    ))
    resume = None
    try:
        await asyncio.wait_for(entered.wait(), 1)
        if cancel_source == "owner":
            pause.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pause
        elif cancel_source == "abort":
            component._asr_audio_dispatcher.abort(token)
        else:
            resume = asyncio.create_task(component._handle_independent_asr_activity(
                SpeechActivityEvent.SPEECH_RESUMED, component._asr_session_epoch
            ))
            await asyncio.sleep(0)
        session._operation_lock.release()
        locked = False
        if resume is not None:
            await asyncio.wait_for(resume, 1)
            await asyncio.wait_for(pause, 1)
        elif cancel_source == "abort":
            await asyncio.wait_for(pause, 1)
        await component._asr_audio_dispatcher.wait_idle()
        assert delivered == ([True] if cancel_source == "resume" else [])
        assert not component._asr_audio_dispatcher._pause_hint_tasks
    finally:
        if locked:
            session._operation_lock.release()
        for task in (pause, resume):
            if task is not None and not task.done():
                task.cancel()
        await component._asr_audio_dispatcher.close()
        await session.close()


@pytest.mark.parametrize("source", ["synthetic", "stale", "real"])
async def test_only_current_physical_resume_reaches_qwen_hint(qwen_sessions, source):
    make_session, sockets, policy = qwen_sessions
    session = make_session()
    await session.connect()
    runtime = _Runtime()
    component = runtime._asr_runtime
    _install_runtime_session(runtime, session, policy)
    hints = []
    original = session.signal_local_activity

    async def observe(*, speech_active):
        hints.append(speech_active)
        await original(speech_active=speech_active)

    session.signal_local_activity = observe
    try:
        await component._handle_independent_asr_activity(
            SpeechActivityEvent.SPEECH_STARTED, component._asr_session_epoch
        )
        hints.clear()
        if source == "stale":
            component._asr_audio_generation += 1
        await component._handle_independent_asr_activity(
            SpeechActivityEvent.SPEECH_RESUMED, component._asr_session_epoch,
            synthetic=source == "synthetic",
        )
        assert hints == ([True] if source == "real" else [])
    finally:
        await session.close()


@pytest.mark.parametrize("source", ["dispatcher_backlog", "same_frame"])
@pytest.mark.parametrize("resume_while_pending", [False, True])
async def test_pause_hint_follows_pending_pcm_before_capturing_position(
    monkeypatch, qwen_sessions, source, resume_while_pending
):
    make_session, sockets, policy = qwen_sessions
    session = make_session()
    await session.connect()
    runtime = _Runtime()
    component = runtime._asr_runtime
    _install_runtime_session(runtime, session, policy)
    entered, release = asyncio.Event(), asyncio.Event()
    original_stream = session.stream_audio
    original_hint = session.signal_local_activity
    hints = []

    async def delayed_stream(audio, **kwargs):
        entered.set()
        await release.wait()
        await original_stream(audio, **kwargs)

    async def observe_hint(*, speech_active):
        hints.append((speech_active, session.provider_wire_audio_ms))
        await original_hint(speech_active=speech_active)

    task = None
    try:
        await component._handle_independent_asr_activity(
            SpeechActivityEvent.SPEECH_STARTED, component._asr_session_epoch
        )
        monkeypatch.setattr(session, "stream_audio", delayed_stream)
        monkeypatch.setattr(session, "signal_local_activity", observe_hint)
        pcm = b"\0" * 3200
        if source == "dispatcher_backlog":
            token = component._capture_turn_token(component._asr_lifecycle)
            assert component._asr_audio_dispatcher.activate(token, session, b"")
            assert component._asr_audio_dispatcher.enqueue_audio(
                token, session, pcm, sample_rate_hz=16000, sequence_no=1
            )
            await asyncio.wait_for(entered.wait(), 1)
            task = asyncio.create_task(component._handle_independent_asr_activity(
                SpeechActivityEvent.CANDIDATE_PAUSE, component._asr_session_epoch
            ))
        else:
            component._asr_detector._feed_result = DetectorFeedResult(
                (SpeechActivityEvent.CANDIDATE_PAUSE,), True
            )
            task = asyncio.create_task(component.submit(
                ProcessedVoiceFrame(pcm16=pcm, sample_rate_hz=16000, speech_probability=None),
                ingress_token=component._asr_current_ingress_token,
            ))
            await asyncio.wait_for(entered.wait(), 1)
        await asyncio.sleep(0)
        assert hints == []
        assert not task.done()
        if resume_while_pending:
            await component._handle_independent_asr_activity(
                SpeechActivityEvent.SPEECH_RESUMED, component._asr_session_epoch
            )
            assert hints == [(True, 0)]
        token = component._capture_turn_token(component._asr_lifecycle)
        assert component._asr_audio_dispatcher.enqueue_audio(
            token, session, b"\0" * 6400, sample_rate_hz=16000, sequence_no=2
        )
        release.set()
        await asyncio.wait_for(task, 1)
        await component._asr_audio_dispatcher.wait_idle()
        await asyncio.wait_for(session._request_queue.join(), 1)
        assert hints == ([(True, 0)] if resume_while_pending else [(False, 100)])
        assert len([p for p in sockets[0].sent if json.loads(p)["type"] == "input_audio_buffer.append"]) == 2
    finally:
        release.set()
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await component._asr_audio_dispatcher.close()
        await session.close()


@pytest.mark.parametrize(
    "transition,hold_lock,reject_hint",
    [
        ("start", False, False),
        ("start", True, False),
        ("close_then_start", True, False),
        ("abort_then_start", True, False),
        ("close_then_start", True, True),
        ("abort_then_start", True, True),
    ],
)
async def test_old_qwen_pause_cannot_retire_successor_onset(
    monkeypatch,
    qwen_sessions,
    transition,
    hold_lock,
    reject_hint,
):
    make_session, _, policy = qwen_sessions
    old_session = make_session()
    await old_session.connect()
    runtime = _Runtime()
    component = runtime._asr_runtime
    _install_runtime_session(runtime, old_session, policy)
    timeline = []
    entered = _observe_hint_entry(
        monkeypatch,
        old_session,
        reject_after_return=reject_hint,
    )
    if hold_lock:
        await old_session._operation_lock.acquire()
    lock_owned = hold_lock

    async def pause():
        await component._handle_independent_asr_activity(
            SpeechActivityEvent.CANDIDATE_PAUSE,
            component._asr_session_epoch,
        )
        timeline.append("old_pause_finished")

    pause_task = asyncio.create_task(pause())
    await asyncio.wait_for(entered.wait(), 1)
    assert not pause_task.done()

    def candidate_factory(*_args, **callbacks):
        assert pause_task.done() is (transition == "start")
        timeline.append("new_candidate_created")
        return make_session(**callbacks)

    monkeypatch.setattr(
        runtime_module,
        "_resolve_asr_selection",
        lambda *_args, **_kwargs: _selection("qwen", "provider"),
    )
    monkeypatch.setattr(
        runtime_module, "_create_asr_session_from_selection", candidate_factory
    )
    teardown_task = None
    start_task = None
    try:
        if transition != "start":
            teardown_task = asyncio.create_task(
                component.close()
                if transition == "close_then_start"
                else component.abort("core_session_ended")
            )
            await asyncio.wait_for(old_session._closing_event.wait(), 1)
            assert component._asr_session is None
        start_task = asyncio.create_task(
            component.start(
                route_key="qwen",
                resource_optimization_enabled=False,
            )
        )
        if hold_lock and transition == "start":
            await asyncio.wait_for(old_session._closing_event.wait(), 1)
            assert not start_task.done()
            assert "new_candidate_created" not in timeline
            old_session._operation_lock.release()
            lock_owned = False
        result = await asyncio.wait_for(start_task, 5)
        assert result.status is runtime_module.AsrStartStatus.READY
        component._asr_current_ingress_token = runtime._capture_ingress_token()
        await component._handle_independent_asr_activity(
            SpeechActivityEvent.SPEECH_STARTED,
            component._asr_session_epoch,
        )
        await component._handle_independent_asr_activity(
            SpeechActivityEvent.SPEECH_RESUMED,
            component._asr_session_epoch,
        )
        new_token = component._asr_current_ingress_token
        assert component._asr_overlap_onset_token == new_token
        if lock_owned:
            old_session._operation_lock.release()
            lock_owned = False
        await asyncio.wait_for(pause_task, 1)
        if teardown_task is not None:
            await asyncio.wait_for(teardown_task, 5)
        assert component._asr_overlap_onset_token == new_token
        assert component._asr_overlap_completed_turns == 0
        assert not component._asr_overlap_completed_onsets
        assert timeline == (
            ["old_pause_finished", "new_candidate_created"]
            if transition == "start"
            else ["new_candidate_created", "old_pause_finished"]
        )
    finally:
        if lock_owned:
            old_session._operation_lock.release()
        tasks = [
            task for task in (pause_task, start_task, teardown_task) if task is not None
        ]
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await component.close()
        await old_session.close()


@pytest.mark.parametrize(
    "scenario",
    [
        "live_overlap",
        "completed_overlap",
        "final_during_hint",
        "cancel_hint",
    ],
)
async def test_current_qwen_activity_preserves_turns_and_cancellation(
    monkeypatch,
    qwen_sessions,
    scenario,
):
    make_session, sockets, policy = qwen_sessions
    ws = sockets[0]
    runtime = _Runtime()
    component = runtime._asr_runtime
    epoch = component._asr_session_epoch
    final_entered, final_release = asyncio.Event(), asyncio.Event()
    if scenario != "final_during_hint":
        final_release.set()
    final_done = {text: asyncio.Event() for text in ("first", "second")}
    errors = []

    async def on_endpoint():
        await component._handle_independent_asr_endpoint(epoch)

    async def on_final(text):
        if text == "first":
            final_entered.set()
            await final_release.wait()
        await component._handle_independent_asr_final(text, epoch, "qwen")
        final_done[text].set()

    async def on_error(error):
        errors.append(error)

    session = make_session(
        on_input_transcript=on_final,
        on_connection_error=on_error,
        on_turn_endpointed=on_endpoint,
    )
    await session.connect()
    _install_runtime_session(runtime, session, policy)
    hint_task = None
    lock_owned = False

    async def provider_final(item_id, text):
        await ws.server_send(
            {"type": "input_audio_buffer.speech_started", "item_id": item_id}
        )
        await ws.server_send(
            {"type": "input_audio_buffer.speech_stopped", "item_id": item_id}
        )
        await ws.server_send(
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "item_id": item_id,
                "transcript": text,
            }
        )

    try:
        await component._handle_independent_asr_activity(
            SpeechActivityEvent.SPEECH_STARTED, epoch
        )
        first_turn = component._asr_lifecycle.snapshot.turn_id
        assert component._asr_lifecycle.snapshot.state is VoiceLifecycleState.ACTIVE
        if scenario in ("live_overlap", "completed_overlap", "cancel_hint"):
            await component._handle_independent_asr_activity(
                SpeechActivityEvent.SPEECH_RESUMED, epoch
            )
            assert (
                component._asr_overlap_onset_token
                == component._asr_current_ingress_token
            )
        if scenario == "completed_overlap":
            await component._handle_independent_asr_activity(
                SpeechActivityEvent.CANDIDATE_PAUSE, epoch
            )
            assert component._asr_overlap_onset_token is None
            assert component._asr_overlap_completed_turns == 1
        if scenario == "cancel_hint":
            onset = component._asr_overlap_onset_token
            await session._operation_lock.acquire()
            lock_owned = True
            entered = _observe_hint_entry(monkeypatch, session)
            hint_task = asyncio.create_task(
                component._handle_independent_asr_activity(
                    SpeechActivityEvent.CANDIDATE_PAUSE,
                    epoch,
                )
            )
            await asyncio.wait_for(entered.wait(), 1)
            assert not hint_task.done()
            hint_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await hint_task
            assert component._asr_overlap_onset_token == onset
            assert component._asr_overlap_completed_turns == 0
            return
        await provider_final("a", "first")
        if scenario == "final_during_hint":
            await asyncio.wait_for(final_entered.wait(), 1)
            assert (
                component._asr_lifecycle.snapshot.state is VoiceLifecycleState.DRAINING
            )
            await session._operation_lock.acquire()
            lock_owned = True
            entered = _observe_hint_entry(monkeypatch, session)
            hint_task = asyncio.create_task(
                component._handle_independent_asr_activity(
                    SpeechActivityEvent.SPEECH_RESUMED,
                    epoch,
                )
            )
            await asyncio.wait_for(entered.wait(), 1)
            assert not hint_task.done()
            final_release.set()
            await asyncio.wait_for(final_done["first"].wait(), 1)
            assert (
                component._asr_lifecycle.snapshot.state is VoiceLifecycleState.WARM_IDLE
            )
            session._operation_lock.release()
            lock_owned = False
            await asyncio.wait_for(hint_task, 1)
        else:
            await asyncio.wait_for(final_done["first"].wait(), 1)
        if scenario != "completed_overlap":
            assert component._asr_lifecycle.snapshot.state is VoiceLifecycleState.ACTIVE
            assert component._asr_lifecycle.snapshot.turn_id == first_turn + 1
            assert component._asr_turn_prepared
        else:
            assert (
                component._asr_lifecycle.snapshot.state is VoiceLifecycleState.WARM_IDLE
            )
        await provider_final("b", "second")
        await asyncio.wait_for(final_done["second"].wait(), 1)
        await runtime._wait_asr_transcript_dispatch_idle()
        assert [
            call.args[0] for call in runtime.handle_input_transcript.await_args_list
        ] == ["first", "second"]
        assert runtime.handle_new_message.await_count == 2
        assert component._asr_overlap_completed_turns == 0
        assert not errors
    finally:
        final_release.set()
        if lock_owned:
            session._operation_lock.release()
        if hint_task is not None and not hint_task.done():
            hint_task.cancel()
            await asyncio.gather(hint_task, return_exceptions=True)
        await component.close()
        await session.close()
