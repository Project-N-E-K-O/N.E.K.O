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
