from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
LIVE2D_INTERACTION_PATH = PROJECT_ROOT / "static" / "live2d-interaction.js"
LIVE2D_CORE_PATH = PROJECT_ROOT / "static" / "live2d-core.js"
APP_GAME_MODE_BETA_PATH = PROJECT_ROOT / "static" / "app-game-mode-beta.js"
INDEX_CSS_PATH = PROJECT_ROOT / "static" / "css" / "index.css"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_live2d_game_mode_edge_peek_is_game_mode_gated_and_uses_waist_anchor():
    source = _source(LIVE2D_INTERACTION_PATH)

    assert "LIVE2D_GAME_MODE_EDGE_PEEK_TRIGGER_RATIO = 0.025" in source
    assert "LIVE2D_GAME_MODE_EDGE_PEEK_ANGLE_DEGREES = 45" in source
    assert "LIVE2D_GAME_MODE_EDGE_PEEK_BELT_X_RATIO = 0.5" in source
    assert "LIVE2D_GAME_MODE_EDGE_PEEK_BELT_Y_RATIO = 0.48" in source
    assert "LIVE2D_GAME_MODE_EDGE_PEEK_EDGE_INSET_PX = 8" in source
    assert "LIVE2D_GAME_MODE_EDGE_PEEK_VISIBLE_MARGIN_PX = 8" in source
    assert "function getLive2DGameModeEdgePeekSide(edge, bounds, viewportW)" in source
    assert "side === 'left'" in source
    assert "side === 'right'" in source
    assert "function isLive2DGameModeEdgePeekEnabled()" in source
    assert "window.nekoGameModeBeta.isEnabled()" in source
    assert "this._tryApplyLive2DGameModeEdgePeek(model)" in source


def test_live2d_game_mode_edge_peek_hides_controls_without_locking_live2d():
    interaction_source = _source(LIVE2D_INTERACTION_PATH)
    css_source = _source(INDEX_CSS_PATH)

    assert "neko-live2d-game-mode-edge-peek" in css_source
    assert "body.neko-live2d-game-mode-edge-peek #live2d-floating-buttons" in css_source
    assert "body.neko-live2d-game-mode-edge-peek #live2d-lock-icon" in css_source
    assert "display: none !important;" in css_source
    assert "pointer-events: none !important;" in css_source

    edge_peek_source = interaction_source.split("LIVE2D_GAME_MODE_EDGE_PEEK_TRIGGER_RATIO", 1)[1]
    edge_peek_source = edge_peek_source.split("Live2DManager.prototype.clearLive2DGameModeEdgePeek", 1)[0]
    assert ".classList.add('neko-live2d-game-mode-edge-peek')" not in edge_peek_source

    full_edge_peek_source = interaction_source.split("LIVE2D_GAME_MODE_EDGE_PEEK_TRIGGER_RATIO", 1)[1]
    full_edge_peek_source = full_edge_peek_source.split("Live2DManager.prototype.setupDragAndDrop", 1)[0]
    assert ".classList.add('neko-live2d-game-mode-edge-peek')" in full_edge_peek_source
    assert ".classList.remove('neko-live2d-game-mode-edge-peek')" in full_edge_peek_source
    assert "setLocked(true" not in full_edge_peek_source
    assert "this.isLocked = true" not in full_edge_peek_source


def test_live2d_game_mode_edge_peek_uses_model_transform_not_canvas_transform():
    interaction_source = _source(LIVE2D_INTERACTION_PATH)
    css_source = _source(INDEX_CSS_PATH)

    assert "body.neko-live2d-game-mode-edge-peek #live2d-canvas" not in css_source
    assert "--neko-live2d-game-mode-edge-peek-x" not in interaction_source
    assert "--neko-live2d-game-mode-edge-peek-angle" not in interaction_source
    assert "model.rotation = placement.rotation;" in interaction_source
    assert "model.x = placement.modelX;" in interaction_source
    assert "model.y = placement.modelY;" in interaction_source
    assert "baseRotation" in interaction_source
    assert "model.rotation = state.baseRotation;" in interaction_source
    assert "model.pivot =" not in interaction_source
    assert "model.pivot.set" not in interaction_source
    assert "transform-origin: var(--neko-live2d-game-mode-edge-peek-origin-x" not in css_source
    assert "rotate(var(--neko-live2d-game-mode-edge-peek-angle" not in css_source
    assert ".style.transform =" not in interaction_source.split("LIVE2D_GAME_MODE_EDGE_PEEK_TRIGGER_RATIO", 1)[1].split("Live2DManager.prototype.clearLive2DGameModeEdgePeek", 1)[0]


