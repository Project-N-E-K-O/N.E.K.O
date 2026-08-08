from __future__ import annotations

import pytest

from plugin.plugins.lifekit import _geocoders
from plugin.plugins.lifekit._geocoders import open_meteo_candidates
from plugin.plugins.lifekit._location import (
    LocationCandidate,
    LocationPurpose,
    LocationRequest,
    LocationResolver,
    LocationStatus,
    SavedLocation,
)

pytestmark = pytest.mark.asyncio


async def test_ambiguous_chinese_city_is_not_silently_resolved() -> None:
    async def open_meteo(query: str, **_kwargs: object) -> list[LocationCandidate]:
        if query == "吉林市":
            return [
                LocationCandidate(
                    display_name="吉林市",
                    latitude=43.85,
                    longitude=126.56,
                    country_code="CN",
                    admin1="吉林省",
                    precision="city",
                    source="open_meteo",
                )
            ]
        return [
            LocationCandidate(
                display_name="吉林",
                latitude=25.00,
                longitude=121.89,
                country_code="TW",
                admin1="台湾",
                precision="city",
                source="open_meteo",
            )
        ]

    async def nominatim(_query: str, **_kwargs: object) -> list[LocationCandidate]:
        return []

    resolver = LocationResolver(open_meteo=open_meteo, nominatim=nominatim)

    result = await resolver.resolve(
        LocationRequest(text="吉林", purpose=LocationPurpose.NEARBY)
    )

    assert result.status is LocationStatus.AMBIGUOUS
    assert result.location is None
    assert {item.country_code for item in result.candidates} == {"CN", "TW"}


async def test_ineligible_foreign_locality_does_not_make_nearby_city_ambiguous() -> (
    None
):
    china_city = LocationCandidate(
        display_name="上海",
        latitude=31.22,
        longitude=121.46,
        country_code="CN",
        admin1="上海市",
        precision="city",
        source="open_meteo",
    )
    foreign_locality = LocationCandidate(
        display_name="上海市",
        latitude=41.05,
        longitude=-90.50,
        country_code="US",
        admin1="Illinois",
        precision="locality",
        source="open_meteo",
    )

    async def open_meteo(query: str, **_kwargs: object) -> list[LocationCandidate]:
        return [foreign_locality] if query == "上海市" else [china_city]

    async def nominatim(*_args: object, **_kwargs: object) -> list[LocationCandidate]:
        return []

    resolver = LocationResolver(open_meteo=open_meteo, nominatim=nominatim)

    result = await resolver.resolve(
        LocationRequest(text="上海", purpose=LocationPurpose.NEARBY)
    )

    assert result.status is LocationStatus.RESOLVED
    assert result.location is not None
    assert result.location.country_code == "CN"
    assert result.location.precision == "city"


async def test_saved_label_resolves_without_network() -> None:
    async def unexpected_geocoder(
        *_args: object, **_kwargs: object
    ) -> list[LocationCandidate]:
        raise AssertionError(
            "saved locations must be resolved before network providers"
        )

    async def saved_locations() -> list[SavedLocation]:
        return [
            SavedLocation(
                label="家",
                is_default=True,
                location=LocationCandidate(
                    display_name="上海市",
                    latitude=31.23,
                    longitude=121.47,
                    country_code="CN",
                    precision="address",
                    source="saved",
                    verified=True,
                ),
            )
        ]

    resolver = LocationResolver(
        open_meteo=unexpected_geocoder,
        nominatim=unexpected_geocoder,
        saved_locations=saved_locations,
    )

    result = await resolver.resolve(
        LocationRequest(text="家", purpose=LocationPurpose.NEARBY)
    )

    assert result.status is LocationStatus.RESOLVED
    assert result.location is not None
    assert result.location.display_name == "上海市"


