import json
import shutil
from pathlib import Path

import pytest

from tests.static_app_parts import read_js_parts
from tests.node_harness import run_node_script


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PNGTUBER_CORE_PATH = PROJECT_ROOT / "static" / "pngtuber-core.js"
APP_BUTTONS_PATH = PROJECT_ROOT / "static" / "app" / "app-buttons.js"
APP_AUDIO_PLAYBACK_PATH = PROJECT_ROOT / "static" / "app" / "app-audio-playback.js"
APP_INTERPAGE_PATH = PROJECT_ROOT / "static" / "app" / "app-interpage"
APP_UI_PATH = PROJECT_ROOT / "static" / "app" / "app-ui"
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
    assert "normalized.position_anchor = (sourceAnchor === 'center' || sourceAnchor === 'bottom_right')" in normalize_block
    assert "? 'bottom_right' : 'center'" in normalize_block
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
        source.index("async saveOrStageCurrentConfig()"):
        source.index("        scheduleSaveCurrentConfig")
    ]
    runtime_save_block = source[
        source.index("async saveCurrentConfig()"):
        source.index("        scheduleSaveCurrentConfig")
    ]

    assert "getActiveLayoutFields()" in transform_block
    assert "getActivePlacement()" in transform_block
    assert "const renderPlacement = this.getRenderPlacement(placement);" in transform_block
    assert "const centerAnchored = modelManagerPage || this.config.position_anchor === 'center';" in transform_block
    assert "left: '50%'" in transform_block
    assert "top: '50%'" in transform_block
    assert "left: 'calc(100% - 48px)'" in transform_block
    assert "top: 'calc(100% - 18px)'" in transform_block
    assert "'translate(-50%, -50%)'" in transform_block
    assert "'translate(-100%, -100%)'" in transform_block
    assert "renderPlacement.scale" in transform_block
    assert "renderPlacement.offsetX" in transform_block
    assert "renderPlacement.offsetY + bounce.y" in transform_block
    assert "this.config.offset_x}px" not in transform_block
    assert "this.config.scale * bounce" not in transform_block

    assert "const placement = this.getActivePlacement();" in drag_block
    assert "startOffsetX: placement.offsetX" in drag_block
    assert "this.setActiveOffsets(state.startOffsetX + dx, state.startOffsetY + dy);" in drag_block
    assert "const currentScale = this.getActivePlacement().scale;" in wheel_block
    assert "this.beginModelManagerPositionEditing();" in wheel_block
    assert "window.stageModelManagerPNGTuberPlacement(this.config);" in wheel_block
    assert "initialScale: placement.scale" in touch_block
    assert "this.beginModelManagerPositionEditing();" in touch_block
    assert "this.setActiveOffsets(state.startOffsetX + dx, state.startOffsetY + dy);" in touch_block
    assert "window.stageModelManagerPNGTuberPlacement(this.config);" in save_block
    assert "this.config.mobile_offset_x" in runtime_save_block
    assert "this.config.mobile_offset_y" in runtime_save_block
    assert "this.config.mobile_scale" in runtime_save_block
    assert "this.config.position_anchor" in runtime_save_block
    assert "apply_runtime: false" in runtime_save_block


def test_pngtuber_drag_uses_the_shared_multiscreen_transfer_contract():
    source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    drag_block = source[
        source.index("        setModelDraggingState(active, moved = false) {"):
        source.index("        handleClick(event) {")
    ]

    assert "this._isDraggingModel = dragging;" in drag_block
    assert "this.image.setAttribute('data-dragging', moved ? 'true' : 'pending');" in drag_block
    assert "this.rememberDragScreenPoint(this._dragState, event, { start: true });" in drag_block
    assert "this.rememberDragScreenPoint(state, event);" in drag_block
    assert "void this.recordDragHintPointerEdgeApproach(state);" in drag_block
    assert "const displaySwitched = await this.checkAndSwitchDisplayAfterDrag(state);" in drag_block
    assert "await this.recordDragHintPointerEdgeRelease(state);" in drag_block
    assert "bridge.getAllDisplays()" in drag_block
    assert "bridge.getCurrentDisplay()" in drag_block
    assert "bridge.getDesktopCoordinateSnapshot()" in drag_block
    assert "coordinateSnapshot?.renderer?.screenOrigin" in drag_block
    assert "state?.dragHintApproachPending" in drag_block
    assert "state.dragHintApproachPending = true;" in drag_block
    assert "state.dragHintApproachPending = false;" in drag_block
    assert "this.isDragCompletionCurrent(state)" in drag_block
    assert "this.beginModelManagerPositionEditing();" in drag_block
    assert "bridge.moveWindowToDisplay(switchScreenX, switchScreenY)" in drag_block
    assert "result.windowBounds" in drag_block
    assert "this.moveModelCenterToWindowPoint(desiredCenterX, desiredCenterY);" in drag_block
    assert "helper.markDisplaySwitchSuccess('pngtuber');" in drag_block
    assert "await this.snapModelIntoScreen({ animate: true });" in drag_block
    assert drag_block.index("await this.recordDragHintPointerEdgeRelease(state);") < drag_block.index(
        "await this.snapModelIntoScreen({ animate: true });"
    )
    assert drag_block.index("await this.snapModelIntoScreen({ animate: true });") < drag_block.index(
        "await this.saveOrStageCurrentConfig();"
    )
    touch_end_block = source[
        source.index("        async endTouchZoom() {"):
        source.index("        setupHTMLLockIcon()")
    ]
    assert "await this.snapModelIntoScreen({ animate: true });" in touch_end_block
    assert "if (!this.isDragCompletionCurrent(state)) return;" in touch_end_block
    assert touch_end_block.index("if (!this.isDragCompletionCurrent(state)) return;") < touch_end_block.index(
        "await this.snapModelIntoScreen({ animate: true });"
    )
    assert touch_end_block.index("await this.snapModelIntoScreen({ animate: true });") < touch_end_block.index(
        "await this.saveOrStageCurrentConfig();"
    )


