from pathlib import Path

from main_routers import pages_router


PROJECT_ROOT = Path(__file__).resolve().parents[2]
AVATAR_UI_BUTTONS_PATH = PROJECT_ROOT / "static" / "avatar-ui-buttons.js"
INDEX_CSS_PATH = PROJECT_ROOT / "static" / "css" / "index.css"
CAT1_ASSET_PATH = PROJECT_ROOT / "static" / "assets" / "neko-idle" / "cat-idle-cat1.png"
CAT1_CLICK_ASSET_PATH = PROJECT_ROOT / "static" / "assets" / "neko-idle" / "cat-idle-cat1-click.png"
CAT2_ASSET_PATH = PROJECT_ROOT / "static" / "assets" / "neko-idle" / "cat-idle-cat2.png"
CAT2_CLICK_ASSET_PATH = PROJECT_ROOT / "static" / "assets" / "neko-idle" / "cat-idle-cat2-click.png"
CAT3_CLICK_ASSET_PATH = PROJECT_ROOT / "static" / "assets" / "neko-idle" / "cat-idle-cat3-click.png"
CAT3_GIF_ASSET_PATH = PROJECT_ROOT / "static" / "assets" / "neko-idle" / "cat-idle-cat3.gif"


def test_return_button_idle_tier_assets_are_mapped_in_source():
    source = AVATAR_UI_BUTTONS_PATH.read_text(encoding="utf-8")

    # Non-click states
    assert "/static/assets/neko-idle/cat-idle-cat1.png" in source
    assert "/static/assets/neko-idle/cat-idle-cat2.png" in source
    assert "/static/assets/neko-idle/cat-idle-cat3.gif" in source
    assert '_NEKO_IDLE_TIER_CAT1' in source
    assert '_NEKO_IDLE_TIER_CAT2' in source
    assert '_NEKO_IDLE_TIER_CAT3' in source

    # Click states
    assert "/static/assets/neko-idle/cat-idle-cat1-click.png" in source
    assert "/static/assets/neko-idle/cat-idle-cat2-click.png" in source
    assert "/static/assets/neko-idle/cat-idle-cat3-click.png" in source
    assert '_getNekoIdleReturnClickAssetUrl' in source


def test_return_button_idle_tier_styles_are_present():
    source = INDEX_CSS_PATH.read_text(encoding="utf-8")

    assert '.neko-idle-return-btn[data-neko-idle-tier="cat2"]' in source
    assert '.neko-idle-return-btn[data-neko-idle-tier="cat3"]' in source


def test_return_button_idle_tier_assets_are_version_tracked():
    for path in (CAT1_ASSET_PATH, CAT1_CLICK_ASSET_PATH,
                 CAT2_ASSET_PATH, CAT2_CLICK_ASSET_PATH,
                 CAT3_CLICK_ASSET_PATH, CAT3_GIF_ASSET_PATH):
        assert path in pages_router._YUI_GUIDE_ASSET_VERSION_PATHS
        assert path.is_file()


def test_no_box_shadow_or_border_in_base_return_btn_css():
    source = INDEX_CSS_PATH.read_text(encoding="utf-8")

    assert 'box-shadow' not in source or 'neko-idle-return-btn' not in _extract_neko_block(source, 'box-shadow')
    assert 'border' not in source or 'neko-idle-return-btn' not in _extract_neko_block(source, 'border')
    assert 'backdrop-filter' not in source or 'neko-idle-return-btn' not in _extract_neko_block(source, 'backdrop-filter')


def _extract_neko_block(source, keyword):
    lines = source.splitlines()
    in_neko = False
    result = []
    for line in lines:
        if '.neko-idle-return' in line:
            in_neko = True
        elif in_neko and line.strip() == '}':
            in_neko = False
            if any(keyword in l for l in result):
                return '.neko-idle-return-btn'
        if in_neko:
            result.append(line)
    return ''