async def test_blank_request_uses_verified_saved_default() -> None:
    async def unexpected_geocoder(
        *_args: object, **_kwargs: object
    ) -> list[LocationCandidate]:
        raise AssertionError(
            "verified default must be resolved before network providers"
        )

    default = SavedLocation(
        label="公司",
        is_default=True,
        location=LocationCandidate(
            display_name="北京市朝阳区",
            latitude=39.92,
            longitude=116.44,
            country_code="CN",
            precision="address",
            source="saved",
            verified=True,
        ),
    )

    async def saved_locations() -> list[SavedLocation]:
        return [default]

    resolver = LocationResolver(
        open_meteo=unexpected_geocoder,
        nominatim=unexpected_geocoder,
        saved_locations=saved_locations,
    )

    result = await resolver.resolve(LocationRequest(purpose=LocationPurpose.NEARBY))

    assert result.status is LocationStatus.RESOLVED
    assert result.location == default.location


async def test_weather_can_use_legacy_saved_default() -> None:
    legacy = SavedLocation(
        label="旧默认",
        is_default=True,
        location=LocationCandidate(
            display_name="上海市",
            latitude=31.23,
            longitude=121.47,
            country_code="CN",
            precision="city",
            source="saved",
            verified=False,
        ),
    )

    async def saved_locations() -> list[SavedLocation]:
        return [legacy]

    async def unexpected(*_args: object, **_kwargs: object) -> list[LocationCandidate]:
        raise AssertionError("weather should preserve legacy saved defaults")

    resolver = LocationResolver(
        open_meteo=unexpected,
        nominatim=unexpected,
        saved_locations=saved_locations,
    )

    result = await resolver.resolve(LocationRequest(purpose=LocationPurpose.WEATHER))

    assert result.status is LocationStatus.RESOLVED
    assert result.location == legacy.location


async def test_legacy_saved_default_blocks_lower_priority_nearby_fallbacks() -> None:
    legacy = SavedLocation(
        label="旧默认",
        is_default=True,
        location=LocationCandidate(
            display_name="上海市",
            latitude=31.23,
            longitude=121.47,
            country_code="CN",
            precision="city",
            source="saved",
            verified=False,
        ),
    )

    async def saved_locations() -> list[SavedLocation]:
        return [legacy]

    async def unexpected(*_args: object, **_kwargs: object) -> object:
        raise AssertionError(
            "lower-priority location sources must not replace saved default"
        )

    resolver = LocationResolver(
        open_meteo=unexpected,
        nominatim=unexpected,
        saved_locations=saved_locations,
        default_text=lambda: "北京市",
        geoip=unexpected,
    )

    result = await resolver.resolve(LocationRequest(purpose=LocationPurpose.NEARBY))

    assert result.status is LocationStatus.NEEDS_CONFIRMATION
    assert result.candidates == (legacy.location,)


async def test_open_meteo_adapter_keeps_candidate_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class Response:
        status_code = 200

        def json(self) -> dict[str, object]:
            return {
                "results": [
                    {
                        "name": "吉林市",
                        "latitude": 43.85,
                        "longitude": 126.56,
                        "country_code": "CN",
                        "admin1": "吉林省",
                        "admin2": "吉林市",
                        "feature_code": "PPLA2",
                        "timezone": "Asia/Shanghai",
                    }
                ]
            }

    class Client:
        async def __aenter__(self) -> "Client":
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def get(self, _url: str, **kwargs: object) -> Response:
            captured.update(kwargs)
            return Response()

    monkeypatch.setattr(_geocoders.httpx, "AsyncClient", lambda **_kwargs: Client())

    candidates = await open_meteo_candidates(
        "吉林市", locale="zh-CN", country_code="CN"
    )

    assert len(candidates) == 1
    assert candidates[0].display_name == "吉林市"
    assert candidates[0].admin1 == "吉林省"
    assert candidates[0].precision == "city"
    assert captured["params"] == {
        "name": "吉林市",
        "count": 10,
        "language": "zh",
        "countryCode": "CN",
    }


async def test_geoip_requires_confirmation_for_nearby_search() -> None:
    async def empty_geocoder(
        *_args: object, **_kwargs: object
    ) -> list[LocationCandidate]:
        return []

    async def geoip() -> LocationCandidate:
        return LocationCandidate(
            display_name="上海市",
            latitude=31.23,
            longitude=121.47,
            country_code="CN",
            precision="city",
            source="geoip",
        )

    resolver = LocationResolver(
        open_meteo=empty_geocoder,
        nominatim=empty_geocoder,
        geoip=geoip,
    )

    result = await resolver.resolve(LocationRequest(purpose=LocationPurpose.NEARBY))

    assert result.status is LocationStatus.NEEDS_CONFIRMATION
    assert result.location is None
    assert result.candidates[0].source == "geoip"


