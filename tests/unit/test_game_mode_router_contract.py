import asyncio
import logging
from pathlib import Path
from unittest.mock import AsyncMock

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


def test_game_mode_beta_http_state_enable_and_disable_flow():
    with _client() as client:
        try:
            client.post("/api/game-mode-beta/enabled", json={"enabled": False})

            state_response = client.get("/api/game-mode-beta/state")
            assert state_response.status_code == 200
            state = state_response.json()["state"]
            assert state["enabled"] is False
            assert state["pressure_state"] == "normal"

            enable_response = client.post("/api/game-mode-beta/enabled", json={"enabled": True})
            assert enable_response.status_code == 200
            assert enable_response.json()["state"]["enabled"] is True

            disable_response = client.post("/api/game-mode-beta/enabled", json={"enabled": False})
            assert disable_response.status_code == 200
            disabled_state = disable_response.json()["state"]
            assert disabled_state["enabled"] is False
            assert disabled_state["resource_session_phase"] == "idle"
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
    source = Path("app/main_server/web_app.py").read_text(encoding="utf-8")

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
@pytest.mark.parametrize("client_state", ["DISCONNECTED", "WebSocketState.DISCONNECTED"])
async def test_game_mode_broadcast_rejects_disconnected_sockets(monkeypatch, client_state):
    class DisconnectedWebSocket:
        def __init__(self):
            self.client_state = client_state

        async def send_json(self, _payload):
            raise AssertionError("disconnected websocket must not be scheduled")

    class Session:
        websocket = DisconnectedWebSocket()

    monkeypatch.setattr(game_mode_router_module, "get_session_manager", lambda: {"pet-a": Session()})
    assert await game_mode_router_module.broadcast_game_mode_event({"type": "test"}) == 0
    assert not game_mode_router_module._game_mode_broadcast_tasks


@pytest.mark.asyncio
async def test_game_mode_broadcast_counts_only_capable_sessions(monkeypatch):
    class RecordingWebSocket:
        client_state = "CONNECTED"

        def __init__(self):
            self.payloads = []

        async def send_json(self, payload):
            self.payloads.append(payload)

    class Session:
        def __init__(self, *, game_mode_capable):
            self.websocket = RecordingWebSocket()
            self.game_mode_capable = game_mode_capable

    pet_session = Session(game_mode_capable=True)
    chat_session = Session(game_mode_capable=False)
    monkeypatch.setattr(
        game_mode_router_module,
        "get_session_manager",
        lambda: {"pet": pet_session, "chat": chat_session},
    )

    delivered = await game_mode_router_module.broadcast_game_mode_event({"type": "test"})
    for _ in range(3):
        await asyncio.sleep(0)

    assert delivered == 1
    assert pet_session.websocket.payloads == [{"type": "test"}]
    assert chat_session.websocket.payloads == []


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
    assert len(game_mode_router_module._game_mode_broadcast_tasks) == 1
    release.set()
    for _ in range(3):
        await asyncio.sleep(0)
    assert not game_mode_router_module._game_mode_broadcast_tasks


@pytest.mark.asyncio
async def test_game_mode_broadcast_serializes_sends_with_session_lock(monkeypatch):
    active_sends = 0
    max_active_sends = 0

    class LockedWebSocket:
        client_state = "CONNECTED"

        async def send_json(self, _payload):
            nonlocal active_sends, max_active_sends
            active_sends += 1
            max_active_sends = max(max_active_sends, active_sends)
            await asyncio.sleep(0.01)
            active_sends -= 1

    class Session:
        websocket = LockedWebSocket()
        websocket_lock = asyncio.Lock()

    monkeypatch.setattr(game_mode_router_module, "get_session_manager", lambda: {"pet-a": Session()})
    assert await game_mode_router_module.broadcast_game_mode_event({"type": "first"}) == 1
    assert await game_mode_router_module.broadcast_game_mode_event({"type": "second"}) == 1

    async def wait_for_broadcasts():
        while game_mode_router_module._game_mode_broadcast_tasks:
            await asyncio.sleep(0.01)

    await asyncio.wait_for(wait_for_broadcasts(), timeout=2.0)

    assert max_active_sends == 1


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/api/game-mode-beta/enabled", {"enabled": True}),
        ("/api/game-mode-beta/settings", {"resource_protection_on_game": True}),
        ("/api/game-mode-beta/windows/register", {"pet_instance_id": "pet-a"}),
        ("/api/game-mode-beta/windows/unregister", {"pet_instance_id": "pet-a"}),
        ("/api/game-mode-beta/resource/ack", {"resource_session_id": "session", "pet_instance_id": "pet-a", "phase": "soft_protected"}),
        ("/api/game-mode-beta/resource/interaction", {"resource_session_id": "session", "pet_instance_id": "pet-a", "interaction": "click"}),
        ("/api/game-mode-beta/resource/exit", {"resource_session_id": "session"}),
    ],
)
def test_game_mode_mutations_require_local_csrf(path, payload):
    with _client(secure=False) as client:
        response = client.post(path, json=payload)
    assert response.status_code == 403
    assert response.json()["error_code"] == "csrf_validation_failed"

