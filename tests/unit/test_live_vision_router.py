"""The probe endpoint plugins use to ask "is the user sharing their screen?".

The answer can carry a picture of somebody's desktop, so two things are load
bearing here: the frame is opt-in, and the route is not reachable from off the
machine.
"""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from main_routers.system_router import live_vision as live_vision_module

ENDPOINT = "/api/system/live-vision"
PERMISSION_ENDPOINT = "/api/system/live-vision/attachment-permission"

pytestmark = pytest.mark.unit


class _Manager:
    def __init__(self, name, snapshot, frame="shared-frame"):
        self.lanlan_name = name
        self._snapshot = snapshot
        self._frame = frame

    def live_vision_snapshot(self):
        return dict(self._snapshot)

    def live_vision_frame_b64(self):
        return self._frame


def _sharing(**over):
    return {
        "active": True,
        "source": "screen",
        "age_seconds": 0.5,
        "native_vision": True,
        **over,
    }


def _idle():
    return {
        "active": False,
        "source": "",
        "age_seconds": None,
        "native_vision": False,
    }


def _client(managers, monkeypatch, *, host="127.0.0.1"):
    monkeypatch.setattr(
        live_vision_module, "get_session_manager", lambda: dict(managers))
    app = FastAPI()
    app.include_router(live_vision_module.router)
    return TestClient(app, client=(host, 51234))


def test_a_sharing_session_is_reported_without_the_pixels(monkeypatch):
    client = _client({"lanlan": _Manager("lanlan", _sharing())}, monkeypatch)

    body = client.get(ENDPOINT).json()

    assert body["ok"] is True
    assert body["active"] is True
    assert body["source"] == "screen"
    assert body["native_vision"] is True
    assert body["role"] == "lanlan"
    # The expensive, sensitive half stays behind an explicit ask.
    assert "frame_b64" not in body


def test_the_frame_comes_only_when_asked_for(monkeypatch):
    client = _client({"lanlan": _Manager("lanlan", _sharing())}, monkeypatch)

    body = client.get(ENDPOINT, params={"include_frame": "true"}).json()

    assert body["frame_b64"] == "shared-frame"
    assert body["frame_mime"] == "image/jpeg"


def test_camera_share_never_returns_pixels(monkeypatch):
    """Camera is the user's room; screen-share probes must not leak it."""
    client = _client(
        {"lanlan": _Manager("lanlan", _sharing(source="camera"), frame="room")},
        monkeypatch,
    )

    body = client.get(ENDPOINT, params={"include_frame": "true"}).json()

    assert body["active"] is True
    assert body["source"] == "camera"
    assert "frame_b64" not in body


def test_no_frame_is_handed_out_when_nothing_is_shared(monkeypatch):
    client = _client({"lanlan": _Manager("lanlan", _idle())}, monkeypatch)

    body = client.get(ENDPOINT, params={"include_frame": "true"}).json()

    assert body["active"] is False
    assert "frame_b64" not in body


def test_a_roleless_query_finds_whoever_is_sharing(monkeypatch):
    """Plugins know which game is running, not which character is listening."""
    client = _client(
        {
            "quiet": _Manager("quiet", _idle()),
            "sharing": _Manager("sharing", _sharing()),
        },
        monkeypatch,
    )

    body = client.get(ENDPOINT).json()

    assert body["role"] == "sharing"
    assert body["active"] is True


def test_a_roleless_query_prefers_screen_over_an_earlier_camera(monkeypatch):
    client = _client(
        {
            "camera": _Manager("camera", _sharing(source="camera")),
            "screen": _Manager("screen", _sharing(source="screen")),
        },
        monkeypatch,
    )

    body = client.get(ENDPOINT).json()

    assert body["role"] == "screen"
    assert body["source"] == "screen"


def test_naming_a_role_pins_the_answer_to_it(monkeypatch):
    client = _client(
        {
            "quiet": _Manager("quiet", _idle()),
            "sharing": _Manager("sharing", _sharing()),
        },
        monkeypatch,
    )

    body = client.get(ENDPOINT, params={"role": "quiet"}).json()

    assert body["role"] == "quiet"
    assert body["active"] is False


def test_an_unknown_role_is_simply_not_sharing(monkeypatch):
    client = _client({"lanlan": _Manager("lanlan", _sharing())}, monkeypatch)

    body = client.get(ENDPOINT, params={"role": "nobody"}).json()

    assert body == {
        "ok": True,
        "role": "",
        "active": False,
        "source": "",
        "age_seconds": None,
        "native_vision": False,
    }


def test_an_uninitialized_host_answers_instead_of_erroring(monkeypatch):
    """Early startup and bare test imports must not 500 a polling probe."""

    def boom():
        raise RuntimeError("shared state not initialized")

    monkeypatch.setattr(live_vision_module, "get_session_manager", boom)
    app = FastAPI()
    app.include_router(live_vision_module.router)
    body = TestClient(app, client=("127.0.0.1", 51234)).get(ENDPOINT).json()

    assert body == {
        "ok": True,
        "role": "",
        "active": False,
        "source": "",
        "age_seconds": None,
        "native_vision": False,
    }


