"""Persisted opt-out and delivery-time enforcement for screen-sharing dialogs."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, call

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from main_logic.activity.snapshot import ActivitySnapshot
from main_logic.activity.tracker import UserActivityTracker
from main_routers.config_router import preferences as routes
from utils import preferences, token_tracker
from utils.activity_config import ActivityPreferences

KEY = 'activityContextPromptEnabled'

@pytest.fixture
def client(monkeypatch, tmp_path):
    path = tmp_path / 'preferences.json'
    monkeypatch.setattr(preferences, '_config_manager', SimpleNamespace(
        get_config_path=lambda _: path,
        get_runtime_config_path=lambda _: path,
        ensure_config_directory=lambda: None,
    ))
    monkeypatch.setattr(preferences, 'assert_cloudsave_writable', lambda *a, **k: None)
    monkeypatch.setattr(token_tracker, 'get_telemetry_branch', lambda: 'main')
    app = FastAPI()
    app.add_api_route('/settings', routes.get_conversation_settings, methods=['GET'])
    app.add_api_route('/settings', routes.save_conversation_settings, methods=['POST'])
    with TestClient(app) as client:
        yield client


def test_api_roundtrip_partial_save_and_stale_write(client):
    initial = client.get('/settings')
    saved = client.post('/settings', json={KEY: False, 'proactiveVisionChatEnabled': True},
                        headers={'If-Match': initial.headers['etag']})
    assert saved.status_code == 200 and saved.json()['success']
    assert client.get('/settings').json()['settings'][KEY] is False
    assert preferences.load_global_conversation_settings()[KEY] is False
    stale = client.post('/settings', json={KEY: True}, headers={'If-Match': initial.headers['etag']})
    assert stale.status_code == 412
    client.post('/settings', json={'proactiveChatInterval': 30})
    settings = client.get('/settings').json()['settings']
    assert settings[KEY] is False and settings['proactiveVisionChatEnabled'] is True
    client.post('/settings', json={KEY: True})
    assert client.get('/settings').json()['settings'][KEY] is True


@pytest.mark.parametrize('value', ['false', 0, 1, None, {}, []])
def test_non_boolean_values_cannot_replace_preference(client, value):
    client.post('/settings', json={KEY: False})
    client.post('/settings', json={KEY: value})
    assert client.get('/settings').json()['settings'][KEY] is False


@pytest.mark.parametrize('context', ['work', 'play'])
@pytest.mark.parametrize('settings,expected', [({}, True), ({KEY: True}, True), ({KEY: False}, False)])
def test_tracker_checks_persisted_preference_at_delivery(monkeypatch, context, settings, expected):
    tracker = UserActivityTracker.__new__(UserActivityTracker)
    tracker.lanlan_name = 'fixture'
    tracker._context_prompt_pending = {'context': context, 'set_at': 1}
    tracker._on_context_prompt = AsyncMock()
    monkeypatch.setattr('main_logic.activity.tracker.is_external_route_active', lambda _: False)
    settings = dict(settings)
    reader = AsyncMock(return_value=settings)
    monkeypatch.setattr(preferences, 'aload_global_conversation_settings', reader)
    asyncio.run(tracker._drain_context_prompt())
    assert tracker._context_prompt_pending is None
    assert tracker._on_context_prompt.await_count == int(expected)
    reader.assert_awaited_once_with(strict=True)
    # Enabling cannot replay a signal already consumed while disabled.
    settings[KEY] = True
    asyncio.run(tracker._drain_context_prompt())
    assert tracker._on_context_prompt.await_count == int(expected)


def test_unreadable_preferences_do_not_send_a_prompt(monkeypatch):
    tracker = UserActivityTracker.__new__(UserActivityTracker)
    tracker.lanlan_name = 'fixture'
    tracker._context_prompt_pending = {'context': 'work'}
    tracker._on_context_prompt = AsyncMock()
    monkeypatch.setattr('main_logic.activity.tracker.is_external_route_active', lambda _: False)
    monkeypatch.setattr(preferences, 'aload_global_conversation_settings', AsyncMock(side_effect=OSError('unavailable')))
    asyncio.run(tracker._drain_context_prompt())
    assert tracker._context_prompt_pending is None
    tracker._on_context_prompt.assert_not_awaited()


def test_route_takeover_during_settings_read_consumes_prompt(monkeypatch):
    tracker = UserActivityTracker.__new__(UserActivityTracker)
    tracker.lanlan_name = 'fixture'
    tracker._context_prompt_pending = {'context': 'work'}
    tracker._on_context_prompt = AsyncMock()
    active = False
    async def read_settings(**kwargs):
        nonlocal active
        active = True
        return {KEY: True}
    monkeypatch.setattr('main_logic.activity.tracker.is_external_route_active', lambda _: active)
    monkeypatch.setattr(preferences, 'aload_global_conversation_settings', read_settings)
    asyncio.run(tracker._drain_context_prompt())
    assert tracker._context_prompt_pending is None
    tracker._on_context_prompt.assert_not_awaited()


@pytest.fixture
def context_tracker(monkeypatch):
    tracker = UserActivityTracker('fixture', collector=SimpleNamespace())
    tracker._sm._prefs = ActivityPreferences()
    tracker.set_context_prompt_callback(AsyncMock())
    monkeypatch.setattr('main_logic.activity.tracker.is_external_route_active', lambda _: False)
    return tracker


def _tick_context_state(tracker, state, *, now):
    tracker._tick_break_reminders(ActivitySnapshot(
        state=state,
        state_age_seconds=10.0,
        previous_state=None,
        transitioned_recently=False,
        stale_returning=False,
        propensity='open',
        active_window=None,
    ), now=now)


@pytest.mark.parametrize('failure', ['missing_callback', 'push_failure'])
async def test_context_prompt_consumes_signal_without_successful_callback(monkeypatch, context_tracker, failure):
    tracker = context_tracker
    _tick_context_state(tracker, 'focused_work', now=1000.0)
    reader = AsyncMock(return_value={KEY: True})
    monkeypatch.setattr(preferences, 'aload_global_conversation_settings', reader)
    callback = AsyncMock(side_effect=OSError('push unavailable')) if failure == 'push_failure' else None
    tracker.set_context_prompt_callback(callback)

    await tracker._drain_context_prompt()

    assert tracker._context_prompt_pending is None
    if callback is None:
        reader.assert_not_awaited()
    else:
        callback.assert_awaited_once_with('work')
        reader.assert_awaited_once_with(strict=True)


@pytest.mark.parametrize('state,context', [('focused_work', 'work'), ('gaming', 'play')])
@pytest.mark.parametrize('during_read', ['unchanged', 'leave', 'reset'])
async def test_context_prompt_rechecks_signal_after_settings_read(
    monkeypatch, context_tracker, state, context, during_read,
):
    tracker = context_tracker
    _tick_context_state(tracker, state, now=1000.0)
    read_started = asyncio.Event()
    release_read = asyncio.Event()

    async def read_settings(**kwargs):
        read_started.set()
        await release_read.wait()
        return {KEY: True}

    reader = AsyncMock(side_effect=read_settings)
    monkeypatch.setattr(preferences, 'aload_global_conversation_settings', reader)
    drain = asyncio.create_task(tracker._drain_context_prompt())
    try:
        await asyncio.wait_for(read_started.wait(), 3)
        if during_read == 'reset':
            tracker.reset_context_prompt_baseline()
        else:
            _tick_context_state(tracker, state if during_read == 'unchanged' else 'idle', now=1020.0)
    finally:
        release_read.set()
        await asyncio.wait_for(drain, 3)

    assert tracker._context_prompt_pending is None
    reader.assert_awaited_once_with(strict=True)
    if during_read == 'unchanged':
        tracker._on_context_prompt.assert_awaited_once_with(context)
    else:
        tracker._on_context_prompt.assert_not_awaited()


@pytest.mark.parametrize('reset', [False, True])
async def test_context_prompt_preserves_replacement_during_settings_read(monkeypatch, context_tracker, reset):
    tracker = context_tracker
    _tick_context_state(tracker, 'focused_work', now=1000.0)
    original = tracker._context_prompt_pending
    read_started = asyncio.Event()
    release_read = asyncio.Event()

    async def read_settings(**kwargs):
        read_started.set()
        await release_read.wait()
        return {KEY: True}

    monkeypatch.setattr(preferences, 'aload_global_conversation_settings', read_settings)
    drain = asyncio.create_task(tracker._drain_context_prompt())
    try:
        await asyncio.wait_for(read_started.wait(), 3)
        if reset:
            tracker.reset_context_prompt_baseline()
            # A new session can observe the same state at the same timestamp.
            _tick_context_state(tracker, 'focused_work', now=1000.0)
        else:
            _tick_context_state(tracker, 'gaming', now=1020.0)
        replacement = tracker._context_prompt_pending
        assert replacement is not None and replacement is not original
        if reset:
            assert replacement == original
    finally:
        release_read.set()
        await asyncio.wait_for(drain, 3)

    tracker._on_context_prompt.assert_not_awaited()
    assert tracker._context_prompt_pending is replacement
    await tracker._drain_context_prompt()
    tracker._on_context_prompt.assert_awaited_once_with('work' if reset else 'play')
    assert tracker._context_prompt_pending is None
    await tracker._drain_context_prompt()
    assert tracker._on_context_prompt.await_count == 1


async def test_concurrent_context_prompt_drains_deliver_once(monkeypatch, context_tracker):
    tracker = context_tracker
    _tick_context_state(tracker, 'focused_work', now=1000.0)
    read_started = asyncio.Event()
    release_read = asyncio.Event()
    push_started = asyncio.Event()
    release_push = asyncio.Event()

    async def read_settings(**kwargs):
        read_started.set()
        await release_read.wait()
        return {KEY: True}

    async def push(context):
        push_started.set()
        await release_push.wait()

    monkeypatch.setattr(preferences, 'aload_global_conversation_settings', read_settings)
    tracker.set_context_prompt_callback(AsyncMock(side_effect=push))
    drains = [asyncio.create_task(tracker._drain_context_prompt()) for _ in range(2)]
    try:
        await asyncio.wait_for(read_started.wait(), 3)
        release_read.set()
        await asyncio.wait_for(push_started.wait(), 3)
    finally:
        release_read.set()
        release_push.set()
        await asyncio.wait_for(asyncio.gather(*drains), 3)

    tracker._on_context_prompt.assert_awaited_once_with('work')
    assert tracker._context_prompt_pending is None


async def test_context_prompt_preserves_new_signal_during_callback(monkeypatch, context_tracker):
    tracker = context_tracker
    _tick_context_state(tracker, 'focused_work', now=1000.0)
    push_started = asyncio.Event()
    release_push = asyncio.Event()

    async def push(context):
        push_started.set()
        await release_push.wait()

    monkeypatch.setattr(preferences, 'aload_global_conversation_settings', AsyncMock(return_value={KEY: True}))
    tracker.set_context_prompt_callback(AsyncMock(side_effect=push))
    drain = asyncio.create_task(tracker._drain_context_prompt())
    try:
        await asyncio.wait_for(push_started.wait(), 3)
        _tick_context_state(tracker, 'gaming', now=1020.0)
        replacement = tracker._context_prompt_pending
    finally:
        release_push.set()
        await asyncio.wait_for(drain, 3)

    tracker._on_context_prompt.assert_awaited_once_with('work')
    assert tracker._context_prompt_pending is replacement
    await tracker._drain_context_prompt()
    assert tracker._on_context_prompt.await_args_list == [call('work'), call('play')]
    assert tracker._context_prompt_pending is None
