"""Explicit-only, fixed-host official World of Warships lookup tool."""

from __future__ import annotations

import asyncio
import json
import socket
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse
from urllib.error import URLError

import pytest

from plugin.sdk.plugin.llm_tool import collect_llm_tool_methods
from plugin.plugins.neko_wows import NekoWowsPlugin
from plugin.plugins.neko_wows.domain.contracts import WowsConfig
from plugin.plugins.neko_wows.ship_data.models import (
    CatalogMeta,
    CatalogShip,
    ShipProfile,
)
from plugin.plugins.neko_wows.ship_data.official_api import OfficialWowsApiClient


SHIP_ID = 4276041424


def response(payload) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def ship_envelope(*, ship_id: int = SHIP_ID):
    return {
        "status": "ok",
        "data": {
            str(ship_id): {
                "ship_id": ship_id,
                "name": "Yamato",
                "tier": 10,
                "type": "Battleship",
                "nation": "japan",
                "is_premium": False,
                "modules": {
                    "hull": [100, 101],
                    "artillery": [200, 201],
                    "engine": [300],
                },
                "modules_tree": {
                    "100": {
                        "module_id": 100,
                        "next_modules": [101],
                        "price_xp": 0,
                        "price_credit": 0,
                    },
                    "101": {
                        "module_id": 101,
                        "next_modules": [],
                        "price_xp": 50_000,
                        "price_credit": 5_000_000,
                    },
                    "200": {
                        "module_id": 200,
                        "next_modules": [201],
                        "price_xp": 0,
                        "price_credit": 0,
                    },
                    "201": {
                        "module_id": 201,
                        "next_modules": [],
                        "price_xp": 20_000,
                        "price_credit": 2_000_000,
                    },
                    "300": {
                        "module_id": 300,
                        "next_modules": [],
                        "price_xp": 0,
                        "price_credit": 0,
                    },
                },
            }
        },
    }


def profile_envelope(*, ship_id: int = SHIP_ID):
    return {
        "status": "ok",
        "data": {
            str(ship_id): {
                "hull": {"health": 97_200},
                "mobility": {
                    "max_speed": 27.0,
                    "turning_radius": 900,
                    "rudder_time": 22.1,
                },
                "concealment": {
                    "detect_distance_by_ship": 17.5,
                    "detect_distance_by_plane": 10.0,
                },
                "artillery": {
                    "distance": 26.63,
                    "shot_delay": 30.0,
                    "rotation_time": 60.0,
                    "max_dispersion": 275,
                    "shells": {
                        "HE": {
                            "type": "HE",
                            "damage": 7_300,
                            "burn_probability": 0.35,
                            "bullet_speed": 805,
                        },
                        "AP": {
                            "type": "AP",
                            "damage": 14_800,
                            "bullet_speed": 780,
                        },
                    },
                },
                "torpedoes": None,
                "raw_unknown": {"secret": "must not escape"},
            }
        },
    }


class ScriptedTransport:
    def __init__(self, script) -> None:
        self.script = list(script)
        self.calls: list[tuple[str, float, int]] = []

    def __call__(self, url: str, timeout: float, max_bytes: int):
        self.calls.append((url, timeout, max_bytes))
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class BlockingFirstTransport(ScriptedTransport):
    def __init__(self, script) -> None:
        super().__init__(script)
        self.started = threading.Event()
        self.release = threading.Event()

    def __call__(self, url: str, timeout: float, max_bytes: int):
        if not self.calls:
            self.started.set()
            if not self.release.wait(timeout=5.0):
                raise TimeoutError("test transport was not released")
        return super().__call__(url, timeout, max_bytes)


def make_client(
    script=None,
    *,
    region="asia",
    application_id="test-app-id",
    clock=lambda: 100.0,
):
    transport = ScriptedTransport(script or [
        (200, response(ship_envelope())),
        (200, response(profile_envelope())),
    ])
    client = OfficialWowsApiClient(
        application_id=application_id,
        region=region,
        timeout_seconds=3.0,
        cache_ttl_seconds=300.0,
        transport=transport,
        clock=clock,
    )
    return client, transport


