from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from plugin.plugins.lifekit import LifeKitPlugin
from plugin.plugins.lifekit._i18n import I18n
from plugin.plugins.lifekit.routers.locations import LocationsRouter
from plugin.sdk.shared.models import Err, Ok
from plugin.sdk.shared.models.exceptions import TransportError

_LOCALES_DIR = Path(__file__).resolve().parents[3] / "plugins" / "lifekit" / "locales"


class _Logger:
    def warning(self, *args: object, **kwargs: object) -> None:
        pass

    def error(self, *args: object, **kwargs: object) -> None:
        pass

    def debug(self, *args: object, **kwargs: object) -> None:
        pass


class _FakeStore:
    def __init__(self, *, enabled: bool = True, get_fn=None) -> None:
        self.enabled = enabled
        self._get_fn = get_fn
        self.set_calls: list[tuple[str, Any]] = []

    async def get(self, key: str, default: Any = None):
        return self._get_fn(key, default)

    async def set(self, key: str, value: Any):
        self.set_calls.append((key, value))
        return Ok(True)


class _Plugin:
    def __init__(self, store: _FakeStore) -> None:
        self.store = store
        self.logger = _Logger()
        self._locations_lock = asyncio.Lock()
        self._i18n = I18n(_LOCALES_DIR)
        self._i18n.set_locale("en")

    def _resolve_locale(self) -> None:
        pass


def _router(store: _FakeStore) -> tuple[LocationsRouter, _FakeStore]:
    router = LocationsRouter()
    router._main_plugin = _Plugin(store)
    return router, store


@pytest.mark.plugin_unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "get_fn",
    [
        lambda _k, _d: Err(TransportError("sqlite locked")),
        lambda _k, _d: Ok({"corrupted": True}),
        lambda _k, _d: Ok(None),
        lambda _k, _d: (_ for _ in ()).throw(RuntimeError("unexpected read crash")),
    ],
)
async def test_locations_refuses_to_overwrite_or_misreport_on_load_failure(
    monkeypatch: pytest.MonkeyPatch,
    get_fn,
) -> None:
    async def _fake_geocode(query: str, locale: str = "en"):
        del query, locale
        return {"city": "Tokyo", "lat": 35.68, "lon": 139.76, "country": "JP", "country_code": "JP"}

    monkeypatch.setattr(
        "plugin.plugins.lifekit.routers.locations.geocode_city",
        _fake_geocode,
    )
    router, store = _router(_FakeStore(enabled=True, get_fn=get_fn))

    add_token = router._write_confirmations.issue(
        "add_location",
        {"label": "work", "city": "Tokyo", "address": "", "set_default": False},
    )
    rem_token = router._write_confirmations.issue(
        "remove_location",
        {"location_id": "home"},
    )
    def_token = router._write_confirmations.issue(
        "set_default_location",
        {"location_id": "home"},
    )

    add_res = await router.add_location(
        label="work", city="Tokyo", confirmed=True, confirmation_token=add_token
    )
    rem_res = await router.remove_location(
        location_id="home", confirmed=True, confirmation_token=rem_token
    )
    def_res = await router.set_default_location(
        location_id="home", confirmed=True, confirmation_token=def_token
    )
    lst_res = await router.list_locations()

    assert add_res.is_err()
    assert rem_res.is_err()
    assert def_res.is_err()
    assert lst_res.is_err()
    assert store.set_calls == []


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_locations_store_disabled_returns_empty_list_but_rejects_mutations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_geocode(query: str, locale: str = "en"):
        del query, locale
        return {"city": "Tokyo", "lat": 35.68, "lon": 139.76, "country": "JP", "country_code": "JP"}

    monkeypatch.setattr(
        "plugin.plugins.lifekit.routers.locations.geocode_city",
        _fake_geocode,
    )
    router, store = _router(_FakeStore(enabled=False, get_fn=lambda _k, _d: Ok([])))

    lst_res = await router.list_locations()
    add_token = router._write_confirmations.issue(
        "add_location",
        {"label": "work", "city": "Tokyo", "address": "", "set_default": False},
    )
    add_res = await router.add_location(
        label="work", city="Tokyo", confirmed=True, confirmation_token=add_token
    )

    assert lst_res.is_ok()
    assert lst_res.value == {"count": 0, "locations": []}
    assert add_res.is_err()
    assert store.set_calls == []


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_locations_legitimate_empty_store_allows_add_location(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_geocode(query: str, locale: str = "en"):
        del query, locale
        return {"city": "Tokyo", "lat": 35.68, "lon": 139.76, "country": "JP", "country_code": "JP"}

    monkeypatch.setattr(
        "plugin.plugins.lifekit.routers.locations.geocode_city",
        _fake_geocode,
    )
    router, store = _router(_FakeStore(enabled=True, get_fn=lambda _k, _d: Ok([])))

    add_token = router._write_confirmations.issue(
        "add_location",
        {"label": "work", "city": "Tokyo", "address": "", "set_default": False},
    )
    add_res = await router.add_location(
        label="work", city="Tokyo", confirmed=True, confirmation_token=add_token
    )

    assert add_res.is_ok()
    assert len(store.set_calls) == 1
    assert store.set_calls[0][0] == "saved_locations"
    assert len(store.set_calls[0][1]) == 1
    assert store.set_calls[0][1][0]["label"] == "work"


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_ui_locations_helper_handles_err_result_safely() -> None:
    plugin = LifeKitPlugin.__new__(LifeKitPlugin)
    plugin.store = _FakeStore(enabled=True, get_fn=lambda _k, _d: Err(TransportError("boom")))
    plugin.logger = _Logger()

    assert await plugin._load_saved_locations_for_ui() == []
