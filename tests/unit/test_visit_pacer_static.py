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

"""Static contracts of the visit pacer / text mouth driver (design §5 PR-13 and the §5 general gates).

* nothing under ``static/visit/`` (outside a ``transport`` directory)
  schedules ``requestAnimationFrame`` or opens a ``WebSocket``, and nothing
  there listens to ``visibilitychange`` (§5 general static gates ①⑤);
* the pacer listens to the playback-state event, the per-speech-id stream
  closed event and the bind event, and never to the turn-keyed
  ``neko-assistant-speech-end``;
* ``app-websocket.js`` dispatches ``neko-audio-stream-closed{speechId}`` in
  its ``audio_done`` branch before handing the id to the player, and
  re-dispatches ``visit_*`` frames as ``neko-visit-frame`` without returning;
* the text mouth driver keeps the RMS exclusion and paces with
  ``requestPacedFrame``, and no visit script touches ``speakerGainNode``
  (the removed "mute locally but keep RMS" switch);
* only ``index.html`` loads the pacer and the mouth driver.
"""

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
VISIT_DIR = PROJECT_ROOT / "static" / "visit"
PACER = VISIT_DIR / "visit-pacer.js"
MOUTH = VISIT_DIR / "text-mouth-driver.js"
WEBSOCKET = PROJECT_ROOT / "static" / "app" / "app-websocket.js"
INDEX = PROJECT_ROOT / "templates" / "index.html"
CHAT = PROJECT_ROOT / "templates" / "chat.html"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _strip_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"(?m)//.*$", "", src)


def _visit_scripts() -> list[Path]:
    return [p for p in VISIT_DIR.rglob("*.js") if "transport" not in p.relative_to(VISIT_DIR).parts]


def test_visit_scripts_exist():
    names = {p.name for p in _visit_scripts()}
    assert {"visit-pacer.js", "text-mouth-driver.js"} <= names


def test_no_raf_websocket_or_visibilitychange_under_static_visit():
    for path in _visit_scripts():
        code = _strip_comments(_read(path))
        assert "requestAnimationFrame(" not in code, path
        assert "new WebSocket(" not in code, path
    for path in VISIT_DIR.rglob("*.js"):
        assert "visibilitychange" not in _read(path), path


def test_pacer_listens_by_speech_id_not_by_turn():
    code = _strip_comments(_read(PACER))
    for event in ("neko-speech-playback-state", "neko-audio-stream-closed", "neko-visit-bound", "neko-visit-frame"):
        assert f"addEventListener('{event}'" in code, event
    assert "neko-assistant-speech-end" not in code
    assert "'chunk_scheduled'" in code
    assert "pendingAudioCountForSpeech" in code
    assert "action: 'visit_speech_progress'" in code


def test_audio_done_branch_dispatches_stream_closed_before_the_player():
    src = _read(WEBSOCKET)
    branch = src[src.index("response.type === 'audio_done'"):]
    branch = branch[:branch.index("} else if (response.type ===")]
    dispatch = branch.index("new CustomEvent('neko-audio-stream-closed'")
    assert "speechId: response.speech_id" in branch[dispatch:dispatch + 200]
    assert dispatch < branch.index("noteAssistantAudioStreamClosed(response.speech_id)")


def test_visit_frames_are_redispatched_without_returning():
    src = _read(WEBSOCKET)
    start = src.index("response.type.indexOf('visit_') === 0")
    block = src[start:src.index("} catch", start)]
    assert "detail: response" in block
    assert "return" not in block


def test_mouth_driver_is_exclusive_with_rms_and_frame_paced():
    code = _strip_comments(_read(MOUTH))
    assert "lipSyncActive === true" in code
    assert "requestPacedFrame(" in code
    assert "paced !== 'estimate'" in code
    assert "setMouth(" in code


def test_no_visit_script_touches_the_speaker_gain():
    for path in VISIT_DIR.rglob("*.js"):
        assert "speakerGainNode" not in _read(path), path


def test_only_index_loads_pacer_and_mouth_driver():
    index = _read(INDEX)
    chat = _read(CHAT)
    for name in ("visit-pacer.js", "text-mouth-driver.js"):
        assert f'src="/static/visit/{name}' in index, name
        assert name not in chat, name
    # Load after app-websocket.js: dispatch and listeners share one page.
    assert index.index("/static/app/app-websocket.js") < index.index("/static/visit/visit-pacer.js")
