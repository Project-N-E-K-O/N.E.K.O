"""A partially sent activation prefix must fail closed without replaying its tail."""

import asyncio
import json

import pytest

from tests.unit.test_voice_activation_cold_prefix import _cold_harness, _feed, _until


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "protected,attempted,fail_at,expected_code",
    [
        (True, True, 2, "ASR_INPUT_DELIVERY_UNCERTAIN"),
        (True, None, 2, "ASR_INPUT_DELIVERY_UNCERTAIN"),
        (True, False, 1, "ASR_INPUT_DELIVERY_FAILED"),
        (False, True, 2, "ASR_INDEPENDENT_STREAM_FAILED"),
    ],
)
async def test_partial_prefix_send_failure_stops_tail_and_reports_uncertainty(
    protected,
    attempted,
    fail_at,
    expected_code,
):
    async with _cold_harness() as h:
        for marker in range(1, 16):
            await _feed(h, marker)
        await _until(lambda: h.lifecycle.pending_connect_bytes == 48000)
        await _until(lambda: bool(h.sessions))
        session = h.sessions[0]
        attempts = []
        if not protected:
            h.manager._asr_runtime._asr_protected_prefix = None

        async def fail_after_first_chunk(pcm, **kwargs):
            session.transport_write_attempted = attempted
            attempts.append(pcm)
            if len(attempts) == fail_at:
                raise RuntimeError("socket send result unknown")
            h.deliveries.append(pcm)

        session.stream_audio = fail_after_first_chunk
        h.release.set()
        await _until(lambda: h.manager._asr_route_mode == "blocked")
        await _until(lambda: session.close.await_count == 1)
        assert len(attempts) == fail_at
        assert len(b"".join(h.deliveries)) == (fail_at - 1) * 32000
        assert h.lifecycle.pending_connect_bytes == 0
        assert not h.lifecycle.peek_active_start_audio()
        for _ in range(30):
            await _feed(h, 99)
        await asyncio.sleep(0)
        assert len(attempts) == fail_at
        assert len(h.sessions) == 1
        codes = []
        for call in h.manager.send_status.await_args_list:
            try:
                payload = json.loads(call.args[0])
            except (TypeError, ValueError, IndexError):
                continue
            codes.append(payload.get("code"))
        assert expected_code in codes, codes