def test_pngtuber_drag_snaps_back_when_less_than_200_pixels_remain_visible():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for PNGTuber edge snap tests")

    source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    script = f"""
const assert = require('node:assert/strict');
const vm = require('node:vm');

let nextFrameId = 0;
const frames = new Map();
const requestAnimationFrame = (callback) => {{
  const frameId = ++nextFrameId;
  frames.set(frameId, callback);
  return frameId;
}};
const cancelAnimationFrame = (frameId) => {{ frames.delete(frameId); }};
const runNextFrame = (timestamp) => {{
  const next = frames.entries().next();
  assert.equal(next.done, false, 'expected a pending animation frame');
  const [frameId, callback] = next.value;
  frames.delete(frameId);
  callback(timestamp);
}};
let stagedPlacement = null;
let modelWidth = 400;
const window = {{
  location: {{ pathname: '/' }},
  innerWidth: 1000,
  innerHeight: 800,
  lanlan_config: {{ model_type: 'pngtuber' }},
  stageModelManagerPNGTuberPlacement(config) {{ stagedPlacement = {{ ...config }}; }},
}};
const document = {{
  body: {{ classList: {{ contains() {{ return false; }}, toggle() {{}} }} }},
  getElementById() {{ return null; }},
  querySelectorAll() {{ return []; }},
}};
const context = {{
  cancelAnimationFrame,
  console,
  document,
  performance: {{ now: () => 0 }},
  requestAnimationFrame,
  window,
}};
vm.runInNewContext({json.dumps(source)}, context, {{ filename: 'pngtuber-core.js' }});

const manager = new window.PNGTuberManager();
manager.config = {{
  scale: 1,
  offset_x: 0,
  offset_y: 0,
  mobile_scale: 1,
  mobile_offset_x: 0,
  mobile_offset_y: 0,
  position_anchor: 'center',
  mirror: false,
}};
manager.image = {{
  classList: {{ toggle() {{}} }},
  closest() {{ return null; }},
  setAttribute() {{}},
  removeAttribute() {{}},
  setPointerCapture() {{}},
  getBoundingClientRect() {{
    const placement = manager.getActivePlacement();
    const width = modelWidth * placement.scale;
    const height = 400 * placement.scale;
    const centerX = window.innerWidth / 2 + placement.offsetX;
    const centerY = window.innerHeight / 2 + placement.offsetY;
    return {{ left: centerX - width / 2, top: centerY - height / 2, width, height }};
  }},
}};
manager.applyTransform = () => {{}};
manager.isLayeredActive = () => false;
manager.resetLayeredDragVelocity = () => {{}};
manager.showDragImage = () => {{}};
manager.restoreStateImage = () => {{}};
manager.restartLayeredAnimationLoop = () => {{}};
manager.syncGlobalConfig = () => {{}};
manager.updateFloatingButtonsPosition = () => {{}};
manager.updateLockIconPosition = () => {{}};
let scheduledSaves = 0;
manager.scheduleSaveCurrentConfig = () => {{ scheduledSaves += 1; }};

(async () => {{
  manager.config.offset_x = -750;
  let target = manager.getEdgeSnapTarget();
  assert.equal(target.offsetX, -500);
  assert.equal(target.offsetY, 0);
  assert.equal(await manager.snapModelIntoScreen({{ animate: false }}), true);
  assert.equal(manager.config.offset_x, -500);

  // Exactly 200 px remains visible, so ordinary edge placement is preserved.
  assert.equal(manager.getEdgeSnapTarget(), null);
  assert.equal(await manager.snapModelIntoScreen({{ animate: false }}), false);

  manager.config.offset_x = 750;
  target = manager.getEdgeSnapTarget();
  assert.equal(target.offsetX, 500);
  assert.equal(await manager.snapModelIntoScreen({{ animate: false }}), true);
  assert.equal(manager.config.offset_x, 500);

  manager.config.offset_x = 0;
  manager.config.offset_y = -650;
  target = manager.getEdgeSnapTarget();
  assert.equal(target.offsetY, -400);
  assert.equal(await manager.snapModelIntoScreen({{ animate: false }}), true);
  assert.equal(manager.config.offset_y, -400);

  manager.config.offset_y = 650;
  target = manager.getEdgeSnapTarget();
  assert.equal(target.offsetY, 400);
  assert.equal(await manager.snapModelIntoScreen({{ animate: false }}), true);
  assert.equal(manager.config.offset_y, 400);

  manager.config.offset_x = -750;
  manager.config.offset_y = 0;
  const animatedSnap = manager.snapModelIntoScreen();
  assert.equal(frames.size, 1);
  runNextFrame(0);
  runNextFrame(130);
  assert.ok(manager.config.offset_x > -500, 'easeOutBack should briefly overshoot the target');
  runNextFrame(260);
  assert.equal(await animatedSnap, true);
  assert.equal(manager.config.offset_x, -500);
  assert.equal(manager.config.offset_y, 0);

  // Layered-canvas padding is transparent and does not count as visible avatar content.
  manager.isLayeredActive = () => true;
  manager.layeredCanvasPadding = 100;
  manager.layeredCanvasLogicalWidth = 600;
  manager.layeredCanvasLogicalHeight = 600;
  manager.config.offset_x = -700;
  const layeredTarget = manager.getEdgeSnapTarget();
  assert.ok(Math.abs(layeredTarget.offsetX - (-433.3333333333333)) < 0.001);
  manager.isLayeredActive = () => false;

  // Content smaller than 200 px stops once it is fully visible.
  modelWidth = 100;
  manager.config.offset_x = -480;
  const smallTarget = manager.getEdgeSnapTarget();
  assert.equal(smallTarget.offsetX, -450);
  modelWidth = 400;

  // State image geometry changes retarget an in-flight rebound.
  manager.config.offset_x = -750;
  modelWidth = 400;
  const resizedImageSnap = manager.snapModelIntoScreen();
  runNextFrame(0);
  modelWidth = 200;
  runNextFrame(130);
  runNextFrame(260);
  assert.equal(await resizedImageSnap, true);
  assert.equal(manager.config.offset_x, -400);

  // A size change during ease-out-back overshoot still replaces the stale target.
  manager.config.offset_x = -750;
  modelWidth = 400;
  const overshootResizeSnap = manager.snapModelIntoScreen();
  runNextFrame(0);
  runNextFrame(130);
  modelWidth = 370;
  runNextFrame(260);
  assert.equal(await overshootResizeSnap, true);
  assert.ok(Math.abs(manager.config.offset_x - (-485)) < 0.001);
  modelWidth = 400;

  // Growing content cannot reverse the snap target past its starting offset.
  manager.config.offset_x = -750;
  const growingImageSnap = manager.snapModelIntoScreen();
  runNextFrame(0);
  modelWidth = 2000;
  runNextFrame(130);
  runNextFrame(260);
  assert.equal(await growingImageSnap, true);
  assert.equal(manager.config.offset_x, -500);
  modelWidth = 400;

  // Crossing the responsive breakpoint restarts against the new layout fields.
  manager.config.offset_x = -750;
  manager.config.mobile_offset_x = -500;
  const responsiveSnap = manager.snapModelIntoScreen();
  runNextFrame(0);
  window.innerWidth = 600;
  runNextFrame(130);
  runNextFrame(130);
  runNextFrame(260);
  assert.equal(await responsiveSnap, true);
  assert.equal(manager.config.offset_x, -750);
  assert.equal(manager.config.mobile_offset_x, -300);
  window.innerWidth = 1000;
  manager.config.mobile_offset_x = 0;

  // Wheel input clamped at the scale boundary leaves the active rebound untouched.
  manager.config.scale = 0.1;
  manager.config.offset_x = -750;
  const minScaleWheelSnap = manager.snapModelIntoScreen();
  const sequenceBeforeClampedWheel = manager._dragSequence;
  manager.handleWheelZoom({{
    deltaY: 1000,
    preventDefault() {{}},
    stopPropagation() {{}},
  }});
  assert.equal(manager._dragSequence, sequenceBeforeClampedWheel);
  assert.equal(frames.size, 1, 'clamped wheel input must not cancel the rebound');
  runNextFrame(0);
  runNextFrame(260);
  assert.equal(await minScaleWheelSnap, true);
  manager.config.scale = 1;

  // Wheel zoom cancels the old rebound and targets the resized model geometry.
  manager.config.offset_x = -750;
  const preZoomSnap = manager.snapModelIntoScreen();
  manager.handleWheelZoom({{
    deltaY: 1000,
    preventDefault() {{}},
    stopPropagation() {{}},
  }});
  assert.equal(await preZoomSnap, false);
  assert.equal(frames.size, 1);
  runNextFrame(0);
  runNextFrame(260);
  await Promise.resolve();
  const resizedBounds = manager.image.getBoundingClientRect();
  const resizedRight = resizedBounds.left + resizedBounds.width;
  assert.ok(Math.abs(resizedRight - 200) < 0.001);
  assert.equal(scheduledSaves, 1);

  // An unchanged two-finger gesture leaves the active rebound untouched.
  manager.config.scale = 1;
  manager.config.offset_x = -750;
  const untouchedSnap = manager.snapModelIntoScreen();
  manager.startTouchZoom({{
    touches: [{{ clientX: 100, clientY: 100 }}, {{ clientX: 200, clientY: 100 }}],
    preventDefault() {{}},
    stopPropagation() {{}},
  }});
  assert.equal(frames.size, 1);
  await manager.endTouchZoom();
  assert.equal(frames.size, 1, 'unchanged touch must not cancel the rebound');
  runNextFrame(0);
  runNextFrame(260);
  assert.equal(await untouchedSnap, true);
  assert.equal(manager.config.offset_x, -500);

  // A pinch clamped at the scale boundary with no pan also preserves the rebound.
  manager.config.scale = 5;
  manager.config.offset_x = -3000;
  const maxScaleTouchSnap = manager.snapModelIntoScreen();
  const sequenceBeforeClampedTouch = manager._dragSequence;
  manager.startTouchZoom({{
    touches: [{{ clientX: 100, clientY: 100 }}, {{ clientX: 200, clientY: 100 }}],
    preventDefault() {{}},
    stopPropagation() {{}},
  }});
  manager.moveTouchZoom({{
    touches: [{{ clientX: 90, clientY: 100 }}, {{ clientX: 210, clientY: 100 }}],
    preventDefault() {{}},
    stopPropagation() {{}},
  }});
  assert.equal(manager._touchZoomState.changed, false);
  assert.equal(manager._dragSequence, sequenceBeforeClampedTouch);
  await manager.endTouchZoom();
  assert.equal(frames.size, 1, 'clamped touch scale must not cancel the rebound');
  runNextFrame(0);
  runNextFrame(260);
  assert.equal(await maxScaleTouchSnap, true);
  manager.config.scale = 1;

  // A rebound that finishes during a pending click refreshes the first drag grab offset.
  manager.config.offset_x = -750;
  const completedPendingSnap = manager.snapModelIntoScreen();
  manager.startDrag({{
    target: manager.image,
    button: 0,
    pointerId: 8,
    clientX: 100,
    clientY: 100,
    screenX: 100,
    screenY: 100,
    preventDefault() {{}},
    stopPropagation() {{}},
  }});
  runNextFrame(0);
  runNextFrame(260);
  assert.equal(await completedPendingSnap, true);
  manager.moveDrag({{
    pointerId: 8,
    clientX: 110,
    clientY: 100,
    screenX: 110,
    screenY: 100,
    preventDefault() {{}},
  }});
  const centerAfterPendingCompletionMove = manager.getModelCenterInWindow();
  assert.equal(manager._dragState.modelCenterPointerOffset.x, centerAfterPendingCompletionMove.x - 110);
  assert.equal(manager._dragState.modelCenterPointerOffset.y, centerAfterPendingCompletionMove.y - 100);
  manager._dragState = null;

  // Wheel input cannot steal ownership from an active two-finger interaction.
  manager.config.scale = 1;
  manager.config.offset_x = 0;
  manager.config.offset_y = 0;
  manager.startTouchZoom({{
    touches: [{{ clientX: 100, clientY: 100 }}, {{ clientX: 200, clientY: 100 }}],
    preventDefault() {{}},
    stopPropagation() {{}},
  }});
  manager.moveTouchZoom({{
    touches: [{{ clientX: 110, clientY: 100 }}, {{ clientX: 210, clientY: 100 }}],
    preventDefault() {{}},
    stopPropagation() {{}},
  }});
  const activeTouchSequence = manager._dragSequence;
  const activeTouchState = manager._touchZoomState;
  manager.handleWheelZoom({{
    deltaY: 1000,
    preventDefault() {{}},
    stopPropagation() {{}},
  }});
  assert.equal(manager._dragSequence, activeTouchSequence);
  assert.equal(manager._touchZoomState, activeTouchState);
  assert.equal(manager.config.scale, 1);
  manager.moveTouchZoom({{
    touches: [{{ clientX: 120, clientY: 100 }}, {{ clientX: 220, clientY: 100 }}],
    preventDefault() {{}},
    stopPropagation() {{}},
  }});
  assert.equal(manager.config.offset_x, 20);
  await manager.endTouchZoom();
  assert.equal(manager._touchZoomState, null);

  // Model-manager wheel replacement stages the final snapped placement.
  window.location.pathname = '/model_manager';
  manager._modelManagerUseCurrentPlacement = true;
  manager.config.offset_x = -750;
  manager.handleWheelZoom({{
    deltaY: 1000,
    preventDefault() {{}},
    stopPropagation() {{}},
  }});
  runNextFrame(0);
  runNextFrame(260);
  await Promise.resolve();
  assert.ok(stagedPlacement);
  assert.equal(stagedPlacement.offset_x, manager.config.offset_x);
  assert.equal(scheduledSaves, 1, 'model-manager placement must be staged instead of auto-saved');
  window.location.pathname = '/';

  manager.config.offset_x = -750;
  const cancelledSnap = manager.snapModelIntoScreen();
  assert.equal(frames.size, 1);
  manager.startDrag({{
    target: manager.image,
    button: 0,
    pointerId: 9,
    clientX: 100,
    clientY: 100,
    screenX: 100,
    screenY: 100,
    preventDefault() {{}},
    stopPropagation() {{}},
  }});
  assert.equal(frames.size, 1, 'pointerdown alone must not cancel the rebound');
  runNextFrame(0);
  runNextFrame(130);
  manager.moveDrag({{
    pointerId: 9,
    clientX: 110,
    clientY: 100,
    screenX: 110,
    screenY: 100,
    preventDefault() {{}},
  }});
  const centerAfterTakeover = manager.getModelCenterInWindow();
  assert.equal(manager._dragState.modelCenterPointerOffset.x, centerAfterTakeover.x - 110);
  assert.equal(manager._dragState.modelCenterPointerOffset.y, centerAfterTakeover.y - 100);
  manager.setActiveOffsets(-123, 45);
  assert.equal(await cancelledSnap, false);
  assert.equal(frames.size, 0);
  assert.equal(manager.config.offset_x, -123);
  assert.equal(manager.config.offset_y, 45);
}})().catch((error) => {{
  console.error(error);
  process.exit(1);
}});
"""
    run_node_script(node, script, check=True, cwd=PROJECT_ROOT)


