import asyncio
from pathlib import Path
import threading
from types import SimpleNamespace

import numpy as np
import pytest

from main_logic.voice_identity.pvad import models


def test_frontend_matches_frozen_official_speechbrain_features():
    # Fixture created with SpeechBrain Fbank(n_mels=80) and sentence CMN,
    # independently of this implementation. Its edges catch reflect padding;
    # the frozen bank catches the misleading 80x201 binary filename layout.
    with np.load(Path(__file__).with_name("speechbrain_features.npz")) as fixture:
        pcm = np.random.default_rng(428).integers(-15000, 15000, 24000, dtype=np.int16)
        actual = models.ecapa_features(pcm.astype("<i2").tobytes(), fixture["matrix"])
        np.testing.assert_allclose(actual, fixture["features"], atol=0.001, rtol=0)


@pytest.mark.parametrize("pcm", [b"", b"x", bytes(47998), bytes(160002)],
                         ids=["empty", "odd", "too_short", "too_long"])
def test_ecapa_frontend_rejects_invalid_duration(pcm):
    with pytest.raises(ValueError, match="invalid_ecapa_audio"):
        models.ecapa_features(pcm, np.ones((80, 201), np.float32))


def test_ecapa_frontend_rejects_bad_matrix_and_silence_reference(tmp_path):
    with pytest.raises(ValueError, match="filterbank"):
        models.ecapa_features(bytes(48000), np.full((80, 201), np.nan))
    with pytest.raises(ValueError, match="insufficient_ecapa_audio"):
        models.EcapaExtractor(tmp_path).extract(bytes(48000))


@pytest.mark.parametrize("outcome", ["valid", "nan", "zero", "shape", "cancel"])
def test_ecapa_extractor_validates_output_and_wipes_features_on_every_exit(
    monkeypatch, tmp_path, outcome,
):
    import onnxruntime as ort

    with np.load(Path(__file__).with_name("speechbrain_features.npz")) as fixture:
        fixture["matrix"].T.astype("<f4").tofile(tmp_path / models.ECAPA_ASSETS[1].filename)
    monkeypatch.setattr(models, "verify_asset", lambda directory, asset: directory / asset.filename)
    extractor = models.EcapaExtractor(tmp_path)
    observed = []
    class Session:
        def get_inputs(self):
            return [SimpleNamespace(name="features", type="tensor(float)", shape=[None, None, 80]),
                    SimpleNamespace(name="feature_lens", type="tensor(float)", shape=[None])]

        def get_outputs(self):
            return [SimpleNamespace(name="embedding", type="tensor(float)", shape=[None, 192])]

        def run(self, names, inputs, options):
            observed.append(inputs["features"])
            output = np.arange(1, 193, dtype=np.float32)[None]
            if outcome == "nan":
                output[0, 0] = np.nan
            elif outcome == "zero":
                output.fill(0)
            elif outcome == "shape":
                output = output[:, :-1].copy()
            elif outcome == "cancel":
                extractor.cancel()
            observed.append(output)
            return [output]
    monkeypatch.setattr(ort, "InferenceSession", lambda *args, **kwargs: Session())
    pcm = np.random.default_rng(428).integers(-15000, 15000, 24000, dtype=np.int16).tobytes()
    if outcome == "valid":
        embedding = extractor.extract(pcm)
        assert embedding.shape == (192,) and np.linalg.norm(embedding) == pytest.approx(1)
        assert embedding.any()
        embedding.fill(0)
    else:
        with pytest.raises(RuntimeError if outcome == "cancel" else ValueError):
            extractor.extract(pcm)
    assert all(not array.any() for array in observed)


