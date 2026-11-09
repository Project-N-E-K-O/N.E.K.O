"""M-001 P0-2: the numeric contract lives in the service, not only in the clients.

``POST /api/config/preferences`` used to accept any value that ``json`` could
parse, which is how ``NaN`` / ``Infinity`` reached disk. These tests pin the
server-side validation and the resulting HTTP 400 -- the three browser clients
guard ``position``/``scale`` themselves but leave ``parameters`` values
unchecked, so the server is the only place that sees them all.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from starlette.requests import Request
from starlette.responses import Response

from main_routers.config_router import preferences as preferences_router
from utils import preferences
from utils import preferences_sanitize as sanitize

INVALID_MESSAGE = "偏好数据格式无效"


@pytest.fixture
def isolated_preferences(monkeypatch, tmp_path):
    path = tmp_path / "user_preferences.json"

    class Config:
        def get_config_path(self, _name):
            return path

        get_runtime_config_path = get_config_path

        def ensure_config_directory(self):
            path.parent.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(preferences, "_config_manager", Config())
    monkeypatch.setattr(preferences, "assert_cloudsave_writable", lambda *a, **k: None)
    monkeypatch.setattr(sanitize, "_SANITIZE_WARNED", set())
    monkeypatch.setattr(sanitize, "_SANITIZE_SUPPRESSED", [0])
    return path


def save_request(payload):
    async def receive():
        return {
            "type": "http.request",
            "body": json.dumps(payload).encode(),
            "more_body": False,
        }

    return Request({"type": "http", "method": "POST", "path": "/preferences"}, receive)


def payload(**overrides):
    data = {
        "model_path": "avatar.model3.json",
        "position": {"x": 1, "y": 2},
        "scale": {"x": 1, "y": 1},
    }
    data.update(overrides)
    return data


def post(payload_data):
    """Invoke the handler the way FastAPI does, with an injected response."""
    response = Response()
    body = asyncio.run(
        preferences_router.save_preferences(save_request(payload_data), response)
    )
    return response.status_code, body


# ---------------------------------------------------------------------------
# A. validator: required fields, numbers, and the B-1 magnitude rule
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "bad",
    [float("nan"), float("inf"), float("-inf")],
)
def test_position_rejects_non_finite(bad):
    assert preferences.validate_model_preferences(
        payload(position={"x": bad, "y": 2})
    ) is False


@pytest.mark.unit
def test_position_rejects_missing_key():
    assert preferences.validate_model_preferences(payload(position={"x": 1})) is False


@pytest.mark.unit
@pytest.mark.parametrize("bad", [0, -1, -0.5])
def test_scale_must_be_positive(bad):
    assert preferences.validate_model_preferences(payload(scale={"x": bad, "y": 1})) is False


@pytest.mark.unit
def test_parameters_reject_non_finite_values():
    """The one channel no client validates."""
    assert preferences.validate_model_preferences(
        payload(parameters={"ParamAngleX": float("nan")})
    ) is False


@pytest.mark.unit
def test_parameters_reject_boolean_values():
    assert preferences.validate_model_preferences(
        payload(parameters={"ParamAngleX": True})
    ) is False


@pytest.mark.unit
def test_parameters_reject_overlong_keys():
    assert preferences.validate_model_preferences(
        payload(parameters={"P" * 257: 1})
    ) is False


@pytest.mark.unit
def test_parameters_accept_finite_snapshot():
    assert preferences.validate_model_preferences(
        payload(parameters={"ParamAngleX": 12.5, "ParamEyeLOpen": 0},
                parameters_mode="replace")
    ) is True


@pytest.mark.unit
def test_huge_integer_is_rejected():
    """B-1: 10**500 must not raise; it is simply not a usable coordinate."""
    assert preferences.validate_model_preferences(
        payload(position={"x": 10 ** 500, "y": 0})
    ) is False


@pytest.mark.unit
def test_safe_integer_boundary():
    assert preferences.validate_model_preferences(payload(position={"x": 2 ** 53, "y": 0})) is True
    assert preferences.validate_model_preferences(
        payload(position={"x": 2 ** 53 + 1, "y": 0})
    ) is False


@pytest.mark.unit
def test_camera_position_rejects_non_finite():
    assert preferences.validate_model_preferences(
        payload(camera_position={"x": 0, "y": 0, "z": 0, "qx": float("nan")})
    ) is False


@pytest.mark.unit
def test_display_rejects_non_finite():
    assert preferences.validate_model_preferences(
        payload(display={"screenX": float("nan"), "screenY": 0})
    ) is False


@pytest.mark.unit
def test_rotation_rejects_non_finite():
    assert preferences.validate_model_preferences(
        payload(rotation={"x": 0, "y": float("inf"), "z": 0})
    ) is False


@pytest.mark.unit
def test_viewport_requires_positive_dimensions():
    assert preferences.validate_model_preferences(
        payload(viewport={"width": -1, "height": 1080})
    ) is False
    assert preferences.validate_model_preferences(
        payload(viewport={"width": 1920})
    ) is False
    assert preferences.validate_model_preferences(
        payload(viewport={"width": 1920, "height": 1080})
    ) is True


@pytest.mark.unit
@pytest.mark.parametrize("bad_path", ["", 123, None, "a" * 4097, "bad\x00path"])
def test_model_path_is_validated(bad_path):
    assert preferences.validate_model_preferences(payload(model_path=bad_path)) is False


@pytest.mark.unit
def test_parameters_mode_requires_parameters():
    assert preferences.validate_model_preferences(payload(parameters_mode="replace")) is False


@pytest.mark.unit
def test_parameters_mode_must_be_replace():
    assert preferences.validate_model_preferences(
        payload(parameters={"P": 1}, parameters_mode="merge")
    ) is False


@pytest.mark.unit
def test_full_valid_payload_is_accepted():
    assert preferences.validate_model_preferences(
        payload(
            rotation={"x": 0, "y": 0.5, "z": 0},
            viewport={"width": 1920, "height": 1080},
            display={"screenX": 0, "screenY": 0},
            camera_position={"x": 1, "y": 2, "z": 3, "qx": 0, "qy": 0, "qz": 0, "qw": 1},
            parameters={"ParamAngleX": 10},
            parameters_mode="replace",
        )
    ) is True


@pytest.mark.unit
def test_unknown_keys_reject_non_finite_but_keep_finite_values():
    """Unknown keys are tolerated, but only with values the writer can emit."""
    assert preferences.validate_model_preferences(
        payload(position={"x": 1, "y": 2, "extra": float("nan")})
    ) is False
    assert preferences.validate_model_preferences(
        payload(rotation={"x": 0, "y": 0, "z": 0, "meta": [float("-inf")]})
    ) is False
    assert preferences.validate_model_preferences(
        payload(position={"x": 1, "y": 2, "label": "a", "extra": 3.5}, future_field=1)
    ) is True


@pytest.mark.unit
def test_explicit_null_optional_fields_stay_accepted():
    """Clients have always been allowed to send null for an optional block."""
    assert preferences.validate_model_preferences(
        payload(viewport=None, rotation=None, display=None, camera_position=None)
    ) is True


# ---------------------------------------------------------------------------
# C. HTTP layer: 400 with a stable body, and nothing written
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "bad_payload",
    [
        payload(position={"x": float("nan"), "y": 0}),
        payload(position={"x": float("inf"), "y": 0}),
        payload(scale={"x": float("-inf"), "y": 1}),
        payload(position={"x": True, "y": 0}),
        payload(position={"x": 10 ** 500, "y": 0}),
        payload(parameters={"ParamAngleX": float("nan")}),
        payload(parameters_mode="replace"),
        payload(model_path=123),
        # Values outside the documented numeric keys still reach the strict
        # writer; they must fail validation (400), not the write (500).
        payload(position={"x": 1, "y": 2, "extra": float("nan")}),
        payload(display={"screenX": 0, "nested": {"z": float("inf")}}),
        payload(future_field=float("nan")),
    ],
)
def test_post_rejects_non_finite_with_400_and_writes_nothing(
    isolated_preferences, bad_payload
):
    assert preferences.update_model_preferences(
        "keep.model3.json", {"x": 1, "y": 2}, {"x": 1, "y": 1}
    )
    before = isolated_preferences.read_bytes()

    status, body = post(bad_payload)

    assert status == 400
    assert body["success"] is False
    assert body["error"] == INVALID_MESSAGE
    assert isolated_preferences.read_bytes() == before


@pytest.mark.unit
def test_post_rejects_empty_body_with_400(isolated_preferences):
    status, body = post({})
    assert status == 400
    assert body["success"] is False


@pytest.mark.unit
def test_post_accepts_valid_payload_with_200(isolated_preferences):
    status, body = post(
        payload(
            parameters={"ParamAngleX": 12},
            parameters_mode="replace",
            viewport={"width": 1920, "height": 1080},
        )
    )

    assert status == 200
    assert body["success"] is True
    stored = json.loads(isolated_preferences.read_text(encoding="utf-8"))
    entry = next(e for e in stored if e["model_path"] == "avatar.model3.json")
    assert entry["position"] == {"x": 1, "y": 2}
    assert entry["parameters"] == {"ParamAngleX": 12}
    assert entry["parameters_mode"] == "replace"


@pytest.mark.unit
def test_post_error_body_does_not_leak_internal_state(isolated_preferences):
    status, body = post(payload(position={"x": float("nan"), "y": 0}))
    assert status == 400
    assert set(body) == {"success", "error"}
    # Fixed copy, not an exception repr.
    assert body["error"] == INVALID_MESSAGE