def test_pngtuber_drag_hint_edge_approach_allows_only_one_in_flight_call():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for PNGTuber drag hint tests")

    source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    script = f"""
const assert = require('node:assert/strict');
const vm = require('node:vm');

let approachCalls = 0;
let resolveApproach;
const window = {{
  location: {{ pathname: '/' }},
  lanlan_config: {{ model_type: 'pngtuber' }},
  NekoAvatarMultiScreenDragHint: {{
    recordPointerEdgeApproach() {{
      approachCalls += 1;
      return new Promise((resolve) => {{ resolveApproach = resolve; }});
    }},
  }},
}};
const document = {{
  body: {{ classList: {{ contains() {{ return false; }} }} }},
  getElementById() {{ return null; }},
  querySelectorAll() {{ return []; }},
}};
const context = {{ console, document, window }};
vm.runInNewContext({json.dumps(source)}, context, {{ filename: 'pngtuber-core.js' }});

(async () => {{
  const manager = new window.PNGTuberManager();
  const state = {{
    dragHintStartPointer: {{ x: 10, y: 20, startedAt: 1 }},
    dragHintLastPointer: {{ x: 30, y: 40 }},
    dragHintApproachShown: false,
    dragHintApproachPending: false,
  }};
  const first = manager.recordDragHintPointerEdgeApproach(state);
  const second = manager.recordDragHintPointerEdgeApproach(state);

  assert.equal(approachCalls, 1);
  assert.equal(await second, false);
  resolveApproach(true);
  assert.equal(await first, true);
  assert.equal(state.dragHintApproachPending, false);
  assert.equal(state.dragHintApproachShown, true);
  assert.equal(await manager.recordDragHintPointerEdgeApproach(state), false);
  assert.equal(approachCalls, 1);
}})().catch((error) => {{
  console.error(error);
  process.exit(1);
}});
"""
    run_node_script(node, script, check=True, cwd=PROJECT_ROOT)


