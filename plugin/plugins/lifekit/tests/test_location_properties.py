"""Generated invariants for location candidate selection."""

from __future__ import annotations

import asyncio

from hypothesis import given, settings, strategies as st

from plugin.plugins.lifekit._location import (
    LocationCandidate,
    LocationPurpose,
    LocationRequest,
    LocationResolver,
    LocationStatus,
)

_CJK_PLACE_NAMES = st.text(
    alphabet=st.characters(min_codepoint=0x4E00, max_codepoint=0x9FFF),
    min_size=2,
    max_size=6,
).filter(lambda value: not value.endswith(("市", "省", "区", "县", "州", "旗")))


@settings(max_examples=100, deadline=None)
@given(
    place_name=_CJK_PLACE_NAMES,
    candidate_order=st.permutations((0, 1, 2)),
)
def test_unique_administrative_match_beats_same_name_localities_for_any_place(
    place_name: str,
    candidate_order: tuple[int, int, int],
) -> None:
    candidates = [
        LocationCandidate(
            display_name=place_name,
            latitude=30.0,
            longitude=120.0,
            country_code="CN",
            precision="city",
            source="open_meteo",
        ),
        LocationCandidate(
            display_name=f"{place_name}市",
            latitude=40.0,
            longitude=-90.0,
            country_code="US",
            precision="locality",
            source="open_meteo",
        ),
        LocationCandidate(
            display_name=f"{place_name}市场社区",
            latitude=35.0,
            longitude=110.0,
            country_code="CN",
            precision="locality",
            source="open_meteo",
        ),
    ]
    ordered = [candidates[index] for index in candidate_order]

    async def geocoder(*_args: object, **_kwargs: object) -> list[LocationCandidate]:
        return ordered

    async def scenario():
        resolver = LocationResolver(open_meteo=geocoder, nominatim=geocoder)
        return await resolver.resolve(
            LocationRequest(text=place_name, purpose=LocationPurpose.WEATHER)
        )

    result = asyncio.run(scenario())

    assert result.status is LocationStatus.RESOLVED
    assert result.location is not None
    assert result.location.display_name == place_name
    assert result.location.source == "open_meteo"
    assert result.location.country_code == "CN"
    assert result.location.precision == "city"


@settings(max_examples=100, deadline=None)
@given(
    place_name=_CJK_PLACE_NAMES,
    candidate_order=st.permutations((0, 1)),
)
def test_equally_relevant_real_places_remain_ambiguous(
    place_name: str,
    candidate_order: tuple[int, int],
) -> None:
    candidates = [
        LocationCandidate(
            display_name=place_name,
            latitude=30.0,
            longitude=120.0,
            country_code="CN",
            precision="city",
            source="open_meteo",
        ),
        LocationCandidate(
            display_name=f"{place_name}市",
            latitude=40.0,
            longitude=-90.0,
            country_code="US",
            precision="city",
            source="open_meteo",
        ),
    ]
    ordered = [candidates[index] for index in candidate_order]

    async def geocoder(*_args: object, **_kwargs: object) -> list[LocationCandidate]:
        return ordered

    async def scenario():
        resolver = LocationResolver(open_meteo=geocoder, nominatim=geocoder)
        return await resolver.resolve(
            LocationRequest(text=place_name, purpose=LocationPurpose.WEATHER)
        )

    result = asyncio.run(scenario())

    assert result.status is LocationStatus.AMBIGUOUS
    assert len(result.candidates) == 2


