from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from main_routers import system_router as system_router_module


class _FakeUserStats:
    def __init__(self, *, unlocked: bool, set_result: bool = True) -> None:
        self.unlocked = unlocked
        self.set_result = set_result
        self.request_count = 0
        self.get_count = 0
        self.set_count = 0
        self.store_count = 0

    def RequestCurrentStats(self) -> bool:
        self.request_count += 1
        return True

    def GetAchievement(self, name: str) -> bool:
        self.get_count += 1
        return self.unlocked

    def SetAchievement(self, name: str) -> bool:
        self.set_count += 1
        if self.set_result:
            self.unlocked = True
        return self.set_result

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

    monkeypatch.setattr(system_router_module, "AUTOSTART_CSRF_TOKEN", "test-csrf-token")
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
