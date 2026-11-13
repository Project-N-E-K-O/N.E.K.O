import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from main_logic import music_playback


def response(data):
    return SimpleNamespace(json=lambda: data, raise_for_status=lambda: None)


@pytest.mark.asyncio
async def test_receipt_retries_failed_run_after_successful_http_acceptance(monkeypatch):
    from utils import internal_http_client
    client = SimpleNamespace(
        post=AsyncMock(side_effect=[response({'run_id':'1', 'status':'queued'}), response({'run_id':'2','status':'succeeded'})]),
        get=AsyncMock(return_value=response({'run_id':'1','status':'failed'})),
    )
    monkeypatch.setattr(internal_http_client, 'get_internal_http_client', lambda: client)
    monkeypatch.setattr(music_playback.asyncio, 'sleep', AsyncMock())
    await music_playback._deliver_queue_terminal('Mika', 'a'*32, 'ended')
    assert client.post.await_count == 2
    assert client.get.await_count == 1
    keys = [call.kwargs['json']['idempotency_key'] for call in client.post.call_args_list]
    assert len(set(keys)) == 2


@pytest.mark.asyncio
async def test_receipt_retry_is_bounded(monkeypatch):
    from utils import internal_http_client
    client = SimpleNamespace(post=AsyncMock(return_value=response({'run_id':'1','status':'failed'})))
    monkeypatch.setattr(internal_http_client, 'get_internal_http_client', lambda: client)
    monkeypatch.setattr(music_playback.asyncio, 'sleep', AsyncMock())
    await music_playback._deliver_queue_terminal('Mika', 'a'*32, 'closed')
    assert client.post.await_count == 2


@pytest.mark.asyncio
async def test_closed_after_ended_is_delivered_and_socket_disconnect_clears_track(monkeypatch):
    deliver = AsyncMock()
    monkeypatch.setattr(music_playback, '_deliver_queue_terminal', deliver)
    owner, other = object(), object()
    manager = SimpleNamespace(lanlan_name='Mika', enqueue_agent_callback=MagicMock())
    event = dict(state='playing', track={'name':'Song','artist':'Band'}, playback_id='1',
                 playback_window_id='window', playback_started_at=100, source='music_play_url',
                 netease_id=1, queue_ticket='a'*32)
    assert music_playback.handle_music_playback_state(manager, event, websocket=owner)
    music_playback.clear_music_playback_connection(manager, other)
    assert manager._current_netease_track is not None
    music_playback.clear_music_playback_connection(manager, owner)
    assert manager._current_netease_track is None
    assert music_playback.handle_music_playback_state(manager, event, websocket=other)
    assert manager._current_netease_track['song_id'] == 1
    assert music_playback.handle_music_playback_state(manager, {**event,'state':'ended'}, websocket=owner)
    assert music_playback.handle_music_playback_state(manager, {**event,'state':'ended','queue_closed':True}, websocket=owner)
    await asyncio.gather(*music_playback._queue_callbacks)
    assert [c.args[2] for c in deliver.call_args_list] == ['ended', 'closed']


def test_buffering_then_closed_clears_confirmed_track():
    owner = object()
    manager = SimpleNamespace(lanlan_name='Mika', enqueue_agent_callback=MagicMock())
    event = dict(state='playing', track={'name':'Song','artist':'Band'}, playback_id='1',
                 playback_window_id='window', playback_started_at=100, source='music_play_url', netease_id=1)
    assert music_playback.handle_music_playback_state(manager, event, websocket=owner)
    assert music_playback.handle_music_playback_state(manager, {**event, 'state':'buffering'}, websocket=owner)
    assert manager._current_netease_track['song_id'] == 1
    assert music_playback.handle_music_playback_state(manager, {**event, 'state':'ended', 'queue_closed':True}, websocket=owner)
    assert manager._current_netease_track is None