@pytest.mark.parametrize(("region", "hostname"), [
    ("na", "api.worldofwarships.com"),
    ("eu", "api.worldofwarships.eu"),
    ("asia", "api.worldofwarships.asia"),
])
def test_official_client_uses_only_region_whitelist(region, hostname):
    client, transport = make_client(region=region)

    result = client.query_ship_id(SHIP_ID, configuration="top", language="zh-cn")

    assert result["ok"] is True
    assert {urlparse(call[0]).hostname for call in transport.calls} == {hostname}
    assert all(urlparse(call[0]).scheme == "https" for call in transport.calls)


def test_apply_config_strips_region_like_the_constructor():
    client, _transport = make_client(region="asia")
    client.apply_config(SimpleNamespace(
        official_api_application_id="test-app-id",
        official_api_region=" ASIA ",
        official_api_timeout_seconds=3.0,
        official_api_cache_ttl_seconds=300.0,
    ))

    assert client.region == "asia"
    assert client._validate("top", "zh-cn") == ""


@pytest.mark.parametrize("lookup_kind", ["id", "name"])
@pytest.mark.parametrize(("new_region", "new_application_id"), [
    ("eu", "old-app-id"),
    ("asia", "new-app-id"),
])
def test_inflight_lookup_uses_one_config_snapshot_and_drops_stale_cache(
    lookup_kind,
    new_region,
    new_application_id,
):
    listing = (200, response({
        "status": "ok",
        "meta": {"page_total": 1},
        "data": {
            str(SHIP_ID): {"ship_id": SHIP_ID, "name": "Yamato"},
        },
    }))
    request_script = [
        (200, response(ship_envelope())),
        (200, response(profile_envelope())),
    ]
    if lookup_kind == "name":
        request_script.insert(0, listing)
    request_count = len(request_script)
    transport = BlockingFirstTransport(request_script * 2)
    client = OfficialWowsApiClient(
        application_id="old-app-id",
        region="asia",
        timeout_seconds=3.0,
        cache_ttl_seconds=300.0,
        transport=transport,
        clock=lambda: 100.0,
    )
    def lookup():
        if lookup_kind == "id":
            return client.query_ship_id(SHIP_ID, language="en")
        return client.query_ship("Yamato", language="en")

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(lookup)
        try:
            assert transport.started.wait(timeout=2.0)
            client.apply_config(SimpleNamespace(
                official_api_application_id=new_application_id,
                official_api_region=new_region,
                official_api_timeout_seconds=3.0,
                official_api_cache_ttl_seconds=300.0,
            ))
        finally:
            transport.release.set()
        first = future.result(timeout=5.0)

    assert first["ok"] is True
    assert first["data"]["region"] == "asia"
    assert len(transport.calls) == request_count
    for url, timeout, _max_bytes in transport.calls:
        parsed = urlparse(url)
        assert parsed.hostname == "api.worldofwarships.asia"
        assert parse_qs(parsed.query)["application_id"] == ["old-app-id"]
        assert timeout == 3.0
    assert client.stats()["cache_entries"] == 0
    if lookup_kind == "name":
        assert not client._name_index

    second = (
        client.query_ship_id(SHIP_ID, language="en")
        if lookup_kind == "id"
        else client.query_ship("Yamato", language="en")
    )

    expected_hostname = (
        "api.worldofwarships.eu"
        if new_region == "eu"
        else "api.worldofwarships.asia"
    )
    assert second["ok"] is True
    assert second["data"]["region"] == new_region
    assert len(transport.calls) == request_count * 2
    for url, timeout, _max_bytes in transport.calls[request_count:]:
        parsed = urlparse(url)
        assert parsed.hostname == expected_hostname
        assert parse_qs(parsed.query)["application_id"] == [
            new_application_id]
        assert timeout == 3.0


def test_official_name_lookup_pages_without_unsupported_search_parameter():
    client, transport = make_client(script=[
        (200, response({
            "status": "ok",
            "meta": {"page_total": 2},
            "data": {
                "1": {"ship_id": 1, "name": "Yamato II"},
            },
        })),
        (200, response({
            "status": "ok",
            "meta": {"page_total": 2},
            "data": {
                str(SHIP_ID): {"ship_id": SHIP_ID, "name": "YAMATO"},
            },
        })),
        (200, response(ship_envelope())),
        (200, response(profile_envelope())),
    ])

    result = client.query_ship("  yamato  ", language="en")

    assert result["ok"] is True
    listing_queries = [
        parse_qs(urlparse(call[0]).query)
        for call in transport.calls[:2]
    ]
    assert [query["page_no"] for query in listing_queries] == [["1"], ["2"]]
    for query in listing_queries:
        assert "search" not in query
        assert query["language"] == ["en"]
        assert query["fields"] == ["ship_id,name"]
        assert query["limit"] == ["100"]


