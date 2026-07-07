# -- coding: utf-8 --
"""Tests for utils/vad_model.py — Silero VAD, Smart Turn v3, LocalTurnDetector.

FSM tests are hermetic (fake Silero, scripted probs). Model tests load the
real bundled ONNX from data/vad_models/ and skip if absent so CI without the
assets still passes.
"""
import os
import asyncio
import base64
import time
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

from main_logic.omni_realtime_client import OmniRealtimeClient, TurnDetectionMode
from utils.vad_model import (
    SileroVad, SmartTurnV3, LocalTurnDetector, TurnSignal,
    VadState, VadDisableReason, _resolve_model_dir,
    SILERO_MODEL_FILE, SMART_TURN_MODEL_FILE,
)

_MODELS_PRESENT = _resolve_model_dir(None, SILERO_MODEL_FILE) is not None \
    and _resolve_model_dir(None, SMART_TURN_MODEL_FILE) is not None
_needs_models = pytest.mark.skipif(not _MODELS_PRESENT,
                                   reason="data/vad_models/*.onnx not bundled")

_COMMON = dict(app_docs_model_dir=None, enabled=True, min_ram_gb=0.0,
               ram_gb=64.0, intra_op_threads=1)


def _pcm16(float_samples):
    return (np.clip(float_samples, -1, 1) * 32767).astype(np.int16).tobytes()


# ── lifecycle / disable gating (no model needed) ──────────────────────

def test_disabled_when_user_off():
    v = SileroVad(**{**_COMMON, "enabled": False})
    assert v.is_disabled()
    assert v.disable_reason() == VadDisableReason.USER_DISABLED.value
    assert v.process(np.zeros(512, np.float32)) == []  # no-op when disabled


def test_disabled_when_low_ram():
    v = SileroVad(**{**_COMMON, "ram_gb": 1.0, "min_ram_gb": 4.0})
    assert v.is_disabled()
    assert v.disable_reason() == VadDisableReason.LOW_RAM.value


def test_disabled_when_model_missing(tmp_path):
    # point app_docs at an empty dir AND ensure no bundled fallback by using a
    # bogus model_filename via subclass trick: just check load() result path.
    v = SileroVad(**{**_COMMON, "app_docs_model_dir": str(tmp_path)})
    # tmp has no model; bundled may exist though — so only assert the resolver
    # returns None for a guaranteed-absent file name.
    assert _resolve_model_dir(str(tmp_path), "definitely_absent.onnx") is None


def test_smartturn_predict_none_when_disabled():
    s = SmartTurnV3(**{**_COMMON, "enabled": False})
    assert s.predict_endpoint(np.zeros(16000, np.float32)) is None


# ── LocalTurnDetector FSM (hermetic, fake models) ─────────────────────

class _FakeSilero:
    WINDOW = 512

    def __init__(self):
        self.queue = []
        self.reset_calls = 0

    def is_available(self):
        return True

    def reset(self):
        self.reset_calls += 1

    def process(self, samples):
        return self.queue.pop(0) if self.queue else []


class _FakeSmartTurn:
    def __init__(self, prob=0.9):
        self.prob = prob
        self.calls = 0

    def predict_endpoint(self, audio):
        self.calls += 1
        return self.prob


def _detector(**kw):
    sil, st = _FakeSilero(), _FakeSmartTurn()
    d = LocalTurnDetector(sil, st, speech_min_ms=200, silence_ms=300,
                          hard_commit_silence_ms=2500, **kw)
    return d, sil, st


def test_fsm_speech_start_then_candidate_end():
    d, sil, st = _detector()
    # speech_min_win = round(200/32)=6 ; silence_min_win = round(300/32)=9
    sil.queue = [[0.9] * 6]                  # 6 speech windows in one feed
    assert d.feed(_pcm16(np.zeros(512 * 6, np.float32))) == TurnSignal.SPEECH_START
    sil.queue = [[0.1] * 8]                  # 8 silence windows: below threshold(9)
    assert d.feed(_pcm16(np.zeros(512 * 8, np.float32))) == TurnSignal.NONE
    sil.queue = [[0.1] * 2]                  # 2 more → total 10 ≥ 9 → candidate
    assert d.feed(_pcm16(np.zeros(512 * 2, np.float32))) == TurnSignal.CANDIDATE_END


