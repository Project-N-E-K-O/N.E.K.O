from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
LIVE2D_INTERACTION_PATH = PROJECT_ROOT / "static" / "live2d" / "live2d-interaction.js"
LIVE2D_CORE_PATH = PROJECT_ROOT / "static" / "live2d" / "live2d-core.js"
APP_GAME_MODE_BETA_PATH = PROJECT_ROOT / "static" / "app" / "app-game-mode-beta.js"
INDEX_CSS_PATH = PROJECT_ROOT / "static" / "css" / "index.css"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _edge_peek_source() -> str:
    source = _source(LIVE2D_INTERACTION_PATH)
    return source.split("LIVE2D_GAME_MODE_EDGE_PEEK_TRIGGER_RATIO", 1)[1].split("/**", 1)[0]


def test_live2d_game_mode_edge_peek_is_game_mode_gated_and_uses_six_anchors():
    source = _source(LIVE2D_INTERACTION_PATH)
    edge_peek_source = _edge_peek_source()

    assert "LIVE2D_GAME_MODE_EDGE_PEEK_TRIGGER_RATIO = 0.025" in source
    assert "LIVE2D_GAME_MODE_EDGE_PEEK_VISIBLE_RATIO = 0.22" in source
    assert "LIVE2D_GAME_MODE_EDGE_PEEK_VISIBLE_MIN_PX = 96" in source
    assert "LIVE2D_GAME_MODE_EDGE_PEEK_VISIBLE_MAX_PX = 180" in source
    assert "LIVE2D_GAME_MODE_EDGE_PEEK_SIDE_ROTATION_DEGREES = 60" in source
    assert "LIVE2D_GAME_MODE_EDGE_PEEK_CORNER_ROTATION_DEGREES = 45" in source
    assert "LIVE2D_GAME_MODE_EDGE_PEEK_HEAD_Y_RATIO = 0.24" in source
    assert "function isLive2DGameModeEdgePeekEnabled()" in source
    assert "window.nekoGameModeBeta.isEnabled()" in source
    assert "function getLive2DGameModeEdgePeekAnchor(bounds, viewport)" in edge_peek_source
    assert "nearLeft" in edge_peek_source
    assert "nearRight" in edge_peek_source
    assert "nearTop" in edge_peek_source
    assert "nearBottom" in edge_peek_source
    assert "verticalEdge ? `${verticalEdge}-${side}` : side" in edge_peek_source
    assert "'top-left', 'top-right', 'bottom-left', 'bottom-right'" in edge_peek_source
    assert "this._tryApplyLive2DGameModeEdgePeek(model)" in source


def test_live2d_game_mode_edge_peek_hides_controls_without_locking_live2d():
    interaction_source = _source(LIVE2D_INTERACTION_PATH)
    css_source = _source(INDEX_CSS_PATH)

    assert "neko-live2d-game-mode-edge-peek" in css_source
    assert "body.neko-live2d-game-mode-edge-peek #live2d-floating-buttons" in css_source
    assert "body.neko-live2d-game-mode-edge-peek #live2d-lock-icon" in css_source
    assert "display: none !important;" in css_source
    assert "pointer-events: none !important;" in css_source

    full_edge_peek_source = interaction_source.split("LIVE2D_GAME_MODE_EDGE_PEEK_TRIGGER_RATIO", 1)[1]
    full_edge_peek_source = full_edge_peek_source.split("Live2DManager.prototype.setupDragAndDrop", 1)[0]
    assert ".classList.add('neko-live2d-game-mode-edge-peek')" in full_edge_peek_source
    assert ".classList.remove('neko-live2d-game-mode-edge-peek')" in full_edge_peek_source
    assert "setLocked(true" not in full_edge_peek_source
    assert "this.isLocked = true" not in full_edge_peek_source


