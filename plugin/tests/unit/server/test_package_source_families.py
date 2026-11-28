from __future__ import annotations

import asyncio
import dataclasses
import json

from fastapi import FastAPI
import httpx
import pytest

from plugin.server.infrastructure import package_sources as sources
from plugin.server.routes import market_bridge as bridge
from tests.fake_clock import patch_module_clock

pytestmark = pytest.mark.plugin_unit
GITHUB_URL = "https://github.com/example/plugin/releases/download/v1/plugin.neko-plugin"
SIMPLE_URL = "https://pypi.org/simple/iniconfig/"
PYPI_IDS = [key for key, _ in sources.PYPI_INDEX_SOURCES]


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(sources, "preferences_path", lambda: tmp_path / "market-download.json")
    monkeypatch.setattr(bridge, "_DOWNLOAD_PREFERENCES_LOCK", asyncio.Lock())
    monkeypatch.setattr(sources, "_last_pypi_probe", None)
    monkeypatch.setattr(sources, "_pypi_probe_lock", None)
    sources._routes.clear()
    monkeypatch.setattr(sources, "_loaded", True)
    yield
    sources._routes.clear()


class FakeIndex:
    """Answers simple-page probes; ``fail`` names the routes that error."""

    def __init__(self, *, fail=(), status=None, body=None, delay=0.0):
        self.fail = set(fail)
        self.status = status or {}
        self.body = body or {}
        self.delay = delay
        self.requests: list[str] = []

    def client(self):
        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            self.requests.append(url)
            source_id = next(key for key, base in sources.PYPI_INDEX_SOURCES if url.startswith(base))
            if source_id in self.fail:
                raise httpx.ConnectError("refused", request=request)
            page = self.body.get(source_id, b'<a href="../../packages/x/iniconfig-2.0.0-py3-none-any.whl#sha256=00">')
            return httpx.Response(self.status.get(source_id, 200), content=page)

        async def async_handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            source_id = next(key for key, base in sources.PYPI_INDEX_SOURCES if url.startswith(base))
            delay = self.delay.get(source_id, 0.0) if isinstance(self.delay, dict) else self.delay
            if delay:
                await asyncio.sleep(delay)
            return handler(request)

        return httpx.AsyncClient(transport=httpx.MockTransport(async_handler))


@pytest.fixture
def index(monkeypatch):
    fake = FakeIndex()
    client = fake.client()

    async def shared_client():
        return client

    monkeypatch.setattr(sources, "package_http_client", shared_client)
    return fake


# -- families: matching and rewriting ---------------------------------------------------------

def test_each_family_matches_its_own_origin_only():
    assert sources.family_for(GITHUB_URL) is sources.GITHUB_FAMILY
    assert sources.family_for(SIMPLE_URL) is sources.PYPI_FAMILY
    for url in [
        "https://example.com/file",
        "https://user:password@github.com/a/releases/download/b/c",
        "http://pypi.org/simple/six/",
        "https://pypi.org/project/six/",
        "https://files.pythonhosted.org/packages/aa/bb/six-1.0-py3-none-any.whl",
        "https://user@pypi.org/simple/six/",
    ]:
        assert sources.family_for(url) is None, url


def test_github_mirrors_prefix_and_pypi_mirrors_rebase():
    assert sources.GITHUB_FAMILY.mirror_url("gh-proxy-org", GITHUB_URL) == "https://gh-proxy.org/" + GITHUB_URL
    assert (sources.PYPI_FAMILY.mirror_url("pypi-aliyun", SIMPLE_URL)
            == "https://mirrors.aliyun.com/pypi/simple/iniconfig/")
    with pytest.raises(ValueError):
        sources.PYPI_FAMILY.mirror_url("pypi-aliyun", "https://example.com/simple/iniconfig/")


def test_route_ids_of_the_families_do_not_overlap():
    github = {key for key, _ in sources.GITHUB_DOWNLOAD_SOURCES}
    pypi = set(PYPI_IDS)
    assert github.isdisjoint(pypi) and len(pypi) == len(PYPI_IDS)
    assert not (sources.GITHUB_FAMILY.retired_ids | sources.PYPI_FAMILY.retired_ids) & (github | pypi)


def test_github_candidates_are_unchanged_by_the_families():
    preferences = sources.DownloadPreferences(specified_pypi_source_id="pypi-ustc")
    auto = sources.download_candidates(GITHUB_URL, preferences)
    assert [key for key, _ in auto] == [key for key, _ in sources.GITHUB_DOWNLOAD_SOURCES]
    assert auto[0] == ("github-direct", GITHUB_URL)
    assert all(url == prefix + GITHUB_URL for (_, url), (_, prefix) in zip(auto[1:], sources.GITHUB_DOWNLOAD_SOURCES[1:]))
    # PyPI measurements never reorder GitHub routes.
    for key in PYPI_IDS:
        sources._record(sources.RouteObservation(key, latency=0.01), sources.time.time())
    assert sources.download_candidates(GITHUB_URL, preferences) == auto


