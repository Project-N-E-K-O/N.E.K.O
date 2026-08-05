from __future__ import annotations

import asyncio
import hashlib

import httpx
import pytest
from fastapi import FastAPI

from knowledge.api import canonical_pack_bytes
from plugin.server.routes import knowledge_market as module


def _pack() -> dict:
    return {
        "schema_version": 1,
        "pack_id": "e2e-pack",
        "collection_id": "meme",
        "source": {"name": "E2E", "homepage": "", "license": "CC0-1.0"},
        "entries": [
            {
                "title": "e2e fixture",
                "terms": {"alias": [], "recognition": []},
                "tags": [],
                "summary": "",
                "content": "fixture content",
            }
        ],
    }


@pytest.mark.asyncio
async def test_subscribe_poll_and_local_handoff(monkeypatch):
    raw = canonical_pack_bytes(_pack())
    captured = {}

    async def fake_download(_url):
        return raw

    async def fake_main(method, path, **kwargs):
        captured.update(method=method, path=path, **kwargs)
        return {
            "ok": True,
            "pack_id": "e2e-pack",
            "collection": "meme",
            "entries": 1,
        }

    async def no_report(*_args):
        return None

    monkeypatch.setattr(module, "get_bridge_token", lambda: "e2e-token")
    monkeypatch.setattr(module, "_download_artifact", fake_download)
    monkeypatch.setattr(module, "_main_request", fake_main)
    monkeypatch.setattr(module, "_report_subscription_best_effort", no_report)
    module._tasks.clear()
    app = FastAPI()
    app.include_router(module.router)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://127.0.0.1:48916",
    ) as client:
        response = await client.post(
            "/market/knowledge/subscribe",
            headers={"Authorization": "Bearer e2e-token"},
            json={
                "package_id": 9,
                "remote_id": "knowledge/e2e-pack",
                "pack_id": "e2e-pack",
                "version": "1.0.0",
                "channel": "stable",
                "artifact_url": (
                    "https://github.com/example/repo/releases/download/v1/"
                    "e2e.neko-knowledge.json"
                ),
                "artifact_sha256": hashlib.sha256(raw).hexdigest(),
            },
        )
        assert response.status_code == 200
        task_id = response.json()["task_id"]

        task = {}
        for _ in range(200):
            polled = await client.get(
                f"/market/knowledge/tasks/{task_id}",
                headers={"Authorization": "Bearer e2e-token"},
            )
            task = polled.json()
            if task.get("status") in {"completed", "failed"}:
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail(f"subscription task did not settle: {task}")

    assert task["status"] == "completed"
    assert task["progress"] == 1.0
    assert captured["path"] == "subscriptions/apply"
    assert captured["json"]["pack"]["pack_id"] == "e2e-pack"


@pytest.mark.asyncio
async def test_process_restart_state_returns_task_not_found(monkeypatch):
    monkeypatch.setattr(module, "get_bridge_token", lambda: "e2e-token")
    module._tasks.clear()
    app = FastAPI()
    app.include_router(module.router)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://127.0.0.1:48916",
    ) as client:
        response = await client.get(
            "/market/knowledge/tasks/from-a-previous-process",
            headers={"Authorization": "Bearer e2e-token"},
        )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_knowledge_endpoints_reject_legacy_query_token(monkeypatch):
    monkeypatch.setattr(module, "get_bridge_token", lambda: "e2e-token")
    app = FastAPI()
    app.include_router(module.router)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://127.0.0.1:48916",
    ) as client:
        response = await client.get(
            "/market/knowledge/tasks/fixture",
            params={"token": "e2e-token"},
        )

    assert response.status_code == 403
