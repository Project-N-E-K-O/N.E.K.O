# -*- coding: utf-8 -*-
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

"""
Audio silence detection and trimming tool

Features:
- Detects silent segments using an RMS energy detection algorithm
- Shrinks overlong silent segments to a fixed duration (trimming from the exact middle)
- Keeps head/tail edges of silent segments for natural transitions, introducing no phase discontinuity
- Keeps output technical parameters identical to the input
- Supports cancellation and progress callbacks
- MD5 checksum ensures data integrity

Silence threshold: below -40 dBFS lasting continuously >= 200 ms
Trimming strategy: each silent segment is shrunk to 200 ms, trimmed from the exact middle
"""

import io
import wave
import hashlib
import logging
import math
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np

logger = logging.getLogger(__name__)

# ─────────────────── 常量 ───────────────────
SILENCE_THRESHOLD_DBFS = -40.0  # 静音阈值 (dBFS)
MIN_SILENCE_DURATION_MS = 200   # 最小静音持续时间 (ms)
RETAINED_SILENCE_MS = 200       # 每段静音裁剪后保留的时长 (ms)
RMS_FRAME_DURATION_MS = 10      # RMS 计算帧长 (ms)


@dataclass
class SilenceSegment:
    """A detected silent interval"""
    start_ms: float   # 起始时间 (ms)
    end_ms: float     # 结束时间 (ms)

    @property
    def duration_ms(self) -> float:
        return self.end_ms - self.start_ms


@dataclass
class SilenceAnalysisResult:
    """Silence analysis result"""
    original_duration_ms: float           # 原始音频总时长 (ms)
    silence_segments: list[SilenceSegment] = field(default_factory=list)  # 所有静音段
    total_silence_ms: float = 0.0         # 检测到的静音总时长 (ms)
    removable_silence_ms: float = 0.0     # 实际可移除的静音时长 (ms)
    estimated_duration_ms: float = 0.0    # 处理后预计剩余时长 (ms)
    saving_percentage: float = 0.0        # 节省百分比 (基于实际可移除量)
    sample_rate: int = 0
    sample_width: int = 0                 # bytes
    channels: int = 0


@dataclass
class TrimResult:
    """Trim processing result"""
    audio_data: bytes           # 处理后的音频二进制数据 (WAV)
    md5: str                    # MD5 校验值
    original_duration_ms: float
    trimmed_duration_ms: float
    removed_silence_ms: float
    sample_rate: int
    sample_width: int
    channels: int
    filename: str = ""


class SilenceRemovalCancelledError(Exception):
    """Task cancelled by the user"""
    pass


# 兼容别名，避免破坏现有调用方
CancelledError = SilenceRemovalCancelledError


def _samples_to_float(data: bytes, sample_width: int) -> np.ndarray:
    """Convert raw PCM bytes to a float32 numpy array (range -1.0 ~ 1.0)."""
    if sample_width == 1:
        # 8-bit unsigned
        arr = np.frombuffer(data, dtype=np.uint8).astype(np.float32)
        arr = (arr - np.float32(128.0)) / np.float32(128.0)
    elif sample_width == 2:
        # 16-bit signed
        arr = np.frombuffer(data, dtype=np.int16).astype(np.float32)
        arr = arr / np.float32(32768.0)
    elif sample_width == 3:
        # 24-bit signed – numpy 向量化解码
        n_samples = len(data) // 3
        raw = np.frombuffer(data, dtype=np.uint8).reshape(n_samples, 3)
        # 组装为 32-bit 整数 (little-endian: byte0 + byte1<<8 + byte2<<16)
        i32 = (raw[:, 0].astype(np.int32)
               | (raw[:, 1].astype(np.int32) << 8)
               | (raw[:, 2].astype(np.int32) << 16))
        # 符号扩展: 如果最高位 (bit 23) 为 1，扩展为负数
        i32[i32 >= 0x800000] -= 0x1000000
        arr = i32.astype(np.float32) / np.float32(8388608.0)
    elif sample_width == 4:
        # 32-bit signed
        arr = np.frombuffer(data, dtype=np.int32).astype(np.float32)
        arr = arr / np.float32(2147483648.0)
    else:
        raise ValueError(f"不支持的采样宽度: {sample_width} bytes")
    return arr


