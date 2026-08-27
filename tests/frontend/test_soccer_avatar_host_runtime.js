const fs = require('fs');
const path = require('path');
const vm = require('vm');

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

async function main() {
  const activeTimers = new Set();
  let loadCalls = 0;
  let destroyCalls = 0;
  const container = { clientWidth: 200, clientHeight: 300 };
  const documentMock = {
    getElementById(id) {
      return id === 'ai-l2d-container' ? container : null;
    },
  };
  const windowMock = {
    AbortController,
    document: documentMock,
    innerWidth: 1280,
    innerHeight: 720,
    console: { log() {}, warn() {}, error() {} },
    setTimeout(callback, delay) {
      const id = setTimeout(() => {
        activeTimers.delete(id);
        callback();
      }, delay);
      activeTimers.add(id);
      return id;
    },
    clearTimeout(id) {
      activeTimers.delete(id);
      clearTimeout(id);
    },
    live2dManager: {
      currentModel: null,
      async initPIXI() {},
      loadModel() {
        loadCalls += 1;
        return new Promise(() => {});
      },
      pauseRendering() {},
      resumeRendering() {},
      destroy() { destroyCalls += 1; },
    },
  };
  global.window = windowMock;
  global.document = documentMock;

  const genericHostPath = path.resolve(
    __dirname,
    '../../static/game/sdk/neko-minigame-avatar-host.js',
  );
  const soccerHostPath = path.resolve(
    __dirname,
    '../../static/game/games/soccer/soccer-avatar-host.js',
  );
  vm.runInThisContext(fs.readFileSync(genericHostPath, 'utf8'), { filename: genericHostPath });
  vm.runInThisContext(fs.readFileSync(soccerHostPath, 'utf8'), { filename: soccerHostPath });

  const host = windowMock.createSoccerAvatarHost();
  const pendingMount = host.mount({
    slot: 'ai',
    model: { type: 'live2d', path: '/models/stuck.model3.json' },
    viewport: { mode: 'fixed', width: 200, height: 300 },
    fit: { mode: 'contain', align: 'bottom-center', padding: 0, scaleMultiplier: 1 },
    resize: { mode: 'fixed' },
  }).then(() => null, (error) => error);

  for (let index = 0; index < 10 && loadCalls === 0; index += 1) {
    await new Promise((resolve) => setImmediate(resolve));
  }
  assert(loadCalls === 1, 'Live2D model load did not reach the readiness wait');
  assert(activeTimers.size === 1, 'Live2D readiness polling did not own one tracked timer');

  host.dispose();
  const disposeError = await pendingMount;
  assert(disposeError?.code === 'cancelled',
    'disposing a pending soccer Live2D mount did not cancel its readiness wait');
  assert(activeTimers.size === 0, 'soccer Live2D readiness timer survived host disposal');
  assert(destroyCalls === 1, 'pending soccer Live2D manager was not disposed exactly once');

  process.stdout.write('soccer Avatar host cancellation test passed\n');
}

main().catch((error) => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exitCode = 1;
});
