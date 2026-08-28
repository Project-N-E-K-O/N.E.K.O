import json
import re
import shutil
from pathlib import Path

import pytest

from tests.node_harness import run_node_script


def read_source(relative_path: str) -> str:
    return Path(relative_path).read_text(encoding="utf-8")


def test_existing_model_drag_hit_areas_are_not_expanded():
    live2d = read_source("static/live2d/live2d-interaction.js")
    live2d_drag = live2d.split(
        "Live2DManager.prototype.setupDragAndDrop = function (model)", 1
    )[1].split("Live2DManager.prototype.setupWheelZoom", 1)[0]
    assert not re.search(r"^\s*stage\.hitArea\s*=", live2d_drag, re.MULTILINE)
    assert "modelManagerBackgroundDragEnabled" not in live2d_drag

    vrm = read_source("static/vrm/vrm-interaction.js")
    mmd = read_source("static/mmd/mmd-interaction.js")
    assert "if (!this._hitTestModel(e.clientX, e.clientY))" in vrm
    assert "if (!this._hitTestModel(e.clientX, e.clientY)) return;" in mmd
    assert "_isModelManagerBackgroundPanEnabled" not in vrm
    assert "_isModelManagerBackgroundPanEnabled" not in mmd


def test_background_drag_is_a_separate_model_manager_controller():
    source = read_source("static/js/model_manager/background-model-drag.js")
    template = read_source("templates/model_manager.html")

    assert "/static/js/model_manager/background-model-drag.js" in template
    assert "document.addEventListener('pointerdown', onPointerDown, true);" in source
    assert "const adapter = createBackgroundAdapter(event);" in source
    assert "if (!adapter) return;" in source
    assert "isPointOnLive2DModel(manager" in source
    assert "interaction._hitTestModel(event.clientX, event.clientY)" in source
    assert "event.target !== container" in source
    assert "model.x += deltaX * point.scaleX;" in source
    assert "interaction._moveModelCenterToWindowPoint(" in source
    assert "manager.setActiveOffsets(" in source
    assert "manager.beginModelManagerPositionEditing();" in source
    assert "window.stageModelManagerPNGTuberPlacement(manager.config);" in source


def test_pngtuber_background_surface_does_not_reuse_image_drag_handler():
    source = read_source("static/pngtuber-core.js")

    assert "modelManagerPage ? 'auto' : 'none'" in source
    assert "isModelManagerPage() ? 'auto' : 'none'" in source
    assert "_boundBackgroundDragStart" not in source
    assert "captureTarget" not in source


def test_live2d_background_drag_moves_by_page_delta_without_stealing_model_hit():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for model-manager background drag tests")

    source = read_source("static/js/model_manager/background-model-drag.js")
    script = f"""
const assert = require('node:assert/strict');
const vm = require('node:vm');

const documentListeners = new Map();
const windowListeners = new Map();
const bodyClasses = new Set(['model-manager-page']);
const canvas = {{
  getBoundingClientRect() {{ return {{ left: 0, top: 0, width: 1000, height: 800 }}; }},
  setPointerCapture() {{}},
  releasePointerCapture() {{}},
}};
const model = {{
  destroyed: false,
  x: 100,
  y: 100,
  getBounds() {{
    return {{ left: this.x - 50, right: this.x + 50, top: this.y - 50, bottom: this.y + 50 }};
  }},
}};
let saves = 0;
const manager = {{
  currentModel: model,
  _isModelReadyForInteraction: true,
  isLocked: false,
  isFocusing: true,
  pixi_app: {{ view: canvas, renderer: {{ screen: {{ width: 1000, height: 800 }} }} }},
  _checkAndPerformSnap: async () => false,
  _savePositionAfterInteraction: async () => {{ saves += 1; }},
}};
const document = {{
  readyState: 'complete',
  body: {{
    classList: {{
      contains(name) {{ return bodyClasses.has(name); }},
      toggle(name, active) {{ if (active) bodyClasses.add(name); else bodyClasses.delete(name); }},
    }},
  }},
  addEventListener(name, handler) {{ documentListeners.set(name, handler); }},
  getElementById() {{ return null; }},
}};
const window = {{
  _modelManagerCurrentAvatarType: 'live2d',
  live2dManager: manager,
  addEventListener(name, handler) {{ windowListeners.set(name, handler); }},
  getComputedStyle() {{ return {{ display: 'block', visibility: 'visible' }}; }},
}};
const context = {{ console, document, window, Promise, Math }};
vm.runInNewContext({json.dumps(source)}, context);

function pointer(target, x, y) {{
  return {{
    target, clientX: x, clientY: y, pointerId: 7, button: 0, isPrimary: true,
    preventDefault() {{}}, stopPropagation() {{}},
  }};
}}

(async () => {{
  documentListeners.get('pointerdown')(pointer(canvas, 500, 500));
  windowListeners.get('pointermove')(pointer(canvas, 530, 520));
  assert.equal(model.x, 130);
  assert.equal(model.y, 120);
  windowListeners.get('pointerup')(pointer(canvas, 530, 520));
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(saves, 1);

  const modelX = model.x;
  const modelY = model.y;
  documentListeners.get('pointerdown')(pointer(canvas, model.x, model.y));
  windowListeners.get('pointermove')(pointer(canvas, model.x + 40, model.y + 40));
  assert.equal(model.x, modelX);
  assert.equal(model.y, modelY);
}})().catch(error => {{ console.error(error); process.exit(1); }});
"""
    run_node_script(node, script, check=True, cwd=Path.cwd())


