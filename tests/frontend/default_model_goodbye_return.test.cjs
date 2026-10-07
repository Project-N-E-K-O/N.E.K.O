// Copyright 2025-2026 Project N.E.K.O. Team
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

// pytest's shared Node launcher copies this source to a temporary directory.
// Fall back to its cwd so direct Node runs and CI resolve the same real files.
const fileRoot = path.resolve(__dirname, '..', '..');
const projectRoot = fs.existsSync(path.join(fileRoot, 'static')) ? fileRoot : process.cwd();
const surfaceSource = fs.readFileSync(
  path.join(projectRoot, 'static/app/app-ui/surface-floating-controls.js'),
  'utf8',
);
const resetSource = fs.readFileSync(
  path.join(projectRoot, 'static/app/app-interpage/listeners-and-api.js'),
  'utf8',
);

function extractResetBlock() {
  const start = resetSource.indexOf("var DEFAULT_LIVE2D_MODEL_NAME = 'yui-lolita';");
  const end = resetSource.indexOf('// Public API', start);
  assert.notEqual(start, -1, 'default-model reset declaration must exist');
  assert.notEqual(end, -1, 'default-model reset block must have a stable end marker');
  return `${resetSource.slice(start, end)}\nwindow.__testResetToDefaultModel = resetToDefaultModel;`;
}

function extractCanonicalReturnBlock() {
  const start = surfaceSource.indexOf('function restoreReturnBallAfterBlockedModelViewport(event)');
  const end = surfaceSource.indexOf('// 统一监听各模型类型的回来事件', start);
  assert.notEqual(start, -1, 'return viewport recovery helper must exist');
  assert.notEqual(end, -1, 'canonical return handler must have a stable end marker');
  return `${surfaceSource.slice(start, end)}
    window.addEventListener('live2d-return-click', handleReturnClick);
    window.addEventListener('vrm-return-click', handleReturnClick);
    window.addEventListener('mmd-return-click', handleReturnClick);
    window.addEventListener('pngtuber-return-click', handleReturnClick);`;
}

