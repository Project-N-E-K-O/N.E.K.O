"""Opt-in smoke checks against the real public geocoding providers."""

from __future__ import annotations

import os

import pytest

from plugin.plugins.lifekit._geocoders import (
    nominatim_candidates,
    open_meteo_candidates,
)
from plugin.plugins.lifekit._location import (
    LocationPurpose,
    LocationRequest,
    LocationResolver,
    LocationStatus,
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        os.environ.get("LIFEKIT_LIVE_TESTS") != "1",
        reason="set LIFEKIT_LIVE_TESTS=1 to call public geocoding providers",
    ),
]


async def test_real_providers_use_administrative_context_without_rewriting_query() -> None:
    resolver = LocationResolver(
        open_meteo=open_meteo_candidates,
        nominatim=nominatim_candidates,
    )

    jilin = await resolver.resolve(
        LocationRequest(text="吉林", purpose=LocationPurpose.NEARBY)
    )
    road = await resolver.resolve(
        LocationRequest(text="上海南京东路", purpose=LocationPurpose.NEARBY)
    )

    assert jilin.status is LocationStatus.RESOLVED
    assert jilin.location is not None and jilin.location.country_code == "CN"
    assert road.status is LocationStatus.RESOLVED
    assert road.location is not None and road.location.admin1 == "上海市"
