from __future__ import annotations

import asyncio
import hashlib
import time

import httpx
import pytest
from fastapi import HTTPException

from knowledge.api import canonical_pack_bytes
from plugin.server.routes import knowledge_market as module
from tests.fake_clock import patch_module_clock


def _pack(pack_id: str = "fixture-pack") -> dict:
    return {
        "schema_version": 1,
        "pack_id": pack_id,
        "collection_id": "meme",
        "source": {"name": "Fixture", "homepage": "", "license": "CC0-1.0"},
        "entries": [
            {
                "title": "fixture",
                "terms": {"alias": [], "recognition": []},
                "tags": [],
                "summary": "",
                "content": "fixture content",
            }
        ],
    }


def _request(raw: bytes, *, pack_id: str = "fixture-pack"):
    return module.KnowledgeSubscribeRequest(
        package_id=7,
        remote_id=f"knowledge/{pack_id}",
        pack_id=pack_id,
        version="1.0.0",
        channel="stable",
        artifact_url=(
            "https://github.com/example/repo/releases/download/v1/"
            "fixture.neko-knowledge.json"
        ),
        artifact_sha256=hashlib.sha256(raw).hexdigest(),
    )


def _task() -> dict:
    return {
        "status": "pending",
        "stage": "pending",
        "progress": 0.0,
        "message": "",
        "result": None,
        "error": None,
        "error_code": None,
        "completed_at": None,
    }


@pytest.mark.asyncio
async def test_subscription_verifies_and_hands_off_to_main_server(monkeypatch):
    raw = canonical_pack_bytes(_pack())
    request = _request(raw)
    captured = {}

    async def fake_main(method, path, **kwargs):
        captured.update(method=method, path=path, **kwargs)
        return {"ok": True, "pack_id": "fixture-pack", "entries": 1}

    monkeypatch.setattr(module, "_download_artifact", lambda _url: _async_value(raw))
    monkeypatch.setattr(module, "_main_request", fake_main)
    monkeypatch.setattr(
        module,
        "_report_subscription_best_effort",
        lambda *_args: _async_value(None),
    )
    module._tasks["fixture"] = _task()

    await module._execute_subscription("fixture", request)

    assert module._tasks["fixture"]["status"] == "completed"
    assert captured["path"] == "subscriptions/apply"
    assert captured["json"]["subscription"]["provider"] == "plugin-market"
    assert captured["json"]["pack"]["pack_id"] == "fixture-pack"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raw", "request_pack_id", "error_code"),
    [
        (canonical_pack_bytes(_pack()), "fixture-pack", "artifact_hash_mismatch"),
        (canonical_pack_bytes(_pack()).rstrip(b"\n"), "fixture-pack", "invalid_artifact"),
        (canonical_pack_bytes(_pack("other-pack")), "fixture-pack", "package_identity_mismatch"),
    ],
)
async def test_subscription_rejects_invalid_artifacts(
    monkeypatch,
    raw,
    request_pack_id,
    error_code,
):
    request = _request(raw, pack_id=request_pack_id)
    if error_code == "artifact_hash_mismatch":
        request.artifact_sha256 = "0" * 64
    monkeypatch.setattr(module, "_download_artifact", lambda _url: _async_value(raw))
    module._tasks[error_code] = _task()

    await module._execute_subscription(error_code, request)

    assert module._tasks[error_code]["status"] == "failed"
    assert module._tasks[error_code]["error_code"] == error_code


@pytest.mark.asyncio
async def test_remote_report_failure_keeps_completed_local_install(monkeypatch):
    raw = canonical_pack_bytes(_pack())

    async def fake_main(*_args, **_kwargs):
        return {"ok": True, "pack_id": "fixture-pack", "entries": 1}

    async def report_failure(*_args):
        raise RuntimeError("market unavailable")

    monkeypatch.setattr(module, "_download_artifact", lambda _url: _async_value(raw))
    monkeypatch.setattr(module, "_main_request", fake_main)
    monkeypatch.setattr(module, "_report_subscription_best_effort", report_failure)
    module._tasks["report-failure"] = _task()

    await module._execute_subscription("report-failure", _request(raw))

    assert module._tasks["report-failure"]["status"] == "completed"


def test_artifact_url_requires_https_443_allowlist_and_suffix():
    allowed = (
        "https://github.com/example/repo/releases/download/v1/"
        "fixture.neko-knowledge.json"
    )
    module._validate_artifact_url(allowed, require_suffix=True)

    rejected = (
        "http://github.com/file.neko-knowledge.json",
        "https://github.com:444/file.neko-knowledge.json",
        "https://evil.example/file.neko-knowledge.json",
        "https://github.com/file.json",
    )
    for value in rejected:
        with pytest.raises(HTTPException):
            module._validate_artifact_url(value, require_suffix=True)


