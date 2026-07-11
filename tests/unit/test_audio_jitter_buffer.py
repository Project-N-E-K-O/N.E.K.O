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

"""Shared TTS jitter buffer semantics (qwen / step / other streaming workers)."""

import queue

import pytest

from main_logic.tts_client._infra import (
    AudioJitterBuffer,
    TTS_SHUTDOWN_SENTINEL,
    _AudioQueueProxy,
    _non_bistream_tts_main_loop,
    make_audio_jitter_buffer,
    _TTS_OUTPUT_BYTES_PER_SECOND,
)


class _FakeQueue:
    def __init__(self):
        self.items = []

    def put(self, item):
        self.items.append(item)


def test_initial_head_start_holds_back_until_threshold():
    q = _FakeQueue()
    buf = AudioJitterBuffer(q, initial_buffer_bytes=10, steady_buffer_bytes=4)
    buf.reset("speech-1")
    # 首包累计不足 initial 阈值时不放行（盖住开头 inter-chunk gap）。
    buf.append(b"abc")
    buf.append(b"def")
    assert q.items == []
    # 攒够 initial 后一次性放出累计领先量。
    buf.append(b"ghij")
    assert q.items == [("__audio__", "speech-1", b"abcdefghij")]


def test_steady_flush_after_started():
    q = _FakeQueue()
    buf = AudioJitterBuffer(q, initial_buffer_bytes=4, steady_buffer_bytes=4)
    buf.reset("speech-1")
    buf.append(b"aaaa")  # 越过 initial，放行首包
    assert q.items == [("__audio__", "speech-1", b"aaaa")]
    buf.append(b"bb")  # 不足 steady，缓冲
    assert q.items == [("__audio__", "speech-1", b"aaaa")]
    buf.append(b"cc")  # 凑够 steady，放行
    assert q.items == [
        ("__audio__", "speech-1", b"aaaa"),
        ("__audio__", "speech-1", b"bbcc"),
    ]


def test_flush_drains_remainder_below_threshold():
    q = _FakeQueue()
    buf = AudioJitterBuffer(q, initial_buffer_bytes=4, steady_buffer_bytes=4)
    buf.reset("speech-1")
    buf.append(b"aaaa")
    buf.append(b"z")  # 尾音不足 steady
    buf.flush()  # response.done 时强制放出
    assert q.items == [
        ("__audio__", "speech-1", b"aaaa"),
        ("__audio__", "speech-1", b"z"),
    ]


def test_flush_during_head_start_marks_started():
    # 短句不足 initial：靠终结 flush 首次放行后，stray delta 应走 steady 而非
    # 重新 head-start（否则会被下一轮 reset 静默丢弃）。
    q = _FakeQueue()
    buf = AudioJitterBuffer(q, initial_buffer_bytes=10, steady_buffer_bytes=2)
    buf.reset("speech-1")
    buf.append(b"ab")  # 不足 initial，缓冲
    assert q.items == []
    buf.flush()  # response.done：放行短句
    assert q.items == [("__audio__", "speech-1", b"ab")]
    assert buf.started is True
    # 晚到的 stray delta 直接走 steady（凑够 2 字节即放行），不再被 hold 400ms
    buf.append(b"cd")
    assert q.items == [
        ("__audio__", "speech-1", b"ab"),
        ("__audio__", "speech-1", b"cd"),
    ]


def test_missing_speech_id_drops_audio_instead_of_guessing_current_turn():
    q = _FakeQueue()
    buf = AudioJitterBuffer(q, initial_buffer_bytes=1, steady_buffer_bytes=1)
    buf.append(b"old-audio")
    assert q.items == []


def test_flush_on_empty_buffer_does_not_mark_started():
    # 空 flush 没放行任何音频，不应改变 head-start 状态。
    q = _FakeQueue()
    buf = AudioJitterBuffer(q, initial_buffer_bytes=10, steady_buffer_bytes=2)
    buf.flush()
    assert buf.started is False


def test_reset_discards_unsent_buffer_and_restarts_head_start():
    q = _FakeQueue()
    buf = AudioJitterBuffer(q, initial_buffer_bytes=10, steady_buffer_bytes=4)
    buf.append(b"abc")  # 缓冲中、未放出
    buf.reset()  # 打断 / 新轮次
    assert q.items == []
    # reset 后重新进入 head-start 状态：又要攒够 initial 才放行。
    buf.append(b"xyz")
    assert q.items == []


def test_empty_append_is_noop():
    q = _FakeQueue()
    buf = AudioJitterBuffer(q, initial_buffer_bytes=4, steady_buffer_bytes=4)
    buf.append(b"")
    buf.append(None)
    assert q.items == []


def test_factory_defaults_to_400_200_ms():
    q = _FakeQueue()
    buf = make_audio_jitter_buffer(q)
    assert buf._initial_buffer_bytes == int(400 / 1000 * _TTS_OUTPUT_BYTES_PER_SECOND)
    assert buf._steady_buffer_bytes == int(200 / 1000 * _TTS_OUTPUT_BYTES_PER_SECOND)


def test_factory_generic_env_override(monkeypatch):
    monkeypatch.setenv("NEKO_TTS_JITTER_INITIAL_MS", "600")
    monkeypatch.setenv("NEKO_TTS_JITTER_STEADY_MS", "100")
    q = _FakeQueue()
    buf = make_audio_jitter_buffer(q)
    assert buf._initial_buffer_bytes == int(600 / 1000 * _TTS_OUTPUT_BYTES_PER_SECOND)
    assert buf._steady_buffer_bytes == int(100 / 1000 * _TTS_OUTPUT_BYTES_PER_SECOND)


def test_factory_legacy_env_fallback(monkeypatch):
    # 旧 NEKO_QWEN_TTS_* 覆盖仍生效（generic 未设时回退）。
    monkeypatch.delenv("NEKO_TTS_JITTER_INITIAL_MS", raising=False)
    monkeypatch.setenv("NEKO_QWEN_TTS_INITIAL_BUFFER_MS", "800")
    q = _FakeQueue()
    buf = make_audio_jitter_buffer(q, legacy_env_prefix="NEKO_QWEN_TTS")
    assert buf._initial_buffer_bytes == int(800 / 1000 * _TTS_OUTPUT_BYTES_PER_SECOND)


def test_factory_generic_env_wins_over_legacy(monkeypatch):
    monkeypatch.setenv("NEKO_TTS_JITTER_INITIAL_MS", "500")
    monkeypatch.setenv("NEKO_QWEN_TTS_INITIAL_BUFFER_MS", "800")
    q = _FakeQueue()
    buf = make_audio_jitter_buffer(q, legacy_env_prefix="NEKO_QWEN_TTS")
    assert buf._initial_buffer_bytes == int(500 / 1000 * _TTS_OUTPUT_BYTES_PER_SECOND)


@pytest.mark.asyncio
async def test_sentence_worker_tags_raw_audio_with_source_speech_id():
    request_queue = queue.Queue()
    response_queue = _FakeQueue()
    proxy = _AudioQueueProxy(response_queue)
    request_queue.put(("old-speech", "hello."))
    request_queue.put((None, None))
    request_queue.put((TTS_SHUTDOWN_SENTINEL, None))

    async def synthesize(_text, _speech_id):
        proxy.put(b"old-audio")

    await _non_bistream_tts_main_loop(
        request_queue,
        proxy,
        synthesize,
        label="test",
    )

    assert response_queue.items == [("__audio__", "old-speech", b"old-audio")]