def test_pngtuber_drag_switches_to_the_pointer_display_without_losing_the_grab_point():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for PNGTuber multiscreen drag tests")

    source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    script = f"""
const assert = require('node:assert/strict');
const vm = require('node:vm');

const bodyClasses = new Set();
const bodyClassList = {{
  contains(name) {{ return bodyClasses.has(name); }},
  add(name) {{ bodyClasses.add(name); }},
  remove(name) {{ bodyClasses.delete(name); }},
  toggle(name, active) {{
    if (active) bodyClasses.add(name);
    else bodyClasses.delete(name);
  }},
}};
const document = {{
  body: {{ classList: bodyClassList }},
  getElementById() {{ return null; }},
  querySelectorAll() {{ return []; }},
}};

const primary = {{ id: 'primary', screenX: 0, screenY: 0, width: 1707, height: 1067 }};
const secondary = {{ id: 'secondary', screenX: -2560, screenY: 0, width: 2560, height: 1440 }};
const primaryWindowBounds = {{ x: 1, y: 1, width: 1706, height: 1066 }};
const secondaryWindowBounds = {{ x: -2559, y: 1, width: 2559, height: 1439 }};
let currentDisplay = primary;
let currentWindowBounds = {{ ...primaryWindowBounds }};
let movedPoint = null;
let saves = 0;
let snapshotCalls = 0;
let deferredFrames = null;
const markedSwitches = [];

const requestAnimationFrame = (callback) => {{
  if (deferredFrames) {{
    deferredFrames.push(callback);
    return deferredFrames.length;
  }}
  callback(0);
  return 1;
}};
const window = {{
  location: {{ pathname: '/' }},
  innerWidth: primaryWindowBounds.width,
  innerHeight: primaryWindowBounds.height,
  __LANLAN_IS_ELECTRON_PET__: true,
  lanlan_config: {{ model_type: 'pngtuber' }},
  requestAnimationFrame,
  electronScreen: {{
    async getAllDisplays() {{ return [primary, secondary]; }},
    async getCurrentDisplay() {{ return currentDisplay; }},
    async getDesktopCoordinateSnapshot() {{
      snapshotCalls += 1;
      return {{
        version: 2,
        window: {{ actualBounds: {{ ...currentWindowBounds }} }},
        renderer: {{ screenOrigin: {{ x: currentWindowBounds.x, y: currentWindowBounds.y }} }},
      }};
    }},
    async moveWindowToDisplay(x, y) {{
      movedPoint = {{ x, y }};
      currentDisplay = secondary;
      currentWindowBounds = {{ ...secondaryWindowBounds }};
      window.innerWidth = currentWindowBounds.width;
      window.innerHeight = currentWindowBounds.height;
      return {{
        success: true,
        sameDisplay: false,
        windowBounds: {{ ...currentWindowBounds }},
      }};
    }},
  }},
  NekoAvatarMultiScreenDragHint: {{
    async recordPointerEdgeApproach() {{ return false; }},
    async recordPointerEdgeRelease() {{ return false; }},
    markDisplaySwitchSuccess(source) {{ markedSwitches.push(source); }},
  }},
}};

const context = {{
  console,
  document,
  window,
  performance: {{ now: () => 0 }},
  requestAnimationFrame,
}};
vm.runInNewContext({json.dumps(source)}, context, {{ filename: 'pngtuber-core.js' }});

let manager = new window.PNGTuberManager();
const attributes = new Map();
const imageClasses = new Set();
const image = {{
  style: {{}},
  classList: {{
    toggle(name, active) {{
      if (active) imageClasses.add(name);
      else imageClasses.delete(name);
    }},
  }},
  closest() {{ return null; }},
  setAttribute(name, value) {{ attributes.set(name, String(value)); }},
  removeAttribute(name) {{ attributes.delete(name); }},
  setPointerCapture() {{}},
  releasePointerCapture() {{}},
  getBoundingClientRect() {{
    const centerX = window.innerWidth / 2 + manager.config.offset_x;
    const centerY = window.innerHeight / 2 + manager.config.offset_y;
    return {{ left: centerX - 100, top: centerY - 100, width: 200, height: 200 }};
  }},
}};
manager.image = image;
manager.container = {{ style: {{}} }};
manager.config = {{
  scale: 1,
  offset_x: -700,
  offset_y: 0,
  mobile_scale: 1,
  mobile_offset_x: 0,
  mobile_offset_y: 0,
  position_anchor: 'center',
  mirror: false,
}};
manager.resetLayeredDragVelocity = () => {{}};
manager.isLayeredActive = () => false;
manager.showDragImage = () => {{}};
manager.restoreStateImage = () => {{}};
manager.restartLayeredAnimationLoop = () => {{}};
manager.applyTransform = () => {{}};
manager.syncGlobalConfig = () => {{}};
manager.updateFloatingButtonsPosition = () => {{}};
manager.updateLockIconPosition = () => {{}};
manager.saveCurrentConfig = async () => {{ saves += 1; return true; }};

function pointer(type, clientX, clientY, screenX, screenY, pointerId = 7) {{
  return {{
    type,
    target: image,
    button: 0,
    pointerId,
    clientX,
    clientY,
    screenX,
    screenY,
    preventDefault() {{}},
    stopPropagation() {{}},
  }};
}}

(async () => {{
  manager.startDrag(pointer('pointerdown', 154, 534, 155, 535));
  assert.equal(manager._isDraggingModel, true);
  assert.equal(attributes.get('data-dragging'), 'pending');

  manager.moveDrag(pointer('pointermove', -201, 534, -200, 535));
  assert.equal(attributes.get('data-dragging'), 'true');

  await manager.endDrag(pointer('pointerup', -201, 534, -200, 535));

  assert.deepEqual(movedPoint, {{ x: -200, y: 535 }});
  assert.equal(currentDisplay.id, 'secondary');
  // -200 - secondary origin(-2559) + grab offset(-1) = 2358.
  assert.equal(manager.getModelCenterInWindow().x, 2358);
  // 535 - secondary origin(1) + grab offset(-1) = 533.
  assert.equal(manager.getModelCenterInWindow().y, 533);
  // 2358 - secondary window center(2559 / 2) = 1078.5.
  assert.equal(manager.config.offset_x, 1078.5);
  // 533 - secondary window center(1439 / 2) = -186.5.
  assert.equal(manager.config.offset_y, -186.5);
  assert.equal(saves, 1);
  assert.ok(snapshotCalls >= 1);
  assert.deepEqual(markedSwitches, ['pngtuber']);
  assert.equal(manager._isDraggingModel, false);
  assert.equal(attributes.has('data-dragging'), false);
  assert.equal(bodyClasses.has('neko-model-dragging'), false);

  currentDisplay = primary;
  currentWindowBounds = {{ ...primaryWindowBounds }};
  window.innerWidth = currentWindowBounds.width;
  window.innerHeight = currentWindowBounds.height;
  manager.config.offset_x = -855;
  manager.config.offset_y = 0;
  movedPoint = null;
  manager._dragSequence += 1;
  const modelOnlyState = {{
    dragSequence: manager._dragSequence,
    lastScreenPoint: null,
    modelCenterPointerOffset: {{ x: 0, y: 0 }},
  }};
  assert.equal(await manager.checkAndSwitchDisplayAfterDrag(modelOnlyState), true);
  // Primary actual origin(1) + local model center(-2) = screen x(-1).
  assert.deepEqual(movedPoint, {{ x: -1, y: 534 }});

  currentDisplay = primary;
  currentWindowBounds = {{ ...primaryWindowBounds }};
  window.innerWidth = Number.NaN;
  window.innerHeight = currentWindowBounds.height;
  movedPoint = null;
  const getModelCenterInWindow = manager.getModelCenterInWindow.bind(manager);
  manager.getModelCenterInWindow = () => ({{ x: 100, y: 100 }});
  manager._dragSequence += 1;
  const invalidWindowState = {{
    dragSequence: manager._dragSequence,
    lastScreenPoint: {{ x: -200, y: 100 }},
    modelCenterPointerOffset: {{ x: 0, y: 0 }},
  }};
  assert.equal(await manager.checkAndSwitchDisplayAfterDrag(invalidWindowState), false);
  assert.equal(movedPoint, null);
  manager.getModelCenterInWindow = getModelCenterInWindow;

  // A second pointerdown without movement must not invalidate the prior completion.
  currentDisplay = primary;
  currentWindowBounds = {{ ...primaryWindowBounds }};
  window.innerWidth = currentWindowBounds.width;
  window.innerHeight = currentWindowBounds.height;
  manager.config.offset_x = -700;
  manager.config.offset_y = 0;
  movedPoint = null;
  saves = 0;
  deferredFrames = [];
  manager.startDrag(pointer('pointerdown', 154, 534, 155, 535));
  manager.moveDrag(pointer('pointermove', -201, 534, -200, 535));
  const pendingClickEnd = manager.endDrag(pointer('pointerup', -201, 534, -200, 535));
  while (deferredFrames.length === 0) await new Promise(setImmediate);
  manager.startDrag(pointer('pointerdown', 100, 100, -2459, 101, 8));
  deferredFrames.shift()(0);
  await new Promise(setImmediate);
  deferredFrames.shift()(0);
  await pendingClickEnd;
  assert.equal(manager._dragState.pointerId, 8);
  assert.equal(manager._dragState.dragSequence, null);
  assert.equal(saves, 1);
  await manager.endDrag(pointer('pointerup', 100, 100, -2459, 101, 8));

  currentDisplay = primary;
  currentWindowBounds = {{ ...primaryWindowBounds }};
  window.innerWidth = currentWindowBounds.width;
  window.innerHeight = currentWindowBounds.height;
  manager.config.offset_x = -700;
  manager.config.offset_y = 0;
  movedPoint = null;
  saves = 0;
  deferredFrames = [];
  manager.startDrag(pointer('pointerdown', 154, 534, 155, 535));
  manager.moveDrag(pointer('pointermove', -201, 534, -200, 535));
  const staleEnd = manager.endDrag(pointer('pointerup', -201, 534, -200, 535));
  while (deferredFrames.length === 0) await new Promise(setImmediate);

  manager.startDrag(pointer('pointerdown', 100, 100, -2459, 101, 8));
  manager.moveDrag(pointer('pointermove', 120, 100, -2439, 101, 8));
  const activeDragOffsets = {{
    x: manager.config.offset_x,
    y: manager.config.offset_y,
  }};
  deferredFrames.shift()(0);
  await new Promise(setImmediate);
  deferredFrames.shift()(0);
  await staleEnd;

  assert.equal(manager._dragState.pointerId, 8);
  assert.equal(manager.config.offset_x, activeDragOffsets.x);
  assert.equal(manager.config.offset_y, activeDragOffsets.y);
  assert.equal(saves, 0);
}})().catch((error) => {{
  console.error(error);
  process.exit(1);
}});
"""
    run_node_script(node, script, check=True, cwd=PROJECT_ROOT)


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


