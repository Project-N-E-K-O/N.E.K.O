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

"""Streaming TTS + audio-paced subtitles of one visit cat line (visit design §3.6.4, PR-09a)."""

from __future__ import annotations

import asyncio
import itertools

import pytest

from main_routers.visit_router import line_speaker as ls
from utils.visit_wire import (
    encode_msg,
    estimate_speech_ms,
    fit_text_to_wire,
    split_clauses,
    wire_size,
)

VISIT = "visit00000000000000001"
NEUTRAL = "家里人"
FAMILY = ["小明"]
LINE = "今天天气真好。我们去公园吧！你喜欢猫吗？"
CLAUSES = split_clauses(LINE)
EST = [estimate_speech_ms(c) for c in CLAUSES]
_ids = itertools.count(1)


class FakeStream:
    def __init__(self, on_enqueued, finish_result=True):
        self.speech_id = f"sp-{next(_ids)}"
        self.pushed: list[str] = []
        self.push_after_close = 0
        self.finishes = 0
        self.aborts = 0
        self.closed = False
        self.finish_result = finish_result
        self.on_enqueued = on_enqueued

    def push(self, delta):
        if self.closed:
            self.push_after_close += 1
            return False
        self.pushed.append(delta)
        self.on_enqueued(len(delta))
        return True

    def finish(self):
        if self.closed:
            return False
        self.finishes += 1
        return self.finish_result

    def abort(self):
        self.aborts += 1
        self.closed = True
        return True


class Harness:
    def __init__(self, *, voice=True, wu=False, finish_result=True, open_fails=False, voice_state=None):
        self.t = 100.0
        self.pieces: list[ls.ReleasedPiece] = []
        self.results: list[ls.LineResult] = []
        self.cancels = 0
        self.fallbacks = 0
        self.usage: list[dict] = []
        self.streams: list[FakeStream] = []
        self.router = ls.SpeechRouter()
        self.voice = voice_state or ls.VoiceState(enabled=voice)
        self.finish_result = finish_result
        self.open_fails = open_fails
        self.wu = wu
        self.speaker = self.new_line()

    def open_stream(self, on_enqueued):
        if self.open_fails:
            raise RuntimeError("tts not ready")
        stream = FakeStream(on_enqueued, self.finish_result)
        self.streams.append(stream)
        return stream

    def new_line(self, ln="h:1"):
        def cancel():
            self.cancels += 1

        def fallback():
            self.fallbacks += 1

        return ls.LineSpeaker(
            visit_id=VISIT, header=ls.LineHeader(ln=ln, lp=1, ad="gc", rt="", wu=self.wu),
            family_names=FAMILY, neutral_term=NEUTRAL, voice=self.voice, open_stream=self.open_stream,
            router=self.router, clock=lambda: self.t, on_piece=self.pieces.append, on_done=self.results.append,
            on_cancel_llm=cancel, on_tts_fallback=fallback, on_usage=self.usage.append,
        )

    @property
    def stream(self) -> FakeStream:
        return self.streams[-1]

    def at(self, seconds):
        self.t = 100.0 + seconds
        self.speaker.tick()

    def progress(self, played_ms, *, ended=False, final=False):
        sid = self.stream.speech_id
        return self.router.route(sid, played_ms=played_ms, ended=ended, final=final, now=self.t)

    def texts(self):
        return [p.text for p in self.pieces]


def _feed_all(h, text=LINE, step=3):
    for k in range(0, len(text), step):
        h.speaker.feed(text[k:k + step])


# ── 一行一条流 ─────────────────────────────────────────────────────────


