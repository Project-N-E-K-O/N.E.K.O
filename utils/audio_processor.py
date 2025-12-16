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
import logging
from typing import Optional
import soxr
import time

logger = logging.getLogger(__name__)

# Lazy import pyrnnoise
_RNNoise = None
_rnnoise_available = None

def _get_rnnoise():
    """Lazy load RNNoise module."""
    global _RNNoise, _rnnoise_available
    if _rnnoise_available is None:
        try:
            from pyrnnoise import RNNoise
            _RNNoise = RNNoise
            _rnnoise_available = True
            logger.info("✅ pyrnnoise library loaded successfully")
        except ImportError:
            logger.warning("⚠️ pyrnnoise library not installed. Run: pip install pyrnnoise")
            _rnnoise_available = False
    return _RNNoise if _rnnoise_available else None


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
    RESET_TIMEOUT_SECONDS = 2.0
    
    # AGC Configuration
    AGC_TARGET_LEVEL = 0.25        # Target RMS level (0.0-1.0), raised for easier VAD trigger
    AGC_MAX_GAIN = 12.0             # Maximum gain multiplier, raised for quieter mics
    AGC_MIN_GAIN = 0.25            # Minimum gain multiplier
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
        limiter_enabled: bool = True
    ):
        self.input_sample_rate = input_sample_rate
        self.output_sample_rate = output_sample_rate
        self.noise_reduce_enabled = noise_reduce_enabled
        self.agc_enabled = agc_enabled
        self.limiter_enabled = limiter_enabled
        
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
        
        logger.info(f"🎤 AudioProcessor initialized: input={input_sample_rate}Hz, "
                   f"output={output_sample_rate}Hz, rnnoise={self._denoiser is not None}, "
                   f"agc={agc_enabled}, limiter={limiter_enabled}")
    
    def _init_denoiser(self) -> None:
        """Initialize RNNoise denoiser if available."""
        if not self.noise_reduce_enabled:
            return
        
        # RNNoise requires input at exactly 48kHz
        if self.input_sample_rate != self.RNNOISE_SAMPLE_RATE:
            logger.warning(
                f"⚠️ Skipping RNNoise initialization: input sample rate "
                f"{self.input_sample_rate}Hz != required {self.RNNOISE_SAMPLE_RATE}Hz"
            )
            return
            
        RNNoise = _get_rnnoise()
        if RNNoise:
            try:
                self._denoiser = RNNoise(sample_rate=self.RNNOISE_SAMPLE_RATE)
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
        if self._needs_reset or (current_time - self._last_speech_time > self.RESET_TIMEOUT_SECONDS):
            if self._denoiser is not None:
                self._reset_internal_state()
                self._last_speech_time = current_time  # Prevent infinite reset loop
                logger.debug("🔄 RNNoise state auto-reset after silence")
            self._needs_reset = False
        
        # Apply RNNoise if available (processes int16, returns int16)
        if self._denoiser is not None and self.noise_reduce_enabled:
            processed = self._process_with_rnnoise(audio_int16)
            if len(processed) == 0:
                return b''  # Buffering
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
        
        # Limit buffer size to prevent memory issues (max 1 second of audio)
        max_buffer_samples = self.RNNOISE_SAMPLE_RATE
        if len(self._frame_buffer) > max_buffer_samples:
            self._frame_buffer = self._frame_buffer[-max_buffer_samples:]
        
        # Process complete frames
        output_frames = []
        while len(self._frame_buffer) >= self.RNNOISE_FRAME_SIZE:
            frame = self._frame_buffer[:self.RNNOISE_FRAME_SIZE]
            self._frame_buffer = self._frame_buffer[self.RNNOISE_FRAME_SIZE:]
            
            # RNNoise expects [channels, samples] format with int16
            frame_2d = frame.reshape(1, -1)
            
            try:
                # Process frame - pyrnnoise takes int16 and returns int16
                for speech_prob, denoised_frame in self._denoiser.denoise_chunk(frame_2d):
                    prob = float(speech_prob[0])
                    self._last_speech_prob = prob
                    
                    # Track last time speech was detected
                    if prob > 0.5:
                        self._last_speech_time = time.time()
                    
                    output_frames.append(denoised_frame.flatten())
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
    
    @property
    def speech_probability(self) -> float:
        """Get the last detected speech probability (0.0-1.0)."""
        return self._last_speech_prob
    
    def set_enabled(self, enabled: bool) -> None:
        """Enable or disable noise reduction."""
        self.noise_reduce_enabled = enabled
        if enabled and self._denoiser is None:
            self._init_denoiser()
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
        
        # Calculate desired gain
        if rms > 1e-6:  # Only adjust if there's actual audio
            desired_gain = self.AGC_TARGET_LEVEL / rms
            desired_gain = np.clip(desired_gain, self.AGC_MIN_GAIN, self.AGC_MAX_GAIN)
        else:
            desired_gain = self._agc_gain  # Maintain current gain during silence
        
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
