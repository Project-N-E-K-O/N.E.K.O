# -- coding: utf-8 --
"""
Audio Processor Module with RNNoise, AGC and Limiter
使用 RNNoise 进行深度学习降噪的音频预处理模块，并内置AGC和Limiter

RNNoise 是 Mozilla 开发的实时降噪算法，使用 GRU 神经网络，
延迟仅 13.3ms，适合实时语音处理。

处理链：RNNoise -> AGC -> Limiter -> 降采样

AGC（Automatic Gain Control）：自动增益控制，使音量稳定
Limiter：限幅器，防止音频削波

重要：RNNoise 的 GRU 状态会随着处理背景噪音而漂移，
需要在检测到语音结束后重置状态。
"""

import numpy as np
from typing import Optional
from utils.logger_config import get_module_logger
import soxr
import time
import os
import wave

logger = get_module_logger(__name__)

# ============== DEBUG 音频存储功能 ==============
# 设置为 True 可以将 RNNoise 处理前后的音频存储到文件中
# 用于对比降噪效果
DEBUG_SAVE_AUDIO = False
DEBUG_AUDIO_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "debug_audio")
# ===============================================

# Direct ctypes loading of rnnoise native library.
# We bypass ALL pyrnnoise Python code because pyrnnoise/__init__.py
# unconditionally imports pyrnnoise.pyrnnoise which imports audiolab,
# and audiolab is excluded from Nuitka via --nofollow-import-to.
# Python's import system executes __init__.py even for submodule imports,
# so there is no way to import pyrnnoise.rnnoise without triggering audiolab.
import sys
import ctypes
import platform
import importlib.util

_rnnoise_lib = None
_rnnoise_available = None


def _find_rnnoise_dll() -> str:
    """Locate rnnoise native library inside pyrnnoise package-data."""
    names = {
        "Windows": "rnnoise.dll",
        "Darwin": "librnnoise.dylib",
        "Linux": "librnnoise.so",
    }
    lib_name = names.get(platform.system())
    if lib_name is None:
        raise OSError(f"Unsupported platform: {platform.system()}")

    # Method 1: find_spec — works in normal Python / uv environments.
    # find_spec does NOT execute __init__.py, only queries metadata.
    spec = importlib.util.find_spec("pyrnnoise")
    if spec is not None and spec.submodule_search_locations:
        for search_dir in spec.submodule_search_locations:
            candidate = os.path.join(search_dir, lib_name)
            if os.path.isfile(candidate):
                return candidate

    # Method 2: Nuitka standalone — --include-package-data=pyrnnoise places
    # the DLL at <exe_dir>/pyrnnoise/<lib_name>.
    exe_dir = os.path.dirname(sys.executable)
    candidate = os.path.join(exe_dir, "pyrnnoise", lib_name)
    if os.path.isfile(candidate):
        return candidate

    raise OSError(f"{lib_name} not found via find_spec or Nuitka dist")


def _load_rnnoise_native():
    """Load rnnoise native library and return a lightweight API object."""
    lib_path = _find_rnnoise_dll()
    lib = ctypes.CDLL(lib_path)

    lib.rnnoise_create.argtypes = [ctypes.c_void_p]
    lib.rnnoise_create.restype = ctypes.c_void_p
    lib.rnnoise_destroy.argtypes = [ctypes.c_void_p]
    lib.rnnoise_get_frame_size.restype = ctypes.c_int
    lib.rnnoise_process_frame.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_float),
        ctypes.POINTER(ctypes.c_float),
    ]
    lib.rnnoise_process_frame.restype = ctypes.c_float

    frame_size = lib.rnnoise_get_frame_size()

    class _Lib:
        FRAME_SIZE = frame_size
        SAMPLE_RATE = 48000

        @staticmethod
        def create():
            return lib.rnnoise_create(None)

        @staticmethod
        def destroy(state):
            lib.rnnoise_destroy(state)

        @staticmethod
        def process_mono_frame(state, frame):
            if frame.dtype == np.int16:
                frame = frame.astype(np.float32)
            else:
                frame = (frame * 32767).astype(np.float32)
            n = len(frame)
            if n < frame_size:
                frame = np.pad(frame, (0, frame_size - n))
            ptr = frame.ctypes.data_as(ctypes.POINTER(ctypes.c_float))
            prob = lib.rnnoise_process_frame(state, ptr, ptr)
            return frame.astype(np.int16)[:n], float(prob)

    return _Lib()


