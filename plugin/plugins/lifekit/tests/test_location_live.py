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


async def test_real_providers_do_not_silently_resolve_ambiguous_jilin() -> None:
    resolver = LocationResolver(
        open_meteo=open_meteo_candidates,
        nominatim=nominatim_candidates,
    )

    exact = await resolver.resolve(
        LocationRequest(text="吉林市", purpose=LocationPurpose.NEARBY)
    )
    ambiguous = await resolver.resolve(
        LocationRequest(text="吉林", purpose=LocationPurpose.NEARBY)
    )

    assert exact.status is LocationStatus.RESOLVED
    assert exact.location is not None and exact.location.country_code == "CN"
    assert ambiguous.status is LocationStatus.AMBIGUOUS
    assert {item.country_code for item in ambiguous.candidates} >= {"CN", "TW"}
