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
const response = (data) => ({ ok: true, status: 200, json: async () => data, clone: () => response(data) });

async function main() {
  const calls = [];
  const storage = new Map();
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
    setInterval: () => 1, clearInterval() {}, addEventListener, removeEventListener,
    lanlan_config: { lanlan_name: 'test-character' },
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
      resetSoccerSessionDebugLogEnableState() {}, ensureSoccerCharacterInfo: async () => {},
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
    install('function _gameRoutePayload(', 'async function _sendGameRouteHeartbeat(');
    install('async function _startGameRoute()', 'function _scoreDiffOf(');
    install('function _gameRouteEndPayload(', 'async function _endGameLLMSession(');
    game.runtime.configure({ payload: () => sandbox._gameRoutePayload(), heartbeat: { intervalMs: 60000 },
      outputs: { intervalMs: 60000 }, pageExit: false });
    await sandbox._startGameRoute();
    assert.equal(appliedContext.initialMood, 'happy');
    const start = calls.find((c) => c.url.endsWith('/route/start')).payload;
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
    sandbox._isGameMemoryEnabled = () => false;
    await sandbox._startGameRoute();
    const disabled = calls.findLast((c) => c.url.endsWith('/route/start')).payload;
    assert.equal(disabled.game_memory_enabled, false);
    assert.equal(disabled.game_memory_archive_enabled, false);
    assert.equal(disabled.game_memory_player_interaction_enabled, false);
    assert.equal(disabled.game_memory_event_reply_enabled, false);
    assert.equal(disabled.game_memory_postgame_context_enabled, false);
    await game.runtime.end({});
    game.runtime.reset({ newSession: true });
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
  } finally { game.dispose(); }
  console.log('soccer SDK migration runtime test passed');
}
main().catch((error) => { console.error(error); process.exitCode = 1; });
