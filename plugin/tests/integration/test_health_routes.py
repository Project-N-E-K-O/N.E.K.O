from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.plugin_integration
@pytest.mark.asyncio
async def test_health_ok(plugin_async_client: AsyncClient) -> None:
    response = await plugin_async_client.get("/health")
    assert response.status_code == 200

    payload = response.json()
    assert payload["status"] == "ok"
    assert isinstance(payload.get("time"), str)