def test_live2d_game_mode_edge_peek_uses_natural_offscreen_transform_not_mask_or_canvas_clip():
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
        "createLive2DGameModeEdgePeekWrapper",
    ]
    for token in forbidden:
        assert token not in edge_peek_source

    assert "body.neko-live2d-game-mode-edge-peek #live2d-canvas" not in css_source
    assert "model.x = target.x;" in edge_peek_source
    assert "model.y = target.y;" in edge_peek_source
    assert "model.rotation = target.rotation;" in edge_peek_source
    assert "model.scale.x = target.scaleX;" in edge_peek_source
    assert "getLive2DGameModeEdgePeekViewportIntersection" in edge_peek_source


def test_live2d_game_mode_edge_peek_faces_screen_inward_and_restores_transform():
    edge_peek_source = _edge_peek_source()
    full_source = _source(LIVE2D_INTERACTION_PATH)

    assert "function getLive2DGameModeEdgePeekInwardScaleX(model, side)" in edge_peek_source
    assert "side === 'left' ? Math.abs(baseScaleX) : -Math.abs(baseScaleX)" in edge_peek_source
    assert "LIVE2D_GAME_MODE_EDGE_PEEK_SIDE_ROTATION_DEGREES" in edge_peek_source
    assert "LIVE2D_GAME_MODE_EDGE_PEEK_CORNER_ROTATION_DEGREES" in edge_peek_source
    assert "baseScaleX" in edge_peek_source
    assert "model.scale.x = state.baseScaleX;" in full_source
    assert "model.rotation = state.baseRotation;" in full_source
    assert "model.x = state.baseX;" in full_source
    assert "model.y = state.baseY;" in full_source


def test_live2d_game_mode_edge_peek_prefers_head_anchor_and_preserves_vertical_intent():
    edge_peek_source = _edge_peek_source()

    assert "LIVE2D_GAME_MODE_EDGE_PEEK_HEAD_Y_RATIO" in edge_peek_source
    assert "const baseHeadAnchor = getLive2DGameModeEdgePeekHeadAnchor(manager);" in edge_peek_source
    assert "const baseBodyRect = getLive2DGameModeEdgePeekBodyRect(manager);" in edge_peek_source
    assert "bounds.top + bounds.height * LIVE2D_GAME_MODE_EDGE_PEEK_HEAD_Y_RATIO" in edge_peek_source
    assert "const desiredHeadX = side === 'left'" in edge_peek_source
    assert "desiredHeadX - transformedHeadAnchor.x" in edge_peek_source
    assert "desiredWaistX - transformedBodyRect.centerX" in edge_peek_source
    assert "baseBodyRect.bottom - transformedBodyRect.bottom" in edge_peek_source
    assert "transformedBounds.top + transformedBounds.height * LIVE2D_GAME_MODE_EDGE_PEEK_HEAD_Y_RATIO" in edge_peek_source
    assert "const useHeadAnchor = !!verticalEdge && !!transformedHeadAnchor;" in edge_peek_source
    assert "const useWaistAnchor = !verticalEdge && !!(baseBodyRect && transformedBodyRect);" in edge_peek_source
    assert "? viewport.bottom - desiredHeadInsetY" in edge_peek_source
    assert ": viewport.top + desiredHeadInsetY;" in edge_peek_source
    assert "offsetY = desiredHeadYAtEdge - transformedHeadAnchor.y;" in edge_peek_source
    assert "offsetY = baseBodyRect.bottom - transformedBodyRect.bottom;" in edge_peek_source
    assert "offsetY = desiredHeadY - targetHeadY;" in edge_peek_source
    assert "getLive2DGameModeEdgePeekVerticalCorrection" in edge_peek_source


