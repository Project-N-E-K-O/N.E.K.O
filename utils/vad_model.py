# -- coding: utf-8 --
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Local voice turn detection with Silero VAD and Smart Turn v3.

This module uses two CPU-only ONNX models through the project's existing
``onnxruntime`` dependency. It intentionally does not pull in PyTorch,
transformers, silero-vad, or pipecat because those dependencies would add a
large resident memory cost for a narrow turn-boundary feature.

The lifecycle mirrors ``memory/embeddings.py``: INIT -> LOADING -> READY or
DISABLED, with sticky disable after missing assets, missing runtime support, low
RAM, or inference errors. Callers can therefore fall back to provider-side VAD
without special error handling.

Unlike embeddings, this path does not gate on AVX-VNNI or AVX2 int8 SIMD.
Silero is fp32 and runs on ordinary CPUs; Smart Turn v3.2 is int8 but only runs
once at candidate endpoint boundaries, so slower CPU paths are acceptable.

Model contracts:

Silero v5/v6 (``silero_vad.onnx``):
  inputs: input[batch, 64+512=576] f32, state[2,1,128] f32, sr() int64
  outputs: output[batch,1] f32 speech probability, stateN[2,1,128] f32
  The 16 kHz input must include the previous 64-sample context plus the current
  512-sample window. Feeding only the 512-sample window silently collapses the
  probability near zero because the ONNX model accepts variable-length input.

Smart Turn v3.2-cpu (``smart_turn_v3.onnx``):
  input: input_features[batch, 80, 800] f32 Whisper-style log-mel features
  output: logits[batch,1]. Despite the output name, sigmoid is embedded in the
  graph, so the value is already P(complete) in [0, 1].
  Preprocessing is a numpy copy of
  ``WhisperFeatureExtractor(chunk_length=8, do_normalize=True)``. The mel filter
  bank is loaded from ``whisper_mel_80.npy`` and the periodic-Hann window is
  generated directly.
