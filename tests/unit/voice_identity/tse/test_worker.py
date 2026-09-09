import asyncio
import multiprocessing
import os
from pathlib import Path
import threading
from types import SimpleNamespace

import numpy as np
import pytest

from main_logic.voice_identity.tse import TseModelError, TseStream
from main_logic.voice_identity.tse import worker
from main_logic.voice_identity.tse.contracts import TSE_ENCODER_IDENTITY


def install_model(monkeypatch, *, load_gate=None, run_gate=None, fail_load=False, fail_infer=False):
    state = SimpleNamespace(load_started=threading.Event(), run_started=threading.Event(),
                            threads=[], closed=threading.Event(), streams=0)

    class Model:
        def __init__(self, directory):
            state.threads.append(threading.get_ident())
            state.load_started.set()
            if load_gate is not None:
                assert load_gate.wait(5)
            if fail_load:
                raise RuntimeError("model load failure")

        def create_stream(self, embedding, *, start_sample):
            state.streams += 1
            return TseStream(self, embedding, start_sample=start_sample)

        def infer(self, spectrum, embedding, hidden, cell):
            state.threads.append(threading.get_ident())
            state.run_started.set()
            if run_gate is not None:
                assert run_gate.wait(5)
            if fail_infer:
                raise RuntimeError("native failure")
            return spectrum, hidden, cell

        def close(self):
            state.closed.set()

    monkeypatch.setattr(worker, "TseModel", Model)
    return state


@pytest.mark.asyncio
async def test_worker_runs_in_order_off_loop_and_preserves_exact_ranges(monkeypatch, tmp_path):
    state = install_model(monkeypatch)
    instance = worker.TseWorker(tmp_path, np.ones(192), start_sample=7000)
    await instance.start()
    assert await instance.push(np.empty(0), start_sample=7000) == []
    first, second = await asyncio.gather(
        instance.push(np.ones(640, np.float32), start_sample=7000),
        instance.push(np.ones(641, np.float32), start_sample=7640),
    )
    chunks = first + second + await instance.flush()
    assert chunks[0].start_sample == 7000
    assert chunks[-1].end_sample == 8281
    np.testing.assert_allclose(np.concatenate([chunk.pcm for chunk in chunks]), 1, atol=2e-7)
    assert all(identity != threading.get_ident() for identity in state.threads)
    assert len(set(state.threads)) == 1
    assert instance.pending_samples == 0
    assert await instance.close()
    assert state.closed.is_set() and instance.stopped
    with pytest.raises(TseModelError):
        await instance.push(np.ones(1), start_sample=8281)


@pytest.mark.asyncio
async def test_queue_capacity_retires_every_waiter_and_reports_native_still_running(monkeypatch, tmp_path):
    gate = threading.Event()
    state = install_model(monkeypatch, run_gate=gate)
    instance = worker.TseWorker(tmp_path, np.ones(192), max_age_ms=2000)
    await instance.start()
    tasks = [asyncio.create_task(instance.push(np.ones(640), start_sample=0))]
    assert await asyncio.to_thread(state.run_started.wait, 1)
    tasks.extend(asyncio.create_task(instance.push(np.ones(640), start_sample=index * 640)) for index in range(1, 5))
    await asyncio.sleep(0.02)
    assert instance.pending_samples == 3200
    assert instance.oldest_age_ms > 0
    try:
        with pytest.raises(TseModelError):
            await instance.push(np.ones(640), start_sample=3200)
        assert instance.generation == 2
        assert instance.failure_reason == "tse_queue_full"
        assert await instance.close(timeout=0.01) is False
        outcomes = await asyncio.gather(*tasks, return_exceptions=True)
        assert all(isinstance(result, TseModelError) for result in outcomes)
    finally:
        gate.set()
        assert await instance.close()
    assert state.closed.is_set() and instance.pending_samples == 0


@pytest.mark.asyncio
async def test_operation_age_timeout_drops_late_native_result(monkeypatch, tmp_path):
    gate = threading.Event()
    install_model(monkeypatch, run_gate=gate)
    instance = worker.TseWorker(tmp_path, np.ones(192), max_age_ms=20)
    await instance.start()
    try:
        with pytest.raises(TimeoutError):
            await instance.push(np.ones(640), start_sample=0)
        assert not instance.stopped
        assert await instance.close(timeout=0) is False
    finally:
        gate.set()
        assert await instance.close()
    with pytest.raises(TseModelError):
        await instance.flush()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_queued", [False, True])
