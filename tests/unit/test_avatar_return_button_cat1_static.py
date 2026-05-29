from pathlib import Path

from main_routers import pages_router


PROJECT_ROOT = Path(__file__).resolve().parents[2]
AVATAR_UI_BUTTONS_PATH = PROJECT_ROOT / "static" / "avatar-ui-buttons.js"
INDEX_CSS_PATH = PROJECT_ROOT / "static" / "css" / "index.css"
CAT1_ASSET_PATH = PROJECT_ROOT / "static" / "assets" / "neko-idle" / "cat-idle-cat1.gif"


def test_cat1_return_button_visual_contract_is_present():
    source = AVATAR_UI_BUTTONS_PATH.read_text(encoding="utf-8")

    assert "neko:auto-goodbye:state-change" in source
    assert "data-neko-idle-tier" in source
    assert "/static/assets/neko-idle/cat-idle-cat1.gif" in source

    create_return_block = source.split("ManagerPrototype.createReturnButton = function()", 1)[1].split(
        "ManagerPrototype._setupReturnButtonDrag",
        1,
    )[0]
    assert "rest_off.png" not in create_return_block
    assert "rest_on.png" not in create_return_block
    assert "neko-idle-return-art" in create_return_block


def test_cat1_return_button_assets_are_version_tracked():
    assert AVATAR_UI_BUTTONS_PATH in pages_router._YUI_GUIDE_ASSET_VERSION_PATHS
    assert INDEX_CSS_PATH in pages_router._YUI_GUIDE_ASSET_VERSION_PATHS
    assert CAT1_ASSET_PATH in pages_router._YUI_GUIDE_ASSET_VERSION_PATHS
    assert CAT1_ASSET_PATH.is_file()
