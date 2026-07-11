from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from main_routers.system_router import steam as system_router_module
from main_routers.system_router import _shared as system_router_shared


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


@pytest.mark.unit
def test_update_playtime_requires_steamworks(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(system_router_module, "get_steamworks", lambda: None)

    response = client.post(
        "/api/steam/update-playtime",
        headers=_auth_headers(),
        json={"seconds": 10},
    )

    assert response.status_code == 503
    assert response.json()["error"] == "Steamworks未初始化"


@pytest.mark.unit
def test_update_playtime_sets_progress_stat_without_set_achievement(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stats = _FakeUserStats(playtime=100)
    steamworks = _FakeSteamworks(stats)
    monkeypatch.setattr(system_router_module, "get_steamworks", lambda: steamworks)

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
    monkeypatch.setattr(system_router_module, "get_steamworks", lambda: steamworks)

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
    monkeypatch.setattr(system_router_module, "get_steamworks", lambda: steamworks)

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
    monkeypatch.setattr(system_router_module, "get_steamworks", lambda: steamworks)

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
    monkeypatch.setattr(system_router_module, "get_steamworks", lambda: steamworks)

    response = client.post(
        "/api/steam/update-playtime",
        headers={**_auth_headers(), "Content-Type": "application/json"},
        content=('{"seconds": %s}' % raw_seconds).encode("utf-8"),
    )

    assert response.status_code == 400
    assert stats.set_stat_calls == []
