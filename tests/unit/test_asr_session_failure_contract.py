"""Fault evidence and finish capability belong to the public session contract."""

import pytest
from unittest.mock import AsyncMock

from main_logic.asr_client._infra import (
    AsrSessionConfig,
    RealtimeAsrSession,
    _RealtimeAsrSessionImpl,
)
from main_logic.asr_client.provider_policy import resolve_provider_policy


def test_public_protocol_declares_failure_and_finish_surface():
    assert {
        "last_failure_code", "failure_started_at",
        "supports_result_preserving_finish", "finish_and_drain",
    } <= vars(RealtimeAsrSession).keys()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["qwen", "step", "openai", "soniox", "grok"])
async def test_built_in_sessions_implement_the_same_contract(provider):
    async def unused_worker(*_args):
        raise AssertionError("an unconnected session must not start its worker")

    session = _RealtimeAsrSessionImpl(
        worker_fn=unused_worker,
        api_key="",
        config=AsrSessionConfig(endpointing_mode="provider"),
        on_input_transcript=AsyncMock(),
        on_connection_error=AsyncMock(),
        provider_policy=resolve_provider_policy(provider, "provider"),
    )
    try:
        assert isinstance(session, RealtimeAsrSession)
        assert session.last_failure_code is None
        assert session.failure_started_at is None
        assert session.supports_result_preserving_finish is (provider == "qwen")
        if not session.supports_result_preserving_finish:
            with pytest.raises(RuntimeError, match="^ASR_FINISH_NOT_SUPPORTED$"):
                await session.finish_and_drain(deadline=0)
    finally:
        await session.close()
