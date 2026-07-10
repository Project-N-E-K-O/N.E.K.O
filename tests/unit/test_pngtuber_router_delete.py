import json
from types import SimpleNamespace

import pytest

import main_routers.pngtuber_router as pngtuber_router


def test_normalize_pngtuber_config_preserves_mobile_layout_fields():
    result = pngtuber_router._normalize_pngtuber_config(
        "avatar",
        {
            "model_type": "pngtuber",
            "pngtuber": {
                "idle_image": "idle.png",
                "scale": 1.25,
                "offset_x": 12,
                "offset_y": -24,
                "mobile_scale": 0.8,
                "mobile_offset_x": 3,
                "mobile_offset_y": -6,
            },
        },
    )

    assert result["idle_image"] == "/user_pngtuber/avatar/idle.png"
    assert result["scale"] == 1.25
    assert result["offset_x"] == 12
    assert result["offset_y"] == -24
    assert result["mobile_scale"] == 0.8
    assert result["mobile_offset_x"] == 3
    assert result["mobile_offset_y"] == -6


def test_normalize_pngtuber_config_defaults_mobile_scale_from_desktop_scale_string():
    result = pngtuber_router._normalize_pngtuber_config(
        "avatar",
        {
            "model_type": "pngtuber",
            "pngtuber": {
                "idle_image": "idle.png",
                "scale": "0.75",
            },
        },
    )

    assert result["mobile_scale"] == 0.75
    assert result["mobile_offset_x"] == 0
    assert result["mobile_offset_y"] == 0


@pytest.mark.parametrize("scale", ["nan", "inf", "-inf"])
def test_normalize_pngtuber_config_defaults_mobile_scale_for_non_finite_desktop_scale(scale):
    result = pngtuber_router._normalize_pngtuber_config(
        "avatar",
        {
            "model_type": "pngtuber",
            "pngtuber": {
                "idle_image": "idle.png",
                "scale": scale,
            },
        },
    )

    assert result["mobile_scale"] == 1


@pytest.mark.parametrize(
    "payload",
    [
        {"folder": "avatar(1)"},
        {"url": "/user_pngtuber/avatar(1)/model.json"},
    ],
)
async def test_delete_pngtuber_model_preserves_existing_folder_name(monkeypatch, tmp_path, payload):
    target_dir = tmp_path / "avatar(1)"
    target_dir.mkdir()
    (target_dir / "model.json").write_text('{"model_type":"pngtuber"}', encoding="utf-8")
    config_manager = SimpleNamespace(
        pngtuber_dir=tmp_path,
        ensure_pngtuber_directory=lambda: True,
    )
    monkeypatch.setattr(pngtuber_router, "get_config_manager", lambda: config_manager)

    response = await pngtuber_router.delete_pngtuber_model(payload)
    body = json.loads(response.body.decode("utf-8"))

    assert response.status_code == 200
    assert body["success"] is True
    assert not target_dir.exists()


@pytest.mark.parametrize(
    "payload",
    [
        {"folder": "avatar(1)/nested"},
        {"url": "/user_pngtuber/../avatar(1)/model.json"},
        {"url": "/user_pngtuber/avatar(1)/nested/model.json"},
    ],
)
async def test_delete_pngtuber_model_rejects_non_folder_keys(monkeypatch, tmp_path, payload):
    config_manager = SimpleNamespace(
        pngtuber_dir=tmp_path,
        ensure_pngtuber_directory=lambda: True,
    )
    monkeypatch.setattr(pngtuber_router, "get_config_manager", lambda: config_manager)

    response = await pngtuber_router.delete_pngtuber_model(payload)
    body = json.loads(response.body.decode("utf-8"))

    assert response.status_code == 400
    assert body["success"] is False