def test_each_family_uses_its_own_specified_mirror():
    preferences = sources.DownloadPreferences(
        mode="specified", specified_source_id="cdn-gh-proxy-org", specified_pypi_source_id="pypi-tencent",
    )
    assert sources.download_candidates(GITHUB_URL, preferences) == [
        ("cdn-gh-proxy-org", "https://cdn.gh-proxy.org/" + GITHUB_URL)]
    assert sources.download_candidates(SIMPLE_URL, preferences) == [
        ("pypi-tencent", "https://mirrors.cloud.tencent.com/pypi/simple/iniconfig/")]


# -- preferences --------------------------------------------------------------------------------

def test_a_file_from_before_the_families_reads_as_the_github_mirror():
    sources.preferences_path().write_text(
        '{"mode": "specified", "specified_source_id": "edgeone-gh-proxy-org"}', encoding="utf-8")
    assert sources.load_preferences() == (sources.DownloadPreferences(
        mode="specified", specified_source_id="edgeone-gh-proxy-org",
        specified_pypi_source_id=sources.PYPI_FAMILY.default_mirror_id), True)


def test_saved_file_round_trips_and_keeps_the_keys_old_versions_read():
    preferences = sources.DownloadPreferences(
        mode="direct", specified_source_id="gh-proxy-org", specified_pypi_source_id="pypi-aliyun")
    sources.save_preferences(preferences)
    saved = json.loads(sources.preferences_path().read_text(encoding="utf-8"))
    # What a version before the families reads: these two keys, extra keys ignored.
    assert sources.validate_preferences(saved["mode"], saved["specified_source_id"]).specified_source_id == "gh-proxy-org"
    assert sources.load_preferences() == (preferences, True)


@pytest.mark.parametrize("pypi_value", ["pypi-gone", 42, ["pypi-tuna"]])
def test_an_unusable_pypi_mirror_keeps_the_github_choice(pypi_value):
    sources.preferences_path().write_text(json.dumps({
        "mode": "specified", "specified_source_id": "gh-proxy-org", "specified_pypi_source_id": pypi_value,
    }), encoding="utf-8")
    assert sources.load_preferences() == (sources.DownloadPreferences(
        mode="specified", specified_source_id="gh-proxy-org"), True)


def test_a_retired_pypi_mirror_falls_back_to_the_default(monkeypatch):
    retiring = dataclasses.replace(sources.PYPI_FAMILY, retired_ids=frozenset({"pypi-old"}))
    monkeypatch.setattr(sources, "PYPI_FAMILY", retiring)
    assert sources.validate_preferences("auto", "gh-proxy-com", "pypi-old").specified_pypi_source_id == "pypi-tuna"
    with pytest.raises(ValueError):
        sources.validate_preferences("auto", "gh-proxy-com", "pypi-unknown")


async def test_an_old_page_saving_keeps_the_saved_pypi_mirror_and_a_new_page_sets_it():
    sources.save_preferences(sources.DownloadPreferences(specified_pypi_source_id="pypi-ustc"))
    app = FastAPI()
    app.include_router(bridge.router)
    params = {"token": bridge.get_bridge_token()}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://local") as client:
        old_page = await client.put("/market/download-preferences", params=params, json={
            "mode": "specified", "specified_source_id": "gh-proxy-org",
        })
        assert old_page.json()["specified_pypi_source_id"] == "pypi-ustc"
        new_page = await client.put("/market/download-preferences", params=params, json={
            "mode": "specified", "specified_source_id": "gh-proxy-org", "specified_pypi_source_id": "pypi-aliyun",
        })
        assert new_page.json()["specified_pypi_source_id"] == "pypi-aliyun"
        rejected = await client.put("/market/download-preferences", params=params, json={
            "mode": "specified", "specified_source_id": "gh-proxy-org", "specified_pypi_source_id": "evil.example",
        })
        assert rejected.status_code == 422
        current = await client.get("/market/download-preferences", params=params)
    assert current.json()["specified_pypi_source_id"] == "pypi-aliyun"
    assert sources.load_preferences()[0].specified_pypi_source_id == "pypi-aliyun"


# -- PyPI index selection -----------------------------------------------------------------------

async def test_direct_and_specified_modes_do_not_probe(index):
    assert await sources.select_pypi_index_url(sources.DownloadPreferences(mode="direct")) == "https://pypi.org/simple"
    assert await sources.select_pypi_index_url(sources.DownloadPreferences(
        mode="specified", specified_pypi_source_id="pypi-aliyun")) == "https://mirrors.aliyun.com/pypi/simple"
    assert index.requests == []


async def test_saved_preferences_are_used_when_none_are_given(index):
    sources.save_preferences(sources.DownloadPreferences(mode="specified", specified_pypi_source_id="pypi-tencent"))
    assert await sources.select_pypi_index_url() == "https://mirrors.cloud.tencent.com/pypi/simple"