def test_pre_cancelled_ecapa_does_not_load_model(monkeypatch, tmp_path):
    import onnxruntime as ort
    with np.load(Path(__file__).with_name("speechbrain_features.npz")) as fixture:
        fixture["matrix"].T.astype("<f4").tofile(tmp_path / models.ECAPA_ASSETS[1].filename)
    monkeypatch.setattr(models, "verify_asset", lambda directory, asset: directory / asset.filename)
    monkeypatch.setattr(ort, "InferenceSession", lambda *args, **kwargs: pytest.fail("loaded after cancel"))
    extractor = models.EcapaExtractor(tmp_path)
    extractor.cancel()
    with pytest.raises(RuntimeError, match="ecapa_cancelled"):
        extractor.extract(b"\x01\x00" * 24000)


class ProbabilitySession:
    def __init__(self, probabilities):
        self.probabilities = probabilities
        self.index = 0
        self.initial_states = []
        self.outputs = []

    def run(self, names, values):
        if self.index % len(self.probabilities) == 0:
            self.initial_states.append((values["mel_buffer"].copy(), values["gru_buffer"].copy()))
        value = self.probabilities[self.index % len(self.probabilities)]
        self.index += 1
        result = [np.zeros((1, 1), np.float32), np.array([[value]], np.float32),
                  np.ones((1, 80, 15), np.float32), np.ones((2, 1, 256), np.float32)]
        self.outputs.extend(result)
        return result


def ready_model(probabilities):
    model = models.FireRedPvad(np.ones(192, np.float32))
    model._session = ProbabilitySession(probabilities)
    return model


def test_first_sample_initializes_exp_filter_and_160ms_is_sustained():
    model = ready_model([0.75] * 20)
    try:
        assert model.score(bytes(6400), 16000) == pytest.approx(0.75)
    finally:
        model.close()


def test_smoothing_and_short_excursion_do_not_manufacture_sustained_activity():
    sequence = [0.0] * 10 + [1.0] * 8 + [0.0] * 12
    expected = []
    smoothed = None
    for probability in sequence:
        smoothed = probability if smoothed is None else 0.8 * smoothed + 0.2 * probability
        expected.append(smoothed)
    model = ready_model(sequence)
    try:
        assert model.score(bytes(len(sequence) * 320), 16000) == pytest.approx(
            max(min(expected[i:i + 16]) for i in range(len(expected) - 15)))
        assert model.score(bytes(len(sequence) * 320), 16000) < 0.5
    finally:
        model.close()


def test_candidates_have_fresh_states_outputs_are_wiped_and_tail_is_uncovered():
    model = ready_model([0.75] * 20)
    session = model._session
    try:
        first = model.analyze(bytes(6402), 16000)
        second = model.analyze(bytes(6402), 16000)
        assert first == second
        assert (first.captured_samples, first.covered_samples, first.frame_count) == (3201, 3200, 20)
        assert all(not value.any() for states in session.initial_states for value in states)
        assert all(not value.any() for value in session.outputs)
    finally:
        model.close()


@pytest.mark.parametrize("samples, accepted", [(3184, False), (3199, False), (3200, True),
    (3216, True), (23984, True), (23999, True), (24000, False), (24016, False)])
def test_exact_sample_duration_boundary(samples, accepted):
    model = ready_model([0.6])
    try:
        if accepted:
            result = model.analyze(bytes(samples * 2), 16000)
            assert result.covered_samples == samples // 160 * 160
        else:
            with pytest.raises(ValueError, match="unsupported_pvad_duration"):
                model.analyze(bytes(samples * 2), 16000)
    finally:
        model.close()


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -0.1, 1.1])
def test_invalid_probability_is_unavailable_not_low_score_and_wipes(value):
    model = ready_model([value])
    session = model._session
    try:
        with pytest.raises(ValueError, match="invalid_pvad"):
            model.score(bytes(6400), 16000)
        assert all(not output.any() for output in session.outputs)
    finally:
        model.close()


@pytest.mark.parametrize("reference", [np.zeros(192), np.ones(191), np.full(192, np.nan)])
def test_invalid_reference_is_rejected(reference):
    with pytest.raises(ValueError):
        models.FireRedPvad(reference)


