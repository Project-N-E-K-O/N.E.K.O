"""Actual send boundaries, not adapter admission, determine delivery evidence."""

import asyncio
import json

import pytest

from main_logic.asr_client._infra import AsrSessionConfig, _AsrWorkerRequest
from main_logic.asr_client.delivery import delivery_evidence
from main_logic.asr_client.workers.qwen import _QwenConnectionState, _qwen_sender


@pytest.mark.asyncio
async def test_many_frames_have_one_wire_log_and_connection_local_totals(caplog):
    class Socket:
        async def send(self, payload):
            assert json.loads(payload)["type"] == "input_audio_buffer.append"

    requests, responses = asyncio.Queue(), asyncio.Queue()
    state = _QwenConnectionState(7, 3, 1, False)
    state.configured.set()
    for _ in range(150):
        requests.put_nowait(_AsrWorkerRequest("audio", 7, 3, 1, b"\x00\x01" * 160))
    with caplog.at_level("INFO", logger="main_logic.asr_client.delivery"):
        task = asyncio.create_task(
            _qwen_sender(Socket(), requests, responses, AsrSessionConfig(), state)
        )
        await asyncio.wait_for(requests.join(), 1)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    evidence = delivery_evidence(requests)
    assert evidence.written_audio_bytes == 48000
    assert sum("phase=transport_written" in r.message for r in caplog.records) == 1
    successor = delivery_evidence(asyncio.Queue())
    assert successor.trace_id != evidence.trace_id
    assert not successor.attempted and successor.written_audio_bytes == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["success", "error", "cancel"])
async def test_qwen_delivery_evidence_waits_for_actual_send(outcome, caplog):
    entered, release = asyncio.Event(), asyncio.Event()

    class Socket:
        async def send(self, payload):
            assert json.loads(payload)["type"] == "input_audio_buffer.append"
            entered.set()
            await release.wait()
            if outcome == "error":
                raise RuntimeError("socket interrupted")

    requests, responses = asyncio.Queue(), asyncio.Queue()
    state = _QwenConnectionState(7, 3, 1, False)
    state.configured.set()
    requests.put_nowait(_AsrWorkerRequest("audio", 7, 3, 1, b"\x00\x01" * 160))
    with caplog.at_level("INFO", logger="main_logic.asr_client.delivery"):
        task = asyncio.create_task(
            _qwen_sender(Socket(), requests, responses, AsrSessionConfig(), state)
        )
        await asyncio.wait_for(entered.wait(), 1)
        evidence = delivery_evidence(requests)
        assert evidence.attempted
        assert evidence.written_audio_bytes == 0
        if outcome == "cancel":
            task.cancel()
        else:
            release.set()
        if outcome == "success":
            await asyncio.wait_for(requests.join(), 1)
            assert evidence.written_audio_bytes == 320
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        if outcome != "success":
            assert evidence.written_audio_bytes == 0
        assert sum("phase=transport_written" in r.message for r in caplog.records) == (
            outcome == "success"
        )
        assert delivery_evidence(asyncio.Queue()).attempted is False
