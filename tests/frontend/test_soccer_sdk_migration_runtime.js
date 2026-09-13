// Exercise the actual soccer manifest/payload/start code through the real SDK
// and trusted host. Only HTTP, browser surfaces and gameplay rendering are fake.
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '../..');
const read = (name) => fs.readFileSync(path.join(root, name), 'utf8');
const page = read('static/game/games/soccer/soccer-demo.js');
const connectAnchor = 'window.NekoMiniGame.connect(';
const connectIndex = page.indexOf(connectAnchor);
assert.notEqual(connectIndex, -1, 'soccer-demo.js is missing the SDK connect anchor');
const manifestStart = connectIndex + connectAnchor.length;
const manifestEnd = page.indexOf('}, {', manifestStart);
assert.notEqual(manifestEnd, -1, 'soccer-demo.js is missing the manifest end anchor');
const manifest = vm.runInThisContext('(' + page.slice(manifestStart, manifestEnd + 1) + ')');
const response = (data) => new Response(JSON.stringify(data), { headers: { 'Content-Type': 'application/json' } });

async function verifyEarlyModuleResults() {
  for (const outcome of ['failed', 'ready', 'pagehide', 'initialization-failed']) {
    const listeners = new Map();
    const window = {
      vrmModuleLoaded: false,
      addEventListener(type, handler) {
        if (!listeners.has(type)) listeners.set(type, new Set());
        listeners.get(type).add(handler);
      },
      removeEventListener(type, handler) { listeners.get(type)?.delete(handler); },
    };
    let assetsDone = false;
    let mounts = 0;
    window.__SoccerLoading = { set() {}, done() { assetsDone = true; } };
    const context = vm.createContext({ window, document: {
      getElementById: () => ({ textContent: '', style: {} }),
    }, console: { log() {}, warn() {}, error() {} }, setTimeout() {},
    soccerGame: { disposed: false, capabilities: { require() {} } },
    replaceSoccerAvatar: async () => { mounts++; },
    ensureSoccerCharacterInfo: async () => ({ name: 'Neko' }),
    mountSoccerCharacterAvatar: async () => { mounts++; },
    });
    const observerStart = page.indexOf('  function observeSoccerVrmModules()');
    assert(observerStart >= 0, 'missing early module observer');
    vm.runInContext(page.slice(observerStart,
      page.indexOf('  const initializeSoccerPage', observerStart)), context);
    const loaderStart = page.indexOf('    async function loadSoccerAvatars()');
    vm.runInContext(page.slice(loaderStart, page.indexOf('    /* ═', loaderStart)), context);
    // The result arrives while settings initialization is still pending.
    if (outcome === 'initialization-failed') {
      context.initializeSoccerPage = async () => { throw new Error('settings_failed'); };
      const initializeStart = page.indexOf('  const runInitializeSoccerPage');
      vm.runInContext(page.slice(initializeStart, page.indexOf("  if (document.readyState", initializeStart))
        + '\nrunInitializeSoccerPage();', context);
      await new Promise(resolve => setImmediate(resolve));
    } else {
      window.vrmModuleLoaded = outcome === 'ready';
      const event = outcome === 'pagehide' ? outcome : `vrm-modules-${outcome}`;
      for (const handler of [...(listeners.get(event) || [])]) handler({ detail: { failedModules: ['fixture.js'] } });
    }
    let settled = false;
    const loading = vm.runInContext('loadSoccerAvatars()', context).then(() => { settled = true; });
    for (let i = 0; i < 10; i++) await new Promise(resolve => setImmediate(resolve));
    assert(settled, `${outcome}: early module result left the asset loader waiting forever`);
    await loading;
    assert.equal(assetsDone, outcome === 'failed' || outcome === 'ready');
    assert.equal(mounts, outcome === 'ready' ? 2 : 0);
    for (const handlers of listeners.values()) assert.equal(handlers.size, 0,
      `${outcome}: module observation retained listeners`);
  }
}