async def test_configured_default_city_is_used_before_geoip() -> None:
    async def open_meteo(query: str, **_kwargs: object) -> list[LocationCandidate]:
        assert query == "北京市"
        return [
            LocationCandidate(
                display_name="北京市",
                latitude=39.90,
                longitude=116.41,
                country_code="CN",
                precision="city",
                source="open_meteo",
            )
        ]

    async def unexpected(*_args: object, **_kwargs: object) -> object:
        raise AssertionError(
            "configured default must be used before fallback providers"
        )

    resolver = LocationResolver(
        open_meteo=open_meteo,
        nominatim=unexpected,
        geoip=unexpected,
        default_text=lambda: "北京市",
    )

    result = await resolver.resolve(LocationRequest(purpose=LocationPurpose.NEARBY))

    assert result.status is LocationStatus.RESOLVED
    assert result.location is not None
    assert result.location.display_name == "北京市"


async def test_legacy_saved_city_requires_confirmation_for_nearby() -> None:
    async def empty_geocoder(
        *_args: object, **_kwargs: object
    ) -> list[LocationCandidate]:
        return []

    legacy = LocationCandidate(
        display_name="吉林市",
        latitude=43.85,
        longitude=126.56,
        country_code="CN",
        precision="city",
        source="saved",
        verified=False,
    )

    async def saved_locations() -> list[SavedLocation]:
        return [SavedLocation(label="老家", location=legacy, is_default=True)]

    resolver = LocationResolver(
        open_meteo=empty_geocoder,
        nominatim=empty_geocoder,
        saved_locations=saved_locations,
    )

    result = await resolver.resolve(
        LocationRequest(text="老家", purpose=LocationPurpose.NEARBY)
    )

    assert result.status is LocationStatus.NEEDS_CONFIRMATION
    assert result.location is None
    assert result.candidates == (legacy,)


async def test_region_is_not_a_nearby_search_center() -> None:
    region = LocationCandidate(
        display_name="吉林省",
        latitude=43.67,
        longitude=126.19,
        country_code="CN",
        precision="region",
        source="nominatim",
    )

    async def open_meteo(*_args: object, **_kwargs: object) -> list[LocationCandidate]:
        return [region]

    async def empty(*_args: object, **_kwargs: object) -> list[LocationCandidate]:
        return []

    resolver = LocationResolver(open_meteo=open_meteo, nominatim=empty)

    result = await resolver.resolve(
        LocationRequest(text="吉林省", purpose=LocationPurpose.NEARBY)
    )

    assert result.status is LocationStatus.NEEDS_CONFIRMATION
    assert result.location is None
    assert result.candidates == (region,)


async def test_country_hint_selects_city_over_region_and_locality() -> None:
    city = LocationCandidate(
        display_name="吉林市",
        latitude=43.85,
        longitude=126.56,
        country_code="CN",
        admin1="吉林省",
        precision="city",
        source="open_meteo",
    )
    locality = LocationCandidate(
        display_name="吉林",
        latitude=24.86,
        longitude=106.35,
        country_code="CN",
        admin1="广西",
        precision="locality",
        source="open_meteo",
    )
    region = LocationCandidate(
        display_name="吉林省",
        latitude=43.67,
        longitude=126.19,
        country_code="CN",
        precision="region",
        source="nominatim",
    )
    duplicate_city = LocationCandidate(
        display_name="吉林市",
        latitude=43.84,
        longitude=126.55,
        country_code="CN",
        admin1="吉林",
        precision="city",
        source="nominatim",
    )

    async def open_meteo(query: str, **_kwargs: object) -> list[LocationCandidate]:
        return [city] if query == "吉林市" else [locality]

    async def nominatim(*_args: object, **_kwargs: object) -> list[LocationCandidate]:
        return [region, duplicate_city]

    resolver = LocationResolver(open_meteo=open_meteo, nominatim=nominatim)

    result = await resolver.resolve(
        LocationRequest(
            text="吉林",
            country_hint="CN",
            purpose=LocationPurpose.NEARBY,
        )
    )

    assert result.status is LocationStatus.RESOLVED
    assert result.location is not None
    assert result.location.display_name == "吉林市"


