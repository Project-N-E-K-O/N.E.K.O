"""Run the merged coordinator with real Uvicorn connections in an owned child."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


def _serve_fixture(result_path, scenario):
    import asyncio
    from contextlib import asynccontextmanager
    import socket
    import threading
    from types import SimpleNamespace

    from fastapi import FastAPI
    from fastapi.responses import PlainTextResponse
    import httpx
    import uvicorn
    from starlette.background import BackgroundTask

    import app
    from launcher_core import runtime

    events, errors = [], []
    ready, finished = threading.Event(), threading.Event()
    start_config = {}
    sockets = {}
    for name in ("Main", "Memory", "Agent"):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        sockets[name] = sock
    ports = {name: sock.getsockname()[1] for name, sock in sockets.items()}
    runtime.MAIN_SERVER_PORT = ports["Main"]
    runtime.MEMORY_SERVER_PORT = ports["Memory"]
    runtime.TOOL_SERVER_PORT = ports["Agent"]
    runtime.INSTANCE_ID = "merged-shutdown-fixture"
    runtime._reload_runtime_config_from_env = lambda: None
    runtime._publish_ready_runtime_record = lambda _mode: None
    runtime._persist_post_startup_root_state = lambda _config: None
    runtime.get_config_manager = lambda _name: None
    runtime._take_teardown_snapshot_once = lambda: events.append("snapshot")
    runtime.emit_frontend_event = lambda event, _payload: ready.set() if event == "startup_ready" else None
    if scenario == "stuck_lifespan":
        runtime.MERGED_SERVER_SHUTDOWN_TIMEOUTS = {"Main": .5, "Memory": 1, "Agent": 1}

    def make_application(name):
        @asynccontextmanager
        async def lifespan(_application):
            events.append("start:" + name)
            try:
                yield
            finally:
                if name == "Main" and scenario == "stuck_lifespan":
                    await asyncio.Event().wait()
                next_service = {"Main": "Memory", "Memory": "Agent"}.get(name)
                if next_service:
                    async with httpx.AsyncClient(trust_env=False) as client:
                        response = await client.get(f"http://127.0.0.1:{ports[next_service]}/health")
                        assert response.status_code == 200
                events.append("stop:" + name)

        application = FastAPI(lifespan=lifespan)

        @application.get("/health")
        async def health():
            return {"app": "N.E.K.O", "service": name.lower(), "instance_id": runtime.INSTANCE_ID}

        if name == "Main":
            @application.get("/hold")
            async def hold():
                async def finish_request():
                    try:
                        await asyncio.Event().wait()
                    finally:
                        events.append("stream:cancelled")
                return PlainTextResponse("open\n", background=BackgroundTask(finish_request))
        return application

    for name in sockets:
        module = SimpleNamespace(app=make_application(name), set_start_config=start_config.update)
        setattr(app, name.lower() + "_server", module)
        sys.modules["app." + name.lower() + "_server"] = module

    real_server = uvicorn.Server

    def server(config):
        name = next(name for name, port in ports.items() if port == config.port)
        instance = real_server(config)
        serve = instance.serve

        async def serve_bound():
            await serve(sockets=[sockets[name]])
        instance.serve = serve_bound
        return instance

    uvicorn.Server = server

    def request_shutdown():
        try:
            assert ready.wait(15), "servers never became ready"
            if scenario in {"idle", "stuck_lifespan"}:
                start_config["request_runtime_shutdown"](reason="desktop_owner_exit")
                return
            with httpx.Client(trust_env=False, timeout=6) as client:
                with client.stream("GET", f"http://127.0.0.1:{ports['Main']}/hold") as response:
                    assert next(response.iter_lines()) == "open"
                    events.append("stream:open")
                    start_config["request_runtime_shutdown"](reason="desktop_owner_exit")
                    finished.wait()
        except Exception as exc:
            errors.append(repr(exc))

    owner = threading.Thread(target=request_shutdown, daemon=True)
    owner.start()
    failure = None
    try:
        exit_code = runtime.run_merged_servers()
    except RuntimeError as exc:
        failure = str(exc)
        exit_code = 1
    finally:
        finished.set()
        owner.join(2)
        for sock in sockets.values():
            sock.close()
    Path(result_path).write_text(json.dumps({"exit_code": exit_code, "failure": failure, "events": events, "errors": errors}), encoding="utf-8")


@pytest.mark.parametrize("scenario", ["idle", "connection", "stuck_lifespan"])
def test_merged_shutdown_drains_connections_and_starts_its_budget(tmp_path, scenario):
    from tests.process_isolation import child_process_env

    result_path = tmp_path / "result.json"
    env = child_process_env(tmp_path / "runtime", dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2]), PYTHONUTF8="1"))
    try:
        process = subprocess.run(
            [sys.executable, __file__, "--serve", str(result_path), scenario],
            capture_output=True, text=True, encoding="utf-8", env=env, timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        pytest.fail(f"Merged fixture timed out: {exc.stdout!r}\n{exc.stderr!r}")
    assert process.returncode == 0, process.stdout + process.stderr
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert not result["errors"], result
    stops = [event for event in result["events"] if event.startswith("stop:")]
    if scenario != "stuck_lifespan":
        assert result["exit_code"] == 0, result
        assert result["failure"] is None
        if scenario == "connection":
            assert "stream:open" in result["events"]
            assert result["events"].index("stream:cancelled") < result["events"].index("stop:Main")
        assert stops == ["stop:Main", "stop:Memory", "stop:Agent"]
    else:
        assert result["exit_code"] == 1
        assert "Main:shutdown_timeout" in result["failure"]
        assert stops == ["stop:Memory", "stop:Agent"]


if __name__ == "__main__" and sys.argv[1:2] == ["--serve"]:
    _serve_fixture(sys.argv[2], sys.argv[3])
