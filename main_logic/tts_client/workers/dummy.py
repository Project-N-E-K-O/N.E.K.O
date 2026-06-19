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

"""No-op TTS worker (drains the queue, emits no audio)."""

from .._infra import TTS_SHUTDOWN_SENTINEL
from .._telemetry import _record_tts_telemetry
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Main")

def dummy_tts_worker(request_queue, response_queue, audio_api_key, voice_id):
    """
    Empty TTS worker (for core_apis without TTS support)
    Keeps draining the request queue without producing any audio, so the program runs normally but with no speech output
    
    Args:
        request_queue: multiprocess request queue receiving (speech_id, text) tuples
        response_queue: multiprocess response queue (also used for the ready signal)
        audio_api_key: API key (unused)
        voice_id: voice ID (unused)
    """
    logger.warning("TTS Worker 未启用，不会生成语音")
    
    # 立即发送就绪信号
    response_queue.put(("__ready__", True))
    
    while True:
        try:
            # 持续清空队列以避免阻塞，但不做任何处理
            sid, tts_text = request_queue.get()
            if sid == TTS_SHUTDOWN_SENTINEL:
                break
            # sid is None 是 end-of-utterance 信号，dummy 不做任何处理
            if sid == "__interrupt__" or sid is None:
                continue
            # 即便不合成音频也上报字符数 + 调用次数，方便分析"配置成无 TTS"
            # 的用户产生了多少假装合成的请求；只传 len()，不传原文。
            if tts_text:
                _record_tts_telemetry("dummy", len(tts_text))
        except Exception as e:
            logger.error(f"Dummy TTS Worker 错误: {e}")
            break