def test_live2d_game_mode_edge_peek_places_belt_after_model_rotation():
    interaction_source = _source(LIVE2D_INTERACTION_PATH)
    edge_peek_source = interaction_source.split("LIVE2D_GAME_MODE_EDGE_PEEK_TRIGGER_RATIO", 1)[1]
    edge_peek_source = edge_peek_source.split("Live2DManager.prototype.clearLive2DGameModeEdgePeek", 1)[0]

    assert "function rotateLive2DGameModeEdgePeekPoint(point, origin, angleRadians)" in edge_peek_source
    assert "const rotationOrigin = { x: model.x, y: model.y };" in edge_peek_source
    assert "const rotatedBelt = rotateLive2DGameModeEdgePeekPoint(belt, rotationOrigin, rotation);" in edge_peek_source
    assert "let offsetX = targetBeltX - rotatedBelt.x;" in edge_peek_source
    assert "let offsetY = targetBeltY - rotatedBelt.y;" in edge_peek_source
    assert "modelX: model.x + offsetX" in edge_peek_source
    assert "modelY: model.y + offsetY" in edge_peek_source


def test_live2d_game_mode_edge_peek_anchors_belt_at_edge_and_keeps_belt_up_visible():
    interaction_source = _source(LIVE2D_INTERACTION_PATH)
    edge_peek_source = interaction_source.split("LIVE2D_GAME_MODE_EDGE_PEEK_TRIGGER_RATIO", 1)[1]
    edge_peek_source = edge_peek_source.split("Live2DManager.prototype.clearLive2DGameModeEdgePeek", 1)[0]

    assert "const beltX = bounds.left + bounds.width * LIVE2D_GAME_MODE_EDGE_PEEK_BELT_X_RATIO;" in edge_peek_source
    assert "const beltY = bounds.top + bounds.height * LIVE2D_GAME_MODE_EDGE_PEEK_BELT_Y_RATIO;" in edge_peek_source
    assert "targetBeltX = LIVE2D_GAME_MODE_EDGE_PEEK_EDGE_INSET_PX;" in edge_peek_source
    assert "targetBeltX = viewportW - LIVE2D_GAME_MODE_EDGE_PEEK_EDGE_INSET_PX;" in edge_peek_source
    assert "function getLive2DGameModeEdgePeekVisibleUpperBounds(bounds, rotationOrigin, offsetX, offsetY, angleRadians)" in edge_peek_source
    assert "function getLive2DGameModeEdgePeekVisibleUpperCorrection(visibleBounds, viewportW, viewportH)" in edge_peek_source
    assert "const visibleUpperBounds = getLive2DGameModeEdgePeekVisibleUpperBounds(" in edge_peek_source
    assert "const correction = getLive2DGameModeEdgePeekVisibleUpperCorrection(visibleUpperBounds, viewportW, viewportH);" in edge_peek_source
    assert "bounds.bottom" not in edge_peek_source.split("function getLive2DGameModeEdgePeekVisibleUpperBounds", 1)[1].split("function getLive2DGameModeEdgePeekVisibleUpperCorrection", 1)[0]
    assert "bounds.top + bounds.height * LIVE2D_GAME_MODE_EDGE_PEEK_BELT_Y_RATIO" in edge_peek_source.split("function getLive2DGameModeEdgePeekVisibleUpperBounds", 1)[1].split("function getLive2DGameModeEdgePeekVisibleUpperCorrection", 1)[0]
    assert "getLive2DGameModeEdgePeekVisibilityCorrection" not in edge_peek_source


def test_live2d_game_mode_edge_peek_uses_one_shot_wrapper_mask_for_waist_up_crop():
    interaction_source = _source(LIVE2D_INTERACTION_PATH)
    edge_peek_source = interaction_source.split("LIVE2D_GAME_MODE_EDGE_PEEK_TRIGGER_RATIO", 1)[1]
    edge_peek_source = edge_peek_source.split("/**", 1)[0]

    assert "function createLive2DGameModeEdgePeekWrapper(model, bounds, placement)" in edge_peek_source
    assert "function getLive2DGameModeEdgePeekVisibleUpperMaskPoints(bounds, rotationOrigin, offsetX, offsetY, angleRadians)" in edge_peek_source
    assert "function applyLive2DGameModeEdgePeekCanvasClip(maskPoints)" in edge_peek_source
    assert "function clearLive2DGameModeEdgePeekCanvasClip()" in edge_peek_source
    assert "canvas.style.clipPath = clipPath;" in edge_peek_source
    assert "canvas.style.webkitClipPath = clipPath;" in edge_peek_source
    assert "canvas.style.clipPath = '';" in edge_peek_source
    assert "canvas.style.webkitClipPath = '';" in edge_peek_source
    assert "applyLive2DGameModeEdgePeekCanvasClip(maskPoints)" in edge_peek_source
    assert "clearLive2DGameModeEdgePeekCanvasClip();" in edge_peek_source
    assert "const wrapper = new PIXI.Container();" in edge_peek_source
    assert "const mask = new PIXI.Graphics();" in edge_peek_source
    assert "mask.beginFill(0xffffff, 1);" in edge_peek_source
    assert "mask.drawPolygon(maskPoints.flatMap((point) => [point.x, point.y]));" in edge_peek_source
    assert "bounds.top + bounds.height * LIVE2D_GAME_MODE_EDGE_PEEK_BELT_Y_RATIO" in edge_peek_source
    assert "getLocalBounds" not in edge_peek_source
    assert "const parent = model.parent && typeof model.parent.addChild === 'function' ? model.parent : null;" in edge_peek_source
    assert "addLive2DGameModeEdgePeekChildAt(parent, wrapper" in edge_peek_source
    assert "wrapper.addChild(model);" in edge_peek_source
    assert "parent.addChild(mask);" in edge_peek_source
    assert "wrapper.mask = mask;" in edge_peek_source
    assert "model.addChild(mask)" not in edge_peek_source
    assert "mask.x = targetX;" not in edge_peek_source
    assert "mask.y = targetY;" not in edge_peek_source
    assert "mask.rotation = Number.isFinite(targetRotation) ? targetRotation : 0;" not in edge_peek_source
    assert "copyLive2DGameModeEdgePeekVector" not in edge_peek_source
    assert "model.mask = mask;" not in edge_peek_source
    assert "mask.visible = false" not in edge_peek_source
    assert "baseMask" in edge_peek_source
    assert "state.wrapper.mask = null;" in edge_peek_source
    assert "model.mask = state.baseMask || null;" in edge_peek_source
    assert "addLive2DGameModeEdgePeekChildAt(" in edge_peek_source
    assert "state.wrapper.destroy({ children: false })" in edge_peek_source
    assert "state.mask.destroy" in edge_peek_source
    assert "mask.parent.removeChild(mask)" in edge_peek_source
    assert "requestAnimationFrame" not in edge_peek_source
    assert "setInterval" not in edge_peek_source
    assert "MutationObserver" not in edge_peek_source


