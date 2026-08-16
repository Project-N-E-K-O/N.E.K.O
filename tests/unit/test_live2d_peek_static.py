from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
LIVE2D_INTERACTION_PATH = PROJECT_ROOT / "static" / "live2d" / "live2d-interaction.js"
LIVE2D_CORE_PATH = PROJECT_ROOT / "static" / "live2d" / "live2d-core.js"
LIVE2D_UI_BUTTONS_PATH = PROJECT_ROOT / "static" / "live2d" / "live2d-ui-buttons.js"
INDEX_CSS_PATH = PROJECT_ROOT / "static" / "css" / "index.css"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _edge_peek_source() -> str:
    source = _source(LIVE2D_INTERACTION_PATH)
    return source.split("const LIVE2D_EDGE_CONTACT_TOLERANCE_PX", 1)[1].split(
        "Live2DManager.prototype.setupDragAndDrop", 1
    )[0]


def _try_apply_source() -> str:
    source = _source(LIVE2D_INTERACTION_PATH)
    return source.split(
        "Live2DManager.prototype._tryApplyLive2DPeek = async function", 1
    )[1].split("function clearLive2DPeek", 1)[0]


def test_live2d_widget_mode_edge_peek_is_widget_mode_gated_and_uses_six_anchors():
    source = _source(LIVE2D_INTERACTION_PATH)
    edge_peek_source = _edge_peek_source()

    assert "LIVE2D_PEEK_TRIGGER_RATIO" not in source
    assert "LIVE2D_PEEK_TRIGGER_MIN_PX" not in source
    assert "LIVE2D_PEEK_TRIGGER_MAX_PX" not in source
    assert "LIVE2D_EDGE_CONTACT_TOLERANCE_PX = 8" in source
    assert "LIVE2D_PEEK_VISIBLE_RATIO = 0.22" in source
    assert "LIVE2D_PEEK_VISIBLE_MIN_PX = 96" in source
    assert "LIVE2D_PEEK_VISIBLE_MAX_PX = 180" in source
    assert "LIVE2D_PEEK_SIDE_ROTATION_DEGREES = 60" in source
    assert "LIVE2D_PEEK_CORNER_ROTATION_DEGREES = 45" in source
    assert "LIVE2D_PEEK_HEAD_Y_RATIO = 0.24" in source
    assert "function isLive2DPeekEnabled()" in source
    assert "isLive2DPeekDesktopRuntime()" in source
    assert "window.nekoWidgetMode.isEnabled()" in source
    assert (
        "function getLive2DPeekEdgeContact(manager, model, viewport = null, options = null)"
        in edge_peek_source
    )
    assert "getLive2DModelGeometryBounds(manager, model)" in edge_peek_source
    assert "manager.getModelDrawableScreenRects({ padding: 0 }, model)" in edge_peek_source
    assert "nearLeft" in edge_peek_source
    assert "nearRight" in edge_peek_source
    assert "nearTop" in edge_peek_source
    assert "nearBottom" in edge_peek_source
    assert "verticalEdge ? `${verticalEdge}-${side}` : side" in edge_peek_source
    assert "'top-left', 'top-right', 'bottom-left', 'bottom-right'" in edge_peek_source
    assert "this._tryApplyLive2DPeek(model, settledContact, { isCurrentSettlement })" in source


def test_live2d_widget_mode_edge_peek_hides_controls_without_locking_live2d():
    interaction_source = _source(LIVE2D_INTERACTION_PATH)
    css_source = _source(INDEX_CSS_PATH)

    assert "neko-live2d-peek" in css_source
    assert "body.neko-live2d-peek #live2d-floating-buttons" in css_source
    assert "body.neko-live2d-peek #live2d-lock-icon" in css_source
    assert "display: none !important;" in css_source
    assert "pointer-events: none !important;" in css_source

    full_edge_peek_source = interaction_source.split("const LIVE2D_EDGE_CONTACT_TOLERANCE_PX", 1)[1]
    full_edge_peek_source = full_edge_peek_source.split("Live2DManager.prototype.setupDragAndDrop", 1)[0]
    assert ".classList.add('neko-live2d-peek')" in full_edge_peek_source
    assert ".classList.remove('neko-live2d-peek')" in full_edge_peek_source
    assert "this._setLive2DPeekControlsSuppressed(true)" in full_edge_peek_source
    assert "this._setLive2DPeekControlsSuppressed(false)" in full_edge_peek_source
    assert "element.style.setProperty('display', 'none', 'important')" in full_edge_peek_source
    assert "setLocked(true" not in full_edge_peek_source
    assert "this.isLocked = true" not in full_edge_peek_source


