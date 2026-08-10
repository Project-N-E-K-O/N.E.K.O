from __future__ import annotations

import httpx
import pytest
from plugin.plugins.lifekit import _api


class _MalformedResponseClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def get(self, url: str, **_: object) -> httpx.Response:
        return httpx.Response(
            200,
            json=[],
            request=httpx.Request("GET", url),
        )


@pytest.mark.asyncio
async def test_forecast_rejects_non_object_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_api.httpx, "AsyncClient", lambda **_: _MalformedResponseClient())

    with pytest.raises(_api.ForecastError) as exc_info:
        await _api.fetch_forecast(31.2, 121.4)

    assert exc_info.value.cause == "api_error"


@pytest.mark.asyncio
async def test_air_quality_rejects_non_object_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_api.httpx, "AsyncClient", lambda **_: _MalformedResponseClient())

    with pytest.raises(_api.AirQualityError) as exc_info:
        await _api.fetch_air_quality(31.2, 121.4)

    assert exc_info.value.cause == "api_error"
