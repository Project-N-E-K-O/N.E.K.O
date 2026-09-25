"""An activation prefix protects audio without replacing explicit fault causes."""

import asyncio
import json
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from tests.unit.activation.test_voice_activation_cold_prefix import (
    _cold_harness,
    _feed,
    _until,
    _Gate,
)

from main_logic.asr_client.recovery import (
    FailureSource,
    RecoveryDisposition,
    decide_failure,
)
from main_logic.asr_client._infra import AsrSessionConfig, _RealtimeAsrSessionImpl
from main_logic.asr_client.connection_cleanup import ConnectionRetirementError
from main_logic.asr_client.workers import qwen
from main_logic.voice_turn.contracts import PreserveUnsentPrefix
from tests.unit.asr_runtime import test_fault_recovery as cases
from tests.support.realtime_harness import _FakeConnector, _FakeWebSocket


@pytest.fixture
def protected_input(monkeypatch):
    started = cases._started

    async def started_with_prefix(*args, **kwargs):
        result = await started(*args, **kwargs)
        owner, runtime, sessions, *_ = result
        runtime._asr_protected_prefix = PreserveUnsentPrefix(
            ingress=owner._capture_ingress_token(),
            batch_id="activation-review",
            start_sequence=0,
        )
        sessions[0].transport_write_attempted = True
        return result

    monkeypatch.setattr(cases, "_started", started_with_prefix)


