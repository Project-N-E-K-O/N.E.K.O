const fs = require('fs');
const path = require('path');
const vm = require('vm');

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function settleWithin(promise, timeoutMs, message) {
  let timer = null;
  return Promise.race([
    promise,
    new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error(message)), timeoutMs);
    }),
  ]).finally(() => clearTimeout(timer));
}

async function main() {
  const listeners = new Map();
  const frames = new Map();
  const observers = [];
  let nextFrameId = 1;
  const windowMock = {
    innerWidth: 1280,
    innerHeight: 720,
    console: { error() {}, warn() {} },
    addEventListener(type, handler) {
      if (!listeners.has(type)) listeners.set(type, new Set());
      listeners.get(type).add(handler);
    },
    removeEventListener(type, handler) { listeners.get(type)?.delete(handler); },
  };
  class ResizeObserverMock {
    constructor(callback) {
      this.callback = callback;
      this.target = null;
      this.disconnected = false;
      observers.push(this);
    }
    observe(target) { this.target = target; }
    disconnect() { this.disconnected = true; this.target = null; }
    trigger() { this.callback([{ target: this.target }]); }
  }
  const containers = {
    fixed: { clientWidth: 200, clientHeight: 300 },
    container: { clientWidth: 320, clientHeight: 240 },
    hostone: { clientWidth: 10, clientHeight: 10 },
    hosttwo: { clientWidth: 10, clientHeight: 10 },
  };
  const controllerStates = [];
  function slot(containerId) {
    return {
      containerId,
      async createController({ config, viewport }) {
        const state = {
          slot: config.slot,
          viewport,
          models: [],
          resizes: [],
          resizeAttempts: [],
          failNextResize: false,
          disposed: 0,
        };
        controllerStates.push(state);
        return {
          async setModel(model) { state.models.push(model); },
          focus(point) { state.focus = point; },
          setEmotion(name) { state.emotion = name; },
          pause() { state.paused = true; },
          resume() { state.paused = false; },
          getState() { return { ready: state.models.length > 0 }; },
          async resize(viewport, fit, metadata) {
            state.resizeAttempts.push({ viewport, fit, metadata });
            if (state.failNextResize) {
              state.failNextResize = false;
              throw new Error('transient resize failure');
            }
            state.viewport = viewport;
            state.resizes.push({ viewport, fit, metadata });
          },
          dispose() { state.disposed += 1; },
        };
      },
    };
  }

  const context = vm.createContext({ window: windowMock, console: windowMock.console });
  const sourcePath = path.resolve(__dirname, '../../static/game/sdk/neko-minigame-avatar-host.js');
  vm.runInContext(fs.readFileSync(sourcePath, 'utf8'), context, { filename: sourcePath });
  assert(typeof windowMock.NekoMiniGameAvatarHost?.create === 'function',
    'public avatar host factory was not exported');

  const host = windowMock.NekoMiniGameAvatarHost.create({
    slots: {
      fixed: slot('fixed'),
      container: slot('container'),
      hostone: slot('hostone'),
      hosttwo: slot('hosttwo'),
    },
    windowImpl: windowMock,
    documentImpl: { getElementById: (id) => containers[id] || null },
    ResizeObserverImpl: ResizeObserverMock,
    requestAnimationFrameImpl(callback) {
      const id = nextFrameId++;
      frames.set(id, callback);
      return id;
    },
    cancelAnimationFrameImpl(id) { frames.delete(id); },
  });

  const base = {
    model: { type: 'live2d', path: '/model.model3.json' },
    fit: { mode: 'contain', align: 'bottom-center', padding: 6, scaleMultiplier: 1 },
  };
  const fixed = await host.mount({
    ...base,
    slot: 'fixed',
    viewport: { mode: 'fixed', width: 200, height: 300 },
    resize: { mode: 'fixed' },
  });
  assert(host.activeCount === 1, 'fixed controller was not tracked');
  assert(!listeners.get('resize')?.size, 'fixed controller installed a host resize listener');
  assert(observers.length === 0, 'fixed controller installed a ResizeObserver');
  assert(controllerStates[0].resizes.at(-1).viewport.width === 200,
    'fixed viewport was not applied');

  const container = await host.mount({
    ...base,
    slot: 'container',
    viewport: { mode: 'container' },
    resize: { mode: 'container' },
  });
  assert(observers.length === 1 && observers[0].target === containers.container,
    'container controller did not observe its registered container');
  assert(controllerStates[1].resizes.at(-1).viewport.width === 320,
    'container viewport did not use the measured width');
  containers.container.clientWidth = 500;
  containers.container.clientHeight = 400;
  observers[0].trigger();
  for (const [id, callback] of Array.from(frames)) { frames.delete(id); callback(); }
  await new Promise((resolve) => setImmediate(resolve));
  assert(controllerStates[1].resizes.at(-1).viewport.width === 500,
    'container resize was not delivered');
  controllerStates[1].failNextResize = true;
  containers.container.clientWidth = 550;
  observers[0].trigger();
  for (const [id, callback] of Array.from(frames)) { frames.delete(id); callback(); }
  await new Promise((resolve) => setImmediate(resolve));
  assert(container.getState().viewport.width === 500,
    'failed resize committed an unrendered viewport');
  const attemptsAfterFailure = controllerStates[1].resizeAttempts.length;
  observers[0].trigger();
  for (const [id, callback] of Array.from(frames)) { frames.delete(id); callback(); }
  await new Promise((resolve) => setImmediate(resolve));
  assert(controllerStates[1].resizeAttempts.length === attemptsAfterFailure + 1
    && container.getState().viewport.width === 550,
    'same-size retry was skipped after a transient resize failure');

  const hostOne = await host.mount({
    ...base,
    slot: 'hostone',
    viewport: { mode: 'host-window' },
    resize: { mode: 'host-window' },
  });
  const hostTwo = await host.mount({
    ...base,
    slot: 'hosttwo',
    viewport: { mode: 'host-window' },
    resize: { mode: 'host-window' },
  });
  assert(listeners.get('resize')?.size === 1,
    'host-window controllers did not share one resize listener');
  windowMock.innerWidth = 1440;
  windowMock.innerHeight = 900;
  for (const handler of listeners.get('resize')) handler();
  for (const [id, callback] of Array.from(frames)) { frames.delete(id); callback(); }
  await new Promise((resolve) => setImmediate(resolve));
  assert(controllerStates[2].resizes.at(-1).viewport.height === 900,
    'host-window resize was not delivered to the first controller');
  assert(controllerStates[3].resizes.at(-1).viewport.height === 900,
    'host-window resize was not delivered to the second controller');

  await fixed.setModel({ type: 'vrm', path: '/replacement.vrm' });
  assert(controllerStates[0].models.length === 2, 'model replacement was not forwarded');
  assert(controllerStates[0].resizes.at(-1).metadata.reason === 'model-changed',
    'model replacement did not trigger an idempotent refit');

  hostOne.dispose();
  assert(listeners.get('resize')?.size === 1,
    'shared resize listener was removed while a host-window controller remained');
  hostTwo.dispose();
  assert(!listeners.get('resize')?.size,
    'shared resize listener was not removed after the last host-window controller');
  container.dispose();
  assert(observers[0].disconnected, 'container ResizeObserver was not disconnected');
  fixed.dispose();
  assert(host.activeCount === 0, 'explicit controller disposal did not clear host state');
  assert(controllerStates.every((state) => state.disposed === 1),
    'raw avatar controllers were not disposed exactly once');
  host.dispose();

  let releasePendingFactory;
  let pendingRawDisposed = 0;
  const pendingGate = new Promise((resolve) => { releasePendingFactory = resolve; });
  const pendingRaw = {
    async setModel() {},
    focus() {},
    setEmotion() {},
    pause() {},
    resume() {},
    getState() { return {}; },
    async resize() {},
    dispose() { pendingRawDisposed += 1; },
  };
  const limitedHost = windowMock.NekoMiniGameAvatarHost.create({
    rendererLimit: 1,
    slots: {
      first: {
        container: { clientWidth: 200, clientHeight: 300 },
        async createController() { await pendingGate; return pendingRaw; },
      },
      second: {
        container: { clientWidth: 200, clientHeight: 300 },
        async createController() { return pendingRaw; },
      },
    },
    windowImpl: windowMock,
    documentImpl: {},
    ResizeObserverImpl: ResizeObserverMock,
    requestAnimationFrameImpl: (callback) => { callback(); return 1; },
    cancelAnimationFrameImpl() {},
  });
  const pendingMount = limitedHost.mount({
    ...base,
    slot: 'first',
    viewport: { mode: 'fixed', width: 200, height: 300 },
    resize: { mode: 'fixed' },
  }).then(() => null, (error) => error);
  await new Promise((resolve) => setImmediate(resolve));
  let pendingLimitError = null;
  try {
    await limitedHost.mount({
      ...base,
      slot: 'second',
      viewport: { mode: 'fixed', width: 200, height: 300 },
      resize: { mode: 'fixed' },
    });
  } catch (error) {
    pendingLimitError = error;
  }
  assert(pendingLimitError?.code === 'busy', 'pending renderer did not consume the host bound');
  limitedHost.dispose();
  const pendingDisposeError = await settleWithin(
    pendingMount,
    1000,
    'controller factory cancellation left mount pending',
  );
  assert(pendingDisposeError?.code === 'disposed', 'pending mount did not observe host disposal');
  assert(limitedHost.pendingCount === 0,
    'cancelled controller factory retained its pending mount slot');
  releasePendingFactory();
  await new Promise((resolve) => setImmediate(resolve));
  assert(pendingRawDisposed === 1, 'controller resolved after disposal was not released exactly once');

  let releaseInitialModel;
  const initialModelGate = new Promise((resolve) => { releaseInitialModel = resolve; });
  let stalledRawDisposed = 0;
  const stalledModelHost = windowMock.NekoMiniGameAvatarHost.create({
    slots: {
      stalled: {
        container: { clientWidth: 200, clientHeight: 300 },
        async createController() {
          return {
            async setModel() { await initialModelGate; },
            focus() {},
            setEmotion() {},
            pause() {},
            resume() {},
            getState() { return {}; },
            async resize() {},
            dispose() { stalledRawDisposed += 1; },
          };
        },
      },
    },
    windowImpl: windowMock,
    documentImpl: {},
    ResizeObserverImpl: ResizeObserverMock,
  });
  const stalledMount = stalledModelHost.mount({
    ...base,
    slot: 'stalled',
    viewport: { mode: 'fixed', width: 200, height: 300 },
    resize: { mode: 'fixed' },
  }).then(() => null, (error) => error);
  await new Promise((resolve) => setImmediate(resolve));
  stalledModelHost.dispose();
  const stalledMountError = await settleWithin(
    stalledMount,
    1000,
    'initial model cancellation left mount pending',
  );
  assert(stalledMountError?.code === 'disposed',
    'host disposal did not settle an initial model load that ignored cancellation');
  assert(stalledModelHost.pendingCount === 0 && stalledRawDisposed === 1,
    'cancelled initial model load did not release its pending slot and raw controller');
  releaseInitialModel();
  await new Promise((resolve) => setImmediate(resolve));
  assert(stalledRawDisposed === 1, 'late initial model completion disposed the raw controller twice');

  let releaseInitialResize;
  const initialResizeGate = new Promise((resolve) => { releaseInitialResize = resolve; });
  let stalledResizeRawDisposed = 0;
  const stalledResizeHost = windowMock.NekoMiniGameAvatarHost.create({
    slots: {
      stalledresize: {
        container: { clientWidth: 200, clientHeight: 300 },
        async createController() {
          return {
            async setModel() {},
            focus() {},
            setEmotion() {},
            pause() {},
            resume() {},
            getState() { return {}; },
            async resize() { await initialResizeGate; },
            dispose() { stalledResizeRawDisposed += 1; },
          };
        },
      },
    },
    windowImpl: windowMock,
    documentImpl: {},
    ResizeObserverImpl: ResizeObserverMock,
  });
  const stalledResizeMount = stalledResizeHost.mount({
    ...base,
    slot: 'stalledresize',
    viewport: { mode: 'fixed', width: 200, height: 300 },
    resize: { mode: 'fixed' },
  }).then(() => null, (error) => error);
  await new Promise((resolve) => setImmediate(resolve));
  stalledResizeHost.dispose();
  const stalledResizeError = await settleWithin(
    stalledResizeMount,
    1000,
    'initial resize cancellation left mount pending',
  );
  assert(stalledResizeError?.code === 'disposed',
    'host disposal did not settle an initial resize that ignored cancellation');
  assert(stalledResizeHost.pendingCount === 0 && stalledResizeRawDisposed === 1,
    'cancelled initial resize did not release its pending slot and raw controller');
  releaseInitialResize();
  await new Promise((resolve) => setImmediate(resolve));
  assert(stalledResizeRawDisposed === 1, 'late initial resize completion disposed the raw controller twice');

  let releaseBlockedModel;
  const blockedModelGate = new Promise((resolve) => { releaseBlockedModel = resolve; });
  let modelCalls = 0;
  let queuedRawDisposed = 0;
  const operationHost = windowMock.NekoMiniGameAvatarHost.create({
    pendingOperationLimit: 2,
    slots: {
      queue: {
        container: { clientWidth: 200, clientHeight: 300 },
        async createController() {
          return {
            async setModel() {
              modelCalls += 1;
              if (modelCalls === 2) await blockedModelGate;
            },
            focus() {},
            setEmotion() {},
            pause() {},
            resume() {},
            getState() { return {}; },
            async resize() {},
            dispose() { queuedRawDisposed += 1; },
          };
        },
      },
    },
    windowImpl: windowMock,
    documentImpl: {},
    ResizeObserverImpl: ResizeObserverMock,
    requestAnimationFrameImpl: (callback) => { callback(); return 1; },
    cancelAnimationFrameImpl() {},
  });
  const queuedController = await operationHost.mount({
    ...base,
    slot: 'queue',
    viewport: { mode: 'fixed', width: 200, height: 300 },
    resize: { mode: 'fixed' },
  });
  const blockedModel = queuedController.setModel({ type: 'live2d', path: '/blocked.json' })
    .then(() => null, (error) => error);
  const queuedModel = queuedController.setModel({ type: 'live2d', path: '/queued.json' })
    .then(() => null, (error) => error);
  const overflowError = await queuedController
    .setModel({ type: 'live2d', path: '/overflow.json' })
    .then(() => null, (error) => error);
  assert(overflowError?.code === 'busy',
    'per-controller Avatar operation limit did not reject excess queued work');
  queuedController.dispose();
  const [blockedDisposeError, queuedDisposeError] = await settleWithin(
    Promise.all([blockedModel, queuedModel]),
    1000,
    'blocked Avatar operations did not settle after controller disposal',
  );
  assert(blockedDisposeError?.code === 'disposed' && queuedDisposeError?.code === 'disposed',
    'Avatar disposal did not settle active and queued operations when the renderer stayed blocked');
  releaseBlockedModel();
  await new Promise((resolve) => setImmediate(resolve));
  assert(queuedRawDisposed === 1, 'queued Avatar controller was not disposed exactly once');
  operationHost.dispose();

  const model = {
    width: 100,
    height: 200,
    x: 0,
    y: 0,
    anchor: { x: 0.5, y: 0.5 },
    scale: {
      x: 1,
      y: 1,
      set(x, y) {
        model.width *= x / this.x;
        model.height *= y / this.y;
        this.x = x;
        this.y = y;
      },
    },
  };
  windowMock.NekoMiniGameAvatarHost.fitLive2DModel(model, { width: 200, height: 300 }, base.fit);
  const firstScale = model.scale.x;
  windowMock.NekoMiniGameAvatarHost.fitLive2DModel(model, { width: 200, height: 300 }, base.fit);
  assert(Math.abs(model.scale.x - firstScale) < 0.000001,
    'repeated Live2D fit accumulated model scale');
  assert(Math.round(model.y + model.height * 0.5) === 294,
    'Live2D bottom alignment did not respect padding');

  process.stdout.write('mini-game Avatar host runtime test passed\n');
}

main().catch((error) => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exitCode = 1;
});