def test_live2d_game_mode_edge_peek_click_restores_and_drag_exits_without_click_action():
    source = _source(LIVE2D_INTERACTION_PATH)
    drag_source = source.split("Live2DManager.prototype.setupDragAndDrop = function", 1)[1]
    drag_source = drag_source.split("Live2DManager.prototype.setupWheelZoom", 1)[0]
    wheel_source = source.split("Live2DManager.prototype.setupWheelZoom = function", 1)[1]
    wheel_source = wheel_source.split("Live2DManager.prototype.setupTouchZoom", 1)[0]
    touch_source = source.split("Live2DManager.prototype.setupTouchZoom = function", 1)[1]
    touch_source = touch_source.split("Live2DManager.prototype.enableMouseTracking", 1)[0]

    assert "const edgePeekOnPointerDown = this.isLive2DGameModeEdgePeekActive();" in drag_source
    assert "await this.restoreLive2DGameModeEdgePeek('click-restore');" in drag_source
    assert "this.clearLive2DGameModeEdgePeek('drag-start', { restore: false });" in drag_source
    assert "return; // edge peek click restores instead of triggering touch motions" in drag_source
    assert "if (this.isLive2DGameModeEdgePeekActive()) {" in wheel_source
    assert "return; // edge peek ignores wheel zoom" in wheel_source
    assert "this._debouncedSnapCheck();" not in wheel_source.split("if (this.isLive2DGameModeEdgePeekActive()) {", 1)[1].split("return; // edge peek ignores wheel zoom", 1)[0]
    assert "return; // edge peek ignores touch zoom start" in touch_source
    assert "return; // edge peek ignores touch zoom move" in touch_source
    assert "return; // edge peek ignores touch zoom end without saving peek state" in touch_source
    assert "this.currentModel.scale.set(newScale);" not in touch_source.split("return; // edge peek ignores touch zoom move", 1)[0].split("const onTouchMove", 1)[1]
    assert "await this._savePositionAfterInteraction();" not in touch_source.split("return; // edge peek ignores touch zoom end without saving peek state", 1)[0].split("const onTouchEnd", 1)[1]


def test_live2d_game_mode_edge_peek_reports_viewport_intersection_bounds():
    core_source = _source(LIVE2D_CORE_PATH)
    interaction_source = _source(LIVE2D_INTERACTION_PATH)
    edge_peek_source = _edge_peek_source()
    bounds_source = core_source.split("getModelScreenBounds() {", 1)[1]
    bounds_source = bounds_source.split("const model = this.currentModel;", 1)[0]
    viewport_source = interaction_source.split("function getLive2DGameModeEdgePeekViewport(bounds = null, manager = null)", 1)[1]
    viewport_source = viewport_source.split("function getLive2DGameModeEdgePeekViewportIntersection", 1)[0]

    assert "const edgePeekState = this._live2DGameModeEdgePeekState;" in bounds_source
    assert "edgePeekState.active" in bounds_source
    assert "model.getBounds()" in bounds_source
    assert "const viewportLeft = 0;" in bounds_source
    assert "const viewportTop = 0;" in bounds_source
    assert "const visibleLeft = Math.max(left, viewportLeft);" in bounds_source
    assert "const visibleRight = Math.min(right, viewportRight);" in bounds_source
    assert "const renderer = this.pixi_app && this.pixi_app.renderer;" in bounds_source
    assert "const screen = renderer && renderer.screen;" in bounds_source
    assert "Number.isFinite(rendererW) && rendererW > 0" in bounds_source
    assert "maskPoints" not in bounds_source
    assert "drawPolygon" not in bounds_source
    assert "const renderer = manager && manager.pixi_app && manager.pixi_app.renderer;" in edge_peek_source
    assert "const screen = renderer && renderer.screen;" in edge_peek_source
    assert "const viewportW = Number.isFinite(rendererW) && rendererW > 0 ? rendererW : Number(window.innerWidth);" in edge_peek_source
    assert "const viewportH = Number.isFinite(rendererH) && rendererH > 0 ? rendererH : Number(window.innerHeight);" in edge_peek_source
    assert "const width = Number.isFinite(viewportW) && viewportW > 0 ? viewportW : fallbackW;" in edge_peek_source
    assert "const height = Number.isFinite(viewportH) && viewportH > 0 ? viewportH : fallbackH;" in edge_peek_source
    assert "getLive2DGameModeEdgePeekPlacement(model, bounds, this)" in edge_peek_source
    assert "Math.max(fallbackW" not in viewport_source
    assert "Math.max(fallbackH" not in viewport_source