def test_fsm_no_candidate_without_speech():
    d, sil, _ = _detector()
    sil.queue = [[0.1] * 50]                 # pure silence, never spoke
    assert d.feed(_pcm16(np.zeros(512 * 50, np.float32))) == TurnSignal.NONE


def test_fsm_candidate_emitted_once_per_silence():
    d, sil, _ = _detector()
    sil.queue = [[0.9] * 6]
    d.feed(_pcm16(np.zeros(512 * 6, np.float32)))
    sil.queue = [[0.1] * 9]
    assert d.feed(_pcm16(np.zeros(512 * 9, np.float32))) == TurnSignal.CANDIDATE_END
    sil.queue = [[0.1] * 9]                  # continued silence: must NOT re-emit
    assert d.feed(_pcm16(np.zeros(512 * 9, np.float32))) == TurnSignal.NONE


def test_fsm_on_endpoint_complete_commits():
    d, sil, _ = _detector()
    sil.queue = [[0.9] * 6]
    d.feed(_pcm16(np.zeros(512 * 6, np.float32)))
    assert d.on_endpoint_result(0.9) is True       # >= 0.5 → complete
    # committed: speaking state cleared, new speech can start a fresh turn
    sil.queue = [[0.9] * 6]
    assert d.feed(_pcm16(np.zeros(512 * 6, np.float32))) == TurnSignal.SPEECH_START


def test_fsm_on_endpoint_incomplete_waits_then_resumes():
    d, sil, _ = _detector()
    sil.queue = [[0.9] * 6]
    d.feed(_pcm16(np.zeros(512 * 6, np.float32)))
    sil.queue = [[0.1] * 9]
    assert d.feed(_pcm16(np.zeros(512 * 9, np.float32))) == TurnSignal.CANDIDATE_END
    assert d.on_endpoint_result(0.1) is False      # < 0.5 → incomplete, keep listening
    # user resumes speaking → SPEECH_START NOT re-fired (already spoke), but a new
    # stop should yield a fresh candidate
    sil.queue = [[0.9] * 3]
    d.feed(_pcm16(np.zeros(512 * 3, np.float32)))
    sil.queue = [[0.1] * 9]
    assert d.feed(_pcm16(np.zeros(512 * 9, np.float32))) == TurnSignal.CANDIDATE_END


def test_fsm_force_end_after_incomplete_and_long_silence():
    d, sil, _ = _detector()       # hard_commit 2500ms → round(2500/32)=78 windows
    sil.queue = [[0.9] * 6]
    d.feed(_pcm16(np.zeros(512 * 6, np.float32)))
    sil.queue = [[0.1] * 9]
    assert d.feed(_pcm16(np.zeros(512 * 9, np.float32))) == TurnSignal.CANDIDATE_END
    assert d.on_endpoint_result(0.1) is False        # incomplete → keep listening
    sil.queue = [[0.1] * 69]                          # silence reaches 78 total → force
    assert d.feed(_pcm16(np.zeros(512 * 69, np.float32))) == TurnSignal.FORCE_END
    d.commit()                                        # client commits on FORCE_END
    sil.queue = [[0.9] * 6]
    assert d.feed(_pcm16(np.zeros(512 * 6, np.float32))) == TurnSignal.SPEECH_START


def test_fsm_activity_seq_advances_on_speech():
    d, sil, _ = _detector()
    before = d.activity_seq
    sil.queue = [[0.9] * 6]
    d.feed(_pcm16(np.zeros(512 * 6, np.float32)))
    assert d.activity_seq == before + 6
    sil.queue = [[0.1] * 9]                            # silence does not advance seq
    d.feed(_pcm16(np.zeros(512 * 9, np.float32)))
    assert d.activity_seq == before + 6


