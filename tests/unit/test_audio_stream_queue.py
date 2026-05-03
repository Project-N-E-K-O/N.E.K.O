import asyncio
import os
import sys
from unittest.mock import AsyncMock

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from main_logic.core import LLMSessionManager
from main_logic.omni_realtime_client import OmniRealtimeClient


async def test_starting_session_audio_does_not_enter_pending_input_data():
    mgr = LLMSessionManager.__new__(LLMSessionManager)
    mgr.session_ready = False
    mgr._starting_session_count = 1
    mgr.pending_input_data = []
    mgr.input_cache_lock = asyncio.Lock()

    await LLMSessionManager._stream_data_now(mgr, {"input_type": "audio", "data": [0] * 480})

    assert mgr.pending_input_data == []


async def test_flush_pending_input_data_routes_audio_through_bounded_queue():
    mgr = LLMSessionManager.__new__(LLMSessionManager)
    audio_msg = {"input_type": "audio", "data": [1] * 480}
    text_msg = {"input_type": "text", "data": "hello"}
    mgr.pending_input_data = [audio_msg, text_msg]
    mgr.input_cache_lock = asyncio.Lock()
    mgr.session = object()
    mgr.is_active = True
    mgr._enqueue_audio_stream_data = AsyncMock()
    mgr._process_stream_data_internal = AsyncMock()

    await LLMSessionManager._flush_pending_input_data(mgr)

    mgr._enqueue_audio_stream_data.assert_awaited_once_with(audio_msg)
    mgr._process_stream_data_internal.assert_awaited_once_with(text_msg)
    assert mgr.pending_input_data == []


async def test_audio_stream_queue_drops_oldest_when_full():
    mgr = LLMSessionManager.__new__(LLMSessionManager)
    mgr.lanlan_name = "Test"
    mgr._audio_stream_queue = asyncio.Queue(maxsize=2)
    mgr._audio_stream_dropped_total = 0
    mgr._last_audio_stream_backlog_log_time = 0.0
    mgr._ensure_audio_stream_worker = lambda: None

    await LLMSessionManager._enqueue_audio_stream_data(mgr, {"seq": 1})
    await LLMSessionManager._enqueue_audio_stream_data(mgr, {"seq": 2})
    await LLMSessionManager._enqueue_audio_stream_data(mgr, {"seq": 3})

    assert mgr._audio_stream_dropped_total == 1
    assert mgr._audio_stream_queue.get_nowait()["seq"] == 2
    assert mgr._audio_stream_queue.get_nowait()["seq"] == 3


async def test_inflight_audio_is_dropped_when_epoch_changes():
    mgr = LLMSessionManager.__new__(LLMSessionManager)
    mgr.lanlan_name = "Test"
    mgr.session_ready = True
    mgr._starting_session_count = 0
    mgr.is_active = True
    mgr.session_start_failure_count = 0
    mgr.session_start_max_failures = 3
    mgr._session_start_circuit_open = False
    mgr._audio_stream_epoch = 0
    mgr.session_closed_by_server = False
    mgr.last_audio_send_error_time = 0.0
    mgr.audio_error_log_interval = 2.0
    mgr.is_hot_swap_imminent = False
    mgr.is_flushing_hot_swap_cache = False
    mgr.hot_swap_cache_lock = asyncio.Lock()

    class _RealtimeSession(OmniRealtimeClient):
        def __init__(self):
            self.ws = object()
            self._fatal_error_occurred = False
            self._audio_processor = object()
            self.stream_audio = AsyncMock()

        async def process_audio_chunk_async(self, audio_bytes):
            mgr._audio_stream_epoch += 1
            return audio_bytes

    session = _RealtimeSession()
    mgr.session = session

    await LLMSessionManager._process_stream_data_internal(
        mgr,
        {"input_type": "audio", "data": [1] * 480},
    )

    session.stream_audio.assert_not_awaited()