def test_the_route_is_not_reachable_from_another_machine(monkeypatch):
    client = _client(
        {"lanlan": _Manager("lanlan", _sharing())}, monkeypatch, host="10.0.0.7")

    assert client.get(ENDPOINT).status_code == 403


@pytest.fixture(autouse=True)
def _isolated_live_frame_permissions():
    try:
        from main_logic.core.live_frame_permissions import (
            clear_live_frame_permissions,
            clear_plugin_delivery_permissions,
        )
    except ImportError:
        yield
        return
    clear_live_frame_permissions()
    clear_plugin_delivery_permissions()
    yield
    clear_live_frame_permissions()
    clear_plugin_delivery_permissions()


def test_a_local_permission_update_is_installed_before_it_is_acknowledged(
    monkeypatch,
):
    client = _client({}, monkeypatch)

    response = client.post(
        PERMISSION_ENDPOINT,
        json={
            "source_name": "neko_wows",
            "token": "generation-two",
            "enabled": False,
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "source_name": "neko_wows",
        "token": "generation-two",
        "enabled": False,
    }
    from main_logic.core.live_frame_permissions import allows_live_frame

    assert allows_live_frame("neko_wows", "generation-two") is False


def test_authorizing_a_generation_allows_that_token_only(monkeypatch):
    from main_logic.core.live_frame_permissions import allows_live_frame

    client = _client({}, monkeypatch)
    response = client.post(
        PERMISSION_ENDPOINT,
        json={
            "source_name": "neko_wows",
            "token": "generation-one",
            "enabled": True,
        },
    )

    assert response.status_code == 200
    assert allows_live_frame("neko_wows", "generation-one") is True
    assert allows_live_frame("neko_wows", "generation-two") is False


def test_replacing_the_generation_revokes_the_previous_token(monkeypatch):
    from main_logic.core.live_frame_permissions import allows_live_frame

    client = _client({}, monkeypatch)
    client.post(
        PERMISSION_ENDPOINT,
        json={
            "source_name": "neko_wows",
            "token": "generation-one",
            "enabled": True,
        },
    )
    client.post(
        PERMISSION_ENDPOINT,
        json={
            "source_name": "neko_wows",
            "token": "generation-two",
            "enabled": False,
        },
    )

    assert allows_live_frame("neko_wows", "generation-one") is False
    assert allows_live_frame("neko_wows", "generation-two") is False


def test_the_permission_route_is_not_reachable_from_another_machine(monkeypatch):
    client = _client({}, monkeypatch, host="10.0.0.7")

    assert client.post(
        PERMISSION_ENDPOINT,
        json={
            "source_name": "neko_wows",
            "token": "generation-two",
            "enabled": False,
        },
    ).status_code == 403


DELIVERY_ENDPOINT = "/api/system/plugin-callbacks/delivery-permission"


def test_disabling_delivery_permission_replaces_the_generation_and_retracts(
    monkeypatch,
):
    from main_logic.core.live_frame_permissions import allows_plugin_delivery

    retracted = []

    class _Session:
        def retract_callbacks_from_source(self, source_name):
            retracted.append(source_name)

    client = _client({"lanlan": _Session()}, monkeypatch)
    client.post(
        DELIVERY_ENDPOINT,
        json={
            "source_name": "neko_wows",
            "token": "generation-one",
            "enabled": True,
        },
    )
    response = client.post(
        DELIVERY_ENDPOINT,
        json={
            "source_name": "neko_wows",
            "token": "generation-two",
            "enabled": False,
        },
    )

    assert response.status_code == 200
    assert response.json()["enabled"] is False
    assert allows_plugin_delivery("neko_wows", "generation-one") is False
    assert allows_plugin_delivery("neko_wows", "generation-two") is False
    assert retracted == ["neko_wows"]


def test_an_empty_delivery_token_does_not_retract(monkeypatch):
    from main_logic.core.live_frame_permissions import allows_plugin_delivery

    retracted = []

    class _Session:
        def retract_callbacks_from_source(self, source_name):
            retracted.append(source_name)

    client = _client({"lanlan": _Session()}, monkeypatch)
    client.post(
        DELIVERY_ENDPOINT,
        json={
            "source_name": "neko_wows",
            "token": "generation-one",
            "enabled": True,
        },
    )
    response = client.post(
        DELIVERY_ENDPOINT,
        json={
            "source_name": "neko_wows",
            "token": "",
            "enabled": False,
        },
    )

    assert response.status_code == 200
    assert allows_plugin_delivery("neko_wows", "generation-one") is True
    assert retracted == []
