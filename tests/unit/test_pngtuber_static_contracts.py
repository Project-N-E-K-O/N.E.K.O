from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PNGTUBER_CORE_PATH = PROJECT_ROOT / "static" / "pngtuber-core.js"
APP_INTERPAGE_PATH = PROJECT_ROOT / "static" / "app-interpage.js"
APP_UI_PATH = PROJECT_ROOT / "static" / "app-ui.js"
INDEX_CSS_PATH = PROJECT_ROOT / "static" / "css" / "index.css"


def test_pngtuber_mobile_web_detection_uses_canonical_width_predicate():
    source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    block = source[
        source.index("function isPngtuberMobileWebPage()"):
        source.index("function canInteractWithAvatar()")
    ]

    assert "if (isModelManagerPage()) return false;" in block
    assert "document.body?.classList.contains('electron-chat-window')" in block
    assert "if (window.__LANLAN_IS_ELECTRON_PET__) return false;" in block
    assert "typeof window.isMobileWidth === 'function'" in block
    assert "return window.innerWidth <= 768;" in block


def test_pngtuber_config_keeps_separate_mobile_placement_fields():
    source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    normalize_block = source[
        source.index("function normalizeConfig(config)"):
        source.index("class PNGTuberManager")
    ]

    assert "normalized.scale = clampNumber(source.scale, SCALE_MIN, SCALE_MAX, 1);" in normalize_block
    assert "normalized.offset_x = Number.isFinite(Number(source.offset_x)) ? Number(source.offset_x) : 0;" in normalize_block
    assert "normalized.offset_y = Number.isFinite(Number(source.offset_y)) ? Number(source.offset_y) : 0;" in normalize_block
    assert "normalized.mobile_scale = clampNumber(source.mobile_scale, SCALE_MIN, SCALE_MAX, Math.min(normalized.scale, 1));" in normalize_block
    assert "normalized.mobile_offset_x = Number.isFinite(Number(source.mobile_offset_x)) ? Number(source.mobile_offset_x) : 0;" in normalize_block
    assert "normalized.mobile_offset_y = Number.isFinite(Number(source.mobile_offset_y)) ? Number(source.mobile_offset_y) : 0;" in normalize_block
    assert "centerPreview ? 0" not in normalize_block


def test_pngtuber_transform_and_interactions_use_active_layout_fields():
    source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    transform_block = source[
        source.index("applyTransform()"):
        source.index("applyScale(nextScale)")
    ]
    drag_block = source[
        source.index("        startDrag(event) {"):
        source.index("        handleClick(event) {")
    ]
    wheel_block = source[
        source.index("handleWheelZoom(event)"):
        source.index("getTouchDistance(touch1, touch2)")
    ]
    touch_block = source[
        source.index("startTouchZoom(event)"):
        source.index("async endTouchZoom()")
    ]
    save_block = source[
        source.index("async saveCurrentConfig()"):
        source.index("        scheduleSaveCurrentConfig")
    ]

    assert "getActiveLayoutFields()" in transform_block
    assert "getActivePlacement()" in transform_block
    assert "const renderPlacement = this.getRenderPlacement(placement);" in transform_block
    assert "renderPlacement.scale" in transform_block
    assert "renderPlacement.offsetX" in transform_block
    assert "renderPlacement.offsetY + bounce.y" in transform_block
    assert "this.config.offset_x}px" not in transform_block
    assert "this.config.scale * bounce" not in transform_block

    assert "const placement = this.getActivePlacement();" in drag_block
    assert "startOffsetX: placement.offsetX" in drag_block
    assert "this.setActiveOffsets(state.startOffsetX + dx, state.startOffsetY + dy);" in drag_block
    assert "const currentScale = this.getActivePlacement().scale;" in wheel_block
    assert "initialScale: placement.scale" in touch_block
    assert "this.setActiveOffsets(state.startOffsetX + dx, state.startOffsetY + dy);" in touch_block
    assert "this.config.mobile_offset_x" in save_block
    assert "this.config.mobile_offset_y" in save_block
    assert "this.config.mobile_scale" in save_block


def test_pngtuber_model_manager_preview_centering_does_not_mutate_saved_offsets():
    source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    render_block = source[
        source.index("getRenderPlacement(placement) {"):
        source.index("        setActiveScale(nextScale)")
    ]

    assert "isModelManagerPage()" in render_block
    assert "!this.config.preserve_model_manager_position" in render_block
    assert "offsetX: 0" in render_block
    assert "offsetY: 0" in render_block
    assert "this.config.offset_x = 0" not in source
    assert "this.config.offset_y = 0" not in source
    assert "this.config.mobile_offset_x = 0" not in source
    assert "this.config.mobile_offset_y = 0" not in source


def test_pngtuber_container_pointer_events_stay_passthrough_on_restore_paths():
    core_source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    interpage_source = APP_INTERPAGE_PATH.read_text(encoding="utf-8")
    app_ui_source = APP_UI_PATH.read_text(encoding="utf-8")
    css_source = INDEX_CSS_PATH.read_text(encoding="utf-8")

    css_container_block = css_source[
        css_source.index("#pngtuber-container {"):
        css_source.index("#pngtuber-container.minimized")
    ]
    css_image_block = css_source[
        css_source.index("#pngtuber-container .pngtuber-image {"):
        css_source.index("#pngtuber-container .pngtuber-image.is-dragging")
    ]
    assert "pointer-events: none;" in css_container_block
    assert "pointer-events: auto;" in css_image_block
    assert "this.container.style.pointerEvents = 'none';" in core_source

    assert "restoredPngtuberContainer.style.pointerEvents = 'auto';" not in interpage_source
    assert "pngtuberContainer.style.pointerEvents = 'auto';" not in interpage_source
    assert "restoredPngtuberContainer.style.pointerEvents = 'none';" in interpage_source
    assert "pngtuberContainer.style.pointerEvents = 'none';" in interpage_source
    assert "pngtuberContainer.style.setProperty('pointer-events', 'none', 'important');" in app_ui_source
    assert "pngtuberContainer.style.setProperty('pointer-events', 'auto', 'important');" not in app_ui_source
