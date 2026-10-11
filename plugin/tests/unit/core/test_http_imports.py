from __future__ import annotations

import asyncio
import threading
from types import ModuleType

import pytest

from plugin.utils import http_imports as module

pytestmark = pytest.mark.plugin_unit


@pytest.mark.asyncio
async def test_first_http_backend_import_runs_off_event_loop(monkeypatch):
    owner = threading.current_thread()
    seen = []
    backend = ModuleType("httpx")
    monkeypatch.setattr(module, "_backend", None)

    def load(name):
        assert name == "httpx"
        seen.append(threading.current_thread())
        return backend

    monkeypatch.setattr(module, "import_module", load)
    assert await module.ensure_httpx() is backend
    assert await module.ensure_httpx() is backend
    assert module._backend is backend
    assert len(seen) == 1 and seen[0] is not owner


@pytest.mark.asyncio
async def test_backend_override_wins_while_first_import_is_in_flight(monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    monkeypatch.setattr(module, "_backend", None)
    imported = ModuleType("imported")
    override = ModuleType("override")

    def load(name):
        entered.set()
        assert release.wait(5)
        return imported

    monkeypatch.setattr(module, "import_module", load)
    task = asyncio.create_task(module.ensure_httpx())
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        module._backend = override
        release.set()
        assert await task is override
        assert module._backend is override
    finally:
        release.set()
        await task


@pytest.mark.asyncio
async def test_cancelled_import_does_not_leave_a_partial_backend(monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    backend = ModuleType("httpx")
    monkeypatch.setattr(module, "_backend", None)

    def load(name):
        entered.set()
        assert release.wait(5)
        return backend

    original = module.load_httpx

    def tracked():
        try:
            return original()
        finally:
            finished.set()

    monkeypatch.setattr(module, "import_module", load)
    monkeypatch.setattr(module, "load_httpx", tracked)
    task = asyncio.create_task(module.ensure_httpx())
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        release.set()
        assert await asyncio.to_thread(finished.wait, 5)
        assert await module.ensure_httpx() is backend
    finally:
        release.set()
        await asyncio.to_thread(finished.wait, 5)
