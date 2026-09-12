const fs = require('fs');
const path = require('path');
const vm = require('vm');

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

async function rejection(promise) {
  try {
    await promise;
    return null;
  } catch (error) {
    return error;
  }
}

async function withTimeout(promise, message, timeoutMs = 2000) {
  let timer = null;
  try {
    return await Promise.race([
      promise,
      new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error(message)), timeoutMs);
      }),
    ]);
  } finally {
    if (timer !== null) clearTimeout(timer);
  }
}

function jsonResponse(data, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    async json() { return data; },
  };
}

function element(width = 420, height = 360) {
  return {
    clientWidth: width,
    clientHeight: height,
    hidden: false,
    classList: { toggle() {}, add() {}, remove() {} },
    style: { display: '', setProperty() {} },
    getBoundingClientRect() { return { width, height }; },
  };
}

async function verifyConfiguredLive2DIdleReplay() {
  class ReplayManager {}
  const replayCalls = [];
  const scheduledTimers = [];
  const replayContext = vm.createContext({
    Live2DManager: ReplayManager,
    window: { _currentMotionPreviewId: null },
    console: { log() {}, warn() {}, error() {} },
    setTimeout(callback, delay) {
      const timer = { callback, delay, cleared: false };
      scheduledTimers.push(timer);
      return timer;
    },
    clearTimeout(timer) { if (timer) timer.cleared = true; },
    performance: { now: () => 0 },
    requestAnimationFrame: () => 0,
    fetch: async () => { throw new Error('unexpected replay fetch'); },
  });
  const source = fs.readFileSync(
    path.resolve(__dirname, '../../static/live2d/live2d-model.js'),
    'utf8',
  );
  vm.runInContext(source, replayContext, { filename: 'live2d-model.js' });

  const manager = new ReplayManager();
  manager._avatarPerformanceBypassLocks = true;
  manager._userIdleAnimations = ['configured.motion3.json'];
  manager._clearActiveMotionParamIds = () => replayCalls.push(['clear']);
  manager._trackActiveMotionParametersFromFile = async (file) => {
    replayCalls.push(['track', file]);
  };
  const motionManager = {
    definitions: {
      Idle: [{ File: '/motions/default.motion3.json' }],
      PreviewAll: [
        { File: '/motions/other.motion3.json' },
        { File: '/motions/configured.motion3.json' },
      ],
    },
    motionGroups: { Idle: [{}], PreviewAll: [{}, {}] },
    state: { currentPriority: 1, currentGroup: 'Idle', currentIndex: 0 },
    playing: true,
    stopAllMotions() {
      replayCalls.push(['stop-default']);
      this.playing = false;
      this.state.currentPriority = 0;
    },
    async startMotion(group, index, priority) {
      replayCalls.push(['start', group, index, priority]);
      this.playing = true;
      this.state.currentPriority = priority;
      this.state.currentGroup = group;
      this.state.currentIndex = index;
      return true;
    },
    async startRandomMotion() {
      replayCalls.push(['unexpected-random']);
      return true;
    },
  };
  let motionFinishHandler = null;
  manager.currentModel = {
    destroyed: false,
    internalModel: {
      motionManager,
      events: {
        on(event, handler) { if (event === 'motionFinish') motionFinishHandler = handler; },
        removeListener() {},
      },
    },
  };

  manager.setupIdleMotionLoop(manager.currentModel);
  assert(scheduledTimers.length === 1 && scheduledTimers[0].delay === 2000,
    'Live2D configured idle replay scheduler was not installed');
  scheduledTimers[0].callback();
  await new Promise((resolve) => setImmediate(resolve));
  const stopIndex = replayCalls.findIndex((entry) => entry[0] === 'stop-default');
  const startIndex = replayCalls.findIndex((entry) => entry[0] === 'start');
  assert(replayCalls.some((entry) => entry[0] === 'start'
    && entry[1] === 'PreviewAll' && entry[2] === 1 && entry[3] === 1)
    && stopIndex >= 0 && stopIndex < startIndex,
  'Live2D idle replay did not replace the native default Idle by runtime definition index');
  assert(replayCalls.some((entry) => entry[0] === 'track'
    && entry[1] === '/motions/configured.motion3.json')
    && !replayCalls.some((entry) => entry[0] === 'unexpected-random'),
  'Live2D idle replay fell back to a random motion after a configured motion was cached');

  const startsAfterConfiguredIdle = replayCalls.filter((entry) => entry[0] === 'start').length;
  const stopsAfterConfiguredIdle = replayCalls.filter((entry) => entry[0] === 'stop-default').length;
  await manager._playIdleMotion(motionManager);
  assert(replayCalls.filter((entry) => entry[0] === 'start').length === startsAfterConfiguredIdle
    && replayCalls.filter((entry) => entry[0] === 'stop-default').length
      === stopsAfterConfiguredIdle,
  'Live2D idle replay restarted an already active configured idle motion');

  motionManager.playing = true;
  motionManager.state = { currentPriority: 1, currentGroup: 'Idle', currentIndex: 0 };
  motionFinishHandler();
  const recoveryTimer = scheduledTimers.at(-1);
  assert(recoveryTimer?.delay === 0,
    'Live2D action completion did not schedule prompt configured-idle recovery');
  recoveryTimer.callback();
  await new Promise((resolve) => setImmediate(resolve));
  assert(replayCalls.filter((entry) => entry[0] === 'start').length
    === startsAfterConfiguredIdle + 1
    && replayCalls.filter((entry) => entry[0] === 'stop-default').length
      === stopsAfterConfiguredIdle + 1,
  'Live2D configured idle did not reclaim the motion slot after native Idle started');
}

