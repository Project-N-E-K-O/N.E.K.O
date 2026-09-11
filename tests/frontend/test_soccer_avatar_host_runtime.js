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
  // Replace only module acquisition; execute the actual soccer loader/controller.
  const soccerSource = fs.readFileSync(soccerHostPath, 'utf8')
    .replace("import('three/addons/loaders/GLTFLoader.js')", "window.loadTestVrmModule('loader')")
    .replace("import('@pixiv/three-vrm')", "window.loadTestVrmModule('vrm')");
  vm.runInThisContext(soccerSource, { filename: soccerHostPath });

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

  const flush = async () => {
    for (let index = 0; index < 10; index += 1) {
      await new Promise((resolve) => setImmediate(resolve));
    }
  };
  for (const slot of ['player', 'ai']) {
    for (const stage of ['init', 'gltf', 'mood', 'idle', 'success']) {
      let release;
      let reached = false;
      const wait = () => {
        reached = true;
        return new Promise((resolve) => { release = resolve; });
      };
      const calls = { added: 0, animated: 0, idle: 0, changed: 0, disposed: 0, released: 0 };
      const vrm = { scene: { visible: false }, meta: { metaVersion: '1' } };
      let manager;
      class Manager {
        constructor() {
          manager = this;
          this.core = { init: async () => {
            if (stage === 'init') await wait();
            this.scene = { add() { calls.added += 1; }, remove() {} };
            this.camera = {};
            this.renderer = { domElement: { style: {} } };
          } };
          this.expression = { loadMoodMap: async () => {
            if (stage === 'mood') await wait();
          } };
        }
        startAnimateLoop() { calls.animated += 1; }
        async playVRMAAnimation() {
          calls.idle += 1;
          if (stage === 'idle') await wait();
        }
        dispose() {
          calls.disposed += 1;
          if (this.currentModel) {
            calls.released += 1;
            this.currentModel = null;
          }
        }
      }
      windowMock.VRMManager = Manager;
      windowMock.loadTestVrmModule = async (name) => name === 'loader' ? {
        GLTFLoader: class {
          register() {}
          load(_path, resolve) {
            if (stage === 'gltf') wait().then(() => resolve({ userData: { vrm } }));
            else resolve({ userData: { vrm } });
          }
        },
      } : { VRMLoaderPlugin: class {}, VRMUtils: { deepDispose(scene) {
        assert(scene === vrm.scene, 'late disposal released a different scene');
        calls.released += 1;
      } } };
      const vrmHost = windowMock.createSoccerAvatarHost({ onAvatarChanged() { calls.changed += 1; } });
      const mount = vrmHost.mount({
        slot, model: { type: 'vrm', path: '/models/delayed.vrm' },
        viewport: { mode: 'fixed', width: 200, height: 300 },
        fit: { mode: 'contain', align: 'bottom-center', padding: 0, scaleMultiplier: 1 },
        resize: { mode: 'fixed' },
      }).then((value) => value, (error) => error);
      await flush();
      if (stage === 'success') {
        const controller = await mount;
        assert(!controller.code && controller.getState().ready, `${slot}: healthy VRM mount failed`);
        assert(calls.changed === 1 && calls.animated === 1, `${slot}: healthy VRM was not published`);
        vrmHost.dispose();
        assert(calls.released === 1, `${slot}: healthy VRM was not released`);
        continue;
      }
      assert(reached, `${slot}: did not reach ${stage} wait`);
      vrmHost.dispose();
      const error = await mount;
      assert(['cancelled', 'disposed'].includes(error.code),
        `${slot}/${stage}: mount did not cancel promptly: ${error.stack || error}`);
      release();
      await flush();
      assert(calls.changed === 0, `${slot}/${stage}: disposed avatar was published`);
      if (stage === 'init' || stage === 'gltf') {
        assert(calls.added === 0 && calls.animated === 0 && calls.idle === 0,
          `${slot}/${stage}: disposed manager restarted model rendering`);
      }
      if (stage === 'mood') assert(calls.idle === 0, `${slot}: idle started after disposal`);
      if (stage === 'init') assert(calls.disposed === 2, `${slot}: late init resources survived`);
      else assert(calls.released === 1, `${slot}/${stage}: VRM scene was not released exactly once`);
      assert(!manager.currentModel, `${slot}/${stage}: disposed manager retained a model`);
      assert(windowMock[slot === 'player' ? 'vrmManager' : 'aiVrmManager'] === null,
        `${slot}/${stage}: disposed manager was restored globally`);
    }
  }
  process.stdout.write('soccer Avatar host cancellation tests passed\n');
}

main().catch((error) => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exitCode = 1;
});