def test_game_mode_beta_settings_endpoint_has_resource_only_contract():
    legacy_fields = {"auto" + "_cat_on_game", "game" + "_trigger_mode"}
    with _client() as client:
        original = client.get("/api/game-mode-beta/settings").json()
        try:
            assert set(original) == {
                "resource_protection_on_game",
                "compact_pet_window_enabled",
            }
            assert set(original).isdisjoint(legacy_fields)

            response = client.post(
                "/api/game-mode-beta/settings",
                json={
                    "resource_protection_on_game": False,
                    "compact_pet_window_enabled": False,
                },
            )
            assert response.status_code == 200
            assert response.json() == {
                "resource_protection_on_game": False,
                "compact_pet_window_enabled": False,
            }
            assert client.get("/api/game-mode-beta/settings").json() == response.json()
        finally:
            client.post("/api/game-mode-beta/settings", json=original)

@pytest.mark.parametrize(
    "payload",
    [
        {"resource_protection_on_game": "yes"},
        {"compact_pet_window_enabled": 1},
    ],
)
def test_game_mode_beta_settings_endpoint_rejects_non_boolean_toggles(payload):
    with _client() as client:
        response = client.post("/api/game-mode-beta/settings", json=payload)
    assert response.status_code == 400

def test_game_mode_beta_window_registration_has_resource_only_contract():
    legacy_keys = {"join_" + "as_cat", "cycle_" + "id", "cycle_" + "phase"}
    with _client() as client:
        try:
            client.post("/api/game-mode-beta/enabled", json={"enabled": True})
            registration = client.post(
                "/api/game-mode-beta/windows/register",
                json={
                    "pet_instance_id": "pet-contract",
                    "window_type": "pet",
                    "signal_capabilities": {"exact_game": True},
                    "host_capabilities": {"compactPetWindowLeaseV1": True},
                },
            )
            assert registration.status_code == 200
            payload = registration.json()
            assert payload["resource_session_active"] is False
            assert set(payload).isdisjoint(legacy_keys)
        finally:
            client.post(
                "/api/game-mode-beta/windows/unregister",
                json={"pet_instance_id": "pet-contract"},
            )
            client.post("/api/game-mode-beta/enabled", json={"enabled": False})


@pytest.mark.parametrize(
    "path",
    [
        "/api/game-mode-beta/manual" + "-restore",
        "/api/game-mode-beta/" + "ack",
        "/api/game-mode-beta/deep-sleep" + "-ack",
        "/api/game-mode-beta/reset" + "-candidate",
        "/api/game-mode-beta/debug/" + "trigger",
    ],
)
def test_removed_model_switching_routes_return_not_found(path):
    with _client() as client:
        response = client.post(path, json={})
    assert response.status_code == 404

def test_game_mode_beta_resource_session_endpoints_validate_and_forward(monkeypatch):
    ack = AsyncMock(return_value={"resource_session_phase": "soft_protected"})
    interaction = AsyncMock(return_value={"resource_session_phase": "soft_protected"})
    exit_session = AsyncMock(return_value={"resource_session_phase": "idle"})
    monkeypatch.setattr(game_mode_router_module.protector, "acknowledge_resource_phase", ack)
    monkeypatch.setattr(game_mode_router_module.protector, "record_resource_interaction", interaction)
    monkeypatch.setattr(game_mode_router_module.protector, "exit_resource_session", exit_session)

    with _client() as client:
        ack_response = client.post(
            "/api/game-mode-beta/resource/ack",
            json={
                "resource_session_id": "session-1",
                "pet_instance_id": "pet-1",
                "phase": "deep_sleep",
                "compact_lease": "acquired",
            },
        )
        interaction_response = client.post(
            "/api/game-mode-beta/resource/interaction",
            json={
                "resource_session_id": "session-1",
                "pet_instance_id": "pet-1",
                "interaction": "click",
            },
        )
        exit_response = client.post(
            "/api/game-mode-beta/resource/exit",
            json={"resource_session_id": "session-1", "reason": "user-exit"},
        )

    assert ack_response.status_code == 200
    assert interaction_response.status_code == 200
    assert exit_response.status_code == 200
    ack.assert_awaited_once_with(
        resource_session_id="session-1",
        pet_instance_id="pet-1",
        phase="deep_sleep",
        compact_lease="acquired",
        error=None,
    )
    interaction.assert_awaited_once_with(
        resource_session_id="session-1",
        pet_instance_id="pet-1",
        interaction="click",
    )
    exit_session.assert_awaited_once_with(
        resource_session_id="session-1",
        reason="user-exit",
    )


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/api/game-mode-beta/resource/ack", {"resource_session_id": "bad space", "pet_instance_id": "pet", "phase": "deep_sleep"}),
        ("/api/game-mode-beta/resource/ack", {"resource_session_id": "session", "pet_instance_id": "pet", "phase": "unknown"}),
        ("/api/game-mode-beta/resource/interaction", {"resource_session_id": "session", "pet_instance_id": "pet", "interaction": "mousemove"}),
        ("/api/game-mode-beta/resource/exit", {"resource_session_id": "x" * 129}),
    ],
)
def test_game_mode_beta_resource_session_endpoints_reject_invalid_payloads(path, payload):
    with _client() as client:
        response = client.post(path, json=payload)
    assert response.status_code == 400