async def test_cancellation_retires_active_and_queued_work(monkeypatch, tmp_path, cancel_queued):
    gate = threading.Event()
    state = install_model(monkeypatch, run_gate=gate)
    instance = worker.TseWorker(tmp_path, np.ones(192), max_age_ms=2000)
    await instance.start()
    active = asyncio.create_task(instance.push(np.ones(640), start_sample=0))
    assert await asyncio.to_thread(state.run_started.wait, 1)
    queued = asyncio.create_task(instance.push(np.ones(640), start_sample=640))
    await asyncio.sleep(0)
    (queued if cancel_queued else active).cancel()
    try:
        results = await asyncio.gather(active, queued, return_exceptions=True)
        assert sum(isinstance(item, asyncio.CancelledError) for item in results) == 1
        assert sum(isinstance(item, TseModelError) for item in results) == 1
        assert not instance.stopped
    finally:
        gate.set()
        assert await instance.close()


@pytest.mark.asyncio
async def test_startup_cancellation_prevents_stream_creation(monkeypatch, tmp_path):
    gate = threading.Event()
    state = install_model(monkeypatch, load_gate=gate)
    instance = worker.TseWorker(tmp_path, np.ones(192))
    startup = asyncio.create_task(instance.start())
    assert await asyncio.to_thread(state.load_started.wait, 1)
    startup.cancel()
    with pytest.raises(asyncio.CancelledError):
        await startup
    assert await instance.close(timeout=0.01) is False
    gate.set()
    assert await instance.close()
    assert state.streams == 0 and state.closed.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize("at_load", [False, True])
async def test_native_failure_is_terminal(monkeypatch, tmp_path, at_load):
    install_model(monkeypatch, fail_load=at_load, fail_infer=not at_load)
    instance = worker.TseWorker(tmp_path, np.ones(192))
    if at_load:
        with pytest.raises(TseModelError):
            await instance.start()
    else:
        await instance.start()
        with pytest.raises(TseModelError):
            await instance.push(np.ones(640), start_sample=0)
    assert await instance.close()
    assert instance.failure_reason is not None


@pytest.mark.asyncio
async def test_sample_gap_retires_instead_of_concatenating(monkeypatch, tmp_path):
    install_model(monkeypatch)
    instance = worker.TseWorker(tmp_path, np.ones(192))
    await instance.start()
    with pytest.raises(TseModelError):
        await instance.push(np.ones(1), start_sample=1)
    assert instance.failure_reason == "tse_input_discontinuity"
    assert await instance.close()


@pytest.mark.asyncio
async def test_oversized_audio_is_rejected_before_copy_or_native_work(monkeypatch, tmp_path):
    state = install_model(monkeypatch)
    instance = worker.TseWorker(tmp_path, np.ones(192))
    await instance.start()
    pcm = np.ones(3201, np.float32)
    with pytest.raises(TseModelError):
        await instance.push(pcm, start_sample=0)
    assert not state.run_started.is_set()
    assert pcm.all()
    assert await instance.close()


@pytest.mark.asyncio
async def test_closing_an_unstarted_worker_is_terminal(tmp_path):
    instance = worker.TseWorker(tmp_path, np.ones(192))
    assert await instance.close(timeout=0)
    assert instance.stopped
    with pytest.raises(TseModelError):
        await instance.start()


@pytest.mark.asyncio
async def test_push_before_start_does_not_load_native_model(tmp_path):
    instance = worker.TseWorker(tmp_path, np.ones(192))
    with pytest.raises(TseModelError):
        await instance.push(np.ones(640), start_sample=0)
    assert await instance.close()