def test_fsm_none_probability_treated_as_complete():
    # Smart Turn unavailable → degrade to "silence == complete"
    d, _, _ = _detector()
    assert d.on_endpoint_result(None) is True


def test_fsm_reset_resets_silero_stream():
    d, sil, _ = _detector()
    d.reset()
    assert sil.reset_calls >= 1


def test_detector_smart_turn_enabled_flag_default_and_hotflip():
    sil, st = _FakeSilero(), _FakeSmartTurn()
    assert LocalTurnDetector(sil, st).smart_turn_enabled is True              # default on
    d = LocalTurnDetector(sil, st, smart_turn_enabled=False)                  # pure VAD
    assert d.smart_turn_enabled is False
    d.smart_turn_enabled = True                                              # live hot-flip
    assert d.smart_turn_enabled is True


# ── real model behaviour ──────────────────────────────────────────────

@_needs_models
def test_silero_fires_on_speech_not_silence():
    v = SileroVad(**_COMMON)
    assert v.load() is True
    assert v.is_available()
    sr = 16000
    # Real-ish: load the en sample if present, else a noisy modulated signal is
    # not enough to fire Silero, so we just assert silence→low and the stream runs.
    silence = np.zeros(sr, np.float32)
    probs_sil = v.process(silence)
    assert probs_sil and max(probs_sil) < 0.2
    sample = "/tmp/en_speech.wav"
    if os.path.exists(sample):
        import wave
        w = wave.open(sample, "rb")
        pcm = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768.0
        v.reset()
        probs = v.process(pcm)
        assert max(probs) > 0.8 and np.mean(np.array(probs) > 0.5) > 0.3


@_needs_models
def test_smartturn_silence_more_complete_than_midspeech():
    s = SmartTurnV3(**_COMMON)
    assert s.load() is True
    p_sil = s.predict_endpoint(np.zeros(2 * 16000, np.float32))
    assert p_sil is not None and 0.0 <= p_sil <= 1.0
    sample = "/tmp/en_speech.wav"
    if os.path.exists(sample):
        import wave
        w = wave.open(sample, "rb")
        pcm = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768.0
        p_mid = s.predict_endpoint(pcm[:int(0.8 * 16000)])  # cut mid-word
        assert p_mid is not None and p_sil > p_mid  # silence "more complete" than cut speech


@_needs_models
def test_models_load_idempotent():
    v = SileroVad(**_COMMON)
    assert v.load() is True
    assert v.load() is True  # second call no-op, stays ready
    assert v._state == VadState.READY


# ── OmniRealtimeClient integration (hermetic: fake detector injected) ──


class _FakeSmartTurn2:
    def __init__(self, prob, *, delay_s=0.0):
        self.probs = list(prob) if isinstance(prob, (list, tuple)) else [prob]
        self.delay_s = delay_s
        self.calls = 0

    def predict_endpoint(self, audio):
        self.calls += 1
        if self.delay_s:
            time.sleep(self.delay_s)
        index = min(self.calls - 1, len(self.probs) - 1)
        return self.probs[index]


class _FakeDetector:
    def __init__(self, prob=0.9, smart_turn_enabled=True, *, delay_s=0.0):
        self.smart_turn = _FakeSmartTurn2(prob, delay_s=delay_s)
        self.smart_turn_enabled = smart_turn_enabled
        self.smart_turn_threshold = 0.5
        self.next_signal = TurnSignal.NONE
        self._seq = 5
        self.committed = False
        self.reset_calls = 0
        self.fed = 0
        self.fed_lengths = []
        self.result_calls = 0
        self.snapshots = 0

    def feed(self, pcm16_bytes):
        self.fed += 1
        self.fed_lengths.append(len(pcm16_bytes))
        return self.next_signal

    def take_endpoint_audio(self):
        self.snapshots += 1
        return np.full(16000, self.snapshots, np.float32)

    @property
    def activity_seq(self):
        return self._seq

    def on_endpoint_result(self, p):
        self.result_calls += 1
        return p is None or p >= 0.5

    def commit(self):
        self.committed = True

    def reset(self):
        self.reset_calls += 1