async function main() {
  await verifyEarlyModuleResults();
  const calls = [];
  const storage = new Map();
  const renderers = [];
  const mountAttempts = [];
  let mountGate = null;
  let mountGatePath = null;
  const unavailableModels = new Set();
  const listeners = new Map();
  const addEventListener = (type, handler) => {
    if (!listeners.has(type)) listeners.set(type, new Set());
    listeners.get(type).add(handler);
  };
  const removeEventListener = (type, handler) => listeners.get(type)?.delete(handler);
  const launch = {
    textContent: read('templates/soccer_demo.html').match(/<script[^>]+id="neko-minigame-host-launch"[^>]*>([\s\S]*?)<\/script>/)[1],
    remove() {},
  };
  const document = {
    hidden: false, visibilityState: 'visible', addEventListener, removeEventListener,
    getElementById: (id) => id === 'neko-minigame-host-launch' ? launch : null,
    createElement: () => ({ remove() {} }),
  };
  const window = {
    document, navigator: {}, location: { origin: 'http://localhost', search: '' },
    console, AbortController, setTimeout, clearTimeout,
    createSoccerAvatarHost: () => ({ async mount(config) {
      mountAttempts.push(config.model.path);
      if (mountGate && (!mountGatePath || mountGatePath === config.model.path)) await mountGate;
      if (unavailableModels.has(config.model.path)) throw new Error('asset_missing');
      if (config.model.path === '/broken.pmx') throw new Error('asset_failed');
      const renderer = { config, model: config.model, disposed: false, paused: false,
        async setModel(model) {
          if (['mmd', 'pngtuber'].includes(this.model?.type) || ['mmd', 'pngtuber'].includes(model.type)) {
            this.model = null;
          }
          if (unavailableModels.has(model.path)) throw new Error('asset_missing');
          if (model.path === '/broken.pmx') throw new Error('asset_failed');
          this.model = model;
        },
        setSpeechPlayback() {}, setSpeaking() {}, setEmotion() {}, focus() {},
        pause() { this.paused = true; }, resume() { this.paused = false; },
        getState() { return { model: this.model, paused: this.paused }; },
        dispose() { this.disposed = true; },
      };
      renderers.push(renderer);
      return renderer;
    }, dispose() {} }),
    setInterval: () => 1, clearInterval() {}, addEventListener, removeEventListener,
    lanlan_config: { lanlan_name: 'soccer_demo' },
    localStorage: {
      get length() { return storage.size; },
      key: (i) => [...storage.keys()][i] ?? null,
      getItem: (key) => storage.get(key) ?? null,
      setItem: (key, value) => storage.set(key, String(value)),
      removeItem: (key) => storage.delete(key),
    },
    nekoLocalMutationSecurity: {
      getMutationHeaders: async () => ({ 'X-CSRF-Token': 'test-token' }),
      peekCachedToken: () => 'test-token',
    },
    fetch: async (url, options = {}) => {
      const payload = options.body ? JSON.parse(options.body) : {};
      calls.push({ url, payload });
      if (url.includes('/character')) return response({ lanlan_name: 'test-character',
        model_type: 'vrm', vrm_path: '/test.vrm', live2d_path: '/fallback.model3.json',
        language: 'ja', language_preference_resolved: true });
      if (url.endsWith('/route/start')) return response({ ok: true, state: {
        game_route_active: true, lanlan_name: 'test-character',
        sdk_route_instance_id: payload.sdk_route_instance_id,
      } });
      if (url.endsWith('/context/read')) return response({ ok: true,
        scope_metadata: { 'pregame-context': { source: 'fallback', error: 'test-provider-unavailable' } }, scopes: {
        'pregame-context': { initialMood: 'happy', initialDifficulty: 'lv3', openingLine: 'Ready!' },
      } });
      if (url.endsWith('/chat')) return response({ ok: true, text: 'Ready!',
        control: { mood: 'happy', difficulty: 'lv3', reason: 'test' } });
      if (url.endsWith('/route/drain')) return response({ ok: true, outputs: [] });
      if (url.endsWith('/route/heartbeat')) return response({ ok: true, active: true });
      return response({ ok: true, lanlan_name: 'test-character' });
    },
  };
  const sandbox = global;
  Object.assign(sandbox, { window, document });
  const run = (name) => vm.runInThisContext(read(name), { filename: name });
  document.head = { appendChild(script) {
    document.currentScript = script;
    run('static/game/sdk/neko-minigame-same-origin-host.js');
    document.currentScript = null;
    script.onload?.();
  } };
  run('static/game/games/soccer/soccer-neko-host-registration.js');
  run('static/game/sdk/neko-minigame-same-origin-bootstrap.js');
  await window.nekoMiniGameSameOriginHostReady;
  run('static/game/games/soccer/soccer-neko-adapter.js');
  run('static/game/sdk/neko-minigame-sdk.js');
  const host = await window.createSoccerNekoAdapter({ audioHost: {} });
  const game = await window.NekoMiniGame.connect(manifest, { transport: host, windowImpl: window, documentImpl: document });
  try {
    storage.set('neko.soccerGameAudio.voiceMix', '75');
    storage.set('neko.soccer.surrenderReminderEnabled', 'false');
    await host.migrateLegacySettings(game);
    assert.equal((await game.storage.get('settings/voice-mix-percent')).data.value, 75);
    assert.equal((await game.storage.get('settings/surrender-reminder-enabled')).data.value, false);
    await game.storage.set('settings/voice-mix-percent', 0);
    await host.migrateLegacySettings(game);
    assert.equal((await game.storage.get('settings/voice-mix-percent')).data.value, 0);
    assert.equal(storage.get('neko.soccerGameAudio.voiceMix'), '75', 'legacy settings must remain recoverable');

    let appliedContext;
    Object.assign(sandbox, {
      soccerGame: game, _runtimeSessionId: () => game.runtime.session.id,
      _runtimeCharacterName: () => game.runtime.session.characterName,
      _soccerConversationCharacterName: () => 'test-character',
      _llm: { gameStarted: true, gameStartedAtEpochMs: 1234, gameMemoryTailCount: 6 },
      _isGameMemoryEnabled: () => true, _gameStartedElapsedMs: () => 20000,
      _isAccidentalGameEntryExit: () => false,
      MAX_GAME_MEMORY_TAIL_COUNT: 50, DEFAULT_GAME_MEMORY_TAIL_COUNT: 6,
      SoccerDemo: { _snapshot: () => ({ score: { player: 2, ai: 1 }, mood: 'happy' }) },
      _soccerGameMemoryPolicyPayload: (enabled) => ({ game_memory_enabled: enabled }),
      _conversationLanguagePayload: () => ({ language: 'zh' }),
      _gameRouteStartOptions: {}, _i18n: (_key, fallback) => fallback,
      resetSoccerSessionDebugLogEnableState() {},
      _enableSoccerSessionDebugLogAfterRouteStart: async () => {},
      _applyPreGameContext: (state) => {
        assert.equal(state.pre_game_context_source, 'fallback');
        assert.equal(state.pre_game_context_error, 'test-provider-unavailable');
        appliedContext = state.preGameContext;
      },
      _recordFallbackDiagnostic() {}, soccerRecoverableLog() {},
    });
    const install = (start, end) => {
      const from = page.indexOf(start);
      assert.notEqual(from, -1, `Missing start anchor: ${start}`);
      const to = page.indexOf(end, from);
      assert.notEqual(to, -1, `Missing end anchor: ${end}`);
      return vm.runInThisContext(page.slice(from, to));
    };
    install('const normalizeSoccerExplicitLanguage', 'const SOCCER_AVATAR_LAYOUT');
    const firstBinding = vm.runInThisContext('ensureSoccerCharacterInfo()');
    assert.equal(vm.runInThisContext('ensureSoccerCharacterInfo()'), firstBinding, 'concurrent loaders must share one query');
    const descriptor = await firstBinding;
    const avatarEvents = [];
    sandbox.emitEvent = (name, data) => avatarEvents.push({ name, data });
    const avatarLoaderAnchor = '    async function loadSoccerAvatars()';
    install('const SOCCER_AVATAR_LAYOUT', avatarLoaderAnchor);
    install('async function setPlayerAvatar(', 'function roundDebugNumber(');
    await sandbox.setPlayerAvatar({ type: 'vrm', path: '/first.vrm' });
    await sandbox.setPlayerAvatar({ type: 'vrm', path: '/second.vrm' });
    assert.equal(avatarEvents.length, 2, 'model replacement did not emit its completion event');
    assert.equal(renderers.length, 1, 'same-fit replacement should retain the controller');
    const characterQueryBeforeReplacements = host.getAvatarCharacter;
    host.getAvatarCharacter = async () => ({ ...descriptor,
      model: { type: 'mmd', path: '/first.pmx' },
      fallbackModels: [
        { type: 'pngtuber', path: '/image.png' }, { type: 'pngtuber', path: '/next.png' },
        { type: 'mmd', path: '/broken.pmx' }, { type: 'pngtuber', path: '/fallback.png' },
      ],
    });
    for (const [oldModel, failedModel] of [
      [{ type: 'live2d', path: '/old.model3.json' }, { type: 'vrm', path: '/missing.vrm' }],
      [{ type: 'vrm', path: '/old.vrm' }, { type: 'live2d', path: '/missing.model3.json' }],
      [{ type: 'pngtuber', path: '/image.png' }, { type: 'mmd', path: '/broken.pmx' }],
      [{ type: 'vrm', path: '/old.vrm' }, { type: 'mmd', path: '/broken.pmx' }],
      [{ type: 'live2d', path: '/old.model3.json' }, { type: 'pngtuber', path: '/next.png' }],
      [{ type: 'mmd', path: '/first.pmx' }, { type: 'mmd', path: '/broken.pmx' }],
      [{ type: 'mmd', path: '/first.pmx' }, { type: 'vrm', path: '/missing.vrm' }],
      [{ type: 'pngtuber', path: '/image.png' }, { type: 'live2d', path: '/missing.model3.json' }],
    ]) {
      await sandbox.setAiAvatar(oldModel);
      await window.__SoccerAiAvatarController.pause();
      const eventCount = avatarEvents.length;
      const attemptCount = mountAttempts.length;
      unavailableModels.add(failedModel.path);
      await assert.rejects(sandbox.setAiAvatar(failedModel), /asset_missing/);
      unavailableModels.delete(failedModel.path);
      const restored = window.__SoccerAiAvatarController;
      assert(restored && !restored.disposed, 'cross-fit failure left the AI blank');
      assert.deepEqual(restored.getState().model, oldModel);
      assert.deepEqual(renderers.at(-1).model, oldModel, 'recovery only retained stale SDK metadata');
      assert.equal(restored.getState().paused, true, 'cross-fit recovery lost pause');
      assert.equal(restored.config.fit.mode, ['vrm', 'mmd'].includes(oldModel.type) ? 'height' : 'contain');
      assert.equal(mountAttempts.length - attemptCount, 2, 'recovery must attempt the previous model once');
      assert.equal(avatarEvents.length, eventCount, 'failed replacement emitted success');
    }
    unavailableModels.add('/image.png');
    unavailableModels.add('/missing.vrm');
    const failedRecoveryAttempts = mountAttempts.length;
    await assert.rejects(sandbox.setAiAvatar({ type: 'vrm', path: '/missing.vrm' }), /asset_missing/);
    assert.equal(window.__SoccerAiAvatarController, null, 'failed recovery kept a disposed controller');
    assert.equal(mountAttempts.length - failedRecoveryAttempts, 2, 'failed recovery retried indefinitely');
    assert.equal(vm.runInThisContext('soccerAvatarChanging.ai'), false);
    unavailableModels.clear();
    await sandbox.setAiAvatar({ type: 'vrm', path: '/preserved.vrm' });
    const preservedAi = window.__SoccerAiAvatarController;
    preservedAi.pause();
    for (const model of [{ type: 'mmd', path: '/untrusted.pmx' },
      { type: 'pngtuber', path: '/untrusted.png' }]) {
      await assert.rejects(sandbox.setAiAvatar(model), /model_not_allowed/);
      assert.equal(window.__SoccerAiAvatarController, preservedAi, 'invalid path replaced the old controller');
      assert.equal(preservedAi.disposed, false, 'invalid path disposed the old controller');
      assert.equal(preservedAi.getState().paused, true, 'invalid path changed pause state');
      assert.equal(preservedAi.getState().model.path, '/preserved.vrm');
    }
    await sandbox.setAiAvatar({ type: 'mmd', path: '/first.pmx' });
    window.__SoccerAiAvatarController.pause();
    const oldAi = renderers.at(-1);
    await sandbox.setAiAvatar({ type: 'pngtuber', path: '/image.png' });
    assert.equal(oldAi.disposed, true, 'cross-fit replacement retained the old renderer');
    assert.equal(renderers.at(-1).config.fit.mode, 'contain');
    assert.equal(renderers.at(-1).paused, true, 'replacement lost the paused state');
    let releaseModel;
    mountGate = new Promise(resolve => { releaseModel = resolve; });
    mountGatePath = '/next.png';
    const changing = sandbox.setAiAvatar({ type: 'pngtuber', path: '/next.png' });
    await assert.rejects(sandbox.setAiAvatar({ type: 'vrm', path: '/competing.vrm' }), /busy/);
    releaseModel();
    await changing;
    mountGate = null;
    mountGatePath = null;
    await assert.rejects(sandbox.setAiAvatar({ type: 'mmd', path: '/broken.pmx' }), /asset_failed/);
    await sandbox.setAiAvatar({ type: 'vrm', path: '/recovered.vrm' });
    assert.equal(renderers.at(-1).config.fit.mode, 'height', 'failed mount retained the operation lock');
    await vm.runInThisContext(`mountSoccerCharacterAvatar({ model: {type:'mmd',path:'/broken.pmx'},
      fallbackModels:[{type:'pngtuber',path:'/fallback.png'},{type:'vrm',path:'/unused.vrm'}] })`);
    assert.equal(renderers.at(-1).model.path, '/fallback.png', 'fallback did not follow canonical order');
    host.getAvatarCharacter = characterQueryBeforeReplacements;
    window.__SoccerPlayerAvatarController.dispose();
    window.__SoccerAiAvatarController.dispose();
    assert.equal(descriptor.name, 'test-character');
    assert.equal(game.runtime.session.characterName, 'test-character', 'direct entry kept the placeholder identity');
    assert.equal(game.runtime.state, 'idle', 'binding prematurely started the route');
    assert.equal(vm.runInThisContext('soccerCharacterExplicitLanguage'), 'ja');
    assert.deepEqual(descriptor.model, { type: 'vrm', path: '/test.vrm' });
    assert.deepEqual(descriptor.fallbackModels, [{ type: 'live2d', path: '/fallback.model3.json' }]);
    assert.equal(calls.filter(c => c.url.includes('/character')).length, 1);
    install('function _gameRoutePayload(', 'async function _sendGameRouteHeartbeat(');
    install('async function _startGameRoute()', 'function _scoreDiffOf(');
    install('function _gameRouteEndPayload(', 'async function _endGameLLMSession(');
    game.runtime.configure({ payload: () => sandbox._gameRoutePayload(), heartbeat: { intervalMs: 60000 },
      outputs: { intervalMs: 60000 }, pageExit: false });
    await sandbox._startGameRoute();
    assert.equal(appliedContext.initialMood, 'happy');
    const start = calls.find((c) => c.url.endsWith('/route/start')).payload;
    assert.equal(start.lanlan_name, 'test-character');
    for (const request of calls.filter(c => c.url.endsWith('/context/read') || c.url.endsWith('/preload'))) {
      assert.equal(request.payload.lanlan_name, 'test-character', 'pregame request used the wrong identity');
    }
    assert.equal(start.game_started, true);
    assert.equal(start.game_started_elapsed_ms, 20000);
    assert.equal(start.currentState.score.player, 2);
    assert.equal(start.game_memory_enabled, true);
    assert.equal(start.game_memory_archive_enabled, true);
    assert.equal(start.game_memory_player_interaction_enabled, true);
    assert.equal(start.game_memory_event_reply_enabled, true);
    assert.equal(start.game_memory_postgame_context_enabled, true);
    assert.equal(game.memory.consent.locked, true);
    await assert.rejects(game.memory.configureConsent(false), { code: 'consent_locked' });
    const dialogue = await game.dialogue.request({ event: { kind: 'goal-scored' } });
    assert.equal(dialogue.data.control.mood, 'happy', 'valid soccer control must not trigger fallback');
    await game.runtime.pulse(true);
    await game.runtime.pollOutputs();
    for (const suffix of ['/route/heartbeat', '/route/drain']) {
      const payload = calls.find((c) => c.url.endsWith(suffix)).payload;
      assert.equal(payload.game_started, true);
      assert.equal(payload.game_memory_archive_enabled, true);
      assert.equal(payload.sdk_route_instance_id, start.sdk_route_instance_id);
    }
    await game.runtime.end(sandbox._gameRouteEndPayload(false, { reason: 'manual_user_exit', postgameProactive: false }));
    const end = calls.findLast((c) => c.url.endsWith('/soccer/end')).payload;
    assert.equal(end.reason, 'manual_user_exit');
    assert.equal(end.game_started, true);
    assert.equal(end.game_started_elapsed_ms, 20000);
    assert.equal(end.postgameProactive, false);
    assert.equal(end.game_memory_archive_enabled, true);
    assert.equal(end.currentState.score.player, 2);
    game.runtime.reset({ newSession: true });
    sandbox.resetSoccerCharacterInfo();
    sandbox._isGameMemoryEnabled = () => false;
    await sandbox._startGameRoute();
    assert.equal(calls.filter(c => c.url.includes('/character')).length, 2, 'restart did not rebind the character');
    const disabled = calls.findLast((c) => c.url.endsWith('/route/start')).payload;
    assert.equal(disabled.game_memory_enabled, false);
    assert.equal(disabled.game_memory_archive_enabled, false);
    assert.equal(disabled.game_memory_player_interaction_enabled, false);
    assert.equal(disabled.game_memory_event_reply_enabled, false);
    assert.equal(disabled.game_memory_postgame_context_enabled, false);
    await game.runtime.end({});
    game.runtime.reset({ newSession: true });
    sandbox.resetSoccerCharacterInfo();
    const configureConsent = host.configureGameMemoryConsent;
    host.configureGameMemoryConsent = async () => ({ ok: false });
    const startsBeforeRejection = calls.filter((c) => c.url.endsWith('/route/start')).length;
    await assert.rejects(sandbox._startGameRoute(), /memory_consent_failed/);
    assert.equal(calls.filter((c) => c.url.endsWith('/route/start')).length, startsBeforeRejection,
      'failed consent must not establish a route');
    host.configureGameMemoryConsent = configureConsent;
    const loadingStart = page.indexOf('window.__SoccerLoading = (() => {');
    vm.runInThisContext(page.slice(loadingStart, page.indexOf('void (async () => {', loadingStart)));
    window.__SoccerLoading.showStart();
    assert.equal(window.__SoccerLoading.canStart(), false, 'assets still loading');
    window.__SoccerLoading.done('assets');
    assert.equal(window.__SoccerLoading.canStart(), true);
    assert.equal(window.__SoccerLoading.startGame(), false, 'selection is not an established route');
    window.__SoccerLoading.beginStart();
    assert.equal(window.__SoccerLoading.canStart(), false, 'second click during startup is blocked');
    window.__SoccerLoading.done('route');
    assert.equal(window.__SoccerLoading.startGame(), true);
    window.__SoccerLoading.showStart();
    Object.assign(sandbox, {
      _prepareStartInFlight: false, gameMemoryToggle: { disabled: false },
      soccerGameAudio: { unlock: async () => {}, sync() {} },
    });
    sandbox._llm.gameStarted = false;
    install('async function _startGameFromStartScreen()', '// 注：之前这里有两段');
    host.configureGameMemoryConsent = async () => ({ ok: false });
    await sandbox._startGameFromStartScreen();
    assert.equal(window.__SoccerLoading.isReady(), false);
    assert.equal(window.__SoccerLoading.canStart(), true, 'failed consent allows an explicit retry');
    assert.equal(sandbox.gameMemoryToggle.disabled, false);
    assert.equal(sandbox._llm.gameStarted, false);
    host.configureGameMemoryConsent = configureConsent;
    // A reset must retire the page cache as well as the SDK query generation.
    const originalCharacterQuery = host.getAvatarCharacter;
    let releaseLateCharacter;
    host.getAvatarCharacter = () => new Promise(resolve => { releaseLateCharacter = resolve; });
    const lateBinding = vm.runInThisContext('ensureSoccerCharacterInfo()');
    for (let i = 0; i < 10 && !releaseLateCharacter; i++) await Promise.resolve();
    assert(releaseLateCharacter, 'delayed query did not start');
    game.runtime.reset({ newSession: true });
    sandbox.resetSoccerCharacterInfo();
    await assert.rejects(lateBinding);
    releaseLateCharacter({ name: 'retired-character', model: null });
    await Promise.resolve();
    assert.equal(window.__SoccerResolvedLanlanName, 'test-character', 'late query restored retired identity');
    host.getAvatarCharacter = async () => null;
    const startsBeforeMissingCharacter = calls.filter(c => c.url.endsWith('/route/start')).length;
    await assert.rejects(sandbox._startGameRoute(), /character_unavailable/);
    assert.equal(calls.filter(c => c.url.endsWith('/route/start')).length, startsBeforeMissingCharacter,
      'missing character still established a route');
    host.getAvatarCharacter = async () => ({ name: 'test-character', model: null,
      languagePreference: { resolved: true, locale: '' }, fallbackModels: [] });
    const cleared = await vm.runInThisContext('ensureSoccerCharacterInfo()');
    assert.equal(vm.runInThisContext('soccerCharacterExplicitLanguage'), '', 'explicitly cleared language kept the old locale');
    assert.equal(vm.runInThisContext('soccerCharacterLanguagePreferenceResolved'), true);
    assert.deepEqual(cleared.fallbackModels, [], 'missing fallback invented a default model');
    host.getAvatarCharacter = originalCharacterQuery;
    // Run the actual asset loader and startup sequence with storage held open.
    // The real SDK must not have its first character bind cancelled by reset.
    game.runtime.reset({ newSession: true });
    sandbox.resetSoccerCharacterInfo();
    window.__SoccerAiAvatarController = null;
    window.__SoccerPlayerAvatarController = null;
    window.vrmModuleLoaded = true;
    install('  function observeSoccerVrmModules()', '  const initializeSoccerPage');
    const previousGetElement = document.getElementById;
    document.getElementById = (id) => id === 'status'
      ? { textContent: '', style: {} } : previousGetElement(id);
    let releaseSettings;
    const settingsGate = new Promise(resolve => { releaseSettings = resolve; });
    const initialCharacterQueries = [];
    let assetsDone = false;
    host.getAvatarCharacter = () => new Promise(resolve => {
      initialCharacterQueries.push(() => resolve(descriptor));
    });
    let releaseQuickLines;
    const previousFetch = window.fetch;
    window.fetch = async (url, options) => {
      if (!url.endsWith('/quick-lines')) return previousFetch(url, options);
      calls.push({ url, payload: JSON.parse(options.body) });
      return new Promise(resolve => {
        releaseQuickLines = () => resolve(response({ ok: true, character: descriptor.name,
          lines: { 'goal-scored': ['Generated goal line'] }, missing: [] }));
      });
    };
    sandbox.LINES = { 'goal-scored': ['Built-in goal line'] };
    sandbox._conversationLanguagePayload = () => ({
      i18n_language: vm.runInThisContext('soccerCharacterExplicitLanguage'),
    });
    install('      const QUICK_LINE_KEYS = [', '      function triggerScene(');
    window.__SoccerLoading = {
      set() {}, done(part) { if (part === 'assets') assetsDone = true; },
    };
    Object.assign(sandbox, {
      _loadSurrenderReminderEnabled: () => settingsGate,
      _readSurrenderReminderEnabled: () => true,
      _setSurrenderReminderEnabled() {},
      _prepareGameForStartScreen: async () => {
        game.runtime.reset({ newSession: true });
        sandbox.resetSoccerCharacterInfo();
      },
    });
    install(avatarLoaderAnchor, '    /* ═');
    const startupFrom = page.indexOf('      await _loadSurrenderReminderEnabled();');
    const startupTo = page.indexOf('      // 注册 onSpeak', startupFrom);
    const startup = vm.runInThisContext(`(async () => {${page.slice(startupFrom, startupTo)}})()`);
    for (let i = 0; i < 10; i++) await new Promise(resolve => setImmediate(resolve));
    releaseSettings();
    await startup;
    for (let i = 0; i < 10; i++) await new Promise(resolve => setImmediate(resolve));
    assert(initialCharacterQueries.length, 'startup never queried the character');
    for (const releaseCharacter of initialCharacterQueries) releaseCharacter();
    for (let i = 0; i < 10 && !assetsDone; i++) await new Promise(resolve => setImmediate(resolve));
    assert.equal(assetsDone, true, 'startup did not settle the asset loader');
    assert(window.__SoccerAiAvatarController && !window.__SoccerAiAvatarController.disposed,
      'initial runtime reset cancelled the only AI avatar mount');
    assert.equal(window.__SoccerAiAvatarController.getState().model.path, descriptor.model.path);
    assert.equal(game.runtime.session.characterName, descriptor.name);
    assert.equal(game.runtime.state, 'idle', 'loading assets started a game route');
    for (let i = 0; i < 10 && !releaseQuickLines; i++) await new Promise(resolve => setImmediate(resolve));
    assert(releaseQuickLines, 'initial runtime reset cancelled quick-line generation without a retry');
    assert.equal(initialCharacterQueries.length, 1, 'startup loaders did not share the post-reset binding');
    const quickLineRequests = calls.filter(c => c.url.endsWith('/quick-lines'));
    assert.equal(quickLineRequests.length, 1);
    assert.equal(quickLineRequests[0].payload.lanlan_name, descriptor.name);
    assert.equal(quickLineRequests[0].payload.i18n_language, 'ja');
    assert.deepEqual(sandbox.LINES['goal-scored'], ['Built-in goal line'],
      'assets must become ready while generated lines are still pending');
    releaseQuickLines();
    for (let i = 0; i < 10 && sandbox.LINES['goal-scored'][0] !== 'Generated goal line'; i++) {
      await new Promise(resolve => setImmediate(resolve));
    }
    assert.deepEqual(sandbox.LINES['goal-scored'], ['Generated goal line']);
    assert.equal(game.runtime.state, 'idle', 'quick-line generation started a game route');
    window.fetch = previousFetch;
    host.getAvatarCharacter = originalCharacterQuery;
    await sandbox.setPlayerAvatar({ type: 'vrm', path: '/retry-player.vrm' });
    await sandbox.setAiAvatar({ type: 'vrm', path: '/retry-ai.vrm' });
    window.__SoccerAiAvatarController.pause();
    const retryPlayer = window.__SoccerPlayerAvatarController;
    const retryAi = window.__SoccerAiAvatarController;
    game.runtime.reset({ newSession: true });
    sandbox.resetSoccerCharacterInfo();
    assert.equal(retryPlayer.disposed, true, 'restart must release the player before rebinding');
    assert.equal(retryAi.disposed, true, 'restart must release the AI before rebinding');
    await sandbox._startGameRoute();
    assert.equal(game.runtime.session.characterName, descriptor.name);
    assert.equal(window.__SoccerPlayerAvatarController.getState().model.path, '/retry-player.vrm',
      'restart must preserve the chosen player model');
    assert.equal(window.__SoccerAiAvatarController.getState().model.path, '/retry-ai.vrm',
      'restart must preserve the chosen AI model');
    assert.equal(window.__SoccerAiAvatarController.getState().paused, true,
      'restart must preserve the chosen pause state');
    assert.notEqual(window.__SoccerAiAvatarController, retryAi, 'restart reused a disposed controller');
    assert.equal(vm.runInThisContext('soccerAvatarRestore.player'), null);
    assert.equal(vm.runInThisContext('soccerAvatarRestore.ai'), null);
    await game.runtime.end({});
    window.__SoccerAiAvatarController.dispose();
    window.__SoccerPlayerAvatarController.dispose();
    for (const failure of ['empty', 'removed', 'unauthorized', 'all-unavailable']) {
      game.runtime.reset({ newSession: true });
      sandbox.resetSoccerCharacterInfo();
      await vm.runInThisContext('ensureSoccerCharacterInfo()');
      await sandbox.setAiAvatar({ type: 'vrm', path: '/saved.vrm' });
      window.__SoccerAiAvatarController.pause();
      const previous = window.__SoccerAiAvatarController;
      if (failure === 'empty') renderers.at(-1).model = null;
      if (failure === 'unauthorized') renderers.at(-1).model = { type: 'mmd', path: '/revoked.pmx' };
      if (failure === 'removed' || failure === 'all-unavailable') unavailableModels.add('/saved.vrm');
      if (failure === 'all-unavailable') {
        unavailableModels.add('/test.vrm');
        unavailableModels.add('/fallback.model3.json');
      }
      game.runtime.reset({ newSession: true });
      sandbox.resetSoccerCharacterInfo();
      assert.equal(previous.disposed, true);
      const saved = vm.runInThisContext('soccerAvatarRestore.ai');
      assert(!saved || !saved.model || saved.model.type, 'reset saved an empty model object');
      await sandbox._startGameRoute();
      assert.equal(game.runtime.state, 'running', `${failure}: avatar recovery blocked route startup`);
      assert.equal(vm.runInThisContext('soccerAvatarRestore.ai'), null, `${failure}: invalid snapshot survived`);
      if (failure !== 'all-unavailable') {
        assert.equal(window.__SoccerAiAvatarController.getState().model.path, '/test.vrm');
        assert.equal(window.__SoccerAiAvatarController.getState().paused, true);
      }
      await game.runtime.end({});
      unavailableModels.clear();
      window.__SoccerAiAvatarController?.dispose();
    }
    game.runtime.reset({ newSession: true });
    sandbox.resetSoccerCharacterInfo();
    await vm.runInThisContext('ensureSoccerCharacterInfo()');
    for (const phase of ['candidate', 'recovery']) {
      await sandbox.setAiAvatar({ type: 'live2d', path: '/before-reset.model3.json' });
      let release;
      mountGatePath = phase === 'candidate' ? '/reset-missing.vrm' : '/before-reset.model3.json';
      mountGate = new Promise(resolve => { release = resolve; });
      unavailableModels.add('/reset-missing.vrm');
      const pending = sandbox.setAiAvatar({ type: 'vrm', path: '/reset-missing.vrm' }).catch(error => error);
      for (let i = 0; i < 10; i++) await new Promise(resolve => setImmediate(resolve));
      assert.equal(mountAttempts.at(-1), mountGatePath, `${phase}: did not reach delayed mount`);
      const attemptsAtReset = mountAttempts.length;
      sandbox.resetSoccerCharacterInfo();
      release();
      assert(await pending instanceof Error);
      mountGate = null; mountGatePath = null;
      unavailableModels.clear();
      assert.equal(window.__SoccerAiAvatarController, null, `${phase}: reset revived an old avatar`);
      assert.equal(mountAttempts.length, attemptsAtReset, `${phase}: reset started another recovery`);
      assert.equal(vm.runInThisContext('soccerAvatarChanging.ai'), false);
      if (phase === 'recovery') assert.equal(renderers.at(-1).disposed, true, 'late recovery leaked its renderer');
    }
    await vm.runInThisContext('ensureSoccerCharacterInfo()');
    host.getAvatarCharacter = originalCharacterQuery;
    document.getElementById = previousGetElement;
    let releaseMount;
    mountGate = new Promise(resolve => { releaseMount = resolve; });
    const eventCountBeforeExit = avatarEvents.length;
    host.getAvatarCharacter = async () => ({ ...descriptor, model: { type: 'mmd', path: '/late.pmx' } });
    const exitingAvatar = sandbox.setAiAvatar({ type: 'mmd', path: '/late.pmx' });
    for (let i = 0; i < 10; i++) await new Promise(resolve => setImmediate(resolve));
    game.dispose();
    releaseMount();
    await assert.rejects(exitingAvatar, /disposed|cancelled/);
    assert.equal(renderers.at(-1).disposed, true, 'late mount survived SDK disposal');
    assert.equal(avatarEvents.length, eventCountBeforeExit, 'late mount emitted a success event');
    assert.equal(vm.runInThisContext('soccerAvatarChanging.ai'), false, 'exit retained the slot fence');
  } finally { game.dispose(); }
  console.log('soccer SDK migration runtime test passed');
}
main().catch((error) => { console.error(error); process.exitCode = 1; });
