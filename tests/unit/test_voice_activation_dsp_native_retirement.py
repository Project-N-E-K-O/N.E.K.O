"""DSP revocation must fence the native connection of an unsettled write."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from tests.unit.test_voice_activation_handoff import _harness, _until


pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("replacement", ["none", "object", "connection"])
@pytest.mark.parametrize("disable_before_settlement", [False, True])
async def test_dsp_failure_retires_only_unsettled_source(replacement, disable_before_settlement):
    async with _harness("native") as h:
        source = h.manager.session
        source._connection_generation = 1
        source.close = AsyncMock()
        h.manager.session_closed_by_server = False
        h.manager.handle_connection_error = AsyncMock()
        entered, release = asyncio.Event(), asyncio.Event()

        async def held_send(pcm):
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
            await source._stream(pcm)

        source.stream_audio.side_effect = held_send
        try:
            await h.feed(3000)
            await _until(entered.is_set)
            writer = h.activation._output_task
            if replacement == "object":
                h.manager.session = h.session("successor")
            elif replacement == "connection":
                source._connection_generation = 2
            h.factory.noise_reduction_enabled = True
            await h.manager._route_microphone_audio(
                b"\x34\x12" * 1600, sample_rate_hz=16000,
                rnnoise_available=False,
            )

            async def disable():
                await h.manager.set_voice_session_activation_factory(
                    None, activation_generation="explicitly-disabled",
                )

            if disable_before_settlement:
                await disable()
                before = len(h.deliveries)
                h.manager.session.stream_audio.side_effect = h.manager.session._stream
                await h.feed(4000)
                assert len(h.deliveries) == before + (replacement != "none")
            release.set()
            await writer
            if not disable_before_settlement:
                await disable()
            cleanup = tuple(h.manager._core_asr_cleanup_tasks)
            if cleanup:
                await asyncio.gather(*cleanup)
            h.manager.session.stream_audio.side_effect = h.manager.session._stream
            before = len(h.deliveries)
            await h.feed(5000)
            assert len(h.deliveries) == before + (replacement != "none")
            if replacement == "none":
                source.close.assert_awaited_once()
                assert h.manager.session_closed_by_server
            else:
                source.close.assert_not_awaited()
                h.manager.handle_connection_error.assert_not_awaited()
                assert not h.manager.session_closed_by_server
        finally:
            release.set()
