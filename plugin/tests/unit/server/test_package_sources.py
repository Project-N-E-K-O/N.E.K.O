from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
import time

from fastapi import FastAPI
import httpx
import pytest

from plugin.server.infrastructure import package_sources as sources
from plugin.server.routes import market_bridge as bridge

pytestmark = pytest.mark.plugin_unit
URL = "https://github.com/example/plugin/releases/download/v1/plugin.neko-plugin"


@pytest.fixture(autouse=True)
def isolated_preferences(tmp_path, monkeypatch):
    monkeypatch.setattr(sources, "preferences_path", lambda: tmp_path / "market-download.json")
    monkeypatch.setattr(bridge, "_DOWNLOAD_PREFERENCES_LOCK", asyncio.Lock())
    sources._routes.clear()
    monkeypatch.setattr(sources, "_loaded", True)
    yield
    sources._routes.clear()


def record(source_id, *, now=None, **values):
    """Commit one observation synchronously, as a finished transfer would."""
    asyncio.run(sources.commit_route_observations(
        [sources.RouteObservation(source_id, **values)], sources.route_generation(), now=now))


def test_preferences_and_candidate_boundaries():
    assert sources.load_preferences() == (sources.DownloadPreferences(), False)
    direct = sources.DownloadPreferences(mode="direct")
    assert sources.download_candidates(URL, direct) == [("github-direct", URL)]
    selected = sources.DownloadPreferences(mode="specified", specified_source_id="cdn-gh-proxy-org")
    assert sources.download_candidates(URL, selected) == [("cdn-gh-proxy-org", "https://cdn.gh-proxy.org/" + URL)]
    sources.save_preferences(selected)
    assert sources.load_preferences() == (selected, True)
    for url in ["https://example.com/file", "https://user:password@github.com/a/releases/download/b/c"]:
        assert sources.download_candidates(url, sources.DownloadPreferences()) == [(httpx.URL(url).host, url)]


def test_measured_routes_change_priority_without_removing_other_routes():
    record("cdn-gh-proxy-org", latency=0.2, throughput=8 * 1024 * 1024)
    record("github-direct", latency=1.2, throughput=20 * 1024)
    candidates = sources.download_candidates(URL, sources.DownloadPreferences())
    assert candidates[0][0] == "cdn-gh-proxy-org"
    assert candidates[-1][0] == "github-direct"
    assert len(candidates) == 6
    asyncio.run(sources.reset_transfer_history())
    assert sources.download_candidates(URL, sources.DownloadPreferences())[0][0] == "github-direct"


def test_latency_dominates_typical_packages_and_failures_decay():
    # Fast first byte beats raw throughput for a megabyte-sized package.
    record("gh-proxy-com", latency=0.15, throughput=4 * 1024 * 1024)
    record("hk-gh-proxy-org", latency=1.5, throughput=40 * 1024 * 1024)
    order = [key for key, _ in sources.download_candidates(URL, sources.DownloadPreferences())]
    assert order.index("gh-proxy-com") < order.index("hk-gh-proxy-org")

    record("gh-proxy-com", failed=True, now=1000.0)
    now = 1000.0
    penalised = sources.estimate_route("gh-proxy-com", now=now).latency
    recovered = sources.estimate_route("gh-proxy-com", now=now + 4 * 3600).latency
    assert penalised > 1.5 and recovered < 0.5


async def test_concurrent_commits_snapshot_on_the_loop_and_keep_the_newest_write():
    await asyncio.gather(*(sources.commit_route_observations(
        [sources.RouteObservation(f"route-{index}", latency=0.1 * (index + 1))], sources.route_generation(),
    ) for index in range(20)))
    saved = json.loads(sources.route_stats_path().read_text(encoding="utf-8"))["routes"]
    assert len(saved) == 20


async def test_a_commit_from_before_a_reset_is_dropped():
    generation = sources.route_generation()
    await sources.reset_transfer_history()
    await sources.commit_route_observations([sources.RouteObservation("github-direct", latency=0.1)], generation)
    assert sources._routes == {}
    assert not sources.route_stats_path().exists()


