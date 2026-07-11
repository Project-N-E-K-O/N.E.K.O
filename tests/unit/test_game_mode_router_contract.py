import asyncio
import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

import pytest

import main_routers.game_mode_router as game_mode_router_module
from main_routers.system_router import _shared as system_router_shared
from main_routers.game_mode_router import router


def _client(*, secure: bool = True) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    if secure:
        client.headers.update({
            "Origin": "http://testserver",
            "X-CSRF-Token": system_router_shared.AUTOSTART_CSRF_TOKEN,
        })
    return client


def test_game_mode_beta_http_state_enable_manual_restore_and_disable_flow(monkeypatch):
    monkeypatch.setenv("NEKO_GAME_MODE_DEBUG", "1")

    with _client() as client:
        try:
            client.post("/api/game-mode-beta/enabled", json={"enabled": False})

            state_response = client.get("/api/game-mode-beta/state")
            assert state_response.status_code == 200
            state = state_response.json()["state"]
            assert state["enabled"] is False
            assert state["pressure_state"] == "normal"
            assert state["trigger_reason"] is None

            enable_response = client.post("/api/game-mode-beta/enabled", json={"enabled": True})
            assert enable_response.status_code == 200
            enabled_state = enable_response.json()["state"]
            assert enabled_state["enabled"] is True

            trigger_response = client.post(
                "/api/game-mode-beta/debug/trigger",
                json={"reason": "http-contract", "percent": 97},
            )
            assert trigger_response.status_code == 200
            trigger_state = trigger_response.json()["state"]
            assert trigger_state["auto_switch_active"] is True

            restore_response = client.post("/api/game-mode-beta/manual-restore")
            assert restore_response.status_code == 200
            restore_state = restore_response.json()["state"]
            assert restore_state["enabled"] is True
            assert restore_state["manual_override"] is True
            assert restore_state["auto_switch_active"] is False
            assert restore_state["suppressed_until"] is not None

            disable_response = client.post("/api/game-mode-beta/enabled", json={"enabled": False})
            assert disable_response.status_code == 200
            disabled_state = disable_response.json()["state"]
            assert disabled_state["enabled"] is False
            assert disabled_state["pressure_state"] == "normal"
            assert disabled_state["trigger_reason"] is None
            assert disabled_state["suppressed_until"] is None
            assert disabled_state["manual_override"] is False
        finally:
            client.post("/api/game-mode-beta/enabled", json={"enabled": False})


def test_game_mode_beta_http_manual_restore_without_auto_switch_does_not_start_cooldown():
    with _client() as client:
        try:
            client.post("/api/game-mode-beta/enabled", json={"enabled": False})
            enable_response = client.post("/api/game-mode-beta/enabled", json={"enabled": True})
            assert enable_response.status_code == 200

            restore_response = client.post("/api/game-mode-beta/manual-restore")
            assert restore_response.status_code == 200
            restore_state = restore_response.json()["state"]
            assert restore_state["enabled"] is True
            assert restore_state["manual_override"] is False
            assert restore_state["auto_switch_active"] is False
            assert restore_state["suppressed_until"] is None
        finally:
            client.post("/api/game-mode-beta/enabled", json={"enabled": False})


def test_game_mode_beta_http_debug_trigger_stays_gated_for_normal_runtime(monkeypatch):
    monkeypatch.delenv("NEKO_GAME_MODE_DEBUG", raising=False)
    monkeypatch.delenv("NEKO_DEBUG", raising=False)

    with _client() as client:
        try:
            client.post("/api/game-mode-beta/enabled", json={"enabled": False})
            response = client.post("/api/game-mode-beta/debug/trigger", json={"reason": "debug", "percent": 97})

            assert response.status_code == 404
            assert response.json()["detail"] == "debug trigger unavailable"
            state = client.get("/api/game-mode-beta/state").json()["state"]
            assert state["enabled"] is False
            assert state["trigger_reason"] is None
        finally:
            client.post("/api/game-mode-beta/enabled", json={"enabled": False})


def test_game_mode_beta_http_enabled_flag_coerces_common_text_values():
    with _client() as client:
        try:
            on_response = client.post("/api/game-mode-beta/enabled", json={"enabled": "on"})
            assert on_response.status_code == 200
            assert on_response.json()["state"]["enabled"] is True

            false_response = client.post("/api/game-mode-beta/enabled", json={"enabled": "false"})
            assert false_response.status_code == 200
            assert false_response.json()["state"]["enabled"] is False

            unknown_response = client.post("/api/game-mode-beta/enabled", json={"enabled": "definitely"})
            assert unknown_response.status_code == 200
            assert unknown_response.json()["state"]["enabled"] is False
        finally:
            client.post("/api/game-mode-beta/enabled", json={"enabled": False})


def test_game_mode_beta_router_is_registered_on_main_app():
    source = Path("app/main_server.py").read_text(encoding="utf-8")

    assert "from main_routers.game_mode_router import router as game_mode_router" in source
    assert "app.include_router(game_mode_router)" in source


