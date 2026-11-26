"""Exercise the actual import adapter with bounded ASGI bodies and isolated N2 jobs."""

import asyncio
import json
import threading
from contextlib import contextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from main_routers import model_market_router as api
from main_routers.system_router import _shared
from services.model_workshop import runtime
from tests.unit.test_model_workshop_install import bundle_path, config as config

AUTH = {"Origin": "http://127.0.0.1", "X-CSRF-Token": "test-import-token"}


@pytest.fixture
def import_app(config, monkeypatch):
    monkeypatch.setattr(api, "get_config_manager", lambda: config)
    monkeypatch.setattr(_shared, "AUTOSTART_CSRF_TOKEN", AUTH["X-CSRF-Token"])
    monkeypatch.setattr(runtime, "_services", {})
    monkeypatch.setattr(runtime, "_stopped", False)
    app = FastAPI()
    app.include_router(api.router)
    app.add_middleware(api.ModelImportBodyGuard, config_getter=lambda: config)
    yield app
    # Drain only this test's actual owners; never touch the application's root.
    for service in list(runtime._services.values()):
        service.close()


@contextmanager
def local_client(app):
    with TestClient(
        app, base_url="http://127.0.0.1", client=("127.0.0.1", 40000)
    ) as client:
        yield client


def multipart(raw=b"model", *, name="file", end=True):
    body = (
        b'--test\r\nContent-Disposition: form-data; name="'
        + name.encode()
        + b'"; filename="Avatar.neko-model.zip"\r\n\r\n'
        + raw
    )
    return body + (b"\r\n--test--\r\n" if end else b"")


async def asgi_call(app, body, *, headers=None, client="127.0.0.1", disconnect=False):
    received, sent = [], []
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/model-market/imports",
        "raw_path": b"/api/model-market/imports",
        "query_string": b"",
        "client": (client, 40000),
        "server": ("127.0.0.1", 80),
        "headers": [
            (k.lower().encode(), v.encode()) for k, v in (headers or {}).items()
        ],
    }

    async def receive():
        received.append(True)
        if disconnect:
            return {"type": "http.disconnect"}
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        sent.append(message)

    await app(scope, receive, send)
    return received, sent


@pytest.mark.parametrize(
    "headers,peer,status",
    [
        ({}, "127.0.0.1", 403),
        (
            {**AUTH, "Content-Type": "multipart/form-data; boundary=test"},
            "203.0.113.1",
            403,
        ),
        ({**AUTH, "Origin": "https://foreign.example"}, "127.0.0.1", 403),
        ({**AUTH, "Content-Type": "application/json"}, "127.0.0.1", 415),
    ],
)
def test_preflight_rejects_without_receiving_or_creating_storage(
    import_app, config, headers, peer, status
):
    read, sent = asyncio.run(
        asgi_call(import_app, b"never read", headers=headers, client=peer)
    )
    assert read == [] and sent[0]["status"] == status
    assert not (config.app_docs_dir / "model_workshop").exists()


@pytest.mark.parametrize("kind", ["live2d", "vrm", "pmx", "pmd"])
def test_real_package_import_status_and_installed_projection(
    import_app, config, tmp_path, kind
):
    path, _ = bundle_path(tmp_path, kind)
    with local_client(import_app) as client:
        response = client.post(
            "/api/model-market/imports",
            files={"file": (path.name, path.read_bytes())},
            headers=AUTH,
        )
        assert response.status_code == 202, response.text
        attempt = response.json()["task_id"]
        service = runtime.get_service(config)
        assert service.wait(attempt, 10)["phase"] == "completed"
        result = client.get("/api/model-market/tasks/" + attempt).json()
        assert (
            result["phase"] == "completed" and result["result"]["status"] == "installed"
        )
        assert result["result"]["display_name"] == "Native fixture"
        assert str(config.app_docs_dir) not in json.dumps(result)
        installed = client.get("/api/model-market/installed").json()
        assert (
            installed["items"][0]["package_sha256"]
            == result["result"]["package_sha256"]
        )
        assert installed["items"][0]["url"].startswith(
            "/user_" + ("mmd" if kind in ("pmx", "pmd") else kind) + "/"
        )
        # The adapter imports bytes, not a market identity or a character card.
        assert not any(
            key in result["result"] for key in ("item_id", "market_id", "characters")
        )
        assert client.get("/api/model-market/tasks/" + "0" * 32).status_code == 404
        assert (
            client.post("/api/model-market/tasks/" + attempt + "/cancel").status_code
            == 403
        )
        cancelled = client.post(
            "/api/model-market/tasks/" + attempt + "/cancel", headers=AUTH
        )
        assert (
            cancelled.json()["phase"] == "completed"
        )  # Never claim a committed model was cancelled.


@pytest.mark.parametrize(
    "body,content_type",
    [
        (multipart(end=False), "multipart/form-data; boundary=test"),
        (multipart(name="url"), "multipart/form-data; boundary=test"),
        (
            b'--test\r\nContent-Disposition: form-data; name="url"\r\n\r\nhttps://foreign.example\r\n--test--\r\n',
            "multipart/form-data; boundary=test",
        ),
        (multipart(), "multipart/form-data"),
        (b"malformed", "multipart/form-data; boundary=test"),
        (
            multipart().replace(b"--test--", b"--test")
            + multipart().removeprefix(b"--test\r\n"),
            "multipart/form-data; boundary=test",
        ),
        (
            b"--test\r\nX-Large: " + b"x" * 17000 + b"\r\n\r\n--test--\r\n",
            "multipart/form-data; boundary=test",
        ),
    ],
)
def test_incomplete_extra_field_and_malformed_parts_never_become_jobs(
    import_app, config, body, content_type
):
    with local_client(import_app) as client:
        response = client.post(
            "/api/model-market/imports",
            content=body,
            headers={**AUTH, "Content-Type": content_type},
        )
        assert response.status_code == 400, response.text
        assert not runtime.get_service(config)._tasks