def _float_to_samples(arr: np.ndarray, sample_width: int) -> bytes:
    """Convert a floating-point numpy array (-1.0 ~ 1.0) to raw PCM bytes."""
    arr = np.clip(arr, -1.0, 1.0)
    if sample_width == 1:
        out = ((arr * 128.0) + 128.0).astype(np.uint8)
        return out.tobytes()
    elif sample_width == 2:
        out = (arr * 32768.0).astype(np.int16)
        return out.tobytes()
    elif sample_width == 3:
        # numpy 向量化 24-bit 编码
        i32 = np.clip(arr * 8388608.0, -8388608, 8388607).astype(np.int32)
        # 将有符号 32-bit 转为无符号以便位运算提取字节
        u32 = i32.view(np.uint32)
        raw = np.empty((len(u32), 3), dtype=np.uint8)
        raw[:, 0] = u32 & 0xFF
        raw[:, 1] = (u32 >> 8) & 0xFF
        raw[:, 2] = (u32 >> 16) & 0xFF
        return raw.tobytes()
    elif sample_width == 4:
        out = (arr * 2147483648.0).astype(np.int32)
        return out.tobytes()
    else:
        raise ValueError(f"不支持的采样宽度: {sample_width} bytes")


def _rms_dbfs(samples: np.ndarray) -> float:
    """Compute the RMS value (dBFS) of one frame of samples"""
    if len(samples) == 0:
        return -100.0
    rms = np.sqrt(np.mean(samples ** 2))
    if rms < 1e-10:
        return -100.0
    return 20.0 * math.log10(rms)


