from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from main_logic.asr_client.detector import (
    DetectorCandidateKey,
    DetectorIngressIdentity,
    DetectorRuntimeEvent,
)
from main_logic.asr_client.lifecycle import VoiceIngressToken
from main_logic.asr_client.detector import (
    AsrDetectorDispatcher,
    CoreDetectorEventEnvelope,
)


pytestmark = pytest.mark.asyncio


def _envelope() -> CoreDetectorEventEnvelope:
    ingress = DetectorIngressIdentity(
        ingress_token=VoiceIngressToken(1, "socket", 1, 1, 1),
        detector_epoch=1,
        sequence_no=1,
    )
    return CoreDetectorEventEnvelope(
        event=DetectorRuntimeEvent(
            ingress=ingress,
            candidate=DetectorCandidateKey(1, 1),
            kind="control_lane_failed",
        ),
        detector_ref=object(),
        lifecycle_ref=object(),
        session_epoch=1,
    )


async def test_handler_failure_fails_closed_without_stranding_wait_idle() -> None:
    handler = AsyncMock(side_effect=RuntimeError("handler failed"))
    on_failure = AsyncMock()
    dispatcher = AsrDetectorDispatcher(handler, on_failure=on_failure)
    envelope = _envelope()

    assert dispatcher.submit_nowait(envelope)
    await dispatcher.wait_idle()

    on_failure.assert_awaited_once()
    assert dispatcher.submit_nowait(envelope) is False
    await dispatcher.close()
