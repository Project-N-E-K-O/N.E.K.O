# -- coding: utf-8 --
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

from ._shared import (
    Dict,
    Path,
    wave,
)

_PROACTIVE_AUDIO_DIR = Path(__file__).resolve().parent.parent.parent / "static" / "proactive_audio"

_PROACTIVE_AUDIO_CACHE: Dict[str, bytes] = {}

def _load_proactive_audio(filename: str) -> bytes:
    """Load a proactive prompt WAV file as raw PCM16 bytes (cached).

    Validates that the file is PCM16 mono 16 kHz before caching.
    Raises ``ValueError`` on format mismatch, ``FileNotFoundError`` if absent.
    """
    if filename in _PROACTIVE_AUDIO_CACHE:
        return _PROACTIVE_AUDIO_CACHE[filename]
    path = _PROACTIVE_AUDIO_DIR / filename
    with wave.open(str(path), "rb") as wf:
        if wf.getnchannels() != 1 or wf.getsampwidth() != 2 or wf.getframerate() != 16000 or wf.getcomptype() != "NONE":
            raise ValueError(
                f"{filename}: expected PCM16 mono 16kHz, got "
                f"ch={wf.getnchannels()} sw={wf.getsampwidth()} "
                f"rate={wf.getframerate()} comp={wf.getcomptype()}"
            )
        data = wf.readframes(wf.getnframes())
    _PROACTIVE_AUDIO_CACHE[filename] = data
    return data