def detect_silence(
    audio_buffer: io.BytesIO,
    threshold_dbfs: float = SILENCE_THRESHOLD_DBFS,
    min_silence_ms: float = MIN_SILENCE_DURATION_MS,
    progress_callback: Optional[Callable[[int], None]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> SilenceAnalysisResult:
    """
    Analyze silent segments in WAV audio.

    Args:
        audio_buffer: BytesIO of the WAV audio data
        threshold_dbfs: silence threshold (dBFS); below this counts as silence
        min_silence_ms: minimum silence duration (ms)
        progress_callback: progress callback (0-100)
        cancel_check: cancellation callback; returning True means cancel

    Returns:
        SilenceAnalysisResult
    """
    audio_buffer.seek(0)
    with wave.open(audio_buffer, 'rb') as wf:
        sample_rate = wf.getframerate()
        sample_width = wf.getsampwidth()
        channels = wf.getnchannels()
        n_frames = wf.getnframes()
        raw_data = wf.readframes(n_frames)

    duration_ms = (n_frames / sample_rate) * 1000.0

    # 转为 float
    float_samples = _samples_to_float(raw_data, sample_width)

    # 如果是多声道，取平均作为单声道进行分析
    if channels > 1:
        float_samples_mono = float_samples.reshape(-1, channels).mean(axis=1)
    else:
        float_samples_mono = float_samples

    # 每帧的采样数
    frame_size = int(sample_rate * RMS_FRAME_DURATION_MS / 1000.0)
    if frame_size < 1:
        frame_size = 1

    total_frames = len(float_samples_mono) // frame_size
    silence_segments: list[SilenceSegment] = []
    in_silence = False
    silence_start_frame = 0

    for i in range(total_frames):
        if cancel_check and cancel_check():
            raise SilenceRemovalCancelledError("静音检测已被用户取消")

        start_idx = i * frame_size
        end_idx = start_idx + frame_size
        frame_data = float_samples_mono[start_idx:end_idx]

        rms = _rms_dbfs(frame_data)

        if rms < threshold_dbfs:
            if not in_silence:
                in_silence = True
                silence_start_frame = i
        else:
            if in_silence:
                in_silence = False
                start_ms = (silence_start_frame * frame_size / sample_rate) * 1000.0
                end_ms = (i * frame_size / sample_rate) * 1000.0
                seg_duration = end_ms - start_ms
                if seg_duration >= min_silence_ms:
                    silence_segments.append(SilenceSegment(start_ms=start_ms, end_ms=end_ms))

        # 进度回调 (检测阶段占 0-100%)
        if progress_callback and i % max(1, total_frames // 100) == 0:
            pct = int((i / total_frames) * 100)
            progress_callback(min(pct, 100))

    # 处理末尾仍在静音中的情况
    if in_silence:
        start_ms = (silence_start_frame * frame_size / sample_rate) * 1000.0
        end_ms = duration_ms
        seg_duration = end_ms - start_ms
        if seg_duration >= min_silence_ms:
            silence_segments.append(SilenceSegment(start_ms=start_ms, end_ms=end_ms))

    total_silence_ms = sum(s.duration_ms for s in silence_segments)
    # 每段静音保留 RETAINED_SILENCE_MS，超出部分才是实际可移除量
    removable_silence_ms = sum(
        max(0, s.duration_ms - RETAINED_SILENCE_MS) for s in silence_segments
    )
    estimated_duration_ms = duration_ms - removable_silence_ms
    saving_pct = (removable_silence_ms / duration_ms * 100.0) if duration_ms > 0 else 0.0

    if progress_callback:
        progress_callback(100)

    result = SilenceAnalysisResult(
        original_duration_ms=duration_ms,
        silence_segments=silence_segments,
        total_silence_ms=total_silence_ms,
        removable_silence_ms=removable_silence_ms,
        estimated_duration_ms=estimated_duration_ms,
        saving_percentage=round(saving_pct, 1),
        sample_rate=sample_rate,
        sample_width=sample_width,
        channels=channels,
    )
    logger.info(
        "静音分析完成: 原始时长=%.1fms, 静音段=%d个, 检测静音=%.1fms, 可移除=%.1fms, 预计剩余=%.1fms, 节省=%.1f%%",
        duration_ms, len(silence_segments), total_silence_ms,
        removable_silence_ms, estimated_duration_ms, saving_pct,
    )
    return result


def trim_silence(
    audio_buffer: io.BytesIO,
    analysis: SilenceAnalysisResult,
    progress_callback: Optional[Callable[[int], None]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> TrimResult:
    """
    Shrink each silent segment to RETAINED_SILENCE_MS (200 ms) based on the silence analysis.

    Trimming strategy:
        For each detected silent interval, keep RETAINED_SILENCE_MS / 2 edges at the
        head and tail, and remove the excess silence in the exact middle. Samples at
        the splice points transition naturally (all near zero), introducing no new
        phase discontinuities or clicks.

    Args:
        audio_buffer: BytesIO of the original WAV audio
        analysis: analysis result returned by detect_silence
        progress_callback: progress callback (0-100)
        cancel_check: cancellation callback

    Returns:
        TrimResult
    """
    if not analysis.silence_segments:
        # 没有静音段，直接返回原始音频
        audio_buffer.seek(0)
        original_data = audio_buffer.read()
        md5 = hashlib.md5(original_data).hexdigest()
        return TrimResult(
            audio_data=original_data,
            md5=md5,
            original_duration_ms=analysis.original_duration_ms,
            trimmed_duration_ms=analysis.original_duration_ms,
            removed_silence_ms=0,
            sample_rate=analysis.sample_rate,
            sample_width=analysis.sample_width,
            channels=analysis.channels,
        )

    audio_buffer.seek(0)
    with wave.open(audio_buffer, 'rb') as wf:
        sample_rate = wf.getframerate()
        sample_width = wf.getsampwidth()
        channels = wf.getnchannels()
        n_frames = wf.getnframes()
        raw_data = wf.readframes(n_frames)

    # 每侧保留的采样数
    retain_half_samples = int(sample_rate * (RETAINED_SILENCE_MS / 2) / 1000.0)

    if progress_callback:
        progress_callback(0)

    # 按顺序遍历音频，对每段静音只保留首尾各 retain_half，移除正中间部分
    total_segs = len(analysis.silence_segments)
    prev_end = 0  # 上一次拷贝到的样本位置
    kept_frames = 0
    frame_width = sample_width * channels
    raw_view = memoryview(raw_data)
    output_buf = io.BytesIO()
    with wave.open(output_buf, 'wb') as out_wf:
        out_wf.setnchannels(channels)
        out_wf.setsampwidth(sample_width)
        out_wf.setframerate(sample_rate)

        for idx, seg in enumerate(analysis.silence_segments):
            if cancel_check and cancel_check():
                raise SilenceRemovalCancelledError("裁剪处理已被用户取消")

            seg_start = int(seg.start_ms * sample_rate / 1000.0)
            seg_end = int(seg.end_ms * sample_rate / 1000.0)

            # 计算中心裁剪区域
            cut_start = seg_start + retain_half_samples  # 前半保留结束点
            cut_end = seg_end - retain_half_samples       # 后半保留起始点

            if cut_start >= cut_end:
                # 静音段不足以裁剪（≤ RETAINED_SILENCE_MS），保留完整静音
                continue

            # 直接写入原始 PCM 帧，避免浮点往返和整段 concatenate。
            if cut_start > prev_end:
                out_wf.writeframesraw(
                    raw_view[prev_end * frame_width : cut_start * frame_width]
                )
                kept_frames += cut_start - prev_end

            # 跳过中间部分 [cut_start, cut_end)
            prev_end = cut_end

            # 进度回调
            if progress_callback:
                pct = int(((idx + 1) / total_segs) * 100)
                progress_callback(min(pct, 100))

        # 拷贝最后一段静音之后的剩余音频
        if prev_end < n_frames:
            out_wf.writeframesraw(raw_view[prev_end * frame_width :])
            kept_frames += n_frames - prev_end

    output_data = output_buf.getvalue()
    md5 = hashlib.md5(output_data).hexdigest()

    trimmed_duration_ms = (kept_frames / sample_rate) * 1000.0

    if progress_callback:
        progress_callback(100)

    result = TrimResult(
        audio_data=output_data,
        md5=md5,
        original_duration_ms=analysis.original_duration_ms,
        trimmed_duration_ms=trimmed_duration_ms,
        removed_silence_ms=analysis.original_duration_ms - trimmed_duration_ms,
        sample_rate=sample_rate,
        sample_width=sample_width,
        channels=channels,
    )
    logger.info(
        "裁剪完成: 原始=%.1fms → 裁剪后=%.1fms, 移除=%.1fms, MD5=%s",
        result.original_duration_ms, result.trimmed_duration_ms,
        result.removed_silence_ms, result.md5,
    )
    return result


def format_duration_mmss(ms: float) -> str:
    """Convert milliseconds to mm:ss format"""
    total_seconds = int(ms / 1000.0)
    minutes = total_seconds // 60
    seconds = total_seconds % 60
    return f"{minutes:02d}:{seconds:02d}"


def convert_to_wav_if_needed(audio_buffer: io.BytesIO, filename: str) -> tuple[io.BytesIO, str]:
    """
    If the input is not WAV, decode it to 16-bit PCM mono WAV using pyav.
    WAV files are returned as-is (no resampling/format conversion).

    Returns: (wav_buffer, original_format)
    """
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''

    if ext == 'wav':
        audio_buffer.seek(0)
        try:
            with wave.open(audio_buffer, 'rb') as _:
                pass
            audio_buffer.seek(0)
            return audio_buffer, 'wav'
        except Exception as err:
            raise ValueError("无效的 WAV 文件") from err

    try:
        import av
    except ImportError as err:
        raise ValueError(
            f"不支持直接处理 .{ext} 格式的音频文件。"
            "请安装 pyav，或上传 WAV 格式文件。"
        ) from err

    audio_buffer.seek(0)
    try:
        with av.open(audio_buffer, mode='r') as container:
            audio_streams = [s for s in container.streams if s.type == 'audio']
            if not audio_streams:
                raise ValueError('文件中没有音频流')
            stream = audio_streams[0]

            sample_rate = 0
            resampler: av.AudioResampler | None = None
            audio_chunks: list[np.ndarray] = []

            def _drain(resampled):
                if resampled is None:
                    return
                frames = resampled if isinstance(resampled, list) else [resampled]
                for rf in frames:
                    chunk = rf.to_ndarray()
                    if chunk is None:
                        continue
                    audio_chunks.append(np.asarray(chunk).reshape(-1))

            for packet in container.demux(stream):
                for frame in packet.decode():
                    if resampler is None:
                        # 优先用流元数据；某些容器/编码下 stream.sample_rate
                        # 会缺失或为 0，此时回退到首个解码帧的采样率
                        sample_rate = int(stream.sample_rate or frame.sample_rate or 0)
                        if sample_rate <= 0:
                            raise ValueError('无法确定音频采样率')
                        resampler = av.AudioResampler(
                            format='s16', layout='mono', rate=sample_rate
                        )
                    _drain(resampler.resample(frame))

            if resampler is None:
                raise ValueError('音频数据为空')
            _drain(resampler.resample(None))

            if not audio_chunks:
                raise ValueError('音频数据为空')

            samples = np.concatenate(audio_chunks).astype(np.int16, copy=False)

            wav_buf = io.BytesIO()
            with wave.open(wav_buf, 'wb') as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(sample_rate)
                wav_file.writeframes(samples.tobytes())
            wav_buf.seek(0)
            return wav_buf, ext
    except ValueError:
        raise
    except Exception as err:
        raise ValueError(f"无法解析 .{ext} 音频文件: {err}") from err


def convert_wav_back(wav_buffer: io.BytesIO, original_format: str, original_params: dict) -> io.BytesIO:
    """
    Convert WAV back to the original format (if the original format is not WAV).
    Keeps technical parameters identical to the original file.
    """
    if original_format == 'wav':
        wav_buffer.seek(0)
        return wav_buffer

    try:
        from pydub import AudioSegment
    except ImportError:
        # fallback: 返回 WAV
        wav_buffer.seek(0)
        return wav_buffer

    wav_buffer.seek(0)
    audio_seg = AudioSegment.from_wav(wav_buffer)

    output_buf = io.BytesIO()
    export_params = {}
    bitrate = original_params.get('bitrate')
    if bitrate:
        export_params['bitrate'] = bitrate

    audio_seg.export(output_buf, format=original_format, **export_params)
    output_buf.seek(0)
    return output_buf