async def test_a_reset_keeps_statistics_written_after_it(monkeypatch):
    await sources.commit_route_observations(
        [sources.RouteObservation("github-direct", latency=0.1)], sources.route_generation())
    run_file_operation = sources.run_file_operation

    async def commit_before_deleting(operation, *args):
        # A transfer that began after the reset finishes before the deletion runs.
        if operation is sources._delete_stats_file:
            await sources.commit_route_observations(
                [sources.RouteObservation("gh-proxy-com", latency=0.2)], sources.route_generation())
        return await run_file_operation(operation, *args)

    monkeypatch.setattr(sources, "run_file_operation", commit_before_deleting)
    await sources.reset_transfer_history()
    saved = json.loads(sources.route_stats_path().read_text(encoding="utf-8"))["routes"]
    assert list(saved) == ["gh-proxy-com"]


def test_unreadable_route_statistics_file_falls_back_to_priors():
    sources.route_stats_path().mkdir()
    sources._routes.clear()
    sources._loaded = False
    assert sources.estimate_route("github-direct").confidence == 0.0


async def test_an_unreadable_saved_choice_is_not_replaced_by_seeding(monkeypatch):
    sources.save_preferences(sources.DownloadPreferences(mode="direct"))

    def unreadable(path):
        raise PermissionError(13, "Permission denied", str(path))

    monkeypatch.setattr(sources, "read_json_tolerating_replace", unreadable)
    app = FastAPI()
    app.include_router(bridge.router)
    params = {"token": bridge.get_bridge_token()}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://local") as client:
        assert (await client.get("/market/download-preferences", params=params)).json()["configured"] is True
        await client.put("/market/download-preferences", params=params, json={
            "mode": "specified", "specified_source_id": "gh-proxy-com", "initialize_only": True,
        })
    assert json.loads(sources.preferences_path().read_text(encoding="utf-8"))["mode"] == "direct"


def test_invalid_saved_preferences_count_as_unconfigured():
    sources.preferences_path().write_text('{"mode": "turbo"}', encoding="utf-8")
    assert sources.load_preferences() == (sources.DownloadPreferences(), False)


def test_route_estimates_persist_and_regress_towards_prior_with_age():
    record("cdn-gh-proxy-org", latency=0.1, throughput=30 * 1024 * 1024, now=0.0)
    sources._routes.clear()
    sources._loaded = False
    fresh = sources.estimate_route("cdn-gh-proxy-org", now=0.0)
    assert fresh.confidence > 0 and fresh.latency < 0.6
    stale = sources.estimate_route("cdn-gh-proxy-org", now=30 * 24 * 3600.0)
    assert abs(stale.latency - sources._PRIOR_LATENCY) < 0.01


def test_unreadable_route_statistics_fall_back_to_priors(tmp_path):
    sources.route_stats_path().write_text('{"routes": {"github-direct": {"latency": "x"}}}', encoding="utf-8")
    sources._routes.clear()
    sources._loaded = False
    estimate = sources.estimate_route("github-direct")
    assert estimate.confidence == 0.0
    assert sources.download_candidates(URL, sources.DownloadPreferences())[0][0] == "github-direct"


async def test_warmup_covers_the_opening_race_and_githubs_asset_host(monkeypatch):
    warmed = []

    async def warm(urls):
        warmed.extend(urls)

    monkeypatch.setattr(bridge, "warm_package_connections", warm)
    await bridge._warm_package_routes(URL, sources.DownloadPreferences())
    assert warmed == [URL, "https://gh-proxy.com/" + URL, "https://gh-proxy.org/" + URL,
                      "https://release-assets.githubusercontent.com/"]

    warmed.clear()
    await sources.commit_route_observations(
        [sources.RouteObservation("github-direct", latency=2.0)], sources.route_generation())
    await bridge._warm_package_routes(URL, sources.DownloadPreferences())
    # Direct is no longer in the opening race, so its redirect target is not warmed.
    assert len(warmed) == 3 and "https://release-assets.githubusercontent.com/" not in warmed


