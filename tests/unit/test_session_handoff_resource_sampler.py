"""Acceptance sampling must fail visibly instead of producing a partial report."""

import asyncio
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize("sampling_fails", [False, True])
async def test_acceptance_server_propagates_sampling_failure(monkeypatch, sampling_fails):
    script = Path(__file__).resolve().parents[2] / "scripts/run_session_handoff_server.py"
    spec = importlib.util.spec_from_file_location("handoff_acceptance_server", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    exit_requested = asyncio.Event()
    sampler_cancelled = asyncio.Event()
    server_stopped = asyncio.Event()

    class Server:
        def __init__(self, config):
            self._should_exit = False

        @property
        def should_exit(self):
            return self._should_exit

        @should_exit.setter
        def should_exit(self, value):
            self._should_exit = value
            if value:
                exit_requested.set()

        async def serve(self):
            if sampling_fails:
                await self.main_loop()
            else:
                await asyncio.sleep(0)
            server_stopped.set()

        async def main_loop(self):
            await exit_requested.wait()

    uvicorn = ModuleType("uvicorn")
    uvicorn.Server = Server
    uvicorn.Config = lambda *args, **kwargs: None
    main_server = ModuleType("app.main_server")
    main_server.app = object()
    main_server.set_start_config = lambda config: None
    config = ModuleType("config")
    config.MAIN_SERVER_PORT = 48911
    monkeypatch.setitem(sys.modules, "uvicorn", uvicorn)
    monkeypatch.setitem(sys.modules, "app.main_server", main_server)
    monkeypatch.setitem(sys.modules, "config", config)

    async def sample_resources(*args):
        if sampling_fails:
            raise OSError("resource sample write failed")
        try:
            await asyncio.Event().wait()
        finally:
            sampler_cancelled.set()

    monkeypatch.setattr(module, "sample_resources", sample_resources)
    args = SimpleNamespace(output=Path("unused.jsonl"), interval=0.1)
    if sampling_fails:
        with pytest.raises(OSError, match="resource sample write failed"):
            await asyncio.wait_for(module.serve(args), timeout=1)
        assert exit_requested.is_set()
    else:
        await asyncio.wait_for(module.serve(args), timeout=1)
        assert sampler_cancelled.is_set()
    assert server_stopped.is_set()

@pytest.mark.asyncio
async def test_sampling_failure_during_real_uvicorn_startup_cleans_lifespan(monkeypatch):
    import uvicorn

    script = Path(__file__).resolve().parents[2] / "scripts/run_session_handoff_server.py"
    spec = importlib.util.spec_from_file_location("handoff_acceptance_real_server", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    startup_entered = asyncio.Event()
    sampling_failed = asyncio.Event()
    shutdown_complete = asyncio.Event()
    servers = []
    actual_config = uvicorn.Config

    async def app(scope, receive, send):
        assert scope["type"] == "lifespan"
        assert (await receive())["type"] == "lifespan.startup"
        startup_entered.set()
        await sampling_failed.wait()
        await send({"type": "lifespan.startup.complete"})
        assert (await receive())["type"] == "lifespan.shutdown"
        shutdown_complete.set()
        await send({"type": "lifespan.shutdown.complete"})

    def configure(app, **kwargs):
        kwargs["port"] = 0
        return actual_config(app, lifespan="on", log_level="error", **kwargs)

    main_server = ModuleType("app.main_server")
    main_server.app = app
    main_server.set_start_config = lambda config: servers.append(config["server"])
    config = ModuleType("config")
    config.MAIN_SERVER_PORT = 0
    monkeypatch.setitem(sys.modules, "app.main_server", main_server)
    monkeypatch.setitem(sys.modules, "config", config)
    monkeypatch.setattr(uvicorn, "Config", configure)

    async def sample_resources(*args):
        await startup_entered.wait()
        sampling_failed.set()
        raise OSError("sampling failed during lifespan startup")

    monkeypatch.setattr(module, "sample_resources", sample_resources)
    try:
        with pytest.raises(OSError, match="sampling failed during lifespan startup"):
            await asyncio.wait_for(
                module.serve(SimpleNamespace(output=Path("unused.jsonl"), interval=0.1)),
                timeout=2,
            )
        assert shutdown_complete.is_set()
        assert all(listener.is_serving() is False for listener in servers[0].servers)
    finally:
        # Keep the failing baseline reproduction from leaking its real listener/lifespan.
        if servers and servers[0].started and not shutdown_complete.is_set():
            await servers[0].shutdown()
