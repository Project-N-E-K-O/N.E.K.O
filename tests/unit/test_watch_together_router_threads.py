import threading
from types import SimpleNamespace

import pytest

from main_routers import watch_together_router as router


@pytest.mark.asyncio
async def test_library_construction_and_history_query_run_off_event_loop(monkeypatch):
    event_thread = threading.get_ident()
    def checked(value):
        assert threading.get_ident() != event_thread
        return value
    def library():
        checked(None)
        return SimpleNamespace(history=lambda: checked(["analysis"]), watches=lambda: checked(["watch"]))
    monkeypatch.setattr(router, "application_library", library)
    assert await router.history() == {"analyses": ["analysis"], "watches": ["watch"]}

@pytest.mark.asyncio
async def test_watches_endpoint_does_not_read_analysis_history(monkeypatch):
    event_thread = threading.get_ident()
    def page(limit, offset):
        assert threading.get_ident() != event_thread
        assert (limit, offset) == (50, 100)
        return {'watches': ['watch'], 'next_offset': 150}
    monkeypatch.setattr(router, 'application_library', lambda: SimpleNamespace(watch_page=page))
    assert await router.watches(50, 100) == {'watches': ['watch'], 'next_offset': 150}
