import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from main_logic.watch_together import preparation
from main_routers import watch_together_router as router
from main_routers.game_router import runtime


@pytest.fixture
def counted(monkeypatch):
    from utils import instrument
    calls = []
    monkeypatch.setattr(instrument, 'counter', lambda name, value=1, **dims: calls.append((name, dims)))
    return calls


@pytest.fixture
def scene(monkeypatch):
    from utils import game_route_state
    route = {'state': {'game_route_active': True, 'session_id': 's'}}
    monkeypatch.setattr(game_route_state, '_get_active_game_route_state',
                        lambda name, game: route['state'] if (name, game) == ('Lan', 'watch-together') else None)
    monkeypatch.setattr(runtime, '_sdk_route_instance_error', lambda _state, _data: None)
    starts = iter(f'watch-{index}' for index in range(1, 10))
    recorded = []

    def record_watch(identifier, position, event):
        if identifier == 'unknown':
            raise KeyError(identifier)
        recorded.append((identifier, event['type']))
    monkeypatch.setattr(router, 'application_library', lambda: SimpleNamespace(
        start_watch=lambda *_args: next(starts), record_watch=record_watch))
    return SimpleNamespace(route=route, recorded=recorded)


def _request(**body):
    payload = {'lanlan_name': 'Lan', 'session_id': 's', **body}
    return SimpleNamespace(headers={}, json=AsyncMock(return_value=payload))


async def _start(**body):
    return (await router.watch(_request(action='start', job='job', version='v', **body)))['id']


async def _event(identifier, kind):
    return await router.watch(_request(id=identifier, position=1, event={'type': kind}))


@pytest.mark.asyncio
async def test_only_actual_playback_counts_once_per_viewing_and_route(scene, counted):
    first = await _start()
    await _event(first, 'progress')
    assert counted == [], 'starting a viewing session before playback is not watching'
    for kind in ('play', 'pause', 'play', 'seek', 'ended', 'play', 'ended', 'exit'):
        await _event(first, kind)
    assert counted == [
        ('watch_together_video_played', {'automatic': False}),
        ('mini_game_played', {'game_type': 'watch-together'}),
        ('watch_together_video_completed', {'automatic': False}),
    ]
    counted.clear()
    # Automatic mode keeps the same route across videos: a new video counts, the route does not.
    second = await _start(automatic=True)
    await _event(second, 'play')
    await _event(second, 'ended')
    assert counted == [
        ('watch_together_video_played', {'automatic': True}),
        ('watch_together_video_completed', {'automatic': True}),
    ]
    counted.clear()
    # A new route activation is a new play of the scene.
    scene.route['state'] = {'game_route_active': True, 'session_id': 's'}
    third = await _start(automatic='yes')
    await _event(third, 'play')
    assert counted == [
        ('watch_together_video_played', {'automatic': False}),
        ('mini_game_played', {'game_type': 'watch-together'}),
    ]


@pytest.mark.asyncio
async def test_rejected_viewing_records_are_not_counted(scene, counted):
    identifier = await _start()
    with pytest.raises(HTTPException):
        await _event('other', 'play')
    scene.route['state']['watch_together_id'] = 'unknown'
    with pytest.raises(HTTPException):
        await _event('unknown', 'play')
    scene.route['state']['watch_together_id'] = identifier
    with pytest.raises(HTTPException):
        await _event(identifier, 'unsupported')
    assert counted == []


@pytest.mark.asyncio
async def test_counter_failure_never_blocks_the_viewing_record(scene, monkeypatch):
    from utils import instrument

    def broken(*_args, **_kwargs):
        raise RuntimeError('telemetry down')
    monkeypatch.setattr(instrument, 'counter', broken)
    identifier = await _start()
    assert await _event(identifier, 'play') == {'ok': True}
    assert scene.recorded == [(identifier, 'play')]


@pytest.mark.asyncio
@pytest.mark.parametrize('automatic,source', [(False, 'manual'), (True, 'discovery')])
@pytest.mark.parametrize('failure,outcome', [(None, 'ready'), (RuntimeError('vision down'), 'error')])
async def test_preparation_counts_its_terminal_outcome(tmp_path, monkeypatch, counted, automatic, source, failure, outcome):
    monkeypatch.setattr(preparation.media, 'check_available', lambda: None)

    class Engine:
        def __init__(self, root, *_args, **_kwargs):
            self.root = root

        async def vision_config(self):
            return {}

        async def prepare(self, job, *_args, **_kwargs):
            if failure:
                raise failure
            (self.root / job['id']).mkdir(parents=True)
            job['status'] = 'ready'

    async def speech_ready(*_args, **_kwargs):
        return {'ok': True}
    monkeypatch.setattr(preparation, 'Engine', Engine)
    monkeypatch.setattr(preparation, 'application_library',
                        lambda: SimpleNamespace(root=tmp_path, import_sources=lambda *_args, **_kwargs: None))
    monkeypatch.setattr(preparation, 'jobs', {})
    monkeypatch.setattr(preparation, 'tasks', set())
    manager = SimpleNamespace(preflight_game_speech_audio=speech_ready,
                              game_speech_audio_cache_identity=lambda *_args, **_kwargs: ('key', 'voice'))
    await preparation.prepare('video', manager, 'cat', automatic=automatic)
    await asyncio.gather(*preparation.tasks)
    assert counted == [('watch_together_prepared', {'source': source, 'outcome': outcome})]