def test_live2d_game_mode_edge_peek_reports_cropped_visible_bounds():
    core_source = _source(LIVE2D_CORE_PATH)
    bounds_source = core_source.split("getModelScreenBounds() {", 1)[1]
    bounds_source = bounds_source.split("const model = this.currentModel;", 1)[0]

    assert "const edgePeekState = this._live2DGameModeEdgePeekState;" in bounds_source
    assert "edgePeekState.active" in bounds_source
    assert "Array.isArray(edgePeekState.maskPoints)" in bounds_source
    assert "edgePeekState.maskPoints.length >= 3" in bounds_source
    assert "const xs = points.map((point) => Number(point.x));" in bounds_source
    assert "const ys = points.map((point) => Number(point.y));" in bounds_source
    assert "const left = Math.min(...xs);" in bounds_source
    assert "const right = Math.max(...xs);" in bounds_source
    assert "const top = Math.min(...ys);" in bounds_source
    assert "const bottom = Math.max(...ys);" in bounds_source
    assert "centerX: left + width / 2" in bounds_source
    assert "centerY: top + height / 2" in bounds_source


def test_live2d_game_mode_edge_peek_is_side_aware_without_polling():
    interaction_source = _source(LIVE2D_INTERACTION_PATH)
    edge_peek_source = interaction_source.split("LIVE2D_GAME_MODE_EDGE_PEEK_TRIGGER_RATIO", 1)[1]
    edge_peek_source = edge_peek_source.split("Live2DManager.prototype.clearLive2DGameModeEdgePeek", 1)[0]

    assert "const side = getLive2DGameModeEdgePeekSide(edge, bounds, viewportW);" in edge_peek_source
    assert "targetBeltX = LIVE2D_GAME_MODE_EDGE_PEEK_EDGE_INSET_PX;" in edge_peek_source
    assert "angle = LIVE2D_GAME_MODE_EDGE_PEEK_ANGLE_DEGREES;" in edge_peek_source
    assert "targetBeltX = viewportW - LIVE2D_GAME_MODE_EDGE_PEEK_EDGE_INSET_PX;" in edge_peek_source
    assert "angle = -LIVE2D_GAME_MODE_EDGE_PEEK_ANGLE_DEGREES;" in edge_peek_source
    assert "setInterval" not in edge_peek_source
    assert "setTimeout" not in edge_peek_source
    assert "requestAnimationFrame" not in edge_peek_source
    assert "MutationObserver" not in edge_peek_source


def test_live2d_game_mode_edge_peek_clears_on_disable_goodbye_reset_and_auto_cat():
    interaction_source = _source(LIVE2D_INTERACTION_PATH)
    core_source = _source(LIVE2D_CORE_PATH)
    game_mode_source = _source(APP_GAME_MODE_BETA_PATH)

    assert "window.addEventListener('neko:game-mode-beta-state', clearLive2DGameModeEdgePeekOnDisabled)" in interaction_source
    assert "window.addEventListener('live2d-goodbye-click', clearLive2DGameModeEdgePeekOnGoodbye)" in interaction_source
    assert "clearLive2DGameModeEdgePeek('game-mode-disabled')" in interaction_source
    assert "clearLive2DGameModeEdgePeek('live2d-goodbye')" in interaction_source
    assert "this.clearLive2DGameModeEdgePeek('drag-start')" in interaction_source
    assert "this.clearLive2DGameModeEdgePeek('reset-model-position')" in core_source
    assert "window.nekoLive2DGameModeEdgePeek.clear('game-mode-auto')" in game_mode_source