def test_one_stream_per_line_every_delta_pushed_finished_once():
    h = Harness()
    _feed_all(h)
    h.speaker.llm_done()
    assert len(h.streams) == 1
    assert "".join(h.stream.pushed) == LINE and len(h.stream.pushed) == -(-len(LINE) // 3)
    assert h.stream.finishes == 1
    assert h.usage[0] == {"tts_requests": 1}
    assert sum(u.get("tts_chars", 0) for u in h.usage) == len(LINE)


def test_tts_speaks_the_raw_text_and_subtitles_are_redacted():
    h = Harness()
    h.speaker.feed("小明今天回来了。他带了鱼！")
    h.speaker.llm_done()
    h.progress(10_000, ended=True, final=True)
    assert "小明" in "".join(h.stream.pushed)
    assert all("小明" not in p for p in h.texts()) and NEUTRAL in "".join(h.texts())
    assert "小明" not in h.results[0].text


def test_release_thresholds_follow_the_spoken_raw_text():
    h = Harness()
    h.speaker = ls.LineSpeaker(
        visit_id=VISIT, header=ls.LineHeader(ln="h:1", lp=1, ad="gc", rt=""), family_names=["亚历山大德罗夫"],
        neutral_term=NEUTRAL, voice=h.voice, open_stream=h.open_stream, router=h.router, clock=lambda: h.t,
        on_piece=h.pieces.append, on_done=h.results.append,
    )
    first, second = "亚历山大德罗夫回来了。", "我们一起吃饭吧！"
    h.speaker.feed(first + second)
    h.speaker.llm_done()
    h.progress(0)
    raw_est = estimate_speech_ms(first)
    assert raw_est > estimate_speech_ms(NEUTRAL + "回来了。")
    h.t += raw_est / 1000 - 0.1
    h.progress(raw_est - 100)            # 按脱敏后的短文本估时会在这里提前放出
    assert len(h.pieces) == 1 and NEUTRAL in h.pieces[0].text
    h.t += 0.1
    h.progress(raw_est)
    assert len(h.pieces) == 2


# ── 按已播音频放出 ─────────────────────────────────────────────────────


def test_progress_releases_each_piece_once_at_its_threshold():
    h = Harness()
    _feed_all(h)
    h.speaker.llm_done()
    h.at(0.1)
    h.progress(0)
    assert h.texts() == CLAUSES[:1]                   # 第 0 片阈值 0：开播即放
    h.at(0.2)
    h.progress(EST[0] - 1)
    assert h.texts() == CLAUSES[:1]
    h.t = 100.0 + EST[0] / 1000 + 0.2
    h.progress(EST[0])
    assert h.texts() == CLAUSES[:2]
    h.progress(EST[0])
    assert h.texts() == CLAUSES[:2]                   # 恰一次
    h.progress(EST[0] + 100, ended=True, final=True)
    assert h.texts() == CLAUSES and h.results[0].tail_ms == 0 and h.results[0].text == LINE
    assert h.router.route("unknown", played_ms=1, ended=False, final=False) is False


def test_old_speech_id_progress_is_ignored_after_the_line_ended():
    h = Harness()
    _feed_all(h)
    h.speaker.llm_done()
    sid = h.stream.speech_id
    h.progress(0, ended=True, final=True)
    assert h.router.route(sid, played_ms=1, ended=False, final=False) is False


def test_generation_ending_first_does_not_close_the_line():
    h = Harness()
    _feed_all(h)
    h.progress(0)
    h.speaker.llm_done()                          # LLM 在第 1 片播放中就结束
    assert h.results == [] and h.texts() == CLAUSES[:1]
    h.t += EST[0] / 1000
    h.progress(EST[0])
    assert h.texts() == CLAUSES[:2] and h.results == []
    h.t += 1
    h.progress(EST[0] + EST[1], ended=True, final=True)
    assert h.results[0].tail_ms == 0 and h.results[0].text == LINE


def test_audio_faster_than_estimate_closes_without_tail():
    h = Harness()
    _feed_all(h)
    h.speaker.llm_done()
    h.progress(0)
    h.t += 0.5
    h.progress(500, ended=True, final=True)       # 第 2 片估时未到就播完了
    assert h.texts() == CLAUSES and h.results[0].tail_ms == 0


def test_emotion_tags_never_reach_tts_even_split_across_deltas():
    h = Harness()
    for delta in ("<ha", "ppy>今天天气真好。", "我们去公园吧！<", "/happy>你喜欢猫吗？"):
        h.speaker.feed(delta)
    h.speaker.llm_done()
    spoken = "".join(h.stream.pushed)
    assert spoken == LINE and "happy" not in spoken
    # 阈值按真正念出的（去掉标签的）原文估时
    assert [p.est_ms for p in h.speaker._pieces] == EST


def test_a_family_name_split_by_a_tag_is_budgeted_as_the_name():
    filler = "谢。" * 5000
    capacity = len(Harness().speaker.feed(filler))
    h = Harness()
    # 「小<happy>明」剥标签后就是「小明」：预算必须按换成「家里人」之后的长度量
    h.speaker.feed(filler[:capacity - 2] + "小<happy>明同学")
    h.progress(0)
    h.progress(10**7, ended=True, final=True)
    result = h.results[0]
    assert "小" not in result.text and "happy" not in result.text
    payload = {"t": "text", "v": 1, "ln": "h:1", "lp": 1, "sp": "c", "ad": "gc", "rt": "", "wu": False,
               "final": True, "txt": result.text, "truncated": True, "i_done": result.pieces,
               "trunc_reason": "wire_size", "tail_ms": 0}
    assert fit_text_to_wire(payload, visit_id=VISIT) == payload


def test_goodbye_cap_does_not_count_tags():
    h = Harness(wu=True)
    h.speaker.feed("<happy>" * 5 + "拜拜啦，下次再来玩！")
    h.speaker.llm_done()
    h.progress(0)
    h.progress(10**7, ended=True, final=True)
    assert h.results[0].text == "拜拜啦，下次再来玩！" and not h.results[0].truncated


def test_a_held_bracket_tail_still_obeys_the_goodbye_cap():
    h = Harness(wu=True)
    h.speaker.feed("好" * 39 + "<abc")            # 末尾「<abc」被当作可能的标签扣着
    h.speaker.llm_done()                          # 收尾放出时同样过告别硬顶
    h.progress(0)
    h.progress(10**7, ended=True, final=True)
    result = h.results[0]
    assert len(result.text) <= 40 and result.truncated and result.trunc_reason == "goodbye_cap"


def test_a_hard_clause_cut_never_splits_a_tag_into_the_subtitles():
    h = Harness()
    # 没有标点的长句在 800 字节处硬切，切点正好落在 <happy> 中间
    line = "谢" * 265 + "<happy>" + "谢" * 60 + "。"
    for k in range(0, len(line), 7):
        h.speaker.feed(line[k:k + 7])
    h.speaker.llm_done()
    h.progress(0)
    h.progress(10**7, ended=True, final=True)
    shown = "".join(h.texts())
    assert "<" not in shown and ">" not in shown and "ppy" not in shown
    assert shown == h.results[0].text == "谢" * 325 + "。"


def test_angle_brackets_that_are_not_tags_are_still_spoken():
    h = Harness()
    for delta in ("我爱你 <", "3 真的。", "a < b 吗？", "看 <https://a.example", "> 吧。"):
        h.speaker.feed(delta)
    h.speaker.llm_done()
    assert "".join(h.stream.pushed) == "我爱你 <3 真的。a < b 吗？看 <https://a.example> 吧。"


def test_an_unclosed_bracket_at_the_end_is_spoken_and_shown():
    h = Harness()
    h.speaker.feed("我爱你 <")
    h.speaker.llm_done()
    h.progress(0)
    h.progress(10**7, ended=True, final=True)
    assert "".join(h.stream.pushed) == "我爱你 <" and h.results[0].text.endswith("<")


def test_emotion_tag_filter_lets_a_long_unclosed_tail_through():
    f = ls.EmotionTagFilter()
    assert f.feed("x <") == "x "
    assert f.feed("b" * 40) == "<" + "b" * 40          # 不可能是标签：不再扣着
    assert f.feed("<sad") == "" and f.flush() == "<sad"


def test_a_non_final_drain_releases_only_what_played():
    h = Harness()
    first_two = CLAUSES[0] + CLAUSES[1]
    h.speaker.feed(first_two)
    h.speaker.feed(CLAUSES[2][:1])                # 第 3 片开了个头，还没成片
    h.progress(0)
    h.t += 1
    h.progress(900, ended=True, final=False)      # 已入队的音频播空，第 2 片可能还在合成
    assert h.texts() == CLAUSES[:1] and h.results == []
    h.speaker.feed(CLAUSES[2][1:])
    h.speaker.llm_done()
    h.t += 0.5
    h.progress(1200)
    h.t += 2
    h.progress(3000, ended=True, final=True)
    assert h.texts() == CLAUSES and h.results[0].text == LINE


def test_pieces_after_a_drain_keep_following_played_ms():
    h = Harness()
    h.speaker.feed(CLAUSES[0] + CLAUSES[1])
    h.speaker.feed(CLAUSES[2][:1])
    h.progress(0)
    h.t += 0.5
    h.progress(500, ended=True, final=False)      # 播空：不提前放第 2 片
    assert h.texts() == CLAUSES[:1]
    h.speaker.feed(CLAUSES[2][1:] + "好")          # 第 3 片成片、随新音频入队
    h.t += 1.0
    h.progress(EST[0])
    assert h.texts() == CLAUSES[:2]                # 第 2 片的音频到点才放
    h.t += 2.0
    h.progress(EST[0] + EST[1])
    assert h.texts() == CLAUSES


def test_stale_non_final_ended_after_finish_does_not_close():
    h = Harness()
    _feed_all(h)
    h.progress(0)
    h.speaker.llm_done()
    h.t += 0.5
    h.progress(500, ended=True, final=False)      # 路上的旧 ended，晚于 finish 才到
    assert h.results == []
    h.t += 0.5
    h.progress(4000, ended=True, final=True)
    assert len(h.results) == 1


def test_drain_just_before_finish_then_silence_closes_by_estimate():
    h = Harness()
    _feed_all(h)
    h.progress(0)
    h.t += 1
    h.progress(1000, ended=True, final=False)
    h.speaker.llm_done()                          # finish 之后再无任何信号
    h.at(1 + 2.9)
    assert h.results == [] and h.stream.aborts == 0
    h.at(1 + 3.0)
    assert h.stream.aborts == 1 and h.speaker.mode == ls.PACED_ESTIMATE
    h.at(1 + 3.0 + sum(EST) / 1000)
    assert h.results and h.results[0].text == LINE


# ── TTS 故障兜底 ───────────────────────────────────────────────────────


def test_no_first_progress_in_4s_aborts_and_falls_back_for_the_visit():
    h = Harness()
    _feed_all(h)
    h.speaker.llm_done()
    h.at(3.9)
    assert h.stream.aborts == 0
    h.at(4.0)
    assert h.stream.aborts == 1 and h.fallbacks == 1 and h.voice.fallen_back
    assert h.texts() == CLAUSES[:1]               # 估时从切换时刻起：第 0 片立即放出
    h.at(4.0 + sum(EST) / 1000)
    assert h.results[0].text == LINE and h.results[0].tail_ms == EST[-1]
    # 迟到的音频进度不再生效
    assert h.progress(100) is False
    # 本场后续各行不再开流
    h.speaker = h.new_line("h:2")
    h.speaker.feed("好的。")
    assert len(h.streams) == 1 and h.speaker.mode == ls.PACED_ESTIMATE
    assert h.fallbacks == 1


def test_fallback_status_is_raised_once_per_visit_even_for_overlapping_lines():
    h = Harness()
    first = h.speaker
    second = h.new_line("h:2")
    first.feed(CLAUSES[0])
    second.feed(CLAUSES[1])
    h.t += 4
    first.tick()
    second.tick()
    assert h.fallbacks == 1 and all(s.aborts == 1 for s in h.streams)


def test_progress_stall_aborts_and_continues_from_last_played():
    h = Harness()
    _feed_all(h)
    h.speaker.llm_done()
    h.progress(0)
    h.t += 0.3
    h.progress(300)
    h.at(0.3 + 2.9)
    assert h.stream.aborts == 0
    h.at(0.3 + 3.0)
    assert h.stream.aborts == 1 and h.speaker.mode == ls.PACED_ESTIMATE
    assert h.texts() == CLAUSES[:1]
    # 第 1 片在 300 + (now - stall) >= EST[0] 时放出
    h.at(0.3 + 3.0 + (EST[0] - 300) / 1000 - 0.01)
    assert h.texts() == CLAUSES[:1]
    h.at(0.3 + 3.0 + (EST[0] - 300) / 1000)
    assert h.texts() == CLAUSES[:2]
    h.at(60)
    assert h.results[0].text == LINE
    assert h.progress(9999, ended=True, final=True) is False


def test_finish_without_worker_aborts_and_paces_by_estimate():
    h = Harness(finish_result=ls.FINISH_NO_WORKER)
    _feed_all(h)
    h.speaker.llm_done()
    assert h.stream.aborts == 1 and h.speaker.mode == ls.PACED_ESTIMATE
    h.at(60)
    assert h.results[0].text == LINE


def test_tts_not_ready_falls_back_without_a_stream():
    h = Harness(open_fails=True)
    _feed_all(h)
    h.speaker.llm_done()
    assert h.streams == [] and h.fallbacks == 1
    h.at(60)
    assert h.results[0].text == LINE


def test_deltas_after_an_abort_only_reach_the_subtitles():
    h = Harness()
    h.speaker.feed(CLAUSES[0])
    h.progress(0)
    h.at(3.0)                                     # 进度中断 → abort
    pushed = list(h.stream.pushed)
    chars = sum(u.get("tts_chars", 0) for u in h.usage)
    h.speaker.feed(CLAUSES[1])
    h.speaker.feed(CLAUSES[2])
    h.speaker.llm_done()
    assert h.stream.pushed == pushed and h.stream.finishes == 0 and h.stream.push_after_close == 0
    assert sum(u.get("tts_chars", 0) for u in h.usage) == chars
    h.at(60)
    assert h.results[0].text == LINE


def test_voice_off_never_opens_a_stream_and_paces_by_estimate():
    h = Harness(voice=False)
    _feed_all(h)
    assert h.texts() == CLAUSES[:1]
    h.speaker.llm_done()
    h.at(EST[0] / 1000 - 0.01)
    assert h.texts() == CLAUSES[:1]
    h.at(EST[0] / 1000)
    assert h.texts() == CLAUSES[:2]
    h.at((EST[0] + EST[1]) / 1000)
    assert h.results[0].text == LINE and h.results[0].tail_ms == EST[-1]
    assert h.streams == [] and not h.usage
    assert {p.paced for p in h.pieces} == {ls.PACED_ESTIMATE}


# ── 打断 ───────────────────────────────────────────────────────────────


def test_interrupt_stops_now_and_ignores_the_cleared_pipeline_ended():
    h = Harness()
    _feed_all(h, LINE + "好")                     # 第 3 片已生成、尚未放出
    h.progress(0)
    h.t += EST[0] / 1000
    h.progress(EST[0])                            # 第 2 片在播
    result = h.speaker.interrupt("human_interrupt")
    assert h.stream.aborts == 1 and h.cancels == 1
    assert result.truncated and result.trunc_reason == "human_interrupt"
    assert result.text == CLAUSES[0] + CLAUSES[1] and result.pieces == 2
    # 管线被清后前端回报的 ended 不是播完：未念出的不再放出
    assert h.progress(EST[0] + 10, ended=True, final=True) is False
    assert h.texts() == CLAUSES[:2] and len(h.results) == 1
    assert h.speaker.interrupt("human_interrupt") is None


# ── 入口截断 ───────────────────────────────────────────────────────────


def test_goodbye_line_is_capped_at_40_chars_everywhere():
    h = Harness(wu=True)
    long = "这一趟玩得真开心，谢谢你们的招待，下次再来找你们玩，" * 10
    for k in range(0, len(long), 7):
        h.speaker.feed(long[k:k + 7])
    h.speaker.llm_done()
    h.progress(0)
    h.progress(99_999, ended=True, final=True)
    pushed = "".join(h.stream.pushed)
    assert len(pushed) == 40 and "".join(h.texts()) == pushed == h.results[0].text
    assert h.results[0].trunc_reason == "goodbye_cap" and h.results[0].truncated
    assert h.cancels == 1 and h.stream.finishes == 1


def test_a_cut_in_the_middle_of_a_family_name_does_not_leak_its_prefix():
    h = Harness(wu=True)
    h.speaker = ls.LineSpeaker(
        visit_id=VISIT, header=ls.LineHeader(ln="h:1", lp=1, ad="gc", rt="", wu=True),
        family_names=["小明同学"], neutral_term=NEUTRAL, voice=h.voice, open_stream=h.open_stream,
        router=h.router, clock=lambda: h.t, on_piece=h.pieces.append, on_done=h.results.append,
    )
    h.speaker.feed("谢" * 38 + "小明同学要记得来玩哦")         # 第 40 字截在名字中间
    h.speaker.llm_done()
    h.progress(0)
    h.progress(99_999, ended=True, final=True)
    text = h.results[0].text
    assert h.results[0].trunc_reason == "goodbye_cap"
    assert "小明" not in text and "小" not in text.replace(NEUTRAL, "") and text.endswith(NEUTRAL)
    assert "".join(h.texts()) == text


def test_a_wire_cut_inside_a_family_name_still_fits_the_wire():
    filler = "谢。" * 5000
    capacity = len(Harness().speaker.feed(filler))
    h = Harness()
    # 只剩 1 字的余量时来了「小明」：「小」按原样量放得下，收尾换成更长的「家里人」就超了
    accepted = h.speaker.feed(filler[:capacity - 1] + "小明同学")
    assert accepted == filler[:capacity - 1]
    h.progress(0)
    h.progress(10**7, ended=True, final=True)
    result = h.results[0]
    assert result.trunc_reason == "wire_size" and "小" not in result.text
    payload = {"t": "text", "v": 1, "ln": "h:1", "lp": 1, "sp": "c", "ad": "gc", "rt": "", "wu": False,
               "final": True, "txt": result.text, "truncated": True, "i_done": result.pieces,
               "trunc_reason": "wire_size", "tail_ms": 0}
    assert fit_text_to_wire(payload, visit_id=VISIT) == payload          # 兜底截断未触发


def test_a_stream_rejecting_its_first_push_falls_back_for_the_visit():
    h = Harness()
    opener = h.open_stream

    def closed_at_once(on_enqueued):
        stream = opener(on_enqueued)
        stream.closed = True                    # worker 一开流就退出了
        return stream

    h.open_stream = closed_at_once
    h.speaker = h.new_line("h:2")
    h.speaker.feed(CLAUSES[0])
    assert h.speaker.mode == ls.PACED_ESTIMATE and h.voice.fallen_back and h.fallbacks == 1
    h.speaker = h.new_line("h:3")
    h.speaker.feed(CLAUSES[1])
    assert len(h.streams) == 1                  # 本场后续各行不再开流


def test_frozen_playback_reporting_the_same_position_still_stalls():
    h = Harness()
    _feed_all(h)
    h.progress(0)
    h.t += 1
    h.progress(1000)
    for k in range(1, 7):                       # 播放冻住：页面照报同一个 played_ms
        h.at(1 + 0.5 * k)
        h.progress(1000)
    assert h.stream.aborts == 1 and h.speaker.mode == ls.PACED_ESTIMATE


def test_stream_closed_under_us_switches_to_estimate_at_once():
    h = Harness()
    h.speaker.feed(CLAUSES[0])
    h.progress(0)
    h.stream.closed = True                      # worker 退出把流关了（不是本行 abort 的）
    h.speaker.feed(CLAUSES[1])
    assert h.speaker.mode == ls.PACED_ESTIMATE
    h.speaker.feed(CLAUSES[2])
    h.speaker.llm_done()
    h.at(60)
    assert h.results[0].text == LINE


def test_new_audio_after_a_drain_restarts_the_stall_clock():
    h = Harness()
    h.speaker.feed(CLAUSES[0] + CLAUSES[1][:1])
    h.progress(0)
    h.t += 1
    h.progress(1000, ended=True, final=False)   # 播空：没有停滞计时
    assert h.speaker.next_deadline() is None
    h.t += 5
    h.speaker.feed(CLAUSES[1][1:])              # 新文本入队，之后 TTS 再无任何回报
    assert h.speaker.next_deadline() == pytest.approx(h.t + 3)
    h.at(6 + 3)
    assert h.stream.aborts == 1 and h.speaker.mode == ls.PACED_ESTIMATE


def _backslash_line() -> str:
    return ("\\\"" * 40 + "。") * 60


def test_wire_cut_keeps_tts_subtitles_and_final_identical():
    h = Harness()
    text = _backslash_line()
    for k in range(0, len(text), 50):
        h.speaker.feed(text[k:k + 50])
    pushed = "".join(h.stream.pushed)
    assert len(pushed) < len(text) and h.cancels == 1 and h.stream.finishes == 1
    h.speaker.feed("more text after the cut")
    assert "".join(h.stream.pushed) == pushed and h.stream.finishes == 1
    h.progress(0)
    h.progress(10**7, ended=True, final=True)
    result = h.results[0]
    assert "".join(h.texts()) == result.text == pushed
    assert result.truncated and result.trunc_reason == "wire_size"
    payload = {"t": "text", "v": 1, "ln": "h:1", "lp": 1, "sp": "c", "ad": "gc", "rt": "", "wu": False,
               "final": True, "txt": result.text, "truncated": True, "i_done": result.pieces,
               "trunc_reason": "wire_size", "tail_ms": 0}
    assert fit_text_to_wire(payload, visit_id=VISIT) == payload          # 兜底截断未触发
    body = dict(payload, seq=2**32 - 1)
    assert wire_size(encode_msg(body), visit_id=VISIT)[0] <= 8


def test_a_cut_delta_never_reaches_the_splitter_beyond_its_accepted_part():
    h = Harness()
    text = _backslash_line()
    h.speaker.feed(text)                          # 一大段一次进来，被截
    h.speaker.llm_done()
    h.progress(0)
    h.progress(10**7, ended=True, final=True)
    assert "".join(h.texts()) == "".join(h.stream.pushed) == h.results[0].text


def test_whole_sentence_mode_cuts_at_entry_too():
    # 整句模式（不发 line_delta）也是同一个入口：截断与 final 不变
    h = Harness(voice=False)
    text = _backslash_line()
    h.speaker.feed(text)
    h.at(10**4)
    assert h.results[0].trunc_reason == "wire_size" and len(h.results[0].text) < len(text)


# ── 路由表 ─────────────────────────────────────────────────────────────


def test_router_holds_no_speech_after_the_visit_stops():
    h = Harness()
    h.speaker.feed(CLAUSES[0])
    assert len(h.router) == 1
    for speaker in h.router.clear():
        speaker.interrupt("visit_end")
    assert len(h.router) == 0 and h.stream.aborts == 1


# ── 驱动循环 ───────────────────────────────────────────────────────────


async def test_drive_ticks_until_the_line_is_done():
    loop = asyncio.get_running_loop()
    results = []
    speaker = ls.LineSpeaker(
        visit_id=VISIT, header=ls.LineHeader(ln="h:1", lp=1, ad="gc", rt=""), family_names=[],
        neutral_term=NEUTRAL, voice=ls.VoiceState(enabled=True), open_stream=FakeStream,
        router=ls.SpeechRouter(), clock=loop.time, on_done=results.append, start_timeout_s=0.05,
    )
    task = asyncio.create_task(ls.drive(speaker, clock=loop.time))
    speaker.feed("好。")
    speaker.llm_done()
    result = await asyncio.wait_for(task, 5)
    assert result is results[0] and result.text == "好。"


def test_merged_pieces_keep_wire_indices_contiguous(tmp_path):
    from main_logic.visit.outbox import VisitOutbox

    h = Harness()
    outbox = VisitOutbox(VISIT, "host", clock=lambda: h.t, spool_dir=tmp_path, peer_present=True)
    header = h.speaker.header

    def send_piece(piece):
        msg = {"t": "line_delta", "v": 1, "ln": header.ln, "lp": header.lp, "txt": piece.text}
        if piece.index == 0:
            msg.update(sp=header.sp, ad=header.ad, rt=header.rt, wu=header.wu)
        outbox.send(msg, now=h.t, final_piece=piece.last)

    h.speaker._cb.on_piece = send_piece
    five = "一二三。四五六。七八九。十一二。三四五。"
    h.speaker.feed(five)
    h.speaker.llm_done()
    h.progress(0)
    frames = outbox.due(h.t)
    for step in range(1, 6):                       # 五片在 250 ms 内陆续放出
        h.t += 0.04
        h.progress(10**5 if step == 5 else 40 * step, ended=step == 5, final=step == 5)
        frames += outbox.due(h.t)
    result = h.results[0]
    outbox.send({"t": "text", "v": 1, "ln": header.ln, "lp": header.lp, "sp": "c", "ad": header.ad,
                 "rt": header.rt, "wu": False, "final": True, "txt": result.text, "truncated": False,
                 "i_done": result.pieces, "tail_ms": 0}, now=h.t)
    for _ in range(20):
        h.t += 0.3
        frames += outbox.due(h.t)
    deltas = [f.payload for f in frames if f.t == "line_delta"]
    texts = [f.payload for f in frames if f.t == "text"]
    assert len(deltas) < 5                                 # 发生了合并
    assert [d["i"] for d in deltas] == list(range(len(deltas)))
    assert "".join(d["txt"] for d in deltas) == result.text == five
    assert texts[0]["i_done"] == len(deltas)


@pytest.mark.parametrize("voice", [True, False])
def test_released_pieces_join_to_the_final_text(voice):
    h = Harness(voice=voice)
    _feed_all(h, LINE * 3, step=5)
    h.speaker.llm_done()
    if voice:
        h.progress(0)
        h.progress(10**6, ended=True, final=True)
    else:
        h.at(10**4)
    assert "".join(h.texts()) == h.results[0].text == LINE * 3
    assert [p.index for p in h.pieces] == list(range(len(h.pieces)))
    assert h.pieces[-1].last
