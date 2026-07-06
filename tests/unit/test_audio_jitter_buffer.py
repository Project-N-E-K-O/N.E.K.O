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

import re
from pathlib import Path

from main_logic.tts_client._infra import (
    AudioJitterBuffer,
    make_audio_jitter_buffer,
    _TTS_OUTPUT_BYTES_PER_SECOND,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]

REALTIME_WORKERS = {
    "step": PROJECT_ROOT / "main_logic/tts_client/workers/step.py",
    "qwen": PROJECT_ROOT / "main_logic/tts_client/workers/qwen.py",
    "grok": PROJECT_ROOT / "main_logic/tts_client/workers/grok.py",
    "elevenlabs": PROJECT_ROOT / "main_logic/tts_client/workers/elevenlabs.py",
}


class _FakeQueue:
    def __init__(self):
        self.items = []

    def put(self, item):
        self.items.append(item)


def test_initial_head_start_holds_back_until_threshold():
    q = _FakeQueue()
    buf = AudioJitterBuffer(q, initial_buffer_bytes=10, steady_buffer_bytes=4)
    # 首包累计不足 initial 阈值时不放行（盖住开头 inter-chunk gap）。
    buf.append(b"abc")
    buf.append(b"def")
    assert q.items == []
    # 攒够 initial 后一次性放出累计领先量。
    buf.append(b"ghij")
    assert q.items == [b"abcdefghij"]


def test_steady_flush_after_started():
    q = _FakeQueue()
    buf = AudioJitterBuffer(q, initial_buffer_bytes=4, steady_buffer_bytes=4)
    buf.append(b"aaaa")  # 越过 initial，放行首包
    assert q.items == [b"aaaa"]
    buf.append(b"bb")  # 不足 steady，缓冲
    assert q.items == [b"aaaa"]
    buf.append(b"cc")  # 凑够 steady，放行
    assert q.items == [b"aaaa", b"bbcc"]


def test_flush_drains_remainder_below_threshold():
    q = _FakeQueue()
    buf = AudioJitterBuffer(q, initial_buffer_bytes=4, steady_buffer_bytes=4)
    buf.append(b"aaaa")
    buf.append(b"z")  # 尾音不足 steady
    buf.flush()  # response.done 时强制放出
    assert q.items == [b"aaaa", b"z"]


def test_flush_during_head_start_marks_started():
    # 短句不足 initial：靠终结 flush 首次放行后，stray delta 应走 steady 而非
    # 重新 head-start（否则会被下一轮 reset 静默丢弃）。
    q = _FakeQueue()
    buf = AudioJitterBuffer(q, initial_buffer_bytes=10, steady_buffer_bytes=2)
    buf.append(b"ab")  # 不足 initial，缓冲
    assert q.items == []
    buf.flush()  # response.done：放行短句
    assert q.items == [b"ab"]
    assert buf.started is True
    # 晚到的 stray delta 直接走 steady（凑够 2 字节即放行），不再被 hold 400ms
    buf.append(b"cd")
    assert q.items == [b"ab", b"cd"]


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


def test_interrupt_guard_drops_append_and_flush_until_resumed():
    q = _FakeQueue()
    buf = AudioJitterBuffer(q, initial_buffer_bytes=4, steady_buffer_bytes=2)

    buf.append(b"ab")
    buf.begin_interrupt()
    buf.append(b"cd")
    buf.flush()
    assert q.items == []

    buf.reset()
    buf.end_interrupt()
    buf.append(b"wxyz")
    assert q.items == [b"wxyz"]


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


def test_realtime_workers_guard_interrupt_close_windows():
    for provider, path in REALTIME_WORKERS.items():
        source = path.read_text(encoding="utf-8")
        assert ".begin_interrupt()" in source, provider
        assert ".end_interrupt()" in source, provider

    for provider in ("step", "qwen", "grok"):
        source = REALTIME_WORKERS[provider].read_text(encoding="utf-8")
        start = source.index('if sid == "__interrupt__":')
        end = source.index("continue", start)
        block = source[start:end]
        assert block.index(".begin_interrupt()") < block.index("receive_task.cancel()"), provider
        assert block.index("receive_task.cancel()") < block.index("ws.close"), provider

    elevenlabs = REALTIME_WORKERS["elevenlabs"].read_text(encoding="utf-8")
    interrupt_start = elevenlabs.index('if sid == "__interrupt__":')
    interrupt_end = elevenlabs.index("continue", interrupt_start)
    interrupt_block = elevenlabs[interrupt_start:interrupt_end]
    assert "interrupt=True" in interrupt_block

    close_start = elevenlabs.index("async def _close_ws")
    close_end = elevenlabs.index("async def _open_ws", close_start)
    close_block = elevenlabs[close_start:close_end]
    assert close_block.index("if interrupt and receive_task") < close_block.index("if ws is not None")


def test_realtime_workers_flush_jitter_on_non_cancelled_receiver_exit():
    expected_flush_guards = {
        "step": 2,
        "qwen": 2,
        "grok": 1,
        "elevenlabs": 1,
    }
    pattern = re.compile(
        r"finally:\s+if not cancelled:\s+(?:qwen_)?audio_jitter\.flush\(\)",
        re.MULTILINE,
    )

    for provider, expected_count in expected_flush_guards.items():
        source = REALTIME_WORKERS[provider].read_text(encoding="utf-8")
        assert len(pattern.findall(source)) >= expected_count, provider


def test_realtime_workers_flush_tail_before_normal_receiver_cancel():
    for provider in ("step", "qwen", "grok"):
        source = REALTIME_WORKERS[provider].read_text(encoding="utf-8")
        start = source.index("if current_speech_id != sid:")
        end = source.index("receive_task = asyncio.create_task", start)
        block = source[start:end]
        flush_call = "qwen_audio_jitter.flush()" if provider == "qwen" else "audio_jitter.flush()"
        assert block.index(flush_call) < block.index("receive_task.cancel()"), provider

    elevenlabs = REALTIME_WORKERS["elevenlabs"].read_text(encoding="utf-8")
    close_start = elevenlabs.index("async def _close_ws")
    close_end = elevenlabs.index("async def _open_ws", close_start)
    close_block = elevenlabs[close_start:close_end]
    normal_cancel_start = close_block.index("if receive_task and not receive_task.done():")
    normal_cancel_block = close_block[normal_cancel_start:]
    assert normal_cancel_block.index("if not interrupt:") < normal_cancel_block.index("audio_jitter.flush()")
    assert normal_cancel_block.index("audio_jitter.flush()") < normal_cancel_block.index("receive_task.cancel()")