async def test_preferences_are_authenticated_and_migrated_only_once():
    app = FastAPI()
    app.include_router(bridge.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://local") as client:
        assert (await client.get("/market/download-preferences", params={"token": "wrong"})).status_code == 403
        params = {"token": bridge.get_bridge_token()}
        original = await client.get("/market/download-preferences", params=params)
        assert original.json()["configured"] is False
        first = await client.put("/market/download-preferences", params=params, json={
            "mode": "specified", "specified_source_id": "gh-proxy-com", "initialize_only": True,
        })
        assert first.json()["mode"] == "specified"
        second = await client.put("/market/download-preferences", params=params, json={
            "mode": "direct", "initialize_only": True,
        })
        assert second.json()["mode"] == "specified"
        assert (await client.put("/market/download-preferences", params=params,
                                 json={"specified_source_id": "arbitrary-host"})).status_code == 422
        assert (await client.post("/market/download-history/reset", params=params)).status_code == 200


@pytest.mark.parametrize("snapshot", [None, sources.DownloadPreferences(mode="direct")])
async def test_saved_policy_uses_catalogue_url_and_one_shared_deadline(monkeypatch, snapshot):
    request = bridge.MarketInstallRequest(
        package_url="https://caller.test/unused", canonical_package_url=URL,
        package_sha256="a" * 64, plugin_id="42", version="1",
    )
    task = {"task_id": "policy-test", "cancel_requested": False, "created_at": 0}
    if snapshot is not None:
        sources.save_preferences(snapshot)
    monkeypatch.setitem(bridge._tasks, "policy-test", task)

    async def bind(payload):
        return payload, {}

    seen = []

    async def install(current, payload, context):
        seen.append(payload.package_url)
        route = current["_download_route"]
        assert len(route.candidates) == (1 if snapshot else 6)
        assert route.deadline > 0
        current["result"] = {}

    async def report(*args):
        return None

    monkeypatch.setattr(bridge, "_bind_market_package_hash", bind)
    monkeypatch.setattr(bridge, "_do_install", install)
    monkeypatch.setattr(bridge, "_record_market_download_best_effort", lambda *args: None)
    monkeypatch.setattr(bridge, "_report_market_install_best_effort", report)
    await bridge._execute_install("policy-test", request)
    assert seen == [URL]
    assert task["status"] == "completed"


def test_task_status_exposes_the_route_in_use():
    status = bridge.MarketTaskStatus(task_id="t", status="downloading", active_sources=["gh-proxy-com"])
    assert status.model_dump()["active_sources"] == ["gh-proxy-com"]
    assert dataclasses.asdict(sources.DownloadPreferences())["mode"] == "auto"


@pytest.mark.parametrize("after_hash_mismatch", [False, True])
def test_no_direct_retry_once_the_deadline_has_passed(after_hash_mismatch):
    route = bridge._DownloadRoute(
        candidates=(("gh-proxy-com", "https://gh-proxy.com/" + URL),), direct_fallback=True,
        deadline=time.monotonic() - 1,
    )
    task = {"_download_route": route}
    assert not bridge._retry_from_github_direct(task, after_hash_mismatch=after_hash_mismatch)
    assert task["_download_route"] is route


async def test_mixed_source_hash_failure_retries_once_without_resetting_budget(tmp_path, monkeypatch):
    bad = tmp_path / "bad.neko-plugin"
    good = tmp_path / "good.neko-plugin"
    bad.write_bytes(b"bad")
    good.write_bytes(b"verified")
    route = bridge._DownloadRoute(
        candidates=(("github-direct", URL),), direct_fallback=False, deadline=time.monotonic() + 60,
    )
    task = {"_download_route": route}
    attempts = []

    async def direct(url, state):
        attempts.append(url)
        assert state["_download_route"] == route.direct_only()
        return good

    monkeypatch.setattr(bridge, "_download_package_once", direct)
    path, check = await bridge._verify_downloaded_package_with_fallback(
        URL, bad, hashlib.sha256(b"verified").hexdigest(), task,
    )
    assert path == good and check == "passed"
    assert attempts == [URL]
    assert not bad.exists()
