from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import main_routers.widget_mode_router as widget_mode_router_module
from main_logic.widget_mode_runtime import WidgetModeCoordinator
from main_routers.system_router import _shared as system_router_shared
from main_routers.widget_mode_router import router


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


def test_widget_mode_state_and_enable_contract(monkeypatch) -> None:
    coordinator = WidgetModeCoordinator()
    monkeypatch.setattr(widget_mode_router_module, "widget_mode_coordinator", coordinator)

    with _client() as client:
        assert client.get("/api/widget-mode/state").json() == {
            "success": True,
            "state": {"enabled": False},
        }
        assert client.post("/api/widget-mode/enabled", json={"enabled": "on"}).json() == {
            "success": True,
            "state": {"enabled": True},
        }
        assert client.post("/api/widget-mode/enabled", json={"enabled": 0}).json() == {
            "success": True,
            "state": {"enabled": False},
        }


def test_widget_mode_mutation_requires_local_csrf() -> None:
    with _client(secure=False) as client:
        response = client.post("/api/widget-mode/enabled", json={"enabled": True})

    assert response.status_code == 403
    assert response.json()["error_code"] == "csrf_validation_failed"


@pytest.mark.parametrize(
    "path",
    [
        "/api/widget-mode/user-restore",
        "/api/widget-mode/windows/register",
        "/api/widget-mode/windows/unregister",
        "/api/widget-mode/compaction/ack",
        "/api/widget-mode/renderer-suspension/ack",
        "/api/widget-mode/debug/compaction",
    ],
)
def test_removed_widget_mode_protocol_routes_return_404(path: str) -> None:
    with _client() as client:
        response = client.post(path, json={})

    assert response.status_code == 404


def test_widget_mode_router_is_registered_on_main_app() -> None:
    source = Path("app/main_server/web_app.py").read_text(encoding="utf-8")
    assert "from main_routers.widget_mode_router import router as widget_mode_router" in source
    assert "app.include_router(widget_mode_router)" in source
