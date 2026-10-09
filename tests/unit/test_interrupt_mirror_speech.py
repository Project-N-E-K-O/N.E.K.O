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

"""``interrupt_mirror_speech`` is a behavior-preserving extraction (visit design §5 PR-09b).

The call sequence of ``mirror_assistant_speech(interrupt_audio=True)`` is
snapshotted; the same test runs unchanged against the code before the
extraction (it only drives ``mirror_assistant_speech``).
"""

from __future__ import annotations

import pytest

import main_logic.core as core_module
from tests.unit.test_core_game_route_memory_contract import _make_manager, _soccer_mirror_meta

LLM = core_module.LLMSessionManager


class _RealtimeSession(core_module.OmniRealtimeClient):
    """Only ``isinstance`` and ``cancel_response`` matter here; built without the real initializer."""

    _calls: list

    async def cancel_response(self):
        self._calls.append(("cancel_response",))


def _realtime_session(calls):
    session = object.__new__(_RealtimeSession)
    session._calls = calls
    return session


def _spy(mgr, *, realtime: bool):
    calls: list[tuple] = []

    class Resampler:
        def clear(self):
            calls.append(("resampler_clear",))

    async def clear_pipeline():
        calls.append(("clear_tts_pipeline",))

    def release(speech_id):
        calls.append(("release_gain", speech_id))

    async def activity(speech_id):
        calls.append(("user_activity", speech_id))

    mgr.audio_resampler = Resampler()
    mgr._clear_tts_pipeline = clear_pipeline
    mgr.release_speech_playback_gain = release
    mgr.send_user_activity = activity
    if realtime:
        mgr.session = _realtime_session(calls)
    return calls


async def _mirror(mgr, *, interrupt_audio: bool):
    return await LLM.mirror_assistant_speech(
        mgr, "先听我说完", metadata=_soccer_mirror_meta({"kind": "user-text"}), request_id="r",
        mirror_text=False, emit_turn_end_after=False, interrupt_audio=interrupt_audio,
    )


@pytest.mark.parametrize("realtime", [False, True])
async def test_interrupt_prelude_sequence_is_unchanged(realtime):
    mgr = _make_manager()
    calls = _spy(mgr, realtime=realtime)
    result = await _mirror(mgr, interrupt_audio=True)
    expected = [("resampler_clear",), ("clear_tts_pipeline",), ("release_gain", "old-speech")]
    if realtime:
        expected.append(("cancel_response",))
    expected.append(("user_activity", "old-speech"))
    assert calls == expected
    # 前奏之后照常轮换 speech id
    assert result["speech_id"] != "old-speech" and mgr.current_speech_id == result["speech_id"]


async def test_no_interrupt_touches_nothing():
    mgr = _make_manager()
    calls = _spy(mgr, realtime=True)
    await _mirror(mgr, interrupt_audio=False)
    assert calls == []


async def test_public_method_runs_the_same_prelude():
    if not hasattr(LLM, "interrupt_mirror_speech"):
        pytest.skip("before the extraction")
    mgr = _make_manager()
    calls = _spy(mgr, realtime=True)
    await LLM.interrupt_mirror_speech(mgr)
    assert calls == [("resampler_clear",), ("clear_tts_pipeline",), ("release_gain", "old-speech"),
                     ("cancel_response",), ("user_activity", "old-speech")]
    assert mgr.current_speech_id == "old-speech"      # 只打断，不轮换
