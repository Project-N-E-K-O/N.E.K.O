import base64
import wave
import io
# from config import QWEN_OMNI_URL
# from funasr import AutoModel
#########

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
