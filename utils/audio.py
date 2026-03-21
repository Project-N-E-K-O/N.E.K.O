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
    """为语音克隆参考音频选择允许的目标采样率。

    阿里云 DashScope VoiceEnrollmentService 要求采样率至少为 16kHz。
    规则：
    1. 如果原采样率 >= 48000Hz，向下匹配到 48000Hz
    2. 如果原采样率在 44100-47999Hz 之间，向下匹配到 44100Hz
    3. 如果原采样率在 22050-44099Hz 之间，向下匹配到 22050Hz
    4. 如果原采样率在 16000-22049Hz 之间，向下匹配到 16000Hz
    5. 如果原采样率 < 16000Hz，抛出错误（API最低要求16kHz）

    例如：32000 -> 22050，47000 -> 44100，96000 -> 48000。
    低于16kHz的文件会被拒绝，需要用户自行提供符合要求的音频。
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
    """将上传的语音克隆参考音频重新生成成规范化 WAV。

    这里不是只做"校验"，而是无论原文件是否已经满足要求，都会：
    1. 解析原始音频；
    2. 将采样率按允许列表向下匹配；
    3. 转为单声道；
    4. 转为 16-bit；
    5. 导出为新的 WAV 文件后再上传。

    使用 pyav 进行音频处理。

    返回值：
    - 规范化后的 WAV 二进制缓冲区
    - 规范化后的文件名（统一为 .wav）
    - 原始/规范化后的音频元信息，供日志记录使用
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
