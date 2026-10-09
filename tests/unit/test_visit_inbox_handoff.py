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

"""When parked plugin callbacks are handed back after a visit (``inbox_handoff``)."""

from __future__ import annotations

import pytest

from config.visit_settings import VISIT_INBOX_HANDOFF_ABS_MAX_S, VISIT_INBOX_HANDOFF_MAX_S
from main_routers.visit_router import inbox_handoff as ih


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture(autouse=True)
def _clean():
    ih._reset_for_tests()
    yield
    ih._reset_for_tests()


def _voiced(clock, *, est=(3000, 4000)):
    h = ih.InboxHandoff("v" * 22, finalize_at=clock(), clock=clock)
    h.attach_speech("ritual", "sp-r")
    h.mark_queued("ritual", est[0])
    h.attach_speech("debrief", "sp-d")
    h.mark_queued("debrief", est[1])
    return h


def test_both_segments_must_end():
    clock = Clock()
    h = _voiced(clock)
    assert not h.due()
    assert ih.route_progress("sp-r", ended=True, final=True)
    assert not h.due()
    assert ih.route_progress("sp-d", ended=True, final=True)
    assert h.due()
    h.close()
    assert ih.pending_speech_ids() == 0
    assert not ih.route_progress("sp-r", ended=True, final=True)


def test_an_end_signal_before_the_other_segment_is_registered_is_kept():
    clock = Clock()
    h = ih.InboxHandoff("v" * 22, finalize_at=clock(), clock=clock)
    h.attach_speech("ritual", "sp-r")
    h.mark_queued("ritual", 2000)
    ih.route_progress("sp-r", ended=True, final=True)          # 仪式句很快播完，简述还在生成
    assert not h.due()
    clock.now += 8
    h.attach_speech("debrief", "sp-d")
    h.mark_queued("debrief", 2000)
    ih.route_progress("sp-d", ended=True, final=True)
    assert h.due()


def test_a_skipped_voiced_segment_does_not_hold_the_text_fallback():
    clock = Clock()
    h = ih.InboxHandoff("v" * 22, finalize_at=clock(), clock=clock)
    h.attach_speech("ritual", "sp-r")                # 仪式句开了 TTS 流
    h.mark_queued("ritual", 2000)
    h.skip("ritual")                                 # 推流被拒：这段跳过，简述改走文字
    h.mark_queued("debrief", 3000)                   # 简述只上屏
    clock.now += 5.1                                 # 过了文字的估时（远不到 VISIT_INBOX_HANDOFF_MAX_S）
    assert clock.now - 100.0 < VISIT_INBOX_HANDOFF_MAX_S
    assert h.due()                                   # 按文字估时交还，不为那段已结束的语音等满上限


def test_twenty_second_cap_counts_from_the_second_segment_and_respects_playback():
    clock = Clock()
    h = ih.InboxHandoff("v" * 22, finalize_at=clock(), clock=clock)
    h.attach_speech("ritual", "sp-r")
    h.mark_queued("ritual", 2000)
    clock.now += 8                                   # 简述生成 8 s：不计入
    h.attach_speech("debrief", "sp-d")
    h.mark_queued("debrief", 2000)
    clock.now += VISIT_INBOX_HANDOFF_MAX_S - 1
    assert not h.due()
    clock.now += 2
    assert h.due()                                   # 没有任何在播的进度
    ih.route_progress("sp-d", ended=False, final=False)           # 还在播：不交还
    assert not h.due()


def test_absolute_deadline_follows_the_estimates_and_is_capped():
    clock = Clock()
    h = _voiced(clock, est=(6000, 9000))
    deadline = h.absolute_deadline()
    assert deadline == max(100 + 30, 100 + 15 + 10)
    long = ih.InboxHandoff("w" * 22, finalize_at=clock(), clock=clock)
    long.attach_speech("ritual", "sp-a")
    long.mark_queued("ritual", 12000)
    long.attach_speech("debrief", "sp-b")
    long.mark_queued("debrief", 12000)
    clock.now += 100
    long.mark_queued("ritual", 12000)
    assert long.absolute_deadline() <= 100 + VISIT_INBOX_HANDOFF_ABS_MAX_S


def test_absolute_deadline_hands_back_even_while_progress_keeps_coming():
    clock = Clock()
    h = _voiced(clock)
    while clock.now < h.absolute_deadline():
        ih.route_progress("sp-r", ended=False, final=False)
        ih.route_progress("sp-d", ended=False, final=False)
        assert not h.due() or clock.now >= h.absolute_deadline()
        clock.now += 1
    assert h.due()


def test_voice_off_uses_the_estimates():
    clock = Clock()
    h = ih.InboxHandoff("v" * 22, finalize_at=clock(), clock=clock)
    h.mark_queued("ritual", 3000)
    h.mark_queued("debrief", 4000)
    clock.now += 6.9
    assert not h.due()
    clock.now += 0.2
    assert h.due()


def test_interruption_by_the_family_counts_as_ended():
    clock = Clock()
    h = _voiced(clock)
    h.interrupted_since(last_input=50.0, input_stamps={"ritual": 60.0, "debrief": 60.0})
    assert not h.due()
    h.interrupted_since(last_input=70.0, input_stamps={"ritual": 60.0, "debrief": 60.0})
    assert h.due()


def test_skipped_segments_count_as_done():
    clock = Clock()
    h = ih.InboxHandoff("v" * 22, finalize_at=clock(), clock=clock)
    h.skip("ritual")
    h.attach_speech("debrief", "sp-d")
    h.mark_queued("debrief", 2000)
    assert not h.due()
    ih.route_progress("sp-d", ended=True, final=True)
    assert h.due()


def test_a_drained_queue_is_not_the_end_of_the_segment():
    # ended{final:false} 只是音频队列暂时排空、之后还会接着播：不算这一段结束
    clock = Clock()
    h = _voiced(clock)
    assert ih.route_progress("sp-r", ended=True, final=False)
    assert ih.route_progress("sp-d", ended=True, final=False)
    assert not h.due()
    clock.now += VISIT_INBOX_HANDOFF_MAX_S + 1
    ih.route_progress("sp-d", ended=False, final=False)  # 接着播：20 s 兜底也不交还
    assert not h.due()
    ih.route_progress("sp-r", ended=True, final=True)
    ih.route_progress("sp-d", ended=True, final=True)
    assert h.due()


def test_abandon_skips_only_the_segments_not_queued_yet():
    clock = Clock()
    h = ih.InboxHandoff("v" * 22, finalize_at=clock(), clock=clock)
    h.attach_speech("ritual", "sp-r")
    h.mark_queued("ritual", 2000)
    h.abandon()                                      # 收尾中途失败：简述不会再来
    assert not h.due()                               # 仪式句还没播完
    ih.route_progress("sp-r", ended=True, final=True)
    assert h.due()