def test_official_name_lookup_shares_one_deadline_with_detail_requests():
    now = [100.0]
    listing = (200, response({
        "status": "ok",
        "meta": {"page_total": 7},
        "data": {
            str(SHIP_ID): {"ship_id": SHIP_ID, "name": "Yamato"},
        },
    }))

    class AdvancingTransport(ScriptedTransport):
        def __call__(self, url: str, timeout: float, max_bytes: int):
            result = super().__call__(url, timeout, max_bytes)
            now[0] += min(4.0, timeout)
            return result

    transport = AdvancingTransport([
        *([listing] * 7),
        (200, response(ship_envelope())),
        (200, response(profile_envelope())),
    ])
    client = OfficialWowsApiClient(
        application_id="test-app-id",
        region="asia",
        timeout_seconds=5.0,
        cache_ttl_seconds=300.0,
        transport=transport,
        clock=lambda: now[0],
    )

    result = client.query_ship("Yamato", language="en")

    assert result["code"] == "timeout"
    assert now[0] == pytest.approx(130.0)
    assert len(transport.calls) == 8
    assert transport.calls[-1][1] == pytest.approx(2.0)
    assert all("/shipprofile/" not in url for url, *_ in transport.calls)


def _listing_calls(transport) -> list[str]:
    found = []
    for url, *_ in transport.calls:
        query = parse_qs(urlparse(url).query)
        if query.get("fields") == ["ship_id,name"]:
            found.append(url)
    return found


def test_official_name_lookup_reuses_the_paged_index_within_ttl():
    client, transport = make_client(script=[
        (200, response({
            "status": "ok",
            "meta": {"page_total": 2},
            "data": {
                "1": {"ship_id": 1, "name": "Yamato II"},
            },
        })),
        (200, response({
            "status": "ok",
            "meta": {"page_total": 2},
            "data": {
                str(SHIP_ID): {"ship_id": SHIP_ID, "name": "YAMATO"},
            },
        })),
        (200, response(ship_envelope())),
        (200, response(profile_envelope())),
        (200, response(ship_envelope(ship_id=1))),
        (200, response(profile_envelope(ship_id=1))),
    ])

    first = client.query_ship("Yamato", language="en")
    again = client.query_ship("  yamato  ", language="en")
    other = client.query_ship("Yamato II", language="en")

    assert first["ok"] is True
    assert again["ok"] is True
    assert other["ok"] is True
    assert other["data"]["ship"]["ship_id"] == 1
    assert len(_listing_calls(transport)) == 2
    assert len(transport.calls) == 6


@pytest.mark.parametrize("invalid_ship_id", [0, -1])
def test_official_name_lookup_ignores_nonpositive_exact_name_ids(
    invalid_ship_id,
):
    client, _transport = make_client(script=[
        (200, response({
            "status": "ok",
            "meta": {"page_total": 1},
            "data": {
                "invalid": {"ship_id": invalid_ship_id, "name": "Yamato"},
                str(SHIP_ID): {"ship_id": SHIP_ID, "name": "Yamato"},
            },
        })),
        (200, response(ship_envelope())),
        (200, response(profile_envelope())),
    ])

    result = client.query_ship("Yamato", language="en")

    assert result["ok"] is True
    assert result["data"]["ship"]["ship_id"] == SHIP_ID


@pytest.mark.parametrize("page_total", [None, True, "2", 2.0, 0, -1, 10 ** 9])
def test_official_name_lookup_rejects_untrusted_page_total_without_more_requests(
    page_total,
):
    client, transport = make_client(script=[(200, response({
        "status": "ok",
        "meta": {"page_total": page_total},
        "data": {},
    }))])

    result = client.query_ship("Yamato", language="en")

    assert result["code"] == "invalid_response"
    assert len(transport.calls) == 1


@pytest.mark.parametrize(("status", "code"), [
    (401, "unauthorized"),
    (403, "unauthorized"),
    (429, "rate_limited"),
    (500, "upstream_error"),
])
def test_official_status_codes_are_stable(status, code):
    client, _ = make_client(script=[(status, b"{}")])

    result = client.query_ship_id(SHIP_ID, configuration="top", language="en")

    assert result["ok"] is False
    assert result["code"] == code
    assert result["source"] == "official_wargaming_api"


