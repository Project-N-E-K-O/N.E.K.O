"""The probe endpoint plugins use to ask "is the user sharing their screen?".

The answer can carry a picture of somebody's desktop, so two things are load
bearing here: the frame is opt-in, and the route is not reachable from off the
machine.
"""

from collections import UserDict
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from main_routers.system_router import live_vision as live_vision_module

ENDPOINT = "/api/system/live-vision"
PERMISSION_ENDPOINT = "/api/system/live-vision/attachment-permission"
HOST_TOKEN_ENV = "NEKO_PLUGIN_HOST_API_TOKEN"
HOST_TOKEN_HEADER = "X-NEKO-Plugin-Host-Token"
FRAME_TOKEN_HEADER = "X-NEKO-Live-Frame-Token"
HOST_TOKEN = "test-plugin-host-token"

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


def _client(managers, monkeypatch, *, host="127.0.0.1", authenticated=False):
    monkeypatch.setenv(HOST_TOKEN_ENV, HOST_TOKEN)
    monkeypatch.setattr(
        live_vision_module, "get_session_manager", lambda: dict(managers))
    app = FastAPI()
    app.include_router(live_vision_module.router)
    client = TestClient(app, client=(host, 51234))
    if authenticated:
        client.headers[HOST_TOKEN_HEADER] = HOST_TOKEN
    return client


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
    client = _client(
        {"lanlan": _Manager("lanlan", _sharing())},
        monkeypatch,
        authenticated=True,
    )
    client.post(
        PERMISSION_ENDPOINT,
        json={
            "source_name": "demo_plugin",
            "token": "generation-one",
            "enabled": True,
        },
    )

    body = client.get(
        ENDPOINT,
        headers={FRAME_TOKEN_HEADER: "generation-one"},
        params={
            "include_frame": "true",
            "source_name": "demo_plugin",
        },
    ).json()

    assert body["frame_b64"] == "shared-frame"
    assert body["frame_mime"] == "image/jpeg"


def test_the_frame_is_withheld_without_a_matching_plugin_generation(monkeypatch):
    client = _client({"lanlan": _Manager("lanlan", _sharing())}, monkeypatch)

    body = client.get(
        ENDPOINT,
        params={
            "include_frame": "true",
            "source_name": "demo_plugin",
            "token": "not-authorized",
        },
    ).json()

    assert body["active"] is True
    assert body["source"] == "screen"
    assert "frame_b64" not in body


