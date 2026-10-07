import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from main_logic.voice_turn.contracts import AsrLifecycleNotification, AsrStatusEvent
from tests.support.asr_fakes import _Runtime
from tests.support.core_asr_harness import _install_ready_lifecycle

pytestmark = [pytest.mark.asyncio, pytest.mark.unit_fast]


def recovery_notice(runtime, lifecycle=False):
    fields = dict(provider="qwen", session_epoch=runtime._asr_session_epoch,
                  recovery_id=1, lease_generation=runtime._voice_lease_generation,
                  route_generation=runtime._capture_ingress_token().route_generation,
                  recovery_session_epoch=runtime._asr_session_epoch - 1,
                  buffering=True)
    return (AsrLifecycleNotification(state="blocked", **fields) if lifecycle else
            AsrStatusEvent(code="ASR_RECOVERY_STARTED", **fields))


@pytest.mark.parametrize("lifecycle", [False, True])
async def test_recovery_notifications_retain_old_ui_epoch_and_reach_recording_owner(lifecycle):
    runtime = _Runtime()
    # These counters represent different authorities and need not coincide.
    runtime._asr_route_operation_generation = 17
    runtime._voice_input_transition_generation = 23
    runtime._microphone_route_generation = 31
    runtime._voice_lease_owner = "core"
    runtime._voice_lease_hard_muted = False
    owner = object()
    runtime._voice_owner_socket = lambda: owner
    runtime._send_to_voice_owner = AsyncMock(return_value=owner)
    event = recovery_notice(runtime, lifecycle)
    sender = runtime._send_core_asr_lifecycle if lifecycle else runtime._send_core_asr_status
    await sender(event)
    display = json.loads(runtime.send_status.await_args.args[0])
    copy = json.loads(runtime._send_to_voice_owner.await_args.args[0]["message"])
    assert copy == display
    assert copy["details"]["session_epoch"] == event.recovery_session_epoch
    assert copy["details"]["recovery_id"] == 1
    assert copy["details"]["route_generation"] == 31
    assert copy["details"]["buffering"] is True


@pytest.mark.parametrize("lifecycle", [False, True])
@pytest.mark.parametrize("stale", ["session_epoch", "lease_generation", "route_generation"])
async def test_recovery_notifications_reject_stale_source_before_delivery(lifecycle, stale):
    runtime = _Runtime()
    runtime._voice_lease_owner = "core"
    runtime._voice_lease_hard_muted = False
    event = recovery_notice(runtime, lifecycle)
    event = replace(event, **{stale: getattr(event, stale) - 1})
    sender = runtime._send_core_asr_lifecycle if lifecycle else runtime._send_core_asr_status
    await sender(event)
    runtime.send_status.assert_not_awaited()


@pytest.mark.parametrize("lifecycle", [False, True])
async def test_recovery_takeover_during_display_send_does_not_notify_successor(lifecycle):
    runtime = _Runtime()
    runtime._voice_lease_owner = "core"
    runtime._voice_lease_hard_muted = False
    runtime._voice_owner_socket = lambda: object()
    runtime._send_to_voice_owner = AsyncMock()

    async def takeover(_message):
        runtime._voice_lease_generation += 1
        return True

    runtime.send_status.side_effect = takeover
    sender = runtime._send_core_asr_lifecycle if lifecycle else runtime._send_core_asr_status
    await sender(recovery_notice(runtime, lifecycle))
    runtime._send_to_voice_owner.assert_not_awaited()


async def test_abandoned_turn_clears_only_its_preview_and_invalidates_its_registry_token():
    runtime = _Runtime()
    _install_ready_lifecycle(runtime)
    token = runtime._asr_runtime._capture_turn_token(runtime._asr_lifecycle)
    runtime.websocket = SimpleNamespace(send_json=AsyncMock())
    runtime._core_asr_preview_turn_id = "new-preview"
    runtime._core_asr_preview_text = "new sentence"
    runtime._voice_input_registry = SimpleNamespace(invalidate_utterance=MagicMock(), wait_idle=AsyncMock())
    await runtime._handle_core_asr_turn_abandoned(token)
    assert runtime._core_asr_preview_text == "new sentence"
    assert runtime.websocket.send_json.await_args.args[0]["asr_turn_id"] == f"asr-{token.ingress.session_epoch}-{token.turn_id}"
    runtime._voice_input_registry.invalidate_utterance.assert_called_once_with(token, reason="asr_turn_abandoned")


async def test_abandoned_turn_releases_registry_token_even_when_notification_cancelled():
    runtime = _Runtime()
    _install_ready_lifecycle(runtime)
    token = runtime._asr_runtime._capture_turn_token(runtime._asr_lifecycle)
    runtime.websocket = SimpleNamespace(send_json=AsyncMock(side_effect=asyncio.CancelledError))
    runtime._voice_input_registry = SimpleNamespace(invalidate_utterance=MagicMock(), wait_idle=AsyncMock())
    with pytest.raises(asyncio.CancelledError):
        await runtime._handle_core_asr_turn_abandoned(token)
    runtime._voice_input_registry.invalidate_utterance.assert_called_once_with(token, reason="asr_turn_abandoned")
