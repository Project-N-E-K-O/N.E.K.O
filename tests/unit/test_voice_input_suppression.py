from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from main_logic.voice_input.suppression import (
    VoiceInputSuppressionController,
    VoiceInputSuppressionError,
)


pytestmark = pytest.mark.asyncio


async def test_acquire_and_release_are_serialized_and_idempotent() -> None:
    suppress = AsyncMock()
    restore = AsyncMock()
    controller = VoiceInputSuppressionController(suppress, restore)

    lease = await controller.acquire("voice_identity_enrollment")

    assert controller.snapshot().active is True
    suppress.assert_awaited_once_with("voice_identity_enrollment")
    with pytest.raises(VoiceInputSuppressionError):
        await controller.acquire("second")
    assert await lease.release() is True
    assert await lease.release() is False
    assert controller.snapshot().active is False
    restore.assert_awaited_once_with("voice_identity_enrollment")


async def test_failed_suppress_never_leaves_active_lease() -> None:
    suppress = AsyncMock(side_effect=RuntimeError("abort failed"))
    restore = AsyncMock()
    controller = VoiceInputSuppressionController(suppress, restore)

    with pytest.raises(RuntimeError, match="abort failed"):
        await controller.acquire("voice_identity_enrollment")

    assert controller.snapshot().active is False
    restore.assert_awaited_once_with("voice_identity_enrollment")


async def test_hung_suppress_times_out_and_restores_pcm_gate() -> None:
    entered = asyncio.Event()

    async def suppress(_reason: str) -> None:
        entered.set()
        await asyncio.Event().wait()

    restore = AsyncMock()
    controller = VoiceInputSuppressionController(
        suppress,
        restore,
        default_ttl_seconds=0.05,
        hard_ttl_seconds=0.1,
        callback_timeout_seconds=0.01,
    )

    with pytest.raises(TimeoutError):
        await controller.acquire("voice_identity_enrollment")

    assert entered.is_set()
    assert controller.snapshot().active is False
    restore.assert_awaited_once_with("voice_identity_enrollment")


async def test_cancelled_acquire_keeps_owned_restore_cleanup() -> None:
    entered = asyncio.Event()

    async def suppress(_reason: str) -> None:
        entered.set()
        await asyncio.Event().wait()

    restore = AsyncMock()
    controller = VoiceInputSuppressionController(suppress, restore)
    task = asyncio.create_task(controller.acquire("voice_identity_enrollment"))
    await entered.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await controller.close()

    assert controller.snapshot().active is False
    restore.assert_awaited_once_with("voice_identity_enrollment")


async def test_ttl_revokes_and_restores_without_client_cancel() -> None:
    suppress = AsyncMock()
    restore = AsyncMock()
    controller = VoiceInputSuppressionController(
        suppress,
        restore,
        default_ttl_seconds=0.01,
        hard_ttl_seconds=0.02,
    )

    lease = await controller.acquire("voice_identity_enrollment")
    await asyncio.sleep(0.03)

    assert controller.snapshot().active is False
    restore.assert_awaited_once_with("voice_identity_enrollment")
    assert await lease.release() is False


async def test_close_revokes_active_lease_and_rejects_new_acquire() -> None:
    suppress = AsyncMock()
    restore = AsyncMock()
    controller = VoiceInputSuppressionController(suppress, restore)
    await controller.acquire("voice_identity_enrollment")

    await controller.close()

    assert controller.snapshot().active is False
    restore.assert_awaited_once()
    with pytest.raises(VoiceInputSuppressionError, match="closed"):
        await controller.acquire("later")


async def test_hard_ttl_rejects_oversized_request() -> None:
    controller = VoiceInputSuppressionController(
        AsyncMock(),
        AsyncMock(),
        hard_ttl_seconds=60.0,
    )

    with pytest.raises(ValueError, match="hard limit"):
        await controller.acquire("voice_identity_enrollment", ttl_seconds=61.0)
