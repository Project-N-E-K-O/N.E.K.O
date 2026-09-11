const fs = require('fs');
const path = require('path');
const vm = require('vm');

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

async function main() {
  const addedEvents = [];
  const removedEvents = [];
  const canvas = { style: {} };
  const container = { style: {} };
  const windowMock = {
    innerWidth: 1280,
    innerHeight: 720,
    screen: { width: 1920, height: 1080 },
    devicePixelRatio: 1,
    renderQuality: 'medium',
    mouseTrackingEnabled: true,
    live2dFullscreenTrackingEnabled: false,
    addEventListener(type, handler) { addedEvents.push({ type, handler }); },
    removeEventListener(type, handler) { removedEvents.push({ type, handler }); },
  };
  class ApplicationMock {
    constructor(options) {
      this.view = options.view;
      this.stage = {};
      this.renderer = {
        screen: { width: options.width, height: options.height },
        resolution: options.resolution,
        backgroundAlpha: options.backgroundAlpha,
        background: { alpha: 1 },
        resize: (width, height) => {
          this.renderer.screen.width = width;
          this.renderer.screen.height = height;
        },
      };
      this.ticker = {
        maxFPS: 60,
        stop() {},
        start() {},
      };
    }
    destroy() {}
  }
  const PIXI = {
    Application: ApplicationMock,
    live2d: { Live2DModel: class Live2DModelMock {} },
  };
  const context = vm.createContext({
    window: windowMock,
    document: {
      getElementById(id) {
        if (id === 'avatar-canvas') return canvas;
        if (id === 'avatar-container') return container;
        return null;
      },
    },
    PIXI,
    console: { log() {}, warn() {}, error() {}, debug() {} },
    setTimeout,
    clearTimeout,
    setInterval,
    clearInterval,
    requestAnimationFrame: (callback) => setTimeout(callback, 0),
    performance: { now: () => 0 },
  });
  const sourcePath = path.resolve(__dirname, '../../static/live2d/live2d-core.js');
  vm.runInContext(fs.readFileSync(sourcePath, 'utf8'), context, { filename: sourcePath });

  const fixedManager = new context.window.Live2DManager();
  fixedManager._startIdleFpsGovernor = () => {};
  fixedManager._stopIdleFpsGovernor = () => {};
  await fixedManager.initPIXI('avatar-canvas', 'avatar-container', {
    width: 200,
    height: 300,
    resizeMode: 'fixed',
  });
  assert(fixedManager.pixi_app.renderer.screen.width === 200, 'fixed renderer width changed');
  assert(fixedManager.pixi_app.renderer.screen.height === 300, 'fixed renderer height changed');
  assert(!addedEvents.some((event) => event.type === 'resize'),
    'fixed avatar renderer subscribed to host-window resize');
  assert(!addedEvents.some((event) => event.type === 'electron-display-changed'),
    'fixed avatar renderer subscribed to display resize');
  const firstFixedApp = fixedManager.pixi_app;
  await fixedManager.ensurePIXIReady('avatar-canvas', 'avatar-container', {
    width: 400,
    height: 600,
    resizeMode: 'fixed',
  });
  assert(fixedManager.pixi_app !== firstFixedApp,
    'fixed renderer was reused across different viewport dimensions');
  assert(fixedManager.pixi_app.renderer.screen.width === 400
    && fixedManager.pixi_app.renderer.screen.height === 600,
  'fixed renderer was not rebuilt with the requested viewport dimensions');

  const hostManager = new context.window.Live2DManager();
  hostManager._startIdleFpsGovernor = () => {};
  hostManager._stopIdleFpsGovernor = () => {};
  await hostManager.initPIXI('avatar-canvas', 'avatar-container', {
    resizeMode: 'host-window',
  });
  assert(addedEvents.some((event) => event.type === 'resize'),
    'host-window renderer lost its resize listener');
  assert(addedEvents.some((event) => event.type === 'electron-display-changed'),
    'host-window renderer lost its display listener');

  const concurrentManager = new context.window.Live2DManager();
  concurrentManager._startIdleFpsGovernor = () => {};
  concurrentManager._stopIdleFpsGovernor = () => {};
  const fixedInitialization = concurrentManager.initPIXI(
    'avatar-canvas',
    'avatar-container',
    { width: 240, height: 360, resizeMode: 'fixed' },
  );
  let mismatchedInitializationError = null;
  try {
    await concurrentManager.initPIXI('avatar-canvas', 'avatar-container', {
      resizeMode: 'host-window',
    });
  } catch (error) {
    mismatchedInitializationError = error;
  }
  await fixedInitialization;
  assert(/不能并发复用/.test(String(mismatchedInitializationError?.message || '')),
    'concurrent initPIXI reused a promise created for a different resizeMode');
  assert(concurrentManager.pixi_app.renderer.screen.width === 240
    && concurrentManager.pixi_app.renderer.screen.height === 360,
  'mismatched concurrent initPIXI changed the fixed renderer dimensions');

  process.stdout.write('Live2D fixed viewport runtime test passed\n');
}

main().catch((error) => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exitCode = 1;
});