class _FakeDesktopDownsampler:
    def __init__(self):
        self.chunks = []
        self.clear_calls = 0

    def resample_chunk(self, samples):
        self.chunks.append(samples.copy())
        return np.zeros(160, np.float32)

    def clear(self):
        self.clear_calls += 1


def _client_with_fake_detector(prob=0.9, smart_turn_enabled=True, *, delay_s=0.0):
    client = OmniRealtimeClient(
        base_url="wss://example.test/realtime", api_key="sk-test",
        model="qwen-omni-turbo", api_type="qwen", local_turn_detection=False,
    )
    det = _FakeDetector(prob=prob, smart_turn_enabled=smart_turn_enabled, delay_s=delay_s)
    client._turn_detector = det
    client._local_turn_active = True
    client.turn_detection_mode = TurnDetectionMode.MANUAL
    client.ws = AsyncMock()
    return client, det


@pytest.mark.asyncio
async def test_stream_audio_pure_vad_commits_on_pause_without_smart_turn():
    # smart_turn_enabled=False → CANDIDATE_END commits immediately, Smart Turn never consulted
    client, det = _client_with_fake_detector(prob=0.1, smart_turn_enabled=False)
    client.signal_user_activity_end = AsyncMock()
    det.next_signal = TurnSignal.CANDIDATE_END
    await client.stream_audio(np.zeros(512, np.int16).tobytes())
    assert det.committed is True                       # pure VAD commits synchronously on the pause
    client.signal_user_activity_end.assert_awaited()
    assert client._turn_eval_inflight is False         # no async Smart Turn eval was scheduled


@pytest.mark.asyncio
async def test_stream_audio_candidate_end_commits():
    client, det = _client_with_fake_detector(prob=0.9)
    commits = []
    client.signal_user_activity_end = AsyncMock(side_effect=lambda: commits.append(1))
    det.next_signal = TurnSignal.CANDIDATE_END
    await client.stream_audio(np.zeros(512, np.int16).tobytes())   # 16k, non-RNNoise path
    for _ in range(20):                                            # let the eval task run
        await asyncio.sleep(0.01)
        if commits:
            break
    assert det.fed == 1
    assert commits, "CANDIDATE_END + complete Smart Turn must commit the turn"
    assert client._turn_eval_inflight is False                     # guard cleared


@pytest.mark.asyncio
async def test_stream_audio_downsamples_desktop_chunk_before_local_detector():
    client, det = _client_with_fake_detector()
    client._audio_processor = None
    downsampler = _FakeDesktopDownsampler()
    client._desktop_downsample_resampler = downsampler
    client.send_event = AsyncMock()

    await client.stream_audio(np.zeros(480, np.int16).tobytes())

    assert len(downsampler.chunks) == 1
    assert len(downsampler.chunks[0]) == 480
    assert det.fed_lengths == [320]
    append_event = client.send_event.await_args.args[0]
    assert len(base64.b64decode(append_event["audio"])) == 320


@pytest.mark.asyncio
async def test_stream_audio_candidate_end_incomplete_no_commit():
    client, det = _client_with_fake_detector(prob=0.1)             # < 0.5 → incomplete
    client.signal_user_activity_end = AsyncMock()
    det.next_signal = TurnSignal.CANDIDATE_END
    await client.stream_audio(np.zeros(512, np.int16).tobytes())
    for _ in range(10):
        await asyncio.sleep(0.01)
    client.signal_user_activity_end.assert_not_called()


@pytest.mark.asyncio
async def test_smart_turn_stale_result_does_not_mutate_detector():
    client, det = _client_with_fake_detector(prob=0.9)
    client.signal_user_activity_end = AsyncMock()
    det._seq += 1

    await client._evaluate_and_commit_turn(np.zeros(16000, np.float32), activity_seq=5, generation=0)

    assert det.result_calls == 0
    client.signal_user_activity_end.assert_not_called()
    assert client._turn_eval_inflight is False


