from __future__ import annotations

import pytest

from plugin.plugins.lifekit._poi import POIService


class _FailingProvider:
    name = "broken"

    async def search(self, *_args, **_kwargs):
        raise RuntimeError("upstream down")


@pytest.mark.asyncio
async def test_poi_service_reports_provider_errors() -> None:
    service = POIService({})
    service._providers = [_FailingProvider()]

    result = await service.search("coffee", 31.2, 121.5)

    assert result.items == []
    assert "broken" in result.error
    assert "provider error" in result.error
    assert "upstream down" not in result.error
