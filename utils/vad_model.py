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

"""本地语音轮次检测：Silero VAD（帧级）+ Smart Turn v3（语义断句）。

两个 CPU-only ONNX 模型，复用项目已有的 ``onnxruntime``——**不引入 PyTorch /
transformers / silero-vad / pipecat**（它们都会拖进 torch，几百 MB，与本特性的
"几十 MB 常驻"预算冲突）。

生命周期照搬 ``memory/embeddings.py`` 的 ``EmbeddingService``：INIT → LOADING →
READY / DISABLED，sticky disable（一旦关闭进程内不再重试），缺 onnxruntime / 缺
模型文件 / RAM 不足 / 任意推理异常 都优雅降级为 no-op，让调用方无缝退回服务端
VAD 行为。

与 embeddings 不同，**这里不做 AVX-VNNI / AVX2 int8 SIMD 门控**：Silero 是 fp32，
任何 CPU 都能跑；Smart Turn v3.2 是 int8，但只在「停顿处」触发一次（弱 CPU 上
~60-300ms），即便落到 SSE 慢路径也能用，没有像 embeddings 那样"无快路径就关闭"
的必要。因此门控只剩：master 开关 / onnxruntime 在不在 / 模型文件在不在 / RAM。

── 模型契约（均已对真实音频实测验证，见 git 历史里的验证脚本）──

Silero v5/v6（``silero_vad.onnx``）：
  inputs:  input[batch, 64+512=576] f32, state[2,1,128] f32, sr() int64
  outputs: output[batch,1] f32(语音概率), stateN[2,1,128] f32
  **关键坑**：16kHz 下每步输入不是裸 512 样本，而是「上一帧尾部 64 样本 context
  + 当前 512 样本」= 576 样本（官方 OnnxWrapper 的 _context 机制）。喂裸 512 会
  让概率恒为 ~0（输入是变长所以不会报错，只是静默失灵）。

Smart Turn v3.2-cpu（``smart_turn_v3.onnx``）：
  input:  input_features[batch, 80, 800] f32（Whisper 风格 log-mel，80 mel × 800 帧 = 8s@hop160）
  output: logits[batch,1] —— **注意命名虽叫 logits，但 sigmoid 已内置在图里**，
          输出已经是 [0,1] 的 P(complete)，直接取用，不要再 sigmoid。
  预处理 = HF ``WhisperFeatureExtractor(chunk_length=8, do_normalize=True)`` 的纯
  numpy 复刻（已对 HF 验证逐元素 < 6e-6，端到端 ONNX 输出逐位一致）。mel 滤波器组
  从 ``whisper_mel_80.npy``（201×80，从 HF 提取）加载；periodic-hann 窗按公式硬编。
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
    """文件存在且 >0 字节。中断下载留下的 0 字节残file 能过 isfile 但会让
    onnxruntime/np.load 在更深处炸，这里当成「缺失」以走最干净的降级理由。"""
    try:
        return os.path.isfile(path) and os.path.getsize(path) > 0
    except OSError:
        return False


def _candidate_model_dirs(app_docs_model_dir: Optional[str]) -> list[str]:
    """VAD 模型资产候选根目录，优先级从高到低：

      1. 用户 app-data 覆盖目录（``<app_docs>/vad_models``），允许用户替换模型；
      2. PyInstaller 解包目录（``sys._MEIPASS/data/vad_models``）；
      3. Nuitka standalone（``<exe_dir>/data/vad_models``）；
      4. 源码运行（``<repo>/data/vad_models``，相对本文件上一级）。

    与 ``memory/embeddings.py`` 的 ``_bundled_model_dirs`` 同构，只是子目录换成
    vad_models —— 打包侧用同一套 --include-data-files 规则带上即可。
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
    """返回第一个含有非空 ``required_file`` 的候选目录，找不到返回 None。"""
    for d in _candidate_model_dirs(app_docs_model_dir):
        if _is_nonempty_file(os.path.join(d, required_file)):
            return d
    return None