def test_pngtuber_container_pointer_events_stay_passthrough_outside_model_manager():
    core_source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    interpage_source = read_js_parts(APP_INTERPAGE_PATH)
    app_ui_source = read_js_parts(APP_UI_PATH)
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
    assert "this.container.style.pointerEvents = modelManagerPage ? 'auto' : 'none';" in core_source
    assert "this.container.style.pointerEvents = isModelManagerPage() ? 'auto' : 'none';" in core_source

    assert "restoredPngtuberContainer.style.pointerEvents = 'auto';" not in interpage_source
    assert "pngtuberContainer.style.pointerEvents = 'auto';" not in interpage_source
    assert "restoredPngtuberContainer.style.pointerEvents = 'none';" in interpage_source
    assert "pngtuberContainer.style.pointerEvents = 'none';" in interpage_source
    assert "pngtuberContainer.style.setProperty('pointer-events', 'none', 'important');" in app_ui_source
    assert "pngtuberContainer.style.setProperty('pointer-events', 'auto', 'important');" not in app_ui_source


def test_pngtuber_mouth_flap_does_not_restart_layered_motion_timeline():
    source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    set_state_block = source[
        source.index("setState(state"):
        source.index("        currentRemixStateSettings()")
    ]
    animation_loop_block = source[
        source.index("startLayeredAnimationLoop(options = {})"):
        source.index("        motionValue(")
    ]
    schedule_block = source[
        source.index("scheduleSpeakingMouthFrame()"):
        source.index("        startSpeakingMouthAnimation()")
    ]
    start_block = source[
        source.index("        startSpeakingMouthAnimation() {"):
        source.index("        stopSpeakingMouthAnimation()")
    ]

    assert "restartLayeredAnimation !== false" in set_state_block
    assert source.count("this.layeredAnimationStart = performance.now();") == 1
    assert "this.layeredAnimationStart = performance.now();" in animation_loop_block
    assert "if (!options.preserveTimeline || !this.layeredAnimationStart)" in animation_loop_block
    assert "layeredAnimationStart = performance.now()" not in set_state_block
    assert "layeredAnimationStart = performance.now()" not in schedule_block
    assert "layeredAnimationStart = performance.now()" not in start_block
    assert set_state_block.count("this.restartLayeredAnimationLoop();") == 1
    assert (
        "if (options.restartLayeredAnimation !== false) {\n"
        "                    this.restartLayeredAnimationLoop();\n"
        "                } else if (!this.layeredAnimationFrame && this.hasMotionLayersForCurrentState()) {\n"
        "                    this.startLayeredAnimationLoop({ preserveTimeline: true });\n"
        "                }"
    ) in set_state_block
    assert "this.restartLayeredAnimationLoop();" not in schedule_block
    assert "this.restartLayeredAnimationLoop();" not in start_block
    assert "this.setState(this.speakingMouthOpen ? 'talking' : 'idle', { restartLayeredAnimation: false });" in schedule_block
    assert "this.setState('talking', { restartLayeredAnimation: false });" in start_block


def test_layered_pngtuber_speaking_bounce_does_not_transform_whole_canvas():
    source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    bounce_config_block = source[
        source.index("speakingBounceConfig()"):
        source.index("        currentSpeakingBounceTransform(")
    ]
    apply_transform_block = source[
        source.index("applyTransform(timestamp = performance.now())"):
        source.index("        getActiveLayoutFields()")
    ]

    assert "if (this.isLayeredActive()) return null;" in bounce_config_block
    assert "const bounce = this.currentSpeakingBounceTransform();" in apply_transform_block
    assert "this.image.style.transform" in apply_transform_block


def test_layered_pngtuber_motion_requires_explicit_runtime_feature_flags():
    source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    feature_block = source[
        source.index("layeredRuntimeFeatureEnabled("):
        source.index("        stateHasMotion(")
    ]
    state_motion_block = source[
        source.index("stateHasMotion(layerState)"):
        source.index("        stateFrameInfo(")
    ]
    current_motion_block = source[
        source.index("hasMotionLayersForCurrentState("):
        source.index("        startLayeredAnimationLoop(")
    ]
    frame_block = source[
        source.index("stateFrameInfo(layer, layerState, img"):
        source.index("        stateHasFrameAnimation(")
    ]
    draw_block = source[
        source.index("drawLayeredState(stateName"):
        source.index("        showTransientImage(")
    ]

    assert "return features[featureName] === true;" in feature_block
    assert "hasMotionLayersForCurrentState(stateName = this.state || 'idle')" in current_motion_block
    assert "this.shouldRenderLayer(layer, stateName)" in current_motion_block
    assert "this.layeredRuntimeFeatureEnabled('layer_motion')" in state_motion_block
    assert "this.layeredRuntimeFeatureEnabled('sprite_sheet_animation')" in state_motion_block
    assert "this.layeredRuntimeFeatureEnabled('sprite_sheet_animation')" in frame_block
    assert "const layerMotionEnabled = this.layeredRuntimeFeatureEnabled('layer_motion');" in draw_block
    assert "layerMotionEnabled ? this.motionValue(layerState.xAmp, layerState.xFrq" in draw_block
    assert "layerMotionEnabled ? this.motionValue(layerState.yAmp, layerState.yFrq" in draw_block
    assert "const wiggleDegrees = layerMotionEnabled" in draw_block
    assert "this.motionValue(layerState.wiggle_amp, layerState.wiggle_freq || layerState.rot_frq" in draw_block


def test_layered_pngtuber_caps_render_resolution_without_changing_logical_coordinates():
    source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    setup_block = source[
        source.index("        async setupLayeredAdapter()"):
        source.index("        hasBlinkLayers()")
    ]
    pointer_block = source[
        source.index("        layeredPointerForLayer("):
        source.index("        layeredPointerNeedsFrame(")
    ]
    draw_block = source[
        source.index("        drawLayeredState(stateName"):
        source.index("        showTransientImage(")
    ]

    assert "const PNGTUBER_LAYERED_CANVAS_MAX_RENDER_EDGE = 1024;" in source
    assert "PNGTUBER_LAYERED_CANVAS_MAX_RENDER_EDGE / Math.max(logicalWidth, logicalHeight)" in setup_block
    assert "this.layeredCanvasLogicalWidth = logicalWidth;" in setup_block
    assert "this.layeredCanvasLogicalHeight = logicalHeight;" in setup_block
    assert "this.layeredCanvasScaleX = renderWidth / logicalWidth;" in setup_block
    assert "this.layeredCanvasScaleY = renderHeight / logicalHeight;" in setup_block
    assert "const heightLimitedWidthVh = (maxHeightVh * logicalWidth) / logicalHeight;" in setup_block
    assert "canvas.style.width = `min(${logicalWidth}px, ${viewportWidthLimits})`;" in setup_block
    assert "canvas.style.height = 'auto';" in setup_block
    assert "Number(this.layeredCanvasLogicalWidth)" in pointer_block
    assert "Number(this.layeredCanvasLogicalHeight)" in pointer_block
    assert "ctx.clearRect(0, 0, canvas.width, canvas.height);" in draw_block
    assert "ctx.setTransform(renderScaleX, 0, 0, renderScaleY, 0, 0);" in draw_block