def test_live2d_widget_mode_edge_peek_uses_natural_offscreen_transform_not_mask_or_canvas_clip():
    edge_peek_source = _edge_peek_source()
    css_source = _source(INDEX_CSS_PATH)

    forbidden = [
        "PIXI.Graphics",
        "wrapper.mask",
        "model.mask",
        "clipPath",
        "webkitClipPath",
        "drawPolygon",
        "maskPoints",
        "createLive2DPeekWrapper",
    ]
    for token in forbidden:
        assert token not in edge_peek_source

    assert "body.neko-live2d-peek #live2d-canvas" not in css_source
    assert "model.x = target.x;" in edge_peek_source
    assert "model.y = target.y;" in edge_peek_source
    assert "model.rotation = target.rotation;" in edge_peek_source
    assert "model.scale.x = target.scaleX;" in edge_peek_source
    assert "getLive2DPeekViewportIntersection" in edge_peek_source


def test_live2d_widget_mode_edge_peek_faces_screen_inward_and_restores_transform():
    edge_peek_source = _edge_peek_source()
    full_source = _source(LIVE2D_INTERACTION_PATH)

    assert "function getLive2DPeekInwardScaleX(model, side)" in edge_peek_source
    assert "side === 'left' ? Math.abs(baseScaleX) : -Math.abs(baseScaleX)" in edge_peek_source
    assert "LIVE2D_PEEK_SIDE_ROTATION_DEGREES" in edge_peek_source
    assert "LIVE2D_PEEK_CORNER_ROTATION_DEGREES" in edge_peek_source
    assert "baseScaleX" in edge_peek_source
    assert "model.scale.x = state.baseScaleX;" in full_source
    assert "model.rotation = state.baseRotation;" in full_source
    assert "model.x = state.baseX;" in full_source
    assert "model.y = state.baseY;" in full_source


def test_live2d_widget_mode_edge_peek_prefers_head_anchor_and_preserves_vertical_intent():
    edge_peek_source = _edge_peek_source()

    assert "LIVE2D_PEEK_HEAD_Y_RATIO" in edge_peek_source
    assert "const baseHeadAnchor = getLive2DPeekHeadAnchor(manager);" in edge_peek_source
    assert "const baseBodyRect = getLive2DPeekBodyRect(manager);" in edge_peek_source
    assert "bounds.top + bounds.height * LIVE2D_PEEK_HEAD_Y_RATIO" in edge_peek_source
    assert "const desiredHeadX = side === 'left'" in edge_peek_source
    assert "desiredHeadX - transformedHeadAnchor.x" in edge_peek_source
    assert "desiredWaistX - transformedBodyRect.centerX" in edge_peek_source
    assert "baseBodyRect.bottom - transformedBodyRect.bottom" in edge_peek_source
    assert "transformedBounds.top + transformedBounds.height * LIVE2D_PEEK_HEAD_Y_RATIO" in edge_peek_source
    assert "const useHeadAnchor = !!verticalEdge && !!transformedHeadAnchor;" in edge_peek_source
    assert "const useWaistAnchor = !verticalEdge && !!(baseBodyRect && transformedBodyRect);" in edge_peek_source
    assert "? viewport.bottom - desiredHeadInsetY" in edge_peek_source
    assert ": viewport.top + desiredHeadInsetY;" in edge_peek_source
    assert "offsetY = desiredHeadYAtEdge - transformedHeadAnchor.y;" in edge_peek_source
    assert "offsetY = baseBodyRect.bottom - transformedBodyRect.bottom;" in edge_peek_source
    assert "offsetY = desiredHeadY - targetHeadY;" in edge_peek_source
    assert "getLive2DPeekVerticalCorrection" in edge_peek_source