@pytest.mark.asyncio
async def test_game_mode_broadcast_failures_are_logged_and_isolated(monkeypatch, caplog):
    def unavailable_session_manager():
        raise RuntimeError("session manager unavailable")

    monkeypatch.setattr(game_mode_router_module, "get_session_manager", unavailable_session_manager)
    with caplog.at_level(logging.WARNING, logger=game_mode_router_module.__name__):
        assert await game_mode_router_module.broadcast_game_mode_event({"type": "test"}) == 0
    assert "session manager unavailable" in caplog.text

    class FailingWebSocket:
        client_state = "CONNECTED"

        async def send_json(self, _payload):
            raise RuntimeError("socket closed")

    class Session:
        websocket = FailingWebSocket()

    monkeypatch.setattr(game_mode_router_module, "get_session_manager", lambda: {"pet-a": Session()})
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger=game_mode_router_module.__name__):
        assert await game_mode_router_module.broadcast_game_mode_event({"type": "test"}) == 1
        for _ in range(3):
            await asyncio.sleep(0)
    assert "broadcast failed for session 'pet-a'" in caplog.text


@pytest.mark.asyncio
async def test_game_mode_broadcast_schedules_slow_sockets_without_waiting(monkeypatch):
    release = asyncio.Event()

    class SlowWebSocket:
        client_state = "CONNECTED"

        async def send_json(self, _payload):
            await release.wait()

    class Session:
        websocket = SlowWebSocket()

    monkeypatch.setattr(game_mode_router_module, "get_session_manager", lambda: {"pet-a": Session()})
    delivered = await asyncio.wait_for(
        game_mode_router_module.broadcast_game_mode_event({"type": "test"}),
        timeout=0.1,
    )
    assert delivered == 1
    release.set()
    for _ in range(3):
        await asyncio.sleep(0)


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/api/game-mode-beta/enabled", {"enabled": True}),
        ("/api/game-mode-beta/manual-restore", {}),
        ("/api/game-mode-beta/settings", {"auto_cat_on_game": True}),
        ("/api/game-mode-beta/windows/register", {"pet_instance_id": "pet-a"}),
        ("/api/game-mode-beta/windows/unregister", {"pet_instance_id": "pet-a"}),
        ("/api/game-mode-beta/ack", {"cycle_id": "cycle", "pet_instance_id": "pet-a"}),
        ("/api/game-mode-beta/deep-sleep-ack", {"cycle_id": "cycle", "pet_instance_id": "pet-a"}),
        ("/api/game-mode-beta/reset-candidate", {}),
        ("/api/game-mode-beta/debug/trigger", {}),
    ],
)
def test_game_mode_mutations_require_local_csrf(monkeypatch, path, payload):
    monkeypatch.setenv("NEKO_GAME_MODE_DEBUG", "1")
    with _client(secure=False) as client:
        response = client.post(path, json=payload)
    assert response.status_code == 403
    assert response.json()["error_code"] == "csrf_validation_failed"


def test_game_mode_beta_settings_endpoint_has_independent_exact_contract():
    with _client() as client:
        original = client.get("/api/game-mode-beta/settings").json()
        try:
            assert set(original) == {"auto_cat_on_game", "game_trigger_mode"}

            response = client.post(
                "/api/game-mode-beta/settings",
                json={"auto_cat_on_game": True, "game_trigger_mode": "instant"},
            )
            assert response.status_code == 200
            assert response.json() == {
                "auto_cat_on_game": True,
                "game_trigger_mode": "instant",
            }
            assert client.get("/api/game-mode-beta/settings").json() == response.json()
        finally:
            client.post("/api/game-mode-beta/settings", json=original)


def test_game_mode_beta_settings_endpoint_rejects_invalid_mode_and_non_boolean_toggle():
    with _client() as client:
        response = client.post(
            "/api/game-mode-beta/settings",
            json={"auto_cat_on_game": "yes", "game_trigger_mode": "guess"},
        )
        assert response.status_code == 400


def test_game_mode_beta_window_registration_and_ack_contract():
    with _client() as client:
        try:
            client.post("/api/game-mode-beta/enabled", json={"enabled": True})
            registration = client.post(
                "/api/game-mode-beta/windows/register",
                json={
                    "pet_instance_id": "pet-contract",
                    "window_type": "pet",
                    "signal_capabilities": {"exact_game": True},
                },
            )
            assert registration.status_code == 200
            assert registration.json()["join_as_cat"] is False

            stale_ack = client.post(
                "/api/game-mode-beta/ack",
                json={
                    "cycle_id": "stale",
                    "pet_instance_id": "pet-contract",
                    "status": "protected",
                },
            )
            assert stale_ack.status_code == 200
            assert stale_ack.json()["state"]["cycle_phase"] == "idle"
        finally:
            client.post(
                "/api/game-mode-beta/windows/unregister",
                json={"pet_instance_id": "pet-contract"},
            )
            client.post("/api/game-mode-beta/enabled", json={"enabled": False})
