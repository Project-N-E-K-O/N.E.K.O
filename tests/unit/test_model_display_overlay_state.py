import json
import shutil
from pathlib import Path

import pytest

from tests.node_harness import run_node_script


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("model_type", "hidden", "pending_return_config"),
    [(model, hidden, False) for model in ("vrm", "mmd", "pngtuber") for hidden in (False, True)]
    + [("pngtuber", False, True)],
)
@pytest.mark.parametrize("mobile", [False, True])
def test_model_display_overlay_state(model_type, hidden, pending_return_config, mobile):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for model display tests")

    script = "const scenario = " + json.dumps({
        "modelType": model_type, "hidden": hidden, "mobile": mobile,
        "pendingReturnConfig": pending_return_config,
    }) + ";\n" + r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const elements = new Map();
const writes = [];
const calls = { load: 0, setup: 0, unlock: 0 };

function element(id, properties = {}) {
  const classes = new Set();
  const style = new Proxy({
    ...properties,
    setProperty(key, value) { this[key] = value; },
    removeProperty(key) { delete this[key]; },
  }, {
    set(target, key, value) {
      writes.push({ id, key, value });
      target[key] = value;
      return true;
    },
  });
  const node = {
    id, style, dataset: {}, offsetWidth: 400,
    classList: {
      contains: key => classes.has(key),
      add: key => classes.add(key),
      remove: key => classes.delete(key),
    },
    removeAttribute() {}, querySelectorAll: () => [],
    addEventListener() {}, removeEventListener() {},
  };
  elements.set(id, node);
  return node;
}

const prefix = scenario.modelType;
const container = element(`${prefix}-container`, {
  display: scenario.hidden ? 'none' : 'block', visibility: 'visible',
});
if (scenario.hidden) container.classList.add('hidden');
const canvas = element(`${prefix}-canvas`, { opacity: '1' });
const toolbar = element(`${prefix}-floating-buttons`, {
  display: 'flex', visibility: 'visible', opacity: '1', top: '71px',
});
toolbar.dataset.active = 'true';
const lock = element(`${prefix}-lock-icon`, {
  display: 'flex', visibility: 'visible', opacity: '1',
});
const popup = element(`${prefix}-popup-mic`, { display: 'flex', opacity: '1' });
const manager = {
  currentModel: { destroyed: false }, image: {}, config: {}, isLocked: false,
  show() { container.classList.remove('hidden'); container.style.display = 'block'; },
  setupFloatingButtons() {
    calls.setup++;
    element(`${prefix}-floating-buttons`, { display: 'flex' });
    element(`${prefix}-lock-icon`, { display: 'flex' });
  },
  core: { setLocked() { calls.unlock++; toolbar.style.display = 'flex'; } },
};
const document = {
  getElementById: id => elements.get(id) || null,
  querySelectorAll: () => [],
};
const config = { lanlan_name: 'test', model_type: prefix };
const errors = [];
const window = {
  appUi: {}, __appUiParts: { mod: {} }, lanlan_config: config,
  innerWidth: scenario.mobile ? 400 : 1200, isMobileWidth: () => scenario.mobile,
};
window[`${prefix}Manager`] = manager;
window.loadPNGTuberAvatar = async config => {
  calls.load++;
  calls.loadedConfig = config;
  manager.setupFloatingButtons();
};
const context = vm.createContext({
  window, document, lanlan_config: config,
  console: { log() {}, warn() {}, error: (...args) => errors.push(args) },
  getComputedStyle: node => ({ display: node.style.display || 'block' }),
  isMobileViewport: () => scenario.mobile,
  setTimeout: () => 1, clearTimeout() {},
  fetch: async () => ({ ok: true, json: async () => ({ '猫娘': { test: config } }) }),
});
vm.runInContext(fs.readFileSync('static/app/app-ui/model-display.js', 'utf8'), context);
const ui = window.__appUiParts;
if (scenario.pendingReturnConfig) ui.pendingPngtuberReturnConfig = { offset_x: 73 };
ui.ensureModelViewportReadyBeforeShowCurrentModel = async () => ({ ready: true });
ui.consumeModelReturnEnterRect = () => null;
ui.prepareModelReturnContainer = node => {
  node.classList.remove('hidden');
  node.classList.remove('minimized');
  node.style.display = 'block';
};

(async () => {
  await ui.showCurrentModel();
  assert.deepEqual(errors, [], 'model display must not fall back after an exception');
  assert.equal(container.style.display, 'block');
  assert.equal(container.classList.contains('hidden'), false);
  if (scenario.hidden || scenario.pendingReturnConfig) {
    if (prefix === 'pngtuber') assert.equal(calls.load, 1);
    if (scenario.pendingReturnConfig) {
      assert.equal(calls.loadedConfig.offset_x, 73);
      assert.equal(ui.pendingPngtuberReturnConfig, null);
    }
    assert.ok(document.getElementById(`${prefix}-floating-buttons`));
  } else {
    assert.equal(calls.load, 0, 'a visible model must not be reloaded');
    assert.equal(calls.setup, 0, 'the existing toolbar must not be rebuilt');
    assert.equal(calls.unlock, 0, 'showing an already visible model must preserve its lock state');
    assert.equal(document.getElementById(`${prefix}-floating-buttons`), toolbar);
    assert.equal(document.getElementById(`${prefix}-lock-icon`), lock);
    assert.equal(document.getElementById(`${prefix}-popup-mic`), popup);
    assert.equal(toolbar.dataset.active, 'true');
    assert.equal(toolbar.style.top, '71px');
    assert.equal(popup.style.display, 'flex');
    assert.deepEqual(writes.filter(({ id }) => id === toolbar.id || id === lock.id), [],
      'visible overlay controls must not be hidden and shown again');
    assert.equal(writes.some(({ id, key, value }) =>
      id === canvas.id && key === 'opacity' && value === '0'), false,
      'an already visible canvas must not replay its fade-in');
  }
  process.stdout.write('model display overlay state OK\n');
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    result = run_node_script(
        node, script, cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert "model display overlay state OK" in result.stdout
