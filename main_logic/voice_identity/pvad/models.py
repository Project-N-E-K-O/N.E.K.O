"""CPU-only ECAPA enrollment and causal target-speaker activity inference."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import math
import multiprocessing
from pathlib import Path
import threading

import numpy as np

from main_logic.voice_identity.reference import SpeakerReference
from .assets import ECAPA_ASSETS, ECAPA_IDENTITY, bundled_pvad, verify_asset

SAMPLE_RATE = 16000
MINIMUM_SHORT_SAMPLES = 3200
FIRST_CHECKPOINT_SAMPLES = 24000


def session_options():
    import onnxruntime as ort

    options = ort.SessionOptions()
    options.intra_op_num_threads = options.inter_op_num_threads = 1
    options.enable_cpu_mem_arena = False
    options.add_session_config_entry("session.intra_op.allow_spinning", "0")
    options.add_session_config_entry("session.inter_op.allow_spinning", "0")
    return options


def ecapa_features(pcm16: bytes, matrix: np.ndarray) -> np.ndarray:
    """SpeechBrain Fbank: centered zero-padded STFT, Hamming, power, dB, CMN."""
    if not isinstance(pcm16, bytes) or len(pcm16) % 2 or not 48000 <= len(pcm16) <= 160000:
        raise ValueError("invalid_ecapa_audio")
    if matrix.shape != (80, 201) or not np.isfinite(matrix).all() or (matrix < 0).any():
        raise ValueError("invalid_ecapa_filterbank")
    samples = np.frombuffer(pcm16, dtype="<i2").astype(np.float32) / np.float32(32768)
    padded = np.pad(samples, (200, 200), mode="constant")
    window = (0.54 - 0.46 * np.cos(2 * np.pi * np.arange(400) / 400)).astype(np.float32)
    frames = np.lib.stride_tricks.sliding_window_view(padded, 400)[::160] * window
    spectrum = np.fft.rfft(frames, n=400).astype(np.complex64)
    power = (spectrum.real ** 2 + spectrum.imag ** 2).astype(np.float32)
    bands = power @ matrix.T
    features = (10 * np.log10(np.maximum(bands, np.float32(1e-10)))).astype(np.float32)
    np.maximum(features, features.max() - 80, out=features)
    features -= features.mean(axis=0, keepdims=True)
    for value in (samples, padded, frames, spectrum, power, bands):
        value.fill(0)
    return features


class EcapaExtractor:
    """One enrollment-owned ORT session, released after extracting a reference."""

    def __init__(self, directory: Path) -> None:
        import onnxruntime as ort

        self.directory = directory
        self._lock = threading.Lock()
        self._cancelled = False
        self._run_options = ort.RunOptions()
        self._load_options = session_options()

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True
            self._run_options.terminate = True
            cancel_load = getattr(self._load_options, "set_load_cancellation_flag", None)
            if cancel_load is not None:
                cancel_load(True)

    def extract(self, pcm16: bytes) -> np.ndarray:
        import onnxruntime as ort

        if not isinstance(pcm16, bytes) or len(pcm16) % 2 or not 48000 <= len(pcm16) <= 160000:
            raise ValueError("invalid_ecapa_audio")
        if not np.any(np.frombuffer(pcm16, dtype="<i2")):
            raise ValueError("insufficient_ecapa_audio")
        features = output = matrix = None
        session = None
        try:
            path = verify_asset(self.directory, ECAPA_ASSETS[0])
            bank = verify_asset(self.directory, ECAPA_ASSETS[1])
            # The upstream filename describes mel x FFT dimensions, but its
            # stored values are row-major FFT x mel, exactly SpeechBrain's bank.
            matrix = np.fromfile(bank, dtype="<f4").reshape(201, 80).T
            with self._lock:
                if self._cancelled:
                    raise RuntimeError("ecapa_cancelled")
            session = ort.InferenceSession(str(path), sess_options=self._load_options,
                                           providers=["CPUExecutionProvider"])
            _validate_ecapa_session(session)
            features = ecapa_features(pcm16, matrix)
            output = session.run(["embedding"], {
                "features": features[None], "feature_lens": np.ones(1, dtype=np.float32),
            }, self._run_options)[0]
            if output.shape != (1, 192) or not np.isfinite(output).all():
                raise ValueError("invalid_ecapa_embedding")
            norm = float(np.linalg.norm(output))
            if norm <= 1e-12:
                raise ValueError("invalid_ecapa_embedding")
            with self._lock:
                if self._cancelled:
                    raise RuntimeError("ecapa_cancelled")
            return np.array(output[0] / norm, dtype=np.float32, copy=True)
        finally:
            for value in (features, output, matrix):
                if value is not None:
                    value.fill(0)
            session = None


async def extract_activity_reference(directory: Path, pcm16: bytes, *, timeout: float = 30) -> SpeakerReference:
    """Extract in an owned process; cancellation joins retirement before returning.

    A cancelled worker is terminated, then killed if necessary, before this
    coroutine exits. Native process startup is joined before retirement. The
    caller must still fence the returned reference against enrollment ownership.
    """
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("invalid_ecapa_timeout")
    if not isinstance(pcm16, bytes) or len(pcm16) % 2 or not 48000 <= len(pcm16) <= 160000:
        raise ValueError("invalid_ecapa_audio")
    deadline = asyncio.get_running_loop().time() + timeout
    startup = asyncio.create_task(asyncio.to_thread(_start_ecapa_process, directory, pcm16))
    embedding = None
    reference = None
    try:
        # Windows spawn can block while bootstrapping/importing the child.
        # Shield startup so timeout/cancellation can still recover its handles.
        process, receiver = await asyncio.wait_for(asyncio.shield(startup), timeout)
        while not receiver.poll():
            if not process.is_alive():
                raise RuntimeError("ecapa_worker_failed")
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError("ecapa_extraction_timeout")
            await asyncio.sleep(0.01)
        valid, result = receiver.recv()
        if not valid:
            raise RuntimeError(result)
        embedding = result
        reference = SpeakerReference(ECAPA_IDENTITY, embedding)
    finally:
        if embedding is not None:
            embedding.fill(0)
        retirement = asyncio.create_task(_finish_ecapa_process(startup))
        interrupted = False
        try:
            while True:
                try:
                    await asyncio.shield(retirement)
                    break
                except asyncio.CancelledError:
                    interrupted = True
                    if retirement.cancelled():
                        raise
        except BaseException:
            if reference is not None:
                reference.close()
            raise
        if interrupted:
            if reference is not None:
                reference.close()
            raise asyncio.CancelledError
    return reference


def _start_ecapa_process(directory: Path, pcm16: bytes):
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = None
    try:
        process = context.Process(
            target=_ecapa_worker, args=(sender, str(directory), pcm16),
            name="neko-ecapa-enrollment", daemon=True,
        )
        process.start()
        sender.close()
        return process, receiver
    except BaseException:
        receiver.close()
        sender.close()
        if process is not None:
            if process.pid is not None:
                _stop_ecapa_process(process)
            else:
                process.close()
        raise


async def _finish_ecapa_process(startup) -> None:
    try:
        process, receiver = await startup
    except Exception:
        # Startup owns cleanup if it fails before returning the handles.
        return
    receiver.close()
    await _retire_ecapa_process(process)


def _ecapa_worker(sender, directory: str, pcm16: bytes) -> None:
    embedding = None
    try:
        embedding = EcapaExtractor(Path(directory)).extract(pcm16)
        sender.send((True, embedding))
    except Exception:
        sender.send((False, "ecapa_extraction_failed"))
    finally:
        if embedding is not None:
            embedding.fill(0)
        sender.close()


async def _retire_ecapa_process(process) -> None:
    await asyncio.to_thread(_stop_ecapa_process, process)


def _stop_ecapa_process(process) -> None:
    if process.is_alive():
        process.terminate()
    process.join(0.5)
    if process.is_alive():
        process.kill()
        process.join(0.5)
    if process.is_alive():
        raise RuntimeError("ecapa_worker_retirement_failed")
    process.close()


@dataclass(frozen=True, slots=True)
class PvadActivityEvidence:
    """Target activity over real complete frames, never an identity verdict."""

    sustained_score: float
    captured_samples: int
    covered_samples: int
    frame_count: int


class FireRedPvad:
    """Process an exact candidate in 10 ms causal frames with fresh recurrent state."""

    def __init__(self, reference: np.ndarray) -> None:
        owned = SpeakerReference(ECAPA_IDENTITY, reference)
        try:
            self._reference = owned.copy_embedding().reshape(1, 192)
        finally:
            owned.close()
        self._session = None
        self._closed = False
        self._lock = threading.Lock()

    def load(self) -> bool:
        import onnxruntime as ort

        with self._lock:
            if self._closed:
                raise RuntimeError("pvad_closed")
            if self._session is None:
                session = ort.InferenceSession(
                    str(bundled_pvad()), sess_options=session_options(),
                    providers=["CPUExecutionProvider"],
                )
                _validate_pvad_session(session)
                self._session = session
            return True

    def score(self, pcm16: bytes, sample_rate_hz: int) -> float:
        return self.analyze(pcm16, sample_rate_hz).sustained_score

    def analyze(self, pcm16: bytes, sample_rate_hz: int) -> PvadActivityEvidence:
        """Run one isolated candidate and report exactly its covered samples."""
        with self._lock:
            return self._analyze(pcm16, sample_rate_hz)

    def _analyze(self, pcm16: bytes, sample_rate_hz: int) -> PvadActivityEvidence:
        if self._closed:
            raise RuntimeError("pvad_closed")
        if not isinstance(pcm16, bytes):
            raise ValueError("invalid_pvad_audio")
        count = len(pcm16) // 2
        if sample_rate_hz != SAMPLE_RATE or len(pcm16) % 2:
            raise ValueError("invalid_pvad_audio")
        if not MINIMUM_SHORT_SAMPLES <= count < FIRST_CHECKPOINT_SAMPLES:
            raise ValueError("unsupported_pvad_duration")
        if self._session is None:
            raise RuntimeError("pvad_unavailable")
        audio = np.frombuffer(pcm16, dtype="<i2").astype(np.float32) / np.float32(32768)
        mel = np.zeros((1, 80, 15), dtype=np.float32)
        gru = np.zeros((2, 1, 256), dtype=np.float32)
        probabilities: list[float] = []
        filtered = None
        output = None
        try:
            for start in range(0, count - 159, 160):
                output = self._session.run(None, {
                    "input_audio": audio[start:start + 160][None], "spkemb": self._reference,
                    "mel_buffer": mel, "gru_buffer": gru,
                })
                if (
                    len(output) != 4 or output[1].shape != (1, 1)
                    or output[2].shape != (1, 80, 15) or output[3].shape != (2, 1, 256)
                    or any(value.dtype != np.float32 or not np.isfinite(value).all() for value in output)
                ):
                    raise ValueError("invalid_pvad_output")
                probability = float(output[1].reshape(-1)[0])
                if not np.isfinite(probability) or not 0 <= probability <= 1:
                    raise ValueError("invalid_pvad_probability")
                mel.fill(0)
                gru.fill(0)
                mel, gru = output[2], output[3]
                filtered = probability if filtered is None else 0.8 * filtered + 0.2 * probability
                probabilities.append(filtered)
                output[0].fill(0)
                output[1].fill(0)
                output = None
            # Upstream ExpFilter(alpha=.8), initialized by the first sample,
            # followed by >=.5 for 16 consecutive 10ms frames. This continuous
            # score reports the strongest such run, not a non-owner verdict.
            score = max(min(probabilities[i:i + 16]) for i in range(len(probabilities) - 15))
            return PvadActivityEvidence(score, count, len(probabilities) * 160, len(probabilities))
        finally:
            audio.fill(0)
            mel.fill(0)
            gru.fill(0)
            if output is not None:
                for value in output:
                    value.fill(0)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._session = None
            self._reference.fill(0)


def _validate_pvad_session(session) -> None:
    expected_inputs = {
        "input_audio": (None, 160), "spkemb": (None, 192),
        "mel_buffer": (None, 80, 15), "gru_buffer": (2, None, 256),
    }
    inputs = {value.name: value for value in session.get_inputs()}
    if set(inputs) != set(expected_inputs):
        raise ValueError("invalid_pvad_model_contract")
    for name, shape in expected_inputs.items():
        value = inputs[name]
        if (value.type != "tensor(float)" or len(value.shape) != len(shape)
            or any(expected is not None and actual != expected
                   for actual, expected in zip(value.shape, shape))):
            raise ValueError("invalid_pvad_model_contract")
    outputs = session.get_outputs()
    if [value.name for value in outputs] != [
        "linear_out", "sigmoid_out", "mel_buffer_out", "gru_buffer_out",
    ] or any(value.type != "tensor(float)" for value in outputs):
        raise ValueError("invalid_pvad_model_contract")


def _validate_ecapa_session(session) -> None:
    inputs, outputs = session.get_inputs(), session.get_outputs()
    if (
        [(value.name, value.type) for value in inputs]
        != [("features", "tensor(float)"), ("feature_lens", "tensor(float)")]
        or len(inputs[0].shape) != 3 or inputs[0].shape[-1] != 80
        or len(inputs[1].shape) != 1
        or [(value.name, value.type) for value in outputs]
        != [("embedding", "tensor(float)")]
        or len(outputs[0].shape) != 2 or outputs[0].shape[-1] != 192
    ):
        raise ValueError("invalid_ecapa_model_contract")