@pytest.mark.asyncio
async def test_download_validates_redirect_before_requesting_next_host(monkeypatch):
    requested = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(
            302,
            headers={"location": "https://evil.example/payload"},
            request=request,
        )

    _install_transport(monkeypatch, handler)

    with pytest.raises(module._KnowledgeTaskError) as exc_info:
        await module._download_artifact(
            "https://github.com/example/file.neko-knowledge.json"
        )

    assert exc_info.value.code == "unsafe_artifact_redirect"
    assert requested == ["https://github.com/example/file.neko-knowledge.json"]


@pytest.mark.asyncio
async def test_download_allows_verified_redirect_and_enforces_size(monkeypatch):
    raw = canonical_pack_bytes(_pack())

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "github.com":
            return httpx.Response(
                302,
                headers={
                    "location": (
                        "https://release-assets.githubusercontent.com/"
                        "fixture.neko-knowledge.json"
                    )
                },
                request=request,
            )
        return httpx.Response(200, content=raw, request=request)

    _install_transport(monkeypatch, handler)
    assert await module._download_artifact(
        "https://github.com/example/file.neko-knowledge.json"
    ) == raw

    monkeypatch.setattr(module, "MAX_PACK_BYTES", len(raw) - 1)
    with pytest.raises(module._KnowledgeTaskError) as exc_info:
        await module._download_artifact(
            "https://github.com/example/file.neko-knowledge.json"
        )
    assert exc_info.value.code == "artifact_too_large"


@pytest.mark.asyncio
async def test_unsubscribe_report_failure_does_not_undo_local_result(monkeypatch):
    async def fake_main(*_args, **_kwargs):
        return {"ok": True, "removed_entries": 1}

    async def report_failure(*_args):
        raise RuntimeError("market unavailable")

    monkeypatch.setattr(module, "get_bridge_token", lambda: "fixture-token")
    monkeypatch.setattr(module, "_main_request", fake_main)
    monkeypatch.setattr(module, "_report_unsubscribe_best_effort", report_failure)

    result = await module.unsubscribe_knowledge_package(
        module.KnowledgeUnsubscribeRequest(
            package_id=7,
            collection="meme",
            pack_id="fixture-pack",
        ),
        authorization="Bearer fixture-token",
    )

    assert result == {"ok": True, "removed_entries": 1}


def test_bridge_token_requires_bearer_header_and_handles_unicode(monkeypatch):
    monkeypatch.setattr(module, "get_bridge_token", lambda: "fixture-token")

    module._verify_bridge_token("Bearer fixture-token")
    for authorization in (None, "fixture-token", "Bearer 鐚猫"):
        with pytest.raises(HTTPException) as exc_info:
            module._verify_bridge_token(authorization)
        assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_subscribe_retains_background_task_until_completion(monkeypatch):
    release = asyncio.Event()

    async def wait_for_release(_task_id, _payload):
        await release.wait()

    monkeypatch.setattr(module, "get_bridge_token", lambda: "fixture-token")
    monkeypatch.setattr(module, "_execute_subscription", wait_for_release)
    module._tasks.clear()
    module._background_tasks.clear()

    result = await module.subscribe_knowledge_package(
        _request(canonical_pack_bytes(_pack())),
        authorization="Bearer fixture-token",
    )
    task = next(iter(module._background_tasks))
    assert result["task_id"] in module._tasks
    assert not task.done()

    release.set()
    await task
    await asyncio.sleep(0)
    assert module._background_tasks == set()


def test_completed_tasks_expire_after_one_hour(monkeypatch):
    now = time.time()
    module._tasks.clear()
    module._tasks.update(
        expired={"completed_at": now - 3601},
        retained={"completed_at": now - 3599},
        pending={"completed_at": None},
    )
    patch_module_clock(monkeypatch, module, time=lambda: now)

    module._cleanup_tasks()

    assert set(module._tasks) == {"retained", "pending"}


def test_knowledge_router_is_registered_before_market_bridge():
    from plugin.server.http_app import build_plugin_server_app

    paths = [route.path for route in build_plugin_server_app().routes]

    assert paths.index("/market/knowledge/subscribe") < paths.index("/market/install")


async def _async_value(value):
    return value


def _install_transport(monkeypatch, handler) -> None:
    real_async_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)

    def client_factory(**kwargs):
        return real_async_client(transport=transport, **kwargs)

    monkeypatch.setattr(module.httpx, "AsyncClient", client_factory)