function createHarness({
  modelType = 'live2d',
  subType = '',
  goodbyeActive = true,
  visibleReturnType = '',
} = {}) {
  const listeners = new Map();
  const dispatched = [];
  const timeline = [];
  let active = goodbyeActive;
  let visibleContainer = null;
  let transitionDirection = '';
  let transitionPromise = null;
  let viewportCheckCount = 0;
  let returnBallShowCount = 0;
  let returnBallRevealCount = 0;
  const testConsole = { log() {}, warn() {}, error() {} };

  const makeReturnContainer = (type) => ({
    id: `${type}-return-button-container`,
    style: { display: 'block' },
    getBoundingClientRect() {
      return { left: 12, top: 24, width: 80, height: 80 };
    },
  });
  const setVisibleReturnType = (type) => {
    visibleContainer = type ? makeReturnContainer(type) : null;
  };
  setVisibleReturnType(visibleReturnType);

  const parts = {
    mod: {},
    getVisibleIdleReturnBallContainer() {
      return visibleContainer;
    },
    isNekoModelCatTransitionActive(direction) {
      return direction ? transitionDirection === direction : !!transitionDirection;
    },
    toNekoVirtualTransitionRect(rect) {
      return rect;
    },
  };

  const document = {
    querySelector(selector) {
      if (!selector.includes('-return-button-container')) return null;
      return visibleContainer;
    },
    getElementById(id) {
      return visibleContainer && visibleContainer.id === id ? visibleContainer : null;
    },
  };

  const window = {
    appUi: {},
    __appUiParts: parts,
    innerWidth: 1280,
    innerHeight: 720,
    lanlan_config: {
      lanlan_name: '测试角色',
      model_type: modelType,
      live3d_sub_type: subType,
    },
    isNekoGoodbyeModeActive: () => active,
    addEventListener(type, listener) {
      const bucket = listeners.get(type) || [];
      bucket.push(listener);
      listeners.set(type, bucket);
    },
    removeEventListener(type, listener) {
      const bucket = listeners.get(type) || [];
      listeners.set(type, bucket.filter((entry) => entry !== listener));
    },
    dispatchEvent(event) {
      dispatched.push(event);
      if (event.type.endsWith('-return-click')) timeline.push(event.type);
      for (const listener of [...(listeners.get(event.type) || [])]) listener(event);
      return true;
    },
    setTimeout,
    clearTimeout,
    showStatusToast() {},
    t: (key) => key,
  };

  class CustomEvent {
    constructor(type, init = {}) {
      this.type = type;
      this.detail = init.detail;
    }
  }

  const context = {
    window,
    document,
    CustomEvent,
    console: testConsole,
    setTimeout,
    clearTimeout,
  };
  vm.runInNewContext(surfaceSource, context, { filename: 'surface-floating-controls.js' });

  const interpage = {
    async handleModelReload() {
      timeline.push('reload');
      return true;
    },
  };
  vm.runInNewContext(extractResetBlock(), {
    window,
    document,
    I: interpage,
    console: testConsole,
    fetch: async (_url, options = {}) => {
      assert.equal(options.method, 'PUT');
      timeline.push('put');
      return {
        ok: true,
        status: 200,
        async text() { return ''; },
      };
    },
    setTimeout,
    clearTimeout,
  }, { filename: 'listeners-and-api-reset.js' });

  return {
    window,
    parts,
    dispatched,
    timeline,
    runReset: () => window.__testResetToDefaultModel(),
    waitForEvent(type) {
      return new Promise((resolve) => {
        const listener = (event) => {
          window.removeEventListener(type, listener);
          resolve(event);
        };
        window.addEventListener(type, listener);
      });
    },
    installCanonicalReturnHandler({ viewportReady = false } = {}) {
      parts.ensureModelViewportReadyBeforeShowCurrentModel = async () => {
        viewportCheckCount += 1;
        return { ready: viewportReady };
      };
      parts.showReturnBallContainer = (container) => {
        returnBallShowCount += 1;
        container.style.display = 'block';
        return container;
      };
      parts.revealReturnBallContainer = () => {
        returnBallRevealCount += 1;
      };
      vm.runInNewContext(extractCanonicalReturnBlock(), {
        window,
        document,
        CustomEvent,
        I: parts,
        console: testConsole,
        setTimeout,
        clearTimeout,
      }, { filename: 'canonical-goodbye-return.js' });
    },
    setReturnInProgress(value) {
      parts.nekoCatReturnInProgress = value === true;
    },
    hideVisibleReturnContainer() {
      if (visibleContainer) visibleContainer.style.display = 'none';
    },
    get viewportCheckCount() { return viewportCheckCount; },
    get returnBallShowCount() { return returnBallShowCount; },
    get returnBallRevealCount() { return returnBallRevealCount; },
    setTransition(direction, promise = null) {
      transitionDirection = direction;
      transitionPromise = promise;
      parts.nekoModelCatTransitionActive = direction ? { direction, promise: transitionPromise } : null;
    },
    clearTransition() {
      transitionDirection = '';
      transitionPromise = null;
      parts.nekoModelCatTransitionActive = null;
    },
    completeReturn() {
      active = false;
      setVisibleReturnType('');
      timeline.push('complete');
      window.dispatchEvent(new CustomEvent('neko:cat-return-complete'));
      parts.nekoCatReturnInProgress = false;
    },
    abortReturn() {
      timeline.push('abort');
      parts.nekoCatReturnInProgress = false;
      window.dispatchEvent(new CustomEvent('neko:cat-return-abort'));
    },
  };
}

const flushMicrotasks = () => new Promise((resolve) => setImmediate(resolve));

test('default reset waits for the canonical goodbye return before PUT and reload', async () => {
  const harness = createHarness({ visibleReturnType: 'live2d' });
  const reset = harness.runReset();
  await flushMicrotasks();

  assert.deepEqual(harness.timeline, ['live2d-return-click']);
  const returnEvent = harness.dispatched.find((event) => event.type === 'live2d-return-click');
  assert.deepEqual(
    JSON.parse(JSON.stringify(returnEvent.detail.returnButtonRect)),
    { left: 12, top: 24, width: 80, height: 80 },
  );
  harness.completeReturn();

  assert.equal((await reset).success, true);
  assert.deepEqual(harness.timeline, ['live2d-return-click', 'complete', 'put', 'reload']);
});

test('an aborted goodbye return does not persist or reload the default model', async () => {
  const harness = createHarness({ visibleReturnType: 'live2d' });
  const reset = harness.runReset();
  await flushMicrotasks();
  harness.abortReturn();

  const result = await reset;
  assert.equal(result.success, false);
  assert.equal(result.error, 'goodbye_return_failed');
  assert.deepEqual(harness.timeline, ['live2d-return-click', 'abort']);
});

test('default reset keeps its existing direct path outside goodbye mode', async () => {
  const harness = createHarness({ goodbyeActive: false });
  assert.equal((await harness.runReset()).success, true);
  assert.deepEqual(harness.timeline, ['put', 'reload']);
  assert.equal(harness.dispatched.some((event) => event.type.endsWith('-return-click')), false);
});