def test_live2d_widget_mode_show_buttons_clears_hide_timer_before_peek_suppression():
    source = _source(LIVE2D_INTERACTION_PATH)
    show_buttons_source = source.split("const showButtons = () => {", 1)[1]
    show_buttons_source = show_buttons_source.split("const startHideTimer", 1)[0]

    peek_guard = show_buttons_source.split("if (this.isLive2DPeekActive()) {", 1)[1].split("return;", 1)[0]
    assert "clearTimeout(this._hideButtonsTimer);" in peek_guard
    assert "this._hideButtonsTimer = null;" in peek_guard
    assert "this._setLive2DPeekControlsSuppressed(true);" in peek_guard
    clear_index = peek_guard.index("clearTimeout(this._hideButtonsTimer);")
    null_index = peek_guard.index("this._hideButtonsTimer = null;")
    suppress_index = peek_guard.index("_setLive2DPeekControlsSuppressed(true);")
    assert clear_index < null_index < suppress_index


def test_live2d_widget_mode_edge_peek_click_keeps_anchor_and_explicit_drag_exits():
    source = _source(LIVE2D_INTERACTION_PATH)
    drag_source = source.split("Live2DManager.prototype.setupDragAndDrop = function", 1)[1]
    drag_source = drag_source.split("Live2DManager.prototype.setupWheelZoom", 1)[0]
    wheel_source = source.split("Live2DManager.prototype.setupWheelZoom = function", 1)[1]
    wheel_source = wheel_source.split("Live2DManager.prototype.setupTouchZoom", 1)[0]
    touch_source = source.split("Live2DManager.prototype.setupTouchZoom = function", 1)[1]
    touch_source = touch_source.split("Live2DManager.prototype.enableMouseTracking", 1)[0]

    assert "const edgePeekOnPointerDown = this.isLive2DPeekActive();" in drag_source
    assert "await this.restoreLive2DPeek('click-restore');" not in drag_source
    assert "clearLive2DPeek('drag-start');" in drag_source
    assert "this.clearLive2DPeek('drag-start');" not in drag_source
    assert drag_source.count(
        "placeLive2DGrabPointAtPointer(model, dragGrabLocalPoint, pointer);"
    ) == 1
    assert "restore: false" not in drag_source
    assert "if (this.isLive2DPeekActive()) {" in wheel_source
    assert "return; // edge peek ignores wheel zoom" in wheel_source
    assert "this._debouncedSnapCheck();" not in wheel_source.split("if (this.isLive2DPeekActive()) {", 1)[1].split("return; // edge peek ignores wheel zoom", 1)[0]
    assert "return; // edge peek ignores touch zoom start" in touch_source
    assert "return; // edge peek ignores touch zoom move" in touch_source
    assert "return; // edge peek ignores touch zoom end without saving peek state" in touch_source
    assert "this.currentModel.scale.set(newScale);" not in touch_source.split("return; // edge peek ignores touch zoom move", 1)[0].split("const onTouchMove", 1)[1]
    assert "await this._savePositionAfterInteraction();" not in touch_source.split("return; // edge peek ignores touch zoom end without saving peek state", 1)[0].split("const onTouchEnd", 1)[1]


