from __future__ import annotations

import asyncio

import pytest

from plugin.plugins.lifekit._nearby_discovery import (
    DiscoveryRequest,
    NearbyDiscovery,
    SearchCenter,
)
from plugin.plugins.lifekit._poi import POIItem, POIService


@pytest.mark.asyncio
async def test_discovery_caps_total_searches_and_global_concurrency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = POIService({})
    active = 0
    max_active = 0
    calls: list[tuple[str, float]] = []

    async def fake_search(
        query: str,
        lat: float,
        lon: float,
        radius: int = 3000,
        limit: int = 10,
    ):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        calls.append((query, lat))
        await asyncio.sleep(0.01)
        active -= 1
        from plugin.plugins.lifekit._poi import POIResult

        return POIResult(
            query=query,
            items=[POIItem(name=f"{query}-{lat}", lat=lat, lon=lon)],
            provider="fake",
        )

    monkeypatch.setattr(service, "search", fake_search)
    discovery = NearbyDiscovery(service)

    results = await discovery.discover(
        DiscoveryRequest(
            search_terms=("商场", "百货", "书店", "咖啡馆"),
            radius=3000,
        ),
        (
            SearchCenter(31.1, 121.1),
            SearchCenter(31.2, 121.2),
            SearchCenter(31.3, 121.3),
        ),
    )

    assert len(results) == 3
    assert len(calls) == 8
    assert {query for query, _ in calls} == {"商场", "百货", "书店"}
    assert [result.searched_terms for result in results] == [
        ("商场", "百货", "书店"),
        ("商场", "百货", "书店"),
        ("商场", "百货"),
    ]
    assert max_active <= 4


@pytest.mark.asyncio
async def test_discovery_timeout_is_returned_as_a_result_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = POIService({})

    async def slow_search(*args: object, **kwargs: object):
        await asyncio.sleep(0.05)
        raise AssertionError("the discovery timeout should cancel this search")

    monkeypatch.setattr(service, "search", slow_search)
    discovery = NearbyDiscovery(service, timeout_seconds=0.01)

    results = await discovery.discover(
        DiscoveryRequest(search_terms=("商场",), radius=3000),
        (SearchCenter(31.1, 121.1),),
    )

    assert len(results) == 1
    assert results[0].error == "nearby discovery timed out"


@pytest.mark.asyncio
async def test_discovery_never_exceeds_eight_searches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = POIService({})
    call_count = 0

    async def fake_search(*args: object, **kwargs: object):
        nonlocal call_count
        call_count += 1
        from plugin.plugins.lifekit._poi import POIResult

        return POIResult(query="商场", provider="fake")

    monkeypatch.setattr(service, "search", fake_search)
    results = await NearbyDiscovery(service).discover(
        DiscoveryRequest(search_terms=("商场", "书店"), radius=3000),
        tuple(SearchCenter(30.0 + index, 120.0) for index in range(9)),
    )

    assert len(results) == 9
    assert call_count == 8
    assert results[-1].error == "nearby discovery search budget exhausted"