for (const [modelType, subType, expectedEvent] of [
  ['live2d', '', 'live2d-return-click'],
  ['vrm', '', 'vrm-return-click'],
  ['live3d', 'vrm', 'vrm-return-click'],
  ['live3d', 'mmd', 'mmd-return-click'],
  ['mmd', '', 'mmd-return-click'],
  ['pngtuber', '', 'pngtuber-return-click'],
]) {
  test(`programmatic return follows the ${modelType}/${subType || '-'} route`, async () => {
    const harness = createHarness({ modelType, subType });
    const returned = harness.window.appUi.returnFromGoodbye({ source: 'reset-to-default-model' });
    await flushMicrotasks();
    assert.deepEqual(harness.timeline, [expectedEvent]);
    harness.completeReturn();
    assert.equal(await returned, true);
  });
}

test('the visible return control wins over stale model configuration', async () => {
  const harness = createHarness({ modelType: 'vrm', visibleReturnType: 'mmd' });
  const returned = harness.window.appUi.returnFromGoodbye();
  await flushMicrotasks();
  assert.deepEqual(harness.timeline, ['mmd-return-click']);
  harness.completeReturn();
  assert.equal(await returned, true);
});

test('programmatic return waits for the model-to-cat transition', async () => {
  const harness = createHarness({ visibleReturnType: 'live2d' });
  let finishTransition;
  const transition = new Promise((resolve) => { finishTransition = resolve; });
  harness.setTransition('model-to-cat', transition);

  const returned = harness.window.appUi.returnFromGoodbye();
  await flushMicrotasks();
  assert.deepEqual(harness.timeline, []);

  harness.clearTransition();
  finishTransition();
  await flushMicrotasks();
  assert.deepEqual(harness.timeline, ['live2d-return-click']);
  harness.completeReturn();
  assert.equal(await returned, true);
});

test('concurrent programmatic returns share one canonical return request', async () => {
  const harness = createHarness({ visibleReturnType: 'pngtuber' });
  const first = harness.window.appUi.returnFromGoodbye();
  const second = harness.window.appUi.returnFromGoodbye();

  assert.strictEqual(first, second);
  await flushMicrotasks();
  assert.deepEqual(harness.timeline, ['pngtuber-return-click']);
  harness.completeReturn();
  assert.equal(await first, true);
});

test('default reset joins a manual return already in progress without redispatching', async () => {
  const harness = createHarness({ visibleReturnType: 'vrm' });
  const reset = harness.runReset();
  // Race a user-initiated return into the helper's first async boundary.
  harness.setReturnInProgress(true);
  await flushMicrotasks();
  assert.deepEqual(harness.timeline, []);

  harness.completeReturn();
  assert.equal((await reset).success, true);
  assert.deepEqual(harness.timeline, ['complete', 'put', 'reload']);
});

test('default reset joins a cat-to-model transition after its terminal event', async () => {
  const harness = createHarness({ goodbyeActive: false });
  let finishTransition;
  const transition = new Promise((resolve) => { finishTransition = resolve; });
  harness.setTransition('cat-to-model', transition);

  const reset = harness.runReset();
  await flushMicrotasks();
  assert.deepEqual(harness.timeline, []);

  harness.clearTransition();
  finishTransition();
  assert.equal((await reset).success, true);
  assert.deepEqual(harness.timeline, ['put', 'reload']);
  assert.equal(harness.dispatched.some((event) => event.type.endsWith('-return-click')), false);
});

test('programmatic return waits for a reserved model-to-cat transition without a promise', async () => {
  const harness = createHarness({ visibleReturnType: 'live2d' });
  harness.setTransition('model-to-cat');

  const returned = harness.window.appUi.returnFromGoodbye();
  await flushMicrotasks();
  assert.deepEqual(harness.timeline, []);

  const returnEvent = harness.waitForEvent('live2d-return-click');
  harness.clearTransition();
  await returnEvent;
  assert.deepEqual(harness.timeline, ['live2d-return-click']);
  harness.completeReturn();
  assert.equal(await returned, true);
});

test('a real viewport restore abort prevents persistence and leaves return retryable', async () => {
  const harness = createHarness({ visibleReturnType: 'live2d' });
  harness.installCanonicalReturnHandler({ viewportReady: false });
  harness.hideVisibleReturnContainer();

  const result = await harness.runReset();

  assert.equal(result.success, false);
  assert.equal(result.error, 'goodbye_return_failed');
  assert.equal(harness.viewportCheckCount, 3);
  assert.equal(harness.returnBallShowCount, 1);
  assert.equal(harness.returnBallRevealCount, 1);
  assert.equal(harness.parts.nekoCatReturnInProgress, false);
  assert.deepEqual(harness.timeline, ['live2d-return-click']);
  const abortEvents = harness.dispatched.filter((event) => event.type === 'neko:cat-return-abort');
  assert.equal(abortEvents.length, 1);
  assert.equal(abortEvents[0].detail.reason, 'return-incomplete');
});