def test_live2d_widget_mode_edge_peek_reports_viewport_intersection_bounds():
    core_source = _source(LIVE2D_CORE_PATH)
    interaction_source = _source(LIVE2D_INTERACTION_PATH)
    edge_peek_source = _edge_peek_source()
    bounds_source = core_source.split("getModelScreenBounds() {", 1)[1]
    bounds_source = bounds_source.split("const model = this.currentModel;", 1)[0]
    viewport_source = interaction_source.split("function getLive2DPeekViewport(bounds = null, manager = null)", 1)[1]
    viewport_source = viewport_source.split("function getLive2DPeekViewportIntersection", 1)[0]

    assert "const edgePeekState = this._live2DPeekState;" in bounds_source
    assert "edgePeekState.active" in bounds_source
    assert "edgePeekState.phase === 'hidden' || edgePeekState.phase === 'hiding'" in bounds_source
    assert "return null;" in bounds_source
    assert "model.getBounds()" in bounds_source
    assert "const viewportLeft = 0;" in bounds_source
    assert "const viewportTop = 0;" in bounds_source
    assert "const visibleLeft = Math.max(left, viewportLeft);" in bounds_source
    assert "const visibleRight = Math.min(right, viewportRight);" in bounds_source
    assert "const renderer = this.pixi_app && this.pixi_app.renderer;" in bounds_source
    assert "const screen = renderer && renderer.screen;" in bounds_source
    assert "Number.isFinite(rendererW) && rendererW > 0" in bounds_source
    assert "const niriVirtualViewport = getLive2DNiriPetVirtualViewportSize();" in bounds_source
    assert "? niriVirtualViewport.width" in bounds_source
    assert "? niriVirtualViewport.height" in bounds_source
    assert "maskPoints" not in bounds_source
    assert "drawPolygon" not in bounds_source
    assert "const renderer = manager && manager.pixi_app && manager.pixi_app.renderer;" in viewport_source
    assert "const screen = renderer && renderer.screen;" in viewport_source
    assert "const canvasW = Number(screen && screen.width);" in viewport_source
    assert "const vw = Number(window.innerWidth);" in viewport_source
    assert "const niriVirtualViewport = getLive2DNiriPetVirtualViewport();" in viewport_source
    assert "const viewportW = niriVirtualViewport" in viewport_source
    assert "? niriVirtualViewport.width" in viewport_source
    assert "? niriVirtualViewport.height" in viewport_source
    assert "Number.isFinite(canvasW) && canvasW > 0" in viewport_source
    assert "? Math.min(canvasW, vw)" in viewport_source
    assert "(validVw ? vw : fallbackW)" in viewport_source
    assert "(validVh ? vh : fallbackH)" in viewport_source
    assert "__nekoNiriPetPhysicalCrop" in core_source
    assert "__nekoNiriPetPhysicalCrop" in interaction_source
    assert "state && state.enabled === true ? state.virtualBounds : null" in core_source
    assert "state && state.enabled === true ? state.virtualBounds : null" in interaction_source
    assert "getLive2DPeekPlacement(model, bounds, this, contact)" in edge_peek_source
    assert "Math.max(fallbackW" not in viewport_source
    assert "Math.max(fallbackH" not in viewport_source


def test_live2d_delayed_toolbar_initialization_preserves_active_peek_suppression():
    source = _source(LIVE2D_UI_BUTTONS_PATH)
    delayed_setup = source.split("this.pixi_app.ticker.add(tick);", 1)[1]
    delayed_setup = delayed_setup.split("if (this.tutorialProtectionTimer)", 1)[0]
    tutorial_protection = source.split(
        "this.tutorialProtectionTimer = setInterval(() => {", 1
    )[1]
    tutorial_protection = tutorial_protection.split("}, 300);", 1)[0]

    assert delayed_setup.count("this.isLive2DPeekActive()") >= 2
    assert delayed_setup.count("this._setLive2DPeekControlsSuppressed(true);") >= 2
    assert delayed_setup.index("this.isLive2DPeekActive()") < delayed_setup.index(
        "buttonsContainer.style.display = 'flex';"
    )
    assert "this.isLive2DPeekActive()" in tutorial_protection
    assert (
        "this._setLive2DPeekControlsSuppressed(true);" in tutorial_protection
    )
    assert tutorial_protection.index(
        "this.isLive2DPeekActive()"
    ) < tutorial_protection.index("if (window.isInTutorial === true)")


def test_live2d_widget_mode_edge_peek_normal_snap_caps_renderer_at_web_viewport():
    interaction_source = _source(LIVE2D_INTERACTION_PATH)
    snap_source = interaction_source.split("Live2DManager.prototype._checkSnapRequired = async function", 1)[1]
    snap_source = snap_source.split("Live2DManager.prototype._applySnapAnimation", 1)[0]

    assert "const bounds = getLive2DModelGeometryBounds(this, model);" in snap_source
    assert "const renderer = this.pixi_app && this.pixi_app.renderer;" in snap_source
    assert "const rendererScreen = renderer && renderer.screen;" in snap_source
    assert "let screenRight = Number.isFinite(rendererW) && rendererW > 0 ? rendererW : window.innerWidth;" in snap_source
    assert "let screenBottom = Number.isFinite(rendererH) && rendererH > 0 ? rendererH : window.innerHeight;" in snap_source
    assert "screenRight = Math.min(screenRight, viewportW);" in snap_source
    assert "screenBottom = Math.min(screenBottom, viewportH);" in snap_source