def _get_rnnoise():
    """Lazy load rnnoise native library."""
    global _rnnoise_lib, _rnnoise_available
    if _rnnoise_available is None:
        try:
            _rnnoise_lib = _load_rnnoise_native()
            _rnnoise_available = True
            logger.info(f"✅ rnnoise loaded (frame_size={_rnnoise_lib.FRAME_SIZE})")
        except Exception as e:
            logger.warning(f"⚠️ rnnoise not available: {e}")
            _rnnoise_available = False
    return _rnnoise_lib if _rnnoise_available else None


class _LiteDenoiser:
    """Lightweight RNNoise wrapper using direct ctypes calls.
    
    No pyrnnoise / audiolab / av Python imports — only the native DLL.
    """

    def __init__(self, rnnoise_lib):
        self._lib = rnnoise_lib
        self._state = rnnoise_lib.create()
        if not self._state:
            raise RuntimeError("rnnoise_create() returned NULL — native library failed to initialise")

    def process_frame(self, frame_int16: np.ndarray):
        """Process one mono 480-sample int16 frame.
        
        Returns (denoised_int16, speech_prob_float).
        """
        return self._lib.process_mono_frame(self._state, frame_int16)

    def reset(self):
        if self._state:
            self._lib.destroy(self._state)
            self._state = None
        new_state = self._lib.create()
        if not new_state:
            raise RuntimeError("rnnoise_create() returned NULL during reset")
        self._state = new_state

    def __del__(self):
        lib = getattr(self, "_lib", None)
        state = getattr(self, "_state", None)
        if lib is not None and state:
            try:
                lib.destroy(state)
            except Exception:
                pass
            self._state = None


