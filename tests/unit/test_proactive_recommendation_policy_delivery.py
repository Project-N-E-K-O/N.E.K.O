"""Publication guards and cleanup stay bound to the rejected proactive turn."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from main_logic.core.proactive import ProactiveMixin
from main_logic.core.tts_runtime import TtsRuntimeMixin
from main_logic.core.turn import TurnMixin
from main_logic.proactive_chat import delivery
from tests.unit.test_core_game_route_memory_contract import _make_manager


class GateLock:
    def __init__(self):
        self.waiting = asyncio.Event()
        self.release = asyncio.Event()

    async def __aenter__(self):
        self.waiting.set()
        await self.release.wait()

    async def __aexit__(self, *_args):
        return None


@pytest.mark.asyncio
async def test_tts_rechecks_policy_after_cache_lock_wait():
    manager = _make_manager()
    manager.use_tts = True
    manager.tts_cache_lock = lock = GateLock()
    allowed = True
    task = asyncio.create_task(ProactiveMixin.feed_tts_chunk(
        manager, "Synthetic recommendation", expected_speech_id=manager.current_speech_id,
        delivery_allowed=lambda: allowed,
    ))
    try:
        await asyncio.wait_for(lock.waiting.wait(), 2)
        allowed = False
        lock.release.set()
        assert await asyncio.wait_for(task, 2) is False
        assert manager.tts_pending_chunks == []
        assert manager.tts_request_queue.empty()
    finally:
        lock.release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_final_policy_recheck_after_real_focus_cleanup():
    manager = _make_manager()
    manager._proactive_write_lock = asyncio.Lock()
    manager.state = SimpleNamespace(fire=AsyncMock())
    manager.send_lanlan_response = TurnMixin.send_lanlan_response.__get__(manager)
    entered = asyncio.Event()
    release = asyncio.Event()
    allowed = True

    async def focus(_active):
        entered.set()
        await release.wait()

    manager._push_focus_thinking = focus
    task = asyncio.create_task(ProactiveMixin.finish_proactive_delivery(
        manager, "Synthetic recommendation", expected_speech_id=manager.current_speech_id,
        delivery_allowed=lambda: allowed,
    ))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        allowed = False
        release.set()
        assert await asyncio.wait_for(task, 2) is False
        assert manager.sync_message_queue.empty()
        assert manager._current_ai_turn_text == ""
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def cleanup_manager():
    runtime = SimpleNamespace(retired=False)
    pending = ["queued proactive"]
    queue = object()
    session = object()
    manager = SimpleNamespace(
        current_speech_id="proactive", _tts_runtime=runtime,
        session=session, tts_pending_chunks=pending, tts_response_queue=queue,
        tts_cache_lock=asyncio.Lock(), _tts_output_is_current=lambda: True,
        _tts_done_queued_for_turn=True, _tts_done_pending_until_ready=True,
        _reset_tts_replay_state=MagicMock(), _discard_pending_ai_voice_echo=MagicMock(),
        _drain_tts_responses=MagicMock(),
    )
    interrupt = (runtime, queue, pending, session, "proactive", False)
    manager._interrupt_tts_now = MagicMock(return_value=interrupt)
    manager._let_tts_interrupt_land = TtsRuntimeMixin._let_tts_interrupt_land.__get__(manager)
    manager._finish_tts_clear = TtsRuntimeMixin._finish_tts_clear.__get__(manager)
    return manager, interrupt


@pytest.mark.asyncio
async def test_rejected_output_cleanup_preserves_speech_identity():
    manager, _ = cleanup_manager()
    await ProactiveMixin.discard_proactive_delivery(manager, expected_speech_id="proactive")
    assert manager.tts_pending_chunks == []
    assert manager.current_speech_id == "proactive"
    assert manager._tts_done_queued_for_turn is False


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["interrupt_wait", "cache_lock"])
async def test_cleanup_does_not_clear_user_output_after_takeover(monkeypatch, boundary):
    manager, interrupt = cleanup_manager()
    if boundary == "interrupt_wait":
        from main_logic.core import tts_runtime
        entered = asyncio.Event()
        release = asyncio.Event()

        async def controlled_sleep(_seconds):
            entered.set()
            await release.wait()

        monkeypatch.setattr(tts_runtime, "asyncio", SimpleNamespace(sleep=controlled_sleep))
        manager._interrupt_tts_now.return_value = (*interrupt[:-1], True)
    else:
        manager.tts_cache_lock = lock = GateLock()
        entered, release = lock.waiting, lock.release
    task = asyncio.create_task(ProactiveMixin.discard_proactive_delivery(
        manager, expected_speech_id="proactive",
    ))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        manager.current_speech_id = "new-user"
        manager.tts_pending_chunks[:] = ["user answer"]
        release.set()
        await asyncio.wait_for(task, 2)
        assert manager.tts_pending_chunks == ["user answer"]
        assert manager.current_speech_id == "new-user"
        manager._reset_tts_replay_state.assert_not_called()
        manager._drain_tts_responses.assert_not_called()
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def commit(manager, allowed):
    return await delivery._commit_proactive_delivery(
        mgr=manager, proactive_sid="proactive", lanlan_name="Synthetic",
        response_text="Synthetic recommendation", source_tag="CHAT", active_channels=["news"],
        selected_web_link=None, selected_music_link=None, selected_meme_link=None,
        music_content=None, is_music_used=False, is_playing_music=False, music_cooldown=False,
        vision_content=None, phase2_use_vision=False, screenshot_b64=None,
        proactive_lang="en", master_name="User", delivery_allowed=allowed,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["feed", "finish"])
async def test_policy_rejection_cannot_become_text_only_success(boundary):
    allowed = True
    manager = SimpleNamespace(
        current_speech_id="proactive", last_user_engagement_time=None,
        discard_proactive_delivery=AsyncMock(), handle_new_message=AsyncMock(),
    )

    async def feed(*_args, **_kwargs):
        nonlocal allowed
        if boundary == "feed":
            allowed = False
            return False
        return True

    async def finish(*_args, **_kwargs):
        nonlocal allowed
        allowed = False
        return False

    manager.feed_tts_chunk = AsyncMock(side_effect=feed)
    manager.finish_proactive_delivery = AsyncMock(side_effect=finish)
    result = await commit(manager, lambda: allowed)
    assert result.delivery is None
    assert result.result.body["reason_code"] == "PASS_USER_COOLDOWN"
    assert result.result.body["attempt_consumed"] is False
    manager.handle_new_message.assert_not_awaited()
    if boundary == "feed":
        manager.finish_proactive_delivery.assert_not_awaited()
        manager.discard_proactive_delivery.assert_not_awaited()
    else:
        manager.discard_proactive_delivery.assert_awaited_once_with(expected_speech_id="proactive")


@pytest.mark.asyncio
async def test_failed_audio_cleanup_cannot_rotate_user_turn():
    allowed = True

    async def feed(*_args, **_kwargs):
        nonlocal allowed
        allowed = False
        return True

    manager = SimpleNamespace(
        current_speech_id="proactive", last_user_engagement_time=None,
        feed_tts_chunk=AsyncMock(side_effect=feed), finish_proactive_delivery=AsyncMock(),
        discard_proactive_delivery=AsyncMock(side_effect=RuntimeError("synthetic cleanup failure")),
        handle_new_message=AsyncMock(),
    )
    result = await commit(manager, lambda: allowed)
    assert result.result.body["reason_code"] == "PASS_USER_COOLDOWN"
    manager.finish_proactive_delivery.assert_not_awaited()
    manager.handle_new_message.assert_not_awaited()