def test_layered_pngtuber_can_render_full_resolution_snapshot_without_resizing_runtime_canvas():
    source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    snapshot_block = source[
        source.index("        renderLayeredSnapshotCanvas("):
        source.index("        drawLayeredState(stateName")
    ]
    draw_block = source[
        source.index("        drawLayeredState(stateName"):
        source.index("        showTransientImage(")
    ]

    assert "document.createElement('canvas')" in snapshot_block
    assert "Number(this.layeredCanvasLogicalWidth)" in snapshot_block
    assert "Number(this.layeredCanvasLogicalHeight)" in snapshot_block
    assert "this.drawLayeredState(stateName, timestamp, {" in snapshot_block
    assert "scaleX: 1" in snapshot_block
    assert "scaleY: 1" in snapshot_block
    assert "return drawn ? canvas : null;" in snapshot_block
    assert "this.canvasElement =" not in snapshot_block
    assert "renderTarget?.canvas || this.canvasElement" in draw_block
    assert "renderTarget?.scaleX ?? this.layeredCanvasScaleX" in draw_block
    assert "renderTarget?.scaleY ?? this.layeredCanvasScaleY" in draw_block


def test_layered_pngtuber_alt_one_cycles_states_without_imported_hotkeys():
    source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    attach_block = source[
        source.index("attachLayeredHotkeys()"):
        source.index("        detachLayeredHotkeys()")
    ]
    handler_block = source[
        source.index("        handleLayeredHotkey(event) {"):
        source.index("        async setupLayeredAdapter()")
    ]
    cycle_hotkey_block = source[
        source.index("        isLayeredCycleHotkey(event) {"):
        source.index("        cycleLayeredState()")
    ]
    cycle_block = source[
        source.index("        cycleLayeredState() {"):
        source.index("        handleLayeredHotkey(event)")
    ]

    assert "this.getLayeredStateCount() <= 1" in attach_block
    assert "this.layeredMetadata.hotkeys" not in attach_block
    assert "isLayeredCycleHotkey(event)" in handler_block
    assert "cycleLayeredState()" in handler_block
    assert "event.preventDefault();" in handler_block
    assert "event.stopPropagation();" in handler_block
    assert "hotkeyMatchesEvent" not in handler_block
    assert "this.layeredMetadata.hotkeys" not in handler_block
    assert "setLayeredStateIndex(Number(matched.state_index)" not in handler_block
    assert "event.altKey && !event.ctrlKey && !event.metaKey && !event.shiftKey" in cycle_hotkey_block
    assert "event.key === '1' || event.code === 'Digit1' || event.keyCode === 49" in cycle_hotkey_block
    assert "this.getLayeredStateCount() <= 1" in cycle_block
    assert "const stateCount = this.getLayeredStateCount();" in cycle_block
    assert "this.setLayeredStateIndex((this.layeredStateIndex + 1) % stateCount" in cycle_block
    assert "source: 'alt-one-cycle-hotkey'" in cycle_block


def test_pngtuber_plus_imported_toggles_ignore_shift_modified_events():
    source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    toggle_block = source[
        source.index("layeredToggleEntriesForEvent(event) {"):
        source.index("        initializeLayeredToggleState(layers)")
    ]

    assert "if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return [];" in toggle_block


def test_layered_pngtuber_alt_two_toggles_imported_asset_action():
    source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    attach_block = source[
        source.index("attachLayeredHotkeys()"):
        source.index("        detachLayeredHotkeys()")
    ]
    handler_block = source[
        source.index("        handleLayeredHotkey(event) {"):
        source.index("        async setupLayeredAdapter()")
    ]
    asset_hotkey_block = source[
        source.index("        isLayeredAssetActionHotkey(event) {"):
        source.index("        hasLayeredAssetActions()")
    ]
    asset_toggle_block = source[
        source.index("        togglePrimaryLayeredAssetAction() {"):
        source.index("        handleLayeredHotkey(event)")
    ]
    render_block = source[
        source.index("        shouldRenderLayer(layer, stateName) {"):
        source.index("        layerStateForCurrentIndex(layer)")
    ]

    assert "hasLayeredAssetActions()" in attach_block
    assert "isLayeredAssetActionHotkey(event)" in handler_block
    assert "togglePrimaryLayeredAssetAction()" in handler_block
    assert "event.key === '2' || event.code === 'Digit2' || event.keyCode === 50" in asset_hotkey_block
    assert "this.layeredAssetVisibility.set(String(spriteId), true);" in asset_toggle_block
    assert "this.layeredAssetVisibility.set(String(spriteId), false);" in asset_toggle_block
    assert "this.restartLayeredAnimationLoop();" in asset_toggle_block
    assert "source: 'alt-two-asset-hotkey'" in asset_toggle_block
    assert "const assetVisibility = this.layeredAssetVisibility.get(String(layer.sprite_id));" in render_block
    assert "const assetForcedVisible = assetVisibility === true;" in render_block
    assert "if (assetVisibility === false) return false;" in render_block
    assert "if (layer.inactive_asset_ancestor && !assetForcedVisible) return false;" in render_block
    assert "if (layerState.visible === false && !assetForcedVisible) return false;" in render_block


def test_layered_pngtuber_uses_default_mouth_state_under_emotions():
    source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    state_block = source[
        source.index("layerStateForRender(layer, stateName = this.state || 'idle')"):
        source.index("        isLayeredRemixModel()")
    ]
    render_block = source[
        source.index("        shouldRenderLayer(layer, stateName) {"):
        source.index("        layerStateForCurrentIndex(layer)")
    ]

    assert "const layerState = this.layerStateForRender(layer, stateName);" in render_block
    assert "this.isLayeredPlusModel() || this.layerStateHasTalkingMouth(currentState)" in state_block
    assert "const defaultState = states[0] || layer.state || {};" in state_block
    assert "return this.layerStateHasTalkingMouth(defaultState) ? defaultState : currentState;" in state_block


def test_layered_pngtuber_draw_order_uses_imported_effective_z_index():
    source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    helper_block = source[
        source.index("        layerDrawZIndex(layer, layerState = null) {"):
        source.index("        drawLayeredState(stateName")
    ]
    draw_block = source[
        source.index("        drawLayeredState(stateName"):
        source.index("        showTransientImage(")
    ]
    debug_block = source[
        source.index("        renderedLayerDebugInfo(stateName)"):
        source.index("        getDebugState()")
    ]

    assert "layerState.effective_z_index" in helper_block
    assert "layer.effective_zindex" in helper_block
    assert "layerState.z_index" in helper_block
    assert "layer.zindex" in helper_block
    assert "this.fallbackLayerDrawZIndex(layer, layerState)" in helper_block
    assert "fallbackLayerDrawZIndex(layer, layerState = null)" in helper_block
    assert "_fallbackLayersBySpriteIdSource !== layers" in helper_block
    assert "const layersBySpriteId = this._fallbackLayersBySpriteId;" in helper_block
    assert "const layersBySpriteId = new Map();" not in helper_block
    assert "currentState.z_as_relative ?? current.z_as_relative" in helper_block
    assert "this.compareLayerRenderOrder(a, b, stateName)" in draw_block
    assert "this.compareLayerRenderOrder(a, b, stateName)" in debug_block


