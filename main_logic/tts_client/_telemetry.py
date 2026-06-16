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

"""TTS usage telemetry helper."""

def _record_tts_telemetry(model_name: str, char_count: int):
    """Record TTS usage telemetry via TokenTracker.

    TTS providers (CosyVoice, CogTTS, GPT-SoVITS, etc.) bill per character,
    not per token, so we report the input length on the dedicated
    `prompt_chars` field instead of squatting in `prompt_tokens`. Token
    aggregates stay clean for actual LLM usage tracking.

    Telemetry hard rule: this helper takes a count only. Never pass synthesized
    text or any substring of it — only ``len(text)``. Sending raw content into
    the tracker risks leaking user utterances through the remote uploader.
    """
    if char_count <= 0:
        return
    try:
        from utils.token_tracker import TokenTracker
        TokenTracker.get_instance().record(
            model=f"tts:{model_name}",
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            call_type='tts',
            prompt_chars=int(char_count),
        )
    except Exception:
        pass