async def test_short_chinese_name_always_uses_disambiguation_provider() -> None:
    city = LocationCandidate(
        display_name="朝阳市",
        latitude=41.58,
        longitude=120.45,
        country_code="CN",
        admin1="辽宁省",
        precision="city",
        source="open_meteo",
    )
    district = LocationCandidate(
        display_name="朝阳区",
        latitude=39.92,
        longitude=116.44,
        country_code="CN",
        admin1="北京市",
        precision="district",
        source="nominatim",
    )

    async def open_meteo(*_args: object, **_kwargs: object) -> list[LocationCandidate]:
        return [city]

    async def nominatim(*_args: object, **_kwargs: object) -> list[LocationCandidate]:
        return [district]

    resolver = LocationResolver(open_meteo=open_meteo, nominatim=nominatim)

    result = await resolver.resolve(
        LocationRequest(
            text="朝阳",
            country_hint="CN",
            purpose=LocationPurpose.NEARBY,
        )
    )

    assert result.status is LocationStatus.AMBIGUOUS
    assert {item.display_name for item in result.candidates} == {"朝阳市", "朝阳区"}


async def test_same_named_localities_at_different_coordinates_remain_ambiguous() -> (
    None
):
    first = LocationCandidate(
        display_name="新村",
        latitude=31.20,
        longitude=121.40,
        country_code="CN",
        admin1="上海市",
        precision="locality",
        source="open_meteo",
    )
    second = LocationCandidate(
        display_name="新村",
        latitude=31.45,
        longitude=121.10,
        country_code="CN",
        admin1="上海市",
        precision="locality",
        source="open_meteo",
    )

    async def open_meteo(*_args: object, **_kwargs: object) -> list[LocationCandidate]:
        return [first, second]

    async def empty(*_args: object, **_kwargs: object) -> list[LocationCandidate]:
        return []

    resolver = LocationResolver(open_meteo=open_meteo, nominatim=empty)

    result = await resolver.resolve(
        LocationRequest(text="新村", purpose=LocationPurpose.WEATHER, country_hint="CN")
    )

    assert result.status is LocationStatus.AMBIGUOUS
    assert len(result.candidates) == 2


async def test_explicit_country_suffix_becomes_hard_country_hint() -> None:
    seen: list[tuple[str, str]] = []

    async def open_meteo(query: str, **kwargs: object) -> list[LocationCandidate]:
        country = str(kwargs.get("country_code") or "")
        seen.append((query, country))
        if query == "吉林市":
            return [
                LocationCandidate(
                    display_name="吉林市",
                    latitude=43.85,
                    longitude=126.56,
                    country_code="CN",
                    precision="city",
                    source="open_meteo",
                )
            ]
        return []

    async def empty(*_args: object, **_kwargs: object) -> list[LocationCandidate]:
        return []

    resolver = LocationResolver(open_meteo=open_meteo, nominatim=empty)

    result = await resolver.resolve(
        LocationRequest(text="吉林，中国", purpose=LocationPurpose.NEARBY)
    )

    assert result.status is LocationStatus.RESOLVED
    assert ("吉林市", "CN") in seen


async def test_required_disambiguation_failure_never_confirms_first_hit() -> None:
    city = LocationCandidate(
        display_name="朝阳市",
        latitude=41.58,
        longitude=120.45,
        country_code="CN",
        precision="city",
        source="open_meteo",
    )

    async def open_meteo(*_args: object, **_kwargs: object) -> list[LocationCandidate]:
        return [city]

    async def failed(*_args: object, **_kwargs: object) -> list[LocationCandidate]:
        raise RuntimeError("provider unavailable")

    resolver = LocationResolver(open_meteo=open_meteo, nominatim=failed)

    result = await resolver.resolve(
        LocationRequest(text="朝阳", country_hint="CN", purpose=LocationPurpose.NEARBY)
    )

    assert result.status is LocationStatus.PROVIDER_FAILED
    assert result.location is None
    assert result.candidates == (city,)