def test_layered_pngtuber_keeps_stable_breathing_without_raw_layer_motion():
    source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    constructor_block = source[
        source.index("constructor(containerId = 'pngtuber-container')"):
        source.index("        ensureContainer()")
    ]
    timers_block = source[
        source.index("clearLayeredTimers()"):
        source.index("        attachLayeredHotkeys()")
    ]
    restart_block = source[
        source.index("restartLayeredAnimationLoop()"):
        source.index("        motionValue(")
    ]
    animation_loop_block = source[
        source.index("startLayeredAnimationLoop(options = {})"):
        source.index("        layeredBreathingEnabled()")
    ]
    breathing_enabled_block = source[
        source.index("layeredBreathingEnabled()"):
        source.index("        currentLayeredBreathingTransform(")
    ]
    breathing_transform_block = source[
        source.index("currentLayeredBreathingTransform("):
        source.index("        startLayeredBreathingLoop(")
    ]
    breathing_loop_block = source[
        source.index("        startLayeredBreathingLoop("):
        source.index("        motionValue(")
    ]
    apply_transform_block = source[
        source.index("applyTransform(timestamp = performance.now())"):
        source.index("        getActiveLayoutFields()")
    ]

    assert "this.layeredBreathingFrame = null;" in constructor_block
    assert "this.layeredBreathingStart = 0;" in constructor_block
    assert "this.stopLayeredBreathingLoop();" in timers_block
    assert "this.startLayeredAnimationLoop();" in restart_block
    assert "this.startLayeredBreathingLoop();" in animation_loop_block
    assert "features.layered_breathing === false" in breathing_enabled_block
    assert "return this.isLayeredActive();" in breathing_enabled_block
    assert "if (!this.layeredBreathingStart) return { y: 0, scaleX: 1, scaleY: 1 };" in breathing_transform_block
    assert "this.layeredBreathingStart = timestamp;" not in breathing_transform_block
    assert "scaleY" in breathing_transform_block
    assert "scaleX" in breathing_transform_block
    assert "this.applyAnimationTransform(timestamp);" in breathing_loop_block
    assert "const breathing = this.currentLayeredBreathingTransform(timestamp);" in apply_transform_block
    assert "bounce.y + breathing.y" in apply_transform_block
    assert "bounce.scaleX * breathing.scaleX" in apply_transform_block
    assert "bounce.scaleY * breathing.scaleY" in apply_transform_block


def test_audio_playback_routes_lip_sync_to_pngtuber_when_active():
    source = APP_AUDIO_PLAYBACK_PATH.read_text(encoding="utf-8")
    model_type_block = source[
        source.index("function getActiveAvatarModelType()"):
        source.index("    function clearPendingAudioMetaStallTimer()")
    ]
    stop_block = source[
        source.index("function stopActiveLipSync()"):
        source.index("    function maybeFinalizeAssistantSpeech(")
    ]
    schedule_block = source[
        source.index("function scheduleAudioChunks()"):
        source.index("                    var scheduledStartTime = S.nextChunkTime;")
    ]

    assert "pngtuber-container" in model_type_block
    assert "modelType === 'pngtuber'" in model_type_block
    assert "return 'pngtuber';" in model_type_block
    assert "activeModelType === 'pngtuber'" in schedule_block
    assert "typeof window.pngtuberManager.startLipSync === 'function'" in schedule_block
    assert "window.pngtuberManager.startLipSync(S.globalAnalyser)" in schedule_block
    assert "S.lipSyncActive = true;" in schedule_block
    assert "activeModelType === 'pngtuber'" in stop_block
    assert "typeof window.pngtuberManager.stopLipSync === 'function'" in stop_block
    assert "window.pngtuberManager.stopLipSync()" in stop_block
    assert "S.lipSyncActive = false;" in stop_block


def test_pngtuber_analyser_lip_sync_uses_hysteresis_and_timer_mutex():
    source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    constructor_block = source[
        source.index("constructor(containerId = 'pngtuber-container')"):
        source.index("        ensureContainer()")
    ]
    lip_sync_block = source[
        source.index("startLipSync(analyser)"):
        source.index("        stopLipSync()")
    ]
    stop_lip_sync_block = source[
        source.index("stopLipSync()"):
        source.index("        scheduleSpeakingMouthFrame()")
    ]
    schedule_block = source[
        source.index("scheduleSpeakingMouthFrame()"):
        source.index("        startSpeakingMouthAnimation()")
    ]

    assert "this.lipSyncFrame = null;" in constructor_block
    assert "this.lipSyncMouthOpen = 0;" in constructor_block
    assert "this.lipSyncMouthState = false;" in constructor_block
    assert "this.lipSyncLastStateChangeAt = 0;" in constructor_block
    assert "this.lipSyncNextPulseAt = 0;" in constructor_block
    assert "this.lipSyncPulseCloseAt = 0;" in constructor_block
    assert "clearTimeout(this.speakingMouthTimer);" in lip_sync_block
    assert "this.startSpeakingMouthAnimation();" in lip_sync_block
    assert "const sampleSize = Math.max(32, Number(analyser.fftSize) || 2048);" in lip_sync_block
    assert "frequencyBinCount" not in lip_sync_block
    assert "analyser.getByteTimeDomainData(dataArray);" in lip_sync_block
    assert "Math.sqrt(sum / dataArray.length)" in lip_sync_block
    assert "const activeThreshold = 0.16;" in lip_sync_block
    assert "const quietThreshold = 0.07;" in lip_sync_block
    assert "const pulseOpenMs = Math.max(42, Math.min(72, 42 + this.lipSyncMouthOpen * 34));" in lip_sync_block
    assert "const pulseGapMs = Math.max(45, Math.min(135, 135 - this.lipSyncMouthOpen * 90));" in lip_sync_block
    assert "timestamp >= this.lipSyncPulseCloseAt" in lip_sync_block
    assert "timestamp >= this.lipSyncNextPulseAt" in lip_sync_block
    assert "this.lipSyncPulseCloseAt = timestamp + pulseOpenMs;" in lip_sync_block
    assert "this.lipSyncNextPulseAt = timestamp + pulseGapMs;" in lip_sync_block
    assert "this.applyLipSyncMouthState(true);" in lip_sync_block
    assert "this.applyLipSyncMouthState(false);" in lip_sync_block
    assert "this.lipSyncFrame = requestAnimationFrame(tick);" in lip_sync_block
    assert "cancelAnimationFrame(this.lipSyncFrame);" in stop_lip_sync_block
    assert "this.lipSyncFrame = null;" in stop_lip_sync_block
    assert "this.lipSyncMouthOpen = 0;" in stop_lip_sync_block
    assert "this.lipSyncNextPulseAt = 0;" in stop_lip_sync_block
    assert "this.lipSyncPulseCloseAt = 0;" in stop_lip_sync_block
    assert "if (this.lipSyncFrame) return;" in schedule_block
    assert "if (!this.isSpeaking || this.lipSyncFrame) return;" in schedule_block


def test_pngtuber_lip_sync_state_changes_use_layered_safe_mouth_pulses():
    source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    helper_block = source[
        source.index("applyLipSyncMouthState(open)"):
        source.index("        startLipSync(analyser)")
    ]

    assert "if (this.lipSyncMouthState === open && this.speakingMouthOpen === open) return;" in helper_block
    assert "this.lipSyncMouthState = open;" in helper_block
    assert "this.speakingMouthOpen = open;" in helper_block
    assert "this.startSpeakingBounceAnimation();" in helper_block
    assert "this.setState(open ? 'talking' : 'idle', { restartLayeredAnimation: false });" in helper_block


def test_pngtuber_debug_state_exposes_lip_sync_timer():
    source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    debug_block = source[
        source.index("getDebugState()"):
        source.index("        setSpeaking(isSpeaking)")
    ]

    assert "lipSyncFrame: !!this.lipSyncFrame" in debug_block


