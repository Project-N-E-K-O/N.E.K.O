import os
import asyncio
import logging
import wave
from tts_realtime_client import TTSRealtimeClient, SessionMode
import pyaudio

# QwenTTS 服务配置
# 以下是北京地域url，如果使用新加坡地域的模型，需要将url替换为：wss://dashscope-intl.aliyuncs.com/api-ws/v1/realtime?model=qwen3-tts-flash-realtime
URL = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime?model=qwen3-tts-flash-realtime"
# 新加坡和北京地域的API Key不同。获取API Key：https://help.aliyun.com/zh/model-studio/get-api-key
# 若没有配置环境变量，请用百炼API Key将下行替换为：API_KEY="sk-xxx"
API_KEY = os.getenv("DASHSCOPE_API_KEY")

if not API_KEY:
    raise ValueError("Please set DASHSCOPE_API_KEY environment variable")

# 收集音频数据
_audio_chunks = []
# 实时播放相关
_AUDIO_SAMPLE_RATE = 24000
_audio_pyaudio = pyaudio.PyAudio()
_audio_stream = None  # 将在运行时打开

def _audio_callback(audio_bytes: bytes):
    """TTSRealtimeClient 音频回调: 实时播放并缓存"""
    global _audio_stream
    if _audio_stream is not None:
        try:
            _audio_stream.write(audio_bytes)
        except Exception as exc:
            logging.error(f"PyAudio playback error: {exc}")
    _audio_chunks.append(audio_bytes)
    logging.info(f"Received audio chunk: {len(audio_bytes)} bytes")

def _save_audio_to_file(filename: str = "output.wav", sample_rate: int = 24000) -> bool:
    """将收集到的音频数据保存为 WAV 文件"""
    if not _audio_chunks:
        logging.warning("No audio data to save")
        return False

    try:
        audio_data = b"".join(_audio_chunks)
        with wave.open(filename, 'wb') as wav_file:
            wav_file.setnchannels(1)  # 单声道
            wav_file.setsampwidth(2)  # 16-bit
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(audio_data)
        logging.info(f"Audio saved to: {filename}")
        return True
    except Exception as exc:
        logging.error(f"Failed to save audio: {exc}")
        return False

async def _produce_text(client: TTSRealtimeClient):
    """向服务器发送文本片段"""
    text_fragments = [
        "阿里云的大模型服务平台百炼是一站式的大模型开发及应用构建平台。",
        "不论是开发者还是业务人员，都能深入参与大模型应用的设计和构建。",
        "您可以通过简单的界面操作，在5分钟内开发出一款大模型应用，",
        "或在几小时内训练出一个专属模型，从而将更多精力专注于应用创新。",
    ]

    logging.info("Sending text fragments…")
    for text in text_fragments:
        logging.info(f"Sending fragment: {text}")
        await client.append_text(text)
        await asyncio.sleep(0.1)  # 片段间稍作延时

    # 等待服务器完成内部处理后结束会话
    await asyncio.sleep(1.0)
    await client.finish_session()

async def _run_demo():
    """运行完整 Demo"""
    global _audio_stream
    # 打开 PyAudio 输出流
    _audio_stream = _audio_pyaudio.open(
        format=pyaudio.paInt16,
        channels=1,
        rate=_AUDIO_SAMPLE_RATE,
        output=True,
        frames_per_buffer=1024
    )

    client = TTSRealtimeClient(
        base_url=URL,
        api_key=API_KEY,
        voice="Cherry",
        mode=SessionMode.SERVER_COMMIT,
        audio_callback=_audio_callback
    )

    # 建立连接
    await client.connect()

    # 并行执行消息处理与文本发送
    consumer_task = asyncio.create_task(client.handle_messages())
    producer_task = asyncio.create_task(_produce_text(client))

    await producer_task  # 等待文本发送完成

    # 等待 response.done
    await client.wait_for_response_done()

    # 关闭连接并取消消费者任务
    await client.close()
    consumer_task.cancel()

    # 关闭音频流
    if _audio_stream is not None:
        _audio_stream.stop_stream()
        _audio_stream.close()
    _audio_pyaudio.terminate()

    # 保存音频数据
    os.makedirs("outputs", exist_ok=True)
    _save_audio_to_file(os.path.join("outputs", "qwen_tts_output.wav"))

def main():
    """同步入口"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    logging.info("Starting QwenTTS Realtime Client demo…")
    asyncio.run(_run_demo())

if __name__ == "__main__":
    main()