"""A runtime failure reaches both UI planes before its lease is retired."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests.unit.test_core_independent_asr import _Runtime, _install_ready_lifecycle

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("owner", ["core", "game"])
@pytest.mark.parametrize("code", ["ASR_INPUT_DELIVERY_FAILED", "ASR_INPUT_DELIVERY_UNCERTAIN"])
async def test_runtime_failure_code_is_delivered_exactly_once(owner, code):
    manager = _Runtime()
    _install_ready_lifecycle(manager, "qwen")
    receiver = manager._asr_runtime
    receiver._asr_session = SimpleNamespace(is_ready=True, close=AsyncMock())
    manager._voice_lease_owner = owner
    manager._voice_lease_connection_id = "old-owner"
    await receiver._handle_independent_asr_error(
        receiver._asr_session_epoch, "qwen", status_code=code,
    )
    while receiver._asr_close_tasks:
        await asyncio.gather(*tuple(receiver._asr_close_tasks))
    codes = [json.loads(call.args[0]).get("code") for call in manager.send_status.await_args_list]
    assert codes.count(code) == 1
    assert manager._asr_route_mode == "blocked"


async def test_display_send_takeover_does_not_notify_or_revoke_successor():
    manager = _Runtime()
    _install_ready_lifecycle(manager, "qwen")
    receiver = manager._asr_runtime
    receiver._asr_session = SimpleNamespace(is_ready=True, close=AsyncMock())
    manager._voice_lease_connection_id = "old-owner"
    successor = object()
    manager._voice_input_websocket = object()
    manager._voice_owner_socket = lambda: manager._voice_input_websocket
    manager._send_to_voice_owner = AsyncMock()

    async def send_display(message):
        if json.loads(message).get("code") == "ASR_INPUT_DELIVERY_FAILED":
            manager._begin_asr_route_operation()
            manager._voice_lease_connection_id = "successor"
            manager._voice_input_websocket = successor
            receiver._asr_audio_generation += 1
        return True

    manager.send_status.side_effect = send_display
    await receiver._handle_independent_asr_error(
        receiver._asr_session_epoch, "qwen", status_code="ASR_INPUT_DELIVERY_FAILED",
    )
    while receiver._asr_close_tasks:
        await asyncio.gather(*tuple(receiver._asr_close_tasks))
    failures = [call for call in manager._send_to_voice_owner.await_args_list
                if "ASR_INPUT_DELIVERY_FAILED" in str(call)]
    assert not failures
    assert manager._voice_input_websocket is successor
    assert manager._voice_lease_connection_id == "successor"