def detect_total_ram_gb() -> Optional[float]:
    """系统总内存（GiB），探测失败返回 None（上游当作「未知」→ 保守禁用）。"""
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
    """共享生命周期骨架：sticky disable + 单飞（single-flight）blocking 加载。

    线程安全：``load()`` 用 ``threading.Lock`` 保证并发调用只真正加载一次（Silero
    内联在事件循环、Smart Turn 跑在 executor 线程，两条路都可能触发首次加载）。
    onnxruntime session 自身 GIL-release，推理可在 executor 安全调用。
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
        """阻塞加载 ONNX session（首次），返回加载后的 ``is_available()``。

        幂等 + 单飞：多线程并发调用只解压/建 session 一次。任意失败 → sticky
        DISABLED，进程内不再重试。**应在 executor / 后台线程调用**，别在事件循环里
        直接 load（Smart Turn 解包有几十毫秒）。
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
    """Silero v5/v6 帧级 VAD，16kHz 流式。

    用法：``reset()`` 起一段流，然后逐块 ``process(samples)``。内部维护样本缓冲，
    每凑满 512 样本（32ms）跑一次模型，返回该窗的语音概率（0~1）。LSTM ``state``
    与 64 样本 ``context`` 跨窗连续维持；``reset()`` 清零（每轮对话开始 / 强制重置
    时调用，对偶 ``AudioProcessor.reset()``）。
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
        """清 LSTM 隐状态 + context + 样本缓冲，开始一段全新的音频流。"""
        self._lstm = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros(self.CONTEXT, dtype=np.float32)
        self._buf = np.zeros(0, dtype=np.float32)

    def _load_blocking(self, model_path: str, ort) -> None:
        self._session = self._make_session(model_path, ort)

    def process(self, samples: np.ndarray) -> list[float]:
        """喂入任意长度的 16kHz float32 样本（[-1,1]），返回本次凑满的每个 512 窗的
        语音概率列表（可能为空，若不足一窗）。不可用时返回 []。"""
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
    """Smart Turn v3.2 语义断句。``predict_endpoint(audio)`` 输入一段 16kHz 尾部
    话语（任意长，内部取最后 8s 或前向补零到 8s），返回 P(complete) ∈ [0,1]。

    一次性、非流式，**应在 executor 调用**（~30ms@快 CPU，弱机可达 ~100ms+）。
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
        """纯 numpy 复刻 WhisperFeatureExtractor(do_normalize=True) → (80, 800) f32。

        步骤（每步都对齐 HF，整体对真实音频实测端到端 ONNX 输出逐位一致）：
        truncate/前向补零到 8s → 全数组 zero-mean-unit-var → center reflect-pad 200
        → 400/160 加 hann 窗分帧 → rfft → power → mel.T@power → log10(clip 1e-10)
        → 丢最后一帧(801→800) → max(x, max-8) → (x+4)/4。
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
        """返回 P(complete) ∈ [0,1]；不可用或推理失败返回 None。"""
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
    """``LocalTurnDetector.feed`` 的返回信号。"""
    NONE = "none"               # 无事发生
    SPEECH_START = "speech_start"   # 刚检测到用户开始说话（可用于 barge-in）
    CANDIDATE_END = "candidate_end"  # 说过话后静音达阈值 → 取 take_endpoint_audio() 跑 Smart Turn
    FORCE_END = "force_end"     # Smart Turn 判过「未完」后静音仍持续过久 → 不再问 Smart Turn，直接收轮次


class LocalTurnDetector:
    """把 Silero（廉价常驻门控）和 Smart Turn（语义断句）组合成轮次检测器。

    设计成**纯同步、可单测**的 FSM：``feed`` 只跑 Silero（亚毫秒，可在事件循环内联），
    在「说过话 + 静音 ≥ silence_ms」时返回 ``CANDIDATE_END`` 并备好一段尾部音频快照；
    调用方负责把这段快照丢进 executor 跑 ``predict_endpoint``（~30ms 重活），再用
    ``on_endpoint_result`` 回灌结论。

    典型调用方（OmniRealtimeClient.stream_audio，MANUAL 模式）：

        sig = det.feed(pcm16_16k_bytes)              # 内联，廉价
        if sig is TurnSignal.CANDIDATE_END:
            audio = det.take_endpoint_audio()
            p = await loop.run_in_executor(None, det.smart_turn.predict_endpoint, audio)
            complete = det.on_endpoint_result(p)     # None→保持沉默；记录结论
            if complete:
                await self.signal_user_activity_end()

    FSM 不变量：每段静音只发一次 ``CANDIDATE_END``；Smart Turn 判「未完」后，必须等
    用户**重新说话再停**才会再发下一次（避免长尾静音里反复触发 Smart Turn）。另设
    ``hard_commit_silence_ms`` 兜底：判「未完」后若静音持续过久，强制收一次轮次，
    保证轮次终会结束（否则用户真的说完只剩沉默时会卡死）。
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
        """整体复位：清 Silero 流 + FSM + 话语缓冲。每轮对话开始 / 强制重置时调用。"""
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
        """喂入一块 16kHz PCM16 音频。廉价：只跑 Silero + 更新 FSM。

        返回最近一次状态信号（多窗合一时取最「重要」的：CANDIDATE_END > SPEECH_START
        > NONE）。Silero 不可用时退化为静默 no-op（返回 NONE）。
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
        """单调递增的语音活动计数；调用方用它判断异步断句期间用户是否又开口。"""
        return self._activity_seq

    def take_endpoint_audio(self) -> np.ndarray:
        """返回当前话语缓冲（最后 ≤8s）的拷贝，供 executor 里跑 Smart Turn。"""
        return self._utterance.copy()

    def on_endpoint_result(self, probability: Optional[float]) -> bool:
        """回灌 Smart Turn 结论。返回是否判定为「轮次完成」（调用方据此 commit）。

        - prob 为 None（Smart Turn 不可用/失败）→ 退化为「Silero 静音即完成」，返回 True。
        - prob ≥ 阈值 → 完成，复位本轮等待下一轮。
        - prob < 阈值 → 未完，记 ``_resolved_incomplete``，等用户继续说或静音兜底。
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
        """外部强制收尾本轮（FORCE_END 兜底路径用）。等价于一次「轮次完成」复位。"""
        self._commit_reset()

    def _commit_reset(self) -> None:
        """一轮收尾：清话语缓冲 + FSM 计数，但**不**清 Silero LSTM 流（同一段连续
        音频流，硬重置 Silero 反而会丢上下文）。下一轮 SPEECH_START 自然重新计数。"""
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
    """构造（但**不加载**）一个 LocalTurnDetector。调用方拿到后应在 executor 里
    调用 ``detector.silero.load()`` / ``detector.smart_turn.load()`` 预热；任一模型
    DISABLED 时整套退化为 no-op（feed 返回 NONE、predict 返回 None）。"""
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
