import asyncio
import logging
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import main_routers.widget_mode_router as widget_mode_router_module
from main_routers.system_router import _shared as system_router_shared
from main_routers.widget_mode_router import router
from main_logic.widget_mode_runtime import WidgetModeCoordinator, WidgetModeSettingsStore


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


def _register_payload(version: int = 1) -> dict:
    return {
        "pet_instance_id": "pet-contract",
        "window_type": "pet",
        "signal_capabilities": {},
        "widget_mode_protocol_version": version,
        "widget_mode_compaction_lease_v1": True,
    }


def test_widget_mode_state_and_enable_contract(monkeypatch, tmp_path: Path) -> None:
    coordinator = WidgetModeCoordinator(
        store=WidgetModeSettingsStore(tmp_path / "widget_mode_settings.json"),
    )
    monkeypatch.setattr(widget_mode_router_module, "widget_mode_coordinator", coordinator)
    with _client() as client:
        state = client.get("/api/widget-mode/state").json()["state"]
        assert state["enabled"] is False
        assert state["compaction_phase"] == "idle"
        assert "settings" not in state

        enabled = client.post("/api/widget-mode/enabled", json={"enabled": "on"})
        assert enabled.json()["state"]["enabled"] is True
        client.post("/api/widget-mode/enabled", json={"enabled": False})


def test_widget_mode_window_protocol_and_stale_ack_contract() -> None:
    with _client() as client:
        try:
            compatible = client.post("/api/widget-mode/windows/register", json=_register_payload())
            assert compatible.status_code == 200
            assert compatible.json()["widget_mode_capable"] is True
            assert compatible.json()["protocol_compatible"] is True

            mismatch = client.post(
                "/api/widget-mode/windows/register",
                json={**_register_payload(version=2), "pet_instance_id": "pet-old"},
            )
            assert mismatch.json()["widget_mode_capable"] is False
            assert mismatch.json()["protocol_compatible"] is False

            stale = client.post(
                "/api/widget-mode/compaction/ack",
                json={
                    "compaction_cycle_id": "stale",
                    "pet_instance_id": "pet-contract",
                    "status": "restored",
                },
            )
            assert stale.json()["state"]["compaction_phase"] == "idle"
        finally:
            for pet_id in ("pet-contract", "pet-old"):
                client.post("/api/widget-mode/windows/unregister", json={"pet_instance_id": pet_id})


def test_widget_mode_debug_endpoint_is_gated(monkeypatch) -> None:
    monkeypatch.delenv("NEKO_WIDGET_MODE_DEBUG", raising=False)
    monkeypatch.delenv("NEKO_DEBUG", raising=False)
    with _client() as client:
        response = client.post("/api/widget-mode/debug/compaction", json={})
    assert response.status_code == 404


def test_widget_mode_debug_endpoint_triggers_compaction(monkeypatch) -> None:
    async def delivered(_payload: dict) -> int:
        return 1

    coordinator = WidgetModeCoordinator(broadcaster=delivered)
    monkeypatch.setattr(widget_mode_router_module, "widget_mode_coordinator", coordinator)
    monkeypatch.setenv("NEKO_WIDGET_MODE_DEBUG", "1")

    with _client() as client:
        client.post("/api/widget-mode/windows/register", json=_register_payload())
        response = client.post(
            "/api/widget-mode/debug/compaction",
            json={"reason": "contract"},
        )
        client.post("/api/widget-mode/enabled", json={"enabled": False})
        client.post(
            "/api/widget-mode/windows/unregister",
            json={"pet_instance_id": "pet-contract"},
        )

    assert response.status_code == 200
    assert response.json()["state"]["compaction_phase"] == "compacting"
    assert response.json()["state"]["compaction_source"] == "widget_mode_compaction"


def test_widget_mode_router_is_registered_on_main_app() -> None:
    source = Path("app/main_server/web_app.py").read_text(encoding="utf-8")
    assert "from main_routers.widget_mode_router import router as widget_mode_router" in source
    assert "app.include_router(widget_mode_router)" in source


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/api/widget-mode/enabled", {"enabled": True}),
        ("/api/widget-mode/user-restore", {}),
        ("/api/widget-mode/windows/register", _register_payload()),
        ("/api/widget-mode/windows/unregister", {"pet_instance_id": "pet-a"}),
        ("/api/widget-mode/compaction/ack", {"compaction_cycle_id": "x"}),
        ("/api/widget-mode/renderer-suspension/ack", {"compaction_cycle_id": "x"}),
        ("/api/widget-mode/debug/compaction", {}),
    ],
)
def test_widget_mode_mutations_require_local_csrf(monkeypatch, path, payload) -> None:
    monkeypatch.setenv("NEKO_WIDGET_MODE_DEBUG", "1")
    with _client(secure=False) as client:
        response = client.post(path, json=payload)
    assert response.status_code == 403
    assert response.json()["error_code"] == "csrf_validation_failed"


@pytest.mark.asyncio
async def test_broadcast_counts_only_explicitly_capable_sessions(monkeypatch) -> None:
    class RecordingWebSocket:
        client_state = "CONNECTED"

        def __init__(self) -> None:
            self.payloads = []

        async def send_json(self, payload):
            self.payloads.append(payload)

    class Session:
        def __init__(self, capable: bool) -> None:
            self.websocket = RecordingWebSocket()
            self.widget_mode_capable = capable

    pet = Session(True)
    chat = Session(False)
    missing_capability = SimpleSession = type(
        "SimpleSession",
        (),
        {"websocket": RecordingWebSocket()},
    )()
    monkeypatch.setattr(
        widget_mode_router_module,
        "get_session_manager",
        lambda: {"pet": pet, "chat": chat, "missing": missing_capability},
    )
    payload = {"type": "widget_mode_compaction_requested", "compaction_cycle_id": "cycle"}
    delivered = await widget_mode_router_module.broadcast_widget_mode_event(payload)
    for _ in range(3):
        await asyncio.sleep(0)

    assert delivered == 1
    assert pet.websocket.payloads == [payload]
    assert chat.websocket.payloads == []
    assert missing_capability.websocket.payloads == []


@pytest.mark.asyncio
async def test_broadcast_failure_is_logged_and_isolated(monkeypatch, caplog) -> None:
    def unavailable_session_manager():
        raise RuntimeError("session manager unavailable")

    monkeypatch.setattr(
        widget_mode_router_module,
        "get_session_manager",
        unavailable_session_manager,
    )
    with caplog.at_level(logging.WARNING, logger=widget_mode_router_module.__name__):
        delivered = await widget_mode_router_module.broadcast_widget_mode_event({"type": "test"})
    assert delivered == 0
    assert "session manager unavailable" in caplog.text


@pytest.mark.asyncio
async def test_broadcast_uses_session_lock(monkeypatch) -> None:
    active = 0
    max_active = 0

    class LockedWebSocket:
        client_state = "CONNECTED"

        async def send_json(self, _payload):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.01)
            active -= 1

    class Session:
        widget_mode_capable = True
        websocket = LockedWebSocket()
        websocket_lock = asyncio.Lock()

    monkeypatch.setattr(
        widget_mode_router_module,
        "get_session_manager",
        lambda: {"pet": Session()},
    )
    assert await widget_mode_router_module.broadcast_widget_mode_event({"type": "first"}) == 1
    assert await widget_mode_router_module.broadcast_widget_mode_event({"type": "second"}) == 1
    while widget_mode_router_module._widget_mode_broadcast_tasks:
        await asyncio.sleep(0.01)
    assert max_active == 1
