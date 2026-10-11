"""Exercise launcher callback registration against real FastAPI routers."""

import asyncio
import sys
from threading import Event
from types import SimpleNamespace

from fastapi import FastAPI
import pytest

import app
from launcher_core import runtime


@pytest.mark.parametrize("service", ["main", "memory", "agent"])
def test_launcher_callbacks_run_in_real_fastapi_lifespan(monkeypatch, service):
    application = FastAPI()
    lifecycle_events = []

    async def startup():
        lifecycle_events.append("startup")

    async def shutdown():
        lifecycle_events.append("shutdown")

    application.router.add_event_handler("startup", startup)
    application.router.add_event_handler("shutdown", shutdown)
    module = SimpleNamespace(app=application, set_start_config=lambda _config: None)
    monkeypatch.setitem(sys.modules, f"app.{service}_server", module)
    monkeypatch.setattr(app, f"{service}_server", module, raising=False)
    monkeypatch.setattr(runtime, "_apply_child_process_signal_policy", lambda: None)
    monkeypatch.setattr(runtime, "_reload_runtime_config_from_env", lambda: None)
    monkeypatch.setattr(runtime, "_disable_uvicorn_signal_handlers", lambda _server: None)
    monkeypatch.setattr(runtime, "register_child_graceful_stop_hook", lambda _hook: None)
    monkeypatch.setattr(runtime, "IS_FROZEN", False)

    served = []

    class Server:
        def __init__(self, config):
            self.config = config

        async def serve(self):
            async with self.config.app.router.lifespan_context(self.config.app):
                served.append(True)

        def run(self):
            asyncio.run(self.serve())

    import uvicorn

    monkeypatch.setattr(uvicorn, "Server", Server)
    ready, imported, complete = Event(), Event(), Event()
    try:
        getattr(runtime, f"run_{service}_server")(
            ready, import_event=imported, shutdown_complete_event=complete
        )
    finally:
        if service == "memory":
            loop = asyncio.get_event_loop()
            loop.close()
            asyncio.set_event_loop(None)

    assert served == [True]
    assert lifecycle_events == ["startup", "shutdown"]
    assert ready.is_set()
    assert imported.is_set()
    assert complete.is_set()
