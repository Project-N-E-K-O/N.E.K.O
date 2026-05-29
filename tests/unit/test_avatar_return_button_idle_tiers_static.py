from pathlib import Path

from main_routers import pages_router


PROJECT_ROOT = Path(__file__).resolve().parents[2]
AVATAR_UI_BUTTONS_PATH = PROJECT_ROOT / "static" / "avatar-ui-buttons.js"
INDEX_CSS_PATH = PROJECT_ROOT / "static" / "css" / "index.css"
CAT1_ASSET_PATH = PROJECT_ROOT / "static" / "assets" / "neko-idle" / "cat-idle-cat1.gif"
CAT1_CLICK_ASSET_PATH = PROJECT_ROOT / "static" / "assets" / "neko-idle" / "cat-idle-cat1-click.gif"
CAT2_ASSET_PATH = PROJECT_ROOT / "static" / "assets" / "neko-idle" / "cat-idle-cat2.gif"
CAT2_CLICK_ASSET_PATH = PROJECT_ROOT / "static" / "assets" / "neko-idle" / "cat-idle-cat2-click.gif"
CAT3_ASSET_PATH = PROJECT_ROOT / "static" / "assets" / "neko-idle" / "cat-idle-cat3.gif"
CAT3_CLICK_ASSET_PATH = PROJECT_ROOT / "static" / "assets" / "neko-idle" / "cat-idle-cat3-click.gif"


def test_return_button_idle_tier_assets_are_mapped_in_source():
    source = AVATAR_UI_BUTTONS_PATH.read_text(encoding="utf-8")

    # Non-click states
    assert "/static/assets/neko-idle/cat-idle-cat1.gif" in source
    assert "/static/assets/neko-idle/cat-idle-cat2.gif" in source
    assert "/static/assets/neko-idle/cat-idle-cat3.gif" in source
    assert '_NEKO_IDLE_TIER_CAT1' in source
    assert '_NEKO_IDLE_TIER_CAT2' in source
    assert '_NEKO_IDLE_TIER_CAT3' in source

    # Click states
    assert "/static/assets/neko-idle/cat-idle-cat1-click.gif" in source
    assert "/static/assets/neko-idle/cat-idle-cat2-click.gif" in source
    assert "/static/assets/neko-idle/cat-idle-cat3-click.gif" in source
    assert '_getNekoIdleReturnClickAssetUrl' in source


def test_return_button_idle_tier_styles_are_present():
    source = INDEX_CSS_PATH.read_text(encoding="utf-8")

    assert '.neko-idle-return-btn[data-neko-idle-tier="cat2"]' in source
    assert '.neko-idle-return-btn[data-neko-idle-tier="cat3"]' in source


def test_return_button_idle_tier_switch_uses_crossfade_motion():
    button_source = AVATAR_UI_BUTTONS_PATH.read_text(encoding="utf-8")
    css_source = INDEX_CSS_PATH.read_text(encoding="utf-8")

    assert '_NEKO_IDLE_RETURN_TRANSITION_MS = 820' in button_source
    assert '_setNekoIdleReturnArtSource' in button_source
    assert 'neko-idle-return-art-next' in button_source
    assert "button.classList.add('is-tier-transitioning')" in button_source
    assert '_shouldReduceNekoIdleMotion' in button_source

    assert '@keyframes nekoIdleTierOut' in css_source
    assert '@keyframes nekoIdleTierIn' in css_source
    assert '.neko-idle-return-btn.is-tier-transitioning' in css_source
    assert '@media (prefers-reduced-motion: reduce)' in css_source


def test_return_button_idle_tier_assets_are_version_tracked():
    for path in (CAT1_ASSET_PATH, CAT1_CLICK_ASSET_PATH,
                 CAT2_ASSET_PATH, CAT2_CLICK_ASSET_PATH,
                 CAT3_ASSET_PATH, CAT3_CLICK_ASSET_PATH):
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