def test_application_id_is_absent_from_result_and_logs(caplog):
    client, _ = make_client(
        application_id="secret-app-id", script=[(401, b"{}")])

    result = client.query_ship_id(SHIP_ID, configuration="top", language="en")

    assert "secret-app-id" not in json.dumps(result)
    assert "secret-app-id" not in caplog.text


def test_success_is_cached_in_memory_and_never_writes_catalog(tmp_path):
    catalog_path = tmp_path / "ship.sqlite3"
    catalog_path.write_bytes(b"immutable catalog bytes")
    before = (catalog_path.stat().st_mtime_ns, catalog_path.read_bytes())
    client, transport = make_client()

    first = client.query_ship_id(SHIP_ID, configuration="top", language="zh-cn")
    second = client.query_ship_id(SHIP_ID, configuration="top", language="zh-cn")

    assert first == second
    assert len(transport.calls) == 2
    assert (catalog_path.stat().st_mtime_ns, catalog_path.read_bytes()) == before
    assert client.stats()["cache_hits"] == 1


def test_client_selects_terminal_modules_and_returns_only_canonical_fields():
    client, transport = make_client()

    result = client.query_ship_id(SHIP_ID, configuration="top", language="zh-cn")

    assert result["ok"] is True
    data = result["data"]
    assert data["ship"]["ship_id"] == SHIP_ID
    assert data["configuration"] == "top"
    assert data["modules"] == {
        "artillery_id": 201,
        "engine_id": 300,
        "hull_id": 101,
    }
    assert data["profile"]["survivability"]["hit_points"] == 97_200
    assert data["profile"]["main_battery"]["range_m"] == 26_630
    assert "raw_unknown" not in json.dumps(data)
    assert "secret" not in json.dumps(data)

    profile_query = parse_qs(urlparse(transport.calls[1][0]).query)
    assert profile_query["hull_id"] == ["101"]
    assert profile_query["artillery_id"] == ["201"]
    assert profile_query["engine_id"] == ["300"]


@pytest.mark.parametrize(
    "bad_value",
    [True, float("nan"), float("inf"), 10 ** 400],
)
def test_official_invalid_profile_number_maps_to_invalid_response(bad_value):
    profile = profile_envelope()
    profile["data"][str(SHIP_ID)]["hull"]["health"] = bad_value
    client, transport = make_client(script=[
        (200, response(ship_envelope())),
        (200, response(profile)),
    ])

    result = client.query_ship_id(SHIP_ID)

    assert result["code"] == "invalid_response"
    assert len(transport.calls) == 2


@pytest.mark.parametrize("modules", [{}, {"artillery": [200, 201]}])
def test_official_missing_or_hull_free_module_table_stops_before_profile_request(
    modules,
):
    envelope = ship_envelope()
    envelope["data"][str(SHIP_ID)]["modules"] = modules
    client, transport = make_client(script=[(200, response(envelope))])

    result = client.query_ship_id(SHIP_ID)

    assert result["code"] == "invalid_response"
    assert len(transport.calls) == 1


@pytest.mark.parametrize("invalid_module_id", [None, True, "101", -1])
def test_official_malformed_module_candidate_stops_before_profile_request(
    invalid_module_id,
):
    envelope = ship_envelope()
    envelope["data"][str(SHIP_ID)]["modules"]["hull"].append(invalid_module_id)
    client, transport = make_client(script=[(200, response(envelope))])

    result = client.query_ship_id(SHIP_ID)

    assert result["code"] == "invalid_response"
    assert len(transport.calls) == 1


def test_official_empty_non_hull_module_slots_are_ignored():
    envelope = ship_envelope()
    envelope["data"][str(SHIP_ID)]["modules"].update({
        "dive_bomber": [],
        "fighter": [],
        "flight_control": [],
        "torpedo_bomber": [],
        "torpedoes": [],
    })
    client, transport = make_client(script=[
        (200, response(envelope)),
        (200, response(profile_envelope())),
    ])

    result = client.query_ship_id(SHIP_ID)

    assert result["ok"] is True
    assert len(transport.calls) == 2