def test_close_wipes_reference_and_is_terminal():
    model = ready_model([0.6])
    reference = model._reference
    model.close()
    model.close()
    assert not reference.any()
    with pytest.raises(RuntimeError, match="pvad_closed"):
        model.load()
    with pytest.raises(RuntimeError, match="pvad_closed"):
        model.score(bytes(6400), 16000)


def test_bundled_real_pvad_contract_and_recurrent_isolation():
    model = models.FireRedPvad(np.ones(192, np.float32))
    try:
        assert model.load()
        first = model.analyze(bytes(6400), 16000)
        second = model.analyze(bytes(6400), 16000)
        assert first == second
        assert 0 <= first.sustained_score <= 1
    finally:
        model.close()


class FakeProcess:
    def __init__(self, *, target, args, **kwargs):
        self.alive = False
        self.closed = False
        self.terminated = False
        self.joined = False

    def start(self):
        self.alive = True

    def is_alive(self):
        return self.alive

    def terminate(self):
        self.terminated = True
        self.alive = False

    def kill(self):
        self.alive = False

    def join(self, timeout):
        self.joined = True

    def close(self):
        self.closed = True


class IdlePipe:
    def close(self):
        pass

    def poll(self):
        return False


class ProcessContext:
    def Pipe(self, **kwargs):
        return IdlePipe(), IdlePipe()

    def Process(self, **kwargs):
        self.process = FakeProcess(**kwargs)
        return self.process


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_extraction_timeout_and_cancel_retire_owned_process(monkeypatch, tmp_path, cancel):
    context = ProcessContext()
    monkeypatch.setattr(models.multiprocessing, "get_context", lambda method: context)
    task = asyncio.create_task(models.extract_activity_reference(tmp_path, bytes(48000), timeout=0.03))
    if cancel:
        await asyncio.sleep(0.01)
        task.cancel()
    with pytest.raises(asyncio.CancelledError if cancel else TimeoutError):
        await task
    assert context.process.terminated and context.process.joined and context.process.closed


@pytest.mark.asyncio
async def test_real_worker_failure_is_joined_and_not_left_in_process_tree(tmp_path):
    before = {process.pid for process in models.multiprocessing.active_children()}
    with pytest.raises(RuntimeError, match="ecapa_extraction_failed"):
        await models.extract_activity_reference(tmp_path, b"\x01\x00" * 24000, timeout=10)
    assert {process.pid for process in models.multiprocessing.active_children()} == before


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["cancel", "timeout"])
async def test_blocked_spawn_keeps_loop_responsive_and_retains_handles_until_retired(
    monkeypatch, tmp_path, outcome,
):
    entered, release = threading.Event(), threading.Event()
    class TrackedPipe(IdlePipe):
        closed = False

        def close(self):
            self.closed = True

    class SlowProcess(FakeProcess):
        def start(self):
            entered.set()
            assert release.wait(2)
            super().start()

    class SlowContext(ProcessContext):
        def Pipe(self, **kwargs):
            self.pipes = TrackedPipe(), TrackedPipe()
            return self.pipes

        def Process(self, **kwargs):
            self.process = SlowProcess(**kwargs)
            return self.process

    context = SlowContext()
    monkeypatch.setattr(models.multiprocessing, "get_context", lambda method: context)
    loop = asyncio.get_running_loop()
    began = loop.time()
    task = asyncio.create_task(models.extract_activity_reference(
        tmp_path, bytes(48000), timeout=0.03 if outcome == "timeout" else 1))
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        if outcome == "cancel":
            task.cancel()
        for _ in range(10):
            await asyncio.sleep(0.005)
        assert loop.time() - began < 0.3  # A blocked native spawn must not block these heartbeats.
        assert not task.done()  # Retirement must first recover the startup result.
        if outcome == "cancel":
            task.cancel()  # Repeated cancellation cannot abandon ownership.
        await asyncio.sleep(0.01)
        assert not task.done()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError if outcome == "cancel" else TimeoutError):
        await asyncio.wait_for(task, 1)
    assert context.process.terminated and context.process.joined and context.process.closed
    assert all(pipe.closed for pipe in context.pipes)
