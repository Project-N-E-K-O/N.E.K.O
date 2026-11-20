from __future__ import annotations

import ast
from pathlib import Path
import threading
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from main_routers.system_router import steam as system_router_module
from main_routers.system_router import _shared as system_router_shared
from utils import steam_state


class _FakeUserStats:
    def __init__(
        self,
        *,
        unlocked: bool = False,
        unlocked_names: set[str] | None = None,
        set_result: bool = True,
        playtime: int = 0,
        set_stat_result: bool = True,
    ) -> None:
        self.unlocked_names = set(unlocked_names or ())
        self._legacy_all_unlocked = bool(unlocked)
        self.set_result = set_result
        self.playtime = playtime
        self.set_stat_result = set_stat_result
        self.request_count = 0
        self.get_count = 0
        self.set_count = 0
        self.store_count = 0
        self.set_names: list[str] = []
        self.set_stat_calls: list[tuple[str, object]] = []

    def RequestCurrentStats(self) -> bool:
        self.request_count += 1
        return True

    def GetAchievement(self, name: str) -> bool:
        self.get_count += 1
        if self._legacy_all_unlocked:
            return True
        return name in self.unlocked_names

    def SetAchievement(self, name: str) -> bool:
        self.set_count += 1
        self.set_names.append(name)
        if self.set_result:
            self.unlocked_names.add(name)
            return True
        return False

    def GetStatInt(self, name: str) -> int:
        if name != "PLAY_TIME_SECONDS":
            raise KeyError(name)
        return self.playtime

    def SetStat(self, name: str, value: object) -> bool:
        self.set_stat_calls.append((name, value))
        if not self.set_stat_result:
            return False
        if name == "PLAY_TIME_SECONDS":
            self.playtime = int(value)
            # Simulate Steam Progress Stat auto-unlock after StoreStats.
            if self.playtime >= 300:
                self.unlocked_names.add("ACH_TIME_5MIN")
            if self.playtime >= 3600:
                self.unlocked_names.add("ACH_TIME_1HR")
            if self.playtime >= 360000:
                self.unlocked_names.add("ACH_TIME_100HR")
        return True

    def StoreStats(self) -> bool:
        self.store_count += 1
        return True


class _FakeSteamworks:
    def __init__(self, user_stats: _FakeUserStats) -> None:
        self.UserStats = user_stats
        self.callback_count = 0

    def run_callbacks(self) -> None:
        self.callback_count += 1


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch):
    async def _sleep_noop(_delay: float) -> None:
        return None

    monkeypatch.setattr(system_router_shared, "AUTOSTART_CSRF_TOKEN", "test-csrf-token")
    monkeypatch.setattr(system_router_module.asyncio, "sleep", _sleep_noop)

    app = FastAPI()
    app.include_router(system_router_module.router)
    with TestClient(app) as test_client:
        yield test_client


def _auth_headers() -> dict[str, str]:
    return {
        "Origin": "http://testserver",
        "X-CSRF-Token": "test-csrf-token",
    }


@pytest.mark.unit
def test_set_achievement_reports_already_unlocked_without_setting(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stats = _FakeUserStats(unlocked=True)
    steamworks = _FakeSteamworks(stats)
    monkeypatch.setattr(system_router_module, "get_steamworks", lambda: steamworks)

    response = client.post(
        "/api/steam/set-achievement-status/ACH_FIRST_DIALOGUE",
        headers=_auth_headers(),
    )

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "achievement": "ACH_FIRST_DIALOGUE",
        "newlyUnlocked": False,
        "alreadyUnlocked": True,
        "message": "成就 ACH_FIRST_DIALOGUE 已经解锁",
    }
    assert stats.set_count == 0
    assert stats.store_count == 0


