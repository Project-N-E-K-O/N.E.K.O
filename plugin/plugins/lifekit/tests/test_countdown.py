from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path

import pytest
from plugin.plugins.lifekit._i18n import I18n
from plugin.plugins.lifekit.routers.countdown import CountdownRouter
from plugin.sdk.plugin import Ok


class _Plugin:
    plugin_id = "lifekit"

    def __init__(self, locale: str) -> None:
        self.locale = locale
        self._i18n = I18n(Path(__file__).resolve().parents[1] / "locales")

    def _resolve_locale(self) -> None:
        self._i18n.set_locale(self.locale)

    def push_message(self, **_: object) -> dict[str, bool]:
        return {"ok": True}


class _DefaultLocationPlugin(_Plugin):
    async def _load_saved_locations_for_ui(self) -> list[dict[str, object]]:
        return [
            {
                "label": "home",
                "is_default": True,
                "country_code": "CN",
            }
        ]


class _SlowDefaultLocationPlugin(_Plugin):
    async def _load_saved_locations_for_ui(self) -> list[dict[str, object]]:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


@pytest.mark.asyncio
async def test_country_hint_selects_and_discloses_us_labor_day() -> None:
    router = CountdownRouter()
    router._bind(_Plugin("en"))

    result = await router.countdown(target_date="labor day", country_hint="US")

    assert isinstance(result, Ok)
    target = date.fromisoformat(result.value["detail"]["target"])
    assert target.month == 9
    assert target.weekday() == 0
    assert 1 <= target.day <= 7
    assert result.value["detail"]["assumed_country"] == "US"
    assert "(US)" in result.value["summary"]


@pytest.mark.asyncio
async def test_days_between_uses_the_same_regional_holiday_interpretation() -> None:
    router = CountdownRouter()
    router._bind(_Plugin("en"))

    result = await router.days_between(
        start_date="labor day",
        end_date="christmas",
        country_hint="US",
    )

    assert isinstance(result, Ok)
    start = date.fromisoformat(result.value["detail"]["start"])
    assert start.month == 9
    assert start.weekday() == 0
    assert result.value["detail"]["assumed_country"] == "US"


@pytest.mark.asyncio
async def test_regional_holiday_uses_default_location_before_any_language_guess() -> None:
    router = CountdownRouter()
    router._bind(_DefaultLocationPlugin("en"))

    result = await router.countdown(target_date="national day")

    assert isinstance(result, Ok)
    target = date.fromisoformat(result.value["detail"]["target"])
    assert (target.month, target.day) == (10, 1)
    assert result.value["detail"]["assumed_country"] == "CN"
    assert result.value["detail"]["holiday_alternatives"] == []


@pytest.mark.asyncio
async def test_ambiguous_regional_holiday_returns_one_result_with_alternatives() -> None:
    router = CountdownRouter()
    router._bind(_Plugin("en"))

    result = await router.countdown(target_date="national day")

    assert isinstance(result, Ok)
    assert result.value["detail"]["assumed_country"] in {"CN", "TW", "US"}
    alternatives = result.value["detail"]["holiday_alternatives"]
    assert alternatives
    assert all(set(item) == {"country", "date"} for item in alternatives)
    assert "alternatives" in result.value["summary"]


@pytest.mark.asyncio
async def test_slow_default_location_does_not_block_ambiguous_holiday_result() -> None:
    router = CountdownRouter()
    router._bind(_SlowDefaultLocationPlugin("en"))

    result = await asyncio.wait_for(
        router.countdown(target_date="national day"),
        timeout=0.5,
    )

    assert isinstance(result, Ok)
    assert result.value["detail"]["holiday_alternatives"]


@pytest.mark.asyncio
async def test_invalid_weekday_translation_falls_back_safely() -> None:
    plugin = _Plugin("en")
    router = CountdownRouter()
    router._bind(plugin)
    original_value = plugin._i18n.value
    plugin._i18n.value = (
        lambda key, **kwargs: ["broken"]
        if key == "date.weekdays"
        else original_value(key, **kwargs)
    )

    result = await router.countdown(target_date="2030-01-02")

    assert isinstance(result, Ok)
    assert result.value["detail"]["weekday"]


@pytest.mark.asyncio
async def test_unknown_country_hint_keeps_a_best_effort_holiday_result() -> None:
    router = CountdownRouter()
    router._bind(_Plugin("en"))

    result = await router.countdown(
        target_date="national day",
        country_hint="unknown-country",
    )

    assert isinstance(result, Ok)
    assert result.value["detail"]["target"]
    assert result.value["detail"]["holiday_alternatives"]