def fake_encoder_process(monkeypatch, *, ready, startup_gate=None):
    state = SimpleNamespace(recordings=None, started=threading.Event(), retired=threading.Event(), receiver_closed=False)
    process = SimpleNamespace(is_alive=lambda: not state.retired.is_set())

    class Receiver:
        def poll(self):
            return ready

        def recv(self):
            return True, np.arange(1, 193, dtype=np.float32)

        def close(self):
            state.receiver_closed = True

    def startup(directory, recordings):
        state.recordings = recordings
        state.started.set()
        if startup_gate is not None:
            assert startup_gate.wait(5)
        return process, Receiver()

    monkeypatch.setattr(worker, "_start_encoder_process", startup)
    monkeypatch.setattr(worker, "_stop_encoder_process", lambda process: state.retired.set())
    return state


@pytest.mark.asyncio
async def test_enrollment_returns_raw_reference_after_process_retirement(monkeypatch, tmp_path):
    state = fake_encoder_process(monkeypatch, ready=True)
    original = [np.ones(48000, np.float32) for _ in range(3)]
    reference = await worker.extract_extraction_reference(tmp_path, original)
    assert reference.model_identity == TSE_ENCODER_IDENTITY
    np.testing.assert_array_equal(reference.copy_embedding(), np.arange(1, 193, dtype=np.float32))
    assert state.retired.is_set() and state.receiver_closed
    assert all(not pcm.any() for pcm in state.recordings)
    assert all(pcm.all() for pcm in original)
    reference.close()


@pytest.mark.asyncio
async def test_enrollment_cancel_during_startup_joins_then_wipes_owned_copies(monkeypatch, tmp_path):
    gate = threading.Event()
    state = fake_encoder_process(monkeypatch, ready=False, startup_gate=gate)
    original = [np.ones(48000, np.float32) for _ in range(3)]
    task = asyncio.create_task(worker.extract_extraction_reference(tmp_path, original))
    assert await asyncio.to_thread(state.started.wait, 1)
    for pcm in original:
        pcm.fill(0)
    assert all(pcm.all() for pcm in state.recordings)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    gate.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert state.retired.is_set() and state.receiver_closed
    assert all(not pcm.any() for pcm in state.recordings)


@pytest.mark.asyncio
async def test_enrollment_timeout_retires_worker(monkeypatch, tmp_path):
    state = fake_encoder_process(monkeypatch, ready=False)
    with pytest.raises(TimeoutError):
        await worker.extract_extraction_reference(tmp_path, [np.ones(48000)] * 3, timeout=0.02)
    assert state.retired.is_set()


@pytest.mark.asyncio
async def test_enrollment_repeated_cancel_waits_for_retirement_and_closes_result(monkeypatch, tmp_path):
    state = fake_encoder_process(monkeypatch, ready=True)
    retirement_started, release = threading.Event(), threading.Event()
    captured = []
    original_type = worker.SpeakerExtractionReference

    def make_reference(identity, embedding):
        reference = original_type(identity, embedding)
        captured.append(reference)
        return reference

    def retire(process):
        retirement_started.set()
        assert release.wait(5)
        state.retired.set()

    monkeypatch.setattr(worker, "SpeakerExtractionReference", make_reference)
    monkeypatch.setattr(worker, "_stop_encoder_process", retire)
    task = asyncio.create_task(worker.extract_extraction_reference(tmp_path, [np.ones(48000)] * 3))
    assert await asyncio.to_thread(retirement_started.wait, 1)
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert captured[0].closed and state.retired.is_set()
    assert all(not pcm.any() for pcm in state.recordings)


def test_encoder_retirement_escalates_and_never_claims_live_process_closed():
    calls = []
    state = SimpleNamespace(alive=True)
    process = SimpleNamespace(is_alive=lambda: state.alive,
                              terminate=lambda: calls.append("terminate"),
                              join=lambda timeout: calls.append(("join", timeout)),
                              kill=lambda: (calls.append("kill"), setattr(state, "alive", False)),
                              close=lambda: calls.append("close"))
    worker._stop_encoder_process(process)
    assert calls == ["terminate", ("join", 0.5), "kill", ("join", 0.5), "close"]
    calls.clear()
    state.alive = True
    process.kill = lambda: calls.append("kill")
    with pytest.raises(TseModelError):
        worker._stop_encoder_process(process)
    assert "close" not in calls


