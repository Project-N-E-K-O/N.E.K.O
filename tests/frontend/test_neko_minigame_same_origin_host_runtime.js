const fs = require('fs');
const path = require('path');
const vm = require('vm');

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function jsonResponse(data, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    async json() { return data; },
    clone() { return jsonResponse(data, status); },
  };
}

function storage() {
  const values = new Map();
  return {
    get length() { return values.size; },
    key(index) { return [...values.keys()][index] ?? null; },
    getItem(key) { return values.has(key) ? values.get(key) : null; },
    setItem(key, value) { values.set(String(key), String(value)); },
    removeItem(key) { values.delete(String(key)); },
  };
}

// Shape of the server's /route/end response: the full internal archive.
const RAW_END_RESPONSE = {
  ok: true,
  closed: true,
  route_closed: true,
  session_id: 'server-session',
  should_resume_external_on_exit: true,
  before_game_external_mode: 'audio',
  archive: {
    game_type: 'generic-game',
    session_id: 'server-session',
    finalScore: { player: 2, ai: 1 },
    last_state: { round: 4 },
    dialog_count: 7,
    full_dialogues: [{ role: 'user', text: 'private speech' }],
    last_full_dialogues: [{ role: 'user', text: 'private speech' }],
    key_events: ['private event'],
    summary: 'private summary',
    game_context_summary: 'private rolling summary',
    game_context_signals: { private: true },
    game_context_recent_ids: ['id-1'],
    route_activations: [{ kind: 'internal' }],
    nekoInviteText: 'private invite',
    preGameContext: { stance: 'private' },
    pre_game_context_source: 'ai',
    sdk_memory_submissions: [{ summary: 'game submitted this itself' }],
  },
  archive_memory: { text: 'private memory write' },
  postgame: { ok: true, action: 'chat', line: 'assistant postgame line', llm_source: { p: 1 } },
};

const LEAKY_ARCHIVE_FIELDS = [
  'full_dialogues', 'last_full_dialogues', 'key_events', 'summary',
  'game_context_signals', 'game_context_recent_ids', 'route_activations',
  'nekoInviteText',
];

