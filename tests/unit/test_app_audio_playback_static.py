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

"""The ``chunk_scheduled`` playback-state patch only gained fields (design §5 PR-13).

The visit pacer reads ``chunkStartAudioTime`` / ``chunkDurationSec``; every
field the patch carried before stays, and the set of playback-state reasons
the player publishes is unchanged.
"""

import re
from pathlib import Path

PLAYBACK = Path(__file__).resolve().parents[2] / "static" / "app" / "app-audio-playback.js"

ORIGINAL_FIELDS = {
    "active", "speechId", "turnId", "playbackTurnId", "playbackStartAudioTime",
    "playbackEndAudioTime", "scheduledEndAudioTime",
}
NEW_FIELDS = {"chunkStartAudioTime", "chunkDurationSec"}
REASONS = {
    "heartbeat", "speech_cancel", "clear_audio_queue", "clear_audio_queue_without_decoder_reset",
    "source_ended", "chunk_scheduled",
}


def _src() -> str:
    return PLAYBACK.read_text(encoding="utf-8")


def test_chunk_scheduled_patch_fields_are_original_plus_two():
    src = _src()
    start = src.index("publishSpeechPlaybackState('chunk_scheduled', {")
    body = src[start:src.index("});", start)]
    body = re.sub(r"(?m)//.*$", "", body)
    keys = set(re.findall(r"^\s*(\w+)\s*:", body, flags=re.M))
    assert keys == ORIGINAL_FIELDS | NEW_FIELDS
    assert "chunkStartAudioTime: scheduledStartTime" in body
    assert "chunkDurationSec: nextBuffer.duration" in body


def test_published_reasons_are_unchanged():
    assert set(re.findall(r"publishSpeechPlaybackState\('(\w+)'", _src())) == REASONS


def test_pending_count_query_is_exported():
    src = _src()
    assert "mod.pendingAudioCountForSpeech = pendingAudioCountForSpeech;" in src


def _fn_body(src: str, header: str) -> str:
    start = src.index(header)
    depth = 0
    for i in range(src.index("{", start), len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    raise AssertionError("unbalanced braces")


def test_visit_voice_tap_reads_the_analyser_output_before_speaker_volume():
    # OD-15 v4: the peer hears this catgirl's TTS (after per-speech gain) regardless of the local speaker volume.
    src = _src()
    body = _fn_body(src, "async function openAssistantVoiceTap()")
    assert "initializeGlobalAnalyser();" in body
    assert "var analyser = S.globalAnalyser;" in body
    assert "destination.channelCount = 1;" in body
    assert "analyser.connect(destination);" in body
    assert "speakerGainNode" not in body
    assert "analyser.disconnect(destination);" in body
    assert "track.stop()" in body
    assert "getUserMedia" not in src


def test_visit_peer_voice_joins_after_the_analyser():
    # The peer voice follows speaker volume / output device, never drives lip sync and never loops back into the tap.
    body = _fn_body(_src(), "async function playExternalVoiceTrack(track)")
    assert "source.connect(S.speakerGainNode || context.destination);" in body
    assert "globalAnalyser" not in body.replace("initializeGlobalAnalyser();", "")
    assert "source.disconnect();" in body


def test_visit_voice_entry_points_are_exported():
    src = _src()
    assert "mod.openAssistantVoiceTap = openAssistantVoiceTap;" in src
    assert "mod.playExternalVoiceTrack = playExternalVoiceTrack;" in src
