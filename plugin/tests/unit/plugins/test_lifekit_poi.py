from __future__ import annotations

import pytest

from plugin.plugins.lifekit._poi import POIService


class _FailingProvider:
    name = "broken"

    async def search(self, *_args, **_kwargs):
        raise RuntimeError("upstream down")


class _OsmProvider:
    name = "osm"

    async def search(self, *_args, **_kwargs):
        from plugin.plugins.lifekit._poi import POIItem

        return [POIItem(name="Cafe", lat=31.2, lon=121.5)]


@pytest.mark.asyncio
async def test_poi_service_reports_provider_errors() -> None:
    service = POIService({})
    service._providers = [_FailingProvider()]

    result = await service.search("coffee", 31.2, 121.5)

    assert result.items == []
    assert "broken" in result.error
    assert "provider error" in result.error
    assert "upstream down" not in result.error


@pytest.mark.asyncio
async def test_poi_service_rejects_invalid_radius_and_limit() -> None:
    service = POIService({})

    with pytest.raises(ValueError, match="radius"):
        await service.search("coffee", 31.2, 121.5, radius=0)
    with pytest.raises(ValueError, match="limit"):
        await service.search("coffee", 31.2, 121.5, limit=0)


@pytest.mark.asyncio
async def test_poi_service_converts_osm_coordinates_to_gcj02() -> None:
    service = POIService({})
    service._providers = [_OsmProvider()]

    result = await service.search("coffee", 31.2, 121.5)

    assert result.provider == "osm"
    assert result.items
    assert result.items[0].lat != 31.2
    assert result.items[0].lon != 121.5