@pytest.mark.unit
def test_set_achievement_reports_newly_unlocked_and_stores(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stats = _FakeUserStats(unlocked=False)
    steamworks = _FakeSteamworks(stats)
    monkeypatch.setattr(system_router_module, "get_steamworks", lambda: steamworks)

    response = client.post(
        "/api/steam/set-achievement-status/ACH_SEND_IMAGE",
        headers=_auth_headers(),
    )

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "achievement": "ACH_SEND_IMAGE",
        "newlyUnlocked": True,
        "alreadyUnlocked": False,
        "message": "成就 ACH_SEND_IMAGE 已解锁",
    }
    assert stats.set_count == 1
    assert stats.store_count == 1


_WAIT = 5.0


def _join_reconnect_threads() -> None:
    for thread in threading.enumerate():
        if thread.name == "steam-reconnect":
            thread.join(timeout=_WAIT)
            assert not thread.is_alive()


class _BlockingInitializer:
    """Fake initializer that blocks until released and records its calls."""

    def __init__(self, result: object = None) -> None:
        self.result = result
        self.release = threading.Event()
        self.started = threading.Event()
        self.calls = 0
        self.threads: list[threading.Thread] = []

    def __call__(self) -> object:
        self.calls += 1
        self.threads.append(threading.current_thread())
        self.started.set()
        assert self.release.wait(_WAIT)
        return self.result