def test_a_matching_query_token_is_not_accepted(monkeypatch):
    client = _client(
        {"lanlan": _Manager("lanlan", _sharing())},
        monkeypatch,
        authenticated=True,
    )
    client.post(
        PERMISSION_ENDPOINT,
        json={
            "source_name": "demo_plugin",
            "token": "generation-one",
            "enabled": True,
        },
    )

    body = client.get(
        ENDPOINT,
        params={
            "include_frame": "true",
            "source_name": "demo_plugin",
            "token": "generation-one",
        },
    ).json()

    assert body["active"] is True
    assert "frame_b64" not in body


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
    client = _client({}, monkeypatch, authenticated=True)

    response = client.post(
        PERMISSION_ENDPOINT,
        json={
            "source_name": "demo_plugin",
            "token": "generation-two",
            "enabled": False,
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "source_name": "demo_plugin",
        "token": "generation-two",
        "enabled": False,
        "applied": True,
    }
    from main_logic.core.live_frame_permissions import allows_live_frame

    assert allows_live_frame("demo_plugin", "generation-two") is False


def test_authorizing_a_generation_allows_that_token_only(monkeypatch):
    from main_logic.core.live_frame_permissions import allows_live_frame

    client = _client({}, monkeypatch, authenticated=True)
    response = client.post(
        PERMISSION_ENDPOINT,
        json={
            "source_name": "demo_plugin",
            "token": "generation-one",
            "enabled": True,
        },
    )

    assert response.status_code == 200
    assert allows_live_frame("demo_plugin", "generation-one") is True
    assert allows_live_frame("demo_plugin", "generation-two") is False


def test_local_plugin_cannot_replace_an_existing_grant_without_host_credential(
    monkeypatch,
):
    from main_logic.core.live_frame_permissions import (
        allows_live_frame,
        set_live_frame_permission,
    )

    set_live_frame_permission("victim_plugin", "victim-generation", enabled=True)
    client = _client({}, monkeypatch, authenticated=False)

    response = client.post(
        PERMISSION_ENDPOINT,
        json={
            "source_name": "victim_plugin",
            "token": "attacker-generation",
            "enabled": True,
        },
    )

    assert response.status_code == 403
    assert allows_live_frame("victim_plugin", "victim-generation") is True
    assert allows_live_frame("victim_plugin", "attacker-generation") is False


def test_incorrect_host_credential_cannot_install_a_grant(monkeypatch):
    from main_logic.core.live_frame_permissions import allows_live_frame

    client = _client({}, monkeypatch, authenticated=False)

    response = client.post(
        PERMISSION_ENDPOINT,
        headers={HOST_TOKEN_HEADER: "wrong-token"},
        json={
            "source_name": "victim_plugin",
            "token": "attacker-generation",
            "enabled": True,
        },
    )

    assert response.status_code == 403
    assert allows_live_frame("victim_plugin", "attacker-generation") is False


def test_unknown_disable_does_not_replace_the_current_frame_generation(monkeypatch):
    from main_logic.core.live_frame_permissions import allows_live_frame

    client = _client({}, monkeypatch, authenticated=True)
    client.post(
        PERMISSION_ENDPOINT,
        json={
            "source_name": "demo_plugin",
            "token": "generation-one",
            "enabled": True,
        },
    )
    client.post(
        PERMISSION_ENDPOINT,
        json={
            "source_name": "demo_plugin",
            "token": "generation-two",
            "enabled": False,
        },
    )

    assert allows_live_frame("demo_plugin", "generation-one") is True
    assert allows_live_frame("demo_plugin", "generation-two") is False


def test_the_permission_route_is_not_reachable_from_another_machine(monkeypatch):
    client = _client({}, monkeypatch, host="10.0.0.7")

    assert client.post(
        PERMISSION_ENDPOINT,
        json={
            "source_name": "demo_plugin",
            "token": "generation-two",
            "enabled": False,
        },
    ).status_code == 403


DELIVERY_ENDPOINT = "/api/system/plugin-callbacks/delivery-permission"
REVOKE_ENDPOINT = "/api/system/plugin-permissions/revoke"


@pytest.mark.parametrize(
    ("endpoint", "payload"),
    [
        (
            DELIVERY_ENDPOINT,
            {
                "source_name": "victim_plugin",
                "token": "attacker-generation",
                "enabled": True,
            },
        ),
        (REVOKE_ENDPOINT, {"source_name": "victim_plugin"}),
    ],
)
def test_all_permission_mutations_require_the_host_credential(
    monkeypatch,
    endpoint,
    payload,
):
    client = _client({}, monkeypatch, authenticated=False)

    assert client.post(endpoint, json=payload).status_code == 403


def test_disabling_current_delivery_generation_retracts(
    monkeypatch,
):
    from main_logic.core.live_frame_permissions import allows_plugin_delivery

    retracted = []

    class _Session:
        def retract_callbacks_from_source(self, source_name):
            retracted.append(source_name)

    client = _client({"lanlan": _Session()}, monkeypatch, authenticated=True)
    client.post(
        DELIVERY_ENDPOINT,
        json={
            "source_name": "demo_plugin",
            "token": "generation-one",
            "enabled": True,
        },
    )
    response = client.post(
        DELIVERY_ENDPOINT,
        json={
            "source_name": "demo_plugin",
            "token": "generation-one",
            "enabled": False,
        },
    )

    assert response.status_code == 200
    assert response.json()["enabled"] is False
    assert allows_plugin_delivery("demo_plugin", "generation-one") is False
    assert allows_plugin_delivery("demo_plugin", "generation-two") is False
    assert retracted == ["demo_plugin"]


def test_stale_delivery_disable_does_not_revoke_or_retract_the_new_generation(
    monkeypatch,
):
    from main_logic.core.live_frame_permissions import allows_plugin_delivery

    retracted = []

    class _Session:
        def retract_callbacks_from_source(self, source_name):
            retracted.append(source_name)

    client = _client({"lanlan": _Session()}, monkeypatch, authenticated=True)
    for token in ("generation-one", "generation-two"):
        client.post(
            DELIVERY_ENDPOINT,
            json={
                "source_name": "demo_plugin",
                "token": token,
                "enabled": True,
            },
        )

    response = client.post(
        DELIVERY_ENDPOINT,
        json={
            "source_name": "demo_plugin",
            "token": "generation-one",
            "enabled": False,
        },
    )

    assert response.status_code == 200
    assert response.json()["applied"] is False
    assert allows_plugin_delivery("demo_plugin", "generation-two") is True
    assert retracted == []


def test_an_empty_delivery_token_does_not_retract(monkeypatch):
    from main_logic.core.live_frame_permissions import allows_plugin_delivery

    retracted = []

    class _Session:
        def retract_callbacks_from_source(self, source_name):
            retracted.append(source_name)

    client = _client({"lanlan": _Session()}, monkeypatch, authenticated=True)
    client.post(
        DELIVERY_ENDPOINT,
        json={
            "source_name": "demo_plugin",
            "token": "generation-one",
            "enabled": True,
        },
    )
    response = client.post(
        DELIVERY_ENDPOINT,
        json={
            "source_name": "demo_plugin",
            "token": "",
            "enabled": False,
        },
    )

    assert response.status_code == 200
    assert allows_plugin_delivery("demo_plugin", "generation-one") is True
    assert retracted == []


def test_source_revoke_clears_frame_and_delivery_permissions(monkeypatch):
    from main_logic.core.live_frame_permissions import (
        allows_live_frame,
        allows_plugin_delivery,
    )

    retracted = []

    class _Session:
        def retract_callbacks_from_source(self, source_name):
            retracted.append(source_name)

    client = _client({"lanlan": _Session()}, monkeypatch, authenticated=True)
    client.post(
        PERMISSION_ENDPOINT,
        json={
            "source_name": "demo_plugin",
            "token": "frame-generation",
            "enabled": True,
        },
    )
    client.post(
        DELIVERY_ENDPOINT,
        json={
            "source_name": "demo_plugin",
            "token": "delivery-generation",
            "enabled": True,
        },
    )

    response = client.post(
        REVOKE_ENDPOINT,
        json={"source_name": "demo_plugin"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "source_name": "demo_plugin",
        "live_frame_revoked": True,
        "delivery_revoked": True,
    }
    assert allows_live_frame("demo_plugin", "frame-generation") is False
    assert allows_plugin_delivery("demo_plugin", "delivery-generation") is False
    assert retracted == ["demo_plugin"]


def test_source_revoke_retracts_callbacks_from_a_mapping_view(monkeypatch):
    retracted = []

    class _Session:
        def retract_callbacks_from_source(self, source_name):
            retracted.append(source_name)

    client = _client({}, monkeypatch, authenticated=True)
    monkeypatch.setattr(
        live_vision_module,
        "get_session_manager",
        lambda: UserDict({"lanlan": _Session()}),
    )

    response = client.post(
        REVOKE_ENDPOINT,
        json={"source_name": "demo_plugin"},
    )

    assert response.status_code == 200
    assert retracted == ["demo_plugin"]