class AudioProcessor:
    """
    Real-time audio processor using RNNoise for noise reduction,
    with built-in AGC (Automatic Gain Control) and Limiter.
    
    Processing chain: RNNoise -> AGC -> Limiter -> Resample
    
    RNNoise requires 48kHz audio with 480-sample frames (10ms).
    After processing, audio is downsampled to 16kHz for API compatibility.
    
    IMPORTANT: Call reset() after each speech turn to clear RNNoise's
    internal GRU state and prevent state drift during silence/background.
    
    Thread Safety:
        This class is NOT safe for concurrent use. The following mutable
        state is unprotected: _frame_buffer, _last_speech_prob,
        _last_speech_time, _needs_reset, _denoiser.
        
        Callers must NOT invoke process_chunk() or reset() from multiple
        threads or coroutines simultaneously. If concurrent access is
        required, wrap calls with an external lock (e.g., threading.Lock
        for threads or asyncio.Lock for async coroutines).
    """
    
    RNNOISE_SAMPLE_RATE = 48000  # RNNoise requires 48kHz
    RNNOISE_FRAME_SIZE = 480     # 10ms at 48kHz
    API_SAMPLE_RATE = 16000      # API expects 16kHz
    
    # Reset denoiser if no speech detected for this many seconds
    RESET_TIMEOUT_SECONDS = 4.0
    
    # AGC Configuration
    AGC_TARGET_LEVEL = 0.25        # Target RMS level (0.0-1.0), raised for easier VAD trigger
    AGC_MAX_GAIN = 20.0            # Maximum gain multiplier (safe with noise floor protection)
    AGC_MIN_GAIN = 0.25            # Minimum gain multiplier
    AGC_NOISE_FLOOR = 0.015        # RMS below this = silence/noise, don't increase gain
    AGC_ATTACK_TIME = 0.01         # Attack time in seconds (fast response to peaks)
    AGC_RELEASE_TIME = 0.4         # Release time in seconds (slow return to normal)
    
    # Limiter Configuration
    LIMITER_THRESHOLD = 0.95       # Threshold before limiting (0.0-1.0)
    LIMITER_KNEE = 0.05            # Soft knee width
    
    def __init__(
        self,
        input_sample_rate: int = 48000,
        output_sample_rate: int = 16000,
        noise_reduce_enabled: bool = True,
        agc_enabled: bool = True,
        limiter_enabled: bool = True,
        on_silence_reset: Optional[callable] = None
    ):
        self.input_sample_rate = input_sample_rate
        self.output_sample_rate = output_sample_rate
        self.noise_reduce_enabled = noise_reduce_enabled
        self.agc_enabled = agc_enabled
        self.limiter_enabled = limiter_enabled
        # 静音重置回调：当检测到4秒静音并重置状态时调用
        self.on_silence_reset = on_silence_reset
        
        # Initialize RNNoise denoiser
        self._denoiser = None
        self._init_denoiser()
        
        # Buffer for incomplete frames (int16 for pyrnnoise)
        self._frame_buffer = np.array([], dtype=np.int16)
        
        # Track voice activity for auto-reset
        self._last_speech_prob = 0.0
        self._last_speech_time = time.time()
        self._needs_reset = False
        
        # AGC state
        self._agc_gain = 1.0
        self._agc_attack_coeff = np.exp(-1.0 / (self.AGC_ATTACK_TIME * self.RNNOISE_SAMPLE_RATE))
        self._agc_release_coeff = np.exp(-1.0 / (self.AGC_RELEASE_TIME * self.RNNOISE_SAMPLE_RATE))
        
        # Debug audio buffers - 累积存储完整音频
        self._debug_audio_before: list[np.ndarray] = []
        self._debug_audio_after: list[np.ndarray] = []
        if DEBUG_SAVE_AUDIO:
            os.makedirs(DEBUG_AUDIO_DIR, exist_ok=True)
            logger.info(f"🔧 DEBUG: 音频录制已启用，文件将保存到 {DEBUG_AUDIO_DIR}")
        
        logger.info(f"🎤 AudioProcessor initialized: input={input_sample_rate}Hz, "
                   f"output={output_sample_rate}Hz, rnnoise={self._denoiser is not None}, "
                   f"agc={agc_enabled}, limiter={limiter_enabled}")
    
    def _init_denoiser(self) -> None:
        """Initialize RNNoise denoiser if available."""
        if not self.noise_reduce_enabled:
            return
        
        if self.input_sample_rate != self.RNNOISE_SAMPLE_RATE:
            logger.warning(
                f"⚠️ Skipping RNNoise initialization: input sample rate "
                f"{self.input_sample_rate}Hz != required {self.RNNOISE_SAMPLE_RATE}Hz"
            )
            return
            
        rnnoise_mod = _get_rnnoise()
        if rnnoise_mod:
            try:
                self._denoiser = _LiteDenoiser(rnnoise_mod)
                logger.info("🔊 RNNoise denoiser initialized")
            except Exception:  # noqa: BLE001 - RNNoise can fail for various reasons (missing libs, bad state); must catch all to ensure graceful fallback
                logger.exception("❌ Failed to initialize RNNoise")
                self._denoiser = None
    
    def process_chunk(self, audio_bytes: bytes) -> bytes:
        """
        Process a chunk of PCM16 audio data.
        
        Args:
            audio_bytes: Raw PCM16 audio bytes at input_sample_rate (48kHz)
            
        Returns:
            Processed audio as PCM16 bytes at output_sample_rate (16kHz)
        """
        # Keep as int16 - pyrnnoise expects int16!
        audio_int16 = np.frombuffer(audio_bytes, dtype=np.int16)
        
        # Check if we need to reset (after long silence or on request)
        current_time = time.time()
        silence_triggered = (current_time - self._last_speech_time > self.RESET_TIMEOUT_SECONDS)
        if self._needs_reset or silence_triggered:
            if self._denoiser is not None:
                self._reset_internal_state()
                self._last_speech_time = current_time  # Prevent infinite reset loop
                logger.debug("🔄 RNNoise state auto-reset after silence")
                # 调用静音重置回调（仅在静音触发时，非手动请求时）
                if silence_triggered and self.on_silence_reset:
                    try:
                        self.on_silence_reset()
                    except Exception as e:
                        logger.error(f"❌ on_silence_reset callback error: {e}")
            self._needs_reset = False
        
        # Apply RNNoise if available (processes int16, returns int16)
        if self._denoiser is not None and self.noise_reduce_enabled:
            # DEBUG: 记录 RNNoise 处理前的音频
            if DEBUG_SAVE_AUDIO:
                self._debug_audio_before.append(audio_int16.copy())
            
            processed = self._process_with_rnnoise(audio_int16)
            if len(processed) == 0:
                return b''  # Buffering
            
            # DEBUG: 记录 RNNoise 处理后的音频
            if DEBUG_SAVE_AUDIO:
                self._debug_audio_after.append(processed.copy())
            
            audio_int16 = processed
        
        # Apply AGC (Automatic Gain Control) after RNNoise
        if self.agc_enabled and len(audio_int16) > 0:
            audio_int16 = self._apply_agc(audio_int16)
        
        # Apply Limiter to prevent clipping
        if self.limiter_enabled and len(audio_int16) > 0:
            audio_int16 = self._apply_limiter(audio_int16)
        
        # Downsample from 48kHz to 16kHz using high-quality soxr
        if self.input_sample_rate != self.output_sample_rate and len(audio_int16) > 0:
            # Convert to float for soxr, resample, then back to int16
            audio_float = audio_int16.astype(np.float32) / 32768.0
            audio_float = soxr.resample(
                audio_float, 
                self.input_sample_rate, 
                self.output_sample_rate, 
                quality='HQ'
            )
            audio_int16 = (audio_float * 32768.0).clip(-32768, 32767).astype(np.int16)
        return audio_int16.tobytes()
    
    def _process_with_rnnoise(self, audio: np.ndarray) -> np.ndarray:
        """Process audio through RNNoise frame by frame.
        
        Args:
            audio: int16 numpy array
            
        Returns:
            Denoised int16 numpy array
        """
        # Add to frame buffer (int16)
        self._frame_buffer = np.concatenate([self._frame_buffer, audio])
        
        # Limit buffer size to prevent memory issues (max 1 seconds of audio)
        max_buffer_samples = 1 * self.RNNOISE_SAMPLE_RATE
        if len(self._frame_buffer) > max_buffer_samples:
            self._frame_buffer = self._frame_buffer[-max_buffer_samples:]
        
        output_frames = []
        while len(self._frame_buffer) >= self.RNNOISE_FRAME_SIZE:
            frame = self._frame_buffer[:self.RNNOISE_FRAME_SIZE]
            self._frame_buffer = self._frame_buffer[self.RNNOISE_FRAME_SIZE:]
            
            try:
                denoised, prob = self._denoiser.process_frame(frame)
                self._last_speech_prob = prob
                if prob > 0.2:
                    self._last_speech_time = time.time()
                output_frames.append(denoised)
            except Exception as e:
                logger.error(f"❌ RNNoise processing error: {e}")
                output_frames.append(frame)
        
        if output_frames:
            return np.concatenate(output_frames)
        return np.array([], dtype=np.int16)
    
    def _reset_internal_state(self) -> None:
        """Reset RNNoise internal state without full reinitialization."""
        self._frame_buffer = np.array([], dtype=np.int16)
        self._last_speech_prob = 0.0
        # Reset AGC gain state
        self._agc_gain = 1.0
        # Reset denoiser GRU hidden states (do not reinitialize)
        if self._denoiser is not None:
            try:
                self._denoiser.reset()
            except Exception as e:
                logger.warning(f"⚠️ Failed to reset RNNoise denoiser: {e}")
    
    def reset(self) -> None:
        """
        Reset the processor state. Call this after each speech turn ends
        to prevent RNNoise state drift during silence/background noise.
        """
        self._reset_internal_state()
        self._last_speech_time = time.time()
        logger.info("🔄 AudioProcessor state reset (external call)")
    
    def request_reset(self) -> None:
        """Request a reset on the next process_chunk call."""
        self._needs_reset = True
    
    def save_debug_audio(self) -> None:
        """
        将累积的 debug 音频保存到 WAV 文件。
        保存两个文件：
        - debug_audio_before.wav: RNNoise 处理前的原始音频
        - debug_audio_after.wav: RNNoise 处理后的降噪音频
        
        调用此方法后会清空 debug 缓冲区。
        """
        if not DEBUG_SAVE_AUDIO:
            return
        
        if not self._debug_audio_before and not self._debug_audio_after:
            logger.warning("⚠️ 没有可保存的 debug 音频数据")
            return
        
        # 合并所有音频片段
        if self._debug_audio_before:
            audio_before = np.concatenate(self._debug_audio_before)
            before_path = os.path.join(DEBUG_AUDIO_DIR, "debug_audio_before.wav")
            self._save_wav(before_path, audio_before, self.RNNOISE_SAMPLE_RATE)
            logger.info(f"💾 已保存处理前音频: {before_path} ({len(audio_before)/self.RNNOISE_SAMPLE_RATE:.2f}秒)")
        
        if self._debug_audio_after:
            audio_after = np.concatenate(self._debug_audio_after)
            after_path = os.path.join(DEBUG_AUDIO_DIR, "debug_audio_after.wav")
            self._save_wav(after_path, audio_after, self.RNNOISE_SAMPLE_RATE)
            logger.info(f"💾 已保存处理后音频: {after_path} ({len(audio_after)/self.RNNOISE_SAMPLE_RATE:.2f}秒)")
        
        # 清空缓冲区
        self._debug_audio_before.clear()
        self._debug_audio_after.clear()
        logger.info("🔧 DEBUG: 音频已保存，缓冲区已清空")
    
    def _save_wav(self, filepath: str, audio: np.ndarray, sample_rate: int) -> None:
        """将 int16 音频数据保存为 WAV 文件。"""
        with wave.open(filepath, 'wb') as wf:
            wf.setnchannels(1)  # mono
            wf.setsampwidth(2)  # 16-bit = 2 bytes
            wf.setframerate(sample_rate)
            wf.writeframes(audio.tobytes())
    
    @property
    def speech_probability(self) -> float:
        """Get the last detected speech probability (0.0-1.0)."""
        return self._last_speech_prob
    
    def set_enabled(self, enabled: bool) -> None:
        """Enable or disable noise reduction."""
        prev = self.noise_reduce_enabled
        self.noise_reduce_enabled = enabled
        if enabled and self._denoiser is None:
            self._init_denoiser()
        if prev != enabled:
            self._frame_buffer = np.array([], dtype=np.int16)
            self._agc_gain = 1.0
        logger.info(f"🎤 Noise reduction {'enabled' if enabled else 'disabled'}")
    
    def set_agc_enabled(self, enabled: bool) -> None:
        """Enable or disable AGC."""
        self.agc_enabled = enabled
        if enabled:
            self._agc_gain = 1.0  # Reset gain when re-enabling
        logger.info(f"🎤 AGC {'enabled' if enabled else 'disabled'}")
    
    def set_limiter_enabled(self, enabled: bool) -> None:
        """Enable or disable Limiter."""
        self.limiter_enabled = enabled
        logger.info(f"🎤 Limiter {'enabled' if enabled else 'disabled'}")
    
    def _apply_agc(self, audio: np.ndarray) -> np.ndarray:
        """
        Apply Automatic Gain Control to normalize audio levels.
        
        Uses a simple peak-following AGC with attack/release dynamics.
        
        Args:
            audio: int16 numpy array
            
        Returns:
            Gain-adjusted int16 numpy array
        """
        # Convert to float for processing
        audio_float = audio.astype(np.float32) / 32768.0
        
        # Calculate RMS of the current chunk
        rms = np.sqrt(np.mean(audio_float ** 2) + 1e-10)
        
        # Calculate desired gain with noise floor protection
        if rms > self.AGC_NOISE_FLOOR:
            # Real signal detected - calculate normal gain
            desired_gain = self.AGC_TARGET_LEVEL / rms
            desired_gain = np.clip(desired_gain, self.AGC_MIN_GAIN, self.AGC_MAX_GAIN)
        else:
            # Below noise floor: don't increase gain to avoid amplifying background noise
            # Only allow gain to stay same or decrease, cap at 1.0
            desired_gain = min(self._agc_gain, 1.0)
        
        # Smooth gain changes using attack/release coefficients
        if desired_gain < self._agc_gain:
            # Attack: fast response to loud signals
            self._agc_gain = (self._agc_attack_coeff * self._agc_gain + 
                             (1 - self._agc_attack_coeff) * desired_gain)
        else:
            # Release: slow return to higher gain
            self._agc_gain = (self._agc_release_coeff * self._agc_gain + 
                             (1 - self._agc_release_coeff) * desired_gain)
        
        # Apply gain
        audio_float = audio_float * self._agc_gain
        
        # Convert back to int16 (clipping will be handled by limiter)
        return (audio_float * 32768.0).clip(-32768, 32767).astype(np.int16)
    
    def _apply_limiter(self, audio: np.ndarray) -> np.ndarray:
        """
        Apply a soft limiter to prevent clipping.
        
        Uses a soft-knee limiter to gently compress peaks above threshold.
        
        Args:
            audio: int16 numpy array
            
        Returns:
            Limited int16 numpy array
        """
        # Convert to float (-1.0 to 1.0 range)
        audio_float = audio.astype(np.float32) / 32768.0
        
        # Apply soft-knee limiting
        threshold = self.LIMITER_THRESHOLD
        knee = self.LIMITER_KNEE
        
        # Calculate threshold boundaries
        knee_start = threshold - knee / 2
        knee_end = threshold + knee / 2
        
        # Get absolute values for comparison
        abs_audio = np.abs(audio_float)
        
        # Apply soft knee compression
        # Below knee_start: pass through
        # In knee region: gentle compression
        # Above knee_end: hard limiting
        
        output = np.copy(audio_float)
        
        # Knee region (soft transition)
        in_knee = (abs_audio > knee_start) & (abs_audio <= knee_end)
        if np.any(in_knee):
            # Quadratic compression in knee region
            knee_ratio = (abs_audio[in_knee] - knee_start) / knee
            compression = 1 - 0.5 * knee_ratio ** 2
            output[in_knee] = np.sign(audio_float[in_knee]) * (
                knee_start + (abs_audio[in_knee] - knee_start) * compression
            )
        
        # Above knee (hard limiting with soft saturation)
        above_knee = abs_audio > knee_end
        if np.any(above_knee):
            # Soft saturation using tanh
            excess = abs_audio[above_knee] - threshold
            limited = threshold + 0.5 * np.tanh(excess * 2) * (1 - threshold)
            output[above_knee] = np.sign(audio_float[above_knee]) * limited
        
        # Final clip to ensure no samples exceed 1.0
        output = np.clip(output, -1.0, 1.0)
        
        # Convert back to int16
        return (output * 32768.0).clip(-32768, 32767).astype(np.int16)
