from pathlib import Path

from main_routers import pages_router


PROJECT_ROOT = Path(__file__).resolve().parents[2]
AVATAR_UI_BUTTONS_PATH = PROJECT_ROOT / "static" / "avatar-ui-buttons.js"
INDEX_CSS_PATH = PROJECT_ROOT / "static" / "css" / "index.css"
CAT2_ASSET_PATH = PROJECT_ROOT / "static" / "assets" / "neko-idle" / "cat-idle-cat2.png"
CAT3_ASSET_PATH = PROJECT_ROOT / "static" / "assets" / "neko-idle" / "cat-idle-cat3.png"


def test_return_button_idle_tier_assets_are_mapped_in_source():
    source = AVATAR_UI_BUTTONS_PATH.read_text(encoding="utf-8")

    assert "/static/assets/neko-idle/cat-idle-cat2.png" in source
    assert "/static/assets/neko-idle/cat-idle-cat3.png" in source
    assert '_NEKO_IDLE_TIER_CAT2' in source
    assert '_NEKO_IDLE_TIER_CAT3' in source


def test_return_button_idle_tier_styles_are_present():
    source = INDEX_CSS_PATH.read_text(encoding="utf-8")

    assert '.neko-idle-return-btn[data-neko-idle-tier="cat2"]' in source
    assert '.neko-idle-return-btn[data-neko-idle-tier="cat3"]' in source


def test_return_button_idle_tier_assets_are_version_tracked():
    assert CAT2_ASSET_PATH in pages_router._YUI_GUIDE_ASSET_VERSION_PATHS
    assert CAT3_ASSET_PATH in pages_router._YUI_GUIDE_ASSET_VERSION_PATHS
    assert CAT2_ASSET_PATH.is_file()
    assert CAT3_ASSET_PATH.is_file()