@pytest.mark.parametrize("declared", [None, "1", "5000"])
def test_counts_actual_stream_even_without_or_with_false_content_length(
    import_app, config, declared, monkeypatch
):
    # Exercise the production receive limiter without allocating a 500 MiB test body.
    guard = import_app.build_middleware_stack().app
    guard._bounded.max_multipart_body_bytes = 128
    headers = {**AUTH, "Content-Type": "multipart/form-data; boundary=test"}
    if declared is not None:
        headers["Content-Length"] = declared
    _, sent = asyncio.run(asgi_call(guard, multipart(b"x" * 512), headers=headers))
    assert sent[0]["status"] == 413
    assert not runtime.get_service(config)._tasks


def test_storage_selection_gate_is_checked_before_parser_reads(import_app, config):
    config.blocked = True
    read, sent = asyncio.run(
        asgi_call(
            import_app,
            multipart(),
            headers={**AUTH, "Content-Type": "multipart/form-data; boundary=test"},
        )
    )
    assert read == [] and sent[0]["status"] == 503
    assert not (config.app_docs_dir / "model_workshop").exists()


def test_disconnected_multipart_does_not_accept_a_task(import_app, config):
    _, sent = asyncio.run(
        asgi_call(
            import_app,
            b"",
            headers={**AUTH, "Content-Type": "multipart/form-data; boundary=test"},
            disconnect=True,
        )
    )
    assert sent[0]["status"] == 400
    assert not runtime.get_service(config)._tasks


def test_parser_cancellation_drains_spool_write_before_closing(monkeypatch):
    from io import BytesIO

    entered, release = threading.Event(), threading.Event()

    class SlowSpool(BytesIO):
        def write(self, data):
            entered.set()
            assert release.wait(5)
            assert not self.closed
            return super().write(data)

    spool = SlowSpool()
    monkeypatch.setattr("starlette.formparsers.SpooledTemporaryFile", lambda **_: spool)

    async def run():
        async def stream():
            yield multipart(b"private package bytes")

        from starlette.datastructures import Headers

        parser = api._PackageFormParser(
            Headers({"Content-Type": "multipart/form-data; boundary=test"}), stream()
        )
        task = asyncio.create_task(parser.parse())
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done() and not spool.closed
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    try:
        asyncio.run(run())
        assert spool.closed
    finally:
        release.set()
        spool.close()


def test_two_parsers_bound_concurrency_and_third_rejects_before_receive(
    import_app, config
):
    async def run():
        entered, release = 0, asyncio.Event()
        both = asyncio.Event()

        async def hold(scope, receive, send):
            nonlocal entered
            entered += 1
            if entered == 2:
                both.set()
            await release.wait()
            await send({"type": "http.response.start", "status": 204, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        guard = api.ModelImportBodyGuard(hold, lambda: config)
        headers = {**AUTH, "Content-Type": "multipart/form-data; boundary=test"}
        tasks = [
            asyncio.create_task(asgi_call(guard, b"", headers=headers))
            for _ in range(2)
        ]
        try:
            await asyncio.wait_for(both.wait(), 5)
            read, sent = await asgi_call(guard, multipart(), headers=headers)
            assert read == [] and sent[0]["status"] == 409
        finally:
            release.set()
            await asyncio.gather(*tasks)
        assert not guard._slots.locked()

    asyncio.run(run())


def test_unrelated_json_body_retains_its_existing_policy(config):
    # This adapter must not silently apply its internal middleware's default
    # 16 MiB cap to the existing larger external-memory JSON import endpoints.
    from starlette.responses import Response

    async def endpoint(scope, receive, send):
        await Response(status_code=204)(scope, receive, send)

    async def run():
        guard = api.ModelImportBodyGuard(endpoint, lambda: config)
        sent = []

        async def receive():
            raise AssertionError("the body should not be read here")

        async def send(message):
            sent.append(message)

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/memory/external_import/preview",
            "headers": [
                (b"content-length", b"30000000"),
                (b"content-type", b"application/json"),
            ],
        }
        await guard(scope, receive, send)
        assert sent[0]["status"] == 204
        assert not (config.app_docs_dir / "model_workshop").exists()

    asyncio.run(run())


def test_unknown_os_lock_failure_rejects_before_body_and_does_not_leak_paths(
    import_app, config, monkeypatch
):
    import portalocker

    def unknown(_config):
        raise portalocker.exceptions.LockException("/private/sensitive/runtime/locks")

    monkeypatch.setattr(api, "get_service", unknown)
    read, sent = asyncio.run(
        asgi_call(
            import_app,
            multipart(),
            headers={**AUTH, "Content-Type": "multipart/form-data; boundary=test"},
        )
    )
    assert read == [] and sent[0]["status"] == 503
    assert b"/private/sensitive" not in b"".join(
        message.get("body", b"") for message in sent
    )


@pytest.mark.parametrize("suffix", ["", "/select", "/cancel", "/apply", "/ready", "/invalidate"])
def test_connected_commands_preserve_preflight_error_codes_without_consuming_body(import_app, monkeypatch, suffix):
    monkeypatch.setattr(api, "_preflight", lambda scope: api.JSONResponse({"error_code": "csrf_validation_failed"}, status_code=403))
    path = "/api/model-market/launch" + (("/" + "r" * 32 + suffix) if suffix else "")
    with local_client(import_app) as client:
        response = client.post(path, content=b"malformed body", headers={"Content-Type": "application/json"})
    assert response.status_code == 403 and response.json()["error_code"] == "csrf_validation_failed"