def test_live2d_game_mode_edge_peek_normal_snap_uses_renderer_screen_bounds():
    interaction_source = _source(LIVE2D_INTERACTION_PATH)
    snap_source = interaction_source.split("Live2DManager.prototype._checkSnapRequired = async function", 1)[1]
    snap_source = snap_source.split("Live2DManager.prototype._applySnapAnimation", 1)[0]

    assert "const renderer = this.pixi_app && this.pixi_app.renderer;" in snap_source
    assert "const rendererScreen = renderer && renderer.screen;" in snap_source
    assert "let screenRight = Number.isFinite(rendererW) && rendererW > 0 ? rendererW : window.innerWidth;" in snap_source
    assert "let screenBottom = Number.isFinite(rendererH) && rendererH > 0 ? rendererH : window.innerHeight;" in snap_source


def test_live2d_game_mode_edge_peek_does_not_persist_peek_position():
    source = _source(LIVE2D_INTERACTION_PATH)
    edge_peek_source = _edge_peek_source()
    drag_source = source.split("Live2DManager.prototype.setupDragAndDrop = function", 1)[1]
    drag_source = drag_source.split("Live2DManager.prototype.setupWheelZoom", 1)[0]

    assert "_savePositionAfterInteraction" not in edge_peek_source
    assert "baseX" in edge_peek_source
    assert "baseY" in edge_peek_source
    assert "peekX" in edge_peek_source
    assert "peekY" in edge_peek_source
    assert "await this._savePositionAfterInteraction();" in drag_source
    assert "await this._tryApplyLive2DGameModeEdgePeek(model);" in drag_source
    assert (
        drag_source.index("await this._savePositionAfterInteraction();")
        < drag_source.index("await this._tryApplyLive2DGameModeEdgePeek(model);")
    )


def test_live2d_game_mode_edge_peek_animations_do_not_outlive_cleared_state():
    source = _source(LIVE2D_INTERACTION_PATH)
    edge_peek_source = _edge_peek_source()

    assert "shouldContinue = null" in edge_peek_source
    assert "typeof shouldContinue === 'function' && !shouldContinue()" in edge_peek_source
    assert "this._live2DGameModeEdgePeekTransitionId = (this._live2DGameModeEdgePeekTransitionId || 0) + 1;" in source
    assert "transitionId" in edge_peek_source
    assert "activeState.transitionId === transitionId" in edge_peek_source
    assert "const animated = await animateLive2DGameModeEdgePeekTransform(" in edge_peek_source
    assert "if (!animated || !stillCurrent()) return false;" in edge_peek_source


def test_live2d_game_mode_edge_peek_clears_on_disable_goodbye_reset_and_auto_cat():
    interaction_source = _source(LIVE2D_INTERACTION_PATH)
    core_source = _source(LIVE2D_CORE_PATH)
    game_mode_source = _source(APP_GAME_MODE_BETA_PATH)

    assert "window.addEventListener('neko:game-mode-beta-state', clearLive2DGameModeEdgePeekOnDisabled)" in interaction_source
    assert "window.addEventListener('live2d-goodbye-click', clearLive2DGameModeEdgePeekOnGoodbye)" in interaction_source
    assert "clearLive2DGameModeEdgePeek('game-mode-disabled')" in interaction_source
    assert "clearLive2DGameModeEdgePeek('live2d-goodbye')" in interaction_source
    assert "this.clearLive2DGameModeEdgePeek('model-reload')" in interaction_source
    assert "this.clearLive2DGameModeEdgePeek('reset-model-position')" in core_source
    assert "window.nekoLive2DGameModeEdgePeek.clear('game-mode-auto')" in game_mode_source
    assert "let lastViewportW = window.innerWidth;" in core_source
    assert "let lastViewportH = window.innerHeight;" in core_source
    assert "vw === lastViewportW && vh === lastViewportH" in core_source
    assert "this.clearLive2DGameModeEdgePeek(`viewport-changed:${reason}`);" in core_source


def test_live2d_game_mode_edge_peek_uses_event_signal_without_polling():
    edge_peek_source = _edge_peek_source()

    assert "neko:live2d-game-mode-edge-peek-changed" in edge_peek_source
    assert "visibleBounds" in edge_peek_source
    assert "setInterval" not in edge_peek_source
    assert "MutationObserver" not in edge_peek_source
