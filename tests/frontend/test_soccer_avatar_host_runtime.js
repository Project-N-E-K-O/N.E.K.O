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
  // Execute the real manager load/fallback/token implementation. Only network,
  // PIXI setup and model configuration are replaced with deterministic gates.
  const live2dPath = path.resolve(__dirname, '../../static/live2d/live2d-model.js');
  const live2dSource = fs.readFileSync(live2dPath, 'utf8');
  const loadStart = live2dSource.indexOf('Live2DManager.prototype.loadModel =');
  const loadEnd = live2dSource.indexOf('// 获取当前已加载的模型实例', loadStart);
  assert(loadStart !== -1 && loadEnd > loadStart, 'Live2D load implementation anchors missing');
  const originalFactory = windowMock.createSoccerAvatarHost;
  const originalSetTimeout = windowMock.setTimeout;
  const originalClearTimeout = windowMock.clearTimeout;
  for (const action of ['dispose', 'timeout', 'success']) {
    for (const stage of ['primary', 'fallback', 'fallback-after-cancel', 'configuration', 'rejection']) {
      if (action === 'success' && !['primary', 'configuration'].includes(stage)) continue;
      let now = 1000;
      const timers = new Map();
      const downloads = [];
      let finishConfiguration;
      let configured = 0;
      let published = 0;
      const configGate = new Promise(resolve => { finishConfiguration = resolve; });
      const model = {
        width: 200, height: 300, destroyed: false, releases: 0,
        internalModel: { settings: { url: '/models/late.model3.json' } },
        scale: { x: 1, y: 1, set() {} },
        destroy() { this.releases++; this.destroyed = true; },
      };
      class Live2DManager {
        constructor() { this._activeLoadToken = 0; this.currentModel = null; }
        async initPIXI() { this.pixi_app = {}; }
        _resetDerivedModelMetadata() {}
        async removeModel() { this.currentModel = null; }
        async _configureLoadedModel(_model, _path, _options, token) {
          if (!this._isLoadTokenActive(token)) return;
          if (stage === 'configuration') await configGate;
          if (this._isLoadTokenActive(token)) configured++;
        }
        pauseRendering() {}
        resumeRendering() {}
        destroy() {
          if (this.currentModel) this.currentModel.destroy();
          this.currentModel = null;
          this.pixi_app = null;
        }
      }
      windowMock.location = { pathname: '/soccer' };
      windowMock.setTimeout = callback => { const id = {}; timers.set(id, callback); return id; };
      windowMock.clearTimeout = id => timers.delete(id);
      const context = {
        window: windowMock, document: documentMock, console: windowMock.console,
        Date: { now: () => now }, clearTimeout: windowMock.clearTimeout,
        Live2DManager,
        Live2DModel: { from: requestedPath => new Promise((resolve, reject) => downloads.push({
          resolve(value) { value.internalModel.settings.url = requestedPath; resolve(value); }, reject,
        })) },
      };
      vm.runInNewContext(live2dSource.slice(loadStart, loadEnd), context, { filename: live2dPath });
      vm.runInNewContext(soccerSource, context, { filename: soccerHostPath });
      const manager = windowMock.live2dManager = new Live2DManager();
      const modelPath = stage === 'rejection'
        ? '/static/yui-lolita/yui-lolita.model3.json' : '/models/late.model3.json';
      const lateHost = windowMock.createSoccerAvatarHost({ onAvatarChanged() { published++; } });
      let settled = false;
      const mounting = lateHost.mount({
        slot: 'ai', model: { type: 'live2d', path: modelPath },
        viewport: { mode: 'fixed', width: 200, height: 300 },
        fit: { mode: 'contain' }, resize: { mode: 'fixed' },
      }).then(value => { settled = true; return value; }, error => { settled = true; return error; });
      await flush();
      assert(downloads.length === 1, `${action}/${stage}: primary download not started`);
      if (stage === 'fallback') {
        downloads[0].reject(new Error('primary failed'));
        await flush();
        assert(downloads.length === 2, 'fallback download not started');
      }
      if (stage === 'configuration') {
        downloads[0].resolve(model);
        await flush();
      }
      const tick = () => {
        for (const [id, callback] of [...timers]) { timers.delete(id); callback(); }
      };
      if (action === 'dispose') lateHost.dispose();
      if (action === 'timeout') { now += 20001; tick(); }
      await flush();
      if (action !== 'success') assert(settled, `${action}/${stage}: mount did not settle promptly`);
      const successor = { releases: 0, destroy() { this.releases++; } };
      if (stage === 'rejection') {
        // An abandoned error handler must not clear a newer load's state.
        manager._activeLoadToken++;
        manager.currentModel = successor;
      } else if (action === 'dispose') {
        windowMock.live2dManager = { currentModel: successor, destroy() { successor.destroy(); } };
      }
      if (stage === 'fallback-after-cancel') {
        downloads[0].reject(new Error('late primary failure'));
        await flush();
        assert(downloads.length === 2, 'late fallback path was not exercised');
      }
      if (stage === 'rejection') downloads[0].reject(new Error('late default model failure'));
      else if (stage === 'configuration') finishConfiguration();
      else downloads.at(-1).resolve(model);
      await flush();
      tick();
      await flush();
      const result = await mounting;
      if (action === 'success') {
        assert(!result.code && published === 1 && configured === 1, `${stage}: healthy load failed`);
        lateHost.dispose();
      } else {
        assert(published === 0 && configured === 0, `${action}/${stage}: cancelled model configured or published`);
        assert(manager.currentModel === (stage === 'rejection' ? successor : null),
          `${action}/${stage}: late model retained or successor removed`);
      }
      assert(successor.releases === 0, `${action}/${stage}: late cleanup destroyed successor`);
      if (action === 'dispose' && stage !== 'rejection') {
        assert(windowMock.live2dManager.currentModel === successor, `${stage}: replacement manager changed`);
      }
      assert(model.releases === (stage === 'rejection' ? 0 : 1),
        `${action}/${stage}: model not released exactly once (${model.releases})`);
      assert(timers.size === 0, `${action}/${stage}: readiness timer leaked`);
      lateHost.dispose();
    }
  }
  windowMock.createSoccerAvatarHost = originalFactory;
  windowMock.setTimeout = originalSetTimeout;
  windowMock.clearTimeout = originalClearTimeout;
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