@pytest.mark.asyncio
async def test_enrollment_rejects_invalid_contract_before_process_creation(tmp_path):
    for references in ([], [np.ones(48000)] * 4, [np.ones(48000), np.ones(10), np.ones(48000)],
                       [np.ones(48000), np.full(48000, np.nan), np.ones(48000)]):
        with pytest.raises(ValueError):
            await worker.extract_extraction_reference(tmp_path, references)


@pytest.mark.asyncio
async def test_unconfirmed_encoder_retirement_keeps_owner_until_process_exits(monkeypatch, tmp_path):
    state = fake_encoder_process(monkeypatch, ready=True)
    release = threading.Event()
    captured = []
    reference_type = worker.SpeakerExtractionReference

    def make_reference(identity, embedding):
        reference = reference_type(identity, embedding)
        captured.append(reference)
        return reference

    def retire(process):
        if not release.is_set():
            raise TseModelError("process still alive")
        state.retired.set()

    monkeypatch.setattr(worker, "SpeakerExtractionReference", make_reference)
    monkeypatch.setattr(worker, "_stop_encoder_process", retire)
    with pytest.raises(worker.TseEncoderRetirementError) as raised:
        await worker.extract_extraction_reference(tmp_path, [np.ones(48000)] * 3)
    error = raised.value
    try:
        assert not error.retirement_owner.confirmed_stopped
        assert not error.retirement_task.done()
        assert captured[0].closed
        assert all(not pcm.any() for pcm in state.recordings)
        assert not state.receiver_closed
    finally:
        release.set()
        await asyncio.wait_for(asyncio.shield(error.retirement_task), 2)
    assert error.retirement_owner.confirmed_stopped
    assert state.retired.is_set() and state.receiver_closed


@pytest.mark.asyncio
async def test_retirement_waiter_cancellation_does_not_drop_native_owner(monkeypatch):
    release = threading.Event()
    closed = threading.Event()
    process = object()
    receiver = SimpleNamespace(close=closed.set)

    def retire(process):
        if not release.is_set():
            raise TseModelError("process still alive")

    monkeypatch.setattr(worker, "_stop_encoder_process", retire)
    startup = asyncio.create_task(asyncio.sleep(0, result=(process, receiver)))
    with pytest.raises(worker.TseEncoderRetirementError) as raised:
        await worker._finish_encoder_process(startup)
    error = raised.value
    error.retirement_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(error.retirement_task, 0.1)
    assert not error.retirement_owner.confirmed_stopped
    release.set()
    async with asyncio.timeout(2):
        while not error.retirement_owner.confirmed_stopped:
            await asyncio.sleep(0.01)
    assert closed.is_set()
    assert error.retirement_task.cancelled()


@pytest.mark.asyncio
async def test_failed_startup_with_live_process_creates_retirement_owner(monkeypatch):
    release, closed = threading.Event(), threading.Event()
    process = object()
    receiver = SimpleNamespace(close=closed.set)

    def retire(process):
        if not release.is_set():
            raise TseModelError("process still alive")

    async def failed_startup():
        raise worker._EncoderStartupRetirementPending(process, receiver)

    monkeypatch.setattr(worker, "_stop_encoder_process", retire)
    startup = asyncio.create_task(failed_startup())
    with pytest.raises(worker.TseEncoderRetirementError) as raised:
        await worker._finish_encoder_process(startup)
    error = raised.value
    assert not error.retirement_owner.confirmed_stopped
    release.set()
    await asyncio.wait_for(asyncio.shield(error.retirement_task), 2)
    assert error.retirement_owner.confirmed_stopped and closed.is_set()


@pytest.mark.asyncio
async def test_real_encoder_child_process_returns_and_exits():
    directory = os.environ.get("NEKO_TSE_TEST_ASSET_DIR")
    if not directory:
        pytest.skip("local models not supplied")
    before = {process.pid for process in multiprocessing.active_children()}
    pcm = np.random.default_rng(303).normal(0, 0.05, 48000).astype(np.float32)
    reference = await worker.extract_extraction_reference(Path(directory), [pcm] * 3)
    assert reference.model_identity == TSE_ENCODER_IDENTITY
    assert reference.copy_embedding().shape == (192,)
    assert np.isfinite(reference.copy_embedding()).all()
    assert {process.pid for process in multiprocessing.active_children()} == before
    reference.close()