@pytest.mark.parametrize("top_field", ["top", "isTop", "is_top"])
def test_official_explicit_top_terminal_has_priority(top_field):
    envelope = ship_envelope()
    ship = envelope["data"][str(SHIP_ID)]
    ship["modules_tree"]["100"]["next_modules"] = []
    ship["modules_tree"]["100"][top_field] = True
    client, _ = make_client(script=[
        (200, response(envelope)),
        (200, response(profile_envelope())),
    ])

    result = client.query_ship_id(SHIP_ID)

    assert result["ok"] is True
    assert result["data"]["modules"]["hull_id"] == 100


def test_official_module_index_has_priority_over_xp():
    envelope = ship_envelope()
    tree = envelope["data"][str(SHIP_ID)]["modules_tree"]
    tree["100"].update({"next_modules": [], "index": 2, "price_xp": 1})
    tree["101"].update({
        "next_modules": [],
        "module_index": 1,
        "price_xp": 999_999,
    })
    client, _ = make_client(script=[
        (200, response(envelope)),
        (200, response(profile_envelope())),
    ])

    result = client.query_ship_id(SHIP_ID)

    assert result["ok"] is True
    assert result["data"]["modules"]["hull_id"] == 100


@pytest.mark.parametrize(
    "bad_value",
    [True, float("nan"), float("inf"), 10 ** 400],
)
def test_official_invalid_module_rank_stops_before_profile_request(bad_value):
    envelope = ship_envelope()
    tree = envelope["data"][str(SHIP_ID)]["modules_tree"]
    tree["100"].update({"next_modules": [], "price_xp": bad_value})
    tree["101"]["next_modules"] = []
    client, transport = make_client(script=[(200, response(envelope))])

    result = client.query_ship_id(SHIP_ID)

    assert result["code"] == "invalid_response"
    assert len(transport.calls) == 1


def test_official_module_array_position_is_index_fallback():
    envelope = ship_envelope()
    tree = envelope["data"][str(SHIP_ID)]["modules_tree"]
    tree["100"].update({"next_modules": [], "price_xp": 999_999})
    tree["101"].update({"next_modules": [], "price_xp": 0})
    client, _ = make_client(script=[
        (200, response(envelope)),
        (200, response(profile_envelope())),
    ])

    result = client.query_ship_id(SHIP_ID)

    assert result["ok"] is True
    assert result["data"]["modules"]["hull_id"] == 101


@pytest.mark.parametrize("bad_index", [True, 1.5, float("nan"), 10 ** 400])
def test_official_invalid_module_index_stops_before_profile_request(bad_index):
    envelope = ship_envelope()
    tree = envelope["data"][str(SHIP_ID)]["modules_tree"]
    tree["100"].update({
        "next_modules": [],
        "index": bad_index,
        "price_xp": 999_999,
    })
    tree["101"].update({"next_modules": [], "price_xp": 0})
    client, transport = make_client(script=[(200, response(envelope))])

    result = client.query_ship_id(SHIP_ID)

    assert result["code"] == "invalid_response"
    assert len(transport.calls) == 1


def test_official_module_key_breaks_an_exact_rank_tie_lexicographically():
    envelope = ship_envelope()
    tree = envelope["data"][str(SHIP_ID)]["modules_tree"]
    for module_id in ("100", "101"):
        tree[module_id].update({
            "next_modules": [],
            "index": 7,
            "price_xp": 10,
            "price_credit": 20,
        })
    client, _ = make_client(script=[
        (200, response(envelope)),
        (200, response(profile_envelope())),
    ])

    result = client.query_ship_id(SHIP_ID)

    assert result["ok"] is True
    assert result["data"]["modules"]["hull_id"] == 100


@pytest.mark.parametrize("graph_error", ["cycle", "dangling", "missing_node"])
def test_official_invalid_module_graph_stops_before_profile_request(graph_error):
    envelope = ship_envelope()
    ship = envelope["data"][str(SHIP_ID)]
    tree = ship["modules_tree"]
    if graph_error == "cycle":
        ship["modules"]["hull"].append(102)
        tree["100"]["next_modules"] = [101]
        tree["101"]["next_modules"] = [100]
        tree["102"] = {
            "module_id": 102,
            "next_modules": [],
            "price_xp": 999_999,
            "price_credit": 999_999,
        }
    elif graph_error == "dangling":
        tree["100"]["next_modules"] = [999]
        tree["101"]["next_modules"] = []
    else:
        del tree["100"]
    client, transport = make_client(script=[(200, response(envelope))])

    result = client.query_ship_id(SHIP_ID)

    assert result["code"] == "invalid_response"
    assert len(transport.calls) == 1


