from __future__ import annotations

import pytest
from httpx import AsyncClient

from plugin.server.routes import runs as runs_route_module


@pytest.mark.plugin_integration
@pytest.mark.asyncio
async def test_runs_create_upload_uses_relative_base_url(plugin_async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def _fake_create_upload_session(*, run_id: str, base_url: str, body: dict[str, object] | None) -> dict[str, object]:
        captured["run_id"] = run_id
        captured["base_url"] = base_url
        captured["body"] = body
        return {
            "upload_id": "u1",
            "blob_id": "b1",
            "upload_url": "/uploads/u1",
            "blob_url": f"/runs/{run_id}/blobs/b1",
        }

    monkeypatch.setattr(runs_route_module.run_service, "create_upload_session", _fake_create_upload_session)

    response = await plugin_async_client.post(
        "/runs/run-123/uploads",
        headers={"host": "attacker.example"},
        json={"filename": "hello.bin", "max_bytes": 1024},
    )
    assert response.status_code == 200
    assert response.json()["upload_url"].startswith("/")
    assert captured["run_id"] == "run-123"
    assert captured["base_url"] == ""

