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

"""Small provider-neutral helpers shared by ASR workers."""

from __future__ import annotations

import io
import wave

# Segmented workers buffer one 16 kHz mono PCM16 utterance and cap it at 28
# seconds before submitting a single WAV request.
PCM16_SAMPLE_RATE_HZ = 16_000
PCM16_SAMPLE_WIDTH_BYTES = 2
MAX_SEGMENT_PCM_BYTES = PCM16_SAMPLE_RATE_HZ * PCM16_SAMPLE_WIDTH_BYTES * 28


def encode_pcm16_wav(pcm16: bytes) -> bytes:
    """Wrap mono 16 kHz PCM16LE in an in-memory WAV container."""

    if len(pcm16) % PCM16_SAMPLE_WIDTH_BYTES:
        raise ValueError("PCM16LE data has an odd byte length")
    output = io.BytesIO()
    with wave.open(output, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(PCM16_SAMPLE_WIDTH_BYTES)
        wav_file.setframerate(PCM16_SAMPLE_RATE_HZ)
        wav_file.writeframes(pcm16)
    return output.getvalue()


def normalize_zh_en_language(language: str, *, provider_name: str) -> str | None:
    """Normalize the shared auto/Chinese/English language contract."""

    normalized = language.strip().lower()
    if normalized == "auto":
        return None
    if normalized in {"zh", "zh-cn"}:
        return "zh"
    if normalized in {"en", "en-us"}:
        return "en"
    raise ValueError(
        f"ASR_LANGUAGE_NOT_SUPPORTED: {provider_name} language is unsupported"
    )


def is_auth_rejection(exc: BaseException) -> bool:
    """Return whether a provider exception carries an auth rejection status."""

    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    if status_code is None:
        status_code = getattr(exc, "status_code", None)
    return status_code in {401, 403}