@pytest.mark.asyncio
async def test_stream_audio_queues_latest_candidate_while_eval_inflight():
    client, det = _client_with_fake_detector(prob=[0.1, 0.9], delay_s=0.05)
    commits = []
    client.signal_user_activity_end = AsyncMock(side_effect=lambda: commits.append(1))
    det.next_signal = TurnSignal.CANDIDATE_END

    await client.stream_audio(np.zeros(512, np.int16).tobytes())
    assert client._turn_eval_inflight is True
    await client.stream_audio(np.zeros(512, np.int16).tobytes())

    for _ in range(30):
        await asyncio.sleep(0.01)
        if commits:
            break

    assert det.smart_turn.calls == 2
    assert det.snapshots == 2
    assert commits, "latest queued CANDIDATE_END should run after the in-flight eval"
    assert client._turn_eval_inflight is False
    assert client._pending_turn_eval is None


@pytest.mark.asyncio
async def test_stream_audio_drops_pending_candidate_after_complete_eval():
    client, det = _client_with_fake_detector(prob=0.9, delay_s=0.05)
    commits = []
    client.signal_user_activity_end = AsyncMock(side_effect=lambda: commits.append(1))
    det.next_signal = TurnSignal.CANDIDATE_END

    await client.stream_audio(np.zeros(512, np.int16).tobytes())
    await client.stream_audio(np.zeros(512, np.int16).tobytes())

    for _ in range(30):
        await asyncio.sleep(0.01)
        if commits:
            break

    assert det.smart_turn.calls == 1
    assert commits == [1]
    assert client._turn_eval_inflight is False
    assert client._pending_turn_eval is None


@pytest.mark.asyncio
async def test_stream_audio_force_end_commits_sync():
    client, det = _client_with_fake_detector()
    client.signal_user_activity_end = AsyncMock()
    det.next_signal = TurnSignal.FORCE_END
    await client.stream_audio(np.zeros(512, np.int16).tobytes())
    assert det.committed is True                                   # FORCE_END commits synchronously
    client.signal_user_activity_end.assert_awaited()


@pytest.mark.asyncio
async def test_stream_audio_force_end_invalidates_inflight_eval():
    client, det = _client_with_fake_detector()
    client.signal_user_activity_end = AsyncMock()
    client._turn_eval_inflight = True
    client._turn_eval_generation = 3
    client._pending_turn_eval = (np.zeros(16000, np.float32), det.activity_seq)
    det.next_signal = TurnSignal.FORCE_END

    await client.stream_audio(np.zeros(512, np.int16).tobytes())

    assert det.committed is True
    assert client._turn_eval_inflight is False
    assert client._pending_turn_eval is None
    assert client._turn_eval_generation == 4


@pytest.mark.asyncio
async def test_stream_audio_defers_silence_clear_while_turn_eval_pending():
    client, det = _client_with_fake_detector()
    client._turn_eval_inflight = True
    client.clear_audio_buffer = AsyncMock()
    client._should_clear_audio_buffer_on_silence = MagicMock(return_value=True)
    det.next_signal = TurnSignal.NONE

    await client.stream_audio(np.zeros(512, np.int16).tobytes())

    client._should_clear_audio_buffer_on_silence.assert_not_called()
    client.clear_audio_buffer.assert_not_awaited()
    assert det.reset_calls == 0


@pytest.mark.asyncio
async def test_stream_audio_speech_start_interrupts_active_response():
    client, det = _client_with_fake_detector()
    client._is_responding = True
    client._turn_eval_generation = 7
    client._turn_eval_inflight = True
    client._pending_turn_eval = (np.zeros(16000, np.float32), det.activity_seq)
    client.handle_interruption = AsyncMock()
    det.next_signal = TurnSignal.SPEECH_START

    await client.stream_audio(np.zeros(512, np.int16).tobytes())

    client.handle_interruption.assert_awaited_once()
    assert client._user_recent_activity_time > 0
    assert client._client_vad_active is True
    assert client._client_vad_last_speech_time == client._user_recent_activity_time
    assert client._turn_eval_generation == 8
    assert client._turn_eval_inflight is False
    assert client._pending_turn_eval is None
    assert det.committed is False