@pytest.mark.parametrize(("kwargs", "code"), [
    ({"region": "custom.example"}, "invalid_region"),
    ({"language": "xx-evil"}, "invalid_language"),
    ({"configuration": "stock"}, "invalid_configuration"),
])
def test_invalid_options_never_reach_transport(kwargs, code):
    client, transport = make_client(region=kwargs.get("region", "asia"))

    result = client.query_ship_id(
        SHIP_ID,
        configuration=kwargs.get("configuration", "top"),
        language=kwargs.get("language", "en"),
    )

    assert result["code"] == code
    assert transport.calls == []


def test_timeout_and_invalid_json_have_stable_codes():
    timeout_client, _ = make_client(script=[TimeoutError("secret timeout")])
    invalid_client, _ = make_client(script=[(200, b"not-json")])

    assert timeout_client.query_ship_id(SHIP_ID)["code"] == "timeout"
    assert invalid_client.query_ship_id(SHIP_ID)["code"] == "invalid_response"


def test_wrapped_socket_timeout_is_still_reported_as_timeout():
    client, _ = make_client(script=[URLError(socket.timeout("timed out"))])

    result = client.query_ship_id(SHIP_ID)

    assert result["code"] == "timeout"


def test_numeric_official_application_error_is_unauthorized():
    client, _ = make_client(script=[(200, response({
        "status": "error",
        "error": {"code": 402},
    }))])

    result = client.query_ship_id(SHIP_ID)

    assert result["code"] == "unauthorized"


def test_plugin_declares_exactly_one_official_lookup_tool():
    """Going online is the one thing this plugin must not do by accident, so
    there is exactly one tool that can — the rest are local-only."""
    plugin = object.__new__(NekoWowsPlugin)

    tools = collect_llm_tool_methods(plugin)

    official = [meta for meta, _method in tools if "official" in meta.name]
    assert [meta.name for meta in official] == ["wows_query_ship_official"]
    meta = official[0]
    assert meta.parameters["required"] == ["ship"]
    assert meta.parameters["properties"]["configuration"]["enum"] == ["top"]


class CountingOfficialClient:
    def __init__(self) -> None:
        self.calls = []
        self.name_calls = []

    def query_ship(self, ship, *, configuration, language):
        self.name_calls.append((ship, configuration, language))
        return {"ok": True, "code": "ok", "data": {"ship": ship}}

    def query_ship_id(self, ship_id, *, configuration, language):
        self.calls.append((ship_id, configuration, language))
        return {"ok": True, "code": "ok", "data": {"ship_id": ship_id}}


def tool_target(cfg: WowsConfig):
    plugin = object.__new__(NekoWowsPlugin)
    plugin.cfg = cfg
    plugin.official_api = CountingOfficialClient()
    plugin.ship_catalog_store = SimpleNamespace(
        snapshot=lambda: (_ for _ in ()).throw(
            AssertionError("numeric IDs must not open the offline catalog")))
    return plugin


def test_tool_disabled_and_missing_key_do_not_call_client():
    disabled = tool_target(WowsConfig(official_api_enabled=False))
    missing = tool_target(WowsConfig(
        official_api_enabled=True, official_api_application_id=""))

    disabled_result = asyncio.run(disabled.wows_query_ship_official(str(SHIP_ID)))
    missing_result = asyncio.run(missing.wows_query_ship_official(str(SHIP_ID)))

    assert disabled_result["code"] == "disabled"
    assert missing_result["code"] == "missing_application_id"
    assert disabled.official_api.calls == []
    assert missing.official_api.calls == []


def test_numeric_id_tool_query_works_without_offline_catalog():
    plugin = tool_target(WowsConfig(
        official_api_enabled=True,
        official_api_application_id="configured",
        official_api_language="ja",
    ))

    result = asyncio.run(plugin.wows_query_ship_official(str(SHIP_ID)))

    assert result["ok"] is True
    assert plugin.official_api.calls == [(SHIP_ID, "top", "ja")]


