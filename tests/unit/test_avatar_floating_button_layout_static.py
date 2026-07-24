from pathlib import Path
from tests.static_app_parts import read_js_parts

from tests.unit.avatar_ui_buttons_source import read_avatar_ui_buttons_source


PROJECT_ROOT = Path(__file__).resolve().parents[2]
AVATAR_UI_BUTTONS_DIR = PROJECT_ROOT / "static" / "avatar" / "avatar-ui-buttons"


def _read_avatar_ui_buttons_source() -> str:
    return read_avatar_ui_buttons_source()


LIVE2D_UI_BUTTONS_PATH = PROJECT_ROOT / "static" / "live2d" / "live2d-ui-buttons.js"
APP_INTERPAGE_PATH = PROJECT_ROOT / "static" / "app" / "app-interpage"
INDEX_CSS_PATH = PROJECT_ROOT / "static" / "css" / "index.css"


def _source_slice_between(source, start_marker, end_marker, block_name):
    start = source.find(start_marker)
    assert start != -1, f"{block_name} start marker not found: {start_marker}"
    end = source.find(end_marker, start + len(start_marker))
    assert end != -1, f"{block_name} end marker not found after start: {end_marker}"
    assert start < end, f"{block_name} start marker must precede end marker"
    return source[start:end]


def test_avatar_floating_button_rows_keep_fixed_height_when_aux_controls_toggle():
    avatar_source = _read_avatar_ui_buttons_source()
    live2d_source = LIVE2D_UI_BUTTONS_PATH.read_text(encoding="utf-8")

    wrapper_block = _source_slice_between(
        avatar_source,
        "const btnWrapper = document.createElement('div');",
        "const stopWrapperEvent = (e) => { e.stopPropagation(); };",
        "floating button wrapper styles",
    )

    # Live2D positions the toolbar from five 48px rows plus four 12px gaps.
    # The row wrapper must keep that height even when the mic mute button or
    # popup trigger is shown, otherwise the vertical toolbar visibly contracts.
    assert "const LIVE2D_FLOATING_BUTTON_SIZE = 48;" in live2d_source
    assert "const LIVE2D_FLOATING_BUTTON_GAP = 12;" in live2d_source
    assert "const LIVE2D_FLOATING_BUTTON_COUNT = 5;" in live2d_source
    assert "const LIVE2D_BASE_TOOLBAR_HEIGHT =" in live2d_source
    assert "height: '48px'" in wrapper_block
    assert "minHeight: '48px'" in wrapper_block
    assert "flex: '0 0 48px'" in wrapper_block
    assert "boxSizing: 'border-box'" in wrapper_block

    mute_button_block = _source_slice_between(
        avatar_source,
        "Object.assign(muteBtn.style, {",
        "const stopMuteEvent = (e) => { e.stopPropagation(); };",
        "mic mute button styles",
    )
    assert "position: 'absolute'" in mute_button_block
    assert "top: '50%'" in mute_button_block
    assert "transform: 'translateY(-50%)'" in mute_button_block


def test_live2d_lock_icon_tracks_the_floating_toolbar_scale():
    source = LIVE2D_UI_BUTTONS_PATH.read_text(encoding="utf-8")
    lock_icon_block = _source_slice_between(
        source,
        "Live2DManager.prototype.setupHTMLLockIcon = function(model) {",
        "Live2DManager.prototype.setupFloatingButtons = function(model) {",
        "Live2D lock icon setup",
    )
    floating_buttons_block = source[source.find(
        "Live2DManager.prototype.setupFloatingButtons = function(model) {"
    ):]

    scale_call = "getLive2DFloatingControlScale(modelHeight, LIVE2D_BASE_TOOLBAR_HEIGHT)"
    assert scale_call in lock_icon_block
    assert scale_call in floating_buttons_block
    assert "lockIcon.style.transform = nextTransform;" in lock_icon_block
    assert "const actualLockIconSize = baseLockIconSize * scale;" in lock_icon_block
    assert "window.getNekoYuiGuideLockIconMaxTop(defaultMaxLockTop, actualLockIconSize)" in lock_icon_block
    assert "right: clampedLeft + actualLockIconSize" in lock_icon_block
    assert "bottom: clampedTop + actualLockIconSize" in lock_icon_block


def test_interpage_restore_keeps_floating_button_containers_in_flex_layout():
    source = read_js_parts(APP_INTERPAGE_PATH)
    restore_block = _source_slice_between(
        source,
        "restoringFloatingEls.forEach(function (el) {",
        "delete el.dataset.nekoPreHideDisplay;",
        "interpage floating button restore block",
    )

    assert "var isFloatingButtons = !!(el.id && /-floating-buttons$/.test(el.id));" in restore_block
    assert "el.style.display = isFloatingButtons ? 'flex' : restoreDisplay;" in restore_block
    assert "el.style.display = restoreDisplay;" not in restore_block


def test_interpage_hide_records_css_fallback_floating_button_display_as_flex():
    source = read_js_parts(APP_INTERPAGE_PATH)
    hide_block = _source_slice_between(
        source,
        "document.querySelectorAll(\n                '#live2d-floating-buttons",
        "el.style.display = 'none';",
        "interpage floating button hide snapshot block",
    )

    assert "var isFloatingButtons = !!(el.id && /-floating-buttons$/.test(el.id));" in hide_block
    assert "isFloatingButtons && !el.style.display && computedDisplay === 'none'" in hide_block
    assert "? 'flex'" in hide_block


def test_css_fallback_keeps_visible_floating_button_containers_as_flex():
    css_source = INDEX_CSS_PATH.read_text(encoding="utf-8")

    fallback_block = _source_slice_between(
        css_source,
        "#live2d-floating-buttons,",
        "body.neko-game-active #live2d-container,",
        "floating button display fallback css",
    )

    assert "#vrm-floating-buttons," in fallback_block
    assert "#mmd-floating-buttons," in fallback_block
    assert "#pngtuber-floating-buttons" in fallback_block
    assert "display: flex;" in fallback_block
    assert "flex-direction: column;" in fallback_block
    assert "gap: 12px;" in fallback_block
