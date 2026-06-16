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

import base64
import wave
import io
import pathlib

def make_wav_header(data_length, sample_rate, num_channels, sample_width):
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as wf:
        wf.setnchannels(num_channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(sample_rate)
        wf.writeframes(b'\x00' * data_length)  # 只写长度
    return buffer.getvalue()[:44]  # 只取header

def wav_to_base64(wav_file_path):
    # 以二进制模式打开WAV文件
    with open(wav_file_path, "rb") as wav_file:
        # 读取文件内容
        wav_data = wav_file.read()
        # 将二进制数据编码为base64
        base64_encoded = base64.b64encode(wav_data)
        # 将bytes转换为字符串
        base64_string = base64_encoded.decode('utf-8')
        return base64_string

def pcm_to_wav(pcm_data, sample_rate=16000, channels=1, sample_width=2):
    wav_buffer = io.BytesIO()
    with wave.open(wav_buffer, 'wb') as wav_file:
        wav_file.setnchannels(channels)  # 单声道
        wav_file.setsampwidth(sample_width)  # 16位音频
        wav_file.setframerate(sample_rate)  # 采样率
        wav_file.writeframes(pcm_data)

    wav_buffer.seek(0)  # 重要：将指针重置到开始位置
    return wav_buffer.getvalue(), wav_buffer


def select_voice_clone_sample_rate(sample_rate: int) -> int:
    """Pick an allowed target sample rate for the voice-clone reference audio.

    Alibaba Cloud DashScope VoiceEnrollmentService requires a sample rate of at least 16kHz.
    Rules:
    1. If the original rate >= 48000Hz, match down to 48000Hz
    2. If the original rate is within 44100-47999Hz, match down to 44100Hz
    3. If the original rate is within 22050-44099Hz, match down to 22050Hz
    4. If the original rate is within 16000-22049Hz, match down to 16000Hz
    5. If the original rate < 16000Hz, raise an error (the API requires at least 16kHz)

    Examples: 32000 -> 22050, 47000 -> 44100, 96000 -> 48000.
    Files below 16kHz are rejected; users must supply audio meeting the requirement.
    """
    if sample_rate < 16000:
        raise ValueError(
            f"采样率过低: {sample_rate}Hz。"
            f"阿里云语音克隆API要求参考音频采样率至少为16kHz，"
            f"请提供16kHz、22.05kHz、44.1kHz或48kHz的音频文件。"
        )

    allowed_sample_rates = [16000, 22050, 44100, 48000]
    for allowed_rate in reversed(allowed_sample_rates):
        if sample_rate >= allowed_rate:
            return allowed_rate
    return 16000  # 保底，理论上不会走到这里


def normalize_voice_clone_api_audio(
    file_buffer: io.BytesIO,
    filename: str,
) -> tuple[io.BytesIO, str, dict]:
    """Re-generate the uploaded voice-clone reference audio as a normalized WAV.

    This is not mere "validation"; regardless of whether the original file already
    meets the requirements, it will:
    1. decode the original audio;
    2. match the sample rate down against the allowed list;
    3. convert to mono;
    4. convert to 16-bit;
    5. export as a new WAV file before uploading.

    Uses pyav for audio processing.

    Returns:
    - normalized WAV binary buffer
    - normalized filename (always .wav)
    - original/normalized audio metadata for logging
    """
    import numpy as np
    try:
        import av
    except ImportError as err:
        raise ValueError(
            '缺少 av 依赖，无法自动转换语音克隆音频。请安装 pyav。'
        ) from err

    file_buffer.seek(0)

    try:
        # 使用 pyav 打开音频文件
        with av.open(file_buffer, mode="r") as container:

            # 获取音频流
            audio_streams = [s for s in container.streams if s.type == 'audio']
            if not audio_streams:
                raise ValueError('文件中没有音频流')

            stream = audio_streams[0]
            original_sample_rate = stream.sample_rate
            original_channels = stream.channels

            # 采样率按允许值集合向下匹配
            target_sample_rate = select_voice_clone_sample_rate(original_sample_rate)

            original_info = {
                'sample_rate': original_sample_rate,
                'channels': original_channels,
                'sample_width': 2,  # pyav 默认使用 16-bit
                'duration_ms': 0,
            }

            resampler = av.AudioResampler(
                format='s16',
                layout='mono',
                rate=target_sample_rate,
            )
            audio_chunks = []
            total_input_samples = 0

            for packet in container.demux(stream):
                for frame in packet.decode():
                    total_input_samples += frame.samples
                    resampled_frames = resampler.resample(frame)
                    if not isinstance(resampled_frames, list):
                        resampled_frames = [resampled_frames] if resampled_frames is not None else []
                    for resampled_frame in resampled_frames:
                        chunk = resampled_frame.to_ndarray()
                        if chunk is None:
                            continue
                        audio_chunks.append(np.asarray(chunk).reshape(-1))

            flushed_frames = resampler.resample(None)
            if not isinstance(flushed_frames, list):
                flushed_frames = [flushed_frames] if flushed_frames is not None else []
            for flushed_frame in flushed_frames:
                chunk = flushed_frame.to_ndarray()
                if chunk is None:
                    continue
                audio_chunks.append(np.asarray(chunk).reshape(-1))

            if not audio_chunks:
                raise ValueError('音频数据为空')

            audio_array = np.concatenate(audio_chunks).astype(np.int16, copy=False)
            original_info['duration_ms'] = int(total_input_samples / original_sample_rate * 1000)

            # 直接写出标准 PCM WAV，避免 PyAV 在 WAV 编码参数上的兼容性问题
            output_buffer = io.BytesIO()
            mono_audio = np.ascontiguousarray(audio_array.reshape(-1))
            with wave.open(output_buffer, 'wb') as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(target_sample_rate)
                wav_file.writeframes(mono_audio.tobytes())

            output_buffer.seek(0)
            normalized_info = {
                'sample_rate': target_sample_rate,
                'channels': 1,
                'sample_width': 2,
                'n_frames': int(mono_audio.shape[0]),
            }

            output_buffer.seek(0)
            normalized_filename = f"{pathlib.Path(filename or 'prompt_audio').stem}.wav"
            return output_buffer, normalized_filename, {
                'original': original_info,
                'normalized': normalized_info,
            }

    except Exception as err:
        raise ValueError(f'无法解析或处理上传音频文件: {err}') from err


def validate_audio_file(file_buffer: io.BytesIO, filename: str) -> tuple[str, str]:
    """Validate audio file type and format.

    Args:
        file_buffer: in-memory buffer of the audio file
        filename: original filename (used to determine the extension)

    Returns:
        (mime_type, error_message) — empty mime_type means validation failed,
        empty error_message means no error (or warning only).
    """
    try:
        import av
    except ImportError:
        return "", "缺少 av 依赖，无法校验音频文件。请安装 pyav。"

    file_path_obj = pathlib.Path(filename)
    file_extension = file_path_obj.suffix.lower()

    # 检查文件扩展名
    if file_extension not in ['.wav', '.mp3', '.m4a']:
        return "", f"不支持的文件格式: {file_extension}。仅支持 WAV、MP3 和 M4A 格式。"

    # 根据扩展名确定MIME类型
    if file_extension == '.wav':
        mime_type = "audio/wav"
        try:
            file_buffer.seek(0)
            with av.open(file_buffer, mode='r') as container:
                audio_streams = [s for s in container.streams if s.type == 'audio']
                if not audio_streams:
                    return "", "WAV文件中没有音频流。"
                stream = audio_streams[0]
                _ = stream.sample_rate
                _ = stream.channels
            file_buffer.seek(0)
        except Exception as e:
            return "", f"WAV文件格式错误: {str(e)}。请确认您的文件是合法的WAV文件。"

    elif file_extension == '.mp3':
        mime_type = "audio/mpeg"
        try:
            file_buffer.seek(0)
            header = file_buffer.read(32)
            file_buffer.seek(0)

            file_size = len(file_buffer.getvalue())
            if file_size < 1024:
                return "", "MP3文件太小，可能不是有效的音频文件。"
            if file_size > 1024 * 1024 * 10:
                return "", "MP3文件太大，可能不是有效的音频文件。"

            has_id3_header = header.startswith(b'ID3')
            has_frame_sync = False
            for i in range(len(header) - 1):
                if header[i] == 0xFF and (header[i + 1] & 0xE0) == 0xE0:
                    has_frame_sync = True
                    break

            if not has_id3_header and not has_frame_sync:
                return mime_type, f"警告: MP3文件可能格式不标准，文件头: {header[:4].hex()}"

        except Exception as e:
            return "", f"MP3文件读取错误: {str(e)}。请确认您的文件是合法的MP3文件。"

    elif file_extension == '.m4a':
        mime_type = "audio/mp4"
        try:
            file_buffer.seek(0)
            header = file_buffer.read(32)
            file_buffer.seek(0)

            if b'ftyp' not in header:
                return "", "M4A文件格式无效或已损坏。请确认您的文件是合法的M4A文件。"

            valid_types = [b'mp4a', b'M4A ', b'M4V ', b'isom', b'iso2', b'avc1']
            has_valid_type = any(t in header for t in valid_types)

            if not has_valid_type:
                return mime_type, "警告: M4A文件格式无效或已损坏。请确认您的文件是合法的M4A文件。"

        except Exception as e:
            return "", f"M4A文件读取错误: {str(e)}。请确认您的文件是合法的M4A文件。"

    return mime_type, ""