"""
from __future__ import annotations

import os
import sys
import enum
import threading
from typing import Optional

import numpy as np

try:
    from utils.logger_config import get_module_logger
    logger = get_module_logger(__name__, "Main")
except Exception:  # noqa: BLE001 — 极早期/裸测试环境拿不到 config，退回裸 logger
    import logging
    logger = logging.getLogger(__name__)


# ── 资产解析（镜像 embeddings._bundled_model_dirs，但指向 data/vad_models）──

DEFAULT_VAD_MODEL_DIR_NAME = "vad_models"
SILERO_MODEL_FILE = "silero_vad.onnx"
SMART_TURN_MODEL_FILE = "smart_turn_v3.onnx"
WHISPER_MEL_FILE = "whisper_mel_80.npy"


def _is_nonempty_file(path: str) -> bool:
    """Return whether a path exists and has non-zero size."""
    try:
        return os.path.isfile(path) and os.path.getsize(path) > 0
    except OSError:
        return False


def _candidate_model_dirs(app_docs_model_dir: Optional[str]) -> list[str]:
    """Return model asset candidate roots in priority order.

    The search checks user app-data overrides first, then PyInstaller unpacked
    data, Nuitka standalone data, and finally the source-tree data directory.
    The layout mirrors the embedding model resolver but targets ``vad_models``.
    """
    roots: list[str] = []
    seen: set[str] = set()

    def _add(path: str) -> None:
        norm = os.path.abspath(path)
        if norm not in seen:
            seen.add(norm)
            roots.append(norm)

    if app_docs_model_dir:
        _add(app_docs_model_dir)
    if hasattr(sys, "_MEIPASS"):
        _add(os.path.join(str(sys._MEIPASS), "data", DEFAULT_VAD_MODEL_DIR_NAME))
    if getattr(sys, "frozen", False) or "__compiled__" in globals():
        _add(os.path.join(os.path.dirname(os.path.abspath(sys.executable)),
                          "data", DEFAULT_VAD_MODEL_DIR_NAME))
    # 源码运行：utils/vad_model.py → 上一级是 NEKO/ → data/vad_models
    _add(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "data", DEFAULT_VAD_MODEL_DIR_NAME))
    return roots


def _resolve_model_dir(app_docs_model_dir: Optional[str], required_file: str) -> Optional[str]:
    """Return the first candidate directory containing ``required_file``."""
    for d in _candidate_model_dirs(app_docs_model_dir):
        if _is_nonempty_file(os.path.join(d, required_file)):
            return d
    return None


def detect_total_ram_gb() -> Optional[float]:
    """Return total system RAM in GiB, or ``None`` when detection fails."""
    try:
        import psutil
        return psutil.virtual_memory().total / (1024 ** 3)
    except Exception as e:  # noqa: BLE001
        logger.warning("VAD: psutil RAM detection failed: %s", e)
        return None


# ── 生命周期枚举 ──────────────────────────────────────────────────────


class VadState(enum.Enum):
    INIT = "init"
    LOADING = "loading"
    READY = "ready"
    DISABLED = "disabled"


class VadDisableReason(enum.Enum):
    NONE = "none"
    USER_DISABLED = "user_disabled_via_config"
    NO_ONNXRUNTIME = "onnxruntime_not_importable"
    NO_MODEL_FILE = "model_file_missing"
    LOW_RAM = "ram_below_threshold"
    LOAD_ERROR = "load_raised"
    INFERENCE_ERROR = "inference_raised"


class _OnnxVadBase:
    """Shared lifecycle for sticky-disable, single-flight ONNX model loading.

    ``load()`` uses ``threading.Lock`` so concurrent callers only build one
    session. ONNX Runtime releases the GIL for inference, so ready sessions can
    be used safely from executor threads.
    """

    model_filename: str = ""

    def __init__(self, *, app_docs_model_dir: Optional[str], enabled: bool,
                 min_ram_gb: float, ram_gb: Optional[float],
                 intra_op_threads: int) -> None:
        self._app_docs_model_dir = app_docs_model_dir
        self._enabled = enabled
        self._min_ram_gb = min_ram_gb
        self._ram_gb = ram_gb if ram_gb is not None else detect_total_ram_gb()
        self._intra_op_threads = max(1, int(intra_op_threads))
        self._state = VadState.INIT
        self._disable_reason = VadDisableReason.NONE
        self._session = None
        self._load_lock = threading.Lock()

        # 构造期就能定的禁用条件先判掉（模型文件存在性推迟到 load 时，给「先装好
        # 程序、后放模型文件」留余地）。
        if not self._enabled:
            self._mark_disabled(VadDisableReason.USER_DISABLED, log=False)
        elif self._ram_gb is None or self._ram_gb < self._min_ram_gb:
            self._mark_disabled(VadDisableReason.LOW_RAM, log=False)

    # public ----------------------------------------------------------------

    def is_available(self) -> bool:
        return self._state == VadState.READY

    def is_disabled(self) -> bool:
        return self._state == VadState.DISABLED

    def disable_reason(self) -> str:
        return self._disable_reason.value

    def load(self) -> bool:
        """Load the ONNX session once and return ``is_available()``.

        The method is idempotent and single-flight. Any failure moves the model
        into sticky DISABLED state for the lifetime of the process. Callers
        should run it in an executor or background thread.
        """
        if self._state in (VadState.READY, VadState.DISABLED):
            return self.is_available()
        with self._load_lock:
            if self._state in (VadState.READY, VadState.DISABLED):
                return self.is_available()
            self._state = VadState.LOADING
            try:
                model_dir = _resolve_model_dir(self._app_docs_model_dir, self.model_filename)
                if model_dir is None:
                    self._mark_disabled(VadDisableReason.NO_MODEL_FILE)
                    return False
                try:
                    import onnxruntime as ort  # noqa: F401
                except ImportError:
                    self._mark_disabled(VadDisableReason.NO_ONNXRUNTIME)
                    return False
                self._load_blocking(os.path.join(model_dir, self.model_filename), ort)
            except Exception as e:  # noqa: BLE001 — 任意加载失败 → 关闭
                logger.warning("VAD %s: load failed (%s: %s); disabled",
                               type(self).__name__, type(e).__name__, e)
                self._mark_disabled(VadDisableReason.LOAD_ERROR)
                return False
            self._state = VadState.READY
            logger.info("VAD %s: ready (dir=%s, ram=%.1fGB, threads=%d)",
                        type(self).__name__, model_dir, self._ram_gb or 0.0,
                        self._intra_op_threads)
            return True

    # internal --------------------------------------------------------------

    def _make_session(self, model_path: str, ort) -> object:
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = self._intra_op_threads
        opts.inter_op_num_threads = 1
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        # 关 arena：BFCArena 只涨不还会把瞬时峰值钉成永久 RSS。我们的输入定长且
        # 小（Silero 576 样本 / Smart Turn 80×800），每次 run 重新 malloc 的几 μs
        # 相对推理本身可忽略，换 RSS 能跌回基线。与 embeddings.py 同策略。
        opts.enable_cpu_mem_arena = False
        return ort.InferenceSession(model_path, sess_options=opts,
                                    providers=["CPUExecutionProvider"])

    def _load_blocking(self, model_path: str, ort) -> None:
        raise NotImplementedError

    def _mark_disabled(self, reason: VadDisableReason, *, log: bool = True) -> None:
        if self._state != VadState.DISABLED and log:
            logger.warning("VAD %s: disabled (%s)", type(self).__name__, reason.value)
        self._state = VadState.DISABLED
        self._disable_reason = reason
        self._session = None


# ── Silero VAD（帧级，流式）──────────────────────────────────────────


class SileroVad(_OnnxVadBase):
    """Streaming 16 kHz Silero v5/v6 frame-level VAD.

    Call ``reset()`` for a new stream and then feed chunks through
    ``process(samples)``. The class buffers samples until it has 512 samples
    (32 ms), then returns speech probabilities while preserving LSTM state and
    64-sample context across windows.
    """

    model_filename = SILERO_MODEL_FILE
    SR = 16000
    WINDOW = 512       # 16kHz 固定窗
    CONTEXT = 64       # 16kHz context 前缀

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._sr_arr = np.array(self.SR, dtype=np.int64)
        self._lstm = np.zeros((2, 1, 128), dtype=np.float32)  # LSTM 隐状态(h+c)
        self._context = np.zeros(self.CONTEXT, dtype=np.float32)
        self._buf = np.zeros(0, dtype=np.float32)

    def reset(self) -> None:
        """Reset LSTM state, context, and buffered samples for a new stream."""
        self._lstm = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros(self.CONTEXT, dtype=np.float32)
        self._buf = np.zeros(0, dtype=np.float32)

    def _load_blocking(self, model_path: str, ort) -> None:
        self._session = self._make_session(model_path, ort)

    def process(self, samples: np.ndarray) -> list[float]:
        """Feed 16 kHz float32 samples and return completed-window probabilities."""
        if not self.is_available() or samples.size == 0:
            return []
        if samples.dtype != np.float32:
            samples = samples.astype(np.float32)
        self._buf = np.concatenate([self._buf, samples])
        probs: list[float] = []
        try:
            while self._buf.size >= self.WINDOW:
                win = self._buf[:self.WINDOW]
                self._buf = self._buf[self.WINDOW:]
                inp = np.concatenate([self._context, win])[None, :].astype(np.float32)
                out, self._lstm = self._session.run(
                    None, {"input": inp, "state": self._lstm, "sr": self._sr_arr})
                self._context = win[-self.CONTEXT:].copy()
                probs.append(float(out[0, 0]))
        except Exception as e:  # noqa: BLE001 — sticky：单次推理炸就整体关闭
            logger.warning("SileroVad: inference failed (%s: %s); disabled",
                           type(e).__name__, e)
            self._mark_disabled(VadDisableReason.INFERENCE_ERROR)
            return probs
        return probs


# ── Smart Turn v3（语义断句，一次性）────────────────────────────────


class SmartTurnV3(_OnnxVadBase):
    """Smart Turn v3.2 semantic endpoint predictor.

    ``predict_endpoint(audio)`` accepts 16 kHz trailing utterance audio, keeps
    or pads to an 8-second window, and returns P(complete) in [0, 1]. It is
    non-streaming and should run in an executor.
    """

    model_filename = SMART_TURN_MODEL_FILE
    SR = 16000
    N_FFT = 400
    HOP = 160
    N_MELS = 80
    MAX_SAMPLES = 8 * 16000   # 128000
    N_FRAMES = 800

    def _load_blocking(self, model_path: str, ort) -> None:
        model_dir = os.path.dirname(model_path)
        mel_path = os.path.join(model_dir, WHISPER_MEL_FILE)
        if not _is_nonempty_file(mel_path):
            # mel 滤波器组是 Smart Turn 预处理的必需资产，缺了等同模型缺失。
            raise FileNotFoundError(f"missing {WHISPER_MEL_FILE} next to model")
        # (201, 80)；float64 做矩阵乘以匹配已验证的逐位一致路径。
        self._mel = np.load(mel_path).astype(np.float64)
        self._session = self._make_session(model_path, ort)
        self._iname = self._session.get_inputs()[0].name
        # periodic-hann（实测与 HF window_function("hann") 差 4e-16）+ 预算帧索引
        n = np.arange(self.N_FFT)
        self._window = (0.5 - 0.5 * np.cos(2 * np.pi * n / self.N_FFT))
        n_padded = self.MAX_SAMPLES + 2 * (self.N_FFT // 2)
        self._n_frames_full = 1 + (n_padded - self.N_FFT) // self.HOP   # 801
        self._frame_idx = (np.arange(self.N_FFT)[None, :]
                           + self.HOP * np.arange(self._n_frames_full)[:, None])

    def _log_mel(self, audio: np.ndarray) -> np.ndarray:
        """Return Whisper-style log-mel features as ``(80, 800)`` float32.

        The pipeline mirrors HF Whisper preprocessing: keep or left-pad to
        8 seconds, normalize the full array, center reflect-pad, frame with a
        Hann window, run rFFT, project through the mel bank, clip/log, drop the
        final frame, range-limit, and scale.
        """
        m = self.MAX_SAMPLES
        x = audio[-m:] if audio.size > m else np.pad(audio, (m - audio.size, 0))
        x = x.astype(np.float64)
        x = (x - x.mean()) / np.sqrt(x.var() + 1e-7)
        pad = self.N_FFT // 2
        xp = np.pad(x, (pad, pad), mode="reflect")
        frames = xp[self._frame_idx] * self._window[None, :]
        spec = np.fft.rfft(frames, n=self.N_FFT, axis=1)
        power = (spec.real ** 2 + spec.imag ** 2).T
        mel_spec = self._mel.T @ power
        log_spec = np.log10(np.clip(mel_spec, 1e-10, None))
        log_spec = log_spec[:, :-1]
        log_spec = np.maximum(log_spec, log_spec.max() - 8.0)
        log_spec = (log_spec + 4.0) / 4.0
        return log_spec.astype(np.float32)

    def predict_endpoint(self, audio: np.ndarray) -> Optional[float]:
        """Return P(complete) in [0, 1], or ``None`` when unavailable."""
        if not self.is_available() or audio.size == 0:
            return None
        try:
            if audio.dtype != np.float32 and audio.dtype != np.float64:
                audio = audio.astype(np.float32)
            feats = self._log_mel(audio)[None].astype(np.float32)  # (1,80,800)
            out = self._session.run(None, {self._iname: feats})
            # 输出图内已 sigmoid，直接是 P(complete)。
            return float(np.asarray(out[0]).reshape(-1)[0])
        except Exception as e:  # noqa: BLE001
            logger.warning("SmartTurnV3: inference failed (%s: %s); disabled",
                           type(e).__name__, e)
            self._mark_disabled(VadDisableReason.INFERENCE_ERROR)
            return None


# ── 轮次检测 FSM（Silero 门控 + Smart Turn 断句）────────────────────


class TurnSignal(enum.Enum):
    """Signals returned by ``LocalTurnDetector.feed``."""
    NONE = "none"               # 无事发生
    SPEECH_START = "speech_start"   # 刚检测到用户开始说话（可用于 barge-in）
    CANDIDATE_END = "candidate_end"  # 说过话后静音达阈值 → 取 take_endpoint_audio() 跑 Smart Turn
    FORCE_END = "force_end"     # Smart Turn 判过「未完」后静音仍持续过久 → 不再问 Smart Turn，直接收轮次


class LocalTurnDetector:
    """Combine Silero gating and Smart Turn endpointing into a turn FSM.

    ``feed`` is synchronous and cheap: it only runs Silero and updates state. It
    returns ``CANDIDATE_END`` after speech followed by enough silence, leaving
    the caller to run ``predict_endpoint`` in an executor and feed the result
    back through ``on_endpoint_result``.

    Each silence segment emits at most one candidate end. After Smart Turn says
    a turn is incomplete, the FSM waits for new speech before issuing another
    candidate, with ``hard_commit_silence_ms`` as a fallback to guarantee the
    turn eventually ends.
    """

    def __init__(self, silero: SileroVad, smart_turn: SmartTurnV3, *,
                 sample_rate: int = 16000,
                 onset_prob: float = 0.5,
                 offset_prob: float = 0.35,
                 speech_min_ms: int = 200,
                 silence_ms: int = 300,
                 smart_turn_threshold: float = 0.5,
                 hard_commit_silence_ms: int = 2500,
                 smart_turn_enabled: bool = True,
                 max_buffer_s: int = 8) -> None:
        self.silero = silero
        self.smart_turn = smart_turn
        self.sample_rate = sample_rate
        self.onset_prob = onset_prob
        self.offset_prob = offset_prob
        self.smart_turn_threshold = smart_turn_threshold
        # 可热切换：True=停顿时问 Smart Turn 做语义断句；False=纯 Silero VAD，
        # 调用方在 CANDIDATE_END 直接提交（停顿即收轮次）。运行中可由
        # config_router 直接改本属性实现热切，无需重连会话。
        self.smart_turn_enabled = smart_turn_enabled
        self._win_ms = 1000.0 * SileroVad.WINDOW / sample_rate   # 32ms / window
        self._speech_min_win = max(1, round(speech_min_ms / self._win_ms))
        self._silence_min_win = max(1, round(silence_ms / self._win_ms))
        self._hard_commit_win = max(self._silence_min_win,
                                    round(hard_commit_silence_ms / self._win_ms))
        self._max_samples = max_buffer_s * sample_rate
        self.reset()

    def reset(self) -> None:
        """Reset the Silero stream, FSM counters, and utterance buffer."""
        try:
            self.silero.reset()
        except Exception:  # noqa: BLE001 — reset 永不抛
            pass
        self._utterance = np.zeros(0, dtype=np.float32)
        self._speech_count = 0     # 累计语音窗数（达 speech_min 才认为「说过话」）
        self._silence_count = 0    # 连续静音窗数
        self._spoke = False        # 本轮是否已说过话
        self._emitted = False      # 本段静音是否已发过 CANDIDATE_END
        self._resolved_incomplete = False  # Smart Turn 判过「未完」
        # 单调递增：每个语音窗 +1。异步 Smart Turn 结论回来时若 seq 已变（用户又开口），
        # 调用方据此放弃 commit，避免在用户已说下一句时误收上一轮。reset 不清零
        # （跨轮单调），_commit_reset 也保留。
        self._activity_seq = getattr(self, "_activity_seq", 0)

    def _to_float(self, pcm16_bytes: bytes) -> np.ndarray:
        a = np.frombuffer(pcm16_bytes, dtype=np.int16)
        return a.astype(np.float32) / 32768.0

    def feed(self, pcm16_bytes: bytes) -> TurnSignal:
        """Feed one chunk of 16 kHz PCM16 audio into the cheap FSM path.

        When multiple windows are processed, the highest-priority recent signal
        wins: CANDIDATE_END, then SPEECH_START, then NONE. If Silero is not
        available, this degrades to a silent no-op.
        """
        if not pcm16_bytes:
            return TurnSignal.NONE
        samples = self._to_float(pcm16_bytes)
        # 累入话语缓冲（滚动保留最后 max_buffer_s）
        self._utterance = np.concatenate([self._utterance, samples])
        if self._utterance.size > self._max_samples:
            self._utterance = self._utterance[-self._max_samples:]

        probs = self.silero.process(samples)
        if not probs:
            return TurnSignal.NONE

        signal = TurnSignal.NONE
        for p in probs:
            if p >= self.onset_prob:
                # 语音帧
                self._speech_count += 1
                self._silence_count = 0
                self._activity_seq += 1
                if not self._spoke and self._speech_count >= self._speech_min_win:
                    self._spoke = True
                    if signal is TurnSignal.NONE:
                        signal = TurnSignal.SPEECH_START
                # 重新开口 → 清掉上一段静音的发射/未完标记，准许下一次断句
                self._emitted = False
                self._resolved_incomplete = False
            elif p < self.offset_prob:
                # 静音帧（offset~onset 之间是滞回区，既不算语音也不累静音）
                if self._spoke:
                    self._silence_count += 1
                    if (self._resolved_incomplete
                            and self._silence_count >= self._hard_commit_win):
                        # Smart Turn 判过「未完」但静音仍持续过久 → 不再问 Smart Turn，
                        # 直接强制收轮次（保证轮次终会结束，即便模型一直不自信）。
                        signal = TurnSignal.FORCE_END
                    elif not self._emitted and self._silence_count >= self._silence_min_win:
                        self._emitted = True
                        signal = TurnSignal.CANDIDATE_END
        return signal

    @property
    def activity_seq(self) -> int:
        """Monotonic speech-activity counter for stale async endpoint checks."""
        return self._activity_seq

    def take_endpoint_audio(self) -> np.ndarray:
        """Return a copy of the current utterance buffer for Smart Turn."""
        return self._utterance.copy()

    def on_endpoint_result(self, probability: Optional[float]) -> bool:
        """Apply a Smart Turn result and return whether the turn is complete.

        ``None`` degrades to Silero-only completion, values above the threshold
        complete the turn, and lower values mark the current silence as
        incomplete until speech resumes or the hard-silence fallback fires.
        """
        if probability is None:
            self._commit_reset()
            return True
        if probability >= self.smart_turn_threshold:
            self._commit_reset()
            return True
        self._resolved_incomplete = True
        return False

    def commit(self) -> None:
        """Force-complete the current turn and reset turn-local state."""
        self._commit_reset()

    def _commit_reset(self) -> None:
        """Clear turn-local buffers and counters while preserving Silero state."""
        self._utterance = np.zeros(0, dtype=np.float32)
        self._speech_count = 0
        self._silence_count = 0
        self._spoke = False
        self._emitted = False
        self._resolved_incomplete = False


# ── 工厂：从（已解析的）配置构造 detector ────────────────────────────


def build_local_turn_detector(
    *, app_docs_model_dir: Optional[str], enabled: bool, min_ram_gb: float,
    ram_gb: Optional[float] = None, silero_threads: int = 1,
    smart_turn_threads: int = 1, onset_prob: float = 0.5, offset_prob: float = 0.35,
    speech_min_ms: int = 200, silence_ms: int = 300, smart_turn_threshold: float = 0.5,
    hard_commit_silence_ms: int = 2500, smart_turn_enabled: bool = True,
) -> LocalTurnDetector:
    """Construct, but do not load, a ``LocalTurnDetector``."""
    silero = SileroVad(app_docs_model_dir=app_docs_model_dir, enabled=enabled,
                       min_ram_gb=min_ram_gb, ram_gb=ram_gb,
                       intra_op_threads=silero_threads)
    smart_turn = SmartTurnV3(app_docs_model_dir=app_docs_model_dir, enabled=enabled,
                             min_ram_gb=min_ram_gb, ram_gb=ram_gb,
                             intra_op_threads=smart_turn_threads)
    return LocalTurnDetector(
        silero, smart_turn, onset_prob=onset_prob, offset_prob=offset_prob,
        speech_min_ms=speech_min_ms, silence_ms=silence_ms,
        smart_turn_threshold=smart_turn_threshold,
        hard_commit_silence_ms=hard_commit_silence_ms,
        smart_turn_enabled=smart_turn_enabled)
