import json
from types import SimpleNamespace

import pytest

import main_routers.pngtuber_router as pngtuber_router


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