@pytest.mark.parametrize(
    "risk", [None, "ASR_INPUT_DELIVERY_FAILED", "ASR_INPUT_DELIVERY_UNCERTAIN"]
)
@pytest.mark.parametrize("source", list(FailureSource))
def test_read_disconnect_requires_provider_origin_even_with_delivery_risk(risk, source):
    decision = decide_failure(
        "ASR_QWEN_READ_DISCONNECTED", source=source, delivery_risk=risk
    )
    assert (
        decision.cause_code
        == decision.notification_code
        == "ASR_QWEN_READ_DISCONNECTED"
    )
    assert decision.delivery_risk == risk
    assert decision.recovery_disposition is (
        RecoveryDisposition.RECOVER
        if source is FailureSource.PROVIDER
        else RecoveryDisposition.STOP
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("attempted", [False, True])
@pytest.mark.parametrize(
    "code",
    [
        "ASR_INDEPENDENT_FAILED",
        "ASR_INDEPENDENT_STREAM_FAILED",
        "ASR_STREAM_BACKPRESSURE",
        "ASR_QWEN_CONNECTION_CLOSED",
        "ASR_QWEN_WORKER_FAILED",
        "ASR_STEP_CONNECTION_CLOSED",
        "ASR_STEP_WORKER_FAILED",
        "ASR_OPENAI_WORKER_FAILED",
        "ASR_SONIOX_PROTECTED_REPLAY_DISABLED",
        "ASR_SONIOX_REPLAY_INCOMPLETE",
    ],
)
async def test_legacy_delivery_notice_does_not_enable_recovery(
    protected_input,
    monkeypatch,
    attempted,
    code,
):
    _, runtime, sessions, _, _, observers = await cases._started(monkeypatch)
    sessions[0].transport_write_attempted = attempted
    try:
        await runtime._handle_independent_asr_error(
            runtime._asr_session_epoch, "qwen", status_code=code
        )
        assert runtime._asr_recovery is None
        sessions[1].connect.assert_not_awaited()
        observers["on_failure"].assert_awaited_once()
        assert observers["on_failure"].await_args.args[0].code == (
            "ASR_INPUT_DELIVERY_UNCERTAIN" if attempted else "ASR_INPUT_DELIVERY_FAILED"
        )
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_timed_out_recovery_claim_cannot_stop_replacement(
    protected_input, monkeypatch
):
    _, runtime, sessions, _, _, observers = await cases._started(monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()

    async def delayed_claim(*_args):
        entered.set()
        await release.wait()
        return False

    monkeypatch.setattr(runtime, "_begin_asr_recovery", delayed_claim)
    task = asyncio.create_task(
        runtime._handle_independent_asr_error(
            runtime._asr_session_epoch,
            "qwen",
            status_code="ASR_QWEN_READ_DISCONNECTED",
            failure_source=FailureSource.PROVIDER,
        )
    )
    try:
        await asyncio.wait_for(entered.wait(), 1)
        # A different owned session won while the claim awaited settlement.
        runtime._asr_session = sessions[1]
        release.set()
        await asyncio.wait_for(task, 1)
        assert runtime._asr_session is sessions[1]
        sessions[1].close.assert_not_awaited()
        observers["on_failure"].assert_not_awaited()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await runtime.close()


@pytest.mark.asyncio
async def test_real_session_retirement_failure_prevents_replacement(
    protected_input, monkeypatch
):
    _, runtime, sessions, _, _, observers = await cases._started(monkeypatch)

    class UnreleasedSocket(_FakeWebSocket):
        async def close(self, *args, **kwargs):
            raise OSError("injected close failure without transport release")

    async def on_send(socket, payload):
        kind = json.loads(payload)["type"]
        if kind == "session.update":
            await socket.server_send({"type": "session.updated"})
        elif kind == "session.finish":
            await socket.server_send({"type": "session.finished"})

    socket = UnreleasedSocket(on_send=on_send)
    monkeypatch.setattr(qwen.websockets, "connect", _FakeConnector(socket))
    session = _RealtimeAsrSessionImpl(
        worker_fn=qwen.qwen_asr_worker,
        api_key="test-key",
        config=AsrSessionConfig(endpointing_mode="provider"),
        on_input_transcript=AsyncMock(),
        on_connection_error=AsyncMock(),
    )
    try:
        await session.connect()
        runtime._asr_session = session
        await runtime._handle_independent_asr_error(
            runtime._asr_session_epoch,
            "qwen",
            status_code="ASR_QWEN_READ_DISCONNECTED",
            failure_source=FailureSource.PROVIDER,
        )
        operation = runtime._asr_recovery
        assert operation is not None
        await asyncio.wait_for(asyncio.shield(operation.task), 5)
        assert operation.failed and not operation.completed
        assert socket.closed is False
        sessions[1].connect.assert_not_awaited()
        observers["on_failure"].assert_awaited_once()
        assert (
            observers["on_failure"].await_args.args[0].code == "ASR_RECOVERY_EXHAUSTED"
        )
        with pytest.raises(ConnectionRetirementError):
            await session.close()
    finally:
        await socket.server_end()
        await runtime.close()
        await asyncio.gather(session.close(), return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name",
    [
        "test_final_timeout_retires_transport_and_recovers_once",
        "test_partial_only_failed_turn_is_abandoned_and_never_finalized",
        "test_retired_callback_cannot_finalize_or_fail_replacement",
        "test_successful_handshakes_do_not_replenish_two_attempt_budget",
        "test_mute_cancels_waiting_recovery_without_reopening",
        "test_accepted_final_drains_before_recovery_retires_its_identity",
        "test_fresh_recovery_audio_waits_for_connect_then_preserves_order",
        "test_old_sentence_tail_is_suppressed_until_existing_pause_boundary",
        "test_total_recovery_deadline_includes_old_transport_close",
    ],
)
async def test_existing_recovery_with_protection(protected_input, monkeypatch, name):
    await getattr(cases, name)(monkeypatch)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code",
    [
        "ASR_INPUT_DELIVERY_UNCERTAIN",
        "ASR_INPUT_DELIVERY_FAILED",
        "ASR_AUDIO_ORDERING_FAILED",
        "ASR_CREDENTIALS_REJECTED",
        "ASR_UNKNOWN_FAILURE",
        "ASR_ENDPOINTING_FAILED",
        "ASR_RECOVERY_EXHAUSTED",
    ],
)
async def test_nonrecoverable_still_stops(protected_input, monkeypatch, code):
    _, runtime, sessions, _, _, observers = await cases._started(monkeypatch)
    try:
        await runtime._handle_independent_asr_error(
            runtime._asr_session_epoch,
            "qwen",
            status_code=code,
            failure_source=FailureSource.PROVIDER,
        )
        assert runtime._asr_recovery is None
        sessions[1].connect.assert_not_awaited()
        observers["on_failure"].assert_awaited_once()
        assert observers["on_failure"].await_args.args[0].code == code
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_activation_to_recovery_to_fresh_audio(monkeypatch):
    gate = _Gate()
    # Model output is controlled: old sentence has ended. The actual detector,
    # activation controller, Core, runtime and dispatchers remain in the path.
    gate.recovery_boundary_ready = True
    async with _cold_harness(gate=gate) as h:
        runtime = h.manager._asr_runtime
        final = AsyncMock()
        runtime._callbacks = replace(runtime._callbacks, on_final=final)
        first = [await _feed(h, marker) for marker in range(1, 16)]
        await _until(lambda: h.lifecycle.pending_connect_bytes == 48000)
        h.release.set()
        await _until(lambda: sum(map(len, h.deliveries)) == 48000)
        old = h.sessions[0]
        old.stream_audio = AsyncMock(wraps=old.stream_audio)
        prefix = runtime._asr_protected_prefix
        epoch = runtime._asr_session_epoch
        await runtime._handle_independent_asr_endpoint(epoch)
        await runtime._handle_independent_asr_final(
            "completed first turn", epoch, "qwen"
        )
        await runtime.wait_transcript_idle()
        final.assert_awaited_once()
        await runtime._handle_independent_asr_error(
            epoch,
            "qwen",
            status_code="ASR_QWEN_READ_DISCONNECTED",
            failure_source=FailureSource.PROVIDER,
        )
        recovery = runtime._asr_recovery
        assert recovery is not None
        await asyncio.wait_for(asyncio.shield(recovery.task), 2)
        assert recovery.completed
        assert len(h.sessions) == 2
        assert runtime._asr_session is h.sessions[1]
        assert h.manager._asr_route_mode == "independent"
        assert b"".join(h.deliveries) == b"".join(first)
        assert runtime._asr_protected_prefix is prefix
        fresh = [await _feed(h, marker) for marker in range(501, 504)]
        await _until(lambda: sum(map(len, h.deliveries)) == 57600)
        assert b"".join(h.deliveries) == b"".join(first + fresh)
        old.stream_audio.assert_not_awaited()
        await runtime._handle_independent_asr_endpoint(epoch)
        await runtime._handle_independent_asr_final(
            "completed second turn", epoch, "qwen"
        )
        await runtime.wait_transcript_idle()
        assert final.await_count == 2