@pytest.fixture
def empty_steam_cache(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(steam_state, "_steamworks", None)
    monkeypatch.setattr(steam_state, "_steamworks_initializer", None)
    monkeypatch.setattr(steam_state, "_last_init_attempt_monotonic", 0.0)
    monkeypatch.setattr(steam_state, "_init_in_flight", False)
    blocked: list[_BlockingInitializer] = []
    yield blocked
    for initializer in blocked:
        initializer.release.set()
    _join_reconnect_threads()


def _post_playtime(client: TestClient, seconds: int = 10):
    return client.post(
        "/api/steam/update-playtime",
        headers=_auth_headers(),
        json={"seconds": seconds},
    )


@pytest.mark.unit
def test_update_playtime_empty_cache_returns_503_and_reconnects_in_background(
    client: TestClient,
    empty_steam_cache: list,
) -> None:
    stats = _FakeUserStats()
    initializer = _BlockingInitializer(result=_FakeSteamworks(stats))
    empty_steam_cache.append(initializer)
    steam_state._steamworks_initializer = initializer

    response = _post_playtime(client)
    assert response.status_code == 503
    assert response.json()["error"] == "Steamworks未初始化"

    # The initializer runs on a daemon thread, not the request thread, and
    # the 503 above came back while it was still blocked.
    assert initializer.started.wait(_WAIT)
    assert initializer.threads[0].name == "steam-reconnect"
    assert initializer.threads[0].daemon is True
    assert initializer.threads[0] is not threading.current_thread()

    # Reports while the reconnect is in flight start no second one.
    for _ in range(5):
        assert _post_playtime(client).status_code == 503
    assert initializer.calls == 1

    initializer.release.set()
    _join_reconnect_threads()
    assert steam_state.get_steamworks() is initializer.result

    response = _post_playtime(client)
    assert response.status_code == 200
    assert stats.set_stat_calls == [("PLAY_TIME_SECONDS", 10)]
    assert stats.store_count == 1
    assert initializer.calls == 1


@pytest.mark.unit
def test_concurrent_reconnect_kicks_start_one_thread(empty_steam_cache: list) -> None:
    initializer = _BlockingInitializer()
    empty_steam_cache.append(initializer)
    steam_state._steamworks_initializer = initializer

    barrier = threading.Barrier(8)

    def _kick() -> None:
        barrier.wait(_WAIT)
        system_router_module._kick_steam_reconnect()

    kickers = [threading.Thread(target=_kick) for _ in range(8)]
    for thread in kickers:
        thread.start()
    for thread in kickers:
        thread.join(_WAIT)

    assert initializer.started.wait(_WAIT)
    initializer.release.set()
    _join_reconnect_threads()
    assert initializer.calls == 1
    assert not system_router_module._steam_reconnect_guard.locked()


@pytest.mark.unit
def test_reconnect_initializer_error_is_logged_and_retried_after_interval(
    client: TestClient,
    empty_steam_cache: list,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []

    def _failing_initializer():
        calls.append(1)
        raise RuntimeError("SteamAPI_Init boom")

    warnings: list[tuple] = []
    monkeypatch.setattr(system_router_module.logger, "warning", lambda *args, **_kw: warnings.append(args))
    steam_state._steamworks_initializer = _failing_initializer

    assert _post_playtime(client).status_code == 503
    _join_reconnect_threads()
    assert calls == [1]
    assert any("SteamAPI_Init boom" in str(arg) for args in warnings for arg in args)
    assert steam_state._init_in_flight is False
    assert not system_router_module._steam_reconnect_guard.locked()

    # Inside the retry interval the next kick does not call the initializer.
    assert _post_playtime(client).status_code == 503
    _join_reconnect_threads()
    assert calls == [1]

    # Once the interval has elapsed, a later report retries.
    steam_state._last_init_attempt_monotonic = 0.0
    assert _post_playtime(client).status_code == 503
    _join_reconnect_threads()
    assert calls == [1, 1]


@pytest.mark.unit
def test_reconnect_thread_start_failure_releases_guard(
    empty_steam_cache: list,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _UnstartableThread:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def start(self) -> None:
            raise RuntimeError("can't start new thread")

    monkeypatch.setattr(system_router_module.threading, "Thread", _UnstartableThread)

    system_router_module._kick_steam_reconnect()

    assert not system_router_module._steam_reconnect_guard.locked()


@pytest.mark.unit
def test_hung_initializer_does_not_block_other_callers_or_reinstall_late(
    client: TestClient,
    empty_steam_cache: list,
) -> None:
    initializer = _BlockingInitializer(result=_FakeSteamworks(_FakeUserStats()))
    empty_steam_cache.append(initializer)
    steam_state._steamworks_initializer = initializer

    assert _post_playtime(client).status_code == 503
    assert initializer.started.wait(_WAIT)

    # Every other caller returns promptly while the initializer is stuck.
    results: dict[str, object] = {}

    def _probe() -> None:
        results["ensure"] = steam_state.ensure_steamworks(force=True)
        results["get"] = steam_state.get_steamworks()
        steam_state.set_steamworks(None)  # e.g. shutdown clearing the handle
        results["done"] = True

    prober = threading.Thread(target=_probe)
    prober.start()
    prober.join(_WAIT)
    assert not prober.is_alive()
    assert results == {"ensure": None, "get": None, "done": True}
    assert initializer.calls == 1
    assert initializer.threads[0].daemon is True

    # The init finishing after the reset must not resurrect the handle.
    initializer.release.set()
    _join_reconnect_threads()
    assert steam_state.get_steamworks() is None
    assert steam_state._init_in_flight is False


def _load_production_steamworks_initializer(initialize_steamworks):
    """Load ensure_steamworks_initialized from app/main_server/__init__.py without importing the package."""
    path = Path(__file__).resolve().parents[2] / "app/main_server/__init__.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "ensure_steamworks_initialized"
    )
    namespace = {
        "steamworks": None,
        "logger": Mock(),
        "get_default_steam_info": lambda: None,
        "initialize_steamworks": initialize_steamworks,
    }
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"), namespace)
    return namespace["ensure_steamworks_initialized"], namespace


@pytest.mark.unit
def test_production_initializer_does_not_publish_after_reset(
    empty_steam_cache: list,
) -> None:
    blocking = _BlockingInitializer(result=_FakeSteamworks(_FakeUserStats()))
    empty_steam_cache.append(blocking)
    initializer, _namespace = _load_production_steamworks_initializer(
        lambda *, quiet=False: blocking()
    )
    steam_state.set_steamworks_initializer(initializer)

    worker = threading.Thread(
        target=lambda: steam_state.ensure_steamworks(force=True),
        daemon=True,
    )
    worker.start()
    try:
        assert blocking.started.wait(_WAIT)
        steam_state.set_steamworks(None)
        blocking.release.set()
        worker.join(_WAIT)
        assert not worker.is_alive()
    finally:
        blocking.release.set()
        worker.join(_WAIT)

    assert steam_state.get_steamworks() is None


@pytest.mark.unit
def test_production_initializer_publishes_handle_without_reset(
    empty_steam_cache: list,
) -> None:
    expected = _FakeSteamworks(_FakeUserStats())
    initializer, namespace = _load_production_steamworks_initializer(
        lambda *, quiet=False: expected
    )
    steam_state.set_steamworks_initializer(initializer)

    returned = steam_state.ensure_steamworks(force=True)

    assert returned is expected
    assert steam_state.get_steamworks() is expected
    assert namespace["steamworks"] is expected


@pytest.mark.unit
def test_update_playtime_sets_progress_stat_without_set_achievement(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stats = _FakeUserStats(playtime=100)
    steamworks = _FakeSteamworks(stats)
    monkeypatch.setattr(system_router_module, "get_cached_steamworks", lambda: steamworks)

    response = client.post(
        "/api/steam/update-playtime",
        headers=_auth_headers(),
        json={"seconds": 50},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["totalPlayTime"] == 150
    assert body["added"] == 50
    assert body["stat"] == "PLAY_TIME_SECONDS"
    assert body["progressUnlocked"] == []
    assert stats.set_stat_calls == [("PLAY_TIME_SECONDS", 150)]
    assert stats.store_count == 1
    assert stats.set_count == 0  # Progress Stat path never calls SetAchievement


@pytest.mark.unit
def test_update_playtime_reports_progress_unlocked_after_threshold(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stats = _FakeUserStats(playtime=280)
    steamworks = _FakeSteamworks(stats)
    monkeypatch.setattr(system_router_module, "get_cached_steamworks", lambda: steamworks)

    response = client.post(
        "/api/steam/update-playtime",
        headers=_auth_headers(),
        json={"seconds": 30},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["totalPlayTime"] == 310
    assert body["progressUnlocked"] == ["ACH_TIME_5MIN"]
    assert stats.set_count == 0
    assert "ACH_TIME_5MIN" in stats.unlocked_names


@pytest.mark.unit
def test_update_playtime_caps_single_report_to_one_hour(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stats = _FakeUserStats(playtime=0)
    steamworks = _FakeSteamworks(stats)
    monkeypatch.setattr(system_router_module, "get_cached_steamworks", lambda: steamworks)

    response = client.post(
        "/api/steam/update-playtime",
        headers=_auth_headers(),
        json={"seconds": 99999},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["added"] == 3600
    assert body["totalPlayTime"] == 3600
    assert stats.set_stat_calls == [("PLAY_TIME_SECONDS", 3600)]


@pytest.mark.unit
def test_update_playtime_rejects_negative_seconds(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stats = _FakeUserStats()
    steamworks = _FakeSteamworks(stats)
    monkeypatch.setattr(system_router_module, "get_cached_steamworks", lambda: steamworks)

    response = client.post(
        "/api/steam/update-playtime",
        headers=_auth_headers(),
        json={"seconds": -1},
    )

    assert response.status_code == 400
    assert stats.set_stat_calls == []


@pytest.mark.unit
@pytest.mark.parametrize("raw_seconds", ["Infinity", "-Infinity", "NaN"])
def test_update_playtime_rejects_non_finite_seconds(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    raw_seconds: str,
) -> None:
    """json.loads accepts bare Infinity/NaN; int() then raises OverflowError
    (Infinity) or ValueError (NaN) — both must surface as 400, not 500."""
    stats = _FakeUserStats()
    steamworks = _FakeSteamworks(stats)
    monkeypatch.setattr(system_router_module, "get_cached_steamworks", lambda: steamworks)

    response = client.post(
        "/api/steam/update-playtime",
        headers={**_auth_headers(), "Content-Type": "application/json"},
        content=('{"seconds": %s}' % raw_seconds).encode("utf-8"),
    )

    assert response.status_code == 400
    assert stats.set_stat_calls == []
