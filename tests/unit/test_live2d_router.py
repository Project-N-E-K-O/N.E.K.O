import json
from pathlib import Path
from unittest.mock import AsyncMock, patch
from urllib.parse import unquote

import pytest


def _make_cfa_config_manager(tmp_path: Path):
    from utils.config_manager import ConfigManager

    docs_dir = tmp_path / "Documents"
    appdata_dir = tmp_path / "AppData" / "Local"
    (docs_dir / "N.E.K.O" / "live2d").mkdir(parents=True, exist_ok=True)
    (appdata_dir / "N.E.K.O" / "live2d").mkdir(parents=True, exist_ok=True)

    def _fake_get_docs(self):
        self._first_readable_candidate = docs_dir
        return appdata_dir

    with patch.object(ConfigManager, "_get_documents_directory", _fake_get_docs):
        return ConfigManager("N.E.K.O")


def _write_model(model_dir: Path, filename: str, payload: dict) -> None:
    model_dir.mkdir(parents=True, exist_ok=True)
    (model_dir / filename).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


@pytest.mark.unit
def test_dedupe_live2d_models_for_display_prefers_documents_over_documents_local_for_same_model(tmp_path):
    cm = _make_cfa_config_manager(tmp_path)

    docs_model_dir = cm.readable_live2d_dir / "shared_model"
    local_model_dir = cm.live2d_dir / "shared_model"
    model_payload = {"Version": 3, "FileReferences": {"Moc": "shared.moc3"}}
    _write_model(docs_model_dir, "shared_model.model3.json", model_payload)
    _write_model(local_model_dir, "shared_model.model3.json", model_payload)

    from main_routers.live2d_router import _dedupe_live2d_models_for_display

    with patch("main_routers.live2d_router.get_config_manager", return_value=cm), patch(
        "main_routers.live2d_router.get_workshop_path",
        return_value=None,
    ):
        models = _dedupe_live2d_models_for_display(
            [
                {
                    "name": "shared_model",
                    "display_name": "shared_model",
                    "path": "/user_live2d/shared_model/shared_model.model3.json",
                    "source": "documents",
                },
                {
                    "name": "shared_model_documents_local",
                    "display_name": "shared_model (documents_local)",
                    "path": "/user_live2d_local/shared_model/shared_model.model3.json",
                    "source": "documents_local",
                },
            ]
        )

    assert len(models) == 1
    assert models[0]["name"] == "shared_model"
    assert "/user_live2d/shared_model/shared_model.model3.json" in models[0]["path"]


@pytest.mark.unit
def test_dedupe_live2d_models_for_display_keeps_same_name_models_when_payload_differs(tmp_path):
    cm = _make_cfa_config_manager(tmp_path)

    docs_model_dir = cm.readable_live2d_dir / "variant_model"
    local_model_dir = cm.live2d_dir / "variant_model"
    _write_model(docs_model_dir, "variant_model.model3.json", {"Version": 3, "FileReferences": {"Moc": "a.moc3"}})
    _write_model(local_model_dir, "variant_model.model3.json", {"Version": 3, "FileReferences": {"Moc": "b.moc3"}})

    from main_routers.live2d_router import _dedupe_live2d_models_for_display

    with patch("main_routers.live2d_router.get_config_manager", return_value=cm), patch(
        "main_routers.live2d_router.get_workshop_path",
        return_value=None,
    ):
        models = _dedupe_live2d_models_for_display(
            [
                {
                    "name": "variant_model",
                    "display_name": "variant_model",
                    "path": "/user_live2d/variant_model/variant_model.model3.json",
                    "source": "documents",
                },
                {
                    "name": "variant_model_documents_local",
                    "display_name": "variant_model (documents_local)",
                    "path": "/user_live2d_local/variant_model/variant_model.model3.json",
                    "source": "documents_local",
                },
            ]
        )

    assert len(models) == 2
    assert {model["name"] for model in models} == {"variant_model", "variant_model_documents_local"}


@pytest.mark.unit
def test_dedupe_live2d_models_for_display_hides_workshop_export_shadow_when_real_item_exists(tmp_path):
    cm = _make_cfa_config_manager(tmp_path)

    workshop_root = tmp_path / "workshop"
    actual_dir = workshop_root / "3671922309" / "白毛宝宝"
    export_dir = workshop_root / "WorkshopExport" / "item_shadow" / "白毛宝宝"
    model_payload = {"Version": 3, "FileReferences": {"Moc": "white_cat.moc3"}}
    _write_model(actual_dir, "白毛宝宝.model3.json", model_payload)
    _write_model(export_dir, "白毛宝宝.model3.json", model_payload)

    from main_routers.live2d_router import _dedupe_live2d_models_for_display

    with patch("main_routers.live2d_router.get_config_manager", return_value=cm), patch(
        "main_routers.live2d_router.get_workshop_path",
        return_value=str(workshop_root),
    ):
        models = _dedupe_live2d_models_for_display(
            [
                {
                    "name": "白毛宝宝_workshop_shadow",
                    "display_name": "白毛宝宝 (workshop)",
                    "path": "/workshop/WorkshopExport/item_shadow/白毛宝宝/白毛宝宝.model3.json",
                    "source": "workshop",
                },
                {
                    "name": "白毛宝宝_workshop_real",
                    "display_name": "白毛宝宝 (workshop)",
                    "path": "/workshop/3671922309/白毛宝宝/白毛宝宝.model3.json",
                    "source": "workshop",
                    "item_id": "3671922309",
                },
            ]
        )

    assert len(models) == 1
    assert models[0]["name"] == "白毛宝宝_workshop_real"
    assert "/workshop/3671922309/白毛宝宝/白毛宝宝.model3.json" in unquote(models[0]["path"])


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_live2d_models_keeps_workshop_entry_when_same_name_payload_differs(tmp_path):
    cm = _make_cfa_config_manager(tmp_path)

    local_model_dir = cm.readable_live2d_dir / "shared_model"
    workshop_root = tmp_path / "workshop"
    workshop_item_dir = workshop_root / "123456"
    _write_model(local_model_dir, "shared_model.model3.json", {"Version": 3, "FileReferences": {"Moc": "local.moc3"}})
    _write_model(workshop_item_dir, "shared_model.model3.json", {"Version": 3, "FileReferences": {"Moc": "workshop.moc3"}})

    import main_routers.live2d_router as live2d_router_module

    with patch.object(
        live2d_router_module,
        "find_models",
        return_value=[
            {
                "name": "shared_model",
                "display_name": "shared_model",
                "path": "/user_live2d/shared_model/shared_model.model3.json",
                "source": "documents",
            }
        ],
    ), patch.object(
        live2d_router_module,
        "get_subscribed_workshop_items",
        AsyncMock(
            return_value={
                "success": True,
                "items": [
                    {
                        "publishedFileId": "123456",
                        "installedFolder": str(workshop_item_dir),
                    }
                ],
            }
        ),
    ), patch(
        "main_routers.live2d_router.get_config_manager",
        return_value=cm,
    ), patch(
        "main_routers.live2d_router.get_workshop_path",
        return_value=str(workshop_root),
    ):
        models = await live2d_router_module.get_live2d_models(simple=False)

    assert len(models) == 2
    assert sum(1 for model in models if model["name"] == "shared_model") == 2
    assert any(model.get("display_name") == "shared_model" for model in models)
    assert any(model.get("display_name") == "shared_model (steam_workshop 123456)" for model in models)
    assert any(model.get("source") == "documents" for model in models)
    assert any(model.get("item_id") == "123456" for model in models)