async def test_auto_mode_probes_every_route_and_takes_the_fastest(index):
    index.delay = {key: 0.0 if key == "pypi-tuna" else 0.15 for key in PYPI_IDS}
    url = await sources.select_pypi_index_url(sources.DownloadPreferences())
    assert sorted(index.requests) == sorted(base + "iniconfig/" for _, base in sources.PYPI_INDEX_SOURCES)
    assert url == "https://pypi.tuna.tsinghua.edu.cn/simple"
    assert all(sources._routes[key].samples == 1 for key in PYPI_IDS)
    persisted = json.loads(sources.route_stats_path().read_text(encoding="utf-8"))["routes"]
    assert set(PYPI_IDS) <= set(persisted)


async def test_a_failing_route_is_penalised_and_not_selected(index):
    index.fail = {"pypi-direct", "pypi-tuna"}
    index.status = {"pypi-aliyun": 503}
    index.body = {"pypi-ustc": b"<html>maintenance</html>"}
    url = await sources.select_pypi_index_url(sources.DownloadPreferences())
    assert url == "https://mirrors.cloud.tencent.com/pypi/simple"
    for key in ["pypi-direct", "pypi-tuna", "pypi-aliyun", "pypi-ustc"]:
        assert sources._routes[key].failures == 1.0 and sources._routes[key].latency is None
    assert sources._routes["pypi-tencent"].failures == 0.0


async def test_probing_is_skipped_while_fresh_and_retried_only_after_an_interval(index, monkeypatch):
    now = [1_000_000.0]
    patch_module_clock(monkeypatch, sources, time=lambda: now[0])
    await sources.select_pypi_index_url(sources.DownloadPreferences())
    assert len(index.requests) == 5
    now[0] += sources.PYPI_PROBE_RETRY_SECONDS + 1
    await sources.select_pypi_index_url(sources.DownloadPreferences())
    assert len(index.requests) == 5, "fresh measurements are reused"
    now[0] += sources.PYPI_PROBE_FRESH_SECONDS
    await sources.select_pypi_index_url(sources.DownloadPreferences())
    assert len(index.requests) == 10


async def test_a_probe_that_failed_everywhere_waits_for_the_retry_interval(index, monkeypatch):
    now = [1_000_000.0]
    patch_module_clock(monkeypatch, sources, time=lambda: now[0])
    index.fail = set(PYPI_IDS)
    # Nothing answered: every route carries the same penalty, so declared order wins.
    assert await sources.select_pypi_index_url(sources.DownloadPreferences()) == "https://pypi.org/simple"
    await sources.select_pypi_index_url(sources.DownloadPreferences())
    assert len(index.requests) == 5
    now[0] += sources.PYPI_PROBE_RETRY_SECONDS + 1
    index.fail = set()
    await sources.select_pypi_index_url(sources.DownloadPreferences())
    assert len(index.requests) == 10


async def test_a_clock_set_back_does_not_hold_off_probing(index, monkeypatch):
    now = [1_000_000.0]
    patch_module_clock(monkeypatch, sources, time=lambda: now[0])
    index.fail = set(PYPI_IDS)
    await sources.select_pypi_index_url(sources.DownloadPreferences())
    now[0] -= 3600
    await sources.select_pypi_index_url(sources.DownloadPreferences())
    assert len(index.requests) == 10


async def test_a_reset_forgets_pypi_measurements_and_probes_again(index):
    await sources.select_pypi_index_url(sources.DownloadPreferences())
    await sources.reset_transfer_history()
    await sources.select_pypi_index_url(sources.DownloadPreferences())
    assert len(index.requests) == 10


async def test_concurrent_callers_share_one_probe(index):
    index.delay = 0.05
    urls = await asyncio.gather(*(sources.select_pypi_index_url(sources.DownloadPreferences()) for _ in range(4)))
    assert len(set(urls)) == 1 and len(index.requests) == 5


async def test_a_slow_route_times_out_as_a_failure(index, monkeypatch):
    monkeypatch.setattr(sources, "PYPI_PROBE_TIMEOUT", 0.05)
    index.delay = 1.0
    await sources.select_pypi_index_url(sources.DownloadPreferences())
    assert all(sources._routes[key].failures == 1.0 for key in PYPI_IDS)


async def test_cancelling_the_caller_stops_the_probe_without_recording(index):
    index.delay = 10.0
    task = asyncio.create_task(sources.select_pypi_index_url(sources.DownloadPreferences()))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not any(key in sources._routes for key in PYPI_IDS)
    assert sources._last_pypi_probe is None, "a cancelled probe is not counted, so the next caller probes"


async def test_a_probe_from_before_a_reset_is_dropped(index, monkeypatch):
    probe = sources._probe_pypi_routes

    async def reset_during_probe():
        observations = await probe()
        await sources.reset_transfer_history()
        return observations

    monkeypatch.setattr(sources, "_probe_pypi_routes", reset_during_probe)
    await sources.select_pypi_index_url(sources.DownloadPreferences())
    assert not any(key in sources._routes for key in PYPI_IDS)
    assert sources._last_pypi_probe is None
    monkeypatch.setattr(sources, "_probe_pypi_routes", probe)
    await sources.select_pypi_index_url(sources.DownloadPreferences())
    assert len(index.requests) == 10, "the next caller probes again"
    assert all(sources._routes[key].samples == 1 for key in PYPI_IDS)
