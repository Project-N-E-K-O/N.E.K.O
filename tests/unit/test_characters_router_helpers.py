import pytest


@pytest.mark.unit
def test_derive_model_asset_binding_marks_http_path_as_manual_external():
    from main_routers.characters_router import _derive_model_asset_binding

    asset_source, asset_source_id = _derive_model_asset_binding("https://example.com/model.model3.json")

    assert asset_source == "manual_external"
    assert asset_source_id == ""


@pytest.mark.unit
def test_resolve_live2d_model_binding_keeps_http_source_as_manual_external():
    from main_routers.characters_router import _resolve_live2d_model_binding

    _, resolved_source_id, resolved_source = _resolve_live2d_model_binding(
        "https://example.com/model.model3.json"
    )

    assert resolved_source == "manual_external"
    assert resolved_source_id == ""


@pytest.mark.unit
def test_find_live2d_model_catalog_entry_prefers_strict_item_path_match_when_item_id_is_shared():
    from main_routers.characters_router import _find_live2d_model_catalog_entry

    all_models = [
        {
            "item_id": "123456",
            "name": "same_name",
            "path": "/workshop/123456/alpha/alpha.model3.json",
        },
        {
            "item_id": "123456",
            "name": "same_name",
            "path": "/workshop/123456/beta/beta.model3.json",
        },
    ]

    matched = _find_live2d_model_catalog_entry(
        all_models,
        model_name="same_name",
        model_path="/workshop/123456/beta/beta.model3.json",
        asset_source="steam_workshop",
        item_id="123456",
    )

    assert matched is all_models[1]


@pytest.mark.unit
def test_find_live2d_model_catalog_entry_avoids_loose_item_match_when_item_id_is_ambiguous():
    from main_routers.characters_router import _find_live2d_model_catalog_entry

    all_models = [
        {
            "item_id": "123456",
            "name": "model_a",
            "path": "/workshop/123456/a/a.model3.json",
        },
        {
            "item_id": "123456",
            "name": "model_b",
            "path": "/workshop/123456/b/b.model3.json",
        },
    ]

    matched = _find_live2d_model_catalog_entry(
        all_models,
        model_name="",
        model_path="",
        asset_source="steam_workshop",
        item_id="123456",
    )

    assert matched is None