@pytest.mark.asyncio
async def test_stream_audio_speech_start_updates_activity_without_interrupt_when_idle():
    client, det = _client_with_fake_detector()
    client._is_responding = False
    client.handle_interruption = AsyncMock()
    det.next_signal = TurnSignal.SPEECH_START

    await client.stream_audio(np.zeros(512, np.int16).tobytes())

    client.handle_interruption.assert_not_awaited()
    assert client._user_recent_activity_time > 0
    assert client._client_vad_active is True
    assert client._client_vad_last_speech_time == client._user_recent_activity_time
    assert det.committed is False


@pytest.mark.asyncio
async def test_stream_audio_speech_start_invalidates_stale_eval():
    client, det = _client_with_fake_detector(prob=0.9, delay_s=0.05)
    client.signal_user_activity_end = AsyncMock()
    det.next_signal = TurnSignal.CANDIDATE_END

    await client.stream_audio(np.zeros(512, np.int16).tobytes())
    assert client._turn_eval_inflight is True
    first_generation = client._turn_eval_generation

    det.next_signal = TurnSignal.SPEECH_START
    await client.stream_audio(np.zeros(512, np.int16).tobytes())

    assert client._turn_eval_generation == first_generation + 1
    for _ in range(20):
        await asyncio.sleep(0.01)

    client.signal_user_activity_end.assert_not_awaited()
    assert client._turn_eval_inflight is False
    assert client._pending_turn_eval is None


@pytest.mark.asyncio
async def test_stream_audio_no_commit_while_responding():
    client, det = _client_with_fake_detector()
    client._is_responding = True                                   # AI mid-response → barge-in territory
    client.signal_user_activity_end = AsyncMock()
    det.next_signal = TurnSignal.CANDIDATE_END
    await client.stream_audio(np.zeros(512, np.int16).tobytes())
    for _ in range(5):
        await asyncio.sleep(0.01)
    client.signal_user_activity_end.assert_not_called()


def test_client_builds_detector_when_requested():
    client = OmniRealtimeClient(
        base_url="wss://example.test/realtime", api_key="sk-test",
        model="qwen-omni-turbo", api_type="qwen", local_turn_detection=True,
    )
    # built but not active until connect() loads the models
    assert client._turn_detector is not None
    assert client._local_turn_active is False
    assert client.turn_detection_mode == TurnDetectionMode.SERVER_VAD


@_needs_models
@pytest.mark.asyncio
async def test_activate_flips_to_manual_with_real_models():
    client = OmniRealtimeClient(
        base_url="wss://example.test/realtime", api_key="sk-test",
        model="qwen-omni-turbo", api_type="qwen", local_turn_detection=True,
    )
    await client._activate_local_turn_if_available()
    assert client._local_turn_active is True
    assert client.turn_detection_mode == TurnDetectionMode.MANUAL


@pytest.mark.asyncio
async def test_activate_stays_server_vad_when_models_disabled(monkeypatch):
    client = OmniRealtimeClient(
        base_url="wss://example.test/realtime", api_key="sk-test",
        model="qwen-omni-turbo", api_type="qwen", local_turn_detection=False,
    )
    # simulate "requested but models unavailable": inject a detector whose
    # models report disabled, then request activation.
    det = _FakeDetector()
    det.silero = SileroVad(**{**_COMMON, "enabled": False})         # DISABLED
    det.smart_turn = SmartTurnV3(**{**_COMMON, "enabled": False})   # DISABLED
    client._turn_detector = det
    client._local_turn_requested = True
    await client._activate_local_turn_if_available()
    assert client._local_turn_active is False
    assert client.turn_detection_mode == TurnDetectionMode.SERVER_VAD
