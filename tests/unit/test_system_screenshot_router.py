from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from main_routers import system_router as system_router_module
from main_routers.shared_state import init_shared_state


INTERACTIVE_SCREENSHOT_ENDPOINT = "/api/screenshot/interactive"


@pytest.fixture(autouse=True)
def _reset_shared_state_after_test():
    yield
    init_shared_state(
        role_state={},
        steamworks=None,
        templates=None,
        config_manager=None,
        logger=None,
    )


def _build_client():
    init_shared_state(
        role_state={},
        steamworks=None,
        templates=None,
        config_manager=None,
        logger=None,
    )
    app = FastAPI()
    app.include_router(system_router_module.router)
    return TestClient(app)


@pytest.mark.unit
def test_interactive_screenshot_rejects_non_loopback_requests():
    with _build_client() as client, patch.object(
        system_router_module,
        "_is_loopback_request",
        return_value=False,
    ):
        response = client.post(INTERACTIVE_SCREENSHOT_ENDPOINT)

    assert response.status_code == 403
    assert response.json()["error"] == "only available from localhost"


@pytest.mark.unit
def test_interactive_screenshot_returns_unsupported_on_non_macos(monkeypatch):
    monkeypatch.setattr(system_router_module, "_is_loopback_request", lambda _request: True)
    monkeypatch.setattr(system_router_module.sys, "platform", "linux")

    with _build_client() as client:
        response = client.post(INTERACTIVE_SCREENSHOT_ENDPOINT)

    assert response.status_code == 501
    assert "only supported on macOS" in response.json()["error"]


@pytest.mark.unit
def test_interactive_screenshot_returns_canceled_when_user_aborts(monkeypatch):
    monkeypatch.setattr(system_router_module, "_is_loopback_request", lambda _request: True)
    monkeypatch.setattr(system_router_module.sys, "platform", "darwin")
    monkeypatch.setattr(
        system_router_module,
        "_run_macos_interactive_screenshot",
        lambda _path: (1, ""),
    )

    with _build_client() as client:
        response = client.post(INTERACTIVE_SCREENSHOT_ENDPOINT)

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is False
    assert payload["canceled"] is True


@pytest.mark.unit
def test_interactive_screenshot_returns_cropped_image_data(monkeypatch):
    monkeypatch.setattr(system_router_module, "_is_loopback_request", lambda _request: True)
    monkeypatch.setattr(system_router_module.sys, "platform", "darwin")

    def _fake_run(output_path: str):
        Image.new("RGB", (64, 48), (32, 128, 224)).save(output_path, format="PNG")
        return 0, ""

    monkeypatch.setattr(
        system_router_module,
        "_run_macos_interactive_screenshot",
        _fake_run,
    )

    with _build_client() as client:
        response = client.post(INTERACTIVE_SCREENSHOT_ENDPOINT)

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["interactive"] is True
    assert payload["size"] > 0
    assert payload["data"].startswith("data:image/jpeg;base64,")