def test_pngtuber_talking_hop_moves_whole_avatar_while_speaking():
    source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    constructor_block = source[
        source.index("constructor(containerId = 'pngtuber-container')"):
        source.index("        ensureContainer()")
    ]
    hop_transform_block = source[
        source.index("currentTalkingHopTransform("):
        source.index("        startTalkingHopAnimation(")
    ]
    hop_loop_block = source[
        source.index("startTalkingHopAnimation("):
        source.index("        applyLipSyncMouthState(open)")
    ]
    stop_block = source[
        source.index("stopTalkingHopAnimation()"):
        source.index("        applyLipSyncMouthState(open)")
    ]
    apply_transform_block = source[
        source.index("applyTransform(timestamp = performance.now())"):
        source.index("        getActiveLayoutFields()")
    ]
    start_block = source[
        source.index("        startSpeakingMouthAnimation() {"):
        source.index("        stopSpeakingMouthAnimation()")
    ]
    lip_sync_block = source[
        source.index("startLipSync(analyser)"):
        source.index("        stopLipSync()")
    ]
    stop_speaking_block = source[
        source.index("stopSpeakingMouthAnimation()"):
        source.index("        renderedLayerCountForState(")
    ]
    debug_block = source[
        source.index("getDebugState()"):
        source.index("        setSpeaking(isSpeaking)")
    ]

    assert "this.talkingHopFrame = null;" in constructor_block
    assert "this.talkingHopStart = 0;" in constructor_block
    assert "this.talkingHopAmplitude = 0;" in constructor_block
    assert "this.talkingHopPeriodMs = 0;" in constructor_block
    assert "return { y: 0, scaleX: 1, scaleY: 1 };" in hop_transform_block
    assert "const wave = Math.sin(progress * Math.PI);" in hop_transform_block
    assert "y: -this.talkingHopAmplitude * wave" in hop_transform_block
    assert "scaleY: 1 + 0.004 * wave" in hop_transform_block
    assert "if (this.talkingHopFrame || !this.isSpeaking || !this.isLayeredActive()) return;" in hop_loop_block
    assert "this.talkingHopAmplitude = 4.5;" in hop_loop_block
    assert "this.talkingHopPeriodMs = 260;" in hop_loop_block
    assert "this.applyAnimationTransform(timestamp);" in hop_loop_block
    assert "this.talkingHopFrame = requestAnimationFrame(tick);" in hop_loop_block
    assert "cancelAnimationFrame(this.talkingHopFrame);" in stop_block
    assert "this.talkingHopFrame = null;" in stop_block
    assert "this.talkingHopStart = 0;" in stop_block
    assert "const talkingHop = this.currentTalkingHopTransform(timestamp);" in apply_transform_block
    assert "bounce.y + breathing.y + talkingHop.y" in apply_transform_block
    assert "bounce.scaleX * breathing.scaleX * talkingHop.scaleX" in apply_transform_block
    assert "bounce.scaleY * breathing.scaleY * talkingHop.scaleY" in apply_transform_block
    assert "this.startTalkingHopAnimation();" in start_block
    assert "this.startTalkingHopAnimation();" in lip_sync_block
    assert "this.stopTalkingHopAnimation();" in stop_speaking_block
    assert "talkingHopFrame: !!this.talkingHopFrame" in debug_block


def test_pngtuber_animation_loops_throttle_overlay_position_updates():
    source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    constructor_block = source[
        source.index("constructor(containerId = 'pngtuber-container')"):
        source.index("        ensureContainer()")
    ]
    helper_block = source[
        source.index("updateOverlayPositionsForAnimation("):
        source.index("        currentLayeredBreathingTransform(")
    ]
    breathing_loop_block = source[
        source.index("        startLayeredBreathingLoop() {"):
        source.index("        stopLayeredBreathingLoop()")
    ]
    bounce_loop_block = source[
        source.index("        startSpeakingBounceAnimation() {"):
        source.index("        currentTalkingHopTransform(")
    ]
    hop_loop_block = source[
        source.index("        startTalkingHopAnimation() {"):
        source.index("        stopTalkingHopAnimation()")
    ]

    assert "this.lastOverlayPositionUpdateAt = 0;" in constructor_block
    assert "this.lastAnimationTransformAt = 0;" in constructor_block
    assert "const minIntervalMs = 120;" in helper_block
    assert "timestamp - this.lastOverlayPositionUpdateAt < minIntervalMs" in helper_block
    assert "this.updateLockIconPosition();" in helper_block
    assert "this.updateFloatingButtonsPosition();" not in helper_block
    assert "applyAnimationTransform(timestamp = performance.now())" in helper_block
    assert "if (this.lastAnimationTransformAt === timestamp) return;" in helper_block
    assert "this.lastAnimationTransformAt = timestamp;" in helper_block
    assert "this.applyTransform(timestamp);" in helper_block
    assert "this.applyAnimationTransform(timestamp);" in breathing_loop_block
    assert "this.applyAnimationTransform(timestamp);" in bounce_loop_block
    assert "this.applyAnimationTransform(timestamp);" in hop_loop_block
    assert "this.updateOverlayPositionsForAnimation(timestamp);" in breathing_loop_block
    assert "this.updateOverlayPositionsForAnimation(timestamp);" in bounce_loop_block
    assert "this.updateOverlayPositionsForAnimation(timestamp);" in hop_loop_block
    assert "this.updateLockIconPosition();" not in breathing_loop_block
    assert "this.updateLockIconPosition();" not in bounce_loop_block
    assert "this.updateLockIconPosition();" not in hop_loop_block


def test_pngtuber_floating_controls_auto_hide_like_live2d_without_touching_other_models():
    source = PNGTUBER_CORE_PATH.read_text(encoding="utf-8")
    setup_block = source[
        source.index("PNGTuberManager.prototype.setupFloatingButtons = function()"):
        source.index("            window.dispatchEvent(new CustomEvent('live2d-floating-buttons-ready'));")
    ]
    lock_block = source[
        source.index("        updateLockIconPosition()"):
        source.index("        async resolveCurrentLanlanName()")
    ]

    assert "this._pngtuberFloatingControlsVisible = true;" in setup_block
    assert "const hideFloatingControls = () => {" in setup_block
    assert "const showFloatingControls = () => {" in setup_block
    assert "const startHideTimer = (delay = 1000) => {" in setup_block
    assert "const schedulePointerEvaluation = () => {" in setup_block
    assert "this._pngtuberPointerEvaluateFrame = requestAnimationFrame(() => {" in setup_block
    assert "if (window.isInTutorial === true) return;" in setup_block
    assert "buttonsContainer.addEventListener('mouseenter', markControlsHover);" in setup_block
    assert "buttonsContainer.addEventListener('mouseleave', unmarkControlsHover);" in setup_block
    assert "lockIcon.addEventListener('mouseenter', markControlsHover);" in setup_block
    assert "lockIcon.addEventListener('mouseleave', unmarkControlsHover);" in setup_block
    assert "window.addEventListener('pointermove', handlePointerMove, { passive: true });" in setup_block
    assert "window.addEventListener('focus', handleWindowFocus);" in setup_block
    assert "window.addEventListener('blur', handleWindowBlur);" in setup_block
    assert "document.addEventListener('mouseenter', handleDocumentMouseEnter, true);" in setup_block
    assert "document.addEventListener('mouseleave', handleDocumentMouseLeave, true);" in setup_block
    assert "this.image.addEventListener('pointerenter', handleImagePointerEnter);" in setup_block
    assert "this.image.addEventListener('pointerleave', handleImagePointerLeave);" in setup_block
    assert "this.image.addEventListener('mouseover', handleImagePointerEnter);" in setup_block
    assert "this._lastPngtuberPointerX = null;" in setup_block
    handle_pointer_block = setup_block[
        setup_block.index("const handlePointerMove = (event) => {"):
        setup_block.index("const handleImagePointerEnter = () => showFloatingControls();")
    ]
    assert "schedulePointerEvaluation();" in handle_pointer_block
    assert "shouldKeepFloatingControlsVisible()" not in handle_pointer_block
    assert "showFloatingControls();" not in handle_pointer_block
    assert "startHideTimer();" not in handle_pointer_block
    assert "this._pngtuberFloatingControlsVisible === false" in lock_block
    assert "'live2d-lock-icon'" not in setup_block
    assert "'vrm-lock-icon'" not in setup_block
    assert "'mmd-lock-icon'" not in setup_block


def test_apply_emotion_prefers_pngtuber_runtime_when_active():
    source = APP_BUTTONS_PATH.read_text(encoding="utf-8")
    apply_block = source[
        source.index("mod.applyEmotion = function applyEmotion(emotion)"):
        source.index("    window.applyEmotion = mod.applyEmotion;")
    ]

    assert "window.lanlan_config && window.lanlan_config.model_type" in apply_block
    assert "modelType === 'pngtuber'" in apply_block
    assert "window.pngtuberManager.setEmotion(emotion)" in apply_block
    assert "window.LanLan1.setEmotion(emotion)" in apply_block
