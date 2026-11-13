"""Persisted opt-out and delivery-time enforcement for screen-sharing dialogs."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from main_logic.activity.tracker import UserActivityTracker
from main_routers.config_router import preferences as routes
from utils import preferences, token_tracker

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
