# -*- coding: utf-8 -*-
"""
音频静音检测与裁剪工具

功能:
- 基于 RMS 能量检测算法识别静音段落
- 将超长静音段缩减至固定时长（从静音段正中间裁剪）
- 保留静音段首尾边缘以确保自然过渡，不引入相位不连续
- 保持输出与输入完全一致的技术参数
- 支持取消操作与进度回调
- MD5 校验确保数据完整性

静音阈值: -40 dBFS 以下且连续持续时间 ≥ 200 ms
裁剪策略: 每段静音缩减至 200 ms，从正中间执行裁剪
"""

import io
import wave
import struct
import hashlib
import logging
import math
import time
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
    """一段被检测到的静音区间"""
    start_ms: float   # 起始时间 (ms)
    end_ms: float     # 结束时间 (ms)

    @property
    def duration_ms(self) -> float:
        return self.end_ms - self.start_ms


@dataclass
class SilenceAnalysisResult:
    """静音分析结果"""
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
    """裁剪处理结果"""
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
    """任务被用户取消"""
    pass


# 兼容别名，避免破坏现有调用方
CancelledError = SilenceRemovalCancelledError


def _samples_to_float(data: bytes, sample_width: int) -> np.ndarray:
    """将原始 PCM bytes 转为 float64 numpy 数组 (范围 -1.0 ~ 1.0)"""
    if sample_width == 1:
        # 8-bit unsigned
        arr = np.frombuffer(data, dtype=np.uint8).astype(np.float64)
        arr = (arr - 128.0) / 128.0
    elif sample_width == 2:
        # 16-bit signed
        arr = np.frombuffer(data, dtype=np.int16).astype(np.float64)
        arr = arr / 32768.0
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
        arr = i32.astype(np.float64) / 8388608.0
    elif sample_width == 4:
        # 32-bit signed
        arr = np.frombuffer(data, dtype=np.int32).astype(np.float64)
        arr = arr / 2147483648.0
    else:
        raise ValueError(f"不支持的采样宽度: {sample_width} bytes")
    return arr


def _float_to_samples(arr: np.ndarray, sample_width: int) -> bytes:
    """将 float64 numpy 数组 (-1.0 ~ 1.0) 转回原始 PCM bytes"""
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
    """计算一帧采样的 RMS 值 (dBFS)"""
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
    分析 WAV 音频中的静音段落。

    参数:
        audio_buffer: WAV 音频数据的 BytesIO
        threshold_dbfs: 静音阈值 (dBFS)，低于此值视为静音
        min_silence_ms: 最小静音持续时间 (ms)
        progress_callback: 进度回调 (0-100)
        cancel_check: 取消检测回调，返回 True 表示取消

    返回:
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
    根据静音分析结果，将每段静音缩减至 RETAINED_SILENCE_MS (200 ms)。

    裁剪策略:
        对每段检测到的静音区间，保留首尾各 RETAINED_SILENCE_MS / 2 的边缘，
        移除正中间多余的静音。这样拼接点处的采样自然过渡（均为近零值），
        不会引入新的相位不连续或咔嗒声。

    参数:
        audio_buffer: 原始 WAV 音频的 BytesIO
        analysis: detect_silence 返回的分析结果
        progress_callback: 进度回调 (0-100)
        cancel_check: 取消检测回调

    返回:
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

    float_samples = _samples_to_float(raw_data, sample_width)

    # 对于多声道，reshape 为 (n_frames, channels)
    if channels > 1:
        float_samples = float_samples.reshape(-1, channels)
    else:
        float_samples = float_samples.reshape(-1, 1)

    # 每侧保留的采样数
    retain_half_samples = int(sample_rate * (RETAINED_SILENCE_MS / 2) / 1000.0)

    if progress_callback:
        progress_callback(0)

    # 按顺序遍历音频，对每段静音只保留首尾各 retain_half，移除正中间部分
    total_segs = len(analysis.silence_segments)
    result_parts: list[np.ndarray] = []
    prev_end = 0  # 上一次拷贝到的样本位置

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

        # 拷贝: 从 prev_end 到 cut_start（包含语音 + 静音前半段保留）
        if cut_start > prev_end:
            result_parts.append(float_samples[prev_end:cut_start])

        # 跳过中间部分 [cut_start, cut_end)
        prev_end = cut_end

        # 进度回调
        if progress_callback:
            pct = int(((idx + 1) / total_segs) * 100)
            progress_callback(min(pct, 100))

    # 拷贝最后一段静音之后的剩余音频
    total_samples_per_channel = float_samples.shape[0]
    if prev_end < total_samples_per_channel:
        result_parts.append(float_samples[prev_end:total_samples_per_channel])

    if not result_parts:
        # 极端情况：没有任何内容需要保留（理论上不会发生）
        result_parts.append(float_samples[:0])  # 空数组，保持 shape 兼容

    # 拼接所有段
    final_samples = np.concatenate(result_parts, axis=0)

    # reshape 回一维 (多声道交错)
    final_flat = final_samples.reshape(-1)

    # 转回 PCM bytes
    pcm_data = _float_to_samples(final_flat, sample_width)

    # 写入 WAV
    output_buf = io.BytesIO()
    with wave.open(output_buf, 'wb') as out_wf:
        out_wf.setnchannels(channels)
        out_wf.setsampwidth(sample_width)
        out_wf.setframerate(sample_rate)
        out_wf.writeframes(pcm_data)

    output_data = output_buf.getvalue()
    md5 = hashlib.md5(output_data).hexdigest()

    trimmed_duration_ms = (final_samples.shape[0] / sample_rate) * 1000.0

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
    """将毫秒转为 mm:ss 格式"""
    total_seconds = int(ms / 1000.0)
    minutes = total_seconds // 60
    seconds = total_seconds % 60
    return f"{minutes:02d}:{seconds:02d}"


def convert_to_wav_if_needed(audio_buffer: io.BytesIO, filename: str) -> tuple[io.BytesIO, str]:
    """
    如果输入不是 WAV，使用 pydub/ffmpeg 转换为 WAV。
    对 WAV 文件直接返回。

    返回: (wav_buffer, original_format)
    """
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''

    if ext == 'wav':
        audio_buffer.seek(0)
        # 验证是否为有效 WAV
        try:
            with wave.open(audio_buffer, 'rb') as _:
                pass
            audio_buffer.seek(0)
            return audio_buffer, 'wav'
        except Exception as err:
            raise ValueError("无效的 WAV 文件") from err

    # 对于 MP3/M4A 等格式，尝试使用 pydub 转换
    try:
        from pydub import AudioSegment
    except ImportError as err:
        raise ValueError(
            f"不支持直接处理 .{ext} 格式的音频文件。"
            "请安装 pydub 和 ffmpeg，或上传 WAV 格式文件。"
        ) from err

    audio_buffer.seek(0)
    audio_seg = AudioSegment.from_file(audio_buffer, format=ext)
    wav_buf = io.BytesIO()
    audio_seg.export(wav_buf, format='wav')
    wav_buf.seek(0)
    return wav_buf, ext


def convert_wav_back(wav_buffer: io.BytesIO, original_format: str, original_params: dict) -> io.BytesIO:
    """
    将 WAV 转回原始格式（如果原始格式不是 WAV）。
    保持与原始文件完全一致的技术参数。
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
