from __future__ import annotations

from pathlib import Path

import pytest
from plugin.plugins.lifekit._i18n import I18n
from plugin.plugins.lifekit._nearby_intent import build_nearby_request_plan
from plugin.plugins.lifekit.routers.travel import build_travel_advice
from plugin.plugins.lifekit.routers.unit_convert import UnitConvertRouter
from plugin.sdk.plugin import Ok


class _Plugin:
    plugin_id = "lifekit"

    def __init__(self) -> None:
        self._i18n = I18n(Path(__file__).resolve().parents[1] / "locales")

    def _resolve_locale(self) -> None:
        self._i18n.set_locale("zh-CN")

    def push_message(self, **_: object) -> dict[str, bool]:
        return {"ok": True}


@pytest.mark.asyncio
async def test_same_unit_conversion_keeps_complete_public_contract() -> None:
    router = UnitConvertRouter()
    router._bind(_Plugin())

    result = await router.unit_convert(value=2, from_unit="kg", to_unit="kg")

    assert isinstance(result, Ok)
    assert result.value["conversion"] == {
        "value": 2.0,
        "from_unit": "公斤",
        "result": 2.0,
        "to_unit": "公斤",
    }


@pytest.mark.asyncio
async def test_generic_conversion_returns_display_labels() -> None:
    router = UnitConvertRouter()
    router._bind(_Plugin())

    result = await router.unit_convert(value=1, from_unit="ml", to_unit="fl oz")

    assert isinstance(result, Ok)
    assert result.value["conversion"]["from_unit"] == "毫升"
    assert result.value["conversion"]["to_unit"] == "液体盎司"


def test_travel_advice_tolerates_null_uv_and_wind() -> None:
    i18n = I18n(Path(__file__).resolve().parents[1] / "locales")
    i18n.set_locale("en")

    advice = build_travel_advice(
        {
            "temperature_2m": 20,
            "weather_code": 0,
            "uv_index": None,
            "wind_speed_10m": None,
        },
        {},
        i18n,
    )

    assert advice["sunscreen"] is False


def test_mixed_nearby_categories_preserve_each_requested_target() -> None:
    request = "restaurants and coffee near Times Square"

    plan = build_nearby_request_plan(
        request_text=request,
        raw_request=request,
        projected_params=True,
        location_hint="",
        legacy_location="",
        place_intent="explore",
        preference_hints=(),
        search_terms=(),
    )

    assert plan.search_terms == ("餐厅", "咖啡馆")


def test_mixed_culture_categories_keep_museum_and_gallery_distinct() -> None:
    request = "museum and gallery near Trafalgar Square"

    plan = build_nearby_request_plan(
        request_text=request,
        raw_request=request,
        projected_params=True,
        location_hint="",
        legacy_location="",
        place_intent="explore",
        preference_hints=(),
        search_terms=(),
    )

    assert plan.search_terms == ("博物馆", "美术馆")


def test_english_nearby_location_accepts_sentence_punctuation() -> None:
    request = "restaurants near Times Square?"

    plan = build_nearby_request_plan(
        request_text=request,
        raw_request=request,
        projected_params=True,
        location_hint="",
        legacy_location="",
        place_intent="explore",
        preference_hints=(),
        search_terms=(),
    )

    assert plan.location == "Times Square"


def test_english_nearby_location_preserves_comma_qualifier() -> None:
    request = "restaurants near Springfield, IL"

    plan = build_nearby_request_plan(
        request_text=request,
        raw_request=request,
        projected_params=True,
        location_hint="",
        legacy_location="",
        place_intent="explore",
        preference_hints=(),
        search_terms=(),
    )

    assert plan.location == "Springfield, IL"


@pytest.mark.parametrize(
    ("request_text", "expected_term"),
    [
        ("parks near Times Square", "公园"),
        ("museums near Trafalgar Square", "博物馆"),
        ("bars near Shibuya Station", "酒吧"),
        ("pharmacies near Central Station", "药店"),
    ],
)
def test_plural_english_nearby_categories_map_to_specific_terms(
    request_text: str,
    expected_term: str,
) -> None:
    plan = build_nearby_request_plan(
        request_text=request_text,
        raw_request=request_text,
        projected_params=True,
        location_hint="",
        legacy_location="",
        place_intent="explore",
        preference_hints=(),
        search_terms=(),
    )

    assert plan.search_terms == (expected_term,)


@pytest.mark.parametrize(
    ("request_text", "projected_location", "expected_location", "expected_term"),
    [
        ("我附近的公园", "我", "", "公园"),
        ("我附近的公园", "在这里", "", "公园"),
        ("这附近的餐厅", "这", "", "餐厅"),
        ("这附近的餐厅", "在当前位置", "", "餐厅"),
        ("在人民广场附近的餐厅", "在人民广场", "人民广场", "餐厅"),
    ],
)
def test_chinese_nearby_centers_remove_deictics_and_prepositions(
    request_text: str,
    projected_location: str,
    expected_location: str,
    expected_term: str,
) -> None:
    plan = build_nearby_request_plan(
        request_text=request_text,
        raw_request=request_text,
        projected_params=True,
        location_hint=projected_location,
        legacy_location="",
        place_intent="explore",
        preference_hints=(),
        search_terms=(),
    )

    assert plan.location == expected_location
    assert plan.search_terms == (expected_term,)