async function main() {
  const sdkDir = path.resolve(__dirname, '../../static/game/sdk');
  const genericPath = path.join(sdkDir, 'neko-minigame-avatar-host.js');
  const drawingPath = path.join(sdkDir, 'neko-minigame-drawing-avatar-host.js');
  const elements = {
    'model-stage': element(),
    'live2d-container': element(),
    'live2d-canvas': element(),
    'vrm-container': element(),
    'vrm-canvas': element(),
    'mmd-container': element(),
    'mmd-canvas': element(),
    'pngtuber-container': element(),
  };
  const calls = [];
  const analyser = {
    fftSize: 8,
    getByteTimeDomainData(data) { data.fill(144); },
  };
  let nextFrame = 1;
  const frames = new Map();
  const listeners = new Map();
  const activeIntervals = new Set();
  const activeTimeouts = new Set();
  const disposeGates = { vrm: null, mmd: null, pngtuber: null };
  let nextMmdSettingsFailure = null;
  let nextMmdSettingsGate = null;
  let onNextMmdSettingsFetch = null;
  let nextMmdAnimationFailure = null;
  let nextMmdAnimationGate = null;
  let onNextMmdAnimationLoad = null;
  let nextLive2DIdleFailure = null;
  let nextLive2DIdleGate = null;
  let nextLive2DIdleEmptyResult = false;
  let onNextLive2DIdleLoad = null;
  let nextLive2DExpressionGate = null;
  let live2dManagersCreated = 0;

  async function recordRendererDispose(kind) {
    calls.push([`${kind}-dispose-start`]);
    if (disposeGates[kind]) await disposeGates[kind];
    await Promise.resolve();
    calls.push([`${kind}-dispose-end`]);
  }

  class ResizeObserverMock {
    constructor(callback) { this.callback = callback; }
    observe(target) { this.target = target; }
    disconnect() { this.target = null; }
  }

  function live2dModel(config) {
    const parameters = new Map([['ParamMouthOpenY', 0]]);
    const definitions = config?.FileReferences?.Motions || {};
    const motionGroups = Object.fromEntries(
      Object.keys(definitions).map((group) => [group, []]),
    );
    const motionManager = {
      definitions,
      motionGroups,
      state: { currentPriority: 0 },
      playing: false,
      async loadMotion(group, index) {
        const file = definitions[group]?.[index]?.File;
        calls.push(['live2d-idle-load', group, index, file]);
        const failure = nextLive2DIdleFailure;
        nextLive2DIdleFailure = null;
        const gate = nextLive2DIdleGate;
        nextLive2DIdleGate = null;
        const emptyResult = nextLive2DIdleEmptyResult;
        nextLive2DIdleEmptyResult = false;
        const notify = onNextLive2DIdleLoad;
        onNextLive2DIdleLoad = null;
        notify?.();
        if (gate) await gate;
        if (failure) throw failure;
        if (emptyResult) return undefined;
        const motion = {
          setIsLoop(value) { calls.push(['live2d-idle-loop', value]); },
        };
        if (!Array.isArray(motionGroups[group])) motionGroups[group] = [];
        motionGroups[group][index] = motion;
        return motion;
      },
      stopAllMotions() {
        calls.push(['live2d-stop-idle']);
        this.playing = false;
        this.state.currentPriority = 0;
      },
    };
    let destroyed = false;
    return {
      width: 1200,
      height: 1800,
      x: 0,
      y: 0,
      anchor: { set() {} },
      scale: { set(value) { this.value = value; } },
      getLocalBounds() { return { width: 1200, height: 1800 }; },
      getBounds() { return { x: 0, y: 0, width: 120, height: 180 }; },
      removeAllListeners() { calls.push(['live2d-model-remove-listeners']); },
      destroy() {
        if (destroyed) throw new Error('Live2D model was destroyed twice');
        destroyed = true;
        calls.push(['live2d-model-dispose']);
      },
      async motion(group, index, priority) {
        calls.push(['live2d-idle-play', group, index, priority]);
        motionManager.playing = true;
        motionManager.state.currentPriority = priority;
        return true;
      },
      internalModel: {
        motionManager,
        coreModel: {
          getParameterIndex(id) { return parameters.has(id) ? 0 : -1; },
          setParameterValueById(id, value) {
            parameters.set(id, value);
            calls.push(['live2d-mouth', id, value]);
          },
        },
      },
    };
  }

  class Live2DManagerMock {
    constructor() {
      this.instanceId = ++live2dManagersCreated;
      this.currentModel = null;
      this._screenChangeHandler = () => {};
      this._displayChangeHandler = () => {};
      this._idleFpsGovernorTimer = `governor-${this.instanceId}`;
      this._savedParamsTimer = `saved-params-${this.instanceId}`;
      this._idleFpsRestoreTimer = `restore-${this.instanceId}`;
      this._idleMotionLoopTimers = new Set([`idle-loop-${this.instanceId}`]);
      this._popupTimers = { popup: `popup-${this.instanceId}` };
      activeIntervals.add(this._idleFpsGovernorTimer);
      activeIntervals.add(this._savedParamsTimer);
      activeTimeouts.add(this._idleFpsRestoreTimer);
      activeTimeouts.add(`idle-loop-${this.instanceId}`);
      activeTimeouts.add(`popup-${this.instanceId}`);
      windowMock.addEventListener('resize', this._screenChangeHandler);
      windowMock.addEventListener('electron-display-changed', this._displayChangeHandler);
      this.pixi_app = {
        renderer: { resize: (width, height) => calls.push(['live2d-resize', width, height]) },
        view: { style: { setProperty() {} } },
        ticker: {
          start() { calls.push(['live2d-resume']); },
          stop() { calls.push(['live2d-pause']); },
        },
        destroy(removeView) { calls.push(['live2d-pixi-dispose', removeView]); },
      };
    }
    async ensurePIXIReady() { calls.push(['live2d-init']); }
    async loadModel(config, options) {
      calls.push(['live2d-model', config.url, options?.suppressInitialIdle === true]);
      if (options?.suppressInitialIdle === true) {
        config.FileReferences.Motions.PreviewAll = [
          { File: '/animations/live2d-other.motion3.json' },
          { File: '/animations/live2d-idle.motion3.json' },
          { File: '/animations/live2d-legacy-only.motion3.json' },
        ];
      }
      this.currentModel = live2dModel(config);
    }
    async removeModel() {
      calls.push(['live2d-remove-model']);
      this.currentModel?.destroy?.({ children: true });
      this.currentModel = null;
    }
    cleanupEventListeners() { calls.push(['live2d-cleanup-listeners']); }
    _stopIdleFpsGovernor() { calls.push(['live2d-stop-governor']); }
    _clearIdleMotionLoopTimers() { calls.push(['live2d-clear-idle-scheduler']); }
    setupIdleMotionLoop() {
      calls.push(['live2d-setup-idle-scheduler', this._userIdleAnimations?.[0]]);
    }
    hasActiveActionMotion() { return false; }
    async playExpression(name) {
      calls.push(['live2d-expression', name]);
      const gate = nextLive2DExpressionGate;
      nextLive2DExpressionGate = null;
      if (gate) await gate;
      return true;
    }
    setEmotion(name) { calls.push(['live2d-emotion', name]); }
  }

  class VRMManagerMock {
    constructor() {
      this.currentModel = null;
      this.animation = {
        startLipSync(value) { calls.push(['vrm-speaking', value === analyser]); },
        stopLipSync() { calls.push(['vrm-stop-speaking']); },
      };
      this.expression = { setMood(mood) { calls.push(['vrm-emotion', mood]); } };
    }
    async initThreeJS(_canvas, _container, lighting) {
      this.lighting = lighting;
      calls.push(['vrm-init', lighting?.ambient]);
      return true;
    }
    async loadModel(model, options) {
      this.currentModel = {};
      const effectiveIdleAnimation = options?.idleAnimation
        || windowMock.lanlan_config?.vrmIdleAnimation
        || '/static/vrm/animation/wait03.vrma.gz';
      calls.push([
        'vrm-model', model, options?.idleAnimation, options?.idleAnimations, this.lighting?.ambient,
        effectiveIdleAnimation,
      ]);
    }
    onWindowResize() { calls.push(['vrm-resize']); }
    pauseRendering() { calls.push(['vrm-pause']); }
    resumeRendering() { calls.push(['vrm-resume']); }
    async dispose() { await recordRendererDispose('vrm'); }
  }

  class MMDManagerMock {
    constructor() {
      this.currentModel = null;
      this.enablePhysics = true;
      this.physicsStrength = 1.0;
      this.animationModule = {
        startLipSync(value) { calls.push(['mmd-speaking', value === analyser]); },
        stopLipSync() { calls.push(['mmd-stop-speaking']); },
      };
    }
    async init() { calls.push(['mmd-init']); }
    async loadModel(model) {
      this.currentModel = {};
      calls.push(['mmd-model', model, this.enablePhysics, this.physicsStrength]);
    }
    applySettings(settings) {
      calls.push([
        'mmd-settings-apply', settings,
        Object.prototype.hasOwnProperty.call(settings || {}, 'physics'),
      ]);
    }
    async loadAnimation(animation) {
      calls.push(['mmd-idle-load', animation]);
      const failure = nextMmdAnimationFailure;
      nextMmdAnimationFailure = null;
      const gate = nextMmdAnimationGate;
      nextMmdAnimationGate = null;
      const notify = onNextMmdAnimationLoad;
      onNextMmdAnimationLoad = null;
      notify?.();
      if (gate) await gate;
      if (failure) throw failure;
    }
    playAnimation(mode) { calls.push(['mmd-idle-play', mode]); }
    onWindowResize() { calls.push(['mmd-resize']); }
    setEmotion(mood) { calls.push(['mmd-emotion', mood]); }
    pauseRendering() { calls.push(['mmd-pause']); }
    resumeRendering() { calls.push(['mmd-resume']); }
    async dispose() { await recordRendererDispose('mmd'); }
  }

  class PNGTuberManagerMock {
    async load(config) { calls.push(['pngtuber-model', config.idle_image, config.mirror]); }
    setSpeaking(active) { calls.push(['pngtuber-speaking', active]); }
    setState(name) { calls.push(['pngtuber-emotion', name]); }
    pauseRendering() { calls.push(['pngtuber-pause']); }
    resumeRendering() { calls.push(['pngtuber-resume']); }
    show() {}
    async dispose() { await recordRendererDispose('pngtuber'); }
  }

  const characters = {
    'Live Neko': {
      api_key: 'secret-key',
      system_prompt: 'secret prompt',
      live2d_idle_animation: '/animations/live2d-legacy.motion3.json',
      _reserved: {
        avatar: {
          model_type: 'live2d',
          live2d: {
            model_path: 'unresolved.json',
            idle_animation: '/animations/Live2D-Idle.motion3.json',
          },
        },
      },
    },
    'Live Clear Neko': {
      live2d_idle_animation: '/animations/live2d-idle.motion3.json',
      _reserved: {
        avatar: {
          model_type: 'live2d',
          live2d: { model_path: 'clear-live.json', idle_animation: null },
        },
      },
    },
    'Live Legacy Neko': {
      live2d_idle_animation: '/animations/live2d-legacy-only.motion3.json',
      _reserved: {
        avatar: {
          model_type: 'live2d',
          live2d: { model_path: 'legacy-live.json' },
        },
      },
    },
    'VRM Neko': {
      lighting: { ambient: 0.1 },
      idle_animation: ['/animations/vrm-stale-snake.vrma'],
      idleAnimation: '/animations/vrm-legacy.vrma',
      idleAnimations: ['/animations/vrm-legacy-list.vrma'],
      _reserved: {
        avatar: {
          model_type: 'live3d',
          live3d_sub_type: 'vrm',
          vrm: {
            model_path: 'avatar.vrm',
            lighting: { ambient: 0.7 },
            idle_animation: ['/animations/vrm-idle.vrma', '/animations/vrm-idle-2.vrma'],
          },
        },
      },
    },
    'VRM Legacy Neko': {
      lighting: { ambient: 0.4 },
      idleAnimations: ['/animations/vrm-legacy-only.vrma'],
      idleAnimation: '/animations/vrm-stale-singular.vrma',
      _reserved: {
        avatar: {
          model_type: 'live3d',
          live3d_sub_type: 'vrm',
          vrm: { model_path: 'legacy-avatar.vrm' },
        },
      },
    },
    'VRM Snake Legacy Neko': {
      idle_animation: ['/animations/vrm-snake-only.vrma'],
      idleAnimations: ['/animations/vrm-stale-camel-list.vrma'],
      idleAnimation: '/animations/vrm-stale-camel-singular.vrma',
      _reserved: {
        avatar: {
          model_type: 'live3d',
          live3d_sub_type: 'vrm',
          vrm: { model_path: 'snake-legacy-avatar.vrm' },
        },
      },
    },
    'VRM Clear Neko': {
      lighting: { ambient: 0.9 },
      idleAnimation: '/animations/vrm-stale.vrma',
      _reserved: {
        avatar: {
          model_type: 'live3d',
          live3d_sub_type: 'vrm',
          vrm: { model_path: 'clear-avatar.vrm', lighting: null, idle_animation: [] },
        },
      },
    },
    'MMD Neko': {
      mmd_idle_animations: ['/animations/mmd-stale-list.vmd'],
      mmd_idle_animation: '/animations/mmd-stale-single.vmd',
      _reserved: {
        avatar: {
          model_type: 'live3d',
          live3d_sub_type: 'mmd',
          mmd: {
            model_path: 'avatar.pmx',
            idle_animation: ['/animations/mmd-idle.vmd', '/animations/mmd-idle-2.vmd'],
          },
        },
      },
    },
    'MMD Clear Neko': {
      mmd_idle_animations: ['/animations/mmd-clear-stale-list.vmd'],
      mmd_idle_animation: '/animations/mmd-stale.vmd',
      _reserved: {
        avatar: {
          model_type: 'live3d',
          live3d_sub_type: 'mmd',
          mmd: { model_path: 'clear-avatar.pmx', idle_animation: null },
        },
      },
    },
    'MMD Legacy Neko': {
      mmd_idle_animations: ['/animations/mmd-legacy-list.vmd'],
      mmd_idle_animation: '/animations/mmd-stale-singular.vmd',
      _reserved: {
        avatar: {
          model_type: 'live3d',
          live3d_sub_type: 'mmd',
          mmd: { model_path: 'legacy-avatar.pmx' },
        },
      },
    },
    'PNG Neko': {
      pngtuber: { idle_image: '/avatars/legacy.png', mirror: false },
      _reserved: {
        avatar: {
          model_type: 'pngtuber',
          pngtuber: {
            idle_image: '/avatars/idle.png',
            talking_image: '/avatars/talk.png',
            mirror: true,
          },
        },
      },
    },
  };
  const mmdSettingsByName = {
    'MMD Neko': {
      lighting: { ambientIntensity: 0.45 },
      rendering: { exposure: 1.25 },
      physics: { enabled: false, strength: 1.6 },
      cursorFollow: { enabled: true, intensity: 0.7 },
    },
  };
  let liveModelFetchGate = null;
  let onLiveModelFetch = null;

  const fetchImpl = async (url) => {
    const target = String(url);
    if (target === '/api/characters') return jsonResponse({ 猫娘: characters, 当前猫娘: 'Live Neko' });
    if (target === '/api/characters/current_catgirl') return jsonResponse({ current_catgirl: 'Live Neko' });
    if (target.includes('/api/characters/current_live2d_model?')) {
      return jsonResponse({ success: true, model_info: { path: '/resolved/live.model3.json' } });
    }
    if (target === '/resolved/live.model3.json') {
      onLiveModelFetch?.();
      if (liveModelFetchGate) await liveModelFetchGate;
      return jsonResponse({ Version: 3, FileReferences: {} });
    }
    const mmdSettingsMatch = target.match(/^\/api\/characters\/catgirl\/([^/]+)\/mmd_settings$/);
    if (mmdSettingsMatch) {
      const name = decodeURIComponent(mmdSettingsMatch[1]);
      calls.push(['mmd-settings-fetch', name, target]);
      const failure = nextMmdSettingsFailure;
      nextMmdSettingsFailure = null;
      const gate = nextMmdSettingsGate;
      nextMmdSettingsGate = null;
      const notify = onNextMmdSettingsFetch;
      onNextMmdSettingsFetch = null;
      notify?.();
      if (gate) await gate;
      if (failure) throw failure;
      return jsonResponse({ success: true, settings: mmdSettingsByName[name] || {} });
    }
    throw new Error(`unexpected fetch: ${target}`);
  };

  const windowMock = {
    console: { warn() {}, error() {} },
    document: { getElementById: (id) => elements[id] || null },
    fetch: fetchImpl,
    AbortController,
    appState: { globalAnalyser: analyser },
    lanlan_config: { vrmIdleAnimation: '/animations/global-stale.vrma' },
    PIXI: { live2d: {} },
    Live2DManager: Live2DManagerMock,
    VRMManager: VRMManagerMock,
    MMDManager: MMDManagerMock,
    PNGTuberManager: PNGTuberManagerMock,
    vrmModuleLoaded: true,
    mmdModuleLoaded: true,
    convertVRMModelPath: (value) => `/vrm-resolved/${value}`,
    _mmdConvertPath: (value) => `/mmd-resolved/${value}`,
    fetchMMDConfig: async () => true,
    ResizeObserver: ResizeObserverMock,
    setTimeout(callback, delay) { return setTimeout(callback, delay); },
    clearTimeout(timer) { activeTimeouts.delete(timer); clearTimeout(timer); },
    clearInterval(timer) { activeIntervals.delete(timer); clearInterval(timer); },
    addEventListener(type, handler) {
      if (!listeners.has(type)) listeners.set(type, new Set());
      listeners.get(type).add(handler);
    },
    removeEventListener(type, handler) { listeners.get(type)?.delete(handler); },
    requestAnimationFrame(callback) {
      const id = nextFrame++;
      frames.set(id, callback);
      return id;
    },
    cancelAnimationFrame(id) { frames.delete(id); },
  };
  const context = vm.createContext({
    window: windowMock,
    console: windowMock.console,
    setTimeout,
    clearTimeout,
    AbortController,
    URL,
    encodeURIComponent,
  });
  vm.runInContext(fs.readFileSync(genericPath, 'utf8'), context, { filename: genericPath });
  vm.runInContext(fs.readFileSync(drawingPath, 'utf8'), context, { filename: drawingPath });

  const host = windowMock.NekoMiniGameDrawingAvatarHost.create({
    windowImpl: windowMock,
    documentImpl: windowMock.document,
    fetchImpl,
    avatarRuntime: windowMock.NekoMiniGameAvatarHost,
  });
  const names = await host.listCharacters();
  const current = await host.getCurrentCharacter();
  assert(Object.isFrozen(names) && names.length === 11, 'character names were not bounded and frozen');
  assert(Object.isFrozen(current) && Object.isFrozen(current.model),
    'current character descriptor was not deeply frozen');
  assert(current.name === 'Live Neko'
    && current.model.type === 'live2d'
    && current.model.path === '/resolved/live.model3.json',
  'Live2D descriptor did not use the resolved character model path');
  assert(current.api_key === undefined && current.system_prompt === undefined
    && JSON.stringify(current).includes('secret') === false,
  'character secrets crossed the trusted Avatar boundary');

  for (const inheritedName of [
    'constructor', 'toString', 'valueOf', 'hasOwnProperty', '__proto__',
  ]) {
    assert(await host.getCharacter(inheritedName) === null,
      `unknown character ${inheritedName} resolved through Object.prototype`);
  }
  Object.defineProperty(characters, '__proto__', {
    enumerable: true,
    configurable: true,
    value: {
      'Injected Neko': {
        _reserved: {
          avatar: {
            model_type: 'live3d',
            live3d_sub_type: 'vrm',
            vrm: { model_path: '/attacker/injected.vrm' },
          },
        },
      },
    },
  });
  assert(await host.getCharacter('Injected Neko') === null,
    'an enumerable __proto__ character polluted the trusted catalog lookup');
  delete characters.__proto__;

  const descriptors = new Map();
  for (const [name, expectedType] of [
    ['Live Neko', 'live2d'],
    ['Live Clear Neko', 'live2d'],
    ['Live Legacy Neko', 'live2d'],
    ['VRM Neko', 'vrm'],
    ['VRM Legacy Neko', 'vrm'],
    ['VRM Snake Legacy Neko', 'vrm'],
    ['VRM Clear Neko', 'vrm'],
    ['MMD Neko', 'mmd'],
    ['MMD Clear Neko', 'mmd'],
    ['MMD Legacy Neko', 'mmd'],
    ['PNG Neko', 'pngtuber'],
  ]) {
    const descriptor = name === 'Live Neko' ? current : await host.getCharacter(name);
    assert(descriptor?.model?.type === expectedType, `${expectedType} descriptor was not normalized`);
    if (name === 'Live Neko') {
      const serialized = JSON.stringify(descriptor).toLowerCase();
      assert(serialized.includes('live2d-idle') === false
        && serialized.includes('live2d-legacy') === false,
      'private Live2D motion paths crossed the public Avatar descriptor boundary');
    }
    if (name === 'VRM Neko') {
      const serialized = JSON.stringify(descriptor);
      assert(serialized.includes('vrm-idle') === false
        && serialized.includes('vrm-legacy') === false
        && serialized.includes('ambient') === false,
      'private VRM lighting or motion settings crossed the public Avatar descriptor boundary');
    }
    if (name === 'MMD Neko') {
      const serialized = JSON.stringify(descriptor);
      assert(serialized.includes('mmd-idle') === false
        && serialized.includes('physics') === false
        && serialized.includes('cursorFollow') === false,
      'private MMD settings crossed the public Avatar descriptor boundary');
    }
    if (name === 'PNG Neko') {
      assert(JSON.stringify(descriptor).includes('mirror') === false,
        'private PNGTuber mirror settings crossed the public Avatar descriptor boundary');
    }
    descriptors.set(name, descriptor);
  }

  function mountConfig(characterName, model) {
    return {
      slot: 'drawing-guess-character',
      ...(characterName ? { characterName } : {}),
      model,
      viewport: { mode: 'container' },
      fit: { mode: 'contain', align: 'center', padding: 0, scaleMultiplier: 1 },
      resize: { mode: 'container' },
    };
  }

  const rendererCallsBeforeAttacks = calls.length;
  const forgedNameError = await rejection(host.mount(mountConfig('Forged Neko', current.model)));
  const forgedPathError = await rejection(host.mount(mountConfig(
    'Live Neko', { type: 'live2d', path: '/attacker/model.model3.json' },
  )));
  const implicitCurrentPathError = await rejection(host.mount(mountConfig(
    '', { type: 'live2d', path: '/attacker/current.model3.json' },
  )));
  assert(forgedNameError?.code === 'model_not_allowed'
    && forgedPathError?.code === 'model_not_allowed'
    && implicitCurrentPathError?.code === 'model_not_allowed',
  'forged character names or arbitrary Avatar paths crossed the trusted catalog boundary');
  assert(calls.length === rendererCallsBeforeAttacks,
    'a rejected Avatar model reached a renderer constructor or loader');

  for (const [name, expectedType] of [
    ['Live Neko', 'live2d'],
    ['Live Clear Neko', 'live2d'],
    ['Live Legacy Neko', 'live2d'],
    ['VRM Neko', 'vrm'],
    ['VRM Legacy Neko', 'vrm'],
    ['VRM Snake Legacy Neko', 'vrm'],
    ['VRM Clear Neko', 'vrm'],
    ['MMD Neko', 'mmd'],
    ['MMD Clear Neko', 'mmd'],
    ['MMD Legacy Neko', 'mmd'],
    ['PNG Neko', 'pngtuber'],
  ]) {
    const descriptor = descriptors.get(name);
    const controller = await host.mount({
      ...mountConfig(name, descriptor.model),
    });
    if (name === 'Live Neko') {
      const replacementError = await rejection(controller.setModel({
        type: 'live2d', path: '/attacker/replacement.model3.json',
      }));
      assert(replacementError?.code === 'model_not_allowed',
        'controller.setModel accepted a model outside its trusted character binding');
    }
    const initialView = controller.getState().view;
    assert(initialView.scale === 325.63 && initialView.x === -0.96 && initialView.y === 66.41,
      `${expectedType} controller did not use the drawing game's configured default view`);
    await controller.setView({ scale: 190, x: 2, y: 28 });
    await controller.setSpeaking(true);
    await controller.setEmotion('happy');
    await controller.pause();
    assert(controller.getState().paused === true, `${expectedType} controller did not enter paused state`);
    await controller.resume();
    assert(controller.getState().paused === false, `${expectedType} controller did not resume`);
    await controller.setSpeaking(false);
    await controller.dispose();
  }

  assert(calls.some((entry) => entry[0] === 'live2d-model' && entry[1] === '/resolved/live.model3.json')
    && calls.some((entry) => entry[0] === 'vrm-model')
    && calls.some((entry) => entry[0] === 'mmd-model')
    && calls.some((entry) => entry[0] === 'pngtuber-model'),
  'the four Avatar renderer types did not follow symmetric host-owned loading paths');
  const firstLive2DModel = calls.findIndex((entry) => entry[0] === 'live2d-model');
  const firstLive2DIdleLoad = calls.findIndex((entry) => entry[0] === 'live2d-idle-load');
  const firstLive2DIdleLoop = calls.findIndex((entry) => entry[0] === 'live2d-idle-loop');
  const firstLive2DIdlePlay = calls.findIndex((entry) => entry[0] === 'live2d-idle-play');
  assert(firstLive2DModel >= 0 && firstLive2DModel < firstLive2DIdleLoad
    && firstLive2DIdleLoad < firstLive2DIdleLoop
    && firstLive2DIdleLoop < firstLive2DIdlePlay
    && calls[firstLive2DModel][2] === true
    && calls[firstLive2DIdleLoad][1] === 'PreviewAll'
    && calls[firstLive2DIdleLoad][2] === 1
    && calls[firstLive2DIdleLoad][3] === '/animations/live2d-idle.motion3.json'
    && calls[firstLive2DIdlePlay][1] === 'PreviewAll'
    && calls[firstLive2DIdlePlay][2] === 1
    && calls[firstLive2DIdlePlay][3] === 1
    && calls.some((entry) => entry[0] === 'live2d-expression' && entry[1] === 'Idle')
    && calls.some((entry) => entry[0] === 'live2d-setup-idle-scheduler'
      && entry[1] === 'live2d-idle.motion3.json')
    && !calls.some((entry) => entry.includes('/animations/live2d-legacy.motion3.json')),
  'Live2D did not load, loop, and play the canonical configured idle motion by runtime index');
  const vrmInit = calls.find((entry) => entry[0] === 'vrm-init');
  const vrmModel = calls.find((entry) => entry[0] === 'vrm-model');
  assert(vrmInit?.[1] === 0.7
    && vrmModel?.[1] === '/vrm-resolved/avatar.vrm'
    && vrmModel?.[2] === '/animations/vrm-idle.vrma'
    && Array.isArray(vrmModel?.[3])
    && vrmModel[3][1] === '/animations/vrm-idle-2.vrma'
    && !calls.some((entry) => entry.includes('/animations/vrm-stale-snake.vrma')
      || entry.includes('/animations/vrm-legacy.vrma')
      || entry.includes('/animations/vrm-legacy-list.vrma')),
  'VRM did not prefer canonical lighting and idle animation settings');
  const legacyVrmModel = calls.find((entry) => entry[0] === 'vrm-model'
    && entry[1] === '/vrm-resolved/legacy-avatar.vrm');
  const snakeLegacyVrmModel = calls.find((entry) => entry[0] === 'vrm-model'
    && entry[1] === '/vrm-resolved/snake-legacy-avatar.vrm');
  const clearedVrmModel = calls.find((entry) => entry[0] === 'vrm-model'
    && entry[1] === '/vrm-resolved/clear-avatar.vrm');
  assert(legacyVrmModel?.[2] === '/animations/vrm-legacy-only.vrma'
    && legacyVrmModel?.[4] === 0.4
    && !calls.some((entry) => entry.includes('/animations/vrm-stale-singular.vrma')),
  'VRM plural legacy idle animation priority or lighting compatibility was lost');
  assert(snakeLegacyVrmModel?.[2] === '/animations/vrm-snake-only.vrma'
    && !calls.some((entry) => entry.includes('/animations/vrm-stale-camel-list.vrma')
      || entry.includes('/animations/vrm-stale-camel-singular.vrma')),
  `VRM snake-case legacy idle animation priority was lost: ${JSON.stringify(snakeLegacyVrmModel)}`);
  assert(clearedVrmModel?.[2] === '/static/vrm/animation/wait03.vrma.gz'
    && Array.isArray(clearedVrmModel?.[3]) && clearedVrmModel[3].length === 0
    && clearedVrmModel?.[4] === undefined
    && clearedVrmModel?.[5] === '/static/vrm/animation/wait03.vrma.gz'
    && !calls.some((entry) => entry.includes('/animations/vrm-stale.vrma')
      || entry.includes('/animations/global-stale.vrma')),
  'explicit empty canonical VRM settings revived stale legacy values');
  const live2dModelCalls = calls.filter((entry) => entry[0] === 'live2d-model');
  assert(live2dModelCalls.some((entry) => entry[2] === true)
    && live2dModelCalls.some((entry) => entry[2] === false)
    && calls.some((entry) => entry[0] === 'live2d-idle-load'
      && entry[2] === 2 && entry[3] === '/animations/live2d-legacy-only.motion3.json'),
  'Live2D legacy idle compatibility or explicit canonical clearing was lost');
  const firstMmdModel = calls.findIndex((entry) => entry[0] === 'mmd-model');
  const firstMmdInit = calls.findIndex((entry) => entry[0] === 'mmd-init');
  const firstMmdSettingsFetch = calls.findIndex((entry) => entry[0] === 'mmd-settings-fetch');
  const firstMmdSettingsApply = calls.findIndex((entry) => entry[0] === 'mmd-settings-apply');
  const firstMmdIdleLoad = calls.findIndex((entry) => entry[0] === 'mmd-idle-load');
  const firstMmdIdlePlay = calls.findIndex((entry) => entry[0] === 'mmd-idle-play');
  assert(firstMmdInit >= 0 && firstMmdInit < firstMmdSettingsFetch
    && firstMmdSettingsFetch < firstMmdModel
    && firstMmdModel < firstMmdSettingsApply
    && firstMmdSettingsApply < firstMmdIdleLoad
    && firstMmdIdleLoad < firstMmdIdlePlay
    && calls[firstMmdSettingsFetch][1] === 'MMD Neko'
    && calls[firstMmdSettingsFetch][2]
      === '/api/characters/catgirl/MMD%20Neko/mmd_settings'
    && calls[firstMmdModel][2] === false
    && calls[firstMmdModel][3] === 1.6
    && calls[firstMmdSettingsApply][2] === false
    && calls[firstMmdSettingsApply][1]?.lighting?.ambientIntensity === 0.45
    && calls[firstMmdSettingsApply][1]?.rendering?.exposure === 1.25
    && calls[firstMmdSettingsApply][1]?.cursorFollow?.enabled === true
    && calls[firstMmdIdleLoad][1] === '/animations/mmd-idle.vmd'
    && calls[firstMmdIdlePlay][1] === 'idle'
    && !calls.some((entry) => entry[0] === 'mmd-idle-load'
      && entry[1] === '/animations/mmd-idle-2.vmd'),
  'MMD did not apply saved settings in the required init/load/apply/idle order');
  assert(calls.some((entry) => entry[0] === 'mmd-model'
    && entry[1] === '/mmd-resolved/avatar.pmx')
    && !calls.some((entry) => entry.includes('/animations/mmd-stale-list.vmd')
      || entry.includes('/animations/mmd-stale-single.vmd')
      || entry.includes('/animations/mmd-clear-stale-list.vmd')
      || entry.includes('/animations/mmd-stale.vmd')),
  'MMD did not prefer its canonical model path or respect an explicit idle clear');
  const legacyMmdModel = calls.findIndex((entry) => entry[0] === 'mmd-model'
    && entry[1] === '/mmd-resolved/legacy-avatar.pmx');
  const legacyMmdIdleLoad = calls.findIndex((entry, index) => index > legacyMmdModel
    && entry[0] === 'mmd-idle-load');
  assert(legacyMmdModel >= 0 && legacyMmdIdleLoad > legacyMmdModel
    && calls[legacyMmdIdleLoad][1] === '/animations/mmd-legacy-list.vmd'
    && !calls.some((entry) => entry.includes('/animations/mmd-stale-singular.vmd')),
  'MMD plural legacy idle animation was overridden by the stale singular field');
  assert(calls.some((entry) => entry[0] === 'pngtuber-model'
    && entry[1] === '/avatars/idle.png' && entry[2] === true),
  'PNGTuber did not preserve the canonical mirror setting');
  assert(calls.some((entry) => entry[0] === 'live2d-mouth')
    && calls.some((entry) => entry[0] === 'vrm-speaking' && entry[1] === true)
    && calls.some((entry) => entry[0] === 'mmd-speaking' && entry[1] === true)
    && calls.some((entry) => entry[0] === 'pngtuber-speaking' && entry[1] === true),
  'the four Avatar renderer types did not follow symmetric host-owned speaking paths');
  assert(calls.some((entry) => entry[0] === 'live2d-emotion' && entry[1] === 'happy')
    && calls.some((entry) => entry[0] === 'vrm-emotion' && entry[1] === 'happy')
    && calls.some((entry) => entry[0] === 'mmd-emotion' && entry[1] === 'happy')
    && calls.some((entry) => entry[0] === 'pngtuber-emotion' && entry[1] === 'happy'),
  'the four Avatar renderer types did not follow symmetric host-owned mood paths');
  assert(calls.some((entry) => entry[0] === 'live2d-pause')
    && calls.some((entry) => entry[0] === 'live2d-resume')
    && calls.some((entry) => entry[0] === 'vrm-pause')
    && calls.some((entry) => entry[0] === 'vrm-resume')
    && calls.some((entry) => entry[0] === 'mmd-pause')
    && calls.some((entry) => entry[0] === 'mmd-resume')
    && calls.some((entry) => entry[0] === 'pngtuber-pause')
    && calls.some((entry) => entry[0] === 'pngtuber-resume'),
  'the four Avatar renderer types did not follow symmetric host-owned pause/resume paths');
  assert(calls.some((entry) => entry[0] === 'vrm-dispose-end')
    && calls.some((entry) => entry[0] === 'mmd-dispose-end')
    && calls.some((entry) => entry[0] === 'pngtuber-dispose-end'),
  'the non-Live2D renderers did not complete their asynchronous disposal paths');
  assert(activeIntervals.size === 0 && activeTimeouts.size === 0
    && (listeners.get('resize')?.size || 0) === 0
    && (listeners.get('electron-display-changed')?.size || 0) === 0,
  `Live2D disposal leaked resources: intervals=${[...activeIntervals]}, `
    + `timeouts=${[...activeTimeouts]}, resize=${listeners.get('resize')?.size || 0}, `
    + `display=${listeners.get('electron-display-changed')?.size || 0}`);
  assert(calls.some((entry) => entry[0] === 'live2d-remove-model')
    && calls.some((entry) => entry[0] === 'live2d-model-dispose')
    && calls.some((entry) => entry[0] === 'live2d-pixi-dispose' && entry[1] === false),
  'Live2D disposal did not retire the model and PIXI runtime while preserving the host canvas');

  const live2dDescriptor = descriptors.get('Live Neko');
  let releaseLive2DExpression;
  nextLive2DExpressionGate = new Promise((resolve) => { releaseLive2DExpression = resolve; });
  let expressionGatedMountSettled = false;
  const expressionGatedMount = host.mount(mountConfig('Live Neko', live2dDescriptor.model));
  expressionGatedMount.then(() => { expressionGatedMountSettled = true; });
  await new Promise((resolve) => setImmediate(resolve));
  const expressionDidNotGateMount = expressionGatedMountSettled;
  releaseLive2DExpression();
  const expressionGatedController = await expressionGatedMount;
  assert(expressionDidNotGateMount,
    'optional Live2D Idle expression loading blocked Avatar renderer readiness');
  expressionGatedController.dispose();
  await new Promise((resolve) => setImmediate(resolve));

  for (const failureMode of ['reject', 'empty']) {
    const optionalMotionDisposalsBefore = calls.filter(
      (entry) => entry[0] === 'live2d-model-dispose'
    ).length;
    const optionalMotionPlaysBefore = calls.filter(
      (entry) => entry[0] === 'live2d-idle-play'
    ).length;
    const idleExpressionsBefore = calls.filter(
      (entry) => entry[0] === 'live2d-expression' && entry[1] === 'Idle'
    ).length;
    const idleSchedulersBefore = calls.filter(
      (entry) => entry[0] === 'live2d-setup-idle-scheduler'
    ).length;
    if (failureMode === 'reject') {
      nextLive2DIdleFailure = new Error('broken_optional_live2d_motion');
    } else {
      nextLive2DIdleEmptyResult = true;
    }
    const resilientLive2D = await host.mount(mountConfig('Live Neko', live2dDescriptor.model));
    assert(resilientLive2D.getState().ready === true,
      `${failureMode} optional Live2D idle motion prevented the model from becoming ready`);
    assert(calls.filter((entry) => entry[0] === 'live2d-model-dispose').length
      === optionalMotionDisposalsBefore,
    `${failureMode} optional Live2D idle motion disposed a usable model`);
    assert(calls.filter((entry) => entry[0] === 'live2d-idle-play').length
      === optionalMotionPlaysBefore,
    `${failureMode} Live2D idle motion was played after it failed to load`);
    assert(calls.filter((entry) => entry[0] === 'live2d-expression' && entry[1] === 'Idle').length
      === idleExpressionsBefore + 1,
    `${failureMode} optional Live2D motion did not preserve the Idle expression`);
    const idleSchedulers = calls.filter(
      (entry) => entry[0] === 'live2d-setup-idle-scheduler'
    );
    assert(idleSchedulers.length === idleSchedulersBefore + 1
      && idleSchedulers.at(-1)[1] === undefined,
    `${failureMode} optional Live2D motion did not restore the default idle scheduler`);
    resilientLive2D.dispose();
    await new Promise((resolve) => setImmediate(resolve));
  }

  const staleLive2D = await host.mount(mountConfig('Live Neko', live2dDescriptor.model));
  const staleLive2DPlaysBefore = calls.filter((entry) => entry[0] === 'live2d-idle-play').length;
  let releaseLive2DIdle;
  nextLive2DIdleGate = new Promise((resolve) => { releaseLive2DIdle = resolve; });
  const live2DIdleStarted = new Promise((resolve) => { onNextLive2DIdleLoad = resolve; });
  const staleLive2DReload = staleLive2D.setModel(live2dDescriptor.model);
  await withTimeout(
    live2DIdleStarted,
    'timed out waiting for the stale Live2D idle motion load to start',
  );
  const staleLive2DDisposalsBefore = calls.filter(
    (entry) => entry[0] === 'live2d-model-dispose'
  ).length;
  staleLive2D.dispose();
  const staleLive2DError = await withTimeout(
    rejection(staleLive2DReload),
    'timed out waiting for the stale Live2D load to be cancelled',
  );
  assert(staleLive2DError?.code === 'disposed',
    'disposing during a pending Live2D idle motion did not cancel the stale model load');
  releaseLive2DIdle();
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));
  assert(calls.filter((entry) => entry[0] === 'live2d-model-dispose').length
    === staleLive2DDisposalsBefore + 1,
  'the Live2D manager waiting on an idle motion was not disposed exactly once');
  assert(calls.filter((entry) => entry[0] === 'live2d-idle-play').length
    === staleLive2DPlaysBefore,
  'a stale Live2D manager played its idle motion after disposal');

  const mmdDescriptor = descriptors.get('MMD Neko');
  const rejectedSettingsModelsBefore = calls.filter(
    (entry) => entry[0] === 'mmd-model'
  ).length;
  const rejectedSettingsDisposalsBefore = calls.filter(
    (entry) => entry[0] === 'mmd-dispose-start'
  ).length;
  nextMmdSettingsFailure = new Error('settings_unavailable');
  const resilientMmdSettings = await host.mount(mountConfig('MMD Neko', mmdDescriptor.model));
  assert(resilientMmdSettings.getState().ready === true
    && calls.filter((entry) => entry[0] === 'mmd-model').length
      === rejectedSettingsModelsBefore + 1,
  'a rejected optional MMD settings request prevented the model from becoming ready');
  assert(calls.filter((entry) => entry[0] === 'mmd-dispose-start').length
    === rejectedSettingsDisposalsBefore,
  'a rejected optional MMD settings request disposed a usable model');
  await resilientMmdSettings.dispose();

  const staleSettingsMmd = await host.mount(mountConfig('MMD Neko', mmdDescriptor.model));
  let releaseMmdSettings;
  nextMmdSettingsGate = new Promise((resolve) => { releaseMmdSettings = resolve; });
  const mmdSettingsStarted = new Promise((resolve) => { onNextMmdSettingsFetch = resolve; });
  const staleSettingsReload = staleSettingsMmd.setModel(mmdDescriptor.model);
  await withTimeout(
    mmdSettingsStarted,
    'timed out waiting for the stale MMD settings request to start',
  );
  const staleSettingsModelsBefore = calls.filter((entry) => entry[0] === 'mmd-model').length;
  const staleSettingsDisposalsBefore = calls.filter(
    (entry) => entry[0] === 'mmd-dispose-start'
  ).length;
  await staleSettingsMmd.dispose();
  releaseMmdSettings();
  const staleSettingsError = await withTimeout(
    rejection(staleSettingsReload),
    'timed out waiting for the stale MMD settings load to be cancelled',
  );
  assert(staleSettingsError?.code === 'disposed'
    && calls.filter((entry) => entry[0] === 'mmd-model').length === staleSettingsModelsBefore,
  'disposing during MMD settings loading did not stop the stale model load');
  assert(calls.filter((entry) => entry[0] === 'mmd-dispose-start').length
    === staleSettingsDisposalsBefore + 1,
  'the MMD manager waiting on saved settings was not disposed exactly once');

  const rejectedMotionDisposalsBefore = calls.filter(
    (entry) => entry[0] === 'mmd-dispose-start'
  ).length;
  const rejectedMotionPlaysBefore = calls.filter((entry) => entry[0] === 'mmd-idle-play').length;
  nextMmdAnimationFailure = new Error('broken_optional_motion');
  const resilientMmd = await host.mount(mountConfig('MMD Neko', mmdDescriptor.model));
  assert(resilientMmd.getState().ready === true,
    'a rejected optional MMD idle motion prevented the model from becoming ready');
  assert(calls.filter((entry) => entry[0] === 'mmd-dispose-start').length
    === rejectedMotionDisposalsBefore,
  'a rejected optional MMD idle motion disposed a usable model');
  assert(calls.filter((entry) => entry[0] === 'mmd-idle-play').length
    === rejectedMotionPlaysBefore,
  'a rejected MMD idle motion was played');
  resilientMmd.dispose();
  await new Promise((resolve) => setImmediate(resolve));

  const staleMmd = await host.mount(mountConfig('MMD Neko', mmdDescriptor.model));
  const staleMotionPlaysBefore = calls.filter((entry) => entry[0] === 'mmd-idle-play').length;
  let releaseMmdAnimation;
  nextMmdAnimationGate = new Promise((resolve) => { releaseMmdAnimation = resolve; });
  const mmdAnimationStarted = new Promise((resolve) => { onNextMmdAnimationLoad = resolve; });
  const staleMmdReload = staleMmd.setModel(mmdDescriptor.model);
  await withTimeout(
    mmdAnimationStarted,
    'timed out waiting for the stale MMD idle motion load to start',
  );
  const staleMotionDisposalsBefore = calls.filter(
    (entry) => entry[0] === 'mmd-dispose-start'
  ).length;
  staleMmd.dispose();
  const staleMmdError = await withTimeout(
    rejection(staleMmdReload),
    'timed out waiting for the stale MMD load to be cancelled',
  );
  assert(staleMmdError?.code === 'disposed',
    'disposing during a pending MMD motion did not cancel the stale model load');
  assert(calls.filter((entry) => entry[0] === 'mmd-dispose-start').length
    === staleMotionDisposalsBefore + 1,
  'the MMD manager waiting on an idle motion was not disposed');
  releaseMmdAnimation();
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));
  assert(calls.filter((entry) => entry[0] === 'mmd-idle-play').length
    === staleMotionPlaysBefore,
  'a stale MMD manager played its idle motion after disposal');

  const replacementSequenceStart = calls.length;
  let releaseVrmDisposal;
  disposeGates.vrm = new Promise((resolve) => { releaseVrmDisposal = resolve; });
  const gatedVrm = await host.mount(mountConfig('VRM Neko', descriptors.get('VRM Neko').model));
  const mmdLoadsBeforeReplacement = calls.filter((entry) => entry[0] === 'mmd-model').length;
  const gatedVrmDisposal = gatedVrm.dispose();
  const replacementMount = host.mount(mountConfig('MMD Neko', descriptors.get('MMD Neko').model));
  await new Promise((resolve) => setImmediate(resolve));
  assert(calls.some((entry) => entry[0] === 'vrm-dispose-start')
    && calls.filter((entry) => entry[0] === 'mmd-model').length === mmdLoadsBeforeReplacement + 1,
  'rapid character replacement did not preserve the upstream non-blocking disposal behavior');
  releaseVrmDisposal();
  await gatedVrmDisposal;
  disposeGates.vrm = null;
  const replacementController = await replacementMount;
  await new Promise((resolve) => setImmediate(resolve));
  const replacementSequence = calls.slice(replacementSequenceStart).map((entry) => entry[0]);
  assert(replacementSequence.indexOf('mmd-model')
    < replacementSequence.indexOf('vrm-dispose-end'),
  'the replacement renderer unexpectedly waited on retired asynchronous cleanup');
  await replacementController.dispose();

  assert(host.activeCount === 0, 'debug-style Avatar replacement leaked a controller');

  let releaseLiveModelFetch;
  liveModelFetchGate = new Promise((resolve) => { releaseLiveModelFetch = resolve; });
  const liveModelFetchStarted = new Promise((resolve) => {
    onLiveModelFetch = () => { onLiveModelFetch = null; resolve(); };
  });
  const liveManagersBeforeCancellation = live2dManagersCreated;
  const cancelledMount = host.mount(mountConfig('Live Neko', current.model));
  await withTimeout(
    liveModelFetchStarted,
    'timed out waiting for the cancellable Live2D model fetch to start',
  );
  const hostDisposal = host.dispose();
  const cancelledMountError = await withTimeout(
    rejection(cancelledMount),
    'timed out waiting for the pending Avatar mount to be cancelled',
  );
  assert(cancelledMountError?.code === 'disposed',
    'host disposal did not cancel a pending Avatar model load');
  assert(live2dManagersCreated === liveManagersBeforeCancellation,
    'a cancelled pending loader constructed a late Live2D manager');
  releaseLiveModelFetch();
  liveModelFetchGate = null;
  await withTimeout(hostDisposal, 'timed out waiting for Avatar host disposal');
  await new Promise((resolve) => setImmediate(resolve));
  assert(live2dManagersCreated === liveManagersBeforeCancellation
    && host.pendingCount === 0 && host.activeCount === 0
    && activeIntervals.size === 0 && activeTimeouts.size === 0
    && (listeners.get('resize')?.size || 0) === 0
    && (listeners.get('electron-display-changed')?.size || 0) === 0,
  `cancelled loader leaked resources: managers=${live2dManagersCreated}/${liveManagersBeforeCancellation}, `
    + `pending=${host.pendingCount}, active=${host.activeCount}, intervals=${[...activeIntervals]}, `
    + `timeouts=${[...activeTimeouts]}, resize=${listeners.get('resize')?.size || 0}, `
    + `display=${listeners.get('electron-display-changed')?.size || 0}`);

  await verifyConfiguredLive2DIdleReplay();
  process.stdout.write('mini-game Drawing Avatar host runtime test passed\n');
}

main().catch((error) => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exitCode = 1;
});