def test_live2d_widget_mode_edge_peek_does_not_persist_peek_position():
    source = _source(LIVE2D_INTERACTION_PATH)
    edge_peek_source = _edge_peek_source()
    drag_source = source.split("Live2DManager.prototype.setupDragAndDrop = function", 1)[1]
    drag_source = drag_source.split("Live2DManager.prototype.setupWheelZoom", 1)[0]
    terminal_source = source.split(
        "Live2DManager.prototype._settleLive2DDragTerminal = async function", 1
    )[1].split("// 设置拖拽功能", 1)[0]

    assert "_savePositionAfterInteraction" not in _try_apply_source()
    assert "baseX" in edge_peek_source
    assert "baseY" in edge_peek_source
    assert "peekX" in edge_peek_source
    assert "peekY" in edge_peek_source
    assert "hiddenX" in edge_peek_source
    assert "hiddenY" in edge_peek_source
    assert "await this._settleLive2DDragTerminal(model, settlementOptions);" in drag_source
    assert "await this._savePositionAfterInteraction({ isCurrentSettlement });" in terminal_source
    assert "const edgeContact = isLive2DPeekEnabled()" in terminal_source
    assert "settleLive2DBaseAtEdgeContact(model, edgeContact)" in terminal_source
    edge_terminal_source = terminal_source.split(
        "if (edgeContact && settleLive2DBaseAtEdgeContact(model, edgeContact))", 1
    )[1].split(
        "const snapped = await this._checkAndPerformSnap(model, { isCurrentSettlement });",
        1,
    )[0]
    assert (
        "await this._savePositionAfterInteraction({ isCurrentSettlement });"
        in edge_terminal_source
    )
    assert (
        "await this._tryApplyLive2DPeek(model, settledContact, { isCurrentSettlement });"
        in edge_terminal_source
    )
    assert (
        edge_terminal_source.index(
            "await this._savePositionAfterInteraction({ isCurrentSettlement });"
        )
        < edge_terminal_source.index(
            "await this._tryApplyLive2DPeek(model, settledContact, { isCurrentSettlement });"
        )
    )


def test_live2d_widget_mode_edge_peek_animations_do_not_outlive_cleared_state():
    source = _source(LIVE2D_INTERACTION_PATH)
    edge_peek_source = _edge_peek_source()

    assert "shouldContinue = null" in edge_peek_source
    assert "typeof shouldContinue === 'function' && !shouldContinue()" in edge_peek_source
    assert "this._live2DPeekTransitionId = (this._live2DPeekTransitionId || 0) + 1;" in source
    assert "transitionId" in edge_peek_source
    assert "activeState.transitionId === transitionId" in edge_peek_source
    assert "const animated = await animateLive2DPeekTransform(" in edge_peek_source
    assert "if (!animated || !stillCurrent()) return false;" in edge_peek_source


def test_live2d_widget_mode_edge_peek_clears_on_disable_goodbye_reset_and_auto_cat():
    interaction_source = _source(LIVE2D_INTERACTION_PATH)
    core_source = _source(LIVE2D_CORE_PATH)

    assert "window.addEventListener('neko:widget-mode-state-changed', clearLive2DPeekOnDisabled)" in interaction_source
    assert "window.addEventListener('neko:widget-interaction-state-changed', syncLive2DPeekWithInteraction)" in interaction_source
    assert "clearLive2DPeek('display-changed')" in interaction_source
    assert "window.addEventListener('live2d-goodbye-click', clearLive2DPeekOnGoodbye)" in interaction_source
    assert "clearLive2DPeek('widget-mode-disabled')" in interaction_source
    assert "clearLive2DPeek('live2d-goodbye')" in interaction_source
    assert "clearLive2DPeek('model-reload')" in interaction_source
    assert "this.clearLive2DPeek('model-reload')" not in interaction_source
    assert "this.clearLive2DPeek('reset-model-position')" in core_source
    assert "let lastViewportW = window.innerWidth;" in core_source
    assert "let lastViewportH = window.innerHeight;" in core_source
    assert "vw === lastViewportW && vh === lastViewportH" in core_source
    assert "this.clearLive2DPeek(`viewport-changed:${reason}`);" in core_source


def test_live2d_widget_mode_edge_peek_uses_event_signal_without_polling():
    edge_peek_source = _edge_peek_source()

    assert "neko:live2d-peek-changed" in edge_peek_source
    assert "visibleBounds" in edge_peek_source
    assert "setInterval" not in edge_peek_source
    assert "MutationObserver" not in edge_peek_source