def test_pngtuber_background_drag_renders_offsets_and_stages_manager_save():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for model-manager background drag tests")

    source = read_source("static/js/model_manager/background-model-drag.js")
    script = f"""
const assert = require('node:assert/strict');
const vm = require('node:vm');

const documentListeners = new Map();
const windowListeners = new Map();
const bodyClasses = new Set(['model-manager-page']);
const container = {{
  classList: {{ contains() {{ return false; }} }},
  style: {{ display: 'block', visibility: 'visible' }},
  setPointerCapture() {{}},
  releasePointerCapture() {{}},
}};
let renderedPlacement = null;
let stagedConfig = null;
let externalDrags = 0;
let snapCalls = 0;
const manager = {{
  container,
  image: {{}},
  isLocked: false,
  config: {{ offset_x: 100, offset_y: 50 }},
  editing: false,
  beginExternalPositionDrag() {{ externalDrags += 1; return externalDrags; }},
  isExternalPositionDragCurrent(sequence) {{ return sequence === externalDrags; }},
  async snapModelIntoScreen() {{
    snapCalls += 1;
    this.config.offset_x = 15;
    this.config.offset_y = 10;
    return true;
  }},
  beginModelManagerPositionEditing() {{
    if (!this.editing) this.setActiveOffsets(0, 0);
    this.editing = true;
  }},
  getActivePlacement() {{
    return {{ offsetX: this.config.offset_x, offsetY: this.config.offset_y }};
  }},
  setActiveOffsets(x, y) {{ this.config.offset_x = x; this.config.offset_y = y; }},
  applyTransform() {{
    renderedPlacement = this.editing
      ? {{ x: this.config.offset_x, y: this.config.offset_y }}
      : {{ x: 0, y: 0 }};
  }},
  isLayeredActive() {{ return false; }},
  syncGlobalConfig() {{}},
  saveCurrentConfig() {{ throw new Error('model-manager drag must not use runtime auto-save'); }},
}};
const document = {{
  readyState: 'complete',
  body: {{
    classList: {{
      contains(name) {{ return bodyClasses.has(name); }},
      toggle(name, active) {{ if (active) bodyClasses.add(name); else bodyClasses.delete(name); }},
    }},
  }},
  addEventListener(name, handler) {{ documentListeners.set(name, handler); }},
  getElementById() {{ return null; }},
}};
const window = {{
  _modelManagerCurrentAvatarType: 'pngtuber',
  pngtuberManager: manager,
  stageModelManagerPNGTuberPlacement(config) {{ stagedConfig = {{ ...config }}; }},
  addEventListener(name, handler) {{ windowListeners.set(name, handler); }},
  getComputedStyle(element) {{ return element.style; }},
}};
const context = {{ console, document, window, Promise, Math }};
vm.runInNewContext({json.dumps(source)}, context);

function pointer(x, y) {{
  return {{
    target: container, clientX: x, clientY: y, pointerId: 11,
    button: 0, isPrimary: true,
    preventDefault() {{}}, stopPropagation() {{}},
  }};
}}

(async () => {{
  documentListeners.get('pointerdown')(pointer(400, 300));
  assert.equal(externalDrags, 0, 'pointerdown alone must not cancel an active snap');
  windowListeners.get('pointermove')(pointer(430, 320));
  assert.equal(externalDrags, 1);
  assert.deepEqual(renderedPlacement, {{ x: 30, y: 20 }});
  windowListeners.get('pointerup')(pointer(430, 320));
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(snapCalls, 1);
  assert.equal(stagedConfig.offset_x, 15);
  assert.equal(stagedConfig.offset_y, 10);

  stagedConfig = null;
  snapCalls = 0;
  documentListeners.get('pointerdown')(pointer(400, 300));
  windowListeners.get('pointermove')(pointer(430, 320));
  externalDrags += 1; // A newer position interaction invalidates this completion.
  windowListeners.get('pointerup')(pointer(430, 320));
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(snapCalls, 0);
  assert.equal(stagedConfig, null);
}})().catch(error => {{ console.error(error); process.exit(1); }});
"""
    run_node_script(node, script, check=True, cwd=Path.cwd())


def test_pngtuber_manager_page_stages_dragged_config_for_explicit_save():
    core = read_source("static/pngtuber-core.js")
    controller = read_source("static/js/model_manager/page-controller.js")

    render_block = core.split("getRenderPlacement(placement) {", 1)[1].split(
        "setActiveScale(nextScale)", 1
    )[0]
    stage_block = controller.split(
        "function stageModelManagerPNGTuberPlacement(runtimeConfig) {", 1
    )[1].split("async function saveModelToCharacter", 1)[0]

    assert "!this._modelManagerUseCurrentPlacement" in render_block
    assert "beginModelManagerPositionEditing()" in core
    assert "const renderPlacement = this.getRenderPlacement(this.getActivePlacement());" in core
    assert "this.setActiveOffsets(renderPlacement.offsetX, renderPlacement.offsetY);" in core
    assert "this._modelManagerUseCurrentPlacement = false;" in core
    assert "currentModelInfo.pngtuber = mergePNGTuberConfigForSave(" in stage_block
    assert "window.hasUnsavedChanges = true;" in stage_block
    assert "savePositionBtn.disabled = false;" in stage_block
    assert "window.stageModelManagerPNGTuberPlacement" in stage_block