@settings(max_examples=100, deadline=None)
@given(
    place_name=_CJK_PLACE_NAMES,
    candidate_order=st.permutations((0, 1)),
)
def test_exact_locality_beats_unrelated_higher_precision_candidate(
    place_name: str,
    candidate_order: tuple[int, int],
) -> None:
    candidates = [
        LocationCandidate(
            display_name=place_name,
            latitude=30.0,
            longitude=120.0,
            country_code="CN",
            precision="locality",
            source="open_meteo",
        ),
        LocationCandidate(
            display_name=f"新{place_name}城",
            latitude=40.0,
            longitude=-90.0,
            country_code="US",
            precision="city",
            source="open_meteo",
        ),
    ]
    ordered = [candidates[index] for index in candidate_order]

    async def geocoder(*_args: object, **_kwargs: object) -> list[LocationCandidate]:
        return ordered

    async def scenario():
        resolver = LocationResolver(open_meteo=geocoder, nominatim=geocoder)
        return await resolver.resolve(
            LocationRequest(text=place_name, purpose=LocationPurpose.WEATHER)
        )

    result = asyncio.run(scenario())

    assert result.status is LocationStatus.RESOLVED
    assert result.location is not None
    assert result.location.display_name == place_name
    assert result.location.precision == "locality"


@settings(max_examples=100, deadline=None)
@given(
    place_name=_CJK_PLACE_NAMES,
    candidate_order=st.permutations((0, 1)),
    latitude_offset=st.floats(min_value=0.001, max_value=0.05),
    longitude_offset=st.floats(min_value=0.001, max_value=0.05),
)
def test_nearby_provider_records_for_one_city_are_not_an_ambiguity(
    place_name: str,
    candidate_order: tuple[int, int],
    latitude_offset: float,
    longitude_offset: float,
) -> None:
    candidates = [
        LocationCandidate(
            display_name=place_name,
            latitude=30.0,
            longitude=120.0,
            country_code="CN",
            admin1="测试省",
            admin2=place_name,
            precision="city",
            source="open_meteo",
        ),
        LocationCandidate(
            display_name=f"{place_name}市",
            latitude=30.0 + latitude_offset,
            longitude=120.0 + longitude_offset,
            country_code="CN",
            admin1="测试",
            admin2=f"{place_name}市",
            precision="city",
            source="nominatim",
        ),
    ]
    ordered = [candidates[index] for index in candidate_order]

    async def geocoder(*_args: object, **_kwargs: object) -> list[LocationCandidate]:
        return ordered

    async def empty(*_args: object, **_kwargs: object) -> list[LocationCandidate]:
        return []

    async def scenario():
        resolver = LocationResolver(open_meteo=geocoder, nominatim=empty)
        return await resolver.resolve(
            LocationRequest(text=place_name, purpose=LocationPurpose.WEATHER)
        )

    result = asyncio.run(scenario())

    assert result.status is LocationStatus.RESOLVED
    assert result.location is not None
    assert result.location.display_name == place_name
    assert result.location.source == "open_meteo"


@settings(max_examples=100, deadline=None)
@given(
    place_name=_CJK_PLACE_NAMES,
    candidate_order=st.permutations((0, 1)),
)
def test_distant_same_named_cities_are_still_ambiguous(
    place_name: str,
    candidate_order: tuple[int, int],
) -> None:
    candidates = [
        LocationCandidate(
            display_name=place_name,
            latitude=20.0,
            longitude=100.0,
            country_code="CN",
            precision="city",
            source="open_meteo",
        ),
        LocationCandidate(
            display_name=f"{place_name}市",
            latitude=40.0,
            longitude=120.0,
            country_code="CN",
            precision="city",
            source="nominatim",
        ),
    ]
    ordered = [candidates[index] for index in candidate_order]

    async def geocoder(*_args: object, **_kwargs: object) -> list[LocationCandidate]:
        return ordered

    async def empty(*_args: object, **_kwargs: object) -> list[LocationCandidate]:
        return []

    async def scenario():
        resolver = LocationResolver(open_meteo=geocoder, nominatim=empty)
        return await resolver.resolve(
            LocationRequest(text=place_name, purpose=LocationPurpose.WEATHER)
        )

    result = asyncio.run(scenario())

    assert result.status is LocationStatus.AMBIGUOUS
    assert len(result.candidates) == 2
