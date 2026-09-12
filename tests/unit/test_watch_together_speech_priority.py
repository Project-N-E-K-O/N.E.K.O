import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from main_logic.core.proactive import ProactiveMixin
from main_logic.core.turn import TurnMixin
from main_logic.session_state import ProactivePhase
from main_routers.game_router import runtime
from .game_route_test_helpers import gr_patch_all


def test_live_plugin_queue_stays_coalesced_until_takeover_ends():
    manager = SimpleNamespace(
        _takeover_active=True, is_goodbye_silent=lambda: False,
        _is_voice_playing=lambda: False,
        state=SimpleNamespace(phase=ProactivePhase.IDLE), session=None,
    )
    assert not ProactiveMixin._can_release_proactive(manager)
    manager._takeover_active = False
    assert ProactiveMixin._can_release_proactive(manager)


@pytest.mark.asyncio
@pytest.mark.parametrize('kind,mode', [('user-text', 'text'), ('user-voice', 'voice')])
async def test_watch_external_input_does_not_interrupt_or_generate_game_speech(monkeypatch, kind, mode):
    manager = SimpleNamespace(mirror_user_input=AsyncMock(), send_user_activity=AsyncMock())
    gr_patch_all(monkeypatch, 'get_session_manager', lambda: {'Lan': manager})
    state = runtime._build_route_state('watch-together', 'watch-test', 'Lan')
    state['game_route_active'] = True
    monkeypatch.setitem(runtime._game_route_states, runtime._route_state_key('Lan', 'watch-together'), state)
    chat = AsyncMock()
    monkeypatch.setattr(runtime, '_run_game_chat', chat)
    assert await runtime._route_external_transcript_to_game(
        'Lan', state, 'hello', source='external_text_route', mode=mode, kind=kind,
    )
    manager.send_user_activity.assert_not_awaited()
    chat.assert_not_awaited()


@pytest.mark.asyncio
async def test_takeover_invalidates_already_playing_ordinary_speech():
    manager = SimpleNamespace(
        lock=asyncio.Lock(), _takeover_active=True, current_speech_id='old',
        audio_resampler=SimpleNamespace(clear=Mock()),
        _clear_tts_pipeline=AsyncMock(), release_speech_playback_gain=Mock(),
        session=None, send_user_activity=AsyncMock(),
    )
    await TurnMixin.interrupt_ordinary_speech_for_takeover(manager)
    assert manager.current_speech_id != 'old'
    manager._clear_tts_pipeline.assert_awaited_once()
    manager.send_user_activity.assert_awaited_once_with('old')
    manager.release_speech_playback_gain.assert_called_once_with('old')


@pytest.mark.asyncio
async def test_takeover_while_proactive_tts_waits_for_lock_drops_chunk():
    lock = asyncio.Lock()
    manager = SimpleNamespace(_takeover_active=False, use_tts=True, tts_cache_lock=lock)
    await lock.acquire()
    pending = asyncio.create_task(ProactiveMixin.feed_tts_chunk(manager, 'ordinary'))
    await asyncio.sleep(0)
    manager._takeover_active = True
    lock.release()
    assert await pending is False


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [RuntimeError, asyncio.CancelledError])
async def test_failed_takeover_rolls_back_activation_before_releasing_route_lock(failure):
    manager = SimpleNamespace(
        _takeover_active=True, _takeover_input_dispatcher=object(),
        interrupt_ordinary_speech_for_takeover=AsyncMock(side_effect=failure()),
    )
    state = dict.fromkeys([
        'game_route_active', 'game_external_voice_route_active',
        'game_external_text_route_active', 'heartbeat_enabled',
    ], True)
    with pytest.raises(failure):
        await runtime._start_watch_speech_takeover(state, manager)
    assert manager._takeover_active is False
    assert manager._takeover_input_dispatcher is None
    assert not any(state[key] for key in state if key != 'exit_reason')
    assert state['exit_reason'] == 'speech_takeover_failed'
