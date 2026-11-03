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
    
    # Send the ready signal immediately
    response_queue.put(("__ready__", True))
    
    while True:
        try:
            # Keep draining the queue to avoid blocking, without processing anything
            sid, tts_text = request_queue.get()
            if sid == TTS_SHUTDOWN_SENTINEL:
                break
            # sid is None signals end-of-utterance; the dummy does nothing
            if sid == "__interrupt__" or sid is None:
                continue
            # Report character counts and call counts even without audio synthesis, to measure simulated synthesis requests
            # from users configured with no TTS; send only len(), never the original text.
            if tts_text:
                _record_tts_telemetry("dummy", len(tts_text))
        except Exception as e:
            logger.error(f"Dummy TTS Worker 错误: {e}")
            break
