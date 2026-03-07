from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.plugin_integration
@pytest.mark.asyncio
async def test_metrics_history_invalid_start_time_returns_400(plugin_async_client: AsyncClient) -> None:
    response = await plugin_async_client.get(
        "/plugin/metrics/demo/history",
        params={"start_time": "not-a-time", "limit": 10},
    )

    assert response.status_code == 400
    payload = response.json()
    assert isinstance(payload.get("detail"), str)
