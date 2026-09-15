import threading
from types import SimpleNamespace

import pytest

from main_routers import watch_together_router as router


@pytest.mark.asyncio
@pytest.mark.parametrize('cancelled', [False, True])
async def test_discovery_transport_failure_and_cancellation(monkeypatch, cancelled):
    import asyncio
    from unittest.mock import AsyncMock
    from fastapi import HTTPException
    from main_logic.watch_together import discovery
    failure = asyncio.CancelledError() if cancelled else ConnectionError('upstream offline')
    monkeypatch.setattr(discovery, 'discover', AsyncMock(side_effect=failure))
    request = SimpleNamespace(headers={}, json=AsyncMock(return_value={'topic': 'cats'}))
    with pytest.raises(asyncio.CancelledError if cancelled else HTTPException) as caught:
        await router.discover_video(request)
    if not cancelled:
        assert caught.value.status_code == 502
        assert caught.value.detail == 'Video search unavailable'


@pytest.mark.asyncio
async def test_prepare_metadata_outage_is_controlled(monkeypatch):
    from unittest.mock import AsyncMock
    from fastapi import HTTPException
    from main_routers import shared_state
    from main_logic.watch_together import discovery
    monkeypatch.setattr(shared_state, 'get_config_manager', lambda: SimpleNamespace(load_characters=lambda: {}))
    monkeypatch.setattr(shared_state, 'get_session_manager', lambda: {'cat': object()})
    monkeypatch.setattr(discovery, 'inspect_video', AsyncMock(side_effect=TimeoutError()))
    request = SimpleNamespace(headers={}, json=AsyncMock(return_value={'lanlan_name': 'cat', 'url': 'BV1GJ411x7h7'}))
    with pytest.raises(HTTPException) as caught:
        await router.prepare_video(request)
    assert caught.value.status_code == 502


@pytest.mark.asyncio
async def test_library_construction_and_history_query_run_off_event_loop(monkeypatch):
    event_thread = threading.get_ident()
    def checked(value):
        assert threading.get_ident() != event_thread
        return value
    def library():
        checked(None)
        return SimpleNamespace(history_page=lambda limit, offset: checked({"analyses": ["analysis"], "next_offset": None}), watches=lambda: checked(["watch"]))
    monkeypatch.setattr(router, "application_library", library)
    assert await router.history(50, 0) == {"analyses": ["analysis"], "watches": ["watch"], "next_offset": None}

@pytest.mark.asyncio
async def test_watches_endpoint_does_not_read_analysis_history(monkeypatch):
    event_thread = threading.get_ident()
    def page(limit, offset):
        assert threading.get_ident() != event_thread
        assert (limit, offset) == (50, 100)
        return {'watches': ['watch'], 'next_offset': 150}
    monkeypatch.setattr(router, 'application_library', lambda: SimpleNamespace(watch_page=page))
    assert await router.watches(50, 100) == {'watches': ['watch'], 'next_offset': 150}


@pytest.mark.asyncio
@pytest.mark.parametrize('filename,media_type,attachment', [
    ('payload.html', 'application/octet-stream', True),
    ('image.svg', 'application/octet-stream', True),
    ('video.mp4', 'video/mp4', False),
])
async def test_imported_resources_cannot_execute_inline(monkeypatch, tmp_path, filename, media_type, attachment):
    monkeypatch.setattr(router, 'application_library', lambda: SimpleNamespace(resource=lambda *args: tmp_path / 'object'))
    response = await router.media('job', 'version', filename)
    assert response.media_type == media_type
    assert ('attachment' in response.headers.get('content-disposition', '')) is attachment
    assert response.headers['x-content-type-options'] == 'nosniff'
    assert 'sandbox' in response.headers['content-security-policy']