@pytest.mark.parametrize(("kwargs", "code"), [
    ({"configuration": "stock"}, "invalid_configuration"),
    ({"language": "xx-evil"}, "invalid_language"),
])
def test_tool_validates_options_before_calling_client(kwargs, code):
    plugin = tool_target(WowsConfig(
        official_api_enabled=True,
        official_api_application_id="configured",
    ))

    result = asyncio.run(plugin.wows_query_ship_official(
        str(SHIP_ID), **kwargs))

    assert result["code"] == code
    assert plugin.official_api.calls == []


class ToolCatalogSnapshot:
    def __init__(self, ship: CatalogShip, profile: ShipProfile, meta: CatalogMeta):
        self.ship_value = ship
        self.profile = profile
        self.meta = meta
        self.closed = False

    def alias_candidates(self, alias):
        return (self.ship_value,) if alias == "yamato" else ()

    def primary_profile(self, ship_id):
        return self.profile if ship_id == self.ship_value.ship_id else None

    def close(self):
        self.closed = True


def test_text_tool_query_resolves_exact_offline_alias_and_closes_snapshot():
    ship = CatalogShip(
        ship_id=SHIP_ID,
        ship_index="PJSB018",
        name_key="IDS_PJSB018",
        display_name="大和",
        nation="Japan",
        ship_class="Battleship",
        tier=10,
    )
    profile = ShipProfile(
        profile_id=f"{SHIP_ID}:reference_top:primary",
        ship_id=SHIP_ID,
        configuration="reference_top",
        variant_key="primary",
        is_primary=True,
        profile_schema_version=1,
        data={},
        profile_sha256="d" * 64,
    )
    meta = CatalogMeta(
        1, "v1", "15.6.0", "live", "repo", "c" * 40, "d" * 64,
        "zh-CN", 1, 1,
    )
    snapshot = ToolCatalogSnapshot(ship, profile, meta)
    plugin = tool_target(WowsConfig(
        official_api_enabled=True,
        official_api_application_id="configured",
    ))
    plugin.ship_catalog_store = SimpleNamespace(snapshot=lambda: snapshot)

    result = asyncio.run(plugin.wows_query_ship_official("  ＹＡＭＡＴＯ  "))

    assert result["ok"] is True
    assert plugin.official_api.calls[0][0] == SHIP_ID
    assert snapshot.closed is True


def test_text_tool_query_falls_back_to_official_name_when_catalog_unavailable():
    plugin = tool_target(WowsConfig(
        official_api_enabled=True,
        official_api_application_id="configured",
        official_api_language="en",
    ))
    snapshot = SimpleNamespace(
        meta=None,
        alias_candidates=lambda _alias: (),
        primary_profile=lambda _ship_id: None,
        close=lambda: None,
    )
    plugin.ship_catalog_store = SimpleNamespace(snapshot=lambda: snapshot)

    result = asyncio.run(plugin.wows_query_ship_official("Yamato"))

    assert result["ok"] is True
    assert plugin.official_api.calls == []
    assert plugin.official_api.name_calls == [("Yamato", "top", "en")]


def test_text_tool_query_falls_back_to_official_name_when_catalog_misses():
    plugin = tool_target(WowsConfig(
        official_api_enabled=True,
        official_api_application_id="configured",
        official_api_language="en",
    ))
    closed = {"value": False}

    class MissSnapshot:
        meta = object()

        def alias_candidates(self, _alias):
            return ()

        def primary_profile(self, _ship_id):
            return None

        def close(self):
            closed["value"] = True

    plugin.ship_catalog_store = SimpleNamespace(snapshot=lambda: MissSnapshot())

    result = asyncio.run(plugin.wows_query_ship_official("Yamato"))

    assert result["ok"] is True
    assert closed["value"] is True
    assert plugin.official_api.calls == []
    assert plugin.official_api.name_calls == [("Yamato", "top", "en")]


def test_text_tool_query_falls_back_to_official_name_when_catalog_errors():
    plugin = tool_target(WowsConfig(
        official_api_enabled=True,
        official_api_application_id="configured",
        official_api_language="en",
    ))
    plugin.ship_catalog_store = SimpleNamespace(
        snapshot=lambda: (_ for _ in ()).throw(RuntimeError("catalog down")))

    result = asyncio.run(plugin.wows_query_ship_official("Yamato"))

    assert result["ok"] is True
    assert plugin.official_api.calls == []
    assert plugin.official_api.name_calls == [("Yamato", "top", "en")]