async function main() {
  const sourcePath = path.resolve(
    __dirname,
    '../../static/game/sdk/neko-minigame-same-origin-host.js',
  );
  const calls = [];
  const listeners = new Map();
  let releaseProtocolTwo;
  let markProtocolTwoStarted;
  let releaseDelayedDrain;
  let markDelayedDrainStarted;
  let slowLogEnableGate = null;
  let releaseSlowLogEnable = null;
  const protocolTwoGate = new Promise((resolve) => { releaseProtocolTwo = resolve; });
  const protocolTwoStarted = new Promise((resolve) => { markProtocolTwoStarted = resolve; });
  const delayedDrainGate = new Promise((resolve) => { releaseDelayedDrain = resolve; });
  const delayedDrainStarted = new Promise((resolve) => { markDelayedDrainStarted = resolve; });
  const fetchImpl = async (url, init = {}) => {
    const pathName = String(url);
    if (pathName.startsWith('/api/config/page_config')) {
      return jsonResponse({ autostart_csrf_token: 'test-token' });
    }
    if (pathName === '/api/game/logs/enable') {
      if (slowLogEnableGate) await slowLogEnableGate;
      return jsonResponse({ ok: true, enabled: true });
    }
    const body = init.body ? JSON.parse(init.body) : {};
    calls.push({ url: pathName, init, body });
    if (pathName.endsWith('/protocol') && body.sequence === 2) {
      markProtocolTwoStarted();
      await protocolTwoGate;
    }
    if (/\/api\/game\/[^/]+\/end$/.test(pathName)) {
      // FastAPI and common proxies answer a rejected close with a non-2xx
      // `{"detail": ...}` body: it parses fine and carries no `ok`.
      if (body.force_end_http_error === true) {
        return jsonResponse({ detail: 'route is not closable' }, 409);
      }
      return jsonResponse(RAW_END_RESPONSE);
    }
    if (pathName.endsWith('/route/start')) {
      return jsonResponse({
        ok: true,
        state: {
          game_route_active: true,
          session_id: 'server-session',
          lanlan_name: 'Server Neko',
        },
      });
    }
    if (pathName.endsWith('/route/drain')) {
      const responseData = {
        ok: true,
        outputs: [{
          ts: 123,
          result: { control: { stance: 'ready' } },
        }],
      };
      if (body.delay_control_parse === true) {
        return {
          ok: true,
          status: 200,
          async json() { return responseData; },
          clone() {
            return {
              async json() {
                markDelayedDrainStarted();
                await delayedDrainGate;
                return responseData;
              },
            };
          },
        };
      }
      return jsonResponse(responseData);
    }
    return jsonResponse({ ok: true, accepted: true });
  };
  const windowMock = {
    AbortController,
    console: { warn() {}, error() {}, log() {} },
    fetch: fetchImpl,
    navigator: {
      sendBeacon: () => false,
      locks: { request: async (_name, _options, callback) => callback() },
    },
    location: { origin: 'http://127.0.0.1:48911' },
    localStorage: storage(),
    setTimeout,
    clearTimeout,
    setInterval,
    clearInterval,
    addEventListener(type, handler) {
      if (!listeners.has(type)) listeners.set(type, new Set());
      listeners.get(type).add(handler);
    },
    removeEventListener(type, handler) {
      listeners.get(type)?.delete(handler);
      if (!listeners.get(type)?.size) listeners.delete(type);
    },
    dispatchEvent(event) {
      for (const handler of Array.from(listeners.get(event.type) || [])) handler(event);
    },
    CustomEvent: class CustomEventMock {
      constructor(type, options = {}) { this.type = type; this.detail = options.detail; }
    },
    crypto: {
      getRandomValues(values) {
        values.fill(7);
        return values;
      },
    },
  };
  const defaultCapabilities = [
    'runtime', 'dialogue', 'logging', 'voice-input', 'speech-output',
    'context-read', 'memory', 'storage', 'leaderboard-local', 'quick-lines',
  ];
  const hostLaunchRegistrations = Object.fromEntries(
    [...[
      'example-game',
      'waiting-lock-game',
      'third-party-game',
      'speech-only-game',
      'no-lock-game',
      'logger-one',
      'logger-two',
      'log-timeout-game',
    ], ...Array.from({ length: 70 }, (_unused, index) => `overflow-game-${index}`)]
      .map((gameId) => [gameId, {
      mode: gameId === 'example-game' ? 'registered' : 'development',
      gameId,
      publisherId: 'test-host',
      version: '1.0.0',
      allowedCapabilities: defaultCapabilities,
      capabilityProviders: gameId === 'example-game' ? {
        quickLines: async () => jsonResponse({ ok: true, lines: ['ready'] }),
      } : {},
    }]),
  );
  const launchNode = {
    textContent: JSON.stringify({ registrations: hostLaunchRegistrations }),
    nekoCapabilityProviders: {
      'example-game': {
        quickLines: async () => jsonResponse({ ok: true, lines: ['ready'] }),
      },
    },
    remove() { this.removed = true; },
  };
  let adapterScript = null;
  let launchBindingWasImmutable = false;
  windowMock.document = {
    currentScript: null,
    getElementById(id) { return id === 'neko-minigame-host-launch' ? launchNode : null; },
    createElement() {
      return { remove() { this.removed = true; } };
    },
    head: {
      appendChild(script) {
        adapterScript = script;
        const descriptor = Object.getOwnPropertyDescriptor(script, 'nekoHostLaunchRegistry');
        try {
          Object.defineProperty(script, 'nekoHostLaunchRegistry', {
            value: { forged: true },
          });
        } catch (_) {
          launchBindingWasImmutable = descriptor?.configurable === false
            && descriptor?.writable === false;
        }
        windowMock.document.currentScript = script;
        try {
          vm.runInThisContext(fs.readFileSync(sourcePath, 'utf8'), { filename: sourcePath });
        } finally {
          windowMock.document.currentScript = null;
        }
        script.onload?.();
      },
    },
  };
  global.window = windowMock;

  const bootstrapPath = path.resolve(
    __dirname,
    '../../static/game/sdk/neko-minigame-same-origin-bootstrap.js',
  );
  vm.runInThisContext(fs.readFileSync(bootstrapPath, 'utf8'), { filename: bootstrapPath });
  await windowMock.nekoMiniGameSameOriginHostReady;
  assert(launchNode.removed === true, 'trusted launch node was not consumed before game code');
  assert(launchBindingWasImmutable === true && adapterScript?.removed === true,
    'adapter launch binding was mutable or its script node remained resident after consumption');
  assert(windowMock.bootstrapNekoMiniGameSameOriginHost === undefined,
    'game code received a public registration producer');
  const trustedFactory = windowMock.createNekoMiniGameSameOriginHost;
  const factoryDescriptor = Object.getOwnPropertyDescriptor(
    windowMock,
    'createNekoMiniGameSameOriginHost',
  );
  assert(factoryDescriptor?.configurable === false && factoryDescriptor?.writable === false,
    'trusted host factory remained replaceable after adapter bootstrap');
  windowMock.document.currentScript = {
    nekoHostLaunchRegistry: {
      'forged-game': {
        mode: 'registered',
        gameId: 'forged-game',
        version: '1.0.0',
        allowedCapabilities: defaultCapabilities,
      },
    },
  };
  vm.runInThisContext(fs.readFileSync(sourcePath, 'utf8'), { filename: sourcePath });
  windowMock.document.currentScript = null;
  assert(windowMock.createNekoMiniGameSameOriginHost === trustedFactory,
    'a later game-loaded adapter replaced the trusted host factory');

  const createHost = (options = {}) => window.createNekoMiniGameSameOriginHost(options);
  let missingRegistrationError = null;
  try {
    window.createNekoMiniGameSameOriginHost({
      gameType: 'forged-game',
      fetchImpl,
      windowImpl: windowMock,
      navigatorImpl: windowMock.navigator,
    });
  } catch (error) { missingRegistrationError = error; }
  assert(missingRegistrationError?.code === 'game_unregistered',
    'a game minted a registered host identity without a launch registration');
  let overflowRegistrationError = null;
  try {
    window.createNekoMiniGameSameOriginHost({
      gameType: 'overflow-game-69',
      fetchImpl,
      windowImpl: windowMock,
      navigatorImpl: windowMock.navigator,
    });
  } catch (error) { overflowRegistrationError = error; }
  assert(overflowRegistrationError?.code === 'game_unregistered',
    'the host launch registry exceeded its page-lifetime capacity bound');

  const host = createHost({
    gameType: 'example-game',
    sessionId: 'client-session',
    fetchImpl,
    windowImpl: windowMock,
    navigatorImpl: windowMock.navigator,
    capabilityProviders: {
      quickLines: async () => jsonResponse({ ok: true, lines: ['forged'] }),
    },
  });
  const handshake = host.connectGame({
    protocolVersions: ['1'],
    manifest: {
      id: 'example-game',
      version: '1.0.0',
      requiredCapabilities: ['runtime', 'logging'],
      optionalCapabilities: [
        'dialogue', 'quick-lines', 'context-read', 'memory', 'storage', 'leaderboard-local', 'speech-output',
        'voice-input',
      ],
    },
  });
  assert(handshake.grantedCapabilities.includes('context-read'),
    'same-origin host did not grant its context adapter');
  assert(handshake.grantedCapabilities.includes('memory'),
    'same-origin host did not grant its memory adapter');
  assert(handshake.grantedCapabilities.includes('quick-lines'),
    'host-provided quick-lines were not granted');
  assert(handshake.grantedCapabilities.includes('storage')
    && handshake.grantedCapabilities.includes('leaderboard-local'),
  'cross-window-safe local leaderboard capability was not granted');
  let storageLockEntered = false;
  await host.runGameStorageExclusive('leaderboards/main', async () => {
    storageLockEntered = true;
  });
  assert(storageLockEntered, 'trusted host did not enter its origin-wide storage lock');

  let initialSpeechError = null;
  windowMock.localStorage.setItem('neko_speech_playback_state', JSON.stringify({
    type: 'speech_playback_state',
    active: true,
    speech_id: 'initial-speech',
  }));
  host.startSpeechOutputBridge({
    onState() { throw new Error('consumer failed'); },
    onError(error, source) { initialSpeechError = { error, source }; },
  });
  assert(initialSpeechError?.error?.message === 'consumer failed'
    && initialSpeechError.source === 'local_storage_initial',
  'initial speech state callback failures did not reach the host error bridge');
  host.stopSpeechOutputBridge();

  await host.configureGameMemoryConsent({ enabled: true, session_id: 'client-session' });
  const cancelledDirectRequest = new AbortController();
  cancelledDirectRequest.abort();
  let cancelledStorageError = null;
  try {
    host.requestGameStorage(
      'set',
      { key: 'cancelled-direct', value: true },
      { signal: cancelledDirectRequest.signal },
    );
  } catch (error) { cancelledStorageError = error; }
  const cancelledStorageKey = `${host._gameStoragePrefix()}cancelled-direct`;
  assert(cancelledStorageError?.code === 'cancelled'
    && windowMock.localStorage.getItem(cancelledStorageKey) == null,
  'already-cancelled direct storage request mutated localStorage');
  let cancelledConsentError = null;
  try {
    host.configureGameMemoryConsent(
      { enabled: false, session_id: 'client-session' },
      { signal: cancelledDirectRequest.signal },
    );
  } catch (error) { cancelledConsentError = error; }
  assert(cancelledConsentError?.code === 'cancelled' && host._memoryConsentEnabled === true,
    'already-cancelled direct memory consent request changed host state');
  const startResponse = await host.start({
    session_id: 'attacker-session',
    lanlan_name: 'Attacker Neko',
    game_memory_archive_enabled: false,
    legacyGameMemoryEnabled: false,
    legacy_game_memory_event_reply_enabled: false,
  });
  const startData = await startResponse.clone().json();
  host.applyRouteState(startData.state);
  const startCall = calls.find((call) => call.url.endsWith('/route/start'));
  assert(startCall.body.session_id === 'client-session',
    'route start trusted an application-supplied session id');
  assert(startCall.body.game_memory_enabled === true,
    'opening-screen memory consent was not attached to route start');
  assert(startCall.body.game_memory_player_interaction_enabled === true
    && startCall.body.game_memory_event_reply_enabled === true
    && startCall.body.game_memory_archive_enabled === true
    && startCall.body.game_memory_postgame_context_enabled === true,
  'trusted host did not derive the complete memory policy from consent');
  assert(!Object.hasOwn(startCall.body, 'legacyGameMemoryEnabled')
    && !Object.hasOwn(startCall.body, 'legacy_game_memory_event_reply_enabled'),
  'caller-controlled legacy memory aliases survived the trusted host boundary');
  const ungrantedHost = createHost({
    gameType: 'third-party-game',
    sessionId: 'ungranted-session',
    fetchImpl,
    windowImpl: windowMock,
    navigatorImpl: windowMock.navigator,
  });
  const ungrantedHandshake = ungrantedHost.connectGame({
    protocolVersions: ['1'],
    manifest: {
      id: 'third-party-game',
      version: '1.0.0',
      requiredCapabilities: ['runtime'],
      optionalCapabilities: [],
    },
  });
  assert(!ungrantedHandshake.grantedCapabilities.includes('voice-input'),
    'an unrequested voice capability was granted');
  const callsBeforeDeniedCapabilities = calls.length;
  const deniedCapabilityOperations = [
    () => ungrantedHost.readGameContext({ scopes: ['character-public'] }),
    () => ungrantedHost.configureGameMemoryConsent({ enabled: true }),
    () => ungrantedHost.requestGameStorage('set', { key: 'denied', value: true }),
    () => ungrantedHost.requestDialogue({ event: { kind: 'denied' } }),
    () => ungrantedHost.getQuickLines({ event: { kind: 'denied' } }),
    () => ungrantedHost.speak({ line: 'denied' }),
    () => ungrantedHost.submitVoiceTranscript({ transcript: 'denied' }),
    () => ungrantedHost.mountAvatar({}),
    () => ungrantedHost.mountAudio({}),
    () => ungrantedHost.postLog({ event: 'denied' }),
  ];
  for (const invoke of deniedCapabilityOperations) {
    let deniedError = null;
    try { await invoke(); } catch (error) { deniedError = error; }
    assert(deniedError?.code === 'capability_denied',
      'direct host transport bypassed a negotiated capability');
  }
  assert(calls.length === callsBeforeDeniedCapabilities
    && windowMock.localStorage.getItem(`${ungrantedHost._gameStoragePrefix()}denied`) == null,
  'a denied direct host operation produced a fetch or storage side effect');
  await ungrantedHost.start({
    gameMemoryEnabled: true,
    game_archive_memory_enabled: true,
    legacy_game_memory_enabled: true,
    legacyGameMemoryArchiveEnabled: true,
    event: {
      kind: 'nested-memory-bypass',
      game_memory_enabled: true,
      legacyGameMemoryArchiveEnabled: true,
      legacy_game_memory_event_reply_enabled: true,
    },
  });
  const ungrantedStart = calls.filter((call) => call.url.endsWith('/route/start')).at(-1);
  assert(ungrantedStart.body.game_memory_enabled === false
    && ungrantedStart.body.game_memory_player_interaction_enabled === false
    && ungrantedStart.body.game_memory_event_reply_enabled === false
    && ungrantedStart.body.game_memory_archive_enabled === false
    && ungrantedStart.body.game_memory_postgame_context_enabled === false,
  'a game without memory grant overrode the host-owned memory policy');
  assert(!Object.hasOwn(ungrantedStart.body, 'gameMemoryEnabled')
    && !Object.hasOwn(ungrantedStart.body, 'game_archive_memory_enabled')
    && !Object.hasOwn(ungrantedStart.body, 'legacy_game_memory_enabled')
    && !Object.hasOwn(ungrantedStart.body, 'legacyGameMemoryArchiveEnabled'),
  'ungranted legacy memory aliases were forwarded to the backend');
  assert(ungrantedStart.body.event.kind === 'nested-memory-bypass'
    && !Object.hasOwn(ungrantedStart.body.event, 'game_memory_enabled')
    && !Object.hasOwn(ungrantedStart.body.event, 'legacyGameMemoryArchiveEnabled')
    && !Object.hasOwn(ungrantedStart.body.event, 'legacy_game_memory_event_reply_enabled'),
  'nested legacy memory aliases bypassed the host-owned memory policy');
  await ungrantedHost.heartbeat({
    game_memory_enabled: true,
    legacy_game_memory_archive_enabled: true,
  });
  const ungrantedHeartbeat = calls.filter((call) => call.url.endsWith('/route/heartbeat')).at(-1);
  assert(ungrantedHeartbeat.body.game_memory_enabled === false
    && ungrantedHeartbeat.body.game_memory_archive_enabled === false
    && !Object.hasOwn(ungrantedHeartbeat.body, 'legacy_game_memory_archive_enabled'),
  'a heartbeat bypassed the host-owned memory opt-out policy');
  let eventToJsonCalls = 0;
  await ungrantedHost.heartbeat({
    event: {
      kind: 'serialization-hook-bypass',
      toJSON() {
        eventToJsonCalls += 1;
        return { kind: 'forged', game_memory_enabled: true };
      },
    },
  });
  const safeSerializationHeartbeat = calls.filter(
    (call) => call.url.endsWith('/route/heartbeat'),
  ).at(-1);
  assert(eventToJsonCalls === 0
    && safeSerializationHeartbeat.body.event.kind === 'serialization-hook-bypass'
    && !Object.hasOwn(safeSerializationHeartbeat.body.event, 'toJSON')
    && !Object.hasOwn(safeSerializationHeartbeat.body.event, 'game_memory_enabled'),
  'an event serialization hook reintroduced caller-controlled memory policy');
  const heartbeatCallsBeforeWidePayload = calls.filter(
    (call) => call.url.endsWith('/route/heartbeat'),
  ).length;
  let widePayloadError = null;
  try {
    await ungrantedHost.heartbeat({
      event: Object.fromEntries(
        Array.from({ length: 4100 }, (_, index) => [`field_${index}`, index]),
      ),
    });
  } catch (error) {
    widePayloadError = error;
  }
  assert(widePayloadError?.code === 'invalid_payload'
    && calls.filter(
      (call) => call.url.endsWith('/route/heartbeat'),
    ).length === heartbeatCallsBeforeWidePayload,
  'a payload wider than the trusted clone bound reached the backend');
  // Depth and node counts measure structure only -- a string is one node
  // however many bytes it holds, and keys were not measured at all -- so the
  // runtime lifecycle payload had no byte bound anywhere. Every other SDK
  // egress path is capped at 256 KiB; this one shipped whatever
  // configure({payload}) returned, at the heartbeat and drain cadence.
  const heartbeatCallsBeforeHeavyPayload = calls.filter(
    (call) => call.url.endsWith('/route/heartbeat'),
  ).length;
  let heavyPayloadError = null;
  try { await ungrantedHost.heartbeat({ replay: 'x'.repeat(300 * 1024) }); }
  catch (error) { heavyPayloadError = error; }
  assert(heavyPayloadError?.code === 'invalid_payload'
    && calls.filter(
      (call) => call.url.endsWith('/route/heartbeat'),
    ).length === heartbeatCallsBeforeHeavyPayload,
  'a multi-hundred-KiB runtime payload reached the backend unbounded');
  let heavyKeyError = null;
  try {
    await ungrantedHost.heartbeat({
      [`k${'y'.repeat(300 * 1024)}`]: 1,
    });
  } catch (error) { heavyKeyError = error; }
  assert(heavyKeyError?.code === 'invalid_payload',
    'payload bytes hidden in a key bypassed the trusted payload budget');
  // The bound is the SDK's own 256 KiB, so it can never reject a payload the
  // SDK has already accepted.
  const heartbeatCallsBeforeAdmittedPayload = calls.filter(
    (call) => call.url.endsWith('/route/heartbeat'),
  ).length;
  await ungrantedHost.heartbeat({ replay: 'x'.repeat(200 * 1024) });
  assert(calls.filter(
    (call) => call.url.endsWith('/route/heartbeat'),
  ).length === heartbeatCallsBeforeAdmittedPayload + 1,
  'a payload within the shared 256 KiB budget was rejected by the host');
  ungrantedHost.dispose();

  const speechOnlyHost = createHost({
    gameType: 'speech-only-game',
    sessionId: 'speech-only-session',
    fetchImpl,
    windowImpl: windowMock,
    navigatorImpl: windowMock.navigator,
  });
  const speechOnlyHandshake = speechOnlyHost.connectGame({
    protocolVersions: ['1'],
    manifest: {
      id: 'speech-only-game',
      version: '1.0.0',
      requiredCapabilities: ['runtime', 'logging', 'speech-output'],
      optionalCapabilities: [],
    },
  });
  assert(speechOnlyHandshake.grantedCapabilities.includes('speech-output')
    && !speechOnlyHandshake.grantedCapabilities.includes('dialogue'),
  'speech-only fixture unexpectedly received dialogue');
  await speechOnlyHost.mirrorSpeechOutput({ line: 'speech-only mirror' });
  const speechOnlyMirror = calls.filter((call) => call.url.endsWith('/mirror-assistant')).at(-1);
  assert(speechOnlyMirror.body.line === 'speech-only mirror',
    'speech-output mirroring was incorrectly gated by dialogue');
  speechOnlyHost.dispose();

  const disconnectedHost = createHost({
    gameType: 'third-party-game',
    sessionId: 'disconnected-session',
    fetchImpl,
    windowImpl: windowMock,
    navigatorImpl: windowMock.navigator,
  });
  const callsBeforeDisconnectedStart = calls.length;
  let disconnectedStartError = null;
  try { await disconnectedHost.start({}); }
  catch (error) { disconnectedStartError = error; }
  assert(disconnectedStartError?.code === 'capability_denied'
    && calls.length === callsBeforeDisconnectedStart,
  'a host operation was usable before capability negotiation');
  disconnectedHost.dispose();
  assert(host.sessionId === 'server-session' && host.routeLanlanName === 'Server Neko',
    'authoritative route identity did not replace the provisional host identity');
  assert(typeof host.evaluatePassiveGuard === 'undefined',
    'the game-specific PassiveGuard leaked into the public same-origin host');
  const quickLinesResponse = await host.getQuickLines({ event: { kind: 'prepare' } });
  assert((await quickLinesResponse.json()).lines[0] === 'ready',
    'quick-lines did not use the host-owned provider');
  const endCallsBeforeInvalidPayload = calls.filter(
    (call) => call.url.endsWith('/end'),
  ).length;
  let invalidEndError = null;
  try { await host.end('{not-json'); } catch (error) { invalidEndError = error; }
  assert(invalidEndError?.code === 'invalid_payload'
    && calls.filter((call) => call.url.endsWith('/end')).length === endCallsBeforeInvalidPayload,
  'an invalid string end payload reached the backend');
  await host.end(JSON.stringify({
    session_id: 'forged-session',
    lanlan_name: 'Forged Neko',
    game_memory_enabled: false,
  }));
  const trustedStringEnd = calls.filter((call) => call.url.endsWith('/end')).at(-1);
  assert(trustedStringEnd.body.session_id === 'server-session'
    && trustedStringEnd.body.lanlan_name === 'Server Neko'
    && trustedStringEnd.body.game_memory_enabled === true,
  'a JSON string end payload bypassed trusted runtime ownership');

  await host.publishGameProtocol('event', {
    protocolVersion: '1',
    sequence: 1,
    type: 'round-started',
    sessionId: 'attacker-session',
    payload: { round: 1 },
  });
  const protocolCall = calls.find((call) => call.url.endsWith('/protocol'));
  assert(protocolCall.body.session_id === 'server-session',
    'protocol messages did not use the authoritative route session');
  assert(protocolCall.body._csrf_token === 'test-token'
    && protocolCall.init.headers['X-CSRF-Token'] === 'test-token',
  'protocol mutation did not carry the host CSRF contract');

  const protocolTwo = host.publishGameProtocol('event', {
    protocolVersion: '1', sequence: 2, type: 'second', payload: {},
  });
  const protocolThree = host.publishGameProtocol('state', {
    protocolVersion: '1', sequence: 3, type: 'third', payload: {},
  });
  await protocolTwoStarted;
  assert(!calls.some((call) => call.url.endsWith('/protocol') && call.body.sequence === 3),
    'protocol transport allowed a later sequence to overtake an active request');
  releaseProtocolTwo();
  await Promise.all([protocolTwo, protocolThree]);
  assert(calls.filter((call) => call.url.endsWith('/protocol')).map((call) => call.body.sequence).join(',') === '1,2,3',
    'protocol transport did not preserve SDK call order');

  await host.readGameContext({
    session_id: 'attacker-session',
    scopes: ['character-public'],
  });
  await host.submitGameMemory({
    session_id: 'attacker-session',
    submission: { summary: 'visible result' },
  });
  await host.preloadSpeechOutput({
    session_id: 'attacker-session',
    lines: ['预载台词'],
  });
  const contextCall = calls.find((call) => call.url.endsWith('/context/read'));
  const memoryCall = calls.find((call) => call.url.endsWith('/memory/submit'));
  const speechPreloadCall = calls.find((call) => call.url.endsWith('/speech/preload'));
  assert(contextCall.body.session_id === 'server-session',
    'context read did not bind the authoritative route session');
  assert(contextCall.body._csrf_token === 'test-token'
    && contextCall.init.headers['X-CSRF-Token'] === 'test-token',
  'context read did not carry the host CSRF contract');
  assert(memoryCall.body.session_id === 'server-session'
    && memoryCall.body._csrf_token === 'test-token',
  'memory submission did not bind the authoritative session and CSRF token');
  assert(speechPreloadCall.body.session_id === 'server-session'
    && speechPreloadCall.body._csrf_token === 'test-token'
    && speechPreloadCall.init.headers['X-CSRF-Token'] === 'test-token',
  'speech preload did not bind the authoritative session and CSRF token');

  let speechChannel = null;
  class SpeechChannelMock {
    constructor() { speechChannel = this; this.onmessage = null; }
    close() {}
  }
  const playbackStates = [];
  host.startSpeechPlaybackBridge({
    BroadcastChannelImpl: SpeechChannelMock,
    onState: (state, source) => playbackStates.push({ state, source }),
  });
  const sharedPlaybackState = {
    type: 'speech_playback_state',
    active: true,
    speechId: 'dedupe-speech',
    remainingSeconds: 2,
    updatedAt: 1700000000000,
  };
  speechChannel.onmessage({ data: sharedPlaybackState });
  windowMock.dispatchEvent(new windowMock.CustomEvent('neko-speech-playback-state', {
    detail: sharedPlaybackState,
  }));
  windowMock.dispatchEvent({
    type: 'storage',
    key: 'neko_speech_playback_state',
    newValue: JSON.stringify(sharedPlaybackState),
  });
  assert(playbackStates.length === 1,
    'identical speech playback state was delivered once per active transport');
  windowMock.dispatchEvent(new windowMock.CustomEvent('neko-speech-playback-state', {
    detail: { ...sharedPlaybackState, updatedAt: sharedPlaybackState.updatedAt + 1 },
  }));
  assert(playbackStates.length === 2,
    'a newer speech playback state was incorrectly deduplicated');
  host.stopSpeechPlaybackBridge();

  const controls = [];
  host.startGameControlBridge({ onControl: (control) => controls.push(control) });
  const delayedDrain = host.drain({
    session_id: 'attacker-session',
    sdk_route_instance_id: 'route-instance-A',
    delay_control_parse: true,
  });
  await delayedDrainStarted;
  host.applyRuntimeState({
    session_id: 'replacement-session',
    lanlan_name: 'Server Neko',
  });
  releaseDelayedDrain();
  await delayedDrain;
  assert(controls.length === 1 && controls[0].type === 'stance'
    && controls[0].payload === 'ready',
  'route outputs were not converted into SDK control envelopes');
  assert(controls[0].sessionId === 'server-session',
    'control envelope did not preserve the drain request session');
  assert(controls[0].routeInstanceId === 'route-instance-A',
    'control envelope did not preserve the drain request route generation');
  assert(controls[0].timestamp === 123000,
    'second-based backend control timestamps were not normalized to milliseconds');
  host.applyRuntimeState({
    session_id: 'server-session',
    lanlan_name: 'Server Neko',
  });

  const millisecondControls = [];
  host.stopGameControlBridge();
  host.startGameControlBridge({ onControl: (control) => millisecondControls.push(control) });
  host._dispatchGameControls([{ ts: 1700000000123, control: { stance: 'ready' } }]);
  assert(millisecondControls[0].timestamp === 1700000000123,
    'millisecond control timestamps were changed during normalization');

  let sameDocumentState = null;
  host.startVoiceControlBridge({
    BroadcastChannelImpl: null,
    onState: (state, source) => { sameDocumentState = { state, source }; },
  });
  windowMock.dispatchEvent(new windowMock.CustomEvent('neko-game-voice-control-message', {
    detail: {
      type: 'game_voice_control_state',
      game_type: 'example-game',
      session_id: 'server-session',
      reason: 'state-sync',
    },
  }));
  assert(sameDocumentState?.source === 'same_document'
    && sameDocumentState.state.reason === 'state-sync',
  'same-document voice fallback state was not received');
  windowMock.dispatchEvent(new windowMock.CustomEvent('neko-game-voice-control-message', {
    detail: {
      type: 'game_voice_control_state',
      game_type: 'example-game',
      session_id: 'server-session',
      route_active: false,
      active: false,
      reason: 'route_closed',
    },
  }));
  assert(sameDocumentState?.state.route_active === false
    && sameDocumentState.state.reason === 'route_closed',
  'the trusted host dropped the closing route inactive voice state');
  const sameDocumentController = (event) => {
    if (event?.detail?.type !== 'game_voice_control_request') return;
    windowMock.dispatchEvent(new windowMock.CustomEvent('neko-game-voice-control-message', {
      detail: {
        type: 'game_voice_control_state',
        game_type: 'example-game',
        session_id: 'server-session',
        sdk_route_instance_id: event.detail.sdk_route_instance_id,
        request_id: event.detail.request_id,
        reason: 'queried',
        ok: true,
      },
    }));
  };
  windowMock.addEventListener('neko-game-voice-control-message', sameDocumentController);
  // Two adapters sharing a session and a millisecond both minted `voice-<ms>-1`,
  // and the shared channel then routed one adapter's reply into the other's
  // pending map. Capture the ids the requests actually carry.
  const observedVoiceRequestIds = [];
  const voiceRequestIdObserver = (event) => {
    if (event?.detail?.type === 'game_voice_control_request') {
      observedVoiceRequestIds.push(event.detail.request_id);
    }
  };
  windowMock.addEventListener('neko-game-voice-control-message', voiceRequestIdObserver);
  const sameDocumentResponse = await host.requestVoiceControl('query', {
    timeoutMs: 500,
    sdkRouteInstanceId: 'route-instance-a',
  });
  assert(sameDocumentResponse.reason === 'queried',
    'same-document voice fallback request did not complete without BroadcastChannel');
  assert(sameDocumentResponse.sdk_route_instance_id === 'route-instance-a',
    'same-document voice request did not preserve the route generation');
  const originalStorageSetItem = windowMock.localStorage.setItem;
  windowMock.localStorage.setItem = () => { throw new Error('storage blocked'); };
  const storageBlockedResponse = await host.requestVoiceControl('query', {
    timeoutMs: 500,
    sdkRouteInstanceId: 'route-instance-a',
  });
  windowMock.localStorage.setItem = originalStorageSetItem;
  assert(storageBlockedResponse.reason === 'queried',
    'same-document voice fallback was skipped when localStorage failed');
  const realVoiceDateNow = Date.now;
  let voiceEntropyCounter = 0;
  try {
    Date.now = () => 1700000000000;
    // TWO FRESH adapters: both per-bridge counters start at 0, which is the
    // collision. Reusing the long-lived `host` here would not reproduce it --
    // its counter has already advanced past 1 in the assertions above, so the
    // ids would differ for the wrong reason.
    const peerIds = [];
    for (const peerIndex of [0, 1]) {
      // windowMock.crypto is a deterministic `values.fill(7)` stub, which
      // would make both ids equal for the wrong reason. Counter source, so
      // "distinct" really means the entropy reached the id.
      const peerWindow = {
        ...windowMock,
        BroadcastChannel: undefined,
        crypto: {
          getRandomValues(values) {
            for (let index = 0; index < values.length; index += 1) {
              voiceEntropyCounter += 1;
              values[index] = voiceEntropyCounter;
            }
            return values;
          },
        },
      };
      const peerVoiceHost = createHost({
        gameType: 'example-game',
        sessionId: 'server-session',
        fetchImpl,
        windowImpl: peerWindow,
        navigatorImpl: windowMock.navigator,
      });
      peerVoiceHost.connectGame({
        protocolVersions: ['1'],
        manifest: {
          id: 'example-game', version: '1.0.0',
          requiredCapabilities: ['runtime', 'logging'],
          optionalCapabilities: ['voice-input'],
        },
      });
      peerVoiceHost.applyRouteState({
        game_route_active: true,
        session_id: 'server-session',
        lanlan_name: 'Server Neko',
      });
      peerVoiceHost.startVoiceControlBridge({ onState() {} });
      const before = observedVoiceRequestIds.length;
      await peerVoiceHost.requestVoiceControl('query', {
        timeoutMs: 500,
        sdkRouteInstanceId: 'route-instance-a',
      }).catch(() => { /* answered by the shared controller above */ });
      assert(observedVoiceRequestIds.length === before + 1,
        `peer adapter ${peerIndex} did not dispatch a voice request`);
      peerIds.push(observedVoiceRequestIds.at(-1));
      peerVoiceHost.dispose();
    }
    assert(peerIds[0] !== peerIds[1],
      `two same-session adapters minted the same voice request id in one millisecond: ${JSON.stringify(peerIds)}`);
  } finally {
    Date.now = realVoiceDateNow;
  }
  windowMock.removeEventListener('neko-game-voice-control-message', voiceRequestIdObserver);
  windowMock.removeEventListener('neko-game-voice-control-message', sameDocumentController);
  const voiceAbortController = new AbortController();
  const cancelledVoiceRequest = host.requestVoiceControl('query', {
    timeoutMs: 500,
    signal: voiceAbortController.signal,
  }).catch((error) => error);
  voiceAbortController.abort();
  const cancelledVoiceError = await cancelledVoiceRequest;
  assert(cancelledVoiceError?.code === 'cancelled' && host._voiceControlBridge.pending.size === 0,
    'aborted voice control request remained pending in the trusted host');
  host.stopVoiceControlBridge();
  assert(!listeners.has('neko-game-voice-control-message'),
    'same-document voice fallback listener was not released');

  const dualChannelMessages = [];
  let dualChannel = null;
  class DualVoiceChannelMock {
    constructor() { dualChannel = this; this.onmessage = null; }
    postMessage(message) { dualChannelMessages.push(message); }
    close() {}
  }
  let dualFallbackRequests = 0;
  let dualStateDeliveries = 0;
  const dualController = (event) => {
    if (event?.detail?.type !== 'game_voice_control_request') return;
    dualFallbackRequests += 1;
    const response = {
      type: 'game_voice_control_state',
      message_id: 'dual-response-1',
      game_type: 'example-game',
      session_id: 'server-session',
      sdk_route_instance_id: event.detail.sdk_route_instance_id,
      request_id: event.detail.request_id,
      reason: 'dual-queried',
      ok: true,
    };
    windowMock.dispatchEvent(new windowMock.CustomEvent('neko-game-voice-control-message', {
      detail: response,
    }));
    dualChannel.onmessage({ data: response });
  };
  windowMock.addEventListener('neko-game-voice-control-message', dualController);
  host.startVoiceControlBridge({
    BroadcastChannelImpl: DualVoiceChannelMock,
    onState: () => { dualStateDeliveries += 1; },
  });
  const dualResponse = await host.requestVoiceControl('query', {
    timeoutMs: 500,
    sdkRouteInstanceId: 'route-instance-b',
  });
  assert(dualResponse.reason === 'dual-queried'
    && dualChannelMessages.some((message) => message.type === 'game_voice_control_request')
    && dualFallbackRequests === 1,
  'voice control did not publish the same request over channel and fallback paths');
  assert(dualChannelMessages.some((message) => message.sdk_route_instance_id === 'route-instance-b'),
    'voice control transport omitted the active route generation');
  assert(dualStateDeliveries === 1,
    'voice control delivered one response more than once across dual transports');
  host.stopVoiceControlBridge();
  windowMock.removeEventListener('neko-game-voice-control-message', dualController);

  let recognitionAbortCalls = 0;
  class RecognitionMock {
    start() {}
    stop() {}
    abort() { recognitionAbortCalls += 1; }
  }
  host.startSpeechRecognition('release-test', { RecognitionImpl: RecognitionMock });
  host.releaseSpeechRecognition('release-test');
  assert(recognitionAbortCalls === 1 && host._speechRecognitionSlots.size === 0,
    'speech recognition release did not abort and remove its browser recognizer');

  let releaseLimitedProtocol;
  let markLimitedProtocolStarted;
  const limitedProtocolGate = new Promise((resolve) => { releaseLimitedProtocol = resolve; });
  const limitedProtocolStarted = new Promise((resolve) => { markLimitedProtocolStarted = resolve; });
  const limitedFetch = async (url, init = {}) => {
    if (String(url).startsWith('/api/config/page_config')) {
      return jsonResponse({ autostart_csrf_token: 'test-token' });
    }
    if (String(url).endsWith('/protocol')) {
      markLimitedProtocolStarted();
      await limitedProtocolGate;
    }
    return jsonResponse({ ok: true });
  };
  const limitedHost = createHost({
    gameType: 'example-game',
    protocolQueueLimit: 2,
    fetchImpl: limitedFetch,
    windowImpl: windowMock,
    navigatorImpl: windowMock.navigator,
  });
  limitedHost.connectGame({
    protocolVersions: ['1'],
    manifest: {
      id: 'example-game', version: '1.0.0', requiredCapabilities: ['runtime'], optionalCapabilities: [],
    },
  });
  const limitedFirst = limitedHost.publishGameProtocol('event', {
    protocolVersion: '1', sequence: 1, type: 'first', payload: {},
  });
  await limitedProtocolStarted;
  const limitedQueued = limitedHost.publishGameProtocol('event', {
    protocolVersion: '1', sequence: 2, type: 'second', payload: {},
  });
  let queueLimitError = null;
  try {
    await limitedHost.publishGameProtocol('event', {
      protocolVersion: '1', sequence: 3, type: 'third', payload: {},
    });
  } catch (error) {
    queueLimitError = error;
  }
  assert(queueLimitError?.code === 'busy', 'protocol queue did not enforce its hard capacity');
  limitedHost.dispose({ preservePendingOperations: ['game_protocol'] });
  releaseLimitedProtocol();
  await limitedFirst;
  let disposedQueueError = null;
  try { await limitedQueued; } catch (error) { disposedQueueError = error; }
  assert(disposedQueueError?.code === 'disposed',
    'queued protocol work survived host disposal');

  const waitingLockNavigator = {
    sendBeacon: () => false,
    locks: {
      request(_name, options) {
        return new Promise((_resolve, reject) => {
          options.signal.addEventListener('abort', () => {
            reject(Object.assign(new Error('aborted'), { name: 'AbortError' }));
          }, { once: true });
        });
      },
    },
  };
  const waitingLockHost = createHost({
    gameType: 'waiting-lock-game',
    fetchImpl,
    windowImpl: windowMock,
    navigatorImpl: waitingLockNavigator,
  });
  waitingLockHost.connectGame({
    protocolVersions: ['1'],
    manifest: {
      id: 'waiting-lock-game', version: '1.0.0', requiredCapabilities: ['leaderboard-local'], optionalCapabilities: [],
    },
  });
  const waitingLock = waitingLockHost.runGameStorageExclusive('leaderboards/main', async () => true)
    .catch((error) => error);
  await Promise.resolve();
  assert(waitingLockHost._pendingStorageLockControllers.size === 1,
    'trusted host did not track the pending Web Lock request');
  waitingLockHost.dispose();
  const waitingLockError = await waitingLock;
  assert(waitingLockError?.code === 'disposed'
    && waitingLockHost._pendingStorageLockControllers.size === 0,
  'trusted host disposal did not abort and release its pending Web Lock request');

  const genericHost = createHost({
    gameType: 'third-party-game',
    fetchImpl,
    windowImpl: windowMock,
    navigatorImpl: windowMock.navigator,
  });
  const genericHandshake = genericHost.connectGame({
    protocolVersions: ['1'],
    manifest: {
      id: 'third-party-game',
      version: '1.0.0',
      requiredCapabilities: ['logging'],
      optionalCapabilities: ['dialogue', 'quick-lines'],
    },
  });
  assert(genericHandshake.grantedCapabilities.includes('dialogue')
    && !genericHandshake.grantedCapabilities.includes('quick-lines'),
  'generic games received a quick-lines route without a registered dictionary');

  const noLockHost = createHost({
    gameType: 'no-lock-game',
    fetchImpl,
    windowImpl: windowMock,
    navigatorImpl: { sendBeacon: () => false },
  });
  const noLockHandshake = noLockHost.connectGame({
    protocolVersions: ['1'],
    manifest: {
      id: 'no-lock-game',
      version: '1.0.0',
      requiredCapabilities: ['logging'],
      optionalCapabilities: ['storage', 'leaderboard-local'],
    },
  });
  assert(noLockHandshake.grantedCapabilities.includes('storage')
    && !noLockHandshake.grantedCapabilities.includes('leaderboard-local'),
  'host granted cross-window leaderboard mutations without an origin-wide lock');

  const originalConsoleWarn = windowMock.console.warn;
  const originalConsoleError = windowMock.console.error;
  const loggerHostOne = createHost({
    gameType: 'logger-one',
    fetchImpl,
    windowImpl: windowMock,
    navigatorImpl: windowMock.navigator,
  });
  const loggerHostTwo = createHost({
    gameType: 'logger-two',
    fetchImpl,
    windowImpl: windowMock,
    navigatorImpl: windowMock.navigator,
  });
  for (const loggerHost of [loggerHostOne, loggerHostTwo]) {
    loggerHost.connectGame({
      protocolVersions: ['1'],
      manifest: {
        id: loggerHost.gameType, version: '1.0.0', requiredCapabilities: ['logging'], optionalCapabilities: [],
      },
    });
  }
  loggerHostOne.configureLogger();
  loggerHostTwo.configureLogger();
  const sharedCaptureRegistry = loggerHostOne._logger.consoleCaptureRegistry;
  assert(sharedCaptureRegistry === loggerHostTwo._logger.consoleCaptureRegistry
    && sharedCaptureRegistry.hosts.size === 2,
  'same-document hosts did not share one bounded console capture registry');
  const throwingValue = new Proxy({}, {
    get(_target, property) {
      if (property === Symbol.toPrimitive || property === 'toString') {
        return () => { throw new Error('cannot stringify'); };
      }
      return undefined;
    },
  });
  let consoleCaptureError = null;
  try {
    windowMock.console.warn(Object.create(null), throwingValue);
    windowMock.console.error(throwingValue);
  } catch (error) { consoleCaptureError = error; }
  assert(consoleCaptureError === null,
    'global console capture changed caller control flow for unprintable values');
  // `details` is truncated leaf by leaf, but `message` was kept verbatim -- and
  // the global console capture joins every argument into it. One accidental
  // data URL or serialized snapshot therefore became a multi-megabyte body, and
  // the send queue holds up to 256 of them before anything leaves the page.
  const capturedLogPayloads = [];
  const realRecordLogPayload = loggerHostOne._recordOrSendLogPayload.bind(loggerHostOne);
  loggerHostOne._recordOrSendLogPayload = (payload) => { capturedLogPayloads.push(payload); };
  loggerHostOne._logger.enabled = true;
  windowMock.console.warn('y'.repeat(50000));
  loggerHostOne._logger.enabled = false;
  loggerHostOne._recordOrSendLogPayload = realRecordLogPayload;
  assert(capturedLogPayloads.length === 1 && capturedLogPayloads[0].message.length === 4096,
    'an oversized console message was queued verbatim');

  // Per-leaf truncation does not bound the RESULT: three object levels of 30
  // keys each keeps 27,000 leaves of up to 1,200 characters. One cumulative
  // budget across the whole walk is what actually bounds the body.
  const wideDetails = {};
  for (let outer = 0; outer < 30; outer += 1) {
    const level2 = {};
    for (let middle = 0; middle < 30; middle += 1) {
      const level3 = {};
      for (let inner = 0; inner < 30; inner += 1) {
        level3[`k${inner}`] = 'z'.repeat(1200);
      }
      level2[`m${middle}`] = level3;
    }
    wideDetails[`o${outer}`] = level2;
  }
  const wideLogPayloads = [];
  const realWideRecord = loggerHostOne._recordOrSendLogPayload.bind(loggerHostOne);
  loggerHostOne._recordOrSendLogPayload = (payload) => { wideLogPayloads.push(payload); };
  loggerHostOne._logger.enabled = true;
  loggerHostOne.log('warning', 'frontend', 'wide_details', 'wide', wideDetails);
  loggerHostOne._logger.enabled = false;
  loggerHostOne._recordOrSendLogPayload = realWideRecord;
  assert(wideLogPayloads.length === 1, 'the wide-details probe did not queue a payload');
  const wideDetailsChars = JSON.stringify(wideLogPayloads[0].details).length;
  assert(wideDetailsChars < 200 * 1024,
    `nested log details were not bounded in aggregate: ${wideDetailsChars} chars`);

  loggerHostOne.dispose();
  windowMock.console.warn('capture remains after first dispose');
  windowMock.console.error('capture remains after first dispose');
  assert(sharedCaptureRegistry.hosts.size === 1
    && windowMock.console.warn === sharedCaptureRegistry.warnWrapper,
  'disposing the first host corrupted the shared console wrapper');
  loggerHostTwo.dispose();
  windowMock.console.warn('original warn restored');
  windowMock.console.error('original error restored');
  assert(sharedCaptureRegistry.hosts.size === 0
    && windowMock.console.warn === originalConsoleWarn
    && windowMock.console.error === originalConsoleError,
  'disposing the final host did not restore and release global console capture');

  // --- route-end archive is projected against granted capabilities ---
  // The server returns its full internal archive on /route/end. Game code is
  // the untrusted party, so captured dialogue, the in-session summary and the
  // pregame context must not reach it just because it holds `runtime`.
  // Drive the real transport boundary first: projecting correctly is useless if
  // end() stops calling the projection.
  // The SDK forwards runtime.end(payload, { timeoutMs }) and the .d.ts
  // advertises it, but this method enumerates _post options explicitly (so
  // operation/keepalive/headers cannot be overridden) and used to drop it.
  const endOptionCalls = [];
  const realPost = host._post.bind(host);
  host._post = (url, body, options) => {
    endOptionCalls.push({ url: String(url), timeoutMs: options?.timeoutMs });
    return realPost(url, body, options);
  };
  await host.end({ session_id: 'server-session' }, { timeoutMs: 1234 });
  assert(endOptionCalls.some((call) => /\/end$/.test(call.url) && call.timeoutMs === 1234),
    'runtime end ignored the caller-supplied timeout');
  endOptionCalls.length = 0;
  await host.end({ session_id: 'server-session' }, { timeoutMs: 999999 });
  assert(endOptionCalls.some((call) => /\/end$/.test(call.url) && call.timeoutMs === 30000),
    'runtime end did not clamp an oversized caller timeout');
  endOptionCalls.length = 0;
  await host.end({ session_id: 'server-session' }, { timeoutMs: 'nonsense' });
  assert(endOptionCalls.some((call) => /\/end$/.test(call.url) && call.timeoutMs === 8000),
    'an invalid caller timeout did not degrade to the existing default');
  host._post = realPost;

  const endResult = await host.end({ session_id: 'server-session' });
  for (const field of LEAKY_ARCHIVE_FIELDS) {
    assert(!(field in endResult.archive),
      `end() returned ${field} from the raw server archive`);
  }
  assert(!('archive_memory' in endResult),
    'end() returned the host memory-write result to game code');
  assert(!('line' in endResult.postgame),
    'end() returned the assistant postgame line to game code');
  assert(endResult.archive.finalScore.player === 2 && endResult.ok === true,
    'end() dropped fields the game legitimately needs');

  const rawEndResponse = RAW_END_RESPONSE;
  // The host-supplied provider closures are the one thing a launch registration
  // actually gates that same-origin script cannot obtain some other way, so they
  // must not be readable off a host that has not completed a handshake. Assert
  // on a FRESHLY CONSTRUCTED host: checking the already-connected `host` would
  // not test the claim, since that one has been granted `quick-lines` anyway.
  const bareHost = createHost({
    gameType: 'example-game',
    fetchImpl,
    windowImpl: windowMock,
    navigatorImpl: windowMock.navigator,
  });
  assert(bareHost._capabilityProviders === undefined,
    'capability provider closures were readable off a host with no handshake');
  assert(!Object.values(bareHost).some((value) => typeof value === 'function'
    && value !== bareHost.dispose && String(value).includes('lines')),
  'a provider closure leaked onto a public property of an unconnected host');
  let bareQuickLinesError = null;
  try { await bareHost.getQuickLines({ event: { kind: 'prepare' } }); }
  catch (error) { bareQuickLinesError = error; }
  assert(bareQuickLinesError?.code === 'capability_denied',
    'an unconnected host served quick-lines without a granted capability');
  bareHost.dispose();
  assert(host._capabilityProviders === undefined,
    'capability provider closures were exposed on a public host property');

  const grantedProjection = host._projectRouteEndResponse(rawEndResponse);
  for (const field of LEAKY_ARCHIVE_FIELDS) {
    assert(!(field in grantedProjection.archive),
      `route-end archive leaked ${field} to game code`);
  }
  assert(!('archive_memory' in grantedProjection),
    'route-end response leaked the host memory-write result to game code');
  assert(!('line' in grantedProjection.postgame),
    'route-end response leaked the assistant postgame line to game code');
  assert(!('should_resume_external_on_exit' in grantedProjection),
    'route-end response leaked host-internal session state to game code');
  assert(grantedProjection.archive.finalScore.player === 2
    && grantedProjection.archive.last_state.round === 4
    && grantedProjection.ok === true
    && grantedProjection.postgame.action === 'chat',
  'route-end projection dropped fields the game legitimately needs');
  // This host holds context-read and memory, so those scopes survive.
  assert(grantedProjection.archive.preGameContext?.stance === 'private'
    && grantedProjection.archive.game_context_summary === 'private rolling summary'
    && Array.isArray(grantedProjection.archive.sdk_memory_submissions),
  'route-end projection withheld scopes the game was actually granted');

  const restoreGrants = host._grantedCapabilities;
  host._grantedCapabilities = new Set(['logging', 'runtime']);
  const runtimeOnlyProjection = host._projectRouteEndResponse(rawEndResponse);
  host._grantedCapabilities = restoreGrants;
  for (const field of [...LEAKY_ARCHIVE_FIELDS,
    'preGameContext', 'pre_game_context_source', 'game_context_summary',
    'sdk_memory_submissions']) {
    assert(!(field in runtimeOnlyProjection.archive),
      `a runtime-only game received ${field} without the capability granting it`);
  }
  assert(runtimeOnlyProjection.archive.finalScore.player === 2,
    'a runtime-only game lost its own outcome fields');

  // --- a timed-out logger enable must not commit when it finally lands ---
  // The generation guard only tracks teardown, so without an attempt token a
  // slow POST (30s default) landing after the 3.5s enable timeout would flip
  // logger.enabled AFTER the caller was told enabling failed, and console
  // output would start being transmitted unexpectedly.
  slowLogEnableGate = new Promise((resolve) => { releaseSlowLogEnable = resolve; });
  const timeoutLogHost = createHost({
    gameType: 'log-timeout-game',
    fetchImpl,
    windowImpl: windowMock,
    navigatorImpl: windowMock.navigator,
  });
  await timeoutLogHost.connectGame({
    protocolVersions: ['1'],
    manifest: {
      id: 'log-timeout-game', version: '1.0.0',
      requiredCapabilities: ['logging'], optionalCapabilities: [],
    },
  });
  timeoutLogHost.configureLogger({ enableTimeoutMs: 5 });
  const timedOutEnable = await timeoutLogHost.enableLogger('test');
  assert(timedOutEnable?.ok === false && timedOutEnable.reason === 'enable_timeout',
    'the slow logger enable did not time out as set up: ' + JSON.stringify(timedOutEnable));
  assert(timeoutLogHost._logger.enabled !== true,
    'logging was enabled even though the caller was told it timed out');

  releaseSlowLogEnable();
  await new Promise((resolve) => setTimeout(resolve, 25));
  assert(timeoutLogHost._logger.enabled !== true,
    'a timed-out logger enable committed after it finally settled');
  timeoutLogHost.dispose();
  slowLogEnableGate = null;

  noLockHost.dispose();
  genericHost.dispose();
  host.dispose();
  const endHost = createHost({
    gameType: 'example-game',
    sessionId: 'end-projection-session',
    fetchImpl,
    windowImpl: windowMock,
    navigatorImpl: windowMock.navigator,
  });
  endHost.connectGame({
    protocolVersions: ['1'],
    manifest: {
      id: 'example-game',
      version: '1.0.0',
      requiredCapabilities: ['runtime', 'logging'],
      optionalCapabilities: [],
    },
  });

  // A rejected close must not read as success. `/end` answering non-2xx with a
  // FastAPI-shaped `{"detail": ...}` parses fine, carries no `ok`, and keeps no
  // field the projection preserves -- so it used to arrive as `{}`, which the
  // SDK reads as SUCCESS. The client then retired its route generation and
  // entered `ended` while the backend had refused to close the route.
  const rejectedEnd = await endHost.end({ force_end_http_error: true });
  assert(rejectedEnd.ok === false && rejectedEnd.status === 409,
    `a non-2xx route end was projected as success: ${JSON.stringify(rejectedEnd)}`);
  const acceptedEnd = await endHost.end({});
  assert(acceptedEnd.ok !== false,
    'a successful route end was projected as a failure');

  // Fetch caps keepalive bodies at 64 KiB and the quota is SHARED with the
  // diagnostic logger, so an oversized-but-valid end payload (the SDK admits up
  // to 256 KiB) used to reject before the request left the page: explicit end
  // degraded, and an unloading page skipped cleanup and postgame entirely.
  const endCallsBefore = calls.filter((call) => /\/end$/.test(call.url)).length;
  await endHost.end({ small: 'x'.repeat(1024) });
  const smallEndCall = calls.filter((call) => /\/end$/.test(call.url)).at(-1);
  assert(smallEndCall.init.keepalive === true,
    'a small route end payload lost its keepalive guarantee');
  await endHost.end({ big: 'x'.repeat(100 * 1024) });
  const bigEndCall = calls.filter((call) => /\/end$/.test(call.url)).at(-1);
  // `_post` omits the key entirely rather than sending `keepalive: false`.
  assert(bigEndCall.init.keepalive !== true,
    'an end payload past the keepalive quota was still sent with keepalive');
  assert(calls.filter((call) => /\/end$/.test(call.url)).length === endCallsBefore + 2,
    'the keepalive probe did not reach the backend');
  endHost.dispose();

  // The generated session id used to be timestamp-only, so two hosts for the same
  // game constructed in the same millisecond started life with the SAME client
  // session id -- and every game endpoint keys route identity on session_id, so
  // one window's requests would answer for the other's route. resetSession
  // ({newSession:true}) mints through the same generator, so an immediate reset
  // could also claim a new session while keeping the old identity.
  const realSessionDateNow = Date.now;
  const generatedSessionIds = new Set();
  const probeIds = [];
  // windowMock.crypto is deliberately deterministic (`values.fill(7)`), which
  // would make every id here identical for the wrong reason. Give the probe a
  // counter-based source: distinct ids then prove the entropy is actually mixed
  // into the id, not that the clock moved.
  let sessionEntropyCounter = 0;
  const sessionEntropyWindow = {
    ...windowMock,
    crypto: {
      getRandomValues(values) {
        for (let index = 0; index < values.length; index += 1) {
          sessionEntropyCounter += 1;
          values[index] = sessionEntropyCounter;
        }
        return values;
      },
    },
  };
  try {
    Date.now = () => 1700000000000;
    for (let index = 0; index < 4; index += 1) {
      const idHost = createHost({
        gameType: 'example-game',
        fetchImpl,
        windowImpl: sessionEntropyWindow,
        navigatorImpl: windowMock.navigator,
      });
      const constructed = idHost.sessionId;
      const reset = idHost.resetRuntime({ newSession: true }).sessionId;
      probeIds.push([constructed, reset]);
      generatedSessionIds.add(constructed);
      generatedSessionIds.add(reset);
      idHost.dispose();
    }
  } finally {
    Date.now = realSessionDateNow;
  }
  assert(generatedSessionIds.size === 8,
    `same-millisecond hosts minted colliding session ids: ${JSON.stringify(probeIds)}`);

  // On unload, keepalive is the only delivery with any chance, so an oversized
  // page-exit body must shed the CALLER's payload rather than the delivery
  // guarantee -- dropping keepalive there lets the unloading document cancel the
  // request outright, and route cleanup plus postgame are lost until expiry.
  const exitHost = createHost({
    gameType: 'example-game',
    sessionId: 'page-exit-session',
    fetchImpl,
    windowImpl: windowMock,
    navigatorImpl: { ...windowMock.navigator, sendBeacon: () => false },
  });
  exitHost.connectGame({
    protocolVersions: ['1'],
    manifest: {
      id: 'example-game', version: '1.0.0',
      requiredCapabilities: ['runtime', 'logging'], optionalCapabilities: [],
    },
  });
  await exitHost.end(
    { reason: 'pagehide', bulk: 'x'.repeat(100 * 1024) },
    { useBeacon: true },
  );
  const exitCall = calls.filter((call) => /\/end$/.test(call.url)).at(-1);
  assert(exitCall.init.keepalive === true,
    'an oversized page-exit end lost its keepalive guarantee');
  assert(exitCall.body.reason === 'pagehide',
    'the shed page-exit payload dropped the reason the backend finalizes on');
  assert(exitCall.body.bulk === undefined,
    'the oversized caller payload was not shed from the page-exit body');
  assert(exitCall.init.body.length < 60 * 1024,
    'the page-exit body still exceeds the keepalive quota after shedding');
  // Shedding the caller's payload is not enough on its own: `reason` is kept
  // (the backend finalizes on it) and is caller-sized, so an oversized reason
  // would leave the body over quota -- and keepalive on an over-quota body
  // fails BEFORE the request leaves the page, i.e. it guarantees exactly the
  // loss it was kept for. Trim what is kept until the body actually fits.
  await exitHost.end(
    { reason: `pagehide-${'r'.repeat(100 * 1024)}` },
    { useBeacon: true },
  );
  const unshrinkableExitCall = calls.filter((call) => /\/end$/.test(call.url)).at(-1);
  assert(unshrinkableExitCall.init.body.length <= 60 * 1024,
    'an oversized page-exit reason left the body over the keepalive quota');
  assert(unshrinkableExitCall.init.keepalive === true,
    'a page-exit end whose reason had to be trimmed lost keepalive');
  assert(typeof unshrinkableExitCall.body.reason === 'string'
    && unshrinkableExitCall.body.reason.startsWith('pagehide-')
    && unshrinkableExitCall.body.reason.length <= 512,
  'the trimmed page-exit reason was dropped instead of shortened');

  // A direct host caller is not bound by the SDK's four-generation cap, so the
  // retained candidate list is the next thing that can hold the body over quota.
  await exitHost.end({
    reason: 'pagehide',
    sdk_route_instance_ids: Array.from({ length: 900 }, (_unused, index) => (
      `route-${index}-${'i'.repeat(120)}`
    )),
  }, { useBeacon: true });
  const bulkIdsExitCall = calls.filter((call) => /\/end$/.test(call.url)).at(-1);
  assert(bulkIdsExitCall.init.body.length <= 60 * 1024,
    'an oversized retained candidate list left the page-exit body over quota');
  assert(bulkIdsExitCall.init.keepalive === true,
    'trimming the candidate list lost keepalive on the unload path');
  assert(bulkIdsExitCall.body.reason === 'pagehide',
    'the candidate-list trim dropped the reason before the list');
  exitHost.dispose();

  // And when nothing CAN be shed -- the host's own stamped session id is over
  // quota by itself -- keepalive must be dropped rather than kept: a keepalive
  // request over the shared budget fails before it leaves the page, which is
  // exactly the loss it was kept for. A plain request at least has a chance.
  const hugeSessionHost = createHost({
    gameType: 'example-game',
    sessionId: `s${'q'.repeat(80 * 1024)}`,
    fetchImpl,
    windowImpl: windowMock,
    navigatorImpl: { ...windowMock.navigator, sendBeacon: () => false },
  });
  hugeSessionHost.connectGame({
    protocolVersions: ['1'],
    manifest: {
      id: 'example-game', version: '1.0.0',
      requiredCapabilities: ['runtime', 'logging'], optionalCapabilities: [],
    },
  });
  await hugeSessionHost.end({ reason: 'pagehide' }, { useBeacon: true });
  const unshedableExitCall = calls.filter((call) => /\/end$/.test(call.url)).at(-1);
  assert(unshedableExitCall.init.body.length > 60 * 1024,
    'the unshedable probe did not actually exceed the keepalive quota');
  assert(unshedableExitCall.init.keepalive !== true,
    'a page-exit body that cannot be shed under the quota still used keepalive, '
    + 'which fails before the request leaves the page');
  hugeSessionHost.dispose();

  // Two windows for the same game each scanned the namespace, each saw the
  // same pre-write key count, and each committed -- so the documented per-game
  // bounds could be pushed past what either write checked. Scan and commit are
  // one critical section now.
  {
    const sharedStorage = storage();
    // A Web Locks stand-in that actually queues per name, which the default
    // fixture mock does not: `request(name, opts, cb) => cb()` runs both
    // callbacks concurrently and would make this probe pass without the fix.
    const heldLocks = new Map();
    const lockCalls = [];
    let insideNamespaceLock = 0;
    let sawConcurrentCriticalSections = false;
    const serializingNavigator = {
      sendBeacon: () => false,
      locks: {
        request: async (name, _options, callback) => {
          lockCalls.push(name);
          const previous = heldLocks.get(name) || Promise.resolve();
          let release;
          const mine = new Promise((resolve) => { release = resolve; });
          heldLocks.set(name, previous.then(() => mine));
          await previous;
          insideNamespaceLock += 1;
          if (insideNamespaceLock > 1) sawConcurrentCriticalSections = true;
          try { return await callback(); } finally {
            insideNamespaceLock -= 1;
            release();
          }
        },
      },
    };
    const quotaWindow = {
      ...windowMock,
      navigator: serializingNavigator,
      localStorage: sharedStorage,
    };
    const quotaPeers = [];
    for (const peerIndex of [0, 1]) {
      const peer = createHost({
        gameType: 'example-game',
        sessionId: `quota-peer-${peerIndex}`,
        fetchImpl,
        windowImpl: quotaWindow,
        navigatorImpl: serializingNavigator,
      });
      peer.connectGame({
        protocolVersions: ['1'],
        manifest: {
          id: 'example-game',
          version: '1.0.0',
          requiredCapabilities: ['runtime', 'logging'],
          optionalCapabilities: ['storage'],
        },
      });
      quotaPeers.push(peer);
    }
    // One key short of the documented limit, so exactly one of the two
    // concurrent writes below can legitimately land.
    const quotaPrefix = quotaPeers[0]._gameStoragePrefix();
    for (let index = 0; index < 255; index += 1) {
      sharedStorage.setItem(`${quotaPrefix}seed-${index}`, '"x"');
    }
    // Instrumented only AFTER seeding: from here on, every namespace scan and
    // every commit must be observed with the namespace lock held. Asserting
    // that `locks.request` was called is not enough -- an implementation that
    // scanned and wrote first and only then requested an empty lock would
    // satisfy that, and localStorage here is synchronous so nothing else would
    // notice.
    const observedOutsideLock = [];
    let scannedInsideLock = 0;
    let wroteInsideLock = 0;
    const rawSetItem = sharedStorage.setItem.bind(sharedStorage);
    const rawKey = sharedStorage.key.bind(sharedStorage);
    sharedStorage.setItem = (key, value) => {
      if (insideNamespaceLock) wroteInsideLock += 1;
      else observedOutsideLock.push(`setItem:${key}`);
      return rawSetItem(key, value);
    };
    sharedStorage.key = (index) => {
      if (insideNamespaceLock) scannedInsideLock += 1;
      else observedOutsideLock.push(`key:${index}`);
      return rawKey(index);
    };
    const quotaResults = await Promise.allSettled([
      Promise.resolve().then(() => quotaPeers[0].requestGameStorage(
        'set', { key: 'peer-a', value: 'a' },
      )),
      Promise.resolve().then(() => quotaPeers[1].requestGameStorage(
        'set', { key: 'peer-b', value: 'b' },
      )),
    ]);
    const quotaAccepted = quotaResults.filter((entry) => entry.status === 'fulfilled');
    const quotaRejected = quotaResults.filter((entry) => entry.status === 'rejected');
    assert(quotaAccepted.length === 1 && quotaRejected.length === 1,
      `concurrent writes from two windows both passed the key limit: ${
        quotaResults.map((entry) => entry.status).join(',')}`);
    assert(quotaRejected[0].reason?.code === 'quota_exceeded',
      'the losing concurrent write failed for a reason other than the quota');
    // rawKey, not the instrumented wrapper: this loop is the test counting for
    // itself, and routing it through the probe would report the harness as an
    // out-of-lock namespace scan.
    let quotaKeyCount = 0;
    for (let index = 0; index < sharedStorage.length; index += 1) {
      if (String(rawKey(index) || '').startsWith(quotaPrefix)) quotaKeyCount += 1;
    }
    assert(quotaKeyCount === 256,
      `the namespace ended up with ${quotaKeyCount} keys past its 256 bound`);
    // The two assertions above hold with or without the lock: both hosts live
    // in ONE JS context here, so their synchronous scan+commit cannot actually
    // interleave. The real race is across windows, which this harness cannot
    // stage -- so what is pinned here is the mechanism that closes it: each
    // write ran inside the namespace-wide Web Lock, and no two critical
    // sections were ever open at once.
    const namespaceLockName = `${quotaPrefix}lock:__namespace__`;
    assert(lockCalls.filter((name) => name === namespaceLockName).length === 2,
      `storage writes did not take the namespace lock: ${JSON.stringify(lockCalls)}`);
    assert(!sawConcurrentCriticalSections,
      'two storage critical sections were open at the same time');
    assert(observedOutsideLock.length === 0,
      `the quota scan or the commit ran outside the namespace lock: ${
        JSON.stringify(observedOutsideLock.slice(0, 8))}`);
    // The probe really did touch storage, so the assertion above cannot pass
    // by observing nothing at all.
    assert(scannedInsideLock > 0 && wroteInsideLock > 0,
      `the probe observed no scan/commit at all: scans=${scannedInsideLock} writes=${wroteInsideLock}`);
    sharedStorage.setItem = rawSetItem;
    sharedStorage.key = rawKey;
    for (const peer of quotaPeers) peer.dispose();
  }

  process.stdout.write('mini-game same-origin host runtime test passed\n');
}

main().catch((error) => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exitCode = 1;
});
